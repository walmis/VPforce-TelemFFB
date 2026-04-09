from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING, Any, Callable, Optional

import telemffb.globals as G
from telemffb.hw.ffb_rhino import HapticEffect
import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase

if TYPE_CHECKING:
    from telemffb.telem.SimConnectManager import SimConnectManager


class SimConnectProxy(object):
    """Proxy object that handles None simconnect gracefully.

    At runtime, delegates attribute access to the SimConnectManager
    instance returned by the getter, or returns a no-op callable
    when SimConnect is unavailable.

    For type-checking, inherits from SimConnectManager so Pylance
    can provide full autocompletion on the proxied methods.
    """

    def __init__(self, simconnect_getter: Callable[[], Optional[SimConnectManager]]) -> None:
        self._simconnect_getter = simconnect_getter

    def __getattr__(self, name: str) -> Any:
        simconnect = self._simconnect_getter()
        if simconnect is None:
            logging.warning(f"SimConnect is None, cannot access attribute/method: {name}")
            return lambda *args, **kwargs: None
        return getattr(simconnect, name)

    def __bool__(self) -> bool:
        return self._simconnect_getter() is not None


class MsfsXpSimConnectMixIn(AircraftEffectUtilsBase):
    """Mixin for MSFS SimConnect integration."""

    # user parameters
    local_disable_axis_control: bool = False
    telemffb_controls_axes: bool = False
    use_firmware_axis_override: bool = False
    _xplane_event_states: dict = {}
    # end of user parameters

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._socket = None
        self.__xplane_axis_override_active = False
        self.__xplane_addr = ("127.0.0.1", 34391)
        self._simconnect_proxy = SimConnectProxy(lambda: G.telem_manager.simconnect if G.telem_manager else None)

    @property
    def _simconnect(self) -> SimConnectManager:
        return self._simconnect_proxy # type: ignore

    def send_xp_command(self, cmd):
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        if self._use_sim_axis_backend():
            self.toggle_xp_control()
        self._socket.sendto(bytes(cmd, "utf-8"), self.__xplane_addr)

    def _axis_control_enabled(self) -> bool:
        return self.telemffb_controls_axes and not self.local_disable_axis_control

    def _firmware_axis_override_supported(self) -> bool:
        if not HapticEffect.device:
            return False
        return HapticEffect.device.supports_axis_override()

    def _use_firmware_axis_backend(self) -> bool:
        if not (self._axis_control_enabled() and self.use_firmware_axis_override):
            return False
        supported = self._firmware_axis_override_supported()
        if not supported:
            logging.warning("Firmware axis override requested but not supported; falling back to simulator backend")
        return supported

    def _use_sim_axis_backend(self) -> bool:
        return self._axis_control_enabled() and not self._use_firmware_axis_backend()

    def _send_firmware_axis_override(self, x_mode=0, x_value=0, y_mode=0, y_value=0, watchdog_ms=1000):
        if not self._use_firmware_axis_backend():
            return False

        device = HapticEffect.device
        if device is None:
            return False
        device.send_axis_override(
            x_mode=x_mode,
            x_value=int(x_value),
            y_mode=y_mode,
            y_value=int(y_value),
            watchdog_ms=watchdog_ms,
        )
        return True

    def _clear_firmware_axis_override(self):
        device = HapticEffect.device
        if device is None:
            return
        clear = getattr(device, "clear_axis_override", None)
        if callable(clear):
            clear()

    def _send_firmware_fixed_axes(
        self,
        x_value: float | None = None,
        y_value: float | None = None,
        watchdog_ms: int = 1000,
    ) -> bool:
        def normalize(value: float | None) -> tuple[int, int]:
            if value is None:
                return 0, 0
            return 2, int(round(max(-1.0, min(1.0, value)) * 4096))

        x_mode, x_fixed = normalize(x_value)
        y_mode, y_fixed = normalize(y_value)
        return self._send_firmware_axis_override(
            x_mode=x_mode,
            x_value=x_fixed,
            y_mode=y_mode,
            y_value=y_fixed,
            watchdog_ms=watchdog_ms,
        )

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
        if self._use_firmware_axis_backend():
            if self.__xplane_axis_override_active and self._socket is not None:
                sendstr = f"OVERRIDE:{self.telem_data.FFBType}=false"
                self._socket.sendto(bytes(sendstr, "utf-8"), self.__xplane_addr)
                logging.info(f"Sending to XPLANE: >>{sendstr}<<")
                self.__xplane_axis_override_active = False
            return

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
            if self.telem_data.get("cOvrd", 1):  # non-zero default: keep .get()
                sendstr = f"OVERRIDE:{self.telem_data.FFBType}=false"
                self._socket.sendto(bytes(sendstr, "utf-8"), self.__xplane_addr)
                logging.info(f"Sending to XPLANE: >>{sendstr}<<")
                self.__xplane_axis_override_active = False
            return

        if (
            self.telemffb_controls_axes
            and not self.local_disable_axis_control
            and not self.__xplane_axis_override_active
        ):
            sendstr = f"OVERRIDE:{self.telem_data.FFBType}=true"
            self._socket.sendto(bytes(sendstr, "utf-8"), self.__xplane_addr)
            logging.info(f"Sending to XPLANE: >>{sendstr}<<")
            self.__xplane_axis_override_active = True
        elif self.__xplane_axis_override_active and not self.telemffb_controls_axes:
            sendstr = f"OVERRIDE:{self.telem_data.FFBType}=false"
            self._socket.sendto(bytes(sendstr, "utf-8"), self.__xplane_addr)
            logging.info(f"Sending to XPLANE: >>{sendstr}<<")
            self.__xplane_axis_override_active = False
