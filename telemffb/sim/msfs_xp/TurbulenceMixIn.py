import math
from typing import override

from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.util.TurbulenceModulator import TurbulenceForces, TurbulenceModulator


class TurbulenceMixIn(AircraftEffectUtilsBase):
    """Mixin for MSFS/X-Plane atmospheric turbulence FFB effect.

    Applies turbulence forces to both joystick (pitch + roll) and pedals (yaw).
    """

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
            try:
                # MSFS / X-Plane body frame: [0]=lateral, [1]=vertical, [2]=longitudinal
                lateral_wind = self.telem_data['RelWind'][0]
                vertical_wind = self.telem_data['RelWind'][1]
                longitudinal_wind = self.telem_data['RelWind'][2]
            except (KeyError, IndexError, TypeError):
                return

            forces = self.turbulence_modulator.update(
                lateral_wind,
                vertical_wind,
                longitudinal_wind,
                hpf_alpha=self.turbulence_hpf_alpha,
                smoothing_alpha=self.turbulence_smoothing_alpha,
                sensitivity=self.turbulence_sensitivity,
                intensity=self.turbulence_intensity,
            )

            if self.is_joystick():
                self._apply_joystick_turbulence(forces)
            elif self.is_pedals():
                self._apply_pedal_turbulence(forces)
        else:
            self.effects["turbulence"].destroy()

    def _apply_joystick_turbulence(self, forces: TurbulenceForces):
        """Convert pitch/roll forces to constant-force magnitude + direction.

        Device direction convention (SDL HAPTIC_POLAR / VPforce Rhino):
          0° = force from North = stick pulls backward  (pitch up)
         90° = force from East  = stick pushes left     (roll left)
        180° = force from South = stick pushes forward   (pitch down)
        270° = force from West  = stick pushes right     (roll right)
        """
        magnitude = round(math.sqrt(forces.pitch ** 2 + forces.roll ** 2), 4)
        if magnitude > 0.0001:
            # atan2(-roll, pitch) maps:
            #   pitch>0 → 0° (pull back),  roll>0 → 270° (right)
            direction = int(math.degrees(math.atan2(-forces.roll, forces.pitch)) % 360)
        else:
            direction = 0
        self.effects["turbulence"].constant(magnitude, direction).start()

    def _apply_pedal_turbulence(self, forces: TurbulenceForces):
        """Apply yaw turbulence force to rudder pedals.

        270° = push right pedal, 90° = push left pedal.
        """
        magnitude = round(abs(forces.yaw), 4)
        if magnitude > 0.0001:
            direction = 270 if forces.yaw > 0 else 90
            self.effects["turbulence"].constant(magnitude, direction).start()
        else:
            self.effects["turbulence"].stop()

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        # Turbulence applies to joystick (pitch/roll) and pedals (yaw)
        if self.is_joystick() or self.is_pedals():
            self.update_turbulence()
