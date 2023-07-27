import logging
import time
import utils
from typing import List, Dict
# from utils import clamp, HighPassFilter, Derivative, Dispenser

from ffb_rhino import HapticEffect, FFBReport_SetCondition
# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
effects : Dict[str, HapticEffect] = utils.Dispenser(HapticEffect)

class AircraftBase(object):
    def __init__(self, name : str, **kwargs):
        self._name = name
        self._changes = {}
        self._change_counter = {}
        self._telem_data = None

    def apply_settings(self, settings_dict):
        for k,v in settings_dict.items():
            if k in ["type"]: continue
            if getattr(self, k, None) is None:
                logging.warn(f"Unknown parameter {k}")
                continue
            logging.info(f"set {k} = {v}")
            setattr(self, k, v)

    def has_changed(self, item : str, delta_ms = 0, data = None) -> bool:
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
            return (prev_val,new_val)
        
        if time.perf_counter() - tm < delta_ms/1000.0:
            return True

        return False

    def anything_has_changed(self, item : str, value, delta_ms = 0) -> bool:
        """track if any parameter, given as key "item" has changed between two consecutive calls of the function
        delta_ms can be used to smooth the effects of telemetry which does not update regularly but is still "moving"
        a positive delta_ms value will allow the data to remain unchanged for that period of time before returning false
        #logging.debug(f"Value: {value}")"""

        prev_val, tm, changed_yet = self._changes.get(item, (None, 0, 0))
        new_val = value
        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        # make sure we do not return true until the key has changed at least once (after init)
        if prev_val == None and not changed_yet:
            self._changes[item] = (new_val, tm, 0)
            prev_val = new_val

       # logging.debug(f"Prev: {prev_val}, New: {new_val}, TM: {tm}")

        if prev_val != new_val:
            self._changes[item] = (new_val, time.perf_counter(), 1)

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return True

        if time.perf_counter() - tm < delta_ms/1000.0:
            return True

        return False

    def _update_flaps(self, flapspos):
        #flapspos = data.get("Flaps")
        logging.debug(f"Flaps:{flapspos}")
        if self.anything_has_changed("Flaps", flapspos, delta_ms = 50):
            # logging.debug(f"Flaps Pos: {flapspos}")
            effects["flapsmovement"].periodic(180, self.flaps_motion_intensity, 0, 3).start()
        #     # effects["flapsmovement2"].periodic(150, self.flaps_motion_intensity, 45, 3, phase=120).start()
        else:
            effects.dispose("flapsmovement")
            #logging.debug("disposing flaps effect")
        #     # effects.dispose("flapsmovement2")
    def on_timeout(self): # override me
        pass

    def on_telemetry(self, data): # override me
        pass