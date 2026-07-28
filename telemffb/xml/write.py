"""ConfigWriter — all write/erase/profile operations on userconfig.

Every method mutates the user XML tree and persists to disk.
"""
import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional, TYPE_CHECKING

import telemffb.globals as G

if TYPE_CHECKING:
    from telemffb.xml.read import ConfigResolver
from telemffb.xml.store import XmlStore


class ConfigWriter:
    """Mutates userconfig XML for settings, profiles, and SC overrides."""

    def __init__(self, store: XmlStore, resolver: 'ConfigResolver') -> None:
        self._store = store
        self._resolver = resolver

    # ── Write operations ──────────────────────────────────────

    def write_models_to_xml(
        self,
        sim: str,
        model: str,
        value: str,
        name: str,
        unit: str = '',
        device: str = '',
        profile_name: Optional[str] = None,
    ) -> None:
        if not model:
            raise ValueError(f"Invalid model name >{model}<")

        dev = device or self._store.device
        any_settings = self._resolver.read_anydevice_settings(sim)
        if name in any_settings:
            dev = 'any'

        is_profile = profile_name is not None and profile_name.lower() != 'none'

        if is_profile and profile_name is not None and profile_name.lower() == 'built-in':
            cls = self._resolver.get_class_for_sim_model(sim, model)
            root = self._store.user_root
            if root is not None:
                existing = root.find(
                    f'.//models[sim="{sim}"][model="{model}"][name="profile"][profile="Auto User"]')
                if existing is None:
                    self.add_new_profile(sim, cls or '', model, 'Auto User')
                self.update_active_profile_entry(sim, cls or '', model, "Auto User")
            profile_name = "Auto User"

        xpath = f'.//models[sim="{sim}"][device="{dev}"][model="{model}"][name="{name}"]'
        if is_profile and profile_name is not None:
            xpath += f'[profile="{profile_name}"]'

        # For profile rows, also try without device filter (existing row may use a different device)
        profile_fallback_xpath = None
        if name == 'profile' and profile_name is not None:
            profile_fallback_xpath = f'.//models[sim="{sim}"][model="{model}"][name="profile"][profile="{profile_name}"]'

        root = self._store.user_root
        if root is None:
            return
        elem = root.find(xpath)
        if elem is None and profile_fallback_xpath is not None:
            elem = root.find(profile_fallback_xpath)
        if elem is not None:
            for child in elem:
                if child.tag == 'value':
                    child.text = str(value)
                elif child.tag == 'unit':
                    child.text = str(unit)
            self._store.write_userconfig()
            logging.info("Updated <models>: sim=%s model=%s name=%s", sim, model, name)
            return

        # Dedup check
        def same(e: ET.Element) -> bool:
            tags = {'name': name, 'model': model, 'value': str(value),
                    'unit': unit, 'sim': sim, 'device': dev}
            if is_profile and profile_name is not None:
                tags['profile'] = profile_name
            return all(e.find(t) is not None and e.find(t).text == v for t, v in tags.items())

        if any(same(e) for e in root.findall('models')):
            return

        new_elem = _make_models(sim, model, value, name, unit, dev, profile_name if is_profile else None)
        root.append(new_elem)
        self._store.write_userconfig()
        logging.info("Added <models>: sim=%s model=%s name=%s", sim, model, name)

    def write_class_to_xml(
        self,
        sim: str,
        cls: str,
        value: str,
        name: str,
        unit: str = '',
        device: str = '',
    ) -> None:
        dev = device or self._store.device
        any_settings = self._resolver.read_anydevice_settings(sim)
        if name in any_settings:
            dev = 'any'

        root = self._store.user_root
        if root is None:
            return
        xpath = f'.//classSettings[sim="{sim}"][type="{cls}"][device="{dev}"][name="{name}"]'
        elem = root.find(xpath)
        if elem is not None:
            for child in elem:
                if child.tag == 'value':
                    child.text = str(value)
                elif child.tag == 'unit':
                    child.text = str(unit)
            self._store.write_userconfig()
            return

        new_elem = ET_element('classSettings', [
            ('name', name), ('value', str(value)), ('sim', sim),
            ('type', cls), ('device', dev)])
        if unit:
            ET.SubElement(new_elem, 'unit').text = unit
        root.append(new_elem)
        self._store.write_userconfig()

    def write_sim_to_xml(
        self,
        sim: str,
        value: str,
        name: str,
        unit: str = '',
        device: str = '',
    ) -> None:
        dev = device or self._store.device
        any_settings = self._resolver.read_anydevice_settings(sim)
        if name in any_settings:
            dev = 'any'

        root = self._store.user_root
        if root is None:
            return
        xpath = f'.//simSettings[sim="{sim}"][device="{dev}"][name="{name}"]'
        elem = root.find(xpath)
        if elem is not None:
            for child in elem:
                if child.tag == 'value':
                    child.text = str(value)
                elif child.tag == 'unit':
                    child.text = str(unit)
            self._store.write_userconfig()
            return

        new_elem = ET_element('simSettings', [
            ('name', name), ('value', str(value)), ('sim', sim),
            ('device', dev)])
        if unit:
            ET.SubElement(new_elem, 'unit').text = unit
        root.append(new_elem)
        self._store.write_userconfig()

    def write_sc_override_to_xml(
        self,
        model: str,
        var: str,
        name: str,
        sc_unit: str = '',
        scale: str = '',
    ) -> None:
        root = self._store.user_root
        if root is None:
            return
        xpath = f'.//sc_overrides[model="{model}"][name="{name}"]'
        elem = root.find(xpath)
        if elem is not None:
            for child in elem:
                if child.tag == 'var':
                    child.text = var
                elif child.tag == 'sc_unit':
                    child.text = sc_unit
                elif child.tag == 'scale':
                    child.text = str(scale)
                elif child.tag == 'source':
                    child.text = 'user'
            self._store.write_userconfig()
            return

        new_elem = ET_element('sc_overrides', [
            ('name', name), ('model', model), ('var', var),
            ('sc_unit', sc_unit), ('scale', str(scale)), ('source', 'user')])
        root.append(new_elem)
        self._store.write_userconfig()

    # ── Erase operations ──────────────────────────────────────

    def erase_models_from_xml(
        self,
        sim: str,
        model: str,
        name: str,
        device: str = '',
        profile_name: Optional[str] = None,
    ) -> None:
        dev = device or self._store.device
        any_settings = self._resolver.read_anydevice_settings(sim)
        if name in any_settings:
            dev = 'any'

        if profile_name is None:
            mgr = getattr(G, 'settings_mgr', None)
            profile_name = mgr.active_profile if mgr else 'Built-In'

        if profile_name.lower() == 'built-in':
            logging.warning("Refused to erase from default profile: %s %s %s", sim, model, name)
            return

        root = self._store.user_root
        if root is None:
            return
        xpath = (f'models[sim="{sim}"][device="{dev}"][model="{model}"]'
                 f'[name="{name}"][profile="{profile_name}"]')
        for elem in root.findall(xpath):
            root.remove(elem)
            self._store.write_userconfig()
            logging.info("Removed <models>: sim=%s model=%s name=%s", sim, model, name)

    def erase_class_from_xml(
        self,
        sim: str,
        cls: str,
        name: str,
        device: str = '',
    ) -> None:
        dev = device or self._store.device
        any_settings = self._resolver.read_anydevice_settings(sim)
        if name in any_settings:
            dev = 'any'

        root = self._store.user_root
        if root is None:
            return
        xpath = f'classSettings[sim="{sim}"][type="{cls}"][device="{dev}"][name="{name}"]'
        for elem in root.findall(xpath):
            root.remove(elem)
        self._store.write_userconfig()

    def erase_sim_from_xml(
        self,
        sim: str,
        name: str,
        device: str = '',
    ) -> None:
        dev = device or self._store.device
        any_settings = self._resolver.read_anydevice_settings(sim)
        if name in any_settings:
            dev = 'any'

        root = self._store.user_root
        if root is None:
            return
        xpath = f'simSettings[sim="{sim}"][device="{dev}"][name="{name}"]'
        for elem in root.findall(xpath):
            root.remove(elem)
        self._store.write_userconfig()

    def erase_sc_override_from_xml(self, model: str, name: str) -> None:
        root = self._store.user_root
        if root is None:
            return
        xpath = f'sc_overrides[model="{model}"][name="{name}"]'
        for elem in root.findall(xpath):
            root.remove(elem)
        self._store.write_userconfig()

    def erase_aircraft_profiles(self, sim: str, cls: str, model: str) -> None:
        root = self._store.user_root
        if root is None:
            return
        for elem in list(root.findall(f'.//models[sim="{sim}"][model="{model}"]')):
            root.remove(elem)
        self._store.write_userconfig()

    def erase_model_profile(self, sim: str, model: str, profile: str) -> None:
        root = self._store.user_root
        if root is None:
            return
        for elem in list(root.findall(f'.//models[sim="{sim}"][model="{model}"][profile="{profile}"]')):
            root.remove(elem)
        self._store.write_userconfig()

    def erase_entire_model_from_xml(self, sim: str, model: str) -> None:
        root = self._store.user_root
        if root is None:
            return
        for elem in list(root.findall(f'.//models[model="{model}"][sim="{sim}"]')):
            root.remove(elem)
        self._store.write_userconfig()

    # ── Profile management ────────────────────────────────────

    def update_active_profile_entry(self, sim: str, cls: str, model: str, new_profile: str) -> None:
        root = self._store.user_root
        if root is None:
            return
        xpath = f'.//profileMappings[sim="{sim}"][cls="{cls}"][model="{model}"]'
        elem = root.find(xpath)
        if elem is not None:
            ap = elem.find('active_profile')
            if ap is not None:
                ap.text = new_profile
            else:
                ap = _sub(elem, 'active_profile', new_profile)
            self._store.write_userconfig()
        else:
            new_elem = ET_element('profileMappings', [
                ('sim', sim), ('cls', cls), ('model', model),
                ('active_profile', new_profile)])
            root.append(new_elem)
            self._store.write_userconfig()

    def clone_profile_entry(
        self,
        sim: str,
        cls: str,
        src_model: str,
        src_profile: str,
        dst_profile: str,
    ) -> None:
        root = self._store.user_root
        defaults_root = self._store.defaults_root

        is_builtin = src_profile.lower() in ('built-in', 'default')

        source_root = defaults_root if is_builtin else root
        if source_root is None:
            return
        xpath = f'models[sim="{sim}"][model="{src_model}"]'
        if not is_builtin:
            xpath += f'[profile="{src_profile}"]'

        for elem in source_root.findall(xpath):
            name = elem.findtext('name')
            if name == 'profile':
                continue
            value = elem.findtext('value', '')
            unit = elem.findtext('unit', '')
            device = elem.findtext('device', '')
            self.write_models_to_xml(sim, src_model, value, name, unit, device, dst_profile)

    def clone_whole_model(
        self,
        sim: str,
        old_pattern: str,
        new_pattern: str,
        old_profile: str,
        new_profile: str,
    ) -> None:
        # Clone models data
        model_data, _ = self._resolver.read_models_data('defaults', sim, old_pattern,
                                                         user=False, profile=None)
        for m in model_data:
            self.write_models_to_xml(sim, new_pattern, m['value'], m['name'],
                                     m.get('unit', ''), m.get('device', ''), new_profile)

        user_data, _ = self._resolver.read_models_data('user', sim, old_pattern,
                                                       user=True, profile=old_profile)
        for m in user_data:
            self.write_models_to_xml(sim, new_pattern, m['value'], m['name'],
                                     m.get('unit', ''), m.get('device', ''), new_profile)

        # Clone SC overrides
        for ov in self._resolver.read_sc_overrides(old_pattern):
            self.write_sc_override_to_xml(new_pattern, ov['var'], ov['name'],
                                          ov.get('sc_unit', ''), ov.get('scale', ''))

    def add_new_model(
        self,
        sim: str,
        class_name: str,
        match_string: str,
        profile_name: str,
    ) -> None:
        self.write_models_to_xml(sim, match_string, class_name, 'type',
                                 device=self._store.device, profile_name=profile_name)

    def add_new_profile(
        self,
        sim: str,
        class_name: str,
        match_string: str,
        profile_name: str,
    ) -> None:
        root = self._store.user_root
        if root is None:
            return
        existing = root.find(
            f'.//models[sim="{sim}"][model="{match_string}"][name="profile"][profile="{profile_name}"]')
        if existing is None:
            self.write_models_to_xml(sim, match_string, class_name, 'profile',
                                     device=self._store.device, profile_name=profile_name)

    def rename_profile(
        self,
        sim: str,
        class_name: str,
        match_string: str,
        existing_name: str,
        new_name: str,
    ) -> None:
        root = self._store.user_root
        if root is None:
            return
        for elem in root.findall(f'models[sim="{sim}"][model="{match_string}"]'):
            prof = elem.find('profile')
            if prof is not None and prof.text == existing_name:
                prof.text = new_name
        for elem in root.findall('profileMappings'):
            if (elem.findtext('sim') == sim and elem.findtext('cls') == class_name
                    and elem.findtext('model') == match_string):
                ap = elem.find('active_profile')
                if ap is not None and ap.text == existing_name:
                    ap.text = new_name
        self._store.write_userconfig()

    def write_user_model_notes(
        self,
        sim: str,
        model: str,
        note_text: str,
        profile_name: str,
    ) -> Optional[str]:
        """Write, replace, or remove <notes> on the userconfig's name="profile" row.

        Mirrors the settings write redirect: the "Built-in" pseudo-profile is not
        writable, so notes for it land on "Auto User" (the profile row is created
        if missing). An empty or whitespace-only note removes the <notes> element.
        The active profile mapping is deliberately left untouched.

        Args:
            sim: Simulator name.
            model: The exact model pattern.
            note_text: Notes text to write (empty/whitespace removes the element).
            profile_name: Profile name.

        Returns:
            The profile name the note was written under, or None on failure.
        """
        if not model:
            logging.error("write_user_model_notes: no model pattern provided")
            return None
        if not profile_name or profile_name.lower() in ('none', 'built-in', 'default'):
            profile_name = 'Auto User'

        row_xpath = f'models[sim="{sim}"][model="{model}"][name="profile"][profile="{profile_name}"]'
        root = self._store.user_root
        if root is None:
            return None
        row = root.find(row_xpath)
        if row is None:
            cls = self._resolver.get_class_for_sim_model(sim, model) or ''
            self.add_new_profile(sim, cls, model, profile_name)
            row = root.find(row_xpath)
            if row is None:
                logging.error(
                    "write_user_model_notes: could not create profile row for "
                    "sim=%s, model=%s, profile=%s", sim, model, profile_name)
                return None

        notes_elem = row.find('notes')
        text = (note_text or '').strip()
        if text:
            if notes_elem is None:
                notes_elem = ET.SubElement(row, 'notes')
            notes_elem.text = text
        elif notes_elem is not None:
            row.remove(notes_elem)

        self._store.write_userconfig()
        logging.info(
            "Saved profile notes for sim=%s, model=%s, profile=%s",
            sim, model, profile_name)
        return profile_name


# ── Element builders ─────────────────────────────────────────

def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    e = ET.SubElement(parent, tag)
    e.text = text
    return e


def ET_element(tag: str, children: list[tuple[str, object]]) -> ET.Element:
    e = ET.Element(tag)
    for child_tag, child_text in children:
        c = ET.SubElement(e, child_tag)
        c.text = str(child_text) if child_text is not None else ''
    return e


def _make_models(
    sim: str,
    model: str,
    value: str,
    name: str,
    unit: str,
    device: str,
    profile: Optional[str],
) -> ET.Element:
    e = ET.Element('models')
    for tag, val in [('name', name), ('model', model), ('value', str(value)),
                     ('sim', sim), ('device', device)]:
        c = ET.SubElement(e, tag)
        c.text = str(val) if val is not None else ''
    if unit:
        c = ET.SubElement(e, 'unit')
        c.text = str(unit)
    if profile:
        c = ET.SubElement(e, 'profile')
        c.text = str(profile)
    return e