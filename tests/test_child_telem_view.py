"""A child instance's telemetry view in the master's Monitor tab, over IPC.

    master -> children   VIEW TELEM:<device>     (that child sends; others stop)
    child  -> master     view:<device>:<seq>:<json frame>

The frames are large and frequent next to everything else on the socket, so
what matters is who sends and when - only the child being watched, only while
it is - and that the master's count of lost frames can be believed: it is the
evidence for whether UDP is good enough for this.
"""
import time

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
from telemffb.ChildTelemView import STALE_SEC, ChildTelemView
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from tests.test_preview_ipc import _thread

pytestmark = [pytest.mark.unit,
              pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")]

MASTER = ('127.0.0.1', 1)


@pytest.fixture
def child(monkeypatch):
    monkeypatch.setattr(G, 'device_type', 'pedals', raising=False)
    ipc, fake = _thread(monkeypatch, dstport=40000)
    return ipc, fake.sent


@pytest.fixture
def master(monkeypatch):
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    ipc, fake = _thread(monkeypatch)
    ipc._child_addrs = {'pedals': ('127.0.0.1', 41001), 'collective': ('127.0.0.1', 41002)}
    return ipc, fake.sent


def _frame(**values):
    return BaseTelemetryData({'N': 'Bell 407', 'TAS': 61.5, 'ACCs': [0.1, -0.2, 1.0], **values})


class TestWhoSends:
    def test_a_child_nobody_asked_sends_nothing(self, child):
        ipc, sent = child
        ipc.send_ipc_view(_frame())
        assert sent == []

    def test_the_child_asked_sends_and_the_master_shows_what_it_sent(self, master, child):
        ipc, sent = child                 # requested last: G.device_type is the child's
        ipc._handle_message("VIEW TELEM:pedals", MASTER)
        ipc.send_ipc_view(_frame())

        (message, _), = sent
        m_ipc, _ = master
        m_ipc._handle_message(message, ('127.0.0.1', 41001))
        shown = m_ipc.child_view.frame('pedals')
        assert (shown['N'], shown['TAS'], shown['ACCs']) == ('Bell 407', 61.5, [0.1, -0.2, 1.0])

    def test_asking_another_child_stops_this_one(self, child):
        ipc, sent = child
        ipc._handle_message("VIEW TELEM:pedals", MASTER)
        ipc._handle_message("VIEW TELEM:collective", MASTER)
        ipc.send_ipc_view(_frame())
        assert sent == []

    def test_a_master_that_stops_asking_stops_being_sent_to(self, child):
        """The request is a lease the master renews each keepalive tick, so a
        master that crashed scoped to this child is not sent to forever."""
        ipc, sent = child
        ipc._handle_message("VIEW TELEM:pedals", MASTER)
        ipc._view_lease_until = time.monotonic() - 0.01
        ipc.send_ipc_view(_frame())
        assert sent == []

    def test_a_value_json_cannot_carry_does_not_stop_the_view(self, child):
        ipc, sent = child
        ipc._handle_message("VIEW TELEM:pedals", MASTER)
        ipc.send_ipc_view(_frame(odd=object()))
        assert len(sent) == 1


class TestMasterAsks:
    def test_at_once_and_again_on_each_keepalive(self, master):
        ipc, sent = master
        ipc.request_child_view('pedals')
        assert {m for m, _ in sent} == {"VIEW TELEM:pedals"} and len(sent) == 2  # both children hear it
        del sent[:]
        ipc._send_keepalive()
        assert "VIEW TELEM:pedals" in {m for m, _ in sent}

    def test_back_to_its_own_device_it_tells_the_child_to_stop_and_stops_asking(self, master):
        ipc, sent = master
        ipc.request_child_view('pedals')
        del sent[:]
        ipc.request_child_view(None)
        assert {m for m, _ in sent} == {"VIEW TELEM:"}
        del sent[:]
        ipc._send_keepalive()
        assert {m for m, _ in sent} == {"Keepalive"}


class TestCounting:
    @pytest.fixture
    def view(self):
        self.now = 100.0
        return ChildTelemView(clock=lambda: self.now)

    def test_a_jump_in_the_sequence_is_that_many_frames_lost(self, view):
        for seq in (1, 2, 5, 6):
            view.accept('pedals', seq, '{}')
        stats = view.stats('pedals')
        assert (stats.received, stats.lost) == (4, 2)

    def test_a_child_that_restarted_lost_nothing(self, view):
        for seq in (41, 42, 1, 2):
            view.accept('pedals', seq, '{}')
        assert view.stats('pedals').lost == 0

    def test_the_rate_is_frames_per_second(self, view):
        for seq in range(1, 42):          # 50 ms apart, for two seconds
            view.accept('pedals', seq, '{}')
            self.now = 100.0 + seq * 0.05
        assert view.stats('pedals').rate == pytest.approx(20, abs=1)

    def test_a_frame_gone_stale_is_not_the_childs_view(self, view):
        view.accept('pedals', 1, '{"TAS": 1.0}')
        assert view.frame('pedals') == {'TAS': 1.0}
        self.now += STALE_SEC + 0.01
        assert view.frame('pedals') is None
        assert view.stats('pedals').rate == 0

    def test_only_the_newest_frame_is_kept(self, view):
        view.accept('pedals', 1, '{"TAS": 1.0}')
        view.accept('pedals', 2, '{"TAS": 2.0}')
        assert view.frame('pedals') == {'TAS': 2.0}
