"""Trimwheel devices are single-purpose: no haptic effects may run on them.

The mixin refactor lost the old per-class early-return guards, so the whole
cooperative effects chain (engine rumble was the one noticed in the field)
played through trimwheel devices. ``Aircraft.on_telemetry`` now gates
trimwheels BEFORE the chain; these tests pin that gate with a positive
control proving the negative assertion has teeth.
"""
import pytest

from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.PropellerAircraft import PropellerAircraft

pytestmark = [pytest.mark.unit, pytest.mark.msfs]


def _telem(ffb_type):
    return (TelemetryDataBuilder()
            .ffb_type(ffb_type)
            .set("N", "TestAircraft")
            .set("Parked", 0)
            .set("PropRPM", 2400.0)
            .set("IAS", 60.0)
            .set("AccBody", [0, 1, 0])
            .set("VelWorld", [0.0, 0.0, 60.0])
            .set("AmbWind", [0.0, 0.0, 0.0])
            .set("Heading", 0.0)
            .set("Pitch", 0.0)
            .set("Roll", 0.0)
            .build())


class TestTrimwheelEffectsGate(BaseTelemetryEffectTestCase):
    def _aircraft(self):
        ac = self.create_aircraft_instance(PropellerAircraft, _test_sim_is_msfs=True)
        ac._sim_is = lambda sim: sim == "MSFS"   # rumble uses _sim_is('MSFS')
        # make engine rumble definitely eligible so the joystick control run
        # proves the effect WOULD fire were it not for the device gate
        ac.engine_prop_rumble_enabled = True
        ac.engine_rumble_lowrpm = 600
        ac.engine_rumble_highrpm = 2700
        ac.engine_rumble_lowrpm_intensity = 0.1
        ac.engine_rumble_highrpm_intensity = 0.15
        return ac

    def _started_effects(self, exclude=("pause_spring",)):
        return [name for name, eff in self.mock_effects.dict.items()
                if eff.started and name not in exclude]

    def test_no_effects_run_on_a_trimwheel_device(self):
        ac = self._aircraft()
        telem = _telem("trimwheel")
        ac._telem_data = telem   # same object, like TelemManager does
        ac.on_telemetry(telem)
        assert self._started_effects() == [], \
            f"haptic effects must never play on a trimwheel: {self._started_effects()}"

    def test_engine_rumble_still_runs_on_a_joystick(self):
        # positive control: identical telemetry on a joystick must start the
        # prop rumble, so the trimwheel assertion above cannot pass vacuously
        ac = self._aircraft()
        telem = _telem("joystick")
        ac._telem_data = telem   # same object, like TelemManager does
        ac.on_telemetry(telem)
        started = self._started_effects()
        assert any(name.startswith("prop_rpm") for name in started), \
            f"expected prop rumble on a joystick; started={started}"
