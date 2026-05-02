# STEP_03 — Layer editor UI in System Settings

## Goal

Let the user edit layer specs for any whitelisted effect from the System
Settings dialog. Adding, removing, and reordering layers (per row), saving
to disk, reloading from disk, resetting an individual effect to the
bundled default, and running a quick audible test of the unsaved spec.

The host is the existing `SystemSettingsDialog` (`telemffb/SystemSettings\
Dialog.py`). The "Shaker" tab is added programmatically there
(`_setup_shaker_tab`, around line 284). The new section sits inside that
same tab, below the existing soundcard / gain / channel-mode / pan
controls.

## Layout

```
┌─ Shaker tab (existing) ─────────────────────────────────────┐
│  Output device:    [...]                                    │
│  Master gain:      [...]                                    │
│  Output channel:   [...]                                    │
│  Pan:              [...]                                    │
│  [Test]                                                     │
│                                                             │
│  ── Effect layers ─────────────────────────────────────     │
│                                                             │
│  Effect:  [ je_rumble_1_1 ▼ ]   ●  (modified marker)        │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ #  Freq ×   Gain    Route        OscType    Remove   │   │
│  │ 0   0.50    0.80    [shaker ▼]   [sine  ▼]   [-]     │   │
│  │ 1   1.00    0.60    [stick  ▼]   [sine  ▼]   [-]     │   │
│  │ 2   2.00    0.40    [shaker ▼]   [sine  ▼]   [-]     │   │
│  └──────────────────────────────────────────────────────┘   │
│  [+ Add layer]   [Reset effect to default]   [Test effect]  │
│                                                             │
│  [Save all effects]   [Reload from disk]                    │
│  [Reset all effects to defaults]                            │
└─────────────────────────────────────────────────────────────┘
```

## Widget choices

- Effect dropdown: `QComboBox`, populated from
  `sorted(SHAKER_EFFECT_WHITELIST)`. Shows a leading `●` for entries
  whose working copy diverges from the saved-to-disk state.
- Layer table: `QTableWidget` (5 + 1 columns: index header, freq×, gain,
  route combo, osctype combo, remove button). Cell widgets are real
  controls, not text:
  - Freq ×: `QDoubleSpinBox`, range `0.10 .. 4.00`, step `0.05`,
    decimals `2`.
  - Gain: `QDoubleSpinBox`, range `0.00 .. 1.50`, step `0.05`,
    decimals `2`.
  - Route: `QComboBox` with userData `"shaker" | "stick" | "both"`.
  - OscType: `QComboBox` with userData `"sine" | "impulse"`.
  - Remove: `QPushButton("−")` per row.
- Buttons: `+ Add layer`, `Reset effect to default`, `Test effect`,
  `Save all effects`, `Reload from disk`, `Reset all effects to
  defaults`.

## State model

Two layers of state in the dialog:

- **Saved state** — what's on disk (loaded via `shaker_layers_io.load`).
- **Working copy** — `dict[str, list[Layer]]` mutated by the UI as the
  user edits. Diverges from saved state until the user hits Save.

Switching the effect dropdown writes the current rows into the working
copy under the previously-shown effect name, then loads the rows for the
newly-selected effect from the working copy. This way edits persist
across effect-dropdown changes without touching disk.

A small helper `_is_modified(name)` compares the working copy to the
saved state for a given effect → drives the `●` marker on the dropdown
and a "Save all effects" button enable state.

## Behaviour notes

- An effect that has no `EFFECT_LAYERS` entry on disk shows **one**
  implicit default row (`Layer()` = freq×1.0, gain×1.0, route=both,
  sine). Editing and saving that row turns the implicit default into a
  real entry on disk.
- Removing the last layer is **not allowed** — the remove button on the
  only remaining row is disabled. (Otherwise a saved-empty effect would
  produce nothing on the shaker even when the user expected it to.)
- `+ Add layer` appends a new row with `Layer()` defaults.
- `Reset effect to default` reads the bundled default JSON for the
  current effect (STEP_04) and rewrites the working copy for that effect
  only. Other effects in the working copy are untouched. Confirmation
  dialog if the effect has unsaved changes.
- `Test effect`: builds a temporary `[Layer]` list from the current row
  widgets (no save needed) and plays it for ~2 seconds at a
  representative call-site frequency (40 Hz), mirroring the existing
  shaker-test-button mechanism (a worker thread + `QTimer.singleShot`).
  Uses the currently-configured shaker output device, channel mode, and
  pan from the dialog (so the user can A/B with positioning).
- `Save all effects`: serialises the working copy via
  `shaker_layers_io.save()` and calls `ffb_shaker.reload_layers()` so
  the running shaker child picks up the new spec without a restart.
  Disabled if nothing is modified.
- `Reload from disk`: discards the working copy, re-reads the file,
  re-populates the table. Confirmation dialog if any effect is
  modified.
- `Reset all effects to defaults`: confirmation dialog, then overwrites
  the on-disk file with the bundled default JSON and reloads. The
  working copy is replaced wholesale.

## Implementation hints

The existing System Settings dialog mixes Qt-Designer–generated UI
(`SM/system_settings.ui` → compiled by `makesystem_settings.bat`) with
programmatic additions. The Shaker tab itself is built programmatically
in `_setup_shaker_tab()` (`SystemSettingsDialog.py:284`). The new
"Effect layers" section should follow the same programmatic pattern so
the `.ui` file stays untouched and Designer-generated code does not need
regenerating.

Suggested method skeleton, added below `_setup_shaker_tab`:

```python
def _setup_shaker_layers_section(self, parent_layout):
    """Build the layered-effects editor inside the Shaker tab.

    parent_layout is the QVBoxLayout returned by _setup_shaker_tab so
    the layer editor sits below the existing soundcard controls.
    """
    ...
```

Working-copy lifecycle:

- Populate from `shaker_layers_io.load(get_shaker_effects_path())` once,
  during `_setup_shaker_layers_section`.
- Whenever the effect dropdown changes index, copy the table rows back
  into the working copy under the *previous* effect, then rebuild the
  table from the working copy under the *new* effect.
- On `accept()` or "Save all effects": serialise the working copy.

Do **not** mutate `ffb_shaker.EFFECT_LAYERS` directly from the dialog —
go through `save()` then `reload_layers()` so disk and runtime stay in
sync.

## Acceptance

- The Shaker tab now contains an "Effect layers" subsection beneath the
  existing soundcard controls.
- Effect dropdown lists every name from `SHAKER_EFFECT_WHITELIST`.
- Selecting an effect shows its current layers (or the implicit default
  single row).
- Editing freq×, gain, route, osctype and switching between effects
  preserves the unsaved edits in the working copy.
- `+ Add layer` and `−` (remove) work; the last row's remove is
  disabled.
- `Test effect` plays the unsaved spec on the configured shaker output
  for ~2 s.
- `Save all effects` writes JSON; reopening the dialog later shows the
  saved values; the running shaker child uses the new layers without
  restart.
- `Reload from disk` discards unsaved edits after confirmation.
- `Reset effect to default` reverts the current effect to the bundled
  default; other effects in the working copy stay untouched.
- `Reset all effects to defaults` overwrites the JSON with the bundled
  default after confirmation.
- The `●` marker appears next to modified effect names and disappears
  on save/reload.

## Out of scope for STEP_03

- Stick-side awareness (stick code is unmodified).
- Backup / restore of `shaker_effects.json` outside the dialog.
- Per-aircraft layer overrides.

## Stop here

Tick the box in `PLAN_LAYERS.md`, append any UX surprises in the Notes
section, stop for review.
