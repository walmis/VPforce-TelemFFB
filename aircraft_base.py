import logging
import time
import math
import random
import utils
from typing import List, Dict
# from utils import clamp, HighPassFilter, Derivative, Dispenser

from ffb_rhino import HapticEffect, FFBReport_SetCondition

# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
effects: Dict[str, HapticEffect] = utils.Dispenser(HapticEffect)

# Highpass filter dispenser
HPFs: Dict[str, utils.HighPassFilter] = utils.Dispenser(utils.HighPassFilter)

# Lowpass filter dispenser
LPFs: Dict[str, utils.LowPassFilter] = utils.Dispenser(utils.LowPassFilter)

# unit conversions (to m/s)
knots = 0.514444
kmh = 1.0 / 3.6
deg = math.pi / 180
fpss2gs = 1 / 32.17405


class AircraftBase(object):
    damper_force = 0
    inertia_force = 0

    engine_rumble: int = 0  # Engine Rumble - Disabled by default - set to 1 in config file to enable

    engine_rumble_intensity: float = 0.02
    engine_rumble_lowrpm = 450
    engine_rumble_lowrpm_intensity: float = 0.12
    engine_rumble_highrpm = 2800
    engine_rumble_highrpm_intensity: float = 0.06

    max_aoa_cf_force: float = 0.2  # CF force sent to device at %stall_aoa
    rpm_scale: float = 45

    _engine_rumble_is_playing = 0

    def __init__(self, name: str, **kwargs):
        self._name = name
        self._changes = {}
        self._change_counter = {}
        self._telem_data = None

    def apply_settings(self, settings_dict):
        for k, v in settings_dict.items():
            if k in ["type"]: continue
            if getattr(self, k, None) is None:
                logging.warn(f"Unknown parameter {k}")
                continue
            logging.info(f"set {k} = {v}")
            setattr(self, k, v)

    def has_changed(self, item: str, delta_ms=0, data=None) -> bool:
        if data == None:
            data = self._telem_data

        prev_val, tm = self._changes.get(item, (None, 0))
        new_val = data.get(item)

        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        if prev_val != new_val:
            self._changes[item] = (new_val, time.perf_counter())

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val, new_val)

        if time.perf_counter() - tm < delta_ms / 1000.0:
            return True

        return False
    
    def is_joystick(self):
        return self._telem_data.get("FFBType", "joystick") == "joystick"
    
    def is_pedals(self):
        return self._telem_data.get("FFBType") == "pedals"

    def anything_has_changed(self, item: str, value, delta_ms=0):
        """track if any parameter, given as key "item" has changed between two consecutive calls of the function
        delta_ms can be used to smooth the effects of telemetry which does not update regularly but is still "moving"
        a positive delta_ms value will allow the data to remain unchanged for that period of time before returning false"""

        prev_val, tm, changed_yet = self._changes.get(item, (None, 0, 0))
        new_val = value
        new_tm = time.perf_counter()
        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        # make sure we do not return true until the key has changed at least once (after init)
        if prev_val == None and not changed_yet:
            self._changes[item] = (new_val, tm, 0)
            prev_val = new_val

        # logging.debug(f"Prev: {prev_val}, New: {new_val}, TM: {tm}")

        if prev_val != new_val:
            self._changes[item] = (new_val, new_tm, 1)

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val, new_val, new_tm - tm)

        if time.perf_counter() - tm < delta_ms / 1000.0:
            return True

        return False
    def _sim_is_msfs(self, telem_data):
        if telem_data.get("src") == "MSFS2020":
            return 1
        else:
            return 0

    def _sim_is_dcs(self, telem_data):
        if telem_data.get("src") == "DCS":
            return 1
        else:
            return 0

    ########################################
    ######                            ######
    ######  Generic Aircraft Effects  ######
    ######                            ######
    ########################################
    def _update_runway_rumble(self, telem_data):
        """Add wheel based rumble effects for immersion
        Generates bumps/etc on touchdown, rolling, field landing etc
        """
        if self.runway_rumble_intensity:
            WoW = telem_data.get("WeightOnWheels", (0, 0, 0))  # left, nose, right - wheels
            # get high pass filters for wheel shock displacement data and update with latest data
            hp_f_cutoff_hz = 3
            v1 = HPFs.get("center_wheel", hp_f_cutoff_hz).update((WoW[1])) * self.runway_rumble_intensity
            v2 = HPFs.get("side_wheels", hp_f_cutoff_hz).update(WoW[0] - WoW[2]) * self.runway_rumble_intensity

            v1 = utils.clamp_minmax(v1, 0.5)
            v2 = utils.clamp_minmax(v2, 0.5)

            # modulate constant effects for X and Y axis
            # connect Y axis to nosewheel, X axis to the side wheels
            tot_weight = sum(WoW)

            if telem_data.get("T", 0) > 2:  # wait a bit for data to settle
                if tot_weight:
                    logging.debug(f"Runway Rumble : v1 = {v1}. v2 = {v2}")
                    effects["runway0"].constant(v1, 0).start()
                    effects["runway1"].constant(v2, 90).start()
                else:
                    effects.dispose("runway0")
                    effects.dispose("runway1")

    def _gforce_effect(self, telem_data):

        # gforce_effect_enable = 1
        gneg = -1.0
        gmin = self.gforce_min_gs
        gmax = self.gforce_max_gs
        direction = 180
        # if not gforce_effect_enable:
        #     return
        if self._sim_is_dcs(telem_data):
            z_gs: float = telem_data.get("ACCs")[1]
        elif self._sim_is_msfs(telem_data):
            z_gs: float = telem_data.get("G")

        if z_gs < gmin:
            effects.dispose("gforce")
            # effects.dispose("gforce_damper")
            return
        # g_factor = round(utils.scale(z_gs, (gmin, gmax), (0, self.gforce_effect_max_intensity)), 4)
        if self.gforce_effect_invert_force:
            direction = 0
        g_factor = round(utils.non_linear_scaling(z_gs, gmin, gmax, curvature=self.gforce_effect_curvature), 4)
        g_factor = utils.clamp(g_factor, 0.0, 1.0)
        effects["gforce"].constant(g_factor, direction).start()
        #  effects["gforce_damper"].damper(coef_y=1024).start()

        logging.debug(f"G's = {z_gs} | gfactor = {g_factor}")

    def _aoa_reduction_force_effect(self, telem_data):
        start_aoa = self.critical_aoa_start
        end_aoa = self.critical_aoa_max
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)

        if aoa >= start_aoa and tas > 10:
            force_factor = round(utils.non_linear_scaling(aoa, start_aoa, end_aoa, curvature=1.5), 4)
            force_factor = self.aoa_reduction_max_force * force_factor
            force_factor = utils.clamp(force_factor, 0.0, 1.0)
            logging.debug(f"AoA Reduction Effect:  AoA={aoa}, force={force_factor}, max allowed force={self.aoa_reduction_max_force}")
            effects["crit_aoa"].constant(force_factor, 180).start()
        else:
            effects.dispose("crit_aoa")
        return
    
    def _decel_effect(self, telem_data):
        if self._sim_is_dcs(telem_data):
            y_gs = telem_data.get("ACCs")[0]
        elif self._sim_is_msfs(telem_data):
            y_gs = telem_data.get("G_BODY_Z")
        if not self.anything_has_changed("decel", y_gs):
            return
        if not sum(telem_data.get("WeightOnWheels")):
            return
        max_gs = self.deceleration_max_force
        if y_gs < -0.1:
            if abs(y_gs) > max_gs:
                y_gs = -max_gs
            logging.debug(f"y_gs = {y_gs}")
            effects["decel"].constant(abs(y_gs), 180).start()
        else:
            effects.dispose("decel")

    def _calc_buffeting(self, aoa, speed, telem_data) -> tuple:
        """Calculate buffeting amount and frequency

        :param aoa: Angle of attack in degrees
        :type aoa: float
        :param speed: Airspeed in m/s
        :type speed: float
        :return: Tuple (freq_hz, magnitude)
        :rtype: tuple
        """
        stall_buffet_threshold_percent = 110


        if self._sim_is_msfs(telem_data):
            local_stall_aoa = telem_data.get("StallAoA", 0)   # Get stall AoA telemetry from MSFS
            local_buffet_aoa = local_stall_aoa * (stall_buffet_threshold_percent/100)
        else:
            local_stall_aoa = self.stall_aoa
            local_buffet_aoa = self.buffet_aoa

        if not self.buffeting_intensity:
            return (0, 0)
        max_airflow_speed = 75*knots  # speed at which airflow_factor is 1.0
        airflow_factor = utils.scale_clamp(speed, (0, max_airflow_speed), (0, 1.0))
        buffeting_factor = utils.scale_clamp(aoa, (local_buffet_aoa, local_stall_aoa), (0.0, 1.0))
        # todo calc frequency
        return (13.0, airflow_factor * buffeting_factor * self.buffeting_intensity)

    def _update_buffeting(self, telem_data: dict):
        if not self.buffeting_intensity:
            return
        
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)

        max_airflow_speed = 75*knots  # speed at which airflow_factor is 1.0

        ds = telem_data.get("DesignSpeed", None)
        if ds:
            stall_aoa = telem_data.get("StallAoA", None)
            #vc - This design constant represents the aircraft ideal cruising speed
            #vs0 - This design constant represents the the stall speed when flaps are fully extended
            #vs1 - This design constant represents the stall speed when flaps are fully retracted
            vc, vs0, vs1 = ds
            #max_airflow_speed = vc
            
        local_stall_aoa = telem_data.get("StallAoA", None)
        if local_stall_aoa is not None:
            flaps = utils.average(telem_data.get("Flaps", 0)) * 0.2 # flaps down increases stall threshold by 20%
            stall_buffet_threshold_percent = 0.5 + flaps
            local_buffet_aoa = local_stall_aoa * stall_buffet_threshold_percent
        else:
            local_stall_aoa = self.stall_aoa
            local_buffet_aoa = self.buffet_aoa

        if aoa < local_buffet_aoa:
            effects.dispose("buffeting")
            return
        
        airflow_factor = utils.scale_clamp(tas, (0, max_airflow_speed), (0, 1.0))
        buffeting_factor = utils.scale_clamp(aoa, (local_buffet_aoa, local_stall_aoa), (0.0, 1.0))
        # todo calc frequency
        freq = 13
        # return (13.0, airflow_#factor * buffeting_factor * self.buffeting_intensity)
        # freq, mag = self._calc_buffeting(aoa, tas, telem_data)
        # manage periodic effect for buffeting
        mag = airflow_factor * buffeting_factor * self.buffeting_intensity
        #logging.debug(f"Buffeting: {mag}")
        effects["buffeting"].periodic(freq, mag, utils.RandomDirectionModulator).start()
        # effects["buffeting2"].periodic(freq, mag, 45, phase=120).start()

        telem_data["_buffeting"] = mag  # save debug value

    def _update_drag_buffet(self, telem_data: dict, type: str):
        drag_buffet_threshold = 100  # indicated TAS via telemetry
        tas = telem_data.get("TAS", 0)
        if tas < drag_buffet_threshold:
            return 0

    def _update_cm_weapons(self, telem):
        payload = telem.get("PayloadInfo")
        gun = telem.get("Gun")
        flares = telem.get("Flares")
        chaff = telem.get("Chaff")
        if self.anything_has_changed("PayloadInfo", payload):
            effects["cm"].stop()
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                # Init random number for effect direction
                random.seed(time.perf_counter())
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"Payload Effect Direction is randomized: {random_weapon_release_direction} deg"); effects["cm"].periodic(10, self.weapon_release_intensity, random_weapon_release_direction,
                                       duration=80).start()
            else:
                effects["cm"].periodic(10, self.weapon_release_intensity, self.weapon_effect_direction,
                                       duration=80).start()

        if self.anything_has_changed("Gun", gun):
            effects["cm"].stop()
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                # Init random number for effect direction
                random.seed(time.perf_counter())
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"Gun Effect Direction is randomized: {random_weapon_release_direction} deg"); effects["cm"].periodic(10, self.gun_vibration_intensity, random_weapon_release_direction,
                                       duration=80).start()
            else:
                effects["cm"].periodic(10, self.gun_vibration_intensity, self.weapon_effect_direction,
                                       duration=80).start()

        if self.anything_has_changed("Flares", flares) or self.anything_has_changed("Chaff", chaff):
            effects["cm"].stop()
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                # Init random number for effect direction
                random.seed(time.perf_counter())
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"CM Effect Direction is randomized: {random_weapon_release_direction} deg"); effects["cm"].periodic(50, self.cm_vibration_intensity, random_weapon_release_direction,
                                       duration=80).start()
            else:
                effects["cm"].periodic(50, self.cm_vibration_intensity, self.weapon_effect_direction,
                                       duration=80).start()

    def _update_flaps(self, flapspos):
        # flapspos = data.get("Flaps")
        if self.anything_has_changed("Flaps", flapspos, delta_ms=50):
            logging.debug(f"Flaps Pos: {flapspos}")
            effects["flapsmovement"].periodic(180, self.flaps_motion_intensity, 0, 3).start()
        else:
            effects.dispose("flapsmovement")

    def _update_canopy(self, canopypos):
        # canopypos = self._telem_data.get("canopy_value", 0)
        if self.anything_has_changed("Canopy", canopypos, delta_ms=300):
            logging.debug(f"Canopy Pos: {canopypos}")
            effects["canopymovement"].periodic(120, self.canopy_motion_intensity, 0, 3).start()
        else:
            effects.dispose("canopymovement")

    def _update_landing_gear(self, gearpos, tas, spd_thresh_low=100, spd_thresh_high=150):
        # gearpos = self._telem_data.get("gear_value", 0)
        rumble_freq = 10
        # tas =  self._telem_data.get("TAS", 0)
        if self.anything_has_changed("gear_value", gearpos, 50):
            logging.debug(f"Landing Gear Pos: {gearpos}")
            effects["gearmovement"].periodic(150, self.gear_motion_intensity, 0, 3).start()
            effects["gearmovement2"].periodic(150, self.gear_motion_intensity, 45, 3, phase=120).start()
        else:
            effects.dispose("gearmovement")
            effects.dispose("gearmovement2")

        if tas > spd_thresh_low and gearpos > .1:
            # calculate insensity based on deployment percentage
            # intensity will go from 0 to %100 configured between spd_thresh_low and spd_thresh_high

            realtime_intensity = utils.scale(tas, (spd_thresh_low, spd_thresh_high),
                                             (0, self.gear_buffet_intensity)) * gearpos

            effects["gearbuffet"].periodic(rumble_freq, realtime_intensity, 0, 4).start()
            effects["gearbuffet2"].periodic(rumble_freq, realtime_intensity, 90, 4).start()
            logging.debug(f"PLAYING GEAR RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("gearbuffet")
            effects.dispose("gearbuffet2")

    def _update_speed_brakes(self, spdbrk, tas, spd_thresh=70):
        # tas = self._telem_data.get("TAS",0)

        # spdbrk = self._telem_data.get("speedbrakes_value", 0)
        if self.anything_has_changed("speedbrakes_value", spdbrk, 50):
            logging.debug(f"Speedbrake Pos: {spdbrk}")
            effects["speedbrakemovement"].periodic(180, self.speedbrake_motion_intensity, 0, 3).start()
        else:
            effects.dispose("speedbrakemovement")

        if tas > spd_thresh and spdbrk > .1:
            # calculate insensity based on deployment percentage
            realtime_intensity = self.speedbrake_buffet_intensity * spdbrk
            effects["speedbrakebuffet"].periodic(13, realtime_intensity, 0, 4).start()
            effects["speedbrakebuffet2"].periodic(13, realtime_intensity, 45, 4).start()
            logging.debug(f"PLAYING SPEEDBRAKE RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("speedbrakebuffet")
            effects.dispose("speedbrakebuffet2")

    def _update_spoiler(self, spoiler, tas, spd_thresh_low=25, spd_thresh_hi=60):
        # tas = self._telem_data.get("TAS",0)
        tas_intensity = utils.clamp_minmax(utils.scale(tas, (spd_thresh_low, spd_thresh_hi), (0.0, 1.0)), 1.0)

        # spoiler = self._telem_data.get("Spoilers", 0)
        if spoiler == 0 or spoiler == None:
            effects.dispose("spoilermovement")
            effects.dispose("spoilermovement2")
            return
        # average all spoiler values together
        if type(spoiler) == list:
            if "F-14" in self._telem_data.get("N"):
                # give %85 weight to inner spoilers for intensity calculation
                spoiler_inner = (spoiler[1], spoiler[2])
                spoiler_outer = (spoiler[0], spoiler[3])
                spoiler = (0.85 * sum(spoiler_inner) + 0.15 * sum(spoiler_outer)) / 2
            else:
                spoiler = sum(spoiler) / len(spoiler)

        if self.spoiler_motion_intensity > 0:
            if self.anything_has_changed("Spoilers", spoiler):
                logging.debug(f"Spoilers Pos: {spoiler}")
                effects["spoilermovement"].periodic(118, self.spoiler_motion_intensity, 0, 4).start()
                effects["spoilermovement2"].periodic(118, self.spoiler_motion_intensity, 90, 4).start()
            else:
                logging.debug(f"Destroying Spoiler Effects")
                effects.dispose("spoilermovement")
                effects.dispose("spoilermovement2")

        if tas > spd_thresh_low and spoiler > .1:
            # calculate insensity based on deployment percentage
            realtime_intensity = self.spoiler_buffet_intensity * spoiler * tas_intensity
            logging.debug(f"PLAYING SPOILER RUMBLE | intensity: {realtime_intensity}, d-factor: {spoiler}, s-factor: {tas_intensity}")
            effects["spoilerbuffet1-1"].periodic(15, realtime_intensity, 0, 4).start()
            effects["spoilerbuffet1-2"].periodic(16, realtime_intensity, 0, 4).start()
            effects["spoilerbuffet2-1"].periodic(14, realtime_intensity, 90, 4).start()
            effects["spoilerbuffet2-2"].periodic(18, realtime_intensity, 90, 4).start()
        else:
            effects.dispose("spoilerbuffet1-1")
            effects.dispose("spoilerbuffet1-2")
            effects.dispose("spoilerbuffet2-1")
            effects.dispose("spoilerbuffet2-2")

    def _update_wind_effect(self, telem_data):

        wind = telem_data.get("Wind", (0, 0, 0))
        wnd = math.sqrt(wind[0] ** 2 + wind[1] ** 2 + wind[2] ** 2)

        v = HPFs.get("wnd", 3).update(wnd)
        v = LPFs.get("wnd", 15).update(v)
        logging.debug(f"Adding wind effect intensity:{v}")
        effects["wnd"].constant(v, utils.RandomDirectionModulator, 5).start()

    ########################################
    ######                            ######
    ######    Prop Aircraft Effects   ######
    ######                            ######
    ########################################
    def _update_aoa_effect(self, telem_data):
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)
        if self._sim_is_msfs(telem_data):
            local_stall_aoa = telem_data.get("StallAoA")
        else:
            local_stall_aoa = self.stall_aoa
        if aoa:
            aoa = float(aoa)
            speed_factor = utils.scale_clamp(tas, (50 * kmh, 140 * kmh), (0, 1.0))
            mag = utils.scale_clamp(abs(aoa), (0, local_stall_aoa), (0, self.max_aoa_cf_force))
            mag *= speed_factor
            if (aoa > 0):
                dir = 0
            else:
                dir = 180

            telem_data["aoa_pull"] = mag
            logging.debug(f"AOA EFFECT:{mag}")
            effects["aoa"].constant(mag, dir).start()

    def _update_engine_rumble(self, rpm):
        if type(rpm) == list:
            rpm = rpm[0]

        frequency = float(rpm) / 60

        # frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency + median_modulation
        precision = 2
        r1_modulation = utils.get_random_within_range("rumble_1", median_modulation, median_modulation - modulation_neg, median_modulation + modulation_pos, precision, time_period=5)
        r2_modulation = utils.get_random_within_range("rumble_2", median_modulation, median_modulation - modulation_neg, median_modulation + modulation_pos, precision, time_period=5)

        if frequency > 0:
            force_limit = max(self.engine_rumble_highrpm_intensity, self.engine_rumble_lowrpm_intensity)
            dynamic_rumble_intensity = utils.clamp(self._calc_engine_intensity(rpm), 0, force_limit)
            logging.debug(f"Current Engine Rumble Intensity = {dynamic_rumble_intensity}")

            effects["rpm0-1"].periodic(frequency, dynamic_rumble_intensity, 0).start()  # vib on X axis
            effects["rpm0-2"].periodic(frequency + r1_modulation, dynamic_rumble_intensity, 0).start()  # vib on X
            effects["rpm1-1"].periodic(frequency2, dynamic_rumble_intensity, 90).start()  # vib on Y axis
            effects["rpm1-2"].periodic(frequency2 + r2_modulation, dynamic_rumble_intensity, 90).start()  # vib on Y
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

        if rpm < min_rpm:
            #give some extra juice if RPM is very low (i.e. on engine start)
            interpolated_intensity = utils.scale(rpm, (0, min_rpm), (max_intensity*2, max_intensity))
        else:
            #update to use scaling function
            interpolated_intensity = utils.scale(rpm, (min_rpm, max_rpm), (max_intensity, min_intensity))
        logging.debug(f"rpm = {rpm} | rpm percent of range: {rpm_percentage} | interpolated intensity: {interpolated_intensity}")

        return interpolated_intensity

    ########################################
    ######                            ######
    ######    Jet Aircraft Effects    ######
    ######                            ######
    ########################################
    def _update_ab_effect(self, telem_data):
        frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency + median_modulation
        precision = 2
        try:
            afterburner_pos = max(telem_data.get("Afterburner")[0], telem_data.get("Afterburner")[1])
        except Exception as e:
            logging.error(f"Error getting afterburner position, sim probably disconnected: {e}")
            return
        logging.debug(f"Afterburner = {afterburner_pos}")
        r1_modulation = utils.get_random_within_range("rumble_1", median_modulation, median_modulation - modulation_neg,
                                                      median_modulation + modulation_pos, precision, time_period=5)
        r2_modulation = utils.get_random_within_range("rumble_2", median_modulation, median_modulation - modulation_neg,
                                                      median_modulation + modulation_pos, precision, time_period=5)
        if afterburner_pos and (
                self.anything_has_changed("Afterburner", afterburner_pos) or self.anything_has_changed("Modulation",
                                                                                                       r1_modulation)):
            # logging.debug(f"AB Effect Updated: LT={Left_Throttle}, RT={Right_Throttle}")
            intensity = self.afterburner_effect_intensity * afterburner_pos
            effects["ab_rumble_1_1"].periodic(frequency, intensity, 0, ).start()
            effects["ab_rumble_1_2"].periodic(frequency + r1_modulation, intensity, 0).start()
            effects["ab_rumble_2_1"].periodic(frequency2, intensity, 45, 4, phase=120, offset=60).start()
            effects["ab_rumble_2_2"].periodic(frequency2 + r2_modulation, intensity, 45, 4, phase=120,
                                              offset=60).start()
            # logging.debug(f"AB-Modul1= {r1_modulation} | AB-Modul2 = {r2_modulation}")
            self._ab_is_playing = 1
        elif afterburner_pos == 0:
            # logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            effects.dispose("ab_rumble_1_1")
            effects.dispose("ab_rumble_1_2")
            effects.dispose("ab_rumble_2_1")
            effects.dispose("ab_rumble_2_2")
            self._ab_is_playing = 0

    def _update_jet_engine_rumble(self, telem_data):
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
        r1_modulation = utils.get_random_within_range("jetengine_1", median_modulation,
                                                      median_modulation - modulation_neg,
                                                      median_modulation + modulation_pos, precision, time_period=1)
        r2_modulation = utils.get_random_within_range("jetengine_2", median_modulation,
                                                      median_modulation - modulation_neg,
                                                      median_modulation + modulation_pos, precision, time_period=2)
        if self.engine_rumble and (
                self.anything_has_changed("EngRPM", jet_eng_rpm) or self.anything_has_changed("JetEngineModul",
                                                                                              r1_modulation)):
            intensity = self.jet_engine_rumble_intensity * (jet_eng_rpm / 100)
            rt_freq = round(frequency + (10 * (jet_eng_rpm / 100)), 4)
            rt_freq2 = round(rt_freq + median_modulation, 4)
            effects["je_rumble_1_1"].periodic(rt_freq, intensity, 0, effect_index).start()
            effects["je_rumble_1_2"].periodic(rt_freq + r1_modulation, intensity, 0, effect_index).start()
            effects["je_rumble_2_1"].periodic(rt_freq2, intensity, 90, effect_index, phase=phase_offset).start()
            effects["je_rumble_2_2"].periodic(rt_freq2 + r2_modulation, intensity, 90, effect_index,
                                              phase=phase_offset).start()
            # logging.debug(f"RPM={jet_eng_rpm}")
            # logging.debug(f"Intensty={intensity}")
            logging.debug(f"JE-M1={r1_modulation}, F1-1={rt_freq}, F1-2={round(rt_freq + r1_modulation,4)} | JE-M2 = {r2_modulation}, F2-1={rt_freq2}, F2-2={round(rt_freq2 + r2_modulation, 4)} ")
            self._jet_rumble_is_playing = 1
        elif jet_eng_rpm == 0:
            # logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            effects.dispose("je_rumble_1_1")
            effects.dispose("je_rumble_1_2")
            effects.dispose("je_rumble_2_1")
            effects.dispose("je_rumble_2_2")
            self._jet_rumble_is_playing = 0

    ########################################
    ######                            ######
    ######     Helicopter Effects     ######
    ######                            ######
    ########################################

    def _calc_etl_effect(self, telem_data, blade_ct=None):
        #  rotor = 245
        mod = telem_data.get("N")
        tas = telem_data.get("TAS", 0)
        WoW = sum(telem_data.get("WeightOnWheels"))
        if mod == "UH-60L":
            # UH60 always shows positive value for tailwheel
            WoW = telem_data.get("WeightOnWheels")[0] + telem_data.get("WeightOnWheels")[2]
        rotor = telem_data.get("RotorRPM")
        if WoW > 0:
            logging.debug("On the Ground, moving forward. Probably on a Ship! - Dont play effect!")
            return
        if blade_ct is None:
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
                rotor = 245  # Apache does not have exportable data related to Rotor RPM
            elif "UH-60L" in mod:
                blade_ct = 4
            elif "SA342" in mod:
                blade_ct = 3
            else:
                blade_ct = 2
                rotor = 250

        if rotor:
            self.etl_shake_frequency = (rotor / 75) * blade_ct

        etl_mid = (self.etl_start_speed + self.etl_stop_speed) / 2.0

        if tas >= self.etl_start_speed and tas <= self.etl_stop_speed:
            shake = self.etl_effect_intensity * utils.gaussian_scaling(tas, self.etl_start_speed, self.etl_stop_speed, peak_percentage=0.5, curve_width=.7)
        # logging.debug(f"Gaussian Scaling calc = {shake}")
            logging.debug(f"Playing ETL shake (freq = {self.etl_shake_frequency}, intens= {shake})")

        elif tas >= self.overspeed_shake_start:
            shake = self.overspeed_shake_intensity * utils.non_linear_scaling(tas, self.overspeed_shake_start, self.overspeed_shake_start + 15, curvature=.7)
            # shake = utils.scale_clamp(tas, (self.overspeed_shake_start, self.overspeed_shake_start+20), (0, self.overspeed_shake_intensity))
            logging.debug(f"Overspeed shake (freq = {self.etl_shake_frequency}, intens= {shake}) ")
        else:
            shake = 0

        # telem_data["dbg_shake"] = shake

        if shake:
            effects["etlY"].periodic(self.etl_shake_frequency, shake, 0).start()
            effects["etlX"].periodic(self.etl_shake_frequency + 4, shake, 90).start()
            # effects["etlY"].periodic(12, shake, 0).start()
        else:
            effects["etlX"].stop()
            effects["etlY"].stop()
            # effects["etlY"].stop()

    def _update_heli_engine_rumble(self, telem_data, blade_ct=None):
        rrpm = telem_data.get("RotorRPM")
        mod = telem_data.get("N")
        tas = telem_data.get("TAS", 0)
        # rotor = telem_data.get("RotorRPM")
        if self._sim_is_msfs(telem_data) and rrpm < 10:
            #MSFS sends telemetry when aircraft is sitting in hangar with the rotor spinning very slowly....
            return

        if blade_ct is None:
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
        logging.debug(f"Engine Rumble: Blade_Ct={blade_ct}, RPM={rrpm}")
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
        if frequency > 0:
            logging.debug(f"Current Heli Engine Rumble Intensity = {self.heli_engine_rumble_intensity}")
            effects["rpm0-1"].periodic(frequency, self.heli_engine_rumble_intensity * .5, 0).start()  # vib on X axis
            # effects["rpm0-2"].periodic(frequency + r1_modulation, dynamic_rumble_intensity, 0).start()  # vib on X axis
            effects["rpm1-1"].periodic(frequency2, self.heli_engine_rumble_intensity * .5, 90).start()  # vib on Y axis
            # effects["rpm1-2"].periodic(frequency2 + r2_modulation, dynamic_rumble_intensit, 90).start()  # vib on Y axis
            self._engine_rumble_is_playing = 1
        else:
            self._engine_rumble_is_playing = 0
            effects.dispose("rpm0-1")
            # effects.dispose("rpm0-2")
            effects.dispose("rpm1-1")
            # effects.dispose("rpm1-2")

    def on_timeout(self):  # override me
        logging.debug("Timeout, preparing to stop effects")
        for e in effects.values():
            logging.debug(f"Timeout effect: {e}")
            e.stop()

    def on_telemetry(self, data): 

        if self.damper_force:
            force = utils.clamp(self.damper_force, 0.0, 1.0)
            effects["damper"].damper(4096*force, 4096*force).start()
        else:
            effects["damper"].destroy()

        if self.inertia_force:
            force = utils.clamp(self.inertia_force, 0.0, 1.0)
            effects["inertia"].damper(4096*force, 4096*force).start()
        else:
            effects["inertia"].destroy()
