"""Device status UX tests (device-state-handling plan, Task 5).

The device panel used to be binary green/grey and showed *green* even
when the configured board never opened (the zombie state) - the user in
the 2026-08-29 log discovered the missing board only by accident in the
configurator.  These tests cover:

- the pure state derivation (not found / reconnecting / active);
- the panel's status->color mapping and tooltips for the three states;
- the switch-device prompt (Windows-only: main.py imports winreg),
  including that a user-No leaves every setting untouched and a Yes
  persists the new selection and opens the board.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault('telemffb.hw.hid', MagicMock())

import telemffb.hw.ffb_rhino as ffb_rhino_module
from telemffb.hw.ffb_rhino import HapticEffect

try:
    import main as main_module
except Exception:  # winreg is Windows-only
    main_module = None

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from telemffb.DevicePanel import DeviceIconPanel, device_status_state
from telemffb.utils import device_pid_key


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
        return (pathlib.Path(__file__).parent.parent / "telemffb" / "MainWindow.py").read_text()

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


# ---------------------------------------------------------------------------
# switch-device prompt (Windows-only: main.py imports winreg)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(main_module is None,
                    reason="main.py requires winreg (Windows-only)")
class TestSwitchPrompt:

    def _env(self, monkeypatch, boards):
        """boards: list of (pid, product_string, path) tuples."""
        from telemffb.hw.ffb_rhino import DeviceInfo
        infos = [
            DeviceInfo(
                interface_number=0, manufacturer_string="VPforce",
                path=path, product_id=pid, product_string=ps,
                release_number=516, serial_number=f"S{pid}",
                usage=4, usage_page=1, vendor_id=0xFFFF,
            )
            for pid, ps, path in boards
        ]
        monkeypatch.setattr(main_module.FFBRhino, "enumerate",
                            staticmethod(lambda pid=0: infos))
        monkeypatch.setattr(main_module.G, "device_usbpid", "2055",
                            raising=False)
        fake_he = MagicMock()
        fake_he.device = None
        monkeypatch.setattr(main_module, "HapticEffect", fake_he)
        monkeypatch.setattr(main_module.G, "system_settings",
                            MagicMock(), raising=False)
        monkeypatch.setattr(main_module.G, "args",
                            SimpleNamespace(reset=False), raising=False)
        monkeypatch.setattr(main_module.G, "main_window", MagicMock(),
                            raising=False)
        wired = []
        monkeypatch.setattr(main_module, "_wire_opened_device",
                            lambda dev: wired.append(dev))
        return fake_he, wired

    def test_no_alternate_when_only_configured_board_present(self, monkeypatch):
        self._env(monkeypatch, [(0x2055, "Rhino FFB Joystick", b"p1")])
        assert main_module._find_alternate_vpforce_board() is None

    def test_finds_differing_pid(self, monkeypatch):
        self._env(monkeypatch, [
            (0x2055, "Rhino FFB Joystick", b"p1"),
            (0x2054, "Rhino FFB RhinoMFG", b"p2"),
        ])
        found = main_module._find_alternate_vpforce_board()
        assert found is not None
        assert found.product_id == 0x2054

    def test_no_answer_leaves_settings_untouched(self, monkeypatch):
        fake_he, wired = self._env(monkeypatch,
                                   [(0x2054, "Rhino FFB RhinoMFG", b"p2")])
        with patch.object(main_module.QMessageBox, "question",
                          return_value=main_module.QMessageBox.StandardButton.No):
            assert main_module._offer_device_switch() is False
        assert main_module.G.system_settings.setValue.call_count == 0
        assert main_module.G.device_usbpid == "2055"
        assert fake_he.open.call_count == 0
        assert wired == []

    def test_yes_persists_selection_and_opens_board(self, monkeypatch):
        fake_he, wired = self._env(monkeypatch,
                                   [(0x2054, "Rhino FFB RhinoMFG", b"p2")])
        dev = MagicMock()
        fake_he.open.return_value = dev
        with patch.object(main_module.QMessageBox, "question",
                          return_value=main_module.QMessageBox.StandardButton.Yes):
            assert main_module._offer_device_switch() is True

        calls = {c.args[0]: c.args[1]
                 for c in main_module.G.system_settings.setValue.call_args_list}
        assert calls[device_pid_key("joystick")] == "2054"
        assert calls["devpath_joystick"] == "p2"
        assert main_module.G.device_usbpid == "2054"
        fake_he.open.assert_called_once_with(pid=0x2054)
        assert wired == [dev]
