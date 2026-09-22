#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2026 Valmantas Palikša.
# Copyright (c) 2026 Micah Frisby
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

"""Where an effect plays on a shaker rig, from the settings tree.

The shaker backend asks, by an effect's name, which transducers should
hear it.  The answer lives in the settings tree, not in a table of
effects: every effect already has an intensity setting (the monitor's
effect translator knows which), every setting sits in a group of the
tree (ground, mechanical, aerodynamics, inertial, weapons), and the
shaker's settings carry one placement per group.  So the path is name,
to setting, to group, to that group's placement, read off the running
aircraft the way every other setting is.  A new effect lands in a group
the moment it has an intensity setting, with no entry anywhere here.

Nothing here decides how an effect sounds; that is the backend's rules.
"""

import logging
import re
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Tuple

import telemffb.globals as G
from telemffb.hw.ffb_shaker import DEFAULT_PLACEMENT_DELAY_MS, Placement, placement_from_choice
from telemffb.utils.misc import EffectTranslator

log = logging.getLogger(__name__)

#: the placement setting for each family the tree groups effects into
CATEGORY_SETTINGS = {
    'ground': 'shaker_placement_ground',
    'mechanical': 'shaker_placement_mechanical',
    'aerodynamics': 'shaker_placement_aerodynamics',
    'inertial': 'shaker_placement_inertial',
    'weapons': 'shaker_placement_weapons',
    'other': 'shaker_placement_other',
}
DELAY_SETTING = 'shaker_placement_delay_ms'

#: what the tree ships as defaults, for before an aircraft is loaded
DEFAULT_CHOICES = {
    'shaker_placement_ground': 'floor then seat back',
    'shaker_placement_mechanical': 'seat',
    'shaker_placement_aerodynamics': 'seat back',
    'shaker_placement_inertial': 'seat',
    'shaker_placement_weapons': 'seat back + floor',
    'shaker_placement_other': 'all',
}

SettingIndex = Dict[str, Tuple[str, str]]

_index: Optional[SettingIndex] = None
_categories: Dict[str, str] = {}


def _defaults_root():
    from telemffb import xmlutils
    root = getattr(xmlutils, 'auto_defaults_root', None)
    if root is not None:
        return root
    path = getattr(G, 'defaults_path', '') or getattr(xmlutils, 'defaults_path', '')
    return ET.parse(path).getroot() if path else None


def index_settings(root) -> SettingIndex:
    """setting name -> (parent group, display grouping) for one tree."""
    index: SettingIndex = {}
    for elem in root.findall('defaults'):
        name = elem.findtext('name')
        if name and name not in index:
            index[name] = (elem.findtext('parentgroup') or '', elem.findtext('grouping') or '')
    return index


def setting_index() -> SettingIndex:
    """The index of the tree this instance runs with, built once it can
    be read; empty (every effect 'other') until then."""
    global _index
    if _index is None:
        root = _defaults_root()
        if root is None:
            return {}
        _index = index_settings(root)
    return _index


def _lookup(index: SettingIndex, setting: str):
    hit = index.get(setting)
    if hit is None:
        # some translator entries are patterns over setting names
        try:
            pattern = re.compile(setting)
        except re.error:
            return None
        hit = next((meta for name, meta in index.items() if pattern.match(name)), None)
    return hit


def category_of(effect_name: Optional[str], index: Optional[SettingIndex] = None) -> str:
    """The family an effect belongs to: the parent group of its intensity
    setting, with the Weapons display group counting as weapons wherever
    the tree files it, and 'other' for an effect with no setting."""
    if not effect_name:
        return 'other'
    cached = index is None
    if cached and effect_name in _categories:
        return _categories[effect_name]
    index = setting_index() if index is None else index
    _display, setting = EffectTranslator.get_translation(effect_name)
    category = 'other'
    hit = _lookup(index, setting) if setting else None
    if hit is not None:
        parent, grouping = hit
        if grouping.strip().lower() == 'weapons':
            category = 'weapons'
        elif parent in CATEGORY_SETTINGS:
            category = parent
    if cached and index:
        _categories[effect_name] = category
    return category


def _setting_value(aircraft, name: str, default):
    value = getattr(aircraft, name, None) if aircraft is not None else None
    return default if value is None or value == '' else value


def current_aircraft():
    manager = getattr(G, 'telem_manager', None)
    return getattr(manager, 'currentAircraft', None) if manager is not None else None


def resolve(effect_name: Optional[str], aircraft=None) -> Placement:
    """The placement for an effect, from the running aircraft's settings
    (the shaker child's own scope of the tree), or the tree's defaults
    before an aircraft is loaded."""
    if aircraft is None:
        aircraft = current_aircraft()
    setting = CATEGORY_SETTINGS[category_of(effect_name)]
    choice = _setting_value(aircraft, setting, DEFAULT_CHOICES[setting])
    try:
        delay = float(_setting_value(aircraft, DELAY_SETTING, DEFAULT_PLACEMENT_DELAY_MS))
    except (TypeError, ValueError):
        delay = DEFAULT_PLACEMENT_DELAY_MS
    return placement_from_choice(str(choice), delay)


def forget_placements(device) -> None:
    """Have a shaker re-resolve where its effects play, after the aircraft
    or its settings changed.  Any other device, or none, is left alone, so
    this can be connected once and outlive device swaps."""
    forget = getattr(device, 'forget_placements', None)
    if callable(forget):
        forget()
