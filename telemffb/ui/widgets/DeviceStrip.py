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

"""DeviceStrip: the compact device row - the small per-device icons, the
view-toggle glyph beside them and the grip - that moves between the places
the devices can be shown, and floats when it is in none of them.

One strip, moved from slot to slot (``DeviceSlot``), rather than a row built
into each place: the places differ only in where they are, and a copy in
each meant every device change being mirrored onto all of them, every slot
having its own idea of when to show, and four near-identical ways to put the
glyph beside the icons.

Floating, it is a window in its own right - or a child of the main window
when it is confined to it, which is the default: it moves with the window,
is minimized with it, and cannot be lost behind it. Free of the window it
can go on another monitor or over a sim; having no taskbar button of its
own it is then made an owned window of the main one, which the window
system keeps in front of its owner - unless it is kept on top of everything
anyway, when it is left unowned so that it does not minimize along with the
main window.

Dragging is by the grip (or the strip's own margins), never by the icons,
which keep their click: switch to that device.
"""
from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (QHBoxLayout, QLayout, QSizePolicy, QVBoxLayout,
                             QWidget)

from telemffb.ui.panels.DevicePanel import MiniDevicePanel
from telemffb.ui.widgets.DeviceViewToggle import DeviceViewToggle

_GRIP_WIDTH = 12
_BORDER = QColor("gray")  # the group boxes' border, styles.py
#: Margins around the strip's contents: roomier floating, where it is a
#: window with a border of its own to clear.
_DOCKED_MARGINS = (0, 0, 0, 0)
_FLOATING_MARGINS = (4, 2, 8, 2)


class DeviceSlot(QWidget):
    """A place the device strip can be docked into.

    Takes no space while it holds nothing, so a slot the strip is not in
    costs its host no room - which the Application Status box, where the
    row can be the tallest thing in its column, would otherwise pay for
    whichever view is in effect.
    """

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.hide()

    def holds(self, strip: "DeviceStrip") -> bool:
        return strip.parentWidget() is self

    def take(self, strip: "DeviceStrip") -> None:
        if not self.holds(strip):
            strip.dock_into(self)
            self.layout().addWidget(strip)
        strip.show()  # it may have been hidden while this slot kept it
        self.show()

    def release(self) -> None:
        self.hide()


class _Grip(QWidget):
    """The drag handle: two columns of dots, and the move cursor."""

    def __init__(self, strip: "DeviceStrip"):
        super().__init__(strip)
        self._strip = strip
        self.setFixedWidth(_GRIP_WIDTH)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Drag to move.\nDrop on a highlighted area to dock there (hold Ctrl not to).")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorGroup.Disabled,
                                              QPalette.ColorRole.WindowText))
        top = (self.height() - 5 * 5) // 2
        for column in (3, 8):
            for row in range(5):
                painter.drawEllipse(QPoint(column, top + row * 5 + 2), 1, 1)
        painter.end()

    def mousePressEvent(self, event) -> None:
        self._strip.begin_drag(event)

    def mouseMoveEvent(self, event) -> None:
        self._strip.continue_drag(event)

    def mouseReleaseEvent(self, event) -> None:
        self._strip.end_drag(event)


class DeviceStrip(QWidget):
    """``interactive`` is False for the stand-in row a detached window gets:
    it shows the devices and switches between them, but has no glyph and no
    grip - the view it would change, and the window it would float over,
    are not the ones it is in.

    ``moved`` reports where a drag left it, for whoever remembers that: in
    the main window's coordinates while confined to it, the screen's while
    free. ``dock_requested`` is the user closing the free strip (Alt+F4,
    there being no title bar): the devices have to show somewhere, so the
    owner is asked to put them back in the main window rather than the
    strip simply vanishing.
    """

    DeviceClicked = pyqtSignal(str)
    moved = pyqtSignal(QPoint)
    dock_requested = pyqtSignal()
    #: The glyph beside the icons: clicked, and dragged off to float.
    view_toggled = pyqtSignal()
    tear_off = pyqtSignal(QPoint)
    #: Where the cursor is on the screen, throughout a drag and at its end,
    #: for an owner that offers somewhere to drop the strip. A handler of
    #: ``drag_ended`` that takes the drop calls ``revert_drag``.
    drag_moved = pyqtSignal(QPoint)
    drag_ended = pyqtSignal(QPoint)

    def __init__(self, interactive: bool = True, keep_on_top: bool = False):
        super().__init__(None)
        self._floating = False
        self._keep_on_top = keep_on_top
        self._drag_offset = None
        self._drag_from = None
        self._grabbing = False
        self._owner = None
        # A window of its own until it is given an owner to be confined to
        # (MainWindow does, from the saved preference).
        self._confined = False
        self._wanted = None  # confined: where it was put, as opposed to where it currently fits
        self.setWindowTitle("TelemFFB Devices")
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Window)

        self.device_mini_panel = MiniDevicePanel()
        self.device_mini_panel.DeviceClicked.connect(self.DeviceClicked.emit)
        self.device_mini_panel.show()

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(*_DOCKED_MARGINS)
        self._row.setSpacing(6)

        self._grip = _Grip(self)
        self._grip.setVisible(False)
        self._row.addWidget(self._grip)

        self.toggle = None
        if interactive:
            # Kept its space when hidden, so the icons do not shift as it
            # comes and goes with the mouse.
            self.toggle = DeviceViewToggle(frame_shown=False)
            policy = self.toggle.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            self.toggle.setSizePolicy(policy)
            self.toggle.reveal_on_hover(self)
            self.toggle.clicked.connect(self.view_toggled.emit)
            self.toggle.drag_started.connect(self.tear_off.emit)
            self.toggle.set_draggable(True)
            # Level with the top of the icons, not the middle of the row.
            holder = QVBoxLayout()
            holder.setContentsMargins(
                0, max(0, MiniDevicePanel.ICON_TOP - DeviceViewToggle.GLYPH_TOP), 0, 0)
            holder.addWidget(self.toggle)
            holder.addStretch()
            self._row.addLayout(holder)

        self._row.addWidget(self.device_mini_panel)

    # -- where it lives ----------------------------------------------------

    def floating(self) -> bool:
        return self._floating

    def dock_into(self, slot: QWidget) -> None:
        """Become a plain child of ``slot`` - laid out by it, no window of
        its own, no grip and no border."""
        self._floating = False
        self._grip.setVisible(False)
        self._row.setContentsMargins(*_DOCKED_MARGINS)
        self._row.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        if self._owner is not None:
            self._owner.removeEventFilter(self)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setParent(slot)
        self.show()

    def float_off(self, at: QPoint = None) -> None:
        """Leave whatever slot it is in and float: a window of its own, or
        a free-placed child of the owner window while confined to it.

        ``at`` is where to put it (the owner window's coordinates while
        confined, the screen's while free); left out, it keeps where it is.
        """
        was_floating = self._floating
        self._floating = True
        self._grip.setVisible(True)
        self._row.setContentsMargins(*_FLOATING_MARGINS)
        # Exactly the size of what it holds, and kept so: nothing here
        # gains from spare room, and left to itself the window came up
        # twice the height of its icons, which set the view-toggle glyph -
        # level with their top - adrift above them.
        self._row.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        if not was_floating:
            self._apply_float_parent()
        if at is not None:
            self.place(at)
        self.show()
        self.raise_()

    def _apply_float_parent(self) -> None:
        if self._in_owner:
            self.setWindowFlags(Qt.WindowType.Widget)
            self.setParent(self._owner)
            self._owner.installEventFilter(self)
        else:
            if self._owner is not None:
                self._owner.removeEventFilter(self)
            self.setParent(None)
            self.setWindowFlags(self._window_flags())

    def _window_flags(self):
        # Tool: no taskbar button of its own. Frameless: it is a strip, not
        # a dialog - the grip is its title bar.
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        if self._keep_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def set_owner_window(self, owner: QWidget) -> None:
        """The main window: what the strip is confined to, or while free
        must never end up behind."""
        self._owner = owner
        # The owner being torn down destroys this strip with it when it is
        # a child, and the events that go with that reach the filter below.
        owner.destroyed.connect(self._retire)
        if self._floating:
            self._apply_float_parent()  # confined, it belongs inside the owner
        self._apply_ownership()

    def _retire(self, *_) -> None:
        self._owner = None

    def confined(self) -> bool:
        return self._confined

    @property
    def _in_owner(self) -> bool:
        """Floating inside the owner window rather than as a window of its
        own - which asking to be confined only amounts to once there is an
        owner to be confined to."""
        return self._confined and self._owner is not None

    def set_confined(self, confined: bool) -> None:
        """Float inside the owner window, or as a window of its own. It
        stays where it is on the screen across the change, as near as the
        owner window's edges allow."""
        if confined == self._confined or self._owner is None:
            return
        was_visible = self.isVisible()
        on_screen = self.mapToGlobal(QPoint(0, 0))
        self._confined = confined
        if not self._floating:
            return  # docked: applied when it next floats
        self._apply_float_parent()
        self.place(self._owner.mapFromGlobal(on_screen) if self._in_owner else on_screen)
        if was_visible:
            self.show()
            self.raise_()

    def place(self, pos: QPoint) -> None:
        """Go to ``pos`` - kept inside the owner window while confined."""
        if self._in_owner:
            # Wanted as asked, shown as it fits: it may be placed while the
            # window is smaller than it is about to be (coming off the Hide
            # tab), and should end up where it was asked to go.
            self._wanted = pos
            pos = self._clamped(pos)
        self.move(pos)

    def _clamped(self, pos: QPoint) -> QPoint:
        room = self._owner.rect()
        size = self.sizeHint().expandedTo(self.size()) if self.isVisible() else self.sizeHint()
        return QPoint(max(0, min(pos.x(), room.width() - size.width())),
                      max(0, min(pos.y(), room.height() - size.height())))

    def moveEvent(self, event) -> None:
        # Wherever it ends up, a confined strip is inside its window:
        # becoming its child again hands it back the position it had as a
        # window of its own, which is anywhere on the screen.
        super().moveEvent(event)
        if self._floating and self._in_owner:
            fitted = self._clamped(self.pos())
            if fitted != self.pos():
                self.move(fitted)

    def eventFilter(self, obj, event):
        # The owner window shrinking must not leave a confined strip
        # outside it, where it could be neither seen nor dragged back. It
        # is fitted from where it was put, not from where it last fitted,
        # so it goes back there when the window has room again - the Hide
        # tab's collapse does not drag it up the window for good.
        if (obj is self._owner and self._floating and self._in_owner
                and event.type() == QEvent.Type.Resize and self._wanted is not None):
            self.move(self._clamped(self._wanted))
        return False

    def _apply_ownership(self) -> None:
        # By window handle, not as a child widget: a child would be hidden
        # whenever the owner is, and the free strip outlives a trip to the
        # tray. A confined or docked strip is a child, and has no window to
        # own.
        if self._in_owner or not self._floating:
            return
        handle = self.windowHandle()
        if handle is None:
            return  # not created yet; showEvent comes back here
        owner = None
        if self._owner is not None and not self._keep_on_top:
            owner = self._owner.windowHandle()
        if handle.transientParent() is not owner:
            handle.setTransientParent(owner)

    def showEvent(self, event) -> None:
        # The native window is made on first show, and made again whenever
        # the flags change, so this is where it can be given its owner.
        self._apply_ownership()
        super().showEvent(event)

    def keep_on_top(self) -> bool:
        return self._keep_on_top

    def set_keep_on_top(self, keep_on_top: bool) -> None:
        """Stay above every other window - a sim included - or only take
        its turn like any other. Changing a window's flags hides it, so it
        is shown again if it was showing."""
        if keep_on_top == self._keep_on_top:
            return
        self._keep_on_top = keep_on_top
        if self._in_owner or not self._floating:
            return  # no window of its own to flag; applied when it next has one
        was_visible = self.isVisible()
        self.setWindowFlags(self._window_flags())
        if was_visible:
            self.show()

    # -- dragging: the grip forwards here, and the strip's own margins count too

    def begin_drag(self, event) -> None:
        if self._floating and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.mapToGlobal(QPoint(0, 0))
            self._drag_from = self.pos()
            self.raise_()

    def continue_drag(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            on_screen = event.globalPosition().toPoint() - self._drag_offset
            self.move(self._clamped(self._owner.mapFromGlobal(on_screen))
                      if self._in_owner else on_screen)
            self.drag_moved.emit(event.globalPosition().toPoint())

    def drag_under_cursor(self, on_screen: QPoint) -> None:
        """Carry on, as a drag of this strip, a drag that began somewhere
        else (the view-toggle glyph torn off): jump under the cursor, grip
        first, and take the mouse until the button is let go.

        Where it was before the jump is where ``revert_drag`` puts it back,
        and what decides whether the drag counts as a move.
        """
        self._drag_from = self.pos()
        self._drag_offset = QPoint(_FLOATING_MARGINS[0] + _GRIP_WIDTH // 2, self.height() // 2)
        target = on_screen - self._drag_offset
        self.move(self._clamped(self._owner.mapFromGlobal(target)) if self._in_owner else target)
        self.raise_()
        self._grabbing = True
        self.grabMouse(Qt.CursorShape.SizeAllCursor)

    def revert_drag(self) -> None:
        """Put the strip back where the drag began, and report no move: the
        drop was taken as something other than "float here" (a dock), and
        where it happened to be let go is not where it should float next
        time."""
        if self._drag_from is not None:
            self.move(self._drag_from)

    def end_drag(self, event) -> None:
        if self._drag_offset is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            if self._grabbing:
                # Before anything is told: a handler may hide the strip (a
                # dock), and a hidden widget must not be holding the mouse.
                self._grabbing = False
                self.releaseMouse()
            self.drag_ended.emit(event.globalPosition().toPoint())  # may revert_drag()
            if self.pos() != self._drag_from:
                if self._in_owner:
                    self._wanted = self.pos()
                self.moved.emit(self.pos())

    def mousePressEvent(self, event) -> None:
        # An icon does not take the press itself (only the release, its
        # click), so one that starts on an icon arrives here too. It is
        # not a drag: the icons keep their click and move nothing.
        under = self.childAt(event.position().toPoint())
        if under is not None and (under is self.device_mini_panel
                                  or self.device_mini_panel.isAncestorOf(under)):
            return
        self.begin_drag(event)

    def mouseMoveEvent(self, event) -> None:
        self.continue_drag(event)

    def mouseReleaseEvent(self, event) -> None:
        self.end_drag(event)

    def paintEvent(self, event) -> None:
        if not self._floating:
            return  # docked it is part of whatever it sits in, not a thing of its own
        painter = QPainter(self)
        painter.setPen(QPen(_BORDER, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()

    def closeEvent(self, event) -> None:
        # A close that comes from outside the application is the user's
        # (Alt+F4): put the devices back in the main window. One that comes
        # from inside it is the application shutting down, which a window
        # refusing to close would block.
        if event.spontaneous():
            event.ignore()
            self.dock_requested.emit()
        else:
            event.accept()
