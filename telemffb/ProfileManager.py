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


import logging
import os
import re


from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, pyqtSlot, QRegularExpression
from PyQt6.QtGui import QAction, QIcon, QRegularExpressionValidator
from PyQt6.QtWidgets import QDialog, QMessageBox, QTreeWidgetItem, QHeaderView, QStyle, QMenu, QFileDialog, QTreeWidget, \
    QInputDialog, QTableWidgetItem, QComboBox, QVBoxLayout, QLabel, QLineEdit, QCheckBox, QHBoxLayout, QPushButton, \
    QRadioButton, QButtonGroup, QApplication, QListWidget, QListWidgetItem, QTableWidget, QAbstractItemView

import telemffb.globals as G
from telemffb.ui.Ui_ProfileManagerDialog import Ui_ProfileManagerDialog
from telemffb.ProfileImportDialog import ProfileImportDialog
from telemffb.NewAircraftWizard import NewAircraftWizard
from telemffb.utils import dbprint
import xml.etree.ElementTree as ET
import telemffb.xmlutils as xmlutils
import time

class ProfileManagerDialog(QDialog, Ui_ProfileManagerDialog):
    def __init__(self, parent=None):
        super(ProfileManagerDialog, self).__init__(parent)
        self.sorted_column = -1
        self.sort_order = Qt.SortOrder.AscendingOrder

        self.setupUi(self)
        self.retranslateUi(self)
        current_ac = G.settings_mgr.current_pattern
        self.setWindowTitle(f"Profile Manager - Active Aircraft: {current_ac}")
        # self.populate_aircraft_tree(self.treeWidget)
        self.treeWidget.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)

        self.treeWidget.setColumnCount(4)
        self.treeWidget.setHeaderLabels(["Aircraft Name", "Source", "Profile Name", "Active"])
        self.treeWidget.headerItem().setToolTip(0, "Click to sort on Aircraft Name")
        self.treeWidget.headerItem().setToolTip(1, "Click to sort on Source")
        self.treeWidget.headerItem().setToolTip(1, "Click to sort on Profile Name")
        self.header = self.treeWidget.header()
        self.header.setStretchLastSection(False)
        self.header.setSortIndicatorShown(True)
        self.header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # First column: stretch to fill
        self.header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Second column: shrink to fit
        self.header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Second column: shrink to fit
        self.header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Second column: shrink to fit
        self.header.show()
        self.header.setSectionsClickable(True)
        self.header.sectionClicked.connect(self.on_header_clicked)
        self.treeWidget.itemSelectionChanged.connect(self.on_tree_item_selected)
        self.treeWidget.expandAll()

        self.tb_aircraft_filter.textChanged.connect(self.apply_combined_filters)
        self.tb_profile_filter.textChanged.connect(self.apply_combined_filters)
        self.filterButtonGroup.buttonToggled.connect(lambda b, c: self.apply_combined_filters() if c else None)

        self.pb_clear_filters.clicked.connect(self.clear_text_filters)
        self.pb_Import.clicked.connect(self.import_profiles)
        self.pb_Clone.clicked.connect(self.on_clone_clicked)
        self.pb_Activate.clicked.connect(self.on_activate_clicked)
        self.pb_Edit.clicked.connect(self.on_edit_clicked)

        self.pb_newAircraft.clicked.connect(self.on_new_wizard_clicked)


        # self.cb_showDefaults.toggled.connect(self.toggle_show_defaults)

        self.treeWidget.setStyleSheet("""
        QTreeWidget::item {
            padding-right: 12px;
        }
        """)
        self.pb_Delete.clicked.connect(self.delete_profile)
        self.pb_Exit.clicked.connect(self.close)
        self.pb_expandButton.clicked.connect(self.toggle_tree_expansion)
        self.treeWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeWidget.customContextMenuRequested.connect(self.open_context_menu)
        # self.cb_filterActive.toggled.connect(self.filter_active_profiles)
        # self.cb_filterCurrentAircraft.toggled.connect(self.on_filter_current_ac_clicked)
        self.start_tree_population()

    def start_tree_population(self):
        """
        Starts the background tree population process in a separate thread.
        Prevents UI freezing or IPC thread starvation during heavy XML processing.
        """
        placeholder = QTreeWidgetItem(["🔄 Building tree... Please wait"])
        placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.treeWidget.addTopLevelItem(placeholder)


        self.thread = QThread()
        self.worker = TreePopulationWorker()
        self.worker.moveToThread(self.thread)

        # Wire signals
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_tree_population_done)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        # Save scroll position
        self._last_scroll_pos = self.treeWidget.verticalScrollBar().value()

        # Save selected item text (column 0), or use custom data if more precise identification is needed
        selected_item = self.treeWidget.currentItem()
        self._last_selected_text = selected_item.text(0) if selected_item else None

        # Start thread
        self.thread.start()

    def on_tree_population_done(self, result_data):
        """
        Processes data from the background tree population worker.

        Args:
            result_data (list): Structured list of sim/class/aircraft dicts from the worker.

        Updates:
            - Clears and rebuilds the QTreeWidget with aircraft profile data.
            - Applies appropriate metadata and visuals per item.
        """
        self.treeWidget.clear()

        for sim in result_data:
            sim_item = QTreeWidgetItem([sim["name"]])
            sim_item.setFlags(sim_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            sim_item.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "sim",
                "sim_name": sim["name"],
                "enabled": sim["enabled"]
            })
            self.treeWidget.addTopLevelItem(sim_item)

            for cls in sim["classes"]:
                cls_item = QTreeWidgetItem([cls["name"]])
                cls_item.setFlags(cls_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                sim_item.addChild(cls_item)

                for ac in cls["aircraft"]:
                    ac_item = QTreeWidgetItem([
                        ac["aircraft"],
                        ac["source"],
                        ac["profile"] if ac["source"] == "User" else ""
                    ])
                    ac_item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": ac["type"],
                        "sim_name": sim["name"],
                        "cls_name": cls["name"],
                        "aircraft_name": ac["aircraft"],
                        "profile_name": ac["profile"],
                        "active": ac["active"],
                        "enabled": sim["enabled"]
                    })
                    ac_item.setHidden(not ac["show"])
                    cls_item.addChild(ac_item)

                    if ac["active"]:
                        self.mark_item_as_active(ac_item)
                    self.update_active_column_text(ac_item, ac["active"])

        self.sort_aircraft_items()
        self.prune_empty_tree_items(self.treeWidget)
        self.treeWidget.expandAll()

        # Restore scroll position
        if hasattr(self, '_last_scroll_pos'):
            self.treeWidget.verticalScrollBar().setValue(self._last_scroll_pos)

        # Restore selection
        def find_item_by_text(text: str, parent_item: QTreeWidgetItem = None) -> QTreeWidgetItem | None:
            if parent_item is None:
                parent_item = self.treeWidget.invisibleRootItem()

            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if child.text(0) == text:
                    return child
                result = find_item_by_text(text, child)
                if result:
                    return result
            return None

        if getattr(self, '_last_selected_text', None):
            item_to_select = find_item_by_text(self._last_selected_text)
            if item_to_select:
                self.treeWidget.setCurrentItem(item_to_select)
                self.treeWidget.scrollToItem(item_to_select, QAbstractItemView.ScrollHint.PositionAtCenter)

        self.pb_Delete.setEnabled(False)
        self.pb_Export.setEnabled(False)
        self.pb_Clone.setEnabled(False)
        self.pb_Edit.setEnabled(False)
        self.pb_Activate.setEnabled(False)

        self.pb_Export.clicked.connect(self.export_selected_profile)


    def open_context_menu(self, position):
        item = self.treeWidget.itemAt(position)
        if not item:
            return
        multiselection = len(self.treeWidget.selectedItems()) > 1

        menu = QMenu()

        # Example context-specific actions
        type = self.get_metadata(item, "type")
        if (type == "user" or type == "default") and not multiselection:
            if not self.get_metadata(item, 'active'):
                p_name = self.get_metadata(item, 'profile_name')
                a_name = self.get_metadata(item, 'aircraft_name')
                make_active_action = QAction(f"Set '{p_name}' as active for '{a_name}'", self)
                make_active_action.triggered.connect(lambda: self.make_active_profile(item))
                menu.addAction(make_active_action)

        if type == "user":
            export_action = QAction("Export selection to file", self)
            export_action.triggered.connect(self.export_selected_profile)
            menu.addAction(export_action)

            delete_action = QAction("Delete selected profile(s)", self)
            delete_action.triggered.connect(self.delete_profile)
            menu.addAction(delete_action)

        menu.exec(self.treeWidget.viewport().mapToGlobal(position))

    def toggle_tree_expansion(self):
        if self.treeWidget.topLevelItemCount() == 0:
            return  # avoid errors if tree is empty

        index = self.treeWidget.indexFromItem(self.treeWidget.topLevelItem(0))
        if self.treeWidget.isExpanded(index):
            self.treeWidget.collapseAll()
            self.lb_expandButton.setText("Expand All:")
            self.pb_expandButton.setArrowType(Qt.ArrowType.DownArrow)
        else:
            self.treeWidget.expandAll()
            self.lb_expandButton.setText("Collapse All:")
            self.pb_expandButton.setArrowType(Qt.ArrowType.UpArrow)

    def get_enabled_sims(self):
        enabled_sims = []
        for sim in xmlutils.get_sims():
            if G.system_settings.get(f'enable{sim}', False):
                enabled_sims.append(sim)
        return sorted(enabled_sims)

    def on_tree_item_selected(self):
        selected_item = self.treeWidget.selectedItems()


        if selected_item:
            # any item(s) are selected
            if len(self.treeWidget.selectedItems()) > 1:
                # more than one item is selcted
                self.pb_Clone.setEnabled(False)
                self.pb_Edit.setEnabled(False)
                self.pb_Activate.setEnabled(False)
                if any(self.get_metadata(item, "type") == "user" for item in selected_item):
                    self.pb_Export.setEnabled(True)
                else:
                    self.pb_Export.setEnabled(False)
            else:
                # only one item is selected
                self.pb_Clone.setEnabled(True)
                self.pb_Edit.setEnabled(True)
                if self.get_metadata(selected_item[0], 'active'):
                    self.pb_Activate.setEnabled(False)
                else:
                    self.pb_Activate.setEnabled(True)

                if self.get_metadata(selected_item[0], "type") == "user":
                    self.pb_Delete.setEnabled(True)
                    self.pb_Export.setEnabled(True)
                    self.pb_Edit.setEnabled(True)
                else:
                    self.pb_Delete.setEnabled(False)
                    self.pb_Export.setEnabled(False)
                    self.pb_Edit.setEnabled(False)
        else:
            # all items unsulected
            self.pb_Delete.setEnabled(False)
            self.pb_Export.setEnabled(False)
            self.pb_Edit.setEnabled(False)
            self.pb_Clone.setEnabled(False)
            self.pb_Activate.setEnabled(False)


    def on_header_clicked(self, logicalIndex):
        if self.sorted_column == logicalIndex:
            self.sort_order = (
                Qt.SortOrder.DescendingOrder
                if self.sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self.sorted_column = logicalIndex
            self.sort_order = Qt.SortOrder.AscendingOrder

        # Set the sort arrow indicator
        self.header.setSortIndicator(logicalIndex, self.sort_order)

        self.sort_aircraft_items(by_column=logicalIndex, order=self.sort_order)

    def on_filter_mode_changed(self, button, checked):
        if not checked:
            return  # Ignore button uncheck events

        if button == self.rb_showAll:
            self.clear_sim_class_model_filter()
            return

        if button == self.rb_showDefaults:
            self.clear_sim_class_model_filter()
            self.toggle_show_defaults(True)

        elif button == self.rb_filterActive:
            self.clear_sim_class_model_filter()
            self.filter_active_profiles(True)

        elif button == self.rb_filterCurrentAircraft:
            self.toggle_show_defaults(False)  # Hide defaults before filtering
            self.filter_by_sim_class_model(
                G.settings_mgr.current_sim,
                G.settings_mgr.current_class,
                G.settings_mgr.current_pattern
            )

    def apply_combined_filters(self):
        """Apply both the radio button and text filter constraints."""
        self.apply_radio_filter()  # Applies showDefaults, active-only, etc.
        self.apply_text_filters()  # Then narrows results to matching search

    def filter_active_profiles(self, show_only_active_user):
        """
        Filters the tree view to display only active user-created profiles when toggled.

        If the toggle is on:
            - Only user profiles with an active status are shown.
            - Default profiles are hidden regardless of the 'Show Defaults' checkbox unless the default profile is active
              and there are other active user profiles for that aircraft .

        If the toggle is off:
            - Default profiles are shown or hidden based on 'Show Defaults' checkbox.
            - All user profiles are shown.

        Args:
            show_only_active_user (bool): True if filtering for active user profiles only.
        """
        def process_leaf(item):
            item_type = self.get_metadata(item, "type")
            ac_name = self.get_metadata(item, "aircraft_name")
            cls = item.parent()
            siblings = [cls.child(k) for k in range(cls.childCount())
                        if self.get_metadata(cls.child(k), "aircraft_name") == ac_name]
            has_user = any(self.get_metadata(s, "type") == "user" for s in siblings)
            is_active = item.checkState(3) == Qt.CheckState.Checked

            if show_only_active_user:
                if item_type == "user":
                    item.setHidden(not is_active)
                elif item_type == "default":
                    item.setHidden(not (is_active and has_user))
            else:
                if item_type == "default":
                    item.setHidden(not (is_active and has_user))
                else:
                    item.setHidden(False)

        def recurse(node):
            if node.childCount() == 0:
                process_leaf(node)
            else:
                for i in range(node.childCount()):
                    recurse(node.child(i))

        for idx in range(self.treeWidget.topLevelItemCount()):
            recurse(self.treeWidget.topLevelItem(idx))

        self.prune_empty_tree_items(self.treeWidget)

    def filter_by_sim_class_model(self, target_sim, target_class, target_model):
        """
        Filters the tree to show only items matching the specified sim, class, and model.
        All unrelated branches will be hidden.

        Args:
            target_sim (str): Simulator name (e.g., 'MSFS').
            target_class (str): Aircraft class (e.g., 'Fighter').
            target_model (str): Aircraft model name (e.g., 'F-16').
        """
        for i in range(self.treeWidget.topLevelItemCount()):
            sim_item = self.treeWidget.topLevelItem(i)
            sim_match = sim_item.text(0) == target_sim
            sim_item.setHidden(not sim_match)

            for j in range(sim_item.childCount()):
                cls_item = sim_item.child(j)
                cls_match = cls_item.text(0) == target_class
                cls_item.setHidden(not (sim_match and cls_match))

                for k in range(cls_item.childCount()):
                    ac_item = cls_item.child(k)
                    ac_name = self.get_metadata(ac_item, "aircraft_name")
                    ac_item.setHidden(not (sim_match and cls_match and ac_name == target_model))

        self.prune_empty_tree_items(self.treeWidget)

    def clear_text_filters(self):
        self.tb_aircraft_filter.setText('')
        self.tb_profile_filter.setText('')

    def apply_text_filters(self):
        aircraft_query = self.tb_aircraft_filter.text().lower()
        profile_query = self.tb_profile_filter.text().lower()

        def matches(item):
            aircraft = item.text(0).lower()
            profile = item.text(2).lower()
            return aircraft_query in aircraft and profile_query in profile

        def recurse(item):
            if item.childCount() == 0:  # Leaf
                if item.isHidden():
                    return False  # Already hidden by radio logic
                item.setHidden(not matches(item))
            else:
                for i in range(item.childCount()):
                    recurse(item.child(i))

        for i in range(self.treeWidget.topLevelItemCount()):
            recurse(self.treeWidget.topLevelItem(i))

        self.prune_empty_tree_items(self.treeWidget)

    def apply_radio_filter(self):
        button = self.filterButtonGroup.checkedButton()

        if button == self.rb_showAll:
            self.clear_sim_class_model_filter()
        elif button == self.rb_showDefaults:
            self.clear_sim_class_model_filter()
            self.toggle_show_defaults(True)
        elif button == self.rb_filterActive:
            self.clear_sim_class_model_filter()
            self.filter_active_profiles(True)
        elif button == self.rb_filterCurrentAircraft:
            self.toggle_show_defaults(False)
            self.filter_by_sim_class_model(
                G.settings_mgr.current_sim,
                G.settings_mgr.current_class,
                G.settings_mgr.current_pattern
            )

    def clear_sim_class_model_filter(self):
        for i in range(self.treeWidget.topLevelItemCount()):
            sim_item = self.treeWidget.topLevelItem(i)
            sim_item.setHidden(False)
            for j in range(sim_item.childCount()):
                cls_item = sim_item.child(j)
                cls_item.setHidden(False)
                for k in range(cls_item.childCount()):
                    ac_item = cls_item.child(k)
                    ac_item.setHidden(False)

        # Re-apply whatever the currently selected radio button is
        active_button = self.filterButtonGroup.checkedButton()
        if active_button == self.rb_showDefaults:
            self.toggle_show_defaults(True)
        elif active_button == self.rb_filterActive:
            self.filter_active_profiles(True)
        elif active_button == self.rb_filterCurrentAircraft:
            self.filter_by_sim_class_model(
                G.settings_mgr.current_sim,
                G.settings_mgr.current_class,
                G.settings_mgr.current_pattern
            )

    def sort_aircraft_items(self, by_column=0, order=Qt.SortOrder.AscendingOrder):
        reverse = order == Qt.SortOrder.DescendingOrder
        for i in range(self.treeWidget.topLevelItemCount()):
            sim_item = self.treeWidget.topLevelItem(i)
            for j in range(sim_item.childCount()):
                cls_item = sim_item.child(j)
                aircraft_items = [cls_item.child(k) for k in range(cls_item.childCount())]

                if by_column == 2:  # Profile Name
                    with_profile = [item for item in aircraft_items if item.text(by_column)]
                    without_profile = [item for item in aircraft_items if not item.text(by_column)]
                    with_profile.sort(key=lambda item: item.text(by_column).lower(), reverse=reverse)
                    sorted_items = with_profile + without_profile
                else:
                    sorted_items = sorted(aircraft_items, key=lambda item: item.text(by_column).lower(),
                                          reverse=reverse)

                cls_item.takeChildren()
                for ac in sorted_items:
                    cls_item.addChild(ac)

    def mark_item_as_active(self, item, column=3):
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)  # Remove user toggle ability
        item.setData(column, Qt.ItemDataRole.CheckStateRole, Qt.CheckState.Checked)

    def unmark_item_as_active(self, item, column=3):
        item.setData(column, Qt.ItemDataRole.CheckStateRole, Qt.CheckState.Unchecked)


    def get_metadata(self, item, property):
        metadata = item.data(0, Qt.ItemDataRole.UserRole)
        return metadata.get(property, None) if metadata else None

    def prune_empty_tree_items(self, tree_widget):
        for i in range(tree_widget.topLevelItemCount()):
            sim_item = tree_widget.topLevelItem(i)
            sim_visible = False

            for j in range(sim_item.childCount()):
                cls_item = sim_item.child(j)
                cls_visible = any(not cls_item.child(k).isHidden() for k in range(cls_item.childCount()))
                cls_item.setHidden(not cls_visible)

                if cls_visible:
                    sim_visible = True

            sim_item.setHidden(not sim_visible)

    def toggle_show_defaults(self, checked):
        """
        If checked, only show default profiles. All user profiles are hidden.
        If unchecked, visibility is not altered.
        """
        for i in range(self.treeWidget.topLevelItemCount()):
            sim_item = self.treeWidget.topLevelItem(i)
            for j in range(sim_item.childCount()):
                cls_item = sim_item.child(j)

                for k in range(cls_item.childCount()):
                    item = cls_item.child(k)
                    item_type = self.get_metadata(item, "type")
                    is_active = self.get_metadata(item, "active")

                    if checked:
                        # Show only default profiles
                        if item_type == "default":
                            item.setHidden(False)
                        else:
                            item.setHidden(True)
                    else:
                        # Reset visibility (handled elsewhere)
                        item.setHidden(False)

        self.prune_empty_tree_items(self.treeWidget)

    def update_active_column_text(self, item, active: bool, column=3):
        """
        Sets an invisible Unicode hint in the 'Active' column for proper sorting.
        The hint ensures active profiles sort differently, even if the column has no visible text.

        Args:
            item (QTreeWidgetItem): The tree item to update.
            active (bool): Whether the profile is active.
            column (int): The column index to update (default is 3).
        """
        # ZWNJ ("\u200C") will sort after ZWSP ("\u200B") but neither is visible.
        sort_hint = "\u200C" if active else "\u200B"
        item.setText(column, sort_hint)

    def make_active_profile(self, target_item):
        """
        Marks the selected aircraft profile as active, and deactivates any other profiles
        for the same aircraft. Also updates the backing XML file.

        - Sets the active checkbox UI state.
        - Updates the item's metadata ("active").
        - Applies an invisible Unicode hint for proper sorting in the Active column.
        - Updates the user's config XML file to store the active profile state.

        Args:
            target_item (QTreeWidgetItem): The selected profile item to mark as active.
        """
        if not target_item:
            return

        aircraft_name, cls_name, sim_name = self.get_aircraft_info(target_item)
        cls_item = target_item.parent()

        for i in range(cls_item.childCount()):
            child = cls_item.child(i)
            meta = child.data(0, Qt.ItemDataRole.UserRole) or {}
            same_aircraft = meta.get("aircraft_name") == aircraft_name

            if not same_aircraft:
                continue

            is_target = (child is target_item)

            # Update UI check state
            child.setData(3, Qt.ItemDataRole.CheckStateRole,
                          Qt.CheckState.Checked if is_target else Qt.CheckState.Unchecked)

            # Update metadata
            meta["active"] = is_target
            child.setData(0, Qt.ItemDataRole.UserRole, meta)

            # Update invisible sort text for consistent column sorting
            self.update_active_column_text(child, active=is_target)

            # Update XML for target only
            if is_target:
                profile_name = meta.get("profile_name")
                if profile_name:
                    xmlutils.update_active_profile_entry(sim_name, cls_name, aircraft_name, profile_name)

    def get_aircraft_info(self, item):
        """
        Extract aircraft-related metadata from a QTreeWidgetItem.

        Parameters:
            item (QTreeWidgetItem): The item representing an aircraft entry.

        Returns:
            tuple[str, str, str]: A tuple containing (aircraft_name, cls_name, sim_name).

        Raises:
            ValueError: If any of the expected metadata keys are missing.
        """
        metadata = item.data(0, Qt.ItemDataRole.UserRole)
        if not metadata:
            raise ValueError("Item metadata is missing.")

        aircraft_name = metadata.get("aircraft_name")
        cls_name = metadata.get("cls_name")
        sim_name = metadata.get("sim_name")

        if not all([aircraft_name, cls_name, sim_name]):
            raise ValueError("Incomplete aircraft metadata in item.")

        return aircraft_name, cls_name, sim_name

    def delete_profile(self):
        """
        Handles deletion of selected user profiles with safety checks:
        - Prevents deletion of default profiles.
        - Disallows deletion of multiple profiles if any are active.
        - If deleting a single active profile and other profiles exist for the same aircraft,
          prompts user to select a new active profile.
        - If no other user profiles exist, deletes all profile records for the aircraft.
        """
        items = [i for i in self.treeWidget.selectedItems() if i.childCount() == 0]
        if not items:
            return

        # Block default profile deletions
        default_items = [i for i in items if self.get_metadata(i, "type") == "default"]
        if default_items:
            names = "\n".join(f"{i.text(0)} (Default)" for i in default_items)
            QMessageBox.warning(
                self,
                "Cannot Delete Default Profiles",
                f"The following default profiles cannot be deleted:\n\n{names}\n\nPlease de-select and try again"
            )
            return

        # Disallow multi-selection if any are active
        if any(self.get_metadata(i, "active") for i in items):
            if len(items) > 1:
                QMessageBox.warning(
                    self, "Cannot Multi-Delete Active Profiles",
                    "Active profiles cannot be deleted as part of a multi-selection.\n\n"
                    "Please select only *one* active profile for deletion."
                )
                return

            # Handle single active profile deletion
            active_item = items[0]
            sim = active_item.parent().parent().text(0)
            cls = self.get_metadata(active_item, "cls_name")
            model = active_item.text(0)
            profile = active_item.text(2)
            cls_item = active_item.parent()

            # Get sibling profiles for same aircraft
            siblings = [
                cls_item.child(i)
                for i in range(cls_item.childCount())
                if self.get_metadata(cls_item.child(i), "aircraft_name") == model
            ]
            user_siblings = [s for s in siblings if self.get_metadata(s, "type") == "user" and s is not active_item]

            # If no other user profiles...
            if not user_siblings:
                # Ask for confirmation before continuing
                confirm = QMessageBox.question(
                    self, "Confirm Deletion",
                    f"This is the last user profile for '{model}'.\n"
                    "Are you sure you want to delete it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return

                # Check for default fallback
                default_item = next((s for s in siblings if self.get_metadata(s, "type") == "default"), None)

                if default_item:
                    self.make_active_profile(default_item)
                    xmlutils.erase_model_profile(sim, model, profile)
                    cls_item.removeChild(active_item)
                    QMessageBox.information(
                        self, "Deleted",
                        f"Deleted profile '{profile}' for '{model}'.\nDefault profile has been set as active."
                    )
                else:
                    xmlutils.erase_aircraft_profiles(sim, cls, model)
                    for sib in siblings:
                        cls_item.removeChild(sib)
                    QMessageBox.information(self, "Deleted", f"Deleted entire aircraft profile set for '{model}'.")

                self.prune_empty_tree_items(self.treeWidget)
                return

            # Ask which profile should become active
            options = [self.get_metadata(s, "profile_name") for s in user_siblings]
            has_default = any(self.get_metadata(s, "type") == "default" for s in siblings)
            if has_default:
                options.insert(0, "Default")

            new_active, ok = QInputDialog.getItem(
                self, "Choose Replacement Active Profile",
                f"'{profile}' is active. Choose replacement active profile for '{model}':",
                options, 0, False
            )
            if not ok:
                return

            # Make selected replacement profile active
            for sib in siblings:
                if (new_active == "Default" and self.get_metadata(sib, "type") == "default") or \
                        self.get_metadata(sib, "profile_name") == new_active:
                    self.make_active_profile(sib)
                    break

            # Delete original
            xmlutils.erase_model_profile(sim, model, profile)
            cls_item.removeChild(active_item)
            self.prune_empty_tree_items(self.treeWidget)
            QMessageBox.information(self, "Deleted", f"Deleted profile '{profile}' for '{model}'.")
            return

        # Handle standard deletions
        names = "\n".join(f"{i.text(0)} ({i.text(2)})" for i in items)
        resp = QMessageBox.question(
            self, "Confirm Deletion",
            f"Delete the following profiles?\n\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        for item in items:
            sim = item.parent().parent().text(0)
            model = item.text(0)
            profile = item.text(2)
            xmlutils.erase_model_profile(sim, model, profile)
            item.parent().removeChild(item)

        self.prune_empty_tree_items(self.treeWidget)
        QMessageBox.information(self, "Deleted", f"Deleted {len(items)} profile(s).")

    def remove_tree_item(self, selected_item):
        if selected_item:
            item = selected_item[0]
            parent = item.parent()
            if parent:
                parent.removeChild(item)
                self.prune_empty_tree_items(self.treeWidget)

    def export_multiple_profile_xmls(
            self,
            profiles,
            export_sim,
            export_class,
            devices,
            output_dir,
            get_metadata
    ):
        device_types = devices + ["any"]
        seen_global = set()

        for item in profiles:
            sim = get_metadata(item, "sim_name")
            cls = get_metadata(item, "cls_name")
            model = item.text(0)
            profile = get_metadata(item, "profile_name")

            root = ET.Element("TelemFFB")
            seen = set()

            # Export sim-level defaults
            if export_sim:
                for device in device_types:
                    for el in xmlutils.get_sim_defaults(sim, device):
                        key = ET.tostring(el)
                        if key not in seen:
                            root.append(el)
                            seen.add(key)

            # Export class-level defaults
            if export_class:
                for device in device_types:
                    for el in xmlutils.get_class_defaults(sim, cls, device):
                        key = ET.tostring(el)
                        if key not in seen:
                            root.append(el)
                            seen.add(key)

            # Export model overrides
            for el in xmlutils.get_sc_override(model):
                key = ET.tostring(el)
                if key not in seen:
                    root.append(el)
                    seen.add(key)

            # Export model type
            type_el = xmlutils.get_model_type(sim, model, cls)
            if type_el is not None:
                key = ET.tostring(type_el)
                if key not in seen:
                    root.append(type_el)
                    seen.add(key)

            # Export model settings
            for device in device_types:
                for el in xmlutils.get_model_profile(sim, model, profile, device):
                    key = ET.tostring(el)
                    if key not in seen:
                        root.append(el)
                        seen.add(key)

            tree = ET.ElementTree(root)
            tree = xmlutils.consolidate_sort_and_write_userconfig(tree, ret=True)

            filename = f"{sim}_{cls}_{model}_{profile}.xml"
            filename = self.sanitize_filename(filename)
            file_path = os.path.join(output_dir, filename)

            tree.write(file_path, encoding="utf-8", xml_declaration=True)

    def export_combined_profile_xml(
            self,
            profiles,
            export_sim,
            export_class,
            devices,
            output_path,
            get_metadata
    ):
        root = ET.Element("TelemFFB")
        seen = set()
        device_types = devices + ["any"]

        sims = set()
        classes = set()
        models = set()

        for item in profiles:
            sim = get_metadata(item, "sim_name")
            cls = get_metadata(item, "cls_name")
            model = item.text(0)
            profile = get_metadata(item, "profile_name")


            sims.add(sim)
            classes.add((sim, cls))
            models.add((sim, model, cls, profile))

        if export_sim:
            for sim in sims:
                for device in device_types:
                    for el in xmlutils.get_sim_defaults(sim, device):
                        key = ET.tostring(el)
                        if key not in seen:
                            root.append(el)
                            seen.add(key)

        if export_class:
            for sim, cls in classes:
                for device in device_types:
                    for el in xmlutils.get_class_defaults(sim, cls, device):
                        key = ET.tostring(el)
                        if key not in seen:
                            root.append(el)
                            seen.add(key)

        for sim, model, cls, profile in models:
            for el in xmlutils.get_sc_override(model):
                key = ET.tostring(el)
                if key not in seen:
                    root.append(el)
                    seen.add(key)

            type_el = xmlutils.get_model_type(sim, model, cls)
            if type_el is not None:
                key = ET.tostring(type_el)
                if key not in seen:
                    root.append(type_el)
                    seen.add(key)

            for device in device_types:
                for el in xmlutils.get_model_profile(sim, model, profile, device):
                    key = ET.tostring(el)
                    if key not in seen:
                        root.append(el)
                        seen.add(key)

        tree = ET.ElementTree(root)
        tree = xmlutils.consolidate_sort_and_write_userconfig(tree, ret=True)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)


    def export_selected_profile(self):
        """
        Export selected user profiles to XML.

        - Single selection: prompts for save file name.
        - Multi-selection: lets user choose single file (combined) or multiple files (one per profile).

        Skips invalid/default entries. Cleans file names.
        """
        xmlutils.update_roots() # make sure roots get updated in case state is timedout and file has changed

        selected_items = self.treeWidget.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more profiles to export.")
            return

        dialog = ExportOptionsDialog(selected_items, self.get_metadata, self)
        if dialog.exec():
            filtered_items, export_sim, export_class, export_mode, selected_devices = dialog.get_data()
        else:
            return

        if not filtered_items:
            QMessageBox.warning(self, "No Exportable Profiles",
                                "All selected profiles are default and cannot be exported.")
            return

        if export_mode == "combined":
            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Combined Profile Export",
                "profile_export.xml",
                "XML Files (*.xml)"
            )
            if not save_path:
                return  # Cancelled

            try:
                self.export_combined_profile_xml(
                    profiles=filtered_items,
                    export_sim=export_sim,
                    export_class=export_class,
                    devices=selected_devices,
                    output_path=save_path,
                    get_metadata=self.get_metadata
                )
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", f"An error occurred while exporting:\n{e}")
                return

            QMessageBox.information(self, "Export Complete",
                                    f"{len(filtered_items)} profiles exported to:\n{save_path}")
        else:
            # Multi-file export
            output_dir = QFileDialog.getExistingDirectory(
                self,
                "Select Export Folder",
                "",
                QFileDialog.Option.ShowDirsOnly
            )
            if not output_dir:
                return  # Cancelled

            try:
                self.export_multiple_profile_xmls(
                    profiles=filtered_items,
                    export_sim=export_sim,
                    export_class=export_class,
                    devices=selected_devices,
                    output_dir=output_dir,
                    get_metadata=self.get_metadata
                )
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", f"An error occurred during export:\n{e}")
                return

            QMessageBox.information(self, "Export Complete",
                                    f"{len(filtered_items)} profiles exported to:\n{output_dir}")



    def export_profile_to_xml(self, path: str, sim: str, cls: str, model: str, profile: str) -> bool:
        """
        Export all matching <models> entries to a new XML file,
        preserving all sub-elements (including optional ones like <unit>, future additions, etc.).

        Args:
            path (str): Destination XML file path.
            sim (str): Simulator (e.g., "MSFS").
            cls (str): Aircraft class (for future use).
            model (str): Aircraft model.
            profile (str): Profile name.

        Returns:
            bool: True if entries were found & saved; False otherwise.

        Raises:
            ValueError if any parameter is missing.
        """
        if not all([path, sim, cls, model, profile]):
            raise ValueError("All parameters (path, sim, cls, model, profile) are required.")

        tree = xmlutils.try_parse(xmlutils.userconfig_path)
        root = tree.getroot()

        # Filter for matching entries
        entries = [
            elem
            for elem in root.findall("models")
            if elem.findtext("sim") == sim
               and elem.findtext("model") == model
               and elem.findtext("profile") == profile
        ]

        if not entries:
            logging.warning(f"No matching entries for sim={sim}, model={model}, profile={profile}")
            return False

        # Build export XML with full sub-elements
        export_root = ET.Element("TelemFFB")
        for elem in entries:
            export_root.append(ET.fromstring(ET.tostring(elem)))

        ET.ElementTree(export_root).write(path, encoding="utf-8", xml_declaration=True)
        logging.info(f"Exported {len(entries)} entries to: {path}")
        return True


    def import_profiles(self):
        xmlutils.update_roots() # make sure roots get updated in case state is timedout and file has changed

        # Load and parse the import file
        filename, _ = QFileDialog.getOpenFileName(self, "Import Aircraft Profiles", "", "XML Files (*.xml)")
        if not filename:
            return

        tree = ET.parse(filename)
        imported_sim_defaults = tree.getroot().findall('simSettings')
        imported_class_defaults = tree.getroot().findall('classSettings')
        imported_sc_overrides = tree.getroot().findall('sc_overrides')
        imported_models = tree.getroot().findall("models")

        # Gather unique keys from current config
        current_tree = xmlutils.try_parse(xmlutils.userconfig_path)
        current_root = current_tree.getroot()
        existing_keys = {
            (e.findtext("sim"), e.findtext("model"), e.findtext("profile"))
            for e in current_root.findall("models")
        }

        # Show import dialog
        dlg = ProfileImportDialog(self)
        dlg.load_entries(imported_sim_defaults, imported_class_defaults, imported_sc_overrides, imported_models, existing_keys)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        actions, devices = dlg.get_import_actions()

        devices.add('any')
        # Apply actions
        for entry in actions:
            sim, model, _ = entry["key"]
            new_profile = entry["new_profile"]
            action = entry["action"]

            for elem in entry["elements"]:
                elem.find("profile").text = new_profile  # Rename if applicable

            if action == "skip":
                continue

            if action == "overwrite":
                for e in list(current_root.findall("models")):
                    e_sim = e.findtext("sim")
                    e_model = e.findtext("model")
                    e_profile = e.findtext("profile")
                    e_device = e.findtext("device")

                    if (e_sim, e_model, e_profile) != (sim, model, new_profile):
                        continue

                    if e_device not in devices:
                        continue  # Keep existing user entries for devices not being imported

                    current_root.remove(e)

            # Add new elements
            for elem in entry["elements"]:
                device = elem.findtext("device")
                if device not in devices:
                    continue  # skip if device was not selected in import dialog
                current_root.append(elem)

        # Add sc_overrides entries for imported models (with deduping)
        for entry in actions:
            model = entry["key"][1]
            action = entry["action"]

            if action == "skip":
                continue  # Skip models that weren't imported

            for sc_elem in imported_sc_overrides:
                if sc_elem.findtext("model") != model:
                    continue

                # De-dupe: remove any existing entry with same model + name
                sc_name = sc_elem.findtext("name")
                for existing in list(current_root.findall("sc_overrides")):
                    if existing.findtext("model") == model and existing.findtext("name") == sc_name:
                        current_root.remove(existing)

                current_root.append(sc_elem)

        # Save updated XML
        xmlutils.consolidate_sort_and_write_userconfig(current_tree)
        QMessageBox.information(self, "Import Complete", "Profiles were successfully imported.")
        self.start_tree_population()  # Refresh the tree

    def on_new_wizard_clicked(self):
        dlg = NewAircraftWizard(parent=self, manual=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.start_tree_population()

    def on_activate_clicked(self):
        selected = self.treeWidget.selectedItems()
        if len(selected) != 1:
            return

        item = selected[0]
        if not self.get_metadata(item, "active"):
            self.make_active_profile(item)

    def on_edit_clicked(self):
        selected = self.treeWidget.selectedItems()
        if len(selected) != 1:
            return
        item = selected[0]
        sim = self.get_metadata(item, 'sim_name')
        cls = self.get_metadata(item, 'cls_name')
        model = self.get_metadata(item, 'aircraft_name')
        profile = self.get_metadata(item, 'profile_name')
        G.main_window.load_single_offline_model(sim, cls, model, profile)
        self.hide()


    @pyqtSlot()
    def on_clone_clicked(self):
        items = [i for i in self.treeWidget.selectedItems() if i.childCount() == 0]
        if len(items) != 1:
            return

        item = items[0]
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        sim = meta["sim_name"]
        cls = meta["cls_name"]
        model = meta["aircraft_name"]
        profile_type = meta["type"]
        if profile_type == "default":
            old_profile = "default"
        else:
            old_profile = meta["profile_name"]

        dlg = NewProfileDialog(self, type='profile_mgr', sim=sim, cls=cls, model=model, profile=old_profile)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_profile, make_active, _, _ = dlg.get_data()
        if not new_profile:
            return

        xmlutils.clone_profile_entry(
            sim=sim,
            cls=cls,
            src_model=model,
            src_profile=old_profile,
            dst_profile=new_profile
        )


        if make_active:
            xmlutils.update_active_profile_entry(sim, cls, model, new_profile)
            # self.make_active_profile(item)  # UI toggle

        self.start_tree_population()
        QApplication.processEvents()
        QMessageBox.information(self, "Cloned", f"Profile '{old_profile}' cloned to '{new_profile}'")

    def sanitize_filename(self, name: str) -> str:
        """Remove illegal filename characters for Windows."""
        return re.sub(r'[<>:"/\\|?*]', '', name)

class NewProfileDialog(QDialog):
    def __init__(self, parent=None, type=None, sim=None, cls=None, model=None, profile=None):
        super().__init__(parent)
        self.setWindowTitle("New Profile")

        self.radio_new = QRadioButton("New Empty Profile")
        self.radio_clone = QRadioButton("Clone Existing Profile")

        self.radio_group = QButtonGroup(self)
        self.radio_group.addButton(self.radio_new)
        self.radio_group.addButton(self.radio_clone)
        self.radio_group.setExclusive(True)
        self.combo_clone = QComboBox(self)
        self.radio_group.buttonToggled.connect(self.on_radio_change)
        self.radio_new.setChecked(True)

        self.layout = QVBoxLayout(self)

        if type == 'profile_mgr':
            if not sim or not cls or not model:
                raise ValueError("sim, cls, model and profile are required for type 'profile_mgr'")
            self.setWindowTitle(f"Cloning profile '{profile}' for {model}")
            self.profiles = xmlutils.get_available_profiles(sim, cls, model)
            self.setMinimumWidth(400)
            self.radio_new.hide()
            self.radio_clone.hide()
            self.combo_clone.hide()

        # elif type != 'new':

        self.profiles = xmlutils.get_available_profiles(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern)
        if not len(self.profiles):
            self.combo_clone.setEnabled(False)
            self.radio_clone.setEnabled(False)
        p = []
        for profile in self.profiles:
            if profile.lower() != "default":
                p.append(profile)

        if not p:
            self.combo_clone.setEnabled(False)
            self.radio_clone.setEnabled(False)
        else:
            self.combo_clone.addItem('')
            for profile in p:
                self.combo_clone.addItem(profile)


        self.layout.addWidget(self.radio_new)
        self.layout.addWidget(self.radio_clone)
        self.layout.addWidget(self.combo_clone)

        self.label = QLabel("Enter new profile name:")
        self.layout.addWidget(self.label)

        self.lineEdit = QLineEdit()
        regex = QRegularExpression(r"^[ a-zA-Z0-9_\-()]+$")
        validator = QRegularExpressionValidator(regex)
        self.lineEdit.setValidator(validator)
        self.lineEdit.textChanged.connect(self.on_profile_name_changed)

        self.layout.addWidget(self.lineEdit)

        self.checkBox = QCheckBox("Make this the active profile")
        self.layout.addWidget(self.checkBox)

        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.ok_button.setEnabled(False)
        self.cancel_button = QPushButton("Cancel")
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        self.layout.addLayout(button_layout)

        self.ok_button.clicked.connect(self.validate_form)
        self.cancel_button.clicked.connect(self.reject)

    def validate_form(self):
        if self.radio_clone.isChecked():
            if not self.combo_clone.currentText():
                QMessageBox.warning(self, "Error", "Please select a profile to clone.")
                return
        self.accept()

    def on_radio_change(self):
        if self.radio_new.isChecked():
            self.combo_clone.setEnabled(False)
        elif self.radio_clone.isChecked():
            self.combo_clone.setEnabled(True)

    def on_profile_name_changed(self, txt):
        if txt.lower() == 'default':
            QMessageBox.critical(self, "Error", "'Default' is not a valid profile name.")
            self.ok_button.setEnabled(False)
            return
        if txt.lower() in (item.lower() for item in self.profiles):
            QMessageBox.critical(self, "Error", f"{txt} conflicts with an existing profile name for this aircraft.")
            self.ok_button.setEnabled(False)
            return
        if txt != '':
            self.ok_button.setEnabled(True)
    def get_data(self):
        """
        Returns:
            tuple[str, bool, bool]: (profile_name, make_active, clone_existing)
        """
        profile_name = self.lineEdit.text().strip()
        make_active = self.checkBox.isChecked()
        clone_existing = self.radio_clone.isChecked()
        to_clone = self.combo_clone.currentText() if self.radio_clone.isChecked() else None
        return profile_name, make_active, clone_existing, to_clone


class ExportOptionsDialog(QDialog):
    def __init__(self, selected_items, get_metadata, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Options")
        self.setMinimumWidth(400)

        self.original_items = selected_items
        self.get_metadata = get_metadata
        self.valid_items = [i for i in selected_items if get_metadata(i, "type") != "default"]
        self.removed_defaults = [i for i in selected_items if get_metadata(i, "type") == "default"]

        layout = QVBoxLayout(self)

        # Warning section for excluded default items
        if self.removed_defaults:
            warning_label = QLabel(
                "The following default profiles cannot be exported and will be excluded:"
            )
            warning_label.setStyleSheet("""
                QLabel {
                padding-left: 10px;
                padding-top: 2px;
                color: #dddddd; /* Softer red for dark mode */
                background-color: rgba(255, 50, 50, 30); /* Light red background tint */
                border: 1px solid #c33;
                border-radius: 4px;
            }
            """)
            layout.addWidget(warning_label)

            table_widget = QTableWidget()
            table_widget.setSortingEnabled(True)

            table_widget.setColumnCount(4)
            table_widget.setHorizontalHeaderLabels(["Aircraft", "Sim", "Class", "Profile"])
            table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            table_widget.setRowCount(len(self.removed_defaults))
            table_widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            table_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            table_widget.verticalHeader().setVisible(False)
            table_widget.horizontalHeader().setStretchLastSection(True)

            for row, item in enumerate(self.removed_defaults):
                table_widget.setItem(row, 0, QTableWidgetItem(item.text(0)))
                table_widget.setItem(row, 1, QTableWidgetItem(get_metadata(item, "sim_name") or "—"))
                table_widget.setItem(row, 2, QTableWidgetItem(get_metadata(item, "cls_name") or "—"))
                table_widget.setItem(row, 3, QTableWidgetItem(get_metadata(item, "profile_name") or "—"))

            table_widget.resizeColumnsToContents()
            layout.addWidget(table_widget)

        if self.valid_items:
            included_label = QLabel("The following profiles will be exported:")
            included_label.setStyleSheet("""
                font-weight: bold;
                margin-top: 8px;
            """)
            layout.addWidget(included_label)

            included_table = QTableWidget()
            included_table.setSortingEnabled(True)
            included_table.setColumnCount(4)
            included_table.setHorizontalHeaderLabels(["Aircraft", "Sim", "Class", "Profile"])
            included_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            included_table.setRowCount(len(self.valid_items))
            included_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            included_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            included_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            included_table.verticalHeader().setVisible(False)

            for row, item in enumerate(self.valid_items):
                included_table.setItem(row, 0, QTableWidgetItem(item.text(0)))
                included_table.setItem(row, 1, QTableWidgetItem(get_metadata(item, "sim_name") or "—"))
                included_table.setItem(row, 2, QTableWidgetItem(get_metadata(item, "cls_name") or "—"))
                included_table.setItem(row, 3, QTableWidgetItem(get_metadata(item, "profile_name") or "—"))

            included_table.resizeColumnsToContents()
            layout.addWidget(included_table)


        # Checkboxes
        layout.addWidget(QLabel("Override Options:"))
        self.checkbox_sim_defaults = QCheckBox("Include Applicable Default Sim Overrides")
        self.checkbox_sim_defaults.setChecked(True)
        self.checkbox_class_defaults = QCheckBox("Include Applicable Default Class Overrides")
        self.checkbox_class_defaults.setChecked(True)
        layout.addWidget(self.checkbox_sim_defaults)
        layout.addWidget(self.checkbox_class_defaults)

        # Radio Buttons for file export mode
        layout.addWidget(QLabel("Export Mode:"))
        self.radio_single_file = QRadioButton("Single Combined File")
        self.radio_multiple_files = QRadioButton("Multiple Files (per aircraft)")

        self.radio_group = QButtonGroup(self)
        self.radio_group.addButton(self.radio_single_file)
        self.radio_group.addButton(self.radio_multiple_files)
        self.radio_single_file.setChecked(True)
        if len(self.valid_items) == 1:
            self.radio_multiple_files.setEnabled(False)

        layout.addWidget(self.radio_single_file)
        layout.addWidget(self.radio_multiple_files)

        # Add device checkboxes if any launched instances are found
        self.device_checkboxes = []
        device_keys = list(set(G.launched_instances.keys()) | {G.device_type})
        if G.launched_instances.keys():
            layout.addWidget(QLabel("Include Settings For Devices (All active devices shown):"))
            for key in sorted(device_keys):
                checkbox = QCheckBox(key.capitalize())
                checkbox.setChecked(True)
                layout.addWidget(checkbox)
                self.device_checkboxes.append((key, checkbox))
        else:
            self.device_checkboxes = []  # no checkboxes to show

        # OK / Cancel Buttons
        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("Cancel")
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

    def get_data(self):
        """
        Returns:
            tuple[list, bool, bool, str, list[str]]: (filtered_items, export_sim_defaults, export_class_defaults, export_mode, selected_device_keys)
        """
        export_sim = self.checkbox_sim_defaults.isChecked()
        export_class = self.checkbox_class_defaults.isChecked()
        export_mode = 'combined' if self.radio_single_file.isChecked() else 'multiple'

        if self.device_checkboxes:
            selected_devices = [key for key, cb in self.device_checkboxes if cb.isChecked()]
        else:
            selected_devices = [G.device_type]

        return self.valid_items, export_sim, export_class, export_mode, selected_devices


class TreePopulationWorker(QObject):
    """
    Worker object responsible for populating aircraft tree data in the background.

    Emits:
        finished (list): Nested data structure representing sims, classes, and aircraft data.
    """
    finished = pyqtSignal(list)

    def __init__(self, show_defaults=False):
        super().__init__()
        self.show_defaults = show_defaults

    def get_enabled_sims(self):
        enabled_sims = []
        for sim in xmlutils.get_sims():
            if G.system_settings.get(f'enable{sim}', False):
                enabled_sims.append(sim)
        return sorted(enabled_sims)

    def run(self):
        """
        Background worker logic to collect all aircraft, profile, and activation metadata
        from XML sources, avoiding any direct UI interaction.
        """
        from telemffb import xmlutils
        import xml.etree.ElementTree as ET

        defaults_root = xmlutils.try_parse(xmlutils.defaults_path).getroot()
        users_root = xmlutils.try_parse(xmlutils.userconfig_path).getroot()
        enabled_sims = self.get_enabled_sims()

        result_data = []  # Will contain tree hierarchy: sims > classes > aircraft

        for sim in enabled_sims:
            sim_data = {"name": sim, "enabled": sim in enabled_sims, "classes": []}
            for cls in xmlutils.get_classes_for_sim(sim):
                cls_data = {"name": cls, "aircraft": []}

                # default profiles
                for aircraft, _ in xmlutils.read_user_models(sim, cls, default_only=True):
                    active = xmlutils.get_active_profile_for_model(sim, cls, aircraft) == 'default'
                    cls_data["aircraft"].append({
                        "aircraft": aircraft,
                        "source": "default",
                        "profile": "default",
                        "type": "default",
                        "active": active,
                        "show": True
                    })

                # User profiles
                for aircraft, profile_name in xmlutils.read_user_models(sim, cls):
                    active = xmlutils.get_active_profile_for_model(sim, cls, aircraft) == profile_name
                    cls_data["aircraft"].append({
                        "aircraft": aircraft,
                        "source": "User",
                        "profile": profile_name,
                        "type": "user",
                        "active": active,
                        "show": True
                    })

                sim_data["classes"].append(cls_data)
            result_data.append(sim_data)

        # Emit the final structured tree data
        self.finished.emit(result_data)