"""One of the user's own aircraft profiles and a shipped one both match the
loaded aircraft: the one collision TelemFFB raises with the user.

The telemetry manager leaves the collision on the settings manager for the
main window's prompt; the merge moves (or copies) everything under the
user's pattern onto the built-in; the answer is remembered per pattern pair.
"""
import json
import os
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest

import telemffb.globals as G
from telemffb import match_history, xmlutils
from telemffb.ProfileOfferDialog import offers_decline

pytestmark = [pytest.mark.unit]

DEFAULTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<TelemFFB>
  <defaults>
    <datatype>n_float</datatype><grouping>Basic</grouping><order>100.0</order>
    <name>aileron_expo</name><displayname>Aileron Expo</displayname>
    <MSFS>true</MSFS><joystick>true</joystick><value>0.3</value><unit></unit>
  </defaults>
  <defaults>
    <datatype>n_float</datatype><grouping>Basic</grouping><order>110.0</order>
    <name>max_aileron_coeff</name><displayname>Max Aileron</displayname>
    <MSFS>true</MSFS><joystick>true</joystick><value>0.5</value><unit></unit>
  </defaults>
  <models><name>type</name><model>737.*</model><value>JetAircraft</value><sim>MSFS</sim><device>joystick</device></models>
  <models><name>max_aileron_coeff</name><model>737.*</model><value>0.9</value><sim>MSFS</sim><device>joystick</device></models>
  <models><name>type</name><model>737-600.*</model><value>JetAircraft</value><sim>MSFS</sim><device>joystick</device></models>
  <models><name>max_aileron_coeff</name><model>737-600.*</model><value>0.8</value><sim>MSFS</sim><device>joystick</device></models>
  <sc_overrides><name>ParkBrake</name><model>737-600.*</model><var>L:CURATED_PARK</var><sc_unit>bool</sc_unit></sc_overrides>
</TelemFFB>
"""

# The user edited the built-in 737.*: Auto-User-style rows, no type row of
# their own.  Nothing here competes with the shipped file.
USERCONFIG = """\
<?xml version="1.0" encoding="UTF-8"?>
<TelemFFB>
  <models><name>profile</name><model>737.*</model><value>JetAircraft</value><sim>MSFS</sim><device>joystick</device><profile>Tweaked</profile></models>
  <models><name>aileron_expo</name><model>737.*</model><value>0.8</value><sim>MSFS</sim><device>joystick</device><profile>Tweaked</profile></models>
  <profileMappings><sim>MSFS</sim><cls>JetAircraft</cls><model>737.*</model><active_profile>Tweaked</active_profile></profileMappings>
</TelemFFB>
"""

SIM, AC = "MSFS", "737-600 PAX TC"


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "defaults.xml"
    u = tmp_path / "userconfig_v2.xml"
    d.write_text(DEFAULTS)
    u.write_text(USERCONFIG)
    xmlutils.update_vars("joystick", str(u), str(d))
    G.userconfig_path, G.defaults_path = str(u), str(d)
    sm = MagicMock()
    sm.active_profile = "Built-In"
    sm.current_aircraft_name = ""
    sm.current_sim = SIM
    sm.profile_change = None
    G.settings_mgr = sm
    G.master_instance = True
    G.system_settings = MagicMock()
    G.system_settings.get = lambda key, default=False: default
    xmlutils.update_roots()
    yield {"user": u, "sm": sm}
    xmlutils.device = ""
    xmlutils.userconfig_path = ""
    xmlutils.defaults_path = ""


def _added(pattern, *settings, profile="User Default"):
    """A User Default aircraft profile of the user's, as the wizard writes it."""
    xmlutils.write_models_to_xml(SIM, pattern, "JetAircraft", "type", "", "joystick", "User Default")
    xmlutils.update_active_profile_entry(SIM, "JetAircraft", pattern, profile)
    for name, value in settings:
        xmlutils.write_models_to_xml(SIM, pattern, value, name, "", "joystick", profile)


def _resolved(name):
    """The real resolution, past the stub _manager puts on read_single_model."""
    _, pattern, rows = xmlutils._resolver().resolve(SIM, name, "", "joystick")
    return pattern, {r["name"]: r["value"] for r in rows}


def _rows(store, pattern):
    root = ET.parse(store["user"]).getroot()
    return {(e.findtext("name"), e.findtext("profile")): e.findtext("value")
            for e in root.findall("models") if e.findtext("model") == pattern}


# ---- the record ----------------------------------------------------------------

def test_an_unseen_aircraft_has_no_record(store):
    assert match_history.last_match(SIM, AC) is None
    assert not os.path.exists(match_history.path())


def test_a_match_is_recorded_once_per_aircraft_and_updated_in_place(store):
    match_history.record_match(SIM, AC, "737.*")
    match_history.record_match(SIM, AC, "737-600.*")
    match_history.record_match(SIM, "Cessna 172", "Cessna.*")
    match_history.record_match("DCS", "F-14B", "F-14.*")
    assert match_history.last_match(SIM, AC) == "737-600.*"
    assert match_history.last_match("DCS", AC) is None
    with open(match_history.path(), encoding="utf-8") as f:
        assert json.load(f) == {
            "matches": {"MSFS": {AC: "737-600.*", "Cessna 172": "Cessna.*"}, "DCS": {"F-14B": "F-14.*"}},
            "collisions": {}}


def test_an_answer_is_remembered_per_pattern_pair(store):
    assert match_history.resolution(SIM, "737-600 PAX.*", "737-600.*") is None
    match_history.resolve(SIM, "737-600 PAX.*", "737-600.*", match_history.DECLINED)
    match_history.resolve(SIM, "737-600 PAX.*", "737-600.*", match_history.DECLINED)
    assert match_history.resolution(SIM, "737-600 PAX.*", "737-600.*") == "declined"
    # the same user pattern against a different shipped one is a new question
    assert match_history.resolution(SIM, "737-600 PAX.*", "737-600 PAX TC.*") is None
    assert match_history.resolution("DCS", "737-600 PAX.*", "737-600.*") is None
    match_history.resolve(SIM, "737.*", "737.*", match_history.MERGED)
    assert match_history.resolution(SIM, "737.*", "737.*") == "merged"
    match_history.resolve(SIM, "x", "y", "whatever")                    # not an answer: ignored
    assert match_history.resolution(SIM, "x", "y") is None


def test_a_decline_lapses_when_the_shipped_profile_changes_but_a_merge_does_not(store):
    before = match_history.fingerprint(xmlutils.curated_rows_for_fingerprint(SIM, "737-600.*"))
    match_history.resolve(SIM, "mine.*", "737-600.*", match_history.DECLINED, before)
    assert match_history.resolution(SIM, "mine.*", "737-600.*", before) == "declined"
    assert match_history.resolution(SIM, "mine.*", "737-600.*", "something else") is None
    # a caller that does not care what the built-in holds still sees the answer
    assert match_history.resolution(SIM, "mine.*", "737-600.*") == "declined"
    match_history.resolve(SIM, "theirs.*", "737-600.*", match_history.MERGED, before)
    assert match_history.resolution(SIM, "theirs.*", "737-600.*", "something else") == "merged"


def test_the_fingerprint_follows_settings_and_overrides_but_not_notes(store):
    rows = xmlutils.curated_rows_for_fingerprint(SIM, "737-600.*")
    assert rows and match_history.fingerprint(rows) == match_history.fingerprint(list(reversed(rows)))
    assert match_history.fingerprint([]) == ""
    # a notes-only row is not part of it, a setting and an override are
    assert not any("notes" in field for row in rows for field in row)
    kinds = {row[0] for row in rows}
    assert kinds == {"setting", "override"}


def test_the_record_lives_beside_the_user_config_and_never_touches_it(store):
    before = store["user"].read_text(encoding="utf-8")
    match_history.record_match(SIM, AC, "737.*")
    assert os.path.dirname(match_history.path()) == os.path.dirname(str(store["user"]))
    assert store["user"].read_text(encoding="utf-8") == before
    mtime = os.path.getmtime(match_history.path())
    match_history.record_match(SIM, AC, "737.*")     # nothing new: not rewritten
    assert os.path.getmtime(match_history.path()) == mtime


def test_an_unreadable_or_foreign_history_starts_over(store):
    with open(match_history.path(), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert match_history.last_match(SIM, AC) is None
    with open(match_history.path(), "w", encoding="utf-8") as f:
        json.dump({"MSFS": {AC: {"model": "737.*", "dismissed": ["x"]}}}, f)   # an older layout
    assert match_history.last_match(SIM, AC) is None
    match_history.record_match(SIM, AC, "737.*")
    assert match_history.last_match(SIM, AC) == "737.*"
    match_history.reset()
    assert not os.path.exists(match_history.path())


# ---- the detection in the telemetry manager -------------------------------------------

def _manager(monkeypatch, resolved_pattern):
    from telemffb.telem.TelemManager import TelemManager
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    monkeypatch.setattr(xmlutils, 'read_single_model',
                        lambda *a, **k: ('JetAircraft', resolved_pattern, []))
    monkeypatch.setattr(xmlutils, 'get_active_profile_for_model', lambda sim, cls, model: "Built-In")
    return TelemManager.__new__(TelemManager)


def _load(store, monkeypatch, resolved_pattern=None):
    resolved_pattern = resolved_pattern or xmlutils.get_pattern_by_sim_fullname(SIM, AC) or ""
    mgr = _manager(monkeypatch, resolved_pattern)
    mgr.get_aircraft_config(AC, SIM)
    return store["sm"].profile_change


def test_editing_a_builtin_is_not_a_collision(store, monkeypatch):
    # the user's 737.* rows sit under the shipped pattern itself: nothing competes
    assert _load(store, monkeypatch) is None
    assert match_history.last_match(SIM, AC) == "737-600.*"


def test_a_more_specific_pattern_of_the_users_wins_and_is_offered_a_move(store, monkeypatch):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    change = _load(store, monkeypatch)
    assert (change["user"], change["curated"], change["winner"]) == ("737-600 PAX.*", "737-600.*", "user")
    assert change["keep"] is False and change["same_claim"] is False
    assert (change["aircraft"], change["sim"]) == (AC, SIM)


def test_a_broader_pattern_of_the_users_loses_and_is_offered_a_copy(store, monkeypatch):
    # theirs may still be the only thing naming a 737-700, so a merge leaves it standing
    _added("737-6.*", ("aileron_expo", "0.9"))
    change = _load(store, monkeypatch)
    assert (change["user"], change["curated"], change["winner"]) == ("737-6.*", "737-600.*", "curated")
    assert change["keep"] is True


def test_an_equal_claim_goes_to_the_shipped_file_and_is_offered_a_move(store, monkeypatch):
    # the bare 737-600 matches exactly what 737-600.* matches: dead weight after a merge
    _added("737-600", ("aileron_expo", "0.9"))
    assert xmlutils.get_pattern_by_sim_fullname(SIM, AC) == "737-600.*"
    change = _load(store, monkeypatch)
    assert (change["user"], change["winner"], change["same_claim"], change["keep"]) == \
        ("737-600", "curated", True, False)
    # and, having lost the tie, none of their rows reach the aircraft
    pattern, vals = _resolved(AC)
    assert (pattern, vals["aileron_expo"]) == ("737-600.*", "0.3")


def test_the_identical_string_is_raised_too(store, monkeypatch):
    # a curated profile that ships under the very string the user added:
    # both trees' rows apply, but the two compete and the user should know
    _added("737-600.*", ("aileron_expo", "0.9"))
    change = _load(store, monkeypatch)
    assert (change["user"], change["curated"], change["winner"], change["keep"]) == \
        ("737-600.*", "737-600.*", "curated", False)
    pattern, vals = _resolved(AC)
    assert (pattern, vals["aileron_expo"], vals["max_aileron_coeff"]) == ("737-600.*", "0.9", "0.8")


def test_an_answered_pair_is_not_raised_again_but_a_new_shipped_pattern_is(store, monkeypatch):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    shipped = match_history.fingerprint(xmlutils.curated_rows_for_fingerprint(SIM, "737-600.*"))
    match_history.resolve(SIM, "737-600 PAX.*", "737.*", match_history.DECLINED, shipped)
    assert _load(store, monkeypatch) is not None                          # a different pair
    match_history.resolve(SIM, "737-600 PAX.*", "737-600.*", match_history.DECLINED, shipped)
    assert _load(store, monkeypatch) is None
    assert match_history.last_match(SIM, AC) == "737-600 PAX.*"                     # still recorded


def test_the_offer_is_recomputed_on_every_resolution(store, monkeypatch):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    assert _load(store, monkeypatch)["user"] == "737-600 PAX.*"
    xmlutils.discard_user_pattern(SIM, "737-600 PAX.*")            # the collision is gone
    assert _load(store, monkeypatch) is None


def test_a_child_instance_offers_but_leaves_the_record_to_the_master(store, monkeypatch):
    match_history.record_match(SIM, AC, "737.*")
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    G.master_instance = False
    assert _load(store, monkeypatch)["user"] == "737-600 PAX.*"
    assert match_history.last_match(SIM, AC) == "737.*"


def test_an_unmatched_aircraft_neither_records_nor_offers(store, monkeypatch):
    mgr = _manager(monkeypatch, "")
    mgr.get_aircraft_config("Mystery Plane", SIM)
    assert store["sm"].profile_change is None
    assert match_history.last_match(SIM, "Mystery Plane") is None


def test_what_the_user_holds_is_listed_for_the_dialog(store):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    xmlutils.add_new_profile(SIM, "JetAircraft", "737-600 PAX.*", "Profile2")
    xmlutils.write_models_to_xml(SIM, "737-600 PAX.*", "0.7", "aileron_expo", "", "pedals", "Profile2")
    held = xmlutils.user_rows_by_profile(SIM, "737-600 PAX.*")
    assert sorted((p, n, v) for p, n, v, _ in held) == [
        ("Profile2", "aileron_expo", "0.7"), ("User Default", "aileron_expo", "0.9")]
    assert all(d for _, _, _, d in held)                    # every row says which device
    assert xmlutils.user_rows_by_profile(SIM, "nothing.*") == []


# ---- the merge --------------------------------------------------------------------------

def test_a_merge_moves_every_profile_across(store):
    # theirs wins or claims the same aircraft: every profile, the override and
    # the notes move onto the built-in, the base becoming Auto User, and the
    # pattern is gone
    sim, mine, theirs = SIM, "737-600 PAX.*", "737-600.*"
    _added(mine, ("aileron_expo", "0.9"))
    xmlutils.add_new_profile(sim, "JetAircraft", mine, "Profile2")
    xmlutils.write_models_to_xml(sim, mine, "0.7", "aileron_expo", "", "joystick", "Profile2")
    xmlutils.write_user_model_notes(sim, mine, "my second setup", "Profile2")
    xmlutils.write_sc_override_to_xml(mine, "L:MINE", "ParkBrake", "bool", "")
    xmlutils.update_active_profile_entry(sim, "JetAircraft", mine, "Profile2")

    moved = xmlutils.merge_user_pattern(sim, mine, theirs)
    assert moved["profiles"] == {"User Default": "Auto User", "Profile2": "Profile2"}
    assert (moved["active"], moved["rows"], moved["overrides"]) == ("Profile2", 2, 1)

    root = ET.parse(store["user"]).getroot()
    assert not [e for e in root.iter() if e.tag == "model" and e.text == mine]
    rows = _rows(store, theirs)
    assert rows[("aileron_expo", "Auto User")] == "0.9"
    assert rows[("aileron_expo", "Profile2")] == "0.7"
    assert not [k for k in rows if k[0] == "type"]          # a built-in has no user type row
    assert [o.findtext("var") for o in root.findall("sc_overrides") if o.findtext("model") == theirs] == ["L:MINE"]
    assert xmlutils.get_active_profile_for_model(sim, "JetAircraft", theirs) == "Profile2"
    notes = xmlutils.read_user_model_notes(sim, theirs, "Profile2")
    assert "my second setup" in notes and mine in notes
    assert mine in xmlutils.read_user_model_notes(sim, theirs, "Auto User")
    # the built-in names the aircraft now and flies with the moved profile
    assert xmlutils.get_pattern_by_sim_fullname(sim, AC) == theirs
    _, pattern, values = xmlutils.read_single_model(sim, AC, "", "joystick")
    assert pattern == theirs
    assert {r["name"]: r["value"] for r in values}["aileron_expo"] == "0.7"


def test_a_merge_from_a_broader_pattern_copies_and_leaves_it_standing(store, monkeypatch):
    # two profiles under theirs, flying with the stacked one, plus an override
    # and a note: every one of them comes across, and the source is untouched
    sim, mine, theirs = SIM, "737-6.*", "737-600.*"
    _added(mine, ("aileron_expo", "0.9"))
    xmlutils.add_new_profile(sim, "JetAircraft", mine, "Night")
    xmlutils.write_models_to_xml(sim, mine, "0.5", "aileron_expo", "", "joystick", "Night")
    xmlutils.write_user_model_notes(sim, mine, "dimmer at night", "Night")
    xmlutils.write_sc_override_to_xml(mine, "L:MINE", "ParkBrake", "bool", "")
    xmlutils.update_active_profile_entry(sim, "JetAircraft", mine, "Night")

    def source():
        root = ET.parse(store["user"]).getroot()
        return (_rows(store, mine), xmlutils.get_active_profile_for_model(sim, "JetAircraft", mine),
                [o.findtext("var") for o in root.findall("sc_overrides") if o.findtext("model") == mine],
                xmlutils.read_user_model_notes(sim, mine, "Night"))
    before = source()

    moved = xmlutils.merge_user_pattern(sim, mine, theirs, keep=True)
    assert moved["profiles"] == {"User Default": "Auto User", "Night": "Night"}
    assert (moved["active"], moved["rows"], moved["overrides"]) == ("Night", 2, 1)
    assert source() == before                # rows, mapping, override and note all as they were
    after = ET.parse(store["user"]).getroot()
    rows = _rows(store, theirs)
    assert rows[("aileron_expo", "Auto User")] == "0.9"
    assert rows[("aileron_expo", "Night")] == "0.5"
    assert [o.findtext("var") for o in after.findall("sc_overrides") if o.findtext("model") == theirs] == ["L:MINE"]
    night = xmlutils.read_user_model_notes(sim, theirs, "Night")
    assert "dimmer at night" in night and mine in night
    assert mine in xmlutils.read_user_model_notes(sim, theirs, "Auto User")
    # the aircraft flies with the copy of what it was flying with
    assert xmlutils.get_active_profile_for_model(sim, "JetAircraft", theirs) == "Night"
    pattern, vals = _resolved(AC)
    assert (pattern, vals["aileron_expo"], vals["max_aileron_coeff"]) == (theirs, "0.5", "0.8")
    # another 737-6xx still has the broad pattern to name it
    assert xmlutils.get_pattern_by_sim_fullname(sim, "737-6MAX") == mine
    # and once the pair is answered the collision is not raised again
    match_history.resolve(sim, mine, theirs, match_history.MERGED)
    assert _load(store, monkeypatch) is None


def test_a_moved_profile_never_displaces_one_already_on_the_builtin(store):
    # the built-in already carries the user's Auto User edits: those stay,
    # the incoming base takes another name, and the aircraft flies with it
    sim, mine, theirs = SIM, "737-600 PAX.*", "737-600.*"
    xmlutils.write_models_to_xml(sim, theirs, "0.2", "aileron_expo", "", "joystick", "Auto User")
    _added(mine, ("aileron_expo", "0.9"))

    moved = xmlutils.merge_user_pattern(sim, mine, theirs)
    new = moved["profiles"]["User Default"]
    assert new != "Auto User" and moved["active"] == new
    rows = _rows(store, theirs)
    assert rows[("aileron_expo", "Auto User")] == "0.2"
    assert rows[("aileron_expo", new)] == "0.9"
    assert xmlutils.merge_user_pattern(sim, mine, theirs) == {}     # nothing left to move


def test_discarding_a_user_pattern_removes_every_trace(store):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    xmlutils.write_sc_override_to_xml("737-600 PAX.*", "L:MINE", "ParkBrake", "bool", "")
    assert xmlutils.get_pattern_by_sim_fullname(SIM, AC) == "737-600 PAX.*"
    assert xmlutils.discard_user_pattern(SIM, "737-600 PAX.*") >= 4
    root = ET.parse(store["user"]).getroot()
    assert not [e for e in root.iter() if e.tag == "model" and e.text == "737-600 PAX.*"]
    assert xmlutils.get_pattern_by_sim_fullname(SIM, AC) == "737-600.*"
    assert _rows(store, "737.*")[("aileron_expo", "Tweaked")] == "0.8"    # the rest untouched
    assert xmlutils.discard_user_pattern(SIM, "737-600 PAX.*") == 0        # already gone


# ---- the sim is paused ---------------------------------------------------------------------

def test_a_config_change_applies_while_the_sim_is_paused(store, monkeypatch):
    # frames stop while the sim is paused; the wait branch of the run loop re-checks the
    # config against the last frame's aircraft, so a profile edit does not wait for a frame
    from telemffb.telem.TelemManager import TelemManager, AircraftInfo
    mgr = TelemManager.__new__(TelemManager)
    mgr.currentAircraftName = AC
    mgr.currentAircraft = object()
    mgr._last_aircraft_info = AircraftInfo(name=AC, data_source=SIM, module=None)
    seen = []
    mgr._handle_config_changes = lambda info: seen.append(info.name)
    mgr.on_timeout = lambda: None
    mgr.timed_out = False
    mgr._process_check_deadline = None
    mgr._events, mgr._data = [], None
    mgr._safe_call = lambda what, fn: fn()

    class Cond:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def wait(self, t):
            mgr._run = False              # one idle pass, then leave the loop
            return False
    mgr._cond = Cond()
    monkeypatch.setattr(G, "system_settings", {"telemTimeout": 200}, raising=False)
    mgr.run()
    assert seen == [AC]


# ---- the comparison the dialog shows ---------------------------------------------------

def _entry(preview, name, kind="setting"):
    return next(e for e in preview["entries"] if e["name"] == name and e["kind"] == kind)


def test_the_preview_shows_what_the_builtin_brings_when_theirs_wins(store):
    # theirs names the aircraft: their rows apply, the curated max_aileron_coeff
    # and ParkBrake override do not - those are what a merge brings
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    xmlutils.write_sc_override_to_xml("737-600 PAX.*", "L:MINE", "GearHandle", "bool", "")
    pv = xmlutils.merge_preview(SIM, AC, "737-600 PAX.*", "737-600.*")
    assert pv["active_profile"] == "User Default" and pv["other_profiles"] == []
    kept = _entry(pv, "aileron_expo")
    assert (kept["built_in"], kept["yours"], kept["after"], kept["changes"]) == (None, "0.9", "0.9", False)
    gained = _entry(pv, "max_aileron_coeff")
    assert (gained["built_in"], gained["yours"], gained["after"], gained["changes"]) == ("0.8", None, "0.8", True)
    # their override pattern is the more specific one, so the curated override is not in effect
    park = _entry(pv, "ParkBrake", "override")
    assert (park["built_in"], park["yours"], park["after"], park["changes"]) == \
        ("L:CURATED_PARK", None, "L:CURATED_PARK", True)
    gear = _entry(pv, "GearHandle", "override")
    assert (gear["built_in"], gear["yours"], gear["changes"]) == (None, "L:MINE", False)
    assert (pv["gains"], pv["restores"], pv["keeps"]) == (2, 0, 2)
    assert (pv["in_effect"], pv["conflicts"]) == ("yours", 0)


def test_the_preview_shows_what_a_merge_restores_when_the_builtin_wins(store):
    # the built-in names the aircraft: theirs is broader and none of it applies;
    # a merge puts their values back on top and keeps the curated rest
    _added("737-6.*", ("aileron_expo", "0.9"), ("max_aileron_coeff", "0.7"))
    xmlutils.add_new_profile(SIM, "JetAircraft", "737-6.*", "Night")
    pv = xmlutils.merge_preview(SIM, AC, "737-6.*", "737-600.*")
    assert pv["other_profiles"] == ["Night"]
    restored = _entry(pv, "aileron_expo")
    assert (restored["built_in"], restored["yours"], restored["after"], restored["changes"]) == \
        (None, "0.9", "0.9", True)
    # both set it, so the row reads as a disagreement and theirs is what stands
    overridden = _entry(pv, "max_aileron_coeff")
    assert (overridden["built_in"], overridden["yours"], overridden["after"]) == ("0.8", "0.7", "0.7")
    assert overridden["conflict"] is True
    # no override of theirs: the curated one applies now and still does after
    park = _entry(pv, "ParkBrake", "override")
    assert (park["built_in"], park["yours"], park["changes"]) == ("L:CURATED_PARK", None, False)
    assert (pv["gains"], pv["restores"], pv["keeps"]) == (0, 2, 0)
    assert (pv["in_effect"], pv["conflicts"]) == ("built-in", 1)


def test_the_preview_on_the_identical_string_shows_both_already_applying(store):
    _added("737-600.*", ("aileron_expo", "0.9"))
    pv = xmlutils.merge_preview(SIM, AC, "737-600.*", "737-600.*")
    assert not [e for e in pv["entries"] if e["changes"]]
    assert _entry(pv, "aileron_expo")["yours"] == "0.9"
    assert _entry(pv, "max_aileron_coeff")["built_in"] == "0.8"
    assert (pv["gains"], pv["restores"], pv["keeps"]) == (0, 0, 1)
    # one string, a row in each tree, the user's on top: both are in effect
    assert pv["in_effect"] == "both"


def test_a_curated_override_is_shut_out_while_theirs_names_the_aircraft(store):
    # theirs holds no overrides at all; the built-in's still do not apply,
    # because overrides follow the naming pattern like any other setting
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    assert xmlutils.read_sc_overrides(AC, sim=SIM) == []
    pv = xmlutils.merge_preview(SIM, AC, "737-600 PAX.*", "737-600.*")
    park = _entry(pv, "ParkBrake", "override")
    assert (park["built_in"], park["yours"], park["after"], park["changes"]) == \
        ("L:CURATED_PARK", None, "L:CURATED_PARK", True)
    # merging moves theirs onto the built-in, and the override applies
    xmlutils.merge_user_pattern(SIM, "737-600 PAX.*", "737-600.*")
    assert [r["var"] for r in xmlutils.read_sc_overrides(AC, sim=SIM)] == ["L:CURATED_PARK"]


# ---- overrides belong to a sim ----------------------------------------------------------

def test_an_override_stamped_for_a_sim_is_invisible_to_the_other(store):
    # 737-600.* names the aircraft in MSFS; give it an X-Plane row of its own
    xmlutils.write_sc_override_to_xml("737-600.*", "sim/cockpit/park", "ParkBrake", "int", "", sim="XPLANE")
    xmlutils.write_sc_override_to_xml("737-600.*", "L:MSFS_PARK", "ParkBrake", "bool", "", sim="MSFS")
    msfs = xmlutils.read_sc_overrides(AC, sim="MSFS")
    xplane = xmlutils.read_sc_overrides(AC, sim="XPLANE")
    assert [(r["var"], r["sim"]) for r in msfs] == [("L:MSFS_PARK", "MSFS")]
    assert [(r["var"], r["sim"]) for r in xplane] == [("sim/cockpit/park", "XPLANE")]
    # the two rows sit side by side; the shipped, unstamped one is what each replaced
    root = ET.parse(store["user"]).getroot()
    assert sorted((o.findtext("var"), o.findtext("sim")) for o in root.findall("sc_overrides")) == \
        [("L:MSFS_PARK", "MSFS"), ("sim/cockpit/park", "XPLANE")]


def test_a_row_naming_no_sim_belongs_to_any_and_is_stamped_when_edited(store):
    xmlutils.write_sc_override_to_xml("737-600.*", "L:OLD", "GearHandle", "bool", "")          # legacy row
    assert [r["sim"] for r in xmlutils.read_sc_overrides(AC, sim="MSFS") if r["name"] == "GearHandle"] == [""]
    assert [r["var"] for r in xmlutils.read_sc_overrides(AC, sim="XPLANE") if r["name"] == "GearHandle"] == ["L:OLD"]
    # the editor touches it in MSFS: same row, now stamped, and X-Plane no longer sees it
    xmlutils.write_sc_override_to_xml("737-600.*", "L:NEW", "GearHandle", "bool", "", sim="MSFS")
    root = ET.parse(store["user"]).getroot()
    rows = [(o.findtext("var"), o.findtext("sim")) for o in root.findall("sc_overrides") if o.findtext("name") == "GearHandle"]
    assert rows == [("L:NEW", "MSFS")]
    assert [r["var"] for r in xmlutils.read_sc_overrides(AC, sim="XPLANE") if r["name"] == "GearHandle"] == []


def test_erasing_for_a_sim_leaves_the_other_sims_row(store):
    xmlutils.write_sc_override_to_xml("737-600.*", "L:A", "GearHandle", "bool", "", sim="MSFS")
    xmlutils.write_sc_override_to_xml("737-600.*", "sim/b", "GearHandle", "int", "", sim="XPLANE")
    xmlutils.erase_sc_override_from_xml("737-600.*", "GearHandle", sim="MSFS")
    root = ET.parse(store["user"]).getroot()
    assert [(o.findtext("var"), o.findtext("sim")) for o in root.findall("sc_overrides") if o.findtext("name") == "GearHandle"] == \
        [("sim/b", "XPLANE")]
    xmlutils.erase_sc_override_from_xml("737-600.*", "GearHandle")                            # no sim: all of them
    assert not [o for o in ET.parse(store["user"]).getroot().findall("sc_overrides") if o.findtext("name") == "GearHandle"]


def test_a_clone_and_a_merge_carry_the_sim_along(store):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    xmlutils.write_sc_override_to_xml("737-600 PAX.*", "L:MINE", "GearHandle", "bool", "", sim="MSFS")
    xmlutils.clone_whole_model(SIM, "737-600 PAX.*", "737-600 PAX TC.*", "User Default", "User Default")
    root = ET.parse(store["user"]).getroot()
    assert [(o.findtext("var"), o.findtext("sim")) for o in root.findall("sc_overrides") if o.findtext("model") == "737-600 PAX TC.*"] == \
        [("L:MINE", "MSFS")]
    xmlutils.merge_user_pattern(SIM, "737-600 PAX.*", "737-600.*")
    root = ET.parse(store["user"]).getroot()
    assert [(o.findtext("var"), o.findtext("sim")) for o in root.findall("sc_overrides") if o.findtext("model") == "737-600.*"] == \
        [("L:MINE", "MSFS")]


# ---- which answers each shape offers -------------------------------------------

def test_a_permanent_no_is_offered_only_where_their_rows_still_reach_the_aircraft():
    theirs_wins = {"user": "737-600 PAX.*", "curated": "737-600.*", "winner": "user", "same_claim": False}
    identical = {"user": "737-600.*", "curated": "737-600.*", "winner": "curated", "same_claim": True}
    equal_claim = {"user": "737-600", "curated": "737-600.*", "winner": "curated", "same_claim": True}
    builtin_wins = {"user": "737.*", "curated": "737-600.*", "winner": "curated", "same_claim": False}
    assert offers_decline(theirs_wins) is True
    assert offers_decline(identical) is True
    assert offers_decline(equal_claim) is False
    assert offers_decline(builtin_wins) is False


def test_a_shipped_change_reopens_a_declined_pair(store, monkeypatch):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    change = _load(store, monkeypatch)
    assert change["winner"] == "user"
    match_history.resolve(SIM, change["user"], change["curated"], match_history.DECLINED,
                          change["shipped"])
    assert _load(store, monkeypatch) is None
    # the shipped profile gains an override the user would never have seen
    root = ET.parse(G.defaults_path).getroot()
    ov = ET.SubElement(root, "sc_overrides")
    for tag, text in (("name", "APMaster"), ("model", "737-600.*"), ("sim", SIM),
                      ("var", "L:NEW_AP"), ("sc_unit", "bool")):
        ET.SubElement(ov, tag).text = text
    ET.ElementTree(root).write(G.defaults_path, encoding="UTF-8", xml_declaration=True)
    xmlutils.update_roots()
    assert _load(store, monkeypatch)["user"] == "737-600 PAX.*"


def test_a_merged_pair_stays_settled_when_the_shipped_profile_changes(store, monkeypatch):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    change = _load(store, monkeypatch)
    match_history.resolve(SIM, change["user"], change["curated"], match_history.MERGED, "stale")
    assert _load(store, monkeypatch) is None


def test_a_decline_can_be_given_back_but_a_merge_and_the_matches_stay(store):
    assert match_history.has_declines() is False
    match_history.record_match(SIM, AC, "737-600 PAX.*")
    match_history.resolve(SIM, "737-6.*", "737-600.*", match_history.MERGED, "abc")
    assert match_history.has_declines() is False             # neither is a decline
    match_history.resolve(SIM, "737-600 PAX.*", "737-600.*", match_history.DECLINED, "abc")
    assert match_history.has_declines() is True
    match_history.forget_declines()
    assert match_history.has_declines() is False
    assert match_history.resolution(SIM, "737-600 PAX.*", "737-600.*") is None
    assert match_history.resolution(SIM, "737-6.*", "737-600.*") == "merged"
    assert match_history.last_match(SIM, AC) == "737-600 PAX.*"


def test_forgetting_declines_raises_the_offer_again(store, monkeypatch):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    change = _load(store, monkeypatch)
    match_history.resolve(SIM, change["user"], change["curated"], match_history.DECLINED,
                          change["shipped"])
    assert _load(store, monkeypatch) is None
    match_history.forget_declines()
    assert _load(store, monkeypatch)["user"] == "737-600 PAX.*"


def test_a_copy_merge_stays_settled_after_declines_are_forgotten(store, monkeypatch):
    # theirs is broader, so the merge copies and leaves it standing; only the
    # merge record keeps it from being offered a second copy
    _added("737-6.*", ("aileron_expo", "0.9"))
    change = _load(store, monkeypatch)
    assert change["keep"] is True
    xmlutils.merge_user_pattern(SIM, change["user"], change["curated"], keep=True)
    match_history.resolve(SIM, change["user"], change["curated"], match_history.MERGED, change["shipped"])
    assert _load(store, monkeypatch) is None
    match_history.forget_declines()
    assert _load(store, monkeypatch) is None
    assert xmlutils.is_user_pattern(SIM, "737-6.*")                  # still standing, still quiet


def test_the_offer_can_be_rechecked_from_the_settings_manager_alone(store, monkeypatch):
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    mgr = _manager(monkeypatch, "737-600 PAX.*")
    mgr.get_aircraft_config(AC, SIM)
    change = store["sm"].profile_change
    match_history.resolve(SIM, change["user"], change["curated"], match_history.DECLINED, change["shipped"])
    # the settings manager keeps the loaded aircraft even while the loop's own
    # name is cleared for a full re-resolve, which is when refresh cannot help
    sm = store["sm"]
    sm.current_aircraft_name, sm.current_pattern = AC, "737-600 PAX.*"
    mgr.currentAircraftName = None
    assert mgr.refresh_aircraft_profile() is None
    mgr.recheck_profile_offer()
    assert sm.profile_change is None
    match_history.forget_declines()
    mgr.recheck_profile_offer()
    assert sm.profile_change["user"] == "737-600 PAX.*"


def test_a_row_where_the_two_sides_disagree_is_a_conflict(store):
    # the same value on both sides is no disagreement, a different one is
    _added("737-600 PAX.*", ("aileron_expo", "0.9"), ("max_aileron_coeff", "0.8"))
    xmlutils.write_sc_override_to_xml("737-600 PAX.*", "L:MINE_PARK", "ParkBrake", "bool", "")
    pv = xmlutils.merge_preview(SIM, AC, "737-600 PAX.*", "737-600.*")
    assert _entry(pv, "max_aileron_coeff")["conflict"] is False      # theirs 0.8, the built-in's 0.8
    xmlutils.write_models_to_xml(SIM, "737-600 PAX.*", "0.4", "max_aileron_coeff", "",
                                 "joystick", "User Default")
    pv = xmlutils.merge_preview(SIM, AC, "737-600 PAX.*", "737-600.*")
    clash = _entry(pv, "max_aileron_coeff")
    assert (clash["built_in"], clash["yours"], clash["after"], clash["changes"]) == \
        ("0.8", "0.4", "0.4", False)
    assert clash["conflict"] is True
    ov = _entry(pv, "ParkBrake", "override")             # the case that can break a feature
    assert (ov["built_in"], ov["yours"], ov["conflict"]) == ("L:CURATED_PARK", "L:MINE_PARK", True)
    assert _entry(pv, "aileron_expo")["conflict"] is False    # the built-in does not set it
    assert pv["conflicts"] == 2


def test_the_same_value_written_differently_is_not_a_disagreement(store):
    same = xmlutils._resolver()._same_value
    assert same("0.5", "0.50") and same("10kt", "5.1444m/s") and same("true", "True")
    assert same("Follows Trim", "Follows Trim") and same("", "")
    # a value rounded by hand is a real difference, small as it is
    assert not same("5m/s", "10kt")
    assert not same("0.268", "0.5") and not same("Follows Trim", "Center")


def test_the_identical_string_merges_in_place(store):
    # their own entry and a shipped one carry the same string: nothing moves,
    # but the type row goes and the base becomes an ordinary user profile
    mine = theirs = "737-600.*"
    _added(mine, ("aileron_expo", "0.9"))
    xmlutils.add_new_profile(SIM, "JetAircraft", mine, "Night")
    xmlutils.write_models_to_xml(SIM, mine, "0.5", "aileron_expo", "", "joystick", "Night")
    xmlutils.write_user_model_notes(SIM, mine, "an aircraft I added myself", "User Default")
    before = _resolved(AC)

    moved = xmlutils.merge_user_pattern(SIM, mine, theirs)
    assert moved["profiles"] == {"User Default": "Auto User"}
    assert (moved["active"], moved["overrides"]) == ("Auto User", 0)

    root = ET.parse(store["user"]).getroot()
    rows = {(e.findtext("name"), e.findtext("profile")) for e in root.findall("models")
            if e.findtext("model") == theirs}
    assert ("type", "User Default") not in rows            # no longer an aircraft of their own
    assert ("aileron_expo", "Auto User") in rows           # the base was renamed
    assert ("aileron_expo", "Night") in rows               # the rest kept their names
    assert ("profile", "Auto User") in rows                # and the base is registered
    assert xmlutils.get_active_profile_for_model(SIM, "JetAircraft", theirs) == "Auto User"
    assert "an aircraft I added myself" in xmlutils.read_user_model_notes(SIM, theirs, "Auto User")
    assert xmlutils.collision(SIM, AC) is None             # the two stop competing
    assert _resolved(AC) == before                         # and nothing the aircraft flies with moves


def test_an_identical_string_merge_does_not_collide_with_an_existing_auto_user(store):
    mine = theirs = "737-600.*"
    _added(mine, ("aileron_expo", "0.9"))
    xmlutils.add_new_profile(SIM, "JetAircraft", mine, "Auto User")   # already taken
    xmlutils.write_models_to_xml(SIM, mine, "0.2", "aileron_expo", "", "joystick", "Auto User")
    moved = xmlutils.merge_user_pattern(SIM, mine, theirs)
    assert moved["profiles"] == {"User Default": "Auto User (737-600)"}
    rows = {(e.findtext("name"), e.findtext("profile")) for e in ET.parse(store["user"]).getroot()
            .findall("models") if e.findtext("model") == theirs}
    assert ("aileron_expo", "Auto User") in rows and ("aileron_expo", "Auto User (737-600)") in rows


def test_the_columns_name_the_match_and_profile_they_stand_for(store):
    # one column per side, then the result; theirs is the one in effect here
    _added("737-600 PAX.*", ("aileron_expo", "0.9"))
    pv = xmlutils.merge_preview(SIM, AC, "737-600 PAX.*", "737-600.*")
    assert (pv["built_in_pattern"], pv["your_pattern"]) == ("737-600.*", "737-600 PAX.*")
    assert (pv["your_profile"], pv["in_effect"]) == ("User Default", "yours")
    assert (pv["after_pattern"], pv["after_profile"]) == ("737-600.*", "Auto User")
    # a profile of their own keeps its name across the merge
    xmlutils.add_new_profile(SIM, "JetAircraft", "737-600 PAX.*", "Night")
    xmlutils.update_active_profile_entry(SIM, "JetAircraft", "737-600 PAX.*", "Night")
    pv = xmlutils.merge_preview(SIM, AC, "737-600 PAX.*", "737-600.*")
    assert (pv["your_profile"], pv["after_profile"]) == ("Night", "Night")


def test_which_side_is_in_effect_is_reported_for_each_shape(store):
    _added("737.*", ("aileron_expo", "0.9"))       # broader than the shipped 737-600.*
    pv = xmlutils.merge_preview(SIM, AC, "737.*", "737-600.*")
    assert pv["in_effect"] == "built-in"           # theirs reaches nothing today
    assert (pv["after_pattern"], pv["after_profile"]) == ("737-600.*", "Auto User")
    xmlutils.discard_user_pattern(SIM, "737.*")
    _added("737-600.*", ("aileron_expo", "0.9"))   # the identical string
    pv = xmlutils.merge_preview(SIM, AC, "737-600.*", "737-600.*")
    assert pv["in_effect"] == "both"
