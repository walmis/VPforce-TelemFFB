# STEP_02 — Config loading & file management

## Goal

Replace the hardcoded `EFFECT_LAYERS` test dict from STEP_01 with a JSON
file loaded at shaker-instance startup. Provide a reload entry point that
the UI (STEP_03) will call after Save. Survive missing or malformed files
by falling back to built-in defaults.

## File location

User config dir is `G.userconfig_rootpath`. It is populated in
`main.py:_setup_config_paths()` (around line 316). On Windows production
this resolves to `%LOCALAPPDATA%/VPForce-TelemFFB`; in dev mode (the repo
checkout) it's the repo root, alongside `userconfig_v2.xml`.

```
<G.userconfig_rootpath>/shaker_effects.json
```

Do **not** invent a new directory. Use the same `userconfig_rootpath` the
existing settings infrastructure uses, so the user's settings backups
include this file automatically.

## File format

```json
{
  "version": 1,
  "effects": {
    "je_rumble_1_1": {
      "layers": [
        {"freq_factor": 0.5, "gain": 0.8, "route": "shaker", "osc_type": "sine"},
        {"freq_factor": 1.0, "gain": 0.6, "route": "stick",  "osc_type": "sine"},
        {"freq_factor": 2.0, "gain": 0.3, "route": "stick",  "osc_type": "sine"}
      ]
    },
    "gunfire": {
      "layers": [
        {"freq_factor": 0.3, "gain": 0.9, "route": "shaker", "osc_type": "impulse"},
        {"freq_factor": 1.0, "gain": 0.5, "route": "stick",  "osc_type": "sine"}
      ]
    }
  }
}
```

Top-level `version` is for future schema changes. Mismatch logs a warning
and attempts a best-effort load — reject only on actual structural
failure.

## New module — `telemffb/hw/shaker_layers_io.py`

Small, focused, ~80 lines. Includes the GPL v3 header copied from an
existing file.

```python
"""Load and save shaker effect layer specifications."""
import json
import logging
import os
from dataclasses import asdict
from typing import Optional

from .ffb_shaker import Layer

CURRENT_VERSION = 1
log = logging.getLogger(__name__)


def load(path: str) -> dict[str, list[Layer]]:
    """Read a layer-spec JSON file. Missing/malformed -> empty dict.

    The shaker child treats an empty result as "no user customisation;
    use built-in defaults" (which are populated separately in STEP_04).
    """
    if not os.path.exists(path):
        log.info("No shaker effects file at %s — using built-in defaults", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        log.exception("Failed to read shaker effects from %s — using empty set", path)
        return {}

    file_version = data.get("version")
    if file_version != CURRENT_VERSION:
        log.warning(
            "Shaker effects file version mismatch (%s != %s); attempting load anyway",
            file_version, CURRENT_VERSION,
        )

    out: dict[str, list[Layer]] = {}
    for eff_name, eff_data in data.get("effects", {}).items():
        try:
            layers = [Layer(**ld) for ld in eff_data.get("layers", [])]
        except TypeError:
            log.exception("Bad layer spec for effect %r; skipping", eff_name)
            continue
        if layers:
            out[eff_name] = layers

    log.info("Loaded %d effect layer specs from %s", len(out), path)
    return out


def save(path: str, effect_layers: dict[str, list[Layer]]) -> None:
    """Atomically persist a layer-spec dict to disk.

    Writes to ``<path>.tmp`` first, then ``os.replace`` — survives a
    crash mid-write without leaving a half-written JSON file behind.
    """
    data = {
        "version": CURRENT_VERSION,
        "effects": {
            name: {"layers": [asdict(layer) for layer in layers]}
            for name, layers in effect_layers.items()
        },
    }
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    log.info("Saved %d effect layer specs to %s", len(effect_layers), path)


def get_shaker_effects_path() -> Optional[str]:
    """Resolve the user's shaker_effects.json path.

    Returns None if userconfig_rootpath is not set yet (e.g. shaker child
    started before _setup_config_paths ran). Caller treats None like
    "no file present".
    """
    from telemffb import globals as G
    root = getattr(G, "userconfig_rootpath", "") or ""
    if not root:
        return None
    return os.path.join(root, "shaker_effects.json")
```

## Wiring into shaker startup

`main.py` already has the shaker-init branch around line 380. After
`init_shaker(synth)` and after `aircraft_base.use_shaker_backend()` add
the layer load:

```python
from telemffb.hw.shaker_layers_io import load as _load_shaker_layers, get_shaker_effects_path
from telemffb.hw import ffb_shaker as _ffb_shaker
_path = get_shaker_effects_path()
if _path:
    _ffb_shaker.EFFECT_LAYERS.update(_load_shaker_layers(_path))
logging.info("Shaker effects: %d effects with custom layers",
             len(_ffb_shaker.EFFECT_LAYERS))
```

`update()` not assignment — preserves any built-in defaults baked into
the module by STEP_04 (and the STEP_01 hardcoded test entries are
removed in STEP_02 once this load path is in).

## Reload entry point

In `ffb_shaker.py`:

```python
def reload_layers() -> None:
    """Re-read the user's shaker_effects.json and replace EFFECT_LAYERS.

    Called by the System Settings UI after Save. Does NOT clear built-in
    defaults — anything not in the JSON falls back to the built-in
    default layer pack.
    """
    from .shaker_layers_io import load, get_shaker_effects_path
    path = get_shaker_effects_path()
    if not path:
        return
    new_data = load(path)
    EFFECT_LAYERS.clear()
    EFFECT_LAYERS.update(_BUILTIN_DEFAULT_LAYERS)  # populated by STEP_04
    EFFECT_LAYERS.update(new_data)
    logger.info("EFFECT_LAYERS reloaded: %d entries", len(EFFECT_LAYERS))
```

`_BUILTIN_DEFAULT_LAYERS` is added in STEP_04. For STEP_02 this is
simply an empty dict (the JSON file is the only source).

## Removing the STEP_01 hardcoded test dict

The STEP_01 hardcoded entries (`je_rumble_1_1`, `gunfire`, `touchdown`)
are removed in STEP_02 — they only existed to verify dispatch logic in
isolation. Replace with `EFFECT_LAYERS: dict[str, list[Layer]] = {}` and
let the JSON load populate it.

## Acceptance

1. **No file present** → shaker child starts cleanly, logs the "no shaker
   effects file at ..." info line, `EFFECT_LAYERS` is empty, all effects
   fall back to legacy profile / default behaviour. No crash.
2. **Hand-crafted JSON with 2-3 effects** → entries appear in
   `EFFECT_LAYERS` after startup; running an effect plays the configured
   layers.
3. **Malformed JSON** (truncated, bad keys, wrong types) → warning logged,
   shaker child does not crash, falls back to empty `EFFECT_LAYERS`.
4. **Schema version mismatch** (e.g. `"version": 999`) → warning logged,
   best-effort load proceeds, valid entries still applied.
5. **`reload_layers()` after editing the file** → new entries take effect
   immediately on the next `effects[name].start()` call without
   restarting the shaker child.
6. **Atomic save**: `save()` followed by `kill -9`-style termination
   never leaves an incomplete `shaker_effects.json` (verified by
   inspecting the `.tmp` mechanism).

## Out of scope for STEP_02

- UI editor (STEP_03).
- Default layer pack content (STEP_04).
- Reset-to-default semantics (STEP_03/04).

## Stop here

After implementing and verifying STEP_02, tick the box in `PLAN_LAYERS.md`
and stop for review.
