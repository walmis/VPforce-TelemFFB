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
import time
import zmq
from zmq import Context, Socket

if TYPE_CHECKING:
    from telemffb.telem.TelemManager import TelemManager


class DcsIpcThread(threading.Thread):
    def __init__(self, telemetry: 'TelemManager') -> None:
        super().__init__()
        assert telemetry is not None, "Telemetry manager must be provided"
        self._run: bool = False
        self._telem: 'TelemManager' = telemetry
        self.zmq_context: Optional[zmq.Context] = zmq.Context()
        self.recv_socket: Optional[zmq.Socket] = None
        self.send_socket: Optional[zmq.Socket] = None
        DcsIpcThread._instance = self

    def __del__(self) -> None:
        """
        Clean up ZMQ sockets and context when the instance is deleted.
        """
        self._cleanup_sockets()
        if self.zmq_context:
            self.zmq_context.destroy()
            self.zmq_context = None

    @classmethod
    def send_commands(cls, command: str) -> None:
        """
        Send a command to the DCS ZMQ channel.
        :param command: Command string to send.
        """
        self = cls._instance

        if not self.send_socket:
            self.send_socket = self.zmq_context.socket(zmq.PUB)
            self.send_socket.connect("tcp://localhost:34385")

        try:
            self.send_socket.send_string(command, zmq.NOBLOCK)
        except zmq.Again:
            logging.warning("Failed to send command: socket would block")
        except zmq.ZMQError as e:
            logging.error(f"Error sending command to DCS ZMQ channel: {e}")
        else:
            logging.error("DCS ZMQ send socket is not initialized.")

    def run(self) -> None:
        self._run = True
        try:
            if not self.recv_socket:
                self.recv_socket = self.zmq_context.socket(zmq.SUB)
                self.recv_socket.connect("tcp://localhost:34384")
                self.recv_socket.setsockopt(zmq.SUBSCRIBE, b"")  # Subscribe to all messages
                self.recv_socket.setsockopt(zmq.RCVTIMEO, 100)  # 100ms timeout

            logging.info("Listening on DCS ZMQ channel")
            
            while self._run:
                try:
                    packet = self.recv_socket.recv()
                    packet = packet.strip(b"\0")
                    self._telem.submit_frame(packet)
                except zmq.Again:
                    continue
                except zmq.ZMQError as e:
                    logging.exception(f"Error receiving data from DCS ZMQ channel: {e}")
                    continue
        finally:
            self._cleanup_sockets()

    def quit(self) -> None:
        if self._run:
            logging.info("DcsIpcThread stopping")
            self._run = False

    def _cleanup_sockets(self) -> None:
        """Clean up ZMQ sockets and context."""
        if self.recv_socket:
            self.recv_socket.close()
        if self.send_socket:
            self.send_socket.close()
        
        self.send_socket = None
        self.recv_socket = None


