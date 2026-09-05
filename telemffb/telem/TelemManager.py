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
# time.time() of the last mtime stat(); None until the first check. The mtime()
# syscall previously ran on every telemetry frame (60-120 Hz). Config edits are
# human-scale, so re-statting at most once per _MTIME_CHECK_INTERVAL removes the
# per-frame filesystem I/O from the frame thread, at the cost of at most
# _MTIME_CHECK_INTERVAL of extra change-detection latency.
_last_mtime_check = None
_MTIME_CHECK_INTERVAL = 0.1

def config_has_changed(update=False) -> bool:
    # if update is true, update the current modified time
    global _config_mtime, _future_config_update_time, _pending_config_update, _last_mtime_check
    time_now = time.time()
    update_delay = 0.1  # Delay added here to avoid file access errors with multiple instances

    if _last_mtime_check is None or time_now - _last_mtime_check >= _MTIME_CHECK_INTERVAL:
        _last_mtime_check = time_now
        # "hash" both mtimes together
        tm = int(os.path.getmtime(G.userconfig_path)) + int(os.path.getmtime(G.defaults_path))
        if not _config_mtime:
            # First real check: seed the baseline and report no change, to avoid
            # a spurious config load on the very first frame.
            _config_mtime = tm
        elif _config_mtime != tm:
            _future_config_update_time = time_now + update_delay
            _pending_config_update = True
            _config_mtime = tm
            logging.info(f'Config changed: Waiting {update_delay} seconds to read changes')

    if _pending_config_update and time_now >= _future_config_update_time:
        _pending_config_update = False
        logging.info(f'Config changed: {update_delay} second timer expired, reading changes')
        return True
    return False

def force_config_change() -> None:
    """Force the next config_has_changed() call to report a change, regardless
    of the file mtime. Used after a programmatic XML write (e.g. the DCS
    settings channel) where two writes in the same wall-clock second would
    otherwise produce an identical integer-second mtime and be missed."""
    global _pending_config_update, _future_config_update_time
    _pending_config_update = True
    _future_config_update_time = time.time()

class TelemManager(QObject, threading.Thread):
    telemetryReceived = pyqtSignal(object)
    eventReceived = pyqtSignal(tuple)

    aircraftUpdated = pyqtSignal()
    telemetryTimeout = pyqtSignal(bool)

    first_frame_received = pyqtSignal(str)
    sim_exited = pyqtSignal(str)   # emitted when a sim exit detected
    #: per-aircraft device swap, handled on the main thread (the device's
    #: read timer must live there); the payload is the devpath to acquire,
    #: or '' for the stored primary
    deviceSwapRequested = pyqtSignal(str)

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
        self._vpconf_deferred_frame = None   # single-slot buffer for a frame arriving during the startup vpconf push
        self._flushing_deferred = False      # True only while re-injecting the deferred frame (diagnostic logging)
        self._first_frame_from_sim = False
        self._sim_exit_signaled = False   # True after notify_sim_exited fires; prevents re-entrancy until reset_sim_connected() clears it
        self._process_check_deadline: Optional[float] = None  # perf_counter() timestamp of the next scheduled process check; None when inactive
        self._device_swap_attempt = None  # (aircraft, devpath) already requested - one attempt per aircraft


    def set_paused(self, pause_state: bool = False):
        self.pause_state = pause_state

    def frame_hold(self):
        """The lock guarding frame processing.

        Pausing only gates NEW frames; one already being processed (or
        buffered) keeps driving effects.  A device teardown pauses, then
        acquires this for the duration, so no frame ever writes to a
        half-switched device - the per-aircraft swap made that race a
        near-certainty, since the request originates mid-frame."""
        return self._cond

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
            try:
                self.currentAircraft.on_timeout()
            except Exception as e:
                # A crashing aircraft hook must not abort the rest of the
                # sim-exit cleanup (effect release, listener restart).
                logging.error(f"Aircraft on_timeout failed for {src}: {e}", exc_info=True)
            # on_timeout() is *pause* semantics: with keep_forces_on_pause it
            # deliberately leaves the condition effects running so the stick
            # does not go limp mid-session.  A sim exit ends the session, and
            # currentAircraft is dropped immediately below, so without this
            # those forces would stay on the device with nothing left to
            # manage them.  Frees each effect individually - never a device
            # reset, which would also wipe effects the sim itself created.
            freed = HapticEffect.destroy_all()
            if freed:
                logging.info(f"Sim exit: freed {freed} effect(s) from the device")
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
                # Attach the unit only when there is a value to attach it to. An
                # empty value (e.g. the shipped default for vne_override, which is
                # the documented "leave blank to use sim data") must stay empty:
                # concatenating a <unit> to it produced a bare unit string ("kt")
                # that to_number() returns verbatim, so the value reached aircraft
                # code as a string and crashed downstream arithmetic (`vne * ms2kt`).
                vu = (v + u) if v else v
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
        frame = self._vpconf_deferred_frame
        self._vpconf_deferred_frame = None
        if frame is None:
            return
        logging.info("Delivering telemetry frame deferred during the "
                     "startup vpconf push")
        # _flushing_deferred lets the drop/pause branches below distinguish a
        # re-injected startup frame (a real loss) from an ordinary dropped
        # frame, without logging on the per-frame hot path.
        self._flushing_deferred = True
        try:
            self.submit_frame(frame)
        finally:
            self._flushing_deferred = False

    def submit_frame(self, data_in: bytes):
        if G.vpconf_init_pending:
            # Startup vpconf push not complete yet (reset by
            # utils.init_vpconf_profile, which then flushes the frame stashed
            # here). DEFER the frame instead of discarding it: while MSFS sits
            # in the menus the SimConnect stop latch delivers exactly ONE
            # telemetry packet, and dropping it in this window left the master
            # instance with no aircraft until a camera-state change — a
            # milliseconds-wide race, master-only, maddeningly intermittent.
            if self._vpconf_deferred_frame is None:
                # log once per window (empty->full); overwrites stay silent
                logging.info("Deferring telemetry frame during startup vpconf push")
            self._vpconf_deferred_frame = data_in
            return
        if self.pause_state:
            # don't process frames while paused state True
            if self._flushing_deferred:
                logging.warning("Deferred startup frame swallowed by pause_state gate")
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
                if self._flushing_deferred:
                    logging.warning("Deferred startup frame dropped on re-inject "
                                    "(previous frame not yet consumed)")
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

        self._handle_device_selection(aircraft_name, params)
        self._handle_vpconf_setup(params)
        self._handle_command_runner(params)
        self._handle_configurator_overrides(params)

        logging.info(f"Creating handler for [blue]{aircraft_name}[/blue]: [dim]{Aircraft_Class.__module__}.{Aircraft_Class.__name__}[/dim]")

        # Create and configure new aircraft instance
        self.currentAircraft : AircraftBase = Aircraft_Class(aircraft_name)
        self.currentAircraft.apply_settings(params)
        self.currentAircraftConfig = params
        self._stamp_trim_cal_availability(data_source, cls_name)

        self._setup_simconnect_overrides(aircraft_name, data_source)
        self._setup_xpplugin_overrides(aircraft_name, data_source)
        # self._update_settings_ui()
        self.aircraftUpdated.emit()

    def _handle_device_selection(self, aircraft_name, params):
        """Acquire the device this aircraft asks for (per-aircraft swap).

        The ``joystick_device`` aircraft setting stores the devpath of one
        of the joystick role's configured devices; absent or empty means
        the user's primary.  The swap is EPHEMERAL - the stored settings
        are never touched, so a restart, or the next aircraft without a
        preference, comes back up on the primary.  Only the joystick
        instance acts: the setting addresses the one role that can hold
        alternate devices.

        The switch itself runs on the main thread (the device's read
        timer must live there), so this emits and finishes the current
        pass on the old device; the switch forces an aircraft reload
        whose second pass finds the right device already in place.  One
        request per aircraft: a swap that fails (device unplugged) falls
        back to the primary on the main-thread side and is not retried
        until another aircraft loads.
        """
        if G.device_type != 'joystick':
            return
        primary = str(G.system_settings.get('devpath_joystick', '') or '')
        value = str(params.get('joystick_device', '') or '')
        wanted = primary if value in ('', 'primary') else value
        if wanted != primary:
            configured = {primary} | {
                str(G.system_settings.get(f'devpath_joystick_{slot}', '') or '')
                for slot in (2, 3)}
            if wanted not in configured:
                logging.warning(
                    f"'{aircraft_name}' asks for a device that is no "
                    f"longer configured ({wanted}) - staying on the "
                    "primary device")
                wanted = primary
        current = str(getattr(G, 'device_devpath', '') or '')
        if wanted == current and getattr(G, 'device_connection_status', False):
            return
        request = (aircraft_name, wanted)
        if self._device_swap_attempt == request:
            return
        self._device_swap_attempt = request
        which = 'primary device' if wanted == primary else wanted
        logging.info(f"'{aircraft_name}' asks for {which} - requesting a "
                     "device swap")
        self.deviceSwapRequested.emit('' if wanted == primary else wanted)

    def _stamp_trim_cal_availability(self, data_source, cls_name):
        """Evaluate ONCE per aircraft load whether elevator trim-curve
        calibration applies to this aircraft, and stamp the instance so UI
        consumers (the main-window discovery prompt) read a plain attribute.

        Availability is a per-class CONFIGURATION fact, taken from the same
        "!class" exclusion markers in defaults.xml that hide the curve
        settings rows for helicopter classes — one data source, no class
        names in code. Prereq VALUES are deliberately ignored: a user who
        has not enabled trim following yet is still a valid discovery
        target (the whole chain defaults off out of the box); a class
        excluded anywhere along the chain is not.
        """
        ac = self.currentAircraft
        if ac is None:
            return
        available = False
        try:
            ds = str(data_source or "")
            sim = "MSFS" if "MSFS" in ds else ("XPLANE" if "XPLANE" in ds else None)
            if sim and hasattr(ac, "get_trim_calibrator"):
                _, removal = xmlutils.read_default_class_data(
                    sim, cls_name, G.device_type)
                chain = {"joystick_trim_follow_curve_y",
                         "joystick_trim_follow_use_curve_y",
                         "trim_following", "telemffb_controls_axes"}
                available = not (removal and chain.intersection(removal))
        except Exception as e:
            logging.debug(f"trim-cal availability stamp failed: {e}")
        ac._trim_cal_available = available

    def _resolve_aircraft_class(self, aircraft_info: AircraftInfo, cls_name, params):
        """Resolve the appropriate aircraft class to use."""
        Aircraft_Class = getattr(aircraft_info.module, cls_name, None)
        if Aircraft_Class:
            logging.debug(f"CLASS={Aircraft_Class.__name__}")

        if not Aircraft_Class or Aircraft_Class.__name__ == "Aircraft":
            Aircraft_Class = self.resolve_aircraft_class_from_sc(aircraft_info, params)

        return Aircraft_Class

    @staticmethod
    def _device_has_gains() -> bool:
        """VPConf profiles and Configurator gain overrides only apply to
        devices with Configurator gain sliders (VPforce hardware)."""
        caps = getattr(HapticEffect.device, 'caps', None)
        return caps is None or caps.has_gains

    def _handle_vpconf_setup(self, params):
        """Handle VPConf profile setup for the aircraft."""
        # Nothing to do (and no dereference) without a live device.  A
        # push arriving while the device is dead is dropped here, but
        # the firmware holds profiles in RAM only, so the recovery
        # replay (main._replay_device_setup) re-pushes the current
        # context's profile when the device comes back.
        if not HapticEffect.device_alive():
            return
        if not self._device_has_gains():
            if "vpconf" in params:
                logging.info("vpconf profile configured but this device has no Configurator gains; skipping")
            return
        if "vpconf" in params:
            if G.current_vpconf_profile != params.get('vpconf', None) or G.force_reload_aircraft_trigger:
                upload_vpconf_profile(params['vpconf'], HapticEffect.device.serial)
                G.vpconf_configurator_gains = HapticEffect.device.get_gains()
                G.force_reload_aircraft_trigger = False
        else:
            self._handle_global_vpconf_default()

    def _handle_global_vpconf_default(self):
        """Handle global VPConf default profile setup."""
        # Same drop rule as _handle_vpconf_setup: a push while the device
        # is dead is dropped here; the recovery replay re-pushes the
        # global default on the device's return, since the firmware
        # profile is RAM-only and died with the power cycle.
        if not HapticEffect.device_alive():
            return
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
        if not self._device_has_gains():
            if params.get('configurator_override_enabled', False):
                logging.info("configurator overrides enabled but this device has no Configurator gains; skipping")
            return
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

            # the user picked a different device for the LOADED aircraft:
            # the swap must not wait for the next aircraft change
            if 'joystick_device' in updated_params:
                self._handle_device_selection(aircraft_name, params)

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
        if not self._device_has_gains():
            return
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
        self._stamp_trim_cal_availability(aircraft_info.data_source, cls_name)

    def _process_current_aircraft_telemetry(self, telem_data: BaseTelemetryData):
        """Process telemetry for the current aircraft."""
        if self.currentAircraft:
            # A just-(re)opened device has no input snapshot until its
            # first report arrives, and the aircraft mixins read input
            # every frame assuming it is always there - hold frames for
            # the moment that takes (a device switch mid-session is when
            # this window actually gets hit).
            device = HapticEffect.device
            if device is not None:
                try:
                    if device.get_input() is None:
                        logging.debug(
                            "Telemetry frame skipped: no input report "
                            "from the device yet")
                        return
                except Exception:
                    pass
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

    def force_config_change(self) -> None:
        """Force a config re-read on the next frame (see module-level
        force_config_change). Called after a programmatic XML write so the
        change is picked up even if the file mtime didn't advance a whole
        second."""
        force_config_change()

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
            try:
                self.currentAircraft.on_timeout()
            except Exception as e:
                # A crashing aircraft hook must not block the timed_out
                # transition, or it would re-fire (and re-log with a stack
                # trace) on every timeout cycle.  One error per episode.
                logging.error(f"Aircraft on_timeout failed for {src}: {e}", exc_info=True)
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

    def _safe_call(self, what: str, fn) -> None:
        """Run a per-frame/per-event hook without letting exceptions kill this thread.

        The TelemManager thread is the only consumer of telemetry for every
        aircraft; a single unhandled exception (e.g. from a device that was
        never opened or was hot-unplugged) used to terminate the thread and
        freeze FFB permanently.  Exceptions here are logged with a stack
        trace and the loop continues - the same isolation the per-aircraft
        on_telemetry path already has.
        """
        try:
            fn()
        except Exception as e:
            logging.error(f"TelemManager {what} failed: {e}", exc_info=True)

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
                        self._safe_call("on_timeout", self.on_timeout)

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
                            self._safe_call("_check_sim_process", self._check_sim_process)

                        continue

                if self._data:
                    if self.timed_out:
                        # Data has resumed after a timeout — clear timeout state and
                        # cancel the process-check so it doesn't fire spuriously.
                        logging.info("Telemetry resumed after timeout")
                        self.telemetryTimeout.emit(False)
                        self.timed_out = False
                        self._process_check_deadline = None  # sim resumed; cancel check

                    G.settings_mgr.timed_out = False
                    data = self._data
                    self._data = None
                    self._safe_call("process_data", lambda: self.process_data(data))
                
                if self._events:
                    self._safe_call("process_events", self.process_events)
