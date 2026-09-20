"""TabHeaderBar (telemffb/ui/widgets/TabHeaderBar.py) - the header row a
tab page puts its own controls in, with the compact device row centered
in the middle.

The point of the widget is that the Monitor page (toolbar and filter on
the left, effects label on the right) and the Settings page (nothing at
all) put their device row in the same place, so that is what these cover,
along with the side groups keeping their own width as the page widens.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (QApplication, QLabel, QLineEdit, QPushButton,
                             QVBoxLayout, QWidget)

from telemffb.ui.panels.DevicePanel import MiniDevicePanel
from telemffb.ui.widgets.TabHeaderBar import TabHeaderBar

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _host(bar, width=1200):
    """A laid-out parent, so the bar's children have real geometry."""
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.addWidget(bar)
    layout.addStretch()
    host.resize(width, 300)
    host.show()
    QApplication.processEvents()
    return host


def _populated(bar):
    bar.device_mini_panel.set_devices(['joystick', 'pedals', 'collective', 'trimwheel'])
    bar.device_mini_panel.show()
    return bar


def _center_in_bar(bar):
    mini = bar.device_mini_panel
    return mini.mapTo(bar, mini.rect().center()).x()


class TestConstruction:
    def test_owns_its_own_device_row(self, qapp):
        """Not a shared instance: the Monitor page can be detached into a
        window of its own and would take a shared row with it."""
        one, two = TabHeaderBar(), TabHeaderBar()
        assert isinstance(one.device_mini_panel, MiniDevicePanel)
        assert one.device_mini_panel is not two.device_mini_panel

    def test_device_clicks_are_re_emitted(self, qapp):
        bar = _populated(TabHeaderBar())
        seen = []
        bar.DeviceClicked.connect(seen.append)
        bar.device_mini_panel.DeviceClicked.emit('pedals')
        assert seen == ['pedals']


class TestSideGroups:
    def test_left_content_keeps_its_own_width(self, qapp):
        """The widgets pack against the left edge instead of growing to
        fill the column, so they stay together as the window widens."""
        bar = _populated(TabHeaderBar())
        left = QPushButton('Detach')
        right_of_it = QLabel('Telemetry:')
        bar.add_left(left)
        bar.add_left(right_of_it)
        host = _host(bar, width=1600)

        gap = right_of_it.mapTo(bar, right_of_it.rect().topLeft()).x() - (
            left.mapTo(bar, left.rect().topLeft()).x() + left.width())
        assert 0 <= gap <= 20
        host.close()

    def test_left_content_stays_put_as_the_bar_widens(self, qapp):
        bar = _populated(TabHeaderBar())
        button = QPushButton('Detach')
        bar.add_left(button)
        host = _host(bar, width=800)
        narrow = button.width()
        host.resize(1600, 300)
        QApplication.processEvents()
        assert button.width() == narrow
        host.close()

    def test_add_left_keeps_insertion_order(self, qapp):
        bar = _populated(TabHeaderBar())
        first, second = QPushButton('one'), QPushButton('two')
        bar.add_left(first)
        bar.add_left(second)
        host = _host(bar)
        assert (first.mapTo(bar, first.rect().topLeft()).x()
                < second.mapTo(bar, second.rect().topLeft()).x())
        host.close()

    def test_right_content_ends_at_the_right_edge(self, qapp):
        bar = _populated(TabHeaderBar())
        label = QLabel('Active Effects for: Joystick')
        bar.add_right(label)
        host = _host(bar)
        right_edge = label.mapTo(bar, label.rect().topLeft()).x() + label.width()
        assert abs(bar.width() - right_edge) <= 2
        host.close()


class TestDeviceRowPlacement:
    def test_the_row_is_centered_on_an_empty_bar(self, qapp):
        bar = _populated(TabHeaderBar())
        host = _host(bar)
        assert abs(_center_in_bar(bar) - bar.width() // 2) <= 2
        host.close()

    def test_a_page_with_side_content_agrees_with_one_without(self, qapp):
        """The Monitor and Settings pages are the same width, so their
        device rows land at the same x whenever the side content fits."""
        monitor = _populated(TabHeaderBar())
        monitor.add_left(QPushButton('Detach'))
        monitor.add_left(QLabel('Telemetry:'))
        filter_box = QLineEdit()
        filter_box.setMaximumWidth(100)
        monitor.add_left(filter_box)
        monitor.add_right(QLabel('Active Effects for: Joystick'))
        settings = _populated(TabHeaderBar())

        monitor_host, settings_host = _host(monitor), _host(settings)
        assert monitor.width() == settings.width()
        assert abs(_center_in_bar(monitor) - _center_in_bar(settings)) <= 2
        monitor_host.close()
        settings_host.close()


class TestPageFit:
    """Both pages are laid out differently - the Monitor page has a layout
    margin of its own, the settings page's form fills it edge to edge - so
    the bar is told what to match rather than assuming."""

    def test_match_page_background_paints_the_pages_own_color(self, qapp):
        """Window is what the settings form is painted in; the tab pane
        the bar sits on is lighter."""
        bar = TabHeaderBar()
        bar.match_page_background()
        assert bar.autoFillBackground()
        assert bar.backgroundRole() == QPalette.ColorRole.Window

    def test_a_bar_paints_nothing_of_its_own(self, qapp):
        """Only a page that needs the match asks for it."""
        assert not TabHeaderBar().autoFillBackground()

    def test_set_page_inset_holds_the_row_below_the_top(self, qapp):
        bar = _populated(TabHeaderBar())
        bar.set_page_inset(9)
        host = _host(bar)
        mini = bar.device_mini_panel
        assert mini.mapTo(bar, mini.rect().topLeft()).y() == 9
        host.close()

    def test_the_same_inset_puts_both_pages_rows_at_one_height(self, qapp):
        """A page that insets its bar through its own layout margin (the
        Monitor page), and a page whose content runs edge to edge and
        insets the bar itself (the settings page), agree - which is what
        stops the row jumping as you switch tabs."""
        inset = 9

        with_margin = _populated(TabHeaderBar())
        monitor_page = QWidget()
        monitor_layout = QVBoxLayout(monitor_page)
        monitor_layout.setContentsMargins(inset, inset, inset, inset)
        monitor_layout.addWidget(with_margin)
        monitor_layout.addStretch()

        with_inset = _populated(TabHeaderBar())
        with_inset.set_page_inset(inset)
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.addWidget(with_inset)
        settings_layout.addStretch()

        for page in (monitor_page, settings_page):
            page.resize(1200, 300)
            page.show()
        QApplication.processEvents()

        def row_top(bar, page):
            row = bar.device_mini_panel
            return row.mapTo(page, row.rect().topLeft()).y()

        assert row_top(with_margin, monitor_page) == inset
        assert row_top(with_margin, monitor_page) == row_top(with_inset, settings_page)
        monitor_page.close()
        settings_page.close()
