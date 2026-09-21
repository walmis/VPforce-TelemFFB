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

"""A bar-graph cell for the Monitor page's active-effects intensity column.

The model stores plain strings, as it does for every other column, so the
cell's value is its own display text - "42%", or "-" where an effect has no
meaningful intensity. This delegate reads that text back rather than taking
a separate numeric role: it keeps KeyValueTableModel generic, and it means
a copied selection carries the readable percentage instead of a raw float.

Drawn rather than widgeted: the effects list rebuilds every telemetry frame
(20 Hz), and a QProgressBar per row would be built and destroyed at that
rate for no visual gain.
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QStyledItemDelegate

import telemffb.globals as G

#: Bars below this read as a hairline rather than as nothing, so an effect
#: that is started but commanding almost no force stays visibly distinct
#: from one commanding none at all.
MIN_VISIBLE_FRACTION = 0.02


def parse_percent(text):
    """The fraction 0.0-1.0 behind a "42%" cell, or None for anything else
    (the "-" a condition effect gets, or an empty cell)."""
    text = (text or "").strip()
    if not text.endswith("%"):
        return None
    try:
        return max(0.0, min(1.0, float(text[:-1]) / 100.0))
    except ValueError:
        return None


class IntensityBarDelegate(QStyledItemDelegate):
    """Paints a proportional bar behind the percentage text."""

    def paint(self, painter: QPainter, option, index) -> None:
        fraction = parse_percent(index.data(Qt.ItemDataRole.DisplayRole))
        if fraction is None:
            # Nothing to graph - let the default painter draw the text.
            super().paint(painter, option, index)
            return

        painter.save()
        rect = QRectF(option.rect).adjusted(2, 3, -2, -3)

        track = QColor("#3a3a3a") if G.useDarkMode else QColor("#d8d8d8")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, 2, 2)

        if fraction > 0:
            width = max(rect.width() * fraction,
                        rect.width() * MIN_VISIBLE_FRACTION)
            painter.setBrush(self._bar_color(fraction))
            painter.drawRoundedRect(
                QRectF(rect.left(), rect.top(), width, rect.height()), 2, 2)

        painter.restore()
        # The number on top of the bar: the bar answers "how much" at a
        # glance, the text answers it exactly.
        painter.save()
        painter.setPen(QColor("#f0f0f0") if G.useDarkMode else QColor("#202020"))
        painter.drawText(option.rect, int(Qt.AlignmentFlag.AlignCenter),
                         index.data(Qt.ItemDataRole.DisplayRole))
        painter.restore()

    @staticmethod
    def _bar_color(fraction: float) -> QColor:
        """Green through amber to red as the effect approaches full scale -
        the same reading as the settings page's live coefficient handles."""
        if fraction < 0.5:
            return QColor("#4caf50")
        if fraction < 0.8:
            return QColor("#c8a02a")
        return QColor("#c0392b")
