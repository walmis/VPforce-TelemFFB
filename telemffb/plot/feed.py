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

"""PlotFeed: sends the marked telemetry keys to the plot process.

``send_frame`` runs on the telemetry thread, once per frame, the way the
Teleplot exporter does: it picks the marked keys out of the frame, encodes
one datagram and sends it.  Nothing here touches Qt.  ``keys`` is replaced
whole, never mutated, so the telemetry thread reads a consistent tuple.
"""

import socket
import time
from typing import Iterable, Tuple

from telemffb.plot.protocol import encode_command, encode_frame, encode_keys, plottable


class PlotFeed:
    """A connected UDP socket to the plotter on localhost."""

    def __init__(self, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.connect(("127.0.0.1", port))
        self.keys: Tuple[str, ...] = ()

    def set_keys(self, keys: Iterable[str]) -> None:
        self.keys = tuple(sorted(keys))
        self.send_keys()

    def send_keys(self) -> None:
        self._send(encode_keys(self.keys))

    def send_command(self, command: str) -> None:
        self._send(encode_command(command))

    def send_frame(self, telem_data) -> None:
        """The marked keys present in this frame, stamped with the sender's
        monotonic clock.  Telemetry thread."""
        keys = self.keys
        if not keys:
            return
        values = {}
        for key in keys:
            value = telem_data.get(key)
            if isinstance(value, (list, tuple)):
                values[key] = value
            else:
                number = plottable(value)
                if number is not None:
                    values[key] = number
        if values:
            self._send(encode_frame(time.perf_counter(), values))

    def _send(self, datagram: bytes) -> None:
        # A connected UDP socket on Windows reports the peer's absence as a
        # ConnectionResetError on a later send; the plotter's liveness is the
        # launcher's business, not this one's.
        try:
            self._sock.send(datagram)
        except OSError:
            pass

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
