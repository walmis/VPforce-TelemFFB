"""IconBadge: a small colored disc laid over one corner of a device icon,
with an SVG glyph on it.

A child of the widget it marks (the icon label), so it moves with the icon.
Hidden until ``set_shown(True)``; it takes no mouse events, so the icon's
own clicks and tooltip are unaffected.  The glyph is an ``image/`` SVG
whose fills say ``currentColor``; it is rendered in ``glyph_color``.
"""

from typing import Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget

from telemffb.ui.widgets.EffectTypeDelegate import _svg_bytes

#: The glyph's box as a fraction of the disc diameter.
_GLYPH_SPAN = 0.62


class IconBadge(QWidget):
    """A ``size`` px disc in ``color`` in its parent's top-right corner, or
    its bottom-right corner when ``bottom`` is set, showing ``glyph`` (an
    SVG file name under ``image/``) in ``glyph_color``."""

    def __init__(self, parent: QWidget, size: int, color: str, glyph: str,
                 glyph_color: str = "white", bottom: bool = False):
        super().__init__(parent)
        self._color = QColor(color)
        self._bottom = bottom
        self._renderer: Optional[QSvgRenderer] = None
        data = _svg_bytes(glyph)
        if data:
            self._renderer = QSvgRenderer(data.replace(b"currentColor",
                                                       QColor(glyph_color).name().encode()))
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._place()
        self.hide()

    def set_shown(self, shown: bool) -> None:
        if shown:
            self._place()
            self.raise_()
        self.setVisible(shown)

    def _place(self) -> None:
        parent = self.parentWidget()
        y = max(0, parent.height() - self.height()) if self._bottom else 0
        self.move(max(0, parent.width() - self.width()), y)

    def paintEvent(self, event) -> None:
        s = float(self.width())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # disc, with a light rim so it stands off any icon tint
        rim = max(1.0, s / 14)
        painter.setPen(QPen(QColor(255, 255, 255, 220), rim))
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(rim / 2, rim / 2, s - rim, s - rim))

        if self._renderer is not None and self._renderer.isValid():
            box = self._renderer.viewBoxF()
            if box.width() > 0 and box.height() > 0:
                # fit the glyph's own aspect inside a centered square
                span = s * _GLYPH_SPAN
                scale = min(span / box.width(), span / box.height())
                w, h = box.width() * scale, box.height() * scale
                self._renderer.render(painter, QRectF((s - w) / 2, (s - h) / 2, w, h))
        painter.end()
