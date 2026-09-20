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

"""TabHeaderBar: the header row a tab page puts its own controls in, with
the compact device row in the middle.

The device row used to sit under the logo, outside the tabs, which meant
it was also on screen on the Hide tab where there is no page to act on.
Here it belongs to the page, so the Hide tab loses it without a special
case, and the Monitor and Settings pages show it in the same place.

"Same place" is why the middle is its own grid column with equal stretch
on either side, rather than a widget laid out after whatever sits to its
left: the column is centered on the page, so a page with a toolbar on the
left (Monitor) and a page with nothing at all (Settings) still agree. The
side columns keep their natural width, so a window narrow enough that the
left content needs more than half of it does push the middle across -
below roughly 800px on the Monitor page, which is why the filter field is
capped rather than left to size itself.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QSizePolicy, QWidget

from telemffb.ui.panels.DevicePanel import MiniDevicePanel


class TabHeaderBar(QWidget):
    """A tab page's header row: ``add_left``/``add_right`` for the page's
    own controls, ``device_mini_panel`` in the middle.

    Every bar owns its own ``MiniDevicePanel`` rather than sharing one,
    because the Monitor page can be detached into a window of its own and
    would take a shared row with it. MainWindow mirrors the full Active
    Devices panel onto all of them together (``_sync_mini_device_panel``).
    """

    DeviceClicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        # A stretch at the far end of each side layout takes the width the
        # side column is given over its content's own: without it the
        # widgets themselves grow to fill the column and drift apart as the
        # window widens.
        self._left = QHBoxLayout()
        self._left.setContentsMargins(0, 0, 0, 0)
        self._left.addStretch()
        self._right = QHBoxLayout()
        self._right.setContentsMargins(0, 0, 0, 0)
        self._right.addStretch()

        self.device_mini_panel = MiniDevicePanel()
        self.device_mini_panel.DeviceClicked.connect(self.DeviceClicked.emit)

        grid.addLayout(self._left, 0, 0,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.device_mini_panel, 0, 1,
                       Qt.AlignmentFlag.AlignCenter)
        grid.addLayout(self._right, 0, 2,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)

    def match_page_background(self) -> None:
        """Paint the palette's Window role - what the settings form below
        the bar is painted in - rather than letting the tab pane's own
        lighter background show through.

        The bar then reads as the top of that form instead of a strip
        above it. The role follows the light/dark theme, so this needs no
        color of its own.
        """
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Window)

    def set_page_inset(self, top: int) -> None:
        """Hold the bar ``top`` pixels below the top of its page.

        A page laid out with a margin of its own already insets its bar by
        that much. A page without one (the settings form fills its page
        edge to edge) has to be told, or its device row sits higher than
        the other page's and jumps as you switch tabs.
        """
        self.setContentsMargins(0, top, 0, 0)

    def add_left(self, widget: QWidget) -> None:
        """Append to the left group, before its trailing stretch."""
        self._left.insertWidget(self._left.count() - 1, widget)

    def add_right(self, widget: QWidget) -> None:
        """Append to the right group, after its leading stretch."""
        self._right.addWidget(widget)
