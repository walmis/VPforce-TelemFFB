import json
import logging
import socket
import threading
import time

from PyQt5.QtCore import QCoreApplication, QThread, pyqtSignal, QObject
from PyQt5.QtWidgets import QMessageBox

import telemffb.globals as G
from telemffb.utils import ChildPopen, load_custom_userconfig, overrides


class IPCNetworkThread(QThread):
    message_received = pyqtSignal(str)
    exit_signal = pyqtSignal(str)
    restart_sim_signal = pyqtSignal(str)
    show_signal = pyqtSignal()
    showlog_signal = pyqtSignal()
    hide_signal = pyqtSignal()
    show_settings_signal = pyqtSignal()
    child_keepalive_signal = pyqtSignal(str, str)

    def __init__(self, host="localhost", myport=0, dstport=0, master=False, keepalive_sec=1, missed_keepalive=3):
        super().__init__()

        self._running = False
        self._myport = int(myport or 0)
        self._dstport = int(dstport or 0)
        self._host = host
        # master False imples child
        self._master = master
        self._child_keepalive_timestamp = {}
        self._keepalive_sec = keepalive_sec
        self._missed_keepalive = missed_keepalive
        self._last_keepalive_timestamp = time.time()
        self._ipc_telem = {}
        self._ipc_telem_effects = {}
        self._timer : int = None # Qt timer

        self._child_active = {
            'joystick': False,
            'pedals': False,
            'collective': False
        }

        # Initialize socket
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        self._socket.settimeout(0.1)
        logging.info(f"Setting up IPC socket at {self._host}:{self._myport}")
        try:
            self._socket.bind((self._host, self._myport))
        except OSError as e:
            QMessageBox.warning(None, "Error", f"There was an error while setting up the inter-instance communications for the {G.device_type} instance of TelemFFB.\n\n{e}\nLikely there is a hung instance of TelemFFB (or python if running from source) that is holding the socket open.\n\nPlease close any instances of TelemFFB and then open Task Manager and kill any existing instances of TelemFFB")
            QCoreApplication.instance().quit()

    def send_ipc_telem(self, telem):
        j_telem = json.dumps(telem)
        message = f"telem:{j_telem}"
        self.send_message(message)

    def notify_close_children(self):
        self.send_broadcast_message("MASTER INSTANCE QUIT")

    def send_ipc_effects(self, active_effects, active_settings):
        payload = {
            f'{G.device_type}_active_effects': active_effects,
            f'{G.device_type}_active_settings': active_settings
        }

        msg = json.dumps(payload)

        msg = f'effects:{msg}'

        self.send_message(msg)

    def send_message(self, message):
        if not self._dstport:
            return
        encoded_data = message.encode("utf-8")
        try:    # socket may be closed
            self._socket.sendto(encoded_data, (self._host, self._dstport))
        except OSError as e:
            logging.warning(f"Error sending IPC frame: {e}")

    def send_broadcast_message(self, message):
        inst : ChildPopen
        for inst in G.launched_instances.values():
            port = inst.udp_port
            encoded_data = message.encode("utf-8")
            self._socket.sendto(encoded_data, (self._host, int(port)))

    def _send_keepalive(self):

        if self._master:
            self.send_broadcast_message("Keepalive")
            ts = time.time()
            logging.debug(f"SENT KEEPALIVES: {ts}")
        else:
            self.send_message(f"Child Keepalive:{G.device_type}")
            ts = time.time()
            logging.debug(f"{G.device_type} SENT CHILD KEEPALIVE: {ts}")

    def _receive_messages_loop(self):
        while self._running:
            try:
                data, addr = self._socket.recvfrom(4096)

                msg = data.decode("utf-8")
                if msg == 'Keepalive':
                    if not self._master:
                        ts = time.time()
                        logging.debug(f"GOT KEEPALIVE: {ts}")
                        self._last_keepalive_timestamp = ts
                elif msg.startswith('Child Keepalive:'):
                    ch_dev = msg.removeprefix('Child Keepalive:')
                    logging.debug(f"GOT KEEPALIVE FROM CHILD: '{ch_dev}'")
                    ts = time.time()
                    self._child_keepalive_timestamp[ch_dev] = ts
                    pass
                elif msg == 'MASTER INSTANCE QUIT':
                    logging.info("Received QUIT signal from master instance.  Running exit/cleanup function.")
                    self.exit_signal.emit("Received QUIT signal from master instance.  Running exit/cleanup function.")
                elif msg == 'RESTART SIMS':
                    self.restart_sim_signal.emit('Restart Sims')
                elif msg.startswith('SHOW LOG:'):
                    dev = msg.removeprefix('SHOW LOG:')
                    if dev == G.device_type:
                        logging.info("Show log command received via IPC")
                        self.showlog_signal.emit()
                elif msg == 'SHOW WINDOW':
                    logging.info("Show command received via IPC")
                    self.show_signal.emit()
                elif msg == 'HIDE WINDOW':
                    logging.info("Hide command received via IPC")
                    self.hide_signal.emit()
                elif msg == "SHOW SETTINGS":
                    logging.info("Show system settings command received via IPC")
                    self.show_settings_signal.emit()
                elif msg.startswith('telem:'):
                    payload = msg.removeprefix('telem:')
                    try:
                        ipc_telem = json.loads(payload)
                        # logging.info(f"GOT JSON PAYLOAD: {ipc_telem}")
                        self._ipc_telem.update(ipc_telem)

                    except json.JSONDecodeError:
                        pass
                elif msg.startswith('effects:'):
                    payload = msg.removeprefix('effects:')
                    try:
                        telem_effects_dict = json.loads(payload)
                        self._ipc_telem_effects.update(telem_effects_dict)
                        # print(f"GOT EFFECTS:{self._ipc_telem_effects}")

                    except json.JSONDecodeError:
                        pass
                elif msg.startswith("LOADCONFIG:"):
                    path = msg.removeprefix("LOADCONFIG:")
                    load_custom_userconfig(path)
                else:
                    logging.info(f"GOT GENERIC MESSAGE: {msg}")

                    self.message_received.emit(msg)
            except OSError:
                continue
            except ConnectionResetError:
                continue
            except socket.timeout:
                continue

    def start(self):
        if not self._running:
            self._running = True
            self._timer = self.startTimer(self._keepalive_sec*1000)
            super().start()


    def _check_missed_keepalives(self):
        if self._master:
            for device in self._child_keepalive_timestamp:
                elapsed_time = time.time() - self._child_keepalive_timestamp.get(device, time.time())
                if elapsed_time > (self._keepalive_sec * self._missed_keepalive):
                    logging.info(f"{device} KEEPALIVE TIMEOUT")
                    if self._child_active.get(device):
                        self.child_keepalive_signal.emit(device, 'TIMEOUT')
                        self._child_active[device] = False
                else:
                    logging.debug(f"{device} KEEPALIVE ACTIVE")
                    if not self._child_active.get(device):
                        self.child_keepalive_signal.emit(device, 'ACTIVE')
                        self._child_active[device] = True

        else: # we are child instance
            elapsed_time = time.time() - self._last_keepalive_timestamp
            if elapsed_time > (self._keepalive_sec * self._missed_keepalive):
                logging.error("KEEPALIVE TIMEOUT... exiting in 2 seconds")
                time.sleep(2)
                # QCoreApplication.instance().quit()
                self.exit_signal.emit("Missed too many keepalives. Exiting.")
                return

    @overrides(QObject)
    def timerEvent(self, id):
        self._check_missed_keepalives()
        self._send_keepalive()

    @property
    def running(self):
        return self._running

    @overrides(QThread)
    def run(self):
        self._receive_messages_loop()

    def stop(self):
        if self._running:
            logging.info("IPC Thread stopping")
            self.killTimer(self._timer)
            self._running = False
            self._socket.close()