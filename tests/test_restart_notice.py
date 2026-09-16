"""The "Restart Required" notice: only for what truly needs a restart, only
after the save went through, and saying what changed.

Drives the real dialog through the workflow harness's World, which answers
every prompt and records it.
"""
import os
import random

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_tap_workflows import World

pytestmark = [pytest.mark.unit]


@pytest.fixture
def app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def restart_notices(world):
    return [text for title, text in world.policy.asked if title == "Restart Required"]


#: A settings base complete enough that nothing looks changed at open: the
#: stored pids are what the selectors will report for the configured devices.
SETTLED = {'pidJoystick': '2054', 'pidPedals': '2052', 'pidCollective': '',
           'pidTrimWheel': '', 'masterInstance': 1, 'themeId': 2}


class TestWhenItFires:
    def test_saving_with_nothing_restart_worthy_changed_says_nothing(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.tap("DCS", True)                       # not a restart matter
        assert world.save()
        assert restart_notices(world) == []

    def test_a_changed_device_switches_live_instead_of_asking_for_restart(
            self, app, tmp_path, monkeypatch):
        """Device selections apply immediately at save (the master
        re-acquires its own device); they used to be a restart matter."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.set_device("joystick", 2)              # the Warthog
        assert world.save()
        assert restart_notices(world) == []
        assert world.device_switches == 1

    def test_the_master_device_counts(self, app, tmp_path, monkeypatch):
        """The old notice named the master device in its text and never
        compared it."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.dialog.master_button_group.button(2).click()   # pedals as master
        assert world.save()
        assert any("master device changed" in t for t in restart_notices(world))

    def test_a_refused_save_says_nothing(self, app, tmp_path, monkeypatch):
        """It used to fire before validation - a restart for changes that
        were never written."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.set_device("joystick", 0)              # no master device: refused
        world.dialog.save_settings()
        assert restart_notices(world) == []

    def test_saving_twice_does_not_say_it_twice(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.dialog.master_button_group.button(2).click()   # pedals as master
        assert world.save()
        assert world.save()
        assert len(restart_notices(world)) == 1

    def test_saving_twice_does_not_switch_twice(self, app, tmp_path, monkeypatch):
        """The live switch compares stored state before and after the save -
        an unchanged second save must not re-acquire the device."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.set_device("joystick", 2)
        assert world.save()
        assert world.save()
        assert world.device_switches == 1

    def test_change_then_change_back_never_switches(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        original = world.dialog.cb_select_j.currentIndex()
        world.set_device("joystick", 2)
        world.set_device("joystick", original)
        assert world.save()
        assert world.device_switches == 0

    def test_unchanged_save_retries_while_deviceless(self, app, tmp_path, monkeypatch):
        """The recovery path for a failed open: nothing watches for a
        replug (a failed open left no device object), so saving again -
        with the selection unchanged - must retry the acquire."""
        import telemffb.globals as G
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        monkeypatch.setattr(G, 'device_connection_status', False, raising=False)
        assert world.save()                      # nothing changed in the dialog
        assert world.device_switches == 1
        assert restart_notices(world) == []

    def test_unchanged_save_while_connected_stays_quiet(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        assert world.save()
        assert world.device_switches == 0


class TestMasterChangeKeepsLaunchFlags:
    def test_exploring_the_master_choice_and_back_loses_nothing(
            self, app, tmp_path, monkeypatch):
        """Designating a role master used to CLEAR its launch checkboxes -
        an exploratory master change and back silently wiped that role's
        startup configuration.  Launch ignores the master's own flags, so
        the values now survive untouched (hidden while master)."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        dlg = world.dialog
        dlg.cb_al_enable.setChecked(True)
        dlg.cb_al_enable_p.setChecked(True)
        dlg.cb_headless_p.setChecked(True)
        dlg.master_button_group.button(2).click()   # pedals as master...
        dlg.master_button_group.button(1).click()   # ...and back
        assert dlg.cb_al_enable_p.isChecked()
        assert dlg.cb_headless_p.isChecked()


class TestAutoLaunchNeedsADevice:
    def test_a_switched_on_role_with_no_device_refuses_to_save(
            self, app, tmp_path, monkeypatch):
        """The switch works with nothing picked (it is the door into
        configuring an empty role), so the save is the guard: a role
        cannot leave the dialog auto-launching nothing."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.dialog.cb_al_enable.setChecked(True)
        world.dialog.cb_al_enable_c.setChecked(True)   # collective: no device
        assert not world.save()
        assert any('Collective' in text and 'auto-launch' in text
                   for _t, text in world.policy.warned)


class TestWindowMode:
    """The header's Window Mode tri-state: Headless / Minimized / Normal,
    stored as windowMode{Role} with the legacy booleans written in step
    (the launch code reads those)."""

    def _mode(self, world, suffix='p'):
        return world.dialog.device_cards.cards[
            {'j': 'joystick', 'p': 'pedals'}[suffix]].window_mode.currentText()

    def test_legacy_untouched_store_reads_headless(self, app, tmp_path, monkeypatch):
        """Legacy 'neither box checked' technically meant a normal window,
        but it is indistinguishable from the untouched default and almost
        nobody ran children windowed on purpose - it migrates to Headless
        (a deliberate Normal is a one-time re-pick)."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        assert self._mode(world) == 'Headless'
        assert world.dialog.cb_headless_p.isChecked()

    def test_legacy_minimized_survives(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0),
                      settings=dict(SETTLED, startMinPedals=True))
        assert self._mode(world) == 'Minimized'

    def test_normal_survives_a_reopen(self, app, tmp_path, monkeypatch):
        """As two False booleans, a deliberate Normal would be
        indistinguishable from the untouched default and flip back to
        Headless on every reopen - the tri-state key is what prevents
        that fight."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.cards['pedals']
        card.window_mode.setCurrentIndex(2)            # Normal
        assert world.save()
        assert world.settings.get('windowModePedals') == 'normal'
        assert world.settings.get('startHeadlessPedals') is False
        assert world.settings.get('startMinPedals') is False
        reopened = World(tmp_path / 'reopen', monkeypatch, random.Random(1),
                         settings=dict(world.settings))
        assert self._mode(reopened) == 'Normal'

    def test_the_combo_and_booleans_stay_in_step(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.cards['pedals']
        card.window_mode.setCurrentIndex(1)            # Minimized
        assert world.dialog.cb_min_enable_p.isChecked()
        assert not world.dialog.cb_headless_p.isChecked()
        assert world.save()
        assert world.settings.get('windowModePedals') == 'minimized'
        assert world.settings.get('startMinPedals') is True
