# STEP_00 — Bootstrap planning

## Goal

Create `docs/shaker-mvp/` and write all planning files. **No code changes outside this folder.**

## Files to create

- `docs/shaker-mvp/PLAN.md`
- `docs/shaker-mvp/ARCHITECTURE.md`
- `docs/shaker-mvp/STEP_00_bootstrap.md` (this file)
- `docs/shaker-mvp/STEP_01_synth_core.md`
- `docs/shaker-mvp/STEP_02_hapticeffect_facade.md`
- `docs/shaker-mvp/STEP_03_device_type.md`
- `docs/shaker-mvp/STEP_04_routing.md`
- `docs/shaker-mvp/STEP_05_soundcard_ui.md`
- `docs/shaker-mvp/STEP_06_msfs_smoketest.md`
- `docs/shaker-mvp/STEP_07_docs.md`

`MSFS_TEST_RESULTS.md` and `KNOWN_LIMITATIONS.md` are created later (STEP_06 and STEP_07).

## What goes in each file

- `PLAN.md` — checklist with the 8 phases (STEP_00..STEP_07), status legend, working-agreement summary, and a "Notes / Deferred" section that gets appended-to as work proceeds.
- `ARCHITECTURE.md` — one-page ASCII diagram showing master → ZeroMQ → shaker child → `ShakerSynth` → soundcard, plus three short sections explaining (1) why a 5th device type instead of dual-output in the joystick instance, (2) the HapticEffect facade pattern, (3) the effect-whitelist rationale.
- Each `STEP_NN_*.md` — copy of the corresponding section of the brief, expanded where useful with verified line numbers / file references from the actual repo.

## Acceptance

- All listed files exist with substantive content (not stubs).
- `PLAN.md` checkboxes are all unchecked except STEP_00 once this step is done.
- Stop and request human review.

## Deviations / observations during STEP_00

- Verified `Dispenser.get` at `telemffb/utils.py:1108` already assigns `v.name = name`. STEP_04 therefore does **not** need to "fix the Dispenser" — that contingency in the brief is mooted, and STEP_04 below is updated accordingly.
- Verified `aircraft_base.py:29-31` imports include `EFFECT_SAWTOOTHUP/DOWN`, `EFFECT_SPRING/DAMPER/INERTIA/FRICTION/SPRING_ADJUSTER`, `EFFECT_TRIANGLE`, `HapticEffect`, and `FFBReport_SetCondition`. The facade in STEP_02 must export the same set so STEP_03's conditional-import block can be a single drop-in replacement.
- Verified `main.py:147`, `:264`, and `:348` (Rhino `HapticEffect.open`) line numbers match the brief.
- Verified `globals.py:64` defines `device_type : str = ""` — this is the file that gets `shaker_synth: 'ShakerSynth | None' = None` added in STEP_03.
- Confirmed both `telemffb/SystemSettingsDialog.py` and `telemffb/ui/Ui_SystemDialog.py` exist; STEP_05 will need to inspect both before deciding where to add UI elements.
