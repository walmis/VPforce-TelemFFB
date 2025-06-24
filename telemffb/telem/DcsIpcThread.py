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
from typing import Optional, Tuple, TYPE_CHECKING
from libipc_ctypes import IPCChannel, ChannelType, ConnMode, IPCError, IPCStatus

if TYPE_CHECKING:
    from telemffb.telem.TelemManager import TelemManager


class DcsIpcThread(threading.Thread):
    def __init__(self, telemetry: 'TelemManager') -> None:
        super().__init__()
        assert telemetry is not None, "Telemetry manager must be provided"
        self._run: bool = False
        self._telem: 'TelemManager' = telemetry
        self.recv_channel: IPCChannel
        self.send_channel: IPCChannel

    @classmethod
    def send_commands(cls, command: str) -> None:
        """
        Send a command to the DCS IPC channel.
        :param command: Command string to send.
        """
        logging.error("DCS IPC send channel is not initialized.")


    def run(self) -> None:
        self._run = True
        self.recv_channel: IPCChannel = IPCChannel(ChannelType.CHANNEL, "eu.vpforce.telemffb.telem", ConnMode.RECEIVER)
        self.send_channel: IPCChannel = IPCChannel(ChannelType.CHANNEL, "eu.vpforce.telemffb.cmds", ConnMode.SENDER)

        logging.info("Listening on DCS IPC channel")
        
        while self._run:
            try:
                packet : bytes = self.recv_channel.receive(100)
                packet = packet.strip(b"\0")
                self._telem.submit_frame(packet)
            except IPCError as e:
                if e.status_code == IPCStatus.ERROR_RECEIVE_FAILED:
                    continue
                # Handle any exceptions that occur during IPC communication
                logging.exception(f"Error receiving data from DCS IPC channel: {e.status_code}")
                continue

    def quit(self) -> None:
        if self._run:
            logging.info("DcsIpcThread stopping")
            self._run = False