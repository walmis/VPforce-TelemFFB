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
import time
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

import telemffb.globals as G
from telemffb import utils
from telemffb.utils import load_custom_userconfig


class IPCNetworkThread(QObject, threading.Thread):
    message_received = pyqtSignal(str)
    exit_signal = pyqtSignal(str)
    restart_sim_signal = pyqtSignal(str)
    reload_aircraft_signal = pyqtSignal()
    show_signal = pyqtSignal()
    showlog_signal = pyqtSignal()
    hide_signal = pyqtSignal()
    show_settings_signal = pyqtSignal()
    show_adv_spr_signal = pyqtSignal(str)
    show_cfg_ovds_signal = pyqtSignal()
    erase_cfg_ovds_signal = pyqtSignal()
    child_keepalive_signal = pyqtSignal(str, str)
    toggle_offline_mode_signal = pyqtSignal(bool)
    set_offline_sim_signal = pyqtSignal(str)
    set_offline_class_signal = pyqtSignal(str)
    set_offline_ac_signal = pyqtSignal(str)
    set_offline_profile_signal = pyqtSignal(str)
    show_offline_model_signal = pyqtSignal(str, str, str, str)
    reload_caller_signal = pyqtSignal()

    def __init__(self, host="127.0.0.1", dstport=0, keepalive_sec=1, missed_keepalive=3):
        QObject.__init__(self)
        threading.Thread.__init__(self, daemon=True)

        self._running = threading.Event()
        self._myport = 0
        self._dstport = int(dstport or 0)
        self._host = host
        self._master = not bool(dstport)
        self._keepalive_sec = keepalive_sec
        self._missed_keepalive = missed_keepalive
        self._last_keepalive_timestamp = time.time()
        self._ipc_telem = {}
        self._ipc_telem_effects = {}
        self._child_keepalive_info = {}
        self._child_addrs = {}
        self._child_active = {'joystick': None, 'pedals': None, 'collective': None, 'trimwheel': None}

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Generous kernel receive buffer: the master ingests continuous
        # telemetry/effects datagrams from every child instance, and a small
        # buffer (formerly 4 KB = ~1-3 datagrams) overflows under load, making
        # the kernel silently drop packets — including the 1 Hz child
        # keepalives, which showed up as spurious child TIMEOUT flaps.
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        self._socket.settimeout(0.1)

        try:
            self._socket.bind((self._host, 0))
        except OSError as e:
            QMessageBox.warning(None, "IPC Error", str(e))
            raise SystemExit

        self._host, self._myport = self._socket.getsockname()
        logging.info(f"IPC socket initialized at {self._host}:{self._myport}")

    @property
    def local_port(self):
        return self._myport

    @property
    def running(self):
        return self._running.is_set()

    def start(self):
        if not self.running:
            self._running.set()
            super().start()
            self._send_keepalive()  # immediate kick-off

    def stop(self):
        self._running.clear()
        self._socket.close()

    def run(self):
        last_check = time.monotonic()

        while self._running.is_set():
            now = time.monotonic()

            # 1. Handle socket input: block briefly for the first datagram,
            # then drain everything already queued without blocking. Reading a
            # single datagram per pass (with the 10 ms sleep below) capped
            # intake at ~100 msg/s — below the combined telemetry/effects rate
            # of multiple child instances — so packets were dropped wholesale.
            # recvfrom(65535) also prevents Windows from silently discarding
            # datagrams larger than the read size (WSAEMSGSIZE).
            try:
                data, fromaddr = self._socket.recvfrom(65535)
                self._handle_message(data.decode("utf-8"), fromaddr)
                self._socket.settimeout(0)
                try:
                    # Bounded drain so a sustained flood can never starve the
                    # periodic keepalive send below.
                    for _ in range(500):
                        data, fromaddr = self._socket.recvfrom(65535)
                        self._handle_message(data.decode("utf-8"), fromaddr)
                except (BlockingIOError, socket.timeout, OSError):
                    pass
                finally:
                    self._socket.settimeout(0.1)
            except (socket.timeout, OSError):
                pass

            # 2. Periodic check
            if now - last_check >= self._keepalive_sec:
                self._check_missed_keepalives()
                self._send_keepalive()
                last_check = now

            time.sleep(0.01)  # avoid CPU spin

    def _check_child_dev_status(self):
        pass

    def _check_missed_keepalives(self):
        now = time.time()

        if self._master:
            for dev, info in self._child_keepalive_info.items():
                ts = info["timestamp"]
                is_connected = info["connected"]
                delta = now - ts
                if delta > self._keepalive_sec * self._missed_keepalive:
                    if self._child_active.get(dev):
                        self.child_keepalive_signal.emit(dev, "TIMEOUT")
                        self._child_active[dev] = False
                else:
                    # utils.dbprint("yellow",
                    #               f"{G.device_type} - Child: {dev} is {is_connected} at {ts}. ")
                    if not is_connected:
                        self.child_keepalive_signal.emit(dev, "DISCONNECTED")
                        self._child_active[dev] = False
                    elif not self._child_active.get(dev):
                        self.child_keepalive_signal.emit(dev, "ACTIVE")
                        self._child_active[dev] = True
        else:
            delta = now - self._last_keepalive_timestamp
            if delta > self._keepalive_sec * self._missed_keepalive:
                logging.error("Keepalive timeout. Waiting grace period...")
                timeout_start = time.time()
                while time.time() - timeout_start < 2:
                    if time.time() - self._last_keepalive_timestamp <= self._keepalive_sec * self._missed_keepalive:
                        logging.info("Keepalive received during grace period")
                        return
                    time.sleep(0.1)
                self.exit_signal.emit("Missed too many keepalives. Exiting.")

    def _send_keepalive(self):
        if self._master:
            self.send_broadcast_message("Keepalive")
        else:
            self.send_message(f"Child Keepalive:{G.device_type}:{G.device_connection_status}")

    def _handle_message(self, msg, fromaddr):
        if msg == 'Keepalive':
            if not self._master:
                ts = time.time()
                logging.debug(f"GOT KEEPALIVE: {ts}")
                self._last_keepalive_timestamp = ts
        elif msg.startswith('Child Keepalive:'):
            _, ch_dev, ch_status = msg.split(':')
            # ch_dev = msg.removeprefix('Child Keepalive:')
            logging.debug(f"GOT KEEPALIVE FROM CHILD: '{ch_dev}', Device Connection Status is: {ch_status}")
            ts = time.time()
            self._child_keepalive_info[ch_dev] = {
                "timestamp": ts,
                "connected": ch_status == "True"
            }
            self._child_addrs[ch_dev] = fromaddr
            pass
        elif msg == 'MASTER INSTANCE QUIT':
            logging.info("Received QUIT signal from master instance.  Running exit/cleanup function.")
            self.exit_signal.emit("Received QUIT signal from master instance.  Running exit/cleanup function.")
        elif msg == 'RESTART SIMS':
            self.restart_sim_signal.emit('Restart Sims')
        elif msg == "RELOAD AIRCRAFT":
            self.reload_aircraft_signal.emit()
        elif msg.startswith('SHOW LOG:'):
            dev = msg.removeprefix('SHOW LOG:')
            if dev == G.device_type:
                logging.info("Show log command received via IPC")
                self.showlog_signal.emit()
        elif msg.startswith('SHOW ADV SPR;'):
            parts = msg.split(';')  # delimiter is semicolon since value contains colons (json dictionary)
            if len(parts) == 3:
                _, target_dev, value = parts
                if target_dev == G.device_type:
                    logging.info('Show advanced spring override config command received via IPC')
                    self.show_adv_spr_signal.emit(value)
        elif msg.startswith('SHOW GAIN OVD:'):
            dev = msg.removeprefix('SHOW GAIN OVD:')
            if dev == G.device_type:
                logging.info("Show configurator overrides command received via IPC")
                self.show_cfg_ovds_signal.emit()
        elif msg.startswith('ERASE GAIN OVD:'):
            dev = msg.removeprefix('ERASE GAIN OVD:')
            if dev == G.device_type:
                logging.info("Erase configurator overrides command received via IPC")
                self.erase_cfg_ovds_signal.emit()
        elif msg.startswith('RELOAD CALLER:'):
            dev = msg.removeprefix('RELOAD CALLER:')
            if dev == G.device_type:
                logging.info("Reload Caller command received via IPC")
                self.reload_caller_signal.emit()
        elif msg == 'SHOW WINDOW':
            logging.info("Show command received via IPC")
            self.show_signal.emit()
        elif msg.startswith('SHOW WINDOW:'):
            dev = msg.removeprefix('SHOW WINDOW:')
            if dev == G.device_type:
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
        elif msg.startswith("MASTER_BUTTONS:"):
            payload = msg.removeprefix("MASTER_BUTTONS:")
            G.master_buttons = json.loads(payload)
            # print(f"MB: {G.master_buttons}")
        elif msg.startswith("BUTTONS:"):
            payload = msg.removeprefix("BUTTONS:").split("_")
            dev = payload[0]
            btns = json.loads(payload[1])
            G.child_buttons[dev] = btns
            # print(G.child_buttons)
        elif msg.startswith('TOGGLE OFFLINE:'):
            state_str = msg.removeprefix('TOGGLE OFFLINE:')
            state = state_str == 'True'
            self.toggle_offline_mode_signal.emit(state)
        elif msg.startswith("OFFLINE_SIM:"):
            sim = msg.removeprefix("OFFLINE_SIM:")
            self.set_offline_sim_signal.emit(sim)
        elif msg.startswith("OFFLINE_CLASS:"):
            class_name = msg.removeprefix("OFFLINE_CLASS:")
            self.set_offline_class_signal.emit(class_name)
        elif msg.startswith("OFFLINE_AC:"):
            ac = msg.removeprefix("OFFLINE_AC:")
            self.set_offline_ac_signal.emit(ac)
        elif msg.startswith("OFFLINE_PROFILE:"):
            profile = msg.removeprefix("OFFLINE_PROFILE:")
            self.set_offline_profile_signal.emit(profile)
        elif msg.startswith("SHOW_OFFLINE_MODEL:"):
            payload = msg.removeprefix("SHOW_OFFLINE_MODEL:")
            args = json.loads(payload)
            self.show_offline_model_signal.emit(*args)
        else:
            logging.info(f"GOT GENERIC MESSAGE: {msg}")

    def send_ipc_telem(self, telem):
        self.send_message(f"telem:{json.dumps(telem)}")

    def send_ipc_effects(self, active_effects, active_settings):
        payload = {
            f'{G.device_type}_active_effects': active_effects,
            f'{G.device_type}_active_settings': active_settings,
            # Device-status context so the master can mirror this instance's
            # vpconf-profile / gain-override indicators when the user selects
            # this device as the config scope.
            f'{G.device_type}_vpconf_profile': G.current_vpconf_profile or '',
            f'{G.device_type}_gain_ovd_active':
                bool(G.telem_manager.gain_overrides_active) if G.telem_manager else False,
        }
        self.send_message(f"effects:{json.dumps(payload)}")

    def notify_close_children(self):
        self.send_broadcast_message("MASTER INSTANCE QUIT")

    def send_message(self, message):
        if not self._dstport:
            return
        try:
            self._socket.sendto(message.encode("utf-8"), (self._host, self._dstport))
        except OSError as e:
            logging.warning(f"IPC send error: {e}")

    def send_broadcast_message(self, message):
        for addr in self._child_addrs.values():
            if addr:
                try:
                    self._socket.sendto(message.encode("utf-8"), addr)
                except OSError as e:
                    logging.warning(f"IPC broadcast error: {e}")
