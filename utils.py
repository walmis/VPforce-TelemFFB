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

import math
import os
import random
import select
from time import monotonic
import logging
import sys
import winpaths
import winreg
import socket
import math
import time
import random
import time
import zlib
import subprocess
import urllib.request
import json
import ssl
import io


class Smoother:
    def __init__(self, window_size=5):
        self.value_dict = {}

    def get_average (self, key, value, window_size=10):
        if key not in self.value_dict:
            self.value_dict[key] = []
        self.value_dict[key].append(value)
        if len(self.value_dict[key]) > window_size:
            self.value_dict[key].pop(0)

        values = self.value_dict.get(key, [])
        if not values:
            return 0
        return sum(values) / len(values)


class EffectTranslator:
    def __init__(self):
        self.effect_dict = {
            "ab_rumble_1_1" : "Afterburner Rumble",
            "ab_rumble_1_2" : "Afterburner Rumble",
            "ab_rumble_2_1" : "Afterburner Rumble",
            "ab_rumble_2_2" : "Afterburner Rumble",
            "aoa" : "AoA Effect",
            "buffeting" : "AoA\\Stall Buffeting",
            "canopymovement" : "Canopy Motion",
            "crit_aoa" : "AoA Reduction Force",
            "cm" : "Countermeasure Deployment",
            "decel" : "Decelration Force",
            "etlY" : "ETL Shaking",
            "etlX" : "ETL Shaking",
            "flapsmovement" : "Flap Motion",
            "gearbuffet" : "Gear Drag Buffeting",
            "gearbuffet2" : "Gear Drag Buffeting",
            "gearmovement" : "Gear Motion",
            "gearmovement2" : "Gear Motion",
            "gforce" : "G-Force Loading",
            "gunfire" : "Gunfire Rumble",
            "je_rumble_1_1" : "Jet Engine Rumble",
            "je_rumble_1_2" : "Jet Engine Rumble",
            "je_rumble_2_1" : "Jet Engine Rumble",
            "je_rumble_2_2" : "Jet Engine Rumble",
            "nw_shimmy" : "Nosewheel Shimmy",
            "payload_rel" :"Payload Release",
            "pedal_spring" : "Pedal Spring",
            "prop_rpm0-1" : "Propeller Engine Rumble",
            "prop_rpm0-2" : "Propeller Engine Rumble",
            "prop_rpm1-1" : "Propeller Engine Rumble",
            "prop_rpm1-2" : "Propeller Engine Rumble",
            "rotor_rpm0-1" : "Rotor RPM\\Engine Rumble",
            "rotor_rpm1-1" : "Rotor RPM\\Engine Rumble",
            "runway0" : "Runway Rumlble",
            "runway1" : "Runway Rumlble",
            "speedbrakebuffet": "Speedbrake Buffeting",
            "speedbrakebuffet2": "Speedbrake Buffeting",
            "speedbrakemovement" : "Speedbrake Motion",
            "spoilerbuffet1-1" : "Spoiler Buffeting",
            "spoilerbuffet1-2" : "Spoiler Buffeting",
            "spoilerbuffet2-1" : "Spoiler Buffeting",
            "spoilerbuffet2-2" : "Spoiler Buffeting",
            "spoilermovement" : "Spoiler Motion",
            "trim_spring" : "Trim Override Spring",
            "wnd" : "Wind Effect"
        }

    def get_translation(self, key):
        return self.effect_dict.get(key, "no_lookup")
class Destroyable:
    def destroy():
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
    def __init__(self, x, y=None, z=None):
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

def mix(a, b, val):
    return a*(1-val) + b*(val)

def to_number(v : str):
    """Try to convert string to number
    If unable, return the original string
    """
    try:
        # handle boolean strings -> bool return
        lower = v.lower()
        if lower in ["true", "yes"]:
            return True
        if lower in ["false", "no"]:
            return False 

        scale = 1
        if v.endswith("%"): # handle percent strings
            scale = 0.01
            v = v.strip("%")
        if v.endswith("kt"): # handle unit conversion: kt->ms
            scale = 0.51444
            v = v.strip("kt")
        if v.endswith("kph"): # handle unit conversion: kph->ms
            scale = 1/3.6
            v = v.strip("kph")
        if v.endswith("mph"): # handle unit conversion: mph->ms
            scale = 0.44704
            v = v.strip("mph")
        if v.endswith("deg"): # just strip out degrees suffix
            v = v.strip("deg")
        if v.endswith("ms"): # strip out milliseconds suffix
            v = v.strip("ms")

        if "." in v:
            return float(v) * scale
        else:
            return int(v) * scale
        
    except ValueError:
        return v
    
def sanitize_dict(d):
    out = {}
    for k,v in d.items():
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
    r,_,_ = select.select([s], [],[], 0)
    return s in r

def clamp(n, minn, maxn):
    return sorted((minn, n, maxn))[1]

def clamp_minmax(n, max):
    return clamp(n, -max, max)


def scale(val, src : tuple, dst : tuple):
    """
    Scale the given value from the scale of src to the scale of dst.
    """   
    return (val - src[0]) * (dst[1] - dst[0]) / (src[1] - src[0]) + dst[0]


def scale_clamp(val, src : tuple, dst : tuple):
    """
    Scale the given value from the scale of src to the scale of dst. 
    and clamp the result to dst
    """   
    v = scale(val, src, dst)
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
    return sum(l)/float(len(l))

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
        if dt > 1: self.x_filt = x # initialize filter
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
            self.last_input = x # initialize filter

        self.last_update = now
        alpha = self.RC / (self.RC + dt)

        self.value = alpha * (self.value + x - self.last_input)
        self.last_input = x
        return self.value
    
class Derivative:
    def __init__(self, filter_hz=None) -> None:
        self.prev_update = 0
        self.prev_value = 0
        self.value = 0
        self.lpf = None
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


class DirectionModulator:
    pass

class RandomDirectionModulator(DirectionModulator):
    def __init__(self, period = 0.1, *args, **kwargs):
        self.prev_upd = time.perf_counter()
        self.value = 0
        self.period = period

    def update(self):
        now = time.perf_counter()
        #dt = now - self.prev_upd
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
            v = self.cls(*args, **kwargs, name=name)
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
        for k in self.dict.keys():
            v = self.dict[k]
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

    def configure(self, address:str):
        address = address.split(":")
        address[1] = int(address[1])
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect(tuple(address))

    def sendTelemetry(self, name, value):
        if self.sock:
            now = time.time() * 1000

            if type(value) == list:
                msg = "\n".join([f"{name}_{i}:{now}:{value[i]}" for i in range(len(value))])
            else:
                msg = f"{name}:{now}:{value}"
            self.sock.send(msg.encode())

teleplot = Teleplot()

if __name__ == "__main__":
    pass

def dot(m1, m2):
    return [
        [sum(x * y for x, y in zip(m1_r, m2_c)) for m2_c in zip(*m2)] for m1_r in m1
    ]

def transpose(m):
    return [[m[j][i] for j in range(len(m))] for i in range(len(m[0]))]

def to_body_vector(yaw, pitch, roll, world_coordinates):
    # Pre-compute the sine and cosine of the Euler angles
    c_roll = math.cos(roll)
    s_roll = math.sin(roll)
    c_pitch = math.cos(pitch)
    s_pitch = math.sin(pitch)
    c_yaw = math.cos(yaw)
    s_yaw = math.sin(yaw)

    # Create the rotation matrix using the pre-computed sine and cosine values
    R_x = [[1, 0, 0],
        [0, c_roll, -s_roll],
        [0, s_roll, c_roll]]

    R_y = [[c_pitch, 0, s_pitch],
        [0, 1, 0],
        [-s_pitch, 0, c_pitch]]

    R_z = [[c_yaw, -s_yaw, 0],
        [s_yaw, c_yaw, 0],
        [0, 0, 1]]

    # DCS Main axes:
    # x is directed to the north
    # z is directed to the east
    # y is directed up

    R = dot(R_z, dot(R_y, R_x))

    # Transform the world coordinates to body coordinates
    body_coordinates = dot(R, [[x] for x in world_coordinates])

    return [x[0] for x in body_coordinates]


from PyQt5.QtWidgets import QMessageBox

def install_export_lua():
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
        except:
            data = ""

        local_telemffb = os.path.join(os.path.dirname(__file__), "export", "TelemFFB.lua")
        def write_script():
            data = open(local_telemffb, "rb").read()
            logging.info(f"Writing to {out_path}")
            open(out_path, "wb").write(data)

        export_installed = "telemffblfs" in data

        if export_installed and os.path.exists(out_path):
            # if os.path.getmtime(out_path) < os.path.getmtime(local_telemffb):
            # Use file checksum rather than timestamp to determine if contents have changed - useful when changing installed versions
            crc_a, crc_b = calculate_checksum(out_path), calculate_checksum(local_telemffb)
            #logging.info(f"local path: {local_telemffb}, remote {out_path}")
            #logging.info(f"crc_a {crc_a}, crc_b {crc_b}")
            if crc_a != crc_b:
                dia = QMessageBox.question(None, "Contents of TelemFFB.lua export script have changed", f"Update export script {out_path} ?")
                if dia == QMessageBox.StandardButton.Yes:
                    write_script()
        else:
            dia = QMessageBox.question(None, "Confirm", f"Install export script into {path}?")
            if dia == QMessageBox.StandardButton.Yes:
                if not export_installed:
                    logging.info("Updating export.lua")
                    line = "local telemffblfs=require('lfs');dofile(telemffblfs.writedir()..'Scripts/TelemFFB.lua')"
                    f = open(os.path.join(path, "Export.lua"), "a+")
                    f.write("\n" + line)
                    f.close()
                write_script()

from PyQt5 import QtCore, QtGui, Qt

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

    def on_received(self, m):
        try:
            if self.color:
                tc = self.edit.textColor()
                self.edit.setTextColor(self.color)

            self.edit.moveCursor(QtGui.QTextCursor.End)
            self.edit.insertPlainText( m )

            if self.color:
                self.edit.setTextColor(tc)
        except: pass

    def write(self, m):
        try:
            self.textReceived.emit(m)
        except: pass
        if self.out:
            self.out.write(m)

    def flush(self): pass


def winreg_get(path, key):
    try:
        registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                                       winreg.KEY_READ)
        value, regtype = winreg.QueryValueEx(registry_key, key)
        winreg.CloseKey(registry_key)
        return value
    except WindowsError:
        return None

def get_version():
    ver = "UNKNOWN"
    try:
        import version
        ver = version.VERSION
        return ver
    except: pass

    try:
        ver = subprocess.check_output(['git', 'describe', '--always', '--abbrev=8', '--dirty']).decode('ascii').strip()
        ver = f"local-{ver}"
    except: pass
    return ver

def fetch_latest_version():

    ctx = ssl._create_unverified_context()

    current_version = get_version()
    latest_version = None
    latest_url = None
    url = "https://vpforcecontrols.com/downloads/TelemFFB/"
    file = "latest.json"
    send_url = url + file

    try:
        with urllib.request.urlopen(send_url, context=ctx) as req:
            latest = json.loads(req.read().decode())
            latest_version = latest["version"]
            latest_url = url + latest["filename"]
    except:
        logging.exception(f"Error checking latest version status: {url}")
 
    if current_version != latest_version and latest_version is not None and latest_url is not None:
        logging.debug(f"Current version: {current_version} | Latest version: {latest_version}")
        return latest_version, latest_url
    elif current_version == latest_version:
        return False
    else:
        return None
    
def self_update(zip_uri):
    r = urllib.request.urlopen(zip_uri, context=ssl._create_unverified_context())
    r.read()



if __name__ == "__main__":
    #test install
    #from PyQt5.QtWidgets import QApplication
    #app = QApplication(sys.argv)
    #install_export_lua()
    uri = "https://vpforcecontrols.com/downloads/TelemFFB/VPforce-TelemFFB-wip-2e79e046.zip"
    self_update(uri)