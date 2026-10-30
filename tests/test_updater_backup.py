"""Updater hardening: retried renames with no copy+delete fallback, backup
rollback on lock failure (a failed update must leave a launchable install),
and the wait-for-app-exit gate."""
import os
import stat
import subprocess
import sys
import types

import pytest

import updater
from updater import UpdateWorker, _move_with_retry, _remove_tree_with_retry, _rollback_moves

pytestmark = [pytest.mark.unit]

#: POSIX does not honor an open handle as a lock - rename and unlink
#: succeed on a held file - so the lock-failure paths below only exist
#: where the OS creates them
LOCK_TESTS = pytest.mark.skipif(
    sys.platform != "win32",
    reason="an open handle only blocks rename/delete on Windows")


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    # retries stay logically intact but don't sleep in tests
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


FOREIGN_FILE = "options.lua"
FOREIGN_DIR = "Mods"


def _make_install(tmp_path):
    """A miniature install tree in the release layout - the executable, the
    assets dir, the release notes - unzipped into a folder that also holds
    files of the user's own."""
    app = tmp_path / "install"
    app.mkdir()
    (app / updater.EXECUTABLE_NAME).write_text("exe")
    assets = app / "assets"
    (assets / "PyQt6").mkdir(parents=True)
    (assets / "PyQt6" / "Qt6Core.dll").write_text("dll")
    (assets / "base_library.zip").write_text("stdlib")
    (assets / "defaults.xml").write_text("x")
    (app / "_RELEASE_NOTES.txt").write_text("notes")
    (app / FOREIGN_FILE).write_text("user data")
    (app / FOREIGN_DIR / "aircraft").mkdir(parents=True)
    (app / FOREIGN_DIR / "aircraft" / "entry.lua").write_text("mod")
    return app


def _foreign_untouched(app):
    backup = app / updater.BACKUP_FOLDER
    return ((app / FOREIGN_FILE).read_text() == "user data"
            and (app / FOREIGN_DIR / "aircraft" / "entry.lua").exists()
            and not (backup / FOREIGN_FILE).exists()
            and not (backup / FOREIGN_DIR).exists())


class TestMoveWithRetry:
    def test_moves_when_unlocked(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("1")
        _move_with_retry(str(src), str(tmp_path / "b.txt"))
        assert not src.exists() and (tmp_path / "b.txt").exists()

    @LOCK_TESTS
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
        w.status = types.SimpleNamespace(emit=lambda *a: None)
        w.log = types.SimpleNamespace(emit=lambda *a: None)
        w.progress = types.SimpleNamespace(emit=lambda *a: None)
        return w

    def test_backup_moves_only_the_releases_own_entries(self, tmp_path):
        app = _make_install(tmp_path)
        w = self._worker(app)
        w._backup()
        backup = app / updater.BACKUP_FOLDER
        assert (backup / updater.EXECUTABLE_NAME).exists()
        assert (backup / "assets" / "PyQt6" / "Qt6Core.dll").exists()
        assert (backup / "assets" / "defaults.xml").exists()
        assert (backup / "_RELEASE_NOTES.txt").exists()
        assert not (app / "assets").exists()
        assert not (app / updater.EXECUTABLE_NAME).exists()
        assert _foreign_untouched(app)

    def test_missing_release_notes_are_not_a_failure(self, tmp_path):
        app = _make_install(tmp_path)
        (app / "_RELEASE_NOTES.txt").unlink()
        self._worker(app)._backup()
        backup = app / updater.BACKUP_FOLDER
        assert (backup / "assets" / "PyQt6" / "Qt6Core.dll").exists()
        assert not (backup / "_RELEASE_NOTES.txt").exists()

    def test_a_folder_without_telemffb_files_backs_up_nothing(self, tmp_path):
        app = tmp_path / "install"
        app.mkdir()
        (app / FOREIGN_FILE).write_text("user data")
        self._worker(app)._backup()
        assert (app / updater.BACKUP_FOLDER).is_dir()
        assert os.listdir(app / updater.BACKUP_FOLDER) == []
        assert (app / FOREIGN_FILE).read_text() == "user data"

    @LOCK_TESTS
    def test_a_locked_foreign_file_does_not_block_the_update(self, tmp_path):
        app = _make_install(tmp_path)
        with open(app / FOREIGN_FILE, "r"):
            self._worker(app)._backup()
        assert (app / updater.BACKUP_FOLDER / "assets" / "PyQt6" / "Qt6Core.dll").exists()
        assert _foreign_untouched(app)

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

    @LOCK_TESTS
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

    @LOCK_TESTS
    def test_locked_file_aborts_and_restores_install(self, tmp_path):
        app = _make_install(tmp_path)
        w = self._worker(app)
        # the release notes are backed up last: the executable and the
        # assets have been moved by the time the lock is hit
        locked = app / "_RELEASE_NOTES.txt"
        with open(locked, "r"):
            with pytest.raises(RuntimeError, match="locked by another program"):
                w._backup()
        # the cardinal rule: the install is intact after a failed update
        assert (app / updater.EXECUTABLE_NAME).exists()
        assert (app / "assets" / "PyQt6" / "Qt6Core.dll").exists()
        assert (app / "assets" / "defaults.xml").exists()
        assert locked.exists()
        assert _foreign_untouched(app)

    @LOCK_TESTS
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
        assert (app / updater.EXECUTABLE_NAME).exists()


class TestStaleBackupRemoval:
    """The backup folder left by a previous update is cleared before the new
    backup; read-only entries are deleted anyway, and a folder that stays
    put stops the update before any file has moved."""

    def _worker(self, app_path):
        w = UpdateWorker.__new__(UpdateWorker)
        w.app_path = str(app_path)
        w.status = types.SimpleNamespace(emit=lambda *a: None)
        w.log = types.SimpleNamespace(emit=lambda *a: None)
        w.progress = types.SimpleNamespace(emit=lambda *a: None)
        return w

    @pytest.mark.skipif(sys.platform != "win32", reason="the read-only attribute blocks deletion on Windows")
    def test_read_only_entries_are_removed(self, tmp_path):
        tree = tmp_path / "old_backup"
        (tree / "assets" / "numpy" / "fft").mkdir(parents=True)
        f = tree / "assets" / "numpy" / "fft" / "x.pyd"
        f.write_text("1")
        os.chmod(f, stat.S_IREAD)
        os.chmod(tree / "assets" / "numpy" / "fft", stat.S_IREAD)
        _remove_tree_with_retry(str(tree))
        assert not tree.exists()

    def test_unremovable_backup_aborts_before_any_move(self, tmp_path, monkeypatch):
        app = _make_install(tmp_path)
        stale = app / updater.BACKUP_FOLDER
        stale.mkdir()
        (stale / "leftover.txt").write_text("old")
        before = sorted(p.relative_to(app) for p in app.rglob("*"))

        def refuse(path, attempts=3, delay=0.5):
            raise PermissionError(5, "Access is denied", str(stale / "leftover.txt"))
        monkeypatch.setattr(updater, "_remove_tree_with_retry", refuse)

        with pytest.raises(RuntimeError) as info:
            self._worker(app)._backup()
        assert str(stale / "leftover.txt") in str(info.value)
        assert sorted(p.relative_to(app) for p in app.rglob("*")) == before


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
