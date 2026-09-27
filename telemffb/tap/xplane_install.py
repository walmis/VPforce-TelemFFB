#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
"""X-Plane installs and TelemFFB's two plugins in them: the telemetry plugin
(TelemFFB-XPP, which force feedback needs) and the in-sim panel
(TelemFFB-Panel).

Installs come from X-Plane's own record of them - its installer writes one
per version to %LOCALAPPDATA%\\x-plane_install_<version>.txt, a path a line -
plus any folder the user added, less any detected one the user removed from
the list. Installing is each install's own choice, as
with the MSFS panel: what is there is kept up to date, and a folder without a
plugin is left alone. The one exception is the telemetry plugin when no
install has it at all, which is offered everywhere, since without it there
is no force feedback in X-Plane.
"""
import glob
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional

from telemffb.utils import calculate_crc, get_resource_path

TELEMETRY = "TelemFFB-XPP"
PANEL = "TelemFFB-Panel"
PLUGINS = (TELEMETRY, PANEL)
PLUGIN_NAMES = {TELEMETRY: "Telemetry plugin", PANEL: "Panel"}

#: The installs the user added by hand, as a JSON list of folders
ADDED_SETTING = "xplaneInstalls"
#: The single folder TelemFFB used to know; the first added install, once
LEGACY_SETTING = "pathXPLANE"
#: Detected installs the user removed from the list, as a JSON list of folders
HIDDEN_SETTING = "xplaneHiddenInstalls"

ABSENT, CURRENT, OUTDATED = "absent", "current", "outdated"

_VERSION_RE = re.compile(r"X-Plane (\d+\.\d+(?:\.\d+)?)")


def _plugin_file(root: str, plugin: str) -> str:
    return os.path.join(root, "Resources", "plugins", plugin, "64", "win.xpl")


def bundled_plugin(plugin: str) -> Optional[str]:
    """The copy of ``plugin`` this TelemFFB ships, or None when it has none."""
    path = get_resource_path(os.path.join("xplane-plugin", plugin, "64", "win.xpl"), prefer_root=True)
    return path if os.path.isfile(path) else None


def _same_folder(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def is_install(root: str) -> bool:
    """Whether ``root`` looks like an X-Plane install: it has a plugins folder."""
    return bool(root) and os.path.isdir(os.path.join(root, "Resources", "plugins"))


def detected_installs() -> List[str]:
    """The installs X-Plane's installer recorded, oldest version first."""
    found: List[str] = []
    local = os.environ.get("LOCALAPPDATA", "")
    for record in sorted(glob.glob(os.path.join(local, "x-plane_install_*.txt"))):
        try:
            with open(record, "r", encoding="utf-8", errors="replace") as f:
                lines = [line.strip() for line in f if line.strip()]
        except OSError:
            continue
        for line in lines:
            root = os.path.normpath(line)
            if is_install(root) and not any(_same_folder(root, seen) for seen in found):
                found.append(root)
    return found


def _folder_list(raw) -> List[str]:
    try:
        folders = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(folders, list):
        return []
    return [os.path.normpath(f) for f in folders if isinstance(f, str) and f.strip()]


def added_installs(settings) -> List[str]:
    """The folders the user added by hand. The old single X-Plane folder
    counts as the first, until the list is first saved."""
    raw = settings.get(ADDED_SETTING, None)
    if raw in (None, ""):
        legacy = str(settings.get(LEGACY_SETTING, "") or "").strip()
        return [os.path.normpath(legacy)] if legacy else []
    return _folder_list(raw)


def hidden_installs(settings) -> List[str]:
    """The detected installs the user removed from the list."""
    return _folder_list(settings.get(HIDDEN_SETTING, "[]"))


def contains(folders: List[str], folder: str) -> bool:
    return any(_same_folder(folder, known) for known in folders)


def listed_installs(detected: List[str], added: List[str], hidden: List[str]) -> List[str]:
    """The installs to list and look after: the detected ones not hidden,
    then the ones added. A folder added by hand is listed even if hidden."""
    installs = [root for root in detected if not contains(hidden, root) or contains(added, root)]
    for folder in added:
        if not contains(installs, folder):
            installs.append(folder)
    return installs


def all_installs(settings) -> List[str]:
    """Every install TelemFFB lists, from the saved settings."""
    return listed_installs(detected_installs(), added_installs(settings), hidden_installs(settings))


def sim_version(root: str) -> Optional[str]:
    """The X-Plane version that last ran from ``root``, from its Log.txt."""
    try:
        with open(os.path.join(root, "Log.txt"), "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return None
    match = _VERSION_RE.search(first)
    return f"X-Plane {match.group(1)}" if match else None


def plugin_state(root: str, plugin: str) -> str:
    """ABSENT, or CURRENT / OUTDATED against the copy this TelemFFB ships."""
    installed = _plugin_file(root, plugin)
    if not os.path.isfile(installed):
        return ABSENT
    bundled = bundled_plugin(plugin)
    if bundled is None:
        return CURRENT          # nothing to compare with, nothing to offer
    try:
        return CURRENT if calculate_crc(installed) == calculate_crc(bundled) else OUTDATED
    except OSError:
        return CURRENT


def install_plugin(root: str, plugin: str) -> None:
    """Copy ``plugin`` into ``root``, replacing what is there. Raises OSError
    when it cannot - usually X-Plane running with the plugin loaded."""
    bundled = bundled_plugin(plugin)
    if bundled is None:
        raise FileNotFoundError(f"This TelemFFB does not include the {PLUGIN_NAMES[plugin]}")
    target = _plugin_file(root, plugin)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(bundled, target)
    logging.info(f"Installed the X-Plane {PLUGIN_NAMES[plugin]} into {root}")


@dataclass(frozen=True)
class Offer:
    root: str
    plugin: str
    missing: bool       # an install rather than an update


def startup_offers(installs: List[str], telemetry: bool, panel: bool) -> List[Offer]:
    """What to offer at startup: every plugin installed but out of date, and
    the telemetry plugin everywhere when no install has it yet. ``telemetry``
    and ``panel`` say which plugins TelemFFB is looking after."""
    offers = []
    plugins = [p for p, wanted in ((TELEMETRY, telemetry), (PANEL, panel)) if wanted]
    for root in installs:
        for plugin in plugins:
            if plugin_state(root, plugin) == OUTDATED and bundled_plugin(plugin):
                offers.append(Offer(root, plugin, missing=False))
    if telemetry and installs and bundled_plugin(TELEMETRY) and all(
            plugin_state(root, TELEMETRY) == ABSENT for root in installs):
        offers.extend(Offer(root, TELEMETRY, missing=True) for root in installs)
    return offers
