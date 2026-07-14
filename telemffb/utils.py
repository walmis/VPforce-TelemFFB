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
import hashlib
import html
import inspect
from datetime import datetime, timedelta
import math
import os
import random
import re
import shutil
import tempfile
import typing
from typing import override
import zipfile
from collections import defaultdict, deque
import threading

import select
import logging
import sys

try:
    import winreg
except ImportError:
    # winreg is Windows-only; allow this module to import on Linux/macOS for
    # offline tests of platform-agnostic helpers (filters, scaling, etc.).
    # The two callers that actually use winreg are wrapped to fail loudly if
    # invoked on a non-Windows host.
    winreg = None
import socket
import time
import traceback
import urllib.error
import urllib.request
import uuid
import zlib
import subprocess
import json
import ssl
import xml.etree.ElementTree as ET

import numpy as np
import akima

from PyQt6.QtCore import QCoreApplication, QSize, QThread, pyqtSignal, QObject, QSettings, Qt, QMetaObject, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QPixmap, QTextCharFormat, QColor

from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog
import stransi

from enum import Enum, auto

import telemffb.globals as G
import telemffb.xmlutils as xmlutils
from .util import conversions as conv

def check_min_firmware_version(dev_firmware_version, min_firmware_version):
    """Check if device firmware version meets minimum requirements."""
    minver = re.sub(r'\D', '', min_firmware_version)
    devver = re.sub(r'\D', '', dev_firmware_version)
    return devver >= minver


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


def dbprint(color, msg, instance=None):
    if instance is not None:
        if instance != G.device_type:
            return
    reset = '\033[0m'
    match color:
        case "red":
            ccode = '\033[91m'
        case 'yellow':
            ccode = '\033[93m'
        case 'blue':
            ccode = '\033[94m'
        case 'green':
            ccode = '\033[92m'
        case _:
            ccode = '\033[0m'
    print(f"{ccode}{msg}{reset}")

def debug_timed(func):
    """
    import debug_timed from utils into any module
    add '@debug_timed` decorator to any method
    timing results will be logged along with the calling function and arguments that were passed


    """

    def wrapper(*args, **kwargs):
        if not G.master_instance:
            return func(*args, **kwargs)
        # Get caller frame
        caller_frame = inspect.stack()[1]
        caller_name = caller_frame.function
        start = time.perf_counter()
        result = func(*args, **kwargs)
        arg_strs = [repr(a) for a in args]
        kwarg_strs = [f"{k}={v!r}" for k, v in kwargs.items()]
        all_args = ", ".join(arg_strs + kwarg_strs)
        elapsed = (time.perf_counter() - start) * 1000

        logging.info(f"[TIMER] {elapsed:.2f} ms taken by {func.__name__} - called by {caller_name} ({all_args})")
        return result

    return wrapper

def debug_caller_args(color):
    frame = inspect.currentframe().f_back
    caller_frame = frame.f_back

    callee = frame.f_code.co_name
    caller = caller_frame.f_code.co_name if caller_frame else "<top-level>"

    args, _, _, values = inspect.getargvalues(frame)
    arg_list = ", ".join(f"{arg}={repr(values[arg])}" for arg in args)

    dbprint(color, f'"{callee}" called by "{caller}" Args: {arg_list}')

def millis() -> int:
    """return millisecond timer

    :return: milliseconds
    :rtype: int
    """
    return time.perf_counter_ns() // 1000000

def micros() -> int:
    """return microsecond timer

    :return: microseconds
    :rtype: int
    """
    return time.perf_counter_ns() // 1000


class Interp1D:
    def __init__(self, x, y, bounds_error=False, fill_value=None):
        self.x = np.asarray(x)
        self.y = np.asarray(y)
        self.bounds_error = bounds_error
        self.fill_value = fill_value

        if np.any(np.diff(self.x) <= 0):
            raise ValueError("x values must be strictly increasing")

    def __call__(self, x_new):
        x_new = np.asarray(x_new)

        # Interpolation for in-bounds
        y_interp = np.interp(x_new, self.x, self.y)

        # Out-of-bounds handling
        if not self.bounds_error and self.fill_value is not None:
            below = x_new < self.x[0]
            above = x_new > self.x[-1]
            y_interp = np.where(below, self.fill_value[0], y_interp)
            y_interp = np.where(above, self.fill_value[1], y_interp)
        elif self.bounds_error:
            if np.any(x_new < self.x[0]) or np.any(x_new > self.x[-1]):
                raise ValueError("A value in x_new is outside the interpolation range.")

        return y_interp if x_new.ndim > 0 else y_interp.item()

class Akima1DInterpolator:
    """
        A drop-in replacement for scipy.interpolate.Akima1DInterpolator
        using the 'akima' package backend, but mimicking SciPy's behavior.

        - No extrapolation by default: returns np.nan for out-of-bounds inputs.
        - Accepts any input shape (scalar, list, or ndarray).
        """

    def __init__(self, x, y, extrapolate=False):
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        if len(self.x) != len(self.y):
            raise ValueError("x and y must have the same length")

        # Must be strictly increasing
        sort_idx = np.argsort(self.x)
        self.x = self.x[sort_idx]
        self.y = self.y[sort_idx]

        self._interp = akima.interpolate
        self.extrapolate = extrapolate

    def __call__(self, x_new):
        scalar_input = np.isscalar(x_new)
        x_new = np.atleast_1d(x_new).astype(float)

        y_new = self._interp(self.x, self.y, x_new)

        if not self.extrapolate:
            out_of_bounds = (x_new < self.x[0]) | (x_new > self.x[-1])
            y_new = np.where(out_of_bounds, np.nan, y_new)

        return y_new[0] if scalar_input else y_new


class Smoother:
    def __init__(self):
        self.value_dict = {}

    def get_average(self, key, value, sample_size=10):
        # Get average of 'sample_size' instances of 'value', tracked by string 'key'
        if key not in self.value_dict:
            self.value_dict[key] = []
        self.value_dict[key].append(value)
        if len(self.value_dict[key]) > sample_size:
            self.value_dict[key].pop(0)

        values = self.value_dict.get(key, [])
        if not values:
            return 0
        return sum(values) / len(values)

    def get_rolling_average(self, key, value, window_ms=1000):
        # get average value of a rolling window of tracker string 'key', updated by 'value' over a period of 'window_ms'
        current_time_ms = time.time() * 1000  # Convert current time to milliseconds

        if key not in self.value_dict:
            self.value_dict[key] = deque()

        # Remove values older than the specified window
        while self.value_dict[key] and (current_time_ms - self.value_dict[key][0][1]) > window_ms:
            self.value_dict[key].popleft()

        self.value_dict[key].append((value, current_time_ms))

        if not self.value_dict[key]:
            return 0

        total = sum(val[0] for val in self.value_dict[key])
        return total / len(self.value_dict[key])


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
        "aoa": ["AoA Effect", "aoa_effect_gain"],
        "ap_spring": ["Autopilot Spring", ""],
        "buffeting": ["AoA/Stall Buffeting", "buffeting_intensity"],
        "bombs": ["Bomb Release", "weapon_release_intensity"],
        "canopymovement": ["Canopy Motion", "canopy_motion_intensity"],
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
        "trim_spring": ["Trim Override Spring", ""],
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


def archive_logs(directory):
    today = datetime.today().strftime('%Y%m%d')

    for filename in os.listdir(directory):
        if filename.endswith('.log'):
            file_date = filename[:-4][-8:]  # Extract the date part
            if file_date != today:
                zip_filename = f"TelemFFB_Log_Archive_{file_date}.zip"
                readable_date = datetime.strptime(file_date, "%Y%m%d").strftime('%B %d, %Y')
                logging.info(f"Archiving logs from {readable_date} into {zip_filename}")
                zip_path = os.path.join(directory, zip_filename)

                with zipfile.ZipFile(zip_path, 'a', compression=zipfile.ZIP_LZMA, compresslevel=9) as zip_file:
                    log_file_path = os.path.join(directory, filename)
                    zip_file.write(log_file_path, os.path.basename(log_file_path))
                    os.remove(log_file_path)  # Remove the original log file
    if G.system_settings.get("pruneLogs", False):
        num = G.system_settings.get('pruneLogsNum', 1)
        unit = G.system_settings.get('pruneLogsUnit', "Month(s)")
        prune_log_files(directory, num, unit)


def prune_log_files(path, number, unit):
    # Define mapping from unit strings to timedelta units
    units_mapping = {
        "Day(s)": 1,
        "Week(s)": 7,
        "Month(s)": 30  # approximate month as 30 days
    }

    # Get the timedelta unit from the mapping
    num_days = number * units_mapping.get(unit)
    if num_days is None:
        raise ValueError("Invalid unit. Valid units are: 'Day(s)', 'Week(s)', 'Month(s)'.")


    # Calculate the cutoff date
    cutoff_date = datetime.now() - timedelta(**{"days": num_days + 1})
    # Iterate over log files and delete files older than the cutoff date
    for filename in os.listdir(path):
        if filename.endswith(".zip") and filename.startswith("TelemFFB_Log_Archive_"):
            # Extract date from filename
            date_str = filename.split("_")[-1].split(".")[0]
            try:
                file_date = datetime.strptime(date_str, "%Y%m%d")
            except ValueError:
                # Skip files with invalid date format
                continue

            # Delete file if older than cutoff date
            if file_date < cutoff_date:
                os.remove(os.path.join(path, filename))
                logging.info(f'Deleting log archive: {filename} as it has exceeded the pruning threshold of {num_days} Days')


def create_ssl_context():
    return ssl._create_unverified_context()


def _decode_http_response(response):
    return response.read().decode("utf-8", errors="replace")


def open_url(url, data=None, headers=None, timeout=30, method=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    return urllib.request.urlopen(request, context=create_ssl_context(), timeout=timeout)


def fetch_json_url(url, timeout=30):
    with open_url(url, timeout=timeout) as response:
        return json.loads(_decode_http_response(response))


def _encode_multipart_formdata(fields=None, files=None):
    boundary = f"----TelemFFBBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields or []:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        if isinstance(value, bytes):
            body.extend(value)
        else:
            body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for name, filename, content, content_type in files or []:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def post_multipart_url(url, files, fields=None, timeout=30):
    data, content_type = _encode_multipart_formdata(fields=fields, files=files)
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
        "Accept": "application/json",
    }
    with open_url(url, data=data, headers=headers, timeout=timeout, method="POST") as response:
        return response.status, _decode_http_response(response)


def classify_http_exception(exc):
    if isinstance(exc, urllib.error.HTTPError):
        try:
            text = _decode_http_response(exc)
        except Exception:
            text = str(exc)
        return {"status_code": exc.code, "text": text}

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return {"error": "timeout"}

    reason = getattr(exc, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return {"error": "timeout"}

    if isinstance(exc, urllib.error.URLError):
        return {"error": "connection"}

    return {"error": str(exc), "traceback": traceback.format_exc()}

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

def _create_support_bundle_zip(zip_file_path, userconfig_rootpath, exceptions=None):
    """Internal helper to create a support bundle zip file.
    
    Args:
        zip_file_path: Output path for the zip file
        userconfig_rootpath: Path to user config directory
        exceptions: Optional list of ExceptionRecord objects to include
    """
    from datetime import datetime
    import telemffb.winpaths as winpaths

    
    # Get the system settings
    sys_dict = read_all_system_settings()
    
    # Create the support zip file directly
    with zipfile.ZipFile(zip_file_path, 'w', compression=zipfile.ZIP_LZMA, compresslevel=9) as support_zip:
        # Add userconfig_v2.xml
        userconfig_path = os.path.join(userconfig_rootpath, "userconfig_v2.xml")
        legacy_userconfig_path = os.path.join(userconfig_rootpath, "userconfig.xml")

        if os.path.exists(userconfig_path):
            support_zip.write(userconfig_path, "userconfig_v2.xml")

        if os.path.exists(legacy_userconfig_path):
            support_zip.write(userconfig_path, "userconfig.xml")

        # Add log files
        log_folder = os.path.join(userconfig_rootpath, "log")
        if os.path.exists(log_folder):
            for folder_name, subfolders, filenames in os.walk(log_folder):
                for filename in filenames:
                    file_path = os.path.join(folder_name, filename)
                    arcname = os.path.relpath(file_path, userconfig_rootpath)
                    support_zip.write(file_path, arcname)
        
        # Add system settings
        cfg_content = "\n".join(f"{key}={value}" for key, value in sys_dict.items())
        support_zip.writestr("system_settings.cfg", cfg_content)
        
        # Add exception details if provided
        if exceptions:
            exc_content = []
            exc_content.append(f"Exception Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            exc_content.append(f"Total Exceptions: {len(exceptions)}\n")
            exc_content.append("=" * 80 + "\n\n")
            
            for i, exc in enumerate(exceptions, 1):
                exc_content.append(f"Exception {i}/{len(exceptions)}\n")
                exc_content.append("-" * 80 + "\n")
                exc_content.append(exc.format_full())
                exc_content.append("\n" + "=" * 80 + "\n\n")
            
            support_zip.writestr("exceptions.txt", "".join(exc_content))
        
        # Add DCS files if available
        try:
            saved_games = winpaths.get_path(winpaths.FOLDERID.SavedGames)
            if saved_games:
                dcs_variant = get_dcs_variant()
                dcs_folders = ['DCS', 'DCS.openbeta']
                if dcs_variant and f'DCS.{dcs_variant}' not in dcs_folders:
                    dcs_folders.append(f'DCS.{dcs_variant}')
                
                for dcs_folder in dcs_folders:
                    dcs_path = os.path.join(saved_games, dcs_folder)
                    if os.path.exists(dcs_path):
                        # Add DCS log file
                        dcs_log = os.path.join(dcs_path, "Logs", "dcs.log")
                        if os.path.exists(dcs_log):
                            support_zip.write(dcs_log, f"{dcs_folder}/Logs/dcs.log")
                        
                        # Add Export.lua if present
                        export_lua = os.path.join(dcs_path, "Scripts", "Export.lua")
                        if os.path.exists(export_lua):
                            support_zip.write(export_lua, f"{dcs_folder}/Scripts/Export.lua")
        except Exception as e:
            # If DCS detection fails, continue without DCS files
            pass


def create_support_bundle_data(userconfig_rootpath, exceptions=None):
    """Create support bundle as bytes (in memory) for API upload.
    
    Args:
        userconfig_rootpath: Path to user config directory
        exceptions: Optional list of ExceptionRecord objects to include
        
    Returns:
        bytes: Support bundle as zip file data
    """
    # Use a temporary file to create the zip, then read it into memory
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        tmp_path = tmp.name
    
    try:
        _create_support_bundle_zip(tmp_path, userconfig_rootpath, exceptions)
        with open(tmp_path, 'rb') as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def report_exceptions(parent_widget=None, on_complete_callback=None):
    """Report exceptions by uploading support bundle to API.
    
    This function runs bundle creation and HTTP POST in a background QThread
    so the Qt event loop (UI) remains responsive. It includes user prompts
    and confirmation dialogs.
    
    Args:
        parent_widget: Parent QWidget for dialog boxes (can be None)
        on_complete_callback: Optional callback function to call when upload completes (success or failure)
    
    Returns:
        bool: True if upload process started, False if user cancelled or no exceptions
    """
    
    exceptions_list = G.exception_tracker.get_exceptions()

    # Confirm upload
    reply = QMessageBox.question(
        parent_widget,
        "Report Exceptions",
        (
            f"Upload Support Bundle to VPforce support?\n\n"
            f"This will include:\n"
            f"  • Exception details and tracebacks\n"
            f"  • System configuration\n"
            f"  • Application logs\n\n"
            f"You will need to complete a verification challenge."
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )

    if reply != QMessageBox.StandardButton.Yes:
        return False

    # Progress dialog (indeterminate)
    progress = QProgressDialog("Creating support bundle and uploading to server...", None, 0, 0, parent_widget)
    progress.setWindowTitle("Uploading...")
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.show()

    # Worker that runs in background thread
    class UploadWorker(QObject):
        finished = pyqtSignal(bool, dict)

        def __init__(self, api_url: str, userconfig_rootpath: str, exceptions_list):
            super().__init__()
            self.api_url = api_url
            self.userconfig_rootpath = userconfig_rootpath
            self.exceptions_list = exceptions_list

        def run(self):
            try:
                # Create bundle in memory
                bundle = create_support_bundle_data(self.userconfig_rootpath, self.exceptions_list)

                status_code, response_text = post_multipart_url(
                    self.api_url,
                    files=[('bundle', 'support_bundle.zip', bundle, 'application/zip')],
                    timeout=30,
                )

                if status_code == 200:
                    try:
                        payload = json.loads(response_text) if response_text else {'challenge_url': None}
                    except Exception:
                        payload = {'challenge_url': None}
                    self.finished.emit(True, payload)
                else:
                    self.finished.emit(False, {'status_code': status_code, 'text': response_text})

            except Exception as e:
                self.finished.emit(False, classify_http_exception(e))

    # Build API URL and capture userconfig path now
    userconfig_rootpath = G.userconfig_rootpath
    api_url = 'https://vpforce.eu/telemffb/api/upload'

    # Prepare thread and worker
    thread = QThread(parent_widget)
    worker = UploadWorker(api_url, userconfig_rootpath, exceptions_list)
    worker.moveToThread(thread)

    # Connect signals
    thread.started.connect(worker.run)

    def on_finished(success: bool, payload: dict):
        try:
            progress.close()
        except Exception:
            pass

        if success:
            challenge_url = payload.get('challenge_url') if isinstance(payload, dict) else None
            if challenge_url:
                # Open challenge URL in browser from main thread
                import webbrowser
                webbrowser.open(challenge_url)
                QMessageBox.information(
                    parent_widget,
                    "Verification Required",
                    "A verification page has been opened in your browser.\n\nPlease complete the challenge to submit your report.",
                )
            else:
                QMessageBox.warning(parent_widget, "Upload Error", "Server did not return a challenge URL.")
        else:
            # Handle common errors
            if payload.get('error') == 'connection':
                QMessageBox.critical(
                    parent_widget,
                    "Connection Error",
                    "Could not connect to the support server.\n\nPlease check your internet connection and try again.",
                )
            elif payload.get('error') == 'timeout':
                QMessageBox.critical(parent_widget, "Timeout Error", "Upload timed out.\n\nPlease try again later.")
            else:
                # Show server response if available
                status = payload.get('status_code')
                text = payload.get('text') or payload.get('error') or ''
                if status:
                    QMessageBox.critical(
                        parent_widget,
                        "Upload Failed",
                        f"Failed to upload support bundle.\n\nStatus: {status}\nMessage: {text[:200]}",
                    )
                else:
                    QMessageBox.critical(
                        parent_widget,
                        "Upload Error",
                        f"An error occurred while uploading:\n\n{text}",
                    )

        # Clean up thread and worker
        try:
            thread.quit()
            thread.wait(2000)
        except Exception:
            pass

        worker.deleteLater()
        thread.deleteLater()
        
        # Call completion callback if provided
        if on_complete_callback:
            try:
                on_complete_callback(success, payload)
            except Exception:
                pass

    worker.finished.connect(on_finished)
    # Ensure we stop the thread if it finishes
    worker.finished.connect(thread.quit)
    thread.start()
    
    return True


def create_support_bundle(userconfig_rootpath):
    """Create a support bundle with a file save dialog, showing progress during creation.
    
    This function runs bundle creation in a background QThread with an indeterminate
    progress dialog, keeping the UI responsive during the zip operation.
    
    Args:
        userconfig_rootpath: Path to user config directory
    """
    # Prompt the user for the destination and filename for the zip file
    file_dialog = QFileDialog()
    file_dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    file_dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    file_dialog.setNameFilter("Zip Files (*.zip)")

    if not file_dialog.exec():
        return

    # Get the selected file path
    zip_file_path = file_dialog.selectedFiles()[0]

    # Progress dialog (indeterminate)
    progress = QProgressDialog("Creating support bundle...", None, 0, 0)
    progress.setWindowTitle("Creating Support Bundle")
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.show()

    # Worker to perform the zip creation in background
    class BundleWorker(QObject):
        finished = pyqtSignal(bool, dict)

        def __init__(self, userconfig_rootpath, zip_file_path):
            super().__init__()
            self.userconfig_rootpath = userconfig_rootpath
            self.zip_file_path = zip_file_path

        def run(self):
            try:
                _create_support_bundle_zip(self.zip_file_path, self.userconfig_rootpath)
                # Success
                self.finished.emit(True, {"path": self.zip_file_path})
            except Exception as e:
                # Report error
                try:
                    self.finished.emit(False, {"error": str(e)})
                except Exception:
                    pass

    # Prepare thread and worker
    thread = QThread()
    worker = BundleWorker(userconfig_rootpath, zip_file_path)
    worker.moveToThread(thread)

    # Connect signals
    thread.started.connect(worker.run)

    def _on_finished(success: bool, payload: dict):
        try:
            progress.close()
        except Exception:
            pass

        if success:
            try:
                QMessageBox.information(
                    None, 
                    "Support Bundle Created", 
                    f"Support bundle saved to:\n{payload.get('path')}"
                )
            except Exception:
                pass
        else:
            try:
                QMessageBox.critical(
                    None, 
                    "Error", 
                    f"Failed to create support bundle:\n{payload.get('error')}"
                )
            except Exception:
                pass

        # Cleanup
        try:
            thread.quit()
            thread.wait(2000)
        except Exception:
            pass

        worker.deleteLater()
        thread.deleteLater()

    worker.finished.connect(_on_finished)
    # Ensure thread stops when finished
    worker.finished.connect(thread.quit)
    thread.start()


def read_all_system_settings():
    import winreg

    REG_PATH = r"SOFTWARE\VPForce\TelemFFB"

    settings_dict = {}

    try:
        registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)

        # Iterate through all values in the registry key
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(registry_key, index)
                settings_dict[name] = value
                index += 1
            except WindowsError as e:
                # Break when there are no more values
                if e.winerror == 259:  # ERROR_NO_MORE_ITEMS
                    break
    except WindowsError:
        # Handle errors (key not found, etc.)
        pass
    finally:
        try:
            winreg.CloseKey(registry_key)
        except UnboundLocalError:
            # Handle case where registry_key is not defined
            pass

    return settings_dict


class SystemSettings(QSettings):
    # Type hints for common settings to improve IDE autocompletion and discovery
    # These are intentionally class-level annotations (no runtime effect) so editors can
    # surface available settings via dot-completion (e.g. settings.logLevel)
    logLevel: str
    telemTimeout: int
    saveWindow: bool
    saveLastTab: bool
    enableVPConfStartup: bool
    pathVPConfStartup: str
    enableVPConfExit: bool
    enableVPConfGlobalDefault: bool
    pathVPConfExit: str
    enableResetGainsExit: bool

    pruneLogs: bool
    pruneLogsNum: int
    pruneLogsUnit: str
    ignoreUpdate: bool
    startToTray: bool
    closeToTray: bool
    enableDCS: bool
    enableMSFS: bool
    enableXPLANE: bool
    validateXPLANE: bool
    pathXPLANE: str
    validateIL2: bool
    pathIL2: str
    portIL2: int
    enableBMS: bool
    masterInstance: int
    autolaunchMaster: bool
    autolaunchJoystick: bool
    autolaunchPedals: bool
    autolaunchCollective: bool
    startMinJoystick: bool
    startMinPedals: bool
    startMinCollective: bool
    startHeadlessJoystick: bool
    startHeadlessPedals: bool
    startHeadlessCollective: bool
    debug: bool

    default_inst = {
        'logLevel': 'INFO',
        'telemTimeout': 200,
        'saveWindow': True,
        'saveLastTab': True,
        'enableVPConfStartup': False,
        'pathVPConfStartup': '',
        'enableVPConfExit': False,
        'enableVPConfGlobalDefault': False,
        'pathVPConfExit': '',
        'enableResetGainsExit': False,
        'teleplotPort': '',
        'teleplotVars': ''
    }

    globl_sys_dict = {
        'pruneLogs': False,
        'pruneLogsNum': 1,
        'pruneLogsUnit': 'Week(s)',
        'ignoreUpdate': False,
        'startToTray': False,
        'closeToTray': False,
        'enableDCS': False,
        'enableMSFS': False,
        'enableXPLANE': False,
        'validateXPLANE': False,
        'pathXPLANE': '',
        'enableIL2': False,
        'validateIL2': True,
        'focus_pauseIL2': True,
        'validateDCS': True,
        'pathIL2': 'C:/Program Files/IL-2 Sturmovik Great Battles',
        'portIL2': 34385,
        'il2_fwd_enable': False,
        'il2_fwd_destinations': '[]',
        'enableBMS': False,
        'masterInstance': 1,
        'autolaunchMaster': False,
        'autolaunchJoystick': False,
        'autolaunchPedals': False,
        'autolaunchCollective': False,
        'autolaunchShaker': False,
        'startMinJoystick': False,
        'startMinPedals': False,
        'startMinCollective': False,
        'startMinShaker': False,
        'startHeadlessJoystick': False,
        'startHeadlessPedals': False,
        'startHeadlessCollective': False,
        'startHeadlessShaker': False,
        'pidShaker': '2059',  # synthetic ID; not a real USB PID. Used for IPC port + settings namespace.
        'debug': False,  # debug is False by default.  To permanently enable the debug menu, manually set debug = true (1) in registry
        'shakerDevice': '',  # bass-shaker output device name; '' = system default
        'shakerGain': 1.0,
        'shakerChannelMode': 'mono',  # one of: 'mono', 'left', 'right', 'pan'
        'shakerPan': 0.0,             # [-1, +1]; only used when shakerChannelMode == 'pan'
        'shakerProfile': 'Generic',   # active ShakerProfile name in shaker_profiles.json
    }

    @property
    def defaults(self):
        s = {}
        s.update(self.default_inst)
        s.update(self.globl_sys_dict)
        return s

    def __init__(self, pid=None, tp=None):
        super().__init__('VPforce', 'TelemFFB')
        #self.def_inst_sys_dict, self.def_global_sys_dict = get_default_sys_settings(pid, tp, cmb=False)
        # No additional initialization required. Keep QSettings initialization intact.
        return

    @override
    def setValue(self, key: str, value, instance=None) -> None:
        if instance:
            super().setValue(f"{instance}/{key}", value)
        else:
            super().setValue(key, value)

    def __getattr__(self, name: str):
        """Allow dot-access to settings, e.g. settings.someOption

        If the setting does not exist in QSettings, the default from
        SystemSettings.defaults (if any) or None is returned.
        """
        # Only handle attribute-style access for settings keys. Let Python
        # raise AttributeError for truly missing attributes.
        try:
            return self.get(name)
        except Exception:
            raise AttributeError(name)

    def __setattr__(self, name: str, value):
        """Assigning to attributes will persist the value to QSettings unless
        it's an internal attribute (starts with '_') or a real class attribute.
        """
        # Allow normal attribute behavior for internals and attributes that
        # already exist on the instance/class (to avoid interfering with QSettings internals)
        if name.startswith('_') or name in self.__dict__ or hasattr(type(self), name):
            object.__setattr__(self, name, value)
            return

        # Otherwise persist via QSettings
        try:
            # store as instance/global agnostic key; setValue will handle saving
            # under instance-specific key when appropriate elsewhere
            self.setValue(name, value)
        except Exception:
            # Fallback: if persistence fails, store as a regular attribute
            object.__setattr__(self, name, value)

    def __dir__(self):
        # Include known setting keys from defaults to improve autocompletion in REPLs and editors
        extra = []
        try:
            extra = list(self.defaults.keys())
        except Exception:
            extra = []
        return sorted(set(super().__dir__() + extra))

    def get(self, name, default=None):       
        # check instance params
        val = self.value(f"{G.device_type}/{name}")
        if val is None:
            # check global param
            val = self.value(name)

        if val is None:
            val = self.defaults.get(name, None)
            if val is not None:
                if name in self.default_inst:
                    self.setValue(f"{G.device_type}/{name}", val) # save instance variable
                else:
                    self.setValue(name, val)
                return val
            #logging.warn(f"SystemSettings: not found {name} default={repr(default)} val={repr(val)}")
            return default

        if val == "true": 
            val = 1
        elif val == "false":
            val = 0
        try:
            val = int(val)
        except Exception: pass

        return val


def mix(a, b, val):
    return a * (1 - val) + b * (val)

_TRUE_SET = frozenset(["true", "yes", "on", "enable", "enabled"])
_FALSE_SET = frozenset(["false", "no", "off", "disable", "disabled"])
_UNIT_CONVERSIONS = {
    "%": conv.percent,
    "kt": conv.kt2ms,
    "kph": conv.kmh2ms,
    "fpm": 0.00508,
    "m/s": 1,
    "mph": conv.mph2ms,
    "deg": 1,
    "ms": 1,
    "hz": 1,
    "m": 1,
    "ft": conv.ft2m,
    "in": conv.in2m,
}

def to_number(v: str):
    """Try to convert string to number
    If unable, return the original string
    """
    orig_v = v
    if isinstance(v, (bool, int, float)):
        return v

    v_lower = v.lower()
    if v_lower in _TRUE_SET:
        return True
    if v_lower in _FALSE_SET:
        return False

    scale = 1

    for unit, factor in _UNIT_CONVERSIONS.items():
        if v_lower.endswith(unit) or v_lower.startswith(unit):
            scale = factor
            v = v.strip(unit)
            break

    try:
        return round(float(v) * scale, 4) if "." in v else int(v) * scale
    except ValueError:
        return orig_v


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


def sock_readable(s) -> bool:
    r, _, _ = select.select([s], [], [], 0)
    return s in r


def clamp(n, minn, maxn):
    return type(n)(sorted((minn, n, maxn))[1])

def clamp_minmax(n, max):
    return clamp(n, -max, max)


def scale(val, src: tuple, dst: tuple, return_round=False, return_int=False):
    """
    Scale the given value from the scale of src to the scale of dst.
    """
    if src[0] == src[1]: # avoid div/0
        return dst[1]
    result = (val - src[0]) * (dst[1] - dst[0]) / (src[1] - src[0]) + dst[0]
    if return_round:
        return round(result)
    elif return_int:
        return int(result)
    else:
        return result


def scale_clamp(val, src: tuple, dst: tuple, return_round=False, return_int=False):
    """
    Scale the given value from the scale of src to the scale of dst. 
    and clamp the result to dst
    """
    v = scale(val, src, dst, return_round=return_round, return_int=return_int)
    return clamp(v, dst[0], dst[1])


def non_linear_scaling(x, min_val, max_val, curvature=1.0):
    # Scale the input value to a value between 0 and 1 within the given range
    scaled_value = (x - min_val) / (max_val - min_val)

    # Apply the non-linear scaling based on the specified curvature
    if curvature < 0:
        result = scaled_value ** (1 / abs(curvature))
    elif curvature > 0:
        result = scaled_value ** curvature
    else:
        result = scaled_value

    return result


def gaussian_scaling(x, min_val, max_val, peak_percentage=0.5, curve_width=1.0):
    # Calculate the midpoint of the range and the distance between the min and max values
    midpoint = (min_val + max_val) / 2
    range_distance = (max_val - min_val)

    # Calculate the value of x as a percentage between 0 and 1 in the range
    scaled_value = (x - min_val) / range_distance

    # Calculate the distance of the scaled value from the peak_percentage
    distance_from_peak = abs(scaled_value - peak_percentage)

    # Apply the Gaussian distribution to get the scaling factor
    scaling_factor = math.exp(-0.5 * ((distance_from_peak / (curve_width / 2)) ** 2))

    # Scale the result back to the desired range (0 to 1)
    result = scaling_factor

    return result


def sine_point_in_time(amplitude, period_ms, phase_offset_deg=0):
    current_time = time.perf_counter()  # Get the current time in seconds with high resolution

    # Convert frequency from milliseconds to Hz
    frequency_hz = 1 / (period_ms / 1000)

    # Calculate the angular frequency (2 * pi * frequency)
    angular_frequency = 2 * math.pi * frequency_hz

    phase_offset_rad = math.radians(phase_offset_deg)

    # Calculate the value of the sine wave at the current time with phase offset
    value = amplitude * math.sin(angular_frequency * current_time + phase_offset_rad)

    # print(f"Amp:{amplitude}     |Freq:{frequency_hz}     |Offset:{phase_offset_deg}        |Val:{value}")

    return value


def interpolate_curve_y_point(curve_dict, input_x, conversion_factor=1):
    """Interpolates the Y value given curve data and input x value."""
    points = curve_dict.get("points", [])
    smooth_curve_enabled = curve_dict.get("smooth_curve_enabled", False)

    # Extract x and y values from points
    x_values = np.array([p["x"] for p in points]) * conversion_factor  # Convert curve points from user units to m/s
    y_values = np.array([p["y"] for p in points])

    # Handle out-of-bounds x_values
    if input_x <= x_values[0]:
        return y_values[0]
    if input_x >= x_values[-1]:
        return y_values[-1]

    # Perform interpolation
    if smooth_curve_enabled:
        if len(x_values) < 4:
            # Fallback to linear interpolation for insufficient points
            interpolation = Interp1D(x_values, y_values, bounds_error=False,
                                     fill_value=(y_values[0], y_values[-1]))
        else:
            interpolation = Akima1DInterpolator(x_values, y_values)
    else:
        interpolation = Interp1D(x_values, y_values, bounds_error=False,
                                 fill_value=(y_values[0], y_values[-1]))

    return float(interpolation(input_x))


def get_gain_from_gs(curve_settings, input_gs):
    if isinstance(curve_settings, str):
        settings = json.loads(curve_settings)
    elif isinstance(curve_settings, dict):
        settings = curve_settings
    else:
        raise ValueError("Invalid input: must be a JSON string or a dictionary.")

    curve_pos = settings.get("curve_pos", {})
    curve_neg = settings.get("curve_neg", {})
    gain_pos = settings.get('gain_pos') / 100
    gain_neg = settings.get('gain_neg') / 100

    interpolated_pos = round(float(interpolate_curve_y_point(curve_pos, input_gs) / 100) * gain_pos, 3)
    interpolated_neg = round(float(interpolate_curve_y_point(curve_neg, input_gs) / 100) * gain_neg, 3)

    return interpolated_pos, interpolated_neg


def get_gain_from_speed(curve_settings : str | dict, input_airspeed_ms):
    """
    Interpolates the % force input airspeed and the advanced spring curve settings passed.

    Args:
        json_string (str): JSON-encoded string containing x and y curve dictionaries, units, and scale.
        input_airspeed_ms (float): The airspeed in m/s for which to calculate the interpolated values.

    Returns:
        dict: A dictionary containing the interpolated X and Y gain values as a factor (0...1).
    """
    # Unit conversion factors (to m/s)
    UNIT_CONVERSIONS = {
        "kt": conv.kt2ms,
        "mph": conv.mph2ms,
        "kph": conv.kmh2ms,
        "m/s": 1.0,
    }

    if isinstance(curve_settings, dict):
        settings = curve_settings
    else:
        # Parse JSON string
        settings = json.loads(curve_settings)
    assert settings is not None, "Invalid settings for speed curve."

    # Extract curves and units
    curve_x = settings.get("curve_x", {})
    curve_y = settings.get("curve_y", {})
    gain_x = settings.get('gain_x')/100
    gain_y = settings.get('gain_y')/100
    units = settings.get("units", "m/s")  # Default to m/s if units not specified

    # Conversion factor to m/s
    conversion_factor = UNIT_CONVERSIONS[units]

    # Interpolate X and Y values
    # print(f"x:{gain_x}, y:{gain_y}")
    interpolated_x = round(float(interpolate_curve_y_point(curve_x, input_airspeed_ms, conversion_factor) / 100) * gain_x, 3)
    interpolated_y = round(float(interpolate_curve_y_point(curve_y, input_airspeed_ms, conversion_factor) / 100) * gain_y, 3)

    return {"x": interpolated_x, "y": interpolated_y}


def pressure_from_altitude(altitude_m):
    """Calculate pressure at specified altitude

    Args:
        altitude_m (float): meters

    Returns:
        float: Pressure in kpa
    """
    return 101.3 * ((288 - 0.0065 * altitude_m) / 288) ** 5.256


def calculate_checksum(file_path):
    crc = zlib.crc32(open(file_path, 'rb').read())
    return crc


def average(l):
    if not l:
        return 0
    return sum(l) / float(len(l))


def polar_to_cartesian_deg(angle_deg, magnitude):
    angle_rad = math.radians(angle_deg)
    x = magnitude * math.cos(angle_rad)
    y = magnitude * math.sin(angle_rad)
    return x, y


def add_vectors_deg(angle1_deg, mag1, angle2_deg, mag2):
    x1, y1 = polar_to_cartesian_deg(angle1_deg, mag1)
    x2, y2 = polar_to_cartesian_deg(angle2_deg, mag2)

    x_sum = x1 + x2
    y_sum = y1 + y2

    # Convert back to polar
    magnitude = math.hypot(x_sum, y_sum)
    angle_deg = math.degrees(math.atan2(y_sum, x_sum))

    return angle_deg, magnitude



class LowPassFilter:
    def __init__(self, cutoff_freq_hz, init_val=0.0, **kwargs):
        self.cutoff_freq_hz = cutoff_freq_hz
        self.alpha = 0.0
        self.x_filt = init_val
        self.last_update = time.perf_counter()

    def __call__(self, x):
        return self.update(x)

    def update(self, x):
        now = time.perf_counter()
        dt = now - self.last_update
        if dt > 1: self.x_filt = x  # initialize filter
        self.last_update = now
        self.alpha = dt / (1.0 / self.cutoff_freq_hz + dt)
        self.x_filt = self.alpha * x + (1.0 - self.alpha) * self.x_filt
        return self.x_filt

    @property
    def value(self):
        return self.x_filt


class HighPassFilter:
    def __init__(self, cutoff_freq_hz, init_val=0.0, **kwargs):
        self.RC = 1.0 / (2 * math.pi * cutoff_freq_hz)
        self.value = 0
        self.last_update = 0
        self.last_input = init_val
        self.value = init_val

    def __call__(self, x):
        return self.update(x)

    def update(self, x):
        now = time.perf_counter()
        dt = now - self.last_update
        if dt > 1:
            self.last_input = x  # initialize filter
            self.value = x

        self.last_update = now
        alpha = self.RC / (self.RC + dt)

        self.value = alpha * (self.value + x - self.last_input)
        self.last_input = x
        return self.value

    def reset(self):
        self.last_update = 0


class Derivative:
    def __init__(self, filter_hz=None) -> None:
        self.prev_update = 0
        self.prev_value = 0
        self.value = 0
        self.lpf = None
        self.derivative_dict = {}
        if filter_hz:
            self.lpf = LowPassFilter(filter_hz)

    def update(self, value):
        now = time.perf_counter()
        dx = value - self.prev_value
        self.prev_value = value
        dt = now - self.prev_update
        self.prev_update = now
        val = dx / dt
        if self.lpf:
            val = self.lpf.update(val)
        self.value = val

        return self.value

    def dampen_value(self, var, name, derivative_hz=5, derivative_k=0.1):
        # Check if derivative information is already stored, and initialize if not
        derivative_data = self.derivative_dict.get(name, None)
        if derivative_data is None:
            derivative_data = self.derivative_dict[name] = {
                'derivative': Derivative(derivative_hz),
                'cutoff_freq_hz': derivative_hz,
            }

        # Update the cutoff frequency if needed
        if derivative_data['cutoff_freq_hz'] != derivative_hz:
            derivative_data['derivative'].lpf.cutoff_freq_hz = derivative_hz
            derivative_data['cutoff_freq_hz'] = derivative_hz

        # Compute the derivative
        derivative = -derivative_data['derivative'].update(var) * derivative_k

        # Update the variable
        var += derivative

        return var
    
class Dampener(Derivative):
    def __init__(self, filter_hz=5, k=0.1):
        super().__init__(filter_hz)
        self.k = k

    def update(self, value, derivative_hz=5, derivative_k=0.1):
        # update filters if needed
        if self.lpf:
            if self.lpf.cutoff_freq_hz != derivative_hz:
                self.lpf.cutoff_freq_hz = derivative_hz
        if derivative_k != self.k:
            self.k = derivative_k

        derivative = -super().update(value) * self.k
        value += derivative
        return value


# PhaseAccumulator lives in telemffb.hw.shaker_synth (its only consumer).
# Re-exported here so external code can keep importing from utils.
from telemffb.hw.shaker_synth import PhaseAccumulator  # noqa: E402,F401


class DirectionModulator:
    pass


class RandomDirectionModulator(DirectionModulator):
    def __init__(self, *args, period=0.1, **kwargs):
        self.prev_upd = time.perf_counter()
        self.value = 0
        self.period = period

    def update(self):
        now = time.perf_counter()
        # dt = now - self.prev_upd
        if now - self.prev_upd > self.period:
            self.prev_upd = now
            random.seed()
            self.value = random.randint(0, 360)

        return self.value

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


def resolve_il2_ffb_device_ordinal(il2_korea_path, vendor_id, product_id):
    """
    Look up this device's DirectInput-style attach ordinal from IL-2 Korea's
    'known.devices.json', for matching the 'devNo' field in FFB telemetry records.

    known.devices.json entries carry an 'ident' field formatted as '<vid>_<pid>' (lowercase
    hex, no separators) and a 'lastAttachedId' which reflects the device's enumeration order -
    this is distinct from 'deviceId', which is the user-facing control-mapping slot in IL-2.

    Returns None if the file is missing, malformed, or no entry matches the given VID/PID.
    """
    known_devices_path = os.path.join(il2_korea_path, 'game\\data\\Input\\known.devices.json')
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

def calculate_crc(file_path):
    # Calculate CRC for a file
    crc = hashlib.md5()
    with open(file_path, 'rb') as file:
        for chunk in iter(lambda: file.read(4096), b''):
            crc.update(chunk)
    return crc.hexdigest()

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
    import winreg

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
    """Warn if DCRealistic autostart is active in Export.lua (known to break FFB spring effects in DCS)."""
    for line in export_data.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        if "DCREALISTIC_AUTOSTART" in stripped:
            logging.error(
                f"The DCRealistic autostart feature is enabled in:\n{export_lua_path}\n\n"
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

class AnsiColors:
    """ ANSI color codes """
    BLACK = "\033[30m"
    RED = "\033[38;5;160m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    PURPLE = "\033[35m"
    CYAN = "\033[36m"
    LIGHT_GRAY = "\033[37m"

    BLACKBG = "\033[40m"
    REDBG = "\033[48;5;160m"
    GREENBG = "\033[42m"
    YELLOWBG = "\033[43m"
    BLUEBG = "\033[44m"
    PURPLEBG = "\033[45m"
    CYANBG = "\033[46m"
    LIGHT_GRAYBG = "\033[47m"

    BRIGHT_REDBG = "\033[101m"

    GRAY = DARK_GRAY = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_BROWN = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_PURPLE = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    WHITE = "\033[97m"

    BOLD = "\033[1m"
    FAINT = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    BLINK = "\033[5m"
    NEGATIVE = "\033[7m"
    CROSSED = "\033[9m"
    END = "\033[0m"

    try:
        # cancel SGR codes if we don't write to a terminal
        if not __import__("sys").stdout.isatty():
            for _ in dir():
                if isinstance(_, str) and _[0] != "_":
                    locals()[_] = ""
        else:
            # set Windows console in VT mode
            if __import__("platform").system() == "Windows":
                kernel32 = __import__("ctypes").windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                del kernel32
    except: pass

def parseAnsiText(ansi_text):
    parsed = stransi.Ansi(ansi_text)
    current_format = QTextCharFormat()
    output = []
    for i in parsed.instructions():
        if isinstance(i, stransi.SetColor):
            if not i.color:
                current_format = QTextCharFormat()
            else:
                rgb = i.color.hex
                if i.role == stransi.color.ColorRole.BACKGROUND:
                    current_format.setBackground(QColor(rgb.hex_code))
                elif i.role ==  stransi.color.ColorRole.FOREGROUND:
                    current_format.setForeground(QColor(rgb.hex_code))
        elif isinstance(i, stransi.SetAttribute):
            match i.attribute:
                case stransi.attribute.Attribute.BOLD:
                    current_format.setFontWeight(100)
                case stransi.attribute.Attribute.DIM:
                    cl = current_format.foreground().color()
                    cl.setAlpha(128)
                    current_format.setForeground(cl)
                case stransi.attribute.Attribute.NEITHER_BOLD_NOR_DIM:
                    current_format.clearProperty(QTextCharFormat.Property.FontWeight)
                    current_format.clearForeground()
                case stransi.attribute.Attribute.ITALIC:
                    current_format.setFontItalic(True)
                case stransi.attribute.Attribute.NOT_ITALIC:
                    current_format.setFontItalic(False)
                case stransi.attribute.Attribute.UNDERLINE:
                    current_format.setFontUnderline(True)
                case stransi.attribute.Attribute.NOT_UNDERLINE:
                    current_format.setFontUnderline(False)
                case stransi.attribute.Attribute.NORMAL:
                    current_format = QTextCharFormat()
        else:
            output.append((i, QTextCharFormat(current_format)))
    return output

class OutLog(QtCore.QObject):
    textReceived = QtCore.pyqtSignal(str)

    def __init__(self, edit, out=None, color=None):
        QtCore.QObject.__init__(self)

        """(edit, out=None, color=None) -> can write stdout, stderr to a
        QTextEdit.
        edit = QTextEdit
        out = alternate stream ( can be the original sys.stdout )
        color = alternate color (i.e. color stderr a different color)
        """
        self.edit = edit
        self.out = out
        self.color = QtGui.QColor(color) if color else None
        self.textReceived.connect(self.on_received, Qt.ConnectionType.QueuedConnection)
        self.log_paused = False

    def isatty(self):
        return False

    def toggle_pause(self):
        # Toggle the pause state
        self.log_paused = not self.log_paused

    def on_received(self, m):
        p = parseAnsiText(m)
        try:
            if self.color:
                tc = self.edit.textColor()
                self.edit.setTextColor(self.color)

            self.edit.moveCursor(QtGui.QTextCursor.MoveOperation.End)
            for text, char_format in p:
                self.edit.setCurrentCharFormat(char_format)
                self.edit.insertPlainText(text)

            if self.color:
                self.edit.setTextColor(tc)
        except:
            pass

    def write(self, m):
        try:
            if not self.log_paused:
                self.textReceived.emit(m)
        except:
            pass
        if self.out:
            self.out.write(m)

    def flush(self):
        pass


class DedupHandler(logging.Handler):
    """Handler that suppresses immediate duplicate log records and emits
    a summary when a different message arrives or periodically while the
    same message keeps repeating.

    It forwards records to one or more inner handlers passed during
    construction. Thread-safe.
    """

    def __init__(self, handlers=None, period_seconds: float = 5.0):
        super().__init__()
        self.handlers = handlers or []
        self._lock = threading.Lock()
        self._last_key = None
        self._count = 0
        self._last_record = None
        self._first_ts = 0.0
        self._last_periodic_emit_ts = 0.0
        self.period_seconds = float(period_seconds)

    def _normalize_message(self, record: logging.LogRecord) -> str:
        """
        Return a normalized, *uncolored* message string for de-duplication.

        - First attempt to collapse msg+args via record.getMessage().
        - If that fails (e.g. bad % formatting), fall back to record.msg.
        - In either case, clear record.args so future getMessage() calls are safe.
        """
        # First, try to safely collapse msg+args into a single string
        try:
            txt = record.getMessage() or ""
            # Now that we've successfully formatted, freeze it and drop args
            record.msg = txt
            record.args = ()
        except Exception as e:
            # getMessage() itself failed (e.g. "not all arguments converted")
            # Fall back to the raw msg, but still clear args so it can't blow up later.
            record.args = ()
            try:
                txt = str(record.msg) if record.msg is not None else ""
            except Exception:
                # Last-resort fallback
                txt = f"<unformattable log message: {e}>"

        # At this point txt is *some* string, args is empty, so no more % formatting.
        parts = parseAnsiText(txt)
        return "".join(t for t, _ in parts)

    def _make_key(self, record: logging.LogRecord):
        # Key by level, logger name and normalized message
        return (record.levelno, record.name, self._normalize_message(record))

    def _make_summary_record(self, base_record: logging.LogRecord, repeated_count: int, periodic: bool = False) -> logging.LogRecord:
        if periodic:
            msg = f"{self._normalize_message(base_record)} (message repeated {repeated_count} times so far)"
        else:
            msg = f"{self._normalize_message(base_record)} (message repeated {repeated_count} times)"
        new_rec = logging.LogRecord(
            name=base_record.name,
            level=base_record.levelno,
            pathname=base_record.pathname,
            lineno=base_record.lineno,
            msg=msg,
            args=(),
            exc_info=None,
            func=base_record.funcName,
        )
        # preserve timestamp
        new_rec.created = base_record.created
        return new_rec

    def emit(self, record: logging.LogRecord):
        try:
            key = self._make_key(record)
            now = time.time()
            with self._lock:
                if key == self._last_key:
                    # same as previous: increment and buffer
                    self._count += 1
                    self._last_record = record

                    # On first repeat, set first timestamp if not set
                    if not self._first_ts:
                        self._first_ts = now

                    # If enough time passed since last periodic emit, emit a periodic summary
                    if self.period_seconds and (now - self._last_periodic_emit_ts) >= self.period_seconds and self._count > 1:
                        summary = self._make_summary_record(self._last_record, self._count, periodic=True)
                        for h in self.handlers:
                            try:
                                h.emit(summary)
                            except Exception:
                                pass
                        self._last_periodic_emit_ts = now

                    return

                # Different message: if previous one was repeated, emit final summary
                if self._count > 1 and self._last_record is not None:
                    summary = self._make_summary_record(self._last_record, self._count, periodic=False)
                    for h in self.handlers:
                        try:
                            h.emit(summary)
                        except Exception:
                            pass

                # Forward current record to inner handlers
                for h in self.handlers:
                    try:
                        h.emit(record)
                    except Exception:
                        pass

                # update tracking state
                self._last_key = key
                self._count = 1
                self._last_record = record
                self._first_ts = now
                self._last_periodic_emit_ts = now
        except Exception:
            # In case of any failure in dedup logic, fallback to best-effort forwarding
            for h in self.handlers:
                try:
                    h.emit(record)
                except Exception:
                    pass

    def flush(self):
        # flush inner handlers if they support flush
        for h in self.handlers:
            try:
                h.flush()
            except Exception:
                pass

    def close(self):
        # Emit pending summary if any
        try:
            with self._lock:
                if self._count > 1 and self._last_record is not None:
                    summary = self._make_summary_record(self._last_record, self._count)
                    for h in self.handlers:
                        try:
                            h.emit(summary)
                        except Exception:
                            pass
        except Exception:
            pass

        # close inner handlers
        for h in self.handlers:
            try:
                h.close()
            except Exception:
                pass

        super().close()


class FetchLatestVersion(QThread):
    workers = []

    version_result_signal = pyqtSignal(str, str)
    error_signal = pyqtSignal(str)
    def __init__(self, on_fetch, on_error) -> None:
        super().__init__()
        if on_fetch:
            self.version_result_signal.connect(on_fetch)
        if on_error:
            self.error_signal.connect(on_error)
        self.__class__.workers.append(self)
        self.start()


    def run(self):
        try:
            current_version = get_version()
            latest_version = None
            latest_url = None
            url = "https://vpforcecontrols.com/downloads/TelemFFB/"
            file = "latest.json"
            send_url = url + file

            if 'dirty' in current_version:
                logging.info("Running from source with locally modified files, skipping version check")
            else:
                try:
                    latest = fetch_json_url(send_url, timeout=10)
                    latest_version = latest["version"]
                    latest_url = url + latest["filename"]
                except Exception as e:
                    logging.exception(f"Error checking latest version status: {url}")
                    self.error_signal.emit(str(e))
            if getattr(sys, 'frozen', False):
                if 'local' in current_version or 'dirty' in current_version:
                    self.version_result_signal.emit('dev', 'dev')
                elif current_version != latest_version and latest_version is not None and latest_url is not None:
                    logging.debug(f"Current version: {current_version} | Latest version: {latest_version}")
                    self.version_result_signal.emit(latest_version, latest_url)
                elif current_version == latest_version:
                    self.version_result_signal.emit("uptodate", "uptodate")
                else:
                    self.version_result_signal.emit("error", "error")
            else:  # running from source

                current_version = current_version.removeprefix('local-')
                if '-dirty' in current_version:
                    self.version_result_signal.emit('dirty', 'dirty')
                else:
                    self.version_result_signal.emit("dev", "dev")

        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            self.__class__.workers.remove(self)


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

def convert_legacy_userconfig(path):
    """
    Upgrades a legacy userconfig file to the new format that includes profile tags and profileMappings.

    This function performs the following:
    - Identifies "User Default" entries based on 'type' settings.
    - Identifies modified aircraft (with settings but no type) as "Auto User" entries.
    - Appends appropriate <profile> tags to all models.
    - Creates <models> entries for missing "Auto User" profiles.
    - Adds <profileMappings> entries for all aircraft that were converted.
    - Skips execution if conversion has already been applied.

    Returns:
        bool: True if conversion was applied, False otherwise.
    """
    tree = xmlutils.try_parse(path)
    if tree is None:
        logging.exception(f"Failed to parse: {path}")
        return False

    root = tree.getroot()

    # Convert legacy root tag
    new_root = ET.Element("TelemFFB_v2")
    for child in list(root):
        new_root.append(child)
    tree._setroot(new_root)
    root = new_root

    # If already converted (profileMappings exist), exit
    if root.find("profileMappings") is not None:
        logging.info('Userconfig is already v2, conversion not needed')
        return False

    # check if there are profile entries, if so this is a new config that has had profiles added but nothing configured as active profile
    p = root.findall('models[name="profile"]')
    if p:
        logging.info('Found profile entries in userconfig is already v2, conversion not needed')
        return False

    # Check if there's anything to convert
    if root.find("models") is None:
        return False

    """
    # Find all user created models ('type' entries) - these models will become 'User Default'
    """
    user_default_list = []
    user_default_models = root.findall('models[name="type"]')
    for user_default in user_default_models:
        model = user_default.findtext('model')
        sim = user_default.findtext('sim')
        if model and sim:
            user_default_list.append((model, sim))

    """
    find all model/sim pairings that exist but don't have a 'type' parent entry.  These will become 'Auto User' profiles
    """
    user_settings_only_list = []
    for entry in root.findall('models'):
        model = entry.findtext('model')
        sim = entry.findtext('sim')
        if not model or not sim:
            continue
        if (model, sim) not in user_default_list and (model, sim) not in user_settings_only_list:
            user_settings_only_list.append((model, sim))

    """
    We now have two lists:
    user_default_models has (model, sim) tuples for aircraft that will become 'user defaults' (created by user)
    user_settings_only_list has (model, sim) tuples for aircraft that will become 'auto user' profiles (settings modified from default aircraft)
    """
    for entry in root.findall('models'):
        model = entry.findtext('model')
        sim = entry.findtext('sim')
        if not model or not sim:
            continue

        existing_profiles = entry.findall("profile")
        if any(p.text in ("User Default", "Auto User") for p in existing_profiles):
            continue  # Already patched

        if (model, sim) in user_default_list:
            ET.SubElement(entry, 'profile').text = "User Default"
        elif (model, sim) in user_settings_only_list:
            ET.SubElement(entry, 'profile').text = "Auto User"
        else:
            logging.warning(f"Unhandled model/sim: {model}/{sim}")

    """
    Each 'Auto User' profile gets a name='profile' entry that indicates a profile.
    User Defaults have their name='type' entries.
    """
    existing_profile_defs = {(e.findtext("model"), e.findtext("sim"))
                             for e in root.findall('models[name="profile"]')}

    for model, sim in user_settings_only_list:
        if (model, sim) in existing_profile_defs:
            continue
        cls = xmlutils.get_class_for_sim_model(sim, model)
        profile_def = ET.SubElement(root, 'models')
        ET.SubElement(profile_def, 'name').text = "profile"
        ET.SubElement(profile_def, 'model').text = model
        ET.SubElement(profile_def, 'value').text = cls
        ET.SubElement(profile_def, 'sim').text = sim
        ET.SubElement(profile_def, 'device').text = "any"
        ET.SubElement(profile_def, 'profile').text = "Auto User"

    """
    Now we build the profileMappings table.
    Each model will be set to its "profile" value (Auto User or User Default) depending on its type
    """
    seen_mappings = set()
    for model, sim in user_default_list + user_settings_only_list:
        if not model or not sim or (model, sim) in seen_mappings:
            continue
        seen_mappings.add((model, sim))

        cls = xmlutils.get_class_for_sim_model(sim, model)
        profile = "User Default" if (model, sim) in user_default_list else "Auto User"

        mapping = ET.SubElement(root, "profileMappings")
        ET.SubElement(mapping, "model").text = model
        ET.SubElement(mapping, "sim").text = sim
        ET.SubElement(mapping, "cls").text = cls
        ET.SubElement(mapping, "active_profile").text = profile

    xmlutils.consolidate_sort_and_write_userconfig(tree)
    logging.info("Conversion complete: userconfig upgraded to v2.")
    return True

def copy_legacy_config_to_new(path):
    # path is the destination for the new v2 config file
    if os.path.exists(path):
        # new config already exists, don't copy
        return

    # get root of path
    user_rootpath = os.path.dirname(path)
    legacy_config_path = os.path.join(user_rootpath, "userconfig.xml")
    if not os.path.exists(legacy_config_path):
        # there is no legacy config to copy over
        return
    shutil.copy(legacy_config_path, path)
    logging.info(f"Copied legacy userconfig.xml to {path}")


def create_empty_userxml_file(path):
    if not os.path.isfile(path):
        # Create an empty XML file with the specified root element
        root = ET.Element("TelemFFB_v2")
        tree = ET.ElementTree(root)
        # Create a backup directory if it doesn't exist
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        tree.write(path)
        logging.info(f"Empty XML file created at {path}")
    else:
        logging.info(f"XML file exists at {path}")


def get_script_path():
    if getattr(sys, 'frozen', False):
        # we are running in a bundle
        script_dir = os.path.dirname(sys.executable)
    else:
        # we are running in a normal Python environment
        script_dir = os.path.dirname(os.path.abspath(__file__))
    return script_dir


def get_resource_path(relative_path, prefer_root=False, force=False):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if getattr(sys, 'frozen', False):
        # we are running in a bundle
        bundle_dir = sys._MEIPASS
        script_dir = os.path.dirname(sys.executable)
    else:
        # we are running in a normal Python environment
        bundle_dir = os.path.dirname(os.path.abspath(__file__))
        bundle_dir = os.path.abspath(os.path.join(bundle_dir, ".."))
        script_dir = bundle_dir

    if prefer_root:
        # if prefer_root is true, look in 'script dir' to find the relative path
        f_path = os.path.join(script_dir, relative_path)
        if os.path.isfile(f_path) or force:
            # if the file exists, return the path
            return f_path
        else:
            logging.debug(
                f"get_resource_path, root_prefer=True.  Did not find {relative_path} relative to script/exe dir.. looking in bundle dir...")
            # fall back to bundle dir if not found it script dir, log warning if still not found
            # note, script dir and bundle dir are same when running from source
            f_path = os.path.join(bundle_dir, relative_path)
            if not os.path.isfile(f_path):
                logging.warning(
                    f"Warning, get_resource_path, root_prefer=True, did not find file in script/exe folder or bundle folder: {f_path}")
            logging.debug(f"get_resource_path, Found {relative_path} located at {f_path}")
            return f_path
    else:
        f_path = os.path.join(bundle_dir, relative_path)
        if not os.path.isfile(f_path):
            logging.warning(f"Warning, get_resource_path did not find file in bundle folder: {f_path}")
        return f_path


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
            str: Device identifier
        """
        dev_info = G.instance_dev_dict.get(pid)
        if dev_info is not None:
            return dev_info.ident
        if G.device_info and G.device_info.product_id == pid:
            return G.device_info.ident
        return G.device_info.ident if G.device_info else "UnknownDevice"


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
            f"  Name: {target_device_ident}\n\n"
            f"Profile settings:\n"
            f"  PID: {cfg_pid:04X}\n"
            f"  Name: {cfg_device_name}\n"
            f"  Serial: {cfg_serial}"
        )
        _show_error_message("Device Mismatch", error_msg, silent, window)
        return False
    
    # Step 4: Validate device identifier matching
    current_device_ident = _get_current_device_ident(pid)
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


class LoggingFilter(logging.Filter):
    def __init__(self, keywords):
        self.keywords = keywords

    def filter(self, record):
        # Check if any of the keywords are present in the log message
        record.device_type = G.device_type
        for keyword in self.keywords:
            if keyword in record.getMessage():
                # If any keyword is found, prevent the message from being logged
                return False
        # If none of the keywords are found, allow the message to be logged
        return True


def load_custom_userconfig(new_path=""):
    print(f"newpath=>{new_path}<")

    if new_path == "":
        options = QFileDialog.Option(0)
        options |= QFileDialog.Option.DontUseNativeDialog  # Optional: makes dialog consistent across platforms

        file_path, _ = QFileDialog.getOpenFileName(
            None,
            "Select File",
            "",
            "All Files (*)",
            options=options
        )

        if file_path == "":
            return

        G.userconfig_rootpath = os.path.basename(file_path)
        G.userconfig_path = file_path
    else:
        G.userconfig_rootpath = os.path.basename(new_path)
        G.userconfig_path = new_path

    xmlutils.update_vars(
        G.device_type,
        _userconfig_path=G.userconfig_path,
        _defaults_path=G.defaults_path,
    )

    # G.settings_mgr.init_ui()

    logging.info(f"Custom Configuration was loaded via debug menu: {G.userconfig_path}")

    if G.master_instance and G.launched_instances:
        G.ipc_instance.send_broadcast_message(f"LOADCONFIG:{G.userconfig_path}")


def upload_vpconf_profile(config_filepath, serial):
    from .namedmutex import NamedMutex

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
        G.main_window.status_container.request_set_active_vpconf.emit(config_filepath)

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

        thread = threading.Thread(target=exec)
        thread.start()

    else:
        logging.error("Unable to find VPforce Configurator installation location")


def format_dict(data, prefix=""):
    output = ""
    for key, value in data.items():
        if isinstance(value, dict):
            output += format_dict(value, prefix + key + ".")
        else:
            output += prefix + key + " = " + str(value) + "\n"
    return output


def get_install_path():
    """ return path where executable or main script is installed"""
    if getattr(sys, 'frozen', False):
        _install_path = os.path.dirname(sys.executable)
    else:
        _install_path = os.path.dirname(os.path.abspath(__file__))
    return _install_path


def get_device_logo(dev_type :str):

    match str.lower(dev_type):
        case 'joystick':
            if G.useDarkMode:
                _device_logo = ':/image/logo_j_dm.png'
            else:
                _device_logo = ':/image/logo_j.png'
        case 'pedals':
            if G.useDarkMode:
                _device_logo = ':/image/logo_p_dm.png'
            else:
                _device_logo = ':/image/logo_p.png'
        case 'collective':
            if G.useDarkMode:
                _device_logo = ':/image/logo_c_dm.png'
            else:
                _device_logo = ':/image/logo_c.png'
        case 'trimwheel':
            if G.useDarkMode:
                _device_logo = ':/image/logo_t_dm.png'
            else:
                _device_logo = ':/image/logo_t.png'
        case _:
            if G.useDarkMode:
                _device_logo = ':/image/logo_j_dm.png'
            else:
                _device_logo = ':/image/logo_j.png'
    return _device_logo


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
        Return error if any, return None if no error occurred
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


def exit_application():
    # Perform any cleanup or save operations here
    G.main_window.save_main_window_geometry()
    QCoreApplication.instance().quit()

def get_legacy_override_file():
    _legacy_override_file = None

    if G.args.overridefile == 'None':
        _install_path = get_install_path()

        # Need to determine if user is using default config.user.ini without passing the override flag:
        if os.path.isfile(os.path.join(_install_path, 'config.user.ini')):
            _legacy_override_file = os.path.join(_install_path, 'config.user.ini')

    else:
        if not os.path.isabs(G.args.overridefile):  # user passed just file name, construct absolute path from script/exe directory
            ovd_path = get_resource_path(G.args.overridefile, prefer_root=True, force=True)
        else:
            ovd_path = G.args.overridefile  # user passed absolute path, use that

        if os.path.isfile(ovd_path):
            _legacy_override_file = ovd_path
        else:
            _legacy_override_file = ovd_path
            logging.warning(f"Override file {G.args.overridefile} passed with -o argument, but can not find the file for auto-conversion")

    return _legacy_override_file

class ChildPopen(subprocess.Popen):
    udp_port : int

def check_launch_instance(dev_type :str, master_port : int) -> subprocess.Popen:
    """Check prerequisites and launch a new telemFFB instance

    :param dev_type: _description_
    :type dev_type: str
    """
    dev_type_cap = dev_type.capitalize()
    if G.system_settings.get(f'autolaunch{dev_type_cap}', False) and G.device_type != dev_type:
        usbpid = G.system_settings.get(f'pid{dev_type_cap}', '2055')

        if not usbpid:
            logging.warning("Device PID unset for device %s, not launching", dev_type)
            return None
        
        usb_vidpid = f"FFFF:{usbpid}"
    
        args = [sys.argv[0], '-D', usb_vidpid, '-t', dev_type, '--child', '--masterport', str(master_port)]
        if sys.argv[0].endswith(".py"): # insert python interpreter if we launch ourselves as a script
            args.insert(0, sys.executable)

        if G.system_settings.get(f'startMin{dev_type_cap}', False):
            args.append('--minimize')
        if G.system_settings.get(f'startHeadless{dev_type_cap}', False):
            args.append('--headless')

        if G.args.darkmode:
            args.append('--darkmode')
        elif G.args.lightmode:
            args.append('--lightmode')

        logging.info("Auto-Launch: starting instance: %s", args)
        proc = ChildPopen(args)
        proc.udp_port = 60000 + int(usbpid)
        G.launched_instances[dev_type] = proc
        return proc


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


def hexdump(src, length=16, sep='.'):
    """Hex dump bytes to ASCII string, padded neatly
    In [107]: x = b'\x01\x02\x03\x04AAAAAAAAAAAAAAAAAAAAAAAAAABBBBBBBBBBBBBBBBBBBBBBBBBB'

    In [108]: print('\n'.join(hexdump(x)))
    00000000  01 02 03 04 41 41 41 41  41 41 41 41 41 41 41 41 |....AAAAAAAAAAAA|
    00000010  41 41 41 41 41 41 41 41  41 41 41 41 41 41 42 42 |AAAAAAAAAAAAAABB|
    00000020  42 42 42 42 42 42 42 42  42 42 42 42 42 42 42 42 |BBBBBBBBBBBBBBBB|
    00000030  42 42 42 42 42 42 42 42                          |BBBBBBBB        |
    """
    FILTER = ''.join([(len(repr(chr(x))) == 3) and chr(x) or sep for x in range(256)])
    lines = []
    for c in range(0, len(src), length):
        chars = src[c: c + length]
        hex_ = ' '.join(['{:02x}'.format(x) for x in chars])
        if len(hex_) > 24:
            hex_ = '{} {}'.format(hex_[:24], hex_[24:])
        printable = ''.join(['{}'.format((x <= 127 and FILTER[x]) or sep) for x in chars])
        lines.append('{0:08x}  {1:{2}s} |{3:{4}s}|'.format(c, hex_, length * 3, printable, length))

    return ("\n".join(lines))

def expocurve(x, k):
    # expo function for + k: y = (1-k)x + k( (1-e^(-ax)) / (1-e^-a))
    #       for negative k: y = (1+k)x + -k(e^(a(x-1))-e^(-a)) / (1-e^(-a))
    #   x = orig pct_max
    #   y = new pct_max
    #   k = expo value 0-1
    #   a = alpha, controls how much to bend the curve.
    #       a=5.5 gives approx 2x increase at 25% orig pct_max with k=0.5, 3x at 25% with k=1
    #               and 1/2x decrease with k=-0.5, 1/3x with k=-1 at 75%
    newvalue = 0
    expo_a = 5.5  # alpha
    if k >= 0:
        newvalue = (1 - k) * x + k * (1 - math.exp(-expo_a * x)) / (1 - math.exp(-expo_a))
    else:
        newvalue = (1 + k) * x + (-k) * (math.exp(expo_a * (x - 1)) - math.exp(-expo_a)) / (1 - math.exp(-expo_a))
    #print(f'expo input:{x} k:{k} output:{newvalue}')
    return newvalue