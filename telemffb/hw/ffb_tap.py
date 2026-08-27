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
effects, on one device.  :func:`read_game_effects` reads the rest of the
mirror - the game's constant forces, periodic vibrations and damping
effects - for the same mode's per-type game-effect rendering.

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
import math
import mmap
import time
from dataclasses import dataclass, field
from typing import List, Optional

SHM_NAME = "Local\\FFBTap_v1"
TAP_MAGIC = 0x50415446
TAP_VERSION = 1
MAX_DEVICES = 4
MAX_EFFECTS = 32

# Effect type codes (FfbTapEffectType in ffb_tap.h) - numerically identical
# to the app's EFFECT_* enum in ffb_rhino.py; both follow HID PID ordering.
ET_CONSTANT = 1
ET_RAMP = 2
ET_SQUARE = 3
ET_SINE = 4
ET_TRIANGLE = 5
ET_SAWTOOTH_UP = 6
ET_SAWTOOTH_DN = 7
ET_SPRING = 8
ET_DAMPER = 9
ET_INERTIA = 10
ET_FRICTION = 11
ET_CUSTOM = 12

PERIODIC_TYPES = frozenset((ET_SQUARE, ET_SINE, ET_TRIANGLE,
                            ET_SAWTOOTH_UP, ET_SAWTOOTH_DN))
#: Condition types other than the spring (which the spring mode owns).
CONDITION_TYPES = frozenset((ET_DAMPER, ET_INERTIA, ET_FRICTION))

# DIEFFECT.dwFlags bits mirrored verbatim by the wrapper.
DIEFF_OBJECTOFFSETS = 0x02
DIEFF_CARTESIAN = 0x10
DIEFF_POLAR = 0x20
DIEFF_SPHERICAL = 0x40

DIJOFS_Y = 4          # offsetof(DIJOYSTATE, lY), identifies the Y axis
DI_INFINITE = 0xFFFFFFFF

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

    def scaled(self, gain: float) -> "TapAxisCondition":
        """The condition with a user gain applied to its force output:
        the coefficients scale (clamped to Rhino range); positions (offset,
        deadband) and the saturation caps are untouched."""
        def s(v: int) -> int:
            return max(-4096, min(4096, round(v * gain)))
        return TapAxisCondition(
            offset=self.offset,
            positive_coefficient=s(self.positive_coefficient),
            negative_coefficient=s(self.negative_coefficient),
            positive_saturation=self.positive_saturation,
            negative_saturation=self.negative_saturation,
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


def _translate_axis(c, i: int, gain: float = 1.0) -> TapAxisCondition:
    return TapAxisCondition(
        offset=max(-4096, min(4096, di_to_rhino(c.offset[i]))),
        positive_coefficient=round(di_to_rhino(c.positiveCoefficient[i]) * gain),
        negative_coefficient=round(di_to_rhino(c.negativeCoefficient[i]) * gain),
        positive_saturation=di_to_rhino_sat(c.positiveSaturation[i]),
        negative_saturation=di_to_rhino_sat(c.negativeSaturation[i]),
        deadband=min(di_to_rhino(abs(c.deadBand[i])), 4096),
    )


# ---------------------------------------------------------------------------
# Non-spring game effects (constant / periodic / damping) - the rest of what
# the tap mirrors.  The spring stays with TapSpringState above: it is the
# spring MODE itself, with its own field-proven read path.
# ---------------------------------------------------------------------------
@dataclass
class TapEnvelope:
    """A DIENVELOPE translated to Rhino conventions (levels in counts, ms)."""
    attack_level: int
    attack_time_ms: int
    fade_level: int
    fade_time_ms: int


@dataclass
class TapGameEffect:
    """One mirrored non-spring effect slot, translated for rendering.

    Magnitudes are floats in the app's effect-API ranges with the mirrored
    per-effect DIEFFECT gain already applied; ``direction_deg`` is in the
    app's polar-degrees convention (identical to DirectInput polar).
    ``duration_ms`` is None for an infinite (or never-set) duration.
    Ramp and custom effects carry no payload - they are not renderable.
    """
    slot: int
    effect_type: int
    playing: bool
    start_count: int
    update_count: int
    duration_ms: Optional[int]
    direction_deg: float
    constant_magnitude: float = 0.0          # -1..1
    periodic_magnitude: float = 0.0          # 0..1
    periodic_offset: int = 0                 # Rhino counts
    periodic_phase: int = 0                  # Rhino phase, 0..255 per cycle
    periodic_freq: float = 0.0               # Hz (0 = period never set)
    x: Optional[TapAxisCondition] = None     # condition types only
    y: Optional[TapAxisCondition] = None
    envelope: Optional[TapEnvelope] = None


@dataclass
class TapGameEffects:
    """All mirrored non-spring effect slots of this instance's tapped
    device (see FfbTapReader._select_device)."""
    effects: List[TapGameEffect] = field(default_factory=list)
    device_name: str = ""
    generation: int = 0
    reset_count: int = 0


def _direction_degrees(e) -> float:
    """DIEFFECT direction -> app polar degrees (0 pushes +Y, 270 pushes +X).

    The app's degree convention IS the DirectInput polar convention (the DI
    backend passes it through verbatim), so polar values just drop the
    hundredths scaling and cartesian/spherical rays convert geometrically.
    A single-axis effect has no meaningful direction - the force is the
    signed magnitude along that axis, so the positive ray of the axis is
    returned and the magnitude keeps its sign.
    """
    if e.axisCount <= 1:
        axis_is_y = bool(e.flags & DIEFF_OBJECTOFFSETS) and e.axes[0] == DIJOFS_Y
        return 0.0 if axis_is_y else 270.0
    if e.flags & DIEFF_POLAR:
        return (e.direction[0] / 100.0) % 360.0
    x, y = float(e.direction[0]), float(e.direction[1])
    if e.flags & DIEFF_SPHERICAL:
        # first angle: from the +X axis toward +Y, hundredths of degrees
        phi = math.radians(e.direction[0] / 100.0)
        x, y = math.cos(phi), math.sin(phi)
    if x or y:
        # DI cartesian: a ray in axis space, north (-Y) = polar 0, clockwise
        return math.degrees(math.atan2(x, -y)) % 360.0
    return 0.0


def _translate_effect(e, slot: int) -> TapGameEffect:
    # 0 (never set) and FFB_TAP_UNKNOWN both mean "no gain observed": full.
    gain = 1.0 if e.gain in (0, DI_INFINITE) else min(e.gain / 10000.0, 1.0)
    fx = TapGameEffect(
        slot=slot,
        effect_type=e.effectType,
        playing=bool(e.playing),
        start_count=e.startCount,
        update_count=e.updateCount,
        duration_ms=(None if e.duration in (0, DI_INFINITE)
                     else max(1, e.duration // 1000)),
        direction_deg=_direction_degrees(e),
    )
    if e.effectType == ET_CONSTANT:
        fx.constant_magnitude = max(-1.0, min(
            1.0, e.u.constant.magnitude * gain / 10000.0))
    elif e.effectType in PERIODIC_TYPES:
        p = e.u.periodic
        fx.periodic_magnitude = min(p.magnitude * gain / 10000.0, 1.0)
        fx.periodic_offset = max(-4096, min(
            4096, round(di_to_rhino(p.offset) * gain)))
        fx.periodic_phase = round((p.phase / 100.0) % 360.0 * 255 / 360)
        fx.periodic_freq = 1e6 / p.period if p.period else 0.0
    elif e.effectType in CONDITION_TYPES:
        c = e.u.condition
        n = min(c.count, 2)
        fx.x = _translate_axis(c, 0, gain) if n >= 1 else None
        fx.y = _translate_axis(c, 1, gain) if n >= 2 else None
    if e.hasEnvelope:
        env = e.envelope
        fx.envelope = TapEnvelope(
            attack_level=min(di_to_rhino(env.attackLevel), 4096),
            attack_time_ms=env.attackTime // 1000,
            fade_level=min(di_to_rhino(env.fadeLevel), 4096),
            fade_time_ms=env.fadeTime // 1000,
        )
    return fx


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
        self._target_ids_cache = (0.0, None)   # (checked_at, (vid, pid)|None)
        self._warned_no_match = False

    def _target_ids(self):
        """The USB ids of THIS instance's game-facing device, or None.

        Read from the stored identity for this instance's role
        (devids_{role}, kept current by the settings reconcile) - NOT
        from the currently-connected device, which a per-aircraft swap
        may have pointed elsewhere while the game keeps addressing the
        configured one.  Cached briefly: this runs every telemetry frame
        and the settings store is a registry."""
        now = time.monotonic()
        checked_at, ids = self._target_ids_cache
        if now - checked_at < 5.0:
            return ids
        ids = None
        try:
            import telemffb.globals as G
            raw = str(G.system_settings.get(
                f'devids_{G.device_type}', '') or '')
            vid_s, _, pid_s = raw.partition(':')
            if vid_s and pid_s:
                ids = (int(vid_s, 16), int(pid_s, 16))
        except Exception:
            ids = None
        self._target_ids_cache = (now, ids)
        return ids

    def _select_device(self, shm: TapShm, warn: bool = True):
        """The mirror block this instance renders, or None.

        The mirror can carry several tapped devices at once (IL-2 Korea
        taps the joystick AND the pedals); each TelemFFB instance renders
        its own, matched by the configured device's USB ids.  With no
        match: a lone tapped device is used anyway (stored ids may be
        absent or stale on old configs), several without a match is
        ambiguous and none is returned - rendering another instance's
        forces would double them."""
        used = [d for d in shm.devices if d.used]
        if not used:
            return None
        ids = self._target_ids()
        if ids is not None:
            vid, pid = ids
            for dev in used:
                if dev.vid == vid and dev.pid == pid:
                    self._warned_no_match = False
                    return dev
            if len(used) > 1:
                if warn and not self._warned_no_match:
                    listed = ", ".join(f"{d.vid:04X}:{d.pid:04X}"
                                       for d in used)
                    logging.warning(
                        "DirectInput tap: none of the tapped devices "
                        f"({listed}) matches this instance's configured "
                        f"device ({vid:04X}:{pid:04X}); not rendering")
                    self._warned_no_match = True
                return None
        return used[0]

    def attach(self) -> bool:
        if self._buf is not None:
            return True
        try:
            self._buf = mmap.mmap(-1, ctypes.sizeof(TapShm), tagname=SHM_NAME,
                                  access=mmap.ACCESS_WRITE)
            return True
        except OSError as e:
            logging.debug(f"DirectInput tap: mapping unavailable: {e}")
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
        """This instance's tapped device's playing spring, in Rhino
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
                logging.warning(f"DirectInput tap: protocol version {shm.version} != "
                                f"{TAP_VERSION}; ignoring tap")
                self._logged_writer = True
            return None
        if not self.writer_alive(shm):
            self._log_writer_state(False)
            return None
        self._log_writer_state(True, shm.writerPid)

        dev = self._select_device(shm)
        if dev is None:
            return None
        name = dev.name.decode("utf-8", "replace")
        if self._logged_device_gen != (dev.generation, dev.resetCount):
            logging.info(f"DirectInput tap: tapped device [{name}] "
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

    def read_game_effects(self) -> Optional[TapGameEffects]:
        """Every mirrored NON-spring effect slot of this instance's tapped
        device,
        translated for rendering - or None under the same conditions that
        make :meth:`read_game_spring` return None (no writer, wrong
        protocol version, device paused).

        Spring slots are excluded: the spring is the mode itself and its
        read path stays :meth:`read_game_spring`.  Stopped slots ARE
        included - the renderer needs them to reconcile stops and to catch
        transients from startCount deltas.  Writer-state logging is owned
        by the spring path, which runs every frame in this mode.
        """
        shm = self.snapshot()
        if shm is None or shm.magic != TAP_MAGIC:
            return None
        if shm.version != TAP_VERSION:
            return None
        if not self.writer_alive(shm):
            return None
        dev = self._select_device(shm)
        if dev is None or dev.pausedState:
            return None
        out = TapGameEffects(
            device_name=dev.name.decode("utf-8", "replace"),
            generation=dev.generation,
            reset_count=dev.resetCount,
        )
        for slot, e in enumerate(dev.effects):
            if e.slotUsed and e.effectType != ET_SPRING:
                out.effects.append(_translate_effect(e, slot))
        return out

    def _log_writer_state(self, alive: bool, pid: int = 0):
        if alive and not self._logged_writer:
            logging.info(f"DirectInput tap: writer detected (pid={pid})")
            self._logged_writer = True
        elif not alive and self._logged_writer:
            logging.info("DirectInput tap: writer gone")
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
        logging.exception("DirectInput tap: read failed")
        return None


def read_game_effects() -> Optional[TapGameEffects]:
    """Aircraft-facing entry point (see FfbTapReader.read_game_effects)."""
    global _reader
    if _reader is None:
        _reader = FfbTapReader()
    try:
        return _reader.read_game_effects()
    except Exception:
        logging.exception("DirectInput tap: effects read failed")
        return None
