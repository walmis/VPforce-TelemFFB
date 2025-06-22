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
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal
from typing import Optional, Tuple, TYPE_CHECKING

import telemffb.globals as G
import telemffb.utils as utils
from telemffb.utils import dbprint
import telemffb.xmlutils as xmlutils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim import aircrafts_dcs, aircrafts_il2, aircrafts_msfs_xp
from telemffb.telem.SimConnectManager import SimConnectManager
from telemffb.utils import set_vpconf_profile

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
    update_delay = 0.4  # Delay added here to avoid file access errors with multiple instances

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

    def set_simconnect(self, sc : SimConnectManager):
        self._simconnect = sc

    @property
    def simconnect(self) -> SimConnectManager:
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

            cls_name, pattern, result = xmlutils.read_single_model(the_sim, aircraft_name, input_modeltype, G.device_type)
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
                current_pattern=pattern)

            return params, cls_name

            # logging.info(f"Got settings from settingsmanager:\n{formatted_result}")
        except Exception as e:
            logging.exception(f"Error getting settings from Settings Manager:{e}")

    def quit(self):
        self._run = False
        self.join()

    def submit_frame(self, data_in: bytes):
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

    def _parse_telemetry_data(self, data):
        """Parse raw telemetry data and calculate frame timing metrics."""
        data = data.split(";")
        telem_data = {"FFBType": G.device_type}
        
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

    def _update_frame_timing(self, telem_data):
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

    def _merge_ipc_telemetry(self, telem_data):
        """Merge telemetry data from child instances via IPC."""
        if G.master_instance and G.launched_instances:
            self._ipc_telem_data = G.ipc_instance._ipc_telem
            if self._ipc_telem_data:
                telem_data.update(self._ipc_telem_data)
                self._ipc_telem_data.clear()

    def _extract_aircraft_info(self, telem_data) -> AircraftInfo:
        """Extract aircraft information and determine the appropriate module."""
        aircraft_name = telem_data.get("N")
        data_source = telem_data.get("src", None)
        
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
        else:
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

    def _handle_aircraft_changes(self, aircraft_info: AircraftInfo, telem_data):
        """Handle aircraft changes and initialization."""
        aircraft_name = aircraft_info.name
        
        if aircraft_name and aircraft_name != self.currentAircraftName:
            if self.currentAircraft is None or aircraft_name != self.currentAircraftName:
                self._initialize_new_aircraft(aircraft_info, telem_data)
            self.currentAircraftName = aircraft_name

    def _initialize_new_aircraft(self, aircraft_info: AircraftInfo, telem_data):
        """Initialize a new aircraft when it changes."""
        aircraft_name = aircraft_info.name
        data_source = aircraft_info.data_source
        module = aircraft_info.module
        
        logging.info(f"New aircraft loaded {aircraft_name}: resetting current aircraft config")
        self.currentAircraftConfig = {}

        params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
        Aircraft_Class = self._resolve_aircraft_class(aircraft_info, module, cls_name, params)
        
        self._handle_vpconf_setup(params)
        self._handle_command_runner(params)
        self._handle_configurator_overrides(params)
        
        logging.info(f"Creating handler for {aircraft_name}: {Aircraft_Class.__module__}.{Aircraft_Class.__name__}")
        
        # Create and configure new aircraft instance
        self.currentAircraft : AircraftBase = Aircraft_Class(aircraft_name)
        self.currentAircraft.apply_settings(params)
        self.currentAircraftConfig = params
        
        self._setup_simconnect_overrides(aircraft_name, data_source)
        self._update_settings_ui()
        self.aircraftUpdated.emit()

    def _resolve_aircraft_class(self, aircraft_info: AircraftInfo, module, cls_name, params):
        """Resolve the appropriate aircraft class to use."""
        Aircraft_Class = getattr(module, cls_name, None)
        
        if Aircraft_Class:
            logging.debug(f"CLASS={Aircraft_Class.__name__}")
        
        if not Aircraft_Class or Aircraft_Class.__name__ == "Aircraft":
            Aircraft_Class = self.resolve_aircraft_class_from_sc(
                aircraft_info.name, 
                aircraft_info.data_source, 
                module, 
                aircraft_info.sc_aircraft_type, 
                aircraft_info.sc_engine_type, 
                params
            )
        
        return Aircraft_Class

    def _handle_vpconf_setup(self, params):
        """Handle VPConf profile setup for the aircraft."""
        if "vpconf" in params:
            if G.current_vpconf_profile != params.get('vpconf', None) or G.force_reload_aircraft_trigger:
                set_vpconf_profile(params['vpconf'], HapticEffect.device.serial)
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
            set_vpconf_profile(global_path, HapticEffect.device.serial)
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
                self.gain_overrides_active = True
            else:
                G.gain_override_dialog.set_gains_from_object(G.vpconf_configurator_gains)
                self.gain_overrides_active = True
        else:
            if self.gain_overrides_active:
                G.gain_override_dialog.set_gains_from_object(G.vpconf_configurator_gains)
                self.gain_overrides_active = False

    def _setup_simconnect_overrides(self, aircraft_name, data_source):
        """Setup SimConnect variable overrides for MSFS aircraft."""
        if data_source == "MSFS" and aircraft_name:
            overrides = xmlutils.read_sc_overrides(aircraft_name)
            for sv in overrides:
                self._simconnect.add_simvar(name=sv['name'], var=sv['var'], sc_unit=sv['sc_unit'], scale=sv['scale'])
            self._simconnect._resubscribe()

    def _update_settings_ui(self):
        """Update settings UI if visible."""
        if G.settings_mgr.isVisible():
            G.settings_mgr.b_getcurrentmodel.click()

    def _handle_config_changes(self, aircraft_info: AircraftInfo):
        """Handle configuration changes for existing aircraft."""
        if self.currentAircraft and config_has_changed():
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
            self.aircraftUpdated.emit()

    def _handle_configurator_overrides_update(self, params):
        """Handle configurator overrides during config updates."""
        if params.get('configurator_override_enabled', False):
            state = params.get('configurator_gains', 'none')
            if state != "none":
                state = json.loads(state)
                G.gain_override_dialog.set_gains_from_state(state)
                G.current_configurator_gains = state
                self.gain_overrides_active = True
            else:
                G.gain_override_dialog.set_gains_from_object(G.vpconf_configurator_gains)
                self.gain_overrides_active = True

    def _recreate_aircraft_with_new_type(self, aircraft_info: AircraftInfo, params, cls_name):
        """Recreate aircraft instance when type changes."""
        Aircraft_Class = getattr(aircraft_info.module, cls_name, None)
        self.currentAircraft = Aircraft_Class(aircraft_info.name)
        self.currentAircraft.apply_settings(params)
        self.currentAircraftConfig = params

    def _process_current_aircraft_telemetry(self, telem_data):
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

    def _handle_ipc_and_plotting(self, telem_data):
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

    def _emit_telemetry(self, telem_data):
        """Emit telemetry signal safely."""
        try:
            self.telemetryReceived.emit(telem_data)
        except:
            pass  # Qt object may be destroyed on exit

    def resolve_aircraft_class_from_sc(self, aircraft_name, data_source, module, sc_aircraft_type, sc_engine_type, params):
        """Resolve the aircraft class based on SimConnect data."""
        aircraftClass = None
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
        if self.currentAircraft and not self.timed_out:
            self.currentAircraft.on_timeout()
            self.telemetryTimeout.emit(True)
            self.timed_out = True
            G.settings_mgr.timed_out = True

    def run(self):
        self.timeout_sec = int(G.system_settings.get('telemTimeout', 200))/1000.0
        logging.info(f"Telemetry timeout: {self.timeout_sec}")
        self._run = True
        while self._run:
            with self._cond:
                if not self._events and not self._data:
                    if not self._cond.wait(self.timeout_sec):
                        self.on_timeout()
                        continue

                if self._data:
                    if self.timed_out:
                        self.telemetryTimeout.emit(False)
                        self.timed_out = False

                    G.settings_mgr.timed_out = False
                    data = self._data
                    self._data = None
                    self.process_data(data)
                
                if self._events:
                    self.process_events()