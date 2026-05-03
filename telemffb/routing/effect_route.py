"""Data model for the generalised effect-routing matrix.

A ``RouteLayer`` is a superset of the legacy ``ffb_shaker.Layer``:

- ``target`` replaces the old three-valued ``route`` ("shaker" | "stick" |
  "both"). It is a selector string of the form
    * ``"id:<device_id>"``    -> a specific device by stable slug
    * ``"type:<device_type>"`` -> any device of that type (joystick, pedals, …)
    * ``"pos:<position_tag>"`` -> any device with that position tag
    * ``"both"``               -> legacy alias: matches stick and shaker
- ``enabled`` lets the user mute a single layer without deleting it.
- ``direction_policy`` + ``direction_value`` decouple direction from the
  hardcoded constants in ``aircraft_base.py``.

A v1/v2/v3 shaker layer (with ``route``) is converted on load to the
equivalent ``RouteLayer`` by ``RouteLayer.from_legacy``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


# --- selector helpers ----------------------------------------------------

_LEGACY_TARGETS = {
    "shaker": "type:shaker",
    "stick": "type:joystick",
    "both": "both",
}


def parse_target_selector(target: str) -> tuple[str, str]:
    """Split ``"<kind>:<value>"`` into ``(kind, value)``.

    ``"both"`` maps to ``("both", "")``. Unknown shapes default to
    ``("id", target)`` so that bare device ids still work.
    """
    if target == "both":
        return ("both", "")
    if ":" not in target:
        return ("id", target)
    kind, _, value = target.partition(":")
    return (kind, value)


def layer_targets_device(
    target: str,
    *,
    device_id: str,
    device_type: str,
    device_positions: Iterable[str] = (),
) -> bool:
    """Return True iff a layer with ``target`` should play on this device."""
    kind, value = parse_target_selector(target)
    if kind == "both":
        return device_type in ("joystick", "shaker")
    if kind == "id":
        return value == device_id
    if kind == "type":
        return value == device_type
    if kind == "pos":
        return value in set(device_positions)
    return False


# --- direction policy ----------------------------------------------------

class DirectionPolicy:
    """String-enum of supported direction policies.

    A class-with-constants (rather than ``enum.Enum``) keeps round-trip JSON
    serialisation a no-op — values are stored verbatim as strings.
    """

    INHERIT = "inherit"          # use the direction from the call site (legacy default)
    AUTO = "auto"                # router picks based on device position
    FIXED = "fixed"              # use direction_value as-is
    FROM_TELEMETRY = "from_telemetry"  # not implemented in P1; placeholder

    ALL = (INHERIT, AUTO, FIXED, FROM_TELEMETRY)


# --- core dataclasses ----------------------------------------------------

@dataclass
class RouteLayer:
    """One layer of an effect's routing decision.

    Field shapes mirror ``ffb_shaker.Layer`` for the audio-side parameters so
    that the migration is field-compatible. New fields default to None /
    defaults that preserve the legacy behaviour.
    """

    target: str = "type:shaker"
    enabled: bool = True
    gain: float = 1.0
    freq_factor: float = 1.0
    osc_type: str = "sine"      # "sine" | "impulse" | "bandpass_noise" | "passthrough"

    # bandpass_noise only:
    center_hz: Optional[float] = None
    bandwidth_hz: Optional[float] = None

    # impulse only (per-layer override of the active ShakerProfile):
    attack_ms: Optional[float] = None
    decay_ms: Optional[float] = None

    # direction control (added in v4):
    direction_policy: str = DirectionPolicy.INHERIT
    direction_value: Optional[float] = None

    # ---- conversions ----

    @classmethod
    def from_legacy(cls, raw: dict) -> "RouteLayer":
        """Convert a v1/v2/v3 shaker ``Layer`` dict (with ``route``) to a RouteLayer.

        Unknown / malformed routes fall back to ``"both"``.
        """
        data = dict(raw)
        legacy_route = data.pop("route", None)
        if legacy_route is not None:
            data["target"] = _LEGACY_TARGETS.get(str(legacy_route).lower(), "both")
        # Reject keys we don't know about so a typo doesn't silently no-op.
        known = {f for f in cls.__dataclass_fields__}
        for k in list(data.keys()):
            if k not in known:
                logger.warning("RouteLayer.from_legacy: dropping unknown key %r", k)
                data.pop(k)
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EffectRoute:
    """All layers that fire when an effect with ``name`` is triggered."""

    name: str
    layers: List[RouteLayer] = field(default_factory=list)

    def layers_for(
        self,
        *,
        device_id: str,
        device_type: str,
        device_positions: Iterable[str] = (),
    ) -> List[RouteLayer]:
        """Subset of layers whose ``target`` matches this device."""
        positions = set(device_positions)
        return [
            l for l in self.layers
            if l.enabled and layer_targets_device(
                l.target,
                device_id=device_id,
                device_type=device_type,
                device_positions=positions,
            )
        ]


@dataclass
class EffectRoutesPack:
    """A loaded routes file plus metadata.

    ``aircraft_class_overrides`` is a sparse map ``class_name -> {effect: route}``
    that patches the global routes when an aircraft of that class is active.
    """

    version: int = 4
    routes: dict[str, EffectRoute] = field(default_factory=dict)
    aircraft_class_overrides: dict[str, dict[str, EffectRoute]] = field(default_factory=dict)

    def merge_class_override(self, class_name: Optional[str]) -> dict[str, EffectRoute]:
        """Return the effective routes after applying the class patch (if any).

        Patch semantics: if a class override defines ``routes[name]``, it
        wholesale replaces the global ``routes[name]``. There is no per-layer
        merge — keep it simple and predictable.
        """
        if not class_name:
            return dict(self.routes)
        patch = self.aircraft_class_overrides.get(class_name, {})
        if not patch:
            return dict(self.routes)
        merged = dict(self.routes)
        merged.update(patch)
        return merged
