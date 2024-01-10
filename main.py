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

from traceback_with_variables import print_exc, prints_exc
import argparse
import json
import logging
import sys
import time
import os
import utils
import re
import socket
import threading
from collections import OrderedDict
import subprocess

import traceback
from ffb_rhino import HapticEffect
from configobj import ConfigObj

from settingsmanager import *
import xmlutils

from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QMainWindow, QVBoxLayout, QMessageBox, QPushButton, QDialog, \
    QRadioButton, QListView, QScrollArea, QHBoxLayout, QAction, QPlainTextEdit, QMenu, QButtonGroup, QFrame, \
    QDialogButtonBox, QSizePolicy, QSpacerItem, QTabWidget, QGroupBox, QShortcut
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QCoreApplication, QUrl, QRect, QMetaObject, QSize, QByteArray, QTimer, \
    QThread, QMutex, QRegExp
from PyQt5.QtGui import QFont, QPixmap, QIcon, QDesktopServices, QPainter, QColor, QKeyEvent, QIntValidator, QCursor, \
    QTextCursor, QRegExpValidator, QKeySequence
from PyQt5.QtWidgets import QGridLayout, QToolButton, QStyle

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

args = parser.parse_args()

# script_dir = os.path.dirname(os.path.abspath(__file__))
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True) #enable highdpi scaling
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True) #use highdpi icons

headless_mode = args.headless

config_was_default = False
_launched_joystick = False
_launched_pedals = False
_launched_collective = False
_launched_children = False
_child_ipc_ports = []
_master_instance = False
_ipc_running = False
_ipc_thread = None
_child_instance = args.child

system_settings = utils.read_system_settings(args.device, args.type)

if system_settings.get('wasDefault', False):
    config_was_default = True

# _vpf_logo = os.path.join(script_dir, "image/vpforcelogo.png")
_vpf_logo = ":/image/vpforcelogo.png"
if args.device is None:
    master_rb = system_settings.get('masterInstance', 1)
    match master_rb:
        case 1:
            _device_pid = system_settings.get('pidJoystick', "2055")
            _device_type = 'joystick'
            _device_logo = ':/image/logo_j.png'
        case 2:
            _device_pid = system_settings.get('pidPedals', "2055")
            _device_type = 'pedals'
            _device_logo = ':/image/logo_p.png'
        case 3:
            _device_pid = system_settings.get('pidCollective', "2055")
            _device_type = 'collective'
            _device_logo = ':/image/logo_c.png'
        case _:
            _device_pid = system_settings.get('pidJoystick', "2055")
            _device_type = 'joystick'
            _device_logo = ':/image/logo_j.png'

    _device_vid_pid = f"FFFF:{_device_pid}"
    args.type = _device_type
else:
    if args.type is None:
        _device_type = 'joystick'
        args.type = _device_type
    else:
        _device_type = str.lower(args.type)

    _device_pid = args.device.split(":")[1]
    _device_vid_pid = args.device
    match str.lower(args.type):
        case 'joystick':
            _device_logo = ':/image/logo_j.png'
        case 'pedals':
            _device_logo = ':/image/logo_p.png'
        case 'collective':
            _device_logo = ':/image/logo_c.png'
        case _:
            _device_logo = ':/image/logo_j.png'

args.sim = str.upper(args.sim)
args.type = str.lower(args.type)

# need to determine if someone has auto-launch enabled but has started an instance with -D
# The 'masterInstance' reg key holds the radio button index of the configured master instance
# 1=joystick, 2=pedals, 3=collective
index_dict = {
    'joystick': 1,
    'pedals': 2,
    'collective': 3
}
master_index = system_settings.get('masterInstance', 1)
if index_dict[_device_type] == master_index:
    _master_instance = True
else:
    _master_instance = False


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
global dev_firmware_version, dcs_telem, il2_telem, sim_connect_telem, settings_mgr, telem_manager
global window, log_window, log_folder, log_file, log_tail_window

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
        record.device_type = _device_type
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

if args.overridefile == 'None':
    # Need to determine if user is using default config.user.ini without passing the override flag:
    if os.path.isfile(os.path.join(_install_path, 'config.user.ini')):
        _legacy_override_file = os.path.join(_install_path, 'config.user.ini')

else:
    if not os.path.isabs(args.overridefile):  # user passed just file name, construct absolute path from script/exe directory
        ovd_path = utils.get_resource_path(args.overridefile, prefer_root=True, force=True)
    else:
        ovd_path = args.overridefile  # user passed absolute path, use that

    if os.path.isfile(ovd_path):
        _legacy_override_file = ovd_path
    else:
        _legacy_override_file = ovd_path
        logging.warning(f"Override file {args.overridefile} passed with -o argument, but can not find the file for auto-conversion")


log_folder = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB", 'log')

if not os.path.exists(log_folder):
    os.makedirs(log_folder)

date_str = datetime.now().strftime("%Y%m%d")

logname = "".join(["TelemFFB", "_", _device_vid_pid.replace(":", "-"), '_', args.type, "_", date_str, ".log"])
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

defaults_path = utils.get_resource_path('defaults.xml', prefer_root=True)

userconfig_rootpath = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB")
userconfig_path = os.path.join(userconfig_rootpath, 'userconfig.xml')

utils.create_empty_userxml_file(userconfig_path)

if not args.child:
    try:    # in case other instance tries doing at the same time
        utils.archive_logs(log_folder)
    except:
        pass

import aircrafts_dcs
import aircrafts_msfs
import aircrafts_il2
from sc_manager import SimConnectManager
from il2_telem import IL2Manager
from aircraft_base import effects


if getattr(sys, 'frozen', False):
    appmode = 'Executable'
else:
    appmode = 'Source'
logging.info("**************************************")
logging.info("**************************************")
logging.info(f"*****    TelemFFB starting up from {appmode}:  Args= {args.__dict__}")
logging.info("**************************************")
logging.info("**************************************")
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


def config_has_changed(update=False) -> bool:
    # if update is true, update the current modified time
    global _config_mtime, _future_config_update_time, _pending_config_update
    # "hash" both mtimes together
    tm = int(os.path.getmtime(userconfig_path)) + int(os.path.getmtime(defaults_path))
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
        self.setWindowTitle(f"Log Console ({args.type})")
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


class LogTailWindow(QMainWindow):
    def __init__(self, main_window):
        super(LogTailWindow, self).__init__()

        self.main_window = main_window
        self.setWindowTitle(f"Log File Monitor ({args.type})")
        # Construct the absolute path of the icon file
        icon_path = os.path.join(script_dir, "image/vpforceicon.png")
        icon = QIcon(":/image/vpforceicon.png")
        self.setWindowIcon(icon)
        self.resize(800, 500)
        self.move(self.main_window.x() + 50, self.main_window.y() + 100)
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.log_tail_thread = self.main_window.log_tail_thread

        # Replicate the log_tab_widget contents in the new window
        self.log_widget = QPlainTextEdit(self.central_widget)
        self.log_widget.setReadOnly(True)
        self.log_widget.setFont(QFont("Courier New"))
        self.log_widget.setLineWrapMode(QPlainTextEdit.NoWrap)

        self.clear_button = QPushButton("Clear", self.central_widget)
        self.toggle_button = QPushButton("Pause", self.central_widget)
        self.close_button = QPushButton("Close Window", self.central_widget)
        self.clear_button.clicked.connect(self.clear_log_widget)
        self.toggle_button.clicked.connect(self.toggle_log_tailing)
        self.close_button.clicked.connect(lambda: self.hide())

        # self.open_log_button.clicked.connect(self.toggle_log_window)

        # Layout for the new window
        layout = QVBoxLayout(self.central_widget)
        layout.addWidget(self.log_widget)
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.toggle_button)
        button_layout.addStretch()  # Add stretch to push the next button to the right
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

        self.log_tail_thread.log_updated.connect(self.update_log_widget)

    def toggle_log_tailing(self):
        if self.log_tail_thread.is_paused():
            self.log_tail_thread.resume()
            self.toggle_button.setText("Pause")
        else:
            self.log_tail_thread.pause()
            self.toggle_button.setText("Resume")

    def clear_log_widget(self):
        self.log_widget.clear()

    def update_log_widget(self, log_line):
        cursor = self.log_widget.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(log_line)
        self.log_widget.setTextCursor(cursor)
        self.log_widget.ensureCursorVisible()


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

            cls_name, pattern, result = xmlutils.read_single_model(the_sim, aircraft_name, input_modeltype, args.type)
            settings_mgr.current_pattern = pattern
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
        telem_data["FFBType"] = args.type

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
        if _master_instance and _launched_children:
            self._ipc_telem = self.ipc_thread._ipc_telem
            if self._ipc_telem != {}:
                telem_data.update(self._ipc_telem)
                self._ipc_telem = {}
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

                self.currentAircraft.apply_settings(params)
                self.currentAircraftConfig = params

                if settings_mgr.isVisible():
                    settings_mgr.b_getcurrentmodel.click()

                self.updateSettingsLayout.emit()

            self.currentAircraftName = aircraft_name

        if self.currentAircraft:
            if config_has_changed():
                logging.info("Configuration has changed, reloading")
                params, cls_name = self.get_aircraft_config(aircraft_name, data_source)
                updated_params = self.get_changed_params(params)
                self.currentAircraft.apply_settings(updated_params)
                self.updateSettingsLayout.emit()
            try:
                _tm = time.perf_counter()
                self.currentAircraft._telem_data = telem_data
                self.currentAircraft.on_telemetry(telem_data)
                telem_data["perf"] = f"{(time.perf_counter() - _tm) * 1000:.3f}ms"

            except:
                print_exc()
        # Send locally generated telemetry to master here
        if args.child and self.currentAircraft:
            ipc_telem = self.currentAircraft._ipc_telem
            if ipc_telem != {}:
                self.ipc_thread.send_ipc_telem(ipc_telem)
                self.currentAircraft._ipc_telem = {}
        if args.plot:
            for item in args.plot:
                if item in telem_data:
                    if _child_instance or _launched_children:
                        utils.teleplot.sendTelemetry(item, telem_data[item], instance=_device_type)
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

    @prints_exc
    def run(self):
        self.timeout = int(utils.read_system_settings(args.device, args.type).get('telemTimeout', 200))/1000
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
    child_keepalive_signal = pyqtSignal(str, str)

    def __init__(self, host="localhost", myport=0, dstport=0, child_ports=[], master=False, child=False, keepalive_timer=1, missed_keepalive=3):
        super().__init__()
        global window
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
            QMessageBox.warning(None, "Error", f"There was an error while setting up the inter-instance communications for the {_device_type} instance of TelemFFB.\n\n{e}\nLikely there is a hung instance of TelemFFB (or python if running from source) that is holding the socket open.\n\nPlease close any instances of TelemFFB and then open Task Manager and kill any existing instances of TelemFFB")
            QCoreApplication.instance().quit()

    def send_ipc_telem(self, telem):
        j_telem = json.dumps(telem)
        message = f"telem:{j_telem}"
        self.send_message(message)

    def send_ipc_effects(self, active_effects, active_settings):
        payload = {
            f'{_device_type}_active_effects': active_effects,
            f'{_device_type}_active_settings': active_settings
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
                self.send_message(f"Child Keepalive:{_device_type}")
                ts = time.time()
                logging.debug(f"{_device_type} SENT CHILD KEEPALIVE: {ts}")
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
                    if dev == _device_type:
                        logging.info("Show log command received via IPC")
                        self.showlog_signal.emit()
                elif msg == 'SHOW WINDOW':
                    logging.info("Show command received via IPC")
                    self.show_signal.emit()
                elif msg == 'HIDE WINDOW':
                    logging.info("Hide command received via IPC")
                    self.hide_signal.emit()
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
    def __init__(self, telem: TelemManager):
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


class Ui_TeleplotDialog(object):
    def setupUi(self, TeleplotDialog):
        if not TeleplotDialog.objectName():
            TeleplotDialog.setObjectName(u"TeleplotDialog")
        TeleplotDialog.resize(273, 381)
        self.layoutWidget = QWidget(TeleplotDialog)
        self.layoutWidget.setObjectName(u"layoutWidget")
        self.layoutWidget.setGeometry(QRect(7, 10, 258, 296))
        self.gridLayout = QGridLayout(self.layoutWidget)
        self.gridLayout.setObjectName(u"gridLayout")
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.tb_port = QLineEdit(self.layoutWidget)
        self.tb_port.setObjectName(u"tb_port")

        self.gridLayout.addWidget(self.tb_port, 2, 0, 1, 1, Qt.AlignLeft)

        self.label = QLabel(self.layoutWidget)
        self.label.setObjectName(u"label")

        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)

        self.label_2 = QLabel(self.layoutWidget)
        self.label_2.setObjectName(u"label_2")

        self.gridLayout.addWidget(self.label_2, 1, 0, 1, 1)

        self.tb_vars = QPlainTextEdit(self.layoutWidget)
        self.tb_vars.setObjectName(u"tb_vars")

        self.gridLayout.addWidget(self.tb_vars, 5, 0, 1, 1)

        self.label_3 = QLabel(self.layoutWidget)
        self.label_3.setObjectName(u"label_3")

        self.gridLayout.addWidget(self.label_3, 3, 0, 1, 1)

        self.label_4 = QLabel(self.layoutWidget)
        self.label_4.setObjectName(u"label_4")

        self.gridLayout.addWidget(self.label_4, 4, 0, 1, 1)

        self.label_5 = QLabel(TeleplotDialog)
        self.label_5.setObjectName(u"label_5")
        self.label_5.setGeometry(QRect(7, 310, 256, 26))
        self.widget = QWidget(TeleplotDialog)
        self.widget.setObjectName(u"widget")
        self.widget.setGeometry(QRect(10, 350, 251, 25))
        self.horizontalLayout = QHBoxLayout(self.widget)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setContentsMargins(0, 0, 0, 0)
        self.pb_clear = QPushButton(self.widget)
        self.pb_clear.setObjectName(u"pb_clear")

        self.horizontalLayout.addWidget(self.pb_clear, 0, Qt.AlignLeft)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout.addItem(self.horizontalSpacer_2)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout.addItem(self.horizontalSpacer)

        self.buttonBox = QDialogButtonBox(self.widget)
        self.buttonBox.setObjectName(u"buttonBox")
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Cancel | QDialogButtonBox.Save)

        self.horizontalLayout.addWidget(self.buttonBox, 0, Qt.AlignRight)

        self.retranslateUi(TeleplotDialog)

        QMetaObject.connectSlotsByName(TeleplotDialog)
    # setupUi

    def retranslateUi(self, TeleplotDialog):
        TeleplotDialog.setWindowTitle(QCoreApplication.translate("TeleplotDialog", u"Teleplot Setup", None))
        self.label.setText(QCoreApplication.translate("TeleplotDialog", u"Open a browser to teleplot.fr, record port number", None))
        self.label_2.setText(QCoreApplication.translate("TeleplotDialog", u"Port:", None))
        self.label_3.setText(QCoreApplication.translate("TeleplotDialog", u"Space separated list of Telemetry variables", None))
        self.label_4.setText(QCoreApplication.translate("TeleplotDialog", u"List:", None))
        self.label_5.setText(QCoreApplication.translate("TeleplotDialog", u"To stop sending teleplot data,\nclear the boxes and select OK", None))
        self.pb_clear.setText(QCoreApplication.translate("TeleplotDialog", u"Clear", None))
    # retranslateUi


class TeleplotSetupDialog(QDialog, Ui_TeleplotDialog):

    def __init__(self, parent=None):
        super(TeleplotSetupDialog, self).__init__(parent)

        if args.teleplot is None:
            self.telem_port = ''
        elif isinstance(args.teleplot, str):
            if ':' in args.teleplot:
                self.telem_port = args.teleplot.split(':')[1]
            else:
                self.telem_port = args.teleplot

        if args.plot is None:
            args.plot = []
        self.telem_vars = ' '.join(args.plot)

        self.setupUi(self)
        self.retranslateUi(self)
        self.parent = parent
        int_validator = QIntValidator()
        self.tb_port.setValidator(int_validator)
        self.buttonBox.accepted.connect(self.save_teleplot)
        self.buttonBox.rejected.connect(self.close)
        self.pb_clear.clicked.connect(self.clear_form)
        self.tb_port.setText(self.telem_port)
        self.tb_vars.setPlainText(str(self.telem_vars))

    def save_teleplot(self):
        if self.validate_text():
            if self.tb_port == '':
                args.plot = []
                self.accept()
            else:
                address = f"teleplot.fr:{str(self.tb_port.text())}"
                utils.teleplot.configure(address)
                args.plot = self.tb_vars.toPlainText().split()
                args.teleplot = str(self.tb_port.text())
                self.accept()

    def clear_form(self):
        self.tb_port.clear()
        self.tb_vars.clear()

    def validate_text(self):
        regex_string = r"[a-zA-Z_][a-zA-Z0-9_ ]*"
        current_text = self.tb_vars.toPlainText()
        validator = QRegExpValidator(QRegExp(regex_string))
        pos = 0
        state, valid_text, pos = validator.validate(current_text, pos)
        if self.tb_port.text() == '':
            if current_text == '':
                # remove all teleplot
                return True
            elif current_text != '':
                QMessageBox.warning(self, "Error", "Please enter a port number or remove the telemetry variables to stop sending")
                return False

        if current_text == '':
            if self.tb_port.text() != '':
                QMessageBox.warning(self, "Error", "Please enter telemetry variables to monitor or remove the port to stop sending")
                return False

        if state == QRegExpValidator.Acceptable or current_text == '':
            return True
        else:
            QMessageBox.warning(self, "Error", "Please only enter valid variable characters")
            return False


class Ui_SystemDialog(object):
    def setupUi(self, SystemDialog):
        if not SystemDialog.objectName():
            SystemDialog.setObjectName(u"SystemDialog")
        SystemDialog.resize(607, 524)
        sizePolicy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(SystemDialog.sizePolicy().hasHeightForWidth())
        SystemDialog.setSizePolicy(sizePolicy)
        SystemDialog.setMinimumSize(QSize(490, 355))
        SystemDialog.setMaximumSize(QSize(800, 800))
        self.line = QFrame(SystemDialog)
        self.line.setObjectName(u"line")
        self.line.setGeometry(QRect(9, 176, 581, 16))
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
        self.buttonBox.setGeometry(QRect(402, 492, 156, 23))
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        self.resetButton = QPushButton(SystemDialog)
        self.resetButton.setObjectName(u"resetButton")
        self.resetButton.setGeometry(QRect(20, 492, 101, 23))
        self.layoutWidget = QWidget(SystemDialog)
        self.layoutWidget.setObjectName(u"layoutWidget")
        self.layoutWidget.setGeometry(QRect(20, 39, 258, 71))
        self.gridLayout = QGridLayout(self.layoutWidget)
        self.gridLayout.setObjectName(u"gridLayout")
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(self.layoutWidget)
        self.label.setObjectName(u"label")

        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)

        self.logLevel = QComboBox(self.layoutWidget)
        self.logLevel.setObjectName(u"logLevel")

        self.gridLayout.addWidget(self.logLevel, 0, 1, 1, 1)

        self.label_2 = QLabel(self.layoutWidget)
        self.label_2.setObjectName(u"label_2")

        self.gridLayout.addWidget(self.label_2, 1, 0, 1, 1)

        self.telemTimeout = QLineEdit(self.layoutWidget)
        self.telemTimeout.setObjectName(u"telemTimeout")

        self.gridLayout.addWidget(self.telemTimeout, 1, 1, 1, 1)

        self.ignoreUpdate = QCheckBox(self.layoutWidget)
        self.ignoreUpdate.setObjectName(u"ignoreUpdate")

        self.gridLayout.addWidget(self.ignoreUpdate, 2, 0, 1, 2)

        self.layoutWidget1 = QWidget(SystemDialog)
        self.layoutWidget1.setObjectName(u"layoutWidget1")
        self.layoutWidget1.setGeometry(QRect(16, 212, 170, 65))
        self.verticalLayout = QVBoxLayout(self.layoutWidget1)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.enableDCS = QCheckBox(self.layoutWidget1)
        self.enableDCS.setObjectName(u"enableDCS")

        self.verticalLayout.addWidget(self.enableDCS)

        self.enableMSFS = QCheckBox(self.layoutWidget1)
        self.enableMSFS.setObjectName(u"enableMSFS")

        self.verticalLayout.addWidget(self.enableMSFS)

        self.enableIL2 = QCheckBox(self.layoutWidget1)
        self.enableIL2.setObjectName(u"enableIL2")

        self.verticalLayout.addWidget(self.enableIL2)

        self.layoutWidget2 = QWidget(SystemDialog)
        self.layoutWidget2.setObjectName(u"layoutWidget2")
        self.layoutWidget2.setGeometry(QRect(30, 283, 452, 75))
        self.il2_sub_layout = QGridLayout(self.layoutWidget2)
        self.il2_sub_layout.setObjectName(u"il2_sub_layout")
        self.il2_sub_layout.setContentsMargins(0, 0, 0, 0)
        self.validateIL2 = QCheckBox(self.layoutWidget2)
        self.validateIL2.setObjectName(u"validateIL2")

        self.il2_sub_layout.addWidget(self.validateIL2, 0, 0, 1, 1)

        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.lab_pathIL2 = QLabel(self.layoutWidget2)
        self.lab_pathIL2.setObjectName(u"lab_pathIL2")
        sizePolicy1 = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.lab_pathIL2.sizePolicy().hasHeightForWidth())
        self.lab_pathIL2.setSizePolicy(sizePolicy1)
        self.lab_pathIL2.setMinimumSize(QSize(98, 0))

        self.horizontalLayout.addWidget(self.lab_pathIL2)

        self.pathIL2 = QLineEdit(self.layoutWidget2)
        self.pathIL2.setObjectName(u"pathIL2")

        self.horizontalLayout.addWidget(self.pathIL2)

        self.browseIL2 = QToolButton(self.layoutWidget2)
        self.browseIL2.setObjectName(u"browseIL2")

        self.horizontalLayout.addWidget(self.browseIL2)

        self.il2_sub_layout.addLayout(self.horizontalLayout, 1, 0, 1, 1)

        self.horizontalLayout_2 = QHBoxLayout()
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.lab_portIL2 = QLabel(self.layoutWidget2)
        self.lab_portIL2.setObjectName(u"lab_portIL2")

        self.horizontalLayout_2.addWidget(self.lab_portIL2)

        self.portIL2 = QLineEdit(self.layoutWidget2)
        self.portIL2.setObjectName(u"portIL2")

        self.horizontalLayout_2.addWidget(self.portIL2)

        self.horizontalSpacer = QSpacerItem(279, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.horizontalLayout_2.addItem(self.horizontalSpacer)

        self.il2_sub_layout.addLayout(self.horizontalLayout_2, 2, 0, 1, 1)

        self.line_2 = QFrame(SystemDialog)
        self.line_2.setObjectName(u"line_2")
        self.line_2.setGeometry(QRect(10, 369, 581, 16))
        self.line_2.setFont(font)
        self.line_2.setLineWidth(2)
        self.line_2.setMidLineWidth(1)
        self.line_2.setFrameShape(QFrame.HLine)
        self.line_2.setFrameShadow(QFrame.Sunken)
        self.label_8 = QLabel(SystemDialog)
        self.label_8.setObjectName(u"label_8")
        self.label_8.setGeometry(QRect(30, 402, 101, 16))
        self.layoutWidget3 = QWidget(SystemDialog)
        self.layoutWidget3.setObjectName(u"layoutWidget3")
        self.layoutWidget3.setGeometry(QRect(36, 422, 184, 42))
        self.verticalLayout_2 = QVBoxLayout(self.layoutWidget3)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.cb_save_geometry = QCheckBox(self.layoutWidget3)
        self.cb_save_geometry.setObjectName(u"cb_save_geometry")

        self.verticalLayout_2.addWidget(self.cb_save_geometry)

        self.cb_save_view = QCheckBox(self.layoutWidget3)
        self.cb_save_view.setObjectName(u"cb_save_view")

        self.verticalLayout_2.addWidget(self.cb_save_view)

        self.label_10 = QLabel(SystemDialog)
        self.label_10.setObjectName(u"label_10")
        self.label_10.setGeometry(QRect(315, 160, 151, 16))
        self.label_12 = QLabel(SystemDialog)
        self.label_12.setObjectName(u"label_12")
        self.label_12.setGeometry(QRect(386, 53, 51, 26))
        self.label_12.setAlignment(Qt.AlignLeading | Qt.AlignLeft | Qt.AlignTop)
        self.label_9 = QLabel(SystemDialog)
        self.label_9.setObjectName(u"label_9")
        self.label_9.setGeometry(QRect(313, 53, 42, 26))
        self.lab_auto_launch = QLabel(SystemDialog)
        self.lab_auto_launch.setObjectName(u"lab_auto_launch")
        self.lab_auto_launch.setEnabled(False)
        self.lab_auto_launch.setGeometry(QRect(450, 53, 38, 26))
        self.lab_auto_launch.setAlignment(Qt.AlignLeading | Qt.AlignLeft | Qt.AlignTop)
        self.lab_start_min = QLabel(SystemDialog)
        self.lab_start_min.setObjectName(u"lab_start_min")
        self.lab_start_min.setEnabled(False)
        self.lab_start_min.setGeometry(QRect(496, 53, 49, 26))
        self.lab_start_min.setAlignment(Qt.AlignLeading | Qt.AlignLeft | Qt.AlignTop)
        self.line_3 = QFrame(SystemDialog)
        self.line_3.setObjectName(u"line_3")
        self.line_3.setGeometry(QRect(283, 10, 20, 141))
        self.line_3.setFont(font)
        self.line_3.setLineWidth(2)
        self.line_3.setFrameShape(QFrame.VLine)
        self.line_3.setFrameShadow(QFrame.Sunken)
        self.layoutWidget4 = QWidget(SystemDialog)
        self.layoutWidget4.setObjectName(u"layoutWidget4")
        self.layoutWidget4.setGeometry(QRect(313, 82, 284, 74))
        self.gridLayout_2 = QGridLayout(self.layoutWidget4)
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        self.gridLayout_2.setContentsMargins(0, 0, 0, 0)
        self.cb_min_enable_j = QCheckBox(self.layoutWidget4)
        self.cb_min_enable_j.setObjectName(u"cb_min_enable_j")
        self.cb_min_enable_j.setEnabled(False)
        self.cb_min_enable_j.setMaximumSize(QSize(15, 16777215))
        self.cb_min_enable_j.setCheckable(True)

        self.gridLayout_2.addWidget(self.cb_min_enable_j, 0, 5, 1, 1)

        self.horizontalSpacer_2 = QSpacerItem(22, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_2, 0, 2, 1, 1)

        self.horizontalSpacer_5 = QSpacerItem(34, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_5, 2, 4, 1, 1)

        self.cb_al_enable_j = QCheckBox(self.layoutWidget4)
        self.cb_al_enable_j.setObjectName(u"cb_al_enable_j")
        self.cb_al_enable_j.setEnabled(False)
        self.cb_al_enable_j.setMaximumSize(QSize(15, 16777215))

        self.gridLayout_2.addWidget(self.cb_al_enable_j, 0, 3, 1, 1)

        self.cb_min_enable_p = QCheckBox(self.layoutWidget4)
        self.cb_min_enable_p.setObjectName(u"cb_min_enable_p")
        self.cb_min_enable_p.setEnabled(False)
        self.cb_min_enable_p.setMaximumSize(QSize(15, 16777215))
        self.cb_min_enable_p.setCheckable(True)

        self.gridLayout_2.addWidget(self.cb_min_enable_p, 1, 5, 1, 1)

        self.cb_headless_c = QCheckBox(self.layoutWidget4)
        self.cb_headless_c.setObjectName(u"cb_headless_c")
        self.cb_headless_c.setEnabled(False)
        self.cb_headless_c.setMaximumSize(QSize(15, 16777215))
        self.cb_headless_c.setCheckable(True)

        self.gridLayout_2.addWidget(self.cb_headless_c, 2, 7, 1, 1, Qt.AlignLeft)

        self.tb_pid_c = QLineEdit(self.layoutWidget4)
        self.tb_pid_c.setObjectName(u"tb_pid_c")
        self.tb_pid_c.setMaximumSize(QSize(50, 16777215))

        self.gridLayout_2.addWidget(self.tb_pid_c, 2, 1, 1, 1, Qt.AlignLeft)

        self.cb_al_enable_c = QCheckBox(self.layoutWidget4)
        self.cb_al_enable_c.setObjectName(u"cb_al_enable_c")
        self.cb_al_enable_c.setEnabled(False)
        self.cb_al_enable_c.setMaximumSize(QSize(15, 16777215))

        self.gridLayout_2.addWidget(self.cb_al_enable_c, 2, 3, 1, 1)

        self.rb_master_j = QRadioButton(self.layoutWidget4)
        self.rb_master_j.setObjectName(u"rb_master_j")
        self.rb_master_j.setChecked(True)

        self.gridLayout_2.addWidget(self.rb_master_j, 0, 0, 1, 1, Qt.AlignLeft)

        self.horizontalSpacer_7 = QSpacerItem(34, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_7, 0, 4, 1, 1)

        self.cb_headless_p = QCheckBox(self.layoutWidget4)
        self.cb_headless_p.setObjectName(u"cb_headless_p")
        self.cb_headless_p.setEnabled(False)
        self.cb_headless_p.setMaximumSize(QSize(15, 16777215))
        self.cb_headless_p.setCheckable(True)

        self.gridLayout_2.addWidget(self.cb_headless_p, 1, 7, 1, 1)

        self.horizontalSpacer_3 = QSpacerItem(22, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_3, 2, 2, 1, 1)

        self.rb_master_p = QRadioButton(self.layoutWidget4)
        self.rb_master_p.setObjectName(u"rb_master_p")

        self.gridLayout_2.addWidget(self.rb_master_p, 1, 0, 1, 1, Qt.AlignLeft)

        self.rb_master_c = QRadioButton(self.layoutWidget4)
        self.rb_master_c.setObjectName(u"rb_master_c")

        self.gridLayout_2.addWidget(self.rb_master_c, 2, 0, 1, 1, Qt.AlignLeft)

        self.tb_pid_p = QLineEdit(self.layoutWidget4)
        self.tb_pid_p.setObjectName(u"tb_pid_p")
        self.tb_pid_p.setMaximumSize(QSize(50, 16777215))

        self.gridLayout_2.addWidget(self.tb_pid_p, 1, 1, 1, 1, Qt.AlignLeft)

        self.cb_headless_j = QCheckBox(self.layoutWidget4)
        self.cb_headless_j.setObjectName(u"cb_headless_j")
        self.cb_headless_j.setEnabled(False)
        self.cb_headless_j.setMaximumSize(QSize(15, 16777215))
        self.cb_headless_j.setCheckable(True)

        self.gridLayout_2.addWidget(self.cb_headless_j, 0, 7, 1, 1)

        self.tb_pid_j = QLineEdit(self.layoutWidget4)
        self.tb_pid_j.setObjectName(u"tb_pid_j")
        self.tb_pid_j.setMaximumSize(QSize(50, 16777215))

        self.gridLayout_2.addWidget(self.tb_pid_j, 0, 1, 1, 1, Qt.AlignLeft)

        self.cb_min_enable_c = QCheckBox(self.layoutWidget4)
        self.cb_min_enable_c.setObjectName(u"cb_min_enable_c")
        self.cb_min_enable_c.setEnabled(False)
        self.cb_min_enable_c.setMaximumSize(QSize(15, 16777215))
        self.cb_min_enable_c.setCheckable(True)

        self.gridLayout_2.addWidget(self.cb_min_enable_c, 2, 5, 1, 1)

        self.horizontalSpacer_4 = QSpacerItem(22, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_4, 1, 2, 1, 1)

        self.horizontalSpacer_6 = QSpacerItem(34, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_6, 1, 4, 1, 1)

        self.cb_al_enable_p = QCheckBox(self.layoutWidget4)
        self.cb_al_enable_p.setObjectName(u"cb_al_enable_p")
        self.cb_al_enable_p.setEnabled(False)
        self.cb_al_enable_p.setMaximumSize(QSize(15, 16777215))

        self.gridLayout_2.addWidget(self.cb_al_enable_p, 1, 3, 1, 1)

        self.horizontalSpacer_9 = QSpacerItem(28, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_9, 0, 6, 1, 1)

        self.horizontalSpacer_10 = QSpacerItem(28, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_10, 1, 6, 1, 1)

        self.horizontalSpacer_11 = QSpacerItem(28, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_11, 2, 6, 1, 1)

        self.layoutWidget5 = QWidget(SystemDialog)
        self.layoutWidget5.setObjectName(u"layoutWidget5")
        self.layoutWidget5.setGeometry(QRect(310, 10, 269, 22))
        self.horizontalLayout_3 = QHBoxLayout(self.layoutWidget5)
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.horizontalLayout_3.setContentsMargins(0, 0, 0, 0)
        self.label_11 = QLabel(self.layoutWidget5)
        self.label_11.setObjectName(u"label_11")
        font1 = QFont()
        font1.setBold(True)
        font1.setUnderline(True)
        font1.setWeight(75)
        self.label_11.setFont(font1)

        self.horizontalLayout_3.addWidget(self.label_11)

        self.horizontalSpacer_8 = QSpacerItem(37, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)

        self.horizontalLayout_3.addItem(self.horizontalSpacer_8)

        self.cb_al_enable = QCheckBox(self.layoutWidget5)
        self.cb_al_enable.setObjectName(u"cb_al_enable")
        font2 = QFont()
        font2.setBold(False)
        font2.setUnderline(False)
        font2.setWeight(50)
        self.cb_al_enable.setFont(font2)

        self.horizontalLayout_3.addWidget(self.cb_al_enable)

        self.lab_start_headless = QLabel(SystemDialog)
        self.lab_start_headless.setObjectName(u"lab_start_headless")
        self.lab_start_headless.setEnabled(False)
        self.lab_start_headless.setGeometry(QRect(550, 53, 49, 26))
        self.lab_start_headless.setAlignment(Qt.AlignLeading | Qt.AlignLeft | Qt.AlignTop)
        self.label_3 = QLabel(SystemDialog)
        self.label_3.setObjectName(u"label_3")
        self.label_3.setGeometry(QRect(310, 30, 101, 16))
        self.widget = QWidget(SystemDialog)
        self.widget.setObjectName(u"widget")
        self.widget.setGeometry(QRect(20, 192, 144, 15))
        self.horizontalLayout_4 = QHBoxLayout(self.widget)
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.horizontalLayout_4.setContentsMargins(0, 0, 0, 0)
        self.label_6 = QLabel(self.widget)
        self.label_6.setObjectName(u"label_6")
        self.label_6.setFont(font1)

        self.horizontalLayout_4.addWidget(self.label_6)

        self.label_4 = QLabel(self.widget)
        self.label_4.setObjectName(u"label_4")

        self.horizontalLayout_4.addWidget(self.label_4)

        self.widget1 = QWidget(SystemDialog)
        self.widget1.setObjectName(u"widget1")
        self.widget1.setGeometry(QRect(20, 384, 167, 15))
        self.horizontalLayout_5 = QHBoxLayout(self.widget1)
        self.horizontalLayout_5.setObjectName(u"horizontalLayout_5")
        self.horizontalLayout_5.setContentsMargins(0, 0, 0, 0)
        self.label_7 = QLabel(self.widget1)
        self.label_7.setObjectName(u"label_7")
        self.label_7.setFont(font1)

        self.horizontalLayout_5.addWidget(self.label_7)

        self.label_13 = QLabel(self.widget1)
        self.label_13.setObjectName(u"label_13")

        self.horizontalLayout_5.addWidget(self.label_13)

        self.widget2 = QWidget(SystemDialog)
        self.widget2.setObjectName(u"widget2")
        self.widget2.setGeometry(QRect(20, 10, 125, 15))
        self.horizontalLayout_6 = QHBoxLayout(self.widget2)
        self.horizontalLayout_6.setObjectName(u"horizontalLayout_6")
        self.horizontalLayout_6.setContentsMargins(0, 0, 0, 0)
        self.label_5 = QLabel(self.widget2)
        self.label_5.setObjectName(u"label_5")
        self.label_5.setFont(font1)

        self.horizontalLayout_6.addWidget(self.label_5)

        self.label_14 = QLabel(self.widget2)
        self.label_14.setObjectName(u"label_14")

        self.horizontalLayout_6.addWidget(self.label_14)

        QWidget.setTabOrder(self.logLevel, self.telemTimeout)
        QWidget.setTabOrder(self.telemTimeout, self.ignoreUpdate)
        QWidget.setTabOrder(self.ignoreUpdate, self.cb_al_enable)
        QWidget.setTabOrder(self.cb_al_enable, self.rb_master_j)
        QWidget.setTabOrder(self.rb_master_j, self.rb_master_p)
        QWidget.setTabOrder(self.rb_master_p, self.rb_master_c)
        QWidget.setTabOrder(self.rb_master_c, self.tb_pid_j)
        QWidget.setTabOrder(self.tb_pid_j, self.tb_pid_p)
        QWidget.setTabOrder(self.tb_pid_p, self.tb_pid_c)
        QWidget.setTabOrder(self.tb_pid_c, self.cb_al_enable_j)
        QWidget.setTabOrder(self.cb_al_enable_j, self.cb_al_enable_p)
        QWidget.setTabOrder(self.cb_al_enable_p, self.cb_al_enable_c)
        QWidget.setTabOrder(self.cb_al_enable_c, self.cb_min_enable_j)
        QWidget.setTabOrder(self.cb_min_enable_j, self.cb_min_enable_p)
        QWidget.setTabOrder(self.cb_min_enable_p, self.cb_min_enable_c)
        QWidget.setTabOrder(self.cb_min_enable_c, self.cb_headless_j)
        QWidget.setTabOrder(self.cb_headless_j, self.cb_headless_p)
        QWidget.setTabOrder(self.cb_headless_p, self.cb_headless_c)
        QWidget.setTabOrder(self.cb_headless_c, self.enableDCS)
        QWidget.setTabOrder(self.enableDCS, self.enableMSFS)
        QWidget.setTabOrder(self.enableMSFS, self.enableIL2)
        QWidget.setTabOrder(self.enableIL2, self.validateIL2)
        QWidget.setTabOrder(self.validateIL2, self.pathIL2)
        QWidget.setTabOrder(self.pathIL2, self.browseIL2)
        QWidget.setTabOrder(self.browseIL2, self.portIL2)
        QWidget.setTabOrder(self.portIL2, self.cb_save_geometry)
        QWidget.setTabOrder(self.cb_save_geometry, self.cb_save_view)
        QWidget.setTabOrder(self.cb_save_view, self.resetButton)

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
        self.lab_pathIL2.setText(QCoreApplication.translate("SystemDialog", u"IL-2 Install Path:", None))
        self.browseIL2.setText(QCoreApplication.translate("SystemDialog", u"...", None))
        self.lab_portIL2.setText(QCoreApplication.translate("SystemDialog", u"IL-2 Telemetry Port:", None))
        self.label_8.setText(QCoreApplication.translate("SystemDialog", u"Startup  Behavior:", None))
        self.cb_save_geometry.setText(QCoreApplication.translate("SystemDialog", u"Restore window position", None))
        self.cb_save_view.setText(QCoreApplication.translate("SystemDialog", u"Restore last tab view", None))
        self.label_10.setText(QCoreApplication.translate("SystemDialog", u"Rhino default = 2055", None))
        self.label_12.setText(QCoreApplication.translate("SystemDialog", u"USB\n"
"Product ID", None))
        self.label_9.setText(QCoreApplication.translate("SystemDialog", u"Master\n"
"Instance", None))
        self.lab_auto_launch.setText(QCoreApplication.translate("SystemDialog", u"Auto\n"
"Launch:", None))
        self.lab_start_min.setText(QCoreApplication.translate("SystemDialog", u"Start\n"
"Minimized:", None))
        self.cb_min_enable_j.setText("")
        self.cb_al_enable_j.setText("")
        self.cb_min_enable_p.setText("")
        self.cb_headless_c.setText("")
        self.cb_al_enable_c.setText("")
        self.rb_master_j.setText(QCoreApplication.translate("SystemDialog", u"Joystick", None))
        self.cb_headless_p.setText("")
        self.rb_master_p.setText(QCoreApplication.translate("SystemDialog", u"Pedals", None))
        self.rb_master_c.setText(QCoreApplication.translate("SystemDialog", u"Colective", None))
        self.cb_headless_j.setText("")
        self.tb_pid_j.setText(QCoreApplication.translate("SystemDialog", u"2055", None))
        self.cb_min_enable_c.setText("")
        self.cb_al_enable_p.setText("")
        self.label_11.setText(QCoreApplication.translate("SystemDialog", u"Launch Options:", None))
        self.cb_al_enable.setText(QCoreApplication.translate("SystemDialog", u"Enable Auto-Launch", None))
        self.lab_start_headless.setText(QCoreApplication.translate("SystemDialog", u"Start\n"
"Headless:", None))
        self.label_3.setText(QCoreApplication.translate("SystemDialog", u"* System Global", None))
        self.label_6.setText(QCoreApplication.translate("SystemDialog", u"Sim Setup:", None))
        self.label_4.setText(QCoreApplication.translate("SystemDialog", u"* System Global", None))
        self.label_7.setText(QCoreApplication.translate("SystemDialog", u"Other Settings: ", None))
        self.label_13.setText(QCoreApplication.translate("SystemDialog", u"* Per-Instance", None))
        self.label_5.setText(QCoreApplication.translate("SystemDialog", u"System:", None))
        self.label_14.setText(QCoreApplication.translate("SystemDialog", u"* Per-Instance", None))
    # retranslateUi


class SystemSettingsDialog(QDialog, Ui_SystemDialog):
    def __init__(self, parent=None):
        super(SystemSettingsDialog, self).__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"System Settings ({_device_type.capitalize()})")

        # Add  "INFO" and "DEBUG" options to the logLevel combo box
        self.logLevel.addItems(["INFO", "DEBUG"])
        self.master_button_group = QButtonGroup()
        self.master_button_group.setObjectName(u"master_button_group")
        self.master_button_group.addButton(self.rb_master_j, id=1)
        self.master_button_group.addButton(self.rb_master_p, id=2)
        self.master_button_group.addButton(self.rb_master_c, id=3)

        # Connect signals to slots
        self.enableIL2.stateChanged.connect(self.toggle_il2_widgets)
        self.browseIL2.clicked.connect(self.select_il2_directory)
        self.buttonBox.accepted.connect(self.save_settings)
        self.resetButton.clicked.connect(self.reset_settings)
        self.master_button_group.buttonClicked.connect(lambda button: self.change_master_widgets(button))
        self.cb_al_enable.stateChanged.connect(self.toggle_al_widgets)
        self.buttonBox.rejected.connect(self.close)

        # Set initial state
        self.toggle_il2_widgets()
        self.toggle_al_widgets()
        self.parent_window = parent
        # Load settings from the registry and update widget states
        self.current_al_dict = {}
        self.load_settings()
        int_validator = QIntValidator()
        self.telemTimeout.setValidator(int_validator)
        self.tb_pid_j.setValidator(int_validator)
        self.tb_pid_p.setValidator(int_validator)
        self.tb_pid_c.setValidator(int_validator)

        self.cb_min_enable_j.setObjectName('minimize_j')
        self.cb_min_enable_j.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_p.setObjectName('minimize_p')
        self.cb_min_enable_p.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_c.setObjectName('minimize_c')
        self.cb_min_enable_c.clicked.connect(self.toggle_launchmode_cbs)

        self.cb_headless_j.setObjectName('headless_j')
        self.cb_headless_j.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_p.setObjectName('headless_p')
        self.cb_headless_p.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_c.setObjectName('headless_c')
        self.cb_headless_c.clicked.connect(self.toggle_launchmode_cbs)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)

    def reset_settings(self):
        # Load default settings and update widgets
        # default_settings = utils.get_default_sys_settings()
        self.load_settings(default=True)

    def change_master_widgets(self, button):
        if button == self.rb_master_j:
            self.cb_al_enable_j.setChecked(False)
            self.cb_al_enable_j.setVisible(False)
            self.cb_min_enable_j.setChecked(False)
            self.cb_min_enable_j.setVisible(False)
            self.cb_headless_j.setChecked(False)
            self.cb_headless_j.setVisible(False)
            self.cb_al_enable_c.setVisible(True)
            self.cb_min_enable_c.setVisible(True)
            self.cb_headless_c.setVisible(True)
            self.cb_al_enable_p.setVisible(True)
            self.cb_min_enable_p.setVisible(True)
            self.cb_headless_p.setVisible(True)
        elif button == self.rb_master_p:
            self.cb_al_enable_p.setChecked(False)
            self.cb_al_enable_p.setVisible(False)
            self.cb_min_enable_p.setChecked(False)
            self.cb_min_enable_p.setVisible(False)
            self.cb_headless_p.setChecked(False)
            self.cb_headless_p.setVisible(False)
            self.cb_al_enable_c.setVisible(True)
            self.cb_min_enable_c.setVisible(True)
            self.cb_headless_c.setVisible(True)
            self.cb_al_enable_j.setVisible(True)
            self.cb_min_enable_j.setVisible(True)
            self.cb_headless_j.setVisible(True)
        elif button == self.rb_master_c:
            self.cb_al_enable_c.setChecked(False)
            self.cb_al_enable_c.setVisible(False)
            self.cb_min_enable_c.setChecked(False)
            self.cb_min_enable_c.setVisible(False)
            self.cb_headless_c.setChecked(False)
            self.cb_headless_c.setVisible(False)
            self.cb_al_enable_j.setVisible(True)
            self.cb_min_enable_j.setVisible(True)
            self.cb_headless_j.setVisible(True)
            self.cb_al_enable_p.setVisible(True)
            self.cb_min_enable_p.setVisible(True)
            self.cb_headless_p.setVisible(True)

    def toggle_al_widgets(self):
        al_enabled = self.cb_al_enable.isChecked()
        self.lab_auto_launch.setEnabled(al_enabled)
        self.lab_start_min.setEnabled(al_enabled)
        self.lab_start_headless.setEnabled(al_enabled)
        self.cb_al_enable_j.setEnabled(al_enabled)
        self.cb_al_enable_p.setEnabled(al_enabled)
        self.cb_al_enable_c.setEnabled(al_enabled)
        self.cb_min_enable_j.setEnabled(al_enabled)
        self.cb_min_enable_p.setEnabled(al_enabled)
        self.cb_min_enable_c.setEnabled(al_enabled)
        self.cb_headless_j.setEnabled(al_enabled)
        self.cb_headless_p.setEnabled(al_enabled)
        self.cb_headless_c.setEnabled(al_enabled)

        if al_enabled:
            style = "QCheckBox::indicator:checked {image: url(:/image/purplecheckbox.png); }"
        else:
            style = "QCheckBox::indicator:checked {image: url(:/image/disabledcheckbox.png); }"

        self.cb_al_enable.setStyleSheet(style)
        self.cb_al_enable_j.setStyleSheet(style)
        self.cb_al_enable_p.setStyleSheet(style)
        self.cb_al_enable_c.setStyleSheet(style)
        self.cb_min_enable_j.setStyleSheet(style)
        self.cb_min_enable_p.setStyleSheet(style)
        self.cb_min_enable_c.setStyleSheet(style)
        self.cb_headless_j.setStyleSheet(style)
        self.cb_headless_p.setStyleSheet(style)
        self.cb_headless_c.setStyleSheet(style)

    def toggle_il2_widgets(self):
        # Show/hide IL-2 related widgets based on checkbox state
        il2_enabled = self.enableIL2.isChecked()
        # self.il2_sub_layout.setEnabled(il2_enabled)
        self.validateIL2.setEnabled(il2_enabled)
        self.lab_pathIL2.setEnabled(il2_enabled)
        self.pathIL2.setEnabled(il2_enabled)
        self.browseIL2.setEnabled(il2_enabled)
        self.lab_portIL2.setEnabled(il2_enabled)
        self.portIL2.setEnabled(il2_enabled)
        if il2_enabled:
            self.validateIL2.setStyleSheet("QCheckBox::indicator:checked {image: url(:/image/purplecheckbox.png); }")
        else:
            self.validateIL2.setStyleSheet("QCheckBox::indicator:checked {image: url(:/image/disabledcheckbox.png); }")

    def select_il2_directory(self):
        # Open a directory dialog and set the result in the pathIL2 QLineEdit
        directory = QFileDialog.getExistingDirectory(self, "Select IL-2 Install Path", "")
        if directory:
            self.pathIL2.setText(directory)

    def toggle_launchmode_cbs(self):
        sender = self.sender()
        if not sender.isChecked():
            return
        object_name = sender.objectName()
        match object_name:
            case 'headless_j':
                self.cb_min_enable_j.setChecked(False)
            case 'minimize_j':
                self.cb_headless_j.setChecked(False)
            case 'headless_p':
                self.cb_min_enable_p.setChecked(False)
            case 'minimize_p':
                self.cb_headless_p.setChecked(False)
            case 'headless_c':
                self.cb_min_enable_c.setChecked(False)
            case 'minimize_c':
                self.cb_headless_c.setChecked(False)
        logging.debug(f"{sender.objectName()} checked:{sender.isChecked()}")

    def validate_settings(self):
        master = self.master_button_group.checkedId()
        match master:
            case 1:
                val_entry = self.tb_pid_j.text()
            case 2:
                val_entry = self.tb_pid_p.text()
            case 3:
                val_entry = self.tb_pid_c.text()
        if self.cb_al_enable.isChecked() and not (self.cb_al_enable_j.isChecked() or self.cb_al_enable_p.isChecked() or self.cb_al_enable_c.isChecked()):
            QMessageBox.warning(self, "Config Error", "Auto Launching is enabled but no devices are configured for auto launch.  Please enable a device or disable auto launching")
            return False
        if val_entry == '':
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the selected Master Instance')
            return False
        if self.cb_al_enable_c.isChecked() and self.tb_pid_c.text() == '':
            r = self.tb_pid_c.text()
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the collective device or disable auto-launch')
            return False
        if self.cb_al_enable_j.isChecked() and self.tb_pid_j.text() == '':
            r = self.tb_pid_j.text()
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the joystick device or disable auto-launch')
            return False
        if self.cb_al_enable_p.isChecked() and self.tb_pid_p.text() == '':
            r = self.tb_pid_p.text()
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the pedals device or disable auto-launch')
            return False


        return True

    def save_settings(self):
        # Create a dictionary with the values of all components
        tp = args.type

        global_settings_dict = {
            "enableDCS": self.enableDCS.isChecked(),
            "enableMSFS": self.enableMSFS.isChecked(),
            "enableIL2": self.enableIL2.isChecked(),
            "validateIL2": self.validateIL2.isChecked(),
            "pathIL2": self.pathIL2.text(),
            "portIL2": str(self.portIL2.text()),
            'masterInstance': self.master_button_group.checkedId(),
            'autolaunchMaster': self.cb_al_enable.isChecked(),
            'autolaunchJoystick': self.cb_al_enable_j.isChecked(),
            'autolaunchPedals': self.cb_al_enable_p.isChecked(),
            'autolaunchCollective': self.cb_al_enable_c.isChecked(),
            'startMinJoystick': self.cb_min_enable_j.isChecked(),
            'startMinPedals': self.cb_min_enable_p.isChecked(),
            'startMinCollective': self.cb_min_enable_c.isChecked(),
            'startHeadlessJoystick': self.cb_headless_j.isChecked(),
            'startHeadlessPedals': self.cb_headless_p.isChecked(),
            'startHeadlessCollective': self.cb_headless_c.isChecked(),
            'pidJoystick': str(self.tb_pid_j.text()),
            'pidPedals': str(self.tb_pid_p.text()),
            'pidCollective': str(self.tb_pid_c.text()),
        }

        instance_settings_dict = {
            "logLevel": self.logLevel.currentText(),
            "telemTimeout": str(self.telemTimeout.text()),
            "ignoreUpdate": self.ignoreUpdate.isChecked(),
            "saveWindow": self.cb_save_geometry.isChecked(),
            "saveLastTab": self.cb_save_view.isChecked(),
        }

        key_list = [
            'autolaunchMaster',
            'autolaunchJoystick',
            'autolaunchPedals',
            'autolaunchCollective',
            'startMinJoystick',
            'startMinPedals',
            'startMinCollective',
            'startHeadlessJoystick',
            'startHeadlessPedals',
            'startHeadlessCollective',
            'pidJoystick',
            'pidPedals',
            'pidCollective',
        ]
        saved_al_dict = {}
        for key in key_list:
            saved_al_dict[key] = global_settings_dict[key]

        if self.current_al_dict != saved_al_dict:
            QMessageBox.information(self, "Restart Required", "The Auto-Launch or Master Device settings have changed.  Please restart TelemFFB.")

        # Save settings to the registry
        g_settings = json.dumps(global_settings_dict)
        i_settings = json.dumps(instance_settings_dict)
        utils.set_reg('Sys', g_settings)
        utils.set_reg(f'{tp}Sys', i_settings)

        if not self.validate_settings():
            return

        stop_sims()
        init_sims()

        if _master_instance and _launched_children:
            _ipc_thread.send_broadcast_message("RESTART SIMS")

        self.parent_window.init_sim_indicators(['DCS', 'MSFS', 'IL2'], global_settings_dict)
        # adjust logging level:
        ll = self.logLevel.currentText()
        if ll == "INFO":
            logger.setLevel(logging.INFO)
        elif ll == "DEBUG":
            logger.setLevel(logging.DEBUG)
        self.accept()

    def load_settings(self, default=False):
        """
        Load settings from the registry and update widget states.
        """
        if default:
            settings_dict = utils.get_default_sys_settings(args.device, args.type, cmb=True)
            self.cb_save_geometry.setChecked(True)
            self.cb_save_view.setChecked(True)
        else:
            # Read settings from the registry
            settings_dict = utils.read_system_settings(args.device, args.type)
            pass
        # Update widget states based on the loaded settings
        self.logLevel.setCurrentText(settings_dict.get('logLevel', 'INFO'))

        self.telemTimeout.setText(str(settings_dict.get('telemTimeout', 200)))

        self.ignoreUpdate.setChecked(settings_dict.get('ignoreUpdate', False))

        self.enableDCS.setChecked(settings_dict.get('enableDCS', False))

        self.enableMSFS.setChecked(settings_dict.get('enableMSFS', False))

        self.enableIL2.setChecked(settings_dict.get('enableIL2', False))
        self.toggle_il2_widgets()

        self.validateIL2.setChecked(settings_dict.get('validateIL2', True))

        self.pathIL2.setText(settings_dict.get('pathIL2', 'C:/Program Files/IL-2 Sturmovik Great Battles'))

        self.portIL2.setText(str(settings_dict.get('portIL2', 34385)))

        self.cb_save_geometry.setChecked(settings_dict.get('saveWindow', True))

        self.cb_save_view.setChecked(settings_dict.get('saveLastTab', True))

        self.tb_pid_j.setText(str(settings_dict.get('pidJoystick', '')))

        self.tb_pid_p.setText(str(settings_dict.get('pidPedals', '')))

        self.tb_pid_c.setText(str(settings_dict.get('pidCollective', '')))

        self.cb_al_enable.setChecked(settings_dict.get('autolaunchMaster', False))

        self.cb_al_enable_j.setChecked(settings_dict.get('autolaunchJoystick', False))
        self.cb_al_enable_p.setChecked(settings_dict.get('autolaunchPedals', False))
        self.cb_al_enable_c.setChecked(settings_dict.get('autolaunchCollective', False))

        self.cb_min_enable_j.setChecked(settings_dict.get('startMinJoystick', False))
        self.cb_min_enable_p.setChecked(settings_dict.get('startMinPedals', False))
        self.cb_min_enable_c.setChecked(settings_dict.get('startMinCollective', False))

        self.cb_headless_j.setChecked(settings_dict.get('startHeadlessJoystick', False))
        self.cb_headless_p.setChecked(settings_dict.get('startHeadlessPedals', False))
        self.cb_headless_c.setChecked(settings_dict.get('startHeadlessCollective', False))

        self.master_button_group.button(settings_dict.get('masterInstance', 1)).setChecked(True)
        self.master_button_group.button(settings_dict.get('masterInstance', 1)).click()
        self.toggle_al_widgets()

        # build record of auto-launch settings to see if they changed on save:
        self.current_al_dict = {
            'autolaunchMaster': self.cb_al_enable.isChecked(),
            'autolaunchJoystick': self.cb_al_enable_j.isChecked(),
            'autolaunchPedals': self.cb_al_enable_p.isChecked(),
            'autolaunchCollective': self.cb_al_enable_c.isChecked(),
            'startMinJoystick': self.cb_min_enable_j.isChecked(),
            'startMinPedals': self.cb_min_enable_p.isChecked(),
            'startMinCollective': self.cb_min_enable_c.isChecked(),
            'startHeadlessJoystick': self.cb_headless_j.isChecked(),
            'startHeadlessPedals': self.cb_headless_p.isChecked(),
            'startHeadlessCollective': self.cb_headless_c.isChecked(),
            'pidJoystick': str(self.tb_pid_j.text()),
            'pidPedals': str(self.tb_pid_p.text()),
            'pidCollective': str(self.tb_pid_c.text()),
        }


class ButtonPressThread(QThread):
    button_pressed = pyqtSignal(str, int)

    def __init__(self, device, button_obj, timeout=5):
        button_name = button_obj.objectName().replace('pb_', '')
        self.button_obj = button_obj
        super(ButtonPressThread, self).__init__()
        self.device = device
        self.button_name = button_name
        self.timeout = timeout
        self.prev_button_state = None

    def run(self):
        start_time = time.time()
        emit_sent = 0
        input_data = self.device.device.getInput()

        initial_buttons = input_data.getPressedButtons()

        while not emit_sent and time.time() - start_time < self.timeout:
            input_data = self.device.device.getInput()
            current_btns = set(input_data.getPressedButtons())
            countdown = int(self.timeout - (time.time() - start_time))
            self.button_obj.setText(f"Push a button! {countdown}..")
            # Check for new button press
            for btn in current_btns:
                if btn not in initial_buttons:
                    self.button_pressed.emit(self.button_name, btn)
                    emit_sent = 1

            self.prev_button_state = current_btns
            time.sleep(0.1)

        # Emit signal for timeout with value 0
        if not emit_sent:
            self.button_pressed.emit(self.button_name, 0)


class MainWindow(QMainWindow):
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
            dl_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=A'

        # notes_url = os.path.join(script_dir, '_RELEASE_NOTES.txt')
        notes_url = utils.get_resource_path('_RELEASE_NOTES.txt')
        self._current_config_scope = args.type
        if system_settings.get('saveLastTab', 0):
            if _device_type == 'joystick':
                tab_key = 'jTab'
            elif _device_type == 'pedals':
                tab_key = 'pTab'
            elif _device_type == 'collective':
                tab_key = 'cTab'

            tab = utils.get_reg(tab_key)
            self.current_tab_index = tab
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
        self.system_settings_dict = utils.read_system_settings(args.device, args.type)
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

        self.setGeometry(x_pos, y_pos, 530, 700)
        if version:
            self.setWindowTitle(f"TelemFFB ({args.type}) ({version})")
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

        if _master_instance and system_settings.get('autolaunchMaster', 0):
            self.window_menu = self.menu.addMenu('Window')
            self.show_children_action = QAction('Show Child Instance Windows')
            self.show_children_action.triggered.connect(lambda: self.toggle_child_windows('show'))
            self.window_menu.addAction(self.show_children_action)
            self.hide_children_action = QAction('Hide Child Instance Windows')
            self.hide_children_action.triggered.connect(lambda: self.toggle_child_windows('hide'))
            self.window_menu.addAction(self.hide_children_action)

        if _child_instance:
            self.window_menu = self.menu.addMenu('Window')
            self.hide_window_action = QAction('Hide Window')
            self.hide_window_action.triggered.connect(hide_window)
            self.window_menu.addAction(self.hide_window_action)

        log_menu = self.menu.addMenu('Log')
        self.log_window_action = QAction("Open Console Log", self)
        self.log_window_action.triggered.connect(self.toggle_log_window)
        log_menu.addAction(self.log_window_action)
        if _master_instance and system_settings.get('autolaunchMaster', 0):
            self.child_log_menu = log_menu.addMenu('Open Child Logs')
            if _launched_joystick:
                self.joystick_log_action = QAction('Joystick Log')
                self.joystick_log_action.triggered.connect(lambda: self.show_child_log('joystick'))
                self.child_log_menu.addAction(self.joystick_log_action)
            if _launched_pedals:
                self.pedals_log_action = QAction('Pedals Log')
                self.pedals_log_action.triggered.connect(lambda: self.show_child_log('pedals'))
                self.child_log_menu.addAction(self.pedals_log_action)
            if _launched_collective:
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
        self.support_action.triggered.connect(lambda: utils.create_support_bundle(userconfig_rootpath))
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

        dcs_enabled = utils.read_system_settings(args.device, args.type).get('enableDCS')
        il2_enabled = utils.read_system_settings(args.device, args.type).get('enableIL2')
        msfs_enabled = utils.read_system_settings(args.device, args.type).get('enableMSFS')

        self.icon_size = QSize(18, 18)
        if args.sim == "DCS" or dcs_enabled:
            dcs_color = QColor(255, 255, 0)
            dcs_icon = self.create_colored_icon(dcs_color, self.icon_size)
        else:
            dcs_color = QColor(128, 128, 128)
            dcs_icon = self.create_x_icon(dcs_color, self.icon_size)

        if args.sim == "MSFS" or msfs_enabled:
            msfs_color = QColor(255, 255, 0)
            msfs_icon = self.create_colored_icon(msfs_color, self.icon_size)
        else:
            msfs_color = QColor(128, 128, 128)
            msfs_icon = self.create_x_icon(msfs_color, self.icon_size)

        if args.sim == "IL2" or il2_enabled:
            il2_color = QColor(255, 255, 0)
            il2_icon = self.create_colored_icon(il2_color, self.icon_size)
        else:
            il2_color = QColor(128, 128, 128)
            il2_icon = self.create_x_icon(il2_color, self.icon_size)

        # xplane_color = QColor(128,128,128)
        # condor_color = QColor(128, 128, 128)

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
        if not args.child:
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
        status_layout.setAlignment(Qt.AlignRight)

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
        self.cur_craft.setStyleSheet("QLabel { padding-left: 15px; padding-top: 2px; }")
        self.cur_craft.setAlignment(Qt.AlignLeft)

        self.cur_pattern = QLabel()
        self.cur_pattern.setText('(No Match)')
        self.cur_pattern.setStyleSheet("QLabel { padding-left: 15px; padding-top: 2px; }")
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
        sims = ['', 'DCS', 'IL2', 'MSFS']
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
        self.tab_widget.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
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

        dcs_enabled = utils.read_system_settings(args.device, args.type).get('enableDCS')
        il2_enabled = utils.read_system_settings(args.device, args.type).get('enableIL2')
        msfs_enabled = utils.read_system_settings(args.device, args.type).get('enableMSFS')

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
        self.lbl_telem_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_telem_data.setStyleSheet("""padding: 2px""")

        # Set the QLabel widget as the widget inside the scroll area
        self.telem_area.setWidget(self.lbl_telem_data)

        self.lbl_effects_data = QLabel()
        self.effects_area.setWidget(self.lbl_effects_data)
        self.lbl_effects_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_effects_data.setStyleSheet("""padding: 2px""")

        self.telem_lbl = QLabel('Telemetry:')
        self.effect_lbl = QLabel('Active Effects:')
        if _master_instance:
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

        if _master_instance and _launched_children:
            self.instance_status_row = QHBoxLayout()
            self.master_status_icon = StatusLabel(None, f'This Instance({ _device_type.capitalize() }):', Qt.green, 8)
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

    def test_function(self):
        self.set_scrollbar(400)

    def refresh_telem_status(self, dcs, il2, msfs):
        dcs_status = "Enabled" if dcs else "Disabled"
        il2_status = "Enabled" if il2 else "Disabled"
        msfs_status = "Enabled" if msfs else "Disabled"

        self.lbl_telem_data.setText(
            f"Waiting for data...\n\n"
            f"DCS Enabled: {dcs_status}\n"
            f"IL2 Enabled: {il2_status}\n"
            f"MSFS Enabled: {msfs_status}\n\n"
            "Enable or Disable in System -> System Settings"
        )

    def add_debug_menu(self):
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
        self._ipc_thread.send_broadcast_message(f'SHOW LOG:{child}')

    def toggle_child_windows(self, toggle):
        if toggle == 'show':
            self._ipc_thread.send_broadcast_message("SHOW WINDOW")
            pass
        elif toggle == 'hide':
            self._ipc_thread.send_broadcast_message("HIDE WINDOW")
            pass

    def reset_user_config(self):
        global userconfig_path, userconfig_rootpath
        ans = QMessageBox.warning(self, "Caution", "Are you sure you want to proceed?  All contents of your user configuration will be erased\n\nA backup of the configuration will be generated containing the current timestamp.", QMessageBox.Ok | QMessageBox.Cancel)

        if ans == QMessageBox.Ok:
            try:
                # Get the current timestamp
                timestamp = datetime.now().strftime('%Y%m%d_%H%M')

                # Create the backup file name with the timestamp
                backup_file = os.path.join(userconfig_rootpath, ('userconfig_' + timestamp + '.bak'))

                # Copy the file to the backup file
                shutil.copy(userconfig_path, backup_file)

                logging.debug(f"Backup created: {backup_file}")

            except Exception as e:
                logging.error(f"Error creating backup: {str(e)}")
                QMessageBox.warning(self, 'Error', f'There was an error resetting the config:\n\n{e}')
                return

            os.remove(userconfig_path)
            utils.create_empty_userxml_file(userconfig_path)

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
        global _launched_pedals, _launched_collective, _launched_collective
        # print("External function executed on label click")
        # print(self._current_config_scope)
        if self._current_config_scope == 'joystick':
            if _launched_pedals or _device_type == 'pedals':
                self.change_config_scope(2)
            elif _launched_collective or _device_type == 'collective':
                self.change_config_scope(3)
        elif self._current_config_scope == 'pedals':
            if _launched_collective or _device_type == 'collective':
                self.change_config_scope(3)
            elif _launched_joystick or _device_type == 'joystick':
                self.change_config_scope(1)
        elif self._current_config_scope == 'collective':
            if _launched_joystick or _device_type == 'joystick':
                self.change_config_scope(1)
            elif _launched_pedals or _device_type == 'pedals':
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
        global log_folder, log_file
        current_log_ts = log_file.split('_')[-1]
        if isinstance(_arg, str):
            if 'joystick' in _arg: arg = 1
            elif 'pedals' in _arg: arg = 2
            elif 'collective' in _arg: arg = 3
        else:
            arg = _arg

        if arg == 1:
            xmlutils.update_vars('joystick', userconfig_path, defaults_path)
            self._current_config_scope = 'joystick'
            new_device_logo = ':/image/logo_j.png'
        elif arg == 2:
            xmlutils.update_vars('pedals', userconfig_path, defaults_path)
            self._current_config_scope = 'pedals'
            new_device_logo = ':/image/logo_p.png'
        elif arg == 3:
            xmlutils.update_vars('collective', userconfig_path, defaults_path)
            self._current_config_scope = 'collective'
            new_device_logo = ':/image/logo_c.png'

        pixmap = QPixmap(new_device_logo)
        self.devicetype_label.setPixmap(pixmap)
        self.devicetype_label.setFixedSize(pixmap.width(), pixmap.height())

        if _master_instance:
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
        if _child_instance:
            self.hide()
            event.ignore()
        else:
            exit_application()

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

        self.setGeometry(x_pos, y_pos, 530, 700)

    def load_main_window_geometry(self):
        device_type = args.type
        sys_settings = utils.read_system_settings(args.device, args.type)
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
        settings_mgr.current_sim = self.test_sim.currentText()
        settings_mgr.current_aircraft_name = self.test_name.currentText()
        self.settings_layout.expanded_items.clear()
        self.monitor_widget.hide()
        self.settings_layout.reload_caller()

    def open_teleplot_setup_dialog(self):
        dialog = TeleplotSetupDialog(self)
        dialog.exec_()

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
            os.startfile(userconfig_rootpath, 'open')

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
        modifiers = QApplication.keyboardModifiers()
        if ((modifiers & QtCore.Qt.ControlModifier) and (modifiers & QtCore.Qt.ShiftModifier)) or dbg:
            if self.test_craft_area.isVisible():
                self.test_craft_area.hide()
            else:
                self.test_craft_area.show()
        else:
            if settings_mgr.isVisible():
                settings_mgr.hide()
            else:
                settings_mgr.move(self.x() + 50, self.y() + 100)
                settings_mgr.show()
                logging.debug(f"# toggle settings window   sim:'{settings_mgr.current_sim}' ac:'{settings_mgr.current_aircraft_name}'")
                if settings_mgr.current_aircraft_name != '':
                    settings_mgr.currentmodel_click()
                else:
                    settings_mgr.update_table_on_class_change()

                if settings_mgr.current_sim == '' or settings_mgr.current_sim == 'nothing':
                    settings_mgr.update_table_on_sim_change()

    def show_user_model_dialog(self):
        mprint("show_user_model_dialog")
        current_aircraft = self.cur_craft.text()
        dialog = UserModelDialog(settings_mgr.current_sim, current_aircraft, settings_mgr.current_class, self)
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
                xmlutils.write_models_to_xml(settings_mgr.current_sim, new_aircraft, new_combo_box_value, 'type', None)
            else:
                logging.info(f"Cloning: {pat_to_clone} as {new_aircraft}")
                xmlutils.clone_pattern(settings_mgr.current_sim, pat_to_clone, new_aircraft)
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

    def update_sim_indicators(self, source, paused=False, error=False):
        active_color = QColor(0, 255, 0)
        paused_color = QColor(0, 0, 255)
        error_color = Qt.red
        active_icon = self.create_colored_icon(active_color, self.icon_size)
        paused_icon = self.create_paused_icon(paused_color, self.icon_size)
        error_icon = self.create_x_icon(error_color, self.icon_size)
        if error:
            match source:
                case 'DCS':
                    self.dcs_label_icon.setPixmap(error_icon)
                case 'IL2':
                    self.il2_label_icon.setPixmap(error_icon)
                case 'MSFS2020':
                    self.msfs_label_icon.setPixmap(error_icon)
        elif not paused:
            match source:
                case 'DCS':
                    self.dcs_label_icon.setPixmap(active_icon)
                case 'IL2':
                    self.il2_label_icon.setPixmap(active_icon)
                case 'MSFS2020':
                    self.msfs_label_icon.setPixmap(active_icon)
        elif paused:
            match source:
                case 'DCS':
                    self.dcs_label_icon.setPixmap(paused_icon)
                case 'IL2':
                    self.il2_label_icon.setPixmap(paused_icon)
                case 'MSFS2020':
                    self.msfs_label_icon.setPixmap(paused_icon)

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
                match args.type:
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
                if type(v) == float:
                    items += f"{k}: {v:.3f}\n"
                else:
                    if isinstance(v, list):
                        v = "[" + ", ".join([f"{x:.3f}" if not isinstance(x, str) else x for x in v]) + "]"
                    items += f"{k}: {v}\n"
            active_effects = ""
            active_settings = []

            if _master_instance and self._current_config_scope != _device_type:
                dev = self._current_config_scope
                active_effects = self._ipc_thread._ipc_telem_effects.get(f'{dev}_active_effects', '')
                active_settings = self._ipc_thread._ipc_telem_effects.get(f'{dev}_active_settings', [])
            else:
                for key in effects.dict.keys():
                    if effects[key].started:
                        descr = effects_translator.get_translation(key)[0]
                        settingname = effects_translator.get_translation(key)[1]
                        if descr not in active_effects:
                            active_effects = '\n'.join([active_effects, descr])
                        if settingname not in active_settings and settingname != '':
                            active_settings.append(settingname)

            if args.child:
                child_effects = str(effects.dict.keys())
                if len(child_effects):
                    self._ipc_thread.send_ipc_effects(active_effects, active_settings)

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

            shown_pattern = settings_mgr.current_pattern
            if settings_mgr.current_pattern == '' and data.get('N', '') != '':
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

        ignore_auto_updates = utils.read_system_settings(args.device, args.type).get('ignoreUpdate', False)
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
        if is_exe and _update_available and not ignore_auto_updates and not _child_instance:
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
                    QCoreApplication.instance().quit()
                else:
                    return True

        return False


class SettingsLayout(QGridLayout):
    expanded_items = []
    prereq_list = []
    ##########
    # debug settings
    show_slider_debug = False   # set to true for slider values shown
    show_order_debug = False    # set to true for order numbers shown
    bump_up = True              # set to false for no row bumping up

    all_sliders = []

    def __init__(self, parent=None, mainwindow=None):
        super(SettingsLayout, self).__init__(parent)
        result = None
        if settings_mgr.current_sim != 'nothing':
            a, b, result = xmlutils.read_single_model(settings_mgr.current_sim, settings_mgr.current_aircraft_name)

        self.mainwindow = mainwindow
        if result is not None:
            self.build_rows(result)
        self.device = HapticEffect()

    def handleScrollKeyPressEvent(self, event):
        # Forward key events to each slider in the layout
        for i in range(self.count()):
            item = self.itemAt(i)
            if isinstance(item.widget(), NoWheelSlider()):
                item.widget().handleKeyPressEvent(event)

    def append_prereq_count(self, datalist):
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

    def has_bump(self, datalist):
        for item in datalist:
            item['hasbump'] = ''
            bumped_up = item['order'][-2:] == '.1'
            if bumped_up:
                for b in datalist:
                    if item['prereq'] == b['name']:
                        b['hasbump'] = 'true'

    def add_expanded(self, datalist):
        for item in datalist:
            item['parent_expanded'] = ''
            for exp in self.expanded_items:
                if item['prereq'] == exp:
                    item['parent_expanded'] = 'true'
                else:
                    item['parent_expanded'] = 'false'

    def is_visible(self, datalist):

        for item in datalist:
            bumped_up = item['order'][-2:] == '.1'
            iv = 'false'
            cond = ''
            if item['prereq'] == '':
                iv = 'true'
                cond = 'no prereq needed'
            else:
                for p in datalist:
                    if item['prereq'] == p['name']:
                        if p['value'].lower() == 'true':
                            if p['has_expander'].lower() == 'true':
                                if p['name'] in self.expanded_items and p['is_visible'] == 'true':
                                    iv = 'true'
                                    cond = 'item parent expanded'
                                else:
                                    if p['hasbump'].lower() == 'true':
                                        if bumped_up:
                                            iv = 'true'
                                            cond = 'parent hasbump & bumped'
                            else:
                                if p['is_visible'].lower() == 'true':
                                    if p['hasbump'].lower() == 'true':
                                        if bumped_up:
                                            iv = 'true'
                                            cond = 'parent hasbump & bumped no expander par vis'
                        break

            item['is_visible'] = iv
            # for things not showing debugging:
            # if iv.lower() == 'true':
            #     print (f"{item['displayname']} visible because {cond}")

    def eliminate_invisible(self, datalist):
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
                    if row['has_expander'].lower() == 'true':
                        pcount -= 1
            item['prereq_count'] = str(pcount)

        return newlist

    def read_active_prereqs(self, datalist):
        p_list = []
        for item in datalist:
            found = False
            count = 0
            for p in datalist:
                if item['name'] == p['prereq']:
                    count += 1
                    found = True
                # If 'prereq' is not in the list, add a new entry
            if found:
                p_list.append({'prereq': item['name'], 'value': 'False', 'count': count})
        return p_list

    def build_rows(self, datalist):
        sorted_data = sorted(datalist, key=lambda x: float(x['order']))
        # self.prereq_list = xmlutils.read_prereqs()
        self.prereq_list = self.read_active_prereqs(sorted_data)
        self.has_bump(sorted_data)
        self.append_prereq_count(sorted_data)
        self.add_expanded(sorted_data)
        self.is_visible(sorted_data)
        newlist = self.eliminate_invisible(sorted_data)

        def is_expanded(item):
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
            # print(f"{item['order']} - {item['value']} - b {bumped_up} - hb {item['hasbump']} - ex {is_expnd} - hs {item['has_expander']} - pex {item['parent_expanded']} - iv {item['is_visible']} - pcount {item['prereq_count']} - {item['displayname']} - pr {item['prereq']}")
            if item['is_visible'].lower() == 'true':
                i += 1
                if bumped_up:
                    if self.bump_up:  # debug
                        i -= 1   # bump .1 setting onto the enable row
                self.generate_settings_row(item, i, rowdisabled)

        spacerItem = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.addItem(spacerItem, i+1, 1, 1, 1)
        # Give entry column a high stretch factor, all others remain default 0.
        # When window is resized, the entry column will grow to take up all the new space
        self.setColumnStretch(4, 10)

        # print (f"{i} rows with {self.count()} widgets")

    def reload_caller(self):
        # self.mainwindow.settings_area.setUpdatesEnabled(False)
        # pos = self.mainwindow.settings_area.verticalScrollBar().value()
        self.reload_layout(None)
        # QTimer.singleShot(150, lambda: self.mainwindow.set_scrollbar(pos))
        # self.mainwindow.settings_area.setUpdatesEnabled(True)

    def reload_layout(self, result=None):
        self.clear_layout()
        if result is None:
            cls, pat, result = xmlutils.read_single_model(settings_mgr.current_sim, settings_mgr.current_aircraft_name)
            settings_mgr.current_pattern = pat
        if result is not None:
            self.build_rows(result)

    def clear_layout(self):
        while self.count():
            item = self.takeAt(0)
            widget = item.widget()
            if widget:
                self.removeWidget(widget)
                widget.deleteLater()

    def generate_settings_row(self, item, i, rowdisabled=False):
        entry_colspan = 2
        lbl_colspan = 2

        exp_col = 0
        chk_col = 1
        lbl_col = 2
        entry_col = 4
        unit_col = 5
        val_col = 6
        erase_col = 7
        fct_col = 10
        ord_col = 11

        validvalues = item['validvalues'].split(',')

        if self.show_order_debug:
            order_lbl = QLabel()
            order_lbl.setText(item['order'])
            order_lbl.setMaximumWidth(30)
            self.addWidget(order_lbl, i, ord_col)

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
            if item['prereq'] != '':
                chk_col += 1
            self.addWidget(checkbox, i, chk_col)
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
            self.addWidget(unit_dropbox, i, unit_col)
            unit_dropbox.blockSignals(False)
            unit_dropbox.setDisabled(rowdisabled)

        # everything has a name, except for things that have a checkbox *and* slider
        label = InfoLabel(f"{item['displayname']}")
        label.setToolTip(item['info'])
        label.setMinimumHeight(20)
        label.setMinimumWidth(20)
        # label.setMaximumWidth(150)
        if item['order'][-2:] == '.1':
            olditem = self.itemAtPosition(i, lbl_col)
            if olditem is not None:
                self.remove_widget(olditem)
            # for p_item in self.prereq_list:
            #     if p_item['prereq'] == item['prereq'] and p_item['count'] == 1:
            #         olditem = self.itemAtPosition(i, self.exp_col)
            #         if olditem is not None:
            #             self.remove_widget(olditem)
        if item['prereq'] != '' and item['hasbump'] != 'true' and item['order'][-2:] != '.1':
            # label.setStyleSheet("QLabel { padding-left: 20px; }")
            lbl_colspan = 1
            lbl_col += 1
        self.addWidget(label, i, lbl_col, 1, lbl_colspan)

        slider = NoWheelSlider()
        slider.setOrientation(QtCore.Qt.Horizontal)
        slider.setObjectName(f"sld_{item['name']}")

        d_slider = NoWheelSlider()
        d_slider.setOrientation(QtCore.Qt.Horizontal)
        d_slider.setObjectName(f"dsld_{item['name']}")

        df_slider = NoWheelSlider()
        df_slider.setOrientation(QtCore.Qt.Horizontal)
        df_slider.setObjectName(f"dfsld_{item['name']}")

        line_edit = QLineEdit()
        line_edit.blockSignals(True)
        # unit?
        line_edit.setText(item['value'])
        line_edit.blockSignals(False)
        line_edit.setAlignment(Qt.AlignHCenter)
        line_edit.setObjectName(f"vle_{item['name']}")
        line_edit.setMinimumWidth(150)
        line_edit.editingFinished.connect(self.line_edit_changed)

        expand_button = QToolButton()
        if item['name'] in self.expanded_items:
            expand_button.setArrowType(Qt.DownArrow)
        else:
            expand_button.setArrowType(Qt.RightArrow)
        expand_button.setMaximumWidth(24)
        expand_button.setMinimumWidth(24)
        expand_button.setObjectName(f"ex_{item['name']}")
        expand_button.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
        expand_button.clicked.connect(self.expander_clicked)

        usb_button_text = f"Button {item['value']}"
        if item['value'] == '0':
            usb_button_text = 'Click to Configure'
        self.usbdevice_button = QPushButton(usb_button_text)
        self.usbdevice_button.setMinimumWidth(150)
        self.usbdevice_button.setObjectName(f"pb_{item['name']}")
        self.usbdevice_button.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
        self.usbdevice_button.clicked.connect(self.usb_button_clicked)

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
                logging.debug(f"read value: {item['value']}  factor: {item['sliderfactor']} slider: {pctval}")
            slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                slider.setRange(int(validvalues[0]), int(validvalues[1]))
            slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')
            slider.valueChanged.connect(self.slider_changed)
            slider.sliderPressed.connect(self.sldDisconnect)
            slider.sliderReleased.connect(self.sldReconnect)
            self.addWidget(slider, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)

            slider.blockSignals(False)

        if item['datatype'] == 'd_float':

            d_val = float(item['value'])
            factor = float(item['sliderfactor'])
            val = float(round(d_val / factor))
            if self.show_slider_debug:
                print(f"read value: {item['value']}   df_slider: {val}")
            df_slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                df_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            df_slider.setValue(round(val))
            value_label.setText(str(d_val))
            df_slider.valueChanged.connect(self.df_slider_changed)
            df_slider.sliderPressed.connect(self.sldDisconnect)
            df_slider.sliderReleased.connect(self.df_sldReconnect)
            self.addWidget(df_slider, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)
            df_slider.blockSignals(False)

        if item['datatype'] == 'cfgfloat':

            # print(f"label {value_label.objectName()} for slider {slider.objectName()}")

            if '%' in item['value']:
                pctval = int(item['value'].replace('%', ''))
            else:
                pctval = int(float(item['value']) * 100)
            pctval = int(round(pctval))
            # print (f"configurator value: {item['value']}   slider: {pctval}")
            slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                slider.setRange(int(validvalues[0]), int(validvalues[1]))
            slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')
            slider.valueChanged.connect(self.cfg_slider_changed)
            slider.sliderPressed.connect(self.sldDisconnect)
            slider.sliderReleased.connect(self.cfg_sldReconnect)
            self.addWidget(slider, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)

            slider.blockSignals(False)

        if item['datatype'] == 'd_int':

            d_val = int(item['value'])
            factor = float(item['sliderfactor'])
            val = int(round(d_val / factor))
            if self.show_slider_debug:
                print(f"read value: {item['value']}   d_slider: {val}")
            d_slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                d_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            d_slider.setValue(val)
            value_label.setText(str(d_val))
            d_slider.valueChanged.connect(self.d_slider_changed)
            d_slider.sliderPressed.connect(self.sldDisconnect)
            d_slider.sliderReleased.connect(self.d_sldReconnect)
            self.addWidget(d_slider, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)

            d_slider.blockSignals(False)

        if item['datatype'] == 'list' or item['datatype'] == 'anylist':
            dropbox = QComboBox()
            dropbox.setMinimumWidth(150)
            dropbox.setEditable(True)
            dropbox.lineEdit().setAlignment(QtCore.Qt.AlignHCenter)
            dropbox.setObjectName(f"db_{item['name']}")
            dropbox.addItems(validvalues)
            dropbox.blockSignals(True)
            dropbox.setCurrentText(item['value'])
            if item['datatype'] == 'list':
                dropbox.lineEdit().setReadOnly(True)
                dropbox.editTextChanged.connect(self.dropbox_changed)
            else:
                dropbox.currentTextChanged.connect(self.dropbox_changed)
            dropbox.blockSignals(False)
            self.addWidget(dropbox, i, entry_col, 1, entry_colspan)
            # dropbox.currentTextChanged.connect(self.dropbox_changed)

        if item['datatype'] == 'path':
            browse_button = QPushButton()
            browse_button.blockSignals(True)
            browse_button.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
            browse_button.setMinimumWidth(150)
            if item['value'] == '-':
                browse_button.setText('Browse...')
            else:
                fname = os.path.basename(item['value'])
                button_text = fname
                browse_button.setToolTip(item['value'])
                # p_length = len(item['value'])
                # if p_length > 45:
                #     button_text = f"{button_text[:40]}...{button_text[-25:]}"
                browse_button.setText(button_text)
            browse_button.blockSignals(False)
            browse_button.setMaximumHeight(25)
            browse_button.clicked.connect(self.browse_for_config)
            self.addWidget(browse_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignLeft)

        if item['datatype'] == 'int' or item['datatype'] == 'anyfloat':
            self.addWidget(line_edit, i, entry_col, 1, entry_colspan)

        if item['datatype'] == 'button':
            self.addWidget(self.usbdevice_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignLeft)

        if item['has_expander'] == 'true' and item['prereq'] != '':
            exp_col += 1
        if not rowdisabled:
            # for p_item in self.prereq_list:
            #     if p_item['prereq'] == item['name'] : # and p_item['count'] > 1:
            p_count = 0
            if item['prereq_count'] != '':
                p_count = int(item['prereq_count'])

            if item['has_expander'].lower() == 'true':
                if item['name'] in self.expanded_items:
                    row_count = p_count
                    if item['hasbump'].lower() != 'true':
                        row_count += 1
                    expand_button.setMaximumHeight(200)
                    # self.addWidget(expand_button, i, exp_col, row_count, 1)
                    self.addWidget(expand_button, i, exp_col)
                else:
                    self.addWidget(expand_button, i, exp_col)

        label.setDisabled(rowdisabled)
        slider.setDisabled(rowdisabled)
        d_slider.setDisabled(rowdisabled)
        df_slider.setDisabled(rowdisabled)
        line_edit.setDisabled(rowdisabled)
        expand_button.setDisabled(rowdisabled)

        self.parent().parent().parent().addSlider(slider)
        self.parent().parent().parent().addSlider(d_slider)
        self.parent().parent().parent().addSlider(df_slider)

        erase_button = QToolButton()
        erase_button.setObjectName(f"eb_{item['name']}")
        pixmapi = QStyle.SP_DockWidgetCloseButton
        icon = erase_button.style().standardIcon(pixmapi)
        erase_button.setIcon(icon)

        erase_button.clicked.connect(lambda _, name=item['name']: self.erase_setting(name))

        if item['replaced'] == 'Model (user)':
            if item['name'] != 'type':  # dont erase type on mainwindow settings
                self.addWidget(erase_button, i, erase_col)
                # print(f"erase {item['name']} button set up")

        self.setRowStretch(i, 0)

    def remove_widget(self, olditem):
        widget = olditem.widget()
        if widget is not None:
            widget.deleteLater()
            self.removeWidget(widget)

    def checkbox_changed(self, name, state):
        logging.debug(f"Checkbox {name} changed. New state: {state}")
        value = 'false' if state == 0 else 'true'
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value, name)
        if settings_mgr.timedOut:
            self.reload_caller()

    def erase_setting(self, name):
        logging.debug(f"Erase {name} clicked")
        xmlutils.erase_models_from_xml(settings_mgr.current_sim, settings_mgr.current_pattern, name)
        if settings_mgr.timedOut:
            self.reload_caller()

    def browse_for_config(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog

        # Open the file browser dialog
        file_path, _ = QFileDialog.getOpenFileName(self.mainwindow, "Choose File", "", "vpconf Files (*.vpconf);;All Files (*)", options=options)

        if file_path:
            lprint(f"Selected File: {file_path}")
            xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, file_path, 'vpconf')
            if settings_mgr.timedOut:
                self.reload_caller()

    def dropbox_changed(self):
        setting_name = self.sender().objectName().replace('db_', '')
        value = self.sender().currentText()
        logging.debug(f"Dropbox {setting_name} changed. New value: {value}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value, setting_name)
        if settings_mgr.timedOut:
            self.reload_caller()

    def unit_dropbox_changed(self):
        setting_name = self.sender().objectName().replace('ud_', '')
        line_edit_name = 'vle_' + self.sender().objectName().replace('ud_', '')
        line_edit = self.mainwindow.findChild(QLineEdit, line_edit_name)
        value = ''
        unit = self.sender().currentText()
        if line_edit is not None:
            value = line_edit.text()
        logging.debug(f"Unit {self.sender().objectName()} changed. New value: {value}{unit}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value, setting_name, unit)
        if settings_mgr.timedOut:
            self.reload_caller()

    def line_edit_changed(self):
        setting_name = self.sender().objectName().replace('vle_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('vle_', '')
        unit_dropbox = self.mainwindow.findChild(QComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value = self.sender().text()
        logging.debug(f"Text box {self.sender().objectName()} changed. New value: {value}{unit}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value, setting_name, unit)
        if settings_mgr.timedOut:
            self.reload_caller()

    def expander_clicked(self):
        logging.debug(f"expander {self.sender().objectName()} clicked.  value: {self.sender().text()}")
        settingname = self.sender().objectName().replace('ex_', '')
        if self.sender().arrowType() == Qt.RightArrow:
            # print ('expanded')

            self.expanded_items.append(settingname)
            self.sender().setArrowType(Qt.DownArrow)

            self.reload_caller()
        else:
            # print ('collapsed')
            new_exp_items = []
            for ex in self.expanded_items:
                if ex != settingname:
                    new_exp_items.append(ex)
            self.expanded_items = new_exp_items
            self.sender().setArrowType(Qt.DownArrow)

            self.reload_caller()

    def usb_button_clicked(self):
        button_name = self.sender().objectName().replace('pb_', '')
        the_button = self.mainwindow.findChild(QPushButton, f'pb_{button_name}')
        the_button.setText("Push a button! ")
        # listen for button loop
        # Start a thread to fetch button press with a timeout
        self.thread = ButtonPressThread(self.device, self.sender())
        self.thread.button_pressed.connect(self.update_button)
        self.thread.start()

    def update_button(self, button_name, value):
        the_button = self.mainwindow.findChild(QPushButton, f'pb_{button_name}')
        the_button.setText(str(value))
        if str(value) != '0':
            xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, str(value), button_name)
        if settings_mgr.timedOut:
            self.reload_caller()

    def slider_changed(self):
        setting_name = self.sender().objectName().replace('sld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('sld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('sld_', '')
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
            logging.debug(f"Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value_to_save, setting_name)
        if settings_mgr.timedOut:
            self.reload_caller()

    def cfg_slider_changed(self):
        setting_name = self.sender().objectName().replace('sld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('sld_', '')

        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        value = 0
        factor = 1.0
        if value_label is not None:
            value_label.setText(str(self.sender().value()) + '%')
            value = int(self.sender().value())

        value_to_save = str(round(value / 100, 4))
        if self.show_slider_debug:
            logging.debug(f"Slider {self.sender().objectName()} cfg changed. New value: {value}  saving: {value_to_save}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value_to_save, setting_name)
        if settings_mgr.timedOut:
            self.reload_caller()

    def d_slider_changed(self):
        setting_name = self.sender().objectName().replace('dsld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('dsld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('dsld_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('dsld_', '')
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
            logging.debug(f"d_Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}{unit}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value_to_save, setting_name, unit)
        if settings_mgr.timedOut:
            self.reload_caller()

    def df_slider_changed(self):
        setting_name = self.sender().objectName().replace('dfsld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('dfsld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('dfsld_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('dfsld_', '')
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
            value_to_save = str(round(value * factor, 2))
            value_label.setText(value_to_save)
        if self.show_slider_debug:
            logging.debug(f"df_Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}{unit}")
        xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, value_to_save, setting_name, unit)
        if settings_mgr.timedOut:
            self.reload_caller()

    # prevent slider from sending values as you drag
    def sldDisconnect(self):
        self.sender().valueChanged.disconnect()

    # reconnect slider after you let go
    def sldReconnect(self):
        self.sender().valueChanged.connect(self.slider_changed)
        self.sender().valueChanged.emit(self.sender().value())

    def cfg_sldReconnect(self):
        self.sender().valueChanged.connect(self.cfg_slider_changed)
        self.sender().valueChanged.emit(self.sender().value())

    def d_sldReconnect(self):
        self.sender().valueChanged.connect(self.d_slider_changed)
        self.sender().valueChanged.emit(self.sender().value())

    def df_sldReconnect(self):
        self.sender().valueChanged.connect(self.df_slider_changed)
        self.sender().valueChanged.emit(self.sender().value())


class ClickableLabel(QLabel):
    def __init__(self, parent=None):
        super(ClickableLabel, self).__init__(parent)

    def mousePressEvent(self, event):
        os.startfile(userconfig_rootpath, 'open')
        logging.debug("userpath opened")


class NoKeyScrollArea(QScrollArea):
    def __init__(self):
        super().__init__()

        self.sliders = []
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)

    def addSlider(self, slider):
        self.sliders.append(slider)

    def keyPressEvent(self, event):
        # Forward keypress events to all sliders
        for slider in self.sliders:
            try:
                slider.keyPressEvent(event)
            except:
                pass

    def keyReleaseEvent(self, event):
        # Forward keypress events to all sliders
        for slider in self.sliders:
            try:
                slider.keyReleaseEvent(event)
            except:
                pass


class NoWheelSlider(QSlider):
    def __init__(self, *args, **kwargs):
        super(NoWheelSlider, self).__init__(*args, **kwargs)
        # Default colors
        self.groove_color = "#bbb"
        self.handle_color = vpf_purple
        self.handle_height = 20
        self.handle_width = 16
        self.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
        # Apply styles
        self.update_styles()

        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.is_mouse_over = False

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ShiftModifier:
            # Adjust the value by increments of 1
            current_value = self.value()
            if event.angleDelta().y() > 0:
                new_value = current_value + 1
            elif event.angleDelta().y() < 0:
                new_value = current_value - 1

            # Ensure the new value is within the valid range
            new_value = max(self.minimum(), min(self.maximum(), new_value))

            self.setValue(new_value)
            event.accept()
        else:
            event.ignore()

    def update_styles(self):
        # Generate CSS based on color and size properties
        css = f"""
            QSlider::groove:horizontal {{
                border: 1px solid #565a5e;
                height: 8px;  /* Adjusted groove height */
                background: {self.groove_color};
                margin: 0;
                border-radius: 3px;  /* Adjusted border radius */
            }}
            QSlider::handle:horizontal {{
                background: {self.handle_color};
                border: 1px solid #565a5e;
                width: {self.handle_width}px;  /* Adjusted handle width */
                height: {self.handle_height}px;  /* Adjusted handle height */
                border-radius: {self.handle_height / 4}px;  /* Adjusted border radius */
                margin-top: -{self.handle_height / 4}px;  /* Negative margin to overlap with groove */
                margin-bottom: -{self.handle_height / 4}px;  /* Negative margin to overlap with groove */
                margin-left: -1px;  /* Adjusted left margin */
                margin-right: -1px;  /* Adjusted right margin */
            }}
        """
        self.setStyleSheet(css)

    def setGrooveColor(self, color):
        self.groove_color = color
        self.update_styles()

    def setHandleColor(self, color):
        self.handle_color = color
        self.update_styles()

    def setHandleHeight(self, height):
        self.handle_height = height
        self.update_styles()

    def enterEvent(self, event):
        self.is_mouse_over = True

    def leaveEvent(self, event):
        self.is_mouse_over = False

    def keyPressEvent(self, event):

        if self.is_mouse_over:
            # self.blockSignals(True)
            if event.key() == Qt.Key_Left:
                self.setValue(self.value() - 1)
            elif event.key() == Qt.Key_Right:
                self.setValue(self.value() + 1)
            else:
                super().keyPressEvent(event)
        # else:
        #     super().keyPressEvent(event)

    def keyReleaseEvent(self, event):

        if self.is_mouse_over:
            self.blockSignals(False)
            # if event.key() == Qt.Key_Left:
            #     self.valueChanged.emit(self.value() - 1)
            # elif event.key() == Qt.Key_Right:
            #     self.valueChanged.emit(self.value() + 1)

            pass
        else:
            super().keyReleaseEvent(event)


class ClickLogo(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent=None):

        super(ClickLogo, self).__init__(parent)

        # Initial clickable state
        self._clickable = False

    def setClickable(self, clickable):
        self._clickable = clickable
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if self._clickable:
            self.clicked.emit()

    def enterEvent(self, event):
        if self._clickable:
            self.setCursor(Qt.PointingHandCursor)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)


class InfoLabel(QWidget):
    def __init__(self, text=None, tooltip=None, parent=None):
        super(InfoLabel, self).__init__(parent)

        # Text label
        self.text_label = QLabel(self)
        self.text_label.setText(text)
        self.text_label.setMinimumWidth(self.text_label.sizeHint().height())

        # Information icon
        self.icon_label = QLabel(self)
        # icon_img = os.path.join(script_dir, "image/information.png")
        icon_img = ":/image/information.png"
        self.pixmap = QPixmap(icon_img)
        self.icon_label.setPixmap(self.pixmap.scaledToHeight(self.text_label.sizeHint().height()))  # Adjust the height as needed
        self.icon_label.setVisible(False)

        # Layout to align the text label and icon
        self.layout = QHBoxLayout(self)
        self.layout.addWidget(self.text_label, alignment=Qt.AlignLeft)
        self.layout.addSpacing(0)
        self.layout.addWidget(self.icon_label, alignment=Qt.AlignLeft)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addStretch()

        # Set initial size for text_label based on the size of the icon
        # self.text_label.setFixedHeight(self.icon_label.height())

        if text:
            self.setText(text)
        if tooltip:
            self.setToolTip(tooltip)

    def setText(self, text):
        self.text_label.setText(text)
        # Adjust the size of text_label based on the new text
        # self.text_label.setFixedHeight(self.icon_label.height())

    def setToolTip(self, tooltip):
        if tooltip:
            self.icon_label.setToolTip(tooltip)
            self.icon_label.setVisible(True)
        else:
            self.icon_label.setToolTip('')
            self.icon_label.setVisible(False)

    def setTextStyleSheet(self, style_sheet):
        self.text_label.setStyleSheet(style_sheet)

    def show_icon(self):
        # Manually scale the pixmap to a reasonable size
        scaled_pixmap = self.pixmap.scaledToHeight(self.text_label.sizeHint().height())  # Adjust the height as needed
        self.icon_label.setPixmap(scaled_pixmap)


class StatusLabel(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, parent=None, text='', color: QColor = Qt.yellow, size=8):
        super(StatusLabel, self).__init__(parent)

        self.label = QLabel(text)
        self.label.setStyleSheet("QLabel { padding-right: 5px; }")

        self.dot_color = color  # Default color
        self.dot_size = size
        self.setCursor(Qt.PointingHandCursor)
        self._clickable = True
        self.setToolTip('Click to manage this device')
        layout = QHBoxLayout(self)
        layout.addWidget(self.label)

    def enterEvent(self, event):
        # Set the label to be blue and underlined when the mouse enters
        self.label.setStyleSheet("QLabel { padding-right: 5px; color: blue; text-decoration: underline; }")

    def leaveEvent(self, event):
        # Set the label back to its original style when the mouse leaves
        self.label.setStyleSheet("QLabel { padding-right: 5px; color: black; text-decoration: none; }")

    def mousePressEvent(self, event):
        if self._clickable:
            dev = self.label.text().lower()
            self.clicked.emit(dev)

    def hide(self):
        self.label.hide()
        super().hide()

    def show(self):
        self.label.show()
        super().show()

    def set_text(self, text):
        self.label.setText(text)

    def set_dot_color(self, color: QColor):
        self.dot_color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate adjusted positioning for the dot
        dot_x = self.label.geometry().right() - 1  # 5 is an arbitrary offset for better alignment
        dot_y = self.label.geometry().center().y() - self.dot_size // 2 + 1

        painter.setBrush(QColor(self.dot_color))
        painter.drawEllipse(dot_x, dot_y, self.dot_size, self.dot_size)


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


def hide_window():
    global window
    try:
        window.hide()
    except Exception as e:
        logging.error(f"EXCEPTION: {e}")


def exit_application():
    global window
    # Perform any cleanup or save operations here
    save_main_window_geometry()
    QCoreApplication.instance().quit()


def save_main_window_geometry():
    global window
    # Capture the mai n window's geometry
    device_type = _device_type
    cur_index = window.tab_widget.currentIndex()
    window.tab_sizes[str(cur_index)]['width'] = window.width()
    window.tab_sizes[str(cur_index)]['height'] = window.height()

    window_dict = {
        'WindowPosition': {
            'x': window.pos().x(),
            'y': window.pos().y(),
        },
        'Tab': window.tab_widget.currentIndex(),
        'TabSizes': window.tab_sizes
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
    global _ipc_running, _ipc_thread, _child_ipc_ports, _master_instance
    if _ipc_running:
        if _master_instance:
            _ipc_thread.send_broadcast_message("TEST MESSAGE TO ALL")
        else:
            _ipc_thread.send_message("TEST MESSAGE")


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


def config_to_dict(section, name, value, isim='', device=args.type, new_ac=False):
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

    def_dev_dict, def_sys_dict = utils.get_default_sys_settings(args.device, args.type)

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
    utils.set_reg(f'{args.type}Sys', formatted_dev_dict)
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
                    dev = args.type
                dif_item = config_to_dict(section, key, valuestring, isim=sim, device=dev, new_ac=True)
                differences.append(dif_item)

    xmlutils.write_converted_to_xml(differences)
    return True

def autoconvert_config(main_window, cfg=_legacy_config_file, usr=_legacy_override_file):
    if _child_instance: return
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
    global window, _device_pid, _device_type
    sim_list = ['DCS', 'MSFS', 'IL2']
    sys_settings = utils.read_system_settings(args.device, _device_type)
    stop_sims()
    init_sims()
    window.init_sim_indicators(sim_list, sys_settings)


def init_sims():
    global dcs_telem, il2_telem, sim_connect_telem, telem_manager
    dcs_telem = NetworkThread(telem_manager, host="", port=34380)
    # dcs_enabled = utils.sanitize_dict(config["system"]).get("dcs_enabled", None)
    dcs_enabled = utils.read_system_settings(args.device, args.type).get('enableDCS', False)
    if dcs_enabled or args.sim == "DCS":
        # check and install/update export lua script
        if not _child_instance:
            utils.install_export_lua(window)
        logging.info("Starting DCS Telemetry Listener")
        dcs_telem.start()

    il2_mgr = IL2Manager()
    # il2_port = utils.sanitize_dict(config["system"]).get("il2_telem_port", 34385)
    il2_port = int(utils.read_system_settings(args.device, args.type).get('portIL2', 34385))
    # il2_path = utils.sanitize_dict(config["system"]).get("il2_path", 'C: \\Program Files\\IL-2 Sturmovik Great Battles')
    il2_path = utils.read_system_settings(args.device, args.type).get('pathIL2', 'C: \\Program Files\\IL-2 Sturmovik Great Battles')
    # il2_validate = utils.sanitize_dict(config["system"]).get("il2_cfg_validation", True)
    il2_validate = utils.read_system_settings(args.device, args.type).get('validateIL2', True)
    il2_telem = NetworkThread(telem_manager, host="", port=il2_port, telem_parser=il2_mgr)

    # il2_enabled = utils.sanitize_dict(config["system"]).get("il2_enabled", None)
    il2_enabled = utils.read_system_settings(args.device, args.type).get('enableIL2', False)

    if il2_enabled or args.sim == "IL2":
        if not _child_instance:
            if il2_validate:
                utils.analyze_il2_config(il2_path, port=il2_port, window=window)
            else:
                logging.warning(
                    "IL2 Config validation is disabled - please ensure the IL2 startup.cfg is configured correctly")
        logging.info("Starting IL2 Telemetry Listener")
        il2_telem.start()

    sim_connect_telem = SimConnectSock(telem_manager)
    msfs = utils.read_system_settings(args.device, args.type).get('enableMSFS', False)

    try:
        # msfs = utils.sanitize_dict(config["system"]).get("msfs_enabled", None)
        logging.debug(f"MSFS={msfs}")
        if msfs or args.sim == "MSFS":
            logging.info("MSFS Enabled:  Starting Simconnect Manager")
            sim_connect_telem.start()
            aircrafts_msfs.Aircraft.set_simconnect(sim_connect_telem)

    except:
        logging.exception("Error loading MSFS enable flag from config file")

    dcs_status = "Enabled" if dcs_enabled else "Disabled"
    il2_status = "Enabled" if il2_enabled else "Disabled"
    msfs_status = "Enabled" if msfs else "Disabled"

    window.refresh_telem_status(dcs_enabled, il2_enabled, msfs)

def stop_sims():
    global dcs_telem, il2_telem, sim_connect_telem
    dcs_telem.quit()
    il2_telem.quit()
    sim_connect_telem.quit()


def notify_close_children():
    global _child_ipc_ports, _ipc_running, _ipc_thread
    if not len(_child_ipc_ports) or not _ipc_running:
        return
    _ipc_thread.send_broadcast_message("MASTER INSTANCE QUIT")


def launch_children():
    global _launched_joystick, _launched_pedals, _launched_collective, _child_ipc_ports, script_dir, _device_pid, _master_instance
    if not system_settings.get('autolaunchMaster', False) or args.child or not _master_instance:
        return False

    if getattr(sys, 'frozen', False):
        app = ['VPForce-TelemFFB.exe']
    else:
        app = ['python', 'main.py']

    master_port = f"6{_device_pid}"
    # full_path = os.path.join(script_dir, app)
    try:
        if system_settings.get('autolaunchJoystick', False) and _device_type != 'joystick':
            min = ['--minimize'] if system_settings.get('startMinJoystick', False) else []
            headless = ['--headless'] if system_settings.get('startHeadlessJoystick', False) else []
            pid = system_settings.get('pidJoystick', '2055')
            vidpid = f"FFFF:{pid}"
            command = app + ['-D', vidpid, '-t', 'joystick', '--child', '--masterport', master_port] + min + headless
            logging.info(f"Auto-Launch: starting instance: {command}")
            subprocess.Popen(command)
            _launched_joystick = True
            _child_ipc_ports.append(int(f"6{pid}"))
        if system_settings.get('autolaunchPedals', False) and _device_type != 'pedals':
            min = ['--minimize'] if system_settings.get('startMinPedals', False) else []
            headless = ['--headless'] if system_settings.get('startHeadlessPedals', False) else []
            pid = system_settings.get('pidPedals', '2055')
            vidpid = f"FFFF:{pid}"
            command = app + ['-D', vidpid, '-t', 'pedals', '--child', '--masterport', master_port] + min + headless
            logging.info(f"Auto-Launch: starting instance: {command}")
            subprocess.Popen(command)
            _launched_pedals = True
            _child_ipc_ports.append(int(f"6{pid}"))
        if system_settings.get('autolaunchCollective', False) and _device_type != 'collective':
            min = ['--minimize'] if system_settings.get('startMinCollective', False) else []
            headless = ['--headless'] if system_settings.get('startHeadlessCollective', False) else []
            pid = system_settings.get('pidCollective', '2055')
            vidpid = f"FFFF:{pid}"
            command = app + ['-D', vidpid, '-t', 'collective', '--child', '--masterport', master_port] + min + headless
            logging.info(f"Auto-Launch: starting instance: {command}")
            subprocess.Popen(command)
            _launched_collective = True
            _child_ipc_ports.append(int(f"6{pid}"))
    except Exception as e:
        logging.error(f"Error during Auto-Launch sequence: {e}")
    return True


def main():
    app = QApplication(sys.argv)
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
        """
    )
    global window, log_window, log_tail_window
    global dev_firmware_version
    global dev_serial
    log_window = LogWindow()
    global settings_mgr, telem_manager, config_was_default
    xmlutils.update_vars(args.type, userconfig_path, defaults_path)
    settings_mgr = SettingsWindow(datasource="Global", device=args.type, userconfig_path=userconfig_path, defaults_path=defaults_path)
    icon_path = ":/image/vpforceicon.png"
    settings_mgr.setWindowIcon(QIcon(icon_path))
    sys.stdout = utils.OutLog(log_window.widget, sys.stdout)
    sys.stderr = utils.OutLog(log_window.widget, sys.stderr)

    log_window.pause_button.clicked.connect(sys.stdout.toggle_pause)

    logging.getLogger().handlers[0].setStream(sys.stdout)
    logging.info(f"TelemFFB (version {version}) Starting")
    try:
        vid_pid = [int(x, 16) for x in _device_vid_pid.split(":")]
    except:
        pass

    try:
        dev = HapticEffect.open(vid_pid[0], vid_pid[1])  # try to open RHINO
        if args.reset:
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
        QMessageBox.warning(None, "Cannot connect to Rhino", f"Unable to open HID at {_device_vid_pid} for device: {_device_type}\nError: {e}\n\nPlease open the System Settings and verify the Master\ndevice PID is configured correctly")
        dev_firmware_version = 'ERROR'

    # config = get_config()
    # ll = config["system"].get("logging_level", "INFO")
    ll = utils.read_system_settings(args.device, args.type).get('logLevel', 'INFO')
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    logger.setLevel(log_levels.get(ll, logging.DEBUG))
    logging.info(f"Logging level set to:{logging.getLevelName(logger.getEffectiveLevel())}")

    global _ipc_running, _ipc_thread, is_master, _child_ipc_ports, _launched_children
    _ipc_running = False
    is_master = launch_children()
    if is_master:
        myport = int(f"6{_device_pid}")
        _ipc_thread = IPCNetworkThread(master=True, myport=myport, child_ports=_child_ipc_ports)
        _ipc_thread.child_keepalive_signal.connect(lambda device, status: window.update_child_status(device, status))
        _ipc_thread.start()
        _ipc_running = True
        _launched_children = True
    elif args.child:
        myport = int(f"6{_device_pid}")
        _ipc_thread = IPCNetworkThread(child=True, myport=myport, dstport=args.masterport)
        _ipc_thread.exit_signal.connect(lambda: exit_application())
        _ipc_thread.restart_sim_signal.connect(lambda: restart_sims())
        _ipc_thread.show_signal.connect(lambda: window.show())
        _ipc_thread.hide_signal.connect(lambda: window.hide())
        _ipc_thread.showlog_signal.connect(lambda: log_window.show())
        _ipc_thread.start()
        _ipc_running = True

    window = MainWindow(ipc_thread=_ipc_thread)

    # log_tail_window = LogTailWindow(window)

    if not headless_mode:
        if args.minimize:
            window.showMinimized()
        else:
            window.show()

    autoconvert_config(window)
    if not _release:
        fetch_version_thread = utils.FetchLatestVersionThread()
        fetch_version_thread.version_result_signal.connect(window.update_version_result)
        fetch_version_thread.error_signal.connect(lambda error_message: print("Error in thread:", error_message))
        fetch_version_thread.start()

    telem_manager = TelemManager(settings_manager=settings_mgr, ipc_thread=_ipc_thread)
    telem_manager.start()

    telem_manager.telemetryReceived.connect(window.update_telemetry)
    telem_manager.updateSettingsLayout.connect(window.update_settings)

    init_sims()

    if is_master:
        window.show_device_logo()
        window.enable_device_logo_click(True)
        current_title = window.windowTitle()
        new_title = f"** MASTER INSTANCE ** {current_title}"
        window.setWindowTitle(new_title)
        if _launched_joystick:
            window.joystick_status_icon.show()
        if _launched_pedals:
            window.pedals_status_icon.show()
        if _launched_collective:
            window.collective_status_icon.show()

    if config_was_default:
        window.open_system_settings_dialog()

    utils.signal_emitter.telem_timeout_signal.connect(window.update_sim_indicators)
    utils.signal_emitter.error_signal.connect(window.process_error_signal)
    # if getattr(sys, 'frozen', False):
    #     path = os.path.dirname(sys.executable)
    # else:
    #     path = os.path.dirname(os.path.abspath(__file__))
    #
    # frozen = getattr(sys, 'frozen', False)
    # if frozen:
    #     # we are running in a bundle
    #     bundle_dir = sys._MEIPASS
    # else:
    #     # we are running in a normal Python environment
    #     bundle_dir = os.path.dirname(os.path.abspath(__file__))
    # log_info = f'''
    #     Frozen is {frozen}
    #     bundle dir is {bundle_dir}
    #     sys.argv[0] is {sys.argv[0]}
    #     sys.executable is {sys.executable}
    #     os.getcwd is {os.getcwd()}
    #     '''
    # logging.info(log_info)

    app.exec_()
    if _ipc_running:
        notify_close_children()
        _ipc_thread.stop()
    stop_sims()
    telem_manager.quit()


if __name__ == "__main__":
    main()
