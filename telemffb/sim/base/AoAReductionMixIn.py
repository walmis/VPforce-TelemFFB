"""AoA reduction force effect mixin.

Provides state and method for AoA reduction force effect which was
previously embedded in AircraftBase.
"""
import logging
import math
import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


class AoAReductionMixIn(AircraftEffectUtilsBase):
    """Mixin that encapsulates AoA-reduction force behavior.

    Expects the host class to provide:
      - self._should_skip_joystick_effect()
      - self._should_skip_airborne_effect(telem_data)
      - self._should_skip_no_airspeed_effect(telem_data)
      - self.smoother (with get_average)
      - self.effects (effect container with dispose and dict access)
    """

    def __init__(self, *args, **kwargs):
        # allow cooperative multiple-inheritance init
        super().__init__(*args, **kwargs)

        # AoA reduction effect settings / state
        self.aoa_reduction_effect_enabled = 0
        self.aoa_reduction_max_force = 0.0
        self.critical_aoa_start = 22
        self.critical_aoa_max = 25
        self.smoother = utils.Smoother()

    def ac_update_aoa_reduction_force_effect(self, telem_data):
        """Generates an AoA-reduction force when AoA crosses critical thresholds.

        This logic was moved out of AircraftBase to keep AoA-specific state
        and behavior self-contained.
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
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)
        avg_aoa = self.smoother.get_average("crit_aoa", aoa, sample_size=8)
        # local import to avoid circular import during module import

        if avg_aoa >= start_aoa and tas > 10:
            force_factor = round(utils.non_linear_scaling(avg_aoa, start_aoa, end_aoa, curvature=1.5), 4)
            force_factor = self.aoa_reduction_max_force * force_factor
            force_factor = utils.clamp(force_factor, 0.0, 1.0)
            logging.debug(f"AoA Reduction Effect:  AoA= {aoa} avg_AoA={avg_aoa}, force={force_factor}, max allowed force={self.aoa_reduction_max_force}")
            self.effects["crit_aoa"].constant(force_factor, 180).start()
        else:
            self.effects.dispose("crit_aoa")
        return
