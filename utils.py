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



def to_number(v):
    """Try to convert string to number
    If unable, return the original string
    """
    try:
        if "." in v:
            return float(v)
        else:
            return int(v)
    except ValueError:
        return v
        

def sock_readable(s) -> bool:
    r,_,_ = select.select([s], [],[], 0)
    return s in r

def clamp(n, minn, maxn):
    return sorted((minn, n, maxn))[1]

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

def pressure_from_altitude(altitude_m):
    """Calculate pressure at specified altitude

    Args:
        altitude_m (float): meters

    Returns:
        float: Pressure in kpa
    """
    return 101.3 * ((288 - 0.0065 * altitude_m) / 288) ** 5.256


class LowPassFilter:
    def __init__(self, cutoff_freq_hz, init_val=0.0):
        self.cutoff_freq_hz = cutoff_freq_hz
        self.alpha = 0.0
        self.x_filt = init_val
        self.last_update = monotonic()

    def update(self, x):
        now = monotonic()
        dt = now - self.last_update
        if dt > 1: self.x_filt = x # initialize filter
        self.last_update = now
        self.alpha = dt / (1.0 / self.cutoff_freq_hz + dt)
        self.x_filt = self.alpha * x + (1.0 - self.alpha) * self.x_filt
        return self.x_filt

class HighPassFilter:
    def __init__(self, cutoff_freq_hz, init_val=0.0):
        self.RC = 1.0 / (2 * math.pi * cutoff_freq_hz)
        self.value = 0
        self.last_update = 0
        self.last_input = init_val
        self.value = init_val
        
    def update(self, x):
        now = monotonic()
        dt = now - self.last_update
        if dt > 1: 
            self.last_input = x # initialize filter

        self.last_update = now
        alpha = self.RC / (self.RC + dt)

        self.value = alpha * (self.value + x - self.last_input)
        self.last_input = x
        return self.value

class DirectionModulator:
    pass

class RandomDirectionModulator(DirectionModulator):
    def __init__(self, period = 10):
        self.prev_upd = monotonic()
        self.value = 0
        self.period = period

    def update(self):
        now = monotonic()
        #dt = now - self.prev_upd
        if now - self.prev_upd > self.period/1000:
            self.prev_upd = now
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
            self.dict[name] = v
        return v

    def remove(self, name):
        if name in self.dict:
            del self.dict[name]
    
    def __contains__(self, name):
        return name in self.dict

    def __getitem__(self, name):
        return self.get(name)

    def __iter__(self):
        return self.dict.__iter__()

    def __delitem__(self, name):
        del self.dict[name]

    def clear(self):
        self.dict.clear()

    def values(self):
        return self.dict.values()

    def dispose(self, name):
        if name in self.dict:
            del self.dict[name]

import socket
import math
import time

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

        local_telemffb = os.path.dirname(__file__) + "/export/TelemFFB.lua"
        def write_script():
            data = open(local_telemffb).read()
            logging.info(f"Writing to {out_path}")
            open(out_path, "w").write(data)

        export_installed = "telemffblfs" in data

        if export_installed and os.path.exists(out_path):
            if os.path.getmtime(out_path) < os.path.getmtime(local_telemffb):
                dia = QMessageBox.question(None, "Confirm", f"Update export script {out_path} ?")
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
        if self.color:
            tc = self.edit.textColor()
            self.edit.setTextColor(self.color)

        self.edit.moveCursor(QtGui.QTextCursor.End)
        self.edit.insertPlainText( m )

        if self.color:
            self.edit.setTextColor(tc)

        if self.out:
            self.out.write(m)

    def write(self, m):
        self.textReceived.emit(m)

    def flush(self): pass


if __name__ == "__main__":
    #test install
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    install_export_lua()
