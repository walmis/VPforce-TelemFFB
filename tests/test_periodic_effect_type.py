"""The waveform of a periodic effect must be passed by keyword.

``HapticEffect.periodic(frequency, magnitude, direction, *args,
effect_type=...)`` keeps ``*args`` for a DirectionModulator's constructor.
A waveform passed positionally as the fourth argument landed there and was
silently discarded, so nine effects rendered as sine while their code said
square or sawtooth (flaps, canopy, gear, speedbrake motion, both stick
shakers, tailhook, fuel boom, wing fold).  ``periodic`` now refuses a
stray positional argument, the call sites pass ``effect_type=``, and these
pin both: the guard, and the waveform each fixed effect actually renders.
"""
import pytest

import telemffb.utils as utils
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.hw.ffb_rhino import (
    HapticEffect, EFFECT_SINE, EFFECT_SQUARE, EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN)
from telemffb.sim import aircrafts_dcs, aircrafts_msfs_xp


class TestPeriodicGuard:
    def test_positional_waveform_is_refused(self):
        with pytest.raises(TypeError, match="effect_type="):
            HapticEffect().periodic(10, 0.5, 0, EFFECT_SQUARE)

    def test_keyword_waveform_is_accepted(self):
        fx = HapticEffect().periodic(10, 0.5, 0, effect_type=EFFECT_SQUARE)
        assert fx._pending_create is not None            # queued for the device

    def test_modulator_direction_still_takes_constructor_args(self):
        # the one legitimate use of the positional slot
        fx = HapticEffect().periodic(10, 0.5, utils.RandomDirectionModulator)
        assert fx.modulator is not None


class TestFixedCallSitesRenderTheirWaveform(BaseTelemetryEffectTestCase):
    """Drive each fixed effect through a real aircraft class and read the
    waveform the mock captured (production would have dropped it)."""

    def _frames(self, ac, method, field, values, **extra):
        for v in values:
            t = TelemetryDataBuilder().ffb_type("joystick").build()
            t[field] = v
            for k, val in extra.items():
                t[k] = val
            ac._telem_data = t
            method(t)

    def test_flaps_motion_is_square(self):
        ac = aircrafts_dcs.Aircraft('t')
        ac.flaps_motion_effect_enabled = True
        ac.flaps_motion_intensity = 0.2
        self._frames(ac, ac.ac_update_flaps, 'Flaps', [0.0, 0.5])
        assert self.mock_effects['flapsmovement']._periodic[3] == {'effect_type': EFFECT_SQUARE}

    def test_gear_motion_is_square_on_both_axes(self):
        ac = aircrafts_dcs.Aircraft('t')
        ac.gear_motion_effect_enabled = True
        ac.gear_motion_intensity = 0.2
        self._frames(ac, ac.ac_update_landing_gear, 'gear_value', [0.0, 0.5], IAS=0.0)
        assert self.mock_effects['gearmovement']._periodic[3] == {'effect_type': EFFECT_SQUARE}
        assert self.mock_effects['gearmovement2']._periodic[3] == {'effect_type': EFFECT_SQUARE, 'phase': 120}

    def test_gear_buffet_stays_sine(self):
        ac = aircrafts_dcs.Aircraft('t')
        ac.gear_buffet_effect_enabled = True
        ac.gear_buffet_intensity = 0.2
        self._frames(ac, ac.ac_update_landing_gear, 'gear_value', [1.0], IAS=ac.gear_buffet_speed_high)
        assert self.mock_effects['gearbuffet']._periodic[3] == {'effect_type': EFFECT_SINE}

    def test_tailhook_motion_is_sawtooth_up(self):
        ac = aircrafts_dcs.Aircraft('t')
        ac.tailhook_motion_effect_enabled = True
        ac.tailhook_motion_intensity = 0.2
        self._frames(ac, ac.ac_update_tailhook_effect, 'TailHook', [0.0, 0.5])
        assert self.mock_effects['hookmovement']._periodic[3] == {'effect_type': EFFECT_SAWTOOTHUP}

    def test_fuel_boom_motion_is_sawtooth_down(self):
        ac = aircrafts_dcs.Aircraft('t')
        ac.fuelboom_motion_effect_enabled = True
        ac.fuelboom_motion_intensity = 0.2
        self._frames(ac, ac.ac_update_fuelboom_effect, 'FuelBoom', [0.0, 0.5])
        assert self.mock_effects['boommovement']._periodic[3] == {'effect_type': EFFECT_SAWTOOTHDOWN, 'phase': 0}

    def test_dcs_stick_shaker_is_a_sawtooth_pair(self):
        ac = aircrafts_dcs.Aircraft('t')
        ac.enable_stick_shaker = True
        ac.stick_shaker_intensity = 0.5
        self._frames(ac, ac.dcs_update_stick_shaker, 'AoA', [ac.stick_shaker_aoa + 5], SimOnGround=0)
        assert self.mock_effects['stick_shaker1']._periodic[3] == {'effect_type': EFFECT_SAWTOOTHUP}
        assert self.mock_effects['stick_shaker2']._periodic[3] == {'effect_type': EFFECT_SAWTOOTHDOWN}

    def test_msfs_stick_shaker_is_square(self):
        ac = aircrafts_msfs_xp.Aircraft('t')
        ac.enable_stick_shaker = True
        ac.stick_shaker_intensity = 0.5
        t = TelemetryDataBuilder().ffb_type("joystick").build()
        t['src'] = 'MSFS'
        t['StallWarning'] = 1
        ac._telem_data = t
        ac.msfs_update_stick_shaker(t)
        assert self.mock_effects['stick_shaker']._periodic[3] == {'effect_type': EFFECT_SQUARE}
