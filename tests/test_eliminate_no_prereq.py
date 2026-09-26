"""
Unit tests for xmlutils.eliminate_no_prereq cascade (fixed-point) filtering.

The settings hierarchy is implicit and re-derived from each item's `prereq` string.
`eliminate_no_prereq` prunes items whose prereq is not met. Historically it ran a
single pass, which under-pruned three-level chains: when a mid-chain parent was
removed because ITS parent was off (while the mid-chain node's own value was still
'true'), the grandchild was evaluated against the soon-to-be-removed parent and
wrongly kept -- leaving it orphaned in the merged result.

That orphan is what fed the SettingsLayout RecursionError (an orphan whose name is a
substring of its own prereq resolves as its own parent). These tests lock in the
fixed-point behavior at the data layer: an item survives only with an unbroken chain
of satisfied parents up to a root.
"""

from telemffb.xmlutils import eliminate_no_prereq


def _item(name, prereq='', value='true', order='100', **extra):
    return {'name': name, 'prereq': prereq, 'value': value, 'order': order, **extra}


def _names(result):
    return {d['name'] for d in result}


def test_three_level_orphan_is_pruned():
    """top=false must cascade-remove both the mid toggle AND its child."""
    data = [
        _item('top', prereq='', value='false', order='100'),
        _item('mid', prereq='top', value='true', order='100.2'),   # own value true, parent off
        _item('child', prereq='mid', value='true', order='100.3'),
    ]
    assert _names(eliminate_no_prereq(data)) == {'top'}


def test_three_level_chain_kept_when_root_true():
    """top=true keeps the whole satisfied chain."""
    data = [
        _item('top', prereq='', value='true', order='100'),
        _item('mid', prereq='top', value='true', order='100.2'),
        _item('child', prereq='mid', value='true', order='100.3'),
    ]
    assert _names(eliminate_no_prereq(data)) == {'top', 'mid', 'child'}


def test_two_level_parent_false_prunes_child():
    """Existing single-pass behavior is preserved for the direct case."""
    data = [
        _item('parent', prereq='', value='false', order='200'),
        _item('child', prereq='parent', value='true', order='200.2'),
    ]
    assert _names(eliminate_no_prereq(data)) == {'parent'}


def test_group_parent_keeps_child_regardless_of_group_value():
    """A top-level group (order contains '.0') satisfies its children."""
    data = [
        _item('grp', prereq='', value='false', order='300.0'),   # group value ignored
        _item('child', prereq='grp', value='true', order='300.2'),
    ]
    assert _names(eliminate_no_prereq(data)) == {'grp', 'child'}


def test_orphan_with_self_substring_prereq_is_pruned():
    """The exact DA40 shape: parent absent, orphan name is a substring of its prereq."""
    data = [
        # enable_custom_x_axis intentionally absent (already pruned upstream)
        _item('custom_x_axis', prereq='enable_custom_x_axis', value='AXIS_AILERONS_SET', order='2200.2'),
        _item('raw_x_axis_scale', prereq='enable_custom_x_axis', value='16384', order='2200.3'),
    ]
    assert eliminate_no_prereq(data) == []


def test_self_match_via_dotted_prereq_excluded():
    """A dotted prereq relaxes the exact-name check; a value='true' item must not
    keep itself alive by matching its own name as a substring of its own prereq."""
    data = [
        _item('foo', prereq='foo.BAR', value='true', order='400.2'),
    ]
    assert eliminate_no_prereq(data) == []


class TestVisibleRows:
    """What the settings form would show with every section open - the rows
    the MSFS toolbar panel is sent."""

    def _visible(self, data):
        from telemffb.xml.merge import visible_rows
        return _names(visible_rows(data))

    def _spring_tree(self, mode):
        return [
            _item('basic_group', order='50.0', datatype='group'),
            _item('spring_mode', prereq='basic_group', value=mode, order='200.0'),
            _item('aileron_spring_gain', prereq='spring_mode.CENTER.CNTR_FT', value='0.3', order='550.2'),
            _item('fbw_aileron_gain', prereq='spring_mode.FBW', value='0.6', order='600.2'),
            _item('cyclic_spring_gain', prereq='spring_mode.FORCETRIM', value='0.65', order='700.2'),
        ]

    def test_only_the_selected_modes_settings_show(self):
        """The H145 on Force Trim was shown Spring Center and Fly-By-Wire."""
        assert self._visible(self._spring_tree('FORCETRIM')) == {
            'basic_group', 'spring_mode', 'cyclic_spring_gain'}

    def test_a_setting_listed_for_several_modes_shows_in_each(self):
        assert 'aileron_spring_gain' in self._visible(self._spring_tree('CNTR_FT'))

    def test_a_child_of_a_hidden_parent_is_hidden(self):
        data = self._spring_tree('FBW') + [
            _item('fbw_trim', prereq='fbw_aileron_gain', value='true', order='600.3')]
        data[3]['value'] = 'true'
        assert 'fbw_trim' in self._visible(data)
        data[1]['value'] = 'FORCETRIM'
        assert 'fbw_trim' not in self._visible(data)

    def test_a_failing_render_gate_hides_a_row(self):
        data = [_item('curve', value='false'),
                _item('detail', value='0.5', render_prereq='curve'),
                _item('fallback', value='0.5', render_prereq='!curve')]
        assert self._visible(data) == {'curve', 'fallback'}

    def test_legacy_convert_rows_never_show(self):
        assert self._visible([_item('aircraft_is_fbw', value='true', datatype='convert')]) == set()

