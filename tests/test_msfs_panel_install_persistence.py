"""The MSFS Community-folder override survives Save and a reopen.

refresh_msfs_panel_installs() holds a Browse override in an instance dict
that only reaches settings on Save (see SystemSettingsDialog.__init__'s
_msfs_community_overrides comment) - this drives the real dialog through
tests/test_tap_workflows.py's World harness (the only fixture in this repo
that exercises a full save_settings()/load_settings() round trip without
refusal) to check the override actually makes that round trip, under the
msfsCommunityOverrides key.

Kept out of test_msfs_panel_install_ui.py: building a World there, in the
same process as that file's disposable per-test dialog fixture, hits a
PyQt6 widget-teardown ordering crash (QPropertyAnimation already deleted)
that is a test-isolation issue, not a defect in the code under test - see
that file's module docstring.
"""
import json
import os
import random

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.tap import msfs_panel_install
from tests.test_tap_workflows import World

pytestmark = [pytest.mark.unit]

#: A detected install whose Community folder detection couldn't find -
#: exactly the case Browse exists to work around.
FAILED_DETECTION = {'version': '2024', 'edition': 'Microsoft Store',
                    'usercfg_path': None, 'community_path': None,
                    'installed_panel_version': None}


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestPersistence:
    def test_override_survives_save_and_a_fresh_reload(self, app, tmp_path, monkeypatch):
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs',
                            lambda: [FAILED_DETECTION])
        world = World(tmp_path, monkeypatch, random.Random(0))

        community = tmp_path / "Community"
        community.mkdir()
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getExistingDirectory',
                            staticmethod(lambda *a, **k: str(community)))
        override_key = "2024|Microsoft Store"
        world.dialog._browse_msfs_community(override_key, None)

        assert world.save()

        stored = json.loads(world.settings['msfsCommunityOverrides'])
        assert stored[override_key] == os.path.normpath(str(community))

        # a brand-new dialog reading the same settings back, the way
        # reopening System Settings would
        world.open()
        assert world.dialog._msfs_community_overrides[override_key] == \
            os.path.normpath(str(community))

    def test_a_typed_path_survives_save_and_a_fresh_reload(self, app, tmp_path, monkeypatch):
        """The line edit is editable, not just Browse-fillable - a path the
        user typed by hand and never ran through Browse must round-trip
        the same way, see save_settings()'s
        _sync_msfs_overrides_from_rows() call."""
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs',
                            lambda: [FAILED_DETECTION])
        world = World(tmp_path, monkeypatch, random.Random(0))

        override_key = "2024|Microsoft Store"
        typed = str(tmp_path / "TypedCommunity")
        world.dialog._msfs_install_rows[override_key].path_edit.setText(typed)

        assert world.save()

        stored = json.loads(world.settings['msfsCommunityOverrides'])
        assert stored[override_key] == typed

        world.open()
        assert world.dialog._msfs_community_overrides[override_key] == typed


class _ImportedSettings(dict):
    """A settings file standing in for the store, as import_settings()
    passes one: the instance panels read it with an ``instance=`` keyword
    that a plain dict's get() won't take."""

    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)


class TestReloadReplacesTheRows:
    """A reload must show what it loaded, not what the old rows held.

    refresh_msfs_panel_installs() opens by folding the live rows' text
    back into _msfs_community_overrides, which is right for the rebuild
    after an install (it keeps a path typed but not yet saved). After
    load_settings() has just replaced that dict, though, the rows still
    belong to the previous settings, so the same fold copies stale text
    over what was loaded - File > Reset to Defaults kept the old override
    and File > Import Settings silently dropped the imported one.
    """

    OVERRIDE_KEY = "2024|Microsoft Store"

    def _world_with_override(self, tmp_path, monkeypatch, path):
        monkeypatch.setattr(msfs_panel_install, 'find_msfs_installs',
                            lambda: [FAILED_DETECTION])
        world = World(tmp_path, monkeypatch, random.Random(0))
        world.dialog._msfs_install_rows[self.OVERRIDE_KEY].path_edit.setText(path)
        return world

    def test_reset_to_defaults_clears_the_path(self, app, tmp_path, monkeypatch):
        world = self._world_with_override(tmp_path, monkeypatch,
                                          str(tmp_path / "Custom"))
        world.dialog.reset_settings()
        assert self.OVERRIDE_KEY not in world.dialog._msfs_community_overrides
        assert world.dialog._msfs_install_rows[self.OVERRIDE_KEY].path_edit.text() == ""

    def test_an_imported_override_reaches_the_field(self, app, tmp_path, monkeypatch):
        world = self._world_with_override(tmp_path, monkeypatch,
                                          str(tmp_path / "Custom"))
        imported = str(tmp_path / "Imported")
        world.dialog.load_settings(source=_ImportedSettings({
            'msfsCommunityOverrides': json.dumps({self.OVERRIDE_KEY: imported})}))
        assert world.dialog._msfs_community_overrides[self.OVERRIDE_KEY] == imported
        assert world.dialog._msfs_install_rows[self.OVERRIDE_KEY].path_edit.text() == imported
