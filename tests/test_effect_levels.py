"""Effect levels: the level table and its scaling at HapticEffect's sends."""
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('telemffb.hw.hid', MagicMock())

from telemffb.hw.effect_levels import MUTE_ALL, MUTE_KEEP_SPRING, MUTE_OFF, levels
from telemffb.hw.ffb_rhino import (
    EFFECT_CONSTANT,
    EFFECT_DAMPER,
    EFFECT_DETENT,
    EFFECT_FRICTION,
    EFFECT_INERTIA,
    EFFECT_SINE,
    EFFECT_SPRING,
    PERIODIC_EFFECTS,
    FFBEffectHandle,
    FFBReport_SetCondition,
    HapticEffect,
)

pytestmark = pytest.mark.unit

COND_FIELDS = ("parameterBlockOffset", "cpOffset", "positiveCoefficient", "negativeCoefficient",
               "positiveSaturation", "negativeSaturation", "deadBand")


def fields(cond):
    return {name: getattr(cond, name) for name in COND_FIELDS}


class RecordingHandle:
    """A device effect handle that records what reaches it."""

    def __init__(self, effect_type, effect_id=1):
        self.type = effect_type
        self.effect_id = effect_id
        self._started = False
        self.periodic = []
        self.constant = []
        self.conditions = []   # (object passed, its fields at call time)
        self.envelopes = []
        self.fail_with = None

    def __bool__(self):
        return bool(self.effect_id and self.type)

    @property
    def started(self):
        return self._started

    @property
    def name(self):
        return "recording"

    def setEffect(self, **kwargs):
        pass

    def setPeriodic(self, freq, magnitude, direction, duration=0, **kwargs):
        self.periodic.append((freq, magnitude, direction, duration, kwargs))

    def setConstantForce(self, magnitude, direction, **kwargs):
        self.constant.append((magnitude, direction, kwargs))

    def setCondition(self, cond):
        if self.fail_with:
            raise self.fail_with
        self.conditions.append((cond, fields(cond)))

    def setEnvelope(self, envelope):
        self.envelopes.append((envelope, envelope.attackFromForce, envelope.decayToForce,
                               envelope.attackTime, envelope.decayTime))

    def start(self, loopCount=1, override=False):
        self._started = True
        return self

    def stop(self):
        self._started = False
        return self

    def forget_playback(self):
        self._started = False

    def destroy(self):
        self.effect_id = None
        self.type = 0
        self._started = False

    def last_condition(self, axis):
        for cond, values in reversed(self.conditions):
            if values["parameterBlockOffset"] == axis:
                return values
        return None


class FakeDevice:
    def __init__(self):
        self.connected = True
        self.handles = []

    def create_effect(self, effect_type):
        handle = RecordingHandle(effect_type, effect_id=len(self.handles) + 1)
        self.handles.append(handle)
        return handle


@pytest.fixture(autouse=True)
def default_levels():
    levels.reset()
    yield
    levels.reset()


@pytest.fixture
def device():
    saved = HapticEffect.device
    dev = FakeDevice()
    HapticEffect.device = dev
    yield dev
    HapticEffect.device = saved


def started_spring(cond_x=None, cond_y=None):
    effect = HapticEffect().spring()
    for cond in (cond_x, cond_y):
        if cond is not None:
            effect.setCondition(cond)
    return effect.start()


# ---------------------------------------------------------------------------
# factor math
# ---------------------------------------------------------------------------

class TestFactor:
    def test_master_times_type(self):
        levels.set_levels({"master": 0.5, "spring": 0.5, "damper": 0.8})
        assert not levels.unity
        assert levels.factor(EFFECT_SPRING) == pytest.approx(0.25)
        assert levels.factor(EFFECT_DAMPER) == pytest.approx(0.4)
        assert levels.factor(EFFECT_CONSTANT) == pytest.approx(0.5)
        for effect_type in PERIODIC_EFFECTS:
            assert levels.factor(effect_type) == pytest.approx(0.5)

    def test_each_type_uses_its_own_level(self):
        expected = {EFFECT_SINE: "periodic", EFFECT_CONSTANT: "constant", EFFECT_SPRING: "spring",
                    EFFECT_DAMPER: "damper", EFFECT_INERTIA: "inertia", EFFECT_FRICTION: "friction",
                    EFFECT_DETENT: "spring"}
        for effect_type, name in expected.items():
            levels.reset()
            levels.set_level(name, 0.3)
            assert levels.factor(effect_type) == pytest.approx(0.3)

    def test_levels_clamp_to_unit_range(self):
        levels.set_level("damper", 1.7)
        assert levels.level("damper") == 1.0
        levels.set_level("damper", -0.3)
        assert levels.level("damper") == 0.0
        assert levels.factor(EFFECT_DAMPER) == 0.0

    def test_unknown_level_or_mode_is_rejected(self):
        with pytest.raises(ValueError):
            levels.set_level("rumble", 0.5)
        with pytest.raises(ValueError):
            levels.set_level("spring", float("nan"))
        with pytest.raises(ValueError):
            levels.set_mute("loud")
        assert levels.unity

    def test_mute_all_zeroes_every_bucket(self):
        levels.set_mute(MUTE_ALL)
        for effect_type in (*PERIODIC_EFFECTS, EFFECT_CONSTANT, EFFECT_SPRING, EFFECT_DAMPER,
                            EFFECT_INERTIA, EFFECT_FRICTION, EFFECT_DETENT):
            assert levels.factor(effect_type) == 0.0

    def test_mute_keep_spring_keeps_the_spring_bucket(self):
        levels.set_levels({"master": 0.8, "spring": 0.5})
        levels.set_mute(MUTE_KEEP_SPRING)
        assert levels.factor(EFFECT_SPRING) == pytest.approx(0.4)
        assert levels.factor(EFFECT_DETENT) == pytest.approx(0.4)
        for effect_type in (*PERIODIC_EFFECTS, EFFECT_CONSTANT, EFFECT_DAMPER,
                            EFFECT_INERTIA, EFFECT_FRICTION):
            assert levels.factor(effect_type) == 0.0

    def test_a_suspended_mute_applies_the_levels_alone(self):
        levels.set_levels({"master": 0.5})
        levels.set_mute(MUTE_ALL)
        levels.set_mute_suspended(True)
        assert levels.factor(EFFECT_CONSTANT) == pytest.approx(0.5)
        levels.set_mute_suspended(False)
        assert levels.factor(EFFECT_CONSTANT) == 0.0

    def test_reset(self):
        levels.set_levels({"master": 0.2, "friction": 0.1})
        levels.set_mute(MUTE_KEEP_SPRING)
        levels.reset()
        assert levels.unity
        assert levels.mute_mode == MUTE_OFF
        assert set(levels.snapshot().values()) == {1.0}


# ---------------------------------------------------------------------------
# what reaches the handle
# ---------------------------------------------------------------------------

class TestPeriodicAndConstant:
    def test_periodic_magnitude_and_offset_scale(self, device):
        levels.set_level("periodic", 0.5)
        effect = HapticEffect().periodic(10, 0.8, 90, offset=1000, phase=64).start()
        freq, magnitude, direction, duration, kwargs = effect._h_effect.periodic[-1]
        assert (freq, direction) == (10, 90)
        assert magnitude == pytest.approx(0.4)
        assert kwargs == {"offset": 500, "phase": 64}

    def test_periodic_update_is_scaled(self, device):
        effect = HapticEffect().periodic(10, 0.8, 90).start()
        levels.set_level("master", 0.25)
        effect.periodic(10, 0.6, 90)
        assert effect._h_effect.periodic[-1][1] == pytest.approx(0.15)

    def test_constant_magnitude_scales(self, device):
        levels.set_levels({"master": 0.5, "constant": 0.5})
        effect = HapticEffect().constant(-0.6, 45).start()
        magnitude, direction, _ = effect._h_effect.constant[-1]
        assert magnitude == pytest.approx(-0.15)
        assert direction == 45


class TestConditions:
    def make_cond(self, axis=0):
        return FFBReport_SetCondition(parameterBlockOffset=axis, cpOffset=1000,
                                      positiveCoefficient=2000, negativeCoefficient=-1000,
                                      positiveSaturation=4000, negativeSaturation=3000,
                                      deadBand=200)

    def test_coefficients_and_saturations_scale_positions_do_not(self, device):
        levels.set_level("spring", 0.5)
        cond = self.make_cond()
        before = fields(cond)
        effect = started_spring(cond)
        sent = effect._h_effect.last_condition(0)
        assert sent["positiveCoefficient"] == 1000
        assert sent["negativeCoefficient"] == -500
        assert sent["positiveSaturation"] == 2000
        assert sent["negativeSaturation"] == 1500
        assert sent["cpOffset"] == 1000
        assert sent["deadBand"] == 200
        assert fields(cond) == before

    def test_default_levels_pass_the_callers_struct_through(self, device):
        effect = started_spring()
        cond = self.make_cond()
        effect.setCondition(cond)
        sent, values = effect._h_effect.conditions[-1]
        assert sent is cond
        assert values == fields(cond)

    def test_zero_saturation_stays_unlimited(self, device):
        levels.set_level("friction", 0.5)
        effect = HapticEffect().friction(0.5, 0.5).start()
        sent = effect._h_effect.last_condition(0)
        assert sent["positiveSaturation"] == 0
        assert sent["negativeSaturation"] == 0

    def test_nonzero_saturation_never_becomes_unlimited(self, device):
        levels.set_level("spring", 0.001)
        cond = FFBReport_SetCondition(parameterBlockOffset=0, positiveCoefficient=4096,
                                      negativeCoefficient=4096, positiveSaturation=100,
                                      negativeSaturation=100)
        effect = started_spring(cond)
        sent = effect._h_effect.last_condition(0)
        assert sent["positiveSaturation"] == 1
        assert sent["negativeSaturation"] == 1

    def test_spring_adjuster_passes_unscaled(self, device):
        levels.set_level("spring", 0.5)
        levels.set_mute(MUTE_ALL)
        effect = HapticEffect().spring_adjuster().start()
        cond = FFBReport_SetCondition(parameterBlockOffset=1, cpOffset=500,
                                      positiveCoefficient=8192, negativeCoefficient=8192,
                                      positiveSaturation=4096, negativeSaturation=4096)
        before = fields(cond)
        effect.setCondition(cond)
        sent, values = effect._h_effect.conditions[-1]
        assert sent is cond
        assert values == before

    def test_detent_follows_the_spring_level(self, device):
        levels.set_levels({"spring": 0.5, "damper": 0.1})
        effect = HapticEffect().detent(position_x=1000, peak_x=2000, range_x=3000,
                                       gate_pos_x=3500, gate_neg_x=2500, deadband_x=100).start()
        sent = effect._h_effect.last_condition(0)
        assert sent["positiveCoefficient"] == 1000
        # width, gates and center are positions
        assert sent["negativeCoefficient"] == 3000
        assert sent["positiveSaturation"] == 3500
        assert sent["negativeSaturation"] == 2500
        assert sent["cpOffset"] == 1000
        assert sent["deadBand"] == 100


class TestEnvelope:
    def test_envelope_levels_scale_with_the_effect_type(self, device):
        levels.set_level("constant", 0.5)
        effect = HapticEffect().constant(0.5, 90).envelope(
            attackFromForce=2000, decayToForce=1000, attackTime=300, decayTime=200).start()
        _, attack, fade, attack_time, fade_time = effect._h_effect.envelopes[-1]
        assert (attack, fade) == (1000, 500)
        assert (attack_time, fade_time) == (300, 200)
        assert effect._pending_envelope.attackFromForce == 2000
        assert effect._pending_envelope.decayToForce == 1000


class TestReadouts:
    """The device handles record what was sent, so the readouts show the
    scaled forces."""

    @pytest.fixture
    def rhino(self):
        saved = HapticEffect.device
        dev = MagicMock()
        dev.connected = True
        dev.create_effect = lambda effect_type: FFBEffectHandle(dev, 1, effect_type)
        HapticEffect.device = dev
        yield dev
        HapticEffect.device = saved

    def test_intensity_and_axis_gains_are_scaled(self, rhino):
        levels.set_levels({"periodic": 0.5, "spring": 0.5})
        periodic = HapticEffect().periodic(10, 0.8, 0).start()
        spring = HapticEffect().spring(2048, 4096).start()
        assert periodic.intensity == pytest.approx(0.4)
        assert spring.axis_gains == (0.25, 0.5)


# ---------------------------------------------------------------------------
# reapply_levels
# ---------------------------------------------------------------------------

class TestReapply:
    def test_spring_set_once_is_resent_with_the_new_level(self, device):
        cond = FFBReport_SetCondition(parameterBlockOffset=0, positiveCoefficient=4000,
                                      negativeCoefficient=4000)
        effect = started_spring(cond)
        cond.positiveCoefficient = 1   # the caller reuses its struct
        levels.set_level("spring", 0.5)
        HapticEffect.reapply_levels()
        assert effect._h_effect.last_condition(0)["positiveCoefficient"] == 2000
        assert effect._h_effect.last_condition(0)["negativeCoefficient"] == 2000

    def test_effect_without_a_handle_is_skipped(self, device):
        effect = HapticEffect().spring(0.5, 0.5)
        levels.set_level("spring", 0.5)
        HapticEffect.reapply_levels()
        assert effect._h_effect is None
        assert device.handles == []

    def test_mute_then_unmute_restores(self, device):
        periodic = HapticEffect().periodic(10, 0.8, 90, offset=400).start()
        constant = HapticEffect().constant(0.3, 10).start()
        spring = HapticEffect().spring(0.5, 0.5).start()
        original = [spring._h_effect.last_condition(axis) for axis in (0, 1)]

        levels.set_mute(MUTE_ALL)
        HapticEffect.reapply_levels()
        assert periodic._h_effect.periodic[-1][1] == 0.0
        assert periodic._h_effect.periodic[-1][4]["offset"] == 0
        assert constant._h_effect.constant[-1][0] == 0.0
        for axis in (0, 1):
            assert spring._h_effect.last_condition(axis)["positiveCoefficient"] == 0

        levels.set_mute(MUTE_OFF)
        HapticEffect.reapply_levels()
        assert periodic._h_effect.periodic[-1][1] == 0.8
        assert periodic._h_effect.periodic[-1][4]["offset"] == 400
        assert constant._h_effect.constant[-1][0] == 0.3
        assert [spring._h_effect.last_condition(axis) for axis in (0, 1)] == original

    def test_envelope_is_resent(self, device):
        effect = HapticEffect().constant(0.5, 90).envelope(
            attackFromForce=2000, decayToForce=1000, attackTime=300, decayTime=200).start()
        levels.set_level("master", 0.5)
        HapticEffect.reapply_levels()
        _, attack, fade, _, _ = effect._h_effect.envelopes[-1]
        assert (attack, fade) == (1000, 500)

    def test_stopped_effect_stays_stopped(self, device):
        effect = HapticEffect().spring(0.5, 0.5).start()
        effect.stop(destroy_after=0)
        levels.set_level("spring", 0.5)
        HapticEffect.reapply_levels()
        assert not effect.started
        assert effect._h_effect.last_condition(0)["positiveCoefficient"] == 1024

    def test_recreated_effect_forgets_conditions_its_creation_does_not_send(self, device):
        effect = started_spring(FFBReport_SetCondition(parameterBlockOffset=1,
                                                       positiveCoefficient=3000))
        effect._h_effect.effect_id = 0     # the device dropped the block
        effect.start()
        levels.set_level("spring", 0.5)
        HapticEffect.reapply_levels()
        assert effect._h_effect.last_condition(1) is None

    def test_no_device_is_a_noop(self, device):
        effect = HapticEffect().spring(0.5, 0.5).start()
        sent = len(effect._h_effect.conditions)
        levels.set_level("spring", 0.5)
        HapticEffect.device = None
        assert HapticEffect.reapply_levels() == 0
        HapticEffect.device = device
        device.connected = False
        assert HapticEffect.reapply_levels() == 0
        assert len(effect._h_effect.conditions) == sent

    def test_a_failing_handle_does_not_raise(self, device):
        effect = HapticEffect().spring(0.5, 0.5).start()
        other = HapticEffect().constant(0.4, 0).start()
        effect._h_effect.fail_with = OSError("bridge fault")
        levels.set_level("master", 0.5)
        HapticEffect.reapply_levels()
        assert other._h_effect.constant[-1][0] == pytest.approx(0.2)
