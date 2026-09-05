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

from telemffb.hw.ffb_rhino import HapticEffect
from .Helicopter import Helicopter
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class SASHelicopter(Helicopter):
    # user parameters
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
    # end of user parameters

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        
        self.phys_x, self.phys_y = self._get_device_axes()
        self.cpO_y = self.phys_y


    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)


    def on_timeout(self):
        super().on_timeout()


    @override
    def msfs_update_heli_controls(self, telem_data: BaseTelemetryData):
        super().msfs_update_heli_controls(telem_data)
        ffb_type = telem_data.FFBType or "joystick"
        ap_active = telem_data.APMaster or 0
        # trim_reset = max(telem_data.get("h145TrimRelease", 0), telem_data.get("h160TrimRelease", 0))
        trim_reset = telem_data.hpgTrimRelease or 0

        if ffb_type == "joystick":
            if not self.telemffb_controls_axes and not self.local_disable_axis_control:
                self.flag_error(
                    "Aircraft is configured as class SASHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the cyclic axes in MSFS settings")
                return
            sema_x = telem_data.SEMAx or 0
            sema_y = telem_data.SEMAy or 0

            sema_x_avg = self.smoother.get_rolling_average('s_sema_x', sema_x, window_ms=100)
            sema_y_avg = self.smoother.get_rolling_average('s_sema_y', sema_y, window_ms=100)

            if not trim_reset:
                sx = round(abs(sema_x_avg), 3)
                sy = round(abs(sema_y_avg), 3)

                self.afcsx_step_size = sx * 0.1 / 4096
                self.afcsy_step_size = sy * 0.3 / 4096

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

            self.spring_x.set_offset(self.cpO_x)
            self.spring_y.set_offset(self.cpO_y)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)

            if telem_data.hpgHandsOnCyclic:
                hands_on_dict = self.check_hands_on(self.hands_off_deadzone)
            else:
                hands_on_dict = self.check_hands_on(self.hands_on_deadzone)
            hands_on_either = hands_on_dict["master_result"]

            self._dispatch_hands_on_state(telem_data, hands_on_dict, hands_on_either)

            self._spring_handle.start()

    @override
    def _update_cyclic_trim(self, telem_data: BaseTelemetryData):
        # Trimming is handled by the AFCS integration - override parent class function
        pass
