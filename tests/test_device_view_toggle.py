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

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPalette
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


class TestWhichWayItGoes:
    def test_the_frames_button_offers_the_compact_icons(self, qapp):
        assert "compact" in DeviceViewToggle(frame_shown=True).toolTip().lower()

    def test_a_rows_button_offers_the_panel(self, qapp):
        assert "panel" in DeviceViewToggle(frame_shown=False).toolTip().lower()

    def test_a_click_is_reported(self, qapp):
        toggle = DeviceViewToggle(frame_shown=True)
        seen = []
        toggle.clicked.connect(lambda: seen.append(True))
        toggle.click()
        assert seen == [True]


class TestPinnedToTheTitle:
    def test_sits_at_the_right_hand_end_of_the_title_line(self, qapp):
        frame, _ = _frame()
        toggle = DeviceViewToggle(frame_shown=True)
        toggle.pin_to_title(frame)
        frame.show()
        QApplication.processEvents()
        assert toggle.parentWidget() is frame
        assert toggle.y() <= 4
        assert 0 < frame.width() - (toggle.x() + toggle.width()) <= 12

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

    def test_follows_the_corner_as_the_frame_widens(self, qapp):
        frame, _ = _frame()
        toggle = DeviceViewToggle(frame_shown=True)
        toggle.pin_to_title(frame)
        frame.show()
        QApplication.processEvents()
        gap = frame.width() - toggle.x()
        frame.resize(frame.width() + 80, 400)
        QApplication.processEvents()
        assert frame.width() - toggle.x() == gap
        frame.close()

    def test_covers_the_border_line_behind_it(self, qapp):
        """Opaque, in the window color, so the frame's border stops either
        side of the glyph the way it does around the title."""
        frame, _ = _frame()
        toggle = DeviceViewToggle(frame_shown=True)
        toggle.pin_to_title(frame)
        assert toggle.autoFillBackground()
        assert toggle.backgroundRole() == QPalette.ColorRole.Window
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

    def test_nothing_is_drawn_until_the_host_is_entered(self, qapp):
        host, toggle = self._host_and_toggle()
        assert not toggle.is_revealed()
        assert not self._draws_something(toggle)
        host.close()

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

    def test_a_toggle_with_no_host_is_always_drawn(self, qapp):
        toggle = DeviceViewToggle(frame_shown=True)
        assert toggle.is_revealed()
        assert self._draws_something(toggle)
