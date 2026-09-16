"""Input events (MSFS "B:" variables) through SimConnectManager.

The sim addresses these by a per-aircraft hash, so the manager keeps them
out of the data definition, enumerates the aircraft's events, subscribes
by hash, merges the values into every frame, and writes through the same
table.  Everything here drives the manager with fabricated SimConnect
records and a recording stand-in for the SDK; no sim is involved.
"""
import struct
from ctypes import POINTER, cast, create_string_buffer

import pytest

from simconnect import scdefs
from simconnect.receiver import ReceiverInstance

from telemffb.telem.SimConnectManager import SimConnectManager, SimVar, is_input_event

pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


class FakeSDK:
    """Records every SimConnect call the manager makes."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            return 0
        return call

    def named(self, name):
        return [args for n, args in self.calls if n == name]

    def clear(self):
        self.calls.clear()


def _recv(recv_id, body):
    size = 12 + len(body)
    buf = create_string_buffer(struct.pack("<III", size, 0, recv_id) + body, size)
    recv = ReceiverInstance.cast_recv(cast(buf, POINTER(scdefs.RECV)))
    recv._keepalive = buf
    return recv


def enumeration(req_id, entries, entry=0, outof=1):
    body = struct.pack("<IIII", req_id, len(entries), entry, outof)
    for name, h, t in entries:
        body += name.encode().ljust(64, b"\0") + struct.pack("<QI", h, t)
    return _recv(scdefs.RECV_ID_ENUMERATE_INPUT_EVENTS, body)


def subscribe_value(h, value):
    body = struct.pack("<QI", h, scdefs.INPUT_EVENT_TYPE_DOUBLE) + struct.pack("<d", value)
    return _recv(scdefs.RECV_ID_SUBSCRIBE_INPUT_EVENT, body)


def get_value(req_id, value):
    body = struct.pack("<II", req_id, scdefs.INPUT_EVENT_TYPE_DOUBLE) + struct.pack("<d", value)
    return _recv(scdefs.RECV_ID_GET_INPUT_EVENT, body)


class Manager(SimConnectManager):
    """The manager with its frame output captured instead of emitted."""

    def __init__(self):
        super().__init__()
        self.sc = FakeSDK()
        self.packets = []
        self.events = []

    def emit_packet(self, data):
        self.packets.append(dict(data))

    def emit_event(self, event, *args):
        self.events.append((event, args))


def make_manager(*extra_vars):
    m = Manager()
    m.sim_vars = [SimVar("T", "ABSOLUTE TIME", "Seconds"),
                  SimVar("N", "TITLE", "", datatype=scdefs.DATATYPE_STRING128)]
    for sv in extra_vars:
        m.add_simvar(*sv)
    m._subscribe()
    return m


def test_prefix_detection_is_case_insensitive_and_exact():
    assert is_input_event("B:AUTOPILOT_Master")
    assert is_input_event("b:AUTOPILOT_Master")
    assert not is_input_event("L:AUTOPILOT_Master")
    assert not is_input_event("AUTOPILOT MASTER")
    assert not is_input_event(None)


def test_b_vars_stay_out_of_the_data_definition_and_trigger_enumeration():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    defined = [args[1] for args in m.sc.named("AddToDataDefinition")]
    assert "B:AUTOPILOT_Master" not in defined
    assert defined == ["ABSOLUTE TIME", "TITLE"]
    assert [sv.name for sv in m.subscribed_vars] == ["T", "N"]
    assert m.sv_dict["APMaster"] == "B:AUTOPILOT_Master"
    assert len(m.sc.named("EnumerateInputEvents")) == 1
    assert m._b_enum_req is not None


def test_enumeration_subscribes_by_hash_and_reads_the_starting_value():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    req = m._b_enum_req
    m._handle_recv(enumeration(req, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE),
                                     ("Other", 0x9999, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    assert m.sc.named("SubscribeInputEvent") == [(0x1234,)]
    gets = m.sc.named("GetInputEvent")
    assert len(gets) == 1 and gets[0][1] == 0x1234
    assert m._b_hash_to_var[0x1234].name == "APMaster"
    assert m._b_enum_req is None


def test_a_chunked_enumeration_is_gathered_before_resolving():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    req = m._b_enum_req
    m._handle_recv(enumeration(req, [("Other", 0x1, scdefs.INPUT_EVENT_TYPE_DOUBLE)], entry=0, outof=2))
    assert m.sc.named("SubscribeInputEvent") == []
    m._handle_recv(enumeration(req, [("AUTOPILOT_Master", 0x2, scdefs.INPUT_EVENT_TYPE_DOUBLE)], entry=1, outof=2))
    assert m.sc.named("SubscribeInputEvent") == [(0x2,)]
    assert set(m._input_events) == {"Other", "AUTOPILOT_Master"}


def test_an_enumeration_for_another_request_is_ignored():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    m._handle_recv(enumeration(m._b_enum_req + 1, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    assert m.sc.named("SubscribeInputEvent") == []
    assert m._b_enum_req is not None


def test_values_arrive_under_the_simvar_name_with_scaling_applied():
    m = make_manager(("Flaps", "B:FLAPS_Handle", "number", None, scdefs.DATATYPE_FLOAT64, 2.0))
    m._handle_recv(enumeration(m._b_enum_req, [("FLAPS_Handle", 0x77, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    get_req = list(m._b_get_reqs)[0]
    m._handle_recv(get_value(get_req, 0.25))
    assert m._b_values["Flaps"] == pytest.approx(0.5)
    m._handle_recv(subscribe_value(0x77, 1.0))
    assert m._b_values["Flaps"] == pytest.approx(2.0)
    assert m._b_get_reqs == {}


@pytest.mark.parametrize("scale", ["", "None", " none ", None])
def test_an_empty_or_none_scale_means_no_scaling(scale):
    sv = SimVar("Flaps", "B:FLAPS_Handle", "number", scale=scale)
    assert sv.scale is None
    assert sv._calculate(100.0) == 100.0


def test_a_broken_transform_keeps_the_raw_value_and_the_thread(caplog):
    m = make_manager(("Flaps", "B:FLAPS_Handle", "number", None, scdefs.DATATYPE_FLOAT64, "x +"))
    m._handle_recv(enumeration(m._b_enum_req, [("FLAPS_Handle", 0x77, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    m._handle_recv(subscribe_value(0x77, 100.0))
    m._handle_recv(subscribe_value(0x77, 50.0))
    assert m._b_values["Flaps"] == 50.0
    assert len([r for r in caplog.records if "transform" in r.message]) == 1


def test_a_value_for_an_unknown_hash_is_dropped():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    m._handle_recv(enumeration(m._b_enum_req, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    m._handle_recv(subscribe_value(0x4321, 1.0))
    assert m._b_values == {}


def test_a_name_the_aircraft_lacks_is_reported_once(caplog):
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    m._handle_recv(enumeration(m._b_enum_req, [("Other", 0x1, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    m._resolve_input_events()
    m._resolve_input_events()
    hits = [r for r in caplog.records if "AUTOPILOT_Master" in r.message]
    assert len(hits) == 1
    assert m.sc.named("SubscribeInputEvent") == []


def test_an_empty_answer_is_retried_a_bounded_number_of_times(monkeypatch):
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    now = [1000.0]
    monkeypatch.setattr("telemffb.telem.SimConnectManager.time.time", lambda: now[0])
    for _ in range(3):
        m._handle_recv(enumeration(m._b_enum_req, []))
        assert m._b_enum_req is None and m._b_enum_retry_at is not None
        now[0] += 5
        m._tick_input_events()
        assert m._b_enum_req is not None
    m._handle_recv(enumeration(m._b_enum_req, []))
    assert m._b_enum_retry_at is None
    assert len(m.sc.named("EnumerateInputEvents")) == 4


def test_an_aircraft_change_drops_the_old_hashes_and_enumerates_again():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    m._handle_recv(enumeration(m._b_enum_req, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    m.sc.clear()
    m._note_aircraft_title("Cessna 172")           # the first title owns the table in hand
    assert m.sc.named("EnumerateInputEvents") == []
    m._note_aircraft_title("PMDG 737")
    assert m.sc.named("UnsubscribeInputEvent") == [(0x1234,)]
    assert len(m.sc.named("EnumerateInputEvents")) == 1
    assert m._b_hash_to_var == {} and m._input_events == {}
    m._note_aircraft_title("PMDG 737")
    assert len(m.sc.named("EnumerateInputEvents")) == 1


def test_an_unanswered_enumeration_is_given_up_and_retried_a_bounded_number_of_times(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("telemffb.telem.SimConnectManager.time.time", lambda: now[0])
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    first_req = m._b_enum_req
    now[0] += 3
    m._tick_input_events()
    assert m._b_enum_req == first_req                    # still waiting inside the window
    for _ in range(3):
        now[0] += 6
        m._tick_input_events()                           # given up, retry scheduled
        assert m._b_enum_req is None
        now[0] += 3
        m._tick_input_events()                           # retry sent
        assert m._b_enum_req is not None
    now[0] += 6
    m._tick_input_events()
    assert m._b_enum_req is None and m._b_enum_retry_at is None
    assert len(m.sc.named("EnumerateInputEvents")) == 4
    m.sc.clear()
    m._note_aircraft_title("PMDG 737")                   # an aircraft change can ask again
    assert len(m.sc.named("EnumerateInputEvents")) == 1


def test_the_first_title_after_a_menu_enumeration_asks_again():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    for _ in range(4):                                  # the menu answers empty until the budget is spent
        m._handle_recv(enumeration(m._b_enum_req, []))
        m._b_enum_retry_at = None
        m._request_input_event_enumeration()
    m._handle_recv(enumeration(m._b_enum_req, []))
    assert m._b_enum_req is None and m._input_events == {}
    m.sc.clear()
    m._note_aircraft_title("Cessna 172")
    assert len(m.sc.named("EnumerateInputEvents")) == 1


def test_a_reconnect_forgets_the_table_and_starts_over():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    m._handle_recv(enumeration(m._b_enum_req, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    m._handle_recv(subscribe_value(0x1234, 1.0))
    m.sc.clear()
    m._reset_input_events()
    assert m._input_events == {} and m._b_hash_to_var == {} and m._b_values == {}
    assert len(m.sc.named("EnumerateInputEvents")) == 1


def test_dropping_a_b_var_unsubscribes_it_and_removes_its_value():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    m._handle_recv(enumeration(m._b_enum_req, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    m._handle_recv(subscribe_value(0x1234, 1.0))
    m.sc.clear()
    m.temp_sim_vars.clear()
    m._subscribe()
    assert m.sc.named("UnsubscribeInputEvent") == [(0x1234,)]
    assert m._b_values == {} and m._b_vars == []


def test_a_numeric_write_goes_out_as_an_eight_byte_double():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    m._handle_recv(enumeration(m._b_enum_req, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    m.send_event_to_msfs("B:AUTOPILOT_Master", 1)
    m.tx_events_to_msfs()
    sets = m.sc.named("SetInputEvent")
    assert len(sets) == 1
    assert sets[0][0] == 0x1234 and sets[0][1] == 8
    assert m.sc.named("TransmitClientEvent") == []


def test_a_string_write_is_sent_terminated():
    m = make_manager(("Scratch", "B:FMS_Scratchpad", "string"))
    m._handle_recv(enumeration(m._b_enum_req, [("FMS_Scratchpad", 0x55, scdefs.INPUT_EVENT_TYPE_STRING)]))
    m.set_simdatum_to_msfs("B:FMS_Scratchpad", "KJFK")
    m.tx_simdatums_to_msfs()
    sets = m.sc.named("SetInputEvent")
    assert sets[0][0] == 0x55 and sets[0][1] == len("KJFK") + 1


def test_a_write_before_enumeration_waits_for_the_hash():
    m = make_manager()
    assert m.sc.named("EnumerateInputEvents") == []
    m.send_event_to_msfs("B:AUTOPILOT_Master", 1)
    m.tx_events_to_msfs()
    assert m.sc.named("SetInputEvent") == []
    assert len(m.sc.named("EnumerateInputEvents")) == 1
    m._handle_recv(enumeration(m._b_enum_req, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    sets = m.sc.named("SetInputEvent")
    assert len(sets) == 1 and sets[0][0] == 0x1234
    assert m._b_pending_writes == {}


def test_l_var_and_plain_event_writes_keep_their_old_paths():
    m = make_manager()
    m.send_event_to_msfs("ROTOR_TRIM_RESET", 1)
    m.send_event_to_msfs("L:FFB_HANDS_ON", 1)
    m.tx_events_to_msfs()
    m.tx_simdatums_to_msfs()
    assert m.sc.named("send_event") == [("ROTOR_TRIM_RESET", 1)]
    assert [args[0] for args in m.sc.named("set_simdatum")] == ["L:FFB_HANDS_ON"]
    assert m.sc.named("SetInputEvent") == []


def test_frames_carry_the_latest_input_event_values():
    m = make_manager(("APMaster", "B:AUTOPILOT_Master", "number"))
    m._handle_recv(enumeration(m._b_enum_req, [("AUTOPILOT_Master", 0x1234, scdefs.INPUT_EVENT_TYPE_DOUBLE)]))
    m._handle_recv(subscribe_value(0x1234, 1.0))
    # a SIMOBJECT_DATA frame with the two defined datums: T (double) and N (string128)
    body = struct.pack("<IIIIIII", m.req_id, 0, m.def_id, 0, 1, 1, 2)
    body += struct.pack("<I", 0) + struct.pack("<d", 12.5)
    body += struct.pack("<I", 1) + b"Cessna".ljust(128, b"\0")
    m._handle_recv(_recv(scdefs.RECV_ID_SIMOBJECT_DATA, body))
    assert m.packets and m.packets[-1]["APMaster"] == pytest.approx(1.0)
    assert m.packets[-1]["N"] == "Cessna"
    assert m._last_title == "Cessna"
