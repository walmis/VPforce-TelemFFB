"""Prop engine rumble (Classic renderer).

Pins the shipped structure — two detuned pairs at 0/90 driven by the
Low/High RPM intensity taper — plus the one deliberate change: the +-3 Hz
detune sweeps taper inversely with frequency above the Low RPM point, so
the chunky idle throb is untouched while cruise loses the robotic
shifting undertones.
"""
import pytest

from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.base.EngineRumbleMixIn import EngineRumbleMixIn
import telemffb.utils as utils


class TestPropEngineRumble(BaseTelemetryEffectTestCase):
    def _inst(self):
        inst = self.create_test_instance(EngineRumbleMixIn)
        inst.engine_prop_rumble_enabled = True
        inst.engine_rumble_lowrpm = 650
        inst.engine_rumble_lowrpm_intensity = 0.06
        inst.engine_rumble_highrpm = 2800
        inst.engine_rumble_highrpm_intensity = 0.03
        return inst

    def _telem(self, **kw):
        t = TelemetryDataBuilder().ffb_type("joystick").build()
        for k, v in kw.items():
            t[k] = v
        return t

    def _update(self, inst, telem):
        # _sim_is() reads the bound telemetry's src.
        inst._telem_data = telem
        inst.ac_update_piston_engine_rumble(telem)

    def test_classic_renderer_structure(self):
        inst = self._inst()
        self._update(inst, self._telem(PropRPM=2400.0))
        base = inst.ac_calc_engine_intensity(2400.0)
        depth = ((650 / 60.0) / 40.0) ** 2
        f, m, d, kw = self.mock_effects["prop_rpm0-1"]._periodic
        assert f == pytest.approx(2400.0 / 60)
        assert m == pytest.approx(base * (2.0 - depth ** 2) ** 0.5, abs=1e-6)
        assert d == 0
        assert "effect_type" not in kw  # sine carrier
        # Second pair at +2 Hz on the other axis.
        assert self.mock_effects["prop_rpm1-1"]._periodic[0] == pytest.approx(42.0)
        assert self.mock_effects["prop_rpm1-1"]._periodic[2] == 90

    def test_beat_depth_fades_at_cruise_but_not_at_idle(self):
        # A twin at EQUAL amplitude beats at full depth however small the
        # detune, so cruise kept a slow wah-wah even with the sweep tapered
        # (field report). The twin's amplitude now fades with the same
        # inverse-frequency law: at/below the Low RPM point all four tones
        # are equal (bit-identical legacy chunk), at cruise the twins are
        # nearly gone and the mains carry the pair's RMS.
        inst = self._inst()
        self._update(inst, self._telem(PropRPM=650.0))
        base = inst.ac_calc_engine_intensity(650.0)
        mags = [self.mock_effects[s]._periodic[1]
                for s in ("prop_rpm0-1", "prop_rpm0-2",
                          "prop_rpm1-1", "prop_rpm1-2")]
        assert mags == [pytest.approx(base, abs=1e-6)] * 4
        self._update(inst, self._telem(PropRPM=2400.0))
        base = inst.ac_calc_engine_intensity(2400.0)
        depth = ((650 / 60.0) / 40.0) ** 2
        main = self.mock_effects["prop_rpm0-1"]._periodic[1]
        twin = self.mock_effects["prop_rpm0-2"]._periodic[1]
        assert twin == pytest.approx(base * depth, abs=1e-6)
        assert twin / main < 0.06, "beat depth must be residual at cruise"
        # Felt level preserved: pair RMS matches the legacy equal pair.
        assert (main ** 2 + twin ** 2) ** 0.5 == \
            pytest.approx(base * 2 ** 0.5, rel=0.01)

    def test_classic_multi_engine_takes_max(self):
        inst = self._inst()
        self._update(inst, self._telem(PropRPM=[2400.0, 2430.0]))
        assert self.mock_effects["prop_rpm0-1"]._periodic[0] == \
            pytest.approx(2430.0 / 60)

    def test_classic_detune_tapers_above_low_rpm_only(self, monkeypatch):
        # The +-3 Hz sweep is full-depth at and below the Low RPM point
        # (the chunky idle throb) and shrinks as 1/f above it (the fix
        # for robotic shifting undertones at cruise).
        amplitudes = []

        def fake_sine(amplitude, period_ms, phase_offset_deg=0):
            amplitudes.append(amplitude)
            return 0.0

        monkeypatch.setattr(utils, "sine_point_in_time", fake_sine)
        inst = self._inst()
        self._update(inst, self._telem(PropRPM=650.0))
        assert amplitudes[-2:] == [pytest.approx(3.0)] * 2
        self._update(inst, self._telem(PropRPM=300.0))  # below: still full
        assert amplitudes[-2:] == [pytest.approx(3.0)] * 2
        self._update(inst, self._telem(PropRPM=2400.0))
        assert amplitudes[-2:] == [pytest.approx(3.0 * (650 / 60.0) / 40.0)] * 2

    def test_uses_per_sim_rpm_sources(self):
        inst = self._inst()
        self._update(inst, self._telem(src="DCS", ActualRPM=[3000.0, 2970.0]))
        assert self.mock_effects["prop_rpm0-1"]._periodic[0] == pytest.approx(50.0)
        inst = self._inst()
        self._update(inst, self._telem(src="IL2", RPM=1800.0))
        assert self.mock_effects["prop_rpm0-1"]._periodic[0] == pytest.approx(30.0)

    def test_disposes_when_disabled_or_stopped(self):
        inst = self._inst()
        self._update(inst, self._telem(PropRPM=2400.0))
        assert self.mock_effects.get("prop_rpm0-1") is not None
        self._update(inst, self._telem(PropRPM=0.0))
        assert self.mock_effects.get("prop_rpm0-1") is None
        self._update(inst, self._telem(PropRPM=2400.0))
        inst.engine_prop_rumble_enabled = False
        self._update(inst, self._telem(PropRPM=2400.0))
        assert self.mock_effects.get("prop_rpm0-1") is None


class TestSimplifiedPropEngineRumble(BaseTelemetryEffectTestCase):
    """One sine per axis carrying the tone and twin's beat."""

    def _inst(self, simplified=True):
        inst = self.create_test_instance(EngineRumbleMixIn)
        inst.engine_prop_rumble_enabled = True
        inst.engine_prop_rumble_simplified = simplified
        inst.engine_rumble_lowrpm = 650
        inst.engine_rumble_lowrpm_intensity = 0.06
        inst.engine_rumble_highrpm = 2800
        inst.engine_rumble_highrpm_intensity = 0.03
        return inst

    def _update(self, inst, rpm):
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        telem["PropRPM"] = rpm
        inst._telem_data = telem
        inst.ac_update_piston_engine_rumble(telem)

    def _clock(self, monkeypatch, detune_hz):
        import telemffb.sim.base.EngineRumbleMixIn as module
        clock = {"now": 100.0}
        monkeypatch.setattr(module.time, "perf_counter", lambda: clock["now"])
        monkeypatch.setattr(utils, "sine_point_in_time",
                            lambda amplitude, period_ms, phase_offset_deg=0: detune_hz)
        return clock

    def test_plays_two_effects(self):
        inst = self._inst()
        self._update(inst, 2400.0)
        assert self.mock_effects.get("prop_rpm0-1") is not None
        assert self.mock_effects.get("prop_rpm1-1") is not None
        assert self.mock_effects.get("prop_rpm0-2") is None
        assert self.mock_effects.get("prop_rpm1-2") is None

    def test_full_depth_beat_swings_from_the_pair_sum_to_a_null(self, monkeypatch):
        clock = self._clock(monkeypatch, 2.0)
        inst = self._inst()
        self._update(inst, 650.0)
        base = inst.ac_calc_engine_intensity(650.0)
        f, m, d, _ = self.mock_effects["prop_rpm0-1"]._periodic
        assert m == pytest.approx(2 * base, abs=1e-6)
        assert f == pytest.approx(650.0 / 60 + 1.0)  # mean of the tone and its twin
        assert d == 0
        clock["now"] += 0.25  # half a beat at 2 Hz
        self._update(inst, 650.0)
        assert self.mock_effects["prop_rpm0-1"]._periodic[1] == pytest.approx(0.0, abs=1e-6)
        clock["now"] += 0.25
        self._update(inst, 650.0)
        assert self.mock_effects["prop_rpm0-1"]._periodic[1] == pytest.approx(2 * base, abs=1e-6)
        assert self.mock_effects["prop_rpm1-1"]._periodic[2] == 90

    def test_cruise_beat_is_residual(self, monkeypatch):
        clock = self._clock(monkeypatch, 1.0)
        inst = self._inst()
        self._update(inst, 2400.0)
        peak = self.mock_effects["prop_rpm0-1"]._periodic[1]
        clock["now"] += 0.5
        self._update(inst, 2400.0)
        trough = self.mock_effects["prop_rpm0-1"]._periodic[1]
        assert trough < peak
        assert trough / peak > 0.85

    def test_updates_are_capped_whatever_the_telemetry_rate(self, monkeypatch):
        clock = self._clock(monkeypatch, 2.0)
        inst = self._inst()
        sends = []
        self._update(inst, 650.0)
        effect = self.mock_effects["prop_rpm0-1"]
        real_periodic = effect.periodic
        monkeypatch.setattr(effect, "periodic",
                            lambda *a, **kw: sends.append(clock["now"]) or real_periodic(*a, **kw))
        for fps, expected in ((120, 30), (60, 30), (30, 30)):
            sends.clear()
            for _ in range(fps):
                clock["now"] += 1.0 / fps
                self._update(inst, 650.0)
            assert len(sends) == pytest.approx(expected, abs=1)
        # the beat keeps its timing between sends: half a beat on is a null
        clock["now"] += 0.25
        self._update(inst, 650.0)
        assert effect._periodic[1] == pytest.approx(0.0, abs=1e-6)

    def test_switching_modes_swaps_the_twins(self):
        inst = self._inst(simplified=False)
        self._update(inst, 2400.0)
        assert self.mock_effects.get("prop_rpm0-2") is not None
        inst.engine_prop_rumble_simplified = True
        self._update(inst, 2400.0)
        assert self.mock_effects.get("prop_rpm0-2") is None
        inst.engine_prop_rumble_simplified = False
        self._update(inst, 2400.0)
        assert self.mock_effects.get("prop_rpm0-2") is not None
