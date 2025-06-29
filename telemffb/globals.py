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

from typing import TYPE_CHECKING, Dict, Optional, Any

if TYPE_CHECKING:
    # from PyQt5.QtCore import QSettings
    from .LogWindow import LogWindow
    from .IPCNetworkThread import IPCNetworkThread
    from .utils import SystemSettings, ChildPopen
    from .SettingsManager import SettingsManager
    from .telem.TelemManager import TelemManager
    from .telem.SimTelemListener import SimListenerManager
    from telemffb.MainWindow import MainWindow
    from subprocess import Popen
    from telemffb.CmdLineArgs import CmdLineArgs
    from telemffb.ConfiguratorDialog import ConfiguratorDialog
    from telemffb.hw.ffb_rhino import DeviceInfo

# Application state
is_exe: bool = False
args : 'CmdLineArgs'

# Version and build configuration
release_version : bool = False  # When true, build version will be 'release_version_str' and will not look for updates
release_version_str: str = "Vx.x.x"
dev_build : bool = False # when True, build versions will use 'dev_build_str', will use a dev branded logo and will not look for updates
dev_userconfig: bool = True  # will use/create userconfig.xml in root when True (dev_build must also be true)
dev_build_str: str = "DEV_BUILD"
allow_multi_instance: bool = False  # if true, will skip mutex lock checks and allow multiple instances to run simultaneously
vpf_logo: str = ":/image/vpforcelogo.png"

# UI components
main_window :  'MainWindow' 
settings_mgr : 'SettingsManager'
log_window :   'LogWindow' 
useDarkMode : bool = False

# Configuration paths and profiles
userconfig_rootpath : str = ""
userconfig_path : str = ""
defaults_path : str = ""
current_vpconf_profile : Optional[str] = None
current_device_config_scope: Optional[str] = None # add current device config scope to globals for tracking across telemffb modules
current_offline_config_scope: Optional[str] = None  # Tracks scope of offline config mode for checking in settings_mgr
offline_config_mode: bool = False  # Tracks offline config mode for checking in settings_mgr

# Device information
device_type : str = ""
device_usbpid : str 
device_usbvidpid : str  # "FFFF:2055"
device_ident : str  #Joystick, Pedals, etc.. as set in configurator

# Gain management
startup_configurator_gains: Optional[Any] = None  # Gain object direct from 'device.get_gains'.  Gains get read at TelemFFB startup fallback baseline values.
vpconf_configurator_gains: Optional[Any] = None  # Gain object direct from 'device.get_gains'. Updated every time a configurator profile is pushed to the device to use as revert data
current_configurator_gains: Optional[Any] = None  # Gain settings table set by gain override dialog.  Updated when gains set/saved in dialog or read from config
gain_override_dialog: 'ConfiguratorDialog'

# Instance management
launched_instances : Dict[str, 'ChildPopen'] = {}
instance_dev_dict : Dict[int, 'DeviceInfo'] = {}
master_instance : bool = False
child_instance : bool = False
ipc_instance : 'IPCNetworkThread'

# Button management
active_buttons: list[Any] = []
master_buttons: list[Any] = []
child_buttons: Dict[str, Any] = {}

# System components
system_settings : 'SystemSettings'
telem_manager : 'TelemManager'
sim_listeners : 'SimListenerManager'

# Triggers and flags
force_reload_aircraft_trigger: bool = False