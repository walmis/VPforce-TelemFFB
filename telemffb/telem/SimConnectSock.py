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


from telemffb.telem.SimConnectManager import SimConnectManager
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.utils import overrides
import telemffb.globals as G

from telemffb.telem.TelemManager import TelemManager

class SimConnectSock(SimConnectManager):
    def __init__(self, telem: TelemManager):
        super().__init__()
        telem.set_simconnect(self)
        self._telem : TelemManager = telem

    def quit(self):
        self._telem.set_simconnect(None)
        super().quit()

    def fmt(self, val):
        if isinstance(val, list):
            return "~".join([str(x) for x in val])
        return val
    
    @overrides(SimConnectManager)
    def emit_packet(self, data):
        data["src"] = "MSFS"
        packet = bytes(";".join([f"{k}={self.fmt(v)}" for k, v in data.items()]), "utf-8")
        self._telem.submit_frame(packet)
    
    @overrides(SimConnectManager)
    def emit_event(self, event, *args):
        # special handling of Open event
        if event == "Open":
            # Reset all FFB effects on device, ensure we have a clean start
            HapticEffect.device.reset_effects()

        if event == "Quit":
            # Restart sim listeners on MSFS quit, TODO: Why?
            G.sim_listeners.restart_all()

        args = [str(x) for x in args]
        self._telem.submit_frame(f"Ev={event};" + ";".join(args))