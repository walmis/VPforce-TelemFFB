import logging
import re
import sys
import os
import shutil
from PyQt5 import QtGui, QtWidgets, QtCore
from PyQt5.QtWidgets import (QApplication,  QTableWidgetItem, QCheckBox, QLineEdit, QDialog, QLabel, QComboBox,
                             QVBoxLayout, QPushButton, QFileDialog, QMessageBox)
from PyQt5.QtWidgets import QTableWidget, QTextEdit, QWidget, QSlider
from datetime import datetime
from PyQt5.QtCore import Qt, pyqtSignal
from settingswindow import Ui_SettingsWindow


import xmlutils

print_debugs = False
print_method_calls = False


def lprint(msg):
    if print_debugs:
        print(msg)

def mprint(msg):
    if print_method_calls:
        print(msg)

class SettingsWindow(QtWidgets.QMainWindow, Ui_SettingsWindow):

    sim = "nothing"                             # DCS, MSFS, IL2       -- set in get_current_model below
    current_sim = "nothing"
    current_class = ""
    model_name = "unknown airplane"    # full model name with livery etc
    crafttype = ""                       # suggested, send whatever simconnect finds
    current_aircraft_name = ""
    current_pattern = ''

    data_list = []
    prereq_list = []

    model_type = ""     # holder for current type/class
    model_pattern = ""  # holder for current matching pattern found in config xmls
    edit_mode = '' # holder for current editing mode.

    user_setting_count = 0

    allow_in_table_editing = False

    def __init__(self, datasource='', device='joystick', userconfig_path='', defaults_path=''):
        mprint(f"__init__ {datasource}, {device}")
        super(SettingsWindow, self).__init__()
        self.setupUi(self)  # This sets up the UI from Ui_SettingsWindow
        self.defaults_path = defaults_path
        self.userconfig_path = userconfig_path
        self.device=device
        self.timedOut = False

        self.sim = self.current_sim
        self.setWindowTitle(f"TelemFFB Settings Manager ({self.device})")
        self.b_browse.clicked.connect(self.choose_directory)
        # self.b_usb_button.clicked.connect(self.usb_button_clicked)
        self.b_update.clicked.connect(self.update_button)
        self.b_deleteModel.clicked.connect(self.delete_model)
        self.slider_float.valueChanged.connect(self.update_textbox)
        self.cb_enable.stateChanged.connect(self.cb_enable_setvalue)
        self.drp_valuebox.currentIndexChanged.connect(self.update_dropbox)
        self.buttonBox.rejected.connect(self.hide)
        self.clear_propmgr()
        self.backup_userconfig()
        self.drp_device.addItem('any')
        self.drp_device.addItem('joystick')
        self.drp_device.addItem('pedals')
        self.drp_device.addItem('collective')
        self.drp_device.setCurrentText(self.device)
        self.drp_device.currentTextChanged.connect(self.update_device_scope)
        self.init_ui()

    @classmethod
    def update_state_vars(cls, **kwargs):
        for key, value in kwargs.items():
            setattr(cls, key, value)

    def update_device_scope(self, dev):
        self.device = dev
        xmlutils.update_vars(dev, self.userconfig_path, self.defaults_path)
        # self.update_table_on_model_change()
        # self.clear_propmgr()
        self.reload_table()

    def get_current_model(self,the_sim, dbg_model_name, dbg_crafttype = None ):
        mprint(f"get_current_model {the_sim}, {dbg_model_name}, {dbg_crafttype}")
        # in the future, get from simconnect.
        if the_sim is not None:
            if the_sim == '':
                self.sim = 'nothing'
        else:
            self.sim = self.current_sim

        if dbg_model_name is not None:
            self.model_name = dbg_model_name     #type value in box for testing. will set textbox in future
        else:
            if self.current_aircraft_name is None:
                self.model_name = ''
            else:
                self.model_name = self.input_model_name
        if dbg_crafttype is not None:
            self.crafttype = dbg_crafttype  # suggested, send whatever simconnect finds
        else:
            self.crafttype = self.current_class

        lprint(f'get current model {self.sim}  {self.model_name} {self.crafttype}')

        self.tb_currentmodel.setText(self.model_name)
        self.table_widget.clearContents()
        # self.setup_table()
        self.setup_class_list()
        self.setup_model_list()

        # output a single model
        if self.sim != 'nothing':
            self.model_type, self.model_pattern, self.data_list = xmlutils.read_single_model(self.sim, self.model_name, self.crafttype)
        self.drp_sim.blockSignals(True)
        self.drp_sim.setCurrentText(self.sim)
        self.drp_sim.blockSignals(False)
        self.drp_class.blockSignals(True)
        self.drp_class.setCurrentText(self.model_type)
        self.drp_class.blockSignals(False)

        if self.model_pattern != '':
            #self.rb_model.setChecked(True)
            self.set_edit_mode('Model')
            self.drp_models.blockSignals(True)
            self.drp_models.setCurrentText(self.model_pattern)
            self.drp_models.blockSignals(False)
        else:
            if self.model_type == '':
                self.set_edit_mode(self.sim)
            else:
                self.set_edit_mode('Class')


        lprint(f"\nCurrent: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{self.device}\n")

        # put model name and class into UI

        if self.model_type != '': self.drp_class.setCurrentText(self.model_type)

        self.populate_table()

    def init_ui(self):
        mprint(f"init_ui")

        # xmlutils.create_empty_userxml_file()  # Now handled by TelemFFB on startup

        self.tb_currentmodel.setText(self.model_name)

        self.get_current_model('', '')
        self.b_getcurrentmodel.clicked.connect(self.currentmodel_click)

        lprint (f"init {self.sim}")
        # Your custom logic for table setup goes here
        self.setup_table()

        # Connect the stateChanged signal of the checkbox to the toggle_rows function
        self.cb_show_inherited.stateChanged.connect(self.toggle_rows)

        self.b_revert.clicked.connect(self.restore_userconfig_backup)

        self.set_edit_mode(self.sim)
        # read models from xml files to populate dropdown
        self.setup_model_list()
        self.setup_class_list()
        self.setup_sim_list()

        # allow changing sim dropdown
        self.drp_sim.currentIndexChanged.connect(lambda index: self.update_table_on_sim_change())
        # change class dropdown
        self.drp_class.currentIndexChanged.connect(lambda index: self.update_table_on_class_change())
        #allow changing model dropdown
        self.drp_models.currentIndexChanged.connect(lambda index: self.update_table_on_model_change())
        self.pb_clear.clicked.connect(lambda: self.clear_selections())
        # create model setting button
        self.b_createusermodel.clicked.connect(self.show_user_model_dialog)

        # read prereqs
        self.prereq_list = xmlutils.read_prereqs()

        # Manual Link
        bookmarked_section =  "https://docs.google.com/document/d/1YL5DLkiTxlaNx_zKHEYSs25PjmGtQ6_WZDk58_SGt8Y/edit#bookmark=id.og67qrvv8gt7"
        # <a href="https://docs.google.com/document/d/1YL5DLkiTxlaNx_zKHEYSs25PjmGtQ6_WZDk58_SGt8Y">Read TelemFFB manual for settings details</a>
        self.l_manual.setText(f'<a href="{bookmarked_section}">Read manual for details</a>')

        # Initial visibility of rows based on checkbox state
        self.toggle_rows()
        self.get_user_count()

    def clear_selections(self):
        # self.drp_sim.clear()
        # self.drp_models.setCurrentTest("")
        self.drp_models.setCurrentIndex(0)
        self.drp_class.setCurrentIndex(0)
        self.update_table_on_sim_change()
    def currentmodel_click(self):

        mprint("currentmodel_click")
        self.sim = self.current_sim
        self.model_name = self.current_aircraft_name
        self.model_type = self.current_class
        self.get_current_model(self.sim, self.model_name, self.model_type)


    def backup_userconfig(self):
        mprint("backup_userconfig")
        # Ensure the userconfig.xml file exists
        # xmlutils.create_empty_userxml_file()  # Now handled by TelemFFB on startup

        backup_path = self.userconfig_path + ".backup"
        # Create a timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path_time = f"{self.userconfig_path}_{timestamp}.backup"
        try:
            # Copy the userconfig.xml file to the backup location
            shutil.copy2(self.userconfig_path, backup_path)
            #shutil.copy2(self.userconfig_path, backup_path_time)        #  do we want lots of backups?
            logging.info(f"Backup created: {backup_path}")
        except Exception as e:
            logging.info(f"Error creating backup: {e}")

    def restore_userconfig_backup(self):
        mprint("restore_userconfig_backup")
        # Ensure the backup file exists
        backup_path = self.userconfig_path + ".backup"

        if not os.path.isfile(backup_path):
            logging.info(f"Backup file '{backup_path}' not found.")
            return

        try:
            # Copy the backup file to userconfig.xml
            shutil.copy2(backup_path, self.userconfig_path)
            logging.info(f"Backup '{backup_path}' restored to userconfig.xml")
            #self.get_current_model()

        except Exception as e:
            logging.info(f"Error restoring backup: {e}")

    def show_user_model_dialog(self):
        mprint("show_user_model_dialog")
        current_aircraft = self.tb_currentmodel.text()
        dialog = UserModelDialog(self.sim,current_aircraft, self.model_type, self)
        result = dialog.exec_()
        if result == QtWidgets.QDialog.Accepted:
            # Handle accepted
            new_aircraft = dialog.tb_current_aircraft.currentText()
            new_combo_box_value = dialog.combo_box.currentText()
            pat_to_clone = dialog.models_combo_box.currentText()
            if new_combo_box_value != '':
                logging.info (f"New: {new_aircraft} {new_combo_box_value}")
                xmlutils.write_models_to_xml(self.sim, new_aircraft, new_combo_box_value, 'type', None)
            else:
                logging.info(f"Cloning: {pat_to_clone} as {new_aircraft}")
                xmlutils.clone_pattern(self.sim, pat_to_clone, new_aircraft)

            self.model_name = new_aircraft
            self.tb_currentmodel.setText(new_aircraft)
            self.get_current_model(self.sim, new_aircraft)
        else:
            # Handle canceled
            pass

    def setup_sim_list(self):
        mprint("setup_class_list")
        self.drp_sim.blockSignals(True)
        sims = ['DCS', 'IL2', 'MSFS']
        self.drp_sim.clear()
        self.drp_sim.addItems(sims)
        self.drp_sim.setCurrentText(self.sim)
        self.drp_sim.blockSignals(False)

    def setup_class_list(self):
        mprint("setup_class_list")
        self.drp_class.blockSignals(True)


        match self.sim:

            case 'DCS':
                for disable in { 'TurbopropAircraft', 'GliderAircraft', 'HPGHelicopter'}:
                    lprint(f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)
                for enable in {'PropellerAircraft', 'JetAircraft', 'Helicopter'}:
                    lprint(f"enable {enable}")
                    self.drp_class.model().item(self.drp_class.findText(enable)).setEnabled(True)

            case 'IL2':
                for disable in {'TurbopropAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter'}:
                    lprint(f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)
                for enable in {'PropellerAircraft', 'JetAircraft'}:
                    lprint(f"enable {enable}")
                    self.drp_class.model().item(self.drp_class.findText(enable)).setEnabled(True)

            case 'MSFS':
                for enable in {'PropellerAircraft', 'TurbopropAircraft', 'JetAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter'}:
                    lprint(f"enable {enable}")
                    self.drp_class.model().item(self.drp_class.findText(enable)).setEnabled(True)

        #self.drp_class.addItems(classes)
        self.drp_class.blockSignals(False)

    def setup_model_list(self):
        mprint("setup_model_list")
        models = xmlutils.read_models(self.sim)
        self.drp_models.blockSignals(True)
        self.drp_models.clear()
        self.drp_models.addItems(models)
        self.drp_models.setCurrentText(self.model_pattern)
        self.drp_models.blockSignals(False)

    def catch_doubleclick(self):
        self.reload_table()

    def setup_table(self):
        mprint("setup_table")
        self.table_widget.setColumnCount(12)
        headers = ['Source', 'Grouping', 'Display Name', 'Value', 'Info', "name", "valid", "datatype", "unit", "state", "Device", "Factor"]
        self.table_widget.setHorizontalHeaderLabels(headers)
        self.table_widget.setColumnWidth(0, 120)
        self.table_widget.setColumnWidth(1, 110)
        self.table_widget.setColumnWidth(2, 200)
        self.table_widget.setColumnWidth(3, 80)
        self.table_widget.setColumnHidden(4, True)
        self.table_widget.setColumnHidden(5, True)
        self.table_widget.setColumnHidden(6, True)
        self.table_widget.setColumnHidden(7, True)
        self.table_widget.setColumnHidden(8, True)
        self.table_widget.setColumnHidden(9, True)
        self.table_widget.setColumnWidth(10, 60)
        self.table_widget.setColumnWidth(11, 40)
        self.table_widget.cellDoubleClicked.connect(self.catch_doubleclick)

        # row click for property manager
        self.table_widget.setSelectionBehavior(QtWidgets.QTableView.SelectRows)
        # this is for handling clicking the actual value cell..
        self.table_widget.itemSelectionChanged.connect(self.handle_item_click)


        # disable in-table value editing here
        if self.allow_in_table_editing:
            self.table_widget.itemChanged.connect(self.handle_item_change)  # Connect to the custom function


    def populate_table(self):
        mprint("populate_table")
        self.table_widget.blockSignals(True)

        # sorted_data = sorted(self.data_list, key=lambda x: (x['grouping'] != 'Basic', x['grouping'] == 'Debug', x['grouping'], x['displayname']))
        sorted_data = sorted(self.data_list, key=lambda x: float(x['order']))

        list_length = len(self.data_list) -1
        pcount = 1
        self.prereq_list = xmlutils.read_prereqs()
        self.data_list = xmlutils.check_prereq_value(self.prereq_list,self.data_list)

        for row, data_dict in enumerate(sorted_data):
            #
            # hide 'type' setting in class mode
            #
            if self.edit_mode == 'Class' and data_dict['name'] == 'type':
                self.table_widget.setRowHeight(row, 0)
                continue

            # hide prereqs not satisfied
            #
            found_prereq = False

            if data_dict['prereq'] != '':
                for pr in self.prereq_list:
                    if pr['prereq']==data_dict['prereq']:
                        if pr['value'].lower() == 'false':
                            lprint(f"no prereq - name: {data_dict['displayname']} data: {data_dict['prereq']}  pr:{pr['prereq']}  value:{pr['value']}")

                            self.table_widget.setRowHeight(row, 0)

                            found_prereq = True
                            break
                if found_prereq: continue

            state = self.set_override_state(data_dict['replaced'])
            checkbox = QCheckBox()
            # Manually set the initial state
            checkbox.setChecked(state)


            checkbox.clicked.connect(
                lambda state, trow=row, tdata_dict=data_dict: self.override_state_changed(trow, tdata_dict, state))

            item = QTableWidgetItem()
            item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            #item.setStyleSheet("margin-left:50%; margin-right:50%;")
            item.setData(QtCore.Qt.UserRole, row)  # Attach row to the item
            item.setData(QtCore.Qt.CheckStateRole, QtCore.Qt.Unchecked)  # Set initial state

            grouping_item = QTableWidgetItem(data_dict['grouping'])
            displayname_item = QTableWidgetItem(data_dict['displayname'])
            if data_dict['sliderfactor'] == '' or data_dict['sliderfactor'] == '1':
                factortext = ''
            else:
                factortext = data_dict['sliderfactor']
            sliderfactor_item = QTableWidgetItem(factortext)
            value_item = self.create_datatype_item(data_dict['datatype'], data_dict['value'], data_dict['unit'], checkbox.checkState(), data_dict['sliderfactor'])
            info_item = QTableWidgetItem(data_dict['info'])
            replaced_item = QTableWidgetItem("      " + data_dict['replaced'])
            unit_item = QTableWidgetItem(data_dict['unit'])
            valid_item = QTableWidgetItem(data_dict['validvalues'])
            datatype_item = QTableWidgetItem(data_dict['datatype'])
            device_item = QTableWidgetItem(data_dict['device_text'])


            # store name for use later, not shown
            name_item = QTableWidgetItem(data_dict['name'])
            name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            state_item = QTableWidgetItem(str(state))


            # Connect the itemChanged signal to your custom function
            value_item.setData(Qt.UserRole, row)  # Attach row to the item
            value_item.setData(Qt.UserRole + 1, data_dict['name'])  # Attach name to the item
            value_item.setData(Qt.UserRole + 2, data_dict['value'])  # Attach original value to the item
            value_item.setData(Qt.UserRole + 3, data_dict['unit'])  # Attach unit to the item
            value_item.setData(Qt.UserRole + 4, data_dict['datatype'])  # Attach datatype to the item
            value_item.setData(Qt.UserRole + 5, data_dict['validvalues'])  # Attach datatype to the item
            value_item.setData(Qt.UserRole + 6, str(state))  # Attach datatype to the item

            lprint(f"Row {row} - Grouping: {data_dict['grouping']}, Display Name: {data_dict['displayname']}, Unit: {data_dict['unit']}, Ovr: {data_dict['replaced']}")

            # Check if replaced is an empty string and set text color accordingly
            for item in [grouping_item, displayname_item, value_item, info_item, replaced_item]:  #colorizing device_item puts in in 1st column
                match data_dict['replaced']:
                    case 'Sim Default':
                        item.setForeground(QtGui.QColor('darkblue'))
                    case 'Sim (user)':
                        item.setForeground(QtGui.QColor('blue'))
                    case 'Class Default':
                        item.setForeground(QtGui.QColor('darkGreen'))
                    case 'Class (user)':
                        item.setForeground(QtGui.QColor('green'))
                    case 'Model Default':
                        item.setForeground(QtGui.QColor('darkMagenta'))
                    case 'Model (user)':
                        item.setForeground(QtGui.QColor('magenta'))
                if data_dict['grouping'] == 'Debug':
                    item.setForeground(QtGui.QColor('red'))

            # Make specific columns read-only
            grouping_item.setFlags(grouping_item.flags() & ~Qt.ItemIsEditable)
            displayname_item.setFlags(displayname_item.flags() & ~Qt.ItemIsEditable)
            displayname_item.setToolTip(data_dict['info'])
            info_item.setFlags(info_item.flags() & ~Qt.ItemIsEditable)
            replaced_item.setFlags(replaced_item.flags() & ~Qt.ItemIsEditable)
            device_item.setFlags(device_item.flags() & ~Qt.ItemIsEditable)

            if not self.allow_in_table_editing:
                value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)

            #
            # disable in-table value editing here
            # if self.allow_in_table_editing:
            #     value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)

            # Set the row count based on the actual data
            self.table_widget.setRowCount(list_length)

            self.table_widget.insertRow(row)
            self.table_widget.setItem(row, 0, item)
            try:
                self.table_widget.setCellWidget(row, 0, checkbox)
            except Exception as e:
                logging.error(f"EXCEPTION: {e}")
            self.table_widget.setItem(row, 1, grouping_item)
            self.table_widget.setItem(row, 2, displayname_item)
            self.table_widget.setItem(row, 3, value_item)
            self.table_widget.setItem(row, 4, info_item)
            self.table_widget.setItem(row, 5, name_item)
            self.table_widget.setItem(row, 6, valid_item)
            self.table_widget.setItem(row, 7, datatype_item)
            self.table_widget.setItem(row, 8, unit_item)
            self.table_widget.setItem(row, 9, state_item)
            self.table_widget.setItem(row, 10, device_item)
            self.table_widget.setItem(row, 11, sliderfactor_item)


            #self.connected_rows.add(row)


            # make unselectable in not checked
            if not state:
                for col in range(self.table_widget.columnCount()):
                    unselitem = self.table_widget.item(row, col)
                    if unselitem is not None:
                        unselitem.setFlags(unselitem.flags() & ~Qt.ItemIsSelectable)

            # if row not in self.connected_rows:
            #     value_item.dataChanged.connect(self.handle_item_change)
            #     self.connected_rows.add(row)


        self.table_widget.blockSignals(False)

    def get_user_count(self):
        count = 0
        for row in range(self.table_widget.rowCount()):
            item = self.table_widget.item(row, 0)

            if item is not None and 'user' in item.text() and self.edit_mode in item.text():
                count += 1

        if count > 0 and self.edit_mode == 'Model':
            self.b_deleteModel.setEnabled(True)
        else:
            self.b_deleteModel.setEnabled(False)

    def toggle_rows(self):
        mprint("toggle_rows")
        show_inherited = self.cb_show_inherited.isChecked()

        for row in range(self.table_widget.rowCount()):
            item = self.table_widget.item(row, 0)

            if item is not None and 'user' in item.text() and self.edit_mode in item.text():
                self.table_widget.setRowHidden(row, False)

            else:
                self.table_widget.setRowHidden(row, not show_inherited)

    def clear_propmgr(self):
        #mprint("clear_propmgr")
        self.l_displayname.setText("Select a Row to Edit")
        self.cb_enable.hide()
        self.t_info.setText('')
        self.l_sliderfactor.hide()
        self.l_value.hide()
        self.l_name.hide()
        self.slider_float.hide()
        self.b_update.hide()
        self.drp_valuebox.hide()
        self.tb_value.hide()
        self.b_browse.hide()
        self.b_usb_button.hide()

    def handle_item_click(self):
        #mprint("handle_item_click")
        selected_items = self.table_widget.selectedItems()

        if selected_items:
            # Get the row number of the first selected item
            row = selected_items[0].row()
            if row is not None:

                mprint(f"Clicked on Row {row + 1}")

                #for col in range(self.table_widget.columnCount()):

                source_item = self.table_widget.item(row, 0)
                if source_item is None:
                    return
                source = source_item.text()
                displayname_item = self.table_widget.item(row, 2, )
                displayname = displayname_item.text()
                value_item = self.table_widget.item(row, 3 )

                info_item = self.table_widget.item(row, 4 )

                info = info_item.text()
                name_item = self.table_widget.item(row, 5 )
                name = name_item.text()
                valid_item = self.table_widget.item(row, 6 )
                validvalues = valid_item.text()
                datatype_item = self.table_widget.item(row, 7 )
                datatype = datatype_item.text()
                unit_item = self.table_widget.item(row, 8 )
                unit = unit_item.text()
                state_item = self.table_widget.item(row, 9 )
                state = state_item.text()
                sliderfactor_item = self.table_widget.item(row, 11)
                if sliderfactor_item is None:
                    sliderfactor = ''
                else:
                    sliderfactor = sliderfactor_item.text()

                if datatype == 'bool':
                    # For a checkbox
                    value_state = str(value_item.data(Qt.CheckStateRole))
                    if value_state == '0':
                        value = 'False'
                    else:
                        value = 'True'
                elif datatype in ['int', 'd_int', 'float', 'negfloat', 'cfgfloat']:
                    # For line edit
                    value = value_item.text()
                else:
                    # For other cases

                    value = value_item.text()


                self.populate_propmgr(name, displayname, value, unit, validvalues, datatype, info, state, sliderfactor)
        else:
            self.clear_propmgr()

    def populate_propmgr(self, name, displayname, value, unit, validvalues, datatype, info, state, sliderfactor):
        mprint(f"populate_propmgr  {name}, {displayname}, {value}, {unit}, {validvalues}, {datatype}, {info}, {state}")
        self.clear_propmgr()
        if state.lower() == 'false':
            return
        self.l_displayname.setText(displayname)
        if info != 'None':
            self.t_info.setText(info)
            self.t_info.setToolTip(info)
        else:
            self.t_info.setText('')
            self.t_info.setToolTip('')
        self.l_name.setText(name)
        self.tb_value.setText(value)
        self.l_name.show()
        self.b_update.show()

        match datatype:
            case 'bool':
                self.cb_enable.show()
                if value == '': value = 'false'
                chkvalue = 0
                if self.strtobool(value) == 1: chkvalue = 2
                self.cb_enable.setCheckState(chkvalue)
                #self.tb_value.show()
                self.tb_value.setText(value)
            case 'float' | 'cfgfloat':
                self.slider_float.setMinimum(0)
                self.slider_float.setMaximum(100)
                self.l_value.show()
                self.slider_float.show()
                self.tb_value.show()
                if '%' in value:
                    pctval = int(value.replace('%', ''))
                else:
                    pctval = int(float(value) * 100)

                self.slider_float.setValue(pctval)
                self.tb_value.setText(str(pctval) + '%')
                if sliderfactor != '':
                    self.l_sliderfactor.show()

            case 'negfloat':
                self.slider_float.setMinimum(-100)
                self.slider_float.setMaximum(100)
                self.l_value.show()
                self.slider_float.show()
                self.tb_value.show()
                if '%' in value:
                    pctval = int(value.replace('%', ''))
                else:
                    pctval = int(float(value) * 100)

                self.slider_float.setValue(pctval)
                self.tb_value.setText(str(pctval) + '%')
                if sliderfactor != '1':
                    self.l_sliderfactor.show()

            case 'int' | 'd_int' | 'text' | 'anyfloat' | 'd_float' | 'button':
                self.l_value.show()
                self.tb_value.show()

            # case 'button':
            #
            #     self.b_usb_button.setText(f"Button {value}")
            #     self.b_usb_button.show()

            case 'list':
                self.l_value.show()
                self.tb_value.show()
                self.drp_valuebox.show()
                self.drp_valuebox.blockSignals(True)
                self.drp_valuebox.clear()
                valids = validvalues.split(',')
                self.drp_valuebox.addItems(valids)
                self.drp_valuebox.setCurrentText(value)
                self.drp_valuebox.blockSignals(False)
            case 'path':
                self.b_browse.show()
                self.l_value.show()

    def cb_enable_setvalue(self):
        mprint("cb_enable_setvalue")
        state = self.cb_enable.checkState()
        strstate = 'false' if state == 0 else 'true'
        self.tb_value.setText(strstate)

    def update_button(self):
        mprint("update_button")
        self.write_values(self.l_name.text(),self.tb_value.text())
        self.reload_table()

    def update_textbox(self, value):
        mprint(f"update_textbox  {value}")
        pct_value = int(value)  # Convert slider value to a float (adjust the division factor as needed)
        self.tb_value.setText(str(pct_value)+'%')

    def update_dropbox(self):
        mprint("update_dropbox")
        self.tb_value.setText(self.drp_valuebox.currentText())

    def delete_model(self):
        model_to_delete = self.drp_models.currentText()
        print(f"delete model {model_to_delete}")
        qm = QMessageBox()
        ret = qm.question(self, 'Delete User Model Settings',
                          f"Are you sure you want to delete your settings\nfor match pattern:\n{model_to_delete}?",
                          qm.Yes | qm.No)
        if ret == qm.No:
            return
        xmlutils.erase_entire_model_from_xml(self.sim, model_to_delete)
        self.reload_table()

    # needs work....using value box for now.
    # def usb_button_clicked(self):
    #     button_name = self.sender().objectName().replace('pb_', '')
    #     the_button = self.mainwindow.findChild(QPushButton, f'pb_{button_name}')
    #     the_button.setText("Push a button! ")
    #     # listen for button loop
    #     # Start a thread to fetch button press with a timeout
    #     self.thread = mainwindow.ButtonPressThread(self.device, self.sender())
    #     self.thread.button_pressed.connect(self.update_usb_button)
    #     self.thread.start()
    #
    # def update_usb_button(self, button_name, value):
    #     the_button = self.mainwindow.findChild(QPushButton, f'pb_{button_name}')
    #     the_button.setText(str(value))
    #     if str(value) != '0':
    #         xmlutils.write_models_to_xml(settings_mgr.current_sim, settings_mgr.current_pattern, str(value), button_name)
    #     self.reload_caller()


    def choose_directory(self):
        current_text = self.tb_value.text()
        # options |= QFileDialog.DontUseNativeDialog

        if current_text and os.path.isfile(current_text):
            # Use the existing file path as the starting point
            starting_dir = os.path.dirname(current_text)
        else:
            # Use the current working directory as the starting point
            starting_dir = os.getcwd()

        options = QFileDialog.Options()

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Choose File", starting_dir,
            "vpconf Files (*.vpconf)", options=options
        )

        if file_path:
            self.tb_value.setText(file_path)

    def handle_item_change(self,  item):
        mprint(f"handle_item_change {item}")
        lprint (f"{item.column()} : {self.value_previous} ")
        if item.column() == 3:  #  column 3 contains the 'value' items

            row = item.data(Qt.UserRole)
            name = item.data(Qt.UserRole + 1)
            original_value = item.data(Qt.UserRole + 2)
            unit = item.data(Qt.UserRole + 3)
            datatype = item.data(Qt.UserRole + 4)
            valid = item.data(Qt.UserRole + 5)
            state = item.data(Qt.UserRole + 6)
            new_value = item.text()

            if datatype == 'bool':
                newbool = not self.strtobool(original_value)
                new_value = 'true' if newbool else 'false'

            if original_value == '': original_value = "(blank)"


            if new_value != original_value:
                lprint(f"{item.column()} : CHANGED ")

                self.write_values(name, new_value)

                lprint(
                            f"Row {row} - Name: {name}, Original: {original_value}, New: {new_value}, Unit: {unit}, Datatype: {datatype}, valid values: {valid}")

    def write_values(self, name, new_value):
        mprint(f"write values  {name}, {new_value}")
        mysim = self.drp_sim.currentText()
        myclass = self.drp_class.currentText()
        mymodel = self.drp_models.currentText()
        match self.edit_mode:
            case 'Sim':
                xmlutils.write_sim_to_xml(self.sim, new_value, name)
                self.drp_sim.setCurrentText('')
                self.drp_sim.setCurrentText(mysim)
            case 'Class':
                xmlutils.write_class_to_xml( self.sim, self.model_type, new_value, name)
                self.drp_class.setCurrentText('')
                self.drp_class.setCurrentText(myclass)
            case 'Model':
                xmlutils.write_models_to_xml( self.sim, self.model_pattern, new_value, name)
                self.drp_models.setCurrentText('')
                self.drp_models.setCurrentText(mymodel)
        self.reload_table()

    def strtobool(self,val):
        """Convert a string representation of truth to true (1) or false (0).
        True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
        are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
        'val' is anything else.
        """
        val = val.lower()
        if val in ('y', 'yes', 't', 'true', 'on', '1'):
            return 1
        elif val in ('n', 'no', 'f', 'false', 'off', '0'):
            return 0
        else:
            raise ValueError("invalid truth value %r" % (val,))


    # Slot function to handle checkbox state changes
    # blows up 0x0000005 after 3 clicks

    def override_state_changed(self, row, data_dict, state):
        mprint(f"Override - Row: {row}, Name: {data_dict['name']}, value: {data_dict['value']}, State: {state}, Edit: {self.edit_mode}")
        mysim = self.drp_sim.currentText()
        myclass = self.drp_class.currentText()
        mymodel = self.drp_models.currentText()

        self.table_widget.blockSignals(True)
        if state:

            # add row to userconfig
            match self.edit_mode:
                case 'Sim':
                    lprint(
                        f"Override - {self.sim}, Name: {data_dict['name']}, value: {data_dict['value']}, State: {state}, Edit: {self.edit_mode}")
                    xmlutils.write_sim_to_xml(self.sim, data_dict['value'], data_dict['name'])
                    # self.drp_sim.setCurrentText('')
                    # self.drp_sim.setCurrentText(mysim)
                case 'Class':
                    lprint(f"Override - {self.sim}.{myclass}, Name: {data_dict['name']}, value: {data_dict['value']}, State: {state}, Edit: {self.edit_mode}")
                    xmlutils.write_class_to_xml(self.sim, myclass, data_dict['value'],data_dict['name'])
                    # self.drp_class.setCurrentText('')
                    # self.drp_class.setCurrentText(myclass)
                case 'Model':
                    lprint(f"Override - {self.sim}.{mymodel}, Name: {data_dict['name']}, value: {data_dict['value']}, State: {state}, Edit: {self.edit_mode}")
                    xmlutils.write_models_to_xml(self.sim, mymodel,data_dict['value'],data_dict['name'])

                    # self.drp_models.setCurrentText('')
                    # self.drp_models.setCurrentText(mymodel)
        # make value editable & reset view

            self.reload_table()
            self.table_widget.selectRow(row)
            self.handle_item_click()

        else:
            match self.edit_mode:
                case 'Sim':
                    lprint(
                        f"Remove - {self.sim}, Name: {data_dict['name']}, value: {data_dict['value']}, State: {state}, Edit: {self.edit_mode}")
                    xmlutils.erase_sim_from_xml(self.sim,data_dict['value'], data_dict['name'])
                    # self.drp_sim.setCurrentText('')
                    # self.drp_sim.setCurrentText(mysim)
                case 'Class':
                    lprint(
                        f"Remove - {self.sim}.{self.drp_class.currentText()}, Name: {data_dict['name']}, value: {data_dict['value']}, State: {state}, Edit: {self.edit_mode}")
                    xmlutils.erase_class_from_xml(self.sim,self.drp_class.currentText(), data_dict['value'], data_dict['name'])
                    # self.drp_class.setCurrentText('')
                    # self.drp_class.setCurrentText(myclass)
                case 'Model':
                    lprint(
                        f"Remove - {self.sim}.{self.drp_models.currentText()}, Name: {data_dict['name']}, value: {data_dict['value']}, State: {state}, Edit: {self.edit_mode}")
                    xmlutils.erase_models_from_xml(self.sim,self.drp_models.currentText(), data_dict['name'])
                    # self.drp_models.setCurrentText('')
                    # self.drp_models.setCurrentText(mymodel)
                # make value editable & reset view
            self.reload_table()
            self.clear_propmgr()
        self.table_widget.blockSignals(False)



    def update_table_on_model_change(self):
        mprint("update_table_on_model_change")
        # Get the selected model from the combo box
        self.set_edit_mode('Model')

        self.model_name = self.drp_models.currentText()

        if self.model_name != '':

            self.model_type, self.model_pattern, self.data_list = xmlutils.read_single_model(self.sim, self.model_name)
            lprint(f"\nmodel change for: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{self.device}\n")

            # Update the table with the new data
            self.drp_class.blockSignals(True)
            self.drp_class.setCurrentText(self.model_type)
            self.drp_class.blockSignals(False)

        else:
            lprint("model cleared")
            self.set_edit_mode('Class')
            old_model_type = self.model_type
            lprint(self.model_type)
            self.drp_class.setCurrentText('')
            self.drp_class.setCurrentText(old_model_type)

        self.reload_table()

    def update_table_on_class_change(self):
        mprint("update_table_on_class_change")
        # Get the selected model from the combo box
        self.drp_models.blockSignals(True)
        self.drp_models.setCurrentText('')
        self.model_name = ''
        self.drp_models.blockSignals(False)
        self.set_edit_mode('Class')
        self.model_type = self.drp_class.currentText()
        if self.model_type != '':

            self.reload_table()

            lprint(
                f"\nclass change for: {self.sim}  model: ---  pattern: {self.model_pattern}  class: {self.model_type}  device:{self.device}\n")

        else:
            lprint("class cleared")
            self.drp_class.setCurrentText('')
            self.set_edit_mode('Sim')
            old_sim = self.sim
            lprint(self.model_type)
            self.drp_sim.setCurrentText('noting')
            self.drp_sim.setCurrentText(old_sim)

        self.table_widget.clearContents()
        # self.setup_table()
        self.reload_table()

    def update_table_on_sim_change(self):
        mprint("update_table_on_sim_change")
        # Get the selected sim from the radio buttons

        self.sim = self.drp_sim.currentText()

        self.set_edit_mode('Sim')

        self.setup_class_list()
        self.setup_model_list()

        self.reload_table()

        lprint(f"\nsim change for: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{self.device}\n")

    def reload_table(self):
        mprint("reload_table")
        self.table_widget.blockSignals(True)
        # Read all the data
        self.model_type, self.model_pattern, self.data_list = xmlutils.read_single_model(self.sim, self.model_name, self.model_type)
        # Update the table with the new data
        self.table_widget.clearContents()
        # self.setup_table()
        self.populate_table()
        self.toggle_rows()
        self.get_user_count()
        self.table_widget.blockSignals(False)

    def create_datatype_item(self, datatype, value, unit, checkstate, sliderfactor=1):
        #mprint(f"create_datatype_item {datatype} {value} {unit}, {str(checkstate)}")
        if datatype == 'bool':
            toggle = QCheckBox()
            toggle.setChecked(value.lower() == 'true')
            #checkbox.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            toggle.setStyleSheet("margin-left:50%; margin-right:50%;")
            item = QTableWidgetItem()
            #item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            boolvalue = self.strtobool(value)
            item.setData(Qt.CheckStateRole, Qt.Checked if boolvalue else Qt.Unchecked)
            if not checkstate:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)   # no editing if not allowed in this mode
            # disable in-table value editing here
            if not self.allow_in_table_editing:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)  #
            return item
        elif datatype == 'int' or datatype == 'd_int' or datatype == 'text' or datatype == 'float' or datatype == 'negfloat' or datatype == 'cfgfloat' :
            the_unit = str(unit)
            if the_unit == 'None':
                the_unit = ''
            line_edit = QLineEdit(str(value) + the_unit)
            item = QTableWidgetItem(line_edit.text())  # Set the widget
            if not checkstate:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)   # no editing if not allowed in this mode
            return item
        # making float numeric for now...
        # elif datatype == 'float':
        #     slider = QSlider(Qt.Horizontal)
        #     slider.setValue(int(float(value) * 100))  # Assuming float values between 0 and 1
        #     item = QTableWidgetItem()
        #     item.setData(Qt.DisplayRole, slider)
        #     return item
        else:
            item = QTableWidgetItem(value)
            if not checkstate:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)   # no editing if not allowed in this mode
            return item

    def set_override_state(self, override_text):
        #mprint(f"set_override_state  {override_text}")
        state = False
        if '(user)' not in override_text:
            state = False
        else:
            match self.edit_mode:
                case 'Sim':
                    state = (override_text == 'Sim (user)')
                case 'Class':
                    state = (override_text == 'Class (user)')
                case 'Model':
                    state =(override_text == 'Model (user)')
        return state

    def set_edit_mode(self,mode):
        mprint(f"set_edit_mode  {mode}")
        oldmode = self.edit_mode

        if mode != oldmode:
            match mode:
                case 'MSFS' | 'IL2' | 'DCS':
                    mode = 'Sim'

            self.l_mode.setText(mode)
            self.edit_mode = mode
            self.setup_class_list()
            match mode:
                case 'Sim':
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)
                    self.drp_class.blockSignals(True)
                    self.drp_models.blockSignals(True)
                    self.drp_class.setCurrentText('')
                    self.drp_models.setCurrentText('')
                    self.model_name = ''
                    self.model_type = ''
                    self.drp_class.blockSignals(False)
                    self.drp_models.blockSignals(False)
                    self.b_deleteModel.setEnabled(False)
                    self.b_createusermodel.setEnabled(True)

                case 'Class':
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)
                    self.drp_models.blockSignals(True)
                    self.drp_models.setCurrentText('')
                    self.model_name = ''
                    self.drp_models.blockSignals(False)
                    self.b_createusermodel.setEnabled(True)
                    self.b_deleteModel.setEnabled(False)

                case 'Model':
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)
                    self.b_createusermodel.setEnabled(True)
                    self.b_deleteModel.setEnabled(True)

                case _:
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)

            if self.tb_currentmodel.text() == '':
                self.b_createusermodel.setEnabled(False)
                self.b_getcurrentmodel.setEnabled(False)
            else:
                self.b_getcurrentmodel.setEnabled(True)

        lprint(f"{mode} Mode")




    def skip_bad_combos(self, craft):
        if self.sim == 'DCS' and craft == 'HPGHelicopter': return True
        if self.sim == 'DCS' and craft == 'TurbopropAircraft': return True
        if self.sim == 'DCS' and craft == 'GliderAircraft': return True
        if self.sim == 'IL2' and craft == 'GliderAircraft': return True
        if self.sim == 'IL2' and craft == 'HPGHelicopter': return True
        if self.sim == 'IL2' and craft == 'Helicopter': return True
        if self.sim == 'IL2' and craft == 'TurbopropAircraft': return True

        return False


class UserModelDialog(QDialog):
    the_sim = ''
    def __init__(self, sim, current_aircraft, current_type, parent=None):
        super(UserModelDialog, self).__init__(parent)
        self.the_sim = sim
        self.combo_box = None
        self.models_combo_box = None
        self.tb_current_aircraft = None
        self.setWindowTitle("Create Model Setting")
        self.init_ui(sim, current_aircraft,current_type)

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

    def generate_regex_patterns(self,input_str):
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

    def init_ui(self,sim,current_aircraft,current_type):

        layout = QVBoxLayout()

        label1 = QLabel("TelemFFB uses regex to match aircraft names")
        label2 = QLabel("Name.* will match anything starting with 'Name'")
        label3 = QLabel("^Name$ will match only the exact 'Name'")
        label4 = QLabel("(The )?Name.* matches starting with 'Name' or 'The Name'" )
        label5 = QLabel("Choose or Edit the match pattern below.")

        label6 = QLabel("And choose the aircraft class:")

        label7 = QLabel("Or, choose an existing pattern to clone:")

        classes = []
        match sim:
            case 'DCS':
                classes = ["PropellerAircraft", "JetAircraft", "Helicopter"]
            case 'IL2':
                classes = ["PropellerAircraft", "JetAircraft"]
            case 'MSFS':
                classes = ['PropellerAircraft', 'TurbopropAircraft', 'JetAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter']

        # FOR TESTING
        # classes.append('AllSettings')

        label_aircraft = QtWidgets.QLabel("Current Aircraft:")

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
        self.ok_button.setEnabled(False)
        cancel_button = QPushButton("Cancel")
        cancel_button.setStyleSheet("text-align:center;")

        layout.addWidget(label1)
        layout.addWidget(label2)
        layout.addWidget(label3)
        layout.addWidget(label4)
        layout.addWidget(label5)

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





if __name__ == "__main__":

    app = QApplication(sys.argv)
    sw = SettingsWindow(device='joystick')

    sw.show()

    sys.exit(app.exec_())

