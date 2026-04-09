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
import math
from typing import override
from .Helicopter import Helicopter
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.util.conversions import kt2ms, ms2kt
from telemffb.utils import clamp, PerformanceTracker
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


import logging
import time

perftracker = PerformanceTracker()

class XAW109Helicopter(Helicopter):
    afcs_step_size = 2
    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 0
    collective_spring_coeff_y = 0
    hands_on_deadzone = 0.1
    hands_off_deadzone = 0.05
    feet_on_deadzone = 0.05
    feet_off_deadzone = 0.03
    hands_on_active = 0
    hands_on_x_active = 0
    hands_on_y_active = 0
    feet_on_active = 0
    send_individual_hands_on = 0
    vrs_effect_enable: bool = True
    vrs_effect_intensity = 0
    afcs_motion_rate: int = 4
    afcs_threshold_value: float = 0.4

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        self.phys_x, self.phys_y = self._get_device_axes()
        self.cpO_y = round(self.phys_y * 4096)


    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)


    def on_timeout(self):
        super().on_timeout()

    def msfs_update_heli_controls(self, telem_data: BaseTelemetryData):
        super().msfs_update_heli_controls(telem_data)
        ffb_type = telem_data.FFBType or "joystick"
        ap_active = telem_data.APMaster or 0

        if ffb_type == "joystick":
            phys_x, phys_y = self._get_device_axes()
            telem_data.act_target_roll = phys_x
            telem_data.act_target_pitch = phys_y
            x_rate = telem_data.AW109_aileron_trim_rate or 0
            y_rate = telem_data.AW109_elevator_trim_rate or 0

            # self.cpO_x += x_rate
            # self.cpO_y += y_rate
            self.afcsx_step_size = x_rate * self.afcs_motion_rate
            self.afcsy_step_size = y_rate * self.afcs_motion_rate

            self.cpO_x += self.afcsx_step_size
            self.cpO_y += self.afcsy_step_size


            self.spring_x.cpOffset = round(self.cpO_x)
            self.spring_y.cpOffset = round(self.cpO_y)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)

            self._spring_handle.start()



    def _update_cyclic_trim(self, telem_data: BaseTelemetryData):
        # Trimming is handled by the AFCS integration - override parent class function
        pass

    def msfs_update_pedals(self, telem_data: BaseTelemetryData):

        if telem_data.FFBType != 'pedals':
            return

        # if self.telemffb_controls_axes and not self.local_disable_axis_control:
        phys_x, phys_y = self._get_device_axes()
        telem_data.phys_x = phys_x
        telem_data.pedal_position = phys_x
        telem_data.IAS_kt = (telem_data.IAS or 0) * ms2kt
        # if self.pedal_ft_release_button:
        #     state = input_data.isButtonPressed(self.pedal_ft_release_button)
        #     self.trigger_xp_event("SPECIAL/buttons/cmd_ft_ped_rel", state=state, type="track")

        self._spring_handle.name = "pedal_ap_spring"

        if not self.pedals_init:

            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = self.pedal_spring_coeff_x
            if telem_data.get("SimOnGround", 1):
                self.cpO_x = 0
            else:
                # print(f"last_colelctive_y={self.last_collective_y}")
                self.cpO_x = round(4096 * self.last_pedal_x)

            self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = round(
                4096 * utils.clamp(self.pedal_spring_gain, 0, 1))

            self.spring_x.cpOffset = self.cpO_x

            self._spring_handle.setCondition(self.spring_x)
            # self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
            self._spring_handle.start()
            logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
            if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                # dont start sending position until physical pedals have centered
                self.pedals_init = 1
                logging.info("Pedals Initialized")
            else:
                return
            self.running_trim_total = 0


        pedal_ft_released = telem_data.AW109_ped_force_trim_release_pressed or 0


        trim_coarse = telem_data.AW109_rud_trim_coarse or 0
        trim_fine = telem_data.AW109_rud_trim_fine or 0
        trim_zero = telem_data.AW109_rud_trim_zero or 0

        trim_total = trim_coarse + trim_zero + trim_fine
        trim_total_abs = abs(trim_total)
        telem_data._AW109_rud_trim_total = trim_total
        telem_data._AW109_rud_trim_total_abs = trim_total_abs

        # trim_threshold_high = telem_data.AW109_rud_trim_thresh_hi or 0
        trim_threshold = self.afcs_threshold_value
        telem_data._AW109_rud_trim_threshold = trim_threshold

        trim_required_calc = trim_total_abs > trim_threshold
        telem_data._AW109_rud_trim_required_calc = trim_required_calc

        trim_step_size = self.afcs_motion_rate
        trim_step_size = -trim_step_size if trim_total < 0 else trim_step_size


        if pedal_ft_released:
            if self.pedal_ft_damper_enabled:
                force = int(self.pedal_ft_damper_force * 4096)
            else:
                force = 0
            self.cpO_x = round(4096 * utils.clamp(phys_x, -1, 1))
            # print(self.cpO_y)
            self.spring_x.cpOffset = self.cpO_x

            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = force

            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()
        else:
            # if not (self.feet_on_active):
                # self.cpO_x -= self.afcsx_step_size
            trim_threshold = 1

            if trim_required_calc:
                self.cpO_x += trim_step_size
                telem_data._telemffb_moving_rud = True
            else:
                telem_data._telemffb_moving_rud = False




            force = round(4096 * self.pedal_spring_gain)
            self.spring_x.cpOffset = round(self.cpO_x)
            self.spring_y.cpOffset = 0
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 0
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = int(self.pedal_spring_gain * force)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()


        self.last_pedal_x = phys_x


    def msfs_update_collective(self, telem_data: BaseTelemetryData):
        if telem_data.FFBType != 'collective':
            return

        self._spring_handle.name = "collective_ap_spring"
        # self.damper = effects["collective_damper"].damper()

        input_data = HapticEffect.device.get_input()
        # force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)

        collective_ft_released = telem_data.AW109_col_force_trim_release_pressed or 0

        collective_rate = telem_data.AW109_collective_trim_rate or 0

        collective_v_mode = telem_data.AW109_collective_mode or 0

        collective_afcs_pos = telem_data.AW109_collective_ratio or 0
        axis_afcs_pos = utils.scale_clamp(collective_afcs_pos, (0,1), (4096, -4096), return_int=True)

        phys_x, phys_y = self._get_device_axes()
        telem_data.phys_y = phys_y

        if not self.collective_init:

            self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)

            if telem_data.get("SimOnGround", 1):
                # Sim is on ground - set init point to full down
                self.cpO_y = 4096
            elif self.last_collective_y is None:
                # Air start or new aircraft.  Use current physical position as init point
                self.cpO_y = round(4096 * phys_y)
            else:
                # set init point to last point before pause
                self.cpO_y = round(4096 * self.last_collective_y)

            self.spring_y.cpOffset = self.cpO_y

            self._spring_handle.setCondition(self.spring_y)

            self._spring_handle.start()

            if self.cpO_y / 4096 - 0.1 < phys_y < self.cpO_y / 4096 + 0.1:
                # Check if phys y position is within %10 of init point
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                return
        # Only reach here if collective position is initialized
        self.last_collective_y = phys_y

        if collective_ft_released:

            self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
            # print(self.cpO_y)
            self.spring_y.cpOffset = self.cpO_y

            # self.damper.damper(coef_y=0).start()
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = int(
                4096 * self.trim_release_spring_gain)

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()
        else:
            if collective_v_mode:
                self.cpO_y = axis_afcs_pos
            # self.cpO_y -= self.afcsy_step_size
            self.spring_y.cpOffset = round(self.cpO_y)

            # self.damper.damper(coef_y=0).start()
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 4096
            self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()