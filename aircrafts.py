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
from typing import List, Dict
from ffb_rhino import HapticEffect

import utils
import logging
import random
# from PyQt5.QtWidgets import QPushButton
#
# from effect_settings import periodic_effect_index
periodic_effect_index = 4
logging.debug(f"Read HapticEffectIndex from ffb_rhino")

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

class Aircraft(object):
    """Base class for Aircraft based FFB"""
    ####
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
    
    gear_motion_intensity : float = 0.12      # peak vibration intensity when gear is moving, 0 to disable
    gear_buffet_intensity : float = 0.15      # peak buffeting intensity when gear down during flight,  0 to disable
    
    flaps_motion_intensity : float = 0.12      # peak vibration intensity when flaps are moving, 0 to disable
    flaps_buffet_intensity : float = 0.0      # peak buffeting intensity when flaps are deployed,  0 to disable
    
    canopy_motion_intensity : float = 0.12      # peak vibration intensity when canopy is moving, 0 to disable
    canopy_buffet_intensity : float = 0.0      # peak buffeting intensity when canopy is open during flight,  0 to disable

    afterburner_effect_intensity = 0.2      # peak intensity for afterburner rumble effect

    ####
    _engine_rumble_is_playing = 0
    def __init__(self, name : str, **kwargs):
        self._name = name
        self._changes = {}
        self._change_counter = {}
        self._telem_data = None

        #self.__dict__.update(kwargs)
        for k,v in kwargs.items():
            Tp = type(getattr(self, k, None))
            #logging.debug(f"Type = {Tp}")
            #logging.debug(f"Key = {k}")
            #logging.debug(f"Val = {v}")
            if Tp is not type(None):
                logging.info(f"set {k} = {Tp(v)}")
                setattr(self, k, Tp(v))

        #clear any existing effects
        for e in effects.values(): e.destroy()
        effects.clear()
    effect_index_set = 4
    # def incr_clicked():
    #     if Aircraft.effect_index_set == 7:
    #         Aircraft.effect_index_set = 3
    #         logging.debug("Resetting Effect Index to 3")
    #         return
    #     Aircraft.effect_index_set += 1
    #     print("INCR button clicked!")
    #
    # def decr_clicked():
    #     if Aircraft.effect_index_set == 3:
    #         Aircraft.effect_index_set = 7
    #         logging.debug("Resetting Effect Index to 7")
    #         return
    #     Aircraft.effect_index_set -= 1
    #     print("DECR button clicked!")
    # def add_button_to_main_window(main_window):
    #     # Create a button
    #     button = QPushButton("Increment")
    #     button.clicked.connect(Aircraft.incr_clicked)
    #     main_window.layout().addWidget(button)

    # def add_button2_to_main_window(main_window):
    #     # Create a button
    #     button = QPushButton("Decrement")
    #     button.clicked.connect(Aircraft.decr_clicked)
    #     main_window.layout().addWidget(button)


    def has_changed(self, item : str) -> bool:
        prev_val = self._changes.get(item)
        new_val = self._telem_data.get(item)
        self._changes[item] = new_val
        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val,new_val)
        return False

    def anything_has_changed(self, item : str, value) -> bool:
        # track if any parameter, given as key "item" has changed between two consecutive calls of the function
        prev_val = self._changes.get(item)
        new_val = value
        self._changes[item] = new_val
        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val,new_val)
        return False
    def _calc_buffeting(self, aoa, speed) -> tuple:
        """Calculate buffeting amount and frequency

        :param aoa: Angle of attack in degrees
        :type aoa: float
        :param speed: Airspeed in m/s
        :type speed: float
        :return: Tuple (freq_hz, magnitude)
        :rtype: tuple
        """
        if not self.buffeting_intensity:
            return (0, 0)
        max_airflow_speed = 70 # speed at which airflow_factor is 1.0
        airflow_factor = utils.scale_clamp(speed, (0, max_airflow_speed), (0, 1.0))
        buffeting_factor = utils.scale_clamp(aoa, (self.buffet_aoa, self.stall_aoa), (0.0, 1.0))
        #todo calc frequency
        return (13.0, airflow_factor * buffeting_factor * self.buffeting_intensity)
        
 
        

    def _update_runway_rumble(self, telem_data):
        """Add wheel based rumble effects for immersion
        Generates bumps/etc on touchdown, rolling, field landing etc
        """
        if self.runway_rumble_intensity:
            WoW = telem_data.get("WeightOnWheels", (0,0,0)) # left, nose, right - wheels
            # get high pass filters for wheel shock displacement data and update with latest data
            hp_f_cutoff_hz = 3
            v1 = HPFs.get("center_wheel", hp_f_cutoff_hz).update((WoW[1])) * self.runway_rumble_intensity
            v2 = HPFs.get("side_wheels", hp_f_cutoff_hz).update(WoW[0]-WoW[2]) * self.runway_rumble_intensity

            # modulate constant effects for X and Y axis
            # connect Y axis to nosewheel, X axis to the side wheels
            tot_weight = sum(WoW)

            if tot_weight:
                effects["runway0"].constant(v1, 0).start()
                effects["runway1"].constant(v2, 90).start()
            else:
                effects.dispose("runway0")
                effects.dispose("runway1")

    def _update_buffeting(self, telem_data : dict):
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)
        agl = telem_data.get("altAgl", 0)

        buffeting = self._calc_buffeting(aoa, tas)
        # manage periodic effect for buffeting
        effects["buffeting"].periodic(buffeting[0], buffeting[1], 0).start()
        effects["buffeting2"].periodic(buffeting[0], buffeting[1], 45, phase=120).start()


        telem_data["dbg_buffeting"] = buffeting # save debug value
    def _update_drag_buffet(self, telem_data : dict, type : str):
            drag_buffet_threshold = 100     #indicated TAS via telemetry
            tas = telem_date.get("TAS", 0)
            if tas < drag_buffet_threshold:
                return 0

    def _update_cm_weapons(self, telem_data):
        if self.has_changed("PayloadInfo"):
            effects["cm"].stop()
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                #Init random number for effect direction
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"Payload Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["cm"].periodic(10, self.weapon_release_intensity, random_weapon_release_direction, duration=80).start()
            else:
                effects["cm"].periodic(10, self.weapon_release_intensity, self.weapon_effect_direction, duration=80).start()

        if self.has_changed("Gun") or self.has_changed("CannonShells"):
            effects["cm"].stop()
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                #Init random number for effect direction
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"Gun Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["cm"].periodic(10, self.gun_vibration_intensity, random_weapon_release_direction, duration=80).start()
            else:
                effects["cm"].periodic(10, self.gun_vibration_intensity, self.weapon_effect_direction, duration=80).start()
        
        if self.has_changed("Flares") or self.has_changed("Chaff"):
            effects["cm"].stop()
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                #Init random number for effect direction
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"CM Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["cm"].periodic(10, self.cm_vibration_intensity, random_weapon_release_direction, duration=80).start()
            else:
                effects["cm"].periodic(10, self.cm_vibration_intensity, self.weapon_effect_direction, duration=80).start()
  

    def _update_speed_brakes(self, telem_data : dict, spd_thresh=70):
        if "SpdbrakePos" not in self._change_counter:
            self._change_counter["SpdbrakePos"] = 0  # Initialize item for tracking consecutive "no change" calls to dampen start/stop of effects which do not update as fast as telemetry calls
        spdbrk = telem_data.get("SpeedbrakePos", 0)
        tas = telem_data.get("TAS",0)
        if self.has_changed("SpeedbrakePos"):
            logging.debug(f"Speedbrake Pos: {spdbrk}")
            effects["speedbrakemovement"].periodic(150, self.speedbrake_motion_intensity, 0, 3).start()
            effects["speedbrakemovement2"].periodic(150, self.speedbrake_motion_intensity, 45, 3).start()
        elif self._change_counter["SpdbrakePos"] < 20:
            #data has not seen 20 consecutive calls with no change, increment counter
            count = self._change_counter["SpdbrakePos"]
            self._change_counter["SpdbrakePos"] += 1
            #logging.debug(f"Speed Brake negative count = {count}")

        else:
            effects.dispose("speedbrakemovement")
            effects.dispose("speedbrakemovement2")
            self._change_counter["SpdbrakePos"] = 0

        if tas > spd_thresh and spdbrk > .1:
            #calculate insensity based on deployment percentage
            realtime_intensity = self.speedbrake_buffet_intensity * spdbrk
            effects["speedbrakebuffet"].periodic(13, realtime_intensity, 0, 4).start()
            effects["speedbrakebuffet2"].periodic(13, realtime_intensity, 45, 4).start()
           # logging.debug(f"PLAYING SPEEDBRAKE RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("speedbrakebuffet")
            effects.dispose("speedbrakebuffet2")

    def _update_landing_gear(self, telem_data : dict, spd_thresh_low=100, spd_thresh_high=150):
        if "GearPos" not in self._change_counter:
            self._change_counter["GearPos"] = 0 # Initialize item for tracking consecutive "no change" calls to dampen start/stop of effects which do not update as fast as telemetry calls
        gearpos = telem_data.get("GearPos", 0)
        tas = telem_data.get("TAS", 0)
        if self.has_changed("GearPos"):
            #logging.debug(f"Landing Gear Pos: {gearpos}")
            effects["gearmovement"].periodic(150, self.gear_motion_intensity, 0, 3).start()
            effects["gearmovement2"].periodic(150, self.gear_motion_intensity, 45, 3, phase=120).start()
        elif self._change_counter["GearPos"] < 20:
            #data has not seen 3 consecutive calls with no change, increment counter
            count = self._change_counter["GearPos"]
            #logging.debug(f"Gear Change negative count = {count}")
            self._change_counter["GearPos"] += 1
        else:
            #data has reached 20 consecutive calls with no data, destroy effects, reset counter to 0
            effects.dispose("gearmovement")
            effects.dispose("gearmovement2")
            self._change_counter["GearPos"] = 0

        if tas > spd_thresh_low and gearpos > .1:
            #calculate insensity based on deployment percentage
            #intensity will go from 0 to %100 configured between spd_thresh_low and spd_thresh_high

            realtime_intensity = utils.scale(tas, (spd_thresh_low, spd_thresh_high), (0, self.gear_buffet_intensity)) * gearpos

            effects["gearbuffet"].periodic(13, realtime_intensity, 0, 3).start()
            effects["gearbuffet2"].periodic(13, realtime_intensity, 90, 3).start()
            logging.debug(f"PLAYING GEAR RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("gearbuffet")
            effects.dispose("gearbuffet2")
         
    def _update_flaps(self, flapspos):
        #flapspos = telem_data.get("FlapsPos", 0)
        if self.has_changed("FlapsPos"):
            logging.debug(f"Flaps Pos: {flapspos}")
            effects["flapsmovement"].periodic(150, self.flaps_motion_intensity, 0, 3).start()
            effects["flapsmovement2"].periodic(150, self.flaps_motion_intensity, 45, 3, phase=120).start()
        else:
            effects.dispose("flapsmovement")
            effects.dispose("flapsmovement2")
    
    def _update_canopy(self, canopypos):
        #canopypos = telem_data.get("CanopyPos", 0)
        if self.has_changed("CanopyPos"):
            logging.debug(f"Canopy Pos: {canopypos}")
            effects["canopymovement"].periodic(150, self.canopy_motion_intensity, 0, 3).start()
            effects["canopymovement2"].periodic(150, self.canopy_motion_intensity, 45, 3, phase=120).start()
        else:
            effects.dispose("canopymovement")
            effects.dispose("canopymovement2")
            
    def on_telemetry(self, telem_data : dict):
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """
        self._telem_data = telem_data

        self._update_buffeting(telem_data)
        self._update_runway_rumble(telem_data)
        self._update_cm_weapons(telem_data)
       

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
    max_aoa_cf_force : float = 0.2 # CF force sent to device at %stall_aoa
    _engine_rumble_is_playing = 0
    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)
       
        #(wx,wz,wy) = telem_data["12_Wind"]
        #yaw, pitch, roll = telem_data.get("SelfData", (0,0,0))
        #wnd = utils.to_body_vector(yaw, pitch, roll, (wx,wy,wz) )
        wind = telem_data.get("Wind", (0,0,0))
        wnd = math.sqrt(wind[0]**2 + wind[1]**2 + wind[2]**2)

        v = HPFs.get("wnd", 3).update(wnd)
        v = LPFs.get("wnd", 15).update(v)

        effects["wnd"].constant(v, utils.RandomDirectionModulator, 5).start()

        rpm = telem_data.get("EngRPM", 0)

        self._update_aoa_effect(telem_data)
        
        if self.engine_rumble:
            self._update_engine_rumble(rpm)

        if self.speedbrake_motion_intensity > 0 or self.speedbrake_buffet_intensity > 0:
            super()._update_speed_brakes(telem_data, spd_thresh=60)
        if self.gear_motion_intensity > 0 or self.gear_buffet_intensity > 0:

            super()._update_landing_gear(telem_data, spd_thresh_low=60, spd_thresh_high=90)

        if self.flaps_motion_intensity > 0:
            super()._update_flaps(self._telem_data.get("FlapsPos", 0))
        if self.canopy_motion_intensity > 0:
            super()._update_canopy(self._telem_data.get("CanopyPos", 0))

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
        logging.debug(f"rpm percent: {rpm_percentage}")
        interpolated_intensity = min_intensity + (max_intensity - min_intensity) * rpm_percentage
        
        return interpolated_intensity


class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    #flaps_motion_intensity = 0.0

    _ab_is_playing = 0
    def _read_effect_index(self):
        global periodic_effect_index
        logging.debug(f"periodic_effect_index: {periodic_effect_index}")
    def _update_ab_effect(self, intensity, telem_data):
        frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency+median_modulation
        precision = 2
        try:
            afterburner_pos = max(telem_data.get("Afterburner")[0], telem_data.get("Afterburner")[1])
        except: pass
        #logging.debug(f"Afterburner = {afterburner_pos}")
        r1_modulation = utils.get_random_within_range("rumble_1", median_modulation, median_modulation-modulation_neg, median_modulation+modulation_pos, precision, time_period=5  )
        r2_modulation = utils.get_random_within_range("rumble_2", median_modulation, median_modulation-modulation_neg, median_modulation+modulation_pos, precision, time_period=5  )

        if super().has_changed("Afterburner") or super().anything_has_changed("Modulation", r1_modulation):
            #logging.debug(f"AB Effect Updated: LT={Left_Throttle}, RT={Right_Throttle}")
            intensity = self.afterburner_effect_intensity * afterburner_pos
            effects["ab_rumble_1_1"].periodic(frequency, intensity, 0, Aircraft.effect_index_set).start()
            effects["ab_rumble_1_2"].periodic(frequency + r1_modulation, intensity, 0, Aircraft.effect_index_set).start()
            effects["ab_rumble_2_1"].periodic(frequency2, intensity, 45, 4, phase=120, offset=60).start()
            effects["ab_rumble_2_2"].periodic(frequency2 + r2_modulation, intensity, 45, 4, phase=120, offset=60).start()
            logging.debug(f"Modul1= {r1_modulation} | Modul2 = {r2_modulation}")
            self._ab_is_playing = 1
        elif afterburner_pos == 0:
            
            #logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            effects.dispose("ab_rumble_1_1")
            effects.dispose("ab_rumble_1_2")
            effects.dispose("ab_rumble_2_1")
            effects.dispose("ab_rumble_2_2")
            self._ab_is_playing = 0
    
    
 #    def _calculate_ab_effect(self, intensity, min_throt, max_throt, eng1, eng2=-1):
 #        eng1_intensity = 0
 #        eng2_intensity = 0
 #        effect_factor = 1
 #        if eng1 < min_throt and eng2 < min_throt:
 #            return 0
 #        # Determine if calling aircraft is single or multi-engine (eng2 will be -1 if eng2 argument was not sent)
 #        if eng2 == -1:
 #            percentage1 = (eng1 - min_throt) / (max_throt - min_throt)
 #            eng1_intensity = intensity * (0.1 + (0.9 * percentage1))
 #            return eng1_intensity
 #
 #        # If twin-engine, and only one engine is above the afterburner threshold, we will return 80% of the highest engine's calculated intensity
 #        # Else we will return 100% of the highest engine's calculated intensity
 #        if eng1 < min_throt or eng2 < min_throt:
 #            effect_factor = 0.6
 #
 #        # Calculate both effect factors
 #        percentage1 = (eng1 - min_throt) / (max_throt - min_throt)
 #        eng1_intensity = intensity * (0.1 + (0.9 * percentage1))
 #
 #        percentage2 = (eng2 - min_throt) / (max_throt - min_throt)
 #        eng2_intensity = intensity * (0.1 + (0.9 * percentage2))
 #        #return highest throttle setting to use for intensity
 #        return max(eng1_intensity, eng2_intensity) * effect_factor
 # #_calculate_ab_effect(0.3, .8, 1.0, .5, .5)
    
    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)
        
        if self.speedbrake_motion_intensity > 0 or self.speedbrake_buffet_intensity > 0:
            super()._update_speed_brakes(telem_data, spd_thresh=90)
        if self.gear_motion_intensity > 0 or self.gear_buffet_intensity > 0:
            super()._update_landing_gear(telem_data, spd_thresh_low=100, spd_thresh_high=150)
        if self.flaps_motion_intensity > 0:
            super()._update_flaps(self._telem_data.get("FlapsPos", 0))
        if self.canopy_motion_intensity > 0:
            super()._update_canopy(self._telem_data.get("CanopyPos", 0))
        if self.afterburner_effect_intensity > 0:
            self._update_ab_effect(self.afterburner_effect_intensity, telem_data)

        
class FA18(JetAircraft):
    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)
        # try:
        #     eng1 = telem_data.get("Engine_RPM")[0]
        # except:
        #     logging.error("Error getting engine RPM data")
        #     eng1 = 0
        #
        # try:
        #     eng2 = telem_data.get("Engine_RPM")[1]
        # except:
        #     logging.error("Error getting engine RPM data")
        #     eng2 = 0
        # logging.debug(f"F18 - Eng1={eng1}, Eng2={eng2}")
        # if eng1 > 0 or eng2 > 0:
        #     if self.afterburner_effect_intensity > 0:
        #         super()._update_ab_effect(self.afterburner_effect_intensity, telem_data, 0.8, 1.0, self._telem_data.get("Throttle_1", 0), self._telem_data.get("Throttle_2", 0))

class F16(JetAircraft):
    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)
        # try:
        #     eng1 = telem_data.get("Engine_RPM")[0]
        # except:
        #     logging.error("Error getting engine RPM data")
        #     eng1 = 0
        # logging.debug(f"F16 - Eng1={eng1}")
        # if eng1 > 0:
        #     if self.afterburner_effect_intensity > 0:
        #         super()._update_ab_effect(self.afterburner_effect_intensity, telem_data, 0.7, 1.0, self._telem_data.get("Throttle_1", 0))

class SU33(JetAircraft):
    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)
        # try:
        #     eng1 = telem_data.get("Engine_RPM")[0]
        #     eng2 = telem_data.get("Engine_RPM")[1]
        # except:
        #     logging.error("Error getting engine RPM data")
        # logging.debug(f"F18 - Eng1={eng1}, Eng2={eng2}")
        # if eng1 > 0 or eng2 > 0:
        #     if self.afterburner_effect_intensity > 0:
        #         super()._update_ab_effect(self.afterburner_effect_intensity, telem_data, 0.8, 1.0, self._telem_data.get("Throttle_1", 0), self._telem_data.get("Throttle_2", 0))
class Helicopter(Aircraft):
    """Generic Class for Helicopters"""
    buffeting_intensity = 0.0

    etl_start_speed = 6.0 # m/s
    etl_stop_speed = 22.0 # m/s
    etl_effect_intensity = 0.2 # [ 0.0 .. 1.0]
    etl_shake_frequency = 14.0
    overspeed_shake_start = 70.0 # m/s
    overspeed_shake_intensity = 0.2

    def _calc_etl_effect(self, telem_data):
        tas = telem_data.get("TAS", 0)
        etl_mid = (self.etl_start_speed + self.etl_stop_speed)/2.0

        if tas < etl_mid and tas > self.etl_start_speed:
            shake = utils.scale_clamp(tas, (self.etl_start_speed, etl_mid), (0.0, self.etl_effect_intensity))
        elif tas >= etl_mid and tas < self.etl_stop_speed:
            shake = utils.scale_clamp(tas, (etl_mid, self.etl_stop_speed), (self.etl_effect_intensity, 0.0))
        elif tas > self.overspeed_shake_start:
            shake = utils.scale_clamp(tas, (self.overspeed_shake_start, self.overspeed_shake_start+20), (0, self.overspeed_shake_intensity))
        else:
            shake = 0

        #telem_data["dbg_shake"] = shake

        if shake:
            effects["etlX"].periodic(self.etl_shake_frequency, shake, 45).start()
            #effects["etlY"].periodic(12, shake, 0).start()
        else:
            effects["etlX"].stop()
            #effects["etlY"].stop()

    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)

        self._calc_etl_effect(telem_data)


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
    "F-16C_50": F16,
    "FA-18C_hornet": FA18,
    "default": Aircraft
}
