# STEP_02 — HapticEffect facade

## Goal

Create `telemffb/hw/ffb_shaker.py` — a class with the **same surface** as `telemffb.hw.ffb_rhino.HapticEffect` so that `aircraft_base.py` code calling `effects["runway0"].periodic(...).start()` works unchanged. Behind the scenes, calls route into a module-level `ShakerSynth` instance.

## Required exported symbols

Mirror what `aircraft_base.py:29-31` imports from `ffb_rhino`:

```
EFFECT_TRIANGLE, EFFECT_SQUARE, EFFECT_SINE, EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN
EFFECT_CONSTANT, EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION
EFFECT_SPRING_ADJUSTER
HapticEffect, FFBReport_SetCondition  # FFBReport_SetCondition can be a stub class
```

Use the same numeric constants as `ffb_rhino.py` so both modules are interchangeable. (Read them out of `ffb_rhino.py` directly; do not redefine values from training data.)

## `class HapticEffect`

| Method | Behaviour on shaker |
|---|---|
| `__init__()` | Stores `self.name = None`, `self.frequency = 0.0`, `self.magnitude = 0.0`, `self.direction = 0`, `self.duration = 0`, `self.modulator = None`. `Dispenser` will set `self.name` on creation (verified at `telemffb/utils.py:1108`). |
| `.periodic(frequency, magnitude, direction, *args, effect_type=EFFECT_SINE, duration=0, **kwargs)` | Stores `frequency`, `magnitude`, `direction`, `duration`. Returns `self`. **Does not start playback** — playback begins on `.start()`. |
| `.constant(magnitude, direction, *args, **kwargs)` | Stores params with a default frequency of **25 Hz** (low rumble). Effects like `runway0`, `gforce` use `.constant()` and are perceived as continuous shake; mapping them to a low-freq sine produces a useful sensation. Returns `self`. |
| `.start(force=False, **kw)` | If `self.name not in SHAKER_EFFECT_WHITELIST` (added in STEP_04), log debug and return `self`. Otherwise look up oscillator by `self.name` from the module-level `ShakerSynth`. Call `osc.set(self.frequency, self.magnitude * master_gain_factor)`. If `self.duration > 0`, schedule `.stop()` after `duration` ms via `threading.Timer`. |
| `.stop(destroy_after=10000)` | `osc.stop()`. `destroy_after` is ignored (audio oscillators are cheap, no need to destroy). |
| `.destroy()` | `synth.remove_oscillator(self.name)`. |
| `.spring`, `.damper`, `.friction`, `.inertia`, `.setCondition`, `.spring_adjuster`, `._conditional_effect` | All return `self` (chainable) and log at `debug` level: `"Force effect %s ignored on shaker device"`. |
| `.started` (property) | True if oscillator exists for `self.name` and is not silent. |
| `.modulator` | Attribute exists for compatibility (set to `None`); direction modulators don't affect the shaker output in MVP. |

## Module-level state

```python
_synth: ShakerSynth | None = None  # set via init_shaker(synth) at startup

def init_shaker(synth: ShakerSynth) -> None: ...

class HapticEffect:
    device = None  # placeholder for compat with ffb_rhino.HapticEffect.device
```

The whitelist `SHAKER_EFFECT_WHITELIST` introduced in STEP_04 lives in this same module.

## Acceptance

- The following import succeeds:
  ```python
  from telemffb.hw.ffb_shaker import (
      HapticEffect, EFFECT_SINE, EFFECT_SQUARE, EFFECT_TRIANGLE,
      EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN,
      EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION,
      EFFECT_SPRING_ADJUSTER, FFBReport_SetCondition,
  )
  ```
- A small ad-hoc test:
  ```python
  synth = ShakerSynth(device=<idx>); synth.start()
  init_shaker(synth)
  e = HapticEffect(); e.name = "test"
  # NOTE: "test" must temporarily be added to the whitelist for this assertion,
  # OR run the assertion before STEP_04 lands.
  e.periodic(35, 0.5, 0).start()
  time.sleep(2); e.stop(); time.sleep(0.5); synth.stop()
  ```
  produces audible shake.
- Calling `e.spring()`, `e.damper(coef_x=4096)`, `e.setCondition(...)` is harmless (no-ops with debug logs).
- GPL v3 header copied from `telemffb/hw/ffb_rhino.py`.
