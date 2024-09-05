from typing import TYPE_CHECKING, Dict
if TYPE_CHECKING:
    from PyQt5.QtCore import QSettings
    from .LogWindow import LogWindow
    from .IPCNetworkThread import IPCNetworkThread
    from .utils import SystemSettings, ChildPopen
    from .settingsmanager import SettingsWindow
    from .telem.TelemManager import TelemManager
    from .telem.SimTelemListener import SimListenerManager
    from telemffb.MainWindow import MainWindow
    from subprocess import Popen
    from telemffb.CmdLineArgs import CmdLineArgs

is_exe: bool = False

settings_mgr : 'SettingsWindow' = None
userconfig_rootpath = None
userconfig_path = None
defaults_path = None
defaults_path = None
current_vpconf_profile = None

# main window instance
main_window : 'MainWindow' = None

device_type : str = None
device_usbpid : str = None
device_usbvidpid : str = None # "FFFF:2055"

launched_instances : Dict[str, 'ChildPopen'] = {}
master_instance : bool = False
ipc_instance : 'IPCNetworkThread' = None
child_instance : bool = None
active_buttons: list = []
master_buttons: list = []
child_buttons: dict = {}

force_reload_aircraft_trigger: bool = False

current_device_config_scope: str = None # add current device config scope to globals for tracking across telemffb modules

# systems settings
system_settings : 'SystemSettings' = None

#parsed startup arguments
args : 'CmdLineArgs' = None

# telemetry manager instance
telem_manager : 'TelemManager' = None

# configurator gains read at startup
startup_configurator_gains = None  # Gain object direct from 'device.get_gains'.  Gains get read at TelemFFB startup fallback baseline values.
vpconf_configurator_gains = None  # Gain object direct from 'device.get_gains'. Updated every time a configurator profile is pushed to the device to use as revert data
current_configurator_gains = None  # Gain settings table set by gain override dialog.  Updated when gains set/saved in dialog or read from config
gain_override_dialog = None

sim_listeners : 'SimListenerManager' = None

log_window : 'LogWindow' = None

release_version : bool = False
