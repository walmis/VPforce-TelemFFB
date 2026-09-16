"""Endpoint clunks for the motion effects that go through
``_create_motion_effect`` (fuel boom, wing fold).

Regression: the mixin refactor (60cb2bd) carried over the copy of the
helper whose clunk loop computed a direction and never issued the
periodic, so the boom and wing-fold clunks silently stopped playing
while the tailhook and canopy (which render their own) kept theirs.
These pin the restored behaviour: once the surface stops at 0 or 1 and
the change tracker's quiet period has passed, one square-wave clunk per
configured slot, at twice the motion intensity, aimed per the config.
"""
import time

import pytest

from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.hw.ffb_rhino import EFFECT_SQUARE
from telemffb.sim import aircrafts_dcs


class TestMotionEndpointClunk(BaseTelemetryEffectTestCase):
    QUIET = 0.25   # past the helper's 200 ms change-tracker window

    def _inst(self):
        # A real aircraft class: the framework's generic test instance
        # stubs anything_has_changed without its delta_ms quiet period,
        # which is the very thing the endpoint clunk keys on.
        inst = aircrafts_dcs.Aircraft('test')
        inst.fuelboom_motion_effect_enabled = True
        inst.fuelboom_motion_intensity = 0.2
        inst.wingfold_motion_effect_enabled = True
        inst.wingfold_motion_intensity = 0.2
        return inst

    def _drive(self, inst, method, field, values, on_ground=0):
        """Feed a value sequence, then the last value again after the
        quiet period (the frame on which the endpoint clunk fires)."""
        for v in values:
            t = TelemetryDataBuilder().ffb_type("joystick").build()
            t[field] = v
            t['SimOnGround'] = on_ground
            inst._telem_data = t
            method(t)
        time.sleep(self.QUIET)
        method(t)

    def test_fuel_boom_extending_clunks_forward(self):
        inst = self._inst()
        self._drive(inst, inst.ac_update_fuelboom_effect, 'FuelBoom', [0.0, 0.5, 1.0])
        clunk = self.mock_effects['clunk']
        assert clunk.started
        assert clunk._periodic == (10, pytest.approx(0.4), 0,
                                   {'effect_type': EFFECT_SQUARE, 'duration': 40})
        assert 'boommovement' not in self.mock_effects        # motion disposed

    def test_fuel_boom_retracting_clunks_aft(self):
        inst = self._inst()
        self._drive(inst, inst.ac_update_fuelboom_effect, 'FuelBoom', [1.0, 0.5, 0.0])
        assert self.mock_effects['clunk']._periodic[2] == 180

    def test_wing_fold_clunks_twice_per_its_config(self):
        inst = self._inst()
        self._drive(inst, inst.ac_update_wingfold_effect, 'WingFold', [0.0, 0.5, 1.0], on_ground=1)
        one = self.mock_effects['wingfoldclunk1']._periodic
        two = self.mock_effects['wingfoldclunk2']._periodic
        assert one == (10, pytest.approx(0.4), 90, {'effect_type': EFFECT_SQUARE, 'duration': 100})
        assert two == (10, pytest.approx(0.4), 270, {'effect_type': EFFECT_SQUARE, 'duration': 100})

    def test_no_clunk_mid_travel(self):
        inst = self._inst()
        self._drive(inst, inst.ac_update_fuelboom_effect, 'FuelBoom', [0.0, 0.3, 0.6])
        assert 'clunk' not in self.mock_effects

    def test_no_clunk_without_prior_motion(self):
        """A surface that was already at an endpoint when telemetry began
        never moved, so nothing to seat."""
        inst = self._inst()
        self._drive(inst, inst.ac_update_fuelboom_effect, 'FuelBoom', [1.0, 1.0])
        assert 'clunk' not in self.mock_effects
