"""MainMenu.add_debug_menu (telemffb/ui/menus.py) - the Alt+D / debug-key
Debug menu extracted from MainWindow. Only the "builds once" guard (it
scans self.menu.actions() for an existing "Debug" entry before adding
another) is cheap to cover without standing up the rest of MainWindow.
"""
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMenuBar, QWidget

import telemffb.globals as G
from telemffb.ui.menus import MainMenu

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeMainWindow(QWidget):
    """Stands in for MainWindow: a real QObject to parent the debug menu's
    actions to, plus the one callback add_debug_menu calls synchronously."""

    def refresh_configurator_gating(self):
        pass

    preview = SimpleNamespace(start=lambda spec: None, stop=lambda: None)


@pytest.fixture
def main_menu(qapp, monkeypatch):
    monkeypatch.setattr(G, 'master_instance', False, raising=False)
    mw = FakeMainWindow()
    mm = MainMenu(mw)
    mm.menu = QMenuBar()
    return mm


class TestAddDebugMenuIsIdempotent:
    def test_first_call_adds_the_debug_menu(self, main_menu):
        main_menu.add_debug_menu()
        assert any(a.text() == "Debug" for a in main_menu.menu.actions())

    def test_second_call_does_not_add_a_second_one(self, main_menu):
        main_menu.add_debug_menu()
        main_menu.add_debug_menu()
        debug_actions = [a for a in main_menu.menu.actions() if a.text() == "Debug"]
        assert len(debug_actions) == 1

    def test_configurator_settings_action_lands_on_the_main_menu(self, main_menu):
        """refresh_configurator_gating (on MainWindow) reaches this
        attribute through main_menu.set_configurator_action_enabled."""
        main_menu.add_debug_menu()
        assert hasattr(main_menu, 'configurator_settings_action')
