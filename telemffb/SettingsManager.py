import logging
import os
import shutil
from datetime import datetime
from enum import Enum, auto

from PyQt6.QtCore import QObject, pyqtSignal

import telemffb.globals as G
from . import xmlutils

class SpringModeEnum(Enum):
    NONE = auto()
    TELEM = auto()
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
    DINPUT_TAP = auto()

class GEffectModeEnum(Enum):
    DISABLED = auto()
    LEGACY = auto()
    NEW = auto()
    ADVANCED = auto()
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
        # Set when the pattern naming the loaded aircraft differs from
        # the one recorded at its last load; the main window offers to
        # copy the old profile's rows across and then clears it.
        self.profile_change = None
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

    #: Sim key -> the name shown wherever a user picks a sim.  Keys stay
    #: the value everywhere else (config rows, telemetry src, IPC).
    SIM_LABELS = {
        "DCS": "DCS World",
        "BMS": "Falcon BMS",
        "MSFS": "MSFS 20/24",
        "XPLANE": "X-Plane 11/12",
        "IL2": "IL-2 Sturmovik",
        "IL2K": "IL-2 Korea",
    }

    @classmethod
    def sim_label(cls, sim: str) -> str:
        return cls.SIM_LABELS.get(sim, sim)

    #: Settings-tab sim -> the DirectInput Tap sims whose enable toggle
    #: offers the DINPUT_TAP spring mode there.  IL-2 Great Battles and
    #: Korea are separate sims (their listeners tag frames IL2 / IL2K),
    #: each offered by its own tap toggle.
    TAP_SIM_KEYS = {
        'DCS': ('DCS',),
        'IL2': ('IL2',),
        'IL2K': ('IL2_K',),
        'BMS': ('BMS',),
    }

    def resolve_enum_list(self, name, current_value=''):
        """The enum label dict for a ``validvalues`` collection name, with
        options that do not apply right now removed.

        Today that is one rule: 'Game Managed (DirectInput Tap)' is only
        offered for a sim whose DirectInput Tap toggle is on in System
        Settings (see TAP_SIM_KEYS) - the mode cannot work without the
        tap wrapper, so listing it everywhere just invites a dead pick.
        A row whose CURRENT value is DINPUT_TAP keeps the entry, however
        it got there: an existing selection must display and be
        deselectable normally, never fall into the invalid-value branch.
        """
        label_dict = getattr(self, name, None)
        if not isinstance(label_dict, dict):
            return label_dict
        if (SpringModeEnum.DINPUT_TAP in label_dict
                and current_value != SpringModeEnum.DINPUT_TAP.name
                and not self.tap_mode_offered()):
            label_dict = {k: v for k, v in label_dict.items()
                          if k is not SpringModeEnum.DINPUT_TAP}
        return label_dict

    def tap_mode_offered(self):
        """Whether the tap captures in the current sim (see TAP_SIM_KEYS):
        the gate for the tap spring mode and for the tap settings groups."""
        try:
            from telemffb.tap.tap_install import SIMS_BY_KEY
            from telemffb.tap.tap_reconcile import tap_captures
            return any(
                tap_captures(SIMS_BY_KEY[key], G.system_settings)
                for key in self.TAP_SIM_KEYS.get(self.current_sim, ()))
        except Exception:
            # settings display must never break on tap plumbing; offer the
            # mode rather than hide a configured one
            logging.exception("tap-mode availability check failed")
            return True

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
            if not model:
                # An aircraft nothing names has no profile to hold a change.
                # The form is read-only in that state; this is the backstop,
                # since an exception here would escape a Qt slot.
                logging.warning(f"No profile names the loaded aircraft; not writing {setting}")
                return
            xmlutils.write_models_to_xml(sim, model, value, setting, unit=unit, the_device=the_device, profile_name=self.active_profile)
            return
        else:
            match self.offline_scope:
                case 'SIM':
                    xmlutils.write_sim_to_xml(sim, value, setting, unit=unit, the_device=the_device)
                case 'CLASS':
                    xmlutils.write_class_to_xml(sim, class_name, value, setting, unit=unit, the_device=the_device)
                case 'MODEL':
                    if (G.master_instance and self.active_profile == 'Auto User'
                            and 'Auto User' not in xmlutils.get_available_profiles(sim, class_name, model)):
                        # A profile created by an edit is the one the aircraft should use,
                        # as it is when a live edit forks Built-In.
                        xmlutils.add_new_profile(sim, class_name, model, profile_name='Auto User')
                        xmlutils.update_active_profile_entry(sim, class_name, model, 'Auto User')
                    xmlutils.write_models_to_xml(sim, model, value, setting, unit=unit, the_device=the_device,profile_name=self.active_profile)
                case _:
                    pass

    def erase_from_xml(self, sim, class_name, model, setting, the_device=''):
        if not self.offline_mode:
            if not model:
                logging.warning(f"No profile names the loaded aircraft; nothing to erase for {setting}")
                return
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

    MSFS_XP_FT_ONLY_PEDAL_SPRING_MODE = {
        SpringModeEnum.FORCETRIM: "Force Trim",
    }

    # IL-2 Great Battles renders no pedal FFB and publishes no ffbdevice
    # records, so its lists carry neither the pedal tap nor the
    # FFB-telemetry mode; both belong to Korea's lists below.
    IL2_PEDAL_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.STATIC: "Static Spring",
        SpringModeEnum.DYNAMIC: "Dynamic Spring",
        SpringModeEnum.CUSTOM: "Dynamic with Custom Speeds",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }

    IL2_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.DINPUT_TAP: "Game Managed (DirectInput Tap)",
        SpringModeEnum.CUSTOM: "Static Override w/ Hardware Trim",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }

    IL2K_PEDAL_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.TELEM: "FFB Telemetry (Game Managed)",
        SpringModeEnum.DINPUT_TAP: "Game Managed (DirectInput Tap)",
        SpringModeEnum.STATIC: "Static Spring",
        SpringModeEnum.DYNAMIC: "Dynamic Spring",
        SpringModeEnum.CUSTOM: "Dynamic with Custom Speeds",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }

    IL2K_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.TELEM: "FFB Telemetry (Game Managed)",
        SpringModeEnum.DINPUT_TAP: "Game Managed (DirectInput Tap)",
        SpringModeEnum.CUSTOM: "Static Override w/ Hardware Trim",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
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
        SpringModeEnum.DINPUT_TAP: "Game Managed (DirectInput Tap)",
        SpringModeEnum.CUSTOM: "Static Override w/ Hardware Trim",
        SpringModeEnum.ADVANCED: "Advanced Dynamic"
    }

    DCS_HELI_JOYSTICK_SPRING_MODE = {
        SpringModeEnum.NONE: "None (Game Managed)",
        SpringModeEnum.DINPUT_TAP: "Game Managed (DirectInput Tap)",
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
