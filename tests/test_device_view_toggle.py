"""DeviceViewToggle (telemffb/ui/widgets/DeviceViewToggle.py) - the small
button on the Active Devices frame, and beside each compact icon row, that
switches between the two.

What it switches is MainWindow's business (one preference, also behind the
Window menu); here it is the button itself: which way each one says it
goes, and where the frame's one puts itself - on the title line, which a
group box has no slot for.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from telemffb.ui.widgets.DeviceViewToggle import DeviceViewToggle

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _frame(height=400):
    frame = QGroupBox("Active Devices")
    layout = QVBoxLayout(frame)
    content = QLabel("icons")
    content.setMinimumSize(120, 200)
    layout.addWidget(content)
    layout.addStretch()
    frame.resize(140, height)
    return frame, content


class TestPinnedToTheTitle:
    def test_the_frame_is_made_wide_enough_for_title_and_button(self, qapp):
        """The frame is otherwise as wide as its title alone, and the
        button would sit on the last letters."""
        frame, _ = _frame()
        frame.setTitle("A Considerably Longer Frame Title")
        toggle = DeviceViewToggle(frame_shown=True)
        toggle.pin_to_title(frame)
        frame.show()
        QApplication.processEvents()
        font = QFont(frame.font())
        font.setBold(True)
        title_width = QFontMetrics(font).horizontalAdvance(frame.title())
        assert toggle.x() >= 10 + title_width
        frame.close()

class TestRevealOnHover:
    """Beside a compact row the glyph is not drawn until the mouse is over
    the row's header bar."""

    def _host_and_toggle(self):
        host = QWidget()
        layout = QHBoxLayout(host)
        toggle = DeviceViewToggle(frame_shown=False)
        layout.addWidget(toggle)
        layout.addWidget(QLabel("icons"))
        toggle.reveal_on_hover(host)
        host.show()
        QApplication.processEvents()
        # The offscreen platform's cursor sits at the origin, inside the
        # window it has just shown, and Qt duly reports the host entered.
        # Start every test from "the mouse is elsewhere".
        QApplication.sendEvent(host, QEvent(QEvent.Type.Leave))
        return host, toggle

    def _draws_something(self, toggle):
        image = QImage(toggle.size(), QImage.Format.Format_ARGB32)
        blank = QColor(1, 2, 3)
        image.fill(blank)
        # The widget's own painting only - by default render() lays the
        # window background down first, which is not the glyph.
        toggle.render(image, flags=QWidget.RenderFlag.DrawChildren)
        return any(image.pixelColor(x, y) != blank
                   for x in range(image.width()) for y in range(image.height()))

    def test_entering_the_host_draws_it_and_leaving_clears_it(self, qapp):
        host, toggle = self._host_and_toggle()
        QApplication.sendEvent(host, QEvent(QEvent.Type.Enter))
        assert toggle.is_revealed()
        assert self._draws_something(toggle)
        QApplication.sendEvent(host, QEvent(QEvent.Type.Leave))
        assert not toggle.is_revealed()
        assert not self._draws_something(toggle)
        host.close()

    def test_it_keeps_its_place_and_its_clicks_while_undrawn(self, qapp):
        """Only the painting stops: it is still a visible, clickable widget
        of the same size, so nothing beside it moves."""
        host, toggle = self._host_and_toggle()
        assert toggle.isVisible()
        assert toggle.size() == toggle.sizeHint()
        seen = []
        toggle.clicked.connect(lambda: seen.append(True))
        toggle.click()
        assert seen == [True]
        host.close()


class TestClickOrDrag:
    """A click switches views; a drag tears the devices off to float. One
    press must not be taken for both - a drag that also clicked would flip
    the view a second time as it was let go."""

    def _toggle(self):
        toggle = DeviceViewToggle(frame_shown=False)
        toggle.set_draggable(True)
        toggle.show()
        QApplication.processEvents()
        clicks, drags = [], []
        toggle.clicked.connect(lambda: clicks.append(True))
        toggle.drag_started.connect(drags.append)
        return toggle, clicks, drags

    def test_a_press_moved_past_the_drag_distance_is_a_drag_and_not_a_click(self, qapp):
        toggle, clicks, drags = self._toggle()
        center = toggle.rect().center()
        far = center + QPoint(QApplication.startDragDistance() + 10, 0)
        QTest.mousePress(toggle, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(toggle, far)
        QTest.mouseMove(toggle, far + QPoint(30, 0))
        QTest.mouseRelease(toggle, Qt.MouseButton.LeftButton, pos=center)  # let go back over the button
        assert len(drags) == 1
        assert clicks == []
        toggle.close()

    def test_a_press_let_go_in_place_is_still_a_click(self, qapp):
        toggle, clicks, drags = self._toggle()
        QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        assert clicks == [True]
        assert drags == []
        toggle.close()

    def test_a_button_not_made_draggable_never_drags(self, qapp):
        toggle = DeviceViewToggle(frame_shown=False)
        toggle.show()
        drags = []
        toggle.drag_started.connect(drags.append)
        center = toggle.rect().center()
        QTest.mousePress(toggle, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(toggle, center + QPoint(80, 0))
        QTest.mouseRelease(toggle, Qt.MouseButton.LeftButton, pos=center + QPoint(80, 0))
        assert drags == []
        toggle.close()
