#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""The plot process: ``run`` is what ``main.py`` hands over to when started
with ``--plot-window``, before any of its own startup phases.

A listener thread reads the feed's datagrams and relays them as Qt signals
to the window on this process's main thread; the window closes to hide and
keeps its buffer; the process exits on the feed's ``quit`` command, on
``FEED_TIMEOUT_S`` of silence, or when the application quits.
"""

import logging
import socket
import threading
import time

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

import styles
from telemffb.plot.protocol import CMD_QUIT, CMD_SHOW, FEED_TIMEOUT_S, decode
from telemffb.ui.dialogs.TelemetryPlotWindow import TelemetryPlotWindow


class FeedReceiver(QObject):
    """Turns the feed's datagrams into signals.  ``handle`` runs on the
    listener thread; the signals are delivered on the main thread."""

    frame = pyqtSignal(float, object)
    keys = pyqtSignal(object)
    units = pyqtSignal(object)
    show = pyqtSignal()
    quit = pyqtSignal()
    timedOut = pyqtSignal()

    def handle(self, datagram: bytes) -> None:
        message = decode(datagram)
        if message is None:
            return
        if "v" in message:
            try:
                self.frame.emit(float(message.get("t", 0.0)), message["v"])
            except (TypeError, ValueError):
                pass
        elif "keys" in message:
            self.keys.emit(list(message["keys"]))
            self.units.emit(_units(message.get("units")))
        elif message.get("cmd") == CMD_SHOW:
            self.show.emit()
        elif message.get("cmd") == CMD_QUIT:
            self.quit.emit()


def _units(raw) -> dict:
    """The ``units`` of a keys message as ``{key: (unit, factor)}``,
    skipping anything malformed."""
    units = {}
    if isinstance(raw, dict):
        for key, pair in raw.items():
            try:
                unit, factor = pair
                units[str(key)] = (str(unit), float(factor))
            except (TypeError, ValueError):
                continue
    return units


class FeedListener(threading.Thread):
    """Reads the feed socket until told to stop, and reports silence."""

    def __init__(self, port: int, receiver: FeedReceiver):
        super().__init__(name="plot-feed", daemon=True)
        self._receiver = receiver
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", port))
        self._sock.settimeout(0.5)
        # not ``_stop``: threading.Thread has a method of that name
        self._stopping = threading.Event()

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        try:
            self._listen()
        finally:
            self._sock.close()

    def _listen(self) -> None:
        last = time.monotonic()
        while not self._stopping.is_set():
            try:
                datagram, _ = self._sock.recvfrom(65535)
            except socket.timeout:
                if time.monotonic() - last > FEED_TIMEOUT_S:
                    self._receiver.timedOut.emit()
                    return
                continue
            except OSError:
                return
            last = time.monotonic()
            self._receiver.handle(datagram)


def _apply_theme(app: QApplication, theme: str) -> None:
    dark = theme == 'dark'
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
    app.setStyleSheet(styles.DARK_MODE_STYLESHEET if dark else styles.LIGHT_MODE_STYLESHEET)


def run(app: QApplication, args) -> int:
    """The plot process's whole life: window, listener, event loop."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _apply_theme(app, args.plot_theme or 'light')
    window = TelemetryPlotWindow(device=args.plot_device or '')
    receiver = FeedReceiver()
    receiver.frame.connect(window.add_frame)
    receiver.keys.connect(window.set_keys)
    receiver.units.connect(window.set_units)
    receiver.show.connect(window.show_and_raise)
    receiver.quit.connect(app.quit)
    receiver.timedOut.connect(app.quit)
    try:
        listener = FeedListener(int(args.plot_port), receiver)
    except OSError:
        logging.exception("Plot: the feed port could not be opened")
        return 1
    listener.start()
    window.show_and_raise()
    code = app.exec()
    listener.stop()
    return code
