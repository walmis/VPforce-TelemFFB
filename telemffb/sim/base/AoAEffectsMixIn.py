"""AoA reduction force effect mixin.

Provides state and method for AoA reduction force effect which was
previously embedded in AircraftBase.
"""
import logging
import math
import telemffb.utils as utils
from telemffb.util.conversions import kmh2ms
from telemffb.sim.base.AircraftParamsMixIn import AircraftParamsMixIn
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class AoAEffectsMixIn(AdvancedSpringMixIn, AircraftParamsMixIn):
    """Local mixin for AoA effects (both basic CF and AoA-reduction).

    This mixin keeps AoA-related state and methods together inside
    `aircraft_base.py` to simplify refactoring and avoid import cycles.
    """

    aoa_effect_enabled: bool = False
    aoa_effect_gain: float = 1.0
    max_aoa_cf_force: float = 0.2  # CF force sent to device at %stall_aoa

    # AoA reduction (stall-protection) effect state
    aoa_reduction_effect_enabled = 0
    aoa_reduction_max_force = 0.0
    critical_aoa_start = 22
    critical_aoa_max = 25

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.smoother = utils.Smoother()

    def ac_update_aoa_reduction_force_effect(self, telem_data: BaseTelemetryData):
        """Apply stall-protection push-forward force as AoA approaches critical.

        Telemetry:
            Read: AoA - float (degrees); angle of attack; 8-sample running average
                        compared against critical_aoa_start/max thresholds
                  TAS - float (m/s, ≥ 0); true airspeed; effect suppressed below 10 m/s
        """
        if not self.aoa_reduction_effect_enabled:
            return
        if self._should_skip_joystick_effect():
            return
        if self._should_skip_airborne_effect(telem_data):
            self.effects.dispose("crit_aoa")
            return
        if self._should_skip_no_airspeed_effect(telem_data):
            self.effects.dispose("crit_aoa")
            return
        start_aoa = self.critical_aoa_start
        end_aoa = self.critical_aoa_max
        aoa = telem_data.AoA or 0
        tas = telem_data.TAS or 0
        avg_aoa = self.smoother.get_average("crit_aoa", aoa, sample_size=8)
        import telemffb.utils as utils

        if avg_aoa >= start_aoa and tas > 10:
            force_factor = round(utils.non_linear_scaling(avg_aoa, start_aoa, end_aoa, curvature=1.5), 4)
            force_factor = self.aoa_reduction_max_force * force_factor
            force_factor = utils.clamp(force_factor, 0.0, 1.0)
            logging.debug(f"AoA Reduction Effect:  AoA= {aoa} avg_AoA={avg_aoa}, force={force_factor}, max allowed force={self.aoa_reduction_max_force}")
            self.effects["crit_aoa"].constant(force_factor, 180).start()
        else:
            self.effects.dispose("crit_aoa")
        return

    def ac_update_aoa_effect(self, telem_data: BaseTelemetryData, minspeed=50*kmh2ms, maxspeed=140*kmh2ms):
        """Apply constant force proportional to AoA for aerodynamic feel on joystick.

        Telemetry:
            Read:    AoA      - float (degrees); angle of attack; positive = nose up;
                                 magnitude drives force, sign sets direction (0° or 180°)
                     TAS      - float (m/s, ≥ 0); true airspeed; speed_factor scales
                                 from 0 at minspeed to 1.0 at maxspeed
                     ACisFBW  - bool; True suppresses effect for fly-by-wire aircraft
            Written: aoa_pull (float, 0.0–max_aoa_cf_force; force magnitude sent to effect)
        """
        if not self.aoa_effect_enabled:
            return
        if not self.is_joystick():
            return
        from telemffb.SettingsManager import SpringModeEnum
        if self.spring_mode_is(SpringModeEnum.FBW) or telem_data.ACisFBW:
            return

        aoa = telem_data.AoA or 0
        tas = telem_data.TAS or 0
        local_stall_aoa = getattr(self, 'stall_aoa', 0)

        if aoa:
            aoa = float(aoa)
            speed_factor = utils.scale_clamp(tas, (minspeed, maxspeed), (0, 1.0))
            mag = utils.scale_clamp(abs(aoa), (0, local_stall_aoa), (0, self.max_aoa_cf_force))
            mag *= speed_factor
            dir = 0 if (aoa > 0) else 180

            telem_data.aoa_pull = mag
            logging.debug(f"AOA EFFECT:{mag}")
            self.effects["aoa"].constant(mag, dir).start()

            # effects.dispose("ab_rumble_2_2")

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_aoa_reduction_force_effect(telem_data)

        if not self.is_helicopter():
            # not helicopter
            vs0 = self.aircraft_vs_speed
            vne = self.aircraft_vne_speed
            # print(f"Got Vs0={vs0}, Vne={vne}")
            self.ac_update_aoa_effect(telem_data, minspeed=vs0, maxspeed=vne)