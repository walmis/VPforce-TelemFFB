"""Modular XML configuration system for TelemFFB.

Provides encapsulated classes for reading, writing, and merging aircraft
configuration data stored in ``defaults.xml`` and ``userconfig_v2.xml``.

For backward compatibility with existing code, prefer importing from
:mod:`telemffb.xmlutils` which wraps these classes with the original
module-level global-state API.

Usage (new-style)::

    from telemffb.xml import XmlConfigManager

    mgr = XmlConfigManager('joystick', userconfig_path, defaults_path)
    mgr.store.update_roots()
    cls, pattern, settings = mgr.resolver.resolve('MSFS', 'Cessna 172')
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from telemffb.xml.store import XmlStore, try_parse
from telemffb.xml.read import ConfigResolver
from telemffb.xml.write import ConfigWriter
from telemffb.xml.merge import (
    update_default_data_with_craft_result,
    update_data_with_models,
    update_sc_overrides_with_user,
    remove_dicts_by_names,
    check_prereq_value,
    eliminate_no_prereq,
    filter_rows,
)

if TYPE_CHECKING:
    from telemffb.xml.types import (
        ClassDataRow,
        DefaultDataRow,
        ModelDataRow,
        PrereqRow,
        ScOverrideRow,
    )

__all__ = [
    # Core classes
    'XmlStore',
    'ConfigResolver',
    'ConfigWriter',
    'XmlConfigManager',
    # Store helpers
    'try_parse',
    # Merge utilities (pure functions)
    'update_default_data_with_craft_result',
    'update_data_with_models',
    'update_sc_overrides_with_user',
    'remove_dicts_by_names',
    'check_prereq_value',
    'eliminate_no_prereq',
    'filter_rows',
]


class XmlConfigManager:
    """Convenience wrapper providing access to all three components.

    Creates a single coordinated instance of :class:`XmlStore`,
    :class:`ConfigResolver`, and :class:`ConfigWriter` sharing the same
    underlying XML trees.

    Args:
        device: Device type identifier (``'joystick'``, ``'pedals'``, etc.)
        userconfig_path: Path to user configuration XML file
        defaults_path: Path to default settings XML file
    """

    def __init__(self, device: str, userconfig_path: str, defaults_path: str) -> None:
        self._store = XmlStore(device, userconfig_path, defaults_path)
        self._resolver = ConfigResolver(self._store)
        self._writer = ConfigWriter(self._store, self._resolver)

    @property
    def store(self) -> XmlStore:
        """Parsed XML trees and file path management."""
        return self._store

    @property
    def resolver(self) -> ConfigResolver:
        """Read-only query interface over parsed XML config trees."""
        return self._resolver

    @property
    def writer(self) -> ConfigWriter:
        """Mutates userconfig XML for settings, profiles, and SC overrides."""
        return self._writer