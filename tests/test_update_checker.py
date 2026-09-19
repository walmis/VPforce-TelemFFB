"""UpdateChecker (telemffb/ui/updates.py) - extracted from MainWindow's
version-check flow (on_version_check_cancelled/update_version_result/
on_version_check_error/perform_update/update_from_menu).

Covers the emit-exactly-once guarantee across all three resolution paths
(result, error, user-cancel) and the "Install Latest TelemFFB" action's
enable/text update on a newer-version result vs an up-to-date one.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

import telemffb.globals as G
from telemffb.ui.updates import UpdateChecker

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeMainWindow(QWidget):
    """Stands in for MainWindow: UpdateChecker only needs a real QObject to
    parent to, plus the version_label it writes status text into."""

    def __init__(self):
        super().__init__()
        self.version_label = QLabel()


@pytest.fixture
def checker(qapp, monkeypatch):
    monkeypatch.setattr(G, 'release_version', False, raising=False)
    monkeypatch.setattr(G, 'child_instance', False, raising=False)
    monkeypatch.setattr(G, 'system_settings', {}, raising=False)
    mw = FakeMainWindow()
    return UpdateChecker(mw)


class TestEmitExactlyOnce:
    def test_result_path_emits_once(self, checker):
        seen = []
        checker.version_check_complete.connect(lambda: seen.append(1))
        checker.update_version_result("uptodate", "uptodate")
        assert seen == [1]

    def test_error_path_emits_once(self, checker):
        seen = []
        checker.version_check_complete.connect(lambda: seen.append(1))
        checker.on_version_check_error("boom")
        assert seen == [1]

    def test_cancel_path_emits_once(self, checker):
        seen = []
        checker.version_check_complete.connect(lambda: seen.append(1))
        checker.on_version_check_cancelled()
        assert seen == [1]

    def test_second_path_after_first_does_not_emit_again(self, checker):
        """A late result/error/cancel racing in after the signal has already
        resolved must not double-emit."""
        seen = []
        checker.version_check_complete.connect(lambda: seen.append(1))
        checker.on_version_check_error("boom")
        checker.update_version_result("uptodate", "uptodate")
        checker.on_version_check_cancelled()
        assert seen == [1]


class TestUpdateAction:
    def test_newer_version_enables_action_and_sets_install_text(self, checker):
        action = QAction("Install Latest TelemFFB")
        action.setDisabled(True)
        checker.bind_action(action)

        checker.update_version_result("9.9.9", "http://example.com/download")

        assert action.isEnabled()
        assert action.text() == "Install Latest TelemFFB"
        assert checker.latest_version == "9.9.9"

    def test_uptodate_leaves_action_disabled(self, checker):
        action = QAction("Install Latest TelemFFB")
        action.setDisabled(True)
        checker.bind_action(action)

        checker.update_version_result("uptodate", "uptodate")

        assert not action.isEnabled()

    def test_no_bound_action_does_not_raise(self, checker):
        """bind_action is never called (e.g. release build skips the menu
        item entirely) - the result handler must tolerate that."""
        checker.update_version_result("9.9.9", "http://example.com/download")
        assert checker.latest_version == "9.9.9"
