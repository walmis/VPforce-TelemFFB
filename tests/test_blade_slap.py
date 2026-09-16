"""Blade-slap effect: native X-Plane signal, the wake-geometry heuristic for
MSFS/DCS, gating, and the sharp-waveform rendering at blade-passage rate."""
import pytest

from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.Helicopter import Helicopter
from telemffb.hw.ffb_rhino import EFFECT_SAWTOOTHDOWN
import telemffb.globals as G

# Standalone runs need this present (framework supports_axis_override reads it
# unconditionally; full-suite runs set it via earlier tests).
G.device_firmware_version = getattr(G, "device_firmware_version", None)

# heuristic band peaks at blade_slap_band_center (63 kt default); descent
# band peaks at the midpoint of (0, 12) deg
PEAK_IAS = 32.4
PEAK_DESCENT_VS = -PEAK_IAS * 0.10510  # tan(6 deg) -> 6 deg descent angle


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestBladeSlap(BaseTelemetryEffectTestCase):

    def _make(self, xplane=False, device="joystick", **telem_fields):
        if xplane:
            instance = self.create_aircraft_instance(
                Helicopter, name="TestHeli", _test_sim_is_xplane=True,
                _test_device_type=device)
            instance._test_sim_is_xplane = True
        else:
            instance = self.create_aircraft_instance(
                Helicopter, name="TestHeli", _test_sim_is_msfs=True,
                _test_device_type=device)
            instance._test_sim_is_msfs = True
        instance.blade_slap_enable = True
        builder = (TelemetryDataBuilder()
                   .on_ground(False)
                   .with_field("AircraftClass", "Helicopter")
                   .with_field("FFBType", "joystick")
                   .with_field("RotorRPM", 300.0)
                   .with_field("WeightOnWheels", [0, 0, 0])
                   .with_field("IAS", PEAK_IAS)
                   .with_field("VerticalSpeed", 0.0)
                   .with_field("G", 1.0))
        for k, v in telem_fields.items():
            builder = builder.with_field(k, v)
        telem = builder.build()
        self.set_telemetry(instance, telem)
        return instance, telem

    def test_native_signal_drives_effect_on_xplane(self):
        # level flight (heuristic would be ~0) but the sim says slap = 0.6.
        # XP rotor speed comes from PropRPM.
        inst, telem = self._make(xplane=True, BladeSlap=0.6, PropRPM=[300.0])
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert telem["_blade_slap_src"] == "native"
        eff = self.mock_effects["blade_slap_y"]
        assert eff.started
        freq, mag, direction, kwargs = eff._periodic
        assert freq == pytest.approx(10.0)          # 300 RPM x 2 blades / 60
        assert mag == pytest.approx(0.15 * 0.6, abs=1e-6)
        assert kwargs["effect_type"] == EFFECT_SAWTOOTHDOWN
        assert self.mock_effects["blade_slap_x"]._periodic[2] == 90

    def test_native_mode_never_falls_back_to_heuristic(self):
        # THE support scenario the toggle exists for: native says zero during
        # a heuristic-perfect descent profile -> the effect must stay silent,
        # not sneak in via the inferred path.
        inst, telem = self._make(xplane=True, BladeSlap=0.0, PropRPM=[300.0],
                                 VerticalSpeed=PEAK_DESCENT_VS)
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert telem["_blade_slap_src"] == "native"
        assert not self.mock_effects["blade_slap_y"].started

    def test_native_toggle_off_uses_heuristic_on_xplane(self):
        inst, telem = self._make(xplane=True, BladeSlap=0.0, PropRPM=[300.0],
                                 VerticalSpeed=PEAK_DESCENT_VS)
        inst.blade_slap_use_native = False
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert telem["_blade_slap_src"] == "inferred"
        assert self.mock_effects["blade_slap_y"].started

    def test_msfs_always_inferred_even_with_native_field(self):
        # a stray BladeSlap key on a non-XP sim must not select the native path
        inst, telem = self._make(BladeSlap=0.6)
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert telem["_blade_slap_src"] == "inferred"
        assert not self.mock_effects["blade_slap_y"].started  # level, 1G

    def test_heuristic_peaks_in_descent_band(self):
        # ~63 kt on a 6-degree descent gradient: the wop-wop envelope
        inst, telem = self._make(VerticalSpeed=PEAK_DESCENT_VS)
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert telem["_blade_slap_src"] == "inferred"
        assert telem["_blade_slap_sig"] == pytest.approx(1.0, abs=0.01)
        assert self.mock_effects["blade_slap_y"]._periodic[1] == pytest.approx(0.15, abs=0.01)

    def test_silent_in_level_cruise(self):
        inst, telem = self._make()  # level, 1G
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert not self.mock_effects["blade_slap_y"].started

    def test_silent_in_climb(self):
        inst, telem = self._make(VerticalSpeed=+3.4)
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert not self.mock_effects["blade_slap_y"].started

    def test_silent_during_shallow_accel_sink(self):
        # accelerating nose-down at band speed with a transient ~100 fpm sink:
        # the angle gate alone would pass (1.9 deg at 15 m/s) but the sink-rate
        # floor keeps it silent — this is not wake re-entry (field report)
        inst, telem = self._make(IAS=15.0, VerticalSpeed=-0.5)
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert not self.mock_effects["blade_slap_y"].started

    def test_real_approach_sink_still_triggers(self):
        # 65 kt at ~400 fpm: comfortably past the sink floor
        inst, telem = self._make(VerticalSpeed=-2.0)
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert self.mock_effects["blade_slap_y"].started

    def test_loaded_flare_or_turn_triggers(self):
        inst, telem = self._make(G=1.65)  # level but loaded
        inst.ac_update_blade_slap(telem, blade_ct=2)
        eff = self.mock_effects["blade_slap_y"]
        assert eff.started
        # g_term = (1.65-1.15)*1.5 = 0.75 -> sig = g_factor*0.75 at peak speed band
        assert telem["_blade_slap_sig"] == pytest.approx(
            inst.blade_slap_g_factor * 0.75, abs=0.01)

    def test_g_factor_zero_disables_maneuver_slap_only(self):
        # loaded turn with the factor at zero: silent
        inst, telem = self._make(G=1.65)
        inst.blade_slap_g_factor = 0.0
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert not self.mock_effects["blade_slap_y"].started
        # descent wop-wop unaffected by the factor
        inst2, telem2 = self._make(VerticalSpeed=PEAK_DESCENT_VS)
        inst2.blade_slap_g_factor = 0.0
        inst2.ac_update_blade_slap(telem2, blade_ct=2)
        assert telem2["_blade_slap_sig"] == pytest.approx(1.0, abs=0.01)

    def test_suppressed_on_ground(self):
        inst, telem = self._make(VerticalSpeed=PEAK_DESCENT_VS,
                                 WeightOnWheels=[0.5, 0.5, 0])
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert not self.mock_effects["blade_slap_y"].started

    def test_disposed_when_disabled(self):
        inst, telem = self._make(VerticalSpeed=PEAK_DESCENT_VS)
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert self.mock_effects["blade_slap_y"].started
        inst.blade_slap_enable = False
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert not self.mock_effects["blade_slap_y"].started

    def test_blade_count_frequency_and_softening(self):
        # more blades: higher passage rate, softer slap character
        inst, telem = self._make(xplane=True, BladeSlap=1.0, PropRPM=[300.0])
        inst.ac_update_blade_slap(telem, blade_ct=4)
        freq, mag, _, _ = self.mock_effects["blade_slap_y"]._periodic
        assert freq == pytest.approx(20.0)          # 300 RPM x 4 blades / 60
        assert mag == pytest.approx(0.15 * 0.6, abs=1e-6)  # 2/4 clamped to 0.6

    def test_no_rotor_no_slap(self):
        inst, telem = self._make(xplane=True, BladeSlap=1.0, PropRPM=[0.0])
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert not self.mock_effects["blade_slap_y"].started

    def test_dcs_g_loading_via_accs_vector(self):
        # DCS/IL2 have no G field - normal load factor arrives as ACCs[1].
        # A loaded turn at band speed must trigger the G-term there too
        # (field report: no load-induced slap at all in DCS).
        inst, telem = self._make(ACCs=[0.0, 1.65, 0.0])
        telem["G"] = None
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert telem["_blade_slap_sig"] == pytest.approx(
            inst.blade_slap_g_factor * 0.75, abs=0.01)
        assert self.mock_effects["blade_slap_y"].started

    def test_plays_on_pedals_and_collective_devices(self):
        # the effect is deliberately not device-gated: pedals and collective
        # share the control-rod network and receive slap in the real aircraft
        for device in ("pedals", "collective"):
            inst, telem = self._make(device=device,
                                     VerticalSpeed=PEAK_DESCENT_VS)
            telem["FFBType"] = device
            inst.ac_update_blade_slap(telem, blade_ct=2)
            assert self.mock_effects["blade_slap_y"].started, device
            assert self.mock_effects["blade_slap_x"].started, device

    def test_band_center_setting_moves_the_response(self):
        # tune the band down: response peaks at the new center, and the old
        # 63 kt peak speed is now well off-peak
        low_center = 20.0
        inst, telem = self._make(VerticalSpeed=-low_center * 0.10510, IAS=low_center)
        inst.blade_slap_band_center = low_center
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert telem["_blade_slap_sig"] == pytest.approx(1.0, abs=0.01)

        inst2, telem2 = self._make(VerticalSpeed=PEAK_DESCENT_VS)  # IAS 32.4
        inst2.blade_slap_band_center = low_center
        inst2.ac_update_blade_slap(telem2, blade_ct=2)
        assert telem2["_blade_slap_sig"] < 0.6  # well off the re-centered peak

    def test_band_scales_with_center_no_hover_leakage(self):
        # even with a very low center, sub-5 m/s stays silent
        inst, telem = self._make(VerticalSpeed=-1.0, IAS=4.0)
        inst.blade_slap_band_center = 15.0
        inst.ac_update_blade_slap(telem, blade_ct=2)
        assert not self.mock_effects["blade_slap_y"].started
