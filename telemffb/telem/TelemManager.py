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


import json
import logging
import os
import subprocess
import threading
import time
import socket
import psutil

from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal
from typing import Optional, Tuple, TYPE_CHECKING

import telemffb.globals as G
import telemffb.utils as utils
from telemffb.utils import dbprint
import telemffb.xmlutils as xmlutils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim import aircrafts_dcs, aircrafts_il2, aircrafts_msfs_xp
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.telem.SimConnectManager import SimConnectManager
from telemffb.utils import upload_vpconf_profile

if TYPE_CHECKING:
    from telemffb.sim.aircraft_base import AircraftBase

@dataclass
class AircraftInfo:
    name: Optional[str]
    data_source: Optional[str]
    module: object
    sc_aircraft_type: Optional[str] = None
    sc_engine_type: Optional[int] = None

_config_mtime = 0
_future_config_update_time = time.time()
_pending_config_update = False

def config_has_changed(update=False) -> bool:
    # if update is true, update the current modified time
    global _config_mtime, _future_config_update_time, _pending_config_update
    # "hash" both mtimes together
    tm = int(os.path.getmtime(G.userconfig_path)) + int(os.path.getmtime(G.defaults_path))
    time_now = time.time()
    update_delay = 0.1  # Delay added here to avoid file access errors with multiple instances

    if not _config_mtime:
        # if the first time called, initialize times and return - to avoid double config load on first call
        _config_mtime = tm
        return False

    if _config_mtime != tm:
        _future_config_update_time = time_now + update_delay
        _pending_config_update = True
        _config_mtime = tm
        logging.info(f'Config changed: Waiting {update_delay} seconds to read changes')
    if _pending_config_update and time_now >= _future_config_update_time:
        _pending_config_update = False
        logging.info(f'Config changed: {update_delay} second timer expired, reading changes')
        return True
    return False

class TelemManager(QObject, threading.Thread):
    telemetryReceived = pyqtSignal(object)
    eventReceived = pyqtSignal(tuple)

    aircraftUpdated = pyqtSignal()
    telemetryTimeout = pyqtSignal(bool)

    first_frame_received = pyqtSignal(str)
    sim_exited = pyqtSignal(str)   # emitted when a sim exit detected

    currentAircraft: Optional['AircraftBase'] = None
    currentAircraftName: Optional[str] = None
    currentAircraftConfig: dict

    timed_out: bool = True
    last_frame_time: float
    numFrames: int = 0

    def __init__(self) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self, daemon=True)

        self._run = True
        self._cond = threading.Condition()
        self._data = None
        self._events = []
        self._dropped_frames = 0
        self.last_frame_time = time.perf_counter()
        self.frame_times = []
        self.max_frame_time = 0
        self.timeout_sec = 0.2
        self._ipc_telem_data = {}
        self._simconnect : Optional[SimConnectManager] = None
        self.gain_overrides_active = False
        self.stop_state = False
        self.pause_state = False
        self._first_frame_from_sim = False
        self._sim_exit_signaled = False   # True after notify_sim_exited fires; prevents re-entrancy until reset_sim_connected() clears it
        self._process_check_deadline: Optional[float] = None  # perf_counter() timestamp of the next scheduled process check; None when inactive


    def set_paused(self, pause_state: bool = False):
        self.pause_state = pause_state

    def reset_sim_connected(self):
        """Called by SimListenerManager.allStarted when all sim listeners have been
        (re)started.  Clears any leftover timeout / exit state from the previous
        sim session so the next telemetry frame is treated as a clean connection."""
        self._first_frame_from_sim = False
        self.timed_out = False
        self._sim_exit_signaled = False
        self._process_check_deadline = None

    def notify_sim_exited(self, src: str):
        """Called by any sim transport when it detects a clean exit.
        Stops active FFB effects, clears aircraft state, and emits sim_exited
        so that main.py connections can restart all listeners and reset the UI.
        Guarded against double-firing; resets automatically via reset_sim_connected()."""
        if self._sim_exit_signaled:
            logging.debug(f"notify_sim_exited({src}): already signaled, ignoring duplicate")
            return
        self._sim_exit_signaled = True
        self._process_check_deadline = None  # cancel any pending process check
        logging.info(f"Sim exit received from {src} - resetting sim listeners")
        if self.currentAircraft:
            self.currentAircraft.on_timeout()
            self.currentAircraft = None
        self.currentAircraftName = None
        self.sim_exited.emit(src)

    def set_simconnect(self, sc : SimConnectManager):
        self._simconnect = sc

    @property
    def simconnect(self) -> Optional[SimConnectManager]:
        return self._simconnect

    def get_aircraft_config(self, aircraft_name, data_source) -> Tuple[dict, str]:
        params = {}
        cls_name = "UNKNOWN"
        input_modeltype = ''
        try:
            if data_source == "MSFS":
                send_source = "MSFS"
            else:
                send_source = data_source

            if '.' in send_source:
                input = send_source.split('.')
                sim_temp = input[0]
                the_sim = sim_temp.replace('2020', '')
                input_modeltype = input[1]
            else:
                the_sim = send_source
            ptrn = xmlutils.get_pattern_by_sim_fullname(the_sim, aircraft_name)

            cls_name, pattern, result = xmlutils.read_single_model(the_sim, aircraft_name, input_modeltype, G.device_type)
            active_profile = xmlutils.get_active_profile_for_model(the_sim, cls_name, pattern)
            #globals.settings_mgr.current_pattern = pattern
            if cls_name == '': 
                cls_name = 'Aircraft'
            for setting in result:
                k = setting['name']
                v = setting['value']
                u = setting['unit']
                if v is None:
                    v = '0'
                if u is not None:
                    vu = v + u
                else:
                    vu = v
                if setting['value'] != '-':
                    params[k] = vu
                    logging.debug(f"Got from Settings Manager: {k} : {vu}")
                else:
                    logging.debug(f"Ignoring blank setting from Settings Manager: {k} : {vu}")
                # print(f"SETTING:\n{setting}")
            params = utils.sanitize_dict(params)

            G.settings_mgr.update_state_vars(
                current_sim=the_sim,
                current_aircraft_name=aircraft_name,
                current_class=cls_name,
                current_pattern=pattern,
                active_profile=active_profile)

            return params, cls_name

            # logging.info(f"Got settings from settingsmanager:\n{formatted_result}")
        except Exception as e:
            logging.exception(f"Error getting settings from Settings Manager:{e}")

    def quit(self):
        self._run = False
        self.join()

    def flush_deferred_startup_frame(self):
        """Deliver the last telemetry frame that arrived while the startup
        vpconf push had frame processing suspended (see submit_frame). Called
        by utils.init_vpconf_profile right after clearing the pending flag."""
        frame = getattr(self, "_vpconf_deferred_frame", None)
        self._vpconf_deferred_frame = None
        if frame is not None:
            logging.info("Delivering telemetry frame deferred during the "
                         "startup vpconf push")
            self.submit_frame(frame)

    def submit_frame(self, data_in: bytes):
        if G.vpconf_init_pending:
            # Startup vpconf push not complete yet (reset by
            # utils.init_vpconf_profile, which then flushes the frame stashed
            # here). DEFER the frame instead of discarding it: while MSFS sits
            # in the menus the SimConnect stop latch delivers exactly ONE
            # telemetry packet, and dropping it in this window left the master
            # instance with no aircraft until a camera-state change — a
            # milliseconds-wide race, master-only, maddeningly intermittent.
            self._vpconf_deferred_frame = data_in
            return
        if self.pause_state:
            # don't process frames while paused state True
            return

        data : str
        if isinstance(data_in, bytes):
            data = data_in.decode("utf-8")
        else:
            data = str(data_in)

        with self._cond:
            if data.startswith("Ev="):
                self._events.append(data.lstrip("Ev="))
                self._cond.notify()
            elif self._data is None:
                self._data = data
                self._cond.notify()  # notify waiting thread of new data
            else:
                self._dropped_frames += 1
                # log dropped frames, this is not necessarily a bad thing
                # USB interrupt transfers (1ms) might take longer than one video frame
                # we drop frames to keep latency to a minimum
                logging.debug(f"Dropped frame (total {self._dropped_frames})")

    def process_events(self):
        while self._events:
            ev = self._events.pop(0)
            ev = ev.split(";")

            if self.currentAircraft:
                self.currentAircraft.on_event(*ev)
            self.eventReceived.emit(tuple(ev))
            continue

    def get_changed_params(self, params):
        diff_dict = {}

        # Check for new keys or keys with different values
        for key, new_value in params.items():
            if key not in self.currentAircraftConfig or self.currentAircraftConfig[key] != new_value:
                diff_dict[key] = new_value
        logging.debug(f"get_changed_settings: {diff_dict.items()}")
        self.currentAircraftConfig.update(diff_dict)
        return diff_dict

    def process_data(self, data):
        """Main telemetry data processing pipeline."""
        parsed_data = self._parse_telemetry_data(data)
        aircraft_info = self._extract_aircraft_info(parsed_data)

        self._handle_aircraft_changes(aircraft_info, parsed_data)
        self._handle_config_changes(aircraft_info)
        self._process_current_aircraft_telemetry(parsed_data)
        self._handle_ipc_and_plotting(parsed_data)
        self._emit_telemetry(parsed_data)
        if not self._first_frame_from_sim:
            self._first_frame_from_sim = True
            self.first_frame_received.emit(parsed_data.get('src', None))

    def _parse_telemetry_data(self, data) -> BaseTelemetryData:
        """Parse raw telemetry data and calculate frame timing metrics."""
        data = data.split(";")
        telem_data = BaseTelemetryData({"FFBType": G.device_type})

        # Calculate frame timing
        self._update_frame_timing(telem_data)

        # Parse telemetry parameters
        for param in data:
            try:
                if len(param):
                    section, conf = param.split("=")
                    values = conf.split("~")
                    telem_data[section] = [utils.to_number(v) for v in values] if len(values) > 1 else utils.to_number(conf)
            except Exception:
                logging.exception("Error Parsing Parameter: %s", repr(param))

        # Merge IPC telemetry data
        self._merge_ipc_telemetry(telem_data)

        return telem_data

    def _update_frame_timing(self, telem_data: BaseTelemetryData):
        """Update frame timing metrics and add to telemetry data."""
        current_frame_time = int((time.perf_counter() - self.last_frame_time) * 1000)
        self.frame_times.append(current_frame_time)

        if len(self.frame_times) > 500:
            self.frame_times.pop(0)

        # Check for frame time threshold violations
        if current_frame_time > self.max_frame_time and len(self.frame_times) > 40:
            threshold = 100
            if current_frame_time > threshold:
                logging.debug(f'*!*!*!* - Frametime threshold of {threshold}ms exceeded: time = {current_frame_time}ms')
            self.max_frame_time = current_frame_time

        telem_data["frameTimes"] = [current_frame_time, max(self.frame_times)]
        telem_data["maxFrameTime"] = f"{round(self.max_frame_time, 3)}"
        telem_data["avgFrameTime"] = f"{round(sum(self.frame_times) / len(self.frame_times), 3):.3f}"

        self.last_frame_time = time.perf_counter()

    def _merge_ipc_telemetry(self, telem_data: BaseTelemetryData):
        """Merge telemetry data from child instances via IPC."""
        if G.master_instance and G.launched_instances:
            self._ipc_telem_data = G.ipc_instance._ipc_telem
            if self._ipc_telem_data:
                telem_data.update(self._ipc_telem_data)
                self._ipc_telem_data.clear()

    def _extract_aircraft_info(self, telem_data: BaseTelemetryData) -> AircraftInfo:
        """Extract aircraft information and determine the appropriate module."""
        aircraft_name = telem_data.get("N")
        data_source = telem_data.get("src", None)

        # Defaults — used when data_source is None or unrecognised (e.g. malformed packet)
        module = None
        sc_aircraft_type = None
        sc_engine_type = None

        # Determine aircraft module based on data source
        if data_source == "MSFS":
            module = aircrafts_msfs_xp
            sc_aircraft_type = telem_data.get("SimconnectCategory", None)
            sc_engine_type = telem_data.get("EngineType", 4)
        elif data_source == "IL2":
            module = aircrafts_il2
            sc_aircraft_type = None
            sc_engine_type = None
        elif data_source == 'XPLANE':
            module = aircrafts_msfs_xp
            sc_aircraft_type = None
            sc_engine_type = None
        elif data_source == 'BMS':
            module = aircrafts_dcs
            sc_aircraft_type = None
            sc_engine_type = None
        elif data_source == 'DCS':
            module = aircrafts_dcs
            sc_aircraft_type = None
            sc_engine_type = None

        return AircraftInfo(
            name=aircraft_name,
            data_source=data_source,
            module=module,
            sc_aircraft_type=sc_aircraft_type,
            sc_engine_type=sc_engine_type
        )

    def _handle_aircraft_changes(self, aircraft_info: AircraftInfo, telem_data: BaseTelemetryData):
        """Handle aircraft changes and initialization."""
        aircraft_name = aircraft_info.name

        if aircraft_name and aircraft_name != self.currentAircraftName:
            if self.currentAircraft is None or aircraft_name != self.currentAircraftName:
                self._initialize_new_aircraft(aircraft_info, telem_data)
            self.currentAircraftName = aircraft_name

    def _initialize_new_aircraft(self, aircraft_info: AircraftInfo, telem_data: BaseTelemetryData):
        """Initialize a new aircraft when it changes."""
        aircraft_name = aircraft_info.name
        data_source = aircraft_info.data_source

        logging.info(f"New aircraft loaded {aircraft_name}: resetting current aircraft config")
        self.currentAircraftConfig = {}

        params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
        Aircraft_Class = self._resolve_aircraft_class(aircraft_info, cls_name, params)

        self._handle_vpconf_setup(params)
        self._handle_command_runner(params)
        self._handle_configurator_overrides(params)

        logging.info(f"Creating handler for [blue]{aircraft_name}[/blue]: [dim]{Aircraft_Class.__module__}.{Aircraft_Class.__name__}[/dim]")

        # Create and configure new aircraft instance
        self.currentAircraft : AircraftBase = Aircraft_Class(aircraft_name)
        self.currentAircraft.apply_settings(params)
        self.currentAircraftConfig = params

        self._setup_simconnect_overrides(aircraft_name, data_source)
        self._setup_xpplugin_overrides(aircraft_name, data_source)
        # self._update_settings_ui()
        self.aircraftUpdated.emit()

    def _resolve_aircraft_class(self, aircraft_info: AircraftInfo, cls_name, params):
        """Resolve the appropriate aircraft class to use."""
        Aircraft_Class = getattr(aircraft_info.module, cls_name, None)
        if Aircraft_Class:
            logging.debug(f"CLASS={Aircraft_Class.__name__}")

        if not Aircraft_Class or Aircraft_Class.__name__ == "Aircraft":
            Aircraft_Class = self.resolve_aircraft_class_from_sc(aircraft_info, params)

        return Aircraft_Class

    def _handle_vpconf_setup(self, params):
        """Handle VPConf profile setup for the aircraft."""
        if "vpconf" in params:
            if G.current_vpconf_profile != params.get('vpconf', None) or G.force_reload_aircraft_trigger:
                upload_vpconf_profile(params['vpconf'], HapticEffect.device.serial)
                G.vpconf_configurator_gains = HapticEffect.device.get_gains()
                G.force_reload_aircraft_trigger = False
        else:
            self._handle_global_vpconf_default()

    def _handle_global_vpconf_default(self):
        """Handle global VPConf default profile setup."""
        load_global = G.system_settings.get("enableVPConfGlobalDefault", False)
        global_path = G.system_settings.get("pathVPConfStartup", "")
        if load_global and global_path != G.current_vpconf_profile:
            logging.info("Aircraft changed, current loaded vpconf no longer applicable, reloading configured global default profile")
            upload_vpconf_profile(global_path, HapticEffect.device.serial)
            G.vpconf_configurator_gains = HapticEffect.device.get_gains()

    def _handle_command_runner(self, params):
        """Handle command runner execution for the aircraft."""
        if params.get('command_runner_enabled', False):
            command = params.get('command_runner_command', '')
            if command and 'Enter full path' not in command:
                try:
                    subprocess.Popen(command, shell=True)
                except Exception as e:
                    logging.error(f"Error running Command Executor for model: {e}")

    def _handle_configurator_overrides(self, params):
        """Handle configurator gain overrides for the aircraft."""
        if params.get('configurator_override_enabled', False):
            state = params.get('configurator_gains', 'none')
            if state != "none":
                state = json.loads(state)
                G.gain_override_dialog.set_gains_from_state(state)
                G.current_configurator_gains = state
                any_true = any(sub.get('enabled', False) for sub in state.values())
                self.gain_overrides_active = any_true
                G.main_window.refresh_scope_status_indicators(force=True)
            else:
                G.gain_override_dialog.set_gains_from_object(G.vpconf_configurator_gains)
                self.gain_overrides_active = False
                G.main_window.refresh_scope_status_indicators(force=True)
        else:
            if self.gain_overrides_active:
                G.gain_override_dialog.set_gains_from_object(G.vpconf_configurator_gains)
                self.gain_overrides_active = False
                G.main_window.refresh_scope_status_indicators(force=True)

    def _setup_simconnect_overrides(self, aircraft_name, data_source):
        """Setup SimConnect variable overrides for MSFS aircraft."""
        if data_source == "MSFS" and aircraft_name:
            overrides = xmlutils.read_sc_overrides(aircraft_name)
            for sv in overrides:
                self._simconnect.add_simvar(name=sv['name'], var=sv['var'], sc_unit=sv['sc_unit'], scale=sv['scale'])
            self._simconnect._resubscribe()

    def _setup_xpplugin_overrides(self, aircraft_name, data_source):
        """Setup Dataref variable overrides for XPLANE aircraft."""
        if data_source == "XPLANE" and aircraft_name != '':
            if not getattr(self, "_socket", None):
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
            d1 = xmlutils.read_sc_overrides(aircraft_name)
            for sv in d1:
                scale = sv['scale'] if sv['scale'] is not None or sv['scale'] == '' else 1.0
                sendstr = f"SUBSCRIBE:dataref={sv['var']},type={sv['sc_unit']},tag={sv['name']},precision=3,conversion={scale}"
                # if sv['scale'] is None or sv['scale'] == '':
                #     print(f"SUBSCRIBE:dataref={sv['var']},type={sv['sc_unit']},tag={sv['name']},precision=3,conversion=>{scale}<")
                # sendstr = f"SUBSCRIBE:dataref=sim/flightmodel/position/latitude,type=float,tag=LLLatitude,precision=6,conversion=0.51444"
                self._socket.sendto(bytes(sendstr, "utf-8"), ("127.0.0.1", 34391))

    # def _update_settings_ui(self):
    #     """Update settings UI if visible."""
    #     if G.settings_mgr.isVisible():
    #         G.settings_mgr.b_getcurrentmodel.click()

    def _handle_config_changes(self, aircraft_info: AircraftInfo):
        """Handle configuration changes for existing aircraft."""
        if self.currentAircraft and config_has_changed():
            xmlutils.update_roots()  # ride the same logic and Update the xml tree in xmlutils when the config changes
            logging.info("Configuration has changed, reloading")
            aircraft_name = aircraft_info.name
            data_source = aircraft_info.data_source

            params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
            updated_params = self.get_changed_params(params)
            self.currentAircraft.apply_settings(updated_params)

            self._handle_vpconf_setup(params)
            self._handle_command_runner(params)
            self._handle_configurator_overrides_update(params)

            if "type" in updated_params:
                self._recreate_aircraft_with_new_type(aircraft_info, params, cls_name)

            self._setup_simconnect_overrides(aircraft_name, data_source)
            self._setup_xpplugin_overrides(aircraft_name, data_source)
            self.aircraftUpdated.emit()

    def _handle_configurator_overrides_update(self, params):
        """Handle configurator overrides during config updates."""
        if params.get('configurator_override_enabled', False):
            state = params.get('configurator_gains', 'none')
            if state != "none":
                state = json.loads(state)
                G.gain_override_dialog.set_gains_from_state(state)
                G.current_configurator_gains = state
                any_true = any(sub.get('enabled', False) for sub in state.values())
                self.gain_overrides_active = any_true
                G.main_window.refresh_scope_status_indicators(force=True)
            else:
                G.gain_override_dialog.set_gains_from_object(G.vpconf_configurator_gains)
                self.gain_overrides_active = False
                G.main_window.refresh_scope_status_indicators(force=True)

    def _recreate_aircraft_with_new_type(self, aircraft_info: AircraftInfo, params, cls_name):
        """Recreate aircraft instance when type changes."""
        Aircraft_Class = getattr(aircraft_info.module, cls_name, None)
        self.currentAircraft = Aircraft_Class(aircraft_info.name)
        self.currentAircraft.apply_settings(params)
        self.currentAircraftConfig = params

    def _process_current_aircraft_telemetry(self, telem_data: BaseTelemetryData):
        """Process telemetry for the current aircraft."""
        if self.currentAircraft:
            try:
                _tm = time.perf_counter()
                self.currentAircraft._last_telem_data = self.currentAircraft._telem_data.copy()
                self.currentAircraft._telem_data = telem_data
                self.currentAircraft.on_telemetry(telem_data)
                telem_data["perf"] = f"{(time.perf_counter() - _tm) * 1000:.3f}ms"
            except Exception:
                logging.exception(".on_telemetry Exception")

    def _handle_ipc_and_plotting(self, telem_data: BaseTelemetryData):
        """Handle IPC telemetry sending and plotting."""
        # Send locally generated telemetry to master
        if G.child_instance and self.currentAircraft:
            ipc_telem = self.currentAircraft._ipc_telem
            if ipc_telem:
                G.ipc_instance.send_ipc_telem(ipc_telem)
                self.currentAircraft._ipc_telem.clear()

        # Handle plotting
        if G.args.plot:
            for item in G.args.plot:
                if item in telem_data:
                    if G.child_instance or G.launched_instances:
                        utils.teleplot.sendTelemetry(item, telem_data[item], instance=G.device_type)
                    else:
                        utils.teleplot.sendTelemetry(item, telem_data[item])

    def _emit_telemetry(self, telem_data: BaseTelemetryData):
        """Emit telemetry signal safely."""
        try:
            self.telemetryReceived.emit(telem_data)
        except:
            pass  # Qt object may be destroyed on exit

    def resolve_aircraft_class_from_sc(self, aircraft_info: AircraftInfo, params):
        """Resolve the aircraft class based on SimConnect data."""
        aircraftClass = None
        aircraft_name = aircraft_info.name
        data_source = aircraft_info.data_source
        module = aircraft_info.module
        sc_aircraft_type = aircraft_info.sc_aircraft_type
        sc_engine_type = aircraft_info.sc_engine_type

        if data_source == "MSFS":
            if sc_aircraft_type == "Helicopter":
                logging.warning("Aircraft definition not found, using SimConnect Data (Helicopter Type)")
                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS.Helicopter")
                params.update(type_cfg)
                aircraftClass = module.Helicopter
            elif sc_aircraft_type == "Jet":
                logging.warning("Aircraft definition not found, using SimConnect Data (Jet Type)")
                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS.JetAircraft")
                params.update(type_cfg)
                aircraftClass = module.JetAircraft
            elif sc_aircraft_type == "Airplane":
                if sc_engine_type == 0:     # Piston
                    logging.warning("Aircraft definition not found, using SimConnect Data (Propeller Type)")
                    type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS.PropellerAircraft")
                    params.update(type_cfg)
                    aircraftClass = module.PropellerAircraft
                elif sc_engine_type == 1:     # Jet
                    logging.warning("Aircraft definition not found, using SimConnect Data (Jet Type)")
                    type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS.JetAircraft")
                    params.update(type_cfg)
                    aircraftClass = module.JetAircraft
                elif sc_engine_type == 2:   # None
                    logging.warning("Aircraft definition not found, using SimConnect Data (Glider Type)")
                    type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS.GliderAircraft")
                    params.update(type_cfg)
                    aircraftClass = module.GliderAircraft
                elif sc_engine_type == 3:   # Heli
                    logging.warning("Aircraft definition not found, using SimConnect Data (Helo Type)")
                    type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS.HelicopterAircraft")
                    params.update(type_cfg)
                    aircraftClass = module.Helicopter
                elif sc_engine_type == 5:   # Turboprop
                    logging.warning("Aircraft definition not found, using SimConnect Data (Turboprop Type)")
                    type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS.TurbopropAircraft")
                    params.update(type_cfg)
                    aircraftClass = module.TurbopropAircraft
                elif sc_engine_type == 6:   # Electric - just use PropellerAircraft type for this
                    logging.warning("Aircraft definition not found, using SimConnect Data (Propeller Type)")
                    type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS.PropellerAircraft")
                    params.update(type_cfg)
                    aircraftClass = module.PropellerAircraft
            else:
                logging.warning(f"Aircraft definition not found, using default class for {aircraft_name}")
                aircraftClass = module.Aircraft
        else:
            logging.warning(f"Aircraft definition not found, using default class for {aircraft_name}")
            aircraftClass = module.Aircraft

        return aircraftClass

    def getTelemValue(self, key):
        if self.currentAircraft:
            return self.currentAircraft._telem_data.get(key, None)

    def on_timeout(self):
        """Called by the run() loop each time the telemetry condition variable times out
        without receiving new data.  Fires once per timeout event (guarded by timed_out)
        to stop active FFB effects and signal the UI.  The run() loop then arms the
        process-check deadline so _check_sim_process() is called periodically until
        telemetry resumes or the sim process disappears."""
        if self.currentAircraft and not self.timed_out:
            src = self.currentAircraft._telem_data.get('src', 'unknown')
            logging.info(
                f"Telemetry timeout from {src} — no data received for {self.timeout_sec * 1000:.0f}ms. "
                f"Process status will be checked every {self._PROCESS_CHECK_INTERVAL:.0f}s."
            )
            self.currentAircraft.on_timeout()
            self.telemetryTimeout.emit(True)
            self.timed_out = True
            G.settings_mgr.timed_out = True

    # Mapping from the telemetry 'src' tag to the list of known OS process names for
    # that sim.  Multiple names per entry cover platform variants (Windows .exe vs.
    # native Linux binary) and multiple sim versions (e.g. MSFS 2020 / 2024).
    # Matching in _check_sim_process() is case-insensitive via psutil.process_iter().
    _SIM_PROCESS_NAMES: dict = {
        'DCS':    ['DCS.exe', 'DCS'],
        'IL2':    ['IL-2.exe', 'IL-2', 'IL2Series.exe'],
        'MSFS':   ['FlightSimulator.exe', 'FlightSimulator2024.exe'],
        'BMS':    ['Falcon BMS.exe', 'falcon'],
        'XPLANE': ['X-Plane.exe', 'X-Plane-x86_64', 'X-Plane'],
    }
    _PROCESS_CHECK_INTERVAL = 5.0   # seconds between successive process checks while telemetry is timed out
    _PROCESS_CHECK_DELAY    = 5.0   # grace period (seconds) after the first timeout before the first check fires

    def _check_sim_process(self) -> None:
        """Universal sim-exit detector, called periodically by the run() loop while
        telemetry is timed out.

        Rationale: not all sims send an explicit exit signal over their telemetry
        transport (IL2 being the primary example).  By watching the OS process list
        after a timeout we can detect a sim exit for any supported sim without
        requiring per-sim exit handling in each transport.

        Behaviour:
        - Looks up the active sim's src tag in _SIM_PROCESS_NAMES.
        - Uses psutil (cross-platform: Windows and Linux) to scan the process list.
        - If the sim process is still running, logs and returns — no action taken.
        - If the sim process is gone, calls notify_sim_exited() which stops FFB
          effects, clears aircraft state, and triggers a full listener restart.
        - If psutil fails for any reason the check is skipped and the sim is assumed
          to still be running (fail-safe — we never fire a false exit).
        """
        if self._sim_exit_signaled:
            return
        src = None
        if self.currentAircraft:
            src = self.currentAircraft._telem_data.get('src')
        if not src:
            return
        process_names = self._SIM_PROCESS_NAMES.get(src)
        if not process_names:
            return   # sim not in the table — skip check rather than risk a false exit
        logging.debug(f"Process check: looking for {src} process ({', '.join(process_names)})")
        try:
            names_lower = {n.lower() for n in process_names}
            running = any(
                p.info['name'].lower() in names_lower
                for p in psutil.process_iter(['name'])
            )
        except Exception as e:
            logging.error(f"Process check for {src} failed ({e}); assuming still running")
            return   # fail-safe: don't fire exit if we can't determine state
        if running:
            logging.debug(f"Process check: {src} is still running")
        else:
            logging.info(f"Process check: {src} process not found ({process_names})— treating as sim exit")
            self.notify_sim_exited(src)

    def run(self):
        """Main telemetry processing loop.

        Waits on a condition variable for incoming telemetry data or events.
        If the wait times out (no data received within telemTimeout ms) it:
          1. Calls on_timeout() once to stop FFB effects and signal the UI.
          2. Arms a delayed deadline (_PROCESS_CHECK_DELAY seconds after the first
             timeout) so brief pauses don't trigger a false exit detection.
          3. Fires _check_sim_process() on each subsequent interval
             (_PROCESS_CHECK_INTERVAL seconds) until data resumes or the sim
             process disappears, at which point notify_sim_exited() is called.

        When data arrives after a timeout the timeout state is cleared and the
        process-check deadline is cancelled — telemetry has resumed normally.
        """
        self.timeout_sec = int(G.system_settings.get('telemTimeout', 200))/1000.0
        logging.info(f"Telemetry timeout: {self.timeout_sec}")
        self._run = True
        while self._run:
            with self._cond:
                if not self._events and not self._data:
                    if not self._cond.wait(self.timeout_sec):
                        self.on_timeout()

                        # Arm the process-check deadline on the first timeout.
                        # The _PROCESS_CHECK_DELAY grace period lets us ignore brief
                        # pauses (e.g. loading screens) before declaring a sim exit.
                        if self.timed_out and self._process_check_deadline is None:
                            self._process_check_deadline = (
                                time.perf_counter() + self._PROCESS_CHECK_DELAY
                            )

                        # Fire a process check when the deadline is reached, then
                        # reschedule for the next interval so we keep polling until
                        # telemetry resumes or the sim process is gone.
                        if (self._process_check_deadline is not None
                                and time.perf_counter() >= self._process_check_deadline):
                            self._process_check_deadline = (
                                time.perf_counter() + self._PROCESS_CHECK_INTERVAL
                            )
                            self._check_sim_process()

                        continue

                if self._data:
                    if self.timed_out:
                        # Data has resumed after a timeout — clear timeout state and
                        # cancel the process-check so it doesn't fire spuriously.
                        self.telemetryTimeout.emit(False)
                        self.timed_out = False
                        self._process_check_deadline = None  # sim resumed; cancel check

                    G.settings_mgr.timed_out = False
                    data = self._data
                    self._data = None
                    self.process_data(data)
                
                if self._events:
                    self.process_events()