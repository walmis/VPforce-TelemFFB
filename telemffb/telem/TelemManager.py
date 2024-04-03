import telemffb.globals as G
import telemffb.utils as utils
from telemffb.utils import set_vpconf_profile
import telemffb.xmlutils as xmlutils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.settingsmanager import logging, pyqtSignal, utils, xmlutils
from telemffb.sim import aircrafts_dcs, aircrafts_il2, aircrafts_msfs_xp

from PyQt5.QtCore import QObject, pyqtSignal


import logging
import subprocess
import threading
import time
import traceback
import os

_config_mtime = 0
_future_config_update_time = time.time()
_pending_config_update = False

def config_has_changed(update=False) -> bool:
    # if update is true, update the current modified time
    global _config_mtime, _future_config_update_time, _pending_config_update
    # "hash" both mtimes together
    tm = int(os.path.getmtime(G.userconfig_path)) + int(os.path.getmtime(G.defaults_path))
    time_now = time.time()
    update_delay = 0.4
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
    updateSettingsLayout = pyqtSignal()
    currentAircraft: aircrafts_dcs.Aircraft = None
    currentAircraftName: str = None
    currentAircraftConfig: dict = {}
    timedOut: bool = True
    lastFrameTime: float
    numFrames: int = 0

    def __init__(self, settings_manager, ipc_thread=None) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self, daemon=True)

        self._run = True
        self._cond = threading.Condition()
        self._data = None
        self._events = []
        self._dropped_frames = 0
        self.lastFrameTime = time.perf_counter()
        self.frameTimes = []
        self.maxframeTime = 0
        self.timeout = 0.2
        self.settings_manager = settings_manager
        self.ipc_thread = ipc_thread
        self._ipc_telem = {}

    @classmethod
    def set_simconnect(cls, sc):
        cls._simconnect = sc

    def get_aircraft_config(self, aircraft_name, data_source):
        params = {}
        cls_name = "UNKNOWN"
        input_modeltype = ''
        try:
            if data_source == "MSFS2020":
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

            cls_name, pattern, result = xmlutils.read_single_model(the_sim, aircraft_name, input_modeltype, G._device_type)
            #globals.settings_mgr.current_pattern = pattern
            if cls_name == '': cls_name = 'Aircraft'
            for setting in result:
                k = setting['name']
                v = setting['value']
                u = setting['unit']
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
            self.settings_manager.update_state_vars(
                current_sim=the_sim,
                current_aircraft_name=aircraft_name,
                current_class=cls_name,
                current_pattern=pattern)

            return params, cls_name

            # logging.info(f"Got settings from settingsmanager:\n{formatted_result}")
        except Exception as e:
            logging.warning(f"Error getting settings from Settings Manager:{e}")

    def quit(self):
        self._run = False
        self.join()

    def submitFrame(self, data: bytes):
        if type(data) == bytes:
            data = data.decode("utf-8")

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
                logging.debug(f"Droppped frame (total {self._dropped_frames})")

    def process_events(self):
        while len(self._events):
            ev = self._events.pop(0)
            ev = ev.split(";")

            if self.currentAircraft:
                self.currentAircraft.on_event(*ev)
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

        data = data.split(";")

        telem_data = {}
        telem_data["FFBType"] = G._device_type

        self.frameTimes.append(int((time.perf_counter() - self.lastFrameTime)*1000))
        if len(self.frameTimes) > 500: self.frameTimes.pop(0)

        if self.frameTimes[-1] > self.maxframeTime and len(self.frameTimes) > 40:  # skip the first frames before counting frametime as max
            threshold = 100
            if self.frameTimes[-1] > threshold:
                logging.debug(
                    f'*!*!*!* - Frametime threshold of {threshold}ms exceeded: time = {self.frameTimes[-1]}ms')

            self.maxframeTime = self.frameTimes[-1]

        telem_data["frameTimes"] = [self.frameTimes[-1], max(self.frameTimes)]
        telem_data["maxFrameTime"] = f"{round(self.maxframeTime, 3)}"
        telem_data["avgFrameTime"] = f"{round(sum(self.frameTimes) / len(self.frameTimes), 3):.3f}"

        self.lastFrameTime = time.perf_counter()

        for i in data:
            try:
                if len(i):
                    section, conf = i.split("=")
                    values = conf.split("~")
                    telem_data[section] = [utils.to_number(v) for v in values] if len(values) > 1 else utils.to_number(conf)

            except Exception as e:
                traceback.print_exc()
                logging.error("Error Parsing Parameter: ", repr(i))

        # Read telemetry sent via IPC channel from child instances and update local telemetry stream
        if G._master_instance and G._launched_children:
            self._ipc_telem = self.ipc_thread._ipc_telem
            if self._ipc_telem != {}:
                telem_data.update(self._ipc_telem)
                self._ipc_telem = {}
        # print(items)
        aircraft_name = telem_data.get("N")
        data_source = telem_data.get("src", None)
        if data_source == "MSFS2020":
            module = aircrafts_msfs_xp
            sc_aircraft_type = telem_data.get("SimconnectCategory", None)
            sc_engine_type = telem_data.get("EngineType", 4)
            # 0 = Piston
            # 1 = Jet
            # 2 = None
            # 3 = Helo(Bell) turbine
            # 4 = Unsupported
            # 5 = Turboprop
        elif data_source == "IL2":
            module = aircrafts_il2
        elif data_source == 'XPLANE':
            module = aircrafts_msfs_xp
        else:
            module = aircrafts_dcs

        if aircraft_name and aircraft_name != self.currentAircraftName:

            if self.currentAircraft is None or aircraft_name != self.currentAircraftName:
                logging.info(f"New aircraft loaded: resetting current aircraft config")
                self.currentAircraftConfig = {}

                params, cls_name = self.get_aircraft_config(aircraft_name, data_source)

                # self.settings_manager.update_current_aircraft(send_source, aircraft_name, cls_name)
                Class = getattr(module, cls_name, None)
                logging.debug(f"CLASS={Class.__name__}")

                if not Class or Class.__name__ == "Aircraft":
                    if data_source == "MSFS2020":
                        if sc_aircraft_type == "Helicopter":
                            logging.warning(f"Aircraft definition not found, using SimConnect Data (Helicopter Type)")
                            type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.Helicopter")
                            params.update(type_cfg)
                            Class = module.Helicopter
                        elif sc_aircraft_type == "Jet":
                            logging.warning(f"Aircraft definition not found, using SimConnect Data (Jet Type)")
                            type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.JetAircraft")
                            params.update(type_cfg)
                            Class = module.JetAircraft
                        elif sc_aircraft_type == "Airplane":
                            if sc_engine_type == 0:     # Piston
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Propeller Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.PropellerAircraft")
                                params.update(type_cfg)
                                Class = module.PropellerAircraft
                            if sc_engine_type == 1:     # Jet
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Jet Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.JetAircraft")
                                params.update(type_cfg)
                                Class = module.JetAircraft
                            elif sc_engine_type == 2:   # None
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Glider Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.GliderAircraft")
                                params.update(type_cfg)
                                Class = module.GliderAircraft
                            elif sc_engine_type == 3:   # Heli
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Helo Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.HelicopterAircraft")
                                params.update(type_cfg)
                                Class = module.Helicopter
                            elif sc_engine_type == 5:   # Turboprop
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Turboprop Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.TurbopropAircraft")
                                params.update(type_cfg)
                                Class = module.TurbopropAircraft
                        else:
                            logging.warning(f"Aircraft definition not found, using default class for {aircraft_name}")
                            Class = module.Aircraft
                    else:
                        logging.warning(f"Aircraft definition not found, using default class for {aircraft_name}")
                        Class = module.Aircraft

                if "vpconf" in params:
                    set_vpconf_profile(params['vpconf'], HapticEffect.device.serial)

                if params.get('command_runner_enabled', False):
                    if params.get('command_runner_command', '') != '':
                        try:
                            subprocess.Popen(params['command_runner_command'], shell=True)
                        except Exception as e:
                            logging.error(f"Error running Command Executor for model: {e}")

                logging.info(f"Creating handler for {aircraft_name}: {Class.__module__}.{Class.__name__}")

                # instantiate new aircraft handler
                self.currentAircraft = Class(aircraft_name)

                self.currentAircraft.apply_settings(params)
                self.currentAircraftConfig = params
                if data_source == "MSFS2020" and aircraft_name != '':
                    d1 = xmlutils.read_overrides(aircraft_name)
                    for sv in d1:
                        self._simconnect.addSimVar(name=sv['name'], var=sv['var'], sc_unit=sv['sc_unit'], scale=sv['scale'])
                    self._simconnect._resubscribe()
                if G.settings_mgr.isVisible():
                    G.settings_mgr.b_getcurrentmodel.click()

                self.updateSettingsLayout.emit()

            self.currentAircraftName = aircraft_name

        if self.currentAircraft:
            if config_has_changed():
                logging.info("Configuration has changed, reloading")
                params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
                updated_params = self.get_changed_params(params)
                self.currentAircraft.apply_settings(updated_params)

                if "vpconf" in updated_params:
                    set_vpconf_profile(params['vpconf'], HapticEffect.device.serial)

                if params.get('command_runner_enabled', False):
                    if params.get('command_runner_command', '') != '' and 'Enter full path' not in params.get('command_runner_command', ''):
                        try:
                            subprocess.Popen(params['command_runner_command'], shell=True)
                        except Exception as e:
                            logging.error(f"Error running Command Executor for model: {e}")

                if "type" in updated_params:
                    # if user changed type or if new aircraft dialog changed type, update aircraft class
                    Class = getattr(module, cls_name, None)
                    self.currentAircraft = Class(aircraft_name)
                    self.currentAircraft.apply_settings(params)
                    self.currentAircraftConfig = params

                if data_source == "MSFS2020" and aircraft_name != '':
                    d1 = xmlutils.read_overrides(aircraft_name)
                    for sv in d1:
                        self._simconnect.addSimVar(name=sv['name'], var=sv['var'], sc_unit=sv['sc_unit'], scale=sv['scale'])
                    self._simconnect._resubscribe()

                self.updateSettingsLayout.emit()
            try:
                _tm = time.perf_counter()
                self.currentAircraft._telem_data = telem_data
                self.currentAircraft.on_telemetry(telem_data)
                telem_data["perf"] = f"{(time.perf_counter() - _tm) * 1000:.3f}ms"

            except:
                logging.exception(".on_telemetry Exception")

        # Send locally generated telemetry to master here
        if G.args.child and self.currentAircraft:
            ipc_telem = self.currentAircraft._ipc_telem
            if ipc_telem != {}:
                self.ipc_thread.send_ipc_telem(ipc_telem)
                self.currentAircraft._ipc_telem = {}
        if G.args.plot:
            for item in G.args.plot:
                if item in telem_data:
                    if G._child_instance or G._launched_children:
                        utils.teleplot.sendTelemetry(item, telem_data[item], instance=G._device_type)
                    else:
                        utils.teleplot.sendTelemetry(item, telem_data[item])

        try:  # sometime Qt object is destroyed first on exit and this may cause a runtime exception
            self.telemetryReceived.emit(telem_data)
        except: pass

    def on_timeout(self):
        if self.currentAircraft and not self.timedOut:
            self.currentAircraft.on_timeout()
        self.timedOut = True
        self.settings_manager.timedOut = True

    def run(self):
        self.timeout = int(utils.read_system_settings(G._device_vid_pid, G._device_type).get('telemTimeout', 200))/1000
        logging.info(f"Telemetry timeout: {self.timeout}")
        while self._run:
            with self._cond:
                if not len(self._events) and not self._data:
                    if not self._cond.wait(self.timeout):
                        self.on_timeout()
                        continue

                if len(self._events):
                    self.process_events()

                if self._data:
                    self.timedOut = False
                    self.settings_manager.timedOut = False
                    data = self._data
                    self._data = None
                    self.process_data(data)