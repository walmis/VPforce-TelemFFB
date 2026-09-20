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
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout,
                             QWidget)

from telemffb.ui.panels.DevicePanel import MiniDevicePanel

#: Clear space either side of the device row.
_GAP = 6


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
        # No spacing of the grid's own between the columns: a grid leaves
        # it out beside a column holding no widget, which the left column
        # is on a page with no controls of its own, and the device row
        # would sit half a gap off center there. The side layouts carry
        # the gap themselves instead (_GAP), where it is unconditional.
        grid.setHorizontalSpacing(0)

        # A stretch at the far end of each side layout takes the width the
        # side column is given over its content's own: without it the
        # widgets themselves grow to fill the column and drift apart as the
        # window widens.
        self._left = QHBoxLayout()
        self._left.setContentsMargins(0, 0, 0, 0)
        self._left.addStretch()
        self._left.addSpacing(_GAP)
        self._left_count = 0  # widgets added by add_left; they go before the stretch
        self._right = QHBoxLayout()
        self._right.setContentsMargins(0, 0, 0, 0)
        self._right.addSpacing(_GAP)
        self._right.addStretch()

        self.device_mini_panel = MiniDevicePanel()
        self.device_mini_panel.DeviceClicked.connect(self.DeviceClicked.emit)

        # The side layouts fill their cells - their own stretches do the
        # packing - so that something can sit at the near end of the left
        # one, up against the device row and level with the top of its
        # icons (add_before_devices).
        grid.addLayout(self._left, 0, 0)
        grid.addWidget(self.device_mini_panel, 0, 1,
                       Qt.AlignmentFlag.AlignCenter)
        grid.addLayout(self._right, 0, 2)
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
        """Append to the left group, before its trailing stretch and gap."""
        self._left.insertWidget(self._left_count, widget, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._left_count += 1

    def add_before_devices(self, widget: QWidget, top_padding: int = 0) -> None:
        """Put ``widget`` immediately left of the device row, its top level
        with the top of the row's icons.

        ``top_padding`` is how far below its own top edge the widget's
        visible content starts, so that it is the content that lines up.

        It goes at the near end of the left-hand column rather than into
        the row itself, and the right-hand column is given the same width
        of empty space at its own near end, so the two columns still
        balance and the row stays centered on the page. The widget keeps
        its space when hidden for the same reason: the row must not shift
        as the widget comes and goes, or differ between a page that shows
        it and one that does not.
        """
        policy = widget.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        widget.setSizePolicy(policy)
        holder = QVBoxLayout()
        holder.setContentsMargins(0, max(0, MiniDevicePanel.ICON_TOP - top_padding), 0, 0)
        holder.addWidget(widget)
        holder.addStretch()
        self._left.insertLayout(self._left.count() - 1, holder)  # after the stretch, before the gap
        width = widget.sizeHint().boundedTo(widget.maximumSize()).expandedTo(widget.minimumSize()).width()
        self._right.insertSpacing(1, width)

    def add_right(self, widget: QWidget) -> None:
        """Append to the right group, after its leading stretch."""
        self._right.addWidget(widget, alignment=Qt.AlignmentFlag.AlignVCenter)
