from typing import override
import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging
import math
from typing import Dict


class WindEffectMixIn(AircraftEffectUtilsBase):
    """Local mixin to encapsulate wind effect state and logic."""

    wind_effect_enabled: int = 0
    wind_effect_scaling: int = 0
    wind_effect_max_intensity: int = 0
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.__wind_hpf = utils.HighPassFilter(3)
        self.__wind_lpf = utils.LowPassFilter(15)

    def ac_update_wind_effect(self, telem_data):
        if not self.is_joystick():
            return
        if not self.wind_effect_enabled:
            self.effects.dispose("wnd")
            return

        wind = telem_data.get("Wind", (0, 0, 0))
        wnd = math.sqrt(wind[0] ** 2 + wind[1] ** 2 + wind[2] ** 2)

        v = self.__wind_hpf.update(wnd)
        v = self.__wind_lpf.update(v)
        v = utils.clamp(v, 0, self.wind_effect_max_intensity)
        v = utils.clamp(v * self.wind_effect_scaling, 0.0, 1.0)
        if v == 0:
            self.effects.dispose("wind")
            return
        logging.debug(f"Adding wind effect intensity:{v}")
        self.effects["wnd"].constant(v, utils.RandomDirectionModulator, 5).start()

    @override
    def on_telemetry(self, telem_data: Dict):
        super().on_telemetry(telem_data)
        self.ac_update_wind_effect(telem_data)