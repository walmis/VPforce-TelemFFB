import time
from typing import Optional

import telemffb.utils as utils
from telemffb.sim.base.FFBForcesMixIn import FFBForcesMixIn
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.utils.TransformExpr import TransformExpr

class HydraulicLossMixIn(FFBForcesMixIn):
    """Mixin to handle hydraulic-loss related configuration, runtime state and effects."""
    enable_hydraulic_loss_effect: bool = False
    hydraulic_loss_threshold: float = 0.95
    hydraulic_loss_damper: float = 1
    hydraulic_loss_friction: float = 1
    # MSFS: subscribe HydSys to a variable the user names, and turn its raw
    # value into health with the user's transform
    hydraulic_source_var_enabled: bool = False
    hydraulic_source_var: str = ""
    hydraulic_source_transform: str = ""

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
        # (transform text, parsed TransformExpr or the error it raised)
        self._hyd_transform = ("", None)

    def _hydraulic_source_active(self) -> bool:
        return bool(self.enable_hydraulic_loss_effect and self.hydraulic_source_var_enabled
                    and self.hydraulic_source_var)

    def _hydraulic_source_transform(self):
        """The parsed transform, None when blank; raises ValueError when it does not parse."""
        text = self.hydraulic_source_transform
        text = "" if text is None else str(text).strip()
        if text != self._hyd_transform[0]:
            try:
                parsed = TransformExpr(text) if text else None
            except ValueError as e:
                parsed = e
            self._hyd_transform = (text, parsed)
        parsed = self._hyd_transform[1]
        if isinstance(parsed, ValueError):
            raise parsed
        return parsed

    def _custom_hydraulic_health(self, telem_data: BaseTelemetryData) -> Optional[float]:
        """HydSys through the user's transform, as health 0..1; None until it has a value.

        Telemetry:
            Read:    HydSys - float; the variable named in hydraulic_source_var, raw
        """
        raw = telem_data.get("HydSys", None)
        if raw is None:
            return None
        try:
            transform = self._hydraulic_source_transform()
        except ValueError as e:
            self.flag_error(f"Hydraulic loss: the transform '{self.hydraulic_source_transform}' "
                            f"is not valid ({e}). Use x for the variable's value, for example x/3000")
            return None
        try:
            value = float(raw)
            if transform is not None:
                value = float(transform.apply(value))
        except (ArithmeticError, TypeError, ValueError):
            return None
        return utils.clamp(value, 0.0, 1.0)

    def _hydraulic_health(self, telem_data: BaseTelemetryData) -> Optional[float]:
        """HydSys as health, 0 (no hydraulics) to 1 (normal); None when absent.

        With a custom hydraulic variable set, HydSys carries that variable's raw
        value and the user's transform turns it into health.

        Telemetry:
            Read:    HydSys   - Union[bool, int, float, List[float]]; hydraulic system state.
                                 MSFS: integrity percent (0.0–1.0); bool: True=OK/False=failed;
                                 list: per-system values, the best one counts; "n/a" = not subscribed.
                     HydPress - Union[float, List[float]] (psi, ≥ 0); hydraulic pressure.
                                 Default .get() value is 1 (non-zero) when absent.
                                 Only used in DCS bool path to disambiguate HydSys=True
                                 with zero pressure (i.e. fluid present but no pressure).
        """
        if self._hydraulic_source_active():
            return self._custom_hydraulic_health(telem_data)

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
            Written: _hyd_health      (debug: hydraulic health this frame, 0.0–1.0, before the ramp)
                     _hyd_factor      (debug: current hydraulic factor 0.0–1.0)
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
        telem_data._hyd_health = health
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

    def ac_update_hydraulic_and_ffb_forces(self, telem_data: BaseTelemetryData):
        """The hydraulic loss effect, then the FFB overrides it shares damper
        and friction with - in that order, because the loss effect decides
        each frame who owns the two.  Below the threshold it plays them and
        the overrides leave them alone; above it the overrides play their
        normal values.  Its own method so the effect preview can play the
        same pair the live loop does, normal feel included.

        Telemetry:
            Read:    HydSys, HydPress - see _hydraulic_health
        """
        hyd_loss = self.ac_update_hydraulic_loss_effect(telem_data)
        self.ac_update_ffb_forces(telem_data, skip=("damper", "friction") if hyd_loss else ())

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_hydraulic_and_ffb_forces(telem_data)
