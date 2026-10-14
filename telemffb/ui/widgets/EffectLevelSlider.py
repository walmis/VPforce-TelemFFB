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

"""EffectLevelSlider: one effect level, 0..100%, as a slider with a
percent readout.

``edited(value, persist)`` reports the user's changes only: ``persist`` is
False while the handle is being dragged and True on its release and for
keyboard, wheel and groove clicks.  ``set_value`` shows a value from the
model without reporting it, and leaves the slider alone mid-drag.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget


class EffectLevelSlider(QWidget):
    """A 0..100 slider and its "NN%" readout."""

    #: (value, persist) on a change the user made
    edited = pyqtSignal(int, bool)

    def __init__(self, parent=None, slider_width: int = 0):
        super().__init__(parent)
        self._quiet = False

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(10)
        self.slider.setValue(100)
        if slider_width:
            self.slider.setFixedWidth(slider_width)

        self.readout = QLabel()
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.readout.setFixedWidth(self.readout.fontMetrics().horizontalAdvance("100%") + 4)
        self._show(100)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.slider, 1)
        row.addWidget(self.readout)

        self.slider.valueChanged.connect(self._on_value_changed)
        self.slider.sliderReleased.connect(self._on_released)

    def value(self) -> int:
        return self.slider.value()

    def dragging(self) -> bool:
        return self.slider.isSliderDown()

    def set_value(self, value: int) -> None:
        """Show ``value`` without reporting it.  Ignored while the user is
        dragging the handle, so a model update never fights the drag."""
        if self.slider.isSliderDown():
            return
        self._quiet = True
        try:
            self.slider.setValue(int(value))
        finally:
            self._quiet = False
        self._show(self.slider.value())

    def _show(self, value: int) -> None:
        self.readout.setText(f"{value}%")

    def _on_value_changed(self, value: int) -> None:
        self._show(value)
        if not self._quiet:
            self.edited.emit(value, not self.slider.isSliderDown())

    def _on_released(self) -> None:
        self.edited.emit(self.slider.value(), True)
