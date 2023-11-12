import xml.etree.ElementTree as ET
import sys
from PyQt5.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QSlider, QCheckBox, QLineEdit, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt


def get_craft_attributes(file_path,sim,device):
    craft_attributes = set()
    craft_attributes.add('Aircraft')

    tree = ET.parse(file_path)
    root = tree.getroot()

    for defaults_elem in root.findall(f'.//defaults[{sim}="true"][{device}="true"]'):
    #for defaults_elem in root.findall(f'.//defaults[{sim}="true" and {device}="true"]'):
        for value_elem in defaults_elem.findall('.//value'):
            craft_attr = value_elem.get('Craft')
            if craft_attr is not None:
     
                craft_attributes.add(craft_attr)

    return sorted(list(craft_attributes))

def read_xml_file(file_path,sim,craft,device): 
    tree = ET.parse(file_path)
    root = tree.getroot()



    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in root.findall(f'.//defaults[{sim}="true"][{device}="true"]'):

        grouping = defaults_elem.find('Grouping').text
        name = defaults_elem.find('name').text
        #print(name)
        displayname = defaults_elem.find('displayname').text
        datatype = defaults_elem.find('datatype').text
        unit_elem = defaults_elem.find('unit')
        unit = unit_elem.text  if unit_elem is not None else ""
        valid_elem = defaults_elem.find('validvalues')
        validvalues = valid_elem.text  if valid_elem is not None else ""

        # Check if the 'value' element has the 'Craft' attribute
        value_elem_craft = defaults_elem.find(f".//value[@Craft='{craft}']")
        value_craft = value_elem_craft.text if value_elem_craft is not None else None

        # If 'value' element without 'Craft' attribute exists, use that in 'Aircraft' section
        if value_craft is None and craft == 'Aircraft':
            value_elem_craft = defaults_elem.find('value')
            value_craft = value_elem_craft.text if value_elem_craft is not None else None
                
        # Store data in a dictionary
        if value_craft is not None:
            spacing = 50 - (len(name) + len(value_craft) + len(unit))
            space = " " * spacing
            info_elem = defaults_elem.find('info')
            info = (f"{space} # {info_elem.text}")  if info_elem is not None else ""

            data_dict = {
                'grouping': grouping,
                'name': name,
       #         'displayname': displayname,
                'value': value_craft,
                'unit': unit,
       #         'datatype': datatype,
        #        'validvalues': validvalues,
                'info': info
            }
            #print(data_dict)

            # Append to the list
            data_list.append(data_dict)
            #print(data_list)
    # Sort the data by grouping and then by name
   
    sorted_data = sorted(data_list, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['name']))
    #print(sorted_data)
    #printconfig(sim, craft, sorted_data)
    return sorted_data

def skip_bad_combos(sim,craft):
    
    if sim == 'DCS' and craft == 'HPGHelicopter': return True
    if sim == 'DCS' and craft == 'TurbopropAircraft': return True
    if sim == 'DCS' and craft == 'GliderAircraft': return True
    if sim == 'IL2' and craft == 'GliderAircraft': return True
    if sim == 'IL2' and craft == 'HPGHelicopter': return True
    if sim == 'IL2' and craft == 'Helicopter': return True
    if sim == 'IL2' and craft == 'TurbopropAircraft': return True

    return False



def printconfig(sim, craft, sorted_data):
    #print("printconfig: " +sorted_data)
    print("\n")  # Separate sections with a blank line
    print("\n#######################################################################################################################")
    # Print the appropriate header based on tags
    if craft == 'Aircraft':
        current_header = '['+ sim + ']'
        print(current_header)
    else:
        current_header = '['+ sim + "." + craft + ']'
        print(current_header)

    # Print the sorted data with group names and headers
    current_group = None
    current_header = None
    for item in sorted_data:
        if item['grouping'] != current_group:
            current_group = item['grouping']
            if current_header is not None:
                print("\n\n")  # Separate sections with a blank line
            print(f"\n# {current_group}")           

        print(f"{item['name']} = {item['value']} {item['unit']}{item['info']}")

class MyTableWidget(QWidget):
    def __init__(self, data_list):
        super(MyTableWidget, self).__init__()

        self.data_list = data_list

        self.init_ui()

    def init_ui(self):
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(4)  # Assuming you want to display 4 columns

        # Set table headers
        headers = ['Grouping', 'Display Name', 'Unit', 'Info']
        self.table_widget.setHorizontalHeaderLabels(headers)

        self.populate_table()

        layout = QVBoxLayout()
        layout.addWidget(self.table_widget)

        self.setLayout(layout)

    def populate_table(self):
        for row, data_dict in enumerate(self.data_list):
            grouping_item = QTableWidgetItem(data_dict['grouping'])
            displayname_item = QTableWidgetItem(data_dict['displayname'])
            unit_item = self.create_unit_item(data_dict['unit'], data_dict['value'])
            info_item = QTableWidgetItem(data_dict['info'])

            self.table_widget.insertRow(row)
            self.table_widget.setItem(row, 0, grouping_item)
            self.table_widget.setItem(row, 1, displayname_item)
            self.table_widget.setItem(row, 2, unit_item)
            self.table_widget.setItem(row, 3, info_item)

    def create_unit_item(self, unit, value):
        if unit == 'bool':
            checkbox = QCheckBox()
            checkbox.setChecked(value.lower() == 'true')
            return QTableWidgetItem()  # Empty item; set the widget later in setItem
        elif unit == 'int' or unit == 'text':
            line_edit = QLineEdit(str(value))
            return QTableWidgetItem()  # Empty item; set the widget later in setItem
        elif unit == 'float':
            slider = QSlider(Qt.Horizontal)
            slider.setValue(int(float(value) * 100))  # Assuming float values between 0 and 1
            return QTableWidgetItem()  # Empty item; set the widget later in setItem
        else:
            return QTableWidgetItem(value)

        return None
    

if __name__ == "__main__":
    xml_file_path = "defaults.xml"  # Replace with the path to your XML file
    app = QApplication(sys.argv)

    mydata= []
    # output a single config
   # read_xml_file(xml_file_path,"DCS","Aircraft","joystick")

    #output all default configs for device
    device = "joystick"
    for sim in "global","DCS","MSFS","IL2":
        crafts = get_craft_attributes(xml_file_path,sim,device)
        for craft in crafts:
            skip = skip_bad_combos(sim,craft)
            if skip == True: continue
            mydata = read_xml_file(xml_file_path,sim,craft,device)
            #print("main: "+ mydata)
    
            printconfig(sim, craft, mydata)

    
           # table_widget = MyTableWidget(mydata)
           # table_widget.show()

  #  sys.exit(app.exec_())





