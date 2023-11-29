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

import atexit
import glob

from traceback_with_variables import print_exc, prints_exc


import argparse

import settingsmanager

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
parser.add_argument('-s', '--sim', type=str, help='Set simulator options DCS|MSFS|IL2 (default DCS', default="None")
parser.add_argument('-t', '--type', help='FFB Device Type | joystick (default) | pedals | collective', default='joystick')
parser.add_argument('-X', '--xml', help='use XML config', nargs='?', const='default')

args = parser.parse_args()

import json
import logging
import sys
import time
import os


sys.path.insert(0, '')
#sys.path.append('/simconnect')

log_folder = os.path.join(os.environ['LOCALAPPDATA'],"VPForce-TelemFFB", 'log')
#log_folder = './log'
if not os.path.exists(log_folder):
    os.makedirs(log_folder)
if args.xml is None:
    logname = "".join(["TelemFFB", "_", args.device.replace(":", "-"), "_", os.path.basename(args.configfile), "_",
                       os.path.basename(args.overridefile), ".log"])
else:
    logname = "".join(["TelemFFB", "_", args.device.replace(":", "-"), "_xmlconfig.log"])
log_file = os.path.join(log_folder, logname)

# Create a logger instance
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Create a formatter for the log messages
formatter = logging.Formatter('%(asctime)s.%(msecs)d - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

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
    QRadioButton, QListView, QScrollArea, QHBoxLayout, QAction, QPlainTextEdit, QMenu, QButtonGroup, QFrame
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QCoreApplication, QUrl, QRect, QMetaObject
from PyQt5.QtGui import QFont, QPixmap, QIcon, QDesktopServices
# from PyQt5.QtWidgets import *
# from PyQt5.QtCore import *
# from PyQt5.QtGui import *

import socket
import threading
import aircrafts_dcs
import aircrafts_msfs
import aircrafts_il2
import utils
import subprocess

import traceback
from ffb_rhino import HapticEffect
from configobj import ConfigObj

from sc_manager import SimConnectManager
from il2_telem import IL2Manager
from aircraft_base import effects
from settingsmanager import *
import pprint
effects_translator = utils.EffectTranslator()

script_dir = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(args.configfile) == args.configfile:
    # just the filename is present, assume it is in the script directory
    # print("Config File is in the script dir")
    configfile = os.path.join(script_dir, args.configfile)
else:
    # assume is absolute path to file
    # print("Config file is absolute path")
    configfile = args.configfile
if os.path.basename(args.overridefile) == args.overridefile:
    # just the filename is present, assume it is in the script directory
    overridefile = os.path.join(script_dir, args.overridefile)
else:
    # assume is absolute path to file
    overridefile = args.overridefile

if args.teleplot:
    logging.info(f"Using {args.teleplot} for plotting")
    utils.teleplot.configure(args.teleplot)

version = utils.get_version()
min_firmware_version = 'v1.0.15'
global dev_firmware_version
dev_firmware_version = None

import xmlutils

class LoggingFilter(logging.Filter):
    def __init__(self, keywords):
        self.keywords = keywords

    def filter(self, record):
        # Check if any of the keywords are present in the log message
        for keyword in self.keywords:
            if keyword in record.getMessage():
                # If any keyword is found, prevent the message from being logged
                return False
        # If none of the keywords are found, allow the message to be logged
        return True

# Create a list of keywords to filter
log_filter_strings = [
    # "unrecognized Miscellaneous Unit in typefor(POSITION)",
    # "Unrecognized event AXIS_CYCLIC_LATERAL_SET",
    # "Unrecognized event AXIS_CYCLIC_LONGITUDINAL_SET",
    # "Unrecognized event ROTOR_AXIS_TAIL_ROTOR_SET",
    # "Unrecognized event AXIS_COLLECTIVE_SET",
]

log_filter = LoggingFilter(log_filter_strings)

console_handler.addFilter(log_filter)
file_handler.addFilter(log_filter)


def format_dict(data, prefix=""):
    output = ""
    for key, value in data.items():
        if isinstance(value, dict):
            output += format_dict(value, prefix + key + ".")
        else:
            output += prefix + key + " = " + str(value) + "\n"
    return output


def load_config(filename, raise_errors=True) -> ConfigObj:
    if not os.path.sep in filename:
        #construct absolute path
        config_path = os.path.join(os.path.dirname(__file__), filename)
    else:
        #filename is absolute path
        config_path = filename

    try:
        config = ConfigObj(config_path, raise_errors=raise_errors)
        logging.info(f"Loading Config: {config_path}")
        if not os.path.exists(config_path):
            logging.warning(f"Configuration file {filename} does not exist")
            path = os.path.dirname(config_path)
            ini_files = glob.glob(f"{path}/*.ini")
            logging.warning(f"Possible ini files in that location are:")
            for file in ini_files:
                logging.warning(f"{os.path.basename(file)}")
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

_config : ConfigObj = None
_config_mtime = 0

# if update is true, update the current modified time
def config_has_changed(update=False) -> bool:
    global _config_mtime
    global _config
    
    # "hash" both mtimes together
    if args.xml is not None:
        userfile=settingsmanager.SettingsWindow.userconfig_path
        tm = int(os.path.getmtime(userfile))
    else:
        tm = int(os.path.getmtime(configfile))
        if os.path.exists(overridefile):
            tm += int(os.path.getmtime(overridefile))
    if update:
        _config_mtime = tm

    if _config_mtime != tm:
        _config = None # force reloading config on next get_config call
        return True
    return False


def get_config_xml():
    global _config
    global settings_mgr
    if _config: return _config
    a, b, main = xmlutils.read_single_model('Global', '')
    # user = settingsmanager.read_xml_file(overridefile, 'global', 'Aircraft', args.type)
    params = ConfigObj()
    params['system'] = {}
    for setting in main:
        k = setting['name']
        v = setting['value']
        if setting["grouping"] == "System":
            params['system'][k] = v
            logging.warning(f"Got Globals from Settings Manager: {k} : {v}")
    # for setting in user:
    #     k = setting['name']
    #     v = setting['value']
    #     if setting["grouping"] == "System":
    #         params['system'][k] = v
    #         logging.warning(f"Got USER config from SMITTY: {k} : {v}")
    return params

def get_config() -> ConfigObj:
    global _config
    if _config: return _config
    if args.xml is not None:
        params = get_config_xml()
        config_has_changed(update=True)
        _config = params
        return params

    main = load_config(configfile)
    user = load_config(overridefile, raise_errors=False)
    if user and main:
        main.merge(user)   
    
    config_has_changed(update=True)
    _config = main
    return main


class LogWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Log Console")
        self.resize(800, 500)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.widget = QPlainTextEdit(self.central_widget)
        self.widget.setReadOnly(True)
        self.widget.setFont(QFont("Courier New"))

        layout = QVBoxLayout(self.central_widget)
        layout.addWidget(self.widget)

        # Create a menu bar
        menubar = self.menuBar()

        # Create a "Logging" menu
        logging_menu = menubar.addMenu("Logging Level")

        # Create "Debug Logging" action and add it to the "Logging" menu
        debug_action = QAction("Debug Logging", self)
        debug_action.triggered.connect(self.set_debug_logging)
        logging_menu.addAction(debug_action)

        # Create "Normal Logging" action and add it to the "Logging" menu
        normal_action = QAction("Normal Logging", self)
        normal_action.triggered.connect(self.set_info_logging)
        logging_menu.addAction(normal_action)

    def set_debug_logging(self):
        logger.setLevel(logging.DEBUG)
        logging.info(f"Logging level set to DEBUG")

    def set_info_logging(self):
        logger.setLevel(logging.INFO)
        logging.info(f"Logging level set to INFO")


class TelemManager(QObject, threading.Thread):
    telemetryReceived = pyqtSignal(object)
    currentAircraft: aircrafts_dcs.Aircraft = None
    currentAircraftName: str = None
    timedOut: bool = True
    lastFrameTime: float
    numFrames: int = 0

    def __init__(self, settings_manager) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self, daemon=True)

        self._run = True
        self._cond = threading.Condition()
        self._data = None
        self._events = []
        self._dropped_frames = 0
        self.lastFrameTime = time.perf_counter()
        self.frameTimes = []
        self.timeout = 0.2
        self.settings_manager = settings_manager
        # settings_manager.show()

    def get_aircraft_config(self, aircraft_name, default_section=None):
        if args.xml is not None:
            config = get_config()
            params, class_name = self.sm_get_aircraft_config(aircraft_name, default_section)
            return params, class_name
        config = get_config()

        if default_section:
            logging.info(f"Loading parameters from '{default_section}' section")
            params = utils.sanitize_dict(config[default_section])
        else:
            params = utils.sanitize_dict(config["default"])

        class_name = "Aircraft"
        for section,conf in config.items():
            # find matching aircraft in config
            if re.match(section, aircraft_name):
                conf = utils.sanitize_dict(conf)
                logging.info(f"Found section [{section}] for aircraft '{aircraft_name}' in config")
                class_name = conf.get("type", "Aircraft")

                # load params from that class in config
                s = ".".join([default_section, class_name] if default_section else [class_name])
                logging.info(f"Loading parameters from [{s}] section")
                class_params = config.get(s)
                if class_params:
                    class_params = utils.sanitize_dict(class_params)
                    params.update(class_params)
                else:
                    logging.warning(f"Section [{s}] does not exist")

                params.update(conf)

        return (params, class_name)

    def sm_get_aircraft_config(self, aircraft_name, data_source):
        params = {}
        cls_name = "UNKNOWN"
        try:
            if data_source == "MSFS2020":
                send_source = "MSFS"
            else:
                send_source = data_source
            # cls_name,pattern, result = settingsmanager.read_single_model(configfile, send_source, aircraft_name, args.type, userconfig_path=overridefile)

            cls_name, pattern, result = xmlutils.read_single_model(send_source, aircraft_name)
            if cls_name == '': cls_name = 'Aircraft'
            for setting in result:
                k = setting['name']
                v = setting['value']
                u = setting['unit']
                vu= v+u
                if setting['value'] != '-':
                    params[k] = vu
                    logging.warning(f"Got from Settings Manager: {k} : {vu}")
                else:
                    logging.warning(f"Ignoring blank setting from Settings Manager: {k} : {vu}")
                # print(f"SETTING:\n{setting}")
            params = utils.sanitize_dict(params)

            return params, cls_name

            # logging.info(f"Got settings from settingsmanager:\n{formatted_result}")
        except Exception as e:
            logging.warning(f"Error getting settings from Settings Manager:{e}")

    def quit(self):
        self._run = False
        self.join()

    def submitFrame(self, data : bytes):
        if type(data) == bytes:
            data = data.decode("utf-8")

        with self._cond:
            if data.startswith("Ev="):
                self._events.append(data.lstrip("Ev="))
                self._cond.notify()
            elif self._data is None:
                self._data = data
                self._cond.notify() # notify waiting thread of new data
            else:
                self._dropped_frames += 1
                # log dropped frames, this is not necessarily a bad thing
                # USB interrupt transfers (1ms) might take longer than one video frame
                # we drop frames to keep latency to a minimum
                logging.debug(f"Droppped frame (total {self._dropped_frames})")

    def process_events(self):
        while len(self._events):
            ev = self._events.pop(0)
            ev = ev.split(";")

            if self.currentAircraft:
                self.currentAircraft.on_event(*ev)
            continue

    def process_data(self, data):

        data = data.split(";")

        telem_data = {}
        telem_data["FFBType"] = args.type

        self.frameTimes.append(int((time.perf_counter() - self.lastFrameTime)*1000))
        if len(self.frameTimes) > 50: self.frameTimes.pop(0)
        
        telem_data["frameTimes"] = [self.frameTimes[-1], max(self.frameTimes)]


        self.lastFrameTime = time.perf_counter()

        for i in data:
            try:
                if len(i):
                    section, conf = i.split("=")
                    values = conf.split("~")
                    telem_data[section] = [utils.to_number(v) for v in values] if len(values) > 1 else utils.to_number(conf)

            except Exception as e:
                traceback.print_exc()
                logging.error("Error Parsing Parameter: ", repr(i))

        # print(items)
        aircraft_name = telem_data.get("N")
        data_source = telem_data.get("src", None)
        if data_source == "MSFS2020":
            module = aircrafts_msfs
            sc_aircraft_type = telem_data.get("SimconnectCategory", None)
            sc_engine_type = telem_data.get("EngineType", 4)
            # 0 = Piston
            # 1 = Jet
            # 2 = None
            # 3 = Helo(Bell) turbine
            # 4 = Unsupported
            # 5 = Turboprop
        elif data_source == "IL2":
            module = aircrafts_il2
        else:
            module = aircrafts_dcs

        if aircraft_name and aircraft_name != self.currentAircraftName:
            
            if self.currentAircraft is None or aircraft_name != self.currentAircraftName:

                params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
                if args.xml is not None:

                    if data_source == "MSFS2020":
                        send_source = "MSFS"

                    xmlutils.current_sim = send_source
                    xmlutils.current_aircraft_name = aircraft_name
                    # tried hiding, still crashes
                    # if settings_mgr.isVisible():
                    #     settings_mgr.hide()
                    #settingsmanager.SettingsWindow.update_current_aircraft(settings_mgr)

                    #self.settings_manager.update_current_aircraft(send_source, aircraft_name, cls_name)
                Class = getattr(module, cls_name, None)
                logging.debug(f"CLASS={Class.__name__}")

                if not Class or Class.__name__ == "Aircraft":
                    if data_source == "MSFS2020":
                        if sc_aircraft_type == "Helicopter":
                            logging.warning(f"Aircraft definition not found, using SimConnect Data (Helicopter Type)")
                            type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.Helicopter")
                            params.update(type_cfg)
                            Class = module.Helicopter
                        elif sc_aircraft_type == "Jet":
                            logging.warning(f"Aircraft definition not found, using SimConnect Data (Jet Type)")
                            type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.JetAircraft")
                            params.update(type_cfg)
                            Class = module.JetAircraft
                        elif sc_aircraft_type == "Airplane":
                            if sc_engine_type == 0:     # Piston
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Propeller Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.PropellerAircraft")
                                params.update(type_cfg)
                                Class = module.PropellerAircraft
                            if sc_engine_type == 1:     # Jet
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Jet Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.JetAircraft")
                                params.update(type_cfg)
                                Class = module.JetAircraft
                            elif sc_engine_type == 2:   # None
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Glider Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.GliderAircraft")
                                params.update(type_cfg)
                                Class = module.GliderAircraft
                            elif sc_engine_type == 3:   # Heli
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Helo Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.HelicopterAircraft")
                                params.update(type_cfg)
                                Class = module.Helicopter
                            elif sc_engine_type == 5:   # Turboprop
                                logging.warning(f"Aircraft definition not found, using SimConnect Data (Turboprop Type)")
                                type_cfg, cls_name = self.get_aircraft_config(aircraft_name, "MSFS2020.TurbopropAircraft")
                                params.update(type_cfg)
                                Class = module.TurbopropAircraft
                        else:
                            logging.warning(f"Aircraft definition not found, using default class for {aircraft_name}")
                            Class = module.Aircraft
                    else:
                        logging.warning(f"Aircraft definition not found, using default class for {aircraft_name}")
                        Class = module.Aircraft

                vpconf_path = utils.winreg_get("SOFTWARE\\VPforce\\RhinoFFB", "path")
                if vpconf_path and "vpconf" in params:
                    logging.info(f"Found VPforce Configurator at {vpconf_path}")
                    serial = HapticEffect.device.serial
                    workdir = os.path.dirname(vpconf_path)
                    env = {}
                    env["PATH"] = os.environ["PATH"]
                    subprocess.call([vpconf_path, "-config", params["vpconf"], "-serial", serial], cwd=workdir, env=env)

                logging.info(f"Creating handler for {aircraft_name}: {Class.__module__}.{Class.__name__}")
                # instantiate new aircraft handler
                self.currentAircraft = Class(aircraft_name)
                # self.currentAircraft.apply_settings(params)
                self.currentAircraft.apply_settings(params)

            self.currentAircraftName = aircraft_name

        if self.currentAircraft:
            if config_has_changed():
                logging.info("Configuration has changed, reloading")
                params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
                self.currentAircraft.apply_settings(params)
            try:
                _tm = time.perf_counter()
                self.currentAircraft._telem_data = telem_data
                self.currentAircraft.on_telemetry(telem_data)
                telem_data["perf"] = f"{(time.perf_counter() - _tm) * 1000:.3f}ms"

            except:
                print_exc()

        if args.plot:
            for item in args.plot:
                if item in telem_data:
                    utils.teleplot.sendTelemetry(item, telem_data[item])

        try: # sometime Qt object is destroyed first on exit and this may cause a runtime exception
            self.telemetryReceived.emit(telem_data)
        except: pass

    def on_timeout(self):
        if self.currentAircraft and not self.timedOut:
            self.currentAircraft.on_timeout()
        self.timedOut = True

    @prints_exc
    def run(self):
        global _config
        self.timeout = utils.sanitize_dict(_config["system"]).get("telemetry_timeout", 200)/1000
        logging.info(f"Telemetry timeout: {self.timeout}")
        while self._run:
            with self._cond:
                if not len(self._events) and not self._data:
                    if not self._cond.wait(self.timeout):
                        self.on_timeout()
                        continue

                if len(self._events):
                    self.process_events()

                if self._data:
                    self.timedOut = False
                    data = self._data
                    self._data = None
                    self.process_data(data)

class NetworkThread(threading.Thread):
    def __init__(self, telemetry : TelemManager, host = "", port = 34380, telem_parser = None):
        super().__init__()
        self._run = True
        self._port = port
        self._telem = telemetry
        self._telem_parser = telem_parser

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)

        s.settimeout(0.1)
        s.bind(("", self._port))
        logging.info(f"Listening on UDP :{self._port}")  

        while self._run:
            try:
                data, sender = s.recvfrom(4096)
                if self._telem_parser is not None:
                    data = self._telem_parser.process_packet(data)

                self._telem.submitFrame(data)
            except ConnectionResetError:
                continue
            except socket.timeout:
                continue
    
    def quit(self):
        self._run = False

class SimConnectSock(SimConnectManager):
    def __init__(self, telem : TelemManager):
        super().__init__()
        self._telem = telem

    def fmt(self, val):
        if isinstance(val, list):
            return "~".join([str(x) for x in val])
        return val

    def emit_packet(self, data):
        data["src"] = "MSFS2020"
        packet = bytes(";".join([f"{k}={self.fmt(v)}" for k, v in data.items()]), "utf-8")
        self._telem.submitFrame(packet)

    def emit_event(self, event, *args):
        # special handling of Open event
        if event == "Open":
            # Reset all FFB effects on device, ensure we have a clean start
            HapticEffect.device.resetEffects()

        args = [str(x) for x in args]
        self._telem.submitFrame(f"Ev={event};" + ";".join(args))



# Subclass QMainWindow to customize your application's main window
class MainWindow(QMainWindow):
    def __init__(self, settings_manager):
        super().__init__()

        self.setGeometry(100, 100, 400, 700)
        if version:
            self.setWindowTitle(f"TelemFFB ({args.type}) ({version})")
        else:
            self.setWindowTitle(f"TelemFFB")

        self.resize(400, 700)
        # Get the absolute path of the script's directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Create a layout for the main window
        layout = QVBoxLayout()
        notes_row_layout = QHBoxLayout()

        # Create the menu bar
        menu_frame = QFrame()
        menu_frame_layout = QVBoxLayout(menu_frame)

        # Create the menu bar
        menubar = self.menuBar()

        # Set the background color of the menu bar
        menubar.setStyleSheet("""
            QMenuBar { background-color: #f0f0f0; } /* Set the background color of the menu bar */
            QMenu::item:selected { color: red; } /* Set the text color when a menu item is selected */
        """)

        # Create the "Utilities" menu
        utilities_menu = menubar.addMenu('Utilities')

        # Add the "Reset" action to the "Utilities" menu
        reset_action = QAction('Reset All Effects', self)
        reset_action.triggered.connect(self.reset_all_effects)
        utilities_menu.addAction(reset_action)

        # Add settings converter
        if args.xml is None:
            convert_settings_action = QAction('Convert user config.ini to XML',self)
            convert_settings_action.triggered.connect(self.convert_settings)
            utilities_menu.addAction(convert_settings_action)

        # menubar.setStyleSheet("QMenu::item:selected { color: red; }")

        # Create a line beneath the menu bar
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)

        # Add the line to the menu frame layout
        menu_frame_layout.addWidget(menubar)
        menu_frame_layout.addWidget(line)
        menu_frame_layout.setContentsMargins(0, 0, 0, 0)

        # Set the layout of the menu frame as the main layout
        layout.addWidget(menu_frame)

        self.notes_label = QLabel()
        notes_url = os.path.join(script_dir, '_RELEASE_NOTES.txt')
        label_txt = 'Release Notes'
        # self.notes_label.setOpenExternalLinks(True)

        # Connect the linkActivated signal to the open_file method and pass the URL
        self.notes_label.linkActivated.connect(lambda url=notes_url: self.open_file(url))

        self.notes_label.setText(f'<a href="{notes_url}">{label_txt}</a>')
        self.notes_label.setAlignment(Qt.AlignRight)
        self.notes_label.setToolTip(notes_url)

        notes_row_layout.addWidget(self.notes_label)
        layout.addLayout(notes_row_layout)

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


        # show xml file info
        if args.xml is None:
            self.ovrd_label = QLabel()
            self.cfg_label.setText(f"Config File: {args.configfile}")
            self.cfg_label.setToolTip("You can use a custom configuration file by passing the -c argument to TelemFFB\n\nExample: \"VPForce-TelemFFB.exe -c customconfig.ini\"")

            if os.path.exists(args.overridefile):
                self.ovrd_label.setText(f"User Override File: {args.overridefile}")
            else:
                self.ovrd_label.setText(f"User Override File: None")

            self.ovrd_label.setToolTip("Rename \'config.user.ini.README\' to \'config.user.ini\' or create a new <custom_name>.user.ini file and pass the name to TelemFFB with the -o argument\n\nExample \"VPForce-TelemFFB.exe -o myconfig.user.ini\" (starting TelemFFB without the override flag will look for the default config.user.ini)")
            cfg_layout.addWidget(self.cfg_label)

        else:
            # self.cfg_label.setText(f"XML Defaults File: {SettingsWindow.defaults_path}")
            # self.cfg_label.setToolTip("Use the Settings Manager to customize aircraft settings")
            self.ovrd_label = ClickableLabel()
            if os.path.exists(SettingsWindow.userconfig_path):
                self.ovrd_label.setText(f"User File: {SettingsWindow.userconfig_path}")

            self.ovrd_label.setToolTip("Use the Settings Manager to customize aircraft settings.\nClick to open the userconfig directory if you need to send the file for support.")


        self.ovrd_label.setAlignment(Qt.AlignLeft)
        self.cfg_label.setAlignment(Qt.AlignLeft)


        cfg_layout.addWidget(self.ovrd_label)

        layout.addLayout(cfg_layout)

        cfg = get_config()
        dcs_enabled = utils.sanitize_dict(cfg["system"]).get("dcs_enabled", False)
        msfs_enabled = utils.sanitize_dict(cfg["system"]).get("msfs_enabled", False)
        il2_enabled = utils.sanitize_dict(cfg["system"]).get("il2_enabled", False)
        if args.sim == "DCS" or dcs_enabled:
            dcs_enabled = 'True'
        else:
            dcs_enabled = 'False'
        if args.sim == "MSFS" or msfs_enabled:
            msfs_enabled = 'True'
        else:
            msfs_enabled = 'False'
        if args.sim == "IL2" or il2_enabled:
            il2_enabled = 'True'
        else:
            il2_enabled = 'False'
        simlabel = QLabel(f"Sims Enabled: DCS: {dcs_enabled} | MSFS: {msfs_enabled} | IL2: {il2_enabled}")
        simlabel.setToolTip("Enable/Disable Sims in config file or use '-s DCS|MSFS' argument to specify")
        layout.addWidget(simlabel)
        # Add a label and telemetry data label
        # layout.addWidget(QLabel("Telemetry"))


        self.radio_button_group = QButtonGroup()
        radio_row_layout = QHBoxLayout()
        self.telem_monitor_radio = QRadioButton("Telemetry Monitor")
        self.effect_monitor_radio = QRadioButton("Effects Monitor")

        radio_row_layout.addWidget(self.telem_monitor_radio)
        radio_row_layout.addWidget(self.effect_monitor_radio)

        self.telem_monitor_radio.setChecked(True)

        self.radio_button_group.addButton(self.telem_monitor_radio)
        self.radio_button_group.addButton(self.effect_monitor_radio)

        # self.radio_button_group.buttonClicked.connect(self.update_monitor_window)

        layout.addLayout(radio_row_layout)


        # Create a scrollable area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        # Create the QLabel widget and set its properties
        if cfg.get("EXCEPTION"):
            error = cfg["EXCEPTION"]["ERROR"]
            logging.error(f"CONFIG ERROR: {error}")
            self.lbl_telem_data = QLabel(f"CONFIG ERROR: {error}")
            QMessageBox.critical(None, "CONFIG ERROR", f"Error: {error}")
        else:
            self.lbl_telem_data = QLabel("Waiting for data...")
        self.lbl_telem_data.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_telem_data.setWordWrap(True)

        # Set the QLabel widget as the widget inside the scroll area
        scroll_area.setWidget(self.lbl_telem_data)

        # Add the scroll area to the layout
        layout.addWidget(scroll_area)
        if args.xml is None:
            edit_button = QPushButton("Edit Config File")
            edit_button.setMinimumWidth(200)
            edit_button.setMaximumWidth(200)

            layout.addWidget(edit_button, alignment=Qt.AlignCenter)

            # Create a sub-menu for the button
            self.sub_menu = QMenu(edit_button)
            # smgr_config_action = QAction("Settings Manager", self)
            # smgr_config_action.triggered.connect(lambda: (settings_manager.show(), settings_manager.get_current_model()))
            primary_config_action = QAction("Primary Config", self)
            primary_config_action.triggered.connect(lambda: self.edit_config_file("Primary"))
            if os.path.exists(args.overridefile):
                user_config_action = QAction("User Config", self)
                user_config_action.triggered.connect(lambda: self.edit_config_file("User"))

            # self.sub_menu.addAction(smgr_config_action)
            self.sub_menu.addAction(primary_config_action)
            if os.path.exists(args.overridefile):
                self.sub_menu.addAction(user_config_action)

            # Connect the button's click event to show the sub-menu
            edit_button.clicked.connect(self.show_sub_menu)
        else:
            edit_button = QPushButton("Settings Manager")
            edit_button.setMinimumWidth(200)
            edit_button.setMaximumWidth(200)
            edit_button.clicked.connect(self.toggle_settings_window)
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

        link_row_layout = QHBoxLayout()
        self.doc_label = QLabel()
        doc_url = 'https://docs.google.com/document/d/1YL5DLkiTxlaNx_zKHEYSs25PjmGtQ6_WZDk58_SGt8Y/edit#heading=h.27yzpife8719'
        label_txt = 'TelemFFB Documentation'
        self.doc_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.doc_label.setOpenExternalLinks(True)
        self.doc_label.setText(f'<a href="{doc_url}">{label_txt}</a>')
        self.doc_label.setToolTip(doc_url)
        self.dl_label = QLabel()
        dl_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=A'
        label_txt = 'Download Latest'
        self.dl_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.dl_label.setOpenExternalLinks(True)
        self.dl_label.setText(f'<a href="{dl_url}">{label_txt}</a>')
        self.dl_label.setAlignment(Qt.AlignRight)
        self.dl_label.setToolTip(dl_url)

        link_row_layout.addWidget(self.doc_label)
        link_row_layout.addWidget(self.dl_label)

        version_row_layout = QHBoxLayout()
        self.version_label = QLabel()

        status_text = "UNKNOWN"
        status = utils.fetch_latest_version()
        if status == False:
            status_text = "Up To Date"
        elif status == None:
            status_text = "UNKNOWN"
        else:
            status_text = f"New version <a href='{status[1]}'><b>{status[0]}</b></a> is available!"
        if status:
            self.version_label.setToolTip(status[1])

        self.version_label.setText(f'Version Status: {status_text}')
        self.version_label.setOpenExternalLinks(True)

        global dev_firmware_version
        self.firmware_label = QLabel()
        self.firmware_label.setText(f'Rhino Firmware: {dev_firmware_version}')

        self.version_label.setAlignment(Qt.AlignLeft)
        self.firmware_label.setAlignment(Qt.AlignLeft)
        version_row_layout.addWidget(self.version_label)
        version_row_layout.addWidget(self.firmware_label)

        layout.addLayout(link_row_layout)

        layout.addLayout(version_row_layout)

        central_widget.setLayout(layout)

    def show_sub_menu(self):
        edit_button = self.sender()
        self.sub_menu.popup(edit_button.mapToGlobal(edit_button.rect().bottomLeft()))

    def open_file(self, url):
        try:
            file_url = QUrl.fromLocalFile(url)
            QDesktopServices.openUrl(file_url)
        except Exception as e:
            logging.error(f"There was an error opening the file: {str(e)}")
    def reset_all_effects(self):
        result = QMessageBox.warning(None, "Are you sure?", "*** Only use this if you have effects which are 'stuck' ***\n\n  Proceeding will result in the destruction"
                                                            " of any effects which are currently being generated by the simulator and may result in requiring a restart of"
                                                            " the sim or a new session.\n\n~~ Proceed with caution ~~", QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)

        if result == QMessageBox.Ok:
            try:
                HapticEffect.device.resetEffects()
            except Exception as error:
                pass

    def convert_settings(self):
        differences = []
        defaultconfig = load_config(configfile)
        userconfig = load_config(overridefile, raise_errors=False)
        def_sections = defaultconfig.sections
        usr_sections = userconfig.sections

        for section,dconf in defaultconfig.items():
            #print (f"reading {section}")


            def_params = utils.sanitize_dict(defaultconfig[section])
            if section in usr_sections:

                usr_params = utils.sanitize_dict(userconfig[section])
                for ditem in def_params:
                    #print (f"{section} - {ditem} = {def_params[ditem]}" )
                    for uitem in usr_params:
                        if ditem == uitem:
                            if def_params[ditem] != usr_params[uitem]:
                                #print(f"{section} - {uitem} = {usr_params[uitem]}")
                                valuestring = str(usr_params[uitem])
                                dif_item = self.config_to_dict(section,uitem,valuestring)
                                differences.append(dif_item)

        xmlutils.write_converted_to_xml(differences)

        message = f'\n\nConverted {overridefile} for {args.type} to XML.\nYou can now use the -X argument to use the new Settings Manager\n'
        print(message)



    def config_to_dict(self,section,name,value):
        sim=''
        cls = ''
        model = ''
        match section:
            case 'system':
                sim = 'Global'
            case 'DCS':
                sim = 'DCS'
            case 'IL2':
                sim = 'IL2'
            case 'MSFS2020':
                sim = 'MSFS'

        if '.' in section:
            subsection = section.split('.')
            ssim = subsection[0]
            cls = subsection[1]
            match ssim:
                case 'DCS':
                    sim = 'DCS'
                case 'IL2':
                    sim = 'IL2'
                case 'MSFS2020':
                    sim = 'MSFS'

        # if sim is still blank here, must be a model
        if sim == '':
            model = section
            sim = 'any'
            cls = ''

        data_dict = {
            'name': name,
            'value': value,
            'sim' : sim,
            'class': cls,
            'model': model,
            'device': args.type
        }
        print (data_dict)
        return data_dict

    def edit_config_file(self, config_type):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if config_type == "Primary":
            config_file = args.configfile
        elif config_type == "User":
            config_file = args.overridefile
        config_path = os.path.join(script_dir, config_file)
        file_url = QUrl.fromLocalFile(config_path)
        try:
            QDesktopServices.openUrl(file_url)
        except:
            logging.error(f"There was an error opening the config file")


    def toggle_log_window(self):
        if d.isVisible():
            d.hide()
        else:
            d.show()

    def toggle_settings_window(self):
        if settings_mgr.isVisible():
            settings_mgr.hide()
        else:
            settings_mgr.show()
            print (f"# toggle settings window   {xmlutils.current_sim} {xmlutils.current_aircraft_name}")
            settingsmanager.SettingsWindow.currentmodel_click(settings_mgr)
            # settingsmanager.SettingsWindow.update_current_aircraft(TelemManager.currentsim, TelemManager.currentAircraftName)

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
            active_effects = ""
            for key in effects.dict.keys():
                if effects[key].started:
                    descr = effects_translator.get_translation(key)
                    if descr not in active_effects:
                        active_effects = '\n'.join([active_effects, descr])
            window_mode = self.radio_button_group.checkedButton()
            if window_mode == self.telem_monitor_radio:
                self.lbl_telem_data.setText(items)
                self.lbl_telem_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            elif window_mode == self.effect_monitor_radio:
                self.lbl_telem_data.setText(active_effects)
                self.lbl_telem_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        except Exception as e:
            traceback.print_exc()
class ClickableLabel(QLabel):
    def __init__(self, parent=None):
        super(ClickableLabel, self).__init__(parent)

    def mousePressEvent(self, event):
        os.startfile(SettingsWindow.userconfig_rootpath,'open')
        print("userpath opened")
        # Example: SettingsWindow.some_function()

def main():
    app = QApplication(sys.argv)
    global d
    global dev_firmware_version
    d = LogWindow()
    #d.show()
    global settings_mgr
    settings_mgr = SettingsWindow(device=args.type)

    sys.stdout = utils.OutLog(d.widget, sys.stdout)
    sys.stderr = utils.OutLog(d.widget, sys.stderr)

    logging.getLogger().handlers[0].setStream(sys.stdout)
    logging.info(f"TelemFFB (version {version}) Starting")



    vid_pid = [int(x, 16) for x in args.device.split(":")]
    try:
        dev = HapticEffect.open(vid_pid[0], vid_pid[1]) # try to open RHINO
        if args.reset:
            dev.resetEffects()
        dev_firmware_version = dev.get_firmware_version()
        if dev_firmware_version:
            logging.info(f"Rhino Firmware: {dev_firmware_version}")
            minver = re.sub(r'\D', '', min_firmware_version)
            devver = re.sub(r'\D', '', dev_firmware_version)
            if devver < minver:
                QMessageBox.warning(None, "Outdated Firmware", f"This version of TelemFFB requires Rhino Firmware version {min_firmware_version} or later.\n\nThe current version installed is {dev_firmware_version}\n\n\n Please update to avoid errors!")
    except Exception as e:
        QMessageBox.warning(None, "Cannot connect to Rhino", f"Unable to open Rhino HID at {args.device}\nError: {e}")
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

    window = MainWindow(settings_manager=settings_mgr)
    window.show()

    telem_manager = TelemManager(settings_manager=settings_mgr)
    telem_manager.start()

    telem_manager.telemetryReceived.connect(window.update_telemetry)

    dcs = NetworkThread(telem_manager, host="", port=34380)
    dcs_enabled = utils.sanitize_dict(config["system"]).get("dcs_enabled", None)

    if dcs_enabled or args.sim == "DCS":
        # check and install/update export lua script
        utils.install_export_lua()
        logging.info("Starting DCS Telemetry Listener")
        dcs.start()

    il2_mgr = IL2Manager()
    il2_port = utils.sanitize_dict(config["system"]).get("il2_telem_port", 34385)
    il2_path = utils.sanitize_dict(config["system"]).get("il2_path", 'C: \\Program Files\\IL-2 Sturmovik Great Battles')
    il2_validate = utils.sanitize_dict(config["system"]).get("il2_cfg_validation", True)
    il2 = NetworkThread(telem_manager, host="", port=il2_port, telem_parser=il2_mgr)

    il2_enabled = utils.sanitize_dict(config["system"]).get("il2_enabled", None)

    if il2_enabled or args.sim == "IL2":

        if il2_validate:
            utils.analyze_il2_config(il2_path, port=il2_port)
        else:
            logging.warning("IL2 Config validation is disabled - please ensure the IL2 startup.cfg is configured correctly")
        logging.info("Starting IL2 Telemetry Listener")
        il2.start()

    sim_connect = SimConnectSock(telem_manager)
    try:
        msfs = utils.sanitize_dict(config["system"]).get("msfs_enabled", None)
        logging.debug(f"MSFS={msfs}")
        if msfs or args.sim == "MSFS":
            logging.info("MSFS Enabled:  Starting Simconnect Manager")
            sim_connect.start()
    except:
        logging.exception("Error loading MSFS enable flag from config file")

    app.exec_()

    dcs.quit()
    il2.quit()
    sim_connect.quit()
    telem_manager.quit()


if __name__ == "__main__":
    main()
