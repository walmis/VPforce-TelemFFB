"""Startup readiness gate (main._StartupGate): the sim listeners start once
the version check has resolved and the device is ready, or after the
fallback.  Windows-only because main.py imports winreg.
"""
import sys
import warnings
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('telemffb.hw.hid', MagicMock())

if sys.platform == "win32" and "simconnect" not in sys.modules:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        import simconnect  # noqa: F401

try:    import main as main_module
except Exception:  # winreg is Windows-only
    main_module = None


@pytest.mark.skipif(main_module is None,
                    reason="main.py requires winreg (Windows-only)")
class TestStartupGate:

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        self.starts = []
        monkeypatch.setattr(main_module.G, "sim_listeners",
                            SimpleNamespace(start_all=lambda: self.starts.append(1)),
                            raising=False)
        # timers never fire on their own here; the tests drive the gate
        self.timers = []

        def make_timer():
            t = MagicMock()
            self.timers.append(t)
            return t
        monkeypatch.setattr(main_module, "QTimer", make_timer)

    def _gate(self):
        gate = main_module._StartupGate()
        gate.arm_fallback()          # self.timers[0]
        return gate

    def _dev(self, has_input=True):
        dev = MagicMock()
        dev.get_input.return_value = object() if has_input else None
        return dev

    def test_version_check_alone_does_not_start(self):
        gate = self._gate()
        gate.version_checked()
        assert self.starts == []

    def test_device_alone_does_not_start(self):
        gate = self._gate()
        gate.device_init_done(self._dev())
        assert self.starts == []

    def test_both_conditions_start_once_in_either_order(self):
        gate = self._gate()
        gate.version_checked()
        gate.device_init_done(self._dev())
        assert self.starts == [1]

        gate = self._gate()
        gate.device_init_done(self._dev())
        gate.version_checked()
        assert self.starts == [1, 1]

    def test_no_device_counts_as_ready(self):
        gate = self._gate()
        gate.version_checked()
        gate.device_init_done(None)
        assert self.starts == [1]

    def test_device_with_input_needs_no_poll(self):
        gate = self._gate()
        gate.version_checked()
        gate.device_init_done(self._dev(has_input=True))
        assert self.starts == [1]
        assert len(self.timers) == 1          # the fallback only

    def test_waits_for_the_first_input_snapshot(self):
        gate = self._gate()
        gate.version_checked()
        dev = self._dev(has_input=False)
        gate.device_init_done(dev)
        assert self.starts == []              # polling, not started
        poll = self.timers[1]
        assert poll.start.called

        dev.get_input.return_value = object()  # the read timer delivered it
        gate._check_input(dev)
        assert self.starts == [1]
        assert poll.stop.called

    def test_missing_input_gives_up_at_the_deadline(self, monkeypatch):
        monkeypatch.setattr(main_module._StartupGate, "INPUT_WAIT_MS", 0)
        gate = self._gate()
        gate.version_checked()
        gate.device_init_done(self._dev(has_input=False))
        assert self.starts == [1]

    def test_listeners_start_once_whichever_fires_first(self):
        gate = self._gate()
        gate._fallback_fired()                # fallback first
        gate.version_checked()
        gate.device_init_done(self._dev())
        gate._fallback_fired()
        assert self.starts == [1]

        gate = self._gate()
        gate.version_checked()                # conditions first
        gate.device_init_done(None)
        gate._fallback_fired()
        assert self.starts == [1, 1]

    def test_device_that_raises_on_read_does_not_hold_the_gate(self):
        gate = self._gate()
        gate.version_checked()
        dev = MagicMock()
        dev.get_input.side_effect = RuntimeError("gone")
        gate.device_init_done(dev)
        assert self.starts == [1]


class TestStartupGateWiring:
    """Static checks on main.py (readable on every platform)."""

    @staticmethod
    def _source():
        import pathlib
        p = pathlib.Path(main_module.__file__) if main_module else \
            pathlib.Path(__file__).parent.parent / "main.py"
        return p.read_text(encoding="utf-8")

    def test_version_check_feeds_the_gate_not_the_listeners(self):
        src = self._source()
        assert "version_check_complete.connect(startup_gate.version_checked)" in src
        assert "version_check_complete.connect(G.sim_listeners.start_all)" not in src

    def test_async_device_init_reports_to_the_gate_unconditionally(self):
        src = self._source()
        i = src.index("def _setup_async_initialization(")
        body = src[i:src.index("\ndef ", i + 1)]
        assert "finally:" in body
        assert "startup_gate.device_init_done(dev)" in body

    def test_startup_vpconf_push_is_waited_for(self):
        src = self._source()
        i = src.index("def _init_device_async(")
        body = src[i:src.index("\ndef ", i + 1)]
        assert "wait=True" in body
