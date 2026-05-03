"""``EffectRouter`` — central routing decision for effect dispatch.

Loading order:
1. ``effect_routes_default.json`` (bundled, read-only) — base.
2. ``effect_routes_user.json`` (in userconfig dir) — user overrides; replaces
   matching effects wholesale.
3. Active aircraft class is supplied per ``resolve()`` call to apply the
   class-level patch, if any.

Migration: a v1/v2/v3 ``shaker_effects.json`` (with the legacy ``route``
field) is auto-converted on load via ``RouteLayer.from_legacy``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Iterable, List, Optional

from .effect_route import (
    DirectionPolicy,
    EffectRoute,
    EffectRoutesPack,
    RouteLayer,
)

logger = logging.getLogger(__name__)

# JSON schema versioning. v1-v3 are the legacy shaker_effects.json schemas
# (with ``route``); v4 is the generalised routes file (with ``target``).
SCHEMA_VERSION = 4
LEGACY_VERSIONS = (1, 2, 3)


def _read_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to read JSON from %s", path)
        return None


def _route_from_dict(name: str, raw: dict, *, file_version: Optional[int]) -> EffectRoute:
    """Construct an EffectRoute from a single ``effects[name]`` entry.

    For v1-v3 files (legacy shaker layers) ``RouteLayer.from_legacy`` does
    the field rename. For v4+ we instantiate ``RouteLayer`` directly.
    """
    layers_raw = raw.get("layers", [])
    layers: List[RouteLayer] = []
    is_legacy = file_version in LEGACY_VERSIONS or any("route" in l for l in layers_raw)
    for ld in layers_raw:
        if not isinstance(ld, dict):
            continue
        try:
            if is_legacy:
                layers.append(RouteLayer.from_legacy(ld))
            else:
                # Reject unknown keys to catch typos.
                known = {f for f in RouteLayer.__dataclass_fields__}
                clean = {k: v for k, v in ld.items() if k in known}
                layers.append(RouteLayer(**clean))
        except TypeError:
            logger.exception("Bad layer in effect %r; skipping", name)
    return EffectRoute(name=name, layers=layers)


def load_routes_pack(path: str) -> Optional[EffectRoutesPack]:
    """Load an effect_routes JSON file. Returns None if unreadable."""
    data = _read_json(path)
    if data is None:
        return None
    file_version = data.get("version")
    routes_raw = data.get("effects", {}) or {}
    routes = {
        name: _route_from_dict(name, entry, file_version=file_version)
        for name, entry in routes_raw.items()
        if isinstance(entry, dict)
    }
    overrides_raw = data.get("aircraft_class_overrides", {}) or {}
    overrides: dict[str, dict[str, EffectRoute]] = {}
    for class_name, class_routes in overrides_raw.items():
        if not isinstance(class_routes, dict):
            continue
        overrides[class_name] = {
            name: _route_from_dict(name, entry, file_version=file_version)
            for name, entry in class_routes.items()
            if isinstance(entry, dict)
        }
    return EffectRoutesPack(
        version=file_version if isinstance(file_version, int) else SCHEMA_VERSION,
        routes=routes,
        aircraft_class_overrides=overrides,
    )


class EffectRouter:
    """Owns the merged routes pack + the device inventory.

    A single instance is held per process. Aircraft modules don't talk to
    the router directly — the ``ffb_router`` haptic-effect facade calls
    ``resolve()`` on every ``.start()``.
    """

    def __init__(
        self,
        *,
        defaults: Optional[EffectRoutesPack] = None,
        user_overrides: Optional[EffectRoutesPack] = None,
    ) -> None:
        self._defaults = defaults or EffectRoutesPack()
        self._user = user_overrides or EffectRoutesPack()
        self._aircraft_class: Optional[str] = None

    # --- mutation API ---

    def set_defaults(self, pack: EffectRoutesPack) -> None:
        self._defaults = pack

    def set_user_overrides(self, pack: EffectRoutesPack) -> None:
        self._user = pack

    def set_aircraft_class(self, class_name: Optional[str]) -> None:
        """Called by the telemetry pipeline when a new aircraft loads."""
        self._aircraft_class = class_name

    # --- query API ---

    def effective_routes(self, aircraft_class: Optional[str] = None) -> dict[str, EffectRoute]:
        """Resolve defaults <- user overrides <- class patch.

        Per-effect granularity: if ``user`` defines an effect, it replaces the
        default wholesale. The class patch is applied last and replaces both.
        """
        cls = aircraft_class if aircraft_class is not None else self._aircraft_class
        merged: dict[str, EffectRoute] = {}
        merged.update(self._defaults.merge_class_override(cls))
        # User overrides take precedence over defaults.
        for name, route in self._user.routes.items():
            merged[name] = route
        # User-specified class patch wins over default class patch.
        user_patch = self._user.aircraft_class_overrides.get(cls or "", {})
        for name, route in user_patch.items():
            merged[name] = route
        return merged

    def resolve(
        self,
        effect_name: str,
        *,
        device_id: str,
        device_type: str,
        device_positions: Iterable[str] = (),
        aircraft_class: Optional[str] = None,
    ) -> List[RouteLayer]:
        """Layers that should fire on this device for this effect.

        Returns ``[]`` if the effect has no route (= silently dropped on this
        device, same semantic as the legacy SHAKER_EFFECT_WHITELIST miss).
        """
        routes = self.effective_routes(aircraft_class=aircraft_class)
        route = routes.get(effect_name)
        if route is None:
            return []
        return route.layers_for(
            device_id=device_id,
            device_type=device_type,
            device_positions=device_positions,
        )

    def is_effect_known(self, effect_name: str, *, aircraft_class: Optional[str] = None) -> bool:
        return effect_name in self.effective_routes(aircraft_class=aircraft_class)

    def known_effect_names(self, *, aircraft_class: Optional[str] = None) -> set[str]:
        return set(self.effective_routes(aircraft_class=aircraft_class).keys())

    # --- direction helpers (used by ffb_router backend) ---

    @staticmethod
    def resolved_direction(
        layer: RouteLayer,
        *,
        call_site_direction: float,
        device_positions: Iterable[str] = (),
    ) -> float:
        """Apply the layer's direction policy.

        ``inherit`` -> call_site_direction (legacy default)
        ``fixed``   -> layer.direction_value (or call_site if missing)
        ``auto``    -> position-derived heuristic; falls back to call_site
        ``from_telemetry`` -> not implemented yet, falls back to call_site
        """
        policy = layer.direction_policy
        if policy == DirectionPolicy.FIXED and layer.direction_value is not None:
            return float(layer.direction_value)
        if policy == DirectionPolicy.AUTO:
            return _auto_direction_for(device_positions, call_site_direction)
        return call_site_direction


# Position-tag heuristics for ``DirectionPolicy.AUTO``. Conservative: only
# the most common cases ship a default, everything else falls back to the
# call-site direction (i.e. behaves like ``INHERIT``).
def _auto_direction_for(positions: Iterable[str], fallback: float) -> float:
    pset = set(positions)
    if "front" in pset:
        return 0.0     # forward push: vibrate along the seat-to-rudder axis
    if "rear" in pset:
        return 180.0
    if "left" in pset and "right" not in pset:
        return 270.0
    if "right" in pset and "left" not in pset:
        return 90.0
    return fallback
