"""OfflineEditorPanel (telemffb/ui/panels/OfflineEditorPanel.py) - the
"Offline Editor Setup" sim/class/aircraft/profile selector cascade extracted
from MainWindow's ~15 offline_* widgets and handlers.

No real XML files or IPC threads here: xmlutils' read functions and the
G globals it touches (settings_mgr, master_instance, ipc_instance,
telem_manager) are monkeypatched, and the owning MainWindow is stood in for
by a SimpleNamespace exposing just the methods this panel calls back into
(settings_layout.reload_caller, refresh_profile_notes_button,
toggle_offline_mode, profile_mgr_dialog) - the same "stand-in window" shape
tests/test_offline_editor_button.py already uses for MainWindow itself.
"""
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

import telemffb.globals as G
import telemffb.ui.panels.OfflineEditorPanel as offline_editor_module
from telemffb.ui.panels.OfflineEditorPanel import OfflineEditorPanel

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeMainWindow:
    """Stands in for MainWindow: only the public surface OfflineEditorPanel
    calls back into."""

    def __init__(self):
        self.settings_layout = SimpleNamespace(
            reload_caller=lambda: self.reload_calls.append(True),
            clear_layout=lambda: None,
        )
        self.reload_calls = []
        self.notes_refreshes = 0
        self.toggle_calls = []
        self.profile_mgr_dialog = SimpleNamespace(show=lambda: None)

    def refresh_profile_notes_button(self):
        self.notes_refreshes += 1

    def toggle_offline_mode(self, state, broadcast=True):
        self.toggle_calls.append((state, broadcast))


class FakeXml:
    """Stand-in for telemffb.xmlutils: fixed sim/class/model/profile tree,
    call log for the broadcast-worthy assertions."""

    def __init__(self):
        self.calls = []

    def get_sims(self):
        return ['DCS', 'MSFS']

    def get_classes_for_sim(self, sim):
        self.calls.append(('get_classes_for_sim', sim))
        return {'DCS': ['JetAircraft', 'PropellerAircraft'], 'MSFS': ['Helicopter']}.get(sim, [])

    def read_models(self, sim, cls=None):
        self.calls.append(('read_models', sim, cls))
        if cls == 'JetAircraft':
            return ['F-16C', 'F/A-18C']
        if cls is None:
            return ['F-16C', 'F/A-18C']
        return []

    def get_available_profiles(self, sim, cls, model):
        self.calls.append(('get_available_profiles', sim, cls, model))
        return ['Built-In', 'Auto User']

    def update_active_profile_entry(self, sim, cls, model, new_profile):
        self.calls.append(('update_active_profile_entry', sim, cls, model, new_profile))


@pytest.fixture
def fake_xml(monkeypatch):
    fx = FakeXml()
    monkeypatch.setattr(offline_editor_module, 'xmlutils', fx)
    return fx


@pytest.fixture
def fake_settings_mgr(monkeypatch):
    sm = SimpleNamespace(offline_scope=None, current_sim=None, current_class=None,
                         current_aircraft_name=None, active_profile=None)
    monkeypatch.setattr(G, 'settings_mgr', sm, raising=False)
    return sm


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []
    monkeypatch.setattr(G, 'ipc_instance', SimpleNamespace(
        send_broadcast_message=lambda msg: sent.append(msg)), raising=False)
    return sent


@pytest.fixture
def panel(qapp, fake_xml, fake_settings_mgr, broadcasts, monkeypatch):
    monkeypatch.setattr(G, 'master_instance', False, raising=False)
    # offline_aircraft_changed calls this - reachable any time a combo's
    # currentTextChanged actually fires (including via the premature
    # blockSignals(False) inside filter_offline_name_list - see
    # TestLoadSingleOfflineModel's module docstring note below).
    monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(
        get_aircraft_config=lambda name, sim: ({}, 'JetAircraft')), raising=False)
    mw = FakeMainWindow()
    p = OfflineEditorPanel(mainwindow=mw)
    p.mainwindow = mw
    return p


class TestConstruction:
    def test_starts_hidden(self, panel):
        assert panel.isHidden()

    def test_owns_its_own_widgets_not_the_old_mainwindow_names(self, panel):
        # The panel is self-contained: everything the old MainWindow.offline_*
        # attributes named now lives on the panel instance.
        for name in ('offline_sim', 'offline_class', 'offline_name', 'offline_profile',
                    'offline_name_filter', 'offline_scope_label',
                    'back_to_profile_mgr_button', 'exit_offline_button'):
            assert hasattr(panel, name)


class TestSimCascade:
    def test_choosing_a_sim_populates_classes_and_sets_scope(self, panel, fake_settings_mgr):
        panel.select_sim('DCS')
        assert [panel.offline_class.itemText(i) for i in range(panel.offline_class.count())] == \
            ['', 'JetAircraft', 'PropellerAircraft']
        assert fake_settings_mgr.offline_scope == 'SIM'
        assert panel.offline_scope_label.text() == "Editing SIM Defaults (DCS)"
        # force_sim_aircraft ran: settings tab reloaded, notes button refreshed
        assert panel.mainwindow.reload_calls
        assert panel.mainwindow.notes_refreshes

    def test_clearing_the_sim_resets_everything(self, panel):
        panel.select_sim('DCS')
        panel.select_sim('')
        assert panel.offline_class.count() == 0
        assert panel.offline_scope_label.text() == 'None'
        assert not panel.offline_name_filter.isEnabled()

    def test_master_instance_broadcasts_the_selection(self, panel, monkeypatch, broadcasts):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel.select_sim('DCS')
        assert any(m.startswith('OFFLINE_SIM:DCS') for m in broadcasts)

    def test_child_instance_does_not_broadcast(self, panel, broadcasts):
        # panel fixture already sets G.master_instance False
        panel.select_sim('DCS')
        assert broadcasts == []


class TestMirrorMethods:
    """IPC calls these directly instead of reaching into the combo boxes -
    see main.py's set_offline_*_signal connections."""

    def test_mirror_sim_drives_the_same_cascade_as_a_user_pick(self, panel, fake_settings_mgr):
        panel.mirror_sim('DCS')
        assert fake_settings_mgr.offline_scope == 'SIM'
        assert panel.offline_class.count() == 3

    def test_mirror_class(self, panel, fake_settings_mgr):
        panel.select_sim('DCS')
        panel.mirror_class('JetAircraft')
        assert fake_settings_mgr.offline_scope == 'CLASS'

    def test_mirror_aircraft(self, panel):
        panel.select_sim('DCS')
        panel.offline_class.setCurrentText('JetAircraft')
        panel.mirror_aircraft('F-16C')
        assert panel.offline_name.currentText() == 'F-16C'

    def test_mirror_profile(self, panel, fake_settings_mgr):
        panel.select_sim('DCS')
        panel.offline_class.setCurrentText('JetAircraft')
        panel.offline_name.setCurrentText('F-16C')
        panel.mirror_profile('Auto User')
        assert panel.offline_scope_label.text() == "Editing Aircraft (F-16C - Auto User)"


class TestResetForEntry:
    def test_clears_combos_and_repopulates_sims(self, panel):
        panel.select_sim('DCS')
        panel.reset_for_entry()
        assert [panel.offline_sim.itemData(i) for i in range(panel.offline_sim.count())] == \
            ['', 'DCS', 'MSFS']
        assert panel.offline_class.count() == 0
        assert panel.offline_name.count() == 0

    def test_a_stale_profile_does_not_survive_re_entry(self, panel):
        """With no sim selected, clearing offline_sim emits nothing, so only
        reset_for_entry itself can clear a profile left from last time."""
        panel.offline_profile.addItem('Stale Profile')
        assert panel.selected_sim == ''
        panel.reset_for_entry()
        assert panel.offline_profile.count() == 0
        assert not panel.offline_profile.signalsBlocked()


class TestFilterNameList:
    def test_a_single_match_auto_selects_and_drills_down(self, panel, fake_settings_mgr):
        panel.select_sim('DCS')
        panel.offline_class.setCurrentText('JetAircraft')
        panel.offline_name_filter.setText('F-16')
        assert panel.offline_name.currentText() == 'F-16C'
        assert fake_settings_mgr.offline_scope == 'MODEL'


class TestBackToProfileManager:
    def test_normal_path_hides_button_shows_dialog_and_exits_offline(self, panel):
        panel.back_to_profile_mgr_button.setVisible(True)
        panel.back_to_profile_mgr()
        assert panel.back_to_profile_mgr_button.isHidden()
        assert panel.mainwindow.toggle_calls == [(False, True)]

    def test_a_closed_dialog_is_swallowed_and_still_exits_offline(self, panel, monkeypatch):
        """Latent bug preserved from MainWindow.back_to_profile_mgr: this is
        a bare `except:`, so it also swallows KeyboardInterrupt/SystemExit,
        not just the AttributeError/RuntimeError a torn-down dialog raises."""
        def boom():
            raise RuntimeError("dialog was closed")
        panel.mainwindow.profile_mgr_dialog = SimpleNamespace(show=boom)
        warned = []
        monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: warned.append(a))
        panel.back_to_profile_mgr()
        assert warned
        assert panel.mainwindow.toggle_calls == [(False, True)]


class TestLoadSingleOfflineModel:
    def test_populates_the_full_selection_and_enters_offline_mode(self, panel, fake_settings_mgr):
        panel.load_single_offline_model('DCS', 'JetAircraft', 'F-16C', 'Auto User')
        assert panel.mainwindow.toggle_calls == [(True, False)]
        assert panel.selected_sim == 'DCS'
        assert panel.offline_class.currentText() == 'JetAircraft'
        assert panel.offline_name.currentText() == 'F-16C'
        assert panel.offline_profile.currentText() == 'Auto User'
        assert fake_settings_mgr.offline_scope == 'MODEL'

    def test_no_user_profile_selects_and_activates_auto_user(self, panel, fake_xml,
                                                             fake_settings_mgr, monkeypatch):
        """Built-In cannot be edited: with no user profile the editor works on
        Auto User, so edits are not written as rows no profile owns."""
        monkeypatch.setattr(fake_xml, 'get_available_profiles',
                            lambda sim, cls, model: ['Built-In'])
        panel.load_single_offline_model('DCS', 'JetAircraft', 'F-16C', '')
        assert [panel.offline_profile.itemText(i)
                for i in range(panel.offline_profile.count())] == ['Auto User']
        assert panel.offline_profile.currentText() == 'Auto User'
        assert fake_settings_mgr.active_profile == 'Auto User'
        assert panel.offline_scope_label.text() == "Editing Aircraft (F-16C - Auto User)"

    def test_a_class_only_aircraft_gets_no_auto_user(self, panel, fake_xml, monkeypatch):
        monkeypatch.setattr(fake_xml, 'get_available_profiles',
                            lambda sim, cls, model: ['Built-In'])
        panel.load_single_offline_model('DCS', 'PropellerAircraft', '', '')
        assert panel.offline_profile.findText('Auto User') == -1

    def test_a_class_only_aircraft_edits_class_defaults(self, panel, fake_settings_mgr):
        panel.load_single_offline_model('DCS', 'PropellerAircraft', '', '')
        assert fake_settings_mgr.offline_scope == 'CLASS'
        assert panel.offline_scope_label.text() == "Editing Class Defaults (PropellerAircraft)"

    def test_master_instance_shows_back_button_and_broadcasts(self, panel, monkeypatch, broadcasts):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel.load_single_offline_model('DCS', 'JetAircraft', 'F-16C', 'Auto User',
                                        from_profile_manager=True)
        assert not panel.back_to_profile_mgr_button.isHidden()
        assert any(m.startswith('SHOW_OFFLINE_MODEL:') for m in broadcasts)

    def test_not_from_profile_manager_leaves_back_button_hidden(self, panel, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel.load_single_offline_model('DCS', 'JetAircraft', 'F-16C', 'Auto User',
                                        from_profile_manager=False)
        assert panel.back_to_profile_mgr_button.isHidden()

    def test_child_instance_does_not_broadcast_or_touch_the_back_button(self, panel, broadcasts):
        # panel fixture already sets G.master_instance False
        panel.load_single_offline_model('DCS', 'JetAircraft', 'F-16C', 'Auto User')
        assert broadcasts == []
