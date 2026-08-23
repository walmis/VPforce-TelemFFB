"""The telemetry listeners let the process exit.

Every instance ran all five sim listeners, and one of them - the BMS shared
memory reader - slept ten seconds between connect attempts on a non-daemon
thread.  Telling it to stop only set a flag it could not see until it woke,
so each TelemFFB process lingered for whatever was left of that sleep after
its last log line, and the master's mutex with it.
"""
import threading
import time

import pytest

# Importing the telemetry package pulls in simconnect, which leaves a file
# handle open that the collector later complains about; scoped to this
# module, as in the other files that import it.
pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


class NeverConnects:
    def connect(self):
        return False


def bms_parser():
    """A BMSManager with just the connect machinery, no sim."""
    # imported here, not at module scope: the telemetry package pulls in
    # simconnect, whose import leaks a file handle the collector reports
    # against whichever test is running - so it has to be one of these
    from telemffb.telem.BMSTelemManager import BMSManager
    parser = BMSManager.__new__(BMSManager)
    parser.bms_memory = NeverConnects()
    parser._connected = False
    parser._connection_attempts = 1          # keep it from logging
    parser.stop_event = None
    return parser


class TestTheReadersNeverHoldTheProcess:
    def test_shared_memory_reader_is_a_daemon_thread(self):
        from telemffb.telem.SharedMemThread import SharedMemThread
        assert SharedMemThread(telemetry=object(), telem_parser=None).daemon

    def test_network_reader_is_a_daemon_thread(self):
        from telemffb.telem.NetworkThread import NetworkThread
        assert NetworkThread(object(), host="127.0.0.1", port=0).daemon

    def test_dcs_ipc_reader_is_a_daemon_thread(self):
        from telemffb.telem.DcsIpcThread import DcsIpcThread
        assert DcsIpcThread(object()).daemon


class TestTheBmsBackoffWakesWhenStopped:
    def test_a_stop_ends_the_wait_at_once(self):
        parser = bms_parser()
        parser.stop_event = threading.Event()
        started = time.monotonic()
        threading.Timer(0.1, parser.stop_event.set).start()
        parser._try_connect()
        assert time.monotonic() - started < 2, "still slept out the backoff"

    def test_without_a_listener_it_still_waits(self, monkeypatch):
        """Nothing else uses it that way, but the fallback has to be the old
        behavior rather than a busy loop."""
        parser = bms_parser()
        slept = []
        monkeypatch.setattr("telemffb.telem.BMSTelemManager.time.sleep",
                            lambda s: slept.append(s))
        parser._try_connect()
        assert slept == [10]

    def test_quitting_the_listener_wakes_the_parser_inside_it(self):
        """End to end: the thread is mid-backoff; quit() returns it."""
        from telemffb.telem.SharedMemThread import SharedMemThread
        parser = bms_parser()
        thread = SharedMemThread(telemetry=object(), telem_parser=parser)
        assert parser.stop_event is thread.stop_event
        thread.start()
        time.sleep(0.2)                      # into the ten-second wait
        thread.quit()
        thread.join(2)
        assert not thread.is_alive()
