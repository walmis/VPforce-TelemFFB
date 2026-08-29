"""
Regression tests for MsfsXpTrimwheelMixIn.

The mixin-split refactor replaced the trimwheel's direct-write mode (the
default: set the ELEVATOR TRIM POSITION SimVar, mapped through the trim
travel limits) with ``raise ValueError("Trimwheel must use axis")``, killing
the mode entirely. These tests pin down both write methods and the
missing-limits fallbacks so the direct path cannot silently die again.
"""
import time

import pytest

import telemffb.globals as G
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.MsfsXpTrimwheelMixIn import MsfsXpTrimwheelMixIn


@pytest.fixture(autouse=True)
def _reset_trimwheel_hold():
    G.trimcal_hold_until = 0.0
    yield
    G.trimcal_hold_until = 0.0


class TestTrimwheelWriteMethods(BaseTelemetryEffectTestCase):
    def _trimwheel(self, use_axis, phys_y=0.05):
        instance = self.create_test_instance(
            MsfsXpTrimwheelMixIn,
            telemffb_controls_axes=True,
            trimwheel_use_axis=use_axis,
        )
        instance._test_sim_is_msfs = True
        self.mock_device.get_input().set_axis(x=0.0, y=phys_y)
        return instance

    def _telem(self, **extra):
        builder = (
            TelemetryDataBuilder()
            .ffb_type("trimwheel")
            .set("APMaster", 0)
            .set("ElevTrimPct", 0.0)
        )
        for key, value in extra.items():
            builder.set(key, value)
        return builder.build()

    def test_direct_mode_writes_trim_position_simvar(self):
        # THE regression: direct mode (use_axis=False, the class default)
        # raised ValueError every frame after the refactor instead of writing
        # the surface position.
        instance = self._trimwheel(use_axis=False)
        telem = self._telem(ElevTrim=2.0, ElevTrimMax=10.0, ElevTrimMin=-10.0)
        instance._telem_data = telem

        instance.msfs_update_trimwheel(telem)

        assert instance.trimwheel_init == 1
        # spring mirrors the sim trim mapped through the travel limits
        assert telem["trimwheel_pos_calc"] == pytest.approx(0.2)
        # phys_y 0.05 -> 0.5 deg -> radians, written to the SimVar directly
        writes = instance.mock_simconnect.sim_data_written
        assert ("ELEVATOR TRIM POSITION", pytest.approx(0.5 * 0.01745), "radians") in writes
        assert not any(e[0] == "AXIS_ELEV_TRIM_SET"
                       for e in instance.mock_simconnect.sent_events)

    def test_axis_mode_sends_trim_axis_event(self):
        instance = self._trimwheel(use_axis=True)
        telem = self._telem()
        instance._telem_data = telem

        instance.msfs_update_trimwheel(telem)

        # phys_y 0.05 -> scaled to +-16384 and sign-inverted
        events = instance.mock_simconnect.sent_events
        assert ("AXIS_ELEV_TRIM_SET", -int(0.05 * 16384)) in events
        assert instance.mock_simconnect.sim_data_written == []

    def test_direct_mode_without_limits_falls_back_and_does_not_crash(self):
        # Aircraft that report no trim-limit telemetry: the spring follows
        # ElevTrimPct and no surface write is attempted (warned once).
        instance = self._trimwheel(use_axis=False, phys_y=0.3)
        telem = self._telem(ElevTrimPct=0.3, ElevTrim=2.0)
        instance._telem_data = telem

        instance.msfs_update_trimwheel(telem)

        assert instance.trimwheel_init == 1
        assert telem["trimwheel_pos_calc"] == pytest.approx(0.3)
        # nothing touched SimConnect at all (the lazy mock was never created)
        assert instance.mock_simconnect is None or \
            instance.mock_simconnect.sim_data_written == []
        assert instance._tw_limits_warned


class TestTrimwheelAxisControlGate(BaseTelemetryEffectTestCase):
    """The wheel must stop driving the sim when axis control is switched off.

    Either switch does it: the global "TelemFFB controls axes", or the
    per-device disable.  The gate used to read
    ``not telemffb_controls_axes and not local_disable_axis_control``, so a
    wheel disabled for its own device alone kept writing trim to the sim.
    """

    def _trimwheel(self, controls_axes, local_disable):
        instance = self.create_test_instance(
            MsfsXpTrimwheelMixIn,
            telemffb_controls_axes=controls_axes,
            local_disable_axis_control=local_disable,
            trimwheel_use_axis=False,
        )
        instance._test_sim_is_msfs = True
        self.mock_device.get_input().set_axis(x=0.0, y=0.05)
        return instance

    def _run(self, instance):
        telem = (
            TelemetryDataBuilder()
            .ffb_type("trimwheel")
            .set("APMaster", 0)
            .set("ElevTrimPct", 0.0)
            .set("ElevTrim", 2.0)
            .set("ElevTrimMax", 10.0)
            .set("ElevTrimMin", -10.0)
            .build()
        )
        instance._telem_data = telem
        instance.msfs_update_trimwheel(telem)
        # A gated-off wheel returns before ever touching simconnect, so the
        # mock is never even built — that counts as "no writes".
        simconnect = instance.mock_simconnect
        return [] if simconnect is None else simconnect.sim_data_written

    def test_local_disable_stops_trim_writes(self):
        instance = self._trimwheel(controls_axes=True, local_disable=True)

        assert self._run(instance) == []

    def test_global_disable_stops_trim_writes(self):
        instance = self._trimwheel(controls_axes=False, local_disable=False)

        assert self._run(instance) == []

    def test_both_disabled_stops_trim_writes(self):
        instance = self._trimwheel(controls_axes=False, local_disable=True)

        assert self._run(instance) == []

    def test_enabled_still_writes_trim(self):
        instance = self._trimwheel(controls_axes=True, local_disable=False)

        assert self._run(instance) != []


class TestTrimwheelCalibrationHold(BaseTelemetryEffectTestCase):
    """A running trim calibration broadcasts a hold (G.trimcal_hold_until):
    the wheel is parked where it sits (the calibrator restores the starting
    trim, so the parked position is the correct post-run position) and must
    not write back; on release it re-syncs before sending again."""

    def _trimwheel(self, phys_y=0.05):
        instance = self.create_test_instance(
            MsfsXpTrimwheelMixIn,
            telemffb_controls_axes=True,
            trimwheel_use_axis=False,
        )
        instance._test_sim_is_msfs = True
        self.mock_device.get_input().set_axis(x=0.0, y=phys_y)
        return instance

    def _telem(self, elev_trim=2.0, pct=0.0):
        return (TelemetryDataBuilder()
                .ffb_type("trimwheel")
                .set("APMaster", 0)
                .set("ElevTrimPct", pct)
                .set("ElevTrim", elev_trim)
                .set("ElevTrimMax", 10.0)
                .set("ElevTrimMin", -10.0)
                .build())

    def _writes(self, instance):
        sc = instance.mock_simconnect
        return [] if sc is None else sc.sim_data_written

    def test_hold_suppresses_writes_and_parks_the_wheel(self):
        instance = self._trimwheel(phys_y=0.05)
        telem = self._telem()
        instance._telem_data = telem
        G.trimcal_hold_until = time.perf_counter() + 5.0

        instance.msfs_update_trimwheel(telem)

        assert instance.trimwheel_init == 1
        assert self._writes(instance) == [], "no sim writes while held"
        # the wheel is parked where it sat at hold onset — NOT dragged along
        # the sweep (chasing the sweep ends displaced and wedges the latch)
        assert instance.spring_y.cpOffset == pytest.approx(0.05 * 4096, abs=2)

        # sim trim moves during the sweep: the park must not budge
        telem2 = self._telem(elev_trim=6.0)   # sim trim now 0.6
        instance._telem_data = telem2
        instance.msfs_update_trimwheel(telem2)
        assert instance.spring_y.cpOffset == pytest.approx(0.05 * 4096, abs=2)
        assert self._writes(instance) == []

    def test_hold_release_resyncs_wheel_before_sending(self):
        instance = self._trimwheel(phys_y=0.05)
        G.trimcal_hold_until = time.perf_counter() + 5.0
        telem = self._telem()
        instance._telem_data = telem
        instance.msfs_update_trimwheel(telem)
        assert self._writes(instance) == []

        # calibration ends; wheel (0.05) does not match the sim trim (0.2) yet
        G.trimcal_hold_until = 0.0
        telem = self._telem()
        instance._telem_data = telem
        instance.msfs_update_trimwheel(telem)
        assert instance.trim_active, "must latch until the wheel converges"
        assert self._writes(instance) == [], "no snap-back to the stale wheel position"

        # wheel converges on the sim trim: latch clears and writes resume
        self.mock_device.get_input().set_axis(y=0.2)
        telem = self._telem()
        instance._telem_data = telem
        instance.msfs_update_trimwheel(telem)
        assert not instance.trim_active
        assert len(self._writes(instance)) == 1

    def test_hold_release_near_restored_trim_resumes_immediately(self):
        # The normal case: the calibrator restored the starting trim, so the
        # parked wheel is already within the (widened) re-sync tolerance and
        # the latch must clear on the first frame — no stuck wheel.
        instance = self._trimwheel(phys_y=0.19)
        G.trimcal_hold_until = time.perf_counter() + 5.0
        # wheel was in sync before the run (pct near the wheel so init passes)
        telem = self._telem(pct=0.19)   # sim trim 0.2; wheel parked at 0.19
        instance._telem_data = telem
        instance.msfs_update_trimwheel(telem)
        assert self._writes(instance) == []

        G.trimcal_hold_until = 0.0
        telem = self._telem(pct=0.19)
        instance._telem_data = telem
        instance.msfs_update_trimwheel(telem)
        assert not instance.trim_active, \
            "wheel within post-hold tolerance of the restored trim must unlatch"
        assert len(self._writes(instance)) == 1
        assert instance._tw_resync_tol == pytest.approx(0.003), \
            "tolerance must return to the button-trim default after release"

    def test_expired_hold_does_not_suppress(self):
        instance = self._trimwheel()
        telem = self._telem()
        instance._telem_data = telem
        # a crashed master left a stale hold behind: TTL already passed
        G.trimcal_hold_until = time.perf_counter() - 0.1

        instance.msfs_update_trimwheel(telem)

        assert len(self._writes(instance)) == 1, "expired hold must not mute the wheel"


class TestTrimwheelLimitFallbacks(BaseTelemetryEffectTestCase):
    def _instance(self):
        return self.create_test_instance(MsfsXpTrimwheelMixIn)

    def test_prefers_max_min_over_up_dn_limits(self):
        telem = (TelemetryDataBuilder()
                 .set("ElevTrimMax", 20.0).set("ElevTrimMin", -12.0)
                 .set("ElevTrimUpLmt", 8.0).set("ElevTrimDnLmt", -8.0)
                 .build())
        assert self._instance()._trimwheel_trim_limits(telem) == (-12.0, 20.0)

    def test_up_dn_limits_used_when_max_min_missing(self):
        telem = (TelemetryDataBuilder()
                 .set("ElevTrimUpLmt", 8.0).set("ElevTrimDnLmt", -6.0)
                 .build())
        assert self._instance()._trimwheel_trim_limits(telem) == (-6.0, 8.0)

    def test_missing_lower_limit_assumes_symmetric_travel(self):
        telem = TelemetryDataBuilder().set("ElevTrimUpLmt", 12.0).build()
        assert self._instance()._trimwheel_trim_limits(telem) == (-12.0, 12.0)

    def test_no_usable_limits_returns_none(self):
        instance = self._instance()
        assert instance._trimwheel_trim_limits(TelemetryDataBuilder().build()) is None
        assert instance._tw_limits_warned
        # degenerate zero-width travel is also unusable
        telem = (TelemetryDataBuilder()
                 .set("ElevTrimMax", 5.0).set("ElevTrimMin", 5.0).build())
        assert instance._trimwheel_trim_limits(telem) is None

    def test_sim_pos_prefers_scaled_elevtrim_else_pct(self):
        instance = self._instance()
        telem = (TelemetryDataBuilder()
                 .set("ElevTrim", 5.0).set("ElevTrimPct", 0.9).build())
        assert instance._trimwheel_sim_pos(telem, (-10.0, 10.0)) == pytest.approx(0.5)
        assert instance._trimwheel_sim_pos(telem, None) == pytest.approx(0.9)
        no_trim = TelemetryDataBuilder().set("ElevTrimPct", 0.9).build()
        assert instance._trimwheel_sim_pos(no_trim, (-10.0, 10.0)) == pytest.approx(0.9)
