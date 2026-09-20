"""CornerLogo (telemffb/ui/widgets/CornerLogo.py) - the app logo floating
over the window's top-right corner.

Where it sits and how big it is are a matter of looking at it. What is
pinned here is when it gets out of the way: for the menus, for an image
that will not load, and for the device row when told to.

``G.vpf_logo`` is a Qt resource path registered by main.py's ``import
resources`` (not imported by the suite), so a real temp PNG stands in.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QGroupBox, QMainWindow, QVBoxLayout, QWidget

from telemffb.ui.widgets.CornerLogo import CornerLogo

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

    def test_it_can_be_told_to_give_up_the_corner(self, logo_path):
        """For the device row, in a window too narrow for both."""
        win, box = _window(top_margin=30)
        logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=logo_path)
        _shown(win)
        natural = logo.natural_reserved_width()
        logo.set_suppressed(True)
        assert not logo.isVisible()
        assert logo.reserved_width() == 0
        assert logo.natural_reserved_width() == natural > 0  # what it would take, were it let back
        logo.set_suppressed(False)
        assert logo.isVisible()
        win.close()

    def test_a_logo_that_cannot_load_reserves_no_width(self, qapp):
        win, box = _window(top_margin=30)
        logo = CornerLogo(win, below=box, menubar=win.menuBar(), logo_path=':/nonexistent/resource.png')
        _shown(win)
        assert logo.natural_reserved_width() == 0
        win.close()
