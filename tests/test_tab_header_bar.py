"""TabHeaderBar (telemffb/ui/widgets/TabHeaderBar.py) - a tab page's header
row: the page's own controls packed from the left, a slot for the device
strip at the right-hand end.

Where things sit in it is a matter of looking at it. What is pinned here is
that each bar has a slot of its own - the Monitor page can be detached, and
would take a shared one with it.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from telemffb.ui.widgets.DeviceStrip import DeviceSlot
from telemffb.ui.widgets.TabHeaderBar import TabHeaderBar

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestConstruction:
    def test_owns_its_own_device_slot(self, qapp):
        """Not a shared one: the Monitor page can be detached into a window
        of its own and would take a shared slot with it."""
        one, two = TabHeaderBar(), TabHeaderBar()
        assert isinstance(one.device_slot, DeviceSlot)
        assert one.device_slot is not two.device_slot

    def test_an_empty_slot_takes_no_room(self, qapp):
        """The bar is only as tall as the page's own controls until the
        strip is in it."""
        assert TabHeaderBar().device_slot.isHidden()
