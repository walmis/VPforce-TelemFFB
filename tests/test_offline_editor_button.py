"""The "Enter Offline Editor" corner button: opens the offline editor on
the aircraft that is loaded right now, pre-selected all the way down to
its profile, instead of from a blank selector chain.

Pins the pure parts - what target the loaded aircraft resolves to, and
when the button shows - without constructing the main window.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets

import telemffb.globals as G
from telemffb.MainWindow import MainWindow


class TestLiveOfflineTarget:
    def test_loaded_model_with_an_active_profile(self):
        sm = SimpleNamespace(current_sim='MSFS', current_class='Helicopter',
                             current_pattern='Bell 407', active_profile='Auto User')
        assert MainWindow.live_offline_target(sm) == ('MSFS', 'Helicopter', 'Bell 407', 'Auto User')

    def test_no_active_profile_takes_the_first_real_one(self):
        sm = SimpleNamespace(current_sim='DCS', current_class='JetAircraft',
                             current_pattern='F-16C', active_profile=None)
        assert MainWindow.live_offline_target(sm, ('Built-In', 'Mine', 'Other')) == \
            ('DCS', 'JetAircraft', 'F-16C', 'Mine')
        assert MainWindow.live_offline_target(sm, ('Built-In',)) == ('DCS', 'JetAircraft', 'F-16C', '')

    def test_class_level_aircraft_has_no_model_and_no_profile(self):
        sm = SimpleNamespace(current_sim='DCS', current_class='PropellerAircraft',
                             current_pattern='', active_profile=None)
        assert MainWindow.live_offline_target(sm, ('Mine',)) == ('DCS', 'PropellerAircraft', '', '')

    def test_nothing_loaded(self):
        assert MainWindow.live_offline_target(SimpleNamespace(current_sim='nothing')) is None
        assert MainWindow.live_offline_target(SimpleNamespace()) is None


@pytest.fixture
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestButtonVisibility:
    """refresh_offline_editor_button on a stand-in window with a real button."""

    def _window(self):
        return SimpleNamespace(offline_editor_button=QtWidgets.QPushButton(),
                               live_offline_target=MainWindow.live_offline_target)

    def _refresh(self, win, monkeypatch, *, loaded, offline, sm=None):
        monkeypatch.setattr(G, 'telem_manager',
                            SimpleNamespace(currentAircraft=object() if loaded else None),
                            raising=False)
        monkeypatch.setattr(G, 'settings_mgr', sm or SimpleNamespace(
            current_sim='MSFS', current_class='Helicopter', current_pattern='Bell 407',
            active_profile='Auto User', offline_mode=offline), raising=False)
        MainWindow.refresh_offline_editor_button(win)
        return win.offline_editor_button

    def test_shown_with_an_aircraft_loaded_and_the_editor_closed(self, qapp, monkeypatch):
        b = self._refresh(self._window(), monkeypatch, loaded=True, offline=False)
        assert b.isVisibleTo(b.parentWidget()) or not b.isHidden()
        assert 'MSFS / Helicopter / Bell 407 / Auto User' in b.toolTip()

    def test_hidden_with_nothing_loaded(self, qapp, monkeypatch):
        b = self._refresh(self._window(), monkeypatch, loaded=False, offline=False)
        assert b.isHidden()

    def test_hidden_while_the_editor_is_open(self, qapp, monkeypatch):
        b = self._refresh(self._window(), monkeypatch, loaded=True, offline=True)
        assert b.isHidden()

    def test_class_level_aircraft_says_so(self, qapp, monkeypatch):
        sm = SimpleNamespace(current_sim='DCS', current_class='PropellerAircraft',
                             current_pattern='', active_profile=None, offline_mode=False)
        b = self._refresh(self._window(), monkeypatch, loaded=True, offline=False, sm=sm)
        assert not b.isHidden()
        assert 'DCS / PropellerAircraft / (class defaults)' in b.toolTip()
