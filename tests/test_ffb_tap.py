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
