#!/usr/bin/env python3
"""Generate ``telemffb/data/effect_routes_default.json`` from the canonical
sources in ``telemffb/hw/ffb_shaker.py``.

What this script does
---------------------
1. Parses ``SHAKER_EFFECT_WHITELIST``, ``SHAKER_EFFECT_PROFILES`` and
   ``SHAKER_PHYSICS_PROFILES`` out of ``ffb_shaker.py`` via the AST. We
   never import ``ffb_shaker`` because that pulls in usb1 / sounddevice /
   numpy — we only need the dict literals.
2. Reads the existing ``effect_routes_default.json`` so any hand-tuned
   layers there (the 20 effects originally migrated from
   ``shaker_effects_default.json``) are preserved verbatim.
3. For every effect in the whitelist that does NOT already have an entry,
   synthesises a sensible default Layer set:

   - effects with a ``transient`` profile → impulse layer on the shaker,
     softer impulse/sine on the stick
   - effects in ``SHAKER_PHYSICS_PROFILES`` → impulse on shaker only
     (these are phase-locked impulse trains, only meaningful on the synth)
   - everything else → balanced sine on shaker + stick using
     ``_DEFAULT_PROFILE`` gain/ramp values

   The synthesised layer always uses the new ``target:`` selector form
   (``type:shaker`` / ``type:joystick``) and the v4 schema fields.

4. Writes the result to ``telemffb/data/effect_routes_default.json``,
   preserving stable ordering by category so diffs are reviewable.

When to re-run
--------------
Whenever ``SHAKER_EFFECT_WHITELIST`` gains an entry, run this script and
commit the regenerated JSON. Hand-tuned routes are not overwritten — the
script only fills in what's missing.

Run::

    python3 scripts/migrate_shaker_to_routes.py            # in-place rewrite
    python3 scripts/migrate_shaker_to_routes.py --dry-run  # print to stdout

Exits non-zero if it would have changed anything in --check mode (CI).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
SHAKER_PY = REPO_ROOT / "telemffb" / "hw" / "ffb_shaker.py"
ROUTES_JSON = REPO_ROOT / "telemffb" / "data" / "effect_routes_default.json"


# --- AST extractors ------------------------------------------------------

def _read_module() -> ast.Module:
    return ast.parse(SHAKER_PY.read_text(encoding="utf-8"),
                     filename=str(SHAKER_PY))


def _extract_set_assignment(name: str) -> set[str]:
    """Pull a ``NAME = {...}`` set-of-strings literal out of ffb_shaker.py."""
    for node in ast.walk(_read_module()):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name):
            continue
        if not isinstance(node.value, ast.Set):
            raise SystemExit(f"{name} is not a set literal — adapt loader")
        out: set[str] = set()
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.add(elt.value)
        return out
    raise SystemExit(f"{name} not found in {SHAKER_PY}")


def _extract_dict_assignment(name: str) -> dict:
    """Pull a ``NAME: dict = {...}`` literal — for SHAKER_*_PROFILES dicts.

    Each value is itself a dict with primitive (string / number) entries.
    Anything else is skipped with a warning so the script doesn't crash on
    a future profile entry that uses a function call or named constant.
    """
    tree = _read_module()
    target_node: Optional[ast.AST] = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name
                and isinstance(node.value, ast.Dict)):
            target_node = node.value
            break
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name
                and isinstance(node.value, ast.Dict)):
            target_node = node.value
            break
    if target_node is None:
        raise SystemExit(f"{name} not found (or not a dict literal)")

    out: dict[str, dict] = {}
    for k_node, v_node in zip(target_node.keys, target_node.values):
        if not (isinstance(k_node, ast.Constant) and isinstance(k_node.value, str)):
            continue
        if not isinstance(v_node, ast.Dict):
            continue
        entry: dict = {}
        for kk, vv in zip(v_node.keys, v_node.values):
            if not (isinstance(kk, ast.Constant) and isinstance(kk.value, str)):
                continue
            if isinstance(vv, ast.Constant) and isinstance(vv.value, (int, float, str, bool)):
                entry[kk.value] = vv.value
            elif isinstance(vv, ast.UnaryOp) and isinstance(vv.op, ast.USub) \
                    and isinstance(vv.operand, ast.Constant) \
                    and isinstance(vv.operand.value, (int, float)):
                entry[kk.value] = -vv.operand.value
        out[k_node.value] = entry

    # ffb_shaker.py applies a runtime fan-out for prop_phys_2..4 / cyl_phys_2..4
    # at module-load time. Reproduce that here so the JSON has full coverage.
    if name == "SHAKER_PHYSICS_PROFILES":
        for src, copies in (
            ("prop_phys_1", ("prop_phys_2", "prop_phys_3", "prop_phys_4")),
            ("cyl_phys_1",  ("cyl_phys_2",  "cyl_phys_3",  "cyl_phys_4")),
        ):
            if src in out:
                for dst in copies:
                    out.setdefault(dst, dict(out[src]))
    return out


# --- defaults from ffb_shaker.py reproduced inline -----------------------

# Mirror of ``_DEFAULT_PROFILE`` (ffb_shaker.py:133). Loose mirror — the
# values feed synthesised layers; production runtime always reads the
# canonical copy from ffb_shaker itself.
DEFAULT_PROFILE = {"kind": "continuous", "ramp_ms": 50.0, "gain": 1.0}

# Effects intentionally excluded from the JSON. ``__effect_tester__`` is
# the EffectTestDialog harness pseudo-effect — runtime-only, not a real
# routing target.
SKIP_EFFECTS = frozenset({"__effect_tester__"})


# --- categorisation for stable JSON ordering -----------------------------

CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wheel / runway", (
        "runway_carrier", "runway_carrier_delayed", "runway1",
        "runway_impulse", "runway_impulse_delayed",
        "touchdown", "touchdown_vs_main", "touchdown_vs_nose",
        "gearclunk", "nw_shimmy",
    )),
    ("weapons / countermeasures", (
        "gunfire", "cm", "payload_rel",
        "il2_bombs", "il2_rockets", "il2_gunfire",
    )),
    ("buffeting", (
        "buffeting", "buffeting2", "vrs_buffet", "vrs_buffet2",
        "il2_buffet", "il2_buffet2",
        "gearbuffet", "gearbuffet2",
        "spoilerbuffet1-1", "spoilerbuffet1-2",
        "spoilerbuffet2-1", "spoilerbuffet2-2",
    )),
    ("damage / impact", ("hit", "damage")),
    ("afterburner / jet rumble", (
        "ab_rumble_1_1", "ab_rumble_1_2", "ab_rumble_2_1", "ab_rumble_2_2",
        "je_rumble_1_1", "je_rumble_1_2", "je_rumble_2_1", "je_rumble_2_2",
    )),
    ("prop / rotor RPM", (
        "prop_rpm0-1", "prop_rpm0-2", "prop_rpm1-1", "prop_rpm1-2",
        "rotor_rpm0-1", "rotor_rpm1-1",
    )),
    ("physics-driven impulse trains", (
        "rotor_phys_main", "rotor_phys_tail",
        "prop_phys_1", "prop_phys_2", "prop_phys_3", "prop_phys_4",
        "cyl_phys_1", "cyl_phys_2", "cyl_phys_3", "cyl_phys_4",
    )),
    ("ETL", ("etlX", "etlY")),
    ("surface motion / clunks", (
        "flapsmovement", "gearmovement", "gearmovement2",
        "speedbrakemovement", "spoilermovement", "spoilermovement2",
        "canopymovement", "canopyclunk", "hookmovement", "clunk",
    )),
    ("overspeed / aoa", ("overspeedX", "overspeedY", "aoa", "crit_aoa")),
    ("wind", ("wnd",)),
)


def _ordered_effect_names(whitelist: set[str]) -> list[str]:
    """Sort whitelist into category order for a clean JSON diff.

    Effects not listed in any category bucket are appended alphabetically
    so future additions to the whitelist still land somewhere stable.
    """
    seen: set[str] = set()
    out: list[str] = []
    for _, names in CATEGORIES:
        for n in names:
            if n in whitelist and n not in seen:
                out.append(n)
                seen.add(n)
    leftover = sorted(n for n in whitelist if n not in seen and n not in SKIP_EFFECTS)
    out.extend(leftover)
    return out


# --- layer synthesis -----------------------------------------------------

def _layer(target: str, gain: float, freq_factor: float, osc_type: str,
           **extra) -> dict:
    """One layer dict in the v4 schema. ``extra`` carries optional
    bandpass / impulse fields the synthesiser leaves at None.
    """
    out = {
        "target": target,
        "gain": round(float(gain), 3),
        "freq_factor": round(float(freq_factor), 3),
        "osc_type": osc_type,
    }
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _synthesise_layers(name: str,
                       profiles: dict, physics_profiles: dict) -> list[dict]:
    """Produce a sensible default layer set for an effect.

    Heuristics:
    - Physics-train voices (``rotor_phys_*``, ``prop_phys_*``, ``cyl_phys_*``)
      get an impulse layer on the shaker (where the phase-locked impulse
      synthesis runs) plus a softer sine layer on the joystick. The
      EffectRouter requires an explicit per-device layer for ``physics()``
      to produce output — the periodic-on-stick fallback in
      ``ffb_rhino.HapticEffect.physics()`` only runs if the layer list
      includes ``type:joystick``.
    - Transient profiles (``kind == "transient"``) are pulse-shaped — both
      a shaker layer (impulse) and a softer joystick layer (impulse).
    - Continuous default — sine on shaker + sine on joystick.

    Gain values come from the source profile when available so the audio
    output stays close to today's behaviour.
    """
    profile = profiles.get(name)
    physics = physics_profiles.get(name)

    if physics is not None:
        shaker_gain = physics.get("gain", 1.0)
        # Joystick gain ratio mirrors the prop_rpm0-1 / rotor_rpm0-1 legacy
        # voices (~0.7-0.8× shaker), with a safety floor for low-amplitude
        # cylinder-firing trains so they're still felt on the stick.
        stick_gain = min(0.5, max(0.25, shaker_gain * 0.75))
        return [
            _layer("type:shaker", shaker_gain,
                   freq_factor=1.0, osc_type="impulse"),
            _layer("type:joystick", stick_gain,
                   freq_factor=1.0, osc_type="sine"),
        ]

    if profile is not None and profile.get("kind") == "transient":
        gain = float(profile.get("gain", 1.0))
        attack = profile.get("attack_ms")
        decay = profile.get("decay_ms")
        return [
            _layer("type:shaker", gain, freq_factor=1.0, osc_type="impulse",
                   attack_ms=attack, decay_ms=decay),
            _layer("type:joystick", min(0.5, gain * 0.5),
                   freq_factor=1.5, osc_type="impulse"),
        ]

    # Continuous fallback.
    gain = float(profile.get("gain", DEFAULT_PROFILE["gain"])) \
        if profile else DEFAULT_PROFILE["gain"]
    return [
        _layer("type:shaker",   gain * 0.85, freq_factor=0.5, osc_type="sine"),
        _layer("type:joystick", gain * 0.50, freq_factor=1.0, osc_type="sine"),
    ]


# --- main ----------------------------------------------------------------

def build(existing_routes: dict, whitelist: set[str], profiles: dict,
          physics_profiles: dict) -> dict:
    """Assemble the new JSON dict. ``existing_routes`` is the parsed
    contents of the current effect_routes_default.json (its ``effects``
    sub-map). Hand-tuned entries there are preserved verbatim.
    """
    effects: dict = {}
    for name in _ordered_effect_names(whitelist):
        if name in existing_routes:
            # Keep hand-tuned layers exactly as they are.
            effects[name] = existing_routes[name]
            continue
        effects[name] = {"layers": _synthesise_layers(
            name, profiles, physics_profiles)}
    return effects


def serialise(effects: dict, existing_overrides: dict) -> str:
    """Produce the final JSON text. Preserves the existing top-level
    ``aircraft_class_overrides`` and the explanatory ``_comment`` block.
    """
    payload: dict = {
        "version": 4,
        "_comment": [
            "Generalised effect-routing pack. Generated by",
            "scripts/migrate_shaker_to_routes.py from SHAKER_EFFECT_WHITELIST",
            "+ SHAKER_EFFECT_PROFILES + SHAKER_PHYSICS_PROFILES in",
            "telemffb/hw/ffb_shaker.py, plus any hand-tuned layers from a",
            "prior version of this file. Re-run the script after adding",
            "an effect to the whitelist; hand-tuned routes are preserved.",
            "Selectors: 'type:<type>' (joystick / shaker / pedals / ...),",
            "'id:<device_id>' (specific device), 'pos:<tag>'.",
        ],
        "effects": effects,
        "aircraft_class_overrides": existing_overrides,
    }
    return json.dumps(payload, indent=2) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                   help="Print the result to stdout, don't write the file.")
    p.add_argument("--check", action="store_true",
                   help="Exit non-zero if the file would change. "
                        "Useful in CI to catch missing whitelist updates.")
    args = p.parse_args()

    whitelist = _extract_set_assignment("SHAKER_EFFECT_WHITELIST")
    profiles = _extract_dict_assignment("SHAKER_EFFECT_PROFILES")
    physics_profiles = _extract_dict_assignment("SHAKER_PHYSICS_PROFILES")

    existing_data = (
        json.loads(ROUTES_JSON.read_text(encoding="utf-8"))
        if ROUTES_JSON.exists() else {}
    )
    existing_routes = existing_data.get("effects", {}) or {}
    existing_overrides = existing_data.get("aircraft_class_overrides", {}) or {}

    effects = build(existing_routes, whitelist, profiles, physics_profiles)
    new_text = serialise(effects, existing_overrides)

    if args.dry_run:
        sys.stdout.write(new_text)
        return 0

    if args.check:
        current = ROUTES_JSON.read_text(encoding="utf-8") if ROUTES_JSON.exists() else ""
        if current.strip() != new_text.strip():
            sys.stderr.write(
                "effect_routes_default.json is out of date. "
                "Run scripts/migrate_shaker_to_routes.py.\n"
            )
            return 1
        return 0

    # Atomic write: temp file + rename.
    tmp = ROUTES_JSON.with_suffix(".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(ROUTES_JSON)
    sys.stdout.write(
        f"Wrote {len(effects)} effects to {ROUTES_JSON}\n"
        f"  (whitelist size: {len(whitelist)}, "
        f"hand-tuned preserved: {sum(1 for n in effects if n in existing_routes)})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
