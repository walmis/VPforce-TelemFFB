"""The MSFS stop latch: which conditions suppress telemetry, and the log
says which.

While any stop condition is true the manager drops every packet and
emits only the transition frame; the latch and the release both log,
naming the conditions.  FS2024 stops on paused, slew, avatar, rtc or no
MOTION SIMULATION; FS2020 (no such variable) on paused, slew or hangar
(InHangar = PLANE IN PARKING STATE, its menu flag).
"""
import ctypes

import pytest

from simconnect import RECV_SIMOBJECT_DATA
from telemffb.telem.SimConnectManager import SimConnectManager, SimVar

pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


class FakeSDK:
    def __getattr__(self, name):
        return lambda *args, **kwargs: 0


class RecordingManager(SimConnectManager):
    def __init__(self):
        super().__init__()
        self.packets = []
        self.events = []

    def emit_packet(self, data):
        self.packets.append(dict(data))

    def emit_event(self, event, *args):
        self.events.append((event, *args))


#: the variables the stop check consults, exactly as the manager declares
#: them (SimConnectManager.sim_vars); the packet carries them in this order
STOP_VARS = [
    SimVar("InHangar", "PLANE IN PARKING STATE", "Bool"),
    SimVar("Slew", "IS SLEW ACTIVE", "Bool"),
    SimVar("_IS AVATAR", "IS AVATAR", "bool"),
    SimVar("_IS IN RTC", "IS IN RTC", "bool"),
    SimVar("_MOTION SIMULATION", "MOTION SIMULATION", "bool"),
]


def make_manager(version=None):
    m = RecordingManager()
    m.connected_version = version
    m.sc = FakeSDK()
    m.subscribed_vars = list(STOP_VARS)
    return m


def _pack(field_type, value):
    """Raw bytes of one ctypes field, exactly as the reader's
    ``cast(byref(recv, off), POINTER(c_type))[0]`` would consume them."""
    arr = (field_type * 1)(value)
    return ctypes.string_at(ctypes.addressof(arr), ctypes.sizeof(arr))


def make_packet(m, values):
    """One RECV_SIMOBJECT_DATA in the fork's in-struct layout: a DWORD
    define index per variable, then its c_type-sized value, starting at
    the dwData field."""
    items = b""
    for i, var in enumerate(m.subscribed_vars):
        items += _pack(ctypes.c_uint, i)
        items += _pack(var.c_type, int(values.get(var.name, 0)))
    raw = bytearray(ctypes.sizeof(RECV_SIMOBJECT_DATA) + len(items))
    recv = RECV_SIMOBJECT_DATA.from_buffer(raw)
    recv.dwSize = ctypes.sizeof(RECV_SIMOBJECT_DATA)
    recv.dwRequestID = m.req_id
    recv.dwDefineID = m.def_id
    recv.dwDefineCount = len(m.subscribed_vars)
    data_off = RECV_SIMOBJECT_DATA.dwData.offset
    raw[data_off:data_off + len(items)] = items
    return recv


def flying_frame(**over):
    """A normal in-flight frame: nothing that would stop telemetry."""
    values = {"InHangar": 0, "Slew": 0,
              "_IS AVATAR": 0, "_IS IN RTC": 0, "_MOTION SIMULATION": 1}
    values.update(over)
    return values


class TestStopLatch:
    def test_hangar_latch_is_logged_and_the_following_packets_are_dropped(
            self, caplog):
        import logging as _logging
        m = make_manager()
        with caplog.at_level(_logging.INFO):
            assert m._handle_recv(make_packet(m, flying_frame(InHangar=1)))
            assert m._handle_recv(make_packet(m, flying_frame(InHangar=1)))
        stopped = [r.getMessage() for r in caplog.records
                   if "MSFS telemetry stopped" in r.getMessage()]
        assert stopped == ["MSFS telemetry stopped (hangar): packets are "
                           "suppressed until all of these clear"], stopped
        # one transition frame only: the second hangar frame was dropped
        assert len(m.packets) == 1
        assert m.packets[0]["STOP"] == 1
        assert m.events == [("STOP",)]
        assert m._stop_state

    def test_releasing_the_condition_resumes_and_says_so(self, caplog):
        import logging as _logging
        m = make_manager()
        with caplog.at_level(_logging.INFO):
            m._handle_recv(make_packet(m, flying_frame(InHangar=1)))
            m._handle_recv(make_packet(m, flying_frame(InHangar=0)))
        resumed = [r.getMessage() for r in caplog.records
                   if "MSFS telemetry resumed" in r.getMessage()]
        assert resumed == ["MSFS telemetry resumed: stop condition cleared"]
        assert len(m.packets) == 2
        assert "STOP" not in m.packets[1]
        assert not m._stop_state

    def test_a_flying_frame_says_nothing(self, caplog):
        import logging as _logging
        m = make_manager()
        with caplog.at_level(_logging.INFO):
            m._handle_recv(make_packet(m, flying_frame()))
        assert not [r for r in caplog.records
                    if "MSFS telemetry" in r.getMessage()]
        assert len(m.packets) == 1

    def test_every_latched_condition_is_named(self, caplog):
        import logging as _logging
        m = make_manager()
        m._sim_paused = 1
        with caplog.at_level(_logging.INFO):
            m._handle_recv(make_packet(m, flying_frame(**{
                "InHangar": 1, "Slew": 1,
                "_IS AVATAR": 1, "_IS IN RTC": 1})))
        msg = [r.getMessage() for r in caplog.records
               if "MSFS telemetry stopped" in r.getMessage()][0]
        assert "paused" in msg
        assert "hangar" in msg
        assert "slew" in msg
        assert "avatar" in msg
        assert "rtc" in msg

    def test_the_avatar_and_rtc_flags_are_checked(self, caplog):
        """2024-only conditions: a handoff to the avatar or a cut scene
        stops telemetry just like a pause."""
        import logging as _logging
        m = make_manager()
        with caplog.at_level(_logging.INFO):
            m._handle_recv(make_packet(m, flying_frame(**{"_IS AVATAR": 1})))
        msg = [r.getMessage() for r in caplog.records
               if "MSFS telemetry stopped" in r.getMessage()][0]
        assert "avatar" in msg and "rtc" not in msg

    def test_fs2020_ignores_the_motion_simulation(self):
        # the variable does not exist on FS2020 and reads as 0
        m = make_manager("MSFS2020")
        m._handle_recv(make_packet(m, flying_frame(**{"_MOTION SIMULATION": 0})))
        assert "STOP" not in m.packets[0]


class TestFS2024MotionGate:
    """FS2024 stops on paused, slew, avatar, rtc, or no MOTION SIMULATION."""

    def _frame(self, **over):
        m = make_manager("MSFS2024")
        m._handle_recv(make_packet(m, flying_frame(**over)))
        return m

    def test_flows_while_the_motion_simulation_runs(self):
        m = self._frame()
        assert "STOP" not in m.packets[0]

    def test_no_motion_stops(self):
        m = self._frame(**{"_MOTION SIMULATION": 0})
        assert m.packets[0]["STOP"] == 1
        assert m._stop_reasons == ("no_motion",)

    def test_hangar_does_not_stop_fs2024(self):
        m = self._frame(InHangar=1)
        assert "STOP" not in m.packets[0]

    def test_walkaround_and_slew_stop_while_the_motion_simulation_runs(self):
        for frame, reason in (({"_IS AVATAR": 1}, "avatar"), ({"Slew": 1}, "slew")):
            m = self._frame(**frame)
            assert m.packets[0]["STOP"] == 1
            assert m._stop_reasons == (reason,)
