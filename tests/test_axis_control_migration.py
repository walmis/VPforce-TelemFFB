"""Axis control is always enabled and the global toggle is gone from the settings.
A user's rows for it become 'Disable for this device' rows for every device,
once: on where the row turned axis control off, off where it turned it back
on; every row naming the old toggle is removed."""
import os
import xml.etree.ElementTree as ET

import pytest

from telemffb.utils.settings import migrate_axis_control_userconfig

pytestmark = [pytest.mark.unit]


def row(tag, **fields):
    return "<%s>%s</%s>" % (tag, "".join("<%s>%s</%s>" % (k, v, k) for k, v in fields.items()), tag)


def write_config(path, rows):
    path.write_text('<?xml version="1.0"?>\n<TelemFFB_v2>\n' + "\n".join(rows) + "\n</TelemFFB_v2>\n",
                    encoding="utf-8")


def rows_named(path, name):
    root = ET.parse(path).getroot()
    return [{c.tag: c.text for c in e} | {"_tag": e.tag} for e in root.iter() if e.findtext("name") == name]


@pytest.fixture
def user(tmp_path):
    return tmp_path / "userconfig_v3.xml"


def test_off_rows_become_device_rows_for_each_configured_device(user):
    write_config(user, [
        row("models", name="telemffb_controls_axes", model="Diamond DA40TDI.*", value="false",
            sim="MSFS", device="any", profile="Auto User"),
        row("models", name="telemffb_controls_axes", model="Cessna 172.*", value="true",
            sim="MSFS", device="any", profile="Auto User"),
        row("classSettings", name="telemffb_controls_axes", type="GliderAircraft", value="false",
            sim="XPLANE", device="any"),
        # the user already disabled this device by hand: left as it is
        row("models", name="local_disable_axis_control", model="Diamond DA40TDI.*", value="true",
            sim="MSFS", device="pedals", profile="Auto User"),
    ])
    assert migrate_axis_control_userconfig(str(user)) is True

    assert rows_named(user, "telemffb_controls_axes") == []
    da40 = sorted((r["_tag"], r["device"], r["value"]) for r in rows_named(user, "local_disable_axis_control")
                  if r.get("model") == "Diamond DA40TDI.*")
    assert da40 == [("models", "collective", "true"), ("models", "joystick", "true"),
                    ("models", "pedals", "true"), ("models", "trimwheel", "true")]
    glider = sorted((r["_tag"], r["device"], r["value"], r.get("sim")) for r in rows_named(user, "local_disable_axis_control")
                    if r.get("type") == "GliderAircraft")
    assert glider == [("classSettings", "collective", "true", "XPLANE"), ("classSettings", "joystick", "true", "XPLANE"),
                      ("classSettings", "pedals", "true", "XPLANE"), ("classSettings", "trimwheel", "true", "XPLANE")]
    # the Cessna's 'on' row re-enabled axis control below a wider-scope off row;
    # it keeps doing so per device
    cessna = sorted((r["device"], r["value"]) for r in rows_named(user, "local_disable_axis_control")
                    if r.get("model") == "Cessna 172.*")
    assert cessna == [("collective", "false"), ("joystick", "false"),
                      ("pedals", "false"), ("trimwheel", "false")]


def test_a_converted_file_is_left_alone(user):
    write_config(user, [
        row("models", name="telemffb_controls_axes", model="Diamond DA40TDI.*", value="false",
            sim="MSFS", device="any", profile="Auto User"),
    ])
    assert migrate_axis_control_userconfig(str(user)) is True
    before = user.read_text(encoding="utf-8")
    assert migrate_axis_control_userconfig(str(user)) is False
    assert user.read_text(encoding="utf-8") == before


def test_nothing_to_convert_is_not_rewritten(user):
    write_config(user, [
        row("models", name="spring_mode", model="Cessna 172.*", value="CENTER",
            sim="MSFS", device="joystick", profile="Auto User"),
    ])
    before = user.read_text(encoding="utf-8")
    assert migrate_axis_control_userconfig(str(user)) is False
    assert user.read_text(encoding="utf-8") == before

