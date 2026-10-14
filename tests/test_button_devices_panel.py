"""The Button Devices group in System Settings: its rows follow the
manager's device list, the Enabled column turns a device on or off through
the manager, and pressed buttons follow the manager's signal.  Runs on a
fake manager; no hidapi or hardware is touched.
"""
import os
import re
import sys
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.modules.setdefault('telemffb.hw.hid', MagicMock())

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

import telemffb.globals as G
from telemffb.hw.button_devices import ButtonDeviceInfo
from telemffb.ui.panels.ButtonDevicesPanel import ButtonDevicesPanel

pytestmark = [pytest.mark.unit]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeManager(QObject):
    buttons_changed = pyqtSignal(str, object)
    devices_changed = pyqtSignal()

    def __init__(self, listed):
        super().__init__()
        self.listed = listed
        self.ignored_calls = []

    def devices(self):
        return list(self.listed)

    def set_ignored(self, key, ignored):
        self.ignored_calls.append((key, ignored))


def device(key, name='', ignored=False):
    vid, pid = (int(part, 16) for part in key.partition('#')[0].split(':'))
    return ButtonDeviceInfo(key=key, path=key.encode(), name=name, vid=vid, pid=pid,
                            ignored=ignored)


@pytest.fixture
def panel(qapp, monkeypatch):
    monkeypatch.setattr(G, 'button_states', {}, raising=False)
    manager = FakeManager([device('1234:0001', 'Box'), device('1234:0002', 'Throttle')])
    widget = ButtonDevicesPanel(manager=manager)
    yield widget, manager
    widget.deleteLater()
    qapp.processEvents()


def row_keys(widget):
    table = widget.table
    return [table.item(row, widget.COL_ID).text() for row in range(table.rowCount())]


def test_rows_follow_the_manager_device_list(qapp, panel):
    widget, manager = panel
    assert row_keys(widget) == ['1234:0001', '1234:0002']

    manager.listed = [device('1234:0002', 'Throttle'), device('5678:0001', 'Panel')]
    manager.devices_changed.emit()
    qapp.processEvents()

    assert row_keys(widget) == ['1234:0002', '5678:0001']


def test_enabled_toggle_turns_the_device_off_and_on(panel):
    widget, manager = panel
    enabled = widget.table.item(1, widget.COL_ENABLED)

    enabled.setCheckState(Qt.CheckState.Unchecked)
    enabled.setCheckState(Qt.CheckState.Checked)

    assert manager.ignored_calls == [('1234:0002', True), ('1234:0002', False)]


def test_buttons_changed_updates_the_pressed_cell(panel):
    widget, manager = panel

    manager.buttons_changed.emit('1234:0002', [3, 7])

    pressed = [widget.table.item(row, widget.COL_PRESSED).text() for row in range(2)]
    assert [[int(n) for n in re.findall(r'[0-9]+', text)] for text in pressed] == [[], [3, 7]]
