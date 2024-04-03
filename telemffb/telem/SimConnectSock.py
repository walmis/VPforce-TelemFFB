import telemffb.globals as G
import telemffb.utils as utils
from telemffb.telem.SimConnectManager import SimConnectManager
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.settingsmanager import utils

from telemffb.telem.TelemManager import TelemManager

class SimConnectSock(SimConnectManager):
    def __init__(self, telem: TelemManager, ffb_type=G._device_type, unique_id=None):
        if not unique_id:
            # TODO: maybe use process PID here?
            unique_id = int(G._device_pid)

        super().__init__(unique_id)
        telem.set_simconnect(self)
        self._telem = telem
        self._ffb_type = ffb_type


    def fmt(self, val):
        if isinstance(val, list):
            return "~".join([str(x) for x in val])
        return val

    def emit_packet(self, data):
        data["src"] = "MSFS2020"
        packet = bytes(";".join([f"{k}={self.fmt(v)}" for k, v in data.items()]), "utf-8")
        self._telem.submitFrame(packet)

    def emit_event(self, event, *args):
        # special handling of Open event
        if event == "Open":
            # Reset all FFB effects on device, ensure we have a clean start
            HapticEffect.device.resetEffects()
        if event == "Quit":
            utils.signal_emitter.msfs_quit_signal.emit()

        args = [str(x) for x in args]
        self._telem.submitFrame(f"Ev={event};" + ";".join(args))