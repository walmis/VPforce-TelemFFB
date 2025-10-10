import telemffb.globals as G
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging
import socket


class MsfsXpSimConnectMixIn(AircraftEffectUtilsBase):
    """Mixin for MSFS SimConnect integration."""

    # user parameters
    local_disable_axis_control: bool = False
    telemffb_controls_axes: bool = False
    # end of user parameters

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._socket = None
        self.__xplane_axis_override_active = False
        self.__xplane_addr = ("127.0.0.1", 34391)

    @property
    def _simconnect(self):
        return G.telem_manager.simconnect

    def send_xp_command(self, cmd):
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        self.toggle_xp_control()
        self._socket.sendto(bytes(cmd, "utf-8"), self.__xplane_addr)

    def toggle_xp_control(self):
        if self.is_collective():
            # issues with axis override for collectve
            return

        if self.is_trimwheel():
            # not implemented
            return

        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)

        if self.is_collective() and not self.is_helicopter():
            # we don't want to send the "prop pitch" override (collective) to XPLANE if we are not in a helo
            if self.telem_data.get("cOvrd", 1):
                sendstr = f"OVERRIDE:{self.telem_data['FFBType']}=false"
                self._socket.sendto(bytes(sendstr, "utf-8"), self.__xplane_addr)
                logging.info(f"Sending to XPLANE: >>{sendstr}<<")
                self.__xplane_axis_override_active = False
            return

        if (
            self.telemffb_controls_axes
            and not self.local_disable_axis_control
            and not self.__xplane_axis_override_active
        ):
            sendstr = f"OVERRIDE:{self.telem_data['FFBType']}=true"
            self._socket.sendto(bytes(sendstr, "utf-8"), self.__xplane_addr)
            logging.info(f"Sending to XPLANE: >>{sendstr}<<")
            self.__xplane_axis_override_active = True
        elif self.__xplane_axis_override_active and not self.telemffb_controls_axes:
            sendstr = f"OVERRIDE:{self.telem_data['FFBType']}=false"
            self._socket.sendto(bytes(sendstr, "utf-8"), self.__xplane_addr)
            logging.info(f"Sending to XPLANE: >>{sendstr}<<")
            self.__xplane_axis_override_active = False
