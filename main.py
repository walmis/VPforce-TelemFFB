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
import textwrap

import argparse
import json
import logging
import sys
import time
import os

from telemffb.ConfiguratorDialog import ConfiguratorDialog
from telemffb.TeleplotSetupDialog import TeleplotSetupDialog
from telemffb.SettingsLayout import SettingsLayout
from telemffb.LogTailWindow import LogTailWindow
from telemffb.SystemSettingsDialog import SystemSettingsDialog
from telemffb.SCOverridesEditor import SCOverridesEditor

import telemffb.globals as G

import telemffb.hw.ffb_rhino as ffb_rhino
import telemffb.utils as utils
import re
import socket
import threading
from collections import OrderedDict
import subprocess

import traceback
from traceback import print_exc

from telemffb.hw.ffb_rhino import HapticEffect, FFBRhino

from configobj import ConfigObj

from telemffb.settingsmanager import *
from telemffb.utils import validate_vpconf_profile
import telemffb.xmlutils as xmlutils

from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QMainWindow, QVBoxLayout, QMessageBox, QPushButton, QRadioButton, QScrollArea, QHBoxLayout, QAction, QPlainTextEdit, QMenu, QButtonGroup, QFrame, \
    QSizePolicy, QSpacerItem, QTabWidget, QGroupBox, QShortcut
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QCoreApplication, QUrl, QSize, QByteArray, QTimer, \
    QThread, QMutex
from PyQt5.QtGui import QFont, QPixmap, QIcon, QDesktopServices, QPainter, QColor, QKeyEvent, QCursor, \
    QTextCursor, QKeySequence
from PyQt5.QtWidgets import QGridLayout, QToolButton

from telemffb.custom_widgets import *

import resources

parser = argparse.ArgumentParser(description='Send telemetry data over USB')

# Add destination telemetry address argument
parser.add_argument('--teleplot', type=str, metavar="IP:PORT", default=None,
                    help='Destination IP:port address for teleplot.fr telemetry plotting service')

parser.add_argument('-p', '--plot', type=str, nargs='+',
                    help='Telemetry item names to send to teleplot, separated by spaces')

parser.add_argument('-D', '--device', type=str, help='Rhino device USB VID:PID', default=None)
parser.add_argument('-r', '--reset', help='Reset all FFB effects', action='store_true')

# Add config file argument, default config.ini
parser.add_argument('-c', '--configfile', type=str, help='Config ini file (default config.ini)', default='config.ini')
parser.add_argument('-o', '--overridefile', type=str, help='User config override file (default = config.user.ini', default='None')
parser.add_argument('-s', '--sim', type=str, help='Set simulator options DCS|MSFS|IL2 (default DCS', default="None")
parser.add_argument('-t', '--type', help='FFB Device Type | joystick (default) | pedals | collective', default=None)
parser.add_argument('--headless', action='store_true', help='Run in headless mode')
parser.add_argument('--child', action='store_true', help='Is a child instance')
parser.add_argument('--masterport', type=str, help='master instance IPC port', default=None)

parser.add_argument('--minimize', action='store_true', help='Minimize on startup')

G.args = parser.parse_args()

# script_dir = os.path.dirname(os.path.abspath(__file__))
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True) #enable highdpi scaling
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True) #use highdpi icons

headless_mode = G.args.headless

config_was_default = False
G._launched_joystick = False
G._launched_pedals = False
G._launched_collective = False
G._launched_children = False
G._child_ipc_ports = []
G._master_instance = False
G._ipc_running = False
G._ipc_thread = None
G._child_instance = G.args.child

G.system_settings = utils.read_system_settings(G.args.device, G.args.type)


# _vpf_logo = os.path.join(script_dir, "image/vpforcelogo.png")
_vpf_logo = ":/image/vpforcelogo.png"
if G.args.device is None:
    master_rb = G.system_settings.get('masterInstance', 1)
    match master_rb:
        case 1:
            G._device_pid = G.system_settings.get('pidJoystick', "2055")
            G._device_type = 'joystick'
            _device_logo = ':/image/logo_j.png'
        case 2:
            G._device_pid = G.system_settings.get('pidPedals', "2055")
            G._device_type = 'pedals'
            _device_logo = ':/image/logo_p.png'
        case 3:
            G._device_pid = G.system_settings.get('pidCollective', "2055")
            G._device_type = 'collective'
            _device_logo = ':/image/logo_c.png'
        case _:
            G._device_pid = G.system_settings.get('pidJoystick', "2055")
            G._device_type = 'joystick'
            _device_logo = ':/image/logo_j.png'

    _device_vid_pid = f"FFFF:{G._device_pid}"
    G.args.type = G._device_type
else:
    if G.args.type is None:
        G._device_type = 'joystick'
        G.args.type = G._device_type
    else:
        G._device_type = str.lower(G.args.type)

    G._device_pid = G.args.device.split(":")[1]
    _device_vid_pid = G.args.device
    match str.lower(G.args.type):
        case 'joystick':
            _device_logo = ':/image/logo_j.png'
        case 'pedals':
            _device_logo = ':/image/logo_p.png'
        case 'collective':
            _device_logo = ':/image/logo_c.png'
        case _:
            _device_logo = ':/image/logo_j.png'

G.system_settings = utils.read_system_settings(G.args.device, G._device_type)

if G.system_settings.get('wasDefault', False):
    config_was_default = True

G.args.sim = str.upper(G.args.sim)
G.args.type = str.lower(G.args.type)

# need to determine if someone has auto-launch enabled but has started an instance with -D
# The 'masterInstance' reg key holds the radio button index of the configured master instance
# 1=joystick, 2=pedals, 3=collective
index_dict = {
    'joystick': 1,
    'pedals': 2,
    'collective': 3
}
master_index = G.system_settings.get('masterInstance', 1)
if index_dict[G._device_type] == master_index:
    G._master_instance = True
else:
    G._master_instance = False


sys.path.insert(0, '')
# sys.path.append('/simconnect')


_config_mtime = 0
_future_config_update_time = time.time()
_pending_config_update = False
effects_translator = utils.EffectTranslator()

#################
################
###  Setting _release flag to true will disable all auto-updating and 'WiP' downloads server version checking
###  Set the version number to version tag that will be pushed to master repository branch
_release = False  # Todo: Validate release flag!

if _release:
    version = "vX.X.X"
else:
    version = utils.get_version()


min_firmware_version = 'v1.0.15'
global dev, dev_firmware_version, dcs_telem, il2_telem, sim_connect_telem, xplane_telem
global startup_configurator_gains
global log_window, log_folder, log_file, log_tail_window

_update_available = False
_latest_version = None
_latest_url = None
_current_version = version
dev_serial = None
vpf_purple = "#ab37c8"   # rgb(171, 55, 200)


class LoggingFilter(logging.Filter):
    def __init__(self, keywords):
        self.keywords = keywords

    def filter(self, record):
        # Check if any of the keywords are present in the log message
        record.device_type = G._device_type
        for keyword in self.keywords:
            if keyword in record.getMessage():
                # If any keyword is found, prevent the message from being logged
                return False
        # If none of the keywords are found, allow the message to be logged
        return True


if getattr(sys, 'frozen', False):
    _install_path = os.path.dirname(sys.executable)
else:
    _install_path = os.path.dirname(os.path.abspath(__file__))

_legacy_override_file = None
_legacy_config_file = utils.get_resource_path('config.ini')  # get from bundle (if exe) or local root if source

if G.args.overridefile == 'None':
    # Need to determine if user is using default config.user.ini without passing the override flag:
    if os.path.isfile(os.path.join(_install_path, 'config.user.ini')):
        _legacy_override_file = os.path.join(_install_path, 'config.user.ini')

else:
    if not os.path.isabs(G.args.overridefile):  # user passed just file name, construct absolute path from script/exe directory
        ovd_path = utils.get_resource_path(G.args.overridefile, prefer_root=True, force=True)
    else:
        ovd_path = G.args.overridefile  # user passed absolute path, use that

    if os.path.isfile(ovd_path):
        _legacy_override_file = ovd_path
    else:
        _legacy_override_file = ovd_path
        logging.warning(f"Override file {G.args.overridefile} passed with -o argument, but can not find the file for auto-conversion")


log_folder = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB", 'log')

if not os.path.exists(log_folder):
    os.makedirs(log_folder)

date_str = datetime.now().strftime("%Y%m%d")

logname = "".join(["TelemFFB", "_", _device_vid_pid.replace(":", "-"), '_', G.args.type, "_", date_str, ".log"])
log_file = os.path.join(log_folder, logname)

# Create a logger instance
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Create a formatter for the log messages
formatter = logging.Formatter('%(asctime)s.%(msecs)03d - %(levelname)s - %(device_type)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Create a StreamHandler to log messages to the console
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

# Create a FileHandler to log messages to the log file
file_handler = logging.FileHandler(log_file, mode='a')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)
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

G.defaults_path = utils.get_resource_path('defaults.xml', prefer_root=True)
G.userconfig_rootpath = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB")
G.userconfig_path = os.path.join(G.userconfig_rootpath, 'userconfig.xml')

utils.create_empty_userxml_file(G.userconfig_path)

if not G.args.child:
    try:    # in case other instance tries doing at the same time
        utils.archive_logs(log_folder)
    except:
        pass

import aircrafts_dcs
import aircrafts_msfs_xp
import aircrafts_il2
import aircrafts_msfs_xp
from telemffb.sc_manager import SimConnectManager
from il2_telem import IL2Manager
from aircraft_base import effects


if getattr(sys, 'frozen', False):
    appmode = 'Executable'
else:
    appmode = 'Source'
logging.info("**************************************")
logging.info("**************************************")
logging.info(f"*****    TelemFFB starting up from {appmode}:  Args= {G.args.__dict__}")
logging.info("**************************************")
logging.info("**************************************")
if G.args.teleplot:
    logging.info(f"Using {G.args.teleplot} for plotting")
    utils.teleplot.configure(G.args.teleplot)


def format_dict(data, prefix=""):
    output = ""
    for key, value in data.items():
        if isinstance(value, dict):
            output += format_dict(value, prefix + key + ".")
        else:
            output += prefix + key + " = " + str(value) + "\n"
    return output


def config_has_changed(update=False) -> bool:
    # if update is true, update the current modified time
    global _config_mtime, _future_config_update_time, _pending_config_update
    # "hash" both mtimes together
    tm = int(os.path.getmtime(G.userconfig_path)) + int(os.path.getmtime(defaults_path))
    time_now = time.time()
    update_delay = 0.4
    if _config_mtime != tm:
        _future_config_update_time = time_now + update_delay
        _pending_config_update = True
        _config_mtime = tm
        logging.info(f'Config changed: Waiting {update_delay} seconds to read changes')
    if _pending_config_update and time_now >= _future_config_update_time:
        _pending_config_update = False
        logging.info(f'Config changed: {update_delay} second timer expired, reading changes')
        return True
    return False


class LogWindow(QMainWindow):
    log_paused = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Log Console ({G.args.type})")
        self.resize(800, 500)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.widget = QPlainTextEdit(self.central_widget)
        self.widget.setMaximumBlockCount(20000)
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

        # Create a QHBoxLayout for the buttons
        button_layout = QHBoxLayout()
        button_layout.addItem(QSpacerItem(20, 40, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.clear_button = QPushButton('Clear', self.central_widget)
        self.clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(self.clear_button)

        self.pause_button = QPushButton("Pause", self.central_widget)
        self.pause_button.clicked.connect(self.toggle_pause)
        button_layout.addWidget(self.pause_button)

        # Add the button layout to the main layout
        layout.addLayout(button_layout)

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def clear_log(self):
        self.widget.clear()

    def toggle_pause(self):
        # Implement the logic to toggle pause/unpause
        if self.log_paused:
            self.log_paused = False
            self.pause_button.setText("Pause")
        else:
            self.log_paused = True
            self.pause_button.setText("Resume")

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
    currentAircraftConfig: dict = {}
    timedOut: bool = True
    lastFrameTime: float
    numFrames: int = 0

    def __init__(self, settings_manager, ipc_thread=None) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self, daemon=True)

        self._run = True
        self._cond = threading.Condition()
        self._data = None
        self._events = []
        self._dropped_frames = 0
        self.lastFrameTime = time.perf_counter()
        self.frameTimes = []
        self.maxframeTime = 0
        self.timeout = 0.2
        self.settings_manager = settings_manager
        self.ipc_thread = ipc_thread
        self._ipc_telem = {}

    @classmethod
    def set_simconnect(cls, sc):
        cls._simconnect = sc

    def get_aircraft_config(self, aircraft_name, data_source):
        params = {}
        cls_name = "UNKNOWN"
        input_modeltype = ''
        try:
            if data_source == "MSFS2020":
                send_source = "MSFS"
            else:
                send_source = data_source

            if '.' in send_source:
                input = send_source.split('.')
                sim_temp = input[0]
                the_sim = sim_temp.replace('2020', '')
                input_modeltype = input[1]
            else:
                the_sim = send_source

            cls_name, pattern, result = xmlutils.read_single_model(the_sim, aircraft_name, input_modeltype, G.args.type)
            #globals.settings_mgr.current_pattern = pattern
            if cls_name == '': cls_name = 'Aircraft'
            for setting in result:
                k = setting['name']
                v = setting['value']
                u = setting['unit']
                if u is not None:
                    vu = v + u
                else:
                    vu = v
                if setting['value'] != '-':
                    params[k] = vu
                    logging.debug(f"Got from Settings Manager: {k} : {vu}")
                else:
                    logging.debug(f"Ignoring blank setting from Settings Manager: {k} : {vu}")
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

    def submitFrame(self, data: bytes):
        if type(data) == bytes:
            data = data.decode("utf-8")

        with self._cond:
            if data.startswith("Ev="):
                self._events.append(data.lstrip("Ev="))
                self._cond.notify()
            elif self._data is None:
                self._data = data
                self._cond.notify()  # notify waiting thread of new data
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

    def get_changed_params(self, params):
        diff_dict = {}

        # Check for new keys or keys with different values
        for key, new_value in params.items():
            if key not in self.currentAircraftConfig or self.currentAircraftConfig[key] != new_value:
                diff_dict[key] = new_value
        logging.debug(f"get_changed_settings: {diff_dict.items()}")
        self.currentAircraftConfig.update(diff_dict)
        return diff_dict

    def process_data(self, data):

        data = data.split(";")

        telem_data = {}
        telem_data["FFBType"] = G.args.type

        self.frameTimes.append(int((time.perf_counter() - self.lastFrameTime)*1000))
        if len(self.frameTimes) > 500: self.frameTimes.pop(0)

        if self.frameTimes[-1] > self.maxframeTime and len(self.frameTimes) > 40:  # skip the first frames before counting frametime as max
            threshold = 100
            if self.frameTimes[-1] > threshold:
                logging.debug(
                    f'*!*!*!* - Frametime threshold of {threshold}ms exceeded: time = {self.frameTimes[-1]}ms')

            self.maxframeTime = self.frameTimes[-1]

        telem_data["frameTimes"] = [self.frameTimes[-1], max(self.frameTimes)]
        telem_data["maxFrameTime"] = f"{round(self.maxframeTime, 3)}"
        telem_data["avgFrameTime"] = f"{round(sum(self.frameTimes) / len(self.frameTimes), 3):.3f}"

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

        # Read telemetry sent via IPC channel from child instances and update local telemetry stream
        if G._master_instance and G._launched_children:
            self._ipc_telem = self.ipc_thread._ipc_telem
            if self._ipc_telem != {}:
                telem_data.update(self._ipc_telem)
                self._ipc_telem = {}
        # print(items)
        aircraft_name = telem_data.get("N")
        data_source = telem_data.get("src", None)
        if data_source == "MSFS2020":
            module = aircrafts_msfs_xp
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
        elif data_source == 'XPLANE':
            module = aircrafts_msfs_xp
        else:
            module = aircrafts_dcs

        if aircraft_name and aircraft_name != self.currentAircraftName:

            if self.currentAircraft is None or aircraft_name != self.currentAircraftName:
                logging.info(f"New aircraft loaded: resetting current aircraft config")
                self.currentAircraftConfig = {}

                params, cls_name = self.get_aircraft_config(aircraft_name, data_source)

                # self.settings_manager.update_current_aircraft(send_source, aircraft_name, cls_name)
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

                if "vpconf" in params:
                    set_vpconf_profile(params['vpconf'], HapticEffect.device.serial)

                if params.get('command_runner_enabled', False):
                    if params.get('command_runner_command', '') != '':
                        try:
                            subprocess.Popen(params['command_runner_command'], shell=True)
                        except Exception as e:
                            logging.error(f"Error running Command Executor for model: {e}")

                logging.info(f"Creating handler for {aircraft_name}: {Class.__module__}.{Class.__name__}")

                # instantiate new aircraft handler
                self.currentAircraft = Class(aircraft_name)

                self.currentAircraft.apply_settings(params)
                self.currentAircraftConfig = params
                if data_source == "MSFS2020" and aircraft_name != '':
                    d1 = xmlutils.read_overrides(aircraft_name)
                    for sv in d1:
                        self._simconnect.addSimVar(name=sv['name'], var=sv['var'], sc_unit=sv['sc_unit'], scale=sv['scale'])
                    self._simconnect._resubscribe()
                if G.settings_mgr.isVisible():
                    G.settings_mgr.b_getcurrentmodel.click()

                self.updateSettingsLayout.emit()

            self.currentAircraftName = aircraft_name

        if self.currentAircraft:
            if config_has_changed():
                logging.info("Configuration has changed, reloading")
                params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
                updated_params = self.get_changed_params(params)
                self.currentAircraft.apply_settings(updated_params)

                if "vpconf" in updated_params:
                    set_vpconf_profile(params['vpconf'], HapticEffect.device.serial)

                if params.get('command_runner_enabled', False):
                    if params.get('command_runner_command', '') != '' and 'Enter full path' not in params.get('command_runner_command', ''):
                        try:
                            subprocess.Popen(params['command_runner_command'], shell=True)
                        except Exception as e:
                            logging.error(f"Error running Command Executor for model: {e}")

                if "type" in updated_params:
                    # if user changed type or if new aircraft dialog changed type, update aircraft class
                    Class = getattr(module, cls_name, None)
                    self.currentAircraft = Class(aircraft_name)
                    self.currentAircraft.apply_settings(params)
                    self.currentAircraftConfig = params

                if data_source == "MSFS2020" and aircraft_name != '':
                    d1 = xmlutils.read_overrides(aircraft_name)
                    for sv in d1:
                        self._simconnect.addSimVar(name=sv['name'], var=sv['var'], sc_unit=sv['sc_unit'], scale=sv['scale'])
                    self._simconnect._resubscribe()

                self.updateSettingsLayout.emit()
            try:
                _tm = time.perf_counter()
                self.currentAircraft._telem_data = telem_data
                self.currentAircraft.on_telemetry(telem_data)
                telem_data["perf"] = f"{(time.perf_counter() - _tm) * 1000:.3f}ms"

            except:
                logging.exception(".on_telemetry Exception")

        # Send locally generated telemetry to master here
        if G.args.child and self.currentAircraft:
            ipc_telem = self.currentAircraft._ipc_telem
            if ipc_telem != {}:
                self.ipc_thread.send_ipc_telem(ipc_telem)
                self.currentAircraft._ipc_telem = {}
        if G.args.plot:
            for item in G.args.plot:
                if item in telem_data:
                    if G._child_instance or G._launched_children:
                        utils.teleplot.sendTelemetry(item, telem_data[item], instance=G._device_type)
                    else:
                        utils.teleplot.sendTelemetry(item, telem_data[item])

        try:  # sometime Qt object is destroyed first on exit and this may cause a runtime exception
            self.telemetryReceived.emit(telem_data)
        except: pass

    def on_timeout(self):
        if self.currentAircraft and not self.timedOut:
            self.currentAircraft.on_timeout()
        self.timedOut = True
        self.settings_manager.timedOut = True

    def run(self):
        self.timeout = int(utils.read_system_settings(G.args.device, G.args.type).get('telemTimeout', 200))/1000
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
                    self.settings_manager.timedOut = False
                    data = self._data
                    self._data = None
                    self.process_data(data)


class IPCNetworkThread(QThread):
    message_received = pyqtSignal(str)
    exit_signal = pyqtSignal(str)
    restart_sim_signal = pyqtSignal(str)
    show_signal = pyqtSignal()
    showlog_signal = pyqtSignal()
    hide_signal = pyqtSignal()
    show_settings_signal = pyqtSignal()
    child_keepalive_signal = pyqtSignal(str, str)

    def __init__(self, host="localhost", myport=0, dstport=0, child_ports=[], master=False, child=False, keepalive_timer=1, missed_keepalive=3):
        super().__init__()

        self._run = True
        self._myport = int(myport)
        self._dstport = int(dstport)
        self._host = host
        self._master = master
        self._child = child
        self._child_ports = child_ports
        self._child_keepalive_timestamp = {}
        self._keepalive_timer = keepalive_timer
        self._missed_keepalive = missed_keepalive
        self._last_keepalive_timestamp = time.time()
        self._ipc_telem = {}
        self._ipc_telem_effects = {}

        self._child_active = {
            'joystick': False,
            'pedals': False,
            'collective': False
        }

        # Initialize socket
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        self._socket.settimeout(0.1)
        logging.info(f"Setting up IPC socket at {self._host}:{self._myport}")
        try:
            self._socket.bind((self._host, self._myport))
        except OSError as e:
            QMessageBox.warning(None, "Error", f"There was an error while setting up the inter-instance communications for the {G._device_type} instance of TelemFFB.\n\n{e}\nLikely there is a hung instance of TelemFFB (or python if running from source) that is holding the socket open.\n\nPlease close any instances of TelemFFB and then open Task Manager and kill any existing instances of TelemFFB")
            QCoreApplication.instance().quit()

    def send_ipc_telem(self, telem):
        j_telem = json.dumps(telem)
        message = f"telem:{j_telem}"
        self.send_message(message)

    def send_ipc_effects(self, active_effects, active_settings):
        payload = {
            f'{G._device_type}_active_effects': active_effects,
            f'{G._device_type}_active_settings': active_settings
        }

        msg = json.dumps(payload)

        msg = f'effects:{msg}'

        self.send_message(msg)

    def send_message(self, message):
        encoded_data = message.encode("utf-8")
        try:    # socket may be closed
            self._socket.sendto(encoded_data, (self._host, self._dstport))
        except OSError as e:
            logging.warning(f"Error sending IPC frame: {e}")

    def send_broadcast_message(self, message):
        for port in self._child_ports:
            encoded_data = message.encode("utf-8")
            self._socket.sendto(encoded_data, (self._host, int(port)))

    def send_keepalive(self):
        while self._run:
            if self._master:
                self.send_broadcast_message("Keepalive")
                ts = time.time()
                logging.debug(f"SENT KEEPALIVES: {ts}")
                time.sleep(self._keepalive_timer)
            elif self._child:
                self.send_message(f"Child Keepalive:{G._device_type}")
                ts = time.time()
                logging.debug(f"{G._device_type} SENT CHILD KEEPALIVE: {ts}")
                time.sleep(self._keepalive_timer)

    def receive_messages(self):
        while self._run:
            try:
                data, addr = self._socket.recvfrom(4096)

                msg = data.decode("utf-8")
                if msg == 'Keepalive':
                    if self._child:
                        ts = time.time()
                        logging.debug(f"GOT KEEPALIVE: {ts}")
                        self._last_keepalive_timestamp = ts
                elif msg.startswith('Child Keepalive:'):
                    ch_dev = msg.removeprefix('Child Keepalive:')
                    logging.debug(f"GOT KEEPALIVE FROM CHILD: '{ch_dev}'")
                    ts = time.time()
                    self._child_keepalive_timestamp[ch_dev] = ts
                    pass
                elif msg == 'MASTER INSTANCE QUIT':
                    logging.info("Received QUIT signal from master instance.  Running exit/cleanup function.")
                    self.exit_signal.emit("Received QUIT signal from master instance.  Running exit/cleanup function.")
                elif msg == 'RESTART SIMS':
                    self.restart_sim_signal.emit('Restart Sims')
                elif msg.startswith('SHOW LOG:'):
                    dev = msg.removeprefix('SHOW LOG:')
                    if dev == G._device_type:
                        logging.info("Show log command received via IPC")
                        self.showlog_signal.emit()
                elif msg == 'SHOW WINDOW':
                    logging.info("Show command received via IPC")
                    self.show_signal.emit()
                elif msg == 'HIDE WINDOW':
                    logging.info("Hide command received via IPC")
                    self.hide_signal.emit()
                elif msg == "SHOW SETTINGS":
                    logging.info("Show system settings command received via IPC")
                    self.show_settings_signal.emit()
                elif msg.startswith('telem:'):
                    payload = msg.removeprefix('telem:')
                    try:
                        ipc_telem = json.loads(payload)
                        # logging.info(f"GOT JSON PAYLOAD: {ipc_telem}")
                        self._ipc_telem.update(ipc_telem)

                    except json.JSONDecodeError:
                        pass
                elif msg.startswith('effects:'):
                    payload = msg.removeprefix('effects:')
                    try:
                        telem_effects_dict = json.loads(payload)
                        self._ipc_telem_effects.update(telem_effects_dict)
                        # print(f"GOT EFFECTS:{self._ipc_telem_effects}")

                    except json.JSONDecodeError:
                        pass
                elif msg.startswith("LOADCONFIG:"):
                    path = msg.removeprefix("LOADCONFIG:")
                    load_custom_userconfig(path)
                else:
                    logging.info(f"GOT GENERIC MESSAGE: {msg}")

                    self.message_received.emit(msg)
            except OSError:
                continue
            except ConnectionResetError:
                continue
            except socket.timeout:
                continue

    def check_missed_keepalives(self):
        while self._run:
            if self._child:
                time.sleep(self._keepalive_timer)
                elapsed_time = time.time() - self._last_keepalive_timestamp
                if elapsed_time > (self._keepalive_timer * self._missed_keepalive):
                    logging.error("KEEPALIVE TIMEOUT... exiting in 2 seconds")
                    time.sleep(2)
                    # QCoreApplication.instance().quit()
                    self.exit_signal.emit("Missed too many keepalives. Exiting.")
                    break
            elif self._master:
                time.sleep(self._keepalive_timer)
                for device in self._child_keepalive_timestamp:
                    elapsed_time = time.time() - self._child_keepalive_timestamp.get(device, time.time())
                    if elapsed_time > (self._keepalive_timer * self._missed_keepalive):
                        logging.info(f"{device} KEEPALIVE TIMEOUT")
                        if self._child_active.get(device):
                            self.child_keepalive_signal.emit(device, 'TIMEOUT')
                            self._child_active[device] = False
                    else:
                        logging.debug(f"{device} KEEPALIVE ACTIVE")
                        if not self._child_active.get(device):
                            self.child_keepalive_signal.emit(device, 'ACTIVE')
                            self._child_active[device] = True

    def run(self):
        self.receive_thread = threading.Thread(target=self.receive_messages)
        self.receive_thread.start()

        if self._master:
            self._send_ka_thread = threading.Thread(target=self.send_keepalive)
            self._send_ka_thread.start()
            self._check_ka_thread = threading.Thread(target=self.check_missed_keepalives)
            self._check_ka_thread.start()

        if self._child:
            self._send_ka_thread = threading.Thread(target=self.send_keepalive)
            self._send_ka_thread.start()
            self._check_ka_thread = threading.Thread(target=self.check_missed_keepalives)
            self._check_ka_thread.start()

    def stop(self):
        logging.info("IPC Thread stopping")
        self._run = False
        self._socket.close()


class NetworkThread(threading.Thread):
    def __init__(self, telemetry: TelemManager, host="", port=34380, telem_parser=None):
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
        logging.info("NetworkThread stopping")
        self._run = False


class SimConnectSock(SimConnectManager):
    def __init__(self, telem: TelemManager, ffb_type=G._device_type, unique_id=int(G._device_pid)):
        super().__init__(unique_id)
        telem.set_simconnect(self)
        self._telem = telem
        self._ffb_type = ffb_type


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
        if event == "Quit":
            utils.signal_emitter.msfs_quit_signal.emit()

        args = [str(x) for x in args]
        self._telem.submitFrame(f"Ev={event};" + ";".join(args))


    # retranslateUi
    # setupUi

class MainWindow(QMainWindow):
    show_simvars = False
    def __init__(self, ipc_thread=None):
        super().__init__()

        global _update_available
        global _latest_version, _latest_url, log_tail_window

        self.show_new_craft_button = False
        # Get the absolute path of the script's directory
        # script_dir = os.path.dirname(os.path.abspath(__file__))
        doc_url = 'https://vpforcecontrols.com/downloads/VPforce_Rhino_Manual.pdf'

        if _release:
            dl_url = 'https://github.com/walmis/VPforce-TelemFFB/releases'
        else:
            dl_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=D'

        # notes_url = os.path.join(script_dir, '_RELEASE_NOTES.txt')
        notes_url = utils.get_resource_path('_RELEASE_NOTES.txt')
        self._current_config_scope = G.args.type
        if G.system_settings.get('saveLastTab', 0):
            if G._device_type == 'joystick':
                tab_key = 'jWindowData'
            elif G._device_type == 'pedals':
                tab_key = 'pWindowData'
            elif G._device_type == 'collective':
                tab_key = 'cWindowData'
            data = utils.get_reg(tab_key)
            if data is not None:
                tab = json.loads(data)
                self.current_tab_index = tab.get("Tab", 0)
            else:
                self.current_tab_index = 0
        else:
            self.current_tab_index = 0
        self.default_tab_sizes = {
            "0": {  # monitor
                'height': 530,
                'width': 700,
            },
            "1": {  # settings
                'height': 530,
                'width': 700,
            },
            # "2": {  # log
            #     'height': 530,
            #     'width': 700,
            # },
            "2": {  # hide
                'height': 0,
                'width': 0,
            }
        }
        self.setMinimumWidth(600)
        self.tab_sizes = self.default_tab_sizes
        self._ipc_thread = ipc_thread
        self.system_settings_dict = utils.read_system_settings(G.args.device, G.args.type)
        self.settings_layout = SettingsLayout(parent=self, mainwindow=self)
        match G.args.type:
            case 'joystick':
                x_pos = 150
                y_pos = 130
            case 'pedals':
                x_pos = 100
                y_pos = 100
            case 'collective':
                x_pos = 50
                y_pos = 70

        self.setGeometry(x_pos, y_pos, 530, 700)
        if version:
            self.setWindowTitle(f"TelemFFB ({G.args.type}) ({version})")
        else:
            self.setWindowTitle(f"TelemFFB")
        # Construct the absolute path of the icon file
        icon = QIcon(":/image/vpforceicon.png")

        self.setWindowIcon(icon)

        self.resize(530, 700)
        self.hidden_active = False
        # Create a layout for the main window
        layout = QVBoxLayout()
        notes_row_layout = QHBoxLayout()

        # Create the menu bar
        menu_frame = QFrame()
        menu_frame_layout = QVBoxLayout(menu_frame)

        # Create the menu bar
        menubar = self.menuBar()
        self.menu = menubar
        # Set the background color of the menu bar
        # "#ab37c8" is VPForce purple
        self.menu.setStyleSheet("""
            QMenuBar { background-color: #f0f0f0; } 
            QMenu::item {background-color: transparent;}
            QMenu::item:selected { color: #ffffff; background-color: "#ab37c8"; } 
        """)
        # Add the "System" menu and its sub-option

        system_menu = self.menu.addMenu('&System')

        system_settings_action = QAction('System Settings', self)
        system_settings_action.triggered.connect(self.open_system_settings_dialog)
        system_menu.addAction(system_settings_action)

        settings_manager_action = QAction('Edit Sim/Class Defaults && Offline Models', self)
        settings_manager_action.triggered.connect(self.toggle_settings_window)
        system_menu.addAction(settings_manager_action)



        cfg_log_folder_action = QAction('Open Config/Log Directory', self)
        cfg_log_folder_action.triggered.connect(self.open_cfg_dir)
        system_menu.addAction(cfg_log_folder_action)

        reset_geometry = QAction('Reset Window Size/Position', self)
        reset_geometry.triggered.connect(self.reset_window_size)
        system_menu.addAction(reset_geometry)

        # self.menu.setStyleSheet("QMenu::item:selected { color: red; }")
        exit_app_action = QAction('Quit TelemFFB', self)
        exit_app_action.triggered.connect(exit_application)
        system_menu.addAction(exit_app_action)



        # Create the "Utilities" menu
        utilities_menu = self.menu.addMenu('Utilities')

        # Add the "Reset" action to the "Utilities" menu
        reset_action = QAction('Reset All Effects', self)
        reset_action.triggered.connect(self.reset_all_effects)
        utilities_menu.addAction(reset_action)

        self.update_action = QAction('Install Latest TelemFFB', self)
        self.update_action.triggered.connect(self.update_from_menu)
        if not _release:
            utilities_menu.addAction(self.update_action)
        self.update_action.setDisabled(True)

        download_action = QAction('Download Other Versions', self)
        download_action.triggered.connect(lambda: self.open_url(dl_url))
        utilities_menu.addAction(download_action)

        self.reset_user_config_action = QAction('Reset User Config', self)
        self.reset_user_config_action.triggered.connect(self.reset_user_config)
        utilities_menu.addAction(self.reset_user_config_action)

        self.vpconf_action = QAction("Launch VPforce Configurator", self)
        self.vpconf_action.triggered.connect(lambda: utils.launch_vpconf(dev_serial))
        utilities_menu.addAction(self.vpconf_action)

        # Add settings converter
        if _legacy_override_file is not None:
            convert_settings_action = QAction('Convert legacy user config to XML', self)
            convert_settings_action.triggered.connect(lambda: autoconvert_config(self))
            utilities_menu.addAction(convert_settings_action)

        if G._master_instance and G.system_settings.get('autolaunchMaster', 0):
            self.window_menu = self.menu.addMenu('Window')
            self.show_children_action = QAction('Show Child Instance Windows')
            self.show_children_action.triggered.connect(lambda: self.toggle_child_windows('show'))
            self.window_menu.addAction(self.show_children_action)
            self.hide_children_action = QAction('Hide Child Instance Windows')
            self.hide_children_action.triggered.connect(lambda: self.toggle_child_windows('hide'))
            self.window_menu.addAction(self.hide_children_action)

        if G._child_instance:
            self.window_menu = self.menu.addMenu('Window')
            self.hide_window_action = QAction('Hide Window')
            self.hide_window_action.triggered.connect(hide_window)
            self.window_menu.addAction(self.hide_window_action)

        log_menu = self.menu.addMenu('Log')
        self.log_window_action = QAction("Open Console Log", self)
        self.log_window_action.triggered.connect(self.toggle_log_window)
        log_menu.addAction(self.log_window_action)
        if G._master_instance and G.system_settings.get('autolaunchMaster', 0):
            self.child_log_menu = log_menu.addMenu('Open Child Logs')
            if G._launched_joystick:
                self.joystick_log_action = QAction('Joystick Log')
                self.joystick_log_action.triggered.connect(lambda: self.show_child_log('joystick'))
                self.child_log_menu.addAction(self.joystick_log_action)
            if G._launched_pedals:
                self.pedals_log_action = QAction('Pedals Log')
                self.pedals_log_action.triggered.connect(lambda: self.show_child_log('pedals'))
                self.child_log_menu.addAction(self.pedals_log_action)
            if G._launched_collective:
                self.collective_log_action = QAction('Collective Log')
                self.collective_log_action.triggered.connect(lambda: self.show_child_log('collective'))
                self.child_log_menu.addAction(self.collective_log_action)

        help_menu = self.menu.addMenu('Help')

        notes_action = QAction('Release Notes', self)
        notes_action.triggered.connect(lambda : self.open_file(notes_url))
        help_menu.addAction(notes_action)

        docs_action = QAction('Documentation', self)
        docs_action.triggered.connect(lambda: self.open_url(doc_url))
        help_menu.addAction(docs_action)

        self.support_action = QAction("Create support bundle", self)
        self.support_action.triggered.connect(lambda: utils.create_support_bundle(G.userconfig_rootpath))
        help_menu.addAction(self.support_action)

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

        dcs_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableDCS')
        il2_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableIL2')
        msfs_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableMSFS')
        xplane_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableXPLANE')

        self.icon_size = QSize(18, 18)
        if G.args.sim == "DCS" or dcs_enabled:
            dcs_color = QColor(255, 255, 0)
            dcs_icon = self.create_colored_icon(dcs_color, self.icon_size)
        else:
            dcs_color = QColor(128, 128, 128)
            dcs_icon = self.create_x_icon(dcs_color, self.icon_size)

        if G.args.sim == "MSFS" or msfs_enabled:
            msfs_color = QColor(255, 255, 0)
            msfs_icon = self.create_colored_icon(msfs_color, self.icon_size)
        else:
            msfs_color = QColor(128, 128, 128)
            msfs_icon = self.create_x_icon(msfs_color, self.icon_size)

        if G.args.sim == "IL2" or il2_enabled:
            il2_color = QColor(255, 255, 0)
            il2_icon = self.create_colored_icon(il2_color, self.icon_size)
        else:
            il2_color = QColor(128, 128, 128)
            il2_icon = self.create_x_icon(il2_color, self.icon_size)

        if G.args.sim == "XPLANE" or xplane_enabled:
            xplane_color = QColor(255, 255, 0)
            xplane_icon = self.create_colored_icon(xplane_color, self.icon_size)
        else:
            xplane_color = QColor(128, 128, 128)
            xplane_icon = self.create_x_icon(xplane_color, self.icon_size)


        logo_status_layout = QHBoxLayout()

        # Add a label for the image
        # Construct the absolute path of the image file
        self.logo_stack = QGroupBox()
        global _device_logo, _vpf_logo
        self.vpflogo_label = QLabel(self.logo_stack)
        self.devicetype_label = ClickLogo(self.logo_stack)
        self.devicetype_label.clicked.connect(self.device_logo_click_event)
        pixmap = QPixmap(_vpf_logo)
        pixmap2 = QPixmap(_device_logo)
        self.vpflogo_label.setPixmap(pixmap)
        self.devicetype_label.setPixmap(pixmap2)
        self.devicetype_label.setScaledContents(True)

        # Resize QGroupBox to match the size of the larger label
        max_width = pixmap.width()
        max_height = pixmap.height()
        self.logo_stack.setFixedSize(max_width, max_height)
        self.logo_stack.setStyleSheet("QGroupBox { border: none; }")
        # Align self.image_label2 with the upper left corner of self.image_label
        self.devicetype_label.move(self.vpflogo_label.pos())
        if not G.args.child:
            self.devicetype_label.hide()
        # Add the image labels to the layout
        logo_status_layout.addWidget(self.logo_stack, alignment=Qt.AlignVCenter | Qt.AlignLeft)

        rh_status_area = QWidget()
        rh_status_layout = QVBoxLayout()

        sim_status_area = QWidget()
        status_layout = QGridLayout()

        self.dcs_label_icon = QLabel("", self)
        self.dcs_label_icon.setPixmap(dcs_icon)
        self.dcs_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dcs_label = QLabel("DCS", self)
        dcs_label.setStyleSheet("""padding: 2px""")
        dcs_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.dcs_label_icon, 0, 0)
        status_layout.addWidget(dcs_label, 0, 1)

        self.il2_label_icon = QLabel("", self)
        self.il2_label_icon.setPixmap(il2_icon)
        self.il2_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        il2_label = QLabel("IL2", self)
        il2_label.setStyleSheet("""padding: 2px""")
        il2_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.il2_label_icon, 0, 2)
        status_layout.addWidget(il2_label, 0, 3)

        self.msfs_label_icon = QLabel("", self)
        self.msfs_label_icon.setPixmap(msfs_icon)
        self.msfs_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        msfs_label = QLabel("MSFS", self)
        msfs_label.setStyleSheet("""padding: 2px""")
        msfs_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.msfs_label_icon, 0, 4)
        status_layout.addWidget(msfs_label, 0, 5)

        self.xplane_label_icon = QLabel("", self)
        self.xplane_label_icon.setPixmap(dcs_icon)
        self.xplane_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dcs_label = QLabel("X-Plane", self)
        dcs_label.setStyleSheet("""padding: 2px""")
        dcs_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.xplane_label_icon, 0, 6)
        status_layout.addWidget(dcs_label, 0, 7)


        status_layout.setAlignment(Qt.AlignRight)
        self.init_sim_indicators(['DCS', 'IL2', 'MSFS', 'XPLANE'], G.system_settings)



        # self.xplane_label_icon = QLabel("", self)
        # xplane_icon = self.create_colored_icon(xplane_color, self.icon_size)
        # self.xplane_label_icon.setPixmap(xplane_icon)
        # self.xplane_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # xplane_label = QLabel("XPlane", self)
        # status_layout.addWidget(self.xplane_label_icon, 1, 0)
        # status_layout.addWidget(xplane_label, 1, 1)
        #
        # self.condor_label_icon = QLabel("", self)
        # condor_icon = self.create_colored_icon(condor_color, self.icon_size)
        # self.condor_label_icon.setPixmap(condor_icon)
        # self.condor_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # condor_label = QLabel("Condor2", self)
        # status_layout.addWidget(self.condor_label_icon, 1, 2)
        # status_layout.addWidget(condor_label, 1, 3)

        sim_status_area.setLayout(status_layout)

        rh_status_layout.addWidget(sim_status_area)
        rh_status_layout.setAlignment(Qt.AlignRight)

        ############
        # current craft

        cur_ac_lbl = QLabel()
        cur_ac_lbl.setText("<b>Current Aircraft:</b>")
        cur_ac_lbl.setAlignment(Qt.AlignLeft)
        cur_ac_lbl.setStyleSheet("QLabel { padding-left: 10px; padding-top: 2px; }")

        self.cur_craft = QLabel()
        self.cur_craft.setText('Unknown')
        self.cur_craft.setStyleSheet("QLabel { padding-left: 15px; padding-top: 2px; font-family: Courier New; }")
        self.cur_craft.setAlignment(Qt.AlignLeft)

        self.cur_pattern = QLabel()
        self.cur_pattern.setText('(No Match)')
        self.cur_pattern.setStyleSheet("QLabel { padding-left: 15px; padding-top: 2px; font-family: Courier New; }")
        self.cur_pattern.setAlignment(Qt.AlignLeft)

        rh_status_layout.addWidget(cur_ac_lbl)
        rh_status_layout.addWidget(self.cur_craft)
        rh_status_layout.addWidget(self.cur_pattern)

        rh_status_area.setLayout(rh_status_layout)

        logo_status_layout.addWidget(rh_status_area)

        layout.addLayout(logo_status_layout)

        ##################
        #  new craft button

        new_craft_layout = QVBoxLayout()
        self.new_craft_button = QPushButton('Create/clone config for new aircraft')
        ncb_css = """QPushButton {
                            background-color: #ab37c8;
                            border-style: outset;
                            border-width: 1px;
                            border-radius: 10px;
                            border-color: black;
                            color: white;
                            font: bold 14px;
                            min-width: 10em;
                            padding: 5px;
                        }"""
        self.new_craft_button.setStyleSheet(ncb_css)
        self.new_craft_button.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
        new_craft_layout.addWidget(self.new_craft_button)
        new_craft_layout.addSpacing(7)
        self.new_craft_button.clicked.connect(self.show_user_model_dialog)
        layout.addLayout(new_craft_layout)
        self.new_craft_button.hide()

        #####################
        #  test loading buttons, set to true for debug

        self.test_craft_area = QWidget()
        test_craft_layout = QHBoxLayout()
        test_sim_lbl = QLabel('Sim:')
        test_sim_lbl.setMaximumWidth(30)
        test_sim_lbl.setAlignment(Qt.AlignRight)
        sims = ['', 'DCS', 'IL2', 'MSFS', 'XPLANE']
        self.test_sim = QComboBox()
        self.test_sim.setMaximumWidth(60)
        self.test_sim.addItems(sims)
        self.test_sim.currentTextChanged.connect(self.test_sim_changed)
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
        self.test_craft_area.setLayout(test_craft_layout)
        self.test_craft_area.hide()
        layout.addWidget(self.test_craft_area)
        self.configmode_group = QButtonGroup()
        self.cb_joystick = QCheckBox('Joystick')
        self.cb_pedals = QCheckBox('Pedals')
        self.cb_collective = QCheckBox('Collective')

        self.config_scope_row = QHBoxLayout()
        self.configmode_group.addButton(self.cb_joystick, 1)
        self.configmode_group.addButton(self.cb_pedals, 2)
        self.configmode_group.addButton(self.cb_collective, 3)
        self.configmode_group.buttonClicked[int].connect(self.change_config_scope)
        self.config_scope_row.addWidget(self.cb_joystick)
        self.config_scope_row.addWidget(self.cb_pedals)
        self.config_scope_row.addWidget(self.cb_collective)
        self.cb_joystick.setVisible(False)
        self.cb_pedals.setVisible(False)
        self.cb_collective.setVisible(False)

        layout.addLayout(self.config_scope_row)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setTabShape(QTabWidget.Triangular)  # Set triangular tab shape
        # self.tab_widget.addTab(QWidget(), "Log")
        # self.tab_widget.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
        self.tab_widget.currentChanged.connect(lambda index: self.switch_window_view(index))

        # Set the main window area height to 0
        self.tab_widget.setMinimumHeight(14)
        style_sheet = """
                    QTabBar::tab:selected {
                        background-color: #ab37c8;
                        color: white;
                    }
                """
        self.tab_widget.setStyleSheet(style_sheet)

        # Create a horizontal line widget
        self.line_widget = QFrame(self)
        self.line_widget.setFrameShape(QFrame.HLine)
        self.line_widget.setFrameShadow(QFrame.Sunken)

        # Add the tab widget and line widget to the main layout
        layout.addWidget(self.tab_widget)
        layout.setSpacing(0)
        layout.addWidget(self.line_widget)

        ################
        #  main scroll area

        self.monitor_widget = QWidget()
        self.telem_area = QScrollArea()
        monitor_area_layout = QGridLayout()
        self.telem_area.setWidgetResizable(True)
        self.telem_area.setMinimumHeight(100)

        self.effects_area = QScrollArea()
        self.effects_area.setWidgetResizable(True)
        self.effects_area.setMinimumHeight(100)
        self.effects_area.setMaximumWidth(200)

        # Create the QLabel widget and set its properties

        dcs_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableDCS')
        il2_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableIL2')
        msfs_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableMSFS')
        xplane_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableXPLANE')

        # Convert True/False to "enabled" or "disabled"
        dcs_status = "Enabled" if dcs_enabled else "Disabled"
        il2_status = "Enabled" if il2_enabled else "Disabled"
        msfs_status = "Enabled" if msfs_enabled else "Disabled"
        xplane_status = "Enabled" if xplane_enabled else "Disabled"

        self.lbl_telem_data = QLabel(
            f"Waiting for data...\n\n"
            f"DCS     : {dcs_status}\n"
            f"IL2     : {il2_status}\n"
            f"MSFS    : {msfs_status}\n"
            f"X-Plane : {xplane_status}\n\n"
            "Enable or Disable in System -> System Settings"
        )
        self.lbl_telem_data.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_telem_data.setWordWrap(False)
        self.lbl_telem_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_telem_data.setStyleSheet("""
            padding: 2px;
            font-family: Courier New;
        """)

        # Set the QLabel widget as the widget inside the scroll area
        self.telem_area.setWidget(self.lbl_telem_data)

        self.lbl_effects_data = QLabel()
        self.effects_area.setWidget(self.lbl_effects_data)
        self.lbl_effects_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_effects_data.setStyleSheet("""
            padding: 2px;
            font-family: Courier New;
        """)

        self.telem_lbl = QLabel('Telemetry:')
        self.effect_lbl = QLabel('Active Effects:')
        if G._master_instance:
            self.effect_lbl.setText(f'Active Effects for: {self._current_config_scope}')
        monitor_area_layout.addWidget(self.telem_lbl, 0, 0)
        monitor_area_layout.addWidget(self.effect_lbl, 0, 1)
        monitor_area_layout.addWidget(self.telem_area, 1, 0)
        monitor_area_layout.addWidget(self.effects_area, 1, 1)

        self.monitor_widget.setLayout(monitor_area_layout)
        # Add the scroll area to the layout
        self.tab_widget.addTab(self.monitor_widget, "Monitor")

        # layout.addWidget(self.monitor_widget)

        # Create a scrollable area
        self.settings_area = NoKeyScrollArea()
        self.settings_area.setObjectName('theScrollArea')
        self.settings_area.setWidgetResizable(True)

        ##############
        # settings

        # Create a widget to hold the layout
        scroll_widget = QWidget()

        all_sliders = []
        scroll_widget.setLayout(self.settings_layout)

        self.settings_area.setWidget(scroll_widget)

        self.tab_widget.addTab(self.settings_area, "Settings")

        # self.log_tab_widget = QWidget(self.tab_widget)
        # self.tab_widget.addTab(self.log_tab_widget, "Log")

        # self.log_widget = QPlainTextEdit(self.log_tab_widget)
        # self.log_widget.setMaximumBlockCount(20)
        # self.log_widget.setReadOnly(True)
        # self.log_widget.setFont(QFont("Courier New"))
        # self.log_widget.setLineWrapMode(QPlainTextEdit.NoWrap)
        # self.log_tail_thread = LogTailer(log_file)
        # self.log_tail_thread.log_updated.connect(self.update_log_widget)
        # self.log_tail_thread.start()

        # self.clear_button = QPushButton("Clear", self.log_tab_widget)
        # self.toggle_button = QPushButton("Pause", self.log_tab_widget)
        # self.open_log_button = QPushButton("Open in Window", self.log_tab_widget)

        # self.clear_button.clicked.connect(self.clear_log_widget)
        # self.toggle_button.clicked.connect(self.toggle_log_tailing)
        # self.open_log_button.clicked.connect(self.show_tail_log_window)

        self.tab_widget.addTab(QWidget(), "Hide")

        # log_layout = QVBoxLayout(self.log_tab_widget)
        # log_layout.addWidget(self.log_widget)

        # button_layout = QHBoxLayout()
        # button_layout.addWidget(self.clear_button)
        # button_layout.addWidget(self.toggle_button)
        # button_layout.addStretch()  # Add stretch to push the next button to the right
        # button_layout.addWidget(self.open_log_button)
        # log_layout.addLayout(button_layout)

        # self.log_tab_widget.setLayout(log_layout)
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

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        self.layout = QVBoxLayout(central_widget)

        if G._master_instance and G._launched_children:
            self.instance_status_row = QHBoxLayout()
            self.master_status_icon = StatusLabel(None, f'This Instance({ G._device_type.capitalize() }):', Qt.green, 8)
            self.joystick_status_icon = StatusLabel(None, 'Joystick:', Qt.yellow, 8)
            self.pedals_status_icon = StatusLabel(None, 'Pedals:', Qt.yellow, 8)
            self.collective_status_icon = StatusLabel(None, 'Collective:', Qt.yellow, 8)

            self.master_status_icon.clicked.connect(self.change_config_scope)
            self.joystick_status_icon.clicked.connect(self.change_config_scope)
            self.pedals_status_icon.clicked.connect(self.change_config_scope)
            self.collective_status_icon.clicked.connect(self.change_config_scope)

            self.instance_status_row.addWidget(self.master_status_icon)
            self.instance_status_row.addWidget(self.joystick_status_icon)
            self.instance_status_row.addWidget(self.pedals_status_icon)
            self.instance_status_row.addWidget(self.collective_status_icon)
            self.joystick_status_icon.hide()
            self.pedals_status_icon.hide()
            self.collective_status_icon.hide()

            self.instance_status_row.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
            self.instance_status_row.setSpacing(10)

            layout.addLayout(self.instance_status_row)

        version_row_layout = QHBoxLayout()
        self.version_label = QLabel()

        if _release:
            status_text = f"Release Version {version}"
        else:
            status_text = "UNKNOWN"

        self.version_label.setText(f'Version Status: {status_text}')
        self.version_label.setOpenExternalLinks(True)

        global dev_firmware_version
        self.firmware_label = QLabel()
        self.firmware_label.setText(f'Rhino Firmware: {dev_firmware_version}')

        self.version_label.setAlignment(Qt.AlignLeft)
        self.firmware_label.setAlignment(Qt.AlignRight)
        version_row_layout.addWidget(self.version_label)
        version_row_layout.addWidget(self.firmware_label)

        version_row_layout.setAlignment(Qt.AlignBottom)
        layout.addLayout(version_row_layout)

        # self.test_button = QPushButton("SEND TEST MESSAGE")
        # self.test_button.clicked.connect(lambda: send_test_message())

        # layout.addWidget(self.test_button)

        central_widget.setLayout(layout)

        # Load Stored Geomoetry
        self.load_main_window_geometry()
        shortcut = QShortcut(QKeySequence('Alt+D'), self)
        shortcut.activated.connect(self.add_debug_menu)
        try:
            if utils.get_reg("debug"):
                self.add_debug_menu()
        except:
            pass

    @classmethod
    def set_telem_manager(cls, tm):
        cls._telem_manager = tm
    def test_function(self):
        self.set_scrollbar(400)

    def refresh_telem_status(self, dcs, il2, msfs, xplane):
        dcs_status = "Enabled" if dcs else "Disabled"
        il2_status = "Enabled" if il2 else "Disabled"
        msfs_status = "Enabled" if msfs else "Disabled"
        xplane_status = "Enabled" if xplane else "Disabled"

        self.lbl_telem_data.setText(
            f"Waiting for data...\n\n"
            f"DCS     : {dcs_status}\n"
            f"IL2     : {il2_status}\n"
            f"MSFS    : {msfs_status}\n"
            f"X-Plane : {xplane_status}\n\n"
            "Enable or Disable in System -> System Settings"
        )

    def add_debug_menu(self):
        # debug mode
        for action in self.menu.actions():
            if action.text() == "Debug":
                return
        self.debug_menu = self.menu.addMenu("Debug")
        aircraft_picker_action = QAction('Enable Manual Aircraft Selection', self)
        aircraft_picker_action.triggered.connect(lambda: self.toggle_settings_window(dbg=True))
        self.debug_menu.addAction(aircraft_picker_action)

        self.teleplot_action = QAction("Teleplot Setup", self)
        self.teleplot_action.triggered.connect(self.open_teleplot_setup_dialog)
        self.debug_menu.addAction(self.teleplot_action)

        self.show_simvar_action = QAction("Show simvar in telem window", self)
        self.show_simvar_action.triggered.connect(self.toggle_simvar_telemetry)
        self.show_simvar_action.setCheckable(True)
        self.debug_menu.addAction(self.show_simvar_action)

        self.configurator_settings_action = QAction('Configurator Gain Override', self)
        self.configurator_settings_action.triggered.connect(self.open_configurator_dialog)
        self.debug_menu.addAction(self.configurator_settings_action)

        self.sc_overrides_action = QAction('SimConnect Overrides Editor', self)
        self.sc_overrides_action.triggered.connect(self.open_sc_override_dialog)
        self.debug_menu.addAction(self.sc_overrides_action)

        if G._master_instance:
            self.custom_userconfig_action = QAction("Load Custom User Config", self)
            self.custom_userconfig_action.triggered.connect(lambda: load_custom_userconfig())
            self.debug_menu.addAction(self.custom_userconfig_action)

    def set_scrollbar(self, pos):
        self.settings_area.verticalScrollBar().setValue(pos)

    def process_error_signal(self, message):
        # Placeholder function to process signal generated from anywhere using utils.signal_emitter
        pass

    def update_child_status(self, device, status):
        status_icon_name = f'{device}_status_icon'
        status_icon = getattr(self, status_icon_name, None)

        if status_icon is not None and status == 'ACTIVE':
            status_icon.set_dot_color(Qt.green)
        if status_icon is not None and status == 'TIMEOUT':
            status_icon.set_dot_color(Qt.red)

    def show_child_log(self, child):
        self.G._ipc_thread.send_broadcast_message(f'SHOW LOG:{child}')

    def show_child_settings(self):
        self.G._ipc_thread.send_broadcast_message("SHOW SETTINGS")

    def toggle_child_windows(self, toggle):
        if toggle == 'show':
            self.G._ipc_thread.send_broadcast_message("SHOW WINDOW")
            pass
        elif toggle == 'hide':
            self.G._ipc_thread.send_broadcast_message("HIDE WINDOW")
            pass

    def reset_user_config(self):
        ans = QMessageBox.warning(self, "Caution", "Are you sure you want to proceed?  All contents of your user configuration will be erased\n\nA backup of the configuration will be generated containing the current timestamp.", QMessageBox.Ok | QMessageBox.Cancel)

        if ans == QMessageBox.Ok:
            try:
                # Get the current timestamp
                timestamp = datetime.now().strftime('%Y%m%d_%H%M')

                # Create the backup file name with the timestamp
                backup_file = os.path.join(G.userconfig_rootpath, ('userconfig_' + timestamp + '.bak'))

                # Copy the file to the backup file
                shutil.copy(G.globals.userconfig_path, backup_file)

                logging.debug(f"Backup created: {backup_file}")

            except Exception as e:
                logging.error(f"Error creating backup: {str(e)}")
                QMessageBox.warning(self, 'Error', f'There was an error resetting the config:\n\n{e}')
                return

            os.remove(G.userconfig_path)
            utils.create_empty_userxml_file(G.userconfig_path)

            logging.info(f"User config Reset:  Backup file created: {backup_file}")
        else:
            return

    def update_log_widget(self, log_line):
        cursor = self.log_widget.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(log_line)
        self.log_widget.setTextCursor(cursor)
        self.log_widget.ensureCursorVisible()

    def clear_log_widget(self):
        self.log_widget.clear()

    def toggle_log_tailing(self):
        if self.log_tail_thread.is_paused():
            self.log_tail_thread.resume()
            self.toggle_button.setText("Pause")
        else:
            self.log_tail_thread.pause()
            self.toggle_button.setText("Resume")

    def show_device_logo(self):
        self.devicetype_label.show()

    def enable_device_logo_click(self, state):
        self.devicetype_label.setClickable(state)
        self.devicetype_label.setStyleSheet(
            "QLabel {"
            # "   background-color: #4CAF50;"  # Set background color
            # "   color: white;"               # Set text color
            # "   padding: 1px;"               # Add padding
            "   border-radius: 4px;"         # Add rounded corners
            # "   border: 2px solid #808080;"  # Add border
            "}"
            "QLabel:hover {"
            "   background-color: #DCDCDC;"  # Change background color on hover
            "}"
        )

    def device_logo_click_event(self):
        # print("External function executed on label click")
        # print(self._current_config_scope)
        if self._current_config_scope == 'joystick':
            if G._launched_pedals or G._device_type == 'pedals':
                self.change_config_scope(2)
            elif G._launched_collective or G._device_type == 'collective':
                self.change_config_scope(3)
        elif self._current_config_scope == 'pedals':
            if G._launched_collective or G._device_type == 'collective':
                self.change_config_scope(3)
            elif G._launched_joystick or G._device_type == 'joystick':
                self.change_config_scope(1)
        elif self._current_config_scope == 'collective':
            if G._launched_joystick or G._device_type == 'joystick':
                self.change_config_scope(1)
            elif G._launched_pedals or G._device_type == 'pedals':
                self.change_config_scope(2)

    def update_version_result(self, vers, url):
        global _update_available
        global _latest_version
        global _latest_url
        _latest_version = vers
        _latest_url = url

        is_exe = getattr(sys, 'frozen', False)

        if vers == "uptodate":
            status_text = "Up To Date"
            self.update_action.setDisabled(True)
            self.version_label.setText(f'Version Status: {status_text}')
        elif vers == "error":
            status_text = "UNKNOWN"
            self.version_label.setText(f'Version Status: {status_text}')
        elif vers == 'dev':
            if is_exe:
                self.version_label.setText(f'Version Status: <b>Development Build</b>')
            else:
                self.version_label.setText(f'Version Status: <b>Non release - Modified Source</b>')

        elif vers == 'needsupdate':
            self.version_label.setText(f'Version Status: <b>Out of Date Source - Git pull needed</b>')

        else:
            # print(_update_available)
            _update_available = True
            logging.info(f"<<<<Update available - new version={vers}>>>>")

            status_text = f"New version <a href='{url}'><b>{vers}</b></a> is available!"
            self.update_action.setDisabled(False)
            self.update_action.setText("Install Latest TelemFFB")
            self.version_label.setToolTip(url)
            self.version_label.setText(f'Version Status: {status_text}')

        self.perform_update(auto=True)

    def change_config_scope(self, _arg):

        current_log_ts = log_file.split('_')[-1]
        if isinstance(_arg, str):
            if 'joystick' in _arg: arg = 1
            elif 'pedals' in _arg: arg = 2
            elif 'collective' in _arg: arg = 3
        else:
            arg = _arg

        if arg == 1:
            xmlutils.update_vars('joystick', G.userconfig_path, G.defaults_path)
            self._current_config_scope = 'joystick'
            new_device_logo = ':/image/logo_j.png'
        elif arg == 2:
            xmlutils.update_vars('pedals', G.userconfig_path, G.defaults_path)
            self._current_config_scope = 'pedals'
            new_device_logo = ':/image/logo_p.png'
        elif arg == 3:
            xmlutils.update_vars('collective', G.userconfig_path, G.defaults_path)
            self._current_config_scope = 'collective'
            new_device_logo = ':/image/logo_c.png'

        pixmap = QPixmap(new_device_logo)
        self.devicetype_label.setPixmap(pixmap)
        self.devicetype_label.setFixedSize(pixmap.width(), pixmap.height())

        if G._master_instance:
            self.effect_lbl.setText(f'Active Effects for: {self._current_config_scope}')

        # for file in os.listdir(log_folder):
        #     if file.endswith(self._current_config_scope + '_' + current_log_ts):
        #         self.log_tail_thread.change_log_file(os.path.join(log_folder, file))
        #         pass
        # log_tail_window.setWindowTitle(f"Log File Monitor ({self._current_config_scope})")

        self.update_settings()

    def test_sim_changed(self):
        models = xmlutils.read_models(self.test_sim.currentText())
        self.test_name.blockSignals(True)
        self.test_name.clear()
        self.test_name.addItems(models)
        self.test_name.blockSignals(False)

    def closeEvent(self, event):
        # Perform cleanup before closing the application
        if G._child_instance:
            self.hide()
            event.ignore()
        else:
            exit_application()

    def reset_window_size(self):
        match G.args.type:
            case 'joystick':
                x_pos = 150
                y_pos = 130
            case 'pedals':
                x_pos = 100
                y_pos = 100
            case 'collective':
                x_pos = 50
                y_pos = 70

        self.setGeometry(x_pos, y_pos, 530, 700)

    def load_main_window_geometry(self):
        device_type = G.args.type
        sys_settings = utils.read_system_settings(G.args.device, G.args.type)
        if device_type == 'joystick':
            reg_key = 'jWindowData'
        elif device_type == 'pedals':
            reg_key = 'pWindowData'
        elif device_type == 'collective':
            reg_key = 'cWindowData'

        window_data = utils.get_reg(reg_key)
        # print(window_data)
        if window_data is not None:
            window_data_dict = json.loads(window_data)
        else:
            window_data_dict = {}
        # print(window_data_dict)
        load_geometry = sys_settings.get('saveWindow', False)
        load_tab = sys_settings.get('saveLastTab', False)

        if load_tab:
            tab = window_data_dict.get('Tab', 0)
            self.tab_sizes = window_data_dict.get('TabSizes', self.default_tab_sizes)
            self.tab_widget.setCurrentIndex(tab)
            h = self.tab_sizes[str(tab)]['height']
            w = self.tab_sizes[str(tab)]['width']
            self.resize(w, h)

        if load_geometry:
            win_pos = window_data_dict.get('WindowPosition', {})
            win_x = win_pos.get('x', 100)
            win_y = win_pos.get('y', 100)
            self.move(win_x, win_y)

    def force_sim_aircraft(self):
        G.settings_mgr.current_sim = self.test_sim.currentText()
        G.settings_mgr.current_aircraft_name = self.test_name.currentText()
        self.settings_layout.expanded_items.clear()
        self.monitor_widget.hide()
        self.settings_layout.reload_caller()

    def open_teleplot_setup_dialog(self):
        dialog = TeleplotSetupDialog(self)
        dialog.exec_()

    def open_configurator_dialog(self):
        dialog = ConfiguratorDialog(self)
        dialog.raise_()
        dialog.activateWindow()
        dialog.show()

    def open_system_settings_dialog(self):
        try:
            dialog = SystemSettingsDialog(self)
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        except:
            traceback.print_exc()
        # dialog.exec_()

    def open_sc_override_dialog(self):
        dialog = SCOverridesEditor(self)
        dialog.raise_()
        dialog.activateWindow()
        dialog.show()
        # dialog.exec_()

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
        result = QMessageBox.warning(self, "Are you sure?", "*** Only use this if you have effects which are 'stuck' ***\n\n  Proceeding will result in the destruction"
                                                            " of any effects which are currently being generated by the simulator and may result in requiring a restart of"
                                                            " the sim or a new session.\n\n~~ Proceed with caution ~~", QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)

        if result == QMessageBox.Ok:
            try:
                HapticEffect.device.resetEffects()
            except Exception as error:
                pass

    def open_cfg_dir(self):
        modifiers = QApplication.keyboardModifiers()
        if (modifiers & QtCore.Qt.ControlModifier) and (modifiers & QtCore.Qt.ShiftModifier) and getattr(sys, 'frozen', False):
            os.startfile(sys._MEIPASS, 'open')
        else:
            os.startfile(G.userconfig_rootpath, 'open')

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
        line1_x = int((size.width() / 2) - 2)
        line2_x = int((size.width() / 2) + 2)
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

    def show_tail_log_window(self):
        log_tail_window.move(self.x() + 50, self.y() + 100)
        log_tail_window.show()
        log_tail_window.activateWindow()

    def toggle_log_window(self):
        if log_window.isVisible():
            log_window.hide()
        else:
            log_window.move(self.x()+50, self.y()+100)
            log_window.show()

    def toggle_settings_window(self, dbg=False):
        try:
            modifiers = QApplication.keyboardModifiers()
            if ((modifiers & QtCore.Qt.ControlModifier) and (modifiers & QtCore.Qt.ShiftModifier)) or dbg:
                if self.test_craft_area.isVisible():
                    self.test_craft_area.hide()
                else:
                    self.test_craft_area.show()
            else:
                sm = G.settings_mgr
                if sm.isVisible():
                    sm.hide()
                else:
                    sm.move(self.x() + 50, self.y() + 100)
                    sm.show()
                    logging.debug(f"# toggle settings window   sim:'{sm.current_sim}' ac:'{sm.current_aircraft_name}'")
                    if sm.current_aircraft_name != '':
                        sm.currentmodel_click()
                    else:
                        sm.update_table_on_class_change()

                    if sm.current_sim == '' or sm.current_sim == 'nothing':
                        sm.update_table_on_sim_change()
        except:
            traceback.print_exc()

    def show_user_model_dialog(self):
        mprint("show_user_model_dialog")
        current_aircraft = self.cur_craft.text()
        dialog = UserModelDialog(G.settings_mgr.current_sim, current_aircraft, G.settings_mgr.current_class, self)
        result = dialog.exec_()
        if result == QtWidgets.QDialog.Accepted:
            # Handle accepted
            new_aircraft = dialog.tb_current_aircraft.currentText()
            if new_aircraft == current_aircraft:
                qm = QMessageBox()
                ret = qm.question(self, 'Create Match Pattern', "Are you sure you want to match on the\nfull aircraft name and not a search pattern?", qm.Yes | qm.No)
                if ret == qm.No:
                    return
            new_combo_box_value = dialog.combo_box.currentText()
            pat_to_clone = dialog.models_combo_box.currentText()
            if pat_to_clone == '':
                logging.info(f"New: {new_aircraft} {new_combo_box_value}")
                xmlutils.write_models_to_xml(G.settings_mgr.current_sim, new_aircraft, new_combo_box_value, 'type', None)
            else:
                logging.info(f"Cloning: {pat_to_clone} as {new_aircraft}")
                xmlutils.clone_pattern(G.settings_mgr.current_sim, pat_to_clone, new_aircraft)
        else:
            # Handle canceled
            pass

    def update_from_menu(self):
        if self.perform_update(auto=False):
            QCoreApplication.instance().quit()

    def init_sim_indicators(self, sims, settings_dict):
        label_icons = {
            'DCS': self.dcs_label_icon,
            'IL2': self.il2_label_icon,
            'MSFS': self.msfs_label_icon,
            'XPLANE': self.xplane_label_icon,

        }
        enable_color = QColor(255, 255, 0)
        disable_color = QColor(128, 128, 128)
        enable_icon = self.create_colored_icon(enable_color, self.icon_size)
        disable_icon = self.create_x_icon(disable_color, self.icon_size)
        for sim in sims:
            state = settings_dict.get(f"enable{sim}")
            lb = label_icons[sim]
            if state:
                lb.setPixmap(enable_icon)
                lb.setToolTip("Sim is enabled, no telemetry yet received")

            else:
                lb.setPixmap(disable_icon)
                lb.setToolTip("Sim is disabled")

    def update_sim_indicators(self, source, paused=False, error=False):
        label_icons = {
            'DCS': self.dcs_label_icon,
            'IL2': self.il2_label_icon,
            'MSFS2020': self.msfs_label_icon,
            'XPLANE': self.xplane_label_icon,
        }
        active_color = QColor(0, 255, 0)
        paused_color = QColor(0, 0, 255)
        error_color = Qt.red
        active_icon = self.create_colored_icon(active_color, self.icon_size)
        active_tooltip = "Sim is running, receiving telemetry"
        paused_icon = self.create_paused_icon(paused_color, self.icon_size)
        paused_tooltip = "Telemetry stopped or sim is paused"
        error_icon = self.create_x_icon(error_color, self.icon_size)
        error_tooltip = "Error condition: check log"
        lb = label_icons[source]
        if error:
            lb.setPixmap(error_icon)
            lb.setToolTip(error_tooltip)
        elif not paused:
            lb.setPixmap(active_icon)
            lb.setToolTip(active_tooltip)
        elif paused:
            lb.setPixmap(paused_icon)
            lb.setToolTip(paused_tooltip)

    def switch_window_view(self, index):
        previous_index = self.current_tab_index
        # Get window geometry and store as the geometry for the previous index for later recall
        self.tab_sizes[str(previous_index)]['height'] = self.height()
        self.tab_sizes[str(previous_index)]['width'] = self.width()

        if index == 0:  # Monitor Tab
            self.current_tab_index = 0
            try:
                h = self.tab_sizes[str(index)]['height']
                w = self.tab_sizes[str(index)]['width']
                self.resize(int(w), int(h))
            except: pass

        elif index == 1:  # Settings Tab
            self.current_tab_index = 1
            modifiers = QApplication.keyboardModifiers()
            if (modifiers & QtCore.Qt.ControlModifier) and (modifiers & QtCore.Qt.ShiftModifier):
                self.cb_joystick.setVisible(True)
                self.cb_pedals.setVisible(True)
                self.cb_collective.setVisible(True)
                match G.args.type:
                    case 'joystick':
                        self.cb_joystick.setChecked(True)
                    case 'pedals':
                        self.cb_pedals.setChecked(True)
                    case 'collective':
                        self.cb_collective.setChecked(True)

            try:
                h = self.tab_sizes[str(index)]['height']
                w = self.tab_sizes[str(index)]['width']
                self.resize(int(w), int(h))
            except:
                pass

        # elif index == 2:  # Log Tab
        #     self.current_tab_index = 2
        #     try:
        #         h = self.tab_sizes[str(index)]['height']
        #         w = self.tab_sizes[str(index)]['width']
        #         self.resize(int(w), int(h))
        #     except: pass

        elif index == 2:  # Hide Tab
            self.current_tab_index = 2

            self.resize(0, 0)

    def interpolate_color(self, color1, color2, value):
        # Ensure value is between 0 and 1
        value = max(0.0, min(1.0, value))

        # Extract individual color components
        r1, g1, b1, a1 = color1.getRgb()
        r2, g2, b2, a2 = color2.getRgb()

        # Interpolate each color component
        r = int(r1 + (r2 - r1) * value)
        g = int(g1 + (g2 - g1) * value)
        b = int(b1 + (b2 - b1) * value)
        a = int(a1 + (a2 - a1) * value)

        # Create and return the interpolated color
        return QColor(r, g, b, a)

    def update_telemetry(self, datadict: dict):

        data = OrderedDict(sorted(datadict.items()))  # Alphabetize telemetry data
        keys = data.keys()
        try:
            # use ordereddict and move some telemetry to the top
            # Items to move to the beginning (reverse order)
            if 'SimconnectCategory' in keys: data.move_to_end('SimconnectCategory', last=False)
            if 'AircraftClass' in keys: data.move_to_end('AircraftClass', last=False)
            if 'src' in keys: data.move_to_end('src', last=False)
            if 'N' in keys: data.move_to_end('N', last=False)
            if 'FFBType' in keys: data.move_to_end('FFBType', last=False)
            if 'perf' in keys: data.move_to_end('perf', last=False)
            if 'avgFrameTime' in keys: data.move_to_end('avgFrameTime', last=False)
            if 'maxFrameTime' in keys: data.move_to_end('maxFrameTime', last=False)
            if 'frameTimes' in keys: data.move_to_end('frameTimes', last=False)
            if 'T' in keys: data.move_to_end('T', last=False)

            # Items to move to the end
        except:
            pass

        try:

            items = ""
            for k, v in data.items():
                # check for msfs and debug mode (alt-d pressed), change to simvar name
                if self.show_simvars:
                    if data["src"] == "MSFS2020":
                        s = self._telem_manager._simconnect.get_var_name(k)
                        # s = simvarnames.get_var_name(k)
                        if s is not None:
                            k = s
                if type(v) == float:
                    items += f"{k}: {v:.3f}\n"
                else:
                    if isinstance(v, list):
                        v = "[" + ", ".join([f"{x:.3f}" if not isinstance(x, str) else x for x in v]) + "]"
                    items += f"{k}: {v}\n"
            active_effects = ""
            active_settings = []

            if G._master_instance and self._current_config_scope != G._device_type:
                dev = self._current_config_scope
                active_effects = self.G._ipc_thread._ipc_telem_effects.get(f'{dev}_active_effects', '')
                active_settings = self.G._ipc_thread._ipc_telem_effects.get(f'{dev}_active_settings', [])
            else:
                for key in effects.dict.keys():
                    if effects[key].started:
                        descr = effects_translator.get_translation(key)[0]
                        settingname = effects_translator.get_translation(key)[1]
                        if descr not in active_effects:
                            active_effects = '\n'.join([active_effects, descr])
                        if settingname not in active_settings and settingname != '':
                            active_settings.append(settingname)

            if G.args.child:
                child_effects = str(effects.dict.keys())
                if len(child_effects):
                    self.G._ipc_thread.send_ipc_effects(active_effects, active_settings)

            window_mode = self.tab_widget.currentIndex()
            # update slider colors
            pct_max_a = data.get('_pct_max_a', 0)
            pct_max_e = data.get('_pct_max_e', 0)
            pct_max_r = data.get('_pct_max_r', 0)
            qcolor_green = QColor("#17c411")
            qcolor_grey = QColor("grey")
            if window_mode == 1:
                sliders = self.findChildren(NoWheelSlider)
                for my_slider in sliders:
                    slidername = my_slider.objectName().replace('sld_', '')
                    my_slider.blockSignals(True)

                    if slidername == 'max_elevator_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_e)
                        my_slider.setHandleColor(new_color.name())
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    if slidername == 'max_aileron_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_a)
                        my_slider.setHandleColor(new_color.name())
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    if slidername == 'max_rudder_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_r)
                        my_slider.setHandleColor(new_color.name())
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    for a_s in active_settings:
                        if bool(re.search(a_s, slidername)):
                            my_slider.setHandleColor("#17c411")
                            break
                        else:
                            my_slider.setHandleColor(vpf_purple)
                    my_slider.blockSignals(False)

            is_paused = max(data.get('SimPaused', 0), data.get('Parked', 0))
            error_cond = data.get('error', 0)
            if error_cond:
                self.update_sim_indicators(data.get('src'), error=True)
            else:
                self.update_sim_indicators(data.get('src'), paused=is_paused)

            shown_pattern = G.settings_mgr.current_pattern
            if G.settings_mgr.current_pattern == '' and data.get('N', '') != '':
                shown_pattern = 'Using defaults'
                self.new_craft_button.show()
            else:
                self.new_craft_button.hide()

            self.cur_craft.setText(data['N'])
            self.cur_pattern.setText(f'Matched: <span style="font-family: Consolas, monospace;font-size: 14px">"{shown_pattern}"</span> ')

            if window_mode == 0:
                self.lbl_telem_data.setText(items)
            # elif window_mode == self.effect_monitor_radio:
                self.lbl_effects_data.setText(active_effects)

        except Exception as e:
            traceback.print_exc()

    def perform_update(self, auto=True):
        if _release:
            return False

        ignore_auto_updates = utils.read_system_settings(G.args.device, G.args.type).get('ignoreUpdate', False)
        if not auto:
            ignore_auto_updates = False
        update_ans = QMessageBox.No
        proceed_ans = QMessageBox.Cancel
        try:
            updater_execution_path = os.path.join(utils.get_script_path(), 'updater.exe')
            if os.path.exists(updater_execution_path):
                os.remove(updater_execution_path)
        except Exception as e:
            logging.error(f'Error in perform_update: {e}')
        is_exe = getattr(sys, 'frozen', False)  # TODO: Make sure to swap these comment-outs before build to commit - this line should be active, next line should be commented out
        # is_exe = True
        if is_exe and _update_available and not ignore_auto_updates and not G._child_instance:
            # vers, url = utils.fetch_latest_version()
            update_ans = QMessageBox.Yes
            if auto:
                update_ans = QMessageBox.information(self, "Update Available!!",
                                                     f"A new version of TelemFFB is available ({_latest_version}).\n\nWould you like to automatically download and install it now?\n\nYou may also update later from the Utilities menu, or the\nnext time TelemFFB starts.\n\n~~ Note ~~ If you no longer wish to see this message on startup,\nyou may enable `ignore_auto_updates` in your user config.\n\nYou will still be able to update via the Utilities menu",
                                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

            if update_ans == QMessageBox.Yes:
                proceed_ans = QMessageBox.information(self, "TelemFFB Updater",
                                                      f"TelemFFB will now exit and launch the updater.\n\nOnce the update is complete, TelemFFB will restart.\n\n~~ Please Note~~~  The primary `config.ini` file will be overwritten.  If you\nhave made changes to `config.ini`, please back up the file or move the modifications to a user config file before upgrading.\n\nPress OK to continue",
                                                      QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)

            if proceed_ans == QMessageBox.Ok:
                global _current_version
                updater_source_path = utils.get_resource_path('updater/updater.exe', prefer_root=True, force=True)
                updater_execution_path = os.path.join(utils.get_script_path(), 'updater.exe')

                # Copy the updater executable with forced overwrite
                shutil.copy2(updater_source_path, updater_execution_path)
                active_args, unknown_args = parser.parse_known_args()
                args_list = [f'--{k}={v}' for k, v in vars(active_args).items() if
                             v is not None and v != parser.get_default(k)]
                call = [updater_execution_path, "--current_version", _current_version] + args_list
                subprocess.Popen(call, cwd=_install_path)
                if auto:
                    for child_widget in self.findChildren(QMessageBox):
                        child_widget.reject()
                    QTimer.singleShot(250, lambda: exit_application())
                else:
                    return True

        return False
    def toggle_simvar_telemetry(self):
        if self.show_simvars:
            self.show_simvars = False
            self.show_simvar_action.setChecked(False)

        else:
            self.show_simvars = True
            self.show_simvar_action.setChecked(True)

class LogTailer(QThread):
    log_updated = pyqtSignal(str)

    def __init__(self, log_file_path, parent=None):
        super(LogTailer, self).__init__(parent)
        self.log_file_path = log_file_path
        self.pause_mutex = QMutex()
        self.paused = False

    def run(self):
        with open(self.log_file_path, 'r') as self.log_file:
            self.log_file.seek(0, os.SEEK_END)
            while True:
                self.pause_mutex.lock()
                while self.paused:
                    self.pause_mutex.unlock()
                    time.sleep(0.1)
                    self.pause_mutex.lock()

                where = self.log_file.tell()
                line = self.log_file.readline()
                if not line:
                    time.sleep(0.1)
                    self.log_file.seek(where)
                else:
                    self.log_updated.emit(line)

                self.pause_mutex.unlock()

    def pause(self):
        self.pause_mutex.lock()
        self.paused = True
        self.pause_mutex.unlock()

    def resume(self):
        self.pause_mutex.lock()
        self.paused = False
        self.pause_mutex.unlock()

    def is_paused(self):
        return self.paused

    def change_log_file(self, new_log_file_path):
        self.pause()  # Pause the tailing while changing the log file
        if self.log_file:
            self.log_file.close()  # Close the current file handle
        self.log_file_path = new_log_file_path
        self.log_file = open(self.log_file_path, 'r')  # Open the new log file
        self.log_file.seek(0, os.SEEK_END)
        self.resume()  # Resume tailing with the new log file


def set_vpconf_profile(config, serial):
    vpconf_path = utils.winreg_get("SOFTWARE\\VPforce\\RhinoFFB", "path")
    # serial = HapticEffect.device.serial

    if vpconf_path:
        logging.info(f"Found VPforce Configurator at {vpconf_path}")
        workdir = os.path.dirname(vpconf_path)
        env = {}
        env["PATH"] = os.environ["PATH"]
        if not os.path.isfile(config):
            logging.error(f"Error loading VPforce Configurator Profile: ({config}) - The file does not exist! ")
            return

        if not validate_vpconf_profile(config, silent=True):
            logging.error(f"VPForce Config Error: ({config}) - The file failed validation!  Check the PID is correct for the device")
            return

        logging.info(f"set_vpconf_profile - Loading vpconf for with: {vpconf_path} -config {config} -serial {serial}")

        subprocess.call([vpconf_path, "-config", config, "-serial", serial], cwd=workdir, env=env)
    else:
        logging.error("Unable to find VPforce Configurator installation location")

def load_custom_userconfig(new_path=''):
    print(f"newpath=>{new_path}<")
    if new_path == '':
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(None, "Select File", "", "All Files (*)", options=options)
        if file_path == '':
            return
        G.userconfig_rootpath = os.path.basename(file_path)
        G.userconfig_path = file_path
    else:
        G.userconfig_rootpath = os.path.basename(new_path)
        G.userconfig_path = new_path
    xmlutils.update_vars(G._device_type, _userconfig_path=G.userconfig_path, _defaults_path=G.defaults_path)
    G.settings_mgr = SettingsWindow(datasource="Global", device=G.args.type, userconfig_path=G.userconfig_path, defaults_path=G.defaults_path, system_settings=G.system_settings)
    logging.info(f"Custom Configuration was loaded via debug menu: {G.userconfig_path}")
    if G._master_instance and G._launched_children:
        G._ipc_thread.send_broadcast_message(f"LOADCONFIG:{G.userconfig_path}")

def hide_window():

    try:
        G.main_window.hide()
    except Exception as e:
        logging.error(f"EXCEPTION: {e}")


def exit_application():
    # Perform any cleanup or save operations here
    save_main_window_geometry()
    QCoreApplication.instance().quit()


def save_main_window_geometry():
    # Capture the mai n window's geometry
    device_type = G._device_type
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
    if device_type == 'joystick':
        reg_key = 'jWindowData'
    elif device_type == 'pedals':
        reg_key = 'pWindowData'
    elif device_type == 'collective':
        reg_key = 'cWindowData'
    j_window_dict = json.dumps(window_dict)
    utils.set_reg(reg_key, j_window_dict)


def send_test_message():
    if G._ipc_running:
        if G._master_instance:
            G._ipc_thread.send_broadcast_message("TEST MESSAGE TO ALL")
        else:
            G._ipc_thread.send_message("TEST MESSAGE")


def select_sim_for_conversion(window, aircraft_name):
    msg_box = QMessageBox(window)
    msg_box.setIcon(QMessageBox.Question)
    msg_box.setText(f"Please select the simulator to which '{aircraft_name}' from your user configuration belongs:")
    msg_box.setWindowTitle("Simulator Selection")

    msfs_button = QPushButton("MSFS")
    dcs_button = QPushButton("DCS")
    il2_button = QPushButton("IL2")

    msg_box.addButton(msfs_button, QMessageBox.YesRole)
    msg_box.addButton(dcs_button, QMessageBox.NoRole)
    msg_box.addButton(il2_button, QMessageBox.NoRole)

    result = msg_box.exec_()

    if result == 0:  # MSFS button
        return "MSFS"
    elif result == 1:  # DCS button
        return "DCS"
    elif result == 2:  # IL2 button
        return "IL2"
    else:
        return None


def config_to_dict(section, name, value, isim='', device=G.args.type, new_ac=False):
    classes = ['PropellerAircraft', 'JetAircraft', 'TurbopropAircraft', 'Glider', 'Helicopter', 'HPGHelicopter']
    sim = ''
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
        if subsection[1] in classes:  # Make sure it is actually a sim/class section and not a regex aircraft section
            ssim = subsection[0]
            cls = subsection[1]
            match ssim:
                case 'DCS':
                    sim = 'DCS'
                case 'IL2':
                    sim = 'IL2'
                case 'MSFS2020':
                    sim = 'MSFS'

    # if isim is present, is a new aircraft and user has responded with the sim information, add as new model
    if isim != '':
        model = section
        sim = isim
        cls = ''

    # if sim is still blank here, must be a default model section in the user config
    if sim == '':
        model = section
        sim = 'any'
        cls = ''

    data_dict = {
        'name': name,
        'value': value,
        'sim': sim,
        'class': cls,
        'model': model,
        'device': device,
        'new_ac': new_ac
    }
    # print(data_dict)
    return data_dict


def convert_system_settings(sys_dict):
    map_dev_dict = {
        'logging_level': 'logLevel',
        'telemetry_timeout': 'telemTimeout',
    }

    map_sys_dict = {
        'ignore_auto_updates': 'ignoreUpdate',
        'msfs_enabled': 'enableMSFS',
        'dcs_enabled': 'enableDCS',
        'il2_enabled': 'enableIL2',
        'il2_telem_port': 'portIL2',
        'il2_cfg_validation': 'validateIL2',
        'il2_path': 'pathIL2',
    }

    def_dev_dict, def_sys_dict = utils.get_default_sys_settings(G.args.device, G.args.type)

    sys_dict = utils.sanitize_dict(sys_dict)
    for key, value in sys_dict.items():  # iterate through the values in the ini user config
        if key in map_dev_dict:  # check if the key is in the new device specific settings
            new_key = map_dev_dict.get(key, None)  # get the translated key name from the map dictionary
            if new_key is None:  # should never be none since we already checked it existed.. but just in case..
                continue
            def_dev_dict[new_key] = value  # write the old value to the new dictionary with the new key

        elif key in map_sys_dict:  # elif check if the key is in the new system global settings
            new_key = map_sys_dict.get(key, None)  # get the translated key name from the map dictionary
            if new_key is None:  # should never be none since we already checked it existed.. but just in case..
                continue
            def_sys_dict[new_key] = value  # write the old value to the new dictionary with the new key

    # now format and write the new reg keys
    formatted_dev_dict = json.dumps(def_dev_dict)
    formatted_sys_dict = json.dumps(def_sys_dict)
    utils.set_reg('Sys', formatted_sys_dict)
    utils.set_reg(f'{G.args.type}Sys', formatted_dev_dict)
    # sys_out = {}
    # for key, value in sys_dict.items():
    #     reg_key = map_dict.get(key, None)
    #     if reg_key is None:
    #         logging.error(f"System Setting conversion error: '{key}' is not a valid setting!")
    #         continue
    #     sys_out[key] = value
    # out_val = json.dumps(sys_out)
    # utils.set_reg('Sys', out_val)
    # pass


def convert_settings(cfg=_legacy_config_file, usr=_legacy_override_file, window=None):
    differences = []
    defaultconfig = ConfigObj(cfg)
    userconfig = ConfigObj(usr, raise_errors=False)

    def_params = {section: utils.sanitize_dict(defaultconfig[section]) for section in defaultconfig}
    try:
        usr_params = {section: utils.sanitize_dict(userconfig[section]) for section in userconfig}
    except:
        QMessageBox.warning(window, "Conversion Error",
                            "There was an error converting the config.  Please inspect the .ini config for syntax issues.\n\nMake sure all settings fall under a [section] and there are no spaces in any setting name")
        return False
    sys = userconfig.get('system', None)
    for section in usr_params:
        if section == 'system':
            convert_system_settings(usr_params[section])
            continue
        if section in def_params:
            # Compare common keys with different values
            for key, value in usr_params[section].items():
                if key in def_params[section] and def_params[section][key] != value:
                    value = 0 if value == 'not_configured' else value
                    valuestring = str(value)
                    dif_item = config_to_dict(section, key, valuestring)
                    differences.append(dif_item)

            # Identify keys that exist only in the user configuration
            for key, value in usr_params[section].items():
                if key not in def_params[section]:
                    value = 0 if value == 'not_configured' else value
                    valuestring = str(value)
                    dif_item = config_to_dict(section, key, valuestring)
                    differences.append(dif_item)
                    differences.append(dif_item)
        else:
            # All keys in the user configuration section are new
            # non matching sections must be new aircraft

            sim = select_sim_for_conversion(window, section)
            for key, value in usr_params[section].items():
                value = 0 if value == 'not_configured' else value
                valuestring = str(value)
                if key == "type":
                    dev = "any"
                else:
                    dev = G.args.type
                dif_item = config_to_dict(section, key, valuestring, isim=sim, device=dev, new_ac=True)
                differences.append(dif_item)

    xmlutils.write_converted_to_xml(differences)
    return True

def autoconvert_config(main_window, cfg=_legacy_config_file, usr=_legacy_override_file):
    if G._child_instance: return
    if usr is not None:
        ans = QMessageBox.information(
            main_window,
            "Important TelemFFB Update Notification",
            textwrap.dedent(f'''
                The 'ini' config file type is now deprecated.
            
                This version of TelemFFB uses a new UI-driven config model.
            
                It appears you are using a user override file ({usr}).  Would you
                like to auto-convert that file to the new config model?
            
                If you choose no, you may also perform the conversion from
                the Utilities menu.
            
                Proceeding will convert the config and re-name your
                existing user config to '{os.path.basename(usr)}.legacy'
            '''),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if ans == QMessageBox.No:
            return
        if not os.path.isfile(_legacy_override_file):
            QMessageBox.warning(main_window, "Error", f"Legacy override file {usr} was passed at runtime for auto-conversion, but the file does not exist")
            return
        # perform the conversion
        if not convert_settings(cfg=cfg, usr=usr, window=main_window):
            return False
        try:
            os.rename(usr, f"{usr}.legacy")
        except OSError:
            ans = QMessageBox.warning(main_window, 'Warning', f'The legacy backup file for "{usr}" already exists, would you like to replace it?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans == QMessageBox.Yes:
                os.replace(usr, f"{usr}.legacy")

        QMessageBox.information(main_window, "Conversion Completed", '''
            The conversion is complete.
            
            If you are utilizing multiple VPforce FFB enabled devices, please set up the auto-launch capabilities in the system settings menu.
            
            To avoid unnecessary log messages, please remove any '-c' or '-o' arguments from your startup shortcut as they are no longer supported'''
                                )


def restart_sims():
    sim_list = ['DCS', 'MSFS', 'IL2', 'XPLANE']
    sys_settings = utils.read_system_settings(G.args.device, G._device_type)
    stop_sims()
    init_sims()
    G.main_window.init_sim_indicators(sim_list, sys_settings)


def init_sims():
    global dcs_telem, il2_telem, sim_connect_telem, xplane_telem

    xplane_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableXPLANE', False)

    xplane_telem = NetworkThread(G.telem_manager, host='', port=34390)
    # xplane_enabled = utils.read_system_settings(args.device, args.type).get('enableXPLANE', False)
    if xplane_enabled or G.args.sim == 'XPLANE':
        if not G._child_instance and utils.read_system_settings(G.args.device, G.args.type).get('validateXPLANE', False):
            xplane_path = utils.read_system_settings(G.args.device, G.args.type).get('pathXPLANE', '')
            utils.install_xplane_plugin(xplane_path, G.main_window)
        logging.info("Starting XPlane Telemetry Listener")
        xplane_telem.start()

    dcs_telem = NetworkThread(G.telem_manager, host="", port=34380)
    # dcs_enabled = utils.sanitize_dict(config["system"]).get("dcs_enabled", None)
    dcs_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableDCS', False)
    if dcs_enabled or G.args.sim == "DCS":
        # check and install/update export lua script
        if not G._child_instance:
            utils.install_export_lua(G.main_window)
        logging.info("Starting DCS Telemetry Listener")
        dcs_telem.start()

    il2_mgr = IL2Manager()
    # il2_port = utils.sanitize_dict(config["system"]).get("il2_telem_port", 34385)
    il2_port = int(utils.read_system_settings(G.args.device, G.args.type).get('portIL2', 34385))
    # il2_path = utils.sanitize_dict(config["system"]).get("il2_path", 'C: \\Program Files\\IL-2 Sturmovik Great Battles')
    il2_path = utils.read_system_settings(G.args.device, G.args.type).get('pathIL2', 'C: \\Program Files\\IL-2 Sturmovik Great Battles')
    # il2_validate = utils.sanitize_dict(config["system"]).get("il2_cfg_validation", True)
    il2_validate = utils.read_system_settings(G.args.device, G.args.type).get('validateIL2', True)
    il2_telem = NetworkThread(G.telem_manager, host="", port=il2_port, telem_parser=il2_mgr)

    # il2_enabled = utils.sanitize_dict(config["system"]).get("il2_enabled", None)
    il2_enabled = utils.read_system_settings(G.args.device, G.args.type).get('enableIL2', False)

    if il2_enabled or G.args.sim == "IL2":
        if not G._child_instance:
            if il2_validate:
                utils.analyze_il2_config(il2_path, port=il2_port, window=G.main_window)
            else:
                logging.warning(
                    "IL2 Config validation is disabled - please ensure the IL2 startup.cfg is configured correctly")
        logging.info("Starting IL2 Telemetry Listener")
        il2_telem.start()

    sim_connect_telem = SimConnectSock(G.telem_manager)
    msfs = utils.read_system_settings(G.args.device, G.args.type).get('enableMSFS', False)

    try:
        # msfs = utils.sanitize_dict(config["system"]).get("msfs_enabled", None)
        logging.debug(f"MSFS={msfs}")
        if msfs or G.args.sim == "MSFS":
            logging.info("MSFS Enabled:  Starting Simconnect Manager")
            sim_connect_telem.start()
            aircrafts_msfs_xp.Aircraft.set_simconnect(sim_connect_telem)

    except:
        logging.exception("Error loading MSFS enable flag from config file")

    dcs_status = "Enabled" if dcs_enabled else "Disabled"
    il2_status = "Enabled" if il2_enabled else "Disabled"
    msfs_status = "Enabled" if msfs else "Disabled"

    G.main_window.refresh_telem_status(dcs_enabled, il2_enabled, msfs, xplane_enabled)

def stop_sims():
    xplane_telem.quit()
    dcs_telem.quit()
    il2_telem.quit()
    sim_connect_telem.quit()

# this needs a better solution
G.stop_sims = stop_sims
G.init_sims = init_sims

def notify_close_children():
    if not len(G._child_ipc_ports) or not G._ipc_running:
        return
    G._ipc_thread.send_broadcast_message("MASTER INSTANCE QUIT")


def launch_children():
    global script_dir
    if not G.system_settings.get('autolaunchMaster', False) or G.args.child or not G._master_instance:
        return False

    if getattr(sys, 'frozen', False):
        app = ['VPForce-TelemFFB.exe']
    else:
        app = ['python', 'main.py']

    master_port = f"6{G._device_pid}"
    # full_path = os.path.join(script_dir, app)
    try:
        if G.system_settings.get('autolaunchJoystick', False) and G._device_type != 'joystick':
            min = ['--minimize'] if G.system_settings.get('startMinJoystick', False) else []
            headless = ['--headless'] if G.system_settings.get('startHeadlessJoystick', False) else []
            pid = G.system_settings.get('pidJoystick', '2055')
            vidpid = f"FFFF:{pid}"
            command = app + ['-D', vidpid, '-t', 'joystick', '--child', '--masterport', master_port] + min + headless
            logging.info(f"Auto-Launch: starting instance: {command}")
            subprocess.Popen(command)
            G._launched_joystick = True
            G._child_ipc_ports.append(int(f"6{pid}"))
        if G.system_settings.get('autolaunchPedals', False) and G._device_type != 'pedals':
            min = ['--minimize'] if G.system_settings.get('startMinPedals', False) else []
            headless = ['--headless'] if G.system_settings.get('startHeadlessPedals', False) else []
            pid = G.system_settings.get('pidPedals', '2055')
            vidpid = f"FFFF:{pid}"
            command = app + ['-D', vidpid, '-t', 'pedals', '--child', '--masterport', master_port] + min + headless
            logging.info(f"Auto-Launch: starting instance: {command}")
            subprocess.Popen(command)
            G._launched_pedals = True
            G._child_ipc_ports.append(int(f"6{pid}"))
        if G.system_settings.get('autolaunchCollective', False) and G._device_type != 'collective':
            min = ['--minimize'] if G.system_settings.get('startMinCollective', False) else []
            headless = ['--headless'] if G.system_settings.get('startHeadlessCollective', False) else []
            pid = G.system_settings.get('pidCollective', '2055')
            vidpid = f"FFFF:{pid}"
            command = app + ['-D', vidpid, '-t', 'collective', '--child', '--masterport', master_port] + min + headless
            logging.info(f"Auto-Launch: starting instance: {command}")
            subprocess.Popen(command)
            G._launched_collective = True
            G._child_ipc_ports.append(int(f"6{pid}"))
    except Exception as e:
        logging.error(f"Error during Auto-Launch sequence: {e}")
    return True


def main():
    app = QApplication(sys.argv)

    def excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(tb)
        #QtWidgets.QApplication.quit()
        # or QtWidgets.QApplication.exit(0)
    sys.excepthook = excepthook

    app.setStyleSheet(
        """
            QCheckBox::indicator:checked { image: url(:/image/purplecheckbox.png); }
            QRadioButton::indicator:checked { image: url(:/image/rchecked.png);}
            
            QPushButton, #styledButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                  stop: 0 #ab37c8, stop: 0.4 #9a24b5,
                                                  stop: 0.5 #8e1da8, stop: 1.0 #ab37c8);
                border: 1px solid #6e1d6f;
                border-radius: 5px;
                padding: 3px;
                margin: 1px;
                color: white;
            }
            QPushButton:disabled {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                  stop: 0 #cccccc, stop: 0.4 #bbbbbb,
                                                  stop: 0.5 #C1ADC6, stop: 1.0 #cccccc);
                color: #666666;  /* Set the text color for disabled buttons */
                border: 1px solid #6e1d6f;
                border-radius: 5px;
                padding: 3px;
                margin: 1px;
            }
            QPushButton:hover, #styledButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                stop: 0 #d97ed1, stop: 0.4 #d483d4,
                                                stop: 0.5 #d992da, stop: 1.0 #e4a9e7);
            }

            QComboBox::down-arrow {
                image: url(:/image/down-down.png);
            }

            QComboBox QAbstractItemView {
                border: 2px solid darkgray;
                selection-background-color: #ab37c8;
            }
            QLineEdit {
                selection-background-color: #ab37c8;  /* Set the highlight color for selected text */
            }
            QPlainTextEdit {
                selection-background-color: #ab37c8;  /* Set the highlight color for selected text */
            }
            QSlider::handle:horizontal {
                background: #ab37c8; /* Set the handle color */
                border: 1px solid #565a5e;
                width: 16px;  /* Adjusted handle width */
                height: 20px;  /* Adjusted handle height */
                border-radius: 5px;  /* Adjusted border radius */
                margin-top: -5px;  /* Negative margin to overlap with groove */
                margin-bottom: -5px;  /* Negative margin to overlap with groove */
                margin-left: -1px;  /* Adjusted left margin */
                margin-right: -1px;  /* Adjusted right margin */
            }
            QSlider::handle:horizontal:disabled {
                background: #888888; /* Set the color of the handle when disabled */
            }
        """
    )
    # TODO: Avoid globals
    global log_window, log_tail_window
    global dev_firmware_version
    global dev_serial
    global dev

    log_window = LogWindow()

    xmlutils.update_vars(G.args.type, G.userconfig_path, G.defaults_path)
    try:
        G.settings_mgr = SettingsWindow(datasource="Global", device=G.args.type, userconfig_path=G.userconfig_path, defaults_path=G.defaults_path, system_settings=G.system_settings)
    except Exception as e:
        traceback.print_exc()
        logging.error(f"Error Reading user config file..")
        ans = QMessageBox.question(None, "User Config Error", "There was an error reading the userconfig.  The file is likely corrupted.\n\nDo you want to back-up the existing config and create a new default (empty) config?\n\nIf you chose No, TelemFFB will exit.")
        if ans == QMessageBox.Yes:
            # Get the current timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')

            # Create the backup file name with the timestamp
            backup_file = os.path.join(G.userconfig_rootpath, ('userconfig_' + os.environ['USERNAME'] + "_" + timestamp + '_corrupted.bak'))

            # Copy the file to the backup file
            shutil.copy(G.userconfig_path, backup_file)

            logging.debug(f"Backup created: {backup_file}")

            os.remove(G.userconfig_path)
            utils.create_empty_userxml_file(G.userconfig_path)

            logging.info(f"User config Reset:  Backup file created: {backup_file}")
            G.settings_mgr = SettingsWindow(datasource="Global", device=G.args.type, userconfig_path=G.userconfig_path, defaults_path=G.defaults_path, system_settings=G.system_settings)
            QMessageBox.information(None, "New Userconfig created", f"A backup has been created: {backup_file}\n")
        else:
            QCoreApplication.instance().quit()
            return

    icon_path = ":/image/vpforceicon.png"
    G.settings_mgr.setWindowIcon(QIcon(icon_path))
    sys.stdout = utils.OutLog(log_window.widget, sys.stdout)
    sys.stderr = utils.OutLog(log_window.widget, sys.stderr)

    log_window.pause_button.clicked.connect(sys.stdout.toggle_pause)

    logging.getLogger().handlers[0].setStream(sys.stdout)
    logging.info(f"TelemFFB (version {version}) Starting")
    try:
        vid_pid = [int(x, 16) for x in _device_vid_pid.split(":")]
    except:
        pass

    devs = FFBRhino.enumerate()
    logging.info("Available Rhino Devices:")
    logging.info("-------")
    for dev in devs:

        logging.info(f"* {dev.vendor_id:04X}:{dev.product_id:04X} - {dev.product_string} - {dev.serial_number}")
        logging.info(f"* Path:{dev.path}")
        logging.info(f"*")

    logging.info("-------")


    try:
        dev = HapticEffect.open(vid_pid[0], vid_pid[1])  # try to open RHINO
        if G.args.reset:
            dev.resetEffects()
        dev_firmware_version = dev.get_firmware_version()
        dev_serial = dev.serial
        if dev_firmware_version:
            logging.info(f"Rhino Firmware: {dev_firmware_version}")
            minver = re.sub(r'\D', '', min_firmware_version)
            devver = re.sub(r'\D', '', dev_firmware_version)
            if devver < minver:
                QMessageBox.warning(None, "Outdated Firmware", f"This version of TelemFFB requires Rhino Firmware version {min_firmware_version} or later.\n\nThe current version installed is {dev_firmware_version}\n\n\n Please update to avoid errors!")

    except Exception as e:
        QMessageBox.warning(None, "Cannot connect to Rhino", f"Unable to open HID at {_device_vid_pid} for device: {G._device_type}\nError: {e}\n\nPlease open the System Settings and verify the Master\ndevice PID is configured correctly")
        dev_firmware_version = 'ERROR'

    # config = get_config()
    # ll = config["system"].get("logging_level", "INFO")
    ll = utils.read_system_settings(G.args.device, G.args.type).get('logLevel', 'INFO')
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    logger.setLevel(log_levels.get(ll, logging.DEBUG))
    logging.info(f"Logging level set to:{logging.getLevelName(logger.getEffectiveLevel())}")

    global is_master, _child_ipc_ports, _launched_children
    G._ipc_running = False
    is_master = launch_children()
    if is_master:
        myport = int(f"6{G._device_pid}")
        G._ipc_thread = IPCNetworkThread(master=True, myport=myport, child_ports=G._child_ipc_ports)
        G._ipc_thread.child_keepalive_signal.connect(lambda device, status: G.main_window.update_child_status(device, status))
        G._ipc_thread.start()
        G._ipc_running = True
        _launched_children = True
    elif G.args.child:
        myport = int(f"6{G._device_pid}")
        G._ipc_thread = IPCNetworkThread(child=True, myport=myport, dstport=G.args.masterport)
        G._ipc_thread.exit_signal.connect(lambda: exit_application())
        G._ipc_thread.restart_sim_signal.connect(lambda: restart_sims())
        G._ipc_thread.show_signal.connect(lambda: G.main_window.show())
        G._ipc_thread.hide_signal.connect(lambda: G.main_window.hide())
        G._ipc_thread.showlog_signal.connect(lambda: log_window.show())
        G._ipc_thread.show_settings_signal.connect(lambda: G.main_window.open_system_settings_dialog())
        G._ipc_thread.start()
        G._ipc_running = True

    G.main_window = MainWindow(ipc_thread=G._ipc_thread)

    # log_tail_window = LogTailWindow(window)

    if not headless_mode:
        if G.args.minimize:
            G.main_window.showMinimized()
        else:
            G.main_window.show()

    autoconvert_config(G.main_window)
    if not _release:
        fetch_version_thread = utils.FetchLatestVersionThread()
        fetch_version_thread.version_result_signal.connect(G.main_window.update_version_result)
        fetch_version_thread.error_signal.connect(lambda error_message: print("Error in thread:", error_message))
        fetch_version_thread.start()

    G.telem_manager = TelemManager(settings_manager=G.settings_mgr, ipc_thread=G._ipc_thread)
    G.telem_manager.start()

    G.main_window.set_telem_manager(G.telem_manager)

    G.telem_manager.telemetryReceived.connect(G.main_window.update_telemetry)
    G.telem_manager.updateSettingsLayout.connect(G.main_window.update_settings)

    init_sims()

    if is_master:
        G.main_window.show_device_logo()
        G.main_window.enable_device_logo_click(True)
        current_title = G.main_window.windowTitle()
        new_title = f"** MASTER INSTANCE ** {current_title}"
        G.main_window.setWindowTitle(new_title)
        if G._launched_joystick:
            G.main_window.joystick_status_icon.show()
        if G._launched_pedals:
            G.main_window.pedals_status_icon.show()
        if G._launched_collective:
            G.main_window.collective_status_icon.show()

    if config_was_default:
        G.main_window.open_system_settings_dialog()

    utils.signal_emitter.telem_timeout_signal.connect(G.main_window.update_sim_indicators)
    utils.signal_emitter.error_signal.connect(G.main_window.process_error_signal)
    utils.signal_emitter.msfs_quit_signal.connect(restart_sims)

    # do some init in the background not blocking the main window first appearance
    def init_async():
        if G.system_settings.get('enableVPConfStartup', False):
            try:
                set_vpconf_profile(G.system_settings.get('pathVPConfStartup', ''), dev_serial)
            except:
                logging.error("Unable to set VPConfigurator startup profile")

        try:
            G.startup_configurator_gains = dev.getGains()
        except:
            logging.error("Unable to get configurator slider values from device")

    threading.Thread(target=init_async, daemon=True).start()

    app.exec_()

    if G._ipc_running:
        notify_close_children()
        G._ipc_thread.stop()
    stop_sims()
    G.telem_manager.quit()
    if G.system_settings.get('enableVPConfExit', False):
        try:
            set_vpconf_profile(G.system_settings.get('pathVPConfExit', ''), dev_serial)
        except:
            logging.error("Unable to set VPConfigurator exit profile")


if __name__ == "__main__":
    main()
