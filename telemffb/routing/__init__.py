"""Generalised effect routing.

Exposes the device-agnostic routing primitives:

- ``RouteLayer`` / ``EffectRoute``: data model for "which effect goes to which
  device, at what gain, with what direction", a superset of the shaker-only
  ``ffb_shaker.Layer``.
- ``EffectRouter``: resolves an effect name + the current device inventory
  into the concrete list of layers that this process should play.

The router runs *per-process* — every device child loads the same
``effect_routes_user.json`` and filters locally for its own ``device_id`` /
type / position. There is intentionally no master-to-slave effect forwarding;
each instance reads the shared telemetry and decides on its own.
"""

from .effect_route import (
    DirectionPolicy,
    EffectRoute,
    EffectRoutesPack,
    RouteLayer,
    layer_targets_device,
    parse_target_selector,
)
from .router import EffectRouter

__all__ = [
    "DirectionPolicy",
    "EffectRoute",
    "EffectRoutesPack",
    "EffectRouter",
    "RouteLayer",
    "layer_targets_device",
    "parse_target_selector",
]
