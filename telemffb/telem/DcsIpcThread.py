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
from telemffb.util.SharedMemReader import SharedMemoryReader
from telemffb.utils import schedule_on_main_thread
import telemffb.globals as G

if TYPE_CHECKING:
    from telemffb.telem.TelemManager import TelemManager

class DcsIpcThread(threading.Thread):
    _instance: Optional['DcsIpcThread'] = None

    def __init__(self, telemetry: 'TelemManager') -> None:
        # Daemon: a telemetry reader must never hold the process open.  These
        # threads are told to stop at exit, but one asleep in a retry backoff
        # would keep the interpreter - and the master's mutex - alive until it
        # woke; the BMS listener's ten-second sleep did exactly that.
        super().__init__(daemon=True)
        assert telemetry is not None, "Telemetry manager must be provided"
        self._run: bool = False
        self._telem: 'TelemManager' = telemetry

        self._shm : SharedMemoryReader = SharedMemoryReader(b"telemffb_shared_memory", 65536)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        DcsIpcThread._instance = self

    def __del__(self) -> None:
        """
        Clean up ZMQ sockets and context when the instance is deleted.
        """
        if self._shm:
            self._shm.close()
            self._shm = None
        self._socket.close()

    @classmethod
    def send_commands(cls, command: str) -> None:
        """
        Send a command to the DCS UDP channel.
        :param command: Command string to send.
        """
        self = cls._instance
        if self is None:
            logging.debug("DcsIpcThread.send_commands: no DCS thread yet, dropping command")
            return
        port = self._telem.getTelemValue("UDP_Port")
        if port:
            try:
                self._socket.sendto(command.encode('utf-8'), ('localhost', port))
            except socket.error as e:
                logging.error(f"Failed to send command to DCS: {e}")



    def run(self) -> None:
        self._run = True

        logging.info("Listening on DCS IPC channel")
        
        while self._run:
            try:
                packet = self._shm.read(1000)
                #print(f"Read packet: {packet}")
                if not packet:
                    continue
                message = packet.message

                # If we get Ev=Start, indicate to GUI that DCS sim is active and telemFFB is loaded
                if message == "Ev=Start":
                    # Schedule GUI update on main thread
                    schedule_on_main_thread(lambda: G.main_window.update_sim_indicators("DCS", paused=True))

                if message == "Ev=Stop":
                    # Schedule GUI update on main thread
                    schedule_on_main_thread(lambda: G.main_window.status_container.set_waiting("DCS"))

                self._telem.submit_frame(message)
            except Exception as e:
                logging.error(f"Error reading from shared memory: {e}")
                continue


    def quit(self) -> None:
        if self._run:
            logging.info("DcsIpcThread stopping")
            self._run = False



