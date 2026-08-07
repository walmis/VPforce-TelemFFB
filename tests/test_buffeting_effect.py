"""Stall/AoA buffeting effect: per-sim thresholds and q-relative magnitude.

MSFS knows the stall AoA and estimates the onset; X-Plane knows the onset
(author-set warning alpha) and estimates the stall. Magnitude scales with
dynamic pressure relative to the aircraft's OWN stall speed, so a slow
trainer buffets as fully at its stall as a jet does at its — the old fixed
0..75 kt scale permanently muted slow aircraft.
"""
import pytest

from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.base.BuffetingEffectMixIn import BuffetingEffectMixIn
from telemffb.util.conversions import kt2ms


class TestBuffetingEffect(BaseTelemetryEffectTestCase):
    def _inst(self, msfs=True):
        inst = self.create_test_instance(BuffetingEffectMixIn)
        if msfs:
            inst._test_sim_is_msfs = True
        else:
            inst._test_sim_is_xplane = True
        inst.buffeting_intensity = 0.2
        inst.aoa_buffeting_enabled = True
        return inst

    def _telem(self, **kw):
        b = TelemetryDataBuilder().ffb_type("joystick")
        t = b.build()
        # The builder ships a default DesignSpeed; real X-Plane frames never
        # carry it (MSFS-only), and it would hijack the stall-speed reference.
        t["DesignSpeed"] = kw.pop("DesignSpeed", None)
        for k, v in kw.items():
            t[k] = v
        t["WeightOnWheels"] = kw.get("WeightOnWheels", [0, 0, 0])
        return t

    def _mag(self, inst, telem):
        inst.ac_update_buffeting(telem)
        eff = self.mock_effects.get("buffeting")
        if eff is None or getattr(eff, "_periodic", None) is None:
            return None
        return eff._periodic[1]

    # ---- thresholds ------------------------------------------------------

    def test_msfs_thresholds_from_stall_aoa(self):
        inst = self._inst(msfs=True)
        # StallAoA 16: onset 8 (clean). Below onset: no effect.
        assert self._mag(inst, self._telem(AoA=7.9, TAS=60, StallAoA=16.0)) is None
        # At the stall: full AoA ramp.
        m = self._mag(inst, self._telem(AoA=16.0, TAS=60, StallAoA=16.0,
                                        DesignSpeed=(80.0, 25.0, 30.0)))
        assert m == pytest.approx(0.2 * min((60 / 30.0) ** 2, 1.5), abs=1e-6)

    def test_xplane_thresholds_straddle_warn_alpha(self):
        inst = self._inst(msfs=False)
        # WarnAlpha 12: band straddles the horn — onset 10.2 (85%), full at
        # 14.4 (120%). Below onset: nothing.
        assert self._mag(inst, self._telem(AoA=10.1, TAS=30, WarnAlpha=12.0,
                                           Vs=30.0)) is None
        # A light nibble is already present BEFORE the horn fires...
        m = self._mag(inst, self._telem(AoA=11.5, TAS=30, WarnAlpha=12.0,
                                        Vs=30.0))
        assert 0 < m < 0.2 * 0.45
        # ...and it is clearly building right AT the horn (~43% of peak).
        m = self._mag(inst, self._telem(AoA=12.0, TAS=30, WarnAlpha=12.0,
                                        Vs=30.0))
        assert m == pytest.approx(0.2 * (12.0 - 10.2) / (14.4 - 10.2), abs=1e-4)
        # Full magnitude at the estimated stall (120% of warn).
        m = self._mag(inst, self._telem(AoA=14.4, TAS=30, WarnAlpha=12.0,
                                        Vs=30.0))
        assert m == pytest.approx(0.2, abs=1e-4)

    def test_static_fallback_without_telemetry(self):
        inst = self._inst(msfs=False)
        # No WarnAlpha, no Vs: class 10/15 thresholds + legacy 75 kt scale.
        m = self._mag(inst, self._telem(AoA=15.0, TAS=75 * kt2ms))
        assert m == pytest.approx(0.2, abs=1e-6)
        m = self._mag(inst, self._telem(AoA=15.0, TAS=37.5 * kt2ms))
        assert m == pytest.approx(0.1, abs=1e-3)

    # ---- q-relative airflow ----------------------------------------------

    def test_slow_aircraft_reaches_full_magnitude_at_its_own_stall(self):
        # The fix: a 45 kt-stall trainer at its stall gets factor 1.0, where
        # the old fixed 0..75 kt scale gave it only 0.6.
        inst = self._inst(msfs=False)
        vs = 45 * kt2ms
        m = self._mag(inst, self._telem(AoA=15.0, TAS=vs, WarnAlpha=12.0,
                                        Vs=vs))
        assert m == pytest.approx(0.2, abs=1e-6)

    def test_accelerated_stall_buffets_harder_but_capped(self):
        inst = self._inst(msfs=False)
        vs = 45 * kt2ms
        # 1.2x Vs: q-ratio 1.44 -> stronger than 1g stall.
        m = self._mag(inst, self._telem(AoA=15.0, TAS=1.2 * vs, WarnAlpha=12.0,
                                        Vs=vs))
        assert m == pytest.approx(0.2 * 1.44, abs=1e-4)
        # 2x Vs: q-ratio 4 capped at ACCEL_STALL_Q_CAP.
        m = self._mag(inst, self._telem(AoA=15.0, TAS=2 * vs, WarnAlpha=12.0,
                                        Vs=vs))
        assert m == pytest.approx(0.2 * BuffetingEffectMixIn.ACCEL_STALL_Q_CAP,
                                  abs=1e-6)

    def test_flaps_blend_stall_reference(self):
        inst = self._inst(msfs=False)
        # Vs 30, Vso 24; full flaps -> vref 24, so TAS 24 is full q.
        m = self._mag(inst, self._telem(AoA=15.0, TAS=24.0, WarnAlpha=12.0,
                                        Vs=30.0, Vso=24.0, Flaps=1.0))
        assert m == pytest.approx(0.2, abs=1e-6)
        # Clean at the same 24 m/s: (24/30)^2 = 0.64 of peak.
        m = self._mag(inst, self._telem(AoA=15.0, TAS=24.0, WarnAlpha=12.0,
                                        Vs=30.0, Vso=24.0, Flaps=0.0))
        assert m == pytest.approx(0.2 * 0.64, abs=1e-4)

    def test_msfs_flaps_raise_onset_threshold(self):
        inst = self._inst(msfs=True)
        # StallAoA 16, full flaps: onset 16*(0.5+0.2)=11.2 — AoA 10 silent.
        assert self._mag(inst, self._telem(AoA=10.0, TAS=60, StallAoA=16.0,
                                           Flaps=1.0)) is None
        # Clean, onset 8: same AoA buffets.
        assert self._mag(inst, self._telem(AoA=10.0, TAS=60, StallAoA=16.0,
                                           Flaps=0.0)) is not None

    # ---- lifecycle -------------------------------------------------------

    def test_disable_disposes_running_effect(self):
        inst = self._inst(msfs=False)
        assert self._mag(inst, self._telem(AoA=15.0, TAS=30, WarnAlpha=12.0,
                                           Vs=30.0)) is not None
        inst.aoa_buffeting_enabled = False
        inst.ac_update_buffeting(self._telem(AoA=15.0, TAS=30, WarnAlpha=12.0,
                                             Vs=30.0))
        assert self.mock_effects.get("buffeting") is None

    # ---- renderer styles -------------------------------------------------

    def _freq_mag(self, inst, telem):
        inst.ac_update_buffeting(telem)
        eff = self.mock_effects.get("buffeting")
        assert eff is not None and eff._periodic is not None
        return eff._periodic[0], eff._periodic[1]

    def test_classic_style_is_unchanged(self):
        inst = self._inst(msfs=False)
        inst.stall_buffet_style = "Classic"
        f, m = self._freq_mag(inst, self._telem(AoA=14.4, TAS=30,
                                                WarnAlpha=12.0, Vs=30.0))
        assert f == inst.aoa_buffet_freq
        assert m == pytest.approx(0.2, abs=1e-4)

    def test_dynamic_style_envelope_and_frequency(self):
        import random as _random
        _random.seed(42)
        inst = self._inst(msfs=False)
        inst.stall_buffet_style = "Dynamic"
        # Force per-frame gust re-picks so a fast test loop sees variation.
        inst.DYN_ENV_PERIOD = (0.0, 1e-6)
        inst.DYN_ENV_TAU = 1e-9

        telem = self._telem(AoA=14.4, TAS=30, WarnAlpha=12.0, Vs=30.0)
        base = 0.2  # estimator magnitude at full ramp, own stall speed
        mags, freqs = [], []
        for _ in range(60):
            f, m = self._freq_mag(inst, telem)
            mags.append(m)
            freqs.append(f)
        # Envelope: within [floor, peak] x RMS compensation, varying, and
        # MEAN-NEUTRAL in PERCEIVED terms: the triangle carrier is ~18%
        # quieter RMS than Classic's sine, so commanded magnitude runs
        # base * DYN_WAVEFORM_RMS_COMP on average — equal felt level.
        comp = inst.DYN_WAVEFORM_RMS_COMP
        assert min(mags) >= base * inst.DYN_ENV_FLOOR * comp - 1e-6
        assert max(mags) <= base * inst.DYN_ENV_PEAK * comp + 1e-6
        assert max(mags) - min(mags) > 0.02 * base, "envelope never varied"
        mean = sum(mags) / len(mags)
        assert mean == pytest.approx(base * comp, rel=0.25), \
            f"envelope not mean-neutral (mean {mean} vs {base * comp})"
        # Dynamic renders a triangle carrier; Classic stays on default sine.
        from telemffb.hw.ffb_rhino import EFFECT_TRIANGLE
        kwargs = self.mock_effects["buffeting"]._periodic[3]
        assert kwargs.get("effect_type") == EFFECT_TRIANGLE
        # Depth 1.0 -> deep-stall frequency: slider + DEEP_DELTA (13-4=9).
        f_deep = inst.aoa_buffet_freq + inst.DYN_FREQ_DEEP_DELTA
        assert all(f == pytest.approx(f_deep) for f in freqs)

        # Shallow depth -> higher frequency (light onset nibble).
        onset_telem = self._telem(AoA=10.5, TAS=30, WarnAlpha=12.0, Vs=30.0)
        f_shallow, _ = self._freq_mag(inst, onset_telem)
        assert f_shallow > f_deep
        assert f_shallow <= inst.aoa_buffet_freq + inst.DYN_FREQ_ONSET_DELTA

        # The Buffeting Frequency slider anchors the whole sweep in Dynamic
        # mode too (it is not a Classic-only control).
        inst.aoa_buffet_freq = 20
        f, _ = self._freq_mag(inst, telem)  # depth 1.0
        assert f == pytest.approx(20 + inst.DYN_FREQ_DEEP_DELTA)
        inst.aoa_buffet_freq = 5   # sweep floor engages
        f, _ = self._freq_mag(inst, telem)
        assert f == pytest.approx(inst.DYN_FREQ_MIN)

    def test_dynamic_style_aims_single_axis_devices(self):
        # Pedals feel only X (90/270) and collective only Y (0/180): Dynamic
        # aims the carrier along the felt axis there — the random walk would
        # project into amplitude noise that masks the gust envelope.
        import telemffb.utils as utils
        for ffb, want in (("pedals", 90), ("collective", 0)):
            inst = self._inst(msfs=False)
            inst.stall_buffet_style = "Dynamic"
            telem = self._telem(AoA=14.4, TAS=30, WarnAlpha=12.0, Vs=30.0,
                                FFBType=ffb)
            inst._telem_data = telem
            inst.ac_update_buffeting(telem)
            assert self.mock_effects["buffeting"]._periodic[2] == want
        # Joysticks keep the random-direction walk.
        inst = self._inst(msfs=False)
        inst.stall_buffet_style = "Dynamic"
        telem = self._telem(AoA=14.4, TAS=30, WarnAlpha=12.0, Vs=30.0)
        inst._telem_data = telem
        inst.ac_update_buffeting(telem)
        assert (self.mock_effects["buffeting"]._periodic[2]
                is utils.RandomDirectionModulator)

    def test_on_ground_disposes(self):
        inst = self._inst(msfs=False)
        assert self._mag(inst, self._telem(AoA=15.0, TAS=30, WarnAlpha=12.0,
                                           Vs=30.0)) is not None
        inst.ac_update_buffeting(self._telem(AoA=15.0, TAS=30, WarnAlpha=12.0,
                                             Vs=30.0, WeightOnWheels=[1, 1, 1]))
        assert self.mock_effects.get("buffeting") is None
