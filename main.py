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

import glob
import shutil

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
parser.add_argument('-o', '--overridefile', type=str, help='User config override file (default = config.user.ini', default='None')
parser.add_argument('-s', '--sim', type=str, help='Set simulator options DCS|MSFS|IL2 (default DCS', default="None")
parser.add_argument('-t', '--type', help='FFB Device Type | joystick (default) | pedals | collective', default='joystick')
#parser.add_argument('-X', '--xml', help='use XML config', nargs='?', const='default')

args = parser.parse_args()

import json
import logging
import sys
import time
import os


sys.path.insert(0, '')
# sys.path.append('/simconnect')

script_dir = os.path.dirname(os.path.abspath(__file__))

if args.overridefile == 'None':
    # Need to determine if user is using default config.user.ini without passing the override flag:
    if os.path.isfile(os.path.join(script_dir, 'config.user.ini')):
        # re-set the override file argument var as if user had passed it
        args.overridefile = 'config.user.ini'
    else:
        pass


log_folder = os.path.join(os.environ['LOCALAPPDATA'],"VPForce-TelemFFB", 'log')
#log_folder = './log'
if not os.path.exists(log_folder):
    os.makedirs(log_folder)
if args.overridefile!= 'None':
    logname = "".join(["TelemFFB", "_", args.device.replace(":", "-"), "_", os.path.basename(args.configfile), "_",
                       os.path.basename(args.overridefile), ".log"])
else:
    logname = "".join(["TelemFFB", "_", args.device.replace(":", "-"), '_', args.type, "_xmlconfig.log"])
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

from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QMainWindow, QVBoxLayout, QMessageBox, QPushButton, QDialog, \
    QRadioButton, QListView, QScrollArea, QHBoxLayout, QAction, QPlainTextEdit, QMenu, QButtonGroup, QFrame, \
    QDialogButtonBox, QSizePolicy, QSpacerItem
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QCoreApplication, QUrl, QRect, QMetaObject, QSize, QByteArray
from PyQt5.QtGui import QFont, QPixmap, QIcon, QDesktopServices, QPainter, QColor, QPainterPath
from PyQt5.QtWidgets import QGridLayout, QToolButton, QStyle

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
import xmlutils

effects_translator = utils.EffectTranslator()


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

defaults_path = 'defaults.xml'
userconfig_rootpath = os.path.join(os.environ['LOCALAPPDATA'],"VPForce-TelemFFB")
userconfig_path = os.path.join(userconfig_rootpath , 'userconfig.xml')

utils.create_empty_userxml_file(userconfig_path)
# xmlutils.update_vars(args.type, userconfig_path, defaults_path)

version = utils.get_version()
min_firmware_version = 'v1.0.15'
global dev_firmware_version
dev_firmware_version = None
global dcs_telem, il2_telem, sim_connect_telem, settings_mgr, telem_manager

_update_available = False
_latest_version = None
_latest_url = None
_current_version = version
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
    tm = int(os.path.getmtime(defaults_path)) + int(os.path.getmtime(userconfig_path))
    # if args.overridefile== 'None':
    #     userfile=settings_mgr.userconfig_path
    #     tm = int(os.path.getmtime(userfile))
    # else:
    #     tm = int(os.path.getmtime(configfile))
    #     if os.path.exists(overridefile):
    #         tm += int(os.path.getmtime(overridefile))
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
    # a, b, main = xmlutils.read_single_model('Global', '')
    main = []
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
    if args.overridefile== 'None':
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
        self.setWindowTitle(f"Log Console ({args.type})")
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
    updateSettingsLayout = pyqtSignal()
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
        # self.main_window = main_window
        # self.settings_layout = self.main_window.settings_layout
        # self.main_window = main_window
        # settings_manager.show()

    def get_aircraft_config(self, aircraft_name, default_section=None):
        if args.overridefile== 'None':
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
        input_modeltype = ''
        try:
            if data_source == "MSFS2020":
                send_source = "MSFS"
            else:
                send_source = data_source
            # cls_name,pattern, result = settingsmanager.read_single_model(configfile, send_source, aircraft_name, args.type, userconfig_path=overridefile)

            if '.' in send_source:
                input = send_source.split('.')
                sim_temp = input[0]
                the_sim = sim_temp.replace('2020', '')
                input_modeltype = input[1]
            else:
                the_sim = send_source

            cls_name, pattern, result = xmlutils.read_single_model(the_sim, aircraft_name, input_modeltype)
            if cls_name == '': cls_name = 'Aircraft'
            for setting in result:
                k = setting['name']
                v = setting['value']
                u = setting['unit']
                if u is not None:
                    vu= v+u
                else:
                    vu = v
                if setting['value'] != '-':
                    params[k] = vu
                    logging.warning(f"Got from Settings Manager: {k} : {vu}")
                else:
                    logging.warning(f"Ignoring blank setting from Settings Manager: {k} : {vu}")
                # print(f"SETTING:\n{setting}")
            params = utils.sanitize_dict(params)
            self.settings_manager.update_state_vars(
                current_sim=the_sim,
                current_aircraft_name=aircraft_name,
                current_class=cls_name,
                current_pattern=pattern)


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
                    logging.info(f"Loading vpconf for aircraft with: {vpconf_path} -config {params['vpconf']} -serial {serial}")
                    subprocess.call([vpconf_path, "-config", params["vpconf"], "-serial", serial], cwd=workdir, env=env)

                logging.info(f"Creating handler for {aircraft_name}: {Class.__module__}.{Class.__name__}")
                # instantiate new aircraft handler
                self.currentAircraft = Class(aircraft_name)
                # self.currentAircraft.apply_settings(params)
                self.currentAircraft.apply_settings(params)
                if args.overridefile== 'None':
                    if settings_mgr.isVisible():
                        settings_mgr.b_getcurrentmodel.click()
                    # a,b,res = xmlutils.read_single_model(data_source, aircraft_name)
                    # self.main_window.settings_layout.build_rows(res)
                    # self.main_window.reload_button.click()
                    # self.main_window.settings_layout.reload_caller()
                    self.updateSettingsLayout.emit()


                # future :
                # pop create dialog on load where pattern is blank
                # currently pops on aircraft change but not initial load if sim already running

                # if settings_mgr.current_sim != 'Global':
                #     if settings_mgr.current_pattern == '':
                #         settings_mgr.b_createusermodel.click()

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
        # self.timeout = utils.sanitize_dict(_config["system"]).get("telemetry_timeout", 200)/1000
        self.timeout = int(utils.read_system_settings().get('telemTimeout', 200))/1000
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


class Ui_SystemDialog(object):
    def setupUi(self, SystemDialog):
        if not SystemDialog.objectName():
            SystemDialog.setObjectName(u"SystemDialog")
        SystemDialog.resize(490, 355)
        sizePolicy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(SystemDialog.sizePolicy().hasHeightForWidth())
        SystemDialog.setSizePolicy(sizePolicy)
        SystemDialog.setMinimumSize(QSize(490, 355))
        SystemDialog.setMaximumSize(QSize(490, 355))
        self.line = QFrame(SystemDialog)
        self.line.setObjectName(u"line")
        self.line.setGeometry(QRect(9, 102, 466, 16))
        font = QFont()
        font.setBold(False)
        font.setWeight(50)
        self.line.setFont(font)
        self.line.setLineWidth(2)
        self.line.setMidLineWidth(1)
        self.line.setFrameShape(QFrame.HLine)
        self.line.setFrameShadow(QFrame.Sunken)
        self.buttonBox = QDialogButtonBox(SystemDialog)
        self.buttonBox.setObjectName(u"buttonBox")
        self.buttonBox.setGeometry(QRect(310, 310, 156, 23))
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Cancel|QDialogButtonBox.Save)
        self.resetButton = QPushButton(SystemDialog)
        self.resetButton.setObjectName(u"resetButton")
        self.resetButton.setGeometry(QRect(20, 310, 101, 23))
        self.widget = QWidget(SystemDialog)
        self.widget.setObjectName(u"widget")
        self.widget.setGeometry(QRect(20, 19, 258, 71))
        self.gridLayout = QGridLayout(self.widget)
        self.gridLayout.setObjectName(u"gridLayout")
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(self.widget)
        self.label.setObjectName(u"label")

        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)

        self.logLevel = QComboBox(self.widget)
        self.logLevel.setObjectName(u"logLevel")

        self.gridLayout.addWidget(self.logLevel, 0, 1, 1, 1)

        self.label_2 = QLabel(self.widget)
        self.label_2.setObjectName(u"label_2")

        self.gridLayout.addWidget(self.label_2, 1, 0, 1, 1)

        self.telemTimeout = QLineEdit(self.widget)
        self.telemTimeout.setObjectName(u"telemTimeout")

        self.gridLayout.addWidget(self.telemTimeout, 1, 1, 1, 1)

        self.ignoreUpdate = QCheckBox(self.widget)
        self.ignoreUpdate.setObjectName(u"ignoreUpdate")

        self.gridLayout.addWidget(self.ignoreUpdate, 2, 0, 1, 2)

        self.widget1 = QWidget(SystemDialog)
        self.widget1.setObjectName(u"widget1")
        self.widget1.setGeometry(QRect(16, 133, 170, 65))
        self.verticalLayout = QVBoxLayout(self.widget1)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.enableDCS = QCheckBox(self.widget1)
        self.enableDCS.setObjectName(u"enableDCS")

        self.verticalLayout.addWidget(self.enableDCS)

        self.enableMSFS = QCheckBox(self.widget1)
        self.enableMSFS.setObjectName(u"enableMSFS")

        self.verticalLayout.addWidget(self.enableMSFS)

        self.enableIL2 = QCheckBox(self.widget1)
        self.enableIL2.setObjectName(u"enableIL2")

        self.verticalLayout.addWidget(self.enableIL2)

        self.widget2 = QWidget(SystemDialog)
        self.widget2.setObjectName(u"widget2")
        self.widget2.setGeometry(QRect(30, 204, 452, 75))
        self.il2_sub_layout = QGridLayout(self.widget2)
        self.il2_sub_layout.setObjectName(u"il2_sub_layout")
        self.il2_sub_layout.setContentsMargins(0, 0, 0, 0)
        self.validateIL2 = QCheckBox(self.widget2)
        self.validateIL2.setObjectName(u"validateIL2")

        self.il2_sub_layout.addWidget(self.validateIL2, 0, 0, 1, 1)

        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.label_3 = QLabel(self.widget2)
        self.label_3.setObjectName(u"label_3")
        sizePolicy1 = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.label_3.sizePolicy().hasHeightForWidth())
        self.label_3.setSizePolicy(sizePolicy1)
        self.label_3.setMinimumSize(QSize(98, 0))

        self.horizontalLayout.addWidget(self.label_3)

        self.pathIL2 = QLineEdit(self.widget2)
        self.pathIL2.setObjectName(u"pathIL2")

        self.horizontalLayout.addWidget(self.pathIL2)

        self.browseIL2 = QToolButton(self.widget2)
        self.browseIL2.setObjectName(u"browseIL2")

        self.horizontalLayout.addWidget(self.browseIL2)


        self.il2_sub_layout.addLayout(self.horizontalLayout, 1, 0, 1, 1)

        self.horizontalLayout_2 = QHBoxLayout()
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.label_4 = QLabel(self.widget2)
        self.label_4.setObjectName(u"label_4")

        self.horizontalLayout_2.addWidget(self.label_4)

        self.portIL2 = QLineEdit(self.widget2)
        self.portIL2.setObjectName(u"portIL2")

        self.horizontalLayout_2.addWidget(self.portIL2)

        self.horizontalSpacer = QSpacerItem(279, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.horizontalLayout_2.addItem(self.horizontalSpacer)


        self.il2_sub_layout.addLayout(self.horizontalLayout_2, 2, 0, 1, 1)


        self.retranslateUi(SystemDialog)

        QMetaObject.connectSlotsByName(SystemDialog)
    # setupUi

    def retranslateUi(self, SystemDialog):
        SystemDialog.setWindowTitle(QCoreApplication.translate("SystemDialog", u"System Settings", None))
        self.resetButton.setText(QCoreApplication.translate("SystemDialog", u"Reset to  Defaults", None))
        self.label.setText(QCoreApplication.translate("SystemDialog", u"System Logging Level:", None))
        self.label_2.setText(QCoreApplication.translate("SystemDialog", u"Telemetry Timeout (ms):", None))
        self.ignoreUpdate.setText(QCoreApplication.translate("SystemDialog", u"Disable Update Prompt on Startup", None))
        self.enableDCS.setText(QCoreApplication.translate("SystemDialog", u"Enable DCS World Support", None))
        self.enableMSFS.setText(QCoreApplication.translate("SystemDialog", u"Enable MSFS 2020 Support", None))
        self.enableIL2.setText(QCoreApplication.translate("SystemDialog", u"Enable IL-2 Sturmovik Support", None))
        self.validateIL2.setText(QCoreApplication.translate("SystemDialog", u"Auto IL-2 Telemetry setup", None))
        self.label_3.setText(QCoreApplication.translate("SystemDialog", u"IL-2 Install Path:", None))
        self.browseIL2.setText(QCoreApplication.translate("SystemDialog", u"...", None))
        self.label_4.setText(QCoreApplication.translate("SystemDialog", u"IL-2 Telemetry Port:", None))
    # retranslateUi





class SystemSettingsDialog(QDialog, Ui_SystemDialog):
    def __init__(self, parent=None):
        super(SystemSettingsDialog, self).__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)

        # Add "INFO" and "DEBUG" options to the logLevel combo box
        self.logLevel.addItems(["INFO", "DEBUG"])

        # Connect signals to slots
        self.enableIL2.stateChanged.connect(self.toggle_il2_widgets)
        self.browseIL2.clicked.connect(self.select_il2_directory)
        self.buttonBox.accepted.connect(self.save_settings)
        self.resetButton.clicked.connect(self.reset_settings)
        self.buttonBox.rejected.connect(self.close)

        # Set initial state
        self.toggle_il2_widgets()
        self.parent_window = parent
        # Load settings from the registry and update widget states
        self.load_settings()

    def reset_settings(self):
        # Load default settings and update widgets
        # default_settings = utils.get_default_sys_settings()
        self.load_settings(default=True)
    def toggle_il2_widgets(self):
        # Show/hide IL-2 related widgets based on checkbox state
        il2_enabled = self.enableIL2.isChecked()
        self.validateIL2.setVisible(il2_enabled)
        self.label_3.setVisible(il2_enabled)
        self.pathIL2.setVisible(il2_enabled)
        self.browseIL2.setVisible(il2_enabled)
        self.label_4.setVisible(il2_enabled)
        self.portIL2.setVisible(il2_enabled)

    def select_il2_directory(self):
        # Open a directory dialog and set the result in the pathIL2 QLineEdit
        directory = QFileDialog.getExistingDirectory(self, "Select IL-2 Install Path", "")
        if directory:
            self.pathIL2.setText(directory)

    def save_settings(self):
        # Create a dictionary with the values of all components
        settings_dict = {
            "logLevel": self.logLevel.currentText(),
            "telemTimeout": int(self.telemTimeout.text()),
            "ignoreUpdate": self.ignoreUpdate.isChecked(),
            "enableDCS": self.enableDCS.isChecked(),
            "enableMSFS": self.enableMSFS.isChecked(),
            "enableIL2": self.enableIL2.isChecked(),
            "validateIL2": self.validateIL2.isChecked(),
            "pathIL2": self.pathIL2.text(),
            "portIL2": int(self.portIL2.text()),
        }

        # Save settings to the registry
        for key, value in settings_dict.items():
            utils.set_reg(key, value)
        stop_sims()
        init_sims()
        self.parent_window.init_sim_indicators(['DCS', 'MSFS', 'IL2'], settings_dict)

        self.accept()

    def load_settings(self, default=False):
        """
        Load settings from the registry and update widget states.
        """
        if default:
            settings_dict = utils.get_default_sys_settings()
        else:
            # Read settings from the registry
            settings_dict = utils.read_system_settings()

        # Update widget states based on the loaded settings
        if 'logLevel' in settings_dict:
            log_level = settings_dict['logLevel']
            self.logLevel.setCurrentText(log_level)

        if 'telemTimeout' in settings_dict:
            telem_timeout = settings_dict['telemTimeout']
            self.telemTimeout.setText(str(telem_timeout))

        if 'ignoreUpdate' in settings_dict:
            ignore_update = settings_dict['ignoreUpdate']
            self.ignoreUpdate.setChecked(ignore_update)

        if 'enableDCS' in settings_dict:
            dcs_enabled = settings_dict['enableDCS']
            self.enableDCS.setChecked(dcs_enabled)

        if 'enableMSFS' in settings_dict:
            msfs_enabled = settings_dict['enableMSFS']
            self.enableMSFS.setChecked(msfs_enabled)

        if 'enableIL2' in settings_dict:
            il2_enabled = settings_dict['enableIL2']
            self.enableIL2.setChecked(il2_enabled)
            self.toggle_il2_widgets()

        if 'validateIL2' in settings_dict:
            il2_validate = settings_dict['validateIL2']
            self.validateIL2.setChecked(il2_validate)

        if 'pathIL2' in settings_dict:
            il2_install_path = settings_dict['pathIL2']
            self.pathIL2.setText(il2_install_path)

        if 'portIL2' in settings_dict:
            il2_port = settings_dict['portIL2']
            self.portIL2.setText(str(il2_port))

class MainWindow(QMainWindow):
    def __init__(self, settings_manager):
        super().__init__()
        # Get the absolute path of the script's directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        doc_url = 'https://docs.google.com/document/d/1YL5DLkiTxlaNx_zKHEYSs25PjmGtQ6_WZDk58_SGt8Y/edit#heading=h.27yzpife8719'
        dl_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=A'
        notes_url = os.path.join(script_dir, '_RELEASE_NOTES.txt')
        self.settings_layout = SettingsLayout(parent=self, mainwindow=self)
        match args.type:
            case 'joystick':
                x_pos = 150
                y_pos = 130
            case 'pedals':
                x_pos = 100
                y_pos = 100
            case 'collective':
                x_pos = 50
                y_pos = 70

        self.setGeometry(x_pos, y_pos, 400, 700)
        if version:
            self.setWindowTitle(f"TelemFFB ({args.type}) ({version})")
        else:
            self.setWindowTitle(f"TelemFFB")
        global _update_available
        global _latest_version, _latest_url
        self.resize(400, 700)

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
        # Add the "System" menu and its sub-option
        system_menu = menubar.addMenu('System')

        system_settings_action = QAction('System Settings', self)
        system_settings_action.triggered.connect(self.open_system_settings_dialog)
        system_menu.addAction(system_settings_action)

        # Create the "Utilities" menu
        utilities_menu = menubar.addMenu('Utilities')

        # Add the "Reset" action to the "Utilities" menu
        reset_action = QAction('Reset All Effects', self)
        reset_action.triggered.connect(self.reset_all_effects)
        utilities_menu.addAction(reset_action)

        download_action = QAction('Download Other Versions', self)
        download_action.triggered.connect(lambda: self.open_url(dl_url))
        utilities_menu.addAction(download_action)


        update_action = QAction('Update TelemFFB', self)
        update_action.triggered.connect(self.update_from_menu)
        utilities_menu.addAction(update_action)
        if utils.fetch_latest_version():
            update_action.setDisabled(False)
        else:
            update_action.setDisabled(True)

        reset_geometry = QAction('Reset Window Size/Position', self)
        reset_geometry.triggered.connect(self.reset_window_size)
        utilities_menu.addAction(reset_geometry)
        # menubar.setStyleSheet("QMenu::item:selected { color: red; }")


        # Add settings converter
        if args.overridefile != 'None':
            convert_settings_action = QAction('Convert user config.ini to XML', self)
            convert_settings_action.triggered.connect(self.convert_settings)
            utilities_menu.addAction(convert_settings_action)

        help_menu = menubar.addMenu('Help')

        notes_action = QAction('Release Notes', self)
        notes_action.triggered.connect(lambda url=notes_url: self.open_file(url))
        help_menu.addAction(notes_action)

        docs_action = QAction('Documentation', self)
        docs_action.triggered.connect(lambda: self.open_url(doc_url))
        help_menu.addAction(docs_action)

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

        # Construct the absolute path of the icon file
        icon_path = os.path.join(script_dir, "image/vpforceicon.png")
        self.setWindowIcon(QIcon(icon_path))

        # self.notes_label = QLabel()

        # # self.notes_label.setOpenExternalLinks(True)
        #
        # label_txt = 'Release Notes'
        # # Connect the linkActivated signal to the open_file method and pass the URL
        # self.notes_label.linkActivated.connect(lambda url=notes_url: self.open_file(url))
        #
        # self.notes_label.setText(f'<a href="{notes_url}">{label_txt}</a>')
        # self.notes_label.setAlignment(Qt.AlignRight)
        # self.notes_label.setToolTip(notes_url)
        #
        # notes_row_layout.addWidget(self.notes_label)
        # layout.addLayout(notes_row_layout)
        cfg = get_config()
        dcs_enabled = utils.read_system_settings().get('enableDCS')
        il2_enabled = utils.read_system_settings().get('enableIL2')
        msfs_enabled = utils.read_system_settings().get('enableMSFS')

        self.icon_size = QSize(18, 18)
        if args.sim == "DCS" or dcs_enabled:
            dcs_color = QColor(255,255,0)
            dcs_icon = self.create_colored_icon(dcs_color, self.icon_size)
        else:
            dcs_color = QColor(128,128,128)
            dcs_icon = self.create_x_icon(dcs_color, self.icon_size)

        if args.sim == "MSFS" or msfs_enabled:
            msfs_color = QColor(255, 255, 0)
            msfs_icon = self.create_colored_icon(msfs_color, self.icon_size)
        else:
            msfs_color = QColor(128,128,128)
            msfs_icon = self.create_x_icon(dcs_color, self.icon_size)

        if args.sim == "IL2" or il2_enabled:
            il2_color = QColor(255, 255, 0)
            il2_icon = self.create_colored_icon(il2_color, self.icon_size)
        else:
            il2_color = QColor(128,128,128)
            il2_icon = self.create_x_icon(dcs_color, self.icon_size)

        # xplane_color = QColor(128,128,128)
        # condor_color = QColor(128, 128, 128)

        logo_status_layout = QHBoxLayout()

        # Add a label for the image
        # Construct the absolute path of the image file
        image_path = os.path.join(script_dir, "image/vpforcelogo.png")
        self.image_label = QLabel()
        pixmap = QPixmap(image_path)
        self.image_label.setPixmap(pixmap)

        # Add the image label to the layout
        logo_status_layout.addWidget(self.image_label, alignment=Qt.AlignTop | Qt.AlignLeft)
        # layout.addWidget(QLabel(f"Config File: {args.configfile}"))



        status_layout = QGridLayout()
        self.dcs_label_icon = QLabel("", self)

        self.dcs_label_icon.setPixmap(dcs_icon)
        self.dcs_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dcs_label = QLabel("DCS", self)
        status_layout.addWidget(self.dcs_label_icon, 0, 0)
        status_layout.addWidget(dcs_label, 0, 1)

        self.il2_label_icon = QLabel("", self)
        self.il2_label_icon.setPixmap(il2_icon)
        self.il2_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        il2_label = QLabel("IL2", self)
        status_layout.addWidget(self.il2_label_icon, 1, 0)
        status_layout.addWidget(il2_label, 1, 1)

        self.msfs_label_icon = QLabel("", self)
        self.msfs_label_icon.setPixmap(msfs_icon)
        self.msfs_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        msfs_label = QLabel("MSFS", self)
        status_layout.addWidget(self.msfs_label_icon, 2, 0)
        status_layout.addWidget(msfs_label, 2, 1)

        # self.xplane_label_icon = QLabel("", self)
        # xplane_icon = self.create_colored_icon(xplane_color, self.icon_size)
        # self.xplane_label_icon.setPixmap(xplane_icon)
        # self.xplane_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # xplane_label = QLabel("XPlane", self)
        # status_layout.addWidget(self.xplane_label_icon, 0, 2)
        # status_layout.addWidget(xplane_label, 0, 3)
        #
        # self.condor_label_icon = QLabel("", self)
        # condor_icon = self.create_colored_icon(condor_color, self.icon_size)
        # self.condor_label_icon.setPixmap(condor_icon)
        # self.condor_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # condor_label = QLabel("Condor2", self)
        # status_layout.addWidget(self.condor_label_icon, 1, 2)
        # status_layout.addWidget(condor_label, 1, 3)

        logo_status_layout.addLayout(status_layout)


        layout.addLayout(logo_status_layout)


        cfg_layout = QHBoxLayout()
        self.cfg_label = QLabel()

        ###########
        #  radio buttons

        self.radio_button_group = QButtonGroup()
        radio_row_layout = QHBoxLayout()
        self.settings_radio = QRadioButton("Settings")
        self.telem_monitor_radio = QRadioButton("Telemetry Monitor")
        self.effect_monitor_radio = QRadioButton("Effects Monitor")
        self.hide_scroll_area = QRadioButton("Hide")

        radio_row_layout.addWidget(self.settings_radio)
        radio_row_layout.addWidget(self.telem_monitor_radio)
        radio_row_layout.addWidget(self.effect_monitor_radio)
        radio_row_layout.addWidget(self.hide_scroll_area)

        self.settings_radio.setChecked(True)

        self.radio_button_group.addButton(self.settings_radio)
        self.radio_button_group.addButton(self.telem_monitor_radio)
        self.radio_button_group.addButton(self.effect_monitor_radio)
        self.radio_button_group.addButton(self.hide_scroll_area)

        # self.radio_button_group.buttonClicked.connect(self.update_monitor_window)

        layout.addLayout(radio_row_layout)

        ############
        # current craft

        current_craft_area = QWidget()
        current_craft_layout = QHBoxLayout()
        cur_sim = QLabel()
        cur_sim.setText("Current Aircraft:")
        cur_sim.setAlignment(Qt.AlignRight)
        cur_sim.setMaximumWidth(80)
        current_craft_layout.addWidget(cur_sim)
        self.cur_craft = QLabel()
        self.cur_craft.setText('Unknown')
        current_craft_layout.addWidget(self.cur_craft)
        self.current_pattern = QLabel()
        self.current_pattern.setText('(No Match)')
        self.current_pattern.setAlignment(Qt.AlignRight)
        current_craft_layout.addWidget(self.current_pattern)
        current_craft_area.setLayout(current_craft_layout)
        layout.addWidget(current_craft_area)

        show_craft_loader = True
        if show_craft_loader:
            test_craft_area = QWidget()
            test_craft_layout = QHBoxLayout()
            test_sim_lbl = QLabel('Sim:')
            test_sim_lbl.setMaximumWidth(30)
            test_sim_lbl.setAlignment(Qt.AlignRight)
            sims = ['', 'DCS', 'IL2', 'MSFS']
            self.test_sim = QComboBox()
            self.test_sim.setMaximumWidth(60)
            self.test_sim.addItems(sims)
            self.test_sim.currentIndexChanged.connect(self.test_sim_changed)
            test_name_lbl = QLabel('Aircraft Name:')
            test_name_lbl.setMaximumWidth(90)
            test_name_lbl.setAlignment(Qt.AlignRight)
            self.test_name = QComboBox()
            self.test_name.setMinimumWidth(100)
            self.test_name.setEditable(True)
            test_button = QToolButton()
            test_button.setMaximumWidth(20)
            test_button.setText('>')
            test_button.clicked.connect(self.force_sim_aircraft)
            test_craft_layout.addWidget(test_sim_lbl)
            test_craft_layout.addWidget(self.test_sim)
            test_craft_layout.addWidget(test_name_lbl)
            test_craft_layout.addWidget(self.test_name)
            test_craft_layout.addWidget(test_button)
            test_craft_area.setLayout(test_craft_layout)
            layout.addWidget(test_craft_area)

        ################
        #  main scroll area

        self.monitor_area = QScrollArea()
        self.monitor_area.setWidgetResizable(True)
        self.monitor_area.setMinimumHeight(100)

        # Create the QLabel widget and set its properties
        if cfg.get("EXCEPTION"):
            error = cfg["EXCEPTION"]["ERROR"]
            logging.error(f"CONFIG ERROR: {error}")
            self.lbl_telem_data = QLabel(f"CONFIG ERROR: {error}")
            QMessageBox.critical(None, "CONFIG ERROR", f"Error: {error}")
        else:
            dcs_enabled = utils.read_system_settings().get('enableDCS')
            il2_enabled = utils.read_system_settings().get('enableIL2')
            msfs_enabled = utils.read_system_settings().get('enableMSFS')

            # Convert True/False to "enabled" or "disabled"
            dcs_status = "Enabled" if dcs_enabled else "Disabled"
            il2_status = "Enabled" if il2_enabled else "Disabled"
            msfs_status = "Enabled" if msfs_enabled else "Disabled"

            self.lbl_telem_data = QLabel(
                f"Waiting for data...\n\n"
                f"DCS Enabled: {dcs_status}\n"
                f"IL2 Enabled: {il2_status}\n"
                f"MSFS Enabled: {msfs_status}\n\n"
                "Enable or Disable in System -> System Settings"
            )
        self.lbl_telem_data.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_telem_data.setWordWrap(True)

        # Set the QLabel widget as the widget inside the scroll area
        self.monitor_area.setWidget(self.lbl_telem_data)

        # Add the scroll area to the layout
        layout.addWidget(self.monitor_area)

        # Create a scrollable area
        self.settings_area = QScrollArea()
        self.settings_area.setWidgetResizable(True)

        ##############
        # settings

        # Create a widget to hold the layout
        scroll_widget = QWidget()
        # self.settings_layout = settings_layout
        # settings_layout = SettingsLayout()
        scroll_widget.setLayout(self.settings_layout)

        # Add the grid layout to the main layout
        self.settings_area.setWidget(scroll_widget)

        # Set the widget as the content of the scroll area
        layout.addWidget(self.settings_area)

        ##############
        #  buttons

        # test buttons
        show_clear_reload = False
        if show_clear_reload:
            test_layout = QHBoxLayout()
            clear_button = QPushButton('clear')
            clear_button.clicked.connect(self.settings_layout.clear_layout)
            test_layout.addWidget(clear_button)
            self.reload_button = QPushButton('reload')
            self.reload_button.clicked.connect(self.settings_layout.reload_caller)
            test_layout.addWidget(self.reload_button)
            layout.addLayout(test_layout)

        button_layout = QHBoxLayout()

        if args.overridefile!= 'None':
            edit_button = QPushButton("Edit Config File")
            edit_button.setMinimumWidth(200)
            edit_button.setMaximumWidth(200)

            button_layout.addWidget(edit_button, alignment=Qt.AlignCenter)

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
            edit_button.setMinimumWidth(130)
            edit_button.setMaximumWidth(200)
            edit_button.clicked.connect(self.toggle_settings_window)
            button_layout.addWidget(edit_button, alignment=Qt.AlignCenter)

        self.log_button = QPushButton("Open/Hide Log")
        self.log_button.setMinimumWidth(130)
        self.log_button.setMaximumWidth(200)
        self.log_button.clicked.connect(self.toggle_log_window)
        button_layout.addWidget(self.log_button, alignment=Qt.AlignCenter)

        # Add the exit button
        exit_button = QPushButton("Exit")
        exit_button.setMinimumWidth(130)  # Set the minimum width
        exit_button.setMaximumWidth(200)  # Set the maximum width
        exit_button.clicked.connect(self.exit_application)
        button_layout.addWidget(exit_button, alignment=Qt.AlignCenter)

        layout.addLayout(button_layout)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        self.layout = QVBoxLayout(central_widget)

        # show xml file info
        if args.overridefile!= 'None':
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

            self.ovrd_label = ClickableLabel()
            if os.path.exists(userconfig_path):
                self.ovrd_label.setText(f"User File: {userconfig_path}")

            self.ovrd_label.setToolTip("Use the Settings Manager to customize aircraft settings.\nClick to open the userconfig directory if you need to send the file for support.")


        self.ovrd_label.setAlignment(Qt.AlignLeft)
        self.cfg_label.setAlignment(Qt.AlignLeft)


        cfg_layout.addWidget(self.ovrd_label)

        layout.addLayout(cfg_layout)

        # link_row_layout = QHBoxLayout()
        # self.doc_label = QLabel()
        #
        # label_txt = 'TelemFFB Documentation'
        # self.doc_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        # self.doc_label.setOpenExternalLinks(True)
        # self.doc_label.setText(f'<a href="{doc_url}">{label_txt}</a>')
        # self.doc_label.setToolTip(doc_url)
        # self.dl_label = QLabel()
        #
        # label_txt = 'Download Latest'
        # self.dl_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        # self.dl_label.setOpenExternalLinks(True)
        # self.dl_label.setText(f'<a href="{dl_url}">{label_txt}</a>')
        # self.dl_label.setAlignment(Qt.AlignRight)
        # self.dl_label.setToolTip(dl_url)
        #
        # link_row_layout.addWidget(self.doc_label)
        # link_row_layout.addWidget(self.dl_label)

        # layout.addLayout(link_row_layout)


        version_row_layout = QHBoxLayout()
        self.version_label = QLabel()

        status_text = "UNKNOWN"
        status = utils.fetch_latest_version()
        if status == False:
            status_text = "Up To Date"
        elif status == None:
            status_text = "UNKNOWN"
        else:
            # print(_update_available)
            _update_available = True
            _latest_version, _latest_url = status
            logging.info(f"<<<<Update available - new version={_latest_version}>>>>")

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



        layout.addLayout(version_row_layout)

        central_widget.setLayout(layout)
        self.load_main_window_geometry()

    def test_sim_changed(self):
        models = xmlutils.read_models(self.test_sim.currentText())
        self.test_name.blockSignals(True)
        self.test_name.clear()
        self.test_name.addItems(models)
        self.test_name.blockSignals(False)

    def closeEvent(self, event):
        # Perform cleanup before closing the application
        self.exit_application()
    def reset_window_size(self):
        match args.type:
            case 'joystick':
                x_pos = 150
                y_pos = 130
            case 'pedals':
                x_pos = 100
                y_pos = 100
            case 'collective':
                x_pos = 50
                y_pos = 70

        self.setGeometry(x_pos, y_pos, 400, 700)
    def load_main_window_geometry(self):
        device_type = args.type
        if device_type == 'joystick':
            reg_key = 'jWindowGeometry'
        elif device_type == 'pedals':
            reg_key = 'pWindowGeometry'
        elif device_type == 'collective':
            reg_key = 'cWindowGeometry'

        geometry = utils.get_reg(reg_key)
        if geometry is not None:
            q_geometry = QByteArray(utils.get_reg(reg_key))
            self.restoreGeometry(q_geometry)
    def save_main_window_geometry(self):
        # Capture the main window's geometry
        device_type = args.type
        geometry = self.saveGeometry()
        geometry_bytes = bytes(geometry)
        if device_type == 'joystick':
            reg_key = 'jWindowGeometry'
        elif device_type == 'pedals':
            reg_key = 'pWindowGeometry'
        elif device_type == 'collective':
            reg_key = 'cWindowGeometry'
        # Extract position and size
        # x, y, width, height = geometry.x(), geometry.y(), geometry.width(), geometry.height()
        # geometry_string = f"{x},{y},{width},{height}"
        # Store the values in the registry
        utils.set_reg(reg_key, geometry_bytes)

    def force_sim_aircraft(self):
        settings_mgr.current_sim = self.test_sim.currentText()
        settings_mgr.current_aircraft_name = self.test_name.currentText()
        self.settings_layout.expanded_items.clear()
        self.monitor_area.hide()
        self.settings_layout.reload_caller()

    def open_system_settings_dialog(self):
        dialog = SystemSettingsDialog(self)
        dialog.exec_()

    def update_settings(self):
        self.settings_layout.reload_caller()

    def show_sub_menu(self):
        edit_button = self.sender()
        self.sub_menu.popup(edit_button.mapToGlobal(edit_button.rect().bottomLeft()))

    def open_file(self, url):
        try:
            file_url = QUrl.fromLocalFile(url)
            QDesktopServices.openUrl(file_url)
        except Exception as e:
            logging.error(f"There was an error opening the file: {str(e)}")

    def open_url(self, url):

        # Open the URL
        QDesktopServices.openUrl(QUrl(url))

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

        message = f'\n\nConverted {overridefile} for {args.type} to XML.\nYou can now remove the -o argument to use the new Settings Manager\n'
        print(message)

    def create_colored_icon(self, color, size):
        # Create a QPixmap with the specified color and size
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)
        painter.end()

        return pixmap

    def create_x_icon(self, color, size):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)

        # Draw two vertical lines for the pause icon
        line_length = int(size.width() / 3)
        line_width = 1
        line1_x = int((size.width() / 2) - 2)
        line2_x = int((size.width() / 2) + 2)
        line_y = int((size.height() - line_length) / 2)

        painter.setPen(QColor(Qt.white))
        painter.drawLine(line1_x, line_y, line2_x, line_y + line_length)
        painter.drawLine(line2_x, line_y, line1_x, line_y + line_length)

        painter.end()

        return pixmap
    def create_paused_icon(self, color, size):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)

        # Draw two vertical lines for the pause icon
        line_length = int(size.width() / 3)
        line_width = 1
        line1_x = int((size.width()  / 2)- 2)
        line2_x = int((size.width()  / 2)+ 2)
        line_y = int((size.height() - line_length) / 2)

        painter.setPen(QColor(Qt.white))
        painter.drawLine(line1_x, line_y, line1_x, line_y + line_length)
        painter.drawLine(line2_x, line_y, line2_x, line_y + line_length)

        painter.end()

        return pixmap

    def create_x_icon(self, color, size):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)

        # Draw two vertical lines for the pause icon
        line_length = int(size.width() / 3)
        line_width = 1
        line1_x = int((size.width() / 2) - 2)
        line2_x = int((size.width() / 2) + 2)
        line_y = int((size.height() - line_length) / 2)

        painter.setPen(QColor(Qt.white))
        painter.drawLine(line1_x, line_y, line2_x, line_y + line_length)
        painter.drawLine(line2_x, line_y, line1_x, line_y + line_length)

        painter.end()

        return pixmap

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
            print (f"# toggle settings window   {settings_mgr.current_sim} {settings_mgr.current_aircraft_name}")
            settings_mgr.currentmodel_click()

    def exit_application(self):
        # Perform any cleanup or save operations here
        self.save_main_window_geometry()
        QCoreApplication.instance().quit()

    def update_from_menu(self):
        if perform_update(auto=False):
            QCoreApplication.instance().quit()

    def init_sim_indicators(self, sims, settings_dict):
        label_icons = {
            'DCS': self.dcs_label_icon,
            'IL2': self.il2_label_icon,
            'MSFS': self.msfs_label_icon,
        }
        enable_color = QColor(255, 255, 0)
        disable_color = QColor(128, 128, 128)
        enable_color = self.create_colored_icon(enable_color, self.icon_size)
        disable_color = self.create_x_icon(disable_color, self.icon_size)
        for sim in sims:
            state = settings_dict.get(f"enable{sim}")
            lb = label_icons[sim]
            if state:
                lb.setPixmap(enable_color)
            else:
                lb.setPixmap(disable_color)
    def update_sim_indicators(self, source, state):
        active_color = QColor(0, 255, 0)
        paused_color = QColor(0, 0, 255)
        active_icon = self.create_colored_icon(active_color, self.icon_size)
        paused_icon = self.create_paused_icon(paused_color, self.icon_size)
        if state:
            match source:
                case 'DCS':
                    self.dcs_label_icon.setPixmap(active_icon)
                case 'IL2':
                    self.il2_label_icon.setPixmap(active_icon)
                case 'MSFS2020':
                    self.msfs_label_icon.setPixmap(active_icon)
        else:
            match source:
                case 'DCS':
                    self.dcs_label_icon.setPixmap(paused_icon)
                case 'IL2':
                    self.il2_label_icon.setPixmap(paused_icon)
                case 'MSFS2020':
                    self.msfs_label_icon.setPixmap(paused_icon)

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


            self.update_sim_indicators(data.get('src'), data.get('SimPaused', 0))


            self.cur_craft.setText(data['N'])
            self.current_pattern.setText(f"({settings_mgr.current_pattern})")

            if window_mode == self.telem_monitor_radio:
                self.monitor_area.show()
                self.settings_area.hide()
                self.lbl_telem_data.setText(items)
                self.lbl_telem_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            elif window_mode == self.effect_monitor_radio:
                self.monitor_area.show()
                self.settings_area.hide()
                self.lbl_telem_data.setText(active_effects)
                self.lbl_telem_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            elif window_mode == self.settings_radio:
                self.monitor_area.hide()
                self.settings_area.show()
            elif window_mode == self.hide_scroll_area:
                self.monitor_area.hide()
                self.settings_area.hide()


        except Exception as e:
            traceback.print_exc()

class SettingsLayout(QGridLayout):
    expanded_items = []
    prereq_list = []
    show_slider_debug = False
    show_order_debug = True
    bump_up = True

    chk_col = 0
    exp_col = 1
    lbl_col = 2
    entry_col = 3
    unit_col = 4
    val_col = 5
    erase_col = 6
    fct_col = 10
    ord_col = 11

    def __init__(self, parent=None, mainwindow=None):
        super(SettingsLayout, self).__init__(parent)
        result = None
        if settings_mgr.current_sim != 'nothing':
            a,b,result = xmlutils.read_single_model(settings_mgr.current_sim, settings_mgr.current_aircraft_name)
        #a, b, result = xmlutils.read_single_model('MSFS', 'Cessna 172')

        self.mainwindow = mainwindow
        if result is not None:
            self.build_rows(result)


    def append_prereq_count (self, datalist):
        for item in datalist:
            item['prereq_count'] = ''
            item['has_expander'] = ''
            for pr in self.prereq_list:
                if item['name'] == pr['prereq']:
                    item['prereq_count'] = pr['count']
            p_count = 0
            if item['prereq_count'] != '':
                p_count = int(item['prereq_count'])

            if p_count > 1 or (p_count == 1 and item['hasbump'] != 'true'):
                item['has_expander'] = 'true'

    def has_bump(self,datalist):
        for item in datalist:
            item['hasbump'] = ''
            bumped_up = item['order'][-2:] == '.1'
            if bumped_up:
                for b in datalist:
                    if item['prereq'] == b['name']:
                        b['hasbump'] = 'true'

    def add_expanded (self, datalist):
        for item in datalist:
            item['parent_expanded'] = ''
            for exp in self.expanded_items:
                if item['prereq'] == exp:
                    item['parent_expanded'] = 'true'
                else:
                    item['parent_expanded'] = 'false'

    def is_visible (self,datalist):

        for item in datalist:
            bumped_up = item['order'][-2:] == '.1'
            iv = 'false'
            cond =''
            if item['prereq'] == '':
                iv = 'true'
                cond = 'no prereq needed'
            else:
                for p in datalist:
                    if item['prereq'] == p['name']:
                        if p['value'] == 'true':
                            if p['has_expander'] == 'true':
                                if p['name'] in self.expanded_items:
                                    iv = 'true'
                                    cond = 'item parent expanded'
                                else:
                                    if p['hasbump'] == 'true':
                                        if bumped_up:
                                            iv = 'true'
                                            cond = 'parent hasbump & bumped'
                            else:
                                if p['is_visible']=='true':
                                    if p['hasbump'] == 'true':
                                        if bumped_up:
                                            iv = 'true'
                                            cond = 'parent hasbump & bumped no expander par vis'
                        break


            item['is_visible'] = iv
            if iv == 'true':
                print (f"{item['displayname']} visible because {cond}")

    def eliminate_invisible(self,datalist):
        newlist = []
        for item in datalist:
            if item['is_visible'] == 'true':
                newlist.append(item)

        for item in newlist:
            item['prereq_count'] = '0'
            pcount = 0
            for row in newlist:
                if row['prereq'] == item['name']:
                    pcount += 1
                    if row['has_expander'] == 'true':
                        pcount -= 1
            item['prereq_count'] = str(pcount)

        return newlist

    def build_rows(self,datalist):
        sorted_data = sorted(datalist, key=lambda x: float(x['order']))
        self.prereq_list = xmlutils.read_prereqs()
        self.has_bump(sorted_data)
        self.append_prereq_count(sorted_data)
        self.add_expanded(sorted_data)
        self.is_visible(sorted_data)
        newlist = self.eliminate_invisible(sorted_data)

        def is_expanded (item):
            if item['name'] in self.expanded_items:
                return True
            for row in sorted_data:
                if row['name'] == item['prereq'] and is_expanded(row):
                    return True
            return False

        i = 0
        for item in newlist:
            bumped_up = item['order'][-2:] == '.1'
            rowdisabled = False
            addrow = False
            is_expnd = is_expanded(item)
            print(f"{item['order']} - {item['value']} - b {bumped_up} - hb {item['hasbump']} - ex {is_expnd} - hs {item['has_expander']} - pex {item['parent_expanded']} - iv {item['is_visible']} - pcount {item['prereq_count']} - {item['displayname']} - pr {item['prereq']}")
            if item['is_visible'] == 'true':
                i += 1
                if bumped_up:
                    if self.bump_up:  # debug
                        i -= 1   # bump .1 setting onto the enable row
                self.generate_settings_row(item, i, rowdisabled)

        spacerItem = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.addItem(spacerItem, i+1, 1, 1, 1)
        print (f"{i} rows with {self.count()} widgets")

    def reload_caller(self):
        self.reload_layout(None)

    def reload_layout(self, result=None):
        self.clear_layout()
        if result is None:
            cls,pat,result = xmlutils.read_single_model(settings_mgr.current_sim, settings_mgr.current_aircraft_name)
            settings_mgr.current_pattern = pat
        if result is not None:
            self.build_rows(result)

    def clear_layout(self):
        print (f"clear_layout - count: {self.count()}")
        while self.count():
            item = self.takeAt(0)
            widget = item.widget()
            if widget:
                self.removeWidget(widget)
                widget.deleteLater()
        print(f"clear_layout - count: {self.count()}")

    def generate_settings_row(self, item, i, rowdisabled=False):
        entry_colspan = 2
        validvalues = item['validvalues'].split(',')

        if self.show_order_debug:
            order_lbl = QLabel()
            order_lbl.setText(item['order'])
            order_lbl.setMaximumWidth(30)
            self.addWidget(order_lbl, i, self.ord_col)

        # booleans get a checkbox
        if item['datatype'] == 'bool':
            checkbox = QCheckBox("")
            checkbox.setMaximumSize(QtCore.QSize(14, 20))
            checkbox.setMinimumSize(QtCore.QSize(14, 20))
            checkbox.setObjectName(f"cb_{item['name']}")
            checkbox.blockSignals(True)
            if item['value'] == 'false':
                checkbox.setCheckState(0)
                rowdisabled = True
            else:
                checkbox.setCheckState(2)
            checkbox.blockSignals(False)
            self.addWidget(checkbox, i, self.chk_col)
            checkbox.stateChanged.connect(lambda state, name=item['name']: self.checkbox_changed(name, state))

        if item['unit'] is not None and item['unit'] != '':
            entry_colspan = 1
            unit_dropbox = QComboBox()
            unit_dropbox.blockSignals(True)
            if item['unit'] == 'hz':
                unit_dropbox.addItem('hz')
                unit_dropbox.setCurrentText('hz')
            elif item['unit'] == 'deg':
                unit_dropbox.addItem('deg')
                unit_dropbox.setCurrentText('deg')
            else:
                unit_dropbox.addItems(validvalues)
                unit_dropbox.setCurrentText(item['unit'])
            unit_dropbox.setObjectName(f"ud_{item['name']}")
            unit_dropbox.currentIndexChanged.connect(self.unit_dropbox_changed)
            self.addWidget(unit_dropbox, i, self.unit_col)
            unit_dropbox.blockSignals(False)
            unit_dropbox.setDisabled(rowdisabled)

        # everything has a name, except for things that have a checkbox *and* slider
        label = QLabel(f"{item['displayname']}")
        label.setToolTip(item['info'])
        label.setMinimumHeight(20)
        # label.setMinimumWidth(150)
        # label.setMaximumWidth(150)
        if item['order'][-2:] == '.1':
            olditem = self.itemAtPosition(i, self.lbl_col)
            if olditem is not None:
                self.remove_widget(olditem)
            # for p_item in self.prereq_list:
            #     if p_item['prereq'] == item['prereq'] and p_item['count'] == 1:
            #         olditem = self.itemAtPosition(i, self.exp_col)
            #         if olditem is not None:
            #             self.remove_widget(olditem)

        self.addWidget(label, i, self.lbl_col)


        slider = NoWheelSlider()
        slider.setOrientation(QtCore.Qt.Horizontal)
        slider.setObjectName(f"s_{item['name']}")

        d_slider = NoWheelSlider()
        d_slider.setOrientation(QtCore.Qt.Horizontal)
        d_slider.setObjectName(f"d_{item['name']}")

        line_edit = QLineEdit()
        line_edit.blockSignals(True)
        ## unit?
        line_edit.setText(item['value'])
        line_edit.blockSignals(False)
        line_edit.setAlignment(Qt.AlignHCenter)
        line_edit.setObjectName(f"le_{item['name']}")
        line_edit.textChanged.connect(self.line_edit_changed)

        expand_button = QToolButton()
        if item['name'] in self.expanded_items:
            expand_button.setArrowType(Qt.UpArrow)
        else:
            expand_button.setArrowType(Qt.DownArrow)
        expand_button.setMaximumWidth(24)
        expand_button.setMinimumWidth(24)
        expand_button.setObjectName(f"ex_{item['name']}")
        expand_button.clicked.connect(self.expander_clicked)


        value_label = QLabel()
        value_label.setAlignment(Qt.AlignHCenter)
        value_label.setMaximumWidth(50)
        value_label.setObjectName(f"vl_{item['name']}")
        sliderfactor = QLabel(f"{item['sliderfactor']}")
        if self.show_slider_debug:
            sliderfactor.setMaximumWidth(20)
        else:
            sliderfactor.setMaximumWidth(0)
        sliderfactor.setObjectName(f"sf_{item['name']}")



        if item['datatype'] == 'float' or \
                item['datatype'] == 'negfloat':

            # print(f"label {value_label.objectName()} for slider {slider.objectName()}")
            factor = float(item['sliderfactor'])
            if '%' in item['value']:
                pctval = int(item['value'].replace('%', ''))
            else:
                pctval = int(float(item['value']) * 100)
            pctval = int(round(pctval / factor))
            if self.show_slider_debug:
                print (f"read value: {item['value']}  factor: {item['sliderfactor']} slider: {pctval}")
            slider.blockSignals(True)
            slider.setRange(int(validvalues[0]), int(validvalues[1]))
            slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')
            slider.valueChanged.connect(self.slider_changed)
            slider.sliderPressed.connect(self.sldDisconnect)
            slider.sliderReleased.connect(self.sldReconnect)
            self.addWidget(slider, i, self.entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, self.val_col)
            self.addWidget(sliderfactor, i, self.fct_col)

            slider.blockSignals(False)

        if item['datatype'] == 'd_int' :

            d_val = int(item['value'])
            factor = float(item['sliderfactor'])
            val = int(round(d_val / factor))
            if self.show_slider_debug:
                print(f"read value: {item['value']}   d_slider: {val}")
            d_slider.blockSignals(True)
            d_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            d_slider.setValue(val)
            value_label.setText(str(d_val))
            d_slider.valueChanged.connect(self.d_slider_changed)
            d_slider.sliderPressed.connect(self.sldDisconnect)
            d_slider.sliderReleased.connect(self.d_sldReconnect)
            self.addWidget(d_slider, i, self.entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, self.val_col)
            self.addWidget(sliderfactor, i, self.fct_col)

            d_slider.blockSignals(False)

        if item['datatype'] == 'list':
            dropbox = QComboBox()
            dropbox.setEditable(True)
            dropbox.lineEdit().setAlignment(QtCore.Qt.AlignHCenter)
            dropbox.setObjectName(f"db_{item['name']}")
            dropbox.addItems(validvalues)
            dropbox.blockSignals(True)
            dropbox.setCurrentText(item['value'])
            dropbox.lineEdit().setReadOnly(True)
            dropbox.blockSignals(False)
            self.addWidget(dropbox, i, self.entry_col, 1, entry_colspan)
            dropbox.currentIndexChanged.connect(self.dropbox_changed)



        if item['datatype'] == 'int' or item['datatype'] == 'anyfloat':
            self.addWidget(line_edit, i, self.entry_col, 1, entry_colspan)

        if not rowdisabled:
            # for p_item in self.prereq_list:
            #     if p_item['prereq'] == item['name'] : # and p_item['count'] > 1:
            p_count = 0
            if item['prereq_count'] != '':
                p_count = int(item['prereq_count'])

            if item['has_expander'] == 'true':
                if item['name'] in self.expanded_items:
                    row_count = int(item['prereq_count'])
                    if item['hasbump'] != 'true':
                        row_count += 1
                    expand_button.setMaximumHeight(200)
                    self.addWidget(expand_button, i, self.exp_col, row_count, 1)
                else:
                    self.addWidget(expand_button, i, self.exp_col)

        label.setDisabled(rowdisabled)
        slider.setDisabled(rowdisabled)
        d_slider.setDisabled(rowdisabled)
        line_edit.setDisabled(rowdisabled)
        expand_button.setDisabled(rowdisabled)


        erase_button = QToolButton()
        erase_button.setObjectName(f"eb_{item['name']}")
        pixmapi = QStyle.SP_DockWidgetCloseButton
        icon = erase_button.style().standardIcon(pixmapi)
        erase_button.setIcon(icon)

        erase_button.clicked.connect(lambda _, name=item['name']: self.erase_setting(name))

        if item['replaced'] == 'Model (user)':
            self.addWidget(erase_button,i, self.erase_col)
            #print(f"erase {item['name']} button set up")


        self.setRowStretch(i,0)

    def remove_widget(self, olditem):
        widget = olditem.widget()
        if widget is not None:
            widget.deleteLater()
            self.removeWidget(widget)

    def checkbox_changed(self, name, state):
        print(f"Checkbox {name} changed. New state: {state}")
        value = 'false' if state == 0 else 'true'
        xmlutils.write_models_to_xml(settings_mgr.current_sim,settings_mgr.current_pattern,value,name)
        self.reload_caller()

    def erase_setting(self, name):
        print(f"Erase {name} clicked")
        xmlutils.erase_models_from_xml(settings_mgr.current_sim, settings_mgr.current_pattern, name)
        self.reload_caller()

    def dropbox_changed(self):
        setting_name = self.sender().objectName().replace('db_', '')
        value = self.sender().currentText()
        print(f"Dropbox {setting_name} changed. New value: {value}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value, setting_name)
        self.reload_caller()

    def unit_dropbox_changed(self):
        setting_name = self.sender().objectName().replace('ud_', '')
        line_edit_name = 'le_' + self.sender().objectName().replace('ud_', '')
        line_edit = self.mainwindow.findChild(QLineEdit, line_edit_name)
        value = ''
        unit = self.sender().currentText()
        if line_edit is not None:
            value = line_edit.text()
        print(f"Unit {self.sender().objectName()} changed. New value: {value}{unit}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value, setting_name, unit)
        self.reload_caller()

    def line_edit_changed(self):
        setting_name = self.sender().objectName().replace('le_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('le_', '')
        unit_dropbox = self.mainwindow.findChild(QComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value = self.sender().text()
        print(f"Text box {self.sender().objectName()} changed. New value: {value}{unit}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value, setting_name, unit)
        self.reload_caller()

    def expander_clicked(self):
        print(f"expander {self.sender().objectName()} clicked.  value: {self.sender().text()}")
        settingname = self.sender().objectName().replace('ex_','')
        if self.sender().arrowType() == Qt.DownArrow:
            print ('expanded')

            self.expanded_items.append(settingname)
            self.sender().setArrowType(Qt.UpArrow)

            self.reload_caller()
        else:
            print ('collapsed')
            new_exp_items = []
            for ex in self.expanded_items:
                if ex != settingname:
                    new_exp_items.append(ex)
            self.expanded_items = new_exp_items
            self.sender().setArrowType(Qt.DownArrow)

            self.reload_caller()

    # def slider_changed(self, name, value, factor):
    #     print(f"Slider {name} changed. New value: {value}  factor: {factor}")


    def slider_changed(self):
        setting_name = self.sender().objectName().replace('s_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('s_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('s_', '')
        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)
        value = 0
        factor = 1.0
        if value_label is not None:
            value_label.setText(str(self.sender().value()) + '%')
            value = int(self.sender().value())
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())
        value_to_save = str(round(value * factor / 100, 4))
        if self.show_slider_debug:
            print(f"Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value_to_save, setting_name)
        self.reload_caller()

    def d_slider_changed(self):
        setting_name = self.sender().objectName().replace('d_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('d_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('d_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('d_', '')
        unit_dropbox = self.mainwindow.findChild(QComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)
        factor = 1
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())
        if value_label is not None:
            value = self.sender().value()
            value_to_save = str(round(value * factor))
            value_label.setText(value_to_save)

        if self.show_slider_debug:
            print(f"d_Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}{unit}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value_to_save, setting_name, unit)
        self.reload_caller()

    # prevent slider from sending values as you drag
    def sldDisconnect(self):
        self.sender().valueChanged.disconnect()

    # reconnect slider after you let go
    def sldReconnect(self):
        self.sender().valueChanged.connect(self.slider_changed)
        self.sender().valueChanged.emit(self.sender().value())

    def d_sldReconnect(self):
        self.sender().valueChanged.connect(self.d_slider_changed)
        self.sender().valueChanged.emit(self.sender().value())

class ClickableLabel(QLabel):
    def __init__(self, parent=None):
        super(ClickableLabel, self).__init__(parent)

    def mousePressEvent(self, event):
        os.startfile(userconfig_rootpath,'open')
        print("userpath opened")

class NoWheelSlider(QSlider):
    def wheelEvent(self, event):
        # Block the wheel event
        event.ignore()

def perform_update(auto=True):
    # config = get_config()
    # ignore_auto_updates = utils.sanitize_dict(config["system"]).get("ignore_auto_updates", 0)
    ignore_auto_updates = utils.read_system_settings().get('ignoreUpdate', False)
    if not auto:
        ignore_auto_updates = False
    update_ans = QMessageBox.No
    proceed_ans = QMessageBox.Cancel
    is_exe = getattr(sys, 'frozen', False) #TODO: Make sure to swap these comment-outs before build to commit - this line should be active, next line should be commented out
    # is_exe = True
    if is_exe and _update_available and not ignore_auto_updates:
        # vers, url = utils.fetch_latest_version()
        update_ans = QMessageBox.Yes
        if auto:
            update_ans = QMessageBox.information(None, "Update Available!!",
                                                 f"A new version of TelemFFB is available ({_latest_version}).\n\nWould you like to automatically download and install it now?\n\nYou may also update later from the Utilities menu, or the\nnext time TelemFFB starts.\n\n~~ Note ~~ If you no longer wish to see this message on startup,\nyou may enable `ignore_auto_updates` in your user config.\n\nYou will still be able to update via the Utilities menu",
                                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if update_ans == QMessageBox.Yes:
            proceed_ans = QMessageBox.information(None, "TelemFFB Updater",
                                                  f"TelemFFB will now exit and launch the updater.\n\nOnce the update is complete, TelemFFB will restart.\n\n~~ Please Note~~~  The primary `config.ini` file will be overwritten.  If you\nhave made changes to `config.ini`, please back up the file or move the modifications to a user config file before upgrading.\n\nPress OK to continue",
                                                  QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)

        if proceed_ans == QMessageBox.Ok:

            global _current_version
            updater_source_path = os.path.join(os.path.dirname(__file__), 'updater', 'updater.exe')
            updater_execution_path = os.path.join(os.path.dirname(__file__), 'updater.exe')

            # Copy the updater executable with forced overwrite
            shutil.copy2(updater_source_path, updater_execution_path)
            active_args, unknown_args = parser.parse_known_args()
            args_list = [f'--{k}={v}' for k, v in vars(active_args).items() if v is not None and v != parser.get_default(k)]
            call = [updater_execution_path, "--current_version", _current_version] + args_list
            subprocess.Popen(call, cwd=os.path.dirname(__file__))
            return True
    else:
        try:
            updater_execution_path = os.path.join(os.path.dirname(__file__), 'updater.exe')
            if os.path.exists(updater_execution_path):
                os.remove(updater_execution_path)
        except Exception as e:
            print(e)
    return False

def init_sims():
    global dcs_telem, il2_telem, sim_connect_telem, telem_manager
    dcs_telem = NetworkThread(telem_manager, host="", port=34380)
    # dcs_enabled = utils.sanitize_dict(config["system"]).get("dcs_enabled", None)
    dcs_enabled = utils.read_system_settings().get('enableDCS', False)
    if dcs_enabled or args.sim == "DCS":
        # check and install/update export lua script
        utils.install_export_lua()
        logging.info("Starting DCS Telemetry Listener")
        dcs_telem.start()

    il2_mgr = IL2Manager()
    # il2_port = utils.sanitize_dict(config["system"]).get("il2_telem_port", 34385)
    il2_port = utils.read_system_settings().get('portIL2', 34385)
    # il2_path = utils.sanitize_dict(config["system"]).get("il2_path", 'C: \\Program Files\\IL-2 Sturmovik Great Battles')
    il2_path = utils.read_system_settings().get('pathIL2', 'C: \\Program Files\\IL-2 Sturmovik Great Battles')
    # il2_validate = utils.sanitize_dict(config["system"]).get("il2_cfg_validation", True)
    il2_validate = utils.read_system_settings().get('validateIL2', True)
    il2_telem = NetworkThread(telem_manager, host="", port=il2_port, telem_parser=il2_mgr)

    # il2_enabled = utils.sanitize_dict(config["system"]).get("il2_enabled", None)
    il2_enabled = utils.read_system_settings().get('enableIL2', False)

    if il2_enabled or args.sim == "IL2":

        if il2_validate:
            utils.analyze_il2_config(il2_path, port=il2_port)
        else:
            logging.warning(
                "IL2 Config validation is disabled - please ensure the IL2 startup.cfg is configured correctly")
        logging.info("Starting IL2 Telemetry Listener")
        il2_telem.start()

    sim_connect_telem = SimConnectSock(telem_manager)
    try:
        # msfs = utils.sanitize_dict(config["system"]).get("msfs_enabled", None)
        msfs = utils.read_system_settings().get('enableMSFS', False)
        logging.debug(f"MSFS={msfs}")
        if msfs or args.sim == "MSFS":
            logging.info("MSFS Enabled:  Starting Simconnect Manager")
            sim_connect_telem.start()
    except:
        logging.exception("Error loading MSFS enable flag from config file")
def stop_sims():
    global dcs_telem, il2_telem, sim_connect_telem
    dcs_telem.quit()
    il2_telem.quit()
    sim_connect_telem.quit()

def main():
    app = QApplication(sys.argv)
    global d
    global dev_firmware_version
    d = LogWindow()
    global settings_mgr, telem_manager
    xmlutils.update_vars(args.type, userconfig_path, defaults_path)
    settings_mgr = SettingsWindow(datasource="Global", device=args.type, userconfig_path=userconfig_path, defaults_path=defaults_path)
    icon_path = os.path.join(script_dir, "image/vpforceicon.png")
    settings_mgr.setWindowIcon(QIcon(icon_path))
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


    # config = get_config()
    # ll = config["system"].get("logging_level", "INFO")
    ll = utils.read_system_settings().get('loggingLevel', 'INFO')
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
    telem_manager.updateSettingsLayout.connect(window.update_settings)

    init_sims()

    if not perform_update():
        app.exec_()
    stop_sims()
    telem_manager.quit()


if __name__ == "__main__":
    main()
