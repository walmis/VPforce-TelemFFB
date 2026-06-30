# STEP_01 — Audio synth core

## Goal

Create `telemffb/hw/shaker_synth.py` — a standalone audio synthesis module that opens a PortAudio output stream via `sounddevice`, mixes phase-continuous sine oscillators, and exposes a small thread-safe API.

## Constraint

This module must be **standalone**. It must not import anything from `telemffb.*`. It uses only `numpy`, `sounddevice`, `threading`, `logging`, `dataclasses`, and stdlib. This makes it independently testable and reusable.

Add `sounddevice` to `requirements.txt` as part of this step.

## Public API

```python
class Oscillator:
    """Single phase-continuous sine oscillator with smooth amplitude ramping."""
    def __init__(self, samplerate: int): ...
    def set(self, freq: float, amplitude: float, ramp_ms: float = 50.0) -> None:
        """Update target freq/amplitude; amplitude ramps linearly over ramp_ms."""
    def stop(self, ramp_ms: float = 50.0) -> None:
        """Ramp amplitude to 0."""
    def render(self, num_samples: int) -> np.ndarray:
        """Render num_samples float32 samples in [-1, 1]. Phase-continuous across calls."""
    @property
    def is_silent(self) -> bool: ...

class ShakerSynth:
    """Mixer + sounddevice OutputStream wrapper. Thread-safe."""
    def __init__(self, samplerate: int = 48000, device: int | str | None = None,
                 blocksize: int = 256, master_gain: float = 1.0): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def get_oscillator(self, name: str) -> Oscillator:
        """Auto-creates an oscillator under the given name. Idempotent."""
    def remove_oscillator(self, name: str) -> None: ...
    def set_master_gain(self, gain: float) -> None: ...

    @staticmethod
    def list_output_devices() -> list[dict]:
        """Wraps sounddevice.query_devices, filters output-capable, returns
        list of {'index': int, 'name': str, 'samplerate': float, 'channels': int}."""
```

## Implementation notes

- Phase stored as float, fmod'ed by `2π` after each render to avoid precision drift.
- Amplitude ramping: store `current_amplitude` and `target_amplitude`; per-sample step = `(target - current) / ramp_samples`; clamp when reached.
- Frequency change: takes effect immediately at sample boundary (no glide for MVP — frequency changes on a bass shaker are imperceptible compared to amplitude clicks).
- Mixer: sum oscillators into one buffer, multiply by `master_gain`, clip to `[-1, 1]`. Output is mono float32. (If the chosen device requires stereo, duplicate the mono signal.)
- The `OutputStream` callback runs in PortAudio's audio thread. Keep the callback fast: just acquire the lock, iterate oscillators, `+=` their renders, release. **No allocation in the callback hot path** — preallocate the buffer in `__init__`.
- Lock granularity: one `threading.Lock` around the oscillator dict and amplitude/frequency updates. Cheap to acquire.
- GPL v3 header at the top of the file (copy verbatim from `telemffb/hw/ffb_rhino.py`).
- Use `logging` for diagnostics (no `print`) — module logger via `logging.getLogger(__name__)`. The selftest `__main__` block may use `print` for human-readable output.

## Selftest

Add a `if __name__ == "__main__":` block:

```
python -m telemffb.hw.shaker_synth --list-devices
  → prints the output devices

python -m telemffb.hw.shaker_synth --selftest [--device IDX] [--samplerate SR]
  → opens stream, plays:
      0.0 - 2.0 s : 30 Hz @ 0.4 amplitude
      2.0 - 4.0 s : 30 Hz fades out, 80 Hz @ 0.3 fades in (crossfade)
      4.0 - 6.0 s : 80 Hz fades out, 15 Hz @ 0.2 fades in
      6.0 s        : stop
```

## Acceptance

- `python -m telemffb.hw.shaker_synth --list-devices` prints sane output on the dev machine.
- `python -m telemffb.hw.shaker_synth --selftest` runs without exceptions, no audible clicks at transitions, no underruns logged by sounddevice.
- Code runs with `python -m` without any TelemFFB imports having to be available.
- `requirements.txt` contains `sounddevice`.
