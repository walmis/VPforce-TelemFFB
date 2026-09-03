"""Characterization tests for SASHelicopter cyclic SEMA follow.

These tests pin the observable behavior of the SAS cyclic path after the
TASK002 unit normalization: the spring center (cpO_x/cpO_y) is expressed as
a normalized float while ``set_offset()`` performs the device-unit conversion.

The device-unit ``spring_x/y.cpOffset`` assertions are the invariant
regression guard: the hardware must still receive the same device-unit offsets.
"""
import pytest

import telemffb.globals as G
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.SASHelicopter import SASHelicopter


@pytest.fixture(autouse=True)
def _firmware_version():
    G.device_firmware_version = "1.0.18"
    yield
    G.device_firmware_version = None


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestSASCyclicSemaFollow(BaseTelemetryEffectTestCase):
    """SEMA-driven spring-center follow (hands off the cyclic)."""

    def _inst(self):
        ac = self.create_aircraft_instance(
            SASHelicopter, name="SAS H160",
            _test_sim_is_msfs=True, _test_device_type="joystick")
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

    def test_sema_x_step_size_is_0_1x(self):
        ac = self._inst()
        # First rolling-average call returns the value itself: sema_x=20
        # -> historical 2-device-unit step = 2/4096 normalized.
        ac.msfs_update_heli_controls(self._telem(SEMAx=20, SEMAy=0))
        assert ac.afcsx_step_size == pytest.approx(2 / 4096)
        assert ac.cpO_x == pytest.approx(-2 / 4096)

    def test_sema_y_step_size_is_0_3x(self):
        ac = self._inst()
        # sema_y=10 -> historical 3-device-unit step = 3/4096 normalized
        ac.msfs_update_heli_controls(self._telem(SEMAx=0, SEMAy=10))
        assert ac.afcsy_step_size == pytest.approx(3 / 4096)
        assert ac.cpO_y == pytest.approx(-3 / 4096)

    def test_sema_sign_direction(self):
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(SEMAx=-20, SEMAy=-10))
        assert ac.cpO_x == pytest.approx(2 / 4096)
        assert ac.cpO_y == pytest.approx(3 / 4096)

    def test_spring_cpOffset_tracks_cpO_in_device_units(self):
        # THE invariant guard: the hardware must see the same device-unit
        # offset before and after the TASK002 normalization.
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(SEMAx=20, SEMAy=10))
        assert ac.spring_x.cpOffset == round(ac.cpO_x * 4096)
        assert ac.spring_y.cpOffset == round(ac.cpO_y * 4096)
        assert ac.spring_x.cpOffset == -2
        assert ac.spring_y.cpOffset == -3

    def test_hands_on_stops_sema_follow(self):
        ac = self._inst()
        # Hands-on state is dispatched at the END of the frame; frame 1 moves
        # the center, frame 2 must hold it.
        self.mock_device._input_data.set_axis(x=0.5, y=0.0)
        ac.msfs_update_heli_controls(self._telem(SEMAx=20, SEMAy=0))
        assert ac.hands_on_active == 1
        after_first = ac.cpO_x
        ac.msfs_update_heli_controls(self._telem(SEMAx=20, SEMAy=0))
        assert ac.cpO_x == after_first, \
            "SEMA follow must stop once hands-on is latched"

    def test_trim_release_freezes_follow(self):
        ac = self._inst()
        ac.msfs_update_heli_controls(
            self._telem(SEMAx=20, SEMAy=0, hpgTrimRelease=1))
        assert ac.cpO_x == 0 and ac.cpO_y == 0, \
            "trim release must freeze the SEMA follow"
