"""Jitter measurement on the sim's control-input axes.

A second writer on an axis TelemFFB drives - a binding left mapped in the
simulator's own controls - does not show up as a steady offset between what
was commanded and what the sim reports.  Both writers update the axis at
frame rate, so the sampled position alternates between them and the mean
sits somewhere unremarkable in between.  The signature is in the sample to
sample motion, not in the magnitude.

The rate at which the first difference changes sign measures that, but not
on the reported position alone: a vibration effect - propeller or engine
rumble above all - physically shakes the control, so the axis really does
turn around on almost every frame and reads identically to contention.

What separates them is the residual against what TelemFFB commanded.  Both
writers derive from the same shaking control, TelemFFB sending the physical
position less its virtual offset and the simulator's own binding sending
its curve of that position, so the vibration is common to both and cancels
when the command is subtracted.  What survives is the difference between
the two writers, which is the virtual offset - smooth, and near zero only
when nothing else is writing.  Residual statistics are therefore the ones
to read; position statistics are kept beside them because a capture is
worth more when it shows both.

The command's sign convention and its lag behind the reported value are
resolved by trying each and keeping the closest fit, rather than assuming a
convention this module has no way to verify.

Measurement only: nothing here decides whether a conflict exists, and the
thresholds that would make that decision are meant to come from comparing
captures of a known-clean and a deliberately contested configuration.
"""
from collections import deque
from typing import Dict, Optional, Tuple
import logging
import math
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
    """Rolling per-axis jitter statistics, logged at an interval.

    ``window_s`` bounds the sample history each statistic is computed over,
    and ``report_s`` how often a line is emitted.  ``deadband`` is the
    smallest move counted as a direction change, in the units of the
    sampled value (control positions are -1..1), and exists so that
    quantization noise on a stationary axis does not read as a high
    reversal rate.  It applies against a moving reference rather than the
    previous sample, so a slow drift still registers once it has travelled
    far enough instead of being discarded a sample at a time.
    """

    #: Travel a control must show before its mapping can be judged.
    #:
    #: A reported variable stuck at zero agrees perfectly with a command
    #: that is also near zero, so a still control makes a wrong mapping look
    #: right - which is how a helicopter's collective and tail rotor first
    #: read as confirmed while barely moving.  Nothing is concluded until
    #: the control has actually been worked.
    MAPPING_TRAVEL = 0.2

    #: Reports between one sighting of an unconfirmed axis and the next.
    #:
    #: Those axes exist to be checked, so they have to be visible without
    #: turning on debug logging - which on a normal flight is far too much
    #: to read.  At the usual cadence this is about a line a minute per
    #: axis, enough to confirm a mapping over a short flight and few enough
    #: not to bury anything.
    PROVISIONAL_EVERY = 12

    #: Rows a raw capture will write before closing itself.  At a frame per
    #: axis this is a few minutes, which is long enough to hold a
    #: configuration change and short enough to mail.
    RAW_ROW_LIMIT = 120000

    def __init__(self, window_s: float = 2.0, report_s: float = 5.0,
                 deadband: float = 0.001, raw_path: Optional[str] = None,
                 check_s: float = 1.0) -> None:
        self.window_s = window_s
        self.report_s = report_s
        # Evaluation runs on its own cadence, shorter than the window, so
        # consecutive evaluations overlap and every sample is examined by at
        # least one.  Evaluating only when reporting would leave the samples
        # between reports to be evicted unseen, and a burst of contention
        # falling in that gap would never be judged.
        self.check_s = min(check_s, window_s)
        self.deadband = deadband
        self._samples: Dict[str, deque] = {}
        # Sign and lag settle for a session; remembered per axis so the
        # search is not repeated on every window.
        self._fit_cache: Dict[str, tuple] = {}
        self._fit_ceiling: Dict[str, float] = {}
        self._next_check: Optional[float] = None
        self._next_report: Optional[float] = None
        self._peak: Dict[str, float] = {}
        self._travel: Dict[str, float] = {}
        self._seen: Dict[str, bool] = {}
        # Axes whose reported variable is not yet confirmed against a
        # flight.  Measured and logged like any other, but never allowed to
        # reach a verdict: a wrong mapping leaves a standing residual that
        # reads exactly like a second writer.
        self._provisional: set = set()
        self._latest: Dict[str, dict] = {}
        self._checks = 0
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
        than treated as zero, which would read as a full-scale excursion.
        """
        value = self._number(value)
        if value is None:
            return
        now = time.perf_counter() if now is None else now
        buf = self._samples.get(axis)
        if buf is None:
            buf = self._samples[axis] = deque()
        commanded = self._number(commanded)
        buf.append((now, value, commanded))
        self._write_raw(axis, now, value, commanded)
        horizon = now - self.window_s
        while buf and buf[0][0] < horizon:
            buf.popleft()

    def _measure(self, values) -> Tuple[int, float, list]:
        """(direction reversals, peak-to-peak, spacing between reversals).

        The spacing is in samples, and is what separates a driven effect
        from a race.  A vibration effect turns the control around on a
        period, so its gaps are all alike; which of two writers lands last
        in a frame is a matter of scheduling, so its gaps are not.
        """
        reversals = 0
        last_sign = 0
        reference = values[0]
        last_turn = 0
        spacing = []
        for index, value in enumerate(values[1:], start=1):
            delta = value - reference
            if abs(delta) < self.deadband:
                continue
            sign = 1 if delta > 0 else -1
            if last_sign and sign != last_sign:
                reversals += 1
                spacing.append(index - last_turn)
                last_turn = index
            last_sign = sign
            reference = value
        return reversals, max(values) - min(values), spacing

    #: How many recent commands the reported value is allowed to have come
    #: from.  The simulator sometimes reports from between two consecutive
    #: commands rather than from the latest, so a span of a few frames
    #: covers the delivery race without being wide enough to hide a gap.
    SPAN_FRAMES = 3

    #: Commands at or beyond this are clamped by the simulator, which
    #: reports its limit instead.  Those frames say nothing about a second
    #: writer and are left out - a large virtual offset pushes the command
    #: past full scale routinely.
    CLAMP_AT = 0.999

    def _excursion(self, buf, sign: int) -> Optional[dict]:
        """How far the reported position falls outside the range the command
        has recently visited.

        This is the measurement that survived the captures.  Everything the
        simulator does to an axis on its own - reporting from between two
        commands, lagging a fraction of a frame, shaking hard enough to make
        either large - keeps the reported value somewhere the command has
        just been.  Only a second writer can report a position the command
        never took, and the distance it lands outside is the gap between
        them.

        Unlike amplitude, turn rate or bias, none of which separated the two
        in the field, this does not move with how fast the control is
        shaking or where it is being held.
        """
        commanded = [sign * c for _, _, c in buf]
        reported = [v for _, v, _ in buf]
        span = self.SPAN_FRAMES
        values = []
        clamped = 0
        for i in range(span, len(buf)):
            if abs(commanded[i]) >= self.CLAMP_AT:
                clamped += 1
                continue
            window = commanded[i - span:i + 1]
            low, high = min(window), max(window)
            if reported[i] > high:
                values.append(reported[i] - high)
            elif reported[i] < low:
                values.append(low - reported[i])
            else:
                values.append(0.0)
        if len(values) < 10:
            return None
        values.sort()
        return {
            'median': values[len(values) // 2],
            # The verdict reads this rather than the median.  Which writer
            # lands last is a race, and the median would need the simulator
            # to win more frames than not; a quarter is enough here, and
            # since a clean axis measures the same either way - a ten
            # thousandth of travel at both - the reach costs nothing.
            'p75': values[int(0.75 * len(values))],
            'p99': values[int(0.99 * len(values))],
            'max': values[-1],
            'clamped_frac': clamped / max(1, len(buf) - span),
        }

    @staticmethod
    def _regularity(spacing) -> Tuple[Optional[float], Optional[float]]:
        """(mean gap between turns, how much those gaps vary).

        Variation is the spread over the mean, so it does not move with the
        rate itself: an evenly driven oscillation sits near zero however
        fast it runs, while gaps drawn at random sit near one.
        """
        if len(spacing) < 3:
            return None, None
        mean = sum(spacing) / len(spacing)
        if mean <= 0:
            return None, None
        variance = sum((s - mean) ** 2 for s in spacing) / len(spacing)
        return mean, math.sqrt(variance) / mean

    #: Sign conventions and frame lags tried when fitting the residual.  The
    #: reported position may invert what the axis event carries and may trail
    #: it by a frame; both are properties of the simulator, not of a fault.
    _FITS = ((1, 0), (1, 1), (-1, 0), (-1, 1))

    #: A window's median excursion beyond this reads as a second writer.
    #:
    #: Placed from paired captures of the same aircraft and rumble settings,
    #: one with the axes bound in the simulator and one without.  Every
    #: clean window measured at or under 0.000065 and every contested
    #: elevator window at or over 0.010, leaving the threshold two orders of
    #: magnitude clear on each side.
    #:
    #: The aileron was bound in that same run and reads clean, which is a
    #: miss and not a correct acquittal.  It carries almost no trim, so the
    #: gap between the two writers - a median of 0.008 - is smaller than the
    #: command's own travel between frames, and nothing lands outside the
    #: span to be seen.  That is also the case with no flicker to feel, and
    #: an axis bound alone is not a configuration anyone arrives at, so in
    #: practice the elevator carries the finding.
    #:
    #: Three earlier rules were tried and each convicted a clean axis:
    #: position turn rate (a rumble effect turns the control at frame rate
    #: exactly as a second writer does), residual size (a hard-driven rumble
    #: reached the amplitude and turn rate of a contested axis carrying
    #: little trim) and residual bias (predicted near zero for an artifact,
    #: measured at 0.33 to 0.41 - a control held off centre against a small
    #: gain error produces it on its own).  None of those failures was
    #: visible in a summary; all three were obvious in the waveform, which
    #: is what the raw capture is for.
    CONTENDED_EXCURSION = 0.005

    #: How little the reported position may move, while the command is
    #: travelling, before the variable is treated as not reporting this
    #: axis at all.
    #:
    #: Some aircraft never populate the variable an axis is read from -
    #: an external flight model computes its own controls and leaves the
    #: stock one at rest.  Differenced against a moving command that
    #: produces a residual the size of the command itself, which is the
    #: largest possible excursion and would convict on every flight.
    #:
    #: A second writer MOVES the reported value; it cannot hold it
    #: still.  So a frozen variable is an absent one, and saying so is
    #: the alternative to maintaining a list of aircraft that misreport.
    UNRESPONSIVE_TRAVEL = 0.005

    #: Frames arriving later than this multiple of the window's usual
    #: interval are counted as stalled, and the share is reported.  It is
    #: measured rather than acted on.
    #:
    #: A stuttering simulator could in principle answer from a command older
    #: than the span covers, which would put the reported value outside it
    #: for reasons unrelated to a second writer.  Against that: commands go
    #: out in response to frames arriving, so when frames stall the commands
    #: stall with them and the two stay in step.  Captures of real flights
    #: agree, holding at a ten-thousandth of travel through stalls of a
    #: third of a second and frame intervals varying more than a modelled
    #: stutter does.
    #:
    #: Two guards were tried and neither survived.  Counting frames against
    #: the window's own median fails exactly when stalling is pervasive,
    #: because the median is then itself a stalled frame; interval spread
    #: reads higher on real flights than on a modelled stutter.  A guard
    #: that cannot separate the two would only trade this risk for a worse
    #: one, so the share is logged and the verdict left alone until a
    #: capture of a genuinely stuttering sim says one is needed.
    STALL_MULTIPLE = 3.0

    def _residual(self, axis: str, buf, span: float) -> Optional[dict]:
        """Reported position against what was commanded, on the closest-
        fitting sign and lag.  None when any sample lacks a command, which
        is the case whenever TelemFFB is not driving the axis - and then
        there is nothing for a second writer to contend with anyway."""
        commanded = [c for _, _, c in buf]
        if any(c is None for c in commanded):
            return None
        reported = [v for _, v, _ in buf]

        def fit(sign, lag):
            series = [reported[i] - sign * commanded[i - lag]
                      for i in range(lag, len(buf))]
            if len(series) < 3:
                return None
            rms = math.sqrt(sum(x * x for x in series) / len(series))
            return (rms, sign, lag, series)

        # Which sign and lag the simulator answers with is a property of its
        # axis handling, settled for the session, so searching every one of
        # them on every window is most of this method's cost for an answer
        # that does not change.  The remembered pair is retried alone and
        # only rejected when it stops fitting - which a contested axis does
        # not cause, its residual being a gap rather than a misalignment.
        remembered = self._fit_cache.get(axis)
        if remembered is not None:
            best = fit(*remembered)
            if best is not None and best[0] <= self._fit_ceiling.get(axis, 0.0):
                return self._describe(best, buf, axis, span, commanded)

        best = None
        for sign, lag in self._FITS:
            candidate = fit(sign, lag)
            if candidate is None:
                continue
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is None:
            return None
        self._fit_cache[axis] = (best[1], best[2])
        # What the remembered pair is allowed to drift to before the search
        # is run again.  Generous, because a contested axis legitimately
        # carries a large residual on a correct fit.
        self._fit_ceiling[axis] = max(best[0] * 4.0, 0.01)
        return self._describe(best, buf, axis, span, commanded)

    def _describe(self, best, buf, axis, span, commanded) -> dict:
        rms, sign, lag, series = best
        reversals, peak_to_peak, spacing = self._measure(series)
        gap, gap_variation = self._regularity(spacing)
        excursion = self._excursion(buf, sign)
        stalled = self._stalled_fraction(buf)
        reversals_hz = reversals / span
        mean = sum(series) / len(series)
        # How one-sided the residual is.  A second writer can only push the
        # reported value to one side of the command - toward its own idea of
        # the axis - so its residual lies between zero and the gap and
        # averages to about half of it.  A residual that is really the
        # command's own motion seen a fraction of a frame out is symmetric
        # about zero and averages away, whatever its amplitude.
        bias = abs(mean) / rms if rms else 0.0
        # ...and how large it is next to that motion.  A lag artifact cannot
        # exceed the distance the command travels between frames; a genuine
        # gap is unrelated to it.
        command_travel = max(commanded) - min(commanded)
        # A variable that stays put while the command travels is not
        # reporting this axis, whatever the residual says.
        seen = [v for _, v, _ in buf]
        reported_travel = max(seen) - min(seen)
        unresponsive = (command_travel >= self.MAPPING_TRAVEL
                        and reported_travel <= self.UNRESPONSIVE_TRAVEL)
        steps = [commanded[i] - commanded[i - 1] for i in range(1, len(buf))]
        step_rms = math.sqrt(sum(s * s for s in steps) / len(steps)) if steps else 0.0
        return {
            'rms': rms,
            'mean': mean,
            'bias': bias,
            'step_rms': step_rms,
            'command_travel': command_travel,
            'vs_step': (rms / step_rms) if step_rms else float('inf'),
            'sign': sign,
            'lag': lag,
            'reversals_hz': reversals_hz,
            'peak_to_peak': peak_to_peak,
            'gap': gap,
            'gap_variation': gap_variation,
            'excursion': excursion,
            'stalled_frac': stalled,
            'reported_travel': reported_travel,
            'unresponsive': unresponsive,
            'contended': (excursion is not None
                          and not unresponsive
                          and excursion['p75'] >= self.CONTENDED_EXCURSION),
        }

    def _stalled_fraction(self, buf) -> float:
        """Share of this window's frames that arrived late enough to have
        let the simulator answer from a command the span no longer covers."""
        intervals = [buf[i][0] - buf[i - 1][0] for i in range(1, len(buf))]
        if len(intervals) < 3:
            return 0.0
        usual = sorted(intervals)[len(intervals) // 2]
        if usual <= 0:
            return 0.0
        return sum(1 for x in intervals
                   if x > self.STALL_MULTIPLE * usual) / len(intervals)

    def stats(self, axis: str) -> Optional[dict]:
        """Current statistics for one axis, or None without enough history
        to span a measurable interval.

        The top-level figures describe the reported position, which a
        vibration effect moves as readily as a second writer does.  The
        'residual' block is the one that separates them, and is absent when
        TelemFFB is not commanding the axis.
        """
        buf = self._samples.get(axis)
        if not buf or len(buf) < 3:
            return None
        span = buf[-1][0] - buf[0][0]
        if span <= 0:
            return None
        values = [v for _, v, _ in buf]
        reversals, peak_to_peak, _ = self._measure(values)
        return {
            'axis': axis,
            'samples': len(buf),
            'span_s': span,
            'sample_hz': (len(buf) - 1) / span,
            'reversals_hz': reversals / span,
            'peak_to_peak': peak_to_peak,
            'residual': self._residual(axis, buf, span),
        }

    def due(self, now: Optional[float] = None) -> bool:
        """Whether the report interval has elapsed.  The first call arms the
        interval rather than reporting, so a report always covers a settled
        window."""
        now = time.perf_counter() if now is None else now
        if self._next_report is None:
            self._next_report = now + self.report_s
            return False
        if now < self._next_report:
            return False
        self._next_report = now + self.report_s
        return True

    def check(self, now: Optional[float] = None) -> None:
        """Judge the current window, keeping the worst seen since the last
        report.

        Separate from reporting so that detection is continuous while the
        log stays readable: a verdict reached here is remembered and carried
        into the next report even if the window it came from is long gone by
        then.
        """
        now = time.perf_counter() if now is None else now
        if self._next_check is None:
            self._next_check = now + self.check_s
            return
        if now < self._next_check:
            return
        self._next_check = now + self.check_s
        self._checks += 1
        for axis in self._samples:
            stats = self.stats(axis)
            if stats is None:
                continue
            self._latest[axis] = stats
            residual = stats['residual']
            if residual is None:
                continue
            excursion = residual['excursion']
            if excursion is None:
                continue
            self._peak[axis] = max(self._peak.get(axis, 0.0),
                                   excursion['p75'])
            self._travel[axis] = max(self._travel.get(axis, 0.0),
                                     residual['command_travel'])
            if residual['contended'] and axis not in self._provisional:
                self._seen[axis] = True

    def contended_axes(self) -> list:
        """Axes judged contested since the last report.  What a notification
        would be raised from."""
        return sorted(a for a, hit in self._seen.items() if hit)

    def poll(self, now: Optional[float] = None) -> None:
        """Advance the probe: judge on the check cadence, report on the
        report cadence.  Called every frame."""
        now = time.perf_counter() if now is None else now
        self.check(now)
        self.log_if_due(now)

    def log_if_due(self, now: Optional[float] = None) -> None:
        """Emit one line per driven axis, at the report interval.

        Axes TelemFFB is not commanding are skipped: with no command there
        is nothing to difference against, and nothing for a second writer to
        contend with either.  The line carries measurements and draws no
        conclusion - the position figures in particular read the same either
        way, since a rumble effect turns the axis around as briskly as a
        second writer does and on a propeller aircraft does so all flight.
        """
        if not self.due(now):
            return
        checks = self._checks
        self._reports += 1
        show_provisional = (self._reports == 1
                            or self._reports % self.PROVISIONAL_EVERY == 0)
        for axis in sorted(self._latest):
            s = self._latest[axis]
            r = s['residual']
            if r is None:
                continue        # not commanded: nothing to contend with
            fmt = (lambda v: '  n/a' if v is None else '%5.2f' % v)
            x = r['excursion']
            # A finding is worth a normal log line, and so is an occasional
            # sighting of an axis waiting to be confirmed - that one cannot
            # be checked at all if it is only visible under debug.  The rest
            # is the background they were read against and belongs at debug.
            provisional = axis in self._provisional
            emit = (logging.info
                    if self._seen.get(axis) or (provisional and show_provisional)
                    else logging.debug)
            # An unconfirmed axis is being looked at to settle whether its
            # reported variable follows the control at all, so it says so
            # rather than leaving a number to be interpreted.  Read with
            # nothing bound: a mapping that holds sits at the same
            # hundred-thousandth of travel a clean axis does, while one that
            # does not carries the whole command as error.
            note = ''
            # Said out loud rather than passed over in silence: an axis
            # that reads dead looks identical to one nobody is fighting
            # over, and only this line distinguishes them.
            if r.get('unresponsive'):
                note = ('  <- reported variable never moved; this '
                        'aircraft does not populate it, so nothing '
                        'can be judged')
            elif provisional:
                if self._travel.get(axis, 0.0) < self.MAPPING_TRAVEL:
                    note = ('  <- inconclusive; work this control through '
                            'its range to tell')
                elif self._peak.get(axis, 0.0) < self.CONTENDED_EXCURSION:
                    note = '  <- mapping holds'
                else:
                    note = ('  <- mapping does NOT hold; nothing can be '
                            'judged against this variable')
            emit(
                "axis contention: %-9s %s worst outside=%.5f over %d checks "
                "| stalled=%2.0f%% latest p99=%s clamped=%2.0f%% "
                "residual rms=%.4f p2p=%.4f "
                "turns=%.1f/s gap=%s var=%s bias=%.2f  (step rms=%.4f; "
                "position turns=%.1f/s p2p=%.4f; fit sign=%+d lag=%d; "
                "sampled %.1f/s n=%d)%s",
                s['axis'],
                'CONTENDED' if self._seen.get(axis) else
                ('unverified' if provisional else 'clean    '),
                self._peak.get(axis, 0.0), checks,
                100 * r['stalled_frac'],
                'n/a' if x is None else '%.5f' % x['p99'],
                0.0 if x is None else 100 * x['clamped_frac'],
                r['rms'], r['peak_to_peak'], r['reversals_hz'],
                fmt(r['gap']), fmt(r['gap_variation']), r['bias'],
                r['step_rms'],
                s['reversals_hz'], s['peak_to_peak'], r['sign'], r['lag'],
                s['sample_hz'], s['samples'], note)
        self._peak.clear()
        self._travel.clear()
        self._seen.clear()
        self._latest.clear()
        self._checks = 0
