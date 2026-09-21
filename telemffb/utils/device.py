#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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

import logging
from dataclasses import dataclass
import re

import telemffb.globals as G

__all__ = [
    "get_device_logo",
    "DEVICE_DISPLAY_NAMES",
    "device_display_name",
    "device_ids_key",
    "format_usb_ids",
    "parse_usb_ids",
    "device_ident_key",
    "device_panel_icon",
    "device_panel_label",
    "usb_ids_from_devpath",
    "DEVICE_ROLES",
    "ALL_ROLES",
    "AUDIO_PREFIX",
    "SHAKER_PSEUDO_PID",
    "AudioOutputInfo",
    "audio_selection_devices",
    "recover_device_identity",
    "directinput_selection_devices",
    "device_pid_key",
    "active_joystick_slot_suffix",
    "multiple_joystick_devices",
    "joystick_device_choices",
]

def get_device_logo(dev_type :str):

    match str.lower(dev_type):
        case 'joystick':
            if G.useDarkMode:
                _device_logo = ':/image/logo_j_dm.png'
            else:
                _device_logo = ':/image/logo_j.png'
        case 'pedals':
            if G.useDarkMode:
                _device_logo = ':/image/logo_p_dm.png'
            else:
                _device_logo = ':/image/logo_p.png'
        case 'collective':
            if G.useDarkMode:
                _device_logo = ':/image/logo_c_dm.png'
            else:
                _device_logo = ':/image/logo_c.png'
        case 'trimwheel':
            if G.useDarkMode:
                _device_logo = ':/image/logo_t_dm.png'
            else:
                _device_logo = ':/image/logo_t.png'
        case _:
            if G.useDarkMode:
                _device_logo = ':/image/logo_j_dm.png'
            else:
                _device_logo = ':/image/logo_j.png'
    return _device_logo


DEVICE_DISPLAY_NAMES = {
    'joystick': 'Joystick',
    'pedals': 'Pedals',
    'collective': 'Collective',
    'trimwheel': 'Trim Wheel',
    'shaker': 'Shaker',
}


def device_display_name(role):
    """The name to show a user for a device role."""
    return DEVICE_DISPLAY_NAMES.get(role, str(role).capitalize())


def device_ids_key(role):
    """The settings key holding a device role's USB vendor and product ids.

    Stored as the device reports them rather than parsed back out of its
    path.  A DirectInput device's path is its DirectInput instance GUID,
    which carries no ids at all - so deriving them from the path works for
    HID devices and silently fails for the DirectInput ones.
    """
    return 'devids_' + role


def format_usb_ids(vid, pid):
    """``VVVV:PPPP``, or empty when the device did not report real ids."""
    if not vid or not pid:
        return ''
    return f'{int(vid):04X}:{int(pid):04X}'


def parse_usb_ids(value):
    """The (vid, pid) in a stored ``VVVV:PPPP``, or None."""
    match = re.fullmatch(r'([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})', str(value or ''))
    return (int(match.group(1), 16), int(match.group(2), 16)) if match else None


def device_ident_key(role):
    """The settings key holding a device role's product name.

    A display hint only.  Owners rename VPforce devices and vendors reword
    models, so this can be stale; USB ids are what anything functional is
    keyed on.
    """
    return 'devident_' + role


def device_panel_icon(role, settings, slot_suffix=''):
    """Resource path for a role's status icon, or '' for the role default.

    The joystick role's devices each carry an icon choice (stored as
    devicon_joystick / devicon_joystick_2 ...); the ACTIVE device's choice
    is what the panel shows, and it follows the device through swaps.
    ``slot_suffix`` reads an alternate slot's choice - what the panel
    needs while a per-aircraft swap has that slot's device in hand.
    """
    kind = str(settings.get(f'devicon_{role}{slot_suffix}', '') or '')
    if kind == 'yoke':
        return ':/image/icon_yoke.png'
    return ''


def device_panel_label(role, settings, max_chars=14, slot_suffix=''):
    """A short device name for the role's status icon, or '' when unknown.

    VPforce idents are the owner's own Configurator names and already
    short; a generic DirectInput ident is the full product string
    ("Microsoft SideWinder Force Feedback 2"), so it is cut to its first
    word or two.  Either way the result fits under the icon.
    ``slot_suffix`` reads an alternate slot's identity - what the panel
    needs while a per-aircraft swap has that slot's device in hand.
    """
    ident = str(settings.get(
        device_ident_key(role) + slot_suffix, '') or '').strip()
    if not ident:
        return ''
    devpath = str(settings.get(f'devpath_{role}{slot_suffix}', '') or '')
    if devpath.startswith('dinput:'):
        words = ident.split()
        # the selector lists these as '[DI] name' and the ident was stored
        # that way; the marker is not part of the device's name
        if words and words[0].strip('[]').upper() == 'DI':
            words = words[1:]
        short = words[0] if words else ident
        for word in words[1:]:
            if len(short) + 1 + len(word) > max_chars:
                break
            short += ' ' + word
        ident = short
    if len(ident) > max_chars:
        ident = ident[:max_chars - 1] + '…'
    return ident


def usb_ids_from_devpath(devpath):
    r"""The (vid, pid) a Windows device path names, or None.

    Paths look like \\?\HID#VID_FFFF&PID_2054&MI_00#... - the ids are in
    there, which is what lets a rule be written for a device that is not
    currently plugged in.
    """
    if not devpath:
        return None
    # case-insensitive: Windows writes these uppercase, but a path that
    # has been round-tripped through other tooling may not be
    match = re.search(r'VID_([0-9A-F]{4})&PID_([0-9A-F]{4})', str(devpath),
                      re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1), 16), int(match.group(2), 16)


#: Slots TelemFFB can configure, in the order they are shown.
DEVICE_ROLES = ('joystick', 'pedals', 'collective', 'trimwheel')

#: Every role an instance can run as: the force feedback roles and the
#: bass shaker, which drives a sound card rather than a game controller.
ALL_ROLES = DEVICE_ROLES + ('shaker',)

#: Prefix a shaker selection carries in the devpath_* settings, ahead of
#: the audio output's name ('audio:' alone means the system default).
AUDIO_PREFIX = 'audio:'

#: A shaker has no USB product id; this stands in wherever a role's pid
#: is used as an identity (the child's IPC port, the device beacon) and
#: is small enough that 60000 + pid stays a valid port.
SHAKER_PSEUDO_PID = 0x1001


@dataclass
class AudioOutputInfo:
    """An audio output as the device selectors list it: the same
    attributes the HID and DirectInput entries carry, so the selector
    model, the identity storage and the devpath round trip need no
    special case."""
    name: str
    product_string: str = ''
    vendor_id: int = 0
    product_id: int = SHAKER_PSEUDO_PID
    serial_number: str = ''
    path: bytes = b''

    def __post_init__(self):
        if not self.product_string:
            self.product_string = self.name or 'System default output'
        if not self.path:
            self.path = (AUDIO_PREFIX + self.name).encode()

    @property
    def ident(self) -> str:
        return self.product_string.strip()


def audio_selection_devices():
    """Audio outputs, as the shaker's device selector lists them: the
    system default first, then each card once under its preferred host
    API.  Empty, with the cause logged, when the audio library is not
    usable - a build without it must not take the settings dialog down."""
    try:
        from telemffb.hw.shaker_synth import SoundDeviceOutput
        listed = [AudioOutputInfo('')]
        for dev in SoundDeviceOutput.list_devices():
            listed.append(AudioOutputInfo(dev.name))
        return listed
    except Exception as e:
        logging.error(f"Audio outputs could not be enumerated for the shaker: {e}")
        return []


def recover_device_identity(settings, devices):
    """What a configured slot should remember but does not, taken from the
    hardware that is connected right now.

    Returns ``{settings_key: value}`` for every ``devids_``/``devident_``
    key that is empty while its slot's ``devpath_`` names a device in
    ``devices``.  Empty when nothing is missing or nothing is connected to
    learn it from.

    Two things produce these gaps and neither is the user's doing: a
    settings base written before the keys existed, and first-launch
    auto-assignment, which only ever wrote the path.  The ids are the
    device's identity - a rule or a comparison keyed on a slot with no ids
    silently matches nothing - and the name is how a dialog shows the
    device, which without it reads as "unnamed device" next to hardware
    that is plugged in and working.

    Only empty keys are filled.  A stored name is the one that was on the
    device when the slot was set, and a rename the owner has not reselected
    through yet is theirs to keep until they do.  ``devices`` need only
    carry ``path``, ``vendor_id``, ``product_id`` and ``ident``.
    """
    by_path = {}
    for device in devices:
        path = getattr(device, 'path', None)
        if isinstance(path, (bytes, bytearray)):
            path = path.decode(errors='replace')
        if path:
            by_path[str(path)] = device

    updates = {}
    for role in DEVICE_ROLES:
        path = str(settings.get(f'devpath_{role}', '') or '')
        device = by_path.get(path)
        if device is None:
            continue
        key = device_ids_key(role)
        if not settings.get(key, ''):
            ids = format_usb_ids(getattr(device, 'vendor_id', 0),
                                 getattr(device, 'product_id', 0))
            if ids:
                updates[key] = ids
        key = device_ident_key(role)
        if not settings.get(key, ''):
            ident = str(getattr(device, 'ident', '') or '')
            if ident:
                updates[key] = ident
    return updates


def directinput_selection_devices(settings, enabled=None):
    """Generic DirectInput FFB devices, as the device selectors list them.

    Empty unless DirectInput support is on: with it off no [DI] entry
    appears anywhere and the bridge is never loaded, so a stock install
    behaves exactly as it did before the backend existed.  ``enabled``
    overrides the stored setting so a dialog can re-list live as the box
    is ticked.

    VPforce hardware also enumerates as a DirectInput FFB device, so VID
    0xFFFF is filtered out - those entries come from the native HID
    enumeration.  Debug override: the registry value ``vpforce_as_dinput``
    = 1 under HKCU\\Software\\VPforce\\TelemFFB lists VPforce devices as
    [DI] entries too, so a Rhino can be driven through the DirectInput
    backend as a second test implementation.

    The devpath encodes the backend (``dinput:{GUID}``) so a selection
    flows through the existing devpath_* persistence untouched - and so a
    stored slot can be matched back to the device it names, at startup or
    in a dialog, by the same string.
    """
    if enabled is None:
        enabled = settings.get('enableDirectInput', False)
    if not enabled:
        return []
    try:
        from telemffb.hw.ffb_dinput import DInputFFBDevice

        # registry values arrive as strings; bool('0') is True
        flag = str(settings.get('vpforce_as_dinput', '')).strip().lower()
        include_vpforce = flag in ('1', 'true', 'yes', 'on')
        listed = []
        for dev in DInputFFBDevice.enumerate():
            if dev.vendor_id == 0xFFFF and not include_vpforce:
                continue
            dev.product_string = f"[DI] {dev.product_string}"
            dev.path = f"dinput:{dev.guid}".encode()
            listed.append(dev)
        return listed
    except Exception as e:
        # Support is explicitly on at this point, so silence would just
        # look like "my device is missing".  The usual cause is the
        # separately distributed bridge DLL not being installed.
        logging.error(
            f"DirectInput support is enabled but no devices could be "
            f"enumerated: {e}. DirectLink is required - see the "
            "TelemFFB DirectInput documentation.")
        return []


def device_pid_key(role):
    """The settings key holding a device role's USB product ID."""
    return 'pid' + device_display_name(role).replace(' ', '')


def active_joystick_slot_suffix(settings, devpath):
    """Which configured joystick slot holds this devpath: '' (primary),
    '_2', '_3' - or None when no slot matches (device-less, or identity
    that predates the slots).  How the status panel finds the icon and
    name for whatever a per-aircraft swap actually put in hand."""
    for suffix in ('', '_2', '_3'):
        stored = str(settings.get(f'devpath_joystick{suffix}', '') or '')
        if stored and stored == str(devpath or ''):
            return suffix
    return None


def multiple_joystick_devices(settings=None):
    """Whether the joystick role has more than one device configured.

    The gate for the per-aircraft device selection: a single-device rig
    has nothing to choose between, so that setting and its section
    header disappear from the form and from runtime resolution.  A
    stored preference goes inert, not lost - it resurfaces when a
    second device is configured again.  Never hides on plumbing
    failure: an unreadable settings store returns True, which shows
    the setting rather than silently dropping it.
    """
    try:
        settings = settings if settings is not None else G.system_settings
        return len(joystick_device_choices(settings)) > 1
    except Exception:
        return True


def joystick_device_choices(settings):
    """The joystick role's configured devices, as (devpath, label) pairs.

    What the per-aircraft device dropdown offers: the stored value is the
    devpath (the slot system's own unique handle - a product name cannot
    tell two identical side sticks apart), the label is the human side
    (stored ident, with the USB ids appended to disambiguate twins).
    Slot order: primary first, then the alternates; the primary's label
    carries a trailing ' *' so the list shows which named device the
    'Primary (default)' entry currently resolves to (the setting's
    tooltip explains the mark).
    """
    choices = []
    for suffix in ('', '_2', '_3'):
        path = str(settings.get(f'devpath_joystick{suffix}', '') or '')
        if not path:
            continue
        ident = str(settings.get(f'devident_joystick{suffix}', '') or '')
        ids = str(settings.get(f'devids_joystick{suffix}', '') or '')
        label = ident or 'Unnamed device'
        if ids:
            label = f'{label} ({ids})'
        if suffix == '':
            label += ' *'
        choices.append((path, label))
    return choices
