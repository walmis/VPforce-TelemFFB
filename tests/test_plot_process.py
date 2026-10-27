"""The plot process plumbing: the datagram protocol, the feed sent from the
telemetry thread, the launcher's command line, and the plot process's
receiver."""
import socket
import sys
import time

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6.QtWidgets import QApplication

from telemffb.plot import protocol
from telemffb.plot.app import FeedReceiver
from telemffb.plot.feed import PlotFeed
from telemffb.plot.process import free_port, plotter_command
from telemffb.ui.dialogs.TelemetryPlotWindow import TelemetryPlotWindow

pytestmark = [pytest.mark.unit]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def receiver_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    yield sock
    sock.close()


class TestProtocol:
    def test_frame_keys_and_commands_round_trip(self):
        assert protocol.decode(protocol.encode_frame(1.5, {'IAS': 51.0, 'WoW': [0.2, 0.4]})) == \
            {"t": 1.5, "v": {'IAS': 51.0, 'WoW': [0.2, 0.4]}}
        assert protocol.decode(protocol.encode_keys(['b', 'a'])) == {"keys": ['a', 'b']}
        assert protocol.decode(protocol.encode_command(protocol.CMD_SHOW)) == {"cmd": "show"}

    def test_numpy_scalars_become_floats(self):
        import numpy as np
        message = protocol.decode(protocol.encode_frame(0.0, {'G': np.float64(1.25), 'L': [np.float32(0.5)]}))
        assert message["v"] == {'G': 1.25, 'L': [0.5]}

    def test_garbage_decodes_to_none(self):
        assert protocol.decode(b"\xff\xfe") is None
        assert protocol.decode(b"[1, 2]") is None


class TestFeed:
    def test_sends_only_the_marked_numeric_keys(self, receiver_socket):
        feed = PlotFeed(receiver_socket.getsockname()[1])
        feed.keys = ('IAS', 'N', 'WoW', 'Absent')
        feed.send_frame({'IAS': 51.0, 'N': 'C172', 'WoW': [0.2, 0.4], 'TAS': 55.0})
        message = protocol.decode(receiver_socket.recv(65535))
        assert message["v"] == {'IAS': 51.0, 'WoW': [0.2, 0.4]}
        assert message["t"] > 0
        feed.close()

    def test_nothing_is_sent_without_marks(self, receiver_socket):
        feed = PlotFeed(receiver_socket.getsockname()[1])
        feed.send_frame({'IAS': 51.0})
        receiver_socket.settimeout(0.2)
        with pytest.raises(socket.timeout):
            receiver_socket.recv(65535)
        feed.close()

    def test_set_keys_sends_the_sorted_list(self, receiver_socket):
        feed = PlotFeed(receiver_socket.getsockname()[1])
        feed.set_keys({'b', 'a'})
        assert protocol.decode(receiver_socket.recv(65535)) == {"keys": ['a', 'b']}
        feed.close()


class TestLauncher:
    def test_script_run_starts_the_interpreter_with_the_script(self, monkeypatch):
        monkeypatch.delattr(sys, 'frozen', raising=False)
        monkeypatch.setattr(sys, 'argv', ['main.py'])
        assert plotter_command(5000, 'pedals', 'dark') == \
            [sys.executable, 'main.py', '--plot-window', '--plot-port', '5000',
             '--plot-device', 'pedals', '--plot-theme', 'dark']

    def test_frozen_run_starts_the_exe_alone(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        assert plotter_command(5000, 'joystick', 'light')[:2] == [sys.executable, '--plot-window']

    def test_free_port_is_bindable(self):
        port = free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", port))


class TestReceiver:
    def test_datagrams_become_the_matching_signals(self, qapp):
        receiver = FeedReceiver()
        heard = []
        receiver.frame.connect(lambda t, v: heard.append(('frame', t, v)))
        receiver.keys.connect(lambda k: heard.append(('keys', k)))
        receiver.show.connect(lambda: heard.append(('show',)))
        receiver.quit.connect(lambda: heard.append(('quit',)))
        receiver.handle(protocol.encode_frame(2.0, {'IAS': 1.0}))
        receiver.handle(protocol.encode_keys(['IAS']))
        receiver.handle(protocol.encode_command(protocol.CMD_SHOW))
        receiver.handle(protocol.encode_command(protocol.CMD_QUIT))
        receiver.handle(b"not json")
        assert heard == [('frame', 2.0, {'IAS': 1.0}), ('keys', ['IAS']), ('show',), ('quit',)]

    def test_the_window_plots_from_the_first_frame_and_follows_the_keys(self, qapp):
        window = TelemetryPlotWindow(device='joystick')
        window.set_keys(['IAS', 'AoA'])
        window.add_frame(100.0, {'IAS': 1.0, 'AoA': 2.0})
        window.add_frame(100.5, {'IAS': 1.5, 'AoA': 2.5})
        assert window._plot.series_names() == ['IAS', 'AoA']
        assert window._plot.view_span()[1] == 0.5
        window.set_keys(['IAS'])
        assert window._plot.series_names() == ['IAS']
        assert window.windowTitle() == "Telemetry Plot (joystick)"
        window.deleteLater()

    def test_live_frames_are_dropped_while_a_recording_is_loaded(self, qapp, tmp_path):
        window = TelemetryPlotWindow()
        window.set_keys(['IAS'])
        window.add_frame(100.0, {'IAS': 1.0})
        path = str(tmp_path / "rec.csv")
        window._plot.write_csv(path)
        window._plot.load_csv(path)
        window.add_frame(101.0, {'IAS': 2.0})
        assert len(window._plot._series['IAS']) == 1
        window._plot.back_to_live()
        window.add_frame(200.0, {'IAS': 3.0})
        assert window._plot.view_span()[1] == 0.0
        window.deleteLater()


class TestListener:
    def test_the_listener_thread_relays_datagrams_and_reports_silence(self, qapp):
        from telemffb.plot.app import FeedListener
        from telemffb.plot import app as plot_app
        receiver = FeedReceiver()
        heard = []
        receiver.frame.connect(lambda t, v: heard.append(v))
        receiver.timedOut.connect(lambda: heard.append("silence"))
        port = free_port()
        listener = FeedListener(port, receiver)
        listener.start()
        feed = PlotFeed(port)
        feed.keys = ("IAS",)
        feed.send_frame({"IAS": 1.0})
        deadline = time.monotonic() + 2.0
        while not heard and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert heard == [{"IAS": 1.0}]
        deadline = time.monotonic() + plot_app.FEED_TIMEOUT_S + 2.0
        while "silence" not in heard and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)
        assert heard[-1] == "silence"
        listener.join(timeout=2.0)
        assert not listener.is_alive()
        feed.close()
