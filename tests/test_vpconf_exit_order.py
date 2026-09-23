"""The exit sequence leaves the device in the documented state.

docs/telemffb/vpconf-profiles.md rule 4: an Exit profile is pushed regardless,
and with Restore Startup Gains on Exit enabled TelemFFB puts back the gains it
read at startup, "leaving the device as it found it".  The push runs the
Configurator in a background thread, so it has to be waited on: otherwise the
profile lands on top of the restore, or the process exits mid-write.
"""
import threading
import time

import pytest

from telemffb.utils import integration

pytestmark = [pytest.mark.unit]


class FakeThread:
    """A push that finishes only when joined, the way a real one outlives the
    call that started it."""

    def __init__(self, target, order):
        self._target, self._order, self._done = target, order, False

    def start(self):
        self._order.append('push started')

    def join(self, timeout=None):
        self._target()
        self._done = True

    def is_alive(self):
        return not self._done


@pytest.fixture
def push(monkeypatch, tmp_path):
    """upload_vpconf_profile with the Configurator and its device stubbed out."""
    order = []
    cfg = tmp_path / "exit.vpconf"
    cfg.write_text("{}")

    monkeypatch.setattr(integration, 'QSettings',
                        lambda *a: type('S', (), {'value': lambda self, k: 'C:/vpconf.exe'})())
    monkeypatch.setattr(integration, 'validate_vpconf_profile', lambda *a, **k: True)
    monkeypatch.setattr(integration.G, 'device_capabilities', None, raising=False)
    monkeypatch.setattr(integration.G, 'device_info',
                        type('I', (), {'product_id': 1})(), raising=False)
    monkeypatch.setattr(integration.G, 'app_state', None, raising=False)
    monkeypatch.setattr(integration.G, 'telem_manager', None, raising=False)
    monkeypatch.setattr(integration, 'read_device_gains', lambda: None)
    monkeypatch.setattr(integration, 'log_device_gains', lambda *a, **k: None)
    monkeypatch.setattr(integration.subprocess, 'call',
                        lambda *a, **k: (order.append('configurator applied profile'), 0)[1])
    monkeypatch.setattr(integration.threading, 'Thread',
                        lambda target=None, **k: FakeThread(target, order))
    return order, str(cfg)


class TestExitPush:
    def test_waiting_lets_the_profile_land_before_the_caller_continues(self, push):
        order, cfg = push
        integration.upload_vpconf_profile(cfg, "SER1", wait=True)
        order.append('gains restored')
        assert order == ['push started', 'configurator applied profile', 'gains restored']

    def test_without_waiting_the_caller_runs_first(self, push):
        """The aircraft-change path wants this: it must not block telemetry."""
        order, cfg = push
        integration.upload_vpconf_profile(cfg, "SER1")
        order.append('caller continued')
        assert order == ['push started', 'caller continued']

    def test_a_hung_configurator_does_not_hold_shutdown_forever(self, push, monkeypatch):
        order, cfg = push
        joined = []

        class Hung(FakeThread):
            def join(self, timeout=None):
                joined.append(timeout)          # never finishes

        monkeypatch.setattr(integration.threading, 'Thread',
                            lambda target=None, **k: Hung(target, order))
        integration.upload_vpconf_profile(cfg, "SER1", wait=True)
        assert joined == [integration.VPCONF_PUSH_WAIT_S]
        assert 'configurator applied profile' not in order
