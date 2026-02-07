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
from PyQt6.QtWidgets import QAbstractItemView, QDialog, QTableWidgetItem, QMessageBox

from . import globals as G
from . import xmlutils
from .telem.SimConnectManager import SimConnectManager
from .ui.Ui_SCOverridesDialog import Ui_SCOverridesDialog
from .util.TransformExpr import TransformExpr

class SCOverridesEditor(QDialog, Ui_SCOverridesDialog):
    overrides = []
    current_name = ''
    def __init__(self, parent=None, userconfig_path='', defaults_path=''):
        super(SCOverridesEditor, self).__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self.defaults_path = defaults_path
        self.userconfig_path = userconfig_path
        self.pb_add.clicked.connect(self.add_button_clicked)
        self.pb_delete.clicked.connect(self.delete_button_clicked)
        self.pb_Refresh.clicked.connect(self.fill_fields)

        self.msfs_types = ["bool", "enum", "number", "Percent Over 100", "degrees", "meters/second"]
        self.xplane_types = ["int", "float"]

        self.lb_transform.setToolTip(
            """
        <b>Transform Expression</b><br><br>

        Use this field to convert raw telemetry values into the range expected by the application.<br><br>

        You may enter <b>either</b>:<br>
        &bull; <b>A number</b> &ndash; applies a simple multiplier<br>
        <code>0.01</code> &nbsp;&rarr;&nbsp; multiplies the value by 0.01<br><br>

        &bull; <b>A math expression</b> using <code>x</code> as the input value<br>
        <code>(x - 50) * 0.02</code> &nbsp;&rarr;&nbsp; converts <code>0–100</code> to <code>-1–1</code><br><br>

        <b>Rules</b><br>
        &bull; <code>x</code> represents the incoming telemetry value<br>
        &bull; Standard math operators are supported:<br>
        &nbsp;&nbsp;<code>+ &nbsp; - &nbsp; * &nbsp; / &nbsp; // &nbsp; % &nbsp; **</code><br>
        &bull; Parentheses are allowed<br><br>

        <b>Not supported</b>: functions, variables other than <code>x</code>, or programming syntax<br><br>

        <b>Examples</b><br>
        <code>x * 0.01</code><br>
        <code>(x - 50) * 0.02</code><br>
        <code>-x</code><br>
        <code>(x - 16384) / 16384</code><br>
        <code>x ** 2</code><br><br>

        <b>Tip</b><br>
        Leave this field blank to use the raw value without modification.
        """
        )

        self.fill_fields()


    def _parse_scale(self, text: str):
        """
        Validate scale input.

        Returns:
            None            -> empty scale
            float           -> numeric scale
            str             -> valid expression scale
        Raises:
            ValueError      -> invalid expression
        """
        text = text.strip()
        if not text:
            return None

        # First try numeric scale
        try:
            return float(text)
        except ValueError:
            pass

        # Otherwise, treat as expression and validate
        TransformExpr(text)  # will raise if invalid
        return text

    def fill_fields(self):
        self.tableWidget.clear()
        self.fill_table()
        sim = G.settings_mgr.current_sim
        is_valid = sim == 'MSFS' or sim == 'XPLANE'
        if sim == 'MSFS':
            self.cb_sc_unit.clear()
            self.cb_sc_unit.addItems(self.msfs_types)
        if sim == 'XPLANE':
            self.cb_sc_unit.clear()
            self.cb_sc_unit.addItems(self.xplane_types)
        self.pb_add.setEnabled(is_valid)
        self.pb_delete.setEnabled(False)
        self.tb_var.setEnabled(is_valid)
        self.tableWidget.setEnabled(is_valid)
        self.tb_scale.setEnabled(is_valid)
        self.cb_name.setEnabled(is_valid)
        self.cb_sc_unit.setEnabled(is_valid)
        self.tb_pattern.setText(G.settings_mgr.current_pattern)

        if is_valid:
            self.lb_InfoLabel.setText('')
            self.fill_cb_name()

            self.overrides = xmlutils.read_sc_overrides(G.settings_mgr.current_pattern)


            if not any(self.overrides) :
                self.bottomlabel.setText('No overrides are set for this aircraft')
            else:
                self.overrides.sort(key=lambda x: x['name'])
                self.bottomlabel.setText('')
                self.fill_table()

        else:
            self.lb_InfoLabel.setText('SimConnect/Dataref overrides are only available for MSFS and X-Plane.\nLoad into an aircraft, or select an aircraft in the offline editor mode to use this dialog')
            self.bottomlabel.setText('SimConnect/Dataref overrides are for MSFS/X-Plane only. ')
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
        headers = ['Property', 'Variable', 'Unit', 'Scale', 's']
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

        try:
            scale = self._parse_scale(scale_text)
            self.tb_scale.setStyleSheet("")
            self.tb_scale.setToolTip("")
        except ValueError as e:
            # Invalid expression → visual feedback
            self.tb_scale.setStyleSheet("border: 1px solid red;")
            txt = self.label_4.toolTip()
            error_html = (
                "<b>Invalid transform expression.</b><br><br>"
                "Only numbers or math expressions using <code>x</code> are allowed.<br><br>"
                "See the field tooltip for examples."
            )
            QMessageBox.critical(self, "Invalid expression", error_html)
            self.tb_scale.setToolTip(str(e))
            return

        if name and var and sc_unit:
            xmlutils.write_sc_override_to_xml(
                G.settings_mgr.current_pattern,
                var,
                name,
                sc_unit,
                scale_text.strip() if scale is not None else None,
            )

            self.cb_name.setCurrentText('')
            self.tb_var.setText('')
            self.cb_sc_unit.setCurrentText('')
            self.tb_scale.setText('')
            self.tb_scale.setStyleSheet("")
            self.tb_scale.setToolTip("")

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