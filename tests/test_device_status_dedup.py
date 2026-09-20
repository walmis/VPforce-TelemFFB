"""Children report their status on a timer, and nearly every report repeats
the one before. Acting on those re-tinted the full-size icon and, through
``DeviceIconPanel.changed``, reloaded, re-scaled and re-tinted every icon of
every compact row - with several rows and several children, enough to stall
the main thread for seconds. These pin the two places that stops: the panel
does not announce a status that has not changed, and a compact chip does no
work for a value it already has.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from telemffb.ui.panels.DevicePanel import DeviceIconPanel, MiniDevicePanel

pytestmark = pytest.mark.unit

DEVICES = ['joystick', 'pedals']


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    p = DeviceIconPanel()
    p.set_devices(DEVICES)
    return p


@pytest.fixture
def announced(panel):
    seen = []
    panel.changed.connect(lambda: seen.append(True))
    return seen


class TestThePanelOnlyAnnouncesNews:
    def test_a_repeated_status_is_not(self, panel, announced):
        panel.set_device_status('pedals', 'ACTIVE')
        for _ in range(50):
            panel.set_device_status('pedals', 'ACTIVE')
        assert announced == [True]

    def test_a_change_is_announced_again(self, panel, announced):
        panel.set_device_status('pedals', 'ACTIVE')
        panel.set_device_status('pedals', 'DISCONNECTED')
        panel.set_device_status('pedals', 'ACTIVE')
        assert announced == [True, True, True]

    def test_a_status_repeated_after_the_device_was_reconfigured_is_news(self, panel, announced):
        """Its icon was re-colored in between (ghost and back)."""
        panel.set_device_status('pedals', 'ACTIVE')
        panel.set_device_configured('pedals', False)
        panel.set_device_configured('pedals', True)
        del announced[:]
        panel.set_device_status('pedals', 'ACTIVE')
        assert announced == [True]

    def test_a_status_repeated_after_the_devices_were_rebuilt_is_news(self, panel, announced):
        panel.set_device_status('pedals', 'ACTIVE')
        panel.set_devices(DEVICES)
        del announced[:]
        panel.set_device_status('pedals', 'ACTIVE')
        assert announced == [True]


class TestAChipDoesNoWorkForAValueItHas:
    """The shipped icons are Qt resources, registered by main.py's ``import
    resources`` and not by the suite, so real temp images stand in."""

    @pytest.fixture
    def icons(self, tmp_path, qapp):
        paths = []
        for name in ("one.png", "two.png"):
            path = tmp_path / name
            QPixmap(QSize(72, 72)).save(str(path))
            paths.append(str(path))
        return paths

    @pytest.fixture
    def chip(self, icons):
        row = MiniDevicePanel()
        row.set_devices(DEVICES)
        row.chips['pedals'].set_icon(icons[0])
        yield row.chips['pedals']  # yield, not return: the row owns the chip and has to outlive the test
        row.deleteLater()

    def test_the_same_icon_path_is_not_loaded_again(self, chip, icons):
        loaded = chip._original_pixmap
        assert loaded is not None
        chip.set_icon(icons[0])
        assert chip._original_pixmap is loaded

    def test_the_same_status_color_is_not_painted_again(self, chip):
        chip.set_status_color('ok')
        painted = chip.icon_label.pixmap().cacheKey()
        chip.set_status_color('ok')
        assert chip.icon_label.pixmap().cacheKey() == painted
