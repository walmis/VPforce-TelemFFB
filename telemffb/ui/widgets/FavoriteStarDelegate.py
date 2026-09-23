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

"""The star cell in the Monitor page's telemetry table: click it to make a
telemetry key a favourite.

The column carries no text - the model stores an empty string for it - so
the star is painted rather than stored: filled yellow for a key in the
favourites set, a hollow outline otherwise, and a half-lit preview under
the cursor. Which keys those are lives with the panel (``MonitorPanel``
loads and saves them), and this delegate is handed the set itself rather
than a copy, so a toggle shows on the very next repaint without anything
having to be pushed back into it.

Clicking is the *view's* business, not this delegate's: MonitorPanel
connects ``QTableView.clicked``. The delegate only draws, which keeps the
one thing that needs the favourites set to be mutated in the one place
that owns it.

Drawn rather than widgeted, for the same reason as ``IntensityBarDelegate``:
the telemetry table rebuilds at 20 Hz and a QToolButton per row would be
built and destroyed at that rate.
"""

import math
from typing import Optional, Set

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle, QToolTip

#: Wide enough for the star plus its breathing room, narrow enough that the
#: column reads as a gutter rather than as data.
STAR_COLUMN_WIDTH = 22

#: The star's diameter is this share of the smaller cell dimension, so it
#: stays clear of the row separators at any row height.
_STAR_SCALE = 0.62

#: Ratio of a five-pointed star's inner radius to its outer one. Lower is
#: spindlier; this is about what a UI star glyph uses.
_INNER_RATIO = 0.44

_POINTS = 5

#: Favourite. A yellow that holds up on both themes' row backgrounds.
_GOLD = QColor("#f5c518")

#: The hollow star's outline, and the hover preview's fill, as fractions of
#: the row's own text colour / of _GOLD - so the gutter reads as inactive
#: chrome on either theme without naming a second set of colours.
_IDLE_ALPHA = 0.35
_HOVER_ALPHA = 0.45

TOOLTIP = ("Favourite this telemetry item.\n"
           "Favourites are shared by all devices and kept between sessions;\n"
           "tick 'Favorites' beside the filter to list only these.")


class FavoriteStarDelegate(QStyledItemDelegate):
    """Paints the favourite star for a row, by its model row key."""

    def __init__(self, parent=None):
        super().__init__(parent)
        #: The live set owned by the panel - held by reference, not copied.
        self._favorites: Set[str] = set()
        # One path per cell size, centred on the origin: every cell in the
        # column is the same size, so this is built once and translated.
        self._path: Optional[QPainterPath] = None
        self._path_size = None

    def set_favorites(self, favorites: Set[str]) -> None:
        """Adopt the panel's favourites set. Mutations to it are picked up
        on the next repaint - nothing has to be handed back here."""
        self._favorites = favorites

    # ---- painting ---------------------------------------------------------

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)  # row background / selection

        key = self._row_key(index)
        if key is None:
            return
        favorite = key in self._favorites
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if not favorite and not hovered:
            fill, pen_color = None, self._faded(option.palette.text().color(),
                                                _IDLE_ALPHA)
        elif favorite:
            fill, pen_color = _GOLD, _GOLD.darker(130)
        else:
            fill, pen_color = self._faded(_GOLD, _HOVER_ALPHA), _GOLD.darker(130)

        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(rect.center())
        painter.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
        painter.setPen(pen_color)
        painter.drawPath(self._star_path(min(rect.width(), rect.height())))
        painter.restore()

    def helpEvent(self, event, view, option, index) -> bool:
        """The cell has no text of its own, so its hover has to be said
        here rather than coming from the model's ToolTipRole."""
        QToolTip.showText(event.globalPos(), TOOLTIP, view)
        return True

    # ---- internals --------------------------------------------------------

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

    def _star_path(self, size: float) -> QPainterPath:
        """A five-pointed star centred on the origin, sized to ``size``."""
        if size == self._path_size and self._path is not None:
            return self._path
        outer = size * _STAR_SCALE / 2
        path = QPainterPath()
        for i in range(_POINTS * 2):
            radius = outer if i % 2 == 0 else outer * _INNER_RATIO
            angle = -math.pi / 2 + i * math.pi / _POINTS
            point = QPointF(radius * math.cos(angle), radius * math.sin(angle))
            if i:
                path.lineTo(point)
            else:
                path.moveTo(point)
        path.closeSubpath()
        self._path, self._path_size = path, size
        return path
