from __future__ import annotations

import json
import logging
import socket
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

import telemffb.globals as G
from telemffb.hw.ffb_rhino import HapticEffect
import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase

if TYPE_CHECKING:
    from telemffb.telem.SimConnectManager import SimConnectManager

# The X-Plane plugin echoes the live value of each override dataref back in
# every telemetry frame.  Maps this instance's device role to the key carrying
# its state, so the desired override can be reconciled against what the sim
# actually has latched instead of against a local assumption.
XP_OVERRIDE_STATE_KEYS: dict[str, str] = {
    "joystick": "jOvrd",
    "pedals": "pOvrd",
    "collective": "cOvrd",
}

# How long to wait for the plugin to apply a sent OVERRIDE command before
# trusting its echoed state again.  The plugin queues inbound commands and
# applies them on its next flight loop, so frames already in flight still
# carry the pre-command value; without this guard that stale mismatch would
# re-send the command on every telemetry frame.
XP_OVERRIDE_ECHO_GRACE_S = 0.5


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
    # Curve-mode stick travel ("Calibrated Trim Stick Travel"): scales the
    # spring CENTER only, so it sets how far the stick sits from center for a
    # given out-of-trim state — i.e. the force you hold against. 1.0 = the
    # aircraft's measured response. Deliberately NOT the legacy
    # joystick_trim_follow_gain_physical_y: that one is signed (-100..100,
    # inverted trim following) and drives the legacy center, while this is
    # 0..200 and only meaningful with a calibration loaded. See
    # :meth:`_trim_follow_center_y` for what values != 1.0 cost.
    joystick_trim_follow_curve_gain_y: float = 1.0
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
        self._fw_override_supported_last = None
        # Monotonic stamp of the last OVERRIDE command sent; -inf means
        # nothing sent yet, so the plugin's echoed state is trusted at once.
        self.__xplane_override_sent_at = float("-inf")

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
        prev_json = self._trim_curve_y_json
        self._trim_curve_y_json = None
        self._trim_curve_y_fam = None
        self._trim_curve_y_pts = None
        self._trim_blend_last_v = None
        fam = utils.parse_trim_follow_family(value)
        if fam is None:
            # Log only a real clear (had curves, now none) — every uncurved
            # aircraft passes through here on load and would spam otherwise.
            if prev_json is not None:
                logging.info("Trim calibration curves cleared")
            return
        self._trim_curve_y_json = value if isinstance(value, str) else json.dumps(value)
        self._trim_curve_y_fam = fam
        mid = fam[len(fam) // 2]
        self._trim_curve_y_pts = (mid["xs"], mid["ys"])
        if self._trim_curve_y_json != prev_json:
            speeds = ", ".join(f"{e['ias_kt']:.0f}" for e in fam)
            logging.info(f"Trim calibration curves loaded: {len(fam)} "
                         f"speed{'s' if len(fam) != 1 else ''} ({speeds} kt)")

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

        This value is deliberately UNSCALED by the physical gain, and must
        stay that way: it is the measured elevator-equivalent of the trim,
        so subtracting it is exactly what cancels the trim's effect on the
        aircraft. That is the feature's primary invariant — trim with the
        stick HELD and the nose does not move. Scaling it by a gain P
        cancels P times the trim's effect, so at P=2 trimming drives the
        nose the wrong way (field report 2026-07-26). The physical gain
        therefore only scales the spring CENTER (see
        :meth:`_trim_follow_center_y`), and in curve mode any value other
        than 1.0 trades one invariant for the other — see that method.
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

        STICK TRAVEL (``joystick_trim_follow_curve_gain_y``, P) — what it
        does and what it costs. P scales the CENTER only; the delivered-input
        datum (the virtual offset) is never scaled. What P reaches depends on
        the position mode, because the trimmed rest position differs:
          * Stays Centered: trimmed rest is 0 at every speed, so P scales
            ONLY the out-of-trim displacement (and thus the force).
          * Follows Trim: trimmed rest is R(v), so P scales the out-of-trim
            force AND how far the resting stick rides with airspeed —
            the reason lowering it reclaims aft travel in slow flight.
        Two invariants are in play, and P == 1.0 (the default) is the only
        value satisfying both everywhere:
          * stick HELD, trim changed -> nose must not move (the feature's
            primary invariant). Holds EXACTLY at ANY P: delivered input
            never involves P.
          * hands OFF -> the resting stick must deliver zero. The stick
            rests at P*offs but the datum is offs, so rest delivers
            offs*(P-1): zero only at P == 1, or wherever offs == 0.
        So P is a usable feel knob — it directly scales the out-of-trim
        force, |x - P*offs|*coeff — at a bounded, known cost: hands-off
        accuracy. How bounded depends on the position mode:
          * Stays Centered: trimmed offs == 0 at every speed, so the leak
            exists only while OUT of trim (transient states).
          * Follows Trim: trimmed offs == R(v), nonzero away from the
            middle calibration speed — at P != 1 a TRIMMED hands-off
            aircraft drifts at speed extremes. The costlier mode for P != 1.
        The cost-free way to heavier out-of-trim force is the elevator
        SPRING GAIN: force = (x - center)*coefficient changes feel without
        moving the neutral point either invariant depends on.

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
                virt_offs * self.joystick_trim_follow_curve_gain_y, -1.0, 1.0)
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
        if not HapticEffect.device.connected:
            return False
        return HapticEffect.device.supports_axis_override()

    def _use_firmware_axis_backend(self) -> bool:
        if not (self._axis_control_enabled() and self.use_firmware_axis_override):
            return False
        supported = self._firmware_axis_override_supported()
        if supported != self._fw_override_supported_last:
            self._fw_override_supported_last = supported
            if not supported:
                logging.warning(
                    "Firmware axis override requested but not supported; "
                    "falling back to simulator backend")
        return supported

    def _use_sim_axis_backend(self) -> bool:
        return self._axis_control_enabled() and not self._use_firmware_axis_backend()

    def _device_feeding(self) -> bool:
        """Whether the FFB device is connected and delivering HID input.

        Axis overrides must never be claimed without a live device: the
        plugin pins the virtual yoke/rudder to whatever we send, so a dead
        instance would feed zeros/stale values and silence the user's real
        controller. Re-checked every frame, so a hot-unplug releases the
        override and a reconnect reclaims it.
        """
        device = HapticEffect.device
        if device is None or not device.connected:
            return False
        return device.get_input() is not None

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

    def _xp_role(self) -> str:
        """Return the override keyword the plugin knows this device by.

        Read and write must agree on one string: reading state under one role
        while sending commands under another would leave the two permanently
        out of step, and the mismatch would be re-sent every grace period.
        """
        return self.telem_data.FFBType or "joystick"

    def _xp_reported_override_state(self) -> Optional[bool]:
        """Return the override state the X-Plane plugin reports for this device.

        The plugin publishes the live value of each override dataref in every
        telemetry frame (``jOvrd`` / ``pOvrd`` / ``cOvrd``).  That value is the
        authority on what the sim actually has latched — unlike the local
        "override active" flag, which only records what this process last sent
        and is wrong whenever the state changed behind our back: a TelemFFB
        restart, or the plugin's "Clear All Overrides" menu item.

        :return: the reported state, or ``None`` when the frame carries no
            state for this role (older plugin build, or an untracked role).
        """
        key = XP_OVERRIDE_STATE_KEYS.get(self._xp_role())
        if key is None:
            return None
        value = self.telem_data.get(key, None)
        if value is None:
            return None
        try:
            return bool(int(value))
        except (TypeError, ValueError):
            return None

    def _send_xp_override(self, active: bool) -> None:
        """Send an OVERRIDE command for this device's role to the X-Plane plugin.

        :param active: ``True`` hands the axis to TelemFFB, ``False`` gives it
            back to the physical control.
        """
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        sendstr = f"OVERRIDE:{self._xp_role()}={'true' if active else 'false'}"
        self._socket.sendto(bytes(sendstr, "utf-8"), self.__xplane_addr)
        logging.info(f"Sending to XPLANE: >>{sendstr}<<")
        self.__xplane_axis_override_active = active
        self.__xplane_override_sent_at = time.perf_counter()

    def release_xp_axis_override(self) -> None:
        """Hand any axis this device holds back to X-Plane.

        Called on shutdown.  The override datarefs live in the sim and outlive
        this process, so an override left latched leaves the user's physical
        control inert until they clear it from the plugin's menu or restart
        X-Plane.  Sent unconditionally rather than only when an override is
        believed active: the command is idempotent, and "believed active" is
        exactly the state that cannot be trusted here.
        """
        if not self._sim_is_xplane():
            return
        if self.is_trimwheel():
            # the plugin has no trimwheel override keyword — nothing to release
            return
        self._send_xp_override(False)

    def toggle_xp_control(self):
        """Reconcile X-Plane's axis override with what this device needs.

        Called every telemetry frame.  The axis is claimed only while all of
        these hold, and released the moment any of them stops holding:

        * the simulator-side axis backend is in use — false when the user
          disables axis control globally (``telemffb_controls_axes``) or for
          this device alone (``local_disable_axis_control``), and false when
          the axis is driven in device firmware instead;
        * the FFB device is connected and feeding input — a dead instance
          would otherwise pin the sim's yoke to zeros and silence the user's
          real controller;
        * for a collective, the aircraft is a helicopter.

        The command is sent only on a state change, measured against the
        plugin's echoed state whenever that is available.  Reconciling against
        the sim rather than a local flag is what makes the release reliable: a
        device whose override is still latched in the sim — from a previous
        session, or from the user having just disabled axis control for it — is
        handed back to the physical control on the next frame, with no restart
        and no trip to the plugin's "Clear All Overrides" menu.
        """
        if self.is_trimwheel():
            # not implemented — the plugin has no trimwheel override keyword
            return

        desired = self._use_sim_axis_backend() and self._device_feeding()

        if self.is_collective() and not self.is_helicopter():
            # The plugin maps "collective" to the sim's prop-pitch override.
            # On a fixed-wing aircraft that dataref is prop pitch and nothing
            # else, so the axis is never taken — and any override latched by
            # an earlier helicopter session is released here.
            desired = False

        reported = self._xp_reported_override_state()

        # Trust the sim's echoed state, but only once a command we just sent
        # has had time to be applied and reflected back.
        if reported is not None and (
            time.perf_counter() - self.__xplane_override_sent_at
            > XP_OVERRIDE_ECHO_GRACE_S
        ):
            self.__xplane_axis_override_active = reported

        if desired != self.__xplane_axis_override_active:
            self._send_xp_override(desired)
