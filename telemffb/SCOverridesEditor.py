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
from PyQt6.QtWidgets import QAbstractItemView, QDialog, QTableWidgetItem

from . import globals as G
from . import xmlutils
from .telem.SimConnectManager import SimConnectManager
from .ui.Ui_SCOverridesDialog import Ui_SCOverridesDialog


class SCOverridesEditor(QDialog, Ui_SCOverridesDialog):
    overrides = []
    current_name = ''
    def __init__(self, parent=None, userconfig_path='', defaults_path=''):
        super(SCOverridesEditor, self).__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self.defaults_path = defaults_path
        self.userconfig_path = userconfig_path
        self.fill_fields()
        self.pb_add.clicked.connect(self.add_button_clicked)
        self.pb_delete.clicked.connect(self.delete_button_clicked)

    def fill_fields(self):
        is_msfs = G.settings_mgr.current_sim == 'MSFS'
        self.pb_add.setEnabled(is_msfs)
        self.pb_delete.setEnabled(False)
        self.tb_var.setEnabled(is_msfs)
        self.tableWidget.setEnabled(is_msfs)
        self.tb_scale.setEnabled(is_msfs)
        self.cb_name.setEnabled(is_msfs)
        self.cb_sc_unit.setEnabled(is_msfs)
        self.tb_pattern.setText(G.settings_mgr.current_pattern)

        if is_msfs:
            self.fill_cb_name()

            self.overrides = xmlutils.read_sc_overrides(G.settings_mgr.current_pattern)


            if not any(self.overrides) :
                self.bottomlabel.setText('No overrides are set for this aircraft')
            else:
                self.overrides.sort(key=lambda x: x['name'])
                self.bottomlabel.setText('')
                self.fill_table()

        else:
            self.bottomlabel.setText('SimConnect overrides are for MSFS only.')
        pass

    def fill_cb_name(self):
        blocked_names = ['T', 'N', 'G', 'AccBody', 'TAS', 'IAS', 'AirDensity', 'AoA', 'StallAoA', 'SideSlip',
                         'DynPressure', 'Pitch', 'Roll', 'Heading', 'PitchRate', 'RollRate', 'VelRotBody',
                         'PitchAccel', 'RollAccel', 'AccRotBody', 'DesignSpeed', 'VerticalSpeed', 'SimDisabled',
                         'SimOnGround', 'Parked', 'Slew', 'SurfaceType', 'SimConnectCategory', 'EngineType',
                         'AmbWind', 'VelWorld']

        self.cb_name.clear()
        self.cb_name.addItem('')
        self.cb_name.setEditable(True)
        for x in SimConnectManager.sim_vars:
            if x.name not in blocked_names:
                self.cb_name.addItem(x.name)
        model = self.cb_name.model()
        model.sort(0)  # Sort items alphabetically


    def fill_table(self):
        self.tableWidget.blockSignals(True)
        self.tableWidget.clear()
        list_length = len(self.overrides) - 1
        # Set headers
        headers = ['Property', 'Variable', 'SC Unit', 'Scale', 's']
        self.tableWidget.setHorizontalHeaderLabels(headers)
        row_index = 0
        # Set width of the variable column
        self.tableWidget.setColumnWidth(0, 140)
        self.tableWidget.setColumnWidth(1, 300)
        self.tableWidget.setColumnWidth(2, 130)
        self.tableWidget.setColumnWidth(3, 100)
        self.tableWidget.setColumnWidth(4, 60)
        self.tableWidget.setColumnHidden(4, True)

        # Populate the table
        for row, override in enumerate(self.overrides):
            # Increment row index for adding new row
            self.tableWidget.setRowCount(row_index + 1)
            # Extracting specific key-value pairs
            name_item = QTableWidgetItem(override['name'])
            var_item = QTableWidgetItem(override['var'])
            sc_unit_item = QTableWidgetItem(override['sc_unit'])
            if override['scale'] is not None:
                scale_item = QTableWidgetItem(str(override['scale']))
            else:
                scale_item = QTableWidgetItem('')
            source_item = QTableWidgetItem(override['source'])

            # If source is 'defaults', make the text color grey
            if override['source'] == 'defaults':
                name_item.setForeground(Qt.GlobalColor.gray)
                var_item.setForeground(Qt.GlobalColor.gray)
                sc_unit_item.setForeground(Qt.GlobalColor.gray)
                scale_item.setForeground(Qt.GlobalColor.gray)

                # Make entire row unselectable
                for col in range(4):
                    item = QTableWidgetItem()
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    self.tableWidget.setItem(row_index, col, item)

            # Setting items non-editable
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            var_item.setFlags(var_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            sc_unit_item.setFlags(sc_unit_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            scale_item.setFlags(scale_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # Setting items for the row
            self.tableWidget.setItem(row, 0, name_item)
            self.tableWidget.setItem(row, 1, var_item)
            self.tableWidget.setItem(row, 2, sc_unit_item)
            self.tableWidget.setItem(row, 3, scale_item)
            self.tableWidget.setItem(row, 4, source_item)

            # Increment row index
            row_index += 1

        # Set selection behavior to select entire rows
        self.tableWidget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        self.pb_delete.clicked.connect(self.delete_button_clicked)


        # Connect currentItemChanged signal to handle row selection and data copying
        self.tableWidget.itemSelectionChanged.connect(self.on_table_item_changed)

        # Display the table
        self.tableWidget.show()
        self.tableWidget.blockSignals(False)

    def on_table_item_changed(self):

        # Get the current selected items
        selected_items = self.tableWidget.selectedItems()

        # Check if any item is selected
        if selected_items:
            # Assuming you're interested in the first selected item
            current_item = selected_items[0]
            # Get the row number from the current item
            current_row = current_item.row()

            # Copy data from the current row to designated widgets
            self.cb_name.setCurrentText(self.tableWidget.item(current_row, 0).text())
            self.tb_var.setText(self.tableWidget.item(current_row, 1).text())
            self.cb_sc_unit.setCurrentText(self.tableWidget.item(current_row, 2).text())
            self.tb_scale.setText(self.tableWidget.item(current_row, 3).text())
            self.current_name = self.tableWidget.item(current_row, 0).text()
            # enable delete button for user rows
            if self.tableWidget.item(current_row, 4).text() == 'user':
                self.pb_delete.setEnabled(True)
            else:
                self.pb_delete.setEnabled(False)
        else:
            self.current_name = ''
            self.pb_delete.setEnabled(False)

    def add_button_clicked(self):

        name = self.cb_name.currentText()
        var = self.tb_var.text()
        sc_unit = self.cb_sc_unit.currentText()
        scale_text = self.tb_scale.text()
        scale_valid = True
        # Validate and convert scale to a number (integer or float)
        if scale_text != '':
            try:
                scale = float(scale_text)
            except ValueError:
                scale_valid = False
                self.tb_scale.setText('')

        # Handle the case where scale is not a valid number
        # enable delete button for user rows
        if name != '' and var != '' and sc_unit != '' and scale_valid:
            xmlutils.write_sc_override_to_xml(G.settings_mgr.current_pattern, var, name, sc_unit, scale_text)
            self.cb_name.setCurrentText('')
            self.tb_var.setText('')
            self.cb_sc_unit.setCurrentText('')
            self.tb_scale.setText('')
            self.overrides = xmlutils.read_sc_overrides(G.settings_mgr.current_pattern)
            self.fill_table()

    def delete_button_clicked(self):
        # Get the row number from the current item
        if self.current_name != '':
            name = self.current_name
            # print(f"\nerase row: {self.current_name}    pattern: {G.settings_mgr.current_pattern}  name: {name}")
            self.tableWidget.blockSignals(True)
            self.pb_delete.setEnabled(False)
            xmlutils.erase_sc_override_from_xml(G.settings_mgr.current_pattern,name)
            self.overrides = xmlutils.read_sc_overrides(G.settings_mgr.current_pattern)
            self.fill_table()