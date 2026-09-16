"""Tests for aircraft profile notes stored in the models tables.

Curated notes live in a <notes> child on the defaults name="type" row;
user notes live in a <notes> child on the userconfig name="profile" row.
The critical property under test is that notes survive the existing
write/consolidate machinery (which rebuilds and dedups the userconfig).
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import telemffb.globals as G
from telemffb import xmlutils

pytestmark = [pytest.mark.unit]

DEFAULTS_PATH = str(Path(__file__).parents[1] / "defaults.xml")


@pytest.fixture
def xml_env(tmp_path):
    """Point xmlutils at a scratch userconfig and the real defaults.xml."""
    userconfig = tmp_path / "userconfig.xml"
    userconfig.write_text("<TelemFFB_v2>\n</TelemFFB_v2>\n", encoding="utf-8")

    saved = (G.userconfig_path, G.defaults_path)
    G.userconfig_path = str(userconfig)
    G.defaults_path = DEFAULTS_PATH
    xmlutils.update_vars('joystick', str(userconfig), DEFAULTS_PATH)
    xmlutils.update_roots()
    yield userconfig
    G.userconfig_path, G.defaults_path = saved


class TestDefaultNotes:
    def test_reads_curated_note_from_defaults(self, xml_env):
        # The F-4E entry in the shipped defaults.xml carries the first curated note
        note = xmlutils.read_default_model_notes('DCS', 'F-4E-45MC')
        assert 'bob-weight' in note

    def test_prefer_pattern_selects_matching_row(self, xml_env):
        note = xmlutils.read_default_model_notes('DCS', 'F-4E-45MC', prefer_pattern='F-4E-45MC')
        assert 'bob-weight' in note

    def test_no_note_for_unknown_aircraft(self, xml_env):
        assert xmlutils.read_default_model_notes('DCS', 'Totally Unknown Aircraft XYZ') == ''

    def test_no_note_for_empty_name(self, xml_env):
        assert xmlutils.read_default_model_notes('DCS', '') == ''


class TestUserNotesRoundTrip:
    SIM = 'DCS'
    MODEL = 'F-4E-45MC'

    def test_write_creates_profile_row_with_note(self, xml_env):
        written = xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'my test note', 'Auto User')
        assert written == 'Auto User'
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Auto User') == 'my test note'

    def test_note_survives_file_round_trip(self, xml_env):
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'line one\nline two', 'Auto User')
        # Re-parse from disk (fresh roots) — newlines and content must survive
        xmlutils.update_roots()
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Auto User') == 'line one\nline two'

    def test_note_survives_settings_write_and_consolidate(self, xml_env):
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'keep me', 'Auto User')
        # A settings write triggers the update path and the consolidate/dedup
        # rebuild; neither may drop the <notes> child.
        xmlutils.write_models_to_xml(self.SIM, self.MODEL, '0.5', 'stick_shaker_intensity',
                                     the_device='joystick', profile_name='Auto User')
        # Also exercise the update path on the profile row itself
        xmlutils.write_models_to_xml(self.SIM, self.MODEL, 'JetAircraft',
                                     the_device='any', setting_name='profile', profile_name='Auto User')
        xmlutils.update_roots()
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Auto User') == 'keep me'

    def test_empty_note_removes_element(self, xml_env):
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'temporary', 'Auto User')
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, '   ', 'Auto User')
        xmlutils.update_roots()
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Auto User') == ''
        root = ET.parse(str(xml_env)).getroot()
        row = root.find(f'models[sim="{self.SIM}"][model="{self.MODEL}"][name="profile"][profile="Auto User"]')
        assert row is not None
        assert row.find('notes') is None

    def test_builtin_profile_redirects_to_auto_user(self, xml_env):
        written = xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'redirected', 'Built-in')
        assert written == 'Auto User'
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Auto User') == 'redirected'

    def test_named_profile_note_is_separate(self, xml_env):
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'auto user note', 'Auto User')
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'race config note', 'Race Config')
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Auto User') == 'auto user note'
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Race Config') == 'race config note'

    def test_update_existing_note(self, xml_env):
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'first', 'Auto User')
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'second', 'Auto User')
        xmlutils.update_roots()
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Auto User') == 'second'

    def test_profile_notes_do_not_cross_profiles(self, xml_env):
        # A note on one profile's row must not be visible through another
        # profile's read — profiles inherit only the type-row tiers.
        xmlutils.write_user_model_notes(self.SIM, self.MODEL, 'auto user only', 'Auto User')
        assert xmlutils.read_user_model_notes(self.SIM, self.MODEL, 'Profile2') == ''


class TestUserDefaultNotes:
    SIM = 'DCS'
    MODEL = 'My Custom Plane.*'

    def _add_user_type_row(self, path, notes=None):
        import xml.etree.ElementTree as ET_
        tree = ET_.parse(str(path))
        root = tree.getroot()
        row = ET_.SubElement(root, 'models')
        for tag, val in (('name', 'type'), ('model', self.MODEL),
                         ('value', 'PropellerAircraft'), ('sim', self.SIM),
                         ('device', 'any')):
            ET_.SubElement(row, tag).text = val
        if notes:
            ET_.SubElement(row, 'notes').text = notes
        tree.write(str(path), 'utf-8')
        xmlutils.update_roots()

    def test_reads_notes_from_user_type_row(self, xml_env):
        self._add_user_type_row(xml_env, notes='user default note')
        assert xmlutils.read_user_default_model_notes(self.SIM, self.MODEL) == 'user default note'

    def test_no_notes_on_type_row(self, xml_env):
        self._add_user_type_row(xml_env)
        assert xmlutils.read_user_default_model_notes(self.SIM, self.MODEL) == ''

    def test_no_type_row_at_all(self, xml_env):
        assert xmlutils.read_user_default_model_notes(self.SIM, 'Nonexistent.*') == ''
