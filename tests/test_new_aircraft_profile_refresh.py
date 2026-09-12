"""The active profile is re-resolved as soon as the new-aircraft wizard finishes.

The live path resolves an aircraft's active profile once, when the aircraft
loads. For an aircraft that has not been added yet that resolution is None.
The wizard then writes the type row and the profile mapping, but the profile
was only re-resolved when a telemetry frame noticed the config change - and
with the sim paused or sitting in a menu no frame arrives. A setting changed
in that window was written with no <profile> at all (a row nothing reads
back and the importer trips over). Two layers now close it:

* ``TelemManager.refresh_aircraft_profile`` re-resolves on demand, and the
  main window's wizard-finished hook calls it before reloading the form;
* ``ConfigWriter.write_models_to_xml`` refuses to write a profile-less row
  regardless (covered in tests/test_xmlutils.py).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

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


class _SettingsMgr(SimpleNamespace):
    def update_state_vars(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def live(monkeypatch):
    """A TelemManager with an aircraft loaded whose profile resolved to None,
    and an xmlutils that now (after the wizard) resolves 'User Default'."""
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    sm = _SettingsMgr(active_profile=None, current_class='', current_pattern='',
                      offline_mode=False)
    monkeypatch.setattr(G, 'settings_mgr', sm, raising=False)
    monkeypatch.setattr(xmlutils, 'get_pattern_by_sim_fullname', lambda sim, name: 'Hoist.*')
    monkeypatch.setattr(xmlutils, 'read_single_model',
                        lambda the_sim, aircraft_name, input_modeltype='', instance_device='',
                        active_profile=None: ('Helicopter', 'Hoist.*', []))
    monkeypatch.setattr(xmlutils, 'get_active_profile_for_model',
                        lambda sim, cls, model: 'User Default')
    mgr = TelemManager.__new__(TelemManager)      # skip QObject __init__
    mgr.currentAircraftName = 'BELL UH-1H Iroquois Rescue Hoist'
    mgr.currentDataSource = 'MSFS.Helicopter'
    return mgr, sm


class TestRefreshAircraftProfile:
    def test_re_resolves_profile_class_and_pattern(self, live):
        mgr, sm = live
        assert mgr.refresh_aircraft_profile() == 'User Default'
        assert sm.active_profile == 'User Default'
        assert (sm.current_class, sm.current_pattern) == ('Helicopter', 'Hoist.*')

    def test_nothing_loaded_is_a_no_op(self, live):
        mgr, sm = live
        mgr.currentAircraftName = None
        assert mgr.refresh_aircraft_profile() is None
        assert sm.active_profile is None

    def test_data_source_is_remembered_from_the_last_config_resolution(self, live):
        """get_aircraft_config records the data source it was driven with,
        so the refresh re-runs the same resolution (class hint included)."""
        mgr, sm = live
        mgr.currentDataSource = None
        assert mgr.refresh_aircraft_profile() is None          # nothing recorded yet
        mgr.get_aircraft_config(mgr.currentAircraftName, 'MSFS.Helicopter')
        assert mgr.currentDataSource == 'MSFS.Helicopter'
        assert mgr.refresh_aircraft_profile() == 'User Default'


class TestWizardFinishedHook:
    def _window(self):
        from telemffb.MainWindow import MainWindow
        calls = []
        win = SimpleNamespace(
            new_craft_button=MagicMock(),
            _new_craft_anim=MagicMock(),
            profile_change_button=MagicMock(),
            settings_layout=SimpleNamespace(
                reload_layout=lambda *_: calls.append('reload')))
        return MainWindow.new_ac_wizard_finished, win, calls

    def test_refreshes_the_profile_before_reloading_the_form(self, monkeypatch):
        hook, win, calls = self._window()
        tm = SimpleNamespace(refresh_aircraft_profile=lambda: calls.append('refresh'))
        monkeypatch.setattr(G, 'telem_manager', tm, raising=False)
        monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(offline_mode=False), raising=False)
        hook(win)
        assert calls == ['refresh', 'reload']                  # order matters
        win.new_craft_button.setVisible.assert_called_once_with(False)
        win._new_craft_anim.stop.assert_called_once()

    def test_offline_editor_does_not_touch_the_live_profile(self, monkeypatch):
        hook, win, calls = self._window()
        tm = SimpleNamespace(refresh_aircraft_profile=lambda: calls.append('refresh'))
        monkeypatch.setattr(G, 'telem_manager', tm, raising=False)
        monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(offline_mode=True), raising=False)
        hook(win)
        assert calls == ['reload']

    def test_no_telemetry_manager_yet(self, monkeypatch):
        hook, win, calls = self._window()
        monkeypatch.setattr(G, 'telem_manager', None, raising=False)
        monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(offline_mode=False), raising=False)
        hook(win)
        assert calls == ['reload']


class TestSuggestedMatchDefault:
    """Which suggestion the wizard starts on. The whole title ends in the
    livery, the variant or the registration, so preselecting it would pin a
    profile to one paint job."""

    def _pick(self, name, clone_from=None):
        from telemffb.NewAircraftWizard import NewAircraftWizard
        words = name.split()
        patterns = [' '.join(words[:i]) + ".*" for i in range(len(words), 0, -1)]
        wiz = SimpleNamespace(clone_from=clone_from,
                              _MIN_SUGGESTED_WORDS=NewAircraftWizard._MIN_SUGGESTED_WORDS)
        return patterns[NewAircraftWizard._default_suggestion(wiz, patterns, name)]

    def test_the_last_word_of_a_long_title_is_dropped(self):
        assert self._pick("Black Square A36 Bonanza Professional N924SV") ==             "Black Square A36 Bonanza Professional.*"
        assert self._pick("Taylorcraft BC-12D: Bush") == "Taylorcraft BC-12D:.*"
        assert self._pick("Scheibe SF-25 Falke") == "Scheibe SF-25.*"

    def test_a_two_word_title_keeps_both_words(self):
        # dropping one would leave the manufacturer, shared by unrelated aircraft
        assert self._pick("Sopwith Camel") == "Sopwith Camel.*"
        assert self._pick("Cessna Skyhawk") == "Cessna Skyhawk.*"

    def test_a_one_word_title_has_only_one_suggestion(self):
        assert self._pick("MXS-R") == "MXS-R.*"

    def test_a_fork_never_suggests_a_pattern_that_would_lose(self):
        # forking off 737.*: dropping a word still beats it
        assert self._pick("737-600 PAX TC", clone_from=("737.*", "Auto User")) == "737-600 PAX.*"
        # forking off a pattern the shorter suggestion cannot beat
        assert self._pick("Black Square Baron 58P Professional N513LW",
                          clone_from=("Black Square Baron 58P Professional.*", "Auto User")) ==             "Black Square Baron 58P Professional N513LW.*"


class TestForkSuggestions:
    """A fork has to out-rank the pattern it forks off, or the new profile
    never names the aircraft and the button looks like it did nothing."""

    def _wizard(self, clone_from):
        from telemffb.NewAircraftWizard import NewAircraftWizard
        return SimpleNamespace(
            clone_from=clone_from,
            _out_ranks_the_source=lambda p, n: NewAircraftWizard._out_ranks_the_source(
                SimpleNamespace(clone_from=clone_from), p, n))

    def _offered(self, name, clone_from):
        w = self._wizard(clone_from)
        words = name.split()
        pats = [' '.join(words[:i]) + ".*" for i in range(len(words), 0, -1)]
        return [p for p in pats if w._out_ranks_the_source(p, name)]

    def test_equal_or_broader_suggestions_are_not_offered(self):
        offered = self._offered("C172SP Classic Cargo", ("C172SP Classic.*", "Auto User"))
        assert offered == ["C172SP Classic Cargo.*"]
        assert "C172SP Classic.*" not in offered and "C172SP.*" not in offered

    def test_a_broader_source_leaves_more_on_offer(self):
        assert self._offered("C172SP Classic Cargo", ("C172SP.*", "Built-In")) ==             ["C172SP Classic Cargo.*", "C172SP Classic.*"]

    def test_nothing_is_filtered_for_a_brand_new_aircraft(self):
        assert len(self._offered("C172SP Classic Cargo", None)) == 3


class TestSplitButtonState:
    """The split button forks the loaded aircraft off the pattern that names
    it, so with nothing matched there is nothing for it to do."""

    def _enabled(self, monkeypatch, current_pattern, label_text,
                 master=True, sim="MSFS", offline=False):
        from telemffb.MainWindow import MainWindow
        calls = []
        win = SimpleNamespace(
            status_container=SimpleNamespace(
                cur_craft_label=MagicMock(), cur_pattern_label=MagicMock(),
                active_profile_label=MagicMock(),
                set_split_state=lambda v: calls.append(v),
                set_profile_state=lambda v: calls.append(v)),
            refresh_profile_notes_button=lambda: None)
        monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(
            current_aircraft_name="C172SP Classic Cargo", current_pattern=current_pattern,
            active_profile="Built-In", current_sim=sim, offline_mode=offline), raising=False)
        monkeypatch.setattr(G, 'master_instance', master, raising=False)
        MainWindow.update_craft_text_block(win, craft="C172SP Classic Cargo",
                                           pattern=label_text, profile="Built-In")
        assert calls[0] == calls[1]      # the split button and the profile combo agree
        return calls[0]

    def test_disabled_when_nothing_matched(self, monkeypatch):
        # the label reads "Using defaults" there, which is not a pattern to fork
        assert self._enabled(monkeypatch, "", "Using defaults") is False

    def test_enabled_when_a_pattern_names_the_aircraft(self, monkeypatch):
        assert self._enabled(monkeypatch, "C172SP.*", "C172SP.*") is True

    def test_disabled_offline_on_a_child_or_with_no_sim(self, monkeypatch):
        assert self._enabled(monkeypatch, "C172SP.*", "C172SP.*", offline=True) is False
        assert self._enabled(monkeypatch, "C172SP.*", "C172SP.*", master=False) is False
        assert self._enabled(monkeypatch, "C172SP.*", "C172SP.*", sim="nothing") is False


class TestNotesButtonState:
    """A note is written against the pattern that names the aircraft, so with
    nothing matched the dialog would open, fail to save and log an error."""

    def _enabled(self, monkeypatch, pattern, aircraft="C172SP Classic Cargo", sim="MSFS"):
        from telemffb.MainWindow import MainWindow
        calls = []
        win = SimpleNamespace(
            refresh_telem_override_pill=lambda: None,
            status_container=SimpleNamespace(
                set_notes_state=lambda enabled, has_notes: calls.append(enabled)))
        monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(
            current_sim=sim, current_aircraft_name=aircraft,
            current_pattern=pattern, active_profile="Built-In"), raising=False)
        for name in ('read_default_model_notes', 'read_user_default_model_notes',
                     'read_user_model_notes'):
            monkeypatch.setattr(xmlutils, name, lambda *a, **k: '')
        MainWindow.refresh_profile_notes_button(win)
        return calls[0]

    def test_disabled_when_nothing_matched(self, monkeypatch):
        assert self._enabled(monkeypatch, "") is False

    def test_enabled_when_a_pattern_names_the_aircraft(self, monkeypatch):
        assert self._enabled(monkeypatch, "C172SP.*") is True

    def test_disabled_with_no_aircraft_or_no_sim(self, monkeypatch):
        assert self._enabled(monkeypatch, "C172SP.*", aircraft="") is False
        assert self._enabled(monkeypatch, "C172SP.*", sim="nothing") is False


@pytest.fixture
def qt_app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestProfileComboState:
    """Profiles belong to the pattern that names the aircraft, so with nothing
    matched there are none to pick between and none to add to."""

    def _combo(self, monkeypatch, pattern, items):
        from PyQt6.QtWidgets import QComboBox
        from telemffb.MainWindow import MainWindow
        combo = QComboBox()
        win = SimpleNamespace(status_container=SimpleNamespace(
            cb_selectProfileCombo=combo, set_profile_state=combo.setEnabled))
        monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(
            current_sim="MSFS", current_class="PropellerAircraft",
            current_pattern=pattern), raising=False)
        MainWindow.populate_profile_combo(win, items)
        return combo

    def test_disabled_when_nothing_matched(self, qt_app, monkeypatch):
        assert self._combo(monkeypatch, "", []).isEnabled() is False

    def test_enabled_when_a_pattern_names_the_aircraft(self, qt_app, monkeypatch):
        combo = self._combo(monkeypatch, "C172SP.*", ["Built-In", "Auto User"])
        assert combo.isEnabled() is True
        assert [combo.itemText(i) for i in range(combo.count())] ==             ["Select...", "Built-In", "Auto User", "Add New..."]


class TestSimStatusLeavesTheProfileComboAlone:
    """The sim-status transitions run on every telemetry frame.  They used to
    switch the combo back on each time, undoing the gate within a second of
    it being applied."""

    def _widget(self, monkeypatch):
        from telemffb.custom_widgets import AppStatusWidget
        monkeypatch.setattr(G, 'useDarkMode', False, raising=False)
        w = AppStatusWidget(master_instance=True)
        w.set_profile_state(False)
        return w

    def test_running_and_paused_frames_do_not_reenable_it(self, qt_app, monkeypatch):
        w = self._widget(monkeypatch)
        for transition in (w.set_paused, w.set_running, w.set_waiting, w.set_error):
            transition("MSFS")
            assert w.cb_selectProfileCombo.isEnabled() is False

    def test_a_named_aircraft_enables_it(self, qt_app, monkeypatch):
        w = self._widget(monkeypatch)
        w.set_profile_state(True)
        assert w.cb_selectProfileCombo.isEnabled() is True
