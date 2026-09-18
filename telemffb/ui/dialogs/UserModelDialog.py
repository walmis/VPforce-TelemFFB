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

from PyQt6 import QtCore
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton

from telemffb import xmlutils


class UserModelDialog(QDialog):
    the_sim = ''

    def __init__(self, sim, current_aircraft, current_type, parent=None):
        super(UserModelDialog, self).__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        self.the_sim = sim
        self.combo_box = None
        self.models_combo_box = None
        self.tb_current_aircraft = None
        self.setWindowTitle(f"Create Model Settings ({self.the_sim} is current/selected)")
        self.init_ui(sim, current_aircraft, current_type)

    def class_combo_changed(self):
        self.models_combo_box.blockSignals(True)
        self.setup_models()
        self.models_combo_box.setCurrentText('')
        self.models_combo_box.blockSignals(False)
        if self.combo_box.currentText() != '':
            self.ok_button.setEnabled(True)
        else:
            self.ok_button.setEnabled(False)

    def pattern_changed(self):
        self.combo_box.blockSignals(True)
        self.combo_box.setCurrentText('')
        self.combo_box.blockSignals(False)
        if self.models_combo_box.currentText() != '':
            self.ok_button.setEnabled(True)
        else:
            self.ok_button.setEnabled(False)

    def generate_regex_patterns(self, input_str):
        words = input_str.split()
        patterns = []

        for i in range(len(words), 0, -1):
            pattern = ' '.join(words[:i])
            pattern += ".*"
            patterns.append(pattern)

        return patterns

    def setup_models(self):
        models = xmlutils.read_models(self.the_sim, self.combo_box.currentText())
        self.models_combo_box.clear()
        self.models_combo_box.blockSignals(True)
        self.models_combo_box.addItem('')
        self.models_combo_box.addItems(models)
        self.models_combo_box.setCurrentText('')

        self.models_combo_box.blockSignals(False)

    def init_ui(self, sim, current_aircraft, current_type):

        layout = QVBoxLayout()
        lb1_txt = """
    <p>TelemFFB uses regex to match aircraft names</p>
    <p><b style="font-family: Courier">Name.*</b> will match anything starting with '<b>Name</b>'</p>
    <p><b style="font-family: Courier">^Name$</b> will match only the exact '<b>Name</b>'</p>
    <p><b style="font-family: Courier">(The )?Name.*</b> matches starting with '<b>Name</b>' or '<b>The Name</b>'</p>

    <p><b>**Note** if this is a new livery for an existing aircraft, is recommended to clone from the default profile</b></p>
    <p><b>for that aircraft if one exists.  This is mandatory for aircraft with special implementations like HPGHelicopter, SASHelicopter, FlyInsideHelicopter, TaogH500Helicopter, XAW109Helicopter</b></p>

"""
        label1 = QLabel(lb1_txt)
        # label1 = QLabel("TelemFFB uses regex to match aircraft names")
        # label2 = QLabel("Name.* will match anything starting with 'Name'")
        # label3 = QLabel("^Name$ will match only the exact 'Name'")
        # label4 = QLabel("(The )?Name.* matches starting with 'Name' or 'The Name'" )
        lb2_text = f"<br>Creating new aircraft profile within sim: <b>{self.the_sim}</b><br>"
        label2 = QLabel(lb2_text)

        label3 = QLabel("Choose or Edit the match pattern below.")

        label6 = QLabel("And choose the aircraft class:")

        label7 = QLabel("Or, choose an existing pattern to clone:")

        classes = []
        match sim:
            case 'DCS':
                classes = ["PropellerAircraft", "JetAircraft", "Helicopter"]
            case 'IL2':
                classes = ["PropellerAircraft", "JetAircraft"]
            case 'MSFS':
                classes = ['PropellerAircraft', 'TurbopropAircraft', 'JetAircraft', 'GliderAircraft', 'Helicopter',
                           'HPGHelicopter', 'SASHelicopter', 'FlyInsideHelicopter', 'TaogH500Helicopter']
            case 'XPLANE':
                classes = ['PropellerAircraft', 'TurbopropAircraft', 'JetAircraft', 'GliderAircraft', 'Helicopter', 'XAW109Helicopter']

        # FOR TESTING
        # classes.append('AllSettings')

        # label_aircraft = QtWidgets.QLabel("Current Aircraft:")

        self.tb_current_aircraft = QComboBox()
        self.tb_current_aircraft.blockSignals(True)
        patterns = self.generate_regex_patterns(current_aircraft)
        self.tb_current_aircraft.addItem(current_aircraft)
        self.tb_current_aircraft.addItems(patterns)

        self.tb_current_aircraft.setCurrentText(current_aircraft)
        self.tb_current_aircraft.setEditable(True)
        self.tb_current_aircraft.setStyleSheet("QComboBox::view-item { align-text: center; }")
        self.tb_current_aircraft.blockSignals(False)

        self.combo_box = QComboBox()
        self.combo_box.blockSignals(True)
        self.combo_box.addItem('')
        self.combo_box.addItems(classes)
        self.combo_box.setStyleSheet("QComboBox::view-item { align-text: center; }")
        self.combo_box.setCurrentText(current_type)
        self.combo_box.currentIndexChanged.connect(self.class_combo_changed)
        self.combo_box.blockSignals(False)

        self.models_combo_box = QComboBox()
        self.setup_models()
        self.models_combo_box.blockSignals(True)

        self.models_combo_box.setStyleSheet("QComboBox::view-item { align-text: center; }")
        self.models_combo_box.currentIndexChanged.connect(self.pattern_changed)
        self.models_combo_box.blockSignals(False)

        self.ok_button = QPushButton("OK")
        self.ok_button.setStyleSheet("text-align:center;")
        if self.combo_box.currentText() == '':
            self.ok_button.setEnabled(False)
        cancel_button = QPushButton("Cancel")
        cancel_button.setStyleSheet("text-align:center;")

        layout.addWidget(label1)
        layout.addWidget(label2)
        layout.addWidget(label3)
        # layout.addWidget(label4)
        # layout.addWidget(label5)
        layout.addWidget(self.tb_current_aircraft)

        layout.addWidget(label6)
        layout.addWidget(self.combo_box)

        layout.addWidget(label7)
        layout.addWidget(self.models_combo_box)

        layout.addWidget(self.ok_button)
        layout.addWidget(cancel_button)

        self.setLayout(layout)

        self.ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
