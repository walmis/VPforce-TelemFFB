"""Characterization tests for HPGHelicopter cyclic SEMA follow-up trim.

These tests pin the observable behavior of the HPG cyclic path BEFORE the
cpO_* unit normalization (TASK002): the spring center (cpO_x/cpO_y) is
expressed in DEVICE units (0..4096) and written raw to spring.cpOffset.

The device-unit ``spring_x/y.cpOffset`` assertions are the invariant
regression guard: after TASK002 normalizes cpO_* to -1..1, the hardware must
still receive the same device-unit offsets (via the type-sniffing
``set_offset()``), so these assertions must keep passing unchanged. Only the
internal ``cpO_*`` assertions are expected to be rewritten in normalized
units.
"""
import pytest

import telemffb.globals as G
import telemffb.sim.msfs_xp.HPGHelicopter as hpg_module
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.HPGHelicopter import HPGHelicopter


@pytest.fixture(autouse=True)
def _firmware_version():
    # _get_device_raw_axes() consults the firmware version via the mock device
    G.device_firmware_version = "1.0.18"
    yield
    G.device_firmware_version = None


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestHPGCyclicSemaFollow(BaseTelemetryEffectTestCase):
    """SEMA-driven spring-center follow (hands off the cyclic)."""

    def _inst(self):
        ac = self.create_aircraft_instance(
            HPGHelicopter, name="HPG H145",
            _test_sim_is_msfs=True, _test_device_type="joystick")
        # BASIC spring mode: the parent cyclic path leaves cpO_* untouched
        # (coefficients 0), so the HPG SEMA logic is the only mutator.
        from telemffb.SettingsManager import SpringModeEnum
        ac.spring_mode = SpringModeEnum.BASIC
        ac.telemffb_controls_axes = True
        ac.cyclic_spring_init = 1
        ac.hands_on_active = 0
        ac.hands_on_x_active = 0
        ac.hands_on_y_active = 0
        ac.cpO_x = 0
        ac.cpO_y = 0
        # stick centered -> hands-off (deviation 0 < deadzone)
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        return ac

    def _telem(self, **kw):
        b = (TelemetryDataBuilder()
             .ffb_type("joystick")
             .on_ground(False)
             .with_field("APMaster", 0))
        for k, v in kw.items():
            b = b.with_field(k, v)
        return b.build()

    def test_sema_positive_drives_cpO_x_negative(self):
        ac = self._inst()
        # First rolling-average call returns the value itself: sema_x=20
        # -> afcsx_step_size = 20 * 0.25 = 5 device units.
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=20, hpgSEMAy=0))
        assert ac.afcsx_step_size == pytest.approx(5.0)
        assert ac.cpO_x == pytest.approx(-5.0)
        assert ac.cpO_y == 0

    def test_sema_negative_drives_cpO_x_positive(self):
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=-20, hpgSEMAy=0))
        assert ac.cpO_x == pytest.approx(5.0)

    def test_sema_y_drives_cpO_y(self):
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=0, hpgSEMAy=10))
        assert ac.afcsy_step_size == pytest.approx(2.5)
        assert ac.cpO_y == pytest.approx(-2.5)

    def test_spring_cpOffset_tracks_cpO_in_device_units(self):
        # THE invariant guard: the hardware must see the same device-unit
        # offset before and after the TASK002 normalization.
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=20, hpgSEMAy=10))
        assert ac.spring_x.cpOffset == round(ac.cpO_x)
        assert ac.spring_y.cpOffset == round(ac.cpO_y)
        assert ac.spring_x.cpOffset == -5
        assert ac.spring_y.cpOffset == -2

    def test_hands_on_cyclic_stops_sema_follow(self):
        ac = self._inst()
        # Stick deflected far from the spring center -> hands on.  The
        # hands-on state is dispatched at the END of the frame, so it gates
        # the SEMA follow from the SECOND frame on.
        self.mock_device._input_data.set_axis(x=0.5, y=0.0)
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=20, hpgSEMAy=0))
        assert ac.hands_on_active == 1, "deflected stick must dispatch hands-on"
        # Frame 1 already moved the center (hands-on was latched at the END of
        # that frame); frame 2 must hold it — SEMA follow is gated off.
        after_first = ac.cpO_x
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=20, hpgSEMAy=0))
        assert ac.cpO_x == after_first, \
            "SEMA follow must stop once hands-on is latched"


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestHPGFollowupTrim(BaseTelemetryEffectTestCase):
    """Frame-rate-independent follow-up trim accumulator (hands on)."""

    def _inst(self):
        ac = self.create_aircraft_instance(
            HPGHelicopter, name="HPG H145",
            _test_sim_is_msfs=True, _test_device_type="joystick")
        from telemffb.SettingsManager import SpringModeEnum
        ac.spring_mode = SpringModeEnum.BASIC
        ac.telemffb_controls_axes = True
        ac.cyclic_spring_init = 1
        ac.hands_on_active = 1      # latched hands-on (dispatch normally sets this)
        ac.hands_on_x_active = 0
        ac.hands_on_y_active = 0
        ac.cpO_x = 0
        ac.cpO_y = 0
        ac.followup_trim_accumulator = 0.0
        # stick deflected -> hands on + nonzero deviation drives the follow
        self.mock_device._input_data.set_axis(x=0.5, y=0.0)
        return ac

    def _telem(self, **kw):
        b = (TelemetryDataBuilder()
             .ffb_type("joystick")
             .on_ground(False)
             .with_field("APMaster", 0)
             .with_field("hpgFollowupTrimMode", 0)   # 0 = always active
             .with_field("IAS", 0.0))
        for k, v in kw.items():
            b = b.with_field(k, v)
        return b.build()

    def test_followup_trim_steps_toward_stick_deflection(self, monkeypatch):
        # dt = 10 ms at the default rate (100 units/s) -> 1 device unit/frame.
        monkeypatch.setattr(
            hpg_module.perftracker, "get_time_delta", lambda name: 0.01)
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=0, hpgSEMAy=0))
        assert ac.cpO_x == pytest.approx(1.0)
        # accumulator keeps the fractional remainder (0.01*100 = 1 exactly)
        assert ac.followup_trim_accumulator == pytest.approx(0.0, abs=1e-9)

    def test_followup_accumulator_is_frame_rate_independent(self, monkeypatch):
        # Two frames at dt=5 ms must move the center the same total distance
        # as one frame at dt=10 ms (100 units/s in both cases).
        monkeypatch.setattr(
            hpg_module.perftracker, "get_time_delta", lambda name: 0.005)
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=0, hpgSEMAy=0))
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=0, hpgSEMAy=0))
        assert ac.cpO_x == pytest.approx(1.0)

    def test_followup_accumulator_carries_fraction(self, monkeypatch):
        # dt = 15 ms -> 1.5 units: 1 whole unit applied, 0.5 carried.
        monkeypatch.setattr(
            hpg_module.perftracker, "get_time_delta", lambda name: 0.015)
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=0, hpgSEMAy=0))
        assert ac.cpO_x == pytest.approx(1.0)
        assert ac.followup_trim_accumulator == pytest.approx(0.5, abs=1e-9)
        # Next frame adds another 1.5 -> 0.5+1.5 = 2.0 -> 2 units applied.
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=0, hpgSEMAy=0))
        assert ac.cpO_x == pytest.approx(3.0)
        assert ac.followup_trim_accumulator == pytest.approx(0.0, abs=1e-9)

    def test_followup_trim_disabled_on_ground(self, monkeypatch):
        monkeypatch.setattr(
            hpg_module.perftracker, "get_time_delta", lambda name: 0.01)
        ac = self._inst()
        ac.msfs_update_heli_controls(
            self._telem(hpgSEMAx=0, hpgSEMAy=0, SimOnGround=1))
        assert ac.cpO_x == 0, "follow-up trim must be suppressed on the ground"

    def test_followup_trim_respects_mode_off(self, monkeypatch):
        monkeypatch.setattr(
            hpg_module.perftracker, "get_time_delta", lambda name: 0.01)
        ac = self._inst()
        ac.msfs_update_heli_controls(
            self._telem(hpgSEMAx=0, hpgSEMAy=0, hpgFollowupTrimMode=1))
        assert ac.cpO_x == 0, "hpgFollowupTrimMode=1 (off) must suppress follow-up"

    def test_followup_spring_cpOffset_tracks_cpO(self, monkeypatch):
        monkeypatch.setattr(
            hpg_module.perftracker, "get_time_delta", lambda name: 0.05)
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(hpgSEMAx=0, hpgSEMAy=0))
        # 0.05 s * 100 units/s = 5 device units
        assert ac.cpO_x == pytest.approx(5.0)
        assert ac.spring_x.cpOffset == round(ac.cpO_x)
        assert ac.spring_x.cpOffset == 5


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestHPGPedalSemaFollow(BaseTelemetryEffectTestCase):
    """Pedal (yaw SEMA) spring-center follow."""

    def _inst(self):
        ac = self.create_aircraft_instance(
            HPGHelicopter, name="HPG H145",
            _test_sim_is_msfs=True, _test_device_type="pedals")
        from telemffb.SettingsManager import SpringModeEnum
        ac.spring_mode = SpringModeEnum.BASIC
        ac.telemffb_controls_axes = True
        ac.pedals_init = 1
        ac.feet_on_active = 0
        ac.cpO_x = 0
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        return ac

    def _telem(self, **kw):
        b = (TelemetryDataBuilder()
             .ffb_type("pedals")
             .on_ground(False)
             .with_field("APMaster", 0))
        for k, v in kw.items():
            b = b.with_field(k, v)
        return b.build()

    def test_yaw_sema_drives_cpO_x(self):
        ac = self._inst()
        # sema_yaw=10 -> afcsx_step_size = 10 * 0.5 = 5 device units
        telem = self._telem(hpgSEMAyaw=10)
        ac.msfs_update_pedals(telem)
        assert ac.afcsx_step_size == pytest.approx(5.0)
        assert ac.cpO_x == pytest.approx(-5.0)
        # telemetry exposure: _cp0_x mirrors the pedal spring center
        assert getattr(telem, "_cp0_x") == pytest.approx(ac.cpO_x)

    def test_pedal_spring_offset_uses_set_offset(self):
        # The pedal path already routes through set_offset(); with device-unit
        # cpO_x the mock's magnitude heuristic passes it through unchanged.
        ac = self._inst()
        ac.msfs_update_pedals(self._telem(hpgSEMAyaw=10))
        assert ac.spring_x.cpOffset == round(ac.cpO_x)
        assert ac.spring_x.cpOffset == -5

    def test_feet_on_pedals_freezes_sema_follow(self):
        ac = self._inst()
        # feet_on_active is re-derived each frame from deflection detection:
        # sim reports feet-on AND the pedals are deflected past the threshold.
        self.mock_device._input_data.set_axis(x=0.3, y=0.0)
        ac.msfs_update_pedals(self._telem(hpgSEMAyaw=10, hpgFeetOnPedals=1))
        assert ac.feet_on_active is True
        assert ac.cpO_x == 0, "SEMA follow must pause while feet are on the pedals"
