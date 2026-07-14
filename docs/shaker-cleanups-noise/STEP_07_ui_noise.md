# STEP_07 — UI: osc_type=bandpass_noise + Center/Bandwidth fields

## Goal

Layer editor accepts and displays bandpass-noise layers naturally. The
`_make_layer_row_widgets` helper (STEP_02) grows two optional cell widgets that
appear only for noise rows.

## Approach: Option A — extra columns

The QTable currently has 6 columns (`SystemSettingsDialog.py:419`):
`["#", "Freq ×", "Gain", "Route", "OscType", "Remove"]`.

Grow to 8 columns: append `Center Hz` and `Bandwidth Hz` (after Remove or
positioned per UX taste — implementation detail). For sine/impulse rows the
Center/Bandwidth cells are disabled and show "—". For `bandpass_noise` rows they
are enabled `QDoubleSpinBox` widgets.

**Rationale:** the editor is already a wide table; two more disabled cells for
the common case is less surprising than a popover.

## Behaviour

- `osc_type` ComboBox values grow to: `sine`, `impulse`, `bandpass_noise`.
- When osc_type changes to `bandpass_noise`:
  - Enable Center / Bandwidth spin boxes in that row
  - Populate defaults: `Center = round(freq_factor * 40, 1)`, `Bandwidth = 20.0`
- When osc_type changes away from `bandpass_noise`:
  - Disable the spin boxes visually
  - **Keep working-copy values** — don't lose them on accidental click
- Test-Effect button: if a row is `bandpass_noise`, instantiate
  `BandpassNoiseGenerator(synth.samplerate)`, call `.set(center, bandwidth, mag)`,
  inject via `synth.add_oscillator(...)` (STEP_03 API). Stop with
  `osc.stop(ramp_ms=100)` at end.

## Helper signature update

The helper from STEP_02 now populates `extras` for noise rows:

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
    },
}
```

Spin box ranges:
- **Center:** 5.0 .. 200.0, step 0.5, decimals 1, default 40.0
- **Bandwidth:** 1.0 .. 100.0, step 1.0, decimals 1, default 20.0

## Imports

In `_on_shaker_layer_test._run`, replace:

```python
from telemffb.hw.shaker_synth import ShakerSynth, Oscillator
```

with:

```python
from telemffb.hw.shaker_synth import ShakerSynth, Oscillator, BandpassNoiseGenerator
```

Branch on `layer.osc_type` per row:
- `"sine"` / `"impulse"` → `Oscillator(...)` (set/trigger as before)
- `"bandpass_noise"` → `BandpassNoiseGenerator(synth.samplerate)`, `.set(center, bw, amp)`

## Acceptance

- Open layer editor → pick any whitelisted effect → add a layer → switch its
  osc_type to `bandpass_noise`. Center and Bandwidth become editable.
- Change Center to 30, Bandwidth to 25, hit Test effect. Audible bandpass noise
  on the shaker.
- Save, reload from disk, verify the noise layer round-trips with its
  Center/Bandwidth values intact.
- Existing sine/impulse layers behave exactly as before — no regression in the
  editor (add/edit/remove/save/reload/test).

Stop and request review.
