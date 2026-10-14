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

"""PinToggleButton: a small checkable button drawn as a push pin.

Checked (pinned): an upright pin filled in the accent color.  Unchecked:
a tilted outline in a faded text color, so the two states differ in
shape as well as color.  Painted from the widget's palette, so it follows
the light and dark themes.  The tooltip follows the state.
"""

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QToolButton

from telemffb.ui.theme.tokens import PURPLE

_SIZE = 22
#: The glyph is drawn on a 16-unit grid, centered in the button.
_GRID = 16.0
_TILT_DEGREES = 45.0
_IDLE_ALPHA = 0.5
_HOVER_ALPHA = 0.85


def _pin_path() -> QPainterPath:
    """The pin's head, body and collar on the 16-unit grid, centered on
    the origin; the needle is drawn as a line below it."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(-3.0, -7.0, 6.0, 2.2), 0.8, 0.8)   # head
    path.addRect(QRectF(-2.0, -4.8, 4.0, 4.3))                   # body
    path.addRoundedRect(QRectF(-4.6, -0.5, 9.2, 2.2), 0.8, 0.8)  # collar
    return path.simplified()


class PinToggleButton(QToolButton):
    """A checkable push-pin toggle with a tooltip per state."""

    def __init__(self, tip_off: str = "", tip_on: str = "", parent=None):
        super().__init__(parent)
        self._tips = (tip_off, tip_on)
        self._path = _pin_path()
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setFixedSize(QSize(_SIZE, _SIZE))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._show_state)
        self._show_state(False)

    def _show_state(self, on: bool) -> None:
        self.setToolTip(self._tips[1] if on else self._tips[0])
        self.setAccessibleDescription(self.toolTip())
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        hovered = self.underMouse() and self.isEnabled()
        if hovered or self.isDown():
            fill = QColor(palette.midlight().color() if not self.isDown()
                          else palette.mid().color())
            fill.setAlphaF(0.6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        if self.hasFocus():
            painter.setPen(QPen(palette.highlight().color(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)

        scale = min(self.width(), self.height()) * 0.8 / _GRID
        painter.translate(QPointF(self.rect().center()) + QPointF(0.5, 0.5))
        painter.scale(scale, scale)
        if self.isChecked():
            color = QColor(PURPLE)
            if not self.isEnabled():
                color = palette.mid().color()
            pen = QPen(color.darker(120), 1.0 / scale * 1.1)
            painter.setPen(pen)
            painter.setBrush(color)
        else:
            painter.rotate(_TILT_DEGREES)
            color = QColor(palette.buttonText().color())
            color.setAlphaF(_HOVER_ALPHA if hovered else _IDLE_ALPHA)
            if not self.isEnabled():
                color = palette.mid().color()
            pen = QPen(color, 1.0 / scale * 1.2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)
        needle = QPen(pen.color(), 1.0 / scale * 1.4)
        needle.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(needle)
        painter.drawLine(QPointF(0.0, 1.7), QPointF(0.0, 7.0))
        painter.end()
