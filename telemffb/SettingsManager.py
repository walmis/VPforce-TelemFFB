import logging
import os
import shutil
from datetime import datetime
import telemffb.globals as G
from . import xmlutils


class SettingsManager:
    def __init__(self, datasource='', device='joystick', userconfig_path='', defaults_path='', system_settings={}):

        self.current_sim = "nothing"
        self.current_class = ""
        self.current_aircraft_name = ""
        self.current_pattern = ""
        self.device = device

        # offline state variables
        self._online_mode_backup = None
        self.offline_scope = None
        self.offline_mode = False
        self.offline_current_sim = None
        self.offline_current_class = None
        self.offline_current_pattern = None
        self.offline_current_aircraft_name = None

    def update_state_vars(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def set_sim(self, sim):
        self.current_sim = sim
    def go_offline(self):
        """
        Capture the current state of data before going into offline settings mode
        in case the sim is currently active.  Makes restoring operations easier when
        offline mode is exited
        """
        G.telem_manager.set_paused(True)
        self.offline_mode = True
        self._online_mode_backup = {
            key: getattr(self, key) for key in [
                'current_sim',
                'current_class',
                'current_aircraft_name',
                'current_pattern',
                'device'
            ]
        }

    def go_online(self):
        """Restores the previously saved state, if available."""
        G.telem_manager.set_paused(False)
        if self._online_mode_backup is not None:
            self.offline_mode = False
            self.update_state_vars(**self._online_mode_backup)
            self._online_mode_backup = None
            self.offline_scope = None

    def write_single_setting(self, setting_name, setting_value, unit='', device='', scope='MODEL'):
        if self.offline_mode:
            scope = self.offline_scope if self.offline_scope else "MODEL"

        # scope = self.offline_scope if self.offline_mode else "MODEL"
        xmlutils.write_to_xml(self.current_sim, self.current_class, self.current_pattern, setting_value, setting_name, scope=scope)

    def erase_single_setting(self, setting_name):
        xmlutils.erase_from_xml(self.current_sim, self.current_class, self.current_pattern, setting_name)

    def erase_model(self, sim, model, setting):
        pass

    def read_current_model(self):
        cls, pattern, results = xmlutils.read_single_model(self.current_sim, self.current_aircraft_name,
                                   self.current_class)
        return cls, pattern, results


