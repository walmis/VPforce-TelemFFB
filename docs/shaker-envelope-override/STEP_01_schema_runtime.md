# STEP_01 — Schema v3: Layer attack_ms/decay_ms + runtime + test worker

## Goal

Extend the Layer dataclass with two optional impulse-envelope fields,
bump the JSON schema to v3 (loader still accepts v1 + v2), and route the
new values through the runtime impulse dispatch and the layer-editor
test worker.

## Layer dataclass extension

`telemffb/hw/ffb_shaker.py`:

```python
@dataclass(frozen=True)
class Layer:
    freq_factor:  float = 1.0
    gain:         float = 1.0
    route:        str   = "both"
    osc_type:     str   = "sine"  # "sine" | "impulse" | "bandpass_noise"
    # Only meaningful when osc_type == "bandpass_noise":
    center_hz:    Optional[float] = None
    bandwidth_hz: Optional[float] = None
    # Only meaningful when osc_type == "impulse":
    # If None, Oscillator.trigger() uses its built-in defaults
    # (attack_ms=4.0, decay_ms=90.0).
    attack_ms:    Optional[float] = None
    decay_ms:     Optional[float] = None
```

## Schema-IO

`telemffb/hw/shaker_layers_io.py`:

- `CURRENT_VERSION = 3` (was 2).
- Loader: accept `version in (1, 2, 3)` silently. Anything else → warning.
- Saver: always writes v3.
- Update the migration comment block.

## Runtime dispatch

In `_start_layered`'s impulse branch, pass through `attack_ms` /
`decay_ms` as kwargs only if not None — letting the dataclass defaults of
`None` map cleanly to `Oscillator.trigger`'s built-in defaults:

```python
elif layer.osc_type == "impulse":
    osc = _synth.get_oscillator(osc_name)
    kwargs = {}
    if layer.attack_ms is not None:
        kwargs["attack_ms"] = layer.attack_ms
    if layer.decay_ms is not None:
        kwargs["decay_ms"] = layer.decay_ms
    osc.trigger(eff_freq, eff_mag, **kwargs)
```

## Test worker

`telemffb/SystemSettingsDialog.py:_on_shaker_layer_test._run`: the
impulse branch currently calls `osc.trigger(eff_freq, eff_mag)` with
defaults. Mirror the runtime dispatch:

```python
elif layer.osc_type == "impulse":
    osc = Oscillator(synth.samplerate, synth.blocksize)
    kwargs = {}
    if layer.attack_ms is not None:
        kwargs["attack_ms"] = layer.attack_ms
    if layer.decay_ms is not None:
        kwargs["decay_ms"] = layer.decay_ms
    osc.trigger(call_freq * layer.freq_factor,
                call_mag * layer.gain, **kwargs)
```

## Verification

- AST parse all three modified files.
- Layer round-trip with the new fields:
  `Layer(osc_type="impulse", attack_ms=2.0, decay_ms=200.0)`.
- v1 + v2 backward compat — load the bundled default pack (v1 on disk)
  and a hand-crafted v2 file (with center_hz/bandwidth_hz only).
- `python -m telemffb.hw.ffb_shaker --selftest-layered` reaches
  `synth.start()` (sandbox PortAudio fail expected).
- `git grep -nE '_oscillators|_lock'` outside `shaker_synth.py` is empty
  (encapsulation preserved).

## Acceptance

- The new fields exist; both default to None; v1 and v2 files still
  load without warnings.
- Existing layered effects produce identical audio output (since
  None maps to `Oscillator.trigger`'s built-in defaults — unchanged).
- A v3 file with explicit `attack_ms` / `decay_ms` round-trips through
  save/load.

## Constraints

- Edit ONLY `telemffb/hw/ffb_shaker.py`, `telemffb/hw/shaker_layers_io.py`,
  `telemffb/SystemSettingsDialog.py`.
- Do NOT edit the bundled default pack.
- Do NOT touch the existing sine / bandpass_noise dispatch branches.
