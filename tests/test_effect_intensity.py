#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""Effect intensity: the number behind the Monitor page's bar column.

An effect being "started" says nothing about whether it is producing a
force - a runway rumble is started for as long as the aircraft is on the
ground, at whatever magnitude the surface calls for, including none. These
tests cover the magnitude being retained at the write, and the deliberate
refusal to produce one for condition effects.
"""
import gc
from unittest.mock import Mock

import pytest

from telemffb.hw.ffb_rhino import (EFFECT_CONSTANT, EFFECT_DAMPER,
                                   EFFECT_SINE, EFFECT_SPRING, EFFECT_SQUARE,
                                   FFBEffectHandle, HapticEffect)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _free_effects_deterministically():
    """Free every effect this module builds before the next test starts.

    FFBEffectHandle registers a ``weakref.finalize`` that calls destroy(),
    HapticEffect does the same from ``__del__``, and every HapticEffect adds
    itself to a class-level registry that other modules walk. Left to the
    garbage collector, those finalizers run at an arbitrary later moment -
    inside some unrelated test, against whatever device happens to be on the
    class by then - which is a good way to take a whole xdist worker down.

    Freeing them here, while this module's own mock devices are still the
    ones they hold, keeps that contained.
    """
    made.clear()
    yield
    for obj in made:
        if isinstance(obj, FFBEffectHandle):
            obj._finalizer.detach()
            obj.effect_id = None
        else:
            obj._h_effect = None
    made.clear()
    gc.collect()


#: Everything built during the current test, freed by the fixture above.
made = []


def make_handle(effect_type, effect_id=1):
    """A handle over a device that accepts writes and reports itself alive,
    so the magnitude path runs exactly as it does on hardware."""
    device = Mock()
    device.connected = True
    handle = FFBEffectHandle(device, effect_id, effect_type)
    handle._write = Mock()
    made.append(handle)
    return handle


def make_effect(effect_type):
    """A HapticEffect wrapping one of the handles above."""
    effect = HapticEffect()
    effect._h_effect = make_handle(effect_type)
    made.append(effect)
    return effect


class TestConstantForceIntensity:
    def test_the_written_magnitude_is_retained(self):
        handle = make_handle(EFFECT_CONSTANT)
        handle.setConstantForce(0.25, 90)
        assert handle.intensity == pytest.approx(0.25)

    def test_a_negative_magnitude_reads_as_its_strength(self):
        """Direction rides in its own field, so a -0.5 push is as strong as
        a +0.5 one - the bar is about how hard, not which way."""
        handle = make_handle(EFFECT_CONSTANT)
        handle.setConstantForce(-0.5, 270)
        assert handle.intensity == pytest.approx(0.5)

    def test_zero_is_zero_not_none(self):
        """A started effect commanding nothing is the case this column
        exists to expose; it must be distinguishable from 'no reading'."""
        handle = make_handle(EFFECT_CONSTANT)
        handle.setConstantForce(0.0, 0)
        assert handle.intensity == 0.0
        assert handle.intensity is not None

    def test_it_follows_the_latest_write(self):
        handle = make_handle(EFFECT_CONSTANT)
        handle.setConstantForce(0.9, 0)
        handle.setConstantForce(0.1, 0)
        assert handle.intensity == pytest.approx(0.1)

    def test_an_out_of_range_magnitude_reads_clamped(self):
        """setConstantForce clamps rather than asserting in the telemetry
        path, so the reading must match what was actually sent."""
        handle = make_handle(EFFECT_CONSTANT)
        handle.setConstantForce(2.5, 0)
        assert handle.intensity == pytest.approx(1.0)

    def test_nothing_written_yet_reads_none(self):
        assert make_handle(EFFECT_CONSTANT).intensity is None


class TestPeriodicIntensity:
    @pytest.mark.parametrize("effect_type", [EFFECT_SINE, EFFECT_SQUARE])
    def test_the_written_magnitude_is_retained(self, effect_type):
        handle = make_handle(effect_type)
        handle.setPeriodic(10, 0.75, 0)
        assert handle.intensity == pytest.approx(0.75)

    def test_zero_amplitude_reads_zero(self):
        handle = make_handle(EFFECT_SINE)
        handle.setPeriodic(10, 0.0, 0)
        assert handle.intensity == 0.0

    def test_it_follows_the_latest_write(self):
        handle = make_handle(EFFECT_SINE)
        handle.setPeriodic(10, 0.8, 0)
        handle.setPeriodic(10, 0.2, 0)
        assert handle.intensity == pytest.approx(0.2)


class TestConditionsHaveNoIntensity:
    """Spring and damper are parameterised by coefficients, and the force
    they produce depends on stick position or velocity. Reporting a number
    would be a guess dressed as a measurement."""

    @pytest.mark.parametrize("effect_type", [EFFECT_SPRING, EFFECT_DAMPER])
    def test_a_condition_reads_none(self, effect_type):
        assert make_handle(effect_type).intensity is None

    def test_a_condition_stays_none_even_if_a_magnitude_was_stashed(self):
        """The property gates on effect type, not on whether the field
        happens to hold something."""
        handle = make_handle(EFFECT_SPRING)
        handle._magnitude = 0.9
        assert handle.intensity is None


class TestHapticEffectDelegates:
    def test_it_reports_the_handles_intensity(self):
        effect = make_effect(EFFECT_CONSTANT)
        effect._h_effect.setConstantForce(0.33, 0)
        assert effect.intensity == pytest.approx(0.33)

    def test_an_unallocated_effect_reads_none(self):
        effect = HapticEffect()
        made.append(effect)
        assert effect.intensity is None


class TestPercentParsing:
    """The delegate reads the value back out of the cell text, so that the
    model stays a plain string table and a copied selection carries the
    readable percentage."""

    @pytest.mark.parametrize("text,expected", [
        ("0%", 0.0), ("42%", 0.42), ("100%", 1.0), (" 7% ", 0.07),
    ])
    def test_percentages_parse(self, text, expected):
        from telemffb.ui.widgets.IntensityBarDelegate import parse_percent
        assert parse_percent(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["-", "", None, "n/a", "abc%"])
    def test_non_percentages_yield_none(self, text):
        from telemffb.ui.widgets.IntensityBarDelegate import parse_percent
        assert parse_percent(text) is None

    def test_out_of_range_text_is_clamped(self):
        from telemffb.ui.widgets.IntensityBarDelegate import parse_percent
        assert parse_percent("150%") == 1.0
        assert parse_percent("-10%") == 0.0
