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

"""Generic DirectInput FFB backend via the VPforce-DInput-Bridge DLL.

Speaks to any DirectInput force-feedback device through ``dinput_ffb.dll``
(separate repo: VPforce-DInput-Bridge).  All values cross the DLL boundary in
TelemFFB's native +-4096 units; the bridge converts to DirectInput's +-10000.

Differences from the native VPforce backend, hidden behind the
``ffb_backend`` contract:

- Spring center-point telemetry is EMULATED: the device tracks the last
  spring condition written per axis, so ``CP_XY()``/``CP_scaled_axisXY()``
  return the software-known spring center (trim following and helicopter
  force-trim depend on these).  Offsets themselves render natively: the
  bridge builds condition effects with DIEFF_POLAR coordinates, the one
  formation whose lOffset works on both SideWinder-class drivers and the
  standard PID driver.
- ``forceXY()`` returns None (no force output telemetry on generic devices).
- ``start(override=True)`` downgrades to a normal start (no sticky-start
  concept in DirectInput); logged once per effect.
- Effect slots are budgeted by priority: conditions + constant force are the
  force model (tier 0), periodics are cues (tier 1).  When the device pool is
  full, the least-recently-started tier-1 effect is evicted and its
  HapticEffect lazily re-creates when it next starts.
"""

import ctypes
import logging
import json
import os
import time
import weakref
from dataclasses import dataclass, field
from typing import Dict, List, Optional, override

from PyQt6.QtCore import QTimer, QTimerEvent

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


class DIBridge:
    """ctypes binding to dinput_ffb.dll.

    The device backend takes any object with this method surface, so tests
    substitute a pure-Python fake without touching ctypes.
    """

    _library_paths = (
        os.path.join(os.path.dirname(__file__), "..", "..", "dll", "dinput_ffb.dll"),
        "dinput_ffb.dll",
    )

    def __init__(self, dll_path: Optional[str] = None):
        paths = (dll_path,) if dll_path else self._library_paths
        self._dll = None
        for p in paths:
            try:
                self._dll = ctypes.CDLL(p)
                break
            except OSError:
                pass
        if not self._dll:
            raise DIBridgeError(f"Unable to load dinput_ffb.dll from: {', '.join(paths)}")

        abi = self._dll.dib_abi_version()
        if abi != DIB_ABI_VERSION:
            raise DIBridgeError(f"dinput_ffb.dll ABI version {abi}, expected {DIB_ABI_VERSION}")

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

    def poll(self, device: int) -> DibDeviceState:
        state = DibDeviceState()
        rc = self._dll.dib_poll(device, ctypes.byref(state))
        if rc != DIB_OK:
            raise DIBridgeError(f"poll failed ({rc}): {self.last_error()}")
        return state

    def effect_create(self, device: int, effect_type: int, params: DibEffectParams) -> int:
        """Returns a positive effect id, or a negative DIB_ERR_* code
        (DEVICE_FULL is expected and drives eviction, so no exception)."""
        return self._dll.dib_effect_create(device, effect_type, ctypes.byref(params))

    def effect_update(self, effect: int, params: DibEffectParams) -> int:
        return self._dll.dib_effect_update(effect, ctypes.byref(params))

    def effect_start(self, effect: int, iterations: int = 1) -> int:
        return self._dll.dib_effect_start(effect, iterations, 0)

    def effect_stop(self, effect: int) -> int:
        return self._dll.dib_effect_stop(effect)

    def effect_destroy(self, effect: int) -> int:
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

    def invalidate(self):
        """Forget the bridge effect (evicted, device reset, or reconnect) so
        the owning HapticEffect lazily re-creates on next start."""
        self.effect_id = 0
        self._started = False
        self._pushed_hash = None

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
        else:
            logging.warning(f"effect_update failed ({rc}) for {self!r}")

    def start(self, loopCount=1, override=False):
        if override and not self._override_warned:
            logging.info(f"{self!r}: override start not supported on DirectInput devices, starting normally")
            self._override_warned = True
        if not self.effect_id:
            logging.warning(f"start on an invalidated effect ({self.name})")
            return self
        self._push()
        rc = self.device.bridge.effect_start(self.effect_id, loopCount)
        if rc == DIB_OK:
            self._started = True
            self.device.note_effect_started(self)
        else:
            logging.warning(f"effect_start failed ({rc}) for {self!r}")
        return self

    def stop(self):
        if self.effect_id:
            self.device.bridge.effect_stop(self.effect_id)
        self._started = False
        return self

    def destroy(self):
        if self.effect_id:
            logging.debug(f"Destroying effect {self.effect_id} ({self.name})")
            self.device.bridge.effect_destroy(self.effect_id)
            self.type = 0
            self.effect_id = None
            self._started = False

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
        # Rhino firmware convention: saturation 0 = unlimited (the app's
        # condition objects default to 0).  DirectInput reads 0 as a
        # zero-force cap, which silences the effect entirely.
        block.positive_saturation = clamp(cond.positiveSaturation, 0, 4096) or 4096
        block.negative_saturation = clamp(cond.negativeSaturation, 0, 4096) or 4096
        block.dead_band = clamp(cond.deadBand, 0, 4096)
        self._push()

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
        self.bridge = bridge if bridge is not None else DIBridge()
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

        self._handle = self.bridge.open(guid)

        super().__init__()
        if poll_interval_ms:
            self.startTimer(poll_interval_ms)

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
        bridge = bridge if bridge is not None else DIBridge()
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
        if self._reconnecting:
            return
        try:
            self._poll_once()
        except Exception:
            logging.exception("DInput poll failed, device lost")
            self._begin_reconnect()

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
            logging.warning(f"create_effect failed ({effect_id}): {effect_names.get(type, type)}")
            return None

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
