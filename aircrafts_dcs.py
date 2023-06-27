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
    
    speedbrake_motion_intensity : float = 0.0      # peak vibration intensity when speed brake is moving, 0 to disable
    speedbrake_buffet_intensity : float = 0.0      # peak buffeting intensity when speed brake deployed,  0 to disable
    
    gear_motion_intensity : float = 0.0      # peak vibration intensity when gear is moving, 0 to disable
    gear_buffet_intensity : float = 0.0      # peak buffeting intensity when gear down during flight,  0 to disable
    
    flaps_motion_intensity : float = 0.0      # peak vibration intensity when flaps are moving, 0 to disable
    flaps_buffet_intensity : float = 0.0      # peak buffeting intensity when flaps are deployed,  0 to disable
    
    canopy_motion_intensity : float = 0.0      # peak vibration intensity when canopy is moving, 0 to disable
    canopy_buffet_intensity : float = 0.0      # peak buffeting intensity when canopy is open during flight,  0 to disable

    ####
    def __init__(self, name : str, **kwargs):
        self._name = name
        self._changes = {}
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

        self.spring = HapticEffect().spring()
        #self.spring.effect.effect_id = 5
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)

    def has_changed(self, item : str, delta_ms = 0) -> bool:
        prev_val, tm = self._changes.get(item, (None, 0))
        new_val = self._telem_data.get(item)
#        print(new_val)
        if prev_val != new_val:
            self._changes[item] = (new_val, time.perf_counter())

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val,new_val)
        
        if time.perf_counter() - tm < delta_ms/1000.0:
            return True

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
            
            v1 = utils.clamp_minmax(v1, 0.5)
            v2 = utils.clamp_minmax(v1, 0.5)

            # modulate constant effects for X and Y axis
            # connect Y axis to nosewheel, X axis to the side wheels
            tot_weight = sum(WoW)

            if tot_weight:
                # logging.info(f"v1 = {v1}")
                effects["runway0"].constant(v1, 0).start()
                # logging.info(f"v2 = {v2}")
                effects["runway1"].constant(v2, 90).start()
            else:
                effects.dispose("runway0")
                effects.dispose("runway1")

    def _update_buffeting(self, telem_data : dict):
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)
        agl = telem_data.get("altAgl", 0)

        freq, mag = self._calc_buffeting(aoa, tas)
        # manage periodic effect for buffeting
        if mag:
            effects["buffeting"].periodic(freq, mag, utils.RandomDirectionModulator).start()
            #effects["buffeting2"].periodic(freq, mag, 45, phase=120).start()

        telem_data["dbg_buffeting"] = (freq, mag) # save debug value

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
                effects["cm"].periodic(50, self.cm_vibration_intensity, random_weapon_release_direction, duration=80).start()
            else:
                effects["cm"].periodic(50, self.cm_vibration_intensity, self.weapon_effect_direction, duration=80).start()
  
  
    def _update_speed_brakes(self):
        spdbrk = self._telem_data.get("speedbrakes_value", 0)
        if self.has_changed("speedbrakes_value", 50):
            logging.debug(f"Speedbrake Pos: {spdbrk}")
            effects["speedbrakemovement"].periodic(200, self.speedbrake_motion_intensity, 0, 3).start()
        else:
            effects.dispose("speedbrakemovement")
            
    def _update_landing_gear(self):
        gearpos = self._telem_data.get("gear_value", 0)
        if self.has_changed("gear_value", 50):
            #logging.debug(f"Landing Gear Pos: {gearpos}")
            effects["gearmovement"].periodic(150, self.gear_motion_intensity, 0, 3).start()
            #effects["gearmovement2"].periodic(150, self.gear_motion_intensity, 45, 3, phase=120).start()
        else:
            effects.dispose("gearmovement")
            #effects.dispose("gearmovement2")
         
    def _update_flaps(self):
        flapspos = self._telem_data.get("flaps_value", 0)
        if self.has_changed("flaps_value", 50):
            logging.debug(f"Flaps Pos: {flapspos}")
            effects["flapsmovement"].periodic(180, self.flaps_motion_intensity, 0, 3).start()
            #effects["flapsmovement2"].periodic(150, self.flaps_motion_intensity, 45, 3, phase=120).start()
        else:
            effects.dispose("flapsmovement")
            #effects.dispose("flapsmovement2")
    
    def _update_canopy(self):
        canopypos = self._telem_data.get("canopy_value", 0)
        if self.has_changed("canopy_value", 50):
            logging.debug(f"Canopy Pos: {canopypos}")
            effects["canopymovement"].periodic(120, self.canopy_motion_intensity, 0, 3).start()
            #effects["canopymovement2"].periodic(150, self.canopy_motion_intensity, 45, 3, phase=120).start()
        else:
            effects.dispose("canopymovement")
            #effects.dispose("canopymovement2")
            
    def on_telemetry(self, telem_data : dict):
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """
        self._telem_data = telem_data

        self._update_buffeting(telem_data)
        self._update_runway_rumble(telem_data)
        self._update_cm_weapons(telem_data)
       
        if self.speedbrake_motion_intensity > 0:
            self._update_speed_brakes()
        if self.gear_motion_intensity > 0:
            self._update_landing_gear()
        if self.flaps_motion_intensity > 0:
            self._update_flaps()
        if self.canopy_motion_intensity > 0:
            self._update_canopy()

        # if stick position data is in the telemetry packet
        if "StickX" in telem_data and "StickY" in telem_data:
            x, y = HapticEffect.device.getInput()
            telem_data["X"] = x
            telem_data["Y"] = y

            self.spring_x.positiveCoefficient = 4096
            self.spring_x.negativeCoefficient = 4096
            self.spring_y.positiveCoefficient = 4096
            self.spring_y.negativeCoefficient = 4096
            
            # trim signal needs to be slow to avoid positive feedback
            lp_y = LPFs.get("y", 2)
            lp_x = LPFs.get("x", 2)

            # estimate trim from real stick position and virtual stick position
            offs_x = lp_x.update(telem_data['StickX'] - x + lp_x.value)
            offs_y = lp_y.update(telem_data['StickY'] - y + lp_y.value)
            self.spring_x.cpOffset = round(offs_x * 4096)
            self.spring_y.cpOffset = round(offs_y * 4096)

            #upload effect parameters to stick
            self.spring.effect.setCondition(self.spring_x)
            self.spring.effect.setCondition(self.spring_y)
            #ensure effect is started
            self.spring.start()

            # override DCS input and set our own values           
            return f"LoSetCommand(2001, {y - offs_y})\n"\
                   f"LoSetCommand(2002, {x - offs_x})"

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
        if isinstance(rpm, list):
            rpm = [x * self.rpm_scale for x in rpm]
            self._update_engine_rumble(rpm[0])
        else:
            rpm = self.rpm_scale
        telem_data["EngRPM"] = rpm

        self._update_aoa_effect(telem_data)
        
        if self.engine_rumble:
            self._update_engine_rumble(rpm)

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
        freq = float(rpm) / 60
        
        if freq > 0:
            dynamic_rumble_intensity = self._calc_engine_intensity(rpm)
            logging.debug(f"Current Engine Rumble Intensity = {dynamic_rumble_intensity}")
            #logging.info(f"EngineRPM {freq}")
            ## Experimenting with random direction for engine rumble effect
            #effects["rpm0"].periodic(freq, self.engine_rumble_intensity, random.choice([0, 180])).start() # vib on X axis
            #effects["rpm1"].periodic(freq+4, self.engine_rumble_intensity, random.choice([90, 270])).start() # vib on Y axis
            effects["rpm0"].periodic(freq, dynamic_rumble_intensity, 0).start() # vib on X axis
            effects["rpm1"].periodic(freq+2, dynamic_rumble_intensity, 90).start() # vib on Y axis
        else:
            effects.dispose("rpm0")
            effects.dispose("rpm1")
    
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
    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)
        


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
    "default": Aircraft
}
