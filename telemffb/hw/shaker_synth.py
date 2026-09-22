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

"""Bass shaker synthesis: the voices, the mixer and the audio output.

The shaker backend (ffb_shaker.py) turns TelemFFB's effects into voices
here.  A voice renders float32 mono blocks on the audio thread; the mixer
sums the voices, applies the master gain and fans the result out to the
output channels.  Nothing in this module knows about effects, telemetry or
Qt, so the whole thing runs - and is tested - without a sound card: the
output is an object handed in, and ``render_block`` is callable directly.

The synthesis itself is the work of 89Huey89
(https://github.com/89Huey89/vpforce-telemffb-Shaker), carried over onto
TelemFFB's device backend contract: phase-continuous sines with click-free
amplitude ramps, gated half-wave pulses with an active brake to stop a
heavy transducer ringing, and rate-locked impulse trains that fall back to
band-limited noise above the rate a transducer can resolve as separate
hits.  The band-pass noise runs at a decimated rate so it stays numpy-only.
"""

import logging
import math
import re
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

TWO_PI = 2.0 * math.pi

log = logging.getLogger(__name__)


def clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


# ---------------------------------------------------------------------------
# Pulse shape
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PulseShape:
    """One gated pulse, in samples.

    ``drive_env`` multiplies a fresh sine that starts at phase zero: linear
    attack, sustain, linear release, ``drive_end`` samples long.  After a
    gap of silence the ``brake_signal`` - one phase-inverted half sine -
    REPLACES the running sine between ``brake_start`` and ``brake_end``, so
    the brake polarity is exact whatever the carrier phase would have been.
    Shared with the waveform preview, so what is drawn is what plays.
    """
    drive_env: np.ndarray
    brake_signal: np.ndarray
    drive_end: int
    brake_start: int
    brake_end: int

    @property
    def total(self) -> int:
        return self.brake_end


def build_pulse_shape(samplerate: int, carrier_hz: float, halfwaves: int,
                      amplitude: float, attack_ms: float, release_ms: float,
                      brake_amp: float = 0.0, brake_delay_ms: float = 0.0
                      ) -> PulseShape:
    """Drive envelope and brake half-wave for one pulse.

    Durations are half-wave precise: the drive lasts ``halfwaves`` half
    periods of the carrier, so a one-half-wave pulse at 50 Hz is 10 ms.
    Attack and release are each capped at half the drive, so they never
    overlap.
    """
    sr = float(samplerate)
    carrier = max(0.1, float(carrier_hz))
    halfwaves = max(1, int(halfwaves))
    amplitude = clamp(float(amplitude), 0.0, 1.0)
    drive = max(1, int(round(halfwaves / (2.0 * carrier) * sr)))
    attack = clamp(int(round(attack_ms / 1000.0 * sr)), 0, drive // 2)
    release = clamp(int(round(release_ms / 1000.0 * sr)), 0, drive // 2)

    env = np.full(drive, amplitude, dtype=np.float64)
    if attack:
        env[:attack] = np.linspace(0.0, amplitude, attack, endpoint=False)
    if release:
        env[drive - release:] = np.linspace(amplitude, 0.0, release, endpoint=False)

    if brake_amp > 0.0 and brake_delay_ms >= 0.0:
        gap = max(0, int(round(brake_delay_ms / 1000.0 * sr)))
        brake_len = max(1, int(round(1.0 / (2.0 * carrier) * sr)))
        t = np.arange(brake_len, dtype=np.float64) / sr
        brake = -clamp(float(brake_amp), 0.0, 1.0) * np.sin(TWO_PI * carrier * t)
        return PulseShape(env, brake, drive, drive + gap, drive + gap + brake_len)
    return PulseShape(env, np.zeros(0, dtype=np.float64), drive, drive, drive)


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------

class _Expiring:
    """A lifetime in samples, counted down by render(): the effect's
    ``duration`` without a timer thread, so a timed effect ends at the
    same sample every time and tests can drive it by rendering."""

    _expire: Optional[int] = None

    def expire_after(self, samples: Optional[int]) -> None:
        self._expire = None if samples is None else max(1, int(samples))

    def _count_down(self, n: int) -> bool:
        """True when the lifetime ran out during this block."""
        if self._expire is None:
            return False
        self._expire -= n
        if self._expire > 0:
            return False
        self._expire = None
        return True


class Oscillator(_Expiring):
    """One phase-continuous sine.

    Three ways to sound: ``set`` a steady tone with a linear amplitude ramp
    toward the target (click-free changes while an effect updates every
    frame); ``trigger`` a one-shot with a linear attack and an exponential
    decay; ``trigger_pulse`` a gated half-wave pulse with optional brake.
    Setters are safe from any thread; render() runs on the audio thread
    under the mixer's lock.
    """

    def __init__(self, samplerate: int, blocksize: int = 512):
        self._sr = float(samplerate)
        self._phase = 0.0
        self._freq = 0.0
        self._amp = 0.0
        self._target = 0.0
        self._step = 0.0
        # one-shot envelope (trigger)
        self._env_active = False
        self._env_mode = 'classic'   # 'classic' | 'pulse'
        self._env_pos = 0
        self._env_attack = 0
        self._env_total = 0
        self._env_peak = 0.0
        self._env_k = 0.0
        # pulse (trigger_pulse)
        self._pulse: Optional[PulseShape] = None
        self._pulse_pos = 0
        self._capacity(int(blocksize))

    def _capacity(self, n: int) -> None:
        self._out = np.zeros(n, dtype=np.float32)
        self._idx = np.arange(n, dtype=np.float64)
        self._work = np.zeros(n, dtype=np.float64)
        self._work2 = np.zeros(n, dtype=np.float64)

    def set(self, freq: float, amplitude: float, ramp_ms: float = 50.0) -> None:
        amplitude = clamp(float(amplitude), 0.0, 1.0)
        ramp = max(1, int(self._sr * ramp_ms / 1000.0))
        self._freq = float(freq)
        self._target = amplitude
        self._step = (self._target - self._amp) / ramp
        self._env_active = False
        self._env_mode = 'classic'

    def stop(self, ramp_ms: float = 50.0) -> None:
        ramp = max(1, int(self._sr * ramp_ms / 1000.0))
        self._target = 0.0
        self._step = -self._amp / ramp
        self._env_active = False
        self._env_mode = 'classic'
        self._expire = None

    def trigger(self, freq: float, amplitude: float,
                attack_ms: float = 4.0, decay_ms: float = 90.0) -> None:
        amplitude = clamp(float(amplitude), 0.0, 1.0)
        attack = max(1, int(self._sr * attack_ms / 1000.0))
        decay = max(1, int(self._sr * decay_ms / 1000.0))
        self._freq = float(freq)
        self._env_attack = attack
        self._env_total = attack + decay
        self._env_peak = amplitude
        self._env_k = math.log(256.0) / decay
        self._env_pos = 0
        self._env_active = True
        self._env_mode = 'classic'
        self._amp = self._target = self._step = 0.0

    def trigger_pulse(self, carrier_hz: float, halfwaves: int, amplitude: float,
                      attack_ms: float = 1.5, release_ms: float = 2.0,
                      brake_amp: float = 0.0, brake_delay_ms: float = 0.0) -> None:
        """One gated pulse; the phase resets so the carrier starts at a
        zero crossing, which the half-wave-precise duration relies on."""
        self._pulse = build_pulse_shape(int(self._sr), carrier_hz, halfwaves,
                                        amplitude, attack_ms, release_ms,
                                        brake_amp, brake_delay_ms)
        self._freq = max(0.1, float(carrier_hz))
        self._phase = 0.0
        self._pulse_pos = 0
        self._env_mode = 'pulse'
        self._env_active = True
        self._amp = self._target = self._step = 0.0

    @property
    def is_silent(self) -> bool:
        if self._env_active:
            return False
        return self._amp == 0.0 and self._target == 0.0

    @property
    def frequency(self) -> float:
        return self._freq

    @property
    def amplitude(self) -> float:
        return self._amp

    def render(self, n: int) -> np.ndarray:
        if n > self._out.size:
            self._capacity(n)
        out = self._out[:n]
        if self._count_down(n):
            self.stop()
        if self.is_silent:
            out[:] = 0.0
            return out
        if self._env_active and self._env_mode == 'pulse':
            self._render_pulse(out)
            return out

        d_phi = TWO_PI * self._freq / self._sr
        phase = self._work[:n]
        np.multiply(self._idx[:n], d_phi, out=phase)
        phase += self._phase
        sine = self._work2[:n]
        np.sin(phase, out=sine)
        self._phase = math.fmod(self._phase + d_phi * n, TWO_PI)

        if self._env_active:
            env = self._work[:n]          # phase is consumed; reuse
            self._fill_envelope(env)
            sine *= env
        elif self._amp == self._target:
            sine *= self._amp
        else:
            amps = self._work[:n]
            step = self._step
            to_go = 0 if step == 0.0 else max(0, int(math.ceil((self._target - self._amp) / step)))
            if to_go >= n:
                np.multiply(self._idx[:n], step, out=amps)
                amps += self._amp
                self._amp += step * n
            else:
                if to_go:
                    np.multiply(self._idx[:to_go], step, out=amps[:to_go])
                    amps[:to_go] += self._amp
                amps[to_go:] = self._target
                self._amp = self._target
            sine *= amps
        out[:] = sine
        return out

    def _fill_envelope(self, env: np.ndarray) -> None:
        n = env.size
        t = self._idx[:n] + self._env_pos
        attack, total, peak = self._env_attack, self._env_total, self._env_peak
        env[:] = 0.0
        rising = t < attack
        np.multiply(t, peak / attack, out=env, where=rising)
        falling = (t >= attack) & (t < total)
        if falling.any():
            decay = peak * np.exp(-self._env_k * (t - attack))
            np.copyto(env, decay, where=falling)
        self._env_pos += n
        if self._env_pos >= total:
            self._env_active = False
            self._env_pos = 0

    def _render_pulse(self, out: np.ndarray) -> None:
        n = out.size
        out[:] = 0.0
        shape = self._pulse
        start = self._pulse_pos
        end = start + n
        if start < shape.drive_end:
            lo, hi = start, min(shape.drive_end, end)
            t = np.arange(lo, hi, dtype=np.float64) / self._sr
            out[lo - start:hi - start] = np.sin(TWO_PI * self._freq * t) * shape.drive_env[lo:hi]
        if shape.brake_signal.size and start < shape.brake_end and end > shape.brake_start:
            lo, hi = max(start, shape.brake_start), min(shape.brake_end, end)
            out[lo - start:hi - start] = shape.brake_signal[lo - shape.brake_start:hi - shape.brake_start]
        self._pulse_pos = end
        if end >= shape.brake_end:
            self._env_active = False
            self._env_mode = 'classic'
            self._pulse_pos = 0


class BandpassNoise(_Expiring):
    """Band-limited noise: white noise through one RBJ band-pass biquad.

    The biquad is a recursion, which numpy cannot vectorize, so it runs
    at one sixteenth of the sample rate in a short Python loop and the
    result is linearly interpolated up.  Everything this voice is asked
    for lives below a few hundred hertz, three octaves under the
    decimated Nyquist, so nothing audible is lost.  The output level is
    normalized to the filter's noise bandwidth, so the same amplitude
    feels the same at any sample rate or bandwidth.
    """

    DECIMATION = 16
    #: RMS of the output at amplitude 1.0 (Gaussian peaks then sit near 1.0)
    RMS_AT_FULL = 0.35

    def __init__(self, samplerate: int, blocksize: int = 512, seed=None):
        self._sr = float(samplerate)
        self._d = self.DECIMATION
        self._lr = self._sr / self._d
        self._center = 35.0
        self._bw = 20.0
        self._amp = 0.0
        self._target = 0.0
        self._step = 0.0
        self._rng = np.random.default_rng(seed)
        self._z1 = self._z2 = 0.0
        self._prev = 0.0
        self._next = 0.0
        self._carry = 0.0
        self._compute_coeffs()

    def set(self, center_hz: float, bandwidth_hz: float, amplitude: float,
            ramp_ms: float = 50.0) -> None:
        center = clamp(float(center_hz), 1.0, 0.45 * self._lr)
        bw = max(1.0, float(bandwidth_hz))
        if center != self._center or bw != self._bw:
            self._center, self._bw = center, bw
            self._compute_coeffs()
        self._target = clamp(float(amplitude), 0.0, 1.0)
        ramp = max(1, int(self._sr * ramp_ms / 1000.0))
        self._step = (self._target - self._amp) / ramp

    def stop(self, ramp_ms: float = 50.0) -> None:
        ramp = max(1, int(self._sr * ramp_ms / 1000.0))
        self._target = 0.0
        self._step = -self._amp / ramp
        self._expire = None

    @property
    def is_silent(self) -> bool:
        return self._amp <= 1e-6 and self._target <= 1e-6

    @property
    def center_hz(self) -> float:
        return self._center

    def _compute_coeffs(self) -> None:
        omega = TWO_PI * self._center / self._lr
        q = max(0.5, self._center / self._bw)
        alpha = math.sin(omega) / (2.0 * q)
        a0 = 1.0 + alpha
        self._b0 = alpha / a0
        self._b2 = -alpha / a0
        self._a1 = -2.0 * math.cos(omega) / a0
        self._a2 = (1.0 - alpha) / a0
        # unit white noise through a 0 dB-peak band-pass of this bandwidth
        # comes out at roughly sqrt(pi * bw / rate) RMS; undo that
        self._norm = self.RMS_AT_FULL / math.sqrt(math.pi * self._bw / self._lr)

    def _low_rate(self, count: int, into: np.ndarray) -> None:
        x = self._rng.standard_normal(count)
        b0, b2, a1, a2 = self._b0, self._b2, self._a1, self._a2
        z1, z2 = self._z1, self._z2
        for k in range(count):
            xn = x[k]
            yn = b0 * xn + z1
            z1 = -a1 * yn + z2
            z2 = b2 * xn - a2 * yn
            into[k] = yn
        self._z1, self._z2 = z1, z2

    def render(self, n: int) -> np.ndarray:
        if self._count_down(n):
            self.stop()
        if self.is_silent:
            return np.zeros(n, dtype=np.float32)

        positions = self._carry + np.arange(n, dtype=np.float64) / self._d
        q = self._carry + n / self._d
        j = int(math.floor(q))
        top = j + 1
        y = np.empty(top + 1, dtype=np.float64)
        y[0] = self._prev
        y[1] = self._next
        if top >= 2:
            self._low_rate(top - 1, y[2:])
        out = np.interp(positions, np.arange(top + 1, dtype=np.float64), y)
        self._prev = y[j]
        self._next = y[j + 1]
        self._carry = q - j

        if self._amp == self._target:
            env = self._amp
        else:
            env = self._amp + self._step * np.arange(1, n + 1, dtype=np.float64)
            if self._step > 0:
                np.minimum(env, self._target, out=env)
            else:
                np.maximum(env, self._target, out=env)
            self._amp = float(env[-1])
            if self._amp == self._target:
                self._step = 0.0
        out *= env * self._norm
        np.clip(out, -1.0, 1.0, out=out)
        return out.astype(np.float32)


class PhaseAccumulator:
    """Integrates a rate over caller-supplied time and reports how many
    whole cycles passed: blade passes, cylinder firings, runway joints."""

    def __init__(self) -> None:
        self._phase = 0.0

    def advance(self, rate_hz: float, dt: float) -> int:
        if rate_hz <= 0.0 or dt <= 0.0:
            return 0
        new = self._phase + rate_hz * dt
        crossings = int(new) - int(self._phase)
        self._phase = new - int(new)
        return crossings if crossings > 0 else 0

    def reset(self) -> None:
        self._phase = 0.0

    @property
    def phase(self) -> float:
        return self._phase


class ImpulseTrain(_Expiring):
    """One gated pulse per cycle of a rate set from telemetry.

    Above ``max_rate_hz`` a transducer no longer resolves the pulses as
    separate hits, so the voice crosses over to band-limited noise
    centered on the rate: the thrum continues instead of going silent.
    Hysteresis keeps a cruise RPM near the cap from flapping between the
    two, and both voices ramp over the crossover so it does not click.
    """

    HIGH_FRAC = 1.00
    LOW_FRAC = 0.85
    NOISE_BW_FRAC = 0.20
    NOISE_BW_MIN_HZ = 15.0
    CROSSFADE_MS = 80.0

    def __init__(self, samplerate: int, blocksize: int = 512,
                 max_rate_hz: float = 180.0):
        self._sr = float(samplerate)
        self._osc = Oscillator(samplerate, blocksize)
        self._noise = BandpassNoise(samplerate, blocksize)
        self._acc = PhaseAccumulator()
        self._lock = threading.Lock()
        self._rate = 0.0
        self._load = 0.0
        self._carrier_hz = 50.0
        self._halfwaves = 1
        self._attack_ms = 1.5
        self._release_ms = 3.0
        self._brake_amp = 0.0
        self._brake_delay_ms = 0.0
        self._gain = 1.0
        self._max_rate = float(max_rate_hz)
        self._noise_bw: Optional[float] = None
        self._fallback = False

    def configure(self, *, carrier_hz=None, halfwaves=None, attack_ms=None,
                  release_ms=None, brake_amp=None, brake_delay_ms=None,
                  gain=None, max_rate_hz=None, noise_bandwidth_hz=None) -> None:
        with self._lock:
            if carrier_hz is not None:
                self._carrier_hz = float(carrier_hz)
            if halfwaves is not None:
                self._halfwaves = max(1, int(halfwaves))
            if attack_ms is not None:
                self._attack_ms = float(attack_ms)
            if release_ms is not None:
                self._release_ms = float(release_ms)
            if brake_amp is not None:
                self._brake_amp = float(brake_amp)
            if brake_delay_ms is not None:
                self._brake_delay_ms = float(brake_delay_ms)
            if gain is not None:
                self._gain = float(gain)
            if max_rate_hz is not None:
                self._max_rate = float(max_rate_hz)
            if noise_bandwidth_hz is not None:
                v = float(noise_bandwidth_hz)
                self._noise_bw = v if v > 0.0 else None

    def set_rate(self, rate_hz: float, load: Optional[float] = None) -> None:
        with self._lock:
            self._rate = max(0.0, float(rate_hz))
            if load is not None:
                self._load = clamp(float(load), 0.0, 1.0)

    def stop(self, ramp_ms: float = 50.0) -> None:
        with self._lock:
            self._rate = 0.0
            self._fallback = False
        self._osc.stop(ramp_ms)
        self._noise.stop(ramp_ms)
        self._expire = None

    @property
    def is_silent(self) -> bool:
        if self._rate > 0.0:
            return False
        return self._osc.is_silent and self._noise.is_silent

    @property
    def fallback_active(self) -> bool:
        return self._fallback

    @property
    def rate_hz(self) -> float:
        return self._rate

    def render(self, n: int) -> np.ndarray:
        if self._count_down(n):
            self.stop()
        with self._lock:
            rate, load, gain, cap = self._rate, self._load, self._gain, self._max_rate
            carrier, halfwaves = self._carrier_hz, self._halfwaves
            attack, release = self._attack_ms, self._release_ms
            brake_amp, brake_delay = self._brake_amp, self._brake_delay_ms
            bw_override, fallback = self._noise_bw, self._fallback

        if rate > 0.0:
            if not fallback and rate >= cap * self.HIGH_FRAC:
                fallback = True
            elif fallback and rate < cap * self.LOW_FRAC:
                fallback = False
            amp = clamp(load * gain, 0.0, 1.0)
            if fallback:
                bw = bw_override if bw_override is not None else max(self.NOISE_BW_MIN_HZ, rate * self.NOISE_BW_FRAC)
                self._noise.set(rate, bw, amp, ramp_ms=self.CROSSFADE_MS)
            else:
                if not self._noise.is_silent:
                    self._noise.stop(ramp_ms=self.CROSSFADE_MS)
                if self._acc.advance(rate, n / self._sr) and amp > 0.0:
                    self._osc.trigger_pulse(carrier, halfwaves, amp, attack, release,
                                            brake_amp, brake_delay)
        else:
            if not self._noise.is_silent:
                self._noise.stop(ramp_ms=self.CROSSFADE_MS)
            fallback = False

        with self._lock:
            self._fallback = fallback
        return self._osc.render(n) + self._noise.render(n)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str
    host_api: str
    channels: int
    samplerate: float


#: host APIs in the order the device list prefers them when one card shows
#: up under several
HOST_API_PREFERENCE = ('Windows WASAPI', 'Windows DirectSound', 'MME', 'Windows WDM-KS')

#: the host APIs' own stand-ins for "whatever the default is" - the system
#: default is offered as its own choice, so these only add noise
VIRTUAL_OUTPUTS = frozenset(('Microsoft Sound Mapper - Output', 'Primary Sound Driver'))


def clean_device_name(name) -> str:
    """A device name as the list shows and stores it: whitespace runs
    collapsed and none left before a closing bracket.  Some drivers pad a
    name inside its brackets under one host API and not another, which
    would list one card as two and store the padding with it."""
    name = re.sub(r'\s+', ' ', str(name or ''))
    name = re.sub(r'\s+([)\]])', r'\1', name)
    return name.strip()


def sounddevice_available() -> bool:
    try:
        import sounddevice  # noqa: F401
    except Exception:
        return False
    return True


class SoundDeviceOutput:
    """An output stream on a sound card, through PortAudio.

    ``device`` is a PortAudio index, a device name (exact, then substring;
    a name that shows up under several host APIs resolves to the preferred
    one), or None for the system default.  The stream is opened with the
    'high' latency hint: a shaker reproduces tens of hertz, so twenty
    milliseconds of buffer is inaudible and the margin against the Python
    callback being late is what keeps the output free of clicks.
    """

    def __init__(self, device=None, samplerate: int = 48000, blocksize: int = 512,
                 latency='high'):
        self.device = device
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.latency = latency
        self._stream = None

    @property
    def running(self) -> bool:
        stream = self._stream
        return stream is not None and bool(stream.active)

    def start(self, callback: Callable, channels: int,
              finished_callback: Optional[Callable] = None) -> None:
        import sounddevice as sd
        index = self.resolve(self.device)
        self._stream = sd.OutputStream(
            samplerate=self.samplerate, blocksize=self.blocksize, device=index,
            channels=int(channels), dtype='float32', latency=self.latency,
            callback=callback, finished_callback=finished_callback)
        self._stream.start()

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            log.exception("closing the shaker output stream failed")

    def supports_channels(self, channels: int) -> bool:
        import sounddevice as sd
        try:
            sd.check_output_settings(device=self.resolve(self.device),
                                     samplerate=self.samplerate,
                                     channels=int(channels), dtype='float32')
        except Exception:
            return False
        return True

    @staticmethod
    def rescan() -> bool:
        """Make PortAudio look at the machine again.

        The library takes its device list once, when it initializes, so a
        card plugged in after that is invisible to a long-running process
        until the library is torn down and brought back up.  Never call
        this with a stream open in this process: the shaker child owns its
        stream, so the master's settings dialog is where this runs.
        """
        import sounddevice as sd
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            log.exception("audio device rescan failed")
            return False
        return True

    @staticmethod
    def list_devices(all_host_apis: bool = False) -> List[OutputDevice]:
        """Output-capable devices.  PortAudio lists one card once per host
        API; by default each name appears once, under the preferred API.
        Names are cleaned (see clean_device_name): some drivers pad a name
        under one API and not another, which would list one card as two."""
        import sounddevice as sd
        try:
            apis = [a.get('name', '') for a in sd.query_hostapis()]
            raw = sd.query_devices()
        except Exception:
            log.exception("audio device enumeration failed")
            return []
        found: List[OutputDevice] = []
        for i, d in enumerate(raw):
            if d.get('max_output_channels', 0) <= 0:
                continue
            api_index = d.get('hostapi', -1)
            api = apis[api_index] if 0 <= api_index < len(apis) else ''
            found.append(OutputDevice(i, clean_device_name(d.get('name', '')), api,
                                      int(d['max_output_channels']),
                                      float(d.get('default_samplerate', 0.0))))
        if all_host_apis:
            return found

        def rank(dev: OutputDevice) -> int:
            return HOST_API_PREFERENCE.index(dev.host_api) if dev.host_api in HOST_API_PREFERENCE else len(HOST_API_PREFERENCE)

        # MME truncates names to 31 characters, so its copy of a card only
        # matches the others as a prefix; longest names first, and a name
        # that is the start of one already kept is that device again
        best: Dict[str, OutputDevice] = {}
        for dev in sorted(found, key=lambda d: (-len(d.name), rank(d))):
            if dev.name in VIRTUAL_OUTPUTS:
                continue
            key = next((k for k in best if k.startswith(dev.name)), dev.name)
            keep = best.get(key)
            if keep is None or rank(dev) < rank(keep):
                best[key] = dev
        return sorted(best.values(), key=lambda d: d.index)

    @classmethod
    def resolve(cls, spec) -> Optional[int]:
        if spec is None or spec == '':
            return None
        if isinstance(spec, int):
            return spec
        wanted = clean_device_name(spec).lower()
        devices = cls.list_devices()
        for dev in devices:
            if dev.name.lower() == wanted:
                return dev.index
        for dev in devices:
            if wanted in dev.name.lower():
                return dev.index
        log.warning(f"no audio output device matches {spec!r}; using the system default")
        return None


# ---------------------------------------------------------------------------
# Mixer
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Route:
    """One output channel fed from one voice group at a gain: how a
    transducer hangs off the mix.  Voices are rendered per group (a group
    is one calibration profile), and a route says which channel hears
    which group, how loud."""
    channel: int
    group: str
    gain: float = 1.0


class ShakerSynth:
    """Voices in groups under one lock, mixed per group, fanned out to the
    output channels by routes.

    Voices are looked up by key and made on demand; each belongs to a
    group, which is what the routes address.  ``render_block`` is the
    whole audio path and can be called without an output, which is how
    the tests use it.  A peak limiter per output channel keeps a stack of
    effects from clipping into a buzz: the whole block is pulled down at
    once and let go over a few hundred milliseconds.
    """

    #: seconds for a channel's limiter to let go after a peak
    LIMITER_RELEASE_S = 0.3

    def __init__(self, samplerate: int = 48000, blocksize: int = 512,
                 master_gain: float = 1.0, routes: Sequence[Route] = (Route(0, ''),),
                 channels: Optional[int] = None, output=None):
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.lock = threading.RLock()
        self._voices: Dict[str, Tuple[object, str]] = {}
        self._gain = float(master_gain)
        self._routes: List[Route] = []
        self._channels = 0
        self._limiters = np.ones(1)
        self._mix = np.zeros((self.blocksize, 1), dtype=np.float32)
        self.set_routes(routes, channels)
        self._output = output
        self._stopping = False
        self.underruns = 0
        self.on_finished: Optional[Callable[[], None]] = None

    # --- voices ------------------------------------------------------------

    def voice(self, key: str, cls, group: str = '', **kwargs):
        """The voice under ``key`` in ``group``, made (or remade, when the
        class or group differs) as ``cls(samplerate, blocksize, **kwargs)``."""
        with self.lock:
            entry = self._voices.get(key)
            if entry is None or not isinstance(entry[0], cls) or entry[1] != group:
                entry = (cls(self.samplerate, self.blocksize, **kwargs), group)
                self._voices[key] = entry
            return entry[0]

    def get(self, key: str):
        with self.lock:
            entry = self._voices.get(key)
            return entry[0] if entry else None

    def remove(self, key: str) -> None:
        with self.lock:
            self._voices.pop(key, None)

    def keys(self) -> List[str]:
        with self.lock:
            return list(self._voices)

    def silence(self, ramp_ms: float = 50.0) -> None:
        with self.lock:
            for v, _ in self._voices.values():
                v.stop(ramp_ms)

    def clear(self) -> None:
        with self.lock:
            self._voices.clear()

    # --- mix -------------------------------------------------------------

    def set_master_gain(self, gain: float) -> None:
        with self.lock:
            self._gain = float(gain)

    def set_routes(self, routes: Sequence[Route], channels: Optional[int] = None) -> None:
        """Replace the fan-out.  The channel count is the highest routed
        channel plus one unless a wider count is given (an output opened
        wider than the routes use keeps its silent channels)."""
        routes = [Route(int(r.channel), str(r.group), float(r.gain)) for r in routes]
        needed = max((r.channel for r in routes), default=0) + 1
        count = max(needed, int(channels) if channels else 1)
        with self.lock:
            self._routes = routes
            if count != self._channels:
                self._channels = count
                self._limiters = np.ones(count)
                self._mix = np.zeros((self.blocksize, count), dtype=np.float32)

    @property
    def routes(self) -> List[Route]:
        return list(self._routes)

    @property
    def groups(self) -> List[str]:
        """The voice groups the routes address, in route order."""
        seen: List[str] = []
        for r in self._routes:
            if r.group not in seen:
                seen.append(r.group)
        return seen

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def limiter_gains(self) -> np.ndarray:
        """How far each channel's limiter is holding it down (1.0 = not at all)."""
        return self._limiters.copy()

    def render_groups(self, n: int) -> Dict[str, np.ndarray]:
        """Each group's mono mix for the next ``n`` samples, before the
        master gain.  The caller holds the lock."""
        mixes: Dict[str, np.ndarray] = {}
        for v, group in self._voices.values():
            if v.is_silent:
                continue
            block = v.render(n)
            if group in mixes:
                mixes[group] += block
            else:
                mixes[group] = np.array(block, dtype=np.float32, copy=True)
        return mixes

    def render_block(self, n: Optional[int] = None) -> np.ndarray:
        """The next ``n`` samples for every output channel, shape
        ``(n, channels)``, limited to +-1 per channel."""
        n = self.blocksize if n is None else int(n)
        with self.lock:
            out = self._mix if n == self.blocksize else np.zeros((n, self._channels), dtype=np.float32)
            out.fill(0.0)
            mixes = self.render_groups(n)
            for r in self._routes:
                mix = mixes.get(r.group)
                if mix is None or r.channel >= self._channels:
                    continue
                g = r.gain * self._gain
                if g == 1.0:
                    out[:, r.channel] += mix
                elif g != 0.0:
                    out[:, r.channel] += mix * g
            if n:
                peaks = np.abs(out).max(axis=0)
                over = peaks > 1.0
                self._limiters[over] = np.minimum(self._limiters[over], 1.0 / peaks[over])
                held = self._limiters < 1.0
                if held.any():
                    out[:, held] *= self._limiters[held]
                    release = min(1.0, n / (self.LIMITER_RELEASE_S * self.samplerate))
                    self._limiters[held] += (1.0 - self._limiters[held]) * release
            np.clip(out, -1.0, 1.0, out=out)
        return out

    def _callback(self, outdata, frames, time_info, status) -> None:
        if status:
            self.underruns += 1
        block = self.render_block(frames)
        width = min(outdata.shape[1], block.shape[1])
        outdata[:, :width] = block[:, :width]
        if outdata.shape[1] > width:
            outdata[:, width:] = 0.0

    # --- output ------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._output is not None and self._output.running

    def start(self) -> None:
        if self._output is None:
            raise RuntimeError("the shaker synth has no output to start")
        if self.running:
            return
        self._output.start(self._callback, self.channels, self._finished)

    def stop(self) -> None:
        if self._output is None:
            return
        self._stopping = True
        try:
            self._output.stop()
        finally:
            self._stopping = False

    def _finished(self) -> None:
        """The output ended on its own: the card went away, or PortAudio
        gave up.  Not a stop we asked for."""
        if self._stopping:
            return
        if self.on_finished is not None:
            self.on_finished()
