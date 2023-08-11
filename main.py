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

from traceback_with_variables import print_exc, prints_exc


import argparse
parser = argparse.ArgumentParser(description='Send telemetry data over USB')

# Add destination telemetry address argument
parser.add_argument('--teleplot', type=str, metavar="IP:PORT", default=None,
                    help='Destination IP:port address for teleplot.fr telemetry plotting service')

parser.add_argument('-p', '--plot', type=str, nargs='+',
                    help='Telemetry item names to send to teleplot, separated by spaces')

parser.add_argument('-D', '--device', type=str, help='Rhino device USB VID:PID', default="ffff:2055")
parser.add_argument('-r', '--reset', help='Reset all FFB effects', action='store_true')

# Add config file argument, default config.ini
parser.add_argument('-c', '--configfile', type=str, help='Config ini file (default config.ini)', default='config.ini')
parser.add_argument('-o', '--overridefile', type=str, help='User config override file (default = config.user.ini', default='config.user.ini')
parser.add_argument('-s', '--sim', type=str, help='Set simulator options DCS|MSFS (default DCS', default="DCS")
parser.add_argument('-t', '--type', help='FFB Device Type | joystick (default) | pedals | collective', default='joystick')

args = parser.parse_args()
import json
import logging
import sys
import time
import os
sys.path.insert(0, '')

log_folder = './log'
if not os.path.exists(log_folder):
    os.makedirs(log_folder)
logname = "".join(["TelemFFB", "_", args.device.replace(":", "-"), "_", args.configfile, ".log"])
log_file = os.path.join(log_folder, logname)

# Create a logger instance
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Create a formatter for the log messages
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Create a StreamHandler to log messages to the console
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

# Create a FileHandler to log messages to the log file
file_handler = logging.FileHandler(log_file, mode='w')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

import re

import argparse
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QMainWindow, QVBoxLayout, QMessageBox, QPushButton, QDialog, \
    QRadioButton, QListView, QScrollArea, QHBoxLayout
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QCoreApplication, QUrl, QRect, QMetaObject
from PyQt5.QtGui import QFont, QPixmap, QIcon, QDesktopServices
# from PyQt5.QtWidgets import *
# from PyQt5.QtCore import *
# from PyQt5.QtGui import *

from time import monotonic
import socket
import threading
import aircrafts_dcs
import aircrafts_msfs
import utils
import subprocess

import traceback
from ffb_rhino import HapticEffect
from configobj import ConfigObj

from sc_manager import SimConnectManager

script_dir = os.path.dirname(os.path.abspath(__file__))

if args.teleplot:
    logging.info(f"Using {args.teleplot} for plotting")
    utils.teleplot.configure(args.teleplot)

version = utils.get_version()

def format_dict(data, prefix=""):
    output = ""
    for key, value in data.items():
        if isinstance(value, dict):
            output += format_dict(value, prefix + key + ".")
        else:
            output += prefix + key + " = " + str(value) + "\n"
    return output


def load_config(filename, raise_errors=True) -> ConfigObj:
    config_path = os.path.join(os.path.dirname(__file__), filename)

    try:
        config = ConfigObj(config_path, raise_errors=raise_errors)
        logging.info(f"Load Config: {config_path}")
        return config
    except Exception as e:
        logging.error(f"Cannot load config {config_path}:  {e}")
        err = ConfigObj()
        err["EXCEPTION"] = {}
        err["EXCEPTION"]["ERROR"] = e
        err["system"] = {}
        err["system"]["logging_level"] = "DEBUG"
        err["system"]["msfs_enabled"] = 0
        err["system"]["dcs_enabled"] = 0
        return err

_config = None
_config_mtime = 0

# if update is true, update the current modified time
def config_has_changed(update=False) -> bool:
    global _config_mtime
    global _config
    
    # "hash" both mtimes together
    tm = int(os.path.getmtime(args.configfile))
    if os.path.exists(args.overridefile):
        tm += int(os.path.getmtime(args.overridefile))
    if update:
        _config_mtime = tm

    if _config_mtime != tm:
        _config = None # force reloading config on next get_config call
        return True
    return False

def get_config() -> ConfigObj:
    global _config
    # TODO: check if config files changed and reload
    if _config: return _config

    main = load_config(args.configfile)
    user = load_config(args.overridefile, raise_errors=False)
    if user and main:
        main.update(user)
    config_has_changed(update=True)
    _config = main
    return main


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

    currentAircraft: aircrafts_dcs.Aircraft = None
    currentAircraftName: str = None
    timedOut: bool = True
    lastFrameTime: float
    numFrames: int = 0

    def __init__(self) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self)

        self.daemon = True
        self._run = True

    def get_aircraft_config(self, aircraft_name, default_section=None):
        config = get_config()

        if default_section:
            logging.info(f"Loading parameters from '{default_section}' section")
            params = utils.sanitize_dict(config[default_section])
        else:
            params = utils.sanitize_dict(config["default"])

        type = "Aircraft"

        for section,conf in config.items():
            # find matching aircraft in config
            if re.match(section, aircraft_name):
                conf = utils.sanitize_dict(conf)
                logging.info(f"Found aircraft '{aircraft_name}' in config")
                type = conf.get("type", "Aircraft")
                params.update(conf)

        return (params, type)
    
    def quit(self):
        self._run = False
        self.join()
    
    @prints_exc
    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)

        s.settimeout(0.1)
        port = 34380
        s.bind(("", port))
        logging.info(f"Listening on UDP :{port}")

        while self._run:
            try:
                data = s.recvfrom(4096)
                while utils.sock_readable(s):
                    data = s.recvfrom(4096)  # get last frame in OS buffer, to minimize latency
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

            # print(sender)
            self.lastFrameTime = monotonic()
            data = data[0].decode("utf-8").split(";")
            telem_data = {}
            telem_data["FFBType"] = args.type

            if data[0] == "DISCONNECT":
                logging.info("Telemetry disconnected")
                self.currentAircraftName = None

            for i in data:
                try:
                    if len(i) and i != "CONNECT" and i != "DISCONNECT":
                        section, conf = i.split("=")
                        values = conf.split("~")
                        telem_data[section] = [utils.to_number(v) for v in values] if len(values) > 1 else utils.to_number(conf)
                except Exception as e:
                    traceback.print_exc()
                    print("Error Parsing Parameter: ", repr(i))

            # print(items)
            aircraft_name = telem_data.get("N")
            data_source = telem_data.get("src", None)
            if data_source == "MSFS2020":
                module = aircrafts_msfs
            else:
                module = aircrafts_dcs

            if aircraft_name and aircraft_name != self.currentAircraftName:
                
                if self.currentAircraft is None or aircraft_name != self.currentAircraftName:
                    params, cls_name = self.get_aircraft_config(aircraft_name, data_source)

                    Class = getattr(module, cls_name, None)
                    if not Class:
                        logging.warning(f"Aircraft definition not found, using default class for {aircraft_name}")
                        Class = module.Aircraft

                    vpconf_path = utils.winreg_get("SOFTWARE\\VPforce\\RhinoFFB", "path")
                    if vpconf_path and "vpconf" in params:
                        logging.info(f"Found VPforce Configurator at {vpconf_path}")
                        serial = HapticEffect.device.serial
                        subprocess.call([vpconf_path, "-config", params["vpconf"], "-serial", serial])

                    logging.info(f"Creating handler for {aircraft_name}: {Class.__module__}.{Class.__name__}")
                    # instantiate new aircraft handler
                    self.currentAircraft = Class(aircraft_name)
                    self.currentAircraft.apply_settings(params)

                self.currentAircraftName = aircraft_name

            if self.currentAircraft:
                if config_has_changed():
                    logging.info("Configuration has changed, reloading")
                    params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
                    self.currentAircraft.apply_settings(params)
                try:
                    _tm = time.perf_counter()
                    commands = self.currentAircraft.on_telemetry(telem_data)
                    telem_data["perf"] = f"{(time.perf_counter() - _tm) * 1000:.3f}ms"
                    if commands:
                        # send command back
                        s.sendto(bytes(commands, "utf-8"), sender)
                except:
                    print_exc()

            if args.plot:
                for item in args.plot:
                    if item in telem_data:
                        utils.teleplot.sendTelemetry(item, telem_data[item])

            try: # sometime Qt object is destroyed first on exit and this may cause a runtime exception
                self.telemetryReceived.emit(telem_data)
            except: pass

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
        packet = bytes(";".join([f"{k}={self.fmt(v)}" for k, v in data.items()]), "utf-8")
        self.s.sendto(packet, ("127.255.255.255", 34380))



# Subclass QMainWindow to customize your application's main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setGeometry(100, 100, 400, 700)
        if version:
            self.setWindowTitle(f"TelemFFB ({version})")
        else:
            self.setWindowTitle(f"TelemFFB")

        self.resize(400, 700)
        # Get the absolute path of the script's directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Create a layout for the main window
        layout = QVBoxLayout()

        # Add a label for the image
        # Construct the absolute path of the image file
        image_path = os.path.join(script_dir, "image/vpforcelogo.png")
        self.image_label = QLabel()
        pixmap = QPixmap(image_path)
        self.image_label.setPixmap(pixmap)
        # Construct the absolute path of the icon file
        icon_path = os.path.join(script_dir, "image/vpforceicon.png")
        self.setWindowIcon(QIcon(icon_path))
        # Add the image label to the layout
        layout.addWidget(self.image_label, alignment=Qt.AlignTop | Qt.AlignLeft)
        # layout.addWidget(QLabel(f"Config File: {args.configfile}"))
        cfg_layout = QHBoxLayout()
        self.cfg_label = QLabel()
        self.cfg_label.setText(f"Config File: {args.configfile}")
        self.cfg_label.setToolTip("You can use a custom configuration file by passing the -c argument to TelemFFB\n\nExample: \"VPForce-TelemFFB.exe -c customconfig.ini\"")

        self.ovrd_label = QLabel()
        if os.path.exists(args.overridefile):
            self.ovrd_label.setText(f"User Override File: {args.overridefile}")
        else:
            self.ovrd_label.setText(f"User Override File: None")

        self.ovrd_label.setToolTip("Rename \'config.user.ini.README\' to \'config.user.ini\' or create a new <custom_name>.user.ini file and pass the name to TelemFFB with the -o argument\n\nExample \"VPForce-TelemFFB.exe -o myconfig.user.ini\" (starting TelemFFB without the override flag will look for the default config.user.ini)")
        self.ovrd_label.setAlignment(Qt.AlignLeft)
        self.cfg_label.setAlignment(Qt.AlignLeft)

        cfg_layout.addWidget(self.cfg_label)
        cfg_layout.addWidget(self.ovrd_label)

        layout.addLayout(cfg_layout)

        cfg = get_config()
        dcs_enabled = 'Yes'
        msfs_enabled = 'No'
        if args.sim == "MSFS" or cfg["system"].get("msfs_enabled") == "1":
            msfs_enabled = 'Yes'
        simlabel = QLabel(f"Sims Enabled: DCS: {dcs_enabled} | MSFS: {msfs_enabled}")
        simlabel.setToolTip("Enable/Disable Sims in config file or use '-s DCS|MSFS' argument to specify")
        layout.addWidget(simlabel)
        # Add a label and telemetry data label
        layout.addWidget(QLabel("Telemetry"))

        # Create a scrollable area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        # Create the QLabel widget and set its properties
        if cfg.get("EXCEPTION"):
            error = cfg["EXCEPTION"]["ERROR"]
            logging.error(f"CONFIG ERROR: {error}")
            self.lbl_telem_data = QLabel(f"CONFIG ERROR: {error}")
        else:
            self.lbl_telem_data = QLabel("Waiting for data...")
        self.lbl_telem_data.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_telem_data.setWordWrap(True)

        # Set the QLabel widget as the widget inside the scroll area
        scroll_area.setWidget(self.lbl_telem_data)

        # Add the scroll area to the layout
        layout.addWidget(scroll_area)

        # Add the edit button
        edit_button = QPushButton("Edit Config File")
        edit_button.setMinimumWidth(200)
        edit_button.setMaximumWidth(200)
        edit_button.clicked.connect(self.edit_config_file)
        layout.addWidget(edit_button, alignment=Qt.AlignCenter)

        self.log_button = QPushButton("Open/Hide Log")
        self.log_button.setMinimumWidth(200)
        self.log_button.setMaximumWidth(200)
        self.log_button.clicked.connect(self.toggle_log_window)
        layout.addWidget(self.log_button, alignment=Qt.AlignCenter)

        # Add the exit button
        exit_button = QPushButton("Exit")
        exit_button.setMinimumWidth(200)  # Set the minimum width
        exit_button.setMaximumWidth(200)  # Set the maximum width
        exit_button.clicked.connect(self.exit_application)
        layout.addWidget(exit_button, alignment=Qt.AlignCenter)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        self.layout = QVBoxLayout(central_widget)

        row_layout = QHBoxLayout()
        self.doc_label = QLabel()
        doc_url = 'https://docs.google.com/document/d/1YL5DLkiTxlaNx_zKHEYSs25PjmGtQ6_WZDk58_SGt8Y/edit#heading=h.27yzpife8719'
        label_txt = 'TelemFFB Documentation'
        self.doc_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.doc_label.setOpenExternalLinks(True)
        self.doc_label.setText(f'<a href="{doc_url}">{label_txt}</a>')
        self.dl_label = QLabel()
        doc_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=A'
        label_txt = 'Download Latest'
        self.dl_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.dl_label.setOpenExternalLinks(True)
        self.dl_label.setText(f'<a href="{doc_url}">{label_txt}</a>')
        self.dl_label.setAlignment(Qt.AlignRight)

        row_layout.addWidget(self.doc_label)
        row_layout.addWidget(self.dl_label)

        layout.addLayout(row_layout)
        central_widget.setLayout(layout)

    # def show_ffb_test_tool(self):
    #     try:
    #         self.ffb_test_tool = FFBTestToolDialog()
    #         self.ffb_test_tool.show()
    #     except Exception as e:
    #         logging.error(f"Error: {e}")
    #
    # def increment_counter(self):
    #     global periodic_effect_index
    #     periodic_effect_index += 1
    #     print("periodic_effect_index:", periodic_effect_index)
    def toggle_log_window(self):
        if d.isVisible():
            d.hide()
        else:
            d.show()

    def exit_application(self):
        # Perform any cleanup or save operations here
        QCoreApplication.instance().quit()

    def update_telemetry(self, data: dict):
        try:
            items = ""
            for k, v in data.items():
                if type(v) == float:
                    items += f"{k}: {v:.3f}\n"
                else:
                    if isinstance(v, list):
                        v = "[" + ", ".join([f"{x:.3f}" if not isinstance(x, str) else x for x in v]) + "]"
                    items += f"{k}: {v}\n"

            self.lbl_telem_data.setText(items)
        except Exception as e:
            traceback.print_exc()

    def edit_config_file(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))

        config_file = args.configfile
        config_path = os.path.join(script_dir, config_file)
        file_url = QUrl.fromLocalFile(config_path)
        try:
            QDesktopServices.openUrl(file_url)
        except:
            logging.error(f"There was an error opening the config file")


# class FFBTestToolDialog(QDialog):
#     def __init__(self):
#         super().__init__()
#         self.ui = Ui_FFBTestTool()
#         self.ui.setupUi(self)
# class Ui_FFBTestTool(object):
#     def setupUi(self, FFBTestTool):
#         if not FFBTestTool.objectName():
#             FFBTestTool.setObjectName(u"FFBTestTool")
#         FFBTestTool.resize(562, 462)
#         self.radioButton = QRadioButton(FFBTestTool)
#         self.radioButton.setObjectName(u"radioButton")
#         self.radioButton.setGeometry(QRect(60, 60, 82, 17))
#         self.listView = QListView(FFBTestTool)
#         self.listView.setObjectName(u"listView")
#         self.listView.setGeometry(QRect(40, 120, 256, 192))
#
#         self.retranslateUi(FFBTestTool)
#
#         QMetaObject.connectSlotsByName(FFBTestTool)
#     # setupUi

# def retranslateUi(self, FFBTestTool):
#     FFBTestTool.setWindowTitle(QCoreApplication.translate("FFBTestTool", u"FFB Test Tool", None))
#     self.radioButton.setText(QCoreApplication.translate("FFBTestTool", u"RadioButton", None))
# # retranslateUi


def main():
    app = QApplication(sys.argv)
    global d
    d = LogWindow()
    #d.show()

    sys.stdout = utils.OutLog(d.widget, sys.stdout)
    sys.stderr = utils.OutLog(d.widget, sys.stderr)

    logging.getLogger().handlers[0].setStream(sys.stdout)
    logging.info(f"TelemFFB (version {version}) Starting")

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


    config = get_config()
    ll = config["system"].get("logging_level", "INFO")
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    logger.setLevel(log_levels.get(ll, logging.DEBUG))
    logging.info(f"Logging level set to:{logging.getLevelName(logger.getEffectiveLevel())}")


    window = MainWindow()
    window.show()

    manager = TelemManager()
    manager.start()

    manager.telemetryReceived.connect(window.update_telemetry)

    sc = SimConnectSock()
    try:
        msfs = config["system"].get("msfs_enabled", None)
        logging.debug(f"MSFS={msfs}")
        if msfs == "1" or args.sim == "MSFS":
            logging.info("MSFS Enabled:  Starting Simconnect Manager")
            sc.start()
    except:
        logging.exception("Error loading MSFS enable flag from config file")

    app.exec_()
    manager.quit()


if __name__ == "__main__":
    main()
