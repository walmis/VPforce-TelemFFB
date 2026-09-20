"""MainMenu._add_logo (telemffb/ui/menus.py) - the app logo as the menu
bar's top-right corner widget.

The logo used to be a 200px QLabel in a row of its own above the
Application Status box. In the menu bar it sets the bar's height, so it is
scaled to a fixed height, and a resource that will not load has to leave
the bar alone rather than claim the corner with a null pixmap.

``G.vpf_logo`` is a Qt resource path registered by main.py's ``import
resources`` (Windows-only, not imported by the suite), so it is pointed at
a real temp PNG here.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QMenuBar

import telemffb.globals as G
from telemffb.ui.menus import LOGO_HEIGHT, MainMenu

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake_logo(tmp_path, monkeypatch, qapp):
    path = tmp_path / "logo.png"
    QPixmap(QSize(200, 50)).save(str(path))
    monkeypatch.setattr(G, 'vpf_logo', str(path), raising=False)
    return path


class TestLogoCornerWidget:
    def test_the_logo_lands_in_the_top_right_corner(self, fake_logo, qapp):
        menubar = QMenuBar()
        MainMenu(None)._add_logo(menubar)
        assert menubar.cornerWidget(Qt.Corner.TopRightCorner) is not None

    def test_it_is_scaled_to_the_bar_height_keeping_its_shape(self, fake_logo, qapp):
        menubar = QMenuBar()
        menu = MainMenu(None)
        menu._add_logo(menubar)
        size = menu.logo_label.pixmap().deviceIndependentSize()
        assert round(size.height()) == LOGO_HEIGHT
        assert round(size.width()) == LOGO_HEIGHT * 200 // 50

    def test_a_resource_that_will_not_load_is_skipped(self, monkeypatch, qapp):
        """A null pixmap has width 0 - scaling to the bar height used to
        divide by it, and a corner widget holding one would claim the
        space for nothing."""
        monkeypatch.setattr(G, 'vpf_logo', ':/nonexistent/resource.png', raising=False)
        menubar = QMenuBar()
        MainMenu(None)._add_logo(menubar)  # must not raise
        assert menubar.cornerWidget(Qt.Corner.TopRightCorner) is None
