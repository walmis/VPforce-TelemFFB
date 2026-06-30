# STEP_03 — SHAKER.md update + SMOKETEST_RESULTS

## Goal

Document the schema-v3 iteration in the central reference and capture
the mechanical smoke-test results.

## Files

### `docs/SHAKER.md` updates

- **§1 Stand-Zeile**: bump to current branch / latest commit, mention
  the envelope-override iteration alongside cleanups-noise.
- **§3.3 Audio-Synthese**: note that `Oscillator.trigger` defaults are
  `attack_ms=4.0, decay_ms=90.0` (the existing 3 ms claim is wrong).
- **§4.2 Layer dataclass**: add `attack_ms` / `decay_ms` Optional fields
  with the "ignored unless osc_type=impulse" comment; mention the
  fallthrough to Oscillator.trigger defaults.
- **§4.2 Dispatch**: extend the impulse branch description to mention
  the kwargs pass-through.
- **§6.1 UI**: bump the column count to 10; show Attack ms / Decay ms
  in the example table; describe the toggle behaviour analogously to
  the existing Center / Bandwidth wording.
- **§6.2 JSON**: bump example to schema v3; note that loader accepts
  v1, v2, v3.
- **§7 Code paths**: update `CURRENT_VERSION = 3`.
- **§8 Limitationen**: add a note about table width (10 cols may need
  horizontal scroll on small displays).
- **§9 Carry-overs**: add the v3 iteration as "erledigt"; note that
  `SHAKER_EFFECT_PROFILES` retirement is now technically possible
  (every profile field has a Layer counterpart).
- **§10 Erweiterungs-Hooks**: remove the schema-v3-envelope hint
  (done); add a "PROFILES → Layer migration" hint as the natural
  follow-up.
- **§11 Quick Reference**: no changes needed unless a new debug command
  emerges.

### `docs/shaker-envelope-override/SMOKETEST_RESULTS.md`

Mechanical checks (run in the sandbox; PortAudio fail expected for
audible portions):

1. AST parse the three modified files.
2. `Layer(osc_type="impulse", attack_ms=2.0, decay_ms=200.0)` round-trip
   through `asdict` / `Layer(**...)`.
3. `Layer()` defaults — verify `attack_ms` and `decay_ms` are both `None`.
4. `CURRENT_VERSION == 3`.
5. `load(get_default_pack_path())` returns ≥17 effects without warning
   (bundled pack stays v1).
6. Hand-craft a v2-shaped dict in memory, write it as JSON, load it,
   verify no warning is logged (loader accepts v1, v2, v3).
7. `python -m telemffb.hw.ffb_shaker --selftest-layered` reaches
   `synth.start()`.
8. `python -c "from telemffb.SystemSettingsDialog import ..."` AST shows
   `_make_layer_row_widgets` still exists.
9. `grep "QTableWidget(0,"` returns `(0, 10)`.
10. `git grep -nE '_oscillators|_lock'` outside `shaker_synth.py` is empty.

Manual checklist (Windows + shaker hardware):

- M.1 CLI selftests still play correctly (no audible regression).
- M.2 Layer editor: add an impulse layer to (e.g.) `gearclunk`,
  set attack=2 / decay=80, hit Test, verify the snap is sharper than
  with attack=4 / decay=90 defaults.
- M.3 Save → reload → round-trip the new fields.
- M.4 Reset effect to default — values revert.
- M.5 The bundled default pack still plays unchanged (its layers have
  None attack_ms/decay_ms → trigger defaults applied).

## Acceptance

- `docs/SHAKER.md` accurately reflects schema v3.
- `docs/shaker-envelope-override/SMOKETEST_RESULTS.md` exists with
  pass/fail for each mechanical check and the manual checklist.
