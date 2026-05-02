#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""Load and save shaker effect layer specifications."""
import json
import logging
import os
from dataclasses import asdict
from typing import Optional

from .ffb_shaker import Layer

CURRENT_VERSION = 1
log = logging.getLogger(__name__)


def load(path: str) -> "dict[str, list[Layer]]":
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

    out: "dict[str, list[Layer]]" = {}
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


def save(path: str, effect_layers: "dict[str, list[Layer]]") -> None:
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
