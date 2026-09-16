"""HTTP API for the MSFS in-sim toolbar panel.

Serves the current aircraft's editable settings (as read by SettingsManager /
xmlutils) over a small local HTTP API, and accepts writes back to the same
user config XML the desktop Settings panel writes to.

Lifecycle: only meaningful while MSFS is the connected sim, so main.py starts
this on the first MSFS telemetry frame and stops it on sim exit (see
_maybe_start_api_server / _maybe_stop_api_server in main.py). start/stop are
idempotent and safe to call from the Qt thread.

Built on Bottle + wsgiref rather than FastAPI/uvicorn: Bottle has no built-in
CORS support (added by hand below - the panel's JSON POSTs are not "simple"
requests, so browsers preflight them with OPTIONS first) or graceful
start/stop (bottle.run() blocks forever), so this drives
wsgiref.simple_server directly - the same server Bottle's own default
adapter uses - to get a start()/shutdown() pair to call from Qt.
"""

import logging
import re
import threading
from socketserver import ThreadingMixIn
from typing import Any, Optional
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from bottle import Bottle, request, response

import telemffb.globals as G
from telemffb import xmlutils

app = Bottle()

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


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text: str, max_len: Optional[int] = None) -> str:
    """Desktop 'info' and even some 'displayname' strings are sometimes full
    HTML (with <br>, <b>, <img> tags etc.) meant for a QLabel. The panel only
    ever shows this as plain text, and the in-sim browser doesn't render it
    as HTML at all - so strip markup (and cap length for the long ones)
    rather than shipping raw tags down to the panel."""
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()
    if max_len is not None and len(text) > max_len:
        text = text[: max_len - 3].rstrip() + "..."
    return text


def _build_control(item: dict) -> Optional[dict]:
    """Turn one settings row into a JSON-friendly control spec, or None to
    exclude it (group headers, dialogs, free-text fields)."""
    datatype = item.get("datatype")
    name = item["name"]
    if name in _EXCLUDED_NAMES:
        return None
    base = {
        "name": name,
        "displayname": _strip_html(item.get("displayname") or name),
        "grouping": item.get("grouping") or "",
        "order": item.get("order") or "",
        "indent": item.get("indent") or 0,
        "unit": item.get("unit") or "",
        "info": _strip_html(item.get("info") or "", max_len=200),
        # Desktop settings UI (SettingsLayout.py) renders a setting whose order
        # ends in '1' with a '.' (e.g. "10700.1") inline on its 'prereq'
        # parent's own row instead of as a separate row - the panel mirrors
        # that (see extractBumpChildren() in panel.js).
        "prereq": item.get("prereq") or "",
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


def _is_vr() -> bool:
    """MSFS's 'E:IS IN VR' environment variable (see SimConnectManager.py's
    IsInVr SimVar) - 1 when the sim is in VR mode, 0 otherwise. Used to pick
    between the two persisted panel-zoom settings (msfs_panel_zoom /
    msfs_panel_zoom_vr) so VR and flat-screen can each keep their own
    preferred zoom level. """
    tm = getattr(G, "telem_manager", None)
    if tm is None:
        return False
    try:
        is_in_vr = tm.getTelemValue("IsInVr")
    except Exception:
        return False
    if is_in_vr is None:
        return False
    try:
        return bool(int(float(is_in_vr)))
    except (TypeError, ValueError):
        return str(is_in_vr).strip().lower() in ("1", "true")


def _current_state() -> dict:
    sm = _settings_mgr
    return {
        "sim": sm.current_sim,
        "aircraft": sm.current_aircraft_name,
        "pattern": sm.current_pattern,
        "class": sm.current_class,
        "device": sm.device,
        "connected": sm.current_sim == "MSFS" and not sm.timed_out,
    }


# --- CORS ---------------------------------------------------------------
# Dev-time CORS: the panel's iframe origin will usually match this server's
# own origin (it's loading the page you serve), but keep this open while
# you're testing the page in a normal browser tab too. Bottle has no
# built-in CORS handling, unlike FastAPI's CORSMiddleware this replaces -
# both pieces (the header hook, and the OPTIONS preflight route) are
# required for the panel's JSON POSTs to work from a browser context.
@app.hook("after_request")
def _cors_headers():
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"


@app.route("/<_path:path>", method="OPTIONS")
def _cors_preflight(_path):
    return {}


@app.get("/")
def root():
    # No content lives here - it's just a sanity-check landing point for
    # anyone hitting the bare host:port in a browser.
    return {"ok": True, "see": ["/api/status", "/api/settings"]}


@app.get("/api/status")
def get_status():
    return _current_state()


@app.get("/api/panel-zoom")
def get_panel_zoom():
    vr = _is_vr()
    key = "msfs_panel_zoom_vr" if vr else "msfs_panel_zoom"
    zoom = G.system_settings.get(key, 100)
    try:
        zoom = int(zoom)
    except (TypeError, ValueError):
        zoom = 100
    return {"zoom": zoom, "vr": vr}


@app.post("/api/panel-zoom")
def set_panel_zoom():
    data = request.json
    if not data or "zoom" not in data:
        response.status = 400
        return {"detail": "Request body must include 'zoom'"}
    try:
        zoom = int(data["zoom"])
    except (TypeError, ValueError):
        response.status = 400
        return {"detail": "'zoom' must be an integer"}

    vr = _is_vr()
    key = "msfs_panel_zoom_vr" if vr else "msfs_panel_zoom"
    G.system_settings.setValue(key, zoom)
    return {"ok": True, "vr": vr}


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


@app.post("/api/settings")
def set_setting():
    data = request.json
    if not data or "name" not in data or "value" not in data:
        response.status = 400
        return {"detail": "Request body must include 'name' and 'value'"}

    name = data["name"]
    raw_value = data["value"]
    unit = data.get("unit") or ""

    sm = _settings_mgr
    if not sm.current_sim or sm.current_sim == "nothing":
        response.status = 409
        return {"detail": "No sim/aircraft is currently active"}

    if isinstance(raw_value, bool):
        value = "true" if raw_value else "false"
    else:
        value = str(raw_value)

    sm.write_to_xml(sm.current_sim, sm.current_class, sm.current_pattern, value, name, unit=unit)

    # Writing to a 'Built-In' profile forks it into a new 'Auto User' profile
    # (see ConfigWriter.write_models_to_xml). TelemManager normally re-derives
    # active_profile from disk on the next telemetry frame, but refresh it here
    # too so a GET immediately after this POST doesn't read the stale profile.
    if not sm.offline_mode:
        new_profile = xmlutils.get_active_profile_for_model(sm.current_sim, sm.current_class, sm.current_pattern)
        if new_profile != sm.active_profile:
            sm.update_state_vars(active_profile=new_profile)

    return {"ok": True}


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """Handle concurrent requests (a status/settings poll landing while a
    write is in flight) rather than wsgiref's default one-at-a-time."""
    daemon_threads = True


class _QuietWSGIRequestHandler(WSGIRequestHandler):
    """wsgiref logs every request to stderr by default; route it through
    logging instead, at debug level, matching uvicorn's log_level='warning'
    quiet-by-default behavior this replaces."""
    def log_message(self, format, *args):
        logging.debug("TelemFFB API: " + format, *args)


_server: Optional[WSGIServer] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def start_api_server(settings_mgr, host="127.0.0.1", port=9873):
    """Start the API server if it isn't already running. Safe to call
    repeatedly (e.g. on every MSFS reconnect) - idempotent."""
    global _settings_mgr, _server, _thread
    with _lock:
        _settings_mgr = settings_mgr
        if _thread is not None and _thread.is_alive():
            return _thread

        _server = make_server(host, port, app, server_class=_ThreadingWSGIServer,
                               handler_class=_QuietWSGIRequestHandler)

        def _run():
            _server.serve_forever()

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
            _server.shutdown()  # blocks until serve_forever()'s loop exits
            _server.server_close()
        if _thread is not None:
            _thread.join(timeout=5)
        _server = None
        _thread = None
        logging.info("TelemFFB API server stopped")


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def on_first_frame(src):
    """Qt signal handler: start the panel when MSFS first sends a frame.

    Self-gating so main.py stays pure wiring: only the master instance's
    SettingsManager reflects a device worth exposing, and the panel has
    nothing to serve for other sims, so gate on both - plus the System
    Settings > Simulator Setup > MSFS > Options toggle (default on).
    """
    if src != "MSFS" or not G.master_instance:
        return
    if not G.system_settings.get('enableMsfsApiServer', True):
        return
    start_api_server(G.settings_mgr)


def on_sim_exited(src):
    """Qt signal handler: stop the panel when MSFS exits."""
    if src == "MSFS":
        stop_api_server()
