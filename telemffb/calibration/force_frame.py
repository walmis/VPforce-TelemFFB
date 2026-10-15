"""Force direction calibration for a DirectInput device.

``ForceFrameCalibrator`` measures how a device renders the forces it is
sent and derives the ``ForceFrame`` (per-axis mirrors plus a quadrant
rotation) under which they match the DirectInput convention: a spring
with a positive center offset settles on the positive side of the axis,
and a constant force at 0 degrees pushes the stick aft (+Y), at 90 left,
at 180 forward, at 270 right.

The calibrator is a pure state machine.  It talks to the hardware through
``CalibrationDevice`` and is stepped by ``advance(now)`` from whatever
loop the caller has, so it is testable against a simulated base.

Sequence:

1. Input check.  A moderate centering spring holds the stick at center
   (a zero-offset spring reads the same in every frame); the user pushes
   forward, then right, and lets go.  The reported movement must follow;
   a reversed reading is an axis-inversion problem that the force frame
   cannot correct, so the run stops there.  A stick that the spring
   drives to a stop instead is a base the frame cannot describe.
2. Spring per axis.  With the standard frame, a spring whose center is at
   +0.4 pulls the stick to +0.4 on a standard axis and to -0.4 on a
   mirrored one.  A stick that runs to its stop, or that moves on the
   other axis, is a device the frame cannot describe.
3. Direction.  Against a light centering spring, a constant force at 0
   then at 90 degrees deflects the stick; the deflection angles give the
   device's rotation and whether its directions are reflected.  The
   reflection must agree with the springs' mirrors.
4. Verification.  With the derived frame in force, pushes at 180 and
   270 must deflect the stick forward and right.  The input check already
   tied the reported position to the physical stick, so this is the
   physical check.

Every stimulus ramps up over half a second rather than stepping on.
Every automatic step is time-boxed and retried once with a firmer
stimulus.  However a run ends, the stick is first brought back to center
under a spring; then every effect stops and the stored frame is restored.
"""
import enum
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple

from telemffb.hw.ffb_dinput import ForceFrame

Axes = Tuple[float, float]


@dataclass(frozen=True)
class AxisSpring:
    """One axis of a spring condition, in normalized units."""
    offset: float
    coefficient: float
    saturation: float


class CalibrationDevice(Protocol):
    """What the calibrator needs from a device.  All values normalized."""

    def read_axes(self) -> Optional[Axes]:
        """The reported (x, y) position in -1..1, or None with no input."""

    def set_frame(self, frame: Optional[ForceFrame]) -> None:
        """Render through ``frame`` instead of the stored one; None
        restores the stored frame."""

    def spring(self, x: Optional[AxisSpring], y: Optional[AxisSpring]) -> None:
        """Play one spring with these per-axis blocks (None: no force on
        that axis)."""

    def constant(self, magnitude: float, direction_deg: int) -> None:
        """Play one constant force; magnitude 0 stops it."""

    def stop_all(self) -> None:
        """Stop every effect the calibration plays."""

    def release(self) -> None:
        """Free every effect the calibration created."""


class Step(enum.Enum):
    IDLE = "idle"
    INPUT_FORWARD = "input_forward"
    INPUT_RIGHT = "input_right"
    SPRING_X = "spring_x"
    SPRING_Y = "spring_y"
    DIRECTION_0 = "direction_0"
    DIRECTION_90 = "direction_90"
    VERIFY_180 = "verify_180"
    VERIFY_270 = "verify_270"
    #: the run is over; the stick is being centered before the effects stop
    PARKING = "parking"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Stage(enum.Enum):
    """The user-facing grouping of the steps."""
    INPUT = "Input check"
    SPRINGS = "Axis springs"
    DIRECTIONS = "Force directions"
    VERIFY = "Verification"


STAGE_OF_STEP = {
    Step.INPUT_FORWARD: Stage.INPUT, Step.INPUT_RIGHT: Stage.INPUT,
    Step.SPRING_X: Stage.SPRINGS, Step.SPRING_Y: Stage.SPRINGS,
    Step.DIRECTION_0: Stage.DIRECTIONS, Step.DIRECTION_90: Stage.DIRECTIONS,
    Step.VERIFY_180: Stage.VERIFY, Step.VERIFY_270: Stage.VERIFY,
}

STEP_ORDER = (Step.INPUT_FORWARD, Step.INPUT_RIGHT, Step.SPRING_X,
              Step.SPRING_Y, Step.DIRECTION_0, Step.DIRECTION_90,
              Step.VERIFY_180, Step.VERIFY_270)

#: physical direction a push at each quadrant angle moves the stick
DIRECTION_NAMES = {0: "aft", 90: "left", 180: "forward", 270: "right"}

#: what the user reads when a run ends, by outcome: (headline, body).
#: Whole sentences, no values spliced in: the axis or direction a reader
#: needs goes in the calibrator's ``detail`` line.
OUTCOMES = {
    'done_standard': (
        "Detection complete: no correction required",
        "This device renders forces in the standard DirectInput format.  "
        "The force correction options have been configured accordingly."),
    'done_corrected': (
        "Detection complete: correction applied",
        "This device renders forces differently from the standard DirectInput "
        "format.  The force correction options have been configured "
        "accordingly; save the system settings to apply the changes."),
    'cancelled': (
        "Detection cancelled",
        "Nothing was changed."),
    'input_lost': (
        "Detection stopped",
        "The device stopped reporting its position."),
    'error': (
        "Detection stopped",
        "The detection hit an error; see the log."),
    'no_center': (
        "Detection stopped",
        "The stick did not settle at center under the centering spring."),
    'runaway': (
        "Detection stopped",
        "The centering spring drove the stick to its stop.  This device "
        "pushes away from a spring's center, which the force correction "
        "cannot describe."),
    'no_push': (
        "Detection stopped",
        "No push was seen."),
    'wrong_input': (
        "Detection stopped",
        "The device reported movement in a different direction than you were "
        "instructed to push, as listed below.  Reverse those axes in the "
        "device's software, and then run the detection again."),
    'swapped_input': (
        "Detection stopped",
        "The device reported the pushes on the other axis, as listed below.  "
        "Its X and Y axes are assigned the wrong way round; correct the axis "
        "assignment in the device's software, and then run the detection again."),
    'no_release': (
        "Detection stopped",
        "The stick did not return to center after the push, with the "
        "centering spring playing.  Check that the device renders forces, "
        "and keep your hands off it once you let go."),
    'cross_axis': (
        "Detection stopped",
        "A spring on one axis moved the other axis.  This device cannot be "
        "described by a mirror and a rotation."),
    'spring_stop': (
        "Detection stopped",
        "The test spring drove the stick to its stop.  This device pushes "
        "away from a spring's center, which the force correction cannot "
        "describe."),
    'spring_still': (
        "Detection stopped",
        "The stick did not move under the test spring.  Check that the "
        "device renders forces, and keep your hands off during the run."),
    'force_still': (
        "Detection stopped",
        "The stick did not move under the test force.  Check that the "
        "device renders forces, and keep your hands off during the run."),
    'held': (
        "Detection stopped",
        "The stick moved in a way the test cannot explain, or stayed put "
        "when it had moved under the earlier tests.  Keep your hands off "
        "the stick during the run, and run it again."),
    'force_unclear': (
        "Detection stopped",
        "The direction of the test force could not be read; the stick moved "
        "between two directions."),
    'no_turn': (
        "Detection stopped",
        "The device's force directions do not turn with the commanded "
        "direction, so no single correction fits."),
    'reflect_mismatch': (
        "Detection stopped",
        "The device reflects force directions differently from its axis "
        "springs, so no single correction fits."),
    'verify_wrong': (
        "Detection stopped",
        "With the correction applied, a test force moved the stick in the "
        "wrong direction.  Nothing was changed."),
}

#: what the user reads while a run is under way: (headline, body)
PROMPTS = {
    'center': (
        "Center the stick",
        "Let the stick settle at center under the spring, then take your "
        "hand off it."),
    'push_forward': (
        "Push the stick forward",
        "Push it fully forward and hold it there until the direction has "
        "been read."),
    'push_right': (
        "Push the stick right",
        "Push it fully to the right and hold it there until the direction "
        "has been read."),
    'release': (
        "Let go of the stick",
        "Let go and let the spring return the stick to center."),
    'measuring': (
        "Measuring",
        "Keep your hands off the stick."),
}


class _Phase(enum.Enum):
    WAIT_CENTER = "wait_center"
    WAIT_PUSH = "wait_push"
    WAIT_RELEASE = "wait_release"
    CENTER = "center"
    ACT = "act"
    PARK = "park"


def push_angle(dx: float, dy: float) -> float:
    """The direction, clockwise from aft in degrees, of a displacement
    (dx, dy): aft (+Y) is 0, left (-X) is 90."""
    return math.degrees(math.atan2(-dx, dy)) % 360.0


def snap_quadrant(angle: float, tolerance: float) -> Optional[int]:
    """The multiple of 90 nearest ``angle`` when within ``tolerance``
    degrees of it, else None."""
    nearest = int(round(angle / 90.0)) * 90 % 360
    off = abs((angle - nearest + 180.0) % 360.0 - 180.0)
    return nearest if off <= tolerance else None


def derive_frame(mirror_x: bool, mirror_y: bool, psi_0: int, psi_90: int
                 ) -> Tuple[Optional[ForceFrame], str]:
    """The frame under which a device that mirrors the given axes and
    pushes at ``psi_0`` for a 0 degree force and ``psi_90`` for 90
    renders standard directions; or (None, the OUTCOMES key saying why
    not)."""
    delta = (psi_90 - psi_0) % 360
    if delta == 90:
        sense = 1
    elif delta == 270:
        sense = -1
    else:
        return None, 'no_turn'
    frame_sense = -1 if mirror_x != mirror_y else 1
    if frame_sense != sense:
        return None, 'reflect_mismatch'
    base = (-sense * psi_0) % 360
    rotate = (base - (180 if mirror_y else 0)) % 360
    return ForceFrame(mirror_x, mirror_y, rotate), ""


class ForceFrameCalibrator:
    """Drives one calibration run; see the module docstring for the plan.

    Call ``start(now)``, then ``advance(now)`` at a steady rate (50 Hz is
    plenty) until ``finished``; ``cancel(now)`` at any time (with ``now``
    the stick is centered first, over a few more ticks; without, the
    effects stop at once).  ``result`` holds the frame after a successful
    run, ``failure`` the reason otherwise.
    """

    #: sampling window the settle test looks at, seconds
    WINDOW_S = 0.5
    #: a stimulus rises from nothing to its value over this long
    RAMP_S = 0.5
    #: a stimulus acts at full value at least this long before the stick
    #: counts as settled
    SETTLE_MIN_S = 0.6
    #: position range inside the window that counts as at rest
    SETTLE_RANGE = 0.03
    #: an automatic phase gives up waiting for rest after this long and
    #: evaluates the window's mean instead
    PHASE_TIMEOUT_S = 4.5
    #: the end-of-run centering gives up waiting for rest after this long
    PARK_TIMEOUT_S = 2.5
    #: the user has this long to push
    INPUT_TIMEOUT_S = 30.0
    #: the centering spring has this long to bring a released stick back
    RELEASE_TIMEOUT_S = 8.0
    #: no position report for this long is a dead input
    INPUT_LOST_S = 2.0
    #: a reported excursion this large is the user's push
    PUSH_EXCURSION = 0.6
    #: the stick is back at rest below this
    RELEASED_BELOW = 0.3
    #: a stick held at a stop this long while the centering spring plays
    #: is being pushed there by the spring
    RUNAWAY_S = 1.5
    #: a stick that moved less than this did not move
    MOVE_MIN = 0.12
    #: a stick this far out ran to its stop
    AT_STOP = 0.9
    #: the centering spring must bring the stick this close to center
    #: before a test; farther out means something is holding it
    CENTERED_WITHIN = 0.25
    #: a spring centered at +/-0.4 cannot put the stick beyond this
    SPRING_REACH = 0.55
    #: the other axis moving this much under a one-axis spring is a hand
    INTERFERENCE = 0.3
    #: a deflection angle this far from a quadrant is unreadable
    QUADRANT_TOLERANCE_DEG = 25.0
    #: the position trace kept for the log, seconds between samples
    TRACE_PERIOD_S = 0.1

    CENTER_SPRING = AxisSpring(0.0, 0.5, 0.4)
    DIRECTION_SPRING = AxisSpring(0.0, 0.5, 0.6)
    #: the mirror test spring, first try and retry
    MIRROR_SPRINGS = (AxisSpring(0.4, 0.6, 0.3), AxisSpring(0.4, 1.0, 0.5))
    #: the constant force, first try and retry
    DIRECTION_FORCES = (0.3, 0.45)

    def __init__(self, device: CalibrationDevice):
        self.device = device
        self.step = Step.IDLE
        self.headline = ""
        self.prompt = ""
        self.detail = ""
        self.result: Optional[ForceFrame] = None
        self.failure = ""
        self.position: Optional[Axes] = None
        self.rest: Optional[Axes] = None
        self.mirror_x: Optional[bool] = None
        self.mirror_y: Optional[bool] = None
        self.psi_0: Optional[int] = None
        self.psi_90: Optional[int] = None
        self.candidate: Optional[ForceFrame] = None
        #: the input check's pushes: direction asked for -> direction read
        self.input_reads: Dict[int, int] = {}
        self._phase: Optional[_Phase] = None
        self._phase_start = 0.0
        self._attempt = 0
        self._samples: List[Tuple[float, Axes]] = []
        self._last_input = 0.0
        self._release_since: Optional[float] = None
        self._at_stop_since: Optional[float] = None
        self._frame_set: Optional[ForceFrame] = None
        self._released = False
        self._failed_stage: Optional[Stage] = None
        self._pending_terminal: Optional[Step] = None
        self._failed_outcome = 'error'
        self._outcome_detail = ""
        self._settled = True            # how the last reading was reached
        self._moved_once = False        # the device has rendered a force so far
        self._trace: List[Tuple[float, str, float, float]] = []
        self._trace_at = 0.0
        self._t0: Optional[float] = None
        # the stimuli in force and the ramp that brings them there
        self._spring: Tuple[Optional[AxisSpring], Optional[AxisSpring]] = (None, None)
        self._constant: Tuple[float, int] = (0.0, 0)
        self._constant_from = 0.0
        self._constant_sent = 0.0
        self._spring_ramp_start = 0.0
        self._constant_ramp_start = 0.0
        self._ramping: set = set()

    # ------------------------------------------------------------------
    # public surface

    @property
    def finished(self) -> bool:
        return self.step in (Step.DONE, Step.FAILED, Step.CANCELLED)

    @property
    def running(self) -> bool:
        return self.step is not Step.IDLE and not self.finished

    @property
    def parking(self) -> bool:
        return self.step is Step.PARKING

    @property
    def stage(self) -> Optional[Stage]:
        return STAGE_OF_STEP.get(self.step)

    @property
    def awaiting_center(self) -> bool:
        """True while an input-check step waits for the stick at center."""
        return self._phase is _Phase.WAIT_CENTER

    @property
    def awaiting_push(self) -> bool:
        """True while an input-check step waits for the user's push."""
        return self._phase is _Phase.WAIT_PUSH

    def stage_states(self) -> Dict[Stage, str]:
        """Each stage as 'pending', 'active', 'done' or 'failed'."""
        states = {}
        step = self._pending_terminal if self.step is Step.PARKING else self.step
        current = STAGE_OF_STEP.get(step)
        order = [Stage.INPUT, Stage.SPRINGS, Stage.DIRECTIONS, Stage.VERIFY]
        if step is Step.DONE:
            return {s: 'done' for s in order}
        failed_at = self._failed_stage if step is Step.FAILED else None
        for s in order:
            if failed_at is not None:
                states[s] = ('failed' if s is failed_at else
                             'done' if order.index(s) < order.index(failed_at)
                             else 'pending')
            elif current is None:
                states[s] = 'pending'
            elif s is current:
                states[s] = 'active'
            else:
                states[s] = ('done' if order.index(s) < order.index(current)
                             else 'pending')
        return states

    def start(self, now: float):
        if self.step is not Step.IDLE:
            return
        self._last_input = now
        self._enter(Step.INPUT_FORWARD, now)

    def cancel(self, now: Optional[float] = None):
        """End the run.  With ``now`` the stick is centered first and the
        caller keeps ticking ``advance`` until ``finished``; without, or
        when called again while centering, everything stops at once."""
        if not self.running:
            return
        if self.parking:
            self._complete()
            return
        self._finish(Step.CANCELLED, now)

    def advance(self, now: float):
        """One tick: read the position and move the run along."""
        if self.finished or self.step is Step.IDLE:
            return
        try:
            axes = self.device.read_axes()
            if axes is None:
                if now - self._last_input > self.INPUT_LOST_S:
                    # no reading to center against: stop at once
                    self._fail('input_lost')
                return
            self._last_input = now
            self.position = axes
            if self._t0 is None:
                self._t0 = now
            if now - self._trace_at >= self.TRACE_PERIOD_S:
                self._trace_at = now
                self._trace.append((now - self._t0, self.step.value, axes[0], axes[1]))
            self._samples.append((now, axes))
            while self._samples and now - self._samples[0][0] > self.WINDOW_S:
                self._samples.pop(0)
            self._drive(now)
            self._HANDLERS[self.step](self, now, axes)
        except Exception:
            logging.exception("force frame calibration faulted")
            self._fail('error', now)

    # ------------------------------------------------------------------
    # step plumbing

    def _enter(self, step: Step, now: float):
        self.step = step
        self._attempt = 0
        self.detail = ""
        self._release_since = None
        self._at_stop_since = None
        if step in (Step.INPUT_FORWARD, Step.INPUT_RIGHT):
            self._set_frame(ForceFrame())
            self._ramp_spring(self.CENTER_SPRING, self.CENTER_SPRING, now)
            self._set_phase(_Phase.WAIT_CENTER, now)
            self._say('center')
        elif step in (Step.SPRING_X, Step.SPRING_Y, Step.DIRECTION_0,
                      Step.DIRECTION_90, Step.VERIFY_180, Step.VERIFY_270):
            self._say('measuring')
            self._begin_center(now)
        elif step is Step.DONE:
            self._finish(Step.DONE, now)

    def _next_step(self):
        if self.detail:
            logging.info("Force frame calibration, %s: %s" % (self.step.value, self.detail))
        index = STEP_ORDER.index(self.step)
        now = self._samples[-1][0] if self._samples else self._phase_start
        if index + 1 < len(STEP_ORDER):
            self._enter(STEP_ORDER[index + 1], now)
        else:
            self._enter(Step.DONE, now)

    def _say(self, key: str):
        self.headline, self.prompt = PROMPTS[key]

    def _set_phase(self, phase: _Phase, now: float):
        self._phase = phase
        self._phase_start = now
        self._samples.clear()

    def _set_frame(self, frame: ForceFrame):
        if frame != self._frame_set:
            self.device.set_frame(frame)
            self._frame_set = frame

    def _fail(self, outcome: str, now: Optional[float] = None, detail: str = ""):
        """End the run with ``OUTCOMES[outcome]`` as its verdict and
        ``detail`` as the one line of data under it."""
        if self.parking:
            # the centering itself went wrong: stop where it is
            self._complete()
            return
        self._failed_stage = STAGE_OF_STEP.get(self.step)
        self.failure = OUTCOMES[outcome][1]
        self._failed_outcome = outcome
        self._outcome_detail = detail
        self._finish(Step.FAILED, now)

    def _finish(self, step: Step, now: Optional[float]):
        """The run is over with outcome ``step``.  With a ``now`` the
        stick is centered first (``PARKING``); without one (no caller
        left to tick) the effects stop at once."""
        self._pending_terminal = step
        if step is Step.DONE:
            self.result = self.candidate
            if self.candidate.is_identity:
                self.headline, self.prompt = OUTCOMES['done_standard']
                self._outcome_detail = ""
            else:
                self.headline, self.prompt = OUTCOMES['done_corrected']
                self._outcome_detail = "Correction: %s" % self.candidate.describe()
        elif step is Step.FAILED:
            self.headline, self.prompt = OUTCOMES[self._failed_outcome]
        else:
            self.headline, self.prompt = OUTCOMES['cancelled']
            self._outcome_detail = ""
        if now is None:
            self._complete()
            return
        self.step = Step.PARKING
        self.detail = "Centering the stick"
        try:
            self._set_frame(ForceFrame())
            self._ramp_constant(0.0, now)
            # a centering spring already playing stays; only an offset
            # test spring, or none, is replaced
            x, y = self._spring
            if not (x and y and x.offset == 0 and y.offset == 0):
                self._ramp_spring(self.CENTER_SPRING, self.CENTER_SPRING, now)
            self._set_phase(_Phase.PARK, now)
        except Exception:
            logging.exception("force frame calibration could not center the stick")
            self._complete()

    def _complete(self):
        self.step = self._pending_terminal
        self._phase = None
        self.detail = self._outcome_detail
        logging.info("Force frame calibration %s: %s. %s%s" % (
            self.step.value, self.headline, self.prompt,
            (" (%s)" % self.detail) if self.detail else ""))
        # the position trace: at INFO when there is something to explain
        # (a failure, or a correction), DEBUG for a clean standard result
        explain = (self.step is Step.FAILED
                   or (self.result is not None and not self.result.is_identity))
        level = logging.INFO if explain else logging.DEBUG
        if self._trace and logging.getLogger().isEnabledFor(level):
            lines = ["%6.1f %-12s %+.3f %+.3f" % row for row in self._trace]
            logging.log(level, "Force frame calibration position trace (s, step, x, y):"
                        + "".join(chr(10) + "    " + line for line in lines))
        self._cleanup()

    def _on_parking(self, now: float, axes: Axes):
        if self._reading(now) is not None \
                or now - self._phase_start >= self.PARK_TIMEOUT_S:
            self._complete()

    def _cleanup(self):
        if self._released:
            return
        self._released = True
        for call in (self.device.stop_all,
                     lambda: self.device.set_frame(None),
                     self.device.release):
            try:
                call()
            except Exception:
                logging.exception("force frame calibration cleanup failed")

    # ------------------------------------------------------------------
    # settle test

    def _window_range(self) -> float:
        xs = [a[0] for _, a in self._samples]
        ys = [a[1] for _, a in self._samples]
        return max(max(xs) - min(xs), max(ys) - min(ys))

    def _window_mean(self) -> Axes:
        n = len(self._samples)
        return (sum(a[0] for _, a in self._samples) / n,
                sum(a[1] for _, a in self._samples) / n)

    def _reading(self, now: float) -> Optional[Axes]:
        """The settled position once the stick is at rest, the window's
        mean when the phase times out, else None (still waiting)."""
        elapsed = now - self._phase_start
        if elapsed >= self.RAMP_S + self.SETTLE_MIN_S and len(self._samples) >= 3 \
                and self._window_range() < self.SETTLE_RANGE:
            self._settled = True
            return self._samples[-1][1]
        if elapsed >= self.PHASE_TIMEOUT_S and self._samples:
            self.detail = "The stick did not come fully to rest"
            self._settled = False
            return self._window_mean()
        return None

    def _log_measurement(self, reading: Axes):
        """One log line with everything a step's verdict rested on."""
        x, y = self._spring
        spring = "spring x=%s y=%s" % (
            "-" if x is None else "(%.2f,%.2f,%.2f)" % (x.offset, x.coefficient, x.saturation),
            "-" if y is None else "(%.2f,%.2f,%.2f)" % (y.offset, y.coefficient, y.saturation))
        magnitude, direction = self._constant
        constant = "constant %.2f@%d" % (magnitude, direction) if magnitude else "constant -"
        frame = self._frame_set.describe() if self._frame_set is not None else "stored"
        rest = self.rest or (0.0, 0.0)
        logging.info(
            "Force frame calibration, %s try %d [frame %s; %s; %s]: rest (%.3f, %.3f) "
            "-> %s (%.3f, %.3f), moved (%+.3f, %+.3f) at %.0f deg, window range %.3f"
            % (self.step.value, self._attempt + 1, frame, spring, constant,
               rest[0], rest[1], "settled" if self._settled else "TIMED OUT, mean",
               reading[0], reading[1], reading[0] - rest[0], reading[1] - rest[1],
               push_angle(reading[0] - rest[0], reading[1] - rest[1]),
               self._window_range() if self._samples else 0.0))

    # ------------------------------------------------------------------
    # input check

    def _on_input(self, now: float, axes: Axes):
        expect_x = self.step is Step.INPUT_RIGHT
        if self._phase is _Phase.WAIT_CENTER:
            if now - self._phase_start > self.INPUT_TIMEOUT_S:
                self._fail('no_center', now)
                return
            if max(abs(axes[0]), abs(axes[1])) >= self.AT_STOP:
                if self._at_stop_since is None:
                    self._at_stop_since = now
                elif now - self._at_stop_since >= self.RUNAWAY_S:
                    # a centering spring is what drove it there: no parking
                    self._fail('runaway')
                    return
            else:
                self._at_stop_since = None
            if max(abs(axes[0]), abs(axes[1])) < self.RELEASED_BELOW:
                # the push is judged from where the stick settled
                self.rest = axes
                self._set_phase(_Phase.WAIT_PUSH, now)
                self._say('push_right' if expect_x else 'push_forward')
            return
        if self._phase is _Phase.WAIT_PUSH:
            if now - self._phase_start > self.INPUT_TIMEOUT_S:
                self._fail('no_push', now)
                return
            x = axes[0] - self.rest[0]
            y = axes[1] - self.rest[1]
            if max(abs(x), abs(y)) < self.PUSH_EXCURSION:
                return
            if abs(x) >= abs(y):
                read = 270 if x > 0 else 90
            else:
                read = 0 if y > 0 else 180
            wanted = 270 if expect_x else 180
            # judged after both pushes, so one verdict names every axis
            self.input_reads[wanted] = read
            logging.info("Force frame calibration, %s: rest (%.3f, %.3f), push read "
                         "(%.3f, %.3f) = %s, asked for %s"
                         % (self.step.value, self.rest[0], self.rest[1], axes[0], axes[1],
                            DIRECTION_NAMES[read], DIRECTION_NAMES[wanted]))
            self._say('release')
            self.detail = "Read %s" % DIRECTION_NAMES[read]
            self._set_phase(_Phase.WAIT_RELEASE, now)
        elif self._phase is _Phase.WAIT_RELEASE:
            if now - self._phase_start > self.RELEASE_TIMEOUT_S:
                # the centering spring is already playing and did not work
                self._fail('no_release')
                return
            x, y = axes
            if max(abs(x), abs(y)) < self.RELEASED_BELOW:
                if self._release_since is None:
                    self._release_since = now
                elif now - self._release_since >= 0.3:
                    # the spring brought it back: the device renders forces
                    self._moved_once = True
                    wrong = [(wanted, read) for wanted, read in self.input_reads.items()
                             if wanted != read]
                    if self.step is Step.INPUT_RIGHT and wrong:
                        # a push read on the other axis is a swap, not a reversal
                        swapped = any((w in (0, 180)) != (r in (0, 180)) for w, r in wrong)
                        self._fail('swapped_input' if swapped else 'wrong_input', now,
                                   "; ".join("Pushed %s, read %s"
                                             % (DIRECTION_NAMES[w], DIRECTION_NAMES[r])
                                             for w, r in wrong))
                        return
                    self._next_step()
            else:
                self._release_since = None

    # ------------------------------------------------------------------
    # stimuli: every change ramps in over RAMP_S

    def _ramp_spring(self, x: Optional[AxisSpring], y: Optional[AxisSpring],
                     now: float):
        """Bring the spring to these blocks, its stiffness rising from
        nothing (the centers switch at once).  The blocks already in
        force are left alone: a spring is never dropped to nothing while
        a constant force may still be playing against it."""
        if (x, y) == self._spring and 'spring' not in self._ramping:
            return
        self._spring = (x, y)
        self._ramping.add('spring')
        self._spring_ramp_start = now
        self._drive(now)

    def _ramp_constant(self, magnitude: float, now: float,
                       direction_deg: Optional[int] = None):
        """Bring the constant force to ``magnitude`` from the value being
        sent now.  A fade to nothing keeps the direction it was playing."""
        if direction_deg is None:
            direction_deg = self._constant[1]
        self._constant_from = self._constant_sent
        self._constant = (magnitude, direction_deg)
        self._ramping.add('constant')
        self._constant_ramp_start = now
        self._drive(now)

    @staticmethod
    def _scaled(block: Optional[AxisSpring], fraction: float) -> Optional[AxisSpring]:
        if block is None:
            return None
        return AxisSpring(block.offset, block.coefficient * fraction, block.saturation)

    def _drive(self, now: float):
        """Send the stimuli at this instant of their ramp; a no-op once
        they are at their values."""
        if not self._ramping:
            return
        if 'spring' in self._ramping:
            fraction = min(1.0, max(0.0, (now - self._spring_ramp_start) / self.RAMP_S))
            x, y = self._spring
            self.device.spring(self._scaled(x, fraction), self._scaled(y, fraction))
            if fraction >= 1.0:
                self._ramping.discard('spring')
        if 'constant' in self._ramping:
            fraction = min(1.0, max(0.0, (now - self._constant_ramp_start) / self.RAMP_S))
            target, direction = self._constant
            magnitude = self._constant_from + (target - self._constant_from) * fraction
            self._constant_sent = magnitude
            self.device.constant(magnitude, direction)
            if fraction >= 1.0:
                self._ramping.discard('constant')

    # ------------------------------------------------------------------
    # automatic steps: center, then act

    def _begin_center(self, now: float):
        frame = self.candidate if self.step in (Step.VERIFY_180, Step.VERIFY_270) \
            else ForceFrame()
        self._set_frame(frame)
        spring = (self.DIRECTION_SPRING if self.step not in (Step.SPRING_X, Step.SPRING_Y)
                  else self.CENTER_SPRING)
        self._set_phase(_Phase.CENTER, now)
        self._ramp_constant(0.0, now)
        self._ramp_spring(spring, spring, now)

    def _begin_act(self, now: float):
        self.rest = self.position
        step = self.step
        self._set_phase(_Phase.ACT, now)
        if step in (Step.SPRING_X, Step.SPRING_Y):
            test = self.MIRROR_SPRINGS[min(self._attempt, 1)]
            if step is Step.SPRING_X:
                self._ramp_spring(test, None, now)
            else:
                self._ramp_spring(None, test, now)
        else:
            force = self.DIRECTION_FORCES[min(self._attempt, 1)]
            self._ramp_constant(force, now, self._act_angle())

    def _act_angle(self) -> int:
        return {Step.DIRECTION_0: 0, Step.DIRECTION_90: 90,
                Step.VERIFY_180: 180, Step.VERIFY_270: 270}[self.step]

    def _retry_or_fail(self, now: float, outcome: str, detail: str = "") -> None:
        if self._attempt == 0:
            self._attempt = 1
            self.detail = "Retrying with a firmer force"
            self._begin_center(now)
        else:
            self._fail(outcome, now, detail)

    def _on_auto(self, now: float, axes: Axes):
        if self._phase is _Phase.CENTER:
            reading = self._reading(now)
            if reading is None:
                return
            if max(abs(reading[0]), abs(reading[1])) > self.CENTERED_WITHIN:
                self._log_measurement(reading)
                self._retry_or_fail(now, 'held', "not at center before the test")
                return
            self._begin_act(now)
            return
        if self._phase is _Phase.ACT:
            reading = self._reading(now)
            if reading is None:
                return
            if self.step in (Step.SPRING_X, Step.SPRING_Y):
                self._evaluate_spring(now, reading)
            else:
                self._evaluate_direction(now, reading)

    def _evaluate_spring(self, now: float, reading: Axes):
        axis = 0 if self.step is Step.SPRING_X else 1
        other = 1 - axis
        name, other_name = ("X", "Y") if axis == 0 else ("Y", "X")
        p = reading[axis]
        moved = abs(p - self.rest[axis])
        moved_other = abs(reading[other] - self.rest[other])
        self._log_measurement(reading)
        if moved_other > self.MOVE_MIN and moved_other > moved:
            self._fail('cross_axis', now,
                       "%s spring moved the %s axis" % (name, other_name))
            return
        if abs(p) >= self.AT_STOP:
            self._fail('spring_stop', now, "%s spring" % name)
            return
        if moved < self.MOVE_MIN:
            self._retry_or_fail(now, 'held' if self._moved_once else 'spring_still',
                                "%s spring" % name)
            return
        if abs(p) > self.SPRING_REACH or moved_other > self.INTERFERENCE:
            # beyond where the spring could put it, or the free axis moved:
            # something other than the spring moved the stick
            self._retry_or_fail(now, 'held', "%s spring" % name)
            return
        self._moved_once = True
        mirrored = p < self.rest[axis]
        if axis == 0:
            self.mirror_x = mirrored
        else:
            self.mirror_y = mirrored
        self.detail = "%s axis: %s" % (name, "mirrored" if mirrored else "standard")
        self._next_step()

    def _evaluate_direction(self, now: float, reading: Axes):
        dx = reading[0] - self.rest[0]
        dy = reading[1] - self.rest[1]
        self._log_measurement(reading)
        if math.hypot(dx, dy) < self.MOVE_MIN:
            self._retry_or_fail(now, 'held' if self._moved_once else 'force_still')
            return
        self._moved_once = True
        angle = push_angle(dx, dy)
        quadrant = snap_quadrant(angle, self.QUADRANT_TOLERANCE_DEG)
        if quadrant is None:
            self._retry_or_fail(now, 'force_unclear',
                                "Stick moved at %.0f\u00b0" % angle)
            return
        if self.step is Step.DIRECTION_0:
            self.psi_0 = quadrant
            self.detail = "0\u00b0 force: pushed %s" % DIRECTION_NAMES[quadrant]
            self._next_step()
        elif self.step is Step.DIRECTION_90:
            self.psi_90 = quadrant
            frame, outcome = derive_frame(self.mirror_x, self.mirror_y,
                                          self.psi_0, self.psi_90)
            if frame is None:
                self._fail(outcome, now, "90\u00b0 force: pushed %s"
                           % DIRECTION_NAMES[quadrant])
                return
            self.candidate = frame
            self.detail = "Correction: %s" % frame.describe()
            self._next_step()
        else:
            expected = self._act_angle()
            if quadrant != expected:
                self._fail('verify_wrong', now, "%d\u00b0 force: expected %s, read %s" % (
                    expected, DIRECTION_NAMES[expected], DIRECTION_NAMES[quadrant]))
                return
            self.detail = "Verified: %d\u00b0 force pushed %s" % (
                expected, DIRECTION_NAMES[quadrant])
            self._next_step()

    _HANDLERS = {
        Step.INPUT_FORWARD: _on_input,
        Step.INPUT_RIGHT: _on_input,
        Step.SPRING_X: _on_auto,
        Step.SPRING_Y: _on_auto,
        Step.DIRECTION_0: _on_auto,
        Step.DIRECTION_90: _on_auto,
        Step.VERIFY_180: _on_auto,
        Step.VERIFY_270: _on_auto,
        Step.PARKING: _on_parking,
    }
