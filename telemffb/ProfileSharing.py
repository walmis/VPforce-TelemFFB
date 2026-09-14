#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import xml.etree.ElementTree as ET


ROOT_TAG = "TelemFFB_v2"
SHARE_METADATA_TAG = "shareMetadata"
SHARE_SCHEMA_VERSION = "1"

VALID_DEVICE_TYPES = {"joystick", "pedals", "collective", "trimwheel", "any"}
VALID_TOP_LEVEL_TAGS = {
    SHARE_METADATA_TAG,
    "models",
    "simSettings",
    "classSettings",
    "sc_overrides",
}


@dataclass(frozen=True)
class SharedProfileItem:
    sim: str
    cls: str
    model: str
    profile: str


@dataclass(frozen=True)
class SharedProfileMetadata:
    title: str
    author: str = ""
    notes: str = ""
    telemffb_version: str = ""
    exported_at: str = ""
    schema_version: str = SHARE_SCHEMA_VERSION
    items: list[SharedProfileItem] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    omitted_vpconf_refs: list[str] = field(default_factory=list)


class LocalProfileShareService:
    """Build and validate local XML profile share files."""

    def create_metadata_element(self, metadata: SharedProfileMetadata) -> ET.Element:
        share_metadata = ET.Element(SHARE_METADATA_TAG)
        share_metadata.set("schema", metadata.schema_version)

        self._append_text(share_metadata, "title", metadata.title)
        self._append_text(share_metadata, "author", metadata.author)
        self._append_text(share_metadata, "notes", metadata.notes)
        self._append_text(share_metadata, "telemffbVersion", metadata.telemffb_version)
        self._append_text(share_metadata, "exportedAt", metadata.exported_at or self._timestamp())

        items = ET.SubElement(share_metadata, "items")
        for item in metadata.items:
            item_element = ET.SubElement(items, "item")
            item_element.set("sim", item.sim)
            item_element.set("class", item.cls)
            item_element.set("model", item.model)
            item_element.set("profile", item.profile)

        devices = ET.SubElement(share_metadata, "devices")
        for device in self._unique_in_order(metadata.devices):
            self._append_text(devices, "device", device)

        warnings = ET.SubElement(share_metadata, "warnings")
        for vpconf_path in metadata.omitted_vpconf_refs:
            warning = ET.SubElement(warnings, "warning")
            warning.set("type", "vpconf-not-bundled")
            warning.text = vpconf_path

        return share_metadata

    def add_metadata(self, root: ET.Element, metadata: SharedProfileMetadata) -> None:
        existing = root.find(SHARE_METADATA_TAG)
        if existing is not None:
            root.remove(existing)
        root.insert(0, self.create_metadata_element(metadata))

    def parse_metadata(self, root: ET.Element) -> SharedProfileMetadata | None:
        element = root.find(SHARE_METADATA_TAG)
        if element is None:
            return None

        items = [
            SharedProfileItem(
                sim=item.get("sim", ""),
                cls=item.get("class", ""),
                model=item.get("model", ""),
                profile=item.get("profile", ""),
            )
            for item in element.findall("./items/item")
        ]
        devices = [
            device.text.strip()
            for device in element.findall("./devices/device")
            if device.text and device.text.strip()
        ]
        omitted_vpconf_refs = [
            warning.text.strip()
            for warning in element.findall('./warnings/warning[@type="vpconf-not-bundled"]')
            if warning.text and warning.text.strip()
        ]

        return SharedProfileMetadata(
            title=element.findtext("title", ""),
            author=element.findtext("author", ""),
            notes=element.findtext("notes", ""),
            telemffb_version=element.findtext("telemffbVersion", ""),
            exported_at=element.findtext("exportedAt", ""),
            schema_version=element.get("schema", SHARE_SCHEMA_VERSION),
            items=items,
            devices=devices,
            omitted_vpconf_refs=omitted_vpconf_refs,
        )

    def validate_import_root(self, root: ET.Element) -> list[str]:
        if root.tag != ROOT_TAG:
            return ["Importation is only supported for files generated by TelemFFB v2 versions."]

        errors: list[str] = []
        for child in root:
            if child.tag not in VALID_TOP_LEVEL_TAGS:
                errors.append(f"Unsupported top-level XML section: {child.tag}")

        for model in root.findall("models"):
            errors.extend(self._validate_model(model))

        for sim_setting in root.findall("simSettings"):
            errors.extend(self._validate_required(sim_setting, ["sim", "device", "name", "value"]))
            errors.extend(self._validate_device(sim_setting))

        for class_setting in root.findall("classSettings"):
            errors.extend(self._validate_required(class_setting, ["sim", "type", "device", "name", "value"]))
            errors.extend(self._validate_device(class_setting))

        for override in root.findall("sc_overrides"):
            errors.extend(self._validate_required(override, ["model", "name", "var", "sc_unit"]))

        return errors

    def find_omitted_vpconf_refs(self, root: ET.Element) -> list[str]:
        refs = []
        for model in root.findall('models[name="vpconf"]'):
            value = model.findtext("value", "").strip()
            if value and value != "-":
                refs.append(value)
        return sorted(set(refs))

    def _validate_model(self, model: ET.Element) -> list[str]:
        errors = self._validate_required(model, ["sim", "model", "device", "name", "value"])
        errors.extend(self._validate_device(model))

        name = model.findtext("name", "")
        profile = model.findtext("profile")
        if name != "type" and not profile:
            errors.append("Model setting is missing required profile tag.")

        return errors

    def _validate_required(self, element: ET.Element, tags: list[str]) -> list[str]:
        errors = []
        for tag in tags:
            value = element.findtext(tag)
            if value is None or value == "":
                errors.append(f"<{element.tag}> entry is missing required <{tag}> value.")
        return errors

    def _validate_device(self, element: ET.Element) -> list[str]:
        device = element.findtext("device")
        if device and device not in VALID_DEVICE_TYPES:
            return [f"<{element.tag}> entry has unsupported device '{device}'."]
        return []

    def _append_text(self, parent: ET.Element, tag: str, value: str) -> None:
        ET.SubElement(parent, tag).text = value or ""

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _unique_in_order(self, values: list[str]) -> list[str]:
        seen = set()
        result = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result
