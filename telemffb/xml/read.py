"""ConfigResolver — all read/query operations against XML config trees.

Provides the main entry point `resolve()` that performs the full 6-layer override
cascade: sim defaults → class defaults → user sim → user class → model defaults → user model.
"""
import logging
import re
from typing import Optional, TYPE_CHECKING

import telemffb.globals as G
from telemffb.xml import merge as xmmerge
from telemffb.xml.types import (
    ClassDataRow,
    DefaultDataRow,
    ModelDataRow,
    PartialDefaultDataRow,
    PrereqRow,
    ScOverrideRow,
)
from telemffb.xml.store import XmlStore


class ConfigResolver:
    """Read-only query interface over parsed XML config trees."""

    def __init__(self, store: XmlStore) -> None:
        self._store = store

    # ── Main entry point ──────────────────────────────────────

    def resolve(
        self,
        sim: str,
        aircraft_name: str,
        input_modeltype: str = '',
        instance_device: str = '',
        active_profile: Optional[str] = None,
    ) -> tuple[str, str, list[DefaultDataRow]]:
        """Full 6-layer cascade resolution for a single aircraft.

        Returns (model_class, model_pattern, sorted_data).
        """
        if active_profile is None:
            ptrn = self.get_pattern_by_sim_fullname(sim, aircraft_name)
            cls = self.get_class_for_sim_model(sim, ptrn)
            active_profile = self.get_active_profile_for_model(sim, cls or '', ptrn or '')

        dev = instance_device or self._store.device

        # Read model data from defaults and user
        model_data, def_pattern = self._read_models_data(
            'defaults', sim, aircraft_name, False, dev)
        user_model_data, usr_pattern = self._read_models_data(
            'user', sim, aircraft_name, False, dev, user=True, profile=active_profile)

        pattern = def_pattern or usr_pattern
        logging.info("Reading from XML: Pattern Match: %s", pattern)

        # Resolve class
        model_class = input_modeltype
        for m in model_data:
            if m['name'] == 'type':
                model_class = m['value']
                break
        if user_model_data:
            for m in user_model_data:
                if m['name'] == 'type':
                    model_class = m['value']
                    break

        # Layer 1: sim defaults
        defaultdata = self.read_xml_file(sim, dev)

        # Layer 2: class defaults + exclusions
        if model_class:
            craftresult, removal_data = self.read_default_class_data(sim, model_class, dev)
            if removal_data:
                defaultdata = xmmerge.remove_dicts_by_names(defaultdata, removal_data)
            if craftresult:
                defaultdata = xmmerge.update_default_data_with_craft_result(defaultdata, craftresult)

        # Layer 3: user sim overrides
        user_sim = self.read_user_sim_data(sim, dev)
        if user_sim:
            defaultdata = xmmerge.update_data_with_models(defaultdata, user_sim, 'Sim (user)')

        # Layer 4: user class overrides
        if model_class:
            user_class = self.read_user_class_data(sim, model_class, dev)
            if user_class:
                defaultdata = xmmerge.update_data_with_models(
                    defaultdata, user_class, 'Class (user)')

        # Layer 5: model defaults
        defaultdata = xmmerge.update_data_with_models(defaultdata, model_data, 'Model Default')

        # Layer 6: user model overrides
        if user_model_data:
            defaultdata = xmmerge.update_data_with_models(
                defaultdata, user_model_data, 'Model (user)')

        # Filter empty values (except special names)
        defaultdata = [d for d in defaultdata
                       if d['value'] != '' or d['name'] in ('vpconf', 'vne_override')]

        # Apply validvalues overrides
        self._apply_validvalue_overrides(defaultdata, sim, model_class, dev)

        # Prerequisite filtering
        prereq_list = self.read_prereqs()
        xmmerge.check_prereq_value(prereq_list, defaultdata)
        final = xmmerge.eliminate_no_prereq(defaultdata)

        return model_class, pattern, sorted(final, key=lambda x: float(x['order']))

    # ── Read operations ───────────────────────────────────────

    def read_xml_file(self, sim: str, instance_device: str = '') -> list[DefaultDataRow]:
        """Parse sim+device defaults from defaults.xml."""
        dev = instance_device or self._store.device
        root = self._store.defaults_root
        if root is None:
            return []

        data: list[DefaultDataRow] = []
        for elem in root.findall(f'.//defaults[{sim}="true"][{dev}="true"]'):
            # Skip debug-only settings unless debug mode is enabled
            debug_only_elem = elem.find('debug_only')
            if (debug_only_elem is not None and
                    debug_only_elem.text is not None and
                    debug_only_elem.text.strip().lower() == 'true'):
                system_settings = getattr(G, 'system_settings', None)
                is_debug = system_settings.get('debug', False) if system_settings else False
                if not is_debug:
                    continue

            grouping = elem.findtext('grouping', '')
            order_elem = elem.find('order')
            order = order_elem.text if order_elem is not None and order_elem.text is not None else '0'
            name_elem = elem.find('name')
            displayname_elem = elem.find('displayname')
            datatype_elem = elem.find('datatype')

            if name_elem is None or name_elem.text is None:
                continue
            if displayname_elem is None or displayname_elem.text is None:
                continue
            if datatype_elem is None or datatype_elem.text is None:
                continue

            name = name_elem.text
            displayname = displayname_elem.text
            datatype = datatype_elem.text
            exclusive_with = elem.findtext('exclusive_with', '')
            unit = elem.findtext('unit', '')
            value = elem.findtext('value', '')
            validvalues = elem.findtext('validvalues', '')
            info = elem.findtext('info', '')
            prereq = elem.findtext('prereq', '')
            # Cross-tree gates (independent of prereq parentage): render_prereq
            # hides the setting when its condition fails; enable_prereq leaves it
            # visible but disabled. Both evaluated later in SettingsLayout against
            # the resolved values of the referenced bool settings.
            render_prereq = elem.findtext('render_prereq', '')
            enable_prereq = elem.findtext('enable_prereq', '')
            sliderfactor = elem.findtext('sliderfactor', '1')

            has_any = elem.find('any') is not None
            device_text = 'any' if has_any else dev

            data.append({
                'grouping': grouping, 'order': order, 'name': name,
                'displayname': displayname, 'exclusive_with': exclusive_with,
                'value': value, 'unit': unit, 'datatype': datatype,
                'validvalues': validvalues, 'replaced': 'Sim Default',
                'prereq': prereq, 'render_prereq': render_prereq,
                'enable_prereq': enable_prereq, 'info': info,
                'sliderfactor': sliderfactor, 'device_text': device_text,
                'indent': 0,
            })

        return sorted(data, key=lambda x: float(x['order']))

    def read_anydevice_settings(self, sim: str) -> list[str]:
        """Get setting names applicable to any device."""
        root = self._store.defaults_root
        if root is None:
            return []
        result: list[str] = []
        for elem in root.findall(f'.//defaults[{sim}="true"][any="true"]'):
            name = elem.findtext('name')
            if name:
                result.append(name)
        return result

    def read_models(self, sim: str, the_class: str = '') -> list[str]:
        """List all model patterns for a sim/class."""
        dev = self._store.device
        root = self._store.defaults_root
        user_root = self._store.user_root
        models: list[str] = ['']

        def add_patterns(elems):
            for e in elems:
                p = e.findtext('model')
                if p and p not in models:
                    models.append(p)

        if not the_class:
            for xpath in [f'.//models[sim="{sim}"][device="{dev}"]',
                          f'.//models[sim="any"][device="{dev}"]',
                          f'.//models[sim="{sim}"][device="any"]',
                          f'.//models[sim="any"][device="any"]']:
                add_patterns(root.findall(xpath))
                if user_root is not None:
                    add_patterns(user_root.findall(xpath))
        else:
            add_patterns(root.findall(f'.//models[sim="{sim}"][value="{the_class}"]'))
            if user_root is not None:
                add_patterns(user_root.findall(f'.//models[sim="{sim}"][value="{the_class}"]'))

        return sorted(models)

    def read_models_data(
        self,
        which_root: str,
        sim: str,
        full_model_name: str,
        alldevices: bool = False,
        instance_device: str = '',
        user: bool = False,
        profile: Optional[str] = None,
    ) -> tuple[list[ModelDataRow], str]:
        """Extract model-specific config entries by regex matching."""
        return self._read_models_data(which_root, sim, full_model_name, alldevices,
                                      instance_device, user, profile)

    def read_sc_overrides(self, aircraft_name: str) -> list[ScOverrideRow]:
        """Merged SC overrides (defaults + user)."""
        def_ovr = self._read_models_sc_overrides('defaults', aircraft_name, 'default')
        usr_ovr = self._read_models_sc_overrides('user', aircraft_name, 'user')
        return xmmerge.update_sc_overrides_with_user(def_ovr, usr_ovr)

    def read_default_class_data(
        self,
        sim: str,
        cls: str,
        instance_device: str = '',
    ) -> tuple[list[ClassDataRow], Optional[list[str]]]:
        """Class-level defaults + exclusion list."""
        dev = instance_device or self._store.device
        root = self._store.defaults_root
        if root is None:
            return [], None

        class_data: list[ClassDataRow] = []
        xpaths = [
            f'.//classdefaults_{sim}[sim="{sim}"][type="{cls}"][device="{dev}"]',
            f'.//classdefaults_any[sim="any"][type="{cls}"][device="{dev}"]',
            f'.//classdefaults_{sim}[sim="{sim}"][type="{cls}"][device="any"]',
            f'.//classdefaults_any[sim="any"][type="{cls}"][device="any"]',
        ]
        for xp in xpaths:
            for elem in root.findall(xp):
                name_elem = elem.find('name')
                if name_elem is not None and name_elem.text is not None:
                    class_data.append({
                        'name': name_elem.text,
                        'value': elem.findtext('value', ''),
                        'unit': elem.findtext('unit', ''),
                        'replaced': 'Class Default',
                    })

        removal: list[str] = []
        excl_xpaths = [
            f'.//classdefaults_{sim}[sim="{sim}"][type="!{cls}"][device="{dev}"]',
            f'.//classdefaults_any[sim="any"][type="!{cls}"][device="{dev}"]',
            f'.//classdefaults_{sim}[sim="{sim}"][type="!{cls}"][device="any"]',
            f'.//classdefaults_any[sim="any"][type="!{cls}"][device="any"]',
        ]
        for xp in excl_xpaths:
            for elem in root.findall(xp):
                n = elem.findtext('name')
                if n:
                    removal.append(n)

        return class_data, removal or None

    def read_user_sim_data(self, sim: str, instance_device: str = '') -> list[PartialDefaultDataRow]:
        """User sim-level overrides."""
        dev = instance_device or self._store.device
        root = self._store.user_root
        if root is None:
            return []

        data: list[PartialDefaultDataRow] = []
        xpaths = [
            f'.//simSettings[sim="{sim}"][device="{dev}"]',
            f'.//simSettings[sim="any"][device="{dev}"]',
            f'.//simSettings[sim="{sim}"][device="any"]',
            f'.//simSettings[sim="any"][device="any"]',
        ]
        seen: set[str] = set()
        for xp in xpaths:
            for elem in root.findall(xp):
                name = elem.findtext('name')
                if name and name not in seen:
                    seen.add(name)
                    data.append({
                        'name': name,
                        'value': elem.findtext('value', ''),
                        'unit': elem.findtext('unit', ''),
                        'replaced': 'Sim (user)',
                    })
        return data

    def read_user_class_data(
        self,
        sim: str,
        crafttype: str,
        instance_device: str = '',
    ) -> list[PartialDefaultDataRow]:
        """User class-level overrides with regex matching on type."""
        dev = instance_device or self._store.device
        root = self._store.user_root
        if root is None:
            return []

        data: list[PartialDefaultDataRow] = []
        xpaths = [
            f'.//classSettings[sim="{sim}"][device="{dev}"]',
            f'.//classSettings[sim="any"][device="{dev}"]',
            f'.//classSettings[sim="{sim}"][device="any"]',
            f'.//classSettings[sim="any"][device="any"]',
        ]
        seen: set[str] = set()
        for xp in xpaths:
            for elem in root.findall(xp):
                type_val = elem.findtext('type', '')
                if re.match(type_val, crafttype) or type_val == crafttype:
                    name = elem.findtext('name')
                    if name and name not in seen:
                        seen.add(name)
                        data.append({
                            'name': name,
                            'value': elem.findtext('value', ''),
                            'unit': elem.findtext('unit', ''),
                            'replaced': 'Class (user)',
                        })
        return data

    def read_user_models(
        self,
        sim: str,
        cls: str,
        default_only: bool = False,
        user_only: bool = True,
        both: bool = False,
    ) -> list[tuple[str, ...]]:
        """List user-created models."""
        root = self._store.defaults_root
        user_root = self._store.user_root
        result: list[tuple[str, ...]] = []

        if default_only or both:
            for elem in root.findall('.//models'):
                if (elem.findtext('sim') == sim and elem.findtext('name') == 'type'
                        and elem.findtext('value') == cls):
                    result.append((elem.findtext('model', ''), 'Built-In'))
            if default_only:
                return sorted(result)

        if user_only or both and user_root is not None:
            for elem in user_root.findall('.//models'):
                if (elem.findtext('sim') == sim and elem.findtext('value') == cls
                        and elem.findtext('name') in ('type', 'profile')):
                    entry = (elem.findtext('model', ''), elem.findtext('profile', ''))
                    if entry not in result:
                        result.append(entry)
        return result

    def read_prereqs(self) -> list[PrereqRow]:
        """Scan userconfig for unique prereq strings with counts."""
        root = self._store.user_root
        if root is None:
            return []
        data: list[PrereqRow] = []
        for elem in root.findall('.//defaults'):
            if elem.find('name') is None and elem.find('order') is None and elem.find('datatype') is None:
                continue
            prereq = elem.findtext('prereq', '')
            found = False
            for d in data:
                if d['prereq'] == prereq:
                    d['count'] += 1
                    found = True
                    break
            if not found and prereq:
                data.append({'prereq': prereq, 'value': 'False', 'count': 1})
        return data

    # ── Lookup helpers ────────────────────────────────────────

    def get_sims(self) -> list[str]:
        """List all sim identifiers."""
        root = self._store.defaults_root
        if root is None:
            return []
        sims: list[str] = []
        for e in root.findall('.//sims'):
            s = e.findtext('sim')
            if s:
                sims.append(s)
        return sims

    def get_classes_for_sim(self, sim: str) -> list[str]:
        """List all classes for a sim."""
        root = self._store.defaults_root
        if root is None:
            return []
        classes: list[str] = []
        for e in root.findall(f'.//classes[sim="{sim}"]'):
            cn = e.findtext('class_name')
            if cn:
                classes.append(cn)
        return classes

    def get_pattern_by_sim_fullname(self, sim: str, full_name: str) -> Optional[str]:
        """Regex-match full name to config pattern; user first, then defaults."""
        def matches(pattern: str) -> bool:
            return bool(re.match(pattern, full_name) or pattern == full_name)

        user_root = self._store.user_root
        if user_root is not None:
            for elem in user_root.findall(f'models[sim="{sim}"][name="type"]'):
                p = elem.findtext('model')
                if p and matches(p):
                    return p

        root = self._store.defaults_root
        if root is not None:
            for elem in root.findall(f'models[sim="{sim}"][name="type"]'):
                p = elem.findtext('model')
                if p and matches(p):
                    return p
        return None

    def get_class_for_sim_model(self, sim: str, model: Optional[str]) -> Optional[str]:
        """Find aircraft class for sim+model; user first, then defaults."""
        if model is None:
            return None
        user_root = self._store.user_root
        if user_root is not None:
            entry = user_root.find(f'models[sim="{sim}"][model="{model}"][name="type"]')
            if entry is not None:
                return entry.findtext('value')

        root = self._store.defaults_root
        if root is not None:
            entry = root.find(f'models[sim="{sim}"][model="{model}"][name="type"]')
            if entry is not None:
                return entry.findtext('value')
        return None

    def get_active_profile_for_model(self, sim: str, cls: str, model: str) -> Optional[str]:
        """Resolve active profile with 4-step priority."""
        user_root = self._store.user_root

        # 1. profileMappings
        if user_root is not None:
            for p in user_root.findall('.//profileMappings'):
                if (p.findtext('model') == model and p.findtext('sim') == sim
                        and p.findtext('cls') == cls):
                    return p.findtext('active_profile')

        # 2. user models with profile tag
        if user_root is not None:
            for e in user_root.findall('models[name="type"]'):
                if e.findtext('model') == model and e.findtext('sim') == sim:
                    prof = e.findtext('profile')
                    if prof:
                        return prof

        # 3. defaults
        root = self._store.defaults_root
        if root is not None:
            for e in root.findall('models[name="type"]'):
                if e.findtext('model') == model and e.findtext('sim') == sim:
                    return "Built-In"

        return None

    def get_available_profiles(self, sim: str, cls: str, model: str) -> list[str]:
        """List available profiles for sim/class/model."""
        profiles: list[str] = []
        user_root = self._store.user_root
        if user_root is not None:
            for e in user_root.findall(f'.//models[sim="{sim}"][model="{model}"]'):
                p = e.findtext('profile')
                if p and p not in profiles:
                    profiles.append(p)

        root = self._store.defaults_root
        if root is not None:
            elem = root.find(f'.//models[sim="{sim}"][model="{model}"][name="type"][value="{cls}"]')
            if elem is not None:
                profiles.append('Built-In')
        return profiles

    # ── Raw XPath helpers (for ProfileManager etc.) ───────────

    def get_sim_defaults(self, sim: str, dev_type: str) -> list:
        root = self._store.user_root
        return root.findall(f'.//simSettings[sim="{sim}"][device="{dev_type}"]') if root else []

    def get_class_defaults(self, sim: str, cls: str, dev_type: str) -> list:
        root = self._store.user_root
        return root.findall(f'.//classSettings[sim="{sim}"][type="{cls}"][device="{dev_type}"]') if root else []

    def get_model_profile(self, sim: str, model: str, profile: str, dev_type: str) -> list:
        root = self._store.user_root
        xp = f'.//models[sim="{sim}"][model="{model}"][profile="{profile}"][device="{dev_type}"]'
        return root.findall(xp) if root else []

    def get_sc_override(self, model: str) -> list:
        root = self._store.user_root
        return root.findall(f'.//sc_overrides[model="{model}"]') if root else []

    def get_model_type(self, sim: str, model: str, cls: str):
        root = self._store.user_root
        return root.find(f'.//models[sim="{sim}"][model="{model}"][name="type"][value="{cls}"]') if root else None

    # ── Model notes ────────────────────────────────────────────

    def read_default_model_notes(
        self,
        sim: str,
        full_model_name: str,
        prefer_pattern: str = '',
    ) -> str:
        """Return curated <notes> text from defaults <models> for an aircraft.

        Notes live on the name="type" row of a curated model entry. The row is
        located by regex-matching each type row's model pattern against the full
        aircraft name; if prefer_pattern is supplied and one of the matching rows
        uses exactly that pattern, that row wins so the note always corresponds to
        the "Matched Model" shown.

        Args:
            sim: Simulator name (e.g. "DCS", "MSFS").
            full_model_name: Full aircraft name as received in telemetry.
            prefer_pattern: Pattern to prefer among multiple matches.

        Returns:
            The curated notes text, or '' if none.
        """
        if not full_model_name:
            return ''
        notes = ''
        root = self._store.defaults_root
        if root is None:
            return ''
        for elem in root.findall(f'models[sim="{sim}"][name="type"]'):
            pattern = elem.findtext('model') or ''
            if not pattern:
                continue
            if re.match(pattern, full_model_name) or pattern == full_model_name:
                row_notes = elem.findtext('notes') or ''
                if prefer_pattern and pattern == prefer_pattern:
                    return row_notes
                if row_notes:
                    notes = row_notes
        return notes

    def read_user_default_model_notes(self, sim: str, model: str) -> str:
        """Return user's <notes> text on a name="type" row in userconfig.

        These notes are profile-independent and inherited read-only by every
        profile of the model, alongside the curated defaults notes.

        Args:
            sim: Simulator name.
            model: The exact model pattern.

        Returns:
            The notes text, or '' if none.
        """
        if not model:
            return ''
        root = self._store.user_root
        if root is None:
            return ''
        for elem in root.findall(f'models[sim="{sim}"][model="{model}"][name="type"]'):
            notes = elem.findtext('notes') or ''
            if notes:
                return notes
        return ''

    def read_user_model_notes(self, sim: str, model: str, profile: str) -> str:
        """Return user's <notes> on the name="profile" row for a model+profile.

        Args:
            sim: Simulator name.
            model: The exact model pattern the profile row was written with.
            profile: Profile name (e.g. "Auto User").

        Returns:
            The notes text, or '' if none.
        """
        if not model or not profile:
            return ''
        root = self._store.user_root
        if root is None:
            return ''
        elem = root.find(
            f'models[sim="{sim}"][model="{model}"][name="profile"][profile="{profile}"]')
        if elem is None:
            return ''
        return elem.findtext('notes') or ''

    # ── Internal helpers ──────────────────────────────────────

    def _read_models_data(
        self,
        which_root: str,
        sim: str,
        full_model_name: str,
        alldevices: bool = False,
        instance_device: str = '',
        user: bool = False,
        profile: Optional[str] = None,
    ) -> tuple[list[ModelDataRow], str]:
        root = self._store.user_root if which_root == 'user' else self._store.defaults_root
        if root is None:
            return [], ''

        if which_root not in ('user', 'defaults'):
            raise ValueError(f"read_models_data called with invalid root object {which_root}")

        if profile is None:
            profile = getattr(G, 'settings_mgr', None)
            profile = profile.active_profile if profile else None

        dev = instance_device or self._store.device
        # Only add profile filter if user=True and profile is set (non-empty)
        profile_match = f"[profile='{profile}']" if (user and profile) else ''

        if alldevices:
            any_models = root.findall(f'.//models[sim="any"]{profile_match}')
            all_models = root.findall(f'.//models[sim="{sim}"]{profile_match}')
        else:
            any_models = (root.findall(f'.//models[sim="{sim}"][device="any"]{profile_match}')
                          + root.findall(f'.//models[sim="any"][device="any"]{profile_match}'))
            all_models = (root.findall(f'.//models[sim="{sim}"][device="{dev}"]{profile_match}')
                          + root.findall(f'.//models[sim="any"][device="{dev}"]{profile_match}'))

        model_dict: dict[tuple[Optional[str], Optional[str]], object] = {}
        for e in any_models:
            model_dict[(e.findtext('model'), e.findtext('name'))] = e
        for e in all_models:
            model_dict[(e.findtext('model'), e.findtext('name'))] = e

        data: list[ModelDataRow] = []
        found_pattern = ''
        for e in model_dict.values():
            pattern = e.findtext('model', '')
            if pattern and (re.match(pattern, full_model_name) or pattern == full_model_name):
                data.append({
                    'name': e.findtext('name', ''),
                    'value': e.findtext('value', ''),
                    'unit': e.findtext('unit', ''),
                    'device': e.findtext('device', ''),
                })
                found_pattern = pattern
        return data, found_pattern

    def _read_models_sc_overrides(
        self,
        which_root: str,
        full_model_name: str,
        source: str,
    ) -> list[ScOverrideRow]:
        root = self._store.user_root if which_root == 'user' else self._store.defaults_root
        if root is None:
            return []
        data: list[ScOverrideRow] = []
        for elem in root.findall('.//sc_overrides'):
            pattern = elem.findtext('model', '')
            if pattern and (re.match(pattern, full_model_name) or pattern == full_model_name):
                data.append({
                    'name': elem.findtext('name', ''),
                    'var': elem.findtext('var', ''),
                    'sc_unit': elem.findtext('sc_unit', ''),
                    'scale': elem.findtext('scale', ''),
                    'source': source,
                })
        return data

    def _apply_validvalue_overrides(
        self,
        data_list: list[DefaultDataRow],
        sim: str,
        model_class: str,
        instance_device: str,
    ) -> None:
        dev = instance_device or self._store.device
        root = self._store.defaults_root
        if root is None:
            return
        # The findall carries no per-item predicate, so its result is identical
        # on every pass; hoisting it out of the per-item loop turns one full-tree
        # walk per setting into one full-tree walk per resolve().
        overrides = root.findall('.//validvalues_overrides')
        for item in data_list:
            for ov in overrides:
                if (ov.findtext('name') == item['name']
                        and ov.findtext('sim') == sim
                        and ov.findtext('class') == model_class
                        and (ov.findtext('device') == dev or ov.findtext('device') == 'any')):
                    item['validvalues'] = ov.findtext('validvalues', '')