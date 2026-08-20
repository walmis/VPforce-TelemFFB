"""A stored setting this build does not understand must not break the load.

Configs are shared between builds (and edited by hand), so a value like a
spring mode that only exists in another branch will turn up here.  Before
this safeguard the enum setter raised, the exception escaped apply_settings
and aborted the aircraft load, and because the settings form is only built
for a loaded aircraft the offending value could not be corrected from the
UI at all - it had to be edited out of the XML by hand.

The stored value is deliberately left alone: the user replaces it by
picking a valid one in the settings, and until then it still resolves under
the build that wrote it.
"""
import logging

import pytest

pytest.importorskip("PyQt6")

from telemffb.SettingsManager import GEffectModeEnum, SpringModeEnum
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.base.GForceEffectMixIn import GForceEffectMixIn

pytestmark = [pytest.mark.unit]


class TestUnknownEnumValues:
    def test_unknown_spring_mode_falls_back(self, caplog):
        inst = AdvancedSpringMixIn()
        with caplog.at_level(logging.ERROR):
            inst.spring_mode = "DINPUT_TAP"      # exists only in another build

        assert inst.spring_mode == SpringModeEnum.NONE
        msg = " ".join(r.message for r in caplog.records)
        assert "DINPUT_TAP" in msg
        # ERROR so the exception tracker surfaces it: a silent fallback would
        # leave the user wondering why the spring stopped behaving
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_unknown_gforce_mode_falls_back(self, caplog):
        inst = GForceEffectMixIn()
        with caplog.at_level(logging.ERROR):
            inst.gforce_effect_mode = "SOME_FUTURE_MODE"

        assert inst.gforce_effect_mode == GEffectModeEnum.DISABLED
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_known_values_still_work(self):
        inst = AdvancedSpringMixIn()
        inst.spring_mode = "ADVANCED"
        assert inst.spring_mode == SpringModeEnum.ADVANCED
        inst.spring_mode = SpringModeEnum.CENTER      # enum instance
        assert inst.spring_mode == SpringModeEnum.CENTER
        inst.spring_mode = None
        assert inst.spring_mode == SpringModeEnum.NONE

    def test_wrong_type_still_raises(self):
        """A non-string, non-enum value means a code bug, not stale config."""
        inst = AdvancedSpringMixIn()
        with pytest.raises(ValueError):
            inst.spring_mode = 42


class TestApplySettingsIsResilient:
    """The backstop: any setting that rejects its value is skipped, not fatal."""

    class _Subject(AircraftEffectUtilsBase):
        def __init__(self):
            super().__init__()
            self.before = None
            self.after = None
            self._picky = None

        @property
        def picky(self):
            return self._picky

        @picky.setter
        def picky(self, value):
            raise ValueError(f"refusing {value!r}")

    def test_one_bad_setting_does_not_abort_the_rest(self, caplog):
        subject = self._Subject()
        with caplog.at_level(logging.ERROR):
            subject.apply_settings({
                "before": "applied",
                "picky": "bad_value",
                "after": "also applied",
            })

        assert subject.before == "applied"
        assert subject.after == "also applied", "settings after the failure were skipped"
        assert subject.picky is None

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors and "picky" in errors[0].message
        assert "bad_value" in errors[0].message

    def test_unknown_spring_mode_through_apply_settings(self, caplog):
        """End to end: the reported scenario leaves a usable aircraft."""
        class Aircraft(AdvancedSpringMixIn):
            pass

        inst = Aircraft()
        with caplog.at_level(logging.ERROR):
            inst.apply_settings({"spring_mode": "DINPUT_TAP", "damper_force": 0.5})

        assert inst.spring_mode == SpringModeEnum.NONE
        assert inst.damper_force == 0.5      # the rest of the config still applied
