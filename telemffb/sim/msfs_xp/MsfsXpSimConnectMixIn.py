from __future__ import annotations

import json
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
    joystick_trim_follow_use_curve_y: bool = False
    # Where the trimmed stick RESTS in curve mode: "Follows Trim" (tab/cable
    # aircraft — rest position rides the measured elevator-equivalent of the
    # trim state, aft when trimmed slow) or "Stays Centered" (stabilizer/
    # FBW/spring-cartridge — trim re-rigs the feel datum). Force behavior is
    # identical in both; only the resting geometry differs. Class-defaulted
    # (JetAircraft => Stays Centered) in defaults.xml.
    joystick_trim_follow_stick_position: str = "Follows Trim"
    _xplane_event_states: dict = {}
    # end of user parameters

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._socket = None
        self.__xplane_axis_override_active = False
        self.__xplane_addr = ("127.0.0.1", 34391)
        self._trim_curve_y_json: Optional[str] = None
        self._trim_curve_y_fam = None   # parsed multi-speed family (utils.parse_trim_follow_family)
        self._trim_curve_y_pts = None   # transitional single-curve view (median entry) for display
        self._trim_blend_last_v = None  # last valid IAS (kt) fed to the blend — in-frame dropout guard
        self._simconnect_proxy = SimConnectProxy(lambda: G.telem_manager.simconnect if G.telem_manager else None)

    @property
    def joystick_trim_follow_curve_y(self) -> Optional[str]:
        return self._trim_curve_y_json

    @joystick_trim_follow_curve_y.setter
    def joystick_trim_follow_curve_y(self, value):
        """Assigned a JSON-encoded calibration curve family (or a legacy
        single curve, or 'none') via the settings subsystem. Parsed ONCE here
        into anchor-referenced lookup structures — never in the telemetry hot
        path. The parse/rebase/positional-track convention lives in
        :func:`telemffb.utils.parse_trim_follow_family` (shared with the
        calibration dialog). Invalid/short curves are ignored (runtime falls
        back to the static virtual gain).
        """
        self._trim_curve_y_json = None
        self._trim_curve_y_fam = None
        self._trim_curve_y_pts = None
        self._trim_blend_last_v = None
        fam = utils.parse_trim_follow_family(value)
        if fam is None:
            return
        self._trim_curve_y_json = value if isinstance(value, str) else json.dumps(value)
        self._trim_curve_y_fam = fam
        mid = fam[len(fam) // 2]
        self._trim_curve_y_pts = (mid["xs"], mid["ys"])

    def _trim_curve_offset(self, t: float) -> Optional[float]:
        """Single-curve virtual-offset lookup at trim ``t`` (the family's
        median entry), or None when no curve is loaded. Display/legacy view —
        the runtime path is the speed-blended lookup in
        :meth:`_trim_follow_virtual_offset_y`."""
        if self._trim_curve_y_pts is None:
            return None
        xs, ys = self._trim_curve_y_pts
        return utils.clamp(utils.piecewise_linear(xs, ys, t), -1.0, 1.0)

    def _trim_follow_virtual_offset_y(self, t_damp: float, elev_trim: float,
                                      telem_data=None) -> float:
        """Virtual stick offset for elevator trim-following.

        Calibrated curve family (absolute axis units, independent of the
        physical gain) when enabled and loaded — blended across the stored
        calibration speeds by the current IAS (anchor-aligned; exact nearest
        entry beyond the calibrated speed range), with the positional track
        folded in unless the aircraft's trimmed-stick-position mode is
        "Stays Centered". Else the legacy static-gain formula.
        Enabled-but-missing curve flags a UI error each frame (the standing
        convention: the notification lives while the misconfiguration does)
        and falls back to the static gain.

        In-frame IAS guard: a frame that arrives with a missing/zero IAS
        reuses the last valid speed instead of snapping the blend to the
        slowest entry (a real delivered-axis step mid-flight). Full
        telemetry loss stops the whole loop, so no further guard is needed.

        :param t_damp: dampened raw ElevTrimPct (curve lookup space)
        :param elev_trim: t_damp scaled by the physical gain and clamped
            (legacy formula / spring-center space)
        :param telem_data: current frame (for IAS); None (tests, callers
            without a frame) behaves like a missing-IAS frame
        """
        if self.joystick_trim_follow_use_curve_y:
            fam = self._trim_curve_y_fam
            if fam is not None:
                ias = getattr(telem_data, "IAS", None) if telem_data is not None else None
                if ias:
                    v = ias * 1.94384  # m/s -> kt, the family's speed unit
                    self._trim_blend_last_v = v
                else:
                    v = self._trim_blend_last_v
                    if v is None:
                        v = fam[0]["ias_kt"]
                include_r = \
                    self.joystick_trim_follow_stick_position != "Stays Centered"
                return utils.trim_follow_blend(fam, t_damp, v, include_r)
            self.flag_error(
                "'Use Calibrated Trim Curve' is enabled but no calibration is stored "
                "for this aircraft.\nRun the Elevator Trim Calibration or disable the "
                "option.\nUsing the static Y Trim Gain Virtual value instead.")
        return elev_trim - (elev_trim * self.joystick_trim_follow_gain_virtual_y)

    def _trim_follow_center_y(self, elev_trim: float, virt_offs: float) -> float:
        """Spring-center position for elevator trim-following.

        Curve mode: the center walks the measured level-hold curve in AXIS
        units — center(T) = clamp(P * offs(T)) — so trimming relieves a held
        stick's force at the aircraft's true trim-vs-elevator authority rate
        (real trim replaces elevator 1:1 in elevator units). The raw-trim
        center (P * T) under-relieved by the aircraft's slope k:
        imperceptible at k~1 (standard aircraft, where this reduces to ~P*T)
        but a dead trim wheel at k~19 (Hawk T1).

        The curve is anchor-referenced at parse time (offs(t0) == 0), which
        makes one invariant exact everywhere: trimmed for level => stick at
        physical center, zero force, zero delivered input; deviation from
        center = the un-trimmed load. Position and force both mean "how out
        of trim you are", at any speed.

        Legacy/static mode (or curve enabled but missing): the raw-trim
        center, unchanged.

        :param elev_trim: legacy center — t_damp scaled by the physical gain
        :param virt_offs: this frame's virtual offset (the curve lookup when
            the curve is active — reused so the hot path adds no lookup)
        """
        if self.joystick_trim_follow_use_curve_y and self._trim_curve_y_fam is not None:
            return utils.clamp(
                virt_offs * self.joystick_trim_follow_gain_physical_y, -1.0, 1.0)
        return elev_trim

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
        logging.debug(command)
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
