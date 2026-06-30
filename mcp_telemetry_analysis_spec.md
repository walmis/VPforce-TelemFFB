# MCP Telemetry Analysis Specification

## Overview

This document specifies a local-first Model Context Protocol (MCP) server for interactive telemetry analysis in TelemFFB. The MCP server exposes bounded telemetry queries, configuration state, and FFB effect state so an LLM can inspect current or recent simulator state without participating in the real-time force feedback control loop.

Primary example workflow:

- User prompt: "Wind is coming about 30 degrees from the left side. Analyze the AoA signal and suggest improvements."
- LLM tool calls:
  1. `get_current_state` — see sim, aircraft, device, and current telemetry snapshot
  2. `list_available_signals` — discover which signals this sim provides
  3. `get_telemetry_window` — pull bounded AoA + RelWind + Roll history
  4. `get_current_config` — see active XML tuning parameters and their values
  5. `get_effect_state` — inspect current FFB output (springs, dampers, turbulence)
- Result: the LLM reasons over telemetry data + config context to explain observed AoA behavior and suggest specific parameter changes.

**Design philosophy**: The MCP layer is a *data provider*, not an *analysis engine*. It exposes raw telemetry, config state, and effect state through bounded queries. The LLM performs all reasoning, correlation, and suggestion logic. This avoids maintaining fragile heuristic rules in Python that must evolve with every effect change.

## Goals

- Provide an MCP tool interface for on-demand telemetry and config inspection.
- Keep the real-time FFB path deterministic and isolated from LLM latency.
- Expose normalized telemetry from `BaseTelemetryData` rather than sim-specific raw payloads.
- Support both current-state queries and bounded historical analysis.
- Expose active configuration context (XML parameters, effect state) so the LLM can reason about tuning.
- Let the LLM be the reasoning engine — the MCP layer provides data, not advice.
- Make the system local-first for latency, privacy, and offline operation.

## Non-Goals

- The MCP server must not sit in the per-frame FFB control loop.
- The MCP server must not stream raw 60-120 Hz telemetry directly to the LLM indefinitely.
- The LLM must not auto-apply XML or profile changes without explicit user approval.
- The first version does not need full persistent telemetry recording across entire flights.
- The first version does not need autonomous closed-loop tuning.
- The MCP layer does not need to compute derived metrics or heuristic suggestions — the LLM handles reasoning.

## Design Principles

- Separate hard real-time control from data exposure for analysis.
- The MCP layer is a **data provider**: telemetry snapshots, config state, effect state.
- The LLM is the **reasoning engine**: it computes metrics, correlations, and suggestions from the data.
- Use bounded windows to reduce token cost. Let the LLM request what it needs.
- Keep the telemetry schema sim-agnostic at the MCP boundary.
- Preserve TelemFFB conventions, especially `BaseTelemetryData` and `telemffb.globals`.

## System Context

### Existing TelemFFB Flow

Current data flow:

```text
Sim Listener -> TelemManager -> BaseTelemetryData -> Aircraft MixIns -> Haptic Effects -> USB HID
```

Proposed analysis flow:

```text
Sim Listener -> TelemManager -> BaseTelemetryData -> Telemetry Tap -> Analysis Cache/Ring Buffer -> MCP Server -> LLM
```

## High-Level Architecture

### 1. Telemetry Tap

A lightweight telemetry tap captures normalized telemetry frames from the TelemManager processing pipeline.

**Tap placement**: The tap should be placed **after** `aircraft.on_telemetry()` completes and **before** `_emit_telemetry()`, inside `TelemManager.process_data()`. This is the optimal insertion point because:

- All sim-normalized fields from `_parse_telemetry_data()` are present.
- MixIn-computed fields (`_buffeting`, `decel_g`, `_elev_coeff`, `ForceXY`, etc.) have been injected.
- Effects have been dispatched to the device, so effect state on the aircraft instance is current.
- The telemetry is still on the TelemManager thread (no cross-thread synchronization needed for append).

**NOT recommended**: Tapping before `aircraft.on_telemetry()` loses all computed/derived fields. Tapping after `_emit_telemetry()` moves to the Qt event loop thread and adds synchronization complexity.

Responsibilities:

- Receive the fully-enriched `BaseTelemetryData` frame (post-MixIn processing).
- Copy all fields via `telem_data.to_dict()` (typically 40-80 keys — lightweight).
- Skip frames when `SimPaused` is set or during `on_timeout()` to avoid filling the buffer with stale data.
- Flush the buffer on aircraft change events (`_handle_aircraft_changes()` triggers).
- Push frames into a bounded ring buffer via simple `list.append()` (GIL-safe, no lock needed).

### 2. Analysis Cache

A local cache stores recent telemetry in memory.

Recommended initial behavior:

- High-rate ring buffer: last 30-60 seconds at native frame rate.
- Downsampled ring buffer: last 5-10 minutes at adaptive rate. Note: source telemetry rates vary by sim (DCS ~30-60Hz, MSFS ~30-60Hz, IL-2 ~30Hz, BMS ~30Hz). Downsampling to a fixed 20Hz from a 30Hz source loses significant information. Use a decimation factor (e.g., keep every Nth frame) rather than a fixed target Hz. A factor of 3-4 from the native rate is reasonable for the low-rate buffer.
- Optional persistent session log: SQLite or DuckDB for later replay.

### 3. MCP Server

The MCP server exposes telemetry, config, and effect state to an LLM via bounded tool calls.

Responsibilities:

- Accept structured tool calls.
- Query the analysis cache (ring buffer).
- Query active config via `xmlutils`.
- Query effect state via `G.effects` and aircraft MixIn attributes.
- Return compact structured payloads.
- Never block the real-time telemetry pipeline.

### 4. LLM Client

The LLM uses MCP tools to investigate user questions. It reasons over bounded telemetry windows, config context, and effect state to compute metrics, identify patterns, and suggest tuning changes.

The LLM is responsible for:
- Computing derived metrics (means, stddev, rates of change) from telemetry windows.
- Correlating signals (e.g., relating crosswind angle to AoA excursions).
- Interpreting flight conditions from user descriptions.
- Suggesting tuning changes based on config parameters and observed telemetry.

This separation keeps the MCP layer simple and maintenance-free while leveraging the LLM's reasoning capabilities.

## Integration Points In This Repository

### Primary Hook Point

Recommended insertion point:

- `telemffb/telem/TelemManager.py`, inside `process_data()`, between `_process_current_aircraft_telemetry()` and `_handle_ipc_and_plotting()`.

Specifically, after line where `aircraft.on_telemetry(telem_data)` returns and before `_handle_ipc_and_plotting(telem_data)` is called. At this point:

- `BaseTelemetryData` is fully constructed with all sim-normalized fields.
- MixIn-computed fields (`_buffeting`, `decel_g`, `_elev_coeff`, `ForceXY`, etc.) have been injected by the aircraft's `on_telemetry()` chain.
- The tap runs on the TelemManager daemon thread with no contention (single-threaded processing).
- `telem_data.to_dict()` provides a snapshot safe for ring buffer storage.

**Thread safety note**: Writing to the ring buffer is GIL-safe via `list.append()`. However, MCP tool handlers reading the buffer from a separate thread require either:
- A `threading.Lock` around read operations (low contention—reads are infrequent vs 30-120Hz writes), or
- A copy-on-read strategy where the MCP handler copies the buffer slice under a brief lock.

The existing `telemetryReceived` Qt signal (emitted in `_emit_telemetry()`) could serve as an alternative tap point for components already running on the Qt event loop, but is not recommended for the ring buffer due to thread-crossing overhead.

### Relevant Existing Types and Modules

- `telemffb/sim/BaseTelemetryData.py`
  - Canonical normalized telemetry schema.
- `telemffb/sim/base/AircraftEffectUtilsBase.py`
  - Existing telemetry consumers; should remain separate from MCP logic.
- `telemffb/globals.py`
  - Global app state for device type, managers, and runtime configuration.
- `telemffb/xmlutils.py`
  - Future source of profile/config context for tuning suggestions.

### Proposed New Modules

```text
telemffb/analysis/
    __init__.py
    signal_registry.py        # AST-based signal metadata extraction (prototype complete)
    telemetry_tap.py          # Frame capture and queueing
    ring_buffer.py            # Bounded in-memory storage
    context_provider.py       # Active sim/profile/device context
    schemas.py                # Analysis request/response dataclasses

telemffb/mcp/
    __init__.py
    server.py                 # MCP server bootstrap
    tools.py                  # MCP tool handlers
    adapters.py               # Converts internal types to MCP payloads
```

## Telemetry Data Model

### Dynamic Schema — No Fixed Field Set

`BaseTelemetryData` is a **dynamic dict-backed container**, not a fixed dataclass. Fields are populated at runtime by sim listeners and MixIns. This means:

- Available fields change per sim, per aircraft, per device type, and even per frame.
- The ring buffer cannot use fixed-size structs—it must store variable-key snapshots.
- Any tool that queries signals must handle missing fields gracefully, returning `null` or omitting the key rather than erroring.

### Signal Self-Discovery via Docstrings

`BaseTelemetryData` (in `telemffb/sim/BaseTelemetryData.py`) carries **per-field docstrings** that document sim availability, units, source API, and semantics for every annotated attribute. These docstrings follow PEP 257 attribute docstring style and can be extracted at runtime via `BaseTelemetryData.__annotations__` (field names + types) combined with AST inspection of trailing string literals.

The `explain_signal` tool should dynamically extract these docstrings rather than maintaining a separate static metadata registry. This guarantees that signal documentation stays in sync with the code.

Additionally, the LLM can call `get_current_state` to see which fields are **actually populated** in the current frame (via `BaseTelemetryData.keys()`), then call `explain_signal` on any field it needs to understand.

### Frame Envelope

Each cached frame should include:

```json
{
  "timestamp": 1712779200.123,
  "sim": "MSFS",
  "aircraft": "C172",
  "device_type": "joystick",
  "profile": "default",
  "fields": {
    "AoA": 4.2,
    "RelWind": [12.1, -0.8, 48.3],
    "Roll": 2.1,
    "YawRate": -0.4
  }
}
```

### Signal Availability Matrix

Signal availability varies by simulator. The MCP layer must not assume universal availability. The following matrix documents the core analysis signals:

| Signal | DCS | MSFS | IL-2 | BMS | X-Plane | Notes |
|--------|-----|------|------|-----|---------|-------|
| **Airspeed** | | | | | | |
| `TAS` | ✓ m/s | ✓ m/s | ✓ m/s | ✓ m/s | ✓ m/s | Universal |
| `IAS` | ✓ m/s | ✓ m/s | ✓ m/s | ✓ m/s | ✓ m/s | Universal |
| `Mach` | ✓ | ✓ | ✗ | ✓ | ✓ | |
| **Attitude** | | | | | | |
| `Pitch` | ✓ deg | ✓ deg | ✗ | ✗ | ✓ deg | IL-2/BMS lack attitude |
| `Roll` | ✓ deg | ✓ deg | ✗ | ✗ | ✓ deg | IL-2/BMS lack attitude |
| `Heading` | ✓ deg | ✓ deg | ✗ | ✗ | ✓ deg | IL-2/BMS lack heading |
| **Aero angles** | | | | | | |
| `AoA` | ✓ deg | ✓ deg | ✗ | ✓ deg | ✓ deg | IL-2 does not expose AoA |
| `SideSlip` | ✓ deg | ✓ deg | ✗ | ✗ | ✗ | DCS/MSFS only |
| **G-loads** | | | | | | |
| `ACCs` | ✓ [x,y,z] g | ✓ [x,y,z] g | ✓ [x,y,z] g | ✓ [x,y,z] g | ✗ | BMS: only normal G |
| `G` | via ACCs[1] | ✓ | via ACCs[1] | ✓ | ✓ | Extraction method varies |
| **Wind** | | | | | | |
| `AmbWind` | ✓ [x,y,z] m/s | ✓ [x,y,z] m/s | ✗ | ✗ | ✗ | DCS/MSFS only |
| `RelWind` | ✓ [lon,vert,lat] | ✓ [lat,vert,lon] | ✗ | ✗ | ✗ | **Axis order differs!** |
| **Ground state** | | | | | | |
| `SimOnGround` | ✓ | ✓ | ✓ | ✓ | ✓ | Universal |
| `WeightOnWheels` | ✓ | ✓ | ✓ | ✓ | ✓ | Universal |
| **Position** | | | | | | |
| `AGL` | ✓ m | ✓ m | ✓ m | ✓ m | ✓ m | Universal |
| `MSL` | ✓ m | ✓ m | ✗ | ✓ m | ✓ m | IL-2 lacks MSL |
| **Control surfaces** | | | | | | |
| `ElevDeflPct` | ✗ | ✓ | ✗ | ✗ | ✗ | MSFS only |
| `AileronDeflPctLR` | ✗ | ✓ | ✗ | ✗ | ✗ | MSFS only |
| `RudderDeflPct` | ✗ | ✓ | ✗ | ✗ | ✗ | MSFS only |
| **Control inputs** | | | | | | |
| `StickX`/`StickY` | ✗ | via sc_overrides | ✗ | ✗ | ✗ | MSFS only, dynamic subscription |
| `CollectivePos` | ✓ | ✓ | ✗ | ✗ | ✓ | Helicopters only |
| **Trim** | | | | | | |
| `ElevTrimPct` | ✗ | ✓ | ✗ | ✗ | ✗ | MSFS only |
| `AileronTrimPct` | ✗ | ✓ | ✗ | ✗ | ✗ | MSFS only |
| `RudderTrimPct` | ✗ | ✓ | ✗ | ✗ | ✗ | MSFS only |
| **Engine** | | | | | | |
| `EngRPM` | ✓ % | ✓ % | ✓ % | ✓ % | ✓ % | Universal (per engine) |
| **Speed limits** | | | | | | |
| `Vne` | ✗ | ✓ computed | ✗ | ✗ | ✓ dataref | MSFS/XP only |
| `StallAoA` | ✗ | ✓ | ✗ | ✗ | ✗ | MSFS only |
| **Autopilot** | | | | | | |
| `APMaster` | ✓ | ✓ | ✗ | ✗ | ✗ | DCS/MSFS only |
| `APServos` | ✗ | ✓ | ✗ | ✗ | ✓ | MSFS/XP only |

**Critical normalization requirement**: `RelWind` axis order differs between sims. DCS uses `[longitudinal, vertical, lateral]` while MSFS/XP uses `[lateral, vertical, longitudinal]`. The telemetry tap **must normalize to a canonical order** before caching. Recommend adopting `[lateral, vertical, longitudinal]` (MSFS/XP convention) as the MCP canonical form, with DCS values permuted on ingestion.

### MixIn-Computed Fields Available After `on_telemetry()`

Aircraft MixIns inject computed analysis-ready fields into `telem_data` during their `on_telemetry()` processing. These are available if the tap is placed **after** `aircraft.on_telemetry()` completes:

| Field | Source MixIn | Description |
|-------|-------------|-------------|
| `_buffeting` | `BuffetingEffectMixIn` | Buffeting magnitude (0.0–1.0) |
| `_pct_max_stall_buffet` | `BuffetingEffectMixIn` | Buffet percentage of stall max |
| `decel_g` | `DecelerationEffectMixIn` | Raw deceleration in Gs |
| `decel_g_smooth` | `DecelerationEffectMixIn` | Smoothed deceleration |
| `_hyd_factor` | `HydraulicLossMixIn` | Hydraulic system integrity (0.0–1.0) |
| `_elev_coeff` | `MsfsXpFlightControlsMixIn` | Elevator spring coefficient (MSFS/XP) |
| `_aile_coeff` | `MsfsXpFlightControlsMixIn` | Aileron spring coefficient (MSFS/XP) |
| `_rud_coeff` | `MsfsXpFlightControlsMixIn` | Rudder spring coefficient (MSFS/XP) |
| `ForceXY` | `AircraftBase` | Current device force output [x, y] |
| `JoyXY` | `AircraftBase` | Current device axis position [x, y] |
| `_controls_locked` | `MsfsXpFlightControlsMixIn` | Flight controls locked state |
| `frameTimes` | `TelemManager` | [current_ms, max_ms] frame timing |

The MCP layer should **expose these existing computed values** as part of `get_current_state` telemetry snapshots, giving the LLM access to pre-processed effect state alongside raw sim signals.

### Recommended Initial Signal Set

Rather than hardcoding a fixed signal list, Phase 1 should:

1. Cache **all fields** present in `BaseTelemetryData.keys()` per frame (the set is typically 40-80 keys, small enough for in-memory storage).
2. Use `BaseTelemetryData._known_fields()` plus runtime `keys()` to enumerate available signals.
3. Let the LLM discover availability dynamically via `get_current_state` and `explain_signal`.

For cross-sim analysis, the LLM should be aware of signal availability:

- Universal (all 5 sims): `TAS`, `IAS`, `AGL`, `SimOnGround`, `WeightOnWheels`, `EngRPM`, `ACCs`
- DCS + MSFS + XP (3 sims): `Pitch`, `Roll`, `Heading`, `AoA`
- MSFS/XP only: control deflections, trim, speed limits, `Vne`, `StallAoA`
- DCS/MSFS only: wind vectors (`AmbWind`, `RelWind`), `SideSlip`

The `list_available_signals` and `explain_signal` tools provide this information dynamically.

Context (always available):

- `src` (sim name)
- `N` (aircraft name)
- `FFBType` (device type)
- active profile from `G.settings_mgr`

## MCP Tool Specification

### Tool: `get_current_state`

Returns a compact snapshot of current aircraft and telemetry context.

Input:

```json
{}
```

Output:

```json
{
  "sim": "MSFS",
  "aircraft": "C172",
  "device_type": "joystick",
  "profile": "default",
  "telemetry": {
    "AoA": 4.2,
    "IAS": 98.0,
    "Roll": 1.3,
    "YawRate": -0.2,
    "RelWind": [10.0, -0.6, 51.0]
  }
}
```

### Tool: `get_telemetry_window`

Returns a bounded historical window for selected signals.

Input:

```json
{
  "signals": ["AoA", "RelWind", "Roll", "YawRate"],
  "seconds": 8,
  "sample_hz": 20
}
```

Output:

```json
{
  "start_ts": 1712779192.100,
  "end_ts": 1712779200.100,
  "sample_hz": 20,
  "series": {
    "AoA": [3.8, 4.1, 4.3],
    "RelWind": [[8.5, -0.4, 49.0], [9.2, -0.6, 50.1], [10.0, -0.6, 51.0]],
    "Roll": [0.5, 0.8, 1.3],
    "YawRate": [-0.1, -0.2, -0.2]
  }
}
```

### Tool: `get_current_config`

Returns the active XML configuration parameters for the current aircraft/sim/device combination. Uses `xmlutils.read_single_model()` to resolve the full config hierarchy (sim defaults → class defaults → user overrides → model-specific profile).

This exposes the tuning knobs the LLM can reason about when suggesting parameter changes.

Input:

```json
{
  "filter_group": "Dynamic Forces"
}
```

`filter_group` is optional. If omitted, returns all active parameters. Common groups: `"Dynamic Forces"`, `"Spring Center"`, `"Fly-By-Wire"`.

Output:

```json
{
  "sim": "MSFS",
  "aircraft": "C172",
  "aircraft_class": "PropellerAircraft",
  "device_type": "joystick",
  "profile": "default",
  "parameters": [
    {
      "name": "spring_mode",
      "value": "ADVANCED",
      "datatype": "d_enumlist",
      "valid_values": ["BASIC", "ADVANCED", "CENTER", "FBW"],
      "group": "Spring Mode",
      "description": "Selects the force feedback spring simulation mode."
    },
    {
      "name": "max_aileron_coeff",
      "value": 80,
      "datatype": "d_float",
      "valid_range": [0, 100],
      "group": "Dynamic Forces",
      "description": "Maximum aileron spring coefficient at Vne (% of device max).",
      "unit": "%"
    },
    {
      "name": "aileron_expo",
      "value": 0.0,
      "datatype": "d_float",
      "valid_range": [-3, 5],
      "group": "Dynamic Forces",
      "description": "Exponential curve shape for aileron force vs airspeed."
    }
  ],
  "total_parameters": 24,
  "note": "Only parameters applicable to this sim/device/aircraft class are shown. Prerequisites are pre-filtered."
}
```

### Tool: `get_effect_state`

Returns current FFB effect state for the active aircraft instance. This exposes the output side of the TelemFFB pipeline.

**Implementation constraints**: Effect parameters are stored in ctypes structures (`FFBReport_SetCondition`, `FFBReport_SetPeriodic`, etc.) on `HapticEffect` instances within the global `G.effects` Dispenser. Some parameters are also cached as instance attributes on MixIns (e.g., `HydraulicLossMixIn.damper_coeff`).

Not all effect internals are currently exposed via a clean API. Phase 1 should expose:
- Effect names and types (via `G.effects` iteration)
- Started/stopped status (via `effect.started`)
- MixIn-level state attributes (listed below)

Phase 2 should add:
- Spring coefficient readback (from `DynamicSpringMixin.spring_x/y` ctypes structs)
- An `EffectStateCollector` that snapshots effect parameters after each `on_telemetry()` cycle

Input:

```json
{}
```

Output:

```json
{
  "device_type": "joystick",
  "active_effects": [
    {"name": "spring_xy", "type": "EFFECT_SPRING", "started": true},
    {"name": "damper", "type": "EFFECT_DAMPER", "started": true},
    {"name": "turbulence", "type": "EFFECT_SINE", "started": true},
    {"name": "buffeting", "type": "EFFECT_SINE", "started": false}
  ],
  "mixin_state": {
    "hydraulic_factor": 1.0,
    "damper_coeff": 0,
    "friction_coeff": 0,
    "inertia_coeff": 0,
    "gforce_current_factor": 0.32,
    "spring_mode": "ADVANCED",
    "buffeting_magnitude": 0.0,
    "decel_g_smooth": 0.05
  }
}
```

### Tool: `explain_signal`

Returns metadata for a signal by extracting the docstring from `BaseTelemetryData`'s annotated attributes. This ensures documentation stays in sync with the code automatically.

**Implementation**: Parse `BaseTelemetryData.__annotations__` for type info and extract the corresponding attribute docstring (PEP 257 trailing string literal) via AST inspection of `BaseTelemetryData.py`. Cache the result at server startup.

For signals not annotated on `BaseTelemetryData` (dynamic keys injected by MixIns like `_buffeting` or `decel_g`), fall back to a supplementary registry in the analysis module.

Input:

```json
{
  "signal": "RelWind"
}
```

Output:

```json
{
  "signal": "RelWind",
  "type": "Optional[List[float]]",
  "description": "Relative wind vector in aircraft body frame.",
  "docstring": "DCS: [longitudinal fwd+, vertical up+, lateral stbd+] m/s.\nMSFS: RELATIVE WIND VELOCITY BODY X/Y/Z — [lateral stbd+, vertical up+, longitudinal fwd+] m/s.\nIL2/BMS/XP: not available.",
  "available_sims": ["DCS", "MSFS"],
  "units": "m/s",
  "notes": [
    "CRITICAL: Axis order differs between DCS and MSFS. The MCP layer normalizes to [lateral, vertical, longitudinal] before caching."
  ]
}
```

### Tool: `list_available_signals`

Returns the list of signals currently populated in the telemetry stream, grouped by category. This is how the LLM discovers what data is available for the current sim/aircraft combination.

Input:

```json
{}
```

Output:

```json
{
  "sim": "MSFS",
  "aircraft": "C172",
  "device_type": "joystick",
  "total_fields": 72,
  "fields": ["AoA", "IAS", "TAS", "Pitch", "Roll", "Heading", "AGL", "MSL", "..."],
  "computed_fields": ["_buffeting", "_elev_coeff", "_aile_coeff", "decel_g", "ForceXY", "JoyXY"],
  "note": "Use explain_signal to get documentation for any field."
}
```

### Tool: `render_plot` (Phase 2)

Renders a time-series plot of selected signals from the telemetry ring buffer and returns it as a base64-encoded PNG image (MCP `ImageContent`). Enables visual analysis — the LLM can describe observed patterns and the user sees them directly.

**Implementation**: Use PyQt6's `QtCharts` module to render to `QPixmap` → PNG bytes without displaying a window. Since Qt rendering must happen on the main thread, the MCP handler should use a signal/slot pattern (same approach as `telemetryReceived` Qt signal) to request rendering and wait for the result. This avoids adding matplotlib as a dependency.

Input:

```json
{
  "signals": ["AoA", "Roll"],
  "seconds": 10,
  "plot_type": "timeseries",
  "width": 800,
  "height": 400
}
```

`plot_type` options: `"timeseries"` (default), `"xy_scatter"` (first signal vs second). Width/height in pixels, capped at 1200×800 to control token cost.

Output:

MCP `ImageContent` with base64-encoded PNG. Example structured response:

```json
{
  "type": "image",
  "mimeType": "image/png",
  "data": "<base64-encoded PNG>",
  "metadata": {
    "signals": ["AoA", "Roll"],
    "time_range": [1712779190.0, 1712779200.0],
    "sample_count": 300,
    "note": "AoA (left axis, deg), Roll (right axis, deg)"
  }
}
```

## LLM Interaction Pattern

Recommended query flow:

1. Call `get_current_state` to identify sim, aircraft, device, and see current telemetry values.
2. Call `list_available_signals` to discover which signals the current sim actually provides.
3. Call `explain_signal` for any signal the LLM needs to interpret (units, conventions, sim-specific notes).
4. Pull a bounded telemetry window for relevant signals via `get_telemetry_window`.
5. Call `get_effect_state` to inspect FFB output state if relevant.
6. Call `get_current_config` to see active tuning parameters and their values.
7. Optionally call `render_plot` to visualize signal trends — the image helps both the LLM and user understand patterns.
8. The LLM **computes its own metrics** from the telemetry window (means, stddev, rates of change, correlations).
9. The LLM **reasons about tuning** based on observed telemetry + config context, referencing actual parameter names from `get_current_config`.
9. Present findings with explicit confidence, signal availability caveats, and suggested parameter changes.

The LLM should avoid guessing aerodynamic relationships from the prompt alone. It should always inspect telemetry first. When a signal is unavailable for the current sim, the LLM should explain this to the user rather than guessing.

## Safety and Performance Requirements

### Hard Requirements

- No MCP tool may block telemetry delivery or HID output.
- No analysis request may execute on the real-time thread.
- Every telemetry query must be bounded by duration and signal set.
- Any configuration or tuning change must require explicit user approval.

### Performance Targets

Initial targets:

- Telemetry tap overhead: less than 1 ms per frame in the hot path.
- MCP tool response for current state: less than 50 ms local.
- MCP tool response for short-window metrics: less than 200 ms local.
- Memory usage: bounded by configured buffer sizes.

### Failure Handling

If the MCP layer fails:

- TelemFFB real-time control must continue unaffected.
- MCP tools should return degraded but explicit errors such as:
  - telemetry unavailable
  - insufficient samples
  - signal not present for current sim

## Tuning Workflow

The MCP layer does **not** include a heuristic suggestion engine. Instead, the LLM performs all tuning reasoning:

1. The LLM calls `get_current_config` to discover available parameters, current values, and valid ranges.
2. The LLM calls `get_telemetry_window` and `get_effect_state` to observe current behavior.
3. The LLM correlates telemetry with config and suggests specific parameter changes, referencing actual XML parameter names.
4. Any configuration change must require explicit user approval before being applied.

Future versions may add a `propose_config_patch` tool that generates but does not apply XML changes, allowing a review-then-apply workflow.

## Multi-Instance Architecture

TelemFFB runs separate OS processes for each device type (joystick, pedals, collective). The **master instance** auto-launches child instances and broadcasts telemetry to them via ZMQ IPC. Each instance has its own `TelemManager`, aircraft instance, and effect set.

### MCP Server Placement

The MCP server should run **only on the master instance** (`G.master_instance == True`). Rationale:

- The master receives all raw telemetry and merges child IPC data via `_merge_ipc_telemetry()`.
- Running MCP per-instance would create multiple conflicting servers.
- Effect state for child instances can be queried via the existing IPC channel if needed in the future.

### Cross-Instance Effect Queries

Phase 1: The MCP server reports effect state only for the master's device type (typically joystick). The response should include `"device_type": "joystick"` to make this explicit.

Phase 2 (optional): Extend IPC to allow the master MCP server to query child instances' effect state on demand. This would enable correlated joystick + pedals analysis.

### Instance Detection

At startup, check `G.master_instance` before initializing the MCP server. If running as a child instance, skip MCP initialization entirely.

## Telemetry Lifecycle Events

The MCP layer must handle these state transitions:

### Aircraft Change

When `_handle_aircraft_changes()` detects a new aircraft:
- Flush the ring buffer (old aircraft data is irrelevant).
- Reset derived metrics accumulators.
- Update cached config context (new XML parameters, profile, aircraft class).
- `get_current_state` should return immediately with the new context.

### Sim Pause / Timeout

When `on_timeout()` fires (200ms no data) or `SimPaused` is detected:
- Stop appending to the ring buffer (avoid filling with stale copies).
- Mark the buffer's last-valid timestamp.
- `get_telemetry_window` should return data up to the pause timestamp with a `"paused": true` flag.

### Config Hot-Reload

When `_handle_config_changes()` detects XML modification:
- Invalidate any cached config state.
- Next `get_current_config` call should re-read via `xmlutils.read_single_model()`.

### SimConnect Dynamic Subscriptions

MSFS signals like `ControlsLock`, `StickX`, `StickY` are **dynamically subscribed** per aircraft via `sc_overrides`. They may not appear in telemetry until the aircraft is loaded and overrides are configured. The MCP layer must not assume all MSFS fields are present from the first frame.

## Security and Privacy

- Default to localhost-only operation.
- Keep telemetry local by default.
- Make any cloud-backed LLM integration opt-in.
- Expose only the minimum data needed for analysis.

## Phased Implementation Plan

### Phase 1: Core Data Provider (~2-3 days)

- Add telemetry tap after `aircraft.on_telemetry()` in `TelemManager.process_data()`.
- Build in-memory ring buffer with aircraft-change flush and pause-detection.
- Build docstring extraction from `BaseTelemetryData.py` (AST parse at startup, cache results). **Prototype complete**: see `telemffb/analysis/signal_registry.py`.
- Implement tools: `get_current_state`, `get_telemetry_window`, `list_available_signals`, `explain_signal`.
- MCP server bootstrap (using `mcp` Python SDK), runs only on master instance (`G.master_instance` check).
- No persistent storage.

### Phase 2: Config, Effect State, and Visualization (~1-2 days)

- Implement `get_current_config` backed by `xmlutils.read_single_model()`.
- Implement `get_effect_state` — enumerate `G.effects`, read MixIn state attributes (`hydraulic_factor`, `damper_coeff`, `gforce_current_factor`, etc.).
- Implement `render_plot` — QtCharts rendering via signal/slot to main thread, returns base64 PNG.
- Add config hot-reload detection (invalidate cached config on XML change).

### Phase 3 (optional): Persistence and Replay

- Add SQLite session store for post-flight analysis.
- Support replay-style telemetry queries over longer windows.
- Only if demand materializes from users.

### Phase 4 (optional): UI Integration

- Surface MCP-backed analysis summaries in the GUI.
- Add review-then-apply config patch workflow.
- Only if demand materializes.

## Open Questions

### Resolved by Audit

- **Which normalized telemetry fields are guaranteed across all target sims?** → See Signal Availability Matrix above. Only `TAS`, `IAS`, `AGL`, `SimOnGround`, `WeightOnWheels`, `EngRPM`, and `ACCs` are universal. Attitude and wind signals are sim-dependent.
- **Which profile/settings values are most useful for tuning suggestions?** → Use `xmlutils.read_single_model()` to dynamically discover valid parameters for the current sim/aircraft/device combo. Key parameters include `spring_mode`, `max_*_coeff`, `*_expo`, `*_spring_gain`, and `vne_override`.
- **Should we build a suggestion engine?** → No. The LLM is the reasoning engine. The MCP server provides raw data and config context; the LLM interprets, computes metrics, and proposes changes.

### Still Open

- Should the MCP server run in-process as a background thread or as a separate local process? (In-process is simpler but couples lifecycle to TelemFFB.)
- Should session persistence be enabled by default or only for manual capture/debug mode?
- Should the MCP server expose the existing `teleplot` mechanism or replace it? The current teleplot uses UDP datagrams with `name:timestamp:value` format — the MCP ring buffer is strictly more capable.
- How should effect coefficient readback be instrumented? Options: (a) add a `snapshot_state()` method to `HapticEffect` that returns the last-sent ctypes struct, or (b) have MixIns explicitly export their computed state via a protocol method like `get_analysis_state() -> dict`.
- Should `get_current_config` support config writes (apply-patch) in the future, or keep the MCP strictly read-only?

## Recommendation

Implement the MCP layer as a **lean data-provider service**: bounded telemetry cache, configuration readback, effect state readback, and signal introspection. Keep the live FFB path unchanged. Expose only normalized telemetry through bounded tools and let the **LLM be the reasoning engine** — it computes derived metrics, identifies issues, and proposes tuning changes from the raw data. This avoids building and maintaining fragile heuristic rules while leveraging the LLM's ability to reason across the full signal/config space.
