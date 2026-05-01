# STEP_05 — Soundcard selection UI

## File to modify

`telemffb/SystemSettingsDialog.py` (and possibly the Qt-Designer-generated `telemffb/ui/Ui_SystemDialog.py` — both files exist; inspect first to decide where the new widgets belong).

## What to add

A "Shaker" section/tab/group with three controls:

| Field | Type | Behaviour |
|---|---|---|
| Output device | `QComboBox` | Populated from `ShakerSynth.list_output_devices()`. Display: `"{index}: {name} ({samplerate:.0f} Hz)"`. Stored value: device **name string** (not index — index changes between reboots). |
| Master gain | `QDoubleSpinBox` or `QSlider` | Range 0.0–2.0, step 0.05, default 1.0. |
| Test button | `QPushButton` | On click: open a temporary `ShakerSynth` with the currently-selected device, play 2 s of 35 Hz at 0.5 amplitude, close. Don't block the UI thread — run on a `QThread` or `threading.Thread`. |

## Persistence

- Use the same settings store as existing keys like `pidJoystick`, `masterInstance`. Find the helper (likely `G.system_settings.get(...)` and a `set` counterpart). Keys: `shakerDevice` (str) and `shakerGain` (float).
- On startup of a shaker child (STEP_03 step 6): read `shakerDevice`, resolve to a current `sounddevice` index by name match (case-insensitive substring is acceptable), and fall back to the system default with a logged warning if not found.

## Acceptance

- System Settings dialog opens, shows a Shaker section with the three controls.
- Dropdown lists actual audio output devices on the dev machine (verify via `ShakerSynth.list_output_devices()` agreement).
- Test button produces audible shake on the chosen device without blocking the UI.
- After saving and restarting TelemFFB in shaker mode, the saved device is used.
- If the saved device name no longer matches anything connected, the shaker child logs a warning and falls back to the system default — it does not crash.
