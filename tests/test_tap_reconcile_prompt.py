"""Raising a stale tap config at the moment the user changes a device.

This is the trigger, not the logic - what pending_reconcile decides is
covered in test_tap_reconcile.py. What matters here is that the question
reaches the user when it should, does not when it should not, and that
declining leaves every file alone.

The prompt fires from the settings dialog's save, because that is when the
user has just made the change and can still connect the question to what
they did.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G
from telemffb.tap_config import Rule
from telemffb.SystemSettingsDialog import (CLEANUP_CANCELLED,
                                          CLEANUP_LEAVE,
                                          CLEANUP_REMOVE)
from telemffb.tap_install import SIMS_BY_KEY, SimStatus, TapDevice
from telemffb.tap_reconcile import ReconcileItem, TapGap

pytestmark = [pytest.mark.unit]

OLD_PATH = r"\\?\HID#VID_FFFF&PID_2054&MI_00#x"
NEW_PATH = r"\\?\HID#VID_044F&PID_B10A&MI_00#x"


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def dialog(app, monkeypatch):
    """A settings dialog with just enough around it to call the hook."""
    monkeypatch.setattr(G, 'system_settings', {}, raising=False)
    from telemffb.SystemSettingsDialog import SystemSettingsDialog
    instance = SystemSettingsDialog.__new__(SystemSettingsDialog)
    # PyQt raises from __getattr__ on an instance whose __init__ never ran,
    # so anything the code under test reads has to be present
    instance.tap_panels = {}
    instance._pending_devpaths = {}
    return instance


def an_item():
    status = SimStatus(sim=SIMS_BY_KEY["DCS"], root=r"C:\DCS",
                       provenance="test")
    return ReconcileItem(status=status, directory=r"C:\DCS\bin",
                         config="[FFBDevices]\n",
                         obsolete=[Rule("FFFF:2054", "tap", 1,
                                        ids=(0xFFFF, 0x2054))],
                         replacement="044F:B10A=tap    ; Warthog (joystick)")


@pytest.fixture
def spy(monkeypatch):
    """Watch what the hook asks and what it applies."""
    from telemffb import tap_reconcile
    import telemffb.SystemSettingsDialog as module

    state = {"asked": [], "applied": [], "items": [an_item()]}
    monkeypatch.setattr(tap_reconcile, 'pending_reconcile',
                        lambda changes, settings, statuses=None: state["items"])
    monkeypatch.setattr(tap_reconcile, 'apply_reconcile',
                        lambda items: state["applied"].append(items) or [])

    def notice(parent, title, text, *args, **kwargs):
        state["asked"].append(text)
        return QtWidgets.QMessageBox.StandardButton.Ok

    monkeypatch.setattr(module.QMessageBox, 'information', notice)
    return state


class TestWhenTheNoticeAppears:
    def test_a_swapped_device_raises_it(self, dialog, spy):
        dialog._offer_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert len(spy["asked"]) == 1

    def test_an_unchanged_slot_raises_nothing(self, dialog, spy):
        dialog._offer_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": OLD_PATH})
        assert spy["asked"] == []

    def test_nothing_is_said_when_no_sim_names_the_device(self, dialog, spy):
        """The gate that keeps this notice worth reading."""
        spy["items"] = []
        dialog._offer_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert spy["asked"] == []


class TestWhatTheNoticeSays:
    def test_it_says_what_happens_if_ignored(self, dialog, spy):
        """A notice that does not say what is at stake gets dismissed."""
        dialog._offer_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        said = spy["asked"][0]
        assert "keep tapping the old device" in said
        assert "leave the new one alone" in said

    def test_at_the_change_it_says_the_update_is_staged(self, dialog, spy):
        """A notice, not a question: the only effect a 'No' could have had
        was leaving a config pointing at hardware no longer in the slot.
        Cancelling the dialog is how to decline."""
        dialog._raise_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert "staged" in spy["asked"][0]
        assert "Cancel the dialog" in spy["asked"][0]


class TestWhenItIsWritten:
    def test_the_staged_update_is_applied_at_save(self, dialog, spy):
        dialog._offer_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert len(spy["applied"]) == 1

    def test_nothing_is_written_at_the_change(self, dialog, spy):
        """Backing out of the dialog has to leave the game folders alone -
        the selection that justified the rewrite was never saved."""
        dialog._raise_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert spy["applied"] == []


class TestTheDialogSuppliesLiveDevices:
    """tap_devices is what every tap question keys off, so it has to reflect
    the dialog rather than the registry."""

    def test_an_unsaved_pick_wins_over_the_stored_one(self, dialog,
                                                      monkeypatch):
        monkeypatch.setattr(G, 'system_settings',
                            {"devpath_joystick": OLD_PATH,
                             "devident_joystick": "Monster"}, raising=False)
        dialog._pending_devpaths = {"devpath_joystick": NEW_PATH,
                                    "devident_joystick": "Sidewinder"}
        device, = dialog.tap_devices()
        assert device.key == "044F:B10A"
        assert device.ident == "Sidewinder"


    def test_clearing_a_slot_in_the_dialog_empties_it(self, dialog,
                                                      monkeypatch):
        monkeypatch.setattr(G, 'system_settings',
                            {"devpath_joystick": OLD_PATH}, raising=False)
        dialog._pending_devpaths = {"devpath_joystick": ""}
        assert dialog.tap_devices() == []


class TestRaisingItAtTheMomentOfTheChange:
    """The change is what the user just did; by the time they close the
    dialog they may not connect a notice about DCS to a combo box they
    touched ten minutes ago."""

    def test_changing_a_device_says_so_straight_away(self, dialog, spy):
        dialog._raise_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert len(spy["asked"]) == 1

    def test_changing_again_does_not_say_it_again(self, dialog, spy):
        """Cycling through devices to see what is there must not produce a
        popup per step."""
        for _ in range(3):
            dialog._raise_tap_reconcile({"devpath_joystick": OLD_PATH},
                                        {"devpath_joystick": NEW_PATH})
        assert len(spy["asked"]) == 1

    def test_a_change_no_sim_cares_about_stays_silent(self, dialog, spy):
        spy["items"] = []
        dialog._raise_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert spy["asked"] == []


class TestSavingWritesWhatWasStaged:
    def test_applied_without_a_second_notice(self, dialog, spy):
        dialog._raise_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        spy["asked"].clear()
        dialog._offer_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert spy["asked"] == [], "said a second time"
        assert len(spy["applied"]) == 1

    def test_a_change_never_mentioned_is_mentioned_at_save_then_written(
            self, dialog, spy):
        """The change-time notice is a courtesy, not the only guard - a
        selection altered by some other path is still picked up, and still
        said before it is written."""
        dialog._offer_tap_reconcile({"devpath_joystick": OLD_PATH},
                                    {"devpath_joystick": NEW_PATH})
        assert len(spy["asked"]) == 1 and len(spy["applied"]) == 1
        assert "written now" in spy["asked"][0]


class TestSettingsThatPredateStoredIds:
    """The bug that made reconcile inert for real users.

    A settings base written before devids_ existed holds only a devpath, and
    a DirectInput device's devpath is an instance GUID with no ids in it. So
    the outgoing device resolved to "no ids", the change was dropped, and
    the log honestly reported that no sim had a rule for it - while the sim
    configs were full of rules for exactly that device.

    The connected hardware knows its own ids and is listed in the selector,
    so the gap is filled from there.
    """

    class Device:
        def __init__(self, path, vid, pid):
            self.path, self.vendor_id, self.product_id = path, vid, pid

    class Model:
        def __init__(self, devices):
            self._devices = devices

        def rowCount(self):
            return len(self._devices)

        def index(self, row, _col):
            return row

        def data(self, row, _role):
            return self._devices[row]

    class Combo:
        def __init__(self, model):
            self._model = model

        def model(self):
            return self._model

    GUID = "{9E573A80-3F1B-11F0-8001-444553540000}"

    @pytest.fixture
    def legacy(self, dialog, monkeypatch):
        """Monster selected, its ids never stored, and it is plugged in."""
        monkeypatch.setattr(G, 'system_settings', {
            "devpath_joystick": self.GUID,
            "devident_joystick": "[DI] VPforce Rhino FFB Monster"},
            raising=False)
        dialog._device_combos = [self.Combo(self.Model(
            [self.Device(self.GUID.encode(), 0xFFFF, 0x2054)]))]
        return dialog

    def test_the_ids_are_recovered_from_the_connected_device(self, legacy):
        device, = legacy.tap_devices()
        assert device.usable and device.key == "FFFF:2054"

    def test_so_a_swap_away_from_it_is_seen(self, legacy):
        from telemffb.tap_reconcile import device_changes
        baseline = legacy.tap_settings_view()
        legacy._pending_devpaths = {"devpath_joystick": "{OTHER}",
                                    "devids_joystick": "045E:001B"}
        change, = device_changes(baseline, legacy.tap_settings_view())
        assert change.was == (0xFFFF, 0x2054), "the outgoing device is known"

    def test_a_device_that_is_not_connected_stays_unknown(self, legacy):
        """Honest: nothing on screen can tell us, so nothing is invented."""
        legacy._device_combos = []
        device, = legacy.tap_devices()
        assert not device.usable

    def test_stored_ids_are_not_second_guessed(self, legacy):
        """Only gaps are filled - a device that reported its ids is right."""
        legacy._pending_devpaths = {"devpath_joystick": self.GUID,
                                    "devids_joystick": "AAAA:BBBB"}
        device, = legacy.tap_devices()
        assert device.key == "AAAA:BBBB"


class TestOptingASimBackOut:
    """Removing files from a game folder on the strength of a checkbox has
    several distinct wrong answers, so most of what is pinned here is the
    dialog declining to act."""

    class Box:
        """A switch, as much of one as these tests touch.

        blockSignals is part of that: restoring a switch has to suppress the
        handler it would otherwise re-run, so a stand-in without it hides
        whether the real code remembered to."""

        def __init__(self, checked=False):
            self._checked = checked
            self.blocked = False

        def isChecked(self):
            return self._checked

        def setChecked(self, value):
            self._checked = value

        def blockSignals(self, value):
            self.blocked = value

    @pytest.fixture
    def cleanup(self, dialog, monkeypatch):
        import telemffb.SystemSettingsDialog as module
        from telemffb.tap_install import SimStatus
        from telemffb.tap_reconcile import TapCleanup

        plan = TapCleanup(status=SimStatus(sim=SIMS_BY_KEY["DCS"], root="r",
                                           provenance="t"),
                          delete_config=[r"C:\DCS\bin"],
                          remove_wrapper=[r"C:\DCS\bin"])
        state = {"asked": [], "applied": [], "answer": CLEANUP_REMOVE, "plan": plan}

        monkeypatch.setattr(module.SystemSettingsDialog, '_tap_status',
                            lambda self, key: plan.status)
        import telemffb.tap_reconcile as ti
        monkeypatch.setattr(ti, 'plan_tap_cleanup', lambda s: state["plan"])
        monkeypatch.setattr(ti, 'apply_tap_cleanup',
                            lambda plans: state["applied"].append(plans) or [])

        # The cleanup prompt asks through _ask_with_preview, which offers a
        # third button and can loop; the tests care what was asked and what
        # came back, so that is the seam rather than QMessageBox itself.
        def ask(self, message, plan):
            state["asked"].append(message)
            return state["answer"]

        monkeypatch.setattr(module.SystemSettingsDialog,
                            '_ask_with_preview', ask)
        dialog.tap_enable_boxes = {"DCS": self.Box(False)}
        return state

    def test_turning_it_off_asks(self, dialog, cleanup):
        dialog._offer_tap_cleanup("DCS")
        assert len(cleanup["asked"]) == 1
        assert "DCS World" in cleanup["asked"][0]

    def test_nothing_is_removed_before_saving(self, dialog, cleanup):
        """A switch the user then backs out of must not have deleted a file
        from a game folder."""
        dialog._offer_tap_cleanup("DCS")
        assert cleanup["applied"] == []

    def test_saving_carries_it_out(self, dialog, cleanup):
        dialog._offer_tap_cleanup("DCS")
        dialog._apply_tap_cleanup()
        assert len(cleanup["applied"]) == 1

    def test_declining_removes_nothing_ever(self, dialog, cleanup):
        cleanup["answer"] = CLEANUP_LEAVE
        dialog._offer_tap_cleanup("DCS")
        dialog._apply_tap_cleanup()
        assert cleanup["applied"] == []

    def test_turning_it_back_on_cancels_the_removal(self, dialog, cleanup):
        """Agreeing, changing your mind, then saving must not delete
        anything - the switch is on again by the time it matters."""
        dialog._offer_tap_cleanup("DCS")
        dialog.tap_enable_boxes["DCS"].setChecked(True)
        dialog._apply_tap_cleanup()
        assert cleanup["applied"] == []

    def test_it_is_asked_once_however_often_the_switch_moves(self, dialog,
                                                             cleanup):
        for _ in range(3):
            dialog._offer_tap_cleanup("DCS")
        assert len(cleanup["asked"]) == 1

    def test_nothing_installed_asks_nothing(self, dialog, cleanup):
        from telemffb.tap_install import SimStatus
        from telemffb.tap_reconcile import TapCleanup
        cleanup["plan"] = TapCleanup(
            status=SimStatus(sim=SIMS_BY_KEY["DCS"], root="r", provenance="t"))
        dialog._offer_tap_cleanup("DCS")
        assert cleanup["asked"] == []

    def test_the_offer_says_leaving_it_is_fine(self, dialog, cleanup):
        """It is genuinely harmless, and a removal prompt that reads as a
        warning pushes people into deleting things they wanted."""
        dialog._offer_tap_cleanup("DCS")
        assert "harmless" in cleanup["asked"][0]


class TestTurningASimOff:
    """Disabling a sim leaves its tap set up for a sim that will not run.
    Offered, not done - and the IL-2 switch covers two titles, so one
    action can strand two installs."""

    class Box(TestOptingASimBackOut.Box):
        pass

    @pytest.fixture
    def cleanup(self, dialog, monkeypatch):
        import telemffb.SystemSettingsDialog as module
        import telemffb.tap_reconcile as ti
        from telemffb.tap_install import SimStatus
        from telemffb.tap_reconcile import TapCleanup

        state = {"asked": [], "keys": [], "answer": CLEANUP_REMOVE}

        def plan_for(status):
            return TapCleanup(status=status,
                              delete_config=[r"C:\x"], remove_wrapper=[r"C:\x"])

        def status_for(_self, key):
            state["keys"].append(key)
            return SimStatus(sim=SIMS_BY_KEY[key], root="r", provenance="t")

        monkeypatch.setattr(module.SystemSettingsDialog, '_tap_status',
                            status_for)
        monkeypatch.setattr(module.SystemSettingsDialog, 'refresh_tap_panels',
                            lambda self: None)
        monkeypatch.setattr(ti, 'plan_tap_cleanup', plan_for)
        monkeypatch.setattr(ti, 'apply_tap_cleanup', lambda plans: [])

        # The cleanup prompt asks through _ask_with_preview, which offers a
        # third button and can loop; the tests care what was asked and what
        # came back, so that is the seam rather than QMessageBox itself.
        def ask(self, message, plan):
            state["asked"].append(message)
            return state["answer"]

        monkeypatch.setattr(module.SystemSettingsDialog,
                            '_ask_with_preview', ask)
        dialog.tap_enable_boxes = {k: self.Box(True)
                                   for k in ("DCS", "IL2", "IL2_K", "BMS")}
        return state

    def test_disabling_a_sim_offers_to_take_its_tap_out(self, dialog, cleanup):
        dialog._on_sim_enabled(0, 'enableDCS')
        assert len(cleanup["asked"]) == 1
        assert "DCS World is being turned off" in cleanup["asked"][0]

    def test_enabling_one_does_not(self, dialog, cleanup):
        dialog._on_sim_enabled(2, 'enableDCS')
        assert cleanup["asked"] == []

    def test_the_il2_switch_covers_both_titles(self, dialog, cleanup):
        """One switch, two installs, two configs - asking about only one
        would leave the other stranded with no sign of it."""
        dialog._on_sim_enabled(0, 'enableIL2')
        assert sorted(cleanup["keys"]) == ["IL2", "IL2_K"]
        assert len(cleanup["asked"]) == 2

    def test_agreeing_opts_the_sim_out_of_the_tap(self, dialog, cleanup):
        """Otherwise the removal at save, which keys off that switch, would
        skip the very sim they just agreed to clear."""
        dialog._on_sim_enabled(0, 'enableDCS')
        assert not dialog.tap_enable_boxes["DCS"].isChecked()

    def test_declining_leaves_the_opt_in_alone(self, dialog, cleanup):
        cleanup["answer"] = CLEANUP_LEAVE
        dialog._on_sim_enabled(0, 'enableDCS')
        assert dialog.tap_enable_boxes["DCS"].isChecked()

    def test_a_sim_with_nothing_installed_is_not_mentioned(self, dialog,
                                                           cleanup,
                                                           monkeypatch):
        import telemffb.tap_reconcile as ti
        from telemffb.tap_reconcile import TapCleanup
        monkeypatch.setattr(ti, 'plan_tap_cleanup',
                            lambda status: TapCleanup(status=status))
        dialog._on_sim_enabled(0, 'enableDCS')
        assert cleanup["asked"] == []


class TestDismissingTheQuestion:
    """Closing the prompt is an answer about the switch, not about the
    files. Leaving the switch off after a dismissed question keeps half of
    what was asked without the user having agreed to any of it."""

    class Box(TestOptingASimBackOut.Box):
        pass

    @pytest.fixture
    def cancelled(self, dialog, monkeypatch):
        import telemffb.SystemSettingsDialog as module
        import telemffb.tap_reconcile as ti
        from telemffb.tap_install import SimStatus
        from telemffb.tap_reconcile import TapCleanup

        plan = TapCleanup(status=SimStatus(sim=SIMS_BY_KEY["DCS"], root="r",
                                           provenance="t"),
                          delete_config=[r"C:\x"], remove_wrapper=[r"C:\x"])
        state = {"applied": []}
        monkeypatch.setattr(module.SystemSettingsDialog, '_tap_status',
                            lambda self, key: plan.status)
        monkeypatch.setattr(module.SystemSettingsDialog,
                            'refresh_tap_panels', lambda self: None)
        monkeypatch.setattr(ti, 'plan_tap_cleanup', lambda s: plan)
        monkeypatch.setattr(ti, 'apply_tap_cleanup',
                            lambda plans: state["applied"].append(plans) or [])
        monkeypatch.setattr(module.SystemSettingsDialog, '_ask_with_preview',
                            lambda self, message, plan: CLEANUP_CANCELLED)
        dialog.tap_enable_boxes = {"DCS": self.Box(False)}
        dialog._sim_enable_boxes = {"enableDCS": self.Box(False)}
        return state

    def test_cancelling_puts_the_tap_switch_back(self, dialog, cancelled):
        dialog._offer_tap_cleanup("DCS")
        assert dialog.tap_enable_boxes["DCS"].isChecked()

    def test_cancelling_puts_the_sim_switch_back(self, dialog, cancelled):
        dialog._offer_tap_cleanup("DCS", sim_switched_off=True)
        assert dialog._sim_enable_boxes["enableDCS"].isChecked()

    def test_nothing_is_queued_for_removal(self, dialog, cancelled):
        dialog._offer_tap_cleanup("DCS")
        dialog._apply_tap_cleanup()
        assert cancelled["applied"] == []

    def test_it_asks_again_next_time_the_switch_moves(self, dialog, cancelled):
        """A dismissed question was never answered, so flipping the switch
        again is a fresh one."""
        dialog._offer_tap_cleanup("DCS")
        assert "DCS" not in dialog._tap_cleanup_asked

    def test_declining_is_not_cancelling(self, dialog, cancelled,
                                         monkeypatch):
        """"Leave it" keeps the switch where the user just put it - they
        meant to turn it off, they just kept the files."""
        import telemffb.SystemSettingsDialog as module
        monkeypatch.setattr(module.SystemSettingsDialog, '_ask_with_preview',
                            lambda self, message, plan: CLEANUP_LEAVE)
        dialog._offer_tap_cleanup("DCS")
        assert not dialog.tap_enable_boxes["DCS"].isChecked()


class TestCancellingLeavesAConsistentPage:
    """Restoring a switch with signals blocked means nothing downstream
    ran - so a ticked box could sit above an empty space, and the page no
    longer described itself."""

    class Panel:
        def __init__(self):
            self.visible = False

        def set_status(self, status):
            pass

        def setVisible(self, value):
            self.visible = value

    def test_the_panel_comes_back_with_the_switch(self, dialog, monkeypatch):
        import telemffb.SystemSettingsDialog as module
        monkeypatch.setattr(module.SystemSettingsDialog, '_tap_status',
                            lambda self, key: None)
        panel = self.Panel()
        dialog.tap_panels = {"DCS": panel}
        dialog.tap_enable_boxes = {"DCS": TestOptingASimBackOut.Box(True)}
        dialog.refresh_tap_panels()
        assert panel.visible

    def test_and_stays_hidden_when_the_switch_is_off(self, dialog,
                                                     monkeypatch):
        import telemffb.SystemSettingsDialog as module
        monkeypatch.setattr(module.SystemSettingsDialog, '_tap_status',
                            lambda self, key: None)
        panel = self.Panel()
        panel.visible = True
        dialog.tap_panels = {"DCS": panel}
        dialog.tap_enable_boxes = {"DCS": TestOptingASimBackOut.Box(False)}
        dialog.refresh_tap_panels()
        assert not panel.visible


class TestHowTheQuestionReads:
    """One change, said once; the sims listed by name; devices named, not
    numbered.  DCS holds two configs that agree, and listing it per file
    read as a duplicate."""

    def item(self, directory, replacement="045E:001B=tap    ; SideWinder (joystick)",
             sim="DCS"):
        status = SimStatus(sim=SIMS_BY_KEY[sim], root=r"C:\DCS", provenance="t")
        return ReconcileItem(status=status, directory=directory,
                             config="[FFBDevices]\n",
                             obsolete=[Rule("FFFF:2054", "tap", 1,
                                            ids=(0xFFFF, 0x2054))],
                             replacement=replacement, role="joystick",
                             was_ident="Monster")

    def summary(self, *items):
        from telemffb.SystemSettingsDialog import SystemSettingsDialog
        return SystemSettingsDialog._reconcile_summary(list(items))

    def test_the_change_is_stated_once_and_the_sims_listed(self):
        text = self.summary(self.item(r"C:\DCS\bin"), self.item(r"C:\DCS\bin-mt"),
                            self.item(r"C:\IL2\bin\game", sim="IL2"))
        assert text.count("changed from") == 1
        assert text.count("DCS World") == 1
        assert "IL-2 Sturmovik Great Battles" in text

    def test_both_devices_are_named_not_just_numbered(self):
        text = self.summary(self.item(r"C:\DCS\bin"))
        assert "The joystick changed from Monster (FFFF:2054) to " \
               "SideWinder (045E:001B)." in text

    def test_a_cleared_slot_reads_as_cleared(self):
        text = self.summary(self.item(r"C:\DCS\bin", replacement=None))
        assert "The joystick slot was cleared; it held Monster (FFFF:2054)." in text
        assert "leave the new one alone" not in text


class TestADeviceThatCannotBeDrivenIsMentionedOnce:
    """One change raised two dialogs: the reconcile notice, then the gap
    notice about the same device in the same sims.  And the gap notice was
    remembered per dialog, so a second DirectInput device later was never
    mentioned at all."""

    SIDEWINDER = TapDevice("joystick", 0x045E, 0x001B, "SideWinder",
                           directinput=True)
    MOZA = TapDevice("joystick", 0x346E, 0x0005, "Moza", directinput=True)
    #: the device the spy's canned reconcile item replaces with
    WARTHOG = TapDevice("joystick", 0x044F, 0xB10A, "Warthog", directinput=True)

    def gap_for(self, monkeypatch, device):
        from telemffb import tap_reconcile
        status = SimStatus(sim=SIMS_BY_KEY["DCS"], root="r", provenance="t")
        gap = TapGap(status=status, device=device, directory=r"C:\DCS\bin",
                     config="[FFBDevices]\n")
        monkeypatch.setattr(tap_reconcile, "missing_tap_rules",
                            lambda *a, **k: [gap])

    def test_the_same_device_is_not_mentioned_twice(self, dialog, spy,
                                                    monkeypatch):
        self.gap_for(monkeypatch, self.SIDEWINDER)
        dialog._raise_tap_gaps()
        dialog._raise_tap_gaps()
        assert len(spy["asked"]) == 1

    def test_a_different_device_is_a_new_notice(self, dialog, spy,
                                                monkeypatch):
        self.gap_for(monkeypatch, self.SIDEWINDER)
        dialog._raise_tap_gaps()
        self.gap_for(monkeypatch, self.MOZA)
        dialog._raise_tap_gaps()
        assert len(spy["asked"]) == 2

    def test_a_gap_the_staged_reconcile_will_close_is_not_mentioned(
            self, dialog, spy, monkeypatch):
        """The staged reconcile already gives the new device its rule there;
        saying so again in other words is the second dialog nobody wanted."""
        from telemffb.tap_reconcile import device_changes
        dialog._tap_baseline = {"devpath_joystick": OLD_PATH,
                                "devids_joystick": "FFFF:2054"}
        dialog._pending_devpaths = {"devpath_joystick": NEW_PATH,
                                    "devids_joystick": "044F:B10A"}
        changes = device_changes(dialog._tap_baseline, dialog.tap_settings_view())
        dialog._tap_reconcile_seen = frozenset({dialog._change_signature(changes)})
        self.gap_for(monkeypatch, self.WARTHOG)        # DCS, 044F:B10A
        dialog._raise_tap_gaps()
        assert spy["asked"] == []
