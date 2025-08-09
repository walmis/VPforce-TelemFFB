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
from telemffb import utils
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
        self.first_init = True
        self.current_ac = G.settings_mgr.current_pattern
        self.setWindowTitle(f"Profile Manager - Active Aircraft: {self.current_ac}")
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

        self.tb_aircraft_filter.textChanged.connect(self.apply_all_filters)
        self.tb_profile_filter.textChanged.connect(self.apply_all_filters)

        self.filterButtonGroup.buttonToggled.connect(lambda b, c: self.apply_all_filters() if c else None)

        self.pb_clear_filters.clicked.connect(self.clear_text_filters)
        self.pb_Import.clicked.connect(self.import_profiles)
        self.pb_Clone.clicked.connect(self.on_clone_clicked)
        self.pb_Activate.clicked.connect(self.on_activate_clicked)
        self.pb_Edit.clicked.connect(self.on_edit_clicked)
        self.pb_Rename.clicked.connect(self.on_rename_clicked)

        self.pb_newAircraft.clicked.connect(self.on_new_wizard_clicked)


        # self.cb_showDefaults.toggled.connect(self.toggle_show_defaults)

        self.treeWidget.setStyleSheet("""
        QTreeWidget::item {
            padding-right: 12px;
        }
        """)
        self.pb_Delete.clicked.connect(self.on_delete_clicked)
        self.pb_Exit.clicked.connect(self.close)
        self.pb_expandButton.clicked.connect(self.toggle_tree_expansion)

        self.treeWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeWidget.customContextMenuRequested.connect(self.open_context_menu)

        self.cb_active.toggled.connect(self.apply_all_filters)
        self.cb_inactive.toggled.connect(self.apply_all_filters)

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

        if self.first_init:
            if self.current_ac:
                """If this is the first time the tree population is done (on new window show), default
                to the current aircraft view if one is active"""
                self.rb_showCurrentAircraft.setChecked(True)
            else:
                self.rb_showAll.setChecked(True)

            self.first_init = False
        else:
            self.reapply_active_radio_button()

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
        self.pb_Rename.setEnabled(False)
        self.pb_Edit.setEnabled(False)
        self.pb_Activate.setEnabled(False)

        self.pb_Export.clicked.connect(self.export_selected_profile)

    def reapply_active_radio_button(self):
        """Programmatically re-triggers the currently selected radio button's toggled logic."""
        button = self.filterButtonGroup.checkedButton()
        if button is not None:
            # Temporarily uncheck and recheck to fire the signal
            button.setChecked(False)
            button.setChecked(True)

    def apply_all_filters(self):
        """Unified filter pipeline: radio mode, active/inactive state, and search fields."""
        radio_button = self.filterButtonGroup.checkedButton()
        show_mode = (
            "all" if radio_button == self.rb_showAll else
            "defaults" if radio_button == self.rb_showDefaults else
            "user" if radio_button == self.rb_showUser else
            "current_ac"
        )

        show_active = self.cb_active.isChecked()
        show_inactive = self.cb_inactive.isChecked()
        aircraft_query = self.tb_aircraft_filter.text().lower()
        profile_query = self.tb_profile_filter.text().lower()

        def should_show(item):
            # Skip non-leaf items
            if item.childCount() > 0:
                return True

            meta = self.get_metadata(item, "type")
            sim = self.get_metadata(item, "sim_name")
            cls = self.get_metadata(item, "cls_name")
            ac = self.get_metadata(item, "aircraft_name")
            item_type = self.get_metadata(item, "type")
            active = self.get_metadata(item, "active")

            # 1. Radio mode logic
            if show_mode == "defaults" and item_type != "default":
                return False
            elif show_mode == "user" and item_type != "user":
                return False
            elif show_mode == "current_ac":
                if sim != G.settings_mgr.current_sim:
                    return False
                if cls != G.settings_mgr.current_class:
                    return False
                if ac != G.settings_mgr.current_pattern:
                    return False

            # 2. Active/Inactive checkbox logic
            if not ((active and show_active) or (not active and show_inactive)):
                return False

            # 3. Text filters
            ac_text = item.text(0).lower()
            profile_text = item.text(2).lower()
            if aircraft_query not in ac_text or profile_query not in profile_text:
                return False

            return True

        def recurse(item):
            visible_children = 0
            for i in range(item.childCount()):
                child = item.child(i)
                if child.childCount() == 0:
                    child.setHidden(not should_show(child))
                    if not child.isHidden():
                        visible_children += 1
                else:
                    recurse(child)
                    if not child.isHidden():
                        visible_children += 1
            item.setHidden(visible_children == 0)

        for i in range(self.treeWidget.topLevelItemCount()):
            recurse(self.treeWidget.topLevelItem(i))

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
            delete_action.triggered.connect(self.on_delete_clicked)
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
                self.pb_Rename.setEnabled(False)
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
            self.pb_Rename.setEnabled(False)
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

    # def apply_radio_filter(self):
    #     button = self.filterButtonGroup.checkedButton()
    #
    #     if button == self.rb_showAll:
    #         self.clear_sim_class_model_filter()
    #     elif button == self.rb_showDefaults:
    #         self.clear_sim_class_model_filter()
    #         self.toggle_show_defaults(True)
    #     elif button == self.rb_showUser:
    #         self.clear_sim_class_model_filter()
    #         self.filter_active_profiles(True)
    #     elif button == self.rb_showCurrentAircraft:
    #         self.toggle_show_defaults(False)
    #         self.filter_by_sim_class_model(
    #             G.settings_mgr.current_sim,
    #             G.settings_mgr.current_class,
    #             G.settings_mgr.current_pattern
    #         )

    # def clear_sim_class_model_filter(self):
    #     for i in range(self.treeWidget.topLevelItemCount()):
    #         sim_item = self.treeWidget.topLevelItem(i)
    #         sim_item.setHidden(False)
    #         for j in range(sim_item.childCount()):
    #             cls_item = sim_item.child(j)
    #             cls_item.setHidden(False)
    #             for k in range(cls_item.childCount()):
    #                 ac_item = cls_item.child(k)
    #                 ac_item.setHidden(False)
    #
    #     # Re-apply whatever the currently selected radio button is
    #     active_button = self.filterButtonGroup.checkedButton()
    #     if active_button == self.rb_showDefaults:
    #         self.toggle_show_defaults(True)
    #     elif active_button == self.rb_showUser:
    #         self.filter_active_profiles(True)
    #     elif active_button == self.rb_showCurrentAircraft:
    #         self.filter_by_sim_class_model(
    #             G.settings_mgr.current_sim,
    #             G.settings_mgr.current_class,
    #             G.settings_mgr.current_pattern
    #         )

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

    # def toggle_show_defaults(self, checked):
    #     """
    #     If checked, only show default profiles. All user profiles are hidden.
    #     If unchecked, visibility is not altered.
    #     """
    #     for i in range(self.treeWidget.topLevelItemCount()):
    #         sim_item = self.treeWidget.topLevelItem(i)
    #         for j in range(sim_item.childCount()):
    #             cls_item = sim_item.child(j)
    #
    #             for k in range(cls_item.childCount()):
    #                 item = cls_item.child(k)
    #                 item_type = self.get_metadata(item, "type")
    #                 is_active = self.get_metadata(item, "active")
    #
    #                 if checked:
    #                     # Show only default profiles
    #                     if item_type == "default":
    #                         item.setHidden(False)
    #                     else:
    #                         item.setHidden(True)
    #                 else:
    #                     # Reset visibility (handled elsewhere)
    #                     item.setHidden(False)
    #
    #     self.prune_empty_tree_items(self.treeWidget)

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

            root = ET.Element("TelemFFB_v2")
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
        root = ET.Element("TelemFFB_v2")
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
        export_root = ET.Element("TelemFFB_v2")
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
        root = tree.getroot()
        if root.tag != 'TelemFFB_v2':
            QMessageBox.critical(self, "Invalid File", f"Importation is only supported for files generated by TelemFFB v2 versions")
            return
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


    def on_delete_clicked(self):
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
                    QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
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
                G.main_window.update_settings()
                return

            # Ask which profile should become active
            options = [self.get_metadata(s, "profile_name") for s in user_siblings]
            has_default = any(self.get_metadata(s, "type") == "default" for s in siblings)
            if has_default:
                options.insert(0, "Built-In")

            new_active, ok = QInputDialog.getItem(
                self, "Choose Replacement Active Profile",
                f"'{profile}' is active. Choose replacement active profile for '{model}':",
                options, 0, False
            )
            if not ok:
                return

            # Make selected replacement profile active
            for sib in siblings:
                if (new_active == "Built-In" and self.get_metadata(sib, "type") == "default") or \
                        self.get_metadata(sib, "profile_name") == new_active:
                    self.make_active_profile(sib)
                    break

            # Delete original
            xmlutils.erase_model_profile(sim, model, profile)
            cls_item.removeChild(active_item)
            self.prune_empty_tree_items(self.treeWidget)
            G.main_window.update_settings()
            QMessageBox.information(self, "Deleted", f"Deleted profile '{profile}' for '{model}'.")
            return

        # Handle standard deletions
        names = "\n".join(f"{i.text(0)} ({i.text(2)})" for i in items)
        resp = QMessageBox.question(
            self, "Confirm Deletion",
            f"Delete the following profiles?\n\n{names}",
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
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
        G.main_window.update_settings()
        QMessageBox.information(self, "Deleted", f"Deleted {len(items)} profile(s).")

    @pyqtSlot()
    def on_rename_clicked(self):
        selected = [i for i in self.treeWidget.selectedItems() if i.childCount() == 0]
        if len(selected) != 1:
            return

        item = selected[0]
        # safety checks (should already be enforced by button enable logic)
        if self.get_metadata(item, "type") != "user":
            return
        current_name = self.get_metadata(item, "profile_name") or ""
        if current_name.lower() == "user default":
            return

        sim = self.get_metadata(item, "sim_name")
        cls = self.get_metadata(item, "cls_name")
        model = self.get_metadata(item, "aircraft_name")
        is_active = bool(self.get_metadata(item, "active"))

        # profiles for this aircraft to check duplicates
        existing_profiles = xmlutils.get_available_profiles(sim, cls, model)

        dlg = RenameProfileDialog(self, existing_profiles, current_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_name = dlg.value()
        if not new_name or new_name == current_name:
            return

        # try:
        xmlutils.rename_profile(sim, cls, model, current_name, new_name)

        # if this profile was active, keep it active under the new name
        if is_active:
            xmlutils.update_active_profile_entry(sim, cls, model, new_name)

        # refresh
        self.start_tree_population()
        QApplication.processEvents()
        QMessageBox.information(self, "Renamed",
                                f"Profile '{current_name}' renamed to '{new_name}'.")
        # except Exception as e:
        #     QMessageBox.critical(self, "Rename Failed", f"Could not rename profile:\n{e}")

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
            old_profile = "Built-In"
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
        else:
            self.profiles = xmlutils.get_available_profiles(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern)

        if not len(self.profiles):
            self.combo_clone.setEnabled(False)
            self.radio_clone.setEnabled(False)
        p = []
        for profile in self.profiles:
            if profile.lower() != "built-in":
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

        self.layout.addWidget(self.lineEdit)

        # Inline error label (initially hidden)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("""
            QLabel {
                color: #ff4444;
                background-color: rgba(255, 80, 80, 30%);
                border: 1px solid #ff4444;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
            }
        """)
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        self.layout.addWidget(self.error_label)

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
        name = txt.lower().strip()
        reserved = ['built-in', 'built_in', 'builtin', 'built in', 'user default', 'user-default', 'user_default', 'userdefault', 'auto user', 'autouser', 'auto-user', 'auto_user', 'default']

        if name in reserved:
            self.error_label.setText(f"'{name}' is a reserved profile name.")
            self.error_label.setVisible(True)
            self.ok_button.setEnabled(False)
            return

        # print(f"name: {name}, profiles: {self.profiles}")
        if name in (item.lower() for item in self.profiles):
            self.error_label.setText(f"'{txt}' conflicts with an existing profile name for this aircraft.")
            self.error_label.setVisible(True)
            self.ok_button.setEnabled(False)
            return
        if name != '':
            self.error_label.hide()
            self.error_label.clear()
            self.ok_button.setEnabled(True)
            self.adjustSize()

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

class RenameProfileDialog(QDialog):
    def __init__(self, parent, existing_names, current_name):
        super().__init__(parent)
        self.setWindowTitle(f"Rename Profile '{current_name}'")
        self.setModal(True)
        self.setMinimumWidth(320)

        self._existing = {n.lower() for n in existing_names if n}

        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("New profile name:"))
        self.le = QLineEdit(current_name, self)
        # same character policy you used before
        rx = QRegularExpression(r"^[ a-zA-Z0-9_\-()]+$")
        self.le.setValidator(QRegularExpressionValidator(rx, self))
        lay.addWidget(self.le)

        # inline error (theme-aware)
        self.err = QLabel("", self)
        self.err.setWordWrap(True)
        err_retain = self.err.sizePolicy()
        err_retain.setRetainSizeWhenHidden(True)
        self.err.setSizePolicy(err_retain)
        self.err.hide()
        if G.useDarkMode:
            fg = "#FFE3E3"  # soft light red/pink (good contrast on dark)
            bg = "rgba(255, 99, 99, 0.16)"  # faint red tint
            bd = "#FF6B6B"  # border a bit brighter
        else:
            fg = "#B00020"  # material-ish error red
            bg = "rgba(176, 0, 32, 0.10)"  # faint tint
            bd = "rgba(176, 0, 32, 0.35)"

        self.err.setStyleSheet(f"""
                QLabel {{
                    color: {fg};
                    background-color: {bg};
                    border: 1px solid {bd};
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-weight: 600;
                }}
            """)

        lay.addWidget(self.err)

        row = QHBoxLayout()
        self.ok = QPushButton("OK", self)
        self.ok.setEnabled(False)
        cancel = QPushButton("Cancel", self)
        row.addStretch(1)
        row.addWidget(self.ok)
        row.addWidget(cancel)
        lay.addLayout(row)

        # signals
        self.le.textChanged.connect(self._validate)
        self.ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        # run initial validation
        self._validate(self.le.text())

    def _validate(self, txt: str):
        name = txt.strip()
        if not name:
            self.err.setText('')
            self.err.hide()
            self.ok.setEnabled(False)
            return
        reserved = ['built-in', 'built_in', 'builtin', 'built in', 'user default', 'user-default','user_default', 'userdefault', 'auto user','autouser','auto-user','auto_user', 'default']
        if name.lower() in reserved:
            self.err.setText(f"'{name}' is a reserved profile name.")
            self.err.show()
            self.ok.setEnabled(False)
            return

        if name.lower() in self._existing:
            self.err.setText(f"'{name}' already exists. Choose a different name.")
            self.err.show()
            self.ok.setEnabled(False)
            return

        # validator result
        st = self.le.validator().validate(name, 0)[0]
        if st != QRegularExpressionValidator.State.Acceptable:
            self.err.setText("Only letters, numbers, spaces, _ - ( ) are allowed.")
            self.err.show()
            self.ok.setEnabled(False)
            return

        self.err.hide()
        self.err.clear()
        self.ok.setEnabled(True)

    def value(self) -> str:
        return self.le.text().strip()

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
                    active = xmlutils.get_active_profile_for_model(sim, cls, aircraft) == 'Built-In'
                    cls_data["aircraft"].append({
                        "aircraft": aircraft,
                        "source": "default",
                        "profile": "Built-In",
                        "type": "default",
                        "active": active,
                        "show": True
                    })
                    # print(f"Default: {aircraft} is {active}")

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
                    # print(f"User: {aircraft} {profile_name} ta={ta} is {active}")


                sim_data["classes"].append(cls_data)
            result_data.append(sim_data)

        # Emit the final structured tree data
        self.finished.emit(result_data)