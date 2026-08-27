"""Repopulating the device selectors must not disturb the launch options.

populateUSBSelectors() was written as a one-shot at dialog construction, but
the "Enable DirectInput Devices" toggle re-runs it so [DI] entries appear or
disappear immediately.  Swapping each combo's model emits currentIndexChanged
with nothing selected yet, which the change handler reads as "device cleared"
- switching off that role's auto-launch, start-minimised and headless boxes
and queuing its devpath to be cleared on save.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G

pytestmark = [pytest.mark.unit]


class _Settings(dict):
    """A settings stand-in that answers the instance-scoped get() the
    per-device panels use: get(key, default, instance=role)."""

    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


def _make_dialog(monkeypatch, settings):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(G, 'system_settings', settings, raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.SystemSettingsDialog import SystemSettingsDialog
    # never the real hardware layer: DirectInput enumeration loads the
    # bridge DLL and walks the machine's actual devices, which blocks
    # while a running TelemFFB holds one exclusively
    monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                        staticmethod(lambda enabled=None: []))
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                        lambda *a, **k: (True, ''))
    # bridge_status loads the real bridge DLL - never from a test
    from telemffb.hw.ffb_dinput import BridgeStatus
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_status',
                        lambda *a, **k: BridgeStatus(
                            installed=True, version='1.0.0'))
    return app, SystemSettingsDialog()


class _FakeDevice:
    """Enough of DeviceInfo for the selector model and the restore path."""

    def __init__(self, path=b'fake-joystick-path'):
        self.path = path
        self.product_id = 0x2055
        self.vendor_id = 0xFFFF
        self.product_string = 'Fake Rhino FFB Joystick'
        self.manufacturer_string = 'VPforce'
        self.serial_number = 'FAKE0001'

    def vidpid(self):
        return f"{self.vendor_id:04X}:{self.product_id:04X}"


def test_dialog_opens_with_directinput_already_enabled(monkeypatch):
    """Restoring saved settings ticks the box, firing the repopulate handler.

    Connected too early in __init__ that runs before the combos have their
    companion PID fields attached, and the dialog dies on the way up with
    AttributeError: 'QComboBox' object has no attribute '_tb_box'.

    A device the saved devpath matches is required to reach that code: with
    nothing selected the restore loop skips the PID sync entirely.
    """
    device = _FakeDevice()
    monkeypatch.setattr('telemffb.SystemSettingsDialog.FFBRhino.enumerate',
                        staticmethod(lambda *a, **k: [device]), raising=False)
    app, dlg = _make_dialog(monkeypatch, _Settings({
        'devpath_joystick': device.path.decode(), 'devpath_pedals': '',
        'devpath_collective': '', 'devpath_trimwheel': '',
        'enableDirectInput': True,
    }))
    # the crash happened during construction; reaching here at all is the test
    assert dlg.cb_enable_dinput.isChecked()
    dlg.deleteLater()
    app.processEvents()


@pytest.fixture
def dialog(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    # a settings stand-in, so the real configuration is neither read nor written
    monkeypatch.setattr(G, 'system_settings', _Settings({
        'devpath_joystick': '', 'devpath_pedals': '', 'devpath_collective': '',
        'devpath_trimwheel': '', 'enableDirectInput': False,
    }), raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None)):
        monkeypatch.setattr(G, name, value, raising=False)

    from telemffb.SystemSettingsDialog import SystemSettingsDialog
    # stub the hardware layer for the same reason as _make_dialog above -
    # and FFBRhino.enumerate too, which otherwise lists the developer's
    # actual VPforce devices
    monkeypatch.setattr('telemffb.SystemSettingsDialog.FFBRhino.enumerate',
                        staticmethod(lambda *a, **k: []), raising=False)
    monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                        staticmethod(lambda enabled=None: []))
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                        lambda *a, **k: (True, ''))
    # bridge_status loads the real bridge DLL - never from a test
    from telemffb.hw.ffb_dinput import BridgeStatus
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_status',
                        lambda *a, **k: BridgeStatus(
                            installed=True, version='1.0.0'))
    dlg = SystemSettingsDialog()
    yield dlg
    dlg.deleteLater()
    app.processEvents()


def _launch_options(dlg):
    return {
        'autolaunch_joystick': dlg.cb_al_enable_j.isChecked(),
        'autolaunch_pedals': dlg.cb_al_enable_p.isChecked(),
        'autolaunch_collective': dlg.cb_al_enable_c.isChecked(),
        'autolaunch_trimwheel': dlg.cb_al_enable_t.isChecked(),
        'start_min_joystick': dlg.cb_min_enable_j.isChecked(),
        'headless_joystick': dlg.cb_headless_j.isChecked(),
    }


def test_directinput_toggle_preserves_launch_options(dialog):
    for box in (dialog.cb_al_enable_j, dialog.cb_al_enable_p,
                dialog.cb_al_enable_c, dialog.cb_al_enable_t,
                dialog.cb_min_enable_j, dialog.cb_headless_j):
        box.setChecked(True)
    configured = _launch_options(dialog)

    dialog.cb_enable_dinput.setChecked(True)
    assert _launch_options(dialog) == configured, "enabling DirectInput cleared launch options"

    dialog.cb_enable_dinput.setChecked(False)
    assert _launch_options(dialog) == configured, "disabling DirectInput cleared launch options"


def test_repopulate_does_not_queue_cleared_devpaths(dialog):
    """The same emission also reaches persist_combobox_selection, which would
    queue every role's devpath to be blanked when the dialog is saved."""
    dialog._pending_devpaths.clear()
    dialog.populateUSBSelectors(dinput_enabled=True)
    assert not [k for k, v in dialog._pending_devpaths.items() if v == ''], \
        f"repopulate queued devpath clears: {dialog._pending_devpaths}"


def test_repopulate_connects_handlers_once(dialog):
    """Reconnecting on every repopulate would multiply the handler, so a
    single user change would run it repeatedly."""
    before = dialog.cb_select_j.receivers(dialog.cb_select_j.currentIndexChanged)
    dialog.populateUSBSelectors(dinput_enabled=True)
    dialog.populateUSBSelectors(dinput_enabled=False)
    assert dialog.cb_select_j.receivers(dialog.cb_select_j.currentIndexChanged) == before
