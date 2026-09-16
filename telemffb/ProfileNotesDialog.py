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

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
                             QPlainTextEdit, QPushButton)

from . import globals as G
from . import xmlutils


class ProfileNotesDialog(QDialog):
    """View/edit notes for the currently loaded aircraft profile.

    Read-only sections show the inherited tiers: curated notes from the
    defaults name="type" row and "user default" notes from the user config's
    name="type" row (both profile-independent). The bottom section edits the
    note belonging to the current write target — the active profile, or
    "Auto User" when the Built-in profile is active (mirroring where setting
    changes land). Notes on other profiles' rows are never shown.
    """

    notes_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.sim = G.settings_mgr.current_sim
        self.aircraft_name = G.settings_mgr.current_aircraft_name
        self.pattern = G.settings_mgr.current_pattern
        active = G.settings_mgr.active_profile
        if not active or str(active).lower() in ('none', 'built-in', 'default'):
            self.target_profile = 'Auto User'
        else:
            self.target_profile = active

        self.setWindowTitle(f"Profile Notes - {self.pattern or self.aircraft_name}")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        header = QLabel(f"<b>{self.aircraft_name}</b><br>"
                        f"Matched model: {self.pattern or '(none)'} &nbsp;&mdash;&nbsp; "
                        f"Profile: {self.target_profile}")
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        curated = xmlutils.read_default_model_notes(self.sim, self.aircraft_name,
                                                    prefer_pattern=self.pattern)
        if curated:
            layout.addWidget(self._make_readonly_section('Default Profile notes (built-in defaults)', curated))

        # "User default" tier: notes on the user config's name="type" row.
        # Profile-independent — inherited by every profile of this model.
        # Notes on other profiles' rows are NOT shown; each profile sees only
        # the type tiers plus its own note.
        user_default_note = xmlutils.read_user_default_model_notes(self.sim, self.pattern)
        if user_default_note:
            layout.addWidget(self._make_readonly_section('User default notes', user_default_note))

        edit_group = QGroupBox(f"Your notes ({self.target_profile} profile)")
        edit_layout = QVBoxLayout(edit_group)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(xmlutils.read_user_model_notes(self.sim, self.pattern, self.target_profile))
        self.editor.setMinimumHeight(120)
        edit_layout.addWidget(self.editor)
        layout.addWidget(edit_group)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        btn_save = QPushButton('Save')
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton('Cancel')
        btn_cancel.clicked.connect(self.close)
        button_row.addWidget(btn_save)
        button_row.addWidget(btn_cancel)
        layout.addLayout(button_row)

    def _make_readonly_section(self, title, text):
        group = QGroupBox(title)
        group_layout = QVBoxLayout(group)
        # A word-wrapped label sizes itself to the text (the content never
        # changes while the dialog is open), so the section shrinks to fit.
        # Keep ~two lines of cushion so a single-line note doesn't get lost.
        viewer = QLabel(text)
        viewer.setWordWrap(True)
        viewer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        viewer.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        viewer.setMinimumHeight(viewer.fontMetrics().lineSpacing() * 3)
        group_layout.addWidget(viewer)
        return group

    def _save(self):
        written = xmlutils.write_user_model_notes(self.sim, self.pattern,
                                                  self.editor.toPlainText(),
                                                  self.target_profile)
        if written is None:
            logging.error("Failed to save profile notes")
        else:
            self.notes_saved.emit()
        self.close()
