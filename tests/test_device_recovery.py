"""Device recovery tests (device-state-handling plan, Task 4).

Two stuck states from the 2026-08-29 field log must self-heal:

1. Hot-unplugged mid-run: the old reconnect loop reopened the *saved USB
   path* every second - which is exactly the string that stopped existing
   when the board was replugged - so it never recovered, and it emitted
   deviceConnected(False) on every tick, spamming the UI.  These tests
   cover re-enumeration on reconnect (fresh path, known-serial preference),
   the 1/3/9/27/30 s exponential backoff, transition-only signal emission,
   and the once-per-recovery on_reconnected hook.

2. Absent at startup (zombie state): the app used to run forever without
   FFB until manually restarted.  main.py now watches for the configured
   PID and opens it when it appears (watcher tests; Windows-only because
   main.py imports winreg).
"""
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest
from PyQt6.QtCore import QObject
from types import SimpleNamespace

# Mock the hid module before importing ffb_rhino (same as test_ffb_rhino)
sys.modules.setdefault('telemffb.hw.hid', MagicMock())

import telemffb.hw.ffb_rhino as ffb_rhino_module
from telemffb.hw.ffb_rhino import (
    FFBRhino,
    HapticEffect,
    DeviceInfo,
    HIDDisconnectedError,
    RECONNECT_BACKOFF_S,
    _next_reconnect_delay,
)

try:
    import main as main_module
except Exception:  # winreg is Windows-only
    main_module = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _info(path, serial="SERIAL1"):
    return DeviceInfo(
        interface_number=0,
        manufacturer_string="VPforce",
        path=path,
        product_id=0x2055,
        product_string="Rhino FFB Test Device",
        release_number=516,
        serial_number=serial,
        usage=4,
        usage_page=1,
        vendor_id=0xFFFF,
    )


class MockHIDDevice:
    """Minimal HID handle stand-in (write/read never raise here)."""

    def __init__(self, path=b"mock_path"):
        self.path = path
        self.serial = "TEST123456"
        self.product = "Rhino FFB Joystick"
        self.manufacturer = "VPforce"
        self.nonblocking = False
        self._closed = False

    def close(self):
        self._closed = True


def _make_rhino(info, dev=None):
    """An FFBRhino with the recovery bookkeeping, no live Qt timer."""
    r = FFBRhino.__new__(FFBRhino)
    r.vid = 0xFFFF
    r.pid = 0x2055
    r.info = info
    r.firmware_version = ""
    r._button_state = 0
    r._prev_hats = 0xFFFF
    r._in_reports = {}
    r._effect_handles = []
    r._dev = dev if dev is not None else MockHIDDevice()
    r._reconnect_attempts = 0
    r._reconnect_pending = False
    r._last_connected = None
    r.on_reconnected = None
    QObject.__init__(r)
    return r


class _ShotRecorder:
    """Stands in for QTimer: records singleShot(ms, fn) calls."""

    def __init__(self):
        self.shots = []

    def singleShot(self, ms, fn):
        self.shots.append((ms, fn))


@pytest.fixture
def shots(monkeypatch):
    rec = _ShotRecorder()
    monkeypatch.setattr(ffb_rhino_module, "QTimer", rec)
    return rec


@pytest.fixture
def rhino(monkeypatch):
    """A connected FFBRhino whose reconnect() re-enumerates."""
    dev = MockHIDDevice()
    r = _make_rhino(_info(b"old-path", "SERIAL1"), dev)
    monkeypatch.setattr(
        FFBRhino, "enumerate",
        staticmethod(lambda pid=0: [_info(b"fresh-path", "SERIAL1")]),
    )
    monkeypatch.setattr(
        ffb_rhino_module.hid, "Device",
        lambda path: MockHIDDevice(path),
    )
    return r


# ---------------------------------------------------------------------------
# reconnect()
# ---------------------------------------------------------------------------

class TestReconnect:

    def test_reenumerates_instead_of_reusing_saved_path(self, rhino, shots):
        """The saved path must never be trusted again: the board replugged
        on another port has a new path, and only enumeration finds it."""
        assert rhino.info.path == b"old-path"
        assert rhino.reconnect()
        assert rhino.info.path == b"fresh-path"
        assert isinstance(rhino._dev, MockHIDDevice)
        assert rhino._dev.path == b"fresh-path"
        assert rhino.connected

    def test_prefers_known_serial(self, monkeypatch, shots):
        dev = MockHIDDevice()
        r = _make_rhino(_info(b"old", "KNOWN"), dev)
        monkeypatch.setattr(
            FFBRhino, "enumerate",
            staticmethod(lambda pid=0: [
                _info(b"other-board", "OTHER"),
                _info(b"known-board", "KNOWN"),
            ]),
        )
        monkeypatch.setattr(ffb_rhino_module.hid, "Device",
                             lambda path: MockHIDDevice(path))
        r.reconnect()
        assert r.info.serial_number == "KNOWN"
        assert r._dev.path == b"known-board"

    def test_no_matching_device_raises(self, monkeypatch, shots):
        r = _make_rhino(_info(b"old"))
        monkeypatch.setattr(FFBRhino, "enumerate", staticmethod(lambda pid=0: []))

        class NoDeviceError(Exception):
            pass
        # hid is a MagicMock in tests, so give it a real exception class
        monkeypatch.setattr(ffb_rhino_module.hid, "HIDException", NoDeviceError)
        with pytest.raises(NoDeviceError):
            r.reconnect()
        assert r._dev is None  # the dead handle was closed before the attempt

    def test_backoff_schedule_caps_at_30s(self):
        assert _next_reconnect_delay(0) == 1
        assert _next_reconnect_delay(1) == 3
        assert _next_reconnect_delay(2) == 9
        assert _next_reconnect_delay(3) == 27
        assert _next_reconnect_delay(4) == 30
        assert _next_reconnect_delay(99) == 30
        assert RECONNECT_BACKOFF_S == (1, 3, 9, 27, 30)

    def test_failed_attempts_follow_1_3_9_27_30(self, rhino, monkeypatch, shots):
        monkeypatch.setattr(
            rhino, "reconnect",
            Mock(side_effect=HIDDisconnectedError("no device")),
        )
        monkeypatch.setattr(
            rhino, "read_reports",
            Mock(side_effect=Exception("read fails, device gone")),
        )

        # the loss is detected by the 1 ms report timer
        rhino.timerEvent(None)
        assert [ms for ms, _ in shots.shots] == [1000]

        for expected_ms in (3000, 9000, 27000, 30000, 30000):
            ms, fn = shots.shots.pop(0)
            fn()  # attempt fails -> schedules the next
            assert [m for m, _ in shots.shots] == [expected_ms],                 f"expected next backoff {expected_ms} ms, got {shots.shots}"

    def test_success_resets_backoff(self, rhino, monkeypatch, shots):
        attempts = {"n": 0}

        def flaky_reconnect():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise HIDDisconnectedError("still gone")
            return True

        monkeypatch.setattr(rhino, "reconnect", flaky_reconnect)
        monkeypatch.setattr(
            rhino, "read_reports",
            Mock(side_effect=Exception("gone")),
        )
        rhino.timerEvent(None)
        ms, fn = shots.shots.pop(0)
        assert ms == 1000
        fn()   # attempt 1 fails -> next delay must be 3 s
        assert [m for m, _ in shots.shots] == [3000]
        ms, fn = shots.shots.pop(0)
        fn()   # attempt 2 succeeds -> backoff resets
        assert rhino._reconnect_attempts == 0
        assert not shots.shots

        # a fresh loss later starts at 1 s again
        shots.shots.clear()
        rhino._reconnect_pending = False
        rhino.timerEvent(None)
        assert [ms for ms, _ in shots.shots] == [1000]


# ---------------------------------------------------------------------------
# signal transitions + recovery hook
# ---------------------------------------------------------------------------

class TestConnectionSignal:

    def test_emits_only_on_transitions(self, rhino):
        emitted = []
        rhino.deviceConnected.connect(lambda b: emitted.append(b))

        rhino._emit_connection_state()   # None -> True
        rhino._emit_connection_state()   # no change
        assert emitted == [True]

        rhino._dev = None                # hot-unplug
        rhino._emit_connection_state()   # True -> False
        rhino._emit_connection_state()   # no change (no per-tick spam)
        rhino._emit_connection_state()   # no change
        assert emitted == [True, False]

        rhino._dev = MockHIDDevice()     # recovery
        rhino._emit_connection_state()
        assert emitted == [True, False, True]

    def test_on_reconnected_called_once_per_recovery(self, rhino, shots, monkeypatch):
        calls = []
        rhino.on_reconnected = lambda: calls.append(1)
        monkeypatch.setattr(rhino, "reconnect",
                            Mock(side_effect=HIDDisconnectedError("gone")))
        monkeypatch.setattr(rhino, "read_reports",
                            Mock(side_effect=Exception("gone")))
        rhino.timerEvent(None)
        ms, fn = shots.shots.pop(0)
        fn()  # failed attempt: no callback
        assert calls == []

        # second failure, then success
        ms, fn = shots.shots.pop(0)
        fn()
        assert calls == []
        monkeypatch.setattr(rhino, "reconnect", Mock(return_value=True))
        ms, fn = shots.shots.pop(0)
        fn()  # success: exactly one callback
        assert calls == [1]

    def test_read_failure_closes_dead_handle_and_emits_once(self, rhino, shots, monkeypatch):
        emitted = []
        rhino.deviceConnected.connect(lambda b: emitted.append(b))
        rhino._last_connected = True  # was connected

        dead = rhino._dev
        monkeypatch.setattr(
            rhino, "read_reports",
            Mock(side_effect=Exception("device vanished")),
        )
        rhino.timerEvent(None)
        rhino.timerEvent(None)  # repeated 1 ms failures: one emission, one shot
        assert emitted == [False]
        assert len(shots.shots) == 1
        assert dead._closed  # the dead handle was closed
        assert rhino._dev is None


# ---------------------------------------------------------------------------
# zombie-state watcher (main.py; Windows-only - main imports winreg)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(main_module is None,
                    reason="main.py requires winreg (Windows-only)")
class TestZombieStateWatcher:

    def _watcher_env(self, monkeypatch):
        fake_he = MagicMock()
        fake_he.device = None
        monkeypatch.setattr(main_module, "HapticEffect", fake_he)
        # annotation-only in globals.py -> raising=False required
        monkeypatch.setattr(main_module.G, "device_usbpid", "2055",
                            raising=False)
        monkeypatch.setattr(main_module.G, "main_window", MagicMock(),
                            raising=False)
        monkeypatch.setattr(main_module.G, "args",
                            SimpleNamespace(reset=False), raising=False)
        async_calls = []
        monkeypatch.setattr(main_module, "_setup_async_initialization",
                            lambda dev, serial: async_calls.append(dev))
        monkeypatch.setattr(main_module, "_check_firmware_version",
                            lambda *a: None)
        return fake_he, async_calls

    def test_absent_device_is_quiet_and_retries(self, monkeypatch):
        fake_he, _ = self._watcher_env(monkeypatch)
        fake_he.open = MagicMock(side_effect=Exception("unable to open device"))
        timer = MagicMock()
        main_module._device_watcher_tick(timer)
        assert fake_he.open.call_count == 1
        assert not timer.stop.called          # keep watching
        assert fake_he.device is None

    def test_appearing_device_opens_once_and_stops(self, monkeypatch):
        fake_he, async_calls = self._watcher_env(monkeypatch)
        dev = MagicMock()
        dev.info = _info(b"fresh-path")
        dev.serial = "SERIAL1"
        dev.get_firmware_version.return_value = "v1.0.19"

        def fake_open(pid):
            fake_he.device = dev
            return dev
        fake_he.open = MagicMock(side_effect=fake_open)

        timer = MagicMock()
        main_module._device_watcher_tick(timer)
        assert fake_he.open.call_count == 1
        assert timer.stop.called              # self-deactivating
        assert fake_he.device is dev
        assert main_module.G.device_connection_status is True
        assert async_calls == [dev]           # phase 14 replayed
        assert dev.on_reconnected is main_module._replay_device_setup

        # a later tick sees the live device and does not re-open
        timer.stop.reset_mock()
        main_module._device_watcher_tick(timer)
        assert fake_he.open.call_count == 1
        assert timer.stop.called

    def test_start_watcher_only_when_device_missing(self, monkeypatch):
        fake_he, _ = self._watcher_env(monkeypatch)
        fake_timer = MagicMock()
        monkeypatch.setattr(main_module, "QTimer", lambda: fake_timer)

        main_module._start_device_watcher()
        assert fake_timer.start.called

        fake_he.device = MagicMock()          # device present
        fake_timer.start.reset_mock()
        main_module._start_device_watcher()
        assert not fake_timer.start.called


class TestWatcherWiring:
    """Static wiring checks that run on every platform.

    main.py cannot be imported off Windows (winreg), but the shipped file
    must still contain the watcher registration points, otherwise a
    Windows-only refactor could silently drop them without any test
    failing here.
    """

    @classmethod
    def _source(cls):
        import pathlib
        p = pathlib.Path(main_module.__file__) if main_module else \
            pathlib.Path(__file__).parent.parent / "main.py"
        return p.read_text()

    def test_phase14_starts_the_watcher(self):
        src = self._source()
        # the phase-14 call (indented), not the def line
        call = "    _start_device_watcher()"
        assert call in src
        assert src.index(call) > src.index("_setup_async_initialization(dev, dev_serial)")

    def test_phase9_success_registers_replay_hook(self):
        src = self._source()
        assert "dev.on_reconnected = _replay_device_setup" in src
        assert src.count("dev.on_reconnected = _replay_device_setup") == 2  # phase 9 + watcher tick

