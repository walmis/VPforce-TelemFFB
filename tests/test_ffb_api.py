"""Tests for the standardized MSFS helicopter FFB LVAR API (FFBApiMixIn).

Spec: https://github.com/CK-AT/DIY-FFB/blob/ck_dev/Standards/MSFS_Helicopter_FFB_API.md
Plan: docs/ffb_simvar_api_implementation.md

No aircraft implements this API yet, so everything here drives the rig side with
synthetic telemetry.  Coverage follows plan section 8:

- discovery gating, including "write no L:FFB_* variable at all when unsupported"
- the FFB_FEATURES capability matrix, and that it is latched rather than re-read
- trim spring, TR_ON unclutch and fly-through per control
- hydraulic assist loss, including the loss/health inversion
- vendor-class precedence at the three dispatch sites
- axis ownership under both telemffb_controls_axes modes
"""
import pytest
from unittest.mock import patch

from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.FFBApiMixIn import (
    FFB_API_CYCLIC,
    FFB_API_COLLECTIVE,
    FFB_API_PEDALS,
)
from telemffb.sim.msfs_xp.Helicopter import Helicopter
from telemffb.sim.msfs_xp.HPGHelicopter import HPGHelicopter
from telemffb.sim.msfs_xp.MsfsXpHeliControlsMixIn import MsfsXpHeliControlsMixIn
from telemffb.sim.msfs_xp.SASHelicopter import SASHelicopter
from telemffb.sim.msfs_xp.XAW109Helicopter import XAW109Helicopter
from telemffb.utils import clamp


# Feature bits, spelled out so the matrix tests read as the spec does.
BIT_CYCLIC = 0b001
BIT_COLLECTIVE = 0b010
BIT_PEDALS = 0b100
ALL_TRIM = BIT_CYCLIC | BIT_COLLECTIVE | BIT_PEDALS


class FFBApiTestBase(BaseTelemetryEffectTestCase):
    """Shared helpers for driving the API with synthetic telemetry."""

    _SENTINEL = object()

    def setup_method(self):
        super().setup_method()
        # globals.py only *annotates* device_firmware_version, so it exists at runtime
        # only once some module assigns it.  Pin it here rather than depend on another
        # test module having run first, and restore it exactly so this file cannot
        # change behaviour elsewhere in the suite.
        import telemffb.globals as G
        self._saved_fw = getattr(G, "device_firmware_version", self._SENTINEL)
        G.device_firmware_version = None

    def teardown_method(self):
        import telemffb.globals as G
        if self._saved_fw is self._SENTINEL:
            if hasattr(G, "device_firmware_version"):
                del G.device_firmware_version
        else:
            G.device_firmware_version = self._saved_fw
        super().teardown_method()

    def make_telem(self, device="joystick", version=1, features=ALL_TRIM, **fields):
        builder = (
            TelemetryDataBuilder()
            .with_sim_on_ground(0)
            .with_airspeed(10.0)
            .with_field("AircraftClass", "Helicopter")
            .with_field("FFBType", device)
            .with_field("N", 100.0)
        )
        telem = builder.build()
        if version is not None:
            telem["ffbApiVersion"] = version
        if features is not None:
            telem["ffbFeatures"] = features
        for key, value in fields.items():
            telem[key] = value
        return telem

    def make_instance(self, cls=Helicopter, device="joystick", **kwargs):
        instance = self.create_aircraft_instance(
            cls, name="TestHeli", _test_sim_is_msfs=True, _test_device_type=device, **kwargs
        )
        # Default to "user binds axes in MSFS" so the spring/trim assertions are not
        # entangled with the axis-send path.  The axis tests override this.
        instance.telemffb_controls_axes = False
        instance.local_disable_axis_control = False
        return instance

    def arm(self, instance, telem):
        """Set telemetry and run one discovery/lifecycle pass."""
        self.set_telemetry(instance, telem)
        instance.ffb_api_on_telemetry(telem)

    def ffb_writes(self):
        """Every L:FFB_* variable written so far, as (name, value) pairs."""
        return [
            (name, value)
            for name, value, _units in self.mock_simconnect.sim_data_written
            if name.startswith("L:FFB_")
        ]

    def written_values(self, lvar):
        return [value for name, value in self.ffb_writes() if name == lvar]


# ───────────────────────────────────────────────────────────────
# Discovery and mode gating (spec 4 steps 1-2)
# ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestFFBApiDiscovery(FFBApiTestBase):

    def test_subscribes_discovery_and_runtime_vars(self):
        instance = self.make_instance()
        instance.subscribe_ffb_api_simvars()

        for name in ("ffbApiVersion", "ffbFeatures", "ffbTrimCyclicPitch", "ffbTrOnCyclic"):
            assert name in self.mock_simconnect.sv_dict, f"{name} not subscribed"

    def test_unsubscribed_aircraft_never_latches(self):
        """No value yet (None) is not the same as 0: stay unlatched and stay quiet."""
        instance = self.make_instance()
        telem = self.make_telem(version=None, features=None)
        self.arm(instance, telem)

        assert instance._ffb_api_latched is False
        assert instance._ffb_api_active(FFB_API_CYCLIC) is False
        assert self.ffb_writes() == []

    def test_version_zero_writes_no_ffb_variable_at_all(self):
        """Spec 4 step 1: not just no ENABLED - no L:FFB_* write of any kind."""
        instance = self.make_instance()
        telem = self.make_telem(version=0, features=0)
        self.arm(instance, telem)

        # Deliberately still unlatched: see test_version_zero_does_not_latch_early.
        assert instance._ffb_api_latched is False
        assert instance._ffb_api_active(FFB_API_CYCLIC) is False
        assert self.ffb_writes() == []

    def test_version_zero_does_not_latch_early(self):
        """A 0 read is "not created yet" as often as it is "not implemented".

        MSFS reads an LVAR the aircraft has not defined as 0, and the subscription
        normally lands a few frames before the aircraft's own init publishes
        FFB_API_VERSION.  Latching that first 0 would strand a supported aircraft.
        """
        instance = self.make_instance()
        self.arm(instance, self.make_telem(version=0, features=0))
        self.arm(instance, self.make_telem(version=1, features=ALL_TRIM))

        assert instance._ffb_api_latched is True
        assert instance._ffb_api_version == 1
        assert instance._ffb_api_features == ALL_TRIM

    def test_telemetry_path_subscribes_without_an_explicit_call(self):
        """Regression: nothing else subscribes these vars in the running app.

        Helicopter.__init__ gates its subscribe on _sim_is_msfs(), which reads the
        telemetry that is only attached *after* the handler is constructed - so the
        constructor-time call never ran and ffbApiVersion never arrived.  Discovery
        has to (re)subscribe from the per-frame path.
        """
        instance = self.make_instance()
        assert "ffbApiVersion" not in self.mock_simconnect.sv_dict

        instance.ffb_api_on_telemetry(self.make_telem())

        for name in ("ffbApiVersion", "ffbFeatures", "ffbTrimCyclicPitch", "ffbTrOnCyclic"):
            assert name in self.mock_simconnect.sv_dict, f"{name} not subscribed"

    def test_dropped_subscription_is_reinstated(self):
        """A SimConnectManager resubscribe from anywhere else drops runtime-added vars.

        temp_sim_vars is cleared by the subscribe cycle that consumes it, so the next
        _resubscribe() rebuilds from the predefined list alone.  The per-frame check
        is what brings them back.
        """
        instance = self.make_instance()
        self.arm(instance, self.make_telem())
        self.mock_simconnect.sv_dict.clear()

        self.arm(instance, self.make_telem())

        assert "ffbApiVersion" in self.mock_simconnect.sv_dict

    def test_version_one_enables_and_writes_enabled_flag(self):
        instance = self.make_instance()
        telem = self.make_telem(version=1)
        self.arm(instance, telem)

        assert instance._ffb_api_active(FFB_API_CYCLIC) is True
        assert self.written_values("L:FFB_CYCLIC_ENABLED") == [1]

    def test_future_version_is_treated_as_supported(self):
        """Forward-compatible: a newer revision still supports everything we know."""
        instance = self.make_instance()
        self.arm(instance, self.make_telem(version=7))

        assert instance._ffb_api_active(FFB_API_CYCLIC) is True

    def test_master_toggle_disables_everything(self):
        instance = self.make_instance()
        instance.ffb_api_enable = False
        self.arm(instance, self.make_telem(version=1))

        assert instance._ffb_api_active(FFB_API_CYCLIC) is False
        assert self.ffb_writes() == []

    def test_features_are_latched_not_re_read(self):
        """Discovery values are static (spec 3.1): a later change must not take effect."""
        instance = self.make_instance()
        self.arm(instance, self.make_telem(version=1, features=BIT_CYCLIC))
        assert instance._ffb_api_has_trim(FFB_API_CYCLIC) is True

        # Aircraft glitches the value mid-flight; the latched capability stands.
        self.arm(instance, self.make_telem(version=1, features=0))
        assert instance._ffb_api_has_trim(FFB_API_CYCLIC) is True
        assert instance._ffb_api_features == BIT_CYCLIC

    def test_features_relatched_after_timeout(self):
        instance = self.make_instance()
        self.arm(instance, self.make_telem(version=1, features=BIT_CYCLIC))
        instance.ffb_api_on_timeout()

        assert instance._ffb_api_latched is False
        self.arm(instance, self.make_telem(version=1, features=ALL_TRIM))
        assert instance._ffb_api_features == ALL_TRIM

    def test_missing_features_warns_once(self):
        """Legal per spec, but nearly always a forgotten FFB_FEATURES."""
        instance = self.make_instance()
        with patch("telemffb.sim.msfs_xp.FFBApiMixIn.logging") as mock_log:
            self.arm(instance, self.make_telem(version=1, features=0))
            self.arm(instance, self.make_telem(version=1, features=0))

        assert mock_log.warning.call_count == 1


# ───────────────────────────────────────────────────────────────
# Capability matrix (spec 3.1)
# ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestFFBApiFeatureMatrix(FFBApiTestBase):

    @pytest.mark.parametrize("features,cyclic,collective,pedals", [
        (0b000, False, False, False),
        (0b001, True, False, False),
        (0b010, False, True, False),
        (0b100, False, False, True),
        (0b111, True, True, True),
        # A reserved high bit must be masked off, never treated as an error and never
        # as "no features".
        (0b1000_0001, True, False, False),
    ])
    def test_trim_bits_decode(self, features, cyclic, collective, pedals):
        instance = self.make_instance()
        self.arm(instance, self.make_telem(version=1, features=features))

        assert instance._ffb_api_has_trim(FFB_API_CYCLIC) is cyclic
        assert instance._ffb_api_has_trim(FFB_API_COLLECTIVE) is collective
        assert instance._ffb_api_has_trim(FFB_API_PEDALS) is pedals

    def test_float_features_are_rounded(self):
        """LVARs arrive as doubles; decode is round-then-mask."""
        instance = self.make_instance()
        self.arm(instance, self.make_telem(version=1.0, features=3.0000001))

        assert instance._ffb_api_features == 3

    def test_no_trim_bit_means_no_trim_spring(self):
        """The published _TRIM is ignored entirely when the control has no trim."""
        instance = self.make_instance()
        telem = self.make_telem(
            version=1, features=0, ffbTrimCyclicPitch=0.5, ffbTrimCyclicRoll=0.5
        )
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        assert instance.cpO_x == 0
        assert instance.cpO_y == 0

    def test_no_trim_bit_ignores_tr_on(self):
        """Nothing to unclutch on a control the aircraft does not trim."""
        instance = self.make_instance()
        telem = self.make_telem(version=1, features=0, ffbTrOnCyclic=1)
        self.arm(instance, telem)

        assert instance._ffb_api_tr_on(telem, FFB_API_CYCLIC) is False

    def test_enabled_written_even_without_trim(self):
        """Enable is keyed on rig hardware, not on whether the aircraft trims."""
        instance = self.make_instance()
        self.arm(instance, self.make_telem(version=1, features=0))

        assert self.written_values("L:FFB_CYCLIC_ENABLED") == [1]


# ───────────────────────────────────────────────────────────────
# Trim spring and trim release
# ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestFFBApiTrim(FFBApiTestBase):

    def test_cyclic_spring_centre_tracks_published_trim(self):
        instance = self.make_instance()
        telem = self.make_telem(ffbTrimCyclicRoll=0.25, ffbTrimCyclicPitch=-0.5)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        assert instance.cpO_x == round(0.25 * 4096)
        assert instance.cpO_y == round(-0.5 * 4096)

    def test_trim_is_consumed_raw(self):
        """Plan 10.4: no rig-side smoothing - a step lands on the spring immediately."""
        instance = self.make_instance()
        telem = self.make_telem(ffbTrimCyclicRoll=0.0)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)
        assert instance.cpO_x == 0

        telem["ffbTrimCyclicRoll"] = 0.8
        instance._ffb_api_update_cyclic(telem)
        assert instance.cpO_x == round(0.8 * 4096)

    def test_trim_is_clamped_to_unit_range(self):
        instance = self.make_instance()
        telem = self.make_telem(ffbTrimCyclicRoll=3.0, ffbTrimCyclicPitch=-9.0)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        assert instance.cpO_x == 4096
        assert instance.cpO_y == -4096

    def test_tr_on_softens_spring_and_follows_stick(self):
        instance = self.make_instance()
        instance.ffb_api_tr_spring_gain = 0.0
        instance.cyclic_spring_gain = 0.8
        self.mock_device._input_data.set_axis(x=0.3, y=-0.2)

        telem = self.make_telem(ffbTrimCyclicRoll=0.9, ffbTrimCyclicPitch=0.9, ffbTrOnCyclic=1)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        # Centre follows the stick, not the (stale) published trim.
        assert instance.cpO_x == round(0.3 * 4096)
        assert instance.cpO_y == round(-0.2 * 4096)

    def test_tr_release_recentres_on_published_trim(self):
        """The spec guarantees _TRIM followed the stick under TR, so there is no snap."""
        instance = self.make_instance()
        self.mock_device._input_data.set_axis(x=0.3, y=0.0)

        telem = self.make_telem(ffbTrimCyclicRoll=0.3, ffbTrOnCyclic=1)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)
        centre_under_tr = instance.cpO_x

        telem["ffbTrOnCyclic"] = 0
        instance._ffb_api_update_cyclic(telem)

        assert instance.cpO_x == centre_under_tr

    def test_collective_trim_sign_is_inverted_by_default(self):
        """Spec positive = collective up; TelemFFB device y is +1 at full down."""
        instance = self.make_instance(device="collective")
        telem = self.make_telem(device="collective", ffbTrimCollective=0.5)
        self.arm(instance, telem)
        instance._ffb_api_update_collective(telem)

        assert instance.cpO_y == round(-0.5 * 4096)

    def test_collective_inversion_is_configurable(self):
        instance = self.make_instance(device="collective")
        instance.ffb_api_invert_collective = False
        telem = self.make_telem(device="collective", ffbTrimCollective=0.5)
        self.arm(instance, telem)
        instance._ffb_api_update_collective(telem)

        assert instance.cpO_y == round(0.5 * 4096)

    def test_pedal_trim_tracks_published_trim(self):
        instance = self.make_instance(device="pedals")
        telem = self.make_telem(device="pedals", ffbTrimPedals=-0.4)
        self.arm(instance, telem)
        instance._ffb_api_update_pedals(telem)

        assert instance.cpO_x == round(-0.4 * 4096)

    def test_untrimmed_collective_holds_position_from_local_settings(self):
        """No trim bit is not "no spring": fall back to the generic position hold."""
        instance = self.make_instance(device="collective")
        instance.collective_spring_coeff_y = 2048
        self.mock_device._input_data.set_axis(y=0.4)

        telem = self.make_telem(device="collective", features=0, ffbTrimCollective=0.9)
        self.arm(instance, telem)
        instance._ffb_api_update_collective(telem)

        # Centre is the lever, not the (ignored) published trim.
        assert instance.cpO_y == round(0.4 * 4096)
        assert instance.spring_y.positiveCoefficient == 1024

    def test_normalized_gains_are_applied_as_scaled_floats(self):
        """set_coefficient scales floats by 4096 but takes ints raw - keep gains float."""
        instance = self.make_instance(device="pedals")
        instance.pedal_spring_gain = 1  # an int, as a config round-trip can produce
        self.mock_device._input_data.set_axis(x=0.0)

        telem = self.make_telem(device="pedals", ffbTrimPedals=0.0)
        self.arm(instance, telem)
        instance._ffb_api_update_pedals(telem)

        # Full-scale spring, not a raw coefficient of 1.
        assert instance.spring_x.positiveCoefficient == 4096

    def test_spring_gated_until_control_reaches_trim_reference(self):
        """Mirrors the existing init handshake: do not yank a displaced control."""
        instance = self.make_instance(device="pedals")
        instance.pedal_spring_gain = 0.7
        self.mock_device._input_data.set_axis(x=-0.9)

        telem = self.make_telem(device="pedals", ffbTrimPedals=0.9)
        self.arm(instance, telem)
        instance._ffb_api_update_pedals(telem)

        assert instance._ffb_api_spring_init == 0
        assert instance.spring_x.positiveCoefficient == 0


# ───────────────────────────────────────────────────────────────
# Hydraulics (spec 3.4)
# ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestFFBApiHydraulics(FFBApiTestBase):

    @pytest.mark.parametrize("loss,expected_health", [
        (0.0, 1.0),   # full boost
        (0.25, 0.75),
        (1.0, 0.0),   # unassisted / locked
    ])
    def test_loss_is_inverted_into_hydraulic_health(self, loss, expected_health):
        """_HYD_ASSIST_LOSS is loss; HydSys / hydraulic_factor is health."""
        instance = self.make_instance()
        telem = self.make_telem(
            ffbHydLossCyclicPitch=loss, ffbHydLossCyclicRoll=loss
        )
        self.arm(instance, telem)

        assert telem.HydSys == pytest.approx(expected_health)
        assert telem._ffb_hyd_loss == pytest.approx(loss)

    def test_worst_axis_wins_for_a_two_axis_control(self):
        instance = self.make_instance()
        telem = self.make_telem(ffbHydLossCyclicPitch=0.2, ffbHydLossCyclicRoll=0.7)
        self.arm(instance, telem)

        assert telem.HydSys == pytest.approx(0.3)

    def test_absent_loss_reads_as_full_boost(self):
        """Correct default for an aircraft with no hydraulics."""
        instance = self.make_instance()
        telem = self.make_telem()
        self.arm(instance, telem)

        assert telem.HydSys == pytest.approx(1.0)

    def test_hydraulics_not_gated_by_feature_bit(self):
        instance = self.make_instance()
        telem = self.make_telem(features=0, ffbHydLossCyclicPitch=0.6)
        self.arm(instance, telem)

        assert telem.HydSys == pytest.approx(0.4)

    def test_no_injection_when_unsupported(self):
        instance = self.make_instance()
        telem = self.make_telem(version=0, ffbHydLossCyclicPitch=0.6)
        self.arm(instance, telem)

        assert telem.get("HydSys", "n/a") == "n/a"


# ───────────────────────────────────────────────────────────────
# Fly-through (spec 3.3)
# ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestFFBApiFlyThrough(FFBApiTestBase):

    def test_per_axis_detection_is_independent(self):
        instance = self.make_instance()
        instance.ffb_api_fly_through_deadzone = 0.1
        # Roll deviates well past the deadzone, pitch sits on its reference.
        self.mock_device._input_data.set_axis(x=0.8, y=0.0)

        telem = self.make_telem(ffbTrimCyclicRoll=0.0, ffbTrimCyclicPitch=0.0)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        assert self.written_values("L:FFB_CYCLIC_ROLL_FLY_THROUGH") == [1]
        assert self.written_values("L:FFB_CYCLIC_PITCH_FLY_THROUGH") == [0]

    def test_combined_mode_mirrors_onto_both_axes(self):
        instance = self.make_instance()
        instance.ffb_api_individual_fly_through = False
        self.mock_device._input_data.set_axis(x=0.8, y=0.0)

        telem = self.make_telem(ffbTrimCyclicRoll=0.0, ffbTrimCyclicPitch=0.0)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        assert self.written_values("L:FFB_CYCLIC_ROLL_FLY_THROUGH") == [1]
        assert self.written_values("L:FFB_CYCLIC_PITCH_FLY_THROUGH") == [1]

    def test_hysteresis_holds_detection_between_the_two_deadzones(self):
        instance = self.make_instance(device="pedals")
        instance.ffb_api_fly_through_deadzone = 0.2
        instance.ffb_api_fly_through_release_deadzone = 0.05
        telem = self.make_telem(device="pedals", ffbTrimPedals=0.0)
        self.arm(instance, telem)

        self.mock_device._input_data.set_axis(x=0.3)
        instance._ffb_api_update_pedals(telem)
        assert self.written_values("L:FFB_PEDALS_FLY_THROUGH") == [1]

        # Between the release and trigger deadzones: stays latched.
        self.mock_device._input_data.set_axis(x=0.1)
        instance._ffb_api_update_pedals(telem)
        assert self.written_values("L:FFB_PEDALS_FLY_THROUGH") == [1, 1]

        # Below the release deadzone: clears.
        self.mock_device._input_data.set_axis(x=0.01)
        instance._ffb_api_update_pedals(telem)
        assert self.written_values("L:FFB_PEDALS_FLY_THROUGH") == [1, 1, 0]

    def test_deviation_is_measured_from_the_trim_reference(self):
        """A stick parked on a deflected trim position is not flying through."""
        instance = self.make_instance(device="pedals")
        instance.ffb_api_fly_through_deadzone = 0.1
        self.mock_device._input_data.set_axis(x=0.6)

        telem = self.make_telem(device="pedals", ffbTrimPedals=0.6)
        self.arm(instance, telem)
        instance._ffb_api_update_pedals(telem)

        assert self.written_values("L:FFB_PEDALS_FLY_THROUGH") == [0]


# ───────────────────────────────────────────────────────────────
# ENABLED lifecycle (spec 4 steps 3 and 5)
# ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestFFBApiEnabledLifecycle(FFBApiTestBase):

    def test_enabled_is_not_rewritten_every_frame(self):
        """On change plus a heartbeat: the send queue is shared with the axis stream."""
        instance = self.make_instance()
        telem = self.make_telem()
        for _ in range(5):
            self.arm(instance, telem)

        assert self.written_values("L:FFB_CYCLIC_ENABLED") == [1]

    def test_timeout_releases_the_control(self):
        instance = self.make_instance()
        self.arm(instance, self.make_telem())
        instance.ffb_api_on_timeout()

        assert self.written_values("L:FFB_CYCLIC_ENABLED") == [1, 0]

    def test_timeout_without_enable_writes_nothing(self):
        instance = self.make_instance()
        self.arm(instance, self.make_telem(version=0))
        instance.ffb_api_on_timeout()

        assert self.ffb_writes() == []

    def test_helicopter_on_timeout_releases_the_control(self):
        """The release must be wired into the real teardown path, not just callable."""
        instance = self.make_instance()
        self.arm(instance, self.make_telem())
        instance.on_timeout()

        assert self.written_values("L:FFB_CYCLIC_ENABLED") == [1, 0]

    @pytest.mark.parametrize("device,control", [
        ("joystick", "CYCLIC"),
        ("collective", "COLLECTIVE"),
        ("pedals", "PEDALS"),
    ])
    def test_each_instance_writes_only_its_own_control(self, device, control):
        """One process per device: no cross-instance coordination, no stray writes."""
        instance = self.make_instance(device=device)
        self.arm(instance, self.make_telem(device=device))

        enabled = [name for name, _ in self.ffb_writes() if name.endswith("_ENABLED")]
        assert enabled == [f"L:FFB_{control}_ENABLED"]


# ───────────────────────────────────────────────────────────────
# Vendor precedence at the dispatch sites (plan 4.3)
# ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestFFBApiVendorPrecedence(FFBApiTestBase):

    def _dispatch_cyclic(self, instance, telem):
        """Run only MsfsXpHeliControlsMixIn.on_telemetry's dispatch."""
        mro = type(instance).__mro__
        idx = mro.index(MsfsXpHeliControlsMixIn)
        nxt = next(c for c in mro[idx + 1:] if "on_telemetry" in c.__dict__)
        with patch.object(nxt, "on_telemetry", lambda self, td: None):
            MsfsXpHeliControlsMixIn.on_telemetry(instance, telem)

    def _dispatch_collective_and_pedals(self, instance, telem):
        """Run only Helicopter.on_telemetry's two dispatch sites."""
        from telemffb.sim.msfs_xp.Aircraft import Aircraft
        with patch.object(Aircraft, "on_telemetry", lambda self, td: None):
            Helicopter.on_telemetry(instance, telem)

    @pytest.mark.parametrize("cls", [HPGHelicopter, SASHelicopter, XAW109Helicopter])
    def test_vendor_cyclic_path_is_replaced_when_api_active(self, cls):
        """These three drive cyclic trim from their own AFCS/SEMA state inline."""
        instance = self.make_instance(cls)
        telem = self.make_telem(ffbTrimCyclicRoll=0.25)
        self.arm(instance, telem)

        called = []
        instance.msfs_update_heli_controls = lambda td: called.append("vendor")
        self._dispatch_cyclic(instance, telem)

        assert called == []
        assert instance.cpO_x == round(0.25 * 4096)

    @pytest.mark.parametrize("cls", [HPGHelicopter, SASHelicopter, XAW109Helicopter])
    def test_vendor_cyclic_path_still_runs_when_api_absent(self, cls):
        instance = self.make_instance(cls)
        telem = self.make_telem(version=0)
        self.arm(instance, telem)

        called = []
        instance.msfs_update_heli_controls = lambda td: called.append("vendor")
        self._dispatch_cyclic(instance, telem)

        assert called == ["vendor"]

    def test_mixed_enablement_runs_api_cyclic_and_vendor_collective(self):
        """A user with an FFB cyclic but a normal collective needs no special case."""
        instance = self.make_instance(HPGHelicopter, device="joystick")
        telem = self.make_telem(device="joystick")
        self.arm(instance, telem)

        collective_calls = []
        instance.msfs_update_collective = lambda td: collective_calls.append("vendor")
        instance.msfs_update_pedals = lambda td: None
        self._dispatch_collective_and_pedals(instance, telem)

        # This instance owns CYCLIC, so COLLECTIVE is not ours to take over.
        assert instance._ffb_api_active(FFB_API_COLLECTIVE) is False
        assert collective_calls == ["vendor"]

    def test_collective_dispatch_replaces_generic_path(self):
        instance = self.make_instance(device="collective")
        telem = self.make_telem(device="collective", ffbTrimCollective=0.25)
        self.arm(instance, telem)

        called = []
        instance.msfs_update_collective = lambda td: called.append("generic")
        instance.msfs_update_pedals = lambda td: None
        self._dispatch_collective_and_pedals(instance, telem)

        assert called == []
        assert instance.cpO_y == round(-0.25 * 4096)

    def test_pedal_dispatch_replaces_generic_path(self):
        instance = self.make_instance(device="pedals")
        telem = self.make_telem(device="pedals", ffbTrimPedals=0.25)
        self.arm(instance, telem)

        called = []
        instance.msfs_update_pedals = lambda td: called.append("generic")
        instance.msfs_update_collective = lambda td: None
        self._dispatch_collective_and_pedals(instance, telem)

        assert called == []
        assert instance.cpO_x == round(0.25 * 4096)


# ───────────────────────────────────────────────────────────────
# Axis ownership (plan 10.2)
# ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestFFBApiAxisOwnership(FFBApiTestBase):

    def test_no_axis_sent_when_user_binds_axes_in_msfs(self):
        instance = self.make_instance()
        instance.telemffb_controls_axes = False
        telem = self.make_telem(ffbTrimCyclicRoll=0.5)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        assert self.mock_simconnect.sent_events == []

    def test_generic_trim_follow_is_suppressed_under_the_api(self):
        """The aircraft owns trim, so CyclicTrimX/Y must not reach the sent axis."""
        instance = self.make_instance()
        instance.trim_following = True
        telem = self.make_telem(CyclicTrimX=50.0, CyclicTrimY=50.0)
        self.arm(instance, telem)

        instance._update_cyclic_trim(telem)

        assert instance.cyclic_physical_trim_x_offs == 0
        assert instance.cyclic_physical_trim_y_offs == 0
        assert instance.cyclic_virtual_trim_x_offs == 0
        assert instance.cyclic_virtual_trim_y_offs == 0

    def test_sent_axis_is_raw_with_no_trim_contribution(self):
        """Trim lives in the spring centre; applying it to the axis too would double it."""
        instance = self.make_instance()
        instance.telemffb_controls_axes = True
        instance.trim_following = True
        instance.use_firmware_axis_override = False
        instance.cyclic_spring_init = 1
        self.mock_device._input_data.set_axis(x=0.5, y=0.0)

        telem = self.make_telem(
            ffbTrimCyclicRoll=0.9, ffbTrimCyclicPitch=0.9,
            CyclicTrimX=100.0, CyclicTrimY=100.0,
        )
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        lateral = [
            value for name, value in self.mock_simconnect.sent_events
            if name == "AXIS_CYCLIC_LATERAL_SET"
        ]
        assert lateral, "no cyclic axis was sent"
        # The sent value must be the raw physical 0.5 put through the normal scaling,
        # carrying neither the published _TRIM nor CyclicTrimX.  Compared against the
        # real conversion rather than a literal, so the sim's axis sign convention is
        # not baked into the assertion.
        x_var, x_range = instance._get_msfs_axis_config("x", "AXIS_CYCLIC_LATERAL_SET")
        x_scale = clamp(instance.joystick_x_axis_scale, 0, 1)
        expected = instance._scale_msfs_axis_value(0.5, x_range, x_scale)
        assert lateral[-1] == pytest.approx(expected)

    def test_trim_still_reaches_the_spring_when_axes_are_sent(self):
        instance = self.make_instance()
        instance.telemffb_controls_axes = True
        instance.use_firmware_axis_override = False
        instance.cyclic_spring_init = 1
        telem = self.make_telem(ffbTrimCyclicRoll=0.3)
        self.arm(instance, telem)
        instance._ffb_api_update_cyclic(telem)

        assert instance.cpO_x == round(0.3 * 4096)
