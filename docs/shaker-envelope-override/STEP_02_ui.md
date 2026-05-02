# STEP_02 — UI: Attack ms / Decay ms columns on impulse rows

## Goal

Mirror the STEP_07 noise-UI pattern from the previous iteration: grow
the layer-editor table with two additional columns, enabled only when
`osc_type == "impulse"`, with values preserved across toggles.

## QTable layout

Currently 8 columns (after STEP_07 of the cleanups-noise iteration):

```
["#", "Freq ×", "Gain", "Route", "OscType", "Remove", "Center Hz", "Bandwidth Hz"]
```

Grow to 10 columns:

```
["#", "Freq ×", "Gain", "Route", "OscType", "Remove", "Center Hz", "Bandwidth Hz", "Attack ms", "Decay ms"]
```

## Helper signature

`_make_layer_row_widgets` returns a dict whose `extras` field grows:

```python
{
    "freq_factor": ...,
    "gain": ...,
    "route": ...,
    "osc_type": ...,
    "remove": ...,
    "extras": {
        "center_hz": QDoubleSpinBox,
        "bandwidth_hz": QDoubleSpinBox,
        "attack_ms": QDoubleSpinBox,
        "decay_ms": QDoubleSpinBox,
    },
}
```

Spin box ranges:

- Attack: 0.1–50.0, step 0.5, decimals 1, default 4.0 (matches
  `Oscillator.trigger`'s built-in default).
- Decay: 5.0–500.0, step 5.0, decimals 1, default 90.0 (matches
  `Oscillator.trigger`'s built-in default).

Initial values: `layer.attack_ms` if not None else 4.0 (clamped);
`layer.decay_ms` if not None else 90.0 (clamped).

Initial enabled state: `setEnabled(layer.osc_type == "impulse")`.

## Toggle behaviour

In `_on_shaker_layer_cell_changed`, the existing per-row loop already
handles the noise toggle. Extend to also toggle the impulse fields:

```python
for r in range(table.rowCount()):
    osc_combo    = table.cellWidget(r, 4)
    center_spin  = table.cellWidget(r, 6)
    bw_spin      = table.cellWidget(r, 7)
    attack_spin  = table.cellWidget(r, 8)
    decay_spin   = table.cellWidget(r, 9)
    if any(w is None for w in (osc_combo, center_spin, bw_spin, attack_spin, decay_spin)):
        continue
    osc_type = osc_combo.currentData()
    is_noise   = (osc_type == "bandpass_noise")
    is_impulse = (osc_type == "impulse")
    # noise default population (existing) ...
    # impulse default population (new): only when transitioning into impulse
    if is_impulse and not attack_spin.isEnabled():
        attack_spin.blockSignals(True);  attack_spin.setValue(4.0); attack_spin.blockSignals(False)
        decay_spin.blockSignals(True);   decay_spin.setValue(90.0); decay_spin.blockSignals(False)
    center_spin.setEnabled(is_noise)
    bw_spin.setEnabled(is_noise)
    attack_spin.setEnabled(is_impulse)
    decay_spin.setEnabled(is_impulse)
```

## Working-copy persistence

`_shaker_layer_read_table_rows` reads cells 8/9 and writes
`attack_ms` / `decay_ms` into the constructed `Layer(...)`. Mirror the
noise approach: write the spin-box values for every row regardless of
osc_type so toggle-back-into-impulse preserves the user's tuning.

```python
attack_spin = table.cellWidget(r, 8)
decay_spin  = table.cellWidget(r, 9)
attack_ms = attack_spin.value() if attack_spin else None
decay_ms  = decay_spin.value()  if decay_spin  else None
```

## Test worker

STEP_01 already updated the test worker. STEP_02 only needs to ensure
the spin-box values reach the layer through `_shaker_layer_read_table_rows`
— no extra changes here.

## Verification

- AST parse `telemffb/SystemSettingsDialog.py`.
- `grep "QTableWidget(0,"` shows `(0, 10)`.
- `grep "Attack ms\|Decay ms"` finds the column headers and the spin-box
  defaults.
- `git grep -nE '_oscillators|_lock' telemffb/SystemSettingsDialog.py`
  remains empty.

## Acceptance

- Open layer editor → any whitelisted effect → add a layer with
  osc_type=impulse. Attack/Decay become editable, populated with 4.0 / 90.0.
- Edit values; hit Test Effect — audible difference vs. the defaults.
- Toggle osc_type back to sine; Attack/Decay disable but values persist.
- Save → reload → Attack/Decay values round-trip correctly.
- A pre-existing impulse layer (e.g. `touchdown__layer0` from the bundled
  default pack) loads with `attack_ms=None, decay_ms=None` and the UI
  shows the sensible defaults (no breakage).

## Constraints

- Edit ONLY `telemffb/SystemSettingsDialog.py`.
- Mirror the structure of the existing noise toggle exactly — minimum
  divergence to keep the helper schema-pure for future fields.
