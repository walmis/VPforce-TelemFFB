"""CornerDeviceSlot (telemffb/ui/widgets/CornerDeviceSlot.py) - the slot in
the band across the top of the window, beside the logo: where the device
strip goes in the "menu bar" view.

Where it sits is a matter of looking at it. What is pinned here is when
it shows and who gives way: in a window too narrow for the menus, the row
and the logo, the logo goes and the row takes the corner; only if it still
would not clear the menus does the row itself go.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QApplication, QGroupBox, QMainWindow, QVBoxLayout,
                             QWidget)

from telemffb.ui.widgets.CornerDeviceSlot import CornerDeviceSlot
from telemffb.ui.widgets.CornerLogo import CornerLogo
from telemffb.ui.widgets.DeviceStrip import DeviceStrip

pytestmark = pytest.mark.unit

DEVICES = ['joystick', 'pedals', 'collective', 'trimwheel']


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def logo_path(tmp_path, qapp):
    path = tmp_path / "logo.png"
    QPixmap(QSize(400, 100)).save(str(path))
    return str(path)


def _window(width, logo_path=None, menus=("System", "Help")):
    win = QMainWindow()
    for name in menus:
        win.menuBar().addMenu(name)
    central = QWidget()
    layout = QVBoxLayout(central)
    layout.setContentsMargins(9, 40, 9, 9)  # a band tall enough for the row, as the app's is
    box = QGroupBox("Application Status")
    box.setMinimumHeight(120)
    layout.addWidget(box)
    layout.addStretch()
    win.setCentralWidget(central)
    win.resize(width, 400)
    logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path) if logo_path else None
    slot = CornerDeviceSlot(win, below=box, menubar=win.menuBar(), logo=logo)
    strip = DeviceStrip()
    strip.device_mini_panel.set_devices(DEVICES)
    win.strip = strip  # kept alive with the window it is for
    return win, box, logo, slot


def _shown(win):
    win.show()
    QApplication.processEvents()
    QApplication.processEvents()


class TestWhenItShows:
    def test_holding_the_strip_it_shows_and_releasing_it_goes(self, qapp):
        win, box, logo, row = _window(1200)
        _shown(win)
        row.take(win.strip)
        assert row.isVisible() and win.strip.isVisible()
        row.release()
        assert not row.isVisible()
        win.close()


class TestGivingWay:
    def _width_needing(self, win, row, logo_width):
        """The window width at which the row and a logo taking ``logo_width``
        (margin included) only just both fit."""
        menubar = win.menuBar()
        menus_end = menubar.actionGeometry(menubar.actions()[-1]).right()
        return menus_end + 12 + row.width() + 10 + logo_width

    def _squeezed(self, logo_path, width_for):
        win, box, logo, row = _window(1200, logo_path)
        _shown(win)
        row.take(win.strip)
        win.resize(width_for(win, row, logo), 400)
        QApplication.processEvents()
        return win, logo, row

    def test_the_logo_shrinks_first(self, logo_path):
        """Too narrow for both at full size: the logo is decoration, the row
        is not, so it is the logo that makes room - and stays beside it."""
        win, logo, row = self._squeezed(
            logo_path, lambda win, row, logo: self._width_needing(win, row, logo.natural_reserved_width()) - 20)
        assert row.isVisible() and logo.isVisible()
        assert logo.reserved_width() < logo.natural_reserved_width()
        assert row.x() + row.width() < logo.x()
        win.close()

    def test_then_it_goes_and_the_row_takes_the_corner(self, logo_path):
        """Too narrow even for the logo at its smallest."""
        win, logo, row = self._squeezed(
            logo_path, lambda win, row, logo: self._width_needing(win, row, logo.min_reserved_width()) - 5)
        assert row.isVisible()
        assert not logo.isVisible()
        assert 0 < win.width() - (row.x() + row.width()) <= 12
        win.close()

    def test_the_logo_comes_back_at_full_size_when_there_is_room(self, logo_path):
        win, logo, row = self._squeezed(
            logo_path, lambda win, row, logo: self._width_needing(win, row, logo.min_reserved_width()) - 5)
        win.resize(1200, 400)
        QApplication.processEvents()
        assert logo.isVisible() and row.isVisible()
        assert logo.reserved_width() == logo.natural_reserved_width()
        win.close()

    def test_the_logo_comes_back_when_the_slot_lets_the_strip_go(self, logo_path):
        win, logo, row = self._squeezed(
            logo_path, lambda win, row, logo: self._width_needing(win, row, logo.min_reserved_width()) - 5)
        row.release()
        QApplication.processEvents()
        assert logo.isVisible()
        assert logo.reserved_width() == logo.natural_reserved_width()
        win.close()

    def test_the_row_goes_rather_than_cover_the_menus(self, logo_path):
        win, box, logo, row = _window(1200, logo_path,
                                      menus=["A fairly long menu name %d" % i for i in range(8)])
        _shown(win)
        row.take(win.strip)
        assert not row.isVisible()
        win.close()
