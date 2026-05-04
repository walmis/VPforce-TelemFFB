"""Unit tests for the router-aware HapticEffect backend (``ffb_router``).

The Rhino parent's ``periodic`` / ``constant`` / ``start`` / ``physics`` /
``fire_impulse`` are patched with ``unittest.mock`` so the tests run without
HID hardware. We assert that:

- when no layer matches the current device, the parent methods are NOT called
  and ``.start()`` is a no-op;
- when a layer matches, the parent receives the correct (gain-scaled) magnitude
  and (policy-resolved) direction;
- ``init_router`` correctly seeds the module-level state;
- ``_pick_layer`` resolves multi-layer ties by maximum gain.

Run with::

    python -m unittest tests.test_ffb_router
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock

from telemffb.routing import (
    DirectionPolicy,
    EffectRoute,
    EffectRouter,
    EffectRoutesPack,
    RouteLayer,
)
from telemffb.routing import ffb_router

# The HapticEffect facade (and its FFBReport_SetCondition partner) need
# usb1 / PyQt6 / numpy / hidapi to import — present in real installs but
# missing in CI sandboxes. The pure helpers (init_router, _pick_layer,
# _resolve_for_self) work without these and run unconditionally.
def _facade_importable() -> bool:
    try:
        ffb_router.HapticEffect  # triggers lazy load
        return True
    except Exception:
        return False

FACADE_AVAILABLE = _facade_importable()


def _make_router_with_routes(**routes: list[RouteLayer]) -> EffectRouter:
    pack = EffectRoutesPack(
        routes={name: EffectRoute(name, layers) for name, layers in routes.items()},
    )
    return EffectRouter(defaults=pack)


class TestPickLayer(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(ffb_router._pick_layer([]))

    def test_single_returned_as_is(self):
        l = RouteLayer(gain=0.5)
        self.assertIs(ffb_router._pick_layer([l]), l)

    def test_max_gain_wins(self):
        layers = [
            RouteLayer(gain=0.3),
            RouteLayer(gain=0.9),
            RouteLayer(gain=0.7),
        ]
        self.assertEqual(ffb_router._pick_layer(layers).gain, 0.9)

    def test_tie_keeps_first(self):
        first = RouteLayer(gain=0.5, freq_factor=1.0)
        second = RouteLayer(gain=0.5, freq_factor=2.0)
        self.assertEqual(ffb_router._pick_layer([first, second]).freq_factor, 1.0)


class TestInitRouter(unittest.TestCase):
    def setUp(self):
        # Snapshot module state so each test starts fresh.
        self._snapshot = (
            ffb_router._router,
            ffb_router._self_device_id,
            ffb_router._self_device_type,
            ffb_router._self_device_positions,
        )

    def tearDown(self):
        (ffb_router._router,
         ffb_router._self_device_id,
         ffb_router._self_device_type,
         ffb_router._self_device_positions) = self._snapshot

    def test_uninitialised_treated_as_passthrough(self):
        # Reset everything to the uninitialised state.
        ffb_router._router = None
        ffb_router._self_device_id = ""
        ffb_router._self_device_type = ""
        ffb_router._self_device_positions = ()
        self.assertFalse(ffb_router.is_initialised())
        # ``_resolve_for_self`` returns a default RouteLayer (passthrough)
        # rather than None — this preserves legacy behaviour for processes
        # that haven't been migrated yet.
        layer = ffb_router._resolve_for_self("any_effect")
        self.assertIsInstance(layer, RouteLayer)
        self.assertEqual(layer.gain, 1.0)

    def test_init_seeds_state(self):
        router = _make_router_with_routes()
        ffb_router.init_router(
            router, device_id="stick_main", device_type="joystick",
            device_positions=("right", "desk"),
        )
        self.assertTrue(ffb_router.is_initialised())
        self.assertEqual(ffb_router._self_device_id, "stick_main")
        self.assertEqual(ffb_router._self_device_type, "joystick")
        self.assertEqual(ffb_router._self_device_positions, ("right", "desk"))


class TestResolveForSelf(unittest.TestCase):
    def setUp(self):
        self._snapshot = (
            ffb_router._router,
            ffb_router._self_device_id,
            ffb_router._self_device_type,
            ffb_router._self_device_positions,
        )

    def tearDown(self):
        (ffb_router._router,
         ffb_router._self_device_id,
         ffb_router._self_device_type,
         ffb_router._self_device_positions) = self._snapshot

    def test_unmodelled_effect_falls_through(self):
        # An effect with no entry in the router is treated as legacy
        # passthrough on FFB devices — gap in the routes file does NOT
        # silence existing effects.
        router = _make_router_with_routes()  # nothing
        ffb_router.init_router(router, device_id="stick", device_type="joystick")
        layer = ffb_router._resolve_for_self("anything")
        self.assertIsNotNone(layer)
        self.assertEqual(layer.gain, 1.0)

    def test_known_effect_with_no_matching_layer_returns_none(self):
        # Effect IS in the router but has no layer for this device -> drop.
        router = _make_router_with_routes(
            gunfire=[RouteLayer(target="type:shaker", gain=1.0)],
        )
        ffb_router.init_router(router, device_id="stick", device_type="joystick")
        self.assertIsNone(ffb_router._resolve_for_self("gunfire"))

    def test_matching_layer_returned(self):
        router = _make_router_with_routes(
            gunfire=[
                RouteLayer(target="type:shaker", gain=1.0),
                RouteLayer(target="type:joystick", gain=0.5),
            ],
        )
        ffb_router.init_router(router, device_id="stick", device_type="joystick")
        layer = ffb_router._resolve_for_self("gunfire")
        self.assertEqual(layer.target, "type:joystick")
        self.assertEqual(layer.gain, 0.5)

    def test_position_match(self):
        router = _make_router_with_routes(
            runway=[RouteLayer(target="pos:floor", gain=0.3)],
        )
        ffb_router.init_router(
            router, device_id="pedals", device_type="pedals",
            device_positions=("floor", "front"),
        )
        layer = ffb_router._resolve_for_self("runway")
        self.assertIsNotNone(layer)
        self.assertEqual(layer.gain, 0.3)


@unittest.skipUnless(FACADE_AVAILABLE,
                     "HapticEffect facade unavailable in this environment "
                     "(missing usb1/PyQt6/etc.) — runs in real installs")
class TestHapticEffectFacade(unittest.TestCase):
    """Patches the Rhino parent so we can assert what ffb_router forwards."""

    def setUp(self):
        # Snapshot router state.
        self._snapshot = (
            ffb_router._router,
            ffb_router._self_device_id,
            ffb_router._self_device_type,
            ffb_router._self_device_positions,
        )
        # Patch Rhino parent methods. The ffb_router.HapticEffect inherits
        # from _rhino.HapticEffect, so super().periodic() lands here.
        from telemffb.hw import ffb_rhino as _rhino
        self._rhino = _rhino
        self.parent_periodic = mock.patch.object(
            _rhino.HapticEffect, "periodic", return_value=None,
        ).start()
        self.parent_constant = mock.patch.object(
            _rhino.HapticEffect, "constant", return_value=None,
        ).start()
        self.parent_start = mock.patch.object(
            _rhino.HapticEffect, "start", return_value=None,
        ).start()
        self.parent_physics = mock.patch.object(
            _rhino.HapticEffect, "physics", return_value=None,
        ).start()
        self.parent_fire_impulse = mock.patch.object(
            _rhino.HapticEffect, "fire_impulse", return_value=None,
        ).start()

    def tearDown(self):
        mock.patch.stopall()
        (ffb_router._router,
         ffb_router._self_device_id,
         ffb_router._self_device_type,
         ffb_router._self_device_positions) = self._snapshot

    def _wire(self, *, device_type="joystick", device_id="stick", positions=(),
              **routes_layers):
        router = _make_router_with_routes(**routes_layers)
        ffb_router.init_router(
            router, device_id=device_id, device_type=device_type,
            device_positions=positions,
        )

    def _make(self, name="gunfire"):
        eff = ffb_router.HapticEffect()
        eff.name = name
        return eff

    def test_periodic_no_match_does_not_call_parent(self):
        self._wire(gunfire=[RouteLayer(target="type:shaker")])
        eff = self._make("gunfire")
        eff.periodic(50.0, 1.0, 0.0)
        self.parent_periodic.assert_not_called()
        eff.start()
        self.parent_start.assert_not_called()

    def test_periodic_with_match_scales_magnitude(self):
        self._wire(gunfire=[RouteLayer(target="type:joystick", gain=0.5)])
        eff = self._make("gunfire")
        eff.periodic(50.0, 1.0, 0.0)
        self.parent_periodic.assert_called_once()
        args, kwargs = self.parent_periodic.call_args
        # super().periodic(frequency, eff_mag, eff_dir, ...)
        self.assertEqual(args[0], 50.0)         # frequency unchanged
        self.assertAlmostEqual(args[1], 0.5)     # 1.0 * gain=0.5
        self.assertEqual(args[2], 0.0)           # direction inherited

    def test_periodic_fixed_direction_overrides(self):
        self._wire(runway=[RouteLayer(
            target="type:joystick",
            direction_policy=DirectionPolicy.FIXED,
            direction_value=270.0,
        )])
        eff = self._make("runway")
        eff.periodic(30.0, 0.7, 0.0)  # call site says 0
        args, _ = self.parent_periodic.call_args
        self.assertEqual(args[2], 270.0)

    def test_periodic_auto_direction_uses_position(self):
        self._wire(
            device_type="pedals", device_id="pedals", positions=("front",),
            runway=[RouteLayer(
                target="type:pedals",
                direction_policy=DirectionPolicy.AUTO,
            )],
        )
        eff = self._make("runway")
        eff.periodic(30.0, 1.0, 90.0)  # call site direction would be 90
        args, _ = self.parent_periodic.call_args
        # ``front`` -> 0.0 by the AUTO heuristic
        self.assertEqual(args[2], 0.0)

    def test_constant_no_match_does_not_call_parent(self):
        self._wire(gforce=[RouteLayer(target="type:shaker")])
        eff = self._make("gforce")
        eff.constant(0.5, 90.0)
        self.parent_constant.assert_not_called()

    def test_constant_match_scales_magnitude(self):
        self._wire(gforce=[RouteLayer(target="type:joystick", gain=0.8)])
        eff = self._make("gforce")
        eff.constant(0.5, 90.0)
        self.parent_constant.assert_called_once()
        args, _ = self.parent_constant.call_args
        self.assertAlmostEqual(args[0], 0.4)  # 0.5 * 0.8
        self.assertEqual(args[1], 90.0)

    def test_physics_match_scales_load(self):
        self._wire(rotor_phys_main=[RouteLayer(
            target="type:joystick", gain=0.5,
        )])
        eff = self._make("rotor_phys_main")
        eff.physics(rpm=300, divisions=2, load=1.0)
        self.parent_physics.assert_called_once()
        args, _ = self.parent_physics.call_args
        self.assertEqual(args[0], 300)
        self.assertEqual(args[1], 2)
        self.assertAlmostEqual(args[2], 0.5)  # load * gain

    def test_fire_impulse_match_scales(self):
        self._wire(gearclunk=[RouteLayer(
            target="type:joystick", gain=0.4,
            direction_policy=DirectionPolicy.FIXED, direction_value=180.0,
        )])
        eff = self._make("gearclunk")
        eff.fire_impulse(1.0)
        self.parent_fire_impulse.assert_called_once()
        args, kwargs = self.parent_fire_impulse.call_args
        self.assertAlmostEqual(args[0], 0.4)
        self.assertEqual(kwargs["direction"], 180.0)

    def test_unmodelled_effect_passes_through_to_parent(self):
        # Effect not in router -> passthrough RouteLayer with gain=1.
        self._wire()  # empty routes
        eff = self._make("brand_new_effect")
        eff.periodic(50.0, 1.0, 0.0)
        self.parent_periodic.assert_called_once()
        args, _ = self.parent_periodic.call_args
        self.assertEqual(args[1], 1.0)


@unittest.skipUnless(FACADE_AVAILABLE,
                     "HapticEffect facade unavailable in this environment "
                     "(missing usb1/PyQt6/etc.) — runs in real installs")
class TestHapticEffectMultiLayer(unittest.TestCase):
    """Multi-layer fan-out — two or more layers targeting the same device
    must all be configured and started in parallel.

    The composite owns one ``_rhino.HapticEffect`` sub-handle per resolved
    layer. Each sub-handle's method goes through the same patched
    ``_rhino.HapticEffect.<method>`` so we can count call totals against
    the layer count.
    """

    def setUp(self):
        self._snapshot = (
            ffb_router._router,
            ffb_router._self_device_id,
            ffb_router._self_device_type,
            ffb_router._self_device_positions,
        )
        from telemffb.hw import ffb_rhino as _rhino
        self._rhino = _rhino
        # Patch parent (= sub-handle) methods. Each ``_rhino.HapticEffect``
        # instance is a sub-handle; its periodic / start / etc. land on
        # the same mock here.
        self.p_periodic = mock.patch.object(
            _rhino.HapticEffect, "periodic", return_value=None).start()
        self.p_constant = mock.patch.object(
            _rhino.HapticEffect, "constant", return_value=None).start()
        self.p_start = mock.patch.object(
            _rhino.HapticEffect, "start", return_value=None).start()
        self.p_stop = mock.patch.object(
            _rhino.HapticEffect, "stop", return_value=None).start()
        self.p_destroy = mock.patch.object(
            _rhino.HapticEffect, "destroy", return_value=None).start()
        self.p_physics = mock.patch.object(
            _rhino.HapticEffect, "physics", return_value=None).start()
        self.p_fire_impulse = mock.patch.object(
            _rhino.HapticEffect, "fire_impulse", return_value=None).start()

    def tearDown(self):
        mock.patch.stopall()
        (ffb_router._router,
         ffb_router._self_device_id,
         ffb_router._self_device_type,
         ffb_router._self_device_positions) = self._snapshot

    def _wire(self, *, device_type="joystick", device_id="stick",
              positions=(), **routes_layers):
        router = _make_router_with_routes(**routes_layers)
        ffb_router.init_router(
            router, device_id=device_id, device_type=device_type,
            device_positions=positions,
        )

    def _make(self, name="rumble"):
        eff = ffb_router.HapticEffect()
        eff.name = name
        return eff

    def test_periodic_two_matching_layers_configures_both(self):
        # Two layers on the same device — both should be written to Rhino.
        self._wire(rumble=[
            RouteLayer(target="type:joystick", gain=1.0, freq_factor=1.0),
            RouteLayer(target="type:joystick", gain=0.5, freq_factor=2.0),
        ])
        eff = self._make("rumble")
        eff.periodic(30.0, 1.0, 0.0)
        # Two sub-handles -> two periodic writes.
        self.assertEqual(self.p_periodic.call_count, 2)
        # First layer: gain=1.0, freq_factor=1.0 -> mag=1.0, freq=30.0
        # Second layer: gain=0.5, freq_factor=2.0 -> mag=0.5, freq=60.0
        first_args, _ = self.p_periodic.call_args_list[0]
        second_args, _ = self.p_periodic.call_args_list[1]
        self.assertEqual(first_args[0], 30.0)
        self.assertAlmostEqual(first_args[1], 1.0)
        self.assertEqual(second_args[0], 60.0)
        self.assertAlmostEqual(second_args[1], 0.5)

    def test_periodic_layers_with_different_directions(self):
        # The classic motivating case: two pedals layers with different
        # fixed directions so a runway rumble pulses both forward (0°)
        # and laterally (270°).
        self._wire(
            device_type="pedals", device_id="pedals_main", positions=("floor",),
            runway=[
                RouteLayer(target="type:pedals", gain=0.6, freq_factor=1.0,
                           direction_policy=DirectionPolicy.FIXED,
                           direction_value=0.0),
                RouteLayer(target="type:pedals", gain=0.4, freq_factor=1.5,
                           direction_policy=DirectionPolicy.FIXED,
                           direction_value=270.0),
            ],
        )
        eff = self._make("runway")
        eff.periodic(40.0, 1.0, 90.0)  # call-site direction is ignored
        self.assertEqual(self.p_periodic.call_count, 2)
        directions = [self.p_periodic.call_args_list[i][0][2]
                      for i in (0, 1)]
        self.assertEqual(set(directions), {0.0, 270.0})

    def test_layer_with_non_matching_target_is_skipped(self):
        # Layers that don't match this device (e.g. a shaker layer on a
        # joystick process) must NOT consume a sub-handle slot.
        self._wire(rumble=[
            RouteLayer(target="type:shaker",   gain=1.0),    # filtered out
            RouteLayer(target="type:joystick", gain=0.7),    # kept
        ])
        eff = self._make("rumble")
        eff.periodic(50.0, 1.0, 0.0)
        self.assertEqual(self.p_periodic.call_count, 1)
        args, _ = self.p_periodic.call_args
        self.assertAlmostEqual(args[1], 0.7)  # the joystick layer's gain

    def test_start_fans_out_to_all_sub_handles(self):
        self._wire(rumble=[
            RouteLayer(target="type:joystick", gain=1.0),
            RouteLayer(target="type:joystick", gain=0.6),
            RouteLayer(target="type:joystick", gain=0.3),
        ])
        eff = self._make("rumble")
        eff.periodic(30.0, 1.0, 0.0)
        eff.start()
        self.assertEqual(self.p_start.call_count, 3)

    def test_stop_fans_out_to_all_sub_handles(self):
        self._wire(rumble=[
            RouteLayer(target="type:joystick", gain=1.0),
            RouteLayer(target="type:joystick", gain=0.6),
        ])
        eff = self._make("rumble")
        eff.periodic(30.0, 1.0, 0.0)
        eff.stop()
        self.assertEqual(self.p_stop.call_count, 2)

    def test_destroy_clears_sub_handles(self):
        self._wire(rumble=[
            RouteLayer(target="type:joystick", gain=1.0),
            RouteLayer(target="type:joystick", gain=0.6),
        ])
        eff = self._make("rumble")
        eff.periodic(30.0, 1.0, 0.0)
        # Two sub-handles before destroy.
        self.assertEqual(len(eff._sub_handles), 2)
        eff.destroy()
        # All destroyed and the list is cleared.
        self.assertEqual(self.p_destroy.call_count, 2)
        self.assertEqual(eff._sub_handles, [])
        self.assertFalse(eff._is_resolved)

    def test_physics_dispatches_per_layer(self):
        self._wire(rotor_phys_main=[
            RouteLayer(target="type:joystick", gain=1.0),
            RouteLayer(target="type:joystick", gain=0.4),
        ])
        eff = self._make("rotor_phys_main")
        eff.physics(rpm=300, divisions=2, load=1.0)
        self.assertEqual(self.p_physics.call_count, 2)
        # Loads: 1.0 and 0.4 (load * gain).
        loads = sorted(
            self.p_physics.call_args_list[i][0][2] for i in (0, 1))
        self.assertAlmostEqual(loads[0], 0.4)
        self.assertAlmostEqual(loads[1], 1.0)

    def test_fire_impulse_dispatches_per_layer(self):
        self._wire(gearclunk=[
            RouteLayer(target="type:joystick", gain=1.0,
                       direction_policy=DirectionPolicy.FIXED,
                       direction_value=0.0),
            RouteLayer(target="type:joystick", gain=0.6,
                       direction_policy=DirectionPolicy.FIXED,
                       direction_value=180.0),
        ])
        eff = self._make("gearclunk")
        eff.fire_impulse(1.0)
        self.assertEqual(self.p_fire_impulse.call_count, 2)
        # Magnitudes scaled by per-layer gain; directions per-layer fixed.
        mags = sorted(
            self.p_fire_impulse.call_args_list[i][0][0] for i in (0, 1))
        dirs = sorted(
            self.p_fire_impulse.call_args_list[i][1]["direction"]
            for i in (0, 1))
        self.assertAlmostEqual(mags[0], 0.6)
        self.assertAlmostEqual(mags[1], 1.0)
        self.assertEqual(dirs, [0.0, 180.0])

    def test_resize_shrinks_sub_handles_on_re_resolve(self):
        # First resolve: two layers. Second resolve (after a routing
        # reload that drops one): the surplus sub-handle is destroyed.
        self._wire(rumble=[
            RouteLayer(target="type:joystick", gain=1.0),
            RouteLayer(target="type:joystick", gain=0.5),
        ])
        eff = self._make("rumble")
        eff.periodic(30.0, 1.0, 0.0)
        self.assertEqual(len(eff._sub_handles), 2)
        # Reload routing with one layer instead of two.
        self._wire(rumble=[RouteLayer(target="type:joystick", gain=1.0)])
        eff.periodic(30.0, 1.0, 0.0)
        self.assertEqual(len(eff._sub_handles), 1)
        self.assertGreaterEqual(self.p_destroy.call_count, 1)


if __name__ == "__main__":
    unittest.main()
