# STEP_04 — Effect routing whitelist

## Goal

Force-only effects must not produce audio output. Whitelisted rumble/periodic effects must.

## File

`telemffb/hw/ffb_shaker.py` (created in STEP_02).

Add a module-level set:

```python
SHAKER_EFFECT_WHITELIST = {
    # wheel / runway
    "runway0", "runway1", "runway_bump0", "runway_bump1", "touchdown",
    # weapons / countermeasures
    "gunfire", "cm", "payload_rel",
    # buffeting
    "buffeting", "buffeting2", "vrs_buffet",
    "gearbuffet", "gearbuffet2",
    "spoilerbuffet1-1", "spoilerbuffet1-2", "spoilerbuffet2-1", "spoilerbuffet2-2",
    # afterburner / jet
    "ab_rumble_1_1", "ab_rumble_1_2", "ab_rumble_2_1", "ab_rumble_2_2",
    "je_rumble_1_1", "je_rumble_1_2", "je_rumble_2_1", "je_rumble_2_2",
    # prop / rotor
    "prop_rpm0-1", "prop_rpm0-2", "prop_rpm1-1", "prop_rpm1-2",
    "rotor_rpm0-1", "rotor_rpm1-1",
    # ETL
    "etlX", "etlY",
    # surface movements
    "flapsmovement", "gearmovement", "gearmovement2",
    "speedbrakemovement", "spoilermovement", "spoilermovement2",
    "canopymovement", "hookmovement",
    # overspeed / aoa
    "overspeedX", "overspeedY", "aoa", "crit_aoa",
    # wind
    "wnd",
}
```

In `HapticEffect.start()`: if `self.name not in SHAKER_EFFECT_WHITELIST`, log at `debug` and return `self` without touching the synth.

## Dispenser name propagation — already correct

`telemffb/utils.py:1099-1110` (`class Dispenser`) already assigns `v.name = name` at line `:1108`:

```python
def get(self, name, *args, **kwargs):
    v = self.dict.get(name)
    if not v:
        v = self.cls(*args, **kwargs)
        v.name = name        # ← name propagation already in place
        self.dict[name] = v
    return v
```

So `effects["runway0"]` produces a `HapticEffect` instance with `name == "runway0"`. **No Dispenser change is required** — STEP_04's whitelist check can rely on `self.name` being set correctly. The brief listed a contingency to fix the Dispenser; that contingency is mooted.

## Acceptance

- `effects["spring_adjuster"].spring_adjuster()` is a no-op (no audio, no exception). (Method-level no-op verified separately by STEP_02 facade methods.)
- `effects["runway0"].constant(0.5, 0).start()` produces audible shake at 25 Hz.
- `effects["gunfire"].periodic(80, 0.4, 0, duration=200).start()` produces a 200 ms 80 Hz burst.
- An effect name that is **not** in the whitelist (e.g. a temporarily-named test effect) does not produce audio when `.start()` is called, and a debug-level log line is emitted.
- Adding a new effect name to the whitelist requires no code change elsewhere.
