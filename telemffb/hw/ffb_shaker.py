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

"""Bass shaker backend: a sound card as an FFB device.

``ShakerFFBDevice`` sits behind the same ``BaseFFBDevice`` seam as the
VPforce and DirectInput backends, so the aircraft modules never learn a
shaker exists: they start the effects they always start, with the
frequencies, magnitudes and durations they always use, and this backend
turns each one into a voice on a transducer.  Which effects run, and how
strong, is the shaker child's own settings tree - the same enable and
intensity settings every device has, scoped to the shaker.  Nothing here
is defined per effect.

What a voice sounds like follows from the effect's parameters and the
transducer's calibration profile:

* A periodic effect with a short duration is a transient: one gated
  pulse when it starts.
* A periodic effect whose frequency sits inside the transducer's band is
  a tone at that frequency, following every update.
* A periodic effect above the band folds down by octaves until it fits,
  so a 180 Hz flap motor plays at 90 or 45 Hz and the rhythm between
  effects survives.
* A periodic effect below the band's floor becomes one pulse per cycle:
  a transducer cannot play ten hertz, but it can hit ten times a second,
  which is what gunfire and a chugging idle are.
* A constant force is a steady push, which a transducer cannot render;
  what it can render is the push changing.  The magnitude is AC-coupled
  and drives a tone at the transducer's carrier: runway texture and a
  touchdown come through as rumble and a thump, a steady g load as
  nothing.
* Condition effects have no meaning here and are declined.

One shaker instance drives one sound card and every transducer on it.
A transducer is a row in the shaker's settings: an output channel, a
gain and a calibration profile.  Each effect is rendered once per
distinct profile and each transducer hears its profile's mix on its
channel, so a heavy transducer under the seat and two light ones on the
seat back each get pulses shaped for them, at the cost of one extra
render per profile, not per transducer.

The synthesis and the calibration profiles are adapted from 89Huey89's
shaker work (https://github.com/89Huey89/vpforce-telemffb-Shaker).
"""

import json
import logging
import math
import os
import sys
import time
import weakref
from dataclasses import asdict, dataclass, fields
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from telemffb.hw import ffb_backend
from telemffb.hw.ffb_rhino import (
    EFFECT_CONSTANT, PERIODIC_EFFECTS, effect_names,
)
from telemffb.hw.shaker_synth import (
    ImpulseTrain, Oscillator, Route, ShakerSynth, SoundDeviceOutput, clamp,
)
from telemffb.utils import AUDIO_PREFIX, SHAKER_PSEUDO_PID

log = logging.getLogger(__name__)

#: what a shaker can render: the universal periodic set and constant force
SUPPORTED_EFFECTS = frozenset(list(PERIODIC_EFFECTS) + [EFFECT_CONSTANT])

#: a shaker has none of the VPforce extras, and nothing to read back
SHAKER_CAPABILITIES = ffb_backend.DeviceCapabilities()

#: a periodic this short is a hit, not a tone
TRANSIENT_MS = 150

#: master gain a fresh shaker starts with.  TelemFFB's intensities are
#: tuned for stick force - engine rumble runs at a few percent - and a
#: transducer needs several times that to be felt; the mixer's limiter
#: keeps the sum in range.
DEFAULT_GAIN = 3.0

#: where a transducer touches the body, for the placement policies to
#: come; stored with the row, unused by the rendering rules today.  The
#: contact point first (the seat pan, the seat back, the floor at the
#: feet), then the side.  Deliberately no word in common with the channel
#: picker's Rear L / Side R, so the two columns cannot be confused.
POSITIONS = ('seat', 'seat left', 'seat right',
             'seat back', 'seat back left', 'seat back right',
             'floor', 'floor left', 'floor right')

#: where a transducer touches the body, without the side: what a
#: placement names
CONTACTS = ('seat', 'seat back', 'floor')


def contact_of(position: str) -> str:
    """'seat back left' -> 'seat back', 'floor right' -> 'floor'."""
    position = POSITION_ALIASES.get(position, position or 'seat')
    for contact in sorted(CONTACTS, key=len, reverse=True):
        if position == contact or position.startswith(contact + ' '):
            return contact
    return 'seat'


@dataclass(frozen=True)
class Placement:
    """Where an effect plays: the contact points that hear it (empty means
    every transducer), and which of those hear it late, by ``delay_ms``.
    The key names the placement for grouping voices; two effects with the
    same profile and placement share one render."""
    contacts: FrozenSet[str] = frozenset()
    delayed: FrozenSet[str] = frozenset()
    delay_ms: float = 0.0

    @property
    def everywhere(self) -> bool:
        return not self.contacts

    @property
    def key(self) -> str:
        if self.everywhere:
            return 'all'
        parts = []
        for contact in CONTACTS:
            if contact in self.contacts:
                parts.append(contact + ('>' if contact in self.delayed else ''))
        return '+'.join(parts) + (f"@{self.delay_ms:g}" if self.delayed else '')

    def hears(self, position: str) -> bool:
        return self.everywhere or contact_of(position) in self.contacts

    def late(self, position: str) -> bool:
        return contact_of(position) in self.delayed


EVERYWHERE = Placement()

#: the choices a placement setting offers, by their stored value
PLACEMENT_CHOICES = {
    'all': (),
    'seat': ('seat',),
    'seat back': ('seat back',),
    'floor': ('floor',),
    'seat + seat back': ('seat', 'seat back'),
    'seat + floor': ('seat', 'floor'),
    'seat back + floor': ('seat back', 'floor'),
    'floor then seat back': ('floor', 'seat back'),
}
#: which choices lag their second contact behind the first
DELAYED_CHOICES = {'floor then seat back': ('seat back',)}
DEFAULT_PLACEMENT_DELAY_MS = 60.0


def placement_from_choice(choice, delay_ms: float = DEFAULT_PLACEMENT_DELAY_MS) -> Placement:
    """A Placement for a stored setting value; an unknown value plays
    everywhere rather than nowhere."""
    choice = str(choice or 'all').strip().lower()
    contacts = PLACEMENT_CHOICES.get(choice)
    if not contacts:
        return EVERYWHERE
    delayed = DELAYED_CHOICES.get(choice, ())
    return Placement(frozenset(contacts), frozenset(delayed),
                     float(delay_ms) if delayed else 0.0)


#: names an earlier build stored, so a saved row keeps its place
POSITION_ALIASES = {
    'left': 'seat left', 'right': 'seat right',
    'back': 'seat back', 'back left': 'seat back left', 'back right': 'seat back right',
    'front': 'floor', 'front left': 'floor left', 'front right': 'floor right',
}

#: What each channel of a Windows speaker layout carries, by the width of
#: the stream.  Fixed by the WAVEFORMATEXTENSIBLE channel order every
#: layout Windows configures uses, so a card's channels can be named by
#: position; which physical jack carries a position is the card's own
#: business and only a test tone settles it.
CHANNEL_POSITIONS = {
    1: ('Mono',),
    2: ('Left', 'Right'),
    4: ('Front L', 'Front R', 'Rear L', 'Rear R'),
    6: ('Front L', 'Front R', 'Center', 'Subwoofer', 'Rear L', 'Rear R'),
    8: ('Front L', 'Front R', 'Center', 'Subwoofer', 'Rear L', 'Rear R', 'Side L', 'Side R'),
}


def channel_label(index: int, width: int) -> str:
    """'4 Subwoofer' on an eight-channel output, '2 Right' on a stereo
    one, the bare number where the layout is not a standard one or the
    channel lies beyond it."""
    names = CHANNEL_POSITIONS.get(int(width), ())
    number = int(index) + 1
    if 0 <= index < len(names):
        return f"{number} {names[index]}"
    return str(number)


#: the shaker's own settings, global keys like the launch options
SETTING_GAIN = 'shakerGain'
SETTING_TRANSDUCERS = 'shakerTransducers'
#: the single-transducer keys an earlier build stored; read once to make
#: the equivalent rows when no transducer list is stored yet
LEGACY_SETTING_MODE = 'shakerChannelMode'
LEGACY_SETTING_PAN = 'shakerPan'
LEGACY_SETTING_PROFILE = 'shakerProfile'


class ShakerOutputError(Exception):
    """The audio output could not be opened."""


def bundled_data_path(name: str) -> str:
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, 'telemffb', 'data', name)


# ---------------------------------------------------------------------------
# Calibration profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShakerProfile:
    """How one transducer wants to be driven.

    The band is what it reproduces as a tone; frequencies outside it are
    folded in or turned into pulses.  The resonance and the carrier
    offset give the pulse carrier (``carrier_hz``); half waves, attack
    and release give a pulse's length and edges; the brake is one
    phase-inverted half wave that stops a heavy moving mass ringing.
    """
    name: str = 'Generic'
    description: str = ''
    f_res_hz: float = 45.0
    carrier_offset_pct: float = 15.0
    band_low_hz: float = 15.0
    band_high_hz: float = 120.0
    halfwaves: int = 2
    attack_ms: float = 1.5
    release_ms: float = 2.0
    gain: float = 1.0
    brake_enabled: bool = False
    brake_amp_pct: float = 40.0
    brake_delay_ms: float = 0.5
    notes: str = ''

    @property
    def carrier_hz(self) -> float:
        return max(1.0, self.f_res_hz * (1.0 + self.carrier_offset_pct / 100.0))

    def brake_amp(self, amplitude: float) -> float:
        """Brake amplitude for a pulse of ``amplitude``: off unless enabled,
        and proportional to the pulse it is stopping."""
        if not self.brake_enabled:
            return 0.0
        return clamp(self.brake_amp_pct / 100.0 * amplitude, 0.0, 1.0)

    def fold(self, freq_hz: float) -> float:
        """A frequency above the band brought down by octaves until it
        fits (or as close as octaves get when the band is narrower than
        one)."""
        f = float(freq_hz)
        while f > self.band_high_hz and f / 2.0 >= self.band_low_hz:
            f /= 2.0
        return f


DEFAULT_PROFILE = ShakerProfile()

_PROFILE_FIELDS = {f.name for f in fields(ShakerProfile)}


def profile_from_dict(data: dict) -> ShakerProfile:
    return ShakerProfile(**{k: v for k, v in data.items() if k in _PROFILE_FIELDS})


def load_profiles(path: str) -> Tuple[List[ShakerProfile], str]:
    """The profiles in a pack file and the name of the active one; the
    default profile alone when the file is missing or unreadable."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        profiles = [profile_from_dict(p) for p in data.get('profiles', [])]
        active = str(data.get('active', '') or '')
    except Exception:
        log.exception(f"shaker profiles: could not read {path}")
        profiles, active = [], ''
    if not profiles:
        profiles = [DEFAULT_PROFILE]
    if active not in {p.name for p in profiles}:
        active = profiles[0].name
    return profiles, active


def default_profiles_path() -> str:
    return bundled_data_path('shaker_profiles_default.json')


#: names earlier packs used, so a stored choice survives a rename
PROFILE_ALIASES = {
    'Dayton DAEX-25': 'Dayton TT25 Puck',
    'Buttkicker LFE': 'ButtKicker LFE',
}


def find_profile(profiles: Sequence[ShakerProfile], name: str, fallback: str = '') -> ShakerProfile:
    """The profile called ``name`` (or what that name is called now), else
    the one called ``fallback``, else the first - a pack never resolves
    to nothing."""
    by_name = {p.name: p for p in profiles}
    name = PROFILE_ALIASES.get(name, name)
    return by_name.get(name) or by_name.get(fallback) or profiles[0]


# ---------------------------------------------------------------------------
# Transducers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Transducer:
    """One transducer on the shaker's sound card: which output channel it
    is wired to (0-based), how loud, which calibration profile shapes
    what it plays, and where it sits."""
    name: str = 'Shaker'
    channel: int = 0
    gain: float = 1.0
    profile: str = ''
    position: str = 'seat'


_TRANSDUCER_FIELDS = {f.name for f in fields(Transducer)}


def transducer_from_dict(data: dict) -> Optional[Transducer]:
    try:
        clean = {k: v for k, v in data.items() if k in _TRANSDUCER_FIELDS}
        t = Transducer(**clean)
        position = str(t.position or 'seat')
        return Transducer(str(t.name), max(0, int(t.channel)), clamp(float(t.gain), 0.0, 10.0),
                          str(t.profile or ''), POSITION_ALIASES.get(position, position))
    except (TypeError, ValueError, AttributeError):
        return None


def transducers_from_json(text) -> List[Transducer]:
    """The stored transducer list; empty when unset or unreadable."""
    if not text:
        return []
    try:
        data = json.loads(text) if isinstance(text, str) else list(text)
    except (TypeError, ValueError):
        log.warning("shaker transducers: stored list unreadable, ignored")
        return []
    if not isinstance(data, list):
        return []
    return [t for t in (transducer_from_dict(d) for d in data if isinstance(d, dict)) if t]


def transducers_to_json(transducers: Sequence[Transducer]) -> str:
    return json.dumps([asdict(t) for t in transducers])


def default_transducers(profile_name: str = '') -> List[Transducer]:
    """What a fresh shaker drives: one transducer on each of a stereo
    output's channels, so a shaker on either side (or on both) is heard
    before anything is configured."""
    return [Transducer('Left', 0, 1.0, profile_name, 'seat'),
            Transducer('Right', 1, 1.0, profile_name, 'seat')]


def legacy_transducers(settings, profile_name: str) -> List[Transducer]:
    """The rows equivalent to the single-transducer settings an earlier
    build stored (channel mode and pan), so a configured shaker carries
    over unchanged."""
    mode = str(settings.get(LEGACY_SETTING_MODE, 'mono') or 'mono').lower()
    if mode == 'left':
        return [Transducer('Shaker', 0, 1.0, profile_name, 'seat')]
    if mode == 'right':
        return [Transducer('Shaker', 1, 1.0, profile_name, 'seat')]
    if mode == 'pan':
        try:
            pan = clamp(float(settings.get(LEGACY_SETTING_PAN, 0.0)), -1.0, 1.0)
        except (TypeError, ValueError):
            pan = 0.0
        angle = (pan + 1.0) * 0.25 * math.pi
        return [Transducer('Left', 0, round(math.cos(angle), 3), profile_name, 'seat'),
                Transducer('Right', 1, round(math.sin(angle), 3), profile_name, 'seat')]
    return default_transducers(profile_name)


def shaker_settings(settings) -> dict:
    """The shaker's stored configuration as constructor arguments: the
    master gain, the transducer rows and the profile pack they name,
    each falling back to a default when unset or unreadable (registry
    values arrive as strings)."""
    try:
        gain = clamp(float(settings.get(SETTING_GAIN, DEFAULT_GAIN)), 0.0, 10.0)
    except (TypeError, ValueError):
        gain = DEFAULT_GAIN
    profiles, active = load_profiles(default_profiles_path())
    transducers = transducers_from_json(settings.get(SETTING_TRANSDUCERS, ''))
    if not transducers:
        legacy = str(settings.get(LEGACY_SETTING_PROFILE, '') or '')
        transducers = legacy_transducers(settings, find_profile(profiles, legacy, active).name)
    return {'gain': gain, 'transducers': transducers, 'profiles': profiles}


# ---------------------------------------------------------------------------
# Device identity and input
# ---------------------------------------------------------------------------

@dataclass
class ShakerDeviceInfo:
    """Parallel of the HID and DirectInput device descriptions."""
    output_name: str
    product_string: str = ''
    vendor_id: int = 0
    product_id: int = SHAKER_PSEUDO_PID
    path: bytes = b''

    def __post_init__(self):
        if not self.product_string:
            self.product_string = f"Bass shaker on {self.output_name or 'the default output'}"
        if not self.path:
            self.path = (AUDIO_PREFIX + self.output_name).encode()

    def vidpid(self) -> str:
        return 'audio'

    @property
    def ident(self) -> str:
        return self.product_string.strip()


class ShakerInputSnapshot:
    """A shaker has no axes and no buttons: the read surface reports a
    centered, untouched device so per-frame consumers need no special
    case."""

    X = 0
    Y = 0
    buttons = 0
    hats = 0xFFFF

    def isButtonPressed(self, button_number) -> bool:
        return False

    def getPressedButtons(self) -> List[int]:
        return []

    def axisXY(self) -> tuple:
        return (0.0, 0.0)

    def rawAxisXY(self) -> tuple:
        return (0.0, 0.0)

    def axisOverrideActive(self) -> bool:
        return False

    def CP_XY(self) -> tuple:
        return (None, None)

    def forceXY(self):
        return None

    def CP_scaled_axisXY(self) -> tuple:
        return (0.0, 0.0)


_NO_INPUT = ShakerInputSnapshot()


# ---------------------------------------------------------------------------
# Effect handle
# ---------------------------------------------------------------------------

class ShakerEffectHandle(ffb_backend.BaseEffectHandle):
    """One effect, rendered as the voice its parameters call for, once per
    calibration profile in use.

    Continuous voices (a tone, a pulse train, the AC-coupled constant)
    follow every parameter update while the effect is started, the way a
    periodic's frequency tracks engine RPM frame by frame; a transient
    fires only on the not-started to started transition, never on an
    update.
    """

    #: how fast the AC-coupled constant force forgets its steady part
    AC_TAU_S = 0.15
    #: how long the constant's tone holds without a fresh update
    AC_HOLD_S = 0.25
    #: change in constant magnitude that reaches full amplitude
    AC_GAIN = 3.0

    def __init__(self, device: 'ShakerFFBDevice', effect_id: int, effect_type: int):
        self.device = device
        self.effect_id = effect_id
        self.type = effect_type
        self.frequency = 0.0
        self.magnitude = 0.0
        self.direction = 0.0
        self.duration_ms = 0
        self._gain = 1.0
        self._ramp_ms: Optional[float] = None
        self._started = False
        self._voices: Dict[str, object] = {}     # profile name -> voice
        self._kind = None
        # constant force: the running steady part and when it was last seen
        self._ac_steady = 0.0
        self._ac_drive = 0.0
        self._ac_time: Optional[float] = None

    def __bool__(self) -> bool:
        return bool(self.effect_id and self.type)

    def __del__(self):
        try:
            self.destroy()
        except Exception:
            pass

    @property
    def name(self):
        return effect_names.get(self.type)

    def __repr__(self):
        return f"ShakerEffectHandle({self.effect_id}, {self.name}, {self.label!r})"

    @property
    def started(self) -> bool:
        return self._started

    @property
    def voice(self):
        """The voice in the first profile group, or None before a start."""
        return next(iter(self._voices.values()), None)

    @property
    def voices(self) -> Dict[str, object]:
        return dict(self._voices)

    @property
    def kind(self) -> Optional[str]:
        """What the effect renders as right now: 'pulse', 'tone', 'train',
        'constant', or None before it starts."""
        return self._kind

    @property
    def intensity(self):
        """How hard this effect is currently driving the transducers, 0.0 to
        1.0, for the monitor: the magnitude it was last written with, as
        the hardware handles report it (a constant reads the AC-coupled
        drive, which is what is felt)."""
        if self.type == EFFECT_CONSTANT:
            return clamp(self._ac_drive * self._gain, 0.0, 1.0)
        return clamp(self.magnitude * self._gain, 0.0, 1.0)

    @property
    def axis_gains(self):
        """A shaker renders no conditions, so there is never a per-axis
        gain to show."""
        return None

    # --- lifecycle -----------------------------------------------------------

    def invalidate(self):
        self._drop_voices()
        self.effect_id = 0
        self._started = False

    def forget_playback(self):
        self._started = False

    def start(self, loopCount=1, override=False):
        if not self.effect_id:
            log.warning(f"start on an invalidated effect ({self.name})")
            return self
        firing = not self._started
        self._started = True
        self._apply(fire=firing)
        return self

    def stop(self):
        synth = self.device.synth
        with synth.lock:
            for voice in self._voices.values():
                voice.stop()
        self._started = False
        return self

    def destroy(self):
        if self.effect_id:
            log.debug(f"Destroying effect {self.effect_id} ({self.name})")
            self._drop_voices()
            self.type = 0
            self.effect_id = None
            self._started = False

    def _key(self, group: str) -> str:
        return f"{self.effect_id}:{group}"

    @property
    def placement(self) -> Placement:
        return self.device.placement_for(self.label)

    def _drop_voices(self):
        synth = self.device.synth
        with synth.lock:
            for group in self._voices:
                synth.remove(self._key(group))
        self._voices = {}
        self._kind = None

    def regroup(self):
        """The device's transducers changed: render into the new profile
        groups, without firing anything - a started transient has had its
        hit and a settings save is not a new one."""
        if not self.effect_id:
            return
        was_started = self._started
        self._drop_voices()
        if was_started:
            self._apply(fire=False)

    # --- parameters ----------------------------------------------------------

    def setEffect(self, **kwargs):
        if 'duration' in kwargs:
            self.duration_ms = int(kwargs['duration'])
        if 'gain' in kwargs:
            self._gain = clamp(int(kwargs['gain']), 0, 4096) / 4096.0
        if self._started:
            self._apply(fire=False)

    def setCondition(self, condition):
        log.debug(f"{self!r}: condition parameters have no meaning on a shaker; ignored")

    def setPeriodic(self, freq, magnitude, direction, duration=0, **kwargs):
        self.frequency = float(freq) if freq else 0.0
        self.magnitude = clamp(float(magnitude), 0.0, 1.0)
        self.direction = float(direction) % 360.0
        self.duration_ms = int(duration)
        if self._started:
            self._apply(fire=False)
        return self

    def setConstantForce(self, magnitude, direction, **kwargs):
        self.magnitude = clamp(abs(float(magnitude)), 0.0, 1.0)
        self.direction = float(direction) % 360.0
        self._ac_update(self.magnitude)
        if self._started:
            self._apply(fire=False)
        return self

    def setEnvelope(self, envelope):
        attack = int(getattr(envelope, 'attackTime', 0) or 0)
        self._ramp_ms = float(attack) if attack > 0 else None

    # --- the rules -----------------------------------------------------------

    def _ac_update(self, magnitude: float) -> None:
        """AC-couple the constant force: keep a slow estimate of its
        steady part and drive the voice with what is left."""
        now = self.device.clock()
        if self._ac_time is None:
            # the first value is the steady part: a force that appears
            # from nothing is a step, and its onset is what is felt
            self._ac_steady = 0.0
        else:
            dt = max(0.0, now - self._ac_time)
            alpha = clamp(dt / self.AC_TAU_S, 0.0, 1.0)
            self._ac_steady += (magnitude - self._ac_steady) * alpha
        self._ac_time = now
        self._ac_drive = clamp(abs(magnitude - self._ac_steady) * self.AC_GAIN, 0.0, 1.0)

    def _apply(self, fire: bool) -> None:
        device = self.device
        synth = device.synth
        sr = synth.samplerate
        ramp = self._ramp_ms if self._ramp_ms is not None else 50.0
        with synth.lock:
            voices = {}
            for group, profile in device.groups_for(self.placement).items():
                key = self._key(group)
                amp = clamp(self.magnitude * self._gain * profile.gain, 0.0, 1.0)
                if self.type == EFFECT_CONSTANT:
                    amp = clamp(self._ac_drive * self._gain * profile.gain, 0.0, 1.0)
                    voice = synth.voice(key, Oscillator, group)
                    voice.set(profile.carrier_hz, amp, ramp_ms=20.0)
                    voice.expire_after(int(self.AC_HOLD_S * sr))
                    self._kind = 'constant'
                elif 0 < self.duration_ms <= TRANSIENT_MS:
                    voice = synth.voice(key, Oscillator, group)
                    if fire and amp > 0.0:
                        carrier = (profile.fold(self.frequency)
                                   if self.frequency >= profile.band_low_hz else profile.carrier_hz)
                        voice.trigger_pulse(carrier, profile.halfwaves, amp,
                                            profile.attack_ms, profile.release_ms,
                                            profile.brake_amp(amp), profile.brake_delay_ms)
                    self._kind = 'pulse'
                elif self.frequency > 0.0 and self.frequency < profile.band_low_hz:
                    voice = synth.voice(key, ImpulseTrain, group)
                    voice.configure(carrier_hz=profile.carrier_hz, halfwaves=profile.halfwaves,
                                    attack_ms=profile.attack_ms, release_ms=profile.release_ms,
                                    brake_amp=profile.brake_amp(amp),
                                    brake_delay_ms=profile.brake_delay_ms,
                                    gain=1.0, max_rate_hz=profile.band_low_hz)
                    voice.set_rate(self.frequency, load=amp)
                    voice.expire_after(self._lifetime(sr))
                    self._kind = 'train'
                else:
                    voice = synth.voice(key, Oscillator, group)
                    voice.set(profile.fold(self.frequency), amp, ramp)
                    voice.expire_after(self._lifetime(sr))
                    self._kind = 'tone'
                voices[group] = voice
            # groups that went away take their voices with them
            for group in set(self._voices) - set(voices):
                synth.remove(self._key(group))
            self._voices = voices

    def _lifetime(self, sr: int) -> Optional[int]:
        return int(self.duration_ms * sr / 1000.0) if self.duration_ms > 0 else None


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

def stream_width(output, needed: int) -> Optional[int]:
    """The channel count to open ``output`` with for routes that need
    ``needed`` channels: that count when the output takes it, else the
    output's native width when that is at least as wide (a shared-mode
    WASAPI endpoint accepts only its own width; the extra channels stay
    silent), else None.  An output without a probe takes what it is
    given."""
    probe = getattr(output, 'supports_channels', None)
    if probe is None or probe(needed):
        return needed
    native = getattr(output, 'native_channels', None)
    native = native() if native is not None else None
    if native and native >= needed and probe(native):
        return native
    return None


def _routes_for(transducers: Sequence[Transducer], profiles: Sequence[ShakerProfile],
                fallback: str, placement: Placement = EVERYWHERE,
                samplerate: int = 48000) -> Tuple[List[Route], Dict[str, ShakerProfile]]:
    """The mixer routes and the profile groups a transducer list needs
    for one placement: one group per distinct profile among the rows the
    placement reaches, one route per such row (late where the placement
    says so).  A placement no row can honor reaches every row instead -
    a rig with one transducer never loses an effect to a placement it
    cannot satisfy.  Group names carry the placement key, so the same
    profile under two placements renders twice, once each."""
    rows = [t for t in transducers if placement.hears(t.position)]
    if not rows:
        rows, placement = list(transducers), EVERYWHERE
    groups: Dict[str, ShakerProfile] = {}
    routes = []
    delay = int(round(placement.delay_ms * samplerate / 1000.0))
    for t in rows:
        profile = find_profile(profiles, t.profile, fallback)
        group = f"{profile.name}|{placement.key}"
        groups[group] = profile
        routes.append(Route(t.channel, group, t.gain,
                            delay if placement.late(t.position) else 0))
    return routes, groups


class ShakerFFBDevice(ffb_backend.BaseFFBDevice):
    """A bass shaker rig, driven through a sound card.

    Opens the output on construction, like the other backends open their
    hardware, and raises ``ShakerOutputError`` when it cannot, so startup
    treats it as a failed open and retries.  A card that goes away while
    playing is noticed through the stream ending: ``connected`` drops,
    ``deviceConnected(False)`` is emitted from the reconnect timer, and
    the timer keeps trying to reopen; on success ``deviceReconnected``
    fires.  The voices live in software, so nothing is lost across a
    reopen - every started effect resumes on its next frame.
    """

    @property
    def caps(self) -> ffb_backend.DeviceCapabilities:
        return SHAKER_CAPABILITIES

    @property
    def connected(self) -> bool:
        return not self._lost and self.synth.running

    def __init__(self, output_device=None, output=None, samplerate: int = 48000,
                 blocksize: int = 512, gain: float = DEFAULT_GAIN,
                 transducers: Optional[Sequence[Transducer]] = None,
                 profiles: Optional[Sequence[ShakerProfile]] = None,
                 profile: Optional[ShakerProfile] = None,
                 placement_resolver: Optional[Callable[[Optional[str]], Placement]] = None,
                 reconnect_interval_ms: int = 2000, autostart: bool = True,
                 clock=time.monotonic) -> None:
        # plain assignments only before super().__init__ (QObject caveat)
        self.output_device = output_device
        self.clock = clock
        # where an effect plays, by its name: the shaker child hands in a
        # resolver over its settings tree; without one everything plays
        # everywhere
        self.placement_resolver = placement_resolver
        self._placements: Dict[str, Placement] = {}
        # the pack the rows' profile names resolve against; a profile
        # passed outright (tests, previews) is the pack and the fallback
        if profile is not None:
            profiles = [profile] + [p for p in (profiles or []) if p.name != profile.name]
        self.profiles: List[ShakerProfile] = list(profiles) if profiles else [DEFAULT_PROFILE]
        self._fallback_profile = self.profiles[0].name
        if output is None:
            output = SoundDeviceOutput(output_device, samplerate, blocksize)
        self._output = output
        wanted = list(transducers) if transducers else default_transducers(self._fallback_profile)
        rows, channels = self._plan(wanted)
        self._samplerate = samplerate
        self.synth = ShakerSynth(samplerate, blocksize, gain, [], channels, output)
        self.synth.on_finished = self._output_lost
        self.transducers: List[Transducer] = wanted
        self._driven: List[Transducer] = rows
        self.groups: Dict[str, ShakerProfile] = {}
        self._rebuild_routes()
        self.info = ShakerDeviceInfo(str(output_device or ''))
        self.firmware_version = None
        self._handles: List[weakref.ref] = []
        self._next_id = 1
        self._lost = False
        self._announced = True
        self._shutdown = False
        self._declined_types = set()

        super().__init__()
        self._timer_id = None
        if reconnect_interval_ms:
            self._timer_id = self.startTimer(int(reconnect_interval_ms))
        if autostart:
            self._open(first=True)

    @property
    def profile(self) -> ShakerProfile:
        """The first transducer's profile: what single-transducer callers
        mean by 'the profile'."""
        if self._driven:
            return find_profile(self.profiles, self._driven[0].profile, self._fallback_profile)
        return self.profiles[0]

    # --- placement -------------------------------------------------------

    def placement_for(self, label: Optional[str]) -> Placement:
        """Where the effect called ``label`` plays.  Resolved once per name
        and remembered; ``forget_placements`` clears the memory when the
        settings behind the resolver change."""
        if label in self._placements:
            return self._placements[label]
        placement = EVERYWHERE
        if self.placement_resolver is not None:
            try:
                placement = self.placement_resolver(label) or EVERYWHERE
            except Exception:
                log.exception(f"shaker: placement for {label!r} could not be resolved; playing everywhere")
        self._placements[label] = placement
        return placement

    def groups_for(self, placement: Placement) -> Dict[str, ShakerProfile]:
        """The voice groups an effect with ``placement`` renders into,
        making their routes the first time the placement is seen."""
        wanted = {}
        for t in self._driven:
            if placement.hears(t.position):
                wanted[t.profile] = True
        routes, groups = _routes_for(self._driven, self.profiles, self._fallback_profile,
                                     placement, self._samplerate)
        missing = [g for g in groups if g not in self.groups]
        if missing:
            self.groups.update(groups)
            self.synth.set_routes(self.synth.routes + [r for r in routes if r.group in missing],
                                  self.synth.channels)
        return groups

    def forget_placements(self) -> None:
        """The settings behind the resolver changed: re-resolve every
        effect on its next update and drop the routes that are no longer
        used."""
        self._placements.clear()
        self._rebuild_routes()
        for ref in list(self._handles):
            handle = ref()
            if handle is not None:
                handle.regroup()

    def _rebuild_routes(self) -> None:
        """Start the fan-out from the rows alone: the everywhere group per
        profile.  Placements add their groups as effects ask for them."""
        routes, groups = _routes_for(self._driven, self.profiles, self._fallback_profile,
                                     EVERYWHERE, self._samplerate)
        self.groups = groups
        self.synth.set_routes(routes, self.synth.channels)

    def _plan(self, transducers: Sequence[Transducer]):
        """The rows the output can drive and the channel count to open it
        with, dropping (with a warning) rows on channels the output does
        not have: an output that refuses the count loses its highest rows
        until it accepts, so whatever fits is still driven."""
        rows = list(transducers)
        channels = 1
        while rows:
            needed = max(t.channel for t in rows) + 1
            width = stream_width(self._output, needed)
            if width is not None:
                channels = width
                break
            highest = needed - 1
            dropped = [t for t in rows if t.channel == highest]
            rows = [t for t in rows if t.channel != highest]
            log.warning(f"shaker output {self.output_device!r} has no channel "
                        f"{highest + 1}; {', '.join(t.name for t in dropped)} not driven")
        return rows, channels

    # --- output ----------------------------------------------------------

    def _open(self, first: bool = False) -> bool:
        try:
            self.synth.start()
        except Exception as e:
            if first:
                raise ShakerOutputError(
                    f"could not open the audio output {self.output_device!r}: {e}") from e
            log.debug(f"shaker output reopen failed: {e}")
            return False
        rows = ', '.join(f"{t.name} on ch {t.channel + 1} ({t.profile or self._fallback_profile})"
                         for t in self.transducers) or 'no transducers'
        log.info(f"Shaker output open: {self.info.product_string} "
                 f"({self.synth.samplerate} Hz, {self.synth.blocksize} samples, "
                 f"{self.synth.channels} ch); {rows}")
        return True

    def _output_lost(self) -> None:
        """Runs on the audio thread when the stream ends by itself."""
        if self._shutdown:
            return
        self._lost = True
        self._announced = False

    def timerEvent(self, event) -> None:
        self._tick()

    def _tick(self) -> None:
        """Announce a lost output once, then keep trying to reopen it."""
        if self._shutdown or not self._lost:
            return
        if not self._announced:
            self._announced = True
            log.warning("Shaker output stopped: the sound card went away or the stream failed; reconnecting")
            self.deviceConnected.emit(False)
        if self._open():
            self._lost = False
            log.info("Shaker output restored")
            self.deviceConnected.emit(True)
            self.deviceReconnected.emit()

    def shutdown(self) -> None:
        self._shutdown = True
        if self._timer_id is not None:
            try:
                self.killTimer(self._timer_id)
            except Exception:
                pass
            self._timer_id = None
        self.synth.stop()
        self.synth.clear()

    def set_master_gain(self, gain: float) -> None:
        self.synth.set_master_gain(gain)

    def set_transducers(self, transducers: Sequence[Transducer]) -> None:
        """Replace the rows live.  The routes change under the running
        stream; a row on a channel the open stream does not have is
        dropped with a warning, since the width is fixed at open.  Every
        started effect re-renders into the new profile groups."""
        rows = []
        for t in transducers:
            if t.channel < self.synth.channels:
                rows.append(t)
            else:
                log.warning(f"shaker: {t.name} is on channel {t.channel + 1} but the output "
                            f"was opened with {self.synth.channels}; not driven until a restart")
        self.transducers, self._driven = list(transducers), rows
        self._rebuild_routes()
        for ref in list(self._handles):
            handle = ref()
            if handle is not None:
                handle.regroup()

    def apply_settings(self, settings) -> None:
        """Take the stored gain and transducer rows live, after a settings
        save."""
        values = shaker_settings(settings)
        self.profiles = values['profiles']
        self.set_master_gain(values['gain'])
        self.set_transducers(values['transducers'])
        rows = ', '.join(f"{t.name} ch {t.channel + 1} x{t.gain:.2f} {t.profile}" for t in self.transducers)
        log.info(f"Shaker settings applied: gain {values['gain']:.2f}; {rows}")

    # --- identity --------------------------------------------------------

    @property
    def serial(self):
        return None

    @property
    def product(self):
        return self.info.product_string

    # --- input -----------------------------------------------------------

    def get_input(self):
        return _NO_INPUT

    # --- effects ---------------------------------------------------------

    def create_effect(self, type) -> Optional[ShakerEffectHandle]:
        if type not in SUPPORTED_EFFECTS:
            if type not in self._declined_types:
                self._declined_types.add(type)
                log.debug(f"create_effect: {effect_names.get(type, type)} has no meaning on a shaker")
            return None
        if not self.connected:
            log.warning("create_effect: shaker output not open")
            return None
        handle = ShakerEffectHandle(self, self._next_id, type)
        self._next_id += 1
        self._handles.append(weakref.ref(handle, lambda ref: self._handles.remove(ref)))
        return handle

    def reset_effects(self):
        log.info("Shaker: reset effects")
        for ref in list(self._handles):
            handle = ref()
            if handle is not None and handle.effect_id:
                handle.invalidate()
        self.synth.clear()

    def play_test_pulse(self, amplitude: float = 0.8) -> None:
        """One pulse per profile, everywhere, each shaped by its profile, so
        every transducer hears its own hit."""
        with self.synth.lock:
            for group, p in self.groups_for(EVERYWHERE).items():
                voice = self.synth.voice(f'__test__:{group}', Oscillator, group)
                voice.trigger_pulse(p.carrier_hz, p.halfwaves, amplitude, p.attack_ms, p.release_ms,
                                    p.brake_amp(amplitude), p.brake_delay_ms)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

class ShakerPreview:
    """A second of sound on an output, for the settings card: one pulse
    shaped by each transducer's profile, then a short tone at its
    carrier, through the gains being configured.  Opens its own stream,
    so it works whether or not the shaker child is running (Windows
    mixes shared-mode streams), and ``stop`` closes it; the caller
    decides when, since the card runs on Qt's timers.  ``only`` limits
    the sound to one row, which is how a user tells the transducers
    apart.
    """

    PULSE_AMPLITUDE = 0.8
    TONE_AMPLITUDE = 0.5
    TONE_START_S = 0.3
    TONE_LENGTH_S = 0.6
    LENGTH_S = 1.0

    def __init__(self, output_device=None, transducers: Optional[Sequence[Transducer]] = None,
                 profiles: Optional[Sequence[ShakerProfile]] = None, gain: float = DEFAULT_GAIN,
                 only: Optional[int] = None, samplerate: int = 48000, blocksize: int = 512,
                 output=None):
        self.profiles = list(profiles) if profiles else [DEFAULT_PROFILE]
        rows = list(transducers) if transducers else default_transducers(self.profiles[0].name)
        if only is not None and 0 <= only < len(rows):
            rows = [rows[only]]
        self.transducers = rows
        if output is None:
            output = SoundDeviceOutput(output_device, samplerate, blocksize)
        needed = max(t.channel for t in rows) + 1
        channels = stream_width(output, needed)
        if channels is None:
            raise ShakerOutputError(f"the output has no channel {needed}")
        routes, self.groups = _routes_for(rows, self.profiles, self.profiles[0].name)
        self.synth = ShakerSynth(samplerate, blocksize, gain, routes, channels, output)

    def start(self) -> None:
        """Open the output and fire the pulses."""
        with self.synth.lock:
            for group, p in self.groups.items():
                pulse = self.synth.voice(f'pulse:{group}', Oscillator, group)
                pulse.trigger_pulse(p.carrier_hz, p.halfwaves, self.PULSE_AMPLITUDE,
                                    p.attack_ms, p.release_ms,
                                    p.brake_amp(self.PULSE_AMPLITUDE), p.brake_delay_ms)
        self.synth.start()

    def cue_tone(self) -> None:
        """Start the tones; the caller times this after the pulses."""
        with self.synth.lock:
            for group, p in self.groups.items():
                tone = self.synth.voice(f'tone:{group}', Oscillator, group)
                tone.set(p.carrier_hz, self.TONE_AMPLITUDE, ramp_ms=30.0)
                tone.expire_after(int(self.TONE_LENGTH_S * self.synth.samplerate))

    def stop(self) -> None:
        self.synth.stop()
        self.synth.clear()
