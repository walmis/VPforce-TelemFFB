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

    def stop(self, ramp_ms: float = 50.0) -> None:
        """Ramp amplitude to 0."""
        ramp_samples = max(1, int(self._samplerate * ramp_ms / 1000.0))
        self._target_amp = 0.0
        self._ramp_step = (self._target_amp - self._current_amp) / ramp_samples
        self._env_active = False

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

    @property
    def is_silent(self) -> bool:
        if self._env_active:
            return False
        return self._current_amp == 0.0 and self._target_amp == 0.0


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
    elif args.selftest_noise:
        _selftest_noise(_parse_device(args.device), args.samplerate,
                        center=args.center, bandwidth=args.bandwidth,
                        channel_mode=args.channel_mode, pan=args.pan)
    else:
        _selftest(_parse_device(args.device), args.samplerate,
                  channel_mode=args.channel_mode, pan=args.pan)


if __name__ == "__main__":
    main()
