"""
TelemFFB Main Application Entry Point

This module orchestrates the startup and initialization of the TelemFFB application,
which provides force feedback telemetry for flight simulation devices.

Application Flow:
1. Command line argument parsing and initial setup
2. Master/child instance determination and mutex checking
3. Device configuration and USB PID/VID setup
4. Theme and styling configuration
5. Configuration file path setup
6. Device connection and firmware validation
7. Logging system initialization
8. Core component initialization (TelemManager, SimListeners, MainWindow)
9. IPC network setup for multi-instance communication
10. Window display and final setup
11. Background initialization tasks
12. Application event loop
13. Cleanup on exit
"""

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

# When running from source, the local ./simconnect/ folder (which only holds
# SimConnect.dll and scvars*.json for PyInstaller bundling) is picked up by
# Python as a namespace package and shadows the installed pysimconnect regular
# package, leaving `from simconnect import *` empty. Pre-load the real package
# from site-packages so all later imports see it.
def _bootstrap_simconnect():
    import os
    import importlib.util
    if 'simconnect' in sys.modules and getattr(sys.modules['simconnect'], '__file__', None):
        return
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.normcase(os.path.join(here, 'simconnect'))
    for entry in sys.path:
        if not entry:
            entry = os.getcwd()
        candidate = os.path.normcase(os.path.join(entry, 'simconnect'))
        if candidate == local:
            continue
        init_py = os.path.join(entry, 'simconnect', '__init__.py')
        if os.path.isfile(init_py):
            spec = importlib.util.spec_from_file_location(
                'simconnect', init_py,
                submodule_search_locations=[os.path.dirname(init_py)])
            module = importlib.util.module_from_spec(spec)
            sys.modules['simconnect'] = module
            spec.loader.exec_module(module)
            return
_bootstrap_simconnect()

from PyQt6.QtGui import QIcon, QColor, QFont

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
from telemffb.hw.ffb_rhino import DeviceInfo, FFBRhino, HapticEffect
from telemffb.IPCNetworkThread import IPCNetworkThread
from telemffb.LogWindow import LogWindow
from telemffb.MainWindow import MainWindow
from telemffb.SettingsManager import SettingsManager
from telemffb.telem.SimTelemListener import SimListenerManager
from telemffb.ConfiguratorDialog import ConfiguratorDialog
from telemffb.telem.TelemManager import TelemManager
from telemffb.utils import (AnsiColors, LoggingFilter, exit_application,
                            upload_vpconf_profile)
from telemffb.namedmutex import NamedMutex
import styles
resources # used
mutex = None

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
        utils.check_launch_instance("shaker", master_port)

    except Exception:
        logging.exception("Error during Auto-Launch sequence")


def _check_master_instance_mutex():
    """
    Check if another master instance is already running.

    Uses a named mutex to ensure only one master instance can run at a time.
    Displays warning dialog and exits if another master is detected.

    Flow: Mutex check -> Warning dialog if conflict -> Exit if needed
    """
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Icon.Warning)
    msg_box.setWindowTitle("TelemFFB is already running")
    msg_box.setText(
        "TelemFFB is already running and cannot be started.  If you don't see the 'VP' icon in the system tray, "
        "check the task manager for possible hung instances."
    )
    msg_box.setWindowIcon(QIcon(':/image/vpforceicon.png'))
    global mutex
    try:
        mutex = NamedMutex("VPforce_TelemFFB_Master_Instance", acquired=True, timeout=1)
        if not mutex.acquired:
            msg_box.exec()
            sys.exit(1)
    except WindowsError:
        msg_box.exec()
        sys.exit(1)

def _setup_device_configuration():
    """
    Configure device type and USB VID/PID based on args or system settings.

    Flow:
    1. If no device specified in args, use system settings mapping
    2. Map device type to USB PID from configuration
    3. Set global device variables for use throughout application
    """
    if G.args.device is None:
        mapping = {1: "joystick", 2: "pedals", 3: "collective", 4: "trimwheel", 5: "shaker"}
        master_rb = G.system_settings.get('masterInstance', 1)

        try:
            d = mapping[master_rb]
            G.device_usbpid = str(G.system_settings.get(f'pid{d.capitalize()}', "2055"))
            G.device_type = d
        except KeyError:
            G.device_usbpid = '2055'
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
        
    assert isinstance(G.device_usbpid, str), "Device USB PID must be a string"

def _setup_theme_and_styling(app):
    """
    Configure application theme and styling based on system settings.

    Flow:
    1. Read theme preference (light/dark/system)
    2. Set Qt color scheme accordingly
    3. Create custom palette with accent colors
    4. Apply dark mode palette if needed
    5. Apply custom stylesheets
    """
    theme_setting = G.system_settings.get('themeId', 2)

    if G.args.darkmode:
        theme_setting = 1

    if G.args.lightmode:
        theme_setting = 0

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
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#cccccc"))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor('#dddddd'))
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor('red'))

    # Disabled colors
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.WindowText, QColor(127, 127, 127))
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
    """
    Determine if this instance should be the master based on device type.

    Flow:
    1. Map device types to indices
    2. Compare current device with configured master device
    3. Set master instance flag accordingly

    The master instance coordinates child instances and provides system tray interface.
    """
    index_dict = {
        'joystick': 1,
        'pedals': 2,
        'collective': 3,
        'trimwheel': 4,
        'shaker': 5,
    }
    master_index = G.system_settings.get('masterInstance', 1)
    if index_dict[G.device_type] == master_index:
        G.master_instance = True
    else:
        G.master_instance = False

    if G.device_type == 'shaker' and G.master_instance:
        msg = ("Shaker device cannot run as the master instance. "
               "Configure a non-shaker device (joystick / pedals / collective / trimwheel) "
               "as the master in System Settings.")
        logging.error(msg)
        QMessageBox.critical(None, "Invalid configuration", msg)
        sys.exit(1)

def _setup_routing():
    """Initialise the multi-device EffectRouter for THIS process.

    Strict opt-in: if [devices].deviceInventory is empty (the default for
    fresh installs and for users who haven't run the Setup Wizard yet),
    leave G.effect_router=None and skip the backend swap. The aircraft
    pipeline then behaves exactly as before — no behavioural drift.

    When an inventory exists, identify the entry corresponding to this
    process by matching device_type + (optional) USB PID, then build a
    router seeded with the bundled defaults and any user-saved overrides.
    The actual backend swap happens in ``_initialize_device_connection``
    after ``HapticEffect.open()`` has assigned the Rhino device.
    """
    from telemffb.device_inventory import (
        first_of_type, load_inventory_from_ini,
    )
    from telemffb.routing import EffectRouter, EffectRoutesPack, load_routes_pack

    inventory_blob = G.system_settings.get("deviceInventory", "")
    G.devices = load_inventory_from_ini(inventory_blob)

    if not G.devices:
        # No multi-device setup configured -> stay on legacy code path.
        G.effect_router = None
        G.device_id = ""
        G.device_positions = []
        return

    # Identify THIS process. Prefer USB-PID match (stable across renames),
    # fall back to type. Inventory entries with no matching usb_pid still
    # bind by type — wizard-generated inventories always set a usb_pid.
    self_dev = None
    if G.device_usbvidpid:
        for d in G.devices:
            if d.usb_pid and d.usb_pid.lower() == G.device_usbvidpid.lower():
                self_dev = d
                break
    if self_dev is None:
        self_dev = first_of_type(G.devices, G.device_type)

    if self_dev is not None:
        G.device_id = self_dev.device_id
        G.device_positions = list(self_dev.positions)
    else:
        # Inventory present but no matching entry -> create a synthetic id
        # so the router still has something to filter against.
        G.device_id = f"{G.device_type}_unmapped"
        G.device_positions = []
        logging.warning(
            "Routing: no inventory entry matched %s (%s); using id=%r",
            G.device_type, G.device_usbvidpid, G.device_id,
        )

    # Load defaults + user overrides into the router.
    defaults_path = utils.get_resource_path(
        os.path.join("telemffb", "data", "effect_routes_default.json"),
        prefer_root=True,
    )
    defaults = load_routes_pack(defaults_path) or EffectRoutesPack()

    user_overrides = EffectRoutesPack()
    if G.userconfig_rootpath:
        user_path = os.path.join(G.userconfig_rootpath, "effect_routes_user.json")
        loaded = load_routes_pack(user_path)
        if loaded is not None:
            user_overrides = loaded

    G.effect_router = EffectRouter(defaults=defaults, user_overrides=user_overrides)
    logging.info(
        "EffectRouter ready: device_id=%r positions=%s known_effects=%d",
        G.device_id, G.device_positions,
        len(G.effect_router.known_effect_names()),
    )


def _maybe_show_setup_wizard():
    """First-run prompt for the multi-device setup wizard.

    Conditions:
    - Master instance only (children inherit the inventory).
    - Wizard hasn't been completed yet ([system].setup_wizard_done is false).
    - The inventory is currently empty.
    Both conditions are checked by ``SetupWizard.should_offer_wizard()``.
    """
    if not G.master_instance:
        return
    try:
        from telemffb.SetupWizard import SetupWizard, should_offer_wizard
    except Exception:
        logging.exception("SetupWizard module unavailable; skipping first-run prompt")
        return
    if not should_offer_wizard():
        return
    try:
        wiz = SetupWizard(G.main_window)
        wiz.exec()
    except Exception:
        logging.exception("SetupWizard execution failed")


def _setup_config_paths():
    """
    Setup configuration file paths based on build type and mode.

    Flow:
    1. Set defaults path from resources
    2. Configure logo based on build type and theme
    3. Setup userconfig paths for dev vs production
    4. Handle dev userconfig copying if needed
    """
    G.defaults_path = utils.get_resource_path('defaults.xml', prefer_root=True)

    if G.dev_build:
        G.vpf_logo = ":/image/DEVlogo.png"
        if G.dev_userconfig:
            _setup_dev_userconfig_paths()
        else:
            _setup_standard_config_paths()
    else:
        _setup_standard_config_paths()

def _setup_dev_userconfig_paths():
    """Setup development userconfig paths."""
    real_userconfig_path = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB")
    real_userconfig = os.path.join(real_userconfig_path, 'userconfig_v2.xml')
    real_legacy_userconfig = os.path.join(real_userconfig_path, 'userconfig.xml')

    if getattr(sys, 'frozen', False):
        G.userconfig_rootpath = os.path.dirname(sys.executable)
    else:
        G.userconfig_rootpath = os.path.dirname(os.path.abspath(__file__))

    G.userconfig_path = os.path.join(G.userconfig_rootpath, 'userconfig_v2.xml')
    if not os.path.isfile(G.userconfig_path) and os.path.exists(real_userconfig):
        shutil.copy(real_userconfig, G.userconfig_path)

    if not os.path.isfile(os.path.join(G.userconfig_rootpath, 'userconfig_.xml')) and os.path.exists(real_legacy_userconfig):
        shutil.copy(real_legacy_userconfig, os.path.join(G.userconfig_rootpath, 'userconfig.xml'))


def _setup_standard_config_paths():
    """Setup standard configuration paths."""
    G.userconfig_rootpath = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB")
    G.userconfig_path = os.path.join(G.userconfig_rootpath, 'userconfig_v2.xml')

def _initialize_device_connection():
    """
    Initialize connection to the Rhino device and check firmware.
    
    Flow:
    1. Parse USB VID/PID from configuration
    2. Enumerate all available Rhino devices
    3. Attempt to connect to specified device
    4. Validate firmware version meets requirements
    5. Extract device identity and serial number
    
    Returns:
        tuple: (device_object, serial_number, firmware_version)
    """
    min_firmware_version = 'v1.0.18'
    dev_serial = None
    dev_firmware_version = 'ERROR'
    dev = None

    if G.device_type == 'shaker':
        try:
            from telemffb.hw.shaker_synth import ShakerSynth
            from telemffb.hw.ffb_shaker import init_shaker
            from telemffb.sim import aircraft_base
            device_name = G.system_settings.get('shakerDevice', None) or None
            gain = float(G.system_settings.get('shakerGain', 1.0))
            channel_mode = G.system_settings.get('shakerChannelMode', 'mono') or 'mono'
            try:
                pan = float(G.system_settings.get('shakerPan', 0.0))
            except (TypeError, ValueError):
                pan = 0.0
            synth = ShakerSynth(device=device_name, master_gain=gain,
                                channel_mode=channel_mode, pan=pan)
            synth.start()
            init_shaker(synth)
            G.shaker_synth = synth
            aircraft_base.use_shaker_backend()
            from telemffb.hw import ffb_shaker as _ffb_shaker
            from telemffb.hw.shaker_layers_io import get_shaker_effects_path, get_default_pack_path
            user_path = get_shaker_effects_path()
            if user_path and not os.path.exists(user_path):
                default_path = get_default_pack_path()
                if default_path and os.path.exists(default_path):
                    try:
                        os.makedirs(os.path.dirname(user_path), exist_ok=True)
                        shutil.copyfile(default_path, user_path)
                        logging.info("Initialised %s from bundled defaults", user_path)
                    except Exception:
                        logging.exception("Could not seed user shaker_effects.json from bundle")
            _ffb_shaker.reload_layers()
            logging.info("Shaker effects: %d effects with custom layers",
                         len(_ffb_shaker.EFFECT_LAYERS))

            # Load shaker calibration profile pack and pick the active one.
            from telemffb.hw import shaker_profiles_io
            from telemffb.hw.shaker_profile import DEFAULT_PROFILE
            profiles_user_path = shaker_profiles_io.get_user_profiles_path()
            if profiles_user_path and not os.path.exists(profiles_user_path):
                profiles_default_path = shaker_profiles_io.get_default_pack_path()
                if profiles_default_path and os.path.exists(profiles_default_path):
                    try:
                        os.makedirs(os.path.dirname(profiles_user_path), exist_ok=True)
                        shutil.copyfile(profiles_default_path, profiles_user_path)
                        logging.info("Initialised %s from bundled defaults",
                                     profiles_user_path)
                    except Exception:
                        logging.exception(
                            "Could not seed user shaker_profiles.json from bundle")
            file_active, profiles = (None, {})
            if profiles_user_path:
                file_active, profiles = shaker_profiles_io.load(profiles_user_path)
            if not profiles:
                bundled = shaker_profiles_io.get_default_pack_path()
                if bundled and os.path.exists(bundled):
                    file_active, profiles = shaker_profiles_io.load(bundled)
            active_name = (G.system_settings.get('shakerProfile')
                           or file_active or 'Generic')
            active_profile = profiles.get(active_name)
            if active_profile is None and profiles:
                active_profile = next(iter(profiles.values()))
            if active_profile is None:
                active_profile = DEFAULT_PROFILE
            _ffb_shaker.set_active_profile(active_profile)
            G.shaker_active_profile = active_profile
            logging.info("Shaker calibration profile: %r (loaded %d profiles)",
                         active_profile.name, len(profiles))
            G.device_connection_status = True
            logging.info(f"Shaker device initialised (output={device_name!r}, gain={gain})")
        except Exception as e:
            G.device_connection_status = False
            logging.exception("Failed to initialise shaker audio device")
            QMessageBox.warning(None, "Shaker initialisation failed",
                              f"Unable to start audio output for shaker device.\n"
                              f"Error: {e}\n\n"
                              "Open System Settings and verify the Shaker output device.")
        return dev, dev_serial, dev_firmware_version

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
        G.device_firmware_version = dev_firmware_version
        dev_serial = dev.serial

        if dev_firmware_version:
            logging.info(f"Rhino Firmware: {dev_firmware_version}")
            _check_firmware_version(dev_firmware_version, min_firmware_version)

        G.device_ident = dev.info.ident
        G.device_connection_status = True

        # Multi-device routing: swap aircraft_base.effects to the router-aware
        # backend AFTER HapticEffect.open() has assigned the Rhino device, so
        # the inherited cls.device attribute is already populated. Strict
        # opt-in: only fires when [devices] inventory is present and resolved.
        if G.effect_router is not None and G.device_id:
            from telemffb.sim import aircraft_base
            from telemffb.routing import ffb_router as _ffb_router
            aircraft_base.use_router_backend()
            _ffb_router.init_router(
                G.effect_router,
                device_id=G.device_id,
                device_type=G.device_type,
                device_positions=G.device_positions,
            )
            logging.info("Effect routing active for %s (id=%r)",
                         G.device_type, G.device_id)

    except Exception as e:
        G.device_connection_status = False
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
            G.instance_dev_dict[devinfo.product_id] = devinfo

    logging.info("-------")

def _check_firmware_version(dev_firmware_version, min_firmware_version):
    """Check if device firmware version meets minimum requirements."""
    minver = re.sub(r'\D', '', min_firmware_version)
    devver = re.sub(r'\D', '', dev_firmware_version)
    if devver < minver:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Outdated Firmware")
        msg.setTextFormat(Qt.TextFormat.RichText)  # Enable HTML formatting
        msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        msg.setText(
            f"This version of TelemFFB requires Rhino Firmware version <b>{minver}</b> or later.<br><br>"
            f"The current version installed is <b>{devver}</b>.<br><br>"
            "Please update to avoid errors.<br>"
            '<a href="https://vpforcecontrols.com/usb/rhino/">Web USB Updater</a>'
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

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

def _convert_user_config():
    """
    Converts user config from legacy single user profile to multi-user profile capabilities.
    """
    if G.master_instance:
        xmlutils.update_roots()
        xmlutils.update_vars(G.device_type, G.userconfig_path, G.defaults_path)
        utils.convert_legacy_userconfig(G.userconfig_path)


def _initialize_settings_manager():
    """
    Initialize the settings manager with error handling for corrupted config.

    Flow:
    1. Initialize the xml root trees in XML Utils - is kept up to date when config changes
    1. Update XML variables with current device/paths
    2. Attempt to create settings manager
    3. If corruption detected, offer backup/reset option
    4. Create new default config if user agrees
    """
    xmlutils.update_roots()
    xmlutils.update_vars(G.device_type, G.userconfig_path, G.defaults_path)
    try:
        G.settings_mgr = SettingsManager(datasource="Global", device=G.device_type,
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
        G.settings_mgr = SettingsManager(datasource="Global", device=G.device_type,
                                      userconfig_path=G.userconfig_path,
                                      defaults_path=G.defaults_path,
                                      system_settings=G.system_settings)
        QMessageBox.information(None, "New Userconfig created", f"A backup has been created: {backup_file}\n")
    else:
        QCoreApplication.instance().quit()
        return

def _setup_ipc_and_connections():
    """
    Setup IPC network thread and connect all signals.

    Flow:
    1. Create IPC network thread for inter-instance communication
    2. Connect all IPC signals to appropriate handlers
    3. Start IPC thread for message processing

    Enables master-child coordination and remote control capabilities.
    """
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
    G.ipc_instance.reload_caller_signal.connect(G.main_window.settings_layout.reload_caller)
    G.ipc_instance.reload_aircraft_signal.connect(G.main_window.force_reload_aircraft)
    G.ipc_instance.toggle_offline_mode_signal.connect(G.main_window.toggle_offline_mode)
    G.ipc_instance.set_offline_sim_signal.connect(G.main_window.offline_sim.setCurrentText)
    G.ipc_instance.set_offline_class_signal.connect(G.main_window.offline_class.setCurrentText)
    G.ipc_instance.set_offline_ac_signal.connect(G.main_window.offline_name.setCurrentText)
    G.ipc_instance.set_offline_profile_signal.connect(G.main_window.offline_profile.setCurrentText)
    G.ipc_instance.show_offline_model_signal.connect(G.main_window.load_single_offline_model)
    G.ipc_instance.start()

def _setup_device_button_connections():
    """Setup device button event connections."""
    try:
        HapticEffect.device.buttonPressed.connect(G.main_window.get_active_buttons)
        HapticEffect.device.buttonReleased.connect(G.main_window.get_active_buttons)
    except:
        pass

def _setup_device_connect_status():
    """Setup device disconnect/reconnect status signals"""
    try:
        HapticEffect.device.deviceConnected.connect(G.main_window.update_device_status)
    except:
        pass
def _sim_connected_events():
    G.sim_listeners.allStarted.connect(G.telem_manager.reset_sim_connected)
    G.telem_manager.first_frame_received.connect(G.sim_listeners.stop_inactive)

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
    # Shaker child has no Rhino device — no configurator gains / vpconf to push.
    if dev is None:
        return

    @utils.threaded()
    def init_async():
        try:
            G.startup_configurator_gains = dev.get_gains()
        except Exception:
            logging.exception("Unable to get configurator slider values from device")

        if G.system_settings.get('enableVPConfStartup', False):
            logging.info(f'Starting async "startup vpconf" config push: {G.system_settings.get("pathVPConfStartup", "")}')
            G.vpconf_init_pending = True # True flag delays telemetry process until async process completed by upload_vpconf_profile
            try:
                upload_vpconf_profile(G.system_settings.get('pathVPConfStartup', ''), dev_serial)
            except Exception:
                G.vpconf_init_pending = False # allow telem processing if config push fails
                logging.exception("Unable to set VPConfigurator startup profile")

        try:
            G.vpconf_configurator_gains = dev.get_gains()
        except Exception:
            logging.exception("Unable to get configurator slider values from device")

    init_async()

def _cleanup_on_exit(dev_serial):
    """
    Handle cleanup operations when application exits.

    Flow:
    1. Notify child instances to close
    2. Stop IPC communication
    3. Stop all simulation listeners
    4. Quit telemetry manager
    5. Apply exit VPConfigurator profile if configured
    6. Reset device gains to startup values if configured
    """
    if G.ipc_instance:
        G.ipc_instance.notify_close_children()
        G.ipc_instance.stop()

    G.sim_listeners.stop_all()
    G.telem_manager.quit()

    if G.system_settings.get('enableVPConfExit', False):
        try:
            upload_vpconf_profile(G.system_settings.get('pathVPConfExit', ''), dev_serial)
        except Exception:
            logging.error("Unable to set VPConfigurator exit profile")

    if G.system_settings.get('enableResetGainsExit', False):
        try:
            G.gain_override_dialog.set_gains_from_object(G.startup_configurator_gains)
        except:
            pass
    HapticEffect.device.set_deadzone(0) #ensure deadzone is set back to configurator value on exit

def main():
    """
    Main application entry point and initialization flow.

    This function orchestrates the complete startup sequence:
    - Sets up Qt application and basic configuration
    - Determines master/child instance status
    - Initializes device connections and validates firmware
    - Sets up logging, telemetry, and UI components
    - Starts background services and displays the main window
    - Handles the application event loop until exit
    """
    # ============================================================================
    # PHASE 1: Qt Application and Basic Setup
    # ============================================================================
    #QApplication.setAttribute(QtCore.Qt.ApplicationAttribute. AA_EnableHighDpiScaling, True) #enable highdpi scaling
    #QApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)  #use highdpi icons

    dev : FFBRhino = None

    # Initialize Qt application with Fusion style for consistent cross-platform appearance
    app = QApplication(sys.argv)
    app.setStyle('fusion')  # Set Fusion style
    app.setFont(QFont('Segoe UI', 10))

    # ============================================================================
    # PHASE 2: Command Line Arguments and Instance Management
    # ============================================================================
    # Parse command line arguments to determine device type, ports, and operation mode
    G.args = CmdLineArgs.parse()
    G.is_exe = getattr(sys, 'frozen', False)
    headless_mode = G.args.headless
    G.master_instance = not G.args.child

    # Ensure only one master instance can run at a time using named mutex
    if G.master_instance:
        if not G.allow_multi_instance:
            _check_master_instance_mutex()

    # ============================================================================
    # PHASE 3: System Configuration and Device Setup
    # ============================================================================
    # Set child instance flag and load system-wide settings
    G.child_instance = G.args.child
    G.system_settings = utils.SystemSettings()

    # Configure application theme (light/dark/system) and apply custom styling
    _setup_theme_and_styling(app)

    # Determine device type and USB VID/PID based on args or system settings
    _setup_device_configuration()

    # Determine if this instance should be the master based on device type
    _determine_master_instance_status()

    # ============================================================================
    # PHASE 4: Version Info and Path Setup
    # ============================================================================
    sys.path.insert(0, '')
    # sys.path.append('/simconnect')

    # Get application version and initialize device serial tracking
    version = utils.get_version()
    dev_serial = None

    # Setup configuration file paths (dev vs production, userconfig locations)
    _setup_config_paths()

    # Load the multi-device routing inventory (no-op when empty -> behaves
    # exactly like pre-routing builds). Must run AFTER _setup_config_paths
    # so user-override JSON resolves against the right userconfig_rootpath.
    _setup_routing()

    # if legacy 'userconfig.xml' exists, copy it to 'userconfig_v2' for conversion
    utils.copy_legacy_config_to_new(G.userconfig_path)

    # check if userconfig exists.  If not, create empty one
    utils.create_empty_userxml_file(G.userconfig_path)

    # Determine if running from executable or source for logging
    if G.is_exe:
        appmode = 'Executable'
    else:
        appmode = 'Source'

    # ============================================================================
    # PHASE 5: Logging and Debug Setup
    # ============================================================================
    # Log startup banner with version and configuration info
    logging.info("**************************************")
    logging.info("**************************************")
    logging.info(f"*****    TelemFFB version {version}: starting up from {appmode}:  Args= {G.args.__dict__}")
    logging.info("**************************************")
    logging.info("**************************************")

    # Setup teleplot integration for real-time plotting if specified
    if G.args.teleplot:
        logging.info(f"Using {G.args.teleplot} for plotting")
        utils.teleplot.configure(G.args.teleplot)

    # Configure global exception handler for better error reporting
    def excepthook(exc_type, exc_value, exc_tb):
        if exc_type == KeyboardInterrupt:
            utils.exit_application()

        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stdout.write(f"{AnsiColors.BRIGHT_REDBG}[{G.device_type}]{AnsiColors.WHITE}{tb}{AnsiColors.END}")
        #QtWidgets.QApplication.quit()
        # or QtWidgets.QApplication.exit(0)
    sys.excepthook = excepthook

    # ============================================================================
    # PHASE 6: UI and Logging Window Setup
    # ============================================================================
    # Apply final styling and create log window for debug output
    _apply_custom_stylesheet(app)

    # Initialize log window and redirect stdout/stderr for capture
    G.log_window = LogWindow()
    _init_logging(G.log_window.widget)
    G.log_window.pause_button.clicked.connect(sys.stdout.toggle_pause)

    # ============================================================================
    # PHASE 7: Convert Legacy userconfig to new format
    # ============================================================================
    _convert_user_config()

    # ============================================================================
    # PHASE 8: Settings and Configuration Management
    # ============================================================================
    # Initialize settings manager with error handling for corrupted configs
    _initialize_settings_manager()

    logging.info(f"TelemFFB (version {version}) Starting")

    # ============================================================================
    # PHASE 9: Device Connection and Firmware Validation
    # ============================================================================
    # Connect to Rhino FFB device and validate firmware version
    dev, dev_serial, dev_firmware_version = _initialize_device_connection()

    # Set logging level based on system settings
    _setup_logging_level()

    # ============================================================================
    # PHASE 10: Core Component Initialization
    # ============================================================================
    # Initialize telemetry manager for handling sim data
    G.telem_manager = TelemManager()
    G.telem_manager.start()

    # Initialize simulation listener manager for multiple sim support
    G.sim_listeners = SimListenerManager()

    # Create main application window
    G.main_window = MainWindow()

    # ============================================================================
    # PHASE 11: Inter-Process Communication Setup
    # ============================================================================
    # Setup IPC for master-child instance communication and connect all signals
    _setup_ipc_and_connections()

    # Connect device button events to main window handlers
    _setup_device_button_connections()

    # Connect device status events to main window handler:
    _setup_device_connect_status()

    # Connect sim start/stop signals
    _sim_connected_events()

    # ============================================================================
    # PHASE 12: Child Instance Management (Master Only)
    # ============================================================================
    # Launch child instances if this is the master and auto-launch is enabled
    _launch_children()

    # ============================================================================
    # PHASE 12: Window Display and UI Presentation
    # ============================================================================
    # Show main window based on configuration (minimized, tray, normal)
    _handle_window_display(headless_mode)

    # Check for version updates in background (non-release builds)
    _check_version_update()

    # Setup master-specific features (system tray, etc.)
    if G.master_instance:
        G.main_window.setup_master_instance()

    # Prompt for system settings if no devices are configured
    _check_system_settings_required()

    # First-run multi-device setup wizard. Master-only (children inherit
    # the inventory via the shared config). Skipped if the inventory is
    # already populated or the user has explicitly dismissed the wizard.
    _maybe_show_setup_wizard()

    # ============================================================================
    # PHASE 14: Background Initialization
    # ============================================================================
    # Start background tasks that don't block UI appearance
    _setup_async_initialization(dev, dev_serial)

    # ============================================================================
    # PHASE 15: Service Startup and Event Loop
    # ============================================================================
    # Start all simulation listeners to begin telemetry processing
    G.sim_listeners.start_all()

    # Enter Qt application event loop - application runs until user exits
    app.exec()

    # ============================================================================
    # PHASE 16: Cleanup and Shutdown
    # ============================================================================
    # Perform cleanup operations when application exits
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
