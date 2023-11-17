import xml.etree.ElementTree as ET
import sys
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QSlider, QCheckBox, QLineEdit, QVBoxLayout, \
    QWidget
from PyQt5.QtCore import Qt
from settingswindow import Ui_SettingsWindow
import re


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


def read_xml_file(file_path, sim, craft, device):
    tree = ET.parse(file_path)
    root = tree.getroot()

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in root.findall(f'.//defaults[{sim}="true"][{device}="true"]'):

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
        if value_craft is None and craft == 'Aircraft' and 'Craft' not in defaults_elem.attrib:
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


def skip_bad_combos(sim, craft):
    if sim == 'DCS' and craft == 'HPGHelicopter': return True
    if sim == 'DCS' and craft == 'TurbopropAircraft': return True
    if sim == 'DCS' and craft == 'GliderAircraft': return True
    if sim == 'IL2' and craft == 'GliderAircraft': return True
    if sim == 'IL2' and craft == 'HPGHelicopter': return True
    if sim == 'IL2' and craft == 'Helicopter': return True
    if sim == 'IL2' and craft == 'TurbopropAircraft': return True

    return False


def printconfig(sim, craft, sorted_data):
    # print("printconfig: " +sorted_data)

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
        if item['replaced'] == "byCraft": tabstring = "    C   "
        if item['replaced'] == "byModel": tabstring = "    M   "
        if item['replaced'] == "usrAircf": tabstring = "   UA   "
        if item['replaced'] == "usrCraft": tabstring = "   UC   "
        if item['replaced'] == "usrModel": tabstring = "   UM   "
        spacing = 50 - (len(item['name']) + len(item['value']) + len(item['unit']))
        space = " " * spacing + " # "

        print(f"{tabstring}{item['name']} = {item['value']} {item['unit']} {space} {item['info']}")


def read_models_data(file_path, sim, full_model_name, device):
    tree = ET.parse(file_path)
    root = tree.getroot()

    model_data = []
    found_pattern = ''

    # Iterate through models elements
    for model_elem in root.findall(f'.//models[sim="{sim}"][device="{device}"]'):
        # Assuming 'model' is the element containing the wildcard pattern

        unit_pattern = model_elem.find('model')
        if unit_pattern is not None:
            pattern = unit_pattern.text
            if pattern is not None:
                # Check if the full_model_name matches the pattern using fnmatch
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


def read_user_craft_data(file_path, sim, crafttype, device):
    tree = ET.parse(file_path)
    root = tree.getroot()

    model_data = []

    # Iterate through models elements
    for model_elem in root.findall(f'.//models[sim="{sim}"][device="{device}"]'):
        if model_elem.find('type') is not None:
            pattern = model_elem.find('type').text  # Assuming 'model' is the element containing the wildcard pattern

            # Check if the full_model_name matches the pattern using re metch
            if pattern is not None:
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


def print_all_defaults():
    device = "joystick"
    for sim in "global", "DCS", "MSFS", "IL2":
        crafts = get_craft_attributes(xml_file_path, sim, device)
        for craft in crafts:
            skip = skip_bad_combos(sim, craft)
            if skip == True: continue
            mydata = read_xml_file(xml_file_path, sim, craft, device)
            # print("main: "+ mydata)
            printconfig(sim, craft, mydata)


def read_single_model(xml_file_path, sim, modelname, device, suggested_class="", userconfig_path='userconfig.xml'):
    # Read models data first
    model_data, def_model_pattern = read_models_data(xml_file_path, sim, modelname, device)
    user_model_data, usr_model_pattern = read_models_data(userconfig_path, sim, modelname, device)

    model_pattern = def_model_pattern
    if usr_model_pattern != '':
        model_pattern = usr_model_pattern

    if 'print_each_step' not in locals():
        print_each_step = False
    # Extract the type from models data
    model_class = suggested_class
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
    # get default Aircraft settings for all sims and device
    globaldata = read_xml_file(xml_file_path, 'global', 'Aircraft', device)

    # see what we got
    if print_each_step:
        print(f"\nGlobalresult: Global  type: Aircraft  device:{device}\n")
        printconfig('global', 'Aircraft', globaldata)

    # get default Aircraft settings for this sim and device
    simdata = read_xml_file(xml_file_path, sim, 'Aircraft', device)

    # see what we got
    if print_each_step:
        print(f"\nSimresult: {sim} type: Aircraft  device:{device}\n")
        printconfig(sim, 'Aircraft', simdata)

    # combine base stuff
    defaultdata = globaldata
    for item in simdata:
        defaultdata.append(item)

    # get additional class default data
    if model_class != "":
        # Use the extracted type in read_xml_file
        craftresult = read_xml_file(xml_file_path, sim, model_class, device)

        if craftresult is not None:
            # see what we got
            if print_each_step:
                print(f"\nCraftresult: {sim} type: {model_class}  device:{device}\n")
                printconfig(sim, model_class, craftresult)

            # merge if there is any
            default_craft_result = update_default_data_with_craft_result(defaultdata, craftresult)
        else:
            default_craft_result = defaultdata

        # see what we got
        if print_each_step:
            print(f"\nDefaultsresult: {sim} type: Aircraft  device:{device}\n")
            printconfig(sim, model_class, default_craft_result)
    else:
        default_craft_result = defaultdata

    # get userconfig aircraft type overrides
    userairplanedata = read_user_craft_data(userconfig_path, sim, 'Aircraft', device)
    if userairplanedata is not None:
        # merge if there is any
        def_craft_useraircft_result = update_data_with_models(default_craft_result, userairplanedata, 'usrAircf')
    else:
        def_craft_useraircft_result = defaultdata

    if model_class != "":
        # get userconfg craft specific type overrides
        usercraftdata = read_user_craft_data(userconfig_path, sim, model_class, device)
        if usercraftdata is not None:
            # merge if there is any
            def_craft_usercraft_result = update_data_with_models(def_craft_useraircft_result, usercraftdata, 'usrCraft')

    else:
        def_craft_usercraft_result = defaultdata

    # Update result with default models data
    def_craft_models_result = update_data_with_models(def_craft_usercraft_result, model_data, 'byModel')

    # finally get userconfig model specific overrides

    if user_model_data:
        final_result = update_data_with_models(def_craft_models_result, user_model_data, 'usrModel')
    else:
        final_result = def_craft_models_result
    sorted_data = sorted(final_result, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['name']))
    return model_class, model_pattern, sorted_data


def update_default_data_with_craft_result(defaultdata, craftresult):
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
            item['replaced'] = "byCraft"  # Set the 'replaced' flag

        updated_defaultdata.append(item)

    # Create a dictionary mapping names to their corresponding values and units
    updatedresult_dict = {item['name']: {'value': item['value'], 'unit': item['unit']} for item in updated_defaultdata}

    for item in craftresult:
        name = item['name']

        # print (item['name'])
        #    print(f'name: {name}')

        if name not in updatedresult_dict:
            updated_defaultdata.append(item)

    return updated_defaultdata


def update_data_with_models(defaults_data, model_data, replacetext):
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


def read_models(sim, device, defaults_path, userconfig_path):
    all_models = []

    # Read default models
    default_tree = ET.parse(defaults_path)
    default_root = default_tree.getroot()
    default_models = extract_models(sim, device, default_root)
    all_models.extend(default_models)

    # Read userconfig models
    userconfig_tree = ET.parse(userconfig_path)
    userconfig_root = userconfig_tree.getroot()
    userconfig_models = extract_models(sim, device, userconfig_root)
    all_models.extend(userconfig_models)

    return all_models

def extract_models(sim, device, xml_root):
    models = []

    # Assuming models are under a specific tag, adjust as needed
    model_elements = xml_root.findall('.//models')

    for model_element in model_elements:
        # Extract model information based on sim and device
        model_sim = model_element.find('sim').text
        model_device = model_element.find('device').text

        if model_sim == sim and model_device == device:
            model_text = model_element.find('model').text if model_element.find('model') is not None else None
            if model_text is not None:
                if model_text not in models:
                    models.append(model_text)

    return models


#######   GUI STUFF BELOW


class settings_window(QtWidgets.QMainWindow, Ui_SettingsWindow):
    def __init__(self, data_list):
        super(settings_window, self).__init__()
        self.setupUi(self)  # This sets up the UI from Ui_SettingsWindow
        self.data_list = data_list
        self.init_ui()

    def init_ui(self):
        # Your custom logic for table setup goes here
        self.table_widget.setColumnCount(5)
        headers = ['Override', 'Grouping', 'Display Name', 'Value', 'Info']
        self.table_widget.setHorizontalHeaderLabels(headers)
        self.table_widget.setColumnWidth( 0, 60)

        self.table_widget.setColumnWidth( 1, 120)
        self.table_widget.setColumnWidth( 2, 200)
        self.table_widget.setColumnWidth( 3, 100)
        self.table_widget.setColumnWidth( 4, 400)

        # Connect the stateChanged signal of the checkbox to the toggle_rows function
        self.cb_show_inherited.stateChanged.connect(self.toggle_rows)

        # Initial visibility of rows based on checkbox state
        self.toggle_rows()

        self.l_device.setText(device)

        # read models from xml files to populate dropdown
        models = read_models(sim, device, xml_file_path, userconfig_path)
        self.drp_models.clear()
        self.drp_models.addItems(models)
        self.drp_models.setCurrentText(model_pattern)

        # put model name and class into UI
        self.tb_currentmodel.setText(model_name)
        self.drp_class.setCurrentText(model_type)

        #allow changing model
        self.drp_models.currentIndexChanged.connect(self.update_table_on_model_change)


        self.populate_table()

    def populate_table(self):
        for row, data_dict in enumerate(self.data_list):
            override_item = self.create_override_item(data_dict['replaced'])
            grouping_item = QTableWidgetItem(data_dict['grouping'])
            displayname_item = QTableWidgetItem(data_dict['displayname'])
            value_item = self.create_datatype_item(data_dict['datatype'], data_dict['value'], data_dict['unit'])
            info_item = QTableWidgetItem(data_dict['replaced'])
            unit_item = QTableWidgetItem(data_dict['unit'])

            #print(f"Row {row} - Grouping: {data_dict['grouping']}, Display Name: {data_dict['displayname']}, Unit: {data_dict['unit']}, Ovr: {data_dict['replaced']}")




          #   if item['replaced'] == "byCraft": tabstring = "    C   "
          #   if item['replaced'] == "byModel": tabstring = "    M   "
          #   if item['replaced'] == "usrAircf": tabstring = "   UA   "
          #   if item['replaced'] == "usrCraft": tabstring = "   UC   "
          #   if item['replaced'] == "usrModel": tabstring = "   UM   "


            # Check if replaced is an empty string and set text color accordingly
            if data_dict['replaced'] == '':
                for item in [grouping_item, displayname_item, value_item, info_item]:
                    item.setForeground(QtGui.QColor('gray'))
            if data_dict['replaced'] == 'byModel':
                for item in [grouping_item, displayname_item, value_item, info_item]:
                    item.setForeground(QtGui.QColor('blue'))
            if data_dict['replaced'] == 'byCraft':
                for item in [grouping_item, displayname_item, value_item, info_item]:
                    item.setForeground(QtGui.QColor('darkblue'))
            if data_dict['replaced'] == 'usrModel':
                for item in [grouping_item, displayname_item, value_item, info_item]:
                    item.setForeground(QtGui.QColor('green'))
            if data_dict['replaced'] == 'usrCraft':
                for item in [grouping_item, displayname_item, value_item, info_item]:
                    item.setForeground(QtGui.QColor('darkgreen'))
            if data_dict['replaced'] == 'usrAircf':
                for item in [grouping_item, displayname_item, value_item, info_item]:
                    item.setForeground(QtGui.QColor('purple'))

            # Make specific columns read-only
            grouping_item.setFlags(grouping_item.flags() & ~Qt.ItemIsEditable)
            displayname_item.setFlags(displayname_item.flags() & ~Qt.ItemIsEditable)
            info_item.setFlags(info_item.flags() & ~Qt.ItemIsEditable)

            self.table_widget.insertRow(row)
            self.table_widget.setItem(row, 0, override_item)
            self.table_widget.setItem(row, 1, grouping_item)
            self.table_widget.setItem(row, 2, displayname_item)
            self.table_widget.setItem(row, 3, value_item)
            self.table_widget.setItem(row, 4, info_item)
         #   print(f"Row {row} added to the table.")

    def toggle_rows(self):
        show_inherited = self.cb_show_inherited.isChecked()

        for row in range(self.table_widget.rowCount()):
            grouping_item = self.table_widget.item(row, 1)  # Assuming grouping is in the second column
            if grouping_item is not None:
                is_replaced = grouping_item.foreground().color() == QtGui.QColor('gray')
                self.table_widget.setRowHidden(row, not show_inherited and is_replaced)

    def update_table_on_model_change(self, index):
        # Get the selected model from the combo box
        selected_model = self.drp_models.currentText()

        # Replace the following line with your actual XML reading logic
        model_type, model_pattern, mydata = read_single_model(xml_file_path, sim, selected_model, device, crafttype)

        # Update the table with the new data
        self.data_list = mydata
        self.table_widget.clear()
        self.populate_table()
        self.toggle_rows()

    def create_datatype_item(self, datatype, value, unit):
        #print(f"{datatype} {value}")
        if datatype == 'bool':
            checkbox = QCheckBox()
            checkbox.setChecked(value.lower() == 'true')
            #checkbox.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            checkbox.setStyleSheet("margin-left:50%; margin-right:50%;")
            item = QTableWidgetItem()
            #item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            item.setData(Qt.CheckStateRole, Qt.Checked if value.lower() == 'true' else Qt.Unchecked)
            return item
        elif datatype == 'int' or datatype == 'text' or datatype == 'float' or datatype == 'negfloat':
            line_edit = QLineEdit(str(value) + str(unit))
            return QTableWidgetItem(line_edit.text())  # Set the widget
        # making float numeric for now...
        # elif datatype == 'float':
        #     slider = QSlider(Qt.Horizontal)
        #     slider.setValue(int(float(value) * 100))  # Assuming float values between 0 and 1
        #     item = QTableWidgetItem()
        #     item.setData(Qt.DisplayRole, slider)
        #     return item
        else:
            return QTableWidgetItem(value)

    def create_override_item(self, override):
        #print(f"{override}")

        checkbox = QCheckBox()
        checkbox.setChecked(override != '')
        checkbox.setStyleSheet("margin-left:50%; margin-right:50%;")    #why no worky
        item = QTableWidgetItem()
        item.setData(Qt.CheckStateRole, Qt.Checked if override != '' else Qt.Unchecked)
        return item


if __name__ == "__main__":
    xml_file_path = "defaults.xml"  # defaults
    userconfig_path = "userconfig.xml"  # user config overrides stored here

    defaultdata = []
    mydata = []

    sim = "MSFS"
    model_name = "XCub"
    device = "joystick"  # joystick, pedals, collective
    crafttype = "Helicopter"  # suggested, send whatever simconnect finds

    print_each_step = False

    # # output a single aircraft class config
    #
    # defaultdata = read_xml_file(xml_file_path,sim,"Aircraft",device)
    # printconfig(sim, "Aircraft", defaultdata)
    #
    # #defaultdata = read_xml_file(xml_file_path,sim,crafttype,device)
    # #printconfig(sim, crafttype, defaultdata)

    # output a single model
    model_type, model_pattern, mydata = read_single_model(xml_file_path, sim, model_name, device, crafttype)

    print(f"\nData for: {sim}  model: {model_name}  pattern: {model_pattern}  class: {model_type}  device:{device}\n")

    printconfig(sim, model_type, mydata)

    # output all default configs for device
    # print_all_defaults()

    #  GUI stuff below
    data_list = mydata
    print(data_list)
    app = QApplication(sys.argv)
    SettingsWindow = settings_window(data_list)
   # ui = Ui_SettingsWindow()
   # ui.setupUi(SettingsWindow)
    SettingsWindow.show()


    sys.exit(app.exec_())

