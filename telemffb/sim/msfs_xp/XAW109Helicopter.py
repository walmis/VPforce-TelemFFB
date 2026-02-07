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
from .Helicopter import Helicopter
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.util.conversions import kt2ms
from telemffb.utils import clamp, PerformanceTracker


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

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        input_data = HapticEffect.device.get_input()
        self.phys_x, self.phys_y = input_data.axisXY()
        self.cpO_y = round(self.phys_y * 4096)


    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)


    def on_timeout(self):
        super().on_timeout()

    def check_feet_on(self, percent):
        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        # Convert phys input to +/-4096
        phys_x = round(phys_x * 4096)

        ref_x = self.cpO_x

        # Calculate the threshold values based on the input percentage
        threshold = 4096 * percent

        # Calculate the deviation percentages in decimal form
        deviation_x = abs(phys_x - ref_x) / 4096

        # Check if either phys_x or phys_y exceeds the threshold
        x_exceeds_threshold = abs(phys_x - ref_x) > threshold

        result = {
            "result": x_exceeds_threshold,
            "deviation": deviation_x,
        }

        return result

    def check_hands_on(self, percent):
        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        # Convert phys input to +/-4096
        phys_x = round(phys_x * 4096)
        phys_y = round(phys_y * 4096)

        ref_x = self.cpO_x
        ref_y = self.cpO_y

        # Calculate the threshold values based on the input percentage
        threshold = 4096 * percent

        # Calculate the deviation percentages in decimal form
        deviation_x = abs(phys_x - ref_x) / 4096
        deviation_y = abs(phys_y - ref_y) / 4096

        # Check if either phys_x or phys_y exceeds the threshold
        x_exceeds_threshold = abs(phys_x - ref_x) > threshold
        y_exceeds_threshold = abs(phys_y - ref_y) > threshold
        master_exceeds_threshold = x_exceeds_threshold or y_exceeds_threshold

        result = {
            "master_result": master_exceeds_threshold,
            "x_result": x_exceeds_threshold,
            "x_deviation": deviation_x,
            "y_result": y_exceeds_threshold,
            "y_deviation": deviation_y,
        }

        return result

    def msfs_update_heli_controls(self, telem_data):
        super().msfs_update_heli_controls(telem_data)
        ffb_type = telem_data.get("FFBType", "joystick")
        ap_active = telem_data.get("APMaster", 0)

        if ffb_type == "joystick":

            x_rate = telem_data.get("AW109_aileron_trim_rate", 0)
            y_rate = telem_data.get("AW109_elevator_trim_rate", 0)

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


    def alternative_msfs_update_heli_controls(self, telem_data):
        super().msfs_update_heli_controls(telem_data)
        ffb_type = telem_data.get("FFBType", "joystick")
        ap_active = telem_data.get("APMaster", 0)
        # trim_reset = max(telem_data.get("h145TrimRelease", 0), telem_data.get("h160TrimRelease", 0))
        trim_reset = telem_data.get("hpgTrimRelease", 0)

        if ffb_type == "joystick":
            # if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            #     self.flag_error(
            #         "Aircraft is configured as class SASHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the cyclic axes in MSFS settings")
            #     return
            sema_x = -telem_data.get("AW109_aileron_trim_req", 0)
            sema_y = -telem_data.get("AW109_elevator_trim_req", 0)
            x_rate = telem_data.get("AW109_aileron_trim_rate", 0)
            y_rate = telem_data.get("AW109_elevator_trim_rate", 0)

            # sema_x_avg = self.smoother.get_rolling_average('s_sema_x', sema_x, window_ms=100)
            # sema_y_avg = self.smoother.get_rolling_average('s_sema_y', sema_y, window_ms=100)
            sema_x_avg = sema_x
            sema_y_avg = sema_y

            if not trim_reset:
                sx = round(abs(sema_x_avg), 3)
                sy = round(abs(sema_y_avg), 3)

                # self.cpO_x += x_rate
                # self.cpO_y += y_rate
                self.afcsx_step_size = sx * .5
                self.afcsy_step_size = sy * .5

                # if not (self.hands_on_x_active or self.hands_on_active):
                if abs(sema_x_avg) > 0.5:
                    utils.dbprint('blue', f"sema_x_avg={sema_x_avg}")
                    if sema_x_avg > 0:
                        self.cpO_x -= self.afcsx_step_size
                    elif sema_x_avg < 0:
                        self.cpO_x += self.afcsx_step_size

                # if not (self.hands_on_y_active or self.hands_on_active):
                if abs(sema_y_avg) > 0.5:
                    utils.dbprint('yellow', f"sema_y_avg={sema_y_avg}")
                    if sema_y_avg > 0:
                        self.cpO_y -= self.afcsy_step_size
                    elif sema_y_avg < 0:
                        self.cpO_y += self.afcsy_step_size

            self.spring_x.cpOffset = round(self.cpO_x)
            self.spring_y.cpOffset = round(self.cpO_y)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)

            # hands_off_deadzone = 0.02
            if telem_data.get("hpgHandsOnCyclic", 0):
                hands_on_dict = self.check_hands_on(self.hands_off_deadzone)
            else:
                hands_on_dict = self.check_hands_on(self.hands_on_deadzone)
            hands_on_either = hands_on_dict["master_result"]
            hands_on_x = hands_on_dict["x_result"]
            dev_x = hands_on_dict["x_deviation"]
            hands_on_y = hands_on_dict["y_result"]
            dev_y = hands_on_dict["y_deviation"]
            # if self.send_individual_hands_on:
            #     if hands_on_x:
            #         self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICX", 1, units="number")
            #         self.hands_on_x_active = True
            #
            #     else:
            #         self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICX", 0, units="number")
            #         self.hands_on_x_active = False
            #
            #     if hands_on_y:
            #         self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICY", 1, units="number")
            #         self.hands_on_y_active = True
            #     else:
            #         self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICY", 0, units="number")
            #         self.hands_on_y_active = False
            # else:
            #     if hands_on_either:
            #         self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLIC", 1, units="number")
            #         self.hands_on_active = True
            #     else:
            #         self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLIC", 0, units="number")
            #         self.hands_on_active = False

            telem_data["hands_on"] = int(hands_on_either)
            telem_data["hands_on_x"] = int(hands_on_x)
            telem_data["hands_on_y"] = int(hands_on_y)
            telem_data["deviation_x"] = dev_x
            telem_data["deviation_y"] = dev_y

            self._spring_handle.start()

    def _update_cyclic_trim(self, telem_data):
        # Trimming is handled by the AFCS integration - override parent class function
        pass

    def msfs_update_pedals(self, telem_data):

        if telem_data.get("FFBType") != 'pedals':
            return

        # if self.telemffb_controls_axes and not self.local_disable_axis_control:
        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()
        telem_data['phys_x'] = phys_x

        x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

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

        pedal_ft_released = telem_data.get("AW109_ped_force_trim_release_pressed", 0)

        sema_yaw = -telem_data.get("AW109_rudder_trim_req", 0)

        yaw_rate = telem_data.get("AW109_rudder_trim_rate",0)

        sema_yaw_avg = yaw_rate

        sx = round(abs(sema_yaw_avg), 3)

        self.afcsx_step_size = sx * self.afcs_motion_rate
        # self.afcsx_step_size = yaw_rate


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
            if sema_yaw > 0:
                self.cpO_x -= self.afcsx_step_size
            elif sema_yaw < 0:
                self.cpO_x += self.afcsx_step_size

            self.spring_x.cpOffset = round(self.cpO_x)
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = int(self.pedal_spring_gain * 4096)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()


        self.last_pedal_x = phys_x

    def msfs_update_collective(self, telem_data):
        if telem_data.get("FFBType") != 'collective':
            return

        self._spring_handle.name = "collective_ap_spring"
        # self.damper = effects["collective_damper"].damper()

        input_data = HapticEffect.device.get_input()
        # force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)

        collective_ft_released = telem_data.get("AW109_col_force_trim_release_pressed", 0)

        collective_rate = telem_data.get("AW109_collective_trim_rate", 0)

        collective_v_mode = telem_data.get("AW109_collective_mode", 0)

        collective_afcs_pos = telem_data.get("AW109_collective_ratio", 0)
        axis_afcs_pos = utils.scale_clamp(collective_afcs_pos, (0,1), (4096, -4096), return_int=True)
        telem_data['AW109_collective_axis_afcs_pos'] = axis_afcs_pos
        # if collective_rate > 0.05:
        #     step = 1
        # elif collective_rate < -0.05:
        #     step = -1
        # else:
        #     step = 0
        # abs_rate = abs(collective_rate)
        # if abs_rate > 0.025:
        #     step = 5
        # elif 0.02 <= abs_rate <= 0.025:
        #     step = 4
        # elif 0.015 <= abs_rate <= 0.02:
        #     step = 3
        # elif 0.01 <= abs_rate <= 0.015:
        #     step = 1
        # else:
        #     step = 0
        # # self.afcsy_step_size = step * self.afcs_motion_rate
        # step = step * self.afcs_motion_rate
        # self.afcsy_step_size = step if collective_rate > 0 else -step

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()
        telem_data['phys_y'] = phys_y

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

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()

    def alt_msfs_update_collective(self, telem_data):
        if telem_data.get("FFBType") != 'collective':
            return

        self._spring_handle.name = "collective_ap_spring"
        # self.damper = effects["collective_damper"].damper()

        input_data = HapticEffect.device.get_input()
        # force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)


        collective_ft_released = telem_data.get("AW109_col_force_trim_release_pressed", 0)

        collective_rate = telem_data.get("AW109_collective_trim_rate", 0)

        collective_v_mode = telem_data.get("AW109_collective_mode", 0)

        # if collective_rate > 0.05:
        #     step = 1
        # elif collective_rate < -0.05:
        #     step = -1
        # else:
        #     step = 0
        abs_rate = abs(collective_rate)
        if abs_rate > 0.025:
            step = 5
        elif 0.02 <= abs_rate <= 0.025:
            step = 4
        elif 0.015 <= abs_rate <= 0.02:
            step = 3
        elif 0.01 <= abs_rate <= 0.015:
            step = 1
        else:
            step = 0
        # self.afcsy_step_size = step * self.afcs_motion_rate
        step = step * self.afcs_motion_rate
        self.afcsy_step_size = step if collective_rate > 0 else -step

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()
        telem_data['phys_y'] = phys_y

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

            self.cpO_y = round(4096*utils.clamp(phys_y, -1, 1))
            # print(self.cpO_y)
            self.spring_y.cpOffset = self.cpO_y

            # self.damper.damper(coef_y=0).start()
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = int(4096 * self.trim_release_spring_gain)

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()


        else:
            self.cpO_y -= self.afcsy_step_size
            self.spring_y.cpOffset = round(self.cpO_y)

            # self.damper.damper(coef_y=0).start()
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 4096

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()


    def _update_vrs_effect(self, telem_data):
        pass