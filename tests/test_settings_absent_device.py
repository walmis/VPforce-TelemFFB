"""A configured device that is not connected stays in the System Settings
selectors as a 'not connected' entry built from its stored identity, so the
slot reads as absent rather than cleared, a save keeps it, and a marker
swap on the joystick card carries it into the alternate slot intact."""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_settings_device_repopulate import _FakeDevice, _Settings, _make_dialog

pytestmark = [pytest.mark.unit]

ABSENT_PATH = 'absent-stick-path'


def _absent_settings(**extra):
    settings = _Settings({
        'devpath_joystick': ABSENT_PATH,
        'devident_joystick': 'Stick A',
        'devids_joystick': 'FFFF:2055',
        'devpath_pedals': '', 'devpath_collective': '', 'devpath_trimwheel': '',
        'enableDirectInput': False,
    })
    settings.update(extra)
    return settings


def _enumerate(monkeypatch, devices):
    monkeypatch.setattr(
        'telemffb.ui.dialogs.SystemSettingsDialog.FFBRhino.enumerate',
        staticmethod(lambda *a, **k: list(devices)), raising=False)


def test_unplugged_configured_device_is_listed_and_selected(monkeypatch):
    _enumerate(monkeypatch, [])
    app, dlg = _make_dialog(monkeypatch, _absent_settings())
    try:
        device = dlg.cb_select_j.currentData()
        assert device is not None
        assert device.connected is False
        assert device.path == ABSENT_PATH.encode()
        assert (device.vendor_id, device.product_id) == (0xFFFF, 0x2055)
        assert dlg.cb_select_j.property('absent') is True
        assert dlg.device_cards.joystick_card.primary_row.ids_label.text() == 'FFFF:2055'
    finally:
        dlg.deleteLater()
        app.processEvents()


def test_making_the_connected_alternate_primary_keeps_the_unplugged_device(monkeypatch):
    connected = _FakeDevice(b'fake-stick-b')
    _enumerate(monkeypatch, [connected])
    app, dlg = _make_dialog(monkeypatch, _absent_settings(
        devpath_joystick_2=connected.path.decode(),
        devident_joystick_2='Stick B', devids_joystick_2='FFFF:2055'))
    try:
        card = dlg.device_cards.joystick_card
        assert len(card.alt_rows) == 1
        assert card.alt_rows[0].selector.currentData() is connected

        dlg._on_activate_joystick_row(2)

        pending = dlg._pending_devpaths
        assert pending['devpath_joystick'] == connected.path.decode()
        assert pending['devpath_joystick_2'] == ABSENT_PATH
        assert pending['devident_joystick_2'] == 'Stick A'
        assert pending['devids_joystick_2'] == 'FFFF:2055'
    finally:
        dlg.deleteLater()
        app.processEvents()


def test_a_device_that_appears_replaces_its_placeholder(monkeypatch):
    _enumerate(monkeypatch, [])
    app, dlg = _make_dialog(monkeypatch, _absent_settings())
    try:
        assert dlg.cb_select_j.currentData().connected is False
        live = _FakeDevice(ABSENT_PATH.encode())
        _enumerate(monkeypatch, [live])
        dlg.populateUSBSelectors()
        assert dlg.cb_select_j.currentData() is live
        assert dlg.cb_select_j.property('absent') is False
        model = dlg.cb_select_j.model()
        listed = [model.device_at(i) for i in range(model.rowCount())]
        assert sum(1 for d in listed if d is not None) == 1
    finally:
        dlg.deleteLater()
        app.processEvents()
