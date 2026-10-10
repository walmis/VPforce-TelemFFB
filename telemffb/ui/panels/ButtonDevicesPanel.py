#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""The generic game controllers that supply buttons, as a System Settings
group.

On the master the rows mirror the :class:`ButtonDeviceManager`'s list and
the Enabled column turns a controller on or off at once through
:meth:`ButtonDeviceManager.set_ignored`, which persists the choice itself;
the dialog's Save never touches it.  Pressed buttons follow the manager's
``buttons_changed`` signal.

A child runs no manager, so it lists the controllers itself, read-only,
and polls ``G.button_states`` (filled from the master's relay) while the
group is visible.
"""

import logging
from typing import Callable, Iterable, Optional

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QGroupBox, QHeaderView, QLabel,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

import telemffb.globals as G
from telemffb.hw.button_devices import ButtonDeviceInfo, enumerate_button_devices

#: Child poll interval for the pressed buttons.
_POLL_INTERVAL_MS = 200
#: Rows the table always has room for; it grows with the page beyond that.
_MIN_ROWS = 4


class ButtonDevicesPanel(QGroupBox):
    """The "Button Devices" group: one row per generic controller.

    :param manager: the master's ``ButtonDeviceManager`` (or an object with
        its ``buttons_changed``/``devices_changed`` signals, ``devices()``
        and ``set_ignored()``); ``G.button_devices`` when None.  Without
        one the group is read-only and lists the controllers itself.
    :param enumerate_devices: ``callable() -> list[ButtonDeviceInfo]`` for
        the read-only list; :func:`enumerate_button_devices` when None.
    :param parent: the parent widget.
    """

    COL_ENABLED, COL_DEVICE, COL_ID, COL_STATUS, COL_PRESSED = range(5)
    _HEADERS = ('Enabled', 'Device', 'ID', 'Status', 'Pressed')

    def __init__(self, manager: Optional[QObject] = None,
                 enumerate_devices: Optional[Callable[[], list]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__('Button Devices', parent)
        if manager is None:
            manager = getattr(G, 'button_devices', None)
        self._manager = manager
        self._enumerate = enumerate_devices or enumerate_button_devices
        self._rows: dict[str, int] = {}
        self._populating = False

        layout = QVBoxLayout(self)
        intro = QLabel('Any game controller listed here can supply buttons to the '
                       'button settings. Click a button setting, then push the button '
                       'on the controller. Enabled applies at once, without Save.')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setEnabled(False)         # the muted look of secondary text
        layout.addWidget(self.note)

        self.table = QTableWidget(0, len(self._HEADERS))
        self.table.setHorizontalHeaderLabels(self._HEADERS)
        self.table.horizontalHeaderItem(self.COL_ENABLED).setToolTip(
            'Read buttons from this controller. Applies at once.')
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        for column, mode in ((self.COL_ENABLED, QHeaderView.ResizeMode.ResizeToContents),
                             (self.COL_DEVICE, QHeaderView.ResizeMode.Stretch),
                             (self.COL_ID, QHeaderView.ResizeMode.ResizeToContents),
                             (self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents),
                             (self.COL_PRESSED, QHeaderView.ResizeMode.Stretch)):
            header.setSectionResizeMode(column, mode)
        height = (header.sizeHint().height()
                  + self.table.verticalHeader().defaultSectionSize() * _MIN_ROWS
                  + 2 * self.table.frameWidth())
        self.table.setMinimumHeight(height)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        self.empty = QLabel('No game controllers found.')
        self.empty.setEnabled(False)
        layout.addWidget(self.empty)

        # A toggle makes the manager emit devices_changed from inside the
        # itemChanged handler; rebuilding the rows there would delete the
        # item being handled, so rebuilds run from the event loop.
        self._rebuild = QTimer(self)
        self._rebuild.setSingleShot(True)
        self._rebuild.setInterval(0)
        self._rebuild.timeout.connect(self.refresh)

        self._poll = QTimer(self)
        self._poll.setInterval(_POLL_INTERVAL_MS)
        self._poll.timeout.connect(self._poll_states)

        if self._manager is not None:
            self._manager.devices_changed.connect(self._rebuild.start)
            self._manager.buttons_changed.connect(self._on_buttons_changed)
            self.refresh()
        else:
            self.note.setText('The master instance manages these devices.'
                              if G.child_instance else
                              'Button devices are unavailable in this session. '
                              'See the log for details.')
            self._set_rows([])
        self.note.setVisible(self.read_only)

    @property
    def read_only(self) -> bool:
        """True when no manager runs here to apply an Enabled change."""
        return self._manager is None

    def refresh(self) -> None:
        """Rebuild the rows from the manager, or from a fresh listing."""
        if self._manager is not None:
            devices = self._manager.devices()
        else:
            try:
                devices = self._enumerate()
            except Exception:
                logging.exception('Listing button devices failed')
                devices = []
        self._set_rows(devices)

    def showEvent(self, event):
        super().showEvent(event)
        if self.read_only:
            self.refresh()
            self._poll.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._poll.stop()

    @staticmethod
    def _status(info: ButtonDeviceInfo) -> tuple[str, str]:
        """Status text and its tooltip."""
        if info.ignored:
            return 'off', 'Turned off; its buttons are not read.'
        if info.xinput:
            return 'not readable', 'Xbox-style (XInput) controllers send no button reports TelemFFB can read.'
        if not info.readable:
            return 'not readable', 'Opening or reading this controller failed. Replug it to try again.'
        return 'reading', ''

    @staticmethod
    def _pressed_text(buttons: Iterable[int]) -> str:
        return ', '.join(str(b) for b in sorted(buttons))

    @staticmethod
    def _text_item(text: str, tooltip: str = '') -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if tooltip:
            item.setToolTip(tooltip)
        return item

    def _set_rows(self, devices: list) -> None:
        states = getattr(G, 'button_states', None) or {}
        self._populating = True
        try:
            self.table.setRowCount(0)
            self._rows = {}
            self.table.setRowCount(len(devices))
            for row, info in enumerate(devices):
                self._rows[info.key] = row
                enabled = QTableWidgetItem()
                enabled.setData(Qt.ItemDataRole.UserRole, info.key)
                enabled.setCheckState(Qt.CheckState.Unchecked if info.ignored
                                      else Qt.CheckState.Checked)
                if self.read_only or info.xinput:
                    enabled.setFlags(Qt.ItemFlag.NoItemFlags)
                else:
                    enabled.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                self.table.setItem(row, self.COL_ENABLED, enabled)
                self.table.setItem(row, self.COL_DEVICE, self._text_item(info.name, info.name))
                self.table.setItem(row, self.COL_ID, self._text_item(info.key))
                self.table.setItem(row, self.COL_STATUS, self._text_item(*self._status(info)))
                self.table.setItem(row, self.COL_PRESSED,
                                   self._text_item(self._pressed_text(states.get(info.key, ()))))
        finally:
            self._populating = False
        has_rows = bool(devices)
        self.table.setVisible(has_rows)
        self.empty.setVisible(not has_rows)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._populating or self._manager is None or item.column() != self.COL_ENABLED:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        try:
            self._manager.set_ignored(key, not checked)
        except Exception:
            # a Qt slot: an escaping exception ends the process
            logging.exception(f'Turning button device {key} on or off failed')

    def _set_pressed(self, key: str, buttons: Iterable[int]) -> None:
        row = self._rows.get(key)
        if row is None:
            return
        item = self.table.item(row, self.COL_PRESSED)
        text = self._pressed_text(buttons)
        if item is not None and item.text() != text:
            item.setText(text)

    def _on_buttons_changed(self, key: str, buttons) -> None:
        self._set_pressed(key, buttons or ())

    def _poll_states(self) -> None:
        states = getattr(G, 'button_states', None) or {}
        for key in self._rows:
            self._set_pressed(key, states.get(key, ()))
