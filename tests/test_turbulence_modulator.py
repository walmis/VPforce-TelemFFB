import math
import time
from unittest.mock import patch

import pytest

from telemffb.util.TurbulenceModulator import (
    TurbulenceForces,
    TurbulenceModulator,
    _LAT_TO_ROLL,
    _LAT_TO_YAW,
    _LON_TO_PITCH,
    _REF_DT,
    _VERT_TO_PITCH,
)


@pytest.fixture
def modulator():
    return TurbulenceModulator()


def _prime(modulator, lat=0.0, vert=0.0, lon=0.0):
    """Prime the modulator with an initial sample so the next call produces output."""
    modulator.update(lat, vert, lon)


class TestInitialisation:
    def test_first_call_returns_zero(self, modulator):
        result = modulator.update(1.0, 2.0, 3.0)
        assert result == TurbulenceForces(0.0, 0.0, 0.0)

    def test_first_call_stores_prev_values(self, modulator):
        modulator.update(3.5, -1.2, 0.8)
        assert modulator._prev_lateral == 3.5
        assert modulator._prev_vertical == -1.2
        assert modulator._prev_longitudinal == 0.8
        assert modulator._prev_time is not None

    def test_return_type_is_named_tuple(self, modulator):
        result = modulator.update(0.0, 0.0, 0.0)
        assert isinstance(result, TurbulenceForces)
        assert hasattr(result, 'pitch')
        assert hasattr(result, 'roll')
        assert hasattr(result, 'yaw')


class TestBasicOutput:
    def test_no_change_produces_zero_forces(self, modulator):
        _prime(modulator, 5.0, 3.0, 5.0)
        result = modulator.update(5.0, 3.0, 5.0)
        assert result.pitch == pytest.approx(0.0, abs=1e-6)
        assert result.roll == pytest.approx(0.0, abs=1e-6)
        assert result.yaw == pytest.approx(0.0, abs=1e-6)

    def test_vertical_gust_produces_pitch_force(self, modulator):
        _prime(modulator)
        result = modulator.update(0.0, 5.0, 0.0, intensity=1.0, sensitivity=1.0)
        assert abs(result.pitch) > 0.0
        assert result.roll == pytest.approx(0.0, abs=1e-6)

    def test_lateral_gust_produces_roll_and_yaw(self, modulator):
        _prime(modulator)
        result = modulator.update(5.0, 0.0, 0.0, intensity=1.0, sensitivity=1.0)
        assert abs(result.roll) > 0.0
        assert abs(result.yaw) > 0.0
        assert result.pitch == pytest.approx(0.0, abs=1e-6)

    def test_longitudinal_gust_produces_pitch_only(self, modulator):
        _prime(modulator)
        result = modulator.update(0.0, 0.0, 5.0, intensity=1.0, sensitivity=1.0)
        assert abs(result.pitch) > 0.0
        assert result.roll == pytest.approx(0.0, abs=1e-6)
        # Yaw unaffected by longitudinal gusts
        assert result.yaw == pytest.approx(0.0, abs=1e-6)

    def test_forces_bounded_by_intensity(self, modulator):
        _prime(modulator)
        result = modulator.update(100.0, 100.0, 100.0, intensity=0.3, sensitivity=1.0)
        # Joystick magnitude bounded
        stick_mag = math.sqrt(result.pitch ** 2 + result.roll ** 2)
        assert stick_mag <= 0.3 + 1e-6
        # Yaw bounded
        assert abs(result.yaw) <= 0.3 + 1e-6


class TestHighPassFilter:
    """Verify HPF behaviour: sustained offsets decay, transients pass."""

    def test_constant_wind_decays_to_zero(self, modulator):
        # Fixed dt via patched time: at the microsecond dt of an unthrottled
        # loop the dt-normalised alpha is ~1.0, so the HPF barely decays and
        # the test only passed because the output accumulates in proportion
        # to total loop wall time - one scheduler stall mid-loop blew the
        # threshold. Stepping _REF_DT per frame tests the actual decay.
        t0 = 1000.0
        with patch('telemffb.util.TurbulenceModulator.time') as mock_time:
            mock_time.perf_counter.return_value = t0
            modulator.update(0.0, 0.0, 0.0)  # prime
            # Step change, then hold constant across 200 fixed 60 Hz frames
            for i in range(1, 202):
                mock_time.perf_counter.return_value = t0 + i * _REF_DT
                result = modulator.update(10.0, 10.0, 10.0, intensity=1.0, sensitivity=1.0)
        assert abs(result.pitch) < 0.01
        assert abs(result.roll) < 0.01
        assert abs(result.yaw) < 0.01

    def test_alternating_gusts_stay_nonzero(self, modulator):
        _prime(modulator)
        for i in range(50):
            val = 5.0 if i % 2 == 0 else -5.0
            result = modulator.update(val, val, 0.0, intensity=1.0, sensitivity=1.0)
        # Both pitch (from vertical) and roll (from lateral) should be active
        assert abs(result.pitch) > 0.0 or abs(result.roll) > 0.0


class TestDtNormalisation:
    """Verify that the dt correction keeps the filter stable across frame rates."""

    def test_alpha_correction_at_double_dt(self, modulator):
        """At 2× the reference dt, effective alpha should be α^2."""
        _prime(modulator)

        with patch('telemffb.util.TurbulenceModulator.time') as mock_time:
            t0 = 1000.0
            mock_time.perf_counter.return_value = t0 + 2 * _REF_DT
            modulator._prev_time = t0

            modulator.update(10.0, 0.0, 0.0, hpf_alpha=0.9, intensity=1.0, sensitivity=1.0)

        expected_alpha = 0.9 ** 2
        expected_hpf = expected_alpha * 10.0  # delta=10, hpf was 0
        assert modulator.hpf_lat == pytest.approx(expected_hpf, rel=1e-4)

    def test_alpha_correction_at_half_dt(self, modulator):
        """At 0.5× the reference dt, effective alpha should be α^0.5."""
        _prime(modulator)

        with patch('telemffb.util.TurbulenceModulator.time') as mock_time:
            t0 = 1000.0
            mock_time.perf_counter.return_value = t0 + 0.5 * _REF_DT
            modulator._prev_time = t0

            modulator.update(10.0, 0.0, 0.0, hpf_alpha=0.9, intensity=1.0, sensitivity=1.0)

        expected_alpha = 0.9 ** 0.5
        expected_hpf = expected_alpha * 10.0
        assert modulator.hpf_lat == pytest.approx(expected_hpf, rel=1e-4)


class TestSensitivity:
    def _pitch_for_sensitivity(self, sens):
        # Fixed dt via patched time (same pattern as TestSymmetry): with real
        # wall-clock time the output is proportional to the microsecond-scale,
        # jittery gap between the priming and measuring calls (dt-normalised
        # smoothing), which made comparing two separate runs flaky.
        m = TurbulenceModulator()
        t0 = 1000.0
        with patch('telemffb.util.TurbulenceModulator.time') as mock_time:
            mock_time.perf_counter.return_value = t0
            m.update(0.0, 0.0, 0.0)
            mock_time.perf_counter.return_value = t0 + _REF_DT
            result = m.update(0.0, 3.0, 0.0, sensitivity=sens, intensity=1.0)
        return abs(result.pitch)

    def test_higher_sensitivity_produces_more_force(self):
        low = self._pitch_for_sensitivity(0.1)
        high = self._pitch_for_sensitivity(0.9)
        assert high > low


class TestPhysicsMapping:
    """Verify aerodynamic gust → stick-force mapping is physically correct."""

    def test_updraft_produces_pitch_up(self, modulator):
        """Updraft (+Y) increases AoA → nose up → stick pulls back (positive pitch)."""
        _prime(modulator)
        result = modulator.update(0.0, 10.0, 0.0, intensity=1.0, sensitivity=1.0)
        assert result.pitch > 0.0

    def test_downdraft_produces_pitch_down(self, modulator):
        """Downdraft (-Y) decreases AoA → nose down → stick pushes forward (negative pitch)."""
        _prime(modulator)
        result = modulator.update(0.0, -10.0, 0.0, intensity=1.0, sensitivity=1.0)
        assert result.pitch < 0.0

    def test_headwind_increase_produces_small_pitch_up(self, modulator):
        """Forward gust (+lon) increases lift → slight nose up (positive pitch)."""
        _prime(modulator)
        result = modulator.update(0.0, 0.0, 10.0, intensity=1.0, sensitivity=1.0)
        assert result.pitch > 0.0

    def test_starboard_gust_produces_roll_left(self, modulator):
        """Starboard gust (+lat) → port sideslip → dihedral/sweep rolls LEFT (negative roll)."""
        _prime(modulator)
        result = modulator.update(10.0, 0.0, 0.0, intensity=1.0, sensitivity=1.0)
        assert result.roll < 0.0

    def test_port_gust_produces_roll_right(self, modulator):
        """Port gust (-lat) → starboard sideslip → rolls RIGHT (positive roll)."""
        _prime(modulator)
        result = modulator.update(-10.0, 0.0, 0.0, intensity=1.0, sensitivity=1.0)
        assert result.roll > 0.0

    def test_starboard_gust_produces_yaw_left(self, modulator):
        """Starboard gust (+lat) → weathervane yaws LEFT (negative yaw)."""
        _prime(modulator)
        result = modulator.update(10.0, 0.0, 0.0, intensity=1.0, sensitivity=1.0)
        assert result.yaw < 0.0

    def test_port_gust_produces_yaw_right(self, modulator):
        """Port gust (-lat) → weathervane yaws RIGHT (positive yaw)."""
        _prime(modulator)
        result = modulator.update(-10.0, 0.0, 0.0, intensity=1.0, sensitivity=1.0)
        assert result.yaw > 0.0

    def test_vertical_dominates_longitudinal_for_pitch(self, modulator):
        """Same-magnitude vertical gust produces more pitch than longitudinal."""
        # Fixed dt + sub-clamp gust: the old 10-unit gust at sensitivity 1.0
        # saturated the intensity clamp in BOTH runs, so the outputs were
        # equal targets scaled by each run's jittery wall-clock dt - a coin
        # flip. A 2-unit gust at sensitivity 0.5 stays under the clamp so the
        # 1.0 vs 0.25 gain ratio is actually observable.
        def pitch_for(vert, lon):
            m = TurbulenceModulator()
            t0 = 1000.0
            with patch('telemffb.util.TurbulenceModulator.time') as mock_time:
                mock_time.perf_counter.return_value = t0
                m.update(0.0, 0.0, 0.0)
                mock_time.perf_counter.return_value = t0 + _REF_DT
                return abs(m.update(0.0, vert, lon, intensity=1.0, sensitivity=0.5).pitch)

        assert pitch_for(2.0, 0.0) > pitch_for(0.0, 2.0)


class TestAxisMixingGains:
    """Verify the relative weights of axis mixing gains."""

    def test_vertical_pitch_gain_is_dominant(self):
        assert _VERT_TO_PITCH > _LON_TO_PITCH

    def test_lateral_roll_stronger_than_yaw(self):
        assert _LAT_TO_ROLL > _LAT_TO_YAW


class TestSymmetry:
    """Magnitude should be the same regardless of sign."""

    def _make_modulator_with_fixed_dt(self, t0=1000.0):
        m = TurbulenceModulator()
        with patch('telemffb.util.TurbulenceModulator.time') as mock_time:
            mock_time.perf_counter.return_value = t0
            m.update(0.0, 0.0, 0.0)
        return m, t0

    def test_positive_vs_negative_lateral_same_roll_magnitude(self):
        m1, t0 = self._make_modulator_with_fixed_dt()
        with patch('telemffb.util.TurbulenceModulator.time') as mt:
            mt.perf_counter.return_value = t0 + _REF_DT
            r1 = m1.update(5.0, 0.0, 0.0, intensity=1.0, sensitivity=0.5)

        m2, t0 = self._make_modulator_with_fixed_dt()
        with patch('telemffb.util.TurbulenceModulator.time') as mt:
            mt.perf_counter.return_value = t0 + _REF_DT
            r2 = m2.update(-5.0, 0.0, 0.0, intensity=1.0, sensitivity=0.5)

        assert abs(r1.roll) == pytest.approx(abs(r2.roll), rel=0.01)

    def test_positive_vs_negative_vertical_same_pitch_magnitude(self):
        m1, t0 = self._make_modulator_with_fixed_dt()
        with patch('telemffb.util.TurbulenceModulator.time') as mt:
            mt.perf_counter.return_value = t0 + _REF_DT
            r1 = m1.update(0.0, 5.0, 0.0, intensity=1.0, sensitivity=0.5)

        m2, t0 = self._make_modulator_with_fixed_dt()
        with patch('telemffb.util.TurbulenceModulator.time') as mt:
            mt.perf_counter.return_value = t0 + _REF_DT
            r2 = m2.update(0.0, -5.0, 0.0, intensity=1.0, sensitivity=0.5)

        assert abs(r1.pitch) == pytest.approx(abs(r2.pitch), rel=0.01)

    def test_positive_vs_negative_lateral_same_yaw_magnitude(self):
        m1, t0 = self._make_modulator_with_fixed_dt()
        with patch('telemffb.util.TurbulenceModulator.time') as mt:
            mt.perf_counter.return_value = t0 + _REF_DT
            r1 = m1.update(5.0, 0.0, 0.0, intensity=1.0, sensitivity=0.5)

        m2, t0 = self._make_modulator_with_fixed_dt()
        with patch('telemffb.util.TurbulenceModulator.time') as mt:
            mt.perf_counter.return_value = t0 + _REF_DT
            r2 = m2.update(-5.0, 0.0, 0.0, intensity=1.0, sensitivity=0.5)

        assert abs(r1.yaw) == pytest.approx(abs(r2.yaw), rel=0.01)
