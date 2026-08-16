"""
Testing framework for TelemFFB effects.
"""
from tests.framework.base import (
    MockHapticEffect,
    MockFFBDevice,
    MockSimConnect,
    MockEffectDispenser,
    BaseTelemetryEffectTestCase,
)
from tests.framework.utils import (
    TelemetryDataBuilder,
    assert_effect_started,
    assert_effect_stopped,
    assert_effect_not_modified,
    assert_friction_coefficient_in_range,
    assert_spring_coefficient_in_range,
)

__all__ = [
    "MockHapticEffect",
    "MockFFBDevice",
    "MockSimConnect",
    "MockEffectDispenser",
    "BaseTelemetryEffectTestCase",
    "TelemetryDataBuilder",
    "assert_effect_started",
    "assert_effect_stopped",
    "assert_effect_not_modified",
    "assert_friction_coefficient_in_range",
    "assert_spring_coefficient_in_range",
]
