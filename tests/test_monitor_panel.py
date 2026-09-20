"""Unit tests for MonitorPanel (telemffb/ui/panels/MonitorPanel.py) - the
Monitor tab's telemetry/active-effects tables extracted from MainWindow's
two hand-rolled QLabels.

No real MainWindow, TelemManager or SimConnect: G.system_settings and
G.telem_manager.simconnect are monkeypatched, and the owning MainWindow is
stood in for by FakeMainWindow exposing just ``detach_tab`` - the same
"stand-in window" shape tests/test_offline_editor_panel.py already uses.
"""
import os
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import telemffb.globals as G
from telemffb.ui.panels.MonitorPanel import MonitorPanel

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class _FakeSettings:
    """Minimal stand-in for G.system_settings (a .get(key, default) API)."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeMainWindow:
    """Stands in for MainWindow: only the public surface MonitorPanel calls
    back into (the detach toolbar button)."""

    def __init__(self):
        self.detach_calls = []

    def detach_tab(self, index):
        self.detach_calls.append(index)


@pytest.fixture(autouse=True)
def fake_system_settings(monkeypatch):
    monkeypatch.setattr(G, 'system_settings', _FakeSettings({
        'enableDCS': True, 'enableIL2': False, 'enableMSFS': True,
        'enableXPLANE': False, 'enableBMS': False,
    }), raising=False)


@pytest.fixture
def mainwindow():
    return FakeMainWindow()


@pytest.fixture
def panel(qapp, mainwindow):
    return MonitorPanel(mainwindow=mainwindow)


class TestWaitingState:
    def test_starts_on_waiting_page(self, panel):
        assert panel._telem_stack.currentWidget() is panel._telem_waiting_label
        assert 'DCS     : Enabled' in panel._telem_waiting_label.text()
        assert 'IL2     : Disabled' in panel._telem_waiting_label.text()

    def test_telemetry_keys_before_first_frame(self, panel):
        assert panel.telemetry_keys() == ['Sim not running']

    def test_refresh_waiting_status_reflects_current_settings(self, panel, monkeypatch):
        monkeypatch.setattr(G, 'system_settings', _FakeSettings({'enableDCS': False}),
                             raising=False)
        panel.refresh_waiting_status()
        assert 'DCS     : Disabled' in panel._telem_waiting_label.text()


class TestTelemetryRows:
    def test_update_switches_to_table_and_formats_rows(self, panel):
        panel.update_telemetry({'AoA': 1.23456, 'N': 'F-16C', 'src': 'DCS'})
        assert panel._telem_stack.currentWidget() is panel.telem_view
        keys = panel.telemetry_keys()
        assert set(keys) == {'AoA', 'N', 'src'}
        model = panel._telem_model
        row = keys.index('AoA')
        assert model.column_values(1)[row] == '1.235'

    def test_list_values_formatted_like_old_label(self, panel):
        panel.update_telemetry({'Gear': [1.0, 0, None]})
        model = panel._telem_model
        assert model.column_values(1)[0] == '[1.000, 0, None]'

    def test_filter_keeps_only_matching_keys(self, panel):
        panel.telem_filter.setText('aoa, ias')
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0, 'RPM': 3.0})
        assert set(panel.telemetry_keys()) == {'AoA', 'IAS'}

    def test_filter_is_case_insensitive_and_comma_separated(self, panel):
        panel.telem_filter.setText(' AOA ,rpm')
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0, 'RPM': 3.0})
        assert set(panel.telemetry_keys()) == {'AoA', 'RPM'}

    def test_same_keys_next_frame_only_updates_values(self, panel):
        panel.update_telemetry({'AoA': 1.0})
        model = panel._telem_model
        rows_inserted = []
        model.rowsInserted.connect(lambda *a: rows_inserted.append(a))
        panel.update_telemetry({'AoA': 2.0})
        assert rows_inserted == []
        assert model.column_values(1) == ['2.000']

    def test_show_simvars_renames_msfs_keys(self, panel, monkeypatch):
        fake_simconnect = SimpleNamespace(get_var_name=lambda k: f"SIMVAR_{k}" if k == 'foo' else None)
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=fake_simconnect),
                             raising=False)
        panel.set_show_simvars(True)
        panel.update_telemetry({'foo': 1.0, 'src': 'MSFS'})
        assert 'SIMVAR_foo' in panel.telemetry_keys()

    def test_show_simvars_only_applies_to_msfs(self, panel, monkeypatch):
        fake_simconnect = SimpleNamespace(get_var_name=lambda k: 'SHOULD_NOT_APPEAR')
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=fake_simconnect),
                             raising=False)
        panel.set_show_simvars(True)
        panel.update_telemetry({'foo': 1.0, 'src': 'DCS'})
        assert 'foo' in panel.telemetry_keys()

    def test_show_simvars_handles_missing_src_key(self, panel, monkeypatch):
        """Telemetry dicts without a 'src' key (e.g. before the first frame
        with source info arrives) must not raise a KeyError."""
        fake_simconnect = SimpleNamespace(get_var_name=lambda k: 'SHOULD_NOT_APPEAR')
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=fake_simconnect),
                             raising=False)
        panel.set_show_simvars(True)
        panel.update_telemetry({'foo': 1.0})
        assert 'foo' in panel.telemetry_keys()

    def test_duplicate_display_names_do_not_collide(self, panel, monkeypatch):
        """Two distinct telemetry keys that happen to rename to the same
        simvar name must still show as two rows - rows are keyed by the
        original telemetry key, not the (possibly renamed) display key."""
        fake_simconnect = SimpleNamespace(get_var_name=lambda k: 'SAME_NAME')
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=fake_simconnect),
                             raising=False)
        panel.set_show_simvars(True)
        panel.update_telemetry({'foo': 1.0, 'bar': 2.0, 'src': 'MSFS'})
        assert panel._telem_model.rowCount() == 3  # foo, bar, src


class TestActiveEffects:
    def test_update_effects_splits_lines_into_rows(self, panel):
        panel.update_effects('ID:1 Spring Override\nID:2 Damper Override\n')
        model = panel._effects_model
        assert model.rowCount() == 2
        assert model.column_values(0) == ['ID:1 Spring Override', 'ID:2 Damper Override']

    def test_empty_text_yields_no_rows(self, panel):
        panel.update_effects('ID:1 Spring\n')
        panel.update_effects('')
        assert panel._effects_model.rowCount() == 0

    def test_clear_effects_empties_the_table(self, panel):
        panel.update_effects('ID:1 Spring\n')
        panel.clear_effects()
        assert panel._effects_model.rowCount() == 0


class TestEffectsScopeLabel:
    def test_default_label(self, panel):
        assert panel.effect_lbl.text() == 'Active Effects:'

    def test_scoped_label_names_the_device(self, panel):
        panel.set_effects_scope_label('pedals')
        assert panel.effect_lbl.text() == 'Active Effects for: <b>Pedals</b>'

    def test_clearing_scope_restores_default(self, panel):
        panel.set_effects_scope_label('pedals')
        panel.set_effects_scope_label(None)
        assert panel.effect_lbl.text() == 'Active Effects:'


class TestCopySelection:
    """CopyableTableView (telemffb.ui.widgets.custom_widgets) stands in for
    the old QLabel's mouse-selectable free text - Ctrl+C copies the
    selected cells as tab/newline-separated text."""

    def test_copy_selected_row_to_clipboard(self, panel):
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0})
        view = panel.telem_view
        model = view.model()
        selection = view.selectionModel()
        selection.select(model.index(0, 0),
                          selection.SelectionFlag.Select)
        selection.select(model.index(0, 1),
                          selection.SelectionFlag.Select)
        view._copy_selection()
        expected = '\t'.join([model.data(model.index(0, 0)), model.data(model.index(0, 1))])
        assert QApplication.clipboard().text() == expected

    def test_copy_with_no_selection_leaves_clipboard_untouched(self, panel):
        QApplication.clipboard().setText('unchanged')
        panel.telem_view._copy_selection()
        assert QApplication.clipboard().text() == 'unchanged'


class TestDetachToolbar:
    def test_detach_button_calls_back_into_mainwindow_tab_zero(self, panel, mainwindow):
        panel.detach_action.trigger()
        assert mainwindow.detach_calls == [0]

    def test_set_detach_toolbar_visible(self, panel):
        # isHidden() reflects the widget's own explicit visibility flag
        # (unlike isVisible(), which also depends on the panel itself
        # having been shown inside a top-level window).
        panel.set_detach_toolbar_visible(False)
        assert panel.detach_toolbar.isHidden() is True
        panel.set_detach_toolbar_visible(True)
        assert panel.detach_toolbar.isHidden() is False


class TestSplit:
    """The two panes are tables with the same size hint, so the splitter
    would start them level. 70/30 is what the page measured before its
    panes were tables, when the split fell out of two labels' size hints."""

    def _shares(self, panel):
        telemetry, effects = panel._splitter.sizes()
        return telemetry / (telemetry + effects)

    def test_telemetry_starts_with_seventy_percent_of_the_width(self, panel):
        panel.resize(1600, 500)
        panel.show()
        QApplication.processEvents()
        assert self._shares(panel) == pytest.approx(0.70, abs=0.02)
        panel.close()

    def test_the_share_holds_as_the_window_widens(self, panel):
        panel.resize(1600, 500)
        panel.show()
        QApplication.processEvents()
        panel.resize(2200, 500)
        QApplication.processEvents()
        assert self._shares(panel) == pytest.approx(0.70, abs=0.02)
        panel.close()

    def test_a_split_the_user_dragged_is_not_reset_on_reshow(self, panel):
        """The starting split is applied once - coming back to the tab
        (or reattaching the detached window) keeps what the user set."""
        panel.resize(1600, 500)
        panel.show()
        QApplication.processEvents()
        total = sum(panel._splitter.sizes())
        panel._splitter.setSizes([total // 2, total - total // 2])
        panel.hide()
        panel.show()
        QApplication.processEvents()
        assert self._shares(panel) == pytest.approx(0.5, abs=0.02)
        panel.close()
