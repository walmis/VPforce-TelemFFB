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

"""HiddenLevelsNote: the Settings tab header's line naming the effect
levels below 100% that have no slider showing.

``EffectLevelsHeader`` decides which levels it lists (``set_levels``); the
note only words them, in quiet secondary text, and reports a click
(``clicked``), which opens the Effect Levels dialog.
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QLabel, QSizePolicy

from telemffb.ui.theme.tokens import LIGHT, current_tokens
from telemffb.ui.widgets.effect_levels_ui import LEVEL_TEXT


def _quiet_color() -> str:
    try:
        return current_tokens().muted_text_color
    except AttributeError:      # no theme chosen yet (G.useDarkMode unset)
        return LIGHT.muted_text_color


class HiddenLevelsNote(QLabel):
    """The levels below 100% that cannot be seen, as one clickable line."""

    #: the note was clicked
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        #: what the note lists: (level name, percent), in ``LEVEL_NAMES`` order
        self.entries: list[tuple[str, int]] = []
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        color = _quiet_color()
        self.setStyleSheet(f"QLabel {{ color: {color}; }}"
                           f"QLabel:hover {{ text-decoration: underline; }}")
        self.hide()

    def set_levels(self, entries, role_text: str, beside_sliders: bool) -> None:
        """List ``entries`` ((name, percent) pairs) for the device called
        ``role_text``; hidden while there are none.  ``beside_sliders``: the
        note sits under the level sliders, rather than standing in for
        them."""
        self.entries = [(name, int(value)) for name, value in entries]
        listed = ", ".join(f"{LEVEL_TEXT[name][0]} {value}%" for name, value in self.entries)
        self.setText(f"Effect Levels: {listed}")
        where = "click here, the Levels button" if beside_sliders else "click here"
        self.setToolTip(
            f"These TelemFFB effect levels are in effect on the {role_text}.\n"
            f"Change them in the Effect Levels dialog: {where}, or "
            "View > Configure Effect Levels...")
        self.setVisible(bool(self.entries))

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            try:
                self.clicked.emit()
            except Exception:
                logging.exception("Effect levels: the dialog did not open from the note")
