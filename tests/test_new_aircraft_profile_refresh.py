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
        assert [combo.itemText(i) for i in range(combo.count())] == \
            ["Select...", "Built-In", "Auto User", "Add New..."]

    def test_the_aircraft_refresh_applies_the_same_rule(self, monkeypatch):
        from telemffb.MainWindow import MainWindow
        calls = []
        win = SimpleNamespace(
            status_container=SimpleNamespace(
                cur_craft_label=MagicMock(), cur_pattern_label=MagicMock(),
                active_profile_label=MagicMock(),
                set_profile_state=lambda v: calls.append(v)),
            refresh_profile_notes_button=lambda: None)
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        for pattern, want in (("", False), ("C172SP.*", True)):
            monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(
                current_aircraft_name="C172SP Classic Cargo", current_pattern=pattern,
                active_profile="Built-In", current_sim="MSFS", offline_mode=False), raising=False)
            # the label reads "Using defaults" with nothing matched: not a pattern
            MainWindow.update_craft_text_block(win, craft="C172SP Classic Cargo",
                                               pattern=pattern or "Using defaults", profile="Built-In")
            assert calls[-1] is want


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

class TestNoProfileToWriteTo:
    """An MSFS aircraft nothing names is auto-classified and flies the class
    defaults.  A change then has no profile to land in: the funnel must
    refuse quietly, because it is reached from a slider's Qt slot."""

    def _mgr(self):
        from telemffb.SettingsManager import SettingsManager
        sm = SettingsManager.__new__(SettingsManager)
        sm.offline_mode = False
        sm.active_profile = None
        return sm

    def test_a_write_with_no_pattern_is_refused_not_raised(self, monkeypatch):
        written = []
        monkeypatch.setattr(xmlutils, 'write_models_to_xml', lambda *a, **k: written.append(a))
        self._mgr().write_to_xml('MSFS', 'Helicopter', '', '0.01', 'trim_release_spring_gain')
        assert written == []

    def test_an_erase_with_no_pattern_is_refused_not_raised(self, monkeypatch):
        erased = []
        monkeypatch.setattr(xmlutils, 'erase_models_from_xml', lambda *a, **k: erased.append(a))
        self._mgr().erase_from_xml('MSFS', 'Helicopter', '', 'trim_release_spring_gain')
        assert erased == []

    def test_a_write_with_a_pattern_still_goes_through(self, monkeypatch):
        written = []
        monkeypatch.setattr(xmlutils, 'write_models_to_xml', lambda *a, **k: written.append(a))
        self._mgr().write_to_xml('MSFS', 'Helicopter', 'Hoist.*', '0.01', 'trim_release_spring_gain')
        assert len(written) == 1


class TestSettingsFormWithoutProfile:
    """The form still shows what the aircraft flies with, but nothing on it
    can be changed until a profile names the aircraft."""

    @pytest.fixture
    def form(self, qt_app, monkeypatch, tmp_path):
        from PyQt6 import QtWidgets
        import pathlib
        defaults = pathlib.Path(__file__).resolve().parents[1] / "defaults.xml"
        user = tmp_path / "userconfig_v2.xml"
        user.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<TelemFFB>\n</TelemFFB>\n')
        monkeypatch.setattr(G, 'userconfig_path', str(user), raising=False)
        monkeypatch.setattr(G, 'defaults_path', str(defaults), raising=False)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        monkeypatch.setattr(G, 'useDarkMode', False, raising=False)
        monkeypatch.setattr(G, 'system_settings', {}, raising=False)
        xmlutils.update_vars('joystick', str(user), str(defaults))
        xmlutils.update_roots()
        # the real manager: the form calls its enum and setting readers
        from telemffb.SettingsManager import SettingsManager
        sm = SettingsManager(datasource='MSFS', device='joystick',
                             userconfig_path=str(user), defaults_path=str(defaults))
        sm.update_state_vars(current_sim='MSFS', current_class='Helicopter',
                             current_aircraft_name='Mystery Rotorcraft', current_pattern='')
        monkeypatch.setattr(G, 'settings_mgr', sm, raising=False)
        from telemffb.SettingsLayout import SettingsLayout

        from telemffb.custom_widgets import NoKeyScrollArea

        def build(pattern):
            sm.current_pattern = pattern
            # the form reaches three parents up for the scroll area that
            # collects its sliders, so nest it the way the window does
            area = NoKeyScrollArea()
            host = QtWidgets.QWidget()
            area.setWidget(host)
            layout = SettingsLayout(parent=host, mainwindow=None)
            layout.reload_layout(None)
            build.keep = (area, host)          # hold the ancestry alive
            return layout
        return build

    @staticmethod
    def _inputs(layout):
        """Every widget a user could act on, wherever the form nests it."""
        from PyQt6 import QtWidgets
        kinds = (QtWidgets.QSlider, QtWidgets.QAbstractButton, QtWidgets.QComboBox, QtWidgets.QLineEdit)
        found = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget() is not None and isinstance(item.widget(), kinds):
                found.append(item.widget())
            elif item.layout() is not None:
                found.extend(TestSettingsFormWithoutProfile._inputs(item.layout()))
        return found

    def test_every_input_is_disabled_when_nothing_names_the_aircraft(self, form, monkeypatch):
        monkeypatch.setattr(xmlutils, 'read_single_model',
                            lambda sim, name, cls='', dev='', active_profile=None:
                            xmlutils._resolver().resolve(sim, name, cls, dev or 'joystick'))
        layout = form('')
        inputs = self._inputs(layout)
        assert inputs, "the form should still show the defaults the aircraft flies with"
        assert all(not w.isEnabled() for w in inputs)

    def test_inputs_come_back_once_a_pattern_names_it(self, form, monkeypatch):
        monkeypatch.setattr(xmlutils, 'read_single_model',
                            lambda sim, name, cls='', dev='', active_profile=None:
                            xmlutils._resolver().resolve(sim, name, cls, dev or 'joystick'))
        # what the wizard writes: a type row and a profile mapping, after
        # which the form resolves the pattern for itself
        xmlutils.write_models_to_xml('MSFS', 'Mystery.*', 'Helicopter', 'type', '', 'joystick', 'User Default')
        xmlutils.update_active_profile_entry('MSFS', 'Helicopter', 'Mystery.*', 'User Default')
        xmlutils.update_roots()
        layout = form('')
        assert G.settings_mgr.current_pattern == 'Mystery.*'
        inputs = self._inputs(layout)
        assert inputs and any(w.isEnabled() for w in inputs)
