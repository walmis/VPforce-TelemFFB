"""FFB tap reader (telemffb.hw.ffb_tap) and the sim-agnostic
'Game Managed (DirectInput Tap)' spring mode (DINPUT_TAP).

A fake tap writer is simulated by writing TapShm bytes into the real named
mapping - the same order-independent attach the wrapper's writer uses.
"""
import ctypes
import mmap
import os

import pytest

import telemffb.hw.ffb_tap as ffb_tap
from telemffb.hw.ffb_tap import (
    ET_SPRING, TAP_MAGIC, TAP_VERSION,
    FfbTapReader, TapShm, di_to_rhino, di_to_rhino_sat, read_game_spring,
)

pytestmark = [pytest.mark.unit]

# a distinct name so tests never fight a real writer or viewer
TEST_SHM = "Local\\FFBTap_v1_test"


def make_shm(spring_kwargs=None, playing=1, paused=0, writer_pid=None):
    shm = TapShm()
    shm.magic = TAP_MAGIC
    shm.version = TAP_VERSION
    shm.size = ctypes.sizeof(TapShm)
    shm.writerPid = writer_pid if writer_pid is not None else os.getpid()
    shm.deviceCount = 1
    dev = shm.devices[0]
    dev.used = 1
    dev.generation = 1
    dev.vid, dev.pid = 0xFFFF, 0x2054
    dev.pausedState = paused
    dev.name = b"VPforce Rhino FFB Monster"
    e = dev.effects[0]
    e.slotUsed = 1
    e.effectType = ET_SPRING
    e.playing = playing
    c = e.u.condition
    defaults = dict(count=2,
                    offset=(2500, -5000),
                    coef=(10000, 5000),
                    sat=(10000, 0),
                    deadband=(0, 100))
    defaults.update(spring_kwargs or {})
    c.count = defaults["count"]
    for i in range(2):
        c.offset[i] = defaults["offset"][i]
        c.positiveCoefficient[i] = defaults["coef"][i]
        c.negativeCoefficient[i] = defaults["coef"][i]
        c.positiveSaturation[i] = defaults["sat"][i]
        c.negativeSaturation[i] = defaults["sat"][i]
        c.deadBand[i] = defaults["deadband"][i]
    return shm


def add_effect(shm, slot, effect_type, playing=1, flags=0x20, direction=(0, 0),
               axis_count=2, axes=(0, 4), duration=0, gain=10000,
               start_count=1, **type_kw):
    """Populate a non-spring effect slot (slot 0 is make_shm's spring)."""
    e = shm.devices[0].effects[slot]
    e.slotUsed = 1
    e.effectType = effect_type
    e.playing = playing
    e.startCount = start_count
    e.flags = flags
    e.duration = duration
    e.gain = gain
    e.axisCount = axis_count
    e.axes[0], e.axes[1] = axes
    e.direction[0], e.direction[1] = direction
    if effect_type == ffb_tap.ET_CONSTANT:
        e.u.constant.magnitude = type_kw.get("magnitude", 10000)
    elif effect_type in ffb_tap.PERIODIC_TYPES:
        p = e.u.periodic
        p.magnitude = type_kw.get("magnitude", 10000)
        p.offset = type_kw.get("offset", 0)
        p.phase = type_kw.get("phase", 0)
        p.period = type_kw.get("period", 100000)
    elif effect_type in ffb_tap.CONDITION_TYPES:
        c = e.u.condition
        c.count = type_kw.get("count", 2)
        for i in range(2):
            c.offset[i] = type_kw.get("offset", (0, 0))[i]
            c.positiveCoefficient[i] = type_kw.get("coef", (10000, 10000))[i]
            c.negativeCoefficient[i] = type_kw.get("coef", (10000, 10000))[i]
    if type_kw.get("envelope"):
        e.hasEnvelope = 1
        env = type_kw["envelope"]
        e.envelope.attackLevel = env[0]
        e.envelope.attackTime = env[1]
        e.envelope.fadeLevel = env[2]
        e.envelope.fadeTime = env[3]
    return e


@pytest.fixture
def tap_mapping(monkeypatch):
    """A private named mapping standing in for the wrapper's, plus a writer."""
    monkeypatch.setattr(ffb_tap, "SHM_NAME", TEST_SHM)
    buf = mmap.mmap(-1, ctypes.sizeof(TapShm), tagname=TEST_SHM,
                    access=mmap.ACCESS_WRITE)

    def write(shm):
        buf.seek(0)
        buf.write(bytes(shm))

    yield write
    buf.close()


class TestReader:
    def test_snapshot_round_trip(self, tap_mapping):
        tap_mapping(make_shm())
        reader = FfbTapReader()
        snap = reader.snapshot()
        assert snap.magic == TAP_MAGIC
        dev = snap.devices[0]
        assert dev.name == b"VPforce Rhino FFB Monster"
        assert dev.effects[0].effectType == ET_SPRING
        assert dev.effects[0].u.condition.offset[0] == 2500
        # this test process is the "writer" and is definitely alive
        assert reader.writer_alive(snap)
        reader.close()

    def test_dead_writer_detected(self):
        shm = make_shm(writer_pid=0)
        assert not FfbTapReader().writer_alive(shm)


class TestReadGameSpring:
    def _read(self, tap_mapping, shm):
        tap_mapping(shm)
        reader = FfbTapReader()
        state = reader.read_game_spring()
        reader.close()
        return state

    def test_units_translated_to_rhino_scale(self, tap_mapping):
        state = self._read(tap_mapping, make_shm())
        assert state is not None
        assert state.device_name == "VPforce Rhino FFB Monster"
        assert state.x.offset == 1024                 # 2500/10000 * 4096
        assert state.x.positive_coefficient == 4096   # 10000 -> full scale
        assert state.x.positive_saturation == 4096
        assert state.x.deadband == 0
        assert state.y.offset == -2048                # -5000 -> half negative
        assert state.y.positive_coefficient == 2048
        assert state.y.positive_saturation == 4096    # sat 0 = "not set" -> unlimited
        assert state.y.deadband == 41                 # 100/10000 * 4096

    def test_force_trim_signature(self, tap_mapping):
        """The DCS force-trim behavior observed in the field: coefficient
        collapses to 100 (1%) while trim is held."""
        state = self._read(tap_mapping, make_shm(
            spring_kwargs={"coef": (100, 100)}))
        assert state.x.positive_coefficient == 41     # 100/10000*4096

    def test_not_playing_returns_none(self, tap_mapping):
        assert self._read(tap_mapping, make_shm(playing=0)) is None

    def test_paused_device_returns_none(self, tap_mapping):
        assert self._read(tap_mapping, make_shm(paused=1)) is None

    def test_no_writer_returns_none(self, tap_mapping):
        assert self._read(tap_mapping, TapShm()) is None

    def test_dead_writer_returns_none(self, tap_mapping):
        assert self._read(tap_mapping, make_shm(writer_pid=0)) is None

    def test_wrong_version_returns_none(self, tap_mapping):
        shm = make_shm()
        shm.version = TAP_VERSION + 1
        assert self._read(tap_mapping, shm) is None

    def test_single_axis_condition(self, tap_mapping):
        state = self._read(tap_mapping, make_shm(
            spring_kwargs={"count": 1}))
        assert state.x is not None and state.y is None


class TestScaling:
    def test_di_to_rhino(self):
        assert di_to_rhino(10000) == 4096
        assert di_to_rhino(-10000) == -4096
        assert di_to_rhino(0) == 0

    def test_saturation_zero_means_unlimited(self):
        assert di_to_rhino_sat(0) == 4096
        assert di_to_rhino_sat(5000) == 2048


class TestTapSpringMode:
    """The sim-agnostic FfbTapMixIn handler: spring-mode gating,
    rendering, and axis-orientation corrections (exercised via the DCS
    aircraft, which BMS also uses; IL-2 precedence is tested separately)."""

    def _make_instance(self, spring_mode):
        from tests.framework.base import BaseTelemetryEffectTestCase
        from tests.framework.utils import TelemetryDataBuilder
        from telemffb.sim.aircrafts_dcs import Aircraft as DCSAircraft

        case = BaseTelemetryEffectTestCase()
        case.setup_method()
        inst = case.create_aircraft_instance(DCSAircraft, name="TestDCS")
        inst.spring_mode = spring_mode
        inst._telem_data = TelemetryDataBuilder().build()
        return case, inst

    def _run(self, spring_mode, state):
        import unittest.mock as mock
        case, inst = self._make_instance(spring_mode)
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=state):
            rendered = inst.ffb_tap_spring()
        return case.mock_effects, inst, rendered

    def _state(self):
        from telemffb.hw.ffb_tap import TapAxisCondition, TapSpringState
        return TapSpringState(
            x=TapAxisCondition(offset=1024, positive_coefficient=4096,
                               negative_coefficient=4096,
                               positive_saturation=4096,
                               negative_saturation=4096, deadband=0),
            y=TapAxisCondition(offset=-2048, positive_coefficient=41,
                               negative_coefficient=41,
                               positive_saturation=4096,
                               negative_saturation=4096, deadband=0),
            device_name="VPforce Rhino FFB Monster",
            generation=1, reset_count=0, update_count=7)

    def test_renders_in_telem_mode(self):
        effects, inst, rendered = self._run("DINPUT_TAP", self._state())
        assert rendered
        spring = effects.dict['ffb_tap_spring']
        assert spring.started
        assert inst._tap_cond_x.cpOffset == 1024
        assert inst._tap_cond_x.positiveCoefficient == 4096
        assert inst._tap_cond_y.cpOffset == -2048
        assert inst._tap_cond_y.positiveCoefficient == 41
        assert inst._telem_data['FFB_Tap'] == 'active'
        assert inst._telem_data['FFB_X_Center'] == 0.25
        assert inst._telem_data['FFB_Y_Force'] == 0.01

    def test_stops_when_no_tap_state(self):
        effects, inst, rendered = self._run("DINPUT_TAP", None)
        assert not rendered
        assert not effects.dict['ffb_tap_spring'].started
        assert inst._telem_data['FFB_Tap'] == 'inactive'

    def test_gated_off_in_other_modes(self):
        effects, inst, rendered = self._run("NONE", self._state())
        assert not rendered
        assert not effects.dict['ffb_tap_spring'].started
        assert 'FFB_Tap' not in inst._telem_data

    def test_invert_y_mirrors_condition(self):
        """FFTune-style correction: offset negates and the pos/neg-side
        roles swap on the inverted axis; the other axis is untouched."""
        import unittest.mock as mock
        case, inst = self._make_instance("DINPUT_TAP")
        inst.tap_spring_invert_y = True
        state = self._state()
        state.y.positive_saturation = 1000     # make the pos/neg swap visible
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=state):
            inst.ffb_tap_spring()
        assert inst._tap_cond_y.cpOffset == 2048              # -(-2048)
        assert inst._tap_cond_y.positiveSaturation == 4096    # was negative-side
        assert inst._tap_cond_y.negativeSaturation == 1000    # was positive-side
        assert inst._tap_cond_x.cpOffset == 1024              # x untouched

    def test_swap_axes(self):
        import unittest.mock as mock
        case, inst = self._make_instance("DINPUT_TAP")
        inst.tap_spring_swap_axes = True
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=self._state()):
            inst.ffb_tap_spring()
        assert inst._tap_cond_x.cpOffset == -2048    # got the y-axis condition
        assert inst._tap_cond_y.cpOffset == 1024     # got the x-axis condition
        assert inst._tap_cond_y.positiveCoefficient == 4096

    def test_axis_gain_scales_coefficients_only(self):
        """The per-axis gain sliders scale the spring force gradient;
        the center position and the other axis are untouched."""
        import unittest.mock as mock
        case, inst = self._make_instance("DINPUT_TAP")
        inst.tap_spring_gain_y = 2.0
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=self._state()):
            inst.ffb_tap_spring()
        assert inst._tap_cond_y.positiveCoefficient == 82    # 41 * 2
        assert inst._tap_cond_y.cpOffset == -2048            # position: no
        assert inst._tap_cond_x.positiveCoefficient == 4096  # x untouched

    def test_axis_gain_clamps_at_full_scale(self):
        import unittest.mock as mock
        case, inst = self._make_instance("DINPUT_TAP")
        inst.tap_spring_gain_x = 2.0
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=self._state()):
            inst.ffb_tap_spring()
        assert inst._tap_cond_x.positiveCoefficient == 4096  # already full

    def test_axis_gain_publishes_live_force_pct(self):
        """The n_float slider handles read the rendered force fraction from
        _pct_tap_x/_pct_tap_y (telem + IPC mirror for child devices)."""
        import unittest.mock as mock
        case, inst = self._make_instance("DINPUT_TAP")
        inst.tap_spring_gain_y = 2.0
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=self._state()):
            inst.ffb_tap_spring()
        assert inst._telem_data['_pct_tap_x'] == 1.0          # 4096: pinned
        assert inst._telem_data['_pct_tap_y'] == 82 / 4096
        assert inst._ipc_telem['_pct_tap_y'] == 82 / 4096

    def test_axis_gain_applies_after_swap(self):
        """The X slider scales whatever lands on the X axis: with swap
        enabled that is the game's Y condition."""
        import unittest.mock as mock
        case, inst = self._make_instance("DINPUT_TAP")
        inst.tap_spring_swap_axes = True
        inst.tap_spring_gain_x = 2.0
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=self._state()):
            inst.ffb_tap_spring()
        assert inst._tap_cond_x.positiveCoefficient == 82    # game y 41 * 2
        assert inst._tap_cond_y.positiveCoefficient == 4096  # game x, gain 1

    def test_il2_tap_is_separate_mode_from_records(self):
        """IL-2 has two game-spring sources as two DISTINCT modes/effects:
        DINPUT_TAP renders the tap; TELEM (Korea ffbdevice records /
        il2_ffb_spring) never triggers the tap."""
        import unittest.mock as mock
        from tests.framework.base import BaseTelemetryEffectTestCase
        from tests.framework.utils import TelemetryDataBuilder
        from telemffb.sim.aircrafts_il2 import Aircraft as IL2Aircraft

        case = BaseTelemetryEffectTestCase()
        case.setup_method()
        inst = case.create_aircraft_instance(IL2Aircraft, name="TestIL2")
        inst._telem_data = TelemetryDataBuilder().build()

        inst.spring_mode = "DINPUT_TAP"
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=self._state()):
            assert inst.ffb_tap_spring() is True
        assert case.mock_effects.dict['ffb_tap_spring'].started

        inst.spring_mode = "TELEM"
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=self._state()):
            assert inst.ffb_tap_spring() is False
        assert not case.mock_effects.dict['ffb_tap_spring'].started


class TestEffectTranslations:
    """The Active Effects panel resolves the slot-keyed tap effect names
    through EffectTranslator's regex fallback - every family must land on
    a friendly label, and _1 must not swallow _10/_11."""

    def _descr(self, name):
        from telemffb.utils import EffectTranslator
        return EffectTranslator.get_translation(name)[0]

    def test_families_translate(self):
        assert self._descr('tap_game_0_1') == \
            'Game Constant Force (DirectInput Tap)'
        for et in (3, 4, 5, 6, 7):
            assert self._descr(f'tap_game_2_{et}') == \
                'Game Periodic Vibration (DirectInput Tap)'
        assert self._descr('tap_game_5_9') == 'Game Damper (DirectInput Tap)'
        assert self._descr('tap_game_5_10') == 'Game Inertia (DirectInput Tap)'
        assert self._descr('tap_game_5_11') == \
            'Game Friction (DirectInput Tap)'

    def test_no_lookup_never_shown_for_tap_slots(self):
        from telemffb.hw import ffb_tap
        renderable = ({ffb_tap.ET_CONSTANT} | ffb_tap.PERIODIC_TYPES
                      | ffb_tap.CONDITION_TYPES)
        for slot in range(32):
            for et in renderable:
                assert 'No Lookup' not in self._descr(f'tap_game_{slot}_{et}')


class TestReadGameEffects:
    """Translation of the mirror's non-spring slots (read_game_effects)."""

    def _read(self, tap_mapping, shm):
        tap_mapping(shm)
        reader = FfbTapReader()
        state = reader.read_game_effects()
        reader.close()
        return state

    def test_spring_slot_excluded_but_state_present(self, tap_mapping):
        state = self._read(tap_mapping, make_shm())
        assert state is not None
        assert state.effects == []
        assert state.device_name == "VPforce Rhino FFB Monster"

    def test_constant_translated(self, tap_mapping):
        shm = make_shm()
        # polar 90 deg, half magnitude at half gain, negative sign kept
        add_effect(shm, 1, ffb_tap.ET_CONSTANT, direction=(9000, 0),
                   gain=5000, magnitude=-10000)
        state = self._read(tap_mapping, shm)
        fx = state.effects[0]
        assert fx.slot == 1
        assert fx.effect_type == ffb_tap.ET_CONSTANT
        assert fx.playing
        assert fx.direction_deg == 90.0
        assert fx.constant_magnitude == -0.5

    def test_stopped_slot_included(self, tap_mapping):
        shm = make_shm()
        add_effect(shm, 1, ffb_tap.ET_CONSTANT, playing=0)
        state = self._read(tap_mapping, shm)
        assert len(state.effects) == 1
        assert not state.effects[0].playing

    def test_duration_translation(self, tap_mapping):
        shm = make_shm()
        add_effect(shm, 1, ffb_tap.ET_CONSTANT, duration=250000)
        add_effect(shm, 2, ffb_tap.ET_CONSTANT, duration=0)
        add_effect(shm, 3, ffb_tap.ET_CONSTANT, duration=0xFFFFFFFF)
        state = self._read(tap_mapping, shm)
        by_slot = {fx.slot: fx for fx in state.effects}
        assert by_slot[1].duration_ms == 250
        assert by_slot[2].duration_ms is None
        assert by_slot[3].duration_ms is None

    def test_periodic_translated(self, tap_mapping):
        shm = make_shm()
        add_effect(shm, 1, ffb_tap.ET_SINE, direction=(0, 0),
                   magnitude=5000, offset=5000, phase=9000, period=50000)
        state = self._read(tap_mapping, shm)
        fx = state.effects[0]
        assert fx.periodic_magnitude == 0.5
        assert fx.periodic_offset == 2048
        assert fx.periodic_phase == round(90 * 255 / 360)
        assert fx.periodic_freq == 20.0       # 50 ms period

    def test_cartesian_direction_converts_to_polar(self, tap_mapping):
        shm = make_shm()
        add_effect(shm, 1, ffb_tap.ET_CONSTANT, flags=0x10, direction=(1, 0))
        add_effect(shm, 2, ffb_tap.ET_CONSTANT, flags=0x10, direction=(0, -1))
        add_effect(shm, 3, ffb_tap.ET_CONSTANT, flags=0x10, direction=(0, 1))
        state = self._read(tap_mapping, shm)
        by_slot = {fx.slot: fx for fx in state.effects}
        assert by_slot[1].direction_deg == 90.0    # +X ray
        assert by_slot[2].direction_deg == 0.0     # north
        assert by_slot[3].direction_deg == 180.0

    def test_single_axis_uses_axis_ray(self, tap_mapping):
        shm = make_shm()
        # DIEFF_OBJECTOFFSETS (0x02): axes[0]=4 is DIJOFS_Y, 0 is DIJOFS_X
        add_effect(shm, 1, ffb_tap.ET_CONSTANT, flags=0x02, axis_count=1,
                   axes=(4, 0))
        add_effect(shm, 2, ffb_tap.ET_CONSTANT, flags=0x02, axis_count=1,
                   axes=(0, 0))
        state = self._read(tap_mapping, shm)
        by_slot = {fx.slot: fx for fx in state.effects}
        assert by_slot[1].direction_deg == 0.0     # +Y ray
        assert by_slot[2].direction_deg == 270.0   # +X ray

    def test_damper_gain_scales_coefficients_not_offsets(self, tap_mapping):
        shm = make_shm()
        add_effect(shm, 1, ffb_tap.ET_DAMPER, gain=5000,
                   coef=(10000, 10000), offset=(5000, 0))
        state = self._read(tap_mapping, shm)
        fx = state.effects[0]
        assert fx.x.positive_coefficient == 2048   # halved by gain
        assert fx.x.offset == 2048                 # position: gain-free
        assert fx.y.positive_coefficient == 2048

    def test_envelope_translated(self, tap_mapping):
        shm = make_shm()
        add_effect(shm, 1, ffb_tap.ET_CONSTANT,
                   envelope=(5000, 200000, 0, 300000))
        state = self._read(tap_mapping, shm)
        env = state.effects[0].envelope
        assert env.attack_level == 2048
        assert env.attack_time_ms == 200
        assert env.fade_level == 0
        assert env.fade_time_ms == 300

    def test_paused_device_returns_none(self, tap_mapping):
        shm = make_shm(paused=1)
        add_effect(shm, 1, ffb_tap.ET_CONSTANT)
        assert self._read(tap_mapping, shm) is None


class TestTapGameEffectsMode:
    """FfbTapMixIn's non-spring game-effect reconciliation: per-type
    toggles, stop/dispose lifecycle, transient replay, corrections."""

    def _make_instance(self):
        from tests.framework.base import BaseTelemetryEffectTestCase
        from tests.framework.utils import TelemetryDataBuilder
        from telemffb.sim.aircrafts_dcs import Aircraft as DCSAircraft

        case = BaseTelemetryEffectTestCase()
        case.setup_method()
        inst = case.create_aircraft_instance(DCSAircraft, name="TestDCS")
        inst.spring_mode = "DINPUT_TAP"
        inst._telem_data = TelemetryDataBuilder().build()
        return case, inst

    def _fx(self, slot, effect_type, playing=True, start_count=1,
            duration_ms=None, direction_deg=270.0, **kw):
        from telemffb.hw.ffb_tap import TapGameEffect
        return TapGameEffect(slot=slot, effect_type=effect_type,
                             playing=playing, start_count=start_count,
                             update_count=0, duration_ms=duration_ms,
                             direction_deg=direction_deg, **kw)

    def _state(self, *effects, generation=1, reset_count=0):
        from telemffb.hw.ffb_tap import TapGameEffects
        return TapGameEffects(effects=list(effects), device_name="Tapped",
                              generation=generation, reset_count=reset_count)

    def _frame(self, inst, state):
        import unittest.mock as mock
        with mock.patch("telemffb.hw.ffb_tap.read_game_spring",
                        return_value=None), \
             mock.patch("telemffb.hw.ffb_tap.read_game_effects",
                        return_value=state):
            inst.ffb_tap_spring()

    def test_constant_renders(self):
        case, inst = self._make_instance()
        self._frame(inst, self._state(
            self._fx(1, 1, constant_magnitude=0.5, direction_deg=90.0)))
        eff = case.mock_effects.dict['tap_game_1_1']
        assert eff.started
        assert eff._magnitude == 0.5
        assert eff._direction == 90.0
        assert inst._telem_data['FFB_TapFx'] == '1C'

    def test_type_toggle_drops_family(self):
        case, inst = self._make_instance()
        inst.tap_effect_constant = False
        self._frame(inst, self._state(self._fx(1, 1, constant_magnitude=0.5)))
        assert 'tap_game_1_1' not in case.mock_effects.dict
        assert inst._telem_data['FFB_TapFx'] == '-'

    def test_stop_reconciles(self):
        case, inst = self._make_instance()
        self._frame(inst, self._state(self._fx(1, 1, constant_magnitude=0.5)))
        assert case.mock_effects.dict['tap_game_1_1'].started
        self._frame(inst, self._state(
            self._fx(1, 1, playing=False, constant_magnitude=0.5)))
        assert not case.mock_effects.dict['tap_game_1_1'].started

    def test_slot_type_change_disposes_old_effect(self):
        case, inst = self._make_instance()
        self._frame(inst, self._state(self._fx(1, 1, constant_magnitude=0.5)))
        self._frame(inst, self._state(
            self._fx(1, 4, periodic_magnitude=0.5, periodic_freq=20.0)))
        assert 'tap_game_1_1' not in case.mock_effects.dict
        assert case.mock_effects.dict['tap_game_1_4'].started

    def test_vacated_slot_disposes(self):
        case, inst = self._make_instance()
        self._frame(inst, self._state(self._fx(1, 1, constant_magnitude=0.5)))
        self._frame(inst, self._state())
        assert 'tap_game_1_1' not in case.mock_effects.dict

    def test_device_reset_disposes_all(self):
        case, inst = self._make_instance()
        self._frame(inst, self._state(self._fx(1, 1, constant_magnitude=0.5)))
        self._frame(inst, self._state(reset_count=1))
        assert 'tap_game_1_1' not in case.mock_effects.dict

    def test_mode_change_tears_down(self):
        case, inst = self._make_instance()
        self._frame(inst, self._state(self._fx(1, 1, constant_magnitude=0.5)))
        inst.spring_mode = "NONE"
        self._frame(inst, self._state(self._fx(1, 1, constant_magnitude=0.5)))
        assert 'tap_game_1_1' not in case.mock_effects.dict

    def test_missed_one_shot_replayed_with_duration(self):
        """A finite periodic that started AND stopped between two polls is
        caught by its startCount delta and replayed for its duration."""
        case, inst = self._make_instance()
        self._frame(inst, self._state(
            self._fx(2, 4, playing=False, start_count=1, duration_ms=150,
                     periodic_magnitude=0.8, periodic_freq=25.0)))
        assert not case.mock_effects.dict['tap_game_2_4'].started
        self._frame(inst, self._state(
            self._fx(2, 4, playing=False, start_count=2, duration_ms=150,
                     periodic_magnitude=0.8, periodic_freq=25.0)))
        eff = case.mock_effects.dict['tap_game_2_4']
        assert eff.started
        freq, mag, direction, kwargs = eff._periodic
        assert (freq, mag) == (25.0, 0.8)
        assert kwargs['duration'] == 150

    def test_missed_infinite_blip_not_replayed(self):
        case, inst = self._make_instance()
        self._frame(inst, self._state(
            self._fx(1, 1, playing=False, start_count=1,
                     constant_magnitude=0.5)))
        self._frame(inst, self._state(
            self._fx(1, 1, playing=False, start_count=2,
                     constant_magnitude=0.5)))
        assert not case.mock_effects.dict['tap_game_1_1'].started

    def test_swap_axes_rotates_direction(self):
        case, inst = self._make_instance()
        inst.tap_spring_swap_axes = True
        self._frame(inst, self._state(
            self._fx(1, 1, constant_magnitude=0.5, direction_deg=270.0)))
        assert case.mock_effects.dict['tap_game_1_1']._direction == 0.0

    def test_invert_x_mirrors_direction(self):
        case, inst = self._make_instance()
        inst.tap_spring_invert_x = True
        self._frame(inst, self._state(
            self._fx(1, 1, constant_magnitude=0.5, direction_deg=270.0)))
        assert case.mock_effects.dict['tap_game_1_1']._direction == 90.0

    def test_damper_renders_conditions(self):
        from telemffb.hw.ffb_tap import TapAxisCondition
        case, inst = self._make_instance()
        cond = TapAxisCondition(offset=1024, positive_coefficient=2048,
                                negative_coefficient=2048,
                                positive_saturation=4096,
                                negative_saturation=4096, deadband=0)
        self._frame(inst, self._state(self._fx(1, 9, x=cond, y=cond)))
        eff = case.mock_effects.dict['tap_game_1_9']
        assert eff.started
        assert eff._x_offset == 1024
        assert eff._x_coefficient == 2048
        assert eff._y_coefficient == 2048
        assert inst._telem_data['FFB_TapFx'] == '1D'

    def test_envelope_applied(self):
        from telemffb.hw.ffb_tap import TapEnvelope
        case, inst = self._make_instance()
        self._frame(inst, self._state(
            self._fx(1, 1, constant_magnitude=0.5,
                     envelope=TapEnvelope(attack_level=2048,
                                          attack_time_ms=200,
                                          fade_level=0, fade_time_ms=300))))
        eff = case.mock_effects.dict['tap_game_1_1']
        assert eff._envelope == {'attackFromForce': 2048, 'decayToForce': 0,
                                 'attackTime': 200, 'decayTime': 300}

    def test_constant_gain_scales_and_clamps(self):
        case, inst = self._make_instance()
        inst.tap_effect_constant_gain = 2.0
        self._frame(inst, self._state(
            self._fx(1, 1, constant_magnitude=0.25),
            self._fx(2, 1, constant_magnitude=-0.8)))
        assert case.mock_effects.dict['tap_game_1_1']._magnitude == 0.5
        assert case.mock_effects.dict['tap_game_2_1']._magnitude == -1.0

    def test_periodic_gain_scales_magnitude_and_offset(self):
        case, inst = self._make_instance()
        inst.tap_effect_periodic_gain = 1.5
        self._frame(inst, self._state(
            self._fx(1, 4, periodic_magnitude=0.4, periodic_freq=20.0,
                     periodic_offset=1000)))
        freq, mag, direction, kwargs = \
            case.mock_effects.dict['tap_game_1_4']._periodic
        assert mag == pytest.approx(0.6)
        assert kwargs['offset'] == 1500

    def test_damper_gain_scales_coefficients(self):
        from telemffb.hw.ffb_tap import TapAxisCondition
        case, inst = self._make_instance()
        inst.tap_effect_damper_gain = 2.0
        cond = TapAxisCondition(offset=1024, positive_coefficient=1000,
                                negative_coefficient=3000,
                                positive_saturation=4096,
                                negative_saturation=4096, deadband=0)
        self._frame(inst, self._state(self._fx(1, 9, x=cond)))
        eff = case.mock_effects.dict['tap_game_1_9']
        assert eff._x_coefficient == 2000     # 1000 * 2
        assert eff._x_offset == 1024          # position untouched

    def test_family_peak_pcts_published(self):
        """Each family's peak rendered force fraction lands in its
        _pct_tap_* key (max across that type's playing effects)."""
        case, inst = self._make_instance()
        self._frame(inst, self._state(
            self._fx(1, 1, constant_magnitude=0.25),
            self._fx(2, 1, constant_magnitude=-0.8)))
        assert inst._telem_data['_pct_tap_const'] == 0.8
        assert inst._telem_data['_pct_tap_periodic'] == 0.0
        assert inst._telem_data['_pct_tap_damper'] == 0.0
        assert inst._ipc_telem['_pct_tap_const'] == 0.8

    def test_family_peaks_zero_when_idle(self):
        case, inst = self._make_instance()
        self._frame(inst, self._state())
        for key in ('_pct_tap_const', '_pct_tap_periodic',
                    '_pct_tap_damper', '_pct_tap_inertia',
                    '_pct_tap_friction'):
            assert inst._telem_data[key] == 0.0

    def test_damper_peak_reflects_scaled_coefficient(self):
        from telemffb.hw.ffb_tap import TapAxisCondition
        case, inst = self._make_instance()
        inst.tap_effect_damper_gain = 2.0
        cond = TapAxisCondition(offset=0, positive_coefficient=1000,
                                negative_coefficient=1000,
                                positive_saturation=4096,
                                negative_saturation=4096, deadband=0)
        self._frame(inst, self._state(self._fx(1, 9, x=cond)))
        assert inst._telem_data['_pct_tap_damper'] == 2000 / 4096

    def test_condition_types_toggle_independently(self):
        """Damper, inertia and friction each have their own toggle - turning
        one off must not drop the others."""
        from telemffb.hw.ffb_tap import TapAxisCondition
        case, inst = self._make_instance()
        inst.tap_effect_friction = False
        cond = TapAxisCondition(offset=0, positive_coefficient=1000,
                                negative_coefficient=1000,
                                positive_saturation=4096,
                                negative_saturation=4096, deadband=0)
        self._frame(inst, self._state(
            self._fx(1, 9, x=cond),      # damper
            self._fx(2, 10, x=cond),     # inertia
            self._fx(3, 11, x=cond)))    # friction: toggled off
        assert case.mock_effects.dict['tap_game_1_9'].started
        assert case.mock_effects.dict['tap_game_2_10'].started
        assert 'tap_game_3_11' not in case.mock_effects.dict

    def test_condition_gains_are_per_type(self):
        from telemffb.hw.ffb_tap import TapAxisCondition
        case, inst = self._make_instance()
        inst.tap_effect_inertia_gain = 2.0
        cond = TapAxisCondition(offset=0, positive_coefficient=1000,
                                negative_coefficient=1000,
                                positive_saturation=4096,
                                negative_saturation=4096, deadband=0)
        self._frame(inst, self._state(
            self._fx(1, 9, x=cond),      # damper: gain 1.0
            self._fx(2, 10, x=cond)))    # inertia: gain 2.0
        assert case.mock_effects.dict['tap_game_1_9']._x_coefficient == 1000
        assert case.mock_effects.dict['tap_game_2_10']._x_coefficient == 2000
        assert inst._telem_data['_pct_tap_damper'] == 1000 / 4096
        assert inst._telem_data['_pct_tap_inertia'] == 2000 / 4096

    def test_envelope_levels_scale_with_family_gain(self):
        from telemffb.hw.ffb_tap import TapEnvelope
        case, inst = self._make_instance()
        inst.tap_effect_constant_gain = 2.0
        self._frame(inst, self._state(
            self._fx(1, 1, constant_magnitude=0.25,
                     envelope=TapEnvelope(attack_level=1024,
                                          attack_time_ms=200,
                                          fade_level=3000, fade_time_ms=300))))
        env = case.mock_effects.dict['tap_game_1_1']._envelope
        assert env['attackFromForce'] == 2048
        assert env['decayToForce'] == 4096    # 6000 clamped
