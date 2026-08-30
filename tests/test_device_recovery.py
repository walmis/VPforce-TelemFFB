"""Device recovery tests (device-state-handling plan, Task 4).

Two stuck states from the 2026-08-29 field log must self-heal:

1. Hot-unplugged mid-run: the old reconnect loop reopened the *saved USB
   path* every second - which is exactly the string that stopped existing
   when the board was replugged - so it never recovered, and it emitted
   deviceConnected(False) on every tick, spamming the UI.  These tests
   cover re-enumeration on reconnect (fresh path, known-serial preference),
   the 1/3/9/27/30 s exponential backoff, transition-only signal emission,
   and the once-per-recovery deviceReconnected signal.

2. Absent at startup (zombie state): the app used to run forever without
   FFB until manually restarted.  main.py's _DeviceRetryTicker (unified
   with the dinput live-switch watcher in the dinput merge) opens the
   configured device when it appears (watcher tests; Windows-only because
   main.py imports winreg).
"""
import sys
import warnings
from unittest.mock import MagicMock, Mock, patch

import pytest
from PyQt6.QtCore import QObject
from types import SimpleNamespace

# Mock the hid module before importing ffb_rhino (same as test_ffb_rhino)
sys.modules.setdefault('telemffb.hw.hid', MagicMock())

import weakref

import telemffb.hw.ffb_rhino as ffb_rhino_module
from telemffb.hw.ffb_rhino import (
    FFBRhino,
    FFBEffectHandle,
    HapticEffect,
    DeviceInfo,
    HIDDisconnectedError,
    RECONNECT_BACKOFF_S,
    _next_reconnect_delay,
    EFFECT_SINE,
    OP_START,
    HID_REPORT_ID_CREATE_EFFECT,
    HID_REPORT_ID_EFFECT_OPERATION,
    HID_REPORT_ID_PID_BLOCK_LOAD,
)

# The pysimconnect fork (requirements.txt pins a rolling master zip)
# loads scvars.json via `json.load(open(...))` and never closes the
# file.  On Windows the `import main` below pulls `simconnect` into
# the test session for the first time, and the unclosed FileIO's
# ResourceWarning becomes an unraisable that pytest attributes to an
# innocent test.  Pre-import it with ResourceWarning ignored for that
# one import - the finalizer fires inside this context (refcounting)
# and dies quietly, and main.py's later `from simconnect import *`
# hits the sys.modules cache.  Proper fix: close the file in the
# fork's scvars.py.
if sys.platform == "win32" and "simconnect" not in sys.modules:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        import simconnect  # noqa: F401

try:    import main as main_module
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
    r._shutdown = False
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

    def test_device_reconnected_emitted_once_per_recovery(self, rhino, shots, monkeypatch):
        calls = []
        rhino.deviceReconnected.connect(lambda: calls.append(1))
        monkeypatch.setattr(rhino, "reconnect",
                            Mock(side_effect=HIDDisconnectedError("gone")))
        monkeypatch.setattr(rhino, "read_reports",
                            Mock(side_effect=Exception("gone")))
        rhino.timerEvent(None)
        ms, fn = shots.shots.pop(0)
        fn()  # failed attempt: no emission
        assert calls == []

        # second failure, then success
        ms, fn = shots.shots.pop(0)
        fn()
        assert calls == []
        monkeypatch.setattr(rhino, "reconnect", Mock(return_value=True))
        ms, fn = shots.shots.pop(0)
        fn()  # success: exactly one emission
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
# power-cycle recovery: the firmware effect pool is empty again
# ---------------------------------------------------------------------------

class TestPowerCycleInvalidatesHandles:
    """A re-plug boots the firmware with an empty effect pool: every
    handle must forget its block, and the next start must re-create the
    effect (CREATE_EFFECT before OP_START) instead of writing to a block
    that no longer exists."""

    class _RecordingHID:
        def __init__(self, path=b"old-path"):
            self.path = path
            self.nonblocking = False
            self._writes = []
            self._feature_reports = {}

        def write(self, data):
            self._writes.append(bytes(data))
            return len(data)

        def send_feature_report(self, data):
            self._feature_reports[data[0]] = bytes(data)

        def get_feature_report(self, report_id, size):
            return self._feature_reports.get(report_id, bytes(size))

        def close(self):
            pass

    def _handle(self, device, effect_id):
        handle = FFBEffectHandle(device, effect_id, EFFECT_SINE)
        device._effect_handles.append(
            weakref.ref(handle,
                        lambda ref: ref in device._effect_handles
                        and device._effect_handles.remove(ref)))
        return handle

    def test_reconnect_invalidates_handles_and_start_recreates(
            self, rhino, monkeypatch):
        # a connected board with one effect running
        old_dev = self._RecordingHID()
        rhino._dev = old_dev
        handle = self._handle(rhino, 3)
        handle.start()
        assert handle._started and handle.effect_id == 3

        # the power cycle: the report timer lost the handle, and the
        # re-enumeration found the re-plugged board
        rhino._dev = None
        new_dev = self._RecordingHID(path=b"fresh-path")
        monkeypatch.setattr(
            rhino, "reconnect",
            Mock(side_effect=lambda: setattr(rhino, "_dev", new_dev) or True))

        emitted = []
        rhino.deviceReconnected.connect(lambda: emitted.append(1))
        rhino._try_reconnect()

        assert emitted == [1]                  # the recovery hook fired
        assert new_dev is rhino._dev           # fresh handle in place
        assert handle.effect_id == 0           # block id forgotten
        assert not handle._started

        # a fresh start must re-create: CREATE_EFFECT, then OP_START on
        # the new block - never an OP_START aimed at the stale one
        effect = HapticEffect()
        effect._h_effect = handle
        effect.periodic(20, 0.5, 0)            # re-arms the pending creation
        new_dev._feature_reports[HID_REPORT_ID_PID_BLOCK_LOAD] = \
            bytes([HID_REPORT_ID_PID_BLOCK_LOAD, 4, 1, 0, 0])
        orig = HapticEffect.device
        HapticEffect.device = rhino
        try:
            effect.start()
        finally:
            HapticEffect.device = orig

        writes = new_dev._writes
        assert HID_REPORT_ID_CREATE_EFFECT in new_dev._feature_reports
        assert any(w[0] == HID_REPORT_ID_EFFECT_OPERATION
                   and w[2] == OP_START for w in writes)
        assert not any(w[0] == HID_REPORT_ID_EFFECT_OPERATION
                       and w[1] == 3 for w in writes)
        assert effect._h_effect is not handle
        assert effect._h_effect.effect_id == 4


# ---------------------------------------------------------------------------
# zombie-state watcher (main.py; Windows-only - main imports winreg)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(main_module is None,
                    reason="main.py requires winreg (Windows-only)")
class TestZombieStateWatcher:
    """The open-failed case (no device object at all) is covered by
    main._DeviceRetryTicker, which the dinput merge made the single
    watcher for both the zombie state and live device switches: while
    the instance has no open device it checks every couple of seconds
    whether the configured device has appeared, and only then runs
    switch_to_device quietly.  The mid-run hot-unplug case is covered
    by the device object's own reconnect loop (tests above)."""

    def _env(self, monkeypatch):
        fake_he = MagicMock()
        fake_he.device = None
        monkeypatch.setattr(main_module, "HapticEffect", fake_he)
        monkeypatch.setattr(main_module.G, "device_type", "joystick",
                            raising=False)
        monkeypatch.setattr(main_module.G, "system_settings", MagicMock(),
                            raising=False)
        monkeypatch.setattr(main_module.G, "device_connection_status",
                            False, raising=False)
        fake_he.G.system_settings.get.return_value = "2055"  # devpath set
        return fake_he

    def _ticker(self):
        ticker = main_module._DeviceRetryTicker()
        timer = MagicMock()
        timer.isActive.return_value = True
        ticker._timer = timer
        return ticker, timer

    def test_absent_device_is_quiet_and_retries(self, monkeypatch):
        self._env(monkeypatch)
        switches = []
        monkeypatch.setattr(main_module, "_configured_device_present",
                            lambda: False)
        monkeypatch.setattr(main_module, "switch_to_device",
                            lambda show_error=True: switches.append(1) or True)
        ticker, timer = self._ticker()
        ticker._tick()
        assert switches == []           # still absent: stay quiet
        assert not timer.stop.called    # keep watching

    def test_appearing_device_switches_once_and_stops(self, monkeypatch):
        self._env(monkeypatch)
        switches = []
        monkeypatch.setattr(main_module, "_configured_device_present",
                            lambda: True)
        monkeypatch.setattr(main_module, "switch_to_device",
                            lambda show_error=True: (switches.append(1), True)[1])
        ticker, timer = self._ticker()
        ticker._tick()
        assert switches == [1]          # opened unattended
        assert timer.stop.called        # self-deactivating

    def test_already_connected_stops_immediately(self, monkeypatch):
        self._env(monkeypatch)
        monkeypatch.setattr(main_module.G, "device_connection_status", True,
                            raising=False)
        ticker, timer = self._ticker()
        ticker._tick()
        assert timer.stop.called


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
        return p.read_text(encoding="utf-8")

    def test_phase9_starts_the_retry_ticker_when_open_fails(self):
        src = self._source()
        block = """    dev, dev_serial, dev_firmware_version = _open_device_and_derive(
        min_firmware_version)
    if dev is None:
        # the configured device may simply not be plugged in yet; connect
        # unattended when it appears
        device_retry_ticker.start()"""
        assert block in src

    def test_phase14_rearms_the_retry_ticker(self):
        src = self._source()
        rearm = "    if dev is None:\n        device_retry_ticker.start()"
        assert rearm in src
        assert src.index(rearm) > src.index("_setup_async_initialization(dev, dev_serial)")

    def test_open_registers_replay_hook_once(self):
        src = self._source()
        assert "dev.deviceReconnected.connect(_replay_device_setup)" in src
        assert src.count("dev.deviceReconnected.connect(_replay_device_setup)") == 1  # shared open path
