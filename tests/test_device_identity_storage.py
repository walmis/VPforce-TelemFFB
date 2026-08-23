"""What a configured slot remembers about its device.

Two different things, and conflating them is the trap: USB ids are the
device's identity and the only thing a tap rule is keyed on, while the
product name is a display hint. An owner can rename a VPforce device and a
vendor can reword a model, so a remembered name may be stale - which is
fine for labeling a dialog or annotating a config file, and unusable for
deciding which device a rule applies to.

The ids come out of the stored device path, which is why a rule can be
written for a device that is merely switched off.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G
from telemffb.custom_widgets import FFBDeviceListModel
from telemffb.SystemSettingsDialog import _same_hardware
from telemffb.utils import (device_ident_key, device_ids_key,
                            recover_device_identity,
                            usb_ids_from_devpath)

# Importing main (for the startup test) pulls in simconnect, which leaves
# a file handle open that the collector later complains about.
pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

REAL_PATH = (r"\\?\HID#VID_FFFF&PID_2054&MI_00#f&9caf07d&0&0000"
             r"#{4d1e55b2-f16f-11cf-88cb-001111000030}")


class TestUsbIdsFromDevpath:
    def test_the_ids_are_read_from_a_real_path(self):
        assert usb_ids_from_devpath(REAL_PATH) == (0xFFFF, 0x2054)

    def test_lower_case_hex_is_accepted(self):
        assert usb_ids_from_devpath(r"\\?\HID#vid_ffff&pid_2052&MI_00#x") == \
            (0xFFFF, 0x2052)

    @pytest.mark.parametrize("value", ["", None, "not a device path",
                                       r"\\?\HID#VID_ZZZZ&PID_2054#x"])
    def test_anything_unparseable_is_none_rather_than_a_guess(self, value):
        """A wrong id would write a rule for somebody else's hardware."""
        assert usb_ids_from_devpath(value) is None



class TestIdentIsRemembered:
    class FakeSettings(dict):
        def get(self, name, default=None, instance=None):
            key = f"{instance}/{name}" if instance is not None else name
            return dict.get(self, key, default)

        def setValue(self, key, value):
            self[key] = value

    class FakeDevice:
        def __init__(self, pid, ident, path):
            self.product_id = pid
            self.ident = ident
            self.vendor_id = 0xFFFF
            self.serial_number = "SERIAL"
            self.path = path

    @pytest.fixture
    def dialog(self, monkeypatch):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        monkeypatch.setattr(G, 'system_settings', self.FakeSettings({
            'devpath_joystick': '', 'devpath_pedals': '',
            'devpath_collective': '', 'devpath_trimwheel': '',
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

    def test_selecting_a_device_remembers_its_name(self, dialog):
        device = self.FakeDevice(0x2054, "VPforce Rhino", REAL_PATH.encode())
        dialog.cb_select_j.setModel(FFBDeviceListModel([device]))
        dialog.cb_select_j.setCurrentIndex(1)

        pending = dialog._pending_devpaths
        assert pending[device_ident_key('joystick')] == "VPforce Rhino"
        assert pending['devpath_joystick'] == REAL_PATH

    def test_clearing_a_slot_forgets_the_name_too(self, dialog):
        device = self.FakeDevice(0x2054, "VPforce Rhino", REAL_PATH.encode())
        dialog.cb_select_j.setModel(FFBDeviceListModel([device]))
        dialog.cb_select_j.setCurrentIndex(1)
        dialog.cb_select_j.setCurrentIndex(0)          # "(None)"

        pending = dialog._pending_devpaths
        assert pending['devpath_joystick'] == ''
        assert pending[device_ident_key('joystick')] == ''

    def test_reselecting_refreshes_a_renamed_device(self, dialog):
        """VPforce devices are renameable, so the stored name is refreshed
        every time the slot is set rather than written once."""
        first = self.FakeDevice(0x2054, "Old Name", REAL_PATH.encode())
        dialog.cb_select_j.setModel(FFBDeviceListModel([first]))
        dialog.cb_select_j.setCurrentIndex(1)
        assert dialog._pending_devpaths[device_ident_key('joystick')] == "Old Name"

        renamed = self.FakeDevice(0x2054, "New Name", REAL_PATH.encode())
        dialog.cb_select_j.setModel(FFBDeviceListModel([renamed]))
        dialog.cb_select_j.setCurrentIndex(1)
        assert dialog._pending_devpaths[device_ident_key('joystick')] == "New Name"

    def test_a_device_with_no_name_stores_an_empty_hint_not_a_crash(self, dialog):
        class Nameless:
            product_id = 0x2054
            vendor_id = 0xFFFF
            serial_number = "S"
            path = REAL_PATH.encode()

        dialog.cb_select_j.setModel(FFBDeviceListModel([Nameless()]))
        dialog.cb_select_j.setCurrentIndex(1)
        assert dialog._pending_devpaths[device_ident_key('joystick')] == ""
        assert dialog._pending_devpaths['devpath_joystick'] == REAL_PATH


class TestReportedIdsAreStored:
    """The device is asked for its ids rather than having them parsed out of
    its path, because a DirectInput device's path is a GUID with no ids in
    it at all."""

    class FakeSettings(dict):
        def get(self, name, default=None, instance=None):
            return dict.get(self, name, default)

    class DirectInputDevice:
        """What the DirectInput backend hands over: a GUID for a path, and
        the ids reported separately."""
        product_id = 0x001B
        vendor_id = 0x045E
        serial_number = "S"
        ident = "SideWinder Force Feedback 2"
        path = b"{A1B2C3D4-0000-0000-0000-504944564944}"

    @pytest.fixture
    def dialog(self, monkeypatch):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        monkeypatch.setattr(G, 'system_settings', self.FakeSettings({
            'devpath_joystick': '', 'devpath_pedals': '',
            'devpath_collective': '', 'devpath_trimwheel': '',
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

    def test_a_directinput_device_records_usable_ids(self, dialog):
        from telemffb.utils import device_ids_key
        dialog.cb_select_j.setModel(FFBDeviceListModel([self.DirectInputDevice()]))
        dialog.cb_select_j.setCurrentIndex(1)
        assert dialog._pending_devpaths[device_ids_key('joystick')] == "045E:001B"

    def test_clearing_a_slot_clears_the_ids(self, dialog):
        from telemffb.utils import device_ids_key
        dialog.cb_select_j.setModel(FFBDeviceListModel([self.DirectInputDevice()]))
        dialog.cb_select_j.setCurrentIndex(1)
        dialog.cb_select_j.setCurrentIndex(0)
        assert dialog._pending_devpaths[device_ids_key('joystick')] == ""

    def test_a_device_reporting_nothing_stores_nothing(self, dialog):
        """Rather than 0000:0000, which would be a rule matching the wrong
        thing or nothing at all."""
        from telemffb.utils import device_ids_key

        class Unreported(self.DirectInputDevice):
            vendor_id = 0
            product_id = 0

        dialog.cb_select_j.setModel(FFBDeviceListModel([Unreported()]))
        dialog.cb_select_j.setCurrentIndex(1)
        assert dialog._pending_devpaths[device_ids_key('joystick')] == ""

class TestFillingInWhatWasNeverStored:
    """A slot configured before the identity keys existed - or filled by
    first-launch auto-assignment, which only ever wrote the path - holds a
    devpath and nothing else.

    The two gaps differ in how they hurt.  Missing ids make the device
    unidentifiable, so rules and comparisons silently see nothing.  A
    missing name only shows as "unnamed device" - but on a dialog naming
    other devices correctly, which reads as TelemFFB not recognizing
    hardware that is plugged in and working.

    Both are recoverable, because the hardware is enumerated at startup
    and again in the settings dialog's selectors.
    """

    Device = TestIdentIsRemembered.FakeDevice

    #: Distinct from REAL_PATH, which names hardware the machine running
    #: these tests may actually have plugged in.
    PEDAL_PATH = (r"\\?\HID#VID_FFFF&PID_2052&MI_00#f&1234567&0&0000"
                  r"#{4d1e55b2-f16f-11cf-88cb-001111000030}")

    #: A DirectInput path is an instance GUID: no ids to parse back out.
    DINPUT_PATH = "dinput:{a1b2c3d4-0000-0000-0000-504944564944}"

    def test_a_name_that_was_never_stored_comes_from_the_hardware(self):
        pedals = self.Device(0x2052, "VPforce Pedals", self.PEDAL_PATH.encode())
        got = recover_device_identity({'devpath_pedals': self.PEDAL_PATH},
                                      [pedals])
        assert got[device_ident_key('pedals')] == "VPforce Pedals"

    def test_ids_that_were_never_stored_do_too(self):
        """Nothing else can supply them for a DirectInput device."""
        stick = self.Device(0x2054, "Monster", self.DINPUT_PATH.encode())
        got = recover_device_identity({'devpath_joystick': self.DINPUT_PATH},
                                      [stick])
        assert got[device_ids_key('joystick')] == "FFFF:2054"

    def test_what_is_already_stored_is_left_alone(self):
        """Only gaps are filled.  A remembered name is the one that was on
        the device when the slot was set; overwriting it would undo a rename
        its owner has not reselected through yet."""
        pedals = self.Device(0x2052, "New Name", self.PEDAL_PATH.encode())
        got = recover_device_identity(
            {'devpath_pedals': self.PEDAL_PATH,
             device_ident_key('pedals'): "Old Name",
             device_ids_key('pedals'): "FFFF:2052"}, [pedals])
        assert got == {}

    def test_an_unplugged_device_is_not_given_somebody_elses_identity(self):
        """Nothing to learn from, and the slot still has to work: a rule
        can be written for a device that is switched off."""
        other = self.Device(0x2054, "Some Other Stick", REAL_PATH.encode())
        got = recover_device_identity({'devpath_pedals': self.PEDAL_PATH},
                                      [other])
        assert got == {}


class TestStartupHealsOldSettings:
    """Thousands of installs predate the identity keys.  Waiting for each
    owner to reselect their devices is a support case per owner; the
    devices are enumerated at startup anyway, so that is when it is fixed.
    """

    Device = TestIdentIsRemembered.FakeDevice
    PATH = TestFillingInWhatWasNeverStored.PEDAL_PATH

    def run(self, monkeypatch, *, master):
        # imported here: it pulls in simconnect, which leaves a handle open
        import main
        settings = TestIdentIsRemembered.FakeSettings(
            {'devpath_pedals': self.PATH})
        monkeypatch.setattr(G, 'system_settings', settings, raising=False)
        monkeypatch.setattr(G, 'master_instance', master, raising=False)
        pedals = self.Device(0x2052, "VPforce Pedals", self.PATH.encode())
        main._record_device_identity([pedals])
        return settings

    def test_missing_identity_is_written_once_at_startup(self, monkeypatch):
        settings = self.run(monkeypatch, master=True)
        assert settings[device_ident_key('pedals')] == "VPforce Pedals"
        assert settings[device_ids_key('pedals')] == "FFFF:2052"

    def test_a_directinput_stick_heals_too_while_support_is_on(
            self, monkeypatch):
        """HID enumeration never lists it - its path is an instance GUID -
        and the settings dialog used to be the only place it could heal.
        A master driving a DirectInput stick restarts with no ids stored
        for its own slot otherwise."""
        import main
        settings = TestIdentIsRemembered.FakeSettings(
            {'devpath_joystick': 'dinput:{GUID-A}', 'enableDirectInput': True})
        monkeypatch.setattr(G, 'system_settings', settings, raising=False)
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        stick = self.Device(0x001B, "[DI] SideWinder", b"dinput:{GUID-A}")
        stick.vendor_id = 0x045E
        monkeypatch.setattr(main.utils, 'directinput_selection_devices',
                            lambda settings, enabled=None: [stick])
        main._record_device_identity([])
        assert settings[device_ids_key('joystick')] == "045E:001B"
        assert settings[device_ident_key('joystick')] == "[DI] SideWinder"

    def test_a_child_instance_writes_nothing(self, monkeypatch):
        """Settings writes at startup are the master's, as everywhere else;
        four instances racing to write the same keys helps nobody."""
        settings = self.run(monkeypatch, master=False)
        assert device_ident_key('pedals') not in settings


class TestTheSameHardwareTwice:
    """With the vpforce_as_dinput debug flag on, a VPforce device is listed
    twice - natively and as a DirectInput device - under two paths.  The
    native entry in one slot and the DirectInput entry in another would be
    one stick in two roles, which the conflict check used to let through
    because it only compared paths and serials."""

    Device = TestIdentIsRemembered.FakeDevice

    def rhino(self):
        return self.Device(0x2054, "Rhino", REAL_PATH.encode())

    def rhino_as_dinput(self):
        dev = self.Device(0x2054, "[DI] Rhino", b"dinput:{GUID-A}")
        dev.serial_number = None          # DirectInput knows no serial
        return dev

    def test_the_native_and_directinput_listings_are_one_device(self):
        assert _same_hardware(self.rhino(), self.rhino_as_dinput())

    def test_two_identical_models_over_hid_are_still_two_devices(self):
        other = self.Device(0x2054, "Rhino", REAL_PATH.replace("0000#", "0001#").encode())
        other.serial_number = "OTHER"
        assert not _same_hardware(self.rhino(), other)

    def test_two_different_directinput_devices_are_two_devices(self):
        a = self.Device(0x2054, "[DI] Rhino", b"dinput:{GUID-A}")
        b = self.Device(0x2054, "[DI] Rhino", b"dinput:{GUID-B}")
        assert not _same_hardware(a, b)

    def test_the_dialog_refuses_the_second_slot(self, monkeypatch):
        """Assigning the DirectInput listing to the pedals while the native
        listing holds the joystick is reverted like any other conflict."""
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        monkeypatch.setattr(G, 'system_settings',
                            TestIdentIsRemembered.FakeSettings({
                                'devpath_joystick': '', 'devpath_pedals': '',
                                'devpath_collective': '', 'devpath_trimwheel': ''}),
                            raising=False)
        for name, value in (('device_type', 'joystick'),
                            ('master_instance', True), ('child_instance', False),
                            ('launched_instances', []), ('device_usbpid', '2054'),
                            ('device_capabilities', None),
                            ('device_di_guid', None), ('is_exe', False)):
            monkeypatch.setattr(G, name, value, raising=False)
        # the conflict box is built inline and exec'd; a stubbed exec leaves
        # no clicked button, which the dialog reads as "cancel"
        monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', lambda self: 0)
        from telemffb.SystemSettingsDialog import SystemSettingsDialog
        dialog = SystemSettingsDialog()
        dialog.cb_select_j.setModel(FFBDeviceListModel([self.rhino()]))
        dialog.cb_select_p.setModel(FFBDeviceListModel([self.rhino_as_dinput()]))
        dialog._pending_devpaths.clear()
        dialog.cb_select_j.setCurrentIndex(1)
        dialog.cb_select_p.setCurrentIndex(1)
        assert dialog.cb_select_p.currentIndex() == 0
        assert not dialog._pending_devpaths.get('devpath_pedals')
        dialog.deleteLater()
        app.processEvents()


class TestListingDirectInputDevices:
    """What the selectors - and now startup - list as [DI] entries."""

    class Listed:
        def __init__(self, vid, name, guid):
            self.vendor_id, self.product_id = vid, 0x0001
            self.product_string, self.guid, self.path = name, guid, b""

    def listing(self, monkeypatch, devices):
        from telemffb.hw import ffb_dinput
        monkeypatch.setattr(ffb_dinput.DInputFFBDevice, 'enumerate',
                            staticmethod(lambda *a, **k: devices))

    def test_nothing_is_listed_while_support_is_off(self, monkeypatch):
        from telemffb.utils import directinput_selection_devices
        self.listing(monkeypatch, [self.Listed(0x045E, "SideWinder", "{A}")])
        assert directinput_selection_devices({'enableDirectInput': False}) == []

    def test_a_vpforce_device_is_left_to_the_hid_listing(self, monkeypatch):
        """Unless the debug flag asks for it twice."""
        from telemffb.utils import directinput_selection_devices
        self.listing(monkeypatch, [self.Listed(0x045E, "SideWinder", "{A}"),
                                   self.Listed(0xFFFF, "Rhino", "{B}")])
        listed = directinput_selection_devices({'enableDirectInput': True})
        assert [d.guid for d in listed] == ["{A}"]
        assert listed[0].product_string == "[DI] SideWinder"
        assert listed[0].path == b"dinput:{A}"
        self.listing(monkeypatch, [self.Listed(0xFFFF, "Rhino", "{B}")])
        both = directinput_selection_devices({'enableDirectInput': True,
                                              'vpforce_as_dinput': '1'})
        assert [d.guid for d in both] == ["{B}"]
