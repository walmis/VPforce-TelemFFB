"""Unit tests for KeyValueTableModel (telemffb/ui/panels/MonitorTableModel.py)
- the row model backing MonitorPanel's telemetry and active-effects tables.

No MainWindow, no real telemetry - just the model in isolation: row
insertion/removal/reordering as the key set changes, value-only updates
(the common per-frame case), and which Qt signals fire (and don't fire)
for each, since MonitorPanel's whole reason for existing over the old
QLabel is to touch only what changed.
"""
import os
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from telemffb.ui.panels.MonitorTableModel import KeyValueTableModel

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def model(app):
    return KeyValueTableModel(['Key', 'Value'])


def _capture(signal):
    """Collect every emission of a pyqtSignal as a list of arg-tuples."""
    calls = []
    signal.connect(lambda *args: calls.append(args))
    return calls


class TestEmptyModel:
    def test_starts_empty(self, model):
        assert model.rowCount() == 0
        assert model.columnCount() == 2

    def test_headers(self, model):
        assert model.headerData(0, Qt.Orientation.Horizontal) == 'Key'
        assert model.headerData(1, Qt.Orientation.Horizontal) == 'Value'

    def test_invalid_index_has_no_data(self, model):
        assert model.data(model.index(0, 0)) is None


class TestInitialPopulation:
    def test_set_rows_inserts_everything(self, model):
        inserted = _capture(model.rowsInserted)
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2'))])
        assert model.rowCount() == 2
        assert model.data(model.index(0, 0)) == 'a'
        assert model.data(model.index(0, 1)) == '1'
        assert model.data(model.index(1, 1)) == '2'
        assert len(inserted) == 1  # one contiguous run


class TestValueOnlyUpdate:
    """The steady-state per-frame case: same keys, same order, only values
    change - dataChanged only, no structural signal, no full reset."""

    def test_unchanged_values_emit_nothing(self, model):
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2'))])
        changed = _capture(model.dataChanged)
        inserted = _capture(model.rowsInserted)
        removed = _capture(model.rowsRemoved)
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2'))])
        assert changed == []
        assert inserted == []
        assert removed == []

    def test_changed_value_emits_datachanged_for_that_row_only(self, model):
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2')), ('c', ('c', '3'))])
        changed = _capture(model.dataChanged)
        inserted = _capture(model.rowsInserted)
        model.set_rows([('a', ('a', '1')), ('b', ('b', 'NEW')), ('c', ('c', '3'))])
        assert inserted == []
        assert len(changed) == 1
        top_left, bottom_right, roles = changed[0]
        assert top_left.row() == 1 and bottom_right.row() == 1
        assert model.data(model.index(1, 1)) == 'NEW'
        # untouched rows keep their values
        assert model.data(model.index(0, 1)) == '1'
        assert model.data(model.index(2, 1)) == '3'

    def test_datachanged_span_covers_all_changed_rows(self, model):
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2')), ('c', ('c', '3'))])
        changed = _capture(model.dataChanged)
        model.set_rows([('a', ('a', 'X')), ('b', ('b', '2')), ('c', ('c', 'Y'))])
        assert len(changed) == 1
        top_left, bottom_right, _ = changed[0]
        assert (top_left.row(), bottom_right.row()) == (0, 2)


class TestStructuralUpdate:
    """Key set changes (a new telemetry key appears, an effect starts or
    stops) - beginInsertRows/beginRemoveRows only, never a full reset."""

    def test_added_key_at_end_only_inserts(self, model):
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2'))])
        inserted = _capture(model.rowsInserted)
        removed = _capture(model.rowsRemoved)
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2')), ('c', ('c', '3'))])
        assert removed == []
        assert len(inserted) == 1
        parent, first, last = inserted[0]
        assert (first, last) == (2, 2)
        assert model.rowCount() == 3
        assert model.data(model.index(2, 0)) == 'c'

    def test_added_key_in_middle_inserts_at_target_position(self, model):
        model.set_rows([('a', ('a', '1')), ('c', ('c', '3'))])
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2')), ('c', ('c', '3'))])
        assert [model.row_key(i) for i in range(model.rowCount())] == ['a', 'b', 'c']
        assert model.data(model.index(1, 1)) == '2'

    def test_removed_key_only_removes(self, model):
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2')), ('c', ('c', '3'))])
        inserted = _capture(model.rowsInserted)
        removed = _capture(model.rowsRemoved)
        model.set_rows([('a', ('a', '1')), ('c', ('c', '3'))])
        assert inserted == []
        assert len(removed) == 1
        parent, first, last = removed[0]
        assert (first, last) == (1, 1)
        assert [model.row_key(i) for i in range(model.rowCount())] == ['a', 'c']

    def test_remove_and_insert_together(self, model):
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2')), ('c', ('c', '3'))])
        model.set_rows([('a', ('a', '1')), ('c', ('c', '3')), ('d', ('d', '4'))])
        assert [model.row_key(i) for i in range(model.rowCount())] == ['a', 'c', 'd']

    def test_structural_update_never_resets(self, model):
        """A full beginResetModel()/endResetModel() would be visible as
        modelAboutToBeReset/modelReset signals - MonitorPanel relies on
        those never firing so the view keeps its scroll position and
        selection across a key-set change."""
        model.set_rows([('a', ('a', '1'))])
        reset_signals = _capture(model.modelReset)
        model.set_rows([('b', ('b', '2'))])
        assert reset_signals == []

    def test_value_change_on_surviving_row_during_structural_update(self, model):
        """A key can both survive AND change value in the same update as
        another key is added/removed - the structural path must not lose
        that value change."""
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2'))])
        model.set_rows([('a', ('a', 'CHANGED')), ('c', ('c', '3'))])
        assert model.data(model.index(0, 1)) == 'CHANGED'


class TestColumnValues:
    def test_column_values_returns_row_order(self, model):
        model.set_rows([('a', ('Alpha', '1')), ('b', ('Bravo', '2'))])
        assert model.column_values(0) == ['Alpha', 'Bravo']


class TestClear:
    def test_clear_removes_all_rows(self, model):
        model.set_rows([('a', ('a', '1')), ('b', ('b', '2'))])
        removed = _capture(model.rowsRemoved)
        model.clear()
        assert model.rowCount() == 0
        assert len(removed) == 1

    def test_clear_on_empty_model_is_a_noop(self, model):
        removed = _capture(model.rowsRemoved)
        model.clear()
        assert removed == []


class TestSingleColumn:
    """The active-effects table has one column and rows keyed by their own
    rendered text (see MonitorPanel.update_effects)."""

    def test_single_column_rows(self, app):
        effects_model = KeyValueTableModel(['Active Effects'])
        effects_model.set_rows([('ID:1 Spring', ('ID:1 Spring',)),
                                 ('ID:2 Damper', ('ID:2 Damper',))])
        assert effects_model.columnCount() == 1
        assert effects_model.data(effects_model.index(0, 0)) == 'ID:1 Spring'
        assert effects_model.data(effects_model.index(1, 0)) == 'ID:2 Damper'
