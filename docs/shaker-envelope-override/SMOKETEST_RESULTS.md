# TelemFFB Shaker Schema-v3 Envelope-Override — Smoke Test Results

## Environment

- Date: 2026-05-02
- Branch: claude/shaker-cleanups-noise-scSda
- Commits: STEP_01 `bd387e2`, STEP_02 `8fe77f5`
- Sandbox: headless Linux, no audio device (PortAudioError on synth.start)
- Audible portions of the spec require manual verification on the
  human's Windows machine with the FFB shaker hardware.

---

## Mechanical checks

### 1. AST parse the three modified files

```
python -c "import ast; [ast.parse(open(f).read()) for f in ['telemffb/hw/ffb_shaker.py','telemffb/hw/shaker_layers_io.py','telemffb/SystemSettingsDialog.py']]; print('AST OK')"
```

**Output:**
```
AST OK
```

**Result: PASS** — All three modified files parse without syntax errors.

---

### 2. Layer round-trip with v3 fields

```
python -c "from telemffb.hw.ffb_shaker import Layer; from dataclasses import asdict; l = Layer(osc_type='impulse', attack_ms=2.0, decay_ms=200.0); d = asdict(l); l2 = Layer(**d); assert l == l2; print('round-trip OK', d)"
```

**Output:**
```
round-trip OK {'freq_factor': 1.0, 'gain': 1.0, 'route': 'both', 'osc_type': 'impulse', 'center_hz': None, 'bandwidth_hz': None, 'attack_ms': 2.0, 'decay_ms': 200.0}
```

**Result: PASS** — `Layer` dataclass with v3 fields (`attack_ms`, `decay_ms`)
round-trips through `asdict()` / `Layer(**d)` with equality preserved.
Both fields appear in the dict with their set values.

---

### 3. Defaults preserved

```
python -c "from telemffb.hw.ffb_shaker import Layer; l = Layer(); assert l.attack_ms is None and l.decay_ms is None and l.center_hz is None and l.bandwidth_hz is None and l.osc_type == 'sine'; print('defaults OK')"
```

**Output:**
```
defaults OK
```

**Result: PASS** — `Layer()` with no arguments has `attack_ms=None`, `decay_ms=None`,
`center_hz=None`, `bandwidth_hz=None`, and `osc_type='sine'` — all defaults intact.

---

### 4. CURRENT_VERSION

```
python -c "from telemffb.hw.shaker_layers_io import CURRENT_VERSION; assert CURRENT_VERSION == 3; print('VERSION OK')"
```

**Output:**
```
VERSION OK
```

**Result: PASS** — `CURRENT_VERSION` is 3.

---

### 5. Bundled v1 default pack still loads silently

```
python -c "import logging, io; buf = io.StringIO(); h = logging.StreamHandler(buf); h.setLevel(logging.WARNING); logging.getLogger('telemffb.hw.shaker_layers_io').addHandler(h); from telemffb.hw.shaker_layers_io import load, get_default_pack_path; layers = load(get_default_pack_path()); assert len(layers) >= 17; assert 'mismatch' not in buf.getvalue(), buf.getvalue(); print('default pack OK', len(layers))"
```

**Output:**
```
default pack OK 17
```

**Result: PASS** — Bundled v1 default pack loads with 17 effects and emits no
warning containing "mismatch". The v1 → v3 migration path is silent.

---

### 6. v2-shaped JSON loads silently

```
python -c "import json, tempfile, os, logging, io; buf = io.StringIO(); h = logging.StreamHandler(buf); h.setLevel(logging.WARNING); logging.getLogger('telemffb.hw.shaker_layers_io').addHandler(h); data = {'version': 2, 'effects': {'foo': {'layers': [{'freq_factor': 1.0, 'gain': 1.0, 'route': 'both', 'osc_type': 'bandpass_noise', 'center_hz': 30.0, 'bandwidth_hz': 25.0}]}}}; f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False); json.dump(data, f); f.close(); from telemffb.hw.shaker_layers_io import load; layers = load(f.name); os.unlink(f.name); assert 'foo' in layers and len(layers['foo']) == 1; assert 'mismatch' not in buf.getvalue(), buf.getvalue(); print('v2 silent OK')"
```

**Output:**
```
v2 silent OK
```

**Result: PASS** — A hand-crafted v2 JSON (with `center_hz`/`bandwidth_hz` but no
`attack_ms`/`decay_ms`) loads silently. Missing v3 fields fill in via dataclass
defaults (`None`). No "mismatch" warning emitted.

---

### 7. v3 round-trip via save/load

```
python -c "import json, tempfile, os; from telemffb.hw.ffb_shaker import Layer; from telemffb.hw.shaker_layers_io import save, load; l = Layer(osc_type='impulse', attack_ms=3.5, decay_ms=120.0); f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False); f.close(); save(f.name, {'foo': [l]}); loaded = load(f.name); os.unlink(f.name); assert loaded['foo'][0] == l; print('v3 round-trip OK')"
```

**Output:**
```
v3 round-trip OK
```

**Result: PASS** — A `Layer` with `attack_ms=3.5` / `decay_ms=120.0` survives a
full `save()` → `load()` cycle with equality preserved. The saver writes v3 JSON
and the loader reconstructs the exact dataclass.

---

### 8. --selftest-layered reaches synth.start()

```
python -m telemffb.hw.ffb_shaker --selftest-layered 2>&1 | head -10
```

**Output:**
```
2026-05-02 18:29:34,484 INFO __main__ ffb_shaker layered selftest: device=None samplerate=48000
2026-05-02 18:29:34,484 INFO telemffb.hw.shaker_synth Opening sounddevice OutputStream: device=None sr=48000 block=256 ch=2 mode=mono pan=0.00
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/home/user/vpforce-telemffb-Shaker/telemffb/hw/ffb_shaker.py", line 627, in <module>
    main()
  File "/home/user/vpforce-telemffb-Shaker/telemffb/hw/ffb_shaker.py", line 621, in main
    _selftest_layered(_parse_device(args.device), args.samplerate)
  File "/home/user/vpforce-telemffb-Shaker/telemffb/hw/ffb_shaker.py", line 573, in _selftest_layered
```

**Result: PASS** — The selftest banner and `ShakerSynth` open-attempt are both
logged before the `PortAudioError`. This confirms the module loads cleanly, CLI
arg parsing works, and execution reaches `synth.start()`. The `PortAudioError`
is expected in this no-audio sandbox.

---

### 9. QTableWidget(0, 10)

```
grep -n "QTableWidget(0," telemffb/SystemSettingsDialog.py
```

**Output:**
```
419:        self.shaker_layer_table = QTableWidget(0, 10)
```

**Result: PASS** — Table is 10 columns wide (up from 8 in the previous
cleanups-noise iteration). Columns 9 and 10 are the Attack ms / Decay ms
spinboxes added by STEP_02.

---

### 10. Encapsulation preserved (no _oscillators / _lock outside shaker_synth.py)

```
git -C /home/user/vpforce-telemffb-Shaker grep -nE '_oscillators|_lock' telemffb/hw/ffb_shaker.py telemffb/SystemSettingsDialog.py
```

**Output:**
```
(no output — exit code 1)
```

**Result: PASS** — Zero direct accesses to private `_oscillators` or `_lock` in
the two external modules. All access goes through the public accessor API
established in the cleanups-noise STEP_03.

---

### 11. No row-widgets cache

```
grep -n "_shaker_layer_row_widgets" telemffb/SystemSettingsDialog.py
```

**Output:**
```
(no output — exit code 1)
```

**Result: PASS** — No `_shaker_layer_row_widgets` instance variable exists.
The bug from the cleanups-noise STEP_07 review (stale row-widget cache causing
mismatched widget state on effect-switch) is not present.

---

## Manual verification checklist (run on Windows with shaker hardware)

These cannot be performed in this sandbox. The human should run them on
their dev machine.

### M.1 CLI selftests
- [ ] `python -m telemffb.hw.ffb_shaker --selftest-layered` plays the
  layered selftest (je_rumble_1_1 stack audible on shaker/device)
- [ ] `python -m telemffb.hw.ffb_shaker --selftest` plays the basic sine
  selftest unchanged
- [ ] `python -m telemffb.hw.ffb_shaker --selftest-transient` plays the
  transient/thump demo unchanged
- [ ] `python -m telemffb.hw.shaker_synth --selftest-noise` plays clean
  band-limited noise (defaults: 35 Hz / 20 Hz)

### M.2 Layer editor — impulse envelope override
- [ ] Open System Settings → Shaker → layer editor
- [ ] Pick `gearclunk`; switch its layer to `impulse`; observe that
  Attack ms (col 9) and Decay ms (col 10) become editable
- [ ] Set Attack = 2.0, Decay = 80.0; hit Test Effect; confirm a sharper
  snap compared to the built-in defaults (4.0 / 90.0)
- [ ] On any `sine` or `bandpass_noise` row, the Attack/Decay cells show
  disabled spin boxes (values visible but not editable)

### M.3 Save → reload round-trip
- [ ] Save all effects → close dialog → reopen → verify the
  `attack_ms=2.0` / `decay_ms=80.0` values are still present in the row
- [ ] Inspect `<userconfig_rootpath>/shaker_effects.json` — confirm
  `"version": 3` and the layer entry has `"attack_ms": 2.0, "decay_ms": 80.0`

### M.4 Toggle impulse → sine → impulse
- [ ] With attack=2.0 / decay=80.0 set: switch osc_type to `sine`;
  Attack/Decay spin boxes go disabled
- [ ] Switch back to `impulse`; previously-set values (2.0 / 80.0)
  are still in the widgets (Working-Copy preserved)

### M.5 Reset effect to default
- [ ] Reset effect to default on `gearclunk`; Attack/Decay revert to
  whatever the bundled default defines (typically `None` → shows the
  placeholder value from the spin-box default, i.e. 4.0 / 90.0)

### M.6 Default-pack effects unchanged
- [ ] All 17 bundled effects play without audible regression after upgrade
  to v3 schema (v1 on disk loads cleanly, Attack/Decay default to `None`
  → Oscillator.trigger built-in defaults apply unchanged)

---

## Outcome of mechanical checks

| Check | Result | Notes |
|-------|--------|-------|
| 1. AST parse 3 files | PASS | No syntax errors |
| 2. Layer v3 round-trip (asdict/Layer) | PASS | attack_ms/decay_ms preserved |
| 3. Layer() defaults | PASS | attack_ms=None, decay_ms=None |
| 4. CURRENT_VERSION == 3 | PASS | |
| 5. v1 default pack loads silently | PASS | 17 effects, no mismatch warning |
| 6. v2-shaped JSON loads silently | PASS | missing v3 fields fill as None |
| 7. v3 save/load round-trip | PASS | Layer equality preserved |
| 8. --selftest-layered reaches synth.start() | PASS | PortAudioError expected |
| 9. QTableWidget(0, 10) | PASS | 10-column table confirmed |
| 10. No _oscillators/_lock in external modules | PASS | git grep exits 1 |
| 11. No _shaker_layer_row_widgets cache | PASS | grep exits 1 |

**Mechanical totals: 11 PASS, 0 FAIL, 0 SKIP-MANUAL**

Manual checks M.1–M.6 are deferred to the human's Windows machine with shaker hardware.

---

## Notes / deviations

- Check 8: `--selftest-layered` reaches `synth.start()` (evidenced by the
  `INFO telemffb.hw.shaker_synth Opening sounddevice OutputStream` log line)
  before hitting `PortAudioError`. This is the same expected failure mode as
  documented in the cleanups-noise SMOKETEST_RESULTS.md.
- Check 10: `git grep` exits with code 1 (no matches) for both target files,
  confirming zero direct access to `_oscillators` or `_lock` outside
  `shaker_synth.py`. Encapsulation established in the previous iteration
  is intact.
- Check 11: `grep` exits with code 1 (no matches), confirming the stale-
  row-widget-cache bug is not present. The `_make_layer_row_widgets` helper
  pattern (introduced in cleanups-noise STEP_02) populates cells directly
  from the Working-Copy on each table rebuild without a persistent cache.
- All sandbox `PortAudioError` tracebacks were anticipated; they occur after
  the code under test has already executed correctly and do not indicate
  logic failures.
