# TelemFFB Shaker — Cleanups + Bandpass Noise

## Status legend
- [ ] not started
- [~] in progress
- [x] done
- [!] blocked / needs design change

## Phases

### Stream A — Cleanups (independent, can be reviewed/merged separately)
- [x] STEP_00 — Bootstrap planning
- [x] STEP_01 — Remove dead SHAKER_EFFECT_PROFILES entries
- [ ] STEP_02 — DRY layer table row widget creation
- [ ] STEP_03 — Public ShakerSynth.add_oscillator API
- [ ] STEP_04 — Logging instead of print in _selftest_layered

### Stream B — Bandpass noise (depends on Stream A.3 only)
- [ ] STEP_05 — BandpassNoiseGenerator in shaker_synth.py
- [ ] STEP_06 — Schema v2: layer fields + migration in shaker_layers_io.py
- [ ] STEP_07 — UI: osc_type=bandpass_noise + center/bandwidth fields
- [ ] STEP_08 — Smoke test: noise audible via UI test button

## Working agreement (recap)

1. Read before writing — verify exact symbol names and locations via `grep`; the line
   numbers in `docs/SHAKER.md` are approximate.
2. One STEP at a time. Each STEP has acceptance criteria — stop and request human
   review before proceeding.
3. Strictly additive on the audible side. No existing layer or profile changes
   observable behaviour. Stream A removes dead code paths only; Stream B adds new
   generator types without touching existing ones.
4. Schema migration is one-way (v1 → v2). v1 files keep loading.
5. Update this file after every step (tick the box, append notes for deviations).
6. GPL v3 headers on all new files (none planned — only existing files are edited).
7. Logging over print.
8. No silent scope creep — if a STEP_NN file proves wrong, edit it first, surface
   the change, get signoff, then implement.

## Branch

All work lands on `claude/shaker-cleanups-noise-scSda`. One commit per step.
Stream A (steps 01-04) may be batched into a single PR/review at the user's
discretion; Stream B (steps 05-08) stays sequential.

## Notes / Deferred

(append decisions, deviations here)

- **Logger name:** `ffb_shaker.py` line 43 already defines `logger = logging.getLogger(__name__)`.
  Use the existing name `logger`, not `log` as the brief suggests.
- **`remove_oscillator` already exists** on `ShakerSynth` (line 301). Only the matching
  `add_oscillator` is missing — STEP_03 adds just that.
- **Dead-profile intersection: 6 entries** (`touchdown, gunfire, cm, buffeting, vrs_buffet,
  gearbuffet`). Surviving 6 entries (`gearclunk, runway_bump0, runway_bump1, payload_rel,
  buffeting2, gearbuffet2`) get the explanatory comment block.
- **Layer dataclass lives in `ffb_shaker.py`** (re-imported by `shaker_layers_io.py`),
  so the v2 schema fields are added in `ffb_shaker.py`.
- **Existing osc_type dispatch is currently a single if/else** at `ffb_shaker.py:406-409`;
  STEP_06 expands this into an explicit 3-way branch with a fallthrough warning.
- **No `get_impulse_oscillator` exists** — `Oscillator` already handles impulses via
  `_env_active`. STEP_06 adds only `get_noise_oscillator`.
