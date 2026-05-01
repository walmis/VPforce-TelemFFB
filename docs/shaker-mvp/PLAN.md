# TelemFFB Shaker MVP — Plan

## Status legend
- [ ] not started
- [~] in progress
- [x] done
- [!] blocked / needs design change

## Phases
- [x] STEP_00 — Bootstrap planning artifacts
- [x] STEP_01 — Audio synth core (`telemffb/hw/shaker_synth.py`)
- [x] STEP_02 — HapticEffect facade (`telemffb/hw/ffb_shaker.py`)
- [ ] STEP_03 — Device-type integration (`--type shaker` launchable)
- [ ] STEP_04 — Effect routing whitelist
- [ ] STEP_05 — Soundcard selection UI in System Settings
- [ ] STEP_06 — MSFS smoke test
- [ ] STEP_07 — Docs & known limitations

## Working agreement (short form)
- Implement one STEP, satisfy its acceptance criteria, **stop and request human review**, await explicit approval before starting the next STEP.
- All changes must be additive — joystick / pedals / collective / trimwheel paths must keep working unchanged when `G.device_type != 'shaker'`.
- New files get the GPL v3 header copied verbatim from existing `telemffb/hw/ffb_rhino.py`.
- New runtime dependency: `sounddevice` only (update `requirements.txt` in STEP_01).
- Use `logging` (no `print()`) in shipped code; the standalone selftest may print.
- Tick the relevant checkbox above and append a one-line note under "Notes / Deferred" after each step.
- If a STEP's design proves wrong during implementation, update the corresponding `STEP_NN_*.md` first, surface the change in the reply, ask for human signoff, and only then implement.

## Reference notes from initial code inspection (verified against tree)
- `main.py:147`  →  `mapping = {1: "joystick", 2: "pedals", 3: "collective", 4: "trimwheel"}` — extend with `5: "shaker"`.
- `main.py:264`  →  `index_dict = { ... }` — extend with `'shaker': 5`.
- `main.py:348`  →  `dev = HapticEffect.open(vid_pid[0], vid_pid[1])` — wrap so shaker branch initialises `ShakerSynth` instead of opening Rhino HID.
- `telemffb/CmdLineArgs.py:118`  →  `--type` help text — add `shaker`.
- `telemffb/sim/aircraft_base.py:29-31`  →  `from telemffb.hw.ffb_rhino import ...` — replace with conditional binding on `G.device_type`.
- `telemffb/sim/aircraft_base.py:38`  →  `effects: utils.Dispenser = utils.Dispenser(HapticEffect)` — same import switches HapticEffect to the shaker facade when device is shaker.
- `telemffb/sim/aircraft_base.py:491,499,507,515`  →  `is_joystick`, `is_pedals`, `is_collective`, `is_trimwheel` — add `is_shaker` near these.
- `telemffb/hw/ffb_rhino.py:996`  →  `class HapticEffect(Destroyable):` — surface to mirror in `ffb_shaker.py`.
- `telemffb/utils.py:1099-1110`  →  `class Dispenser` already sets `v.name = name` on creation; STEP_04 can rely on that without modifying Dispenser.
- `telemffb/globals.py:64`  →  `device_type : str = ""` — add `shaker_synth: 'ShakerSynth | None' = None` near here in STEP_03.
- `telemffb/SystemSettingsDialog.py` (and `telemffb/ui/Ui_SystemDialog.py`) — Qt-Designer-generated UI plus wrapper. The Shaker section in STEP_05 must be added in both, or in the wrapper alone if widgets are added programmatically.

## Notes / Deferred
- (STEP_00) Confirmed `Dispenser.get` at `telemffb/utils.py:1108` already assigns `v.name = name` — STEP_04 will not need a Dispenser fix; just rely on the existing behaviour.
- (STEP_00) `aircraft_base.py:30` also imports `EFFECT_SPRING/DAMPER/INERTIA/FRICTION/SPRING_ADJUSTER`; line 31 imports `EFFECT_SAWTOOTHUP/DOWN`. The conditional import block in STEP_03 must re-export ALL of those symbols from `ffb_shaker.py` (already listed in the STEP_02 surface).
- (STEP_00) `main.py:348` is the Rhino HID open; the shaker branch in STEP_03 wraps **this** call site (not a higher-level one).
- (STEP_01) `shaker_synth.py` preallocates **all** working buffers (mix, per-oscillator output, indices, phase, sine, amplitude envelope) at construction; `_ensure_capacity` only fires if a render call requests more samples than the configured blocksize. Audio callback path holds a single `threading.Lock` for the whole iteration — set/stop calls are infrequent vs. the audio thread, so contention is negligible.
- (STEP_01) `requirements.txt` now lists `sounddevice`. CLI verified: `--help`, `--list-devices` (returns empty list cleanly when host has no audio devices). Oscillator math sanity-checked against expected sine values for a 30 Hz / 480-sample render — peaks land where phase analysis predicts.
- (STEP_02) Whitelist filtering is **deferred to STEP_04 by design** (per the STEP doc). STEP_02's `start()` calls into the synth unconditionally — STEP_04 will add the `if self.name not in SHAKER_EFFECT_WHITELIST: return self` guard.
- (STEP_02) `start()` and `stop()` consolidate the lookup-or-create + osc.set/stop into a single `_synth._lock` acquisition, avoiding a tiny window where the audio thread could observe partially-updated oscillator state.
- (STEP_02) Verified all 14 `EFFECT_*` constants in `ffb_shaker.py` match `ffb_rhino.py` value-for-value (parsed regex out of the source so the test doesn't require libusb). Behavioural smoke test exercises `.periodic`, `.constant`, `.start`, `.stop`, `.destroy`, `.started`, `duration` timer, force-only no-ops, and the no-synth-bound graceful drop.
