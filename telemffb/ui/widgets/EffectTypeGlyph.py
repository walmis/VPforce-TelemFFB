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

"""EffectTypeGlyph: the Monitor page's effect-type badge as a widget.

Draws the same waveform or letter ``EffectTypeDelegate`` paints beside an
active effect's name, in the same accent color and face, so a control
labeled with it is visibly tied to the effects listed on the Monitor page.
A glyph with no type draws nothing but keeps its size, so rows with and
without one line up.
"""

from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QWidget

from telemffb.telem.telem_format import BADGE_LETTERS, BADGE_SHAPES
from telemffb.ui.widgets.EffectTypeDelegate import (BADGE_WIDTH, LETTER_SCALE, LETTER_WEIGHT,
                                                    badge_color, badge_pixmap)

#: The Monitor page's face, which gives the I its serifs.
_LETTER_FAMILY = "Cascadia Mono"


class EffectTypeGlyph(QWidget):
    """A fixed-size badge for one PID effect type (``None`` for a blank)."""

    def __init__(self, effect_type: Optional[int] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._effect_type = effect_type
        self._font = QFont(_LETTER_FAMILY)
        self._font.setWeight(LETTER_WEIGHT)
        self._font.setPointSizeF(self.font().pointSizeF() * LETTER_SCALE)
        height = QFontMetrics(self._font).height()
        self.setFixedSize(QSize(BADGE_WIDTH, height))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @property
    def effect_type(self) -> Optional[int]:
        return self._effect_type

    def paintEvent(self, event) -> None:
        if self._effect_type is None:
            return
        painter = QPainter(self)
        color = badge_color()
        if self._effect_type in BADGE_SHAPES:
            height = min(BADGE_WIDTH, self.height())
            pixmap = badge_pixmap(self._effect_type, color, height)
            if pixmap is not None:
                painter.drawPixmap(0, (self.height() - height) // 2, pixmap)
            painter.end()
            return
        letter = BADGE_LETTERS.get(self._effect_type)
        if letter is not None:
            painter.setFont(self._font)
            painter.setPen(color)
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), letter)
        painter.end()
