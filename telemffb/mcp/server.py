"""MCP server for TelemFFB telemetry analysis.

Exposes bounded telemetry queries, configuration state, and FFB effect state
so an LLM can inspect current or recent simulator state without participating
in the real-time force feedback control loop.

Runs only on the master instance (G.master_instance == True) as a background
thread with streamable HTTP transport on localhost.
"""

import logging
import threading
from typing import Any, Dict, List, Optional

import telemffb.globals as G
from telemffb.analysis.signal_registry import explain_signal as _explain_signal
from telemffb.analysis.signal_registry import get_signal_registry, list_all_signals

log = logging.getLogger(__name__)

DEFAULT_PORT = 8089

try:
    from mcp.server.fastmcp import FastMCP
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    FastMCP = None

mcp = None
if _MCP_AVAILABLE:
    mcp = FastMCP(
        "TelemFFB Analysis",
        instructions=(
            "TelemFFB MCP server for flight simulator telemetry analysis. "
            "Use get_current_state to see what sim/aircraft is active, "
            "list_available_signals to discover available data fields, "
            "get_telemetry_window for bounded historical queries, "
            "explain_signal for signal documentation, "
            "and get_effect_state for current FFB output state."
        ),
    )


def _tool_decorator():
    """Return the mcp.tool() decorator, or a no-op if mcp is unavailable."""
    if mcp is not None:
        return mcp.tool()
    return lambda fn: fn


_tool = _tool_decorator


def _get_tap():
    """Get the telemetry tap from globals."""
    return getattr(G, 'telemetry_tap', None)


def _get_aircraft_name() -> Optional[str]:
    tm = getattr(G, 'telem_manager', None)
    if tm and tm.currentAircraft:
        return tm.currentAircraftName
    return None


def _get_sim_name() -> Optional[str]:
    tap = _get_tap()
    if tap:
        latest = tap.get_latest_snapshot()
        if latest:
            return latest.get('src')
    return None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@_tool()
def get_current_state() -> Dict[str, Any]:
    """Get a snapshot of current aircraft, sim, device, and telemetry.

    Returns the current simulator name, aircraft name, device type,
    and the latest telemetry frame values. Use this as the first call
    to orient yourself before requesting specific signal data.
    """
    tap = _get_tap()

    result: Dict[str, Any] = {
        "sim": None,
        "aircraft": _get_aircraft_name(),
        "device_type": G.device_type,
        "telemetry": None,
        "paused": False,
    }

    if tap:
        latest = tap.get_latest_snapshot()
        if latest:
            result["sim"] = latest.get('src')
            result["telemetry"] = latest
            result["paused"] = tap.high_rate.is_paused
        else:
            result["paused"] = True

    return result


@_tool()
def get_telemetry_window(
    signals: List[str],
    seconds: float = 10.0,
    sample_hz: Optional[float] = None,
) -> Dict[str, Any]:
    """Get a bounded historical window of telemetry for selected signals.

    Args:
        signals: List of signal names to retrieve (e.g. ["AoA", "Roll", "IAS"]).
        seconds: How many seconds of history to retrieve (max 300).
        sample_hz: Optional target sample rate. If omitted, returns native rate.
                   Lower values reduce data volume for longer windows.

    Returns:
        Dictionary with start_ts, end_ts, actual sample rate, paused flag,
        and a series dict mapping each signal name to its value array.
    """
    tap = _get_tap()
    if not tap:
        return {"error": "Telemetry tap not initialized"}

    seconds = min(seconds, 300.0)

    # Choose buffer based on window size
    if seconds <= 35:
        buf = tap.high_rate
        decimate = 1
    else:
        buf = tap.low_rate
        decimate = 1

    # Apply decimation for target sample rate if requested
    if sample_hz and sample_hz > 0:
        # Estimate native rate from buffer
        latest = buf.get_latest()
        if latest and len(buf) > 1:
            # Rough estimate
            native_hz = len(buf) / max(seconds, 1.0)
            if native_hz > sample_hz:
                decimate = max(1, int(native_hz / sample_hz))

    frames = buf.get_window(seconds, signals=signals, decimate=decimate)

    if not frames:
        return {
            "start_ts": None,
            "end_ts": None,
            "sample_count": 0,
            "series": {},
            "paused": buf.is_paused,
        }

    series: Dict[str, List] = {sig: [] for sig in signals}
    timestamps = []

    for frame in frames:
        timestamps.append(frame.timestamp)
        for sig in signals:
            series[sig].append(frame.fields.get(sig))

    return {
        "start_ts": timestamps[0],
        "end_ts": timestamps[-1],
        "sample_count": len(timestamps),
        "paused": buf.is_paused,
        "series": series,
    }


@_tool()
def list_available_signals() -> Dict[str, Any]:
    """List all signals currently populated in the telemetry stream.

    Returns the current sim, aircraft, device type, and a list of all
    field names present in the latest telemetry frame. Also includes
    a separate list of computed fields (prefixed with underscore).
    Use explain_signal to get documentation for any field.
    """
    tap = _get_tap()
    result: Dict[str, Any] = {
        "sim": _get_sim_name(),
        "aircraft": _get_aircraft_name(),
        "device_type": G.device_type,
        "total_fields": 0,
        "fields": [],
        "computed_fields": [],
    }

    if tap:
        latest = tap.get_latest_snapshot()
        if latest:
            all_keys = sorted(latest.keys())
            fields = [k for k in all_keys if not k.startswith('_')]
            computed = [k for k in all_keys if k.startswith('_')]
            result["total_fields"] = len(all_keys)
            result["fields"] = fields
            result["computed_fields"] = computed

    return result


@_tool()
def explain_signal_tool(signal: str) -> Dict[str, Any]:
    """Get documentation and metadata for a telemetry signal.

    Args:
        signal: The signal name to look up (e.g. "AoA", "RelWind", "TAS").

    Returns:
        Signal metadata including type, description, available sims,
        units, and category. Returns an error if the signal is not found
        in the registry.
    """
    info = _explain_signal(signal)
    if info is None:
        # Check if it exists in current telemetry even if not in registry
        tap = _get_tap()
        if tap:
            latest = tap.get_latest_snapshot()
            if latest and signal in latest:
                return {
                    "signal": signal,
                    "type": type(latest[signal]).__name__,
                    "description": "Dynamic field (not in BaseTelemetryData registry)",
                    "current_value": latest[signal],
                }
        return {"error": f"Signal '{signal}' not found in registry or current telemetry"}
    return info


@_tool()
def get_effect_state() -> Dict[str, Any]:
    """Get current FFB effect state for the active aircraft instance.

    Returns the device type, list of active effects with their types
    and started/stopped status, and MixIn-level state attributes
    (hydraulic factor, damper coefficient, etc.) when available.
    """
    result: Dict[str, Any] = {
        "device_type": G.device_type,
        "active_effects": [],
        "mixin_state": {},
    }

    effects = getattr(G, 'effects', None)
    if effects:
        for name in effects:
            effect = effects.dict.get(name)
            if effect:
                effect_info = {
                    "name": name,
                    "started": getattr(effect, 'started', False),
                }
                # Try to get effect type name
                etype = getattr(effect, 'effect_type', None)
                if etype is not None:
                    effect_info["type"] = str(etype)
                result["active_effects"].append(effect_info)

    # Collect MixIn state from current aircraft
    tm = getattr(G, 'telem_manager', None)
    if tm and tm.currentAircraft:
        aircraft = tm.currentAircraft
        mixin_attrs = [
            'damper_coeff', 'friction_coeff', 'inertia_coeff',
            '_hyd_factor', '_buffeting', '_pct_max_stall_buffet',
            'decel_g', 'decel_g_smooth',
            '_elev_coeff', '_aile_coeff', '_rud_coeff',
            '_controls_locked', 'spring_mode',
        ]
        for attr in mixin_attrs:
            val = getattr(aircraft, attr, None)
            if val is not None:
                # Clean attribute name for output
                key = attr.lstrip('_')
                result["mixin_state"][key] = val

    return result


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _run_server(port: int):
    """Run the MCP server (blocking). Called from a daemon thread."""
    try:
        log.info(f"Starting MCP server on http://127.0.0.1:{port}/mcp")
        mcp.run(transport="streamable-http", host="127.0.0.1", port=port)
    except Exception:
        log.exception("MCP server failed")


_server_thread: Optional[threading.Thread] = None


def start_mcp_server(port: int = DEFAULT_PORT) -> bool:
    """Start the MCP server in a background daemon thread.

    Should only be called from the master instance after globals are initialized.
    Returns True if the server was started, False if mcp is not available.
    """
    global _server_thread
    if not _MCP_AVAILABLE:
        log.info("MCP server not available (install 'mcp' package for dev telemetry analysis)")
        return False
    if _server_thread and _server_thread.is_alive():
        log.warning("MCP server already running")
        return True

    _server_thread = threading.Thread(
        target=_run_server,
        args=(port,),
        daemon=True,
        name="MCPServer",
    )
    _server_thread.start()
    log.info(f"MCP server thread started on port {port}")
    return True


def stop_mcp_server() -> None:
    """Stop the MCP server if running."""
    global _server_thread
    # FastMCP/uvicorn doesn't have a clean shutdown from outside the thread,
    # but since it's a daemon thread it will die with the process.
    _server_thread = None
