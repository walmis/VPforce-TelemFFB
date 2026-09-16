"""Offline-device telemetry tests (device-state-handling plan, Task 3).

The TelemManager thread is the single consumer of all telemetry: one
unhandled exception inside its loop used to terminate the thread and freeze
FFB for the rest of the session (the 13:55:41 field-log death).  These tests
prove the run() loop survives raising on_timeout()/process_data()/
process_events() calls, logs the failure with a stack trace, and keeps
working - and that the per-frame device-touching helpers (deadzone writes,
input reads) degrade to safe no-ops when the device is absent (zombie
startup) or hot-unplugged.
"""
import logging
import threading
import time
import types

import pytest
from unittest.mock import MagicMock


def _guard_scvars_unraisables():
    """Route the scvars.json FileIO finalizer out of pytest's unraisable hook.

    pysimconnect's scvars loader does ``json.load(open(...))`` and never
    closes the file; the orphaned FileIO's finalizer can raise when it is
    garbage-collected (a pysimconnect import-style artifact, unrelated to
    app behaviour).  pytest's unraisable plugin turns that into a test error
    mid-session and/or a hard session failure at unconfigure.  Swallowing
    just that one signature keeps the session green; every other unraisable
    is forwarded untouched so it still fails loudly.
    """
    import sys
    current = sys.unraisablehook

    def guarded(unraisable):
        name = getattr(unraisable.value, "name", None)
        if isinstance(name, str) and "scvars.json" in name:
            return
        current(unraisable)

    sys.unraisablehook = guarded


_guard_scvars_unraisables()


import telemffb.globals as G
import telemffb.utils as utils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.base.DeadzoneMixIn import DeadzoneMixIn
from telemffb.telem.TelemManager import TelemManager



# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class RaisingOnTimeoutAircraft:
    """Aircraft stand-in whose on_timeout() blows up, as the 13:55:41 log
    did (deadzone override write to a device that was never opened)."""
    _telem_data = {"src": "dc"}

    def on_timeout(self):
        raise RuntimeError("simulated device-touching crash in on_timeout")


class _FakeSettings:
    def get(self, key, default=None):
        # run() re-reads this to compute its timeout; a small value keeps
        # the loop tests deterministic (the ctor value alone is not enough).
        if key == "telemTimeout":
            return 10  # ms
        return default


def _make_manager(monkeypatch):
    """A TelemManager not started as a thread, with the globals its run()
    loop touches stubbed out."""
    monkeypatch.setattr(G, "system_settings", _FakeSettings(), raising=False)
    monkeypatch.setattr(G, "settings_mgr", types.SimpleNamespace(timed_out=False), raising=False)
    monkeypatch.setattr(G, "ipc_instance", None, raising=False)
    tm = TelemManager()
    tm.timeout_sec = 0.01  # fast timeout for the loop tests
    return tm


def _stop(tm):
    with tm._cond:
        tm._run = False
        tm._cond.notify_all()


@pytest.mark.unit
class TestTelemManagerRunSurvivesExceptions:
    def test_run_survives_raising_on_timeout(self, monkeypatch, caplog):
        """The 13:55:41 scenario: on_timeout() raises, the loop must keep
        running and arm the process-check deadline instead of dying."""
        tm = _make_manager(monkeypatch)
        tm.currentAircraft = RaisingOnTimeoutAircraft()
        # A fresh manager inherits the class default timed_out=True; the
        # real flow flips it in reset_sim_connected() when listeners start.
        tm.timed_out = False

        with caplog.at_level(logging.ERROR):
            worker = threading.Thread(target=tm.run, daemon=True)
            worker.start()
            time.sleep(0.1)  # several 10ms timeouts have now fired
            _stop(tm)
        worker.join(5)
        assert not worker.is_alive(), "TelemManager thread must not die"
        # the aircraft hook failure was logged (once per timeout episode,
        # not once per timeout cycle)
        assert any("Aircraft on_timeout failed for dc" in r.message for r in caplog.records)
        assert sum(1 for r in caplog.records if "Aircraft on_timeout failed" in r.message) == 1
        # the loop continued past the failure: process-check deadline armed
        assert tm._process_check_deadline is not None
        # the timed_out transition still happened (no per-cycle retry storm)
        assert tm.timed_out is True

    def test_run_survives_raising_process_data(self, monkeypatch, caplog):
        tm = _make_manager(monkeypatch)

        def boom(data):
            raise RuntimeError("simulated per-frame crash in process_data")
        monkeypatch.setattr(tm, "process_data", boom)

        with caplog.at_level(logging.ERROR):
            with tm._cond:
                tm._data = "dummy;frame"
                tm._cond.notify_all()
            worker = threading.Thread(target=tm.run, daemon=True)
            worker.start()
            time.sleep(0.1)
            _stop(tm)
        worker.join(5)
        assert not worker.is_alive(), "TelemManager thread must not die"
        assert any("TelemManager process_data failed" in r.message for r in caplog.records)
        assert any("simulated per-frame crash in process_data" in r.getMessage()
                   for r in caplog.records)

    def test_run_survives_raising_process_events(self, monkeypatch, caplog):
        tm = _make_manager(monkeypatch)

        def boom():
            raise RuntimeError("simulated per-event crash in process_events")
        monkeypatch.setattr(tm, "process_events", boom)

        with caplog.at_level(logging.ERROR):
            with tm._cond:
                tm._events.append("sim;button;0")
                tm._cond.notify_all()
            worker = threading.Thread(target=tm.run, daemon=True)
            worker.start()
            time.sleep(0.1)
            _stop(tm)
        worker.join(5)
        assert not worker.is_alive(), "TelemManager thread must not die"
        assert any("TelemManager process_events failed" in r.message for r in caplog.records)

    def test_steady_state_still_processes_data(self, monkeypatch):
        """No exception: the data branch must deliver frames to the aircraft
        exactly as before (the safe-call wrapper changes no behaviour)."""
        tm = _make_manager(monkeypatch)
        received = []

        class _A:
            _telem_data = {"src": "unknown"}

            def on_telemetry(self, data):
                received.append(data)

        tm.currentAircraft = _A()

        def fake_process_data(data):
            tm.currentAircraft.on_telemetry(data)
        monkeypatch.setattr(tm, "process_data", fake_process_data)

        with tm._cond:
            tm._data = "frame1"
            tm._cond.notify_all()
        worker = threading.Thread(target=tm.run, daemon=True)
        worker.start()
        time.sleep(0.1)
        _stop(tm)
        worker.join(5)
        assert received == ["frame1"]


# ---------------------------------------------------------------------------
# Per-frame device helpers (zombie startup / hot-unplug)
# ---------------------------------------------------------------------------

class _DeadDevice:
    """FFBRhino stand-in: object present but hot-unplugged."""
    connected = False

    def get_input(self):
        return object()

    def set_deadzone(self, dz):
        self.deadzone_set = dz


@pytest.mark.unit
class TestDeviceHelpersOffline:
    def setup_method(self):
        self._saved_device = HapticEffect.device
        HapticEffect.device = None

    def teardown_method(self):
        HapticEffect.device = self._saved_device

    def test_get_device_input_zombie(self):
        assert HapticEffect.get_device_input() is None

    def test_get_device_input_dead(self):
        HapticEffect.device = _DeadDevice()
        assert HapticEffect.get_device_input() is None

    def test_get_device_input_alive(self):
        dev = MagicMock()
        dev.connected = True
        HapticEffect.device = dev
        assert HapticEffect.get_device_input() is dev.get_input.return_value

    def test_deadzone_on_timeout_zombie(self):
        """The on_timeout straggler: deadzone clear with no device must not
        raise and must reset the in-memory state."""
        class _M(DeadzoneMixIn):
            pass

        m = _M()
        m.deadzone_active = True
        m.active_deadzone_pct = 0.05
        m.on_timeout()  # must not raise
        assert m.deadzone_active is False
        assert m.active_deadzone_pct_override == 0.0

    def test_deadzone_update_defers_when_dead_and_retries_when_alive(self):
        class _M(DeadzoneMixIn):
            pass

        m = _M()
        m.enable_deadzone = True
        m.deadzone_base_pct = 0.05

        # device absent: the write is deferred, bookkeeping stays pending
        m.ac_update_deadzone()
        assert m.active_deadzone_pct == 0.0
        assert m.deadzone_active is False

        # device returns: the same frame logic writes and commits
        dev = MagicMock()
        dev.connected = True
        HapticEffect.device = dev
        m.ac_update_deadzone()
        dev.set_deadzone.assert_called_once()
        assert m.active_deadzone_pct == 0.05
        assert m.deadzone_active is True

        # steady state: no further writes
        dev.set_deadzone.reset_mock()
        m.ac_update_deadzone()
        dev.set_deadzone.assert_not_called()
