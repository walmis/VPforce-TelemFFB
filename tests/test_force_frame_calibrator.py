"""ForceFrameCalibrator against a simulated DirectInput base.

Headless: the fake base implements the calibrator's device interface and
a first-order position model, so a run plays out in simulated time.  A
base is parametrized by how it renders what it is sent: which axes it
mirrors for springs, how it turns or reflects a force's direction, and
the defects the calibrator must refuse (reversed input, an anti-restoring
spring, swapped condition axes, a base that renders nothing).
"""
import math

import pytest

from telemffb.calibration.force_frame import (
    OUTCOMES, AxisSpring, ForceFrameCalibrator, Stage, Step, derive_frame,
    push_angle, snap_quadrant,
)
from telemffb.hw.ffb_dinput import ForceFrame

pytestmark = [pytest.mark.unit, pytest.mark.joystick]

DT = 0.02


class FakeBase:
    """A DirectInput base as the calibrator sees it.

    ``mirror_x`` / ``mirror_y``: a spring on that axis centers on the
    negated offset.  A constant force sent at angle ``a`` pushes at
    ``sense * a + rotation``.  ``input_mirror_y`` reports -y (the
    position is reversed, not the forces).  ``anti_restoring`` makes
    springs push away from their center; ``swap_condition_axes`` applies
    the X block to Y and vice versa; ``renders=False`` plays nothing.
    """

    GAIN = 0.3

    def __init__(self, mirror_x=False, mirror_y=False, sense=1, rotation=0,
                 input_mirror_x=False, input_mirror_y=False, anti_restoring=False,
                 swap_condition_axes=False, renders=True):
        self.mirror = (mirror_x, mirror_y)
        self.sense = sense
        self.rotation = rotation
        self.input_mirror_x = input_mirror_x
        self.input_mirror_y = input_mirror_y
        self.anti_restoring = anti_restoring
        self.swap_condition_axes = swap_condition_axes
        self.renders = renders
        self.pos = [0.0, 0.0]
        self.frame = None
        self.blocks = [None, None]      # (center, coefficient, saturation)
        self.push = (0.0, 0.0)
        self.hand = None                # physical position the user holds
        self.released = False
        self.stopped = 0
        self.frames_set = []
        self.fault_on = None            # a method name that raises

    # --- the calibrator's device interface

    def _maybe_fault(self, name):
        if self.fault_on == name:
            raise RuntimeError("bridge fault in %s" % name)

    def read_axes(self):
        x, y = self.pos
        return (-x if self.input_mirror_x else x, -y if self.input_mirror_y else y)

    def set_frame(self, frame):
        self._maybe_fault('set_frame')
        self.frame = frame
        self.frames_set.append(frame)

    def spring(self, x, y):
        self._maybe_fault('spring')
        frame = self.frame or ForceFrame()
        for axis, block in enumerate((x, y)):
            if block is None:
                self.blocks[axis] = None
                continue
            offset = block.offset
            if (frame.mirror_x, frame.mirror_y)[axis]:
                offset = -offset          # what the device is sent
            if self.mirror[axis]:
                offset = -offset          # what the device does with it
            self.blocks[axis] = (offset, block.coefficient, block.saturation)

    def constant(self, magnitude, direction_deg):
        self._maybe_fault('constant')
        frame = self.frame or ForceFrame()
        sent = frame.direction(direction_deg)
        psi = math.radians((self.sense * sent + self.rotation) % 360)
        self.push = (-magnitude * math.sin(psi), magnitude * math.cos(psi))

    def stop_all(self):
        self.stopped += 1
        self.blocks = [None, None]
        self.push = (0.0, 0.0)

    def release(self):
        self.released = True
        self.stop_all()

    # --- physics

    def tick(self):
        if self.hand is not None:
            self.pos = list(self.hand)
            return
        if not self.renders:
            return          # a dead base: the stick stays where it was left
        force = [0.0, 0.0]
        for axis, block in enumerate(self.blocks):
            if block is None:
                continue
            center, k, sat = block
            f = max(-sat, min(sat, -k * (self.pos[axis] - center)))
            if self.anti_restoring:
                f = -f
            target = 1 - axis if self.swap_condition_axes else axis
            force[target] += f
        force[0] += self.push[0]
        force[1] += self.push[1]
        for axis in (0, 1):
            self.pos[axis] = max(-1.0, min(1.0, self.pos[axis] + self.GAIN * force[axis]))

    def physical_push(self, frame, angle):
        """Where a force sent at ``angle`` through ``frame`` moves the stick."""
        saved = self.frame
        self.frame = frame
        self.constant(0.3, angle)
        px, py = self.push
        self.push = (0.0, 0.0)
        self.frame = saved
        return snap_quadrant(push_angle(px, py), 1.0)


def obedient_user(cal, base):
    """Follows the input-check prompts: gives the stick one nudge toward
    center when asked, pushes when asked, lets go otherwise."""
    if cal.awaiting_center:
        if getattr(base, 'nudged_for', None) is not cal.step:
            base.nudged_for = cal.step
            base.hand = None
            base.pos = [0.05, 0.05]
    elif cal.awaiting_push:
        base.hand = (0.0, -0.9) if cal.step is Step.INPUT_FORWARD else (0.9, 0.0)
    else:
        base.hand = None


def run(cal, base, seconds=60.0, user=obedient_user):
    t = 100.0
    if cal.step is Step.IDLE:
        cal.start(t)
    end = t + seconds
    while t < end and not cal.finished:
        user(cal, base)
        base.tick()
        cal.advance(t)
        t += DT
    return t


def frame_corrects(base, frame):
    """``frame`` makes every quadrant push and both springs standard."""
    for angle in (0, 90, 180, 270):
        if base.physical_push(frame, angle) != angle:
            return False
    for axis in (0, 1):
        base.frame = frame
        base.pos = [0.0, 0.0]
        base.spring(AxisSpring(0.4, 0.6, 0.3) if axis == 0 else None,
                    AxisSpring(0.4, 0.6, 0.3) if axis == 1 else None)
        for _ in range(100):
            base.tick()
        if base.pos[axis] < 0.2:
            return False
    return True


class TestHelpers:
    def test_push_angle_follows_the_direction_convention(self):
        assert push_angle(0.0, 1.0) == 0.0        # aft
        assert push_angle(-1.0, 0.0) == 90.0      # left
        assert push_angle(0.0, -1.0) == 180.0     # forward
        assert push_angle(1.0, 0.0) == 270.0      # right

    def test_snap_quadrant_within_tolerance_only(self):
        assert snap_quadrant(10.0, 25.0) == 0
        assert snap_quadrant(350.0, 25.0) == 0
        assert snap_quadrant(100.0, 25.0) == 90
        assert snap_quadrant(45.0, 25.0) is None

    @pytest.mark.parametrize("mx,my,psi0,psi90,expected", [
        (False, False, 0, 90, ForceFrame()),
        (False, True, 270, 180, ForceFrame(False, True, 90)),   # Brunner FFB-G
        (False, False, 90, 180, ForceFrame(False, False, 270)),
        (True, True, 180, 270, ForceFrame(True, True, 0)),
    ])
    def test_derive_frame(self, mx, my, psi0, psi90, expected):
        frame, why = derive_frame(mx, my, psi0, psi90)
        assert frame == expected and why == ""

    def test_derive_frame_refuses_directions_that_do_not_turn(self):
        frame, why = derive_frame(False, False, 0, 180)
        assert frame is None and why

    def test_derive_frame_refuses_a_reflection_the_springs_lack(self):
        frame, why = derive_frame(False, False, 0, 270)
        assert frame is None and why


class TestSuccessfulRuns:
    @pytest.mark.parametrize("base_kwargs,expected", [
        ({}, ForceFrame()),
        ({'mirror_y': True, 'sense': -1, 'rotation': 270}, ForceFrame(False, True, 90)),
        ({'sense': 1, 'rotation': 90}, ForceFrame(False, False, 270)),
        ({'mirror_x': True, 'mirror_y': True, 'rotation': 180}, ForceFrame(True, True, 0)),
        ({'mirror_x': True, 'sense': -1, 'rotation': 0}, ForceFrame(True, False, 0)),
    ])
    def test_the_derived_frame_corrects_the_base(self, base_kwargs, expected):
        base = FakeBase(**base_kwargs)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.step is Step.DONE, cal.failure
        assert cal.result == expected
        assert frame_corrects(FakeBase(**base_kwargs), cal.result)

    def test_a_stick_resting_off_center_is_not_a_push(self):
        """A stick resting off center is centered by the spring first; the
        push is judged from where it settled."""
        base = FakeBase()
        base.pos = [0.0, 0.7]                      # resting well aft
        cal = ForceFrameCalibrator(base)
        cal.start(100.0)
        base.tick()
        cal.advance(100.0)
        assert cal.awaiting_center and not cal.finished
        run(cal, base)
        assert cal.step is Step.DONE, cal.failure

    def test_the_run_visits_every_step_in_order(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        seen = []

        def user(cal, base):
            if not seen or seen[-1] is not cal.step:
                seen.append(cal.step)
            obedient_user(cal, base)
        run(cal, base, user=user)
        seen.append(cal.step)
        assert seen == [Step.INPUT_FORWARD, Step.INPUT_RIGHT, Step.SPRING_X,
                        Step.SPRING_Y, Step.DIRECTION_0, Step.DIRECTION_90,
                        Step.VERIFY_180, Step.VERIFY_270, Step.PARKING,
                        Step.DONE]

    def test_the_device_is_restored_and_released_at_the_end(self):
        base = FakeBase(mirror_y=True, sense=-1, rotation=270)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.finished and base.released
        assert base.frames_set[-1] is None          # the stored frame is back
        assert base.push == (0.0, 0.0) and base.blocks == [None, None]

    def test_the_stick_is_centered_before_the_effects_stop(self):
        """A run ends under the centering spring: the stick is near center
        when the effects are released, however far the last step left it."""
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        at_release = []
        original = base.release

        def release():
            at_release.append(tuple(base.pos))
            original()
        base.release = release
        run(cal, base)
        assert cal.step is Step.DONE
        assert len(at_release) == 1
        assert max(abs(v) for v in at_release[0]) < 0.1

    def test_a_constant_force_never_plays_against_a_weak_spring(self):
        """Between steps the constant force fades while the centering
        spring stays at full stiffness; the spring is never restarted
        under a force that could run the stick to a stop."""
        base = FakeBase(mirror_y=True, sense=-1, rotation=270)
        cal = ForceFrameCalibrator(base)
        violations = []

        def user(cal, base):
            obedient_user(cal, base)
            if math.hypot(*base.push) > 0.02:
                blocks = base.blocks
                if not all(b is not None and b[1] >= 0.49 for b in blocks):
                    violations.append((cal.step, tuple(base.push), list(blocks)))
        run(cal, base, user=user)
        assert cal.step is Step.DONE, cal.failure
        assert violations == []
        assert max(abs(p) for p in base.pos) < 0.1

    def test_a_fading_force_keeps_its_direction(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        sent = []
        constant = base.constant

        def spying_constant(magnitude, direction_deg):
            sent.append((magnitude, direction_deg))
            constant(magnitude, direction_deg)
        base.constant = spying_constant
        run(cal, base)
        assert cal.step is Step.DONE
        for (m0, d0), (m1, d1) in zip(sent, sent[1:]):
            if m1 < m0 and m1 > 0:
                assert d1 == d0, (m0, d0, m1, d1)

    def test_stimuli_ramp_in_rather_than_step(self):
        """The first spring the base sees is weak and grows; so does the
        first constant force."""
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        coefficients, magnitudes = [], []
        spring, constant = base.spring, base.constant

        def spying_spring(x, y):
            if x is not None:
                coefficients.append(x.coefficient)
            spring(x, y)

        def spying_constant(magnitude, direction_deg):
            magnitudes.append(magnitude)
            constant(magnitude, direction_deg)
        base.spring, base.constant = spying_spring, spying_constant
        run(cal, base)
        assert cal.step is Step.DONE
        first = coefficients[:3]
        assert first == sorted(first) and first[0] < first[-1] < 0.5
        rising = [m for m in magnitudes if m > 0][:3]
        assert rising == sorted(rising) and rising[0] < rising[-1] < 0.3

    def test_verification_runs_through_the_candidate_frame(self):
        base = FakeBase(sense=1, rotation=90)
        cal = ForceFrameCalibrator(base)
        frames_while_verifying = set()

        def user(cal, base):
            if cal.step in (Step.VERIFY_180, Step.VERIFY_270) and base.push != (0.0, 0.0):
                frames_while_verifying.add(base.frame)
            obedient_user(cal, base)
        run(cal, base, user=user)
        assert cal.step is Step.DONE
        assert frames_while_verifying == {cal.result}

    def test_stage_states_track_progress(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        assert set(cal.stage_states().values()) == {'pending'}
        run(cal, base)
        assert set(cal.stage_states().values()) == {'done'}


class TestRefusals:
    def test_reversed_input_fails_the_input_check(self):
        base = FakeBase(input_mirror_y=True)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.step is Step.FAILED
        assert cal.stage_states()[Stage.INPUT] == 'failed'
        assert cal.result is None and base.released
        assert base.frames_set[-1] is None

    def test_both_pushes_are_read_before_the_input_verdict(self):
        """A reversed forward reading does not stop the check: the right
        push is read too, so one verdict names every axis that is wrong."""
        base = FakeBase(input_mirror_x=True, input_mirror_y=True)
        cal = ForceFrameCalibrator(base)
        pushes = []

        def user(cal, base):
            if cal.awaiting_push and cal.step not in pushes:
                pushes.append(cal.step)
            obedient_user(cal, base)
        run(cal, base, user=user)
        assert pushes == [Step.INPUT_FORWARD, Step.INPUT_RIGHT]
        assert cal.step is Step.FAILED
        assert cal.input_reads == {180: 0, 270: 90}        # forward read aft, right read left
        base = FakeBase(input_mirror_y=True)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.input_reads == {180: 0, 270: 270}       # only Y wrong

    def test_pushes_read_on_the_other_axis_are_a_swap(self):
        """Forward read as right and right read as forward is not a
        reversal: the verdict is the swapped-axes one."""
        base = FakeBase()
        swap = base.read_axes
        base.read_axes = lambda: tuple(reversed(swap()))
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.step is Step.FAILED
        assert cal.input_reads == {180: 90, 270: 0}        # forward read left, right read aft
        assert cal.failure == OUTCOMES['swapped_input'][1]
        base = FakeBase(input_mirror_y=True)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.failure == OUTCOMES['wrong_input'][1]

    def test_an_anti_restoring_spring_is_unsupported(self):
        """The input check's centering spring is the first spring the base
        plays, so the runaway shows there."""
        base = FakeBase(anti_restoring=True)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.step is Step.FAILED
        assert cal.stage_states()[Stage.INPUT] == 'failed'
        assert cal.result is None and base.released

    def test_swapped_condition_axes_are_unsupported(self):
        """Its centering spring cannot bring a released stick back, so the
        input check is where it shows."""
        base = FakeBase(swap_condition_axes=True)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.step is Step.FAILED
        assert cal.stage_states()[Stage.INPUT] == 'failed'
        assert base.released

    def test_a_stick_that_does_not_move_gets_a_firmer_retry_then_fails(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        springs_sent = []
        original = base.spring

        def spring(x, y):
            if x is not None and x.offset:
                springs_sent.append(x.coefficient)
            original(x, y)
        base.spring = spring

        def user(cal, base):
            if cal.step is Step.SPRING_X:
                base.hand = (0.0, 0.0)
            else:
                obedient_user(cal, base)
        run(cal, base, user=user)
        assert cal.step is Step.FAILED
        assert {0.6, 1.0} <= set(springs_sent)          # first try, then firmer
        assert max(springs_sent) == 1.0

    def test_a_hand_on_the_stick_is_interference_not_a_mirror(self):
        '''A stick held off center through the Y spring step never yields
        a mirror verdict: the center check and the spring's reach both
        name a hand instead, and the run stops on the held outcome.'''
        base = FakeBase()
        cal = ForceFrameCalibrator(base)

        def user(cal, base):
            if cal.step is Step.SPRING_Y:
                base.hand = (0.5, 0.6)
            else:
                obedient_user(cal, base)
        run(cal, base, user=user)
        assert cal.step is Step.FAILED
        assert cal.mirror_x is False and cal.mirror_y is None
        assert cal.failure == OUTCOMES['held'][1]

    def test_a_stick_held_still_after_moving_is_held_not_dead(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)

        def user(cal, base):
            if cal.step is Step.DIRECTION_90:
                base.hand = (0.0, 0.0)
            else:
                obedient_user(cal, base)
        run(cal, base, user=user)
        assert cal.step is Step.FAILED
        assert cal.failure == OUTCOMES['held'][1]
        base = FakeBase()
        cal = ForceFrameCalibrator(base)

        def hold_from_the_first_spring(cal, base):
            if cal.step is Step.SPRING_X:
                base.hand = (0.0, 0.0)
            else:
                obedient_user(cal, base)
        run(cal, base, user=hold_from_the_first_spring)
        assert cal.failure == OUTCOMES['held'][1]           # the spring's return was evidence

    def test_a_base_that_renders_nothing_is_reported_as_such(self):
        '''With no spring to bring the stick back after a push, the input
        check's release wait is where a dead base shows.'''
        base = FakeBase(renders=False)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.step is Step.FAILED
        assert cal.failure == OUTCOMES['no_release'][1]

    def test_reflected_directions_with_standard_springs_are_unsupported(self):
        base = FakeBase(sense=-1, rotation=0)
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.step is Step.FAILED
        assert cal.stage_states()[Stage.DIRECTIONS] == 'failed'

    def test_a_base_that_changes_under_verification_applies_nothing(self):
        """The derived frame is checked on the device: a base whose
        direction response shifts after the measurement fails the check."""
        base = FakeBase()
        cal = ForceFrameCalibrator(base)

        def user(cal, base):
            if cal.step is Step.VERIFY_180:
                base.rotation = 90
            obedient_user(cal, base)
        run(cal, base, user=user)
        assert cal.step is Step.FAILED
        assert cal.stage_states()[Stage.VERIFY] == 'failed'
        assert cal.result is None and base.released
        assert base.push == (0.0, 0.0)

    def test_lost_input_fails(self):
        base = FakeBase()
        base.read_axes = lambda: None
        cal = ForceFrameCalibrator(base)
        run(cal, base, seconds=5.0)
        assert cal.step is Step.FAILED and base.released

    def test_a_device_fault_is_contained(self):
        base = FakeBase()
        base.fault_on = 'constant'
        cal = ForceFrameCalibrator(base)
        run(cal, base)
        assert cal.step is Step.FAILED and base.released

    def test_no_push_times_out(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        run(cal, base, seconds=40.0, user=lambda cal, base: None)
        assert cal.step is Step.FAILED
        assert cal.stage_states()[Stage.INPUT] == 'failed'


class TestCancel:
    def test_cancel_mid_run_stops_everything(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)

        def user(cal, base):
            obedient_user(cal, base)
            if cal.step is Step.DIRECTION_0:
                cal.cancel()
        run(cal, base, user=user)
        assert cal.step is Step.CANCELLED
        assert base.released and base.stopped >= 1
        assert base.frames_set[-1] is None
        assert cal.result is None

    def test_cancel_with_a_clock_centers_the_stick_first(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        seen = []

        def user(cal, base):
            obedient_user(cal, base)
            if cal.step is Step.DIRECTION_0 and not seen:
                seen.append('cancelled')
                cal.cancel(now=cal._samples[-1][0] if cal._samples else 0.0)
            if cal.parking:
                seen.append('parking')
        run(cal, base, user=user)
        assert cal.step is Step.CANCELLED
        assert 'parking' in seen
        assert base.released and base.frames_set[-1] is None

    def test_a_second_cancel_while_centering_stops_at_once(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        cal.start(0.0)
        base.tick()
        cal.advance(0.0)
        cal.cancel(now=0.0)
        assert cal.parking and not base.released
        cal.cancel()
        assert cal.step is Step.CANCELLED and base.released

    def test_advance_after_finish_is_inert(self):
        base = FakeBase()
        cal = ForceFrameCalibrator(base)
        cal.cancel()
        assert cal.step is Step.IDLE          # never started: nothing to cancel
        cal.start(0.0)
        cal.cancel()
        released = base.released
        cal.advance(1.0)
        assert cal.step is Step.CANCELLED and base.released == released
