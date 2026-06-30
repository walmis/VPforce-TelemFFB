# STEP_03 — Device-type integration

## Goal

Make `--type shaker` a launchable mode. The master and Rhino paths must keep working unchanged.

## File-by-file changes

### 1. `telemffb/CmdLineArgs.py:118`

Update `--type` help to include `shaker`:

```python
parser.add_argument('-t', '--type',
                    help='FFB Device Type | joystick (default) | pedals | collective | trimwheel | shaker',
                    default=None)
```

### 2. `main.py:147`

```python
mapping = {1: "joystick", 2: "pedals", 3: "collective", 4: "trimwheel", 5: "shaker"}
```

### 3. `main.py:264`

```python
index_dict = {'joystick': 1, 'pedals': 2, 'collective': 3, 'trimwheel': 4, 'shaker': 5}
```

### 4. `telemffb/sim/aircraft_base.py` — add `is_shaker`

After `is_trimwheel` (around `:515`):

```python
def is_shaker(self):
    return self._telem_data.get("FFBType") == "shaker"
```

### 5. `telemffb/sim/aircraft_base.py` — runtime rebind (use the contingency, not the conditional)

**Decision after inspecting initialisation order:** the simple `if G.device_type == 'shaker': ...` conditional import does **not** work, because `aircraft_base.py` is imported transitively from `main.py`'s top-level imports (via `from telemffb.MainWindow import MainWindow`, whose own top-level `from telemffb.sim.aircraft_base import effects` triggers `aircraft_base` to execute). That happens **before** `main()` runs and before `_setup_device_configuration()` sets `G.device_type`. At conditional-import time, `G.device_type` is still `""` and the shaker branch would never fire.

The brief explicitly anticipates this: "If it isn't, fall back to importing both modules and rebinding ... after `G.device_type` is initialised." We take that contingency.

Concrete change in `aircraft_base.py`: keep the existing static import from `ffb_rhino` (so the joystick / pedals / collective / trimwheel paths are byte-for-byte unchanged) and add a small `use_shaker_backend()` function that swaps the `Dispenser`'s factory class and rebinds the module-level `HapticEffect` / `FFBReport_SetCondition` symbols. Effects in `Dispenser` are created lazily on first `effects[name]` access, which only happens during telemetry processing — well after `_setup_device_configuration()` runs.

```python
def use_shaker_backend() -> None:
    global HapticEffect, FFBReport_SetCondition
    from telemffb.hw.ffb_shaker import (
        HapticEffect as _S_HapticEffect,
        FFBReport_SetCondition as _S_Cond,
    )
    HapticEffect = _S_HapticEffect
    FFBReport_SetCondition = _S_Cond
    effects.cls = _S_HapticEffect
```

`main.py` invokes `aircraft_base.use_shaker_backend()` from inside the shaker branch of `_initialize_device_connection()` (see step 6), right after `init_shaker(synth)`.

### 6. `main.py` Rhino HID open path (`main.py:348` — `dev = HapticEffect.open(...)`)

Wrap so that when `G.device_type == 'shaker'`, instead of opening Rhino HID:

```python
if G.device_type == 'shaker':
    from telemffb.hw.shaker_synth import ShakerSynth
    from telemffb.hw.ffb_shaker import init_shaker
    device_name = G.system_settings.get('shakerDevice', None)
    gain = G.system_settings.get('shakerGain', 1.0)
    synth = ShakerSynth(device=device_name, master_gain=gain)
    synth.start()
    init_shaker(synth)
    G.shaker_synth = synth
else:
    dev = HapticEffect.open(vid_pid[0], vid_pid[1])
    # ... existing code ...
```

### 7. `telemffb/globals.py`

Add near `device_type : str = ""` at line 64:

```python
shaker_synth: 'ShakerSynth | None' = None
```

(Use a string forward-reference to avoid an import cycle / unconditional sounddevice import.)

### 8. Master-instance check in `main.py`

The shaker should never be the master (it depends on telemetry from a sim-connected master). If `device_type == 'shaker'` and would otherwise be master: log a warning and refuse to launch as master. The user must run a non-shaker device as master. The relevant master/index logic is around `main.py:264-271`.

## Acceptance

- `python main.py --type shaker --child --masterport <port>` starts without HID errors.
- The shaker child receives telemetry over IPC (visible in logs) and processes it through `aircraft_base` without exceptions.
- Joystick (Rhino) master instance unchanged in behaviour. Existing pedals / collective / trimwheel children unchanged.
- Attempting to launch the shaker as master surfaces a clear warning and refuses.
