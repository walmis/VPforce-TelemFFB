"""Which profile names an aircraft, and whose settings it gets.

Patterns rank by specificity instead of document order; the winner names
the aircraft, and only the winner's settings and overrides apply.
Everything a single-match aircraft got before, it still gets.
"""
from unittest.mock import MagicMock

import pytest

import telemffb.globals as G
from telemffb import xmlutils
from telemffb.xml import match as xmatch

pytestmark = [pytest.mark.unit]

DEFAULTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<TelemFFB>
  <defaults>
    <datatype>n_float</datatype><grouping>Basic</grouping><order>100.0</order>
    <name>aileron_expo</name><displayname>Aileron Expo</displayname>
    <MSFS>true</MSFS><DCS>true</DCS><joystick>true</joystick>
    <value>0.3</value><unit></unit>
  </defaults>
  <defaults>
    <datatype>n_float</datatype><grouping>Basic</grouping><order>200.0</order>
    <name>elevator_expo</name><displayname>Elevator Expo</displayname>
    <MSFS>true</MSFS><DCS>true</DCS><joystick>true</joystick>
    <value>0.5</value><unit></unit>
  </defaults>
  <defaults>
    <datatype>n_float</datatype><grouping>Basic</grouping><order>300.0</order>
    <name>spring_coeff</name><displayname>Spring</displayname>
    <MSFS>true</MSFS><DCS>true</DCS><joystick>true</joystick>
    <value>100</value><unit></unit>
  </defaults>

  <!-- a broad family pattern listed BEFORE the specific one, as in a curated file -->
  <models><name>type</name><model>737.*</model><value>JetAircraft</value><sim>MSFS</sim><device>joystick</device></models>
  <models><name>aileron_expo</name><model>737.*</model><value>0.6</value><sim>MSFS</sim><device>joystick</device></models>
  <models><name>elevator_expo</name><model>737.*</model><value>0.7</value><sim>MSFS</sim><device>joystick</device></models>

  <models><name>type</name><model>737-600.*</model><value>JetAircraft</value><sim>MSFS</sim><device>joystick</device></models>
  <models><name>aileron_expo</name><model>737-600.*</model><value>0.9</value><sim>MSFS</sim><device>joystick</device></models>

  <models><name>type</name><model>737-600 PAX.*</model><value>PMDGAircraft</value><sim>MSFS</sim><device>joystick</device></models>
  <models><name>spring_coeff</name><model>737-600 PAX.*</model><value>250</value><sim>MSFS</sim><device>joystick</device></models>

  <models><name>type</name><model>F-14.*</model><value>JetAircraft</value><sim>DCS</sim><device>joystick</device></models>
  <!-- a settings-only pattern, more specific than any type row -->
  <models><name>spring_coeff</name><model>F-14A Tomcat.*</model><value>900</value><sim>DCS</sim><device>joystick</device></models>
  <models><name>type</name><model>F-14B</model><value>PinnedJet</value><sim>DCS</sim><device>joystick</device></models>
  <!-- a settings-only pattern for an aircraft no type row names -->
  <models><name>spring_coeff</name><model>Spitfire.*</model><value>700</value><sim>DCS</sim><device>joystick</device></models>

  <sc_overrides><name>ParkBrake</name><model>737.*</model><var>BRAKE PARKING POSITION</var><sc_unit>bool</sc_unit><scale></scale></sc_overrides>
  <sc_overrides><name>ParkBrake</name><model>737-600 PAX.*</model><var>B:LANDING_GEAR_PARKINGBRAKE</var><sc_unit>bool</sc_unit><scale>0.01</scale></sc_overrides>
</TelemFFB>
"""

USERCONFIG = """\
<?xml version="1.0" encoding="UTF-8"?>
<TelemFFB>
  <models><name>type</name><model>737.*</model><value>JetAircraft</value><sim>MSFS</sim><device>joystick</device><profile>User Default</profile></models>
  <models><name>elevator_expo</name><model>737.*</model><value>0.8</value><sim>MSFS</sim><device>joystick</device><profile>Tweaked</profile></models>
</TelemFFB>
"""


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "defaults.xml"
    u = tmp_path / "userconfig_v2.xml"
    d.write_text(DEFAULTS)
    u.write_text(USERCONFIG)
    xmlutils.update_vars("joystick", str(u), str(d))
    G.userconfig_path, G.defaults_path = str(u), str(d)
    sm = MagicMock()
    sm.active_profile = "Tweaked"
    sm.current_aircraft_name = ""
    G.settings_mgr = sm
    G.system_settings = MagicMock()
    G.system_settings.get = lambda key, default=False: default
    xmlutils.update_roots()
    yield sm
    xmlutils.device = ""
    xmlutils.userconfig_path = ""
    xmlutils.defaults_path = ""


# ---- the pure matching rules ---------------------------------------------

def test_a_pattern_matches_exactly_or_as_a_regex_from_the_start():
    assert xmatch.pattern_matches("737-600 PAX TC", "737-600 PAX TC")
    assert xmatch.pattern_matches("737.*", "737-600 PAX TC")
    assert not xmatch.pattern_matches("600.*", "737-600 PAX TC")
    assert not xmatch.pattern_matches("", "737-600 PAX TC") and not xmatch.pattern_matches("737.*", "")


def test_a_bad_regex_never_matches():
    assert not xmatch.pattern_matches("737(", "737-600")


def test_specificity_orders_exact_then_longer_literal_prefix():
    name = "F-14B"
    sp = lambda p: xmatch.specificity(p, name)  # noqa: E731
    assert sp("F-14B") > sp("F-14.*") > sp("F.*") > sp(".*")
    assert xmatch.literal_prefix_len("737-600.*") == 7 and xmatch.literal_prefix_len("plain") == 5


def test_a_pattern_is_ranked_on_the_text_it_pins_down():
    # what opens a pattern must not decide its rank: a leading wildcard or
    # optional group requires nothing on its own, so ranking stops at it and
    # every such pattern used to score zero, losing to anything at all
    n = "N C-17 X"
    assert xmatch.required_literal("C-17.*") == ("C-17", True)
    assert xmatch.required_literal(".*C-17.*") == ("C-17", False)
    assert xmatch.required_literal("(The )?Name.*") == ("Name", False)
    assert xmatch.specificity(".*C-17.*", n) > xmatch.specificity("N.*", n)


def test_pinning_the_text_down_first_beats_finding_it_anywhere():
    # the same requirement, but one pattern insists on it at the start
    n = "C-17 Globemaster"
    assert xmatch.specificity("C-17.*", n) > xmatch.specificity(".*C-17.*", n)
    # and an optional prefix matches strictly more, so it must not win
    m = "Name X"
    assert xmatch.specificity("Name.*", m) > xmatch.specificity("(The )?Name.*", m)


def test_anchors_are_not_wildcards():
    # ^Name$ is the most specific pattern there is; counting ^ as a metacharacter
    # ranked it below everything, so a profile pinned that way never won
    name = "C172SP Classic Cargo"
    assert xmatch.specificity("^" + name + "$", name) > xmatch.specificity(name, name)
    assert xmatch.specificity("^" + name + "$", name) > xmatch.specificity("C172SP Classic.*", name)
    # the trailing $ is what makes a pin exact: bare Name takes Name and
    # anything after it, exactly as Name.* does, so the two rank alike
    assert xmatch.pattern_matches(name, name + " Extra") and not xmatch.pattern_matches("^" + name + "$", name + " Extra")
    assert xmatch.specificity(name, name) == xmatch.specificity(name + ".*", name)
    assert xmatch.specificity(name + "$", name) == xmatch.specificity("^" + name + "$", name)
    assert xmatch.specificity("^C172SP.*", name) == xmatch.specificity("C172SP.*", name)
    assert xmatch.strip_anchors("^X$") == "X" and xmatch.strip_anchors("X") == "X"
    assert xmatch.strip_anchors(r"cost\$") == r"cost\$"      # an escaped $ is a real character


def test_best_pattern_takes_the_earliest_on_a_tie():
    assert xmatch.best_pattern(["F-1.*", "F-14.*", "F-1.*"], "F-14B") == "F-14.*"
    assert xmatch.best_pattern(["Cessna.*", "C.*"], "Piper") is None
    assert xmatch.best_pattern([".*", ".*"], "Anything") == ".*"


def test_rank_matches_keeps_document_order_among_equals():
    ranked = xmatch.rank_matches([("A.*", 1), ("AB.*", 2), ("A.*", 3)], "ABC")
    assert [payload for _, payload in ranked] == [1, 3, 2]


# ---- identity ---------------------------------------------------------------

def test_a_single_match_resolves_as_before(store):
    assert xmlutils.get_pattern_by_sim_fullname("DCS", "F-14A Tomcat") == "F-14.*"


def test_the_specific_pattern_wins_regardless_of_file_order(store):
    assert xmlutils.get_pattern_by_sim_fullname("MSFS", "737-600 PAX TC") == "737-600 PAX.*"
    assert xmlutils.get_pattern_by_sim_fullname("MSFS", "737-600 Cargo") == "737-600.*"


def test_an_exact_pin_beats_a_regex(store):
    assert xmlutils.get_pattern_by_sim_fullname("DCS", "F-14B") == "F-14B"


def test_the_shipped_file_wins_a_tie(store):
    # user 737.* and curated 737.* tie on the same string; either way it is 737.*
    assert xmlutils.get_pattern_by_sim_fullname("MSFS", "737-800") == "737.*"
    # a more specific curated pattern beats the broad user one
    assert xmlutils.get_pattern_by_sim_fullname("MSFS", "737-600 Cargo") == "737-600.*"
    # and a user pattern that claims exactly what a curated one claims loses the tie
    # to it, so a newly shipped profile is noticed rather than shadowed
    xmlutils.write_models_to_xml("MSFS", "737-600", "JetAircraft", "type", "", "joystick", "User Default")
    assert xmlutils.get_pattern_by_sim_fullname("MSFS", "737-600 Cargo") == "737-600.*"
    # theirs wins only by being strictly more specific
    xmlutils.write_models_to_xml("MSFS", "737-600 C.*", "JetAircraft", "type", "", "joystick", "User Default")
    assert xmlutils.get_pattern_by_sim_fullname("MSFS", "737-600 Cargo") == "737-600 C.*"


def test_two_patterns_claim_the_same_aircraft_when_only_anchors_and_a_trailing_wildcard_differ():
    assert xmatch.same_claim("AH-6J", "AH-6J.*")
    assert xmatch.same_claim("^AH-6J$", "AH-6J.*")
    assert xmatch.same_claim("737.*", "737.*")
    assert not xmatch.same_claim("737.*", "737-600.*")
    assert not xmatch.same_claim("AH-6.*", "AH-6J.*")


def test_first_match_is_kept_for_comparison(store):
    r = xmlutils._resolver()
    assert r.first_match_pattern("MSFS", "737-600 PAX TC") == "737.*"


# ---- only the winner's settings apply --------------------------------------------

def _settings(sim, name):
    cls, pattern, rows = xmlutils.read_single_model(sim, name, "", "joystick")
    return cls, pattern, {r["name"]: r["value"] for r in rows}


def test_the_winner_supplies_its_settings_and_the_broad_pattern_nothing(store):
    cls, pattern, values = _settings("MSFS", "737-600 Cargo")
    assert pattern == "737-600.*"
    assert cls == "JetAircraft"
    assert values["aileron_expo"] == "0.9"      # the winner's value
    assert values["elevator_expo"] == "0.5"     # the user's tweak under 737.* no longer reaches it
    assert values["spring_coeff"] == "100"      # untouched sim default


def test_the_most_specific_profile_stands_alone(store):
    cls, pattern, values = _settings("MSFS", "737-600 PAX TC")
    assert pattern == "737-600 PAX.*"
    assert cls == "PMDGAircraft"
    assert values["spring_coeff"] == "250"
    assert values["aileron_expo"] == "0.3"      # 737-600.* and 737.* contribute nothing
    assert values["elevator_expo"] == "0.5"


def test_a_collision_is_a_user_type_pattern_against_a_shipped_one(store):
    r = xmlutils._resolver()
    # the fixture's user 737.* is a type row: it collides with the curated 737.* on "737-800"
    assert r.collision("MSFS", "737-800") == {
        "user": "737.*", "curated": "737.*", "winner": "curated", "same_claim": True}
    # for "737-600 Cargo" the curated 737-600.* is the most specific shipped pattern
    assert r.collision("MSFS", "737-600 Cargo") == {
        "user": "737.*", "curated": "737-600.*", "winner": "curated", "same_claim": False}
    # nothing of the user's names an F-14: no collision
    assert r.collision("DCS", "F-14B") is None


def test_the_class_follows_the_winning_pattern(store):
    cls, pattern, _ = _settings("DCS", "F-14B")
    assert (cls, pattern) == ("PinnedJet", "F-14B")
    cls, pattern, _ = _settings("DCS", "F-14A")
    assert (cls, pattern) == ("JetAircraft", "F-14.*")


def test_a_settings_only_pattern_is_ignored_once_a_type_row_names_the_aircraft(store):
    cls, pattern, values = _settings("DCS", "F-14A Tomcat")
    assert (cls, pattern) == ("JetAircraft", "F-14.*")
    assert values["spring_coeff"] == "100"


def test_a_settings_only_pattern_stands_in_when_no_type_row_matches(store):
    cls, pattern, rows = xmlutils.read_single_model("DCS", "Spitfire LF Mk IX", "PropellerAircraft", "joystick")
    assert (cls, pattern) == ("PropellerAircraft", "Spitfire.*")
    assert {r["name"]: r["value"] for r in rows}["spring_coeff"] == "700"


def test_a_caller_hint_stands_only_when_nothing_matches(store):
    cls, pattern, _ = xmlutils.read_single_model("MSFS", "Unknown Plane", "PropellerAircraft", "joystick")
    assert (cls, pattern) == ("PropellerAircraft", "")


# ---- overrides rank the same way --------------------------------------------

def test_sc_overrides_come_from_the_naming_pattern_only(store):
    rows = xmlutils.read_sc_overrides("737-600 PAX TC", sim="MSFS")
    assert [r["var"] for r in rows if r["name"] == "ParkBrake"] == ["B:LANDING_GEAR_PARKINGBRAKE"]
    rows = xmlutils.read_sc_overrides("737-800", sim="MSFS")
    assert [r["var"] for r in rows if r["name"] == "ParkBrake"] == ["BRAKE PARKING POSITION"]
    # the identity pattern 737-600.* has no overrides, so - like any other setting -
    # the broad pattern's do not reach the aircraft either
    assert xmlutils.read_sc_overrides("737-600 Cargo", sim="MSFS") == []
    # a caller that knows the pattern asks for it directly
    rows = xmlutils.read_sc_overrides("737.*", identity="737.*")
    assert [r["var"] for r in rows] == ["BRAKE PARKING POSITION"]
    # the user's override under the naming pattern replaces the shipped one by name
    xmlutils.write_sc_override_to_xml("737-600 PAX.*", "L:MINE", "ParkBrake", "bool", "")
    rows = xmlutils.read_sc_overrides("737-600 PAX TC", sim="MSFS")
    assert [(r["var"], r["source"]) for r in rows if r["name"] == "ParkBrake"] == [("L:MINE", "user")]


def test_a_stacked_profile_keeps_its_own_rows_when_another_shares_the_setting(store):
    # a User Profile on top of the user's own aircraft, both setting the same
    # thing: only the active profile's value applies, whichever is written first
    xmlutils.write_models_to_xml("MSFS", "C172X.*", "PropellerAircraft", "type", "", "joystick", "User Default")
    xmlutils.write_models_to_xml("MSFS", "C172X.*", "0.5", "aileron_expo", "", "joystick", "User Default")
    xmlutils.write_models_to_xml("MSFS", "C172X.*", "0.9", "aileron_expo", "", "joystick", "Auto User")
    xmlutils.update_active_profile_entry("MSFS", "PropellerAircraft", "C172X.*", "Auto User")
    _, pattern, values = _settings("MSFS", "C172X Skyhawk")
    assert (pattern, values["aileron_expo"]) == ("C172X.*", "0.9")
    xmlutils.update_active_profile_entry("MSFS", "PropellerAircraft", "C172X.*", "User Default")
    assert _settings("MSFS", "C172X Skyhawk")[2]["aileron_expo"] == "0.5"


# ---- the shipped file keeps its own rules ----------------------------------------------

def test_every_shipped_override_belongs_to_a_type_row_in_its_sim():
    # An override applies only under the pattern that names the aircraft, in
    # the sim it is stamped for.  A shipped row keyed to a string no type row
    # carries, or to a sim its type row is not in, can never apply - which is
    # how the FlyInside B206 lost its overrides once.
    import pathlib
    import xml.etree.ElementTree as ET
    root = ET.parse(pathlib.Path(__file__).resolve().parents[1] / "defaults.xml").getroot()
    typed = {(m.findtext("sim"), m.findtext("model"))
             for m in root.findall("models") if m.findtext("name") == "type"}
    unstamped = [(o.findtext("model"), o.findtext("name"))
                 for o in root.findall("sc_overrides") if not (o.findtext("sim") or "").strip()]
    homeless = sorted({(o.findtext("sim"), o.findtext("model")) for o in root.findall("sc_overrides")
                       if (o.findtext("sim"), o.findtext("model")) not in typed})
    assert unstamped == []
    assert homeless == []
