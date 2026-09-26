"""The axis contention probe: jolts, and the verdict drawn from them.

A binding left mapped in the simulator shows up as the reported position
jumping a large step in one frame that the command did not make, over and
over.  Everything the simulator does to an axis on its own moves it
smoothly.  The waveforms below are the shapes the field captures showed.
"""
import math

import pytest

from telemffb.utils.AxisJitter import AxisJitterMonitor

pytestmark = [pytest.mark.unit]

FRAME = 1.0 / 60.0


def flying(i):
    """A command moving the way a captured one did: a control being flown,
    with a little rumble on it."""
    return (0.3 * math.sin(2 * math.pi * 0.5 * i * FRAME)
            + 0.004 * math.sin(2 * math.pi * 37.0 * i * FRAME))


def run(pairs, axis='elevator', monitor=None, step=FRAME):
    """Feed (reported, commanded) pairs frame by frame, judging as it goes."""
    m = monitor or AxisJitterMonitor(report_s=1e9)
    for i, (reported, commanded) in enumerate(pairs):
        now = i * step
        m.sample(axis, reported, commanded, now=now)
        m.check(now)
    return m


def jolts(pairs):
    """Every jolt the probe counts over the pairs, windows ignored."""
    m = AxisJitterMonitor(window_s=1e9, report_s=1e9)
    for i, (reported, commanded) in enumerate(pairs):
        m.sample('elevator', reported, commanded, now=i * FRAME)
    return m._jolts.get('elevator', 0)


class TestWhatJolts:
    def test_a_binding_holding_the_axis_yanked_by_telemffb_jolts(self):
        """The captured shape: the other writer holds -0.52 while the
        command sits at +0.33, and every so often TelemFFB's write gets
        through, jumping the report 0.35 before the simulator pulls it
        back over several frames."""
        pairs = []
        for i in range(600):
            since = i % 25
            reported = -0.52 + 0.35 * (0.7 ** since)
            pairs.append((reported, 0.33))
        assert jolts(pairs) >= 20

    def test_two_writers_alternating_every_frame_jolt(self):
        pairs = [(-0.195 if i % 2 else 0.069, -0.195) for i in range(120)]
        assert jolts(pairs) > 100

    def test_the_command_moving_the_report_does_not(self):
        cmd = [0.0] * 30 + [0.5] * 30
        assert jolts([(c, c) for c in cmd]) == 0

    def test_a_report_a_frame_late_on_a_flick_does_not(self):
        cmd = [0.0] * 30 + [0.5] * 30
        assert jolts(list(zip([0.0] + cmd[:-1], cmd))) == 0

    def test_a_scaled_report_does_not(self):
        """The HPG H145 reports its cyclic at 0.81 of the command."""
        cmd = [flying(i) for i in range(600)]
        assert jolts([(0.81 * c, c) for c in cmd]) == 0

    def test_a_lagging_report_does_not(self):
        """A cold-and-dark helicopter: no hydraulic pressure, the report
        creeping after the command for seconds."""
        cmd = [0.0] * 30 + [0.6] * 300
        reported, pairs = 0.0, []
        for c in cmd:
            reported += (c - reported) * 0.03
            pairs.append((reported, c))
        assert jolts(pairs) == 0

    def test_frames_across_a_stall_are_not_compared(self):
        m = AxisJitterMonitor(window_s=1e9, report_s=1e9)
        m.sample('elevator', 0.0, 0.0, now=0.0)
        m.sample('elevator', 0.4, 0.0, now=0.5)
        assert m._jolts.get('elevator', 0) == 0

    def test_frames_the_simulator_clamps_are_not_compared(self):
        """A large virtual offset pushes the command past full scale, and
        the limit reported back is not evidence of anything."""
        pairs = [(1.0 if i % 2 else 0.8, 1.5) for i in range(60)]
        assert jolts(pairs) == 0

    def test_an_axis_not_being_driven_is_not_compared(self):
        pairs = [(0.3 if i % 2 else 0.0, None) for i in range(60)]
        assert jolts(pairs) == 0


class TestTheVerdict:
    def _flop(self, frames):
        """Two writers alternating every frame for ``frames`` frames, then
        a clean axis."""
        cmd = [flying(i) for i in range(900)]
        return [(cmd[i] + (0.26 if i % 2 and i < frames else 0.0), cmd[i])
                for i in range(900)]

    def test_a_sustained_flop_is_contended(self):
        assert run(self._flop(900)).contended_axes() == ['elevator']

    def test_one_burst_is_not(self):
        """A hitch or a crash jolts once; a binding keeps flopping."""
        assert run(self._flop(90)).contended_axes() == []

    def test_a_clean_axis_is_not(self):
        cmd = [flying(i) for i in range(900)]
        assert run([(c, c) for c in cmd]).contended_axes() == []

    def test_an_unconfirmed_axis_is_never_contended(self):
        m = AxisJitterMonitor(report_s=1e9)
        m.set_provisional('elev_trim', True)
        assert run(self._flop(900), axis='elev_trim', monitor=m).contended_axes() == []

    def test_the_finding_is_cleared_after_each_report(self):
        m = run(self._flop(900))
        m.due(now=0.0)
        m.log_if_due(now=1e9)
        assert m.contended_axes() == []


class TestLogLevels:
    """A finding is a normal log line; a clean axis stays at DEBUG, so an
    ordinary flight is quiet at INFO."""

    def _levels(self, contended, provisional=False):
        import logging as lg
        cmd = [flying(i) for i in range(900)]
        pairs = [(cmd[i] + (0.26 if contended and i % 2 else 0.0), cmd[i])
                 for i in range(900)]
        m = AxisJitterMonitor(report_s=1e9)
        if provisional:
            m.set_provisional('elevator', True)
        m.log_if_due(now=0.0)
        run(pairs, monitor=m)
        levels = []

        class Sink(lg.Handler):
            def emit(self, record):
                levels.append(record.levelno)

        sink = Sink()
        root = lg.getLogger()
        root.addHandler(sink)
        old = root.level
        root.setLevel(lg.DEBUG)
        try:
            m.log_if_due(now=1e9)
        finally:
            root.removeHandler(sink)
            root.setLevel(old)
        return levels

    def test_a_clean_axis_logs_at_debug(self):
        import logging as lg
        assert self._levels(False) == [lg.DEBUG]

    def test_a_finding_logs_at_info(self):
        import logging as lg
        assert self._levels(True) == [lg.INFO]

    def test_an_unconfirmed_axis_is_visible_at_info_on_the_first_report(self):
        import logging as lg
        assert self._levels(False, provisional=True) == [lg.INFO]


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
