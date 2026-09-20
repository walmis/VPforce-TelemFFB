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

"""CornerLogo: the app logo, floating over the window's top-right corner.

As the menu bar's corner widget the logo could be no taller than the bar
without making the bar taller, and everything under it lower. Floating, it
runs from the top of the window down to whatever widget it is told to stay
clear of (the Application Status box), over the menu bar's unused right end
and the margin beneath it - room that is there anyway - and takes part in
no layout, so its size moves nothing.

It sizes itself to that band each time the window is laid out rather than
to a fixed height, since the band's height follows the font and the DPI.
"""
import logging

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtWidgets import QLabel, QWidget

from telemffb.utils import HiDpiPixmap

#: Clear space above the logo and between it and the widget below it.
TOP_MARGIN = 4
BOTTOM_GAP = 3
RIGHT_MARGIN = 8
#: Clear space kept between the last menu and the logo; narrower than
#: this and the logo is hidden rather than drawn over the menus.
MENU_CLEARANCE = 12
#: Bounds on the logo's height, whatever band the layout leaves it.
MIN_HEIGHT = 16
MAX_HEIGHT = 64


class CornerLogo(QLabel):
    """``window``'s logo, kept in its top-right corner and above ``below``.

    ``menubar`` is only consulted for where its last menu ends.
    """

    def __init__(self, window: QWidget, below: QWidget, menubar, logo_path: str):
        super().__init__(window)
        self._below = below
        self._menubar = menubar
        self._logo_path = logo_path
        self._scaled_height = 0
        self._loadable = True
        # Purely decorative: clicks go to whatever is under it.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()
        window.installEventFilter(self)
        below.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move,
                            QEvent.Type.Show, QEvent.Type.LayoutRequest):
            self.place()
        return False

    def _scale_to(self, height: int) -> bool:
        if height == self._scaled_height:
            return True
        pixmap = HiDpiPixmap(self._logo_path)
        if pixmap.width() <= 0 or pixmap.height() <= 0:
            logging.warning("Logo resource %s could not be loaded; skipping app logo", self._logo_path)
            self._loadable = False
            return False
        width = round(pixmap.width() * height / pixmap.height())
        self.setPixmap(pixmap.scaled_logical(width, height))
        self.setFixedSize(width, height)
        self._scaled_height = height
        return True

    def place(self) -> None:
        """Fit the band above ``below`` and sit in the window's corner."""
        window = self.parentWidget()
        if not self._loadable or window is None or not self._below.isVisible():
            self.hide()
            return
        band = self._below.mapTo(window, QPoint(0, 0)).y()
        height = min(MAX_HEIGHT, band - TOP_MARGIN - BOTTOM_GAP)
        if height < MIN_HEIGHT or not self._scale_to(height):
            self.hide()
            return
        x = window.width() - self.width() - RIGHT_MARGIN
        actions = self._menubar.actions() if self._menubar is not None else []
        menus_end = self._menubar.actionGeometry(actions[-1]).right() if actions else 0
        if x < menus_end + MENU_CLEARANCE:
            self.hide()
            return
        self.move(x, TOP_MARGIN)
        self.show()
        self.raise_()
