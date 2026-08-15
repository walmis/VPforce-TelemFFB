"""Unit-selector auto-conversion: the canonical convert_between_units factors
and the settings-layout formatting wrapper (value converts when the user picks
a different unit in the dropdown, preserving the effective setting)."""
import pytest

from telemffb.utils import convert_between_units

pytestmark = [pytest.mark.unit]


class TestConvertBetweenUnits:
    def test_speed_family_round_trips(self):
        assert convert_between_units(7.0, "m/s", "kt") == pytest.approx(13.6, abs=0.05)
        assert convert_between_units(39.0, "kt", "m/s") == pytest.approx(20.06, abs=0.01)
        assert convert_between_units(100.0, "mph", "kt") == pytest.approx(86.9, abs=0.1)
        assert convert_between_units(100.0, "kph", "m/s") == pytest.approx(27.78, abs=0.01)
        # round trip returns the original value
        there = convert_between_units(123.0, "m/s", "kt")
        assert convert_between_units(there, "kt", "m/s") == pytest.approx(123.0, abs=1e-9)

    def test_vertical_speed_fpm(self):
        assert convert_between_units(500.0, "fpm", "m/s") == pytest.approx(2.54, abs=0.001)
        assert convert_between_units(2.54, "m/s", "fpm") == pytest.approx(500.0, abs=0.1)

    def test_length_family(self):
        assert convert_between_units(84.0, "in", "m") == pytest.approx(2.1336, abs=1e-4)
        assert convert_between_units(8.8, "ft", "m") == pytest.approx(2.682, abs=1e-3)
        assert convert_between_units(2.03, "m", "ft") == pytest.approx(6.66, abs=0.01)
        assert convert_between_units(1.88, "m", "in") == pytest.approx(74.0, abs=0.1)

    def test_unknown_units_return_none(self):
        assert convert_between_units(10.0, "furlongs", "kt") is None
        assert convert_between_units(10.0, "kt", "") is None
        assert convert_between_units(10.0, "", "") is None

    def test_same_unit_is_identity(self):
        assert convert_between_units(42.0, "kt", "kt") == pytest.approx(42.0)


class TestSettingsLayoutWrapper:
    """convert_unit_value is a staticmethod — exercised without a Qt layout."""

    @staticmethod
    def _convert(*args):
        from telemffb.SettingsLayout import SettingsLayout
        return SettingsLayout.convert_unit_value(*args)

    def test_tenths_precision_with_stable_round_trips(self):
        # one decimal: matches telemetry fidelity and keeps unit flips stable
        assert self._convert("7", "m/s", "kt") == "13.6"
        assert self._convert("13.6", "kt", "m/s") == "7"     # 6.996 -> "7.0" -> "7"
        assert self._convert("80", "m/s", "kt") == "155.5"
        assert self._convert("84", "in", "m") == "2.1"
        assert self._convert("2.1", "m", "in") == "82.7"

    def test_trailing_zeros_trimmed(self):
        assert self._convert("2", "m", "ft") == "6.6"
        # whole results don't carry decimal baggage
        assert self._convert("0", "m/s", "kt") == "0"

    def test_unconvertible_input_returns_original(self):
        assert self._convert("abc", "m/s", "kt") == "abc"
        assert self._convert("10", "bogus", "kt") == "10"
