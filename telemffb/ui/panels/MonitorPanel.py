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

"""MonitorPanel: the "Monitor" tab's telemetry and active-effects display.

Replaces MainWindow's two hand-rolled ``QLabel``s (``lbl_telem_data``,
``lbl_effects_data``) sitting inside ``QScrollArea``s, each refreshed every
frame by concatenating one formatted line of text per row and calling
``setText()`` on the whole block. That scales badly with a QAbstractItemView
in mind - a table view backed by ``MonitorTableModel.KeyValueTableModel``
redraws only the rows that changed instead of re-laying-out one giant label
- and it is also how selecting a handful of telemetry lines and copying
them (``CopyableTableView``, ``telemffb.ui.widgets.custom_widgets``) keeps
working without free-text selection.

Two visual differences from the old label-based rendering, both judged the
closest faithful option for a table view:

- The "Waiting for data... / DCS: Enabled / ..." pre-telemetry message was
  free text, not key/value rows. It is kept as a plain ``QLabel`` shown in
  a ``QStackedWidget`` ahead of the telemetry table, swapped in by
  ``refresh_waiting_status()`` and swapped out the moment the first
  telemetry frame renders through ``update_telemetry()`` - exactly when the
  old label's text was first overwritten with real data.
- Copy is now cell/row based (``CopyableTableView``'s Ctrl+C) rather than
  drag-selecting arbitrary runs of free text out of a QLabel.

Ownership split follows ``OfflineEditorPanel``/``SettingsLayout``: this
panel owns the two tables, the filter box and the detach toolbar button;
what a detach *does* (reparenting the tab into its own window) stays
MainWindow's business, since it also owns the tab widget and the other
detachable tabs - the panel calls back through ``mainwindow.detach_tab(0)``,
matching the previous hardcoded "Monitor is always tab 0" assumption.

Building the row list itself (alphabetizing telemetry keys, moving certain
keys to the front, walking ``G.effects.dict`` to translate active-effect
names) stays out of this file where it was already entangled with logic
this refactor step is not touching (the per-frame ``NoWheelSlider`` handle
colors, which key off the very same ``active_settings`` list the effects
loop builds). MainWindow still does that part and calls
``update_telemetry(data)`` / ``update_effects(text)`` with the result.
"""

from typing import Dict, List, Optional, Tuple

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (QGridLayout, QLabel, QLineEdit, QSplitter,
                             QStackedWidget, QWidget)

import telemffb.globals as G
from telemffb.ui.panels.MonitorTableModel import KeyValueTableModel
from telemffb.ui.widgets.TabHeaderBar import TabHeaderBar
from telemffb.ui.widgets.custom_widgets import CopyableTableView

_MONOSPACE_STYLE = """
    padding: 2px;
    font-family: Cascadia Mono;
"""


class MonitorPanel(QWidget):
    """The Monitor tab: telemetry table + active-effects table, the filter
    box and the detach-to-window toolbar button."""

    def __init__(self, parent=None, mainwindow=None):
        super().__init__(parent)
        self.mainwindow = mainwindow
        self.show_simvars = False
        self._build_ui()
        self.refresh_waiting_status()

    # ---- construction ----------------------------------------------------

    def _build_ui(self):
        layout = QGridLayout(self)

        """ Detach toolbar - moves the whole Monitor tab into its own
        window. Always tab index 0: MainWindow's tab-context-menu and
        Ctrl+Shift+M shortcut make the same assumption. """
        self.detach_toolbar = QtWidgets.QToolBar(self)
        self.detach_toolbar.setObjectName("monitorInlineToolbar")
        self.detach_toolbar.setMovable(False)
        self.detach_toolbar.setFloatable(False)
        self.detach_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.detach_toolbar.setIconSize(QtCore.QSize(16, 16))
        self.detach_toolbar.setStyleSheet("QToolBar { border: 0; background: transparent; }")

        self.detach_action = self.detach_toolbar.addAction("Detach")
        self.detach_action.setToolTip('Detach the Monitor Tab from the main window\ninto a separate window.')
        self.detach_action.triggered.connect(self._on_detach_clicked)

        btn = self.detach_toolbar.widgetForAction(self.detach_action)
        if isinstance(btn, QtWidgets.QToolButton):
            btn.setAutoRaise(False)
            btn.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet("""
                QToolButton {
                    border: 1px solid palette(mid);
                    border-radius: 4px;
                    padding: 3px 9px;
                    background: palette(button);
                    color: palette(button-text);
                }
                QToolButton:hover { background: palette(midlight); }
                QToolButton:pressed {
                    background: palette(dark);
                    color: palette(highlight);
                }
                QToolButton:disabled { color: palette(mid); border-color: palette(mid); }
            """)

        """ Page header - this page's own controls either side of the
        compact device row, which sits in the same place here as it does
        on the Settings page. See telemffb/ui/widgets/TabHeaderBar.py. """
        self.header_bar = TabHeaderBar()

        self.telem_lbl = QLabel('Telemetry:')
        self.telem_filter = QLineEdit()
        self.telem_filter.setToolTip(
            "Comma Separated, Case Insensitive list of telemetry items to show (e.g. 'aoa, ias, rpm')")
        self.telem_filter.setPlaceholderText("Filter")
        self.telem_filter.setMaximumWidth(100)

        self.header_bar.add_left(self.detach_toolbar)
        self.header_bar.add_left(self.telem_lbl)
        self.header_bar.add_left(self.telem_filter)


        """ Telemetry pane: a plain "waiting for data" label shown until the
        first telemetry frame, then the live table. """
        self._telem_model = KeyValueTableModel(['Key', 'Value'], self)
        self.telem_view = CopyableTableView(self)
        self.telem_view.setModel(self._telem_model)
        self.telem_view.horizontalHeader().setStretchLastSection(True)
        self.telem_view.setStyleSheet(f"QTableView {{ {_MONOSPACE_STYLE} }}")

        self._telem_waiting_label = QLabel()
        self._telem_waiting_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._telem_waiting_label.setWordWrap(False)
        self._telem_waiting_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._telem_waiting_label.setStyleSheet(_MONOSPACE_STYLE)

        self._telem_stack = QStackedWidget()
        self._telem_stack.addWidget(self._telem_waiting_label)
        self._telem_stack.addWidget(self.telem_view)
        self._telem_stack.setMinimumHeight(100)

        """ Active-effects pane. """
        self._effects_model = KeyValueTableModel(['Active Effects'], self)
        self.effects_view = CopyableTableView(self)
        self.effects_view.setModel(self._effects_model)
        # Its one column's header is the pane's title, and says whose
        # effects these are (set_effects_scope_label) - level with the
        # telemetry table's own header beside it.
        self.effects_view.horizontalHeader().setStretchLastSection(True)
        self.effects_view.setStyleSheet(f"QTableView {{ {_MONOSPACE_STYLE} }}")
        self.effects_view.setMinimumHeight(100)

        layout.addWidget(self.header_bar, 0, 0, 1, 2)
        # Telemetry gets 70% of the width, which is what this page gave it
        # before its panes were tables: the split then fell out of the two
        # text labels' size hints, and measured 70/30 at every window
        # width. Two tables have the same size hint and would start level,
        # so the split is stated here. The stretch factors hold it as the
        # window is resized; the starting split is set on first show
        # (showEvent), once the splitter has a width to divide.
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._telem_stack)
        self._splitter.addWidget(self.effects_view)
        self._splitter.setStretchFactor(0, 7)
        self._splitter.setStretchFactor(1, 3)
        self._split_applied = False
        layout.addWidget(self._splitter, 1, 0, 1, 2)  # Span both columns

    def showEvent(self, event):
        super().showEvent(event)
        if not self._split_applied:
            self._split_applied = True
            width = self._splitter.width()
            self._splitter.setSizes([width * 7 // 10, width * 3 // 10])

    def _on_detach_clicked(self):
        if self.mainwindow is not None:
            self.mainwindow.detach_tab(0)

    # ---- detach toolbar visibility (MainWindow.detach_tab/reattach_tab) --

    def set_detach_toolbar_visible(self, visible: bool) -> None:
        self.detach_toolbar.setVisible(visible)

    # ---- active-effects header (master scope switch) ----------------------

    def set_effects_scope_label(self, device_type: Optional[str]) -> None:
        """``device_type`` is the config-scope device to name in the header
        (master only, scoped to something other than its own device type);
        ``None`` restores the plain "Active Effects" header."""
        if device_type:
            self._effects_model.set_header(0, f'Active Effects for: {device_type.title()}')
        else:
            self._effects_model.set_header(0, 'Active Effects')

    def effects_title(self) -> str:
        """The effects pane's header text."""
        return self._effects_model.headerData(0, Qt.Orientation.Horizontal)

    # ---- debug menu: "Show simvar in telem window" ------------------------

    def set_show_simvars(self, value: bool) -> None:
        self.show_simvars = bool(value)

    # ---- pre-telemetry status ----------------------------------------------

    def refresh_waiting_status(self) -> None:
        dcs_enabled = G.system_settings.get('enableDCS')
        il2_enabled = G.system_settings.get('enableIL2')
        msfs_enabled = G.system_settings.get('enableMSFS')
        xplane_enabled = G.system_settings.get('enableXPLANE')
        bms_enabled = G.system_settings.get('enableBMS')

        def status(enabled):
            return "Enabled" if enabled else "Disabled"

        self._telem_waiting_label.setText(
            f"Waiting for data...\n\n"
            f"DCS     : {status(dcs_enabled)}\n"
            f"IL2     : {status(il2_enabled)}\n"
            f"MSFS    : {status(msfs_enabled)}\n"
            f"X-Plane : {status(xplane_enabled)}\n"
            f"BMS     : {status(bms_enabled)}\n\n"
            "Enable or Disable in System -> System Settings"
        )
        self._telem_stack.setCurrentWidget(self._telem_waiting_label)

    # ---- per-frame updates --------------------------------------------------

    def update_telemetry(self, data: Dict) -> None:
        """``data`` is the (already alphabetized/reordered) telemetry dict
        for this frame. Builds one table row per key, applying the same
        filter and MSFS-simvar-name debug substitution
        ``get_telem_items`` used to."""
        rows = self._build_telemetry_rows(data)
        self._telem_model.set_rows(rows)
        self._telem_stack.setCurrentWidget(self.telem_view)

    def _build_telemetry_rows(self, data: Dict) -> List[Tuple[str, Tuple[str, str]]]:
        raw = (self.telem_filter.text() or "")
        tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
        rows: List[Tuple[str, Tuple[str, str]]] = []
        for key, v in data.items():
            display_key = key
            # check for msfs and debug mode (alt-d pressed), change to simvar name
            if self.show_simvars:
                if data.get("src") == "MSFS":
                    s = G.telem_manager.simconnect.get_var_name(display_key)
                    if s is not None:
                        display_key = s

            # Apply simple OR filtering against the (possibly renamed) key only
            if tokens:
                k_cf = str(display_key).lower()
                if not any(tok in k_cf for tok in tokens):
                    continue

            if isinstance(v, float):
                value_str = f"{v:.3f}"
            elif isinstance(v, list):
                value_str = "[" + ", ".join(
                    [f"{x:.3f}" if isinstance(x, float) else str(x) if x is not None else "None" for x in v]) + "]"
            else:
                value_str = str(v)

            # Keyed by the ORIGINAL telemetry key (always unique in `data`)
            # rather than the display key, so two keys that happen to
            # resolve to the same simvar name under the debug rename don't
            # collide into a single row.
            rows.append((str(key), (str(display_key), value_str)))
        return rows

    def update_effects(self, active_effects_text: str) -> None:
        """``active_effects_text`` is the same newline-joined block
        MainWindow builds today (and also sends over IPC to the master when
        this is a child instance) - one line per started effect. Split into
        rows here; each line already embeds the effect's id, so it is a
        unique-enough row key on its own."""
        lines = [line for line in active_effects_text.split('\n') if line]
        self._effects_model.set_rows([(line, (line,)) for line in lines])

    def clear_effects(self) -> None:
        self._effects_model.clear()

    # ---- external reach-in: TeleplotSetupDialog's key picker ---------------

    def telemetry_keys(self) -> List[str]:
        """The Key column's current values, in row order - what
        ``TeleplotSetupDialog.KeySelectionDialog`` used to get by parsing
        ``lbl_telem_data.text()`` (``line.split(':')[0]``) before this was a
        table. Mirrors the old "Sim not running" placeholder for the
        pre-telemetry state."""
        if self._telem_stack.currentWidget() is self._telem_waiting_label:
            return ['Sim not running']
        return self._telem_model.column_values(0)
