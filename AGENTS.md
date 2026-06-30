# TelemFFB Development Guide for AI Coding Agents

## Project Overview
TelemFFB is a Python/PyQt6 desktop application that generates force feedback (FFB) telemetry effects for flight simulator devices. It bridges telemetry data from simulators (DCS, MSFS 2020/2024, IL-2 Sturmovik/Korea, BMS Falcon, X-Plane) to VPforce FFB hardware (Rhino joystick, DIY pedals, collective controls, trim wheel) via direct USB HID communication using `hidapi` and `libusb1`.

**Core concept**: Receive telemetry → Parse into `BaseTelemetryData` → Route through aircraft-specific effect classes (composed from MixIns) → Send HID commands to FFB device.

**License**: GPL v3

---

## Architecture: The Big Picture

### Component Hierarchy
```
main.py (orchestrator - 16-phase startup)
├── TelemManager (QObject + threading.Thread) - Routes telemetry to aircraft instances
│   ├── SimListenerManager - Manages sim-specific listeners (DCS, MSFS, IL2, BMS, X-Plane)
│   │   ├── SimDCS    → DcsIpcThread (shared mem IPC) + NetworkThread (UDP fallback port 34380)
│   │   ├── SimMSFS   → SimConnectSock (pysimconnect fork)
│   │   ├── SimIL2    → NetworkThread (UDP port 34381) + IL2PacketForwarder
│   │   ├── SimBMS    → SharedMemThread + BMSManager
│   │   └── SimXPLANE → NetworkThread (UDP port 34390) + X-Plane plugin
│   ├── Aircraft instances (per-sim modules) - Process telemetry per-aircraft
│   └── TelemetryTap → RingBuffer (high-rate ~30s, low-rate ~5min) for MCP analysis
├── MainWindow (PyQt6) - UI, settings dialogs, status indicators
├── FFBRhino / HapticEffect - Direct USB HID device communication (hidapi)
├── IPCNetworkThread (UDP sockets) - Multi-instance coordination
├── SettingsManager - XML config read/write, profile management, offline mode
├── ExceptionTracker - Captures errors, provides viewer dialog + reporting API
└── MCP Server (dev-only) - Streamable HTTP endpoint for LLM telemetry analysis
```

### Startup Flow (main.py phases)
1. **Phase 1**: Qt app init (Fusion style, Segoe UI 10pt)
2. **Phase 2**: CLI args (`CmdLineArgs.parse()`), master/child determination, mutex check
3. **Phase 3**: System settings (`utils.SystemSettings`), theme setup, device config
4. **Phase 4**: Version info, config paths (dev vs production), legacy config migration
5. **Phase 5**: Logging init (console + file + dedup + ANSI colorization)
6. **Phase 6**: LogWindow, stdout/stderr redirection via `OutLog`
7. **Phase 6.5**: ExceptionTracker init
8. **Phase 7**: Legacy userconfig conversion
9. **Phase 8**: SettingsManager init (with corruption recovery)
10. **Phase 9**: Device connection + firmware validation (min v1.0.18)
11. **Phase 10**: TelemManager start, TelemetryTap init, MCP server (master only, dev builds)
12. **Phase 11**: IPC setup + signal connections
13. **Phase 12a**: Child instance auto-launch (`_launch_children`)
14. **Phase 12b**: Window display (minimized/tray/normal)
15. **Phase 14**: Async initialization (VPConf profile push, gain reading)
16. **Phase 15**: Event loop (`app.exec()`)
17. **Phase 16**: Cleanup (notify children, stop listeners, reset gains/deadzone)

---

### Aircraft Effect System (MixIn Architecture)

Aircraft classes are **composed from MixIns** using multiple inheritance. Each MixIn implements a specific effect category and inherits from `AircraftEffectUtilsBase`.

#### Base MixIn Chain (in `telemffb/sim/aircraft_base.py`)
`AircraftBase` composes (in MRO order):
1. `PedalSpringOverrideMixIn` — Pedal-specific spring behavior
2. `HelicopterEffectsMixIn` — Rotor RPM rumble, collective effects
3. `WeaponsEffectMixIn` — Gun vibration, missile/bomb release, chaff/flare
4. `DeadzoneMixIn` — Deadzone effect handling
5. `HydraulicLossMixIn` — Reduced forces when hydraulic pressure lost
6. `DecelerationEffectMixIn` — Longitudinal G deceleration feel
7. `EngineRumbleMixIn` — RPM-proportional engine vibration
8. `WindEffectMixIn` — Gust/wind buffeting from relative wind data
9. `AdvancedSpringMixIn` — Spring override, trim, adjuster (inherits GForceEffectMixIn + DynamicSpringMixin)
10. `MotionEffectsMixIn` — Gear/flaps/spoiler motion vibration
11. `BuffetingEffectMixIn` — Stall buffeting effects

All MixIns inherit from `AircraftEffectUtilsBase` which provides:
- `effects` property → global `G.effects` Dispenser
- `telem_data` / `_last_telem_data` → current & previous `BaseTelemetryData`
- `check_button_press()`, `check_master_button_press()`
- `has_changed()`, `anything_has_changed()` — change detection with timing
- `apply_settings(settings_dict)` — apply XML config params as attributes
- `is_joystick()`, `is_pedals()`, `is_collective()`, `is_trimwheel()`
- `_sim_is_msfs()`, `_sim_is_xplane()`, `_sim_is_dcs()`, `_sim_is_bms()`, `_sim_is_il2()`
- `_get_device_axes()`, `_get_device_forces()`, `_get_device_report()` — static helpers
- `step_value_over_time()` — animated value interpolation across frames
- `on_timeout()`, `on_telemetry()`, `on_event()` — hooks each MixIn can override

#### Additional Generic MixIns (in `telemffb/sim/base/`)
- `AoAEffectsMixIn` — Angle-of-attack reduction force
- `DynamicSpringMixin` — Dynamic spring coefficient calculation
- `ElevatorDroopEffectMixIn` — Elevator droop force effect
- `FFBForcesMixIn` — Raw body-frame force feeding
- `GForceEffectMixIn` — G-force effect modes (DISABLED/LEGACY/NEW/ADVANCED)
- `RotationalDampingMixIn` — Rotation-based damping

#### MSFS/X-Plane Specific MixIns (in `telemffb/sim/msfs_xp/`)
- `MsfsXpFlightControlsMixIn` — Stick force simulation for non-FBW aircraft
- `MsfsXpFBWFlightControlsMixIn` — FBW control force simulation (AP following, custom axes)
- `MsfsXpHeliControlsMixIn` — Helicopter cyclic/collective/pedal forces
- `MsfsXpSimConnectMixIn` — SimConnect event sending (rudder/stick commands)
- `MsfsXpTrimwheelMixIn` — Trim wheel force feedback
- `MsfsXpNosewheelShimmyMixIn` — Nosewheel shimmy vibration
- `MfsfXpSteeringFrictionEffectMixIn` — Ground steering friction
- `TurbulenceMixIn` — Atmospheric turbulence effects

#### Aircraft Classes
Each sim has its own module that registers aircraft classes:
- `aircrafts_dcs.py` — `Aircraft(AircraftBase, DCSCommands)` + DCS-specific overrides
- `aircrafts_il2.py` — `Aircraft(AircraftBase)` + IL-2 damage/buffet effects
- `aircrafts_msfs_xp.py` — Re-exports from `msfs_xp/`:
  - `Aircraft` — base MSFS/XP aircraft (adds TurbulenceMixIn, FlightControlsMixIn, etc.)
  - `JetAircraft`, `PropellerAircraft`, `TurbopropAircraft`, `GliderAircraft`
  - `Helicopter`, `HPGHelicopter`, `CowanSimHelicopter`, `FlyInsideHelicopter`, `SASHelicopter`, `TaogH500Helicopter`, `XAW109Helicopter`

**Aircraft class resolution**: TelemManager looks up XML config first; if not found, falls back to SimConnect category/engine type to pick appropriate class.

---

### Global State Management (CRITICAL)
**Never use `global` keyword**. All application-wide state lives in `telemffb.globals`:
```python
import telemffb.globals as G
```

Key globals:
- `G.device_type` — `"joystick"`, `"pedals"`, `"collective"`, `"trimwheel"`
- `G.master_instance` / `G.child_instance` — bool flags
- `G.effects` — `Dispenser(HapticEffect)` — global effect pool (init in `aircraft_base.py`)
- `G.telem_manager` — TelemManager instance
- `G.sim_listeners` — SimListenerManager instance
- `G.main_window` — MainWindow instance
- `G.settings_mgr` — SettingsManager instance
- `G.system_settings` — Qt QSettings wrapper (`utils.SystemSettings`)
- `G.ipc_instance` — IPCNetworkThread instance
- `G.log_window` — LogWindow instance
- `G.exception_tracker` — ExceptionTracker instance
- `G.telemetry_tap` — TelemetryTap instance (ring buffer)
- `G.device_info`, `G.device_devpath`, `G.device_usbpid`, `G.device_ident`, `G.device_firmware_version`, `G.device_connection_status`
- `G.userconfig_path`, `G.defaults_path`, `G.userconfig_rootpath`
- `G.args` — CmdLineArgs parsed object
- `G.launched_instances` — dict mapping device type → ChildPopen
- `G.instance_dev_dict` — dict mapping PID → DeviceInfo
- `G.active_buttons`, `G.master_buttons`, `G.child_buttons`
- `G.startup_configurator_gains`, `G.vpconf_configurator_gains`, `G.current_configurator_gains`
- `G.gain_override_dialog` — ConfiguratorDialog instance
- `G.useDarkMode` — bool
- `G.dev_build`, `G.release_version`, `G.is_exe`
- `G.force_reload_aircraft_trigger` — bool
- `G.il2_ffb_device_ordinal` — int (for IL-2 Korea FFB routing)
- `G.vpconf_init_pending` — bool (async init gate)
- `G.current_vpconf_profile` — str path

See `dev_guidelines.md` for coding conventions.

---

### Configuration System (XML-based)
Two parallel XML files:
- `defaults.xml` — shipped defaults (~2400 lines, defines all available settings)
- `userconfig_v2.xml` — user overrides (in `%LOCALAPPDATA%\VPForce-TelemFFB\`)

`telemffb/xmlutils.py` (~2400 lines) handles all XML operations:
- `update_roots()` — re-parse both XML files into global ElementTree roots
- `update_vars(device, userconfig_path, defaults_path)` — set module-level paths
- `try_parse()` — retry parsing with delay (handles multi-instance file locking)
- `read_single_model()` — read settings for a given sim+aircraft+device
- `write_models_to_xml()` — write user overrides
- `get_pattern_by_sim_fullname()` — match aircraft name to config pattern
- `get_active_profile_for_model()` — resolve active profile
- `read_sc_overrides()` — read SimConnect/dataref variable overrides per aircraft

Config hierarchy: SIM → CLASS → MODEL → PROFILE (profile is optional overlay)
Call `xmlutils.update_roots()` after any XML changes. Watch for file locking in multi-instance scenarios.

`SettingsManager` wraps XML ops with state tracking, offline mode support, and PyQt signals.

---

## Critical Developer Workflows

### Building the Application
1. **Development mode**: `python main.py` (standard Python execution)
2. **Resource compilation**: `makeresources.bat` (generates `resources.py` from `resources.qrc`)
3. **Production build**: PyInstaller with `VPforce-TelemFFB.spec`
   - Bundles: X-Plane plugin, hidapi.dll, simconnect.dll, export scripts, defaults.xml, config.ini
   - Excludes: QtQuick, QtWebEngine, Bluetooth, WebSockets, etc. (aggressive trimming)
   - Output: `dist/VPforce-TelemFFB/`

### Running Tests
```bash
pytest                          # Run all tests
pytest tests/test_*.py -v      # Verbose output
pytest --cov=telemffb          # With coverage
```
- Test framework in `tests/framework/`:
  - `MockInputData` — mock device input (axes, buttons)
  - `MockFFBDevice` — mock FFB device
  - `MockConditionEffect` — mock haptic effect with configurable coefficients
  - `MockHapticEffect` — mock effect container
  - `MockEffectDispenser` — mock global effects dispenser
  - `MockSimConnect` — mock SimConnect for MSFS testing
  - `MockDampener` — pass-through dampening mock
  - `MockSpringCondition` — mock spring condition struct
  - `BaseTelemetryEffectTestCase` — base test class with full setup/teardown
- `conftest.py` — autouse fixture resets `G.effects` and `G.master_buttons`
- Markers: `unit`, `integration`, `msfs`, `xplane`, `joystick`, `pedals`, `collective`, `helicopter`, `slow`
- Warnings treated as errors (except DeprecationWarning)

### Multi-Instance Architecture
- **Master instance**: Auto-launches child instances for different devices
- **Child instances**: Handle their own device, send button/telemetry data to master
- **IPC communication**: UDP sockets on localhost (NOT ZMQ — changed from original design)
  - Master binds to random port, children connect to master's port via `--masterport`
  - Keepalive protocol: 1-second interval, 3-missed timeout triggers exit
  - Message types: `Keepalive`, `Child Keepalive:<dev>:<status>`, `telem:<json>`, `effects:<json>`, `MASTER_INSTANCE QUIT`, `RESTART SIMS`, `SHOW WINDOW`, `TOGGLE OFFLINE:`, `LOADCONFIG:`, `MASTER_BUTTONS:`, `BUTTONS:`
- Mutex: Win32 named mutex (`namedmutex.py`) prevents duplicate masters unless `G.allow_multi_instance = True`
- Auto-assign: Master inspects enumerated devices and assigns `devpath_*` settings by product string or VID:PID

---

## Code Conventions & Patterns

### Device Type Handling
`G.device_type` is one of: `"joystick"`, `"pedals"`, `"collective"`, `"trimwheel"`
- Affects which XML config sections are read
- Determines which effects are active (e.g., pedals skip joystick-only effects)
- MixIns use `self.is_joystick()`, `self.is_pedals()`, etc. for conditional logic

### Effect Lifecycle Pattern
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

### Telemetry Flow
1. Sim-specific listener receives raw data (shared memory, UDP, SimConnect)
2. Data normalized to semicolon-delimited string: `KEY1=val1~val2;KEY2=val3;...`
3. `TelemManager.submit_frame(data)` queues it on a condition variable
4. `TelemManager.run()` loop picks up data, parses into `BaseTelemetryData`
5. Frame timing calculated; IPC telemetry merged (master)
6. Aircraft resolved by name + source; new aircraft creates new instance
7. `aircraft.on_telemetry(telem_data)` processes per-MixIn chain
8. Effects updated on device via HID
9. TelemetryTap captures frame for analysis ring buffer
10. `telemetryReceived` signal emitted for UI updates

### Telemetry Timeout & Sim Exit Detection
- Timeout: configurable via `telemTimeout` system setting (default 200ms)
- On first timeout: `on_timeout()` fires once, stops periodic effects
- After 5s grace: `_check_sim_process()` polls OS process list every 5s
- If process gone: `notify_sim_exited()` fires, clears aircraft, restarts listeners
- Grace period prevents false exits during loading screens

---

## Key Files to Understand

| File | Purpose | Lines |
|------|---------|-------|
| `main.py` | 16-phase application bootstrap | ~1200 |
| `telemffb/globals.py` | Single source of truth for app state | ~158 |
| `telemffb/hw/ffb_rhino.py` | USB HID protocol, effect definitions, ctypes structs | ~1750 |
| `telemffb/hw/ffb_sdl.py` | **DEPRECATED** SDL haptic backend (kept for posterity) | ~228 |
| `telemffb/xmlutils.py` | All XML config read/write operations | ~2383 |
| `telemffb/utils.py` | Utilities: Dispenser, LowPassFilter, Dampener, SystemSettings, etc. | ~3570 |
| `telemffb/telem/TelemManager.py` | Telemetry routing, aircraft instantiation, sim exit detection | ~793 |
| `telemffb/telem/SimTelemListener.py` | SimListenerManager + per-sim listener classes | ~315 |
| `telemffb/sim/aircraft_base.py` | MixIn composition + base aircraft behavior | ~104 |
| `telemffb/sim/BaseTelemetryData.py` | Dict-backed telemetry container with typed attribute hints | ~1416 |
| `telemffb/sim/base/AircraftEffectUtilsBase.py` | Base utilities all MixIns inherit from | ~423 |
| `telemffb/sim/aircrafts_dcs.py` | DCS aircraft class + DCS command injection | ~695 |
| `telemffb/sim/aircrafts_il2.py` | IL-2 aircraft class + damage/buffet effects | ~348 |
| `telemffb/sim/aircrafts_msfs_xp.py` | Module exports for MSFS/XP aircraft | ~33 |
| `telemffb/sim/msfs_xp/Aircraft.py` | Base MSFS/XP aircraft class | ~210 |
| `telemffb/sim/msfs_xp/MsfsXpFBWFlightControlsMixIn.py` | FBW control forces + AP following | ~350 |
| `telemffb/sim/msfs_xp/MsfsXpFlightControlsMixIn.py` | Non-FBW stick forces | ~large |
| `telemffb/SettingsManager.py` | XML config manager, profiles, offline mode | ~237 |
| `telemffb/IPCNetworkThread.py` | UDP IPC between master/child instances | ~312 |
| `telemffb/MainWindow.py` | Main PyQt6 window, settings layout, status indicators | ~large |
| `telemffb/ExceptionTracker.py` | Error capture, logging handler, viewer dialog | ~378 |
| `telemffb/mcp/server.py` | MCP analysis server for LLM telemetry queries | ~339 |
| `telemffb/analysis/telemetry_tap.py` | Ring buffer tap for telemetry capture | ~79 |
| `telemffb/analysis/ring_buffer.py` | Circular buffer with pause/flush/window queries | ~medium |
| `telemffb/namedmutex.py` | Win32 named mutex (ctypes) | ~132 |
| `telemffb/CmdLineArgs.py` | CLI argument parser | ~131 |
| `telemffb/MainWindow.py` | Main UI window | ~large |
| `styles.py` | Light/dark mode QSS stylesheets | ~medium |

---

## When Making Changes

1. **Adding new effects**: Create MixIn in `telemffb/sim/base/` (generic) or `telemffb/sim/msfs_xp/` (sim-specific). Inherit from `AircraftEffectUtilsBase`. Add to `AircraftBase` MRO or aircraft subclass. Use `@override` decorator. Call `super().on_telemetry()`, `super().on_timeout()`, `super().on_event()` in each hook.

2. **New aircraft type**: Subclass from appropriate sim's `Aircraft` class. Override `on_telemetry()`. Register in `defaults.xml` under appropriate sim/class/model section. For MSFS, consider SimConnect category fallback.

3. **UI changes**: PyQt6 components in `telemffb/*.py` (MainWindow, dialogs). Use existing QSS from `styles.py`. Follow Fusion style conventions. All GUI updates from non-main threads must use `utils.schedule_on_main_thread(lambda: ...)`.

4. **Config changes**: Update both `defaults.xml` (new default values) and ensure XML reading logic in `xmlutils.py` handles the new keys. For enum settings, add to `SettingsManager` enums (e.g., `SpringModeEnum`, `GEffectModeEnum`). Ensure multi-instance safety (file locking via `try_parse`).

5. **Testing new features**: Add tests to `tests/`. Use `BaseTelemetryEffectTestCase` from `tests/framework/base.py`. Mark with appropriate pytest markers. Run `pytest` before committing.

6. **New telemetry fields**: Add typed attribute hint to `BaseTelemetryData` with docstring documenting sims, source, units. Populate in sim-specific listener/parser.

---

## Dependencies & External Integrations

### Runtime Dependencies (`requirements.txt`)
- `pyqt6==6.9.1` — UI framework (fully migrated from PyQt5)
- `pysimconnect` — MSFS integration (custom fork at github.com/walmis/pysimconnect)
- `libusb1` — USB device enumeration
- `numpy==2.3.0` — Math operations
- `akima` — Spline interpolation for curves
- `psutil` — Cross-platform process checking for sim exit detection
- `stransi` — ANSI color code parsing
- `pygetwindow` — Window focus detection (IL-2)
- `configobj` — INI config parsing

### Dev Dependencies (`requirements-dev.txt`)
- `mcp>=1.0.0` — Model Context Protocol server for telemetry analysis

### Binary Dependencies
- `dll/hidapi.dll` — HID API for direct USB communication
- `simconnect/simconnect.dll` — MSFS SimConnect SDK DLL
- `xplane-plugin/` — X-Plane telemetry export plugin (auto-installed)

---

## Common Pitfalls

1. **Don't use global variables as default args** — see `dev_guidelines.md` line 91-108
2. **Watch for XML file locking** in multi-instance scenarios — use `try_parse()` with retries
3. **Effect names must be unique** within an aircraft instance — `G.effects` is a dict-based Dispenser
4. **Always check `G.master_instance` vs `G.child_instance`** when implementing features that differ per instance type
5. **FFBRhino device communication is USB-latency sensitive** — batch HID updates when possible
6. **GUI updates from worker threads** — use `utils.schedule_on_main_thread()` or Qt signals
7. **MixIn `super()` chain** — always call `super().on_telemetry()`, `super().on_timeout()`, `super().on_event()`
8. **Telemetry data is dict-like** — use `telem_data.get('key', default)` for safe access, or dot-access for known fields
9. **Per-frame performance matters** — telemetry runs at 60-120Hz; avoid heavy computation in `on_telemetry()`
10. **IPC is UDP, not TCP** — messages may be dropped; don't assume delivery ordering
11. **`ffb_sdl.py` is deprecated** — do not use, kept for historical reference only

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
- Will this be called per-frame? Consider performance (60-120Hz telemetry rate)
- Does this modify XML? Ensure thread-safe via `try_parse()` and multi-instance safe
- Is this sim-specific behavior? Keep in sim-specific modules, not base classes
- Does this touch the GUI from a background thread? Use `schedule_on_main_thread()`
- Are all `super()` calls present in the MixIn chain?
- Is the effect name unique within the aircraft instance?