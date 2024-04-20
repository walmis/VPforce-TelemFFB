import telemffb.globals as G
import telemffb.utils as utils
from telemffb.telem.SimConnectManager import SimConnectManager
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.settingsmanager import utils

from telemffb.telem.TelemManager import TelemManager

class SimConnectSock(SimConnectManager):
    def __init__(self, telem: TelemManager):
        super().__init__()
        telem.set_simconnect(self)
        self._telem = telem


    def fmt(self, val):
        if isinstance(val, list):
            return "~".join([str(x) for x in val])
        return val

    def emit_packet(self, data):
        data["src"] = "MSFS2020"
        packet = bytes(";".join([f"{k}={self.fmt(v)}" for k, v in data.items()]), "utf-8")
        self._telem.submit_frame(packet)

    def emit_event(self, event, *args):
        # special handling of Open event
        if event == "Open":
            # Reset all FFB effects on device, ensure we have a clean start
            HapticEffect.device.resetEffects()
        if event == "Quit":
            utils.signal_emitter.msfs_quit_signal.emit()

        args = [str(x) for x in args]
        self._telem.submit_frame(f"Ev={event};" + ";".join(args))