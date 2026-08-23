"""Writing the dinput8.ini that decides what a game hands to TelemFFB.

Getting this wrong is quiet. A missing rule means the game keeps rendering
and TelemFFB appears to do nothing; a rule for the wrong device means a
stick goes dead in the cockpit. Neither reports an error, and the cause is
several menus away from the symptom - so the file's contents are worth
pinning down rather than eyeballing once.
"""
import os

import pytest

from telemffb.tap_install import (TapDevice, WRAPPER_CONFIG, configured_devices,
                                  generate_config)

pytestmark = [pytest.mark.unit]

RHINO = TapDevice("joystick", 0xFFFF, 0x2054, "VPforce Rhino")
PEDALS = TapDevice("pedals", 0x044F, 0xB679, "T-Rudder")
NAMELESS = TapDevice("collective", 0x1234, 0x5678, "")
UNREADABLE = TapDevice("trimwheel", None, None, "Some Wheel")


def rules(text):
    """The rule lines only, with comments and blanks dropped."""
    return [ln.strip() for ln in text.splitlines()
            if "=" in ln and not ln.strip().startswith(";")]


class TestWhichDevicesGetRules:
    def test_a_chosen_device_is_tapped(self):
        assert "FFFF:2054=tap" in generate_config([RHINO])[0:]

    def test_only_the_devices_passed_in(self):
        """Anything else the user owns is untouched by the wrapper."""
        text = generate_config([RHINO])
        assert "044F:B679" not in text

    def test_a_device_with_no_readable_ids_is_left_out(self):
        """A rule needs ids. Guessing one would tap somebody else's
        hardware, so the device is skipped instead."""
        text = generate_config([RHINO, UNREADABLE])
        # the rule, RequireTelemFFB, and the vJoy block every file gets
        assert len(rules(text)) == 3
        assert "Some Wheel" not in text

    def test_vjoy_is_always_blocked(self):
        """A virtual joystick a game counts as force feedback is a slot
        taken from a real one; there is no setup that wants that."""
        assert "vJoy=block" in generate_config([])

    def test_no_devices_still_writes_a_usable_file(self):
        """The wrapper is installed but inert - a real state, not an error."""
        text = generate_config([])
        assert "[FFBDevices]" in text
        assert "RequireTelemFFB=true" in text


class TestTheRuleItself:
    def test_ids_are_upper_case_and_padded(self):
        text = generate_config([TapDevice("joystick", 0xF, 0x20, "x")])
        assert "000F:0020=tap" in text

    def test_the_name_rides_along_as_a_comment(self):
        """For a human reading the file later; the wrapper never reads it."""
        line, = [ln for ln in rules(generate_config([RHINO]))
                 if ln.startswith("FFFF")]
        assert line.startswith("FFFF:2054=tap")
        assert "VPforce Rhino" in line
        assert line.index(";") < line.index("VPforce")




class TestFileShape:
    def test_telemffb_is_required(self):
        """Without this a wrapper left in a game folder would keep swallowing
        force feedback after TelemFFB is uninstalled."""
        assert "RequireTelemFFB=true" in generate_config([RHINO])

    def test_windows_line_endings(self):
        """A game config a user may open in Notepad."""
        text = generate_config([RHINO])
        assert "\r\n" in text
        assert text.count("\n") == text.count("\r\n")




class TestReadingTheConfiguredSlots:
    def test_ids_come_from_the_stored_path(self):
        settings = {"devpath_joystick":
                    r"\?\HID#VID_FFFF&PID_2054&MI_00#x",
                    "devident_joystick": "VPforce Rhino"}
        device, = configured_devices(FakeSettings(settings))
        assert (device.vid, device.pid) == (0xFFFF, 0x2054)
        assert device.ident == "VPforce Rhino"

    def test_an_unassigned_slot_is_not_a_device(self):
        assert configured_devices(FakeSettings({"devpath_joystick": ""})) == []

    def test_an_unplugged_device_is_still_configured(self):
        """The point of reading settings rather than the connected hardware:
        a rule can be written for a stick that is switched off."""
        settings = FakeSettings({"devpath_pedals":
                                 r"\?\HID#VID_FFFF&PID_2052&MI_00#x"})
        device, = configured_devices(settings)
        assert device.usable

    def test_a_slot_with_no_remembered_name_is_still_usable(self):
        settings = FakeSettings({"devpath_pedals":
                                 r"\?\HID#VID_FFFF&PID_2052&MI_00#x"})
        device, = configured_devices(settings)
        assert device.ident == "" and device.usable

    def test_slots_keep_their_roles(self):
        settings = FakeSettings({
            "devpath_joystick": r"\?\HID#VID_FFFF&PID_2054&x",
            "devpath_pedals": r"\?\HID#VID_FFFF&PID_2052&x"})
        assert [d.role for d in configured_devices(settings)] == \
            ["joystick", "pedals"]


class FakeSettings(dict):
    def get(self, name, default=None, instance=None):
        return dict.get(self, name, default)


class TestInstallWritesIt(object):
    def test_the_config_lands_beside_the_wrapper(self, tmp_path, monkeypatch):
        from telemffb import tap_install
        from telemffb.tap_install import (SIMS_BY_KEY, SimStatus, TargetStatus,
                                          WrapperState, install)

        wrapper = tmp_path / "src" / "dinput8.dll"
        wrapper.parent.mkdir()
        wrapper.write_bytes(b"FFB tap: pretend")
        monkeypatch.setattr(tap_install, 'bundled_wrapper', lambda: str(wrapper))

        target = tmp_path / "bin"
        target.mkdir()
        status = SimStatus(sim=SIMS_BY_KEY["DCS"], root=str(tmp_path),
                           provenance="test",
                           targets=[TargetStatus(str(target),
                                                 WrapperState.ABSENT)])
        install(status, generate_config([RHINO]))
        written = (target / WRAPPER_CONFIG).read_text(encoding="utf-8")
        assert "FFFF:2054=tap" in written

    def test_an_existing_config_is_never_replaced(self, tmp_path, monkeypatch):
        """It is the user's file - hand-written, or written earlier and since
        tuned. Installing the wrapper is not consent to discard it."""
        from telemffb import tap_install
        from telemffb.tap_install import (SIMS_BY_KEY, SimStatus, TargetStatus,
                                          WrapperState, install)

        wrapper = tmp_path / "src" / "dinput8.dll"
        wrapper.parent.mkdir()
        wrapper.write_bytes(b"FFB tap: pretend")
        monkeypatch.setattr(tap_install, 'bundled_wrapper', lambda: str(wrapper))

        target = tmp_path / "bin"
        target.mkdir()
        (target / WRAPPER_CONFIG).write_text("; mine\n", encoding="utf-8")
        status = SimStatus(sim=SIMS_BY_KEY["DCS"], root=str(tmp_path),
                           provenance="test",
                           targets=[TargetStatus(str(target),
                                                 WrapperState.ABSENT,
                                                 has_config=True)])
        install(status, generate_config([RHINO]))
        assert (target / WRAPPER_CONFIG).read_text(encoding="utf-8") == "; mine\n"


class TestWhereTheIdsComeFrom:
    """A rule is keyed on USB ids, and how we learn them decides whether a
    whole class of device works.

    A DirectInput device's stored path is its DirectInput instance GUID, not
    a Windows HID path - it carries no ids at all. Parsing the path was the
    original approach and it silently excluded every DirectInput stick,
    which is the hardware this backend exists to support.
    """

    GUID = "{A1B2C3D4-0000-0000-0000-504944564944}"
    HID = r"\?\HID#VID_FFFF&PID_2052&MI_00#x"

    def test_a_directinput_device_is_usable(self):
        device, = configured_devices(FakeSettings({
            "devpath_joystick": self.GUID,
            "devident_joystick": "SideWinder Force Feedback 2",
            "devids_joystick": "045E:001B"}))
        assert device.usable and device.key == "045E:001B"

    def test_the_reported_ids_win_over_anything_in_the_path(self):
        """The path can name a hub or a composite interface; the device's own
        answer is the authority."""
        device, = configured_devices(FakeSettings({
            "devpath_joystick": self.HID,
            "devids_joystick": "045E:001B"}))
        assert device.key == "045E:001B"

    def test_a_hid_path_still_works_without_stored_ids(self):
        """Settings written before the ids were stored must keep working."""
        device, = configured_devices(FakeSettings({
            "devpath_pedals": self.HID}))
        assert device.usable and device.key == "FFFF:2052"

    def test_a_device_reporting_no_ids_is_not_usable(self):
        """Better unoffered than given a rule keyed on 0000:0000, which would
        match nothing or, worse, something else."""
        device, = configured_devices(FakeSettings({
            "devpath_joystick": self.GUID, "devids_joystick": ""}))
        assert not device.usable

    def test_a_malformed_stored_id_does_not_become_a_rule(self):
        device, = configured_devices(FakeSettings({
            "devpath_joystick": self.GUID, "devids_joystick": "garbage"}))
        assert not device.usable
