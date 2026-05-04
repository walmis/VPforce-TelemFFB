"""Drift test: ``effect_routes_default.json`` covers the entire shaker
whitelist.

If a developer adds an effect name to ``SHAKER_EFFECT_WHITELIST`` in
``telemffb/hw/ffb_shaker.py`` but forgets to update the routes file, the
new effect would silently fall through to the ``ffb_router`` passthrough
on FFB devices and the legacy hardcoded profile on the shaker. That's
fine for runtime correctness, but it means the effect is invisible in
the routing matrix and not user-configurable.

This test catches the drift by asserting:

1. Every entry in the whitelist (minus a known harness pseudo-effect)
   has a route in ``telemffb/data/effect_routes_default.json``.
2. The routes file has no entries for names that aren't in the
   whitelist (catches typos and dead routes).
3. Every layer in the routes file uses the new ``target:`` selector
   form (no leftover ``route:`` from v1-v3 schema).

Mirrors ``tests/test_shaker_whitelist.py`` in spirit and uses the same
AST-based whitelist loader so it doesn't need usb1 / sounddevice.

To resolve a failure, run::

    python3 scripts/migrate_shaker_to_routes.py

and commit the regenerated JSON.
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SHAKER_PY = REPO_ROOT / "telemffb" / "hw" / "ffb_shaker.py"
ROUTES_JSON = REPO_ROOT / "telemffb" / "data" / "effect_routes_default.json"

# Effects intentionally not represented in the routes file. ``__effect_tester__``
# is the EffectTestDialog harness pseudo-name (whitelisted so it can play
# arbitrary effects, but not a routable target itself).
SKIPPED_FROM_ROUTES = frozenset({"__effect_tester__"})


def _load_whitelist() -> frozenset[str]:
    tree = ast.parse(SHAKER_PY.read_text(encoding="utf-8"),
                     filename=str(SHAKER_PY))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "SHAKER_EFFECT_WHITELIST"
                and isinstance(node.value, ast.Set)):
            names = {e.value for e in node.value.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            return frozenset(names)
    raise AssertionError(f"SHAKER_EFFECT_WHITELIST not found in {SHAKER_PY}")


def _load_routes() -> dict:
    return json.loads(ROUTES_JSON.read_text(encoding="utf-8"))


class RoutesCoverageTest(unittest.TestCase):
    def setUp(self):
        self.whitelist = _load_whitelist()
        self.routes_data = _load_routes()
        self.routes = self.routes_data.get("effects", {})

    def test_every_whitelisted_effect_has_a_route(self):
        expected = self.whitelist - SKIPPED_FROM_ROUTES
        missing = expected - set(self.routes.keys())
        self.assertFalse(
            missing,
            f"\nThese whitelist effects have no route in "
            f"effect_routes_default.json:\n  "
            + "\n  ".join(sorted(missing))
            + "\nRun: python3 scripts/migrate_shaker_to_routes.py",
        )

    def test_no_dead_routes(self):
        # The routes file must not contain entries for effects that are
        # not in the whitelist — those would never fire on the shaker
        # backend and likely indicate a typo.
        extra = set(self.routes.keys()) - self.whitelist
        self.assertFalse(
            extra,
            f"\nThese routes have no matching whitelist entry "
            f"(typos? dead code?):\n  "
            + "\n  ".join(sorted(extra)),
        )

    def test_all_layers_use_v4_target_selector(self):
        # Catches leftover v1-v3 ``route:`` keys after a migration that
        # bypassed the script. Every layer must declare ``target``.
        legacy = []
        no_target = []
        for name, route in self.routes.items():
            for i, layer in enumerate(route.get("layers", [])):
                if "route" in layer:
                    legacy.append(f"{name}#{i}")
                if "target" not in layer:
                    no_target.append(f"{name}#{i}")
        self.assertFalse(
            legacy,
            f"\nLayers still using legacy 'route:' field: "
            + ", ".join(legacy)
            + "\nRun: python3 scripts/migrate_shaker_to_routes.py",
        )
        self.assertFalse(
            no_target,
            f"\nLayers missing 'target:' selector: " + ", ".join(no_target),
        )

    def test_target_selectors_are_well_formed(self):
        # Sanity: every target is one of the four supported shapes.
        bad = []
        for name, route in self.routes.items():
            for i, layer in enumerate(route.get("layers", [])):
                target = layer.get("target", "")
                if target == "both":
                    continue
                if not (target.startswith("id:")
                        or target.startswith("type:")
                        or target.startswith("pos:")):
                    bad.append(f"{name}#{i}: {target!r}")
        self.assertFalse(
            bad,
            "\nLayers with malformed target selector "
            "(must be id:/type:/pos:/both):\n  " + "\n  ".join(bad),
        )

    def test_schema_version_is_current(self):
        self.assertEqual(self.routes_data.get("version"), 4)

    def test_migration_script_check_mode_is_clean(self):
        # Running the script in --check mode must report "no changes
        # needed". If this fails, someone hand-edited the JSON and broke
        # the output of ``scripts/migrate_shaker_to_routes.py`` — re-run
        # the script and commit the result.
        import subprocess
        result = subprocess.run(
            ["python3", "scripts/migrate_shaker_to_routes.py", "--check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Migration script reports the JSON is out of date.\n"
            f"stderr:\n{result.stderr}\n"
            f"Run: python3 scripts/migrate_shaker_to_routes.py",
        )


if __name__ == "__main__":
    unittest.main()
