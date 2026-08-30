# TelemFFB Development Guide for AI Coding Agents

## Project Overview
TelemFFB is a Python/PyQt6 desktop application that generates force feedback (FFB) telemetry effects for flight simulator devices. It bridges telemetry data from simulators (DCS, MSFS 2020/2024, IL-2 Sturmovik/Korea, BMS Falcon, X-Plane) to VPforce FFB hardware (Rhino joystick, DIY pedals, collective controls, trim wheel) via direct USB HID communication using `hidapi` and `libusb1`.

**Core concept**: Receive telemetry → Parse into `BaseTelemetryData` → Route through aircraft-specific effect classes (composed from MixIns) → Send HID commands to FFB device.

**License**: GPL v3

**Topic-specific references** (read the matching doc when the task touches that area):
- Adding or restructuring an **aircraft class** → `docs/adding_an_aircraft_class.md`
- Editing **`defaults.xml`** (settings, model overrides, sc_overrides) → `docs/defaults_xml_reference.md`

---

## Architecture: The Big Picture

### Component Hierarchy
```
main.py (orchestrator - 16-phase startup)
├── TelemManager (QObject + threading.Thread) - Routes telemetry to aircraft instances
│   ├── SimListenerManager - Manages sim-specific listeners (DCS, MSFS, IL2, BMS, X-Plane)
│   │   ├── SimDCS    → DcsIpcThread (shared mem IPC) + NetworkThread (UDP fallback port 34380)
│   │   ├── SimMSFS   → SimConnectSock (pysimconnect fork)
│   │   ├── SimIL2    → NetworkThread (UDP port 34385, from `portIL2` setting) + IL2PacketForwarder
│   │   ├── SimBMS    → SharedMemThread + BMSManager
│   │   └── SimXPLANE → NetworkThread (UDP port 34390) + X-Plane plugin
│   └── Aircraft instances (per-sim modules) - Process telemetry per-aircraft
├── MainWindow (PyQt6) - UI, settings dialogs, status indicators
├── FFBRhino / HapticEffect - Direct USB HID device communication (hidapi)
├── IPCNetworkThread (UDP sockets) - Multi-instance coordination
├── SettingsManager - XML config read/write, profile management, offline mode
└── ExceptionTracker - Captures errors, provides viewer dialog + reporting API
```

### Startup Flow (main.py phases)
1. Qt app init (Fusion style, Segoe UI 10pt)
2. CLI args (`CmdLineArgs.parse()`), master/child determination, mutex check
3. System settings (`utils.SystemSettings`), theme setup, device config
4. Version info, config paths (dev vs production), legacy config migration
5. Logging init (console + file + dedup + ANSI colorization)
6. LogWindow, stdout/stderr redirection via `OutLog`
6.5. ExceptionTracker init
7. Legacy userconfig conversion
8. SettingsManager init (with corruption recovery)
9. Device connection + firmware validation (min v1.0.18)
10. TelemManager start
11. IPC setup + signal connections
12. Child instance auto-launch (`_launch_children`); window display (minimized/tray/normal)
14. Async initialization (VPConf profile push, gain reading)
15. Event loop (`app.exec()`)
16. Cleanup (notify children, stop listeners, reset gains/deadzone)

### Aircraft Effect System (MixIn Architecture)
Aircraft classes are **composed from MixIns** using multiple inheritance. Each MixIn implements one effect category, inherits from `AircraftEffectUtilsBase`, and may override `on_telemetry()` / `on_timeout()` / `on_event()`. The MRO order in `AircraftBase` defines the execution order — one MixIn per file.

- **All generic effect MixIns live in `telemffb/sim/base/`** — one class per file (17: PedalSpringOverride, HelicopterEffects, Weapons, Deadzone, HydraulicLoss, DecelerationEffect, EngineRumble, WindEffect, AdvancedSpring (composes GForce + DynamicSpring), MotionEffects, BuffetingEffect, ElevatorDroop, AoAEffects, DynamicSpring, FFBForces, GForceEffect, AircraftParams). `AircraftBase` (`telemffb/sim/aircraft_base.py`) composes twelve of them, in this MRO order: PedalSpringOverride → HelicopterEffects → Weapons → Deadzone → HydraulicLoss → Deceleration → EngineRumble → Wind → AdvancedSpring → MotionEffects → Buffeting → ElevatorDroop.
- **MSFS/X-Plane MixIns** (`telemffb/sim/msfs_xp/`): FlightControls (non-FBW), FBWFlightControls (AP following, custom axes), HeliControls, SimConnect (event sending), Trimwheel, NosewheelShimmy, SteeringFriction, Turbulence.
- **Aircraft classes** per sim: `aircrafts_dcs.py` (DCS + BMS, inline), `aircrafts_il2.py` (IL-2, inline), `aircrafts_msfs_xp.py` (re-exports one class per file from `msfs_xp/`: `Aircraft`, `JetAircraft`, `PropellerAircraft`, `TurbopropAircraft`, `GliderAircraft`, and the `Helicopter` family).

`AircraftEffectUtilsBase` provides: `effects` (the global `Dispenser`), current/previous `telem_data`, `check_button_press()` / `check_master_button_press()`, change detection with timing (`has_changed()` / `anything_has_changed()`), `apply_settings()` (XML params → attributes), device-type checks (`is_joystick()` / `is_pedals()` / `is_collective()` / `is_trimwheel()`), sim checks (`_sim_is_msfs()` etc.), `step_value_over_time()` (per-frame interpolation), and the three hooks.

**Class resolution**: `TelemManager` first looks up the aircraft name in `defaults.xml`; if not found (MSFS/X-Plane only) it falls back to SimConnect category + engine type:

| SimConnect data | Class |
|---|---|
| `Helicopter` | `Helicopter` |
| `Jet` category, or `Airplane` with engine type 1 | `JetAircraft` |
| `Airplane` engine type 0 (piston) | `PropellerAircraft` |
| `Airplane` engine type 5 | `TurbopropAircraft` |
| `Airplane` engine type 2 (none) | `GliderAircraft` |
| `Airplane` engine type 3 (helo) | `Helicopter` |

Full walkthrough of creating a new class (module placement, sc_overrides, registration, tests): `docs/adding_an_aircraft_class.md`.

### Global State
**Never use the `global` keyword.** All application-wide state lives in `telemffb.globals`:
```python
import telemffb.globals as G
```
Key attributes (the full list, annotation-only, is in the file — they have no runtime value until `main.py` startup phases set them; tests use `monkeypatch.setattr(G, ..., raising=False)`): `G.device_type` (`"joystick"` / `"pedals"` / `"collective"` / `"trimwheel"`), `G.master_instance` / `G.child_instance`, `G.effects` (global `HapticEffect` dispenser), `G.telem_manager`, `G.sim_listeners`, `G.main_window`, `G.settings_mgr`, `G.system_settings`, `G.ipc_instance`, `G.log_window`, `G.exception_tracker`, `G.device_info` / `G.device_devpath` / `G.device_usbpid` / `G.device_firmware_version` / `G.device_connection_status`, `G.userconfig_path` / `G.defaults_path` / `G.userconfig_rootpath`, `G.args`, `G.launched_instances`, `G.instance_dev_dict`, `G.active_buttons` / `G.master_buttons` / `G.child_buttons`, `G.startup_configurator_gains` / `G.vpconf_configurator_gains` / `G.current_configurator_gains`, `G.gain_override_dialog`, `G.useDarkMode`, `G.dev_build` / `G.release_version` / `G.is_exe`, `G.force_reload_aircraft_trigger`, `G.il2_ffb_device_ordinal`, `G.vpconf_init_pending`, `G.current_vpconf_profile`.

### Configuration System (XML-based)
Two parallel XML files:
- `defaults.xml` — shipped defaults (full schema and 6-layer resolution hierarchy: `docs/defaults_xml_reference.md`)
- `userconfig_v2.xml` — user overrides (in `%LOCALAPPDATA%\VPForce-TelemFFB\`)

`telemffb/xmlutils.py` is a **legacy facade** over the `telemffb/xml/` package — `XmlStore` (I/O, parsing, tree management, file locking), `ConfigResolver` (reads), `ConfigWriter` (writes); all read/write operations go through that triple. The module-level names (`auto_user_root` & co) are **one-way sync targets** (store → globals): reading them still works, but assigning to them changes no behavior — in tests, inject fixture trees via `XmlStore`.

Locking: per-file OS-level `FileLock` (`telemffb/namedmutex.py`) — shared for reads, exclusive for writes, and writes go through temp-file + atomic replace, so a crash mid-write never leaves a half-written file. `update_roots()` re-parses both files; the parsed trees persist until the next call (a recreated store — device-scope switch — adopts the previous trees), so **call it after any programmatic change**. `try_parse()` is lock-aware: it holds the shared lock only while reading bytes, retries on `ParseError`, and returns `None` on lock timeout instead of blocking (safe from worker threads). The facade itself is main-thread-only in practice (its singleton is not thread-safe).

Config hierarchy: SIM → CLASS → MODEL → PROFILE (profile is an optional overlay). `SettingsManager` wraps XML ops with state tracking, offline mode support, and PyQt signals.

### Telemetry Flow
1. Sim-specific listener receives raw data (DCS shared mem/UDP, MSFS SimConnect, IL-2 UDP, BMS shared mem, X-Plane UDP via plugin)
2. Data normalized to semicolon-delimited string: `KEY1=val1~val2;KEY2=val3;...` (tilde = array)
3. `TelemManager.submit_frame(data)` queues on a condition variable; `run()` parses into `BaseTelemetryData`, calculates frame timing, merges IPC telemetry (master)
4. Aircraft resolved by name + source (see resolution table); new aircraft creates a new instance
5. `aircraft.on_telemetry(telem_data)` runs the MixIn chain; effects update on the device via HID
6. TelemetryTap captures the frame for analysis; `telemetryReceived` signal emitted for the UI

**Timeout & sim exit detection**: per-frame timeout from `telemTimeout` (default 200 ms) → `on_timeout()` fires once, periodic effects stop. After a 5 s grace, `_check_sim_process()` polls the OS process list every 5 s; if gone → `notify_sim_exited()`, clears aircraft, restarts listeners.

### Multi-Instance Architecture
- **Master** auto-launches **child** instances (one per additional device); children handle their own device and send button/telemetry data to the master
- IPC over UDP sockets on localhost (**not** ZMQ): master binds a random port, children connect via `--masterport`; keepalive 1 s interval, 3 missed → exit; message types: `Keepalive`, `Child Keepalive:<dev>:<status>`, `telem:<json>`, `effects:<json>`, `MASTER INSTANCE QUIT`, `RESTART SIMS`, `SHOW WINDOW`, `TOGGLE OFFLINE:`, `LOADCONFIG:`, `MASTER_BUTTONS:`, `BUTTONS:`
- Win32 named mutex (`namedmutex.py`) prevents duplicate masters unless `G.allow_multi_instance`; master auto-assigns `devpath_*` settings by product string or VID:PID

---

## Key Developer Workflows

### Building the Application
1. **Development mode**: `python main.py`
2. **Resource compilation**: `makeresources.bat` (generates `resources.py` from `resources.qrc`)
3. **Production build**: PyInstaller with `VPforce-TelemFFB.spec` (bundles X-Plane plugin, hidapi.dll, simconnect.dll, export scripts, defaults.xml, config.ini; aggressively trims Qt modules) → `dist/VPforce-TelemFFB/`

### Running Tests
```bash
pytest                          # Run all tests
pytest tests/test_*.py -v      # Verbose output
pytest --cov=telemffb          # With coverage
```
- Test framework in `tests/framework/`: `MockFFBDevice` (`connected` / `set_connected()` for higher-level tests), `MockInputData`, `MockConditionEffect`, `MockHapticEffect`, `MockEffectDispenser`, `MockSimConnect`, `MockDampener`, `MockSpringCondition`, `BaseTelemetryEffectTestCase` (base class with full setup/teardown)
- `conftest.py` — autouse fixture resets `G.effects` and `G.master_buttons`
- Markers: `unit`, `integration`, `msfs`, `xplane`, `joystick`, `pedals`, `collective`, `helicopter`, `slow`
- Warnings treated as errors (except DeprecationWarning)
- The `xmlutils` module globals are one-way (store → globals): to inject a fixture tree in tests, use `XmlStore` — setting `xu.auto_user_root` alone no longer affects reads/writes
- `HapticEffect.device` is a **class attribute** — tests that construct devices must save/restore it
- `telemffb.hw.hid` is a `MagicMock` in `sys.modules` — install yours with `sys.modules.setdefault('telemffb.hw.hid', MagicMock())` and patch the module **as `ffb_rhino` sees it** (`ffb_rhino_module.hid`), never via a fresh `import telemffb.hw.hid` (a sibling test may have swapped the `sys.modules` entry, so a fresh import can bind a different mock)
- `main.py` is **Windows-only** (imports `winreg` via `MainWindow`) — tests that need it must guard with `skipif` or use static source checks

---

## Coding Guidelines

### Globals
All application state lives in `telemffb.globals` (imported as `G`). Never use the `global` keyword, and never use globals as default arguments — they are evaluated once at function definition time, not at call time:

```python
# Don't do this
def foo(val=G.some_setting): ...

# Do this instead
def foo(val=None):
    if val is None:
        val = G.some_setting
```

### Structure
One class per file. Large classes belong in their own module under `telemffb/sim/base/` or `telemffb/sim/msfs_xp/`. Prefix private members with `_` to signal they're internal to the class (`self._my_var`) — cross-module code only uses the public API.

### MixIns
Aircraft effects are built from MixIns that inherit from `AircraftEffectUtilsBase`. When overriding hooks, always chain up:

```python
@override
def on_telemetry(self, telem_data):
    super().on_telemetry(telem_data)
    # your logic here
```

Do the same for `on_timeout()` and `on_event()`.

### Performance
Telemetry runs at 60–120 Hz. Keep `on_telemetry()` lean: no heavy math, no I/O, no allocations you can avoid. Precompute what you can outside the hot path.

### Error Handling
In the telemetry hot path, never let an exception propagate — it kills the processing loop. Catch broadly, call `logging.exception()`, and keep going. In UI/threaded code, you can be more specific. In the device layer, failures surface as explicit exceptions and liveness checks rather than asserts.

### Threading & GUI
`TelemManager` runs in its own thread. Never touch Qt widgets from background threads — route everything through `utils.schedule_on_main_thread(lambda: ...)`, or use PyQt signals.

### Effects & HID Values
The Rhino firmware uses a fixed-point range of **-4096 to 4096** for coefficients, offsets, saturation, and magnitude. Float values in MixIns (e.g. `0.5` spring coefficient) are multiplied by 4096 before sending to the device; `FFBReport_SetCondition.set_coefficient()` and `.set_offset()` handle the conversion automatically.

`G.effects` is a `Dispenser` — accessing `G.effects["name"]` lazily creates the effect on first use, then returns the cached instance. Effect names must be unique within an aircraft instance.

When you create a new effect, register its name in the effects translator: `effect_dict` in `telemffb/utils.py`. Each entry maps an effect-name pattern (regex supported, e.g. `"blade_slap.*"`) to `["Human Readable Name", "intensity_setting_name"]`. The translator supplies the readable name shown in the effects panel, and the setting-name half is how the UI links an active effect to its slider (green highlight and live % force display). An unregistered effect still works but appears under its raw internal name with no slider linkage.

### Telemetry Data
Raw telemetry arrives as semicolon-delimited key-value pairs, with tilde-separated arrays: `KEY1=val1~val2;KEY2=val3;...`. `TelemManager` parses this into `BaseTelemetryData` — a dict-backed container that also supports dot-access (`telem_data.AoA`). All known fields are type-annotated on the class for IDE autocomplete; values default to `None` when absent. Access safely with `telem_data.get("key", default)` or dot-access with a `None` check.

### Style
Follow PEP 8. Write docstrings in reStructuredText format. Use type hints where they help readability. Prefer `typing.Optional` and `typing.Literal["joystick", "pedals", "collective", "trimwheel"]` for device types.

Imports go at the top of the file, just after any module comments and docstrings, and before module globals and constants, grouped in the standard order:

1. Standard library imports
2. Third-party imports
3. Local application imports

This rule may only be broken to avoid circular imports — and even then, the circular dependency should be fixed upstream rather than worked around.

---

## Key Files to Understand

| File | Purpose |
|------|---------|
| `main.py` | 16-phase application bootstrap |
| `telemffb/globals.py` | Single source of truth for app state |
| `telemffb/hw/ffb_rhino.py` | USB HID protocol, effect definitions, ctypes structs |
| `telemffb/hw/ffb_sdl.py` | **DEPRECATED** SDL haptic backend (kept for posterity) |
| `telemffb/xmlutils.py` | Legacy facade over `telemffb/xml/` (module-global API, one-way sync) |
| `telemffb/xml/` (`store.py`, `read.py`, `write.py`, `merge.py`) | XML I/O, per-file locking, parsing, settings resolution |
| `telemffb/utils.py` | Utilities: Dispenser, LowPassFilter, Dampener, SystemSettings, effects translator (`effect_dict`), etc. |
| `telemffb/telem/TelemManager.py` | Telemetry routing, aircraft instantiation, sim exit detection |
| `telemffb/telem/SimTelemListener.py` | SimListenerManager + per-sim listener classes |
| `telemffb/sim/aircraft_base.py` | MixIn composition + base aircraft behavior |
| `telemffb/sim/BaseTelemetryData.py` | Dict-backed telemetry container with typed attribute hints |
| `telemffb/sim/base/AircraftEffectUtilsBase.py` | Base utilities all MixIns inherit from |
| `telemffb/sim/aircrafts_dcs.py` | DCS aircraft class + DCS command injection |
| `telemffb/sim/aircrafts_il2.py` | IL-2 aircraft class + damage/buffet effects |
| `telemffb/sim/aircrafts_msfs_xp.py` | Module exports for MSFS/XP aircraft |
| `telemffb/sim/msfs_xp/Aircraft.py` | Base MSFS/XP aircraft class |
| `telemffb/sim/msfs_xp/MsfsXpFBWFlightControlsMixIn.py` | FBW control forces + AP following |
| `telemffb/sim/msfs_xp/MsfsXpFlightControlsMixIn.py` | Non-FBW stick forces |
| `telemffb/SettingsManager.py` | XML config manager, profiles, offline mode |
| `telemffb/IPCNetworkThread.py` | UDP IPC between master/child instances |
| `telemffb/MainWindow.py` | Main PyQt6 window, settings layout, status indicators |
| `telemffb/ExceptionTracker.py` | Error capture, logging handler, viewer dialog |
| `telemffb/namedmutex.py` | Win32 named mutex (ctypes) |
| `telemffb/CmdLineArgs.py` | CLI argument parser |
| `styles.py` | Light/dark mode QSS stylesheets |

Line counts are large (`xmlutils.py` ~2400, `utils.py` ~3600, `ffb_rhino.py` ~1800) — search within them rather than reading top to bottom.

---

## When Making Changes

1. **Adding new effects**: Create a MixIn in `telemffb/sim/base/` (generic) or `telemffb/sim/msfs_xp/` (sim-specific). Inherit from `AircraftEffectUtilsBase`. Add to `AircraftBase`'s MRO or the aircraft subclass. Use `@override`. Call `super()` in each hook. Register the effect name in `effect_dict` (`telemffb/utils.py`).
2. **New aircraft type**: Follow `docs/adding_an_aircraft_class.md` — one class per file under `msfs_xp/` (MSFS/X-Plane) or inline in the DCS/IL-2 module; register in `defaults.xml`; MSFS/X-Plane-only: telemetry remapping via `<sc_overrides>`.
3. **UI changes**: PyQt6 components in `telemffb/*.py` (MainWindow, dialogs). Use existing QSS from `styles.py`, Fusion style conventions. All GUI updates from non-main threads via `utils.schedule_on_main_thread()`.
4. **Config changes**: See `docs/defaults_xml_reference.md`. Update `defaults.xml` (new default values) and ensure the read path in `telemffb/xml/` handles the new keys. New enum settings go into `SettingsManager`'s class-level dicts. Writes go through the store's locked write path, then `update_roots()`.
5. **Testing new features**: Add tests to `tests/`. Use `BaseTelemetryEffectTestCase` from `tests/framework/base.py`. Mark with the appropriate pytest markers. Run `pytest` before committing.
6. **New telemetry fields**: Add a typed attribute + docstring (sims, source, units) to `BaseTelemetryData`; populate in the sim-specific listener/parser.

**Effect lifecycle pattern**:
```python
effect = self.effects.get("effect_name")  # Get from global dispenser
if not effect:
    effect = HapticEffect(effect_type=EFFECT_SINE)
    effect.start()  # Allocates on device
    self.effects.set("effect_name", effect)

effect.magnitude = calculate_magnitude(telem_data)
effect.start()  # Sends to device (updates + restarts)

# On timeout/cleanup:
effect.stop()  # Frees device resource
```

---

## Dependencies & External Integrations

**Runtime** (`requirements.txt`): `pyqt6==6.9.1`, `pysimconnect` (custom fork at github.com/walmis/pysimconnect — rolling `master.zip`), `libusb1`, `numpy==2.3.0`, `akima`, `psutil`, `stransi`, `pygetwindow`, `configobj`.
**Testing** (`requirements.txt`): `pytest`, `pytest-cov` — strict markers and warnings-as-errors configured in `pytest.ini`.
**Bundled binaries**: `dll/hidapi.dll`, `simconnect/simconnect.dll`, `xplane-plugin/` (auto-installed).

---

## Common Pitfalls

1. **Don't use global variables as default args** — see Coding Guidelines → Globals
2. **Never write the XML config files directly** — the multi-instance setup is protected by the per-file locks and atomic replace inside `XmlStore`; route writes through the store, then refresh the in-memory trees with `update_roots()` (they persist until called)
3. **Every new effect name must be registered in the effects translator** (`effect_dict` in `telemffb/utils.py`) — see Coding Guidelines → Effects & HID Values
4. **Always check `G.master_instance` vs `G.child_instance`** when implementing features that differ per instance type
5. **FFBRhino device communication is USB-latency sensitive** — batch HID updates when possible
6. **GUI updates from worker threads** — use `utils.schedule_on_main_thread()` or Qt signals
7. **MixIn `super()` chain** — always call `super().on_telemetry()`, `super().on_timeout()`, `super().on_event()`
8. **Telemetry data is dict-like** — use `telem_data.get('key', default)` for safe access, or dot-access for known fields
9. **Per-frame performance matters** — telemetry runs at 60–120 Hz; no per-frame log lines, no exceptions escaping `on_telemetry()`
10. **IPC is UDP, not TCP** — messages may be dropped; don't assume delivery ordering
11. **`ffb_sdl.py` is deprecated** — do not use, kept for historical reference only
12. **Never `assert` on runtime state in production paths** (device liveness included) — asserts are stripped under `python -O` and would silently kill the calling thread; use explicit checks instead

---

## Git Workflow

- Keep `main` clean and working. Develop features on short-lived branches:
  ```
  git checkout -b feature/descriptive-name
  ```
- Commit frequently with descriptive messages. Run `pytest` before committing.
- When ready, push the branch and open a pull request into `main`. Keep PRs small and focused on one concern.
- Never force-push to `main`. Avoid `--no-verify` or `--no-gpg-sign`.

### Commit Message Format

Use [Conventional Commits](https://www.conventionalcommits.org/) with a scope:

```
type(scope): subject

Body paragraph(s) explaining what changed and why. Reference specific files,
parameters, or telemetry variables. Note caveats, TODOs, or known limitations.
```

**Types**: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

**Scopes**: module or component name, e.g. `msfs-xp`, `flight-controls`, `conversions`, `telem`, `hw`, `ui`, `xml`

**Examples**:
- `refactor(msfs-xp): extract turbulence effect into TurbulenceMixIn` — body lists moved params, methods, and notes "no behaviour change"
- `docs(flight-controls): add physics audit TODOs to MsfsXpFlightControlsMixIn` — body lists affected methods with specific caveats
- `fix(conversions): add TODO for incorrect vsound constant and add rho0 alias` — body states impact (~27% gain error), rationale for leaving as-is, and secondary changes

---

## Questions to Ask When Reviewing Code

- Does this need access to global state? Use `telemffb.globals as G`
- Is this effect device-type specific? Check `G.device_type` / `self.is_joystick()` etc.
- Could this run in a child instance? Check `G.master_instance` / `G.child_instance`
- Will this be called per-frame? Consider performance (60–120 Hz telemetry rate)
- Does this modify XML? Route writes through the store's locked write path (never raw file I/O), then call `update_roots()`. Note the `xmlutils` facade is main-thread-only in practice
- Is this sim-specific behavior? Keep it in sim-specific modules, not base classes
- Does this touch the GUI from a background thread? Use `schedule_on_main_thread()` or signals
- Are all `super()` calls present in the MixIn chain?
- Is the effect name unique within the aircraft instance, and registered in `effect_dict`?
- Do private (`_`-prefixed) members stay private to their class?
