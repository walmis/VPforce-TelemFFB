"""Detection of a second writer on the sim's control-input axes.

A binding left mapped in the simulator's own controls competes with
TelemFFB for the axis.  What that looks like, in every capture of one, is
the other writer holding the axis while TelemFFB's writes get through now
and then - each one yanking the reported position a large step in a single
frame before the simulator's own input pulls it back.  That is the flop a
user sees on the controls, and it is what is counted here.

Everything a simulator does to an axis on its own moves the reported
position smoothly: reporting it scaled, a fraction of a frame early or
late, lagging behind with no hydraulic pressure, settling after an aircraft
loads.  None of it produces a jump the command did not make, which is why
this measures jumps rather than positions.
"""
from collections import deque
from typing import Dict, Optional, Tuple
import logging
import time


#: MSFS axis events the probe understands, and the axis each drives.
#:
#: Recording happens where the events are sent rather than in the effect
#: paths that raise them, so every path is covered by one interception -
#: fixed wing, fly-by-wire, the helicopter cyclic and collective, the trim
#: wheel - and a path added later is covered without being told to.
AXIS_EVENTS = {
    'AXIS_AILERONS_SET': 'aileron',
    'AXIS_ELEVATOR_SET': 'elevator',
    'AXIS_RUDDER_SET': 'rudder',
    'AXIS_ELEV_TRIM_SET': 'elev_trim',
    # Rotorcraft.  Which of these a helicopter uses is configurable -
    # the cyclic can be sent on the fixed-wing events instead - so both
    # spellings are mapped and both are provisional.
    'AXIS_CYCLIC_LATERAL_SET': 'cyclic_lat',
    'AXIS_CYCLIC_LONGITUDINAL_SET': 'cyclic_lon',
    'AXIS_COLLECTIVE_SET': 'collective',
    'ROTOR_AXIS_TAIL_ROTOR_SET': 'tail_rotor',
}

#: Every rotorcraft axis above is PROVISIONAL and none may convict.
#:
#: The cyclic reports on YOKE X POSITION LINEAR and YOKE Y POSITION, not
#: on AILERON/ELEVATOR POSITION.  Those sat flat at zero through a whole
#: helicopter flight on the H125 and H160, which is what an earlier
#: mapping onto them mistook for a conflict the size of the command
#: itself - 0.85 of full travel.  They are NOT flat on every rotorcraft:
#: the FlyInside B206 populates them identically to the yoke variables.
#: So neither reading may be assumed from the airframe type.
#:
#: Take the LINEAR variant for lateral.  The plain YOKE X POSITION
#: carries the simulator's non-linear response curve and read 0.0172
#: where the linear one read 0.0716 on the same parked cyclic, which
#: would stand as a permanent residual against a commanded value.
#:
#: The collective and tail rotor were measured against COLLECTIVE
#: POSITION and TAIL ROTOR PEDAL POSITION and fitted at R2 0.76 and
#: 0.91, on slopes of -0.69 and 0.86, trailing the command by four to
#: five frames, with a residual of 0.06 to 0.08 against a ten-thousandth
#: on a clean fixed-wing axis - reporting where the control ended up
#: rather than what was asked of it.  That measurement predates the
#: cyclic finding above and was taken under the assumption the cyclic
#: was unmeasurable, so re-check it before trusting either way.
#:
#: Confirming any of these is one flight with nothing bound: a correct
#: mapping reads near zero.  Promote it here only then.
#: Events whose reported position has been confirmed against a flight.
#:
#: The rest are measured and logged but never reach a verdict.  A mapping
#: onto the wrong reported variable, or one reading 0..1 where the command
#: is -1..1, leaves a large standing residual that is indistinguishable
#: from a second writer - which is precisely the false report this is meant
#: to detect rather than manufacture.  Confirming one is a single flight:
#: with nothing bound, a correct mapping reads near zero.
VERIFIED_AXIS_EVENTS = {
    'AXIS_AILERONS_SET',
    'AXIS_ELEVATOR_SET',
    'AXIS_RUDDER_SET',
    # Rotorcraft, confirmed on two helicopters from different
    # developers with engines running on the ground.  Every axis fitted
    # at zero lag with mean error between 0.00003 and 0.00301, against
    # a contention threshold of 0.005, over more than half of full
    # travel.  Not confirmed on a helicopter with an external flight
    # model - the FlyInside B206 is the obvious untested case.
    'AXIS_CYCLIC_LATERAL_SET',
    'AXIS_CYCLIC_LONGITUDINAL_SET',
    'AXIS_COLLECTIVE_SET',
    'ROTOR_AXIS_TAIL_ROTOR_SET',
}

#: The range those events carry, which is what makes interception at the
#: send possible: the value there is already scaled, and only a known range
#: turns it back into the -1..1 the reported position is in.  An axis
#: configured with a custom variable or range is deliberately absent from
#: the map above rather than guessed at - being uncovered is a gap, being
#: wrongly scaled would be a false finding.
AXIS_EVENT_RANGE = 16384.0

#: Last value sent on each axis, normalized, awaiting collection.  Written
#: at the send and taken by the probe, so an axis that went unsent this
#: frame offers nothing rather than a stale value.
_pending_commands: Dict[str, Tuple[float, bool]] = {}


def record_axis_command(event: str, value) -> None:
    """Note an axis event on its way to the simulator.

    Called from the send itself, so it must stay cheap and must never
    raise: a diagnostic has no business disturbing the telemetry path.
    """
    axis = AXIS_EVENTS.get(event)
    if axis is None:
        return
    try:
        # the send negates as it scales; undone here so the recorded value
        # is in the same sense and range as the reported position
        _pending_commands[axis] = (-float(value) / AXIS_EVENT_RANGE,
                                   event in VERIFIED_AXIS_EVENTS)
    except (TypeError, ValueError):
        pass


def take_axis_command(axis: str) -> Tuple[Optional[float], bool]:
    """(value last sent on an axis, whether it may be judged), consumed."""
    return _pending_commands.pop(axis, (None, True))


def forget_axis_commands() -> None:
    """Drop anything uncollected.  Used when a session restarts, so a value
    from a previous flight cannot be differenced against a new one."""
    _pending_commands.clear()


class AxisJitterMonitor:
    """Counts jolts on each driven axis and judges, over a few windows,
    whether the controls are flopping.

    A jolt is a frame in which the reported position moved at least
    ``JOLT`` of travel and that move was at least ``JOLT`` away from what
    the command moved in that frame or the one before.  A window of
    ``window_s`` holding ``JOLTS_PER_WINDOW`` of them is flickering, and an
    axis with ``FLICKERING_WINDOWS`` of its last ``WINDOWS_SEEN`` windows
    flickering is contended: a binding flops the control for as long as it
    is bound, while a hitch or a crash jolts once.
    """

    #: A tenth of full travel.  The captured bindings jolted by about 0.35;
    #: nothing the simulator did on its own in a clean capture came close.
    JOLT = 0.1

    JOLTS_PER_WINDOW = 3
    FLICKERING_WINDOWS = 3
    WINDOWS_SEEN = 5

    #: Frames further apart than this are not compared: across a stall
    #: both writers have moved, and the jump is not evidence of either.
    MAX_FRAME_GAP_S = 0.1

    #: Commands at or beyond this are clamped by the simulator, which
    #: reports its limit instead, so the move there says nothing.
    CLAMP_AT = 0.999

    #: Reports between one sighting of an unconfirmed axis and the next,
    #: so those axes stay visible without debug logging and without
    #: burying anything: about a line a minute each.
    PROVISIONAL_EVERY = 12

    #: Rows a raw capture will write before closing itself.  At a frame per
    #: axis this is a few minutes, which is long enough to hold a
    #: configuration change and short enough to mail.
    RAW_ROW_LIMIT = 120000

    def __init__(self, window_s: float = 2.0, report_s: float = 5.0,
                 raw_path: Optional[str] = None) -> None:
        self.window_s = window_s
        self.report_s = report_s
        # axis -> the previous two samples, (time, reported, commanded)
        self._recent: Dict[str, deque] = {}
        self._jolts: Dict[str, int] = {}          # in the window under way
        self._driven: set = set()                 # axes commanded in that window
        self._history: Dict[str, deque] = {}      # flickering or not, per window
        self._largest: Dict[str, float] = {}      # since the last report
        self._frames: Dict[str, int] = {}         # commanded frames since the last report
        self._seen: Dict[str, bool] = {}          # contended since the last report
        # Axes whose reported variable is not yet confirmed against a
        # flight: measured and logged, never judged.
        self._provisional: set = set()
        self._next_window: Optional[float] = None
        self._next_report: Optional[float] = None
        self._reports = 0
        self._raw_path = raw_path
        self._raw = None
        self._raw_rows = 0

    def _write_raw(self, axis: str, now: float, reported: float,
                   commanded: Optional[float]) -> None:
        """Append one sample to the raw capture.

        Summary statistics have repeatedly failed to tell a second writer
        from the artifacts of the simulator's own handling of the axis, and
        each failure was only visible in the waveform.  The capture exists
        so that question is settled by looking rather than by modelling.
        """
        if self._raw_path is None or self._raw_rows >= self.RAW_ROW_LIMIT:
            return
        try:
            if self._raw is None:
                self._raw = open(self._raw_path, 'w', encoding='utf-8',
                                 newline='')
                self._raw.write('t,axis,reported,commanded\n')
                logging.info("axis contention: raw capture writing to %s",
                             self._raw_path)
            self._raw.write('%.6f,%s,%.6f,%s\n' % (
                now, axis, reported,
                '' if commanded is None else '%.6f' % commanded))
            self._raw_rows += 1
            if self._raw_rows >= self.RAW_ROW_LIMIT:
                self._raw.close()
                self._raw = None
                logging.info("axis contention: raw capture complete (%d rows)",
                             self._raw_rows)
            elif self._raw_rows % 500 == 0:
                self._raw.flush()
        except Exception:
            # a diagnostic must never take the telemetry loop down with it
            self._raw_path = None
            self._raw = None
            logging.exception("axis contention: raw capture stopped")

    #: Registry value that turns the raw capture on, off unless set.
    #:
    #: Deliberately its own switch rather than the general 'debug' flag:
    #: that one is left on by anyone doing development, and a capture is a
    #: file per session of every frame on every axis.  Tying the two would
    #: quietly fill their disks to no purpose, since the capture is only of
    #: use while a particular verdict is being investigated.
    CAPTURE_SETTING = 'axisCapture'

    @classmethod
    def capture_enabled(cls) -> bool:
        """Whether to keep a raw capture of every frame.

        The probe itself always runs - a finding is worth having from any
        installation, and a window costs a few thousand operations once a
        second.  Read once by the caller and cached: this is consulted from
        a per-frame path, and the settings store is not free.
        """
        try:
            import telemffb.globals as G
            value = G.system_settings.get(cls.CAPTURE_SETTING, False)
            return str(value).strip().lower() in ('1', 'true', 'yes', 'on')
        except Exception:
            return False

    @staticmethod
    def default_raw_path() -> Optional[str]:
        """Where a raw capture goes: beside the logs, named for the device
        role and the session, so a support bundle picks it up."""
        try:
            import os
            import telemffb.globals as G
            # the same expression the logger uses, rather than the config
            # root, which a dev or beta channel may put beside the exe - a
            # capture belongs next to the logs it is read with
            folder = os.path.join(os.environ['LOCALAPPDATA'],
                                  'VPForce-TelemFFB', 'log')
            os.makedirs(folder, exist_ok=True)
            role = getattr(G, 'device_type', 'device')
            return os.path.join(
                folder, 'axis_capture_%s_%s.csv'
                % (role, time.strftime('%Y%m%d_%H%M%S')))
        except Exception:
            return None

    @classmethod
    def if_enabled(cls, **kwargs) -> Optional["AxisJitterMonitor"]:
        """A monitor.  Always built; the capture setting only decides
        whether it also writes every frame to disk."""
        if cls.capture_enabled():
            kwargs.setdefault('raw_path', cls.default_raw_path())
        return cls(**kwargs)

    @staticmethod
    def _number(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def set_provisional(self, axis: str, provisional: bool) -> None:
        """Whether this axis may reach a verdict.  Unconfirmed mappings are
        measured so a flight can confirm them, and no more."""
        if provisional:
            self._provisional.add(axis)
        else:
            self._provisional.discard(axis)

    def sample(self, axis: str, value, commanded=None,
               now: Optional[float] = None) -> None:
        """Record the position the simulator reports for an axis, and where
        available what TelemFFB last commanded on it.

        A value of None (simvar absent for this aircraft) is skipped rather
        than treated as zero.
        """
        value = self._number(value)
        if value is None:
            return
        now = time.perf_counter() if now is None else now
        commanded = self._number(commanded)
        self._write_raw(axis, now, value, commanded)
        recent = self._recent.get(axis)
        if recent is None:
            recent = self._recent[axis] = deque(maxlen=3)
        recent.append((now, value, commanded))
        if commanded is None:
            return
        self._driven.add(axis)
        self._frames[axis] = self._frames.get(axis, 0) + 1
        jolt = self._jolt(recent)
        if jolt is not None:
            self._jolts[axis] = self._jolts.get(axis, 0) + 1
            self._largest[axis] = max(self._largest.get(axis, 0.0), jolt)

    def _jolt(self, recent) -> Optional[float]:
        """How far the latest report jumped beyond the command's own move,
        or None when it did not jolt."""
        if len(recent) < 2:
            return None
        (t0, rep0, cmd0), (t1, rep1, cmd1) = recent[-2], recent[-1]
        if (cmd0 is None or t1 - t0 > self.MAX_FRAME_GAP_S
                or abs(cmd0) >= self.CLAMP_AT or abs(cmd1) >= self.CLAMP_AT):
            return None
        moved = rep1 - rep0
        if abs(moved) < self.JOLT:
            return None
        # the report may take the command's move a frame late
        command_moves = [cmd1 - cmd0]
        if len(recent) == 3 and recent[0][2] is not None:
            command_moves.append(cmd0 - recent[0][2])
        beyond = min(abs(moved - m) for m in command_moves)
        return beyond if beyond >= self.JOLT else None

    def due(self, now: Optional[float] = None) -> bool:
        """Whether the report interval has elapsed.  The first call arms the
        interval rather than reporting, so a report always covers a settled
        stretch."""
        now = time.perf_counter() if now is None else now
        if self._next_report is None:
            self._next_report = now + self.report_s
            return False
        if now < self._next_report:
            return False
        self._next_report = now + self.report_s
        return True

    def check(self, now: Optional[float] = None) -> None:
        """Close the window under way, once it has run its length: record
        whether each driven axis flickered in it, and whether that makes
        the axis contended."""
        now = time.perf_counter() if now is None else now
        if self._next_window is None:
            self._next_window = now + self.window_s
            return
        if now < self._next_window:
            return
        self._next_window = now + self.window_s
        for axis in self._driven:
            history = self._history.get(axis)
            if history is None:
                history = self._history[axis] = deque(maxlen=self.WINDOWS_SEEN)
            history.append(self._jolts.get(axis, 0) >= self.JOLTS_PER_WINDOW)
            if (sum(history) >= self.FLICKERING_WINDOWS
                    and axis not in self._provisional):
                self._seen[axis] = True
        self._driven.clear()
        self._jolts.clear()

    def contended_axes(self) -> list:
        """Axes judged contended since the last report."""
        return sorted(a for a, hit in self._seen.items() if hit)

    def poll(self, now: Optional[float] = None) -> None:
        """Advance the probe.  Called every frame."""
        now = time.perf_counter() if now is None else now
        self.check(now)
        self.log_if_due(now)

    def log_if_due(self, now: Optional[float] = None) -> None:
        """One line per axis driven since the last report: at INFO for a
        finding, and now and then for an axis awaiting confirmation; at
        DEBUG otherwise."""
        if not self.due(now):
            return
        self._reports += 1
        show_provisional = (self._reports == 1
                            or self._reports % self.PROVISIONAL_EVERY == 0)
        for axis in sorted(self._frames):
            provisional = axis in self._provisional
            contended = self._seen.get(axis, False)
            emit = (logging.info
                    if contended or (provisional and show_provisional)
                    else logging.debug)
            history = self._history.get(axis, ())
            emit("axis contention: %-10s %s flickering windows=%d/%d "
                 "largest jolt=%.3f frames=%d%s",
                 axis,
                 'CONTENDED' if contended else
                 ('unverified' if provisional else 'clean    '),
                 sum(history), len(history),
                 self._largest.get(axis, 0.0), self._frames[axis],
                 '  <- reported variable not yet confirmed for this axis; '
                 'not judged' if provisional else '')
        self._seen.clear()
        self._largest.clear()
        self._frames.clear()
