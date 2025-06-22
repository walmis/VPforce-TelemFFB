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


import logging
import socket
import threading
from typing import Optional, Tuple

from telemffb.telem.TelemManager import TelemManager
from telemffb.telem.TelemParserBase import TelemParserBase

class NetworkThread(threading.Thread):
    def __init__(self, telemetry: TelemManager, host: str = "", port: int = 34380, telem_parser: Optional[TelemParserBase] = None) -> None:
        super().__init__()
        self._run: bool = False
        self._port: int = port
        self._host: str = host
        self._telem: TelemManager = telemetry
        self._telem_parser: Optional[TelemParserBase] = telem_parser

    def run(self) -> None:
        self._run = True
        s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)

        s.settimeout(0.1)
        s.bind((self._host, self._port))
        logging.info(f"Listening on UDP {self._host}:{self._port}")
        
        while self._run:
            try:
                data: bytes
                sender: Tuple[str, int]
                data, sender = s.recvfrom(4096)
                if self._telem_parser is not None:
                    data = self._telem_parser.process_packet(data)

                self._telem.submit_frame(data)
            except ConnectionResetError:
                continue
            except socket.timeout:
                continue

    def quit(self) -> None:
        if self._run:
            logging.info(f"NetworkThread stopping")
            self._run = False