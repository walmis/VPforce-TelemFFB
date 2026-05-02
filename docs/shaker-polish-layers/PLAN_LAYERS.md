# TelemFFB Shaker Polish — Frequency-Band Layered Routing

## Mission (one-liner)

Each whitelisted shaker effect can be defined as a stack of **layers**. Each
layer carries its own `freq_factor`, `gain`, `route` (`shaker` / `stick` /
`both`) and `osc_type` (`sine` / `impulse`). The shaker child instance plays
only the layers tagged for it. The stick (Rhino) code is unmodified in this
iteration; for shaker-relevant effects it will appear "louder" on its band
because the shaker no longer doubles up.

Why: in the user's setup the Rhino is mounted to the chair and already
delivers low-frequency energy through the chair frame. The bass shaker should
complement (different band, different texture), not duplicate.

## Status legend

- [ ] not started
- [~] in progress
- [x] done
- [!] blocked / needs design change

## Phases

- [x] STEP_00 — Bootstrap planning artifacts
- [x] STEP_01 — Layer runtime (Layer dataclass, dispatch in HapticEffect)
- [x] STEP_02 — Config loading & file management
- [x] STEP_03 — Layer editor UI in System Settings
- [ ] STEP_04 — Ship default layer pack
- [ ] STEP_05 — Smoke test in MSFS

## Codebase reality check (gathered before STEP_00)

The brief was written against an earlier mental model of the code. After
re-reading the current shaker pipeline the following adjustments matter for
later steps — flag them once, then use the actual symbol names from here on.

| Brief assumption | Actual current code |
|------------------|---------------------|
| Separate `ImpulseOscillator` class with its own `get_impulse_oscillator()` | Single `Oscillator` class with two methods: `set(freq, amp, ramp_ms)` for continuous, `trigger(freq, amp, attack_ms, decay_ms)` for transients (`telemffb/hw/shaker_synth.py:45-208`). `osc_type="impulse"` in a layer simply selects the `trigger()` path. |
| `HapticEffect._frequency`, `._magnitude`, `._duration` (underscore-prefixed private) | `self.frequency`, `self.magnitude`, `self.duration` — no underscores. Set by `.periodic()` and `.constant()` (`telemffb/hw/ffb_shaker.py:218-256`). |
| Multi-layer support already exists for some effects | Only `SHAKER_EFFECT_PROFILES` exists — a flat per-effect tuning dict (kind/freq/gain/attack_ms/decay_ms/ramp_ms). Single oscillator per effect. STEP_01 adds true multi-layer. |
| Find config dir via `shakerDevice` persistence | Config dir is `G.userconfig_rootpath` (set in `main.py:_setup_config_paths()` around line 316). On Windows production this is `%LOCALAPPDATA%/VPForce-TelemFFB`; in dev mode it's the repo root. `shaker_effects.json` lives next to `userconfig_v2.xml`. |

## How `EFFECT_LAYERS`, `SHAKER_EFFECT_PROFILES`, and the legacy whitelist coexist

After STEP_01:

1. Effect not in `SHAKER_EFFECT_WHITELIST` → dropped at `start()` (unchanged).
2. Effect in whitelist with an `EFFECT_LAYERS` entry → dispatched across
   layers; `SHAKER_EFFECT_PROFILES` is **not** consulted for that effect
   (layers carry their own gain / freq factors).
3. Effect in whitelist with a `SHAKER_EFFECT_PROFILES` entry but no
   `EFFECT_LAYERS` entry → existing single-oscillator path with the profile's
   tuning (unchanged).
4. Effect in whitelist with neither → implicit `DEFAULT_LAYER` single sine
   oscillator at the call-site frequency / magnitude (preserves all current
   non-profile behaviour).

This preserves every existing code path; layers are strictly additive.

## Notes / Deferred

(append decisions, deviations, blockers here as steps progress)

- _2026-05-02_: STEP_00 complete. Plan files match the actual current code
  (Oscillator.trigger() path, real attribute names on HapticEffect, real
  config-dir resolver). No code touched.
- _2026-05-02_: STEP_01 complete. `Layer` dataclass, `DEFAULT_LAYER`,
  `_layer_is_for_shaker`, `EFFECT_LAYERS` (hardcoded test dict), `_start_layered`,
  `_stop_layer_names`, and layered dispatch in `start()`/`stop()` added to
  `ffb_shaker.py`. `shaker_synth.py` required no changes (confirmed: single
  `Oscillator` with `set()`/`trigger()` handles both osc_type paths). The
  `--selftest-layered` CLI entry point lives in `ffb_shaker.py` under
  `python -m telemffb.hw.ffb_shaker --selftest-layered`; it requires an audio
  device to actually play sound but the `--help` / argparse path works without
  one. Smoke test confirmed all six acceptance criteria pass without audio hardware.
  `touchdown` is present in both `SHAKER_EFFECT_PROFILES` and `EFFECT_LAYERS`;
  because the EFFECT_LAYERS check comes first in `start()`, the layer path wins —
  this is intentional per the spec ("enters EFFECT_LAYERS → opts out of legacy
  profile"). STEP_02 implementer should remove `touchdown` and `gunfire` from
  `SHAKER_EFFECT_PROFILES` when the JSON-loaded layer pack ships, to keep the
  dicts consistent.
- _2026-05-02_: STEP_02 complete. New `telemffb/hw/shaker_layers_io.py` with
  `load()`, `save()`, `get_shaker_effects_path()`, and `CURRENT_VERSION = 1`.
  `ffb_shaker.py`: hardcoded STEP_01 test dict replaced with empty `EFFECT_LAYERS`,
  `_BUILTIN_DEFAULT_LAYERS` stub added, `reload_layers()` function wires the two
  together. Two layer-awareness gaps from STEP_01 independent review fixed:
  `HapticEffect.destroy()` now iterates `__layerN` oscillators for layered effects
  instead of calling `remove_oscillator(self.name)` on the plain name; `started`
  property checks all shaker-routed layer oscillators instead of the plain-name
  oscillator (which never exists for layered effects). `main.py` calls
  `reload_layers()` after `aircraft_base.use_shaker_backend()`. No surprises:
  `G.userconfig_rootpath` confirmed at `telemffb/globals.py:55`; the shaker
  branch in `main.py` was exactly where expected (line 380).
- _2026-05-02_: STEP_03 complete. New `_setup_shaker_layers_section()` method
  added to `SystemSettingsDialog`, called from `_setup_shaker_tab()` before
  `outer.addStretch(1)`. All required widgets created: effect dropdown
  (`shaker_layer_effect_combo`), layer table (`shaker_layer_table`, 6 cols),
  per-effect buttons (`shaker_layer_add_btn`, `shaker_layer_reset_btn`,
  `shaker_layer_test_btn`) and bottom buttons (`shaker_layer_save_btn`,
  `shaker_layer_reload_btn`, `shaker_layer_reset_all_btn`). State model:
  `_shaker_layer_saved_state` (from disk), `_shaker_layer_working_copy` (dict
  mutated by edits). Layout decisions: separator QFrame + bold "Effect layers"
  label to visually delimit the section; table uses ResizeToContents for index/
  freq/gain/remove columns and Stretch for route/osctype to use available width.
  Test effect plays each layer at 40 Hz * freq_factor for ~2 s on a short-lived
  ShakerSynth (same worker-thread pattern as the soundcard test button). Save
  merges working copy over the on-disk state to preserve effects the user never
  opened in this session. STEP_04 note: `_BUILTIN_DEFAULT_LAYERS` is currently
  empty; "Reset effect to default" and "Reset all effects to defaults" both
  fall back to a single `Layer()` row until STEP_04 populates that dict — no
  code change needed in STEP_04 beyond filling `_BUILTIN_DEFAULT_LAYERS`.
  PyQt6 not available in the CI environment, so static smoke verified via AST
  parse + direct module import (without the Qt event loop).
