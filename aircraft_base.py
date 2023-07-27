import logging
import time
import random
import utils
from typing import List, Dict
# from utils import clamp, HighPassFilter, Derivative, Dispenser

from ffb_rhino import HapticEffect, FFBReport_SetCondition

# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
effects : Dict[str, HapticEffect] = utils.Dispenser(HapticEffect)

# Highpass filter dispenser
HPFs : Dict[str, utils.HighPassFilter]  = utils.Dispenser(utils.HighPassFilter)

# Lowpass filter dispenser
LPFs : Dict[str, utils.LowPassFilter] = utils.Dispenser(utils.LowPassFilter)


class AircraftBase(object):
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
                    # logging.info(f"v1 = {v1}")
                    effects["runway0"].constant(v1, 0).start()
                    # logging.info(f"v2 = {v2}")
                    effects["runway1"].constant(v2, 90).start()
                else:
                    effects.dispose("runway0")
                    effects.dispose("runway1")

    def _decel_effect(self, telem_data):
        x_gs = telem_data.get("ACCs")[0]
        if not self.anything_has_changed("decel", x_gs):
            # logging.debug("nothing changed.,....")
            return
        if not sum(telem_data.get("WeightOnWheels")):
            return
        max_gs = self.deceleration_max_force
        if x_gs < -0.03:
            if effects["runway0"].started:
                effects.dispose("runway0")
                # logging.debug("disposing runway effect")
            if abs(x_gs) > max_gs:
                x_gs = -max_gs
            # logging.debug(f"x_gs = {x_gs}")
            effects["decel_x"].constant(abs(x_gs), 180).start()
        else:
            effects.dispose("decel_x")

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
        max_airflow_speed = 70  # speed at which airflow_factor is 1.0
        airflow_factor = utils.scale_clamp(speed, (0, max_airflow_speed), (0, 1.0))
        buffeting_factor = utils.scale_clamp(aoa, (self.buffet_aoa, self.stall_aoa), (0.0, 1.0))
        # todo calc frequency
        return (13.0, airflow_factor * buffeting_factor * self.buffeting_intensity)

    def _update_buffeting(self, telem_data: dict):
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)
        agl = telem_data.get("altAgl", 0)

        freq, mag = self._calc_buffeting(aoa, tas)
        # manage periodic effect for buffeting
        if mag:
            # logging.debug(f"Buffeting: {mag}")
            effects["buffeting"].periodic(freq, mag, utils.RandomDirectionModulator).start()
        # effects["buffeting2"].periodic(freq, mag, 45, phase=120).start()

        telem_data["dbg_buffeting"] = mag  # save debug value

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
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"Payload Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["cm"].periodic(10, self.weapon_release_intensity, random_weapon_release_direction,
                                       duration=80).start()
            else:
                effects["cm"].periodic(10, self.weapon_release_intensity, self.weapon_effect_direction,
                                       duration=80).start()

        if self.anything_has_changed("Gun", gun):
            effects["cm"].stop()
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                # Init random number for effect direction
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"Gun Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["cm"].periodic(10, self.gun_vibration_intensity, random_weapon_release_direction,
                                       duration=80).start()
            else:
                effects["cm"].periodic(10, self.gun_vibration_intensity, self.weapon_effect_direction,
                                       duration=80).start()

        if self.anything_has_changed("Flares", flares) or self.anything_has_changed("Chaff", chaff):
            effects["cm"].stop()
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                # Init random number for effect direction
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"CM Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["cm"].periodic(50, self.cm_vibration_intensity, random_weapon_release_direction,
                                       duration=80).start()
            else:
                effects["cm"].periodic(50, self.cm_vibration_intensity, self.weapon_effect_direction,
                                       duration=80).start()

    def _update_flaps(self, flapspos):
        # flapspos = data.get("Flaps")
        if self.anything_has_changed("Flaps", flapspos, delta_ms=50):
            # logging.debug(f"Flaps Pos: {flapspos}")
            effects["flapsmovement"].periodic(180, self.flaps_motion_intensity, 0, 3).start()
        else:
            effects.dispose("flapsmovement")

    def _update_canopy(self, canopypos):
        # canopypos = self._telem_data.get("canopy_value", 0)
        if self.anything_has_changed("Canopy", canopypos, delta_ms=300):
            # logging.debug(f"Canopy Pos: {canopypos}")
            effects["canopymovement"].periodic(120, self.canopy_motion_intensity, 0, 3).start()
        else:
            effects.dispose("canopymovement")

    def _update_landing_gear(self, gearpos, tas, spd_thresh_low=100, spd_thresh_high=150):
        # gearpos = self._telem_data.get("gear_value", 0)

        # tas =  self._telem_data.get("TAS", 0)
        if self.anything_has_changed("gear_value", gearpos, 50):
            # logging.debug(f"Landing Gear Pos: {gearpos}")
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

            effects["gearbuffet"].periodic(13, realtime_intensity, 0, 3).start()
            effects["gearbuffet2"].periodic(13, realtime_intensity, 90, 3).start()
            # logging.debug(f"PLAYING GEAR RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("gearbuffet")
            effects.dispose("gearbuffet2")

    def _update_speed_brakes(self, spdbrk, tas, spd_thresh=70):
        # tas = self._telem_data.get("TAS",0)

        # spdbrk = self._telem_data.get("speedbrakes_value", 0)
        if self.anything_has_changed("speedbrakes_value", spdbrk, 50):
            # logging.debug(f"Speedbrake Pos: {spdbrk}")
            effects["speedbrakemovement"].periodic(180, self.speedbrake_motion_intensity, 0, 3).start()
        else:
            effects.dispose("speedbrakemovement")

        if tas > spd_thresh and spdbrk > .1:
            # calculate insensity based on deployment percentage
            realtime_intensity = self.speedbrake_buffet_intensity * spdbrk
            effects["speedbrakebuffet"].periodic(13, realtime_intensity, 0, 4).start()
            effects["speedbrakebuffet2"].periodic(13, realtime_intensity, 45, 4).start()
        # logging.debug(f"PLAYING SPEEDBRAKE RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("speedbrakebuffet")
            effects.dispose("speedbrakebuffet2")

    def _update_spoiler(self, spoiler, tas, spd_thresh_low=25, spd_thresh_hi=60):
        # tas = self._telem_data.get("TAS",0)
        tas_intensity = utils.clamp_minmax(utils.scale(tas, (spd_thresh_low, spd_thresh_hi), (0.0, 1.0)), 1.0)

        # spoiler = self._telem_data.get("Spoilers", 0)
        if spoiler == 0 or spoiler == None:
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
            if self.has_changed("Spoilers", 15):
                # logging.debug(f"Spoilers Pos: {spoiler}")
                effects["spoilermovement"].periodic(118, self.spoiler_motion_intensity, 0, 4).start()
                effects["spoilermovement2"].periodic(118, self.spoiler_motion_intensity, 90, 4).start()
            else:
                effects.dispose("spoilermovement")
                effects.dispose("spoilermovement2")

        if tas > spd_thresh_low and spoiler > .1:
            # calculate insensity based on deployment percentage
            realtime_intensity = self.spoiler_buffet_intensity * spoiler * tas_intensity
            # logging.debug(f"PLAYING SPOILER RUMBLE | intensity: {realtime_intensity}, d-factor: {spoiler}, s-factor: {tas_intensity}")
            effects["spoilerbuffet1-1"].periodic(15, realtime_intensity, 0, 4).start()
            effects["spoilerbuffet1-2"].periodic(16, realtime_intensity, 0, 4).start()
            effects["spoilerbuffet2-1"].periodic(14, realtime_intensity, 90, 4).start()
            effects["spoilerbuffet2-2"].periodic(18, realtime_intensity, 90, 4).start()
        else:
            effects.dispose("spoilerbuffet1-1")
            effects.dispose("spoilerbuffet1-2")
            effects.dispose("spoilerbuffet2-1")
            effects.dispose("spoilerbuffet2-2")
    def _update_stick_position(self, telem_data):
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

            # upload effect parameters to stick
            self.spring.effect.setCondition(self.spring_x)
            self.spring.effect.setCondition(self.spring_y)
            # ensure effect is started
            self.spring.start()
            # logging.debug(f"Updated stick offset X:{offs_x}, Y:{offs_y}")
            # override DCS input and set our own values
            return f"LoSetCommand(2001, {y - offs_y})\n" \
                   f"LoSetCommand(2002, {x - offs_x})"

    def on_timeout(self): # override me
        pass

    def on_telemetry(self, data): # override me
        pass