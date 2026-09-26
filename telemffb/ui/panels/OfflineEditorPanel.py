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

"""OfflineEditorPanel: the "Offline Editor Setup" group box - sim/class/
aircraft-name/profile selectors used to edit defaults.xml / userconfig_v2.xml
and preview effects without a live sim connected.

Replaces MainWindow's ~15 ``offline_*`` widgets (``offline_config_area``,
``offline_groupbox``, ``offline_sim``/``offline_class``/``offline_name``/
``offline_profile`` combos, ``offline_name_filter``, ``offline_scope_label``,
``back_to_profile_mgr_button``, ``exit_offline_button``) and their handler
methods (``offline_sim_changed`` and friends, ``force_sim_aircraft``,
``resize_offline_combos``, ``filter_offline_name_list``,
``load_single_offline_model``, ``back_to_profile_mgr``).

This panel owns the selector widgets and the cascade between them (picking a
sim repopulates classes, picking a class repopulates aircraft names, etc.)
and the two device-scoped globals that cascade drives (``G.settings_mgr``'s
``current_sim``/``current_class``/``current_aircraft_name``/``active_profile``/
``offline_scope``). What happens *outside* the panel as a result - reloading
the settings tab, refreshing the profile-notes button, opening the profile
manager dialog back up, deciding whether offline mode itself is entered or
exited - stays MainWindow's business, since it is the one with those other
panels and dialogs. Like ``telemffb.ui.widgets.SettingsLayout``, this panel
takes the owning ``MainWindow`` as ``mainwindow`` and calls its public
methods for that part (``self.mainwindow.settings_layout.reload_caller()``,
``self.mainwindow.toggle_offline_mode(...)``) rather than MainWindow reaching
back into this panel's widgets.

``toggle_offline_mode`` itself (the state machine deciding whether offline
mode is entered or exited at all - it also touches the status container and
settings tab, which this panel does not own) stays on MainWindow; this panel
only exposes ``reset_for_entry()`` for the widget-clearing part of it, and
``mirror_sim``/``mirror_class``/``mirror_aircraft``/``mirror_profile`` for
IPC to replicate the master's selection on a child instance without reaching
into the combo boxes directly.
"""

import json
import logging

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QSizePolicy, QVBoxLayout, QWidget)

import telemffb.globals as G
import telemffb.xmlutils as xmlutils
from telemffb.SettingsManager import SettingsManager


class OfflineEditorPanel(QWidget):
    """The "Offline Editor Setup" group box and the sim/class/aircraft/
    profile selection cascade behind it."""

    def __init__(self, parent=None, mainwindow=None):
        super().__init__(parent)
        self.mainwindow = mainwindow
        self.all_offline_models = []
        self._build_ui()

    def _build_ui(self):
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        offline_config_layout = QVBoxLayout()  # vertical layout to hold both rows
        # No default top margin, so the Offline Editor Setup frame's top
        # edge lines up with the Active Devices frame beside it.
        offline_config_layout.setContentsMargins(0, 0, 0, 0)

        # First row layout (existing widgets)
        # --- Create the Offline Editor GroupBox ---
        self.offline_groupbox = QGroupBox("Offline Editor Setup")
        self.offline_groupbox.setStyleSheet("""
            QGroupBox {

                font-weight: bold;
                border: 1px solid gray;
                border-radius: 5px;
                margin-top: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
        """)

        offline_layout = QVBoxLayout(self.offline_groupbox)
        offline_layout.setContentsMargins(10, 18, 10, 10)
        offline_layout.setSpacing(10)

        """ Create Offline controls layout """

        offline_grid_layout = QGridLayout()

        # --- Labels ---
        offline_sim_lbl = QLabel('Sim:')
        offline_sim_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        offline_class_lbl = QLabel('Class:')
        offline_class_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        offline_name_lbl = QLabel('Aircraft Name:')
        offline_name_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        offline_profile_lbl = QLabel('Profile:')
        offline_profile_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Create filter box
        self.offline_name_filter = QLineEdit()
        self.offline_name_filter.setPlaceholderText("Filter")
        self.offline_name_filter.setEnabled(False)
        self.offline_name_filter.textChanged.connect(self.filter_offline_name_list)

        """ Add label widgets to layout """

        offline_grid_layout.addWidget(offline_sim_lbl, 0, 0)
        offline_grid_layout.addWidget(offline_class_lbl, 0, 1)
        offline_grid_layout.addWidget(offline_name_lbl, 0, 2)
        offline_grid_layout.addWidget(self.offline_name_filter, 0, 3)
        offline_grid_layout.addWidget(offline_profile_lbl, 0, 4)

        """ Create Offline controls combo boxes """

        # --- ComboBoxes ---
        self.offline_sim = QComboBox()
        self._fill_sims()
        self.offline_sim.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.offline_sim.setMinimumContentsLength(10)
        self.offline_sim.setEditable(False)
        # index, not text: the visible text is the sim's label, the key is
        # the item data (see selected_sim)
        self.offline_sim.currentIndexChanged.connect(
            lambda _index: self.offline_sim_changed(self.selected_sim))

        self.offline_class = QComboBox()
        self.offline_class.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.offline_class.setMinimumContentsLength(15)
        self.offline_class.setEditable(False)
        self.offline_class.currentTextChanged.connect(self.offline_class_changed)

        self.offline_name = QComboBox()
        self.offline_name.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.offline_name.setMinimumContentsLength(20)
        self.offline_name.setEditable(False)
        self.offline_name.currentTextChanged.connect(self.offline_aircraft_changed)

        self.offline_profile = QComboBox()
        self.offline_profile.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.offline_profile.setMinimumContentsLength(15)
        self.offline_profile.setEditable(False)
        self.offline_profile.currentTextChanged.connect(self.offline_profile_changed)

        """ Add offline combo box controls to layout """

        offline_grid_layout.addWidget(self.offline_sim, 1, 0)
        offline_grid_layout.addWidget(self.offline_class, 1, 1)
        offline_grid_layout.addWidget(self.offline_name, 1, 2, 1, 2)
        offline_grid_layout.addWidget(self.offline_profile, 1, 4)

        # --- Column stretch ratios (1:2:4:2) ---
        offline_grid_layout.setColumnStretch(0, 1)
        offline_grid_layout.setColumnStretch(1, 2)
        offline_grid_layout.setColumnStretch(2, 4)
        offline_grid_layout.setColumnStretch(4, 2)

        offline_layout.addLayout(offline_grid_layout)

        """ Add layout for labels/buttons on bottom row of offline config area """

        bottom_row = QHBoxLayout()

        """ Create offline scope label """

        offline_scope = QLabel("<b>Offline Scope:   </b>")
        self.offline_scope_label = QLabel('None')

        """
        Create 'back to profile manager' button.  Only shows when edit is
        activated via profile manager
        """

        self.back_to_profile_mgr_button = QPushButton('Back to Profile Manager')
        self.back_to_profile_mgr_button.setVisible(False)
        self.back_to_profile_mgr_button.clicked.connect(self.back_to_profile_mgr)

        """ Create offline mode exit button """

        self.exit_offline_button = QPushButton()
        self.exit_offline_button.setText('Exit Offline Mode')
        self.exit_offline_button.clicked.connect(lambda: self.mainwindow.toggle_offline_mode(False))

        """ Add labels/buttons to bottom row layout """

        bottom_row.addWidget(offline_scope, alignment=Qt.AlignmentFlag.AlignLeft)
        bottom_row.addWidget(self.offline_scope_label, alignment=Qt.AlignmentFlag.AlignLeft)
        bottom_row.addStretch()
        bottom_row.addWidget(self.back_to_profile_mgr_button, alignment=Qt.AlignmentFlag.AlignRight)
        bottom_row.addWidget(self.exit_offline_button, alignment=Qt.AlignmentFlag.AlignRight)

        """ Add bottom row to layout """

        offline_layout.addLayout(bottom_row)

        """ Add items to layout """

        offline_config_layout.addWidget(self.offline_groupbox)
        # (The original MainWindow code also re-added offline_grid_layout and
        # bottom_row here with addLayout() - both already belong to
        # offline_layout, the groupbox's own layout, via the addLayout calls
        # above. A QLayout can only have one parent layout, so those two
        # calls were silent no-ops (Qt logs a warning and ignores them);
        # dropping them changes nothing observable.)

        """ Add layout to QWidget """

        self.setLayout(offline_config_layout)

        """ Hide Offline config area (gets shown when it is enabled) """

        self.hide()

    # ---- IPC mirroring (child instances replicate the master's selection) --

    def _fill_sims(self) -> None:
        """The sim combo: a blank entry, then every sim by label with its
        key as item data."""
        self.offline_sim.clear()
        self.offline_sim.addItem('', '')
        for sim in xmlutils.get_sims():
            self.offline_sim.addItem(SettingsManager.sim_label(sim), sim)

    @property
    def selected_sim(self) -> str:
        """The selected sim's key ("" when nothing is selected)."""
        return self.offline_sim.currentData() or ''

    def select_sim(self, sim: str) -> None:
        """Select a sim by key; an unknown key selects the blank entry."""
        self.offline_sim.setCurrentIndex(max(0, self.offline_sim.findData(sim)))

    def mirror_sim(self, sim: str) -> None:
        """Replicate the master's 'Sim' selection - child instances only,
        driven by IPCNetworkThread.set_offline_sim_signal."""
        self.select_sim(sim)

    def mirror_class(self, class_name: str) -> None:
        """Replicate the master's 'Class' selection - child instances only."""
        self.offline_class.setCurrentText(class_name)

    def mirror_aircraft(self, name: str) -> None:
        """Replicate the master's 'Aircraft Name' selection - child
        instances only."""
        self.offline_name.setCurrentText(name)

    def mirror_profile(self, profile: str) -> None:
        """Replicate the master's 'Profile' selection - child instances
        only."""
        self.offline_profile.setCurrentText(profile)

    # ---- entering/exiting offline mode (widget part only; MainWindow's
    #      toggle_offline_mode decides whether to enter/exit at all) --------

    def reset_for_entry(self):
        """Clear the combo boxes and repopulate the sim list - called by
        MainWindow.toggle_offline_mode when entering offline editing mode
        with nothing pre-selected yet."""
        # Block signals so we don't trigger text change on .clear() calls
        self.offline_name.blockSignals(True)
        self.offline_class.blockSignals(True)
        self.offline_profile.blockSignals(True)

        # clear contents of combo boxes so they can be repopulated.  The
        # profile is cleared here too: offline_sim.clear() only reaches
        # offline_sim_changed('') (which would clear it) when a sim was
        # selected, so a stale profile would otherwise survive re-entry
        self.offline_name.clear()
        self.offline_class.clear()
        self.offline_profile.clear()
        self.offline_sim.clear()

        # unblock signals
        self.offline_name.blockSignals(False)
        self.offline_class.blockSignals(False)
        self.offline_profile.blockSignals(False)

        self._fill_sims()

    def back_to_profile_mgr(self):
        self.back_to_profile_mgr_button.setVisible(False)
        try:
            # in case it somehow got closed
            self.mainwindow.profile_mgr_dialog.show()
        except (RuntimeError, AttributeError):
            # RuntimeError: underlying Qt dialog was deleted; AttributeError:
            # profile_mgr_dialog not created yet.
            logging.exception("Failed to re-show profile manager dialog")
            QMessageBox.warning(self.mainwindow, "Profile Manager", "IDK WHY THIS ERROR HAPPENED")
        self.mainwindow.toggle_offline_mode(False)

    @pyqtSlot(str, str, str, str)
    def load_single_offline_model(self, sim, cls, model, profile, from_profile_manager=True):

        # Not broadcast: SHOW_OFFLINE_MODEL at the end of this method has each child
        # run this same method, which takes it offline and applies the selection in
        # one step.  An earlier TOGGLE OFFLINE would leave the children offline with
        # nothing selected for as long as the combos below take to fill.
        self.mainwindow.toggle_offline_mode(True, broadcast=False)
        combo_boxes = {self.offline_sim, self.offline_class, self.offline_name, self.offline_profile}
        for cb in combo_boxes:
            cb.blockSignals(True)
            cb.clear()
            cb.addItem('')

        try:
            for s in xmlutils.get_sims():
                self.offline_sim.addItem(SettingsManager.sim_label(s), s)
            self.select_sim(sim)

            cls_list = xmlutils.get_classes_for_sim(sim)
            for c in cls_list:
                self.offline_class.addItem(c)
            self.offline_class.setCurrentText(cls)

            model_list = xmlutils.read_models(sim, cls)
            self.all_offline_models = model_list
            self.filter_offline_name_list(self.offline_name_filter.text())
            self.offline_name.setCurrentText(model)

            profile_list = xmlutils.get_available_profiles(sim, cls, model)
            self.offline_profile.clear()
            for p in profile_list:
                if p != 'Built-In':
                    self.offline_profile.addItem(p)
            if not self.offline_profile.count() and model:
                # Built-In cannot be edited: with no user profile the editor works on
                # Auto User, which the first change creates (as offline_aircraft_changed)
                self.offline_profile.addItem('Auto User')
            self.offline_profile.setCurrentText(profile)
            self.offline_profile_changed(self.offline_profile.currentText())
        finally:
            for cb in combo_boxes:
                cb.blockSignals(False)

        if model:
            G.settings_mgr.offline_scope = 'MODEL'
        else:
            # an aircraft with class-level settings only: edit those
            G.settings_mgr.offline_scope = 'CLASS'
            self.offline_scope_label.setText(f"Editing Class Defaults ({cls})")

        self.force_sim_aircraft()
        if G.master_instance:
            self.back_to_profile_mgr_button.setVisible(from_profile_manager)
            args = [sim, cls, model, profile]
            G.ipc_instance.send_broadcast_message(f"SHOW_OFFLINE_MODEL:{json.dumps(args)} ")
            self.resize_offline_combos()

    # ---- selection cascade -------------------------------------------------

    def resize_offline_combos(self):
        """
            Dynamically resizes the minimum width of all offline mode combo boxes
            based on the widest item in each. Adds 50 pixels padding to ensure space.

            This ensures no items are truncated in display and helps with layout alignment.
            """
        for combo in [self.offline_sim, self.offline_class, self.offline_name, self.offline_profile]:
            metrics = QFontMetrics(combo.font())
            max_width = 0

            for i in range(combo.count()):
                text = combo.itemText(i)
                width = metrics.horizontalAdvance(text)
                max_width = max(max_width, width)

            # Add 2 pixels for spacing and set minimum width
            combo.setMinimumWidth(max_width + 50)

        self.mainwindow.settings_layout.reload_caller()

    def offline_sim_changed(self, sim=None):
        """
            Triggered when the offline 'Sim' combo box changes.

            Updates all related combo boxes (class, aircraft, profile),
            sets the configuration scope, and broadcasts the change
            if in master mode.

            Args:
                sim (str, optional): The selected simulation name. If None or empty,
                                     resets the offline editing UI.
            """
        self.offline_name.blockSignals(True)
        self.offline_name_filter.blockSignals(True)
        self.offline_class.blockSignals(True)
        self.offline_name.clear()
        self.offline_name_filter.clear()
        self.offline_class.clear()
        self.offline_name.blockSignals(False)
        self.offline_name_filter.blockSignals(False)
        self.offline_class.blockSignals(False)
        if sim is None or sim == '':
            # if sim combobox is cleared, reset everything and clear the layout
            self.offline_class.clear()  # clear class field
            self.offline_name.clear()
            self.offline_profile.clear()
            self.mainwindow.settings_layout.clear_layout()
            self.offline_scope_label.setText(f"None")
            self.offline_name_filter.setEnabled(False)
            return
        self.offline_name_filter.setEnabled(True)
        self.offline_class.clear()  #clear class field
        self.offline_class.addItem('')
        self.offline_name.clear()
        #self.offline_name.setMaximumWidth(200)
        self.offline_profile.clear()
        classes = xmlutils.get_classes_for_sim(sim)  # get classes based on chosen sim

        for class_name in classes:
            self.offline_class.addItem(class_name)  #populate class combobox based on results

        self.offline_name.clear()  #clear aircraft selection combobox

        model_list = xmlutils.read_models(sim)
        self.all_offline_models = model_list
        self.filter_offline_name_list(self.offline_name_filter.text())

        if G.master_instance:
            # send to child instances to mimic action
            G.ipc_instance.send_broadcast_message(f"OFFLINE_SIM:{self.selected_sim}")

        G.settings_mgr.offline_scope = 'SIM'  # set config scope to SIM
        self.offline_scope_label.setText(f"Editing SIM Defaults ({sim})")

        self.resize_offline_combos()
        self.force_sim_aircraft() # load settings based on sim

    def offline_class_changed(self, class_name):
        self.offline_name_filter.blockSignals(True)
        self.offline_name_filter.clear()
        self.offline_name_filter.blockSignals(False)
        model_list = xmlutils.read_models(self.selected_sim, class_name)  # get all available models based on sim and class
        self.all_offline_models = model_list
        self.offline_name.clear()  # clear the aircraft selection combobox
        self.offline_profile.clear()
        self.filter_offline_name_list(self.offline_name_filter.text())

        if G.master_instance:
            # send to child instances to mimic action
            G.ipc_instance.send_broadcast_message(f"OFFLINE_CLASS:{self.offline_class.currentText()}")
        if class_name == '':
            # reset back to sim mode if class field is cleared
            self.offline_sim_changed(self.selected_sim)
        else:
            G.settings_mgr.offline_scope = 'CLASS' # set config scope to CLASS
            self.offline_scope_label.setText(f"Editing Class Defaults ({class_name})")

        self.resize_offline_combos()
        self.force_sim_aircraft() # load settings based on class and currently selected sim

    def offline_aircraft_changed(self, ac_name=None):
        cfg, cls = G.telem_manager.get_aircraft_config(ac_name, self.selected_sim) # get class based on selected aircraft
        profiles = xmlutils.get_available_profiles(self.selected_sim, self.offline_class.currentText(), ac_name)
        self.offline_profile.setEnabled(True)
        self.offline_profile.clear()

        for profile_name in profiles:
            if profile_name != 'Built-In':
                self.offline_profile.addItem(profile_name)

        if not self.offline_profile.count() and ac_name:
            self.offline_profile.addItem('Auto User')  # manually add 'Auto User' so it is at the top and always present even if there is not yet a Auto User Profile
            xmlutils.update_active_profile_entry(sim=self.selected_sim, cls=cls, model=ac_name, new_profile="Auto User")
        self.offline_class.blockSignals(True)  # block signals to prevent triggering of offline_class_changed
        self.offline_class.setCurrentText(cls) # set class combobox to learned class from aircraft config
        self.offline_class.blockSignals(False)  # unblock signals

        if ac_name == '':
            self.offline_class_changed(self.offline_class.currentText())
        else:
            G.settings_mgr.offline_scope = 'MODEL'
            self.offline_scope_label.setText(f"Editing Aircraft ({ac_name} - {self.offline_profile.currentText()})")

        if G.master_instance:
            G.ipc_instance.send_broadcast_message(f'OFFLINE_AC:{self.offline_name.currentText()}')

        self.resize_offline_combos()
        self.force_sim_aircraft()

    def offline_profile_changed(self, profile):
        if not profile:
            return
        G.settings_mgr.offline_scope = 'MODEL'
        self.resize_offline_combos()
        self.force_sim_aircraft()
        if G.master_instance:
            # send to child instances to mimic action
            G.ipc_instance.send_broadcast_message(f"OFFLINE_PROFILE:{profile}")
        self.offline_scope_label.setText(f"Editing Aircraft ({self.offline_name.currentText()} - {profile})")

    def filter_offline_name_list(self, text):
        self.offline_name.blockSignals(True)
        self.offline_name.clear()
        self.offline_profile.blockSignals(True)
        self.offline_profile.clear()
        self.offline_name.addItems([''])
        filtered = [name for name in self.all_offline_models if name and text.lower() in name.lower()]
        self.offline_name.addItems(filtered)
        if len(filtered) == 1:
            self.offline_name.setCurrentIndex(1)
            # Manually trigger the downstream handler
            self.offline_aircraft_changed(filtered[0])
        self.offline_name.blockSignals(False)
        self.offline_profile.blockSignals(False)

    def force_sim_aircraft(self):
        G.settings_mgr.current_sim = self.selected_sim
        G.settings_mgr.current_class = self.offline_class.currentText()
        G.settings_mgr.current_aircraft_name = self.offline_name.currentText()
        G.settings_mgr.active_profile = self.offline_profile.currentText()
        self.mainwindow.settings_layout.reload_caller()
        # reload_caller resolves current_pattern; refresh the notes button for
        # the newly selected offline scope (no telemetry loop runs it here)
        self.mainwindow.refresh_profile_notes_button()
