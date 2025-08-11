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
import time
from typing import Optional, Tuple

from telemffb.telem.TelemManager import TelemManager
from telemffb.telem.TelemParserBase import TelemParserBase


class SharedMemThread(threading.Thread):
    def __init__(self, telemetry: TelemManager, telem_parser: Optional[TelemParserBase] = None) -> None:
        super().__init__()
        self._run: bool = False
        self._telem: TelemManager = telemetry
        self._telem_parser: Optional[TelemParserBase] = telem_parser

    def run(self) -> None:
        self._run = True

        while self._run:
            try:
                data: bytes
                data = self._telem_parser.process_packet(b"")
                if data:
                    data = data.decode("utf-8")
                    self._telem.submit_frame(data)
            except Exception as e:
                logging.error(f"Error reading from shared memory: {e}")
                continue
            time.sleep(0.05)

    def quit(self) -> None:
        if self._run:
            logging.info(f"SharedMemThread stopping")
            self._run = False