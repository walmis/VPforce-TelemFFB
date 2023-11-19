import xml.etree.ElementTree as ET
import sys
from PyQt5 import QtGui, QtWidgets, QtCore
from PyQt5.QtWidgets import (QApplication, QRadioButton, QButtonGroup, QTableWidget, QTableWidgetItem,
                             QSlider, QCheckBox, QLineEdit, QVBoxLayout, QWidget)
from PyQt5.QtCore import Qt
from settingswindow import Ui_SettingsWindow
import re


class settings_window(QtWidgets.QMainWindow, Ui_SettingsWindow):

    sim = ""                             # DCS, MSFS, IL2       -- set in get_current_model below
    model_name = "Airbus H160 Luxury"    # full model name with livery etc
    crafttype = ""                       # suggested, send whatever simconnect finds

    data_list = []

    model_type = ""     # holder for current type/class
    model_pattern = ""  # holder for current matching pattern found in config xmls
    edit_mode = '' # holder for current editing mode.

    print_each_step = False  #for debugging


    def __init__(self):
        super(settings_window, self).__init__()
        self.setupUi(self)  # This sets up the UI from Ui_SettingsWindow
        self.init_ui()

    def get_current_model(self):
        # in the future, get from simconnect.
        self.sim = "MSFS"
        self.model_name = self.tb_currentmodel.text()     #type value in box for testing. will set textbox in future
        self.crafttype = "Helicopter"  # suggested, send whatever simconnect finds
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
        self.drp_class.blockSignals(True)

        if self.model_pattern != '':
            #self.rb_model.setChecked(True)
            self.set_edit_mode('Model')
            self.drp_models.blockSignals(True)
            self.drp_models.setCurrentText(self.model_pattern)
            self.drp_models.blockSignals(True)
        else:
            self.set_edit_mode('Class')

        print(f"\nCurrent: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{device}\n")

        # put model name and class into UI

        if self.model_type != '': self.drp_class.setCurrentText(self.model_type)

        self.populate_table()

    def init_ui(self):

        self.tb_currentmodel.setText(self.model_name)

        self.get_current_model()
        self.b_getcurrentmodel.clicked.connect(self.get_current_model)


        print (f"init {self.sim}")
        # Your custom logic for table setup goes here
        self.setup_table()

        # Connect the stateChanged signal of the checkbox to the toggle_rows function
        self.cb_show_inherited.stateChanged.connect(self.toggle_rows)


        self.l_device.setText(device)

        # read models from xml files to populate dropdown
        self.setup_model_list()
        self.setup_class_list()

        # allow changing sim dropdown
        self.drp_sim.currentIndexChanged.connect(lambda index: self.update_table_on_sim_change())
        # change class dropdown
        self.drp_class.currentIndexChanged.connect(lambda index: self.update_table_on_class_change())
        #allow changing model dropdown
        self.drp_models.currentIndexChanged.connect(lambda index: self.update_table_on_model_change())

        # Initial visibility of rows based on checkbox state
        self.toggle_rows()

    def setup_class_list(self):
        self.drp_class.blockSignals(True)
        print_debug = False

        match self.sim:
            case 'Global':
                # Assuming drp_class is your QComboBox
                for disable in {'PropellerAircraft', 'TurbopropAircraft', 'JetAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter'}:
                    if print_debug: print (f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)
                self.drp_class.model().item(self.drp_class.findText('(None - Global)')).setEnabled(True)

            case 'DCS':
                for disable in {'(None - Global)', 'TurbopropAircraft', 'GliderAircraft', 'HPGHelicopter'}:
                    if print_debug: print(f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)
                for enable in {'PropellerAircraft', 'JetAircraft', 'Helicopter'}:
                    if print_debug: print(f"enable {enable}")
                    self.drp_class.model().item(self.drp_class.findText(enable)).setEnabled(True)

            case 'IL2':
                for disable in {'(None - Global)', 'TurbopropAircraft', 'GliderAircraft', 'Helicopter', 'HPGHelicopter'}:
                    if print_debug: print(f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)
                for enable in {'PropellerAircraft', 'JetAircraft'}:
                    if print_debug: print(f"enable {enable}")
                    self.drp_class.model().item(self.drp_class.findText(enable)).setEnabled(True)

            case 'MSFS':
                for disable in {'(None - Global)'}:
                    if print_debug: print(f"disable {disable}")
                    self.drp_class.model().item(self.drp_class.findText(disable)).setEnabled(False)
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
        headers = ['Override', 'Grouping', 'Display Name', 'Value', 'Info', "Source"]
        self.table_widget.setHorizontalHeaderLabels(headers)
        self.table_widget.setColumnWidth(0, 60)
        self.table_widget.setColumnWidth(1, 120)
        self.table_widget.setColumnWidth(2, 200)
        self.table_widget.setColumnWidth(3, 100)
        self.table_widget.setColumnWidth(4, 280)  #make 380 later
        self.table_widget.setColumnWidth(5, 100)

    def populate_table(self):
        for row, data_dict in enumerate(self.data_list):
            override_item = self.create_override_item(data_dict['replaced'])
            grouping_item = QTableWidgetItem(data_dict['grouping'])
            displayname_item = QTableWidgetItem(data_dict['displayname'])
            value_item = self.create_datatype_item(data_dict['datatype'], data_dict['value'], data_dict['unit'], override_item.checkState())
            info_item = QTableWidgetItem(data_dict['info'])
            replaced_item = QTableWidgetItem(data_dict['replaced'])
            unit_item = QTableWidgetItem(data_dict['unit'])

            #print(f"Row {row} - Grouping: {data_dict['grouping']}, Display Name: {data_dict['displayname']}, Unit: {data_dict['unit']}, Ovr: {data_dict['replaced']}")


            # Check if replaced is an empty string and set text color accordingly
            for item in [grouping_item, displayname_item, value_item, info_item, replaced_item]:
                match data_dict['replaced']:
                    case 'Global':
                        item.setForeground(QtGui.QColor('gray'))
                    case 'Global (user)':
                        item.setForeground(QtGui.QColor('black'))
                    case 'Sim Default':
                        item.setForeground(QtGui.QColor('blue'))
                    case 'Sim (user)':
                        item.setForeground(QtGui.QColor('green'))
                    case 'Class Default':
                        item.setForeground(QtGui.QColor('darkBlue'))
                    case 'Class (user)':
                        item.setForeground(QtGui.QColor('darkGreen'))
                    case 'Model Default':
                        item.setForeground(QtGui.QColor('magenta'))
                    case 'Model (user)':
                        item.setForeground(QtGui.QColor('darkMagenta'))

            # Make specific columns read-only
            grouping_item.setFlags(grouping_item.flags() & ~Qt.ItemIsEditable)
            displayname_item.setFlags(displayname_item.flags() & ~Qt.ItemIsEditable)
            info_item.setFlags(info_item.flags() & ~Qt.ItemIsEditable)
            replaced_item.setFlags(replaced_item.flags() & ~Qt.ItemIsEditable)

            # Set the row count based on the actual data
            self.table_widget.setRowCount(len(self.data_list))

            self.table_widget.insertRow(row)
            self.table_widget.setItem(row, 0, override_item)
            self.table_widget.setItem(row, 1, grouping_item)
            self.table_widget.setItem(row, 2, displayname_item)
            self.table_widget.setItem(row, 3, value_item)
            self.table_widget.setItem(row, 4, info_item)
            self.table_widget.setItem(row, 5, replaced_item)
            #print(f"{override_item.text()}\t{grouping_item.text()}\t{displayname_item.text()}\t{value_item.text()}\t{replaced_item.text()}")
         #   print(f"Row {row} added to the table.")

    def toggle_rows_old(self):
        show_inherited = self.cb_show_inherited.isChecked()

        for row in range(self.table_widget.rowCount()):
            replaced_item = self.table_widget.item(row, 5)  # Assuming source is in the second column
            if replaced_item is not None:

                is_editable = replaced_item.foreground().color() == QtGui.QColor('gray')
                self.table_widget.setRowHidden(row, not show_inherited and is_editable)

    def toggle_rows(self):
        show_inherited = self.cb_show_inherited.isChecked()

        for row in range(self.table_widget.rowCount()):
            checkbox_item = self.table_widget.item(row, 0)  # Assuming the checkbox is in the first column
            if checkbox_item is not None and checkbox_item.checkState() != Qt.Checked:
                self.table_widget.setRowHidden(row, not show_inherited)


    def update_table_on_model_change(self):
        # Get the selected model from the combo box
        self.set_edit_mode('Model')

        self.model_name = self.drp_models.currentText()

        if self.model_name != '':

            # Replace the following line with your actual XML reading logic
            self.model_type, self.model_pattern, self.data_list = self.read_single_model()
            print(f"\nmodel change for: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{device}\n")

            # Update the table with the new data
            self.drp_class.blockSignals(True)
            self.drp_class.setCurrentText(self.model_type)
            self.drp_class.blockSignals(False)

        else:
            self.set_edit_mode('Class')
            old_model_type = self.model_type
            print(self.model_type)
            self.drp_class.setCurrentText('(None - Sim Default)')
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
                f"\nclass change for: {self.sim}  model: ---  pattern: {self.model_pattern}  class: {self.model_type}  device:{device}\n")

            # Update the table with the new data

            self.table_widget.clear()
            self.setup_table()
            self.populate_table()
            self.toggle_rows()
        else:
            self.drp_class.setCurrentText('(None - Sim Default)')
            self.set_edit_mode('Sim')


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
            f"\nsim change for: {self.sim}  model: {self.model_name}  pattern: {self.model_pattern}  class: {self.model_type}  device:{device}\n")

        # Update the table with the new data

        self.table_widget.clear()
        self.setup_table()
        self.populate_table()
        self.toggle_rows()

    def create_datatype_item(self, datatype, value, unit, checkstate):
        #print(f"{datatype} {value}")
        if datatype == 'bool':
            checkbox = QCheckBox()
            checkbox.setChecked(value.lower() == 'true')
            #checkbox.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            checkbox.setStyleSheet("margin-left:50%; margin-right:50%;")
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

    def create_override_item(self, override):
        #print(f"{override}")

        checkbox = QCheckBox()
        if '(user)' not in override:
            checkbox.setChecked(False)
        else:
            match self.edit_mode:
                case 'Global': checkbox.setChecked(override == 'Global (user)')
                case 'Sim': checkbox.setChecked(override == 'Sim (user)')
                case 'Class': checkbox.setChecked(override == 'Class (user)')
                case 'Model': checkbox.setChecked(override == 'Model (user)')

        checkbox.setStyleSheet("margin-left:50%; margin-right:50%;")    #why no worky
        item = QTableWidgetItem()
        item.setData(Qt.CheckStateRole, Qt.Checked if checkbox.checkState() else Qt.Unchecked)
        return item

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
                    self.drp_class.setCurrentText('(None - Global)')
                    self.drp_models.setCurrentText('')
                    self.model_name = ''
                    self.model_type = ''
                    self.drp_class.blockSignals(False)
                    self.drp_models.blockSignals(False)
                    self.drp_class.setEnabled(False)
                    self.drp_models.setEnabled(False)
                case 'Sim':
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)
                    self.drp_class.blockSignals(True)
                    self.drp_models.blockSignals(True)
                    self.drp_class.setCurrentText('(None - Sim Default)')
                    self.drp_models.setCurrentText('')
                    self.model_name = ''
                    self.model_type = ''
                    self.drp_class.blockSignals(False)
                    self.drp_models.blockSignals(False)

                case 'Class':
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)
                    self.drp_models.blockSignals(True)
                    self.drp_models.setCurrentText('(None - Class Default)')
                    self.model_name = ''
                    self.drp_models.blockSignals(False)

                case 'Model':
                    self.drp_class.setEnabled(True)
                    self.drp_models.setEnabled(True)

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

    def read_xml_file(self, the_sim, craft):
        tree = ET.parse(defaults_path)
        root = tree.getroot()

        # Collect data in a list of dictionaries
        data_list = []
        for defaults_elem in root.findall(f'.//defaults[{the_sim}="true"][{device}="true"]'):

            grouping = defaults_elem.find('Grouping').text
            name = defaults_elem.find('name').text
            # print(name)
            displayname = defaults_elem.find('displayname').text
            datatype = defaults_elem.find('datatype').text
            unit_elem = defaults_elem.find('unit')
            unit = unit_elem.text if unit_elem is not None else ""
            valid_elem = defaults_elem.find('validvalues')
            validvalues = valid_elem.text if valid_elem is not None else ""

            # Check if the 'value' element has the 'Craft' attribute
            value_elem_craft = defaults_elem.find(f".//value[@Craft='{craft}']")
            value_craft = value_elem_craft.text if value_elem_craft is not None and value_elem_craft.get(
                'Craft') == craft else None

            # If 'value' element without 'Craft' attribute exists, use that in 'Aircraft' section
            if value_craft is None and craft == 'Default' and 'Craft' not in defaults_elem.attrib:
                value_elem_craft = defaults_elem.find('value')
                value_craft = value_elem_craft.text if value_elem_craft is not None and 'Craft' not in value_elem_craft.attrib else None

            # Store data in a dictionary
            if value_craft is not None:

                info_elem = defaults_elem.find('info')
                info = (f"{info_elem.text}") if info_elem is not None else ""

                data_dict = {
                    'grouping': grouping,
                    'name': name,
                    'displayname': displayname,
                    'value': value_craft,
                    'unit': unit,
                    'datatype': datatype,
                    'validvalues': validvalues,
                    'replaced': "",
                    'info': info
                }
                # print(data_dict)

                # Append to the list
                data_list.append(data_dict)
                # print(data_list)
        # Sort the data by grouping and then by name

        sorted_data = sorted(data_list, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['name']))
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
        tree = ET.parse(file_path)
        root = tree.getroot()

        model_data = []
        found_pattern = ''

        # Iterate through models elements
        for model_elem in root.findall(f'.//models[sim="{self.sim}"][device="{device}"]'):
            # Assuming 'model' is the element containing the wildcard pattern

            unit_pattern = model_elem.find('model')
            if unit_pattern is not None:
                pattern = unit_pattern.text
                if pattern is not None:
                    # Check if the full_model_name matches the pattern using re.match
                    if re.match(pattern, full_model_name):
                        setting = model_elem.find('setting').text
                        value = model_elem.find('value').text
                        unit_elem = model_elem.find('unit')
                        unit = unit_elem.text if unit_elem is not None else ""
                        model_dict = {
                            'setting': setting,
                            'value': value,
                            'unit': unit
                        }
                        found_pattern = pattern
                        model_data.append(model_dict)

        return model_data, found_pattern


    def read_user_craft_data(self, the_sim, crafttype):
        tree = ET.parse(userconfig_path)
        root = tree.getroot()

        model_data = []

        # Iterate through models elements
        for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{device}"]'):
            if model_elem.find('type') is not None:
                # Assuming 'model' is the element containing the wildcard pattern
                pattern = model_elem.find('type').text

                if pattern is not None:
                    # Check if the craft type matches the pattern using re match
                    if re.match(pattern, crafttype):
                        setting = model_elem.find('setting').text
                        value = model_elem.find('value').text
                        unit_elem = model_elem.find('unit')
                        unit = unit_elem.text if unit_elem is not None else ""
                        model_dict = {
                            'setting': setting,
                            'value': value,
                            'unit': unit
                        }

                        model_data.append(model_dict)

        return model_data

    def read_single_model(self):
        print_counts = False
        # Read models data first
        model_data, def_model_pattern = self.read_models_data(defaults_path, self.model_name)
        user_model_data, usr_model_pattern = self.read_models_data(userconfig_path, self.model_name)

        model_pattern = def_model_pattern
        if usr_model_pattern != '':
            model_pattern = usr_model_pattern

        if 'print_each_step' not in locals():
            print_each_step = False

        # Extract the type from models data, if name is blank then use the class.  otherwise assume no type is set.
        if self.model_name == '':
            model_class = self.model_type
        else:
            model_class = '(None - Sim Default)'   #self.model_type

        for model in model_data:
            if model['setting'] == 'type':
                model_class = model['value']
                break
        # check if theres an override
        if user_model_data is not None:
            for model in user_model_data:
                if model['setting'] == 'type':
                    model_class = model['value']
                    break
        print(f"class: {model_class}")


        # get default settings for all sims and device
        globaldata = self.read_xml_file('Global', 'Default')
        for g in globaldata: g['replaced'] = 'Global'


        if print_counts: print(f"globaldata count {len(globaldata)}")

        # see what we got
        if self.print_each_step:
            print(f"\nGlobal result: Global  type: ''  device:{device}\n")
            self.printconfig(globaldata)

        # get default Aircraft settings for this sim and device

        simdata = self.read_xml_file(self.sim, 'Default')
        for s in simdata:  s['replaced'] = 'Sim Default'
        if print_counts:  print(f"simdata count {len(simdata)}")

        # see what we got
        if self.print_each_step:
            print(f"\nSimresult: {self.sim} type: Default  device:{device}\n")
            self.printconfig(simdata)

        # combine base stuff
        defaultdata = globaldata
        if self.sim != 'Global':
            for item in simdata: defaultdata.append(item)

        if print_counts:  print(f"defaultdata count {len(defaultdata)}")

        # get additional class default data
        if model_class != "":
            # Use the extracted type in read_xml_file
            craftresult = self.read_xml_file(self.sim,model_class)

            if craftresult is not None:
                for c in craftresult:  c['replaced'] = 'Class Default'
                # see what we got
                if print_each_step:
                    print(f"\nCraftresult: {self.sim} type: {model_class}  device:{device}\n")
                    self.printconfig(craftresult)

                # merge if there is any
                default_craft_result = self.update_default_data_with_craft_result(defaultdata, craftresult)
            else:
                default_craft_result = defaultdata

            if print_counts:  print(f"default_craft_result count {len(default_craft_result)}")

            # see what we got
            if print_each_step:
                print(f"\nDefaultsresult: {self.sim} type: {model_class}  device:{device}\n")
                self.printconfig(default_craft_result)
        else:
            default_craft_result = defaultdata

        # get userconfig aircraft type overrides
        userglobaldata = self.read_user_craft_data( 'Global', 'Default')
        if userglobaldata is not None:
            # merge if there is any
            def_craft_userglobal_result = self.update_data_with_models(default_craft_result, userglobaldata,'Global (user)')
        else:
            def_craft_userglobal_result = defaultdata

        if print_counts:  print(f"def_craft_userglobal_result count {len(def_craft_userglobal_result)}")

        # get userconfig default type overrides
        if self.sim != 'Global':
            user_default_data = self.read_user_craft_data(self.sim, 'Default')
            if user_default_data is not None:
                # merge if there is any
                def_craft_user_default_result = self.update_data_with_models(def_craft_userglobal_result, user_default_data, 'Sim (user)')
            else:
                def_craft_user_default_result = defaultdata

            if print_counts:  print(f"def_craft_user_default_result count {len(def_craft_user_default_result)}")
        else:
            def_craft_user_default_result = defaultdata

        if model_class != "":
            # get userconfg craft specific type overrides
            usercraftdata = self.read_user_craft_data(self.sim, model_class)
            if usercraftdata is not None:
                # merge if there is any
                def_craft_usercraft_result = self.update_data_with_models(def_craft_user_default_result, usercraftdata, 'Class (user)')
            else:
                def_craft_usercraft_result = defaultdata
        else:
            def_craft_usercraft_result = defaultdata

        # Update result with default models data
        def_craft_models_result = self.update_data_with_models(def_craft_usercraft_result, model_data, 'Model Default')

        if print_counts:  print(f"def_craft_models count {len(def_craft_models_result)}")

        # finally get userconfig model specific overrides
        if user_model_data:
            final_result = self.update_data_with_models(def_craft_models_result, user_model_data, 'Model (user)')
        else:
            final_result = def_craft_models_result
        sorted_data = sorted(final_result, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['name']))
        print(f"final count {len(final_result)}")
        return model_class, model_pattern, sorted_data


    def update_default_data_with_craft_result(self,defaultdata, craftresult):
        # updated_defaultdata = defaultdata.copy()  # Create a copy to avoid modifying the original data
        updated_defaultdata = []
        # Create a dictionary mapping names to their corresponding values and units
        craftresult_dict = {item['name']: {'value': item['value'], 'unit': item['unit']} for item in craftresult}

        for item in defaultdata:
            name = item['name']

            # Check if the name exists in the craftresult
            if name in craftresult_dict:
                # Update the value and unit in defaultdata with the values from craftresult
                item['value'] = craftresult_dict[name]['value']
                item['unit'] = craftresult_dict[name]['unit']
                item['replaced'] = "Class Default"  # Set the 'replaced' flag

            updated_defaultdata.append(item)

        # Create a dictionary mapping names to their corresponding values and units
        # updatedresult_dict = {item['name']: {'value': item['value'], 'unit': item['unit']} for item in updated_defaultdata}
        #
        # for item in craftresult:
        #     name = item['name']
        #
        #     # print (item['name'])
        #     #    print(f'name: {name}')
        #
        #     if name not in updatedresult_dict:
        #         updated_defaultdata.append(item)

        return updated_defaultdata


    def update_data_with_models(self,defaults_data, model_data, replacetext):
        updated_result = []

        # Create a dictionary mapping settings to their corresponding values and units
        model_dict = {model['setting']: {'value': model['value'], 'unit': model['unit']} for model in model_data}

        for item in defaults_data:
            setting = item['name']

            # Check if the setting exists in the model_data
            if setting in model_dict:
                # Update the value and unit in defaults_data with the values from model_data
                item['value'] = model_dict[setting]['value']
                item['unit'] = model_dict[setting]['unit']
                item['replaced'] = replacetext  # Set the 'replaced' text

            updated_result.append(item)

        return updated_result


    def read_models(self,mysim):
        all_models = ['']
        tree = ET.parse(defaults_path)
        root = tree.getroot()
        for model_elem in root.findall(f'.//models[sim="{mysim}"][device="{device}"]'):

            pattern = model_elem.find('model')
            #print (pattern.text)
            if pattern is not None:
                if pattern.text not in all_models:
                    all_models.append(pattern.text)

        tree = ET.parse(userconfig_path)
        root = tree.getroot()
        for model_elem in root.findall(f'.//models[sim="{mysim}"][device="{device}"]'):
            pattern = model_elem.find('model')
            #print (pattern.text)
            if pattern is not None:
                if pattern.text not in all_models:
                    all_models.append(pattern.text)

        return all_models

# #  for printing config.ini text file
# def print_all_defaults():    # none of this may work outside teh class
#     device = "joystick"
#     for the_sim in "Global", "DCS", "MSFS", "IL2":
#         crafts = settings_window.get_craft_attributes(defaults_path, the_sim, device)
#         for craft in crafts:
#             skip = settings_window.skip_bad_combos(the_sim, craft)
#             if skip == True: continue
#             mydata = settings_window.read_xml_file(the_sim, craft)
#             # print("main: "+ mydata)
#             settings_window.printconfig( mydata)


if __name__ == "__main__":
    # defaults_path = "defaults.xml"  # defaults file
    # userconfig_path = "userconfig.xml"  # user config overrides stored here, will move to %localappdata% at some point
    defaults_path = "debug_defaults.xml"  # defaults file
    userconfig_path = "debug_userconfig.xml"  # user config overrides stored here, will move to %localappdata% at some point
    device = "joystick"  # joystick, pedals, collective.  essentially permanent per session


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
    SettingsWindow = settings_window()
    # ui = Ui_SettingsWindow()         # ui setup in class
    # ui.setupUi(SettingsWindow)
    SettingsWindow.show()

    sys.exit(app.exec_())

