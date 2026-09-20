"""DeviceStrip (telemffb/ui/widgets/DeviceStrip.py) - the compact device
row, here in the state it is a window in: floating.

MainWindow decides where it goes and mirrors the devices onto it; what is
pinned here is how it behaves once it is floating: dragged by its grip and
not by its icons, saying where it was left, closing by asking for the
devices back rather than losing them - without getting in the way of the
application shutting down - and never able to end up lost behind the
window it belongs to.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from telemffb.ui.widgets.DeviceStrip import DeviceStrip, _Grip

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def strip(qapp):
    s = DeviceStrip()
    s.device_mini_panel.set_devices(['joystick', 'pedals', 'collective', 'trimwheel'])
    s.float_off(QPoint(300, 300))
    QApplication.processEvents()
    yield s
    s.hide()
    s.setParent(None)
    _delete(s)


def _delete(widget):
    """Delete it now, rather than whenever the garbage collector gets to it:
    a widget being torn down halfway through the next test sends events to
    a strip that is still watching it, and takes the process with it."""
    widget.deleteLater()
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _drag(widget, by: QPoint):
    start = widget.rect().center()
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(widget, start + by)
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=start + by)
    QApplication.processEvents()


class TestKeepOnTop:
    def test_turning_it_on_keeps_the_strip_showing(self, strip):
        """Changing a window's flags hides it."""
        strip.set_keep_on_top(True)
        assert strip.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert strip.isVisible()
        strip.set_keep_on_top(False)
        assert not strip.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert strip.isVisible()


class TestDragging:
    def test_the_grip_moves_the_strip(self, strip):
        before = strip.pos()
        _drag(strip.findChild(_Grip), QPoint(40, 25))
        assert strip.pos() == before + QPoint(40, 25)

    def test_a_drag_reports_where_it_ended(self, strip):
        moved = []
        strip.moved.connect(moved.append)
        _drag(strip.findChild(_Grip), QPoint(40, 25))
        assert moved == [strip.pos()]

    def test_an_icon_does_not_drag_it(self, strip):
        """The icons keep their click - switch to that device - so a press
        that starts on one moves nothing."""
        before = strip.pos()
        chip = next(iter(strip.device_mini_panel.chips.values()))
        _drag(chip, QPoint(40, 25))
        assert strip.pos() == before

    def test_a_click_without_a_drag_reports_nothing(self, strip):
        moved = []
        strip.moved.connect(moved.append)
        QTest.mouseClick(strip.device_mini_panel, Qt.MouseButton.LeftButton)
        assert moved == []


class TestClosing:
    def test_the_user_closing_it_asks_for_the_devices_back(self, strip):
        """Alt+F4, there being no title bar. The devices have to show
        somewhere, so it stays up and asks rather than vanishing."""
        asked = []
        strip.dock_requested.connect(lambda: asked.append(True))

        class Spontaneous(QCloseEvent):
            def spontaneous(self):
                return True

        event = Spontaneous()
        strip.closeEvent(event)
        assert asked == [True]
        assert not event.isAccepted()

    def test_the_application_closing_it_is_not_refused(self, strip):
        """A window that refuses to close blocks the application quitting."""
        asked = []
        strip.dock_requested.connect(lambda: asked.append(True))
        event = QCloseEvent()
        strip.closeEvent(event)
        assert asked == []
        assert event.isAccepted()


class TestNotGettingLost:
    """It has no taskbar button, so it must not be able to end up behind
    the main window. An owned window is kept in front of its owner by the
    window system; the always-on-top strip is in front of everything
    already, and is left unowned so it does not minimize with the owner."""

    @pytest.fixture
    def owner(self, qapp):
        from PyQt6.QtWidgets import QMainWindow
        window = QMainWindow()
        window.show()
        QApplication.processEvents()
        yield window
        window.close()
        _delete(window)

    def test_it_is_owned_by_the_window_it_is_given(self, strip, owner):
        strip.set_owner_window(owner)
        assert strip.windowHandle().transientParent() is owner.windowHandle()

    def test_kept_on_top_it_is_not_owned(self, strip, owner):
        strip.set_owner_window(owner)
        strip.set_keep_on_top(True)
        QApplication.processEvents()
        assert strip.windowHandle().transientParent() is None

    def test_it_is_not_a_child_widget_of_its_owner(self, strip, owner):
        """A child would be hidden whenever the owner is - and the strip
        outlives a trip to the tray."""
        strip.set_owner_window(owner)
        assert strip.parent() is None
        owner.hide()
        QApplication.processEvents()
        assert strip.isVisible()


class TestSize:
    def test_no_taller_than_its_icons_need(self, qapp):
        """It came up twice the height of its row, which left the glyph -
        level with the icons' top - adrift above them. The window is held
        to the size of what it holds."""
        strip = DeviceStrip()
        strip.device_mini_panel.set_devices(['joystick', 'pedals', 'collective', 'trimwheel'])
        strip.float_off(QPoint(0, 0))
        QApplication.processEvents()
        row = strip.device_mini_panel
        assert strip.height() == strip.sizeHint().height()
        assert strip.height() <= row.height() + 8
        assert row.y() <= strip.toggle.mapTo(strip, QPoint(0, 0)).y() < row.y() + row.height()
        strip.hide()


class TestConfinedToTheMainWindow:
    """The other way it floats: a child of the main window, over its layout,
    dragged about inside it and no further. It moves with the window and
    cannot be lost, which the free strip can only approximate."""

    @pytest.fixture
    def owner(self, qapp):
        from PyQt6.QtWidgets import QMainWindow
        window = QMainWindow()
        window.resize(900, 600)
        window.move(100, 100)
        window.show()
        QApplication.processEvents()
        yield window
        window.close()
        _delete(window)

    @pytest.fixture
    def confined(self, strip, owner):
        strip.set_owner_window(owner)
        strip.set_confined(True)
        strip.float_off(QPoint(40, 60))
        QApplication.processEvents()
        yield strip
        # Out of the window before the window goes: as its child the strip
        # would be destroyed with it, under the ``strip`` fixture's feet.
        strip.set_confined(False)

    def test_it_is_a_child_of_the_window(self, confined, owner):
        assert confined.confined()
        assert confined.parent() is owner
        assert not confined.isWindow()

    def test_a_drag_stops_at_the_windows_edges(self, confined, owner):
        _drag(confined.findChild(_Grip), QPoint(5000, 5000))
        assert confined.x() + confined.width() == owner.width()
        assert confined.y() + confined.height() == owner.height()
        _drag(confined.findChild(_Grip), QPoint(-5000, -5000))
        assert confined.pos() == QPoint(0, 0)

    def test_the_window_shrinking_keeps_it_inside(self, confined, owner):
        confined.place(QPoint(600, 500))
        owner.resize(500, 300)
        QApplication.processEvents()
        assert confined.x() + confined.width() <= owner.width()
        assert confined.y() + confined.height() <= owner.height()

    def test_it_goes_back_where_it_was_put_when_there_is_room_again(self, confined, owner):
        """The Hide tab collapses the window; that must not drag the strip
        up it for good."""
        confined.place(QPoint(600, 500))
        owner.resize(500, 300)
        QApplication.processEvents()
        owner.resize(900, 600)
        QApplication.processEvents()
        assert confined.pos() == QPoint(600, 500)

    def test_placed_while_the_window_is_small_it_still_ends_up_where_asked(self, confined, owner):
        owner.resize(500, 300)
        QApplication.processEvents()
        confined.place(QPoint(600, 500))
        owner.resize(900, 600)
        QApplication.processEvents()
        assert confined.pos() == QPoint(600, 500)

    def test_set_free_it_is_a_window_of_its_own_in_the_same_place(self, confined, owner):
        on_screen = confined.mapToGlobal(QPoint(0, 0))
        confined.set_confined(False)
        QApplication.processEvents()
        assert confined.parent() is None and confined.isWindow()
        assert confined.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert confined.pos() == on_screen
        assert confined.isVisible()

    def test_confined_again_it_comes_back_inside(self, confined, owner):
        confined.set_confined(False)
        confined.move(owner.mapToGlobal(QPoint(0, 0)) + QPoint(3000, 3000))
        confined.set_confined(True)
        QApplication.processEvents()
        assert confined.parent() is owner
        assert confined.x() + confined.width() <= owner.width()
        assert confined.y() + confined.height() <= owner.height()

    def test_keep_on_top_waits_until_it_has_a_window_of_its_own(self, confined):
        confined.set_keep_on_top(True)
        assert confined.parent() is not None  # still a plain child
        confined.set_confined(False)
        assert confined.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
