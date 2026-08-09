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
import math
import os
import time

import telemffb.globals as G
from telemffb.utils import PID, clamp, piecewise_linear

logger = logging.getLogger(__name__)

MS_TO_FPM = 196.85


class CalState(enum.Enum):
    IDLE = "idle"
    PROBE = "probe"          # determine control polarity (elevator, then aileron)
    STABILIZE = "stabilize"  # close the leveling loops and wait for steady flight
    TRIM_NEUTRAL = "trim_neutral"  # slew trim to the natural (zero-axis) point
    ASSIST_HOLD = "assist_hold"    # trim assistant: hold level while the user picks a speed
    SPEED_SETTLE = "speed_settle"  # optional hold to let airspeed stabilize
    SWEEP = "sweep"          # ramp the sim trim and record elevator axis at each station
    SOLVE = "solve"          # fit the samples and compute virtual_y
    RESTORE = "restore"      # ramp trim back to the natural point while leveling
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
    #
    # LEGACY pitch loop (VS-only PID, used when the probe cannot determine the
    # pitch-response polarity). A VS-only loop is structurally weak against
    # the phugoid — VS is zero at the top and bottom of the cycle — and sits
    # near the stability margin on some sims (observed as a mild sustained
    # limit cycle on X-Plane). PITCH_KD acts on d(VS)/dt and is the main
    # damping term.
    PITCH_KP = 0.06
    PITCH_KI = 0.03
    PITCH_KD = 0.06

    # CASCADE pitch loop (preferred): an inner PID holds pitch ATTITUDE
    # (short-period dynamics — fast, naturally damped, so the phugoid cannot
    # limit-cycle) while the outer loop nudges the attitude target from the
    # VS error and slowly adapts the attitude reference to remove any steady
    # VS residual. Inner gains are in DEGREE space.
    CASC_INNER_KP = 0.06         # u per degree of attitude error
    CASC_INNER_KI = 0.05
    CASC_INNER_KD = 0.02         # per deg/s (LPF'd)
    CASC_VS_TO_PITCH = 1.5       # outer P: degrees of target per m/s of VS error
    CASC_MAX_TARGET_DEG = 5.0    # cap on the outer P correction
    CASC_REF_ADAPT = 0.3         # outer I: reference deg/s per m/s of residual VS
    CASC_REF_LIMIT_DEG = 10.0    # reference excursion bound from engage attitude

    ROLL_KP = 0.020
    ROLL_KD = 0.010
    ROLL_KI = 0.004
    VS_TARGET = 0.0               # m/s; DEFAULT held vertical speed (level).
                                  # The run holds self.vs_target (this by
                                  # default); a glider run sets it to the
                                  # desired sink so an engineless aircraft can
                                  # be calibrated in steady descent. Only the
                                  # calibration-time control/gates use it — the
                                  # stored curve is the trim->stick geometry,
                                  # independent of the VS it was measured at.

    # Polarity probe.
    AUTO_DETECT_POLARITY = True
    PROBE_U = 0.08               # peak normalized probe command (before ramp/scale)
    PROBE_U_MIN = 0.03           # floor so a control-response-derated probe still responds
    PROBE_RAMP_S = 0.6           # ramp the probe in over this long instead of a step —
                                 # a step is broadband and rings the phugoid on
                                 # sensitive/pitchy aircraft, starting an oscillation
                                 # that was not there; a ramp excites far less.
    PROBE_TIME_S = 2.5           # per-axis probe window (upper bound; exits early on response)
    PROBE_BASELINE_S = 0.4       # no-input drift-measurement window before each probe
    PROBE_MIN_VS = 0.15          # m/s; min |dVS| to trust the elevator probe
    PROBE_MIN_ROLL = 1.0         # deg; min |dRoll| to trust the aileron probe
    PROBE_MIN_PITCH = 0.3        # deg; min |dPitch| to trust pitch-polarity detection
    PROBE_VS_EXIT = 0.6          # m/s; |dVS| at which the elevator probe ends early
    PROBE_ROLL_EXIT = 2.5        # deg; |dRoll| at which the aileron probe ends early
    ELEV_SIGN_DEFAULT = 1.0      # used if auto-detect is off or inconclusive
    AIL_SIGN_DEFAULT = 1.0

    # Oscillation watchdog. The pitch loop controls VS, which lags pitch by
    # ~90 deg in the phugoid cycle — on some aircraft the fixed gains sit past
    # the stability boundary and the loop pumps a slowly growing oscillation.
    # When successive VS peak amplitudes keep growing, the pitch gains are
    # backed off in place (integral term preserved) and the run continues;
    # below the floor the aircraft is declared unstabilizable and the run
    # aborts rather than diverging.
    OSC_PEAK_GROWTH = 1.10       # successive-amplitude ratio counted as growth
    OSC_PEAKS_TO_ACT = 3         # growing amplitudes in a row before backing off
    OSC_MIN_PEAK_VS = 0.35       # m/s; smaller amplitudes are jitter (resets streak)
    OSC_HYSTERESIS = 0.15        # m/s retreat from a candidate extremum to confirm it
    OSC_BACKOFF = 0.5            # pitch-gain multiplier applied per detection
    OSC_MIN_GAIN_SCALE = 0.1     # give up below this cumulative scale
    OSC_TRIM_RINGDOWN_S = 2.5    # VS extrema this soon after commanded trim motion
                                 # are the move's FORCED response, not oscillation
                                 # evidence — counting them backed the gains off
                                 # mid-sweep (field: SR22T station hops + the
                                 # side-switch traverse read as growing oscillation
                                 # -> x0.25 -> too weak to hold level -> abort)
    GAIN_SCALE_MAX = 1.5         # ceiling for the Increased control-response option:
                                 # some aircraft (long-period, lightly damped phugoid
                                 # at low speed) need MORE damping authority, not less
                                 # — derating made the P-38L porpoise WORSE; the
                                 # growth watchdog still derates from here if 1.5x
                                 # proves too hot on a given airframe

    # Stabilization gate (must hold within tolerance for the dwell window).
    STABLE_VS_TOL = 0.50         # m/s (~100 fpm)
    STABLE_ROLL_TOL = 3.0        # deg
    STABLE_DWELL_S = 2.0
    STABILIZE_TIMEOUT_S = 25.0

    # Trim neutralization: before the sweep, find the aircraft's NATURAL trim
    # point so the band lands in the same place every run, independent of
    # whatever trim-following settings were active before it. (The old
    # virtual gain changes how much axis leaks into the sim hands-off, which
    # moves the pre-run level-trim point — consecutive runs with different
    # settings were measuring different regions of a curved response.)
    # Method: the sims model a sprung self-centering stick, so neutral is
    # DELIVERED AXIS = 0. The fast elevator PID keeps leveling the aircraft
    # THROUGHOUT (nose stays put). Trim is moved in discrete STEPS toward the
    # trim that nulls the steady delivered elevator, letting the aircraft
    # settle between steps so the measured elevator is the true steady value.
    # Continuous servoing (velocity proportional to a lagged elevator signal)
    # is a double-integrator loop that oscillates — the field-observed hunting;
    # settle-between-steps decouples the timescales and cannot hunt.
    NEUT_U_TOL = 0.03            # |steady elevator| this small = natural point
    NEUT_PROBE = 0.06            # first trim step (secant needs two points to start)
    NEUT_STEP_MAX = 0.15         # cap a single trim step
    NEUT_SLOPE_MIN_DU = 0.02     # |du| below this is drift, not trim response —
                                 # too small to teach a slope
    NEUT_SETTLE_TIMEOUT_S = 12.0 # per-step ramp+settle allowance
    NEUT_DWELL_S = 1.0           # settle dwell before measuring the steady elevator
    NEUT_MAX_STEPS = 10
    NEUT_TIMEOUT_S = 60.0        # overall cap; proceed (with warning) if hit

    # Inert-trim detection (field case: Hot Start Challenger 650, X-Plane —
    # its plugin drives the real stabilizer itself, so the standard trim
    # variable accepts writes and reads back faithfully while having ZERO
    # aerodynamic effect; the assistant walked the trim rail-to-rail for 21
    # minutes with the held load frozen at +0.15). Two independent
    # detectors, both requiring wide provably-covered range:
    #   Neutralization — step cap reached with NO slope ever learned and a
    #   wide span traversed => abort honestly instead of declaring a fake
    #   natural point.
    #   Assist hold — repeated unverified rail reversals with the load
    #   unchanged across each full traverse => the whole range is proven
    #   dead; abort instead of ping-ponging forever.
    NEUT_INERT_SPAN = 0.5        # trim span walked with zero response
    ASSIST_INERT_RAILS = 2       # qualifying rail reversals to conclude
    ASSIST_INERT_DU = 0.05       # "unchanged" load across a full traverse

    # Hold at the natural trim point before a manual (non-assistant) sweep,
    # giving the airspeed time to stabilize at the current throttle/trim so
    # the sweep measures at (and drift-checks against) the equilibrium
    # speed. Always on: skipping it only ever traded 20 seconds for a
    # drift-skewed slope (assistant handoffs use their own short
    # ASSIST_CONFIRM_SETTLE_S hold instead — the assistant's stability gate
    # already did the settling).
    SPEED_SETTLE_S = 20.0

    # Trim assistant (started via start(assist=True)): after neutralization
    # the engine holds ASSIST_HOLD instead of sweeping — the leveling loops
    # stay closed and the neutralization's settle-measure-step cycle keeps
    # running, so the trim re-converges as the user changes power to pick a
    # test speed. Sweep readiness (``assist_stable``) is published with
    # asymmetric hysteresis: any disturbance clears it instantly; it sets
    # only after a sustained calm window.
    ASSIST_STABLE_HOLD_S = 3.0     # calm must persist this long before "stable"
    ASSIST_IAS_RATE_MAX = 0.15     # m/s per s (~0.3 kt/s); IAS still trending = not stable
    ASSIST_IAS_RATE_WINDOW_S = 2.0 # IAS rate measured across this window — a per-frame
                                   # derivative amplifies telemetry jitter ~30x and kept
                                   # resetting the calm window on a pegged airspeed
                                   # (field report: 10 s to re-enable at constant 75 kt)
    # Retrim measurement: ONE estimator — two trailing means of the elevator
    # load (SHORT and LONG) plus an agreement check. Agreement = the world
    # is steady and the fresh short mean is the truth (act at the neutral
    # tolerance); disagreement = something is swinging or winding, and only
    # the long mean is meaningful — its residual swing leak shrinks with the
    # window length by plain arithmetic (leak <= A*P/(pi*W)), so it acts at
    # a modestly higher threshold. No flight-state detection of any kind.
    # (This replaced three estimators — a fast plateau-gated path, a lagged
    # LPF, and a period-matched mean — whose trust gates twice field-froze
    # the retrim entirely with a large load parked on the elevator; and two
    # rewrite candidates, crossing-bounded and flatness-checked windows,
    # which the test plant defeated: short-window flatness IS the plateau
    # trap near a slow cycle's extremes, and crossing spacing lies when the
    # waveform is irregular.)
    ASSIST_RETRIM_S = 2.5          # s; move-attempt cadence
    ASSIST_MEAS_MIN_S = 2.0        # s; minimum data before any mean is trusted
    ASSIST_MEAS_SHORT_S = 6.0      # s; responsive window
    ASSIST_MEAS_LONG_S = 24.0      # s; authoritative window under disagreement
                                   # (window-length cycles leak exactly zero;
                                   # longer/heavier cycles leak under the
                                   # unsteady threshold up to ~P=30, A=0.3)
    ASSIST_SETTLE_GUARD_S = 0.5    # s; data taken this soon after a trim move's
                                   # arrival still reflects the transferring load
    ASSIST_U_AGREE = 0.04          # |short - long| within this = steady
    ASSIST_U_STEADY_RANGE = 0.05   # ...and the short window's content must be
                                   # quiet: mid-swing the two means sweep past
                                   # each other and momentarily agree, but the
                                   # short window is visibly in motion then
    ASSIST_U_MOVE_UNSTEADY = 0.06  # move threshold for the long mean
    ASSIST_MIN_SLOPE = 0.05        # |du/dtrim| below this -> blind probe fallback
    ASSIST_SEQ_MIN_MOVES = 2       # unrelieved moves before the run is judged whole
    ASSIST_SEQ_MIN_DTRIM = 0.10    # trim travel giving the run-secant a real baseline
    TRIM_CMD_CLAMP = 0.95          # commanded trim targets are clamped to +/- this
    ASSIST_PITCH_ANCHOR_SLEW = 0.2 # deg/s; the level attitude legitimately moves with
                                   # the chosen speed, so the pitch-guard anchor drifts
                                   # slowly with it (a fast runaway still trips the guard)
    ASSIST_CONFIRM_SETTLE_S = 5.0  # short pre-sweep confirmation hold from the assistant
                                   # (the speed is already user-stabilized; this guards
                                   # the race between the gate reading and the click)
    ASSIST_CANCEL_SETTLE_S = 0.5   # soft cancel from the hold: beat given to the firm
                                   # spring to walk the stick to the release-continuity
                                   # position before control is handed back

    # Throttle-movement tracking (ThrottlePct telemetry: 0-100 PERCENT per
    # lever, engines 1-4, unused slots pinned at 0): distinguishes an actual
    # power change from airspeed drifting on its own (a sim-ism seen in the
    # field) in the decision logs.
    THR_MOVE_TOL = 2.0             # percent; lever movement beyond this = power change
    THR_SETTLE_S = 1.5             # levers still this long = movement episode over

    # Trim sweep. The band is ADAPTIVE: starting from the center station the
    # sweep walks outward in SWEEP_STEP increments, expanding each side until
    # the elevator needed to hold level exceeds SWEEP_U_BUDGET (relative to
    # the center station), the half-width cap is hit, or the station cap is
    # reached. Strong-trim aircraft get a narrow band automatically; weak-trim
    # aircraft get a wide one — the fit sees comparable elevator excursion on
    # every aircraft, and nonlinearity over the usable range becomes visible.
    SWEEP_STEP = 0.06            # ElevTrimPct distance between stations (maximum)
    # The step ADAPTS to the measured trim-response slope so every aircraft
    # sees comparable elevator excursion per station — the same principle
    # that already sizes the band. 0.2 is the implicit excursion of the
    # field-validated runs (0.06 x slope ~3.3), so nothing with |slope| <=
    # 3.3 changes step at all; steep aircraft (Hawk: slope ~13, where one
    # 0.06 hop consumed the whole elevator budget) get proportionally finer
    # stations over their physically usable trim window.
    SWEEP_U_STEP_TARGET = 0.2    # aimed elevator excursion per station
    SWEEP_STEP_MIN = 0.015       # floor: stations must stay distinguishable
                                 # by the read-back (station tol scales down
                                 # with the step, floored by write precision)
    SWEEP_MAX_HALF = 0.45        # max band half-width each side of trim0
    SWEEP_MAX_STATIONS = 17      # hard cap on total stations
    SWEEP_U_BUDGET = 0.45        # stop expanding a side beyond this |u - u_center|
    STEP_SETTLE_TIMEOUT_S = 15.0
    STEP_DWELL_S = 1.5           # averaging window once settled at a step
    # A dwell is only accepted if the MEAN VS over the window is this small.
    # The instantaneous gate (STABLE_VS_TOL) is loose because real air jitters;
    # the mean gate rejects one-sided residuals from a still-converging PID,
    # which otherwise bias the recorded elevator in a trim-correlated way.
    SAMPLE_VS_MEAN_TOL = 0.15    # m/s (~30 fpm); clean-sample gate
    SAMPLE_VS_HARD_TOL = 0.3048  # m/s (60 fpm); worse than this at the station
                                 # deadline aborts — between the two, the best
                                 # dwell is accepted and flagged in the results
                                 # (a residual that holds steady is a finite-gain
                                 # chase of a drifting target, usually airspeed —
                                 # waiting longer cannot satisfy the clean gate)

    # Trim actuation. Commands are slew-rate limited — an instantaneous trim
    # step acts like yanking the stick and pitches the aircraft violently.
    TRIM_RATE_PER_S = 0.04       # command slew rate, full-range fraction per second
    TRIM_STATION_TOL = 0.02      # read-back must be this close to the station target
    # Command-direction verification: after this much commanded movement the
    # read-back must have moved the same way, else the command sign is flipped
    # (AXIS_ELEV_TRIM_SET polarity varies with aircraft, cf. trimwheel_axis_invert).
    TRIM_SIGN_CHECK_DIST = 0.03
    TRIM_RESPOND_MIN = 0.01      # read-back movement below this = "not responding"
    # Read-back closed loop: some aircraft report travel limits that differ
    # from their actual travel, so the direct pct->degrees mapping lands the
    # read-back proportionally short of the command (field: down-side stations
    # fell 10% short — under the 2% station tolerance near center, over it
    # deep in the band: "read-back never reached the station target (off by
    # 2.7%)" at trim -26%). Once the command reaches its target, any steady
    # residual is bled off with a slew-limited, bounded overdrive correction.
    TRIM_RB_DEADBAND = 0.005     # residual below this = arrived
    TRIM_OVERDRIVE_MAX = 0.20    # bound on the closed-loop correction

    # Elevator-authority guard: sustained command beyond this during the sweep
    # means the trim band exceeds what the elevator can hold level against.
    U_ELEV_SAT = 0.7
    U_ELEV_SAT_TIME_S = 0.7
    # Outbound-ramp freeze: a slope-blind first step on a steep aircraft can
    # lurch far past the designed per-station excursion before any sample has
    # taught the sweep its slope. Past this |u - u_center| while still ramping
    # OUTBOUND, stop and measure where we are instead of riding on toward the
    # saturation guard; the step then resizes from that sample.
    SWEEP_FREEZE_DU = 0.6
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
    # Pre-start VS gate: a probe begun in a real climb/descent measures the
    # phugoid instead of its own input and can coin-flip the pitch polarity
    # (field: back-to-back runs starting +1600 fpm after a previous abort).
    START_MAX_VS = 2.54          # m/s (~500 fpm)
    IAS_BAND_FRAC = 0.30         # abort if IAS drifts more than ±30% from start
    TELEM_TIMEOUT_S = 1.0        # no telemetry for this long -> abort

    HOLD_SPRING_COEFF = 1.0      # firm centered spring to keep hands-off stick put
    TRIM_AXIS_RANGE = 16383      # AXIS_ELEV_TRIM_SET: -16383..16384
    CPOFFSET_RANGE = 4096        # spring cpOffset units per full axis deflection
    HOLD_WALK_RATE = 3000        # cpOffset units/s for repositioning the parked
                                 # stick at handback — an instant center set under
                                 # the firm spring snaps the stick hard enough to
                                 # whack a hand resting near the controls
    HOLD_WALK_GRACE_S = 2.0      # release proceeds at most this long after the
                                 # phase would otherwise end, walk done or not

    # Trimwheel hold: a trimwheel instance writes its absolute wheel position
    # to the sim every frame, which fights our trim commands writer-vs-writer.
    # While the run is active we broadcast a self-expiring hold over IPC
    # (refreshed well inside the TTL so a crashed master un-mutes the wheel).
    TRIMWHEEL_HOLD_TTL_S = 3.0
    TRIMWHEEL_HOLD_TX_S = 1.0    # refresh interval

    # Diagnostic flight recorder (armed via the trace_enabled run option in
    # debug mode): one row per telemetry frame (commands, read-backs, loop
    # internals), dumped as a CSV next to the logs at the end of the run.
    # Cheap (tuples in a list) — and the only way to debug control-loop
    # behavior precisely instead of from descriptions of moving graphs.

    def __init__(self, aircraft):
        self.ac = aircraft
        self.state = CalState.IDLE
        self._active = False
        self.status_message = "Idle"
        self.progress = 0.0
        # Run option: starting pitch-gain scale (1.0 = normal). Lets the user
        # pre-derate the leveling loop for known-sensitive/pitchy aircraft
        # instead of waiting for the oscillation watchdog to discover it.
        self.initial_gain_scale = 1.0
        # Run option: MSFS trim write method — "direct" (default) writes the
        # ELEVATOR TRIM POSITION SimVar; "axis" sends AXIS_ELEV_TRIM_SET.
        # Selectable only via the dialog's debug-mode pulldown (see _set_trim
        # for why direct is primary).
        self.trim_write_method = "direct"
        # Run option: per-frame diagnostic CSV trace (debug-mode checkbox).
        self.trace_enabled = False
        # Run mode (start(assist=True)): stop after neutralization and hold
        # level indefinitely while the user picks a test speed with the
        # throttle; the sweep is then started explicitly via begin_sweep().
        self.assist_mode = False
        # Held vertical speed (m/s). 0 = level (powered aircraft); a glider
        # run sets a negative sink target (dialog spinbox) so an engineless
        # aircraft settles in steady descent instead of chasing an
        # unreachable level. Set by the dialog before every start; reset here
        # so any direct start() defaults to level.
        self.vs_target = self.VS_TARGET
        self.assist_stable = False  # published: calm/trimmed, safe to begin the sweep
        self.result = None          # dict on success, else None
        self.abort_reason = None
        # Set from the UI thread by stop(); consumed in the telemetry thread so
        # all sim I/O (trim restore, spring release) stays on one thread.
        self._stop_requested = False
        self._stop_reason = None
        self._sweep_requested = False  # begin_sweep() flag, consumed in update()

        # runtime state (initialized on start / per phase)
        self._pitch_pid = PID(self.PITCH_KP, self.PITCH_KI, self.PITCH_KD,
                              output_limits=(-1.0, 1.0), integral_limit=1.0,
                              derivative_lpf_hz=4.0)
        self._roll_pid = PID(self.ROLL_KP, self.ROLL_KI, self.ROLL_KD,
                             output_limits=(-1.0, 1.0), integral_limit=1.0,
                             derivative_lpf_hz=4.0)
        self._elev_sign = self.ELEV_SIGN_DEFAULT
        self._ail_sign = self.AIL_SIGN_DEFAULT
        # Pitch-response polarity (pitch_n = Pitch * _pitch_sign is nose-up
        # positive in the +VS sense). Detected by the elevator probe; persists
        # across runs like the other signs. MSFS reports pitch nose-up
        # NEGATIVE, X-Plane nose-up positive — this absorbs the difference.
        self._pitch_sign = 1.0
        self._pitch_sign_known = False
        self._u_elev = 0.0
        self._u_ail = 0.0

        self._reset_runtime()

    def _reset_runtime(self):
        self._last_t = None
        self._phase_start_t = None
        self._probe_stage = 0            # 0 = elevator, 1 = aileron
        self._probe_phase = "baseline"   # "baseline" -> "probe" -> "rampdown"
        self._probe_ref = None           # reference value at sub-phase start
        self._probe_drift = 0.0          # drift rate measured over the baseline window
        self._probe_peak_u = 0.0         # probe magnitude at ramp-down start
        self._probe_pitch_ref = None     # pitch anchor for pitch-polarity detection
        self._probe_pitch_drift = 0.0
        # pitch loop mode: None until engaged (after the probe knows the
        # pitch polarity); "cascade" or "legacy" afterwards
        self._pitch_mode = None
        self._pitch_base_gains = (self.PITCH_KP, self.PITCH_KI, self.PITCH_KD)
        self._pitch_ref_n = None         # cascade attitude reference (normalized)
        self._pitch_ref0 = None
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
        self._station_best = None        # best over-tolerance dwell at this station
        self._flagged = []               # samples accepted with a VS residual (see result)
        # trim command state (read-back space; _set_trim maps to command space)
        self._trim_cmd = 0.0
        self._trim_slew_t = None         # last commanded trim motion (watchdog gate)
        self._trim_overdrive = 0.0       # read-back closed-loop correction
        self._trim_deg_per_pct = {}      # measured pct->deg scale per side (from the
                                         # live ElevTrim/ElevTrimPct telemetry pair)
        self._trim_ratio_logged = False
        self._trim_sign = 1.0            # flipped at runtime if read-back moves opposite
        self._trim_dir_verified = False
        self._sign_cmd_accum = 0.0
        self._sign_rb_start = 0.0
        self._sign_flips = 0
        self._hold_tx_t = 0.0            # last trimwheel-hold broadcast time
        self._trim_method_logged = False # effective write method logged once per run
        # trace (diagnostic flight recorder) state
        self._trace_rows = []
        self._trace_t0 = None
        self._trace_broken = False
        self._trace_trim_write = None    # last raw value written to the sim's trim
        self._trace_axis_y = None        # last elevator axis value sent to the sim
        self._pitch_target_n = None      # cascade outer-loop demand (normalized deg)
        self._u_sat_since = None
        self._sat_recovering = False     # unwinding out of a truncated sweep side
        # trim-neutralization state (discrete step-and-settle root-find)
        self._trim_touched = False       # any trim commanded this run (restore flag)
        self._neut_target = 0.0          # trim target for the current step
        self._neut_dwell_since = None    # settle-dwell start (None = ramping)
        self._neut_u_samples = []        # steady-elevator samples for this step
        self._neut_prev = None           # previous settled (trim, u) for the secant
        self._neut_steps = 0
        self._neut_deadline = None       # per-step ramp+settle deadline
        self._neut_start_t = None        # overall phase start
        self._neut_u_final = 0.0         # steady elevator at the accepted point
        # trim-assistant state
        self.assist_stable = False
        self._assist_sweep_started = False   # begin_sweep() accepted; route onward
        self._assist_unverified_rails = 0    # inert-trim evidence counter
        self._cancel_deadline = None         # soft-cancel settle deadline (hold only)
        self._cancel_reason = None
        self._assist_stable_since = None     # calm-window start (hysteresis)
        self._assist_u_mean = None           # current mean load (or None)
        self._assist_mean_steady = False     # short/long means agreed
        self._assist_agree_since = None      # agreement-persistence start
        self._assist_settle_t = None         # time the trim arrived at its target
        self._assist_move_t = 0.0            # last move-attempt time (cadence)
        self._assist_dir = 1                 # verified relief direction (trim sign
                                             # that relieves a POSITIVE load)
        self._assist_sized = False           # slope magnitude trusted for Newton sizing
        self._assist_premove = None          # (u_mean, step) of the move being verified
        self._assist_worsen = 0              # consecutive moves that made the load worse
        self._assist_dir_proven = False      # a relieved verdict has confirmed _assist_dir
        self._assist_seq0 = None             # (trim, load) at the start of an unrelieved
                                             # run of moves (sequence-secant baseline)
        self._assist_seq_n = 0               # verdict-eligible moves in that run
        self._assist_railed = False          # proven direction but trim pinned at its
                                             # limit: out of trim authority
        self._assist_gate_log_t = None       # ready-gate log flap guard
        # throttle-movement tracking (decision-log context)
        self._thr_ref = None                 # settled lever position reference (pct)
        self._thr_last = None                # previous frame's lever position (pct)
        self._thr_moving = False
        self._thr_move_from = None
        self._thr_last_move_t = None         # last time the levers were seen moving
        self._thr_active = None              # learned per-slot real-engine mask
        self._assist_ias_hist = []           # (t, IAS) samples for the windowed rate
        self._trim_slope_est = None          # measured du/dtrim; sizes the assistant's
                                             # Newton retrim steps (seeded by neutralization)
        self._settle_start_t = None      # SPEED_SETTLE phase start
        self._restore_release_t = None   # RESTORE trim-ramp-complete time (the
                                         # stick walk gets a bounded grace after it)
        self._sweep_step = self.SWEEP_STEP        # slope-adapted at sweep entry
        self._station_tol = self.TRIM_STATION_TOL # scales with the step
        self._settle_s = self.SPEED_SETTLE_S # duration of the current settle hold
        # oscillation watchdog state (extremum tracking with hysteresis)
        self._osc_ext = None         # candidate extremum value
        self._osc_dir = 0            # 0 = undetermined, +1 rising, -1 falling
        self._osc_prev_ext = None    # last confirmed extremum
        self._osc_amps = []          # recent half-cycle amplitudes (peak-to-peak / 2)
        self._assist_u_hist = []     # (t, u) samples for the windowed mean load
        self._gain_scale_request = None  # live control-response change (set_gain_scale)
        self._set_pitch_gain_scale(clamp(self.initial_gain_scale,
                                         self.OSC_MIN_GAIN_SCALE, self.GAIN_SCALE_MAX))
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
        if telem_data.get("SimPaused"):
            return False, "donUnpause the simulator to calibrate"
        if telem_data.get("Slew"):
            return False, "Exit slew mode to calibrate"
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
        if abs(self._vs_err(telem_data.get("VerticalSpeed", 0))) > self.START_MAX_VS:
            return False, ("Settle onto the target sink first"
                           if self.vs_target else
                           "Level off first — still climbing/descending")
        return True, "Ready to calibrate"

    def _vs_err(self, vs):
        """Vertical-speed deviation from the run's held flight condition.

        Zero for a powered level-flight run; for a glider run it is measured
        against ``self.vs_target`` (the chosen sink), so every 'settled /
        level' GATE below reads the same whether the aircraft is holding
        level or holding a steady descent. The pitch loop already drives
        toward ``vs_target``; this keeps the gates consistent with it.
        """
        return (vs or 0.0) - self.vs_target

    def start(self, assist=False):
        """Arm the engine. Baseline is captured on the first :meth:`update`.

        With ``assist=True`` the engine runs the same probe / stabilize /
        neutralize sequence but then holds level (ASSIST_HOLD) instead of
        sweeping, re-trimming as the user changes power to pick a test speed;
        :meth:`begin_sweep` starts the actual calibration from there.

        ``_active`` is set last so the telemetry thread only ever observes a
        fully-initialized engine.
        """
        self.result = None
        self.abort_reason = None
        self.progress = 0.0
        self._stop_requested = False
        self._stop_reason = None
        self._sweep_requested = False
        self.assist_mode = bool(assist)
        self._reset_runtime()
        # NOTE: _elev_sign/_ail_sign deliberately persist across runs. Control
        # polarity is a property of the aircraft, and re-runs are safer using
        # the last detected values (the probe re-detects them every run
        # regardless; resetting to defaults threw that knowledge away).
        self.status_message = "Starting…"
        self._set_state(CalState.PROBE if self.AUTO_DETECT_POLARITY
                        else CalState.STABILIZE, "run started")
        self._active = True
        # Field-diagnosis context: the held VS target was invisible in the
        # 2026-07-23 glider logs and had to be inferred. One line, per run.
        hold_txt = (f", holding {abs(self.vs_target) * MS_TO_FPM:.0f} fpm "
                    + ("descent" if self.vs_target < 0 else "climb")
                    if self.vs_target else ", holding level")
        scale_txt = (f", control response x{self.initial_gain_scale:.2f}"
                     if self.initial_gain_scale != 1.0 else "")
        logger.info("Trim calibration started"
                    + (" (trim-assistant mode)" if self.assist_mode else "")
                    + hold_txt + scale_txt)

    def begin_sweep(self):
        """Leave the trim assistant's hold and start the calibration sweep
        (thread-safe: flagged here, performed on the next :meth:`update` in
        the telemetry thread). Only meaningful while holding in ASSIST_HOLD."""
        if self._active and self.state == CalState.ASSIST_HOLD:
            self._sweep_requested = True

    def set_gain_scale(self, scale):
        """Live control-response change on a running loop (thread-safe).

        Applied on the next :meth:`update` via the same in-place mechanism
        the oscillation watchdog uses (integral term preserved — no output
        step). Deferred while the polarity probe runs so its input amplitude
        is not stepped mid-measurement; ignored from the sweep onward (the
        dialog locks the control there — a run's measurement dynamics stay
        consistent). Sets the scale absolutely: the watchdog can still back
        off further from the new value if oscillation returns.
        """
        self._gain_scale_request = clamp(scale, self.OSC_MIN_GAIN_SCALE,
                                         self.GAIN_SCALE_MAX)

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
            try:
                self._set_trimwheel_hold(False)
            except Exception:
                pass  # the hold self-expires within the TTL anyway
            self._dump_trace()

    # ---- Per-frame driver (called from the telemetry thread) ----------------

    def update(self, telem_data):
        """Advance the state machine one telemetry frame."""
        if self._stop_requested:
            self._stop_requested = False
            if self.state == CalState.ASSIST_HOLD and self._cancel_deadline is None:
                # Soft stop from the hold: park the stick at the release-
                # continuity position first — it is sim-inert while the
                # engine owns the axes — and give the firm spring a beat to
                # walk it there; _do_assist_hold completes the abort at the
                # deadline. (A second stop request, and every hard safety
                # trip via _precheck, still aborts immediately.)
                self._cancel_reason = self._stop_reason or "Cancelled by user"
                self._cancel_deadline = time.perf_counter() + self.ASSIST_CANCEL_SETTLE_S
            else:
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

        if self._gain_scale_request is not None and self.state != CalState.PROBE:
            scale, self._gain_scale_request = self._gain_scale_request, None
            if self.state in (CalState.STABILIZE, CalState.TRIM_NEUTRAL,
                              CalState.ASSIST_HOLD, CalState.SPEED_SETTLE):
                logger.info(f"Control response changed live: pitch gains x{scale:.2f}")
                self._set_pitch_gain_scale(scale)

        if now - self._hold_tx_t >= self.TRIMWHEEL_HOLD_TX_S:
            self._hold_tx_t = now
            self._set_trimwheel_hold(True)

        self._track_throttle(telem_data, now)

        if not self._u_base_captured:
            self._capture_takeover_baseline(telem_data)

        if self.state == CalState.PROBE:
            self._do_probe(telem_data, dt)
        elif self.state == CalState.STABILIZE:
            self._do_stabilize(telem_data, dt)
        elif self.state == CalState.TRIM_NEUTRAL:
            self._do_trim_neutral(telem_data, dt)
        elif self.state == CalState.ASSIST_HOLD:
            self._do_assist_hold(telem_data, dt)
        elif self.state == CalState.SPEED_SETTLE:
            self._do_speed_settle(telem_data, dt)
        elif self.state == CalState.SWEEP:
            self._do_sweep(telem_data, dt)
        elif self.state == CalState.RESTORE:
            self._do_restore(telem_data, dt)
        # SOLVE/DONE/ABORT are terminal and handled inline when reached.

        if self._active:
            self._trace_frame(telem_data, now)

    def _set_state(self, new, why=""):
        """State transition with a decision log — the field-diagnosis
        backbone: every run tells its own story in a dozen lines."""
        if new is self.state:
            return
        logger.info(f"Calibration state: {self.state.name} -> {new.name}"
                    + (f" ({why})" if why else ""))
        self.state = new

    def _track_throttle(self, telem_data, now):
        """Track lever movement (ThrottlePct, mean of engines 1-4) so the
        logs can distinguish a real power change from airspeed drifting on
        its own. Two lines per adjustment: movement start, then settle."""
        thr = telem_data.get("ThrottlePct")
        if isinstance(thr, (list, tuple)):
            vals = [v for v in thr if v is not None]
            if not vals:
                return
            # Learn which lever slots belong to REAL engines: a twin reports
            # four slots with the unused pair pinned at 0, and averaging
            # those in halves the value and dilutes movement detection. A
            # slot that has ever been above idle is an engine — the mask
            # persists, so pulling everything to idle still reads correctly.
            if self._thr_active is None or len(self._thr_active) != len(vals):
                self._thr_active = [False] * len(vals)
            for i, v in enumerate(vals):
                if v > 0.5:
                    self._thr_active[i] = True
            act = [v for i, v in enumerate(vals) if self._thr_active[i]]
            thr = sum(act) / len(act) if act else sum(vals) / len(vals)
        if thr is None:
            return
        if self._thr_ref is None:
            self._thr_ref = self._thr_last = thr
            return
        if abs(thr - self._thr_last) > 0.25:
            self._thr_last_move_t = now
        self._thr_last = thr
        if not self._thr_moving:
            if abs(thr - self._thr_ref) > self.THR_MOVE_TOL:
                self._thr_moving = True
                self._thr_move_from = self._thr_ref
                self._thr_last_move_t = now
                logger.info(f"Throttle movement detected: "
                            f"{self._thr_ref:.0f}% -> ...")
        elif self._thr_last_move_t is not None and \
                now - self._thr_last_move_t >= self.THR_SETTLE_S:
            self._thr_moving = False
            logger.info(f"Throttle settled at {thr:.0f}% "
                        f"(was {self._thr_move_from:.0f}%)")
            self._thr_ref = thr

    def _thr_context(self, now):
        """Log suffix answering: did the user actually change power?"""
        if self._thr_ref is None:
            return ""
        if self._thr_last_move_t is None:
            return " [throttle untouched - airspeed moving on its own]"
        return f" [throttle last moved {now - self._thr_last_move_t:.0f}s ago]"

    # ---- Diagnostic trace (per-run flight recorder) ---------------------------

    TRACE_COLUMNS = ("t,state,detail,vs_ms,pitch_deg,roll_deg,ias_ms,"
                     "trim_pct,trim_deg,trim_cmd,trim_target,trim_write,"
                     "u_base_y,u_elev,u_ail,axis_y_sent,elev_pos,elev_defl_pct,"
                     "pid_out,pid_i_term,"
                     "pitch_target_n,gain_scale,pitch_mode,trim_sign,dir_verified,"
                     "trim_overdrive")

    def _log_trim_method(self, desc):
        """Log the effective MSFS write method once per run (diagnostics)."""
        if not self._trim_method_logged:
            self._trim_method_logged = True
            logger.info(f"Trim writes: {desc}")

    def _trace_frame(self, telem_data, now):
        """Record one row of everything the engine commanded and observed."""
        if not self.trace_enabled or self._trace_broken:
            return
        try:
            if self._trace_t0 is None:
                self._trace_t0 = now
            if self.state == CalState.PROBE:
                detail, target = f"stage{self._probe_stage}-{self._probe_phase}", None
            elif self.state == CalState.TRIM_NEUTRAL:
                detail, target = f"step{self._neut_steps + 1}", self._neut_target
            elif self.state == CalState.ASSIST_HOLD:
                detail, target = "assist", self._neut_target
            elif self.state == CalState.SWEEP:
                detail, target = f"station{len(self._samples) + 1}", self._current_target
            elif self.state == CalState.RESTORE:
                detail, target = "", self._trim0
            else:
                detail, target = "", None
            pid = self._pitch_pid
            self._trace_rows.append((
                now - self._trace_t0, self.state.name, detail,
                telem_data.VerticalSpeed, telem_data.Pitch, telem_data.Roll,
                telem_data.IAS, telem_data.ElevTrimPct, telem_data.ElevTrim,
                self._trim_cmd, target, self._trace_trim_write,
                self._u_base_y, self._u_elev, self._u_ail, self._trace_axis_y,
                # input-side position vs the commanded axis: separates JF-style
                # event mangling from genuine aero response (Hawk probe
                # reversal), and directly measures C208B-class input
                # attenuation; surface position exposes speed-scaled response
                telem_data.ElevPos, telem_data.ElevDeflPct,
                pid.output, pid.ki * pid._integral, self._pitch_target_n,
                self._gain_scale, self._pitch_mode, self._trim_sign,
                int(self._trim_dir_verified), self._trim_overdrive,
            ))
        except Exception:
            # Telemetry hot path: diagnostics must never break the run.
            self._trace_broken = True
            logger.exception("trace disabled for this run (frame capture failed)")

    def _dump_trace(self):
        rows, self._trace_rows = self._trace_rows, []
        if not self.trace_enabled or not rows:
            return
        try:
            folder = os.path.join(os.environ.get("LOCALAPPDATA", "."),
                                  "VPForce-TelemFFB", "log")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, time.strftime("trimcal_trace_%Y%m%d_%H%M%S.csv"))

            def fmt(v):
                if v is None:
                    return ""
                if isinstance(v, float):
                    return f"{v:.5f}"
                return str(v)

            with open(path, "w", encoding="utf-8") as f:
                f.write(self.TRACE_COLUMNS + "\n")
                for r in rows:
                    f.write(",".join(fmt(v) for v in r) + "\n")
            logger.info(f"Trim calibration trace: {len(rows)} frames -> {path}")
        except Exception:
            logger.exception("trace dump failed")

    # ---- Safety --------------------------------------------------------------

    def _ap_engaged(self, telem_data):
        if self.ac._sim_is_msfs():
            return bool(telem_data.get("APMaster", 0))
        if self.ac._sim_is_xplane():
            return bool(telem_data.get("APServos", 0))
        return False

    def _precheck(self, telem_data):
        """Common per-frame safety checks; abort and return False on breach."""
        if telem_data.get("SimPaused"):
            # Frames keep arriving while paused but the aircraft is frozen; the
            # wall-clock settle timers would false-settle on the frozen VS and
            # corrupt the measurement. Bail cleanly — re-run after unpausing.
            self._abort("Simulator paused")
            return False
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
                # Mirror the runtime's actual offset math (calibrated curve
                # or legacy static gain) exactly like _release_hold_targets
                # does — a hand-rolled static-only formula here computed a
                # false baseline in curve mode, and the takeover step it
                # injected upset the aircraft and contaminated the polarity
                # probe (C208B field abort, trace 20260719_201957).
                elev_trim = clamp(t * p_y, -1, 1)
                offs_fn = getattr(self.ac, "_trim_follow_virtual_offset_y", None)
                offs_y = offs_fn(t, elev_trim, telem_data) if offs_fn is not None \
                    else elev_trim * (1 - v_y)
                self._u_base_y = clamp(phys_y - offs_y, -1, 1)
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
            self._probe_pitch_ref = telem_data.Pitch or 0.0
            self.status_message = ("Probing elevator response…" if self._probe_stage == 0
                                   else "Probing aileron response…")

        elapsed = now - self._phase_start_t
        probe_on = self._probe_phase == "probe"

        # Ramped, gentled probe command: ease the input in over PROBE_RAMP_S
        # (no step) and scale by the control-response setting so sensitive
        # aircraft get a smaller nudge. Enough to read polarity, not enough to
        # start an oscillation. The ramp-DOWN is symmetric — releasing the
        # input abruptly is as broadband a disturbance as an abrupt onset.
        if probe_on:
            mag = max(self.PROBE_U * self._gain_scale, self.PROBE_U_MIN)
            u_probe = mag * min(elapsed / self.PROBE_RAMP_S, 1.0)
        elif self._probe_phase == "rampdown":
            u_probe = self._probe_peak_u * max(1.0 - elapsed / self.PROBE_RAMP_S, 0.0)
        else:
            u_probe = 0.0

        if self._probe_stage == 0:
            # Elevator probe. The aileron gets NO closed-loop help here: its
            # polarity is unverified at this point, and a wrong-sign
            # wings-level term is positive feedback — seen in the field as a
            # hard roll at start when beginning with residual bank.
            if self._probe_phase == "rampdown":
                # Blend the held elevator into the leveling loop as we release:
                # recovery starts now (not one stage later) and the handoff is
                # stepless. Pitch polarity was detected just before ramp-down.
                r = min(elapsed / self.PROBE_RAMP_S, 1.0)
                level_u = self._run_pitch_loop(telem_data, dt)
                self._u_elev = (1.0 - r) * (self._u_base_y + self._probe_peak_u) + r * level_u
            else:
                self._u_elev = self._u_base_y + u_probe
            self._u_ail = self._u_base_x
            exit_threshold = self.PROBE_VS_EXIT
        else:
            # Aileron probe; hold pitch with the full leveling loop using the
            # polarities detected in stage 0 (a weak hold lets the probe's
            # climb build into a zoom that trips the attitude guard).
            self._u_elev = self._run_pitch_loop(telem_data, dt)
            self._u_ail = self._u_base_x + u_probe
            exit_threshold = self.PROBE_ROLL_EXIT

        self._command_axes(telem_data)

        value = self._probe_value(telem_data)

        if self._probe_phase == "baseline":
            if elapsed >= self.PROBE_BASELINE_S:
                self._probe_drift = (value - self._probe_ref) / max(elapsed, 1e-6)
                p_now = telem_data.Pitch or 0.0
                self._probe_pitch_drift = (p_now - self._probe_pitch_ref) / max(elapsed, 1e-6)
                self._probe_pitch_ref = p_now
                self._probe_phase = "probe"
                self._phase_start_t = now
                self._probe_ref = value
            return

        if self._probe_phase == "rampdown":
            # Polarity is already recorded; just ease the input out, then
            # advance to the next stage (or stabilize).
            if elapsed >= self.PROBE_RAMP_S:
                self._advance_probe_stage()
            return

        # Drift-compensated response to the probe input.
        delta = (value - self._probe_ref) - self._probe_drift * elapsed

        # End the probe as soon as the response is unambiguous — holding the
        # input for the full window just pumps energy into the aircraft. The
        # early exit is only trusted once the ramp has fully applied the
        # input: a threshold crossing in the first fraction of a second is
        # residual aircraft motion (accelerating phugoid the linear drift
        # model cannot capture), not probe response — and at a random phugoid
        # phase the dPitch/dVS agreement is a coin flip, so a wrong-sign
        # cascade then rails the elevator (seen in the field as full surface
        # deflection during PROBE and a +/-15 deg attitude-guard abort).
        if elapsed >= self.PROBE_TIME_S or \
                (elapsed >= self.PROBE_RAMP_S and abs(delta) >= exit_threshold):
            if self._probe_stage == 0:
                if abs(delta) >= self.PROBE_MIN_VS:
                    self._elev_sign = 1.0 if delta > 0 else -1.0
                    logger.info(f"Elevator polarity detected: {self._elev_sign:+.0f} (dVS={delta:+.2f} m/s in {elapsed:.2f}s)")
                    # Pitch-response polarity rides on the same probe: a climb
                    # (dVS) and its attitude change (dPitch) must agree for
                    # pitch_n = Pitch * sign to be nose-up positive.
                    d_pitch = ((telem_data.Pitch or 0.0) - self._probe_pitch_ref
                               - self._probe_pitch_drift * elapsed)
                    if abs(d_pitch) >= self.PROBE_MIN_PITCH:
                        self._pitch_sign = 1.0 if (d_pitch > 0) == (delta > 0) else -1.0
                        self._pitch_sign_known = True
                        logger.info(f"Pitch polarity detected: {self._pitch_sign:+.0f} "
                                    f"(dPitch={d_pitch:+.2f} deg)")
                    elif not self._pitch_sign_known:
                        logger.warning(f"Pitch response inconclusive (dPitch={d_pitch:+.2f} deg); "
                                       "using legacy VS leveling loop")
                else:
                    logger.warning(f"Elevator probe inconclusive (dVS={delta:+.2f} m/s); "
                                   f"keeping sign {self._elev_sign:+.0f}")
            else:
                if abs(delta) >= self.PROBE_MIN_ROLL:
                    self._ail_sign = 1.0 if delta > 0 else -1.0
                    logger.info(f"Aileron polarity detected: {self._ail_sign:+.0f} (dRoll={delta:+.2f} deg in {elapsed:.2f}s)")
                else:
                    logger.warning(f"Aileron probe inconclusive (dRoll={delta:+.2f} deg); "
                                   f"keeping sign {self._ail_sign:+.0f}")
            # Polarity recorded; ease the input back out before advancing.
            self._probe_peak_u = u_probe
            self._probe_phase = "rampdown"
            self._phase_start_t = now

    def _advance_probe_stage(self):
        """After a probe's ramp-down, move to the aileron probe or stabilize.

        The pitch loop was engaged during the elevator ramp-down and stays
        engaged (its integral already holds recovery progress); resetting it
        here would discard that and re-step the elevator.
        """
        if self._probe_stage == 0:
            self._probe_stage = 1
            self._probe_phase = "baseline"
            self._phase_start_t = None
            self._probe_ref = None
            self._probe_drift = 0.0
        else:
            self._enter_stabilize()

    # ---- Phase: stabilize ----------------------------------------------------

    def _enter_stabilize(self):
        self._set_state(CalState.STABILIZE, "polarity probes complete")
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

        # Live gate progress so the dialog shows what we're waiting on. For a
        # glider run the gate is on the deviation from the target sink, so the
        # target is shown alongside the raw VS.
        gains_txt = f", gains x{self._gain_scale:.2f}" if self._gain_scale < 1.0 else ""
        target_txt = (f" (target {self.vs_target * MS_TO_FPM:+.0f})"
                      if self.vs_target else "")
        self.status_message = (
            f"Stabilizing... VS {vs * MS_TO_FPM:+.0f} fpm{target_txt} "
            f"(need within {self.STABLE_VS_TOL * MS_TO_FPM:.0f}), "
            f"roll {roll:+.1f} deg (need within {self.STABLE_ROLL_TOL:.0f}){gains_txt}"
        )

        if abs(self._vs_err(vs)) <= self.STABLE_VS_TOL and abs(roll) <= self.STABLE_ROLL_TOL:
            if self._stable_since is None:
                self._stable_since = now
            elif now - self._stable_since >= self.STABLE_DWELL_S:
                self._enter_trim_neutral(telem_data)
                return
        else:
            self._stable_since = None

        if now - self._phase_start_t > self.STABILIZE_TIMEOUT_S:
            # A slow phugoid may not complete OSC_PEAKS_TO_ACT half-cycles
            # inside the window — two growing amplitudes at timeout is enough
            # evidence of divergence to back off and retry instead of aborting.
            if len(self._osc_amps) >= 2 and \
                    self._osc_amps[-1] > self._osc_amps[-2] * self.OSC_PEAK_GROWTH:
                logger.warning("Stabilize timeout with a growing oscillation pattern; "
                               "treating as divergence")
                self._backoff_pitch_gains()
                return
            self._abort(
                f"Could not stabilize within {self.STABILIZE_TIMEOUT_S:.0f}s "
                f"(VS {vs * 196.85:+.0f} fpm, roll {roll:+.1f} deg)"
            )

    # ---- Phase: trim neutralization -------------------------------------------

    def _enter_trim_neutral(self, telem_data):
        self._set_state(CalState.TRIM_NEUTRAL, "flight stable")
        # Restore point for an abort during this phase: the user's original
        # trim. _enter_sweep overwrites it with the natural point, which is
        # then the (better) restore target for the rest of the run.
        self._trim0 = telem_data.ElevTrimPct
        self._trim_cmd = telem_data.ElevTrimPct
        self._sign_rb_start = telem_data.ElevTrimPct
        self._sign_cmd_accum = 0.0
        self._trim_touched = True
        self._neut_steps = 0
        self._neut_prev = None
        self._neut_start_t = time.perf_counter()
        self._neut_u_final = self._u_elev
        # Inert-trim evidence across the phase: widest span visited and
        # whether any step ever taught a slope (du above the noise floor).
        self._neut_slope_learned = False
        t_start = telem_data.ElevTrimPct or 0.0
        self._neut_t_lo = t_start
        self._neut_t_hi = t_start
        # First "step" is the current trim: measure u here, then probe.
        self._begin_neut_step(telem_data.ElevTrimPct or 0.0)
        self.status_message = "Centering the elevator axis…"

    def _begin_neut_step(self, target):
        self._neut_target = clamp(target, -self.TRIM_CMD_CLAMP, self.TRIM_CMD_CLAMP)
        self._neut_dwell_since = None
        self._neut_u_samples = []
        self._neut_deadline = time.perf_counter() + self.NEUT_SETTLE_TIMEOUT_S

    def _do_trim_neutral(self, telem_data, dt):
        """Find the natural trim point by a settled root-find on the elevator.

        The sims model a sprung self-centering stick, so the natural trim
        point is where the DELIVERED elevator axis is zero. The fast elevator
        PID keeps leveling throughout (the nose stays put) while trim is moved
        in discrete steps: ramp to the step target, let VS settle, measure the
        true steady elevator, then step trim toward nulling it. Settling
        between steps means the measurement has no lag, so — unlike a
        continuous servo against the lagged elevator — it cannot hunt.
        """
        # Elevator keeps leveling (fast — nose stays put); roll stays closed.
        self._u_elev = self._run_pitch_loop(telem_data, dt)
        roll_err = self._ail_sign * (0.0 - telem_data.Roll)
        self._u_ail = self._u_base_x + self._roll_pid.update(roll_err, dt)

        ramping = self._advance_trim(self._neut_target, telem_data, dt)
        if ramping is None:
            return  # aborted (unresponsive trim)
        self._command_axes(telem_data)

        now = time.perf_counter()
        at_target = (not ramping and
                     abs((telem_data.ElevTrimPct or 0) - self._neut_target) <= self.TRIM_STATION_TOL)
        settled = at_target and abs(self._vs_err(telem_data.VerticalSpeed)) <= self.STABLE_VS_TOL \
            and abs(telem_data.Roll) <= self.STABLE_ROLL_TOL

        self.status_message = (
            f"Centering elevator axis… step {self._neut_steps + 1}, "
            f"trim {100 * (telem_data.ElevTrimPct or 0):+.0f}%, "
            + ("measuring" if settled else "settling"))

        if settled:
            if self._neut_dwell_since is None:
                self._neut_dwell_since = now
            self._neut_u_samples.append(self._u_elev)
            if now - self._neut_dwell_since >= self.NEUT_DWELL_S:
                t = telem_data.ElevTrimPct or self._neut_target
                u = sum(self._neut_u_samples) / len(self._neut_u_samples)
                self._neut_u_final = u
                self._neut_t_lo = min(self._neut_t_lo, t)
                self._neut_t_hi = max(self._neut_t_hi, t)
                if abs(u) <= self.NEUT_U_TOL or self._neut_steps >= self.NEUT_MAX_STEPS:
                    span = self._neut_t_hi - self._neut_t_lo
                    if abs(u) > self.NEUT_U_TOL and not self._neut_slope_learned \
                            and span >= self.NEUT_INERT_SPAN:
                        # Every step measured zero response across a wide
                        # range: the trim variable is disconnected from the
                        # airframe. Say so instead of declaring a fake
                        # natural point and ping-ponging in the hold.
                        self._abort(self._inert_trim_reason(
                            self._neut_t_lo, self._neut_t_hi))
                        return
                    logger.info(f"Natural trim point found at {t:+.3f} "
                                f"(steady elevator {u:+.3f}, {self._neut_steps} steps)")
                    self._finish_neutral(telem_data, centered=abs(u) <= self.NEUT_U_TOL)
                    return
                # Secant root-find toward u = 0. Using the measured local slope
                # handles either trim polarity (a wrong initial guess self-
                # corrects on the next step); the first step is a fixed probe.
                slope_note = ""
                if self._neut_prev is not None and abs(u - self._neut_prev[1]) > 1e-6:
                    t0, u0 = self._neut_prev
                    step = clamp(-u * (t - t0) / (u - u0),
                                 -self.NEUT_STEP_MAX, self.NEUT_STEP_MAX)
                    if abs(t - t0) > 0.005:
                        if abs(u - u0) >= self.NEUT_SLOPE_MIN_DU:
                            # Local trim-response slope; the trim assistant
                            # sizes (and SIGNS) its retrim moves with this. A
                            # du under the floor is drift, not response — a
                            # mild power or speed change moves the load that
                            # much by itself. Field incident (RV-10): a +0.004
                            # du measured during a deceleration exported slope
                            # +0.067, seeding the assistant's relief direction
                            # backwards.
                            self._trim_slope_est = (u - u0) / (t - t0)
                            self._neut_slope_learned = True
                        else:
                            slope_note = (f" (du {u - u0:+.3f} below noise "
                                          "floor; slope not learned)")
                else:
                    step = math.copysign(self.NEUT_PROBE, u)  # assume normal polarity
                logger.info(f"Neutralization step {self._neut_steps + 1}: settled at "
                            f"trim {t:+.3f} with steady elevator {u:+.3f}; "
                            f"stepping {step:+.3f}{slope_note}")
                self._neut_prev = (t, u)
                self._neut_steps += 1
                self._begin_neut_step(t + step)
        elif now > self._neut_deadline:
            self._neutral_give_up(telem_data, "Trim neutralization step could not settle")
            return

        if now - self._neut_start_t > self.NEUT_TIMEOUT_S:
            self._neutral_give_up(telem_data, "Trim neutralization timed out")

    def _inert_trim_reason(self, t_lo, t_hi):
        return (
            f"Trim commands have no measurable effect on this aircraft: the "
            f"trim was moved from {100 * t_lo:+.0f}% to {100 * t_hi:+.0f}% "
            "(movement confirmed by read-back) while the held elevator never "
            "changed. The aircraft likely manages pitch trim through its own "
            "systems and cannot be auto-calibrated.")

    def _neutral_give_up(self, telem_data, what):
        """Neutralization deadline/timeout: degrade or abort, honestly.

        Sweeping around the current point is only a sane degradation when the
        trim has DEMONSTRATED it responds (direction verified against the
        read-back). Without that proof the sweep is doomed and proceeding just
        defers the failure to a slow station timeout: commanded-but-deaf trim
        aborts as unresponsive, and a give-up before any trim was even
        commanded (first step settles in place; the aircraft would not calm
        down) aborts naming the settle problem.
        """
        if not self._trim_dir_verified:
            if abs(self._sign_cmd_accum) >= self.TRIM_SIGN_CHECK_DIST:
                self._abort("Trim is not responding to commands")
            else:
                # Nothing was commanded yet, so name the gate that actually
                # kept the settle from completing (the settle test is
                # at-target AND VS AND roll — reporting VS for a trim-drift
                # or roll problem sends the user chasing the wrong thing).
                rb_err = abs((telem_data.ElevTrimPct or 0) - self._neut_target)
                vs_fpm = (telem_data.VerticalSpeed or 0) * MS_TO_FPM
                hold_txt = "on the target sink" if self.vs_target else "level"
                if rb_err > self.TRIM_STATION_TOL:
                    why = (f"trim read-back drifted {100 * rb_err:.1f}% off the hold "
                           "point while it was held steady — the aircraft's systems "
                           "may be mishandling the trim commands or moving the trim "
                           "themselves")
                elif abs(telem_data.Roll or 0) > self.STABLE_ROLL_TOL:
                    why = f"could not hold wings level (roll {telem_data.Roll:+.1f} deg)"
                elif abs(self._vs_err(telem_data.VerticalSpeed)) > self.STABLE_VS_TOL:
                    why = f"could not settle {hold_txt} (VS {vs_fpm:+.0f} fpm)"
                else:
                    why = ("flight would not stay settled long enough to measure "
                           f"(VS {vs_fpm:+.0f} fpm at abort)")
                self._abort(f"Trim neutralization: {why}")
            return
        logger.warning(f"{what}; sweeping around the current point "
                       f"(steady elevator ~{self._neut_u_final:+.2f})")
        self._finish_neutral(telem_data, centered=False)

    def _finish_neutral(self, telem_data, centered):
        """Hand leveling back to the PID from a clean state and start the sweep.

        At the natural point the correct takeover baseline is zero axis; on a
        timeout the residual elevator becomes the new baseline instead (a
        constant, so it shifts only the fit's intercept, never the slope).
        """
        self._u_base_y = 0.0 if centered else self._neut_u_final
        self._pitch_pid.reset()
        self._pitch_ref_n = None
        self._pitch_ref0 = None
        if self.assist_mode and not self._assist_sweep_started:
            self._enter_assist_hold(telem_data)
        elif self.assist_mode:
            # From the assistant the speed is already user-stabilized (the
            # sweep trigger is gated on assist_stable); a short confirmation
            # hold guards the race between the gate reading and the click.
            self._enter_speed_settle(telem_data, duration=self.ASSIST_CONFIRM_SETTLE_S)
        else:
            # Manual path: always settle — skipping only ever traded 20
            # seconds for a drift-skewed slope.
            self._enter_speed_settle(telem_data)

    # ---- Phase: trim assistant hold ---------------------------------------------

    def _enter_assist_hold(self, telem_data):
        self._set_state(CalState.ASSIST_HOLD, "holding for the user's test speed")
        self.assist_stable = False
        self._assist_stable_since = None
        self._assist_unverified_rails = 0
        self._assist_ias_hist = []
        self._assist_u_hist = []
        self._assist_u_mean = self._neut_u_final
        self._assist_mean_steady = False
        self._assist_agree_since = None
        self._assist_settle_t = None
        self._assist_move_t = time.perf_counter()
        # Relief direction: a NEGATIVE slope means positive (nose-up) load is
        # relieved by positive (nose-up) trim — the normal convention. The
        # slope's magnitude is only trusted for sizing when neutralization
        # actually measured one (multi-step secant).
        self._assist_dir = -1 if (self._trim_slope_est or -1.0) > 0 else 1
        self._assist_sized = self._trim_slope_est is not None
        self._assist_premove = None
        self._assist_worsen = 0
        self._assist_dir_proven = False
        self._assist_seq0 = None
        self._assist_seq_n = 0
        self._assist_railed = False
        # _neut_prev (last settled point) and _trim_slope_est deliberately
        # carry over from neutralization: they let the first retrim after a
        # power change go straight to the predicted level point instead of
        # re-probing blind.
        self._begin_neut_step(telem_data.ElevTrimPct or 0.0)
        logger.info("Trim assistant holding level; waiting for the user to "
                    "pick a test speed")

    def _do_assist_hold(self, telem_data, dt):
        """Hold level indefinitely while the user sets their test power/speed.

        The elevator PID stays the fast stabilizer (trim is rate-limited and
        cannot arrest a VS excursion), but trim is the PRIMARY corrector:
        every ASSIST_RETRIM_S the windowed mean load (see
        :meth:`_assist_mean_load`) is checked and an over-tolerance load is
        answered with one rate-limited move to the predicted level point,
        sized by the measured trim-response slope (Newton step); the elevator
        unwinds in lockstep during the move. Sample-and-hold, never a
        continuous servo on the lagged elevator (which hunts — see
        :meth:`_do_trim_neutral`).

        There is deliberately NO oscillation/flight-state detection gating
        the moves: the single crossing-bounded estimator handles calm and
        porpoising flight in one mechanism. Two earlier designs that gated
        moves on trust predicates (a level gate; an oscillation-presence +
        trusted-period pair) each field-froze the retrim with a large load
        parked on the elevator. There is no give-up: only the safety
        envelope and an unresponsive trim end the hold.
        """
        if self._cancel_deadline is not None:
            # Soft cancel: keep leveling while the hold spring WALKS the
            # stick to the release-continuity position (rate-limited — an
            # instant set can whack a resting hand), then hand back.
            # (_finish keeps the CURRENT trim when ending from the hold.)
            arrived = self._walk_hold_toward(self._release_hold_targets(telem_data), dt)
            self._run_leveling(telem_data, dt)
            now_c = time.perf_counter()
            if now_c >= self._cancel_deadline and \
                    (arrived or now_c >= self._cancel_deadline + self.HOLD_WALK_GRACE_S):
                self._abort(self._cancel_reason or "Cancelled by user")
            return

        if self._sweep_requested:
            self._sweep_requested = False
            self._assist_sweep_started = True
            u = self._assist_u_mean if self._assist_u_mean is not None else 0.0
            self._neut_u_final = u
            logger.info(f"Trim assistant handing off to the sweep "
                        f"(steady elevator {u:+.3f})")
            self._finish_neutral(telem_data, centered=abs(u) <= self.NEUT_U_TOL)
            return

        self._run_leveling(telem_data, dt)  # includes the oscillation watchdog

        ramping = self._advance_trim(self._neut_target, telem_data, dt)
        if ramping is None:
            return  # aborted (unresponsive trim)

        now = time.perf_counter()

        # The level attitude legitimately changes with the user's chosen
        # speed; drift the pitch-guard anchor slowly with it so a slow
        # re-trim never trips the excursion guard while a genuine runaway
        # (fast divergence) still does.
        if self._pitch0 is not None and telem_data.Pitch is not None:
            slew = self.ASSIST_PITCH_ANCHOR_SLEW * dt
            self._pitch0 += clamp(telem_data.Pitch - self._pitch0, -slew, slew)

        # Load history feeds the windowed means (retention slightly beyond
        # the long window so the full-span authority check can be met).
        self._assist_u_hist.append((now, self._u_elev))
        while self._assist_u_hist and \
                now - self._assist_u_hist[0][0] > self.ASSIST_MEAS_LONG_S + 2.0:
            self._assist_u_hist.pop(0)

        # Windowed IAS rate (NOT a per-frame derivative — that amplifies
        # telemetry jitter ~30x and kept the calm gate from ever arming).
        ias = telem_data.IAS or 0.0
        hist = self._assist_ias_hist
        hist.append((now, ias))
        while hist and now - hist[0][0] > self.ASSIST_IAS_RATE_WINDOW_S:
            hist.pop(0)
        span = now - hist[0][0]
        ias_rate = (ias - hist[0][1]) / span if span >= 1.0 else 0.0

        at_target = (not ramping and
                     abs((telem_data.ElevTrimPct or 0) - self._neut_target) <= self.TRIM_STATION_TOL)
        level = abs(self._vs_err(telem_data.VerticalSpeed)) <= self.STABLE_VS_TOL \
            and abs(telem_data.Roll) <= self.STABLE_ROLL_TOL

        # Arrival tracking: data taken before/while the trim ramps reflects
        # the load TRANSFERRING to the trim; the estimator only trusts data
        # taken after arrival (plus a short guard).
        if not at_target:
            self._assist_settle_t = None
        elif self._assist_settle_t is None:
            self._assist_settle_t = now
            self._assist_move_t = now   # full cadence before the next attempt

        self._assist_u_mean, self._assist_mean_steady = self._assist_mean_load(now)

        # Relief verification — the human rule: trim exists to RELIEVE the
        # elevator, so every move is judged by whether it actually did.
        # Judged once per move, at the first mean after arrival, and skipped
        # while the airspeed is trending (a power change alters the load by
        # itself and would frame the move for it — the field incident: a
        # power-change load pinned on a 6% trim move taught a wrong-SIGN
        # slope, which then drove every Newton move the wrong way while the
        # old sign guard rejected all the true measurements).
        if self._assist_premove is not None and at_target and \
                self._assist_u_mean is not None:
            pre_u, prev_step = self._assist_premove
            self._assist_premove = None
            if abs(ias_rate) > self.ASSIST_IAS_RATE_MAX:
                logger.info(f"Assist move unverified: airspeed trending "
                            f"({ias_rate * 1.94384:+.2f} kt/s) — a power change "
                            "would take credit/blame for the move")
                # A trending airspeed also poisons the sequence baseline
                # (both of its endpoints must be power-steady).
                self._assist_seq0 = None
                self._assist_seq_n = 0
            elif abs(prev_step) > 1e-6:
                if abs(self._assist_u_mean) < abs(pre_u) - 0.01:
                    # The move relieved: direction confirmed; its observed
                    # effect IS a slope measurement — better data than any
                    # settled secant pair (bigger baseline, same conditions).
                    self._assist_worsen = 0
                    self._assist_sized = True
                    self._assist_dir_proven = True
                    self._assist_unverified_rails = 0
                    self._assist_seq0 = None
                    self._assist_seq_n = 0
                    s = (self._assist_u_mean - pre_u) / prev_step
                    learned = 0.05 <= abs(s) <= 20.0 and (s < 0) == (self._assist_dir > 0)
                    if learned:
                        self._trim_slope_est = s
                    logger.info(f"Assist move relieved: load {pre_u:+.3f} -> "
                                f"{self._assist_u_mean:+.3f}"
                                + (f", slope learned {s:+.2f}" if learned else ""))
                else:
                    self._assist_seq_n += 1
                    if abs(self._assist_u_mean) > abs(pre_u) + 0.02:
                        # The move made it worse: stop trusting the magnitude
                        # at once; flip the direction only on two consecutive
                        # worsenings (one can be an unlucky gust/transient).
                        self._assist_sized = False
                        self._assist_worsen += 1
                        logger.warning(f"Assist move WORSENED the load: {pre_u:+.3f} -> "
                                       f"{self._assist_u_mean:+.3f} "
                                       f"(sizing distrusted; {self._assist_worsen} consecutive)")
                        if self._assist_worsen >= 2:
                            self._assist_dir = -self._assist_dir
                            self._assist_worsen = 0
                            self._assist_dir_proven = False
                            self._assist_seq0 = None
                            self._assist_seq_n = 0
                            logger.warning("Trim assistant: relief direction flipped "
                                           "after two worsening moves")
                    # An unrelieved run of moves is judged as a WHOLE, not
                    # only per-move — on a weak-trim aircraft a wrong
                    # direction can worsen the load by less than the per-move
                    # threshold on every single move (the RV-10 field
                    # incident: ~+0.01 per probe, fifteen straight
                    # inconclusive verdicts while the trim marched to its
                    # rail).
                    self._assist_seq_flip_check(telem_data)

        # Single retrim rule: at a fixed cadence, an over-tolerance mean load
        # gets one relief move. Steady means move at the neutral tolerance;
        # under disagreement the (leak-bounded) long mean moves only loads
        # above the unsteady threshold.
        if at_target and self._assist_u_mean is not None and \
                now - self._assist_move_t >= self.ASSIST_RETRIM_S:
            self._assist_move_t = now
            u = self._assist_u_mean
            t = telem_data.ElevTrimPct or self._neut_target
            threshold = self.NEUT_U_TOL if self._assist_mean_steady \
                else self.ASSIST_U_MOVE_UNSTEADY
            if abs(u) > threshold:
                step = self._assist_step_from(u)
                # Trim-rail guard: an outward step from a read-back already at
                # the command clamp is a no-op — re-issuing it forever only
                # feeds the verdict judge zero-effect moves (the RV-10 field
                # incident ended pegged at the rail with every verdict
                # inconclusive).
                if abs(t) >= self.TRIM_CMD_CLAMP - self.TRIM_STATION_TOL \
                        and t * step > 0:
                    if not self._assist_dir_proven:
                        # A rail with the load unchanged across the whole
                        # traverse is a full-range zero-response measurement;
                        # two of them (opposite directions) prove the entire
                        # trim range dead — the CL650 signature. The load
                        # comparison excludes the masked-relief case (power
                        # changes suppressing verification while trim was in
                        # fact working: there the load would have moved).
                        seq = self._assist_seq0
                        if seq is not None and \
                                abs(u - seq[1]) <= self.ASSIST_INERT_DU:
                            self._assist_unverified_rails += 1
                        else:
                            self._assist_unverified_rails = 0
                        if self._assist_unverified_rails >= self.ASSIST_INERT_RAILS:
                            self._abort(self._inert_trim_reason(
                                -self.TRIM_CMD_CLAMP, self.TRIM_CMD_CLAMP))
                            return
                        # This direction never once relieved and the trim ran
                        # out of range trying: it cannot get worse than a
                        # rail — reverse.
                        self._assist_dir = -self._assist_dir
                        self._assist_sized = False
                        self._assist_worsen = 0
                        self._assist_seq0 = None
                        self._assist_seq_n = 0
                        logger.warning(
                            "Trim assistant: trim hit its limit with the load "
                            "unrelieved and the relief direction never "
                            "verified — reversing direction")
                        step = self._assist_step_from(u)
                    else:
                        # Direction is relief-proven: the aircraft is
                        # genuinely out of trim authority at this speed.
                        if not self._assist_railed:
                            self._assist_railed = True
                            logger.warning(
                                f"Trim assistant: trim is at its limit with "
                                f"load {u:+.3f} still held — out of trim "
                                "authority at this speed")
                        step = None
                if step is not None:
                    self._assist_railed = False
                    self._assist_premove = (u, step)
                    if self._assist_seq0 is None:
                        # Baseline for judging the coming run of moves as a
                        # whole (cleared by any relieved verdict).
                        self._assist_seq0 = (t, u)
                    logger.info(
                        f"Assist retrim: load {u:+.3f} "
                        f"({'steady' if self._assist_mean_steady else 'long-window'}), "
                        f"{'Newton' if self._assist_sized else 'probe'} step {step:+.3f} "
                        f"-> trim {t + step:+.3f}" + self._thr_context(now))
                    # Keep the load history valid ACROSS the move: its expected
                    # effect on the load is step*slope, so shifting the stored
                    # samples by that much makes history read as if the trim had
                    # always been at the new setting — the long window never
                    # needs resetting through a correction sequence. Only done
                    # while the sizing model is relief-verified; otherwise start
                    # the history fresh.
                    if self._assist_sized and self._trim_slope_est is not None and \
                            abs(self._trim_slope_est) >= self.ASSIST_MIN_SLOPE:
                        du = step * self._trim_slope_est
                        self._assist_u_hist = [(ts, uu + du)
                                               for ts, uu in self._assist_u_hist]
                    else:
                        self._assist_u_hist = []
                    self._begin_neut_step(t + step)
            else:
                self._assist_railed = False

        # Readiness with asymmetric hysteresis: distrust instantly, trust
        # only after a sustained calm window. Trimmed reads the same windowed
        # mean the mover uses — one estimator, one truth.
        trimmed = self._assist_u_mean is not None and \
            abs(self._assist_u_mean) <= self.NEUT_U_TOL
        calm = at_target and level and trimmed and abs(ias_rate) <= self.ASSIST_IAS_RATE_MAX
        was_stable = self.assist_stable
        if calm:
            if self._assist_stable_since is None:
                self._assist_stable_since = now
            self.assist_stable = \
                now - self._assist_stable_since >= self.ASSIST_STABLE_HOLD_S
        else:
            self._assist_stable_since = None
            self.assist_stable = False
        if self.assist_stable != was_stable:
            # Flapping guard: in bumpy air the gate can blink on every VS
            # bump — demote rapid arm/disarm pairs to debug.
            log = logger.info if self._assist_gate_log_t is None or \
                now - self._assist_gate_log_t >= 10.0 else logger.debug
            self._assist_gate_log_t = now
            if self.assist_stable:
                log(f"Assist READY: level, trimmed (load "
                    f"{self._assist_u_mean:+.3f}), speed steady at "
                    f"{(ias or 0) * 1.94384:.0f} kt — sweep may start")
            else:
                why = ("retrimming" if not at_target
                       else "VS/roll disturbed" if not level
                       else "load measurement restarting" if self._assist_u_mean is None
                       else f"load {self._assist_u_mean:+.3f} over tolerance" if not trimmed
                       else f"airspeed trending {ias_rate * 1.94384:+.2f} kt/s")
                log(f"Assist ready gate DISARMED: {why}" + self._thr_context(now))

        ias_kt = ias * 1.94384
        if self.assist_stable:
            self.status_message = (
                f"Holding level and trimmed at {ias_kt:.0f} kt — adjust power "
                "to your test speed, or start the sweep")
        elif self._assist_u_mean is None:
            self.status_message = (
                f"Trim assistant: measuring the trim load… (IAS {ias_kt:.0f} kt)")
        elif self._assist_railed:
            self.status_message = (
                f"Trim assistant: trim is at its limit — the remaining load "
                f"can't be trimmed away (IAS {ias_kt:.0f} kt)")
        elif not (at_target and level) or not trimmed:
            self.status_message = (
                f"Trim assistant: re-trimming for the current power setting… "
                f"(IAS {ias_kt:.0f} kt)")
        else:
            self.status_message = (
                f"Trim assistant: waiting for the airspeed to settle… "
                f"(IAS {ias_kt:.0f} kt)")

    def _assist_mean_load(self, now):
        """Mean elevator load with a steadiness verdict: ``(mean, steady)``.

        Two trailing means: SHORT (responsive) and LONG (authoritative).
        When they AGREE, nothing is swinging or winding on the timescale
        that matters — the fresh short mean is the truth and the caller may
        act at the neutral tolerance. When they DISAGREE, only the long mean
        is meaningful; a cycle's leak into it is bounded by A*P/(pi*W)
        (window-length cycles leak exactly zero), so the caller holds it to
        the unsteady threshold instead. Returns ``(None, False)`` only while
        post-arrival data is still shorter than the minimum window or the
        means disagree before the long window has half its span — a bounded
        wait, never a standing state: the long mean always becomes available
        and moves any large load regardless of what the flight is doing.
        """
        if self._assist_settle_t is None:
            return None, False

        def trailing_mean(t1):
            pts = [u for t, u in self._assist_u_hist if t >= t1]
            if not pts:
                return None, None
            return sum(pts) / len(pts), max(pts) - min(pts)

        # SHORT reads only fresh post-arrival data (a move's ramp/unwind
        # transient must not masquerade as truth); LONG reads the whole
        # retained history — the move-time adjustment above keeps it valid
        # across corrections, which is what makes it both deadlock-proof
        # (always available) and leak-bounded (full window length).
        floor = self._assist_settle_t + self.ASSIST_SETTLE_GUARD_S
        if now - floor < self.ASSIST_MEAS_MIN_S or not self._assist_u_hist:
            return None, False
        hist_span = now - self._assist_u_hist[0][0]
        if hist_span < self.ASSIST_MEAS_MIN_S:
            return None, False
        short, short_range = trailing_mean(max(floor, now - self.ASSIST_MEAS_SHORT_S))
        long_, _ = trailing_mean(now - self.ASSIST_MEAS_LONG_S)
        if short is None or long_ is None:
            return None, False
        # Steadiness needs four things: agreement; windows that genuinely
        # differ (a short history makes long == short and every transient
        # "agrees" with itself); agreement that PERSISTS; and quiet short-
        # window CONTENT — two swinging lobes sweep past each other twice
        # per cycle and momentarily agree at a nonzero value, and a still-
        # growing load can momentarily match the diluted long mean; in both
        # cases the short window is visibly in motion. (Content-quiet alone
        # was a defeated heuristic — a slow cycle is flat at its extremes —
        # but there the means DISAGREE, so it never reaches this check.)
        agree = abs(short - long_) <= self.ASSIST_U_AGREE and \
            short_range <= self.ASSIST_U_STEADY_RANGE
        if agree:
            if self._assist_agree_since is None:
                self._assist_agree_since = now
        else:
            self._assist_agree_since = None
        if agree and hist_span >= 1.5 * self.ASSIST_MEAS_SHORT_S and \
                now - self._assist_agree_since >= self.ASSIST_RETRIM_S:
            return short, True
        # The long mean is only leak-bounded at FULL span; a partial long
        # window is just a medium window with leak.
        if hist_span >= self.ASSIST_MEAS_LONG_S:
            return long_, False
        return None, False

    def _assist_seq_flip_check(self, telem_data):
        """Judge the whole unrelieved run of moves, not only the last one.

        The run's own secant — total load change over total trim travel,
        taken between power-steady verdicts — has the largest baseline of any
        measurement available, so it resolves a wrong direction whose
        per-move effect hides inside the verdict dead zone. It may only
        override a direction that has NEVER been relief-verified: a proven
        direction is never second-guessed by slow drift.
        """
        if self._assist_dir_proven or self._assist_seq0 is None or \
                self._assist_seq_n < self.ASSIST_SEQ_MIN_MOVES:
            return
        t0, u0 = self._assist_seq0
        t = telem_data.ElevTrimPct or self._neut_target
        d_trim = t - t0
        du = self._assist_u_mean - u0
        if abs(d_trim) < self.ASSIST_SEQ_MIN_DTRIM or \
                abs(du) < self.NEUT_SLOPE_MIN_DU:
            return
        slope = du / d_trim
        new_dir = -1 if slope > 0 else 1
        if new_dir == self._assist_dir:
            return
        n = self._assist_seq_n
        self._assist_dir = new_dir
        self._assist_worsen = 0
        self._assist_seq0 = None
        self._assist_seq_n = 0
        if 0.05 <= abs(slope) <= 20.0:
            # The run is a real slope measurement (same trust bounds as
            # relief learning) — the first corrected move can be Newton-sized.
            self._trim_slope_est = slope
            self._assist_sized = True
        else:
            self._assist_sized = False
        logger.warning(
            f"Trim assistant: {n} unrelieved moves measured slope "
            f"{slope:+.2f} over {d_trim:+.3f} trim — relief direction reversed")

    def _assist_step_from(self, u):
        """Trim step RELIEVING the measured load ``u``.

        Direction comes from the verified relief direction — never from the
        slope model. Magnitude is Newton-sized from the measured slope only
        while that model's moves keep demonstrably relieving; otherwise the
        gentle probe (feed a little trim, feel the relief — like a human).
        A wrong model can mis-size one bounded move; it cannot reverse it.
        """
        sign = self._assist_dir if u > 0 else -self._assist_dir
        mag = self.NEUT_PROBE
        if self._assist_sized and self._trim_slope_est is not None and \
                abs(self._trim_slope_est) >= self.ASSIST_MIN_SLOPE:
            mag = min(abs(u / self._trim_slope_est), self.NEUT_STEP_MAX)
        return math.copysign(mag, sign)

    # ---- Phase: airspeed settle ------------------------------------------------

    def _enter_speed_settle(self, telem_data, duration=None):
        self._settle_s = duration if duration is not None else self.SPEED_SETTLE_S
        self._set_state(CalState.SPEED_SETTLE, f"{self._settle_s:.0f}s settle hold")
        self._settle_start_t = time.perf_counter()
        self.status_message = "Letting airspeed stabilize…"

    def _do_speed_settle(self, telem_data, dt):
        """Hold straight-and-level at the natural trim point so the airspeed
        settles at the current throttle/trim before the sweep begins. The IAS
        reference used for the sweep's drift warning and abort band is captured
        at sweep entry, so it then reflects the equilibrium speed."""
        self._run_leveling(telem_data, dt)
        remaining = self._settle_s - (time.perf_counter() - self._settle_start_t)
        ias_kt = (telem_data.IAS or 0) * 1.94384
        self.status_message = (f"Letting airspeed stabilize… {max(remaining, 0):.0f} s "
                               f"(IAS {ias_kt:.0f} kt)")
        if remaining <= 0:
            self._enter_sweep(telem_data)

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
        # Slope-adapted station step (see SWEEP_U_STEP_TARGET): keeps the
        # per-station elevator excursion comparable across aircraft.
        self._sweep_step = self.SWEEP_STEP
        self._station_tol = self.TRIM_STATION_TOL
        slope = self._trim_slope_est
        if slope is not None and abs(slope) > 1e-6:
            self._sweep_step = clamp(self.SWEEP_U_STEP_TARGET / abs(slope),
                                     self.SWEEP_STEP_MIN, self.SWEEP_STEP)
            self._station_tol = min(self.TRIM_STATION_TOL,
                                    max(0.005, self._sweep_step / 2.0))
            if self._sweep_step < self.SWEEP_STEP:
                logger.info(f"Steep trim response (slope {slope:+.1f}): sweep "
                            f"step reduced to {self._sweep_step:.3f} "
                            f"(station tol {self._station_tol:.3f})")
        self._begin_step(self._trim0)   # measure the center station first
        self._set_state(CalState.SWEEP, f"anchored at trim {self._trim0:+.3f}")
        logger.info(f"Sweep started (adaptive): trim0={self._trim0:.3f}, "
                    f"step={self._sweep_step:.3f}, max half-width={self.SWEEP_MAX_HALF}, "
                    f"u budget={self.SWEEP_U_BUDGET}")

    def _begin_step(self, target):
        self._current_target = target
        self._sweep_targets.append(target)
        # Deadline covers the ramp to the station plus the settle allowance.
        ramp_time = abs(target - self._trim_cmd) / self.TRIM_RATE_PER_S
        self._step_settle_deadline = time.perf_counter() + ramp_time + self.STEP_SETTLE_TIMEOUT_S
        self._dwell_since = None
        self._dwell_samples = []
        self._station_best = None

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
            cand = self._edge[side] + self._sweep_step * side
            if abs(cand - self._trim0) > self.SWEEP_MAX_HALF + 1e-9 or abs(cand) > 0.95:
                self._side_done[side] = True
                continue
            # Predict the candidate's steady elevator from the samples so
            # far and stop the side BEFORE launching a station that would
            # ride into the saturation guard (C208B field abort: the
            # retrospective check alone let it saturate mid-approach with 3
            # samples and the nose-down side untouched). The threshold is
            # SOFT — one outermost station may land past the budget, like
            # every field-validated run sampled its band edge; refuse only
            # predictions deep past it or near the saturation guard.
            if len(self._samples) >= 2 and self._u_center is not None:
                slope, intercept, _ = self._fit(self._samples)
                u_pred = intercept + slope * cand
                if abs(u_pred - self._u_center) > \
                        self.SWEEP_U_BUDGET + self.SWEEP_U_STEP_TARGET / 2 or \
                        abs(u_pred) > self.U_ELEV_SAT - 0.05:
                    self._side_done[side] = True
                    logger.info(
                        f"Sweep side {side:+d}: next station predicts "
                        f"u_elev {u_pred:+.2f} (beyond budget); stopping "
                        f"expansion on that side")
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
            self._trim_slew_t = time.perf_counter()
        elif self._trim_dir_verified:
            # Command at target: close the loop on the read-back. Aircraft
            # whose reported travel limits differ from their actual travel
            # land the read-back proportionally short of the command; bleed
            # the steady residual off with a bounded, slew-limited overdrive
            # (a no-op on aircraft with an accurate 1:1 mapping).
            resid = target - (telem_data.ElevTrimPct or 0)
            if abs(resid) > self.TRIM_RB_DEADBAND:
                self._trim_overdrive = clamp(
                    self._trim_overdrive + clamp(resid, -step, step),
                    -self.TRIM_OVERDRIVE_MAX, self.TRIM_OVERDRIVE_MAX)
        self._set_trim(self._trim_cmd + self._trim_overdrive)

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
                    self._trim_overdrive = 0.0
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

        # Outbound-ramp freeze: measure where we are instead of lurching on.
        # Gated on the current trim already being on the TARGET's side of
        # trim0 — side-switch traverses legitimately start with a large
        # elevator excursion left over from the old side's outermost station
        # and must not trigger this.
        trim_now = telem_data.ElevTrimPct or 0
        if ramping and self._side != 0 and self._u_center is not None and \
                (trim_now - self._trim0) * self._side > 0 and \
                abs(self._u_elev - self._u_center) > self.SWEEP_FREEZE_DU:
            self._edge[self._side] = trim_now
            logger.info(
                f"Elevator excursion {abs(self._u_elev - self._u_center):.2f} "
                f"while ramping to trim {100 * target:+.1f}%; measuring at "
                f"{100 * trim_now:+.1f}% instead (station step will resize "
                f"from this sample)")
            self._begin_step(trim_now)
            return

        # Elevator-authority guard (safety net behind the adaptive budget):
        # sustained near-full elevator means level flight is barely holdable.
        if abs(self._u_elev) > self.U_ELEV_SAT:
            if self._sat_recovering:
                # Unwinding out of a truncated side toward the new station:
                # the elevator legitimately stays railed while the trim
                # slews back. The guard re-arms once u drops below the
                # threshold; pitch/roll excursion guards still protect.
                pass
            elif self._u_sat_since is None:
                self._u_sat_since = now
            elif now - self._u_sat_since > self.U_ELEV_SAT_TIME_S:
                self._u_sat_since = None
                if self._side != 0 and not self._side_done[-self._side]:
                    # This side is out of elevator authority but the other
                    # side is unexplored — truncate here and walk the other
                    # side instead of giving up. Aborting threw away a whole
                    # fresh half-band (C208B field abort: 3 nose-up stations,
                    # nose-down side never visited).
                    self._side_done[self._side] = True
                    logger.warning(
                        f"Elevator saturated heading to a "
                        f"{'nose-up' if self._side > 0 else 'nose-dn'}-side "
                        f"station; truncating that side "
                        f"({len(self._samples)} samples) and continuing on "
                        f"the other side")
                    nxt = self._next_station_target(self._u_elev)
                    if nxt is not None:
                        self._sat_recovering = True
                        self._begin_step(nxt)
                        return
                if len(self._samples) >= self.MIN_SAMPLES_FOR_FIT:
                    logger.warning(f"Elevator near saturation; truncating sweep with "
                                   f"{len(self._samples)} samples")
                    self._solve()
                else:
                    self._abort("Elevator saturated before enough samples could be gathered")
                return
        else:
            self._u_sat_since = None
            self._sat_recovering = False

        at_station = (not ramping and
                      abs((telem_data.ElevTrimPct or 0) - target) <= self._station_tol)
        settled = at_station and \
            abs(self._vs_err(telem_data.VerticalSpeed)) <= self.STABLE_VS_TOL and \
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
                mean_trim = sum(s[0] for s in self._dwell_samples) / n
                mean_u = sum(s[1] for s in self._dwell_samples) / n
                mean_vs = sum(s[2] for s in self._dwell_samples) / n
                mean_ias = sum(s[3] for s in self._dwell_samples) / n
                # Residual is measured against the held condition (0 for level,
                # the target sink for a glider), so a station is "clean" when
                # it holds the intended VS — not only when VS is zero.
                mean_vs_err = self._vs_err(mean_vs)
                if abs(mean_vs_err) > self.SAMPLE_VS_MEAN_TOL:
                    # Still converging (one-sided VS residual): retry the dwell
                    # rather than record a biased sample, keeping the best
                    # completed dwell for the deadline compromise below. The
                    # 4th slot carries the deviation from target (what the hard
                    # gate and the flagged result both judge).
                    if self._station_best is None or \
                            abs(mean_vs_err) < abs(self._station_best[3]):
                        self._station_best = (mean_trim, mean_u, mean_ias, mean_vs_err)
                    self._dwell_since = None
                    self._dwell_samples = []
                else:
                    self._accept_station(mean_trim, mean_u, mean_ias)
                    return
        else:
            self._dwell_since = None
            self._dwell_samples = []

        if now > self._step_settle_deadline:
            self._finish_station_on_deadline(telem_data, target)

    def _accept_station(self, mean_trim, mean_u, mean_ias, residual_vs=None):
        """Record a station sample and advance the sweep (or solve at the end).

        ``residual_vs`` marks a deadline-compromise sample taken with a steady
        VS residual; it is surfaced in the result so the user can judge it.
        """
        side_txt = "center" if self._side == 0 else \
            ("nose-up side" if self._side > 0 else "nose-dn side")
        self._samples.append((mean_trim, mean_u))
        self._station_ias.append(mean_ias)
        if self._side == 0:
            self._u_center = mean_u
        if residual_vs is not None:
            self._flagged.append({
                "index": len(self._samples) - 1,
                "trim": mean_trim,
                "vs_fpm": residual_vs * MS_TO_FPM,
            })
        logger.info(
            f"Station {len(self._samples)} ({side_txt}): "
            f"trim {mean_trim:+.4f}, u_elev {mean_u:+.4f}, "
            f"IAS {mean_ias * 1.94384:.1f} kt"
            + (f", residual VS {residual_vs * MS_TO_FPM:+.0f} fpm (flagged)"
               if residual_vs is not None else "")
        )
        self.progress = len(self._samples) / self.SWEEP_MAX_STATIONS
        # Self-sizing: the sweep's own samples ARE a slope measurement. A
        # slope-blind entry (assist session without a power change teaches
        # no slope) starts at the default step, which on a steep aircraft
        # lurches several times the designed excursion per station. Shrink
        # the step toward the design target as soon as the fit knows better
        # (shrink-only: station spacing may tighten mid-sweep, never widen).
        if len(self._samples) >= 2:
            slope_fit, _, _ = self._fit(self._samples)
            if abs(slope_fit) > 1e-6:
                want = clamp(self.SWEEP_U_STEP_TARGET / abs(slope_fit),
                             self.SWEEP_STEP_MIN, self.SWEEP_STEP)
                if want < self._sweep_step * 0.95:
                    self._sweep_step = want
                    self._station_tol = min(self.TRIM_STATION_TOL,
                                            max(0.005, want / 2.0))
                    logger.info(
                        f"Sweep step resized from measured slope "
                        f"{slope_fit:+.2f}: step {want:.3f} "
                        f"(station tol {self._station_tol:.3f})")
        nxt = self._next_station_target(mean_u)
        if nxt is None:
            if len(self._samples) >= self.MIN_SAMPLES_FOR_FIT:
                self._solve()
            else:
                self._abort("Trim band exhausted before enough samples "
                            "could be gathered")
            return
        self._begin_step(nxt)

    def _finish_station_on_deadline(self, telem_data, target):
        """Station deadline expired without a clean sample: compromise or abort.

        A VS residual that holds steady across retries is the leveler chasing
        a moving target at finite gain (usually slow airspeed drift), not a
        lack of elevator authority — waiting longer cannot satisfy the clean
        gate. Within SAMPLE_VS_HARD_TOL the best dwell is accepted and flagged
        in the results; beyond it the run aborts, naming the gate that failed.
        """
        station = len(self._samples) + 1
        # _station_best[3] is the deviation from the held condition (target
        # sink for a glider, else level), so these gates read identically in
        # both cases.
        if self._station_best is not None and \
                abs(self._station_best[3]) <= self.SAMPLE_VS_HARD_TOL:
            mean_trim, mean_u, mean_ias, mean_vs_err = self._station_best
            logger.warning(
                f"Station {station}: accepting best dwell with residual VS "
                f"{mean_vs_err * MS_TO_FPM:+.0f} fpm (clean gate is "
                f"{self.SAMPLE_VS_MEAN_TOL * MS_TO_FPM:.0f} fpm); flagged in results")
            self._accept_station(mean_trim, mean_u, mean_ias, residual_vs=mean_vs_err)
            return
        rb_err = abs((telem_data.ElevTrimPct or 0) - target)
        if rb_err > self._station_tol:
            why = (f"trim read-back never reached the station target "
                   f"(off by {100 * rb_err:.1f}%)")
        elif abs(telem_data.Roll or 0) > self.STABLE_ROLL_TOL:
            why = f"could not hold wings level (roll {telem_data.Roll:+.1f} deg)"
        elif self._station_best is not None:
            why = (f"VS residual would not settle below "
                   f"{self.SAMPLE_VS_HARD_TOL * MS_TO_FPM:.0f} fpm (best "
                   f"{self._station_best[3] * MS_TO_FPM:+.0f} fpm) — airspeed may "
                   f"be drifting; try steadier power (the Trim Assistant helps "
                   f"find a stable speed first)")
        else:
            why = (f"could not hold level "
                   f"(VS {(telem_data.VerticalSpeed or 0) * MS_TO_FPM:+.0f} fpm)")
        self._abort(f"Sweep station {station}: {why}")

    # ---- Phase: solve --------------------------------------------------------

    def _solve(self):
        self._set_state(CalState.SOLVE, f"{len(self._samples)} stations")
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

        # Calibrated response curve: the runtime virtual offset must mirror
        # the measured level-hold curve, offs(T) = -(u(T) - u(0)), stored in
        # absolute axis units (independent of physical gain). Zero-referenced
        # at trim 0 via interp/edge-extrapolation of the stations.
        curve = None
        if len(self._samples) >= 2:
            pts = sorted(self._samples)
            xs = [p[0] for p in pts]
            us = [p[1] for p in pts]
            u_ref = piecewise_linear(xs, us, 0.0)
            curve = {
                "points": [{"t": round(t, 4), "offs": round(-(u - u_ref), 4)}
                           for t, u in zip(xs, us)],
                # Natural trim at calibration: the runtime anchors the curve
                # mode's spring-center walk here so the hands-off rest
                # position matches the legacy center at this point.
                "t0": round(self._trim0, 4),
                "ias_kt": round((self._station_ias[0] if self._station_ias else 0) * 1.94384, 1),
                "date": time.strftime("%Y-%m-%d"),
            }
            if self.vs_target:
                # Historical provenance only (glider runs hold a sink instead
                # of level): recorded so the stored-curve description can say
                # what flight condition the measurement was taken in. Nothing
                # at runtime consumes it — the curve is trim->stick geometry,
                # independent of the VS it was measured at.
                curve["vs_fpm"] = round(self.vs_target * MS_TO_FPM)

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
            "curve": curve,
            "trim0": self._trim0,
            "flagged": list(self._flagged),
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
        self._set_state(CalState.RESTORE, f"restoring trim to {self._trim0:+.3f}")
        self.status_message = "Restoring trim..."

    def _do_restore(self, telem_data, dt):
        """Ramp trim back to the starting point while still holding level, then
        release control. Avoids handing the aircraft back with a trim jump."""
        ramping = self._advance_trim(self._trim0, telem_data, dt)
        if ramping is None:
            return  # aborted (unresponsive trim)
        # Walk the parked stick to the release-continuity position while the
        # trim ramps back — rate-limited, not an instant set: the stick is
        # sim-inert until release, but the firm spring snapping to a new
        # center can whack a hand resting near the controls. At handback the
        # physical position, spring center, and delivered axis all agree
        # with the normal path — no snap in either sense.
        arrived = self._walk_hold_toward(self._release_hold_targets(telem_data), dt)
        self._run_leveling(telem_data, dt)
        if not ramping:
            if self._restore_release_t is None:
                self._restore_release_t = time.perf_counter()
            if arrived or time.perf_counter() - self._restore_release_t \
                    >= self.HOLD_WALK_GRACE_S:
                self.status_message = getattr(self, "_done_status", "Done")
                self._finish(CalState.DONE)

    def _walk_hold_toward(self, targets, dt):
        """Slew the hold-spring center toward ``targets`` at HOLD_WALK_RATE;
        True once both axes have arrived."""
        if self._hold_offs is None or dt <= 0:
            self._hold_offs = targets
            return True
        step = self.HOLD_WALK_RATE * dt
        x, y = self._hold_offs
        tx, ty = targets
        nx = x + clamp(tx - x, -step, step)
        ny = y + clamp(ty - y, -step, step)
        self._hold_offs = (int(round(nx)), int(round(ny)))
        return abs(tx - nx) < 1.0 and abs(ty - ny) < 1.0

    def _release_hold_targets(self, telem_data):
        """Stick position (cpOffset units, both axes) at which the normal
        flight-controls path's first frame delivers exactly what the engine
        is delivering now — release continuity. Mirrors the runtime's
        trim-following offset math (calibrated curve or legacy static gain)."""
        x = clamp(self._u_ail, -1.0, 1.0)
        y = clamp(self._u_elev, -1.0, 1.0)
        try:
            if getattr(self.ac, "trim_following", False):
                t = telem_data.ElevTrimPct or 0
                a = telem_data.AileronTrimPct or 0
                p_y = self.ac.joystick_trim_follow_gain_physical_y
                v_y = self.ac.joystick_trim_follow_gain_virtual_y
                p_x = getattr(self.ac, "joystick_trim_follow_gain_physical_x", 1.0)
                v_x = getattr(self.ac, "joystick_trim_follow_gain_virtual_x", 1.0)
                elev_trim = clamp(t * p_y, -1, 1)
                offs_fn = getattr(self.ac, "_trim_follow_virtual_offset_y", None)
                offs_y = offs_fn(t, elev_trim, telem_data) if offs_fn is not None \
                    else elev_trim * (1 - v_y)
                y = clamp(y + offs_y, -1, 1)
                x = clamp(x + clamp(a * p_x, -1, 1) * (1 - v_x), -1, 1)
        except Exception as e:  # continuity is comfort; never break teardown
            logger.debug(f"release-continuity targets fell back to raw u: {e}")
        return int(x * self.CPOFFSET_RANGE), int(y * self.CPOFFSET_RANGE)

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
        self._watch_oscillation(telem_data.VerticalSpeed)

        self._u_elev = self._run_pitch_loop(telem_data, dt)

        roll_err = self._ail_sign * (0.0 - telem_data.Roll)
        self._u_ail = self._u_base_x + self._roll_pid.update(roll_err, dt)

        self._command_axes(telem_data)

    def _run_pitch_loop(self, telem_data, dt):
        """Elevator command holding level flight.

        Preferred mode is the pitch-attitude CASCADE: an inner PID holds pitch
        attitude (short-period dynamics — fast and naturally damped, so the
        phugoid cannot limit-cycle the way it can against a VS-only loop),
        while the outer loop nudges the attitude target from the VS error and
        slowly adapts the attitude reference (outer integral) to remove any
        steady VS residual. Falls back to the legacy VS-only PID when the
        probe could not determine the pitch-response polarity.
        """
        if self._pitch_mode is None:
            self._engage_pitch_loop(telem_data)

        vs = telem_data.VerticalSpeed
        pitch = telem_data.Pitch
        if self._pitch_mode == "cascade" and pitch is not None:
            pitch_n = pitch * self._pitch_sign
            if self._pitch_ref_n is None:
                self._pitch_ref_n = pitch_n
                self._pitch_ref0 = pitch_n
            self._pitch_ref_n = clamp(
                self._pitch_ref_n + self.CASC_REF_ADAPT * (self.vs_target - vs) * dt,
                self._pitch_ref0 - self.CASC_REF_LIMIT_DEG,
                self._pitch_ref0 + self.CASC_REF_LIMIT_DEG)
            target_n = self._pitch_ref_n + clamp(
                self.CASC_VS_TO_PITCH * (self.vs_target - vs),
                -self.CASC_MAX_TARGET_DEG, self.CASC_MAX_TARGET_DEG)
            self._pitch_target_n = target_n
            return self._u_base_y + self._elev_sign * self._pitch_pid.update(target_n - pitch_n, dt)

        self._pitch_target_n = None
        vs_err = self._elev_sign * (self.vs_target - vs)
        return self._u_base_y + self._pitch_pid.update(vs_err, dt)

    def _engage_pitch_loop(self, telem_data):
        """Pick cascade vs legacy for this run and load the matching base gains.

        The cascade needs the pitch-response polarity (probed alongside the
        elevator polarity); without it a wrong-sign inner loop would be
        positive feedback, so legacy VS-only is the safe fallback.
        """
        use_cascade = self._pitch_sign_known and telem_data.Pitch is not None
        self._pitch_mode = "cascade" if use_cascade else "legacy"
        self._pitch_base_gains = (
            (self.CASC_INNER_KP, self.CASC_INNER_KI, self.CASC_INNER_KD)
            if use_cascade else (self.PITCH_KP, self.PITCH_KI, self.PITCH_KD))
        self._pitch_ref_n = None
        self._pitch_ref0 = None
        self._pitch_pid.reset()
        self._set_pitch_gain_scale(self._gain_scale)
        logger.info(f"Pitch leveling loop engaged: {self._pitch_mode}"
                    + (f" (pitch polarity {self._pitch_sign:+.0f})" if use_cascade else ""))

    def _set_pitch_gain_scale(self, scale):
        """Scale the active pitch-loop gains in place; the PID preserves its
        integral TERM across the change (it carries the trim-holding output)."""
        self._gain_scale = scale
        kp, ki, kd = self._pitch_base_gains
        self._pitch_pid.set_gains(kp * scale, ki * scale, kd * scale)

    def _watch_oscillation(self, vs):
        """Detect a growing VS oscillation and back the pitch gains off.

        Detection is MEAN-INDEPENDENT: local extrema of VS are confirmed by a
        hysteresis retreat, and half-cycle amplitudes are measured
        peak-to-peak. (Zero-crossing based detection missed oscillations
        riding on a nonzero mean — e.g. while the integrator is still
        converging early in STABILIZE — letting a diverging run reach the
        stabilize timeout undetected.)
        """
        if self._trim_slew_t is not None and \
                time.perf_counter() - self._trim_slew_t < self.OSC_TRIM_RINGDOWN_S:
            # Commanded trim motion in progress (or just ended): the VS
            # excursion is the move's forced response. Re-anchor instead of
            # confirming extrema; the amplitude streak survives, so genuine
            # divergence spanning the dwells is still caught.
            self._osc_ext = vs
            self._osc_dir = 0
            self._osc_prev_ext = None
            return
        if self._osc_ext is None:
            self._osc_ext = vs
            return
        h = self.OSC_HYSTERESIS

        if self._osc_dir == 0:
            # Establish the initial movement direction.
            if vs >= self._osc_ext + h:
                self._osc_dir = 1
                self._osc_ext = vs
            elif vs <= self._osc_ext - h:
                self._osc_dir = -1
                self._osc_ext = vs
            return

        if self._osc_dir > 0:
            if vs > self._osc_ext:
                self._osc_ext = vs
                return
            if vs > self._osc_ext - h:
                return  # not retreated enough to confirm the peak
        else:
            if vs < self._osc_ext:
                self._osc_ext = vs
                return
            if vs < self._osc_ext + h:
                return

        # Direction reversal confirmed: _osc_ext is a local extremum.
        if self._osc_prev_ext is not None:
            amp = abs(self._osc_ext - self._osc_prev_ext) / 2.0
            if amp < self.OSC_MIN_PEAK_VS:
                self._osc_amps.clear()
            else:
                self._osc_amps.append(amp)
                if len(self._osc_amps) > self.OSC_PEAKS_TO_ACT:
                    self._osc_amps.pop(0)
                if len(self._osc_amps) == self.OSC_PEAKS_TO_ACT and all(
                        b > a * self.OSC_PEAK_GROWTH
                        for a, b in zip(self._osc_amps, self._osc_amps[1:])):
                    self._backoff_pitch_gains()
        self._osc_prev_ext = self._osc_ext
        self._osc_ext = vs
        self._osc_dir = -self._osc_dir

    def _backoff_pitch_gains(self):
        """Halve the pitch gains in response to a growing oscillation, pushing
        the settle clocks out; at the floor, request a clean abort."""
        grown = [round(a, 2) for a in self._osc_amps]
        self._osc_amps.clear()
        new_scale = self._gain_scale * self.OSC_BACKOFF
        if new_scale < self.OSC_MIN_GAIN_SCALE:
            self._stop_reason = ("Pitch oscillation keeps growing at minimum control "
                                 "gain; aircraft may not be auto-stabilizable at this speed")
            self._stop_requested = True
            return
        self._set_pitch_gain_scale(new_scale)
        now = time.perf_counter()
        if self.state == CalState.STABILIZE and self._phase_start_t is not None:
            self._phase_start_t = now
        elif self.state == CalState.SWEEP and self._step_settle_deadline is not None:
            self._step_settle_deadline = now + self.STEP_SETTLE_TIMEOUT_S
        logger.warning(f"Growing pitch oscillation detected (VS amplitudes {grown} m/s); "
                       f"pitch gains backed off to x{new_scale:.2f}")

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
            self._trace_axis_y = round(u_elev * y_scale, 5)
        elif self.ac._sim_is_xplane():
            self.ac.send_xp_command(
                f"AXIS:jx={round(u_ail * x_scale, 5)},jy={round(u_elev * y_scale, 5)}"
            )
            self._trace_axis_y = round(u_elev * y_scale, 5)
        elif self.ac._sim_is_msfs():
            x_var, x_range = self.ac._get_msfs_axis_config('x', "AXIS_AILERONS_SET")
            y_var, y_range = self.ac._get_msfs_axis_config('y', "AXIS_ELEVATOR_SET")
            self.ac._send_msfs_axis_value(x_var, u_ail, x_range, x_scale)
            self._trace_axis_y = self.ac._send_msfs_axis_value(y_var, u_elev, y_range, y_scale)

        self._hold_stick_centered()

    def _hold_stick_centered(self):
        try:
            if self._hold_offs is None:
                # Pin the stick where the HAND is, not at the old spring
                # center: users start runs while holding the stick deflected
                # against an out-of-trim spring (holding level by force), and
                # pinning at the trim-following center stepped a full-strength
                # spring in at an offset away from the hand — a yank at
                # engagement and a snap across the gap when they let go.
                try:
                    px, py = self.ac._get_device_raw_axes()
                    self._hold_offs = (int(clamp(px, -1, 1) * self.CPOFFSET_RANGE),
                                       int(clamp(py, -1, 1) * self.CPOFFSET_RANGE))
                except Exception:
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

        ``pct`` is in ElevTrimPct read-back space.

        MSFS — the DIRECT method (default) writes the ELEVATOR TRIM POSITION
        SimVar, mapping pct per-side through the reported travel limits (like
        the trimwheel's direct mode). Direct is self-referential — it writes
        the read-back's own quantity — so a hold-in-place command is a true
        no-op on any aircraft. The AXIS method (debug-mode selection, and the
        fallback when no usable travel limits are reported) sends
        AXIS_ELEV_TRIM_SET and assumes the sim maps the event value 1:1 onto
        the read-back; field testing found aircraft that mishandle the event
        (Just Flight Arrow III/IV and Hawk T1 apply it raw/32766 UNNEGATED,
        relocating trim to -cmd/2 on every write — measured in
        trimcal_trace_20260716_123259), which is why direct is primary. The
        event value is sign-inverted relative to the SimVar read-backs (see
        the trimwheel mixin's ``pos_y_pos = -int(pos_y_pos)``), hence the
        negation. ``_trim_sign`` is a runtime correction on top of either
        method, flipped by :meth:`_advance_trim` for aircraft with inverted
        trim response (cf. the ``trimwheel_axis_invert`` user setting).
        """
        pct = clamp(pct * self._trim_sign, -1.0, 1.0)
        if self.ac._sim_is_msfs():
            limits = None
            telem = getattr(self.ac, "_telem_data", None)
            if self.trim_write_method == "direct":
                get_limits = getattr(self.ac, "_trimwheel_trim_limits", None)
                if get_limits is not None and telem is not None:
                    limits = get_limits(telem)
            if limits is not None:
                # The pct->degrees scale is MEASURED from the live telemetry
                # pair (ElevTrim degrees / ElevTrimPct) whenever the trim sits
                # far enough from center, per side — the reported travel
                # limits are only the fallback. Field case (G-111 Albatross):
                # reported travel -6.5..+12.0 deg but the read-back pct is
                # normalized by the UP limit on BOTH sides, so limits-based
                # down-side writes landed 46% short — the very first
                # hold-in-place write RELOCATED the trim by half its value
                # (the self-referential no-op only holds when write and
                # read-back agree on the scale), and the overdrive loop's
                # bound could not span the deficit deep in the band.
                if telem is not None:
                    p = telem.ElevTrimPct
                    d = telem.ElevTrim
                    if p is not None and d is not None and abs(p) >= 0.04:
                        r = d / p
                        if 0.5 <= r <= 50.0:
                            self._trim_deg_per_pct[1 if p >= 0 else -1] = r
                dn, up = limits
                side = 1 if pct >= 0 else -1
                fallback = up if side > 0 else abs(dn)
                ratio = self._trim_deg_per_pct.get(side) \
                    or self._trim_deg_per_pct.get(-side) or fallback
                if not self._trim_ratio_logged and \
                        abs(ratio - fallback) > 0.05 * fallback:
                    self._trim_ratio_logged = True
                    logger.info(f"Trim pct->deg scale measured from telemetry: "
                                f"{ratio:+.2f} deg/unit (reported travel implies "
                                f"{fallback:+.2f}) — using the measured scale")
                self._log_trim_method("direct ELEVATOR TRIM POSITION (travel "
                                      f"{limits[0]:+.1f}..{limits[1]:+.1f} deg)")
                deg = pct * ratio
                self._trace_trim_write = round(deg, 4)
                self.ac._simconnect.set_simdatum_to_msfs(
                    "ELEVATOR TRIM POSITION", math.radians(deg), units="radians"
                )
                return
            self._log_trim_method(
                "AXIS_ELEV_TRIM_SET (debug selection)"
                if self.trim_write_method == "axis"
                else "AXIS_ELEV_TRIM_SET (no usable trim-limit telemetry for direct)")
            out = -int(pct * self.TRIM_AXIS_RANGE)
            self._trace_trim_write = out
            self.ac._simconnect.send_event_to_msfs("AXIS_ELEV_TRIM_SET", out)
        elif self.ac._sim_is_xplane():
            # Write the pilot-control-level trim (float ratio -1..1); the FM
            # propagates it to sim/flightmodel2/controls/elevator_trim, which
            # is what the plugin exports as the ElevTrimPct read-back. Same
            # sign convention both ways, so no MSFS-style negation here.
            # Aircraft with custom trim systems that swallow this write are
            # caught by the read-back verification in _advance_trim.
            self._trace_trim_write = round(pct, 5)
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
            # Only touch trim if we actually moved it (neutralization or the
            # sweep); otherwise trim0 is still its default and untouched by us.
            # Ending from the assistant's hold keeps the CURRENT trim: it is
            # the level trim for the power the user chose, and snapping back
            # to the pre-assist trim would hand back an out-of-trim aircraft.
            if self._trim_touched and self.state != CalState.ASSIST_HOLD:
                self._set_trim(self._trim0)
        except Exception as e:
            logger.debug(f"trim restore skipped: {e}")
        self._set_trimwheel_hold(False)
        self._set_state(end_state)
        self._active = False
        self._dump_trace()

    def _set_trimwheel_hold(self, active):
        """Mute/unmute trimwheel instances' sim writes while we own the trim.

        Sets the local deadline directly (covers a trimwheel on this instance)
        and broadcasts to children over IPC. The hold self-expires after
        TRIMWHEEL_HOLD_TTL_S without a refresh, and the trimwheel re-syncs its
        wheel position before resuming writes (see MsfsXpTrimwheelMixIn).
        """
        G.trimcal_hold_until = \
            (time.perf_counter() + self.TRIMWHEEL_HOLD_TTL_S) if active else 0.0
        ipc = getattr(G, "ipc_instance", None)
        if ipc is not None:
            try:
                ipc.send_broadcast_message(f"TRIMCAL HOLD:{1 if active else 0}")
            except Exception as e:
                logger.debug(f"trimwheel hold broadcast failed: {e}")
