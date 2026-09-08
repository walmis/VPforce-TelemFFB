"""Effect preview on a child instance's device, over IPC.

The master's settings form hosts the play button for every device's rows,
but the effect must play on the instance that owns the device.  These pin
the wire protocol both ways and the child's filtering:

    master -> children   PREVIEW:<device>:<name>    (owner runs it)
    master -> children   PREVIEW STOP:<device>
    child  -> master     PREVIEW DONE:<device>:<name>
"""
import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
from telemffb.IPCNetworkThread import IPCNetworkThread

# The thread object opens a UDP socket in its constructor; these tests never
# start the thread, so the fixtures close the socket themselves - an
# abandoned one raises a ResourceWarning at collection that pytest's
# unraisable plugin pins on whichever test is running.
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


class FakeSocket:
    """Stands in for the thread's UDP socket (a real socket's sendto is
    read-only); records what would have gone on the wire."""

    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data.decode(), addr))

    def close(self):
        pass


def _thread(monkeypatch, **kwargs):
    ipc = IPCNetworkThread(**kwargs)
    ipc._socket.close()
    fake = FakeSocket()
    monkeypatch.setattr(ipc, '_socket', fake)
    return ipc, fake


@pytest.fixture
def child(monkeypatch):
    monkeypatch.setattr(G, 'device_type', 'pedals', raising=False)
    ipc, _ = _thread(monkeypatch, dstport=40000)   # a child talks to a master port
    got, stops = [], []
    ipc.preview_signal.connect(got.append)
    ipc.preview_stop_signal.connect(lambda: stops.append(1))
    return ipc, got, stops


@pytest.fixture
def master(monkeypatch):
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    ipc, fake = _thread(monkeypatch)               # no dstport: the master
    done = []
    ipc.preview_done_signal.connect(lambda dev, name: done.append((dev, name)))
    ipc._child_addrs = {'pedals': ('127.0.0.1', 41001), 'collective': ('127.0.0.1', 41002)}
    return ipc, done, fake.sent


class TestChildSide:
    def test_runs_a_preview_addressed_to_its_device(self, child):
        ipc, got, stops = child
        ipc._handle_message("PREVIEW:pedals:enable_stick_shaker", ('127.0.0.1', 1))
        assert got == ['enable_stick_shaker'] and stops == []

    def test_ignores_a_preview_for_another_device(self, child):
        ipc, got, stops = child
        ipc._handle_message("PREVIEW:collective:etl_effect_enable", ('127.0.0.1', 1))
        assert got == []

    def test_name_may_contain_colons(self, child):
        ipc, got, _ = child
        ipc._handle_message("PREVIEW:pedals:odd:name", ('127.0.0.1', 1))
        assert got == ['odd:name']

    def test_stop_is_filtered_by_device_too(self, child):
        ipc, got, stops = child
        ipc._handle_message("PREVIEW STOP:collective", ('127.0.0.1', 1))
        ipc._handle_message("PREVIEW STOP:pedals", ('127.0.0.1', 1))
        assert stops == [1]

    def test_done_report_names_this_device(self, child, monkeypatch):
        ipc, _, _ = child
        sent = []
        monkeypatch.setattr(ipc, 'send_message', sent.append)
        ipc.send_preview_done('enable_stick_shaker')
        assert sent == ['PREVIEW DONE:pedals:enable_stick_shaker']


class TestMasterSide:
    def test_dispatches_to_every_child_and_the_owner_filters(self, master):
        ipc, _, sent = master
        ipc.send_preview('pedals', 'enable_stick_shaker')
        assert sorted(sent) == [('PREVIEW:pedals:enable_stick_shaker', ('127.0.0.1', 41001)),
                                ('PREVIEW:pedals:enable_stick_shaker', ('127.0.0.1', 41002))]

    def test_stop_goes_out_the_same_way(self, master):
        ipc, _, sent = master
        ipc.send_preview_stop('pedals')
        assert {m for m, _ in sent} == {'PREVIEW STOP:pedals'}

    def test_done_from_a_child_is_surfaced_with_its_device(self, master):
        ipc, done, _ = master
        ipc._handle_message("PREVIEW DONE:pedals:enable_stick_shaker", ('127.0.0.1', 41001))
        assert done == [('pedals', 'enable_stick_shaker')]
