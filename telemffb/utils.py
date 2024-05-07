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
from datetime import datetime
import math
import os
import random
import re
import shutil
import zipfile
from collections import defaultdict, deque
import threading

import select
import logging
import sys

import winreg
import socket
import time
import zlib
import subprocess
import urllib.request
import json
import ssl
import xml.etree.ElementTree as ET

from PyQt5.QtCore import QCoreApplication, QThread, pyqtSignal, QObject, QSettings
from PyQt5.QtGui import QTextCharFormat, QColor

from PyQt5 import QtCore, QtGui, Qt
from PyQt5.QtWidgets import QFileDialog, QMessageBox
import stransi

import telemffb.globals as G
import telemffb.winpaths as winpaths
import telemffb.xmlutils as xmlutils

def overrides(interface_class):
    """Decorator to ensure that a method in a subclass overrides a method in its superclass or interface."""

    def overrider(method):
        assert method.__name__ in dir(interface_class)
        return method
    return overrider

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
        "ab_rumble_1_1": ["Afterburner Rumble", "afterburner_effect_intensity"],
        "ab_rumble_1_2": ["Afterburner Rumble", "afterburner_effect_intensity"],
        "ab_rumble_2_1": ["Afterburner Rumble", "afterburner_effect_intensity"],
        "ab_rumble_2_2": ["Afterburner Rumble", "afterburner_effect_intensity"],
        "aoa": ["AoA Effect", "aoa_effect_gain"],
        "buffeting": ["AoA\\Stall Buffeting", "buffeting_intensity"],
        "bombs": ["Bomb Release", "weapon_release_intensity"],
        "canopymovement": ["Canopy Motion", "canopy_motion_intensity"],
        "collective_ap_spring": ["Collective AP/Lock Spring Force", "collective_ap_spring_gain"],
        "collective_damper": ["Collective Dampening Force", "collective_dampening_gain"],
        "crit_aoa": ["AoA Reduction Force", "aoa_reduction_max_force"],
        "cm": ["Countermeasure Deployment", "cm_vibration_intensity"],
        "cyclic_spring": ["Cyclic Spring Force", "cyclic_spring_gain"],
        "damage": ["Aircraft Damage Event", "damage_effect_intensity"],
        "damper": ["Damper Override", "damper_force"],
        "decel": ["Decelleration Force", "deceleration_max_force"],
        "dynamic_spring": ["Dynamic Spring Force", ".*_spring_gain"],
        "elev_droop": ["Elevator Droop", "elevator_droop_moment"],
        "etlY": ["ETL Shaking", "etl_effect_intensity"],
        "etlX": ["ETL Shaking", "etl_effect_intensity"],
        "fbw_spring": ["Fly-by-wire Spring Force", "fbw_.*_gain"],
        "flapsmovement": ["Flap Motion", "flaps_motion_intensity"],
        "friction": ["Friction Override", "friction_force"],
        "gearbuffet": ["Gear Drag Buffeting", "gear_buffet_intensity"],
        "gearbuffet2": ["Gear Drag Buffeting", "gear_buffet_intensity"],
        "gearmovement": ["Gear Motion", "gear_motion_intensity"],
        "gearmovement2": ["Gear Motion", "gear_motion_intensity"],
        "gforce": ["G-Force Loading", "gforce_effect_max_intensity"],
        "gunfire": ["Gunfire Rumble", "gun_vibration_intensity"],
        "hit": ["Aircraft Hit Event", ""],
        "je_rumble_1_1": ["Jet Engine Rumble", "jet_engine_rumble_intensity"],
        "je_rumble_1_2": ["Jet Engine Rumble", "jet_engine_rumble_intensity"],
        "je_rumble_2_1": ["Jet Engine Rumble", "jet_engine_rumble_intensity"],
        "je_rumble_2_2": ["Jet Engine Rumble", "jet_engine_rumble_intensity"],
        "il2_buffet": ["Buffeting", "il2_buffeting_factor"],
        "il2_buffet2": ["Buffeting", "il2_buffeting_factor"],
        "inertia": ["Inertia Override", "inertia_force"],
        "nw_shimmy": ["Nosewheel Shimmy", "nosewheel_shimmy_intensity"],
        "overspeedY": ["Overspeed Shake", "overspeed_shake_intensity"],
        "overspeedX": ["Overspeed Shake", "overspeed_shake_intensity"],
        "payload_rel": ["Payload Release", "weapon_release_intensity"],
        "pause_spring": ["Pause/Slew Spring Force", ""],
        "pedal_spring": ["Pedal Spring", "pedal_spring_gain"],
        "pedal_damper": ["Pedal Damper", "pedal_dampening_gain"],
        "prop_rpm0-1": ["Propeller Engine Rumble", "engine_rumble_.*"],
        "prop_rpm0-2": ["Propeller Engine Rumble", "engine_rumble_.*"],
        "prop_rpm1-1": ["Propeller Engine Rumble", "engine_rumble_.*"],
        "prop_rpm1-2": ["Propeller Engine Rumble", "engine_rumble_.*"],
        "rockets": ["Rocket Fire", "il2_weapon_release_intensity"],
        "rotor_rpm0-1": ["Rotor RPM\\Engine Rumble", "heli_engine_rumble_intensity"],
        "rotor_rpm1-1": ["Rotor RPM\\Engine Rumble", "heli_engine_rumble_intensity"],
        "runway0": ["Runway Rumble", "runway_rumble_intensity"],
        "runway1": ["Runway Rumble", "runway_rumble_intensity"],
        "speedbrakebuffet": ["Speedbrake Buffeting", "speedbrake_buffet_intensity"],
        "speedbrakebuffet2": ["Speedbrake Buffeting", "speedbrake_buffet_intensity"],
        "speedbrakemovement": ["Speedbrake Motion", "speedbrake_motion_intensity"],
        "spoilerbuffet1-1": ["Spoiler Buffeting", "spoiler_buffet_intensity"],
        "spoilerbuffet1-2": ["Spoiler Buffeting", "spoiler_buffet_intensity"],
        "spoilerbuffet2-1": ["Spoiler Buffeting", "spoiler_buffet_intensity"],
        "spoilerbuffet2-2": ["Spoiler Buffeting", "spoiler_buffet_intensity"],
        "spoilermovement": ["Spoiler Motion", "spoiler_motion_intensity"],
        "stick_shaker" : ["Stick Shaker","stick_shaker_intensity"],
        "touchdown": ["Touch-down Effect", "touchdown_effect_max_force"],
        "trim_spring": ["Trim Override Spring", ""],
        "control_weight": ["Control Weight", ""],
        "vrs_buffet": ["Vortex Ring State Buffeting", "vrs_effect_intensity"],
        "vrs_buffet2": ["Vortex Ring State Buffeting", "vrs_effect_intensity"],
        "wnd": ["Wind Effect", "wind_effect_max_intensity"],
        "hyd_loss_damper": ["Low Hydraulic Damper", "hydraulic_loss_damper"],
        "hyd_loss_inertia": ["Low Hydraulic Inertia", "hydraulic_loss_inertia"],
        "hyd_loss_friction": ["Low Hydraulic Friction", "hydraulic_loss_friction"],
    }
    @classmethod
    def get_translation(cls, key):
        return cls.effect_dict.get(key, [f"No Lookup: {key}", ''])


class Destroyable:
    def destroy(self):
        raise NotImplementedError


class Vector2D:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __str__(self):
        return f"Vector2D({self.x}, {self.y})"

    def __repr__(self):
        return self.__str__()

    def __add__(self, other):
        return Vector2D(self.x + other.x, self.y + other.y)

    def __sub__(self, other):
        return Vector2D(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar):
        return Vector2D(self.x * scalar, self.y * scalar)

    def __rmul__(self, scalar):
        return self.__mul__(scalar)

    def __truediv__(self, scalar):
        return Vector2D(self.x / scalar, self.y / scalar)

    def magnitude(self):
        return math.sqrt(self.x ** 2 + self.y ** 2)

    def dot(self, other):
        return self.x * other.x + self.y * other.y

    def cross(self, other):
        return self.x * other.y - self.y * other.x

    def to_polar(self):
        r = self.magnitude()
        theta_radians = math.atan2(self.y, self.x)
        return r, theta_radians

    def normalize(self):
        magnitude = self.magnitude()
        if magnitude == 0:
            raise ValueError("Cannot normalize a zero-length vector.")
        return Vector2D(self.x / magnitude, self.y / magnitude)


class Vector:
    def __init__(self, x, y=0.0, z=0.0):
        self.x : float
        self.y : float
        self.z : float
        if isinstance(x, list):
            self.x, self.y, self.z = x
        else:
            self.x = x
            self.y = y
            self.z = z

    def __eq__(self, p):
        return self.x == p.x and self.y == p.y and self.z == p.z

    def __add__(self, p):
        return Vector(self.x + p.x, self.y + p.y, self.z + p.z)

    def __sub__(self, p):
        return Vector(self.x - p.x, self.y - p.y, self.z - p.z)

    def __unm__(self):
        return Vector(-self.x, -self.y, -self.z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __mul__(self, s):
        if isinstance(s, Vector):
            return self.x * s.x + self.y * s.y + self.z * s.z
        elif isinstance(s, (int, float)):
            return Vector(self.x * s, self.y * s, self.z * s)

    def __div__(self, s):
        if isinstance(s, (int, float)):
            return Vector(self.x / s, self.y / s, self.z / s)

    def __concat__(self, p):
        return self.x * p.x + self.y * p.y + self.z * p.z

    def __pow__(self, p):
        return Vector(
            self.y * p.z - self.z * p.y,
            self.z * p.x - self.x * p.z,
            self.x * p.y - self.y * p.x
        )

    def ort(self):
        l = self.length()
        if l > 0:
            return Vector(self.x / l, self.y / l, self.z / l)
        else:
            return self

    def normalize(self):
        l = self.length()
        if l > 0:
            self.x /= l
            self.y /= l
            self.z /= l

    def set(self, xx, yy, zz):
        self.x = xx
        self.y = yy
        self.z = zz

    def translate(self, dx, dy, dz):
        return Vector(self.x + dx, self.y + dy, self.z + dz)

    def __str__(self):
        return f'({self.x},{self.y},{self.z})'

    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def rotZ(self, ang):
        sina = math.sin(ang)
        cosa = math.cos(ang)
        return Vector(self.x * cosa - self.y * sina, self.x * sina + self.y * cosa, self.z)

    def rotX(self, ang):
        sina = math.sin(ang)
        cosa = math.cos(ang)
        return Vector(self.x, self.y * cosa - self.z * sina, self.y * sina + self.z * cosa)

    def rotY(self, ang):
        sina = math.sin(ang)
        cosa = math.cos(ang)
        return Vector(self.z * sina + self.x * cosa, self.y, self.z * cosa - self.x * sina)

    def rotAxis(self, axis, ang):
        ax = axis.ort()
        cosa = math.cos(ang)
        sina = math.sin(ang)
        versa = 1.0 - cosa
        xy = ax.x * ax.y
        yz = ax.y * ax.z
        zx = ax.z * ax.x
        sinx = ax.x * sina
        siny = ax.y * sina
        sinz = ax.z * sina
        m10 = ax.x * ax.x * versa + cosa
        m11 = xy * versa + sinz
        m12 = zx * versa - siny
        m20 = xy * versa - sinz
        m21 = ax.y * ax.y * versa + cosa
        m22 = yz * versa + sinx
        m30 = zx * versa + siny
        m31 = yz * versa - sinx
        m32 = ax.z * ax.z * versa + cosa
        return Vector(
            m10 * self.x + m20 * self.y + m30 * self.z,
            m11 * self.x + m21 * self.y + m31 * self.z,
            m12 * self.x + m22 * self.y + m32 * self.z
        )


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

                with zipfile.ZipFile(zip_path, 'a') as zip_file:
                    log_file_path = os.path.join(directory, filename)
                    zip_file.write(log_file_path, os.path.basename(log_file_path))
                    os.remove(log_file_path)  # Remove the original log file


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

def create_support_bundle(userconfig_rootpath):
    # Get the  system settings
    sys_dict = read_all_system_settings()

    # Prompt the user for the destination and filename for the zip file
    file_dialog = QFileDialog()
    file_dialog.setFileMode(QFileDialog.AnyFile)
    file_dialog.setAcceptMode(QFileDialog.AcceptSave)
    file_dialog.setNameFilter("Zip Files (*.zip)")

    if file_dialog.exec_():
        # Get the selected file path
        zip_file_path = file_dialog.selectedFiles()[0]

        # Create a temporary folder to store files
        temp_folder = "temp_support_bundle"
        os.makedirs(temp_folder, exist_ok=True)

        try:
            # Save userconfig.xml to the temporary folder
            userconfig_path = os.path.join(temp_folder, "userconfig.xml")
            shutil.copy(os.path.join(userconfig_rootpath, "userconfig.xml"), userconfig_path)

            # Save the contents of the 'log' folder to the temporary folder
            log_folder_path = os.path.join(temp_folder, "log")
            shutil.copytree(os.path.join(userconfig_rootpath, "log"), log_folder_path)

            # Save system settings to a temporary .cfg file
            cfg_file_path = os.path.join(temp_folder, "system_settings.cfg")
            with open(cfg_file_path, "w") as cfg_file:
                # Write each key-value pair as 'key=value' on a new line
                for key, value in sys_dict.items():
                    cfg_file.write(f"{key}={value}\n")

            # Create the support zip file
            with zipfile.ZipFile(zip_file_path, 'w') as support_zip:
                # Add userconfig.xml, log folder, and system settings .cfg file to the zip
                support_zip.write(userconfig_path, "userconfig.xml")
                for folder_name, subfolders, filenames in os.walk(log_folder_path):
                    for filename in filenames:
                        file_path = os.path.join(folder_name, filename)
                        arcname = os.path.relpath(file_path, temp_folder)
                        support_zip.write(file_path, os.path.join("log", arcname))
                support_zip.write(cfg_file_path, "system_settings.cfg")

        finally:
            # Clean up the temporary folder
            shutil.rmtree(temp_folder)


def read_all_system_settings():
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
    }

    globl_sys_dict = {
        'ignoreUpdate': False,
        'enableDCS': False,
        'enableMSFS': False,
        'enableXPLANE': False,
        'validateXPLANE': False,
        'pathXPLANE': '',
        'enableIL2': False,
        'validateIL2': True,
        'validateDCS': True,
        'pathIL2': 'C:/Program Files/IL-2 Sturmovik Great Battles',
        'portIL2': 34385,
        'masterInstance': 1,
        'autolaunchMaster': False,
        'autolaunchJoystick': False,
        'autolaunchPedals': False,
        'autolaunchCollective': False,
        'startMinJoystick': False,
        'startMinPedals': False,
        'startMinCollective': False,
        'startHeadlessJoystick': False,
        'startHeadlessPedals': False,
        'startHeadlessCollective': False,
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
        pass

    @overrides(QSettings)
    def setValue(self, key: str, value, instance=None) -> None:
        if instance:
            super().setValue(f"{instance}/{key}", value)
        else:
            super().setValue(key, value)

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


def to_number(v: str):
    """Try to convert string to number
    If unable, return the original string
    """
    orig_v = v
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        v = str(v)
    try:
        # handle boolean strings -> bool return
        lower = v.lower()
        if lower in ["true", "yes", "on", "enable", "enabled"]:
            return True
        if lower in ["false", "no", "off", "disable", "disabled"]:
            return False

        scale = 1
        if v.lower().endswith("%") or v.startswith("%"):  # handle percent strings
            scale = 0.01
            v = v.strip("%")
        if v.lower().endswith("kt"):  # handle unit conversion: kt->ms
            scale = 0.51444
            v = v.strip("kt")
        if v.lower().endswith("kph"):  # handle unit conversion: kph->ms
            scale = 1 / 3.6
            v = v.strip("kph")
        if v.lower().endswith("fpm"):  # handle unit conversion: fpm->ms
            scale = 0.00508
            v = v.strip("fpm")
        if v.lower().endswith("m/s"):  # handle unit conversion: kph->ms
            scale = 1
            v = v.strip("m/s")
        if v.lower().endswith("mph"):  # handle unit conversion: mph->ms
            scale = 0.44704
            v = v.strip("mph")
        if v.lower().endswith("deg"):  # just strip out degrees suffix
            v = v.strip("deg")
        if v.lower().endswith("ms"):  # strip out milliseconds suffix
            v = v.strip("ms")
        if v.lower().endswith("hz"):  # strip out hertz suffix
            v = v.strip("hz")
        if v.lower().endswith("m"):  # strip out m meters suffix
            v = v.strip("m")
        if v.lower().endswith("ft"):  # handle ft->m conversion
            scale = 0.3048
            v = v.strip("ft")
        if v.lower().endswith("in"):  # handle in->m conversion
            scale = 0.0254
            v = v.strip("in")
        if v.lower().endswith("m^2"):  # strip out m meters suffix
            v = v.strip("m^2")
        if v.lower().endswith("ft^2"):  # handle ft^2->m^2 conversion
            scale = 0.092903
            v = v.strip("ft^2")

        if "." in v:
            return round(float(v) * scale, 4)
        else:
            return int(v) * scale

    except ValueError:
        # return v
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
    return sorted((minn, n, maxn))[1]


def clamp_minmax(n, max):
    return clamp(n, -max, max)


def scale(val, src: tuple, dst: tuple, return_round=False, return_int=False):
    """
    Scale the given value from the scale of src to the scale of dst.
    """
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


def non_linear_scaling(x, min_val, max_val, curvature=1):
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

    def dispose(self, name):
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


def analyze_il2_config(path, port=34385, window=None):
    config_data = defaultdict(dict)
    file_path = os.path.join(path, "data\\startup.cfg")
    if not os.path.exists(file_path):
        QMessageBox.warning(window, "TelemFFB IL-2 Config Check",
                            f"Unable to find Il-2 configuration file at: {path}\n\nPlease verify the installed path and update the TelemFFB configuration file")
        return
    current_section = None
    ref_addr = '127.255.255.255'
    ref_addr1 = f'127.255.255.255:{port}'
    ref_decimation = '1'
    ref_enable = 'true'
    ref_port = f'{port}'
    telem_proposed = {}
    motion_proposed = {}
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
    telem_config = None
    motion_config = None
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

    if telem_match and motion_match:
        return
    else:
        telem_message = QMessageBox(parent=window)
        telem_message.setIcon(QMessageBox.Question)
        telem_message.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        telem_message.setWindowTitle("TelemFFB IL-2 Config")

        if not telem_match or not motion_match:
            pop = f"""
            The telemetry and/or motion device configuration in the IL-2 startup.cfg is missing or incorrect and may prohibit TelemFFB from receiving data
    
            Would you like to automatically adjust the configuration per the following?
            """

            if not telem_match or not telem_exists:
                pop = pop + f"""
                Existing \'telemetrydevice\': {telem_config}
                Proposed \'telemetrydevice\': {telem_proposed}
                """

            if not motion_match or not motion_exists:
                pop = pop + f"""
                Existing \'motiondevice\': {motion_config}
                Proposed \'motiondevice\': {motion_proposed}
                """
            pop = pop + "\n\n***** - Please ensure Il-2 is not running before selecting 'Yes' - *****"
        telem_message.setText(pop)
        ans = telem_message.exec()
        if ans == QMessageBox.Yes:
            config_data['telemetrydevice'] = telem_proposed
            config_data['motiondevice'] = motion_proposed
            try:
                write_il2_config(file_path, config_data)
            except Exception as e:
                QMessageBox.warning(window, "Config Update Error",
                                    f"There was an error writing to the Il-2 Config file:\n{e}")
        elif ans == QMessageBox.No:
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

    ans = QMessageBox.No
    if not os.path.exists(dst_path):
        ans = QMessageBox.question(window, "X-Plane Plugin Installer", "X-plane plugin is not installed, install now?\n\nNote: X-Plane must not be running for this operation to succeed")
    else:
        src_crc = calculate_crc(src_path)
        dst_crc = calculate_crc(dst_path)
        if src_crc != dst_crc:
            ans = QMessageBox.question(window, "X-Plane Plugin Installer", "X-plane plugin is out of date, update now?\n\nNote: X-Plane must not be running for this operation to succeed")
        else:
            return True

    if ans == QMessageBox.Yes:
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
                retry = QMessageBox.warning(window, "X-Plane Plugin Error", "There was an error copying the file.  Please ensure X-Plane is not running.\n\nWould you like to re-try?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if retry == QMessageBox.No:
                    tryloop = False
                    return False
    else:
        return False
    return True


def install_export_lua(window):
    saved_games = winpaths.get_path(winpaths.FOLDERID.SavedGames)
    logging.info(f"Found Saved Games directory: {saved_games}")

    for dirname in ["DCS", "DCS.openbeta"]:
        p = os.path.join(saved_games, dirname)
        if not os.path.exists(p):
            logging.info(f"{p} does not exist, ignoring")
            continue

        path = os.path.join(saved_games, dirname, 'Scripts')
        os.makedirs(path, exist_ok=True)
        out_path = os.path.join(path, "TelemFFB.lua")

        logging.info(f"Checking {path}")

        try:
            data = open(os.path.join(path, "Export.lua")).read()
        except Exception:
            data = ""

        local_telemffb = get_resource_path('export/TelemFFB.lua', prefer_root=True)

        def write_script():
            data = open(local_telemffb, "rb").read()
            logging.info(f"Writing to {out_path}")
            open(out_path, "wb").write(data)

        export_installed = "telemffblfs" in data

        if export_installed and os.path.exists(out_path):

            crc_a, crc_b = calculate_checksum(out_path), calculate_checksum(local_telemffb)

            if crc_a != crc_b:
                dia = QMessageBox.question(window, "Contents of TelemFFB.lua export script have changed",
                                           f"Update export script {out_path} ?")
                if dia == QMessageBox.Yes:
                    write_script()
        else:
            dia = QMessageBox.question(window, "Confirm", f"Install export script into {path}?")
            if dia == QMessageBox.Yes:
                if not export_installed:
                    logging.info("Updating export.lua")
                    line = "local telemffblfs=require('lfs');dofile(telemffblfs.writedir()..'Scripts/TelemFFB.lua')"
                    f = open(os.path.join(path, "Export.lua"), "a+")
                    f.write("\n" + line)
                    f.close()
                write_script()


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
            rgb = i.color.hex
            if i.role == stransi.color.ColorRole.BACKGROUND:
                current_format.setBackground(QColor(rgb.hex_code))
            elif i.role ==  stransi.color.ColorRole.FOREGROUND:
                current_format.setForeground(QColor(rgb.hex_code))
        elif isinstance(i, stransi.SetAttribute):
            match i.attribute:
                case stransi.attribute.Attribute.BOLD:
                    current_format.setFontWeight(100)
                case stransi.attribute.Attribute.ITALIC:
                    current_format.setFontItalic(i.is_on())
                case stransi.attribute.Attribute.UNDERLINE:
                    current_format.setFontUnderline(i.is_on())
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
        self.textReceived.connect(self.on_received, Qt.Qt.QueuedConnection)
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

            self.edit.moveCursor(QtGui.QTextCursor.End)
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


def winreg_get(path, key):
    try:
        registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                                      winreg.KEY_READ)
        value, regtype = winreg.QueryValueEx(registry_key, key)
        winreg.CloseKey(registry_key)
        return value
    except WindowsError:
        return None


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
            ctx = ssl._create_unverified_context()

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
                    with urllib.request.urlopen(send_url, context=ctx, ) as req:
                        latest = json.loads(req.read().decode())
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
                    self.version_result_signal.emit('dev', 'dev')
                elif current_version == latest_version:
                    self.version_result_signal.emit("uptodate", "uptodate")
                else:
                    self.version_result_signal.emit("needsupdate", "needsupdate")

        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            self.__class__.workers.remove(self)


def launch_vpconf(serial=None):
    vpconf_path = winreg_get("SOFTWARE\\VPforce\\RhinoFFB", "path")
    # serial = HapticEffect.device.serial

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
        version = "vX.X.X"
        return version

    ver = "UNKNOWN"
    try:
        version = __import__("version")
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


def create_empty_userxml_file(path):
    if not os.path.isfile(path):
        # Create an empty XML file with the specified root element
        root = ET.Element("TelemFFB")
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
            logging.info(
                f"get_resource_path, root_prefer=True.  Did not find {relative_path} relative to script/exe dir.. looking in bundle dir...")
            # fall back to bundle dir if not found it script dir, log warning if still not found
            # note, script dir and bundle dir are same when running from source
            f_path = os.path.join(bundle_dir, relative_path)
            if not os.path.isfile(f_path):
                logging.warning(
                    f"Warning, get_resource_path, root_prefer=True, did not find file in script/exe folder or bundle folder: {f_path}")
            return f_path
    else:
        f_path = os.path.join(bundle_dir, relative_path)
        if not os.path.isfile(f_path):
            logging.warning(f"Warning, get_resource_path did not find file in bundle folder: {f_path}")
        return f_path


def validate_vpconf_profile(file, pid=None, dev_type=None, silent=False, window=None):
    if isinstance(pid, str):
        pid = int(pid)
    try:
        with open(file, 'r') as f:
            config_data = json.load(f)
    except:
        if not silent:
            QMessageBox.warning(window, "Error", f"The VPforce Configurator file you selected appears to be invalid\n\nFile={file}")
        return False
    cfg_pid = config_data.get('config', {}).get('usb_pid', 'unknown')
    if cfg_pid == 'unknown':
        if not silent:
            QMessageBox.warning(window, "Error", f"The VPforce Configurator file you selected appears to be invalid\n\nFile={file}")
        return False
    cfg_pid = int(format(cfg_pid, 'x'))
    cfg_serial = config_data.get('serial_number', 'unknown')
    cfg_device_name = config_data.get('config', {}).get('device_name', 'unknown')
    if cfg_serial == 'unknown':
        return False
    if cfg_pid == pid:
        return True
    else:
        if not silent:
            QMessageBox.warning(window, "Wrong Device", f"The VPforce Configurator file you selected does not match the device you are trying to assign it to\n\nFile={file}\n\nThis instance is currently configuring:\n{dev_type}\npid= {pid}\n\nThe chosen profile is for\npid= {cfg_pid}\nname= {cfg_device_name}\nserial= {cfg_serial}")
        return False


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
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(
            None, "Select File", "", "All Files (*)", options=options
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
    # reinitialize table from new config
    G.settings_mgr.init_ui()

    logging.info(f"Custom Configuration was loaded via debug menu: {G.userconfig_path}")
    if G.master_instance and G.launched_instances:
        G.ipc_instance.send_broadcast_message(f"LOADCONFIG:{G.userconfig_path}")


def set_vpconf_profile(config_filepath, serial):
    vpconf_path = winreg_get("SOFTWARE\\VPforce\\RhinoFFB", "path")
    # serial = HapticEffect.device.serial

    if vpconf_path:
        logging.info(f"Found VPforce Configurator at {vpconf_path}")
        workdir = os.path.dirname(vpconf_path)
        env = {}
        env["PATH"] = os.environ["PATH"]
        if not os.path.isfile(config_filepath):
            logging.error(f"Error loading VPforce Configurator Profile: ({config_filepath}) - The file does not exist! ")
            return

        if not validate_vpconf_profile(config_filepath, G.device_usbpid, G.device_type, silent=True):
            logging.error(f"VPForce Config Error: ({config_filepath}) - The file failed validation!  Check the PID is correct for the device")
            return

        logging.info(f"set_vpconf_profile - Loading vpconf for with: {vpconf_path} -config {config_filepath} -serial {serial}")

        subprocess.call([vpconf_path, "-config", config_filepath, "-serial", serial], cwd=workdir, env=env, shell=True)
        G.current_vpconf_profile = config_filepath
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


def save_main_window_geometry():
    # Capture the mai n window's geometry
    device_type = G.device_type
    cur_index = G.main_window.tab_widget.currentIndex()
    G.main_window.tab_sizes[str(cur_index)]['width'] = G.main_window.width()
    G.main_window.tab_sizes[str(cur_index)]['height'] = G.main_window.height()

    window_dict = {
        'WindowPosition': {
            'x': G.main_window.pos().x(),
            'y': G.main_window.pos().y(),
        },
        'Tab': G.main_window.tab_widget.currentIndex(),
        'TabSizes': G.main_window.tab_sizes
    }

    G.system_settings.setValue(f"{device_type}/WindowData", json.dumps(window_dict))


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
            _device_logo = ':/image/logo_j.png'
        case 'pedals':
            _device_logo = ':/image/logo_p.png'
        case 'collective':
            _device_logo = ':/image/logo_c.png'
        case _:
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


def exit_application():
    # Perform any cleanup or save operations here
    save_main_window_geometry()
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

        logging.info("Auto-Launch: starting instance: %s", args)
        proc = ChildPopen(args)
        proc.udp_port = 60000 + int(usbpid)
        G.launched_instances[dev_type] = proc
        return proc
