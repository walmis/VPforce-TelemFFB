"""TrayController (telemffb/ui/tray.py) - extracted from MainWindow's
add_system_tray/pop_tray_notification/update_sim_indicators.

Only the icon/tooltip-selection and notification-dedup logic is worth
covering here without a live tray icon; QSystemTrayIcon itself needs a
QApplication (offscreen, like tests/test_offline_editor_panel.py) but no
real system tray, since showMessage/setIcon/setToolTip are safe no-ops
without one.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

import telemffb.globals as G
from telemffb.ui.tray import TrayController

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeMainWindow(QWidget):
    """Stands in for MainWindow: TrayController only needs a real QObject
    to parent the tray icon to, plus the .show() slot messageClicked
    connects to."""


@pytest.fixture
def tray(qapp, monkeypatch):
    monkeypatch.setattr(G, 'master_instance', True, raising=False)
    mw = FakeMainWindow()
    return TrayController(mw)


class TestSetStatus:
    def test_error_sets_error_icon_and_pops_a_notification(self, tray, monkeypatch):
        popped = {}
        monkeypatch.setattr(tray, 'show_notification',
                             lambda title, message, renew_period: popped.update(
                                 title=title, message=message, renew_period=renew_period))
        tray.set_status('error', 'DCS', message='boom')
        assert 'boom' in tray.icon.toolTip()
        assert popped == {'title': 'Error', 'message': 'boom', 'renew_period': 300}

    def test_paused_sets_paused_tooltip_and_pops_nothing(self, tray, monkeypatch):
        called = []
        monkeypatch.setattr(tray, 'show_notification', lambda *a, **k: called.append(a))
        tray.set_status('paused', 'MSFS')
        assert tray.icon.toolTip() == "VPforce TelemFFB\nMSFS is Paused "
        assert called == []

    def test_running_sets_running_tooltip(self, tray):
        tray.set_status('running', 'IL2')
        assert tray.icon.toolTip() == "VPforce TelemFFB\nIL2 is Running "

    def test_child_instance_has_no_tray_so_status_is_a_no_op(self, tray, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', False, raising=False)
        tray.icon.setToolTip("unchanged")
        tray.set_status('error', 'DCS', message='boom')
        assert tray.icon.toolTip() == "unchanged"


class TestShowNotification:
    def test_renews_after_the_period_but_not_before(self, tray, monkeypatch):
        import telemffb.ui.tray as tray_module
        t = [1000.0]
        monkeypatch.setattr(tray_module.time, 'time', lambda: t[0])

        tray.show_notification("T", "M", renew_period=10)
        first_time = tray._notifications[("T", "M")]

        t[0] += 5
        tray.show_notification("T", "M", renew_period=10)
        assert tray._notifications[("T", "M")] == first_time  # not renewed yet

        t[0] += 10
        tray.show_notification("T", "M", renew_period=10)
        assert tray._notifications[("T", "M")] == 1015.0
