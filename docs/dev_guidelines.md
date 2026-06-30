# Development Guidelines

## Globals

All application state lives in `telemffb.globals`. Import it as `G`:

```python
import telemffb.globals as G
```

Never use the `global` keyword. And never use globals as default arguments — they're evaluated once at function definition time, not at call time:

```python
# Don't do this
def foo(val=G.some_setting): ...

# Do this instead
def foo(val=None):
    if val is None:
        val = G.some_setting
```

## Structure

One class per file. Large classes belong in their own module under `telemffb/sim/base/` or `telemffb/sim/msfs_xp/`. Prefix private members with `_` to signal they're internal (`self._my_var`).

## MixIns

Aircraft effects are built from MixIns that inherit from `AircraftEffectUtilsBase`. When overriding hooks, always chain up:

```python
@override
def on_telemetry(self, telem_data):
    super().on_telemetry(telem_data)
    # your logic here
```

Do the same for `on_timeout()` and `on_event()`. Effect names must be unique within an aircraft instance — `G.effects` is a dict-backed dispenser that reuses or creates by name.

## Performance

Telemetry runs at 60–120 Hz. Keep `on_telemetry()` lean: no heavy math, no I/O, no allocations you can avoid. Precompute what you can outside the hot path.

## Threading & GUI

`TelemManager` runs in its own thread. Never touch Qt widgets from background threads — route everything through `utils.schedule_on_main_thread(lambda: ...)`, or use PyQt signals.

## Config & XML

Config lives in `defaults.xml` (shipped defaults) and `userconfig_v2.xml` (user overrides). After any programmatic XML change, call `xmlutils.update_roots()` to refresh the parsed trees. Use `xmlutils.try_parse()` when reading files — it retries on lock, which matters in multi-instance setups.

## Telemetry Data

Raw telemetry arrives as semicolon-delimited key-value pairs, with tilde-separated arrays:
`KEY1=val1~val2;KEY2=val3;...`

`TelemManager` parses this into `BaseTelemetryData` — a dict-backed container that also supports dot-access (`telem_data.AoA`). All known fields are type-annotated on the class for IDE autocomplete; values default to `None` when absent. Access safely with `telem_data.get("key", default)` or dot-access with a `None` check.

## Effects & HID Values

The Rhino firmware uses a fixed-point range of **-4096 to 4096** for coefficients, offsets, saturation, and magnitude. When you see float values in MixIns (e.g., `0.5` spring coefficient), they get multiplied by 4096 before sending to the device. `FFBReport_SetCondition.set_coefficient()` and `.set_offset()` handle this conversion automatically.

`G.effects` is a `Dispenser` — accessing `G.effects["name"]` lazily creates the effect on first use, then returns the cached instance. Effect names must be unique per aircraft.

## Aircraft Class Resolution

When a new aircraft name appears, `TelemManager` looks it up in `defaults.xml`. If not found (MSFS/X-Plane only), it falls back to SimConnect category + engine type to pick a class:
- `Helicopter` → `Helicopter`
- `Jet` / piston engine type 1 → `JetAircraft`
- Piston engine type 0 → `PropellerAircraft`
- Engine type 5 → `TurbopropAircraft`
- Engine type 2 (none) → `GliderAircraft`

## Error Handling

In the telemetry hot path, never let an exception propagate — it kills the processing loop. Catch broadly, call `logging.exception()`, and keep going. In UI/threaded code, you can be more specific.

## Style

Follow PEP 8. Write docstrings in reStructuredText format. Use type hints where they help readability. Prefer `typing.Optional` and `typing.Literal["joystick", "pedals", "collective", "trimwheel"]` for device types.