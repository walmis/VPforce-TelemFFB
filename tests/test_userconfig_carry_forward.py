"""A first start on this generation copies the previous config to the live
name and gives the copy this generation's root tag; an existing live file
is left alone."""
import xml.etree.ElementTree as ET

import pytest

from telemffb.utils.settings import (PREVIOUS_USERCONFIG_FILENAMES, USERCONFIG_FILENAME,
                                     USERCONFIG_ROOT_TAG, copy_legacy_config_to_new)

pytestmark = [pytest.mark.unit]

V2 = ('<?xml version="1.0"?>\n<TelemFFB_v2>\n'
      '<models><name>spring_mode</name><model>Cessna 172.*</model><value>CENTER</value>'
      '<sim>MSFS</sim><device>joystick</device><profile>Auto User</profile></models>\n'
      '</TelemFFB_v2>\n')


def rows(path):
    root = ET.parse(path).getroot()
    return root.tag, [{c.tag: c.text for c in e} for e in root]


def test_the_previous_generation_is_copied_and_retagged(tmp_path):
    (tmp_path / PREVIOUS_USERCONFIG_FILENAMES[0]).write_text(V2, encoding="utf-8")
    live = tmp_path / USERCONFIG_FILENAME

    copy_legacy_config_to_new(str(live))

    tag, content = rows(live)
    assert tag == USERCONFIG_ROOT_TAG
    assert content == [{"name": "spring_mode", "model": "Cessna 172.*", "value": "CENTER",
                        "sim": "MSFS", "device": "joystick", "profile": "Auto User"}]
    assert rows(tmp_path / PREVIOUS_USERCONFIG_FILENAMES[0])[0] == "TelemFFB_v2"   # untouched


def test_an_existing_live_file_is_left_alone(tmp_path):
    (tmp_path / PREVIOUS_USERCONFIG_FILENAMES[0]).write_text(V2, encoding="utf-8")
    live = tmp_path / USERCONFIG_FILENAME
    live.write_text('<?xml version="1.0"?>\n<TelemFFB_v3>\n</TelemFFB_v3>\n', encoding="utf-8")
    before = live.read_bytes()

    copy_legacy_config_to_new(str(live))

    assert live.read_bytes() == before


def test_nothing_to_carry_forward_creates_nothing(tmp_path):
    live = tmp_path / USERCONFIG_FILENAME
    copy_legacy_config_to_new(str(live))
    assert not live.exists()
