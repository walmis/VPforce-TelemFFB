"""The device gain sliders in the log.

Every force the device renders is scaled by these, so the startup line, the
line after a vpconf push and the effect preview line all name them.  Only
VPforce hardware has them: any other backend must be left alone entirely.
"""
import logging
import sys
from types import SimpleNamespace

import pytest

from telemffb.utils import device as dev_utils

pytestmark = [pytest.mark.unit]

GAINS = SimpleNamespace(master_gain=80, periodic_gain=100, spring_gain=95,
                        damper_gain=70, inertia_gain=45, friction_gain=60,
                        constant_gain=100)


def install_device(monkeypatch, has_gains=True, gains=GAINS, reads=None):
    """A fake HapticEffect whose device reports (or refuses) gain sliders."""
    def get_gains():
        if reads is not None:
            reads.append(True)
        if isinstance(gains, Exception):
            raise gains
        return gains

    device = None
    if has_gains is not None:
        device = SimpleNamespace(caps=SimpleNamespace(has_gains=has_gains), get_gains=get_gains)
    module = SimpleNamespace(HapticEffect=SimpleNamespace(device=device))
    monkeypatch.setitem(sys.modules, 'telemffb.hw.ffb_rhino', module)
    return device


class TestFormatDeviceGains:
    def test_a_vpforce_device_reports_every_slider(self, monkeypatch):
        install_device(monkeypatch)
        line = dev_utils.format_device_gains()
        for slider in dev_utils.GAIN_SLIDERS:
            assert slider in line
            assert str(getattr(GAINS, f'{slider}_gain')) in line

    def test_a_device_without_gains_is_never_asked(self, monkeypatch):
        reads = []
        install_device(monkeypatch, has_gains=False, reads=reads)
        assert dev_utils.format_device_gains() == ''
        assert reads == []

    def test_a_failed_read_is_swallowed(self, monkeypatch):
        install_device(monkeypatch, gains=RuntimeError('device went away'))
        assert dev_utils.format_device_gains() == ''

    def test_no_device_is_swallowed(self, monkeypatch):
        install_device(monkeypatch, has_gains=None)
        assert dev_utils.format_device_gains() == ''


class TestLogDeviceGains:
    def test_gains_already_read_are_not_read_again(self, monkeypatch, caplog):
        """The vpconf worker reads once, latches the revert baseline from it and
        logs the same object; a second read could see different values."""
        reads = []
        install_device(monkeypatch, reads=reads)
        with caplog.at_level(logging.INFO):
            dev_utils.log_device_gains('vpconf Default.vpconf', GAINS)
        assert reads == []
        assert any('vpconf Default.vpconf' in r.message and str(GAINS.master_gain) in r.message
                   for r in caplog.records)

    def test_the_context_and_the_values_are_logged(self, monkeypatch, caplog):
        install_device(monkeypatch)
        with caplog.at_level(logging.INFO):
            dev_utils.log_device_gains('startup')
        assert any('startup' in r.message and str(GAINS.master_gain) in r.message
                   for r in caplog.records)

    def test_a_device_without_gains_logs_nothing(self, monkeypatch, caplog):
        install_device(monkeypatch, has_gains=False)
        with caplog.at_level(logging.INFO):
            dev_utils.log_device_gains('startup')
        assert caplog.records == []
