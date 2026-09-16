"""
Regression tests for SettingsLayout.is_top_level_expanded cycle handling.

Background
----------
The settings hierarchy is implicit: it is re-derived on every render from each
setting's `prereq` string. `is_top_level_expanded` walks *up* that chain and, for
historical reasons, resolves a setting's parent with a substring match:

    parent = next((x for x in datalist if x['name'] in prereq), None)

That substring match becomes a landmine when a mid-chain parent is eliminated during
the settings merge. Concrete real-world trigger (Diamond DA40, joystick):

    telemffb_controls_axes = false
        -> eliminate_no_prereq() drops `enable_custom_x_axis` (its prereq is false)
        -> but its children `custom_x_axis` / `raw_x_axis_scale` are left orphaned
        -> `custom_x_axis` is itself a substring of its own prereq
           `enable_custom_x_axis`, and is now the only surviving match
        -> the row resolves as its OWN parent -> unbounded recursion -> RecursionError
           -> the whole settings form blows up and every setting disappears.

This is a latent bug (reproducible as far back as the 2025-10-29 build). The fix is a
`_visited` guard so a cyclic / dangling prereq degrades to "not expanded" (the row is
simply hidden) instead of crashing the form. These tests lock that in and also confirm
the guard does not change resolution for valid chains.
"""

import types

import pytest

pytest.importorskip("PyQt6")


def _make_layout():
    """Build a SettingsLayout without running __init__ (which needs Qt parents)."""
    from telemffb.SettingsLayout import SettingsLayout
    obj = SettingsLayout.__new__(SettingsLayout)
    obj.expanded_items = set()
    return obj


def test_orphaned_self_substring_prereq_does_not_recurse():
    """The exact DA40 trigger: an orphan whose name is a substring of its own prereq."""
    layout = _make_layout()
    datalist = [
        {'name': 'telemffb_controls_axes', 'prereq': 'basic_group', 'order': '2100'},
        {'name': 'custom_x_axis', 'prereq': 'enable_custom_x_axis', 'order': '2200.2'},
        {'name': 'raw_x_axis_scale', 'prereq': 'enable_custom_x_axis', 'order': '2200.3'},
    ]
    orphan = datalist[1]
    # Must not raise RecursionError; an orphan is not considered "expanded".
    assert layout.is_top_level_expanded(orphan, datalist) is False
    assert layout.is_top_level_expanded(datalist[2], datalist) is False


def test_direct_two_node_cycle_terminates():
    """A -> B -> A prereq cycle must terminate rather than recurse forever."""
    layout = _make_layout()
    datalist = [
        {'name': 'alpha', 'prereq': 'beta', 'order': '10.2'},
        {'name': 'beta', 'prereq': 'alpha', 'order': '10.3'},
    ]
    assert layout.is_top_level_expanded(datalist[0], datalist) is False
    assert layout.is_top_level_expanded(datalist[1], datalist) is False


def test_valid_group_chain_still_resolves_true():
    """A child under a top-level group (order '*.0', no prereq) resolves expanded."""
    layout = _make_layout()
    datalist = [
        {'name': 'group1', 'prereq': '', 'order': '100.0'},
        {'name': 'child', 'prereq': 'group1', 'order': '100.2'},
    ]
    assert layout.is_top_level_expanded(datalist[1], datalist) is True


def test_valid_chain_honors_expanded_items():
    """A non-group top-level parent is expanded only when in self.expanded_items."""
    layout = _make_layout()
    datalist = [
        {'name': 'topswitch', 'prereq': '', 'order': '300'},
        {'name': 'child', 'prereq': 'topswitch', 'order': '300.2'},
    ]
    assert layout.is_top_level_expanded(datalist[1], datalist) is False
    layout.expanded_items.add('topswitch')
    assert layout.is_top_level_expanded(datalist[1], datalist) is True


def test_broken_prereq_link_returns_false():
    """A prereq pointing at a non-existent, non-self-matching name is not expanded."""
    layout = _make_layout()
    datalist = [
        {'name': 'child', 'prereq': 'does_not_exist_anywhere', 'order': '400.2'},
    ]
    assert layout.is_top_level_expanded(datalist[0], datalist) is False
