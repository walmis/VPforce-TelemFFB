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

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette, QFontMetrics
from PyQt6.QtWidgets import QDialog, QTableWidgetItem, QComboBox, QAbstractItemView
import telemffb.globals as G
from telemffb.ui.Ui_ProfileImportDialog import Ui_ProfileImportDialog

class ProfileImportDialog(QDialog, Ui_ProfileImportDialog):
    """
    Dialog to import simulator profile configurations.

    Displays a table of profiles from an import file and compares them against existing profiles.
    Allows the user to choose actions (import, skip, rename, overwrite) for each item, handles
    conflicts, and applies real-time validation and styling based on user selection and state.
    """

    def __init__(self, parent=None):
        """
        Initialize the import dialog UI and logic.
        """
        super().__init__(parent)
        self.setupUi(self)
        self._groups = {}               # { (sim, model, profile): [XML elements] }
        self._existing_keys = set()     # Set of (sim, model, profile) from user config

        self.pb_Ok.clicked.connect(self.accept)
        self.pb_Cancel.clicked.connect(self.reject)
        self.pb_Ok.setEnabled(False)    # OK disabled until all rows are valid

        # Table appearance
        self.tableWidget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tableWidget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.tableWidget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def load_entries(self, imported_elements, existing_keys):
        """
        Load and display the profile entries from the imported XML and compare against existing profiles.
        Adds each profile to the table with default actions, conflict detection, and editable name field if needed.
        """
        self._existing_keys = set(existing_keys)
        grouped = {}

        # Group imported XML elements by (sim, model, profile)
        for elem in imported_elements:
            sim = elem.findtext("sim")
            model = elem.findtext("model")
            profile = elem.findtext("profile")
            key = (sim, model, profile)
            grouped.setdefault(key, []).append(elem)
        self._groups = grouped

        self.tableWidget.setRowCount(len(grouped))
        self.tableWidget.setColumnCount(6)
        self.tableWidget.setHorizontalHeaderLabels(["Sim", "Model", "Profile Name", "Conflict", "Action", "Imported Name"])

        for row, key in enumerate(grouped):
            sim, model, profile = key
            conflict = key in self._existing_keys

            def new_item(text, editable=False):
                item = QTableWidgetItem(text)
                flags = Qt.ItemFlag.ItemIsEnabled
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                return item

            self.tableWidget.setItem(row, 0, new_item(sim))
            self.tableWidget.setItem(row, 1, new_item(model))
            self.tableWidget.setItem(row, 2, new_item(profile))
            self.tableWidget.setItem(row, 3, new_item("Yes" if conflict else "No"))

            # Action dropdown
            combo = QComboBox()
            combo.addItems(["overwrite", "skip", "rename"] if conflict else ["import", "skip", "rename"])
            default_action = "rename" if conflict else "import"
            combo.setCurrentText(default_action)
            combo.currentTextChanged.connect(lambda _, r=row: self._on_action_changed(r))
            self.tableWidget.setCellWidget(row, 4, combo)

            # New name field
            new_name_item = new_item("" if default_action == "rename" else profile, editable=(default_action == "rename"))
            self.tableWidget.setItem(row, 5, new_name_item)

            # Initial row coloring for conflicts
            if conflict:
                bg_color = QColor("#5c2b2b") if G.useDarkMode else QColor("#ffd6d6")
                fg_color = QColor("white") if G.useDarkMode else QColor("black")
                for col in [0, 1, 2, 3, 5]:
                    item = self.tableWidget.item(row, col)
                    if item:
                        item.setBackground(bg_color)
                        item.setForeground(fg_color)

        self.tableWidget.clearSelection()
        self.tableWidget.itemChanged.connect(self._on_name_changed)
        self.validate_entries()

        # Auto-sizing columns based on max of header and content
        self.tableWidget.horizontalHeader().setStretchLastSection(True)
        header = self.tableWidget.horizontalHeader()
        font = header.font()
        metrics = QFontMetrics(font)

        for col in range(self.tableWidget.columnCount()):
            header_text = self.tableWidget.horizontalHeaderItem(col).text()
            header_width = metrics.horizontalAdvance(header_text) + 36
            self.tableWidget.resizeColumnToContents(col)
            content_width = header.sectionSize(col)
            header.resizeSection(col, max(header_width, content_width))

    def _on_action_changed(self, row):
        """
        Triggered when an action is changed in the combo box.
        Updates the editable state and value of the new name field.
        """
        combo = self.tableWidget.cellWidget(row, 4)
        action = combo.currentText()
        name_item = self.tableWidget.item(row, 5)

        if action == "rename":
            name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsEditable)
            name_item.setText("")
        else:
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if action == "skip":
                name_item.setText("")
            else:
                key = list(self._groups.keys())[row]
                name_item.setText(key[2])

        self.validate_entries()

    def _on_name_changed(self, item):
        """
        Called when the user types in the new name field.
        Triggers re-validation.
        """
        if item.column() == 5:
            self.validate_entries()

    def validate_entries(self):
        """
        Validates every row in the table:
        - Highlights conflicting renames
        - Enables or disables the OK button
        - Applies correct styling based on action and theme
        """
        self.tableWidget.blockSignals(True)
        all_valid = True
        proposed = set()

        default_bg = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base)
        default_fg = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)

        for row, key in enumerate(self._groups):
            sim, model, _ = key
            combo = self.tableWidget.cellWidget(row, 4)
            action = combo.currentText()
            name_item = self.tableWidget.item(row, 5)
            conflict_item = self.tableWidget.item(row, 3)
            new_name = name_item.text().strip()
            row_conflict_resolved = False

            if action == "rename":
                new_key = (sim, model, new_name)
                if not new_name or new_key in self._existing_keys or new_key in proposed:
                    all_valid = False
                    color = QColor("#5c2b2b") if G.useDarkMode else QColor("#ffd6d6")
                    name_item.setBackground(color)
                    name_item.setForeground(QColor("white") if G.useDarkMode else QColor("black"))
                    tooltip = "Please enter a new name" if name_item.text() == '' else f"Name '{new_name}' is already in use for this Aircraft/Sim"
                    name_item.setToolTip(tooltip)
                    conflict_item.setText("Yes")
                else:
                    proposed.add(new_key)
                    name_item.setBackground(QColor("#2d4b2d") if G.useDarkMode else QColor("#ccffcc"))
                    name_item.setForeground(QColor("white") if G.useDarkMode else QColor("black"))
                    name_item.setToolTip("")
                    row_conflict_resolved = True
                    conflict_item.setText("No")

            elif action == "overwrite":
                name_item.setBackground(default_bg)
                name_item.setForeground(default_fg)
                conflict_item.setText("No")
                row_conflict_resolved = True

            elif action == "skip":
                # Gray out skipped row
                skip_bg = QColor("#3a3a3a") if G.useDarkMode else QColor("#e0e0e0")
                skip_fg = QColor("#a0a0a0")
                name_item.setBackground(skip_bg)
                name_item.setForeground(skip_fg)
                conflict_item.setText("No")
                for col in [0, 1, 2, 3, 5]:
                    item = self.tableWidget.item(row, col)
                    if item:
                        item.setBackground(skip_bg)
                        item.setForeground(skip_fg)
                continue  # Skip further formatting

            else:
                name_item.setBackground(default_bg)
                name_item.setForeground(default_fg)
                conflict_item.setText("Yes" if key in self._existing_keys else "No")

            # Reset formatting and reapply based on state
            for col in [0, 1, 2, 3, 5]:
                if action == "rename" and col == 5:
                    continue  # preserve name cell custom style
                item = self.tableWidget.item(row, col)
                if not item:
                    continue
                item.setForeground(default_fg)
                item.setBackground(default_bg)

                # Repaint conflict rows
                if not row_conflict_resolved and key in self._existing_keys:
                    bg_color = QColor("#5c2b2b") if G.useDarkMode else QColor("#ffd6d6")
                    fg_color = QColor("white") if G.useDarkMode else QColor("black")
                    item.setBackground(bg_color)
                    item.setForeground(fg_color)

        self.pb_Ok.setEnabled(all_valid)
        self.tableWidget.blockSignals(False)

    def get_import_actions(self):
        """
        Extracts the list of import actions and proposed profile names from the table.
        Returns:
            list[dict]: One dictionary per profile with action and data needed for import logic.
        """
        actions = []
        for row, key in enumerate(self._groups):
            sim, model, orig = key
            combo = self.tableWidget.cellWidget(row, 4)
            action = combo.currentText()
            new_name = self.tableWidget.item(row, 5).text().strip()
            actions.append({
                "key": key,
                "elements": self._groups[key],
                "action": action,
                "new_profile": new_name if action == "rename" else orig
            })
        return actions
