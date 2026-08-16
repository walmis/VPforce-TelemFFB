import ctypes
import pytest

import telemffb.hw.ffb_rhino as ffb_rhino


class DummyDevice:
    def __init__(self, path):
        # mimic hid.Device attributes accessed by FFBRhino
        self.path = path
        self.serial = b"SERIAL123"
        self.product = b"Rhino FFB Joystick"
        self.manufacturer = b"VPforce"
        self._written = []
        self.nonblocking = False

    def write(self, data):
        # pretend to succeed
        self._written.append(bytes(data))
        return len(data)

    def read(self, size):
        # no data available
        return b""

    def get_feature_report(self, report_id, length):
        # return a buffer sized data for get_gains or block load
        # for block load (report id 6) return [reportId, effectId, status, ...]
        if report_id == ffb_rhino.HID_REPORT_ID_PID_BLOCK_LOAD:
            return bytes([ffb_rhino.HID_REPORT_ID_PID_BLOCK_LOAD, 1, ffb_rhino.LOAD_SUCCESS]) + bytes(length - 3)
        # For get gains
        if report_id == ffb_rhino.HID_REPORT_FEATURE_ID_GET_GAINS:
            # return zeroed structure of right size
            return bytes(length)
        return bytes(length)

    def send_feature_report(self, data):
        self._written.append(bytes(data))
        return len(data)

    def close(self):
        pass


def test_open_with_path(monkeypatch):
    # Prepare a fake device info returned by hid.enumerate
    fake_path = b"\\\\?\\HID#VID_FFFF&PID_2055&MI_00#FAKEPATH"
    fake_dev_info = {
        'interface_number': 0,
        'manufacturer_string': 'VPforce',
        'path': fake_path,
        'product_id': 0x2055,
        'product_string': 'Rhino FFB Joystick',
        'release_number': 0,
        'serial_number': 'SERIAL123',
        'usage': 4,
        'usage_page': 1,
        'vendor_id': 0xffff,
    }

    # Monkeypatch hid.enumerate to return our fake device info
    monkeypatch.setattr(ffb_rhino.hid, 'enumerate', lambda vid, pid: [fake_dev_info])

    # Track the path used to open Device
    opened_paths = []

    def fake_device_constructor(path):
        opened_paths.append(path)
        return DummyDevice(path)

    monkeypatch.setattr(ffb_rhino.hid, 'Device', fake_device_constructor)

    # Create FFBRhino by specifying path
    # Note: FFBRhino.__init__ will call enumerate when path is falsy; to test explicit path handling
    # we call FFBRhino with a dummy vid/pid and let the class pick the first device.
    r = ffb_rhino.FFBRhino(path=str(fake_path, 'utf-8'))

    # After initialization, the device's info.path should match our fake path
    assert r.info.path == fake_path
    # And the hid.Device should have been constructed with that path
    assert opened_paths and opened_paths[0] == fake_path
