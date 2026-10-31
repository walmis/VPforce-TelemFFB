"""An open System Settings dialog re-lists the devices when the USB
devices present change: a device that is plugged in replaces its 'not
connected' entry, a listing that has not changed rebuilds nothing, an
unchanged device set lists nothing, and an unsaved pick survives a
refresh."""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from telemffb.hw.ffb_dinput import DIDeviceInfo
from tests.test_settings_device_repopulate import _FakeDevice, _Settings, _make_dialog

pytestmark = [pytest.mark.unit]


class _ScriptedWatch:
    """A USB watch that answers ``changed()`` from a script; empty or
    exhausted, it reports a change every time."""
    answers = ()

    def __init__(self):
        self._answers = list(self.answers)

    def prime(self):
        pass

    def changed(self):
        return self._answers.pop(0) if self._answers else True


def _enumerate(monkeypatch, devices):
    from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
    monkeypatch.setattr(SystemSettingsDialog, 'DEVICE_WATCH_THREADED', False)
    monkeypatch.setattr(SystemSettingsDialog, 'USB_WATCH', _ScriptedWatch)
    monkeypatch.setattr(
        'telemffb.ui.dialogs.SystemSettingsDialog.FFBRhino.enumerate',
        staticmethod(lambda *a, **k: list(devices)), raising=False)


def _settings(**values):
    settings = _Settings({
        'devpath_joystick': '', 'devpath_pedals': '', 'devpath_collective': '',
        'devpath_trimwheel': '', 'enableDirectInput': False,
    })
    settings.update(values)
    return settings


def _row_of(combo, device):
    model = combo.model()
    for row in range(model.rowCount()):
        if model.device_at(row) is device:
            return row
    raise AssertionError("device not listed")


def test_a_device_that_appears_while_open_replaces_its_absent_entry(monkeypatch):
    _enumerate(monkeypatch, [])
    app, dlg = _make_dialog(monkeypatch, _settings(
        devpath_joystick='absent-stick', devident_joystick='Stick A',
        devids_joystick='FFFF:2055'))
    try:
        assert dlg.cb_select_j.currentData().connected is False
        live = _FakeDevice(b'absent-stick')
        _enumerate(monkeypatch, [live])
        dlg.show()
        app.processEvents()
        dlg._watch_devices()
        assert dlg.cb_select_j.currentData() is live
        assert dlg.cb_select_j.property('absent') is False
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def test_an_unchanged_listing_rebuilds_nothing(monkeypatch):
    stick = _FakeDevice(b'stick')
    _enumerate(monkeypatch, [stick])
    app, dlg = _make_dialog(monkeypatch, _settings(devpath_joystick='stick'))
    try:
        calls = []
        monkeypatch.setattr(dlg, '_rebuild_selectors', lambda *a, **k: calls.append(1))
        dlg.show()
        app.processEvents()
        dlg._watch_devices()
        dlg._watch_devices()
        assert calls == []
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def test_an_unsaved_pick_survives_a_refresh(monkeypatch):
    stick = _FakeDevice(b'stick')
    pedals = _FakeDevice(b'pedals')
    pedals.product_id = 0x2052
    _enumerate(monkeypatch, [stick, pedals])
    app, dlg = _make_dialog(monkeypatch, _settings(devpath_joystick='stick'))
    try:
        dlg.cb_select_p.setCurrentIndex(_row_of(dlg.cb_select_p, pedals))
        assert dlg._pending_devpaths['devpath_pedals'] == 'pedals'
        collective = _FakeDevice(b'collective')
        collective.product_id = 0x2051
        _enumerate(monkeypatch, [stick, pedals, collective])
        dlg.show()
        app.processEvents()
        dlg._watch_devices()
        assert dlg.cb_select_p.currentData() is pedals
        assert dlg._pending_devpaths['devpath_pedals'] == 'pedals'
        assert _row_of(dlg.cb_select_c, collective) > 0
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def test_the_watch_runs_only_while_the_dialog_is_shown(monkeypatch):
    _enumerate(monkeypatch, [])
    app, dlg = _make_dialog(monkeypatch, _settings())
    try:
        assert not dlg._device_watch.isActive()
        dlg.show()
        app.processEvents()
        assert dlg._device_watch.isActive()
        dlg.hide()
        app.processEvents()
        assert not dlg._device_watch.isActive()
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def test_the_primary_marker_survives_a_refresh(monkeypatch):
    primary = _FakeDevice(b'stick-a')
    secondary = _FakeDevice(b'stick-b')
    secondary.serial_number = 'B0002'
    _enumerate(monkeypatch, [primary, secondary])
    app, dlg = _make_dialog(monkeypatch, _settings(
        devpath_joystick='stick-a', devpath_joystick_2='stick-b'))
    try:
        card = dlg.device_cards.joystick_card
        dlg._on_activate_joystick_row(2)
        assert dlg._pending_devpaths['devpath_joystick'] == 'stick-b'
        _enumerate(monkeypatch, [primary, secondary, _FakeDevice(b'pedals')])
        dlg.show()
        app.processEvents()
        dlg._watch_devices()
        # rows are re-laid out in storage order: the pending primary is row one
        assert card.active_slot == 1
        assert card.primary_row.selector.currentData() is secondary
        assert card.alt_rows[0].selector.currentData() is primary
        assert dlg._pending_devpaths['devpath_joystick'] == 'stick-b'
        assert dlg._pending_devpaths['devpath_joystick_2'] == 'stick-a'
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def _di_stick():
    return DIDeviceInfo(guid='guid-moza', product_string='MOZA AB6 FFB Base',
                        vendor_id=0x346E, product_id=0x0005, path=b'dinput:guid-moza')


def test_a_directinput_alternate_that_appears_is_listed_on_its_refresh(monkeypatch):
    primary = _FakeDevice(b'stick-a')
    _enumerate(monkeypatch, [primary])
    app, dlg = _make_dialog(monkeypatch, _settings(
        devpath_joystick='stick-a', devpath_joystick_2='dinput:guid-moza',
        devident_joystick_2='MOZA AB6 FFB Base', devids_joystick_2='346E:0005',
        enableDirectInput=True))
    try:
        row = dlg.device_cards.joystick_card.alt_rows[0]
        assert row.selector.currentData().connected is False
        moza = _di_stick()
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                            staticmethod(lambda enabled=None, settings=None: [moza]))
        dlg.show()
        app.processEvents()
        dlg._refresh_dinput_listing()
        assert row.selector.currentData() is moza
        assert row.selector.property('absent') is False
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def test_listings_run_only_when_the_usb_devices_change(monkeypatch):
    """Three ticks, one change: one VPforce listing and one DirectInput
    listing, on that tick."""
    _enumerate(monkeypatch, [])
    app, dlg = _make_dialog(monkeypatch, _settings(enableDirectInput=True))
    try:
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        dlg._usb_watch = _ScriptedWatch()
        dlg._usb_watch._answers = [False, True, False]
        listed, refreshed = [], []
        monkeypatch.setattr(SystemSettingsDialog, '_list_vpforce_devices',
                            staticmethod(lambda: listed.append(1) or []))
        monkeypatch.setattr(dlg, '_refresh_dinput_listing', lambda: refreshed.append(1))
        dlg.show()
        app.processEvents()
        for _ in range(3):
            dlg._watch_devices()
        assert (len(listed), len(refreshed)) == (1, 1)
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def test_an_unchanged_directinput_listing_rebuilds_nothing(monkeypatch):
    _enumerate(monkeypatch, [])
    app, dlg = _make_dialog(monkeypatch, _settings(enableDirectInput=True))
    try:
        calls = []
        monkeypatch.setattr(dlg, '_rebuild_selectors', lambda *a, **k: calls.append(1))
        dlg._dinput_listed([])          # the listing the dialog was built from
        assert calls == []
        dlg._dinput_listed([_di_stick()])
        assert calls == [1]
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def test_the_threaded_listing_rebuilds_on_the_main_thread(monkeypatch):
    """The worker's listing reaches the selectors through the main thread."""
    import threading
    _enumerate(monkeypatch, [])
    app, dlg = _make_dialog(monkeypatch, _settings(
        devpath_joystick='absent-stick', devident_joystick='Stick A',
        devids_joystick='FFFF:2055'))
    try:
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        monkeypatch.setattr(SystemSettingsDialog, 'DEVICE_WATCH_THREADED', True)
        live = _FakeDevice(b'absent-stick')
        seen = {}

        def enumerate_on_worker(*a, **k):
            seen['thread'] = threading.current_thread().name
            return [live]
        monkeypatch.setattr('telemffb.ui.dialogs.SystemSettingsDialog.FFBRhino.enumerate',
                            staticmethod(enumerate_on_worker), raising=False)
        delivered = []
        monkeypatch.setattr('telemffb.ui.dialogs.SystemSettingsDialog.schedule_on_main_thread',
                            lambda fn: delivered.append(fn))
        dlg.show()
        app.processEvents()
        dlg._watch_devices()
        dlg._vpforce_scan.join(timeout=5)
        assert seen['thread'] != threading.main_thread().name
        # the VPforce rebuild and the DirectInput refresh, in that order
        assert len(delivered) == 2
        for fn in delivered:
            fn()                            # what the main thread would run
        assert dlg.cb_select_j.currentData() is live
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()
