# Multi-Device Effect Routing

This document explains the configurable routing system that decides which
effect plays on which device, with what gain and direction. It replaces
the hard-coded "stick + shaker" assumptions that lived in the aircraft
modules.

## TL;DR

- Every effect has a list of **layers**. Each layer says: *"on this
  device (selected by id, type, or position tag), play this effect at
  this gain, with this direction policy."*
- Layers are stored in JSON: bundled defaults (`telemffb/data/effect_routes_default.json`)
  + optional user overrides (`<userconfig>/effect_routes_user.json`).
- A small process-wide `EffectRouter` filters those layers per process so
  each device child only acts on what's relevant to it. Aircraft modules
  are unchanged — the routing happens transparently inside the
  `HapticEffect.start()` chain.
- New users get a 5-page Setup Wizard at first launch. Power users edit
  the matrix in `Tools → Effect Routing…`.

## Why?

Before this change, sim modules contained calls like
`effects['runway0'].periodic(..., direction=0).start()` with the
direction baked in, and the bass shaker had a separate JSON layer system
that the FFB devices didn't share. Setups beyond "stick + shaker" — for
example *VPForce Rudder up front + stick on the right*, or *helicopter
with no shaker but pedals + collective* — required code edits.

The routing engine generalises that one corner of the shaker pipeline
to all devices.

## Architecture

```
[ aircraft_*.py ] -- effects['name'].periodic(...).start()
        |
        v
[ effects Dispenser (aircraft_base.py) ]
        |
        v   (one of three at runtime)
[ ffb_rhino.HapticEffect ]   <-- legacy: stick + FFB devices, no routing
[ ffb_shaker.HapticEffect ]  <-- legacy: shaker child, owns audio mixing
[ ffb_router.HapticEffect ]  <-- new: subclass of ffb_rhino.HapticEffect
        |                         consults EffectRouter, then super().*()
        v
   FFB device write
```

`use_router_backend()` in `aircraft_base.py` swaps `effects.cls` to the
router-aware subclass. It only fires for FFB devices (joystick / pedals
/ rudder / collective / trimwheel). The shaker child keeps its
audio-mixing backend and reads the same routes file.

### The data model

```python
# telemffb/routing/effect_route.py

@dataclass
class RouteLayer:
    target: str                    # "id:stick_main" | "type:pedals" | "pos:floor" | "both"
    enabled: bool = True
    gain: float = 1.0
    freq_factor: float = 1.0
    osc_type: str = "sine"         # sine | impulse | bandpass_noise | passthrough
    direction_policy: str = "inherit"  # inherit | fixed | auto | from_telemetry
    direction_value: Optional[float] = None
    # ...optional shaper fields: center_hz, bandwidth_hz, attack_ms, decay_ms

@dataclass
class EffectRoute:
    name: str
    layers: List[RouteLayer]

@dataclass
class EffectRoutesPack:
    version: int = 4
    routes: dict[str, EffectRoute]                                     # global
    aircraft_class_overrides: dict[str, dict[str, EffectRoute]]        # per Helicopter / Jet / etc.
```

### Selectors

A `RouteLayer.target` is a selector string. Resolution against an
inventory device:

| Form              | Matches when …                              |
| ----------------- | ------------------------------------------- |
| `id:<device_id>`  | the device's `device_id` equals the value   |
| `type:<type>`     | the device's `type` equals the value        |
| `pos:<tag>`       | one of the device's `positions` has the tag |
| `both`            | legacy alias — matches stick **or** shaker  |

`type:` is the most common form because it survives renames. Use `id:`
when you genuinely want the layer to follow a specific physical
device (e.g. one of two shakers with different placements).

### Direction policies

| Policy           | Resolves to                                                     |
| ---------------- | --------------------------------------------------------------- |
| `inherit`        | the direction the aircraft module passed to `.periodic(...)`    |
| `fixed`          | `direction_value` (degrees, `[0, 360)`)                         |
| `auto`           | derived from device position tags (front=0, right=90, …)        |
| `from_telemetry` | reserved for future use; falls through to `inherit` for now     |

This is how runway-rumble can fire forward through the stick *and*
sideways through the pedals without changes to `aircraft_base.py`.

### Resolution order

```
defaults  <-  user overrides  <-  aircraft-class patch
   |               |                       |
bundled JSON   user JSON           merged at .resolve(class=...)
```

Per-effect granularity: a key in the user file replaces the same key in
the defaults wholesale. Per-class patches are applied last and replace
both. There's no per-layer merging — keep it predictable.

### Per-process filtering

Telemetry is shared between master and slave instances over UDP IPC, so
each device child reads the same routes file and decides locally:

```python
# inside ffb_router.HapticEffect.start()
layers = router.resolve(
    self.name,
    device_id=G.device_id,         # this process's device id
    device_type=G.device_type,
    device_positions=G.device_positions,
)
if not layers:
    return self                    # silently drop
```

There is intentionally **no master→slave effect forwarding**. That
would re-introduce the asymmetry we just removed.

## Files

| Path                                                    | What                                                 |
| ------------------------------------------------------- | ---------------------------------------------------- |
| `telemffb/routing/effect_route.py`                      | `RouteLayer`, `EffectRoute`, `EffectRoutesPack`      |
| `telemffb/routing/router.py`                            | `EffectRouter` + JSON loader                         |
| `telemffb/routing/ffb_router.py`                        | `HapticEffect` facade (lazy-built)                   |
| `telemffb/device_inventory.py`                          | `Device` dataclass + INI/JSON I/O                    |
| `telemffb/data/effect_routes_default.json`              | bundled defaults (v4 schema)                         |
| `telemffb/data/setup_presets.json`                      | wizard presets                                       |
| `telemffb/data/device_inventory_default.json`           | empty default inventory                              |
| `telemffb/DeviceInventoryTab.py`                        | "Devices" tab                                        |
| `telemffb/EffectRoutingDialog.py`                       | matrix editor + per-effect detail dialog             |
| `telemffb/SetupWizard.py`                               | first-run wizard                                     |
| `<userconfig>/effect_routes_user.json`                  | user overrides (created on first save)               |
| `config.ini` `[devices].deviceInventory`                | inventory blob, JSON-encoded                         |

## User flow

### First launch

1. Master instance starts. `_setup_routing()` finds an empty inventory
   and `setup_wizard_done=false`, so the SetupWizard opens after the
   main window is up.
2. The wizard auto-detects connected Rhinos, has the user pick a type
   per device, lets them set position tags / X-Y, then offers a list of
   presets compatible with the inventory.
3. Apply writes `[devices].deviceInventory` to `config.ini` and any
   preset's `route_overrides` into `effect_routes_user.json`. The
   wizard sets `setup_wizard_done=true`.

### Editing the inventory

`Devices` tab. Each row is a `Device`. Add / Remove / Auto-detect
Rhinos / Clear-All. Saves on every cell change. Routing changes
**that affect which device is which** require a restart, because
`use_router_backend()` runs once per process and chooses based on
`G.device_type`.

### Editing the routing matrix

`Tools → Effect Routing…`. The matrix shows **effects × devices**.
Toggle the checkbox to enable / disable a layer; the slider sets its
gain (0–200%). Double-click a cell for the per-layer detail dialog
(frequency factor, oscillator type, direction policy + value, bandpass
center / bandwidth, attack / decay).

The **Scope** combo above the matrix switches between:

- **Global** — `effects.routes` (defaults + user overrides merged).
- An aircraft class — `aircraft_class_overrides[<class>]`. First edit
  on an effect in this scope seeds a copy of the global route so you
  start from sensible values; the class patch then lives independently.

Apply / OK writes `effect_routes_user.json` and reloads it into the
running router live. No restart needed for routing-only changes.

### Re-running the wizard

`Help → Multi-Device Setup Wizard...`. Useful after adding new
hardware.

## Migrating older shaker_effects.json

The same loader handles v1–v3 files (with the legacy `route` field) and
the new v4 (`target`). On startup, `_setup_routing()` reads the legacy
file if present and converts it on the fly. Saving via the dialog always
writes v4.

`SHAKER_EFFECT_WHITELIST` in `ffb_shaker.py` is still the source of
truth for "which effects render usefully on a body shaker". The router
on the shaker child still consults the whitelist; the routes file is
applied on top.

## Multi-layer fan-out on FFB devices

When two or more layers of an effect resolve for the same device — for
example a runway layer at 30 Hz forward (0°) and another at 60 Hz
laterally (270°) on the pedals — the router holds **one Rhino effect
slot per layer** as an internal composite. ``periodic()`` configures
each slot independently (with the layer's gain, freq_factor, and
direction policy applied), and ``start()`` / ``stop()`` / ``destroy()``
fan out to all of them. Sub-handles are named ``"<effect>__layer<idx>"``
for log readability and parity with the shaker's audio-side scheme.

The shaker child has its own multi-layer mixing through ``ShakerSynth``
oscillators; the FFB-side fan-out described here is its FFB equivalent.

## Limitations

- **Type / USB-PID changes need a restart.** ``_setup_routing()``
  matches the USB PID once at startup and binds ``G.device_id`` /
  ``G.device_type`` / ``G.device_positions`` for the rest of the process
  lifetime. Position and label edits in the Devices tab are broadcast
  live to all instances via the ``inventory:`` IPC message; type rebinds
  still require a child restart because the type drives which backend
  (router vs. shaker audio) is wired up at process start.
- **``DirectionPolicy.FROM_TELEMETRY`` is not implemented.** The enum
  value is recognised by the router and selectable in the detail
  dialog, but resolution falls through to ``inherit``. A telemetry-
  field selector (``"vel_x"`` / ``"aoa_y"`` / …) is the planned
  follow-up.

## Testing

```bash
# pure routing unit tests (no hardware deps)
python -m unittest tests.test_routing

# router-as-haptic-effect facade (skipped without usb1/PyQt6/numpy)
python -m unittest tests.test_ffb_router
```

The pure-helper tests cover selector matching, override precedence,
class patch resolution, direction policies, JSON↔INI round-trips, and
live-reload semantics. The facade tests assert the right scaling reaches
the Rhino parent for `periodic` / `constant` / `physics` /
`fire_impulse`.
