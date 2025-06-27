#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
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

import sys
# import faulthandler
# faulthandler.enable()
from PyQt6.QtGui import QIcon, QColor


from telemffb.CmdLineArgs import CmdLineArgs

if sys.argv[0].lower().endswith("updater.exe"):
    import updater
    updater.main()
    sys.exit()

import argparse
import logging
import os
import re
import shutil
import subprocess
import traceback
from datetime import datetime

from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit


import resources
import telemffb.globals as G
import telemffb.utils as utils
import telemffb.xmlutils as xmlutils
# from telemffb.config_utils import autoconvert_config
from telemffb.hw.ffb_rhino import DeviceInfo, FFBRhino, HapticEffect
from telemffb.IPCNetworkThread import IPCNetworkThread
from telemffb.LogWindow import LogWindow
from telemffb.MainWindow import MainWindow
from telemffb.settingsmanager import SettingsWindow
from telemffb.telem.SimTelemListener import SimListenerManager
from telemffb.ConfiguratorDialog import ConfiguratorDialog
#from telemffb.LogTailWindow import LogTailWindow
from telemffb.telem.TelemManager import TelemManager
from telemffb.utils import (AnsiColors, LoggingFilter, exit_application,
                            set_vpconf_profile)
from telemffb.namedmutex import NamedMutex
import styles
resources # used

def send_test_message():
    if G.ipc_instance.running:
        if G.master_instance:
            G.ipc_instance.send_broadcast_message("TEST MESSAGE TO ALL")
        else:
            G.ipc_instance.send_message("TEST MESSAGE")

def _launch_children():
    if not G.system_settings.get('autolaunchMaster'):
        return
    if not G.master_instance:
        return
    
    master_port = G.ipc_instance.local_port
    try:
        utils.check_launch_instance("joystick", master_port)
        utils.check_launch_instance("pedals", master_port)
        utils.check_launch_instance("collective", master_port)
        utils.check_launch_instance("trimwheel", master_port)

    except Exception:
        logging.exception("Error during Auto-Launch sequence")


def _check_master_instance_mutex():
    """Check if another master instance is already running."""
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Icon.Warning)
    msg_box.setWindowTitle("TelemFFB is already running")
    msg_box.setText(
        "TelemFFB is already running and cannot be started.  If you don't see the 'VP' icon in the system tray, "
        "check the task manager for possible hung instances."
    )
    msg_box.setWindowIcon(QIcon(':/image/vpforceicon.png'))
    
    try:
        mutex = NamedMutex("VPforce_TelemFFB_Master_Instance", acquired=True, timeout=1)
        if not mutex.acquired:
            msg_box.exec()
            sys.exit(1)
    except WindowsError:
        msg_box.exec()
        sys.exit(1)

def _setup_device_configuration():
    """Configure device type and USB VID/PID based on args or system settings."""
    if G.args.device is None:
        mapping = {1: "joystick", 2: "pedals", 3: "collective", 4: "trimwheel"}
        master_rb = G.system_settings.get('masterInstance', 1)
        
        try:
            d = mapping[master_rb]
            G.device_usbpid = G.system_settings.get(f'pid{d.capitalize()}', "2055")
            G.device_type = d
        except KeyError:
            G.device_usbpid = 2055
            G.device_type = 'joystick'

        if not G.device_usbpid: # check empty string
            G.device_usbpid = '2055'

        G.device_usbvidpid = f"FFFF:{G.device_usbpid}"
        G.args.type = G.device_type
    else:
        if G.args.type is None:
            G.device_type = 'joystick'
            G.args.type = G.device_type
        else:
            G.device_type = str.lower(G.args.type)

        G.device_usbpid = G.args.device.split(":")[1]
        G.device_usbvidpid = G.args.device

def _setup_theme_and_styling(app):
    """Configure application theme and styling based on system settings."""
    theme_setting = G.system_settings.get('themeId', 2)

    match theme_setting:
        case 0: # Light Mode
            app.styleHints().setColorScheme(Qt.ColorScheme.Light)
            G.useDarkMode = False
        case 1: # Dark Mode
            app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
            G.useDarkMode = True
        case 2: # System Controlled
            windows_mode = app.styleHints().colorScheme()
            if windows_mode == Qt.ColorScheme.Light:
                app.styleHints().setColorScheme(Qt.ColorScheme.Light)
                G.useDarkMode = False
            else:
                app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
                G.useDarkMode = True

    # Create and set custom palette with accent color
    palette = app.palette()
    accent_color = QtGui.QColor('#9430ad')
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent_color)
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor('white'))
    palette.setColor(QtGui.QPalette.ColorRole.Link, accent_color)
    app.setPalette(palette)

    if G.useDarkMode:
        _apply_dark_mode_palette(app, palette)
    
    _apply_custom_stylesheet(app)

def _apply_dark_mode_palette(app, palette):
    """Apply dark mode color palette."""
    # Base colors with updated ColorRole enums
    palette.setColor(QtGui.QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#dddddd"))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QColor(35, 35, 35))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor('#dddddd'))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor('#dddddd'))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor('#dddddd'))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor('#dddddd'))
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor('red'))

    # Link colors
    palette.setColor(QtGui.QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor('black'))

    # Disabled colors
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, QColor(127, 127, 127))
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.ButtonText, QColor(127, 127, 127))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QColor(43, 43, 43))  # or #2b2b2b
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor('#dddddd'))

    app.setPalette(palette)

def _apply_custom_stylesheet(app):
    """Apply custom stylesheet based on theme mode."""
    if G.useDarkMode:
        app.setStyleSheet(styles.DARK_MODE_STYLESHEET)
    else:
        app.setStyleSheet(styles.LIGHT_MODE_STYLESHEET)

def _determine_master_instance_status():
    """Determine if this instance should be the master based on device type."""
    index_dict = {
        'joystick': 1,
        'pedals': 2,
        'collective': 3,
        'trimwheel': 4
    }
    master_index = G.system_settings.get('masterInstance', 1)
    if index_dict[G.device_type] == master_index:
        G.master_instance = True
    else:
        G.master_instance = False

def _setup_config_paths():
    """Setup configuration file paths based on build type and mode."""
    G.defaults_path = utils.get_resource_path('defaults.xml', prefer_root=True)
    
    if G.dev_build:
        G.vpf_logo = ":/image/DEVlogo.png"
        if G.dev_userconfig:
            _setup_dev_userconfig_paths()
        else:
            _setup_standard_config_paths()
    else:
        if G.useDarkMode:
            G.vpf_logo = ":/image/vpforcelogo_dm.png"
        else:
            G.vpf_logo = ":/image/vpforcelogo.png"
        _setup_standard_config_paths()

def _setup_dev_userconfig_paths():
    """Setup development userconfig paths."""
    real_userconfig_path = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB")
    real_userconfig = os.path.join(real_userconfig_path, 'userconfig.xml')
    
    if getattr(sys, 'frozen', False):
        G.userconfig_rootpath = os.path.dirname(sys.executable)
    else:
        G.userconfig_rootpath = os.path.dirname(os.path.abspath(__file__))
    
    G.userconfig_path = os.path.join(G.userconfig_rootpath, 'userconfig.xml')
    if not os.path.isfile(G.userconfig_path):
        shutil.copy(real_userconfig, G.userconfig_path)

def _setup_standard_config_paths():
    """Setup standard configuration paths."""
    G.userconfig_rootpath = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB")
    G.userconfig_path = os.path.join(G.userconfig_rootpath, 'userconfig.xml')

def _initialize_device_connection():
    """Initialize connection to the Rhino device and check firmware."""
    min_firmware_version = 'v1.0.17'
    dev_serial = None
    dev_firmware_version = 'ERROR'
    dev = None
    
    try:
        vid_pid = [int(x, 16) for x in G.device_usbvidpid.split(":")]
    except Exception:
        return dev, dev_serial, dev_firmware_version
    
    _enumerate_and_log_devices()
    
    try:
        dev = HapticEffect.open(vid_pid[0], vid_pid[1])
        if G.args.reset:
            dev.reset_effects()
        dev_firmware_version = dev.get_firmware_version()
        dev_serial = dev.serial
        
        if dev_firmware_version:
            logging.info(f"Rhino Firmware: {dev_firmware_version}")
            _check_firmware_version(dev_firmware_version, min_firmware_version)
        
        G.device_ident = dev.info.product_string.replace("Rhino FFB ", "").strip()
        
    except Exception as e:
        logging.exception("Exception")
        QMessageBox.warning(None, "Cannot connect to Rhino", 
                          f"Unable to open HID at {G.device_usbvidpid} for device: {G.device_type}\nError: {e}\n\n"
                          "Please open the System Settings and verify the Master\ndevice PID is configured correctly")
    
    return dev, dev_serial, dev_firmware_version

def _enumerate_and_log_devices():
    """Enumerate and log available Rhino devices."""
    devs = FFBRhino.enumerate()
    logging.info("Available Rhino Devices:")
    logging.info("-------")
    for devinfo in devs:
        devinfo : DeviceInfo
        logging.info(f"* {devinfo.vendor_id:04X}:{devinfo.product_id:04X} - {devinfo.product_string} - {devinfo.serial_number}")
        logging.info(f"* Path:{devinfo.path}")
        logging.info(f"*")
        if G.master_instance:
            pid = int(f"{devinfo.product_id:04X}")
            G.instance_dev_dict[pid] = {}
            G.instance_dev_dict[pid]["ident"] = devinfo.product_string.replace("Rhino FFB ", "").strip()
            G.instance_dev_dict[pid]["serial"] = devinfo.serial_number
    logging.info("-------")

def _check_firmware_version(dev_firmware_version, min_firmware_version):
    """Check if device firmware version meets minimum requirements."""
    minver = re.sub(r'\D', '', min_firmware_version)
    devver = re.sub(r'\D', '', dev_firmware_version)
    if devver < minver:
        QMessageBox.warning(None, "Outdated Firmware", 
                          f"This version of TelemFFB requires Rhino Firmware version {min_firmware_version} or later.\n\n"
                          f"The current version installed is {dev_firmware_version}\n\n\n Please update to avoid errors!")

def _setup_logging_level():
    """Configure logging level based on system settings."""
    ll = G.system_settings.get('logLevel', 'INFO')
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    logger = logging.getLogger()
    logger.setLevel(log_levels.get(ll, logging.DEBUG))
    logging.info(f"Logging level set to:{logging.getLevelName(logger.getEffectiveLevel())}")

def _initialize_settings_manager():
    """Initialize the settings manager with error handling for corrupted config."""
    xmlutils.update_vars(G.device_type, G.userconfig_path, G.defaults_path)
    try:
        G.settings_mgr = SettingsWindow(datasource="Global", device=G.device_type, 
                                      userconfig_path=G.userconfig_path, 
                                      defaults_path=G.defaults_path, 
                                      system_settings=G.system_settings)
    except Exception:
        logging.exception("Error Reading user config file..")
        _handle_corrupted_config()

def _handle_corrupted_config():
    """Handle corrupted configuration file with user interaction."""
    ans = QMessageBox.question(None, "User Config Error", 
                             "There was an error reading the userconfig.  The file is likely corrupted.\n\n"
                             "Do you want to back-up the existing config and create a new default (empty) config?\n\n"
                             "If you chose No, TelemFFB will exit.")
    if ans == QMessageBox.StandardButton.Yes:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        backup_file = os.path.join(G.userconfig_rootpath, 
                                 f'userconfig_{os.environ["USERNAME"]}_{timestamp}_corrupted.bak')
        
        shutil.copy(G.userconfig_path, backup_file)
        logging.debug(f"Backup created: {backup_file}")
        
        os.remove(G.userconfig_path)
        utils.create_empty_userxml_file(G.userconfig_path)
        
        logging.info(f"User config Reset:  Backup file created: {backup_file}")
        G.settings_mgr = SettingsWindow(datasource="Global", device=G.device_type, 
                                      userconfig_path=G.userconfig_path, 
                                      defaults_path=G.defaults_path, 
                                      system_settings=G.system_settings)
        QMessageBox.information(None, "New Userconfig created", f"A backup has been created: {backup_file}\n")
    else:
        QCoreApplication.instance().quit()
        return

def _setup_ipc_and_connections():
    """Setup IPC network thread and connect all signals."""
    G.ipc_instance = IPCNetworkThread(dstport=G.args.masterport)
    G.ipc_instance.child_keepalive_signal.connect(G.main_window.update_child_status)
    G.ipc_instance.exit_signal.connect(exit_application)
    G.ipc_instance.restart_sim_signal.connect(G.sim_listeners.restart_all)
    G.ipc_instance.show_signal.connect(G.main_window.show)
    G.ipc_instance.hide_signal.connect(G.main_window.hide)
    G.ipc_instance.showlog_signal.connect(G.log_window.show)
    G.ipc_instance.show_settings_signal.connect(G.main_window.open_system_settings_dialog)
    G.ipc_instance.show_adv_spr_signal.connect(G.main_window.settings_layout.advanced_spring_button_clicked)
    G.ipc_instance.show_cfg_ovds_signal.connect(G.main_window.settings_layout.configurator_button_clicked)
    G.ipc_instance.erase_cfg_ovds_signal.connect(G.main_window.settings_layout.erase_configurator_overrides)
    G.ipc_instance.reload_aircraft_signal.connect(G.main_window.force_reload_aircraft)
    G.ipc_instance.start()

def _setup_device_button_connections():
    """Setup device button event connections."""
    try:
        HapticEffect.device.buttonPressed.connect(G.main_window.get_active_buttons)
        HapticEffect.device.buttonReleased.connect(G.main_window.get_active_buttons)
    except:
        pass

def _handle_window_display(headless_mode):
    """Handle initial window display based on configuration."""
    if not headless_mode:
        if G.args.minimize or G.system_settings.get('masterStartMin', False):
            G.main_window.showMinimized()
        elif G.master_instance and G.system_settings.get('startToTray', False):
            # Don't show window, tray message will pop during 'setup_master_instance'->'add_system_tray'
            pass
        else:
            G.main_window.show()

def _check_version_update():
    """Check for version updates if not release or dev build."""
    if not G.release_version and not G.dev_build:
        utils.FetchLatestVersion(G.main_window.update_version_result,
                                lambda error_message: logging.error("Error in thread: %s", error_message))

def _check_system_settings_required():
    """Check if system settings dialog should be opened."""
    if (not G.system_settings.get("pidJoystick", None) and
        not G.system_settings.get("pidPedals", None) and
        not G.system_settings.get("pidCollective", None) and
        not G.system_settings.get("pidTrimWheel", None)):
        G.main_window.open_system_settings_dialog()

def _setup_async_initialization(dev, dev_serial):
    """Setup background initialization that doesn't block main window appearance."""
    @utils.threaded()
    def init_async():
        try:
            G.startup_configurator_gains = dev.get_gains()
        except Exception:
            logging.exception("Unable to get configurator slider values from device")

        if G.system_settings.get('enableVPConfStartup', False):
            try:
                set_vpconf_profile(G.system_settings.get('pathVPConfStartup', ''), dev_serial)
            except Exception:
                logging.exception("Unable to set VPConfigurator startup profile")

        try:
            G.vpconf_configurator_gains = dev.get_gains()
        except Exception:
            logging.exception("Unable to get configurator slider values from device")

    init_async()

def _cleanup_on_exit(dev_serial):
    """Handle cleanup operations when application exits."""
    if G.ipc_instance:
        G.ipc_instance.notify_close_children()
        G.ipc_instance.stop()

    G.sim_listeners.stop_all()
    G.telem_manager.quit()
    
    if G.system_settings.get('enableVPConfExit', False):
        try:
            set_vpconf_profile(G.system_settings.get('pathVPConfExit', ''), dev_serial)
        except Exception:
            logging.error("Unable to set VPConfigurator exit profile")
    
    if G.system_settings.get('enableResetGainsExit', False):
        try:
            G.gain_override_dialog.set_gains_from_object(G.startup_configurator_gains)
        except:
            pass

def main():
    #QApplication.setAttribute(QtCore.Qt.ApplicationAttribute. AA_EnableHighDpiScaling, True) #enable highdpi scaling
    #QApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)  #use highdpi icons

    dev : FFBRhino = None

    app = QApplication(sys.argv)
    app.setStyle('fusion')  # Set Fusion style

    G.args = CmdLineArgs.parse()
    G.is_exe = getattr(sys, 'frozen', False)
    headless_mode = G.args.headless
    G.master_instance = not G.args.child

    if G.master_instance:
        _check_master_instance_mutex()

    G.child_instance = G.args.child
    G.system_settings = utils.SystemSettings()

    _setup_theme_and_styling(app)
    _setup_device_configuration()

    G.args.sim = str.upper(G.args.sim)
    G.args.type = str.lower(G.args.type)

    _determine_master_instance_status()

    sys.path.insert(0, '')
    # sys.path.append('/simconnect')

    version = utils.get_version()
    dev_serial = None

    _setup_config_paths()
    utils.create_empty_userxml_file(G.userconfig_path)

    if G.is_exe:
        appmode = 'Executable'
    else:
        appmode = 'Source'

    logging.info("**************************************")
    logging.info("**************************************")
    logging.info(f"*****    TelemFFB version {version}: starting up from {appmode}:  Args= {G.args.__dict__}")
    logging.info("**************************************")
    logging.info("**************************************")

    if G.args.teleplot:
        logging.info(f"Using {G.args.teleplot} for plotting")
        utils.teleplot.configure(G.args.teleplot)

    def excepthook(exc_type, exc_value, exc_tb):
        if exc_type == KeyboardInterrupt:
            utils.exit_application()

        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stdout.write(f"{AnsiColors.BRIGHT_REDBG}[{G.device_type}]{AnsiColors.WHITE}{tb}{AnsiColors.END}")
        #QtWidgets.QApplication.quit()
        # or QtWidgets.QApplication.exit(0)
    sys.excepthook = excepthook

    _apply_custom_stylesheet(app)
    
    G.log_window = LogWindow()
    _init_logging(G.log_window.widget)
    G.log_window.pause_button.clicked.connect(sys.stdout.toggle_pause)

    _initialize_settings_manager()

    logging.info(f"TelemFFB (version {version}) Starting")

    dev, dev_serial, dev_firmware_version = _initialize_device_connection()

    _setup_logging_level()

    G.telem_manager = TelemManager()
    G.telem_manager.start()
    G.sim_listeners = SimListenerManager()
    G.main_window = MainWindow()

    _setup_ipc_and_connections()
    _setup_device_button_connections()

    _launch_children()

    _handle_window_display(headless_mode)
    _check_version_update()

    if G.master_instance:
        G.main_window.setup_master_instance()

    _check_system_settings_required()

    _setup_async_initialization(dev, dev_serial)

    G.sim_listeners.start_all()

    app.exec()

    _cleanup_on_exit(dev_serial)

def _init_logging(log_widget : QPlainTextEdit):
    log_folder = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB", 'log')
    
    sys.stdout = utils.OutLog(log_widget, sys.stdout)
    sys.stderr = utils.OutLog(log_widget, sys.stderr)

    if not os.path.exists(log_folder):
        os.makedirs(log_folder)

    date_str = datetime.now().strftime("%Y%m%d")

    logname = "".join(["TelemFFB", "_", G.device_usbvidpid.replace(":", "-"), '_', G.device_type, "_", date_str, ".log"])
    log_file = os.path.join(log_folder, logname)

    # Create a logger instance
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    logging.addLevelName(logging.DEBUG, f'{AnsiColors.GREEN}DEBUG{AnsiColors.END}')
    logging.addLevelName(logging.INFO, f'{AnsiColors.BLUE}INFO{AnsiColors.END}')
    logging.addLevelName(logging.ERROR, f'{AnsiColors.REDBG}{AnsiColors.WHITE}ERROR{AnsiColors.END}')
    logging.addLevelName(logging.WARNING, f'{AnsiColors.YELLOW}WARNING{AnsiColors.END}')

    # remove ansi escape strings
    class MyFormatter(logging.Formatter):
        def format(self, record):
            s = super().format(record)
            p = utils.parseAnsiText(s)
            return "".join([txt[0] for txt in p])
            
    # Create a formatter for the log messages
    fmt_string = f'{utils.AnsiColors.DARK_GRAY}%(asctime)s.%(msecs)03d - {G.device_type}{utils.AnsiColors.END} - %(levelname)s - %(message)s'
    formatter = logging.Formatter(fmt_string, datefmt='%Y-%m-%d %H:%M:%S')
    formatter_file = MyFormatter(fmt_string, datefmt='%Y-%m-%d %H:%M:%S')

    # Create a StreamHandler to log messages to the console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # Create a FileHandler to log messages to the log file
    file_handler = logging.FileHandler(log_file, mode='a')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter_file)

    # Add the handlers to the logger
    #logger.addHandler(console_handler)
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

    logging.getLogger().handlers[0].setStream(sys.stdout)
    logging.getLogger().handlers[0].setFormatter(formatter)

    if not G.child_instance:
        try:    # in case other instance tries doing at the same time
            utils.archive_logs(log_folder)
        except Exception: pass


if __name__ == "__main__":
    main()
