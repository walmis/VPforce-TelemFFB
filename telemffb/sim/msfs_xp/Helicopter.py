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
        self.cpO_x = 0.0
        self.cpO_y = 0.0

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
        if self.is_trimwheel():
            return  # single-purpose device; effects chain gated in Aircraft

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

    def check_hands_on(self, percent) -> dict:
        phys_x, phys_y = self._get_device_axes()

        ref_x = self.cpO_x
        ref_y = self.cpO_y

        threshold = percent

        deviation_x = abs(phys_x - ref_x)
        deviation_y = abs(phys_y - ref_y)

        raw_deviation_x = phys_x - ref_x
        raw_deviation_y = phys_y - ref_y

        x_exceeds_threshold = abs(phys_x - ref_x) > threshold
        y_exceeds_threshold = abs(phys_y - ref_y) > threshold
        master_exceeds_threshold = x_exceeds_threshold or y_exceeds_threshold

        return {
            "master_result": master_exceeds_threshold,
            "x_result": x_exceeds_threshold,
            "x_deviation": deviation_x,
            "x_deviation_raw": raw_deviation_x,
            "y_result": y_exceeds_threshold,
            "y_deviation": deviation_y,
            "y_deviation_raw": raw_deviation_y,
        }

    def _dispatch_hands_on_state(self, telem_data: BaseTelemetryData,
                                  hands_on_dict: dict, hands_on_either: bool):
        hands_on_x = hands_on_dict["x_result"]
        hands_on_y = hands_on_dict["y_result"]
        dev_x = hands_on_dict["x_deviation"]
        dev_y = hands_on_dict["y_deviation"]

        if self.send_individual_hands_on:
            if hands_on_x:
                self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICX", 1, units="number")
                self.hands_on_x_active = True
            else:
                self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICX", 0, units="number")
                self.hands_on_x_active = False

            if hands_on_y:
                self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICY", 1, units="number")
                self.hands_on_y_active = True
            else:
                self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICY", 0, units="number")
                self.hands_on_y_active = False
        else:
            if hands_on_either:
                self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLIC", 1, units="number")
                self.hands_on_active = True
            else:
                self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLIC", 0, units="number")
                self.hands_on_active = False

        telem_data.hands_on = int(hands_on_either)
        telem_data.hands_on_x = int(hands_on_x)
        telem_data.hands_on_y = int(hands_on_y)
        telem_data.deviation_x = dev_x
        telem_data.deviation_y = dev_y

    def _get_msfs_pedal_axis_config(self) -> tuple[str, float]:
        if self.enable_custom_x_axis:
            return self.custom_x_axis, self.raw_x_axis_scale
        return 'ROTOR_AXIS_TAIL_ROTOR_SET', 16384

    def _get_msfs_collective_axis_config(self) -> tuple[str, float]:
        if self.enable_custom_y_axis:
            return self.custom_y_axis, self.raw_y_axis_scale
        return 'AXIS_COLLECTIVE_SET', 16384

    def _initialize_pedals_if_needed(self, telem_data: BaseTelemetryData, phys_x: float, x_var: str) -> bool:
        if self.pedals_init:
            return True

        self.spring_x.set_coefficient(self.pedal_spring_coeff_x)
        if telem_data.get("SimOnGround", 1):
            self.cpO_x = 0.0
            self.last_pos_x_pos = 0
        else:
            self.cpO_x = self.last_pedal_x

        self.spring_x.set_coefficient(self.pedal_spring_gain, True)
        self.spring_x.set_offset(self.cpO_x)
        self._spring_handle.setCondition(self.spring_x)
        self._spring_handle.start()

        logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
        # Gate output until hardware reaches the seeded center point to avoid control jumps.
        if self.cpO_x - 0.1 < phys_x < self.cpO_x + 0.1:
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

    @override
    def ac_update_pedal_force_trim(self, telem_data: BaseTelemetryData, ft_active=True):
        """Update MSFS/X-Plane pedal force trim using normalized spring centers."""
        if not self.is_pedals():
            return None

        phys_x, _ = self._get_device_axes()
        force_trim_pressed = self.check_button_press(
            self.pedal_ft_release_button, self.pedal_ft_use_master_buttons)
        trim_reset_pressed = self.check_button_press(
            self.pedal_ft_reset_button, self.pedal_ft_use_master_buttons)

        if force_trim_pressed or not ft_active:
            if self.pedal_ft_damper_enabled:
                self.spring_x.set_coefficient(self.pedal_ft_damper_force)
            else:
                self.spring_x.set_coefficient(0)

            self.cpO_x = utils.clamp(phys_x, -1, 1)
            self.spring_x.set_offset(self.cpO_x)
            return True

        if trim_reset_pressed or not self.pedal_trim_reset_complete:
            self.spring_x.set_coefficient(4096)
            self.cpO_x = self.step_value_over_time(
                "center_x", self.cpO_x, 1000, 0.0, floatpoint=True)
            self.spring_x.set_offset(self.cpO_x)
            self.pedal_trim_reset_complete = self.cpO_x == 0.0
            return True

        return False

    def _send_pedal_outputs(self, telem_data: BaseTelemetryData, phys_x: float, x_scale: float, x_var: str, x_range: float):
        if self._use_firmware_axis_backend():
            self._send_firmware_fixed_axes(x_value=phys_x * x_scale)
            return

        if self._sim_is_xplane():
            pos_x_pos = phys_x * x_scale
            self.send_xp_command(f'AXIS:px={round(pos_x_pos, 5)}')

        if self._sim_is_msfs():
            pos_x_pos = self._scale_msfs_axis_value(phys_x, x_range, x_scale)
            self.msfs_send_heli_pedal_pos(x_var, pos_x_pos, telem_data)
            self.last_pos_x_pos = pos_x_pos

    def msfs_send_heli_pedal_pos(self, xvar, xpos, telem_data: BaseTelemetryData):
        self._simconnect.send_event_to_msfs(xvar, xpos)

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
        self.spring_y.set_offset(1.0)
        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.start()

        # Collective reports lock ownership as soon as lock mode is active, then snaps into the upper detent near full-down.
        if 0.9 < phys_y < 1.0:
            self._engage_controls_lock_detents(
                self.spring_y,
                spring_offset=1.0,
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
            self.cpO_y = 1.0
            if self._sim_is_msfs():
                if y_range != 1:
                    self.last_pos_y_pos = -y_range * 1
                else:
                    self.last_pos_y_pos = 1
        elif self.last_collective_y is None:
            # Air start or new aircraft. Use current physical position as init point
            self.cpO_y = utils.clamp(phys_y, -1, 1)
        else:
            # In air, previously paused. Use stored collective position to init point
            self.cpO_y = utils.clamp(self.last_collective_y, -1, 1)

        self.spring_y.set_coefficient(4096)
        self.spring_y.set_offset(self.cpO_y)
        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.start(override=True)

        # Gate output until hardware reaches the seeded center point to avoid control jumps.
        if self.cpO_y - 0.1 < phys_y < self.cpO_y + 0.1:
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
        self.cpO_y = utils.clamp(phys_y, -1, 1)
        self.spring_y.set_offset(self.cpO_y)
        self.spring_y.set_coefficient(round(self.collective_spring_coeff_y / 2))
        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.start(override=True)

    def _send_collective_outputs(self, telem_data: BaseTelemetryData, phys_y: float, y_var: str, y_range: float):
        if self._use_firmware_axis_backend() and self.collective_init:
            self._send_firmware_fixed_axes(y_value=phys_y)
            return

        if self._sim_is_xplane():
            pos_y_pos = utils.scale(phys_y, (-1, 1), (1, 0))
            if self.collective_init:
                self.send_xp_command(f'AXIS:cy={round(pos_y_pos, 5)}')

        if self._sim_is_msfs() and self.collective_init:
            pos_y_pos = self._scale_msfs_axis_value(phys_y, y_range, 1.0)
            self.msfs_send_heli_collective_pos(y_var, pos_y_pos, telem_data)
            self.last_pos_y_pos = pos_y_pos

    def msfs_send_heli_collective_pos(self, yvar, ypos, telem_data: BaseTelemetryData):
        self._simconnect.send_event_to_msfs(yvar, ypos)

    def msfs_update_pedals(self, telem_data: BaseTelemetryData):
        if not self.is_pedals():
            return

        if not self.telemffb_controls_axes or self.local_disable_axis_control:
            return

        phys_x, _ = self._get_device_raw_axes()
        x_scale = clamp(self.rudder_x_axis_scale, 0.0, 1.0)
        x_var, x_range = self._get_msfs_pedal_axis_config()

        self._spring_handle.name = "pedal_spring"

        telem_data.phys_x = phys_x
        self._sync_force_trim_simvar()
        # Enable cockpit switch control (if exists) for force trim. Add LVar as "ForceTrimSW" bool if available for aircraft.
        force_trim_active = telem_data.get('ForceTrimSW', True) if self.custom_ft_sw_var_enabled else True
        self._sync_controls_lock_simvar()
        controls_locked = self._get_controls_lock_state(telem_data)

        # Lock path intentionally short-circuits all downstream axis updates.
        if self._apply_pedal_controls_lock(telem_data, controls_locked):
            return

        if not self._initialize_pedals_if_needed(telem_data, phys_x, x_var):
            return

        self._update_pedal_spring_mode(telem_data, force_trim_active)

        if not self._spring_handle.started:
            self._spring_handle.start()

        self.last_pedal_x = phys_x
        self._send_pedal_outputs(telem_data, phys_x, x_scale, x_var, x_range)

    def msfs_update_collective(self, telem_data: BaseTelemetryData):
        if not self.is_collective():
            return
        if not self._axis_control_enabled():
            return

        y_var, y_range = self._get_msfs_collective_axis_config()

        _, phys_y = self._get_device_raw_axes()

        telem_data.phys_y = phys_y

        self._sync_controls_lock_simvar()
        controls_locked = self._get_controls_lock_state(telem_data)

        # Lock path intentionally short-circuits all downstream axis updates.
        if self._handle_collective_controls_lock(telem_data, controls_locked):
            return

        if not self._initialize_collective_if_needed(telem_data, phys_y, y_var, y_range):
            return

        self.last_collective_y = phys_y
        self._update_collective_spring_mode(telem_data, phys_y)
        self._send_collective_outputs(telem_data, phys_y, y_var, y_range)
