# TelemFFB Development Guide for AI Coding Agents

## Project Overview
TelemFFB is a Python/PyQt6 desktop application that generates force feedback (FFB) telemetry effects for flight simulator devices. It bridges telemetry data from simulators (DCS, MSFS, IL-2, X-Plane, BMS) to VPforce FFB hardware (Rhino joystick, DIY pedals, collective controls) via direct USB HID communication.

**Core concept**: Receive telemetry → Process through aircraft-specific effect classes → Send HID commands to FFB device.

## Architecture: The Big Picture

### Component Hierarchy
```
main.py (orchestrator)
├── TelemManager (thread) - Routes telemetry to aircraft instances
├── SimListenerManager - Manages sim-specific listeners (DCS, MSFS, IL2, etc.)
├── MainWindow (PyQt6) - UI and configuration
├── FFBRhino/HapticEffect - Direct USB HID device communication
└── IPCNetworkThread (ZMQ) - Multi-instance coordination
```

### Aircraft Effect System (MixIn Architecture)
Aircraft classes are **composed from MixIns** using multiple inheritance. Each MixIn implements a specific effect category:
- `AircraftBase` (in `telemffb/sim/aircraft_base.py`) chains together: `PedalSpringOverrideMixIn`, `HelicopterEffectsMixIn`, `WeaponsEffectMixIn`, `DeadzoneMixIn`, `HydraulicLossMixIn`, `DecelerationEffectMixIn`, `EngineRumbleMixIn`, `WindEffectMixIn`, `AdvancedSpringMixIn`, `MotionEffectsMixIn`, `BuffetingEffectMixIn`
- Sim-specific aircraft (e.g., `telemffb/sim/aircrafts_dcs.py::Aircraft`) inherit from `AircraftBase` and override methods like `on_telemetry()` and `on_event()`
- MixIns live in `telemffb/sim/base/` (generic) and `telemffb/sim/msfs_xp/` (sim-specific)

**All MixIns inherit from `AircraftEffectUtilsBase`** which provides shared utilities like `effects` property, `telem_data`, `check_button_press()`, etc.

### Global State Management (CRITICAL)
**Never use `global` keyword**. Instead, use `telemffb.globals` module:
```python
import telemffb.globals as G
# Access: G.device_type, G.master_instance, G.effects, G.telem_manager, etc.
```
All application-wide state lives here. See `dev_guidelines.md` for detailed rules.

### Configuration System (XML-based)
- Two parallel XML files: `defaults.xml` (shipped defaults) and `config.user.ini` (user overrides)
- `telemffb/xmlutils.py` (2300+ lines) handles all XML operations
- Aircraft configs are keyed by sim + aircraft name + device type (joystick/pedals/collective)
- Call `xmlutils.update_roots()` after XML changes
- **Watch for file locking**: Multi-instance support means concurrent XML access (see `config_has_changed()` in `TelemManager.py`)

## Critical Developer Workflows

### Building the Application
1. **Development mode**: `python main.py` (standard Python execution)
2. **Resource compilation**: `makeresources.bat` (generates `resources.py` from `resources.qrc`)
3. **Production build**: Uses PyInstaller with `VPforce-TelemFFB.spec`
   - Note: `main.spec` exists but `VPforce-TelemFFB.spec` is the active build spec
   - Build output goes to custom distpath (see spec file)

### Running Tests
```powershell
pytest                          # Run all tests
pytest tests/test_*.py -v       # Verbose output
pytest --cov=telemffb           # With coverage
```
- Test framework in `tests/framework/` provides `MockFFBDevice`, `MockSimConnect`, `MockEffectDispenser`
- Mark tests with: `@pytest.mark.unit`, `@pytest.mark.msfs`, `@pytest.mark.joystick`, etc. (see `pytest.ini`)
- Tests import from production code using: `from telemffb.sim.base.SomeMixIn import SomeMixIn`

### Multi-Instance Architecture
- **Master instance**: Auto-launches child instances for different devices (joystick + pedals + collective)
- **IPC communication**: ZMQ sockets on localhost (see `IPCNetworkThread.py`)
- Master broadcasts telemetry to children; children send button states to master
- Mutex checking prevents duplicate instances (see `namedmutex.py` and `G.allow_multi_instance`)

## Code Conventions & Patterns

### Device Type Handling
Device type stored in `G.device_type` as string: `"joystick"`, `"pedals"`, `"collective"`, `"trimwheel"`
- Affects which XML config sections are read
- Determines which effects are active (pedals don't get all joystick effects)

### Effect Lifecycle Pattern
```python
# Standard effect usage pattern in aircraft classes:
effect = self.effects.get("effect_name")  # Get from global dispenser
if not effect:
    effect = HapticEffect(effect_type=EFFECT_SINE)
    effect.start()  # Allocates on device
    self.effects.set("effect_name", effect)

effect.magnitude = calculate_magnitude(telem_data)
effect.start()  # Sends to device

# On timeout/cleanup:
effect.stop()  # Frees device resource
```

### Telemetry Flow
1. Sim-specific listener (`DcsIpcThread`, `SimConnectManager`, etc.) receives raw data
2. Normalizes to common format (see `BaseTelemetryData.py`)
3. `TelemManager.on_telemetry()` routes to appropriate aircraft instance
4. Aircraft's `on_telemetry(telem_data)` method processes and updates effects
5. Effects push HID commands via `FFBRhino.device`

### Common Pitfalls
1. **Don't use global variables as default args** (see `dev_guidelines.md` line 91-108)
2. **Watch for XML file locking** in multi-instance scenarios
3. **Effect names must be unique** within an aircraft instance (effects dispenser uses dict)
4. **Always check `G.master_instance` vs `G.child_instance`** when implementing features that differ per instance type
5. **FFBRhino device communication is USB-latency sensitive** - batch HID updates when possible

## Sim-Specific Notes

### MSFS/X-Plane (no native FFB)
- TelemFFB fully implements FFB control forces
- SimConnect integration via `pysimconnect` (custom fork)
- Flight control simulation in `MsfsXpFlightControlsMixIn` and `MsfsXpFBWFlightControlsMixIn`
- X-Plane uses separate plugin for telemetry export (auto-installed by TelemFFB)

### DCS (has native FFB)
- TelemFFB augments native FFB with additional effects
- IPC communication via shared memory (`DcsIpcThread`)
- Can override DCS spring forces for specific aircraft
- Trim following implementation for select aircraft

### IL-2 (has native FFB)
- Similar augmentation approach to DCS
- Uses shared memory telemetry (`IL2Manager.py`)
- Damage effects and aircraft-specific rumble

## File Organization Principles
- **Large classes get their own files** (see `dev_guidelines.md`)
- Aircraft effect MixIns: One file per MixIn in `telemffb/sim/base/` or `telemffb/sim/msfs_xp/`
- Sim-specific aircraft: `aircrafts_dcs.py`, `aircrafts_il2.py`, `aircrafts_msfs_xp.py`
- Hardware layer: `telemffb/hw/ffb_rhino.py` (1200+ lines of USB HID logic)

## Key Files to Understand
- `main.py`: Application bootstrap and initialization sequence (see docstring)
- `telemffb/telem/TelemManager.py`: Telemetry routing and aircraft instantiation
- `telemffb/hw/ffb_rhino.py`: USB HID protocol implementation, effect definitions
- `telemffb/xmlutils.py`: All configuration read/write operations
- `telemffb/globals.py`: Single source of truth for application state
- `telemffb/sim/aircraft_base.py`: MixIn composition and base aircraft behavior

## When Making Changes
1. **Adding new effects**: Create MixIn in `telemffb/sim/base/`, inherit from `AircraftEffectUtilsBase`, add to `AircraftBase` inheritance chain
2. **New aircraft type**: Subclass from `Aircraft` in appropriate sim module, override `on_telemetry()`, register in XML config
3. **UI changes**: PyQt6 components in `telemffb/*.py` (MainWindow, DevicePanel, etc.), use existing styles from `styles.py`
4. **Config changes**: Update both `defaults.xml` and XML reading logic in `xmlutils.py`, ensure multi-instance safety
5. **Testing new features**: Add tests to `tests/`, use mock framework, mark appropriately

## Dependencies & External Integrations
- **PyQt6**: UI framework (migration from PyQt5 in progress - see `makeresources.bat` uses `pyrcc5`)
- **libusb1/hidapi**: USB device communication (DLLs in `dll/` directory)
- **SimConnect**: MSFS integration (custom fork at github.com/walmis/pysimconnect)
- **numpy/akima**: Math operations for curves and interpolation

## Questions to Ask When Reviewing Code
- Does this need access to global state? Use `telemffb.globals as G`
- Is this effect device-type specific? Check `G.device_type`
- Could this run in a child instance? Check master/child state
- Will this be called per-frame? Consider performance (60-120Hz telemetry rate)
- Does this modify XML? Ensure thread-safe and multi-instance safe
- Is this sim-specific behavior? Keep in sim-specific modules, not base classes
