"""``ffb_router`` — third HapticEffect backend that consults ``EffectRouter``.

This sits between the aircraft modules and the underlying Rhino device:

- Aircraft code calls the unchanged ``effects['name'].periodic(...).start()``.
- This facade (a subclass of ``ffb_rhino.HapticEffect``) asks the router for
  *all* layers that match this device for this effect, and configures one
  Rhino effect slot per layer. ``.start()`` / ``.stop()`` / ``.destroy()``
  fan out across every slot.

Design notes
------------
- This backend is **only** wired up for FFB devices (joystick / pedals /
  collective / trimwheel / rudder). The shaker child still uses the
  ``ffb_shaker`` facade — it has its own per-effect layer system with audio
  mixing that this router does not replace.
- True multi-layer FFB output: every layer that resolves for the current
  device gets its own ``_rhino.HapticEffect`` sub-handle. Two layers
  targeting the same device (e.g. one ``runway0`` layer at 30 Hz forward,
  another at 60 Hz sideways on pedals) both write to the Rhino in
  parallel. The composite holds the list of sub-handles internally; each
  sub-handle is named ``"<effect>__layer<idx>"`` for log readability and
  parity with the shaker side.
- ``physics()`` and ``fire_impulse()`` are dispatched per layer too, so
  Rhino's per-effect fallbacks (in ``_rhino.HapticEffect.physics`` /
  ``fire_impulse``) run independently on each slot.
- Force-only effects (``spring`` / ``damper`` / ``inertia`` / ``friction`` /
  ``setCondition``) bypass routing entirely — they're not "effects" in the
  routable sense. They route through the inherited ``_h_effect`` slot like
  before, so the aircraft code that calls them keeps working unchanged.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .effect_route import RouteLayer
from .router import EffectRouter

logger = logging.getLogger(__name__)

# Lazy hardware import — keeps the pure routing helpers (init_router,
# _resolve_layers_for_self, is_initialised) usable in environments
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


# Sentinel returned by ``_resolve_layers_for_self`` when an effect is not
# in the router's effective routes. We treat that as legacy passthrough
# (a single transparent layer) so a gap in the routes file doesn't silence
# working effects during a rollout. An effect with an explicit empty
# layer list is silenced — that's the on-purpose case.
_PASSTHROUGH_LAYER = RouteLayer()


def _resolve_layers_for_self(
        effect_name: Optional[str]) -> list[RouteLayer]:
    """All layers that should fire on THIS device for this effect.

    Returns:
    - ``[]`` when the effect is known to the router but has no layer for
      this device (silently drop)
    - ``[RouteLayer()]`` when the router has no entry at all (legacy
      passthrough)
    - the resolved layer list when known and matching
    """
    if _router is None or effect_name is None:
        return [_PASSTHROUGH_LAYER]
    if not _router.is_effect_known(effect_name):
        return [_PASSTHROUGH_LAYER]
    return _router.resolve(
        effect_name,
        device_id=_self_device_id,
        device_type=_self_device_type,
        device_positions=_self_device_positions,
    )


# Kept for backward compatibility with code (and tests) written against the
# Phase-2 single-layer implementation. New code should prefer
# ``_resolve_layers_for_self``.
def _pick_layer(layers: list[RouteLayer]) -> Optional[RouteLayer]:
    """Single-layer reduction: highest gain wins, stable on ties."""
    if not layers:
        return None
    if len(layers) == 1:
        return layers[0]
    best = layers[0]
    for l in layers[1:]:
        if l.gain > best.gain:
            best = l
    return best


def _resolve_for_self(effect_name: Optional[str]) -> Optional[RouteLayer]:
    """Phase-2 compatibility shim — returns one layer or None."""
    layers = _resolve_layers_for_self(effect_name)
    if not layers:
        return None
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
        """Composite of one ``_rhino.HapticEffect`` per resolved layer.

        The inherited ``_h_effect`` slot from the parent is left
        unused on this class — the actual FFB writes happen on the
        per-layer sub-handles in ``self._sub_handles``. We still
        subclass so isinstance checks continue to work and so
        force-only effects (spring, damper, ...) inherited from the
        parent operate as before.
        """

        def __init__(self):
            super().__init__()
            # One Rhino effect slot per resolved layer. Lazily grown on the
            # first periodic / constant / fire_impulse call so unused
            # effects stay free.
            self._sub_handles: List[_rhino.HapticEffect] = []
            self._sub_layers: List[RouteLayer] = []
            # Cached call-site values — periodic/constant capture them so
            # a later .start() (or a re-resolve after a routing reload)
            # has the input it needs.
            self._call_site_direction: float = 0.0
            self._call_site_magnitude: float = 0.0
            # ``True`` once periodic / constant / physics / fire_impulse has
            # configured at least one sub-handle. ``start()`` is a no-op
            # otherwise, mirroring the gated behaviour from Phase 2.
            self._is_resolved: bool = False

        def __repr__(self) -> str:
            return (f"HapticEffect(router name={self.name!r}, "
                    f"layers={len(self._sub_handles)})")

        # --- sub-handle lifecycle ---

        def _ensure_subs(self, layers: list[RouteLayer]) -> None:
            """Resize ``_sub_handles`` to match the resolved layer count.

            Sub-handles are reused across calls so the underlying Rhino
            effect block is only allocated once; surplus handles from a
            previous call (e.g. after a routing reload that dropped a
            layer) are destroyed cleanly.
            """
            # Grow.
            while len(self._sub_handles) < len(layers):
                sub = _rhino.HapticEffect()
                idx = len(self._sub_handles)
                # Name sub-handles for readable logs & parity with the
                # shaker side, which uses the same suffix scheme.
                sub.name = (f"{self.name}__layer{idx}"
                            if self.name else None)
                self._sub_handles.append(sub)
            # Shrink.
            while len(self._sub_handles) > len(layers):
                extra = self._sub_handles.pop()
                try:
                    extra.destroy()
                except Exception:
                    logger.exception(
                        "Failed to destroy shrunk sub-handle for %r",
                        self.name,
                    )
            self._sub_layers = list(layers)

        # --- shape configuration ---

        def periodic(self, frequency, magnitude: float, direction: float,
                     *args, **kwargs):
            self._call_site_direction = _coerce_direction(direction)
            self._call_site_magnitude = float(magnitude)
            layers = _resolve_layers_for_self(self.name)
            self._ensure_subs(layers)
            if not layers:
                self._is_resolved = False
                return self
            for sub, layer in zip(self._sub_handles, layers):
                eff_dir = EffectRouter.resolved_direction(
                    layer,
                    call_site_direction=self._call_site_direction,
                    device_positions=_self_device_positions,
                )
                eff_mag = self._call_site_magnitude * layer.gain
                # Frequency factor lets two layers on the same device
                # carry different harmonics — the second layer of a
                # rumble can play at 2× the carrier without changing the
                # call site.
                eff_freq = float(frequency) * layer.freq_factor
                sub.periodic(eff_freq, eff_mag, eff_dir, *args, **kwargs)
            self._is_resolved = True
            return self

        def constant(self, magnitude: float, direction: float, *args,
                     **kwargs):
            self._call_site_direction = _coerce_direction(direction)
            self._call_site_magnitude = float(magnitude)
            layers = _resolve_layers_for_self(self.name)
            self._ensure_subs(layers)
            if not layers:
                self._is_resolved = False
                return self
            for sub, layer in zip(self._sub_handles, layers):
                eff_dir = EffectRouter.resolved_direction(
                    layer,
                    call_site_direction=self._call_site_direction,
                    device_positions=_self_device_positions,
                )
                eff_mag = self._call_site_magnitude * layer.gain
                sub.constant(eff_mag, eff_dir, *args, **kwargs)
            self._is_resolved = True
            return self

        def physics(self, rpm: float, divisions: float, load: float = 1.0,
                    **shape_overrides):
            layers = _resolve_layers_for_self(self.name)
            self._ensure_subs(layers)
            if not layers:
                self._is_resolved = False
                return self
            for sub, layer in zip(self._sub_handles, layers):
                # Rhino physics fallback maps (load * gain) into magnitude,
                # which is what we want — gain compounds with the layer's
                # weight in the multi-slot mix.
                scaled_load = float(load) * layer.gain
                sub.physics(rpm, divisions, scaled_load, **shape_overrides)
            self._is_resolved = True
            return self

        def fire_impulse(self, magnitude: float, **shape_overrides):
            layers = _resolve_layers_for_self(self.name)
            self._ensure_subs(layers)
            if not layers:
                self._is_resolved = False
                return self
            for sub, layer in zip(self._sub_handles, layers):
                eff_dir = EffectRouter.resolved_direction(
                    layer,
                    call_site_direction=float(
                        shape_overrides.get("direction", 0.0)),
                    device_positions=_self_device_positions,
                )
                # Per-layer copy so we don't leak direction overrides
                # across sub-handles.
                sub_overrides = dict(shape_overrides)
                sub_overrides["direction"] = eff_dir
                scaled = float(magnitude) * layer.gain
                sub.fire_impulse(scaled, **sub_overrides)
            self._is_resolved = True
            return self

        # --- start / stop / destroy fan-out ---

        def start(self, force: bool = False, **kw):
            if not self._is_resolved or not self._sub_handles:
                return self
            for sub in self._sub_handles:
                sub.start(force=force, **kw)
            return self

        def stop(self, destroy_after: int = 10000):
            for sub in self._sub_handles:
                sub.stop(destroy_after=destroy_after)
            return self

        def destroy(self):
            for sub in self._sub_handles:
                try:
                    sub.destroy()
                except Exception:
                    logger.exception(
                        "Failed to destroy sub-handle for %r", self.name)
            self._sub_handles.clear()
            self._sub_layers.clear()
            self._is_resolved = False

        @property
        def started(self) -> bool:
            return any(getattr(sub, "started", False)
                       for sub in self._sub_handles)

        @property
        def id(self):
            # Mirror ffb_rhino: returns the Rhino effect-block id of the
            # FIRST sub-handle, or None if the composite has no slots
            # configured. Code that displays this typically tolerates None.
            if not self._sub_handles:
                return None
            return self._sub_handles[0].id

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
