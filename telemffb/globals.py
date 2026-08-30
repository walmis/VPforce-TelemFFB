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

from typing import TYPE_CHECKING, Dict, Literal, Optional, Any


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
    from telemffb.hw.ffb_rhino import DeviceInfo, HapticEffect
    from telemffb.utils import Dispenser
    from telemffb.ExceptionTracker import ExceptionTracker
    from .hw.ffb_rhino import DeviceInfo
    from telemffb.hw.ffb_rhino import FFBReport_Get_Gains_Feature_Data

DeviceTypeLiteral = Literal["joystick", "pedals", "collective", "trimwheel"]

# Application state
is_exe: bool = False
args : 'CmdLineArgs'

# Version and build configuration
release_version : bool = False
"""When true, build version will be 'release_version_str' and will not look for updates"""

release_version_str: str = "Vx.x.x"
"""Represents the current release version as a string in the format Vx.x.x."""

beta_build : bool = False
"""when True, build versions will use 'beta_build_str', will use a beta branded logo and will not look for updates"""

beta_build_str: str = "BETA - 07-02-26"

dev_build : bool = False 
"""when True, build versions will use 'dev_build_str', will use a dev branded logo and will not look for updates"""

dev_userconfig: bool = True
"""will use/create userconfig.xml in root when True (dev_build must also be true)"""

dev_build_str: str = "DEV_BUILD"
allow_multi_instance: bool = False
"""if true, will skip mutex lock checks and allow multiple instances to run simultaneously"""

dinput_bridge_min_version: str = "0.9.2"
"""Oldest DInput bridge build this TelemFFB accepts, as the bridge's own
'x.y.z' version string ('' disables the check).

Raised when TelemFFB starts depending on bridge behavior an older build
does not have - and, being the version pairing that was actually tested
together, it doubles as a light gate on redistributed builds.  A soft
one: anyone running from source can edit this line, which is inherent to
a GPL client and deliberately not fought here."""

vpf_logo: str = ":/image/TelemFFB_Logo.png"

release_notes_url: str = "https://docs.vpforce.eu/telemffb/latest/"
"""Stable release-notes URL: the docs site redirects /telemffb/latest/ to the newest release's entry on the release-notes page"""

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
"""File path of the currently loaded VPConfig profile"""

current_device_config_scope: Optional[DeviceTypeLiteral] = None 
"""add current device config scope to globals for tracking across telemffb modules"""

# Device information
device_type : DeviceTypeLiteral = "joystick"
"""device type: joystick, pedals, collective, trimwheel"""

device_info : Optional['DeviceInfo'] = None
"""DeviceInfo object representing the connected device. This attribute is redundant, since HapticEffect.device provides the same information."""

device_devpath : Optional[str] = None
"""System path to device, e.g. /dev/hidraw0 or \\?\\hid#vid_ffff&pid_2055&mi_00#7&2b3b4c3f&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"""

device_di_guid : Optional[str] = None
"""DirectInput instance GUID when this instance drives a generic DI FFB
device (stored as 'dinput:{GUID}' in the devpath_* setting). None = native
VPforce device."""

device_capabilities = None
"""ffb_backend.DeviceCapabilities of the connected device; feature-bearing
UI and effects gate on these flags rather than probing the backend."""

device_usbpid : str # deprecated

device_ident : str
"""Joystick, Pedals, etc.. as set in configurator"""

device_firmware_version : str
"""Firmware version as reported by device"""

device_connection_status: bool = False
"""status of HID connection to device"""

first_launch_autoconfig: Optional[bool] = None
"""First-launch device setup outcome. None = not a first launch (stored
configuration existed). True = no stored config, but the device for this
instance was auto-configured by name from the connected devices. False =
no stored config and the device could not be determined by name. Non-None
suppresses the startup connection attempt and forces the System Settings
dialog to open."""

il2_ffb_device_ordinal: Optional[int] = None
"""This instance's device ordinal ('lastAttachedId') as resolved from IL-2 Korea's
known.devices.json, used to match the 'devNo' field in FFB telemetry records.
Resolved once per SimIL2 listener start; None if not IL-2 Korea or not yet resolved."""

vpconf_init_pending: bool = False
"""switch to True when async device init is complete"""

# Gain management
startup_configurator_gains: Optional['FFBReport_Get_Gains_Feature_Data'] = None  
"""Gain object direct from 'device.get_gains'.  Gains get read at TelemFFB startup fallback baseline values."""
vpconf_configurator_gains: Optional['FFBReport_Get_Gains_Feature_Data'] = None  
"""Gain object direct from 'device.get_gains'. Updated every time a configurator profile is pushed to the device to use as revert data"""
current_configurator_gains: Optional['FFBReport_Get_Gains_Feature_Data'] = None  
"""Gain settings table set by gain override dialog.  Updated when gains set/saved in dialog or read from config"""

gain_override_dialog: 'ConfiguratorDialog'
"""Represents an instance of ConfiguratorDialog used for gain override configuration in the application."""

# Instance management
launched_instances : Dict[DeviceTypeLiteral, 'ChildPopen'] = {}
"""Dictionary mapping instance identifiers to their ChildPopen objects"""

instance_dev_dict : Dict[int, 'DeviceInfo'] = {}
"""Dictionary mapping instance PIDs to their DeviceInfo objects"""

master_instance : bool = False
"""Is current instance the master instance?"""

child_instance : bool = False
"""Is current instance a child instance?"""

ipc_instance : 'IPCNetworkThread'

# Button management
active_buttons: list[int] = []
master_buttons: list[int] = []
child_buttons: Dict[str, int] = {}

# System components
system_settings : 'SystemSettings'
telem_manager : 'TelemManager'
sim_listeners : 'SimListenerManager'

# Triggers and flags
force_reload_aircraft_trigger: bool = False

trimcal_hold_until: float = 0.0
"""While a trim calibration run owns the sim's elevator trim, trimwheel
instances must not write their wheel position (two absolute writers fight
frame-by-frame). The master broadcasts a refreshing hold over IPC; this is
the local time.perf_counter() deadline, so a dead master un-mutes the wheel
on its own within the TTL."""

# Exception tracking
exception_tracker : 'ExceptionTracker'  
"""Tracks logged exceptions for user notification and reporting"""

effects : 'Dispenser'  
"""Haptic effects dispenser, used to manage and access haptic effects by name"""