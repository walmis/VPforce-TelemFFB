# STEP_02 — DRY layer-table row widget creation

## Goal

`_on_shaker_layer_add` (`telemffb/SystemSettingsDialog.py:639-691`) and
`_shaker_layer_rebuild_table` (`:557-610`) duplicate ~42 lines of cell-widget
creation for a layer row. Extract a private helper so future schema changes
touch one place.

## Procedure

1. Both methods build the same six cells:
   - Index (read-only `QTableWidgetItem`)
   - Freq × (`QDoubleSpinBox`, range 0.10–4.00, step 0.05, decimals 2)
   - Gain (`QDoubleSpinBox`, range 0.00–1.50, step 0.05, decimals 2)
   - Route (`QComboBox` with userData: `shaker / stick / both`)
   - OscType (`QComboBox` with userData: `sine / impulse`)
   - Remove ("−" `QPushButton`, width 28)

2. Extract into a private helper:

   ```python
   def _make_layer_row_widgets(self, row_index: int, layer: Layer) -> dict:
       """Build the cell widgets for one layer row.
       Returns a dict of {column_name: widget} for placement and signal wiring.
       """
   ```

3. Both call sites use the helper. `_on_shaker_layer_add` builds widgets for a
   fresh `Layer()`; `_shaker_layer_rebuild_table` iterates existing layers.

4. **Signal wiring stays at the call site.** The helper only constructs widgets; it
   does not connect signals (`_on_shaker_layer_cell_changed`,
   `_on_shaker_layer_remove`). This keeps the helper schema-pure — when osc_type
   gains a value in STEP_06, only the helper changes.

## Anticipating Stream B

The helper signature must accommodate `bandpass_noise` extras (Center / Bandwidth)
in STEP_07. Return a dict shaped:

```python
{
    "freq_factor": QDoubleSpinBox,
    "gain": QDoubleSpinBox,
    "route": QComboBox,
    "osc_type": QComboBox,
    "remove": QPushButton,
    "extras": {},  # populated by STEP_07 for bandpass_noise rows
}
```

For STEP_02, leave `extras` as an empty dict — STEP_07 fills it.

## Acceptance

- Both call sites visibly simplified; the duplicated ~40-line block is gone.
- Layer editor UI behaves identically — add row, edit cells, remove row, save,
  reload all work.
- No regression in working-copy semantics:
  - modified marker `●` still appears for edits (`_shaker_layer_refresh_combo_markers` :516-528)
  - unsaved-edits-on-effect-switch retention still holds (`_shaker_layer_working_copy` :469)

Stop and request review.
