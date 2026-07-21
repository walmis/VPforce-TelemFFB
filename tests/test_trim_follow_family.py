"""Multi-speed trim-calibration family: parsing, the positional track R(v),
and the anchor-aligned blend evaluator (telemffb.utils pure functions)."""
import json
import math

import pytest

from telemffb.utils import (
    parse_trim_follow_curve, parse_trim_follow_family, trim_follow_blend,
    piecewise_linear,
)

pytestmark = [pytest.mark.unit, pytest.mark.msfs, pytest.mark.joystick]


def entry(ias_kt, t0, slope, half=0.2, date="2026-07-20"):
    """A gauge-zero-referenced stored entry with linear offs slope ``slope``
    over t0 +- half — exactly what the solver writes."""
    ts = (t0 - half, t0 + half)
    return {
        "ias_kt": ias_kt, "t0": t0, "date": date,
        "points": [{"t": t, "offs": slope * t} for t in ts],
    }


class TestFamilyParse:
    def test_legacy_single_blob_is_one_entry_family(self):
        raw = {"points": [{"t": -0.37, "offs": -0.814}, {"t": 0.03, "offs": 0.066}],
               "t0": -0.1, "ias_kt": 132.0, "date": "2026-07-18"}
        fam = parse_trim_follow_family(json.dumps(raw))
        assert len(fam) == 1
        e = fam[0]
        assert e["ias_kt"] == pytest.approx(132.0)
        # anchor-rebased: offs(t0) == 0
        assert piecewise_linear(e["xs"], e["ys"], -0.1) == pytest.approx(0.0, abs=1e-9)
        assert e["r"] == pytest.approx(0.0)

    def test_family_form_sorts_by_speed(self):
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(200, -0.1, 2.0), entry(100, 0.0, 2.0)]}))
        assert [e["ias_kt"] for e in fam] == [100, 200]

    def test_bad_entries_skipped_good_ones_kept(self):
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [{"points": [{"t": 0.0, "offs": 0.0}]},   # 1 point: dropped
                        entry(100, 0.0, 2.0)]}))
        assert len(fam) == 1 and fam[0]["ias_kt"] == 100

    def test_unusable_values_return_none(self):
        assert parse_trim_follow_family(None) is None
        assert parse_trim_follow_family("none") is None
        assert parse_trim_follow_family("{not valid") is None
        assert parse_trim_follow_family(json.dumps(
            {"curves": [{"points": [{"t": 0.0, "offs": 0.0}]}]})) is None

    def test_near_duplicate_speeds_keep_later_payload_entry(self):
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(100.0, 0.0, 2.0, date="old"),
                        entry(100.3, 0.0, 3.0, date="new")]}))
        assert len(fam) == 1
        assert fam[0]["date"] == "new"

    def test_r_chain_hand_computed_two_entries(self):
        # A: 100 kt, slope 2, anchor 0; B: 200 kt, slope 2, anchor -0.1.
        # est_via_A = S_A(-0.1) = -0.2; est_via_B = -S_B(+0.1) = -0.2
        # chain [0, -0.2]; median index 1 -> normalized [+0.2, 0.0]
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(100, 0.0, 2.0), entry(200, -0.1, 2.0)]}))
        assert fam[0]["r"] == pytest.approx(+0.2, abs=1e-9)
        assert fam[1]["r"] == pytest.approx(0.0, abs=1e-9)

    def test_r_median_normalization_three_entries(self):
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(100, 0.1, 2.0), entry(150, 0.0, 2.0),
                        entry(200, -0.1, 2.0)]}))
        assert fam[1]["r"] == pytest.approx(0.0, abs=1e-9)
        # slope 2 x anchor step 0.1 per segment
        assert fam[0]["r"] == pytest.approx(+0.2, abs=1e-9)
        assert fam[2]["r"] == pytest.approx(-0.2, abs=1e-9)

    def test_r_clamps_at_stick_limits_with_warning(self, caplog):
        # Hawk-like: slope 14 x anchor step 0.12 = 1.68 -> clamped to 1.0
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(150, 0.06, 14.0, half=0.05),
                        entry(250, -0.06, 14.0, half=0.05)]}))
        assert fam[0]["r"] == pytest.approx(1.0)
        assert any("positional track clamped" in r.message for r in caplog.records)

    def test_legacy_wrapper_returns_median_entry(self):
        fam_json = json.dumps({"curves": [entry(100, 0.0, 2.0),
                                          entry(200, -0.1, 3.0)]})
        xs, ys = parse_trim_follow_curve(fam_json)
        # median index 1 = the 200 kt entry (slope 3)
        assert (ys[-1] - ys[0]) / (xs[-1] - xs[0]) == pytest.approx(3.0)


class TestBlend:
    def test_single_entry_matches_direct_lookup(self):
        fam = parse_trim_follow_family(json.dumps(entry(100, -0.05, 2.5)))
        e = fam[0]
        for t in (-0.2, -0.05, 0.0, 0.1):
            expect = piecewise_linear(e["xs"], e["ys"], t)
            assert trim_follow_blend(fam, t, 100) == pytest.approx(expect, abs=1e-9)
            # speed is irrelevant with one entry
            assert trim_follow_blend(fam, t, 500) == pytest.approx(expect, abs=1e-9)

    def test_exact_endpoints_and_beyond_range_clamp(self):
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(100, 0.0, 2.0), entry(200, 0.0, 4.0)]}))
        for t in (-0.1, 0.05, 0.15):
            lo_val = trim_follow_blend(fam, t, 100)
            hi_val = trim_follow_blend(fam, t, 200)
            # beyond range: the exact nearest calibration, no extrapolation
            assert trim_follow_blend(fam, t, 60) == pytest.approx(lo_val, abs=1e-9)
            assert trim_follow_blend(fam, t, 400) == pytest.approx(hi_val, abs=1e-9)
            assert lo_val == pytest.approx(2.0 * t, abs=1e-9)
            assert hi_val == pytest.approx(4.0 * t, abs=1e-9)

    def test_mid_speed_slope_lerps(self):
        # C208-style: same anchor, gain rides speed. At 150 kt, slope = 3.
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(100, 0.0, 2.0), entry(200, 0.0, 4.0)]}))
        d = trim_follow_blend(fam, 0.1, 150) - trim_follow_blend(fam, -0.1, 150)
        assert d / 0.2 == pytest.approx(3.0, abs=1e-9)

    def test_translation_invariance_reconstructs_moving_anchor(self):
        # SR22T-style: identical shape, translated anchor. The blended curve
        # at any intermediate speed must equal S(T - t0(v)) exactly — the
        # decisive anchor-alignment property that absolute-trim lerping
        # destroys (measured 2.5x under-correction at the knee).
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(100, 0.1, 2.0), entry(200, -0.1, 2.0)]}))
        for v, t0v in ((125, 0.05), (150, 0.0), (175, -0.05)):
            for t in (t0v - 0.15, t0v, t0v + 0.08):
                expect = 2.0 * (t - t0v)
                got = trim_follow_blend(fam, t, v, include_r=False)
                assert got == pytest.approx(expect, abs=1e-9), (v, t)

    def test_include_r_adds_exactly_the_lerped_track(self):
        fam = parse_trim_follow_family(json.dumps(
            {"curves": [entry(100, 0.1, 2.0), entry(200, -0.1, 2.0)]}))
        # chain: est both = 2 * (-0.2) = -0.4 -> r = [+0.4, 0]
        assert fam[0]["r"] == pytest.approx(0.4, abs=1e-9)
        for v, r_expect in ((100, 0.4), (150, 0.2), (200, 0.0), (60, 0.4), (400, 0.0)):
            with_r = trim_follow_blend(fam, 0.0, v, include_r=True)
            without = trim_follow_blend(fam, 0.0, v, include_r=False)
            assert with_r - without == pytest.approx(r_expect, abs=1e-9)

    def test_output_clamped_to_unit_range(self):
        fam = parse_trim_follow_family(json.dumps(entry(100, 0.0, 14.0, half=0.05)))
        assert trim_follow_blend(fam, 0.5, 100) == 1.0
        assert trim_follow_blend(fam, -0.5, 100) == -1.0
