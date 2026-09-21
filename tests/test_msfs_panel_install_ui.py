"""The MSFS panel installer's per-row path field and Install button.

Each row (telemffb/ui/dialogs/SystemSettingsDialog.py's
refresh_msfs_panel_installs() / _add_msfs_install_row()) is shaped like the
X-Plane install-path row: a label, an editable QLineEdit prefilled with the
discovered Community path (or empty when detection failed), a "..." browse
button and an Install/Update/Reinstall button. The bug this file guards
against is Install staying dead whenever detection failed even after the
user supplied a path by hand or by Browse - the line edit holding a
non-empty path, however it got there, is the only thing Install cares about.

find_msfs_installs() can come back with a detected install whose Community
folder it couldn't find (community_path is None), or with nothing detected
at all - its docstring says the caller should fall back to a manual path
either way; that fallback is covered here too. The override's persistence
through Save/reload is covered separately, in
test_msfs_panel_install_persistence.py - it drives the real dialog through
tests/test_tap_workflows.py's World harness, which does not get along with
being built in the same process as this file's disposable dialog fixture
(a PyQt6 widget-teardown ordering issue, not a bug in the code under test).
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G
from telemffb.tap import msfs_panel_install

pytestmark = [pytest.mark.unit]


class FakeSettings(dict):
    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def dialog(app, monkeypatch):
    settings = FakeSettings({'devpath_joystick': 'j', 'devpath_pedals': 'p',
                             'devpath_collective': 'c', 'devpath_trimwheel': ''})
    monkeypatch.setattr(G, 'system_settings', settings, raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None), ('is_exe', False)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
    dlg = SystemSettingsDialog()
    dlg.ensurePolished()
    yield dlg
    dlg.deleteLater()
    app.processEvents()


def _row_widgets(layout, cls):
    """Every top-level widget in the layout matching cls, plus their
    children - the empty-install label sits directly in the layout, while
    everything else is nested one row-container deep."""
    found = []
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if w is None:
            continue
        if isinstance(w, cls):
            found.append(w)
        found.extend(w.findChildren(cls))
    return found


def _install_buttons(dialog):
    return [b for b in _row_widgets(dialog.msfsInstallsLayout, QtWidgets.QPushButton)
            if b.text() in ("Install", "Update", "Reinstall")]


def _path_edits(dialog):
    return _row_widgets(dialog.msfsInstallsLayout, QtWidgets.QLineEdit)


FAILED_DETECTION = {'version': '2024', 'edition': 'Microsoft Store',
                    'usercfg_path': None, 'community_path': None,
                    'installed_panel_version': None}


def _successful_detection(path):
    return {'version': '2024', 'edition': 'Microsoft Store', 'usercfg_path': 'x',
            'community_path': path, 'installed_panel_version': None}


class TestPathField:
    def test_install_is_enabled_when_a_path_was_discovered(self, dialog, monkeypatch, tmp_path):
        community = tmp_path / "Community"
        community.mkdir()
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs',
                            lambda: [_successful_detection(str(community))])
        dialog.refresh_msfs_panel_installs()

        edits = _path_edits(dialog)
        assert edits[0].text() == str(community)
        assert _install_buttons(dialog)[0].isEnabled() is True

    def test_install_is_disabled_when_the_field_is_empty(self, dialog, monkeypatch, tmp_path):
        community = tmp_path / "Community"
        community.mkdir()
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs',
                            lambda: [_successful_detection(str(community))])
        dialog.refresh_msfs_panel_installs()

        _path_edits(dialog)[0].setText("")
        assert _install_buttons(dialog)[0].isEnabled() is False

    def test_typing_a_path_enables_install(self, dialog, monkeypatch, tmp_path):
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs',
                            lambda: [FAILED_DETECTION])
        dialog.refresh_msfs_panel_installs()
        assert _install_buttons(dialog)[0].isEnabled() is False

        _path_edits(dialog)[0].setText(str(tmp_path / "Community"))
        assert _install_buttons(dialog)[0].isEnabled() is True


class TestBrowseOverride:
    def test_a_browse_override_enables_install_after_failed_detection(
            self, dialog, monkeypatch, tmp_path):
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs',
                            lambda: [FAILED_DETECTION])
        dialog.refresh_msfs_panel_installs()
        # detection found the install but not its Community folder - the
        # only row is disabled until Browse (or typing) supplies a path
        assert _install_buttons(dialog)[0].isEnabled() is False

        community = tmp_path / "Community"
        community.mkdir()
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getExistingDirectory',
                            staticmethod(lambda *a, **k: str(community)))

        override_key = "2024|Microsoft Store"
        dialog._browse_msfs_community(override_key, None)

        assert dialog._msfs_community_overrides[override_key] == os.path.normpath(str(community))
        assert _path_edits(dialog)[0].text() == os.path.normpath(str(community))
        assert _install_buttons(dialog)[0].isEnabled() is True

    def test_a_path_that_does_not_look_like_community_asks_first(
            self, dialog, monkeypatch, tmp_path):
        """Add-on linkers and custom package paths are legitimate Community
        folders no signature test can always recognise - so a path that
        doesn't look like one is asked about, not refused."""
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs',
                            lambda: [FAILED_DETECTION])
        dialog.refresh_msfs_panel_installs()

        odd = tmp_path / "not_obviously_it"
        odd.mkdir()
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getExistingDirectory',
                            staticmethod(lambda *a, **k: str(odd)))

        asked = []

        def answer_no(*a, **k):
            asked.append(True)
            return QtWidgets.QMessageBox.StandardButton.No
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question', answer_no)

        dialog._browse_msfs_community("2024|Microsoft Store", None)
        assert asked, "an unrecognised folder must prompt before being used"
        assert "2024|Microsoft Store" not in dialog._msfs_community_overrides

        monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                            lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
        dialog._browse_msfs_community("2024|Microsoft Store", None)
        assert dialog._msfs_community_overrides["2024|Microsoft Store"] == os.path.normpath(str(odd))


class TestManualFallback:
    def test_no_installs_detected_adds_a_manual_row(self, dialog, monkeypatch):
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs', lambda: [])
        dialog.refresh_msfs_panel_installs()

        labels = _row_widgets(dialog.msfsInstallsLayout, QtWidgets.QLabel)
        texts = [l.text() for l in labels]
        assert any("No MSFS" in t for t in texts)
        manual_label = next(l for l in labels if l.text() == "Path to MSFS Community folder:")
        assert manual_label.toolTip() == "Manual Community Folder"
        buttons = _install_buttons(dialog)
        assert len(buttons) == 1
        assert buttons[0].isEnabled() is False   # nothing chosen yet

    def test_a_manual_browse_enables_install(self, dialog, monkeypatch, tmp_path):
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs', lambda: [])
        dialog.refresh_msfs_panel_installs()

        community = tmp_path / "Community"
        community.mkdir()
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getExistingDirectory',
                            staticmethod(lambda *a, **k: str(community)))
        dialog._browse_msfs_community("manual", None)

        assert dialog._msfs_community_overrides["manual"] == os.path.normpath(str(community))
        assert _install_buttons(dialog)[0].isEnabled() is True
