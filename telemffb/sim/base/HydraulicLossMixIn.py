import time
from typing import Optional

import telemffb.utils as utils
from telemffb.sim.base.FFBForcesMixIn import FFBForcesMixIn
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class HydraulicLossMixIn(FFBForcesMixIn):
    """Mixin to handle hydraulic-loss related configuration, runtime state and effects."""
    enable_hydraulic_loss_effect: bool = False
    hydraulic_loss_threshold: float = 0.95
    hydraulic_loss_damper: float = 1
    hydraulic_loss_friction: float = 1

    #: Seconds the hydraulic factor takes to travel the whole 0..1 range.  A
    #: switch that cuts the hydraulics ramps over this time; a pressure that
    #: decays more slowly is followed as it is.
    HYDRAULIC_RAMP_S = 2.5

    def __init__(self, *args, **kwargs):
        # cooperative init for mixin ordering
        super().__init__(*args, **kwargs)
        # per-instance runtime state; None until the first reading, which is
        # taken as it is rather than ramped to
        self.hydraulic_factor: Optional[float] = None
        self._hyd_factor_time = 0.0

    def _hydraulic_health(self, telem_data: BaseTelemetryData) -> Optional[float]:
        """HydSys as health, 0 (no hydraulics) to 1 (normal); None when absent.

        Telemetry:
            Read:    HydSys   - Union[bool, int, float, List[float]]; hydraulic system state.
                                 MSFS: integrity percent (0.0–1.0); bool: True=OK/False=failed;
                                 list: per-system values, the best one counts; "n/a" = not subscribed.
                     HydPress - Union[float, List[float]] (psi, ≥ 0); hydraulic pressure.
                                 Default .get() value is 1 (non-zero) when absent.
                                 Only used in DCS bool path to disambiguate HydSys=True
                                 with zero pressure (i.e. fluid present but no pressure).
        """
        hydraulic_sys = telem_data.get('HydSys', "n/a")
        if hydraulic_sys == 'n/a' or hydraulic_sys is None:
            return None

        if isinstance(hydraulic_sys, list):
            return utils.clamp(float(max(hydraulic_sys)), 0.0, 1.0)

        if isinstance(hydraulic_sys, int) and hydraulic_sys in (0, 1):
            hydraulic_sys = bool(hydraulic_sys)

        if isinstance(hydraulic_sys, bool):
            hydraulic_pressure = telem_data.get('HydPress', 1)  # non-zero default: keep .get()
            if isinstance(hydraulic_pressure, list):
                hydraulic_pressure = max(hydraulic_pressure)
            if self._sim_is_dcs() and hydraulic_sys and hydraulic_pressure == 0:
                hydraulic_sys = False
            return 1.0 if hydraulic_sys else 0.0

        return utils.clamp(float(hydraulic_sys), 0.0, 1.0)

    def _ramp_hydraulic_factor(self, health: float) -> float:
        """Move hydraulic_factor toward health no faster than HYDRAULIC_RAMP_S allows."""
        now = time.perf_counter()
        if self.hydraulic_factor is None:
            self.hydraulic_factor = health
        else:
            max_step = (now - self._hyd_factor_time) / self.HYDRAULIC_RAMP_S
            self.hydraulic_factor += utils.clamp(health - self.hydraulic_factor, -max_step, max_step)
        self._hyd_factor_time = now
        return self.hydraulic_factor

    def _stop_hydraulic_loss_effects(self):
        self.effects["hyd_loss_damper"].destroy()
        self.effects["hyd_loss_friction"].destroy()

    def _reset_hydraulic_loss(self):
        """Stop the effect if it has run, and forget the factor so the next reading is taken as it is."""
        if self.hydraulic_factor is None:
            return
        self.hydraulic_factor = None
        self._stop_hydraulic_loss_effects()

    def ac_update_hydraulic_loss_effect(self, telem_data: BaseTelemetryData) -> bool:
        """Raise damper and friction as the hydraulics fail.

        Below the threshold, damper and friction scale from the override values
        at the threshold to the loss values at 0, so both overrides must be on.
        While that happens this effect owns damper and friction; inertia stays
        with the FFB overrides.

        Returns True while the effect owns damper and friction.

        Telemetry:
            Read:    HydSys, HydPress - see _hydraulic_health
            Written: _hyd_factor      (debug: current hydraulic factor 0.0–1.0)
        """
        if not self.enable_hydraulic_loss_effect:
            self._reset_hydraulic_loss()
            return False

        if not self.enable_damper_ovd or not self.enable_friction_ovd:
            self.flag_error("Low Hydraulic Pressure Effect is enabled but needs the Damper Override and "
                            "Friction Override. Enable both under Basic FFB Effects and set their normal values.")
            self._reset_hydraulic_loss()
            return False

        health = self._hydraulic_health(telem_data)
        if health is None:
            self._reset_hydraulic_loss()
            return False

        factor = self._ramp_hydraulic_factor(health)
        telem_data._hyd_factor = factor

        if factor >= self.hydraulic_loss_threshold:
            self._stop_hydraulic_loss_effects()
            return False

        damper = utils.clamp(utils.scale(factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_damper, self.damper_force)), 0.0, 1.0)
        friction = utils.clamp(utils.scale(factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_friction, self.friction_force)), 0.0, 1.0)

        self.effects["damper"].destroy()
        self.effects["friction"].destroy()

        if not self.effects["hyd_loss_damper"].started or self.anything_has_changed('_hyd_loss_damper', damper):
            self.effects["hyd_loss_damper"].damper(damper, damper).start()
        if not self.effects["hyd_loss_friction"].started or self.anything_has_changed('_hyd_loss_friction', friction):
            self.effects["hyd_loss_friction"].friction(friction, friction).start()

        return True

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        hyd_loss = self.ac_update_hydraulic_loss_effect(telem_data)
        self.ac_update_ffb_forces(telem_data, skip=("damper", "friction") if hyd_loss else ())
