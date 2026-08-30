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

"""Startup offer to update installed tap wrappers.

TelemFFB ships one wrapper build; each game folder holds its own copy,
and those copies only change when something copies over them.  A TelemFFB
update therefore strands every installed wrapper on the old build until
the user thinks to visit each sim's tap panel - this dialog does the
rounds for them: every enabled sim whose installed wrapper the bundled
build supersedes, one prompt, each sim deselectable.

Update only, deliberately: it never installs into a sim that has no
wrapper (that is a decision, made in the tap panel with a device dialog)
and never touches a foreign dinput8.dll.  Configs are preserved -
``install`` overwrites the DLL only.
"""

import logging
from typing import List, Tuple

from PyQt6 import QtWidgets

import telemffb.globals as G
from telemffb.tap_install import (SimStatus, bundled_version, install,
                                  outdated_targets)
from telemffb.tap_reconcile import all_status, tap_is_enabled


def pending_wrapper_updates(settings) -> List[Tuple[SimStatus, list]]:
    """Enabled, tap-opted sims whose installed wrapper is older than the
    bundled one - what the startup offer shows."""
    bundled = bundled_version()
    if not bundled:
        return []
    out = []
    for status in all_status(settings):
        if not status.found:
            continue
        if not tap_is_enabled(status.sim, settings):
            continue
        outdated = outdated_targets(status, bundled)
        if outdated:
            out.append((status, outdated))
    return out


class TapUpdateDialog(QtWidgets.QDialog):
    """One checkbox per sim needing an update, all preselected."""

    def __init__(self, pending, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DirectInput Tap Updates")
        self._boxes = []

        layout = QtWidgets.QVBoxLayout(self)
        bundled = bundled_version()
        intro = QtWidgets.QLabel(
            f"This TelemFFB ships tap wrapper v{bundled}, and these sims "
            "have an older build installed.  Update them now?  Existing "
            "dinput8.ini configurations are kept.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        for status, outdated in pending:
            versions = sorted({t.version or "unversioned" for t in outdated})
            box = QtWidgets.QCheckBox(
                f"{status.sim.name}  ({', '.join(versions)} → v{bundled})")
            box.setChecked(True)
            box.setToolTip("\n".join(t.directory for t in outdated))
            layout.addWidget(box)
            self._boxes.append((box, status))

        buttons = QtWidgets.QDialogButtonBox()
        update = buttons.addButton(
            "Update selected", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Not now",
                          QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        update.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> List[SimStatus]:
        return [status for box, status in self._boxes if box.isChecked()]


def offer_wrapper_updates(parent=None, settings=None) -> bool:
    """Show the startup offer when anything needs updating; apply the
    user's selection.  Returns whether anything was updated."""
    settings = settings if settings is not None else G.system_settings
    try:
        pending = pending_wrapper_updates(settings)
    except Exception:
        logging.exception("DirectInput tap: update scan failed")
        return False
    if not pending:
        return False

    dialog = TapUpdateDialog(pending, parent)
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return False

    failures = []
    updated_any = False
    for status in dialog.selected():
        for outcome in install(status):
            if outcome.ok:
                updated_any = True
                logging.info(f"DirectInput tap: {status.sim.name} wrapper "
                             f"{outcome.action} in {outcome.directory}")
            else:
                failures.append(f"{status.sim.name} - {outcome.directory}: "
                                f"{outcome.detail}")
    if failures:
        QtWidgets.QMessageBox.warning(
            parent, "DirectInput Tap Updates",
            "Some wrappers could not be updated:\n\n"
            + "\n".join(f"    {line}" for line in failures))
    return updated_any
