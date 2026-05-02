# STEP_01 — Layer runtime

## Goal

Teach `telemffb/hw/ffb_shaker.py` to dispatch a single `.start()` call across
multiple layer-oscillators driven by an `EFFECT_LAYERS` dict. No external
file loading yet — STEP_01 hardcodes a small test dict so the dispatch can
be verified standalone. STEP_02 replaces that hardcoded dict with JSON
loading.

## Layer model

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Layer:
    freq_factor: float = 1.0
    gain: float = 1.0
    route: str = "both"        # "shaker" | "stick" | "both"
    osc_type: str = "sine"     # "sine" | "impulse"

DEFAULT_LAYER = Layer()
```

Field semantics:

| Field | Type | Range / values | Effect |
|-------|------|----------------|--------|
| `freq_factor` | float | 0.1 .. 4.0 (UI clamps; runtime accepts any positive) | Multiplies the call-site frequency. `aircraft_base.py` calls `effects["X"].periodic(40, ...)` → a layer with `freq_factor=0.5` plays at 20 Hz. |
| `gain` | float | 0.0 .. 1.5 (UI clamps) | Multiplies the call-site magnitude. The synth's master clip handles per-effect totals exceeding 1.0. |
| `route` | str | `"shaker"`, `"stick"`, `"both"` | Shaker child plays only `"shaker"` and `"both"` layers; `"stick"` is filtered out. Stick code is unmodified in this iteration. |
| `osc_type` | str | `"sine"`, `"impulse"` | `"sine"` → `Oscillator.set()` (continuous, ramped). `"impulse"` → `Oscillator.trigger()` (one-shot envelope). |

## Coexistence rules with the existing pipeline

Order of decision in `HapticEffect.start()` after this step:

1. `self.name not in SHAKER_EFFECT_WHITELIST` → drop (unchanged).
2. `self.name in EFFECT_LAYERS` → take the **layer path** below.
3. Else `self.name in SHAKER_EFFECT_PROFILES` → existing single-oscillator
   profile path (unchanged).
4. Else → existing default / square-pulse-heuristic path (unchanged).

So an effect entering `EFFECT_LAYERS` opts out of the legacy profile;
otherwise everything keeps working.

## Per-layer oscillator naming

```
<effect_name>__layer<index>
```

Two underscores as separator to avoid clashes with effect names that
already contain a single underscore (e.g. `je_rumble_1_1`). Index starts
at 0 and matches the layer's position in the list. When a layer is
filtered out (route is `"stick"`), no oscillator is created for that
index — the index gap is fine, the synth's mixer iterates by name not by
index.

## Routing predicate

```python
def _layer_is_for_shaker(layer: Layer) -> bool:
    return layer.route in ("shaker", "both")
```

Defined at module scope in `ffb_shaker.py`.

## Refactor sketch — `HapticEffect.start()`

The actual current method body lives at `telemffb/hw/ffb_shaker.py:283`
onwards. Adapt the new layer path so the existing profile path is
**preserved verbatim** for non-layered effects.

```python
def start(self, force: bool = False, **kw) -> "HapticEffect":
    if _synth is None or self.name is None:
        ...  # existing early-return logging, unchanged
        return self
    if self.name not in SHAKER_EFFECT_WHITELIST:
        logger.debug("Shaker start: effect %r not in whitelist; dropping", self.name)
        return self

    if self.name in EFFECT_LAYERS:
        return self._start_layered(EFFECT_LAYERS[self.name])

    # ---- existing profile / heuristic / default path unchanged below ----
    profile = SHAKER_EFFECT_PROFILES.get(self.name)
    ...
```

`_start_layered()` is a new helper:

```python
def _start_layered(self, layers: list[Layer]) -> "HapticEffect":
    created_names: list[str] = []
    with _synth._lock:
        for idx, layer in enumerate(layers):
            if not _layer_is_for_shaker(layer):
                continue
            osc_name = f"{self.name}__layer{idx}"
            eff_freq = self.frequency * layer.freq_factor
            eff_mag  = self.magnitude * layer.gain
            osc = _synth._oscillators.get(osc_name)
            if osc is None:
                osc = Oscillator(_synth.samplerate, _synth.blocksize)
                _synth._oscillators[osc_name] = osc
            if layer.osc_type == "impulse":
                osc.trigger(eff_freq, eff_mag)
            else:
                osc.set(eff_freq, eff_mag)
            created_names.append(osc_name)

    logger.debug("Shaker layered start name=%r layers=%d -> %s",
                 self.name, len(layers), created_names)

    # Cancel prior duration timer.
    if self._duration_timer is not None:
        self._duration_timer.cancel()
        self._duration_timer = None
    # Only continuous (sine) layers need a duration-driven stop. Impulse
    # layers carry their own envelope; we should not stop them prematurely.
    needs_timer = self.duration > 0 and any(
        l.osc_type == "sine" and _layer_is_for_shaker(l) for l in layers
    )
    if needs_timer:
        sine_names = [
            f"{self.name}__layer{i}"
            for i, l in enumerate(layers)
            if l.osc_type == "sine" and _layer_is_for_shaker(l)
        ]
        t = threading.Timer(
            self.duration / 1000.0,
            lambda: self._stop_layer_names(sine_names),
        )
        t.daemon = True
        self._duration_timer = t
        t.start()
    return self
```

`_stop_layer_names()`:

```python
def _stop_layer_names(self, names: list[str]) -> None:
    if _synth is None:
        return
    with _synth._lock:
        for name in names:
            osc = _synth._oscillators.get(name)
            if osc is not None:
                osc.stop()
```

## Refactor sketch — `HapticEffect.stop()`

The current `stop()` is at `telemffb/hw/ffb_shaker.py:373`. Add a layer
path symmetrically:

```python
def stop(self, destroy_after: int = 10000) -> "HapticEffect":
    if _synth is None or self.name is None:
        return self
    if self._duration_timer is not None:
        self._duration_timer.cancel()
        self._duration_timer = None

    if self.name in EFFECT_LAYERS:
        layers = EFFECT_LAYERS[self.name]
        names = [
            f"{self.name}__layer{i}"
            for i, l in enumerate(layers)
            if _layer_is_for_shaker(l)
        ]
        self._stop_layer_names(names)
        return self

    # ---- existing single-oscillator stop path unchanged below ----
    with _synth._lock:
        osc = _synth._oscillators.get(self.name)
        ...
```

Impulse layers will already be silent (envelope ended itself). Calling
`osc.stop()` on a silent oscillator is a no-op — safe.

## `ShakerSynth` changes

None needed. The existing `Oscillator` class supports both `set()` and
`trigger()`; the synth's mixer iterates `self._oscillators.values()` and
ignores silent oscillators. A separate `get_impulse_oscillator()` method
would be a footgun (two name spaces for the same dict) — `osc_type` is
purely a method selector at the call site.

## Hardcoded test dict for STEP_01

Add at the top of `ffb_shaker.py` near `SHAKER_EFFECT_PROFILES`:

```python
EFFECT_LAYERS: dict[str, list[Layer]] = {
    "je_rumble_1_1": [
        Layer(freq_factor=0.5, gain=0.85, route="shaker", osc_type="sine"),
        Layer(freq_factor=1.0, gain=0.50, route="stick",  osc_type="sine"),
        Layer(freq_factor=2.0, gain=0.30, route="shaker", osc_type="sine"),
    ],
    "gunfire": [
        Layer(freq_factor=0.4, gain=0.90, route="shaker", osc_type="impulse"),
        Layer(freq_factor=1.0, gain=0.50, route="stick",  osc_type="sine"),
    ],
    "touchdown": [
        Layer(freq_factor=0.4, gain=1.00, route="shaker", osc_type="impulse"),
        Layer(freq_factor=2.0, gain=0.40, route="stick",  osc_type="impulse"),
    ],
}
```

This dict is removed in STEP_02 (replaced by JSON-loaded data) and the
defaults that ship are populated in STEP_04.

## Acceptance

A repl / scripted check, with audio output:

```python
from telemffb.hw.shaker_synth import ShakerSynth
from telemffb.hw.ffb_shaker import (
    HapticEffect, init_shaker, EFFECT_LAYERS, Layer, SHAKER_EFFECT_WHITELIST,
)

synth = ShakerSynth(device=None); synth.start()
init_shaker(synth)

# Override with a known test config:
EFFECT_LAYERS["je_rumble_1_1"] = [
    Layer(freq_factor=0.5, gain=0.8, route="shaker"),
    Layer(freq_factor=1.0, gain=0.6, route="stick"),    # filtered out
    Layer(freq_factor=2.0, gain=0.4, route="shaker"),
]

e = HapticEffect()
e.name = "je_rumble_1_1"
e.periodic(40, 0.5, 0).start()
# Expect: 20 Hz layer0 and 80 Hz layer2 audible (chord), 40 Hz NOT audible
import time; time.sleep(2)
e.stop()
```

Pass criteria:

- Layered effect: only `shaker` / `both` layers produce sound. `stick` is
  silent.
- Non-layered whitelisted effect (e.g. `runway0`) keeps working exactly as
  today — single oscillator, profile / heuristic path unchanged.
- An effect with a `SHAKER_EFFECT_PROFILES` entry but no `EFFECT_LAYERS`
  entry (e.g. `gearclunk`) still triggers via the profile transient path.
- `e.stop()` silences every layer of `e.name` immediately.
- `Oscillator.trigger()` impulse layers self-stop after their envelope —
  no leftover sound after a `touchdown` impulse.
- Repeated `e.start()` calls re-apply layers (set() ramps, trigger()
  retriggers) without leaking oscillators in `synth._oscillators`. (The
  dict grows by `len([shaker layers])` per effect, then plateaus —
  expected.)

## Out of scope for STEP_01

- File loading (STEP_02).
- UI editor (STEP_03).
- Default layer pack (STEP_04).
- Stick-side layer awareness — stick code is untouched throughout this
  iteration.

## Stop here

After implementing and verifying STEP_01, tick the box in `PLAN_LAYERS.md`,
append a note about anything that surprised you, and stop for review.
