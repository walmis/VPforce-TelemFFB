"""What keeps the stick springed when telemetry stops.

Two guards cooperate on a timeout:

- ``MsfsXpFBWFlightControlsMixIn`` stops its private flight-control
  spring handle - but only when keep_forces_on_pause is off, so the
  AircraftBase keep-forces protection is not undone one call later.
  This used to be unconditional, which left a DirectInput device
  (whose own centering spring TelemFFB switched off at open) with no
  centering force at all: a force-free stick holds its deflection and
  the aircraft follows it - the user's "no controls after pause".
- The MSFS/XP ``Aircraft`` starts a full centering spring
  (``pause_spring``) when the user's center_spring_on_pause setting
  says so, or unconditionally on a device with no native centering.
"""
import pytest

from telemffb.hw.ffb_backend import DeviceCapabilities, VPFORCE_CAPABILITIES
from telemffb.hw.ffb_rhino import HapticEffect
from tests.framework.base import BaseTelemetryEffectTestCase
from telemffb.sim.msfs_xp.Aircraft import Aircraft
from telemffb.sim.msfs_xp.MsfsXpFBWFlightControlsMixIn import MsfsXpFBWFlightControlsMixIn

pytestmark = [pytest.mark.unit, pytest.mark.msfs]


class TestFbwSpringTimeout(BaseTelemetryEffectTestCase):
    def _instance(self, keep_forces):
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance.keep_forces_on_pause = keep_forces
        return instance

    def test_keep_forces_on_keeps_the_flight_spring(self):
        instance = self._instance(True)
        spring = instance._spring_handle
        spring.start()
        instance.on_timeout()
        assert spring.started, "the FBW mixin must not undo keep_forces_on_pause"
        assert spring.stop_count == 0

    def test_keep_forces_off_stops_the_flight_spring(self):
        instance = self._instance(False)
        spring = instance._spring_handle
        spring.start()
        instance.on_timeout()
        assert not spring.started

    def test_a_missing_setting_behaves_like_off(self):
        """No config entry for the setting (a fresh instance) must fall
        back to the old behavior, not crash the timeout."""
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        assert not hasattr(instance, "keep_forces_on_pause")
        spring = instance._spring_handle
        spring.start()
        instance.on_timeout()          # must not raise
        assert not spring.started


class TestPauseSpringOnTimeout(BaseTelemetryEffectTestCase):
    def _aircraft(self, keep_forces=True):
        ac = self.create_aircraft_instance(Aircraft, _test_sim_is_msfs=True)
        ac.keep_forces_on_pause = keep_forces
        return ac

    def _dinput_device(self, disabled=True):
        device = self.mock_device
        device.caps = DeviceCapabilities(has_cp_telemetry=True,
                                        autocenter_disabled=disabled)
        HapticEffect.device = device
        return device

    def test_dinput_device_gets_a_pause_spring_without_the_setting(self):
        """The user's report: a MOZA stick whose native spring TelemFFB
        switched off, center_spring_on_pause never set, timeout while
        parked - the stick must not be left force-free."""
        self._dinput_device(disabled=True)
        ac = self._aircraft()
        ac.center_spring_on_pause = False
        ac.on_timeout()
        assert self.mock_effects["pause_spring"].started

    def test_dinput_device_gets_one_even_with_keep_forces_off(self):
        self._dinput_device(disabled=True)
        ac = self._aircraft(keep_forces=False)
        ac.center_spring_on_pause = False
        ac.on_timeout()
        assert self.mock_effects["pause_spring"].started

    def test_a_device_that_keeps_its_native_spring_respects_the_setting(self):
        """VPforce hardware centers itself through a timeout, so the
        pause spring stays the user's opt-in there."""
        self.mock_device.caps = VPFORCE_CAPABILITIES
        HapticEffect.device = self.mock_device
        ac = self._aircraft()
        ac.center_spring_on_pause = False
        ac.on_timeout()
        assert not self.mock_effects["pause_spring"].started

    def test_the_setting_still_applies_on_any_device(self):
        self._dinput_device(disabled=False)
        ac = self._aircraft()
        ac.center_spring_on_pause = True
        ac.on_timeout()
        assert self.mock_effects["pause_spring"].started

    def test_a_missing_device_is_not_a_crash(self):
        HapticEffect.device = None
        ac = self._aircraft()
        ac.center_spring_on_pause = False
        ac.on_timeout()          # must not raise
        assert not self.mock_effects["pause_spring"].started
