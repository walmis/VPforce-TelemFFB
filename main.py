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

import json
import logging
import sys
import time
sys.path.insert(0, '') 

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout,
)

import re
  
import argparse
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QMainWindow, QVBoxLayout,QMessageBox
from PyQt5.QtCore import QObject, pyqtSignal, Qt
from PyQt5.QtGui import QFont

from time import monotonic
import socket
import threading
import aircrafts_dcs
import aircrafts_msfs
import utils

import traceback
import os
from ffb_rhino import HapticEffect
from configobj import ConfigObj

from sc_manager import SimConnectManager

config : ConfigObj = None


parser = argparse.ArgumentParser(description='Send telemetry data over USB')

# Add destination telemetry address argument
parser.add_argument('--teleplot', type=str, metavar="IP:PORT", default=None,
                    help='Destination IP:port address for teleplot.fr telemetry plotting service')

parser.add_argument('-p', '--plot', type=str, nargs='+',
                    help='Telemetry item names to send to teleplot, separated by spaces')

parser.add_argument('-D', '--device', type=str, help='Rhino device USB VID:PID', default="ffff:2055")
parser.add_argument('-r', '--reset', help='Reset all FFB effects', action='store_true')

args = parser.parse_args()

if args.teleplot:
    logging.info(f"Using {args.teleplot} for plotting")
    utils.teleplot.configure(args.teleplot)

def format_dict(data, prefix=""):
    output = ""
    for key, value in data.items():
        if isinstance(value, dict):
            output += format_dict(value, prefix + key + ".")
        else:
            output += prefix + key + " = " + str(value) + "\n"
    return output

def reload_config():
    global config
    # reload config
    config.reload()

class LogWindow(QtWidgets.QDialog, QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Log Console")
        self.resize(800, 500)

        self.widget = QtWidgets.QPlainTextEdit(parent)
        self.widget.setReadOnly(True)
        self.widget.setFont(QFont("Courier New"))

        layout = QtWidgets.QVBoxLayout()
        # Add the new logging box widget to the layout
        layout.addWidget(self.widget)
        self.setLayout(layout)
		

class TelemManager(QObject, threading.Thread):
    telemetryReceived = pyqtSignal(object)

    currentAircraft : aircrafts_dcs.Aircraft = None
    currentAircraftName : str = None
    timedOut : bool = True
    lastFrameTime : float
    numFrames : int = 0

    def __init__(self) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self)

        self.daemon = True
        
    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)

        s.settimeout(0.1)
        port = 34380
        s.bind(("", port))
        logging.info(f"Listening on UDP :{port}")

        while True:
            try:
                data = s.recvfrom(4096)
                while utils.sock_readable(s):
                    data = s.recvfrom(4096) # get last frame in OS buffer, to minimize latency
            except ConnectionResetError:
                continue
            except socket.timeout:
                if self.currentAircraft and not self.timedOut:
                    self.currentAircraft.on_timeout()
                self.timedOut = True
                continue

            self.timedOut = False

            # get UDP sender
            sender = data[1]
          
            #print(sender)
            self.lastFrameTime = monotonic()
            data = data[0].decode("utf-8").split(";")
            telem_data = {}

            if data[0] == "DISCONNECT":
                logging.info("Telemetry disconnected")
                self.currentAircraftName = None

            for i in data:
                try:
                    section,conf = i.split("=")
                    values = conf.split("~")
                    telem_data[section] = [utils.to_number(v) for v in values] if len(values)>1 else utils.to_number(conf)
                except Exception as e:
                    print(e)

            try:
                telem_data["MechInfo"] = json.loads(telem_data["MechInfo"])
            except: pass

            #print(items)
            aircraft_name = telem_data.get("N")
            data_source = telem_data.get("src", None)
            if data_source == "MSFS2020":
                module = aircrafts_msfs
            else:
                module = aircrafts_dcs

            if aircraft_name and aircraft_name != self.currentAircraftName:
                reload_config()
                #load [default] values
                defaults = utils.sanitize_dict(config["default"])

                if self.currentAircraft is None or aircraft_name != self.currentAircraftName:
                    cls = None
                    params = {}
                    # find matching aircraft in config
                    for section,conf in config.items():
                        if re.match(section, aircraft_name):
                            conf = utils.sanitize_dict(conf)
                            logging.info(f"Found aircraft {aircraft_name} in config")
                            cls = getattr(module, conf["type"], None)
                            params = defaults
                            params.update(conf)
                            if not cls:
                                logging.error(f"No such class {conf['type']}")

                    if not cls:
                        logging.warning(f"Aircraft definition not found, using default class for {aircraft_name}")
                        cls = module.Aircraft
                        params = defaults

                    logging.info(f"Creating handler for {aircraft_name}: {cls}")
                    #instantiate new aircraft handler
                    self.currentAircraft = cls(aircraft_name, **params)

                self.currentAircraftName = aircraft_name

            if self.currentAircraft:
                try:
                    _tm = time.perf_counter()
                    commands = self.currentAircraft.on_telemetry(telem_data)
                    telem_data["perf"] = f"{(time.perf_counter() - _tm)*1000:.3f}ms"
                    if commands:
                        #send command back
                        s.sendto(bytes(commands, "utf-8"), sender)
                except:
                    traceback.print_exc()

            if args.plot:
                for item in args.plot:
                    if item in telem_data:
                        utils.teleplot.sendTelemetry(item, telem_data[item])
            
            self.telemetryReceived.emit(telem_data)


class SimConnectSock(SimConnectManager):
    def __init__(self):
        super().__init__()
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def fmt(self, val):
        if isinstance(val, list):
            return "~".join([str(x) for x in val])
        return val

    def emit_packet(self, data):
        data["src"] = "MSFS2020"
        packet = bytes(";".join([f"{k}={self.fmt(v)}" for k,v in data.items()]), "utf-8")
        self.s.sendto(packet, ("127.0.0.1", 34380))



# Subclass QMainWindow to customize your application's main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("TelemFFB")

        label = QLabel("Telemetry Data")

        #self.setFixedSize(QSize(400, 300))

        layout = QVBoxLayout()
        layout.addWidget(label)

        self.lbl_telem_data = QLabel("Waiting for data...")
        self.lbl_telem_data.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_telem_data)

        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)

        # Set the central widget of the Window.
        #self.setCentralWidget(label)
    
    def closeEvent(self, event) -> None:
        QApplication.exit()

    def update_telemetry(self, data : dict):
        try:
            items = ""
            for k,v in data.items():
                if k == "MechInfo":
                    v = format_dict(v, "MechInfo.")
                    items += f"{v}"
                else:
                    if type(v) == float:
                        items += f"{k}: {v:.3f}\n"
                    else:
                        if isinstance(v, list):
                            v = "[" + ", ".join([f"{x:.3f}" if not isinstance(x, str) else x for x in v]) + "]"
                        items += f"{k}: {v}\n"
                
            self.lbl_telem_data.setText(items)
        except Exception as e:
            traceback.print_exc()



def main():
    app = QApplication(sys.argv)
    d = LogWindow()
    d.show()

    sys.stdout = utils.OutLog(d.widget, sys.stdout)
    sys.stderr = utils.OutLog(d.widget, sys.stderr)

    logging.getLogger().handlers[0].setStream(sys.stdout)
    logging.info("TelemFFB Starting")
    
    # check and install/update export lua script
    utils.install_export_lua()
	
    vid_pid = [int(x, 16) for x in args.device.split(":")]
    try:
        dev = HapticEffect.open(vid_pid[0], vid_pid[1]) # try to open RHINO
        if args.reset:
            dev.resetEffects()
    except:
        QMessageBox.warning(None, "Cannot connect to Rhino", f"Unable to open Rhino HID at {args.device}")
        return
    

    config_path = os.path.join(os.path.dirname(__file__), "config.ini")

    try:
        global config
        config = ConfigObj(config_path)
        logging.info(f"Using Config: {config_path}")
    except: 
        logging.exception(f"Cannot load config {config_path}")


    window = MainWindow()
    window.show()

    manager = TelemManager()
    manager.start()

    manager.telemetryReceived.connect(window.update_telemetry)

    sc = SimConnectSock()
    sc.start()


    app.exec_()

if __name__ == "__main__":
    main()
