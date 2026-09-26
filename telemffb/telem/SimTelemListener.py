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
import os
from typing import List, Optional, override

from PyQt6 import QtCore

import telemffb.globals as G
import telemffb.utils as utils
from telemffb.SettingsManager import SettingsManager
from telemffb.telem.IL2Manager import IL2TelemParser
from telemffb.telem.NetworkThread import NetworkThread
from telemffb.telem.UDPForwarder import IL2PacketForwarder
from telemffb.telem.SharedMemThread import SharedMemThread
from telemffb.telem.SimConnectSock import SimConnectSock
from telemffb.telem.DcsIpcThread import DcsIpcThread
from telemffb.telem.BMSTelemManager import BMSManager


class SimTelemListener(QtCore.QObject):
    stateChanged = QtCore.pyqtSignal(bool)

    def __init__(self, name : str):
        super().__init__()
        self.name : str = name
        self.telem : NetworkThread = None
        self._started = False

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def validate(self):
        raise NotImplementedError

    def do_validate(self) -> bool:
        if G.child_instance:
            return None
        if G.system_settings.get(f'validate{self.name}'):
            self.validate()
            return True
        return False

    @property
    def started(self) -> bool:
        return self._started

    @started.setter
    def started(self, value : bool):
        """
        Mark instance as started, will emit a stateChanged signal on change
        """
        if bool(self._started) ^ bool(value):
            self._started = value
            self.stateChanged.emit(value)

    @property
    def is_enabled(self) -> bool:
        # No sim is gated for DirectInput devices: sims that render their
        # own native FFB (DCS/IL-2/BMS) work on a DI device with the
        # tap/sink dinput8 wrapper in the game folder (native FFB absorbed;
        # tap mode mirrors the game spring for the 'FFB Telemetry (Game
        # Managed)' spring mode).  Without the wrapper, the game taking
        # foreground FFB priority surfaces as an actionable error in the
        # exception tracker (see ffb_dinput's DIB_ERR_ACQUISITION handling).
        return bool(SettingsManager.sim_enabled(self.name) or G.args.sim == self.name)

    @property
    def port_udp(self):
        port = int(G.system_settings.get(f'port{self.name}'))
        assert port
        return port


class _SimIL2Base(SimTelemListener):
    """IL-2 Great Battles and IL-2 Korea speak the same telemetry protocol
    and share the IL2 tab, its enable toggle and the forwarder settings.
    Nothing in the packets says which game sent them, so the config
    validator gives each game its own UDP port and each gets its own
    listener; frames are tagged with the listener's source key.
    """
    label: str          # for log lines and the validator's dialogs
    #: the game also publishes ffbdevice records, whose config section the
    #: validator maintains alongside telemetry and motion
    ffb_stream = False

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._forwarder = IL2PacketForwarder()

    @property
    def startup_cfg(self) -> str:
        raise NotImplementedError

    @override
    def start(self):
        if not self.is_enabled:
            return

        self.telem = NetworkThread(G.telem_manager, host="127.0.0.1", port=self.port_udp,
                                    telem_parser=IL2TelemParser(src=self.name),
                                    raw_packet_hook=self._forwarder.forward)

        if self.do_validate() is False:
            logging.warning(
                f"{self.label} config validation is disabled - please ensure its startup.cfg is configured correctly")

        if self.telem is None:  # stopped during validation (e.g. another sim won the race)
            return

        logging.info(f"Starting {self.label} Telemetry Listener")
        self.telem.start()
        self.started = True

    @override
    def validate(self):
        logging.info(f"Validating {self.label} Telemetry Config")
        utils.analyze_il2_config(self.startup_cfg, port=self.port_udp, window=G.main_window,
                                 sim_name=self.label, korea=self.ffb_stream)

    @override
    def stop(self):
        logging.info(f"Stopping {self.label} Telemetry Listener")
        if self.telem:
            self.telem.quit()
            self.telem = None
            self.started = False
        self._forwarder.close()


class SimIL2(_SimIL2Base):
    label = "IL-2 Sturmovik"

    def __init__(self) -> None:
        super().__init__("IL2")

    @property
    def startup_cfg(self) -> str:
        return os.path.join(G.system_settings.get('pathIL2'), 'data\\startup.cfg')


class SimIL2K(_SimIL2Base):
    """IL-2 Korea.  Shares the IL2 tab: enabled by the IL2 toggle once a
    Korea path is set, validated by its own checkbox, listening on its own
    port (portIL2_K).  A Korea install whose startup.cfg still names the
    shared port keeps working through SimIL2, just not as IL2K.
    """
    label = "IL-2 Korea"
    ffb_stream = True

    def __init__(self) -> None:
        super().__init__("IL2K")

    @property
    def port_udp(self):
        port = int(G.system_settings.get('portIL2_K'))
        assert port
        return port

    @override
    def do_validate(self) -> bool:
        if G.child_instance:
            return None
        if G.system_settings.get('validateIL2_K'):
            self.validate()
            return True
        return False

    @property
    def startup_cfg(self) -> str:
        # Standalone: <root>\game\data; Steam (IL2Series): <root>\data.
        game_root = utils.il2_korea_game_root(G.system_settings.get('pathIL2_K'))
        return os.path.join(game_root, 'data', 'startup.cfg')

    @override
    def start(self):
        super().start()
        if self.started and G.device_info:
            G.il2_ffb_device_ordinal = utils.resolve_il2_ffb_device_ordinal(
                G.system_settings.get('pathIL2_K'), G.device_info.vendor_id, G.device_info.product_id
            )


class SimBMS(SimTelemListener):
    def __init__(self) -> None:
        super().__init__("BMS")

    @override
    def start(self):
        if not self.is_enabled:
            return
        self.telem = SharedMemThread(G.telem_manager, telem_parser=BMSManager())
        logging.info("Starting BMS Telemetry Listener")
        self.telem.start()
        self.started = True

    @override
    def validate(self):
        return

    @override
    def stop(self):
        logging.info("Stopping BMS Telemetry Listener")
        if self.telem:
            self.telem.quit()
            self.telem = None
            self.started = False


class SimDCS(SimTelemListener):
    def __init__(self) -> None:
        super().__init__("DCS")
        self.telem_ipc : Optional[DcsIpcThread] = None
        self.telem_udp : Optional[NetworkThread] = None # deprecated

    @override
    def start(self):
        if not self.is_enabled:
            return

        self.telem_udp = NetworkThread(G.telem_manager, host="127.0.0.1", port=34380)
        self.telem_ipc = DcsIpcThread(G.telem_manager)

        self.do_validate()

        if self.telem_ipc is None or self.telem_udp is None:  # stopped during validation (e.g. another sim won the race)
            return

        logging.info("Starting DCS Telemetry Listener")
        self.telem_ipc.start()
        self.telem_udp.start()
        self.started = True

    @override
    def validate(self):
        # check and install/update export lua script
        logging.info("Checking DCS export script")
        if G.system_settings.get('dbg_use_dll') is None:
            G.system_settings.setValue('dbg_use_dll', True)

        use_dll = G.system_settings.get('dbg_use_dll', False)

        if use_dll:
            utils.install_dcs_export_module_dll(G.main_window)
        else:
            utils.install_dcs_export_module_lua(G.main_window)

    @override
    def stop(self):
        logging.info("Stopping DCS Telemetry Listener")
        if self.telem_udp:
            self.telem_udp.quit()
            self.telem_udp = None
        if self.telem_ipc:
            self.telem_ipc.quit()
            self.telem_ipc = None
            self.started = False

class SimXPLANE(SimTelemListener):
    def __init__(self) -> None:
        super().__init__("XPLANE")
        self.telem : Optional[NetworkThread] = None

    @override
    def start(self):
        if not self.is_enabled:
            return

        self.telem = NetworkThread(G.telem_manager, host='127.0.0.1', port=34390)

        self.do_validate()

        if self.telem is None:
            return

        logging.info("Starting XPlane Telemetry Listener")

        self.telem.start()
        self.started = True

    @override
    def validate(self):
        logging.info("Checking XPlane Plugin")
        xplane_path = G.system_settings.get('pathXPLANE', '')
        utils.install_xplane_plugin(xplane_path, G.main_window)

    @override
    def stop(self):
        logging.info("Stopping XPlane Telemetry Listener")
        if self.telem:
            self.telem.quit()
            self.telem = None
            self.started = False

class SimMSFS(SimTelemListener):
    def __init__(self) -> None:
        super().__init__("MSFS")
        self.telem : Optional[SimConnectSock] = None

    @override
    def start(self):
        if not self.is_enabled:
            return

        self.telem = SimConnectSock(G.telem_manager)

        self.telem.start()
        self.started = True

    @override
    def stop(self):
        logging.info("Stopping MSFS Telemetry Listener")
        if self.telem:
            self.telem.quit()
            self.telem = None
            self.started = False
    
    @override
    def validate(self):
        return

class SimListenerManager(QtCore.QObject):
    simStarted = QtCore.pyqtSignal(object)
    simStopped = QtCore.pyqtSignal(object)
    allStarted = QtCore.pyqtSignal()

    def __init__(self, parent = None):
        super().__init__(parent)
        self.sims : List[SimTelemListener] = [
            SimDCS(),
            SimMSFS(),
            SimIL2(),
            SimIL2K(),
            SimBMS(),
            SimXPLANE()
        ]

        for sim in self.sims:
            sim.stateChanged.connect(self._on_state_changed)

    def _on_state_changed(self, state):
        if state:
            self.simStarted.emit(self.sender())
        else:
            self.simStopped.emit(self.sender())

    def stop_inactive(self, active_sim):
        logging.info(f"Started receiving telemetry from {active_sim} - stopping other sim listeners")
        for sim in self.sims:
            if sim.name != active_sim:
                sim.stop()
    def start_all(self):
        self.allStarted.emit()
        for sim in self.sims:
            sim.start()

    def stop_all(self):
        for sim in self.sims:
            sim.stop()

    def restart_all(self):
        self.stop_all()
        self.start_all()