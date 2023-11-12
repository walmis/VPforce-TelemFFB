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
import re
from collections import defaultdict, deque

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
    def __init__(self):
        self.effect_dict = {
            "ab_rumble_1_1" : "Afterburner Rumble",
            "ab_rumble_1_2" : "Afterburner Rumble",
            "ab_rumble_2_1" : "Afterburner Rumble",
            "ab_rumble_2_2" : "Afterburner Rumble",
            "aoa" : "AoA Effect",
            "buffeting" : "AoA\\Stall Buffeting",
            "bombs": "Bomb Release",
            "canopymovement" : "Canopy Motion",
            "collective_ap_spring" : "Collective AP/Lock Spring Force",
            "collective_damper" : "Collective Dampening Force",
            "crit_aoa" : "AoA Reduction Force",
            "cm" : "Countermeasure Deployment",
            "cyclic_spring" : "Cyclic Spring Force",
            "damage" : "Aircraft Damage Event",
            "decel" : "Decelration Force",
            "dynamic_spring" : "Dynamic Spring Force",
            "elev_droop": "Elevator Droop",
            "etlY" : "ETL Shaking",
            "etlX" : "ETL Shaking",
            "fbw_spring" : "Fly-by-wire Spring Force",
            "flapsmovement" : "Flap Motion",
            "gearbuffet" : "Gear Drag Buffeting",
            "gearbuffet2" : "Gear Drag Buffeting",
            "gearmovement" : "Gear Motion",
            "gearmovement2" : "Gear Motion",
            "gforce" : "G-Force Loading",
            "gunfire" : "Gunfire Rumble",
            "hit" : "Aircraft Hit Event",
            "je_rumble_1_1" : "Jet Engine Rumble",
            "je_rumble_1_2" : "Jet Engine Rumble",
            "je_rumble_2_1" : "Jet Engine Rumble",
            "je_rumble_2_2" : "Jet Engine Rumble",
            "il2_buffet": "Buffeting",
            "inertia" : "Inertia",
            "nw_shimmy" : "Nosewheel Shimmy",
            "payload_rel" :"Payload Release",
            "pause_spring" :"Pause/Slew Spring Force",
            "pedal_spring" : "Pedal Spring",
            "prop_rpm0-1" : "Propeller Engine Rumble",
            "prop_rpm0-2" : "Propeller Engine Rumble",
            "prop_rpm1-1" : "Propeller Engine Rumble",
            "prop_rpm1-2" : "Propeller Engine Rumble",
            "rockets" : "Rocket Fire",
            "rotor_rpm0-1" : "Rotor RPM\\Engine Rumble",
            "rotor_rpm1-1" : "Rotor RPM\\Engine Rumble",
            "runway0" : "Runway Rumble",
            "runway1" : "Runway Rumble",
            "speedbrakebuffet": "Speedbrake Buffeting",
            "speedbrakebuffet2": "Speedbrake Buffeting",
            "speedbrakemovement" : "Speedbrake Motion",
            "spoilerbuffet1-1" : "Spoiler Buffeting",
            "spoilerbuffet1-2" : "Spoiler Buffeting",
            "spoilerbuffet2-1" : "Spoiler Buffeting",
            "spoilerbuffet2-2" : "Spoiler Buffeting",
            "spoilermovement" : "Spoiler Motion",
            "trim_spring" : "Trim Override Spring",
            "vrs_buffet" : "Vortex Ring State Buffeting",
            "wnd" : "Wind Effect"
        }

    def get_translation(self, key):
        return self.effect_dict.get(key, f"No Lookup: {key}")
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
        if lower in ["true", "yes", "on", "enable", "enabled"]:
            return True
        if lower in ["false", "no", "off", "disable", "disabled"]:
            return False

        scale = 1
        if v.lower().endswith("%") or v.startswith("%"): # handle percent strings
            scale = 0.01
            v = v.strip("%")
        if v.lower().endswith("kt"): # handle unit conversion: kt->ms
            scale = 0.51444
            v = v.strip("kt")
        if v.lower().endswith("kph"): # handle unit conversion: kph->ms
            scale = 1/3.6
            v = v.strip("kph")
        if v.lower().endswith("mph"): # handle unit conversion: mph->ms
            scale = 0.44704
            v = v.strip("mph")
        if v.lower().endswith("deg"): # just strip out degrees suffix
            v = v.strip("deg")
        if v.lower().endswith("ms"): # strip out milliseconds suffix
            v = v.strip("ms")
        if v.lower().endswith("hz"): # strip out hertz suffix
            v = v.strip("hz")

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
    r,_,_ = select.select([s], [],[], 0)
    return s in r

def clamp(n, minn, maxn):
    return sorted((minn, n, maxn))[1]

def clamp_minmax(n, max):
    return clamp(n, -max, max)


def scale(val, src : tuple, dst : tuple, return_round=False, return_int=False):
    """
    Scale the given value from the scale of src to the scale of dst.
    """   
    result =  (val - src[0]) * (dst[1] - dst[0]) / (src[1] - src[0]) + dst[0]
    if return_round:
        return round(result)
    elif return_int:
        return int(result)
    else:
        return result



def scale_clamp(val, src : tuple, dst : tuple,return_round=False, return_int=False ):
    """
    Scale the given value from the scale of src to the scale of dst. 
    and clamp the result to dst
    """   
    v = scale(val, src, dst,return_round=return_round, return_int=return_int)
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

from PyQt5.QtWidgets import QMessageBox


def analyze_il2_config(path, port=34385):

    config_data = defaultdict(dict)
    file_path = os.path.join(path, "data\\startup.cfg")
    if not os.path.exists(file_path):
        QMessageBox.warning(None, "TelemFFB IL-2 Config Check", f"Unable to find Il-2 configuration file at: {path}\n\nPlease verify the installed path and update the TelemFFB configuration file")
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
    with open(file_path, 'r') as config_file:
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
    if not "telemetrydevice" in config_data:
        #no telemetry config exists in current config, so add our own canned config
        telem_proposed = telemetry_reference
    else:
        #there is an existing telemetry config
        telem_match = 1
        telem_exists = 1
        ignore_port = False
        telem_config = config_data["telemetrydevice"]
        telem_proposed = {}
        for k, v in telem_config.items(): # strip out any quotes
            telem_proposed[k] = v.strip("\'\"")
            telem_config[k] = v.strip("\'\"")

        for k, v in telem_proposed.items():    # see if it matches our reference
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
                        #we must set decimation to 1 for proper effect behavior
                        telem_proposed = insert_dict_item(telem_proposed, 'decimation', f'1', 'enable', before=True)
                        telem_match = 0
                if k == 'enable':
                    if telem_proposed[k] != ref_enable:
                        # enable must be true
                        telem_proposed = insert_dict_item(telem_proposed, 'enable', f'true', 'port', before=True)
                        telem_match = 0
    motion_match = 0
    motion_exists = 0
    if not "motiondevice" in config_data:
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
                            motion_proposed = insert_dict_item(motion_proposed, 'addr1', ref_addr1, 'addr', before=False)
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
        telem_message = QMessageBox()
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
                QMessageBox.warning(None, "Config Update Error", f"There was an error writing to the Il-2 Config file:\n{e}")
        elif ans == QMessageBox.No:
            print(f"Answer: NO")

        # return config_data, telem_match, motion_match

def write_il2_config(file_path, config_data):
    with open(file_path, 'w') as config_file:
        for section, options in config_data.items():
            config_file.write(f"[KEY = {section}]\n")
            for key, value in options.items():
                if key == 'addr' or key == 'addr1':
                    value = value.strip("\'\"")
                    config_file.write(f"\t{key} = \"{value}\"\n")
                else:
                    config_file.write(f"\t{key} = {value}\n")
            config_file.write("[END]\n\n")

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