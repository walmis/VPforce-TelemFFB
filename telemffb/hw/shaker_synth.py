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

    def _ensure_capacity(self, n: int) -> None:
        if n <= self._buf.size:
            return
        self._buf = np.zeros(n, dtype=np.float32)
        self._indices = np.arange(n, dtype=np.float64)
        self._phase_buf = np.zeros(n, dtype=np.float64)
        self._sine_buf = np.zeros(n, dtype=np.float64)
        self._amps_buf = np.zeros(n, dtype=np.float64)

    def set(self, freq: float, amplitude: float, ramp_ms: float = 50.0) -> None:
        """Update target freq/amplitude. Amplitude ramps linearly over ramp_ms."""
        amplitude = float(max(0.0, min(1.0, amplitude)))
        ramp_samples = max(1, int(self._samplerate * ramp_ms / 1000.0))
        self._frequency = float(freq)
        self._target_amp = amplitude
        self._ramp_step = (self._target_amp - self._current_amp) / ramp_samples

    def stop(self, ramp_ms: float = 50.0) -> None:
        """Ramp amplitude to 0."""
        ramp_samples = max(1, int(self._samplerate * ramp_ms / 1000.0))
        self._target_amp = 0.0
        self._ramp_step = (self._target_amp - self._current_amp) / ramp_samples

    def render(self, num_samples: int) -> np.ndarray:
        """Render num_samples float32 samples in [-1, 1]. Phase-continuous."""
        self._ensure_capacity(num_samples)
        out = self._buf[:num_samples]

        if self._current_amp == 0.0 and self._target_amp == 0.0:
            out[:] = 0.0
            return out

        d_phi = TWO_PI * self._frequency / self._samplerate
        phase_buf = self._phase_buf[:num_samples]
        np.multiply(self._indices[:num_samples], d_phi, out=phase_buf)
        phase_buf += self._phase
        sine_buf = self._sine_buf[:num_samples]
        np.sin(phase_buf, out=sine_buf)

        self._phase = math.fmod(self._phase + d_phi * num_samples, TWO_PI)

        if self._current_amp == self._target_amp:
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

    @property
    def is_silent(self) -> bool:
        return self._current_amp == 0.0 and self._target_amp == 0.0


class ShakerSynth:
    """Mixer + sounddevice OutputStream wrapper. Thread-safe."""

    def __init__(self, samplerate: int = 48000,
                 device: Optional[Union[int, str]] = None,
                 blocksize: int = 256, master_gain: float = 1.0):
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.device = device
        self._master_gain = float(master_gain)
        self._oscillators: dict[str, Oscillator] = {}
        self._lock = threading.Lock()
        self._mix_buf = np.zeros(self.blocksize, dtype=np.float32)
        self._stream: Optional[sd.OutputStream] = None
        self._channels = 1

    def start(self) -> None:
        if self._stream is not None:
            logger.debug("ShakerSynth already started")
            return

        device_idx = self._resolve_device(self.device) if self.device is not None else None

        channels = 1
        try:
            sd.check_output_settings(device=device_idx, samplerate=self.samplerate,
                                     channels=1, dtype='float32')
        except sd.PortAudioError:
            channels = 2
        self._channels = channels
        logger.info("Opening sounddevice OutputStream: device=%s sr=%d block=%d ch=%d",
                    device_idx, self.samplerate, self.blocksize, channels)

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


def _selftest(device, samplerate: int) -> None:
    print(f"ShakerSynth selftest: device={device!r} samplerate={samplerate}")
    synth = ShakerSynth(samplerate=samplerate, device=device)
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
    p.add_argument("--device", type=str, default=None,
                   help="Output device (integer index or name substring)")
    p.add_argument("--samplerate", type=int, default=48000,
                   help="Sample rate in Hz (default 48000)")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.list_devices:
        _list_devices()
    else:
        _selftest(_parse_device(args.device), args.samplerate)


if __name__ == "__main__":
    main()
