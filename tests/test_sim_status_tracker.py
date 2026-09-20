"""Unit tests for SimStatusTracker (telemffb/state/sim_status.py).

Moved off MainWindow's on_update_telemetry/on_telemetry_timeout/
on_first_sim_frame/on_sim_exited: the error-onset/hold/clear state machine,
the timed-out flag, and the exception-tracker cleanup that used to be
inline there. These tests exercise the state machine in isolation with a
fake app_state, a fake exception tracker and an injectable clock - no real
MainWindow, no real 3-second sleep.
"""
import pytest

from telemffb.state.sim_status import SimStatusTracker

pytestmark = pytest.mark.unit


class FakeAppState:
    def __init__(self):
        self.calls = []  # list of (state, source, message)

    def set_sim_status(self, state, source, message=None):
        self.calls.append((state, source, message))

    def reset_sim_status(self):
        self.calls.append(('reset', None, None))


class FakeExceptionTracker:
    def __init__(self):
        self.removed = []

    def remove_matching(self, message):
        self.removed.append(message)
        return 1


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def app_state():
    return FakeAppState()


@pytest.fixture
def exception_tracker():
    return FakeExceptionTracker()


@pytest.fixture
def tracker(app_state, exception_tracker, clock):
    return SimStatusTracker(app_state, exception_tracker, clock=clock)


class TestPushStatus:
    def test_none_source_is_a_no_op(self, tracker, app_state):
        tracker.push_status(None, paused=True)
        assert app_state.calls == []

    def test_reports_running_paused_error(self, tracker, app_state):
        tracker.push_status('DCS')
        tracker.push_status('DCS', paused=True)
        tracker.push_status('DCS', error=True, message='boom')
        assert app_state.calls == [
            ('running', 'DCS', None),
            ('paused', 'DCS', None),
            ('error', 'DCS', 'boom'),
        ]


class TestOnFrameErrorOnset:
    def test_error_reported_once_not_repeated_on_every_error_frame(self, tracker, app_state):
        tracker.on_frame({'src': 'DCS', 'error': 'bad config'})
        tracker.on_frame({'src': 'DCS', 'error': 'bad config'})
        tracker.on_frame({'src': 'DCS', 'error': 'bad config'})
        assert app_state.calls == [('error', 'DCS', 'bad config')]
        assert tracker.error_state is True
        assert tracker.flagged_error_msgs == {'bad config'}

    def test_error_free_frame_within_hold_window_does_not_clear(self, tracker, app_state, clock):
        tracker.on_frame({'src': 'DCS', 'error': 'bad config'})
        clock.advance(1.0)
        app_state.calls.clear()
        tracker.on_frame({'src': 'DCS'})  # no 'error' key
        assert app_state.calls == []
        assert tracker.error_state is True

    def test_clears_after_the_hold_window_elapses(self, tracker, app_state, clock):
        tracker.on_frame({'src': 'DCS', 'error': 'bad config'})
        clock.advance(SimStatusTracker.ERROR_CLEAR_HOLD_S)
        app_state.calls.clear()
        tracker.on_frame({'src': 'DCS'})
        assert app_state.calls == [('running', 'DCS', None)]
        assert tracker.error_state is False
        assert tracker.telemetry_timed_out is False

    def test_clear_removes_flagged_messages_from_the_exception_tracker(
            self, tracker, exception_tracker, clock):
        tracker.on_frame({'src': 'DCS', 'error': 'bad config'})
        clock.advance(SimStatusTracker.ERROR_CLEAR_HOLD_S)
        tracker.on_frame({'src': 'DCS'})
        assert exception_tracker.removed == ['bad config']
        assert tracker.flagged_error_msgs == set()

    def test_clear_only_fires_when_timed_out_or_error_state(self, tracker, app_state):
        """A clean frame with neither telemetry_timed_out nor error_state
        set is the routine running case - nothing to clear, no re-push."""
        tracker.telemetry_timed_out = False
        tracker.error_state = False
        tracker.on_frame({'src': 'DCS'})
        assert app_state.calls == []


class TestOnFirstFrame:
    def test_flips_waiting_to_running(self, tracker, app_state):
        tracker.on_first_frame('DCS')
        assert app_state.calls == [('running', 'DCS', None)]

    def test_guarded_while_in_error_state(self, tracker, app_state):
        tracker.on_frame({'src': 'DCS', 'error': 'bad config'})
        app_state.calls.clear()
        tracker.on_first_frame('DCS')
        assert app_state.calls == []


class TestOnTimeout:
    def test_pauses_when_no_error(self, tracker, app_state):
        tracker.on_timeout('DCS')
        assert app_state.calls == [('paused', 'DCS', None)]
        assert tracker.telemetry_timed_out is True

    def test_stays_error_instead_of_pausing(self, tracker, app_state):
        tracker.on_frame({'src': 'DCS', 'error': 'bad config'})
        app_state.calls.clear()
        tracker.on_timeout('DCS')
        assert app_state.calls == []  # not overwritten with 'paused'
        assert tracker.telemetry_timed_out is True


class TestOnSimExited:
    def test_resets_the_timed_out_flag(self, tracker):
        tracker.telemetry_timed_out = True
        tracker.on_sim_exited()
        assert tracker.telemetry_timed_out is False

    def test_forgets_the_reported_status(self, tracker, app_state):
        # the widget is reset directly on exit; AppState must forget too
        tracker.on_sim_exited()
        assert app_state.calls[-1] == ('reset', None, None)


class TestSeveralErrorsAtOnce:
    """A frame carries every flagged config error, newline separated.

    The state machine used to track only the first message it ever saw and
    gate onset on error_state, so a second config error never reached the
    exception tracker and fixing the first cleared nothing - the indicator
    kept showing a message the user had already dealt with.
    """

    A = "Force trim enabled but buttons not configured"
    B = "Low Hydraulic Pressure Effect is enabled but needs the Damper Override"

    def _frame(self, *messages):
        return {"src": "MSFS", "error": "\n".join(messages)}

    def test_every_message_is_held(self, tracker):
        tracker.on_frame(self._frame(self.A, self.B))
        assert tracker.flagged_error_msgs == {self.A, self.B}
        assert tracker.error_state is True

    def test_the_indicator_shows_one_at_a_time(self, tracker, app_state):
        tracker.on_frame(self._frame(self.A, self.B))
        assert [c[0] for c in app_state.calls] == ["error"]
        assert app_state.calls[0][2] == self.A

    def test_repeated_frames_do_not_re_push(self, tracker, app_state):
        for _ in range(5):
            tracker.on_frame(self._frame(self.A, self.B))
        assert len(app_state.calls) == 1

    def test_fixing_the_first_moves_the_indicator_to_the_second(
            self, tracker, app_state, clock, exception_tracker):
        tracker.on_frame(self._frame(self.A, self.B))
        clock.advance(SimStatusTracker.ERROR_CLEAR_HOLD_S)
        tracker.on_frame(self._frame(self.B))

        assert exception_tracker.removed == [self.A]
        assert tracker.flagged_error_msgs == {self.B}
        assert tracker.error_state is True
        assert [(c[0], c[2]) for c in app_state.calls] == [
            ("error", self.A), ("error", self.B)]

    def test_fixing_the_first_does_not_clear_the_status(
            self, tracker, app_state, clock):
        """The whole point: one error resolved, another outstanding, so the
        app status stays in error rather than reporting running."""
        tracker.on_frame(self._frame(self.A, self.B))
        clock.advance(SimStatusTracker.ERROR_CLEAR_HOLD_S)
        tracker.on_frame(self._frame(self.B))
        assert "running" not in [c[0] for c in app_state.calls]

    def test_the_second_error_survives_the_first_clearing(
            self, tracker, exception_tracker, clock):
        """B keeps being seen, so its hold never elapses."""
        tracker.on_frame(self._frame(self.A, self.B))
        for _ in range(4):
            clock.advance(SimStatusTracker.ERROR_CLEAR_HOLD_S)
            tracker.on_frame(self._frame(self.B))
        assert exception_tracker.removed == [self.A]
        assert tracker.flagged_error_msgs == {self.B}

    def test_fixing_both_clears_everything(
            self, tracker, app_state, exception_tracker, clock):
        tracker.on_frame(self._frame(self.A, self.B))
        clock.advance(SimStatusTracker.ERROR_CLEAR_HOLD_S)
        tracker.on_frame({"src": "MSFS"})

        assert sorted(exception_tracker.removed) == sorted([self.A, self.B])
        assert tracker.flagged_error_msgs == set()
        assert tracker.error_state is False
        assert app_state.calls[-1][0] == "running"

    def test_an_error_arriving_later_is_tracked_too(self, tracker, app_state):
        """Onset is no longer gated on error_state, so a second condition
        appearing while the first is up still reaches the tracker."""
        tracker.on_frame(self._frame(self.A))
        tracker.on_frame(self._frame(self.A, self.B))
        assert tracker.flagged_error_msgs == {self.A, self.B}
        # the indicator stays on A: it is still outstanding
        assert len(app_state.calls) == 1

    def test_a_single_error_behaves_as_before(
            self, tracker, app_state, exception_tracker, clock):
        tracker.on_frame(self._frame(self.A))
        assert tracker.error_state is True
        clock.advance(SimStatusTracker.ERROR_CLEAR_HOLD_S)
        tracker.on_frame({"src": "MSFS"})
        assert exception_tracker.removed == [self.A]
        assert tracker.error_state is False

    def test_blank_segments_are_ignored(self, tracker):
        """Messages are joined, and some carry their own trailing newlines."""
        tracker.on_frame({"src": "MSFS", "error": f"{self.A}\n\n\n{self.B}\n"})
        assert tracker.flagged_error_msgs == {self.A, self.B}


class TestFlagErrorAccumulates:
    """AircraftEffectUtilsBase.flag_error is the producer side: the last
    effect to run used to win a single slot, hiding every other error."""

    def _aircraft(self):
        from telemffb.sim.BaseTelemetryData import BaseTelemetryData
        from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase

        ac = AircraftEffectUtilsBase.__new__(AircraftEffectUtilsBase)
        ac._telem_data = BaseTelemetryData({"FFBType": "joystick"})
        ac._ipc_telem = {}
        return ac

    def test_two_errors_both_survive(self):
        ac = self._aircraft()
        ac.flag_error("first problem")
        ac.flag_error("second problem")
        assert ac.telem_data["error"] == "first problem\nsecond problem"

    def test_the_same_error_twice_is_not_duplicated(self):
        ac = self._aircraft()
        ac.flag_error("same problem")
        ac.flag_error("same problem")
        assert ac.telem_data["error"] == "same problem"

    def test_the_first_error_reads_cleanly(self):
        """'error' is not a declared telemetry field, so the accumulator has
        to reach it by item access or the first call would raise."""
        ac = self._aircraft()
        ac.flag_error("only problem")
        assert ac.telem_data["error"] == "only problem"
