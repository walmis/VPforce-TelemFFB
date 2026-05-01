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

### 5. `telemffb/sim/aircraft_base.py:29-31` — conditional import

Replace:

```python
from telemffb.hw.ffb_rhino import EFFECT_TRIANGLE, HapticEffect, FFBReport_SetCondition
from telemffb.hw.ffb_rhino import EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION, EFFECT_SPRING_ADJUSTER
from telemffb.hw.ffb_rhino import EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN
```

with a conditional binding on `G.device_type`:

```python
import telemffb.globals as G
if G.device_type == 'shaker':
    from telemffb.hw.ffb_shaker import (
        EFFECT_TRIANGLE, HapticEffect, FFBReport_SetCondition,
        EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION,
        EFFECT_SPRING_ADJUSTER, EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN,
    )
else:
    from telemffb.hw.ffb_rhino import (
        EFFECT_TRIANGLE, HapticEffect, FFBReport_SetCondition,
        EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION,
        EFFECT_SPRING_ADJUSTER, EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN,
    )
```

**Verify** at runtime that `G.device_type` is set before `aircraft_base.py` is imported. If it isn't, the fallback is to import both modules and rebind `effects: Dispenser = Dispenser(HapticEffect_shaker if G.device_type == 'shaker' else HapticEffect_rhino)` after `G.device_type` is initialised. **Inspect `main.py` initialisation order before deciding which form to use.**

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
