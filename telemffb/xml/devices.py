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

"""User-config entries that name a device.

The per-aircraft ``joystick_device`` setting stores the devpath of one
of the joystick role's configured devices, so replacing that device in
System Settings is what makes these entries stale.  A read and a write
on one setting: kept together because they are used together - find the
references, show them, rewrite them - and separate from the general
read/write modules because the pairing is about this setting, not about
the config format.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:                                  # pragma: no cover
    import xml.etree.ElementTree as ET

from telemffb.xml.store import XmlStore

#: The aircraft setting these functions are about.
DEVICE_SETTING = 'joystick_device'

#: User-config scopes an entry can live in, broadest last.
SCOPES = ('models', 'classSettings', 'simSettings')


def find_references(store: XmlStore, devpath: str) -> List['ET.Element']:
    """User-config entries whose device setting names this devpath,
    across every scope."""
    root = store.auto_user_root
    if root is None:
        return []
    refs = []
    for tag in SCOPES:
        for elem in root.findall(tag):
            if elem.findtext('name') != DEVICE_SETTING:
                continue
            if (elem.findtext('value') or '') == devpath:
                refs.append(elem)
    return refs


def update_references(store: XmlStore, old_path: str, new_path: str) -> int:
    """Rewrite every reference to a replaced device and persist.
    Returns the number of entries changed."""
    refs = find_references(store, old_path)
    for elem in refs:
        value = elem.find('value')
        if value is not None:
            value.text = new_path
    if refs:
        store.write_userconfig(store.auto_user_tree)
    return len(refs)
