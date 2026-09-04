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

from typing import override
from .Helicopter import Helicopter
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.util.conversions import kt2ms
from telemffb.utils import clamp, PerformanceTracker


import logging
import time
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

perftracker = PerformanceTracker()

class HPGHelicopter(Helicopter):
    """FFB aircraft class for HPG helicopters (e.g. H145) in MSFS.

    HPG helicopters expose a rich AFCS (Automatic Flight Control System) telemetry
    interface via SimConnect L-vars.  This class reads those values to drive realistic
    force-feedback behaviour across all three FFB devices (cyclic joystick, collective,
    and pedals), each of which runs as a separate TelemFFB process instance.

    Key integration points
    ----------------------
    - **SEMA actuators** (``hpgSEMAx``, ``hpgSEMAy``, ``hpgSEMAyaw``): The AFCS
      servo actuators have a limited throw of ±100.  TelemFFB reads these values and
      moves the FFB spring center in the direction that drives each SEMA toward zero —
      i.e., the physical stick/pedals continuously "take up the slack" so the AFCS
      actuators can stay near their neutral position for maximum authority.
    - **Collective AFCS mode** (``hpgCollectiveAfcsMode``): Non-zero when a vertical
      AP mode (altitude hold, VS hold, etc.) is active.  The collective spring then
      tracks the live ``CollectivePos`` sim variable rather than a pilot-set trim
      reference.
    - **Hands-on / feet-on detection**: TelemFFB detects pilot contact with the
      controls via axis-deviation thresholds or force sensing.  Contact state is
      written back to the sim via L-vars (``L:FFB_HANDS_ON_CYCLIC``,
      ``L:FFB_FEET_ON_PEDALS``) so the AFCS can react appropriately.
    - **telemffb_controls_axes must be True** for all three devices.  TelemFFB is
      solely responsible for sending axis positions to MSFS; physical device axes
      must be unbound in MSFS settings.

    User-configurable parameters
    ----------------------------
    collective_ap_spring_gain : float
        Spring stiffness for the collective in both manual and AP modes.
    collective_dampening_gain : float
        Collective damper coefficient (currently unused, reserved).
    hands_on_deadzone : float
        Axis-deviation threshold (0–1) at which pilot contact with the cyclic is
        first detected (engage threshold).
    hands_off_deadzone : float
        Axis-deviation threshold at which cyclic contact is considered released
        (disengage threshold, lower than engage for hysteresis).
    feet_on_deadzone : float
        Pedal axis-deviation threshold for detecting initial foot contact (engage).
    feet_off_deadzone : float
        Pedal axis-deviation threshold for releasing foot-contact state (disengage).
    send_individual_hands_on : int
        When non-zero, sends separate X/Y hands-on L-vars in addition to the
        combined master L-var.
    vrs_effect_enable : bool
        Enable the Vortex Ring State buffet effect.
    vrs_effect_intensity : float
        Maximum intensity of the VRS buffet (0–1 scale applied to the VRS datum).
    afcs_followup_trim_rate : int
        Rate (units per second) at which the spring center follows the pilot's
        physical input when follow-up trim is active with hands on the cyclic.
    handson_force_mode : bool
        When True, detects hands-on via measured stick force rather than axis
        deviation. Useful when the pilot rests their hand lightly on the cyclic
        without deflecting it past the deadzone.
    hands_on_force_threshold : float
        Force threshold (normalised, 0–1) above which ``handson_force_mode``
        considers the pilot's hand to be on the cyclic.
    handsoff_force_duration : int
        Milliseconds the force must remain below ``hands_on_force_threshold``
        before the hands-off state is declared (prevents spurious drop-outs).
    handson_force_debug : bool
        Emit per-frame debug log and console output for force-based hands-on state.
    """

    # user parameters
    sema_yaw_max = 5
    afcs_step_size = 2
    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 0
    collective_spring_coeff_y = 0
    hands_on_deadzone = 0.1
    hands_off_deadzone = 0.02
    feet_on_deadzone = 0.05
    feet_off_deadzone = 0.03
    hands_on_active = 0
    hands_on_x_active = 0
    hands_on_y_active = 0
    feet_on_active = 0
    send_individual_hands_on = 0
    vrs_effect_enable: bool = True
    vrs_effect_intensity = 0
    afcs_followup_trim_rate = 100
    handson_force_mode = False
    hands_on_force_threshold = 0.03
    handsoff_force_duration = 500  # ms
    handson_force_debug = False
    # end of user parameters

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        self.phys_x, self.phys_y = self._get_device_axes()
        # Initialise collective spring center from the device's current physical position
        # so the spring does not immediately fight the pilot on startup.
        self.cpO_y = utils.clamp(self.phys_y, -1, 1)
        self.collective_spring_coeff_y = round(4096 * utils.clamp(self.collective_ap_spring_gain, 0, 1))
        self.hands_on_active = 0
        self.hands_on_x_active = 0
        self.hands_on_y_active = 0
        self.feet_on_active = 0
        # Accumulates fractional follow-up trim steps between frames so the
        # effective trim rate (afcs_followup_trim_rate) is frame-rate-independent.
        self.followup_trim_accumulator = 0.0
        self.tracker_var = False
        self._last_hands_on_time_ms = 0  # timestamp used by handsoff_force_duration hold-off
        self.pedals_init = 0
        self._prev_afcs_mode = 0  # tracks previous collective AFCS mode to detect AP disengage transition

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)

        # self._update_vrs_effect(telem_data)

    @override
    def on_timeout(self):
        """Reset all device initialisation flags on telemetry timeout.

        Clearing ``cyclic_spring_init``, ``collective_init``, and ``pedals_init``
        forces each device to go through its startup handshake again when telemetry
        resumes — e.g. after a sim pause or aircraft reload.  This prevents each
        device from issuing axis commands before the physical controls have been
        confirmed to be at the expected starting position.

        ``_prev_afcs_mode`` is also cleared so that the AP-disengage snap guard
        starts from a known state on reconnect.
        """
        super().on_timeout()
        self.cyclic_spring_init = 0
        self.collective_init = 0
        self.pedals_init = 0
        self._prev_afcs_mode = 0

    def check_feet_on(self, percent: float) -> dict:
        """Detect foot contact with the pedals via axis-deviation threshold.

        Measures how far the physical pedal axis has moved from the current trim
        reference (``cpO_x``) and returns whether that deviation exceeds the
        supplied threshold.  Two different thresholds are used by the caller to
        create a hysteresis band:

        - ``feet_on_deadzone``  — engage threshold (wider), checked while feet
          are currently considered *off* the pedals.
        - ``feet_off_deadzone`` — disengage threshold (narrower), checked while
          feet are currently considered *on* the pedals.

        This hysteresis prevents rapid toggling when the pilot's feet rest lightly
        near the trim reference.

        Parameters
        ----------
        percent : float
            Fraction of full axis range (0–1) that constitutes the threshold.
            E.g. 0.05 means a 5 % deflection from the spring center triggers
            the feet-on condition.

        Returns
        -------
        dict
            ``result``    : bool  – True if the pedal deviation exceeds the threshold.
            ``deviation`` : float – Normalised deviation from the trim center (0–1).
        """
        phys_x, phys_y = self._get_device_axes()

        ref_x = self.cpO_x

        threshold = percent

        # Normalised (0–1) distance from the spring center
        deviation_x = abs(phys_x - ref_x)

        x_exceeds_threshold = abs(phys_x - ref_x) > threshold

        return {
            "result": x_exceeds_threshold,
            "deviation": deviation_x,
        }

    @override
    def msfs_update_heli_controls(self, telem_data: BaseTelemetryData):
        """Update cyclic FFB spring, SEMA follow-up trim, and hands-on detection.

        This method is the core cyclic (joystick) update loop for HPG helicopters.
        It is only active when ``FFBType == "joystick"`` and is gated on
        ``cyclic_spring_init`` having been completed by the parent class — if the
        cyclic has not yet finished its startup handshake, only the force-trim
        button press is processed (to avoid blocking the user from centring the
        stick).

        SEMA-driven follow-up trim
        --------------------------
        ``hpgSEMAx`` and ``hpgSEMAy`` are the HPG AFCS servo actuator positions
        (range ±100).  The AFCS uses these actuators for real-time attitude
        stabilisation; if they saturate the AFCS loses authority.  TelemFFB moves
        the physical spring center (``cpO_x`` / ``cpO_y``) in whichever direction
        reduces the corresponding SEMA value toward zero — effectively making the
        physical stick "take up the slack" so the actuators remain near their
        neutral position.

        Hands-on detection
        ------------------
        Pilot contact with the cyclic is determined by one of two methods:

        *Axis-deviation mode (default)*: ``check_hands_on()`` compares the physical
        stick position to the current spring center.  When ``hpgHandsOnCyclic`` is
        already True (sim considers hands on) the tighter ``hands_off_deadzone`` is
        used; otherwise the wider ``hands_on_deadzone`` is used.  This hysteresis
        prevents rapid toggling.

        *Force mode* (``handson_force_mode = True``): uses measured stick force
        rather than position deviation.  Once force exceeds ``hands_on_force_threshold``
        the hands-on state is latched; it is only cleared after the force has
        remained below the threshold for ``handsoff_force_duration`` ms.

        Follow-up trim with hands on
        ----------------------------
        When ``activate_followup_trim`` is True and the pilot has hands on the
        cyclic, the spring center follows the physical stick deviation (``dev_x_raw``
        / ``dev_y_raw``) at the configured ``afcs_followup_trim_rate``.  This allows
        the pilot to make minor attitude corrections by simply deflecting the stick
        without pressing trim release — the trim reference shifts to match.

        Trim release (force-trim button)
        ---------------------------------
        Pressing the configured force-trim button sends ``ROTOR_TRIM_RESET`` to
        MSFS, which tells the HPG AFCS to reset its own cyclic reference to the
        current stick position.  The follow-up trim loop is also frozen while
        ``hpgTrimRelease`` is non-zero to avoid fighting the AFCS during its reset.

        Note: ``telemffb_controls_axes`` must be True and the cyclic axes must be
        unbound in MSFS settings for this integration to work correctly.
        """
        super().msfs_update_heli_controls(telem_data)
        ffb_type = telem_data.FFBType or "joystick"
        ap_active = telem_data.APMaster or 0

        # hpgTrimRelease is a sim-side signal that the AFCS is currently resetting its
        # trim reference.  Freeze the follow-up trim loop while this is active so
        # TelemFFB does not fight the AFCS during its own reset sequence.
        # Only read this after cyclic_spring_init is complete; before that the sim
        # state may not be meaningful.
        if self.cyclic_spring_init:
            trim_reset = telem_data.hpgTrimRelease or 0
        else:
            trim_reset = False
        input_data = self._get_device_report()
        # Force-trim button is only meaningful after the cyclic has initialised.
        force_trim_pressed = (input_data is not None and input_data.isButtonPressed(self.force_trim_button)) if self.cyclic_spring_init else False
        if force_trim_pressed:
            # Tell the HPG AFCS to reset its cyclic trim reference to the current
            # physical stick position, aligning the sim's trim state with the FFB center.
            self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 1)

        if ffb_type == "joystick":
            if not self.telemffb_controls_axes and not self.local_disable_axis_control:
                self.flag_error(
                    "Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the cyclic axes in MSFS settings")
                return

            # SEMA actuator values (range ±100).  A 100 ms rolling average is used
            # to smooth out high-frequency AFCS corrections before driving the spring.
            sema_x = telem_data.hpgSEMAx or 0
            sema_y = telem_data.hpgSEMAy or 0

            sema_x_avg = self.smoother.get_rolling_average('s_sema_x', sema_x, window_ms=100)
            sema_y_avg = self.smoother.get_rolling_average('s_sema_y', sema_y, window_ms=100)

            # Hands-on detection — use the tighter disengage deadzone when the sim
            # already considers the pilot's hands to be on the cyclic (hysteresis).
            if telem_data.hpgHandsOnCyclic:
                hands_on_dict = self.check_hands_on(self.hands_off_deadzone)
            else:
                hands_on_dict = self.check_hands_on(self.hands_on_deadzone)
            hands_on_either = hands_on_dict["master_result"]

            if self.handson_force_mode:
                # Override axis-deviation detection with force-based detection.
                # The hands-on state is latched True as soon as force exceeds the
                # threshold, and held True for handsoff_force_duration ms after the
                # force drops below it — preventing brief contact from flickering.
                force_x, force_y = self._get_device_forces()
                thresh = self.hands_on_force_threshold

                is_hands_on_now = abs(force_x) > thresh or abs(force_y) > thresh

                now_ms = int(time.perf_counter() * 1000)

                if is_hands_on_now:
                    self._last_hands_on_time_ms = now_ms
                    hands_on_either = True
                    if self.handson_force_debug:
                        utils.dbprint('green', f"hands on: True     (fx: {force_x}, fy: {force_y})")
                        logging.info(f"hands on: True     (fx: {force_x}, fy: {force_y})")

                else:
                    if now_ms - self._last_hands_on_time_ms > self.handsoff_force_duration:
                        if self.handson_force_debug:
                            utils.dbprint('blue', f"hands on: False     (fx: {force_x}, fy: {force_y})")
                            logging.info(f"hands on: False     (fx: {force_x}, fy: {force_y})")
                        hands_on_either = False
                    else:
                        # Still within the hold-off window — maintain hands-on state.
                        if self.handson_force_debug:
                            utils.dbprint('yellow', f"hands on: Waiting     (fx: {force_x}, fy: {force_y})")
                            logging.info(f"hands on: Waiting     (fx: {force_x}, fy: {force_y})")

            hands_on_x = hands_on_dict["x_result"]
            dev_x = hands_on_dict["x_deviation"]
            dev_x_raw = hands_on_dict["x_deviation_raw"]
            hands_on_y = hands_on_dict["y_result"]
            dev_y = hands_on_dict["y_deviation"]
            dev_y_raw = hands_on_dict["y_deviation_raw"]

            if not trim_reset:
                # SEMA step sizes are proportional to the smoothed SEMA magnitude
                # so that larger actuator displacements cause faster trim movement.
                sx = round(abs(sema_x_avg), 3)
                sy = round(abs(sema_y_avg), 3)

                self.afcsx_step_size = sx * 0.25 / 4096
                self.afcsy_step_size = sy * 0.25 / 4096

                ias = telem_data.IAS or 0  # Indicated Airspeed in m/s

                self.afcs_followup_trim_rate = 100

                # Frame-rate-independent follow-up trim rate.  The configured rate is
                # in device units/s, but the accumulator and applied center step stay
                # normalized.  Preserve the historical whole-device-unit quantization
                # so the effective trim feel remains unchanged.
                dt = perftracker.get_time_delta('hpg_perf_tracker')

                telem_data.hpg_perf_tracker = round(dt, 6)

                followup_trim_step_size_raw = self.afcs_followup_trim_rate * dt / 4096

                self.followup_trim_accumulator += followup_trim_step_size_raw
                whole_device_steps = int(self.followup_trim_accumulator * 4096)
                followup_trim_step_size = whole_device_steps / 4096
                self.followup_trim_accumulator -= followup_trim_step_size

                telem_data.hpg_followup_step_size = followup_trim_step_size
                telem_data.hpg_followup_step_size_raw = followup_trim_step_size_raw
                telem_data.hpg_followup_trim_accum = self.followup_trim_accumulator

                # hpgFollowupTrimMode is an in-sim user setting that enables follow-up
                # trim only in specific flight regimes (hover, cruise, or always).
                followup_trim_state = telem_data.get('hpgFollowupTrimMode', 1)

                match followup_trim_state:
                    case 0:  # Both (always active)
                        activate_followup_trim = True
                    case 1:  # Off
                        activate_followup_trim = False
                    case 2:  # Hover only (below 40 kt)
                        activate_followup_trim = True if ias < 40 * kt2ms else False
                    case 3:  # Cruise only (above 40 kt)
                        activate_followup_trim = True if ias > 40 * kt2ms else False
                    case _:
                        activate_followup_trim = False

                # Follow-up trim is never active on the ground — suppress to avoid
                # unexpected spring center movement during startup and shutdown.
                if telem_data.get('SimOnGround', 1):
                    activate_followup_trim = False

                # --- X (lateral) spring center update ---
                # Hands OFF: drive cpO_x toward the SEMA zero by the proportional
                # step size so the AFCS actuator can return toward neutral.
                # Hands ON + follow-up trim active: let the pilot steer the trim
                # reference by physically deflecting the stick (dev_x_raw tracks
                # displacement from the current spring center).
                if not (self.hands_on_x_active or self.hands_on_active):
                    if sema_x_avg > 0:
                        self.cpO_x -= self.afcsx_step_size
                    elif sema_x_avg < 0:
                        self.cpO_x += self.afcsx_step_size
                elif activate_followup_trim and (hands_on_x and self.hands_on_active):
                    if dev_x_raw > 0:
                        self.cpO_x += followup_trim_step_size
                    elif dev_x_raw < 0:
                        self.cpO_x -= followup_trim_step_size

                # --- Y (longitudinal) spring center update --- (same logic as X)
                if not (self.hands_on_y_active or self.hands_on_active):
                    if sema_y_avg > 0:
                        self.cpO_y -= self.afcsy_step_size
                    elif sema_y_avg < 0:
                        self.cpO_y += self.afcsy_step_size
                elif activate_followup_trim and (hands_on_y and self.hands_on_active):
                    if dev_y_raw > 0:
                        self.cpO_y += followup_trim_step_size
                    elif dev_y_raw < 0:
                        self.cpO_y -= followup_trim_step_size

            # Apply the updated spring centers to the hardware and start the effect.
            self.spring_x.set_offset(self.cpO_x)
            self.spring_y.set_offset(self.cpO_y)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)

            # Publish hands-on state to the sim via L-vars so the AFCS can react.
            self._dispatch_hands_on_state(telem_data, hands_on_dict, hands_on_either)

            self._spring_handle.start()

    @override
    def _update_cyclic_trim(self, telem_data: BaseTelemetryData):
        """Suppress the generic parent-class cyclic trim logic.

        Cyclic trimming for HPG helicopters is driven entirely by the AFCS SEMA
        integration in ``msfs_update_heli_controls``.  The parent class trim
        algorithm is not compatible with this approach and must not run.
        """
        pass

    @override
    def msfs_update_pedals(self, telem_data: BaseTelemetryData):
        """Update pedal FFB spring, SEMA yaw follow-up trim, and feet-on detection.

        Mirrors the cyclic update logic but for the tail-rotor / yaw axis.
        ``hpgSEMAyaw`` is the yaw AFCS actuator position (range ±100); TelemFFB
        moves the pedal spring center (``cpO_x``) in the direction that drives it
        toward zero, allowing the AFCS to maintain yaw authority.

        Feet-on detection
        -----------------
        Pilot foot contact is detected in one of two ways, evaluated in priority
        order each frame:

        1. **Button press** (FORCETRIM spring mode, ``pedal_ft_release_button``
           configured): Takes unconditional priority.  While the button is held the
           spring is disabled (or set to the optional damper coefficient), ``cpO_x``
           is anchored to the current physical position, and ``L:FFB_FEET_ON_PEDALS``
           is set to 1.  On release the spring re-engages centered at the pilot's
           foot position, giving a smooth handoff without a snap.

        2. **Axis-deviation** (fallback): ``check_feet_on()`` compares the physical
           pedal position to the trim reference.  Hysteresis is applied via two
           thresholds — ``feet_on_deadzone`` (engage) and ``feet_off_deadzone``
           (disengage) — using the sim's ``hpgFeetOnPedals`` state to select which
           threshold to apply this frame.

        While feet are considered on the pedals (by either method) the SEMA yaw
        follow-up trim loop is paused so the pilot can freely reposition without
        fighting the spring.

        Pedal initialization handshake
        --------------------------------
        On first call (``pedals_init == 0``), the spring is initialised to either
        the neutral center (on the ground) or the last known pedal position (in the
        air / resuming from pause).  Axis commands are withheld until the physical
        pedals are within 10 % of the target, preventing a sudden jerk on reconnect.

        Axis output
        -----------
        The physical pedal position is sent to MSFS every frame via
        ``ROTOR_AXIS_TAIL_ROTOR_SET`` (or a custom axis variable if configured).
        ``telemffb_controls_axes`` must be True and the pedal axes must be unbound
        in MSFS settings.
        """
        if telem_data.FFBType != 'pedals':
            return

        if self.telemffb_controls_axes and not self.local_disable_axis_control:
            phys_x, phys_y = self._get_device_axes()
            telem_data.phys_x = phys_x

            # Check for pedal trim release button press (if configured in FORCETRIM mode).
            # This takes priority over deflection-based feet-on detection and allows the
            # pilot to explicitly signal "feet on pedals" via a button rather than relying
            # solely on the axis-deviation threshold.
            pedal_trim_pressed = False
            if self.spring_mode_is(SpringModeEnum.FORCETRIM) and self.pedal_ft_release_button:
                pedal_trim_pressed = self.check_button_press(self.pedal_ft_release_button, self.pedal_ft_use_master_buttons)

            if pedal_trim_pressed:
                # Button held: pilot has feet on pedals.  Anchor the trim reference to the
                # current physical position so the spring re-centers there on release.
                self._simconnect.set_simdatum_to_msfs("L:FFB_FEET_ON_PEDALS", 1, units="number")
                self.feet_on_active = True
                self.cpO_x = utils.clamp(phys_x, -1, 1)


            else:
                # Deflection-based feet-on detection (existing behaviour)
                if telem_data.hpgFeetOnPedals:
                    feet_on_dict = self.check_feet_on(self.feet_off_deadzone)
                else:
                    feet_on_dict = self.check_feet_on(self.feet_on_deadzone)
                feet_on_pedals = feet_on_dict['result']
                dev_x = feet_on_dict['deviation']

                if feet_on_pedals:
                    self._simconnect.set_simdatum_to_msfs("L:FFB_FEET_ON_PEDALS", 1, units="number")
                    self.feet_on_active = True
                else:
                    self._simconnect.set_simdatum_to_msfs("L:FFB_FEET_ON_PEDALS", 0, units="number")
                    self.feet_on_active = False

            x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

            self._spring_handle.name = "pedal_ap_spring"

            if not self.pedals_init:

                self.spring_x.set_coefficient(self.pedal_spring_coeff_x)
                if telem_data.get("SimOnGround", 1):
                    self.cpO_x = 0.0
                else:
                    # print(f"last_colelctive_y={self.last_collective_y}")
                    self.cpO_x = utils.clamp(self.last_pedal_x, -1, 1)

                self.spring_x.set_coefficient(self.hpg_pedal_spring_gain, True)

                self.spring_x.set_offset(self.cpO_x)

                self._spring_handle.setCondition(self.spring_x)
                # self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
                self._spring_handle.start()
                logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
                if self.cpO_x - 0.1 < phys_x < self.cpO_x + 0.1:
                    # dont start sending position until physical pedals have centered
                    self.pedals_init = 1
                    logging.info("Pedals Initialized")
                else:
                    return


            sema_yaw = telem_data.hpgSEMAyaw or 0

            sema_yaw_avg = self.smoother.get_rolling_average('s_sema_yaw', sema_yaw, window_ms=100)

            sx = round(abs(sema_yaw_avg), 3)

            self.afcsx_step_size = sx * 0.5 / 4096

            telem_data._sx = sx
            telem_data._afcsx_step_size = self.afcsx_step_size

            # Pause SEMA follow-up trim whenever feet are on pedals (button or deflection)
            if not (self.feet_on_active):
                if sema_yaw > 0:
                    self.cpO_x -= self.afcsx_step_size
                elif sema_yaw < 0:
                    self.cpO_x += self.afcsx_step_size

            telem_data._cp0_x = self.cpO_x

            self.spring_x.set_offset(self.cpO_x)
            if pedal_trim_pressed:
                # Disable spring while button is held so pilot can freely reposition pedals
                if self.pedal_ft_damper_enabled:
                    self.spring_x.set_coefficient(self.pedal_ft_damper_force)
                else:
                    self.spring_x.set_coefficient(0)
            else:
                self.spring_x.set_coefficient(self.hpg_pedal_spring_gain, True)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()

            if self.enable_custom_x_axis:
                x_var = self.custom_x_axis
                x_range = self.raw_x_axis_scale
            else:
                x_var = 'ROTOR_AXIS_TAIL_ROTOR_SET'
                x_range = 16384

            pos_x_pos = utils.scale(phys_x, (-1, 1), (-x_range * x_scale, x_range * x_scale))

            if x_range != 1:
                pos_x_pos = -int(pos_x_pos)
            else:
                pos_x_pos = round(pos_x_pos, 5)

            self.last_pedal_x = phys_x

            self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

    @override
    def msfs_update_collective(self, telem_data: BaseTelemetryData):
        """Update collective FFB spring based on AFCS vertical mode and pilot input.

        The collective spring center (``cpO_y``) and coefficient are updated every
        frame according to the current AFCS vertical mode (``hpgCollectiveAfcsMode``)
        and whether the force-trim button is being held.

        Axis convention
        ---------------
        The collective physical axis is inverted relative to the FFB range:

        =====================  ================  ==========================
        Condition              ``phys_y``        ``cpO_y`` (normalized)
        =====================  ================  ==========================
        Full down (minimum)     +1.0               +1.0
        Center                   0.0                  0
        Full up (maximum)       −1.0               −1.0
        =====================  ================  ==========================

        ``CollectivePos`` from MSFS also follows an inverted convention:
        0.0 = full down → cpO_y = +1.0; 1.0 = full up → cpO_y = −1.0.

        Initialization handshake
        ------------------------
        On first call (``collective_init == 0``) the spring center is set to:

        - **On ground**: full-down position (``cpO_y = 1.0``).
        - **Air start / new aircraft**: current physical stick position.
        - **Resuming from pause**: last known physical position
          (``last_collective_y``).

        Axis commands are withheld until the physical collective is within 10 % of
        the target, preventing a jerk on reconnect.

        AFCS vertical mode OFF (``afcs_mode == 0``)
        --------------------------------------------
        *Force trim pressed*: Anchors the spring center to the current physical
        position (``cpO_y = phys_y``), applies the softer
        ``trim_release_spring_gain``, and sends the physical axis position to MSFS
        (``AXIS_COLLECTIVE_SET``).  ``AUTO_THROTTLE_DISCONNECT`` is also sent to
        disengage any active throttle hold.

        *No force trim — AP just disengaged*: On the first frame after ``afcs_mode``
        transitions from non-zero to zero, ``cpO_y`` is re-anchored to the current
        physical position.  Without this, the spring would retain the transient
        ``CollectivePos`` value written during the AFCS release frame, causing a
        noticeable snap.  See ``_prev_afcs_mode``.

        *No force trim — steady state*: Spring holds at the stored ``cpO_y`` with
        full ``collective_ap_spring_gain``.  No axis is sent to MSFS; the pilot
        must press force trim to reposition.

        AFCS vertical mode ON (``afcs_mode != 0``)
        --------------------------------------------
        *Force trim pressed*: Pilot overrides the AP.  Same behaviour as the AP-off
        force-trim path — anchors spring to physical position and sends the axis.

        *No force trim*: Spring center tracks ``CollectivePos`` in real time,
        keeping the FFB spring aligned with wherever the AFCS is holding the
        collective.  This provides appropriate resistance cues as the AFCS adjusts
        collective for altitude or vertical-speed hold.
        """
        if telem_data.FFBType != 'collective':
            return
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            self.flag_error("Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the collective axes in MSFS settings")
            return
        self._spring_handle.name = "collective_ap_spring"
        force_trim_pressed = False
        if self.spring_mode_is(SpringModeEnum.FORCETRIM) and self.force_trim_button:
            force_trim_pressed = self.check_button_press(self.force_trim_button, self.collective_ft_use_master_buttons)
            if self._sim_is_msfs() and force_trim_pressed:
                # Disengage any active throttle/collective hold so the pilot's
                # physical input is not immediately overridden by the AFCS.
                self._simconnect.send_event_to_msfs("AUTO_THROTTLE_DISCONNECT", 1)

        collective_tr = telem_data.hpgCollectiveRelease or 0
        afcs_mode = telem_data.hpgCollectiveAfcsMode or 0
        collective_pos = telem_data.CollectivePos or 0

        # Detect the moment the vertical AP mode disengages (non-zero → 0 transition).
        # Used below to re-anchor the spring center and avoid a snap caused by a
        # transient CollectivePos jump on the last AP-on frame during the AFCS release.
        ap_just_disengaged = (self._prev_afcs_mode != 0 and afcs_mode == 0)
        self._prev_afcs_mode = afcs_mode


        phys_x, phys_y = self._get_device_axes()
        telem_data.phys_y = phys_y
        if not self.collective_init:
            telem_data['_dbg_collective_init'] = False
            # self._ipc_telem['_dbg_collective_init'] = False
            self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)

            if telem_data.get("SimOnGround", 1):
                # Sim is on ground - set init point to full down
                self.cpO_y = 1.0
            elif self.last_collective_y is None:
                # Air start or new aircraft.  Use current physical position as init point
                self.cpO_y = utils.clamp(phys_y, -1, 1)
            else:
                # set init point to last point before pause
                self.cpO_y = utils.clamp(self.last_collective_y, -1, 1)

            self.spring_y.set_offset(self.cpO_y)

            self._spring_handle.setCondition(self.spring_y)

            self._spring_handle.start(override=True)

            if self.cpO_y - 0.1 < phys_y < self.cpO_y + 0.1:
                # Check if phys y position is within %10 of init point
                # dont start sending position until physical stick has centered
                self.collective_init = 1

                logging.info("Collective Initialized")
            else:
                return
        # Only reach here if collective position is initialized
        self.last_collective_y = phys_y
        telem_data['_dbg_collective_init'] = True
        # self._ipc_telem['_dbg_collective_init'] = True

        if afcs_mode == 0:
            telem_data['_dbg_collective_UM'] = False
            # self._ipc_telem['_dbg_collective_UM'] = False

            if force_trim_pressed:
                telem_data['_dbg_collective_FT_pressed'] = True
                # self._ipc_telem['_dbg_collective_FT_pressed'] = True

                self.cpO_y = utils.clamp(phys_y, -1, 1)
                self.spring_y.set_offset(self.cpO_y)

                self.spring_y.set_coefficient(self.trim_release_spring_gain, True)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start(override=True)

                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.collective_init:
                    telem_data['_dbg_collective_var'] = y_var
                    # self._ipc_telem['_dbg_collective_var'] = y_var
                    self._simconnect.send_event_to_msfs(y_var, pos_y_pos)
                    telem_data['_dbg_collective_sent_pos'] = pos_y_pos
                    # self._ipc_telem['_dbg_collective_sent_pos'] = pos_y_pos

            else:
                telem_data['_dbg_collective_FT_pressed'] = False
                # self._ipc_telem['_dbg_collective_FT_pressed'] = False
                if ap_just_disengaged:
                    # Re-anchor the spring center to the current physical stick position on
                    # the first AP-off frame.  Without this, cpO_y retains the transient
                    # CollectivePos value from the AFCS release frame, causing a noticeable
                    # snap.  Anchoring to phys_y is equivalent to an automatic force-trim
                    # press at the moment of AP disengagement.
                    self.cpO_y = utils.clamp(phys_y, -1, 1)
                self.spring_y.set_offset(self.cpO_y)
                # self.damper.damper(coef_y=0).start()
                self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start(override=True)

        else:
            telem_data['_dbg_collective_UM'] = True
            # self._ipc_telem['_dbg_collective_UM'] = True

            if force_trim_pressed:
                telem_data['_dbg_collective_FT_pressed'] = True
                # self._ipc_telem['_dbg_collective_FT_pressed'] = True

                self.cpO_y = utils.clamp(phys_y, -1, 1)
                # print(self.cpO_y)
                self.spring_y.set_offset(self.cpO_y)

                # self.damper.damper(coef_y=0).start()
                self.spring_y.set_coefficient(self.trim_release_spring_gain, True)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start(override=True)

                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.collective_init:
                    telem_data['_dbg_collective_var'] = y_var
                    # self._ipc_telem['_dbg_collective_var'] = y_var
                    self._simconnect.send_event_to_msfs(y_var, pos_y_pos)
                    telem_data['_dbg_collective_sent_pos'] = pos_y_pos
                    # self._ipc_telem['_dbg_collective_sent_pos'] = pos_y_pos

            else:
                telem_data['_dbg_collective_FT_pressed'] = False
                # self._ipc_telem['_dbg_collective_FT_pressed'] = False

                self.cpO_y = utils.scale_clamp(collective_pos, (0, 1), (1.0, -1.0))
                self.spring_y.set_offset(self.cpO_y)
                # self.damper.damper(coef_y=0).start()
                self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)

                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.collective_init:
                    telem_data['_dbg_collective_var'] = y_var
                    # self._ipc_telem['_dbg_collective_var'] = y_var
                    self._simconnect.send_event_to_msfs(y_var, pos_y_pos)
                    telem_data['_dbg_collective_sent_pos'] = pos_y_pos
                    # self._ipc_telem['_dbg_collective_sent_pos'] = pos_y_pos

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start(override=True)
                # utils.dbprint("blue", f"collective_pos: {collective_pos}")

    @override
    def ac_update_vrs_effect(self, telem_data: BaseTelemetryData):
        """Drive the Vortex Ring State buffet effect from HPG telemetry.

        The HPG helicopter exposes two VRS telemetry variables:

        - ``hpgVRSDatum`` (``vrs_onset``): An arbitrary progressive onset scale
          (0 = none, 1 = early onset → 33 % intensity, 2 = late onset → 66 %
          intensity).  Used to ramp the buffet up gradually as the helicopter
          approaches a confirmed VRS condition.
        - ``hpgVRSIsInVRS`` (``vrs_certain``): Set to 1 when the helicopter is
          confirmed to be in full Vortex Ring State → 100 % intensity.

        The resulting intensity is multiplied by ``vrs_effect_intensity`` (the
        user-configured maximum magnitude) before being applied to the periodic
        buffet effect.  The effect is only started when both ``vrs_effect_enable``
        and a non-zero ``vrs_effect_intensity`` are set; otherwise the effect is
        disposed to stop any residual output.
        """
        vrs_onset = telem_data.hpgVRSDatum or 0
        vrs_certain = telem_data.hpgVRSIsInVRS or 0

        if vrs_certain:
            vrs_intensity = 1.0
        elif vrs_onset == 1:
            vrs_intensity = .33
        elif vrs_onset == 2:
            vrs_intensity = .66
        else:
            vrs_intensity = 0

        if vrs_intensity and self.vrs_effect_intensity and self.vrs_effect_enable:
            self.effects["vrs_buffet"].periodic(10, self.vrs_effect_intensity * vrs_intensity, utils.RandomDirectionModulator).start()
        else:
            self.effects.dispose("vrs_buffet")
