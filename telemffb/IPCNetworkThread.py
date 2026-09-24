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
from telemffb.ChildTelemView import ChildTelemView
from telemffb.utils import load_custom_userconfig

# How long a child keeps sending its telemetry view after the master last
# asked for it. The master asks again on every keepalive tick, so the view
# outlives a lost request but not a master that stopped wanting it.
VIEW_LEASE_SEC = 3.0
# The largest UDP payload is 65,507 bytes; a frame is normally a tenth of it.
MAX_DATAGRAM = 65000


class IPCNetworkThread(QObject, threading.Thread):
    message_received = pyqtSignal(str)
    exit_signal = pyqtSignal(str)
    restart_sim_signal = pyqtSignal(str)
    reload_aircraft_signal = pyqtSignal()
    reacquire_device_signal = pyqtSignal()
    show_signal = pyqtSignal()
    showlog_signal = pyqtSignal()
    hide_signal = pyqtSignal()
    show_settings_signal = pyqtSignal()
    show_adv_spr_signal = pyqtSignal(str)
    show_cfg_ovds_signal = pyqtSignal()
    erase_cfg_ovds_signal = pyqtSignal()
    child_keepalive_signal = pyqtSignal(str, str)
    child_exception_signal = pyqtSignal(object)
    toggle_offline_mode_signal = pyqtSignal(bool)
    set_offline_sim_signal = pyqtSignal(str)
    set_offline_class_signal = pyqtSignal(str)
    set_offline_ac_signal = pyqtSignal(str)
    set_offline_profile_signal = pyqtSignal(str)
    show_offline_model_signal = pyqtSignal(str, str, str, str)
    reload_caller_signal = pyqtSignal()
    # Effect preview on a child's device: the master's settings form hosts
    # the button for every device, but the effect must play on the
    # instance that owns the device.  child <- master: run / stop a named
    # preview; master <- child: it finished (so the master can reset the
    # button and slider cues).
    preview_signal = pyqtSignal(str)
    preview_stop_signal = pyqtSignal()
    preview_done_signal = pyqtSignal(str, str)

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
        # A child's whole telemetry frame, for the master's Monitor tab while
        # its config scope is that child's device. Master: which child is
        # being asked, and what it sent. Child: until when it was asked, and
        # how many frames it has sent - see request_child_view.
        self.child_view = ChildTelemView()
        self._view_device = None
        self._view_lease_until = 0.0
        self._view_seq = 0
        self._view_oversize_logged = False

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
            if self._view_device:
                self.send_broadcast_message(f"VIEW TELEM:{self._view_device}")
        else:
            self.send_message(f"Child Keepalive:{G.device_type}:{G.device_connection_status}")
            self.send_ipc_status()

    def send_ipc_status(self):
        """Child -> master device-status report (vpconf profile / gain
        overrides), sent on the keepalive tick.

        The effects payload also carries these keys, but it only flows while
        telemetry is arriving — a startup vpconf profile is pushed before any
        telemetry exists, so without this the master never learns about it
        until an aircraft loads. Riding the 1 Hz keepalive keeps the master
        correct from child boot and self-heals a master restart.
        """
        payload = {
            f'{G.device_type}_vpconf_profile': G.current_vpconf_profile or '',
            f'{G.device_type}_gain_ovd_active':
                bool(G.telem_manager.gain_overrides_active) if G.telem_manager else False,
        }
        self.send_message(f"STATUS:{json.dumps(payload)}")

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
        elif msg.startswith('REACQUIRE:'):
            dev = msg.removeprefix('REACQUIRE:')
            if dev == G.device_type:
                logging.info("Device reacquire command received via IPC - "
                             "switching to the saved device selection")
                self.reacquire_device_signal.emit()
        elif msg.startswith('LOGLEVEL:'):
            # master -> the child owning ``dev``: its log level was changed in the
            # master's system settings, which this instance only reads at startup.
            # Applied here rather than through a signal: the root logger is not Qt.
            try:
                _, dev, level = msg.split(':', 2)
            except ValueError:
                return
            if dev == G.device_type:
                logging.info(f"Log level change received via IPC: {level}")
                utils.apply_log_level(level)
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
        elif msg.startswith('view:'):
            # Kept as text: decoded only if the display asks for it.
            try:
                _, dev, seq, payload = msg.split(':', 3)
                self.child_view.accept(dev, int(seq), payload)
            except ValueError:
                pass
        elif msg.startswith('VIEW TELEM:'):
            if msg.removeprefix('VIEW TELEM:') == G.device_type:
                self._view_lease_until = time.monotonic() + VIEW_LEASE_SEC
            else:
                self._view_lease_until = 0.0
        elif msg.startswith('effects:'):
            payload = msg.removeprefix('effects:')
            try:
                telem_effects_dict = json.loads(payload)
                self._ipc_telem_effects.update(telem_effects_dict)
                # print(f"GOT EFFECTS:{self._ipc_telem_effects}")
                self._report_child_status(telem_effects_dict)

            except json.JSONDecodeError:
                pass
        elif msg.startswith("STATUS:"):
            payload = msg.removeprefix("STATUS:")
            try:
                status_dict = json.loads(payload)
                self._ipc_telem_effects.update(status_dict)
                self._report_child_status(status_dict)
            except json.JSONDecodeError:
                pass
        elif msg.startswith("EXCEPTION:"):
            payload = msg.removeprefix("EXCEPTION:")
            try:
                # Emitted (queued) to the main thread; the tracker and its UI
                # consumers must not be touched from the IPC thread.
                self.child_exception_signal.emit(json.loads(payload))
            except json.JSONDecodeError:
                pass
        elif msg.startswith("LOADCONFIG:"):
            path = msg.removeprefix("LOADCONFIG:")
            load_custom_userconfig(path)
        elif msg == "REAPPLY_AXIS_MAP":
            # a settings save changed an FFB axis mapping somewhere; each
            # instance re-reads ITS OWN settings, so an unchanged map is
            # a no-op.  Flag only - the device work runs on the owning
            # instance's poll tick, never this IPC thread.
            from telemffb.hw.ffb_rhino import HapticEffect
            request = getattr(HapticEffect.device,
                              'request_axis_map_reapply', None)
            if request:
                request()
        elif msg.startswith("MASTER_BUTTONS:"):
            payload = msg.removeprefix("MASTER_BUTTONS:")
            G.master_buttons = json.loads(payload)
            # print(f"MB: {G.master_buttons}")
        elif msg.startswith("TRIMCAL HOLD:"):
            # Master is running a trim calibration: hold/release this
            # instance's trimwheel sim writes. Refreshed ~1/s while active and
            # self-expiring, so a crashed master cannot mute the wheel forever.
            if msg.removeprefix("TRIMCAL HOLD:") == "1":
                G.trimcal_hold_until = time.perf_counter() + 3.0
            else:
                G.trimcal_hold_until = 0.0
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
        elif msg.startswith("PREVIEW:"):
            _, dev, name = msg.split(":", 2)
            if dev == G.device_type:
                self.preview_signal.emit(name)
        elif msg.startswith("PREVIEW STOP:"):
            if msg.removeprefix("PREVIEW STOP:") == G.device_type:
                self.preview_stop_signal.emit()
        elif msg.startswith("PREVIEW DONE:"):
            _, dev, name = msg.split(":", 2)
            self.preview_done_signal.emit(dev, name)
        else:
            logging.info(f"GOT GENERIC MESSAGE: {msg}")

    def _report_child_status(self, payload: dict) -> None:
        """Forward any ``{dev}_vpconf_profile`` / ``{dev}_gain_ovd_active``
        keys in an IPC payload to AppState - both the STATUS keepalive
        (send_ipc_status) and the effects payload (send_ipc_effects) carry
        them. Runs on this IPC thread; AppState is its own lock so this is
        safe cross-thread.

        Master-only in practice: a child never receives these messages
        (children only send them to the master), but the guard mirrors the
        historical read-side check (the old refresh only looked at
        ``_ipc_telem_effects`` when ``G.master_instance``).
        """
        app_state = getattr(G, 'app_state', None)
        if not (self._master and app_state):
            return
        devices = set()
        for key in payload:
            if key.endswith('_vpconf_profile'):
                devices.add(key[:-len('_vpconf_profile')])
            elif key.endswith('_gain_ovd_active'):
                devices.add(key[:-len('_gain_ovd_active')])
        for dev in devices:
            vkey, okey = f'{dev}_vpconf_profile', f'{dev}_gain_ovd_active'
            app_state.set_child_status(
                dev,
                vpconf=payload[vkey] if vkey in payload else None,
                gain_ovd_active=payload[okey] if okey in payload else None,
            )

    def send_ipc_telem(self, telem):
        self.send_message(f"telem:{json.dumps(telem)}")

    # --- a child's telemetry view in the master's Monitor tab ---
    #
    #   master -> children   VIEW TELEM:<device>     (that child starts sending;
    #                                                 any other stops)
    #   child  -> master     view:<device>:<seq>:<json frame>
    #
    # Only the child being watched sends, and only as often as the display
    # refreshes, so the traffic is one child's 20 Hz however many there are.

    def request_child_view(self, device):
        """Master: watch ``device``'s telemetry, or nobody's when None. Said
        at once, then again on each keepalive tick while it holds."""
        if device == self._view_device:
            return
        if self._view_device:
            self.child_view.end(self._view_device)
        self._view_device = device
        self.send_broadcast_message(f"VIEW TELEM:{device or ''}")

    @property
    def view_requested(self):
        return time.monotonic() < self._view_lease_until

    def send_ipc_view(self, telem):
        """Child: this frame, whole, if the master is watching this device."""
        if not self.view_requested:
            return
        # default=str: a frame may carry anything an aircraft class put in
        # it, and a value that reads oddly beats a view that stops.
        message = f"view:{G.device_type}:{self._view_seq + 1}:{json.dumps(dict(telem.items()), default=str)}"
        if len(message) > MAX_DATAGRAM:
            if not self._view_oversize_logged:
                self._view_oversize_logged = True
                logging.warning(f"Telemetry view not sent: a {len(message)} byte frame does not fit one datagram")
            return
        self._view_seq += 1
        self.send_message(message)

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

    # --- effect preview on a child's device (see the preview_* signals) ---

    def send_preview(self, device, name):
        """master -> the child owning ``device``: run the named preview."""
        self.send_broadcast_message(f"PREVIEW:{device}:{name}")

    def send_preview_stop(self, device):
        self.send_broadcast_message(f"PREVIEW STOP:{device}")

    def send_preview_done(self, name):
        """child -> master: this instance's preview finished."""
        self.send_message(f"PREVIEW DONE:{G.device_type}:{name}")

    def send_message(self, message):
        if not self._dstport:
            return
        try:
            self._socket.sendto(message.encode("utf-8"), (self._host, self._dstport))
        except OSError as e:
            logging.warning(f"IPC send error: {e}")

    def child_device_connected(self, role):
        """The device-connection state a child last reported in its
        keepalive, or None when that child has never reported."""
        info = self._child_keepalive_info.get(role)
        return None if info is None else bool(info.get("connected"))

    def send_broadcast_message(self, message):
        for addr in self._child_addrs.values():
            if addr:
                try:
                    self._socket.sendto(message.encode("utf-8"), addr)
                except OSError as e:
                    logging.warning(f"IPC broadcast error: {e}")
