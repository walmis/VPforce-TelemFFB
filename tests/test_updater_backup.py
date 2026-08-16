"""Updater hardening: retried renames with no copy+delete fallback, backup
rollback on lock failure (a failed update must leave a launchable install),
and the wait-for-app-exit gate."""
import os
import subprocess
import types

import pytest

import updater
from updater import UpdateWorker, _move_with_retry, _rollback_moves

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    # retries stay logically intact but don't sleep in tests
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


def _make_install(tmp_path):
    """A miniature install tree: a data file, an assets dir, an ini pair."""
    app = tmp_path / "install"
    app.mkdir()
    (app / "defaults.xml").write_text("x")
    assets = app / "assets"
    (assets / "PyQt6").mkdir(parents=True)
    (assets / "PyQt6" / "Qt6Core.dll").write_text("dll")
    (assets / "base_library.zip").write_text("stdlib")
    (app / "config.ini").write_text("cfg")
    (app / "userconfig.ini").write_text("user")
    return app


class TestMoveWithRetry:
    def test_moves_when_unlocked(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("1")
        _move_with_retry(str(src), str(tmp_path / "b.txt"))
        assert not src.exists() and (tmp_path / "b.txt").exists()

    def test_raises_after_retries_without_destroying_source(self, tmp_path):
        src = tmp_path / "locked.txt"
        src.write_text("1")
        with open(src, "r"):  # open handle blocks rename on Windows
            with pytest.raises(OSError):
                _move_with_retry(str(src), str(tmp_path / "b.txt"), attempts=3)
        assert src.exists()  # never copy+delete: the source survives intact


class TestBackupRollback:
    def _worker(self, app_path):
        w = UpdateWorker.__new__(UpdateWorker)  # skip QThread download wiring
        w.app_path = str(app_path)
        w._folder_snapshot = os.listdir(app_path)
        w.status = types.SimpleNamespace(emit=lambda *a: None)
        w.log = types.SimpleNamespace(emit=lambda *a: None)
        w.progress = types.SimpleNamespace(emit=lambda *a: None)
        return w

    def test_clean_backup_moves_everything(self, tmp_path):
        app = _make_install(tmp_path)
        w = self._worker(app)
        w._backup()
        backup = app / updater.BACKUP_FOLDER
        assert (backup / "assets" / "PyQt6" / "Qt6Core.dll").exists()
        assert (backup / "defaults.xml").exists()
        # ini preservation: copied, original left in place
        assert (app / "userconfig.ini").exists() and (backup / "userconfig.ini").exists()
        assert not (app / "assets").exists()

    def test_dir_lock_falls_back_to_per_file_move(self, tmp_path, monkeypatch):
        # the field case: the assets DIRECTORY rename fails persistently
        # (AV/OneDrive handle somewhere in the tree) but individual files
        # are movable - the per-file walk must complete the backup
        app = _make_install(tmp_path)
        w = self._worker(app)
        assets_path = str(app / "assets")
        real_rename = os.rename

        def rename_blocking_dir(src, dst, *a, **k):
            if os.path.normpath(src) == os.path.normpath(assets_path):
                raise PermissionError(5, "Access is denied", src)
            return real_rename(src, dst)

        monkeypatch.setattr(updater.os, "rename", rename_blocking_dir)
        w._backup()
        backup = app / updater.BACKUP_FOLDER
        assert (backup / "assets" / "PyQt6" / "Qt6Core.dll").exists()
        assert not (app / "assets").exists()  # emptied tree removed

    def test_runtime_held_base_library_is_preserved_not_moved(self, tmp_path, monkeypatch):
        # THE field failure: the updater's own bootloader holds
        # assets/base_library.zip open - rename denied through every retry,
        # while read (for the backup copy) and overwrite remain permitted.
        # The backup must complete with the file left in place.
        app = _make_install(tmp_path)
        w = self._worker(app)
        base_lib = app / "assets" / "base_library.zip"
        assets_path = str(app / "assets")
        real_rename = os.rename

        def rename_blocking(src, dst, *a, **k):
            # both the dir rename and the held file's rename are denied
            if os.path.normpath(src) in (os.path.normpath(assets_path),
                                         os.path.normpath(str(base_lib))):
                raise PermissionError(5, "Access is denied", src)
            return real_rename(src, dst)

        monkeypatch.setattr(updater.os, "rename", rename_blocking)
        with open(base_lib, "r"):  # real held handle, like the bootloader's
            w._backup()
        backup = app / updater.BACKUP_FOLDER
        assert base_lib.exists()                          # left for the runtime
        assert (backup / "assets" / "base_library.zip").exists()  # backed up by copy
        assert (backup / "assets" / "PyQt6" / "Qt6Core.dll").exists()

    def test_locked_file_aborts_and_restores_install(self, tmp_path):
        app = _make_install(tmp_path)
        w = self._worker(app)
        locked = app / "zz_locked.dat"  # sorts last: earlier items get moved first
        locked.write_text("busy")
        w._folder_snapshot = sorted(os.listdir(app))
        with open(locked, "r"):
            with pytest.raises(RuntimeError, match="locked by another program"):
                w._backup()
        # the cardinal rule: the install is intact after a failed update
        assert (app / "assets" / "PyQt6" / "Qt6Core.dll").exists()
        assert (app / "defaults.xml").exists()
        assert (app / "config.ini").exists()
        assert locked.exists()

    def test_locked_file_inside_dir_aborts_and_restores(self, tmp_path):
        # per-file fallback hits a file that stays locked: abort + full
        # restore, including files already renamed out of the tree
        app = _make_install(tmp_path)
        w = self._worker(app)
        locked = app / "assets" / "PyQt6" / "locked.dll"
        locked.write_text("busy")
        with open(locked, "r"):
            with pytest.raises(RuntimeError, match="locked by another program"):
                w._backup()
        assert (app / "assets" / "PyQt6" / "Qt6Core.dll").exists()
        assert locked.exists()
        assert (app / "defaults.xml").exists()


class TestWaitForAppExit:
    def _worker(self):
        w = UpdateWorker.__new__(UpdateWorker)
        w.status = types.SimpleNamespace(emit=lambda *a: None)
        w.log = types.SimpleNamespace(emit=lambda *a: None)
        return w

    def test_returns_when_no_instances(self):
        # the real tasklist: VPforce-TelemFFB.exe is not running in CI/test
        self._worker()._wait_for_app_exit(timeout=5)

    def test_times_out_cleanly_when_instance_persists(self, monkeypatch):
        w = self._worker()
        fake = types.SimpleNamespace(stdout=f"{updater.EXECUTABLE_NAME} 1234 Console")
        monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: fake)
        # make the deadline expire immediately after the first check
        times = iter([0.0, 0.0, 100.0])
        monkeypatch.setattr(updater.time, "time", lambda: next(times, 100.0))
        with pytest.raises(RuntimeError, match="still running"):
            w._wait_for_app_exit(timeout=1)
