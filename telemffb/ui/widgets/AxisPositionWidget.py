"""A square readout of a two-axis position: crosshair, a dot for the
reported position, and an optional ring for a reference point."""
from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from telemffb.ui.theme.tokens import DIVIDER_GRAY, OK_GREEN, PURPLE


class AxisPositionWidget(QWidget):
    """Shows (x, y) in -1..1 with +X to the right and +Y (aft) down, so a
    stick pushed forward moves the dot up."""

    def __init__(self, parent=None, size: int = 140):
        super().__init__(parent)
        self._size = size
        self._position: Optional[Tuple[float, float]] = None
        self._reference: Optional[Tuple[float, float]] = None
        self.setFixedSize(QSize(size, size))

    def set_position(self, position: Optional[Tuple[float, float]]):
        self._position = position
        self.update()

    def set_reference(self, reference: Optional[Tuple[float, float]]):
        self._reference = reference
        self.update()

    def _point(self, xy) -> QPointF:
        half = (self._size - 8) / 2.0
        cx = cy = self._size / 2.0
        x = max(-1.0, min(1.0, xy[0]))
        y = max(-1.0, min(1.0, xy[1]))
        return QPointF(cx + x * half, cy + y * half)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QColor(DIVIDER_GRAY)
        painter.setPen(QPen(frame, 1))
        painter.drawRect(3, 3, self._size - 7, self._size - 7)
        mid = self._size / 2.0
        painter.drawLine(QPointF(3, mid), QPointF(self._size - 4, mid))
        painter.drawLine(QPointF(mid, 3), QPointF(mid, self._size - 4))
        if self._reference is not None:
            painter.setPen(QPen(QColor(OK_GREEN), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self._point(self._reference), 7, 7)
        if self._position is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(PURPLE))
            painter.drawEllipse(self._point(self._position), 5, 5)
        painter.end()
