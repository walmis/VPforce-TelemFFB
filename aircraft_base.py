import logging
import time

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

    def has_changed(self, item : str, delta_ms = 0) -> bool:
        prev_val, tm = self._changes.get(item, (None, 0))
        new_val = self._telem_data.get(item)
        
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

    def anything_has_changed(self, item : str, value) -> bool:
        # track if any parameter, given as key "item" has changed between two consecutive calls of the function
        prev_val = self._changes.get(item)
        new_val = value
        self._changes[item] = new_val
        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val,new_val)
        return False
    
    def on_timeout(self): # override me
        pass

    def on_telemetry(self, data): # override me
        pass