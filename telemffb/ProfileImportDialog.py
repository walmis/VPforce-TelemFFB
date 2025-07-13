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
from PyQt6.QtGui import QColor, QPalette, QFontMetrics, QBrush
from PyQt6.QtWidgets import QDialog, QTableWidgetItem, QComboBox, QAbstractItemView, QHeaderView
import telemffb.globals as G
from telemffb import xmlutils
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
        self.lb_deviceOptions.text_label.setText("<b>Device Options:</b>")
        self.lb_deviceOptions.setToolTip("Include or exclude settings that apply to a specific device type.\n\n"
                                         "Settings which apply to all device types will always be included\n\n"
                                         "If a model has no settings for the enabled device types, it will be remove from the list.")
        self._groups = {}               # { (sim, model, profile): [XML elements] }
        self._existing_keys = set()     # Set of (sim, model, profile) from user config

        self.pb_Ok.clicked.connect(self.accept)
        self.pb_Cancel.clicked.connect(self.reject)
        self.pb_Ok.setEnabled(False)    # OK disabled until all rows are valid

        self.cb_joystick.setChecked(True)
        self.cb_pedals.setChecked(True)
        self.cb_collective.setChecked(True)
        self.cb_trimwheel.setChecked(True)

        # Table appearance
        self.tw_detectedModels.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tw_detectedModels.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.tw_detectedModels.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        for device in ["joystick", "pedals", "collective", "trimwheel"]:
            getattr(self, f"cb_{device}").toggled.connect(self._on_device_toggle)

        self.tw_detectedModels.setEditTriggers(
            QAbstractItemView.EditTrigger.CurrentChanged
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )

    def load_entries(self, imported_sim_defaults, imported_class_defaults, imported_sc_overrides, imported_models,
                     existing_keys):
        self._set_device_checkboxes(imported_models)
        self._existing_keys = set(existing_keys)
        self.load_model_entries(imported_models)
        self.load_override_entries(imported_sim_defaults, imported_class_defaults)
        self.validate_entries()
        self._selected_devices = {
            d for d in ["joystick", "pedals", "collective", "trimwheel"]
            if getattr(self, f"cb_{d}").isChecked()
        }
        self._selected_devices.add("any")
        self._apply_device_filter()

    def _set_device_checkboxes(self, imported_models):
        for device in ["joystick", "pedals", "collective", "trimwheel"]:
            cb = getattr(self, f"cb_{device}")
            if not any(elem.findtext("device") == device for elem in imported_models):
                cb.setChecked(False)
                cb.setEnabled(False)
                cb.setText(f"{device.capitalize()} (none found in import file)")
            else:
                cb.setEnabled(True)

    def load_model_entries(self, imported_models):
        grouped = {}
        for elem in imported_models:
            name = elem.findtext("name")
            profile = elem.find("profile")
            if name == "type" and profile is None:
                continue
            sim = elem.findtext("sim")
            model = elem.findtext("model")
            profile_text = profile.text if profile is not None else None
            key = (sim, model, profile_text)
            grouped.setdefault(key, []).append(elem)

        self._groups = grouped
        self._model_devices = {
            key: {elem.findtext("device") or "any" for elem in elements if
                  elem.findtext("name") not in ("profile", "type")}
            for key, elements in grouped.items()
        }

        self.tw_detectedModels.setRowCount(len(grouped))
        self.tw_detectedModels.setColumnCount(7)
        self.tw_detectedModels.setHorizontalHeaderLabels(
            ["Sim", "Class", "Model", "Profile Name", "Conflict", "Action", "Imported Name"])

        for row, key in enumerate(grouped):
            sim, model, profile = key
            cls = next(
                (
                    el.findtext("value")
                    for el in imported_models
                    if el.findtext("name") in {"type", "profile"}
                       and el.findtext("sim") == sim
                       and el.findtext("model") == model
                ),
                ""
            )
            conflict = key in self._existing_keys

            def new_item(text, editable=False):
                item = QTableWidgetItem(text)
                flags = Qt.ItemFlag.ItemIsEnabled
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                return item

            self.tw_detectedModels.setItem(row, 0, new_item(sim))
            self.tw_detectedModels.setItem(row, 1, new_item(cls))
            self.tw_detectedModels.setItem(row, 2, new_item(model))
            self.tw_detectedModels.setItem(row, 3, new_item(profile))
            self.tw_detectedModels.setItem(row, 4, new_item("Yes" if conflict else "No"))

            combo = QComboBox()
            combo.addItems(["overwrite", "skip", "rename"] if conflict else ["import", "skip", "rename"])
            combo.setCurrentText("rename" if conflict else "import")
            combo.currentTextChanged.connect(lambda _, r=row: self._on_action_changed(r))
            self.tw_detectedModels.setCellWidget(row, 5, combo)

            editable = combo.currentText() == "rename"
            self.tw_detectedModels.setItem(row, 6, new_item("" if editable else profile, editable))

            if conflict:
                bg = QColor("#5c2b2b") if G.useDarkMode else QColor("#ffd6d6")
                fg = QColor("white") if G.useDarkMode else QColor("black")
                for col in [0, 1, 2, 3, 5, 6]:
                    item = self.tw_detectedModels.item(row, col)
                    if item:
                        item.setBackground(bg)
                        item.setForeground(fg)

        self.tw_detectedModels.clearSelection()
        self.tw_detectedModels.itemChanged.connect(self._on_name_changed)
        header = self.tw_detectedModels.horizontalHeader()
        font = header.font()
        metrics = QFontMetrics(font)

        for col in range(self.tw_detectedModels.columnCount()):
            header_text = self.tw_detectedModels.horizontalHeaderItem(col).text()
            header_width = metrics.horizontalAdvance(header_text) + 20
            self.tw_detectedModels.resizeColumnToContents(col)
            content_width = header.sectionSize(col)
            header.resizeSection(col, max(header_width, content_width))

        header.setStretchLastSection(True)

    def load_override_entries(self, sim_defaults, class_defaults):
        self.tw_detectedOverrides.setRowCount(0)
        self.tw_detectedOverrides.setColumnCount(9)
        self.tw_detectedOverrides.setHorizontalHeaderLabels([
            "Sim", "Class", "Device", "Display Name", "Setting Name", "Imported Value", "User Value", "Conflict", "Action"
        ])
        self._override_rows = []

        for defaults_list, is_class in [(sim_defaults, False), (class_defaults, True)]:
            for default in defaults_list:
                sim = default.findtext("sim")
                cls = default.findtext("type") if is_class else "all"
                name = default.findtext("name")
                device = default.findtext("device")
                value = default.findtext("value")

                xpath = f'classSettings[sim="{sim}"][type="{cls}"][name="{name}"][device="{device}"]' if is_class \
                    else f'simSettings[sim="{sim}"][name="{name}"][device="{device}"]'

                conflict = xmlutils.auto_user_root.find(xpath)
                user_value = conflict.findtext("value") if conflict is not None else ""
                default_def = xmlutils.auto_defaults_root.find(
                    f'defaults[name="{name}"][{sim}="true"][{device}="true"]'
                )
                valid = default_def is not None
                display_name = default_def.findtext("displayname") if valid else "<invalid>"

                row = self.tw_detectedOverrides.rowCount()
                self.tw_detectedOverrides.insertRow(row)

                def cell(text):
                    item = QTableWidgetItem(text)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    return item

                self.tw_detectedOverrides.setItem(row, 0, cell(sim))
                self.tw_detectedOverrides.setItem(row, 1, cell(cls))
                self.tw_detectedOverrides.setItem(row, 2, cell(device))
                self.tw_detectedOverrides.setItem(row, 3, cell(display_name or name))
                self.tw_detectedOverrides.setItem(row, 4, cell(name))
                self.tw_detectedOverrides.setItem(row, 5, cell(value))
                self.tw_detectedOverrides.setItem(row, 6, cell(user_value))

                combo = QComboBox()
                combo.currentIndexChanged.connect(lambda _, r=row: self._on_override_action_changed(r))
                conflict_status = "No"
                bg_color = fg_color = None

                if not valid:
                    combo.addItem("exclude")
                    combo.setEnabled(False)
                    conflict_status = "invalid"
                    bg_color = QColor("#ffffcc") if not G.useDarkMode else QColor("#554400")
                    fg_color = QColor("black") if not G.useDarkMode else QColor("white")

                elif value == user_value and user_value:
                    combo.addItem("ignored")
                    combo.setEnabled(False)
                    conflict_status = "match"
                    fg_color = QColor("#888888")

                elif conflict:
                    combo.addItems(["overwrite", "exclude"])
                    combo.setCurrentIndex(-1)  # force user to resolve
                    conflict_status = "Yes"
                    bg_color = QColor("#ffd6d6") if not G.useDarkMode else QColor("#5c2b2b")
                    fg_color = QColor("black") if not G.useDarkMode else QColor("white")

                else:
                    combo.addItems(["include", "exclude"])
                    combo.setCurrentText("include")

                self.tw_detectedOverrides.setItem(row, 7, cell(conflict_status))
                self.tw_detectedOverrides.setCellWidget(row, 8, combo)

                for col in range(self.tw_detectedOverrides.columnCount()):
                    item = self.tw_detectedOverrides.item(row, col)
                    if item:
                        if bg_color:
                            item.setBackground(bg_color)
                        if fg_color:
                            item.setForeground(fg_color)

                self._override_rows.append((default, conflict, device))

        self.tw_detectedOverrides.resizeColumnsToContents()
        header = self.tw_detectedOverrides.horizontalHeader()
        font = header.font()
        metrics = QFontMetrics(font)

        for col in range(self.tw_detectedOverrides.columnCount()):
            header_text = self.tw_detectedOverrides.horizontalHeaderItem(col).text()
            header_width = metrics.horizontalAdvance(header_text) + 20
            self.tw_detectedOverrides.resizeColumnToContents(col)
            content_width = header.sectionSize(col)
            header.resizeSection(col, max(header_width, content_width))

        header.setStretchLastSection(True)

    def _on_device_toggle(self):
        self._selected_devices = {
            d for d in ["joystick", "pedals", "collective", "trimwheel"]
            if getattr(self, f"cb_{d}").isChecked()
        }
        self._selected_devices.add("any")

        if hasattr(self, "_override_rows"):  # 💡 Ensure it's defined first
            for row, (_, _, device) in enumerate(self._override_rows):
                visible = device in self._selected_devices or device == "any"
                self.tw_detectedOverrides.setRowHidden(row, not visible)

        self._apply_device_filter()
        self.validate_entries()

    def _on_override_action_changed(self, row):
        combo = self.tw_detectedOverrides.cellWidget(row, 8)
        conflict_item = self.tw_detectedOverrides.item(row, 7)

        if combo is None or conflict_item is None:
            return  # Avoid crash if combo box isn't set yet

        selected_action = combo.currentText().strip().lower()

        if selected_action in {"overwrite", "exclude"}:
            conflict_item.setText("No")
            for col in range(self.tw_detectedOverrides.columnCount() - 1):
                item = self.tw_detectedOverrides.item(row, col)
                if item:
                    item.setBackground(QBrush())  # clear background
                    item.setForeground(QBrush())  # reset text color

        elif selected_action == "":
            conflict_item.setText("Yes")
            bg_color = QColor("#ffd6d6") if not G.useDarkMode else QColor("#5c2b2b")
            fg_color = QColor("black") if not G.useDarkMode else QColor("white")

            for col in range(self.tw_detectedOverrides.columnCount() - 1):
                item = self.tw_detectedOverrides.item(row, col)
                if item:
                    item.setBackground(bg_color)
                    item.setForeground(fg_color)

    def _on_action_changed(self, row):
        """
        Triggered when an action is changed in the combo box.
        Updates the editable state and value of the new name field.
        """
        combo = self.tw_detectedModels.cellWidget(row, 5)
        action = combo.currentText()
        name_item = self.tw_detectedModels.item(row, 6)

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
        if item.column() == 6:
            self.validate_entries()

    def _apply_device_filter(self):
        enabled_devices = {
            device for device in ["joystick", "pedals", "collective", "trimwheel"]
            if getattr(self, f"cb_{device}").isChecked()
        }

        if not enabled_devices:
            self.pb_Ok.setEnabled(False)
        else:
            self.validate_entries()

        show_all = bool(enabled_devices)

        for row, key in enumerate(self._groups):
            device_types = self._model_devices.get(key, {"any"})

            visible = False
            if "any" in device_types:
                visible = show_all  # show if any device is selected
            else:
                visible = bool(device_types & enabled_devices)

            self.tw_detectedModels.setRowHidden(row, not visible)

    def validate_entries(self):
        """
        Validates every row in the table:
        - Highlights conflicting renames
        - Enables or disables the OK button
        - Applies correct styling based on action and theme
        """
        has_non_skipped_rows = False
        self.tw_detectedModels.blockSignals(True)
        all_valid = True
        proposed = set()

        default_bg = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base)
        default_fg = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)

        for row, key in enumerate(self._groups):
            sim, model, _ = key
            combo = self.tw_detectedModels.cellWidget(row, 5)
            action = combo.currentText()
            if action != "skip":
                has_non_skipped_rows = True
            name_item = self.tw_detectedModels.item(row, 6)
            conflict_item = self.tw_detectedModels.item(row, 4)
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
                for col in [0, 1, 2, 3, 4, 5, 6]:
                    item = self.tw_detectedModels.item(row, col)
                    if item:
                        item.setBackground(skip_bg)
                        item.setForeground(skip_fg)
                continue  # Skip further formatting

            else:
                name_item.setBackground(default_bg)
                name_item.setForeground(default_fg)
                conflict_item.setText("Yes" if key in self._existing_keys else "No")

            # Reset formatting and reapply based on state
            for col in [0, 1, 2, 3, 4, 5, 6]:
                if action == "rename" and col == 6:
                    continue  # preserve name cell custom style
                item = self.tw_detectedModels.item(row, col)
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

        self.pb_Ok.setEnabled(all_valid and has_non_skipped_rows)
        self.tw_detectedModels.blockSignals(False)

    def get_import_actions(self):
        """
        Extracts the list of import actions and proposed profile names from the table.
        Returns:
            list[dict]: One dictionary per profile with action and data needed for import logic.
            bool: Whether to import sim defaults
            bool: Whether to import class defaults
            set[str]: Set of enabled device types from checkboxes
        """

        enabled_devices = {
            device for device in ["joystick", "pedals", "collective", "trimwheel"]
            if getattr(self, f"cb_{device}").isChecked()
        }

        actions = []
        for row, key in enumerate(self._groups):
            sim, model, orig = key
            combo = self.tw_detectedModels.cellWidget(row, 5)
            action = combo.currentText()
            new_name = self.tw_detectedModels.item(row, 6).text().strip()
            device_types = self._model_devices.get(key, {"any"})

            actions.append({
                "key": key,
                "elements": self._groups[key],
                "action": action,
                "new_profile": new_name if action == "rename" else orig,
                "device_types": device_types
            })

        return actions, enabled_devices
