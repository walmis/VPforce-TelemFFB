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

"""The speaker layout Windows has given each audio output.

A shared-mode endpoint's channel count says how many channels a card
has, not what they are: four channels is Quadraphonic or 3.1 depending
on what was picked in the speaker panel, and the fourth channel is a
rear speaker in one and the subwoofer in the other.  The audio engine's
device format carries the speaker mask that settles it.  This reads
that mask for every active output through Core Audio, with ctypes
alone, so the shaker card can name channels by what they drive.

Everything here degrades to "unknown" on any failure or off Windows.
"""

import ctypes
import logging
import struct
import sys
from ctypes import wintypes
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

#: speaker positions in channel order (the order of the mask bits)
SPEAKERS: Tuple[Tuple[int, str], ...] = (
    (0x1, 'Front L'), (0x2, 'Front R'), (0x4, 'Center'), (0x8, 'Subwoofer'),
    (0x10, 'Rear L'), (0x20, 'Rear R'), (0x40, 'Front L of C'), (0x80, 'Front R of C'),
    (0x100, 'Rear C'), (0x200, 'Side L'), (0x400, 'Side R'), (0x800, 'Top C'),
    (0x1000, 'Top Front L'), (0x2000, 'Top Front C'), (0x4000, 'Top Front R'),
    (0x8000, 'Top Rear L'), (0x10000, 'Top Rear C'), (0x20000, 'Top Rear R'),
)


def positions_from_mask(mask: int, channels: int) -> Tuple[str, ...]:
    """The speaker name of each channel, in stream order, for a WAVEFORMAT
    channel mask; empty when the mask does not describe that many
    channels (a card with no mask, or a mask for a different count)."""
    names = [name for bit, name in SPEAKERS if mask & bit]
    if len(names) != channels:
        return ()
    return tuple(names)


# --- Core Audio, by hand -----------------------------------------------------

class GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> 'GUID':
        g = cls()
        ole32 = ctypes.windll.ole32
        if ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g)) != 0:
            raise ValueError(text)
        return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', GUID), ('pid', ctypes.c_uint32)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_uint16), ('r1', ctypes.c_uint16), ('r2', ctypes.c_uint16),
                ('r3', ctypes.c_uint16), ('a', ctypes.c_uint64), ('b', ctypes.c_uint64)]


VT_LPWSTR = 31
VT_BLOB = 65
E_RENDER = 0
DEVICE_STATE_ACTIVE = 1
STGM_READ = 0

CLSID_MMDeviceEnumerator = '{BCDE0395-E52F-467C-8E3D-C4579291692E}'
IID_IMMDeviceEnumerator = '{A95664D2-9614-4F35-A746-DE8DB63617E6}'
PKEY_Device_FriendlyName = ('{A45C254E-DF1C-4EFD-8020-67D146A850E0}', 14)
PKEY_AudioEngine_DeviceFormat = ('{F19F064D-082C-4E27-BC73-6882A1BB8E4C}', 0)


def _method(obj, index: int, *argtypes):
    """Slot ``index`` of a COM object's vtable, as a callable."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    return proto(vtable[index])


def _release(obj) -> None:
    if obj:
        _method(obj, 2)(obj)


def _key(fmtid: str, pid: int) -> PROPERTYKEY:
    return PROPERTYKEY(GUID.of(fmtid), pid)


def _property(store, key: PROPERTYKEY) -> Optional[PROPVARIANT]:
    value = PROPVARIANT()
    if _method(store, 5, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
            store, ctypes.byref(key), ctypes.byref(value)) != 0:
        return None
    return value


def _clear(value: PROPVARIANT) -> None:
    ctypes.windll.ole32.PropVariantClear(ctypes.byref(value))


def _text(value: PROPVARIANT) -> str:
    return ctypes.wstring_at(value.a) if value.vt == VT_LPWSTR and value.a else ''


def _blob(value: PROPVARIANT) -> bytes:
    if value.vt != VT_BLOB:
        return b''
    size = value.a & 0xFFFFFFFF
    return ctypes.string_at(value.b, size) if size and value.b else b''


def _mask_of(fmt: bytes) -> Tuple[int, int]:
    """(channels, speaker mask) from a WAVEFORMATEX(TENSIBLE) blob; the
    mask is 0 for a plain WAVEFORMATEX."""
    if len(fmt) < 18:
        return 0, 0
    tag, channels, _sr, _avg, _align, _bits, cbsize = struct.unpack_from('<HHIIHHH', fmt, 0)
    if tag == 0xFFFE and cbsize >= 22 and len(fmt) >= 24:
        return channels, struct.unpack_from('<I', fmt, 20)[0]
    return channels, 0


def output_formats() -> Dict[str, Tuple[int, int]]:
    """Friendly name -> (channels, speaker mask) of the audio engine's
    format for every active output Windows has; outputs whose format
    carries no mask are left out.  Empty off Windows or when Core Audio
    cannot be asked."""
    if sys.platform != 'win32':
        return {}
    try:
        return _read_formats()
    except Exception as e:
        log.debug(f"speaker layouts could not be read: {e}")
        return {}


def output_layouts() -> Dict[str, Tuple[str, ...]]:
    """Friendly name -> speaker names in channel order, for every output
    whose format names its speakers."""
    layouts = {}
    for name, (channels, mask) in output_formats().items():
        positions = positions_from_mask(mask, channels)
        if positions:
            layouts[name] = positions
    return layouts


def _read_formats() -> Dict[str, Tuple[int, int]]:
    ole32 = ctypes.windll.ole32
    initialized = ole32.CoInitializeEx(None, 0) in (0, 1)       # S_OK, S_FALSE
    enumerator = ctypes.c_void_p()
    formats: Dict[str, Tuple[int, int]] = {}
    try:
        if ole32.CoCreateInstance(ctypes.byref(GUID.of(CLSID_MMDeviceEnumerator)), None, 1,
                                  ctypes.byref(GUID.of(IID_IMMDeviceEnumerator)),
                                  ctypes.byref(enumerator)) != 0:
            return {}
        collection = ctypes.c_void_p()
        if _method(enumerator, 3, ctypes.c_uint32, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
                enumerator, E_RENDER, DEVICE_STATE_ACTIVE, ctypes.byref(collection)) != 0:
            return {}
        try:
            count = ctypes.c_uint32()
            _method(collection, 3, ctypes.POINTER(ctypes.c_uint32))(collection, ctypes.byref(count))
            name_key = _key(*PKEY_Device_FriendlyName)
            format_key = _key(*PKEY_AudioEngine_DeviceFormat)
            for i in range(count.value):
                device = ctypes.c_void_p()
                if _method(collection, 4, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))(
                        collection, i, ctypes.byref(device)) != 0:
                    continue
                try:
                    store = ctypes.c_void_p()
                    if _method(device, 4, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
                            device, STGM_READ, ctypes.byref(store)) != 0:
                        continue
                    try:
                        name = _property(store, name_key)
                        fmt = _property(store, format_key)
                        try:
                            friendly = _text(name) if name else ''
                            channels, mask = _mask_of(_blob(fmt)) if fmt else (0, 0)
                            if friendly and channels and mask:
                                formats[friendly] = (channels, mask)
                        finally:
                            for v in (name, fmt):
                                if v is not None:
                                    _clear(v)
                    finally:
                        _release(store)
                finally:
                    _release(device)
        finally:
            _release(collection)
    finally:
        _release(enumerator)
        if initialized:
            ole32.CoUninitialize()
    return formats
