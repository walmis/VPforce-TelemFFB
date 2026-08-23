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

    def test_a_changed_device_is_named(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.set_device("joystick", 2)              # the Warthog
        assert world.save()
        assert restart_notices(world) == [
            "The selected devices changed. Restart TelemFFB for this to take effect."]

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
        world.set_device("joystick", 2)
        assert world.save()
        assert world.save()
        assert len(restart_notices(world)) == 1
