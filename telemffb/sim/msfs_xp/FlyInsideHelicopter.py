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
import telemffb.utils as utils
from .Helicopter import Helicopter
from telemffb.util.conversions import math

import math
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class FlyInsideHelicopter(Helicopter):
    # user parameters
    FI_vibration_enable = True
    FI_vibration_intensity = 0
    FI_vibration_expo = 0
    # end of user parameters

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        # input_data = HapticEffect.device.get_input()
        # self.phys_x, self.phys_y = input_data.axisXY()
        # self.cpO_y = round(self.phys_y * 4096)

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self._update_vibration()

    @override
    def ac_calc_etl_effect(self, *args, **kwargs):
        ## effect not used for FI Heli
        pass

    @override
    def ac_update_vrs_effect(self, *args, **kwargs):
        ## effect not used for FI Heli
        pass

    def _update_vibration(self):
        if not self.FI_vibration_enable:
            self.effects['FI_vibration'].destroy()
            return
        rrpm = self.telem_data.RotorRPM or 0
        if self.is_joystick():
            vx, vy = "FI_VibY", "FI_VibX"
        elif self.is_pedals():
            vx, vy = "FI_VibZ", "FI_VibX"
        elif self.is_collective():
            vx, vy = "FI_VibZ", "FI_VibY"
        else:
            return
        x, y = self.telem_data.get(vx, 0), self.telem_data.get(vy, 0)

        # FI vibration data is initially scaled in sc_overrides.
        # tune there so that the max desired vibration (overspeed probably) is ~1.0

        force = math.hypot(x, y)
        direction = math.degrees(math.atan2(y, x))
        # safety scaling if needed. tested at 50% configurator constant setting
        force *= 1

        # reduce at lower rotor rpm
        force *= utils.scale_clamp(rrpm, (0, 150), (0, 1))

        # crop input values to 1.0 because there may be higher numbers from crashes etc
        force = min(force, 1.0)
        force = utils.expocurve(force, self.FI_vibration_expo)
        force *= self.FI_vibration_intensity

        force = round(force, 4)

        self.effects['FI_vibration'].constant(force, direction).envelope(
             attackFromForce=0,       # Start from zero for noticeable fade-in
             attackTime=1000,          # 1000ms attack
         ).start()