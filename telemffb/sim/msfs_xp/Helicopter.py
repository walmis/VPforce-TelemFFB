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

from telemffb.sim.base.DynamicSpringMixin import DynamicSpringMixin
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.base.AoAEffectsMixIn import AoAEffectsMixIn
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
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

    def msfs_send_heli_pedal_pos(self, xvar, xpos, telem_data: BaseTelemetryData):
        self._simconnect.send_event_to_msfs(xvar, xpos)


    def msfs_update_pedals(self, telem_data: BaseTelemetryData):
        if not self.is_pedals(): 
            return

        if self.telemffb_controls_axes and not self.local_disable_axis_control:
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            x_scale = clamp(self.rudder_x_axis_scale, 0.0, 1.0)

            self._spring_handle.name = "pedal_spring"
            # self.damper = effects["pedal_damper"].damper()

            pedal_pos = telem_data.TailRotorPedalPos
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            telem_data.phys_x = phys_x

            if self._sim_is_msfs():
                if (self.custom_ft_sw_var_enabled and self.anything_has_changed('custom_ft_sw_var', self.custom_ft_sw_var)) or self.anything_has_changed('custom_ft_sw_var_enabled', self.custom_ft_sw_var_enabled):
                    self._simconnect.add_simvar(name='ForceTrimSW', var=self.custom_ft_sw_var, sc_unit="enum")
                    self._simconnect._resubscribe()

            force_trim_active = telem_data.get('ForceTrimSW', True) if self.custom_ft_sw_var_enabled else True  # Enable cockpit switch control (if exists) for force trim.  Add LVar as "ForceTrimSW" bool if available for aircraft

            if self._sim_is_msfs():
                if self.controls_lock_enable and self.controls_lock_simvar != '':
                    self._simconnect.add_simvar(name="ControlsLock", var=self.controls_lock_simvar, sc_unit="enum")
                    self._simconnect._resubscribe()


            # get controls lock status
            controls_locked = telem_data.get("ControlsLock", 0) if self.controls_lock_enable else False

            if self.controls_lock_simvar_invert:
                controls_locked = not controls_locked

            if controls_locked:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                x = round(phys_x * 4096)
                y = round(phys_y * 4096)

                groove_detent_size: int = 4096
                groove_detent_range = 4096
                pos = 1500
                if self.effects['lock_1'].started or self.effects['lock_2'].started:
                    return
                self.effects['control_weight'].stop()

                self.spring_x.set_coefficient(4096)

                self.spring_x.cpOffset = 0

                self._spring_handle.setCondition(self.spring_x)
                self._spring_handle.start()
                if -0.15 < phys_x < 0.15:
                    self.effects['lock_1'].detent(
                        position_x=pos,
                        peak_x=groove_detent_size,
                        range_x=groove_detent_range,
                        gate_pos_y=0,
                        gate_neg_y=0,

                    ).start()
                    self.effects['lock_2'].detent(
                        position_x=-pos,
                        peak_x=groove_detent_size,
                        range_x=groove_detent_range,
                        gate_pos_y=0,
                        gate_neg_y=0,

                    ).start()
                    telem_data["_controls_locked"] = controls_locked
                    self._spring_handle.stop()

                return
            else:
                self.effects['lock_1'].stop()
                self.effects['lock_2'].stop()


            if not self.pedals_init:

                self.spring_x.set_coefficient(self.pedal_spring_coeff_x)
                if telem_data.get("SimOnGround", 1):
                    self.cpO_x = 0
                    self.last_pos_x_pos = 0
                else:
                    # print(f"last_colelctive_y={self.last_collective_y}")
                    self.cpO_x = round(4096 * self.last_pedal_x)

                self.spring_x.set_coefficient(self.pedal_spring_gain, True)
                self.spring_x.set_offset(self.cpO_x)

                self._spring_handle.setCondition(self.spring_x)
                # self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
                self._spring_handle.start()
                logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
                if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                    # dont start sending position until physical pedals have centered
                    self.pedals_init = 1
                    logging.info("Pedals Initialized")
                    if not self.spring_mode_is(SpringModeEnum.FORCETRIM):
                        self._spring_handle.stop()
                else:
                    if self._sim_is_msfs():
                        if self.enable_custom_x_axis:
                            x_var = self.custom_x_axis
                        else:
                            x_var = 'ROTOR_AXIS_TAIL_ROTOR_SET'

                        self.msfs_send_heli_pedal_pos(x_var, self.last_pos_x_pos, telem_data)
                        # self._simconnect.send_event_to_msfs(x_var, self.last_pos_x_pos)

                    return

            if self.spring_mode_is(SpringModeEnum.FORCETRIM):
                if not self.ac_update_pedal_force_trim(telem_data, ft_active=force_trim_active):
                    self.spring_x.set_coefficient(self.pedal_spring_gain, True)
                self._spring_handle.setCondition(self.spring_x)
            else:
                self.spring_x.set_coefficient(0)
                self._spring_handle.setCondition(self.spring_x)

            if not self._spring_handle.started:
                self._spring_handle.start()

            self.last_pedal_x = phys_x

            if self._sim_is_xplane():
                pos_x_pos = phys_x * x_scale
                self.send_xp_command(f'AXIS:px={round(pos_x_pos, 5)}')
            if self._sim_is_msfs():
                if self.enable_custom_x_axis:
                    x_var = self.custom_x_axis
                    x_range = self.raw_x_axis_scale
                else:
                    x_var = 'ROTOR_AXIS_TAIL_ROTOR_SET'
                    x_range = 16384

                pos_x_pos = utils.scale(phys_x, (-1, 1), (-x_range * x_scale, x_range * x_scale))

                if x_range != 1:
                    pos_x_pos = -int(pos_x_pos)
                else:
                    pos_x_pos = round(pos_x_pos, 5)

                self.msfs_send_heli_pedal_pos(x_var, pos_x_pos, telem_data)
                # self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

                self.last_pos_x_pos = pos_x_pos

    def msfs_send_heli_collective_pos(self, yvar, ypos, telem_data: BaseTelemetryData):
        self._simconnect.send_event_to_msfs(yvar, ypos)

    def msfs_update_collective(self, telem_data: BaseTelemetryData):
        if not self.is_collective():
            return
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            return
        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        telem_data.phys_y = phys_y

        if self._sim_is_msfs():
            if self.controls_lock_enable and self.controls_lock_simvar != '':
                self._simconnect.add_simvar(name="ControlsLock", var=self.controls_lock_simvar, sc_unit="enum")
                self._simconnect._resubscribe()

        # get controls lock status
        controls_locked = (telem_data.ControlsLock or 0) if self.controls_lock_enable else False

        if self.controls_lock_simvar_invert:
            controls_locked = not controls_locked

        if controls_locked:
            telem_data._controls_locked = controls_locked
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            x = round(phys_x * 4096)
            y = round(phys_y * 4096)

            groove_detent_size: int = 4096
            groove_detent_range = 4096
            pos = 4000
            if self.effects['lock_1'].started or self.effects['lock_2'].started:
                return
            self.spring_y.set_coefficient(4096)
            self.spring_y.cpOffset = 4096
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()
            if 0.9 < phys_y < 1.0:
                self.effects['lock_1'].detent(
                    position_y=pos,
                    peak_y=groove_detent_size,
                    range_y=groove_detent_range,
                    gate_pos_x=0,
                    gate_neg_x=0
                ).start()
                self.effects['lock_2'].detent(
                    position_y=pos-1500,
                    peak_y=groove_detent_size,
                    range_y=groove_detent_range,
                    gate_pos_x=0,
                    gate_neg_x=0
                ).start()
                telem_data._controls_locked = controls_locked
                self._spring_handle.stop()

            return
        else:
            self.effects['lock_1'].stop()
            self.effects['lock_2'].stop()

        if not self.collective_init:
            self._spring_handle.name = "collective_ap_spring"
            self.spring_y.set_coefficient(4096)
            if self._sim_is_msfs():
                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384

            
            if telem_data.get("SimOnGround", 1):
                # aircraft is on ground, so initialize collective to full down
                self.cpO_y = 4096
                if self._sim_is_msfs():
                    if y_range != 1:
                        self.last_pos_y_pos = -y_range * 1
                    else:
                        self.last_pos_y_pos = 1
            elif self.last_collective_y is None:
                # Air start or new aircraft.  Use current physical position as init point
                self.cpO_y = round(4096 * phys_y)
            else:
                # In air, previously paused.  Use stored collective position to init point
                self.cpO_y = round(4096 * self.last_collective_y)

            self.spring_y.set_coefficient(4096)
            self.spring_y.cpOffset = self.cpO_y

            self._spring_handle.setCondition(self.spring_y)
            # self.damper.damper(coef_y=int(4096*self.collective_dampening_gain)).start()
            self._spring_handle.start(override=True)
            # print(f"self.cpO_y:{self.cpO_y}, phys_y:{phys_y}")
            if self.cpO_y/4096 - 0.1 < phys_y < self.cpO_y/4096 + 0.1:
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                if self._sim_is_msfs():
                    self.msfs_send_heli_collective_pos(y_var, self.last_pos_y_pos, telem_data)
                    # self._simconnect.send_event_to_msfs(y_var, self.last_pos_y_pos)

                return
        self.last_collective_y = phys_y

        if self.spring_mode_is(SpringModeEnum.FORCETRIM):
            self._spring_handle.name = "collective_ft"
            self.ac_collective_force_trim_override(telem_data, self._spring_handle)
        else:
            self._spring_handle.name = "collective_ap_spring"

            self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
            # print(self.cpO_y)
            self.spring_y.cpOffset = self.cpO_y

            # self.damper.damper(coef_y=int(4096*self.collective_dampening_gain)).start()
            self.spring_y.set_coefficient(round(self.collective_spring_coeff_y / 2))

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start(override=True)

        if self._sim_is_xplane():
            pos_y_pos = utils.scale(phys_y, (-1, 1), (1, 0))
            if self.collective_init:
                self.send_xp_command(f'AXIS:cy={round(pos_y_pos, 5)}')

        if self._sim_is_msfs():
            if self.enable_custom_y_axis:
                y_var = self.custom_y_axis
                y_range = self.raw_y_axis_scale
            else:
                y_var = 'AXIS_COLLECTIVE_SET'
                y_range = 16384

            pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

            if y_range != 1:
                pos_y_pos = -int(pos_y_pos)
            else:
                pos_y_pos = round(pos_y_pos, 5)

            if self.collective_init:
                self.msfs_send_heli_collective_pos(y_var, pos_y_pos, telem_data)
                # self._simconnect.send_event_to_msfs(y_var, pos_y_pos)
                self.last_pos_y_pos = pos_y_pos
