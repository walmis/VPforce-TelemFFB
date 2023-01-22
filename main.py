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
import argparse

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QMainWindow, QVBoxLayout
from PyQt5.QtCore import QObject, pyqtSignal, Qt

from time import monotonic
import socket
import threading
import utils
import aircrafts
import traceback
from ffb_rhino import HapticEffect

parser = argparse.ArgumentParser(description='Send telemetry data over USB')

# Add destination telemetry address argument
parser.add_argument('--teleplot', type=str, metavar="IP:PORT", default="127.0.0.1:47269",
                    help='Destination IP:port address for teleplot.fr telemetry plotting service')

parser.add_argument('-p', '--plot', type=str, nargs='+',
                    help='Telemetry item names to send to teleplot, separated by spaces')

parser.add_argument('-D', '--device', type=str, help='Rhino device USB VID:PID', default="ffff:2055")

args = parser.parse_args()

vid_pid = [int(x, 16) for x in args.device.split(":")]
HapticEffect.open(vid_pid[0], vid_pid[1]) # try to open RHINO

# check and install/update export lua script
utils.install_export_lua()

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


class TelemManager(QObject, threading.Thread):
    telemetryReceived = pyqtSignal(object)

    currentAircraft : aircrafts.Aircraft = None
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
        port = 29380
        s.bind(("", port))
        logging.info(f"Listening on UDP :{port}")

        while True:
            try:
                data = s.recvfrom(4096)
                while utils.sock_readable(s):
                    data = s.recvfrom(4096) # get last frame in OS buffer, to minimize latency
                
            except TimeoutError:
                if self.currentAircraft and not self.timedOut:
                    self.currentAircraft.on_timeout()
                self.timedOut = True
                continue

            self.timedOut = False
            # print(data)
            self.lastFrameTime = monotonic()
            data = data[0].decode("utf-8").split(";")
            items = {}

            for i in data:
                try:
                    k,v = i.split("=")
                    values = v.split("~")
                    items[k] = [utils.to_number(v) for v in values] if len(values)>1 else utils.to_number(v)
                except:
                    pass

            try:
                items["MechInfo"] = json.loads(items["MechInfo"])
            except: pass

            #print(items)
            aircraft_name = items.get("N")

            if aircraft_name and aircraft_name != self.currentAircraftName:
                if self.currentAircraft is None or aircraft_name != self.currentAircraftName:
                    cls = aircrafts.classes.get(aircraft_name, None)
                    if cls:
                        logging.info(f"Creating handler for {aircraft_name}: {cls}")
                        #instantiate new aircraft handler
                        self.currentAircraft = cls(aircraft_name)
                    else:
                        cls = aircrafts.classes.get("default", None)
                        logging.warning(f"No handler for {aircraft_name}, using {cls}")

                        self.currentAircraft = cls(aircraft_name)
                
                self.currentAircraftName = aircraft_name

            if self.currentAircraft:
                try:
                    self.currentAircraft.on_telemetry(items)
                except:
                    traceback.print_exc()

            if args.plot:
                for item in args.plot:
                    if item in items:
                        utils.teleplot.sendTelemetry(item, items[item])
            
            self.telemetryReceived.emit(items)





# Subclass QMainWindow to customize your application's main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("TelemFFB")

        label = QLabel("DCS Telemetry")

        #self.setFixedSize(QSize(400, 300))

        layout = QVBoxLayout()
        layout.addWidget(label)

        self.lbl_telem_data = QLabel("waiting for data...")
        self.lbl_telem_data.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_telem_data)

        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)

        # Set the central widget of the Window.
        #self.setCentralWidget(label)

    def update_telemetry(self, data : dict):
        items = ""
        for k,v in data.items():
            if k == "MechInfo":
                v = format_dict(v, "MechInfo.")
                items += f"{v}"
            else:
                if type(v) == float:
                    items += f"{k}: {v:.2f}\n"
                else:
                    items += f"{k}: {v}\n"
            
        self.lbl_telem_data.setText(items)



def main():
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    manager = TelemManager()
    manager.start()

    manager.telemetryReceived.connect(window.update_telemetry)


    app.exec_()

if __name__ == "__main__":
    main()
