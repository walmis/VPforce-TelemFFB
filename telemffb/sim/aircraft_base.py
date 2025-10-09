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
from typing import List

from telemffb.sim.base.FFBForcesMixIn import FFBForcesMixIn
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

from telemffb.util.conversions import kt2ms, kmh2ms

from telemffb.hw.ffb_rhino import (
    HapticEffect,
    EFFECT_SPRING,
    EFFECT_DAMPER,
    EFFECT_INERTIA,
    EFFECT_FRICTION,
    EFFECT_SPRING_ADJUSTER,
    EFFECT_SQUARE,
    EFFECT_SINE,
)
import telemffb.globals as G
from telemffb.SettingsManager import GEffectModeEnum, SpringModeEnum

# Highpass filter dispenser
HPFs: utils.Dispenser = utils.Dispenser(utils.HighPassFilter)

# Lowpass filter dispenser
LPFs: utils.Dispenser = utils.Dispenser(utils.LowPassFilter)

G.effects = utils.Dispenser(HapticEffect)


class AircraftBase(
    GForceEffectMixIn,
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

    cpO_x = 0
    cpO_y = 0
    

    

    # AoA reduction effect moved into AoAReductionMixIn

    # gear motion attributes moved to MotionEffectsMixIn

    ####
    #### Beta effects - set to 1 to enable (moved to DecelerationEffectMixIn)

    damper_coeff: int = 0
    inertia_coeff: int = 0
    friction_coeff: int = 0

    # runway_rumble attributes moved to MotionEffectsMixIn
    # Engine rumble attributes moved to EngineRumbleMixIn

    # gforce_effect_enable : bool = False

    # canopy motion attributes moved to MotionEffectsMixIn

    
    elevator_droop_enabled: bool = False
    elevator_droop_force: float = 0.0
    aircraft_is_fbw: bool = False           #deprecated

    # motion effect flags and weapon/weapon-release attributes moved to MixIns

    # gun/countermeasure attributes moved to WeaponsEffectMixIn


    # spring_mode = G.JoystickSpringMode.BASIC

    ## 0=DCS Default | 1=spring disabled (Heli)), 2=spring enabled at %100 (FW)
    # pedal_spring_mode = G.PedalSpringMode.STATIC

    aircraft_vs_speed = 87
    aircraft_vs_gain = 0.25
    aircraft_vne_speed = 435
    aircraft_vne_gain = 1.0

    pedal_spring_coeff_x = 0

    # Advanced spring override and trim attributes moved into AdvancedSpringMixIn
    trimwheel_elev_up_button: int = 0
    trimwheel_elev_dn_button: int = 0
    trimwheel_use_master_buttons: bool = False
    trimwheel_axis_invert: bool = False
    trimwheel_use_axis: bool = False

    # enable_deadzone and deadzone_base_pct moved into DeadzoneMixIn

    g_y_offset: int = 0


    def __init__(self, name: str, **kwargs):
        super().__init__()

        self._name = name

        self.adv_g_settings_dict: dict = {}
        self.adv_spr_settings_dict: dict = {}


        # hydraulic_factor is initialised in HydraulicLossMixIn.__init__
        # clear any existing effects
        self.effects.clear()

    # spring_adjuster_x/y and spring_adjuster are initialised in AdvancedSpringMixIn.__init__


        self.spring_mode = SpringModeEnum.NONE.name
        self.gforce_effect_mode = GEffectModeEnum.DISABLED.name



    def ac_override_elevator_droop(self, telem_data):
        if not self.is_joystick():
            return
        if not self.elevator_droop_enabled or not self.elevator_droop_force:
            self.effects.dispose('elev_droop')
            return

        if telem_data['TAS'] < 20 * kt2ms:
            force = utils.scale_clamp(telem_data['TAS'], (20 * kt2ms, 0), (0, self.elevator_droop_force))
            self.effects['elev_droop'].constant(force, 180).start()
            logging.debug(f"override elevator:{force}")
        else:
            self.effects.dispose('elev_droop')


    def on_event(self, event, *args):
        super().on_event(event, *args)

    def on_timeout(self):  # override me
        logging.info("Telemetry Timeout, stopping effects")
        # effects.foreach(lambda e: e.stop())
        for key, effect in self.effects.dict.items():
            effect: HapticEffect
            if self.keep_forces_on_pause:
                if effect.effect_type in [EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION, EFFECT_SPRING_ADJUSTER]:
                    continue
            effect.stop()

        super().on_timeout()

    def on_telemetry(self, telem_data): 
        fx,fy = HapticEffect.device.get_input().forceXY()
        self.telem_data['ForceXY'] = [fx,fy]
        jx, jy = HapticEffect.device.get_input().axisXY()
        self.telem_data['JoyXY'] = [jx, jy]
        
        super().on_telemetry(telem_data)

        if self.is_joystick():
            self.ac_override_elevator_droop(telem_data)






