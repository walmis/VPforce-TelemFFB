"""TelemFFB effect levels: per-instance software scaling of every effect
TelemFFB sends to its device.

A Master level and one level per effect bucket (periodic, constant,
spring, damper, inertia, friction), each 0.0..1.0, plus a mute.  The
factor applied to an effect is ``master * bucket level``, or 0.0 while
its bucket is muted.  The spring adjuster is never scaled: its
coefficients are a gain multiplier on the other springs, not a force.

``HapticEffect`` (``ffb_rhino``) applies the factor at its parameter
sends and replays its last requests through ``reapply_levels()`` after a
change; nothing here touches a device.

One TelemFFB process drives one device, so one ``EffectLevels`` instance
(``levels``) serves the process.
"""

import math
from typing import Literal, Optional

LEVEL_NAMES = ("master", "periodic", "constant", "spring", "damper", "inertia", "friction")

#: Mute modes.  ``MUTE_KEEP_SPRING`` silences everything but the spring
#: bucket (spring and detent), which keeps its normal factor.
MUTE_OFF = "off"
MUTE_KEEP_SPRING = "keep_spring"
MUTE_ALL = "all"
MUTE_MODES = (MUTE_OFF, MUTE_KEEP_SPRING, MUTE_ALL)

MuteMode = Literal["off", "keep_spring", "all"]

#: Device-unit full scale of condition coefficients and saturations.
_FULL_SCALE = 4096


def _type_buckets() -> tuple[dict[int, str], int]:
    """Effect type -> bucket name, and the detent type.

    Resolved at call time: ``ffb_rhino`` defines the type constants and
    imports this module.  Types absent from the map (spring adjuster,
    ramp, custom) are never scaled.
    """
    from telemffb.hw import ffb_rhino as fr
    buckets = {t: "periodic" for t in fr.PERIODIC_EFFECTS}
    buckets.update({
        fr.EFFECT_CONSTANT: "constant",
        fr.EFFECT_SPRING: "spring",
        fr.EFFECT_DETENT: "spring",
        fr.EFFECT_DAMPER: "damper",
        fr.EFFECT_INERTIA: "inertia",
        fr.EFFECT_FRICTION: "friction",
    })
    return buckets, fr.EFFECT_DETENT


def _clamp_level(value: float) -> float:
    value = float(value)
    if math.isnan(value):
        raise ValueError("effect level must be a number")
    return min(1.0, max(0.0, value))


def _scale(value: int, factor: float) -> int:
    return int(round(value * factor))


def _scale_coefficient(value: int, factor: float) -> int:
    # clamped first, as the device handles clamp it
    return _scale(min(_FULL_SCALE, max(-_FULL_SCALE, value)), factor)


def _scale_saturation(value: int, factor: float) -> int:
    # 0 means "no cap" to the Rhino firmware and the DirectLink handle,
    # so a nonzero cap never rounds down to 0 while the force is nonzero
    if not value:
        return 0
    scaled = _scale(min(_FULL_SCALE, value), factor)
    return scaled if scaled or not factor else 1


class EffectLevels:
    """The level table and mute state, and the scaling they imply.

    Setters only update the table; callers replay live effects with
    ``HapticEffect.reapply_levels()``.  ``factor()`` is the per-frame
    query and costs one attribute check while every level is 1.0 and
    nothing is muted.
    """

    def __init__(self):
        self._levels: dict[str, float] = dict.fromkeys(LEVEL_NAMES, 1.0)
        self._mute: str = MUTE_OFF
        self._mute_suspended: bool = False
        self._factors: dict[int, float] = {}
        self._detent_type: Optional[int] = None
        self._unity: bool = True

    # --- state ---------------------------------------------------------

    @property
    def unity(self) -> bool:
        """True when every level is 1.0 and nothing is muted: no effect
        is scaled."""
        return self._unity

    @property
    def mute_mode(self) -> str:
        return self._mute

    def level(self, name: str) -> float:
        return self._levels[name]

    def snapshot(self) -> dict[str, float]:
        """A copy of the level table, keyed by ``LEVEL_NAMES``."""
        return dict(self._levels)

    def set_level(self, name: str, value: float) -> bool:
        """Set one level, clamped to 0.0..1.0.  Returns whether it changed.

        :raises ValueError: unknown name, or a NaN value.
        """
        return self.set_levels({name: value})

    def set_levels(self, values: dict[str, float]) -> bool:
        """Set several levels at once, each clamped to 0.0..1.0.
        Returns whether any changed.

        :raises ValueError: an unknown name or a NaN value; nothing is
            applied then.
        """
        new = dict(self._levels)
        for name, value in values.items():
            if name not in new:
                raise ValueError(f"unknown effect level {name!r}")
            new[name] = _clamp_level(value)
        if new == self._levels:
            return False
        self._levels = new
        self._rebuild()
        return True

    def set_mute(self, mode: MuteMode) -> bool:
        """Set the mute mode (one of ``MUTE_MODES``).  Returns whether it
        changed.

        :raises ValueError: unknown mode.
        """
        if mode not in MUTE_MODES:
            raise ValueError(f"unknown mute mode {mode!r}")
        if mode == self._mute:
            return False
        self._mute = mode
        self._rebuild()
        return True

    def set_mute_suspended(self, on: bool) -> bool:
        """While suspended, the factor ignores the mute and applies the
        levels alone; the effect preview plays through a mute this way.
        Returns whether it changed."""
        on = bool(on)
        if on == self._mute_suspended:
            return False
        self._mute_suspended = on
        self._rebuild()
        return True

    def reset(self) -> None:
        """Every level back to 1.0, mute off, mute not suspended."""
        self._unity = True
        self._levels = dict.fromkeys(LEVEL_NAMES, 1.0)
        self._mute = MUTE_OFF
        self._mute_suspended = False
        self._factors = {}

    def _rebuild(self) -> None:
        mute = MUTE_OFF if self._mute_suspended else self._mute
        unity = mute == MUTE_OFF and all(v == 1.0 for v in self._levels.values())
        if unity:
            self._unity = True
            self._factors = {}
            return
        buckets, self._detent_type = _type_buckets()
        master = self._levels["master"]
        factors = {}
        for effect_type, bucket in buckets.items():
            muted = (mute == MUTE_ALL
                     or (mute == MUTE_KEEP_SPRING and bucket != "spring"))
            factors[effect_type] = 0.0 if muted else master * self._levels[bucket]
        # the table is complete before the flag lets readers use it
        self._factors = factors
        self._unity = False

    # --- scaling -------------------------------------------------------

    def factor(self, effect_type: Optional[int]) -> float:
        """The scale for an effect type (an ``EFFECT_*`` constant).
        1.0 for a type that is never scaled."""
        if self._unity:
            return 1.0
        return self._factors.get(effect_type, 1.0)

    def periodic_for_device(self, magnitude: float, kwargs: dict, effect_type: int) -> tuple[float, dict]:
        """A periodic's magnitude and keyword arguments as sent.  The
        ``offset`` keyword (device units) is a force too and scales with
        the magnitude.  The caller's dict is never modified."""
        f = self.factor(effect_type)
        if f == 1.0:
            return magnitude, kwargs
        if "offset" in kwargs:
            kwargs = dict(kwargs)
            kwargs["offset"] = _scale(int(kwargs["offset"]), f)
        return magnitude * f, kwargs

    def magnitude_for_device(self, magnitude: float, effect_type: int) -> float:
        """A constant force's magnitude as sent."""
        f = self.factor(effect_type)
        return magnitude if f == 1.0 else magnitude * f

    def condition_for_device(self, cond, effect_type: int):
        """A condition as sent: the caller's own struct at factor 1.0,
        otherwise a scaled copy, so the caller's struct is never modified.

        Coefficients and saturations scale; ``cpOffset`` and ``deadBand``
        are positions and never do.  A detent scales only its peak
        (``positiveCoefficient``): its negative coefficient is the detent
        width and its saturations are gate positions.
        """
        f = self.factor(effect_type)
        if f == 1.0:
            return cond
        out = type(cond).from_buffer_copy(cond)
        out.positiveCoefficient = _scale_coefficient(cond.positiveCoefficient, f)
        if effect_type != self._detent_type:
            out.negativeCoefficient = _scale_coefficient(cond.negativeCoefficient, f)
            out.positiveSaturation = _scale_saturation(cond.positiveSaturation, f)
            out.negativeSaturation = _scale_saturation(cond.negativeSaturation, f)
        return out

    def envelope_for_device(self, envelope, effect_type: int):
        """An envelope as sent: its attack and fade levels are absolute
        forces (device units) and scale with the effect's own type.  The
        caller's struct at factor 1.0, otherwise a scaled copy."""
        f = self.factor(effect_type)
        if f == 1.0:
            return envelope
        out = type(envelope).from_buffer_copy(envelope)
        out.attackFromForce = _scale(envelope.attackFromForce, f)
        out.decayToForce = _scale(envelope.decayToForce, f)
        return out


def remember_condition(store: dict, cond) -> None:
    """Keep an unscaled copy of ``cond`` in ``store``, keyed by its axis
    (``parameterBlockOffset``).  A copy, because callers reuse and mutate
    their condition structs between frames."""
    if store.get(cond.parameterBlockOffset) is not cond:
        store[cond.parameterBlockOffset] = type(cond).from_buffer_copy(cond)


#: The process-wide level table.
levels = EffectLevels()
