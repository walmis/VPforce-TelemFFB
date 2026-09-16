"""The master dialog configures every device, not just its own.

Child instances exist to drive a device and process telemetry; configuring
them is the master's job. That means the System Settings dialog carries one
panel per configured device and writes each one's settings under its own
``{role}/`` keys - the behavior that lets the child settings pages go away.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G

pytestmark = [pytest.mark.unit]


class FakeSettings(dict):
    """Enough of SystemSettings for the dialog: role-scoped get, flat store."""

    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


def _settings(**extra):
    s = FakeSettings({
        'devpath_joystick': 'usb-path-joystick',
        'devpath_pedals': 'usb-path-pedals',
        'devpath_collective': '',
        'devpath_trimwheel': '',
    })
    s.update(extra)
    return s


@pytest.fixture
def dialog(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = _settings()
    monkeypatch.setattr(G, 'system_settings', settings, raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None), ('is_exe', False)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.SystemSettingsDialog import SystemSettingsDialog
    dlg = SystemSettingsDialog()
    yield dlg, settings
    dlg.deleteLater()
    app.processEvents()


class TestPanelsPerDevice:
    def test_a_tab_exists_for_each_configured_device(self, dialog):
        dlg, _ = dialog
        roles = [dlg.instance_tabs_system.tabText(i).lower()
                 for i in range(dlg.instance_tabs_system.count())]
        assert roles == ['joystick', 'pedals'], roles

    def test_unconfigured_devices_get_no_tab(self, dialog):
        dlg, _ = dialog
        assert ('system', 'collective') not in dlg.instance_panels

    def test_both_sections_have_panels(self, dialog):
        dlg, _ = dialog
        sections = {section for section, _ in dlg.instance_panels}
        assert sections == {'system', 'startup'}


class TestSavingEachDevice:
    def test_each_device_writes_its_own_keys(self, dialog):
        dlg, settings = dialog
        dlg.instance_panels[('system', 'joystick')].widgets['logLevel'].setCurrentText('DEBUG')
        dlg.instance_panels[('system', 'pedals')].widgets['logLevel'].setCurrentText('INFO')
        dlg.instance_panels[('system', 'pedals')].widgets['telemTimeout'].setText('500')

        for panel in dlg.instance_panels.values():
            panel.save(settings)

        assert settings['joystick/logLevel'] == 'DEBUG'
        assert settings['pedals/logLevel'] == 'INFO'
        assert settings['pedals/telemTimeout'] == '500'
        # the joystick's timeout is untouched by the pedals' edit
        assert settings['joystick/telemTimeout'] == '200'

    def test_stored_values_come_back_per_device(self, monkeypatch):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        settings = _settings(**{
            'joystick/logLevel': 'DEBUG',
            'pedals/logLevel': 'INFO',
            'pedals/telemTimeout': '750',
        })
        monkeypatch.setattr(G, 'system_settings', settings, raising=False)
        for name, value in (('device_type', 'joystick'), ('master_instance', True),
                            ('child_instance', False), ('launched_instances', []),
                            ('device_usbpid', '2055'), ('device_capabilities', None),
                            ('device_di_guid', None), ('is_exe', False)):
            monkeypatch.setattr(G, name, value, raising=False)
        from telemffb.SystemSettingsDialog import SystemSettingsDialog
        dlg = SystemSettingsDialog()

        assert dlg.instance_panels[('system', 'joystick')].widgets['logLevel'].currentText() == 'DEBUG'
        assert dlg.instance_panels[('system', 'pedals')].widgets['logLevel'].currentText() == 'INFO'
        assert dlg.instance_panels[('system', 'pedals')].widgets['telemTimeout'].text() == '750'
        dlg.deleteLater()
        app.processEvents()


class TestOwnRoleAlwaysPresent:
    def test_this_instance_gets_a_tab_even_if_unassigned(self, monkeypatch):
        """A device with no stored assignment yet must still be configurable,
        or a fresh install would have nowhere to set its own log level."""
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        settings = FakeSettings({'devpath_joystick': '', 'devpath_pedals': '',
                                 'devpath_collective': '', 'devpath_trimwheel': ''})
        monkeypatch.setattr(G, 'system_settings', settings, raising=False)
        for name, value in (('device_type', 'collective'), ('master_instance', True),
                            ('child_instance', False), ('launched_instances', []),
                            ('device_usbpid', '2055'), ('device_capabilities', None),
                            ('device_di_guid', None), ('is_exe', False)):
            monkeypatch.setattr(G, name, value, raising=False)
        from telemffb.SystemSettingsDialog import SystemSettingsDialog
        dlg = SystemSettingsDialog()
        assert ('system', 'collective') in dlg.instance_panels
        dlg.deleteLater()
        app.processEvents()
