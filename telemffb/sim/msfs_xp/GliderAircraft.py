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

from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.msfs_xp.Aircraft import Aircraft
# removed local 'overrides' helper in favor of typing.override


import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class GliderAircraft(Aircraft):
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

    def msfs_update_force_trim(self, telem_data: BaseTelemetryData, x_axis=True, y_axis=True):
        if not self.spring_mode_is(SpringModeEnum.CNTR_FT):
            return

        ffb_type = telem_data.FFBType or "joystick"
        offs_x = 0
        offs_y = 0
        if ffb_type != "joystick":
            return
        if self.force_trim_button == 0:
            self.flag_error("Force trim enabled but buttons not configured")
            return

        self._spring_handle.name = "dynamic_spring"
        # logging.debug(f"update_force_trim: x={x_axis}, y={y_axis}")
        input_data = HapticEffect.device.get_input()
        force_trim_pressed = self.check_button_press(self.force_trim_button, self.collective_ft_use_master_buttons)
        # force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)
        if self.force_trim_reset_button > 0:
            trim_reset_pressed = input_data.isButtonPressed(self.force_trim_reset_button)
        else:
            trim_reset_pressed = False
        x, y = self._get_device_axes()
        if force_trim_pressed:
            if x_axis:
                self.spring_x.set_coefficient(0.5)
                self.spring_x.set_offset(x)
                self._spring_handle.setCondition(self.spring_x)

            if y_axis:
                self.spring_y.set_offset(y)
                self.spring_y.set_coefficient(0.5)
                self._spring_handle.setCondition(self.spring_y)

            self.stick_center = [x,y]

            logging.debug(f"Force Trim Disengaged:{round(x * 4096)}:{round(y * 4096)}")

            self.force_trim_release_active = 1

        if not force_trim_pressed and self.force_trim_release_active:
            self.spring_x.set_coefficient(self.aileron_spring_gain, True)
            self.spring_y.set_coefficient(self.elevator_spring_gain, True)
            if x_axis:
                self.spring_x.set_offset(x)
                self._spring_handle.setCondition(self.spring_x)

            if y_axis:
                self.spring_y.set_offset(y)
                self._spring_handle.setCondition(self.spring_y)

            # self.spring.start()
            self.stick_center = [x, y]

            logging.debug(f"Force Trim Engaged :{self.spring_x.cpOffset}:{self.spring_y.cpOffset}")

            self.force_trim_release_active = 0

        if trim_reset_pressed or not self.force_trim_spring_init:
            if trim_reset_pressed:
                self.stick_center = [0.0, 0.0]

            if x_axis:
                self.spring_x.set_coefficient(self.aileron_spring_gain, True)
                self.spring_x.set_offset(self.cyclic_center[0])
                self._spring_handle.setCondition(self.spring_x)

            if y_axis:
                self.spring_y.set_coefficient(self.elevator_spring_gain, True)
                self.spring_y.set_offset(self.cyclic_center[1])
                self._spring_handle.setCondition(self.spring_y)

            # self.spring.start()
            self.force_trim_spring_init = 1
            logging.info("Trim Reset Pressed")
            return

        telem_data.StickXY = [x, y]
        telem_data.StickXY_offset = self.stick_center
        self.force_trim_x_offset = self.stick_center[0]
        self.force_trim_y_offset = self.stick_center[1]

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        if telem_data.N is None:
            return
        telem_data.AircraftClass = "GliderAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)

        self.msfs_update_force_trim(telem_data, x_axis=self.aileron_force_trim,
                                                y_axis=self.elevator_force_trim)
