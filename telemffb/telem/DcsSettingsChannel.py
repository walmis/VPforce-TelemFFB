#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
"""UDP channel for the DCS in-sim (ImGui) settings panel.

The DCS DLL (telemffb) renders a Dear ImGui settings overlay. It cannot read
the user config XML itself, so this module is the bridge:

  * DLL -> Python: the DLL sends one-line, tab-delimited commands over UDP to
    a fixed local port (``DCS_SETTINGS_PORT``):
        SET\\t<device>\\t<name>\\t<value>   (user changed a control)
        SHOW\\t<device>                     (user switched the device combo)
    ``device`` is one of joystick / pedals / collective / trimwheel.

  * Python -> DLL: we push a fresh snapshot of the editable settings by
    sending a batch of Lua calls over the DLL's advertised ``UDP_Port`` (the
    same execute-as-Lua channel ``DcsIpcThread.send_commands`` uses). The DLL
    stores the items and renders them in the overlay.

Python is the single source of truth: a ``SET`` is written to the user config
XML (via ``SettingsManager.write_to_xml``), then a fresh snapshot is pushed
back so the overlay self-corrects. The DLL never writes XML.

Lifecycle mirrors ``telemffb.api_server`` (the MSFS panel): main.py wires this
module's on_first_frame / on_sim_exited / on_aircraft_updated to the telemetry
signals; they self-gate on DCS + master + the System Settings toggle.
start/stop are idempotent and safe to call from the Qt thread.
"""

import logging
import math
import socket
import threading
from typing import Optional

import telemffb.globals as G
import telemffb.api_server as api_server
import telemffb.xmlutils as xmlutils
from telemffb.utils import schedule_on_main_thread

# Fixed local port the DLL sends its SET/SHOW commands to. Only the master
# instance binds it (a second master is prevented by the named mutex).
DCS_SETTINGS_PORT = 34381

# Devices the overlay's combo can show. Fixed enum - the DLL hard-codes the
# same list, so keep them in sync.
_DEVICES = ("joystick", "pedals", "collective", "trimwheel")


def _lua_str(s) -> str:
    """Render a Python string as a Lua string literal, escaping the characters
    that would otherwise terminate or alter the literal."""
    s = "" if s is None else str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r")
    return f'"{s}"'


def _lua_num(v) -> Optional[str]:
    """Render a number as a Lua literal, or None if it isn't finite (Lua has
    no inf/nan literals, so such an item must be skipped)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    # Integers render without a decimal point; everything else as a float.
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


class DcsSettingsChannel(threading.Thread):
    """Receives SET/SHOW commands from the DCS overlay and pushes settings
    snapshots back to it.

    The thread only does UDP I/O and parsing; every XML read/write and
    snapshot build is marshalled to the main thread (the ``xmlutils`` facade
    and ``SettingsManager`` are main-thread-only in practice).
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="TelemFFB-DcsSettings")
        self._run = False
        self._settings_mgr = None
        self._socket: Optional[socket.socket] = None
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self, settings_mgr) -> bool:
        """Bind the listener socket and start the thread. Idempotent.

        Returns True if the channel is (now) running.
        """
        with self._lock:
            self._settings_mgr = settings_mgr
            if self._run and self._socket is not None:
                return True

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", DCS_SETTINGS_PORT))
            except OSError as e:
                logging.error(f"DCS settings channel: cannot bind 127.0.0.1:{DCS_SETTINGS_PORT}: {e}")
                sock.close()
                return False

            sock.settimeout(0.5)
            self._socket = sock
            self._run = True
            super().start()
            logging.info(f"DCS settings channel listening on 127.0.0.1:{DCS_SETTINGS_PORT}")
            return True

    def stop(self) -> None:
        """Stop the thread and close the socket. Idempotent."""
        with self._lock:
            self._run = False
            if self._socket is not None:
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
        if self.is_alive():
            self.join(timeout=2)
        logging.info("DCS settings channel stopped")

    def is_running(self) -> bool:
        return self._run

    # -- thread body -------------------------------------------------------

    def run(self) -> None:
        while self._run:
            try:
                data, _addr = self._socket.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break  # socket closed by stop()

            try:
                line = data.decode("utf-8", "replace").strip()
            except Exception:
                continue
            if not line:
                continue

            parts = line.split("\t")
            try:
                if parts[0] == "SET" and len(parts) >= 4:
                    device, name, value = parts[1], parts[2], parts[3]
                    schedule_on_main_thread(lambda d=device, n=name, v=value: self._handle_set(d, n, v))
                elif parts[0] == "SHOW" and len(parts) >= 2:
                    device = parts[1]
                    schedule_on_main_thread(lambda d=device: self._handle_show(d))
                else:
                    logging.debug(f"DCS settings channel: ignoring message: {line!r}")
            except Exception as e:
                logging.exception(f"DCS settings channel: error handling message {line!r}: {e}")

    # -- main-thread handlers ---------------------------------------------

    def _handle_show(self, device: str) -> None:
        """User switched the overlay's device combo - push that device's settings."""
        if device not in _DEVICES:
            logging.debug(f"DCS settings channel: unknown device {device!r}")
            return
        self.push_snapshot(device)

    def _handle_set(self, device: str, name: str, value: str) -> None:
        """User changed a control in the overlay - write it to XML, then push a
        fresh snapshot so the overlay (and the desktop UI / FFB) self-correct."""
        sm = self._settings_mgr
        if sm is None or not sm.current_sim or sm.current_sim == "nothing" or not sm.current_aircraft_name:
            logging.debug("DCS settings channel: SET ignored, no active aircraft")
            return
        if device not in _DEVICES:
            logging.debug(f"DCS settings channel: SET ignored, unknown device {device!r}")
            return

        # Read the current model for this device to find the item's unit and
        # datatype (write_models_to_xml overwrites the <unit> child, so we must
        # pass the existing unit back or we'd clobber it).
        try:
            _cls, _pattern, items = xmlutils.read_single_model(
                sm.current_sim, sm.current_aircraft_name, sm.current_class, device,
                active_profile=sm.active_profile,
            )
        except Exception as e:
            logging.exception(f"DCS settings channel: read_single_model failed for SET {name!r}: {e}")
            return

        item = next((it for it in (items or []) if it.get("name") == name), None)
        if item is None:
            # Not a real editable setting for this device - don't write (would
            # clobber an unknown row's unit).
            logging.debug(f"DCS settings channel: SET for unknown setting {name!r} ({device})")
            return

        unit = item.get("unit") or ""
        try:
            sm.write_to_xml(sm.current_sim, sm.current_class, sm.current_pattern,
                            value, name, unit=unit, the_device=device)
        except Exception as e:
            logging.exception(f"DCS settings channel: write_to_xml failed for {name!r}: {e}")
            return

        # Force the config-change machinery to re-read on the next frame. The
        # mtime poll uses integer-second mtimes, so two writes in the same
        # second would otherwise be missed; this flag guarantees a re-read.
        if hasattr(G.telem_manager, "force_config_change"):
            G.telem_manager.force_config_change()

        # Push a corrected snapshot. We reuse the items we just read and only
        # override the one value we wrote, avoiding a second disk read.
        self.push_snapshot(device, override=(name, value))

    # -- snapshot build + push (main-thread-only) -------------------------

    def build_snapshot(self, device: str, override=None) -> list:
        """Build the list of control specs for a device, reusing the exact
        classification the MSFS API server uses (``api_server._build_control``).

        ``override`` is an optional ``(name, value)`` pair to force a single
        item's value (used right after a SET write, before a re-read).
        """
        sm = self._settings_mgr
        if sm is None or not sm.current_sim or sm.current_sim == "nothing" or not sm.current_aircraft_name:
            return []

        # _build_control resolves enumlist labels via the module-global
        # _settings_mgr; point it at our manager (same object as the API
        # server's, since both use G.settings_mgr).
        api_server._settings_mgr = sm

        try:
            _cls, _pattern, items = xmlutils.read_single_model(
                sm.current_sim, sm.current_aircraft_name, sm.current_class, device,
                active_profile=sm.active_profile,
            )
        except Exception as e:
            logging.exception(f"DCS settings channel: read_single_model failed for {device}: {e}")
            return []

        controls = []
        for item in items or []:
            if override is not None and item.get("name") == override[0]:
                item = {**item, "value": override[1]}
            control = api_server._build_control(item)
            if control is not None:
                controls.append(control)
        return controls

    def push_snapshot(self, device: Optional[str] = None, override=None) -> None:
        """Build a device's settings and push them to the DLL as a Lua batch.

        Must be called on the main thread (it reads the XML via the facade).
        """
        sm = self._settings_mgr
        if sm is None or not self._run:
            return
        if not sm.current_sim or sm.current_sim == "nothing" or not sm.current_aircraft_name:
            return
        if sm.current_sim != "DCS":
            return

        if device is None:
            device = sm.device
        if device not in _DEVICES:
            device = "joystick"

        controls = self.build_snapshot(device, override=override)
        batch = self._build_lua_batch(device, controls)

        port = G.telem_manager.getTelemValue("UDP_Port") if G.telem_manager else None
        if not port:
            logging.debug("DCS settings channel: no UDP_Port yet, skipping push")
            return

        from telemffb.telem.DcsIpcThread import DcsIpcThread
        try:
            DcsIpcThread.send_commands(batch)
            logging.info(f"DCS settings channel: pushed {len(controls)} settings for {device}")
        except Exception as e:
            logging.exception(f"DCS settings channel: push failed: {e}")

    @staticmethod
    def _build_lua_batch(device: str, controls: list) -> str:
        """Serialize control specs into a newline-separated batch of Lua calls
        the DLL executes via its existing processCommands path."""
        lines = ["telemffb_clear_settings()"]
        for c in controls:
            name = _lua_str(c["name"])
            disp = _lua_str(c.get("displayname") or c["name"])
            group = _lua_str(c.get("grouping") or "")
            unit = _lua_str(c.get("unit") or "")
            info = _lua_str(c.get("info") or "")
            dev = _lua_str(device)

            if c["control"] == "bool":
                val = "true" if c["value"] else "false"
                lines.append(f"telemffb_set_bool({dev}, {name}, {val}, {disp}, {group}, {unit}, {info})")
            elif c["control"] == "choice":
                opts = ";".join(f"{o['value']}|{o['label']}" for o in c.get("options", []))
                lines.append(
                    f"telemffb_set_choice({dev}, {name}, {_lua_str(c.get('value') or '')}, "
                    f"{disp}, {group}, {unit}, {info}, {_lua_str(opts)})")
            elif c["control"] == "range":
                nums = [_lua_num(c[k]) for k in ("value", "min", "max", "step")]
                if any(n is None for n in nums):
                    continue  # non-finite bound - skip rather than emit bad Lua
                lines.append(
                    f"telemffb_set_range({dev}, {name}, {nums[0]}, {nums[1]}, {nums[2]}, {nums[3]}, "
                    f"{disp}, {group}, {unit}, {_lua_str(c.get('display') or 'number')}, {info})")
        return "\n".join(lines)


# Module-level singleton + convenience functions (mirrors api_server's API).
_channel = DcsSettingsChannel()


def start_dcs_settings_channel(settings_mgr) -> bool:
    return _channel.start(settings_mgr)


def stop_dcs_settings_channel() -> None:
    _channel.stop()


def push_dcs_settings_snapshot(device: Optional[str] = None) -> None:
    _channel.push_snapshot(device)


def is_dcs_settings_running() -> bool:
    return _channel.is_running()


def on_first_frame(src):
    """Qt signal handler: start the channel when DCS first sends a frame.

    Self-gating so main.py stays pure wiring: only the master instance's
    SettingsManager reflects a device worth exposing, so gate on master +
    DCS + the System Settings toggle (default on).
    """
    if src != "DCS" or not G.master_instance:
        return
    if not G.system_settings.get('enableDcsSettingsPanel', True):
        return
    start_dcs_settings_channel(G.settings_mgr)
    # Push the current device's settings now that the channel is up.
    push_dcs_settings_snapshot()


def on_sim_exited(src):
    """Qt signal handler: stop the channel when DCS exits."""
    if src == "DCS":
        stop_dcs_settings_channel()


def on_aircraft_updated():
    """Qt signal handler: re-push the active device's snapshot on aircraft
    change AND on settings change (both emit aircraftUpdated), so the overlay
    stays in sync with the desktop UI / XML."""
    if not G.master_instance or not G.telem_manager:
        return
    ac = G.telem_manager.currentAircraft
    if ac and ac._telem_data.get('src') == "DCS":
        push_dcs_settings_snapshot()
