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
from telemffb.sim.base.WindEffectMixIn import WindEffectMixIn
import telemffb.utils as utils
from typing import List

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
    

    keep_forces_on_pause: bool = True
    enable_damper_ovd: bool = False
    damper_force: float = 0
    enable_inertia_ovd: bool = False
    inertia_force: float = 0
    enable_friction_ovd: bool = False
    friction_force: float = 0

    
    



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

    # per-instance motion state (last_device_x/last_device_y/smoother) moved to MotionEffectsMixIn.__init__

    # Stubs for MixIn methods — keep these on AircraftBase so callers can resolve them directly.
    # They delegate to the MixIn implementations via super().
    def ac_calc_etl_effect(self, telem_data, blade_ct=None):
        """Calculate ETL/overspeed shake effects (delegates to HelicopterEffectsMixIn)."""
        return super().ac_calc_etl_effect(telem_data, blade_ct=blade_ct)

    def ac_update_vrs_effect(self, telem_data):
        """Update Vortex Ring State (VRS) effect (delegates to HelicopterEffectsMixIn)."""
        return super().ac_update_vrs_effect(telem_data)

    def ac_update_heli_engine_rumble(self, telem_data, blade_ct=None):
        """Update helicopter engine/rotor rumble (delegates to HelicopterEffectsMixIn)."""
        return super().ac_update_heli_engine_rumble(telem_data, blade_ct=blade_ct)

    def ac_collective_force_trim_override(self, telem_data, spring):
        """Collective force-trim override handler (delegates to HelicopterEffectsMixIn)."""
        return super().ac_collective_force_trim_override(telem_data, spring)

    def ac_update_gforce_effect(self, telem_data, adv_spr: bool = False):
        """Delegates to GForceEffectMixIn.ac_update_gforce_effect (stub on AircraftBase)."""
        return super().ac_update_gforce_effect(telem_data, adv_spr=adv_spr)

    def ac_update_hydraulic_loss_effect(self, telem_data):
        """Delegates to HydraulicLossMixIn.ac_update_hydraulic_loss_effect"""
        return super().ac_update_hydraulic_loss_effect(telem_data)

    def ac_modify_game_spring(self):
        """Delegates to AdvancedSpringMixIn.ac_modify_game_spring.
        """
        super().ac_modify_game_spring()

    def __init__(self, name: str, **kwargs):
        super().__init__()

        self._name = name

        self.adv_g_settings_dict: dict = {}
        self.adv_spr_settings_dict: dict = {}


        # hydraulic_factor is initialised in HydraulicLossMixIn.__init__
        # clear any existing effects
        self.effects.clear()

    # spring_adjuster_x/y and spring_adjuster are initialised in AdvancedSpringMixIn.__init__

        self.friction_effect_overridden: bool = False

        self.spring_mode = SpringModeEnum.NONE.name
        self.gforce_effect_mode = GEffectModeEnum.DISABLED.name

    ########################################
    ######                            ######
    ######  Generic Aircraft Effects  ######
    ######                            ######
    ########################################

    def ac_update_touchdown_effect(self, telem_data):
        """Generates a g-based force upon landing or as a result of large bumps"""

        max_force = 0.5
        max_g = 2
        if self.is_collective() or self.is_pedals():
            return
        if self._sim_is("DCS") or self._sim_is("IL2") or self._sim_is('BMS'):
            gs = round(telem_data.get("ACCs")[1] - 1, 2)  # subtract nominal G to align with zero based data from MSFS
        elif self._sim_is("MSFS") or self._sim_is("XPLANE"):
            gs = round(telem_data.get("AccBody")[1], 2)
        else:
            return

        if not self.touchdown_effect_enabled:
            self.effects.dispose("touchdown")
            return
        on_ground = telem_data.get("SimOnGround", 0)
        if not on_ground:
            self.effects.dispose("touchdown")
            return
        force = round(utils.scale_clamp(gs, (0, self.touchdown_effect_max_gs), (0,self.touchdown_effect_max_force)), 2)

        logging.debug(f"Touchdown Effect: Realtime Gs: {gs}, Force:{force}")
        # telem_data["_gs"] = gs
        # telem_data["_force"] = force
        self.effects['touchdown'].constant(force, 180).start()

    def bms_taxi_bumps(self, telem_data):
        super().bms_taxi_bumps(telem_data)

    def ac_update_runway_rumble(self, telem_data):
        super().ac_update_runway_rumble(telem_data)

    # AoA reduction implementation lives in AoAReductionMixIn

    def ac_update_decel_effect(self, telem_data):
        """Delegates to DecelerationEffectMixIn.ac_update_decel_effect"""
        return super().ac_update_decel_effect(telem_data)

    # weapon/CM behavior moved into WeaponsEffectMixIn

    def ac_update_flaps(self, telem_data):
        super().ac_update_flaps(telem_data)

    def ac_update_canopy(self, telem_data):
        """Delegates to MotionEffectsMixIn.ac_update_canopy"""
        return super().ac_update_canopy(telem_data)

    def ac_update_landing_gear(self, telem_data):
        """Delegates to MotionEffectsMixIn.ac_update_landing_gear"""
        return super().ac_update_landing_gear(telem_data)

    def ac_update_speed_brakes(self, telem_data):
        """Delegates to MotionEffectsMixIn.ac_update_speed_brakes"""
        return super().ac_update_speed_brakes(telem_data)

    def ac_update_spoilers(self, telem_data):
        """Delegates to MotionEffectsMixIn.ac_update_spoilers"""
        return super().ac_update_spoilers(telem_data)

    def ac_update_tailhook_effect(self, telem_data):
        """Delegates to MotionEffectsMixIn.ac_update_tailhook_effect"""
        return super().ac_update_tailhook_effect(telem_data)

    def ac_update_fuelboom_effect(self, telem_data):
        """Delegates to MotionEffectsMixIn.ac_update_fuelboom_effect"""
        return super().ac_update_fuelboom_effect(telem_data)

    def ac_update_wingfold_effect(self, telem_data):
        """Delegates to MotionEffectsMixIn.ac_update_wingfold_effect"""
        return super().ac_update_wingfold_effect(telem_data)

    def ac_update_wind_effect(self, telem_data):
        if not self.is_joystick(): return
        if not self.wind_effect_enabled:
            self.effects.dispose("wnd")
            return

        wind = telem_data.get("Wind", (0, 0, 0))
        wnd = math.sqrt(wind[0] ** 2 + wind[1] ** 2 + wind[2] ** 2)

        v = HPFs.get("wnd", 3).update(wnd)
        v = LPFs.get("wnd", 15).update(v)
        v = utils.clamp(v, 0, self.wind_effect_max_intensity)
        v = utils.clamp(v*self.wind_effect_scaling, 0.0,1.0)
        if v == 0:
            self.effects.dispose("wind")
            return
        logging.debug(f"Adding wind effect intensity:{v}")
        self.effects["wnd"].constant(v, utils.RandomDirectionModulator, 5).start()

    def ac_update_ffb_forces(self, telem_data):

        if self.enable_damper_ovd:
            if self.anything_has_changed('damper_value', self.damper_force) or not self.effects['damper'].started:
                force = utils.clamp(self.damper_force, 0.0, 1.0)
                self.effects["damper"].damper(int(4096*force), int(4096*force)).start()
        else:
            if self.effects['damper'].started:
                self.effects["damper"].destroy()

        if self.enable_inertia_ovd:
            if self.anything_has_changed('inertia_value', self.inertia_force) or not self.effects['inertia'].started:
                force = utils.clamp(self.inertia_force, 0.0, 1.0)
                self.effects["inertia"].inertia(int(4096*force), int(4096*force)).start()
        else:
            if self.effects['inertia'].started:
                self.effects["inertia"].destroy()

        if not self.friction_effect_overridden:
            if self.enable_friction_ovd:
                force = utils.clamp(self.friction_force, 0.0, 1.0)
                self.effects['friction'].name = 'friction'
                self.effects["friction"].friction(int(4096*force), int(4096*force)).start()
            else:
                if self.effects['friction'].started:
                    self.effects["friction"].destroy()

    ########################################
    ######                            ######
    ######    Prop Aircraft Effects   ######
    ######                            ######
    ########################################

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


    def ac_update_piston_engine_rumble(self, telem_data):
        """Delegates to EngineRumbleMixIn.ac_update_piston_engine_rumble"""
        return super().ac_update_piston_engine_rumble(telem_data)



    ########################################
    ######                            ######
    ######    Jet Aircraft Effects    ######
    ######                            ######
    ########################################
    def ac_update_ab_effect(self, telem_data):
        super().ac_update_ab_effect(telem_data)

    def ac_update_jet_engine_rumble(self, telem_data):
        super().ac_update_jet_engine_rumble(telem_data)

    def on_event(self, event, *args):
        pass

    def on_timeout(self):  # override me
        logging.info("Telemetry Timeout, stopping effects")
        # effects.foreach(lambda e: e.stop())
        for key, effect in self.effects.dict.items():
            effect: HapticEffect
            if self.keep_forces_on_pause:
                if effect.effect_type in [EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION, EFFECT_SPRING_ADJUSTER]:
                    continue
            effect.stop()
        if self.deadzone_active:
            HapticEffect.device.set_deadzone(0)
            self.deadzone_updated = False
            self.deadzone_active = False

    def on_telemetry(self, telem_data): 
        super().on_telemetry(telem_data)

        fx,fy = HapticEffect.device.get_input().forceXY()
        self.telem_data['ForceXY'] = [fx,fy]
        jx, jy = HapticEffect.device.get_input().axisXY()
        self.telem_data['JoyXY'] = [jx, jy]
        # the methods should decide if they want to run based on the telemetry data
        if self.is_jet_aircraft():
            self.ac_update_ab_effect(telem_data)

        elif self.is_propeller_aircraft():
            self.ac_update_piston_engine_rumble(telem_data)

        self.ac_update_wind_effect(telem_data)
        self.ac_update_jet_engine_rumble(telem_data)


        if self.is_joystick():
            self.ac_override_elevator_droop(telem_data)

        if self.is_pedals():
            if not self._sim_is_msfs() and not self._sim_is_xplane():
                self.ac_override_pedal_spring(telem_data)

        self.ac_update_buffeting(telem_data)
        self.ac_update_cm_weapons(telem_data)

        hyd_loss = self.ac_update_hydraulic_loss_effect(telem_data)
        if not hyd_loss: 
            self.ac_update_ffb_forces(telem_data)

        self.ac_modify_game_spring()
        self.ac_set_deadzone()

        self.ac_update_tailhook_effect(telem_data)
        self.ac_update_fuelboom_effect(telem_data)
        self.ac_update_wingfold_effect(telem_data)
        self.ac_update_touchdown_effect(telem_data)
        self.ac_update_runway_rumble(telem_data)
        self.ac_update_decel_effect(telem_data)

        self.ac_update_speed_brakes(telem_data)
        self.ac_update_landing_gear(telem_data)
        self.ac_update_flaps(telem_data)
        self.ac_update_canopy(telem_data)
        self.ac_update_spoilers(telem_data)

