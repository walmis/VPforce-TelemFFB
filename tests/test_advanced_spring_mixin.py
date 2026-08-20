import pytest

from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.SettingsManager import SpringModeEnum


class Dummy(AdvancedSpringMixIn):
    def __init__(self):
        # don't call super to avoid initializing hardware/effects
        # but need to set underlying storage
        self._spring_mode = SpringModeEnum.NONE

    def flag_error(self, message):
        # capture flag_error calls for assertions
        self._last_flag = message


def test_set_spring_mode_none_with_none():
    d = Dummy()
    d.spring_mode = None
    assert d.spring_mode == SpringModeEnum.NONE


def test_set_spring_mode_with_enum():
    d = Dummy()
    d.spring_mode = SpringModeEnum.ADVANCED
    assert d.spring_mode == SpringModeEnum.ADVANCED


def test_set_spring_mode_with_valid_string():
    d = Dummy()
    d.spring_mode = 'ADVANCED'
    assert d.spring_mode == SpringModeEnum.ADVANCED


def test_set_spring_mode_with_invalid_type_raises():
    d = Dummy()
    with pytest.raises(ValueError):
        d.spring_mode = 123


def test_set_spring_mode_with_unknown_name_falls_back():
    """An unknown mode name comes from a config written by another build;
    it must not raise (see tests/test_unknown_setting_values.py)."""
    from telemffb.SettingsManager import SpringModeEnum
    d = Dummy()
    d.spring_mode = 'INVALID'
    assert d.spring_mode == SpringModeEnum.NONE