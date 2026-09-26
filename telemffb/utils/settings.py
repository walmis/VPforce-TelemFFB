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

import copy
import logging
import os
import re
import shutil
import xml.etree.ElementTree as ET
from typing import override

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QFileDialog

import telemffb.globals as G
import telemffb.xmlutils as xmlutils
from telemffb.namedmutex import FileLock
from telemffb.xml.store import consolidate_sort_and_write
from .filesystem import get_install_path, get_resource_path

__all__ = [
    "check_min_firmware_version",
    "read_all_system_settings",
    "SystemSettings",
    "convert_legacy_userconfig",
    "migrate_il2_korea_userconfig",
    "copy_legacy_config_to_new",
    "create_empty_userxml_file",
    "load_custom_userconfig",
    "get_legacy_override_file",
]

def check_min_firmware_version(dev_firmware_version, min_firmware_version):
    """Check if device firmware version meets minimum requirements."""
    minver = re.sub(r'\D', '', min_firmware_version)
    devver = re.sub(r'\D', '', dev_firmware_version)
    return devver >= minver


def read_all_system_settings():
    try:
        import winreg
    except ImportError:
        return {}  # no registry off Windows

    REG_PATH = r"SOFTWARE\VPForce\TelemFFB"

    settings_dict = {}

    try:
        registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)

        # Iterate through all values in the registry key
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(registry_key, index)
                settings_dict[name] = value
                index += 1
            except WindowsError as e:
                # Break when there are no more values
                if e.winerror == 259:  # ERROR_NO_MORE_ITEMS
                    break
    except WindowsError:
        # Handle errors (key not found, etc.)
        pass
    finally:
        try:
            winreg.CloseKey(registry_key)
        except UnboundLocalError:
            # Handle case where registry_key is not defined
            pass

    return settings_dict


class SystemSettings(QSettings):
    # Type hints for common settings to improve IDE autocompletion and discovery
    # These are intentionally class-level annotations (no runtime effect) so editors can
    # surface available settings via dot-completion (e.g. settings.logLevel)
    logLevel: str
    telemTimeout: int
    saveWindow: bool
    saveLastTab: bool
    enableVPConfStartup: bool
    pathVPConfStartup: str
    enableVPConfExit: bool
    enableVPConfGlobalDefault: bool
    pathVPConfExit: str
    enableResetGainsExit: bool

    pruneLogs: bool
    pruneLogsNum: int
    pruneLogsUnit: str
    ignoreUpdate: bool
    startToTray: bool
    closeToTray: bool
    enableDCS: bool
    pathDCS: str
    enableMSFS: bool
    enableXPLANE: bool
    validateXPLANE: bool
    pathXPLANE: str
    validateIL2: bool
    enableIL2K: bool
    pathIL2: str
    pathIL2_K: str
    portIL2: int
    portIL2_K: int
    enableBMS: bool
    pathBMS: str
    enableDirectInput: bool
    masterInstance: int
    autolaunchMaster: bool
    autolaunchJoystick: bool
    autolaunchPedals: bool
    autolaunchCollective: bool
    startMinJoystick: bool
    startMinPedals: bool
    startMinCollective: bool
    startMinTrimwheel: bool
    startHeadlessJoystick: bool
    startHeadlessPedals: bool
    startHeadlessCollective: bool
    startHeadlessTrimwheel: bool
    debug: bool

    default_inst = {
        'logLevel': 'INFO',
        'telemTimeout': 200,
        'saveWindow': True,
        'saveLastTab': True,
        'enableVPConfStartup': False,
        'pathVPConfStartup': '',
        'enableVPConfExit': False,
        'enableVPConfGlobalDefault': False,
        'pathVPConfExit': '',
        'enableResetGainsExit': False,
        'teleplotPort': '',
        'teleplotVars': ''
    }

    globl_sys_dict = {
        # On by default, one week: a log level left at DEBUG by accident fills
        # a disk quickly, and a user who never opens this setting should not
        # find out that way.  Only day-archives older than the window go.
        'pruneLogs': True,
        'pruneLogsNum': 1,
        'pruneLogsUnit': 'Week(s)',
        'ignoreUpdate': False,
        'startToTray': False,
        'closeToTray': False,
        'enableDCS': False,
        # Empty means "find it": every sim path here overrides discovery
        # rather than seeding it, so a default would be a wrong answer on
        # any machine that installed somewhere else.
        'pathDCS': '',
        'enableMSFS': False,
        'enableXPLANE': False,
        'validateXPLANE': False,
        'pathXPLANE': '',
        'enableIL2': False,
        'enableIL2K': False,
        'validateIL2': True,
        'focus_pauseIL2': True,
        'validateDCS': True,
        'pathIL2': 'C:/Program Files/IL-2 Sturmovik Great Battles',
        'pathIL2_K': '',
        'portIL2': 34385,
        # Korea gets its own port: the two games' telemetry is otherwise
        # indistinguishable, and the port is what tells the listeners apart
        'portIL2_K': 34386,
        'il2_fwd_enable': False,
        'il2_fwd_destinations': '[]',
        'enableBMS': False,
        'pathBMS': '',
        # opt-in per sim: the tap is a thing most VPforce owners never
        # need, and defaulting it on would imply otherwise
        'enableTapDCS': False,
        'enableTapIL2': False,
        'enableTapIL2_K': False,
        'enableTapBMS': False,
        'enableDirectInput': False,
        'masterInstance': 1,
        'autolaunchMaster': False,
        'autolaunchJoystick': False,
        'autolaunchPedals': False,
        'autolaunchCollective': False,
        'startMinJoystick': False,
        'startMinPedals': False,
        'startMinCollective': False,
        'startMinTrimwheel': False,
        # children default to headless: they exist to drive their device
        # and everything is configured from the master
        'startHeadlessJoystick': True,
        'startHeadlessPedals': True,
        'startHeadlessCollective': True,
        'startHeadlessTrimwheel': True,
        'debug': False,  # debug is False by default.  To permanently enable the debug menu, manually set debug = true (1) in registry
    }

    #: Roles by their masterInstance id, as the dialog's radio group numbers
    #: them.
    INSTANCE_ROLES = {1: 'joystick', 2: 'pedals', 3: 'collective',
                      4: 'trimwheel'}

    def migrate_instance_scoped_globals(self):
        """Clear instance-scoped copies of settings that are global.

        Some settings used to be written under `{role}/` even though they
        describe the installation rather than one device - ignoreUpdate is
        the one that mattered, since only the master ever checks for
        updates.  `get()` resolves instance-scoped first, so leaving those
        copies in place would let a stale value shadow the global one
        forever: the setting would appear not to stick, and the updater
        would keep reading the old answer.

        The instance copy is what the app has actually been honoring, so
        the master's copy is promoted before the rest are removed.  Runs on
        every start and does nothing once there is nothing left to move.

        Returns:
            list[str]: the keys that were migrated, for logging.
        """
        master = self.INSTANCE_ROLES.get(self.value('masterInstance'), 'joystick')
        moved = []
        for name in self.globl_sys_dict:
            scoped = [f"{role}/{name}" for role in self.INSTANCE_ROLES.values()
                      if self.value(f"{role}/{name}") is not None]
            if not scoped:
                continue
            authoritative = self.value(f"{master}/{name}")
            if authoritative is not None:
                super().setValue(name, authoritative)
            for key in scoped:
                self.remove(key)
            moved.append(name)
        return moved

    def migrate_il2_korea_enable(self):
        """Give IL-2 Korea its own enable switch, once.

        Korea used to run under the IL2 switch whenever its install path
        was set, so its own switch starts from exactly that and the first
        start that has it behaves like the last one without.  ``value()``
        rather than ``get()`` for the check: get() would answer with the
        default and store it, making the switch look already set.

        Returns:
            bool: True when the switch was set from the old rule.
        """
        if self.value('enableIL2K') is not None:
            return False
        super().setValue('enableIL2K', bool(self.get('enableIL2') and self.get('pathIL2_K')))
        return True

    @property
    def defaults(self):
        s = {}
        s.update(self.default_inst)
        s.update(self.globl_sys_dict)
        return s

    def __init__(self, pid=None, tp=None, path=None):
        """Open the settings store.

        `path` points the store at an ini file instead of the user's real
        settings.  Tests need it: QSettings.setDefaultFormat() does not
        redirect the two-argument constructor on Windows, so without an
        explicit path a test store *is* the live registry.
        """
        if path:
            super().__init__(path, QSettings.Format.IniFormat)
        else:
            super().__init__('VPforce', 'TelemFFB')
        #self.def_inst_sys_dict, self.def_global_sys_dict = get_default_sys_settings(pid, tp, cmb=False)
        # No additional initialization required. Keep QSettings initialization intact.
        return

    @override
    def setValue(self, key: str, value, instance=None) -> None:
        if instance:
            super().setValue(f"{instance}/{key}", value)
        else:
            super().setValue(key, value)

    def __getattr__(self, name: str):
        """Allow dot-access to settings, e.g. settings.someOption

        If the setting does not exist in QSettings, the default from
        SystemSettings.defaults (if any) or None is returned.
        """
        # Only handle attribute-style access for settings keys. Let Python
        # raise AttributeError for truly missing attributes.
        try:
            return self.get(name)
        except Exception:
            raise AttributeError(name)

    def __setattr__(self, name: str, value):
        """Assigning to attributes will persist the value to QSettings unless
        it's an internal attribute (starts with '_') or a real class attribute.
        """
        # Allow normal attribute behavior for internals and attributes that
        # already exist on the instance/class (to avoid interfering with QSettings internals)
        if name.startswith('_') or name in self.__dict__ or hasattr(type(self), name):
            object.__setattr__(self, name, value)
            return

        # Otherwise persist via QSettings
        try:
            # store as instance/global agnostic key; setValue will handle saving
            # under instance-specific key when appropriate elsewhere
            self.setValue(name, value)
        except Exception:
            # Fallback: if persistence fails, store as a regular attribute
            object.__setattr__(self, name, value)

    def __dir__(self):
        # Include known setting keys from defaults to improve autocompletion in REPLs and editors
        extra = []
        try:
            extra = list(self.defaults.keys())
        except Exception:
            extra = []
        return sorted(set(super().__dir__() + extra))

    def get(self, name, default=None, instance=None):
        """Resolve a setting, instance-scoped first then global.

        `instance` names the device whose value to read, defaulting to this
        process's own.  The master instance passes it explicitly so it can
        read and write every instance's settings from one dialog, rather
        than each child having to configure itself.
        """
        instance = instance or G.device_type
        # check instance params
        val = self.value(f"{instance}/{name}")
        if val is None:
            # check global param
            val = self.value(name)

        if val is None:
            val = self.defaults.get(name, None)
            if val is not None:
                if name in self.default_inst:
                    self.setValue(f"{instance}/{name}", val) # save instance variable
                else:
                    self.setValue(name, val)
                return val
            #logging.warn(f"SystemSettings: not found {name} default={repr(default)} val={repr(val)}")
            return default

        if val == "true": 
            val = 1
        elif val == "false":
            val = 0
        try:
            val = int(val)
        except Exception: pass

        return val


def convert_legacy_userconfig(path):
    """
    Upgrades a legacy userconfig file to the new format that includes profile tags and profileMappings.

    This function performs the following:
    - Identifies "User Default" entries based on 'type' settings.
    - Identifies modified aircraft (with settings but no type) as "Auto User" entries.
    - Appends appropriate <profile> tags to all models.
    - Creates <models> entries for missing "Auto User" profiles.
    - Adds <profileMappings> entries for all aircraft that were converted.
    - Skips execution if conversion has already been applied.

    Returns:
        bool: True if conversion was applied, False otherwise.
    """
    tree = xmlutils.try_parse(path)
    if tree is None:
        logging.exception(f"Failed to parse: {path}")
        return False

    root = tree.getroot()

    # Convert legacy root tag
    new_root = ET.Element("TelemFFB_v2")
    for child in list(root):
        new_root.append(child)
    tree._setroot(new_root)
    root = new_root

    # If already converted (profileMappings exist), exit
    if root.find("profileMappings") is not None:
        logging.info('Userconfig is already v2, conversion not needed')
        return False

    # check if there are profile entries, if so this is a new config that has had profiles added but nothing configured as active profile
    p = root.findall('models[name="profile"]')
    if p:
        logging.info('Found profile entries in userconfig is already v2, conversion not needed')
        return False

    # Check if there's anything to convert
    if root.find("models") is None:
        return False

    """
    # Find all user created models ('type' entries) - these models will become 'User Default'
    """
    user_default_list = []
    user_default_models = root.findall('models[name="type"]')
    for user_default in user_default_models:
        model = user_default.findtext('model')
        sim = user_default.findtext('sim')
        if model and sim:
            user_default_list.append((model, sim))

    """
    find all model/sim pairings that exist but don't have a 'type' parent entry.  These will become 'Auto User' profiles
    """
    user_settings_only_list = []
    for entry in root.findall('models'):
        model = entry.findtext('model')
        sim = entry.findtext('sim')
        if not model or not sim:
            continue
        if (model, sim) not in user_default_list and (model, sim) not in user_settings_only_list:
            user_settings_only_list.append((model, sim))

    """
    We now have two lists:
    user_default_models has (model, sim) tuples for aircraft that will become 'user defaults' (created by user)
    user_settings_only_list has (model, sim) tuples for aircraft that will become 'auto user' profiles (settings modified from default aircraft)
    """
    for entry in root.findall('models'):
        model = entry.findtext('model')
        sim = entry.findtext('sim')
        if not model or not sim:
            continue

        existing_profiles = entry.findall("profile")
        if any(p.text in ("User Default", "Auto User") for p in existing_profiles):
            continue  # Already patched

        if (model, sim) in user_default_list:
            ET.SubElement(entry, 'profile').text = "User Default"
        elif (model, sim) in user_settings_only_list:
            ET.SubElement(entry, 'profile').text = "Auto User"
        else:
            logging.warning(f"Unhandled model/sim: {model}/{sim}")

    """
    Each 'Auto User' profile gets a name='profile' entry that indicates a profile.
    User Defaults have their name='type' entries.
    """
    existing_profile_defs = {(e.findtext("model"), e.findtext("sim"))
                             for e in root.findall('models[name="profile"]')}

    for model, sim in user_settings_only_list:
        if (model, sim) in existing_profile_defs:
            continue
        cls = xmlutils.get_class_for_sim_model(sim, model)
        profile_def = ET.SubElement(root, 'models')
        ET.SubElement(profile_def, 'name').text = "profile"
        ET.SubElement(profile_def, 'model').text = model
        ET.SubElement(profile_def, 'value').text = cls
        ET.SubElement(profile_def, 'sim').text = sim
        ET.SubElement(profile_def, 'device').text = "any"
        ET.SubElement(profile_def, 'profile').text = "Auto User"

    """
    Now we build the profileMappings table.
    Each model will be set to its "profile" value (Auto User or User Default) depending on its type
    """
    seen_mappings = set()
    for model, sim in user_default_list + user_settings_only_list:
        if not model or not sim or (model, sim) in seen_mappings:
            continue
        seen_mappings.add((model, sim))

        cls = xmlutils.get_class_for_sim_model(sim, model)
        profile = "User Default" if (model, sim) in user_default_list else "Auto User"

        mapping = ET.SubElement(root, "profileMappings")
        ET.SubElement(mapping, "model").text = model
        ET.SubElement(mapping, "sim").text = sim
        ET.SubElement(mapping, "cls").text = cls
        ET.SubElement(mapping, "active_profile").text = profile

    xmlutils.consolidate_sort_and_write_userconfig(tree)
    logging.info("Conversion complete: userconfig upgraded to v2.")
    return True

def copy_legacy_config_to_new(path):
    # path is the destination for the new v2 config file
    if os.path.exists(path):
        # new config already exists, don't copy
        return

    # get root of path
    user_rootpath = os.path.dirname(path)
    legacy_config_path = os.path.join(user_rootpath, "userconfig.xml")
    if not os.path.exists(legacy_config_path):
        # there is no legacy config to copy over
        return
    shutil.copy(legacy_config_path, path)
    logging.info(f"Copied legacy userconfig.xml to {path}")


def create_empty_userxml_file(path):
    if not os.path.isfile(path):
        # Create an empty XML file with the specified root element
        root = ET.Element("TelemFFB_v2")
        tree = ET.ElementTree(root)
        # Create a backup directory if it doesn't exist
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        tree.write(path)
        logging.info(f"Empty XML file created at {path}")
    else:
        logging.info(f"XML file exists at {path}")


def load_custom_userconfig(new_path=""):
    print(f"newpath=>{new_path}<")

    if new_path == "":
        options = QFileDialog.Option(0)
        options |= QFileDialog.Option.DontUseNativeDialog  # Optional: makes dialog consistent across platforms

        file_path, _ = QFileDialog.getOpenFileName(
            None,
            "Select File",
            "",
            "All Files (*)",
            options=options
        )

        if file_path == "":
            return

        G.userconfig_rootpath = os.path.basename(file_path)
        G.userconfig_path = file_path
    else:
        G.userconfig_rootpath = os.path.basename(new_path)
        G.userconfig_path = new_path

    xmlutils.update_vars(
        G.device_type,
        _userconfig_path=G.userconfig_path,
        _defaults_path=G.defaults_path,
    )

    # G.settings_mgr.init_ui()

    logging.info(f"Custom Configuration was loaded via debug menu: {G.userconfig_path}")

    if G.master_instance and G.launched_instances:
        G.ipc_instance.send_broadcast_message(f"LOADCONFIG:{G.userconfig_path}")


def get_legacy_override_file():
    _legacy_override_file = None

    if G.args.overridefile == 'None':
        _install_path = get_install_path()

        # Need to determine if user is using default config.user.ini without passing the override flag:
        if os.path.isfile(os.path.join(_install_path, 'config.user.ini')):
            _legacy_override_file = os.path.join(_install_path, 'config.user.ini')

    else:
        if not os.path.isabs(G.args.overridefile):  # user passed just file name, construct absolute path from script/exe directory
            ovd_path = get_resource_path(G.args.overridefile, prefer_root=True, force=True)
        else:
            ovd_path = G.args.overridefile  # user passed absolute path, use that

        if os.path.isfile(ovd_path):
            _legacy_override_file = ovd_path
        else:
            _legacy_override_file = ovd_path
            logging.warning(f"Override file {G.args.overridefile} passed with -o argument, but can not find the file for auto-conversion")

    return _legacy_override_file


def _korea_model_patterns(defaults_path) -> set:
    """The model patterns defaults.xml files under the IL2K sim."""
    tree = xmlutils.try_parse(defaults_path)
    if tree is None:
        return set()
    return {e.findtext("model") for e in tree.getroot().findall('models[sim="IL2K"][name="type"]')
            if e.findtext("model")}


def migrate_il2_korea_userconfig(userconfig_path, defaults_path) -> bool:
    """Copy a user's IL-2 Korea settings to the IL2K sim, once.

    User rows are keyed by sim and never fall back to another sim, so when
    Korea became its own sim its aircraft would have loaded with shipped
    defaults while the user's rows sat under IL2.  Rows are COPIED, never
    moved: the IL2 rows stay for a release build that knows only IL2, and
    from then on each version keeps its own.

    - <models> and <profileMappings> rows for the Korea aircraft patterns
      (those defaults.xml lists under IL2K) get an IL2K twin.
    - <simSettings> and <classSettings> rows under IL2 are copied too, but
      only alongside aircraft rows: they applied to both games before the
      split, and the aircraft rows are the evidence that Korea was flown.

    It runs only while the file has no IL2K rows at all - the first start
    of a build that knows the new sim.  After that each sim's rows are
    its own, so nothing edited under IL2 later reaches IL2K.  No marker is
    kept, and a file with nothing to copy is not rewritten.  A backup is
    written beside the file the first time it is changed.

    Returns True when rows were copied.
    """
    if not os.path.isfile(userconfig_path):
        return False
    tree = xmlutils.try_parse(userconfig_path)
    if tree is None:
        logging.error(f"IL-2 Korea migration: failed to parse {userconfig_path}")
        return False
    root = tree.getroot()
    if any(e.findtext("sim") == "IL2K" for e in root):
        return False
    korea = _korea_model_patterns(defaults_path)

    def copy_rows(tag, wanted) -> int:
        rows = [e for e in root.findall(tag) if e.findtext("sim") == "IL2" and wanted(e)]
        for e in rows:
            twin = copy.deepcopy(e)
            twin.find("sim").text = "IL2K"
            root.append(twin)
        return len(rows)

    def korea_aircraft(e) -> bool:
        return e.findtext("model") in korea

    aircraft = copy_rows("models", korea_aircraft) + copy_rows("profileMappings", korea_aircraft)
    if not aircraft:
        return False
    shared = copy_rows("simSettings", lambda e: True) + copy_rows("classSettings", lambda e: True)

    backup = os.path.join(os.path.dirname(userconfig_path), "userconfig_v2_pre-il2k_backup.xml")
    if not os.path.exists(backup):
        shutil.copy2(userconfig_path, backup)
    with FileLock(userconfig_path):
        consolidate_sort_and_write(tree, userconfig_path)
    logging.info(f"IL-2 Korea migration: {aircraft} aircraft rows and {shared} sim/class rows "
                 f"copied to IL2K")
    return True
