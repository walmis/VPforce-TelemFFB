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

"""
Standalone audio-synthesis module for the TelemFFB bass-shaker device type.

Exposes:
    Oscillator    -- one phase-continuous sine with smooth amplitude ramping.
    ShakerSynth   -- mixer + sounddevice OutputStream wrapper, thread-safe.

This module deliberately has no dependency on telemffb.* so that it can be
exercised stand-alone via ``python -m telemffb.hw.shaker_synth ...``.
"""

import argparse
import logging
import math
import threading
import time
from typing import Optional, Union

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

TWO_PI = 2.0 * math.pi


def build_pulse_envelope(samplerate: int,
                         carrier_hz: float,
                         halfwaves: int,
                         amplitude: float,
                         attack_ms: float,
                         release_ms: float,
                         brake_amp: float,
                         brake_delay_ms: float
                         ) -> "tuple[np.ndarray, np.ndarray, int, int, int]":
    """Build the per-sample drive envelope and brake replacement signal.

    Returns:
        drive_env     -- length drive_end. Linear attack 0->amp, sustain at
                         amp, linear release amp->0. Used as a multiplier for
                         a fresh sine that starts at phase 0.
        brake_signal  -- length (brake_end - brake_start). A precomputed
                         phase-inverted half sine at brake_amp, replacing
                         (not multiplying) the running sine for that span so
                         brake polarity is exact regardless of carrier phase.
        drive_end     -- sample index where drive section ends
        brake_start   -- sample index where brake section starts (== drive_end + gap)
        brake_end     -- sample index where brake section ends; total length

    All sample counts are rounded to whole samples. Halfwave-precise
    duration: drive_samples = round(halfwaves / (2*carrier_hz) * sr).

    This helper is also used by ShakerWaveformWidget for the live-preview
    paint, so the audio output and the visualisation are byte-identical.
    """
    sr = float(samplerate)
    carrier = max(0.1, float(carrier_hz))
    halfwaves = max(1, int(halfwaves))
    amplitude = float(max(0.0, min(1.0, amplitude)))
    drive_samples = max(1, int(round(halfwaves / (2.0 * carrier) * sr)))
    attack_samples = min(int(round(attack_ms / 1000.0 * sr)), drive_samples // 2)
    release_samples = min(int(round(release_ms / 1000.0 * sr)), drive_samples // 2)
    attack_samples = max(0, attack_samples)
    release_samples = max(0, release_samples)

    drive_env = np.empty(drive_samples, dtype=np.float64)
    if attack_samples > 0:
        drive_env[:attack_samples] = np.linspace(
            0.0, amplitude, attack_samples, endpoint=False)
    sustain_end = drive_samples - release_samples
    if sustain_end > attack_samples:
        drive_env[attack_samples:sustain_end] = amplitude
    if release_samples > 0:
        drive_env[sustain_end:] = np.linspace(
            amplitude, 0.0, release_samples, endpoint=False)

    if brake_amp > 0.0 and brake_delay_ms >= 0.0:
        gap_samples = max(0, int(round(brake_delay_ms / 1000.0 * sr)))
        brake_samples = max(1, int(round(1.0 / (2.0 * carrier) * sr)))
        brake_amp_clamped = float(max(0.0, min(1.0, brake_amp)))
        # Phase-inverted halfwave: -sin(2*pi*carrier*t) for t in [0, half-period).
        t = np.arange(brake_samples, dtype=np.float64) / sr
        brake_signal = -brake_amp_clamped * np.sin(TWO_PI * carrier * t)
        drive_end = drive_samples
        brake_start = drive_end + gap_samples
        brake_end = brake_start + brake_samples
    else:
        brake_signal = np.zeros(0, dtype=np.float64)
        drive_end = drive_samples
        brake_start = drive_end
        brake_end = drive_end

    return drive_env, brake_signal, drive_end, brake_start, brake_end


class Oscillator:
    """Single phase-continuous sine oscillator with smooth amplitude ramping.

    Setters (set/stop) are safe to call from any thread; render() is invoked by
    ShakerSynth from the audio callback while holding ShakerSynth's lock.
    """

    def __init__(self, samplerate: int, blocksize: int = 512):
        self._samplerate = float(samplerate)
        self._blocksize = int(blocksize)
        self._phase = 0.0
        self._frequency = 0.0
        self._current_amp = 0.0
        self._target_amp = 0.0
        self._ramp_step = 0.0
        self._buf = np.zeros(self._blocksize, dtype=np.float32)
        self._indices = np.arange(self._blocksize, dtype=np.float64)
        self._phase_buf = np.zeros(self._blocksize, dtype=np.float64)
        self._sine_buf = np.zeros(self._blocksize, dtype=np.float64)
        self._amps_buf = np.zeros(self._blocksize, dtype=np.float64)
        self._env_buf = np.zeros(self._blocksize, dtype=np.float64)
        self._env_active = False
        self._env_pos = 0
        self._env_total = 0
        self._env_attack = 0
        self._env_peak = 0.0
        self._env_decay_k = 0.0
        # Pulse-mode state (set by trigger_pulse, read by render).
        self._env_mode: str = "classic"   # "classic" | "pulse"
        self._pulse_drive_env: Optional[np.ndarray] = None
        self._pulse_brake_signal: Optional[np.ndarray] = None
        self._pulse_pos: int = 0
        self._pulse_drive_end: int = 0
        self._pulse_brake_start: int = 0
        self._pulse_brake_end: int = 0

    def _ensure_capacity(self, n: int) -> None:
        if n <= self._buf.size:
            return
        self._buf = np.zeros(n, dtype=np.float32)
        self._indices = np.arange(n, dtype=np.float64)
        self._phase_buf = np.zeros(n, dtype=np.float64)
        self._sine_buf = np.zeros(n, dtype=np.float64)
        self._amps_buf = np.zeros(n, dtype=np.float64)
        self._env_buf = np.zeros(n, dtype=np.float64)

    def set(self, freq: float, amplitude: float, ramp_ms: float = 50.0) -> None:
        """Update target freq/amplitude. Amplitude ramps linearly over ramp_ms."""
        amplitude = float(max(0.0, min(1.0, amplitude)))
        ramp_samples = max(1, int(self._samplerate * ramp_ms / 1000.0))
        self._frequency = float(freq)
        self._target_amp = amplitude
        self._ramp_step = (self._target_amp - self._current_amp) / ramp_samples
        self._env_active = False
        self._env_mode = "classic"

    def stop(self, ramp_ms: float = 50.0) -> None:
        """Ramp amplitude to 0."""
        ramp_samples = max(1, int(self._samplerate * ramp_ms / 1000.0))
        self._target_amp = 0.0
        self._ramp_step = (self._target_amp - self._current_amp) / ramp_samples
        self._env_active = False
        self._env_mode = "classic"

    def trigger(self, freq: float, amplitude: float,
                attack_ms: float = 4.0, decay_ms: float = 90.0) -> None:
        """Fire a one-shot transient: linear attack, exponential decay.

        Replaces any in-progress ramp or prior envelope (re-trigger from
        sample 0). The envelope ends itself; no stop() needed.
        """
        amplitude = float(max(0.0, min(1.0, amplitude)))
        attack = max(1, int(self._samplerate * attack_ms / 1000.0))
        decay = max(1, int(self._samplerate * decay_ms / 1000.0))
        self._frequency = float(freq)
        self._env_attack = attack
        self._env_total = attack + decay
        self._env_peak = amplitude
        self._env_decay_k = math.log(256.0) / decay
        self._env_pos = 0
        self._env_active = True
        self._env_mode = "classic"
        self._current_amp = 0.0
        self._target_amp = 0.0
        self._ramp_step = 0.0

    def trigger_pulse(self, carrier_hz: float, halfwaves: int, amplitude: float,
                      attack_ms: float = 1.5, release_ms: float = 2.0,
                      brake_amp: float = 0.0, brake_delay_ms: float = 0.0) -> None:
        """Fire one gated halfwave-count pulse with optional active brake.

        Phase resets to 0 so the carrier always starts at a zero crossing
        (required for halfwave-precise duration). Replaces any in-progress
        envelope. Brake (if brake_amp > 0) is exactly one phase-inverted
        halfwave at carrier_hz, started after brake_delay_ms.
        """
        drive_env, brake_signal, drive_end, brake_start, brake_end = (
            build_pulse_envelope(int(self._samplerate), carrier_hz, halfwaves,
                                 amplitude, attack_ms, release_ms,
                                 brake_amp, brake_delay_ms))
        self._frequency = float(max(0.1, carrier_hz))
        self._phase = 0.0
        self._pulse_drive_env = drive_env
        self._pulse_brake_signal = brake_signal
        self._pulse_drive_end = drive_end
        self._pulse_brake_start = brake_start
        self._pulse_brake_end = brake_end
        self._pulse_pos = 0
        self._env_mode = "pulse"
        self._env_active = True
        self._current_amp = 0.0
        self._target_amp = 0.0
        self._ramp_step = 0.0

    def render(self, num_samples: int) -> np.ndarray:
        """Render num_samples float32 samples in [-1, 1]. Phase-continuous."""
        self._ensure_capacity(num_samples)
        out = self._buf[:num_samples]

        if not self._env_active and self._current_amp == 0.0 and self._target_amp == 0.0:
            out[:] = 0.0
            return out

        # Pulse mode renders sample-accurate sections with explicit phase
        # control; it does not share the running-phase pipeline below.
        if self._env_active and self._env_mode == "pulse":
            self._render_pulse(out)
            return out

        d_phi = TWO_PI * self._frequency / self._samplerate
        phase_buf = self._phase_buf[:num_samples]
        np.multiply(self._indices[:num_samples], d_phi, out=phase_buf)
        phase_buf += self._phase
        sine_buf = self._sine_buf[:num_samples]
        np.sin(phase_buf, out=sine_buf)

        self._phase = math.fmod(self._phase + d_phi * num_samples, TWO_PI)

        if self._env_active:
            env = self._env_buf[:num_samples]
            self._fill_envelope(env)
            np.multiply(sine_buf, env, out=sine_buf)
        elif self._current_amp == self._target_amp:
            np.multiply(sine_buf, self._current_amp, out=sine_buf)
        else:
            step = self._ramp_step
            if step == 0.0:
                samples_to_target = 0
            else:
                samples_to_target = max(0, int(math.ceil(
                    (self._target_amp - self._current_amp) / step)))

            amps = self._amps_buf[:num_samples]
            if samples_to_target >= num_samples:
                np.multiply(self._indices[:num_samples], step, out=amps)
                amps += self._current_amp
                self._current_amp += step * num_samples
            else:
                if samples_to_target > 0:
                    np.multiply(self._indices[:samples_to_target], step,
                                out=amps[:samples_to_target])
                    amps[:samples_to_target] += self._current_amp
                amps[samples_to_target:] = self._target_amp
                self._current_amp = self._target_amp
            np.multiply(sine_buf, amps, out=sine_buf)

        np.copyto(out, sine_buf, casting='same_kind')
        return out

    def _fill_envelope(self, env: np.ndarray) -> None:
        n = env.size
        start = self._env_pos
        end = start + n
        attack = self._env_attack
        total = self._env_total
        peak = self._env_peak
        k = self._env_decay_k

        t = self._indices[:n] + start

        # Attack: linear 0 -> peak over [0, attack)
        attack_mask = t < attack
        if attack_mask.any():
            np.multiply(t, peak / attack, out=env, where=attack_mask)

        # Decay: peak * exp(-k * (t - attack)) over [attack, total)
        decay_mask = (t >= attack) & (t < total)
        if decay_mask.any():
            decay_t = t - attack
            decay_vals = np.exp(-k * decay_t)
            decay_vals *= peak
            np.copyto(env, decay_vals, where=decay_mask)

        # Past total: silence; envelope ends now.
        past_mask = t >= total
        if past_mask.any():
            env[past_mask] = 0.0

        self._env_pos = end
        if end >= total:
            self._env_active = False
            self._env_pos = 0

    def _render_pulse(self, out: np.ndarray) -> None:
        """Fill ``out`` with the next slice of the active pulse envelope.

        Drive section: fresh sine starting at phase 0 multiplied by the
        precomputed drive envelope. Gap section: zeros. Brake section: the
        precomputed phase-inverted half sine is copied directly into out
        (overrides the running sine — guarantees exact polarity).
        """
        n = out.size
        out[:] = 0.0
        start = self._pulse_pos
        end = start + n
        sr = self._samplerate
        d_e = self._pulse_drive_end
        b_s = self._pulse_brake_start
        b_e = self._pulse_brake_end

        # Drive section.
        if start < d_e:
            seg_lo = start
            seg_hi = min(d_e, end)
            local_lo = seg_lo - start
            local_hi = seg_hi - start
            t = np.arange(seg_lo, seg_hi, dtype=np.float64) / sr
            sine = np.sin(TWO_PI * self._frequency * t)
            env = self._pulse_drive_env[seg_lo:seg_hi]
            out[local_lo:local_hi] = (sine * env).astype(np.float32)

        # Gap section [d_e .. b_s) — already zero from out[:] = 0.0.

        # Brake section.
        if self._pulse_brake_signal is not None and self._pulse_brake_signal.size > 0:
            if start < b_e and end > b_s:
                seg_lo = max(start, b_s)
                seg_hi = min(b_e, end)
                local_lo = seg_lo - start
                local_hi = seg_hi - start
                src_lo = seg_lo - b_s
                src_hi = seg_hi - b_s
                out[local_lo:local_hi] = (
                    self._pulse_brake_signal[src_lo:src_hi].astype(np.float32))

        self._pulse_pos = end
        if end >= b_e:
            self._env_active = False
            self._env_mode = "classic"
            self._pulse_pos = 0

    @property
    def is_silent(self) -> bool:
        if self._env_active:
            return False
        return self._current_amp == 0.0 and self._target_amp == 0.0


class PhaseAccumulator:
    """Integrate angular velocity over caller-supplied dt; report integer
    crossings of the per-revolution division boundary (blade pass, cylinder
    firing, …).

    State is the fractional phase in cycles (0.0–1.0). Calling .advance()
    adds ``rpm/60 * divisions_per_rev * dt`` cycles and returns the integer
    number of crossings that occurred during this dt. Designed for two call
    sites:

      - audio callback at ~190 Hz: dt = blocksize / samplerate
      - telemetry tick at 30–60 Hz: dt = wall-clock since last call

    Float64 throughout: at 0.4 Hz impulse rate the per-block increment is
    ~2e-3 cycles, well above ULP for hours of accumulated phase.
    """

    def __init__(self) -> None:
        self._phase = 0.0

    def advance(self, rpm: float, divisions_per_rev: float, dt: float) -> int:
        if rpm <= 0.0 or divisions_per_rev <= 0.0 or dt <= 0.0:
            return 0
        increment = (rpm / 60.0) * divisions_per_rev * dt
        new_phase = self._phase + increment
        crossings = int(new_phase) - int(self._phase)
        # Keep only the fractional part so accumulated phase stays bounded.
        self._phase = new_phase - int(new_phase)
        return crossings if crossings > 0 else 0

    def reset(self) -> None:
        self._phase = 0.0

    @property
    def phase(self) -> float:
        return self._phase


class ImpulseTrainOscillator:
    """Phase-locked impulse-train generator: one trigger_pulse per blade-pass /
    cylinder-firing crossing, driven by RPM rather than a fixed frequency.

    Wraps an inner Oscillator; satisfies the (is_silent, render(n)) contract
    consumed by ShakerSynth._callback. RPM and load can be updated from any
    thread (set_rpm); the render loop integrates phase block-by-block and
    fires trigger_pulse when the integer division-boundary count advances.

    Onset of each pulse is quantized to the audio block (~5 ms at 256/48k),
    which is well below one period for any usable impulse rate.

    Auto-fallback to bandpass noise: above ``max_impulse_rate_hz`` (e.g. a
    radial engine at high RPM, or a multi-blade rotor near redline) the
    per-pulse oscillation can no longer be resolved as discrete events.
    The oscillator transparently switches to a band-limited noise voice
    centered on the current impulse rate so the tactile feedback continues
    as a broadband thrum instead of going silent. Hysteresis on the
    threshold prevents flapping near the boundary; an ~80 ms ramp on each
    voice produces a click-free crossover.
    """

    # Hysteresis: cross into noise mode at 100% of cap, return to impulse
    # mode only once the rate falls back to 85% of cap. Range tuned so a
    # steady cruise RPM near redline doesn't toggle voices each block.
    _FALLBACK_HIGH_FRAC = 1.00
    _FALLBACK_LOW_FRAC = 0.85
    # Default noise bandwidth = 20% of center, with a floor for low rates
    # so the filter doesn't become a near-pure tone. Override via configure.
    _NOISE_BW_FRAC = 0.20
    _NOISE_BW_MIN_HZ = 15.0
    # Crossfade between voices on mode change (and on mode entry/exit).
    _CROSSFADE_MS = 80.0

    def __init__(self, samplerate: int, blocksize: int = 512,
                 max_impulse_rate_hz: float = 180.0):
        self._osc = Oscillator(samplerate, blocksize)
        self._noise = BandpassNoiseGenerator(samplerate, blocksize)
        self._samplerate = float(samplerate)
        self._phase = 0.0
        self._lock = threading.Lock()
        # Telemetry-driven inputs
        self._rpm = 0.0
        self._divisions = 1.0
        self._load = 0.0
        # Pulse-shape config (configure() to tune)
        self._carrier_hz = 50.0
        self._halfwaves = 1
        self._attack_ms = 1.5
        self._release_ms = 3.0
        self._brake_amp = 0.0
        self._brake_delay_ms = 0.0
        self._gain = 1.0
        self._max_impulse_rate_hz = float(max_impulse_rate_hz)
        # None → auto-compute bandwidth from current center per render call.
        self._noise_bandwidth_hz: Optional[float] = None
        # Hysteresis state for the impulse↔noise voice swap.
        self._fallback_active = False

    def configure(self, *, carrier_hz=None, halfwaves=None, attack_ms=None,
                  release_ms=None, brake_amp=None, brake_delay_ms=None,
                  gain=None, max_impulse_rate_hz=None,
                  noise_bandwidth_hz=None) -> None:
        with self._lock:
            if carrier_hz is not None: self._carrier_hz = float(carrier_hz)
            if halfwaves is not None: self._halfwaves = max(1, int(halfwaves))
            if attack_ms is not None: self._attack_ms = float(attack_ms)
            if release_ms is not None: self._release_ms = float(release_ms)
            if brake_amp is not None: self._brake_amp = float(brake_amp)
            if brake_delay_ms is not None: self._brake_delay_ms = float(brake_delay_ms)
            if gain is not None: self._gain = float(gain)
            if max_impulse_rate_hz is not None:
                self._max_impulse_rate_hz = float(max_impulse_rate_hz)
            if noise_bandwidth_hz is not None:
                v = float(noise_bandwidth_hz)
                self._noise_bandwidth_hz = v if v > 0.0 else None

    def set_rpm(self, rpm: float, divisions_per_rev: Optional[float] = None,
                load: Optional[float] = None) -> None:
        with self._lock:
            self._rpm = max(0.0, float(rpm))
            if divisions_per_rev is not None:
                self._divisions = max(1.0, float(divisions_per_rev))
            if load is not None:
                self._load = max(0.0, min(1.0, float(load)))

    def stop(self, ramp_ms: float = 50.0) -> None:
        with self._lock:
            self._rpm = 0.0
            self._fallback_active = False
        self._osc.stop(ramp_ms=ramp_ms)
        self._noise.stop(ramp_ms=ramp_ms)

    @property
    def is_silent(self) -> bool:
        if self._rpm > 0.0:
            return False
        return self._osc.is_silent and self._noise.is_silent

    @property
    def fallback_active(self) -> bool:
        """True while the noise voice is rendering instead of impulses.
        Exposed for tests and debugging."""
        return self._fallback_active

    def render(self, num_samples: int) -> np.ndarray:
        with self._lock:
            rpm = self._rpm
            divisions = self._divisions
            load = self._load
            carrier = self._carrier_hz
            halfwaves = self._halfwaves
            attack_ms = self._attack_ms
            release_ms = self._release_ms
            brake_amp = self._brake_amp
            brake_delay_ms = self._brake_delay_ms
            gain = self._gain
            cap = self._max_impulse_rate_hz
            bw_override = self._noise_bandwidth_hz
            fallback_active = self._fallback_active

        if rpm > 0.0 and divisions > 0.0:
            impulse_hz = rpm / 60.0 * divisions
            high_thr = cap * self._FALLBACK_HIGH_FRAC
            low_thr = cap * self._FALLBACK_LOW_FRAC
            if not fallback_active and impulse_hz >= high_thr:
                fallback_active = True
            elif fallback_active and impulse_hz < low_thr:
                fallback_active = False

            if fallback_active:
                amp = max(0.0, min(1.0, load * gain))
                bw = (bw_override if bw_override is not None
                      else max(self._NOISE_BW_MIN_HZ,
                               impulse_hz * self._NOISE_BW_FRAC))
                self._noise.set(center_hz=impulse_hz, bandwidth_hz=bw,
                                amplitude=amp, ramp_ms=self._CROSSFADE_MS)
                # Existing transients in _osc decay on their own envelope;
                # don't fire new pulses while in noise mode.
            else:
                if not self._noise.is_silent:
                    self._noise.stop(ramp_ms=self._CROSSFADE_MS)
                if impulse_hz <= cap:
                    dt = num_samples / self._samplerate
                    increment = impulse_hz * dt
                    new_phase = self._phase + increment
                    crossings = int(new_phase) - int(self._phase)
                    self._phase = new_phase - int(new_phase)
                    if crossings > 0:
                        amp = max(0.0, min(1.0, load * gain))
                        if amp > 0.0:
                            self._osc.trigger_pulse(
                                carrier_hz=carrier, halfwaves=halfwaves,
                                amplitude=amp, attack_ms=attack_ms,
                                release_ms=release_ms, brake_amp=brake_amp,
                                brake_delay_ms=brake_delay_ms)
        else:
            # rpm = 0 → wind down the noise voice as well.
            if not self._noise.is_silent:
                self._noise.stop(ramp_ms=self._CROSSFADE_MS)
            fallback_active = False

        with self._lock:
            self._fallback_active = fallback_active

        return self._osc.render(num_samples) + self._noise.render(num_samples)


class BandpassNoiseGenerator:
    """Band-limited noise generator. Same interface as Oscillator.

    Generates white noise filtered through a single RBJ-style biquad bandpass.
    Center frequency and bandwidth are settable; like Oscillator, amplitude is
    ramped on changes to avoid clicks.
    """

    def __init__(self, samplerate: int, blocksize: int | None = None):
        # blocksize accepted for symmetry with Oscillator; unused (render allocates per call)
        self.samplerate = samplerate
        self.center_hz = 35.0
        self.bandwidth_hz = 20.0
        self.target_amp = 0.0
        self.current_amp = 0.0
        self.ramp_per_sample = 0.0
        self._rng = np.random.default_rng()
        self._coeffs_dirty = True
        self._b0 = self._b1 = self._b2 = 0.0
        self._a1 = self._a2 = 0.0
        self._z1 = self._z2 = 0.0  # biquad delay line
        self._compute_coeffs()

    def set(self, center_hz: float, bandwidth_hz: float, amplitude: float, ramp_ms: float = 50.0):
        if center_hz != self.center_hz or bandwidth_hz != self.bandwidth_hz:
            self.center_hz = max(1.0, center_hz)
            self.bandwidth_hz = max(1.0, bandwidth_hz)
            self._coeffs_dirty = True
        self.target_amp = float(np.clip(amplitude, 0.0, 1.5))
        ramp_samples = max(1, int(self.samplerate * ramp_ms / 1000.0))
        self.ramp_per_sample = (self.target_amp - self.current_amp) / ramp_samples

    def stop(self, ramp_ms: float = 50.0):
        self.target_amp = 0.0
        ramp_samples = max(1, int(self.samplerate * ramp_ms / 1000.0))
        self.ramp_per_sample = -self.current_amp / ramp_samples

    @property
    def is_silent(self) -> bool:
        return self.current_amp <= 1e-6 and self.target_amp <= 1e-6

    def render(self, num_samples: int) -> np.ndarray:
        if self._coeffs_dirty:
            self._compute_coeffs()
        x = self._rng.standard_normal(num_samples).astype(np.float64)
        y = np.empty(num_samples, dtype=np.float64)
        z1, z2 = self._z1, self._z2
        b0, b1, b2, a1, a2 = self._b0, self._b1, self._b2, self._a1, self._a2
        for n in range(num_samples):
            xn = x[n]
            yn = b0 * xn + z1
            z1 = b1 * xn - a1 * yn + z2
            z2 = b2 * xn - a2 * yn
            y[n] = yn
        self._z1, self._z2 = z1, z2

        env = np.empty(num_samples, dtype=np.float64)
        amp = self.current_amp
        for n in range(num_samples):
            env[n] = amp
            amp += self.ramp_per_sample
            if (self.ramp_per_sample > 0 and amp >= self.target_amp) or \
               (self.ramp_per_sample < 0 and amp <= self.target_amp):
                amp = self.target_amp
                self.ramp_per_sample = 0.0
        self.current_amp = amp

        out = (y * env).astype(np.float32)
        return np.clip(out, -1.0, 1.0)

    def _compute_coeffs(self):
        """RBJ constant-skirt-gain bandpass biquad.
        https://www.w3.org/TR/audio-eq-cookbook/
        """
        omega = 2.0 * np.pi * self.center_hz / self.samplerate
        Q = max(0.5, self.center_hz / max(1.0, self.bandwidth_hz))
        alpha = np.sin(omega) / (2.0 * Q)
        cos_omega = np.cos(omega)
        b0 = alpha
        b1 = 0.0
        b2 = -alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_omega
        a2 = 1.0 - alpha
        self._b0 = b0 / a0
        self._b1 = b1 / a0
        self._b2 = b2 / a0
        self._a1 = a1 / a0
        self._a2 = a2 / a0
        self._coeffs_dirty = False


class ShakerSynth:
    """Mixer + sounddevice OutputStream wrapper. Thread-safe."""

    _CHANNEL_MODES = ("mono", "left", "right", "pan")

    def __init__(self, samplerate: int = 48000,
                 device: Optional[Union[int, str]] = None,
                 blocksize: int = 256, master_gain: float = 1.0,
                 channel_mode: str = "mono", pan: float = 0.0):
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.device = device
        self._master_gain = float(master_gain)
        self._oscillators: dict[str, Oscillator] = {}
        self._lock = threading.Lock()
        self._mix_buf = np.zeros(self.blocksize, dtype=np.float32)
        self._stream: Optional[sd.OutputStream] = None
        self._channels = 1
        mode = channel_mode if channel_mode in self._CHANNEL_MODES else "mono"
        self._channel_mode = mode
        self._pan = float(max(-1.0, min(1.0, pan)))

    def set_channel_mode(self, mode: str) -> None:
        if mode not in self._CHANNEL_MODES:
            return
        with self._lock:
            self._channel_mode = mode

    def set_pan(self, value: float) -> None:
        with self._lock:
            self._pan = float(max(-1.0, min(1.0, value)))

    def start(self) -> None:
        if self._stream is not None:
            logger.debug("ShakerSynth already started")
            return

        device_idx = self._resolve_device(self.device) if self.device is not None else None

        want_stereo = self._channel_mode != "mono"
        channels = 2 if want_stereo else 1
        try:
            sd.check_output_settings(device=device_idx, samplerate=self.samplerate,
                                     channels=channels, dtype='float32')
        except sd.PortAudioError:
            if want_stereo:
                logger.warning(
                    "Output device does not support stereo; falling back to mono. "
                    "Channel mode and pan will have no effect.")
                channels = 1
                self._channel_mode = "mono"
                try:
                    sd.check_output_settings(device=device_idx, samplerate=self.samplerate,
                                             channels=1, dtype='float32')
                except sd.PortAudioError:
                    channels = 2
            else:
                channels = 2
        self._channels = channels
        logger.info("Opening sounddevice OutputStream: device=%s sr=%d block=%d ch=%d mode=%s pan=%.2f",
                    device_idx, self.samplerate, self.blocksize, channels,
                    self._channel_mode, self._pan)

        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            device=device_idx,
            channels=channels,
            dtype='float32',
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            logger.exception("Error closing sounddevice stream")
        self._stream = None

    def get_oscillator(self, name: str) -> Oscillator:
        with self._lock:
            osc = self._oscillators.get(name)
            if osc is None:
                osc = Oscillator(self.samplerate, self.blocksize)
                self._oscillators[name] = osc
            return osc

    def get_noise_oscillator(self, name: str) -> "BandpassNoiseGenerator":
        """Return the named noise oscillator, creating it if needed. Thread-safe.

        If a different oscillator type is registered under this name, it is
        replaced with a fresh BandpassNoiseGenerator (the caller is asking for
        a noise oscillator specifically).
        """
        with self._lock:
            osc = self._oscillators.get(name)
            if not isinstance(osc, BandpassNoiseGenerator):
                osc = BandpassNoiseGenerator(self.samplerate, self.blocksize)
                self._oscillators[name] = osc
            return osc

    def remove_oscillator(self, name: str) -> None:
        with self._lock:
            self._oscillators.pop(name, None)

    def add_oscillator(self, name: str, oscillator) -> None:
        """Insert a pre-built oscillator under the given name. Replaces any
        existing oscillator with the same name. Thread-safe.

        Used by callers that want to inject custom oscillator subclasses
        (e.g. BandpassNoiseGenerator, or a one-shot test oscillator from the
        layer editor) without going through get_oscillator's auto-vivify path.
        """
        with self._lock:
            self._oscillators[name] = oscillator

    def peek_oscillator(self, name: str):
        """Return the oscillator under ``name`` or ``None`` if not present.
        Read-only; does NOT auto-create. Thread-safe."""
        with self._lock:
            return self._oscillators.get(name)

    def list_oscillator_names(self) -> list:
        """Snapshot of currently-registered oscillator names. Thread-safe.
        Used by callers that need to enumerate (e.g. diagnostics, selftests)."""
        with self._lock:
            return list(self._oscillators.keys())

    def set_master_gain(self, gain: float) -> None:
        with self._lock:
            self._master_gain = float(gain)

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.debug("PortAudio status: %s", status)
        with self._lock:
            if frames == self.blocksize:
                mix = self._mix_buf
            else:
                mix = np.zeros(frames, dtype=np.float32)
            mix.fill(0.0)
            for osc in self._oscillators.values():
                if osc.is_silent:
                    continue
                mix += osc.render(frames)
            if self._master_gain != 1.0:
                mix *= self._master_gain
            np.clip(mix, -1.0, 1.0, out=mix)

            if self._channels == 1:
                outdata[:, 0] = mix
            elif self._channel_mode == "left":
                outdata[:, 0] = mix
                outdata[:, 1] = 0.0
            elif self._channel_mode == "right":
                outdata[:, 0] = 0.0
                outdata[:, 1] = mix
            elif self._channel_mode == "pan":
                angle = (self._pan + 1.0) * 0.25 * math.pi
                gl = math.cos(angle)
                gr = math.sin(angle)
                outdata[:, 0] = mix * gl
                outdata[:, 1] = mix * gr
            else:
                outdata[:, :] = mix[:, np.newaxis]

    @staticmethod
    def _resolve_device(spec: Union[int, str]) -> Optional[int]:
        if isinstance(spec, int):
            return spec
        if isinstance(spec, str):
            try:
                devices = sd.query_devices()
            except Exception:
                logger.exception("sounddevice.query_devices failed")
                return None
            spec_lc = spec.lower()
            for i, d in enumerate(devices):
                if d.get('max_output_channels', 0) > 0 and d.get('name', '').lower() == spec_lc:
                    return i
            for i, d in enumerate(devices):
                if d.get('max_output_channels', 0) > 0 and spec_lc in d.get('name', '').lower():
                    return i
            logger.warning("No output device matching %r found, using system default", spec)
            return None
        return None

    @staticmethod
    def list_output_devices() -> list:
        try:
            devices = sd.query_devices()
        except Exception:
            logger.exception("sounddevice.query_devices failed")
            return []
        result = []
        for i, d in enumerate(devices):
            if d.get('max_output_channels', 0) > 0:
                result.append({
                    'index': i,
                    'name': d.get('name', ''),
                    'samplerate': float(d.get('default_samplerate', 0.0)),
                    'channels': int(d.get('max_output_channels', 0)),
                })
        return result


def _list_devices() -> None:
    devs = ShakerSynth.list_output_devices()
    if not devs:
        print("No output devices found.")
        return
    print(f"{'idx':>4}  {'channels':>8}  {'samplerate':>11}  name")
    for d in devs:
        print(f"{d['index']:>4}  {d['channels']:>8}  {d['samplerate']:>11.0f}  {d['name']}")


def _selftest(device, samplerate: int, channel_mode: str = "mono",
              pan: float = 0.0) -> None:
    print(f"ShakerSynth selftest: device={device!r} samplerate={samplerate} "
          f"channel_mode={channel_mode!r} pan={pan}")
    synth = ShakerSynth(samplerate=samplerate, device=device,
                        channel_mode=channel_mode, pan=pan)
    synth.start()
    try:
        a = synth.get_oscillator("a")
        b = synth.get_oscillator("b")
        c = synth.get_oscillator("c")

        print("0.0 - 2.0 s : 30 Hz @ 0.4")
        a.set(30, 0.4, ramp_ms=100)
        time.sleep(2.0)

        print("2.0 - 4.0 s : 30 Hz fades out, 80 Hz fades in @ 0.3 (crossfade)")
        a.stop(ramp_ms=1500)
        b.set(80, 0.3, ramp_ms=1500)
        time.sleep(2.0)

        print("4.0 - 6.0 s : 80 Hz fades out, 15 Hz fades in @ 0.2")
        b.stop(ramp_ms=1500)
        c.set(15, 0.2, ramp_ms=1500)
        time.sleep(2.0)

        print("6.0 s : stop")
        c.stop(ramp_ms=200)
        time.sleep(0.3)
    finally:
        synth.stop()


def _selftest_transient(device, samplerate: int) -> None:
    print(f"ShakerSynth transient selftest: device={device!r} samplerate={samplerate}")
    synth = ShakerSynth(samplerate=samplerate, device=device,
                        channel_mode="pan", pan=0.0)
    synth.start()
    try:
        osc = synth.get_oscillator("thomp")

        print("Four thomps @ 50 Hz, attack 3 ms, decay 120 ms (centered)")
        for _ in range(4):
            osc.trigger(50, 0.9, attack_ms=3.0, decay_ms=120.0)
            time.sleep(0.5)

        print("Same thomp panned LEFT")
        synth.set_pan(-1.0)
        osc.trigger(50, 0.9, attack_ms=3.0, decay_ms=120.0)
        time.sleep(0.6)

        print("Same thomp CENTER")
        synth.set_pan(0.0)
        osc.trigger(50, 0.9, attack_ms=3.0, decay_ms=120.0)
        time.sleep(0.6)

        print("Same thomp panned RIGHT")
        synth.set_pan(1.0)
        osc.trigger(50, 0.9, attack_ms=3.0, decay_ms=120.0)
        time.sleep(0.6)
    finally:
        synth.stop()


def _selftest_pulse(device, samplerate: int) -> None:
    print(f"ShakerSynth pulse selftest: device={device!r} samplerate={samplerate}")
    synth = ShakerSynth(samplerate=samplerate, device=device)
    synth.start()
    try:
        osc = synth.get_oscillator("pulse")
        print("Pulse #1: 50 Hz, 2 halfwaves, no brake")
        osc.trigger_pulse(50, 2, 0.9, attack_ms=1.0, release_ms=2.0,
                          brake_amp=0.0, brake_delay_ms=0.0)
        time.sleep(0.6)

        print("Pulse #2: 50 Hz, 2 halfwaves, brake 0.5 @ 0.5 ms")
        osc.trigger_pulse(50, 2, 0.9, attack_ms=1.0, release_ms=2.0,
                          brake_amp=0.5, brake_delay_ms=0.5)
        time.sleep(0.6)

        print("Pulse #3: 25 Hz, 1 halfwave, brake 0.7 @ 0.6 ms (Buttkicker-like)")
        osc.trigger_pulse(25, 1, 0.9, attack_ms=1.0, release_ms=1.0,
                          brake_amp=0.7, brake_delay_ms=0.6)
        time.sleep(0.8)
    finally:
        synth.stop()


def _selftest_phase_locked(device, samplerate: int) -> None:
    """Drive an ImpulseTrainOscillator with a synthetic helicopter spool-up:
    rotor_rpm 0 → 250 over 4 s, then steady at 250, 5 blades. Expect chuffs
    that ramp into a smooth blade-pass thrum."""
    print(f"ShakerSynth phase-locked selftest: device={device!r} samplerate={samplerate}")
    synth = ShakerSynth(samplerate=samplerate, device=device)
    synth.start()
    try:
        osc = ImpulseTrainOscillator(synth.samplerate, synth.blocksize)
        osc.configure(carrier_hz=38.0, halfwaves=1, attack_ms=1.5,
                      release_ms=6.0, brake_amp=0.6, gain=1.0)
        synth.add_oscillator("rotor_phys", osc)

        spool_seconds = 4.0
        nominal_rpm = 250.0
        blade_count = 5
        steps = 80
        for i in range(steps):
            frac = i / float(steps - 1)
            rpm = nominal_rpm * frac
            # Load tracks RPM/nominal so spool-up sounds light, on-condition rotor full.
            osc.set_rpm(rpm, blade_count, load=min(1.0, frac + 0.05))
            time.sleep(spool_seconds / steps)

        print(f"Hold at {nominal_rpm} RPM × {blade_count} = "
              f"{nominal_rpm/60.0*blade_count:.1f} Hz for 2 s")
        osc.set_rpm(nominal_rpm, blade_count, load=1.0)
        time.sleep(2.0)

        print("Spool down to 0 RPM over 1.5 s")
        for i in range(40):
            frac = 1.0 - i / 39.0
            osc.set_rpm(nominal_rpm * frac, blade_count, load=max(0.05, frac))
            time.sleep(1.5 / 40.0)
        osc.stop()
        time.sleep(0.5)
    finally:
        synth.stop()


def _selftest_noise(device, samplerate: int, center: float = 35.0,
                    bandwidth: float = 20.0, channel_mode: str = "mono",
                    pan: float = 0.0) -> None:
    print(f"ShakerSynth noise selftest: device={device!r} samplerate={samplerate} "
          f"center={center} Hz bandwidth={bandwidth} Hz "
          f"channel_mode={channel_mode!r} pan={pan}")
    synth = ShakerSynth(samplerate=samplerate, device=device,
                        channel_mode=channel_mode, pan=pan)
    synth.start()
    try:
        noise = BandpassNoiseGenerator(synth.samplerate)
        noise.set(center, bandwidth, amplitude=0.5, ramp_ms=200)
        synth.add_oscillator("noise", noise)
        print(f"Playing {center} Hz bandpass noise for 3 seconds ...")
        time.sleep(3.0)
        noise.stop(ramp_ms=200)
        time.sleep(0.5)
        print("Done.")
    finally:
        synth.stop()


def _parse_device(spec: Optional[str]):
    if spec is None:
        return None
    try:
        return int(spec)
    except ValueError:
        return spec


def main() -> None:
    p = argparse.ArgumentParser(
        description="ShakerSynth standalone selftest / device list")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-devices", action="store_true",
                   help="List output-capable audio devices and exit")
    g.add_argument("--selftest", action="store_true",
                   help="Run a 6-second sine-tone selftest")
    g.add_argument("--selftest-transient", action="store_true",
                   help="Run a transient/thomp selftest with L/center/R pan")
    g.add_argument("--selftest-pulse", action="store_true",
                   help="Run a gated halfwave pulse selftest with active brake")
    g.add_argument("--selftest-phase-locked", action="store_true",
                   help="Run a phase-locked rotor spool-up selftest "
                        "(blade-pass impulse train)")
    g.add_argument("--selftest-noise", action="store_true",
                   help="Run a 3-second bandpass-noise selftest")
    p.add_argument("--device", type=str, default=None,
                   help="Output device (integer index or name substring)")
    p.add_argument("--samplerate", type=int, default=48000,
                   help="Sample rate in Hz (default 48000)")
    p.add_argument("--channel-mode", type=str, default="mono",
                   choices=["mono", "left", "right", "pan"],
                   help="Output channel routing (default mono)")
    p.add_argument("--pan", type=float, default=0.0,
                   help="Pan value in [-1, +1] when --channel-mode=pan (default 0)")
    p.add_argument("--center", type=float, default=35.0,
                   help="Bandpass center frequency in Hz for --selftest-noise (default 35)")
    p.add_argument("--bandwidth", type=float, default=20.0,
                   help="Bandpass bandwidth in Hz for --selftest-noise (default 20)")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.list_devices:
        _list_devices()
    elif args.selftest_transient:
        _selftest_transient(_parse_device(args.device), args.samplerate)
    elif args.selftest_pulse:
        _selftest_pulse(_parse_device(args.device), args.samplerate)
    elif args.selftest_phase_locked:
        _selftest_phase_locked(_parse_device(args.device), args.samplerate)
    elif args.selftest_noise:
        _selftest_noise(_parse_device(args.device), args.samplerate,
                        center=args.center, bandwidth=args.bandwidth,
                        channel_mode=args.channel_mode, pan=args.pan)
    else:
        _selftest(_parse_device(args.device), args.samplerate,
                  channel_mode=args.channel_mode, pan=args.pan)


if __name__ == "__main__":
    main()
