import settingsmanager
import main
import logging
import xml.etree.ElementTree as ET
import os
import re
import xml.dom.minidom

current_sim = ''
current_aircraft_name = ''

print_debugs = False
print_method_calls = False


def create_empty_userxml_file():
    if not os.path.isfile(settingsmanager.SettingsWindow.userconfig_path):
        # Create an empty XML file with the specified root element
        root = ET.Element("TelemFFB")
        tree = ET.ElementTree(root)
        # Create a backup directory if it doesn't exist
        if not os.path.exists(settingsmanager.SettingsWindow.userconfig_rootpath):
            os.makedirs(settingsmanager.SettingsWindow.userconfig_rootpath)
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(f"Empty XML file created at {settingsmanager.SettingsWindow.userconfig_path}")
    else:
        logging.info(f"XML file exists at {settingsmanager.SettingsWindow.userconfig_path}")


def read_xml_file(the_sim):
    mprint(f"read_xml_file  {the_sim}")
    tree = ET.parse(settingsmanager.SettingsWindow.defaults_path)
    root = tree.getroot()

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in root.findall(f'.//defaults[{the_sim}="true"][{main.args.type}="true"]'):

        grouping = defaults_elem.find('Grouping').text
        name = defaults_elem.find('name').text
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
        prereq_elem = defaults_elem.find('prereq')
        prereq = (f"{prereq_elem.text}") if prereq_elem is not None else ""

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
            'prereq': prereq,
            'info': info
        }

        data_list.append(data_dict)

        # lprint(data_list)
    # Sort the data by grouping and then by name

    sorted_data = sorted(data_list, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['displayname']))
    # lprint(sorted_data)
    # printconfig(sim, craft, sorted_data)
    return sorted_data


def read_models(the_sim):
    all_models = ['']
    tree = ET.parse(settingsmanager.SettingsWindow.defaults_path)
    root = tree.getroot()

    for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{main.args.type}"]') + \
                      root.findall(f'.//models[sim="any"][device="{main.args.type}"]') + \
                      root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                      root.findall(f'.//models[sim="any"][device="any"]'):

        pattern = model_elem.find('model')
        # lprint (pattern.text)
        if pattern is not None:
            if pattern.text not in all_models:
                all_models.append(pattern.text)

    create_empty_userxml_file()
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()

    for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{main.args.type}"]') + \
                      root.findall(f'.//models[sim="any"][device="{main.args.type}"]') + \
                      root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                      root.findall(f'.//models[sim="any"][device="any"]'):

        pattern = model_elem.find('model')
        # lprint (pattern.text)
        if pattern is not None:
            if pattern.text not in all_models:
                all_models.append(pattern.text)

    return sorted(all_models)


def read_models_data(file_path, sim, full_model_name):
    mprint(f"read_models_data  {file_path}, {sim}, {full_model_name}")
    # runs on both defaults and userconfig xml files
    tree = ET.parse(file_path)
    root = tree.getroot()

    model_data = []
    found_pattern = ''

    # Iterate through models elements
    #for model_elem in root.findall(f'.//models[sim="{self.sim}"][device="{main.args.type}"]'):
    for model_elem in root.findall(f'.//models[sim="{sim}"][device="{main.args.type}"]') + \
                      root.findall(f'.//models[sim="any"][device="{main.args.type}"]') + \
                      root.findall(f'.//models[sim="{sim}"][device="any"]') + \
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


def read_default_class_data(the_sim, the_class):
    mprint(f"read_default_class_data  sim {the_sim}, class {the_class}")
    tree = ET.parse(settingsmanager.SettingsWindow.defaults_path)
    root = tree.getroot()

    class_data = []

    # Iterate through models elements
    #for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{main.args.type}"]'):
    #for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{main.args.type}"]'):
    for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{main.args.type}"]') + \
                      root.findall(f'.//classdefaults[sim="any"][type="{the_class}"][device="{main.args.type}"]') + \
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


def read_single_model( sim, aircraft_name):
    mprint(f"######################################\nread_single_model  sim {sim}, a/c name {aircraft_name}")
    input_modeltype = ''
    if '.' in sim:
        input = sim.split('.')
        sim_temp = input[0]
        the_sim = sim_temp.replace('2020','')
        input_modeltype = input[1]
    else:
        the_sim = sim

    print_counts = False
    print_each_step = False  # for debugging

    # Read models data first
    model_data, def_model_pattern = read_models_data(settingsmanager.SettingsWindow.defaults_path, the_sim, aircraft_name)
    user_model_data, usr_model_pattern = read_models_data(settingsmanager.SettingsWindow.userconfig_path, the_sim, aircraft_name)

    model_pattern = def_model_pattern
    if usr_model_pattern != '':
        model_pattern = usr_model_pattern

    # Extract the type from models data, if name is blank then use the class.  otherwise assume no type is set.
    if aircraft_name == '':
        model_class = input_modeltype
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
    if model_class == '':
        model_class = input_modeltype

    # get default Aircraft settings for this sim and device
    simdata = read_xml_file(the_sim)

    if print_counts:  lprint(f"simdata count {len(simdata)}")

    # see what we got
    if print_each_step:
        lprint(f"\nSimresult: {the_sim} type: ''  device:{main.args.type}\n")
        printconfig(simdata)

    # combine base stuff
    defaultdata = simdata
    # if self.sim != 'Global':
    #     for item in simdata: defaultdata.append(item)

    if print_counts:  lprint(f"defaultdata count {len(defaultdata)}")

    # get additional class default data
    if model_class != "":
        # Use the extracted type in read_xml_file
        craftresult = read_default_class_data(the_sim, model_class)

        if craftresult is not None:

            # merge if there is any
            default_craft_result = update_default_data_with_craft_result(defaultdata, craftresult)
        else:
            default_craft_result = defaultdata

        if print_counts:  lprint(f"default_craft_result count {len(default_craft_result)}")

        # see what we got
        if print_each_step:
            lprint(f"\nDefaultsresult: {the_sim} type: {model_class}  device:{main.args.type}\n")
            printconfig(default_craft_result)
    else:
        default_craft_result = defaultdata

    # get userconfig global overrides
    userglobaldata = read_user_sim_data( 'Global')
    if userglobaldata is not None:
        # merge if there is any
        def_craft_userglobal_result = update_data_with_models(default_craft_result, userglobaldata,'Global (user)')
    else:
        def_craft_userglobal_result = default_craft_result

    if print_counts:  lprint(f"def_craft_userglobal_result count {len(def_craft_userglobal_result)}")

    # get userconfig sim overrides
    if the_sim != 'Global':
        user_default_data = read_user_sim_data(the_sim)
        if user_default_data is not None:
            # merge if there is any
            def_craft_user_default_result = update_data_with_models(def_craft_userglobal_result, user_default_data, 'Sim (user)')
        else:
            def_craft_user_default_result = def_craft_userglobal_result

        if print_counts:  lprint(f"def_craft_user_default_result count {len(def_craft_user_default_result)}")
    else:
        def_craft_user_default_result = def_craft_userglobal_result

    if model_class != "":
        # get userconfg craft specific type overrides
        usercraftdata = read_user_class_data(the_sim, model_class)
        if usercraftdata is not None:
            # merge if there is any
            def_craft_usercraft_result = update_data_with_models(def_craft_user_default_result, usercraftdata, 'Class (user)')
        else:
            def_craft_usercraft_result = def_craft_user_default_result
    else:
        def_craft_usercraft_result = def_craft_user_default_result

    # Update result with default models data
    def_craft_models_result = update_data_with_models(def_craft_usercraft_result, model_data, 'Model Default')

    if print_counts:  lprint(f"def_craft_models count {len(def_craft_models_result)}")

    # finally get userconfig model specific overrides
    if user_model_data:
        final_result = update_data_with_models(def_craft_models_result, user_model_data, 'Model (user)')
    else:
        final_result = def_craft_models_result

    final_result = [item for item in final_result if item['value'] != '' or item['name'] == 'vpconf']

    prereq_list = read_prereqs()
    final_w_prereqs = check_prereq_value(prereq_list, final_result)

    sorted_data = sorted(final_w_prereqs, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['name']))
    # lprint(f"final count {len(final_result)}")

    return model_class, model_pattern, sorted_data


def read_user_sim_data(the_sim):
    mprint(f"read_user_sim_data {the_sim}")
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()

    sim_data = []

    # Iterate through models elements
    # for model_elem in root.findall(f'.//simSettings[sim="{the_sim}" or sim="any"][device="{main.args.type}" or device="any"]'):
    for model_elem in root.findall(f'.//simSettings[sim="{the_sim}"][device="{main.args.type}"]'):   # + \
                      # root.findall(f'.//simSettings[sim="any"][device="{main.args.type}"]') + \
                      # root.findall(f'.//simSettings[sim="{the_sim}"][device="any"]') + \
                      # root.findall(f'.//simSettings[sim="any"][device="any"]'):

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

def read_user_class_data(the_sim, crafttype):
    mprint(f"read_user_class_data  {the_sim}, {crafttype}")
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()

    model_data = []

    # Iterate through models elements
    #for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{main.args.type}"]'):
    for model_elem in root.findall(f'.//classSettings[sim="{the_sim}"][device="{main.args.type}"]'):     # + \
                      # root.findall(f'.//classSettings[sim="any"][device="{main.args.type}"]') + \
                      # root.findall(f'.//classSettings[sim="{the_sim}"][device="any"]') + \
                      # root.findall(f'.//classSettings[sim="any"][device="any"]'):
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


def update_default_data_with_craft_result(defaultdata, craftresult):
    updated_defaultdata = defaultdata.copy()  # Create a copy to avoid modifying the original data

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

    return updated_defaultdata


def update_data_with_models(defaults_data, model_data, replacetext):
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


def write_models_to_xml(the_sim, the_model, the_value, setting_name):
    mprint(f"write_models_to_xml  {the_sim}, {the_model}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()

    # Check if an identical <models> element already exists
    model_elem = root.find(f'.//models'  # [sim="{the_sim}"]'    # might be 'any' (from convert ini)
                           f'[device="{main.args.type}"]'
                           f'[model="{the_model}"]'
                           f'[name="{setting_name}"]')

    if model_elem is not None:
        # Update the value of the existing element
        for child_elem in model_elem:
            if child_elem.tag == 'value':
                child_elem.text = str(the_value)
            if child_elem.tag == 'sim':
                if child_elem.text == 'any':
                    child_elem.text = the_sim
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(f"Updated <models> element with values: sim={the_sim}, device={main.args.type}, "
                     f"value={the_value}, model={the_model}, name={setting_name}")

    else:
        # Check if an identical <models> element already exists; if so, skip
        model_elem_exists = any(
            all(
                element.tag == tag and element.text == value
                for tag, value in [
                    ("name", setting_name),
                    ("model", the_model),
                    ("value", the_value),
                    ("sim", the_sim),
                    ("device", main.args.type)
                ]
            )
            for element in root.iter("models")
        )

        if model_elem_exists:
            lprint("<models> element with the same values already exists. Skipping.")
        else:
            # Create child elements with the specified content
            models = ET.SubElement(root, "models")
            for tag, value in [("name", setting_name),
                               ("model", the_model),
                               ("value", the_value),
                               ("sim", the_sim),
                               ("device", main.args.type)]:
                ET.SubElement(models, tag).text = value

            # Write the modified XML back to the file
            tree = ET.ElementTree(root)
            tree.write(settingsmanager.SettingsWindow.userconfig_path)
            logging.info(f"Added <models> element with values: sim={the_sim}, device={main.args.type}, "
                         f"value={the_value}, model={the_model}, name={setting_name}")


def write_class_to_xml(the_sim, the_class, the_value, setting_name):
    mprint(f"write_class_to_xml  {the_sim}, {the_class}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()

    # Check if an identical <classSettings> element already exists
    class_elem = root.find(f'.//classSettings[sim="{the_sim}"]'
                           f'[device="{main.args.type}"]'
                           f'[type="{the_class}"]'
                           f'[name="{setting_name}"]')

    if class_elem is not None:
        # Update the value of the existing element
        for child_elem in class_elem:
            if child_elem.tag == 'value':
                child_elem.text = str(the_value)
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(f"Updated <classSettings> element with values: sim={the_sim}, device={main.args.type}, "
                     f"value={the_value}, model={the_class}, name={setting_name}")

    else:
        # Create a new <classSettings> element
        classes = ET.SubElement(root, "classSettings")
        for tag, value in [("name", setting_name),
                           ("type", the_class),
                           ("value", the_value),
                           ("sim", the_sim),
                           ("device", main.args.type)]:
            ET.SubElement(classes, tag).text = value

        # Write the modified XML back to the file
        tree = ET.ElementTree(root)
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(f"Added <classSettings> element with values: sim={the_sim}, device={main.args.type}, "
                     f"value={the_value}, type={the_class}, name={setting_name}")


def write_sim_to_xml(the_sim, the_value, setting_name):
    mprint(f"write_sim_to_xml {the_sim}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()

    # Check if an identical <simSettings> element already exists
    sim_elem = root.find(f'.//simSettings[sim="{the_sim}"]'
                         f'[device="{main.args.type}"]'
                         f'[name="{setting_name}"]')

    if sim_elem is not None:
        # Update the value of the existing element
        for child_elem in sim_elem:
            if child_elem.tag == 'value':
                child_elem.text = str(the_value)
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(f"Updated <simSettings> element with values: sim={the_sim}, device={main.args.type}, "
                     f"value={the_value}, name={setting_name}")

    else:
        # Create a new <simSettings> element
        sims = ET.SubElement(root, "simSettings")
        for tag, value in [("name", setting_name),
                           ("value", the_value),
                           ("sim", the_sim),
                           ("device", main.args.type)]:
            ET.SubElement(sims, tag).text = value

        # Write the modified XML back to the file
        tree = ET.ElementTree(root)
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(
            f"Added <simSettings> element with values: sim={the_sim}, device={main.args.type}, value={the_value}, name={setting_name}")


def write_converted_to_xml(differences):
    sim_set = []
    class_set = []
    model_set = []

    for dif in differences:
        if dif['sim'] == 'any':
            model_set.append(dif)
        else:
            if dif['class'] != '':
                class_set.append(dif)
            else:
                sim_set.append(dif)
    for s in sim_set:
        write_sim_to_xml(s['sim'], s['value'], s['name'])
    for c in class_set:
        write_class_to_xml(c['sim'], c['class'], c['value'], c['name'])
    for m in model_set:
        write_models_to_xml(m['sim'], m['model'], m['value'], m['name'])


def erase_models_from_xml(the_sim, the_model, the_value, setting_name):
    mprint(f"erase_models_from_xml  {the_sim} {the_model}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()
    elements_to_remove = []
    for model_elem in root.findall(f'models[sim="{the_sim}"]'
                                   f'[device="{main.args.type}"]'
                                   f'[value="{the_value}"]'
                                   f'[model="{the_model}"]'
                                   f'[name="{setting_name}"]'):

        if model_elem is not None:
            elements_to_remove.append(model_elem)
        else:
            lprint ("model not found")

    # Remove the elements outside the loop
    for elem in elements_to_remove:
        root.remove(elem)
        # Write the modified XML back to the file
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(f"Removed <models> element with values: sim={the_sim}, device={main.args.type}, "
                  f"value={the_value}, model={the_model}, name={setting_name}")


def erase_class_from_xml( the_sim, the_class, the_value, setting_name):
    mprint(f"erase_class_from_xml  {the_sim} {the_class}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()
    elements_to_remove = []
    for class_elem in root.findall(f'.//classSettings[sim="{the_sim}"]'
                                   f'[device="{main.args.type}"]'
                                   f'[value="{the_value}"]'
                                   f'[type="{the_class}"]'
                                   f'[name="{setting_name}"]'):

        if class_elem is not None:
            elements_to_remove.append(class_elem)
        else:
            lprint ("class not found")

    # Remove the elements outside the loop
    for elem in elements_to_remove:
        root.remove(elem)
        # Write the modified XML back to the file
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(f"Removed <classSettings> element with values: sim={the_sim}, device={main.args.type}, "
                  f"value={the_value}, type={the_class}, name={setting_name}")


def erase_sim_from_xml(the_sim, the_value, setting_name):
    mprint(f"erase_sim_from_xml  {the_sim} {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(settingsmanager.SettingsWindow.userconfig_path)
    root = tree.getroot()

    elements_to_remove = []
    for sim_elem in root.findall(f'.//simSettings[sim="{the_sim}"]'
                                   f'[device="{main.args.type}"]'
                                   f'[value="{the_value}"]'
                                   f'[name="{setting_name}"]'):

        if sim_elem is not None:
            elements_to_remove.append(sim_elem)
        else:
            lprint ("sim setting not found")

    # Remove the elements outside the loop
    for elem in elements_to_remove:
        root.remove(elem)
        # Write the modified XML back to the file
        tree.write(settingsmanager.SettingsWindow.userconfig_path)
        logging.info(f"Removed <simSettings> element with values: sim={the_sim}, device={main.args.type}, value={the_value}, name={setting_name}")

def sort_elements(tree):    #  unused for now.
    # Parse the XML file

    root = tree.getroot()

    # Extract all elements
    all_elements = root.findall('')

    # Sort the elements based on their tag names
    sorted_elements = sorted(all_elements, key=lambda x: x.tag)

# warning!  deletes everything

     # Replace existing elements with sorted elements
    # for elem in root:
    #     root.remove(elem)
    #
    #     # Add sorted elements back to the parent
    # for elem in sorted_elements:
    #     root.append(elem)
###

    # Prettify the XML
    xml_str = xml.dom.minidom.parseString(ET.tostring(root)).toprettyxml()
    with open(settingsmanager.SettingsWindow.userconfig_path, 'w') as xml_file:
        xml_file.write(xml_str)



def read_prereqs():
    tree = ET.parse(settingsmanager.SettingsWindow.defaults_path)
    root = tree.getroot()

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in root.findall(f'.//defaults'):

        name = defaults_elem.find('name').text
        prereq_elem = defaults_elem.find('prereq')
        prereq = (f"{prereq_elem.text}") if prereq_elem is not None else ""

        # Store data in a dictionary
        data_dict = {
            'prereq': prereq,
            'value': 'False'
        }

        if data_dict is not None and data_dict['prereq'] != '' and data_dict not in data_list:
            data_list.append(data_dict)

        # lprint(data_list)

    # lprint(sorted_data)
    # printconfig(sim, craft, sorted_data)
    return data_list

def check_prereq_value(prereq_list,datalist):
    for item in datalist:
        for prereq in prereq_list:
            if prereq['prereq'] == item['name']:
                prereq['value'] = item['value']
    return datalist

# def is_prereq_satisfied(self,setting_name):
#     tree = ET.parse(settingsmanager.SettingsWindow.defaults_path)
#     root = tree.getroot()
#     result = True
#     # Collect data in a list of dictionaries
#     prereq_list = []
#     for defaults_elem in root.findall(f'.//defaults[sim="{self.sim}"][device="{main.args.type}"]'):
#
#         prereq_elem = defaults_elem.find('prereq')
#         prereq = (f"{prereq_elem.text}") if prereq_elem is not None else ""
#         prereq_dict = {'prereq': prereq}
#         # Append to the list
#         prereq_list.append(prereq_dict)
#
#     if prereq_list != []:
#         if setting_name in prereq_dict:
#             lprint (f"prereq {prereq_dict['name']}")
#
#     return result

def printconfig( sorted_data):
    # lprint("printconfig: " +sorted_data)
    show_source = False
    lprint("#############################################")

    # Print the sorted data with group names and headers
    current_group = None
    current_header = None
    for item in sorted_data:
        if item['grouping'] != current_group:
            current_group = item['grouping']
            if current_header is not None:
                lprint("\n\n")  # Separate sections with a blank line
            lprint(f"\n# {current_group}")
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

        lprint(f"{tabstring}{item['name']} = {item['value']} {item['unit']} {space} {item['info']}")


## unused?
def get_craft_attributes(file_path, sim, device):
    mprint(f"get_craft_attributes {file_path}, {sim}, {device}")
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



def lprint(msg):
    if print_debugs:
        print(msg)

def mprint(msg):
    if print_method_calls:
        print(msg)