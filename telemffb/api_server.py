"""HTTP API for the MSFS in-sim toolbar panel.

Serves the current aircraft's editable settings (as read by SettingsManager /
xmlutils) over a small local HTTP API, and accepts writes back to the same
user config XML the desktop Settings panel writes to.

Lifecycle: only meaningful while MSFS is the connected sim, so main.py starts
this on the first MSFS telemetry frame and stops it on sim exit (see
_maybe_start_api_server / _maybe_stop_api_server in main.py). start/stop are
idempotent and safe to call from the Qt thread.
"""

import logging
import threading
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from telemffb import xmlutils

app = FastAPI()

# Dev-time CORS: the panel's iframe origin will usually match this server's
# own origin (it's loading the page you serve), but keep this open while
# you're testing the page in a normal browser tab too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_settings_mgr = None  # injected by start_api_server()

# --- datatype -> control classification -------------------------------
# Anything not listed here (group/text/path/button/advgs/advspr/trimcal/
# configurator/int/anyfloat) opens a dialog or needs typed input on the
# desktop UI and is left out of the panel entirely.
_BOOL_TYPES = {"bool"}
_CHOICE_TYPES = {"list", "anylist", "enumlist"}
# raw = pos * factor / 100 ; pos = raw / factor * 100  (percent-of-factor sliders)
_PCT_FACTOR_TYPES = {"float", "negfloat", "n_float", "pct_float"}
# raw = pos / 100 ; pos = raw * 100  (fixed percent, no sliderfactor)
_PCT_NOFACTOR_TYPES = {"cfgfloat"}
# raw = round(pos * factor) ; pos = round(raw / factor)  (direct scaled sliders)
_DIRECT_TYPES = {"d_float", "d_int"}
# raw == pos, no factor  (plain stepper)
_STEPPER_TYPES = {"spin_int"}
# structural fields that happen to use a plain-editable datatype but aren't
# really a tunable setting (e.g. 'type' remaps the aircraft's effects class).
_EXCLUDED_NAMES = {"type"}


def _parse_raw_value(value: str) -> float:
    value = (value or "0").strip()
    if value.endswith("%"):
        return float(value[:-1]) / 100
    return float(value)


def _validvalues_bounds(item: dict, default=(0.0, 100.0)) -> tuple[float, float]:
    vv = (item.get("validvalues") or "").split(",") if item.get("validvalues") else []
    if len(vv) >= 2:
        try:
            return float(vv[0]), float(vv[1])
        except ValueError:
            pass
    return default


def _build_control(item: dict) -> Optional[dict]:
    """Turn one settings row into a JSON-friendly control spec, or None to
    exclude it (group headers, dialogs, free-text fields)."""
    datatype = item.get("datatype")
    name = item["name"]
    if name in _EXCLUDED_NAMES:
        return None
    base = {
        "name": name,
        "displayname": item.get("displayname") or name,
        "grouping": item.get("grouping") or "",
        "order": item.get("order") or "",
        "indent": item.get("indent") or 0,
        "unit": item.get("unit") or "",
        "info": item.get("info") or "",
    }

    if datatype in _BOOL_TYPES:
        base["control"] = "bool"
        base["value"] = (item.get("value") or "").lower() == "true"
        return base

    if datatype in _CHOICE_TYPES:
        if datatype == "enumlist":
            label_dict = getattr(_settings_mgr, item.get("validvalues") or "", None)
            if not isinstance(label_dict, dict):
                return None
            options = [{"value": member.name, "label": label} for member, label in label_dict.items()]
        else:
            raw_options = [v for v in (item.get("validvalues") or "").split(",") if v != ""]
            if not raw_options:
                return None
            options = [{"value": v, "label": v} for v in raw_options]
        base["control"] = "choice"
        base["value"] = item.get("value") or ""
        base["options"] = options
        return base

    try:
        if datatype in _PCT_FACTOR_TYPES:
            factor = float(item.get("sliderfactor") or 1)
            pos_min, pos_max = _validvalues_bounds(item)
            base["control"] = "range"
            base["display"] = "percent"
            base["value"] = _parse_raw_value(item.get("value"))
            base["min"] = pos_min * factor / 100
            base["max"] = pos_max * factor / 100
            base["step"] = factor / 100
            return base

        if datatype in _PCT_NOFACTOR_TYPES:
            pos_min, pos_max = _validvalues_bounds(item)
            base["control"] = "range"
            base["display"] = "percent"
            base["value"] = _parse_raw_value(item.get("value"))
            base["min"] = pos_min / 100
            base["max"] = pos_max / 100
            base["step"] = 0.01
            return base

        if datatype in _DIRECT_TYPES:
            factor = float(item.get("sliderfactor") or 1)
            pos_min, pos_max = _validvalues_bounds(item)
            base["control"] = "range"
            base["display"] = "number"
            base["value"] = float(item.get("value") or 0)
            base["min"] = pos_min * factor
            base["max"] = pos_max * factor
            base["step"] = factor
            return base

        if datatype in _STEPPER_TYPES:
            pos_min, pos_max = _validvalues_bounds(item)
            base["control"] = "range"
            base["display"] = "number"
            base["value"] = int(float(item.get("value") or 0))
            base["min"] = int(pos_min)
            base["max"] = int(pos_max)
            base["step"] = 1
            return base
    except (TypeError, ValueError) as e:
        logging.debug(f"api_server: skipping setting '{name}' ({datatype}) - couldn't parse: {e}")
        return None

    # group / text / path / button / advgs / advspr / trimcal / configurator /
    # int / anyfloat: opens a dialog or needs typed input - not in the panel.
    return None


def _current_state() -> dict:
    sm = _settings_mgr
    return {
        "sim": sm.current_sim,
        "aircraft": sm.current_aircraft_name,
        "class": sm.current_class,
        "device": sm.device,
        "connected": sm.current_sim == "MSFS" and not sm.timed_out,
    }


@app.get("/api/status")
def get_status():
    return _current_state()


@app.get("/api/settings")
def get_settings():
    sm = _settings_mgr
    if not sm.current_sim or sm.current_sim == "nothing" or not sm.current_aircraft_name:
        return {**_current_state(), "settings": []}

    _cls, _pattern, result = xmlutils.read_single_model(
        sm.current_sim, sm.current_aircraft_name, sm.current_class, sm.device,
        active_profile=sm.active_profile,
    )

    settings = []
    for item in result or []:
        control = _build_control(item)
        if control is not None:
            settings.append(control)

    return {**_current_state(), "settings": settings}


class SettingUpdate(BaseModel):
    name: str
    value: Any
    unit: str = ""


@app.post("/api/settings")
def set_setting(update: SettingUpdate):
    sm = _settings_mgr
    if not sm.current_sim or sm.current_sim == "nothing":
        raise HTTPException(status_code=409, detail="No sim/aircraft is currently active")

    if isinstance(update.value, bool):
        value = "true" if update.value else "false"
    else:
        value = str(update.value)

    sm.write_to_xml(sm.current_sim, sm.current_class, sm.current_pattern, value, update.name, unit=update.unit)

    # Writing to a 'Built-In' profile forks it into a new 'Auto User' profile
    # (see ConfigWriter.write_models_to_xml). TelemManager normally re-derives
    # active_profile from disk on the next telemetry frame, but refresh it here
    # too so a GET immediately after this POST doesn't read the stale profile.
    if not sm.offline_mode:
        new_profile = xmlutils.get_active_profile_for_model(sm.current_sim, sm.current_class, sm.current_pattern)
        if new_profile != sm.active_profile:
            sm.update_state_vars(active_profile=new_profile)

    return {"ok": True}


_server: Optional[uvicorn.Server] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def start_api_server(settings_mgr, host="127.0.0.1", port=9010):
    """Start the API server if it isn't already running. Safe to call
    repeatedly (e.g. on every MSFS reconnect) - idempotent."""
    global _settings_mgr, _server, _thread
    with _lock:
        _settings_mgr = settings_mgr
        if _thread is not None and _thread.is_alive():
            return _thread

        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        _server = uvicorn.Server(config)

        def _run():
            _server.run()

        _thread = threading.Thread(target=_run, daemon=True, name="TelemFFB-API")
        _thread.start()
        logging.info(f"TelemFFB API server started on http://{host}:{port}")
        return _thread


def stop_api_server():
    """Stop the API server if it's running. Safe to call when it isn't -
    idempotent no-op."""
    global _server, _thread
    with _lock:
        if _server is not None:
            _server.should_exit = True
        if _thread is not None:
            _thread.join(timeout=5)
        _server = None
        _thread = None
        logging.info("TelemFFB API server stopped")


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()