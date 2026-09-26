"""IL-2 Korea became its own sim (IL2K); user rows are keyed by sim, so the
rows a user made for the Korea aircraft while they were IL2 are copied to
it once - copied, so a release build that knows only IL2 keeps its rows."""
import os
import xml.etree.ElementTree as ET

import pytest

import telemffb.globals as G
from telemffb.utils.settings import SystemSettings, migrate_il2_korea_userconfig

pytestmark = [pytest.mark.unit]

DEFAULTS = """<?xml version="1.0"?>
<TelemFFB>
 <models><name>type</name><model>F-86A-5.*</model><value>JetAircraft</value><sim>IL2K</sim><device>any</device></models>
 <models><name>type</name><model>MiG-15bis.*</model><value>JetAircraft</value><sim>IL2K</sim><device>any</device></models>
 <models><name>type</name><model>P-51D-15</model><value>PropellerAircraft</value><sim>IL2</sim><device>any</device></models>
</TelemFFB>
"""


def row(tag, **fields):
    return "<%s>%s</%s>" % (tag, "".join("<%s>%s</%s>" % (k, v, k) for k, v in fields.items()), tag)


def write_config(path, rows):
    path.write_text('<?xml version="1.0"?>\n<TelemFFB_v2>\n' + "\n".join(rows) + "\n</TelemFFB_v2>\n",
                    encoding="utf-8")


def add_rows(path, rows):
    root = ET.parse(path).getroot()
    for r in rows:
        root.append(ET.fromstring(r))
    ET.ElementTree(root).write(path, "utf-8")


def rows_of(path, tag, sim):
    root = ET.parse(path).getroot()
    return [{c.tag: c.text for c in e} for e in root.findall(tag) if e.findtext("sim") == sim]


def backup_of(user):
    return str(user).replace("userconfig_v2.xml", "userconfig_v2_pre-il2k_backup.xml")


@pytest.fixture
def paths(tmp_path):
    defaults = tmp_path / "defaults.xml"
    defaults.write_text(DEFAULTS, encoding="utf-8")
    return tmp_path / "userconfig_v2.xml", str(defaults)


def test_korea_rows_are_copied_and_the_il2_rows_stay(paths):
    user, defaults = paths
    write_config(user, [
        row("models", name="gforce_effect_mode", model="F-86A-5.*", value="LEGACY", sim="IL2", device="joystick", profile="Auto User"),
        row("models", name="profile", model="MiG-15bis.*", value="JetAircraft", sim="IL2", device="any", profile="Auto User"),
        row("profileMappings", sim="IL2", cls="JetAircraft", model="F-86A-5.*", active_profile="Auto User"),
        row("models", name="gforce_effect_mode", model="P-51D-15", value="LEGACY", sim="IL2", device="joystick", profile="Auto User"),
        row("simSettings", name="gear_motion_intensity", value="0.15", sim="IL2", device="joystick"),
        row("classSettings", name="deceleration_effect_enable", type="JetAircraft", value="false", sim="IL2", device="joystick"),
    ])
    assert migrate_il2_korea_userconfig(str(user), defaults)

    assert {r["model"] for r in rows_of(user, "models", "IL2K")} == {"F-86A-5.*", "MiG-15bis.*"}
    assert [r["model"] for r in rows_of(user, "profileMappings", "IL2K")] == ["F-86A-5.*"]
    assert [r["value"] for r in rows_of(user, "simSettings", "IL2K")] == ["0.15"]
    assert [r["type"] for r in rows_of(user, "classSettings", "IL2K")] == ["JetAircraft"]
    # the release build's rows are all still there
    assert {r["model"] for r in rows_of(user, "models", "IL2")} == {"F-86A-5.*", "MiG-15bis.*", "P-51D-15"}
    assert len(rows_of(user, "classSettings", "IL2")) == 1
    assert os.path.isfile(backup_of(user))


def test_a_file_without_korea_rows_is_left_alone(paths):
    """Sim-level IL2 rows alone say nothing about Korea; the file is not
    even rewritten."""
    user, defaults = paths
    write_config(user, [row("simSettings", name="a", value="1", sim="IL2", device="joystick")])
    before = user.read_bytes()
    assert not migrate_il2_korea_userconfig(str(user), defaults)
    assert user.read_bytes() == before
    assert not os.path.isfile(backup_of(user))


def test_later_il2_edits_do_not_reach_korea(paths):
    """After the copy each sim's rows are its own: a Korea edit made on the
    release build and a Great Battles edit made anywhere stay under IL2."""
    user, defaults = paths
    write_config(user, [
        row("models", name="gforce_effect_mode", model="F-86A-5.*", value="LEGACY", sim="IL2", device="joystick", profile="Auto User"),
    ])
    migrate_il2_korea_userconfig(str(user), defaults)
    korea_before = rows_of(user, "models", "IL2K")

    add_rows(user, [
        row("models", name="gforce_effect_enabled", model="F-86A-5.*", value="true", sim="IL2", device="joystick", profile="Auto User"),
        row("simSettings", name="gear_motion_intensity", value="0.5", sim="IL2", device="joystick"),
    ])
    assert not migrate_il2_korea_userconfig(str(user), defaults)
    assert rows_of(user, "models", "IL2K") == korea_before
    assert rows_of(user, "simSettings", "IL2K") == []


@pytest.mark.parametrize("il2_on, korea_path, expected", [
    (True, "C:/Korea", True),      # Korea was running under the IL2 switch
    (True, "", False),             # IL2 on, but Korea never set up
    (False, "C:/Korea", False),    # IL2 off: Korea was off too
])
def test_korea_switch_starts_from_the_old_rule(tmp_path, monkeypatch, il2_on, korea_path, expected):
    monkeypatch.setattr(G, "device_type", "joystick", raising=False)
    store = SystemSettings(path=str(tmp_path / "settings.ini"))
    store.setValue("enableIL2", il2_on)
    store.setValue("pathIL2_K", korea_path)
    assert store.migrate_il2_korea_enable()
    assert bool(store.get("enableIL2K")) is expected


def test_korea_switch_is_derived_only_once(tmp_path, monkeypatch):
    """Once set, the switch is the user's: a later start never re-derives
    it, whatever the IL2 switch and the Korea path say."""
    monkeypatch.setattr(G, "device_type", "joystick", raising=False)
    store = SystemSettings(path=str(tmp_path / "settings.ini"))
    store.setValue("enableIL2", True)
    store.setValue("pathIL2_K", "C:/Korea")
    store.migrate_il2_korea_enable()
    store.setValue("enableIL2K", False)             # the user switches Korea off
    assert not store.migrate_il2_korea_enable()
    assert not store.get("enableIL2K")
