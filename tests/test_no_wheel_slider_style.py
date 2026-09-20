"""NoWheelNumberSlider.setHandleColor() is called every ~50ms telemetry tick
for each live-key slider (MainWindow.on_update_telemetry). paintEvent draws
the handle color from self.handle_color directly, so that call never needs
to rebuild and apply a stylesheet - it only has to store the new
color/text and repaint. Guards against that per-frame setStyleSheet
regressing back in.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from telemffb.ui.widgets.custom_widgets import NoWheelNumberSlider

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_set_handle_color_does_not_restyle(qapp, monkeypatch):
    slider = NoWheelNumberSlider()

    calls = []
    monkeypatch.setattr(
        NoWheelNumberSlider, "setStyleSheet",
        lambda self, css: calls.append(css),
    )

    slider.setHandleColor("#ff0000", "50%")
    assert slider.handle_color == "#ff0000"
    assert slider.value_text == "50%"

    slider.setHandleColor("#00ff00", "75%")
    assert slider.handle_color == "#00ff00"
    assert slider.value_text == "75%"

    assert calls == []


def test_set_handle_color_skips_update_when_unchanged(qapp, monkeypatch):
    slider = NoWheelNumberSlider()
    slider.setHandleColor("#ff0000", "50%")

    updates = []
    monkeypatch.setattr(NoWheelNumberSlider, "update", lambda self: updates.append(True))

    # Same color and text as already stored: nothing should repaint.
    slider.setHandleColor("#ff0000", "50%")
    assert updates == []

    # A real change still repaints.
    slider.setHandleColor("#ff0000", "60%")
    assert updates == [True]
