"""ConfigWriter — all write/erase/profile operations on userconfig.

Every method mutates the user XML tree and persists to disk.
"""
import copy
import datetime
import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional, TYPE_CHECKING

import telemffb.globals as G
import telemffb.xml.match as xmatch

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

        if not is_profile:
            # No profile named. Never persist a row without a <profile>: it
            # belongs to no profile, nothing reads it back, and the importer
            # trips over it. The live path hands us None when the aircraft's
            # profile was resolved before the new-aircraft wizard created
            # it, so resolve it ourselves from what is on disk now; a model
            # with nothing to resolve forks to Auto User like Built-In does.
            cls = self._resolver.get_class_for_sim_model(sim, model)
            profile_name = (self._resolver.get_active_profile_for_model(sim, cls or '', model)
                            or 'Built-In')
            is_profile = True

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
        sim: Optional[str] = None,
    ) -> None:
        """Write or update one override.  With ``sim`` the row belongs to
        that sim only; a row of the same pattern and name that names no
        sim (written before rows carried one) is the one updated, and it
        is stamped as it is touched, so a config migrates one edit at a
        time.  A row stamped for another sim is left alone and a new row
        is written beside it."""
        root = self._store.user_root
        if root is None:
            return
        elem = None
        for candidate in root.findall(f'.//sc_overrides[model="{model}"][name="{name}"]'):
            row_sim = candidate.findtext('sim') or ''
            if not sim or row_sim in ('', sim):
                elem = candidate
                break
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
            if sim:
                stamp = elem.find('sim')
                if stamp is None:
                    stamp = ET.SubElement(elem, 'sim')
                stamp.text = sim
            self._store.write_userconfig()
            return

        children = [('name', name), ('model', model), ('var', var),
                    ('sc_unit', sc_unit), ('scale', str(scale)), ('source', 'user')]
        if sim:
            children.append(('sim', sim))
        root.append(ET_element('sc_overrides', children))
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

    def erase_sc_override_from_xml(self, model: str, name: str, sim: Optional[str] = None) -> None:
        """Remove an override: with ``sim``, the rows for that sim and the
        rows that name none; without, every row of that pattern and name."""
        root = self._store.user_root
        if root is None:
            return
        for elem in root.findall(f'sc_overrides[model="{model}"][name="{name}"]'):
            if not sim or (elem.findtext('sim') or '') in ('', sim):
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

    def discard_user_pattern(self, sim: str, model: str) -> int:
        """Remove every trace of one of the user's patterns: its settings and
        type rows, its profile mapping and its SimConnect overrides, so the
        aircraft falls back to whatever else matches.  Returns the number of
        elements removed."""
        root = self._store.user_root
        if root is None or not model:
            return 0
        gone = 0
        for xpath in (f'.//models[model="{model}"][sim="{sim}"]',
                      f'.//profileMappings[model="{model}"][sim="{sim}"]',
                      f'.//sc_overrides[model="{model}"]'):
            for elem in list(root.findall(xpath)):
                root.remove(elem)
                gone += 1
        if gone:
            self._store.write_userconfig()
        return gone

    def _merge_onto_same_string(self, sim: str, pattern: str) -> dict:
        """Merge a user entry into a built-in that carries the same string.

        Nothing moves: every row is already under that string, and both
        trees resolve as one identity with the user's on top.  What makes
        theirs a competing aircraft of its own is the type row, and what
        makes no sense under a built-in is a ``User Default`` profile.  So
        the type row goes, its notes fold into the base, and the base is
        renamed ``Auto User`` - the profile a slider move would have made
        here - leaving an ordinary User Profile on a built-in.  The values
        the aircraft flies with do not change."""
        root = self._store.user_root
        rows = root.findall(f'models[sim="{sim}"][model="{pattern}"]')
        type_rows = [e for e in rows if e.findtext('name') == 'type']
        base = [e for e in rows if e.findtext('name') != 'type'
                and (e.findtext('profile') or 'User Default') == 'User Default']
        if not type_rows and not base:
            return {}

        taken = {e.findtext('profile') or 'User Default' for e in rows} - {'User Default'}
        label = xmatch.required_literal(pattern)[0].strip(' -_.:') or pattern
        new = 'Auto User'
        if new in taken:
            stem, n = new, 2
            new = f"{stem} ({label})"
            while new in taken:
                new, n = f"{stem} ({label} {n})", n + 1

        notes_carried = ''
        for e in type_rows:
            notes_carried = e.findtext('notes') or notes_carried
            root.remove(e)

        moved = 0
        registered = None
        for e in base:
            prof = e.find('profile')
            if prof is None:
                prof = ET.SubElement(e, 'profile')
            prof.text = new
            if e.findtext('name') == 'profile':
                registered = e
            else:
                moved += 1

        cls = self._resolver.get_class_for_sim_model(sim, pattern) or ''
        if registered is None:
            self.write_models_to_xml(sim, pattern, cls, 'profile',
                                     device=self._store.device, profile_name=new)
            registered = root.find(
                f'models[sim="{sim}"][model="{pattern}"][name="profile"][profile="{new}"]')
        stamp = f"Merged into the built-in {pattern} on {datetime.date.today().isoformat()}."
        if registered is not None:
            notes = registered.find('notes')
            if notes is None:
                notes = ET.SubElement(registered, 'notes')
            carried = [t for t in (notes_carried, notes.text or '') if t and t.strip()]
            notes.text = "\n\n".join(carried + [stamp])

        was_active = None
        for pm in root.findall(f'profileMappings[sim="{sim}"][model="{pattern}"]'):
            was_active = pm.findtext('active_profile') or was_active
        active = new if was_active in (None, '', 'User Default') else was_active
        self._store.write_userconfig()
        self.update_active_profile_entry(sim, cls, pattern, active)
        logging.info(f"Merged {pattern} into the built-in of the same string: "
                     f"User Default became {new!r}, active {active!r}, {moved} row(s)")
        return {'profiles': {'User Default': new}, 'active': active,
                'rows': moved, 'overrides': 0}

    def merge_user_pattern(self, sim: str, old_pattern: str, new_pattern: str,
                           keep: bool = False) -> dict:
        """Merge one of the user's own aircraft profiles into a built-in:
        everything under ``old_pattern`` becomes User Profiles of
        ``new_pattern``, and the aircraft flies with the moved active one.
        The old pattern is removed unless ``keep``: a broad pattern may be
        the only thing naming some other aircraft, so a merge from one of
        those copies and leaves it standing.  Nothing the user set is lost.

        Every profile travels, not only the active one.  ``User Default``,
        the base of an aircraft the user added, means nothing under a
        built-in and becomes ``Auto User``, the profile a slider move would
        have made there; the rest keep their names.  A name the built-in
        already carries stays as it is and the incoming profile is suffixed
        with where it came from.  Overrides travel too, replacing a same-named
        one.  Each moved profile's notes record the pattern it came from, and
        the notes on the old type row go with the base.  The active choice
        carries over.

        When the two patterns are the identical string there is nothing to
        move - both trees already resolve as one identity, the user's rows
        on top - so the merge does the rest in place; see
        ``_merge_onto_same_string``.

        Returns ``{'profiles': {old name: new name}, 'active': str,
        'rows': int, 'overrides': int}``, or ``{}`` with nothing to move."""
        root = self._store.user_root
        if root is None or not old_pattern or not new_pattern:
            return {}
        if old_pattern == new_pattern:
            return self._merge_onto_same_string(sim, old_pattern)
        source = root.findall(f'models[sim="{sim}"][model="{old_pattern}"]')
        if not source:
            return {}

        taken = {e.findtext('profile') or ''
                 for e in root.findall(f'models[sim="{sim}"][model="{new_pattern}"]')}
        label = xmatch.required_literal(old_pattern)[0].strip(' -_.:') or old_pattern
        names: dict[str, str] = {}
        for e in source:
            old = e.findtext('profile') or 'User Default'    # a row with no profile is the base's
            if old in names:
                continue
            new = 'Auto User' if old == 'User Default' else old
            if new in taken:
                stem, n = new, 2
                new = f"{stem} ({label})"
                while new in taken:
                    new, n = f"{stem} ({label} {n})", n + 1
            names[old] = new
            taken.add(new)

        cls = self._resolver.get_class_for_sim_model(sim, new_pattern) or ''
        base_notes, rows, registered = '', 0, {}
        for e in source:
            name = e.findtext('name', '')
            if name == 'type':
                base_notes = e.findtext('notes') or ''
                continue
            moved = copy.deepcopy(e)
            moved.find('model').text = new_pattern
            prof = moved.find('profile')
            if prof is None:
                prof = ET.SubElement(moved, 'profile')
            prof.text = names[e.findtext('profile') or 'User Default']
            root.append(moved)
            if name == 'profile':
                registered[prof.text] = moved
            else:
                rows += 1

        stamp = f"{'Copied' if keep else 'Migrated'} from {old_pattern} on {datetime.date.today().isoformat()}."
        for old, new in names.items():
            row = registered.get(new)
            if row is None:
                self.write_models_to_xml(sim, new_pattern, cls, 'profile',
                                         device=self._store.device, profile_name=new)
                row = root.find(f'models[sim="{sim}"][model="{new_pattern}"][name="profile"][profile="{new}"]')
                if row is None:
                    continue
            notes = row.find('notes')
            if notes is None:
                notes = ET.SubElement(row, 'notes')
            carried = [t for t in (base_notes if old == 'User Default' else '', notes.text or '')
                       if t and t.strip()]
            notes.text = "\n\n".join(carried + [stamp])

        overrides = 0
        for ov in root.findall(f'sc_overrides[model="{old_pattern}"]'):
            for dup in list(root.findall(f'sc_overrides[model="{new_pattern}"][name="{ov.findtext("name", "")}"]')):
                root.remove(dup)
            moved = copy.deepcopy(ov)
            moved.find('model').text = new_pattern
            root.append(moved)
            overrides += 1

        was_active = None
        for pm in root.findall(f'profileMappings[sim="{sim}"][model="{old_pattern}"]'):
            was_active = pm.findtext('active_profile') or was_active
        active = names.get(was_active or 'User Default') or names.get('User Default') or next(iter(names.values()))

        if keep:
            self._store.write_userconfig()
        else:
            self.discard_user_pattern(sim, old_pattern)
        self.update_active_profile_entry(sim, cls, new_pattern, active)
        logging.info(f"{'Copied' if keep else 'Moved'} {old_pattern} onto {new_pattern}: profiles {names}, "
                     f"{rows} row(s), {overrides} override(s), active {active!r}")
        return {'profiles': names, 'active': active, 'rows': rows, 'overrides': overrides}

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
        # Clone models data: the source pattern's own rows, by exact pattern
        model_data, _ = self._resolver.read_models_data('defaults', sim, old_pattern,
                                                         user=False, profile=None, identity=old_pattern)
        for m in model_data:
            self.write_models_to_xml(sim, new_pattern, m['value'], m['name'],
                                     m.get('unit', ''), m.get('device', ''), new_profile)

        user_data, _ = self._resolver.read_models_data('user', sim, old_pattern,
                                                       user=True, profile=old_profile, identity=old_pattern)
        for m in user_data:
            self.write_models_to_xml(sim, new_pattern, m['value'], m['name'],
                                     m.get('unit', ''), m.get('device', ''), new_profile)

        # Clone SC overrides
        for ov in self._resolver.read_sc_overrides(old_pattern, identity=old_pattern):
            self.write_sc_override_to_xml(new_pattern, ov['var'], ov['name'],
                                          ov.get('sc_unit', ''), ov.get('scale', ''),
                                          sim=ov.get('sim') or None)

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