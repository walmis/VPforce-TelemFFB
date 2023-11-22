import xml.etree.ElementTree as ET
import sys
import os
import shutil
from PyQt5 import QtGui, QtWidgets, QtCore
from PyQt5.QtWidgets import (QApplication,  QTableWidgetItem, QCheckBox, QLineEdit, QDialog, QLabel, QComboBox,
                             QVBoxLayout, QPushButton)
from PyQt5.QtWidgets import QTableWidget, QTextEdit, QWidget, QSlider
from datetime import datetime
from PyQt5.QtCore import Qt
from settingswindow import Ui_SettingsWindow
import re


class SettingsWindow(QtWidgets.QMainWindow, Ui_SettingsWindow):

    sim = ""                             # DCS, MSFS, IL2       -- set in get_current_model below
    model_name = "unknown airplane"    # full model name with livery etc
    crafttype = ""                       # suggested, send whatever simconnect finds

    data_list = []

    model_type = ""     # holder for current type/class
    model_pattern = ""  # holder for current matching pattern found in config xmls
    edit_mode = '' # holder for current editing mode.


    def __init__(self, datasource='Global', defaults_path='defaults.xml', userconfig_path='userconfig.xml', device='joystick'):
        super(SettingsWindow, self).__init__()
        self.setupUi(self)  # This sets up the UI from Ui_SettingsWindow
        self.defaults_path = defaults_path
        self.userconfig_path = userconfig_path
        self.device = device
        self.sim = datasource
        self.backup_userconfig()
        self.init_ui()

    def get_current_model(self,the_sim='Global'):
        # in the future, get from simconnect.
        self.sim = the_sim
        self.model_name = self.tb_currentmodel.text()     #type value in box for testing. will set textbox in future
        self.crafttype = ""  # suggested, send whatever simconnect finds
        self.table_widget.clear()
        self.setup_table()
        self.setup_class_list()
        self.setup_model_list()

        # output a single model
        self.model_type, self.model_pattern, self.data_list = self.read_single_model()
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
            self.set_edit_mode('Class')

        print(f"\nCurrent: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{self.device}\n")

        # put model name and class into UI

        if self.model_type != '': self.drp_class.setCurrentText(self.model_type)

        self.populate_table()

    def init_ui(self):

        self.create_empty_userxml_file()

        self.tb_currentmodel.setText(self.model_name)

        self.get_current_model()
        self.b_getcurrentmodel.clicked.connect(self.currentmodel_click)

        print (f"init {self.sim}")
        # Your custom logic for table setup goes here
        self.setup_table()

        # Connect the stateChanged signal of the checkbox to the toggle_rows function
        self.cb_show_inherited.stateChanged.connect(self.toggle_rows)

        self.b_revert.clicked.connect(self.restore_userconfig_backup)

        self.l_device.setText(self.device)
        self.set_edit_mode(self.sim)
        # read models from xml files to populate dropdown
        self.setup_model_list()
        self.setup_class_list()

        # allow changing sim dropdown
        self.drp_sim.currentIndexChanged.connect(lambda index: self.update_table_on_sim_change())
        # change class dropdown
        self.drp_class.currentIndexChanged.connect(lambda index: self.update_table_on_class_change())
        #allow changing model dropdown
        self.drp_models.currentIndexChanged.connect(lambda index: self.update_table_on_model_change())

        # create model setting button
        self.b_createusermodel.clicked.connect(self.show_user_model_dialog)

        # Initial visibility of rows based on checkbox state
        self.toggle_rows()

    def currentmodel_click(self):
        self.get_current_model(self.sim)

    def backup_userconfig(self):
        # Ensure the userconfig.xml file exists
        self.create_empty_userxml_file()
        backup_path = self.userconfig_path + ".backup"
        # Create a timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path_time = f"{self.userconfig_path}_{timestamp}.backup"
        try:
            # Copy the userconfig.xml file to the backup location
            shutil.copy2(self.userconfig_path, backup_path)
            shutil.copy2(self.userconfig_path, backup_path_time)
            print(f"Backup created: {backup_path}")
        except Exception as e:
            print(f"Error creating backup: {e}")

    def restore_userconfig_backup(self):
        # Ensure the backup file exists
        backup_path = self.userconfig_path + ".backup"

        if not os.path.isfile(backup_path):
            print(f"Backup file '{backup_path}' not found.")
            return

        try:
            # Copy the backup file to userconfig.xml
            shutil.copy2(backup_path, self.userconfig_path)
            print(f"Backup '{backup_path}' restored to userconfig.xml")
        except Exception as e:
            print(f"Error restoring backup: {e}")

    def show_user_model_dialog(self):
        current_aircraft = self.tb_currentmodel.text()
        dialog = UserModelDialog(self.sim,current_aircraft, self)
        result = dialog.exec_()
        if result == QtWidgets.QDialog.Accepted:
            # Handle accepted
            new_aircraft = dialog.tb_current_aircraft.text()
            new_combo_box_value = dialog.combo_box.currentText()
            print (f"New: {new_aircraft} {new_combo_box_value}")
            self.write_models_to_xml(new_aircraft,new_combo_box_value, 'type')
            self.model_name = new_aircraft
            self.tb_currentmodel.setText(new_aircraft)
            self.get_current_model(self.sim)
        else:
            # Handle canceled
            pass

    def write_models_to_xml(self, the_model, the_value, setting_name):
        # Load the existing XML file or create a new one if it doesn't exist
        tree = ET.parse(self.userconfig_path)
        root = tree.getroot()
        for model_elem in root.findall(f'.//models[sim="{self.sim}"]'
                                       f'[device="{self.device}"]'
                                       f'[value="{the_value}"]'
                                       f'[model="{the_model}"]'
                                       f'[name="{setting_name}"]'):

            if model_elem is not None:
                print("<models> element with the same values already exists. Skipping.")
                return

        # Create child elements with the specified content
        models = ET.SubElement(root, "models")
        for tag, value in [("name", setting_name),
                           ("model", the_model),
                           ("value", the_value),
                           ("sim", self.sim),
                           ("device", self.device)]:
            ET.SubElement(models, tag).text = value

        # Write the modified XML back to the file
        tree = ET.ElementTree(root)
        tree.write(self.userconfig_path)

    def setup_class_list(self):
        self.drp_class.blockSignals(True)
        print_debug = False

        match self.sim:
            case 'Global':
                # Assuming drp_class is your QComboBox
                for disable in {'PropellerAircraft', 'TurbopropAircraft', 'JetAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter'}:
                    if print_debug: print (f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)

            case 'DCS':
                for disable in { 'TurbopropAircraft', 'GliderAircraft', 'HPGHelicopter'}:
                    if print_debug: print(f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)
                for enable in {'PropellerAircraft', 'JetAircraft', 'Helicopter'}:
                    if print_debug: print(f"enable {enable}")
                    self.drp_class.model().item(self.drp_class.findText(enable)).setEnabled(True)

            case 'IL2':
                for disable in {'TurbopropAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter'}:
                    if print_debug: print(f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)
                for enable in {'PropellerAircraft', 'JetAircraft'}:
                    if print_debug: print(f"enable {enable}")
                    self.drp_class.model().item(self.drp_class.findText(enable)).setEnabled(True)

            case 'MSFS':
                for enable in {'PropellerAircraft', 'TurbopropAircraft', 'JetAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter'}:
                    if print_debug: print(f"enable {enable}")
                    self.drp_class.model().item(self.drp_class.findText(enable)).setEnabled(True)

        #self.drp_class.addItems(classes)
        self.drp_class.blockSignals(False)

    def setup_model_list(self):
        models = self.read_models(self.sim)
        self.drp_models.blockSignals(True)
        self.drp_models.clear()
        self.drp_models.addItems(models)
        self.drp_models.setCurrentText(self.model_pattern)
        self.drp_models.blockSignals(False)

    def setup_table(self):
        self.table_widget.setColumnCount(6)
        headers = ['Source', 'Grouping', 'Display Name', 'Value', 'Info', "name"]
        self.table_widget.setHorizontalHeaderLabels(headers)
        self.table_widget.setColumnWidth(0, 120)
        self.table_widget.setColumnWidth(1, 120)
        self.table_widget.setColumnWidth(2, 200)
        self.table_widget.setColumnWidth(3, 100)
        self.table_widget.setColumnWidth(4, 320)  #make 380 later
        self.table_widget.setColumnHidden(5, True)

    def populate_table(self):
        sorted_data = sorted(self.data_list, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['displayname']))
        for row, data_dict in enumerate(sorted_data):
            state = self.set_override_state(data_dict['replaced'])
            checkbox = QCheckBox()
            # Manually set the initial state
            checkbox.setChecked(state)
            checkbox.clicked.connect(
                lambda state, trow=row, tdata_dict=data_dict: self.override_state_changed(trow, tdata_dict, state))

            item = QTableWidgetItem()
            item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)

            item.setData(QtCore.Qt.UserRole, row)  # Attach row to the item
            item.setData(QtCore.Qt.CheckStateRole, QtCore.Qt.Unchecked)  # Set initial state


            grouping_item = QTableWidgetItem(data_dict['grouping'])
            displayname_item = QTableWidgetItem(data_dict['displayname'])
            value_item = self.create_datatype_item(data_dict['datatype'], data_dict['value'], data_dict['unit'], checkbox.checkState())
            info_item = QTableWidgetItem(data_dict['info'])
            replaced_item = QTableWidgetItem("      " + data_dict['replaced'])
            unit_item = QTableWidgetItem(data_dict['unit'])
            # store name for use later, not shown
            name_item = QTableWidgetItem(data_dict['name'])
            name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)

            #print(f"Row {row} - Grouping: {data_dict['grouping']}, Display Name: {data_dict['displayname']}, Unit: {data_dict['unit']}, Ovr: {data_dict['replaced']}")

            # Check if replaced is an empty string and set text color accordingly
            for item in [grouping_item, displayname_item, value_item, info_item, replaced_item]:
                match data_dict['replaced']:
                    case 'Global':
                        item.setForeground(QtGui.QColor('gray'))
                    case 'Global (user)':
                        item.setForeground(QtGui.QColor('black'))
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

            # Make specific columns read-only
            grouping_item.setFlags(grouping_item.flags() & ~Qt.ItemIsEditable)
            displayname_item.setFlags(displayname_item.flags() & ~Qt.ItemIsEditable)
            info_item.setFlags(info_item.flags() & ~Qt.ItemIsEditable)
            replaced_item.setFlags(replaced_item.flags() & ~Qt.ItemIsEditable)

            # Set the row count based on the actual data
            self.table_widget.setRowCount(len(self.data_list))

            self.table_widget.insertRow(row)
            self.table_widget.setItem(row, 0, item)
            self.table_widget.setCellWidget(row, 0, checkbox)
            self.table_widget.setItem(row, 1, grouping_item)
            self.table_widget.setItem(row, 2, displayname_item)
            self.table_widget.setItem(row, 3, value_item)
            self.table_widget.setItem(row, 4, info_item)
            self.table_widget.setItem(row, 5, name_item)

    def toggle_rows(self):
        show_inherited = self.cb_show_inherited.isChecked()

        for row in range(self.table_widget.rowCount()):
            item = self.table_widget.item(row, 0)  # Assuming the column with 'user' is the sixth column
            if item is not None: print (item.text().lower())
            if item is not None and 'user' in item.text().lower():
                self.table_widget.setRowHidden(row, False)
                print ("found")
            else:
                self.table_widget.setRowHidden(row, not show_inherited)


    # Slot function to handle checkbox state changes
    def override_state_changed(self, row, data_dict, state):
        print(f"Override state changed - Row: {row}, Name: {data_dict['name']}, value: {data_dict['value']}, State: {state}")
        # Do something with the changed state and data_dict
    def do_something(self):
        box = self.sender()
        name = box.objectName()
        row, col = name.split('_')[1], name.split('_')[3]
        print(f'A checkbox was pressed: row {row}, column {col}')

    def update_table_on_model_change(self):
        # Get the selected model from the combo box
        self.set_edit_mode('Model')

        self.model_name = self.drp_models.currentText()

        if self.model_name != '':

            # Replace the following line with your actual XML reading logic
            self.model_type, self.model_pattern, self.data_list = self.read_single_model()
            print(f"\nmodel change for: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{self.device}\n")

            # Update the table with the new data
            self.drp_class.blockSignals(True)
            self.drp_class.setCurrentText(self.model_type)
            self.drp_class.blockSignals(False)

        else:
            print("model cleared")
            self.set_edit_mode('Class')
            old_model_type = self.model_type
            print(self.model_type)
            self.drp_class.setCurrentText('')
            self.drp_class.setCurrentText(old_model_type)

        self.table_widget.clear()
        self.setup_table()
        self.populate_table()
        self.toggle_rows()

    def update_table_on_class_change(self):
        # Get the selected model from the combo box
        self.drp_models.blockSignals(True)
        self.drp_models.setCurrentText('')
        self.model_name = ''
        self.drp_models.blockSignals(False)
        self.set_edit_mode('Class')
        self.model_type = self.drp_class.currentText()
        if self.model_type != '':

            # Replace the following line with your actual XML reading logic
            self.model_type, self.model_pattern, self.data_list = self.read_single_model()
            print(
                f"\nclass change for: {self.sim}  model: ---  pattern: {self.model_pattern}  class: {self.model_type}  device:{self.device}\n")

            # Update the table with the new data

            self.table_widget.clear()
            self.setup_table()
            self.populate_table()
            self.toggle_rows()
        else:
            print("class cleared")
            self.drp_class.setCurrentText('')
            self.set_edit_mode('Sim')
            old_sim = self.sim
            print(self.model_type)
            self.drp_sim.setCurrentText('Global')
            self.drp_sim.setCurrentText(old_sim)

        self.table_widget.clear()
        self.setup_table()
        self.populate_table()
        self.toggle_rows()

    def update_table_on_sim_change(self):
        # Get the selected sim from the radio buttons

        self.sim = self.drp_sim.currentText()

        if self.sim == 'Global':
            self.set_edit_mode('Global')
        else:
            self.set_edit_mode('Sim')

        self.setup_class_list()
        self.setup_model_list()


        # Replace the following line with your actual XML reading logic
        self.model_type, self.model_pattern, self.data_list = self.read_single_model()
        print(
            f"\nsim change for: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{self.device}\n")

        # Update the table with the new data

        self.table_widget.clear()
        self.setup_table()
        self.populate_table()
        self.toggle_rows()

    def create_datatype_item(self, datatype, value, unit, checkstate):
        #print(f"{datatype} {value}")
        if datatype == 'bool':
            toggle = QCheckBox()
            toggle.setChecked(value.lower() == 'true')
            #checkbox.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            toggle.setStyleSheet("margin-left:50%; margin-right:50%;")
            item = QTableWidgetItem()
            #item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            item.setData(Qt.CheckStateRole, Qt.Checked if value.lower() == 'true' else Qt.Unchecked)
            if not checkstate:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)   # no editing if not allowed in this mode
            return item
        elif datatype == 'int' or datatype == 'text' or datatype == 'float' or datatype == 'negfloat':
            line_edit = QLineEdit(str(value) + str(unit))
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
        state = False
        if '(user)' not in override_text:
            state = False
        else:
            match self.edit_mode:
                case 'Global': state = (override_text == 'Global (user)')
                case 'Sim': state = (override_text == 'Sim (user)')
                case 'Class': state = (override_text == 'Class (user)')
                case 'Model': state =(override_text == 'Model (user)')
            state = True
        return state

    def set_edit_mode(self,mode):
        oldmode = self.edit_mode

        if mode != oldmode:
            self.l_mode.setText(mode)
            self.edit_mode = mode
            self.setup_class_list()
            match mode:
                case 'Global':

                    self.drp_class.blockSignals(True)
                    self.drp_models.blockSignals(True)
                    self.drp_class.setCurrentText('')
                    self.drp_models.setCurrentText('')
                    self.model_name = ''
                    self.model_type = ''
                    self.drp_class.blockSignals(False)
                    self.drp_models.blockSignals(False)
                    self.drp_class.setEnabled(False)
                    self.drp_models.setEnabled(False)
                    self.b_createusermodel.setEnabled(False)
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
                    self.b_createusermodel.setEnabled(True)

                case 'Class':
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)
                    self.drp_models.blockSignals(True)
                    self.drp_models.setCurrentText('')
                    self.model_name = ''
                    self.drp_models.blockSignals(False)
                    self.b_createusermodel.setEnabled(True)

                case 'Model':
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)
                    self.b_createusermodel.setEnabled(True)

                case _:
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)

        print(f"{mode} Mode")


    def get_craft_attributes(file_path, sim, device):
        craft_attributes = set()
        craft_attributes.add('Aircraft')

        tree = ET.parse(file_path)
        root = tree.getroot()

        for defaults_elem in root.findall(f'.//defaults[{sim}="true"][{device}="true"]'):
            # for defaults_elem in root.findall(f'.//defaults[{sim}="true" and {device}="true"]'):
            for value_elem in defaults_elem.findall('.//value'):
                craft_attr = value_elem.get('Craft')
                if craft_attr is not None:
                    craft_attributes.add(craft_attr)

        return sorted(list(craft_attributes))

#  END GUI STUFF

    def read_xml_file(self, the_sim):
        tree = ET.parse(self.defaults_path)
        root = tree.getroot()

        # Collect data in a list of dictionaries
        data_list = []
        for defaults_elem in root.findall(f'.//defaults[{the_sim}="true"][{self.device}="true"]'):

            grouping = defaults_elem.find('Grouping').text
            name = defaults_elem.find('name').text
            # print(name)
            displayname = defaults_elem.find('displayname').text
            datatype = defaults_elem.find('datatype').text
            unit_elem = defaults_elem.find('unit')
            unit = unit_elem.text if unit_elem is not None else ""
            value_elem = defaults_elem.find('value')
            value = value_elem.text if value_elem is not None else ""
            if value is None: value = ""
            valid_elem = defaults_elem.find('validvalues')
            validvalues = valid_elem.text if valid_elem is not None else ""
            info_elem = defaults_elem.find('info')
            info = (f"{info_elem.text}") if info_elem is not None else ""

            if the_sim == 'Global':
                replaced = 'Global'
            else:
                replaced = 'Sim Default'

            # Store data in a dictionary
            data_dict = {
                'grouping': grouping,
                'name': name,
                'displayname': displayname,
                'value': value,
                'unit': unit,
                'datatype': datatype,
                'validvalues': validvalues,
                'replaced': replaced,
                'info': info
            }
            # print(data_dict)

            # Append to the list
            data_list.append(data_dict)
            # print(data_list)
        # Sort the data by grouping and then by name

        sorted_data = sorted(data_list, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['displayname']))
        # print(sorted_data)
        # printconfig(sim, craft, sorted_data)
        return sorted_data


    def skip_bad_combos(self, craft):
        if self.sim == 'DCS' and craft == 'HPGHelicopter': return True
        if self.sim == 'DCS' and craft == 'TurbopropAircraft': return True
        if self.sim == 'DCS' and craft == 'GliderAircraft': return True
        if self.sim == 'IL2' and craft == 'GliderAircraft': return True
        if self.sim == 'IL2' and craft == 'HPGHelicopter': return True
        if self.sim == 'IL2' and craft == 'Helicopter': return True
        if self.sim == 'IL2' and craft == 'TurbopropAircraft': return True

        if self.sim == 'Global' and craft != 'Aircraft': return True
        return False


    def printconfig(self, sorted_data):
        # print("printconfig: " +sorted_data)
        show_source = False
        print("#############################################")

        # Print the sorted data with group names and headers
        current_group = None
        current_header = None
        for item in sorted_data:
            if item['grouping'] != current_group:
                current_group = item['grouping']
                if current_header is not None:
                    print("\n\n")  # Separate sections with a blank line
                print(f"\n# {current_group}")
            tabstring = "\t\t"
            replacestring = ''
            if show_source:
                if item['replaced'] == "Global": replacestring = " G"
                if item['replaced'] == "Global (user)": replacestring = "UG"
                if item['replaced'] == "Sim Default": replacestring =  "SD"
                if item['replaced'] == "Sim (user)": replacestring = "UD"
                if item['replaced'] == "Class Default": replacestring = "SC"
                if item['replaced'] == "Class (user)": replacestring = "UC"
                if item['replaced'] == "Model Default": replacestring = "DM"
                if item['replaced'] == "Model (user)": replacestring = "UM"
            spacing = 50 - (len(item['name']) + len(item['value']) + len(item['unit']))
            space = " " * spacing + " # " + replacestring + " # "

            print(f"{tabstring}{item['name']} = {item['value']} {item['unit']} {space} {item['info']}")


    def read_models_data(self,file_path, full_model_name):
        # runs on both defaults and userconfig xml files
        tree = ET.parse(file_path)
        root = tree.getroot()

        model_data = []
        found_pattern = ''

        # Iterate through models elements
        #for model_elem in root.findall(f'.//models[sim="{self.sim}"][device="{self.device}"]'):
        for model_elem in root.findall(f'.//models[sim="{self.sim}"][device="{self.device}"]') + \
                          root.findall(f'.//models[sim="any"][device="{self.device}"]') + \
                          root.findall(f'.//models[sim="{self.sim}"][device="any"]') + \
                          root.findall(f'.//models[sim="any"][device="any"]'):

            # Assuming 'model' is the element containing the wildcard pattern

            unit_pattern = model_elem.find('model')
            if unit_pattern is not None:
                pattern = unit_pattern.text
                if pattern is not None:
                    # Check if the full_model_name matches the pattern using re.match
                    if re.match(pattern, full_model_name):
                        name = model_elem.find('name').text
                        value = model_elem.find('value').text
                        unit_elem = model_elem.find('unit')
                        unit = unit_elem.text if unit_elem is not None else ""
                        model_dict = {
                            'name': name,
                            'value': value,
                            'unit': unit
                        }
                        found_pattern = pattern
                        model_data.append(model_dict)

        return model_data, found_pattern

    def read_default_class_data(self, the_sim, the_class):
        tree = ET.parse(self.defaults_path)
        root = tree.getroot()

        class_data = []

        # Iterate through models elements
        #for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{self.device}"]'):
        #for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{self.device}"]'):
        for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{self.device}"]') + \
                          root.findall(f'.//classdefaults[sim="any"][type="{the_class}"][device="{self.device}"]') + \
                          root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="any"]') + \
                          root.findall(f'.//classdefaults[sim="any"][type="{the_class}"][device="any"]'):

            if model_elem.find('name') is not None:

                name = model_elem.find('name').text
                value = model_elem.find('value').text
                unit_elem = model_elem.find('unit')
                unit = unit_elem.text if unit_elem is not None else ""

                model_dict = {
                    'name': name,
                    'value': value,
                    'unit': unit,
                    'replaced': 'Class Default'
                }

                class_data.append(model_dict)

        return class_data

    def read_user_model_data(self, the_sim):
        tree = ET.parse(self.userconfig_path)
        root = tree.getroot()

        model_data = []

        # Iterate through models elements
        #for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{self.device}"]'):
        for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{self.device}"]') + \
                          root.findall(f'.//models[sim="any"][device="{self.device}"]') + \
                          root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                          root.findall(f'.//models[sim="any"][device="any"]'):
            if model_elem.find('type') is None:
                if model_elem.find('name') is not None:

                    setting = model_elem.find('name').text
                    value = model_elem.find('value').text


                    model_dict = {
                        'name': setting,
                        'value': value,
                        'unit': '',
                        'replaced': 'Model (user)'
                    }

                    model_data.append(model_dict)

        return model_data

    def read_user_sim_data(self, the_sim):
        tree = ET.parse(self.userconfig_path)
        root = tree.getroot()

        sim_data = []

        # Iterate through models elements
        #for model_elem in root.findall(f'.//simSettings[sim="{the_sim}" or sim="any"][device="{self.device}" or device="any"]'):
        for model_elem in root.findall(f'.//simSettings[sim="{the_sim}"][device="{self.device}"]') + \
                          root.findall(f'.//simSettings[sim="any"][device="{self.device}"]') + \
                          root.findall(f'.//simSettings[sim="{the_sim}"][device="any"]') + \
                          root.findall(f'.//simSettings[sim="any"][device="any"]'):

            if model_elem.find('name') is not None:

                name = model_elem.find('name').text
                value = model_elem.find('value').text
                if the_sim == 'Global':
                    replaced = 'Global'
                else:
                    replaced = 'Sim (user)'
                model_dict = {
                    'name': name,
                    'value': value,
                    'unit': '',
                    'replaced': replaced
                }

                sim_data.append(model_dict)

        return sim_data

    def read_user_class_data(self, the_sim, crafttype):
        tree = ET.parse(self.userconfig_path)
        root = tree.getroot()

        model_data = []

        # Iterate through models elements
        #for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{self.device}"]'):
        for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{self.device}"]') + \
                          root.findall(f'.//models[sim="any"][device="{self.device}"]') + \
                          root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                          root.findall(f'.//models[sim="any"][device="any"]'):
            if model_elem.find('type') is not None:
                # Assuming 'model' is the element containing the wildcard pattern
                pattern = model_elem.find('type').text

                if pattern is not None:
                    # Check if the craft type matches the pattern using re match
                    if re.match(pattern, crafttype):
                        name = model_elem.find('name').text
                        value = model_elem.find('value').text
                        unit_elem = model_elem.find('unit')
                        unit = unit_elem.text if unit_elem is not None else ""
                        model_dict = {
                            'name': name,
                            'value': value,
                            'unit': unit,
                            'replaced': 'Class (user)'
                        }

                        model_data.append(model_dict)

        return model_data

    def read_single_model(self, sim=None, aircraft_name=None):
        if sim is not None:
            self.sim = sim
        if aircraft_name is not None:
            self.model_name = aircraft_name
        print_counts = False
        print_each_step = False  # for debugging

        # Read models data first
        model_data, def_model_pattern = self.read_models_data(self.defaults_path, self.model_name)
        user_model_data, usr_model_pattern = self.read_models_data(self.userconfig_path, self.model_name)

        model_pattern = def_model_pattern
        if usr_model_pattern != '':
            model_pattern = usr_model_pattern

        # Extract the type from models data, if name is blank then use the class.  otherwise assume no type is set.
        if self.model_name == '':
            model_class = self.model_type
        else:
            model_class = ''   #self.model_type

        for model in model_data:
            if model['name'] == 'type':
                model_class = model['value']
                break
        # check if theres an override
        if user_model_data is not None:
            for model in user_model_data:
                if model['name'] == 'type':
                    model_class = model['value']
                    break
        print(f"class: {model_class}")


        # get default settings for all sims and device
        globaldata = self.read_xml_file('Global')

        if print_counts: print(f"globaldata count {len(globaldata)}")

        # see what we got
        if print_each_step:
            print(f"\nGlobal result: Global  type: ''  device:{self.device}\n")
            self.printconfig(globaldata)


        # get default Aircraft settings for this sim and device
        simdata = self.read_xml_file(self.sim)

        if print_counts:  print(f"simdata count {len(simdata)}")

        # see what we got
        if print_each_step:
            print(f"\nSimresult: {self.sim} type: ''  device:{self.device}\n")
            self.printconfig(simdata)

        # combine base stuff
        defaultdata = globaldata.copy()
        if self.sim != 'Global':
            for item in simdata: defaultdata.append(item)

        if print_counts:  print(f"defaultdata count {len(defaultdata)}")


        # get additional class default data
        if model_class != "":
            # Use the extracted type in read_xml_file
            craftresult = self.read_default_class_data(self.sim,model_class)

            if craftresult is not None:

                # merge if there is any
                default_craft_result = self.update_default_data_with_craft_result(defaultdata, craftresult)
            else:
                default_craft_result = defaultdata

            if print_counts:  print(f"default_craft_result count {len(default_craft_result)}")

            # see what we got
            if print_each_step:
                print(f"\nDefaultsresult: {self.sim} type: {model_class}  device:{self.device}\n")
                self.printconfig(default_craft_result)
        else:
            default_craft_result = defaultdata


        # get userconfig global overrides
        userglobaldata = self.read_user_sim_data( 'Global')
        if userglobaldata is not None:
            # merge if there is any
            def_craft_userglobal_result = self.update_data_with_models(default_craft_result, userglobaldata,'Global (user)')
        else:
            def_craft_userglobal_result = default_craft_result

        if print_counts:  print(f"def_craft_userglobal_result count {len(def_craft_userglobal_result)}")


        # get userconfig sim overrides
        if self.sim != 'Global':
            user_default_data = self.read_user_sim_data(self.sim)
            if user_default_data is not None:
                # merge if there is any
                def_craft_user_default_result = self.update_data_with_models(def_craft_userglobal_result, user_default_data, 'Sim (user)')
            else:
                def_craft_user_default_result = def_craft_userglobal_result

            if print_counts:  print(f"def_craft_user_default_result count {len(def_craft_user_default_result)}")
        else:
            def_craft_user_default_result = def_craft_userglobal_result

        if model_class != "":
            # get userconfg craft specific type overrides
            usercraftdata = self.read_user_class_data(self.sim, model_class)
            if usercraftdata is not None:
                # merge if there is any
                def_craft_usercraft_result = self.update_data_with_models(def_craft_user_default_result, usercraftdata, 'Class (user)')
            else:
                def_craft_usercraft_result = def_craft_user_default_result
        else:
            def_craft_usercraft_result = def_craft_user_default_result


        # Update result with default models data
        def_craft_models_result = self.update_data_with_models(def_craft_usercraft_result, model_data, 'Model Default')

        if print_counts:  print(f"def_craft_models count {len(def_craft_models_result)}")


        # finally get userconfig model specific overrides
        if user_model_data:
            final_result = self.update_data_with_models(def_craft_models_result, user_model_data, 'Model (user)')
        else:
            final_result = def_craft_models_result

        final_result = [item for item in final_result if item['value'] != '' or item['name'] == 'vpconf']

        sorted_data = sorted(final_result, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['name']))
        print(f"final count {len(final_result)}")
        return model_class, model_pattern, sorted_data


    def update_default_data_with_craft_result(self,defaultdata, craftresult):
        updated_defaultdata = defaultdata.copy()  # Create a copy to avoid modifying the original data


        #default_names = set(item['name'] for item in defaultdata)

        # Iterate through craftresult
        for craft_item in craftresult:
            name = craft_item['name']

            # Check if the item with the same name exists in defaultdata
            matching_item = next((item for item in updated_defaultdata if item['name'] == name), None)

            if matching_item:
                # If the item exists, update 'value' and 'unit'
                matching_item['value'] = craft_item['value']
                matching_item['unit'] = craft_item['unit']
                matching_item['replaced'] = "Class Default"  # Set the 'replaced' flag
            #else:
            # If the item doesn't exist, append it to defaultdata
                # no appending
                # defaultdata.append(craft_item)

        return updated_defaultdata

    def update_data_with_models(self,defaults_data, model_data, replacetext):
        updated_result = defaults_data.copy()

        # Create a dictionary mapping settings to their corresponding values and units
        model_dict = {model['name']: {'value': model['value'], 'unit': model['unit']} for model in model_data}

        for item in updated_result:
            name = item['name']

            # Check if the setting exists in the model_data
            if name in model_dict:
                # Update the value and unit in defaults_data with the values from model_data
                item['value'] = model_dict[name]['value']
                item['unit'] = model_dict[name]['unit']
                item['replaced'] = replacetext  # Set the 'replaced' text



        return updated_result
    def update_data_with_models_old(self,defaults_data, model_data, replacetext):
        updated_result = []

        # Create a dictionary mapping settings to their corresponding values and units
        model_dict = {model['name']: {'value': model['value'], 'unit': model['unit']} for model in model_data}

        for item in defaults_data:
            name = item['name']

            # Check if the setting exists in the model_data
            if name in model_dict:
                # Update the value and unit in defaults_data with the values from model_data
                item['value'] = model_dict[name]['value']
                item['unit'] = model_dict[name]['unit']
                item['replaced'] = replacetext  # Set the 'replaced' text

            updated_result.append(item)

        return updated_result

    def create_empty_userxml_file(self):
        # Create a backup directory if it doesn't exist
        # uncomment later when we use %localappdata%
        # os.makedirs(os.path.dirname(self.userconfig_path), exist_ok=True)

        if not os.path.isfile(self.userconfig_path):
            # Create an empty XML file with the specified root element
            root = ET.Element("TelemFFB")
            tree = ET.ElementTree(root)
            tree.write(self.userconfig_path)
            print(f"Empty XML file created at {self.userconfig_path}")
        else:
            print(f"XML file exists at {self.userconfig_path}")

    def read_models(self,the_sim):
        all_models = ['']
        tree = ET.parse(self.defaults_path)
        root = tree.getroot()
        #for model_elem in root.findall(f'.//models[sim="{mysim}"][device="{self.device}"]'):
        for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{self.device}"]') +\
            root.findall(f'.//models[sim="any"][device="{self.device}"]') +\
            root.findall(f'.//models[sim="{the_sim}"][device="any"]') +\
            root.findall(f'.//models[sim="any"][device="any"]'):

            pattern = model_elem.find('model')
            #print (pattern.text)
            if pattern is not None:
                if pattern.text not in all_models:
                    all_models.append(pattern.text)

        tree = ET.parse(self.userconfig_path)
        root = tree.getroot()
        #for model_elem in root.findall(f'.//models[sim="{mysim}"][device="{self.device}"]'):
        for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{self.device}"]') + \
                          root.findall(f'.//models[sim="any"][device="{self.device}"]') + \
                          root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                          root.findall(f'.//models[sim="any"][device="any"]'):

            pattern = model_elem.find('model')
            #print (pattern.text)
            if pattern is not None:
                if pattern.text not in all_models:
                    all_models.append(pattern.text)

        return all_models


class UserModelDialog(QDialog):
    def __init__(self, sim, current_aircraft, parent=None):
        super(UserModelDialog, self).__init__(parent)
        self.combo_box = None
        self.tb_current_aircraft = None
        self.setWindowTitle("Create Model Setting")
        self.init_ui(sim, current_aircraft)

    def init_ui(self,sim,current_aircraft):


        layout = QVBoxLayout()

        label1 = QLabel("TelemFFB uses regex to match aircraft names")
        label2 = QLabel("Name.* will match anything starting with 'Name'")
        label3 = QLabel("^Name$ will match only the exact 'Name'")
        label4 = QLabel("(The )?Name.* matches starting with 'Name' or 'The Name'" )
        label5 = QLabel("Edit the match pattern below.")

        label6 = QLabel("And choose the aircraft class:")

        classes = []
        match sim:
            case 'DCS':
                classes = ["PropellerAircraft", "JetAircraft", "Helicopter"]
            case 'IL2':
                classes = ["PropellerAircraft", "JetAircraft"]
            case 'MSFS':
                classes = ['PropellerAircraft', 'TurbopropAircraft', 'JetAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter']

        label_aircraft = QtWidgets.QLabel("Current Aircraft:")
        self.tb_current_aircraft = QtWidgets.QLineEdit()
        self.tb_current_aircraft.setText(current_aircraft)
        self.tb_current_aircraft.setAlignment(Qt.AlignHCenter)

        self.combo_box = QComboBox()
        self.combo_box.addItems(classes)
        self.combo_box.setStyleSheet("QComboBox::view-item { align-text: center; }")

        ok_button = QPushButton("OK")
        ok_button.setStyleSheet("text-align:center;")
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

        layout.addWidget(ok_button)
        layout.addWidget(cancel_button)

        self.setLayout(layout)

        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)


# #  for printing config.ini text file
# def print_all_defaults():    # none of this may work outside teh class
#     device = "joystick"
#     for the_sim in "Global", "DCS", "MSFS", "IL2":
#         crafts = SettingsWindow.get_craft_attributes(defaults_path, the_sim, device)
#         for craft in crafts:
#             skip = SettingsWindow.skip_bad_combos(the_sim, craft)
#             if skip == True: continue
#             mydata = SettingsWindow.read_xml_file(the_sim, craft)
#             # print("main: "+ mydata)
#             SettingsWindow.printconfig( mydata)


if __name__ == "__main__":
    # defaults_path = "defaults.xml"  # defaults file
    # userconfig_path = "userconfig.xml"  # user config overrides stored here, will move to %localappdata% at some point
    # # defaults_path = "debug_defaults.xml"  # defaults file
    # # userconfig_path = "debug_userconfig.xml"  # user config overrides stored here, will move to %localappdata% at some point
    # device = "joystick"  # joystick, pedals, collective.  essentially permanent per session

    # stuff below is fo printing config.ini, no longer used.

    # defaultdata = []
    # mydata = []
    #
    # # output a single aircraft class config
    #
    # defaultdata = read_xml_file(xml_file_path,sim,"Aircraft",device)
    # printconfig(sim, "Aircraft", defaultdata)
    #
    # #defaultdata = read_xml_file(xml_file_path,sim,crafttype,device)
    # #printconfig(sim, crafttype, defaultdata)

    # output a single model
    # model_type, model_pattern, mydata = read_single_model(defaults_path, sim, model_name, device, crafttype)

    # print(f"\nData for: {sim}  model: {model_name}  pattern: {model_pattern}  class: {model_type}  device:{device}\n")

    # printconfig(sim, model_type, mydata)

    # output all default configs for device
    # print_all_defaults()

    #  GUI stuff below

    app = QApplication(sys.argv)
    sw = SettingsWindow(defaults_path='defaults.xml', userconfig_path='userconfig.xml', device='joystick')

    sw.show()

    sys.exit(app.exec_())

