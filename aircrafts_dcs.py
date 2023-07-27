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
    def __init__(self, name : str, **kwargs):
        super().__init__(name, **kwargs)

        self._engine_rumble_is_playing = 0

        #clear any existing effects
        for e in effects.values(): e.destroy()
        effects.clear()

        self.spring = HapticEffect().spring()
        #self.spring.effect.effect_id = 5
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)

    def on_telemetry(self, telem_data : dict):
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """
        if not "AircraftClass" in telem_data:
            telem_data["AircraftClass"] = "Aircraft"
        self._telem_data = telem_data
        if telem_data.get("N") == None:
            return
        if self.deceleration_effect_enable and self.deceleration_effect_enable_areyoureallysure:
            self._decel_effect(telem_data)
        self._update_buffeting(telem_data)
        self._update_runway_rumble(telem_data)
        self._update_cm_weapons(telem_data)
        if self.speedbrake_motion_intensity > 0 or self.speedbrake_buffet_intensity > 0:
            self._update_speed_brakes(telem_data.get("speedbrakes_value"), telem_data.get("TAS"))
        if self.gear_motion_intensity > 0 or self.gear_buffet_intensity > 0:
            self._update_landing_gear(telem_data.get("gear_value"), telem_data.get("TAS"))
        if self.flaps_motion_intensity > 0:
            self._update_flaps(telem_data.get("Flaps"))
        if self.canopy_motion_intensity > 0:
            self._update_canopy(telem_data.get("Canopy"))
        if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
            self._update_spoiler(telem_data.get("Spoilers"), telem_data.get("TAS"))
        self._update_stick_position(telem_data)


    def on_timeout(self):
        # stop all effects when telemetry stops
        for e in effects.values(): e.stop()

class PropellerAircraft(Aircraft):
    """Generic Class for Prop/WW2 aircraft"""
    engine_rumble : int = 0                         # Engine Rumble - Disabled by default - set to 1 in config file to enable
    
    engine_rumble_intensity : float = 0.02
    engine_rumble_lowrpm = 450
    engine_rumble_lowrpm_intensity: float = 0.12
    engine_rumble_highrpm = 2800
    engine_rumble_highrpm_intensity: float = 0.06
    engine_max_rpm = 2700                           # Assume engine RPM of 2700 at 'EngRPM' = 1.00 for aircraft not exporting 'ActualRPM' in lua script
    max_aoa_cf_force : float = 0.2 # CF force sent to device at %stall_aoa
    rpm_scale : float = 45

    _engine_rumble_is_playing = 0
    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "PropellerAircraft"
        super().on_telemetry(telem_data)

    
        wind = telem_data.get("Wind", (0,0,0))
        wnd = math.sqrt(wind[0]**2 + wind[1]**2 + wind[2]**2)

        v = HPFs.get("wnd", 3).update(wnd)
        v = LPFs.get("wnd", 15).update(v)

        effects["wnd"].constant(v, utils.RandomDirectionModulator, 5).start()

        rpm = telem_data.get("EngRPM", 0)
        if not "ActualRPM" in telem_data:
            if isinstance(rpm, list):
                rpm = [(x / 100) * self.engine_max_rpm for x in rpm]
            else:
                rpm = (rpm / 100) * self.engine_max_rpm
            telem_data["ActualRPM"] = rpm # inject ActualRPM into telemetry

        if self.engine_rumble:
            self._update_engine_rumble(telem_data["ActualRPM"])

        self._update_aoa_effect(telem_data)

    def _update_aoa_effect(self, telem_data):
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)
        if aoa:
            aoa = float(aoa)
            speed_factor = utils.scale_clamp(tas, (50*kmh, 140*kmh), (0, 1.0))
            mag = utils.scale_clamp(abs(aoa), (0, self.stall_aoa), (0, self.max_aoa_cf_force))
            mag *= speed_factor
            if(aoa > 0):
                dir = 0
            else: dir = 180

            telem_data["aoa_pull"] = mag
            effects["aoa"].constant(mag, dir).start()

    def _update_engine_rumble(self, rpm):
        if type(rpm) == list:
            rpm = rpm[0]
            
        frequency = float(rpm) / 60
        
        #frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency + median_modulation
        precision = 2
        r1_modulation = utils.get_random_within_range("rumble_1", median_modulation, median_modulation - modulation_neg, median_modulation + modulation_pos, precision, time_period=5)
        r2_modulation = utils.get_random_within_range("rumble_2", median_modulation, median_modulation - modulation_neg, median_modulation + modulation_pos, precision, time_period=5)
        if frequency > 0 or self._engine_rumble_is_playing:
            dynamic_rumble_intensity = self._calc_engine_intensity(rpm)
            # logging.debug(f"Current Engine Rumble Intensity = {dynamic_rumble_intensity}")


            effects["rpm0-1"].periodic(frequency, dynamic_rumble_intensity, 0).start() # vib on X axis
            effects["rpm0-2"].periodic(frequency+r1_modulation, dynamic_rumble_intensity, 0).start() # vib on X axis
            effects["rpm1-1"].periodic(frequency2, dynamic_rumble_intensity, 90).start() # vib on Y axis
            effects["rpm1-2"].periodic(frequency2+r2_modulation, dynamic_rumble_intensity, 90).start() # vib on Y axis
            self._engine_rumble_is_playing = 1
        else:
            self._engine_rumble_is_playing = 0
            effects.dispose("rpm0-1")
            effects.dispose("rpm0-2")
            effects.dispose("rpm1-1")
            effects.dispose("rpm1-2")

    def _calc_engine_intensity(self, rpm) -> float:
        """
        Calculate the intensity to use based on the configurable high and low intensity settings and high and low RPM settings
        intensity will decrease from max to min settings as the RPM increases from min to max settings
        lower RPM = more rumble effect
        """
        min_rpm = self.engine_rumble_lowrpm
        max_rpm = self.engine_rumble_highrpm
        max_intensity = self.engine_rumble_lowrpm_intensity
        min_intensity = self.engine_rumble_highrpm_intensity
        
        rpm_percentage = 1 - ((rpm - min_rpm) / (max_rpm - min_rpm))
        # logging.debug(f"rpm percent: {rpm_percentage}")
        interpolated_intensity = min_intensity + (max_intensity - min_intensity) * rpm_percentage
        
        return interpolated_intensity


class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    #flaps_motion_intensity = 0.0

    _ab_is_playing = 0
    _jet_rumble_is_playing = 0
    engine_rumble = 0

    def _update_ab_effect(self, intensity, telem_data):
        frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency+median_modulation
        precision = 2
        try:
            afterburner_pos = max(telem_data.get("Afterburner")[0], telem_data.get("Afterburner")[1])
        except Exception as e:
            logging.error(f"Error getting afterburner position, sim probably disconnected, bailing: {e}")
            return
        #logging.debug(f"Afterburner = {afterburner_pos}")
        r1_modulation = utils.get_random_within_range("rumble_1", median_modulation, median_modulation-modulation_neg, median_modulation+modulation_pos, precision, time_period=5  )
        r2_modulation = utils.get_random_within_range("rumble_2", median_modulation, median_modulation-modulation_neg, median_modulation+modulation_pos, precision, time_period=5  )
        #try:
        #print(r1_modulation)
        if afterburner_pos and (self.has_changed("Afterburner") or self.anything_has_changed("Modulation", r1_modulation)):
            #logging.debug(f"AB Effect Updated: LT={Left_Throttle}, RT={Right_Throttle}")
            intensity = self.afterburner_effect_intensity * afterburner_pos
            effects["ab_rumble_1_1"].periodic(frequency, intensity, 0,).start()
            effects["ab_rumble_1_2"].periodic(frequency + r1_modulation, intensity, 0).start()
            effects["ab_rumble_2_1"].periodic(frequency2, intensity, 45, 4, phase=120, offset=60).start()
            effects["ab_rumble_2_2"].periodic(frequency2 + r2_modulation, intensity, 45, 4, phase=120, offset=60).start()
            # logging.debug(f"AB-Modul1= {r1_modulation} | AB-Modul2 = {r2_modulation}")
            self._ab_is_playing = 1
        elif afterburner_pos == 0:
            #logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            effects.dispose("ab_rumble_1_1")
            effects.dispose("ab_rumble_1_2")
            effects.dispose("ab_rumble_2_1")
            effects.dispose("ab_rumble_2_2")
            self._ab_is_playing = 0
        #except:
        #    logging.error("Error playing Afterburner effect")

    def _update_jet_engine_rumble(self, telem_data):
        super().on_telemetry(telem_data)
        frequency = self.jet_engine_rumble_freq
        median_modulation = 10
        modulation_pos = 3
        modulation_neg = 3
        frequency2 = frequency + median_modulation
        precision = 2
        effect_index = 4
        phase_offset = 120
        try:
            jet_eng_rpm = max(telem_data.get("EngRPM")[0], telem_data.get("EngRPM")[1])
        except Exception as e:
            logging.error(f"Error getting Engine RPM, sim probably disconnected, bailing: {e}")
            return
        # logging.debug(f"Afterburner = {afterburner_pos}")
        r1_modulation = utils.get_random_within_range("jetengine_1", median_modulation, median_modulation - modulation_neg, median_modulation + modulation_pos, precision, time_period=1)
        r2_modulation = utils.get_random_within_range("jetengine_2", median_modulation, median_modulation - modulation_neg, median_modulation + modulation_pos, precision, time_period=2)
       # r1_modulation = round(r1_modulation,4)
       # r2_modulation = round(r2_modulation,4)
        # try:
        # print(r1_modulation)
        if self.engine_rumble and (self.has_changed("EngRPM") or self.anything_has_changed("JetEngineModul", r1_modulation)):
            # logging.debug(f"AB Effect Updated: LT={Left_Throttle}, RT={Right_Throttle}")
            intensity = self.jet_engine_rumble_intensity * (jet_eng_rpm / 100)
            rt_freq = round(frequency + (10 * (jet_eng_rpm / 100)),4)
            rt_freq2 = round(rt_freq + median_modulation, 4)
            effects["je_rumble_1_1"].periodic(rt_freq, intensity,0, effect_index).start()
            effects["je_rumble_1_2"].periodic(rt_freq + r1_modulation, intensity,0, effect_index).start()
            effects["je_rumble_2_1"].periodic(rt_freq2, intensity, 90, effect_index, phase=phase_offset).start()
            effects["je_rumble_2_2"].periodic(rt_freq2 + r2_modulation, intensity, 90, effect_index, phase=phase_offset).start()
            # logging.debug(f"RPM={jet_eng_rpm}")
            # logging.debug(f"Intensty={intensity}")
            # logging.debug(f"JE-M1={r1_modulation}, F1-1={rt_freq}, F1-2={round(rt_freq + r1_modulation,4)} | JE-M2 = {r2_modulation}, F2-1={rt_freq2}, F2-2={round(rt_freq2 + r2_modulation, 4)} ")
            self._jet_rumble_is_playing = 1
        elif jet_eng_rpm == 0:
            # logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            effects.dispose("je_rumble_1_1")
            effects.dispose("je_rumble_1_2")
            effects.dispose("je_rumble_2_1")
            effects.dispose("je_rumble_2_2")
            self._jet_rumble_is_playing = 0
        # except:
        #    logging.error("Error playing Afterburner effect")


    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        if telem_data.get("N")== None:
            return
        telem_data["AircraftClass"] = "JetAircraft"
        super().on_telemetry(telem_data)

        if self.afterburner_effect_intensity > 0:
            self._update_ab_effect(self.afterburner_effect_intensity, telem_data)
        if Aircraft.jet_engine_rumble_intensity > 0:
            self._update_jet_engine_rumble(telem_data)


#####
# No longer required now that we have afterburner telemetry - keep as placeholder
#####
# class FA18(JetAircraft):
#     def on_telemetry(self, telem_data):
#         super().on_telemetry(telem_data)
#         # try:
#         #     eng1 = telem_data.get("Engine_RPM")[0]
#         # except:
#         #     logging.error("Error getting engine RPM data")
#         #     eng1 = 0
#         #
#         # try:
#         #     eng2 = telem_data.get("Engine_RPM")[1]
#         # except:
#         #     logging.error("Error getting engine RPM data")
#         #     eng2 = 0
#         # logging.debug(f"F18 - Eng1={eng1}, Eng2={eng2}")
#         # if eng1 > 0 or eng2 > 0:
#         #     if self.afterburner_effect_intensity > 0:
#         #         super()._update_ab_effect(self.afterburner_effect_intensity, telem_data, 0.8, 1.0, self._telem_data.get("Throttle_1", 0), self._telem_data.get("Throttle_2", 0))

#####
# No longer required now that we have afterburner telemetry - keep as placeholder
#####
# class F16(JetAircraft):
#     def on_telemetry(self, telem_data):
#         super().on_telemetry(telem_data)
#         # try:
#         #     eng1 = telem_data.get("Engine_RPM")[0]
#         # except:
#         #     logging.error("Error getting engine RPM data")
#         #     eng1 = 0
#         # logging.debug(f"F16 - Eng1={eng1}")
#         # if eng1 > 0:
#         #     if self.afterburner_effect_intensity > 0:
#         #         super()._update_ab_effect(self.afterburner_effect_intensity, telem_data, 0.7, 1.0, self._telem_data.get("Throttle_1", 0))

#####
# No longer required now that we have afterburner telemetry - keep as placeholder
#####
# class SU33(JetAircraft):
#     def on_telemetry(self, telem_data):
#         super().on_telemetry(telem_data)
#         # try:
#         #     eng1 = telem_data.get("Engine_RPM")[0]
#         #     eng2 = telem_data.get("Engine_RPM")[1]
#         # except:
#         #     logging.error("Error getting engine RPM data")
#         # logging.debug(f"F18 - Eng1={eng1}, Eng2={eng2}")
#         # if eng1 > 0 or eng2 > 0:
#         #     if self.afterburner_effect_intensity > 0:
#         #         super()._update_ab_effect(self.afterburner_effect_intensity, telem_data, 0.8, 1.0, self._telem_data.get("Throttle_1", 0), self._telem_data.get("Throttle_2", 0))
class Helicopter(Aircraft):
    """Generic Class for Helicopters"""
    buffeting_intensity = 0.0

    etl_start_speed = 6.0 # m/s
    etl_stop_speed = 22.0 # m/s
    etl_effect_intensity = 0.2 # [ 0.0 .. 1.0]
    etl_shake_frequency = 14.0 # value has been deprecated in favor of rotor RPM calculation
    overspeed_shake_start = 70.0 # m/s
    overspeed_shake_intensity = 0.2
    heli_engine_rumble_intensity = 0.12

    def _calc_etl_effect(self, telem_data):
        blade_ct = 2
      #  rotor = 245
        mod = telem_data.get("N")
        tas = telem_data.get("TAS", 0)
        WoW = sum(telem_data.get("WeightOnWheels"))
        if mod == "UH-60L":
            # UH60 always shows positive value for tailwheel
            WoW = telem_data.get("WeightOnWheels")[0] + telem_data.get("WeightOnWheels")[2]
        rotor = telem_data.get("RotorRPM")
        if WoW > 0:
            # logging.debug("On the Ground, moving forward. Probably on a Ship! - Dont play effect!")
            return
        if "UH=1H" in mod:
            blade_ct = 2
        elif "KA-50" in mod:
            blade_ct = 3
        elif "Mi-8MT" in mod:
            blade_ct = 5
        elif "Mi-24P" in mod:
            blade_ct = 5
        elif "AH-64" in mod:
            blade_ct = 2
            rotor = 245 # Apache does not have exportable data related to Rotor RPM
        elif "UH-60L" in mod:
            blade_ct = 4
        elif "SA342" in mod:
            blade_ct = 3
        else:
            blade_ct = 2
            rotor = 250
       # logging.debug(f"rotor is now {rotor}")

        if rotor:
            self.etl_shake_frequency = (rotor/60) * blade_ct

        etl_mid = (self.etl_start_speed + self.etl_stop_speed)/2.0

        if tas >= self.etl_start_speed and tas <= self.etl_stop_speed:
            shake = self.etl_effect_intensity * utils.gaussian_scaling(tas, self.etl_start_speed, self.etl_stop_speed, peak_percentage=0.5, curve_width=0.55)
           # logging.debug(f"Gaussian Scaling calc = {shake}")
           #  logging.debug(f"Playing ETL shake (freq = {self.etl_shake_frequency}, intens= {shake})")

        elif tas >= self.overspeed_shake_start:
            shake = self.overspeed_shake_intensity * utils.non_linear_scaling(tas, self.overspeed_shake_start, self.overspeed_shake_start+15, curvature=.7)
            #shake = utils.scale_clamp(tas, (self.overspeed_shake_start, self.overspeed_shake_start+20), (0, self.overspeed_shake_intensity))
            # logging.debug(f"Overspeed shake (freq = {self.etl_shake_frequency}, intens= {shake}) ")
        else:
            shake = 0

        #telem_data["dbg_shake"] = shake

        if shake:
            effects["etlY"].periodic(self.etl_shake_frequency, shake, 0).start()
            effects["etlX"].periodic(self.etl_shake_frequency+4, shake, 90).start()
            #effects["etlY"].periodic(12, shake, 0).start()
        else:
            effects["etlX"].stop()
            effects["etlY"].stop()
            #effects["etlY"].stop()

    def _update_heli_engine_rumble(self, telem_data):
        rrpm = telem_data.get("RotorRPM")
        blade_ct = 2
        mod = telem_data.get("N")
        tas = telem_data.get("TAS", 0)
        #rotor = telem_data.get("RotorRPM")
        if "UH=1H" in mod:
            blade_ct = 2
        elif "KA-50" in mod:
            blade_ct = 2
        elif "Mi-8MT" in mod:
            blade_ct = 5
        elif "Mi-24P" in mod:
            blade_ct = 5
        elif "AH-64" in mod:
            blade_ct = 2
            rrpm = 245
        elif "UH-60L" in mod:
            blade_ct = 4
        elif "SA342" in mod:
            blade_ct = 2
        else:
            blade_ct = 2
            rrpm = 250

        frequency = float(rrpm) / 45 * blade_ct

        # frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency + median_modulation
        precision = 2
        r1_modulation = utils.get_random_within_range("rumble_1", median_modulation, median_modulation - modulation_neg,
                                                      median_modulation + modulation_pos, precision, time_period=3)
        r2_modulation = utils.get_random_within_range("rumble_2", median_modulation, median_modulation - modulation_neg,
                                                      median_modulation + modulation_pos, precision, time_period=5)
        if frequency > 0 or self._engine_rumble_is_playing:
            #rumble_intensity = 0.12
            # logging.debug(f"Current Engine Rumble Intensity = {self.heli_engine_rumble_intensity}")

            effects["rpm0-1"].periodic(frequency, self.heli_engine_rumble_intensity*.5, 0).start()  # vib on X axis
            #effects["rpm0-2"].periodic(frequency + r1_modulation, dynamic_rumble_intensity, 0).start()  # vib on X axis
            effects["rpm1-1"].periodic(frequency2, self.heli_engine_rumble_intensity*.5, 90).start()  # vib on Y axis
            #effects["rpm1-2"].periodic(frequency2 + r2_modulation, dynamic_rumble_intensit, 90).start()  # vib on Y axis
            self._engine_rumble_is_playing = 1
        else:
            self._engine_rumble_is_playing = 0
            effects.dispose("rpm0-1")
            #effects.dispose("rpm0-2")
            effects.dispose("rpm1-1")
            #effects.dispose("rpm1-2")

    def on_telemetry(self, telem_data):
        self.speedbrake_motion_intensity = 0.0
        # logging.debug(f"Speedbrake === {Helicopter.speedbrake_motion_intensity}")
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "Helicopter"
        super().on_telemetry(telem_data)

        self._calc_etl_effect(telem_data)
        self._update_heli_engine_rumble(telem_data)


class TF51D(PropellerAircraft):
    buffeting_intensity = 0 # implement
    runway_rumble_intensity = 1.0
    

# Specialized class for Mig-21
class Mig21(JetAircraft):
    aoa_shaker_enable = True
    buffet_aoa = 8

class Ka50(Helicopter):
    #TODO: KA-50 settings here...
    pass


classes = {
    "Ka-50" : Ka50,
    "Mi-8MT": Helicopter,
    "UH-1H": Helicopter,
    "SA342M" :Helicopter,
    "SA342L" :Helicopter,
    "SA342Mistral":Helicopter,
    "SA342Minigun":Helicopter,
    "AH-64D_BLK_II":Helicopter,

    "TF-51D" : TF51D,
    "MiG-21Bis": Mig21,
    "F-15C": JetAircraft,
    "MiG-29A": JetAircraft,
    "MiG-29S": JetAircraft,
    "MiG-29G": JetAircraft,
    "default": Aircraft
}
