import time

import telemffb.globals as G
import telemffb.utils as utils
from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn


import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class MsfsXpTrimwheelMixIn(MsfsXpFlightControlsMixIn):
    """Mixin for MSFS and X-Plane specific trim wheel handling."""

    # user parameters
    trimwheel_use_axis: bool = False
    trimwheel_elev_up_button: int = 0
    trimwheel_elev_dn_button: int = 0
    trimwheel_use_master_buttons: bool = False
    trimwheel_axis_invert: bool = False
    trimwheel_ap_spring_gain = 1.0
    trimwheel_dampening_gain = 0.0
    # trimwheel_spring_coeff_y = 0.0  # not used
    # end of user parameters

    def __init__(self):
        super().__init__()
        self.trimwheel_init = 0
        self.last_trimwheel_y = None
        self.last_pos_y_pos = 0.0
        self.trim_active = False
        self._tw_limits_warned = False
        self._tw_hold_prev = False
        self._tw_hold_pos = None       # wheel position parked during a calibration hold
        self._tw_resync_tol = 0.003    # trim_active release tolerance (widened post-hold)

    # NOTE: no on_telemetry hook here. Trimwheel devices are single-purpose:
    # Aircraft.on_telemetry gates them BEFORE the cooperative effects chain
    # and calls msfs_update_trimwheel directly, so no haptic effect can play
    # through the wheel.

    def msfs_update_trimwheel(self, telem_data: BaseTelemetryData):
        """Drive trimwheel spring position from sim elevator trim and send trim to the sim.

        Two write methods, selected per aircraft via ``trimwheel_use_axis``:
          - Direct (default, ``False``): write the ELEVATOR TRIM POSITION
            SimVar itself, mapping the wheel through the aircraft's trim
            travel limits. Works on most aircraft.
          - Axis (``True``): send the AXIS_ELEV_TRIM_SET key event (or the
            custom Y-axis variable when configured). Needed by aircraft whose
            systems ignore direct surface writes (see per-model overrides in
            defaults.xml, e.g. C208B, DA40, DA42).

        Telemetry:
            Read (MSFS):   APMaster       - Union[bool, int] (0 or 1); autopilot engaged state
            Read (XPLANE): APServos       - Union[bool, int] (0 or 1); autopilot servo state
            Read: ElevTrim         - float (degrees); raw elevator trim angle; direct mode only
                  ElevTrimMax      - float (degrees); upper trim travel limit; primary source
                  ElevTrimUpLmt    - float (degrees); fallback upper trim limit
                  ElevTrimMin      - float (degrees); lower trim travel limit; primary source
                  ElevTrimDnLmt    - float (degrees); fallback lower trim limit
                  ElevTrimPct      - float (–1.0 to 1.0); normalized elevator trim pct;
                                     axis mode, spring initialization, and direct-mode
                                     fallback when no usable limits are reported
            Written: trimwheel_pos_calc (float, –1.0 to 1.0; scaled trimwheel position)
                     phys_y             (float, –1.0 to 1.0; raw physical Y axis position)
                     _tw_cpO_y          (float; spring center offset, normalized)
                     _tw_phys_y_pos     (float, –1.0 to 1.0; physical Y before scaling)
                     _tw_phys_y_pos_neg (float; negated physical Y)
                     _tw_pos_y_pos      (int or float; scaled position sent to MSFS)
                     TRIM_DELTA         (float; delta between physical and trim positions)
                     _tw_last           (int or float; last position sent to sim)
        """
        if not self.is_trimwheel():
            return
        # Either switch turns the wheel's axis output off: the global
        # "TelemFFB controls axes", or the per-device disable.  The old
        # `not A and not B` form let a locally-disabled wheel keep driving
        # the sim's trim axis.
        if not self.telemffb_controls_axes or self.local_disable_axis_control:
            return

        if not self._device_feeding():  # device unplugged; nothing to send
            return

        ap_active = 0
        if self._sim_is_msfs():
            ap_active = telem_data.APMaster or 0
        if self._sim_is_xplane():
            ap_active = telem_data.APServos or 0

        phys_x, phys_y = self._get_device_axes()
        self._spring_handle.name = "trimwheel_ap_spring"

        # A running trim calibration owns the sim's trim: hold all writes back
        # to the sim (two absolute writers fight every frame) and PARK the
        # wheel where it sits. Following the sweep would be pointless motion —
        # the calibrator restores the starting trim at the end, so the parked
        # position is already the correct post-run position; chasing the sweep
        # only risks ending displaced and wedging the re-sync latch.
        hold = time.perf_counter() < G.trimcal_hold_until
        if hold and not self._tw_hold_prev:
            self._tw_hold_pos = phys_y
        if self._tw_hold_prev and not hold:
            # Calibration released the trim: reuse the button-trim latch so no
            # position is sent until the wheel matches the restored trim. The
            # restored trim ≈ the parked position, so this clears right away;
            # the widened tolerance covers restore/readback imprecision.
            self.trim_active = True
            self._tw_resync_tol = 0.02
        self._tw_hold_prev = hold

        # Sim-reported trim in normalized wheel space: direct mode maps
        # ElevTrim (degrees) through the travel limits, axis mode (or a
        # limits-less direct mode) reads ElevTrimPct.
        trim_limits = None if self.trimwheel_use_axis else self._trimwheel_trim_limits(telem_data)
        trimwheel_pos = self._trimwheel_sim_pos(telem_data, trim_limits)
        if not self.trimwheel_use_axis:
            telem_data.trimwheel_pos_calc = trimwheel_pos

        telem_data.phys_y = phys_y
        if not self.trimwheel_init:
            self.spring_y.set_coefficient(1.0)

            if self.last_trimwheel_y is None:
                # Air start or new aircraft.  Use sim defined trim setpoint as init point
                trimwheel_pos = telem_data.ElevTrimPct
                self.cpO_y = utils.clamp(trimwheel_pos, -1, 1)
            else:
                # In air, previously paused.  Use stored position to init point
                self.cpO_y = utils.clamp(self.last_trimwheel_y, -1, 1)

            self.spring_y.set_coefficient(1.0)

            self.spring_y.set_offset(self.cpO_y)

            self._spring_handle.setCondition(self.spring_y)
            # self.damper.damper(coef_y=int(4096*self.trimwheel_dampening_gain)).start()
            self._spring_handle.start(override=True)
            # print(f"self.cpO_y:{self.cpO_y}, phys_y:{phys_y}")
            if self.cpO_y - 0.1 < phys_y < self.cpO_y + 0.1:
                # dont start sending position until physical stick has centered
                self.trimwheel_init = 1
                logging.info("Trim Wheel Initialized")
            else:
                # if self._sim_is_msfs():
                #     self._simconnect.send_event_to_msfs(y_var, self.last_trimwheel_y)
                return
        self.last_trimwheel_y = phys_y

        if ap_active == 0:

            # trimwheel_pos = self.dampener.dampen_value(trimwheel_pos, '_elev_trim', derivative_hz=5, derivative_k=0.15)
            # Parked at the hold-onset position during a calibration hold;
            # normal sim-trim follow otherwise.
            spring_pos = trimwheel_pos
            if hold and self._tw_hold_pos is not None:
                spring_pos = self._tw_hold_pos
            self.cpO_y = utils.clamp(spring_pos, -1, 1)
            telem_data._tw_cpO_y = spring_pos
            self.spring_y.set_offset(spring_pos)

            # self.damper.damper(coef_y=0).start()
            self.spring_y.set_coefficient(self.trimwheel_ap_spring_gain, True)

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start(override=True)

            if self._sim_is_xplane():  # unknown if this works
                pos_y_pos = utils.scale(phys_y, (-1, 1), (1, 0))
                if self.trimwheel_init and not hold:
                    self.send_xp_command(f"AXIS:cy={round(pos_y_pos, 5)}")

            if self._sim_is_msfs():
                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = "AXIS_ELEV_TRIM_SET"
                    y_range = 16384

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))
                telem_data._tw_phys_y_pos = phys_y
                telem_data._tw_phys_y_pos_neg = -phys_y
                telem_data._tw_pos_y_pos = pos_y_pos
                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.check_button_press(
                    self.trimwheel_elev_up_button, self.trimwheel_use_master_buttons
                ) or self.check_button_press(self.trimwheel_elev_dn_button, self.trimwheel_use_master_buttons):
                    self.trim_active = True
                    return

                delta = round(phys_y - trimwheel_pos, 5)
                # utils.dbprint('yellow', f"delta: {delta}")
                telem_data.TRIM_DELTA = delta
                if self.trim_active:
                    if abs(delta) <= self._tw_resync_tol:
                        self.trim_active = False
                        self._tw_resync_tol = 0.003   # back to the button-trim default

                if not self.trim_active and not hold:
                    if self.trimwheel_use_axis:
                        if self.trimwheel_axis_invert:
                            pos_y_pos = -pos_y_pos
                            phys_y = -phys_y
                        self._simconnect.send_event_to_msfs(y_var, pos_y_pos)
                        # print("TRIM POSITION", pos_y_pos)
                    elif trim_limits is not None:
                        # Direct mode: map the wheel through the trim travel
                        # and write the surface position itself (deg -> rad).
                        pos_y_pos = utils.scale(phys_y, (-1, 1), trim_limits)
                        pos_y_pos = pos_y_pos * 0.01745
                        self._simconnect.set_simdatum_to_msfs("ELEVATOR TRIM POSITION", pos_y_pos, units="radians")
                        # print("TRIM TRIM POSITION", pos_y_pos)
                self.last_pos_y_pos = pos_y_pos
                telem_data._tw_last = self.last_pos_y_pos

        else:
            # trimwheel_pos = self.dampener.dampen_value(trimwheel_pos, '_elev_trim', derivative_hz=5, derivative_k=0.15)
            self.spring_y.set_offset(trimwheel_pos)
            # self.damper.damper(coef_y=0).start()
            self.spring_y.set_coefficient(self.trimwheel_ap_spring_gain, True)

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start(override=True)

    def _trimwheel_trim_limits(self, telem_data: BaseTelemetryData):
        """Elevator trim travel ``(min_deg, max_deg)`` for direct-mode writes.

        MSFS 2024 reports ElevTrimMax/Min; 2020 only the Up/Dn limit vars, and
        some aircraft omit the lower limit (assume symmetric travel). Returns
        None (warning once) when no usable travel is reported — the caller then
        falls back to the ElevTrimPct read-back and skips surface writes.
        """
        max_deg = telem_data.ElevTrimMax
        if max_deg is None:
            max_deg = telem_data.ElevTrimUpLmt
        min_deg = telem_data.ElevTrimMin
        if min_deg is None:
            min_deg = telem_data.ElevTrimDnLmt
        if min_deg is None and max_deg is not None:
            min_deg = -max_deg
        if max_deg is None or min_deg is None or max_deg == min_deg:
            if not self._tw_limits_warned:
                logging.warning("Trimwheel direct mode: no usable elevator trim limits in "
                                "telemetry; using ElevTrimPct and skipping surface writes")
                self._tw_limits_warned = True
            return None
        return (min_deg, max_deg)

    def _trimwheel_sim_pos(self, telem_data: BaseTelemetryData, trim_limits):
        """Normalized (–1..1) wheel position mirroring the sim's current trim."""
        if trim_limits is not None and telem_data.ElevTrim is not None:
            return utils.scale(telem_data.ElevTrim, trim_limits, (-1, 1))
        return telem_data.ElevTrimPct or 0
