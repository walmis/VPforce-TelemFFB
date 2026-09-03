"""XAW109 pedal AFCS following — the modeled parallel trim.

The aircraft's own yaw servo only commands pedal motion above the certified
trim thresholds (nearly never in normal flight — the "pedals feel dead"
known issue). The renderer instead proportionally unwinds the low-passed
trim demand (zero + coarse) through a Schmitt deadband, fades the rate with
IAS (the anti-oscillation measure), and defers to the plugin's own
rudder_trim_rate whenever it speaks.
"""
import pytest

import telemffb.globals as G
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.XAW109Helicopter import XAW109Helicopter


@pytest.fixture(autouse=True)
def _firmware_version():
    # _get_device_raw_axes() consults the firmware version via the mock device
    G.device_firmware_version = "1.0.18"
    yield
    G.device_firmware_version = None


class TestXAW109PedalAfcs(BaseTelemetryEffectTestCase):
    def _inst(self):
        ac = self.create_aircraft_instance(
            XAW109Helicopter, name="AW109",
            _test_sim_is_xplane=True, _test_device_type="pedals")
        ac.PEDAL_AFCS_LPF_TAU = 0.0   # tests: no filter lag (deterministic)
        ac.afcs_threshold_value = 0.4
        ac.afcs_motion_rate = 4
        ac.pedal_spring_gain = 1
        return ac

    def _telem(self, **kw):
        t = TelemetryDataBuilder().ffb_type("pedals").build()
        t["SimOnGround"] = kw.pop("SimOnGround", 1)
        for k, v in kw.items():
            t[k] = v
        return t

    def _init_pedals(self, ac):
        # Ground init: cpO 0 with pedals physically centered initializes
        # on the first frame.
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        ac.msfs_update_pedals(self._telem(SimOnGround=1))
        assert ac.pedals_init == 1

    def test_proportional_channel_with_schmitt_hysteresis(self):
        ac = self._inst()
        self._init_pedals(ac)
        # Demand above the enter threshold: pedals step toward the demand.
        t_hi = self._telem(SimOnGround=0, AW109_rud_trim_zero=0.35,
                           AW109_rud_trim_coarse=0.15, IAS=10.0)
        ac.msfs_update_pedals(t_hi)
        assert ac.cpO_x > 0, "must step in the demand direction"
        first = ac.cpO_x
        # Inside the hysteresis band (exit < |s| < enter): still stepping.
        t_band = self._telem(SimOnGround=0, AW109_rud_trim_zero=0.20,
                             AW109_rud_trim_coarse=0.05, IAS=10.0)
        ac.msfs_update_pedals(t_band)
        assert ac.cpO_x > first, "must keep unwinding inside the band"
        # Below the exit threshold: stops.
        t_lo = self._telem(SimOnGround=0, AW109_rud_trim_zero=0.05,
                           AW109_rud_trim_coarse=0.0, IAS=10.0)
        ac.msfs_update_pedals(t_lo)
        stopped = ac.cpO_x
        ac.msfs_update_pedals(t_lo)
        assert ac.cpO_x == stopped, "must hold once below the exit threshold"
        # Re-entering the band does NOT restart (hysteresis, not threshold).
        ac.msfs_update_pedals(t_band)
        assert ac.cpO_x == stopped, "band re-entry must not re-arm"

    def test_upper_mode_rate_command_is_authoritative(self):
        ac = self._inst()
        self._init_pedals(ac)
        before = ac.cpO_x
        t = self._telem(SimOnGround=0, AW109_rudder_trim_rate=-1,
                        AW109_rud_trim_zero=0.0, IAS=10.0)
        ac.msfs_update_pedals(t)
        assert ac.cpO_x == before - ac.afcs_motion_rate

    def test_ias_fade_reduces_proportional_step(self):
        slow = self._inst()
        self._init_pedals(slow)
        slow.msfs_update_pedals(self._telem(
            SimOnGround=0, AW109_rud_trim_zero=0.5, IAS=20.0))   # ~39 kt
        fast = self._inst()
        self._init_pedals(fast)
        fast.msfs_update_pedals(self._telem(
            SimOnGround=0, AW109_rud_trim_zero=0.5, IAS=60.0))   # ~117 kt
        assert 0 < fast.cpO_x < slow.cpO_x, \
            "step must fade as IAS rises into the oscillation-prone regime"

    def test_ground_lead_in_not_frozen(self):
        # Raising collective before liftoff builds trim demand; the real
        # aircraft leads with pedal before the wheels leave — the renderer
        # must not freeze the pedals on the ground.
        ac = self._inst()
        self._init_pedals(ac)
        t = self._telem(SimOnGround=1, AW109_rud_trim_zero=-0.5, IAS=0.0)
        ac.msfs_update_pedals(t)
        assert ac.cpO_x < 0, "on-ground demand must move the pedals"

    def test_force_trim_release_tracks_feet(self):
        ac = self._inst()
        self._init_pedals(ac)
        self.mock_device._input_data.set_axis(x=0.4, y=0.0)
        t = self._telem(SimOnGround=0,
                        AW109_ped_force_trim_release_pressed=1)
        ac.msfs_update_pedals(t)
        assert ac.cpO_x == round(0.4 * 4096)


@pytest.mark.unit
@pytest.mark.xplane
@pytest.mark.helicopter
class TestXAW109CyclicAfcs(BaseTelemetryEffectTestCase):
    """Cyclic AFCS follow: trim-rate telemetry drives the spring center.

    Untested before TASK002 — pins the device-unit spring offsets (the
    invariant hardware contract) and the per-frame step math.
    """

    def _inst(self):
        ac = self.create_aircraft_instance(
            XAW109Helicopter, name="AW109",
            _test_sim_is_xplane=True, _test_device_type="joystick")
        from telemffb.SettingsManager import SpringModeEnum
        ac.spring_mode = SpringModeEnum.BASIC
        ac.telemffb_controls_axes = True
        ac.cyclic_spring_init = 1
        ac.cpO_x = 0
        ac.cpO_y = 0
        ac.afcs_motion_rate = 4
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        return ac

    def _telem(self, **kw):
        b = (TelemetryDataBuilder()
             .ffb_type("joystick")
             .on_ground(False)
             .with_field("APServos", 0))
        for k, v in kw.items():
            b = b.with_field(k, v)
        return b.build()

    def test_trim_rate_drives_spring_center(self):
        ac = self._inst()
        # rate=0.5 * afcs_motion_rate=4 -> 2 device units per frame
        ac.msfs_update_heli_controls(
            self._telem(AW109_aileron_trim_rate=0.5,
                        AW109_elevator_trim_rate=-0.25))
        assert ac.afcsx_step_size == pytest.approx(2.0)
        assert ac.afcsy_step_size == pytest.approx(-1.0)
        assert ac.cpO_x == pytest.approx(2.0)
        assert ac.cpO_y == pytest.approx(-1.0)

    def test_spring_cpOffset_tracks_cpO_in_device_units(self):
        # THE invariant guard: hardware must see the same device-unit offsets
        # before and after the TASK002 normalization.
        ac = self._inst()
        ac.msfs_update_heli_controls(
            self._telem(AW109_aileron_trim_rate=0.5,
                        AW109_elevator_trim_rate=0.25))
        assert ac.spring_x.cpOffset == round(ac.cpO_x)
        assert ac.spring_y.cpOffset == round(ac.cpO_y)
        assert ac.spring_x.cpOffset == 2
        assert ac.spring_y.cpOffset == 1

    def test_zero_trim_rate_holds_center(self):
        ac = self._inst()
        ac.msfs_update_heli_controls(
            self._telem(AW109_aileron_trim_rate=0,
                        AW109_elevator_trim_rate=0))
        assert ac.cpO_x == 0 and ac.cpO_y == 0


@pytest.mark.unit
@pytest.mark.xplane
@pytest.mark.helicopter
class TestXAW109Collective(BaseTelemetryEffectTestCase):
    """Collective spring: init handshake + AFCS ratio tracking."""

    def _inst(self):
        ac = self.create_aircraft_instance(
            XAW109Helicopter, name="AW109",
            _test_sim_is_xplane=True, _test_device_type="collective")
        ac.telemffb_controls_axes = True
        ac.collective_init = 0
        ac.last_collective_y = None
        return ac

    def _telem(self, **kw):
        b = (TelemetryDataBuilder()
             .ffb_type("collective")
             .on_ground(False)
             .with_field("APServos", 0))
        for k, v in kw.items():
            b = b.with_field(k, v)
        return b.build()

    def test_ground_init_parks_at_full_down(self):
        ac = self._inst()
        self.mock_device._input_data.set_axis(x=0.0, y=1.0)  # full down
        ac.msfs_update_collective(self._telem(SimOnGround=1))
        assert ac.collective_init == 1
        assert ac.cpO_y == 4096
        # THE invariant guard: hardware sees full-down device units.
        assert ac.spring_y.cpOffset == 4096

    def test_air_init_uses_physical_position(self):
        ac = self._inst()
        self.mock_device._input_data.set_axis(x=0.0, y=0.5)
        ac.msfs_update_collective(self._telem(SimOnGround=0))
        assert ac.collective_init == 1
        assert ac.cpO_y == round(0.5 * 4096)
        assert ac.spring_y.cpOffset == round(0.5 * 4096)

    def test_afcs_ratio_drives_spring_center(self):
        ac = self._inst()
        # init at full down (lever physically at 1.0 matches the init point)
        self.mock_device._input_data.set_axis(x=0.0, y=1.0)
        ac.msfs_update_collective(self._telem(SimOnGround=1))
        assert ac.collective_init == 1
        # AW109_collective_ratio 0.25 -> scale_clamp((0,1)->(4096,-4096)) = 2048
        ac.msfs_update_collective(
            self._telem(SimOnGround=0, AW109_collective_mode=1,
                        AW109_collective_ratio=0.25))
        assert ac.cpO_y == 2048
        assert ac.spring_y.cpOffset == 2048

    def test_afcs_ratio_full_up_reaches_negative_full_scale(self):
        ac = self._inst()
        self.mock_device._input_data.set_axis(x=0.0, y=1.0)
        ac.msfs_update_collective(self._telem(SimOnGround=1))
        ac.msfs_update_collective(
            self._telem(SimOnGround=0, AW109_collective_mode=1,
                        AW109_collective_ratio=1.0))
        assert ac.cpO_y == -4096
        assert ac.spring_y.cpOffset == -4096

    def test_init_gate_blocks_until_centered(self):
        ac = self._inst()
        # lever far from the full-down init point -> init must not complete
        self.mock_device._input_data.set_axis(x=0.0, y=-0.5)
        ac.msfs_update_collective(self._telem(SimOnGround=1))
        assert ac.collective_init == 0
