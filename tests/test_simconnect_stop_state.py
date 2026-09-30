"""The MSFS stop latch: which conditions suppress telemetry, and the log
says which.

While any stop condition is true (paused, parked, slewed, controlling the
avatar, in a realtime cinematic, or in menus), the manager drops every
packet and emits only the transition frame.  Before, a stopped gate
logged nothing, so a stuck stop was indistinguishable from a dead
connection.  The latch and the release now both log, naming the
conditions that latched - which is what lets a genuinely-stuck gate be
found.  FS2024 is the tricky case: during a menu cinematic it reports
Pause=0 while IS IN RTC stays 1, so the gate correctly stays closed but
the log alone looks like an unpause with no matching resume.
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
    SimVar("Parked", "PLANE IN PARKING STATE", "Bool"),
    SimVar("Slew", "IS SLEW ACTIVE", "Bool"),
    SimVar("CameraState", "CAMERA STATE", "Enum"),
    SimVar("_IS AVATAR", "IS AVATAR", "bool"),
    SimVar("_IS IN RTC", "IS IN RTC", "bool"),
]


def make_manager():
    m = RecordingManager()
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
    values = {"Parked": 0, "Slew": 0, "CameraState": 2,
              "_IS AVATAR": 0, "_IS IN RTC": 0}
    values.update(over)
    return values


class TestStopLatch:
    def test_parked_latch_is_logged_and_the_following_packets_are_dropped(
            self, caplog):
        import logging as _logging
        m = make_manager()
        with caplog.at_level(_logging.INFO):
            assert m._handle_recv(make_packet(m, flying_frame(Parked=1)))
            assert m._handle_recv(make_packet(m, flying_frame(Parked=1)))
        stopped = [r.getMessage() for r in caplog.records
                   if "MSFS telemetry stopped" in r.getMessage()]
        assert stopped == ["MSFS telemetry stopped (parked): packets are "
                           "suppressed until all of these clear"], stopped
        # one transition frame only: the second parked frame was dropped
        assert len(m.packets) == 1
        assert m.packets[0]["STOP"] == 1
        assert m.events == [("STOP",)]
        assert m._stop_state

    def test_releasing_the_condition_resumes_and_says_so(self, caplog):
        import logging as _logging
        m = make_manager()
        with caplog.at_level(_logging.INFO):
            m._handle_recv(make_packet(m, flying_frame(Parked=1)))
            m._handle_recv(make_packet(m, flying_frame(Parked=0)))
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
                "Parked": 1, "Slew": 1, "CameraState": 7,
                "_IS AVATAR": 1, "_IS IN RTC": 1})))
        msg = [r.getMessage() for r in caplog.records
               if "MSFS telemetry stopped" in r.getMessage()][0]
        assert "paused" in msg
        assert "parked" in msg
        assert "slew" in msg
        assert "in_menus (camera state 7)" in msg
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
