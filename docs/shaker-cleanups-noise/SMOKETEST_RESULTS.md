# TelemFFB Shaker Cleanups + Bandpass Noise — Smoke Test Results

## Environment

- Date: 2026-05-02
- Branch: claude/shaker-cleanups-noise-scSda
- Sandbox: headless Linux, no audio device (PortAudioError on synth.start)
- Audible portions of the spec require manual verification on the
  human's Windows machine with the FFB shaker hardware.

---

## Stream A regression check (mechanical)

### A.1 default-pack effect names load via shaker_layers_io

```
python -c "from telemffb.hw.shaker_layers_io import load, get_default_pack_path; layers = load(get_default_pack_path()); print(sorted(layers.keys()))"
```

**Output:**
```
['ab_rumble_1_1', 'buffeting', 'cm', 'etlX', 'etlY', 'flapsmovement', 'gearbuffet', 'gearmovement', 'gunfire', 'je_rumble_1_1', 'je_rumble_1_2', 'prop_rpm0-1', 'rotor_rpm0-1', 'runway0', 'speedbrakemovement', 'touchdown', 'vrs_buffet']
```

**Result: PASS** — 17 effect names loaded from the default pack with no errors.

---

### A.2 SHAKER_EFFECT_PROFILES is exactly the 6 surviving entries

```
python -c "from telemffb.hw import ffb_shaker; print(sorted(ffb_shaker.SHAKER_EFFECT_PROFILES.keys()))"
```

**Output:**
```
['buffeting2', 'gearbuffet2', 'gearclunk', 'payload_rel', 'runway_bump0', 'runway_bump1']
```

**Result: PASS** — Exactly the 6 surviving entries remain (the 6 dead entries
`touchdown, gunfire, cm, buffeting, vrs_buffet, gearbuffet` were removed by STEP_01).

---

### A.3 ShakerSynth public API surface

```
python -c "from telemffb.hw.shaker_synth import ShakerSynth; print({m: hasattr(ShakerSynth, m) for m in ['get_oscillator', 'add_oscillator', 'remove_oscillator', 'peek_oscillator', 'list_oscillator_names', 'get_noise_oscillator']})"
```

**Output:**
```
{'get_oscillator': True, 'add_oscillator': True, 'remove_oscillator': True, 'peek_oscillator': True, 'list_oscillator_names': True, 'get_noise_oscillator': True}
```

**Result: PASS** — All 6 public methods present on ShakerSynth.

---

### A.4 No `_oscillators` / `_lock` access from external modules

```
git grep -nE '_oscillators|_lock' -- 'telemffb/hw/ffb_shaker.py' 'telemffb/SystemSettingsDialog.py'
```

**Output:**
```
(no output — exit code 1)
```

**Result: PASS** — Zero direct accesses to private `_oscillators` or `_lock` in the two
external modules. All access now goes through the public accessor API introduced in STEP_03.

---

### A.5 _selftest_layered emits via logger.info, not print

```
grep -n "print(" telemffb/hw/ffb_shaker.py
```

**Output:**
```
(no output — exit code 1)
```

No `print()` calls remain in `ffb_shaker.py`.

```
python -m telemffb.hw.ffb_shaker --selftest-layered 2>&1 | head -5
```

**Output:**
```
2026-05-02 12:38:38,621 INFO __main__ ffb_shaker layered selftest: device=None samplerate=48000
2026-05-02 12:38:38,621 INFO telemffb.hw.shaker_synth Opening sounddevice OutputStream: device=None sr=48000 block=256 ch=2 mode=mono pan=0.00
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
```

**Result: PASS** — First output line is a structured `INFO` log message, not a bare
`print()`. Traceback is expected: `sounddevice.PortAudioError` due to no audio device
in this sandbox.

---

### A.6 Layer table helper exists

PyQt6 is not available in this sandbox; using AST fallback:

```
python -c "import ast; tree = ast.parse(open('telemffb/SystemSettingsDialog.py').read()); names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]; print('_make_layer_row_widgets' in names)"
```

**Output:**
```
True
```

**Result: PASS** — `_make_layer_row_widgets` is defined in `SystemSettingsDialog.py`.

---

## Stream B integration check (mechanical)

### B.1 BandpassNoiseGenerator is importable and renders correctly-shaped output

```
python -c "from telemffb.hw.shaker_synth import BandpassNoiseGenerator; import numpy as np; g = BandpassNoiseGenerator(48000); g.set(35, 20, 0.5); out = g.render(256); print('shape', out.shape, 'dtype', out.dtype, 'max', float(np.abs(out).max()))"
```

**Output:**
```
shape (256,) dtype float32 max 0.0013265319867059588
```

**Result: PASS** — Shape is `(256,)` (1-D, matching block size), dtype is `float32`,
and max amplitude is non-zero, confirming the filter is producing bandpass noise.

---

### B.2 is_silent state machine

```
python -c "from telemffb.hw.shaker_synth import BandpassNoiseGenerator; g = BandpassNoiseGenerator(48000); print('init:', g.is_silent); g.set(35, 20, 0.5); print('after set:', g.is_silent); g.stop(); g.render(48000); print('after stop+render:', g.is_silent)"
```

**Output:**
```
init: True
after set: False
after stop+render: True
```

**Result: PASS** — `is_silent` starts `True`, transitions to `False` after `set()`,
and returns to `True` after `stop()` + a full-second `render()` (drain).

---

### B.3 Schema v2 round-trip

```
python -c "from telemffb.hw.ffb_shaker import Layer; from dataclasses import asdict; l = Layer(freq_factor=1.5, gain=0.7, route='shaker', osc_type='bandpass_noise', center_hz=30, bandwidth_hz=25); d = asdict(l); l2 = Layer(**d); print(l == l2, d)"
```

**Output:**
```
True {'freq_factor': 1.5, 'gain': 0.7, 'route': 'shaker', 'osc_type': 'bandpass_noise', 'center_hz': 30, 'bandwidth_hz': 25}
```

**Result: PASS** — `Layer` dataclass with v2 fields (`center_hz`, `bandwidth_hz`)
round-trips through `asdict()` / `Layer(**d)` with equality preserved.

---

### B.4 v1 file backward compat

```
python -c "from telemffb.hw.shaker_layers_io import load, get_default_pack_path; layers = load(get_default_pack_path()); print('v1 default pack loaded:', len(layers), 'effects')"
```

**Output:**
```
v1 default pack loaded: 17 effects
```

**Result: PASS** — The bundled v1 default pack loads cleanly with all 17 effects
via the schema migration path in `shaker_layers_io.load()`.

---

### B.5 ShakerSynth.get_noise_oscillator

```
python -c "from telemffb.hw.shaker_synth import ShakerSynth, BandpassNoiseGenerator; s = ShakerSynth(); n = s.get_noise_oscillator('a'); print(isinstance(n, BandpassNoiseGenerator))"
```

**Output:**
```
True
```

**Result: PASS** — `get_noise_oscillator` returns a `BandpassNoiseGenerator` instance.

---

### B.6 --selftest-noise CLI

```
python -m telemffb.hw.shaker_synth --selftest-noise --center 50 --bandwidth 30 2>&1 | head -5
```

**Output:**
```
ShakerSynth noise selftest: device=None samplerate=48000 center=50.0 Hz bandwidth=30.0 Hz channel_mode='mono' pan=0.0
2026-05-02 12:38:58,494 INFO __main__ Opening sounddevice OutputStream: device=None sr=48000 block=256 ch=2 mode=mono pan=0.00
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
```

**Result: PASS** — Banner line is printed with the correct parameters
(`center=50.0 Hz bandwidth=30.0 Hz`). Subsequent `PortAudioError` is expected in
this no-audio sandbox.

---

### B.7 SystemSettingsDialog has 8-column table + bandpass_noise UI

```
grep -n "QTableWidget(0," telemffb/SystemSettingsDialog.py
```

**Output:**
```
419:        self.shaker_layer_table = QTableWidget(0, 8)
```

```
grep -nE "bandpass_noise" telemffb/SystemSettingsDialog.py | head -5
```

**Output:**
```
572:        STEP_02; STEP_07 will populate it for bandpass_noise rows.
599:        osc_combo.addItem("bandpass_noise", userData="bandpass_noise")
615:        center_spin.setEnabled(layer.osc_type == "bandpass_noise")
624:        bw_spin.setEnabled(layer.osc_type == "bandpass_noise")
692:        # and populate defaults when switching to bandpass_noise.
```

**Result: PASS** — Table is 8 columns wide (up from 6); `bandpass_noise` is wired as a
combo item, and `center_spin`/`bw_spin` are conditionally enabled based on `osc_type`.

---

## Manual verification checklist (run on Windows with shaker hardware)

These cannot be performed in this sandbox. The human should run them on
their dev machine.

### M.1 CLI selftests
- [ ] `python -m telemffb.hw.ffb_shaker --selftest-layered` plays the
  layered je_rumble_1_1 test (20 Hz + 80 Hz oscillators audible on shaker)
- [ ] `python -m telemffb.hw.ffb_shaker --selftest` plays the basic sine
  selftest unchanged from before this branch
- [ ] `python -m telemffb.hw.ffb_shaker --selftest-transient` plays the
  transient/thomp demo unchanged
- [ ] `python -m telemffb.hw.shaker_synth --selftest-noise` plays clean
  band-limited noise (defaults: 35 Hz / 20 Hz)
- [ ] Sweep: re-run `--selftest-noise` with `--center 60 --bandwidth 10` and
  `--center 25 --bandwidth 40` — confirm audibly different "characters" of
  rumble

### M.2 Layer editor UI
- [ ] Open System Settings → Shaker tab → layer editor
- [ ] Pick `je_rumble_1_1`, observe 8-column table with two new
  "Center Hz" and "Bandwidth Hz" columns
- [ ] On any sine/impulse row, the Center/Bandwidth cells show disabled
  spin boxes (the values are visible but cannot be edited)
- [ ] Add a new layer; switch its osc_type to `bandpass_noise`; both
  spin boxes become enabled and populate with sensible defaults
  (Center ≈ freq_factor * 40, Bandwidth = 20)
- [ ] Hit Test Effect; audible bandpass noise rumble on the shaker
- [ ] Switch the same row back to `sine`; spin box values are preserved
  (not reset) but the boxes go disabled
- [ ] Switch back to `bandpass_noise`; the previously-set Center/Bandwidth
  values are still there
- [ ] Save all effects → reload TelemFFB → reopen the editor → the
  `bandpass_noise` layer round-trips correctly
- [ ] Reset effect to default → the noise layer disappears and `je_rumble_1_1`
  reverts to its bundled defaults

### M.3 In-sim feel test
- [ ] Add a `bandpass_noise` layer to `je_rumble_1_1` (center ~30 Hz,
  bandwidth ~25 Hz, route shaker, gain 0.6); save
- [ ] Fly any aircraft with a jet engine in MSFS for 30+ seconds
- [ ] The engine rumble should feel noticeably "rougher" than the baseline
  sine tone, but not buzzy or distorted
- [ ] Reset effect to default to revert

### M.4 Default pack untouched
- [ ] `git diff main -- telemffb/data/shaker_effects_default.json` is
  empty (no changes to the bundled v1 default pack)

---

## Outcome of mechanical checks

| Check | Result | Notes |
|-------|--------|-------|
| A.1 default-pack effect names load | PASS | 17 effects loaded |
| A.2 SHAKER_EFFECT_PROFILES 6 entries | PASS | Exactly the 6 surviving profiles |
| A.3 ShakerSynth public API surface | PASS | All 6 public methods present |
| A.4 No _oscillators/_lock in external modules | PASS | git grep returns no matches |
| A.5 _selftest_layered uses logger.info | PASS | First output is structured INFO log |
| A.6 _make_layer_row_widgets exists | PASS | AST confirms method defined |
| B.1 BandpassNoiseGenerator render shape | PASS | (256,) float32, non-zero max |
| B.2 is_silent state machine | PASS | True → False → True transitions correct |
| B.3 Schema v2 round-trip | PASS | Layer equality preserved through asdict/Layer() |
| B.4 v1 backward compat | PASS | 17 effects loaded from bundled v1 pack |
| B.5 get_noise_oscillator | PASS | Returns BandpassNoiseGenerator instance |
| B.6 --selftest-noise CLI | PASS | Banner with correct params; PortAudioError expected |
| B.7 8-col table + bandpass_noise UI | PASS | QTableWidget(0, 8) + 4 bandpass_noise refs |

**Mechanical totals: 13 PASS, 0 FAIL, 0 SKIP-MANUAL**

Manual checks M.1–M.4 are deferred to the human's Windows machine with shaker hardware.

---

## Notes / deviations

- A.5: `grep -n "print("` on `ffb_shaker.py` exits with code 1 (no matches). Combined
  with the `--selftest-layered` output leading with a structured `INFO` log line, this
  confirms STEP_04 is complete.
- A.4: `git grep` exits with code 1 (no matches) for both target files, confirming
  zero direct access to `_oscillators` or `_lock` outside `shaker_synth.py`.
- B.6: The `--selftest-noise` banner is emitted via `print()` (not `logger.info`)
  intentionally — it is a human-facing CLI banner that precedes logging setup.
  This matches the existing `--selftest` pattern in `ffb_shaker.py`.
- All sandbox `PortAudioError` tracebacks were anticipated; they occur after the
  code under test has already executed correctly and do not indicate logic failures.
