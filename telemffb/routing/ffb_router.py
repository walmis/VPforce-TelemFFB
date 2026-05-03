"""``ffb_router`` — third HapticEffect backend that consults ``EffectRouter``.

This sits between the aircraft modules and the underlying Rhino device:

- Aircraft code calls the unchanged ``effects['name'].periodic(...).start()``.
- This facade subclasses ``ffb_rhino.HapticEffect``. On every ``.start()`` we
  ask the router whether *this* device should play *this* effect (and with
  what gain / direction). If no layer matches, the call becomes a no-op.

Design notes
------------
- This backend is **only** wired up for FFB devices (joystick / pedals /
  collective / trimwheel / rudder). The shaker child still uses the
  ``ffb_shaker`` facade — it has its own per-effect layer system with audio
  mixing that this router does not replace.
- For Phase 2 we apply a single resolved layer per effect+device (highest
  gain wins on tie). True per-device multi-layer mixing on FFB slots is
  out of scope; Rhino has only one ``_h_effect`` slot per ``HapticEffect``
  instance and overlapping layers would need a slot-fan-out we don't need
  yet for the planned setups.
- ``physics()`` and ``fire_impulse()`` are passed through to the Rhino
  fallback implementations after the same routing gate. Force-only effects
  (``spring`` / ``damper`` / ``inertia`` / ``friction`` / ``setCondition``)
  bypass routing entirely — they're not "effects" in the routable sense.
"""

from __future__ import annotations

import logging
from typing import Optional

from .effect_route import RouteLayer
from .router import EffectRouter

logger = logging.getLogger(__name__)

# Lazy hardware import — keeps the pure routing helpers (init_router,
# _pick_layer, _resolve_for_self, is_initialised) usable in environments
# without usb1/PyQt6 (CI sandboxes, dataset-only tests). The HapticEffect
# class is built on first attribute access via __getattr__.
_HAPTIC_EFFECT_CLS = None
_FFB_REPORT_SET_CONDITION_CLS = None


# Module-level state populated from main.py at startup. Kept as module-level
# (not class-level) so the ``HapticEffect`` symbol mirrors the shape of
# ffb_rhino.HapticEffect for the aircraft_base.use_router_backend() swap.
_router: Optional[EffectRouter] = None
_self_device_id: str = ""
_self_device_type: str = ""
_self_device_positions: tuple = ()


def init_router(
    router: EffectRouter,
    *,
    device_id: str,
    device_type: str,
    device_positions=(),
) -> None:
    """Wire the router and the self-device identity for this process.

    Called from main.py after ``EffectRouter`` is built and the inventory is
    resolved. Calling again replaces the prior config.
    """
    global _router, _self_device_id, _self_device_type, _self_device_positions
    _router = router
    _self_device_id = device_id
    _self_device_type = device_type
    _self_device_positions = tuple(device_positions)
    logger.info(
        "ffb_router init: device_id=%r type=%r positions=%s",
        device_id, device_type, _self_device_positions,
    )


def is_initialised() -> bool:
    return _router is not None and bool(_self_device_type)


def _pick_layer(layers: list[RouteLayer]) -> Optional[RouteLayer]:
    """Phase-2 reduction: pick the single layer to actually play.

    Highest ``gain`` wins. Ties resolve to the first occurrence (stable).
    Returns None if the input is empty.
    """
    if not layers:
        return None
    if len(layers) == 1:
        return layers[0]
    # Stable max-by-gain.
    best = layers[0]
    for l in layers[1:]:
        if l.gain > best.gain:
            best = l
    if len(layers) > 1:
        logger.debug(
            "Multiple layers matched device %r; picked gain=%.2f from %d layers",
            _self_device_id, best.gain, len(layers),
        )
    return best


def _resolve_for_self(effect_name: Optional[str]) -> Optional[RouteLayer]:
    """Look up the active layer for this device, or None to drop the effect.

    Falls back to a ``RouteLayer()`` default (gain=1, target inherited) when
    the router has no entry for the effect AND the legacy whitelist behavior
    would have allowed it. This keeps unmodelled effects flowing on FFB
    devices during the rollout — only effects with an *explicit* empty route
    are silenced.
    """
    if _router is None or effect_name is None:
        return RouteLayer()  # transparent passthrough — keep legacy behaviour
    if not _router.is_effect_known(effect_name):
        # Effect has no entry: treat as legacy passthrough on FFB devices so
        # a gap in the routes file doesn't silence working effects.
        return RouteLayer()
    layers = _router.resolve(
        effect_name,
        device_id=_self_device_id,
        device_type=_self_device_type,
        device_positions=_self_device_positions,
    )
    return _pick_layer(layers)


# ----------------------------------------------------------------------
# HapticEffect facade — built lazily on first access. Subclasses
# ``ffb_rhino.HapticEffect`` so isinstance checks keep working and the
# class-level ``device`` attribute is shared with ffb_rhino (important
# because aircraft_base.on_telemetry pokes ``HapticEffect.device.get_input()``
# every frame).
# ----------------------------------------------------------------------

def _coerce_direction(direction) -> float:
    """Mirror of ffb_rhino's tolerance for class-typed direction args.

    ``periodic()`` accepts ``DirectionModulator`` *classes* (not values)
    for direction. We only need a float at the policy level — leave
    modulator handling to the parent for the actual FFB write.
    """
    if isinstance(direction, type):
        return 0.0
    try:
        return float(direction)
    except (TypeError, ValueError):
        return 0.0


def _build_haptic_effect_class():
    """Construct the routed HapticEffect subclass at first use.

    Importing ``ffb_rhino`` at module load time forces usb1/PyQt6/numpy
    into the import graph, which breaks tests and any non-FFB-host
    usage. Deferring to first-access keeps the pure helpers above
    importable on their own.
    """
    from telemffb.hw import ffb_rhino as _rhino

    class _RoutedHapticEffect(_rhino.HapticEffect):
        def __init__(self):
            super().__init__()
            self._resolved_layer: Optional[RouteLayer] = None
            self._call_site_direction: float = 0.0
            self._call_site_magnitude: float = 0.0

        def __repr__(self) -> str:
            return f"HapticEffect(router name={self.name!r})"

        def periodic(self, frequency, magnitude: float, direction: float,
                     *args, **kwargs):
            self._call_site_direction = _coerce_direction(direction)
            self._call_site_magnitude = float(magnitude)
            layer = _resolve_for_self(self.name)
            self._resolved_layer = layer
            if layer is None:
                return self
            eff_dir = EffectRouter.resolved_direction(
                layer,
                call_site_direction=self._call_site_direction,
                device_positions=_self_device_positions,
            )
            eff_mag = self._call_site_magnitude * layer.gain
            return super().periodic(frequency, eff_mag, eff_dir, *args, **kwargs)

        def constant(self, magnitude: float, direction: float, *args, **kwargs):
            self._call_site_direction = _coerce_direction(direction)
            self._call_site_magnitude = float(magnitude)
            layer = _resolve_for_self(self.name)
            self._resolved_layer = layer
            if layer is None:
                return self
            eff_dir = EffectRouter.resolved_direction(
                layer,
                call_site_direction=self._call_site_direction,
                device_positions=_self_device_positions,
            )
            eff_mag = self._call_site_magnitude * layer.gain
            return super().constant(eff_mag, eff_dir, *args, **kwargs)

        def physics(self, rpm: float, divisions: float, load: float = 1.0,
                    **shape_overrides):
            layer = _resolve_for_self(self.name)
            self._resolved_layer = layer
            if layer is None:
                return self
            scaled_load = float(load) * layer.gain
            return super().physics(rpm, divisions, scaled_load,
                                   **shape_overrides)

        def fire_impulse(self, magnitude: float, **shape_overrides):
            layer = _resolve_for_self(self.name)
            self._resolved_layer = layer
            if layer is None:
                return self
            scaled = float(magnitude) * layer.gain
            eff_dir = EffectRouter.resolved_direction(
                layer,
                call_site_direction=float(shape_overrides.get("direction", 0.0)),
                device_positions=_self_device_positions,
            )
            shape_overrides["direction"] = eff_dir
            return super().fire_impulse(scaled, **shape_overrides)

        def start(self, force: bool = False, **kw):
            if self._resolved_layer is None:
                return self
            return super().start(force=force, **kw)

    return _RoutedHapticEffect, _rhino.FFBReport_SetCondition


def __getattr__(name):
    """Lazy module attribute resolver for HapticEffect / FFBReport_SetCondition.

    Triggered only when ``aircraft_base.use_router_backend()`` (or test code)
    actually reads these names; pure-helpers usage stays import-free.
    """
    global _HAPTIC_EFFECT_CLS, _FFB_REPORT_SET_CONDITION_CLS
    if name == "HapticEffect":
        if _HAPTIC_EFFECT_CLS is None:
            _HAPTIC_EFFECT_CLS, _FFB_REPORT_SET_CONDITION_CLS = (
                _build_haptic_effect_class()
            )
        return _HAPTIC_EFFECT_CLS
    if name == "FFBReport_SetCondition":
        if _FFB_REPORT_SET_CONDITION_CLS is None:
            _HAPTIC_EFFECT_CLS, _FFB_REPORT_SET_CONDITION_CLS = (
                _build_haptic_effect_class()
            )
        return _FFB_REPORT_SET_CONDITION_CLS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
