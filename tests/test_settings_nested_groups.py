"""Nested collapsible groups in the settings form.

A `datatype=group` row with no prereq is a top-level section header
(Aerodynamics, Inertial, ...): pinned to column 0, its children start at
indent 0.  A group that carries a prereq of its own is a *nested*
sub-header: it renders as an ordinary indented row with the collapse
arrow baked into its label, and its children indent one level further.
That lets a set of related settings collapse together without inventing a
dummy bool toggle to hang them under.

The hierarchy here is implicit - re-derived from `prereq` strings and
`order` suffixes on every render - so these tests pin the pipeline
behavior (visibility gating + indent) and the real defaults.xml wiring of
the first nested group, whose order strings must dodge the two magic
suffix patterns ('.0' = locked-open header, trailing '1' = bump-up row).
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

pytestmark = [pytest.mark.unit]

DEFAULTS_XML = Path(__file__).resolve().parent.parent / "defaults.xml"


def _make_layout():
    """Build a SettingsLayout without running __init__ (which needs Qt parents)."""
    from telemffb.SettingsLayout import SettingsLayout
    obj = SettingsLayout.__new__(SettingsLayout)
    obj.expanded_items = []      # instance attr shadows the class-level list
    return obj


def _run_pipeline(layout, datalist):
    """The render-time passes that resolve visibility and indentation."""
    layout.prereq_list = layout.read_active_prereqs(datalist)
    layout.has_bump(datalist)
    layout.append_prereq_count(datalist)
    layout.add_expanded(datalist)
    layout.is_visible(datalist)
    layout.get_parent_indent(datalist)


def _tree(mode_value='DINPUT_TAP'):
    """basic_group > spring_mode > [nested group] > two toggles."""
    return [
        {'name': 'basic_group', 'prereq': '', 'order': '50.0',
         'value': 'true', 'datatype': 'group'},
        {'name': 'spring_mode', 'prereq': 'basic_group', 'order': '700.0',
         'value': mode_value, 'datatype': 'enumlist'},
        {'name': 'tap_axis_group', 'prereq': 'spring_mode.DINPUT_TAP',
         'order': '700.2', 'value': 'true', 'datatype': 'group'},
        {'name': 'tap_spring_swap_axes', 'prereq': 'tap_axis_group',
         'order': '700.22', 'value': 'false', 'datatype': 'bool'},
        {'name': 'tap_spring_invert_x', 'prereq': 'tap_axis_group',
         'order': '700.23', 'value': 'false', 'datatype': 'bool'},
    ]


def _by_name(datalist):
    return {item['name']: item for item in datalist}


class TestNestedGroupVisibility:
    def test_collapsed_group_hides_children(self):
        layout = _make_layout()
        data = _tree()
        _run_pipeline(layout, data)
        items = _by_name(data)
        assert items['tap_axis_group']['is_visible'] == 'true'
        # the group is the collapse control: it must offer an expander
        assert items['tap_axis_group']['has_expander'] == 'true'
        assert items['tap_spring_swap_axes']['is_visible'] == 'false'
        assert items['tap_spring_invert_x']['is_visible'] == 'false'

    def test_expanded_group_reveals_children(self):
        layout = _make_layout()
        layout.expanded_items.append('tap_axis_group')
        data = _tree()
        _run_pipeline(layout, data)
        items = _by_name(data)
        assert items['tap_spring_swap_axes']['is_visible'] == 'true'
        assert items['tap_spring_invert_x']['is_visible'] == 'true'

    def test_group_and_children_hidden_when_mode_differs(self):
        """The whole subtree disappears when the parent's value no longer
        satisfies the group's value-qualified prereq."""
        layout = _make_layout()
        layout.expanded_items.append('tap_axis_group')   # even when expanded
        data = _tree(mode_value='NONE')
        _run_pipeline(layout, data)
        items = _by_name(data)
        assert items['tap_axis_group']['is_visible'] == 'false'
        assert items['tap_spring_swap_axes']['is_visible'] == 'false'
        assert items['tap_spring_invert_x']['is_visible'] == 'false'


class TestNestedGroupIndent:
    def test_children_indent_one_level_under_nested_group(self):
        layout = _make_layout()
        data = _tree()
        _run_pipeline(layout, data)
        items = _by_name(data)
        # value-qualified prereq resolves to no indent step (existing
        # behavior for every spring_mode.<VALUE> child)
        assert items['tap_axis_group']['indent'] == 0
        assert items['tap_spring_swap_axes']['indent'] == 1
        assert items['tap_spring_invert_x']['indent'] == 1

    def test_top_level_group_children_stay_flush(self):
        """Regression: a top-level section header still contributes no
        indent, so ordinary settings keep rendering where they always did."""
        layout = _make_layout()
        data = [
            {'name': 'airflow_group', 'prereq': '', 'order': '10000',
             'value': 'true', 'datatype': 'group'},
            {'name': 'some_setting', 'prereq': 'airflow_group',
             'order': '10100', 'value': 'true', 'datatype': 'bool'},
            {'name': 'some_child', 'prereq': 'some_setting',
             'order': '10100.2', 'value': 'false', 'datatype': 'bool'},
        ]
        _run_pipeline(layout, data)
        items = _by_name(data)
        assert items['some_setting']['indent'] == 0
        assert items['some_child']['indent'] == 1


class TestDefaultsXmlNestedGroups:
    """Convention check over every nested group defined in defaults.xml.

    These are easy to get subtly wrong - a stray '.0' or a trailing '1' in
    an order string silently changes the row's meaning - so the rules are
    asserted for whatever nested groups exist rather than for one by name.
    Passes vacuously when there are none.
    """

    @pytest.fixture(scope='class')
    def parsed(self):
        root = ET.parse(DEFAULTS_XML).getroot()
        entries = [d for d in root.findall('defaults') if d.findtext('name')]
        nested = [d for d in entries
                  if d.findtext('datatype') == 'group' and d.findtext('prereq')]
        return entries, nested

    def test_nested_groups_are_true_valued(self, parsed):
        """eliminate_no_prereq keeps children only under a true-valued
        parent, and is_visible tests the same field before revealing them."""
        _, nested = parsed
        for g in nested:
            assert g.findtext('value') == 'true', g.findtext('name')

    def test_nested_group_orders_avoid_magic_suffixes(self, parsed):
        _, nested = parsed
        for g in nested:
            name, order = g.findtext('name'), g.findtext('order')
            assert '.0' not in order, f"{name}: '.0' marks a locked-open group"
            assert not order.endswith('1'), f"{name}: trailing 1 bumps the row up"

    def test_nested_group_children_are_well_formed(self, parsed):
        """Children must sort after their group and dodge the same two
        magic suffixes, or they render detached from it."""
        entries, nested = parsed
        for g in nested:
            gname = g.findtext('name')
            gorder = float(g.findtext('order'))
            children = [e for e in entries if e.findtext('prereq') == gname]
            assert children, f"{gname}: nested group with no children is hidden"
            for c in children:
                cname, corder = c.findtext('name'), c.findtext('order')
                assert '.0' not in corder, f"{cname}: '.0' makes it a group container"
                assert not corder.endswith('1'), f"{cname}: trailing 1 bumps the row up"
                assert float(corder) > gorder, f"{cname}: sorts above its group"

    def test_nested_group_names_do_not_collide(self, parsed):
        """Parent lookup is a substring match, so a name contained in (or
        containing) another setting's name resolves to the wrong row."""
        entries, nested = parsed
        names = {e.findtext('name') for e in entries}
        for g in nested:
            gname = g.findtext('name')
            for other in names:
                if other == gname:
                    continue
                assert other not in gname, f"{other} is a substring of {gname}"
                assert gname not in other, f"{gname} is a substring of {other}"
