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
"""Automatic calibration of the elevator ``virtual_y`` trim-following gain.

Background
----------
TelemFFB makes an FFB stick "follow" in-sim trim by (1) shifting the spring
center point so the physical stick relocates (relieving pressure like a real
trim tab), and (2) subtracting a *virtual* offset from the measured stick
position before sending it to the sim as ``AXIS_ELEVATOR_SET`` so the sim does
not get a second, redundant elevator input on top of its own trim deflection.
The balance knob is ``joystick_trim_follow_gain_virtual_y``.

With the stick held at a fixed position, the only trim-dependent term in the
axis sent to the sim is ``-ElevTrimPct * physical_y * (1 - virtual_y)``. For the
nose to stay level as trim changes, the elevator axis must move to exactly
cancel the trim's pitch effect. If ``k`` is the elevator-axis change needed per
unit trim change (``k = -slope`` of the measured ``elevator_axis(trim)`` line),
matching slopes gives the closed form::

    virtual_y = 1 - k / physical_y  ==  1 + slope / physical_y

This module flies the aircraft straight-and-level (hands-off), sweeps the sim's
own elevator trim, records the elevator axis its leveling loop must hold at each
trim setting, and back-solves ``virtual_y`` from the measured slope.

Design
------
The calibrator is a self-contained state machine driven one step per telemetry
frame by :meth:`update`. It is composed onto the joystick aircraft instance
(held as ``self.ac``) and reuses that instance's axis-output helpers so its
elevator/aileron commands travel through the *identical* scale + sign-inversion
path as normal trim-following — this is what keeps the measured slope in the
same units as the runtime offset math.

All output goes through the sim; the physical stick is held centered by a firm
spring only so the hands-off stick does not drift. The measurement is
independent of the physical stick position.
"""

import enum
import logging
import time

from telemffb.utils import PID, clamp

logger = logging.getLogger(__name__)


class CalState(enum.Enum):
    IDLE = "idle"
    PROBE = "probe"          # determine control polarity (elevator, then aileron)
    STABILIZE = "stabilize"  # close the leveling loops and wait for steady flight
    SWEEP = "sweep"          # ramp the sim trim and record elevator axis at each station
    SOLVE = "solve"          # fit the samples and compute virtual_y
    RESTORE = "restore"      # ramp trim back to the starting point while leveling
    DONE = "done"
    ABORT = "abort"


class TrimCalibrator:
    """Elevator ``virtual_y`` auto-calibration engine (joystick, MSFS/X-Plane).

    Only elevator (pitch) is calibrated. Ailerons are commanded solely to hold
    wings level so the pitch measurement stays clean; nothing about the aileron
    channel is written back.
    """

    # ---- Tunable constants (grouped for easy in-sim tuning) -----------------

    # Leveling loops. Output is a normalized elevator/aileron command in the
    # same [-1, 1] space as the trim-following ``y_pos``/``x_pos``.
    # PITCH_KD acts on d(VS)/dt, which leads the VS error in the phugoid cycle —
    # it is the main damping term; keep it healthy relative to KP.
    PITCH_KP = 0.06
    PITCH_KI = 0.03
    PITCH_KD = 0.06
    ROLL_KP = 0.020
    ROLL_KD = 0.010
    ROLL_KI = 0.004
    VS_TARGET = 0.0               # m/s; hold level flight

    # Polarity probe.
    AUTO_DETECT_POLARITY = True
    PROBE_U = 0.08               # normalized axis command applied during a probe
    PROBE_TIME_S = 1.2           # per-axis probe window (upper bound)
    PROBE_BASELINE_S = 0.4       # no-input drift-measurement window before each probe
    PROBE_MIN_VS = 0.15          # m/s; min |dVS| to trust the elevator probe
    PROBE_MIN_ROLL = 1.0         # deg; min |dRoll| to trust the aileron probe
    PROBE_VS_EXIT = 0.6          # m/s; |dVS| at which the elevator probe ends early
    PROBE_ROLL_EXIT = 2.5        # deg; |dRoll| at which the aileron probe ends early
    ELEV_SIGN_DEFAULT = 1.0      # used if auto-detect is off or inconclusive
    AIL_SIGN_DEFAULT = 1.0

    # Stabilization gate (must hold within tolerance for the dwell window).
    STABLE_VS_TOL = 0.50         # m/s (~100 fpm)
    STABLE_ROLL_TOL = 3.0        # deg
    STABLE_DWELL_S = 2.0
    STABILIZE_TIMEOUT_S = 25.0

    # Trim sweep. The band is ADAPTIVE: starting from the center station the
    # sweep walks outward in SWEEP_STEP increments, expanding each side until
    # the elevator needed to hold level exceeds SWEEP_U_BUDGET (relative to
    # the center station), the half-width cap is hit, or the station cap is
    # reached. Strong-trim aircraft get a narrow band automatically; weak-trim
    # aircraft get a wide one — the fit sees comparable elevator excursion on
    # every aircraft, and nonlinearity over the usable range becomes visible.
    SWEEP_STEP = 0.06            # ElevTrimPct distance between stations
    SWEEP_MAX_HALF = 0.45        # max band half-width each side of trim0
    SWEEP_MAX_STATIONS = 17      # hard cap on total stations
    SWEEP_U_BUDGET = 0.45        # stop expanding a side beyond this |u - u_center|
    STEP_SETTLE_TIMEOUT_S = 15.0
    STEP_DWELL_S = 1.5           # averaging window once settled at a step
    # A dwell is only accepted if the MEAN VS over the window is this small.
    # The instantaneous gate (STABLE_VS_TOL) is loose because real air jitters;
    # the mean gate rejects one-sided residuals from a still-converging PID,
    # which otherwise bias the recorded elevator in a trim-correlated way.
    SAMPLE_VS_MEAN_TOL = 0.15    # m/s

    # Trim actuation. Commands are slew-rate limited — an instantaneous trim
    # step acts like yanking the stick and pitches the aircraft violently.
    TRIM_RATE_PER_S = 0.04       # command slew rate, full-range fraction per second
    TRIM_STATION_TOL = 0.02      # read-back must be this close to the station target
    # Command-direction verification: after this much commanded movement the
    # read-back must have moved the same way, else the command sign is flipped
    # (AXIS_ELEV_TRIM_SET polarity varies with aircraft, cf. trimwheel_axis_invert).
    TRIM_SIGN_CHECK_DIST = 0.03
    TRIM_RESPOND_MIN = 0.01      # read-back movement below this = "not responding"

    # Elevator-authority guard: sustained command beyond this during the sweep
    # means the trim band exceeds what the elevator can hold level against.
    U_ELEV_SAT = 0.7
    U_ELEV_SAT_TIME_S = 0.7
    MIN_SAMPLES_FOR_FIT = 4      # truncated sweep still solves with this many

    # Result acceptance / setting bounds.
    VIRTUAL_Y_MIN = -2.0         # matches the -200%..200% setting range
    VIRTUAL_Y_MAX = 2.0
    LINEAR_R2_OK = 0.95          # below this -> flag "a curve may be needed"

    # Safety envelope. Any breach aborts.
    # Pitch is guarded RELATIVE to the attitude captured at start: the trimmed
    # deck angle is rarely 0 and MSFS reports PLANE PITCH DEGREES sign-flipped
    # (negative = nose up), so an absolute limit misfires on normal attitudes.
    MAX_PITCH_EXCURSION_DEG = 15.0
    MAX_PITCH_DEG = 20.0         # pre-start "roughly level" gate only (can_start)
    MAX_ROLL_DEG = 30.0
    # Pre-start roll gate, much tighter than the in-flight abort limit: the
    # polarity probe must begin from wings-level or its measurements start
    # contaminated (and any wrong prior feeds on the existing bank).
    START_MAX_ROLL_DEG = 10.0
    IAS_BAND_FRAC = 0.30         # abort if IAS drifts more than ±30% from start
    TELEM_TIMEOUT_S = 1.0        # no telemetry for this long -> abort

    HOLD_SPRING_COEFF = 1.0      # firm centered spring to keep hands-off stick put
    TRIM_AXIS_RANGE = 16383      # AXIS_ELEV_TRIM_SET: -16383..16384

    def __init__(self, aircraft):
        self.ac = aircraft
        self.state = CalState.IDLE
        self._active = False
        self.status_message = "Idle"
        self.progress = 0.0
        self.result = None          # dict on success, else None
        self.abort_reason = None
        # Set from the UI thread by stop(); consumed in the telemetry thread so
        # all sim I/O (trim restore, spring release) stays on one thread.
        self._stop_requested = False
        self._stop_reason = None

        # runtime state (initialized on start / per phase)
        self._pitch_pid = PID(self.PITCH_KP, self.PITCH_KI, self.PITCH_KD,
                              output_limits=(-1.0, 1.0), integral_limit=1.0,
                              derivative_lpf_hz=4.0)
        self._roll_pid = PID(self.ROLL_KP, self.ROLL_KI, self.ROLL_KD,
                             output_limits=(-1.0, 1.0), integral_limit=1.0,
                             derivative_lpf_hz=4.0)
        self._elev_sign = self.ELEV_SIGN_DEFAULT
        self._ail_sign = self.AIL_SIGN_DEFAULT
        self._u_elev = 0.0
        self._u_ail = 0.0

        self._reset_runtime()

    def _reset_runtime(self):
        self._last_t = None
        self._phase_start_t = None
        self._probe_stage = 0            # 0 = elevator, 1 = aileron
        self._probe_phase = "baseline"   # "baseline" (measure drift) then "probe"
        self._probe_ref = None           # reference value at sub-phase start
        self._probe_drift = 0.0          # drift rate measured over the baseline window
        self._stable_since = None
        self._trim0 = 0.0
        self._ias0 = None
        self._pitch0 = None              # attitude at start; pitch guard is relative to this
        self._sweep_targets = []         # visited station targets (teardown flag)
        self._current_target = None
        self._side = 0                   # 0 = center station, then +1 side, then -1
        self._side_done = {1: False, -1: False}
        self._edge = {}                  # outermost target commanded per side
        self._u_center = None            # steady-state u at the center station
        self._step_settle_deadline = None
        self._dwell_since = None
        self._dwell_samples = []
        self._samples = []               # [(ElevTrimPct, u_elev)] steady-state points
        self._station_ias = []           # mean IAS per accepted station (drift diagnostics)
        # trim command state (read-back space; _set_trim maps to command space)
        self._trim_cmd = 0.0
        self._trim_sign = 1.0            # flipped at runtime if read-back moves opposite
        self._trim_dir_verified = False
        self._sign_cmd_accum = 0.0
        self._sign_rb_start = 0.0
        self._sign_flips = 0
        self._u_sat_since = None
        # takeover smoothness: hold the stick where it rests and start the
        # axis output where the normal path left it (captured on first frame)
        self._hold_offs = None
        self._u_base_x = 0.0
        self._u_base_y = 0.0
        self._u_base_captured = False
        self._pitch_pid.reset()
        self._roll_pid.reset()

    # ---- Public API (called from the UI thread) -----------------------------

    @property
    def active(self):
        """True while the engine owns the axis output; the flight-controls hook
        delegates to :meth:`update` only while this is True."""
        return self._active

    def can_start(self, telem_data):
        """Return (ok, message) describing whether calibration may start now.

        Also used by the dialog for the live "ready to calibrate" indicator.
        """
        if not (self.ac.telemffb_controls_axes and not self.ac.local_disable_axis_control):
            return False, "Enable 'TelemFFB Controls Axes' (axis control) first"
        if telem_data is None:
            return False, "Waiting for telemetry"
        if telem_data.get("SimOnGround", 1):
            return False, "Aircraft must be airborne"
        for field in ("VerticalSpeed", "Pitch", "Roll", "IAS", "ElevTrimPct"):
            if telem_data.get(field) is None:
                return False, f"Telemetry '{field}' unavailable for this aircraft/sim"
        if self._ap_engaged(telem_data):
            return False, "Disengage the autopilot"
        if abs(telem_data.get("Pitch", 0)) > self.MAX_PITCH_DEG or \
                abs(telem_data.get("Roll", 0)) > self.START_MAX_ROLL_DEG:
            return False, "Get roughly straight-and-level first"
        return True, "Ready to calibrate"

    def start(self):
        """Arm the engine. Baseline is captured on the first :meth:`update`.

        ``_active`` is set last so the telemetry thread only ever observes a
        fully-initialized engine.
        """
        self.result = None
        self.abort_reason = None
        self.progress = 0.0
        self._stop_requested = False
        self._stop_reason = None
        self._reset_runtime()
        # NOTE: _elev_sign/_ail_sign deliberately persist across runs. Control
        # polarity is a property of the aircraft, and re-runs are safer using
        # the last detected values (the probe re-detects them every run
        # regardless; resetting to defaults threw that knowledge away).
        self.status_message = "Starting…"
        self.state = CalState.PROBE if self.AUTO_DETECT_POLARITY else CalState.STABILIZE
        self._active = True
        logger.info("Trim calibration started")

    def stop(self, reason="Cancelled by user"):
        """User-requested abort/stop (thread-safe).

        Only flags the request; the abort (and its sim I/O) is performed on the
        next :meth:`update` in the telemetry thread.
        """
        if self._active:
            self._stop_reason = reason
            self._stop_requested = True

    def force_abort(self, reason):
        """Abort immediately in the caller's thread.

        Unlike :meth:`stop` (which defers to the next :meth:`update`), this is
        for when ``update()`` itself raised — there may be no further clean
        update frames coming, so control must be released right now.
        """
        try:
            self._abort(reason)
        except Exception:
            # Absolute last resort: drop the active flag so the flight
            # controls hook resumes normal processing on the next frame.
            logger.exception("force_abort teardown failed; releasing control flag")
            self.abort_reason = reason
            self.status_message = f"Aborted: {reason}"
            self.state = CalState.ABORT
            self._active = False

    # ---- Per-frame driver (called from the telemetry thread) ----------------

    def update(self, telem_data):
        """Advance the state machine one telemetry frame."""
        if self._stop_requested:
            self._stop_requested = False
            self._abort(self._stop_reason or "Cancelled by user")
            return

        now = time.perf_counter()
        dt = 0.0 if self._last_t is None else now - self._last_t

        # Telemetry-gap safety: a long gap means we lost the sim; bail out.
        if self._last_t is not None and dt > self.TELEM_TIMEOUT_S:
            self._last_t = now
            self._abort("Lost telemetry")
            return
        self._last_t = now

        if not self._precheck(telem_data):
            return

        if not self._u_base_captured:
            self._capture_takeover_baseline(telem_data)

        if self.state == CalState.PROBE:
            self._do_probe(telem_data, dt)
        elif self.state == CalState.STABILIZE:
            self._do_stabilize(telem_data, dt)
        elif self.state == CalState.SWEEP:
            self._do_sweep(telem_data, dt)
        elif self.state == CalState.RESTORE:
            self._do_restore(telem_data, dt)
        # SOLVE/DONE/ABORT are terminal and handled inline when reached.

    # ---- Safety --------------------------------------------------------------

    def _ap_engaged(self, telem_data):
        if self.ac._sim_is_msfs():
            return bool(telem_data.get("APMaster", 0))
        if self.ac._sim_is_xplane():
            return bool(telem_data.get("APServos", 0))
        return False

    def _precheck(self, telem_data):
        """Common per-frame safety checks; abort and return False on breach."""
        if self._ap_engaged(telem_data):
            self._abort("Autopilot engaged")
            return False
        if telem_data.get("SimOnGround", 0):
            self._abort("Aircraft on ground")
            return False
        pitch = telem_data.get("Pitch")
        roll = telem_data.get("Roll")
        vs = telem_data.get("VerticalSpeed")
        ias = telem_data.get("IAS")
        if pitch is None or roll is None or vs is None or ias is None:
            self._abort("Required telemetry unavailable")
            return False
        if self._pitch0 is None:
            self._pitch0 = pitch
        pitch_exc = pitch - self._pitch0
        if abs(pitch_exc) > self.MAX_PITCH_EXCURSION_DEG:
            self._abort(f"Pitch moved {pitch_exc:+.1f} deg from start "
                        f"(limit +/-{self.MAX_PITCH_EXCURSION_DEG:.0f} deg)")
            return False
        if abs(roll) > self.MAX_ROLL_DEG:
            self._abort(f"Roll reached {roll:+.1f} deg "
                        f"(limit +/-{self.MAX_ROLL_DEG:.0f} deg)")
            return False
        if self._ias0 is not None:
            lo = self._ias0 * (1 - self.IAS_BAND_FRAC)
            hi = self._ias0 * (1 + self.IAS_BAND_FRAC)
            if ias < lo or ias > hi:
                self._abort("Airspeed drifted out of range")
                return False
        return True

    # ---- Takeover ------------------------------------------------------------

    def _capture_takeover_baseline(self, telem_data):
        """Bumpless takeover: start the axis output where the normal
        trim-following path left it. Engaging the calibrator with a zero
        command would step the axes (the runtime was delivering
        ``phys - virtual_offset``) and disturb the flight path at start.
        """
        self._u_base_x = 0.0
        self._u_base_y = 0.0
        try:
            if getattr(self.ac, "trim_following", False):
                phys_x, phys_y = self.ac._get_device_raw_axes()
                t = telem_data.ElevTrimPct or 0
                a = telem_data.AileronTrimPct or 0
                p_y = self.ac.joystick_trim_follow_gain_physical_y
                v_y = self.ac.joystick_trim_follow_gain_virtual_y
                p_x = getattr(self.ac, "joystick_trim_follow_gain_physical_x", 1.0)
                v_x = getattr(self.ac, "joystick_trim_follow_gain_virtual_x", 1.0)
                self._u_base_y = clamp(phys_y - clamp(t * p_y, -1, 1) * (1 - v_y), -1, 1)
                self._u_base_x = clamp(phys_x - clamp(a * p_x, -1, 1) * (1 - v_x), -1, 1)
        except Exception as e:  # baseline is comfort-only; never block the run
            logger.debug(f"takeover baseline capture skipped: {e}")
        self._u_base_captured = True
        logger.info(f"Takeover baseline: u_elev={self._u_base_y:+.4f}, u_ail={self._u_base_x:+.4f}")

    # ---- Phase: polarity probe ----------------------------------------------

    def _probe_value(self, telem_data):
        return telem_data.VerticalSpeed if self._probe_stage == 0 else telem_data.Roll

    def _do_probe(self, telem_data, dt):
        now = time.perf_counter()
        if self._phase_start_t is None:
            # Each stage opens with a no-input baseline window measuring the
            # pre-existing drift (leftover phugoid VS trend, residual roll
            # rate — common right after a previous run's handoff). The probe
            # response is then judged against extrapolated drift instead of
            # the raw delta, otherwise a still-moving aircraft gets its
            # polarity misread and the leveling loops go unstable.
            self._phase_start_t = now
            self._probe_phase = "baseline"
            self._probe_ref = self._probe_value(telem_data)
            self.status_message = ("Probing elevator response…" if self._probe_stage == 0
                                   else "Probing aileron response…")

        elapsed = now - self._phase_start_t
        probe_on = self._probe_phase == "probe"

        if self._probe_stage == 0:
            # Elevator probe. The aileron gets NO closed-loop help here: its
            # polarity is unverified at this point, and a wrong-sign
            # wings-level term is positive feedback — seen in the field as a
            # hard roll at start when beginning with residual bank.
            self._u_elev = self._u_base_y + (self.PROBE_U if probe_on else 0.0)
            self._u_ail = self._u_base_x
            exit_threshold = self.PROBE_VS_EXIT
        else:
            # Aileron probe; hold pitch with the full PID using the elevator
            # sign detected in stage 0 (a weak hold lets the probe's climb
            # build into a zoom that trips the attitude guard).
            vs_err = self._elev_sign * (self.VS_TARGET - telem_data.VerticalSpeed)
            self._u_elev = self._u_base_y + self._pitch_pid.update(vs_err, dt)
            self._u_ail = self._u_base_x + (self.PROBE_U if probe_on else 0.0)
            exit_threshold = self.PROBE_ROLL_EXIT

        self._command_axes(telem_data)

        value = self._probe_value(telem_data)

        if not probe_on:
            if elapsed >= self.PROBE_BASELINE_S:
                self._probe_drift = (value - self._probe_ref) / max(elapsed, 1e-6)
                self._probe_phase = "probe"
                self._phase_start_t = now
                self._probe_ref = value
            return

        # Drift-compensated response to the probe input.
        delta = (value - self._probe_ref) - self._probe_drift * elapsed

        # End the probe as soon as the response is unambiguous — holding the
        # input for the full window just pumps energy into the aircraft.
        if elapsed >= self.PROBE_TIME_S or abs(delta) >= exit_threshold:
            if self._probe_stage == 0:
                if abs(delta) >= self.PROBE_MIN_VS:
                    self._elev_sign = 1.0 if delta > 0 else -1.0
                    logger.info(f"Elevator polarity detected: {self._elev_sign:+.0f} (dVS={delta:+.2f} m/s in {elapsed:.2f}s)")
                else:
                    logger.warning(f"Elevator probe inconclusive (dVS={delta:+.2f} m/s); "
                                   f"keeping sign {self._elev_sign:+.0f}")
                self._probe_stage = 1
                self._phase_start_t = None
                self._probe_ref = None
                self._probe_drift = 0.0
                self._pitch_pid.reset()  # fresh start for the recovery/leveling loop
            else:
                if abs(delta) >= self.PROBE_MIN_ROLL:
                    self._ail_sign = 1.0 if delta > 0 else -1.0
                    logger.info(f"Aileron polarity detected: {self._ail_sign:+.0f} (dRoll={delta:+.2f} deg in {elapsed:.2f}s)")
                else:
                    logger.warning(f"Aileron probe inconclusive (dRoll={delta:+.2f} deg); "
                                   f"keeping sign {self._ail_sign:+.0f}")
                self._enter_stabilize()

    # ---- Phase: stabilize ----------------------------------------------------

    def _enter_stabilize(self):
        self.state = CalState.STABILIZE
        self._phase_start_t = time.perf_counter()
        self._stable_since = None
        # Keep the pitch PID's state: it has been actively recovering the
        # aircraft since the elevator probe ended and its integral is useful.
        self._roll_pid.reset()
        self.status_message = "Stabilizing level flight..."

    def _do_stabilize(self, telem_data, dt):
        self._run_leveling(telem_data, dt)

        vs = telem_data.VerticalSpeed
        roll = telem_data.Roll
        now = time.perf_counter()

        # Live gate progress so the dialog shows what we're waiting on.
        self.status_message = (
            f"Stabilizing... VS {vs * 196.85:+.0f} fpm "
            f"(need within {self.STABLE_VS_TOL * 196.85:.0f}), "
            f"roll {roll:+.1f} deg (need within {self.STABLE_ROLL_TOL:.0f})"
        )

        if abs(vs) <= self.STABLE_VS_TOL and abs(roll) <= self.STABLE_ROLL_TOL:
            if self._stable_since is None:
                self._stable_since = now
            elif now - self._stable_since >= self.STABLE_DWELL_S:
                self._enter_sweep(telem_data)
                return
        else:
            self._stable_since = None

        if now - self._phase_start_t > self.STABILIZE_TIMEOUT_S:
            self._abort(
                f"Could not stabilize within {self.STABILIZE_TIMEOUT_S:.0f}s "
                f"(VS {vs * 196.85:+.0f} fpm, roll {roll:+.1f} deg)"
            )

    # ---- Phase: trim sweep ---------------------------------------------------

    def _enter_sweep(self, telem_data):
        self._trim0 = telem_data.ElevTrimPct
        self._ias0 = telem_data.IAS
        self._trim_cmd = self._trim0
        self._sign_rb_start = self._trim0
        self._sign_cmd_accum = 0.0
        self._samples = []
        self._sweep_targets = []
        self._side = 0
        self._side_done = {1: False, -1: False}
        self._edge = {1: self._trim0, -1: self._trim0}
        self._u_center = None
        self._begin_step(self._trim0)   # measure the center station first
        self.state = CalState.SWEEP
        logger.info(f"Sweep started (adaptive): trim0={self._trim0:.3f}, "
                    f"step={self.SWEEP_STEP}, max half-width={self.SWEEP_MAX_HALF}, "
                    f"u budget={self.SWEEP_U_BUDGET}")

    def _begin_step(self, target):
        self._current_target = target
        self._sweep_targets.append(target)
        # Deadline covers the ramp to the station plus the settle allowance.
        ramp_time = abs(target - self._trim_cmd) / self.TRIM_RATE_PER_S
        self._step_settle_deadline = time.perf_counter() + ramp_time + self.STEP_SETTLE_TIMEOUT_S
        self._dwell_since = None
        self._dwell_samples = []

    def _next_station_target(self, last_u):
        """Pick the next station, expanding outward; None ends the sweep.

        A side stops expanding once the steady-state elevator at its outermost
        station is more than SWEEP_U_BUDGET away from the center station's —
        that side of the band has consumed its share of elevator authority.
        """
        if self._side != 0 and self._u_center is not None and \
                abs(last_u - self._u_center) > self.SWEEP_U_BUDGET:
            self._side_done[self._side] = True
            logger.info(f"Sweep side {self._side:+d} reached elevator budget "
                        f"(|u - u_center| = {abs(last_u - self._u_center):.2f}); "
                        f"stopping expansion on that side")
        if len(self._samples) >= self.SWEEP_MAX_STATIONS:
            return None
        for side in (1, -1):  # finish the + side, then walk the - side
            if self._side_done[side]:
                continue
            cand = self._edge[side] + self.SWEEP_STEP * side
            if abs(cand - self._trim0) > self.SWEEP_MAX_HALF + 1e-9 or abs(cand) > 0.95:
                self._side_done[side] = True
                continue
            self._side = side
            self._edge[side] = cand
            return cand
        return None

    def _advance_trim(self, target, telem_data, dt):
        """Slew the trim command toward ``target`` and verify the read-back moves
        the same way (flipping the command sign once if it does not).

        Returns True if still ramping, False when the command has reached the
        target, or None if the run was aborted (unresponsive trim).
        """
        step = self.TRIM_RATE_PER_S * dt
        diff = target - self._trim_cmd
        if abs(diff) > 1e-9:
            delta = clamp(diff, -step, step)
            self._trim_cmd += delta
            self._sign_cmd_accum += delta
        self._set_trim(self._trim_cmd)

        # Direction verification against the read-back.
        if not self._trim_dir_verified and abs(self._sign_cmd_accum) >= self.TRIM_SIGN_CHECK_DIST:
            rb = telem_data.ElevTrimPct or 0
            rb_delta = rb - self._sign_rb_start
            if abs(rb_delta) >= self.TRIM_RESPOND_MIN:
                if (rb_delta > 0) == (self._sign_cmd_accum > 0):
                    self._trim_dir_verified = True
                    logger.info("Trim command direction verified against read-back")
                else:
                    self._sign_flips += 1
                    if self._sign_flips > 2:
                        self._abort("Trim read-back direction is erratic")
                        return None
                    self._trim_sign = -self._trim_sign
                    # Re-anchor the command at the current read-back so the
                    # flipped command does not jump the trim.
                    self._trim_cmd = rb
                    logger.warning("Trim moved opposite to command; flipping command sign "
                                   f"(now {self._trim_sign:+.0f})")
                self._sign_cmd_accum = 0.0
                self._sign_rb_start = rb
            elif abs(self._sign_cmd_accum) >= 1.5 * self.TRIM_SIGN_CHECK_DIST:
                # 1.5x (not 2x): must fire within a single station hop
                # (SWEEP_STEP) or an unresponsive trim only surfaces as a
                # vague settle-timeout at the station.
                self._abort("Trim is not responding to commands")
                return None

        return abs(target - self._trim_cmd) > 1e-9

    def _do_sweep(self, telem_data, dt):
        target = self._current_target
        ramping = self._advance_trim(target, telem_data, dt)
        if ramping is None:
            return  # aborted (unresponsive trim)
        self._run_leveling(telem_data, dt)

        now = time.perf_counter()

        # Elevator-authority guard (safety net behind the adaptive budget):
        # sustained near-full elevator means level flight is barely holdable.
        if abs(self._u_elev) > self.U_ELEV_SAT:
            if self._u_sat_since is None:
                self._u_sat_since = now
            elif now - self._u_sat_since > self.U_ELEV_SAT_TIME_S:
                if len(self._samples) >= self.MIN_SAMPLES_FOR_FIT:
                    logger.warning(f"Elevator near saturation; truncating sweep with "
                                   f"{len(self._samples)} samples")
                    self._solve()
                else:
                    self._abort("Elevator saturated before enough samples could be gathered")
                return
        else:
            self._u_sat_since = None

        at_station = (not ramping and
                      abs((telem_data.ElevTrimPct or 0) - target) <= self.TRIM_STATION_TOL)
        settled = at_station and \
            abs(telem_data.VerticalSpeed) <= self.STABLE_VS_TOL and \
            abs(telem_data.Roll) <= self.STABLE_ROLL_TOL

        phase = "ramping trim" if ramping else ("measuring" if settled else "settling")
        side_txt = "center" if self._side == 0 else ("nose-up side" if self._side > 0 else "nose-dn side")
        self.status_message = (
            f"Sweep station {len(self._samples) + 1} ({side_txt}): {phase} "
            f"(trim {100 * (telem_data.ElevTrimPct or 0):+.0f}%)"
        )

        if settled:
            if self._dwell_since is None:
                self._dwell_since = now
            # Record the steady-state elevator command and the actual trim read-back.
            self._dwell_samples.append(
                (telem_data.ElevTrimPct, self._u_elev, telem_data.VerticalSpeed,
                 telem_data.IAS or 0)
            )
            if now - self._dwell_since >= self.STEP_DWELL_S:
                n = len(self._dwell_samples)
                mean_vs = sum(s[2] for s in self._dwell_samples) / n
                if abs(mean_vs) > self.SAMPLE_VS_MEAN_TOL:
                    # Still converging (one-sided VS residual): retry the dwell
                    # rather than record a biased sample. The step deadline
                    # still bounds the retries.
                    self._dwell_since = None
                    self._dwell_samples = []
                else:
                    mean_trim = sum(s[0] for s in self._dwell_samples) / n
                    mean_u = sum(s[1] for s in self._dwell_samples) / n
                    mean_ias = sum(s[3] for s in self._dwell_samples) / n
                    self._samples.append((mean_trim, mean_u))
                    self._station_ias.append(mean_ias)
                    if self._side == 0:
                        self._u_center = mean_u
                    logger.info(
                        f"Station {len(self._samples)} ({side_txt}): "
                        f"trim {mean_trim:+.4f}, u_elev {mean_u:+.4f}, "
                        f"IAS {mean_ias * 1.94384:.1f} kt"
                    )
                    self.progress = len(self._samples) / self.SWEEP_MAX_STATIONS
                    nxt = self._next_station_target(mean_u)
                    if nxt is None:
                        if len(self._samples) >= self.MIN_SAMPLES_FOR_FIT:
                            self._solve()
                        else:
                            self._abort("Trim band exhausted before enough samples "
                                        "could be gathered")
                        return
                    self._begin_step(nxt)
        else:
            self._dwell_since = None
            self._dwell_samples = []

        if now > self._step_settle_deadline:
            self._abort(
                f"Could not hold level at sweep station {len(self._samples) + 1} "
                f"(VS {telem_data.VerticalSpeed * 196.85:+.0f} fpm)"
            )

    # ---- Phase: solve --------------------------------------------------------

    def _solve(self):
        self.state = CalState.SOLVE
        physical_y = self.ac.joystick_trim_follow_gain_physical_y or 1.0
        slope, intercept, r2 = self._fit(self._samples)

        # virtual_y = 1 - k/physical_y, with k = -slope  ->  1 + slope/physical_y
        if physical_y == 0:
            self._abort("Physical Y gain is 0; set it before calibrating")
            return
        virtual_y = 1.0 + slope / physical_y
        virtual_y = clamp(virtual_y, self.VIRTUAL_Y_MIN, self.VIRTUAL_Y_MAX)

        # IAS drift across the sweep contaminates the slope (static stability
        # vs speed masquerades as trim coupling) — surface it as a warning.
        ias_drift = 0.0
        if len(self._station_ias) >= 2 and self._station_ias[0]:
            ias_drift = (self._station_ias[-1] - self._station_ias[0]) / self._station_ias[0]

        # Per-side fit split at trim = 0: MSFS normalizes ELEVATOR TRIM PCT
        # per-side (up limit vs down limit), so aircraft with asymmetric trim
        # limits have a genuinely different slope on each side of neutral. A
        # single gain can only be a compromise between the two.
        split = None
        above = [s for s in self._samples if s[0] > 0]
        below = [s for s in self._samples if s[0] < 0]
        if len(above) >= 3 and len(below) >= 3:
            slope_a, _, _ = self._fit(above)
            slope_b, _, _ = self._fit(below)
            denom = max(abs(slope_a), abs(slope_b), 1e-9)
            split = {
                "slope_above": round(slope_a, 4),
                "slope_below": round(slope_b, 4),
                "virtual_y_above": round(clamp(1.0 + slope_a / physical_y,
                                               self.VIRTUAL_Y_MIN, self.VIRTUAL_Y_MAX), 4),
                "virtual_y_below": round(clamp(1.0 + slope_b / physical_y,
                                               self.VIRTUAL_Y_MIN, self.VIRTUAL_Y_MAX), 4),
                "mismatch": round(abs(slope_a - slope_b) / denom, 3),
            }
            logger.info(f"Per-side fit: {split}")

        self.result = {
            "virtual_y": round(virtual_y, 4),
            "physical_y": physical_y,
            "current_virtual_y": getattr(self.ac, "joystick_trim_follow_gain_virtual_y", None),
            "slope": slope,
            "intercept": intercept,
            "r_squared": round(r2, 4),
            "linear_ok": r2 >= self.LINEAR_R2_OK,
            "samples": list(self._samples),
            "station_ias": list(self._station_ias),
            "ias_drift": round(ias_drift, 4),
            "split": split,
            "trim0": self._trim0,
        }
        self._done_status = (
            f"Done — Virtual Y = {virtual_y:.3f}"
            + ("" if r2 >= self.LINEAR_R2_OK else "  (non-linear: a curve may be needed)")
        )
        self.progress = 1.0
        logger.info(f"Trim calibration result: {self.result}")
        self._enter_restore()

    # ---- Phase: restore trim -------------------------------------------------

    def _enter_restore(self):
        self.state = CalState.RESTORE
        self.status_message = "Restoring trim..."

    def _do_restore(self, telem_data, dt):
        """Ramp trim back to the starting point while still holding level, then
        release control. Avoids handing the aircraft back with a trim jump."""
        ramping = self._advance_trim(self._trim0, telem_data, dt)
        if ramping is None:
            return  # aborted (unresponsive trim)
        self._run_leveling(telem_data, dt)
        if not ramping:
            self.status_message = getattr(self, "_done_status", "Done")
            self._finish(CalState.DONE)

    @staticmethod
    def _fit(samples):
        """Ordinary least-squares fit of y = slope*x + intercept, plus R²."""
        n = len(samples)
        if n < 2:
            return 0.0, 0.0, 0.0
        xs = [s[0] for s in samples]
        ys = [s[1] for s in samples]
        mx = sum(xs) / n
        my = sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        slope = sxy / sxx if sxx else 0.0
        intercept = my - slope * mx
        ss_tot = sum((y - my) ** 2 for y in ys)
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
        r2 = 1.0 - ss_res / ss_tot if ss_tot else 1.0
        return slope, intercept, r2

    # ---- Leveling loops ------------------------------------------------------

    def _run_leveling(self, telem_data, dt):
        """Drive elevator (VS→0) and aileron (Roll→0), then command the axes.

        Both outputs ride on the takeover baseline so control picks up where
        the normal path left off; the recorded ``_u_elev`` is the TOTAL
        delivered axis, which is what the level-flight condition constrains
        (the constant baseline shifts the fit's intercept, never its slope).
        """
        vs_err = self._elev_sign * (self.VS_TARGET - telem_data.VerticalSpeed)
        self._u_elev = self._u_base_y + self._pitch_pid.update(vs_err, dt)

        roll_err = self._ail_sign * (0.0 - telem_data.Roll)
        self._u_ail = self._u_base_x + self._roll_pid.update(roll_err, dt)

        self._command_axes(telem_data)

    # ---- Output --------------------------------------------------------------

    def _command_axes(self, telem_data):
        """Send the current elevator/aileron commands to the sim, and hold the
        physical stick at its takeover rest position with a firm spring
        (hands-off comfort only).

        Routes through the aircraft's own axis helpers so the scale + sign
        inversion match normal trim-following exactly.
        """
        u_ail = clamp(self._u_ail, -1.0, 1.0)
        u_elev = clamp(self._u_elev, -1.0, 1.0)
        x_scale = clamp(self.ac.joystick_x_axis_scale, 0, 1)
        y_scale = clamp(self.ac.joystick_y_axis_scale, 0, 1)

        if self.ac._use_firmware_axis_backend():
            self.ac._send_firmware_fixed_axes(u_ail * x_scale, u_elev * y_scale)
        elif self.ac._sim_is_xplane():
            self.ac.send_xp_command(
                f"AXIS:jx={round(u_ail * x_scale, 5)},jy={round(u_elev * y_scale, 5)}"
            )
        elif self.ac._sim_is_msfs():
            x_var, x_range = self.ac._get_msfs_axis_config('x', "AXIS_AILERONS_SET")
            y_var, y_range = self.ac._get_msfs_axis_config('y', "AXIS_ELEVATOR_SET")
            self.ac._send_msfs_axis_value(x_var, u_ail, x_range, x_scale)
            self.ac._send_msfs_axis_value(y_var, u_elev, y_range, y_scale)

        self._hold_stick_centered()

    def _hold_stick_centered(self):
        try:
            if self._hold_offs is None:
                # Hold the stick where it currently rests (the trim-following
                # spring center). Snapping the center to 0/0 would yank the
                # controls out of their trimmed position at takeover.
                self._hold_offs = (int(self.ac.spring_x.cpOffset),
                                   int(self.ac.spring_y.cpOffset))
                logger.info(f"Holding stick at spring offsets "
                            f"x={self._hold_offs[0]}, y={self._hold_offs[1]}")
            self.ac.spring_x.cpOffset = self._hold_offs[0]
            self.ac.spring_y.cpOffset = self._hold_offs[1]
            self.ac.spring_x.set_coefficient(self.HOLD_SPRING_COEFF)
            self.ac.spring_y.set_coefficient(self.HOLD_SPRING_COEFF)
            self.ac._spring_handle.name = "trim_cal_spring"
            self.ac._spring_handle.setCondition(self.ac.spring_x)
            self.ac._spring_handle.setCondition(self.ac.spring_y)
            self.ac._spring_handle.start()
        except Exception as e:  # spring is comfort-only; never let it break the loop
            logger.debug(f"trim-cal spring hold skipped: {e}")

    def _set_trim(self, pct):
        """Command the sim's elevator trim to an absolute normalized position.

        ``pct`` is in ElevTrimPct read-back space. MSFS AXIS_* events are
        sign-inverted relative to the SimVar read-backs (see the trimwheel
        mixin's ``pos_y_pos = -int(pos_y_pos)``), hence the negation.
        ``_trim_sign`` is a runtime correction on top of that, flipped by
        :meth:`_advance_trim` for aircraft with inverted trim response
        (cf. the ``trimwheel_axis_invert`` user setting).
        """
        pct = clamp(pct * self._trim_sign, -1.0, 1.0)
        if self.ac._sim_is_msfs():
            self.ac._simconnect.send_event_to_msfs(
                "AXIS_ELEV_TRIM_SET", -int(pct * self.TRIM_AXIS_RANGE)
            )
        elif self.ac._sim_is_xplane():
            # TODO: verify dataref/range in-sim; X-Plane trim actuation is experimental.
            self.ac.write_xp_dataref("sim/cockpit2/controls/elevator_trim", round(pct, 5), type="float")

    # ---- Teardown ------------------------------------------------------------

    def _abort(self, reason):
        self.abort_reason = reason
        self.status_message = f"Aborted: {reason}"
        logger.warning(f"Trim calibration aborted: {reason}")
        self._finish(CalState.ABORT)

    def _finish(self, end_state):
        """Restore trim, release axis control, and hand back to normal flight
        controls (which take over the axis/spring on the next frame)."""
        try:
            # Only touch trim if we actually moved it (i.e. the sweep began);
            # otherwise trim0 is still its default and untouched by us.
            if self._sweep_targets:
                self._set_trim(self._trim0)
        except Exception as e:
            logger.debug(f"trim restore skipped: {e}")
        self.state = end_state
        self._active = False
