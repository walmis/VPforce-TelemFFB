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

The synthesis and the calibration profiles are adapted from 89Huey89's
shaker work (https://github.com/89Huey89/vpforce-telemffb-Shaker).
"""

import json
import logging
import os
import sys
import time
import weakref
from dataclasses import dataclass, fields
from typing import List, Optional, Tuple

from telemffb.hw import ffb_backend
from telemffb.hw.ffb_rhino import (
    EFFECT_CONSTANT, PERIODIC_EFFECTS, effect_names,
)
from telemffb.hw.shaker_synth import (
    ImpulseTrain, Oscillator, ShakerSynth, SoundDeviceOutput, channel_gains, clamp,
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

#: how the mono mix lands on a stereo output
CHANNEL_MODES = ('mono', 'left', 'right', 'pan')

#: the shaker's own settings, global keys like the launch options
SETTING_GAIN = 'shakerGain'
SETTING_MODE = 'shakerChannelMode'
SETTING_PAN = 'shakerPan'
SETTING_PROFILE = 'shakerProfile'


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


def find_profile(profiles: List[ShakerProfile], name: str, fallback: str = '') -> ShakerProfile:
    """The profile called ``name``, else the one called ``fallback``, else
    the first - a pack never resolves to nothing."""
    by_name = {p.name: p for p in profiles}
    return by_name.get(name) or by_name.get(fallback) or profiles[0]


def shaker_settings(settings) -> dict:
    """The shaker's stored configuration as constructor arguments: gain,
    channel mode, pan and the calibration profile, each falling back to
    its default when unset or unreadable (registry values arrive as
    strings)."""
    def number(key, default):
        try:
            return float(settings.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    mode = str(settings.get(SETTING_MODE, 'mono') or 'mono').lower()
    if mode not in CHANNEL_MODES:
        mode = 'mono'
    profiles, active = load_profiles(default_profiles_path())
    wanted = str(settings.get(SETTING_PROFILE, '') or '')
    return {
        'gain': clamp(number(SETTING_GAIN, DEFAULT_GAIN), 0.0, 10.0),
        'channel_mode': mode,
        'pan': clamp(number(SETTING_PAN, 0.0), -1.0, 1.0),
        'profile': find_profile(profiles, wanted, active),
    }


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
    """One effect, rendered as the voice its parameters call for.

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
        self._key = str(effect_id)
        self._voice = None
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
        return f"ShakerEffectHandle({self.effect_id}, {self.name})"

    @property
    def started(self) -> bool:
        return self._started

    @property
    def voice(self):
        return self._voice

    @property
    def kind(self) -> Optional[str]:
        """What the effect renders as right now: 'pulse', 'tone', 'train',
        'constant', or None before it starts."""
        return self._kind

    @property
    def intensity(self):
        """How hard this effect is currently driving the transducer, 0.0 to
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
        self._drop_voice()
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
            if self._voice is not None:
                self._voice.stop()
        self._started = False
        return self

    def destroy(self):
        if self.effect_id:
            log.debug(f"Destroying effect {self.effect_id} ({self.name})")
            self._drop_voice()
            self.type = 0
            self.effect_id = None
            self._started = False

    def _drop_voice(self):
        synth = self.device.synth
        with synth.lock:
            synth.remove(self._key)
        self._voice = None
        self._kind = None

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

    def _amplitude(self) -> float:
        return clamp(self.magnitude * self._gain * self.device.profile.gain, 0.0, 1.0)

    def _apply(self, fire: bool) -> None:
        device = self.device
        synth = device.synth
        profile = device.profile
        sr = synth.samplerate
        ramp = self._ramp_ms if self._ramp_ms is not None else 50.0
        with synth.lock:
            if self.type == EFFECT_CONSTANT:
                amp = clamp(self._ac_drive * self._gain * profile.gain, 0.0, 1.0)
                voice = synth.voice(self._key, Oscillator)
                voice.set(profile.carrier_hz, amp, ramp_ms=20.0)
                voice.expire_after(int(self.AC_HOLD_S * sr))
                self._kind = 'constant'
            elif 0 < self.duration_ms <= TRANSIENT_MS:
                voice = synth.voice(self._key, Oscillator)
                if fire:
                    carrier = (profile.fold(self.frequency)
                               if self.frequency >= profile.band_low_hz else profile.carrier_hz)
                    amp = self._amplitude()
                    if amp > 0.0:
                        voice.trigger_pulse(carrier, profile.halfwaves, amp,
                                            profile.attack_ms, profile.release_ms,
                                            profile.brake_amp(amp), profile.brake_delay_ms)
                self._kind = 'pulse'
            elif self.frequency > 0.0 and self.frequency < profile.band_low_hz:
                voice = synth.voice(self._key, ImpulseTrain)
                amp = self._amplitude()
                voice.configure(carrier_hz=profile.carrier_hz, halfwaves=profile.halfwaves,
                                attack_ms=profile.attack_ms, release_ms=profile.release_ms,
                                brake_amp=profile.brake_amp(amp),
                                brake_delay_ms=profile.brake_delay_ms,
                                gain=1.0, max_rate_hz=profile.band_low_hz)
                voice.set_rate(self.frequency, load=amp)
                voice.expire_after(self._lifetime(sr))
                self._kind = 'train'
            else:
                voice = synth.voice(self._key, Oscillator)
                voice.set(profile.fold(self.frequency), self._amplitude(), ramp)
                voice.expire_after(self._lifetime(sr))
                self._kind = 'tone'
            self._voice = voice

    def _lifetime(self, sr: int) -> Optional[int]:
        return int(self.duration_ms * sr / 1000.0) if self.duration_ms > 0 else None


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

class ShakerFFBDevice(ffb_backend.BaseFFBDevice):
    """A bass shaker, driven through a sound card.

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
                 blocksize: int = 512, gain: float = DEFAULT_GAIN, channel_mode: str = 'mono',
                 pan: float = 0.0, channels: Optional[int] = None,
                 profile: Optional[ShakerProfile] = None,
                 reconnect_interval_ms: int = 2000, autostart: bool = True,
                 clock=time.monotonic) -> None:
        # plain assignments only before super().__init__ (QObject caveat)
        self.output_device = output_device
        self.profile = profile if profile is not None else DEFAULT_PROFILE
        self.channel_mode = channel_mode
        self.pan = float(pan)
        self.clock = clock
        if output is None:
            output = SoundDeviceOutput(output_device, samplerate, blocksize)
        self._output = output
        if channels is None:
            channels = 2
            probe = getattr(output, 'supports_channels', None)
            if probe is not None and not probe(2):
                channels = 1
        self.synth = ShakerSynth(samplerate, blocksize, gain,
                                 channel_gains(channel_mode, pan, channels), output)
        self.synth.on_finished = self._output_lost
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
        log.info(f"Shaker output open: {self.info.product_string} "
                 f"({self.synth.samplerate} Hz, {self.synth.blocksize} samples, "
                 f"{self.synth.channels} ch, mode {self.channel_mode}, "
                 f"profile {self.profile.name})")
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

    def set_channel_mode(self, mode: str, pan: Optional[float] = None) -> None:
        self.channel_mode = mode
        if pan is not None:
            self.pan = float(pan)
        self.synth.set_channel_gains(channel_gains(mode, self.pan, self.synth.channels))

    def apply_settings(self, settings) -> None:
        """Take the stored gain, channel mode, pan and profile live, after
        a settings save.  The profile is read by every effect on its next
        update, so a running tone changes shape without a restart."""
        values = shaker_settings(settings)
        self.set_master_gain(values['gain'])
        self.set_channel_mode(values['channel_mode'], values['pan'])
        self.profile = values['profile']
        log.info(f"Shaker settings applied: gain {values['gain']:.2f}, "
                 f"{values['channel_mode']}, pan {values['pan']:+.2f}, "
                 f"profile {self.profile.name}")

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
        """One pulse shaped by the profile, for the settings card."""
        voice = self.synth.voice('__test__', Oscillator)
        with self.synth.lock:
            voice.trigger_pulse(self.profile.carrier_hz, self.profile.halfwaves, amplitude,
                                self.profile.attack_ms, self.profile.release_ms,
                                self.profile.brake_amp(amplitude), self.profile.brake_delay_ms)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

class ShakerPreview:
    """A second of sound on an output, for the settings card's test
    button: one pulse shaped by the profile, then a short tone at its
    carrier, through the gain and channel mode being configured.  Opens
    its own stream, so it works whether or not the shaker child is
    running (Windows mixes shared-mode streams), and ``stop`` closes it;
    the caller decides when, since the card runs on Qt's timers.
    """

    PULSE_AMPLITUDE = 0.8
    TONE_AMPLITUDE = 0.5
    TONE_START_S = 0.3
    TONE_LENGTH_S = 0.6
    LENGTH_S = 1.0

    def __init__(self, output_device=None, profile: Optional[ShakerProfile] = None,
                 gain: float = DEFAULT_GAIN, channel_mode: str = 'mono', pan: float = 0.0,
                 samplerate: int = 48000, blocksize: int = 512, output=None):
        self.profile = profile if profile is not None else DEFAULT_PROFILE
        if output is None:
            output = SoundDeviceOutput(output_device, samplerate, blocksize)
        channels = 2
        probe = getattr(output, 'supports_channels', None)
        if probe is not None and not probe(2):
            channels = 1
        self.synth = ShakerSynth(samplerate, blocksize, gain,
                                 channel_gains(channel_mode, pan, channels), output)

    def start(self) -> None:
        """Open the output and fire the pulse."""
        p = self.profile
        pulse = self.synth.voice('pulse', Oscillator)
        with self.synth.lock:
            pulse.trigger_pulse(p.carrier_hz, p.halfwaves, self.PULSE_AMPLITUDE,
                                p.attack_ms, p.release_ms,
                                p.brake_amp(self.PULSE_AMPLITUDE), p.brake_delay_ms)
        self.synth.start()

    def cue_tone(self) -> None:
        """Start the tone; the caller times this after the pulse."""
        p = self.profile
        tone = self.synth.voice('tone', Oscillator)
        with self.synth.lock:
            tone.set(p.carrier_hz, self.TONE_AMPLITUDE, ramp_ms=30.0)
            tone.expire_after(int(self.TONE_LENGTH_S * self.synth.samplerate))

    def stop(self) -> None:
        self.synth.stop()
        self.synth.clear()
