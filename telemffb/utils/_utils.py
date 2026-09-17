#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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
import html
import os
import random
import re
import shutil
from collections import defaultdict
import threading

import logging

import socket
import time
import subprocess
import json

from PyQt6.QtCore import QCoreApplication, QSize, QObject, QSettings, Qt, QMetaObject, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QPixmap

from PyQt6 import QtCore
from PyQt6.QtWidgets import QMessageBox

import telemffb.globals as G
from ._math import to_number
from .filesystem import calculate_checksum, calculate_crc, get_resource_path

def schedule_on_main_thread(func):
    """
    Schedule a callable to execute in the main Qt thread.
    
    This is essential when calling GUI methods from worker threads (e.g., threading.Thread).
    Qt GUI objects must only be accessed from the thread they were created in (main thread).
    
    Args:
        func: A callable (lambda or function) to execute in the main thread
    
    Examples:
        # Lambda (simple and clean):
        schedule_on_main_thread(lambda: G.main_window.update_sim_indicators("dcs", True))
        schedule_on_main_thread(lambda: some_widget.setText("Hello"))
        
        # Function reference:
        def update_ui():
            G.main_window.statusBar().showMessage("Updated")
        schedule_on_main_thread(update_ui)
    """
    class CallableWrapper(QObject):
        # Keep references to wrapper objects to prevent garbage collection
        _scheduled_wrappers = []

        def __init__(self, func):
            super().__init__()
            # Keep a reference to prevent garbage collection before execution
            self._scheduled_wrappers.append(self)
            # Clean up old wrappers if list gets too long (prevent memory leak)
            if len(self._scheduled_wrappers) > 100:
                self._scheduled_wrappers[:] = self._scheduled_wrappers[-50:]

            self.func = func
            # Move to main thread
            if QCoreApplication.instance():
                self.moveToThread(QCoreApplication.instance().thread())
        
        @pyqtSlot()
        def execute(self):
            try:
                self.func()
            finally:
                # Remove from references list after execution
                try:
                    self._scheduled_wrappers.remove(self)
                except (ValueError, AttributeError):
                    pass
    
    wrapper = CallableWrapper(func)

    QMetaObject.invokeMethod(
        wrapper,
        "execute",
        Qt.ConnectionType.QueuedConnection
    )


class EffectTranslator:
    """
    Effects translator is used to display "human-readable text" in the UI for effects that are active.  In the
    dictionary, the "key" is the actual effect instance name used during creation.  The "value" is a list containing
    the desired description that will be displayed in the UI, and the applicable setting variable.  The setting
    variable name is used to change the color of the slider handle in the settings page when that particular effect
    is active
    """

    effect_dict = {
        "ab_rumble_.*": ["Afterburner Rumble", "afterburner_effect_intensity"],
        'adv_spr': ["Advanced Spring Override", ""],
        'rudder_const_force': ["Rudder Constant Force", ""],
        "aoa": ["AoA Effect", "aoa_effect_gain"],
        "ap_spring": ["Autopilot Spring", ""],
        "adv_gforce_constant": ["G-Force Loading (Advanced)", ""],
        "blade_slap.*": ["Blade Slap", "blade_slap_intensity"],
        "buffeting": ["AoA/Stall Buffeting", "buffeting_intensity"],
        "bombs": ["Bomb Release", "weapon_release_intensity"],
        "canopyclunk": ["Canopy Clunk", "canopy_motion_intensity"],
        "canopymovement": ["Canopy Motion", "canopy_motion_intensity"],
        "clunk": ["Tail Hook Clunk", "tailhook_motion_intensity"],
        "collective_ap_spring": ["Collective Spring", "collective_ap_spring_gain"],
        "collective_damper": ["Collective Dampening Force", "collective_dampening_gain"],
        "collective_ft": ["Collective Force Trim", "collective_ft_ovd_spring_gain"],
        "cp_ovd_spring": ["Co-Pilot/RIO Override Spring", "cp_spr_override_spring_gain"],
        "crit_aoa": ["AoA Reduction Force", "aoa_reduction_max_force"],
        "cm": ["Countermeasure Deployment", "cm_vibration_intensity"],
        "cyclic_spring": ["Cyclic Spring Force", "cyclic_spring_gain"],
        "damage": ["Aircraft Damage Event", "damage_effect_intensity"],
        "damper": ["Damper Override", "damper_force"],
        "dcs_spr_override": ["Spring Override", ""],
        "il2_spr_override": ["Spring Override", ""],
        "decel": ["Decelleration Force", "deceleration_max_force"],
        "dynamic_spring": ["Dynamic Spring Force", ".*_spring_gain"],
        "elev_droop": ["Elevator Droop", "elevator_droop_moment"],
        "etl.*": ["ETL Shaking", "etl_effect_intensity"],
        "fbw_spring": ["Fly-by-wire Spring Force", "fbw_.*_gain"],
        "flapsmovement": ["Flap Motion", "flaps_motion_intensity"],
        "FI_vibration": ["FI Vibration", "FI_vibration_intensity"],
        "friction": ["Friction Override", "friction_force"],
        "boommovement" : ["Fuel Boom/Door","fuelboom_motion_intensity"],
        "gearbuffet.*": ["Gear Drag Buffeting", "gear_buffet_intensity"],
        "gearclunk": ["Gear Clunk", "gear_motion_intensity"],
        "gearmovement.*": ["Gear Motion", "gear_motion_intensity"],
        "gforce": ["G-Force Loading", "gforce_effect_max_intensity"],
        "new_gforce": ["G-Force Loading V2", "new_gforce_effect_max_intensity"],
        "gunfire": ["Gunfire Rumble", "gun_vibration_intensity"],
        "hit": ["Aircraft Hit Event", ""],
        "je_rumble_.*": ["Jet Engine Rumble", "jet_engine_rumble_intensity"],
        "il2_buffet.*": ["Buffeting", "il2_buffeting_factor"],
        "il2_gunfire.*": ["Gunfire Rumble", "il2_weapon_release_intensity"],
        "il2_bombs": ["Bomb Release", "il2_bomb_release_intensity"],
        "il2_rockets": ["Rocket Fire", "il2_rocket_release_intensity"],
        "il2_ffb_spring": ["FFB Telemetry Spring Override", ""],
        "ffb_tap_spring": ["Game Spring (DirectInput Tap)", ""],
        # the game's non-spring effects rendered from the tap mirror: slot-
        # keyed names 'tap_game_{slot}_{type}', matched by effect type code
        # ($-anchored so _1 does not also swallow _10/_11)
        r"tap_game_\d+_1$": ["Game Constant Force (DirectInput Tap)", "tap_effect_constant_gain"],
        r"tap_game_\d+_[34567]$": ["Game Periodic Vibration (DirectInput Tap)", "tap_effect_periodic_gain"],
        r"tap_game_\d+_9$": ["Game Damper (DirectInput Tap)", "tap_effect_damper_gain"],
        r"tap_game_\d+_10$": ["Game Inertia (DirectInput Tap)", "tap_effect_inertia_gain"],
        r"tap_game_\d+_11$": ["Game Friction (DirectInput Tap)", "tap_effect_friction_gain"],
        "il2_ffb_const": ["FFB Telemetry Constant Force", ""],
        "il2_ffb_damper": ["FFB Telemetry Damper", ""],
        "il2_eng_shk1": ["IL2 Prop Eng Shake (Telemetry)", ""],
        "il2_eng_shk2": ["IL2 Prop Eng Shake (Telemetry)", ""],
        "il2_eng_shk3": ["IL2 Prop Eng Shake (Telemetry)", ""],
        "il2_eng_shk4": ["IL2 Prop Eng Shake (Telemetry)", ""],
        "il2_jet_shk1": ["IL2 Jet Eng Shake (Telemetry)", ""],
        "il2_jet_shk2": ["IL2 Jet Eng Shake (Telemetry)", ""],
        "inertia": ["Inertia Override", "inertia_force"],
        "nw_shimmy": ["Nosewheel Shimmy", "nosewheel_shimmy_intensity"],
        "overspeed.*": ["Overspeed Shake", "overspeed_shake_intensity"],
        "payload_rel": ["Payload Release", "weapon_release_intensity"],
        "pause_spring": ["Pause/Slew Spring Force", ""],
        "pedal_spring": ["Pedal Spring", "pedal_spring_gain"],
        "pedal_ap_spring": ["Pedal AP Spring", "hpg_pedal_spring_gain"],
        "pedal_damper": ["Pedal Damper", "pedal_dampening_gain"],
        "prop_rpm.*": ["Propeller Engine Rumble", "engine_rumble_.*"],
        "rockets": ["Rocket Fire", "il2_weapon_release_intensity"],
        "rotor_rpm.*": ["Rotor RPM/Engine Rumble", "heli_engine_rumble_intensity"],
        "runway.*": ["Runway Rumble", "runway_rumble_intensity"],
        "speedbrakebuffet.*": ["Speedbrake Buffeting", "speedbrake_buffet_intensity"],
        "speedbrakemovement": ["Speedbrake Motion", "speedbrake_motion_intensity"],
        "spoilerbuffet.*": ["Spoiler Buffeting", "spoiler_buffet_intensity"],
        "spoilermovement": ["Spoiler Motion", "spoiler_motion_intensity"],
        "steering_friction": ["Steering Friction", "steering_friction_intensity"],
        "stick_shaker.*" : ["Stick Shaker","stick_shaker_intensity"],
        "hookmovement" : ["Tail Hook","tailhook_motion_intensity"],
        "touchdown": ["Touch-down Effect", "touchdown_effect_max_force"],
        "trim_cal_spring": ["Trim Calibration Spring", ""],
        "trim_spring": ["Trim Override Spring", ""],
        "trimwheel_ap_spring": ["Trimwheel AP Spring", "trimwheel_ap_spring_gain"],
        "turbulence": ["Turbulence", "turbulence_intensity"],
        "control_weight": ["Control Weight", ""],
        "vrs_buffet.*": ["Vortex Ring State Buffeting", "vrs_effect_intensity"],
        "wnd": ["Wind Effect", "wind_effect_max_intensity"],
        "wingfoldmovement.*": ["Wing Fold", "wingfold_motion_intensity"],
        "hyd_loss_damper": ["Low Hydraulic Damper", "hydraulic_loss_damper"],
        "hyd_loss_inertia": ["Low Hydraulic Inertia", "hydraulic_loss_inertia"],
        "hyd_loss_friction": ["Low Hydraulic Friction", "hydraulic_loss_friction"],
        "lock_1": ["Controls Lock Lower Bound", ""],
        "lock_2": ["Controls Lock Upper Bound", ""],
    }
    @classmethod
    def get_translation(cls, key):
        e = cls.effect_dict.get(key, None)
        if e is not None:
            return e
        else:
            for k in cls.effect_dict.keys():
                if re.match(k, key):
                    return cls.effect_dict.get(k, [f"No Lookup: {k}", ''])

        return [f"No Lookup: {key}", '']


class Destroyable:
    def destroy(self):
        raise NotImplementedError


# def set_reg(name, value):
#     REG_PATH = r"SOFTWARE\VPForce\TelemFFB"
#     try:
#         winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
#         registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_WRITE)

#         if isinstance(value, bool):
#             # Convert boolean to integer (1 for True, 0 for False)
#             value = int(value)
#         # Check if the value is an integer
#         if isinstance(value, int):
#             # For integers, use REG_DWORD
#             reg_type = winreg.REG_DWORD
#         elif isinstance(value, bytes):
#             # For binary data, use REG_BINARY
#             reg_type = winreg.REG_BINARY
#         else:
#             # For strings, use REG_SZ
#             reg_type = winreg.REG_SZ

#         winreg.SetValueEx(registry_key, name, 0, reg_type, value)
#         winreg.CloseKey(registry_key)
#         return True
#     except WindowsError:
#         return False


# def get_reg(name):
#     REG_PATH = r"SOFTWARE\VPForce\TelemFFB"
#     try:
#         registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)

#         # Query the value and its type
#         value, reg_type = winreg.QueryValueEx(registry_key, name)

#         # If the type is REG_DWORD, return the integer value
#         if reg_type == winreg.REG_DWORD:
#             return value
#         elif reg_type == winreg.REG_BINARY:
#             return value
#         else:
#             return str(value)  # Return as string for other types

#     except WindowsError:
#         return None

def sanitize_dict(d):
    out = {}
    for k, v in d.items():
        out[k] = to_number(v)
    return out


def _flatten_dict_gen(d, parent_key, sep):
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, dict):
            yield from flatten_dict(v, new_key, sep=sep).items()
        else:
            yield new_key, v


def flatten_dict(d, parent_key: str = '', sep: str = '_'):
    return dict(_flatten_dict_gen(d, parent_key, sep))


def insert_dict_item(original_dict, new_key, new_value, insert_key, before=True):
    updated_dict = {}
    found = False

    for key, value in original_dict.items():
        if key == insert_key and before:
            updated_dict[new_key] = new_value
            found = True
        updated_dict[key] = value
        if key == insert_key and not before:
            updated_dict[new_key] = new_value
            found = True

    if not found:
        # Key not found, append at the end (default behavior)
        updated_dict[new_key] = new_value

    return updated_dict


def get_random_within_range(item, input_number, range_start, range_end, decimal_places=2, time_period=None):
    """ Return a random number between range_start and range_end with a precision level of decimal_places
        if time_period (in seconds) is given, the function will return the same random number during any given
        interval of time_period for 'item' """
    current_time = int(time.time())  # Get the current timestamp in seconds
    random_seed = item

    # If time_period is not provided, generate a random number on every call
    if time_period is None:
        random.seed()
    else:
        time_period_index = current_time // time_period
        random_seed += str(time_period_index)
        random.seed(random_seed)

    # Generate a random number within the specified range with the specified number of decimal places
    factor = 10 ** decimal_places
    random_number = round(random.uniform(range_start, range_end), decimal_places)
    random_number = round(random_number * factor) / factor

    return random_number


class PerformanceTracker:
    def __init__(self):
        self.trackers = {}

    def get_time_delta(self, name: str):
        now = time.perf_counter()
        if name not in self.trackers:
            self.trackers[name] = now
            return 0.0
        last_time = self.trackers[name]
        delta = now - last_time
        self.trackers[name] = now
        return delta

    def remove_tracker(self, name: str):
        if name in self.trackers:
            del self.trackers[name]

    def clear_trackers(self):
        self.trackers.clear()

class Dispenser:
    def __init__(self, cls) -> None:
        self.cls = cls
        self.dict = {}

    def get(self, name, *args, **kwargs):
        v = self.dict.get(name)
        if not v:
            v = self.cls(*args, **kwargs)
            v.name = name
            self.dict[name] = v
        return v

    def remove(self, name):
        self.dispose(name)

    def __contains__(self, name):
        return name in self.dict

    def __getitem__(self, name):
        return self.get(name)

    def __iter__(self):
        return self.dict.__iter__()

    def __delitem__(self, name):
        v = self.dict[name]
        if isinstance(v, Destroyable):
            v.destroy()
        del self.dict[name]

    def clear(self):
        for k,v in self.dict.items():
            if isinstance(v, Destroyable):
                v.destroy()
        self.dict.clear()

    def values(self):
        return self.dict.values()

    def dispose(self, *names):
        for name in names:
            if name in self.dict:
                v = self.dict[name]
                if isinstance(v, Destroyable):
                    v.destroy()
                del self.dict[name]

    def foreach(self, func):
        for i in self.values():
            func(i)


class Teleplot:
    def __init__(self):
        self.sock = None
        self.enabled = False

    def configure(self, address: str):
        try:
            address = address.split(":")
            address[1] = int(address[1])
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.connect(tuple(address))
        except Exception:
            pass

    def sendTelemetry(self, name, value, instance=None):
        if instance is not None:
            name = f"{instance}_{name}"
        try:
            if self.sock:
                now = time.time() * 1000

                if type(value) == list:
                    msg = "\n".join([f"{name}_{i}:{now}:{value[i]}" for i in range(len(value))])
                else:
                    msg = f"{name}:{now}:{value}"
                self.sock.send(msg.encode())
        except Exception:
            pass


teleplot = Teleplot()


def _il2_config_diff_table(section_name, existing: dict, proposed: dict) -> str:
    # existing is None when the section was missing entirely from startup.cfg
    keys = list(proposed.keys())
    if existing:
        keys += [k for k in existing.keys() if k not in keys]

    rows = ""
    for k in keys:
        old_v = existing.get(k, "<i>(missing)</i>") if existing else "<i>(missing)</i>"
        new_v = html.escape(str(proposed.get(k, "-")))
        old_v = old_v if old_v.startswith("<i>") else html.escape(str(old_v))
        changed = existing is None or k not in existing or existing.get(k) != proposed.get(k)
        style = "color:#e08a2b; font-weight:bold;" if changed else ""
        rows += (
            f"<tr><td style='padding:2px 8px;'>{html.escape(k)}</td>"
            f"<td style='padding:2px 8px;'>{old_v}</td>"
            f"<td style='padding:2px 8px;{style}'>&rarr;&nbsp;{new_v}</td></tr>"
        )

    return f"""
    <p style='margin-top:10px; margin-bottom:2px;'><b>'{section_name}'</b></p>
    <table cellspacing='0' style='font-family:Consolas,monospace; font-size:9.5pt;'>
        <tr><th align='left' style='padding:2px 8px;'>Key</th>
            <th align='left' style='padding:2px 8px;'>Existing</th>
            <th align='left' style='padding:2px 8px;'>Proposed</th></tr>
        {rows}
    </table>
    """


def il2_korea_game_root(root_path):
    """Resolve IL-2 Korea's game directory under the configured install root.

    The standalone release nests the game one level down
    (``<root>/game/data/startup.cfg``); the Steam release ("IL2Series")
    drops that level (``<root>/data/startup.cfg``). Returns whichever
    layout actually contains ``data/startup.cfg``; a user pointing at the
    standalone ``game`` folder itself also resolves. Falls back to the
    historical standalone layout so error messages keep naming the
    expected default location.
    """
    root_path = root_path or ''
    for sub in ('game', ''):
        candidate = os.path.join(root_path, sub) if sub else root_path
        if os.path.isfile(os.path.join(candidate, 'data', 'startup.cfg')):
            return candidate
    return os.path.join(root_path, 'game')


def resolve_il2_ffb_device_ordinal(il2_korea_path, vendor_id, product_id):
    """
    Look up this device's DirectInput-style attach ordinal from IL-2 Korea's
    'known.devices.json', for matching the 'devNo' field in FFB telemetry records.

    known.devices.json entries carry an 'ident' field formatted as '<vid>_<pid>' (lowercase
    hex, no separators) and a 'lastAttachedId' which reflects the device's enumeration order -
    this is distinct from 'deviceId', which is the user-facing control-mapping slot in IL-2.

    Returns None if the file is missing, malformed, or no entry matches the given VID/PID.
    """
    known_devices_path = os.path.join(
        il2_korea_game_root(il2_korea_path), 'data', 'Input', 'known.devices.json')
    if not os.path.exists(known_devices_path):
        logging.warning(f"IL2 Korea known.devices.json not found at: {known_devices_path}")
        return None

    try:
        with open(known_devices_path, 'r', encoding='utf-8') as f:
            known_devices = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logging.warning(f"Unable to read IL2 Korea known.devices.json: {e}")
        return None

    target_ident = f"{vendor_id:04x}_{product_id:04x}"
    for guid, entry in known_devices.get('knownDevices', {}).items():
        if entry.get('ident') == target_ident:
            return entry.get('lastAttachedId')

    logging.warning(f"No matching entry for device {target_ident} in IL2 Korea known.devices.json")
    return None


def analyze_il2_config(file_path, port=34385, window=None, sim_name="IL-2", korea=False):
    config_data = defaultdict(dict)

    # file_path = os.path.join(path, "data\\startup.cfg")
    # file_path_k = os.path.join(path, "game\\data\\startup.cfg")
    if not os.path.exists(file_path):
        QMessageBox.warning(window, "TelemFFB IL-2 Config Check",
                            f"Unable to find Il-2 configuration file at: <{path}>\n\nPlease verify the installed path and update the IL2 system settings")
        return
    current_section = None
    ref_addr = '127.255.255.255'
    ref_addr1 = f'127.255.255.255:{port}'
    ref_decimation = '1'
    ref_enable = 'true'
    ref_port = f'{port}'
    telem_proposed = {}
    motion_proposed = {}
    ffb_proposed = {}
    telemetry_reference = {
        'addr': '127.255.255.255',
        'decimation': '1',
        'enable': 'true',
        'port': f'{port}'
    }
    motion_reference = {
        'addr': '127.255.255.255',
        'decimation': '1',
        'enable': 'true',
        'port': f'{port}'
    }
    ffb_reference = {
        'addr': '127.255.255.255',
        'decimation': '1',
        'enable': 'true',
        'port': f'{port}'
    }
    telem_config = None
    motion_config = None
    ffb_config = None
    with open(file_path, 'r', encoding="utf-8") as config_file:
        lines = config_file.readlines()

    for line in lines:
        if '[KEY =' in line:
            match = re.search(r'\[KEY = (\w+)\]', line)
            if match:
                current_section = match.group(1)
                continue
        elif '[END]' in line:
            current_section = None
            continue
        elif current_section and '=' in line:
            key, value = map(str.strip, line.split('=', 1))
            config_data[current_section][key] = value
    telem_match = 0
    telem_exists = 0
    if "telemetrydevice" not in config_data:
        # no telemetry config exists in current config, so add our own canned config
        telem_proposed = telemetry_reference
    else:
        # there is an existing telemetry config
        telem_match = 1
        telem_exists = 1
        ignore_port = False
        telem_config = config_data["telemetrydevice"]
        telem_proposed = {}
        for k, v in telem_config.items():  # strip out any quotes
            telem_proposed[k] = v.strip("\'\"")
            telem_config[k] = v.strip("\'\"")

        for k, v in telem_proposed.items():  # see if it matches our reference
            ref_v = telemetry_reference.get(k, 'null')
            if v != ref_v:
                if k == 'addr':
                    # the address is different, check if the addr1 attribute is present and matches
                    cur_addr1 = telem_proposed.get("addr1", "null")
                    if cur_addr1 != ref_addr1:
                        if "addr1" in telem_proposed:
                            telem_proposed["addr1"] = ref_addr1
                        else:
                            # insert our addr1 value after the existing addr value
                            telem_proposed = insert_dict_item(telem_proposed, 'addr1', ref_addr1, 'addr', before=False)
                            # since we are adding ourselves as a secondary receiver, we can ignore the existing port value
                        telem_match = 0
                    ignore_port = True
                if k == 'port' and not ignore_port:
                    if telem_proposed[k] != ref_port:
                        telem_proposed["port"] = ref_port
                        telem_match = 0
                if k == 'decimation':
                    if telem_proposed[k] != ref_decimation:
                        # we must set decimation to 1 for proper effect behavior
                        telem_proposed = insert_dict_item(telem_proposed, 'decimation', f'1', 'enable', before=True)
                        telem_match = 0
                if k == 'enable':
                    if telem_proposed[k] != ref_enable:
                        # enable must be true
                        telem_proposed = insert_dict_item(telem_proposed, 'enable', f'true', 'port', before=True)
                        telem_match = 0
    motion_match = 0
    motion_exists = 0
    if "motiondevice" not in config_data:
        # no telemetry config exists in current config, so add our own canned config
        motion_proposed = motion_reference
    else:
        # there is an existing telemetry config
        motion_match = 1
        motion_exists = 1
        ignore_port = False
        motion_config = config_data["motiondevice"]
        motion_proposed = {}
        for k, v in motion_config.items():  # strip out any quotes
            motion_proposed[k] = v.strip("\'\"")
            motion_config[k] = v.strip("\'\"")

        for k, v in motion_proposed.items():  # see if it matches our reference
            ref_v = motion_reference.get(k, 'null')
            if v != ref_v:
                if k == 'addr':
                    # the address is different, check if the addr1 attribute is present and matches
                    cur_addr1 = motion_proposed.get("addr1", "null")
                    if cur_addr1 != ref_addr1:
                        if "addr1" in motion_proposed:
                            motion_proposed["addr1"] = ref_addr1
                        else:
                            # insert our addr1 value after the existing addr value
                            motion_proposed = insert_dict_item(motion_proposed, 'addr1', ref_addr1, 'addr',
                                                               before=False)
                            # since we are adding ourselves as a secondary receiver, we can ignore the existing port value
                        motion_match = 0
                    ignore_port = True
                if k == 'port' and not ignore_port:
                    if motion_proposed[k] != ref_port:
                        motion_proposed["port"] = ref_port
                        motion_match = 0
                if k == 'decimation':
                    if motion_proposed[k] != ref_decimation:
                        motion_proposed = insert_dict_item(motion_proposed, 'decimation', f'1', 'enable', before=True)
                        motion_match = 0
                if k == 'enable':
                    # enable must be true
                    if motion_proposed[k] != ref_enable:
                        motion_proposed = insert_dict_item(motion_proposed, 'enable', f'true', 'port', before=True)
                        motion_match = 0

    ffb_match = 1
    ffb_exists = 0
    if korea:
        ffb_exists = 0
        if "ffbdevice" not in config_data:
            ffb_proposed = ffb_reference
            ffb_match = 0
        else:
            ffb_match = 1
            ffb_exists = 1
            ignore_port = False
            ffb_config = config_data["ffbdevice"]
            ffb_proposed = {}
            for k, v in ffb_config.items():
                ffb_proposed[k] = v.strip("\'\"")
                ffb_config[k] = v.strip("\'\"")

            for k, v in ffb_proposed.items():
                ref_v = ffb_reference.get(k, 'null')
                if v != ref_v:
                    if k == 'addr':
                        cur_addr1 = ffb_proposed.get("addr1", "null")
                        if cur_addr1 != ref_addr1:
                            if "addr1" in ffb_proposed:
                                ffb_proposed["addr1"] = ref_addr1
                            else:
                                ffb_proposed = insert_dict_item(ffb_proposed, 'addr1', ref_addr1, 'addr', before=False)
                            ffb_match = 0
                        ignore_port = True
                    if k == 'port' and not ignore_port:
                        if ffb_proposed[k] != ref_port:
                            ffb_proposed["port"] = ref_port
                            ffb_match = 0
                    if k == 'decimation':
                        if ffb_proposed[k] != ref_decimation:
                            ffb_proposed = insert_dict_item(ffb_proposed, 'decimation', '1', 'enable', before=True)
                            ffb_match = 0
                    if k == 'enable':
                        if ffb_proposed[k] != ref_enable:
                            ffb_proposed = insert_dict_item(ffb_proposed, 'enable', 'true', 'port', before=True)
                            ffb_match = 0

    if telem_match and motion_match and ffb_match:
        return
    else:
        telem_message = QMessageBox(parent=window)
        telem_message.setIcon(QMessageBox.Icon.Question)
        telem_message.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        telem_message.setWindowTitle(f"TelemFFB {sim_name} Config")

        if not telem_match or not motion_match or not ffb_match:
            pop = f"""
            <p>The telemetry, motion and/or FFB device configuration in the <b>{html.escape(sim_name)}</b> <b>startup.cfg</b>
            is missing or incorrect and may prohibit TelemFFB from receiving data.</p>
            <p style='font-family:Consolas,monospace; font-size:9pt;'>File = {html.escape(file_path)}</p>
            <p>Would you like to automatically adjust the configuration per the following?</p>
            """

            if not telem_match or not telem_exists:
                pop += _il2_config_diff_table('telemetrydevice', telem_config, telem_proposed)

            if not motion_match or not motion_exists:
                pop += _il2_config_diff_table('motiondevice', motion_config, motion_proposed)

            if korea and (not ffb_match or not ffb_exists):
                pop += _il2_config_diff_table('ffbdevice', ffb_config, ffb_proposed)

            pop += "<p style='color:#d9534f; font-weight:bold; margin-top:12px;'>Please ensure IL-2 is not running before selecting 'Yes'</p>"
        telem_message.setTextFormat(Qt.TextFormat.RichText)
        telem_message.setText(pop)
        ans = telem_message.exec()
        if ans == QMessageBox.StandardButton.Yes:
            config_data['telemetrydevice'] = telem_proposed
            config_data['motiondevice'] = motion_proposed
            if korea:
                config_data['ffbdevice'] = ffb_proposed
            try:
                write_il2_config(file_path, config_data)
            except Exception as e:
                QMessageBox.warning(window, "Config Update Error",
                                    f"There was an error writing to the Il-2 Config file:\n{e}")
        elif ans == QMessageBox.StandardButton.No:
            print("Answer: NO")

        # return config_data, telem_match, motion_match

def write_il2_config(file_path, config_data):
    with open(file_path, 'w', encoding="utf-8") as config_file:
        for section, options in config_data.items():
            config_file.write(f"[KEY = {section}]\n")
            for key, value in options.items():
                if key == 'addr' or key == 'addr1':
                    value = value.strip("\'\"")
                    config_file.write(f"\t{key} = \"{value}\"\n")
                else:
                    config_file.write(f"\t{key} = {value}\n")
            config_file.write("[END]\n\n")


def install_xplane_plugin(path, window):
    src_path = get_resource_path('xplane-plugin/TelemFFB-XPP/64/win.xpl', prefer_root=True)
    dst_path = os.path.join(path, 'resources', 'plugins', 'TelemFFB-XPP', '64', 'win.xpl')

    ans = QMessageBox.StandardButton.No
    if not os.path.exists(dst_path):
        ans = QMessageBox.question(window, "X-Plane Plugin Installer", "X-plane plugin is not installed, install now?\n\nNote: X-Plane must not be running for this operation to succeed")
    else:
        src_crc = calculate_crc(src_path)
        dst_crc = calculate_crc(dst_path)
        if src_crc != dst_crc:
            ans = QMessageBox.question(window, "X-Plane Plugin Installer", "X-plane plugin is out of date, update now?\n\nNote: X-Plane must not be running for this operation to succeed")
        else:
            return True

    if ans == QMessageBox.StandardButton.Yes:
        tryloop = True
        while tryloop:
            try:
                if not os.path.exists(os.path.dirname(dst_path)):
                    os.makedirs(os.path.dirname(dst_path))
                print(os.path.isdir(os.path.dirname(dst_path)))
                shutil.copy(src_path, dst_path)
                tryloop = False
                return True
            except Exception as e:
                print(f"ERROR:{e}")
                retry = QMessageBox.warning(window, "X-Plane Plugin Error", "There was an error copying the file.  Please ensure X-Plane is not running.\n\nWould you like to re-try?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if retry == QMessageBox.StandardButton.No:
                    tryloop = False
                    return False
    else:
        return False
    return True

def get_dcs_variant():
    """
    Resolve the active DCS variant from the registry and dcs_variant.txt.

    Tries these registry keys under HKCU (in order):
      - Software\\Eagle Dynamics\\DCS World OpenBeta
      - Software\\Eagle Dynamics\\DCS World

    Logic:
      1) Find the first key that exists; read its 'Path'.
      2) If <Path>\\dcs_variant.txt exists and has content, return that (e.g., "openbeta").
      3) Otherwise, infer from the registry key name ("openbeta" for OpenBeta; None for stable).

    Returns:
        str | None
    """
    try:
        import winreg
    except ImportError:
        return None  # no registry off Windows

    logging.info("DCS Variant Check: Starting variant discovery via registry and dcs_variant.txt")

    # Try OpenBeta first, then Stable.
    candidate_keys = [
        r"Software\Eagle Dynamics\DCS World OpenBeta",
        r"Software\Eagle Dynamics\DCS World",
    ]

    for subkey in candidate_keys:
        try:
            logging.debug(f"DCS Variant Check: Trying HKCU\\{subkey}")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as reg_key:
                install_path, _ = winreg.QueryValueEx(reg_key, "Path")
            logging.info(f"DCS Variant Check: Install path found in registry HKCU\\{subkey}: {install_path!r}")

            variant_file = os.path.join(install_path, "dcs_variant.txt")
            logging.debug(f"DCS Variant Check: Checking for variant file at: {variant_file}")

            if os.path.exists(variant_file):
                try:
                    with open(variant_file, "r", encoding="utf-8") as f:
                        variant = f.read().strip()
                    if variant:
                        logging.info(f"DCS Variant Check: Variant detected from file: '{variant}'")
                        return variant
                    else:
                        logging.info("DCS Variant Check: Variant file present but empty; will infer from registry key name")
                except Exception as ex:
                    logging.warning(f"DCS Variant Check: Failed to read variant file {variant_file}: {ex}; will infer from registry key name")
            else:
                logging.info(f"DCS Variant Check: Variant file not found at {variant_file}; will infer from registry key name")

            # Fallback: infer from the registry key name
            if "OpenBeta" in subkey:
                logging.info("DCS Variant Check: Inferring variant as 'openbeta' from registry key name")
                return "openbeta"
            else:
                logging.info("DCS Variant Check: No explicit variant in registry key name; treating as stable (no variant)")
                return None

        except FileNotFoundError:
            logging.debug(f"DCS Variant Check: Registry key not found: HKCU\\{subkey}")
            continue
        except OSError as ex:
            logging.debug(f"DCS Variant Check: Could not open HKCU\\{subkey}: {ex}")
            continue
        except Exception as ex:
            logging.error(f"DCS Variant Check: Unexpected error while reading HKCU\\{subkey}: {ex}")
            continue

    logging.info("DCS Variant Check: No known DCS registry keys found under HKCU")
    return None


def _check_dcrealistic_autostart(export_data, export_lua_path, window):
    """Warn if DCRealistic or Simhaptic (rkApps) autostart is active in Export.lua (known to break FFB spring effects in DCS)."""
    for line in export_data.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        if "DCREALISTIC_AUTOSTART" in stripped or "SIMHAPTIC_AUTOSTART" in stripped:
            logging.error(
                f"The DCRealistic or SimHaptic autostart feature is enabled in:\n{export_lua_path}\n\n"
                "This is known to cause FFB spring effects to fail on aircraft load in DCS.\n\n"
                "If you experience issues with the spring effect not starting after loading into "
                "an aircraft in DCS, disable the DCRealistic autostart option in the DCRealistic "
                "settings.\n\n"
                "This is a warning and does not affect the operation of TelemFFB."
            )

def _prepare_dcs_export_context():
    import telemffb.winpaths as winpaths

    """Resolve shared paths and targets for DCS export integration."""
    saved_games = winpaths.get_path(winpaths.FOLDERID.SavedGames)
    logging.info(f"DCS Export Installer: Saved Games directory detected: {saved_games}")

    dcs_variant = get_dcs_variant()
    if dcs_variant:
        logging.info(f"DCS Export Installer: Active DCS variant resolved as: {dcs_variant!r}")
    else:
        logging.info("DCS Export Installer: No DCS variant detected; will check base 'DCS' and 'DCS.openbeta' only")

    dirlist = ['DCS', 'DCS.openbeta']
    if dcs_variant:
        if f'DCS.{dcs_variant}' not in dirlist:
            dirlist.append(f'DCS.{dcs_variant}')

    logging.debug(f"DCS Export Installer: Candidate DCS folders under Saved Games: {dirlist}")

    local_telemffb = get_resource_path('export/TelemFFB.lua', prefer_root=True)
    source_dll_path = os.path.join(os.path.dirname(local_telemffb), "TelemFFB.dll")
    logging.debug(f"DCS Export Installer: Local Lua path: {local_telemffb}")
    logging.debug(f"DCS Export Installer: Local DLL path: {source_dll_path}")

    return saved_games, dirlist, local_telemffb, source_dll_path


def install_dcs_export_module_lua(window):
    """Install/update the Lua-based TelemFFB export integration for DCS."""
    logging.info("DCS Export Installer: Starting DCS Export integration in Lua mode")

    saved_games, dirlist, local_telemffb, source_dll_path = _prepare_dcs_export_context()

    lua_line = "local telemffblfs=require('lfs');dofile(telemffblfs.writedir()..'Scripts/TelemFFB.lua')"
    dll_script = (
        'package.cpath = package.cpath .. ";"..require(\'lfs\').writedir().."\\\\Scripts\\\\?.dll"\n'
        'require("telemffb")'
    )

    any_changes = False

    for dirname in dirlist:
        p = os.path.join(saved_games, dirname)
        if not os.path.exists(p):
            logging.info(f"DCS Export Installer: '{p}' does not exist; skipping this DCS folder")
            continue

        logging.info(f"DCS Export Installer: Processing DCS folder: {p}")

        scripts_dir = os.path.join(p, 'Scripts')
        if not os.path.exists(scripts_dir):
            os.makedirs(scripts_dir, exist_ok=True)
            logging.info(f"DCS Export Installer: Created Scripts directory: {scripts_dir}")
        else:
            logging.debug(f"DCS Export Installer: Scripts directory already exists: {scripts_dir}")

        export_lua_path = os.path.join(scripts_dir, "Export.lua")
        lua_script_path = os.path.join(scripts_dir, "TelemFFB.lua")
        target_dll_path = os.path.join(scripts_dir, "TelemFFB.dll")

        try:
            with open(export_lua_path, "r", encoding="utf-8") as f:
                export_data = f.read()
            logging.info(f"DCS Export Installer: Found existing Export.lua at {export_lua_path}")
        except FileNotFoundError:
            export_data = ""
            logging.info(f"DCS Export Installer: No Export.lua found at {export_lua_path}; will create if needed")

        _check_dcrealistic_autostart(export_data, export_lua_path, window)

        updated = False

        logging.debug("DCS Export Installer: Ensuring DLL-style integration is removed (if present)")
        if 'require("telemffb")' in export_data or "package.cpath" in export_data:
            before_lines = export_data.splitlines()
            after_lines = [
                line for line in before_lines
                if 'require("telemffb")' not in line and "package.cpath" not in line
            ]
            removed = len(before_lines) - len(after_lines)
            export_data = "\n".join(after_lines) + ("\n" if after_lines else "")
            updated = True
            logging.info(f"DCS Export Installer: Removed {removed} DLL-style line(s) from Export.lua")
        else:
            logging.debug("DCS Export Installer: DLL integration lines not present in Export.lua")

        if os.path.exists(target_dll_path):
            try:
                os.remove(target_dll_path)
                updated = True
                logging.info(f"DCS Export Installer: Removed DLL file: {target_dll_path}")
            except Exception as e:
                QMessageBox.critical(
                    window, "DLL Removal Error",
                    f"Failed to delete existing DLL:\n{e}\n\nPlease ensure DCS is not running."
                )
                logging.error(f"DCS Export Installer: Error removing DLL: {e}")
                return
        else:
            logging.debug("DCS Export Installer: DLL file not present; nothing to remove")

        if lua_line not in export_data:
            logging.info("DCS Export Installer: Lua integration not present in Export.lua")
            reply = QMessageBox.question(
                window, "Confirm",
                f"Install TelemFFB export entries into {export_lua_path}?"
            )
            logging.info(f"DCS Export Installer: User response for adding Lua line: {reply.name}")
            if reply == QMessageBox.StandardButton.Yes:
                if export_data and not export_data.endswith("\n"):
                    export_data += "\n"
                export_data += lua_line + "\n"
                updated = True
                logging.info("DCS Export Installer: Added Lua-style export line to Export.lua")
                try:
                    with open(local_telemffb, "rb") as src, open(lua_script_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    logging.info(f"DCS Export Installer: Wrote Lua script to {lua_script_path}")
                except Exception as e:
                    logging.error(f"DCS Export Installer: Failed writing Lua script {lua_script_path}: {e}")
                    return
        else:
            logging.debug("DCS Export Installer: Lua integration already present in Export.lua")
            if os.path.exists(lua_script_path):
                try:
                    crc_existing = calculate_checksum(lua_script_path)
                    crc_local = calculate_checksum(local_telemffb)
                    logging.debug(f"DCS Export Installer: Lua CRC existing={crc_existing} local={crc_local}")
                    if crc_existing != crc_local:
                        logging.info("DCS Export Installer: Lua script differs from local copy; update recommended")
                        reply = QMessageBox.question(
                            window, "Lua script update",
                            f"The DCS Export script 'TelemFFB.lua' has changed. Update {lua_script_path}?"
                        )
                        logging.info(f"DCS Export Installer: User response for Lua script update: {reply.name}")
                        if reply == QMessageBox.StandardButton.Yes:
                            try:
                                with open(local_telemffb, "rb") as src, open(lua_script_path, "wb") as dst:
                                    shutil.copyfileobj(src, dst)
                                updated = True
                                logging.info(f"DCS Export Installer: Updated Lua script at {lua_script_path}")
                            except Exception as e:
                                logging.error(f"DCS Export Installer: Error updating Lua script: {e}")
                                return
                    else:
                        logging.info("DCS Export Installer: Lua script already up-to-date")
                except Exception as e:
                    logging.error(f"DCS Export Installer: Error during Lua script CRC comparison: {e}")
            else:
                logging.info("DCS Export Installer: Lua integration present but script file missing; writing fresh copy")
                try:
                    with open(local_telemffb, "rb") as src, open(lua_script_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    updated = True
                    logging.info(f"DCS Export Installer: Wrote missing Lua script to {lua_script_path}")
                except Exception as e:
                    logging.error(f"DCS Export Installer: Failed writing Lua script {lua_script_path}: {e}")
                    return

        if updated:
            try:
                with open(export_lua_path, "w", encoding="utf-8") as f:
                    f.write(export_data)
                any_changes = True
                logging.info(f"DCS Export Installer: Export.lua written to {export_lua_path} ")
            except Exception as e:
                logging.error(f"DCS Export Installer: Failed writing Export.lua at {export_lua_path}: {e}")
                return
        else:
            logging.info(f"DCS Export Installer: No changes required for {export_lua_path}")

    if any_changes:
        logging.info("DCS Export Installer: Completed with changes applied (mode=Lua)")
    else:
        logging.info("DCS Export Installer: Completed; nothing to change (mode=Lua)")


def install_dcs_export_module_dll(window):
    """Install/update the DLL-based TelemFFB export integration for DCS."""
    logging.info("DCS Export Installer: Starting DCS Export integration in DLL mode")

    saved_games, dirlist, local_telemffb, source_dll_path = _prepare_dcs_export_context()

    lua_line = "local telemffblfs=require('lfs');dofile(telemffblfs.writedir()..'Scripts/TelemFFB.lua')"
    dll_lines = [
        'package.cpath = package.cpath .. ";"..require(\'lfs\').writedir().."\\\\Scripts\\\\?.dll"',
        'require("telemffb")'
    ]

    any_changes = False

    for dirname in dirlist:
        p = os.path.join(saved_games, dirname)
        if not os.path.exists(p):
            logging.info(f"DCS Export Installer: '{p}' does not exist; skipping this DCS folder")
            continue

        logging.info(f"DCS Export Installer: Processing DCS folder: {p}")

        scripts_dir = os.path.join(p, 'Scripts')
        if not os.path.exists(scripts_dir):
            os.makedirs(scripts_dir, exist_ok=True)
            logging.info(f"DCS Export Installer: Created Scripts directory: {scripts_dir}")
        else:
            logging.debug(f"DCS Export Installer: Scripts directory already exists: {scripts_dir}")

        export_lua_path = os.path.join(scripts_dir, "Export.lua")
        lua_script_path = os.path.join(scripts_dir, "TelemFFB.lua")
        target_dll_path = os.path.join(scripts_dir, "TelemFFB.dll")

        try:
            with open(export_lua_path, "r", encoding="utf-8") as f:
                export_data = f.read()
            logging.info(f"DCS Export Installer: Found existing Export.lua at {export_lua_path}")
        except FileNotFoundError:
            export_data = ""
            logging.info(f"DCS Export Installer: No Export.lua found at {export_lua_path}; will create if needed")

        _check_dcrealistic_autostart(export_data, export_lua_path, window)

        updated = False

        if lua_line in export_data:
            export_data = export_data.replace(lua_line + "\n", "").replace(lua_line, "")
            updated = True
            logging.info("DCS Export Installer: Removed Lua integration line from Export.lua ")

        if os.path.exists(lua_script_path):
            try:
                os.remove(lua_script_path)
                updated = True
                logging.info(f"DCS Export Installer: Removed Lua script file: {lua_script_path}")
            except Exception as e:
                logging.error(f"DCS Export Installer: Failed to remove Lua script {lua_script_path}: {e}")

        export_lines = export_data.splitlines()

        def _is_active_line(line: str, target: str) -> bool:
            stripped = line.strip()
            if stripped.startswith("--"):
                return False
            return stripped == target

        pkg_index = next((idx for idx, line in enumerate(export_lines) if _is_active_line(line, dll_lines[0])), None)
        req_index = next((idx for idx, line in enumerate(export_lines) if _is_active_line(line, dll_lines[1])), None)
        has_ordered_block = pkg_index is not None and req_index is not None and req_index == pkg_index + 1

        if has_ordered_block:
            logging.debug("DCS Export Installer: DLL integration already present in Export.lua")
        else:
            logging.info("DCS Export Installer: DLL integration missing or out of order in Export.lua")
            reply = QMessageBox.question(
                window, "Confirm",
                f"Install TelemFFB DLL export entries into {export_lua_path}?"
            )
            logging.info(f"DCS Export Installer: User response for adding DLL lines: {reply.name}")
            if reply == QMessageBox.StandardButton.Yes:
                cleaned_lines = []
                for line in export_lines:
                    stripped = line.strip()
                    stripped_no_comment = stripped[2:].lstrip() if stripped.startswith("--") else stripped
                    if stripped_no_comment in dll_lines:
                        continue
                    cleaned_lines.append(line)

                if cleaned_lines and cleaned_lines[-1].strip():
                    cleaned_lines.append("")

                cleaned_lines.extend(dll_lines)
                export_data = "\n".join(cleaned_lines) + "\n"
                updated = True
                logging.info("DCS Export Installer: Enforced DLL export block in Export.lua")
            else:
                export_data = "\n".join(export_lines) + ("\n" if export_data.endswith("\n") else "")

        if os.path.exists(source_dll_path):
            logging.debug("DCS Export Installer: Checking whether TelemFFB.dll update is needed")
            try:
                needs_copy = False
                if not os.path.exists(target_dll_path):
                    logging.info(f"DCS Export Installer: DLL not found at {target_dll_path}; will copy")
                    needs_copy = True
                else:
                    src_crc = calculate_checksum(source_dll_path)
                    dst_crc = calculate_checksum(target_dll_path)
                    logging.debug(f"DCS Export Installer: CRC source={src_crc} target={dst_crc}")
                    if src_crc != dst_crc:
                        logging.info("DCS Export Installer: DLL differs from local copy; update recommended")
                        needs_copy = True
                    else:
                        logging.info("DCS Export Installer: DLL already up-to-date")

                if needs_copy:
                    reply = QMessageBox.question(
                        window, "DLL update",
                        f"The TelemFFB DLL will be copied to {target_dll_path}. Proceed?"
                    )
                    logging.info(f"DCS Export Installer: User response for DLL copy: {reply.name}")
                    if reply == QMessageBox.StandardButton.Yes:
                        if os.path.exists(target_dll_path):
                            try:
                                os.remove(target_dll_path)
                                logging.info(f"DCS Export Installer: Removed existing DLL at {target_dll_path}")
                            except Exception as e:
                                QMessageBox.critical(
                                    window, "DLL Update Error",
                                    f"Failed to delete the existing DLL:\n{e}\n\nPlease ensure DCS is not running."
                                )
                                logging.error(f"DCS Export Installer: Error deleting DLL: {e}")
                                return
                        try:
                            shutil.copy2(source_dll_path, target_dll_path)
                            updated = True
                            logging.info(f"DCS Export Installer: Copied DLL to {target_dll_path}")
                        except Exception as e:
                            QMessageBox.critical(
                                window, "DLL Copy Error",
                                f"Failed to copy the new DLL:\n{e}\n\nPlease ensure DCS is not running."
                            )
                            logging.error(f"DCS Export Installer: Error copying DLL: {e}")
                            return
            except Exception as e:
                logging.error(f"DCS Export Installer: Unexpected error during DLL update: {e}")
        else:
            logging.warning(f"DCS Export Installer: TelemFFB.dll not found at source: {source_dll_path}")

        if updated:
            try:
                with open(export_lua_path, "w", encoding="utf-8") as f:
                    f.write(export_data)
                any_changes = True
                logging.info(f"DCS Export Installer: Export.lua written to {export_lua_path} ")
            except Exception as e:
                logging.error(f"DCS Export Installer: Failed writing Export.lua at {export_lua_path}: {e}")
                return
        else:
            logging.info(f"DCS Export Installer: No changes required for {export_lua_path}")

    if any_changes:
        logging.info("DCS Export Installer: Completed with changes applied (mode=DLL)")
    else:
        logging.info("DCS Export Installer: Completed; nothing to change (mode=DLL)")


def install_dcs_export_module(window, dll=False):
    """Backwards-compatible wrapper dispatching to DLL or Lua installers."""
    if dll:
        install_dcs_export_module_dll(window)
    else:
        install_dcs_export_module_lua(window)



def launch_vpconf(serial=None):
    settings = QSettings("VPforce", "RhinoFFB")
    vpconf_path = settings.value("path")

    if vpconf_path:
        logging.info(f"Found VPforce Configurator at {vpconf_path}")
        logging.info(f"Launching VPforce Configurator....")
        workdir = os.path.dirname(vpconf_path)
        env = {}
        env["PATH"] = os.environ["PATH"]
        # logging.info(f"Loading vpconf for aircraft with: {vpconf_path} -config {params['vpconf']} -serial {serial}")
        if serial is not None:
            # in case ability to pass serial to configurator command line is added
            call = [vpconf_path, "-serial", serial]
        else:
            call = vpconf_path
        subprocess.Popen(call, cwd=workdir, env=env, shell=True)


def get_version():
    if G.release_version:
        return G.release_version_str
    if G.dev_build:
        return G.dev_build_str
    if G.beta_build:
        return G.beta_build_str

    ver = "UNKNOWN"
    try:
        import version
        ver = version.VERSION
        return ver
    except Exception:
        pass

    try:
        ver = subprocess.check_output(['git', 'describe', '--always', '--abbrev=8', '--dirty'], shell=True).decode('ascii').strip()
        ver = f"local-{ver}"
    except Exception:
        pass
    return ver

def validate_vpconf_profile(file_path, pid=None, dev_type=None, silent=False, window=None):
    """Validate a VPforce Configurator profile file against current device.
    
    This function checks if a VPforce Configurator profile file is compatible
    with the current device by validating:
    1. File format and structure
    2. PID matching between profile and device
    3. Device identifier matching
    
    Args:
        file_path (str): Path to the VPforce Configurator profile file
        pid (int or str): Expected device PID (Product ID)
        dev_type (str): Device type (e.g., 'Joystick', 'Pedals')
        silent (bool): If True, log errors instead of showing message boxes
        window: Parent window for message boxes (can be None)
        
    Returns:
        bool: True if profile is valid for current device, False otherwise
    """

    def _load_vpconf_config(file_path):
        """Load and parse VPforce Configurator configuration file.
        
        Args:
            file_path (str): Path to the configuration file
            
        Returns:
            dict: Parsed configuration data
            
        Raises:
            FileNotFoundError: If file doesn't exist
            json.JSONDecodeError: If file is not valid JSON
            ValueError: If required fields are missing
        """
        try:
            with open(file_path, 'r') as f:
                config_data = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {file_path}")
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"Invalid JSON in configuration file: {file_path}", e.doc, e.pos)
        except Exception as e:
            raise ValueError(f"Unable to read configuration file: {file_path}. Error: {e}")
        
        return config_data


    def _extract_config_info(config_data):
        """Extract key information from VPforce configuration data.
        
        Args:
            config_data (dict): Configuration data from JSON file
            
        Returns:
            tuple: (pid, serial, device_name) extracted from config
            
        Raises:
            ValueError: If required fields are missing or invalid
        """
        # Extract USB PID
        cfg_pid = config_data.get('config', {}).get('usb_pid', None)
        if cfg_pid is None:
            raise ValueError("Missing or invalid 'usb_pid' in configuration file")
        
        
        # Extract serial number
        cfg_serial = config_data.get('serial_number', None)
        if cfg_serial is None:
            raise ValueError("Missing or invalid 'serial_number' in configuration file")
        
        # Extract device name
        cfg_device_name = config_data.get('config', {}).get('device_name', None)
        if cfg_device_name is None:
            raise ValueError("Missing or invalid 'device_name' in configuration file")
        
        return cfg_pid, cfg_serial, cfg_device_name


    def _get_current_device_ident(pid):
        """Get the device identifier for the device with the given USB PID.

        `G.device_info` only describes the device bound to *this* process/instance.
        When the master instance is validating a profile for a different device type
        (e.g. a child instance's pedals while the master owns the joystick), the
        target device's ident has to come from `G.instance_dev_dict`, which the
        master populates at startup from a system-wide enumeration of all connected
        Rhino devices (see `_enumerate_and_log_devices` in main.py) and is keyed by
        USB PID, not by which process opened the device.

        Args:
            pid (int): Device PID
            
        Returns:
            str or None: Device identifier, or None when no connected device
            has that PID.
        """
        dev_info = G.instance_dev_dict.get(pid)
        if dev_info is not None:
            return dev_info.ident
        if G.device_info and G.device_info.product_id == pid:
            return G.device_info.ident
        # Answering with this instance's own identifier would compare the
        # profile against the wrong device entirely.
        return None


    def _show_error_message(title, message, silent, window):
        """Display error message either as popup or log entry.
        
        Args:
            title (str): Title for the message box
            message (str): Error message to display
            silent (bool): If True, log error instead of showing popup
            window: Parent window for message box (can be None)
        """
        if silent:
            logging.error(message)
        else:
            QMessageBox.warning(window, title, message)

    # Normalize PID to integer
    if isinstance(pid, str):
        pid = int(pid, 16)
    
    # Step 1: Load and parse configuration file
    try:
        config_data = _load_vpconf_config(file_path)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        error_msg = f"The VPforce Configurator file appears to be invalid:\n\nFile: {file_path}\nError: {str(e)}"
        _show_error_message("Invalid Configuration File", error_msg, silent, window)
        return False
    
    # Step 2: Extract configuration information
    try:
        cfg_pid, cfg_serial, cfg_device_name = _extract_config_info(config_data)
    except ValueError as e:
        error_msg = f"The VPforce Configurator file is missing required information:\n\nFile: {file_path}\nError: {str(e)}"
        _show_error_message("Invalid Configuration File", error_msg, silent, window)
        return False
    
    # Step 3: Validate PID matching
    if cfg_pid != pid:
        target_device_ident = _get_current_device_ident(pid)
        error_msg = (
            f"The VPforce Configurator file does not match the target device:\n\n"
            f"File: {file_path}\n\n"
            f"Target device:\n"
            f"  Type: {dev_type}\n"
            f"  PID: {pid:04X}\n"
            f"  Name: {target_device_ident or 'not connected'}\n\n"
            f"Profile settings:\n"
            f"  PID: {cfg_pid:04X}\n"
            f"  Name: {cfg_device_name}\n"
            f"  Serial: {cfg_serial}"
        )
        _show_error_message("Device Mismatch", error_msg, silent, window)
        return False
    
    # Step 4: Validate device identifier matching
    current_device_ident = _get_current_device_ident(pid)
    if current_device_ident is None:
        # Configured but not connected, so there is no identifier to compare
        # against; the PID matched, which is as much as can be checked here.
        return True
    if cfg_device_name != current_device_ident:
        error_msg = (
            f"Device identifier mismatch detected:\n\n"
            f"File: {file_path}\n\n"
            f"Profile device identifier: {cfg_device_name}\n"
            f"Connected device identifier: {current_device_ident}\n\n"
            f"This mismatch may cause USB disconnection issues."
        )
        _show_error_message("Device Identifier Mismatch", error_msg, silent, window)
        return False
    
    # All validations passed
    return True


def upload_vpconf_profile(config_filepath, serial):
    from telemffb.namedmutex import NamedMutex

    # central gate: VPConfigurator profiles only apply to VPforce hardware
    # (covers aircraft-change, startup and exit pushes in one place)
    caps = G.device_capabilities
    if caps is not None and not caps.has_gains:
        logging.info("vpconf push skipped: the connected device has no Configurator gains")
        return
    if not serial:
        # no serial means no device was ever successfully opened (or the
        # caller carries stale identity); Configurator selects the target
        # device by serial, so pushing without one is at best a crash and
        # at worst the wrong device
        logging.warning("vpconf push skipped: no device serial available")
        return

    settings = QSettings("VPforce", "RhinoFFB")
    vpconf_path = settings.value("path")

    if vpconf_path:
        logging.info(f"Found VPforce Configurator at {vpconf_path}")
        workdir = os.path.dirname(vpconf_path)
        env = {}
        env["PATH"] = os.environ["PATH"]
        if not os.path.isfile(config_filepath):
            logging.error(f"Error loading VPforce Configurator Profile: ({config_filepath}) - The file does not exist! ")
            return
        
        assert G.device_info is not None, "Device info must be set before uploading profile"
        if not validate_vpconf_profile(config_filepath, G.device_info.product_id, G.device_type, silent=True):
            logging.error(f"VPForce Config Error: ({config_filepath}) - The file failed validation!  Check the PID is correct for the device")
            return

        logging.info(f"upload_vpconf_profile - Loading vpconf for with: {vpconf_path} -config {config_filepath} -serial {serial}")
        G.current_vpconf_profile = config_filepath
        # Scope-aware: only updates the indicator if this device is the
        # selected config scope (a master scoped to a child keeps showing the
        # child's reported state; ours shows when the user switches back).
        if G.main_window is not None:
            G.main_window.refresh_scope_status_indicators(force=True)

        def exec():
            # Use NamedMutex to ensure only one instance of the configurator is executed at a time
            # This might help prevent issues with libusb race conditions when configurator tries to enumerate devices
            try:
                with NamedMutex("vpconf_mutex", acquired=True):
                    G.vpconf_init_pending = True
                    ret = subprocess.call([vpconf_path, "-config", config_filepath, "-serial", serial], cwd=workdir, env=env, shell=True)
                    logging.info(f"VPForce Configurator exited with code {ret}")
            finally:
                G.vpconf_init_pending = False
                # Deliver any telemetry frame that arrived while frames were
                # suspended — in the MSFS menus it is the ONLY frame there is
                # (stop latch), and losing it meant no aircraft until a
                # camera-state change.
                tm = getattr(G, "telem_manager", None)
                if tm is not None:
                    try:
                        tm.flush_deferred_startup_frame()
                    except Exception:
                        logging.exception("deferred startup frame flush failed")

        thread = threading.Thread(target=exec)
        thread.start()

    else:
        logging.error("Unable to find VPforce Configurator installation location")


class ResultThread(threading.Thread):
    """
    A custom Thread that can return the value of the function 
    runned inside it
    """
    fx_output = None
    error = None

    def run(self, *args, **kwargs):
        try:
            if self._target:
                self.fx_output = self._target(*self._args, **self._kwargs)
        except Exception as e:
            self.error = e
        finally:
            # Avoid a refcycle if the thread is running a function with
            # an argument that has a member that points to the thread.
            del self._target, self._args, self._kwargs

    def await_output(self):
        """
        Wait for the thread to finish and return the return value of fx
        """
        self.join()
        return self.fx_output

    def get_error(self):
        """
        Return error if any, return None if no error occured
        """
        self.join()
        return self.error


def threaded(daemon=False):
    """
    A decorator to run a function in a separate thread, this is useful
    when you want to do any IO operations (network request, prints, etc...)
    and want to do something else while waiting for it to finish.
    :param fx: the function to run in a separate thread
    :param daemon: boolean whether or not to run as a daemon thread
    :return: whatever fx returns
    """
    def _threaded(fx):
        def wrapper(*args, **kwargs):
            thread = ResultThread(target=fx, daemon=daemon,
                                  args=args, kwargs=kwargs)
            thread.start()
            return thread
        return wrapper

    return _threaded


class HiDpiPixmap(QPixmap):
    def __init__(self, arg):
        ratio = QGuiApplication.instance().devicePixelRatio()

        if isinstance(arg, QSize):  # If arg is QSize, create a pixmap with a specific size
            super().__init__(QSize(round(arg.width() * ratio), round(arg.height() * ratio)))
        elif isinstance(arg, str):  # If arg is a filename, create a pixmap from a file
            super().__init__(arg)
        else:  # If no arg is provided, create an empty pixmap
            super().__init__()

        self.setDevicePixelRatio(ratio)

    def _scaled(self, width, height, aspectRatioMode=QtCore.Qt.AspectRatioMode.KeepAspectRatio, transformMode=QtCore.Qt.TransformationMode.SmoothTransformation):
        ratio = self.devicePixelRatio()
        scaled_pixmap = super().scaled(int(width * ratio), int(height * ratio), aspectRatioMode, transformMode)
        scaled_pixmap.setDevicePixelRatio(ratio)
        return scaled_pixmap
