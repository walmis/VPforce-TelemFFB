"""Characterization tests for ConfigResolver._apply_validvalue_overrides.

The method mutates each item's ``validvalues`` in place when a matching
``<validvalues_overrides>`` row exists in defaults.xml. It is the dominant cost
inside ``resolve()`` (the per-item loop re-ran a full-tree findall every
iteration), so the behavior here is locked down before that loop is hoisted out.

The resolver only touches ``store.device`` and ``store.defaults_root`` for this
method, so a lightweight stand-in store is sufficient - no files, no winreg.
"""
import xml.etree.ElementTree as ET

import pytest

from telemffb.xml.read import ConfigResolver


class _Store:
    def __init__(self, device, defaults_root, user_root=None):
        self.device = device
        self.defaults_root = defaults_root
        self.user_root = user_root


def _override(name, sim, cls, device, validvalues=None):
    e = ET.Element('validvalues_overrides')
    ET.SubElement(e, 'name').text = name
    ET.SubElement(e, 'sim').text = sim
    ET.SubElement(e, 'class').text = cls
    ET.SubElement(e, 'device').text = device
    if validvalues is not None:
        ET.SubElement(e, 'validvalues').text = validvalues
    return e


def _root(*overrides):
    r = ET.Element('TelemFFB')
    for o in overrides:
        r.append(o)
    return r


def _resolver(root, device='joystick'):
    return ConfigResolver(_Store(device, root))


def _item(name, validvalues='original'):
    return {'name': name, 'validvalues': validvalues}


class TestApplyValidvalueOverrides:

    def test_matching_exact_device_override_applies(self):
        root = _root(_override('spring_mode', 'DCS', 'Jet', 'joystick', 'V1'))
        items = [_item('spring_mode')]
        _resolver(root, 'joystick')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'joystick')
        assert items[0]['validvalues'] == 'V1'

    def test_any_device_override_applies_to_other_devices(self):
        # override declares device="any" -> applies regardless of item device
        root = _root(_override('spring_mode', 'DCS', 'Jet', 'any', 'V_ANY'))
        items = [_item('spring_mode')]
        _resolver(root, 'pedals')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'pedals')
        assert items[0]['validvalues'] == 'V_ANY'

    def test_non_matching_sim_leaves_value(self):
        root = _root(_override('spring_mode', 'MSFS', 'Jet', 'joystick', 'V'))
        items = [_item('spring_mode')]
        _resolver(root, 'joystick')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'joystick')
        assert items[0]['validvalues'] == 'original'

    def test_non_matching_class_leaves_value(self):
        root = _root(_override('spring_mode', 'DCS', 'Glider', 'joystick', 'V'))
        items = [_item('spring_mode')]
        _resolver(root, 'joystick')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'joystick')
        assert items[0]['validvalues'] == 'original'

    def test_non_matching_name_leaves_value(self):
        root = _root(_override('other_setting', 'DCS', 'Jet', 'joystick', 'V'))
        items = [_item('spring_mode')]
        _resolver(root, 'joystick')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'joystick')
        assert items[0]['validvalues'] == 'original'

    def test_last_matching_override_in_document_order_wins(self):
        # both match (same name/sim/class/device); the later one in the tree
        # overwrites the earlier assignment
        root = _root(
            _override('spring_mode', 'DCS', 'Jet', 'joystick', 'FIRST'),
            _override('spring_mode', 'DCS', 'Jet', 'joystick', 'SECOND'),
        )
        items = [_item('spring_mode')]
        _resolver(root, 'joystick')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'joystick')
        assert items[0]['validvalues'] == 'SECOND'

    def test_missing_validvalues_child_yields_empty_string(self):
        root = _root(_override('spring_mode', 'DCS', 'Jet', 'joystick', validvalues=None))
        items = [_item('spring_mode')]
        _resolver(root, 'joystick')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'joystick')
        assert items[0]['validvalues'] == ''

    def test_multiple_items_each_match_independently(self):
        root = _root(
            _override('a', 'DCS', 'Jet', 'joystick', 'VA'),
            _override('b', 'DCS', 'Jet', 'joystick', 'VB'),
        )
        items = [_item('a'), _item('b'), _item('c')]
        _resolver(root, 'joystick')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'joystick')
        assert items[0]['validvalues'] == 'VA'
        assert items[1]['validvalues'] == 'VB'
        assert items[2]['validvalues'] == 'original'

    def test_none_root_is_a_noop(self):
        items = [_item('spring_mode')]
        _resolver(None, 'joystick')._apply_validvalue_overrides(items, 'DCS', 'Jet', 'joystick')
        assert items[0]['validvalues'] == 'original'
