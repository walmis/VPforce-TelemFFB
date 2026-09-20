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

"""TabHeaderBar: a tab page's header row - the page's own controls packed
from the left, and a slot at the right-hand end for the device strip
(``telemffb.ui.widgets.DeviceStrip``).

The "tab header" device view puts the strip in this slot, on whichever of
the Monitor and Settings pages is showing; a detached Monitor window, where
none of the main window's device displays can be seen, keeps a stand-in
strip of its own in it instead. Both pages' bars end at the same x, which
``set_page_inset`` is for, so the strip does not jump as tabs are switched.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from telemffb.ui.widgets.DeviceStrip import DeviceSlot

#: Clear space between the page's controls and the device slot.
_GAP = 6


class TabHeaderBar(QWidget):
    """``add_left`` for the page's own controls, ``device_slot`` at the
    right-hand end."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self.device_slot = DeviceSlot()

        # [ page controls ... ][ stretch ][ gap ][ slot ]
        # The stretch takes the spare width, so the controls keep their own
        # size at the left and the slot keeps to the right as the window
        # widens.
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.addStretch()
        self._row.addSpacing(_GAP)
        self._row.addWidget(self.device_slot, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._left_count = 0  # widgets added by add_left; they go before the stretch

    def match_page_background(self) -> None:
        """Paint the palette's Window role - what the settings form below
        the bar is painted in - rather than letting the tab pane's own
        lighter background show through. The role follows the light/dark
        theme, so this needs no color of its own."""
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Window)

    def set_page_inset(self, top: int, right: int = 0) -> None:
        """Hold the bar's content ``top`` pixels below the top of its page
        and ``right`` pixels in from its right-hand edge.

        A page laid out with margins of its own already insets its bar by
        that much. A page without them (the settings form fills its page
        edge to edge) has to be told, or its device strip sits higher and
        further right than the other page's, and jumps as you switch tabs.
        """
        self.setContentsMargins(0, top, right, 0)

    def add_left(self, widget: QWidget) -> None:
        """Append to the page's own controls, packed from the left."""
        self._row.insertWidget(self._left_count, widget, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._left_count += 1

    def add_beside_slot(self, widget: QWidget) -> None:
        """Put ``widget`` at the right-hand end, next to the device slot -
        for the stand-in strip a detached window shows in place of the one
        that stays behind. Its own to show and hide: it is not in the slot,
        which empties as the strip moves on."""
        self._row.addWidget(widget, alignment=Qt.AlignmentFlag.AlignVCenter)
