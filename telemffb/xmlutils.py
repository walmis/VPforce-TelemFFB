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
"""Legacy xmlutils module — backward-compatible shim over :mod:`telemffb.xml`.

Provides the original module-level global-state API while delegating all
work to the new modular package. Existing code using
``import telemffb.xmlutils as xu`` continues to work unchanged.

Module-level globals are synced automatically with the internal manager::

    import telemffb.xmlutils as xu

    xu.update_vars('joystick', userconfig_path, defaults_path)
    xu.update_roots()
    model_class, pattern, settings = xu.read_single_model('MSFS', 'Cessna 172')
    root = xu.auto_user_root  # direct tree access still works

.. note::
   The global sync is **one-way** (store → module globals).  All
   read/write operations go exclusively through the internal
   ``XmlStore``/``ConfigResolver``/``ConfigWriter`` triple, so assigning
   to ``auto_user_root``/``auto_user_tree``/``auto_defaults_root`` does
   NOT change behaviour — it only changes what subsequent code reads
   from those names directly.  Use :func:`update_roots` to refresh the
   trees, or operate on ``telemffb.xml.XmlConfigManager`` instances for
   fully encapsulated access.  (This differs from the legacy module,
   where the functions read the module globals, so monkeypatching
   ``xu.auto_user_root`` to inject a fixture tree no longer takes
   effect — inject via ``XmlStore`` instead.)
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Optional

from telemffb.xml.store import XmlStore, try_parse as _store_try_parse
from telemffb.xml.read import ConfigResolver
from telemffb.xml.write import ConfigWriter
from telemffb.xml.merge import (
    update_default_data_with_craft_result as _merge_update_default_data_with_craft_result,
    update_data_with_models as _merge_update_data_with_models,
    update_sc_overrides_with_user as _merge_update_sc_overrides_with_user,
    remove_dicts_by_names as _merge_remove_dicts_by_names,
    check_prereq_value as _merge_check_prereq_value,
    eliminate_no_prereq as _merge_eliminate_no_prereq,
    filter_rows as _merge_filter_rows,
)

if TYPE_CHECKING:
    from telemffb.xml.types import (
        ClassDataRow,
        DefaultDataRow,
        ModelDataRow,
        PrereqRow,
        ScOverrideRow,
    )

# ── Module-level globals (backward compat) ────────────────────────
#: Enable coloured debug output from :func:`dbprint`
print_debugs: bool = False

#: Enable method-call tracing via :func:`mprint`
print_method_calls: bool = False

#: Active device type (e.g. ``'joystick'``, ``'pedals'``, ``'collective'``, ``'trimwheel'``)
device: str = ''

#: Path to user configuration XML file (``userconfig_v2.xml``)
userconfig_path: str = ''

#: Path to default settings XML file (``defaults.xml``)
defaults_path: str = ''

#: Parsed root element of userconfig.  One-way sync target: refreshed by
#: :func:`update_roots` (store → globals).  Assigning to it does NOT
#: affect any read/write operation — they all use the internal store.
auto_user_root: Optional[ET.Element] = None

#: Parsed ElementTree of userconfig.  One-way sync target (see
#: :data:`auto_user_root`).
auto_user_tree: Optional[ET.ElementTree] = None

#: Parsed root element of defaults.  One-way sync target (see
#: :data:`auto_user_root`).
auto_defaults_root: Optional[ET.Element] = None

# ── Singleton manager ──────────────────────────────────────────────
# NOTE: Not thread-safe. xmlutils is only called from the main thread in
# TelemFFB, so this is safe in practice. Do not call update_vars/_get_mgr
# concurrently from multiple threads.
_mgr: Optional[tuple[XmlStore, ConfigResolver, ConfigWriter]] = None


def _setting_is_hidden(name: str) -> bool:
    """Whether an application policy says this setting does not apply.

    Supplied to ConfigResolver so the XML layer never reaches up for it
    (see its ``hidden`` parameter).  Today the only such setting is the
    per-aircraft device selection, which means nothing until the
    joystick role holds more than one device; a stored preference goes
    inert, not lost - it resurfaces when a second device is configured
    again.  The import is local because telemffb.utils imports this
    module: the cycle is real, and this is the layer that should carry
    it rather than the parser.
    """
    if name in ('device_group', 'joystick_device'):
        from telemffb.utils import multiple_joystick_devices
        return not multiple_joystick_devices()
    return False


def _get_mgr() -> tuple[XmlStore, ConfigResolver, ConfigWriter]:
    """Return the singleton (store, resolver, writer), recreating when paths change."""
    global _mgr
    if (_mgr is None or
            _mgr[0].device != device or
            _mgr[0].userconfig_path != userconfig_path or
            _mgr[0].defaults_path != defaults_path):
        store = XmlStore(device, userconfig_path, defaults_path)
        if _mgr is not None:
            # Legacy semantics: update_vars() never invalidated the parsed
            # trees; they persist until the next update_roots().  The
            # master's device-scope switch relies on this - it changes the
            # device and reads immediately, and a fresh empty store made
            # every read return nothing (the aircraft came back unknown
            # and the new-aircraft wizard fired).
            store.adopt_trees_from(_mgr[0])
        resolver = ConfigResolver(store, hidden=_setting_is_hidden)
        writer = ConfigWriter(store, resolver)
        _mgr = (store, resolver, writer)
    return _mgr


def _store() -> XmlStore:
    return _get_mgr()[0]


def _resolver() -> ConfigResolver:
    return _get_mgr()[1]


def _writer() -> ConfigWriter:
    return _get_mgr()[2]


# ── Core functions ─────────────────────────────────────────────────

def update_vars(_device: str, _userconfig_path: str, _defaults_path: str) -> None:
    """Set active device type and XML file paths.

    Must be called before any read/write operations.

    Args:
        _device: Device type identifier
        _userconfig_path: Path to user configuration XML
        _defaults_path: Path to default settings XML
    """
    global device, userconfig_path, defaults_path  # noqa: PLW0603
    device = _device
    userconfig_path = _userconfig_path
    defaults_path = _defaults_path


def update_roots() -> None:
    """Re-parse both XML files into in-memory trees.

    Updates :data:`auto_user_root`, :data:`auto_user_tree`, and
    :data:`auto_defaults_root` for direct tree access.
    """
    global auto_user_root, auto_user_tree, auto_defaults_root  # noqa: PLW0603
    store = _store()
    store.update_roots()
    auto_user_root = store.auto_user_root
    auto_user_tree = store.auto_user_tree
    auto_defaults_root = store.auto_defaults_root


def try_parse(file_path: str, max_attempts: int = 3, delay: float = 0.1) -> Optional[ET.ElementTree]:
    """Parse an XML file with automatic retry on parse errors.

    Args:
        file_path: Path to the XML file
        max_attempts: Maximum parse attempts before giving up
        delay: Seconds between retry attempts

    Returns:
        Parsed ElementTree, or ``None`` if all attempts fail
    """
    return _store_try_parse(file_path, max_attempts, delay)


def write_userconfig_xml(tree: Optional[ET.ElementTree] = None) -> None:
    """Write userconfig after consolidation and deduplication.

    Args:
        tree: ElementTree to write. Uses current parsed tree if ``None``.
    """
    _writer()._store.write_userconfig(tree)


def really_write_userconfig_xml(tree: Optional[ET.ElementTree] = None) -> None:
    """Write userconfig directly without consolidation.

    Args:
        tree: ElementTree to write. Uses current parsed tree if ``None``.
    """
    _writer()._store.really_write_userconfig(tree)


def consolidate_sort_and_write_userconfig(tree: Optional[ET.ElementTree] = None, ret: bool = False) -> Optional[ET.ElementTree]:
    """Deduplicate and sort userconfig, optionally returning without writing.

    Args:
        tree: ElementTree to process. Uses current parsed tree if ``None``.
        ret: If ``True``, return the processed tree without writing to disk

    Returns:
        The consolidated ElementTree if ``ret=True``, otherwise ``None``
    """
    store = _writer()._store
    if ret:
        return store.consolidated_tree(tree)
    store.write_userconfig(tree)
    return None


# ── Read functions ─────────────────────────────────────────────────

def read_xml_file(the_sim: str, instance_device: str = '') -> list[DefaultDataRow]:
    """Parse sim+device defaults from defaults.xml.

    Args:
        the_sim: Simulator identifier
        instance_device: Device override (falls back to :data:`device`)

    Returns:
        Sorted list of setting dicts
    """
    return _resolver().read_xml_file(the_sim, instance_device)


def read_anydevice_settings(the_sim: str) -> list[str]:
    """Get setting names applicable to any device.

    Args:
        the_sim: Simulator identifier

    Returns:
        List of setting names marked ``<any>true</any>``
    """
    return _resolver().read_anydevice_settings(the_sim)


def read_models(the_sim: str, the_class: str = '') -> list[str]:
    """List all model patterns for a simulator/class.

    Args:
        the_sim: Simulator identifier
        the_class: Aircraft class filter (empty = all classes)

    Returns:
        Sorted list of model regex patterns (includes empty string)
    """
    return _resolver().read_models(the_sim, the_class)


def read_models_data(which_root: str, sim: str, full_model_name: str, alldevices: bool = False,
                     instance_device: str = '', user: bool = False, profile: Optional[str] = None) -> tuple[list[ModelDataRow], str]:
    """Extract model-specific config entries by regex matching.

    Args:
        which_root: ``'defaults'`` or ``'user'``
        sim: Simulator identifier
        full_model_name: Aircraft name to match against patterns
        alldevices: Ignore device filtering
        instance_device: Device override
        user: Search userconfig instead of defaults
        profile: Profile name filter

    Returns:
        Tuple of (setting list, matched pattern)
    """
    return _resolver().read_models_data(which_root, sim, full_model_name,
                                        alldevices, instance_device, user, profile)


def read_sc_overrides(aircraft_name: str) -> list[ScOverrideRow]:
    """Get merged SimConnect/dataref overrides for an aircraft.

    Args:
        aircraft_name: Aircraft identifier

    Returns:
        List of override dicts (``name``, ``var``, ``sc_unit``, ``scale``, ``source``)
    """
    return _resolver().read_sc_overrides(aircraft_name)


def read_default_class_data(the_sim: str, the_class: str, instance_device: str = '') -> tuple[list[ClassDataRow], Optional[list[str]]]:
    """Read class-level defaults and exclusion list.

    Args:
        the_sim: Simulator identifier
        the_class: Aircraft class name
        instance_device: Device override

    Returns:
        Tuple of (settings list, optional exclusion name list)
    """
    return _resolver().read_default_class_data(the_sim, the_class, instance_device)


def read_single_model(the_sim: str, aircraft_name: str, input_modeltype: str = '', instance_device: str = '',
                      active_profile: Optional[str] = None) -> tuple[str, str, list[DefaultDataRow]]:
    """Resolve the complete setting set for a single aircraft.

    Performs the full 6-layer cascade (sim → class → user-sim → user-class → model → user-model),
    applies prerequisite filtering, and returns sorted results.

    Args:
        the_sim: Simulator identifier
        aircraft_name: Full aircraft name
        input_modeltype: Pre-known class (looked up from XML if empty)
        instance_device: Device override
        active_profile: Profile override (auto-resolved if ``None``)

    Returns:
        Tuple of (class name, matched pattern, sorted settings list)
    """
    return _resolver().resolve(the_sim, aircraft_name, input_modeltype,
                               instance_device, active_profile)


def read_user_sim_data(the_sim: str, instance_device: str = '') -> list[ModelDataRow]:
    """Read user-defined sim-wide overrides from userconfig.

    Args:
        the_sim: Simulator identifier
        instance_device: Device override

    Returns:
        List of setting dicts
    """
    return _resolver().read_user_sim_data(the_sim, instance_device)


def read_user_class_data(the_sim: str, crafttype: str, instance_device: str = '') -> list[ModelDataRow]:
    """Read user-defined class-wide overrides from userconfig.

    Args:
        the_sim: Simulator identifier
        crafttype: Aircraft class name
        instance_device: Device override

    Returns:
        List of setting dicts
    """
    return _resolver().read_user_class_data(the_sim, crafttype, instance_device)


def read_user_models(sim: str, cls: str, default_only: bool = False, user_only: bool = True, both: bool = False) -> list[tuple[str, ...]]:
    """List user-created model entries for a sim/class.

    Args:
        sim: Simulator identifier
        cls: Aircraft class name
        default_only: Only models from defaults.xml
        user_only: Only models from userconfig
        both: Models from both sources

    Returns:
        Sorted list of model patterns
    """
    return _resolver().read_user_models(sim, cls, default_only, user_only, both)


def read_prereqs() -> list[PrereqRow]:
    """Scan userconfig for all prerequisite references.

    Returns:
        List of dicts with ``prereq``, ``value``, ``count``
    """
    return _resolver().read_prereqs()


# ── Write functions ────────────────────────────────────────────────

def write_models_to_xml(the_sim: str, the_model: str, the_value: str, setting_name: str, unit: str = '',
                        the_device: str = '', profile_name: Optional[str] = None) -> None:
    """Write or update a model-specific setting in userconfig.

    Args:
        the_sim: Simulator identifier
        the_model: Model regex pattern
        the_value: Setting value
        setting_name: Setting name (must exist in defaults.xml)
        unit: Unit override (optional)
        the_device: Device override
        profile_name: Profile name (``None``/``'None'`` = no profile filter)
    """
    _writer().write_models_to_xml(the_sim, the_model, the_value, setting_name,
                                  unit, the_device, profile_name)


def write_class_to_xml(the_sim: str, the_class: str, the_value: str, setting_name: str, unit: str = '',
                       the_device: str = '') -> None:
    """Write or update a class-wide setting in userconfig.

    Args:
        the_sim: Simulator identifier
        the_class: Aircraft class name
        the_value: Setting value
        setting_name: Setting name
        unit: Unit override (optional)
        the_device: Device override
    """
    _writer().write_class_to_xml(the_sim, the_class, the_value, setting_name,
                                 unit, the_device)


def write_sim_to_xml(the_sim: str, the_value: str, setting_name: str, unit: str = '', the_device: str = '') -> None:
    """Write or update a sim-wide setting in userconfig.

    Args:
        the_sim: Simulator identifier
        the_value: Setting value
        setting_name: Setting name
        unit: Unit override (optional)
        the_device: Device override
    """
    _writer().write_sim_to_xml(the_sim, the_value, setting_name, unit, the_device)


def write_sc_override_to_xml(the_model: str, the_var: str, setting_name: str, sc_unit: str = '', scale: str = '') -> None:
    """Write or update a SimConnect/dataref variable override in userconfig.

    Args:
        the_model: Aircraft model pattern
        the_var: L:variable or SimConnect variable name
        setting_name: Internal setting name being overridden
        sc_unit: Unit string
        scale: Numeric scaling factor
    """
    _writer().write_sc_override_to_xml(the_model, the_var, setting_name, sc_unit, scale)


# ── Erase functions ────────────────────────────────────────────────

def erase_models_from_xml(the_sim: str, the_model: str, setting_name: str, the_device: str = '',
                          profile_name: Optional[str] = None) -> None:
    """Remove a specific model setting from userconfig.

    Refuses to erase from ``'Built-In'`` profile.

    Args:
        the_sim: Simulator identifier
        the_model: Model pattern
        setting_name: Setting name
        the_device: Device override
        profile_name: Profile to erase from (defaults to active profile)
    """
    _writer().erase_models_from_xml(the_sim, the_model, setting_name, the_device,
                                    profile_name)


def erase_aircraft_profiles(sim: str, cls_name: str, model: str) -> None:
    """Remove ALL model entries for an aircraft (all profiles).

    Args:
        sim: Simulator identifier
        cls_name: Aircraft class name
        model: Model pattern to fully remove
    """
    _writer().erase_aircraft_profiles(sim, cls_name, model)


def erase_model_profile(sim: str, model: str, profile: str) -> None:
    """Remove all settings for a specific profile of a model.

    Args:
        sim: Simulator identifier
        model: Model pattern
        profile: Profile name to erase
    """
    _writer().erase_model_profile(sim, model, profile)


def erase_entire_model_from_xml(the_sim: str, the_model: str) -> None:
    """Remove all entries matching a model pattern from userconfig.

    Args:
        the_sim: Simulator identifier
        the_model: Model pattern to remove entirely
    """
    _writer().erase_entire_model_from_xml(the_sim, the_model)


def erase_class_from_xml(the_sim: str, the_class: str, setting_name: str, the_device: str = '') -> None:
    """Remove a class-level setting from userconfig.

    Args:
        the_sim: Simulator identifier
        the_class: Aircraft class name
        setting_name: Setting name
        the_device: Device override
    """
    _writer().erase_class_from_xml(the_sim, the_class, setting_name, the_device)


def erase_sim_from_xml(the_sim: str, setting_name: str, the_device: str = '') -> None:
    """Remove a sim-level setting from userconfig.

    Args:
        the_sim: Simulator identifier
        setting_name: Setting name
        the_device: Device override
    """
    _writer().erase_sim_from_xml(the_sim, setting_name, the_device)


def erase_sc_override_from_xml(the_model: str, setting_name: str) -> None:
    """Remove a SimConnect/dataref override from userconfig.

    Args:
        the_model: Model pattern
        setting_name: Setting name whose override to remove
    """
    _writer().erase_sc_override_from_xml(the_model, setting_name)


# ── Profile management ─────────────────────────────────────────────

def update_active_profile_entry(sim: str, cls: str, model: str, new_profile: str) -> None:
    """Set or update the active profile for a model in profileMappings.

    Args:
        sim: Simulator identifier
        cls: Aircraft class name
        model: Model pattern
        new_profile: Profile name to activate
    """
    _writer().update_active_profile_entry(sim, cls, model, new_profile)


def clone_profile_entry(sim: str, cls: str, src_model: str, src_profile: str, dst_profile: str) -> None:
    """Clone an existing profile's settings under a new profile name.

    Args:
        sim: Simulator identifier
        cls: Aircraft class name
        src_model: Source model pattern
        src_profile: Source profile name
        dst_profile: New profile name to create
    """
    _writer().clone_profile_entry(sim, cls, src_model, src_profile, dst_profile)


def clone_whole_model(the_sim: str, old_pattern: str, new_pattern: str, old_profile: str, new_profile: str) -> None:
    """Clone an entire model's settings with a new pattern and/or profile.

    Args:
        the_sim: Simulator identifier
        old_pattern: Original model regex pattern
        new_pattern: New model regex pattern
        old_profile: Source profile name
        new_profile: Destination profile name
    """
    _writer().clone_whole_model(the_sim, old_pattern, new_pattern, old_profile, new_profile)


def add_new_model(sim: str, class_name: str, match_string: str, profile_name: str) -> None:
    """Create a new empty model entry in userconfig.

    Args:
        sim: Simulator identifier
        class_name: Aircraft class
        match_string: Regex pattern for aircraft name matching
        profile_name: Profile name
    """
    _writer().add_new_model(sim, class_name, match_string, profile_name)


def add_new_profile(sim: str, class_name: str, match_string: str, profile_name: str) -> None:
    """Add a new profile to an existing model's profileMappings entry.

    Args:
        sim: Simulator identifier
        class_name: Aircraft class name
        match_string: Model regex pattern
        profile_name: New profile name
    """
    _writer().add_new_profile(sim, class_name, match_string, profile_name)


def rename_profile(sim: str, class_name: str, match_string: str, existing_name: str, new_name: str) -> None:
    """Rename an existing profile across all its model entries and the profileMapping.

    Args:
        sim: Simulator identifier
        class_name: Aircraft class name
        match_string: Model regex pattern
        existing_name: Current profile name
        new_name: New profile name
    """
    _writer().rename_profile(sim, class_name, match_string, existing_name, new_name)


# ── Merge / transform helpers (pure functions) ─────────────────────

def update_default_data_with_craft_result(defaultdata: list[DefaultDataRow], craftresult: list[ClassDataRow]) -> list[DefaultDataRow]:
    """Merge class defaults into sim defaults by setting name.

    Pure function — no side effects.

    Args:
        defaultdata: Base sim defaults
        craftresult: Class-level settings to merge in

    Returns:
        New merged list
    """
    return _merge_update_default_data_with_craft_result(defaultdata, craftresult)


def update_data_with_models(defaults_data: list[DefaultDataRow], model_data: list[ModelDataRow], replacetext: str) -> list[DefaultDataRow]:
    """Merge model/user overrides into base data by setting name.

    Pure function.

    Args:
        defaults_data: Base settings
        model_data: Override settings
        replacetext: Label for the ``replaced`` field

    Returns:
        New merged list
    """
    return _merge_update_data_with_models(defaults_data, model_data, replacetext)


def update_sc_overrides_with_user(defaults_ovr: list[ScOverrideRow], user_ovr: list[ScOverrideRow]) -> list[ScOverrideRow]:
    """Merge default + user SC overrides; user wins by name, new items appended.

    Pure function.

    Args:
        defaults_ovr: Default overrides from defaults.xml
        user_ovr: User overrides from userconfig

    Returns:
        Merged override list
    """
    return _merge_update_sc_overrides_with_user(defaults_ovr, user_ovr)


def remove_dicts_by_names(data_list: list[DefaultDataRow], removal_list: list[str]) -> list[DefaultDataRow]:
    """Filter out settings whose names appear in the removal list.

    Pure function. Used to apply class ``<exclusion>`` lists.

    Args:
        data_list: Settings to filter
        removal_list: Setting names to remove

    Returns:
        Filtered list
    """
    return _merge_remove_dicts_by_names(data_list, removal_list)


def check_prereq_value(prereq_list: list[PrereqRow], datalist: list[DefaultDataRow]) -> list[DefaultDataRow]:
    """Resolve prerequisite values from current data (mutates prereq_list in place).

    Args:
        prereq_list: Prerequisite rows to resolve
        datalist: Current settings to look up values from

    Returns:
        The datalist unchanged (prereq_list is mutated)
    """
    return _merge_check_prereq_value(prereq_list, datalist)


def eliminate_no_prereq(datalist: list[DefaultDataRow]) -> list[DefaultDataRow]:
    """Filter out settings with unmet prerequisites.

    Args:
        datalist: Settings to filter

    Returns:
        Filtered list
    """
    return _merge_eliminate_no_prereq(datalist)


def filter_rows(data_list: list[DefaultDataRow]) -> list[DefaultDataRow]:
    """Recursively filter settings by prerequisite chain validity.

    Handles transitive dependencies.

    Args:
        data_list: Settings to filter

    Returns:
        Filtered list
    """
    return _merge_filter_rows(data_list)


# ── Lookup helpers ─────────────────────────────────────────────────

def get_sims() -> list[str]:
    """Return list of all registered simulator identifiers."""
    return _resolver().get_sims()


def get_classes_for_sim(sim: str) -> list[str]:
    """Return list of all aircraft classes registered for a simulator.

    Args:
        sim: Simulator identifier
    """
    return _resolver().get_classes_for_sim(sim)


def get_pattern_by_sim_fullname(sim: str, full_name: str) -> Optional[str]:
    """Look up the model pattern for a full aircraft name.

    Args:
        sim: Simulator identifier
        full_name: Full aircraft name

    Returns:
        Matching model pattern, or ``None``
    """
    return _resolver().get_pattern_by_sim_fullname(sim, full_name)


def get_class_for_sim_model(sim: str, model: Optional[str]) -> Optional[str]:
    """Look up the aircraft class for a model pattern.

    Args:
        sim: Simulator identifier
        model: Model regex pattern

    Returns:
        Class name, or ``None``
    """
    return _resolver().get_class_for_sim_model(sim, model)


def get_active_profile_for_model(sim: str, cls: str, model: str) -> Optional[str]:
    """Look up the active profile for a model from profileMappings.

    Args:
        sim: Simulator identifier
        cls: Aircraft class name
        model: Model pattern

    Returns:
        Active profile name, or ``None``
    """
    return _resolver().get_active_profile_for_model(sim, cls, model)


def get_available_profiles(sim: str, cls: str, model: str) -> list[str]:
    """List all available profiles for a model from profileMappings.

    Args:
        sim: Simulator identifier
        cls: Aircraft class name
        model: Model pattern

    Returns:
        Sorted list of profile names
    """
    return _resolver().get_available_profiles(sim, cls, model)


def get_sim_defaults(sim: str, dev_type: str) -> list[DefaultDataRow]:
    """Get sim-level defaults for a device.

    Args:
        sim: Simulator identifier
        dev_type: Device type

    Returns:
        Sorted list of default settings
    """
    return _resolver().get_sim_defaults(sim, dev_type)


def get_class_defaults(sim: str, cls: str, dev_type: str) -> tuple[list[ClassDataRow], Optional[list[str]]]:
    """Get class-level defaults and exclusions.

    Args:
        sim: Simulator identifier
        cls: Aircraft class name
        dev_type: Device type

    Returns:
        Tuple of (settings list, optional exclusion list)
    """
    return _resolver().get_class_defaults(sim, cls, dev_type)


def get_model_profile(sim: str, model: str, profile: str, dev_type: str) -> list[ModelDataRow]:
    """Get model settings for a specific profile.

    Args:
        sim: Simulator identifier
        model: Model pattern
        profile: Profile name
        dev_type: Device type

    Returns:
        List of model setting dicts
    """
    return _resolver().get_model_profile(sim, model, profile, dev_type)


def get_sc_override(model: str) -> list[ScOverrideRow]:
    """Get SC overrides for a model.

    Args:
        model: Aircraft name or pattern

    Returns:
        List of override dicts
    """
    return _resolver().get_sc_override(model)


def get_model_type(sim: str, model: str, cls: str) -> Optional[str]:
    """Get the aircraft type/class for a model.

    Args:
        sim: Simulator identifier
        model: Model pattern
        cls: Aircraft class hint

    Returns:
        Type string, or ``None``
    """
    return _resolver().get_model_type(sim, model, cls)


# ── Model notes ────────────────────────────────────────────────────

def read_default_model_notes(sim: str, full_model_name: str, prefer_pattern: str = '') -> str:
    """Return curated <notes> text from defaults <models> for an aircraft."""
    return _resolver().read_default_model_notes(sim, full_model_name, prefer_pattern)


def read_user_default_model_notes(sim: str, model: str) -> str:
    """Return user's <notes> on a name="type" row in userconfig."""
    return _resolver().read_user_default_model_notes(sim, model)


def read_user_model_notes(sim: str, model: str, profile: str) -> str:
    """Return user's <notes> on the name="profile" row."""
    return _resolver().read_user_model_notes(sim, model, profile)


def write_user_model_notes(sim: str, model: str, note_text: str, profile_name: str) -> Optional[str]:
    """Write, replace, or remove <notes> on the userconfig's name="profile" row."""
    return _writer().write_user_model_notes(sim, model, note_text, profile_name)


# ── Debug / logging helpers (legacy-only) ──────────────────────────

def dbprint(color: str, msg: str) -> None:
    """Print a coloured debug message to stdout.

    Only outputs when :data:`print_debugs` is ``True``.

    Args:
        color: ANSI colour code (e.g. ``'31'`` for red)
        msg: Message text
    """
    if print_debugs:
        print(f"\033[{color}m{msg}\033[0m")


def printconfig(sorted_data: list[DefaultDataRow]) -> None:
    """Print all settings in a sorted data list to stdout.

    Only outputs when :data:`print_debugs` is ``True``.

    Args:
        sorted_data: List of setting dicts to print
    """
    if print_debugs:
        for row in sorted_data:
            print(row)


def lprint(msg: str) -> None:
    """Print a log message to stdout when :data:`print_debugs` is ``True``."""
    if print_debugs:
        print(msg)


def mprint(msg: str) -> None:
    """Print a method-call trace message when :data:`print_method_calls` is ``True``."""
    if print_method_calls:
        print(msg)


# ── Legacy aliases ─────────────────────────────────────────────────

def old_write_models_to_xml(the_sim: str, the_model: str, the_value: str, setting_name: str, unit: str = '',
                            the_device: str = '', profile_name: Optional[str] = None) -> None:
    """Legacy alias for :func:`write_models_to_xml`."""
    write_models_to_xml(the_sim, the_model, the_value, setting_name, unit,
                        the_device, profile_name)


def get_craft_attributes(which_root: ET.Element, sim: str, device: str) -> Optional[dict[str, str]]:
    """Extract all child tag/text pairs from the first matching model element.

    Args:
        which_root: XML root element to search within
        sim: Simulator identifier to match on
        device: Device type to match on

    Returns:
        Dict mapping child tag names to their text content, or ``None``
    """
    models = which_root.findall(f'./models[@sim="{sim}"]')
    for m in models:
        attrs: dict[str, str] = {}
        for child in m:
            attrs[child.tag] = child.text or ''
        return attrs
    return None


def read_models_sc_overrides(which_root: ET.Element, full_model_name: str, source: str) -> list[ScOverrideRow]:
    """Read SC overrides for a model from a specific XML root.

    Args:
        which_root: XML root element to search within
        full_model_name: Aircraft name to match on
        source: Source label (``'default'`` or ``'user'``)

    Returns:
        List of override dicts with ``source`` field set
    """
    overrides: list[ScOverrideRow] = []
    for ov in which_root.findall(f'.//sc_overrides[model="{full_model_name}"]'):
        entry: ScOverrideRow = {
            'name': ov.findtext('name') or '',
            'var': ov.findtext('var') or '',
            'sc_unit': ov.findtext('sc_unit') or '',
            'scale': ov.findtext('scale') or '',
            'source': source,
        }
        overrides.append(entry)
    return overrides


def apply_validvalue_overrides_from_root(data_list: list[DefaultDataRow], sim: str, model_class: str, instance_device: str) -> list[DefaultDataRow]:
    """Apply validvalues overrides from defaults.xml for a given class/device.

    Args:
        data_list: Settings to potentially modify
        sim: Simulator identifier
        model_class: Aircraft class name
        instance_device: Device type

    Returns:
        The modified data_list (mutated in place)
    """
    return _resolver()._apply_validvalue_overrides(
        data_list, sim, model_class, instance_device)
