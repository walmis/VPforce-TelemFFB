"""Choosing which devices a sim hands to TelemFFB.

The dialog is the only point where the user says what the tap should do, so
what matters is what it offers and what it returns - not how it looks.
Offering a device that cannot be given a working rule, or returning one the
user unchecked, both end as silent force feedback faults in a cockpit.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.TapDeviceDialog import TapDeviceDialog
from telemffb.tap_install import SIMS_BY_KEY, TapDevice

pytestmark = [pytest.mark.unit]

DCS = SIMS_BY_KEY["DCS"]            # joystick only
KOREA = SIMS_BY_KEY["IL2_K"]        # joystick and pedals

RHINO = TapDevice("joystick", 0xFFFF, 0x2054, "VPforce Rhino")
PEDALS = TapDevice("pedals", 0xFFFF, 0x2052, "VPforce Pedals")
COLLECTIVE = TapDevice("collective", 0xFFFF, 0x2055, "VPforce Collective")
UNREADABLE = TapDevice("joystick", None, None, "Some Stick")
#: A DirectInput stick swapped into the joystick slot.
PEDALS_AS_JOYSTICK = TapDevice("joystick", 0x045E, 0x001B, "SideWinder")

#: An ffb-fix config of the kind a user may already have, with a name-keyed
#: rule that would outrank anything we append for the same device.
FFB_FIX = """\
[FFBDevices]
Warthog=block
Rhino=allow
"""


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def boxes(dialog):
    return dialog.findChildren(QtWidgets.QCheckBox)


def device_boxes(dialog):
    """Only the per-device checkboxes, not the nested replace-rule ones."""
    return [b for b, _, _ in dialog._rows]


def text_of(dialog):
    return " ".join(w.text() for w in dialog.findChildren(QtWidgets.QLabel))


class TestOnlyWhatTheSimSupports:
    def test_a_role_the_sim_renders_to_is_offered(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO])
        assert len(device_boxes(dialog)) == 1

    def test_a_role_it_does_not_render_to_is_not(self, app):
        """Tapping a control DCS never sends effects to would look exactly
        like TelemFFB failing."""
        dialog = TapDeviceDialog(DCS, [RHINO, PEDALS])
        assert len(device_boxes(dialog)) == 1
        assert "Rhino" in device_boxes(dialog)[0].text()

    def test_the_same_device_is_offered_where_the_sim_does_support_it(self, app):
        dialog = TapDeviceDialog(KOREA, [RHINO, PEDALS])
        assert len(device_boxes(dialog)) == 2



    def test_no_sim_renders_to_a_collective(self, app):
        dialog = TapDeviceDialog(KOREA, [COLLECTIVE])
        assert device_boxes(dialog) == []


class TestWhatIsOffered:
    def test_a_device_with_no_readable_ids_is_not_offered(self, app):
        dialog = TapDeviceDialog(DCS, [UNREADABLE])
        assert device_boxes(dialog) == []





class TestDefaults:
    def test_supported_devices_start_checked(self, app):
        """Installing the wrapper at all means wanting TelemFFB to render."""
        dialog = TapDeviceDialog(KOREA, [RHINO, PEDALS])
        assert all(b.isChecked() for b in device_boxes(dialog))



    def test_a_device_with_only_a_block_rule_still_starts_checked(self, app):
        """Swapping in a stick the old config happened to block should not
        arrive switched off."""
        dialog = TapDeviceDialog(DCS, [RHINO],
                                 existing="[FFBDevices]\nRhino=block\n")
        assert device_boxes(dialog)[0].isChecked()


class TestAdoptingSomebodyElsesConfig:
    def test_a_rule_that_would_outrank_ours_is_surfaced(self, app):
        """The wrapper takes the first match, so an existing Rhino=allow
        beats a rule appended below it."""
        dialog = TapDeviceDialog(DCS, [RHINO], existing=FFB_FIX)
        assert any("Rhino=allow" in b.text() for b in boxes(dialog))

    def test_an_unrelated_rule_is_not_mentioned(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO], existing=FFB_FIX)
        assert not any("Warthog" in b.text() for b in boxes(dialog))

    def test_replacing_it_is_the_default(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO], existing=FFB_FIX)
        assert dialog.retire_lines() == [2]

    def test_the_user_can_decline_and_keep_their_rule(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO], existing=FFB_FIX)
        retire = [b for b in boxes(dialog) if "Rhino=allow" in b.text()][0]
        retire.setChecked(False)
        assert dialog.retire_lines() == []

    def test_nothing_is_retired_for_a_device_left_unchecked(self, app):
        """Retiring a rule for a device we are not tapping would take away
        force feedback and put nothing in its place."""
        dialog = TapDeviceDialog(DCS, [RHINO], existing=FFB_FIX)
        device_boxes(dialog)[0].setChecked(False)
        assert dialog.retire_lines() == []

    def test_an_existing_tap_rule_needs_no_replacing(self, app):
        """It already does what we would add; there is nothing to displace."""
        ours = "[FFBDevices]\nFFFF:2054=tap\n"
        dialog = TapDeviceDialog(DCS, [RHINO], existing=ours)
        assert dialog.retire_lines() == []
        assert not any("replace" in b.text() for b in boxes(dialog))

    def test_no_config_means_no_replacements_to_consider(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO])
        assert dialog.retire_lines() == []

    def test_unchecking_a_tapped_device_retires_our_own_rule(self, app):
        """Otherwise the checkbox moves and the file does not, and the
        device stays tapped."""
        ours = "[FFBDevices]\nFFFF:2054=tap\n"
        dialog = TapDeviceDialog(DCS, [RHINO], existing=ours)
        assert dialog.retire_lines() == []          # still checked
        device_boxes(dialog)[0].setChecked(False)
        assert dialog.retire_lines() == [1]


class TestWhatComesBack:
    def test_only_the_checked_devices(self, app):
        dialog = TapDeviceDialog(KOREA, [RHINO, PEDALS])
        for box in device_boxes(dialog):
            if "2054" in box.text():
                box.setChecked(False)
        assert [d.key for d in dialog.chosen()] == ["FFFF:2052"]

    def test_unchecking_everything_is_a_valid_answer(self, app):
        """A wrapper installed but handing nothing over - inert, not broken."""
        dialog = TapDeviceDialog(DCS, [RHINO])
        device_boxes(dialog)[0].setChecked(False)
        assert dialog.chosen() == []
class TestItReadsOnBothThemes:
    """The app ships light and dark modes. palette(mid) is a frame-shading
    color, not a text color - on a dark theme it is a dark gray on a dark
    ground, which is how the explanation went invisible."""

    @staticmethod
    def notes(dialog):
        return [w for w in dialog.findChildren(QtWidgets.QLabel)
                if "color:" in w.styleSheet()]

    @staticmethod
    def palette_for(dark):
        from PyQt6.QtGui import QColor, QPalette
        palette = QPalette()
        ground, ink = ((QColor(32, 32, 32), QColor(230, 230, 230)) if dark
                       else (QColor(245, 245, 245), QColor(20, 20, 20)))
        palette.setColor(QPalette.ColorRole.Window, ground)
        palette.setColor(QPalette.ColorRole.WindowText, ink)
        palette.setColor(QPalette.ColorGroup.Disabled,
                         QPalette.ColorRole.WindowText,
                         QColor(140, 140, 140))
        return palette

    @pytest.mark.parametrize("dark", [True, False])
    def test_secondary_text_stands_off_the_background(self, app, dark):
        from PyQt6.QtGui import QColor, QPalette

        original = app.palette()
        try:
            app.setPalette(self.palette_for(dark))
            dialog = TapDeviceDialog(DCS, [RHINO, PEDALS])
            ground = self.palette_for(dark).color(QPalette.ColorRole.Window)
            for label in self.notes(dialog):
                color = QColor(label.styleSheet().split("color:")[1]
                                .strip().rstrip(";"))
                assert abs(color.lightness() - ground.lightness()) > 40, \
                    f"unreadable on a {'dark' if dark else 'light'} theme"
        finally:
            app.setPalette(original)

    def test_the_explanation_is_one_of_them(self, app):
        """Guards the test above against passing because it found nothing."""
        dialog = TapDeviceDialog(DCS, [RHINO, PEDALS])
        assert any("DirectInput Tap" in w.text() for w in self.notes(dialog))


class TestTheDeviceThatLeft:
    """After a swap the config still taps the old device. It is listed so it
    can go, rather than removed behind the user's back - the hardware may
    just be unplugged today."""

    SWAPPED = ("[FFBDevices]\n"
               "FFFF:2054=tap    ; Monster (joystick)\n")

    def test_it_is_listed(self, app):
        dialog = TapDeviceDialog(DCS, [PEDALS_AS_JOYSTICK], existing=self.SWAPPED)
        assert any("FFFF:2054" in b.text() for b in boxes(dialog))


    def test_it_starts_unchecked_so_leaving_it_alone_removes_it(self, app):
        dialog = TapDeviceDialog(DCS, [PEDALS_AS_JOYSTICK], existing=self.SWAPPED)
        orphan, = [b for b, _ in dialog._orphans]
        assert not orphan.isChecked()
        assert dialog.retire_lines() == [1]

    def test_checking_it_keeps_the_rule(self, app):
        """For hardware that is merely unplugged, not gone."""
        dialog = TapDeviceDialog(DCS, [PEDALS_AS_JOYSTICK], existing=self.SWAPPED)
        dialog._orphans[0][0].setChecked(True)
        assert dialog.retire_lines() == []

    def test_it_is_not_returned_as_a_chosen_device(self, app):
        """Its rule already exists; re-adding it would duplicate the line."""
        dialog = TapDeviceDialog(DCS, [PEDALS_AS_JOYSTICK], existing=self.SWAPPED)
        dialog._orphans[0][0].setChecked(True)
        assert [d.key for d in dialog.chosen()] == ["045E:001B"]

    def test_a_still_selected_device_is_not_listed_as_departed(self, app):
        dialog = TapDeviceDialog(
            DCS, [TapDevice("joystick", 0xFFFF, 0x2054, "Monster")],
            existing=self.SWAPPED)
        assert dialog._orphans == []

    def test_a_block_rule_is_never_offered_for_removal(self, app):
        """Not ours, and not about TelemFFB - the user manages that one."""
        dialog = TapDeviceDialog(DCS, [PEDALS_AS_JOYSTICK],
                                 existing="[FFBDevices]\nWarthog=block\n")
        assert dialog._orphans == []

    def test_nothing_is_said_when_no_rule_has_been_orphaned(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO], existing="[FFBDevices]\n")
        assert "no longer selected" not in text_of(dialog)


class TestHavingTheGameSeeTheDeviceFirst:
    """A game that hands force feedback to whichever device it enumerated
    first can leave a tapped device with no effects at all - and then there
    is nothing for TelemFFB to render, with no error to explain it.

    Offered for DCS only: it is the one we have watched strand a stick, and
    some games identify a device by its position, so reordering underneath
    one of those would disturb its bindings."""

    @pytest.mark.parametrize("sim, offered", [
        (DCS, True), (KOREA, False),
        (SIMS_BY_KEY["IL2"], False), (SIMS_BY_KEY["BMS"], False)])
    def test_only_dcs_is_offered_it(self, app, sim, offered):
        """DCS is the one we have watched strand a stick. Elsewhere the
        risk of disturbing position-based bindings outweighs a fix for a
        problem we have not seen."""
        assert (TapDeviceDialog(sim, [RHINO])._order_box is not None) is offered


    def test_it_defaults_on(self, app):
        """No evidence it harms anything, and it guarantees the device the
        user picked is the one the game drives."""
        assert TapDeviceDialog(DCS, [RHINO])._order_box.isChecked()

    def test_the_order_is_the_devices_they_chose(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO])
        assert [d.key for d in dialog.ordered()] == ["FFFF:2054"]

    def test_unchecking_a_device_drops_it_from_the_order_too(self, app):
        """Ordering exists so the game drives these devices; one that is not
        being tapped has no reason to be promoted."""
        dialog = TapDeviceDialog(DCS, [RHINO])
        device_boxes(dialog)[0].setChecked(False)
        assert dialog.ordered() == []

    def test_declining_writes_no_order(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO])
        dialog._order_box.setChecked(False)
        assert dialog.ordered() == []
        assert dialog.chosen()          # the tap rule is still wanted

    def test_nothing_to_order_means_no_offer(self, app):
        """A sim with no usable device has nothing to promote."""
        assert TapDeviceDialog(DCS, [UNREADABLE])._order_box is None

    def test_an_existing_order_section_keeps_the_box_usable(self, app):
        """It was disabled whenever a section existed, which left the one
        config that might need changing unable to change."""
        dialog = TapDeviceDialog(DCS, [RHINO],
                                 existing="[DeviceOrder]\n1=FFFF:2054\n")
        assert dialog._order_box.isEnabled()
        assert dialog._order_box.isChecked()

    def test_unticking_retires_the_existing_entries(self, app):
        """Unticking is how the ordering comes back out."""
        dialog = TapDeviceDialog(DCS, [RHINO],
                                 existing="[DeviceOrder]\n1=FFFF:2054\n")
        dialog._order_box.setChecked(False)
        assert 1 in dialog.retire_lines()

    def test_leaving_it_ticked_retires_nothing(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO],
                                 existing="[DeviceOrder]\n1=FFFF:2054\n")
        assert dialog.retire_lines() == []

    def test_an_entry_for_another_device_is_not_touched(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO],
                                 existing="[DeviceOrder]\n1=Warthog\n")
        dialog._order_box.setChecked(False)
        assert dialog.retire_lines() == []

class TestConfirmingIsOnlyOfferedWhenItWouldDoSomething:
    """A dialog whose OK writes a byte-identical file teaches people that
    confirming is meaningless. It also has nothing to preview."""

    SETTLED = ("[General]\nRequireTelemFFB=true\n\n[FFBDevices]\n"
               "FFFF:2054=tap    ; VPforce Rhino (joystick)\n"
               "\n[DeviceOrder]\n1=FFFF:2054    ; VPforce Rhino (joystick)\n")

    def preview_of(self, existing):
        from telemffb.tap_config import (already_blocked, already_ordered,
                                         already_tapped, amend, lines_for,
                                         retired_identities)
        from telemffb.tap_install import block_line, order_line, rule_line

        def preview(chosen, retire, ordered, blocked):
            names = retired_identities(existing, retire)
            lines = lines_for(existing, *names)
            rules = [rule_line(d) for d in chosen
                     if not already_tapped(existing, (d.vid, d.pid), d.ident,
                                           lines)]
            rules += [block_line(d) for d in blocked
                      if not already_blocked(existing, (d.vid, d.pid),
                                             d.ident, lines)]
            from telemffb.tap_install import order_entries
            return [("dinput8.ini", existing,
                     amend(existing, rules,
                           lines, order=order_entries(ordered)))]
        return preview

    def ok(self, dialog):
        from PyQt6.QtWidgets import QDialogButtonBox
        box = dialog.findChild(QDialogButtonBox)
        return box.button(QDialogButtonBox.StandardButton.Ok)

    def test_a_settled_config_cannot_be_confirmed(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO], existing=self.SETTLED,
                                 preview=self.preview_of(self.SETTLED))
        assert not self.ok(dialog).isEnabled()

    def test_changing_a_box_makes_it_available_again(self, app):
        dialog = TapDeviceDialog(DCS, [RHINO], existing=self.SETTLED,
                                 preview=self.preview_of(self.SETTLED))
        device_boxes(dialog)[0].setChecked(False)
        assert self.ok(dialog).isEnabled()

    def test_a_fresh_config_can_be_confirmed(self, app):
        empty = "[FFBDevices]\n"
        dialog = TapDeviceDialog(DCS, [RHINO], existing=empty,
                                 preview=self.preview_of(empty))
        assert self.ok(dialog).isEnabled()

    def test_without_a_preview_ok_stays_available(self, app):
        """Nothing to compare against is not the same as nothing to do."""
        dialog = TapDeviceDialog(DCS, [RHINO])
        assert self.ok(dialog).isEnabled()


    def look(self, dialog):
        from PyQt6.QtWidgets import QDialogButtonBox, QPushButton
        return [b for b in dialog.findChild(QDialogButtonBox)
                .findChildren(QPushButton) if b.text() == "Preview"][0]

    def test_preview_goes_with_ok(self, app):
        """A preview of no change is the same non-answer as an OK that
        writes an identical file."""
        dialog = TapDeviceDialog(DCS, [RHINO], existing=self.SETTLED,
                                 preview=self.preview_of(self.SETTLED))
        assert not self.look(dialog).isEnabled()
        device_boxes(dialog)[0].setChecked(False)
        assert self.look(dialog).isEnabled()

class TestBlockingWhatTheSimWillNotDrive:
    """A device in a role the sim never renders to gets nothing from the
    game either way - but while it enumerates as a force feedback device it
    can still take a slot from the stick that wanted one."""

    ALL = [RHINO, PEDALS, COLLECTIVE]

    def block_boxes(self, dialog):
        return [(b, d) for b, d, _ in dialog._blocks]

    def test_dcs_offers_to_block_the_roles_it_does_not_drive(self, app):
        dialog = TapDeviceDialog(DCS, self.ALL)
        assert sorted(d.role for _, d in self.block_boxes(dialog)) == \
            ["collective", "pedals"]

    def test_korea_is_not_offered_its_pedals(self, app):
        """It drives them natively, so blocking would take away something
        real. Falls out of the capability table rather than a special case."""
        dialog = TapDeviceDialog(KOREA, self.ALL)
        assert [d.role for _, d in self.block_boxes(dialog)] == ["collective"]
        assert "pedals" in [d.role for d in dialog.chosen()]

    def test_they_start_checked(self, app):
        dialog = TapDeviceDialog(DCS, self.ALL)
        assert [d.role for d in dialog.blocked()] == ["pedals", "collective"]

    def test_a_device_with_no_usable_ids_is_not_offered(self, app):
        """A block is keyed on ids like any other rule."""
        vague = TapDevice("collective", None, None, "Mystery")
        dialog = TapDeviceDialog(DCS, [RHINO, vague])
        assert self.block_boxes(dialog) == []

    def test_unticking_retires_an_existing_block(self, app):
        existing = "[FFBDevices]\nFFFF:2052=block\n"
        dialog = TapDeviceDialog(DCS, [RHINO, PEDALS], existing=existing)
        box, _ = self.block_boxes(dialog)[0]
        box.setChecked(False)
        assert 1 in dialog.retire_lines()

    def test_leaving_it_ticked_retires_nothing(self, app):
        existing = "[FFBDevices]\nFFFF:2052=block\n"
        dialog = TapDeviceDialog(DCS, [RHINO, PEDALS], existing=existing)
        assert dialog.retire_lines() == []


class TestFixOnlyMode:
    """In FFB-Fix only mode the wrapper relays nothing, so there is no
    tap to configure.  Opening this dialog on such a config used to
    offer the joystick as a tap candidate, ticked - and OK wrote
    FFFF:2054=tap, converting the install back to a tapping one without
    saying so."""

    FIX = ("[FFBDevices]\r\nFFFF:2052=block\r\nvJoy=block\r\n"
           "[DeviceOrder]\r\n1=FFFF:2054\r\n")
    TAPPING = ("[FFBDevices]\r\nFFFF:2054=tap\r\nFFFF:2052=block\r\n"
               "[DeviceOrder]\r\n1=FFFF:2054\r\n")

    def _dialog(self, app, existing=None, fix_only=True):
        return TapDeviceDialog(DCS, [RHINO, PEDALS], existing=existing,
                               fix_only=fix_only)

    def test_no_device_is_offered_for_tapping(self, app):
        assert self._dialog(app, self.FIX).chosen() == []

    def test_the_joystick_gets_no_rule_at_all(self, app):
        """The game drives it; a device with no matching rule is
        untouched by the wrapper, which is exactly what is wanted."""
        dialog = self._dialog(app, self.FIX)
        assert RHINO not in dialog.chosen()
        assert RHINO not in dialog.blocked()

    def test_blocking_is_still_a_choice(self, app):
        """Half of what the fix does, so it stays configurable."""
        assert PEDALS in self._dialog(app, self.FIX).blocked()

    def test_the_joystick_is_still_ordered_first(self, app):
        """The other half.  Ordering follows what the sim drives, not
        what is tapped - nothing is."""
        assert self._dialog(app, self.FIX).ordered() == [RHINO]

    def test_existing_tap_rules_are_offered_for_removal(self, app):
        """Every tap rule contradicts this mode, not just the ones
        naming hardware that has gone."""
        dialog = self._dialog(app, self.TAPPING)
        assert [r.key for _b, r in dialog._orphans] == ["FFFF:2054"]

    def test_the_box_is_the_action_and_starts_ticked(self, app):
        """Elsewhere a clear box means "not tapped", so removal is what
        you get by leaving it alone.  That reads backwards here, where
        removing is the whole point - so the box is the action, and it
        starts ticked: wanting the rule gone is the only reason to be in
        this mode."""
        dialog = self._dialog(app, self.TAPPING)
        box, _rule = dialog._orphans[0]
        assert "Remove the tap rule" in box.text()
        assert box.isChecked()
        assert dialog.retire_lines() == [1]

    def test_clearing_it_keeps_the_rule(self, app):
        dialog = self._dialog(app, self.TAPPING)
        box, _rule = dialog._orphans[0]
        box.setChecked(False)
        assert dialog.retire_lines() == []

    def test_it_says_what_removing_it_means(self, app):
        dialog = self._dialog(app, self.TAPPING)
        notes = " ".join(w.text() for w in
                         dialog.findChildren(QtWidgets.QLabel))
        assert "still has a tap configuration" in notes
        assert "send its effects to the device directly" in notes

    def test_the_normal_mode_keeps_its_own_polarity(self, app):
        """A stale rule there is removed by leaving its box clear, and
        that is unchanged."""
        gone = "[FFBDevices]\r\n045E:001B=tap    ; SideWinder\r\n"
        dialog = TapDeviceDialog(DCS, [RHINO], existing=gone,
                                 fix_only=False)
        box, _rule = dialog._orphans[0]
        assert not box.isChecked()
        assert dialog.retire_lines() == [1], "clear box removes it"

    def test_the_dialog_says_which_mode_it_is_in(self, app):
        dialog = self._dialog(app, self.FIX)
        assert "FFB-Fix" in dialog.windowTitle()
        texts = " ".join(w.text() for w in
                         dialog.findChildren(QtWidgets.QLabel))
        assert "nothing is handed to TelemFFB" in texts

    def test_tap_mode_is_unchanged(self, app):
        """The normal path still offers the joystick, ticked."""
        dialog = self._dialog(app, self.FIX, fix_only=False)
        assert dialog.chosen() == [RHINO]
