"""Characterization tests for SASHelicopter cyclic SEMA follow.

These tests pin the observable behavior of the SAS cyclic path BEFORE the
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
        # -> afcsx_step_size = 20 * 0.1 = 2 device units.
        ac.msfs_update_heli_controls(self._telem(SEMAx=20, SEMAy=0))
        assert ac.afcsx_step_size == pytest.approx(2.0)
        assert ac.cpO_x == pytest.approx(-2.0)

    def test_sema_y_step_size_is_0_3x(self):
        ac = self._inst()
        # sema_y=10 -> afcsy_step_size = 10 * 0.3 = 3 device units
        ac.msfs_update_heli_controls(self._telem(SEMAx=0, SEMAy=10))
        assert ac.afcsy_step_size == pytest.approx(3.0)
        assert ac.cpO_y == pytest.approx(-3.0)

    def test_sema_sign_direction(self):
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(SEMAx=-20, SEMAy=-10))
        assert ac.cpO_x == pytest.approx(2.0)
        assert ac.cpO_y == pytest.approx(3.0)

    def test_spring_cpOffset_tracks_cpO_in_device_units(self):
        # THE invariant guard: the hardware must see the same device-unit
        # offset before and after the TASK002 normalization.
        ac = self._inst()
        ac.msfs_update_heli_controls(self._telem(SEMAx=20, SEMAy=10))
        assert ac.spring_x.cpOffset == round(ac.cpO_x)
        assert ac.spring_y.cpOffset == round(ac.cpO_y)
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