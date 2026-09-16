import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
from telemffb.utils import clamp
from typing import override
import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class MsfsXpHeliControlsMixIn(MsfsXpFlightControlsMixIn):
    """Mixin for MSFS and X-Plane specific helicopter flight controls handling."""

    # user parameters
    # Vars for custom force trim switch L:var
    custom_ft_sw_var_enabled = False
    custom_ft_sw_var = "L:TelemFFBHeliFT"
    force_trim_button = 0
    force_trim_reset_button = 0
    cyclic_spring_gain = 1.0
    trim_release_spring_gain = 0
    force_trim_send_reset = True

    # end of user parameters

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _sync_force_trim_simvar(self):
        """Subscribe the ForceTrimSW simvar once and re-subscribe only when the binding changes."""
        if not self._sim_is_msfs():
            return
        # Always call anything_has_changed to initialize tracking state (avoid short-circuit).
        var_changed = self.anything_has_changed('custom_ft_sw_var', self.custom_ft_sw_var)
        enabled_changed = self.anything_has_changed('custom_ft_sw_var_enabled', self.custom_ft_sw_var_enabled)
        if (self.custom_ft_sw_var_enabled and var_changed) or enabled_changed:
            self._simconnect.add_simvar(name="ForceTrimSW", var=self.custom_ft_sw_var, sc_unit="enum")
            self._simconnect._resubscribe()

    def _force_trim_configured(self) -> bool:
        """True when force-trim mode is selected AND a release button is bound.

        Force trim without a bound button has no way to set a center, so every
        caller must treat it as not-force-trim and fall back to the no-spring
        path rather than abandoning the control update.
        """
        return self.spring_mode_is(SpringModeEnum.FORCETRIM) and self.force_trim_button != 0

    def _initialize_cyclic_if_needed(self, telem_data: BaseTelemetryData) -> bool:
        """Initialize cyclic spring to ground-center or last-saved position.

        Returns True if still initializing (caller should return early),
        False once the physical stick has reached the target position.

        Telemetry:
            Read: SimOnGround - int (0 or 1); when on ground (1) and trim reset complete,
                                spring centers to 0; otherwise restores last saved device position
            Written: phys_x, phys_y (float, –1.0 to 1.0; current physical stick position)
        """
        if self.cyclic_spring_init:
            return False

        self.cyclic_center = [0.0, 0.0]

        phys_x, phys_y = self._get_device_axes()
        telem_data.phys_x = phys_x
        telem_data.phys_y = phys_y
        self.spring_x.set_coefficient(self.cyclic_spring_gain, True)
        self.spring_y.set_coefficient(self.cyclic_spring_gain, True)

        if telem_data.get("SimOnGround", 1) and self.trim_reset_complete:
            self.cpO_x = 0
            self.cpO_y = 0
            self.last_pos_x_pos = 0
            self.last_pos_y_pos = 0
        else:
            self.cpO_x = utils.clamp(self.last_device_x, -1, 1)
            self.cpO_y = utils.clamp(self.last_device_y, -1, 1)

        self.spring_x.set_offset(self.cpO_x)
        self.spring_y.set_offset(self.cpO_y)
        self._spring_handle.setCondition(self.spring_x)
        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.start()
        if (self.cpO_x - 0.15 < phys_x < self.cpO_x + 0.15) and (
            self.cpO_y - 0.15 < phys_y < self.cpO_y + 0.15
        ):
            self.cyclic_spring_init = 1
            logging.info("Cyclic Spring Initialized")
            return False
        else:
            if self._sim_is_msfs():
                x_var, _ = self._get_msfs_axis_config('x', "AXIS_CYCLIC_LATERAL_SET")
                y_var, _ = self._get_msfs_axis_config('y', "AXIS_CYCLIC_LONGITUDINAL_SET")
                self.msfs_send_heli_cyclic_pos(x_var, self.last_pos_x_pos, y_var, self.last_pos_y_pos, telem_data)
            return True

    def _handle_cyclic_trim_reset(self):
        """Animate the cyclic spring back to center over 500ms when trim reset is pressed."""
        if not hasattr(self, "_trim_reset_in_progress"):
            self._trim_reset_in_progress = True
            logging.info("Trim Reset Pressed")

        self.cpO_x = self.step_value_over_time("center_x", self.cpO_x, 500, 0, floatpoint=True)
        self.cpO_y = self.step_value_over_time("center_y", self.cpO_y, 500, 0, floatpoint=True)

        if self.cpO_x == 0 and self.cpO_y == 0:
            self.trim_reset_complete = 1
            if self._trim_reset_in_progress:
                logging.info("Trim Reset Complete")
            self._trim_reset_in_progress = False
        else:
            self.trim_reset_complete = 0
            if not self._trim_reset_in_progress:
                logging.info("Trim Reset Pressed")
            self._trim_reset_in_progress = True

        self.spring_x.set_coefficient(self.cyclic_spring_gain)
        self.spring_y.set_coefficient(self.cyclic_spring_gain)

        self.spring_x.set_offset(self.cpO_x)
        self.spring_y.set_offset(self.cpO_y)

        if self._sim_is_msfs() and self.force_trim_send_reset:
            self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 0)

    def _update_cyclic_force_trim(self, telem_data: BaseTelemetryData, input_data, x, y, force_trim_active) -> bool:
        """Run the cyclic force-trim state machine.

        Returns True if the caller should return early (init in progress),
        False otherwise.
        """
        if self.spring_mode_is(SpringModeEnum.FORCETRIM) and not self._force_trim_configured():
            self.flag_error("Force trim enabled but buttons not configured")

        if self._force_trim_configured() and force_trim_active:
            # A missing/hot-unplugged device (input_data is None) reads
            # as "no button pressed", so no trim action can fire.
            if self.cyclic_spring_init and input_data is not None:
                force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)
            else:
                force_trim_pressed = False

            if self.force_trim_reset_button > 0 and input_data is not None:
                trim_reset_pressed = input_data.isButtonPressed(self.force_trim_reset_button)
            else:
                trim_reset_pressed = False

            self.tr_state_change = False

            if self.anything_has_changed("ft_tracker", force_trim_pressed):
                self.tr_state_change = True

            # remember previous "pressed" to detect edge
            force_trim_pressed_prev = getattr(self, "force_trim_pressed_prev", False)
            force_trim_pressed = (
                input_data.isButtonPressed(self.force_trim_button)
                if (input_data is not None and self.cyclic_spring_init) else False
            )
            self.tr_state_change = force_trim_pressed != force_trim_pressed_prev
            self.force_trim_pressed_prev = force_trim_pressed

            if self._initialize_cyclic_if_needed(telem_data):
                return True
            elif force_trim_pressed:
                # Force trim button depressed: absorb trim offsets, soften spring, follow stick
                if self.tr_state_change:
                    total_x = self.cpO_x + self.cyclic_physical_trim_x_offs
                    total_y = self.cpO_y + self.cyclic_physical_trim_y_offs

                    self.cpO_x = total_x
                    self.cpO_y = total_y
                    self.cyclic_physical_trim_x_offs = 0
                    self.cyclic_physical_trim_y_offs = 0
                    self.cyclic_virtual_trim_x_offs = 0.0
                    self.cyclic_virtual_trim_y_offs = 0.0

                    if self._sim_is_msfs() and self.force_trim_send_reset:
                        self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 1)

                    logging.info(f"Force Trim Disengaged total={self.cpO_x}:{self.cpO_y}")

                gain = int(self.trim_release_spring_gain * 4096)
                self.spring_x.set_coefficient(gain)
                self.spring_y.set_coefficient(gain)

                self.cpO_x = utils.clamp(x, -1, 1)
                self.cpO_y = utils.clamp(y, -1, 1)

                self.spring_x.set_offset(self.cpO_x)
                self.spring_y.set_offset(self.cpO_y)

                self.cyclic_center = [x, y]
                self.cyclic_trim_release_active = 1

            elif not force_trim_pressed and self.cyclic_trim_release_active:
                # Force trim released: restore gain, lock center
                self.spring_x.set_coefficient(self.cyclic_spring_gain, True)
                self.spring_y.set_coefficient(self.cyclic_spring_gain, True)

                self.cpO_x = utils.clamp(x, -1, 1)
                self.cpO_y = utils.clamp(y, -1, 1)
                self.spring_x.set_offset(self.cpO_x)
                self.spring_y.set_offset(self.cpO_y)

                self.cyclic_center = [x, y]

                if self._sim_is_msfs() and self.force_trim_send_reset:
                    self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 0)

                logging.info(f"Force Trim Engaged :{self.cpO_x}:{self.cpO_y}")
                self.cyclic_trim_release_active = 0

            elif trim_reset_pressed or not self.trim_reset_complete:
                self._handle_cyclic_trim_reset()

            else:
                if getattr(self, "ft_was_inactive", True):
                    self.spring_x.set_coefficient(self.cyclic_spring_gain, True)
                    self.spring_y.set_coefficient(self.cyclic_spring_gain, True)
                    self.ft_was_inactive = False

            telem_data.StickXY = [x, y]
            telem_data.StickXY_offset = self.cyclic_center

        elif self._force_trim_configured() and not force_trim_active:
            self.ft_was_inactive = True

            gain = int(self.trim_release_spring_gain * 4096)
            self.spring_x.set_coefficient(gain)
            self.spring_y.set_coefficient(gain)

            self.cpO_x = utils.clamp(x, -1, 1)
            self.cpO_y = utils.clamp(y, -1, 1)

            self.spring_x.set_offset(self.cpO_x)
            self.spring_y.set_offset(self.cpO_y)

            self.cyclic_center = [x, y]

        else:
            # The active-force-trim branch only restores the gain when coming
            # back from an inactive state, so re-arm that latch here: without
            # it, leaving force trim (mode switch, or the release button
            # unbound mid-flight) and returning leaves the spring at zero
            # until a telemetry timeout resets the init flag.
            self.ft_was_inactive = True
            self.spring_x.set_coefficient(0)
            self.spring_y.set_coefficient(0)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()

        return False

    def _send_cyclic_axis_output(self, telem_data: BaseTelemetryData, force_trim_active):
        """Read physical stick, apply trim offsets and scaling, send axis values to sim."""
        if not self._axis_control_enabled():
            return

        phys_x, phys_y = self._get_device_raw_axes()
        telem_data.phys_x = phys_x
        telem_data.phys_y = phys_y
        self._update_cyclic_trim(telem_data)

        x_pos = phys_x - self.cyclic_virtual_trim_x_offs
        y_pos = phys_y - self.cyclic_virtual_trim_y_offs

        x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
        y_scale = clamp(self.joystick_y_axis_scale, 0, 1)

        if self._use_firmware_axis_backend():
            self._send_firmware_fixed_axes(x_pos * x_scale, y_pos * y_scale)
            self.last_device_x, self.last_device_y = phys_x, phys_y
            return

        if self._sim_is_xplane():
            pos_x_pos = x_pos * x_scale
            pos_y_pos = y_pos * y_scale
            self.send_xp_command(f"AXIS:jx={round(pos_x_pos, 5)},jy={round(pos_y_pos, 5)}")

        if self.cyclic_spring_init or not (self._force_trim_configured() and force_trim_active):
            if self._sim_is_msfs():
                x_var, x_range = self._get_msfs_axis_config('x', "AXIS_CYCLIC_LATERAL_SET")
                y_var, y_range = self._get_msfs_axis_config('y', "AXIS_CYCLIC_LONGITUDINAL_SET")
                pos_x_pos = self._scale_msfs_axis_value(x_pos, x_range, x_scale)
                pos_y_pos = self._scale_msfs_axis_value(y_pos, y_range, y_scale)
                self.msfs_send_heli_cyclic_pos(x_var, pos_x_pos, y_var, pos_y_pos, telem_data)
                self.last_pos_x_pos = pos_x_pos
                self.last_pos_y_pos = pos_y_pos

        self.last_device_x, self.last_device_y = phys_x, phys_y

    def msfs_send_heli_cyclic_pos(self, xvar, xpos, yvar, ypos, telem_data: BaseTelemetryData):
        self._simconnect.send_event_to_msfs(xvar, xpos)
        self._simconnect.send_event_to_msfs(yvar, ypos)

    def msfs_update_heli_controls(self, telem_data: BaseTelemetryData):
        """Drive cyclic spring (force-trim or center-spring) and send cyclic axis to simulator.

        Telemetry:
            Read: FFBType            - str; device type; only "joystick" path is handled here
            Read (MSFS):   APMaster  - Union[bool, int] (0 or 1); autopilot engaged state
            Read (XPLANE): APServos  - Union[bool, int] (0 or 1); autopilot servo state
            Read: ForceTrimSW        - bool; cockpit force-trim switch state; only subscribed
                                       when custom_ft_sw_var_enabled is True; defaults to True
            Read (via _initialize_cyclic_if_needed): SimOnGround - int (0 or 1)
            Read (via _update_cyclic_trim, when trim_following):
                  ForceTrimSW          - bool; suppresses trim offset updates when False
            Read (MSFS trim-following): CyclicTrimX - float (percent, –100 to 100);
                                                        lateral cyclic trim position
                                         CyclicTrimY - float (percent, –100 to 100);
                                                        longitudinal cyclic trim position
            Read (XPLANE trim-following): AileronTrimPct - float (–1.0 to 1.0); roll trim pct
                                           ElevTrimPct   - float (–1.0 to 1.0); pitch trim pct
            Written: phys_x, phys_y  (float, –1.0 to 1.0; current physical stick position)
                     StickXY         ([float, float]; stick [x, y] at trim engage)
                     StickXY_offset  ([float, float]; cyclic center [x, y] at trim engage)
        """
        if self.is_trimwheel(): return

        ffb_type = telem_data.FFBType or "joystick"
        if self._sim_is_msfs():
            ap_active = telem_data.APMaster or 0
        if self._sim_is_xplane():
            ap_active = telem_data.APServos or 0

        self._sync_force_trim_simvar()

        self._sync_controls_lock_simvar()

        self._spring_handle.name = "cyclic_spring"
        force_trim_active = (
            telem_data.get("ForceTrimSW", True) if self.custom_ft_sw_var_enabled else True  # non-zero default: keep .get()
        )  # Enable cockpit switch control (if exists) for force trim.  Add LVar as "ForceTrimSW" bool if available for aircraft
        if ffb_type == "joystick":
            if not self._device_feeding():  # device unplugged; nothing to send
                return
            x, y = self._get_device_raw_axes()
            telem_data.phys_x = x
            telem_data.phys_y = y

            controls_locked = self._get_controls_lock_state(telem_data)

            if self._apply_joystick_controls_lock(telem_data, controls_locked):
                return

            input_data = HapticEffect.get_device_input()
            if self._update_cyclic_force_trim(telem_data, input_data, x, y, force_trim_active):
                return

            self._send_cyclic_axis_output(telem_data, force_trim_active)

            if self.anything_has_changed(
                "cyclic_gain", self.cyclic_spring_gain
            ):  # check if spring gain setting has been modified in real time
                self.spring_x.set_coefficient(self.cyclic_spring_gain)
                self.spring_y.set_coefficient(self.cyclic_spring_gain)

            self.spring_x.set_offset(self.cpO_x + self.cyclic_physical_trim_x_offs)
            self.spring_y.set_offset(self.cpO_y + self.cyclic_physical_trim_y_offs)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)
            if self._force_trim_configured() and force_trim_active:
                if not self._spring_handle.started:
                    self._spring_handle.start()

    def _update_cyclic_trim(self, telem_data: BaseTelemetryData):
        if not self.is_joystick():
            return
        if not self.trim_following:
            return

        if self.custom_ft_sw_var_enabled and not telem_data.get('ForceTrimSW', True):  # non-zero default: keep .get()
            self.cyclic_physical_trim_x_offs = 0
            self.cyclic_physical_trim_y_offs = 0
            self.cyclic_virtual_trim_x_offs = 0
            self.cyclic_virtual_trim_y_offs = 0
            return

        # NEW: don't mutate offsets while trim-release is active
        if getattr(self, "cyclic_trim_release_active", 0):
            return
        if self._sim_is_msfs():
            cyclic_x_trim = telem_data.CyclicTrimX or 0
            cyclic_y_trim = telem_data.CyclicTrimY or 0
        elif self._sim_is_xplane():
            cyclic_x_trim = telem_data.AileronTrimPct or 0
            cyclic_y_trim = telem_data.ElevTrimPct or 0
        else:
            raise ValueError("Unknown simulator for cyclic trim")


        cyclic_x_trim = clamp(cyclic_x_trim * self.joystick_trim_follow_gain_physical_x * self.joystick_x_axis_scale, -1, 1)
        cyclic_y_trim = clamp(cyclic_y_trim * self.joystick_trim_follow_gain_physical_y * self.joystick_y_axis_scale, -1, 1)

        # print(f"x:{cyclic_x_trim}, y:{cyclic_y_trim}")

        self.cyclic_physical_trim_x_offs = utils.clamp(cyclic_x_trim, -1, 1)
        self.cyclic_physical_trim_y_offs = utils.clamp(cyclic_y_trim, -1, 1)
        self.cyclic_virtual_trim_x_offs = cyclic_x_trim - (cyclic_x_trim * self.joystick_trim_follow_gain_virtual_x)
        self.cyclic_virtual_trim_y_offs = cyclic_y_trim - (cyclic_y_trim * self.joystick_trim_follow_gain_virtual_y)

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.msfs_update_heli_controls(telem_data)
