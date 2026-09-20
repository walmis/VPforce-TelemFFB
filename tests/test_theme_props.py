"""set_state_prop() is the shared "toggle a QSS-selected dynamic property"
helper (telemffb.ui.theme.props) that replaced several per-widget
setStyleSheet() rebuild (MiniDeviceChip's clickable hover tint), plus
NoWheelSlider.setActive, which needs no stylesheet at all. These guard the two things that make it worth
having: it actually flips the Qt property QSS selects on, it never calls
setStyleSheet() itself, and it skips the re-polish when nothing changed.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from telemffb.ui.theme.props import set_state_prop
from telemffb.ui.theme.tokens import PURPLE, ACTIVE_GREEN
from telemffb.ui.widgets.custom_widgets import NoWheelSlider

pytestmark = [pytest.mark.unit]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_set_state_prop_flips_the_property(app):
    widget = QWidget()
    assert widget.property("active") in (None, False)
    set_state_prop(widget, "active", True)
    assert widget.property("active") is True
    set_state_prop(widget, "active", False)
    assert widget.property("active") is False


def test_set_state_prop_never_calls_setStyleSheet(app, monkeypatch):
    widget = QWidget()
    calls = []
    monkeypatch.setattr(QWidget, "setStyleSheet", lambda self, sheet: calls.append(sheet))
    set_state_prop(widget, "active", True)
    set_state_prop(widget, "active", False)
    assert calls == []


def test_set_state_prop_is_a_noop_when_unchanged(app, monkeypatch):
    widget = QWidget()
    set_state_prop(widget, "active", True)
    polished = []
    monkeypatch.setattr(type(widget.style()), "polish", lambda self, w: polished.append(w))
    set_state_prop(widget, "active", True)  # same value again
    assert polished == []


class TestNoWheelSliderSetActive:
    """NoWheelSlider.setActive() is the two-state (idle/live-effect) toggle
    SettingsLayout's preview-lock and active-setting highlighting drive.
    The slider paints its handle itself, so this is color + repaint only."""

    def test_toggling_active_sets_color_without_a_stylesheet(self, app, monkeypatch):
        slider = NoWheelSlider()
        calls = []
        monkeypatch.setattr(NoWheelSlider, "setStyleSheet", lambda self, sheet: calls.append(sheet))

        slider.setActive(True)
        assert slider.handle_color == ACTIVE_GREEN

        slider.setActive(False)
        assert slider.handle_color == PURPLE

        assert calls == [], "setActive should not rebuild a stylesheet"
