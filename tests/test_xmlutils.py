"""Tests for xmlutils.py — XML parser, config read/write, and override cascade."""
from __future__ import annotations

import os
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import telemffb.globals as G
from telemffb import xmlutils


# ─────────────────────────────────────────────────────────────
# Fixtures: minimal XML files that exercise every code path
# ─────────────────────────────────────────────────────────────

MINIMAL_DEFAULTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<TelemFFB>
  <defaults>
    <datatype>n_float</datatype>
    <grouping>Basic</grouping>
    <order>100.0</order>
    <name>aileron_expo</name>
    <displayname>Aileron Expo</displayname>
    <MSFS>true</MSFS>
    <XPLANE>false</XPLANE>
    <DCS>true</DCS>
    <joystick>true</joystick>
    <pedals>false</pedals>
    <value>0.3</value>
    <unit></unit>
  </defaults>
  <defaults>
    <datatype>n_float</datatype>
    <grouping>Basic</grouping>
    <order>200.0</order>
    <name>elevator_expo</name>
    <displayname>Elevator Expo</displayname>
    <MSFS>true</MSFS>
    <DCS>true</DCS>
    <joystick>true</joystick>
    <any>true</any>
    <value>0.5</value>
  </defaults>
  <defaults>
    <datatype>list</datatype>
    <grouping>Basic</grouping>
    <order>50.0</order>
    <name>basic_group</name>
    <displayname>Basic Settings</displayname>
    <any>true</any>
    <MSFS>true</MSFS>
    <joystick>true</joystick>
    <value>true</value>
  </defaults>
  <defaults>
    <datatype>n_float</datatype>
    <grouping>Advanced</grouping>
    <parentgroup>basic</parentgroup>
    <order>300.0</order>
    <name>gear_effect_gain</name>
    <displayname>Gear Effect Gain</displayname>
    <prereq>basic_group</prereq>
    <MSFS>true</MSFS>
    <joystick>true</joystick>
    <value>0.5</value>
  </defaults>
  <defaults>
    <datatype>n_float</datatype>
    <grouping>Debug</grouping>
    <order>900.0</order>
    <name>debug_setting</name>
    <displayname>Debug Setting</displayname>
    <debug_only>true</debug_only>
    <MSFS>true</MSFS>
    <joystick>true</joystick>
    <value>42</value>
  </defaults>
  <defaults>
    <datatype>n_float</datatype>
    <grouping>Basic</grouping>
    <order>150.0</order>
    <name>rudder_expo</name>
    <displayname>Rudder Expo</displayname>
    <XPLANE>true</XPLANE>
    <joystick>true</joystick>
    <value>0.4</value>
  </defaults>

  <models>
    <name>type</name>
    <model>Cessna.*</model>
    <value>PropellerAircraft</value>
    <sim>MSFS</sim>
    <device>joystick</device>
  </models>
  <models>
    <name>aileron_expo</name>
    <model>Cessna.*</model>
    <value>0.8</value>
    <sim>MSFS</sim>
    <device>joystick</device>
  </models>
  <models>
    <name>type</name>
    <model>F-16.*</model>
    <value>JetAircraft</value>
    <sim>DCS</sim>
    <device>joystick</device>
  </models>
  <models>
    <name>spring_coeff</name>
    <model>F-16.*</model>
    <value>500</value>
    <sim>DCS</sim>
    <device>joystick</device>
  </models>
  <models>
    <name>spring_coeff</name>
    <model>F-16.*</model>
    <value>600</value>
    <sim>DCS</sim>
    <device>any</device>
  </models>

  <classdefaults_MSFS>
    <name>aoa_effect_enabled</name>
    <type>PropellerAircraft</type>
    <value>true</value>
    <sim>MSFS</sim>
    <device>joystick</device>
  </classdefaults_MSFS>
  <classdefaults_MSFS>
    <name>aoa_effect_enabled</name>
    <type>!PropellerAircraft</type>
    <value>false</value>
    <sim>MSFS</sim>
    <device>joystick</device>
  </classdefaults_MSFS>

  <sc_overrides>
    <name>EngRPM</name>
    <model>Cessna.*</model>
    <var>L:Aircraft.Engine.RPM</var>
    <sc_unit>RPM</sc_unit>
    <scale>1.0</scale>
  </sc_overrides>

  <validvalues_overrides>
    <name>spring_mode</name>
    <sim>MSFS</sim>
    <class>Helicopter</class>
    <validvalues>HELI_SPRING_MODE</validvalues>
    <device>joystick</device>
  </validvalues_overrides>

  <classes>
    <sim>MSFS</sim>
    <class_name>PropellerAircraft</class_name>
  </classes>
  <classes>
    <sim>MSFS</sim>
    <class_name>JetAircraft</class_name>
  </classes>
  <classes>
    <sim>DCS</sim>
    <class_name>JetAircraft</class_name>
  </classes>

  <sims><sim>MSFS</sim></sims>
  <sims><sim>DCS</sim></sims>
  <sims><sim>XPLANE</sim></sims>
</TelemFFB>
"""

MINIMAL_USERCONFIG = """\
<?xml version="1.0" encoding="UTF-8"?>
<TelemFFB>
  <simSettings>
    <name>global_gain</name>
    <value>1.5</value>
    <sim>MSFS</sim>
    <device>joystick</device>
  </simSettings>

  <classSettings>
    <name>aoa_effect_gain</name>
    <value>0.75</value>
    <type>PropellerAircraft</type>
    <sim>MSFS</sim>
    <device>joystick</device>
  </classSettings>

  <models>
    <name>aileron_expo</name>
    <model>Cessna.*</model>
    <value>0.9</value>
    <sim>MSFS</sim>
    <device>joystick</device>
  </models>

  <models>
    <name>type</name>
    <model>CustomPlane.*</model>
    <value>PropellerAircraft</value>
    <sim>MSFS</sim>
    <device>joystick</device>
    <profile>MyProfile</profile>
  </models>

  <sc_overrides>
    <name>EngRPM</name>
    <model>Cessna.*</model>
    <var>L:Custom.EngRPM</var>
    <sc_unit>RPM</sc_unit>
    <scale>2.0</scale>
  </sc_overrides>

  <profileMappings>
    <model>Cessna.*</model>
    <sim>MSFS</sim>
    <cls>PropellerAircraft</cls>
    <active_profile>Auto User</active_profile>
  </profileMappings>
</TelemFFB>
"""


def _mock_settings_mgr(active_profile="Built-In"):
    """Return a mock G.settings_mgr with an active_profile attribute."""
    mock = MagicMock()
    mock.active_profile = active_profile
    return mock


def _mock_system_settings(debug=False):
    """Return a mock G.system_settings that supports .get(key, default)."""
    mock = MagicMock()
    mock.get = lambda key, default=False: debug if key == "debug" else default
    return mock


@pytest.fixture
def xml_tmpdir(tmp_path):
    """Create temp XML files and initialise xmlutils globals."""
    defaults_file = tmp_path / "defaults.xml"
    userconfig_file = tmp_path / "userconfig_v2.xml"
    defaults_file.write_text(MINIMAL_DEFAULTS)
    userconfig_file.write_text(MINIMAL_USERCONFIG)

    # Initialise module-level state
    xmlutils.update_vars("joystick", str(userconfig_file), str(defaults_file))
    G.userconfig_path = str(userconfig_file)
    G.defaults_path = str(defaults_file)
    G.settings_mgr = _mock_settings_mgr()
    G.system_settings = _mock_system_settings()
    xmlutils.update_roots()

    yield {
        "defaults": str(defaults_file),
        "userconfig": str(userconfig_file),
        "tmp_path": tmp_path,
    }

    # Cleanup module-level globals after each test
    xmlutils.auto_user_root = None
    xmlutils.auto_user_tree = None
    xmlutils.auto_defaults_root = None
    xmlutils.device = ""
    xmlutils.userconfig_path = ""
    xmlutils.defaults_path = ""


# ─────────────────────────────────────────────────────────────
# try_parse
# ─────────────────────────────────────────────────────────────

class TestTryParse:
    def test_parses_valid_xml(self, xml_tmpdir):
        tree = xmlutils.try_parse(xml_tmpdir["defaults"])
        assert tree is not None
        assert tree.getroot().tag == "TelemFFB"

    def test_returns_none_on_bad_xml(self, tmp_path):
        bad = tmp_path / "bad.xml"
        bad.write_text("<not valid xml>")
        result = xmlutils.try_parse(str(bad), max_attempts=2, delay=0.01)
        assert result is None

    def test_retries_then_succeeds(self, tmp_path):
        """Simulate first two attempts failing, third succeeding."""
        good = tmp_path / "good.xml"
        call_count = {"n": 0}

        original_parse = ET.parse

        def fake_parse(path):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ET.ParseError("simulated lock")
            return original_parse(path)

        good.write_text("<root><ok/></root>")
        with patch("xml.etree.ElementTree.parse", side_effect=fake_parse):
            result = xmlutils.try_parse(str(good), max_attempts=3, delay=0.01)
        assert result is not None
        assert call_count["n"] == 3

    def test_all_attempts_fail_returns_none(self, tmp_path):
        bad = tmp_path / "bad.xml"
        bad.write_text("broken")
        call_count = {"n": 0}

        def always_fail(path):
            call_count["n"] += 1
            raise ET.ParseError("locked")

        with patch("xml.etree.ElementTree.parse", side_effect=always_fail):
            result = xmlutils.try_parse(str(bad), max_attempts=2, delay=0.01)
        assert result is None
        assert call_count["n"] == 2


# ─────────────────────────────────────────────────────────────
# update_vars / update_roots
# ─────────────────────────────────────────────────────────────

class TestUpdateVarsAndRoots:
    def test_update_vars_sets_globals(self, xml_tmpdir):
        xmlutils.update_vars("pedals", xml_tmpdir["userconfig"], xml_tmpdir["defaults"])
        assert xmlutils.device == "pedals"
        assert xmlutils.userconfig_path == xml_tmpdir["userconfig"]
        assert xmlutils.defaults_path == xml_tmpdir["defaults"]

    def test_update_roots_populates_trees(self, xml_tmpdir):
        assert xmlutils.auto_user_root is not None
        assert xmlutils.auto_defaults_root is not None
        assert xmlutils.auto_user_tree is not None


# ─────────────────────────────────────────────────────────────
# read_xml_file
# ─────────────────────────────────────────────────────────────

class TestReadXmlFile:
    def test_returns_settings_for_matching_sim_device(self, xml_tmpdir):
        result = xmlutils.read_xml_file("MSFS", "joystick")
        names = [d["name"] for d in result]
        assert "aileron_expo" in names
        assert "elevator_expo" in names
        assert "basic_group" in names

    def test_filters_by_device(self, xml_tmpdir):
        result = xmlutils.read_xml_file("MSFS", "pedals")
        names = [d["name"] for d in result]
        # read_xml_file only checks explicit <device>="true" tags, not <any>
        # Neither aileron_expo nor elevator_expo have <pedals>true</pedals>
        assert "aileron_expo" not in names
        assert "elevator_expo" not in names
        assert len(result) == 0

    def test_sorted_by_order(self, xml_tmpdir):
        result = xmlutils.read_xml_file("MSFS", "joystick")
        orders = [float(d["order"]) for d in result]
        assert orders == sorted(orders)

    def test_omits_debug_only_when_debug_false(self, xml_tmpdir):
        G.system_settings = _mock_system_settings(debug=False)
        result = xmlutils.read_xml_file("MSFS", "joystick")
        names = [d["name"] for d in result]
        assert "debug_setting" not in names

    def test_includes_debug_only_when_debug_true(self, xml_tmpdir):
        G.system_settings = _mock_system_settings(debug=True)
        result = xmlutils.read_xml_file("MSFS", "joystick")
        names = [d["name"] for d in result]
        assert "debug_setting" in names

    def test_returns_empty_for_unknown_sim(self, xml_tmpdir):
        result = xmlutils.read_xml_file("NONEXISTENT", "joystick")
        assert result == []

    def test_dict_has_expected_keys(self, xml_tmpdir):
        result = xmlutils.read_xml_file("MSFS", "joystick")
        entry = result[0]
        for key in ("grouping", "order", "name", "displayname", "value",
                     "unit", "datatype", "validvalues", "replaced", "prereq",
                     "info", "sliderfactor", "device_text", "indent"):
            assert key in entry, f"Missing key: {key}"

    def test_replaced_is_sim_default(self, xml_tmpdir):
        result = xmlutils.read_xml_file("MSFS", "joystick")
        for entry in result:
            assert entry["replaced"] == "Sim Default"


# ─────────────────────────────────────────────────────────────
# read_anydevice_settings
# ─────────────────────────────────────────────────────────────

class TestReadAnyDeviceSettings:
    def test_returns_names_with_any_tag(self, xml_tmpdir):
        result = xmlutils.read_anydevice_settings("MSFS")
        assert "elevator_expo" in result
        assert "basic_group" in result
        # aileron_expo does not have <any>
        assert "aileron_expo" not in result

    def test_empty_for_sim_without_any(self, xml_tmpdir):
        result = xmlutils.read_anydevice_settings("XPLANE")
        # rudder_expo exists for XPLANE but has no <any> tag
        assert "rudder_expo" not in result


# ─────────────────────────────────────────────────────────────
# read_models_data
# ─────────────────────────────────────────────────────────────

class TestReadModelsData:
    def test_matches_model_by_regex_from_defaults(self, xml_tmpdir):
        data, pattern = xmlutils.read_models_data(
            "defaults", "MSFS", "Cessna 172")
        assert pattern == "Cessna.*"
        names = [d["name"] for d in data]
        assert "aileron_expo" in names
        assert "type" in names

    def test_returns_empty_for_nonmatching_model(self, xml_tmpdir):
        data, pattern = xmlutils.read_models_data(
            "defaults", "MSFS", "Boeing 747")
        assert data == []
        assert pattern == ""

    def test_reads_user_models_with_profile(self, xml_tmpdir):
        """User model data requires matching profile in XPath."""
        # CustomPlane.* has profile=MyProfile in the fixture
        data, pattern = xmlutils.read_models_data(
            "user", "MSFS", "CustomPlane Alpha", user=True, profile="MyProfile")
        names = [d["name"] for d in data]
        assert "type" in names

    def test_filters_by_device(self, xml_tmpdir):
        data, _ = xmlutils.read_models_data(
            "defaults", "DCS", "F-16C", instance_device="pedals")
        # F-16 models only have joystick/any entries
        names = [d["name"] for d in data]
        # spring_coeff has device="any" so it should match
        assert "spring_coeff" in names

    def test_invalid_root_raises(self, xml_tmpdir):
        with pytest.raises(ValueError, match="invalid root object"):
            xmlutils.read_models_data("bogus", "MSFS", "Cessna 172")

    def test_alldevices_flag(self, xml_tmpdir):
        data, _ = xmlutils.read_models_data(
            "defaults", "DCS", "F-16C", alldevices=True)
        names = [d["name"] for d in data]
        assert "spring_coeff" in names


# ─────────────────────────────────────────────────────────────
# get_pattern_by_sim_fullname
# ─────────────────────────────────────────────────────────────

class TestGetPatternBySimFullname:
    def test_matches_defaults_pattern(self, xml_tmpdir):
        result = xmlutils.get_pattern_by_sim_fullname("MSFS", "Cessna 172 Skyhawk")
        assert result == "Cessna.*"

    def test_prefers_user_over_defaults(self, xml_tmpdir):
        result = xmlutils.get_pattern_by_sim_fullname("MSFS", "CustomPlane Alpha")
        assert result == "CustomPlane.*"

    def test_returns_none_for_unknown(self, xml_tmpdir):
        result = xmlutils.get_pattern_by_sim_fullname("MSFS", "Boeing 747")
        assert result is None

    def test_checks_sim_namespace(self, xml_tmpdir):
        result = xmlutils.get_pattern_by_sim_fullname("DCS", "F-16C Viper")
        assert result == "F-16.*"

    def test_no_cross_sim_leak(self, xml_tmpdir):
        result = xmlutils.get_pattern_by_sim_fullname("DCS", "Cessna 172")
        # Cessna is only in MSFS defaults
        assert result is None


# ─────────────────────────────────────────────────────────────
# get_class_for_sim_model
# ─────────────────────────────────────────────────────────────

class TestGetClassForSimModel:
    def test_finds_class_in_defaults(self, xml_tmpdir):
        cls = xmlutils.get_class_for_sim_model("MSFS", "Cessna.*")
        assert cls == "PropellerAircraft"

    def test_finds_class_in_user(self, xml_tmpdir):
        cls = xmlutils.get_class_for_sim_model("MSFS", "CustomPlane.*")
        assert cls == "PropellerAircraft"

    def test_returns_none_for_unknown(self, xml_tmpdir):
        cls = xmlutils.get_class_for_sim_model("MSFS", "Unknown.*")
        assert cls is None


# ─────────────────────────────────────────────────────────────
# get_active_profile_for_model
# ─────────────────────────────────────────────────────────────

class TestGetActiveProfileForModel:
    def test_profile_mappings_wins(self, xml_tmpdir):
        profile = xmlutils.get_active_profile_for_model(
            "MSFS", "PropellerAircraft", "Cessna.*")
        assert profile == "Auto User"

    def test_falls_back_to_builtin_for_defaults_entry(self, xml_tmpdir):
        profile = xmlutils.get_active_profile_for_model(
            "DCS", "JetAircraft", "F-16.*")
        assert profile == "Built-In"

    def test_returns_none_for_unknown(self, xml_tmpdir):
        profile = xmlutils.get_active_profile_for_model(
            "MSFS", "GliderAircraft", "Unknown.*")
        assert profile is None

    def test_user_type_with_profile(self, xml_tmpdir):
        profile = xmlutils.get_active_profile_for_model(
            "MSFS", "PropellerAircraft", "CustomPlane.*")
        assert profile == "MyProfile"


# ─────────────────────────────────────────────────────────────
# get_available_profiles
# ─────────────────────────────────────────────────────────────

class TestGetAvailableProfiles:
    def test_returns_builtin_for_defaults_model(self, xml_tmpdir):
        profiles = xmlutils.get_available_profiles("MSFS", "PropellerAircraft", "Cessna.*")
        assert "Built-In" in profiles

    def test_returns_user_profiles(self, xml_tmpdir):
        profiles = xmlutils.get_available_profiles("MSFS", "PropellerAircraft", "CustomPlane.*")
        assert "MyProfile" in profiles


# ─────────────────────────────────────────────────────────────
# get_classes_for_sim / get_sims
# ─────────────────────────────────────────────────────────────

class TestGetClassesAndSims:
    def test_get_classes_for_sim(self, xml_tmpdir):
        classes = xmlutils.get_classes_for_sim("MSFS")
        assert "PropellerAircraft" in classes
        assert "JetAircraft" in classes

    def test_get_classes_empty_for_unknown_sim(self, xml_tmpdir):
        classes = xmlutils.get_classes_for_sim("NONEXISTENT")
        assert classes == []

    def test_get_sims(self, xml_tmpdir):
        sims = xmlutils.get_sims()
        assert "MSFS" in sims
        assert "DCS" in sims
        assert "XPLANE" in sims


# ─────────────────────────────────────────────────────────────
# update_default_data_with_craft_result
# ─────────────────────────────────────────────────────────────

class TestUpdateDefaultDataWithCraftResult:
    def test_overwrites_value_and_marks_replaced(self, xml_tmpdir):
        base = [{"name": "foo", "value": "1", "unit": "", "replaced": "Sim Default"}]
        craft = [{"name": "foo", "value": "2", "unit": "kg"}]
        result = xmlutils.update_default_data_with_craft_result(base, craft)
        assert result[0]["value"] == "2"
        assert result[0]["unit"] == "kg"
        assert result[0]["replaced"] == "Class Default"

    def test_does_not_affect_unmatched_entries(self, xml_tmpdir):
        base = [{"name": "foo", "value": "1", "unit": "", "replaced": "Sim Default"},
                {"name": "bar", "value": "3", "unit": "", "replaced": "Sim Default"}]
        craft = [{"name": "foo", "value": "2", "unit": "kg"}]
        result = xmlutils.update_default_data_with_craft_result(base, craft)
        assert result[1]["value"] == "3"
        assert result[1]["replaced"] == "Sim Default"

    def test_returns_new_list_shallow_copy_dicts_mutated(self, xml_tmpdir):
        """The function creates a shallow copy of the list but mutates dicts in-place."""
        base = [{"name": "foo", "value": "1", "unit": "", "replaced": "Sim Default"}]
        craft = [{"name": "foo", "value": "2", "unit": "kg"}]
        result = xmlutils.update_default_data_with_craft_result(base, craft)
        # Returns a new list object
        assert result is not base
        # But dicts are shallow-copied, so they ARE mutated in-place
        assert result[0]["value"] == "2"
        assert base[0]["value"] == "2"  # same dict object, mutated


# ─────────────────────────────────────────────────────────────
# update_data_with_models
# ─────────────────────────────────────────────────────────────

class TestUpdateDataWithModels:
    def test_applies_model_override(self, xml_tmpdir):
        defaults = [{"name": "foo", "value": "1", "unit": "", "replaced": "Sim Default"}]
        models = [{"name": "foo", "value": "99", "unit": "m"}]
        result = xmlutils.update_data_with_models(defaults, models, "Model Override")
        assert result[0]["value"] == "99"
        assert result[0]["unit"] == "m"
        assert result[0]["replaced"] == "Model Override"

    def test_skips_missing_names(self, xml_tmpdir):
        defaults = [{"name": "foo", "value": "1", "unit": "", "replaced": "Sim Default"}]
        models = [{"name": "bar", "value": "99", "unit": "m"}]
        result = xmlutils.update_data_with_models(defaults, models, "Override")
        assert result[0]["value"] == "1"
        assert result[0]["replaced"] == "Sim Default"

    def test_returns_new_list_shallow_copy_dicts_mutated(self, xml_tmpdir):
        """The function creates a shallow copy of the list but mutates dicts in-place."""
        defaults = [{"name": "foo", "value": "1", "unit": "", "replaced": "Sim Default"}]
        models = [{"name": "foo", "value": "99", "unit": "m"}]
        result = xmlutils.update_data_with_models(defaults, models, "Override")
        assert result is not defaults
        # Shallow copy means dicts ARE mutated
        assert result[0]["value"] == "99"
        assert defaults[0]["value"] == "99"  # same dict object


# ─────────────────────────────────────────────────────────────
# check_prereq_value
# ─────────────────────────────────────────────────────────────

class TestCheckPrereqValue:
    def test_updates_prereq_value_from_datalist(self, xml_tmpdir):
        datalist = [{"name": "basic_group", "value": "true"}]
        prereq_list = [{"prereq": "basic_group", "value": "False", "count": 1}]
        xmlutils.check_prereq_value(prereq_list, datalist)
        assert prereq_list[0]["value"] == "true"

    def test_no_change_when_no_match(self, xml_tmpdir):
        datalist = [{"name": "other", "value": "true"}]
        prereq_list = [{"prereq": "basic_group", "value": "False", "count": 1}]
        xmlutils.check_prereq_value(prereq_list, datalist)
        assert prereq_list[0]["value"] == "False"


# ─────────────────────────────────────────────────────────────
# eliminate_no_prereq
# ─────────────────────────────────────────────────────────────

class TestEliminateNoPrereq:
    def test_keeps_items_without_prereq(self, xml_tmpdir):
        data = [{"name": "foo", "value": "1", "prereq": "", "order": "100"}]
        result = xmlutils.eliminate_no_prereq(data)
        assert len(result) == 1

    def test_keeps_item_when_parent_is_true(self, xml_tmpdir):
        data = [
            {"name": "basic_group", "value": "true", "prereq": "", "order": "50"},
            {"name": "child", "value": "1", "prereq": "basic_group", "order": "100"},
        ]
        result = xmlutils.eliminate_no_prereq(data)
        names = [d["name"] for d in result]
        assert "child" in names

    def test_removes_item_when_parent_is_false(self, xml_tmpdir):
        data = [
            {"name": "basic_group", "value": "false", "prereq": "", "order": "50"},
            {"name": "child", "value": "1", "prereq": "basic_group", "order": "100"},
        ]
        result = xmlutils.eliminate_no_prereq(data)
        names = [d["name"] for d in result]
        assert "child" not in names

    def test_dot_in_prereq_bypasses_exact_match(self, xml_tmpdir):
        """When prereq contains '.', partial name match is enough."""
        data = [
            {"name": "spring_mode", "value": "BASIC", "prereq": "", "order": "200.0"},
            {"name": "spring_coeff", "value": "100", "prereq": "spring_mode.BASIC.CNTR_FT", "order": "200.1"},
        ]
        result = xmlutils.eliminate_no_prereq(data)
        names = [d["name"] for d in result]
        assert "spring_coeff" in names


# ─────────────────────────────────────────────────────────────
# filter_rows
# ─────────────────────────────────────────────────────────────

class TestFilterRows:
    def test_recursive_prereq_chain(self, xml_tmpdir):
        """filter_rows requires parent value == 'true' (case-insensitive)."""
        data = [
            {"name": "a", "value": "true", "prereq": ""},
            {"name": "b", "value": "true", "prereq": "a"},
            {"name": "c", "value": "true", "prereq": "b"},
        ]
        result = xmlutils.filter_rows(data)
        names = [d["name"] for d in result]
        assert "c" in names

    def test_breaks_chain_when_parent_false(self, xml_tmpdir):
        data = [
            {"name": "a", "value": "false", "prereq": ""},
            {"name": "b", "value": "true", "prereq": "a"},
        ]
        result = xmlutils.filter_rows(data)
        names = [d["name"] for d in result]
        assert "b" not in names


# ─────────────────────────────────────────────────────────────
# read_default_class_data
# ─────────────────────────────────────────────────────────────

class TestReadDefaultClassData:
    def test_returns_class_settings(self, xml_tmpdir):
        data, removal = xmlutils.read_default_class_data("MSFS", "PropellerAircraft")
        assert len(data) >= 1
        names = [d["name"] for d in data]
        assert "aoa_effect_enabled" in names

    def test_returns_removal_data_for_exclusion(self, xml_tmpdir):
        data, removal = xmlutils.read_default_class_data("MSFS", "PropellerAircraft")
        # The !PropellerAircraft exclusion entry should be collected
        assert removal is not None
        assert "aoa_effect_enabled" in removal

    def test_replaced_flag_is_class_default(self, xml_tmpdir):
        data, _ = xmlutils.read_default_class_data("MSFS", "PropellerAircraft")
        for d in data:
            assert d["replaced"] == "Class Default"


# ─────────────────────────────────────────────────────────────
# read_sc_overrides / update_sc_overrides_with_user
# ─────────────────────────────────────────────────────────────

class TestScOverrides:
    def test_read_sc_overrides_merges_default_and_user(self, xml_tmpdir):
        overrides = xmlutils.read_sc_overrides("Cessna 172")
        assert len(overrides) >= 1
        eng = [o for o in overrides if o["name"] == "EngRPM"]
        assert len(eng) == 1
        # User override should win
        assert eng[0]["source"] == "user"
        assert eng[0]["var"] == "L:Custom.EngRPM"

    def test_update_sc_overrides_with_user_merges_by_name(self, xml_tmpdir):
        defaults_ovr = [{"name": "EngRPM", "var": "L:Std.RPM", "sc_unit": "RPM",
                         "scale": "1.0", "source": "default"}]
        user_ovr = [{"name": "EngRPM", "var": "L:Custom.RPM", "sc_unit": "RPM",
                      "scale": "2.0", "source": "user"}]
        result = xmlutils.update_sc_overrides_with_user(defaults_ovr, user_ovr)
        assert result[0]["var"] == "L:Custom.RPM"
        assert result[0]["source"] == "user"

    def test_update_sc_overrides_appends_new(self, xml_tmpdir):
        defaults_ovr = [{"name": "EngRPM", "var": "L:Std.RPM", "sc_unit": "RPM",
                         "scale": "1.0", "source": "default"}]
        user_ovr = [{"name": "NewVar", "var": "L:New.Var", "sc_unit": "", "scale": "",
                      "source": "user"}]
        result = xmlutils.update_sc_overrides_with_user(defaults_ovr, user_ovr)
        assert len(result) == 2
        assert result[1]["name"] == "NewVar"


# ─────────────────────────────────────────────────────────────
# read_user_sim_data / read_user_class_data
# ─────────────────────────────────────────────────────────────

class TestReadUserSimAndClassData:
    def test_read_user_sim_data(self, xml_tmpdir):
        data = xmlutils.read_user_sim_data("MSFS")
        assert len(data) >= 1
        names = [d["name"] for d in data]
        assert "global_gain" in names

    def test_read_user_class_data(self, xml_tmpdir):
        data = xmlutils.read_user_class_data("MSFS", "PropellerAircraft")
        names = [d["name"] for d in data]
        assert "aoa_effect_gain" in names

    def test_user_sim_returns_empty_for_unknown_sim(self, xml_tmpdir):
        data = xmlutils.read_user_sim_data("NONEXISTENT")
        assert data == []


# ─────────────────────────────────────────────────────────────
# read_prereqs
# ─────────────────────────────────────────────────────────────

class TestReadPrereqs:
    def test_scans_userconfig_for_prereqs(self, xml_tmpdir):
        # Our minimal userconfig has no <defaults> elements with prereq,
        # so the list should be empty.
        prereqs = xmlutils.read_prereqs()
        assert prereqs == []

    def test_counts_duplicate_prereqs(self, tmp_path, xml_tmpdir):
        # Write a userconfig with multiple defaults having same prereq
        userconfig = tmp_path / "userconfig_v2.xml"
        userconfig.write_text("""<?xml version="1.0"?>
<TelemFFB>
  <defaults>
    <name>a</name><order>1</order><datatype>f</datatype><prereq>grp</prereq>
  </defaults>
  <defaults>
    <name>b</name><order>2</order><datatype>f</datatype><prereq>grp</prereq>
  </defaults>
</TelemFFB>""")
        xmlutils.update_vars("joystick", str(userconfig), xml_tmpdir["defaults"])
        G.userconfig_path = str(userconfig)
        xmlutils.update_roots()

        prereqs = xmlutils.read_prereqs()
        grp = [p for p in prereqs if p["prereq"] == "grp"]
        assert len(grp) == 1
        assert grp[0]["count"] == 2


# ─────────────────────────────────────────────────────────────
# consolidate_sort_and_write_userconfig
# ─────────────────────────────────────────────────────────────

class TestConsolidateSortAndWriteUserconfig:
    def test_deduplicates_models(self, xml_tmpdir):
        # Add a duplicate model entry
        root = xmlutils.auto_user_root
        existing = root.find('.//models')
        dup = ET.SubElement(root, "models")
        for child in existing:
            new_child = ET.SubElement(dup, child.tag)
            new_child.text = child.text
        tree = xmlutils.auto_user_tree

        result_tree = xmlutils.consolidate_sort_and_write_userconfig(tree, ret=True)
        models = result_tree.getroot().findall("models")
        # Should have removed the exact duplicate
        unique = set(ET.tostring(m) for m in models)
        assert len(unique) == len(models)

    def test_deduplicates_profile_mappings(self, xml_tmpdir):
        root = xmlutils.auto_user_root
        existing = root.find('.//profileMappings')
        dup = ET.SubElement(root, "profileMappings")
        for child in existing:
            new_child = ET.SubElement(dup, child.tag)
            new_child.text = child.text
        tree = xmlutils.auto_user_tree

        result_tree = xmlutils.consolidate_sort_and_write_userconfig(tree, ret=True)
        mappings = result_tree.getroot().findall("profileMappings")
        assert len(mappings) == 1

    def test_sorts_models(self, xml_tmpdir):
        tree = xmlutils.auto_user_tree
        result_tree = xmlutils.consolidate_sort_and_write_userconfig(tree, ret=True)
        root = result_tree.getroot()
        models = root.findall("models")
        keys = [(m.findtext("sim", ""), m.findtext("model", ""), m.findtext("profile", ""),
                 m.findtext("device", ""), m.findtext("name", "")) for m in models]
        assert keys == sorted(keys)


# ─────────────────────────────────────────────────────────────
# really_write_userconfig_xml
# ─────────────────────────────────────────────────────────────

class TestReallyWriteUserconfigXml:
    def test_writes_file(self, xml_tmpdir):
        tree = xmlutils.auto_user_tree
        xmlutils.really_write_userconfig_xml(tree)
        content = Path(xml_tmpdir["userconfig"]).read_text()
        assert "<TelemFFB>" in content
        assert "global_gain" in content


# ─────────────────────────────────────────────────────────────
# write_models_to_xml
# ─────────────────────────────────────────────────────────────

class TestWriteModelsToXml:
    def test_creates_new_model_entry(self, xml_tmpdir):
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.5", "test_setting")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/models[sim="MSFS"][model="TestPlane.*"][name="test_setting"]')
        assert found is not None
        assert found.findtext("value") == "0.5"

    def test_updates_existing_entry(self, xml_tmpdir):
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.5", "test_setting")
        xmlutils.update_roots()
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.9", "test_setting")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/models[sim="MSFS"][model="TestPlane.*"][name="test_setting"]')
        assert found.findtext("value") == "0.9"

    def test_raises_on_empty_model(self, xml_tmpdir):
        with pytest.raises(ValueError, match="Invalid model name"):
            xmlutils.write_models_to_xml("MSFS", "", "1", "setting")

    def test_redirects_builtin_profile_to_auto_user(self, xml_tmpdir):
        # First ensure we have a type entry for this model
        xmlutils.write_models_to_xml("MSFS", "RedirectTest.*", "PropellerAircraft", "type",
                                     profile_name="Built-In")
        xmlutils.update_roots()
        # The profile should have been redirected to "Auto User"
        found = xmlutils.auto_user_root.find(
            './/models[sim="MSFS"][model="RedirectTest.*"][name="type"][profile="Auto User"]')
        assert found is not None


# ─────────────────────────────────────────────────────────────
# erase functions
# ─────────────────────────────────────────────────────────────

class TestEraseFunctions:
    def test_erase_models_from_xml_with_profile(self, xml_tmpdir):
        """Write with a profile, then erase with the same profile."""
        xmlutils.write_models_to_xml("MSFS", "DeleteMe.*", "1", "del_setting",
                                     profile_name="TestProfile")
        xmlutils.update_roots()
        # Verify it was written
        found = xmlutils.auto_user_root.find(
            './/models[sim="MSFS"][model="DeleteMe.*"][name="del_setting"][profile="TestProfile"]')
        assert found is not None
        # Now erase it
        xmlutils.erase_models_from_xml("MSFS", "DeleteMe.*", "del_setting",
                                       profile_name="TestProfile")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/models[sim="MSFS"][model="DeleteMe.*"][name="del_setting"][profile="TestProfile"]')
        assert found is None

    def test_erase_refuses_builtin_profile(self, xml_tmpdir):
        """Erasing from Built-In profile should be silently refused."""
        xmlutils.write_models_to_xml("MSFS", "Protected.*", "1", "safe_setting",
                                     profile_name="Auto User")
        xmlutils.update_roots()
        # Attempting to erase as Built-In should do nothing (line 779-782 guard)
        xmlutils.erase_models_from_xml("MSFS", "Protected.*", "safe_setting",
                                       profile_name="Built-In")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/models[sim="MSFS"][model="Protected.*"][name="safe_setting"]')
        assert found is not None  # still exists

    def test_erase_entire_model(self, xml_tmpdir):
        xmlutils.write_models_to_xml("MSFS", "EraseAll.*", "1", "s1")
        xmlutils.write_models_to_xml("MSFS", "EraseAll.*", "2", "s2")
        xmlutils.update_roots()
        xmlutils.erase_entire_model_from_xml("MSFS", "EraseAll.*")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.findall('.//models[model="EraseAll.*"]')
        assert len(found) == 0

    def test_erase_class_from_xml(self, xml_tmpdir):
        xmlutils.write_class_to_xml("MSFS", "PropellerAircraft", "99", "class_test")
        xmlutils.update_roots()
        xmlutils.erase_class_from_xml("MSFS", "PropellerAircraft", "class_test")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/classSettings[sim="MSFS"][type="PropellerAircraft"][name="class_test"]')
        assert found is None

    def test_erase_sim_from_xml(self, xml_tmpdir):
        xmlutils.write_sim_to_xml("MSFS", "42", "sim_test")
        xmlutils.update_roots()
        xmlutils.erase_sim_from_xml("MSFS", "sim_test")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/simSettings[sim="MSFS"][name="sim_test"]')
        assert found is None

    def test_erase_sc_override(self, xml_tmpdir):
        xmlutils.write_sc_override_to_xml("RemoveOvr.*", "TestVar", "TestName")
        xmlutils.update_roots()
        xmlutils.erase_sc_override_from_xml("RemoveOvr.*", "TestName")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/sc_overrides[model="RemoveOvr.*"][name="TestName"]')
        assert found is None


# ─────────────────────────────────────────────────────────────
# write_class_to_xml / write_sim_to_xml
# ─────────────────────────────────────────────────────────────

class TestWriteClassAndSim:
    def test_write_class_to_xml(self, xml_tmpdir):
        xmlutils.write_class_to_xml("MSFS", "PropellerAircraft", "0.75", "aoa_gain")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/classSettings[sim="MSFS"][type="PropellerAircraft"][name="aoa_gain"]')
        assert found is not None
        assert found.findtext("value") == "0.75"

    def test_write_sim_to_xml(self, xml_tmpdir):
        xmlutils.write_sim_to_xml("MSFS", "2.0", "global_scale")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/simSettings[sim="MSFS"][name="global_scale"]')
        assert found is not None
        assert found.findtext("value") == "2.0"


# ─────────────────────────────────────────────────────────────
# update_active_profile_entry
# ─────────────────────────────────────────────────────────────

class TestUpdateActiveProfileEntry:
    def test_creates_profile_mapping(self, xml_tmpdir):
        xmlutils.update_active_profile_entry("MSFS", "PropellerAircraft", "Cessna.*", "NewProf")
        xmlutils.update_roots()
        found = xmlutils.auto_user_root.find(
            './/profileMappings[sim="MSFS"][cls="PropellerAircraft"][model="Cessna.*"]')
        assert found is not None
        assert found.findtext("active_profile") == "NewProf"


# ─────────────────────────────────────────────────────────────
# apply_validvalue_overrides_from_root
# ─────────────────────────────────────────────────────────────

class TestApplyValidvalueOverridesFromRoot:
    def test_overrides_validvalues_for_matching_class_device(self, xml_tmpdir):
        data = [{"name": "spring_mode", "value": "BASIC", "validvalues": "DEFAULT_LIST"}]
        xmlutils.apply_validvalue_overrides_from_root(data, "MSFS", "Helicopter", "joystick")
        assert data[0]["validvalues"] == "HELI_SPRING_MODE"

    def test_no_change_when_class_differs(self, xml_tmpdir):
        data = [{"name": "spring_mode", "value": "BASIC", "validvalues": "DEFAULT_LIST"}]
        xmlutils.apply_validvalue_overrides_from_root(data, "MSFS", "JetAircraft", "joystick")
        assert data[0]["validvalues"] == "DEFAULT_LIST"


# ─────────────────────────────────────────────────────────────
# read_models
# ─────────────────────────────────────────────────────────────

class TestReadModels:
    def test_returns_model_patterns_for_sim(self, xml_tmpdir):
        models = xmlutils.read_models("MSFS")
        assert "Cessna.*" in models

    def test_returns_model_patterns_filtered_by_class(self, xml_tmpdir):
        models = xmlutils.read_models("MSFS", "PropellerAircraft")
        assert "Cessna.*" in models

    def test_returns_empty_for_unknown_sim(self, xml_tmpdir):
        models = xmlutils.read_models("NONEXISTENT")
        # Only '' placeholder
        assert models == [""]


# ─────────────────────────────────────────────────────────────
# read_user_models
# ─────────────────────────────────────────────────────────────

class TestReadUserModels:
    def test_returns_user_created_models(self, xml_tmpdir):
        result = xmlutils.read_user_models("MSFS", "PropellerAircraft", user_only=True)
        models = [r[0] for r in result]
        assert "CustomPlane.*" in models

    def test_returns_default_only(self, xml_tmpdir):
        result = xmlutils.read_user_models("MSFS", "PropellerAircraft", default_only=True)
        models = [r[0] for r in result]
        assert "Cessna.*" in models

    def test_returns_both(self, xml_tmpdir):
        result = xmlutils.read_user_models("MSFS", "PropellerAircraft", both=True)
        models = [r[0] for r in result]
        assert "Cessna.*" in models
        assert "CustomPlane.*" in models


# ─────────────────────────────────────────────────────────────
# read_single_model — integration test
# ─────────────────────────────────────────────────────────────

class TestReadSingleModel:
    def test_returns_resolved_class_and_pattern(self, xml_tmpdir):
        """Class is resolved from XML type entry, not from input_modeltype."""
        model_class, pattern, data = xmlutils.read_single_model(
            "MSFS", "Cessna 172", instance_device="joystick")

        assert model_class == "PropellerAircraft"
        assert pattern == "Cessna.*"
        assert isinstance(data, list)

    def test_model_default_overrides_sim_default(self, xml_tmpdir):
        """Model-level values override sim-level defaults."""
        _, _, data = xmlutils.read_single_model(
            "MSFS", "Cessna 172", instance_device="joystick")
        settings = {d["name"]: d for d in data}

        assert "aileron_expo" in settings
        # defaults.xml has aileron_expo=0.3, model override has 0.8
        assert settings["aileron_expo"]["value"] == "0.8"
        assert settings["aileron_expo"]["replaced"] == "Model Default"

    def test_prereq_filtering_keeps_dependent_settings(self, xml_tmpdir):
        """Settings with prereq=basic_group are kept when basic_group=true."""
        _, _, data = xmlutils.read_single_model(
            "MSFS", "Cessna 172", instance_device="joystick")
        settings = {d["name"]: d for d in data}
        assert "gear_effect_gain" in settings

    def test_returns_tuple_of_three(self, xml_tmpdir):
        result = xmlutils.read_single_model("MSFS", "Cessna 172")
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_input_modeltype_is_overwritten_by_xml_type(self, xml_tmpdir):
        """When a type entry exists in XML, it overrides input_modeltype."""
        model_class, _, _ = xmlutils.read_single_model(
            "MSFS", "Cessna 172", input_modeltype="JetAircraft")
        # XML says Cessna.* -> PropellerAircraft, so input is overwritten
        assert model_class == "PropellerAircraft"

    def test_active_profile_override(self, xml_tmpdir):
        model_class, pattern, data = xmlutils.read_single_model(
            "MSFS", "Cessna 172", active_profile="Built-In")
        assert model_class == "PropellerAircraft"

    def test_unknown_aircraft_returns_sim_defaults_only(self, xml_tmpdir):
        """Unknown aircraft still gets sim-level defaults."""
        model_class, pattern, data = xmlutils.read_single_model(
            "MSFS", "NonExistentPlane", instance_device="joystick")
        assert model_class == ""
        assert pattern == ""
        # Still returns sim defaults, no model-specific overrides
        assert len(data) > 0
        names = [d["name"] for d in data]
        assert "basic_group" in names


# ─────────────────────────────────────────────────────────────
# remove_dicts_by_names
# ─────────────────────────────────────────────────────────────

class TestRemoveDictsByNames:
    def test_removes_matching_names(self, xml_tmpdir):
        data = [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}, {"name": "c", "value": "3"}]
        result = xmlutils.remove_dicts_by_names(data, ["b"])
        names = [d["name"] for d in result]
        assert "b" not in names
        assert "a" in names
        assert "c" in names

    def test_empty_removal_list_unchanged(self, xml_tmpdir):
        data = [{"name": "a", "value": "1"}]
        result = xmlutils.remove_dicts_by_names(data, [])
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────
# Profile management: add_new_model, add_new_profile, rename_profile
# ─────────────────────────────────────────────────────────────

class TestAddNewModel:
    """add_new_model creates a type row in userconfig."""

    def test_creates_type_row_in_userconfig(self, xml_tmpdir):
        xmlutils.add_new_model("MSFS", "PropellerAircraft", "TestPlane.*", "Auto User")
        xmlutils.update_roots()
        entry = xmlutils.auto_user_root.find(
            'models[sim="MSFS"][model="TestPlane.*"][name="type"][profile="Auto User"]')
        assert entry is not None
        assert entry.findtext('value') == "PropellerAircraft"

    def test_no_duplicate_on_second_call(self, xml_tmpdir):
        xmlutils.add_new_model("MSFS", "PropellerAircraft", "TestPlane.*", "Auto User")
        xmlutils.add_new_model("MSFS", "PropellerAircraft", "TestPlane.*", "Auto User")
        xmlutils.update_roots()
        entries = xmlutils.auto_user_root.findall(
            'models[sim="MSFS"][model="TestPlane.*"][name="type"]')
        assert len(entries) == 1


class TestAddNewProfile:
    """add_new_profile creates a profile row without duplicating."""

    def test_creates_profile_row(self, xml_tmpdir):
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Race Config")
        xmlutils.update_roots()
        entry = xmlutils.auto_user_root.find(
            'models[sim="MSFS"][model="TestPlane.*"][name="profile"][profile="Race Config"]')
        assert entry is not None

    def test_skips_if_profile_already_exists(self, xml_tmpdir):
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Race Config")
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Race Config")
        xmlutils.update_roots()
        entries = xmlutils.auto_user_root.findall(
            'models[sim="MSFS"][model="TestPlane.*"][name="profile"][profile="Race Config"]')
        assert len(entries) == 1


class TestRenameProfile:
    """rename_profile updates all model rows and profileMappings."""

    def test_renames_profile_in_model_rows(self, xml_tmpdir):
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Old Name")
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.5", "some_setting",
                                     the_device="joystick", profile_name="Old Name")
        xmlutils.rename_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Old Name", "New Name")
        xmlutils.update_roots()
        old_entries = xmlutils.auto_user_root.findall(
            'models[sim="MSFS"][model="TestPlane.*"][profile="Old Name"]')
        new_entries = xmlutils.auto_user_root.findall(
            'models[sim="MSFS"][model="TestPlane.*"][profile="New Name"]')
        assert len(old_entries) == 0
        assert len(new_entries) >= 1

    def test_renames_in_profile_mappings(self, xml_tmpdir):
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Old Name")
        xmlutils.update_active_profile_entry("MSFS", "PropellerAircraft", "TestPlane.*", "Old Name")
        xmlutils.rename_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Old Name", "New Name")
        xmlutils.update_roots()
        pm = xmlutils.auto_user_root.find(
            'profileMappings[sim="MSFS"][model="TestPlane.*"]')
        assert pm is not None
        assert pm.findtext('active_profile') == "New Name"


# ─────────────────────────────────────────────────────────────
# erase_aircraft_profiles, erase_model_profile
# ─────────────────────────────────────────────────────────────

class TestEraseAircraftProfiles:
    def test_removes_all_model_rows_for_pattern(self, xml_tmpdir):
        xmlutils.add_new_model("MSFS", "PropellerAircraft", "TestPlane.*", "Auto User")
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.5", "some_setting",
                                     the_device="joystick", profile_name="Auto User")
        xmlutils.erase_aircraft_profiles("MSFS", "PropellerAircraft", "TestPlane.*")
        xmlutils.update_roots()
        remaining = xmlutils.auto_user_root.findall('models[sim="MSFS"][model="TestPlane.*"]')
        assert len(remaining) == 0


class TestEraseModelProfile:
    def test_removes_single_profile_rows(self, xml_tmpdir):
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Profile A")
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Profile B")
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.5", "some_setting",
                                     the_device="joystick", profile_name="Profile A")
        xmlutils.erase_model_profile("MSFS", "TestPlane.*", "Profile A")
        xmlutils.update_roots()
        a_entries = xmlutils.auto_user_root.findall(
            'models[sim="MSFS"][model="TestPlane.*"][profile="Profile A"]')
        b_entries = xmlutils.auto_user_root.findall(
            'models[sim="MSFS"][model="TestPlane.*"][profile="Profile B"]')
        assert len(a_entries) == 0
        assert len(b_entries) == 1


# ─────────────────────────────────────────────────────────────
# clone_profile_entry, clone_whole_model
# ─────────────────────────────────────────────────────────────

class TestCloneProfileEntry:
    def test_clones_settings_to_new_profile(self, xml_tmpdir):
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Source")
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.7", "some_setting",
                                     the_device="joystick", profile_name="Source")
        xmlutils.clone_profile_entry("MSFS", "PropellerAircraft", "TestPlane.*",
                                     "Source", "Destination")
        xmlutils.update_roots()
        dst = xmlutils.auto_user_root.find(
            'models[sim="MSFS"][model="TestPlane.*"][name="some_setting"][profile="Destination"]')
        assert dst is not None
        assert dst.findtext('value') == "0.7"

    def test_skips_profile_rows_in_source(self, xml_tmpdir):
        """Profile name='profile' rows should not be cloned as settings."""
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "Source")
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.3", "gain",
                                     the_device="joystick", profile_name="Source")
        xmlutils.clone_profile_entry("MSFS", "PropellerAircraft", "TestPlane.*",
                                     "Source", "Dest")
        xmlutils.update_roots()
        cloned_gain = xmlutils.auto_user_root.findall(
            'models[sim="MSFS"][model="TestPlane.*"][name="gain"][profile="Dest"]')
        assert len(cloned_gain) >= 1


class TestCloneWholeModel:
    def test_clones_defaults_and_user_data(self, xml_tmpdir):
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "OldProf")
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.9", "user_gain",
                                     the_device="joystick", profile_name="OldProf")
        xmlutils.clone_whole_model("MSFS", "TestPlane.*", "NewPlane.*", "OldProf", "NewProf")
        xmlutils.update_roots()
        new_entry = xmlutils.auto_user_root.find(
            'models[sim="MSFS"][model="NewPlane.*"][profile="NewProf"]')
        assert new_entry is not None


# ─────────────────────────────────────────────────────────────
# Raw XPath helpers: get_sim_defaults, get_class_defaults, etc.
# ─────────────────────────────────────────────────────────────

class TestGetSimDefaults:
    def test_returns_user_sim_settings(self, xml_tmpdir):
        xmlutils.write_sim_to_xml("MSFS", "42", "test_sim_setting", the_device="joystick")
        result = xmlutils.get_sim_defaults("MSFS", "joystick")
        assert len(result) >= 1
        tags = [e.findtext('name') for e in result]
        assert 'test_sim_setting' in tags


class TestGetClassDefaults:
    def test_returns_user_class_settings(self, xml_tmpdir):
        xmlutils.write_class_to_xml("MSFS", "PropellerAircraft", "99", "test_cls_setting",
                                    the_device="joystick")
        result = xmlutils.get_class_defaults("MSFS", "PropellerAircraft", "joystick")
        assert len(result) >= 1
        names = [e.findtext('name') for e in result]
        assert 'test_cls_setting' in names


class TestGetModelProfile:
    def test_returns_model_profile_elements(self, xml_tmpdir):
        xmlutils.add_new_profile("MSFS", "PropellerAircraft", "TestPlane.*", "MyProf")
        xmlutils.write_models_to_xml("MSFS", "TestPlane.*", "0.1", "setting_x",
                                     the_device="joystick", profile_name="MyProf")
        result = xmlutils.get_model_profile("MSFS", "TestPlane.*", "MyProf", "joystick")
        names = [e.findtext('name') for e in result]
        assert 'setting_x' in names


class TestGetScOverride:
    def test_returns_sc_overrides_for_model(self, xml_tmpdir):
        xmlutils.write_sc_override_to_xml("TestPlane.*", "L:Custom.Var", "my_override")
        result = xmlutils.get_sc_override("TestPlane.*")
        assert len(result) >= 1
        names = [e.findtext('name') for e in result]
        assert 'my_override' in names


class TestGetModelType:
    def test_returns_type_element_from_user(self, xml_tmpdir):
        xmlutils.add_new_model("MSFS", "PropellerAircraft", "TestPlane.*", "Auto User")
        result = xmlutils.get_model_type("MSFS", "TestPlane.*", "PropellerAircraft")
        assert result is not None

    def test_returns_none_for_unknown_model(self, xml_tmpdir):
        result = xmlutils.get_model_type("MSFS", "NonExistent.*", "JetAircraft")
        assert result is None


# ─────────────────────────────────────────────────────────────
# Internal helpers: get_craft_attributes, read_models_sc_overrides
# ─────────────────────────────────────────────────────────────

class TestGetCraftAttributes:
    def test_returns_first_matching_model_attrs(self, xml_tmpdir):
        xmlutils.update_roots()
        # get_craft_attributes uses @sim attribute syntax; inject matching element
        root = xmlutils.auto_defaults_root
        m = ET.SubElement(root, 'models', {'sim': 'MSFS'})
        for tag, val in [('model', 'TestPlane.*'), ('name', 'type'),
                         ('value', 'PropellerAircraft'), ('device', 'joystick')]:
            c = ET.SubElement(m, tag)
            c.text = val
        attrs = xmlutils.get_craft_attributes(root, "MSFS", "joystick")
        assert attrs is not None
        assert attrs['model'] == 'TestPlane.*'

    def test_returns_none_for_no_match(self, xml_tmpdir):
        xmlutils.update_roots()
        attrs = xmlutils.get_craft_attributes(xmlutils.auto_defaults_root, "FAKE_SIM_XYZ", "joystick")
        assert attrs is None

    def test_returns_none_for_no_match(self, xml_tmpdir):
        xmlutils.update_roots()
        attrs = xmlutils.get_craft_attributes(xmlutils.auto_defaults_root, "FAKE_SIM_XYZ", "joystick")
        assert attrs is None


class TestReadModelsScOverrides:
    def test_reads_overrides_from_root(self, xml_tmpdir):
        xmlutils.write_sc_override_to_xml("TestPlane.*", "L:Test.Var", "test_ovr")
        xmlutils.update_roots()
        overrides = xmlutils.read_models_sc_overrides(
            xmlutils.auto_user_root, "TestPlane.*", "user")
        assert len(overrides) >= 1
        assert overrides[0]['source'] == 'user'
        assert overrides[0]['name'] == 'test_ovr'

    def test_returns_empty_for_no_match(self, xml_tmpdir):
        xmlutils.update_roots()
        overrides = xmlutils.read_models_sc_overrides(
            xmlutils.auto_user_root, "NonExistent.*", "default")
        assert overrides == []


# ─────────────────────────────────────────────────────────────
# prior_value / prior_unit tracking in update_data_with_models
# ─────────────────────────────────────────────────────────────

class TestPriorValueTracking:
    """Verifies that update_data_with_models stashes overridden values."""

    def test_stashes_prior_value_and_unit_on_override(self, xml_tmpdir):
        defaults = [
            {"name": "aileron_expo", "value": "0.3", "unit": "", "replaced": "Sim Default"},
        ]
        models = [{"name": "aileron_expo", "value": "0.9", "unit": "deg"}]
        result = xmlutils.update_data_with_models(defaults, models, "Model Override")
        assert result[0]["value"] == "0.9"
        assert result[0]["unit"] == "deg"
        assert result[0]["prior_value"] == "0.3"
        assert result[0]["prior_unit"] == ""

    def test_no_prior_when_no_prior_value_exists(self, xml_tmpdir):
        """If the item has empty value/unit before override, prior is empty."""
        defaults = [
            {"name": "foo", "value": "", "unit": "", "replaced": "Sim Default"},
        ]
        models = [{"name": "foo", "value": "42", "unit": "m"}]
        result = xmlutils.update_data_with_models(defaults, models, "Override")
        assert result[0]["prior_value"] == ""
        assert result[0]["prior_unit"] == ""

    def test_no_prior_for_unmatched_items(self, xml_tmpdir):
        """Items not matched by model_data get no prior_value key."""
        defaults = [
            {"name": "bar", "value": "1", "unit": "x", "replaced": "Sim Default"},
        ]
        models = [{"name": "other", "value": "9", "unit": "y"}]
        result = xmlutils.update_data_with_models(defaults, models, "Override")
        assert "prior_value" not in result[0]
        assert result[0]["value"] == "1"

    def test_chained_overrides_update_prior_each_time(self, xml_tmpdir):
        """Each layer's prior reflects the value from the previous layer."""
        defaults = [
            {"name": "gain", "value": "1.0", "unit": "", "replaced": "Sim Default"},
        ]
        # Layer 1: class default
        result1 = xmlutils.update_data_with_models(
            defaults, [{"name": "gain", "value": "2.0", "unit": "x"}], "Class Default")
        assert result1[0]["value"] == "2.0"
        assert result1[0]["prior_value"] == "1.0"

        # Layer 2: model default overwrites
        result2 = xmlutils.update_data_with_models(
            result1, [{"name": "gain", "value": "3.0", "unit": "y"}], "Model Default")
        assert result2[0]["value"] == "3.0"
        assert result2[0]["prior_value"] == "2.0"


# ─────────────────────────────────────────────────────────────
# render_prereq / enable_prereq parsing
# ─────────────────────────────────────────────────────────────

class TestRenderEnablePrereqParsing:
    """Verifies that read_xml_file captures render_prereq and enable_prereq."""

    def test_parses_render_prereq_from_defaults(self, xml_tmpdir):
        xmlutils.auto_user_root = None
        xmlutils.auto_defaults_root = None
        xmlutils._mgr = None
        defaults_path = xml_tmpdir["defaults"]
        tree = ET.parse(defaults_path)
        root = tree.getroot()
        setting = root.find('.//defaults[name="aileron_expo"]')
        if setting is not None:
            rp = ET.SubElement(setting, 'render_prereq')
            rp.text = 'use_custom_x_axis,!telemffb_controls_axes'
        tree.write(defaults_path)
        xmlutils.update_roots()
        data = xmlutils.read_xml_file("MSFS", "")
        item = next((d for d in data if d["name"] == "aileron_expo"), None)
        assert item is not None
        assert item.get("render_prereq") == "use_custom_x_axis,!telemffb_controls_axes"

    def test_parses_enable_prereq_from_defaults(self, xml_tmpdir):
        xmlutils.auto_user_root = None
        xmlutils.auto_defaults_root = None
        xmlutils._mgr = None
        defaults_path = xml_tmpdir["defaults"]
        tree = ET.parse(defaults_path)
        root = tree.getroot()
        setting = root.find('.//defaults[name="elevator_expo"]')
        if setting is not None:
            ep = ET.SubElement(setting, 'enable_prereq')
            ep.text = 'use_trim_curve'
        tree.write(defaults_path)
        xmlutils.update_roots()
        data = xmlutils.read_xml_file("MSFS", "")
        item = next((d for d in data if d["name"] == "elevator_expo"), None)
        assert item is not None
        assert item.get("enable_prereq") == "use_trim_curve"

    def test_empty_prereqs_default_to_empty_string(self, xml_tmpdir):
        """Settings without prereq elements get empty string."""
        data = xmlutils.read_xml_file("MSFS", "")
        item = next((d for d in data if d["name"] == "gear_effect_gain"), None)
        assert item is not None
        assert item.get("render_prereq", "") == ""
        assert item.get("enable_prereq", "") == ""


# ─────────────────────────────────────────────────────────────
# Model notes functions
# ─────────────────────────────────────────────────────────────

NOTES_DEFAULTS = '''\
<?xml version="1.0" encoding="UTF-8"?>
<TelemFFB>
  <models>
    <name>type</name>
    <model>Cessna.*</model>
    <value>PropellerAircraft</value>
    <sim>MSFS</sim>
    <device>joystick</device>
    <notes>Curated note for Cessna</notes>
  </models>
  <models>
    <name>type</name>
    <model>Beechcraft.*</model>
    <value>PropellerAircraft</value>
    <sim>MSFS</sim>
    <device>joystick</device>
  </models>
</TelemFFB>
'''


@pytest.fixture
def notes_xml(tmp_path):
    """Temp XML files with model notes in defaults."""
    defaults_file = tmp_path / "defaults.xml"
    userconfig_file = tmp_path / "userconfig_v2.xml"
    defaults_file.write_text(NOTES_DEFAULTS)
    userconfig_file.write_text("<TelemFFB/>")
    xmlutils.update_vars("joystick", str(userconfig_file), str(defaults_file))
    G.userconfig_path = str(userconfig_file)
    G.defaults_path = str(defaults_file)
    G.settings_mgr = _mock_settings_mgr()
    G.system_settings = _mock_system_settings()
    xmlutils.update_roots()
    yield {
        "defaults": str(defaults_file),
        "userconfig": str(userconfig_file),
        "tmp_path": tmp_path,
    }
    xmlutils.auto_user_root = None
    xmlutils.auto_user_tree = None
    xmlutils.auto_defaults_root = None
    xmlutils.device = ""
    xmlutils.userconfig_path = ""
    xmlutils.defaults_path = ""


class TestReadDefaultModelNotes:
    def test_returns_notes_for_matching_aircraft(self, notes_xml):
        result = xmlutils.read_default_model_notes("MSFS", "Cessna 172")
        assert result == "Curated note for Cessna"

    def test_returns_empty_for_no_notes(self, notes_xml):
        result = xmlutils.read_default_model_notes("MSFS", "Beechcraft Baron")
        assert result == ""

    def test_prefer_pattern_selects_exact_match(self, notes_xml):
        result = xmlutils.read_default_model_notes(
            "MSFS", "Cessna 172", prefer_pattern="Cessna.*")
        assert result == "Curated note for Cessna"

    def test_empty_model_name_returns_empty(self, notes_xml):
        assert xmlutils.read_default_model_notes("MSFS", "") == ""


class TestReadUserDefaultModelNotes:
    def test_returns_notes_from_user_type_row(self, notes_xml):
        uc = ET.parse(notes_xml["userconfig"])
        row = ET.SubElement(uc.getroot(), "models")
        for tag, val in [("name", "type"), ("model", "CustomPlane"),
                         ("value", "PropellerAircraft"), ("sim", "MSFS"),
                         ("device", "joystick"), ("notes", "User default note")]:
            c = ET.SubElement(row, tag)
            c.text = val
        ET.indent(uc, " ")
        uc.write(notes_xml["userconfig"], "utf-8")
        xmlutils.auto_user_root = None
        xmlutils.auto_user_tree = None
        xmlutils._mgr = None
        xmlutils.update_roots()
        result = xmlutils.read_user_default_model_notes("MSFS", "CustomPlane")
        assert result == "User default note"

    def test_empty_for_missing_model(self, notes_xml):
        assert xmlutils.read_user_default_model_notes("MSFS", "NonExistent") == ""


class TestReadUserModelNotes:
    def test_returns_profile_notes(self, notes_xml):
        uc = ET.parse(notes_xml["userconfig"])
        row = ET.SubElement(uc.getroot(), "models")
        for tag, val in [("name", "profile"), ("model", "Cessna.*"),
                         ("value", ""), ("sim", "MSFS"), ("device", "joystick"),
                         ("profile", "Auto User"), ("notes", "Profile-specific note")]:
            c = ET.SubElement(row, tag)
            c.text = val
        ET.indent(uc, " ")
        uc.write(notes_xml["userconfig"], "utf-8")
        xmlutils.auto_user_root = None
        xmlutils.auto_user_tree = None
        xmlutils._mgr = None
        xmlutils.update_roots()
        result = xmlutils.read_user_model_notes("MSFS", "Cessna.*", "Auto User")
        assert result == "Profile-specific note"

    def test_empty_for_missing_profile(self, notes_xml):
        assert xmlutils.read_user_model_notes("MSFS", "Cessna.*", "MissingProfile") == ""


class TestWriteUserModelNotes:
    def test_writes_new_profile_notes(self, notes_xml):
        profile = xmlutils.write_user_model_notes(
            "MSFS", "NewPlane", "My custom note", "Auto User")
        assert profile == "Auto User"
        stored = xmlutils.read_user_model_notes("MSFS", "NewPlane", "Auto User")
        assert stored == "My custom note"

    def test_updates_existing_notes(self, notes_xml):
        xmlutils.write_user_model_notes("MSFS", "NewPlane", "First", "Auto User")
        xmlutils.write_user_model_notes("MSFS", "NewPlane", "Second", "Auto User")
        stored = xmlutils.read_user_model_notes("MSFS", "NewPlane", "Auto User")
        assert stored == "Second"

    def test_removes_notes_on_empty_string(self, notes_xml):
        xmlutils.write_user_model_notes("MSFS", "NewPlane", "Note", "Auto User")
        xmlutils.write_user_model_notes("MSFS", "NewPlane", "", "Auto User")
        stored = xmlutils.read_user_model_notes("MSFS", "NewPlane", "Auto User")
        assert stored == ""

    def test_redirects_builtin_profile_to_auto_user(self, notes_xml):
        profile = xmlutils.write_user_model_notes(
            "MSFS", "NewPlane", "Note", "Built-In")
        assert profile == "Auto User"

    def test_returns_none_on_empty_model(self, notes_xml):
        assert xmlutils.write_user_model_notes("MSFS", "", "Note", "Auto User") is None