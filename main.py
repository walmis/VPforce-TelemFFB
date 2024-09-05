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

import sys

from PyQt5.QtGui import QIcon

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

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import QCoreApplication
from PyQt5.QtWidgets import QApplication, QMessageBox, QPlainTextEdit

import resources
import telemffb.globals as G
import telemffb.utils as utils
import telemffb.xmlutils as xmlutils
from telemffb.config_utils import autoconvert_config
from telemffb.hw.ffb_rhino import FFBRhino, HapticEffect
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
resources # used

def send_test_message():
    if G.ipc_instance.running:
        if G.master_instance:
            G.ipc_instance.send_broadcast_message("TEST MESSAGE TO ALL")
        else:
            G.ipc_instance.send_message("TEST MESSAGE")


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


def launch_children():
    if not G.system_settings.get('autolaunchMaster'):
        return
    if not G.master_instance:
        return
    
    master_port = G.ipc_instance.local_port
    try:
        utils.check_launch_instance("joystick", master_port)
        utils.check_launch_instance("pedals", master_port)
        utils.check_launch_instance("collective", master_port)

    except Exception:
        logging.exception("Error during Auto-Launch sequence")


def main():
    QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True) #enable highdpi scaling
    QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)  #use highdpi icons
    
    app = QApplication(sys.argv)

    G.args = CmdLineArgs.parse()

    G.is_exe = getattr(sys, 'frozen', False)

    # script_dir = os.path.dirname(os.path.abspath(__file__))

    headless_mode = G.args.headless

    G.master_instance = not G.args.child

    if G.master_instance:
        # Attempt to acquire a mutex lock.  If the acquisition fails, another master instance of TelemFFB is already running.
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setWindowTitle("TelemFFB is already running")
        msg_box.setText(
            "TelemFFB is already running and cannot be started.  If you don't see the 'VP' icon in the system tray, "
            "check the task manager for possible hung instances."
        )
        msg_box.setWindowIcon(QIcon(':/image/vpforceicon.png'))
        try:
            mutex = NamedMutex("VPforce_TelemFFB_Master_Instance", acquired=True, timeout=1)
            if not mutex.acquired:
                msg_box.exec_()
                sys.exit(1)
        except WindowsError:
            msg_box.exec_()
            sys.exit(1)

    G.child_instance = G.args.child

    G.system_settings = utils.SystemSettings()

    # _vpf_logo = os.path.join(script_dir, "image/vpforcelogo.png")
    _vpf_logo = ":/image/vpforcelogo.png"
    if G.args.device is None:
        mapping = {1: "joystick", 2: "pedals", 3: "collective"}
        master_rb = G.system_settings.get('masterInstance', 1)
        
        try:
            dev = mapping[master_rb]
            G.device_usbpid = G.system_settings.get(f'pid{dev.capitalize()}', "2055")
            G.device_type = dev
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



    G.system_settings = utils.SystemSettings()

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
    if index_dict[G.device_type] == master_index:
        G.master_instance = True
    else:
        G.master_instance = False

    sys.path.insert(0, '')
    # sys.path.append('/simconnect')

    #################
    ################
    ###  Setting _release flag to true will disable all auto-updating and 'WiP' downloads server version checking
    ###  Set the version number to version tag that will be pushed to master repository branch
    G.release_version = False  # Todo: Validate release flag!

    version = utils.get_version()

    min_firmware_version = 'v1.0.15'

    dev_serial = None

    G.defaults_path = utils.get_resource_path('defaults.xml', prefer_root=True)
    G.userconfig_rootpath = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB")
    G.userconfig_path = os.path.join(G.userconfig_rootpath, 'userconfig.xml')

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

    app.setStyleSheet(
        """
        QCheckBox::indicator:checked { image: url(:/image/purplecheckbox.png); }
        QCheckBox::indicator:checked:disabled {image: url(:/image/disabledcheckbox.png); }
        QRadioButton::indicator:checked { image: url(:/image/rchecked.png);}

        QPushButton:!pressed, #styledButton:!pressed {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                              stop: 0 #e4a9e7, stop: 0.2 #c174e6,
                                              stop: 0.5 #ab37c8, stop: 0.8 #8e1da8, stop: 1.0 #6e1d6f);
            border-radius: 5px;
            padding: 3px;
            margin: 0px;
            color: white;
            border: 1px solid #6e1d6f; /* Existing border */

        }

        QPushButton:disabled:!pressed, #styledButton:disabled:!pressed {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                              stop: 0 #e1e1e1, stop: 0.2 #cccccc,
                                              stop: 0.5 #bbbbbb, stop: 0.8 #aaaaaa, stop: 1.0 #999999);
            color: #666666;  /* Set the text color for disabled buttons */
            border-radius: 5px;
            padding: 3px;
            margin: 0px;
            border: 1px solid #999999; /* Existing border */

        }
        
        QPushButton:pressed, #styledButton:pressed {
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                          stop: 0 #6e1d6f, stop: 0.2 #8e1da8,
                                          stop: 0.5 #ab37c8, stop: 0.8 #c174e6, stop: 1.0 #e4a9e7); /* Inverted gradient */
        border-radius: 5px;
        padding: 3px;
        margin: 0px;
        color: white;
        border: 1px solid #4e164e; /* Darker border to indicate pressed state */

        }

        QPushButton:hover:!pressed, #styledButton:hover:!pressed {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                              stop: 0 #f0b0f0, stop: 0.2 #d897d8,
                                              stop: 0.5 #c07ec0, stop: 0.8 #a965a9, stop: 1.0 #914b91);
            border-radius: 5px;
            padding: 3px;
            margin: 0px;
            border: 1px solid #8e1da8; /* Existing border */

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


    G.log_window = LogWindow()
    init_logging(G.log_window.widget)
    G.log_window.pause_button.clicked.connect(sys.stdout.toggle_pause)

    xmlutils.update_vars(G.device_type, G.userconfig_path, G.defaults_path)
    try:
        G.settings_mgr = SettingsWindow(datasource="Global", device=G.device_type, userconfig_path=G.userconfig_path, 
                                        defaults_path=G.defaults_path, system_settings=G.system_settings)
    except Exception:
        logging.exception("Error Reading user config file..")
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
            G.settings_mgr = SettingsWindow(datasource="Global", device=G.device_type, userconfig_path=G.userconfig_path, defaults_path=G.defaults_path, system_settings=G.system_settings)
            QMessageBox.information(None, "New Userconfig created", f"A backup has been created: {backup_file}\n")
        else:
            QCoreApplication.instance().quit()
            return

    logging.info(f"TelemFFB (version {version}) Starting")

    try:
        vid_pid = [int(x, 16) for x in G.device_usbvidpid.split(":")]
    except Exception:
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
            dev.reset_effects()
        dev_firmware_version = dev.get_firmware_version()
        dev_serial = dev.serial
        if dev_firmware_version:
            logging.info(f"Rhino Firmware: {dev_firmware_version}")
            minver = re.sub(r'\D', '', min_firmware_version)
            devver = re.sub(r'\D', '', dev_firmware_version)
            if devver < minver:
                QMessageBox.warning(None, "Outdated Firmware", f"This version of TelemFFB requires Rhino Firmware version {min_firmware_version} or later.\n\nThe current version installed is {dev_firmware_version}\n\n\n Please update to avoid errors!")

    except Exception as e:
        logging.exception("Exception")
        QMessageBox.warning(None, "Cannot connect to Rhino", f"Unable to open HID at {G.device_usbvidpid} for device: {G.device_type}\nError: {e}\n\nPlease open the System Settings and verify the Master\ndevice PID is configured correctly")
        dev_firmware_version = 'ERROR'

    # config = get_config()
    # ll = config["system"].get("logging_level", "INFO")
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
    
    
    

    G.telem_manager = TelemManager()
    G.telem_manager.start()
    G.sim_listeners = SimListenerManager()
    G.main_window = MainWindow()

    G.ipc_instance = IPCNetworkThread(dstport=G.args.masterport)
    G.ipc_instance.child_keepalive_signal.connect(G.main_window.update_child_status)
    G.ipc_instance.exit_signal.connect(exit_application)
    G.ipc_instance.restart_sim_signal.connect(G.sim_listeners.restart_all)
    G.ipc_instance.show_signal.connect(G.main_window.show)
    G.ipc_instance.hide_signal.connect(G.main_window.hide)
    G.ipc_instance.showlog_signal.connect(G.log_window.show)
    G.ipc_instance.show_settings_signal.connect(G.main_window.open_system_settings_dialog)
    G.ipc_instance.show_cfg_ovds_signal.connect(G.main_window.settings_layout.configurator_button_clicked)
    G.ipc_instance.erase_cfg_ovds_signal.connect(G.main_window.settings_layout.erase_configurator_overrides)
    G.ipc_instance.reload_aircraft_signal.connect(G.main_window.force_reload_aircraft)
    G.ipc_instance.start()

    HapticEffect.device.buttonPressed.connect(G.main_window.get_active_buttons)
    HapticEffect.device.buttonReleased.connect(G.main_window.get_active_buttons)

    launch_children()

    # log_tail_window = LogTailWindow(window)

    if not headless_mode:
        if G.args.minimize or G.system_settings.get('masterStartMin', False):
            G.main_window.showMinimized()
        elif G.master_instance and G.system_settings.get('startToTray', False):
            # Don't show window, tray message will pop during 'setup_master_instance'->'add_system_tray'
            pass
        else:
            G.main_window.show()

    autoconvert_config(G.main_window, utils.get_resource_path('config.ini'), utils.get_legacy_override_file())
    if not G.release_version:
        utils.FetchLatestVersion(G.main_window.update_version_result,
                                lambda error_message: logging.error("Error in thread: %s", error_message))


    if G.master_instance:
        G.main_window.setup_master_instance()

    if not G.system_settings.get("pidJoystick", None):
        G.main_window.open_system_settings_dialog()

    # G.telem_manager.telemetryTimeout.connect(lambda state: G.main_window.update_sim_indicators(G.telem_manager.getTelemValue("src"), state))

    # do some init in the background not blocking the main window first appearance
    @utils.threaded()
    def init_async():
        try:
            G.startup_configurator_gains = dev.get_gains()  # capture the gains at TelemFFB startup before any startup vpconf was pushed
        except Exception:
            logging.exception("Unable to get configurator slider values from device")

        if G.system_settings.get('enableVPConfStartup', False):
            try:
                set_vpconf_profile(G.system_settings.get('pathVPConfStartup', ''), dev_serial)
            except Exception:
                logging.exception("Unable to set VPConfigurator startup profile")

        try:
            G.vpconf_configurator_gains = dev.get_gains() # capture the gains here to use as reversion baseline any time a vpconf is pushed
            # utils.dbprint("green", f"Gains: {G.vpconf_configurator_gains}")
        except Exception:
            logging.exception("Unable to get configurator slider values from device")

    init_async()

    G.sim_listeners.start_all()

    app.exec_()

    if G.ipc_instance:
        G.ipc_instance.notify_close_children()
        G.ipc_instance.stop()

    G.sim_listeners.stop_all()
    G.telem_manager.quit()
    if G.system_settings.get('enableVPConfExit', False):
        ## Push the exit configurator profile if one is configured
        try:
            set_vpconf_profile(G.system_settings.get('pathVPConfExit', ''), dev_serial)
        except Exception:
            logging.error("Unable to set VPConfigurator exit profile")
    if G.system_settings.get('enableResetGainsExit', False):
        try:
            ## Last we push the gains we read at startup as good measure in case they were changed and never reverted by
            ## a model change
            G.gain_override_dialog.set_gains_from_object(G.startup_configurator_gains)
        except:
            pass


def init_logging(log_widget : QPlainTextEdit):
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
