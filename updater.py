#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import json
import logging
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from zipfile import ZipFile

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout,
    QProgressBar, QPushButton, QLabel, QWidget, QCheckBox,
    QMessageBox, QPlainTextEdit,
)

DOWNLOAD_URL_BASE = "https://vpforcecontrols.com/downloads/TelemFFB/"
LATEST_JSON = "latest.json"
EXECUTABLE_NAME = "VPforce-TelemFFB.exe"
BACKUP_FOLDER = "_previous_version_backup"

STYLESHEET = """
QMainWindow, QWidget {
    background-color: #353535;
    color: #dddddd;
}
QPlainTextEdit {
    background-color: #1e1e1e;
    color: #cccccc;
    border: 1px solid #555555;
    border-radius: 4px;
    font-family: Consolas, "Cascadia Code", monospace;
    font-size: 9pt;
}
QProgressBar {
    border: 1px solid #555555;
    border-radius: 4px;
    background-color: #232323;
    text-align: center;
    color: #dddddd;
    height: 18px;
}
QProgressBar::chunk {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #6e1d6f, stop:1 #ab37c8
    );
    border-radius: 3px;
}
QLabel {
    color: #dddddd;
}
QCheckBox {
    color: #dddddd;
    spacing: 6px;
}
QPushButton {
    background-color: qlineargradient(
        spread:pad, x1:1, y1:1, x2:0, y2:0.04,
        stop:0 rgba(160, 0, 200, 255),
        stop:1 rgba(174, 106, 206, 255)
    );
    border-radius: 6px;
    padding: 4px 12px;
    color: white;
    border: 1px solid #9d30b3;
    min-width: 80px;
}
QPushButton:hover {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #f0b0f0, stop:0.5 #c07ec0, stop:1 #914b91
    );
    border: 1px solid #8e1da8;
    color: white;
}
QPushButton:pressed {
    background-color: qlineargradient(
        x1:0, y1:1, x2:1, y2:0,
        stop:0 #6e1d6f, stop:1 #ab37c8
    );
    border: 1px solid #ab37c8;
}
QPushButton:disabled {
    background-color: #484848;
    color: #888888;
    border: 1px solid #555555;
}
"""


def _resolve_app_path(debugpath=None):
    if debugpath:
        return debugpath
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _move_with_retry(src, dst, log=None, attempts=6, delay=0.5):
    """Move src to dst via os.rename, retrying transient locks.

    Fresh installs in OneDrive-synced folders (Desktop/Documents) and
    antivirus scans hold files for moments at a time - a retried rename
    rides those out.  Deliberately NEVER falls back to copy-then-delete
    the way shutil.move does: on a locked tree that fallback deletes what
    it can and dies on what it can't, gutting the installation
    (field-observed on Qt6Core.dll).  Raises the last OSError instead so
    the caller can roll back cleanly.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            os.rename(src, dst)
            return
        except OSError as e:
            last_error = e
            if log and attempt < attempts - 1:
                log(f"  Locked: {os.path.basename(src)} - retrying "
                    f"({attempt + 1}/{attempts - 1})...")
            time.sleep(delay)
    raise last_error


def _move_tree_per_file(src, dst, log=None, moved=None):
    """Move a directory by walking it and renaming individual files.

    The all-or-nothing directory rename fails if ANY handle exists anywhere
    in the tree - and on freshly-downloaded installs, antivirus (Mark-of-
    the-Web scanning) or OneDrive sync hold handles somewhere in the tree
    almost continuously.  Per-file renames only contend with the one file a
    scanner is touching at that moment, so retries actually succeed (this is
    how the old flat-layout updater survived Desktop installs for years).

    Appends every completed rename to ``moved`` as (dst, src) so the caller
    can roll back.  Raises OSError on a file that stays locked.
    """
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_root = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_root, exist_ok=True)
        for fname in files:
            f_src = os.path.join(root, fname)
            f_dst = os.path.join(target_root, fname)
            _move_with_retry(f_src, f_dst, log=log)
            if moved is not None:
                moved.append((f_dst, f_src))
    # remove the now-empty source tree (only empty dirs remain)
    shutil.rmtree(src, ignore_errors=True)


def _rollback_moves(moved, log=None):
    """Undo a partial backup: move (backup_path, original_path) pairs home,
    newest first.  Best-effort - logs anything it cannot restore."""
    for backup_path, original_path in reversed(moved):
        try:
            os.makedirs(os.path.dirname(original_path), exist_ok=True)
            os.rename(backup_path, original_path)
        except OSError:
            if log:
                log(f"  ROLLBACK FAILED for {original_path} - restore it "
                    f"manually from {BACKUP_FOLDER}")


def fetch_latest_version(current_version):
    """Returns (latest_version, download_url), or raises RuntimeError."""
    send_url = DOWNLOAD_URL_BASE + LATEST_JSON
    try:
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(send_url, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        latest_version = data["version"]
        latest_url = DOWNLOAD_URL_BASE + data["filename"]
    except Exception as e:
        raise RuntimeError(f"Could not fetch version info from {send_url}:\n{e}") from e

    if latest_version == current_version:
        raise RuntimeError(f"Already up to date (version {current_version}).")

    return latest_version, latest_url


class UpdateWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    log = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, url, app_path, debug_zip=None):
        super().__init__()
        self.url = url
        self.app_path = app_path
        self.debug_zip = debug_zip
        self._folder_snapshot = os.listdir(app_path)

    def run(self):
        try:
            zip_path = self._download()
            self.progress.emit(0)
            self._wait_for_app_exit()
            self._backup()
            self.progress.emit(0)
            self._extract(zip_path)
            if self.debug_zip is None:
                os.remove(zip_path)
            self.progress.emit(100)
            self.status.emit("Update complete!")
            self.finished.emit()
        except Exception as e:
            logging.exception("Update failed")
            self.error.emit(str(e))

    def _download(self):
        if self.debug_zip:
            self.log.emit(f"Debug mode: using local zip {self.debug_zip}")
            self.progress.emit(100)
            return self.debug_zip

        self.status.emit("Downloading update...")
        self.log.emit(f"Downloading from {self.url}")
        zip_fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="telemffb_update_")
        os.close(zip_fd)

        ctx = ssl._create_unverified_context()
        resp = urllib.request.urlopen(self.url, context=ctx)
        total = int(resp.headers.get("Content-Length", 0))
        received = 0

        with open(zip_path, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                if total:
                    self.progress.emit(int(received / total * 100))

        self.progress.emit(100)
        self.log.emit(f"Download complete ({received:,} bytes).")
        return zip_path

    def _wait_for_app_exit(self, timeout=20):
        """Wait for all TelemFFB instances (master and children) to exit
        before touching the installation - a running instance holds every
        DLL in the install tree.  Aborts cleanly (nothing modified) if one
        is still alive after the timeout."""
        self.status.emit("Waiting for TelemFFB to close...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {EXECUTABLE_NAME}"],
                    capture_output=True, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                return  # cannot check; proceed rather than block updates
            if EXECUTABLE_NAME.lower() not in result.stdout.lower():
                self.log.emit("All TelemFFB instances have exited.")
                return
            time.sleep(0.5)
        raise RuntimeError(
            f"{EXECUTABLE_NAME} is still running after {timeout} seconds.\n\n"
            "Close all TelemFFB instances (including child device instances) "
            "and run the update again.\n\n"
            "No changes have been made to your installation."
        )

    def _backup(self):
        self.status.emit("Backing up current version...")
        backup_path = os.path.join(self.app_path, BACKUP_FOLDER)
        if os.path.exists(backup_path):
            shutil.rmtree(backup_path)
        os.makedirs(backup_path)
        self.log.emit(f"Backing up to {backup_path}")

        items = [n for n in self._folder_snapshot if n not in ("updater.exe", BACKUP_FOLDER)]
        moved = []  # (backup_path, original_path) for rollback
        try:
            for i, name in enumerate(items):
                src = os.path.join(self.app_path, name)
                dst = os.path.join(backup_path, name)
                if name.endswith(".ini") and name != "config.ini":
                    self.status.emit(f"Preserving: {name}")
                    shutil.copy(src, dst)
                    self.log.emit(f"  Preserved (copy): {name}")
                elif os.path.isdir(src):
                    self.status.emit(f"Backing up: {name}")
                    try:
                        # fast path: whole-directory rename
                        _move_with_retry(src, dst, log=None, attempts=2, delay=0.25)
                        moved.append((dst, src))
                    except OSError:
                        # tree is being held open somewhere (AV scanning the
                        # fresh download, OneDrive sync) - move file-by-file
                        self.log.emit(f"  {name} is busy - moving file-by-file...")
                        _move_tree_per_file(src, dst, log=self.log.emit, moved=moved)
                    self.log.emit(f"  Backed up: {name}")
                else:
                    self.status.emit(f"Backing up: {name}")
                    _move_with_retry(src, dst, log=self.log.emit)
                    moved.append((dst, src))
                    self.log.emit(f"  Backed up: {name}")
                self.progress.emit(int((i + 1) / len(items) * 100))
        except OSError as e:
            locked = getattr(e, "filename", None) or "a file in the installation"
            self.status.emit("Update aborted - restoring files...")
            self.log.emit(f"Backup failed on {locked}; rolling back.")
            _rollback_moves(moved, log=self.log.emit)
            self.log.emit("Rollback complete - installation unchanged.")
            raise RuntimeError(
                f"Could not back up the current installation:\n\n{locked}\n"
                "is locked by another program.\n\n"
                "Common causes: the installation is in a OneDrive-synced folder "
                "(such as Desktop or Documents) still syncing recently added "
                "files, an antivirus scan in progress, or another TelemFFB "
                "instance still running.\n\n"
                "Your installation has been restored - wait a moment and try "
                "the update again."
            ) from e

    def _extract(self, zip_path):
        self.status.emit("Extracting update...")

        # base_library.zip must exist on disk before PyInstaller extraction can proceed
        try:
            os.makedirs(os.path.join(self.app_path, "assets"), exist_ok=True)
            shutil.copy(
                os.path.join(self.app_path, BACKUP_FOLDER, "assets", "base_library.zip"),
                os.path.join(self.app_path, "assets", "base_library.zip"),
            )
        except Exception:
            pass

        temp_dir = tempfile.mkdtemp(dir=self.app_path)
        try:
            self.status.emit("Extracting archive...")
            with ZipFile(zip_path, "r") as zf:
                entries = zf.namelist()
                total = len(entries)
                for i, entry in enumerate(entries):
                    zf.extract(entry, temp_dir)
                    self.status.emit(f"Extracting: {entry}")
                    self.progress.emit(int((i + 1) / total * 50))  # extraction = 0→50%

            install_items = os.listdir(temp_dir)
            for i, item in enumerate(install_items):
                src = os.path.join(temp_dir, item)
                dst = os.path.join(self.app_path, item)
                self.status.emit(f"Installing: {item}")
                if os.path.isdir(src):
                    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst, follow_symlinks=False)
                self.log.emit(f"  Installed: {item}")
                self.progress.emit(50 + int((i + 1) / len(install_items) * 50))  # install = 50→100%
        finally:
            self.status.emit("Cleaning up...")
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.log.emit("Cleanup done.")


class UpdaterWindow(QMainWindow):
    def __init__(self, current_version, latest_version, download_url,
                 app_path, telemffb_args, debug_zip=None):
        super().__init__()
        self.download_url = download_url
        self.app_path = app_path
        self.telemffb_args = telemffb_args
        self.debug_zip = debug_zip

        self.setWindowTitle("VPforce TelemFFB Updater")
        self.setFixedSize(560, 460)

        icon_path = os.path.join(app_path, "image", "vpforceicon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self._build_ui(icon_path, current_version, latest_version)

    def _build_ui(self, icon_path, current_version, latest_version):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        # Header: logo + version info
        header = QHBoxLayout()
        logo_label = QLabel()
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            logo_label.setPixmap(
                pixmap.scaledToHeight(64, Qt.TransformationMode.SmoothTransformation)
            )
        logo_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        logo_label.setFixedWidth(72)
        header.addWidget(logo_label)

        info_layout = QVBoxLayout()
        title = QLabel("<b>VPforce TelemFFB Updater</b>")
        title.setStyleSheet("font-size: 13pt; color: #dddddd;")
        info_layout.addWidget(title)
        info_layout.addWidget(QLabel(f"Installed version:   {current_version}"))
        info_layout.addWidget(QLabel(f"Available version:   <b>{latest_version}</b>"))
        info_layout.addStretch()
        header.addLayout(info_layout)
        header.addStretch()
        layout.addLayout(header)

        # Log panel
        self.log_panel = QPlainTextEdit()
        self.log_panel.setReadOnly(True)
        layout.addWidget(self.log_panel)

        # Status + progress
        self.status_label = QLabel("Ready.")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Options
        self.launch_checkbox = QCheckBox("Launch TelemFFB after update")
        self.launch_checkbox.setChecked(True)
        self.release_notes_checkbox = QCheckBox("Open release notes after update")
        self.release_notes_checkbox.setChecked(False)
        layout.addWidget(self.launch_checkbox)
        layout.addWidget(self.release_notes_checkbox)

        # Buttons
        btn_row = QHBoxLayout()
        self.exit_btn = QPushButton("Exit")
        self.exit_btn.clicked.connect(self.close)
        self.update_btn = QPushButton("Update Now")
        self.update_btn.clicked.connect(self._start_update)
        self.finish_btn = QPushButton("Finish")
        self.finish_btn.clicked.connect(self._finish)
        self.finish_btn.setVisible(False)

        btn_row.addWidget(self.exit_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.update_btn)
        btn_row.addWidget(self.finish_btn)
        layout.addLayout(btn_row)

    def _append_log(self, text):
        self.log_panel.appendPlainText(text)
        sb = self.log_panel.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _start_update(self):
        self.update_btn.setEnabled(False)
        self.exit_btn.setEnabled(False)
        self.progress_bar.setValue(0)

        self.worker = UpdateWorker(self.download_url, self.app_path, self.debug_zip)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.status.connect(self.status_label.setText)
        self.worker.log.connect(self._append_log)
        self.worker.finished.connect(self._on_update_done)
        self.worker.error.connect(self._on_update_error)
        self.worker.start()

    def _on_update_done(self):
        self.progress_bar.setValue(100)
        self.exit_btn.setEnabled(True)
        if self.launch_checkbox.isChecked():
            self._finish()
        else:
            self.update_btn.setVisible(False)
            self.finish_btn.setVisible(True)

    def _on_update_error(self, message):
        self.exit_btn.setEnabled(True)
        self.update_btn.setEnabled(True)
        QMessageBox.critical(
            self, "Update Failed",
            f"The update did not complete:\n\n{message}\n\n"
            f"Any files that were moved aside during the update are in the "
            f"{BACKUP_FOLDER} folder."
        )

    def _finish(self):
        if self.launch_checkbox.isChecked():
            exe = os.path.join(self.app_path, EXECUTABLE_NAME)
            subprocess.Popen([exe] + self.telemffb_args, cwd=self.app_path)

        if self.release_notes_checkbox.isChecked():
            rn = os.path.join(self.app_path, "_RELEASE_NOTES.txt")
            if os.path.exists(rn):
                subprocess.Popen(["notepad.exe", rn])

        self.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="TelemFFB Updater")
    parser.add_argument("--url", default=None)
    parser.add_argument("--debugzip", default=None)
    parser.add_argument("--debugpath", default=None)
    parser.add_argument("--current_version", default="unknown")
    args, telemffb_args = parser.parse_known_args()

    if telemffb_args:
        logging.info(f"Pass-through args from TelemFFB: {telemffb_args}")

    app_path = _resolve_app_path(args.debugpath)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],
    )

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    if args.current_version == "unknown" and getattr(sys, 'frozen', False):
        QMessageBox.information(
            None, "VPforce TelemFFB Updater",
            "This updater is launched automatically by TelemFFB when an update is available.\n\n"
            "Please start TelemFFB normally — you will be prompted to update if a new version exists."
        )
        sys.exit(0)

    download_url = args.url
    latest_version = None

    if download_url is None and args.debugzip is None:
        try:
            latest_version, download_url = fetch_latest_version(args.current_version)
        except RuntimeError as e:
            QMessageBox.critical(None, "Updater Error", str(e))
            sys.exit(1)
    else:
        latest_version = args.current_version  # debug/direct-URL mode

    window = UpdaterWindow(
        current_version=args.current_version,
        latest_version=latest_version,
        download_url=download_url,
        app_path=app_path,
        telemffb_args=telemffb_args,
        debug_zip=args.debugzip,
    )
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
