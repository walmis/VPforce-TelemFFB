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

"""An effect-type badge for the Monitor page's active-effects names.

Periodic effects get their waveform drawn from image/wave-*.svg - square
against sine is a real difference in feel, and the shapes are well spread
across what TelemFFB renders, so the glyph discriminates rather than
decorates. Everything else gets a letter: C constant, S spring, D damper,
I inertia, F friction, and so on, with the full type name in the cell's
hover.

EVERY row reserves the same badge width, including one whose type is
unknown. Drawing the glyph only where there was one left the names of the
constants and conditions starting further left than the periodics above
them, and a ragged left edge costs more than the badge gains.

The artwork is a filled outline rather than a stroked path, which is what
makes it hold up at this size: a stroke thin enough to look right at 100px
is a grey smudge at 16, where a filled shape keeps its weight as it scales.
The fill is ``currentColor``, substituted for the theme's accent when the
pixmap is built.
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPixmap
from PyQt6.QtWidgets import QStyledItemDelegate

import telemffb.globals as G
from telemffb.ui.theme.tokens import PURPLE, PURPLE_HOVER
from telemffb.hw.ffb_rhino import (EFFECT_CONSTANT, EFFECT_CUSTOM,
                                   EFFECT_DAMPER, EFFECT_DETENT,
                                   EFFECT_FRICTION, EFFECT_INERTIA,
                                   EFFECT_RAMP, EFFECT_SAWTOOTHDOWN,
                                   EFFECT_SAWTOOTHUP, EFFECT_SINE,
                                   EFFECT_SPRING, EFFECT_SPRING_ADJUSTER,
                                   EFFECT_SQUARE, EFFECT_TRIANGLE)

#: Badge box, and the gap between it and the name it belongs to. Reserved
#: on every row so the names line up whatever is (or is not) drawn in it.
BADGE_WIDTH = 16
BADGE_GAP = 5

#: The lettered badges are set in the table's own monospace face, bold and
#: larger than the name they label so they carry the weight of the filled
#: waveforms beside them. Monospace deliberately: it serifs the I top and
#: bottom, where a grotesque (Segoe UI, Tahoma) draws Inertia as a bare
#: bar that reads as a pipe or a 1.
LETTER_WEIGHT = QFont.Weight.Bold
LETTER_SCALE = 1.40


def badge_color():
    """The accent, as the badge is drawn in it.

    The brand purple is dark enough to sink into the dark theme's
    background, so that theme takes the lightened variant - the same choice
    the link colours make (see TeleplotSetupDialog).
    """
    return QColor(PURPLE_HOVER if G.useDarkMode else PURPLE)

#: The periodic waveforms, by the file each is drawn from.
SHAPES = {
    EFFECT_SQUARE: "wave-square.svg",
    EFFECT_SINE: "wave-sine.svg",
    EFFECT_TRIANGLE: "wave-triangle.svg",
    EFFECT_SAWTOOTHUP: "wave-sawtooth-up.svg",
    EFFECT_SAWTOOTHDOWN: "wave-sawtooth-down.svg",
}

#: Everything else, lettered. Distinct letters throughout, so Damper and
#: Detent cannot be read for one another; the hover carries the full name,
#: which is what the letter is a reminder of rather than a code to learn.
LETTERS = {
    EFFECT_CONSTANT: "C",
    EFFECT_RAMP: "R",
    EFFECT_SPRING: "S",
    EFFECT_SPRING_ADJUSTER: "A",
    EFFECT_DETENT: "T",
    EFFECT_DAMPER: "D",
    EFFECT_INERTIA: "I",
    EFFECT_FRICTION: "F",
    EFFECT_CUSTOM: "X",
}


def _svg_bytes(name):
    """The file's contents, from the compiled Qt resource when present so
    frozen builds work, else the source tree. None if neither has it."""
    from PyQt6.QtCore import QFile, QIODevice
    qf = QFile(f":/image/{name}")
    if qf.exists() and qf.open(QIODevice.OpenModeFlag.ReadOnly):
        data = bytes(qf.readAll())
        qf.close()
        return data
    import telemffb.utils as utils  # local: avoids an import cycle
    try:
        with open(utils.get_resource_path(f"image/{name}"), "rb") as f:
            return f.read()
    except OSError:
        return None


class EffectTypeDelegate(QStyledItemDelegate):
    """Draws the name, indented by its effect-type badge.

    The type is looked up by the cell's own text, which is also the row key
    the effects table diffs on, so nothing has to be threaded through
    KeyValueTableModel to keep a parallel index in step.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._types = {}
        self._pixmaps = {}

    def set_types(self, types) -> None:
        """``{row label: PID effect type}``. A label absent from it, or a
        type with neither a shape nor a letter, still gets the indent - the
        badge column is reserved for every row."""
        self._types = dict(types or {})

    def _pixmap(self, effect_type, color, height):
        """The waveform, tinted and sized, built once per (type, colour,
        size). Rasterising an SVG per row per telemetry frame - twenty
        times a second - would be real work for a picture that never
        changes; the cache key covers a theme switch and a DPI change, the
        only two things that invalidate one."""
        ratio = (QGuiApplication.instance().devicePixelRatio()
                 if QGuiApplication.instance() is not None else 1.0)
        key = (effect_type, color.name(), height, ratio)
        if key in self._pixmaps:
            return self._pixmaps[key]

        from PyQt6.QtSvg import QSvgRenderer
        data = _svg_bytes(SHAPES[effect_type])
        pixmap = None
        if data:
            renderer = QSvgRenderer(data.replace(b"currentColor",
                                                 color.name().encode()))
            box = renderer.viewBoxF()
            if box.width() > 0 and box.height() > 0:
                # Fit, never stretch: the five files differ in width (359 to
                # 439 against a common height), so scaling each to a square
                # would draw the sine noticeably lighter than the square.
                scale = min(BADGE_WIDTH / box.width(), height / box.height())
                w, h = box.width() * scale, box.height() * scale
                pixmap = QPixmap(round(BADGE_WIDTH * ratio), round(height * ratio))
                pixmap.setDevicePixelRatio(ratio)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                renderer.render(painter, QRectF((BADGE_WIDTH - w) / 2,
                                                (height - h) / 2, w, h))
                painter.end()
        self._pixmaps[key] = pixmap
        return pixmap

    def paint(self, painter: QPainter, option, index) -> None:
        label = index.data(Qt.ItemDataRole.DisplayRole) or ""
        effect_type = self._types.get(label)

        # Indent first, then let the base class draw the text with the
        # selection background and palette it would use anyway.
        shifted = option.__class__(option)
        shifted.rect = option.rect.adjusted(BADGE_WIDTH + BADGE_GAP, 0, 0, 0)
        super().paint(painter, shifted, index)

        color = badge_color()
        if effect_type in SHAPES:
            height = min(BADGE_WIDTH, max(option.rect.height() - 6, 8))
            pixmap = self._pixmap(effect_type, color, height)
            if pixmap is not None:
                painter.drawPixmap(
                    option.rect.left() + 2,
                    option.rect.center().y() - height // 2, pixmap)
            return

        letter = LETTERS.get(effect_type)
        if letter is None:
            return
        font = QFont(option.font)
        font.setWeight(LETTER_WEIGHT)
        font.setPointSizeF(option.font.pointSizeF() * LETTER_SCALE)
        painter.save()
        box = option.rect.adjusted(2, 0, 0, 0)
        box.setWidth(BADGE_WIDTH)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), letter)
        painter.restore()
