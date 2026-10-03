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

"""Buttons from generic HID game controllers.

Any joystick, gamepad or multi-axis controller on the PC that TelemFFB does
not drive as an FFB device can supply buttons: button boxes, throttles,
non-FFB sticks.  This module lists those controllers, reads their input
reports through hidapi and decodes the pressed buttons with the Windows HID
parser (``hid.dll``).

hidapi opens Windows HID handles with shared access, so a sim reading the
same controller keeps working while TelemFFB reads it.

The listing thread reads the configured device slots and the ignored list
from the system settings (QSettings) off the main thread.  Reads only, as
other modules already do; writes stay on the main thread.

Layers, bottom up:

* :func:`enumerate_button_devices` lists the controllers (no handles opened).
* :class:`HidReportDecoder` turns one input report into the set of pressed
  button numbers, with each hat switch adding four virtual buttons.
* :class:`ButtonDeviceReader` reads one controller on a daemon thread and
  reports changes of its pressed set.
* :class:`ButtonDeviceManager` owns a reader per controller, rescans for
  hot-plugged ones and delivers changes on the Qt main thread.
"""

import ctypes
import json
import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot

import telemffb.globals as G
import telemffb.hw.hid as hid
from telemffb.hw.button_refs import NAMES_SETTING, stored_device_names
from telemffb.utils.device import (DEVICE_ROLES, device_ids_key,
                                   parse_usb_ids)

__all__ = [
    "IGNORED_SETTING",
    "ButtonDeviceInfo",
    "assign_keys",
    "ignored_keys",
    "enumerate_button_devices",
    "hat_buttons",
    "HidReportDecoder",
    "ButtonDeviceReader",
    "ButtonDeviceManager",
]

#: Global system setting: comma-separated device keys the user turned off.
IGNORED_SETTING = 'buttonDevicesIgnored'

#: Generic Desktop usage page and the top-level usages read as controllers.
_USAGE_PAGE_GENERIC_DESKTOP = 0x01
_CONTROLLER_USAGES = (0x04, 0x05, 0x08)    # joystick, gamepad, multi-axis
_USAGE_PAGE_BUTTON = 0x09
_USAGE_HAT_SWITCH = 0x39

_VPFORCE_VID = 0xFFFF

#: Windows marks the HID interface of an XInput controller with ``IG_nn`` in
#: its device path.
_XINPUT_PATH = re.compile(r'&IG_[0-9A-F]{2}', re.IGNORECASE)

#: Configured FFB slots per role; only the joystick role has alternates, the
#: others never store the suffixed keys.
_SLOT_SUFFIXES = ('', '_2', '_3')

#: Smallest read buffer handed to hidapi, whatever the descriptor says.
_MIN_READ_SIZE = 64
_READ_TIMEOUT_MS = 50
_JOIN_TIMEOUT_S = 1.0
_RESCAN_INTERVAL_MS = 3000


# ---------------------------------------------------------------------------
# Windows HID parser declarations (hidpi.h / hidsdi.h), 64-bit layout.
# Only declared here; the DLLs are loaded on first use by HidReportDecoder.
# ---------------------------------------------------------------------------

_USAGE = ctypes.c_ushort
_NTSTATUS = ctypes.c_long

_HidP_Input = 0

_HIDP_STATUS_SUCCESS = 0x00110000
_HIDP_STATUS_INVALID_REPORT_LENGTH = 0xC0110003
_HIDP_STATUS_USAGE_NOT_FOUND = 0xC0110004
_HIDP_STATUS_BUFFER_TOO_SMALL = 0xC0110007
_HIDP_STATUS_INCOMPATIBLE_REPORT_ID = 0xC011000A
_HIDP_STATUS_REPORT_DOES_NOT_EXIST = 0xC0110010

_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _HIDP_CAPS(ctypes.Structure):
    """``HIDP_CAPS``, 64 bytes."""
    _fields_ = [
        ('Usage', _USAGE),
        ('UsagePage', _USAGE),
        ('InputReportByteLength', ctypes.c_ushort),
        ('OutputReportByteLength', ctypes.c_ushort),
        ('FeatureReportByteLength', ctypes.c_ushort),
        ('Reserved', ctypes.c_ushort * 17),
        ('NumberLinkCollectionNodes', ctypes.c_ushort),
        ('NumberInputButtonCaps', ctypes.c_ushort),
        ('NumberInputValueCaps', ctypes.c_ushort),
        ('NumberInputDataIndices', ctypes.c_ushort),
        ('NumberOutputButtonCaps', ctypes.c_ushort),
        ('NumberOutputValueCaps', ctypes.c_ushort),
        ('NumberOutputDataIndices', ctypes.c_ushort),
        ('NumberFeatureButtonCaps', ctypes.c_ushort),
        ('NumberFeatureValueCaps', ctypes.c_ushort),
        ('NumberFeatureDataIndices', ctypes.c_ushort),
    ]


class _CapsRange(ctypes.Structure):
    """The ``Range`` arm of the button and value caps unions."""
    _fields_ = [
        ('UsageMin', _USAGE),
        ('UsageMax', _USAGE),
        ('StringMin', ctypes.c_ushort),
        ('StringMax', ctypes.c_ushort),
        ('DesignatorMin', ctypes.c_ushort),
        ('DesignatorMax', ctypes.c_ushort),
        ('DataIndexMin', ctypes.c_ushort),
        ('DataIndexMax', ctypes.c_ushort),
    ]


class _CapsNotRange(ctypes.Structure):
    """The ``NotRange`` arm of the button and value caps unions."""
    _fields_ = [
        ('Usage', _USAGE),
        ('Reserved1', _USAGE),
        ('StringIndex', ctypes.c_ushort),
        ('Reserved2', ctypes.c_ushort),
        ('DesignatorIndex', ctypes.c_ushort),
        ('Reserved3', ctypes.c_ushort),
        ('DataIndex', ctypes.c_ushort),
        ('Reserved4', ctypes.c_ushort),
    ]


class _CapsUnion(ctypes.Union):
    _fields_ = [
        ('Range', _CapsRange),
        ('NotRange', _CapsNotRange),
    ]


class _HIDP_BUTTON_CAPS(ctypes.Structure):
    """``HIDP_BUTTON_CAPS``, 72 bytes.

    Older SDKs declare ``ULONG Reserved[10]`` where newer ones split the first
    element into ``ReportCount``/``Reserved2``; the layout is identical.
    """
    _anonymous_ = ('u',)
    _fields_ = [
        ('UsagePage', _USAGE),
        ('ReportID', ctypes.c_ubyte),
        ('IsAlias', ctypes.c_ubyte),
        ('BitField', ctypes.c_ushort),
        ('LinkCollection', ctypes.c_ushort),
        ('LinkUsage', _USAGE),
        ('LinkUsagePage', _USAGE),
        ('IsRange', ctypes.c_ubyte),
        ('IsStringRange', ctypes.c_ubyte),
        ('IsDesignatorRange', ctypes.c_ubyte),
        ('IsAbsolute', ctypes.c_ubyte),
        ('ReportCount', ctypes.c_ushort),
        ('Reserved2', ctypes.c_ushort),
        ('Reserved', ctypes.c_ulong * 9),
        ('u', _CapsUnion),
    ]


class _HIDP_VALUE_CAPS(ctypes.Structure):
    """``HIDP_VALUE_CAPS``, 72 bytes."""
    _anonymous_ = ('u',)
    _fields_ = [
        ('UsagePage', _USAGE),
        ('ReportID', ctypes.c_ubyte),
        ('IsAlias', ctypes.c_ubyte),
        ('BitField', ctypes.c_ushort),
        ('LinkCollection', ctypes.c_ushort),
        ('LinkUsage', _USAGE),
        ('LinkUsagePage', _USAGE),
        ('IsRange', ctypes.c_ubyte),
        ('IsStringRange', ctypes.c_ubyte),
        ('IsDesignatorRange', ctypes.c_ubyte),
        ('IsAbsolute', ctypes.c_ubyte),
        ('HasNull', ctypes.c_ubyte),
        ('Reserved', ctypes.c_ubyte),
        ('BitSize', ctypes.c_ushort),
        ('ReportCount', ctypes.c_ushort),
        ('Reserved2', ctypes.c_ushort * 5),
        ('UnitsExp', ctypes.c_ulong),
        ('Units', ctypes.c_ulong),
        ('LogicalMin', ctypes.c_long),
        ('LogicalMax', ctypes.c_long),
        ('PhysicalMin', ctypes.c_long),
        ('PhysicalMax', ctypes.c_long),
        ('u', _CapsUnion),
    ]


class _HIDP_DATA(ctypes.Structure):
    """``HIDP_DATA``, 8 bytes; ``RawValue`` overlays the ``On`` flag."""
    _fields_ = [
        ('DataIndex', ctypes.c_ushort),
        ('Reserved', ctypes.c_ushort),
        ('RawValue', ctypes.c_ulong),
    ]


def _status(value: int) -> int:
    """An ``NTSTATUS`` as the unsigned value the HIDP_STATUS_* codes use."""
    return value & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

@dataclass
class ButtonDeviceInfo:
    """One generic HID controller that can supply buttons.

    :ivar key: stable identity, ``VVVV:PPPP`` with a ``#serial`` or ``#n``
        suffix when several listed controllers share the VID:PID.
    :ivar path: the hidapi device path, as hidapi returns it.
    :ivar readable: False once opening or reading the device has failed,
        and from the start for an XInput controller.
    :ivar ignored: True when the user turned the device off.
    :ivar usage: the top-level Generic Desktop usage (4, 5 or 8).
    :ivar xinput: an XInput controller's HID interface.  It lists buttons
        and a hat but never delivers input reports to a HID reader, so it
        is never opened.
    """
    key: str
    path: bytes
    name: str
    vid: int
    pid: int
    serial: str = ''
    readable: bool = True
    ignored: bool = False
    usage: int = 0
    xinput: bool = False


def _usb_ids(vid: int, pid: int) -> str:
    return f'{int(vid):04X}:{int(pid):04X}'


def _key_suffix(serial: str) -> str:
    """A serial made safe for a key.

    Consumers append ``:<button>`` and split on the last colon, and the
    ignored list is comma-separated, so neither character may appear.
    """
    return serial.replace(':', '_').replace(',', '_')


def assign_keys(infos: list[ButtonDeviceInfo]) -> list[ButtonDeviceInfo]:
    """Set each device's ``key`` and return the list.

    A VID:PID listed once keys as ``VVVV:PPPP``.  Several devices sharing one
    key as ``VVVV:PPPP#<serial>`` when every one of them reports a distinct
    non-empty serial, otherwise as ``VVVV:PPPP#<n>`` numbered from 1 in HID
    path order.  The path encodes the USB port, so two serial-less devices
    of one model can swap numbers after a port or hub change, and their
    bindings move with the numbers; without a serial there is no better key.
    """
    groups: dict[str, list[ButtonDeviceInfo]] = defaultdict(list)
    for info in infos:
        groups[_usb_ids(info.vid, info.pid)].append(info)
    for base, members in groups.items():
        if len(members) == 1:
            members[0].key = base
            continue
        serials = [_key_suffix((member.serial or '').strip()) for member in members]
        if all(serials) and len(set(serials)) == len(serials):
            for member, serial in zip(members, serials):
                member.key = f'{base}#{serial}'
        else:
            ordered = sorted(members, key=lambda member: member.path or b'')
            for number, member in enumerate(ordered, start=1):
                member.key = f'{base}#{number}'
    return infos


def ignored_keys(settings) -> set[str]:
    """The device keys listed in the ``buttonDevicesIgnored`` setting."""
    if settings is None:
        return set()
    raw = str(settings.get(IGNORED_SETTING, '') or '')
    return {part.strip() for part in raw.split(',') if part.strip()}


def _resolve_settings(settings):
    return settings if settings is not None else getattr(G, 'system_settings', None)


def _ffb_identities(settings) -> tuple[set[str], set[tuple[int, int]]]:
    """Paths (lowercased) and VID:PIDs of every configured FFB device."""
    paths: set[str] = set()
    ids: set[tuple[int, int]] = set()
    if settings is None:
        return paths, ids
    for role in DEVICE_ROLES:
        for suffix in _SLOT_SUFFIXES:
            path = str(settings.get(f'devpath_{role}{suffix}', '') or '').strip()
            if path:
                paths.add(path.lower())
            parsed = parse_usb_ids(settings.get(device_ids_key(role) + suffix, ''))
            if parsed:
                ids.add(parsed)
    return paths, ids


def _path_text(path) -> str:
    if isinstance(path, (bytes, bytearray)):
        return bytes(path).decode(errors='replace')
    return str(path or '')


def enumerate_button_devices(settings=None) -> list[ButtonDeviceInfo]:
    """List the HID game controllers that can supply buttons.

    Joysticks, gamepads and multi-axis controllers (Generic Desktop usages 4,
    5 and 8), minus every device TelemFFB drives as an FFB device: VPforce
    hardware (VID 0xFFFF), any configured ``devpath_<role>`` and any stored
    ``devids_<role>`` VID:PID.  Ignored devices are listed with ``ignored``
    set, XInput controllers with ``xinput`` set and ``readable`` False.
    Opens no handles.

    :param settings: a ``SystemSettings``-like object with ``get(key,
        default)``; ``G.system_settings`` when None.
    """
    settings = _resolve_settings(settings)
    ffb_paths, ffb_ids = _ffb_identities(settings)
    infos = []
    for entry in hid.enumerate():
        if entry.get('usage_page') != _USAGE_PAGE_GENERIC_DESKTOP:
            continue
        usage = entry.get('usage')
        if usage not in _CONTROLLER_USAGES:
            continue
        vid = int(entry.get('vendor_id') or 0)
        pid = int(entry.get('product_id') or 0)
        if vid == _VPFORCE_VID or (vid, pid) in ffb_ids:
            continue
        path = entry.get('path') or b''
        if _path_text(path).lower() in ffb_paths:
            continue
        name = str(entry.get('product_string') or '').strip() or _usb_ids(vid, pid)
        xinput = bool(_XINPUT_PATH.search(_path_text(path)))
        infos.append(ButtonDeviceInfo(
            key='', path=path, name=name, vid=vid, pid=pid,
            serial=str(entry.get('serial_number') or ''), usage=int(usage),
            readable=not xinput, xinput=xinput))
    assign_keys(infos)
    ignored = ignored_keys(settings)
    for info in infos:
        info.ignored = info.key in ignored
    return infos


# ---------------------------------------------------------------------------
# Report decoding
# ---------------------------------------------------------------------------

def hat_buttons(value: int, logical_min: int, logical_max: int, base: int) -> frozenset[int]:
    """The virtual buttons a hat switch value presses.

    Each hat owns four buttons from ``base``: up, right, down, left.  The
    logical range is divided into equal compass sectors clockwise from up, so
    an 8-position hat presses both neighbors on a diagonal and a 4-position
    hat presses exactly one.  A value outside the logical range is centered.
    """
    positions = logical_max - logical_min + 1
    if positions <= 0 or value < logical_min or value > logical_max:
        return frozenset()
    offset = value - logical_min
    # nearest eighth of a turn, rounding halves up
    eighth = ((16 * offset + positions) // (2 * positions)) % 8
    pressed = set()
    if eighth in (7, 0, 1):
        pressed.add(base)
    if eighth in (1, 2, 3):
        pressed.add(base + 1)
    if eighth in (3, 4, 5):
        pressed.add(base + 2)
    if eighth in (5, 6, 7):
        pressed.add(base + 3)
    return frozenset(pressed)


@dataclass(frozen=True)
class _Hat:
    report_id: int
    data_index: int
    logical_min: int
    logical_max: int
    bit_size: int
    base: int


class HidReportDecoder:
    """Decodes a controller's input reports into pressed button numbers.

    Real buttons are numbered from their Button-page usage, the numbering
    Windows game controller tools show.  Each hat switch adds four virtual
    buttons (up, right, down, left) after the highest real button, hats in
    (report id, data index) order.

    The device's preparsed data is held for the decoder's lifetime; call
    :meth:`close` to free it.  Not thread-safe: one reader thread owns it.

    :param path: the hidapi device path.
    :raises OSError: when the device cannot be opened or parsed.
    """

    _hid_dll = None
    _kernel32 = None
    _load_lock = threading.Lock()

    @classmethod
    def _libraries(cls):
        """``hid.dll`` and ``kernel32``, loaded and declared on first use."""
        with cls._load_lock:
            if cls._hid_dll is None:
                kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel32.CreateFileW.argtypes = [
                    ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
                kernel32.CreateFileW.restype = ctypes.c_void_p
                kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel32.CloseHandle.restype = ctypes.c_int

                dll = ctypes.WinDLL('hid', use_last_error=True)
                dll.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
                dll.HidD_GetPreparsedData.restype = ctypes.c_ubyte
                dll.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
                dll.HidD_FreePreparsedData.restype = ctypes.c_ubyte
                dll.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
                dll.HidP_GetCaps.restype = _NTSTATUS
                dll.HidP_GetButtonCaps.argtypes = [
                    ctypes.c_int, ctypes.POINTER(_HIDP_BUTTON_CAPS),
                    ctypes.POINTER(ctypes.c_ushort), ctypes.c_void_p]
                dll.HidP_GetButtonCaps.restype = _NTSTATUS
                dll.HidP_GetValueCaps.argtypes = [
                    ctypes.c_int, ctypes.POINTER(_HIDP_VALUE_CAPS),
                    ctypes.POINTER(ctypes.c_ushort), ctypes.c_void_p]
                dll.HidP_GetValueCaps.restype = _NTSTATUS
                dll.HidP_MaxUsageListLength.argtypes = [ctypes.c_int, _USAGE, ctypes.c_void_p]
                dll.HidP_MaxUsageListLength.restype = ctypes.c_ulong
                dll.HidP_MaxDataListLength.argtypes = [ctypes.c_int, ctypes.c_void_p]
                dll.HidP_MaxDataListLength.restype = ctypes.c_ulong
                dll.HidP_GetUsages.argtypes = [
                    ctypes.c_int, _USAGE, ctypes.c_ushort, ctypes.POINTER(_USAGE),
                    ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
                    ctypes.c_char_p, ctypes.c_ulong]
                dll.HidP_GetUsages.restype = _NTSTATUS
                dll.HidP_GetData.argtypes = [
                    ctypes.c_int, ctypes.POINTER(_HIDP_DATA), ctypes.POINTER(ctypes.c_ulong),
                    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong]
                dll.HidP_GetData.restype = _NTSTATUS
                cls._kernel32 = kernel32
                cls._hid_dll = dll
            return cls._hid_dll, cls._kernel32

    def __init__(self, path):
        self._dll, kernel32 = self._libraries()
        self._preparsed = ctypes.c_void_p()
        handle = kernel32.CreateFileW(
            _path_text(path), 0, _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None, _OPEN_EXISTING, 0, None)
        if not handle or handle == _INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._dll.HidD_GetPreparsedData(handle, ctypes.byref(self._preparsed)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.CloseHandle(handle)
        try:
            self._read_caps()
        except Exception:
            self.close()
            raise

    def _check(self, status: int, call: str) -> None:
        if _status(status) != _HIDP_STATUS_SUCCESS:
            raise OSError(f'{call} failed with status 0x{_status(status):08X}')

    def _read_caps(self) -> None:
        dll = self._dll
        caps = _HIDP_CAPS()
        self._check(dll.HidP_GetCaps(self._preparsed, ctypes.byref(caps)), 'HidP_GetCaps')
        self._report_length = int(caps.InputReportByteLength)

        button_caps = []
        count = ctypes.c_ushort(caps.NumberInputButtonCaps)
        if count.value:
            array = (_HIDP_BUTTON_CAPS * count.value)()
            self._check(dll.HidP_GetButtonCaps(_HidP_Input, array, ctypes.byref(count),
                                               self._preparsed), 'HidP_GetButtonCaps')
            button_caps = list(array[:count.value])

        value_caps = []
        count = ctypes.c_ushort(caps.NumberInputValueCaps)
        if count.value:
            array = (_HIDP_VALUE_CAPS * count.value)()
            self._check(dll.HidP_GetValueCaps(_HidP_Input, array, ctypes.byref(count),
                                              self._preparsed), 'HidP_GetValueCaps')
            value_caps = list(array[:count.value])

        # hidapi strips the report-id byte only when the device uses none
        self._numbered = any(cap.ReportID != 0 for cap in button_caps + value_caps)

        self._button_count = 0
        self._button_reports: set[int] = set()
        for cap in button_caps:
            if cap.UsagePage != _USAGE_PAGE_BUTTON:
                continue
            top = cap.Range.UsageMax if cap.IsRange else cap.NotRange.Usage
            self._button_count = max(self._button_count, int(top))
            self._button_reports.add(int(cap.ReportID))

        found = []
        for cap in value_caps:
            if cap.UsagePage != _USAGE_PAGE_GENERIC_DESKTOP:
                continue
            if cap.IsRange:
                if not cap.Range.UsageMin <= _USAGE_HAT_SWITCH <= cap.Range.UsageMax:
                    continue
                data_index = cap.Range.DataIndexMin + (_USAGE_HAT_SWITCH - cap.Range.UsageMin)
            else:
                if cap.NotRange.Usage != _USAGE_HAT_SWITCH:
                    continue
                data_index = cap.NotRange.DataIndex
            found.append((int(cap.ReportID), int(data_index), int(cap.LogicalMin),
                          int(cap.LogicalMax), int(cap.BitSize)))
        found.sort(key=lambda hat: (hat[0], hat[1]))
        self._hats = [_Hat(*hat, base=self._button_count + 1 + 4 * index)
                      for index, hat in enumerate(found)]
        self._hats_by_index = {hat.data_index: hat for hat in self._hats}
        self._hat_reports = {hat.report_id for hat in self._hats}

        length = self._report_length
        self._report = ctypes.create_string_buffer(max(length, 1))
        self._usages = (_USAGE * max(1, int(dll.HidP_MaxUsageListLength(
            _HidP_Input, _USAGE_PAGE_BUTTON, self._preparsed))))()
        self._data = (_HIDP_DATA * max(1, int(dll.HidP_MaxDataListLength(
            _HidP_Input, self._preparsed))))()
        self._by_report: dict[int, frozenset[int]] = {}
        self._pressed: frozenset[int] = frozenset()

    @property
    def input_report_length(self) -> int:
        """``InputReportByteLength``: the report size including the id byte."""
        return self._report_length

    @property
    def button_count(self) -> int:
        """The highest real button number."""
        return self._button_count

    @property
    def hat_count(self) -> int:
        """Hat switches, each adding four virtual buttons."""
        return len(self._hats)

    def decode(self, report: bytes) -> frozenset[int]:
        """The buttons pressed after this input report.

        A device with numbered reports may spread its controls over several
        of them, so the result is the union of the latest state of every
        report id seen.  A report whose id carries no buttons or hats leaves
        the state unchanged.

        :param report: one report as hidapi read it.
        :raises OSError: when the HID parser rejects the report.
        """
        if not self._numbered:
            # Windows hidapi drops the zero report id; HidP_* expect it.
            report = b'\x00' + report
        if not report:
            return self._pressed
        report_id = report[0]
        has_buttons = report_id in self._button_reports
        has_hats = report_id in self._hat_reports
        if not has_buttons and not has_hats:
            return self._pressed

        length = self._report_length
        data = report[:length]
        ctypes.memset(self._report, 0, length)
        ctypes.memmove(self._report, data, len(data))

        pressed: set[int] = set()
        dll = self._dll
        if has_buttons:
            count = ctypes.c_ulong(len(self._usages))
            status = _status(dll.HidP_GetUsages(
                _HidP_Input, _USAGE_PAGE_BUTTON, 0, self._usages, ctypes.byref(count),
                self._preparsed, self._report, length))
            if status == _HIDP_STATUS_SUCCESS:
                pressed.update(self._usages[i] for i in range(count.value))
            elif status not in (_HIDP_STATUS_USAGE_NOT_FOUND, _HIDP_STATUS_INCOMPATIBLE_REPORT_ID):
                raise OSError(f'HidP_GetUsages failed with status 0x{status:08X}')
        if has_hats:
            count = ctypes.c_ulong(len(self._data))
            status = _status(dll.HidP_GetData(
                _HidP_Input, self._data, ctypes.byref(count),
                self._preparsed, self._report, length))
            if status != _HIDP_STATUS_SUCCESS:
                raise OSError(f'HidP_GetData failed with status 0x{status:08X}')
            for i in range(count.value):
                hat = self._hats_by_index.get(self._data[i].DataIndex)
                if hat is None:
                    continue
                value = int(self._data[i].RawValue)
                if hat.logical_min < 0 and hat.bit_size and value & (1 << (hat.bit_size - 1)):
                    value -= 1 << hat.bit_size
                pressed |= hat_buttons(value, hat.logical_min, hat.logical_max, hat.base)

        self._by_report[report_id] = frozenset(pressed)
        self._pressed = frozenset().union(*self._by_report.values())
        return self._pressed

    def close(self) -> None:
        """Free the preparsed data.  Safe to call more than once."""
        if self._preparsed:
            self._dll.HidD_FreePreparsedData(self._preparsed)
            self._preparsed = ctypes.c_void_p()


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def _open_hid_device(path):
    device = hid.Device(path=path)
    device.nonblocking = 0
    return device


class ButtonDeviceReader:
    """Reads one controller on a daemon thread and reports pressed-set changes.

    ``on_change(key, pressed)`` is called from the reader thread with the new
    frozenset whenever it differs from the previous one.  An open or read
    failure marks ``info.readable`` False, logs one warning and ends the
    thread; if anything was pressed a final empty set is reported first, so
    a consumer never holds a button the device can no longer release.

    The thread owns both handles once started and closes them on exit, so
    :meth:`stop` never closes a handle a read is still using.

    :param info: the device to read.
    :param on_change: change callback, run on the reader thread.
    :param decoder_factory: builds the report decoder from the device path.
    :param device_factory: opens the hidapi device from its path.
    """

    def __init__(self, info: ButtonDeviceInfo,
                 on_change: Callable[[str, frozenset], None],
                 decoder_factory: Callable = HidReportDecoder,
                 device_factory: Optional[Callable] = None):
        self.info = info
        self._on_change = on_change
        self._decoder_factory = decoder_factory
        self._device_factory = device_factory or _open_hid_device
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._device = None
        self._decoder = None

    def start(self) -> bool:
        """Open the device and start reading.

        :returns: False when the device could not be opened.
        """
        try:
            self._device = self._device_factory(self.info.path)
            self._decoder = self._decoder_factory(self.info.path)
        except Exception as e:
            self._fail('open', e)
            self._close_handles()
            return False
        size = max(_MIN_READ_SIZE, int(self._decoder.input_report_length or 0))
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, args=(size,), daemon=True,
            name=f'ButtonDeviceReader {self.info.key}')
        self._thread.start()
        return True

    def stop(self) -> None:
        """Ask the thread to finish and wait briefly for it."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(_JOIN_TIMEOUT_S)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self, size: int) -> None:
        pressed: frozenset[int] = frozenset()
        try:
            while not self._stop_event.is_set():
                report = self._device.read(size, _READ_TIMEOUT_MS)
                if not report:
                    continue
                current = self._decoder.decode(report)
                if current != pressed:
                    pressed = current
                    self._on_change(self.info.key, pressed)
        except Exception as e:
            if not self._stop_event.is_set():
                self._fail('read', e)
                if pressed:
                    try:
                        self._on_change(self.info.key, frozenset())
                    except Exception:
                        logging.exception('Button device release callback failed')
        finally:
            self._close_handles()

    def _fail(self, action: str, error: Exception) -> None:
        self.info.readable = False
        logging.warning(f"Button device {self.info.key} ({self.info.name}): "
                        f"{action} failed, device not read: {error}")

    def _close_handles(self) -> None:
        for handle in (self._device, self._decoder):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:
                logging.debug('Closing a button device handle failed', exc_info=True)
        self._device = None
        self._decoder = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ButtonDeviceManager(QObject):
    """Keeps a reader on every enabled controller and publishes their buttons.

    Rescans every few seconds so hot-plugged controllers come and go.  The
    listing runs on a worker thread, since Windows hidapi opens every HID
    interface to read its strings and a sleeping Bluetooth device can hold
    that for seconds; the result is reconciled on the main thread.  A
    device that disappears or is ignored has its pressed buttons released
    through :attr:`buttons_changed` before its key leaves :attr:`states`.
    Both signals are delivered on the thread that owns the manager (the
    main thread).

    :param settings: ``SystemSettings``-like store; ``G.system_settings``
        when None.
    :param enumerate_devices: ``callable(settings) -> list[ButtonDeviceInfo]``.
    :param reader_factory: ``callable(info, on_change)`` returning an object
        with ``start() -> bool`` and ``stop()``.
    """

    #: (key, sorted list of pressed buttons)
    buttons_changed = pyqtSignal(str, object)
    #: the device list, an ignored flag or a readable flag changed
    devices_changed = pyqtSignal()

    _reader_changed = pyqtSignal(object, str, object)
    _scanned = pyqtSignal(object)

    def __init__(self, settings=None,
                 enumerate_devices: Optional[Callable] = None,
                 reader_factory: Optional[Callable] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._settings = settings
        self._enumerate = enumerate_devices or enumerate_button_devices
        self._reader_factory = reader_factory or ButtonDeviceReader
        self._lock = threading.Lock()
        self._devices: list[ButtonDeviceInfo] = []
        self._readers: dict[bytes, object] = {}
        self._reader_keys: dict[bytes, str] = {}
        self._states: dict[str, frozenset[int]] = {}
        self._snapshot: tuple = ()
        self._scan_failed = False
        self._scan_thread: Optional[threading.Thread] = None
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.setInterval(_RESCAN_INTERVAL_MS)
        self._timer.timeout.connect(self.rescan)
        self._reader_changed.connect(self._apply_change, Qt.ConnectionType.QueuedConnection)
        self._scanned.connect(self._reconcile, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        """Open every enabled controller and start rescanning."""
        self._stopped = False
        self.rescan()
        self._timer.start()

    def stop(self) -> None:
        """Stop rescanning and close every reader.

        A listing still running delivers its result after this and is
        dropped.
        """
        self._stopped = True
        self._timer.stop()
        for path in list(self._readers):
            self._stop_reader(path)

    def wait_for_scan(self, timeout: float = 2.0) -> None:
        """Block until the running listing has finished, for tests and the
        probe; the result still needs the event loop to be reconciled."""
        thread = self._scan_thread
        if thread is not None:
            thread.join(timeout)

    def devices(self) -> list[ButtonDeviceInfo]:
        """Every listed controller, including ignored and unreadable ones."""
        return list(self._devices)

    @property
    def states(self) -> dict[str, frozenset[int]]:
        """Pressed buttons per key, for keys with a running reader."""
        with self._lock:
            return dict(self._states)

    def set_ignored(self, key: str, ignored: bool) -> None:
        """Turn a controller off or back on, persisting the choice."""
        settings = _resolve_settings(self._settings)
        keys = ignored_keys(settings)
        if ignored:
            keys.add(key)
        else:
            keys.discard(key)
        if settings is not None:
            settings.setValue(IGNORED_SETTING, ','.join(sorted(keys)))
        for info in self._devices:
            if info.key != key:
                continue
            info.ignored = ignored
            if ignored and info.path in self._readers:
                self._stop_reader(info.path)
            elif not ignored and info.readable and info.path not in self._readers:
                self._start_reader(info)
        self._publish_devices()

    @pyqtSlot()
    def rescan(self) -> None:
        """List the controllers on a worker thread, then reconcile.

        A listing already in progress is left to finish; the next timer
        tick scans again.
        """
        if self._stopped or (self._scan_thread is not None and self._scan_thread.is_alive()):
            return
        settings = _resolve_settings(self._settings)

        def scan():
            try:
                result = list(self._enumerate(settings))
            except Exception as e:
                result = e
            self._scanned.emit(result)

        self._scan_thread = threading.Thread(target=scan, daemon=True,
                                             name='ButtonDeviceManager scan')
        self._scan_thread.start()

    @pyqtSlot(object)
    def _reconcile(self, listed) -> None:
        """Match the readers to a fresh listing (main thread)."""
        if self._stopped:
            return
        if isinstance(listed, Exception):
            if not self._scan_failed:
                logging.error(f'Listing button devices failed: {listed}')
            self._scan_failed = True
            return
        self._scan_failed = False

        settings = _resolve_settings(self._settings)
        ignored = ignored_keys(settings)
        previous = {info.path: info for info in self._devices}
        for info in listed:
            info.ignored = info.key in ignored
            old = previous.get(info.path)
            # a failed device is not retried until it is replugged
            if old is not None and old.key == info.key and not old.readable:
                info.readable = False
        by_path = {info.path: info for info in listed}

        for path in list(self._readers):
            info = by_path.get(path)
            if (info is None or info.ignored or not info.readable
                    or info.key != self._reader_keys.get(path)):
                self._stop_reader(path)

        self._devices = listed
        for info in listed:
            if info.path not in self._readers and info.readable and not info.ignored:
                self._start_reader(info)
        self._publish_devices()

    def _publish_devices(self) -> None:
        snapshot = tuple((info.path, info.key, info.name, info.readable, info.ignored)
                         for info in self._devices)
        if snapshot != self._snapshot:
            self._snapshot = snapshot
            self._remember_names()
            self.devices_changed.emit()

    def _remember_names(self) -> None:
        """Merge the listed devices' names into ``buttonDeviceNames``.

        Entries are never removed, so a binding to an unplugged device keeps
        its name.  A name that is only the VID:PID fallback is not stored:
        the key itself reads better.  Writes only when the map changed.
        """
        settings = _resolve_settings(self._settings)
        if settings is None:
            return
        stored = stored_device_names(settings)
        names = dict(stored)
        for info in self._devices:
            if info.key and info.name and info.name != _usb_ids(info.vid, info.pid):
                names[info.key] = info.name
        if names == stored:
            return
        try:
            settings.setValue(NAMES_SETTING, json.dumps(names, sort_keys=True))
        except Exception:
            # runs inside the rescan timer slot, where an escaping exception
            # ends the process
            logging.exception('Storing button device names failed')

    def _start_reader(self, info: ButtonDeviceInfo) -> None:
        path = info.path

        def on_change(key, pressed, path=path):
            self._reader_changed.emit(path, key, pressed)

        reader = self._reader_factory(info, on_change)
        if not reader.start():
            info.readable = False
            return
        self._readers[path] = reader
        self._reader_keys[path] = info.key
        with self._lock:
            self._states[info.key] = frozenset()

    def _stop_reader(self, path) -> None:
        reader = self._readers.pop(path, None)
        key = self._reader_keys.pop(path, None)
        if reader is not None:
            reader.stop()
        if key is None:
            return
        with self._lock:
            pressed = self._states.pop(key, frozenset())
        if pressed:
            self.buttons_changed.emit(key, [])

    @pyqtSlot(object, str, object)
    def _apply_change(self, path, key: str, pressed) -> None:
        if self._reader_keys.get(path) != key:
            return     # the reader was stopped after this change was queued
        pressed = frozenset(pressed)
        with self._lock:
            if self._states.get(key) == pressed:
                return
            self._states[key] = pressed
        self.buttons_changed.emit(key, sorted(pressed))
