"""The USB device watch reports a change of the devices present, from a
libusb read that opens no device."""
import pytest

from telemffb.utils.device import UsbDeviceWatch

pytestmark = [pytest.mark.unit]


def _watch(monkeypatch, readings):
    readings = list(readings)
    monkeypatch.setattr(UsbDeviceWatch, 'present', staticmethod(lambda: readings.pop(0)))
    return UsbDeviceWatch()


def test_a_change_of_the_devices_present_is_reported_once(monkeypatch):
    stick = (1, 4, 0xFFFF, 0x2055)
    base = (2, 9, 0x346E, 0x1002)
    watch = _watch(monkeypatch, [frozenset({stick}), frozenset({stick}),
                                 frozenset({stick, base}), frozenset({stick, base}),
                                 frozenset({base})])
    assert [watch.changed() for _ in range(5)] == [True, False, True, False, True]


def test_a_primed_watch_reports_no_change_for_the_same_devices(monkeypatch):
    devices = frozenset({(1, 4, 0xFFFF, 0x2055)})
    watch = _watch(monkeypatch, [devices, devices, frozenset()])
    watch.prime()
    assert watch.changed() is False
    assert watch.changed() is True


def test_a_failed_read_counts_as_a_change(monkeypatch):
    devices = frozenset({(1, 4, 0xFFFF, 0x2055)})
    watch = _watch(monkeypatch, [devices, None, None, devices])
    assert [watch.changed() for _ in range(4)] == [True, True, True, True]


def test_the_real_read_lists_devices_or_reports_none():
    """libusb on this machine: a set of 4-tuples, or None where it is
    unavailable; never an exception."""
    present = UsbDeviceWatch.present()
    assert present is None or all(len(d) == 4 for d in present)
