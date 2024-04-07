from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from PyQt5.QtCore import QSettings
    from .LogWindow import LogWindow
    from .IPCNetworkThread import IPCNetworkThread
    from .utils import SystemSettings
    from .settingsmanager import SettingsWindow
    from .telem.TelemManager import TelemManager

settings_mgr : 'SettingsWindow' = None
userconfig_rootpath = None
userconfig_path = None
defaults_path = None
defaults_path = None

# main window instance
main_window = None

_device_type : str = None
_device_pid : str = None
_device_vid_pid : str = None # "FFFF:2055"

_launched_joystick = False
_launched_pedals = False
_launched_collective = False
_launched_children = False
_child_ipc_ports = []
_master_instance = False
_ipc_running = False
_ipc_thread : 'IPCNetworkThread' = None
_child_instance = None
is_master_instance = None

# systems settings
system_settings : 'SystemSettings' = None

#parsed startup arguments
args = None

# telemetry manager instance
telem_manager : 'TelemManager' = None

# configurator gains read at startup
startup_configurator_gains = None

# function reference to stop/init sims
stop_sims = None
init_sims = None

log_window : 'LogWindow' = None

qsettings : 'QSettings' = None