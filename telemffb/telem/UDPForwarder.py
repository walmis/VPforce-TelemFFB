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

import json
import logging
import socket
from typing import Optional

import telemffb.globals as G

# IL-2 raw UDP packet header signatures (see IL2Manager.py)
IL2_PACKET_TELEMETRY = 0x54000101
IL2_PACKET_MOTION = 0x494C0100
IL2_PACKET_FFB = 0x494D0100


class UDPForwarder:
    """Forwards raw UDP payloads, as received, to a single configured destination."""

    def __init__(self):
        self._sock: Optional[socket.socket] = None

    def send(self, data: bytes, addr: str, port) -> None:
        if not addr or not port:
            return
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.sendto(data, (addr, int(port)))
        except (OSError, ValueError) as e:
            logging.warning(f"UDP forward to {addr}:{port} failed: {e}")

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


class IL2PacketForwarder:
    """
    Classifies raw IL-2 UDP packets by header (telemetry/motion/ffb) and fans each one out to
    every configured destination that opted in to that kind.

    Driven by system settings (configured via the IL2 tab in SystemSettingsDialog):
        il2_fwd_enable - master on/off switch
        il2_fwd_destinations - JSON list of {"addr": str, "port": str, "telem": bool,
                                              "motion": bool, "ffb": bool}
    """

    _HEADER_KIND = {
        IL2_PACKET_TELEMETRY: 'telem',
        IL2_PACKET_MOTION: 'motion',
        IL2_PACKET_FFB: 'ffb',
    }

    def __init__(self):
        self._socket = UDPForwarder()

    def forward(self, data: bytes) -> None:
        if len(data) < 4:
            return
        if not G.system_settings.get('il2_fwd_enable', False):
            return
        header = int.from_bytes(data[:4], 'little')
        kind = self._HEADER_KIND.get(header)
        if kind is None:
            return
        for dest in self._get_destinations():
            if dest.get(kind):
                self._socket.send(data, dest.get('addr', ''), dest.get('port', 0))

    def _get_destinations(self) -> list:
        raw = G.system_settings.get('il2_fwd_destinations', '[]')
        try:
            return json.loads(raw) if raw else []
        except (TypeError, ValueError):
            logging.warning("Failed to parse il2_fwd_destinations setting")
            return []

    def close(self) -> None:
        self._socket.close()
