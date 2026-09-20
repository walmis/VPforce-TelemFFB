"""CornerLogo (telemffb/ui/widgets/CornerLogo.py) - the app logo floating
over the window's top-right corner.

It takes part in no layout, so what matters is where it puts itself: in the
corner, no lower than the widget it is told to stay clear of, scaled to the
band above that widget, and out of the way when the menus need the room or
the image will not load.

``G.vpf_logo`` is a Qt resource path registered by main.py's ``import
resources`` (not imported by the suite), so a real temp PNG stands in.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QGroupBox, QMainWindow, QVBoxLayout, QWidget

from telemffb.ui.widgets.CornerLogo import (BOTTOM_GAP, MAX_HEIGHT, RIGHT_MARGIN,
                                            TOP_MARGIN, CornerLogo)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def logo_path(tmp_path, qapp):
    path = tmp_path / "logo.png"
    QPixmap(QSize(400, 100)).save(str(path))
    return str(path)


def _window(top_margin, width=900, menus=("System", "Profiles", "Help")):
    """A main window whose status box starts ``top_margin`` below the menu
    bar - the band the logo has to fit is that plus the bar."""
    win = QMainWindow()
    for name in menus:
        win.menuBar().addMenu(name)
    central = QWidget()
    layout = QVBoxLayout(central)
    layout.setContentsMargins(9, top_margin, 9, 9)
    box = QGroupBox("Application Status")
    box.setMinimumHeight(120)
    layout.addWidget(box)
    layout.addStretch()
    win.setCentralWidget(central)
    win.resize(width, 400)
    return win, box


def _shown(win):
    win.show()
    QApplication.processEvents()
    QApplication.processEvents()


class TestPlacement:
    def test_sits_in_the_top_right_corner(self, logo_path):
        win, box = _window(top_margin=30)
        logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path)
        _shown(win)
        assert logo.isVisible()
        assert logo.y() == TOP_MARGIN
        assert logo.x() + logo.width() == win.width() - RIGHT_MARGIN
        win.close()

    def test_stays_above_the_widget_below_it(self, logo_path):
        win, box = _window(top_margin=30)
        logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path)
        _shown(win)
        box_top = box.mapTo(win, QPoint(0, 0)).y()
        assert logo.y() + logo.height() == box_top - BOTTOM_GAP
        win.close()

    def test_moves_nothing(self, logo_path):
        """It is in no layout: the status box is where it would be with no
        logo at all."""
        bare, bare_box = _window(top_margin=30)
        _shown(bare)
        win, box = _window(top_margin=30)
        CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path)
        _shown(win)
        assert box.mapTo(win, QPoint(0, 0)) == bare_box.mapTo(bare, QPoint(0, 0))
        bare.close()
        win.close()

    def test_follows_the_corner_as_the_window_widens(self, logo_path):
        win, box = _window(top_margin=30)
        logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path)
        _shown(win)
        win.resize(1300, 400)
        QApplication.processEvents()
        assert logo.x() + logo.width() == win.width() - RIGHT_MARGIN
        win.close()


class TestSizing:
    def test_a_taller_band_gets_a_bigger_logo_of_the_same_shape(self, logo_path):
        small_win, small_box = _window(top_margin=10)
        small = CornerLogo(small_win, below=small_box, menubar=small_win.menuBar(), logo_path=logo_path)
        big_win, big_box = _window(top_margin=30)
        big = CornerLogo(big_win, below=big_box, menubar=big_win.menuBar(), logo_path=logo_path)
        _shown(small_win)
        _shown(big_win)
        assert big.height() == small.height() + 20
        assert big.width() == big.height() * 4
        small_win.close()
        big_win.close()

    def test_height_is_capped(self, logo_path):
        win, box = _window(top_margin=300)
        logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path)
        _shown(win)
        assert logo.height() == MAX_HEIGHT
        win.close()


class TestGettingOutOfTheWay:
    def test_hidden_when_it_would_cover_the_menus(self, logo_path):
        win, box = _window(top_margin=30, width=900,
                           menus=["A fairly long menu name %d" % i for i in range(6)])
        logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path)
        _shown(win)
        assert not logo.isVisible()
        win.close()

    def test_a_resource_that_will_not_load_is_skipped(self, qapp):
        win, box = _window(top_margin=30)
        logo = CornerLogo(win, below=box, menubar=win.menuBar(),
                          logo_path=':/nonexistent/resource.png')
        _shown(win)  # must not raise
        assert not logo.isVisible()
        win.close()

    def test_clicks_pass_through(self, logo_path):
        from PyQt6.QtCore import Qt
        win, box = _window(top_margin=30)
        logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path)
        assert logo.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        win.close()
