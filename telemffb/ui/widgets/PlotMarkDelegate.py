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

"""The plot-mark cell in the Monitor page's telemetry table: click it to
plot that telemetry key in the plot window.

Built the way ``FavoriteStarDelegate`` is: the column carries no text, the
glyph (a small line chart) is painted from the panel's live set of marked
keys, handed in by reference, and clicking is the view's business.
"""

from typing import Optional, Set

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle, QToolTip

PLOT_COLUMN_WIDTH = 22
_ACCENT = QColor("#2f8fdc")
_IDLE_ALPHA = 0.35
_HOVER_ALPHA = 0.55
#: The glyph: a short trace that rises, dips and rises, in a unit box.
_TRACE = ((0.0, 0.75), (0.25, 0.3), (0.5, 0.6), (0.75, 0.15), (1.0, 0.45))
_GLYPH_SCALE = 0.62

TOOLTIP = ("Plot this telemetry item in the plot window.\n"
           "Plotted items are shared by all devices and kept between sessions;\n"
           "the Plot button beside Detach opens the window.")


class PlotMarkDelegate(QStyledItemDelegate):
    """Paints the plot mark for a row, by its model row key."""

    def __init__(self, parent=None):
        super().__init__(parent)
        #: The live set owned by the panel - held by reference, not copied.
        self._marked: Set[str] = set()

    def set_marked(self, marked: Set[str]) -> None:
        self._marked = marked

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        key = self._row_key(index)
        if key is None:
            return
        marked = key in self._marked
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if marked:
            color = _ACCENT
        elif hovered:
            color = self._faded(_ACCENT, _HOVER_ALPHA)
        else:
            color = self._faded(option.palette.text().color(), _IDLE_ALPHA)
        rect = QRectF(option.rect)
        size = min(rect.width(), rect.height()) * _GLYPH_SCALE
        origin = QPointF(rect.center().x() - size / 2, rect.center().y() - size / 2)
        points = QPolygonF([QPointF(origin.x() + x * size, origin.y() + y * size) for x, y in _TRACE])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 1.6 if marked else 1.2))
        painter.drawPolyline(points)
        painter.restore()

    def helpEvent(self, event, view, option, index) -> bool:
        QToolTip.showText(event.globalPos(), TOOLTIP, view)
        return True

    @staticmethod
    def _row_key(index) -> Optional[str]:
        model = index.model()
        row_key = getattr(model, 'row_key', None)
        return row_key(index.row()) if row_key is not None else None

    @staticmethod
    def _faded(color: QColor, alpha: float) -> QColor:
        faded = QColor(color)
        faded.setAlphaF(alpha)
        return faded
