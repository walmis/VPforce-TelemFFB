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

"""Pressed buttons per device key, shared by every instance.

``G.button_states`` maps a device key to the frozenset of its pressed
buttons; qualified binding ids (:mod:`telemffb.hw.button_refs`) resolve
against it.  The master fills it from the generic controllers, its own
FFB device and each child's ``BUTTONS:`` report, and relays every change
to the children as::

    BTNDEV:<key>:<json list of pressed buttons>

Children store what they receive plus their own device's buttons.  Once a
second the master also broadcasts the whole table as::

    BTNDEV_ALL:<json object of key to list>

so a dropped change message heals within a second and a child that starts
while a button is held learns of it.

The master computes a child's device key on the IPC thread, which reads the
stored device ids from the system settings (QSettings) off the main thread.
Reads only, as other modules already do.
"""

import json
import logging
from typing import Iterable, Optional

import telemffb.globals as G
from telemffb.hw.button_refs import ffb_device_key
from telemffb.utils.device import active_joystick_slot_suffix

__all__ = [
    "BTNDEV_PREFIX",
    "BTNDEV_ALL_PREFIX",
    "btndev_message",
    "btndev_all_message",
    "apply_remote_snapshot",
    "parse_btndev_payload",
    "store_buttons",
    "publish_buttons",
    "apply_remote_buttons",
    "publish_role_buttons",
    "publish_own_buttons",
    "role_key",
]

BTNDEV_PREFIX = 'BTNDEV:'
BTNDEV_ALL_PREFIX = 'BTNDEV_ALL:'

#: The key each FFB role's buttons were last stored under.  Written on the
#: main thread (own device) and the IPC thread (children), one role each.
_role_keys: dict[str, str] = {}


def btndev_message(key: str, buttons: Iterable[int]) -> str:
    """The ``BTNDEV:`` IPC message carrying a device's pressed buttons."""
    return f'{BTNDEV_PREFIX}{key}:{json.dumps(sorted(int(b) for b in buttons))}'


def parse_btndev_payload(payload: str) -> Optional[tuple[str, list[int]]]:
    """Split a ``BTNDEV:`` payload (prefix removed) into key and buttons.

    The key holds a colon of its own and the json list holds none, so the
    payload splits on its last colon.

    :returns: ``(key, buttons)``, or None when the payload is malformed.
    """
    key, sep, data = payload.rpartition(':')
    if not sep or not key:
        return None
    try:
        buttons = json.loads(data)
    except ValueError:
        return None
    if not isinstance(buttons, list):
        return None
    try:
        return key, [int(b) for b in buttons]
    except (TypeError, ValueError):
        return None


def store_buttons(key: str, buttons: Iterable[int]) -> None:
    """Record ``key``'s pressed buttons in ``G.button_states``."""
    if key:
        G.button_states[key] = frozenset(buttons)


def publish_buttons(key: str, buttons: Iterable[int], ipc=None) -> None:
    """Store a device's pressed buttons and relay them to the children.

    Master only.  A failed send is logged, never raised: this runs from Qt
    slots and the IPC thread.

    :param ipc: the ``IPCNetworkThread`` to broadcast on;
        ``G.ipc_instance`` when None.
    """
    if not key:
        return
    buttons = sorted(buttons)
    store_buttons(key, buttons)
    if ipc is None:
        ipc = getattr(G, 'ipc_instance', None)
    if ipc is None:
        return
    try:
        ipc.send_broadcast_message(btndev_message(key, buttons))
    except Exception:
        logging.exception(f'Relaying the buttons of {key} failed')


def btndev_all_message() -> str:
    """The ``BTNDEV_ALL:`` IPC message carrying every device's buttons."""
    table = {key: sorted(pressed) for key, pressed in list(G.button_states.items())}
    return f'{BTNDEV_ALL_PREFIX}{json.dumps(table, sort_keys=True)}'


def apply_remote_snapshot(payload: str) -> bool:
    """Replace the stored table with a ``BTNDEV_ALL:`` payload.

    Every key in the payload is stored; a key absent from it is dropped,
    except this instance's own device key, which the master learns of from
    this instance in the first place.

    :returns: False when the payload is malformed (nothing is changed).
    """
    try:
        table = json.loads(payload)
        if not isinstance(table, dict):
            return False
        parsed = {str(key): frozenset(int(b) for b in buttons)
                  for key, buttons in table.items()}
    except (TypeError, ValueError):
        return False
    own = role_key(G.device_type)
    for key in list(G.button_states):
        if key not in parsed and key != own:
            del G.button_states[key]
    G.button_states.update(parsed)
    return True


def apply_remote_buttons(payload: str) -> Optional[str]:
    """Store the buttons a ``BTNDEV:`` payload carries.

    :returns: the device key, or None when the payload is malformed.
    """
    parsed = parse_btndev_payload(payload)
    if parsed is None:
        return None
    key, buttons = parsed
    store_buttons(key, buttons)
    return key


def publish_role_buttons(role: str, buttons: Iterable[int], broadcast: bool,
                         ipc=None, slot_suffix: str = '') -> str:
    """Store an FFB device's buttons under its device key.

    The key is computed from the stored settings on every call, so call
    this on a button change only.  When the role's key differs from the
    one its buttons were last stored under (the device was swapped or
    reconfigured), the old key is released first so nothing stays held.

    :param role: the FFB device's role.
    :param broadcast: relay to the children as well (master only).
    :param ipc: passed to :func:`publish_buttons`.
    :param slot_suffix: the joystick slot the device occupies.
    :returns: the key used, or '' when the role has no stored ids.
    """
    key = ffb_device_key(role, slot_suffix=slot_suffix)
    previous = _role_keys.get(role, '')
    if previous and previous != key and G.button_states.get(previous):
        if broadcast:
            publish_buttons(previous, (), ipc)
        else:
            store_buttons(previous, ())
    if key:
        _role_keys[role] = key
    else:
        _role_keys.pop(role, None)
        return ''
    if broadcast:
        publish_buttons(key, buttons, ipc)
    else:
        store_buttons(key, buttons)
    return key


def publish_own_buttons(buttons: Iterable[int]) -> str:
    """Store this instance's own FFB device buttons under its device key.

    The master also relays them to the children; a child stores them only,
    since it reports its buttons to the master through ``BUTTONS:``.  A
    joystick keys by the slot whose device is in hand, so a per-aircraft
    swap moves its buttons to the swapped-in device's key.

    :returns: the key used, or '' when it could not be determined.
    """
    role = G.device_type
    try:
        settings = getattr(G, 'system_settings', None)
        suffix = ''
        if role == 'joystick' and settings is not None:
            suffix = active_joystick_slot_suffix(
                settings, getattr(G, 'device_devpath', '')) or ''
        return publish_role_buttons(role, buttons, broadcast=bool(G.master_instance),
                                    slot_suffix=suffix)
    except Exception:
        logging.exception('Storing the own device buttons failed')
        return ''


def role_key(role: Optional[str]) -> str:
    """The key an FFB role's buttons were last stored under, or ''."""
    if not role:
        return ''
    return _role_keys.get(role, '')
