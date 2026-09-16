"""Shared type definitions for the xml module.

Defines TypedDicts and type aliases used across store, read, write, and merge.
"""
from typing import NotRequired, TypedDict


class DefaultsEntry(TypedDict):
    """Row produced by read_xml_file — one <defaults> element flattened."""
    grouping: str
    order: str
    name: str
    displayname: str
    exclusive_with: str
    value: str
    unit: str
    datatype: str
    validvalues: str
    replaced: str
    prereq: str
    render_prereq: NotRequired[str]
    enable_prereq: NotRequired[str]
    info: str
    sliderfactor: str
    device_text: str
    indent: int
    prior_value: NotRequired[str]
    prior_unit: NotRequired[str]


class PartialDefaultsEntry(TypedDict, total=False):
    """Partial DefaultsEntry for user overrides that only specify a subset of fields."""
    grouping: str
    order: str
    name: str
    displayname: str
    exclusive_with: str
    value: str
    unit: str
    datatype: str
    validvalues: str
    replaced: str
    prereq: str
    render_prereq: str
    enable_prereq: str
    info: str
    sliderfactor: str
    device_text: str
    indent: int
    prior_value: str
    prior_unit: str


class ModelDataEntry(TypedDict):
    """Row produced by _read_models_data — one <models> entry flattened."""
    name: str
    value: str
    unit: str
    device: str


class ClassDefaultsEntry(TypedDict):
    """Row produced by read_default_class_data."""
    name: str
    value: str
    unit: str
    replaced: str


class ScOverrideEntry(TypedDict):
    """Row produced by read_sc_overrides / _read_models_sc_overrides."""
    name: str
    var: str
    sc_unit: str
    scale: str
    source: str


class PrereqEntry(TypedDict):
    """Row produced by read_prereqs."""
    prereq: str
    value: str
    count: int


# Convenience aliases
DefaultDataRow = DefaultsEntry
PartialDefaultDataRow = PartialDefaultsEntry
ModelDataRow = ModelDataEntry
ClassDataRow = ClassDefaultsEntry
ScOverrideRow = ScOverrideEntry
PrereqRow = PrereqEntry