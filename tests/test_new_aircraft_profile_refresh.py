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
