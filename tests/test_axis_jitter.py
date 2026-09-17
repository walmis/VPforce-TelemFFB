"""The axis probe's measurements.

The probe reports; it does not yet judge.  Three candidate rules for calling
an axis contested have each been disproved by a field capture, so what is
pinned here is that the quantities are computed correctly - the reversal
counting, the deadband, the residual and the sign and lag fitted into it -
and not any claim about which combination of them means a second writer.
"""
import math

import pytest

from telemffb.utils.AxisJitter import AxisJitterMonitor

pytestmark = [pytest.mark.unit]

FRAME = 1.0 / 60.0


def feed(monitor, values, axis='elevator', start=0.0, step=FRAME):
    for i, v in enumerate(values):
        monitor.sample(axis, v, now=start + i * step)
    return monitor


def rate(monitor, axis='elevator'):
    return monitor.stats(axis)['reversals_hz']


class TestTheTwoSignals:
    def test_a_steady_ramp_never_reverses(self):
        m = feed(AxisJitterMonitor(), [i * 0.002 for i in range(120)])
        assert rate(m) == 0

    def test_hand_speed_input_reverses_a_few_times_a_second(self):
        """A 2 Hz sine - faster than a pilot moves a stick - turns twice per
        cycle, so it sits near 4/s however finely it is sampled."""
        m = feed(AxisJitterMonitor(),
                 [0.5 * math.sin(2 * math.pi * 2.0 * i * FRAME)
                  for i in range(120)])
        assert rate(m) == pytest.approx(4.0, abs=1.0)

    def test_two_writers_at_frame_rate_reverse_on_every_frame(self):
        """One reversal per frame, so the rate approaches the sample rate
        itself - an order of magnitude clear of anything a hand produces."""
        m = feed(AxisJitterMonitor(),
                 [0.0 if i % 2 else 0.25 for i in range(120)])
        assert rate(m) == pytest.approx(60.0, abs=1.5)

    def test_the_two_signals_do_not_overlap(self):
        smooth = feed(AxisJitterMonitor(),
                      [0.5 * math.sin(2 * math.pi * 2.0 * i * FRAME)
                       for i in range(120)])
        contested = feed(AxisJitterMonitor(),
                         [0.0 if i % 2 else 0.25 for i in range(120)])
        assert rate(contested) > 5 * rate(smooth)


def feed_pairs(monitor, pairs, axis='elevator', start=0.0, step=FRAME):
    for i, (reported, commanded) in enumerate(pairs):
        monitor.sample(axis, reported, commanded, now=start + i * step)
    return monitor


def rumble(i, hz=37.0, amp=0.3):
    """A vibration effect shaking the control faster than the frame rate
    resolves, which is what a propeller does to the stick."""
    return amp * math.sin(2 * math.pi * hz * i * FRAME)


def flying(i):
    """A command moving the way a captured one did: a control being flown,
    carrying rumble, travelling around a hundredth of full scale per frame.
    The synthetic `rumble` above aliases far harder than that and is not a
    stand-in for it where frame-to-frame travel matters."""
    return 0.3 * math.sin(2 * math.pi * 0.5 * i * FRAME) + rumble(i, amp=0.004)


class TestRumbleVersusContention:
    """The distinction the position statistics cannot draw.

    A rumble effect physically moves the control, so the axis genuinely
    turns around on almost every frame.  Both writers derive from that same
    shaking control, so it cancels in the residual while the gap between
    the writers does not.
    """

    def test_rumble_alone_looks_exactly_like_contention_in_the_position(self):
        m = feed_pairs(AxisJitterMonitor(),
                       [(rumble(i), rumble(i)) for i in range(120)])
        assert m.stats('elevator')['reversals_hz'] > 40

    def test_but_its_residual_is_flat(self):
        m = feed_pairs(AxisJitterMonitor(),
                       [(rumble(i), rumble(i)) for i in range(120)])
        r = m.stats('elevator')['residual']
        assert r['rms'] == pytest.approx(0, abs=1e-9)
        assert r['peak_to_peak'] == pytest.approx(0, abs=1e-9)

    def test_a_frame_of_lag_is_fitted_out_rather_than_read_as_conflict(self):
        """Reported trailing commanded by a frame would otherwise leave a
        residual the size of a full rumble excursion."""
        m = feed_pairs(AxisJitterMonitor(),
                       [(rumble(i - 1), rumble(i)) for i in range(1, 120)])
        r = m.stats('elevator')['residual']
        assert r['lag'] == 1
        assert r['rms'] == pytest.approx(0, abs=1e-9)

    def test_an_inverted_report_is_fitted_out_too(self):
        m = feed_pairs(AxisJitterMonitor(),
                       [(-rumble(i), rumble(i)) for i in range(120)])
        r = m.stats('elevator')['residual']
        assert r['sign'] == -1
        assert r['rms'] == pytest.approx(0, abs=1e-9)

    def test_contention_under_rumble_survives_in_the_residual(self):
        """A second writer sends its own curve of the same stick, so the
        reported value alternates between the two by the virtual offset."""
        offset = 0.2
        m = feed_pairs(AxisJitterMonitor(),
                       [(rumble(i) + (0 if i % 2 else offset), rumble(i))
                        for i in range(120)])
        r = m.stats('elevator')['residual']
        assert r['peak_to_peak'] == pytest.approx(offset, abs=0.01)
        assert r['reversals_hz'] > 40

    def test_the_residual_separates_what_the_position_cannot(self):
        clean = feed_pairs(AxisJitterMonitor(),
                           [(rumble(i), rumble(i)) for i in range(120)])
        contested = feed_pairs(AxisJitterMonitor(),
                               [(rumble(i) + (0 if i % 2 else 0.2), rumble(i))
                                for i in range(120)])
        # indistinguishable by position
        assert clean.stats('elevator')['reversals_hz'] > 40
        assert contested.stats('elevator')['reversals_hz'] > 40
        # separated by residual
        assert clean.stats('elevator')['residual']['rms'] < 0.001
        assert contested.stats('elevator')['residual']['rms'] > 0.05



def lagged(values, seed=1):
    """The reported value taken a fraction of a frame out of step with the
    command, which is what a heavily driven vibration effect exposes: the
    residual then carries the command's own motion rather than any gap."""
    import random
    rng = random.Random(seed)
    return [values[i] if rng.random() < 0.5 else values[i - 1]
            for i in range(len(values))]



class TestResidualShape:
    """The quantities that describe a residual's shape, rather than its
    size.  Whether any of them separates contention from the simulator's
    own handling of the axis is unsettled - these only pin what they
    measure."""

    def _residual(self, pairs):
        return feed_pairs(AxisJitterMonitor(), pairs).stats('elevator')['residual']

    def test_a_one_sided_residual_reads_as_biased(self):
        """A gap that only ever pushes one way, which is the shape a second
        writer would have to produce."""
        r = self._residual([(rumble(i) + (0 if i % 2 else 0.2), rumble(i))
                            for i in range(140)])
        assert r['mean'] > 0
        assert r['bias'] > 0.5

    def test_a_symmetric_residual_does_not(self):
        cmd = [rumble(i, amp=0.45) for i in range(140)]
        r = self._residual(list(zip(lagged(cmd), cmd)))
        assert r['bias'] < 0.3

    def test_bias_is_independent_of_the_gap_size(self):
        cmd = [rumble(i, amp=0.05) for i in range(140)]
        small = self._residual([(cmd[i] + (0 if i % 2 else 0.01), cmd[i])
                                for i in range(140)])
        large = self._residual([(cmd[i] + (0 if i % 2 else 0.20), cmd[i])
                                for i in range(140)])
        assert large['rms'] > 10 * small['rms']
        assert large['bias'] == pytest.approx(small['bias'], abs=0.05)

    def test_the_command_step_size_is_measured(self):
        """What a lag artifact is bounded by, recorded so a capture can be
        read against it."""
        cmd = [0.01 * i for i in range(60)]
        r = self._residual([(cmd[i], cmd[i]) for i in range(60)])
        assert r['step_rms'] == pytest.approx(0.01, abs=1e-6)


class TestTurnRegularity:
    """How evenly spaced the turns are, which is the one shape measurement
    a held control cannot corrupt.

    A driven vibration effect turns the control on a period, so its gaps are
    alike; which of two writers lands last in a frame is a scheduling race,
    so its gaps are not.
    """

    def _residual(self, pairs):
        return feed_pairs(AxisJitterMonitor(), pairs).stats('elevator')['residual']

    def test_an_evenly_driven_oscillation_has_consistent_gaps(self):
        cmd = [0.35 * math.sin(2 * math.pi * 9.0 * i * FRAME)
               for i in range(220)]
        r = self._residual([(0.97 * cmd[i], cmd[i]) for i in range(220)])
        assert r['gap_variation'] < 0.3

    def test_a_race_does_not(self):
        import random
        rng = random.Random(5)
        cmd = [0.05 * math.sin(2 * math.pi * 9.0 * i * FRAME)
               for i in range(220)]
        r = self._residual([(cmd[i] + (0.15 if rng.random() < 0.4 else 0.0),
                             cmd[i]) for i in range(220)])
        assert r['gap_variation'] > 0.6

    def test_regularity_survives_a_control_held_off_centre(self):
        """Unlike bias, which a held control plus a small gain error in the
        simulator drives on its own - the reason bias cannot decide."""
        seen = []
        for held in (0.0, 0.3):
            cmd = [held + 0.35 * math.sin(2 * math.pi * 9.0 * i * FRAME)
                   for i in range(220)]
            r = self._residual([(0.97 * cmd[i], cmd[i]) for i in range(220)])
            seen.append(r)
        assert seen[1]['bias'] > seen[0]['bias'] + 0.5      # bias moves
        assert seen[1]['gap_variation'] == pytest.approx(
            seen[0]['gap_variation'], abs=0.05)             # regularity does not

    def test_too_few_turns_to_describe_leaves_it_unmeasured(self):
        r = self._residual([(i * 0.002, i * 0.002) for i in range(60)])
        assert r['gap'] is None
        assert r['gap_variation'] is None


class TestResidualAvailability:
    def test_no_residual_when_the_axis_is_not_being_driven(self):
        m = feed(AxisJitterMonitor(), [rumble(i) for i in range(60)])
        assert m.stats('elevator')['residual'] is None

    def test_no_residual_when_the_command_lapses_mid_window(self):
        """Axis control switched off partway leaves samples with no command,
        and a partial fit would be read against a stale value."""
        m = AxisJitterMonitor()
        for i in range(60):
            m.sample('elevator', rumble(i),
                     rumble(i) if i < 30 else None, now=i * FRAME)
        assert m.stats('elevator')['residual'] is None


class TestAmplitude:
    def test_peak_to_peak_is_the_travel_over_the_window(self):
        m = feed(AxisJitterMonitor(), [-0.4 if i % 2 else 0.35
                                       for i in range(60)])
        assert m.stats('elevator')['peak_to_peak'] == pytest.approx(0.75)

    def test_a_contested_axis_is_told_from_a_still_one_by_amplitude(self):
        """Both alternate every sample; only one is moving enough to matter,
        which is why the reversal rate is never read on its own."""
        noise = feed(AxisJitterMonitor(),
                     [0.0 if i % 2 else 1e-5 for i in range(60)])
        real = feed(AxisJitterMonitor(),
                    [0.0 if i % 2 else 0.25 for i in range(60)])
        assert noise.stats('elevator')['peak_to_peak'] < 1e-3
        assert real.stats('elevator')['peak_to_peak'] > 0.1


class TestDeadband:
    def test_quantization_noise_does_not_register_as_reversals(self):
        m = feed(AxisJitterMonitor(deadband=0.001),
                 [0.0 if i % 2 else 0.0001 for i in range(120)])
        assert rate(m) == 0

    def test_a_drift_slower_than_the_deadband_still_registers(self):
        """The deadband runs against a moving reference, so travel
        accumulates instead of being discarded one sample at a time."""
        up = [i * 0.0001 for i in range(60)]
        m = feed(AxisJitterMonitor(deadband=0.001), up + up[::-1])
        assert rate(m) > 0


class TestSampling:
    def test_an_axis_the_sim_does_not_report_is_skipped(self):
        m = AxisJitterMonitor()
        for i in range(10):
            m.sample('rudder', None, now=i * FRAME)
        assert m.stats('rudder') is None

    def test_a_non_numeric_sample_is_ignored(self):
        m = AxisJitterMonitor()
        for i in range(10):
            m.sample('rudder', 'n/a', now=i * FRAME)
        assert m.stats('rudder') is None

    def test_samples_older_than_the_window_are_dropped(self):
        m = feed(AxisJitterMonitor(window_s=0.5),
                 [i * 0.001 for i in range(120)])
        assert m.stats('elevator')['span_s'] <= 0.5

    def test_the_sample_rate_is_reported(self):
        m = feed(AxisJitterMonitor(), [i * 0.001 for i in range(60)])
        assert m.stats('elevator')['sample_hz'] == pytest.approx(60, abs=1)

    def test_axes_are_measured_independently(self):
        m = AxisJitterMonitor()
        feed(m, [i * 0.002 for i in range(120)], axis='elevator')
        feed(m, [0.0 if i % 2 else 0.25 for i in range(120)], axis='aileron')
        assert rate(m, 'elevator') == 0
        assert rate(m, 'aileron') > 20

    def test_too_little_history_measures_nothing(self):
        m = AxisJitterMonitor()
        m.sample('elevator', 0.1, now=0.0)
        assert m.stats('elevator') is None


class TestReportInterval:
    def test_the_first_call_arms_the_interval_rather_than_reporting(self):
        m = AxisJitterMonitor(report_s=5.0)
        assert not m.due(now=100.0)

    def test_it_reports_once_the_interval_has_passed(self):
        m = AxisJitterMonitor(report_s=5.0)
        m.due(now=100.0)
        assert not m.due(now=104.9)
        assert m.due(now=105.0)
        assert not m.due(now=105.1)


class TestTheCaptureSwitch:
    """The probe runs everywhere; only the raw capture is behind a setting,
    and a setting of its own rather than the general debug flag - that one
    is left on by anyone developing, and a capture is every frame on every
    axis written to disk for the life of the session.
    """

    def test_a_monitor_is_built_whatever_the_setting_says(self, monkeypatch):
        monkeypatch.setattr(AxisJitterMonitor, 'capture_enabled',
                            classmethod(lambda cls: False))
        assert isinstance(AxisJitterMonitor.if_enabled(), AxisJitterMonitor)

    def test_no_capture_is_written_unless_it_is_asked_for(self, monkeypatch):
        monkeypatch.setattr(AxisJitterMonitor, 'capture_enabled',
                            classmethod(lambda cls: False))
        assert AxisJitterMonitor.if_enabled()._raw_path is None

    def test_a_capture_is_written_when_it_is(self, monkeypatch, tmp_path):
        monkeypatch.setattr(AxisJitterMonitor, 'capture_enabled',
                            classmethod(lambda cls: True))
        monkeypatch.setattr(AxisJitterMonitor, 'default_raw_path',
                            staticmethod(lambda: str(tmp_path / 'cap.csv')))
        assert AxisJitterMonitor.if_enabled()._raw_path is not None

    def test_the_general_debug_flag_does_not_turn_it_on(self, monkeypatch):
        import telemffb.globals as G

        class Settings:
            def get(self, key, default=None):
                return True if key == 'debug' else default

        monkeypatch.setattr(G, 'system_settings', Settings(), raising=False)
        assert AxisJitterMonitor.capture_enabled() is False

    def test_an_unreadable_settings_store_leaves_it_off(self, monkeypatch):
        import telemffb.globals as G

        class Boom:
            def get(self, *a, **k):
                raise RuntimeError('no settings here')

        monkeypatch.setattr(G, 'system_settings', Boom(), raising=False)
        assert AxisJitterMonitor.capture_enabled() is False

class TestExcursionBeyondTheCommandSpan:
    """How far the reported position lands outside the range the command has
    just visited.

    Derived from a field capture rather than modelled, after amplitude, turn
    rate and bias each convicted an axis with nothing bound to it.  What the
    capture showed is that the simulator's own handling keeps the reported
    value somewhere the command has recently been - it reports from between
    two commands rather than from the latest - while a second writer has to
    report a position the command never took.
    """

    def _excursion(self, pairs):
        return feed_pairs(AxisJitterMonitor(), pairs).stats(
            'elevator')['residual']['excursion']

    def test_a_report_from_between_two_commands_never_escapes_the_span(self):
        """The delivery race, which is what a hard-driven rumble exposes:
        large residuals, none of them outside where the command has been."""
        cmd = [rumble(i, amp=0.45) for i in range(140)]
        rep = [(cmd[i] + cmd[i - 1]) / 2 for i in range(140)]
        x = self._excursion(list(zip(rep, cmd)))
        assert x['p99'] == 0

    def test_a_second_writer_does_escape_it(self):
        cmd = [rumble(i, amp=0.05) for i in range(140)]
        rep = [cmd[i] + (0 if i % 2 else 0.15) for i in range(140)]
        x = self._excursion(list(zip(rep, cmd)))
        assert x['p99'] > 0.1

    def test_it_does_not_move_with_how_hard_the_control_shakes(self):
        """Unlike residual size, which grew with the rumble until it
        overlapped genuine contention."""
        for amp in (0.05, 0.45):
            cmd = [rumble(i, amp=amp) for i in range(140)]
            rep = [(cmd[i] + cmd[i - 1]) / 2 for i in range(140)]
            assert self._excursion(list(zip(rep, cmd)))['p99'] == 0

    def test_it_does_not_move_with_where_the_control_is_held(self):
        """Unlike bias, which a held control drove on its own."""
        for held in (0.0, 0.3):
            cmd = [held + rumble(i, amp=0.1) for i in range(140)]
            rep = [(cmd[i] + cmd[i - 1]) / 2 for i in range(140)]
            assert self._excursion(list(zip(rep, cmd)))['p99'] == 0

    def test_frames_the_simulator_clamps_are_left_out(self):
        """A large virtual offset pushes the command past full scale, and
        the limit reported back is not evidence of anything."""
        cmd = [1.5 for _ in range(140)]
        rep = [1.0 for _ in range(140)]
        x = self._excursion(list(zip(rep, cmd)))
        assert x is None or x['clamped_frac'] > 0.9


class TestTheVerdict:
    """Placed from paired captures of the same aircraft and rumble settings,
    one with the elevator bound in the simulator and one without.

    Every clean window measured at or under 0.000065 and every contested one
    at or over 0.010, with the aileron - unbound in both runs - staying under
    0.000227 through the contested one.  The figures below are those, so a
    change that would have reclassified the real flights fails here.
    """

    def _verdict(self, pairs):
        return feed_pairs(AxisJitterMonitor(), pairs).stats(
            'elevator')['residual']['contended']

    def test_the_measured_clean_ceiling_is_not_contention(self):
        cmd = [rumble(i, amp=0.3) for i in range(140)]
        rep = [cmd[i] + (0.000065 if i % 2 else 0.0) for i in range(140)]
        assert not self._verdict(list(zip(rep, cmd)))

    def test_a_contested_axis_is(self):
        """The captured gap, on a command moving as the captured one did -
        roughly a hundredth of travel per frame."""
        cmd = [flying(i) for i in range(140)]
        rep = [cmd[i] + (0.45 if i % 2 else 0.0) for i in range(140)]
        assert self._verdict(list(zip(rep, cmd)))

    def test_a_gap_smaller_than_the_control_travels_is_not_seen(self):
        """The measure's reach.  The reported value has to land outside where
        the command has just been, so a gap under a few frames of the
        control's own travel is absorbed - which is why the captured aileron,
        bound in the simulator but carrying almost no trim, read clean.  It
        is also the case that produces no flicker to feel.
        """
        cmd = [flying(i) for i in range(140)]
        rep = [cmd[i] + (0.005 if i % 2 else 0.0) for i in range(140)]
        assert not self._verdict(list(zip(rep, cmd)))

    def test_the_reach_follows_how_hard_the_control_is_moving(self):
        """With the control nearly still the span collapses and a much
        smaller gap becomes visible, so sensitivity tracks conditions rather
        than being fixed."""
        cmd = [0.001 * flying(i) for i in range(140)]
        rep = [cmd[i] + (0.05 if i % 2 else 0.0) for i in range(140)]
        assert self._verdict(list(zip(rep, cmd)))

    def test_an_unbound_axis_beside_a_bound_one_stays_clean(self):
        """The contested capture's aileron: the measure names the axis, not
        the flight."""
        cmd = [rumble(i, amp=0.3) for i in range(140)]
        rep = [cmd[i] + (0.000227 if i % 2 else 0.0) for i in range(140)]
        assert not self._verdict(list(zip(rep, cmd)))

    def test_a_hard_driven_rumble_alone_is_still_not_contention(self):
        """The false positive that disproved the three earlier rules."""
        cmd = [rumble(i, amp=0.45) for i in range(140)]
        rep = [(cmd[i] + cmd[i - 1]) / 2 for i in range(140)]
        assert not self._verdict(list(zip(rep, cmd)))

    def test_no_verdict_without_enough_unclamped_samples(self):
        m = AxisJitterMonitor()
        for i in range(8):
            m.sample('elevator', 0.1, 0.1, now=i * FRAME)
        r = m.stats('elevator')['residual']
        assert r['excursion'] is None
        assert not r['contended']


class TestContinuousDetection:
    """Judging happens on its own cadence, not at the report.

    The sample buffer only holds the last couple of seconds, so evaluating
    only when a report falls due would leave the samples in between to be
    evicted unseen - and a burst of contention landing in that gap would
    never be judged at all.
    """

    def _feed(self, monitor, pairs, start):
        for i, (reported, commanded) in enumerate(pairs):
            now = start + i * FRAME
            monitor.sample('elevator', reported, commanded, now=now)
            monitor.poll(now=now)
        return monitor

    def _clean(self, n, start_i=0):
        return [(flying(i), flying(i)) for i in range(start_i, start_i + n)]

    def _contested(self, n, start_i=0):
        return [(flying(i) + (0.45 if i % 2 else 0.0), flying(i))
                for i in range(start_i, start_i + n)]

    def test_a_burst_between_reports_is_still_caught(self):
        """The whole point: contention that starts and ends inside the gap
        between two reports.  The report interval is held off here so the
        finding is read before it is emitted and cleared."""
        m = AxisJitterMonitor(window_s=2.0, report_s=600.0, check_s=1.0)
        t = 0.0
        for chunk, n in ((self._clean, 60), (self._contested, 90),
                         (self._clean, 150)):
            self._feed(m, chunk(n, int(round(t * 60))), t)
            t += n * FRAME
        assert 'elevator' in m.contended_axes()

    def test_a_clean_run_of_the_same_shape_is_not(self):
        m = AxisJitterMonitor(window_s=2.0, report_s=600.0, check_s=1.0)
        self._feed(m, self._clean(300), 0.0)
        assert m.contended_axes() == []

    def test_the_finding_survives_until_it_is_reported(self):
        """A verdict reached early in the interval is still carried into the
        report, after the window it came from has been evicted."""
        m = AxisJitterMonitor(window_s=2.0, report_s=600.0, check_s=1.0)
        self._feed(m, self._contested(140), 0.0)
        self._feed(m, self._clean(140, 140), 140 * FRAME)
        assert 'elevator' in m.contended_axes()

    def test_checks_overlap_so_no_sample_goes_unjudged(self):
        assert AxisJitterMonitor().check_s <= AxisJitterMonitor().window_s

    def test_the_slate_is_cleared_after_each_report(self):
        m = AxisJitterMonitor(window_s=2.0, report_s=600.0, check_s=1.0)
        self._feed(m, self._contested(140), 0.0)
        assert m.contended_axes() == ['elevator']
        m.log_if_due(now=10000.0)         # report falls due; slate resets
        assert m.contended_axes() == []


class TestStallReporting:
    """Frame-timing stalls are measured, not acted on.

    A stuttering simulator could in principle answer from a command older
    than the span covers.  Against that, commands go out in response to
    frames arriving, so a stall holds both and they stay in step - which is
    what real captures show, holding steady through stalls of a third of a
    second.  Two guards were tried and neither separated a modelled stutter
    from an ordinary flight, so the share is reported and left to a capture
    of a genuinely stuttering sim to settle.
    """

    def _run(self, stall_frac, n=400, seed=1):
        import random
        rng = random.Random(seed)
        cmd = [flying(i) for i in range(n)]
        m = AxisJitterMonitor(window_s=2.0, report_s=1e9, check_s=1.0)
        now = 0.0
        for i in range(n):
            now += FRAME * (5.0 if rng.random() < stall_frac else 1.0)
            m.sample('elevator', cmd[i], cmd[i], now=now)
            m.check(now)
        return m

    def test_a_steady_frame_rate_reports_no_stalls(self):
        assert self._run(0.0).stats('elevator')['residual']['stalled_frac'] == 0.0

    def test_stalls_are_counted_when_they_are_occasional(self):
        assert self._run(0.10).stats('elevator')['residual']['stalled_frac'] > 0.0

    def test_a_stalling_sim_in_step_with_its_commands_is_not_convicted(self):
        """The case the architecture produces: frames stop, commands stop
        with them, and nothing lands outside the span."""
        assert self._run(0.40).contended_axes() == []

    def test_contention_is_still_caught_through_a_stutter(self):
        import random
        rng = random.Random(1)
        cmd = [flying(i) for i in range(400)]
        m = AxisJitterMonitor(window_s=2.0, report_s=1e9, check_s=1.0)
        now = 0.0
        for i in range(400):
            now += FRAME * (5.0 if rng.random() < 0.30 else 1.0)
            m.sample('elevator', cmd[i] + (0.45 if i % 2 else 0.0), cmd[i],
                     now=now)
            m.check(now)
        assert m.contended_axes() == ['elevator']


class TestLogLevels:
    """A finding is a normal log line; the background it was read against is
    not.  Shipped as log-only to gauge false reports from the field, so an
    ordinary flight has to stay quiet at INFO while still leaving the
    figures behind at DEBUG for anything that does get reported.
    """

    def _emit(self, monitor, level):
        import logging as lg
        records = []

        class Sink(lg.Handler):
            def emit(self, record):
                records.append(record)

        sink = Sink()
        root = lg.getLogger()
        root.addHandler(sink)
        old = root.level
        root.setLevel(lg.DEBUG)
        try:
            monitor.log_if_due(now=1e9)
        finally:
            root.removeHandler(sink)
            root.setLevel(old)
        return [r for r in records if r.levelno == level]

    def _run(self, contested):
        cmd = [flying(i) for i in range(200)]
        m = AxisJitterMonitor(window_s=2.0, report_s=1e9, check_s=1.0)
        m.log_if_due(now=0.0)        # arms the interval; reports nothing
        for i in range(200):
            now = i * FRAME
            offset = (0.45 if i % 2 else 0.0) if contested else 0.0
            m.sample('elevator', cmd[i] + offset, cmd[i], now=now)
            m.check(now)
        return m

    def test_a_clean_axis_says_nothing_at_info(self):
        import logging as lg
        assert self._emit(self._run(False), lg.INFO) == []

    def test_but_still_leaves_its_figures_at_debug(self):
        import logging as lg
        assert self._emit(self._run(False), lg.DEBUG) != []

    def test_a_finding_is_reported_at_info(self):
        import logging as lg
        assert self._emit(self._run(True), lg.INFO) != []


class TestCommandInterception:
    """Commands are collected where they are sent, not where they are
    decided.

    Every axis TelemFFB drives passes through one send, so intercepting
    there covers fixed wing, fly-by-wire, the helicopter cyclic and
    collective and the trim wheel together - and an effect path added later
    is covered without being told to take part.
    """

    def setup_method(self):
        from telemffb.utils.AxisJitter import forget_axis_commands
        forget_axis_commands()

    def _record(self, event, value):
        from telemffb.utils.AxisJitter import record_axis_command
        record_axis_command(event, value)

    def _take(self, axis):
        from telemffb.utils.AxisJitter import take_axis_command
        return take_axis_command(axis)[0]

    def _verified(self, axis):
        from telemffb.utils.AxisJitter import take_axis_command
        return take_axis_command(axis)[1]

    def test_the_fixed_wing_axes_are_recognized(self):
        self._record('AXIS_ELEVATOR_SET', -16384)
        self._record('AXIS_AILERONS_SET', 8192)
        self._record('AXIS_RUDDER_SET', 0)
        assert self._take('elevator') == pytest.approx(1.0)
        assert self._take('aileron') == pytest.approx(-0.5)
        assert self._take('rudder') == pytest.approx(0.0)

    def test_helicopter_axes_are_recorded_on_their_own_keys(self):
        """The cyclic must NOT land on elevator and aileron.  Mapped
        there it was differenced against AILERON/ELEVATOR POSITION, which
        sit flat at zero on several rotorcraft, producing a residual the
        size of the command itself."""
        for event in ('AXIS_CYCLIC_LONGITUDINAL_SET',
                      'AXIS_CYCLIC_LATERAL_SET',
                      'AXIS_COLLECTIVE_SET', 'ROTOR_AXIS_TAIL_ROTOR_SET'):
            self._record(event, -16384)
        assert self._take('cyclic_lon') == pytest.approx(1.0)
        assert self._take('cyclic_lat') == pytest.approx(1.0)
        assert self._take('collective') == pytest.approx(1.0)
        assert self._take('tail_rotor') == pytest.approx(1.0)
        for axis in ('elevator', 'aileron'):
            assert self._take(axis) is None

    def test_a_dead_reported_variable_is_not_a_second_writer(self):
        """Some aircraft never populate the variable an axis is read
        from - an external flight model computes its own controls and
        leaves the stock one at rest.  Differenced against a moving
        command that is the largest excursion possible, so it would
        convict on every flight.  A second writer moves the reported
        value; it cannot hold it still, so a frozen one is an absent
        one.  This is what spares a list of misreporting aircraft."""
        m = AxisJitterMonitor()
        t = 0.0
        for i in range(200):
            # command sweeps the full range, reported never budges
            cmd = -1.0 + 2.0 * (i % 50) / 49.0
            m.sample('elevator', 0.0, cmd, now=t)
            t += 1 / 60.0
            m.poll(now=t)
        m.poll(now=t + 10.0)
        assert m.contended_axes() == []

    def test_helicopter_axes_are_confirmed(self):
        """Confirmed on two helicopters from different developers, every
        axis at zero lag with mean error from 0.00003 to 0.00301 against
        a 0.005 threshold.  They convict like any fixed-wing axis; drop
        one back out of the verified set to silence it."""
        from telemffb.utils.AxisJitter import (AXIS_EVENTS,
                                              VERIFIED_AXIS_EVENTS)
        for event in ('AXIS_CYCLIC_LONGITUDINAL_SET',
                      'AXIS_CYCLIC_LATERAL_SET',
                      'AXIS_COLLECTIVE_SET', 'ROTOR_AXIS_TAIL_ROTOR_SET'):
            assert event in AXIS_EVENTS
            assert event in VERIFIED_AXIS_EVENTS

    def test_the_trim_axis_is_too(self):
        self._record('AXIS_ELEV_TRIM_SET', -16384)
        assert self._take('elev_trim') == pytest.approx(1.0)

    def test_events_that_are_not_axes_are_ignored(self):
        self._record('ROTOR_TRIM_RESET', 1)
        self._record('AUTO_THROTTLE_DISCONNECT', 1)
        assert self._take('elevator') is None

    def test_a_custom_axis_variable_is_left_uncovered_rather_than_guessed(self):
        """Its range is the aircraft's own, so the value could not be
        turned back into the sense the reported position is in.  Absent is
        a gap; wrongly scaled would be a false finding."""
        self._record('L:MY_CUSTOM_AXIS', -16384)
        assert self._take('elevator') is None

    def test_a_command_is_taken_once(self):
        self._record('AXIS_ELEVATOR_SET', -16384)
        assert self._take('elevator') is not None
        assert self._take('elevator') is None

    def test_a_malformed_value_does_not_raise(self):
        self._record('AXIS_ELEVATOR_SET', 'not a number')
        assert self._take('elevator') is None

    def test_a_restart_drops_anything_uncollected(self):
        from telemffb.utils.AxisJitter import forget_axis_commands
        self._record('AXIS_ELEVATOR_SET', -16384)
        forget_axis_commands()
        assert self._take('elevator') is None


class TestCustomAxisScaling:
    """An aircraft configured with a custom axis may send on a standard
    event name at a non-standard range.

    Recording happens at the send, which sees only the scaled value and
    assumes the usual range, so the ratio has to be applied where the
    configuration is known.  Reading 4096 as 16384 would put the residual
    out fourfold - a false finding, not a missing one.
    """

    class _Aircraft:
        """Only the parts of the aircraft the correction consults."""
        enable_custom_x_axis = False
        enable_custom_y_axis = False
        raw_x_axis_scale = 16384
        raw_y_axis_scale = 16384

        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def _scale(self, side, **kw):
        from telemffb.sim.msfs_xp.Aircraft import Aircraft
        return Aircraft._probe_command_scale(self._Aircraft(**kw), side)

    def test_the_usual_range_needs_no_correction(self):
        assert self._scale('x') == 1.0

    def test_a_custom_axis_at_the_usual_range_needs_none_either(self):
        assert self._scale('x', enable_custom_x_axis=True,
                           raw_x_axis_scale=16384) == 1.0

    def test_a_narrower_range_is_corrected_by_its_ratio(self):
        assert self._scale('x', enable_custom_x_axis=True,
                           raw_x_axis_scale=4096) == pytest.approx(4.0)
        assert self._scale('y', enable_custom_y_axis=True,
                           raw_y_axis_scale=100) == pytest.approx(163.84)

    def test_the_raw_float_range_is_corrected_too(self):
        assert self._scale('y', enable_custom_y_axis=True,
                           raw_y_axis_scale=1) == pytest.approx(16384.0)

    def test_an_unusable_range_leaves_the_axis_uncovered(self):
        """Better no reading than one scaled by a guess."""
        assert self._scale('x', enable_custom_x_axis=True,
                           raw_x_axis_scale=0) is None
        assert self._scale('x', enable_custom_x_axis=True,
                           raw_x_axis_scale='') is None

    def test_the_trim_axis_has_no_custom_setting_to_consult(self):
        assert self._scale(None) == 1.0


class TestUnverifiedMappings:
    """An axis whose reported variable has not been confirmed against a
    flight is measured and never judged.

    The helicopter events are the case: whether the simulator reflects
    cyclic in the aileron and elevator readings, and whether the tail-rotor
    pedal reads -1..1 or 0..1, are both unconfirmed.  A wrong mapping leaves
    a standing residual indistinguishable from a second writer, which would
    manufacture exactly the false report the trial exists to measure.
    """

    def setup_method(self):
        from telemffb.utils.AxisJitter import forget_axis_commands
        forget_axis_commands()

    def _verified(self, event):
        from telemffb.utils.AxisJitter import (record_axis_command,
                                              take_axis_command, AXIS_EVENTS)
        record_axis_command(event, -8192)
        return take_axis_command(AXIS_EVENTS[event])[1]

    def test_the_confirmed_fixed_wing_events_may_be_judged(self):
        for event in ('AXIS_ELEVATOR_SET', 'AXIS_AILERONS_SET',
                      'AXIS_RUDDER_SET'):
            assert self._verified(event) is True

    def test_the_trim_event_may_not(self):
        assert self._verified('AXIS_ELEV_TRIM_SET') is False

    def test_a_provisional_axis_reaches_no_verdict(self):
        cmd = [flying(i) for i in range(200)]
        m = AxisJitterMonitor(window_s=2.0, report_s=1e9, check_s=1.0)
        m.set_provisional('elevator', True)
        for i in range(200):
            now = i * FRAME
            m.sample('elevator', cmd[i] + (0.45 if i % 2 else 0.0), cmd[i],
                     now=now)
            m.check(now)
        assert m.contended_axes() == []

    def test_the_same_axis_confirmed_does(self):
        cmd = [flying(i) for i in range(200)]
        m = AxisJitterMonitor(window_s=2.0, report_s=1e9, check_s=1.0)
        m.set_provisional('elevator', False)
        for i in range(200):
            now = i * FRAME
            m.sample('elevator', cmd[i] + (0.45 if i % 2 else 0.0), cmd[i],
                     now=now)
            m.check(now)
        assert m.contended_axes() == ['elevator']


class TestWhatIsVisibleAtInfo:
    """What a normal log shows, with debug off.

    Only two things earn a normal line: a finding, and an occasional
    sighting of an axis still waiting to be confirmed - which cannot be
    checked at all if it is visible only under debug, and debug on a normal
    flight is far too much to read.
    """

    def _lines(self, axis_provisional, contested, reports=25):
        import logging as lg
        cmd = [flying(i) for i in range(200)]
        m = AxisJitterMonitor(window_s=2.0, report_s=5.0, check_s=1.0)
        m.set_provisional('elevator', axis_provisional)
        out = {lg.INFO: 0, lg.DEBUG: 0}

        class Sink(lg.Handler):
            def emit(self, record):
                if record.levelno in out:
                    out[record.levelno] += 1

        sink = Sink()
        root = lg.getLogger()
        root.addHandler(sink)
        old = root.level
        root.setLevel(lg.DEBUG)
        try:
            now = 0.0
            m.log_if_due(now=now)          # arms the interval
            for i in range(200):
                now = i * FRAME
                offset = (0.45 if i % 2 else 0.0) if contested else 0.0
                m.sample('elevator', cmd[i] + offset, cmd[i], now=now)
                m.check(now)
            for r in range(reports):       # drive the report cadence
                m.log_if_due(now=now + (r + 1) * 5.0)
        finally:
            root.removeHandler(sink)
            root.setLevel(old)
        return out

    def test_a_clean_confirmed_axis_never_reaches_info(self):
        import logging as lg
        assert self._lines(False, contested=False)[lg.INFO] == 0

    def test_a_finding_does_every_time(self):
        import logging as lg
        assert self._lines(False, contested=True)[lg.INFO] > 0

    def test_an_unconfirmed_axis_is_visible_without_debug(self):
        import logging as lg
        assert self._lines(True, contested=False)[lg.INFO] > 0

    def test_but_only_now_and_then(self):
        """Roughly a line a minute, not one every report."""
        import logging as lg
        seen = self._lines(True, contested=False, reports=24)
        assert seen[lg.INFO] <= 3

    def test_an_unconfirmed_axis_never_reports_a_finding(self):
        m = AxisJitterMonitor(window_s=2.0, report_s=1e9, check_s=1.0)
        m.set_provisional('elevator', True)
        cmd = [flying(i) for i in range(200)]
        for i in range(200):
            now = i * FRAME
            m.sample('elevator', cmd[i] + (0.45 if i % 2 else 0.0), cmd[i],
                     now=now)
            m.check(now)
        assert m.contended_axes() == []


class TestTheUnverifiedReading:
    """An unconfirmed axis states its own reading rather than leaving a
    number to be interpreted.

    It exists to answer one question - does the reported variable follow
    this control at all - and a line that needs a threshold looked up to
    read is a line that gets misread.
    """

    def _line(self, error):
        import logging as lg
        cmd = [flying(i) for i in range(200)]
        m = AxisJitterMonitor(window_s=2.0, report_s=5.0, check_s=1.0)
        m.set_provisional('elev_trim', True)
        m.log_if_due(now=0.0)
        for i in range(200):
            now = i * FRAME
            m.sample('elev_trim', cmd[i] + error, cmd[i], now=now)
            m.check(now)
        said = []

        class Sink(lg.Handler):
            def emit(self, record):
                said.append(record.getMessage())

        sink = Sink()
        root = lg.getLogger()
        root.addHandler(sink)
        try:
            m.log_if_due(now=100.0)
        finally:
            root.removeHandler(sink)
        return said[0] if said else ''

    def test_a_mapping_that_follows_the_control_says_so(self):
        assert 'mapping holds' in self._line(0.0)

    def test_one_that_does_not_says_that_instead(self):
        assert 'does NOT hold' in self._line(0.85)

    def test_a_confirmed_axis_carries_no_such_note(self):
        import logging as lg
        cmd = [flying(i) for i in range(200)]
        m = AxisJitterMonitor(window_s=2.0, report_s=5.0, check_s=1.0)
        m.log_if_due(now=0.0)
        for i in range(200):
            now = i * FRAME
            m.sample('elevator', cmd[i] + (0.45 if i % 2 else 0.0), cmd[i],
                     now=now)
            m.check(now)
        said = []

        class Sink(lg.Handler):
            def emit(self, record):
                said.append(record.getMessage())

        sink = Sink()
        root = lg.getLogger()
        root.addHandler(sink)
        try:
            m.log_if_due(now=100.0)
        finally:
            root.removeHandler(sink)
        assert said and 'mapping' not in said[0]


class TestAMappingNeedsMovementToJudge:
    """A reported variable stuck at zero agrees perfectly with a command
    that is also near zero.

    That is how a helicopter's collective and tail rotor first read as
    confirmed: both were measured while barely moving, so the readings were
    vacuous rather than passing.  Nothing is concluded until the control
    has been worked through some of its range.
    """

    def _note(self, travel, error):
        import logging as lg
        cmd = [travel * math.sin(2 * math.pi * 0.5 * i * FRAME)
               for i in range(200)]
        m = AxisJitterMonitor(window_s=2.0, report_s=5.0, check_s=1.0)
        m.set_provisional('elev_trim', True)
        m.log_if_due(now=0.0)
        for i in range(200):
            now = i * FRAME
            m.sample('elev_trim', cmd[i] + error, cmd[i], now=now)
            m.check(now)
        said = []

        class Sink(lg.Handler):
            def emit(self, record):
                said.append(record.getMessage())

        sink = Sink()
        root = lg.getLogger()
        root.addHandler(sink)
        try:
            m.log_if_due(now=100.0)
        finally:
            root.removeHandler(sink)
        return said[0] if said else ''

    def test_a_still_control_concludes_nothing(self):
        """Even though the residual is zero, which would otherwise read as
        a mapping that holds."""
        assert 'inconclusive' in self._note(travel=0.001, error=0.0)

    def test_a_still_control_with_a_wrong_mapping_also_concludes_nothing(self):
        assert 'inconclusive' in self._note(travel=0.001, error=0.85)

    def test_a_worked_control_that_tracks_confirms_the_mapping(self):
        assert 'mapping holds' in self._note(travel=0.5, error=0.0)

    def test_a_worked_control_that_does_not_track_rejects_it(self):
        assert 'does NOT hold' in self._note(travel=0.5, error=0.85)
