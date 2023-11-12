import xml.etree.ElementTree as ET

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

    # skip bad combos
    if sim == 'DCS' and craft == 'HPGHelicopter': return
    if sim == 'DCS' and craft == 'TurbopropAircraft': return
    if sim == 'DCS' and craft == 'GliderAircraft': return
    if sim == 'IL2' and craft == 'GliderAircraft': return
    if sim == 'IL2' and craft == 'HPGHelicopter': return
    if sim == 'IL2' and craft == 'Helicopter': return
    if sim == 'IL2' and craft == 'TurbopropAircraft': return

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in root.findall(f'.//defaults[{sim}="true"][{device}="true"]'):

        grouping = defaults_elem.find('Grouping').text
        name = defaults_elem.find('name').text
        displayname = defaults_elem.find('displayname').text
        datatype = defaults_elem.find('type').text
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
                'displayname': displayname,
                'value': value_craft,
                'unit': unit,
                'datatype': datatype,
                'validvalues': validvalues,
                'info': info
            }

            # Append to the list
            data_list.append(data_dict)
                
    # Sort the data by grouping and then by name
   
    return sorted(data_list, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['name']))

       

def printconfig(sim, craft, sorted_data):
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


if __name__ == "__main__":
    xml_file_path = "defaults.xml"  # Replace with the path to your XML file

    # output a single config
   # read_xml_file(xml_file_path,"DCS","Aircraft","joystick")

    #output all default configs for device
    device = "joystick"
    for sim in "global","DCS","MSFS","IL2":
        crafts = get_craft_attributes(xml_file_path,sim,device)
        for craft in crafts:
            mydata =  read_xml_file(xml_file_path,sim,craft,device)
     
    printconfig(sim, craft, mydata)
        