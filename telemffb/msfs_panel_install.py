#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
"""Detects installed Microsoft Flight Simulator 2020/2024 copies (Microsoft
Store and Steam editions) and installs/updates the bundled VPforce settings
panel into each one's Community folder.

Detection notes (verified against a real machine with both MSFS 2020 and
2024 Microsoft Store editions installed):
  - Store package family name prefixes: 'Microsoft.FlightSimulator_' (2020),
    'Microsoft.Limitless_' (2024) under %LOCALAPPDATA%\\Packages\\.
  - Each Store package's config lives at
    <package>\\LocalCache\\UserCfg.opt.
  - The LAST non-empty line of UserCfg.opt is
    InstalledPackagesPath "<path>" - the Community folder is
    <path>\\Community.

The Steam-edition paths below (library folder app dirnames, and where
UserCfg.opt lives for a non-Store install) are NOT verified against a real
Steam install - this machine only has Store editions. If Steam detection
comes up empty for a real Steam user, that's the first thing to check.
"""
import json
import logging
import os
import re
import shutil
import winreg
from typing import Optional

from telemffb.utils import get_resource_path

_STORE_PACKAGE_PREFIXES = {
    "2020": "Microsoft.FlightSimulator_",
    "2024": "Microsoft.Limitless_",
}
# Unverified - see module docstring.
_STEAM_APP_DIRNAMES = {
    "2020": "Microsoft Flight Simulator",
    "2024": "Microsoft Flight Simulator 2024",
}
_STEAM_USERCFG_DIRNAMES = {
    "2020": "Microsoft Flight Simulator",
    "2024": "Microsoft Flight Simulator 2024",
}

_INSTALLED_PACKAGES_PATH_RE = re.compile(r'InstalledPackagesPath\s+"([^"]+)"')
_VDF_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')


def _panel_source_dir() -> Optional[str]:
    """Directory containing the bundled panel's manifest.json - works both
    running from source (repo root/assets/msfs-panel/...) and frozen
    (matches how install_dcs_export_module/install_xplane_plugin resolve
    their own bundled resources via get_resource_path(prefer_root=True))."""
    manifest_path = get_resource_path(
        os.path.join('assets', 'msfs-panel', 'vpforce-telemffb-panel', 'manifest.json'),
        prefer_root=True,
    )
    if not os.path.isfile(manifest_path):
        return None
    return os.path.dirname(manifest_path)


def _read_manifest_version(manifest_path: str) -> Optional[str]:
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('package_version')
    except (OSError, ValueError):
        return None


def get_bundled_panel_version() -> Optional[str]:
    """package_version of the panel shipped with this copy of TelemFFB."""
    src = _panel_source_dir()
    if not src:
        return None
    return _read_manifest_version(os.path.join(src, 'manifest.json'))


def _community_path_from_usercfg(usercfg_path: str) -> Optional[str]:
    """The last non-empty line of UserCfg.opt is InstalledPackagesPath "<path>"."""
    try:
        with open(usercfg_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError as e:
        logging.debug(f"MSFS detect: couldn't read {usercfg_path}: {e}")
        return None
    if not lines:
        return None
    m = _INSTALLED_PACKAGES_PATH_RE.match(lines[-1])
    if not m:
        logging.warning(
            f"MSFS detect: last line of {usercfg_path} wasn't InstalledPackagesPath: {lines[-1]!r}")
        return None
    return os.path.join(m.group(1), 'Community')


def _find_store_installs() -> list:
    results = []
    packages_dir = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Packages')
    if not os.path.isdir(packages_dir):
        return results
    try:
        entries = os.listdir(packages_dir)
    except OSError:
        return results
    for version, prefix in _STORE_PACKAGE_PREFIXES.items():
        match = next((e for e in entries if e.startswith(prefix)), None)
        if not match:
            continue
        usercfg = os.path.join(packages_dir, match, 'LocalCache', 'UserCfg.opt')
        if os.path.isfile(usercfg):
            results.append({
                'version': version,
                'edition': 'Microsoft Store',
                'usercfg_path': usercfg,
                'community_path': _community_path_from_usercfg(usercfg),
            })
    return results


def _steam_library_paths() -> list:
    """Steam's own install dir (HKCU\\Software\\Valve\\Steam\\SteamPath), plus
    every library folder listed in its steamapps\\libraryfolders.vdf."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
    except OSError:
        return []

    libraries = [steam_path]
    vdf_path = os.path.join(steam_path, 'steamapps', 'libraryfolders.vdf')
    try:
        with open(vdf_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        libraries += _VDF_PATH_RE.findall(content)
    except OSError:
        pass
    return [p.replace('\\\\', '\\') for p in libraries]


def _find_steam_installs() -> list:
    results = []
    libraries = _steam_library_paths()
    for version, dirname in _STEAM_APP_DIRNAMES.items():
        found = any(os.path.isdir(os.path.join(lib, 'steamapps', 'common', dirname)) for lib in libraries)
        if not found:
            continue
        usercfg = os.path.join(os.environ.get('APPDATA', ''), _STEAM_USERCFG_DIRNAMES[version], 'UserCfg.opt')
        usercfg_exists = os.path.isfile(usercfg)
        results.append({
            'version': version,
            'edition': 'Steam',
            'usercfg_path': usercfg if usercfg_exists else None,
            'community_path': _community_path_from_usercfg(usercfg) if usercfg_exists else None,
        })
    return results


def find_msfs_installs() -> list:
    """Every detected MSFS 2020/2024 install (Store and/or Steam). Each entry:
    {'version', 'edition', 'usercfg_path', 'community_path', 'installed_panel_version'}
    community_path/installed_panel_version may be None if UserCfg.opt
    couldn't be found/parsed - the caller should fall back to a manual path."""
    installs = _find_store_installs() + _find_steam_installs()
    for install in installs:
        install['installed_panel_version'] = None
        if install['community_path']:
            manifest = os.path.join(install['community_path'], 'vpforce-telemffb-panel', 'manifest.json')
            install['installed_panel_version'] = _read_manifest_version(manifest)
    return installs


def install_panel(community_path: str) -> None:
    """Copy the bundled panel into <community_path>/vpforce-telemffb-panel,
    overwriting an existing install in place (used for both first install
    and updates)."""
    src = _panel_source_dir()
    if not src:
        raise FileNotFoundError("Bundled MSFS panel source not found")
    dst = os.path.join(community_path, 'vpforce-telemffb-panel')
    os.makedirs(community_path, exist_ok=True)
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns('Build', 'build_layout.py'),
        dirs_exist_ok=True,
    )
