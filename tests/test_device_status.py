"""Device status UX tests (device-state-handling plan, Task 5).

The device panel used to be binary green/grey and showed *green* even
when the configured board never opened (the zombie state) - the user in
the 2026-08-29 log discovered the missing board only by accident in the
configurator.  These tests cover:

- the pure state derivation (not found / reconnecting / active);
- the panel's status->color mapping and tooltips for the three states;
- the static wiring of the derived state into MainWindow.

The PR74 switch-device prompt was dropped in the dinput merge: dinput's
per-slot device selection plus main.switch_to_device covers that flow.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('telemffb.hw.hid', MagicMock())

import telemffb.hw.ffb_rhino as ffb_rhino_module
from telemffb.hw.ffb_rhino import HapticEffect

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from telemffb.DevicePanel import DeviceIconPanel, device_status_state


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication(sys.argv)
    yield a


# ---------------------------------------------------------------------------
# pure state derivation
# ---------------------------------------------------------------------------

class TestStatusState:

    def test_none_is_not_found(self, monkeypatch):
        monkeypatch.setattr(HapticEffect, "device", None)
        assert device_status_state() == "NOT_FOUND"

    def test_dead_handle_is_reconnecting(self, monkeypatch):
        monkeypatch.setattr(HapticEffect, "device",
                            SimpleNamespace(connected=False))
        assert device_status_state() == "RECONNECTING"

    def test_live_handle_is_active(self, monkeypatch):
        monkeypatch.setattr(HapticEffect, "device",
                            SimpleNamespace(connected=True))
        assert device_status_state() == "ACTIVE"


# ---------------------------------------------------------------------------
# panel mapping (stub icon - no pixmaps/resources needed)
# ---------------------------------------------------------------------------

class TestPanelMapping:

    def _panel_with_stub(self, app, device="joystick"):
        panel = DeviceIconPanel()
        stub = MagicMock()
        panel.icons[device.lower()] = stub
        return panel, stub

    def test_active_maps_to_ok_and_tooltip(self, app):
        panel, stub = self._panel_with_stub(app)
        panel.set_device_status("joystick", "ACTIVE")
        stub.set_status_color.assert_called_once_with("ok")
        stub.setToolTip.assert_called_once_with("Device connected")

    def test_reconnecting_maps_to_warning_and_tooltip(self, app):
        panel, stub = self._panel_with_stub(app)
        panel.set_device_status("joystick", "RECONNECTING")
        stub.set_status_color.assert_called_once_with("warning")
        assert "reconnecting" in stub.setToolTip.call_args[0][0]

    def test_not_found_maps_to_error_and_tooltip(self, app):
        panel, stub = self._panel_with_stub(app)
        panel.set_device_status("joystick", "NOT_FOUND")
        stub.set_status_color.assert_called_once_with("error")
        assert "not found" in stub.setToolTip.call_args[0][0]

    def test_legacy_statuses_still_work(self, app):
        panel, stub = self._panel_with_stub(app)
        panel.set_device_status("joystick", "DISCONNECTED")
        stub.set_status_color.assert_called_with("warning")
        stub.setToolTip.assert_called_with("Device disconnected")
        panel.set_device_status("joystick", "TIMEOUT")
        stub.set_status_color.assert_called_with("error")


# ---------------------------------------------------------------------------
# static wiring (runs on every platform - MainWindow/main are Windows-only
# to import, but the shipped sources must still contain the right calls)
# ---------------------------------------------------------------------------

class TestMainWindowWiring:

    @staticmethod
    def _source():
        import pathlib
        return (pathlib.Path(__file__).parent.parent / "telemffb" / "MainWindow.py").read_text(encoding="utf-8")

    def test_constructor_uses_derived_state(self):
        src = self._source()
        assert src.count('set_device_status(G.device_type, device_status_state())') == 3  # ctor, slot, device-list refresh
        assert 'set_device_status(G.device_type, "ok")' not in src

    def test_slot_uses_derived_state(self):
        src = self._source()
        i = src.index("def update_device_status")
        body = src[i:i + 600]
        assert "device_status_state()" in body
        assert '"DISCONNECTED"' not in body
