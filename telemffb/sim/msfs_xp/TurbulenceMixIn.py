from typing import override

from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.util.TurbulenceModulator import TurbulenceModulator


class TurbulenceMixIn(AircraftEffectUtilsBase):
    """Mixin for MSFS/X-Plane atmospheric turbulence FFB effect."""

    # user parameters
    turbulence_effect_enable: bool = False
    turbulence_hpf_alpha: float = 0.0
    turbulence_smoothing_alpha: float = 0.0
    turbulence_sensitivity: float = 0.0
    turbulence_intensity: float = 0.0
    # end of user parameters

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.turbulence_modulator = TurbulenceModulator()

    def update_turbulence(self):
        if self.turbulence_effect_enable:
            force, dir = self.turbulence_modulator.update(
                self.telem_data,
                self.turbulence_hpf_alpha,
                self.turbulence_smoothing_alpha,
                self.turbulence_sensitivity,
                self.turbulence_intensity,
            )
            force = round(force, 4)
            self.effects["turbulence"].constant(force, dir).start()
        else:
            self.effects["turbulence"].destroy()

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        if self.is_joystick():
            self.update_turbulence()
