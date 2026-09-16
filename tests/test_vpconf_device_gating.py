"""Configurator profile settings only apply to VPforce hardware.

A generic DirectInput device has no Configurator gains, so those settings are
greyed - per device, since one dialog now configures them all - and skipped
by validation.  They are never cleared: a device can be swapped back to
VPforce hardware, and the stored values have to survive the trip.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

import telemffb.globals as G
from telemffb.custom_widgets import FFBDeviceListModel




pytestmark = [pytest.mark.unit]

class FakeSettings(dict):
    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


class FakeDevice:
    """Enough of DeviceInfo for the selector model."""

    def __init__(self, product_id, ident, path):
        self.product_id = product_id
        self.ident = ident
        self.vendor_id = 0xFFFF
        self.serial_number = "SERIAL"
        self.path = path


VPFORCE = FakeDevice(0x2054, "Monster", b"usb-monster")
DINPUT = FakeDevice(0x0483, "[DI] Some Stick", b"dinput:{GUID-1234}")


@pytest.fixture
def dialog(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(G, 'system_settings', FakeSettings({
        'devpath_joystick': 'usb-monster', 'devpath_pedals': 'usb-pedals',
        'devpath_collective': '', 'devpath_trimwheel': '',
        'joystick/enableVPConfStartup': True,
        'joystick/pathVPConfStartup': r'C:\profiles\stick.vpconf',
    }), raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2054'), ('device_capabilities', None),
                        ('device_di_guid', None), ('is_exe', False)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.SystemSettingsDialog import SystemSettingsDialog
    dlg = SystemSettingsDialog()
    yield dlg
    dlg.deleteLater()
    app.processEvents()


def _assign(dialog, combo, device):
    combo.setModel(FFBDeviceListModel([device]))
    combo.setCurrentIndex(1)                  # row 0 is "(None)"
    dialog.toggle_device_launch_widgets()     # the sync that regates


class TestConfiguratorGating:
    def test_a_directinput_device_cannot_use_configurator_profiles(self, dialog):
        _assign(dialog, dialog.cb_select_j, DINPUT)
        panel = dialog.instance_panels[('startup', 'joystick')]
        assert panel.vpforce_blocked
        assert not panel.widgets['enableVPConfStartup'].isEnabled()
        assert not panel.widgets['pathVPConfStartup'].isEnabled()
        assert not panel.widgets['enableResetGainsExit'].isEnabled()

    def test_vpforce_hardware_keeps_them(self, dialog):
        _assign(dialog, dialog.cb_select_j, VPFORCE)
        panel = dialog.instance_panels[('startup', 'joystick')]
        assert not panel.vpforce_blocked
        assert panel.widgets['enableVPConfStartup'].isEnabled()

    def test_the_settings_survive_the_switch(self, dialog):
        """A device can be swapped back, so blocking must not clear."""
        panel = dialog.instance_panels[('startup', 'joystick')]
        panel.widgets['enableVPConfStartup'].setChecked(True)
        panel.widgets['pathVPConfStartup'].setPath(r'C:\profiles\stick.vpconf')

        _assign(dialog, dialog.cb_select_j, DINPUT)
        assert panel.widgets['enableVPConfStartup'].isChecked()
        assert panel.widgets['pathVPConfStartup'].path() == r'C:\profiles\stick.vpconf'

        _assign(dialog, dialog.cb_select_j, VPFORCE)
        assert panel.widgets['enableVPConfStartup'].isEnabled()
        assert panel.widgets['pathVPConfStartup'].isEnabled(), \
            "the dependent path field should follow its toggle again"

    def test_only_the_affected_device_is_gated(self, dialog):
        """The whole point of doing this per role rather than per instance."""
        _assign(dialog, dialog.cb_select_j, DINPUT)
        _assign(dialog, dialog.cb_select_p, VPFORCE)
        assert dialog.instance_panels[('startup', 'joystick')].vpforce_blocked
        assert not dialog.instance_panels[('startup', 'pedals')].vpforce_blocked

    def test_non_configurator_settings_are_untouched(self, dialog):
        _assign(dialog, dialog.cb_select_j, DINPUT)
        panel = dialog.instance_panels[('startup', 'joystick')]
        assert panel.widgets['saveWindow'].isEnabled()
        assert panel.widgets['saveLastTab'].isEnabled()

    def test_validation_skips_a_blocked_device(self, dialog, monkeypatch):
        """The stored path is inert on a DI device; refusing to save over it
        would strand the user."""
        panel = dialog.instance_panels[('startup', 'joystick')]
        panel.widgets['enableVPConfStartup'].setChecked(True)
        panel.widgets['pathVPConfStartup'].setPath(r'C:\nope\missing.vpconf')
        _assign(dialog, dialog.cb_select_j, DINPUT)

        warned = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: warned.append(a)))
        assert dialog.validate_instance_settings()
        assert not warned
