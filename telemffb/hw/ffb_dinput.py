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

"""Generic DirectInput FFB backend via the TelemFFB-DInput-Bridge DLL.

Speaks to any DirectInput force-feedback device through ``directlink.dlk``
(separate repo: TelemFFB-DInput-Bridge).  All values cross the DLL boundary in
TelemFFB's native +-4096 units; the bridge converts to DirectInput's +-10000.

Differences from the native VPforce backend, hidden behind the
``ffb_backend`` contract:

- Spring center-point telemetry is EMULATED: the device tracks the last
  spring condition written per axis, so ``CP_XY()``/``CP_scaled_axisXY()``
  return the software-known spring center (trim following and helicopter
  force-trim depend on these).  Offsets themselves render natively; the
  coordinate formation that makes them portable across driver families is
  the bridge DLL's concern.
- ``forceXY()`` returns None (no force output telemetry on generic devices).
- ``start(override=True)`` downgrades to a normal start (no sticky-start
  concept in DirectInput); logged once per effect.
- Effect slots are budgeted by priority: conditions + constant force are the
  force model (tier 0), periodics are cues (tier 1).  When the device pool is
  full, the least-recently-started tier-1 effect is evicted and its
  HapticEffect lazily re-creates when it next starts.
- Zero-coefficient condition effects are MUTED device-side (stopped, not
  destroyed) - see ``DInputEffectHandle._sync_device_playing``.
- Condition saturation 0 is translated to full scale: the native backend's
  firmware treats 0 as "unlimited", DirectInput as a zero-force cap.

Debugging (registry values under HKCU\\Software\\VPforce\\TelemFFB):

- 'dinput_trace' = 1 (or the DIB_TRACE=1 env var in dev runs): log every
  bridge effect call (create/update/start/stop/destroy) with decoded
  parameters - the ground truth of what the device is actually told to
  render.
- 'vpforce_as_dinput' = 1: list VPforce hardware as selectable [DI] devices
  in System Settings, so a Rhino can exercise this backend end-to-end as a
  second DirectInput test implementation.
"""

import ctypes
import logging
import json
import os
import sys
import time
import weakref
from dataclasses import dataclass, field
from typing import Dict, List, Optional, override

from PyQt6.QtCore import QTimer, QTimerEvent

import telemffb.globals as G
import telemffb.hw.ffb_backend as ffb_backend
from telemffb.hw.ffb_rhino import (
    EFFECT_CONSTANT, EFFECT_SQUARE, EFFECT_SINE, EFFECT_TRIANGLE,
    EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN, EFFECT_SPRING, EFFECT_DAMPER,
    EFFECT_INERTIA, EFFECT_FRICTION, PERIODIC_EFFECTS, effect_names,
    FFBReport_SetCondition, FFBReport_SetEnvelope,
)
from telemffb.utils import clamp

DIB_ABI_VERSION = 1

DIB_OK = 0
DIB_ERR_GENERAL = -1
DIB_ERR_BAD_ARG = -2
DIB_ERR_ACQUISITION = -3
DIB_ERR_DEVICE_FULL = -4
DIB_ERR_UNSUPPORTED = -5
DIB_ERR_DISCONNECTED = -6

#: Effect types a generic DirectInput device can render.
SUPPORTED_EFFECTS = frozenset([
    EFFECT_CONSTANT, EFFECT_SQUARE, EFFECT_SINE, EFFECT_TRIANGLE,
    EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN, EFFECT_SPRING, EFFECT_DAMPER,
    EFFECT_INERTIA, EFFECT_FRICTION,
])

CONDITION_EFFECTS = frozenset([EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION])

#: CP emulation makes center-point reads meaningful, so the flag is on even
#: though the data is software-tracked rather than device-reported.
DINPUT_CAPABILITIES = ffb_backend.DeviceCapabilities(has_cp_telemetry=True)


# ---------------------------------------------------------------------------
# DLL structures (must match include/dinput_bridge.h, pack(1))
# ---------------------------------------------------------------------------

class DibDeviceState(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("x", ctypes.c_int16),
        ("y", ctypes.c_int16),
        ("z", ctypes.c_int16),
        ("rx", ctypes.c_int16),
        ("ry", ctypes.c_int16),
        ("rz", ctypes.c_int16),
        ("slider0", ctypes.c_int16),
        ("slider1", ctypes.c_int16),
        ("buttons", ctypes.c_uint64),
        ("hats", ctypes.c_uint16),
    ]


class DibCondition(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("active", ctypes.c_int32),
        ("cp_offset", ctypes.c_int32),
        ("positive_coefficient", ctypes.c_int32),
        ("negative_coefficient", ctypes.c_int32),
        ("positive_saturation", ctypes.c_int32),
        ("negative_saturation", ctypes.c_int32),
        ("dead_band", ctypes.c_int32),
    ]


class DibEffectParams(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("direction_deg", ctypes.c_int32),
        ("gain", ctypes.c_int32),
        ("duration_ms", ctypes.c_int32),
        ("envelope_active", ctypes.c_int32),
        ("attack_level", ctypes.c_int32),
        ("attack_time_ms", ctypes.c_int32),
        ("fade_level", ctypes.c_int32),
        ("fade_time_ms", ctypes.c_int32),
        ("condition_x", DibCondition),
        ("condition_y", DibCondition),
        ("periodic_magnitude", ctypes.c_int32),
        ("periodic_offset", ctypes.c_int32),
        ("periodic_phase_deg", ctypes.c_int32),
        ("periodic_period_ms", ctypes.c_int32),
        ("constant_magnitude", ctypes.c_int32),
    ]


class DIBridgeError(Exception):
    pass


def _trace_enabled() -> bool:
    """Bridge call tracing: DIB_TRACE=1 environment variable (dev runs), or
    registry value 'dinput_trace' = 1 under HKCU\\Software\\VPforce\\TelemFFB
    (compiled builds - same pattern as the 'debug' and 'vpforce_as_dinput'
    toggles).  Read directly via winreg: the hw layer stays independent of
    the app's settings machinery."""
    def truthy(value) -> bool:
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    if truthy(os.environ.get("DIB_TRACE", "")):
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\VPforce\TelemFFB") as key:
            value, _ = winreg.QueryValueEx(key, "dinput_trace")
        return truthy(value)
    except OSError:
        return False


def _installed_dll_path() -> Optional[str]:
    """The module path the DirectLink installer recorded, or None.

    Read from HKCU\\Software\\DirectLink, value "Path" - the whole
    interface between TelemFFB and the installer, and the only thing the
    installer has to write.

    A full path to the file rather than a directory, because the installed
    name is the installer's to choose: it may carry a licensee, and it is
    deliberately not a .dll.  Nothing here appends a name or inspects the
    extension, so that stays true whatever the installer ships.  The value
    is named "Path" rather than after any extension for the same reason.

    Read directly via winreg, like _trace_enabled, so the hw layer stays
    independent of the app's settings machinery.  Its own key rather than
    Software\\VPforce: DirectLink is a separate product, and that root
    belongs to TelemFFB.

    Absent is the ordinary case, not a fault - during the beta the DLL is
    dropped into dll/ by hand and no installer has run.
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\DirectLink") as key:
            value, _ = winreg.QueryValueEx(key, "Path")
    except OSError:
        return None
    # A key present but blank - a half-finished install, or one an
    # uninstaller emptied - would otherwise join into the working directory
    # and load whatever was sitting there.
    value = str(value).strip()
    return value or None


def _describe_params(effect_type: int, params: DibEffectParams) -> str:
    """One-line decode of DibEffectParams for the DIB_TRACE log."""
    if effect_type in CONDITION_EFFECTS:
        cx, cy = params.condition_x, params.condition_y
        return (f"coefX={cx.positive_coefficient}/{cx.negative_coefficient}"
                f" cpX={cx.cp_offset} satX={cx.positive_saturation}"
                f" | coefY={cy.positive_coefficient}/{cy.negative_coefficient}"
                f" cpY={cy.cp_offset} satY={cy.positive_saturation}")
    if effect_type == EFFECT_CONSTANT:
        return f"mag={params.constant_magnitude} dir={params.direction_deg}"
    if effect_type in PERIODIC_EFFECTS:
        return (f"mag={params.periodic_magnitude} period={params.periodic_period_ms}ms"
                f" dir={params.direction_deg} dur={params.duration_ms}ms"
                f" env={params.envelope_active}")
    return "?"


#: Where a user obtains the bridge DLL.  Placeholder until the download
#: location is settled - every message that mentions it reads from here, so
#: finalising the address is a one-line change.
BRIDGE_DOWNLOAD_LOCATION = "<location to be determined>"


#: One DIBridge per process (see shared_bridge).  The DLL's state (the
#: DirectInput context, the device and effect tables) is process-global
#: regardless, so extra python-side instances only re-log the build banner
#: and waste an init - and every construction historically ran a fresh
#: availability probe.
_shared_bridge: Optional["DIBridge"] = None


def shared_bridge() -> "DIBridge":
    """The process's DIBridge, created on first use."""
    global _shared_bridge
    if _shared_bridge is None:
        _shared_bridge = DIBridge()
    return _shared_bridge


def bridge_availability(dll_path: Optional[str] = None):
    """Whether the DInput bridge DLL can actually be used.

    Returns (available, reason).  `reason` is written for a message box, not
    a log line, and is empty when available.  The bridge is separately
    distributed, so "missing" is an ordinary state rather than an error -
    but one worth explaining rather than leaving the user with an
    unexplained empty device list.
    """
    try:
        # a fresh construction, deliberately: this is a probe of the
        # current truth (paths, expiry), not a user of the bridge - the
        # shared instance is for the device and enumeration paths
        bridge = DIBridge(dll_path)
    except Exception as e:                    # DIBridgeError, OSError, ...
        # The searched paths are deliberately not shown.  DirectLink is
        # installed by its own installer, so a list of folders TelemFFB
        # looked in answers a question the user cannot act on - and reads
        # as an invitation to drop a file into one of them.  They are in
        # the log for support.
        logging.info("DirectLink not loaded: %s", e)
        # An ABI mismatch or a Windows load error says something the user
        # can act on ("wrong version", "missing dependency"); a plain
        # not-found only repeats the first line, so it is dropped.
        # An ABI mismatch or a Windows load error says something the user
        # can act on ("wrong version", "missing dependency"); a plain
        # not-found only repeats the first line, so it is dropped.
        detail = "" if str(e).startswith("Unable to load") else "\n\n" + str(e)
        # The product is named once at the start and once at the end - a
        # sentence apiece opening with "DirectLink" reads like a form
        # letter.  The gap before the last line separates the diagnosis
        # from what to do about it.
        return False, (
            "DirectLink could not be loaded. It must be installed in order "
            "to enable the integration.{}\n\n\n"
            "You can obtain DirectLink from {}.".format(
                detail, BRIDGE_DOWNLOAD_LOCATION))

    minimum = getattr(G, 'dinput_bridge_min_version', '') or ''
    if minimum:
        version = (bridge.build_info or {}).get("version", "")
        if not version_is_at_least(version, minimum):
            return False, (
                "The installed DirectLink is too old for this version of "
                "TelemFFB.\n\nInstalled: {}\nRequired: {} or newer\n\n"
                "You can obtain the current build from {}.".format(
                    version or "an unidentified build predating 0.9",
                    minimum, BRIDGE_DOWNLOAD_LOCATION))

    expires = (bridge.build_info or {}).get("expires")
    if expires:
        try:
            from datetime import date
            days_left = (date.fromisoformat(expires) - date.today()).days
        except ValueError:
            days_left = None
        if days_left is not None and days_left < 0:
            return False, (
                "The DirectLink build expired on {}.\n\n"
                "Beta builds carry a time limit; the device connection will "
                "be refused until a current build is installed.\n\n"
                "You can obtain the current build from {}.".format(
                    expires, BRIDGE_DOWNLOAD_LOCATION))
    return True, ""


def _version_tuple(version: str):
    """'0.9.2' -> (0, 9, 2), for ordering.  Trailing non-numeric parts
    are dropped rather than guessed at, so '1.0.0-rc1' orders as 1.0.0."""
    parts = []
    for chunk in str(version).split('.'):
        digits = ''
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def version_is_at_least(version: str, minimum: str) -> bool:
    """Whether a bridge version meets the minimum this TelemFFB needs.

    An unreadable or absent version fails the check: builds predating
    the identity export cannot be shown to be new enough, and the point
    of a minimum is to refuse what cannot be vouched for.
    """
    if not minimum:
        return True
    have = _version_tuple(version)
    if not have:
        return False
    want = _version_tuple(minimum)
    # compare on equal length so 0.9 is not read as older than 0.9.0
    length = max(len(have), len(want))
    have += (0,) * (length - len(have))
    want += (0,) * (length - len(want))
    return have >= want


@dataclass
class BridgeStatus:
    """What the settings page says about the bridge utility.

    Presentation-free: the dialog decides the wording and whether the
    state deserves attention (``problem`` non-empty).
    """
    installed: bool = False
    version: str = ""
    built: str = ""
    expires: str = ""            # ISO date, or '' for a build with no fuse
    days_left: Optional[int] = None
    problem: str = ""            # '' when nothing is wrong


def bridge_status(dll_path: Optional[str] = None) -> BridgeStatus:
    """Identity and health of the installed bridge DLL, for display.

    Deliberately quiet: constructing the binding logs the build identity
    (that belongs to the app's own startup, not to a settings page
    repainting), so this reads the same facts without the narration.
    """
    bridge = DIBridge.__new__(DIBridge)
    try:
        bridge._dll = DIBridge._load_library(dll_path)
    except Exception as e:
        detail = str(e)
        return BridgeStatus(
            installed=False,
            problem=("not installed" if detail.startswith("Unable to load")
                     else detail or "not installed"))
    try:
        info = bridge._read_build_info()
    except Exception:
        # the library loaded, so it IS installed - only its identity is
        # unreadable, which is what a pre-identity build looks like
        info = {}
    minimum = getattr(G, 'dinput_bridge_min_version', '') or ''
    if not info:
        # pre-0.9 DLLs predate the build-info export, so they cannot be
        # shown to meet a minimum
        return BridgeStatus(
            installed=True, version="",
            problem=("too old for this TelemFFB, which needs DirectLink "
                     f"{minimum} or newer" if minimum else ""))
    status = BridgeStatus(installed=True,
                          version=str(info.get("version", "")),
                          built=str(info.get("built", "")),
                          expires=str(info.get("expires", "") or ""))
    if not version_is_at_least(status.version, minimum):
        status.problem = (f"version {status.version or '(unknown)'} is older "
                          f"than the {minimum} this TelemFFB needs")
        return status
    if status.expires:
        try:
            from datetime import date
            status.days_left = (date.fromisoformat(status.expires)
                                - date.today()).days
        except ValueError:
            status.problem = f"unreadable expiry date '{status.expires}'"
            return status
        if status.days_left < 0:
            status.problem = f"expired {status.expires}"
    return status


#: Which logical axis each role's effects address.  The joystick is
#: deliberately absent: it stays native X/Y, unmapped, always.
ROLE_LOGICAL_AXIS = {'pedals': 'X', 'collective': 'Y', 'trimwheel': 'X'}

AXIS_SETTING_AUTO = 'auto'


def axis_setting_key(role: str) -> str:
    """Where the role's DirectInput axis choice is stored ('auto' or an
    AXIS_NAMES entry), alongside devpath_{role}."""
    return f'dinput_axis_{role}'


def invert_setting_key(role: str) -> str:
    """Where the role's DirectInput axis-inversion flag is stored - the
    DI-world equivalent of reversing the axis in VPConfigurator, for
    hardware that runs its force axis the other way."""
    return f'dinput_invert_{role}'


def resolve_axis_map(role, choice, actuators):
    """(native axis for logical X, native for logical Y) as AXIS_NAMES
    entries or None-for-unmapped - or None entirely for a role that
    never remaps (the joystick).

    'auto' keeps the role's own axis when the device actuates it (a
    modified stick as a collective: Y exists, identity, done), else
    takes the device's first actuator (Rz-only pedals: X -> RZ, nothing
    to configure).  An explicit choice is applied as given - the user
    can see the pulldown, and it only offers axes the device reported.
    The role's OTHER logical axis keeps its identity mapping when that
    axis exists and is not the one chosen, so two-axis effects keep
    their second dimension where the hardware has one.
    """
    primary = ROLE_LOGICAL_AXIS.get(role)
    if primary is None:
        return None
    actuators = list(actuators or [])
    if not actuators:
        # nothing known - an old DirectLink with no axis query, or a
        # driver that reports no actuators.  Identity: exactly the
        # behavior every install had before the map existed.
        return ('X', 'Y')
    if choice and choice != AXIS_SETTING_AUTO and choice in DIBridge.AXIS_NAMES:
        native = choice
    elif primary in actuators:
        native = primary
    else:
        native = actuators[0]
    secondary_logical = 'Y' if primary == 'X' else 'X'
    secondary = (secondary_logical
                 if secondary_logical in actuators
                 and secondary_logical != native else None)
    if primary == 'X':
        return (native, secondary)
    return (secondary, native)


class DIBridge:
    """ctypes binding to directlink.dlk.

    The device backend takes any object with this method surface, so tests
    substitute a pure-Python fake without touching ctypes.
    """

    @staticmethod
    def installed_location() -> Optional[str]:
        """Where the DirectLink installer said it put the DLL, or None.

        The seam the tests replace, so that a suite never depends on
        whether the machine running it happens to have DirectLink
        installed.  The reading itself is _installed_dll_path.
        """
        return _installed_dll_path()

    @classmethod
    def library_paths(cls):
        """Where DirectLink is looked for, in order.

        An installed copy comes first.  It is the supported arrangement
        once the installer exists, and a DLL left behind in dll/ by an
        earlier beta should not quietly outrank the one the user just
        installed - the version gate would then report a shortfall the
        user has already fixed.

        The rest are worked out when asked rather than at import: frozen
        and source installs keep it in different places, and
        os.path.dirname(__file__) in a PyInstaller build points inside the
        unpacked bundle - a temporary directory, not the installation the
        user drops the DLL into. Matches get_resource_path(prefer_root=True):
        beside the executable first, then whatever shipped in the bundle.
        """
        name = "directlink.dlk"
        installed = cls.installed_location()
        if getattr(sys, "frozen", False):
            beside_exe = os.path.dirname(sys.executable)
            bundled = getattr(sys, "_MEIPASS", beside_exe)
            candidates = [os.path.join(beside_exe, "dll", name),
                          os.path.join(beside_exe, name),
                          os.path.join(bundled, "dll", name)]
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            root = os.path.abspath(os.path.join(here, "..", ".."))
            candidates = [os.path.join(root, "dll", name),
                          os.path.join(root, name)]
        # Deliberately no bare-name fallback: letting Windows search its own
        # DLL path makes "not found" depend on the working directory and on
        # whatever is already loaded in the process, and could pick up a
        # directlink.dlk from somewhere nobody intended.
        if installed:
            candidates.insert(0, installed)
        return tuple(candidates)

    @classmethod
    def _load_library(cls, dll_path: Optional[str] = None):
        """The loaded DLL, ABI checked - shared with bridge_status, which
        wants the same facts without constructing a working binding."""
        paths = (dll_path,) if dll_path else cls.library_paths()
        dll = None
        for p in paths:
            try:
                dll = ctypes.CDLL(p)
                break
            except OSError:
                pass
        if not dll:
            raise DIBridgeError(f"Unable to load directlink.dlk from: {', '.join(paths)}")

        abi = dll.dib_abi_version()
        if abi != DIB_ABI_VERSION:
            raise DIBridgeError(f"directlink.dlk ABI version {abi}, expected {DIB_ABI_VERSION}")
        return dll

    def __init__(self, dll_path: Optional[str] = None):
        self._dll = self._load_library(dll_path)

        # trace logs every effect call with decoded parameters - the ground
        # truth of what the device is actually told to render
        self._trace = _trace_enabled()
        self._effect_types: Dict[int, int] = {}
        if self._trace:
            logging.info("DInput bridge trace enabled: logging all bridge effect calls")

        self.build_info = self._read_build_info()
        self._log_build_identity()

    def _read_build_info(self) -> dict:
        """Version/built/expires of the loaded DLL.  The export is additive
        in ABI 1, so older DLLs simply don't have it."""
        try:
            fn = self._dll.dib_build_info
        except AttributeError:
            return {}
        buf = ctypes.create_string_buffer(256)
        if fn(buf, len(buf)) <= 0:
            return {}
        try:
            return json.loads(buf.value.decode(errors="replace"))
        except ValueError:
            return {}

    def _log_build_identity(self):
        """Log the DLL's identity for support, and warn ahead of a beta
        build's expiry fuse instead of cliff-edge failing on launch day."""
        if not self.build_info:
            logging.info("DInput bridge loaded (no build info export - pre-0.9 build)")
            return
        version = self.build_info.get("version", "?")
        built = self.build_info.get("built", "?")
        expires = self.build_info.get("expires")
        logging.info(f"DInput bridge {version} (ABI {self.build_info.get('abi', '?')}, built {built})")
        if not expires:
            return
        try:
            from datetime import date
            days_left = (date.fromisoformat(expires) - date.today()).days
        except ValueError:
            logging.warning(f"DInput bridge BETA build with unparseable expiry '{expires}'")
            return
        if days_left < 0:
            logging.error(f"DInput bridge BETA build EXPIRED {expires} - "
                          "device connection will be refused; download the current build")
        elif days_left <= 14:
            logging.warning(f"DInput bridge BETA build expires in {days_left} day(s) "
                            f"({expires}) - download the current build soon")
        else:
            logging.info(f"DInput bridge BETA build, expires {expires}")

    def last_error(self) -> str:
        buf = ctypes.create_string_buffer(512)
        self._dll.dib_last_error(buf, len(buf))
        return buf.value.decode(errors="replace")

    def enumerate(self) -> List[dict]:
        buf = ctypes.create_string_buffer(65536)
        n = self._dll.dib_enumerate(buf, len(buf))
        if n < 0:
            raise DIBridgeError(f"enumerate failed: {self.last_error()}")
        return json.loads(buf.value.decode(errors="replace"))

    def open(self, guid: str) -> int:
        h = self._dll.dib_open(guid.encode())
        if h < 0:
            raise DIBridgeError(f"open {guid} failed: {self.last_error()}")
        return h

    def release(self, device: int):
        self._dll.dib_release(device)

    #: dib_autocenter_state bits (dinput_bridge.h)
    AC_OFF_APPLIED = 0x1
    AC_PRIOR_KNOWN = 0x2
    AC_PRIOR_ON = 0x4
    AC_OFF_VERIFIED = 0x8

    def autocenter_state(self, device: int) -> int:
        """How the autocenter handover went for an open device, as
        DIB_AC_* bits - or a negative DIB_ERR_*.  Absent in pre-0.9.2
        DLLs."""
        fn = getattr(self._dll, 'dib_autocenter_state', None)
        if fn is None:
            return DIB_ERR_UNSUPPORTED
        return fn(device)

    #: DIB_AXIS_* codes, in DIJOYSTATE2 order (dinput_bridge.h)
    AXIS_NAMES = ('X', 'Y', 'Z', 'RX', 'RY', 'RZ', 'SL0', 'SL1')
    AXIS_NONE = -1

    def ffb_axes(self, device: int):
        """The open device's force-actuator axes, as names from
        AXIS_NAMES.  Empty when the DLL predates 0.9.3 or errors."""
        fn = getattr(self._dll, 'dib_ffb_axes', None)
        if fn is None:
            return []
        return self._axis_names(fn(device))

    def query_ffb_axes(self, guid: str):
        """The same, for a device that is NOT open - the settings dialog
        asks before anything is held.  Safe while another process holds
        the device (object enumeration needs no acquisition), and a
        device held by THIS process answers from its open-time record."""
        fn = getattr(self._dll, 'dib_query_ffb_axes', None)
        if fn is None:
            return []
        return self._axis_names(fn(guid.encode()))

    @classmethod
    def _axis_names(cls, mask: int):
        if mask <= 0:
            return []
        return [name for bit, name in enumerate(cls.AXIS_NAMES)
                if mask & (1 << bit)]

    def set_axis_map(self, device: int, x_axis: int, y_axis: int,
                     invert_x: bool = False, invert_y: bool = False) -> int:
        """Point logical X/Y at native axes (AXIS_NAMES indexes, or
        AXIS_NONE), optionally inverting a logical axis's direction -
        effects mirrored and the input reading negated, as if the
        hardware ran the other way.  Absent in pre-0.9.3 DLLs."""
        fn = getattr(self._dll, 'dib_set_axis_map', None)
        if fn is None:
            return DIB_ERR_UNSUPPORTED
        return fn(device, x_axis, y_axis, int(invert_x), int(invert_y))

    def device_reset(self, device: int) -> int:
        """Device-level reset: destroy every effect the DEVICE holds -
        reachable through a handle or not - and free the bridge's effect
        entries for it.  Absent in pre-0.9.1 DLLs; reported as a general
        failure there."""
        if self._trace:
            logging.info(f"DIB device_reset dev#{device}")
        fn = getattr(self._dll, 'dib_device_reset', None)
        if fn is None:
            return DIB_ERR_UNSUPPORTED
        return fn(device)

    def poll(self, device: int) -> DibDeviceState:
        state = DibDeviceState()
        rc = self._dll.dib_poll(device, ctypes.byref(state))
        if rc != DIB_OK:
            raise DIBridgeError(f"poll failed ({rc}): {self.last_error()}")
        return state

    def effect_create(self, device: int, effect_type: int, params: DibEffectParams) -> int:
        """Returns a positive effect id, or a negative DIB_ERR_* code
        (DEVICE_FULL is expected and drives eviction, so no exception)."""
        effect_id = self._dll.dib_effect_create(device, effect_type, ctypes.byref(params))
        if self._trace:
            self._effect_types[effect_id] = effect_type
            logging.info(f"DIB create #{effect_id} {effect_names.get(effect_type, effect_type)}: "
                         f"{_describe_params(effect_type, params)}")
        return effect_id

    def effect_update(self, effect: int, params: DibEffectParams) -> int:
        rc = self._dll.dib_effect_update(effect, ctypes.byref(params))
        if self._trace:
            effect_type = self._effect_types.get(effect, 0)
            logging.info(f"DIB update #{effect} {effect_names.get(effect_type, effect_type)} rc={rc}: "
                         f"{_describe_params(effect_type, params)}")
        return rc

    def effect_start(self, effect: int, iterations: int = 1) -> int:
        rc = self._dll.dib_effect_start(effect, iterations, 0)
        if self._trace:
            logging.info(f"DIB start #{effect} rc={rc}")
        return rc

    def effect_stop(self, effect: int) -> int:
        if self._trace:
            logging.info(f"DIB stop #{effect}")
        return self._dll.dib_effect_stop(effect)

    def effect_destroy(self, effect: int) -> int:
        if self._trace:
            logging.info(f"DIB destroy #{effect}")
            self._effect_types.pop(effect, None)
        return self._dll.dib_effect_destroy(effect)


# ---------------------------------------------------------------------------
# Device info / input snapshot
# ---------------------------------------------------------------------------

@dataclass
class DIDeviceInfo:
    """Parallel of ffb_rhino.DeviceInfo for DirectInput devices."""
    guid: str
    product_string: str
    vendor_id: int
    product_id: int
    ff_axes: int = 0
    buttons: int = 0
    povs: int = 0
    effects: List[str] = field(default_factory=list)
    path: bytes = b""

    def vidpid(self) -> str:
        return f"{self.vendor_id:04X}:{self.product_id:04X}"

    @property
    def ident(self) -> str:
        return self.product_string.strip()


class DInputInputSnapshot:
    """Input snapshot with the FFBReport_Input read surface.

    Axis values arrive from the bridge already in +-4096 units.  The center
    point comes from the device's software CP tracking, not the hardware.
    """

    def __init__(self, x, y, buttons, hats, cp_x, cp_y):
        self.X = x
        self.Y = y
        self._buttons = buttons
        self.hats = hats
        self._cp_x = cp_x  # float [-1..1] or None (spring coefficient 0)
        self._cp_y = cp_y

    @property
    def buttons(self) -> int:
        return self._buttons

    def isButtonPressed(self, button_number) -> bool:
        assert (button_number > 0)
        if (self._buttons & (1 << (button_number - 1))) != 0:
            return True
        for i in range(4):
            hat_position = (self.hats >> (i * 4)) & 0xF
            if hat_position != 0xF:
                if button_number == 0x80 | (i << 4) | hat_position:
                    return True
        return False

    def getPressedButtons(self) -> List[int]:
        pressed = [i + 1 for i in range(64) if (self._buttons & (1 << i)) != 0]
        for i in range(4):
            hat_position = (self.hats >> (i * 4)) & 0xF
            if hat_position != 0xF:
                pressed.append(0x80 | (i << 4) | hat_position)
        return pressed

    def axisXY(self) -> tuple:
        return (self.X / 4096.0, self.Y / 4096.0)

    def rawAxisXY(self) -> tuple:
        # no firmware curves on a DirectInput device
        return self.axisXY()

    def axisOverrideActive(self) -> bool:
        return False

    def CP_XY(self) -> tuple:
        return (self._cp_x, self._cp_y)

    def forceXY(self):
        # no force output telemetry on generic devices (caps-gated)
        return None

    def CP_scaled_axisXY(self) -> tuple:
        # identical math to FFBReport_Input.CP_scaled_axisXY
        X, Y = self.axisXY()
        cpX, cpY = self.CP_XY()

        if cpX is None:
            scaled_x = 0
        elif X >= cpX:
            scaled_x = (X - cpX) / (1 - cpX) if cpX < 1 else (X - cpX)
        else:
            scaled_x = (X - cpX) / (cpX + 1) if cpX > -1 else (X - cpX)

        if cpY is None:
            scaled_y = 0
        elif Y >= cpY:
            scaled_y = (Y - cpY) / (1 - cpY) if cpY < 1 else (Y - cpY)
        else:
            scaled_y = (Y - cpY) / (cpY + 1) if cpY > -1 else (Y - cpY)

        return (scaled_x, scaled_y)


# ---------------------------------------------------------------------------
# Effect handle
# ---------------------------------------------------------------------------

class DInputEffectHandle(ffb_backend.BaseEffectHandle):
    def __init__(self, device: "DInputFFBDevice", effect_id: int, effect_type: int) -> None:
        self.device = device
        self.effect_id = effect_id
        self.type = effect_type
        self.params = _default_params(effect_type)
        self._started = False
        self._dirty = False
        self._pushed_hash = None
        self._override_warned = False
        self._consecutive_failures = 0
        # condition effects: True while the DirectInput effect is actually
        # playing.  A zero-coefficient condition is muted device-side -
        # zero force should feel like no effect at all (see
        # _sync_device_playing).
        self._device_playing = False

    def __bool__(self) -> bool:
        return bool(self.effect_id and self.type)

    def __del__(self):
        self.destroy()

    @property
    def name(self):
        return effect_names.get(self.type)

    def __repr__(self):
        return f"DInputEffectHandle({self.effect_id}, {self.name})"

    @property
    def started(self):
        return self._started

    #: consecutive generic update/start failures before the device-side
    #: effect is presumed dead and the handle re-creates itself
    FAILURES_BEFORE_RECREATE = 3

    def invalidate(self):
        """Forget the bridge effect (evicted, device reset, or reconnect) so
        the owning HapticEffect lazily re-creates on next start."""
        self.effect_id = 0
        self._started = False
        self._device_playing = False
        self._pushed_hash = None
        self._consecutive_failures = 0

    def _note_call_failed(self, rc) -> None:
        """Self-heal from a device-side effect death.

        Some drivers silently kill downloaded effects out from under
        their handles - every update then fails forever with a generic
        error.  After a few consecutive failures the handle presumes the
        device-side effect dead and invalidates itself, so the owning
        HapticEffect lazily re-creates it on the next frame.
        DIB_ERR_ACQUISITION is exempt: FFB priority loss has its own
        latched handling and resolves itself when priority returns.
        """
        if rc == DIB_ERR_ACQUISITION:
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.FAILURES_BEFORE_RECREATE:
            # dead effects come in batches, and per-effect cleanup cannot
            # be trusted on the drivers that cause this - recovery is
            # device-wide (see recover_effects)
            self.device.recover_effects(reason=repr(self))

    def _push(self):
        """Send params to the bridge if they changed since the last push."""
        if not self.effect_id:
            logging.warning(f"parameter update on an invalidated effect ({self.name})")
            return
        h = hash(bytes(self.params))
        if h == self._pushed_hash:
            return
        rc = self.device.bridge.effect_update(self.effect_id, self.params)
        if rc == DIB_OK:
            self._pushed_hash = h
            self._consecutive_failures = 0
        else:
            # first failure of a run at WARNING; the repeats before the
            # recovery threshold are bookkeeping, and the device-wide
            # recovery line carries the real signal
            log = (logging.warning if self._consecutive_failures == 0
                   else logging.debug)
            log(f"effect_update failed ({rc}) for {self!r}")
            self._note_call_failed(rc)

    def _is_zero_condition(self) -> bool:
        """All coefficients zero: the effect commands no force at any
        deflection, so the device effect can be muted entirely."""
        if self.type not in CONDITION_EFFECTS:
            return False
        cx, cy = self.params.condition_x, self.params.condition_y
        return not any((cx.positive_coefficient, cx.negative_coefficient,
                        cy.positive_coefficient, cy.negative_coefficient))

    def _sync_device_playing(self):
        """Keep the DirectInput effect's play state matched to the logical
        started state, muting zero-coefficient conditions device-side.

        A PLAYING condition with zero coefficients is not force-free on
        some hardware - felt most in the helicopter force-trim modes,
        whose "hold" state is exactly a zero-coefficient spring whose
        center follows the stick - so zero force renders as no effect at
        all.

        Muting means Stop, never destroy: the effect stays downloaded
        with its slot held and parameter updates keep landing on it, so
        per-frame center updates during a force-trim hold accumulate and
        render the instant a nonzero coefficient restarts the effect.
        ``_started`` (the logical state HapticEffect sees) is
        deliberately independent of ``_device_playing`` (what the device
        renders)."""
        if not self.effect_id:
            return
        should_play = self._started and not self._is_zero_condition()
        if should_play and not self._device_playing:
            rc = self.device.bridge.effect_start(self.effect_id, 1)
            if rc == DIB_OK:
                self._device_playing = True
            elif rc == DIB_ERR_ACQUISITION:
                # FFB priority held by another app: create_effect logs the
                # condition once; per-frame retries stay quiet
                logging.debug(f"effect_start blocked by FFB priority for {self!r}")
            else:
                logging.warning(f"effect_start failed ({rc}) for {self!r}")
        elif not should_play and self._device_playing:
            self.device.bridge.effect_stop(self.effect_id)
            self._device_playing = False

    def start(self, loopCount=1, override=False):
        if override and not self._override_warned:
            logging.info(f"{self!r}: override start not supported on DirectInput devices, starting normally")
            self._override_warned = True
        if not self.effect_id:
            logging.warning(f"start on an invalidated effect ({self.name})")
            return self
        self._push()
        if self.type in CONDITION_EFFECTS:
            self._started = True
            self.device.note_effect_started(self)
            self._sync_device_playing()
            return self
        rc = self.device.bridge.effect_start(self.effect_id, loopCount)
        if rc == DIB_OK:
            self._started = True
            self._device_playing = True
            self.device.note_effect_started(self)
            self._consecutive_failures = 0
        elif rc == DIB_ERR_ACQUISITION:
            logging.debug(f"effect_start blocked by FFB priority for {self!r}")
        else:
            log = (logging.warning if self._consecutive_failures == 0
                   else logging.debug)
            log(f"effect_start failed ({rc}) for {self!r}")
            self._note_call_failed(rc)
        return self

    def stop(self):
        if self.effect_id and self._device_playing:
            self.device.bridge.effect_stop(self.effect_id)
        self._started = False
        self._device_playing = False
        return self

    def destroy(self):
        if self.effect_id:
            logging.debug(f"Destroying effect {self.effect_id} ({self.name})")
            self.device.bridge.effect_destroy(self.effect_id)
            self.type = 0
            self.effect_id = None
            self._started = False
            self._device_playing = False

    def setEffect(self, **kwargs):
        # the Rhino handle uses this for the SET_EFFECT report; here only the
        # fields with a DirectInput equivalent apply
        if "duration" in kwargs:
            self.params.duration_ms = int(kwargs["duration"])
        if "gain" in kwargs:
            self.params.gain = clamp(int(kwargs["gain"]), 0, 4096)
        self._push()

    def setCondition(self, cond: FFBReport_SetCondition):
        axis = cond.parameterBlockOffset
        if axis not in (0, 1):
            logging.warning(f"setCondition: unsupported axis {axis}")
            return
        block = self.params.condition_x if axis == 0 else self.params.condition_y
        block.active = 1
        block.cp_offset = clamp(cond.cpOffset, -4096, 4096)
        block.positive_coefficient = clamp(cond.positiveCoefficient, -4096, 4096)
        block.negative_coefficient = clamp(cond.negativeCoefficient, -4096, 4096)
        # Rhino firmware convention: saturation 0 = unlimited, and the app's
        # FFBReport_SetCondition objects default to 0 (most effect code
        # never sets saturation at all).  DirectInput reads 0 as a
        # zero-force CAP - passed through untranslated, every spring/damper
        # in the app renders zero force (the first DI flight test flew with
        # no spring at all because of this).  0 therefore maps to max.
        block.positive_saturation = clamp(cond.positiveSaturation, 0, 4096) or 4096
        block.negative_saturation = clamp(cond.negativeSaturation, 0, 4096) or 4096
        block.dead_band = clamp(cond.deadBand, 0, 4096)
        self._push()
        self._sync_device_playing()

        if self.type == EFFECT_SPRING:
            self.device.note_spring_condition(axis, block.cp_offset, block.positive_coefficient)

    def setConstantForce(self, magnitude, direction, **kwargs):
        if self.effect_id is None:
            logging.warning("setConstantForce on an invalidated effect")
            return self
        assert (self.type == EFFECT_CONSTANT)
        assert (magnitude >= -1.0 and magnitude <= 1.0)
        self.params.direction_deg = round(direction) % 360
        self.params.constant_magnitude = round(4096 * magnitude)
        self._push()
        return self

    def setPeriodic(self, freq, magnitude, direction, duration=0, **kwargs):
        assert (self.type in PERIODIC_EFFECTS)
        assert (magnitude >= 0 and magnitude <= 1.0)
        self.params.direction_deg = round(direction) % 360
        self.params.duration_ms = int(duration)
        self.params.periodic_magnitude = round(4096 * magnitude)
        self.params.periodic_period_ms = round(1000.0 / freq) if freq else 0
        if "offset" in kwargs:
            self.params.periodic_offset = clamp(int(kwargs["offset"]), -4096, 4096)
        if "phase" in kwargs:
            # Rhino phase is uint8 0-255 for a full cycle
            self.params.periodic_phase_deg = round(int(kwargs["phase"]) * 360 / 255) % 360
        self._push()
        return self

    def setEnvelope(self, envelope: FFBReport_SetEnvelope):
        self.params.envelope_active = 1
        self.params.attack_level = clamp(envelope.attackFromForce, 0, 4096)
        self.params.fade_level = clamp(envelope.decayToForce, 0, 4096)
        self.params.attack_time_ms = envelope.attackTime
        self.params.fade_time_ms = envelope.decayTime
        self._push()


def _default_params(effect_type: int) -> DibEffectParams:
    """Creation-time defaults: zero forces, infinite duration, full gain.
    Condition effects claim both axes up front because the bridge fixes the
    axis list when the DirectInput effect is created."""
    params = DibEffectParams()
    params.gain = 4096
    if effect_type in CONDITION_EFFECTS:
        params.condition_x.active = 1
        params.condition_y.active = 1
    elif effect_type in PERIODIC_EFFECTS:
        params.periodic_period_ms = 100
    return params


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

class DInputFFBDevice(ffb_backend.BaseFFBDevice):
    """A DirectInput FFB device, driven through the bridge DLL.

    Mirrors FFBRhino's observable behavior: 1ms Qt-timer polling, button and
    hat press/release signals, deviceConnected on loss/reacquire, effect
    creation returning None when the pool is exhausted (after tier-1
    eviction fails).
    """

    @property
    def caps(self) -> ffb_backend.DeviceCapabilities:
        return DINPUT_CAPABILITIES

    def __init__(self, guid: str, bridge=None, poll_interval_ms: int = 1) -> None:
        self.guid = guid
        self.bridge = bridge if bridge is not None else shared_bridge()
        self.info = self._find_info(guid)
        self.firmware_version = None
        self._button_state: int = 0
        self._prev_hats = 0xFFFF
        self._last_state: Optional[DibDeviceState] = None
        # CP emulation: axis -> (cp_offset int, coefficient int)
        self._spring_cp: Dict[int, tuple] = {}
        self._effect_handles: List[weakref.ref] = []
        self._start_seq = 0
        self._last_started: Dict[int, int] = {}  # id(handle) -> sequence
        self._reconnecting = False
        # latch: log the FFB-priority-lost condition once instead of every
        # frame (the lazy effect re-create retries continuously)
        self._acquisition_warned = False

        self._shutdown = False
        self._last_recovery = 0.0
        # plain assignments only before super().__init__: a getattr for a
        # MISSING attribute on an uninitialized QObject raises RuntimeError
        self._axis_map_state = self._IDENTITY_MAP
        self._axis_map_reapply = False

        self._handle = self.bridge.open(guid)
        self._apply_axis_map()
        self._log_autocenter_state()

        super().__init__()
        self._timer_id = None
        if poll_interval_ms:
            self._timer_id = self.startTimer(poll_interval_ms)

    #: what an untouched device runs: identity axes, uninverted
    _IDENTITY_MAP = (0, 1, False, False)

    def _desired_axis_map(self):
        """(x_code, y_code, invert_x, invert_y, description): what the
        stored settings resolve to right now.  Identity for roles that
        never remap (the joystick) and on resolution failure."""
        identity = (*self._IDENTITY_MAP, "identity (device X/Y)")
        try:
            role = getattr(G, 'device_type', 'joystick')
            choice = AXIS_SETTING_AUTO
            inverted = False
            if getattr(G, 'system_settings', None):
                choice = str(G.system_settings.get(
                    axis_setting_key(role), AXIS_SETTING_AUTO)
                    or AXIS_SETTING_AUTO)
                inverted = bool(G.system_settings.get(
                    invert_setting_key(role), False))
            mapping = resolve_axis_map(role, choice,
                                       self.bridge.ffb_axes(self._handle))
        except Exception:
            logging.exception("DirectInput axis map resolution failed; "
                              "staying on native X/Y")
            return identity
        if mapping is None:
            return identity
        x_name, y_name = mapping
        # inversion belongs to the role's own logical axis; the secondary
        # identity axis is never inverted
        primary = ROLE_LOGICAL_AXIS.get(role, 'X')
        invert_x = inverted and primary == 'X'
        invert_y = inverted and primary == 'Y'

        def code(name):
            return (DIBridge.AXIS_NAMES.index(name) if name
                    else DIBridge.AXIS_NONE)

        described = ", ".join(
            f"logical {logical} -> "
            f"{'device ' + native if native else '(unmapped)'}"
            + (' (inverted)' if inv else '')
            for logical, native, inv in (('X', x_name, invert_x),
                                         ('Y', y_name, invert_y)))
        return (code(x_name), code(y_name), invert_x, invert_y, described)

    def _apply_axis_map(self, recover=False):
        """Point logical X/Y at the axes this role's hardware renders
        force on.

        TelemFFB's conventions are fixed - pedals effects address logical
        X, collective Y, trim wheel X - but third-party hardware puts its
        force feedback wherever it likes (pedals on Rz, typically).  The
        resolved map is handed to DirectLink only when it DIFFERS from
        what the device is running - so an identity map is never sent
        and old DLLs stay quiet.  With ``recover`` (a live settings
        change), the device's effects are recreated afterwards:
        DirectInput fixes an effect's axes at creation, so the ones
        already downloaded would otherwise keep the old map forever.
        """
        role = getattr(G, 'device_type', 'joystick')
        *desired, described = self._desired_axis_map()
        desired = tuple(desired)
        if desired == self._axis_map_state:
            return
        rc = self.bridge.set_axis_map(self._handle, *desired)
        if rc == DIB_ERR_UNSUPPORTED:
            logging.warning(
                f"DirectInput axis map ({role}): this DirectLink build "
                f"has no axis mapping (0.9.3 adds it) - wanted {described}, "
                "effects stay on native X/Y")
        elif rc < 0:
            logging.warning(f"DirectInput axis map ({role}) refused: "
                            f"{self.bridge.last_error()}")
        else:
            self._axis_map_state = desired
            logging.info(f"DirectInput axis map ({role}): {described}")
            if recover:
                self.recover_effects("the FFB axis map changed")

    def request_axis_map_reapply(self):
        """A settings save may have changed this role's axis or invert
        choice: re-resolve and re-apply on the next poll tick.  Called
        from any thread (a settings save, the IPC listener) - the device
        work happens on this instance's own timer, and an unchanged map
        is a no-op there.
        """
        self._axis_map_reapply = True

    def _log_autocenter_state(self):
        """Say whether the device's own centering spring was switched off.

        A driver that refuses leaves that spring fighting every rendered
        force - which looks exactly like TelemFFB rendering nothing, so
        it is worth a warning rather than silence.
        """
        try:
            state = self.bridge.autocenter_state(self._handle)
        except Exception:
            return
        if state < 0:
            return          # pre-0.9.2 bridge: nothing to report
        if state & DIBridge.AC_OFF_APPLIED:
            verified = ('device-confirmed'
                        if state & DIBridge.AC_OFF_VERIFIED
                        else 'not reported back by this driver')
            logging.info("DirectInput: the device's own centering spring "
                         f"was switched off for this session ({verified})")
        else:
            logging.warning(
                "DirectInput: this device refused to switch its own "
                "centering spring off; it will fight the forces TelemFFB "
                "renders.  Check the device's driver/profile software for "
                "a centering or spring setting.")

    def _find_info(self, guid: str) -> DIDeviceInfo:
        for dev in self.bridge.enumerate():
            if dev.get("guid") == guid:
                return DIDeviceInfo(
                    guid=guid,
                    product_string=dev.get("name", "DirectInput FFB device"),
                    vendor_id=dev.get("vid", 0),
                    product_id=dev.get("pid", 0),
                    ff_axes=dev.get("ff_axes", 0),
                    buttons=dev.get("buttons", 0),
                    povs=dev.get("povs", 0),
                    effects=dev.get("effects", []),
                    path=guid.encode(),
                )
        return DIDeviceInfo(guid=guid, product_string="DirectInput FFB device",
                            vendor_id=0, product_id=0, path=guid.encode())

    @staticmethod
    def enumerate(bridge=None) -> List[DIDeviceInfo]:
        bridge = bridge if bridge is not None else shared_bridge()
        return [DIDeviceInfo(
            guid=d.get("guid", ""),
            product_string=d.get("name", "?"),
            vendor_id=d.get("vid", 0),
            product_id=d.get("pid", 0),
            ff_axes=d.get("ff_axes", 0),
            buttons=d.get("buttons", 0),
            povs=d.get("povs", 0),
            effects=d.get("effects", []),
            path=d.get("guid", "").encode(),
        ) for d in bridge.enumerate()]

    @property
    def serial(self):
        return None

    @property
    def product(self):
        return self.info.product_string

    @property
    def manufacturer(self):
        return None

    # --- polling ----------------------------------------------------------

    @override
    def timerEvent(self, a0: QTimerEvent) -> None:
        if self._reconnecting or self._shutdown:
            return
        if self._axis_map_reapply:
            self._axis_map_reapply = False
            try:
                self._apply_axis_map(recover=True)
            except Exception:
                logging.exception("live axis-map re-apply failed")
        try:
            self._poll_once()
        except Exception:
            logging.exception("DInput poll failed, device lost")
            self._begin_reconnect()

    def pump_input(self):
        """One bridge poll, input intake only (see
        BaseFFBDevice.pump_input).  Unlike _poll_once, buttons and hats
        are NOT processed: a device switch pumps this while it holds the
        main thread, and button events must not fire mid-teardown."""
        self._last_state = self.bridge.poll(self._handle)

    def _poll_once(self):
        state = self.bridge.poll(self._handle)
        self._last_state = state
        self._process_buttons(state.buttons)
        self._process_hats(state.hats)

    def _process_buttons(self, btns: int):
        prev = self._button_state
        self._button_state = btns
        diff = btns ^ prev
        i = 0
        while diff:
            if diff & 1:
                if (~prev & btns) & 1:
                    self.buttonPressed.emit(i)
                if (prev & ~btns) & 1:
                    self.buttonReleased.emit(i)
            i += 1
            diff = diff >> 1
            btns = btns >> 1
            prev = prev >> 1

    def _process_hats(self, hats):
        if hats != self._prev_hats:
            hats_changed = hats ^ self._prev_hats
            for i in range(4):
                mask = 0xF << (i * 4)
                if hats_changed & mask:
                    val = (hats >> (i * 4)) & 0xF
                    prev_val = (self._prev_hats >> (i * 4)) & 0xF

                    if val != 0xF:
                        b = 0x80 | (i << 4) | val
                        self.buttonPressed.emit(b)
                    else:
                        b = 0x80 | (i << 4) | prev_val
                        self.buttonReleased.emit(b)
            self._prev_hats = hats

    def shutdown(self):
        """Release the device deliberately (live device switch).

        Stops the poll timer and defuses the loss-reconnect loop - a queued
        retry must not re-acquire the old hardware behind the new device -
        then releases the bridge handle (which frees the exclusive
        DirectInput acquisition).
        """
        self._shutdown = True
        if self._timer_id is not None:
            try:
                self.killTimer(self._timer_id)
            except Exception:
                pass
            self._timer_id = None
        if self._handle is not None:
            try:
                self.bridge.release(self._handle)
            except Exception:
                pass
            self._handle = None
        logging.info(f"DirectInput device released: {self.info.product_string}")

    #: minimum spacing of device-wide effect recoveries; a burst of dead
    #: handles discovered in one frame must trigger exactly one reset
    RECOVERY_DEBOUNCE_S = 1.0

    def recover_effects(self, reason: str = ""):
        """Device-wide effect recovery: reset the device (destroying every
        effect it holds, reachable or not) and invalidate all handles so
        their owners lazily re-create - the effects are back within a frame
        or two.

        The escape hatch for drivers that kill downloaded effects in
        batches while the hardware may keep rendering them: such strays
        cannot be cleaned up individually, and without the device-level
        reset a re-created effect can double forces over its stranded
        predecessor.
        """
        now = time.monotonic()
        if now - getattr(self, '_last_recovery', 0.0) < self.RECOVERY_DEBOUNCE_S:
            return
        self._last_recovery = now
        logging.warning(
            f"DirectInput device effect recovery ({reason or 'requested'}): "
            "resetting the device and re-creating all effects")
        try:
            rc = self.bridge.device_reset(self._handle)
            if rc != DIB_OK:
                logging.warning(f"device_reset failed ({rc}); "
                                "continuing with handle invalidation")
        except Exception:
            logging.exception("device_reset failed")
        for ref in self._effect_handles:
            effect = ref()
            if effect:
                effect.invalidate()

    def _begin_reconnect(self):
        self._reconnecting = True
        try:
            self.bridge.release(self._handle)
        except Exception:
            pass
        self._handle = None
        self.deviceConnected.emit(False)
        logging.warning("Reconnecting DirectInput device in 1s")
        QTimer.singleShot(1000, self._try_reconnect)

    def _try_reconnect(self):
        if self._shutdown:
            return   # deliberately released mid-retry
        try:
            self._handle = self.bridge.open(self.guid)
            # bridge-side effects died with the old handle
            for ref in self._effect_handles:
                effect = ref()
                if effect:
                    effect.invalidate()
            self._reconnecting = False
            logging.info("DirectInput device reconnected")
            self.deviceConnected.emit(True)
        except Exception:
            logging.warning("Reconnecting DirectInput device in 1s")
            QTimer.singleShot(1000, self._try_reconnect)

    # --- input ------------------------------------------------------------

    def note_spring_condition(self, axis: int, cp_offset: int, coefficient: int):
        """CP emulation: remember the last spring condition per axis."""
        self._spring_cp[axis] = (cp_offset, coefficient)

    def _cp_for_axis(self, axis: int):
        cp = self._spring_cp.get(axis)
        if not cp or cp[1] == 0:
            return None  # spring coefficient 0 -> no meaningful center
        return cp[0] / 4096.0

    def get_input(self) -> Optional[DInputInputSnapshot]:
        if self._last_state is None:
            return None
        s = self._last_state
        return DInputInputSnapshot(
            x=s.x, y=s.y, buttons=s.buttons, hats=s.hats,
            cp_x=self._cp_for_axis(0), cp_y=self._cp_for_axis(1),
        )

    # --- effects ----------------------------------------------------------

    def note_effect_started(self, handle: DInputEffectHandle):
        self._start_seq += 1
        self._last_started[id(handle)] = self._start_seq

    def _evict_one_periodic(self) -> bool:
        """Free a slot by dropping the least-recently-started periodic cue.
        Returns True when something was evicted."""
        candidates = []
        for ref in self._effect_handles:
            effect = ref()
            if effect and effect.effect_id and effect.type in PERIODIC_EFFECTS:
                candidates.append(effect)
        if not candidates:
            return False
        victim = min(candidates, key=lambda e: self._last_started.get(id(e), 0))
        logging.info(f"Effect pool full: evicting periodic cue {victim!r} to make room")
        self.bridge.effect_destroy(victim.effect_id)
        victim.invalidate()
        return True

    def create_effect(self, type) -> Optional[DInputEffectHandle]:
        if type not in SUPPORTED_EFFECTS:
            logging.debug(f"create_effect: {effect_names.get(type, type)} not available on DirectInput devices")
            return None
        if self._handle is None:
            logging.warning("create_effect: device not connected")
            return None

        params = _default_params(type)
        while True:
            effect_id = self.bridge.effect_create(self._handle, type, params)
            if effect_id > 0:
                break
            if effect_id == DIB_ERR_DEVICE_FULL:
                # tier 0 (conditions/constant = force model) may displace
                # tier 1 (periodic cues); a periodic may displace an older cue
                if self._evict_one_periodic():
                    continue
                logging.warning("Effects pool full, cannot create new effect")
                return None
            if effect_id == DIB_ERR_ACQUISITION:
                # a foreground app (typically the sim's own FFB) holds
                # priority; effect ops fail until it releases the device.
                # ERROR level so the exception tracker (which de-duplicates
                # with a count) surfaces the condition in the UI; the
                # per-frame lazy re-create doubles as automatic recovery
                # where the other application does release the device.
                logging.error(
                    f"FFB effects blocked: {self.bridge.last_error()}. "
                    "Either turn off the sim's own force feedback, or - if "
                    "using the DirectInput tap - start TelemFFB before the "
                    "sim and restart the sim now, since the tap is set up "
                    "as the sim starts.")
                self._acquisition_warned = True
                return None
            logging.warning(f"create_effect failed ({effect_id}): {effect_names.get(type, type)} "
                            f"- {self.bridge.last_error()}")
            return None

        if self._acquisition_warned:
            logging.info("FFB priority restored - effects resuming")
            self._acquisition_warned = False
        handle = DInputEffectHandle(self, effect_id, type)
        self._effect_handles.append(weakref.ref(handle, lambda x: self._effect_handles.remove(x)))
        return handle

    def reset_effects(self):
        logging.info("DInput: Reset device effects")
        for ref in self._effect_handles:
            effect = ref()
            if effect and effect.effect_id:
                self.bridge.effect_destroy(effect.effect_id)
                effect.invalidate()
        self._spring_cp.clear()
        time.sleep(0.01)
