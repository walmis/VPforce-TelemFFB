# import globalvars
import logging
import time
import xml.etree.ElementTree as ET
import os
import re
import xml.dom.minidom


print_debugs = False
print_method_calls = False

device = ''
userconfig_path = ''
defaults_path = ''

def write_userconfig_xml(tree : ET.ElementTree):
    ET.indent(tree, " ")
    tree.write(userconfig_path, "utf-8")


def update_vars(_device, _userconfig_path, _defaults_path):
    global device, userconfig_path, defaults_path
    device = _device
    userconfig_path = _userconfig_path
    defaults_path = _defaults_path


def read_xml_file(the_sim, instance_device=''):
    mprint(f"read_xml_file  {the_sim}")
    tree = ET.parse(defaults_path)
    root = tree.getroot()

    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in root.findall(f'.//defaults[{the_sim}="true"][{the_device}="true"]'):

        grouping = defaults_elem.find('Grouping').text
        order = defaults_elem.find('order').text
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
        sliderfactor_elem = defaults_elem.find('sliderfactor')
        sliderfactor = (f"{sliderfactor_elem.text}") if sliderfactor_elem is not None else "1"
        device_elem = defaults_elem.find('any')
        device_text = 'any' if device_elem is not None else device
        replaced = 'Sim Default'

        # Store data in a dictionary
        data_dict = {
            'grouping': grouping,
            'order': order,
            'name': name,
            'displayname': displayname,
            'value': value,
            'unit': unit,
            'datatype': datatype,
            'validvalues': validvalues,
            'replaced': replaced,
            'prereq': prereq,
            'info': info,
            'sliderfactor': sliderfactor,
            'device_text': device_text
        }

        data_list.append(data_dict)

        # lprint(data_list)
    # Sort the data by grouping and then by name
    sorted_data = sorted(data_list, key=lambda x: float(x['order']))
    # sorted_data = sorted(data_list, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['displayname']))
    # lprint(sorted_data)
    # printconfig(sim, craft, sorted_data)
    return sorted_data


def read_anydevice_settings(the_sim):

    tree = ET.parse(defaults_path)
    root = tree.getroot()

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in root.findall(f'.//defaults[{the_sim}="true"][any="true"]'):

        name_elem = defaults_elem.find('name')
        if name_elem is not None:
            name = name_elem.text
            data_list.append(name)

    return data_list


def read_models(the_sim, the_class=''):
    all_models = ['']
    tree = ET.parse(defaults_path)
    root = tree.getroot()
    if the_class == '':
        def_models =  root.findall(f'.//models[sim="{the_sim}"][device="{device}"]') + \
                      root.findall(f'.//models[sim="any"][device="{device}"]') + \
                      root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                      root.findall(f'.//models[sim="any"][device="any"]')
    else:
        def_models = root.findall(f'.//models[sim="{the_sim}"][value="{the_class}"]')

    for model_elem in def_models:
        pattern = model_elem.find('model')
        # lprint (pattern.text)
        if pattern is not None:
            if pattern.text not in all_models:
                all_models.append(pattern.text)

    # create_empty_userxml_file() - handled by TelemFFB on startup via utils.py
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    if the_class == '':
        usr_models =  root.findall(f'.//models[sim="{the_sim}"][device="{device}"]') + \
                      root.findall(f'.//models[sim="any"][device="{device}"]') + \
                      root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                      root.findall(f'.//models[sim="any"][device="any"]')
    else:
        usr_models = root.findall(f'.//models[sim="{the_sim}"][value="{the_class}"]')
    for model_elem in usr_models:
        pattern = model_elem.find('model')
        # lprint (pattern.text)
        if pattern is not None:
            if pattern.text not in all_models:
                all_models.append(pattern.text)

    return sorted(all_models)


def read_models_data(file_path, sim, full_model_name, alldevices=False, instance_device = ''):
    mprint(f"read_models_data  {file_path}, {sim}, {full_model_name}")
    # runs on both defaults and userconfig xml files
    tree = ET.parse(file_path)
    root = tree.getroot()

    model_data = []
    found_pattern = ''

    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device

    if alldevices:
        # Iterate through models elements
        #for model_elem in root.findall(f'.//models[sim="{self.sim}"][device="{device}"]'):
        any_models = root.findall(f'.//models[sim="any"]')

        all_models = root.findall(f'.//models[sim="{sim}"]') 

    else:
        # Collect models with 'device' set to 'any' or both 'sim' and 'device' set to 'any'
        any_models = root.findall(f'.//models[sim="{sim}"][device="any"]') + \
                     root.findall(f'.//models[sim="any"][device="any"]')

        # Collect models with specific devices
        all_models = root.findall(f'.//models[sim="{sim}"][device="{the_device}"]') + \
                     root.findall(f'.//models[sim="any"][device="{the_device}"]')

        # Create a dictionary to store models based on unique keys
    model_dict = {}

    # Process any_models
    for model_elem in any_models:
        model_key = (model_elem.find('model').text, model_elem.find('name').text)
        model_dict[model_key] = model_elem

    # Process all_models, overwriting any existing models with the same key
    for model_elem in all_models:
        model_key = (model_elem.find('model').text, model_elem.find('name').text)
        model_dict[model_key] = model_elem

    # Process the models
    for model_elem in model_dict.values():
        # Assuming 'model' is the element containing the wildcard pattern

        unit_pattern = model_elem.find('model')
        if unit_pattern is not None:
            pattern = unit_pattern.text
            if pattern is not None:
                # Check if the full_model_name matches the pattern using re.match
                if re.match(pattern, full_model_name) or pattern == full_model_name:
                    name = model_elem.find('name').text
                    value = model_elem.find('value').text
                    unit_elem = model_elem.find('unit')
                    unit = unit_elem.text if unit_elem is not None else ""
                    saved_device = model_elem.find('device').text
                    model_dict = {
                        'name': name,
                        'value': value,
                        'unit': unit,
                        'device': saved_device
                    }
                    found_pattern = pattern
                    model_data.append(model_dict)
                else:
                    lprint (f"{pattern} does not match {full_model_name}")

    return model_data, found_pattern


def read_sc_overrides(aircraft_name):
    def_model_overrides = read_models_sc_overrides(defaults_path, aircraft_name, 'defaults')
    user_model_overrides = read_models_sc_overrides(userconfig_path, aircraft_name, 'user')
    result = update_sc_overrides_with_user(def_model_overrides,user_model_overrides)
    return result


def read_models_sc_overrides(file_path, full_model_name, source):
    mprint(f"read_models_overrides  {file_path}, {full_model_name}")
    # runs on both defaults and userconfig xml files
    #pass 'all' to get all of them
    tree = ET.parse(file_path)
    root = tree.getroot()

    model_overrides = []

    all_models = root.findall(f'.//sc_overrides')

    # Iterate through models elements
    for model_elem in all_models:
        # Assuming 'model' is the element containing the wildcard pattern

        unit_pattern = model_elem.find('model')
        if unit_pattern is not None:
            pattern = unit_pattern.text
            if pattern is not None:
                # Check if the full_model_name matches the pattern using re.match
                if re.match(pattern, full_model_name) or pattern == full_model_name:
                    name = model_elem.find('name').text
                    var = model_elem.find('var').text
                    sc_unit_elem = model_elem.find('sc_unit')
                    sc_unit = sc_unit_elem.text if sc_unit_elem is not None else ""
                    scale_elem = model_elem.find('scale')
                    scale = float(scale_elem.text) if scale_elem is not None else None

                    model_dict = {
                        'name': name,
                        'var': var,
                        'sc_unit': sc_unit,
                        'scale': scale,
                        'source': source
                    }
                    model_overrides.append(model_dict)
                else:
                    lprint (f"{pattern} does not match {full_model_name}")

    return model_overrides


def update_sc_overrides_with_user(defaults_ovr, user_ovr):
    updated_result = defaults_ovr.copy()
    items_to_append = []

    for user_model in user_ovr:
        user_model_name = user_model['name']
        user_model_var = user_model['var']
        user_model_sc_unit = user_model['sc_unit']
        user_model_scale = user_model['scale']

        # Check if the user override already exists in defaults_ovr
        for existing_item in updated_result:
            if existing_item['name'] == user_model_name:
                existing_item['var'] = user_model_var
                existing_item['sc_unit'] = user_model_sc_unit
                existing_item['scale'] = user_model_scale
                existing_item['source'] = 'user'
                break
        else:
            # If the user override is not found, add it to the updated_result
            updated_result.append({
                'name': user_model_name,
                'var': user_model_var,
                'sc_unit': user_model_sc_unit,
                'scale': user_model_scale,
                'source': 'user'
            })
    return updated_result


def erase_sc_override_from_xml(the_model, setting_name):
    mprint(f"erase_override_from_xml   {the_model}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()

    elements_to_remove = []
    for ovr_elem in root.findall(f'sc_overrides[model="{the_model}"]'
                                   f'[name="{setting_name}"]'):

        if ovr_elem is not None:
            elements_to_remove.append(ovr_elem)
        else:
            lprint ("override not found")

    # Remove the elements outside the loop
    for elem in elements_to_remove:
        root.remove(elem)
        # Write the modified XML back to the file
        write_userconfig_xml(tree)
        logging.info(f"Removed <sc_overrides> element with values: model={the_model}, name={setting_name}")


def write_sc_override_to_xml(the_model, the_var, setting_name, sc_unit='', scale=''):
    mprint(f"write_overrides_to_xml  {the_model}, {the_var}, {setting_name}, {scale}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    ovr_elem = None
    if the_model == '':
        return

    # Check if an identical <models> element already exists
    ovr_elem = root.find(f'.//sc_overrides[model="{the_model}"]'
                               f'[name="{setting_name}"]')

    if ovr_elem is not None:
        # Update the value of the existing element
        for child_elem in ovr_elem:
            if child_elem.tag == 'var':
                child_elem.text = str(the_var)
            if child_elem.tag == 'sc_unit':
                child_elem.text = str(sc_unit)
            if child_elem.tag == 'scale':
                child_elem.text = str(scale)
        if the_model != '':
            write_userconfig_xml(tree)
        logging.info(f"Updated <sc_overrides> element with values: "
                     f"var={the_var}, sc_unit={sc_unit}, model={the_model}, name={setting_name}, scale={scale}")

    else:
        # Check if an identical <models> element already exists; if so, skip
        model_elem_exists = any(
            all(
                element.tag == tag and element.text == value
                for tag, value in [
                    ("name", setting_name),
                    ("model", the_model),
                    ("var", the_var),
                    ("sc_unit", sc_unit),
                    ("scale", scale)
                ]
            )
            for element in root.iter("sc_overrides")
        )

        if model_elem_exists:
            lprint("<sc_overrides> element with the same values already exists. Skipping.")
        else:
            # Create child elements with the specified content
            overrides = ET.SubElement(root, "sc_overrides")
            if scale is None or scale == '':
                for tag, value in [("name", setting_name),
                                   ("model", the_model),
                                   ("var", the_var),
                                   ("sc_unit", sc_unit)]:
                    ET.SubElement(overrides, tag).text = value
            else:
                for tag, value in [("name", setting_name),
                                   ("model", the_model),
                                   ("var", the_var),
                                   ("sc_unit", sc_unit),
                                   ("scale", scale)]:
                    ET.SubElement(overrides, tag).text = value

            # Write the modified XML back to the file
            tree = ET.ElementTree(root)
            write_userconfig_xml(tree)
            logging.info(f"Added <sc_overrides> element with values:"
                         f"var={the_var}, sc_unit={sc_unit}, model={the_model}, name={setting_name}, scale={scale}")


def read_default_class_data(the_sim, the_class, instance_device=''):
    mprint(f"read_default_class_data  sim {the_sim}, class {the_class}")
    tree = ET.parse(defaults_path)
    root = tree.getroot()

    class_data = []
    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device
    # Iterate through models elements
    #for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{device}"]'):
    #for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{device}"]'):
    for model_elem in root.findall(f'.//classdefaults[sim="{the_sim}"][type="{the_class}"][device="{the_device}"]') + \
                      root.findall(f'.//classdefaults[sim="any"][type="{the_class}"][device="{the_device}"]') + \
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


def read_single_model( the_sim, aircraft_name, input_modeltype = '', instance_device = ''):
    logging.info (f"Reading from XML:  Sim: {the_sim}, Aircraft name: {aircraft_name}, Class: {input_modeltype}")

    time.sleep(0.1)

    print_counts = False
    print_each_step = False  # for debugging

    # Read models data first
    model_data, def_model_pattern = read_models_data(defaults_path, the_sim, aircraft_name,False,instance_device)
    user_model_data, usr_model_pattern = read_models_data(userconfig_path, the_sim, aircraft_name,False,instance_device)

    model_pattern = def_model_pattern
    if usr_model_pattern != '':
        model_pattern = usr_model_pattern

    # Extract the type from models data, if name is blank then use the class.  otherwise assume no type is set.
    # if aircraft_name == '':
    #     model_class = input_modeltype
    # else:
    #     model_class = ''   #self.model_type
    model_class = input_modeltype

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


    # get default Aircraft settings for this sim and device
    simdata = read_xml_file(the_sim, instance_device)

    if print_counts:  lprint(f"simdata count {len(simdata)}")

    # see what we got
    if print_each_step:
        lprint(f"\nSimresult: {the_sim} type: ''  device:{device}\n")
        printconfig(simdata)

    # combine base stuff
    defaultdata = simdata


    if print_counts:  lprint(f"defaultdata count {len(defaultdata)}")

    # get additional class default data
    if model_class != "":
        # Use the extracted type in read_xml_file
        craftresult = read_default_class_data(the_sim, model_class)

        if craftresult is not None:
            # place for eliminating !Class data?

            # merge if there is any
            default_craft_result = update_default_data_with_craft_result(defaultdata, craftresult)
        else:
            default_craft_result = defaultdata

        if print_counts:  lprint(f"default_craft_result count {len(default_craft_result)}")

        # see what we got
        if print_each_step:
            lprint(f"\nDefaultsresult: {the_sim} type: {model_class}  device:{device}\n")
            printconfig(default_craft_result)
    else:
        default_craft_result = defaultdata


    # get userconfig sim overrides

    user_default_data = read_user_sim_data(the_sim, instance_device)
    if user_default_data is not None:
        # merge if there is any
        def_craft_user_default_result = update_data_with_models(default_craft_result, user_default_data, 'Sim (user)')
    else:
        def_craft_user_default_result = default_craft_result

    if print_counts:  lprint(f"def_craft_user_default_result count {len(def_craft_user_default_result)}")


    if model_class != "":
        # get userconfg craft specific type overrides
        usercraftdata = read_user_class_data(the_sim, model_class, instance_device)
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
    final_wo_prereqs = eliminate_no_prereq(final_w_prereqs)
    # sorted_data = sorted(final_wo_prereqs, key=lambda x: (x['grouping'] != 'Basic', x['grouping'], x['name']))
    sorted_data = sorted(final_wo_prereqs, key=lambda x: float(x['order']))
    # lprint(f"final count {len(final_result)}")

    return model_class, model_pattern, sorted_data


def read_user_sim_data(the_sim, instance_device=''):
    mprint(f"read_user_sim_data {the_sim}")
    tree = ET.parse(userconfig_path)
    root = tree.getroot()

    sim_data = []
    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device
    # Iterate through models elements
    # for model_elem in root.findall(f'.//simSettings[sim="{the_sim}" or sim="any"][device="{device}" or device="any"]'):
    for model_elem in root.findall(f'.//simSettings[sim="{the_sim}"][device="{the_device}"]') + \
                       root.findall(f'.//simSettings[sim="any"][device="{the_device}"]') + \
                       root.findall(f'.//simSettings[sim="{the_sim}"][device="any"]')  + \
                       root.findall(f'.//simSettings[sim="any"][device="any"]'):

        if model_elem.find('name') is not None:

            name = model_elem.find('name').text
            value = model_elem.find('value').text
            unit_elem = model_elem.find('unit')
            unit = unit_elem.text if unit_elem is not None else ""
            replaced = 'Sim (user)'
            model_dict = {
                'name': name,
                'value': value,
                'unit': unit,
                'replaced': replaced
            }

            sim_data.append(model_dict)

    return sim_data

def read_user_class_data(the_sim, crafttype, instance_device=''):
    mprint(f"read_user_class_data  {the_sim}, {crafttype}")
    tree = ET.parse(userconfig_path)
    root = tree.getroot()

    model_data = []
    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device
    # Iterate through models elements
    #for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{device}"]'):
    for model_elem in root.findall(f'.//classSettings[sim="{the_sim}"][device="{the_device}"]'):     # + \
                      # root.findall(f'.//classSettings[sim="any"][device="{device}"]') + \
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


def write_models_to_xml(the_sim, the_model, the_value, setting_name, unit='', the_device=''):
    mprint(f"write_models_to_xml  {the_sim}, {the_model}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    model_elem = None
    if the_model == '':
        return
    if the_device == '':
        the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'

    # Check if an identical <models> element already exists
    model_elem = root.find(f'.//models[sim="{the_sim}"]'  
                               f'[device="{the_device}"]'
                               f'[model="{the_model}"]'
                               f'[name="{setting_name}"]')


    if model_elem is not None:
        # Update the value of the existing element
        for child_elem in model_elem:
            if child_elem.tag == 'value':
                child_elem.text = str(the_value)
            if child_elem.tag == 'unit':
                child_elem.text = str(unit)
        if the_model != '':
            write_userconfig_xml(tree)
        logging.info(f"Updated <models> element with values: sim={the_sim}, device={the_device}, "
                     f"value={the_value}, unit={unit}, model={the_model}, name={setting_name}")

    else:
        # Check if an identical <models> element already exists; if so, skip
        model_elem_exists = any(
            all(
                element.tag == tag and element.text == value
                for tag, value in [
                    ("name", setting_name),
                    ("model", the_model),
                    ("value", the_value),
                    ("unit", unit),
                    ("sim", the_sim),
                    ("device", the_device)
                ]
            )
            for element in root.iter("models")
        )

        if model_elem_exists:
            lprint("<models> element with the same values already exists. Skipping.")
        else:
            # Create child elements with the specified content
            models = ET.SubElement(root, "models")
            if unit is None or unit == '':
                for tag, value in [("name", setting_name),
                                   ("model", the_model),
                                   ("value", the_value),
                                   ("sim", the_sim),
                                   ("device", the_device)]:
                    ET.SubElement(models, tag).text = value
            else:
                for tag, value in [("name", setting_name),
                                   ("model", the_model),
                                   ("value", the_value),
                                   ("unit", unit),
                                   ("sim", the_sim),
                                   ("device", the_device)]:
                    ET.SubElement(models, tag).text = value

            # Write the modified XML back to the file
            tree = ET.ElementTree(root)
            write_userconfig_xml(tree)
            logging.info(f"Added <models> element with values: sim={the_sim}, device={the_device}, "
                         f"value={the_value}, unit={unit}, model={the_model}, name={setting_name}")


def write_class_to_xml(the_sim, the_class, the_value, setting_name, unit=''):
    mprint(f"write_class_to_xml  {the_sim}, {the_class}, {the_value}{unit}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'
    # Check if an identical <classSettings> element already exists
    class_elem = root.find(f'.//classSettings[sim="{the_sim}"]'
                           f'[device="{the_device}"]'
                           f'[type="{the_class}"]'
                           f'[name="{setting_name}"]')

    if class_elem is not None:
        # Update the value of the existing element
        for child_elem in class_elem:
            if child_elem.tag == 'value':
                child_elem.text = str(the_value)
        write_userconfig_xml(tree)
        logging.info(f"Updated <classSettings> element with values: sim={the_sim}, device={the_device}, "
                     f"value={the_value}, model={the_class}, name={setting_name}")

    else:
        # Create a new <classSettings> element
        classes = ET.SubElement(root, "classSettings")
        for tag, value in [("name", setting_name),
                           ("type", the_class),
                           ("value", the_value),
                           ("unit", unit),
                           ("sim", the_sim),
                           ("device", the_device)]:
            ET.SubElement(classes, tag).text = value

        # Write the modified XML back to the file
        tree = ET.ElementTree(root)
        write_userconfig_xml(tree)
        logging.info(f"Added <classSettings> element with values: sim={the_sim}, device={the_device}, "
                     f"value={the_value}{unit}, type={the_class}, name={setting_name}")


def write_sim_to_xml(the_sim, the_value, setting_name, unit=''):
    mprint(f"write_sim_to_xml {the_sim}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'
    # Check if an identical <simSettings> element already exists
    sim_elem = root.find(f'.//simSettings[sim="{the_sim}"]'
                         f'[device="{the_device}"]'
                         f'[name="{setting_name}"]')

    if sim_elem is not None:
        # Update the value of the existing element
        for child_elem in sim_elem:
            if child_elem.tag == 'value':
                child_elem.text = str(the_value)

        write_userconfig_xml(tree)
        logging.info(f"Updated <simSettings> element with values: sim={the_sim}, device={the_device}, "
                     f"value={the_value}, name={setting_name}")

    else:
        # Create a new <simSettings> element
        sims = ET.SubElement(root, "simSettings")
        for tag, value in [("name", setting_name),
                           ("value", the_value),
                           ("unit", unit),
                           ("sim", the_sim),
                           ("device", the_device)]:
            ET.SubElement(sims, tag).text = value

        # Write the modified XML back to the file
        tree = ET.ElementTree(root)
        write_userconfig_xml(tree)
        logging.info(
            f"Added <simSettings> element with values: sim={the_sim}, device={the_device}, value={the_value}{unit}, name={setting_name}")

def clone_pattern(the_sim, old_pattern, new_pattern):
    model_data, def_model_pattern = read_models_data(defaults_path, the_sim, old_pattern, True)
    user_model_data, usr_model_pattern = read_models_data(userconfig_path, the_sim, old_pattern, True)
    sc_overrides = read_sc_overrides(old_pattern)
    for item in user_model_data:
        model_data.append(item)
    for item in model_data:
        if item['unit'] is None:
            item['unit'] = ''
        write_models_to_xml(the_sim, new_pattern, item['value'],item['name'],item['unit'], item['device'])
    for item in sc_overrides:
        write_sc_override_to_xml(new_pattern,item['var'],item['name'],item['sc_unit'],item['scale'])

def write_converted_to_xml(differences):
    sim_set = []
    class_set = []
    model_set = []

    for dif in differences:
        if dif['new_ac']:
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


def erase_models_from_xml(the_sim, the_model, setting_name):
    mprint(f"erase_models_from_xml  {the_sim} {the_model}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'
    elements_to_remove = []
    for model_elem in root.findall(f'models[sim="{the_sim}"]'
                                   f'[device="{the_device}"]'                                   
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
        write_userconfig_xml(tree)
        logging.info(f"Removed <models> element with values: sim={the_sim}, device={the_device}, "
                  f"model={the_model}, name={setting_name}")

def erase_entire_model_from_xml(the_sim, the_model):
    mprint(f"erase_entire_models_from_xml  {the_sim} {the_model}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)

    elements_to_remove = []
    for model_elem in root.findall(f'models[sim="{the_sim}"]'                                 
                                   f'[model="{the_model}"]'):

        if model_elem is not None:
            elements_to_remove.append(model_elem)
        else:
            lprint ("model not found")

    # Remove the elements outside the loop
    for elem in elements_to_remove:
        root.remove(elem)
        # Write the modified XML back to the file
        write_userconfig_xml(tree)
        logging.info(f"Removed all <models> elements with values: sim={the_sim} model={the_model}")


def erase_class_from_xml( the_sim, the_class, the_value, setting_name):
    mprint(f"erase_class_from_xml  {the_sim} {the_class}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'
    elements_to_remove = []
    for class_elem in root.findall(f'.//classSettings[sim="{the_sim}"]'
                                   f'[device="{the_device}"]'
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
        write_userconfig_xml(tree)
        logging.info(f"Removed <classSettings> element with values: sim={the_sim}, device={the_device}, "
                  f"value={the_value}, type={the_class}, name={setting_name}")


def erase_sim_from_xml(the_sim, the_value, setting_name):
    mprint(f"erase_sim_from_xml  {the_sim} {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = ET.parse(userconfig_path)
    root = tree.getroot()
    the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'
    elements_to_remove = []
    for sim_elem in root.findall(f'.//simSettings[sim="{the_sim}"]'
                                   f'[device="{the_device}"]'
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
        write_userconfig_xml(tree)
        logging.info(f"Removed <simSettings> element with values: sim={the_sim}, device={the_device}, value={the_value}, name={setting_name}")


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

# would be nice if it grouped sim,class,models

    # Prettify the XML
    xml_str = xml.dom.minidom.parseString(ET.tostring(root)).toprettyxml()
    with open(userconfig_path, 'w') as xml_file:
        xml_file.write(xml_str)



def read_prereqs():
    tree = ET.parse(defaults_path)
    root = tree.getroot()

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in root.findall(f'.//defaults'):

        name = defaults_elem.find('name').text
        prereq_elem = defaults_elem.find('prereq')
        prereq = (f"{prereq_elem.text}") if prereq_elem is not None else ""


        # Check if 'prereq' is already in the list
        found = False
        for data_dict in data_list:
            if data_dict['prereq'] == prereq:
                data_dict['count'] += 1
                found = True
                break

        # If 'prereq' is not in the list, add a new entry
        if not found and prereq != '':
            data_list.append({'prereq': prereq, 'value': 'False', 'count': 1})


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

def eliminate_no_prereq(datalist):
    newlist = []
    for d_item in datalist:
        add_item = True
        if d_item['prereq'] != '':
            add_item = False
            for p_item in datalist:
                if d_item['prereq'] == p_item['name'] and p_item['value'].lower() == 'true':
                    add_item = True
                    break

        if add_item:
            newlist.append(d_item)

    return newlist

def filter_rows(data_list):
    valid_rows = []

    def has_valid_prereq(item):
        if 'prereq' not in item or item['prereq'] == '':
            return True
        for row in data_list:
            if row['name'] == item['prereq'] and row['value'].lower() == 'true' and has_valid_prereq(row):
                return True
        return False

    for item in data_list:
        if has_valid_prereq(item):
            valid_rows.append(item)

    return valid_rows

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