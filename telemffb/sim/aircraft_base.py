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


import logging
import math

import telemffb.utils as utils

from telemffb.sim.base.WindEffectMixIn import WindEffectMixIn
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.base.DecelerationEffectMixIn import DecelerationEffectMixIn
from telemffb.sim.base.EngineRumbleMixIn import EngineRumbleMixIn
from telemffb.sim.base.MotionEffectsMixIn import MotionEffectsMixIn
from telemffb.sim.base.WeaponsEffectMixIn import WeaponsEffectMixIn
from telemffb.sim.base.BuffetingEffectMixIn import BuffetingEffectMixIn
from telemffb.sim.base.HelicopterEffectsMixIn import HelicopterEffectsMixIn
from telemffb.sim.base.DeadzoneMixIn import DeadzoneMixIn
from telemffb.sim.base.HydraulicLossMixIn import HydraulicLossMixIn
from telemffb.sim.base.GForceEffectMixIn import GForceEffectMixIn
from telemffb.sim.base.PedalSpringOverrideMixIn import PedalSpringOverrideMixIn
from telemffb.sim.base.AoAEffectsMixIn import AoAEffectsMixIn

from telemffb.hw.ffb_rhino import (
    HapticEffect,
    EFFECT_SPRING,
    EFFECT_DAMPER,
    EFFECT_INERTIA,
    EFFECT_FRICTION,
    EFFECT_SPRING_ADJUSTER,
)
import telemffb.globals as G
from telemffb.SettingsManager import GEffectModeEnum, SpringModeEnum

# initialize the global effects dispenser
G.effects = utils.Dispenser(HapticEffect)


class AircraftBase(
    PedalSpringOverrideMixIn,
    HelicopterEffectsMixIn,
    WeaponsEffectMixIn,
    DeadzoneMixIn,
    HydraulicLossMixIn,
    DecelerationEffectMixIn,
    EngineRumbleMixIn,
    AoAEffectsMixIn,
    WindEffectMixIn,
    AdvancedSpringMixIn,
    MotionEffectsMixIn,
    BuffetingEffectMixIn,
):
    """Base class for all aircraft types, providing common functionality and state management."""

    def __init__(self, name: str, **kwargs):
        super().__init__()
        self._name = name

        # clear any existing effects
        self.effects.clear()
        
    def on_event(self, event, *args):
        super().on_event(event, *args)

    def on_timeout(self):  # override me
        logging.info("Telemetry Timeout, stopping effects")
        # effects.foreach(lambda e: e.stop())
        for key, effect in self.effects.dict.items():
            effect: HapticEffect
            if self.keep_forces_on_pause:
                if effect.effect_type in [
                    EFFECT_SPRING,
                    EFFECT_DAMPER,
                    EFFECT_INERTIA,
                    EFFECT_FRICTION,
                    EFFECT_SPRING_ADJUSTER,
                ]:
                    continue
            effect.stop()

        super().on_timeout()

    def on_telemetry(self, telem_data):
        fx, fy = HapticEffect.device.get_input().forceXY()
        self.telem_data["ForceXY"] = [fx, fy]
        jx, jy = HapticEffect.device.get_input().axisXY()
        self.telem_data["JoyXY"] = [jx, jy]

        super().on_telemetry(telem_data)
