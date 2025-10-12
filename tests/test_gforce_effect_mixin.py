import pytest

from telemffb.sim.base.GForceEffectMixIn import GForceEffectMixIn
from telemffb.SettingsManager import GEffectModeEnum


class DummyG(GForceEffectMixIn):
    def __init__(self):
        # avoid calling parent init to prevent hardware/effect setup
        self._gforce_effect_mode = GEffectModeEnum.DISABLED

    def flag_error(self, message):
        self._last_flag = message


def test_set_gforce_mode_none_sets_disabled():
    d = DummyG()
    d.gforce_effect_mode = None
    assert d.gforce_effect_mode == GEffectModeEnum.DISABLED


def test_set_gforce_mode_with_enum():
    d = DummyG()
    d.gforce_effect_mode = GEffectModeEnum.ADVANCED
    assert d.gforce_effect_mode == GEffectModeEnum.ADVANCED


def test_set_gforce_mode_with_valid_string():
    d = DummyG()
    d.gforce_effect_mode = 'NEW'
    assert d.gforce_effect_mode == GEffectModeEnum.NEW


def test_set_gforce_mode_with_invalid_string_raises():
    d = DummyG()
    with pytest.raises(ValueError):
        d.gforce_effect_mode = 'INVALID'


def test_set_gforce_mode_with_invalid_type_raises():
    d = DummyG()
    with pytest.raises(ValueError):
        d.gforce_effect_mode = 123
