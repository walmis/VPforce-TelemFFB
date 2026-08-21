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

"""FFB tap reader - access to a game's native force-feedback intent.

Sims that render their own force feedback (DCS, IL-2, BMS) take exclusive
access of the device, and DirectInput grants that to exactly one
application - so TelemFFB cannot add its effects on top, however much
telemetry the sim exports.  The obstacle is ownership of the device, not
data.

The TelemFFB-DInput-Tap wrapper (a dinput8 proxy DLL in the game folder,
device policy 'tap') removes it: the sim keeps computing its forces and
believes it is rendering them, while the wrapper absorbs its output,
publishes the live effect state into a shared-memory mirror
(``Local\\FFBTap_v1``) and hands the device over.  This module is the
reader side - the sim-agnostic 'Game Managed (DirectInput Tap)' spring
mode (FfbTapMixIn) calls :func:`read_game_spring` from the telemetry loop
and renders the sim's own spring alongside TelemFFB's telemetry-driven
effects, on one device.

The mirror is a STATE TABLE, not a command stream: per-slot seqlocks keep
snapshots consistent, and monotonic start/stop/update counters expose
transient effects that fire entirely between two polls.  All mirror values
are verbatim DirectInput units (magnitudes/coefficients -10000..10000,
saturations 0..10000); this module owns the translation to TelemFFB's
Rhino conventions (+-4096).  The protocol contract lives in the wrapper
repo: src/ffb_tap.h.

Axis sign conventions are passed through 1:1 (DI +offset -> Rhino +offset)
pending field verification.
"""

import ctypes
import logging
import mmap
import time
from dataclasses import dataclass
from typing import Optional

SHM_NAME = "Local\\FFBTap_v1"
TAP_MAGIC = 0x50415446
TAP_VERSION = 1
MAX_DEVICES = 4
MAX_EFFECTS = 32

ET_CONSTANT = 1
ET_SPRING = 8
ET_DAMPER = 9

STILL_ACTIVE = 259

PID_CHECK_INTERVAL_S = 1.0   # amortize the writer-liveness syscall


# ---------------------------------------------------------------------------
# ctypes mirror of the FfbTapShm layout (ffb_tap.h in the wrapper repo).
# Sizes are asserted below - a mismatch means the contract versions differ.
# ---------------------------------------------------------------------------
class _Envelope(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("attackLevel", ctypes.c_uint32),
                ("attackTime", ctypes.c_uint32),
                ("fadeLevel", ctypes.c_uint32),
                ("fadeTime", ctypes.c_uint32)]


class _Constant(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("magnitude", ctypes.c_int32)]


class _Ramp(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("start", ctypes.c_int32), ("end", ctypes.c_int32)]


class _Periodic(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("magnitude", ctypes.c_uint32),
                ("offset", ctypes.c_int32),
                ("phase", ctypes.c_uint32),
                ("period", ctypes.c_uint32)]


class _Condition(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("count", ctypes.c_uint32),
                ("offset", ctypes.c_int32 * 2),
                ("positiveCoefficient", ctypes.c_int32 * 2),
                ("negativeCoefficient", ctypes.c_int32 * 2),
                ("positiveSaturation", ctypes.c_uint32 * 2),
                ("negativeSaturation", ctypes.c_uint32 * 2),
                ("deadBand", ctypes.c_int32 * 2)]


class _TypeSpecific(ctypes.Union):
    _pack_ = 4
    _fields_ = [("constant", _Constant),
                ("ramp", _Ramp),
                ("periodic", _Periodic),
                ("condition", _Condition),
                ("raw", ctypes.c_uint8 * 64)]


class TapEffect(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("seq", ctypes.c_uint32),
                ("slotUsed", ctypes.c_uint32),
                ("effectType", ctypes.c_uint32),
                ("playing", ctypes.c_uint32),
                ("startCount", ctypes.c_uint32),
                ("stopCount", ctypes.c_uint32),
                ("updateCount", ctypes.c_uint32),
                ("iterations", ctypes.c_uint32),
                ("flags", ctypes.c_uint32),
                ("duration", ctypes.c_uint32),
                ("startDelay", ctypes.c_uint32),
                ("gain", ctypes.c_uint32),
                ("axisCount", ctypes.c_uint32),
                ("axes", ctypes.c_uint32 * 2),
                ("direction", ctypes.c_int32 * 2),
                ("hasEnvelope", ctypes.c_uint32),
                ("envelope", _Envelope),
                ("u", _TypeSpecific)]


class TapDevice(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("seq", ctypes.c_uint32),
                ("used", ctypes.c_uint32),
                ("generation", ctypes.c_uint32),
                ("vid", ctypes.c_uint32),
                ("pid", ctypes.c_uint32),
                ("resetCount", ctypes.c_uint32),
                ("pausedState", ctypes.c_uint32),
                ("deviceGain", ctypes.c_uint32),
                ("autocenter", ctypes.c_uint32),
                ("name", ctypes.c_char * 64),
                ("effects", TapEffect * MAX_EFFECTS)]


class TapShm(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("magic", ctypes.c_uint32),
                ("version", ctypes.c_uint32),
                ("size", ctypes.c_uint32),
                ("writerPid", ctypes.c_uint32),
                ("tickMs", ctypes.c_uint32),
                ("deviceCount", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 10),
                ("devices", TapDevice * MAX_DEVICES)]


assert ctypes.sizeof(TapEffect) == 152, ctypes.sizeof(TapEffect)
assert ctypes.sizeof(TapDevice) == 4964, ctypes.sizeof(TapDevice)
assert ctypes.sizeof(TapShm) == 19920, ctypes.sizeof(TapShm)


def _pid_alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ok) and code.value == STILL_ACTIVE


# ---------------------------------------------------------------------------
# Unit translation: DirectInput verbatim -> Rhino conventions
# ---------------------------------------------------------------------------
def di_to_rhino(v: int) -> int:
    """+-10000 DI nominal -> +-4096 Rhino counts."""
    return round(v * 4096 / 10000)


def di_to_rhino_sat(v: int) -> int:
    # 0 from the game means "not set" far more often than "zero cap";
    # treat it as unlimited (full saturation) like the DI backend's
    # inverse mapping does.
    return 4096 if v == 0 else min(di_to_rhino(v), 4096)


@dataclass
class TapAxisCondition:
    """One axis of a condition effect, already in Rhino counts."""
    offset: int
    positive_coefficient: int
    negative_coefficient: int
    positive_saturation: int
    negative_saturation: int
    deadband: int

    def inverted(self) -> "TapAxisCondition":
        """The condition mirrored for an axis sign flip: the offset negates
        and the positive/negative-side roles swap.  Counterpart to the
        game-side invert-axis adjustments (DCS FFTune) that bake into the
        mirrored data."""
        return TapAxisCondition(
            offset=-self.offset,
            positive_coefficient=self.negative_coefficient,
            negative_coefficient=self.positive_coefficient,
            positive_saturation=self.negative_saturation,
            negative_saturation=self.positive_saturation,
            deadband=self.deadband)


@dataclass
class TapSpringState:
    """The game's spring as published by the tap, translated for rendering."""
    x: Optional[TapAxisCondition]
    y: Optional[TapAxisCondition]
    device_name: str
    generation: int
    reset_count: int
    update_count: int


def _translate_axis(c, i: int) -> TapAxisCondition:
    return TapAxisCondition(
        offset=max(-4096, min(4096, di_to_rhino(c.offset[i]))),
        positive_coefficient=di_to_rhino(c.positiveCoefficient[i]),
        negative_coefficient=di_to_rhino(c.negativeCoefficient[i]),
        positive_saturation=di_to_rhino_sat(c.positiveSaturation[i]),
        negative_saturation=di_to_rhino_sat(c.negativeSaturation[i]),
        deadband=min(di_to_rhino(abs(c.deadBand[i])), 4096),
    )


class FfbTapReader:
    """Attach to the tap mapping and take seqlock-consistent snapshots.

    Either side may create the mapping (it is pagefile-backed and named), so
    attaching before the game starts is fine - the mapping reads as zeroes
    (magic 0) until the wrapper stamps it.  ACCESS_WRITE is required even
    though we never write: creating the object read-only would lock the
    game's writer out of it.
    """

    def __init__(self):
        self._buf: Optional[mmap.mmap] = None
        self._writer_ok = False
        self._writer_pid = 0
        self._last_pid_check = 0.0
        self._logged_writer = False
        self._logged_device_gen = None

    def attach(self) -> bool:
        if self._buf is not None:
            return True
        try:
            self._buf = mmap.mmap(-1, ctypes.sizeof(TapShm), tagname=SHM_NAME,
                                  access=mmap.ACCESS_WRITE)
            return True
        except OSError as e:
            logging.debug(f"FFB tap: mapping unavailable: {e}")
            return False

    def close(self):
        if self._buf is not None:
            self._buf.close()
            self._buf = None

    def snapshot(self) -> Optional[TapShm]:
        """Read a consistent copy, retrying blocks caught mid-write."""
        if self._buf is None and not self.attach():
            return None
        size = ctypes.sizeof(TapShm)
        last = None
        for _ in range(8):
            self._buf.seek(0)
            a = TapShm.from_buffer_copy(self._buf.read(size))
            self._buf.seek(0)
            b = TapShm.from_buffer_copy(self._buf.read(size))
            last = b
            stable = True
            for d_a, d_b in zip(a.devices, b.devices):
                if d_a.seq != d_b.seq or d_a.seq & 1:
                    stable = False
                    break
                for e_a, e_b in zip(d_a.effects, d_b.effects):
                    if e_a.slotUsed and (e_a.seq != e_b.seq or e_a.seq & 1):
                        stable = False
                        break
                if not stable:
                    break
            if stable:
                return b
        return last

    def writer_alive(self, shm: TapShm) -> bool:
        """Liveness of the publishing game, with the syscall amortized."""
        if shm.magic != TAP_MAGIC or shm.writerPid == 0:
            self._writer_ok = False
            return False
        now = time.monotonic()
        if (shm.writerPid != self._writer_pid
                or now - self._last_pid_check > PID_CHECK_INTERVAL_S):
            self._writer_pid = shm.writerPid
            self._writer_ok = _pid_alive(shm.writerPid)
            self._last_pid_check = now
        return self._writer_ok

    def read_game_spring(self) -> Optional[TapSpringState]:
        """The first tapped device's playing spring, translated to Rhino
        units - or None (no writer, wrong protocol version, device paused,
        or spring not playing).

        Called from the telemetry loop, so state-transition logging is
        edge-triggered here.
        """
        shm = self.snapshot()
        if shm is None or shm.magic != TAP_MAGIC:
            self._log_writer_state(False)
            return None
        if shm.version != TAP_VERSION:
            if not self._logged_writer:
                logging.warning(f"FFB tap: protocol version {shm.version} != "
                                f"{TAP_VERSION}; ignoring tap")
                self._logged_writer = True
            return None
        if not self.writer_alive(shm):
            self._log_writer_state(False)
            return None
        self._log_writer_state(True, shm.writerPid)

        for dev in shm.devices:
            if not dev.used:
                continue
            name = dev.name.decode("utf-8", "replace")
            if self._logged_device_gen != (dev.generation, dev.resetCount):
                logging.info(f"FFB tap: tapped device [{name}] "
                             f"vid={dev.vid:04X} pid={dev.pid:04X} "
                             f"gen={dev.generation} resets={dev.resetCount}")
                self._logged_device_gen = (dev.generation, dev.resetCount)
            if dev.pausedState:
                return None
            for e in dev.effects:
                if e.slotUsed and e.effectType == ET_SPRING and e.playing:
                    c = e.u.condition
                    n = min(c.count, 2)
                    return TapSpringState(
                        x=_translate_axis(c, 0) if n >= 1 else None,
                        y=_translate_axis(c, 1) if n >= 2 else None,
                        device_name=name,
                        generation=dev.generation,
                        reset_count=dev.resetCount,
                        update_count=e.updateCount,
                    )
            return None   # tapped device present, no playing spring
        return None

    def _log_writer_state(self, alive: bool, pid: int = 0):
        if alive and not self._logged_writer:
            logging.info(f"FFB tap: writer detected (pid={pid})")
            self._logged_writer = True
        elif not alive and self._logged_writer:
            logging.info("FFB tap: writer gone")
            self._logged_writer = False
            self._logged_device_gen = None


# Module-level singleton: the aircraft telemetry handlers poll through this.
_reader: Optional[FfbTapReader] = None


def read_game_spring() -> Optional[TapSpringState]:
    """Aircraft-facing entry point (see FfbTapReader.read_game_spring)."""
    global _reader
    if _reader is None:
        _reader = FfbTapReader()
    try:
        return _reader.read_game_spring()
    except Exception:
        logging.exception("FFB tap: read failed")
        return None
