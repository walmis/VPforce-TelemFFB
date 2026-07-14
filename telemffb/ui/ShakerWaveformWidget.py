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

"""Single-shot waveform preview widget for the shaker calibration UI.

Paints a precomputed pulse buffer (drive section + gap + brake section) so
the user sees the same waveform that will be sent to the audio device when
they click Play. Recomputed once per parameter change, never per audio block.
"""

from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QSizePolicy, QWidget


class ShakerWaveformWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._samples: Optional[np.ndarray] = None
        self._drive_end: int = 0
        self._brake_start: int = 0
        self._brake_end: int = 0

    def set_buffer(self, samples: np.ndarray, drive_end: int,
                   brake_start: int, brake_end: int) -> None:
        self._samples = samples
        self._drive_end = int(drive_end)
        self._brake_start = int(brake_start)
        self._brake_end = int(brake_end)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w = self.width()
            h = self.height()
            mid = h / 2.0

            # Background.
            painter.fillRect(self.rect(), self.palette().base())

            # Zero line.
            painter.setPen(QPen(QColor(120, 120, 120, 90), 1))
            painter.drawLine(0, int(mid), w, int(mid))

            samples = self._samples
            if samples is None or samples.size == 0 or w <= 2:
                return

            # Section markers (vertical lines).
            total = max(1, self._brake_end if self._brake_end > 0 else samples.size)
            for x_sample, color in (
                (self._drive_end, QColor(170, 170, 170, 140)),
                (self._brake_start, QColor(200, 130, 60, 160)),
            ):
                if 0 < x_sample < total:
                    px = int(x_sample / total * w)
                    painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
                    painter.drawLine(px, 0, px, h)

            # Downsample for paint: target ~2 px per sample max.
            n = samples.size
            stride = max(1, n // (w * 2))
            xs = np.arange(0, n, stride)
            ys = samples[::stride]

            # Map sample index -> x pixel; amplitude [-1, +1] -> y pixel.
            px_x = (xs.astype(np.float64) / max(1, total)) * w
            px_y = mid - np.clip(ys.astype(np.float64), -1.0, 1.0) * (mid - 4)
            poly = QPolygonF()
            for x, y in zip(px_x, px_y):
                poly.append(self._point(x, y))

            painter.setPen(QPen(QColor("#ab37c8"), 1.5))
            painter.drawPolyline(poly)
        finally:
            painter.end()

    @staticmethod
    def _point(x: float, y: float):
        from PyQt6.QtCore import QPointF
        return QPointF(float(x), float(y))
