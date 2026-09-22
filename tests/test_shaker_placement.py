"""Where an effect plays on a shaker rig is read from the settings tree:
effect name, to its intensity setting, to that setting's group, to the
group's placement on the running aircraft."""
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

import telemffb.globals as G
from telemffb import shaker_placement as sp
from telemffb.hw.ffb_shaker import DEFAULT_PLACEMENT_DELAY_MS, EVERYWHERE

DEFAULTS_XML = Path(__file__).parents[1] / "defaults.xml"


@pytest.fixture
def tree_index():
    return sp.index_settings(ET.parse(DEFAULTS_XML).getroot())


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    monkeypatch.setattr(sp, '_index', None)
    monkeypatch.setattr(sp, '_categories', {})
    monkeypatch.setattr(G, 'telem_manager', None, raising=False)


class TestCategory:
    """Every effect the translator knows lands in one of the families the
    tree groups its settings into, from the real tree."""

    @pytest.mark.parametrize('name, family', [
        ('buffeting', 'aerodynamics'),
        ('etl0', 'aerodynamics'),
        ('gunfire', 'weapons'),
        ('cm', 'weapons'),
        ('runway_bump0', 'ground'),
        ('touchdown', 'ground'),
        ('nw_shimmy', 'ground'),
        ('prop_rpm0-1', 'mechanical'),
        ('gearmovement0', 'mechanical'),
        ('gforce', 'inertial'),
        ('decel', 'inertial'),
        ('no_such_effect', 'other'),
        ('damper', 'other'),                      # a condition: no family
        (None, 'other'),
        ('', 'other'),
    ])
    def test_families_from_the_tree(self, tree_index, name, family):
        assert sp.category_of(name, tree_index) == family

    def test_every_family_has_a_setting_in_the_tree(self, tree_index):
        for family, setting in sp.CATEGORY_SETTINGS.items():
            assert setting in tree_index, family
            assert tree_index[setting][0] == 'shaker'
        assert sp.DELAY_SETTING in tree_index

    def test_shipped_defaults_match_the_tree(self):
        root = ET.parse(DEFAULTS_XML).getroot()
        values = {e.findtext('name'): e.findtext('value') for e in root.findall('defaults')}
        for setting, choice in sp.DEFAULT_CHOICES.items():
            assert values[setting] == choice, setting
        assert float(values[sp.DELAY_SETTING]) == DEFAULT_PLACEMENT_DELAY_MS

    def test_every_choice_the_tree_offers_is_understood(self):
        from telemffb.hw.ffb_shaker import PLACEMENT_CHOICES
        root = ET.parse(DEFAULTS_XML).getroot()
        for e in root.findall('defaults'):
            if (e.findtext('name') or '').startswith('shaker_placement_') and e.findtext('datatype') == 'list':
                offered = e.findtext('validvalues').split(',')
                assert set(offered) == set(PLACEMENT_CHOICES), e.findtext('name')

    def test_weapons_display_group_wins_over_its_parent(self):
        index = {'flare_intensity': ('mechanical', 'Weapons'),
                 'rumble_intensity': ('mechanical', 'Engine')}
        translate = {'flare': ['Flare', 'flare_intensity'], 'rumble': ['Rumble', 'rumble_intensity']}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sp.EffectTranslator, 'get_translation',
                       classmethod(lambda cls, k: translate.get(k, ['?', ''])))
            assert sp.category_of('flare', index) == 'weapons'
            assert sp.category_of('rumble', index) == 'mechanical'

    def test_a_pattern_setting_matches_by_prefix(self):
        index = {'engine_rumble_lowrpm': ('mechanical', 'Engine')}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sp.EffectTranslator, 'get_translation',
                       classmethod(lambda cls, k: ['Prop', 'engine_rumble_.*']))
            assert sp.category_of('prop_rpm0-1', index) == 'mechanical'

    def test_without_a_readable_tree_everything_is_other(self, monkeypatch):
        monkeypatch.setattr(sp, '_defaults_root', lambda: None)
        assert sp.category_of('buffeting') == 'other'
        assert sp.setting_index() == {}

    def test_the_tree_is_indexed_once_and_categories_remembered(self, monkeypatch):
        calls = []
        root = ET.parse(DEFAULTS_XML).getroot()

        def read_root():
            calls.append(1)
            return root
        monkeypatch.setattr(sp, '_defaults_root', read_root)
        assert sp.category_of('buffeting') == 'aerodynamics'
        assert sp.category_of('gunfire') == 'weapons'
        assert sp.category_of('buffeting') == 'aerodynamics'
        assert len(calls) == 1
        assert sp._categories == {'buffeting': 'aerodynamics', 'gunfire': 'weapons'}


class TestResolve:
    """The placement is the running aircraft's value for the family's
    setting, or the tree's default before an aircraft is loaded."""

    def test_reads_the_aircraft_the_way_other_settings_are_read(self, tree_index, monkeypatch):
        monkeypatch.setattr(sp, '_index', tree_index)
        aircraft = SimpleNamespace(shaker_placement_aerodynamics='seat + floor',
                                   shaker_placement_weapons='floor then seat back',
                                   shaker_placement_delay_ms=90)
        buff = sp.resolve('buffeting', aircraft)
        assert buff.contacts == {'seat', 'floor'} and not buff.delayed
        guns = sp.resolve('gunfire', aircraft)
        assert guns.contacts == {'floor', 'seat back'}
        assert guns.delayed == {'seat back'} and guns.delay_ms == 90

    def test_the_running_aircraft_is_found_through_the_telemetry_manager(self, tree_index, monkeypatch):
        monkeypatch.setattr(sp, '_index', tree_index)
        aircraft = SimpleNamespace(shaker_placement_ground='seat back')
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(currentAircraft=aircraft), raising=False)
        assert sp.resolve('runway_bump0').contacts == {'seat back'}

    def test_defaults_before_an_aircraft_is_loaded(self, tree_index, monkeypatch):
        monkeypatch.setattr(sp, '_index', tree_index)
        assert sp.resolve('buffeting').contacts == {'seat back'}
        rolled = sp.resolve('runway_bump0')
        assert rolled.contacts == {'floor', 'seat back'} and rolled.delayed == {'seat back'}
        assert rolled.delay_ms == DEFAULT_PLACEMENT_DELAY_MS
        assert sp.resolve('prop_rpm0-1').contacts == {'seat'}
        assert sp.resolve('gforce').contacts == {'seat'}
        assert sp.resolve('gunfire').contacts == {'seat back', 'floor'}
        assert sp.resolve('mystery') is EVERYWHERE

    def test_a_missing_or_blank_value_falls_back_to_the_default(self, tree_index, monkeypatch):
        monkeypatch.setattr(sp, '_index', tree_index)
        aircraft = SimpleNamespace(shaker_placement_aerodynamics='', shaker_placement_delay_ms='')
        assert sp.resolve('buffeting', aircraft).contacts == {'seat back'}
        assert sp.resolve('runway_bump0', aircraft).delay_ms == DEFAULT_PLACEMENT_DELAY_MS

    def test_an_unparseable_delay_keeps_the_default(self, tree_index, monkeypatch):
        monkeypatch.setattr(sp, '_index', tree_index)
        aircraft = SimpleNamespace(shaker_placement_delay_ms='soon')
        assert sp.resolve('runway_bump0', aircraft).delay_ms == DEFAULT_PLACEMENT_DELAY_MS

    def test_an_unknown_choice_plays_everywhere(self, tree_index, monkeypatch):
        monkeypatch.setattr(sp, '_index', tree_index)
        aircraft = SimpleNamespace(shaker_placement_weapons='the ceiling')
        assert sp.resolve('gunfire', aircraft) is EVERYWHERE


class TestForget:
    def test_only_a_shaker_is_asked_to_forget(self):
        calls = []
        sp.forget_placements(SimpleNamespace(forget_placements=lambda: calls.append(1)))
        assert calls == [1]
        sp.forget_placements(SimpleNamespace())          # another backend
        sp.forget_placements(None)                        # no device open


class TestScope:
    """The placement settings belong to the shaker's scope of the tree
    and to no axis device."""

    @staticmethod
    def names_for(device):
        from telemffb.xml import XmlConfigManager
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?><TelemFFB/>')
            userconfig_path = f.name
        try:
            mgr = XmlConfigManager(device=device, userconfig_path=userconfig_path,
                                   defaults_path=str(DEFAULTS_XML))
            mgr.store.update_roots()
            return {r['name'] for r in mgr.resolver.read_xml_file('DCS')}
        finally:
            os.unlink(userconfig_path)

    def test_the_shaker_sees_its_placements_and_a_joystick_does_not(self):
        placements = set(sp.CATEGORY_SETTINGS.values()) | {sp.DELAY_SETTING, 'shaker_group'}
        assert placements <= self.names_for('shaker')
        assert not (placements & self.names_for('joystick'))
