"""Deadzone recovery tests (device-state-handling plan).

The MixIn's per-frame deadzone write is transition-gated, so after a
device power cycle the firmware sits at 0 while the in-memory state says
the configured deadzone is already applied.  The ``force`` flag bypasses
the gate so the recovery replay can push the value back.
"""
from unittest.mock import MagicMock

import pytest

from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.base.DeadzoneMixIn import DeadzoneMixIn


@pytest.fixture
def alive_device(monkeypatch):
    dev = MagicMock()
    dev.connected = True
    monkeypatch.setattr(HapticEffect, 'device', dev)
    return dev


def _steady_mixin():
    class _M(DeadzoneMixIn):
        pass

    m = _M()
    m.enable_deadzone = True
    m.deadzone_base_pct = 0.2
    m.active_deadzone_pct = 0.2   # 20%, "already applied" in memory
    m.deadzone_active = True
    return m


@pytest.mark.unit
class TestForcedReapply:

    def test_steady_deadzone_writes_nothing_without_force(self, alive_device):
        m = _steady_mixin()
        m.ac_update_deadzone()
        alive_device.set_deadzone.assert_not_called()

    def test_force_writes_the_current_value(self, alive_device):
        m = _steady_mixin()
        m.ac_update_deadzone(force=True)
        alive_device.set_deadzone.assert_called_once_with(819)  # pct2dz(0.2)
        assert m.deadzone_active is True
        assert m.active_deadzone_pct == 0.2   # in-memory state unchanged

    def test_force_with_dead_device_defers_silently(self, monkeypatch):
        monkeypatch.setattr(HapticEffect, 'device', None)
        m = _steady_mixin()
        m.ac_update_deadzone(force=True)      # must not raise
        assert m.active_deadzone_pct == 0.2
        assert m.deadzone_active is True
