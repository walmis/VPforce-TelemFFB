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

"""UpdateChecker: the startup version-check flow and the self-update launch
that used to live inline on MainWindow (``on_version_check_cancelled``,
``update_version_result``, ``on_version_check_error``, ``perform_update``,
``update_from_menu``).

Like ``TrayController``/``MainMenu``, this takes the owning ``MainWindow`` as
``mainwindow`` and reads/writes its widgets (``version_label``) directly
rather than duplicating that state here. The "Install Latest TelemFFB" menu
action is *not* mirrored onto MainWindow; ``MainMenu`` builds it and hands it
over via ``bind_action`` so this module can enable/relabel it once an update
is found.

``start()`` replaces the version-check kickoff that used to live in
``main.py``'s ``_check_version_update``: it does the master/release/dev/frozen
gating itself and either launches the background fetch or emits
``version_check_complete`` immediately. That signal is guaranteed to fire
exactly once regardless of which path (result, error, or user-cancel)
resolves it.
"""

import logging
import os
import shutil
import subprocess
import sys

from PyQt6.QtCore import QCoreApplication, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

import telemffb.globals as G
import telemffb.utils as utils
from telemffb.utils import exit_application


class UpdateChecker(QObject):
    version_check_complete = pyqtSignal()

    def __init__(self, mainwindow):
        super().__init__(mainwindow)
        self.mw = mainwindow
        self.latest_version = None
        self._update_available = None
        self._version_check_resolved = False
        self._version_check_dialog = None
        self.update_action = None

    def bind_action(self, action):
        """Wire the 'Install Latest TelemFFB' menu action built in MainMenu;
        it starts disabled until an update is actually found."""
        self.update_action = action

    def start(self):
        """Kick off the background version check, gated the same way the
        startup sequence in main.py used to gate it inline. When checking is
        disabled for this build/instance, emit completion immediately so
        sim listeners (gated on version_check_complete) still start."""
        if G.master_instance and not G.release_version and not (G.dev_build or G.beta_build) and getattr(sys, 'frozen', False):
            logging.info("Checking for version updates...")
            dlg = QProgressDialog("Checking for updates...", "Skip", 0, 0, self.mw)
            dlg.setWindowTitle("TelemFFB")
            dlg.setWindowModality(Qt.WindowModality.WindowModal)
            dlg.setMinimumDuration(0)
            dlg.setAutoClose(False)
            dlg.setAutoReset(False)
            dlg.show()

            utils.FetchLatestVersion(self.update_version_result, self.on_version_check_error)

            self._version_check_dialog = dlg
            dlg.canceled.connect(self.on_version_check_cancelled)
        else:
            # Version checking is disabled; emit immediately so sim listeners can start.
            self._emit_version_check_complete()

    def on_version_check_cancelled(self):
        """Called when the user clicks Skip on the version check progress dialog."""
        if self._version_check_dialog is not None:
            self._version_check_dialog = None
        # Disconnect thread callbacks so a late result doesn't double-resolve.
        for worker in utils.FetchLatestVersion.workers:
            try:
                worker.version_result_signal.disconnect(self.update_version_result)
                worker.error_signal.disconnect(self.on_version_check_error)
            except Exception:
                pass
        self._emit_version_check_complete()

    def update_version_result(self, vers, url):
        # Disconnect the canceled handler before perform_update runs its own
        # QMessageBox inner event loops — QProgressDialog.closeEvent emits canceled,
        # and any modal dialog processing can trigger it spuriously.
        if self._version_check_dialog is not None:
            try:
                self._version_check_dialog.canceled.disconnect(self.on_version_check_cancelled)
            except Exception:
                pass

        self.latest_version = vers

        is_exe = getattr(sys, 'frozen', False)

        if vers == "uptodate":
            status_text = "Up To Date"
            if self.update_action is not None:
                self.update_action.setDisabled(True)
            self.mw.version_label.setText(f'Version Status: {status_text}')
        elif vers == "error":
            status_text = "UNKNOWN"
            self.mw.version_label.setText(f'Version Status: {status_text}')
        elif vers == 'dev':
            if is_exe:
                self.mw.version_label.setText('Version Status: <b>Development Build</b>')
            else:
                self.mw.version_label.setText('Version Status: <b>Development - Clean source</b>')

        elif vers == 'needsupdate':
            self.mw.version_label.setText('Version Status: <b>Out of Date Source - Git pull needed</b>')

        elif vers == 'dirty':
            self.mw.version_label.setText('Version Status: <b>Development - Modified Source</b>')

        else:
            self._update_available = True
            logging.info(f"<<<<Update available - new version={vers}>>>>")

            status_text = (f"New version <a href='{url}'><b>{vers}</b></a> is available! "
                           f"(<a href='{G.release_notes_url}'>release notes</a>)")
            if self.update_action is not None:
                self.update_action.setDisabled(False)
                self.update_action.setText("Install Latest TelemFFB")
            self.mw.version_label.setToolTip(url)
            self.mw.version_label.setText(f'Version Status: {status_text}')

        # If the user accepts the update, perform_update launches the updater and
        # schedules app exit — sim listeners don't need to start in that case.
        # For every other outcome (up to date, dev, error, declined) emit the signal.
        if not self.perform_update(auto=True):
            self._emit_version_check_complete()

        # Hide (not close) the dialog so closeEvent/canceled are not emitted.
        if self._version_check_dialog is not None:
            self._version_check_dialog.hide()
            self._version_check_dialog = None

    def on_version_check_error(self, error_message):
        if self._version_check_dialog is not None:
            try:
                self._version_check_dialog.canceled.disconnect(self.on_version_check_cancelled)
            except Exception:
                pass
            self._version_check_dialog.hide()
            self._version_check_dialog = None
        logging.error("Error checking for version update: %s", error_message)
        self._emit_version_check_complete()

    def _emit_version_check_complete(self):
        """Emit version_check_complete exactly once, regardless of how many paths resolve."""
        if not self._version_check_resolved:
            self._version_check_resolved = True
            self.version_check_complete.emit()

    def update_from_menu(self):
        if self.perform_update(auto=False):
            QCoreApplication.instance().quit()

    def perform_update(self, auto=True):
        mw = self.mw
        if G.release_version:
            return False

        ignore_auto_updates = G.system_settings.get('ignoreUpdate', False)
        if not auto:
            ignore_auto_updates = False
        update_ans = QMessageBox.StandardButton.No
        proceed_ans = QMessageBox.StandardButton.Cancel
        try:
            updater_execution_path = os.path.join(utils.get_script_path(), 'updater.exe')
            if os.path.exists(updater_execution_path):
                os.remove(updater_execution_path)
        except Exception as e:
            logging.error(f'Error in perform_update: {e}')

        is_exe = getattr(sys, 'frozen', False)  # TODO: Make sure to swap these comment-outs before build to commit - this line should be active, next line should be commented out
        # is_exe = True
        if G.child_instance: return False
        if ignore_auto_updates: return False
        if not is_exe: return False

        if self._update_available:
            update_ans = QMessageBox.StandardButton.Yes
            if auto:
                # Rich text so the release-notes link is clickable; clicking
                # it opens the browser without closing the dialog.
                update_ans = QMessageBox.information(mw, "Update Available!!",
                                                     f"A new version of TelemFFB is available (<b>{self.latest_version}</b>).<br><br>"
                                                     f"<a href='{G.release_notes_url}'>View the release notes</a> to see what's new.<br><br>"
                                                     f"Would you like to automatically download and install it now?<br><br>"
                                                     f"You may also update later from the Utilities menu, or the "
                                                     f"next time TelemFFB starts.<br><br>"
                                                     f"~~ Note ~~ If you no longer wish to see this message on startup, "
                                                     f"you may enable `ignore_auto_updates` in your user config. "
                                                     f"You will still be able to update via the Utilities menu",
                                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

            if update_ans == QMessageBox.StandardButton.Yes:
                proceed_ans = QMessageBox.information(mw, "TelemFFB Updater",
                                                      f"TelemFFB will now exit and launch the updater.\n\nPress OK to continue",
                                                      QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)

            if proceed_ans == QMessageBox.StandardButton.Ok:
                updater_execution_path = os.path.join(utils.get_script_path(), 'updater.exe')
                shutil.copy(sys.argv[0], updater_execution_path)

                # Copy the updater executable with forced overwrite

                call = [updater_execution_path, "--current_version", utils.get_version()] + sys.argv[1:]
                subprocess.Popen(call, cwd=utils.get_install_path())
                if auto:
                    for child_widget in mw.findChildren(QMessageBox):
                        child_widget.reject()
                    QTimer.singleShot(250, exit_application)
                return True

        return False
