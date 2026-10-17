"""Generic HID controllers as button sources: key assignment, hat decoding,
the reader's change reporting and the manager's reader bookkeeping.  All of
it runs on fakes; no hidapi, hid.dll or hardware is touched.
"""
import json
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.modules.setdefault('telemffb.hw.hid', MagicMock())

from PyQt6.QtWidgets import QApplication

import telemffb.globals as G
import telemffb.hw.button_devices as button_devices
from telemffb.hw.button_devices import (ButtonDeviceInfo, ButtonDeviceManager,
                                        ButtonDeviceReader, assign_keys,
                                        enumerate_button_devices, hat_buttons)

pytestmark = [pytest.mark.unit]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeSettings:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def info(vid, pid, path, serial='', key='', name=''):
    return ButtonDeviceInfo(key=key, path=path, name=name, vid=vid, pid=pid, serial=serial)


def test_assign_keys_disambiguates_shared_vid_pid():
    unique = info(0x1234, 0x0001, b'p0')
    by_serial = [info(0x1234, 0x0002, b'p2', serial='A:1'), info(0x1234, 0x0002, b'p1', serial='B')]
    by_path = [info(0x1234, 0x0003, b'p9', serial='S'), info(0x1234, 0x0003, b'p3', serial='S'),
               info(0x1234, 0x0003, b'p5')]

    assign_keys([unique] + by_serial + by_path)

    assert unique.key == '1234:0001'
    assert [d.key for d in by_serial] == ['1234:0002#A_1', '1234:0002#B']
    assert [d.key for d in by_path] == ['1234:0003#3', '1234:0003#1', '1234:0003#2']


def test_hat_buttons_maps_positions_to_virtual_buttons():
    base = 11
    up, right, down, left = base, base + 1, base + 2, base + 3
    eight_way = [hat_buttons(v, 0, 7, base) for v in range(8)]
    assert eight_way == [{up}, {up, right}, {right}, {right, down},
                         {down}, {down, left}, {left}, {left, up}]
    assert hat_buttons(8, 0, 7, base) == frozenset()
    assert hat_buttons(0, 1, 8, base) == frozenset()
    assert [hat_buttons(v, 0, 3, base) for v in range(4)] == [{up}, {right}, {down}, {left}]


class ScriptedDevice:
    """Returns each scripted report once, then reads time out (empty)."""

    def __init__(self, reports, fail_after=False):
        self.reports = list(reports)
        self.fail_after = fail_after
        self.exhausted = threading.Event()
        self.closed = False

    def read(self, size, timeout):
        if self.reports:
            return self.reports.pop(0)
        self.exhausted.set()
        if self.fail_after:
            raise OSError('device gone')
        time.sleep(0.001)
        return b''

    def close(self):
        self.closed = True


class ByteDecoder:
    """Each nonzero byte of a report is a pressed button."""
    input_report_length = 8

    def __init__(self, path):
        self.closed = False

    def decode(self, report):
        return frozenset(b for b in report if b)

    def close(self):
        self.closed = True


def run_reader(device):
    calls = []
    decoders = []

    def decoder_factory(path):
        decoders.append(ByteDecoder(path))
        return decoders[-1]

    dev_info = info(0x1234, 0x0001, b'p', key='1234:0001')
    reader = ButtonDeviceReader(dev_info, lambda key, pressed: calls.append((key, pressed)),
                                decoder_factory=decoder_factory,
                                device_factory=lambda path: device)
    assert reader.start()
    assert device.exhausted.wait(2.0)
    if device.fail_after:
        # the thread ends on its own; stopping first would mask the failure
        deadline = time.monotonic() + 2.0
        while reader.running and time.monotonic() < deadline:
            time.sleep(0.005)
    reader.stop()
    assert not reader.running
    return dev_info, calls, decoders[0]


def test_reader_reports_only_changes():
    device = ScriptedDevice([b'\x00', b'\x03', b'\x03', b'\x00', b'\x00'])

    dev_info, calls, decoder = run_reader(device)

    assert calls == [('1234:0001', {3}), ('1234:0001', frozenset())]
    assert device.closed and decoder.closed
    assert dev_info.readable


def test_a_failed_open_marks_the_device_unreadable_off_the_calling_thread():
    """The open is device I/O that can block, so it runs on the reader's
    thread, and its failure is reported on the device info."""
    opened_on = []

    def device_factory(path):
        opened_on.append(threading.current_thread())
        raise OSError('busy')
    dev_info = info(0x1234, 0x0001, b'p', key='1234:0001')
    reader = ButtonDeviceReader(dev_info, lambda key, pressed: None,
                                decoder_factory=ByteDecoder, device_factory=device_factory)

    assert reader.start()
    deadline = time.monotonic() + 2.0
    while reader.running and time.monotonic() < deadline:
        time.sleep(0.005)

    assert not reader.running
    assert opened_on and opened_on[0] is not threading.main_thread()
    assert not dev_info.readable


def test_reader_failure_releases_and_marks_unreadable():
    device = ScriptedDevice([b'\x05'], fail_after=True)

    dev_info, calls, decoder = run_reader(device)

    assert calls == [('1234:0001', {5}), ('1234:0001', frozenset())]
    assert not dev_info.readable
    assert device.closed and decoder.closed


class FakeReader:
    def __init__(self, dev_info, on_change):
        self.info = dev_info
        self.on_change = on_change
        self.stopped = False

    def start(self):
        return True

    def stop(self):
        self.stopped = True


def scan(manager, qapp):
    """One listing, delivered: the worker thread joined and its result
    reconciled through the event loop."""
    manager.rescan()
    manager.wait_for_scan()
    qapp.processEvents()


def make_manager(settings, listed):
    readers = {}

    def factory(dev_info, on_change):
        readers[dev_info.key] = FakeReader(dev_info, on_change)
        return readers[dev_info.key]

    manager = ButtonDeviceManager(
        settings=settings,
        enumerate_devices=lambda s: [info(d.vid, d.pid, d.path, key=d.key, name=d.name)
                                     for d in listed],
        reader_factory=factory)
    emitted = []
    manager.buttons_changed.connect(lambda key, pressed: emitted.append((key, pressed)))
    return manager, readers, emitted


def test_manager_releases_vanished_device(qapp):
    listed = [info(0x1234, 0x0001, b'p1', key='1234:0001')]
    manager, readers, emitted = make_manager(FakeSettings(buttonDevicesEnabled='1234:0001'), listed)
    scan(manager, qapp)
    readers['1234:0001'].on_change('1234:0001', frozenset({2, 7}))
    qapp.processEvents()
    assert manager.states == {'1234:0001': {2, 7}}

    listed.clear()
    scan(manager, qapp)

    assert emitted == [('1234:0001', [2, 7]), ('1234:0001', [])]
    assert manager.states == {}
    assert readers['1234:0001'].stopped
    assert manager.devices() == []


def test_reader_change_is_stored_without_the_event_loop(qapp, monkeypatch):
    monkeypatch.setattr(G, 'button_states', {}, raising=False)
    monkeypatch.setattr(G, 'ipc_instance', None, raising=False)
    listed = [info(0x1234, 0x0001, b'p1', key='1234:0001')]
    manager, readers, emitted = make_manager(FakeSettings(buttonDevicesEnabled='1234:0001'), listed)
    scan(manager, qapp)

    readers['1234:0001'].on_change('1234:0001', frozenset({3}))

    assert G.button_states == {'1234:0001': frozenset({3})}
    assert manager.states == {'1234:0001': {3}}
    assert emitted == [('1234:0001', [3])]


def test_a_controller_is_read_only_once_the_user_enables_it(qapp):
    settings = FakeSettings(buttonDevicesEnabled='1234:0001')
    listed = [info(0x1234, 0x0001, b'p1', key='1234:0001'),
              info(0x1234, 0x0002, b'p2', key='1234:0002')]
    manager, readers, emitted = make_manager(settings, listed)

    scan(manager, qapp)

    assert set(readers) == {'1234:0001'}
    assert [(d.key, d.ignored) for d in manager.devices()] == [('1234:0001', False),
                                                               ('1234:0002', True)]
    assert set(manager.states) == {'1234:0001'}

    manager.set_ignored('1234:0002', False)

    assert set(readers) == {'1234:0001', '1234:0002'}
    assert settings.values['buttonDevicesEnabled'] == '1234:0001,1234:0002'

    manager.set_ignored('1234:0001', True)

    assert readers['1234:0001'].stopped
    assert settings.values['buttonDevicesEnabled'] == '1234:0002'


def test_a_new_controller_starts_off(qapp):
    listed = [info(0x1234, 0x0001, b'p1', key='1234:0001')]
    manager, readers, emitted = make_manager(FakeSettings(), listed)

    scan(manager, qapp)

    assert readers == {}
    assert [(d.key, d.ignored) for d in manager.devices()] == [('1234:0001', True)]


class CountingSettings(FakeSettings):
    def __init__(self, **values):
        super().__init__(**values)
        self.writes = []

    def setValue(self, key, value):
        self.writes.append(key)
        super().setValue(key, value)


def test_manager_merges_device_names_without_dropping_any(qapp):
    settings = CountingSettings(buttonDeviceNames=json.dumps({'AAAA:0001': 'Old'}))
    listed = [info(0x1234, 0x0001, b'p1', key='1234:0001', name='Box'),
              # the VID:PID fallback is no name worth storing
              info(0x1234, 0x0002, b'p2', key='1234:0002', name='1234:0002')]
    manager, readers, emitted = make_manager(settings, listed)

    scan(manager, qapp)
    scan(manager, qapp)

    expected = {'AAAA:0001': 'Old', '1234:0001': 'Box'}
    assert json.loads(settings.values['buttonDeviceNames']) == expected
    assert settings.writes.count('buttonDeviceNames') == 1

    listed.clear()
    scan(manager, qapp)

    assert json.loads(settings.values['buttonDeviceNames']) == expected


def _fake_listing(monkeypatch, present):
    """The native listing on fakes: ``present`` is a one-item list holding
    the current interface set; returns the paths the two passes asked."""
    quick_calls, string_calls = [], []

    def quick(path):
        quick_calls.append(path)
        return {'path': path.encode(), 'vendor_id': 0x1111, 'product_id': 0x0001,
                'usage_page': 1, 'usage': 4, 'product_string': '', 'serial_number': ''}

    def strings(path):
        string_calls.append(path)
        return 'Box', 'S1'

    monkeypatch.setattr(button_devices, 'present_hid_paths', lambda: present[0])
    monkeypatch.setattr(button_devices, '_quick_entry', quick)
    monkeypatch.setattr(button_devices, '_read_strings', strings)
    monkeypatch.setattr(button_devices, '_hid_listing', button_devices._HidListing())
    return quick_calls, string_calls


def test_strings_come_in_a_second_pass_and_neither_pass_repeats(monkeypatch):
    """Ids and usages come from the driver; the strings are requests to
    the device and come in a second pass.  Until they arrive a controller
    is named by its ids, and nothing is asked again while the interface
    set is unchanged."""
    present = [frozenset({'\\\\?\\hid#box'})]
    quick_calls, string_calls = _fake_listing(monkeypatch, present)
    settings = FakeSettings()

    first = enumerate_button_devices(settings, strings=False)
    assert [(d.key, d.name) for d in first] == [('1111:0001', '1111:0001')]
    assert string_calls == []
    assert button_devices._hid_listing.strings_pending()

    full = enumerate_button_devices(settings)
    assert [(d.key, d.name, d.serial) for d in full] == [('1111:0001', 'Box', 'S1')]
    assert not button_devices._hid_listing.strings_pending()

    enumerate_button_devices(settings)
    assert (quick_calls, string_calls) == (['\\\\?\\hid#box'], ['\\\\?\\hid#box'])

    present[0] = frozenset({'\\\\?\\hid#box', '\\\\?\\hid#new'})
    enumerate_button_devices(settings)
    assert quick_calls == ['\\\\?\\hid#box', '\\\\?\\hid#new']
    assert string_calls == ['\\\\?\\hid#box', '\\\\?\\hid#new']


def test_the_manager_publishes_the_controllers_before_their_strings(qapp, monkeypatch):
    """The readers start and the panel fills from the first pass; the
    names follow when the strings arrive."""
    present = [frozenset({'\\\\?\\hid#box'})]
    _fake_listing(monkeypatch, present)
    release = threading.Event()
    original = button_devices._read_strings

    def slow_strings(path):
        release.wait(5)
        return original(path)
    monkeypatch.setattr(button_devices, '_read_strings', slow_strings)
    readers = {}

    def factory(dev_info, on_change):
        readers[dev_info.key] = FakeReader(dev_info, on_change)
        return readers[dev_info.key]
    settings = FakeSettings(buttonDevicesEnabled='1111:0001')
    manager = ButtonDeviceManager(settings=settings, reader_factory=factory)
    names = []
    manager.devices_changed.connect(lambda: names.append([d.name for d in manager.devices()]))

    manager.rescan()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not names:
        qapp.processEvents()
        time.sleep(0.01)
    assert names == [['1111:0001']]
    assert '1111:0001' in readers
    release.set()
    manager.wait_for_scan()
    qapp.processEvents()
    assert names == [['1111:0001'], ['Box']]
    manager.stop()


def test_enumerate_excludes_ffb_devices_and_other_usages(monkeypatch):
    def entry(vid, pid, path, usage_page=1, usage=4, product='Box'):
        return {'path': path, 'vendor_id': vid, 'product_id': pid, 'serial_number': '',
                'product_string': product, 'usage_page': usage_page, 'usage': usage}

    # no interface list: hidapi's listing as is
    monkeypatch.setattr(button_devices, 'present_hid_paths', lambda: None)
    monkeypatch.setattr(button_devices.hid, 'enumerate', lambda *a, **k: [
        entry(0x1111, 0x0001, b'\\\\?\\hid#keep', product=''),
        entry(0x1111, 0x0002, b'\\\\?\\hid#gamepad', usage=5),
        entry(0xFFFF, 0x2055, b'\\\\?\\hid#rhino'),
        entry(0x2222, 0x0001, b'\\\\?\\HID#PEDALS'),
        entry(0x3333, 0x0001, b'\\\\?\\hid#di'),
        entry(0x4444, 0x0001, b'\\\\?\\hid#mouse', usage=2),
        entry(0x5555, 0x0001, b'\\\\?\\hid#consumer', usage_page=0x0C, usage=1),
        entry(0x045E, 0x02FF, b'\\\\?\\HID#VID_045E&PID_02FF&IG_00#xinput', usage=5),
    ])
    settings = FakeSettings(devpath_pedals='\\\\?\\hid#pedals', devids_collective='3333:0001')

    listed = enumerate_button_devices(settings)

    assert [d.key for d in listed] == ['1111:0001', '1111:0002', '045E:02FF']
    # an XInput interface is listed but never opened
    assert [(d.readable, d.xinput) for d in listed] == [(True, False), (True, False), (False, True)]
