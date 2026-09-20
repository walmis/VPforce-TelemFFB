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

"""DockZoneOverlay: the highlight shown while the floating device strip is
dragged over somewhere it can be dropped to dock.

One widget, moved to whichever zone the cursor is over and hidden when it
is over none. It draws over the window's layout and takes no part in it,
and it lets the mouse through - the drag it is reporting on must go on
reaching the strip. It is a highlight only: what the zone is called is said
in a tooltip beside the strip (MainWindow), since anything written in the
zone would be under the strip being dragged over it.
"""
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from telemffb.ui.theme.tokens import PURPLE


class DockZoneOverlay(QWidget):
    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def show_zone(self, rect: QRect) -> None:
        if rect != self.geometry() or not self.isVisible():
            self.setGeometry(rect)
            self.show()
            self.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = QColor(PURPLE)
        fill = QColor(accent)
        fill.setAlpha(50)
        painter.setBrush(fill)
        painter.setPen(QPen(accent, 1.5, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 5, 5)
        painter.end()
