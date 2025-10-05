import logging
import os
import shutil
from datetime import datetime
from enum import Enum, auto

from PyQt6.QtCore import QObject, pyqtSignal

import telemffb.globals as G
from . import xmlutils


class SettingsManager(QObject):
    activeProfileChanged = pyqtSignal(str)
    aircraftChanged = pyqtSignal(str)
    default_write_attempted = pyqtSignal()

    def __init__(self, datasource='', device='joystick', userconfig_path='', defaults_path='', system_settings={}):
        super().__init__()  # Required for QObject to function

        self.timed_out = True
        self.current_sim = "nothing"
        self.current_class = ""
        self.current_aircraft_name = ""
        self.current_pattern = ""
        self.active_profile = None
        self.device = device

        # offline state variables
        self._online_mode_backup = None
        self.offline_scope = None
        self.offline_mode = False

    def update_state_vars(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def set_sim(self, sim):
        self.current_sim = sim

    def get_state_vars(self):
        return {
            'current_sim': self.current_sim,
            'current_class': self.current_class,
            'current_aircraft_name': self.current_aircraft_name,
            'current_pattern': self.current_pattern,
            'active_profile': self.active_profile,
            'device': self.device
        }

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
                'active_profile',
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

    def read_setting_from_xml(self, setting, sim, class_name = '', model = '', the_device = '', active_profile=None):

        _, _, result = xmlutils.read_single_model(the_sim=sim, aircraft_name=model, input_modeltype = class_name, instance_device=the_device, active_profile=self.active_profile)
        value = None
        if result:
            for item in result:
                if item['name'] == setting:
                    value = item.get('value', None)

        return value

    def write_to_xml(self, sim, class_name, model, value, setting, unit='', the_device='', scope='MODEL'):
        if not self.offline_mode:

            xmlutils.write_models_to_xml(sim, model, value, setting, unit=unit, the_device=the_device, profile_name=self.active_profile)

            return
        else:
            match self.offline_scope:
                case 'SIM':
                    xmlutils.write_sim_to_xml(sim, value, setting, unit=unit, the_device=the_device)
                case 'CLASS':
                    xmlutils.write_class_to_xml(sim, class_name, value, setting, unit=unit, the_device=the_device)
                case 'MODEL':
                    if G.master_instance and self.active_profile == 'Auto User':
                        xmlutils.add_new_profile(sim, class_name, model, profile_name=self.active_profile)
                    xmlutils.write_models_to_xml(sim, model, value, setting, unit=unit, the_device=the_device,profile_name=self.active_profile)
                case _:
                    pass

    def erase_from_xml(self, sim, class_name, model, setting, the_device=''):
        if not self.offline_mode:
            xmlutils.erase_models_from_xml(sim, model, setting, the_device=the_device, profile_name=self.active_profile)
            return
        else:
            match self.offline_scope:
                case 'SIM':
                    xmlutils.erase_sim_from_xml(sim, setting, the_device=the_device)
                case 'CLASS':
                    xmlutils.erase_class_from_xml(sim, class_name, setting, the_device=the_device)
                case 'MODEL':
                    xmlutils.erase_models_from_xml(sim, model, setting, the_device=the_device, profile_name=self.active_profile)
                case _:
                    pass


    class SpringModeEnum(Enum):
        NONE = auto()
        BASIC = auto()
        CENTER = auto()
        CNTR_FT = auto()
        ADVANCED = auto()
        FBW = auto()
        DEFAULT = auto()
        NOSPRING = auto()
        STATIC = auto()
        DYNAMIC = auto()
        CUSTOM = auto()
        FORCETRIM = auto()

    # used for both joystick and pedals
    MSFS_XP_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.BASIC: "Basic Dynamic",
        SpringModeEnum.CENTER: "Basic Dynamic with Spring Centering",
        SpringModeEnum.FBW: "FlyByWire (FBW)",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }

    MSFS_XP_PEDAL_SPRING_MODE = {
        SpringModeEnum.BASIC: "Basic Dynamic",
        SpringModeEnum.CENTER: "Basic Dynamic with Spring Centering",
        SpringModeEnum.FBW: "FlyByWire (FBW)",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }

    MSFS_XP_GILDER_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.BASIC: "Basic Dynamic",
        SpringModeEnum.CENTER: "Basic Dynamic with Spring Centering",
        SpringModeEnum.CNTR_FT: "Basic Dynamic with Spring Centering + Force Trim",
        SpringModeEnum.FBW: "FlyByWire (FBW)",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }

    MSFS_XP_HELI_PEDAL_SPRING_MODE = {
        SpringModeEnum.NOSPRING: "No Spring",
        SpringModeEnum.FORCETRIM: "Hardware Force Trim",
    }

    MSFS_XP_HELI_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.NOSPRING: "No Spring",
        SpringModeEnum.FORCETRIM: "Hardware Force Trim",
    }

    MSFS_XP_HELI_COLLECTIVE_SPRING_MODE = {
        SpringModeEnum.NOSPRING: "No Spring",
        SpringModeEnum.FORCETRIM: "Hardware Force Trim",
    }

    MSFS_XP_FT_ONLY_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.FORCETRIM: "Force Trim",
    }

    MSFS_XP_FT_ONLY_COLLECTIVE_SPRING_MODE = {
        SpringModeEnum.FORCETRIM: "Force Trim",
    }

    DCS_IL2_PEDAL_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.NOSPRING: "No Spring (Free Floating)",
        SpringModeEnum.STATIC: "Static Spring",
        SpringModeEnum.DYNAMIC: "Dynamic Spring",
        SpringModeEnum.CUSTOM: "Dynamic with Custom Speeds",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }
    DCS_IL2_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.CUSTOM: "Static Override w/ Hardware Trim",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }

    DCS_HELI_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.CUSTOM: "Static Override w/ Hardware Trim"
    }

    DCS_HELI_PEDAL_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.NOSPRING: "No Spring (Free Floating)",
        SpringModeEnum.STATIC: "Static Spring",
        SpringModeEnum.FORCETRIM: "Hardware Force Trim",
    }

    DCS_HELI_COLLECTIVE_SPRING_MODE = {
        SpringModeEnum.NOSPRING: "No Spring",
        SpringModeEnum.FORCETRIM: "Hardware Force Trim",
    }

    class GEffectModeEnum(Enum):
        DISABLED = auto()
        LEGACY = auto()
        NEW = auto()
        ADVANCED = auto()

    MSFS_XP_G_EFFECT_MODE = {
        GEffectModeEnum.DISABLED: "Disabled",
        GEffectModeEnum.NEW: "Linear + Deflection Based",
        GEffectModeEnum.ADVANCED: "Custom Curve"
    }

    DCS_IL2_G_EFFECT_MODE = {
        GEffectModeEnum.DISABLED: "Disabled",
        GEffectModeEnum.LEGACY: "Exponential Curve",
        GEffectModeEnum.NEW: "Linear + Deflection Based",
        GEffectModeEnum.ADVANCED: "Custom Curve"
    }
