# STEP_06 — Schema v2: layer fields + migration

## Goal

Extend `Layer` and `shaker_effects.json` to support `bandpass_noise`. Existing
version-1 files must still load cleanly.

## Layer dataclass extension

`Layer` is defined in `telemffb/hw/ffb_shaker.py` (re-imported by
`shaker_layers_io.py`). Extend with three optional fields:

```python
@dataclass(frozen=True)
class Layer:
    freq_factor: float = 1.0
    gain: float = 1.0
    route: str = "both"
    osc_type: str = "sine"  # "sine" | "impulse" | "bandpass_noise"
    # New optional fields (only meaningful if osc_type == "bandpass_noise"):
    center_hz: float | None = None     # if None, uses freq_factor * call_site_freq
    bandwidth_hz: float | None = None  # if None, defaults to 20.0 Hz at runtime
```

### Semantics

- `freq_factor` and `gain` retain existing meaning. For noise layers,
  `freq_factor * call_site_freq` is the implicit center frequency unless
  `center_hz` is set explicitly. This keeps simple noise-from-existing-effect
  cases declarative.
- `gain` continues to multiply call-site magnitude as the amplitude argument to
  `BandpassNoiseGenerator.set()`.
- `bandwidth_hz` defaults to 20 Hz when `None`. Sensible "wide enough to feel
  rough, narrow enough to feel pitched" baseline.
- For `osc_type` other than `bandpass_noise`, `center_hz` and `bandwidth_hz`
  are ignored. They may still be present in JSON (future-proof / round-trip safe).

## Schema-IO update

`telemffb/hw/shaker_layers_io.py:28`:

```python
CURRENT_VERSION = 2  # bumped from 1
```

- **Loader (`load()`):** v1 files load via dataclass defaults — extra fields just
  won't be in the JSON, fields default to `None`. v2 files round-trip the new
  fields. v > 2 logs a warning and best-effort-loads (existing permissive
  behaviour).
- **Saver (`save()`):** always writes version 2. After this STEP runs once, the
  user's `shaker_effects.json` upgrades to v2 the next time they hit "Save all
  effects" in the UI. Until then it stays v1 on disk and loads correctly.

## Runtime dispatch

`_start_layered()` in `ffb_shaker.py` currently has a 2-way branch
(lines 406-409). Expand to explicit 3-way + fallthrough warning:

```python
if layer.osc_type == "sine":
    osc = synth.get_oscillator(name)
    osc.set(call_freq * layer.freq_factor, call_mag * layer.gain)
elif layer.osc_type == "impulse":
    osc = synth.get_oscillator(name)
    osc.trigger(call_freq * layer.freq_factor, call_mag * layer.gain)
elif layer.osc_type == "bandpass_noise":
    osc = synth.get_noise_oscillator(name)
    center = layer.center_hz if layer.center_hz is not None else call_freq * layer.freq_factor
    bw = layer.bandwidth_hz if layer.bandwidth_hz is not None else 20.0
    osc.set(center_hz=center, bandwidth_hz=bw, amplitude=call_mag * layer.gain)
else:
    logger.warning("Unknown osc_type %r in layer for %s — skipping", layer.osc_type, self.name)
```

(Note: existing impulse path uses `Oscillator.trigger()`, not a separate
`get_impulse_oscillator()`. There's no impulse-specific accessor.)

## ShakerSynth.get_noise_oscillator

Add to `ShakerSynth`, mirroring `get_oscillator` (line 293):

```python
def get_noise_oscillator(self, name: str) -> "BandpassNoiseGenerator":
    """Return the named noise oscillator, creating it if needed. Thread-safe."""
    with self._lock:
        osc = self._oscillators.get(name)
        if not isinstance(osc, BandpassNoiseGenerator):
            osc = BandpassNoiseGenerator(self.samplerate)
            self._oscillators[name] = osc
        return osc
```

## Acceptance

- A v1 `shaker_effects.json` (use the current default pack) loads without
  warnings; all effects play as before.
- A hand-crafted v2 file with one `bandpass_noise` layer loads, and triggering
  that effect produces audible noise on the shaker.
- `Layer` round-trips through `asdict` → JSON → `Layer(**...)`.
- The default pack (`telemffb/data/shaker_effects_default.json`) is **not
  modified** — the user introduces noise layers manually if MSFS testing
  indicates a need.

Stop and request review.
