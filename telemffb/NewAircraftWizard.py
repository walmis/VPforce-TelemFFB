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

import re

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QRegularExpressionValidator, QFont
from PyQt6.QtWidgets import QButtonGroup, QDialog, QFileDialog, QMessageBox, QSizePolicy, QStyle, QComboBox

from . import globals as G
from . import utils
from. import xmlutils
from .ui.Ui_NewAircraftWizard import Ui_NewAircraftWizard

class NewAircraftWizard(QDialog, Ui_NewAircraftWizard):
    """
        A QDialog-based multi-page wizard for adding a new aircraft profile
        to the TelemFFB system. Supports both manual entry and auto-discovered
        configurations.

        The wizard guides users through:
        1. Selecting a simulation platform
        2. Selecting an aircraft class
        3. Defining a match pattern and optional profile clone source

        Attributes:
            manual_mode (bool): Whether the wizard is in manual input mode.
            auto_sim (str): Auto-detected simulation platform (if any).
            auto_cls (str): Auto-detected aircraft class (if any).
            auto_name (str): Auto-detected aircraft name (if any).
            selected_sim (str): Internal name of selected sim.
            selected_class (str): Internal name of selected aircraft class.
            aircraft_list (list): List of available aircraft names for selected class.
            class_list (list): Available classes for selected sim.
            sim_list (list): Available simulation platforms.
        """


    friendly_sim_names = {  # Build list to get friendly names from internal names
        "DCS": "DCS World",
        "BMS": "Falcon BMS",
        "MSFS": "Microsoft Flight Simulator 20/24",
        "XPLANE": "X-Plane 11/12",
        "IL2": "IL-2 Sturmovik"
    }
    internal_sim_names = {v: k for k, v in friendly_sim_names.items()}  # Build reverse lookup table

    friendly_class_names = {
        "PropellerAircraft": "Propeller/Piston Aircraft",
        "TurbopropAircraft": "Turboprop Aircraft",
        "JetAircraft": "Jet Powered Aircraft",
        "GliderAircraft": "Glider",
        "Helicopter": "Helicopter",
        "CowanSimHelicopter": "CowanSim Helicopter",
        "FlyInsideHelicopter": "FlyInside Helicopter",
        "HPGHelicopter": "Hype Group Airbus Helicopter",
        "SASHelicopter": "SimFocus SAS Helicopter",
        "TaogH500Helicopter": "Taog H500/OH6A Helicopter",
        "XAW109Helicopter": "X-Trident AW109 Helicopter"
    }
    internal_class_names = {v: k for k, v in friendly_class_names.items()}  # Build reverse lookup table

    mandatory_clone_types = ("HPGHelicopter", "SASHelicopter", "FlyInsideHelicopter", "TaogH500Helicopter", "XAW109Helicopter") # Aircraft types with special treatment that must be cloned from a default profile
    mandatory_clone: bool=False
    aircraft_list: list=None
    class_list: list=None
    sim_list: list=None

    def __init__(self, parent=None, manual=False, auto_sim=None, auto_name=None, auto_cls=None):
        """
            Initialize the wizard.

            Args:
                parent (QWidget): Parent widget
                manual (bool): Whether the wizard is being run in manual entry mode
                auto_sim (str): Simulation name (used when manual=False)
                auto_name (str): Aircraft name (used when manual=False)
                auto_cls (str): Aircraft class (optional, used when manual=False)
            """
        if not manual and (auto_sim is None or auto_name is None):
            raise ValueError("Parameter 'auto_sim' and auto_name are required when 'manual' is False")
        super(NewAircraftWizard, self).__init__(parent)
        self.parent = parent
        self.manual_mode = manual
        self.profile_list: list = None
        self.current_match_string: str = ''
        self.auto_sim = auto_sim
        self.auto_cls = auto_cls
        self.auto_name = auto_name
        self.setupUi(self)
        self.retranslateUi(self)

        self.stackedWidget.setCurrentIndex(0) # Force at start page
        self.stackedWidget.currentChanged.connect(self.manage_pages)

        self.pb_next.clicked.connect(self.next_button_clicked)
        self.pb_previous.clicked.connect(self.prev_button_clicked)
        self.pb_finish.clicked.connect(self.finish_button_clicked)
        self.pb_cancel.clicked.connect(self.reject)

        # self.sim_list = self.get_sims()
        self.cb_sim.currentTextChanged.connect(self.sim_changed)
        self.cb_class.currentTextChanged.connect(self.class_changed)
        self.tb_manual_match_string.textChanged.connect(self.validate_ac_page_entries)
        self.tb_manual_full_name.textChanged.connect(self.full_name_text_changed)
        self.bg_matchString.buttonToggled.connect(self.suggested_manual_toggled)
        # self.rb_suggested.toggled.connect(self.suggested_selected)
        # self.rb_manual.toggled.connect(self.validate_ac_page_entries)
        self.cb_clone.currentTextChanged.connect(self.validate_ac_page_entries)
        self.tb_profileName.textChanged.connect(self.validate_profile_text)

        self.lbl_autodiscovery.setVisible(False)
        self.lbl_actype_discovery.setVisible(False)
        self.lbl_profileNameError.setVisible(False)
        # self.pb_cancel.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton))
        # self.pb_next.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        # self.pb_previous.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        # self.pb_finish.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        regex = QRegularExpression(r'^[^<>:"/\\|?*\x00-\x1F]*$')
        profile_validator = QRegularExpressionValidator(regex)
        self.tb_manual_full_name.setValidator(profile_validator)
        self.init_state()

    def init_state(self):
        self.pb_next.setEnabled(False)
        self.pb_previous.setEnabled(False)
        self.pb_previous.setVisible(False)
        self.sim_list = self.get_sims()
        self.cb_sim.clear()
        self.cb_sim.addItem('')
        for sim in self.sim_list:
            self.cb_sim.addItem(self.friendly_sim_names.get(sim))

        if self.manual_mode:
            # self.rb_suggested.setDisabled(True)
            # self.cb_suggested.setDisabled(True)
            # self.rb_manual.setChecked(True)
            self.clear_match_toggles()
            self.cb_clone.setEnabled(False)
            # self.lbl_autodiscovery.setVisible(True)
        else:
            self.setWindowTitle(f"New Aircraft Wizard - {self.auto_sim} {self.auto_name}")
            self.rb_suggested.setChecked(True)
            self.lbl_autodiscovery.setVisible(False)
            self.cb_sim.setCurrentText(self.friendly_sim_names.get(self.auto_sim))
            self.cb_sim.setEnabled(False)
            if self.auto_cls is not None and self.auto_cls != '':
                self.cb_class.setCurrentText(self.friendly_class_names.get(self.auto_cls))
                self.lbl_actype_discovery.setText(f"<i>Type auto-detected.. Please validate</i>")
                self.lbl_actype_discovery.setVisible(True)
            if self.auto_name is not None and self.auto_name != '':
                self.tb_manual_full_name.setText(self.auto_name)
                self.tb_manual_full_name.setDisabled(True)
            self.stackedWidget.setCurrentIndex(1) # Skip the sim page since it is auto discovered

    def manage_pages(self, page_index):
        """
            Called when the current wizard page changes.
            Enables/disables buttons and fields based on context.
            Handles:
            - Sim selection (Page 0)
            - Class selection (Page 1)
            - Aircraft configuration (Page 2)

            """
        match page_index:
            case 0:
                # Sim Page
                self.pb_finish.setEnabled(False)
                self.pb_previous.setEnabled(False)
                self.pb_previous.setVisible(False)
                if self.cb_sim.currentText() == '':
                    self.pb_next.setEnabled(False)
                else:
                    self.pb_next.setEnabled(True)
            case 1:
                # Class Page
                self.pb_finish.setEnabled(False)
                self.pb_previous.setEnabled(True)
                self.pb_previous.setVisible(True)
                self.pb_next.setVisible(True)

                sim_name = self.cb_sim.currentText()
                lbl_txt = self.lbl_class_page.text()
                updated = lbl_txt.replace("&lt;sim&gt;", sim_name)
                self.lbl_class_page.setText(updated)
                if not self.manual_mode:
                    # If ac is auto discovered, SIM is fixed, disable back button
                    self.pb_previous.setEnabled(False)
                if self.cb_class.currentText() == '':
                    self.pb_next.setEnabled(False)
                else:
                    self.pb_next.setEnabled(True)
            case 2:  # Aircraft Page
                # Enable previous button to allow navigation back to class page in case it was disabled in auto-mode
                if self.tb_manual_full_name.text() == '':
                    self.tb_manual_full_name.setFocus()
                    self.tb_manual_full_name.setCursorPosition(len(self.tb_manual_full_name.text()))
                self.pb_previous.setEnabled(True)
                self.pb_next.setEnabled(False)
                self.pb_next.setVisible(False)

                # check if selected class name requires cloning from a default profile
                cls_name = self.internal_class_names.get(self.cb_class.currentText())
                self.check_mandatory_clone(cls_name)

                if not self.manual_mode:
                    # if in auto mode, suggest some regex match strings based on detected aircraft name
                    self.generate_regex_patterns(self.auto_name)

                if self.mandatory_clone:
                    color = "rgba(255, 85, 85, 0.3)" if self.cb_clone.currentText() == '' else ""
                    self.cb_clone.setStyleSheet(f"background-color: {color};")

                self.pb_next.setEnabled(False)

                self.validate_ac_page_entries()
            case 3:  # Profile Name Page
                self.pb_next.setEnabled(False)
                self.validate_profile_text(self.tb_profileName.text())


    def next_button_clicked(self):
        self.stackedWidget.setCurrentIndex(self.stackedWidget.currentIndex() + 1)

    def prev_button_clicked(self):
        self.stackedWidget.setCurrentIndex(self.stackedWidget.currentIndex() - 1)

    def finish_button_clicked(self):
        sim = self.selected_sim
        class_name = self.selected_class
        if self.rb_manual.isChecked():
            match_string = self.tb_manual_match_string.text()
        elif self.rb_suggested.isChecked():
            match_string = self.cb_suggested.currentText()
        else:
            raise ValueError("Invalid match string selection")

        if self.cb_clone.currentData():
            clone_name, clone_profile = self.cb_clone.currentData()
            xmlutils.clone_whole_model(self.selected_sim, clone_name, match_string, clone_profile, "User Default")
            xmlutils.update_active_profile_entry(sim, class_name, match_string, "User Default")
            # xmlutils.clone_profile_entry(self.selected_sim, self.selected_class, clone_name, clone_profile, match_string, self.tb_profileName.text())
        else:
            xmlutils.add_new_model(sim, class_name, match_string, profile_name="User Default")
            xmlutils.update_active_profile_entry(sim, class_name, match_string, "User Default")
        self.accept()

    def sim_changed(self, sim):
        """
            Triggered when the user selects a simulation platform.
            Loads the available aircraft classes for that sim.
            """
        if sim != '':
            self.selected_sim = self.internal_sim_names.get(sim)
            self.pb_next.setEnabled(True)
            self.class_list = self.get_classes(self.selected_sim)
            self.cb_class.blockSignals(True)
            self.cb_class.clear()
            self.cb_class.addItem('')
            for cls in self.class_list:
                self.cb_class.addItem(self.friendly_class_names.get(cls))
            self.cb_class.blockSignals(False)
        else:
            self.selected_sim = None
            self.pb_next.setEnabled(False)

    def class_changed(self, cls):
        """
            Triggered when the user selects an aircraft class.
            Loads available aircraft models and checks whether cloning is required.
            """
        self.pb_finish.setEnabled(False)
        if cls != '':
            self.selected_class = self.internal_class_names.get(cls)
            self.pb_next.setEnabled(True)
            self.aircraft_list = self.get_models(self.selected_sim, self.selected_class)
            self.cb_clone.clear()
            self.cb_clone.addItem('')  # Not needed since read_models returns one null entry
            max_name_len = max(len(aircraft) for aircraft, _ in self.aircraft_list)
            font = QFont("Consolas")  # Or "Monospace" or "Consolas"
            self.cb_clone.setFont(font)
            for aircraft, profile in self.aircraft_list:
                display_text = f"{aircraft.ljust(max_name_len)}  :  {profile}"
                self.cb_clone.addItem(display_text, (aircraft, profile))
                # w = self.cb_clone.width()
                # self.cb_clone.setMinimumWidth(w + 10) # fix stupid sizing issue

            self.check_mandatory_clone(cls)
        else:
            self.pb_next.setEnabled(False)

    def validate_profile_text(self, str):
        state = False if str == '' else True
        state = False if str.lower() == 'default' else state
        self.pb_finish.setEnabled(state)


    def full_name_text_changed(self, str):
        state = False if str == '' else True
        self.rb_manual.setEnabled(state)
        self.rb_suggested.setEnabled(state)
        self.cb_suggested.clear()
        self.cb_suggested.setEnabled(state)
        self.tb_manual_match_string.setEnabled(state)
        self.cb_clone.setEnabled(state)
        if state:
            if not self.rb_suggested.isChecked() and not self.rb_manual.isChecked():
                self.rb_suggested.setChecked(True)
            self.generate_regex_patterns(str)
        else:
            self.pb_finish.setEnabled(False)
            self.cb_suggested.clear()
            self.clear_match_toggles()

    def suggested_manual_toggled(self, rb, state):
        if state:
            if rb == self.rb_suggested:
                self.tb_manual_match_string.setDisabled(True)
                self.tb_manual_match_string.setStyleSheet('')
                self.generate_regex_patterns(self.tb_manual_full_name.text())

            elif rb == self.rb_manual:
                self.tb_manual_match_string.setDisabled(False)

            self.validate_ac_page_entries()

    def validate_ac_page_entries(self):
        """
            Validates the final aircraft page inputs:
            - Verifies that the match string is valid (regex or exact)
            - Checks if clone selection is valid for mandatory clone types
            Enables/disables the Finish button accordingly.
            """

        if self.rb_manual.isChecked():
            valid_match = self.validate_match_string(self.tb_manual_match_string.text())
        elif self.rb_suggested.isChecked():
            valid_match = True
        else:
            valid_match = False
        if self.mandatory_clone:
            if self.cb_clone.currentText() == '':
                self.cb_clone.setStyleSheet('background-color: rgba(255, 85, 85, 0.3);')
                mandatory_satisfied = False
            else:
                self.cb_clone.setStyleSheet('')
                mandatory_satisfied = True
        else:
            mandatory_satisfied = True
        if valid_match and mandatory_satisfied:
            self.pb_finish.setEnabled(True)
        else:
            self.pb_finish.setEnabled(False)



    def validate_match_string(self, match_str):
        """
            Validates whether a match string is either:
            - An exact match of the aircraft name
            - A valid regex pattern that matches the name
            Highlights the match string field in red/green depending on result.
            """
        full_name = self.tb_manual_full_name.text()
        tt = ''

        if not match_str:
            is_match = False
            tt = "Please enter a match string"
        elif match_str in self.aircraft_list:
            is_match = False
            tt = f"This match string already exists for {self.selected_sim} | {self.selected_class}"
        elif match_str == full_name:
            is_match = True
        elif self.looks_like_regex(match_str):
            try:
                is_match = bool(re.match(match_str, full_name))
            except re.error:
                tt = "Invalid regex pattern"
                is_match = False
        else:
            # It's neither full match nor regex-style, reject
            tt = "Match string will not match with full name"
            is_match = False

        # Set background color
        color = "rgba(0, 128, 0, 0.2)" if is_match else "rgba(255, 0, 0, 0.2)"
        self.tb_manual_match_string.setToolTip(tt) if not is_match else self.tb_manual_match_string.setToolTip('')
        self.tb_manual_match_string.setStyleSheet(f"background-color: {color};")
        self.current_match_string = match_str
        return is_match

    def looks_like_regex(self, pattern):
        # Check if pattern contains any regex meta characters
        return any(ch in pattern for ch in "^$.*+?[](){}|\\")

    def check_mandatory_clone(self, cls_name):
        """
            Checks if the selected class requires cloning from a default profile.
            Sets the appropriate label text and sets the mandatory flag accordingly.
        """
        if cls_name in self.mandatory_clone_types:
            self.mandatory_clone = True
            self.lbl_optional.setText('<html><head/><body><p><span style=" font-weight:700;">Mandatory:</span></p></body></html>')
            self.lbl_clone.setText(f'The aircraft type {self.cb_class.currentText()} requires cloning from a default profile')
        else:
            self.mandatory_clone = False
            self.lbl_optional.setText('<html><head/><body><p><span style=" font-weight:700;">Optional:</span></p></body></html>')
            self.lbl_clone.setText('You may select an existing profile to clone from')

    def get_sims(self):
        sims = xmlutils.get_sims()
        return sims

    def get_classes(self, sim):
        classes = xmlutils.get_classes_for_sim(sim)
        return classes

    def get_models(self, sim, cls):
        list = []
        models = xmlutils.read_models(sim, cls)
        for model in models:
            profiles = self.get_profiles(sim, cls, model)
            for profile in profiles:
                list.append((model, profile))
        return list

    def get_profiles(self, sim, cls, model):
        profiles = xmlutils.get_available_profiles(sim, cls, model)
        return profiles


    def generate_regex_patterns(self, input_str):
        """
            Generates a list of suggested regex patterns based on the aircraft name.
            Adds these patterns to the suggestion combo box.
            """
        words = input_str.split()
        patterns = []

        for i in range(len(words), 0, -1):
            pattern = ' '.join(words[:i])
            pattern += ".*"
            patterns.append(pattern)
        self.cb_suggested.clear()
        for match_string in patterns:
            self.cb_suggested.addItem(match_string)
        exact_match = f"^{input_str}$"
        self.cb_suggested.addItem(exact_match)
        return patterns

    def clear_match_toggles(self):
        self.bg_matchString.setExclusive(False)
        self.rb_suggested.setChecked(False)
        self.rb_suggested.setEnabled(False)
        self.cb_suggested.setEnabled(False)
        self.rb_manual.setChecked(False)
        self.rb_manual.setEnabled(False)
        self.tb_manual_match_string.setEnabled(False)
        self.bg_matchString.setExclusive(True)