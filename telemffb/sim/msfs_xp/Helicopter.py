#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

from typing import override
from telemffb.sim.msfs_xp.MsfsXpHeliControlsMixIn import MsfsXpHeliControlsMixIn
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.msfs_xp.Aircraft import Aircraft
from telemffb.utils import clamp

import logging

from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class Helicopter(Aircraft, MsfsXpHeliControlsMixIn):
    """Generic Class for Helicopters"""

    # user parameters
    buffeting_intensity = 0.0

    etl_start_speed = 6.0  # m/s
    etl_stop_speed = 22.0  # m/s
    etl_effect_intensity = 0.2  # [ 0.0 .. 1.0]
    etl_shake_frequency = 14.0  # value has been deprecated in favor of rotor RPM calculation
    overspeed_shake_start = 70.0  # m/s
    overspeed_shake_intensity = 0.2
    heli_engine_rumble_intensity = 0.12

    virtual_cyclic_x_offs = 0
    virtual_cyclic_y_offs = 0
    phys_cyclic_x_offs = 0
    phys_cyclic_y_offs = 0
    stepper_dict = {}
    trim_reset_complete = 1
    last_device_x = 0
    last_device_y = 0
    last_pos_y_pos = 0
    last_pos_x_pos = 0

    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 0
    collective_spring_coeff_y = 0
    last_collective_y = None

    pedal_spring_gain = 1
    hpg_pedal_spring_gain = 1
    pedal_dampening_gain = 1
    pedal_spring_coeff_x = 0

    joystick_trim_follow_gain_physical_x = 0.3
    joystick_trim_follow_gain_virtual_x = 0.2
    joystick_trim_follow_gain_physical_y = 0.3
    joystick_trim_follow_gain_virtual_y = 0.2
    cyclic_physical_trim_x_offs = 0
    cyclic_physical_trim_y_offs = 0
    cyclic_virtual_trim_x_offs = 0
    cyclic_virtual_trim_y_offs = 0
    # end user parameters

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        self.pedals_init = 0
        if self._sim_is_msfs():
            self.subscribe_simvars()

        self.cyclic_spring_init = 0
        self.collective_init = 0
        self.pedals_init = 0
        self.cpO_x = 0
        self.cpO_y = 0

    @override
    def on_timeout(self):
        super().on_timeout()
        self.cyclic_spring_init = 0
        self.collective_init = 0
        self.pedals_init = 0

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        self.speedbrake_motion_intensity = 0.0
        if telem_data.N is None:
            return
        telem_data.AircraftClass = "Helicopter"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)

        self.msfs_update_collective(telem_data)
        # # self._update_cyclic_trim(telem_data)
        self.msfs_update_pedals(telem_data)

    @override
    def msfs_update_trimwheel(self, *args, **kwargs):
        pass

    def subscribe_simvars(self):
        if not self._simconnect:
            return

        if 'ForceTrimSW' not in self._simconnect.sv_dict.keys():
            self._simconnect.add_simvar(name='ForceTrimSW', var="L:TelemFFBHeliFT", sc_unit="enum")
            self._simconnect._resubscribe()

    def _sync_msfs_force_trim_simvar(self):
        if not self._sim_is_msfs():
            return

        # Only resubscribe when the configured LVar binding changes.
        if (self.custom_ft_sw_var_enabled and self.anything_has_changed('custom_ft_sw_var', self.custom_ft_sw_var)) or self.anything_has_changed('custom_ft_sw_var_enabled', self.custom_ft_sw_var_enabled):
            self._simconnect.add_simvar(name='ForceTrimSW', var=self.custom_ft_sw_var, sc_unit="enum")
            self._simconnect._resubscribe()

    def _sync_msfs_controls_lock_simvar(self):
        if self._sim_is_msfs() and self.controls_lock_enable and self.controls_lock_simvar:
            self._simconnect.add_simvar(name="ControlsLock", var=self.controls_lock_simvar, sc_unit="enum")
            self._simconnect._resubscribe()

    def _get_controls_lock_state(self, telem_data: BaseTelemetryData) -> bool:
        if not self.controls_lock_enable:
            return False

        # Normalize simulator value once so pedal/collective lock handlers share the same decision path.
        controls_locked = bool(telem_data.get("ControlsLock", 0))
        if self.controls_lock_simvar_invert:
            controls_locked = not controls_locked
        return controls_locked

    def _prepare_controls_lock(
        self,
        telem_data: BaseTelemetryData,
        controls_locked: bool,
        *,
        set_locked_telemetry: bool = False,
    ) -> bool | None:
        # Centralize the shared lock-state short-circuit so axis handlers only manage axis-specific setup.
        # Return values are tri-state:
        # - False: lock mode inactive, caller should continue normal axis handling
        # - True: lock mode fully owns this frame, caller should return immediately
        # - None: lock mode active and caller must continue with axis-specific spring/detent setup
        if not controls_locked:
            self._stop_lock_effects()
            return False

        if set_locked_telemetry:
            telem_data._controls_locked = True

        if self._lock_effects_started():
            return True

        return None

    def _engage_controls_lock_detents(
        self,
        spring_condition,
        *,
        spring_offset: int,
        detent_1: dict,
        detent_2: dict,
        stop_control_weight: bool = False,
    ):
        # Share the common lock spring + detent startup sequence; callers provide only axis-specific geometry.
        if stop_control_weight:
            self.effects['control_weight'].stop()

        spring_condition.set_coefficient(4096)
        spring_condition.cpOffset = spring_offset
        self._spring_handle.setCondition(spring_condition)
        self._spring_handle.start()
        self.effects['lock_1'].detent(**detent_1).start()
        self.effects['lock_2'].detent(**detent_2).start()
        self._spring_handle.stop()

    def _lock_effects_started(self) -> bool:
        return self.effects['lock_1'].started or self.effects['lock_2'].started

    def _stop_lock_effects(self):
        self.effects['lock_1'].stop()
        self.effects['lock_2'].stop()

    def _get_msfs_pedal_axis_config(self) -> tuple[str, float]:
        if self.enable_custom_x_axis:
            return self.custom_x_axis, self.raw_x_axis_scale
        return 'ROTOR_AXIS_TAIL_ROTOR_SET', 16384

    def _get_msfs_collective_axis_config(self) -> tuple[str, float]:
        if self.enable_custom_y_axis:
            return self.custom_y_axis, self.raw_y_axis_scale
        return 'AXIS_COLLECTIVE_SET', 16384

    def _handle_pedal_controls_lock(self, telem_data: BaseTelemetryData, controls_locked: bool) -> bool:
        # Return True when lock mode owns this frame and normal axis processing must stop.
        lock_result = self._prepare_controls_lock(telem_data, controls_locked)
        if lock_result is not None:
            return lock_result

        phys_x, _ = self._get_device_axes()

        detent_1 = {
            'position_x': 1500,
            'peak_x': 4096,
            'range_x': 4096,
            'gate_pos_y': 0,
            'gate_neg_y': 0,
        }
        detent_2 = {
            'position_x': -1500,
            'peak_x': 4096,
            'range_x': 4096,
            'gate_pos_y': 0,
            'gate_neg_y': 0,
        }

        self.spring_x.set_coefficient(4096)
        self.spring_x.cpOffset = 0
        self._spring_handle.setCondition(self.spring_x)
        self._spring_handle.start()

        # Pedals only advertise `_controls_locked` after the user reaches the centered lock groove.
        if -0.15 < phys_x < 0.15:
            self._engage_controls_lock_detents(
                self.spring_x,
                spring_offset=0,
                detent_1=detent_1,
                detent_2=detent_2,
                stop_control_weight=True,
            )
            telem_data["_controls_locked"] = controls_locked

        return True

    def _initialize_pedals_if_needed(self, telem_data: BaseTelemetryData, phys_x: float, x_var: str) -> bool:
        if self.pedals_init:
            return True

        self.spring_x.set_coefficient(self.pedal_spring_coeff_x)
        if telem_data.get("SimOnGround", 1):
            self.cpO_x = 0
            self.last_pos_x_pos = 0
        else:
            self.cpO_x = round(4096 * self.last_pedal_x)

        self.spring_x.set_coefficient(self.pedal_spring_gain, True)
        self.spring_x.set_offset(self.cpO_x)
        self._spring_handle.setCondition(self.spring_x)
        self._spring_handle.start()

        logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
        # Gate output until hardware reaches the seeded center point to avoid control jumps.
        if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
            # dont start sending position until physical pedals have centered
            self.pedals_init = 1
            logging.info("Pedals Initialized")
            if not self.spring_mode_is(SpringModeEnum.FORCETRIM):
                self._spring_handle.stop()
            return True

        if self._sim_is_msfs():
            self.msfs_send_heli_pedal_pos(x_var, self.last_pos_x_pos, telem_data)
        return False

    def _update_pedal_spring_mode(self, telem_data: BaseTelemetryData, force_trim_active: bool):
        if self.spring_mode_is(SpringModeEnum.FORCETRIM):
            if not self.ac_update_pedal_force_trim(telem_data, ft_active=force_trim_active):
                self.spring_x.set_coefficient(self.pedal_spring_gain, True)
            self._spring_handle.setCondition(self.spring_x)
            return

        self.spring_x.set_coefficient(0)
        self._spring_handle.setCondition(self.spring_x)

    def _send_pedal_outputs(self, telem_data: BaseTelemetryData, phys_x: float, x_scale: float, x_var: str, x_range: float):
        if self._sim_is_xplane():
            pos_x_pos = phys_x * x_scale
            self.send_xp_command(f'AXIS:px={round(pos_x_pos, 5)}')

        if self._sim_is_msfs():
            pos_x_pos = utils.scale(phys_x, (-1, 1), (-x_range * x_scale, x_range * x_scale))
            if x_range != 1:
                pos_x_pos = -int(pos_x_pos)
            else:
                pos_x_pos = round(pos_x_pos, 5)

            self.msfs_send_heli_pedal_pos(x_var, pos_x_pos, telem_data)
            self.last_pos_x_pos = pos_x_pos

    def _handle_collective_controls_lock(self, telem_data: BaseTelemetryData, controls_locked: bool) -> bool:
        # Return True when lock mode owns this frame and normal axis processing must stop.
        lock_result = self._prepare_controls_lock(telem_data, controls_locked, set_locked_telemetry=True)
        if lock_result is not None:
            return lock_result

        _, phys_y = self._get_device_axes()

        detent_1 = {
            'position_y': 4000,
            'peak_y': 4096,
            'range_y': 4096,
            'gate_pos_x': 0,
            'gate_neg_x': 0,
        }
        detent_2 = {
            'position_y': 2500,
            'peak_y': 4096,
            'range_y': 4096,
            'gate_pos_x': 0,
            'gate_neg_x': 0,
        }

        self.spring_y.set_coefficient(4096)
        self.spring_y.cpOffset = 4096
        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.start()

        # Collective reports lock ownership as soon as lock mode is active, then snaps into the upper detent near full-down.
        if 0.9 < phys_y < 1.0:
            self._engage_controls_lock_detents(
                self.spring_y,
                spring_offset=4096,
                detent_1=detent_1,
                detent_2=detent_2,
            )
            telem_data._controls_locked = controls_locked

        return True

    def _initialize_collective_if_needed(self, telem_data: BaseTelemetryData, phys_y: float, y_var: str, y_range: float) -> bool:
        if self.collective_init:
            return True

        self._spring_handle.name = "collective_ap_spring"
        self.spring_y.set_coefficient(4096)

        if telem_data.get("SimOnGround", 1):
            # aircraft is on ground, so initialize collective to full down
            self.cpO_y = 4096
            if self._sim_is_msfs():
                if y_range != 1:
                    self.last_pos_y_pos = -y_range * 1
                else:
                    self.last_pos_y_pos = 1
        elif self.last_collective_y is None:
            # Air start or new aircraft. Use current physical position as init point
            self.cpO_y = round(4096 * phys_y)
        else:
            # In air, previously paused. Use stored collective position to init point
            self.cpO_y = round(4096 * self.last_collective_y)

        self.spring_y.set_coefficient(4096)
        self.spring_y.cpOffset = self.cpO_y
        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.start(override=True)

        # Gate output until hardware reaches the seeded center point to avoid control jumps.
        if self.cpO_y / 4096 - 0.1 < phys_y < self.cpO_y / 4096 + 0.1:
            # dont start sending position until physical stick has centered
            self.collective_init = 1
            logging.info("Collective Initialized")
            return True

        if self._sim_is_msfs():
            self.msfs_send_heli_collective_pos(y_var, self.last_pos_y_pos, telem_data)
        return False

    def _update_collective_spring_mode(self, telem_data: BaseTelemetryData, phys_y: float):
        if self.spring_mode_is(SpringModeEnum.FORCETRIM):
            self._spring_handle.name = "collective_ft"
            self.ac_collective_force_trim_override(telem_data, self._spring_handle)
            return

        self._spring_handle.name = "collective_ap_spring"
        self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
        self.spring_y.cpOffset = self.cpO_y
        self.spring_y.set_coefficient(round(self.collective_spring_coeff_y / 2))
        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.start(override=True)

    def _send_collective_outputs(self, telem_data: BaseTelemetryData, phys_y: float, y_var: str, y_range: float):
        if self._sim_is_xplane():
            pos_y_pos = utils.scale(phys_y, (-1, 1), (1, 0))
            if self.collective_init:
                self.send_xp_command(f'AXIS:cy={round(pos_y_pos, 5)}')

        if self._sim_is_msfs():
            pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))
            if y_range != 1:
                pos_y_pos = -int(pos_y_pos)
            else:
                pos_y_pos = round(pos_y_pos, 5)

            if self.collective_init:
                self.msfs_send_heli_collective_pos(y_var, pos_y_pos, telem_data)
                self.last_pos_y_pos = pos_y_pos

    def msfs_send_heli_pedal_pos(self, xvar, xpos, telem_data: BaseTelemetryData):
        self._simconnect.send_event_to_msfs(xvar, xpos)

    def msfs_update_pedals(self, telem_data: BaseTelemetryData):
        if not self.is_pedals():
            return

        if not self.telemffb_controls_axes or self.local_disable_axis_control:
            return

        phys_x, _ = self._get_device_axes()
        x_scale = clamp(self.rudder_x_axis_scale, 0.0, 1.0)
        x_var, x_range = self._get_msfs_pedal_axis_config()

        self._spring_handle.name = "pedal_spring"

        telem_data.phys_x = phys_x
        self._sync_msfs_force_trim_simvar()
        # Enable cockpit switch control (if exists) for force trim. Add LVar as "ForceTrimSW" bool if available for aircraft.
        force_trim_active = telem_data.get('ForceTrimSW', True) if self.custom_ft_sw_var_enabled else True
        self._sync_msfs_controls_lock_simvar()
        controls_locked = self._get_controls_lock_state(telem_data)

        # Lock path intentionally short-circuits all downstream axis updates.
        if self._handle_pedal_controls_lock(telem_data, controls_locked):
            return

        if not self._initialize_pedals_if_needed(telem_data, phys_x, x_var):
            return

        self._update_pedal_spring_mode(telem_data, force_trim_active)

        if not self._spring_handle.started:
            self._spring_handle.start()

        self.last_pedal_x = phys_x
        self._send_pedal_outputs(telem_data, phys_x, x_scale, x_var, x_range)

    def msfs_send_heli_collective_pos(self, yvar, ypos, telem_data: BaseTelemetryData):
        self._simconnect.send_event_to_msfs(yvar, ypos)

    def msfs_update_collective(self, telem_data: BaseTelemetryData):
        if not self.is_collective():
            return
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            return

        y_var, y_range = self._get_msfs_collective_axis_config()

        _, phys_y = self._get_device_axes()

        telem_data.phys_y = phys_y

        self._sync_msfs_controls_lock_simvar()
        controls_locked = self._get_controls_lock_state(telem_data)

        # Lock path intentionally short-circuits all downstream axis updates.
        if self._handle_collective_controls_lock(telem_data, controls_locked):
            return

        if not self._initialize_collective_if_needed(telem_data, phys_y, y_var, y_range):
            return

        self.last_collective_y = phys_y
        self._update_collective_spring_mode(telem_data, phys_y)
        self._send_collective_outputs(telem_data, phys_y, y_var, y_range)
