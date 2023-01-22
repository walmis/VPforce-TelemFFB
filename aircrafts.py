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
    buffeting_intensity : float = 0.2 # peak AoA buffeting intensity  0 to disable
    buffet_aoa : float          = 10 # AoA when buffeting starts
    stall_aoa : float           = 15 # Stall AoA

    runway_rumble_intensity : float = 1.0 # peak runway intensity, 0 to disable

    gun_vibration_intensity : float = 0.12
    cm_vibration_intensity : float = 0.12
    weapon_release_intensity : float = 0.12

    ####
    def __init__(self, name : str):
        self.name = name
        self.changes = {}
        self.telem_data = None
        #clear any existing effects
        for e in effects.values(): e.destroy()
        effects.clear()

    def has_changed(self, item : str) -> bool:
        prev_val = self.changes.get(item)
        new_val = self.telem_data.get(item)
        self.changes[item] = new_val
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
        return (12.0, airflow_factor * buffeting_factor * self.buffeting_intensity)

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

        telem_data["dbg_buffeting"] = buffeting # save debug value

    def _update_cm_weapons(self, telem_data):
        if self.has_changed("PayloadInfo"):
            effects["cm"].stop()
            effects["cm"].periodic(10, self.weapon_release_intensity, 45, duration=80).start()

        if self.has_changed("Gun") or self.has_changed("CannonShells"):
            effects["cm"].stop()
            effects["cm"].periodic(10, self.gun_vibration_intensity, 45, duration=50).start()
        if self.has_changed("Flares") or self.has_changed("Chaff"):
            effects["cm"].stop()
            effects["cm"].periodic(5, self.cm_vibration_intensity, 45, duration=30).start()

    def on_telemetry(self, telem_data : dict):
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """
        self.telem_data = telem_data

        self._update_buffeting(telem_data)
        self._update_runway_rumble(telem_data)
        self._update_cm_weapons(telem_data)

    def on_timeout(self):
        # stop all effects when telemetry stops
        for e in effects.values(): e.stop()

class PropellerAircraft(Aircraft):
    """Generic Class for Prop/WW2 aircraft"""

    aoa_shaker_enable = True
    engine_rumble : bool            = True
    engine_rumble_intensity : float = 0.02
    max_aoa_cf_force : float           = 0.2 # CF force sent to device at %stall_aoa

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
            effects["rpm0"].periodic(freq, self.engine_rumble_intensity, 0).start() # vib on X axis
            effects["rpm1"].periodic(freq+2, self.engine_rumble_intensity, 90).start() # vib on Y axis
        else:
            effects.dispose("rpm0")
            effects.dispose("rpm1")



class JetAircraft(Aircraft):
    """Generic Class for Jets"""
       
    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)


class Helicopter(Aircraft):
    """Generic Class for Helicopters"""

    etl_start_speed = 6 # m/s
    etl_stop_speed = 22 # m/s
    etl_effect_intensity = 0.2 # [ 0.0 .. 1.0]
    etl_shake_frequency = 14
    overspeed_shake_start = 70 # m/s
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
    aoa_shaker_enable = False # implemented by DCS
    runway_rumble_intensity = 1.0
    engine_rumble = True # rumble based on RPM

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