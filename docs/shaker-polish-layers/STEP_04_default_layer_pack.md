# STEP_04 — Default layer pack

## Goal

Ship a sensible default JSON file with curated layer specs for the common
effects. The user can edit via the UI from STEP_03; this defines the
starting point.

## Design philosophy

Heuristic for the user's setup (Rhino mounted to chair, separate bass
shaker also coupled to the chair):

- **Sub-25 Hz energy → shaker.** The chair-coupled bass shaker delivers
  low-frequency energy more cleanly than the Rhino's stick-mounted
  moving mass.
- **40–90 Hz mids → split.** Biased per effect to wherever it makes
  physical sense (engine RPM fundamental on shaker, blade detail on
  stick, etc.).
- **>80 Hz cracks/transients → stick.** The user's hand on the grip
  resolves high-frequency detail well.
- **Impulse-type effects → low-freq impulse on shaker for the body
  thump, higher-freq sine or impulse on stick for the haptic crack.**

These are starting points. The UI in STEP_03 is the path for
per-pilot/per-rig tuning.

## Bundled default file

Lives at `telemffb/data/shaker_effects_default.json` — read-only resource
shipped with the package.

```json
{
  "version": 1,
  "effects": {
    "je_rumble_1_1": { "layers": [
      {"freq_factor": 0.5, "gain": 0.85, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"},
      {"freq_factor": 2.0, "gain": 0.30, "route": "stick",  "osc_type": "sine"}
    ]},
    "je_rumble_1_2": { "layers": [
      {"freq_factor": 0.5, "gain": 0.85, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"},
      {"freq_factor": 2.0, "gain": 0.30, "route": "stick",  "osc_type": "sine"}
    ]},
    "ab_rumble_1_1": { "layers": [
      {"freq_factor": 0.4, "gain": 1.00, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.60, "route": "stick",  "osc_type": "sine"},
      {"freq_factor": 2.5, "gain": 0.35, "route": "stick",  "osc_type": "sine"}
    ]},
    "prop_rpm0-1": { "layers": [
      {"freq_factor": 0.5, "gain": 0.70, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.55, "route": "stick",  "osc_type": "sine"}
    ]},
    "rotor_rpm0-1": { "layers": [
      {"freq_factor": 0.4, "gain": 0.90, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"}
    ]},
    "runway0": { "layers": [
      {"freq_factor": 0.6, "gain": 0.80, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.5, "gain": 0.40, "route": "stick",  "osc_type": "sine"}
    ]},
    "touchdown": { "layers": [
      {"freq_factor": 0.4, "gain": 1.00, "route": "shaker", "osc_type": "impulse"},
      {"freq_factor": 2.0, "gain": 0.40, "route": "stick",  "osc_type": "impulse"}
    ]},
    "gunfire": { "layers": [
      {"freq_factor": 0.4, "gain": 0.90, "route": "shaker", "osc_type": "impulse"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"}
    ]},
    "cm": { "layers": [
      {"freq_factor": 0.5, "gain": 0.70, "route": "shaker", "osc_type": "impulse"},
      {"freq_factor": 1.0, "gain": 0.45, "route": "stick",  "osc_type": "sine"}
    ]},
    "buffeting": { "layers": [
      {"freq_factor": 0.5, "gain": 0.65, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.55, "route": "stick",  "osc_type": "sine"}
    ]},
    "vrs_buffet": { "layers": [
      {"freq_factor": 0.4, "gain": 0.85, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"}
    ]},
    "gearbuffet": { "layers": [
      {"freq_factor": 0.5, "gain": 0.60, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.55, "route": "stick",  "osc_type": "sine"}
    ]},
    "gearmovement": { "layers": [
      {"freq_factor": 0.6, "gain": 0.70, "route": "shaker", "osc_type": "impulse"},
      {"freq_factor": 1.5, "gain": 0.40, "route": "stick",  "osc_type": "impulse"}
    ]},
    "flapsmovement": { "layers": [
      {"freq_factor": 0.7, "gain": 0.50, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"}
    ]},
    "speedbrakemovement": { "layers": [
      {"freq_factor": 0.6, "gain": 0.55, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"}
    ]},
    "etlX": { "layers": [
      {"freq_factor": 0.5, "gain": 0.70, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"}
    ]},
    "etlY": { "layers": [
      {"freq_factor": 0.5, "gain": 0.70, "route": "shaker", "osc_type": "sine"},
      {"freq_factor": 1.0, "gain": 0.50, "route": "stick",  "osc_type": "sine"}
    ]}
  }
}
```

## Resource resolution

The bundled JSON ships under `telemffb/data/shaker_effects_default.json`.
The package already has a `utils.get_resource_path()` (used in `main.py`
for `defaults.xml`). Reuse that. New helper in `shaker_layers_io.py`:

```python
def get_default_pack_path() -> str:
    """Path to the bundled default shaker_effects JSON (read-only)."""
    from telemffb import utils as _utils
    return _utils.get_resource_path(
        os.path.join("telemffb", "data", "shaker_effects_default.json"),
        prefer_root=True,
    )
```

## On-first-start copy

Wired into the same place `EFFECT_LAYERS` is loaded (the shaker branch of
`main.py:_initialize_device_connection`):

```python
user_path = get_shaker_effects_path()
if user_path and not os.path.exists(user_path):
    default_path = get_default_pack_path()
    try:
        shutil.copyfile(default_path, user_path)
        log.info("Initialised %s from bundled defaults", user_path)
    except Exception:
        log.exception("Could not seed user shaker_effects.json from bundle")
```

After the copy, the regular `load(user_path)` reads the freshly-written
file like any other.

## In-runtime built-in fallback

Even after the on-disk copy, keep an in-memory `_BUILTIN_DEFAULT_LAYERS`
in `ffb_shaker.py` populated by parsing the bundled JSON at module
import time. Two reasons:

1. `reload_layers()` in STEP_02 needs a base to layer the user's JSON
   on top of (so deleting an effect from the user file falls back to
   the built-in default rather than to an empty layer list).
2. STEP_03's "Reset effect to default" button needs to read a single
   effect's defaults without re-parsing the entire on-disk JSON.

Implementation:

```python
def _load_builtin_defaults() -> dict[str, list[Layer]]:
    from .shaker_layers_io import get_default_pack_path, load
    return load(get_default_pack_path())

_BUILTIN_DEFAULT_LAYERS: dict[str, list[Layer]] = _load_builtin_defaults()
```

Initial `EFFECT_LAYERS` becomes:

```python
EFFECT_LAYERS: dict[str, list[Layer]] = dict(_BUILTIN_DEFAULT_LAYERS)
```

And `reload_layers()` (from STEP_02) is updated to:

```python
def reload_layers() -> None:
    from .shaker_layers_io import load, get_shaker_effects_path
    EFFECT_LAYERS.clear()
    EFFECT_LAYERS.update(_BUILTIN_DEFAULT_LAYERS)
    path = get_shaker_effects_path()
    if path:
        EFFECT_LAYERS.update(load(path))
```

## "Reset effect to default" semantics (UI hook)

`SystemSettingsDialog` calls a small helper exposed by `ffb_shaker.py`:

```python
def get_builtin_default_for(name: str) -> list[Layer]:
    """Return the bundled default layers for a single effect, or an empty
    list if the effect has no built-in default. Returns a *fresh copy*
    so the caller can mutate without affecting _BUILTIN_DEFAULT_LAYERS.
    """
    return list(_BUILTIN_DEFAULT_LAYERS.get(name, []))
```

The UI uses this when the user clicks "Reset effect to default" — it
overwrites the working-copy entry for that single effect.

## "Reset all effects to defaults" semantics

UI helper writes the bundled default JSON to the user's path (overwriting
their file), then calls `reload_layers()`. Confirmation dialog before
the overwrite — same UX as the existing "Reset User Config" action in
the main window menu (`MainWindow.py:1256`).

## Acceptance

1. **Fresh install** — delete `<userconfig_rootpath>/shaker_effects.json`,
   start the shaker child. The file reappears identical to the bundled
   default. Effects play with the curated splits.
2. **Edit one effect, save** — only that effect's JSON entry changes;
   other effects stay equal to the bundle.
3. **Reset one effect** — the working copy reverts that effect only;
   other unsaved edits remain.
4. **Reset all effects** — confirmation, then on-disk file equals the
   bundled default again.
5. **Whitelisted effects without a default-pack entry** — fall through
   to the implicit `DEFAULT_LAYER` (single sine, route=both,
   freq×=gain×=1.0), unchanged behaviour vs. STEP_01.

## Out of scope for STEP_04

- Per-aircraft default packs.
- Versioned migrations beyond `version: 1`.
- Bundling alternative packs (e.g. "rotorcraft-focused", "fast-jet-focused").

## Stop here

Tick the box in `PLAN_LAYERS.md`, append notes about any default values
that turned out wrong during STEP_05 testing.
