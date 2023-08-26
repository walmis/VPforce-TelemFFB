# 
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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
from random import randint
import time
from typing import List, Dict
from ffb_rhino import HapticEffect, FFBReport_SetCondition
import utils
import logging
import random
from aircraft_base import AircraftBase
import json
import socket
#Fixme: Weird things happen when IL2 loses window focus.  It stops all effects (including native effects)
#       TelemFFB effects do not restart upon IL2 re-gaining focus (TelemFFB doesn't see a timeout event)
#unit conversions (to m/s)
knots = 0.514444
kmh = 1.0/3.6
deg = math.pi/180

# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
effects : Dict[str, HapticEffect] = utils.Dispenser(HapticEffect)

# Highpass filter dispenser
HPFs : Dict[str, utils.HighPassFilter]  = utils.Dispenser(utils.HighPassFilter)

# Lowpass filter dispenser
LPFs : Dict[str, utils.LowPassFilter] = utils.Dispenser(utils.LowPassFilter)

dbg_en = 1
dbg_lvl = 2
def dbg(level, *args, **kwargs):
    if dbg_en and level >= dbg_lvl:
        print(*args, **kwargs)

class Aircraft(AircraftBase):
    """Base class for Aircraft based FFB"""
    ####
    #### Beta effects - set to 1 to enable
    deceleration_effect_enable = 0
    deceleration_effect_enable_areyoureallysure = 0
    deceleration_max_force = 0.5
    ###
    buffeting_intensity : float = 0.2               # peak AoA buffeting intensity  0 to disable
    buffet_aoa : float          = 10.0              # AoA when buffeting starts
    stall_aoa : float           = 15.0              # Stall AoA
    wind_effect_enabled : int = 0

    engine_rumble : int = 0                         # Engine Rumble - Disabled by default - set to 1 in config file to enable
    
    runway_rumble_intensity : float = 1.0           # peak runway intensity, 0 to disable

    gun_vibration_intensity : float = 0.12          # peak gunfire vibration intensity, 0 to disable
    cm_vibration_intensity : float = 0.12           # peak countermeasure release vibration intensity, 0 to disable
    weapon_release_intensity : float = 0.12         # peak weapon release vibration intensity, 0 to disable
    weapon_effect_direction: int = 45               # Affects the direction of force applied for gun/cm/weapon release effect, Set to -1 for random direction
    
    speedbrake_motion_intensity : float = 0.12      # peak vibration intensity when speed brake is moving, 0 to disable
    speedbrake_buffet_intensity : float = 0.15      # peak buffeting intensity when speed brake deployed,  0 to disable

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable
    
    gear_motion_intensity : float = 0.12      # peak vibration intensity when gear is moving, 0 to disable
    gear_buffet_intensity : float = 0.15      # peak buffeting intensity when gear down during flight,  0 to disable
    
    flaps_motion_intensity : float = 0.12      # peak vibration intensity when flaps are moving, 0 to disable
    flaps_buffet_intensity : float = 0.0      # peak buffeting intensity when flaps are deployed,  0 to disable
    
    canopy_motion_intensity : float = 0.12      # peak vibration intensity when canopy is moving, 0 to disable
    canopy_buffet_intensity : float = 0.0      # peak buffeting intensity when canopy is open during flight,  0 to disable

    afterburner_effect_intensity = 0.2      # peak intensity for afterburner rumble effect
    jet_engine_rumble_intensity = 0.12      # peak intensity for jet engine rumble effect
    jet_engine_rumble_freq = 45             # base frequency for jet engine rumble effect (Hz)

    ####
    #### Beta effects - set to 1 to enable
    gforce_effect_invert_force = 0  # case where "180" degrees does not equal "away from pilot"
    gforce_effect_enable = 0
    gforce_effect_enable_areyoureallysure = 0
    gforce_effect_curvature = 2.2
    gforce_effect_max_intensity = 1.0
    gforce_min_gs = 1.5  # G's where the effect starts playing
    gforce_max_gs = 5.0  # G limit where the effect maxes out at strength defined in gforce_effect_max_intensity

    ###
    ### AoA reduction force effect
    ###
    aoa_reduction_effect_enabled = 0
    aoa_reduction_max_force = 0.0
    critical_aoa_start = 22
    critical_aoa_max = 25

    trim_workaround = False
    gun_is_firing = 0

    il2_enable_weapons = 0
    il2_enable_runway = 0
    il2_enable_aoa = 0

    ####
    ####
    def __init__(self, name : str, **kwargs):
        super().__init__(name, **kwargs)

        #clear any existing effects
        for e in effects.values(): e.destroy()
        effects.clear()

        # self.spring = HapticEffect().spring()
        # self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        # self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
    def _update_cm_weapons(self, telem):
        canon_rof = 600
        canon_hz = canon_rof/60
        ## IL2 does not deliver telemetry in the same way as DCS.  As a result, modified/different effects logic is required
        bombs = telem.get("Bombs")
        gun = telem.get("Gun")
        rockets = telem.get("Rockets")
        #Fixme - Figure out why effects created at the effect module level do not show up in the effect monitor
        if self.anything_has_changed("Bombs", bombs):
            effects["bombs"].periodic(10, self.weapon_release_intensity, 0,effect_type=6, duration=80).start(force=True)
        elif not self.anything_has_changed("Bombs", bombs, delta_ms=160):
            effects["bombs"].stop()

        if self.anything_has_changed("Gun", gun) and not self.gun_is_firing:
            effects["gunfire"].periodic(canon_hz, self.gun_vibration_intensity, 0).start()
            self.gun_is_firing = 1
            logging.info(f"Gunfire={self.weapon_release_intensity}")
        elif not self.anything_has_changed("Gun", gun, delta_ms=100):
            effects["gunfire"].stop()
            self.gun_is_firing = 0

        if self.anything_has_changed("Rockets", rockets):
            effects["rockets"].periodic(50, self.cm_vibration_intensity, 0, effect_type=3, duration=80).start(force=True)
        if not self.anything_has_changed("Rockets", rockets, delta_ms=160):
            effects["rockets"].stop()

    def on_telemetry(self, telem_data : dict):
        ## Generic Aircraft Telemetry Handler
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """

        if telem_data["AircraftClass"] == "unknown":
            telem_data["AircraftClass"] = "GenericAircraft"#inject aircraft class into telemetry
        self._telem_data = telem_data
        if telem_data.get("N") == None:
            return
        # if self.deceleration_effect_enable and self.deceleration_effect_enable_areyoureallysure:
        #     self._decel_effect(telem_data)
        # self._update_buffeting(telem_data)
        # self._update_runway_rumble(telem_data)
        if self.il2_enable_weapons:
            self._update_cm_weapons(telem_data)
        # if self.speedbrake_motion_intensity > 0 or self.speedbrake_buffet_intensity > 0:
        #     self._update_speed_brakes(telem_data.get("speedbrakes_value"), telem_data.get("TAS"))
        # if self.gear_motion_intensity > 0 or self.gear_buffet_intensity > 0:
        #     self._update_landing_gear(telem_data.get("gear_value"), telem_data.get("TAS"))
        # if self.flaps_motion_intensity > 0:
        #     self._update_flaps(telem_data.get("Flaps"))
        # if self.canopy_motion_intensity > 0:
        #     self._update_canopy(telem_data.get("Canopy"))
        # if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
        #     self._update_spoiler(telem_data.get("Spoilers"), telem_data.get("TAS"))
        #
        # if self.is_joystick():
        #     self._update_stick_position(telem_data)


    def on_event(self, event, *args):
        logging.info(f"on_event: {event}")
        if event == "Stop":
            effects.clear()

    def on_timeout(self):
        super().on_timeout()

                   

class PropellerAircraft(Aircraft):
    """Generic Class for Prop/WW2 aircraft"""

    engine_max_rpm = 2700                           # Assume engine RPM of 2700 at 'EngRPM' = 1.00 for aircraft not exporting 'ActualRPM' in lua script
    max_aoa_cf_force : float = 0.2 # CF force sent to device at %stall_aoa
    rpm_scale : float = 45

    _engine_rumble_is_playing = 0

    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        ## Propeller Aircraft Telemetry Handler
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "PropellerAircraft"   #inject aircraft class into telemetry

        super().on_telemetry(telem_data)

        if self.engine_rumble or self._engine_rumble_is_playing: # if _engine_rumble_is_playing is true, check if we need to stop it
            self._update_engine_rumble(telem_data.get("RPM", 0.0))
        # if self.wind_effect_enabled:
        #     self._update_wind_effect(telem_data)
        # self._update_aoa_effect(telem_data)
        if self.gforce_effect_enable:
            self._gforce_effect(telem_data)



class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    #flaps_motion_intensity = 0.0

    _ab_is_playing = 0
    _jet_rumble_is_playing = 0

    # def _update_jet_engine_rumble(self, telem_data):
    #     rpm = telem_data.get("RPM")
    #     max_rpm = telem_data.get("MaxRPM")
    #     if isinstance(rpm, list):
    #         rpm = max(rpm)
    #     if isinstance(max_rpm, list):
    #         max_rpm = max(max_rpm)
    #     if not rpm > 0.0:
    #         return
    #     rpm_pct = (rpm / max_rpm) * 100
    #     print(f"JET RUMBLE pct={rpm_pct}")
    #
    #     eng_rpm = {"EngRPM": [rpm_pct, 0]}
    #     super()._update_jet_engine_rumble(eng_rpm)


    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        ## Jet Aircraft Telemetry Handler
        if telem_data.get("N")== None:
            return
        telem_data["AircraftClass"] = "JetAircraft"   #inject aircraft class into telemetry
        super().on_telemetry(telem_data)

        # if self.afterburner_effect_intensity > 0:
        #     self._update_ab_effect(telem_data)
        if Aircraft.jet_engine_rumble_intensity > 0:
            self._update_jet_engine_rumble(telem_data)
        # if self.aoa_reduction_effect_enabled:
        #     self._aoa_reduction_force_effect(telem_data)
        # if self.gforce_effect_enable and self.gforce_effect_enable_areyoureallysure:
        #     super()._gforce_effect(telem_data)



