"""Unit tests for the generalised effect-routing engine.

Covers:
- ``RouteLayer`` legacy conversion (route -> target)
- ``EffectRoute.layers_for`` selector matching
- ``EffectRouter.resolve`` end-to-end with defaults + user overrides
- ``EffectRouter.resolve`` with aircraft class patch
- Loading the bundled ``effect_routes_default.json``
- Loading a v1 shaker file via the same loader
- ``Device`` round-trip and inventory load from JSON / INI

Run with::

    python -m unittest tests.test_routing
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from telemffb.device_inventory import (
    Device,
    encode_inventory_for_ini,
    load_inventory_from_ini,
    load_inventory_from_json,
    save_inventory_to_json,
)
from telemffb.routing import (
    DirectionPolicy,
    EffectRoute,
    EffectRouter,
    EffectRoutesPack,
    RouteLayer,
    layer_targets_device,
    parse_target_selector,
)
from telemffb.routing.router import load_routes_pack


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROUTES_PATH = REPO_ROOT / "telemffb" / "data" / "effect_routes_default.json"
LEGACY_SHAKER_PATH = REPO_ROOT / "telemffb" / "data" / "shaker_effects_default.json"


class TestSelectorParser(unittest.TestCase):
    def test_type_selector(self):
        self.assertEqual(parse_target_selector("type:shaker"), ("type", "shaker"))

    def test_id_selector(self):
        self.assertEqual(parse_target_selector("id:stick_main"), ("id", "stick_main"))

    def test_pos_selector(self):
        self.assertEqual(parse_target_selector("pos:floor"), ("pos", "floor"))

    def test_both_selector(self):
        self.assertEqual(parse_target_selector("both"), ("both", ""))

    def test_bare_id_falls_back_to_id(self):
        self.assertEqual(parse_target_selector("stick_main"), ("id", "stick_main"))


class TestLayerTargetsDevice(unittest.TestCase):
    def test_id_match(self):
        self.assertTrue(layer_targets_device(
            "id:stick_main", device_id="stick_main", device_type="joystick",
        ))
        self.assertFalse(layer_targets_device(
            "id:stick_main", device_id="other", device_type="joystick",
        ))

    def test_type_match(self):
        self.assertTrue(layer_targets_device(
            "type:shaker", device_id="any", device_type="shaker",
        ))
        self.assertFalse(layer_targets_device(
            "type:shaker", device_id="any", device_type="pedals",
        ))

    def test_position_match(self):
        self.assertTrue(layer_targets_device(
            "pos:floor", device_id="x", device_type="pedals",
            device_positions=("front", "floor"),
        ))
        self.assertFalse(layer_targets_device(
            "pos:floor", device_id="x", device_type="pedals",
            device_positions=("seat",),
        ))

    def test_both_legacy_alias(self):
        # "both" preserves the legacy semantic: stick + shaker only,
        # NOT pedals or collective.
        self.assertTrue(layer_targets_device("both", device_id="x", device_type="joystick"))
        self.assertTrue(layer_targets_device("both", device_id="x", device_type="shaker"))
        self.assertFalse(layer_targets_device("both", device_id="x", device_type="pedals"))


class TestRouteLayerLegacyConversion(unittest.TestCase):
    def test_route_shaker(self):
        layer = RouteLayer.from_legacy({
            "freq_factor": 0.5, "gain": 0.85,
            "route": "shaker", "osc_type": "sine",
        })
        self.assertEqual(layer.target, "type:shaker")
        self.assertEqual(layer.gain, 0.85)
        self.assertEqual(layer.osc_type, "sine")

    def test_route_stick(self):
        layer = RouteLayer.from_legacy({"route": "stick"})
        self.assertEqual(layer.target, "type:joystick")

    def test_route_both(self):
        layer = RouteLayer.from_legacy({"route": "both"})
        self.assertEqual(layer.target, "both")

    def test_unknown_route_falls_back_to_both(self):
        layer = RouteLayer.from_legacy({"route": "magicpony"})
        self.assertEqual(layer.target, "both")

    def test_unknown_field_dropped(self):
        # An unknown key should not crash; it should just be dropped.
        layer = RouteLayer.from_legacy({"route": "stick", "bogus_field": 42})
        self.assertEqual(layer.target, "type:joystick")


class TestEffectRouteLayersFor(unittest.TestCase):
    def setUp(self):
        self.route = EffectRoute(
            name="rumble",
            layers=[
                RouteLayer(target="type:shaker", gain=1.0),
                RouteLayer(target="type:joystick", gain=0.5),
                RouteLayer(target="id:pedals_main", gain=0.7, enabled=False),
                RouteLayer(target="pos:floor", gain=0.3),
            ],
        )

    def test_filters_by_type(self):
        layers = self.route.layers_for(device_id="x", device_type="shaker")
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].gain, 1.0)

    def test_disabled_layer_skipped(self):
        layers = self.route.layers_for(
            device_id="pedals_main", device_type="pedals",
        )
        # The id-match layer is disabled; only ``pos:floor`` would match if
        # we had positions. With no positions, expect empty.
        self.assertEqual(layers, [])

    def test_position_match(self):
        layers = self.route.layers_for(
            device_id="any", device_type="pedals", device_positions=["floor"],
        )
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].gain, 0.3)


class TestEffectRouterResolve(unittest.TestCase):
    def setUp(self):
        defaults = EffectRoutesPack(
            routes={
                "gunfire": EffectRoute("gunfire", [
                    RouteLayer(target="type:shaker", gain=1.0),
                    RouteLayer(target="type:joystick", gain=0.5),
                ]),
                "stall": EffectRoute("stall", [
                    RouteLayer(target="type:joystick", gain=0.8),
                ]),
            },
        )
        user = EffectRoutesPack(
            routes={
                # Override gunfire: silence the shaker, boost the stick.
                "gunfire": EffectRoute("gunfire", [
                    RouteLayer(target="type:joystick", gain=1.5),
                ]),
            },
            aircraft_class_overrides={
                "Helicopter": {
                    # In a heli, stall doesn't make sense — replace with empty.
                    "stall": EffectRoute("stall", []),
                },
            },
        )
        self.router = EffectRouter(defaults=defaults, user_overrides=user)

    def test_user_override_replaces_defaults(self):
        layers = self.router.resolve(
            "gunfire", device_id="stick", device_type="joystick",
        )
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].gain, 1.5)

    def test_user_override_silences_shaker(self):
        layers = self.router.resolve(
            "gunfire", device_id="shaker", device_type="shaker",
        )
        self.assertEqual(layers, [])

    def test_unknown_effect_returns_empty(self):
        layers = self.router.resolve(
            "fictional_effect", device_id="x", device_type="joystick",
        )
        self.assertEqual(layers, [])

    def test_aircraft_class_patch(self):
        layers = self.router.resolve(
            "stall", device_id="x", device_type="joystick",
            aircraft_class="Helicopter",
        )
        self.assertEqual(layers, [])
        # Without the class, stall still has its default layer.
        layers = self.router.resolve(
            "stall", device_id="x", device_type="joystick",
        )
        self.assertEqual(len(layers), 1)


class TestDirectionPolicy(unittest.TestCase):
    def test_inherit_returns_call_site(self):
        layer = RouteLayer(direction_policy=DirectionPolicy.INHERIT)
        self.assertEqual(
            EffectRouter.resolved_direction(layer, call_site_direction=42.0),
            42.0,
        )

    def test_fixed_overrides(self):
        layer = RouteLayer(
            direction_policy=DirectionPolicy.FIXED, direction_value=270.0,
        )
        self.assertEqual(
            EffectRouter.resolved_direction(layer, call_site_direction=0.0),
            270.0,
        )

    def test_fixed_without_value_falls_back(self):
        layer = RouteLayer(direction_policy=DirectionPolicy.FIXED)
        self.assertEqual(
            EffectRouter.resolved_direction(layer, call_site_direction=10.0),
            10.0,
        )

    def test_auto_uses_position(self):
        layer = RouteLayer(direction_policy=DirectionPolicy.AUTO)
        self.assertEqual(
            EffectRouter.resolved_direction(
                layer, call_site_direction=0.0, device_positions=("right",),
            ),
            90.0,
        )
        self.assertEqual(
            EffectRouter.resolved_direction(
                layer, call_site_direction=0.0, device_positions=("front",),
            ),
            0.0,
        )

    def test_auto_falls_back_when_no_position(self):
        layer = RouteLayer(direction_policy=DirectionPolicy.AUTO)
        self.assertEqual(
            EffectRouter.resolved_direction(
                layer, call_site_direction=137.0, device_positions=(),
            ),
            137.0,
        )


class TestRoutesPackLoading(unittest.TestCase):
    def test_load_bundled_default(self):
        pack = load_routes_pack(str(DEFAULT_ROUTES_PATH))
        self.assertIsNotNone(pack)
        self.assertEqual(pack.version, 4)
        # Bundled default covers the whole shaker whitelist (~70 effects);
        # the exact count fluctuates with whitelist additions, so just
        # assert "lots of effects" + a few specific known names.
        self.assertGreater(len(pack.routes), 50)
        self.assertIn("gunfire", pack.routes)
        self.assertIn("runway0", pack.routes)
        self.assertIn("rotor_phys_main", pack.routes)
        # Layer targets are the new selector strings.
        gunfire = pack.routes["gunfire"]
        targets = {l.target for l in gunfire.layers}
        self.assertEqual(targets, {"type:shaker", "type:joystick"})

    def test_load_legacy_shaker_file(self):
        # The same loader handles v1 files (with ``route``).
        pack = load_routes_pack(str(LEGACY_SHAKER_PATH))
        self.assertIsNotNone(pack)
        # Legacy file has the original hand-tuned 20.
        self.assertEqual(len(pack.routes), 20)
        # 'route: shaker' -> 'target: type:shaker'.
        gunfire = pack.routes["gunfire"]
        targets = {l.target for l in gunfire.layers}
        self.assertEqual(targets, {"type:shaker", "type:joystick"})

    def test_legacy_routes_preserved_in_bundled_default(self):
        # The bundled default is a superset of the legacy file: every
        # hand-tuned effect from the v1 shaker_effects_default.json must
        # appear in effect_routes_default.json with identical layers
        # (within float tolerance). New effects added by the migration
        # script live alongside, but never overwrite.
        legacy = load_routes_pack(str(LEGACY_SHAKER_PATH))
        new = load_routes_pack(str(DEFAULT_ROUTES_PATH))
        # All legacy keys must still be present.
        missing = set(legacy.routes) - set(new.routes)
        self.assertFalse(missing,
                         f"hand-tuned effects lost in migration: {missing}")
        # And their layers must match exactly.
        for name in legacy.routes:
            legacy_route = legacy.routes[name]
            new_route = new.routes[name]
            self.assertEqual(len(legacy_route.layers), len(new_route.layers),
                             f"layer count differs for {name!r}")
            for new_l, leg_l in zip(new_route.layers, legacy_route.layers):
                self.assertEqual(new_l.target, leg_l.target,
                                 f"target differs for {name!r}")
                self.assertAlmostEqual(new_l.gain, leg_l.gain)
                self.assertAlmostEqual(new_l.freq_factor, leg_l.freq_factor)
                self.assertEqual(new_l.osc_type, leg_l.osc_type)


class TestRouterLiveReload(unittest.TestCase):
    """``EffectRouter.reload_user_overrides`` swaps the user pack in place
    so the running routing decisions update without re-instantiating the
    router. Used by the EffectRoutingDialog Apply path.
    """

    def test_reload_updates_resolution(self):
        defaults = EffectRoutesPack(routes={
            "gunfire": EffectRoute("gunfire", [
                RouteLayer(target="type:joystick", gain=0.5),
            ]),
        })
        router = EffectRouter(defaults=defaults)
        # Sanity: starts with the default gain.
        layers = router.resolve("gunfire", device_id="x", device_type="joystick")
        self.assertEqual(layers[0].gain, 0.5)

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "user.json")
            with open(path, "w") as f:
                json.dump({"version": 4, "effects": {
                    "gunfire": {"layers": [
                        {"target": "type:joystick", "gain": 0.9, "freq_factor": 1.0,
                         "osc_type": "sine"}
                    ]}
                }}, f)
            ok = router.reload_user_overrides(path)
        self.assertTrue(ok)
        layers = router.resolve("gunfire", device_id="x", device_type="joystick")
        self.assertEqual(layers[0].gain, 0.9)

    def test_reload_missing_file_returns_false(self):
        router = EffectRouter()
        self.assertFalse(router.reload_user_overrides("/no/such/file.json"))

    def test_reload_none_path_clears_overrides(self):
        defaults = EffectRoutesPack(routes={
            "gunfire": EffectRoute("gunfire", [RouteLayer(target="type:joystick")])
        })
        user = EffectRoutesPack(routes={
            "gunfire": EffectRoute("gunfire", [RouteLayer(target="type:shaker")])
        })
        router = EffectRouter(defaults=defaults, user_overrides=user)
        # Before clear: user override silences joystick.
        self.assertEqual(
            router.resolve("gunfire", device_id="x", device_type="joystick"),
            [],
        )
        router.reload_user_overrides(None)
        # After clear: default route is back.
        self.assertEqual(
            len(router.resolve("gunfire", device_id="x", device_type="joystick")),
            1,
        )


class TestDeviceRoundTrip(unittest.TestCase):
    def test_to_from_dict(self):
        d = Device(
            device_id="stick_main", type="joystick",
            positions=["right", "desk"], usb_pid="FFFF:2055",
            label="VPForce Stick", master=True, enabled=True,
        )
        round_tripped = Device.from_dict(d.to_dict())
        self.assertEqual(round_tripped, d)

    def test_unknown_keys_dropped(self):
        d = Device.from_dict({
            "device_id": "x", "type": "joystick",
            "rogue_field": "ignore me",
        })
        self.assertEqual(d.device_id, "x")

    def test_xy_offset_validation(self):
        d = Device(device_id="x", xy_offset={"x": 10.0, "y": 20.0})
        self.assertEqual(d.xy_offset, {"x": 10.0, "y": 20.0})
        # Malformed -> cleared
        d2 = Device(device_id="x", xy_offset={"x": 10.0})  # missing y
        self.assertIsNone(d2.xy_offset)


class TestInventoryIO(unittest.TestCase):
    def test_json_round_trip(self):
        devices = [
            Device(device_id="stick", type="joystick", positions=["right", "desk"]),
            Device(device_id="shaker_seat", type="shaker", positions=["seat", "center"]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "inv.json")
            save_inventory_to_json(path, devices)
            loaded = load_inventory_from_json(path)
        self.assertEqual(loaded, devices)

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(load_inventory_from_json("/nonexistent/path.json"), [])

    def test_ini_round_trip(self):
        devices = [
            Device(device_id="stick", type="joystick"),
            Device(device_id="pedals", type="pedals", positions=["floor", "front"]),
        ]
        encoded = encode_inventory_for_ini(devices)
        loaded = load_inventory_from_ini(encoded)
        self.assertEqual(loaded, devices)

    def test_ini_empty_returns_empty(self):
        self.assertEqual(load_inventory_from_ini(None), [])
        self.assertEqual(load_inventory_from_ini(""), [])

    def test_ini_malformed_returns_empty(self):
        self.assertEqual(load_inventory_from_ini("not valid json {"), [])

    def test_duplicate_ids_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "inv.json")
            with open(path, "w") as f:
                json.dump({"devices": [
                    {"device_id": "x", "type": "joystick"},
                    {"device_id": "x", "type": "shaker"},
                ]}, f)
            loaded = load_inventory_from_json(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].type, "joystick")


if __name__ == "__main__":
    unittest.main()
