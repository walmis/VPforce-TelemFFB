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

"""DeviceViewToggle: the small button that switches the devices between the
Active Devices frame down the window's left edge and the compact icon rows
in the tab page headers.

One sits on each of the two, so the switch is where the thing it switches
is, and not only in the Window menu. It draws the usual "toggle sidebar"
glyph - a window outline with a side panel - with the panel filled in while
the frame is showing, which says what a click will do without an arrow: the
frame does not slide anywhere an arrow could point, it folds up into the
header.

Painted rather than an image so it follows the palette in both themes and
stays crisp at any scaling. Quiet until hovered: it is a preference, not
something to reach for often. Beside a compact row it goes further and draws
nothing at all until the mouse is over the row's header bar
(``reveal_on_hover``), so the header is just the icons until you go to it.
"""
from PyQt6.QtCore import QEvent, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QAbstractButton, QWidget

#: Glyph size and the padding around it. On the frame the button's own
#: background covers that padding, which is what breaks the frame's border
#: line either side of the glyph, the way the frame's title does.
_GLYPH = QSize(15, 11)
_PAD_X = 4
_PAD_Y = 2
#: Where the frame's stylesheet puts its title (styles.py: QGroupBox::title
#: is 10px in, with 3px of padding either side), the clear space kept
#: between title and button, and the button's distance from the frame's
#: right edge - far enough in to clear the rounded corner.
_TITLE_LEFT = 10 + 3
_TITLE_RIGHT_PADDING = 3
_TITLE_GAP = 4
_RIGHT_INSET = 8


class DeviceViewToggle(QAbstractButton):
    """``frame_shown`` says which glyph to draw and which tooltip to give:
    True for the button on the Active Devices frame, False for the one
    beside a compact icon row."""

    #: How far below the button's top edge its glyph starts.
    GLYPH_TOP = _PAD_Y

    def __init__(self, frame_shown: bool, parent: QWidget = None):
        super().__init__(parent)
        self._frame_shown = frame_shown
        self._pinned_to = None
        self._reveal_host = None
        self._host_hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("Show compact device icons" if frame_shown
                        else "Show the Active Devices panel")
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        return QSize(_GLYPH.width() + 2 * _PAD_X, _GLYPH.height() + 2 * _PAD_Y)

    def pin_to_title(self, frame: QWidget) -> None:
        """Become ``frame``'s child and keep to the right-hand end of its
        title line - a group box has no slot for a widget up there.

        The frame is otherwise only as wide as its title, which would
        leave the button sitting on the last letters, so the frame is
        given a minimum width with room for both.
        """
        self.setParent(frame)
        self._pinned_to = frame
        # Opaque, so the frame's border line stops either side of the glyph.
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Window)
        frame.installEventFilter(self)
        self._place()
        self.show()

    def reveal_on_hover(self, host: QWidget) -> None:
        """Draw nothing until the mouse is over ``host``.

        The button stays where it is, taking its space and its clicks -
        only the painting stops - so nothing beside it moves as it comes
        and goes, and being shown or hidden stays its owner's call. A
        widget is not told the mouse has left it when the mouse moves onto
        one of its children, so being over the button, or an icon, counts
        as being over ``host``.
        """
        self._reveal_host = host
        host.installEventFilter(self)

    def is_revealed(self) -> bool:
        return self._reveal_host is None or self._host_hovered

    def eventFilter(self, obj, event):
        if obj is self._pinned_to and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange,
                QEvent.Type.StyleChange, QEvent.Type.Polish):
            self._place()
        elif obj is self._reveal_host and event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self._host_hovered = event.type() == QEvent.Type.Enter
            self.update()
        return False

    def _title_metrics(self) -> QFontMetrics:
        font = QFont(self._pinned_to.font())
        font.setBold(True)  # the stylesheet's QGroupBox rule; not in font()
        return QFontMetrics(font)

    def _place(self) -> None:
        frame = self._pinned_to
        metrics = self._title_metrics()
        title_end = _TITLE_LEFT + metrics.horizontalAdvance(frame.title()) + _TITLE_RIGHT_PADDING
        needed = title_end + _TITLE_GAP + self.width() + _RIGHT_INSET
        if frame.minimumWidth() < needed:
            frame.setMinimumWidth(needed)
        self.move(frame.width() - self.width() - _RIGHT_INSET,
                  max(0, (metrics.height() - self.height()) // 2 + 1))  # +1: optical center of the caps
        self.raise_()

    def paintEvent(self, event) -> None:
        if not self.is_revealed():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        if self.underMouse() and self.isEnabled():
            color = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)
        else:
            color = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        if self.isDown():
            color = QColor(color).darker(130)

        outline = QRectF(_PAD_X + 0.5, _PAD_Y + 0.5, _GLYPH.width() - 1, _GLYPH.height() - 1)
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(outline, 2, 2)

        divider_x = outline.left() + 5
        side_panel = QRectF(outline.left(), outline.top(), 5, outline.height())
        if self._frame_shown:
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(side_panel, 2, 2)
        else:
            painter.drawLine(int(divider_x), int(outline.top()) + 1,
                             int(divider_x), int(outline.bottom()))
        painter.end()
