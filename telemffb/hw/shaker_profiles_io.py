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

"""Load and save shaker calibration profile packs."""
import json
import logging
import os
from dataclasses import asdict, fields
from typing import Optional, Tuple

from .shaker_profile import ShakerProfile

CURRENT_VERSION = 1
log = logging.getLogger(__name__)


def _profile_from_dict(d: dict) -> Optional[ShakerProfile]:
    """Build a ShakerProfile from a JSON object, ignoring unknown keys.

    Returns None if the dict is missing the required ``name`` field or fails
    type coercion. Missing optional fields fall back to dataclass defaults.
    """
    if not isinstance(d, dict):
        return None
    known = {f.name for f in fields(ShakerProfile)}
    cleaned = {k: v for k, v in d.items() if k in known}
    if "name" not in cleaned:
        return None
    try:
        return ShakerProfile(**cleaned)
    except TypeError:
        log.exception("Bad profile spec %r — skipping", cleaned.get("name"))
        return None


def load(path: str) -> Tuple[Optional[str], "dict[str, ShakerProfile]"]:
    """Read a profile pack JSON file.

    Returns ``(active_name_or_None, {name: ShakerProfile})``. A missing or
    malformed file returns ``(None, {})``. The shaker child treats an empty
    result as "no user pack; fall back to the bundled default pack".
    """
    if not os.path.exists(path):
        log.info("No shaker profile pack at %s", path)
        return None, {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        log.exception("Failed to read shaker profile pack from %s", path)
        return None, {}

    file_version = data.get("version")
    if file_version != CURRENT_VERSION:
        log.warning(
            "Shaker profile pack version mismatch (%s != %s); attempting load anyway",
            file_version, CURRENT_VERSION,
        )

    out: "dict[str, ShakerProfile]" = {}
    for entry in data.get("profiles", []):
        prof = _profile_from_dict(entry)
        if prof is not None:
            out[prof.name] = prof

    active = data.get("active") if isinstance(data.get("active"), str) else None
    log.info("Loaded %d shaker profiles from %s (active=%r)", len(out), path, active)
    return active, out


def save(path: str,
         active_name: Optional[str],
         profiles: "dict[str, ShakerProfile]") -> None:
    """Atomically persist a profile pack to disk.

    Writes to ``<path>.tmp`` first then ``os.replace`` — survives a crash
    mid-write without leaving a half-written JSON file behind.
    """
    data = {
        "version": CURRENT_VERSION,
        "active": active_name or "",
        "profiles": [asdict(p) for p in profiles.values()],
    }
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    log.info("Saved %d shaker profiles to %s", len(profiles), path)


def get_user_profiles_path() -> Optional[str]:
    """Resolve the user's shaker_profiles.json path.

    Returns None if userconfig_rootpath is not set yet (e.g. shaker child
    started before _setup_config_paths ran). Caller treats None like
    "no file present".
    """
    from telemffb import globals as G
    root = getattr(G, "userconfig_rootpath", "") or ""
    if not root:
        return None
    return os.path.join(root, "shaker_profiles.json")


def get_default_pack_path() -> str:
    """Path to the bundled default shaker_profiles JSON (read-only resource).

    Mirrors shaker_layers_io.get_default_pack_path() — prefers the script
    directory (repo root / exe dir) so an extracted copy wins over the
    PyInstaller bundle.
    """
    import sys as _sys
    _rel = os.path.join("telemffb", "data", "shaker_profiles_default.json")
    if getattr(_sys, "frozen", False):
        bundle_dir = _sys._MEIPASS
        script_dir = os.path.dirname(_sys.executable)
    else:
        bundle_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        script_dir = bundle_dir
    candidate = os.path.join(script_dir, _rel)
    if os.path.isfile(candidate):
        return candidate
    return os.path.join(bundle_dir, _rel)
