settings_mgr = None
userconfig_rootpath = None
userconfig_path = None
defaults_path = None
defaults_path = None

# main window instance
main_window = None

_device_type = None
_device_pid = None

_launched_joystick = False
_launched_pedals = False
_launched_collective = False
_launched_children = False
_child_ipc_ports = []
_master_instance = False
_ipc_running = False
_ipc_thread = None
_child_instance = None

# systems settings
system_settings = None

#parsed startup arguments
args = None

# telemetry manager instance
telem_manager = None

# configurator gains read at startup
startup_configurator_gains = None

# function reference to stop/init sims
stop_sims = None
init_sims = None