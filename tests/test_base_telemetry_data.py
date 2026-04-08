"""Tests for BaseTelemetryData dict-compatible container."""
import json
import pytest
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class TestBaseTelemetryDataInit:
    def test_default_init(self):
        td = BaseTelemetryData()
        assert td.FFBType == 'joystick'
        assert '_timestamp' in td._data

    def test_init_with_dict(self):
        td = BaseTelemetryData({'src': 'DCS', 'N': 'F-16C', 'TAS': 250.0})
        assert td['src'] == 'DCS'
        assert td['N'] == 'F-16C'
        assert td['TAS'] == 250.0

    def test_init_overrides_defaults(self):
        td = BaseTelemetryData({'FFBType': 'pedals'})
        assert td.FFBType == 'pedals'


class TestBracketAccess:
    def test_setitem_getitem(self):
        td = BaseTelemetryData()
        td['TAS'] = 300.5
        assert td['TAS'] == 300.5

    def test_getitem_missing_returns_none(self):
        td = BaseTelemetryData()
        assert td['nonexistent'] is None

    def test_delitem(self):
        td = BaseTelemetryData()
        td['TAS'] = 100.0
        del td['TAS']
        assert td['TAS'] is None

    def test_delitem_missing_raises(self):
        td = BaseTelemetryData()
        with pytest.raises(KeyError):
            del td['nonexistent']

    def test_delattr(self):
        td = BaseTelemetryData()
        td.TAS = 100.0
        del td.TAS
        assert td.TAS is None

    def test_delattr_missing_raises(self):
        td = BaseTelemetryData()
        with pytest.raises(AttributeError):
            del td.nonexistent

    def test_contains_set_key(self):
        td = BaseTelemetryData()
        td['TAS'] = 100.0
        assert 'TAS' in td

    def test_contains_unset_key(self):
        td = BaseTelemetryData()
        assert 'nonexistent' not in td


class TestDictMethods:
    def test_get_with_default(self):
        td = BaseTelemetryData()
        assert td.get('TAS', 0) == 0

    def test_get_set_value(self):
        td = BaseTelemetryData()
        td['TAS'] = 250.0
        assert td.get('TAS', 0) == 250.0

    def test_pop(self):
        td = BaseTelemetryData()
        td['TAS'] = 100.0
        val = td.pop('TAS')
        assert val == 100.0
        assert td['TAS'] is None

    def test_pop_with_default(self):
        td = BaseTelemetryData()
        val = td.pop('missing', 42)
        assert val == 42

    def test_setdefault_missing(self):
        td = BaseTelemetryData()
        val = td.setdefault('TAS', 300.0)
        assert val == 300.0
        assert td['TAS'] == 300.0

    def test_setdefault_existing(self):
        td = BaseTelemetryData()
        td['TAS'] = 100.0
        val = td.setdefault('TAS', 300.0)
        assert val == 100.0

    def test_keys(self):
        td = BaseTelemetryData({'src': 'DCS', 'TAS': 100.0})
        keys = set(td.keys())
        assert 'src' in keys
        assert 'TAS' in keys

    def test_items(self):
        td = BaseTelemetryData({'src': 'DCS'})
        items = dict(td.items())
        assert items['src'] == 'DCS'

    def test_values(self):
        td = BaseTelemetryData({'src': 'DCS'})
        vals = list(td.values())
        assert 'DCS' in vals

    def test_len(self):
        td = BaseTelemetryData()
        initial_len = len(td)
        td['TAS'] = 100.0
        assert len(td) == initial_len + 1

    def test_iter(self):
        td = BaseTelemetryData({'src': 'DCS', 'N': 'F-16C'})
        keys = list(td)
        assert 'src' in keys
        assert 'N' in keys

    def test_bool_empty(self):
        td = BaseTelemetryData()
        # has _timestamp and FFBType by default
        assert bool(td) is True

    def test_clear(self):
        td = BaseTelemetryData({'src': 'DCS', 'TAS': 100.0})
        td.clear()
        assert len(td) == 0


class TestAttributeAccess:
    def test_getattr_annotated_unset(self):
        td = BaseTelemetryData()
        assert td.TAS is None
        assert td.N is None

    def test_getattr_annotated_set(self):
        td = BaseTelemetryData()
        td['TAS'] = 250.0
        assert td.TAS == 250.0

    def test_setattr_annotated(self):
        td = BaseTelemetryData()
        td.TAS = 300.0
        assert td['TAS'] == 300.0
        assert td.TAS == 300.0

    def test_setattr_unknown_goes_to_data(self):
        td = BaseTelemetryData()
        td.custom_key = 42
        assert td['custom_key'] == 42
        assert td._data['custom_key'] == 42

    def test_getattr_unknown_raises(self):
        td = BaseTelemetryData()
        with pytest.raises(AttributeError):
            _ = td.totally_nonexistent_field


class TestUpdateAndCopy:
    def test_update_from_dict(self):
        td = BaseTelemetryData()
        td.update({'TAS': 100.0, 'IAS': 80.0})
        assert td['TAS'] == 100.0
        assert td['IAS'] == 80.0

    def test_update_from_basetelemdata(self):
        td1 = BaseTelemetryData({'TAS': 100.0})
        td2 = BaseTelemetryData({'IAS': 80.0, 'N': 'Su-25'})
        td1.update(td2)
        assert td1['IAS'] == 80.0
        assert td1['N'] == 'Su-25'
        assert td1['TAS'] == 100.0

    def test_update_with_kwargs(self):
        td = BaseTelemetryData()
        td.update({}, TAS=100.0)
        assert td['TAS'] == 100.0

    def test_copy_returns_independent_instance(self):
        td = BaseTelemetryData({'TAS': 100.0, 'src': 'DCS'})
        td2 = td.copy()
        assert isinstance(td2, BaseTelemetryData)
        assert td2['TAS'] == 100.0
        td2['TAS'] = 999.0
        assert td['TAS'] == 100.0  # original unchanged

    def test_to_dict(self):
        td = BaseTelemetryData({'src': 'DCS', 'TAS': 100.0})
        d = td.to_dict()
        assert isinstance(d, dict)
        assert d['src'] == 'DCS'

    def test_from_dict(self):
        td = BaseTelemetryData.from_dict({'src': 'DCS', 'TAS': 100.0})
        assert isinstance(td, BaseTelemetryData)
        assert td['src'] == 'DCS'


class TestRepr:
    def test_repr_basic(self):
        td = BaseTelemetryData({'src': 'DCS', 'N': 'F-16C'})
        r = repr(td)
        assert 'DCS' in r
        assert 'F-16C' in r

    def test_repr_empty(self):
        td = BaseTelemetryData()
        r = repr(td)
        assert 'BaseTelemetryData' in r


class TestJsonSerialization:
    def test_to_dict_json_roundtrip(self):
        td = BaseTelemetryData({'src': 'DCS', 'TAS': 250.5, 'AccBody': [1.0, 2.0, 3.0]})
        j = json.dumps(td.to_dict())
        restored = json.loads(j)
        assert restored['src'] == 'DCS'
        assert restored['TAS'] == 250.5
        assert restored['AccBody'] == [1.0, 2.0, 3.0]


class TestDictCompatibility:
    """Verify patterns used in actual codebase work correctly."""

    def test_pattern_get_with_default(self):
        td = BaseTelemetryData({'TAS': 250.0})
        tas = td.get("TAS", 0)
        assert tas == 250.0
        ias = td.get("IAS", 0)
        assert ias == 0

    def test_pattern_bracket_write_read(self):
        td = BaseTelemetryData()
        td["ForceXY"] = [0.5, -0.3]
        assert td["ForceXY"] == [0.5, -0.3]

    def test_pattern_type_check_on_value(self):
        td = BaseTelemetryData({'RPM': [8000, 7500]})
        rpm = td.get("RPM", 0)
        if type(rpm) == list:
            rpm = max(rpm)
        assert rpm == 8000

    def test_pattern_update_merge(self):
        td = BaseTelemetryData({'src': 'DCS'})
        ipc_data = {'_pct_max_e': 0.8, '_pct_max_a': 0.6}
        td.update(ipc_data)
        assert td['_pct_max_e'] == 0.8

    def test_pattern_copy_for_last_frame(self):
        td = BaseTelemetryData({'TAS': 100.0, 'G': 1.2})
        last = td.copy()
        td['TAS'] = 200.0
        assert last['TAS'] == 100.0
        assert td['TAS'] == 200.0

    def test_pattern_del_mechinfo(self):
        td = BaseTelemetryData({'MechInfo': '{"damage": 0.5}'})
        del td['MechInfo']
        assert td['MechInfo'] is None

    def test_pattern_dynamic_key_write(self):
        td = BaseTelemetryData()
        for i in range(2):
            td[f"eng{i}_rpm"] = 8000 + i * 100
        assert td["eng0_rpm"] == 8000
        assert td["eng1_rpm"] == 8100

    def test_pattern_contains_check(self):
        td = BaseTelemetryData({'MechInfo': '{"data": true}'})
        assert 'MechInfo' in td
        assert 'Nonexistent' not in td
