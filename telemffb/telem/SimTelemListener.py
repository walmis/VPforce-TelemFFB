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
from typing import List

from PyQt6 import QtCore

import telemffb.globals as G
import telemffb.utils as utils
from telemffb.sim.aircrafts_msfs_xp import Aircraft
from telemffb.telem.IL2Manager import IL2Manager
from telemffb.telem.NetworkThread import NetworkThread
from telemffb.telem.SimConnectSock import SimConnectSock
from telemffb.utils import overrides


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
        return bool(G.system_settings.get(f'enable{self.name}') or G.args.sim == self.name)

    @property
    def port_udp(self):
        port = int(G.system_settings.get(f'port{self.name}'))
        assert port
        return port


class SimIL2(SimTelemListener):
    def __init__(self) -> None:
        super().__init__("IL2")

    @overrides(SimTelemListener)
    def start(self):
        if not self.is_enabled:
            return

        self.telem = NetworkThread(G.telem_manager, host="127.0.0.1", port=self.port_udp, telem_parser=IL2Manager())

        if self.do_validate() is False:
            logging.warning(
                "IL2 Config validation is disabled - please ensure the IL2 startup.cfg is configured correctly")

        logging.info("Starting IL2 Telemetry Listener")
        self.telem.start()
        self.started = True

    @overrides(SimTelemListener)
    def validate(self):
        il2_path = G.system_settings.get('pathIL2')
        logging.info("Validating IL2 Config")
        utils.analyze_il2_config(il2_path, port=self.port_udp, window=G.main_window)

    @overrides(SimTelemListener)
    def stop(self):
        if self.telem:
            self.telem.quit()
            self.telem = None
            self.started = False

class SimDCS(SimTelemListener):
    def __init__(self) -> None:
        super().__init__("DCS")
        self.telem : NetworkThread = None

    @overrides(SimTelemListener)
    def start(self):
        if not self.is_enabled:
            return

        self.telem = NetworkThread(G.telem_manager, host="127.0.0.1", port=34380)

        self.do_validate()
        logging.info("Starting DCS Telemetry Listener")
        self.telem.start()
        self.started = True

    @overrides(SimTelemListener)
    def validate(self):
        # check and install/update export lua script
        logging.info("Checking DCS export script")
        utils.install_export_lua(G.main_window)

    @overrides(SimTelemListener)
    def stop(self):
        if self.telem:
            self.telem.quit()
            self.telem = None
            self.started = False

class SimXPLANE(SimTelemListener):
    def __init__(self) -> None:
        super().__init__("XPLANE")
        self.telem : NetworkThread = None

    @overrides(SimTelemListener)
    def start(self):
        if not self.is_enabled:
            return

        self.telem = NetworkThread(G.telem_manager, host='127.0.0.1', port=34390)

        self.do_validate()
        logging.info("Starting XPlane Telemetry Listener")

        self.telem.start()
        self.started = True

    @overrides(SimTelemListener)
    def validate(self):
        logging.info("Checking XPlane Plugin")
        xplane_path = G.system_settings.get('pathXPLANE', '')
        utils.install_xplane_plugin(xplane_path, G.main_window)

    @overrides(SimTelemListener)
    def stop(self):
        if self.telem:
            self.telem.quit()
            self.telem = None
            self.started = False

class SimMSFS(SimTelemListener):
    def __init__(self) -> None:
        super().__init__("MSFS")
        self.telem : NetworkThread = None

    @overrides(SimTelemListener)
    def start(self):
        if not self.is_enabled:
            return

        self.telem = SimConnectSock(G.telem_manager)

        self.telem.start()
        Aircraft.set_simconnect(self.telem)
        self.started = True

    @overrides(SimTelemListener)
    def stop(self):
        if self.telem:
            self.telem.quit()
            self.telem = None
            Aircraft.set_simconnect(None)
            self.started = False
    
    @overrides(SimTelemListener)
    def validate(self):
        return

class SimListenerManager(QtCore.QObject):
    simStarted = QtCore.pyqtSignal(object)
    simStopped = QtCore.pyqtSignal(object)

    def __init__(self, parent = None):
        super().__init__(parent)
        self.sims : List[SimTelemListener] = [
            SimDCS(),
            SimMSFS(),
            SimIL2(),
            SimXPLANE()
        ]

        for sim in self.sims:
            sim.stateChanged.connect(self._on_state_changed)

    def _on_state_changed(self, state):
        if state:
            self.simStarted.emit(self.sender())
        else:
            self.simStopped.emit(self.sender())

    def start_all(self):
        for sim in self.sims:
            sim.start()

    def stop_all(self):
        for sim in self.sims:
            sim.stop()

    def restart_all(self):
        self.stop_all()
        self.start_all()