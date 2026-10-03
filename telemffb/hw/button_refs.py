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

"""Button binding ids and the device keys they name.

A button setting holds one of two forms:

* a bare int ``n``: button n on the FFB device in this instance's slot,
  whatever device that currently is;
* a qualified id ``<key>:<n>``: button n on one physical device named by
  its identity.  ``key`` is ``VVVV:PPPP`` with an optional ``#suffix``; a
  generic controller's key comes from
  :func:`telemffb.hw.button_devices.assign_keys`, an FFB device's from
  :func:`ffb_device_key`.

An id never names a slot or a role, so after a device swap it goes inert
rather than following the slot to the new device.  The one exception is
two configured slots holding the same VID:PID (two identical units): their
keys carry a ``#<role>`` suffix, so such a binding does follow its slot
through a swap to another identical unit.
"""

import functools
import json
import re
from typing import Optional

import telemffb.globals as G
from telemffb.utils.device import (DEVICE_ROLES, device_display_name,
                                   device_ident_key, device_ids_key,
                                   format_usb_ids, parse_usb_ids)

__all__ = [
    "NAMES_SETTING",
    "parse_button_ref",
    "format_button_ref",
    "ffb_device_key",
    "button_device_name",
    "button_binding_label",
]

#: Global system setting: a JSON object mapping generic controller keys to
#: product names.  The master's button device manager adds to it and never
#: removes entries, so a name stays known while its device is unplugged and
#: in instances that run no manager.
NAMES_SETTING = 'buttonDeviceNames'

#: Configured FFB slots per role; only the joystick role stores the
#: alternates, the other roles never have the suffixed keys.
_SLOT_SUFFIXES = ('', '_2', '_3')

_KEY_PATTERN = re.compile(r'[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}(#[^:,]+)?')
_NUMBER_PATTERN = re.compile(r'[0-9]+')


@functools.lru_cache(maxsize=256)
def _parse_text(text: str) -> tuple[Optional[str], int]:
    text = text.strip()
    if _NUMBER_PATTERN.fullmatch(text):
        return None, int(text)
    key, sep, number = text.rpartition(':')
    if not sep or not _NUMBER_PATTERN.fullmatch(number) or not _KEY_PATTERN.fullmatch(key):
        return None, 0
    number = int(number)
    return (key, number) if number > 0 else (None, 0)


def parse_button_ref(value) -> tuple[Optional[str], int]:
    """Split a button setting value into its device key and button number.

    The key is None for a bare number (this instance's own device).  The
    split is on the last colon, since a key carries a colon of its own.
    Strings are parsed once and cached, so this is cheap per frame.

    :param value: an int or a string as the settings layer delivers it.
    :returns: ``(key, number)``; ``(None, 0)`` for an empty, zero or
        invalid value.
    """
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return None, value if value > 0 else 0
    if isinstance(value, str):
        return _parse_text(value)
    return None, 0


def format_button_ref(key: str, number: int) -> str:
    """The qualified id naming ``number`` on the device ``key``."""
    return f'{key}:{int(number)}'


def _slot_ids(settings, role: str, suffix: str) -> str:
    """A slot's stored ``VVVV:PPPP`` in canonical case, or ''."""
    parsed = parse_usb_ids(settings.get(device_ids_key(role) + suffix, ''))
    return format_usb_ids(*parsed) if parsed else ''


def ffb_device_key(role: str, settings=None, slot_suffix: str = '') -> str:
    """The device key of the FFB device configured for ``role``.

    ``VVVV:PPPP`` from the slot's stored ``devids_<role>`` setting.  When
    another configured slot (one with a stored device path) holds the same
    VID:PID, ``#<role><slot_suffix>`` is appended so the two stay distinct.

    Reads settings, so call it when something changes, never per frame.

    :param role: ``joystick``, ``pedals``, ``collective`` or ``trimwheel``.
    :param settings: a ``SystemSettings``-like object with ``get(key,
        default)``; ``G.system_settings`` when None.
    :param slot_suffix: ``''`` for the role's primary slot, ``'_2'`` or
        ``'_3'`` for a joystick alternate.
    :returns: the key, or '' when the slot has no stored ids.
    """
    if settings is None:
        settings = getattr(G, 'system_settings', None)
    if settings is None or not role:
        return ''
    ids = _slot_ids(settings, role, slot_suffix)
    if not ids:
        return ''
    for other in DEVICE_ROLES:
        for suffix in _SLOT_SUFFIXES:
            if (other, suffix) == (role, slot_suffix):
                continue
            if not str(settings.get(f'devpath_{other}{suffix}', '') or '').strip():
                continue
            if _slot_ids(settings, other, suffix) == ids:
                return f'{ids}#{role}{slot_suffix}'
    return ids


@functools.lru_cache(maxsize=4)
def _parse_names(raw: str) -> dict:
    """The stored name map; empty when the text is not a JSON object.

    Cached on the raw text, so callers must treat the result as read-only.
    """
    try:
        names = json.loads(raw)
    except ValueError:
        return {}
    return names if isinstance(names, dict) else {}


def stored_device_names(settings=None) -> dict:
    """The ``buttonDeviceNames`` map, read-only.

    :param settings: a ``SystemSettings``-like object; ``G.system_settings``
        when None.
    """
    if settings is None:
        settings = getattr(G, 'system_settings', None)
    if settings is None:
        return {}
    return _parse_names(str(settings.get(NAMES_SETTING, '') or ''))


def _ffb_role_name(key: str, settings) -> str:
    """The product name of the FFB slot whose device key is ``key``, or ''.

    Only slots whose stored VID:PID matches the key's are asked for their
    full key, which keeps this to a handful of settings reads.
    """
    ids = key.partition('#')[0].upper()
    for role in DEVICE_ROLES:
        for suffix in _SLOT_SUFFIXES:
            if _slot_ids(settings, role, suffix) != ids:
                continue
            if ffb_device_key(role, settings, suffix) != key:
                continue
            name = str(settings.get(device_ident_key(role) + suffix, '') or '').strip()
            return name or device_display_name(role)
    return ''


def button_device_name(key: str, settings=None) -> str:
    """A readable name for a device key.

    An FFB device configured in a slot gives that slot's stored product
    name (``devident_<role>``), or the role's display name when none is
    stored.  Any other key gives its name from ``buttonDeviceNames``.
    Reads settings, so call it when a form is built, never per frame.

    :param key: a device key as :func:`parse_button_ref` returns it.
    :param settings: a ``SystemSettings``-like object; ``G.system_settings``
        when None.
    :returns: the name, or '' when the key is unknown.
    """
    if settings is None:
        settings = getattr(G, 'system_settings', None)
    if settings is None or not key:
        return ''
    name = _ffb_role_name(key, settings)
    if name:
        return name
    return str(stored_device_names(settings).get(key, '') or '')


def button_binding_label(value, settings=None) -> str:
    """The text a bind button shows for a button setting value.

    ``Button n`` for a bare number, ``<device name>: Button n`` for a
    qualified id, with the device key standing in for an unknown name.

    :param value: the setting value, an int or a string.
    :param settings: passed to :func:`button_device_name`.
    :returns: the label, or '' when the value binds no button.
    """
    key, number = parse_button_ref(value)
    if not number:
        return ''
    if key is None:
        return f'Button {number}'
    return f'{button_device_name(key, settings) or key}: Button {number}'
