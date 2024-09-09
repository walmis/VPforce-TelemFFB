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
from telemffb.hw.ffb_rhino import HapticEffect, FFBReport_SetCondition
import telemffb.utils as utils
import logging
import random
from .aircraft_base import AircraftBase, effects
import json
import socket

#unit conversions (to m/s)
knots = 0.514444
kmh = 1.0/3.6
deg = math.pi/180
EFFECT_SQUARE = 3
EFFECT_SINE = 4
EFFECT_TRIANGLE = 5
EFFECT_SAWTOOTHUP = 6
EFFECT_SAWTOOTHDOWN = 7
# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
# effects : Dict[str, HapticEffect] = utils.Dispenser(HapticEffect)

# Highpass filter dispenser
HPFs : Dict[str, utils.HighPassFilter]  = utils.Dispenser(utils.HighPassFilter)

# Lowpass filter dispenser
LPFs : Dict[str, utils.LowPassFilter] = utils.Dispenser(utils.LowPassFilter)

dbg_en = 0
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


    runway_rumble_intensity : float = 1.0           # peak runway intensity, 0 to disable
    runway_rumble_enabled: bool = False
    il2_runway_rumble_intensity : float = 1.0           # peak runway intensity, 0 to disable

    gun_vibration_intensity : float = 0.12          # peak gunfire vibration intensity, 0 to disable
    cm_vibration_intensity : float = 0.12           # peak countermeasure release vibration intensity, 0 to disable
    weapon_release_intensity : float = 0.12         # peak weapon release vibration intensity, 0 to disable
    il2_weapon_release_intensity : float = 0.12         # peak weapon release vibration intensity, 0 to disable
    weapon_effect_direction: int = 45               # Affects the direction of force applied for gun/cm/weapon release effect, Set to -1 for random direction
    
    speedbrake_motion_intensity : float = 0.12      # peak vibration intensity when speed brake is moving, 0 to disable
    speedbrake_buffet_intensity : float = 0.15      # peak buffeting intensity when speed brake deployed,  0 to disable

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable

    gear_motion_effect_enabled: bool = True
    gear_motion_intensity : float = 0.12      # peak vibration intensity when gear is moving, 0 to disable

    flaps_motion_intensity : float = 0.12      # peak vibration intensity when flaps are moving, 0 to disable

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

    gun_is_firing = 0
    damage_effect_enabled: bool = False
    damage_effect_intensity: float = 0
    il2_shake_master = 0
    il2_enable_weapons = 0
    il2_enable_runway_rumble = 0  # not yet implemented
    il2_enable_buffet = 0  # not yet impelemnted
    il2_buffeting_factor: float  = 1.0
    stop_state = False

    def __init__(self, name : str, **kwargs):
        super().__init__(name, **kwargs)
        self.gun_is_firing = 0
        #clear any existing effects
        self.spring = effects["spring"].spring()
        # self.damper = effects["damper"].damper()
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
        for e in effects.values(): e.destroy()
        effects.clear()
        self._focus_last_value = 1

        # self.spring = HapticEffect().spring()
        # self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        # self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
    def _update_cm_weapons(self, telem):
        ## IL2 does not deliver telemetry in the same way as DCS.  As a result, modified/different effects logic is required
        canon_rof = 600
        canon_hz = canon_rof/60
        bombs = telem.get("Bombs")
        gun = telem.get("Gun")
        rockets = telem.get("Rockets")
        direction = 90 if self.is_pedals() else 0
        if self.anything_has_changed("Bombs", bombs):
            effects["bombs"].periodic(10, self.il2_weapon_release_intensity, direction,effect_type=EFFECT_SAWTOOTHUP, duration=80).start(force=True)
        elif not self.anything_has_changed("Bombs", bombs, delta_ms=160):
            effects["bombs"].stop()

        if self.anything_has_changed("Gun", gun) and not self.gun_is_firing:
            effects["gunfire"].periodic(canon_hz, self.il2_weapon_release_intensity, direction, effect_type=EFFECT_SQUARE).start(force=True)
            self.gun_is_firing = 1
            logging.debug(f"Gunfire={self.il2_weapon_release_intensity}")
        elif not self.anything_has_changed("Gun", gun, delta_ms=100):
            # effects["gunfire"].stop()
            effects.dispose("gunfire")
            self.gun_is_firing = 0

        if self.anything_has_changed("Rockets", rockets):
            effects["rockets"].periodic(50, self.il2_weapon_release_intensity, direction, effect_type=EFFECT_SQUARE, duration=80).start(force=True)
        if not self.anything_has_changed("Rockets", rockets, delta_ms=160):
            effects["rockets"].stop()
    def _update_runway_rumble(self, telem_data):
        if telem_data.get("TAS") > 1.0 and telem_data.get("AGL") < 10.0 and utils.average(telem_data.get("GearPos")) == 1:
            self.runway_rumble_intensity = self.il2_runway_rumble_intensity
            super()._update_runway_rumble(telem_data)
        else:
            self.runway_rumble_intensity = 0
            effects.dispose("runway0")
            effects.dispose("runway1")
    def _update_buffeting(self, telem_data: dict):
        direction = 90 if self.is_pedals() else 0
        freq = telem_data.get("BuffetFrequency", 0)
        amp = utils.clamp(telem_data.get("BuffetAmplitude", 0) * self.il2_buffeting_factor, 0.0, 1.0)
        amp2 = utils.clamp(amp * 1.4, 0, 1)
        if amp:
            effects["il2_buffet"].periodic(freq, amp, direction, effect_type=EFFECT_SINE).start()
            effects["il2_buffet2"].periodic(freq * 1.5, amp2, direction + 180, effect_type=EFFECT_SINE, phase=90).start()

        else:
            effects.dispose("il2_buffet")
            effects.dispose("il2_buffet2")
    def _update_damage(self, telem_data):
        if not self.damage_effect_enabled or not self.damage_effect_intensity:
            effects.dispose("hit")
            effects.dispose("damage")
            return

        hit = telem_data.get("Hits")
        damage = telem_data.get("Damage")
        hit_freq = 5
        hit_amp = utils.clamp(self.damage_effect_intensity, 0.0, 1.0)
        damage_freq = 10
        damage_amp = utils.clamp(self.damage_effect_intensity, 0.0, 1.0)

        if self.anything_has_changed("hit", hit):
            effects["hit"].periodic(hit_freq, hit_amp, utils.RandomDirectionModulator,effect_type=EFFECT_SQUARE, duration=30).start()
        elif not self.anything_has_changed("hit", hit, delta_ms=120):
            effects.dispose("hit")
        if self.anything_has_changed("damage", damage):
            effects["damage"].periodic(damage_freq, damage_amp, utils.RandomDirectionModulator, effect_type=EFFECT_SQUARE, duration=30).start()
        elif not self.anything_has_changed("damage", damage, delta_ms=120):
            effects.dispose("damage")
    def _update_focus_loss(self, telem_data):
        focus = telem_data.get("Focus")
        if focus != self._focus_last_value:
            logging.info("IL-2 Window focus changed, resetting effects")
            effects.clear()
            self._focus_last_value = focus
        else:
            self._focus_last_value = focus

    def on_telemetry(self, telem_data : dict):
        ## Generic Aircraft Telemetry Handler
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """
        if telem_data.get('SimPaused', False):
            if not self.stop_state:
                self.on_timeout()
            self.stop_state = True
            return
        self.stop_state = False

        if telem_data["AircraftClass"] == "unknown":
            telem_data["AircraftClass"] = "GenericAircraft"#inject aircraft class into telemetry
        self._telem_data = telem_data

        if telem_data.get("N") == None:
            return

        if not telem_data.get("Focus",0):
            self.on_timeout()
            return

        # self._update_focus_loss(telem_data)
        if self.deceleration_effect_enable:
            self._decel_effect(telem_data)
        if self.damage_effect_intensity > 0:
            self._update_damage(telem_data)
        if self.il2_shake_master:
            if self.il2_enable_buffet:
                self._update_buffeting(telem_data)
            if self.il2_enable_runway_rumble:
                self._update_runway_rumble(telem_data)
            if self.il2_enable_weapons:
                self._update_cm_weapons(telem_data)
        if self.speedbrake_motion_intensity > 0 or self.speedbrake_buffet_intensity > 0:
            self._update_speed_brakes(telem_data.get("Speedbrakes"), telem_data.get("TAS"))
        if self.gear_motion_intensity > 0:
            self._update_landing_gear(telem_data.get("GearPos"), 0)
        if self.flaps_motion_intensity > 0:
            self._update_flaps(telem_data.get("Flaps"))
        if self.is_pedals():
            self._override_pedal_spring(telem_data)

        # if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
        #     self._update_spoiler(telem_data.get("Spoilers"), telem_data.get("TAS"))



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


    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        ## Propeller Aircraft Telemetry Handler
        if telem_data.get('SimPaused', False):
            if not self.stop_state:
                self.on_timeout()
            self.stop_state = True
            return
        self.stop_state = False

        if telem_data.get("N") == None:
            return

        if not telem_data.get("Focus",0):
            self.on_timeout()
            return

        telem_data["AircraftClass"] = "PropellerAircraft"   #inject aircraft class into telemetry

        super().on_telemetry(telem_data)

        self.update_piston_engine_rumble(telem_data)
        if self.is_joystick():
            self.override_elevator_droop(telem_data)
            self._gforce_effect(telem_data)



class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    #flaps_motion_intensity = 0.0

    _ab_is_playing = 0

      # run on every telemetry frame
    def on_telemetry(self, telem_data):
        ## Jet Aircraft Telemetry Handler
        if telem_data.get('SimPaused', False):
            if not self.stop_state:
                self.on_timeout()
            self.stop_state = True
            return
        self.stop_state = False

        if telem_data.get("N")== None:
            return

        if not telem_data.get("Focus",0):
            self.on_timeout()
            return

        telem_data["AircraftClass"] = "JetAircraft"   #inject aircraft class into telemetry
        super().on_telemetry(telem_data)

        self._update_jet_engine_rumble(telem_data)

        if self.is_joystick():
            self._gforce_effect(telem_data)



