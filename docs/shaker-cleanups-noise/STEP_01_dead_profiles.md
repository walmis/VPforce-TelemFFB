# STEP_01 — Remove dead SHAKER_EFFECT_PROFILES entries

## Goal
Effects already covered by `EFFECT_LAYERS` (whether from the bundled default pack or
the user's `shaker_effects.json`) take the layered code path in `start()` — the
`SHAKER_EFFECT_PROFILES` entry for these effects is dead. Remove it.

## Background — runtime priority chain

`telemffb/hw/ffb_shaker.py:436-503` (`start()`) dispatches in this order:

1. **Whitelist** — drop effects not in `SHAKER_EFFECT_WHITELIST`.
2. **EFFECT_LAYERS** — if `effect.name in EFFECT_LAYERS`, call `_start_layered()` and return.
3. **PROFILES** — if `effect.name in SHAKER_EFFECT_PROFILES`, use the profile's
   freq/gain/kind values.
4. **Heuristic** — square-pulse → transient envelope (≤80 ms) or continuous default.

So any effect in BOTH layers AND profiles never reaches step 3 — the profile entry
is dead code.

## Procedure

1. Read `telemffb/data/shaker_effects_default.json` and list `effects.*` keys
   (already verified: 17 effects).
2. Read `SHAKER_EFFECT_PROFILES` at `telemffb/hw/ffb_shaker.py:105-125` and list its
   keys (already verified: 12 entries).
3. **Intersection = dead profile entries.** Remove these from
   `SHAKER_EFFECT_PROFILES`:

   ```
   touchdown, gunfire, cm, buffeting, vrs_buffet, gearbuffet
   ```

4. **Surviving entries** (profiles only, no layered default — keep):

   ```
   gearclunk, runway_bump0, runway_bump1, payload_rel, buffeting2, gearbuffet2
   ```

   Add a one-line comment block above the surviving profiles, e.g.:

   ```python
   # Per-effect single-oscillator profile tuning. These effects are not in the
   # default layer pack. If a user adds them to their shaker_effects.json as a
   # layered entry, the layer takes precedence at runtime (see start() priority:
   # Whitelist -> EFFECT_LAYERS -> PROFILES -> Heuristic).
   ```

## Verification

- Behaviour for currently-shipped effects is identical: layered effects still hit
  step 2; the 6 deletions never reached step 3 anyway.
- Effects with neither a profile nor a layer entry still fall through to the
  heuristic (step 4) — same as before.

## Acceptance

- `SHAKER_EFFECT_PROFILES` is shorter; the 6 removed entries are exactly the
  dead-profile intersection.
- All three CLI selftests still run:
  - `python -m telemffb.hw.ffb_shaker --selftest`
  - `python -m telemffb.hw.ffb_shaker --selftest-transient`
  - `python -m telemffb.hw.ffb_shaker --selftest-layered`
- A run of TelemFFB in shaker mode with the default pack produces no log
  warnings about missing profiles.

Stop and request review.
