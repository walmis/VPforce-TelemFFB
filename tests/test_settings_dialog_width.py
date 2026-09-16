"""The dialog must not open wider than its contents need.

Blocks that sit above one another should cost the width of the widest one,
not the sum.  QGridLayout gets this wrong when two items span overlapping
but unequal column ranges - which is how the Startup Behavior page came to
demand ~1150px and refuse to shrink below it, with the slack showing up as
dead space to the right of everything on every tab.

These are relationships rather than pixel counts, so they hold whatever the
font and DPI happen to be.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G

pytestmark = [pytest.mark.unit]

#: Room for the page's own margins and a scrollbar; anything beyond this
#: means a block is being counted twice rather than stacked.
MARGIN = 120


class FakeSettings(dict):
    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def dialog(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = FakeSettings({'devpath_joystick': 'j', 'devpath_pedals': 'p',
                             'devpath_collective': 'c', 'devpath_trimwheel': ''})
    monkeypatch.setattr(G, 'system_settings', settings, raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None), ('is_exe', False)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.SystemSettingsDialog import SystemSettingsDialog
    dlg = SystemSettingsDialog()
    dlg.ensurePolished()
    yield dlg
    dlg.deleteLater()
    app.processEvents()


def _blocks(page):
    """The page's top-level blocks - what it stacks vertically."""
    layout = page.layout()
    out = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        width = item.minimumSize().width()
        if width > 0:
            out.append(width)
    return out


class TestNoStackedWidth:
    @pytest.mark.parametrize("tab", ["Devices", "System"])
    def test_a_page_is_as_wide_as_its_widest_block(self, dialog, tab):
        index = next(i for i in range(dialog.tabWidget.count())
                     if dialog.tabWidget.tabText(i) == tab)
        page = dialog.tabWidget.widget(index)
        blocks = _blocks(page)
        assert blocks, f"{tab} has no measurable blocks"
        assert page.minimumSizeHint().width() <= max(blocks) + MARGIN, (
            f"{tab} demands {page.minimumSizeHint().width()}px but its widest "
            f"block only needs {max(blocks)}px - blocks are being summed")

    def test_the_dialog_is_as_wide_as_its_widest_page(self, dialog):
        pages = [dialog.tabWidget.widget(i).minimumSizeHint().width()
                 for i in range(dialog.tabWidget.count())]
        assert dialog.minimumSizeHint().width() <= max(pages) + MARGIN


class TestChildSettingsButtonIsGone:
    def test_no_button_opens_the_child_settings_pages(self, dialog):
        """Every device is configurable from here, so there is nothing left
        for the child settings pages to offer."""
        assert not hasattr(dialog, 'buttonChildSettings')
        assert not hasattr(dialog, 'launch_child_settings_windows')


class TestGrowToFit:
    """The .ui's opening size is a preference: when the content needs a
    taller window (real fonts, all cards expanded), the dialog grows to
    fit rather than letting the layout crush the tallest card - which
    painted the joystick selector clipped until the user dragged the
    frame and the window system enforced the real minimum."""

    def test_a_too_short_window_grows_to_its_content(self, dialog):
        need = dialog.minimumSizeHint().height()
        dialog.resize(dialog.width(), max(200, need - 150))
        dialog._grow_to_fit()
        cap = dialog.screen().availableGeometry().height()
        assert dialog.height() >= min(need, cap)

    def test_a_window_already_tall_enough_is_not_touched(self, dialog):
        need = dialog.minimumSizeHint()
        avail = dialog.screen().availableGeometry()
        width = min(need.width() + 50, avail.width())
        height = min(need.height() + 120, avail.height())
        dialog.resize(width, height)
        dialog._grow_to_fit()
        assert dialog.height() == height     # growth only, never shrink
        assert dialog.width() == width

    def test_the_growth_never_exceeds_the_screen(self, dialog):
        dialog.resize(dialog.width(), 200)
        dialog._grow_to_fit()
        avail = dialog.screen().availableGeometry()
        assert dialog.height() <= avail.height()
        assert dialog.width() <= avail.width()
