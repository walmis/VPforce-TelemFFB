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

"""CornerDeviceSlot: the slot in the band across the top of the window,
beside the app logo - where the device strip goes in the "menu bar" view.

Like the logo (``telemffb.ui.widgets.CornerLogo``) it floats over the menu
bar's unused right-hand end and the margin beneath it, and takes part in no
layout: the band is there anyway, so the strip costs the window no height.

Whether it shows is decided by whether it holds the strip; where it can
show also depends on there being room. In a window too narrow for the
menus, the strip and the logo at full size, the logo gives way in stages -
it is decoration, the strip is not: it shrinks to what room the strip
leaves it, then goes altogether and the strip takes the corner. Only if the
strip still would not clear the menus does it go itself.
"""
from PyQt6.QtCore import QEvent, QPoint

from telemffb.ui.widgets.CornerLogo import RIGHT_MARGIN as LOGO_RIGHT_MARGIN
from telemffb.ui.widgets.DeviceStrip import DeviceSlot

#: Clear space between the slot and the logo (or the window's edge), and
#: between the last menu and the slot.
_LOGO_GAP = 10
_EDGE_MARGIN = 8
_MENU_CLEARANCE = 12


class CornerDeviceSlot(DeviceSlot):
    """Kept in ``window``'s top band, above ``below`` and left of ``logo``.
    ``menubar`` is only consulted for where its last menu ends."""

    def __init__(self, window, below, menubar, logo=None):
        super().__init__(window)
        self._below = below
        self._menubar = menubar
        self._logo = logo
        # Placing itself shows, hides and resizes widgets, which is what
        # the filter below listens for: without this it would be called
        # back into from inside itself, until the stack ran out.
        self._placing = False
        for watched in (window, below, logo):
            if watched is not None:
                watched.installEventFilter(self)
                # A window being torn down destroys its children in no
                # stated order, and the hides that go with that reach the
                # filter below: without this it would place itself against
                # a logo that is already gone.
                watched.destroyed.connect(self._retire)

    def take(self, strip) -> None:
        super().take(strip)
        self.place()

    def release(self) -> None:
        super().release()
        self._logo_gives()

    def _retire(self, *_) -> None:
        self._below = self._menubar = self._logo = None

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show,
                            QEvent.Type.Hide, QEvent.Type.LayoutRequest):
            self.place()
        return False

    def _logo_gives(self, width_limit=None, suppress: bool = False) -> None:
        if self._logo is not None:
            self._logo.set_suppressed(suppress)
            self._logo.set_width_limit(width_limit)

    def place(self) -> None:
        """Sit in the band above ``below``, left of the logo - or give way."""
        if self._placing or self._below is None:
            return
        self._placing = True
        try:
            self._place()
        finally:
            self._placing = False

    def _place(self) -> None:
        window = self.parentWidget()
        if self.layout().count() == 0 or window is None or not self._below.isVisible():
            self._logo_gives()
            self.hide()
            return
        self.adjustSize()
        actions = self._menubar.actions() if self._menubar is not None else []
        menus_end = self._menubar.actionGeometry(actions[-1]).right() if actions else 0
        leftmost = menus_end + _MENU_CLEARANCE
        # Worked out from the widths the logo could take, not from where it
        # is: it may be shrunk or hidden at the moment, on this slot's own
        # account. ``room`` is what the strip leaves to its right when it is
        # as far left as the menus allow.
        room = window.width() - leftmost - self.width()
        full = self._logo.natural_reserved_width() if self._logo is not None else 0
        smallest = self._logo.min_reserved_width() if self._logo is not None else 0
        if full and room >= full + _LOGO_GAP:
            self._logo_gives()
            x = window.width() - full - _LOGO_GAP - self.width()
        elif smallest and room >= smallest + _LOGO_GAP:
            self._logo_gives(width_limit=room - _LOGO_GAP - LOGO_RIGHT_MARGIN)
            x = window.width() - self._logo.reserved_width() - _LOGO_GAP - self.width()
        elif room >= _EDGE_MARGIN:
            self._logo_gives(suppress=True)
            x = window.width() - _EDGE_MARGIN - self.width()
        else:
            self._logo_gives()
            self.hide()
            return
        band = self._below.mapTo(window, QPoint(0, 0)).y()
        self.move(x, max(0, (band - self.height()) // 2))
        self.show()
        self.raise_()
