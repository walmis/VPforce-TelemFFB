"""Unit tests for ``TelemManager.get_aircraft_config`` value+unit handling.

Regression test for TASK004 (the smitty "no FFB in MSFS" post-mortem).

Root cause: a setting whose resolved value is *empty* (e.g. the shipped default
for ``vne_override`` -- ``<unit>kt</unit>`` with no ``<value>`` element) was
concatenated with its unit into a bare unit string (``'' + 'kt' == 'kt'``).
``utils.to_number('kt')`` cannot parse that and returns it verbatim, so the
string ``'kt'`` reached aircraft code and crashed downstream arithmetic
(``vne * ms2kt`` in ``MsfsXpFlightControlsMixIn._calculate_vne_and_gains``)
every telemetry frame, killing all F FB on both devices.

The fix (TelemManager.get_aircraft_config) only attaches a unit when there is a
value to attach it to, so an empty value stays empty (falsy) and the documented
"leave blank to use sim data" behaviour actually works. These tests pin that
contract and guard the normal numeric / no-unit / None cases.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
import telemffb.xmlutils as xmlutils
from telemffb.telem.TelemManager import TelemManager

pytestmark = [
    pytest.mark.unit,
    # importing TelemManager pulls in the simconnect package, which leaks an
    # open FileIO on its scvars.json at interpreter teardown - not ours
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

KT2MS = 0.514444  # mirror of telemffb.util.conversions.kt2ms


@pytest.fixture
def get_config(monkeypatch):
    """Drive get_aircraft_config with a hand-built resolved settings list.

    Returns a callable: ``get_config(rows)`` where each row is a
    (name, value, unit) tuple -> returns the sanitized ``params`` dict.
    """
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    monkeypatch.setattr(xmlutils, 'get_pattern_by_sim_fullname',
                        lambda sim, full_name: '.*')
    monkeypatch.setattr(xmlutils, 'get_active_profile_for_model',
                        lambda sim, cls, model: None)
    monkeypatch.setattr(G, 'settings_mgr',
                        SimpleNamespace(update_state_vars=lambda **kw: None),
                        raising=False)

    def _run(rows, data_source='MSFS'):
        settings = [{'name': n, 'value': v, 'unit': u} for (n, v, u) in rows]

        def fake_read_single_model(the_sim, aircraft_name, input_modeltype='',
                                   instance_device='', active_profile=None):
            return 'Aircraft', '.*', list(settings)

        monkeypatch.setattr(xmlutils, 'read_single_model', fake_read_single_model)
        mgr = TelemManager.__new__(TelemManager)  # skip QObject __init__
        params, _ = mgr.get_aircraft_config('TestPlane', data_source)
        return params

    return _run


class TestUnitConcat:
    def test_empty_value_with_unit_stays_empty(self, get_config):
        """THE regression: empty value + <unit>kt</unit> must stay '' (not 'kt')."""
        params = get_config([('vne_override', '', 'kt')])
        assert params['vne_override'] == ''
        assert not params['vne_override']  # falsy -> falls through to sim data

    def test_numeric_value_with_unit_still_converts(self, get_config):
        """Normal case must be unchanged: '72' + 'kt' -> 72 kt in m/s."""
        params = get_config([('vne_override', '72', 'kt')])
        assert params['vne_override'] == pytest.approx(72 * KT2MS, abs=1e-3)

    def test_numeric_value_without_unit(self, get_config):
        """No unit: the raw number is passed straight through (int for whole value)."""
        params = get_config([('vne_override', '150', '')])
        assert params['vne_override'] == 150

    def test_float_value_with_unit(self, get_config):
        """A decimal value keeps its decimal (float path, not int). The m/s
        factor is 1.0, so the value is preserved as a float."""
        params = get_config([('vne_override', '37.5', 'm/s')])
        assert params['vne_override'] == pytest.approx(37.5)

    def test_none_value_becomes_zero(self, get_config):
        """None value normalises to 0 (pre-existing behaviour, must be preserved)."""
        params = get_config([('vne_override', None, '')])
        assert params['vne_override'] == 0

    def test_unsuffixed_settings_unaffected(self, get_config):
        """A boolean-ish / non-unit setting with an empty value stays empty."""
        params = get_config([('my_flag', '', '')])
        assert params['my_flag'] == ''


class TestMixedSettingsList:
    def test_multiple_settings_resolve_independently(self, get_config):
        """Empty and populated unit-suffixed settings coexist correctly."""
        params = get_config([
            ('vne_override', '', 'kt'),
            ('some_speed', '100', 'mph'),
            ('plain_number', '5', ''),
        ])
        assert params['vne_override'] == ''
        assert params['some_speed'] == pytest.approx(100 / 2.23693679, abs=1e-2)  # mph->m/s
        assert params['plain_number'] == 5
