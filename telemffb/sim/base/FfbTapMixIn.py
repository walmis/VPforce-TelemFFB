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

"""Game spring via the DirectInput FFB tap - sim-agnostic.

Spring mode DINPUT_TAP ('Game Managed (DirectInput Tap)'): the sim
computes its own spring but TelemFFB renders it.  Sims that render their
own force feedback (DCS, IL-2, BMS) take exclusive access of the device,
which is what has always kept companion software off it; the
TelemFFB-DInput-Tap wrapper's 'tap' device policy absorbs the sim's
output and publishes its live effect state into a shared-memory mirror
instead, leaving the device free.  This mixin reconciles the mirrored
spring onto the device each telemetry frame - trim center follow,
force-trim coefficient collapse and the rest reproduced exactly - so the
sim's own forces and TelemFFB's telemetry-driven effects share one
device.  The reader and unit translation live in telemffb.hw.ffb_tap.

Works identically on VPforce hardware (raw-HID rendering) and generic
DirectInput devices (DI bridge).  A distinct mode and effect from IL-2
Korea's native ffbdevice-records source (spring mode TELEM /
il2_ffb_spring) - the two never run together.
"""

from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition


class FfbTapMixIn:
    # Axis-orientation corrections for the mirrored game spring.
    # Counterparts to DCS FFTune's swap/invert settings - those transform
    # the game's FFB output and so bake into the tap; corrections belong
    # here where the re-rendering happens, with the game-side settings
    # left at neutral.
    tap_spring_swap_axes = False
    tap_spring_invert_x = False
    tap_spring_invert_y = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tap_cond_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self._tap_cond_y = FFBReport_SetCondition(parameterBlockOffset=1)

    def ffb_tap_spring(self) -> bool:
        """Render the game-computed spring published by the DirectInput tap.

        Returns True while the tap spring is rendering; False - with this
        mode's effect stopped - when the mode is not DINPUT_TAP, no tap
        writer is publishing, the tapped device is paused, or the game's
        spring is stopped (e.g. in the menus, where the stick intentionally
        goes spring-less).
        """
        if not self.is_joystick():
            return False
        if not self.spring_mode_is(SpringModeEnum.DINPUT_TAP):
            self.effects['ffb_tap_spring'].stop()
            return False

        from telemffb.hw import ffb_tap
        state = ffb_tap.read_game_spring()
        self.telem_data['FFB_Tap'] = 'active' if state else 'inactive'
        if state is None:
            self.effects['ffb_tap_spring'].stop()
            return False

        # axis-orientation corrections (see the tap_spring_* attrs)
        x_state, y_state = state.x, state.y
        if self.tap_spring_swap_axes:
            x_state, y_state = y_state, x_state
        if self.tap_spring_invert_x and x_state is not None:
            x_state = x_state.inverted()
        if self.tap_spring_invert_y and y_state is not None:
            y_state = y_state.inverted()

        spring = self.effects['ffb_tap_spring'].spring()
        for axis_state, cond, axis_name in ((x_state, self._tap_cond_x, 'X'),
                                            (y_state, self._tap_cond_y, 'Y')):
            if axis_state is None:
                continue
            cond.cpOffset = axis_state.offset
            cond.positiveCoefficient = axis_state.positive_coefficient
            cond.negativeCoefficient = axis_state.negative_coefficient
            cond.positiveSaturation = axis_state.positive_saturation
            cond.negativeSaturation = axis_state.negative_saturation
            cond.deadBand = axis_state.deadband
            spring.setCondition(cond)
            self.telem_data[f'FFB_{axis_name}_Force'] = round(
                axis_state.positive_coefficient / 4096, 4)
            self.telem_data[f'FFB_{axis_name}_Center'] = round(
                axis_state.offset / 4096, 4)
        spring.start(override=True)
        return True
