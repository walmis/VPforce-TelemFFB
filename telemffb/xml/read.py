"""ConfigResolver — all read/query operations against XML config trees.

Provides the main entry point `resolve()` that performs the full 6-layer override
cascade: sim defaults → class defaults → user sim → user class → model defaults → user model.
"""
import logging
import re
from . import match as xmatch
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


#: Settings whose visibility is an application policy question rather
#: than an XML one - see ConfigResolver's ``hidden`` parameter.
POLICY_GATED = ('device_group', 'joystick_device')


class ConfigResolver:
    """Read-only query interface over parsed XML config trees."""

    def __init__(self, store: XmlStore, hidden=None) -> None:
        """``hidden`` is an optional predicate, ``name -> bool``, asked
        about the settings in POLICY_GATED: True drops the row from
        every read, here and in the cascade below.

        Injected rather than imported: whether a setting applies can
        depend on application state this layer knows nothing about (the
        per-aircraft device selection means nothing until a second
        joystick is configured), and a parser that reached up for that
        answer would invert the dependency and close an import cycle.
        Default is permissive - nothing is hidden.
        """
        self._store = store
        self._hidden = hidden
        self._shadow_logged: set = set()   # (sim, aircraft) already warned about

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
        ptrn = self.get_pattern_by_sim_fullname(sim, aircraft_name)
        if active_profile is None:
            cls = self.get_class_for_sim_model(sim, ptrn)
            active_profile = self.get_active_profile_for_model(sim, cls or '', ptrn or '')

        dev = instance_device or self._store.device

        # Read model data from defaults and user: the identity's rows only
        model_data, def_pattern = self._read_models_data(
            'defaults', sim, aircraft_name, False, dev, identity=ptrn)
        user_model_data, usr_pattern = self._read_models_data(
            'user', sim, aircraft_name, False, dev, user=True, profile=active_profile,
            identity=ptrn)

        # The identity is the most specific type row in either tree, the
        # shipped file winning a tie; settings are written against it and
        # the class comes from it.  With no type row at all, the most
        # specific settings-only pattern stands in.
        pattern = ptrn or xmatch.best_pattern([p for p in (usr_pattern, def_pattern) if p], aircraft_name) or ''
        self._log_pattern_match(sim, aircraft_name, pattern)
        self._warn_collision(sim, aircraft_name)

        # The class belongs to the pattern that named the aircraft; the
        # caller's hint stands only when nothing did.
        model_class = self.get_class_for_sim_model(sim, pattern) if pattern else None
        if not model_class:
            model_class = input_modeltype

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

            # A policy-gated setting the application says does not
            # apply is dropped here, at the one read the form AND
            # runtime resolution both go through.
            if (name in POLICY_GATED and self._hidden is not None
                    and self._hidden(name)):
                continue
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
        identity: Optional[str] = None,
    ) -> tuple[list[ModelDataRow], str]:
        """Extract model-specific config entries by regex matching."""
        return self._read_models_data(which_root, sim, full_model_name, alldevices,
                                      instance_device, user, profile, identity)

    def read_sc_overrides(self, aircraft_name: str, identity: Optional[str] = None,
                          sim: Optional[str] = None) -> list[ScOverrideRow]:
        """The SimConnect overrides of the pattern that names the aircraft,
        like any other setting: the shipped ones for that pattern, the
        user's under the same pattern replacing them by name, and nothing
        from any other pattern.  ``identity`` is that pattern when the
        caller knows it; otherwise it is resolved for ``sim`` (the current
        one by default).  With no type row naming the aircraft, the most
        specific override pattern stands in, as a settings-only pattern
        does for settings."""
        if not sim:
            current = getattr(getattr(G, 'settings_mgr', None), 'current_sim', None)
            sim = current if isinstance(current, str) and current else 'MSFS'
        def_rows = self._read_models_sc_overrides('defaults', aircraft_name, 'default', sim)
        usr_rows = self._read_models_sc_overrides('user', aircraft_name, 'user', sim)
        if not identity:
            identity = (self.get_pattern_by_sim_fullname(sim, aircraft_name)
                        or xmatch.best_pattern([p for p, _ in def_rows + usr_rows], aircraft_name))
        return xmmerge.update_sc_overrides_with_user([r for p, r in def_rows if p == identity],
                                                     [r for p, r in usr_rows if p == identity])

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

    def _tree_type_patterns(self, root, sim: str) -> list[str]:
        if root is None:
            return []
        return [p for p in (e.findtext('model') for e in root.findall(f'models[sim="{sim}"][name="type"]')) if p]

    def _type_patterns(self, sim: str, user_first: bool = False) -> list[str]:
        """Every model pattern that names an aircraft type, each tree in
        document order.  The shipped file comes first so that it wins a
        tie: a curated profile arriving for an aircraft the user already
        covered is meant to be noticed, not shadowed.  ``user_first`` is
        the order the rule before ranking used, kept for its log line."""
        trees = (self._store.defaults_root, self._store.user_root)
        if user_first:
            trees = trees[::-1]
        return [p for root in trees for p in self._tree_type_patterns(root, sim)]

    def get_pattern_by_sim_fullname(self, sim: str, full_name: str) -> Optional[str]:
        """The pattern that names this aircraft: the most specific match in
        either tree, the user tree winning a tie (see xml.match)."""
        return xmatch.best_pattern(self._type_patterns(sim), full_name)

    def first_match_pattern(self, sim: str, full_name: str) -> Optional[str]:
        """What the rule before ranking would have chosen: the first match
        in document order, user tree first.  Kept for the comparison log."""
        return xmatch.first_match(self._type_patterns(sim, user_first=True), full_name)

    def display_names(self, names) -> dict:
        """Setting name -> the label the UI shows for it, for the settings
        that declare one.  Names with no <defaults> row map to themselves."""
        wanted = set(names)
        out = {n: n for n in wanted}
        root = self._store.defaults_root
        if root is None:
            return out
        for e in root.findall('defaults'):
            n = e.findtext('name')
            if n in wanted:
                label = e.findtext('displayname')
                if label:
                    out[n] = label
        return out

    def is_user_pattern(self, sim: str, pattern: str) -> bool:
        """Whether the user config defines this pattern as an aircraft type."""
        root = self._store.user_root
        if root is None or not pattern:
            return False
        return root.find(f'models[sim="{sim}"][model="{pattern}"][name="type"]') is not None

    def collision(self, sim: str, name: str) -> Optional[dict]:
        """The one situation the merge offer exists for: a type pattern of
        the user's and a shipped one both match this aircraft.  Returns the
        most specific of each, which of them names the aircraft, and
        whether the two claim exactly the same aircraft; None otherwise."""
        user = xmatch.best_pattern(self._tree_type_patterns(self._store.user_root, sim), name)
        curated = xmatch.best_pattern(self._tree_type_patterns(self._store.defaults_root, sim), name)
        if not user or not curated:
            return None
        winner = 'user' if xmatch.specificity(user, name) > xmatch.specificity(curated, name) else 'curated'
        return {'user': user, 'curated': curated, 'winner': winner,
                'same_claim': xmatch.same_claim(user, curated)}

    def curated_rows_for_fingerprint(self, sim: str, pattern: str) -> list:
        """What a shipped pattern does, as plain tuples for a fingerprint:
        its settings rows, the class on its type row, and its SimConnect
        overrides for this sim.  Notes are left out, so rewording one is
        not a change in what the profile does."""
        root = self._store.defaults_root
        if root is None or not pattern:
            return []
        rows = [('setting', e.findtext('name', ''), e.findtext('value', ''),
                 e.findtext('unit', ''), e.findtext('device', ''))
                for e in root.findall(f'models[sim="{sim}"][model="{pattern}"]')]
        rows += [('override', e.findtext('name', ''), e.findtext('var', ''),
                  e.findtext('sc_unit', ''), e.findtext('scale', ''))
                 for e in root.findall(f'sc_overrides[model="{pattern}"]')
                 if (e.findtext('sim') or '') in ('', sim)]
        return rows

    def user_rows_by_profile(self, sim: str, pattern: str) -> list:
        """(profile, setting, value with unit, device) for every settings
        row the user holds under a pattern, every profile and device: what
        a merge carries across.  Type and profile rows are structure, not
        settings, and are left out."""
        root = self._store.user_root
        if root is None or not pattern:
            return []
        out = []
        for e in root.findall(f'models[sim="{sim}"][model="{pattern}"]'):
            name = e.findtext('name', '') or ''
            if name in ('', 'type', 'profile'):
                continue
            out.append((e.findtext('profile') or 'User Default', name,
                        (e.findtext('value', '') or '') + (e.findtext('unit', '') or ''),
                        e.findtext('device', '') or ''))
        return out

    @staticmethod
    def _same_value(a: Optional[str], b: Optional[str]) -> bool:
        """Whether two stored values mean the same thing.  Equal as written
        counts, and so does equal once each is read with its unit, so a row
        holding 10kt does not read as a disagreement with one holding
        5.1444m/s.  The tolerance covers that conversion's own rounding and
        nothing wider: a value a user rounded by hand is a real difference,
        small enough for them to dismiss at a glance once they see both."""
        from telemffb import utils
        if (a or '') == (b or ''):
            return True
        na, nb = utils.to_number(a or ''), utils.to_number(b or '')
        numeric = [x for x in (na, nb) if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if len(numeric) == 2:
            return abs(na - nb) <= 1e-4 * max(1.0, abs(na), abs(nb))
        return na == nb

    def merge_preview(self, sim: str, name: str, user_pattern: str, curated_pattern: str,
                      instance_device: str = '') -> dict:
        """The two sides of the merge and its result, entry by entry: every
        setting either pattern holds, then the SimConnect overrides of both.

        Each entry carries ``built_in`` and ``yours`` - what each side holds,
        or None where it holds nothing - and ``after``, the result, which is
        the built-in with the user's active profile laid over it.  Reading
        the two side by side is what makes a merge legible: a row where they
        differ is a ``conflict``, and there the user's value stands, which is
        what a User Profile means everywhere else.  ``changes`` is whether the
        result differs from what the aircraft flies with today, which is only
        the winning side's rows.

        Settings and overrides alike follow the identity: only the naming
        pattern's apply now, both apply after (theirs on top)."""
        dev = instance_device or self._store.device
        cls = self.get_class_for_sim_model(sim, user_pattern) or ''
        active = self.get_active_profile_for_model(sim, cls, user_pattern) or 'User Default'
        curated, _ = self._read_models_data('defaults', sim, name, False, dev, identity=curated_pattern)
        mine, _ = self._read_models_data('user', sim, name, False, dev, user=True,
                                         profile=active, identity=user_pattern)
        c = {r['name']: (r['value'] or '') + (r['unit'] or '') for r in curated if r['name'] not in ('type', 'profile')}
        u = {r['name']: (r['value'] or '') + (r['unit'] or '') for r in mine if r['name'] not in ('type', 'profile')}
        same = user_pattern == curated_pattern
        theirs_wins = xmatch.specificity(user_pattern, name) > xmatch.specificity(curated_pattern, name)
        now = {**c, **u} if same else (dict(u) if theirs_wins else dict(c))
        after = {**c, **u}

        def rows(kind, now_, after_, theirs, built_in):
            out = []
            for n in sorted(set(now_) | set(after_)):
                out.append({
                    'kind': kind, 'name': n,
                    'built_in': built_in.get(n), 'yours': theirs.get(n),
                    'after': after_.get(n),
                    'changes': now_.get(n) != after_.get(n),
                    'conflict': (n in theirs and n in built_in
                                 and not self._same_value(theirs[n], built_in[n])),
                })
            return out

        c_ov = {r['name']: r['var'] for p, r in self._read_models_sc_overrides('defaults', name, 'default', sim)
                if p == curated_pattern}
        u_ov = {r['name']: r['var'] for p, r in self._read_models_sc_overrides('user', name, 'user', sim)
                if p == user_pattern}
        now_ov = {**c_ov, **u_ov} if same else (dict(u_ov) if theirs_wins else dict(c_ov))
        after_ov = {**c_ov, **u_ov}

        entries = (rows('setting', now, after, u, c)
                   + rows('override', now_ov, after_ov, u_ov, c_ov))
        # What each column stands for, and which side the aircraft is flying
        # on today: the built-in, theirs, or both where one string carries a
        # row in each tree and the user's sits on top.
        in_effect = 'both' if same else ('yours' if theirs_wins else 'built-in')
        under_user = {e.findtext('profile') or 'User Default'
                      for e in (self._store.user_root.findall(f'models[sim="{sim}"][model="{user_pattern}"]')
                                if self._store.user_root is not None else [])
                      if e.findtext('name') != 'type'} or {'User Default'}
        under_curated = {e.findtext('profile') or 'User Default'
                         for e in (self._store.user_root.findall(f'models[sim="{sim}"][model="{curated_pattern}"]')
                                   if self._store.user_root is not None else [])
                         if e.findtext('name') != 'type'}
        renamed = xmmerge.merged_profile_names(
            sorted(under_user), under_curated if not same else under_user,
            xmatch.required_literal(user_pattern)[0].strip(' -_.:') or user_pattern, same_string=same)
        return {
            'active_profile': active,
            'built_in_pattern': curated_pattern,
            'your_pattern': user_pattern,
            'your_profile': active,
            'after_pattern': curated_pattern,
            'after_profile': renamed.get(active, active),
            'in_effect': in_effect,
            'entries': entries,
            'gains': sum(1 for e in entries if e['changes'] and e['yours'] is None),
            'restores': sum(1 for e in entries if e['changes'] and e['yours'] is not None),
            'keeps': sum(1 for e in entries if not e['changes'] and e['yours'] is not None),
            'conflicts': sum(1 for e in entries if e['conflict']),
            # Every profile registered under theirs moves, settings or not, and
            # each is named as it arrives: "User Default" is the base of an
            # aircraft the user added and cannot exist under a built-in.
            'other_profiles': sorted(renamed.get(p, p) for p in under_user - {active}),
            'curated_notes': self.read_default_model_notes(sim, name, prefer_pattern=curated_pattern),
        }

    def _warn_collision(self, sim: str, name: str) -> None:
        if (sim, name) in self._shadow_logged:
            return
        col = self.collision(sim, name)
        if not col:
            return
        self._shadow_logged.add((sim, name))
        logging.warning("%s is matched by both %s (user config) and %s (defaults.xml); %s names it. "
                        "The main window offers to merge them.", name, col['user'], col['curated'],
                        col['user'] if col['winner'] == 'user' else col['curated'])

    def _log_pattern_match(self, sim: str, name: str, pattern: str) -> None:
        legacy = self.first_match_pattern(sim, name)
        if legacy and legacy != pattern:
            logging.info("Reading from XML: Pattern Match: %s (the first-match rule would have chosen %s)",
                         pattern, legacy)
        else:
            logging.info("Reading from XML: Pattern Match: %s", pattern)

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
            if xmatch.pattern_matches(pattern, full_model_name):
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
        identity: Optional[str] = None,
    ) -> tuple[list[ModelDataRow], str]:
        """The rows of the one pattern that applies: ``identity``, the type
        row that names the aircraft, when given; else the most specific
        matching pattern in this tree.  User rows are limited to
        ``profile``.  Nothing from any other matching pattern: a profile
        owns its values and does not follow the pattern it was forked from.
        Returns the rows and the pattern they belong to ('' when none)."""
        root = self._store.user_root if which_root == 'user' else self._store.defaults_root
        if root is None:
            return [], ''

        if which_root not in ('user', 'defaults'):
            raise ValueError(f"read_models_data called with invalid root object {which_root}")

        if profile is None:
            profile = getattr(G, 'settings_mgr', None)
            profile = profile.active_profile if profile else None

        dev = instance_device or self._store.device
        # The profile filter belongs in the query.  The dedup below keys on
        # (pattern, setting) alone, so a row from another profile under the
        # same pattern would overwrite the active profile's before any later
        # filter could see it, and the setting would fall to the sim default.
        profile_match = f'[profile="{profile}"]' if (user and profile) else ''

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

        ranked = xmatch.rank_matches(
            ((e.findtext('model', ''), e) for e in model_dict.values()),
            full_model_name)
        if identity:
            winner = identity if any(p == identity for p, _ in ranked) else ''
        else:
            winner = ranked[-1][0] if ranked else ''
        data: list[ModelDataRow] = []
        for pattern, e in ranked:
            if pattern != winner:
                continue
            data.append({
                'name': e.findtext('name', ''),
                'value': e.findtext('value', ''),
                'unit': e.findtext('unit', ''),
                'device': e.findtext('device', ''),
            })
        return data, winner

    def _read_models_sc_overrides(
        self,
        which_root: str,
        full_model_name: str,
        source: str,
        sim: Optional[str] = None,
    ) -> list[tuple[str, ScOverrideRow]]:
        """Every matching override row with its pattern, least specific
        first.  A row that names a sim belongs to that sim only; one that
        names none - every row written before rows carried a sim - belongs
        to any."""
        root = self._store.user_root if which_root == 'user' else self._store.defaults_root
        if root is None:
            return []
        rows = [(elem.findtext('model', ''), elem) for elem in root.findall('.//sc_overrides')
                if not sim or (elem.findtext('sim') or '') in ('', sim)]
        ranked = xmatch.rank_matches(rows, full_model_name)
        return [(pattern, {
            'name': elem.findtext('name', ''),
            'var': elem.findtext('var', ''),
            'sc_unit': elem.findtext('sc_unit', ''),
            'scale': elem.findtext('scale', ''),
            'source': source,
            'sim': elem.findtext('sim', '') or '',
        }) for pattern, elem in ranked]

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