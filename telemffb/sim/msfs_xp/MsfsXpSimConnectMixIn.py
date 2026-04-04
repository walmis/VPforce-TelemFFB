import telemffb.globals as G
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging
import socket


class SimConnectProxy:
    """Proxy object that handles None simconnect gracefully."""
    
    def __init__(self, simconnect_getter):
        self._simconnect_getter = simconnect_getter
    
    def __getattr__(self, name):
        simconnect = self._simconnect_getter()
        if simconnect is None:
            logging.warning(f"SimConnect is None, cannot access attribute/method: {name}")
            # Return a callable that does nothing for method calls
            return lambda *args, **kwargs: None
        return getattr(simconnect, name)
    
    def __bool__(self):
        return self._simconnect_getter() is not None


class MsfsXpSimConnectMixIn(AircraftEffectUtilsBase):
    """Mixin for MSFS SimConnect integration."""

    # user parameters
    local_disable_axis_control: bool = False
    telemffb_controls_axes: bool = False
    _xplane_event_states: dict = {}
    # end of user parameters

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._socket = None
        self.__xplane_axis_override_active = False
        self.__xplane_addr = ("127.0.0.1", 34391)
        self._simconnect_proxy = SimConnectProxy(lambda: G.telem_manager.simconnect if G.telem_manager else None)

    @property
    def _simconnect(self):
        return self._simconnect_proxy

    def send_xp_command(self, cmd):
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        self.toggle_xp_control()
        self._socket.sendto(bytes(cmd, "utf-8"), self.__xplane_addr)

    def write_xp_dataref(self, dataref, value, type="int"):
        command = f"WRITE:dataref={dataref},value={value},type={type}"
        print(f"WRITE:dataref={dataref},value={value},type={type}")
        self.send_xp_command(command)

    def trigger_xp_event(self, dataref, state: bool, type="track"):
        if type == "once" or type == "begin" or type == "end":
            command = f"COMMAND:cmd={dataref},phase={type}"
            self.send_xp_command(command)
        else:  # "track" mode
            last_state = self._xplane_event_states.get(dataref)  # None if never seen

            if state and last_state is not True:
                # Transition to True → send "begin"
                command = f"COMMAND:cmd={dataref},phase=begin"
                print(f"COMMAND:cmd={dataref},phase=begin")
                self.send_xp_command(command)
                self._xplane_event_states[dataref] = True

            elif not state and last_state is not False:
                # Transition to False → send "end"
                command = f"COMMAND:cmd={dataref},phase=end"
                print(f"COMMAND:cmd={dataref},phase=end")
                self.send_xp_command(command)
                self._xplane_event_states[dataref] = False

            # If state matches last_state, do nothing (no change)

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
