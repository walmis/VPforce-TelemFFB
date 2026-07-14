# STEP_08 — Smoke test

**No code changes.** Verification only. Document outcomes in
`docs/shaker-cleanups-noise/SMOKETEST_RESULTS.md`.

## 1. Stream A regression check

- Default pack (no edits) — all 17 layered effects still trigger as before in
  either MSFS or DCS, no log warnings.
- All three CLI selftests run:
  - `python -m telemffb.hw.ffb_shaker --selftest`
  - `python -m telemffb.hw.ffb_shaker --selftest-transient`
  - `python -m telemffb.hw.ffb_shaker --selftest-layered`
- Layer editor: open, edit a layer, save, reload. Test effect button works.
  Reset to default works.

## 2. Stream B integration check

- `python -m telemffb.hw.shaker_synth --selftest-noise` plays clean noise.
- In the layer editor, create a bandpass-noise layer for `je_rumble_1_1`
  (center ~ 30 Hz, bandwidth ~ 25 Hz, route shaker, gain 0.6). Save.
- Fly an aircraft with a jet engine briefly (sim of choice). Confirm the engine
  rumble feels noticeably "rougher" than before — without making any other
  changes.
- Revert the test edit (Reset effect to default for `je_rumble_1_1`).

## Acceptance

- `SMOKETEST_RESULTS.md` exists with both checks documented.
- No regressions in existing functionality.
- Bandpass noise demonstrably works end-to-end (synth → layer → UI → audible).
