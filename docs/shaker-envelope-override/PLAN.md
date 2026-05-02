# TelemFFB Shaker — Schema-v3: Per-Layer Envelope Override

## Status legend
- [ ] not started
- [~] in progress
- [x] done
- [!] blocked / needs design change

## Phases

- [x] STEP_00 — Bootstrap planning artifacts
- [x] STEP_01 — Schema v3: Layer attack_ms/decay_ms + runtime + test worker
- [x] STEP_02 — UI: two new columns (Attack ms / Decay ms) on impulse rows
- [x] STEP_03 — SHAKER.md update + SMOKETEST_RESULTS

## Context

Today every `osc_type="impulse"` layer triggers via `Oscillator.trigger(...)`'s
defaults (`attack_ms=4.0`, `decay_ms=90.0`). That means `touchdown` (which
should feel like a soft body-thud over ~200 ms) and `gearclunk` (which
should feel like a snappy 80 ms metallic crack) cannot be tuned distinctly
through layers — only through the legacy `SHAKER_EFFECT_PROFILES` table.

This iteration adds two optional `Layer` fields, ignored unless
`osc_type="impulse"`, that override the trigger envelope per layer. Schema
bumps to v3, with the same forward-compat-loader + always-write-current
pattern established in v2.

Once available, the user can migrate the per-effect impulse tuning that
currently lives in `SHAKER_EFFECT_PROFILES` into the layered default pack
and eventually retire the PROFILES table entirely.

## Working agreement

1. Read before writing — verify symbol names and exact line numbers via
   `grep`. The layout has shifted across STEP_01–08 of the previous
   iteration.
2. Each STEP gets an implementer + an independent reviewer (sonnet).
3. Strictly additive — no existing layer or profile changes observable
   behaviour. Schema v1 and v2 files load exactly as before; the new
   v3 fields default to `None`, which preserves `Oscillator.trigger`'s
   built-in defaults (4 ms / 90 ms).
4. UI mirrors the v2 noise pattern: two new disabled-by-default columns,
   enabled only on impulse rows; values preserved across `osc_type`
   toggles in the working copy.
5. The shipped default pack (`shaker_effects_default.json`) is **not**
   modified in this iteration — the user introduces per-layer envelope
   tuning manually in the UI after the in-flight MSFS validation.
6. Tick checkboxes here after each step.

## Notes / Deferred

(append decisions, deviations here)

- `Oscillator.trigger` defaults at `shaker_synth.py:99-100` are
  `attack_ms=4.0, decay_ms=90.0`. SHAKER.md previously claimed 3 ms attack
  — fix in STEP_03's doc update.
- Today's `SHAKER_EFFECT_PROFILES` impulse profiles (`gearclunk`,
  `runway_bump0`, `runway_bump1`, `payload_rel`) carry `attack_ms` /
  `decay_ms` that this schema can absorb 1:1 if the user later migrates
  them into the default pack.
