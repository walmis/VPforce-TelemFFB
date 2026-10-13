"""Unit tests for telemffb/telem/telemetry_units.py - the Monitor tab's
telemetry unit registry, SimConnect unit names and display conversions."""
import typing
from types import SimpleNamespace

import pytest

import telemffb.globals as G
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.telem.SimConnectManager import SimConnectManager, SimVarArray
from telemffb.telem.telemetry_units import (PCT_OVER_100, SIM_UNIT_OVERRIDES,
                                            SIMCONNECT_UNIT_ALIASES,
                                            TELEMETRY_UNITS, UNIT_FAMILIES,
                                            conversions_for, convert,
                                            normalize_simconnect_unit,
                                            unit_for)

pytestmark = pytest.mark.unit


def _float_fields():
    """BaseTelemetryData's annotations that are, or may be, floats."""
    hints = BaseTelemetryData.__annotations__
    return {name for name, hint in hints.items() if 'float' in str(hint)}


def _known_units():
    units = {u for family in UNIT_FAMILIES.values() for u in family}
    units.update(u for u in SIMCONNECT_UNIT_ALIASES.values() if u)
    return units


class TestRegistryDrift:
    def test_every_float_field_has_a_registry_entry(self):
        missing = _float_fields() - set(TELEMETRY_UNITS)
        assert not missing, f"add these BaseTelemetryData fields to TELEMETRY_UNITS: {sorted(missing)}"

    def test_every_registry_key_is_a_known_field(self):
        unknown = set(TELEMETRY_UNITS) - set(BaseTelemetryData.__annotations__)
        assert not unknown, f"TELEMETRY_UNITS names fields BaseTelemetryData lacks: {sorted(unknown)}"

    def test_registry_units_are_known_tokens(self):
        known = _known_units()
        stray = {k: u for k, u in TELEMETRY_UNITS.items() if u is not None and u not in known}
        stray.update({f"{src}:{k}": u for src, keys in SIM_UNIT_OVERRIDES.items()
                      for k, u in keys.items() if u is not None and u not in known})
        assert not stray

    def test_every_predefined_simvar_unit_is_mapped(self):
        """A SimVar added with a unit string this module has never heard of
        would show that string raw and offer no conversions."""
        unmapped = set()
        for sv in SimConnectManager.sim_vars:
            for var in (sv.vars if isinstance(sv, SimVarArray) else [sv]):
                if var.sc_unit.strip().lower() not in SIMCONNECT_UNIT_ALIASES:
                    unmapped.add(var.sc_unit)
        assert not unmapped


class TestNormalize:
    @pytest.mark.parametrize("sc_unit, token", [
        ("meter/second", "m/s"), ("Knots", "kt"), ("degrees", "deg"),
        ("Percent Over 100", PCT_OVER_100), ("percent", "%"), ("pascal", "Pa"),
        ("feet per second squared", "ft/s²"), ("Bool", None), ("Number", None),
        ("", None),
    ])
    def test_known_strings(self, sc_unit, token):
        assert normalize_simconnect_unit(sc_unit) == token

    def test_an_unknown_string_is_kept_verbatim(self):
        assert normalize_simconnect_unit(" furlongs ") == "furlongs"


class TestConversions:
    def test_a_family_offers_all_its_units(self):
        assert conversions_for("m/s") == list(UNIT_FAMILIES["speed"])

    @pytest.mark.parametrize("unit", [None, "", "rpm", "°C", "furlongs"])
    def test_nothing_to_offer(self, unit):
        assert conversions_for(unit) == []

    def test_known_factors(self):
        assert convert(1.0, "kt", "m/s") == pytest.approx(0.514444)
        assert convert(1.0, "m", "ft") == pytest.approx(3.28084, rel=1e-5)
        assert convert(180.0, "deg", "rad") == pytest.approx(3.14159265, rel=1e-6)
        assert convert(0.25, PCT_OVER_100, "%") == pytest.approx(25.0)
        assert convert(1.0, "m/s", "fpm") == pytest.approx(196.8504, rel=1e-5)

    @pytest.mark.parametrize("family", list(UNIT_FAMILIES))
    def test_round_trip_within_each_family(self, family):
        units = list(UNIT_FAMILIES[family])
        for unit in units[1:]:
            assert convert(convert(12.5, units[0], unit), unit, units[0]) == pytest.approx(12.5)

    def test_lists_convert_numbers_and_pass_the_rest(self):
        assert convert([1.0, None, 2, True], PCT_OVER_100, "%") == [100.0, None, 200.0, True]

    def test_across_families_is_left_alone(self):
        assert convert(5.0, "m/s", "deg") == 5.0

    def test_strings_are_left_alone(self):
        assert convert("12.3 ms", "s", "ms") == "12.3 ms"


class _FakeSimConnect:
    def __init__(self, units):
        self.units = units

    def get_var_unit(self, key):
        return self.units.get(key)


@pytest.fixture
def simconnect(monkeypatch):
    def install(units):
        monkeypatch.setattr(G, 'telem_manager',
                            SimpleNamespace(simconnect=_FakeSimConnect(units)), raising=False)
    return install


class TestUnitFor:
    def test_registry_for_a_sim_without_live_units(self):
        assert unit_for("IAS", {"src": "DCS"}) == "m/s"

    def test_msfs_subscription_beats_the_registry(self, simconnect):
        simconnect({"IAS": ("knots", None)})
        assert unit_for("IAS", {"src": "MSFS"}) == "kt"

    def test_only_msfs_consults_simconnect(self, simconnect):
        simconnect({"IAS": ("knots", None)})
        assert unit_for("IAS", {"src": "XPLANE"}) == "m/s"

    def test_a_scaled_simvar_falls_back_to_the_registry(self, simconnect):
        simconnect({"AccBody": ("feet per second squared", 0.031081)})
        assert unit_for("AccBody", {"src": "MSFS"}) == "g"

    def test_a_unitless_subscription_falls_back_to_the_registry(self, simconnect):
        simconnect({"ffbTrimPedals": ("number", None)})
        assert unit_for("ffbTrimPedals", {"src": "MSFS"}) == PCT_OVER_100

    def test_a_key_only_simconnect_knows(self, simconnect):
        simconnect({"MyLvar": ("feet", None)})
        assert unit_for("MyLvar", {"src": "MSFS"}) == "ft"

    def test_simconnect_without_the_lookup(self, monkeypatch):
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=None), raising=False)
        assert unit_for("IAS", {"src": "MSFS"}) == "m/s"

    def test_per_sim_override(self):
        assert unit_for("T", {"src": "IL2"}) is None
        assert unit_for("T", {"src": "DCS"}) == "s"

    def test_unknown_key(self):
        assert unit_for("nope", {}) is None
