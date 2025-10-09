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

from telemffb.hw.ffb_rhino import HapticEffect
from .Helicopter import Helicopter


class SASHelicopter(Helicopter):
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

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        input_data = HapticEffect.device.get_input()
        self.phys_x, self.phys_y = input_data.axisXY()
        self.cpO_y = round(self.phys_y * 4096)


    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)


    def on_timeout(self):
        super().on_timeout()


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
        # trim_reset = max(telem_data.get("h145TrimRelease", 0), telem_data.get("h160TrimRelease", 0))
        trim_reset = telem_data.get("hpgTrimRelease", 0)

        if ffb_type == "joystick":
            if not self.telemffb_controls_axes and not self.local_disable_axis_control:
                self.flag_error(
                    "Aircraft is configured as class SASHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the cyclic axes in MSFS settings")
                return
            sema_x = telem_data.get("SEMAx", 0)
            sema_y = telem_data.get("SEMAy", 0)

            sema_x_avg = self.smoother.get_rolling_average('s_sema_x', sema_x, window_ms=100)
            sema_y_avg = self.smoother.get_rolling_average('s_sema_y', sema_y, window_ms=100)
            # sema_x_avg = sema_x
            # sema_y_avg = sema_y

            if not trim_reset:
                sx = round(abs(sema_x_avg), 3)
                sy = round(abs(sema_y_avg), 3)

                #if 100 >= sx >= 50:
                    #self.afcsx_step_size = 5
                #elif 49.999 > sx >= 20:
                    #self.afcsx_step_size = 3
                #elif 19.999 > sx >= 10:
                    #self.afcsx_step_size = 2
                #elif 9.999 > sx >= 5:
                    #self.afcsx_step_size = 1
                #elif 4.999 > sx >= 0:
                    #self.afcsx_step_size = 1
                #else:
                    #self.afcsx_step_size = 0

                #if 100 >= sy >= 50:
                    #self.afcsy_step_size = 5
                #elif 49.999 > sy >= 20:
                    #self.afcsy_step_size = 3
                #elif 19.999 > sy >= 10:
                    #self.afcsy_step_size = 2
                #elif 9.999 > sx >= 5:
                    #self.afcsy_step_size = 1
                #elif 4.999 > sy >= 0:
                    #self.afcsy_step_size = 1
                #else:
                    #self.afcsy_step_size = 0

                self.afcsx_step_size = sx * 0.1
                self.afcsy_step_size = sy * 0.3

                if not (self.hands_on_x_active or self.hands_on_active):
                    if sema_x_avg > 0:
                        self.cpO_x -= self.afcsx_step_size
                    elif sema_x_avg < 0:
                        self.cpO_x += self.afcsx_step_size

                if not (self.hands_on_y_active or self.hands_on_active):
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

            telem_data["hands_on"] = int(hands_on_either)
            telem_data["hands_on_x"] = int(hands_on_x)
            telem_data["hands_on_y"] = int(hands_on_y)
            telem_data["deviation_x"] = dev_x
            telem_data["deviation_y"] = dev_y

            self._spring_handle.start()

    def _update_cyclic_trim(self, telem_data):
        # Trimming is handled by the AFCS integration - override parent class function
        pass