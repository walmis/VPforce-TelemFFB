# STEP_05 — BandpassNoiseGenerator in shaker_synth.py

## Goal

A new oscillator-compatible class that produces band-limited noise. Same
render / stop / is_silent contract as `Oscillator`, so it plugs into the existing
mixer (`ShakerSynth._callback`) without changes.

A bass shaker doesn't reproduce highs — bandpass noise centred at e.g. 35 Hz with
a bandwidth of 20 Hz feels like a natural rumble (rough engine, wind,
runway-on-grass) rather than a pure sine. Implementation: white-noise source +
RBJ biquad bandpass filter.

## Class skeleton

```python
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
```

## Implementation notes

- **Python `for` loops in `render()` are acceptable for MVP.** Block size is 256
  samples, callback rate ~187 Hz at 48 kHz. If profiling shows the biquad loop is
  the bottleneck, vectorize via `scipy.signal.lfilter` (adds scipy dep — defer).
  For now: numpy-only.
- **Filter stability:** RBJ bandpass is unconditionally stable for sane Q. Clamping
  `bandwidth_hz >= 1.0` and `center_hz >= 1.0` keeps Q in range.
- **Variance normalization:** the constant-skirt-gain BPF has peak gain ≈ Q at
  center. For Q in the 1-3 range peaks can be 1-3× the input white-noise stddev.
  The `np.clip` at the end keeps things sane; an explicit `1/Q` normalization can
  be added later if peaks are too uneven across center frequencies.
- **Reproducibility:** `_rng = np.random.default_rng()` uses fresh entropy. If
  determinism is wanted later, add an optional `seed` parameter.
- **Constructor signature:** takes `(samplerate, blocksize=None)` to match
  `Oscillator(samplerate, blocksize)`'s shape, even though `blocksize` is unused.

## Selftest CLI

Add a mode to `shaker_synth.py`'s `__main__`:

```
python -m telemffb.hw.shaker_synth --selftest-noise [--device IDX] [--center HZ] [--bandwidth HZ]
```

Plays 3 seconds of bandpass noise at the given center/bandwidth (defaults:
35 Hz / 20 Hz), at 0.5 amplitude, with smooth fade-in and fade-out.

## Acceptance

- `BandpassNoiseGenerator` exists in `shaker_synth.py` with the documented interface.
- `--selftest-noise` plays clean band-limited noise — no clicks at start/end (fade),
  no clipping (audible distortion), no silence.
- Sweeping center 20 Hz → 80 Hz produces audibly different "characters" of rumble.
- Manual integration test:

  ```python
  synth = ShakerSynth(device=<idx>); synth.start()
  noise = BandpassNoiseGenerator(synth.samplerate)
  noise.set(center_hz=35, bandwidth_hz=20, amplitude=0.5)
  synth.add_oscillator("test_noise", noise)   # uses STEP_03 API
  time.sleep(2)
  noise.stop(); time.sleep(0.5)
  synth.stop()
  ```

  produces audible noise rumble on the shaker.

Stop and request review.
