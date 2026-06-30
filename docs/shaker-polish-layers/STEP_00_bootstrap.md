# STEP_00 — Bootstrap

## Goal

Create planning artifacts for the layered-routing iteration. **No code
changes.**

## Scope

Create the following files under `docs/shaker-polish-layers/`:

- `PLAN_LAYERS.md` — top-level checklist with status, phases, deferred notes.
- `STEP_00_bootstrap.md` — this document.
- `STEP_01_layer_runtime.md` — design of the `Layer` dataclass, the
  `EFFECT_LAYERS` dict, the dispatch logic in `HapticEffect.start()` /
  `.stop()`.
- `STEP_02_config_loading.md` — JSON file format, atomic save, reload-on-
  demand, integration into shaker-child startup.
- `STEP_03_layer_editor_ui.md` — System Settings tab layout, behaviour, save
  / reload / reset semantics.
- `STEP_04_default_layer_pack.md` — the bundled default JSON, the on-first-
  start copy logic, "reset to default" behaviour.
- `STEP_05_smoketest.md` — MSFS smoke-test checklist; will produce
  `MSFS_LAYER_TEST.md` and `KNOWN_ISSUES.md` in the same folder.

`KNOWN_ISSUES.md` is created later, in STEP_05, once smoke testing has
revealed anything worth recording.

## Codebase reality check

Done before writing the planning docs. Findings captured in
`PLAN_LAYERS.md` under "Codebase reality check". The most important ones
for the implementation steps:

- The synth has **one** `Oscillator` class, not separate sine/impulse
  classes. `osc_type="impulse"` in a layer selects the `trigger()` method;
  `osc_type="sine"` selects `set()`.
- `HapticEffect.frequency / .magnitude / .duration` are public attributes
  (no underscore), set by `.periodic()` and `.constant()`.
- `SHAKER_EFFECT_PROFILES` already exists. STEP_01 adds `EFFECT_LAYERS`
  alongside it; effects that have layers bypass profiles, effects without
  layers keep using profiles. No deletion in this iteration.
- Config files live in `G.userconfig_rootpath`. Use that for
  `shaker_effects.json`.

## Working agreement (echoed from the brief)

1. Read before writing — verified actual symbol names and line numbers
   instead of trusting brief-era assumptions.
2. Additive only on the shaker path. Stick is untouched.
3. One STEP at a time, with explicit human approval before advancing.
4. Update `PLAN_LAYERS.md` after each step (tick checkbox, append notes).
5. GPL v3 headers on new Python files (copy from existing files).
6. Logging over print. No `print()` in shipped code.
7. No silent scope creep — design changes go into the corresponding
   `STEP_NN_*.md` first, signed off, then implemented.

## Acceptance

- All seven files in `docs/shaker-polish-layers/` exist (six STEP docs +
  `PLAN_LAYERS.md`).
- `PLAN_LAYERS.md` checklist:
  - STEP_00 ticked.
  - STEP_01 .. STEP_05 unchecked.
  - "Codebase reality check" table populated.
- `KNOWN_ISSUES.md` does **not** exist yet (created in STEP_05).
- No source files modified. `git status` shows only new files under
  `docs/shaker-polish-layers/`.

## Stop here

After committing the planning artifacts, stop and request human review of
this STEP_00 batch before starting STEP_01.
