#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
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

"""The Unit cell in the Monitor page's telemetry table.

Draws the unit as plain text and, for a key that can be shown in another
unit, a dim drop-down chevron at the cell's right edge - the hint that a
click opens the conversion menu. The chevron is painted rather than put in
the cell text so a Ctrl+C of the row copies the unit alone.

Which rows get one is handed in by the panel (``set_convertible``), keyed
by the model's row key, the same way ``EffectTypeDelegate`` is given its
types.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QStyledItemDelegate

#: Room kept clear for the chevron, so a long unit never runs under it.
CHEVRON_WIDTH = 14

_CHEVRON = "▾"  # small down-pointing triangle

#: The chevron's alpha against the row's own text colour: chrome, not data.
_CHEVRON_ALPHA = 0.45


class UnitCellDelegate(QStyledItemDelegate):
    """Paints a unit, plus a chevron when it is convertible."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._convertible = frozenset()

    def set_convertible(self, row_keys) -> None:
        """The row keys whose unit has a conversion menu."""
        self._convertible = frozenset(row_keys or ())

    def is_convertible(self, index) -> bool:
        model = index.model()
        return (index.isValid() and hasattr(model, "row_key")
                and model.row_key(index.row()) in self._convertible)

    def paint(self, painter, option, index):
        # Background, selection and the left-aligned unit as usual; the
        # column is sized from sizeHint, which keeps the chevron's strip clear.
        super().paint(painter, option, index)
        if not self.is_convertible(index):
            return
        rect = option.rect.adjusted(option.rect.width() - CHEVRON_WIDTH, 0, -2, 0)
        color = option.palette.text().color()
        color.setAlphaF(_CHEVRON_ALPHA)
        painter.save()
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, _CHEVRON)
        painter.restore()

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        if self.is_convertible(index):
            hint.setWidth(hint.width() + CHEVRON_WIDTH)
        return hint
