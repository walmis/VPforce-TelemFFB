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
import inspect
# import globalvars
import logging
import time
import xml.etree.ElementTree as ET
import os
import re
import xml.dom.minidom
import telemffb.globals as G
from telemffb import utils

print_debugs = False
print_method_calls = False

device = ''
userconfig_path = ''
defaults_path = ''
global auto_user_root  #
global auto_user_tree
global auto_defaults_root  #



def update_roots():
    global auto_user_root, auto_defaults_root, auto_user_tree
    auto_user_tree = try_parse(G.userconfig_path)
    auto_user_root = auto_user_tree.getroot()
    auto_defaults_root = try_parse(G.defaults_path).getroot()

def dbprint(color, msg):
    reset = '\033[0m'
    match color:
        case "red":
            ccode = '\033[91m'
        case 'yellow':
            ccode = '\033[93m'
        case 'blue':
            ccode = '\033[94m'
        case 'green':
            ccode = '\033[92m'
        case _:
            ccode = '\033[0m'
    print(f"{ccode}{msg}{reset}")


def try_parse(file_path, max_attempts=3, delay=0.1):
    """
    Tries to parse an XML file up to max_attempts times with a delay between attempts.

    :param file_path: Path to the XML file.
    :param max_attempts: Maximum number of attempts to parse the file.
    :param delay: Delay (in seconds) between attempts.
    :return: Parsed XML tree or None if all attempts fail.
    """
    attempt = 0
    while attempt < max_attempts:
        try:
            tree = ET.parse(file_path)
            return tree
        except ET.ParseError as e:
            attempt += 1
            # dbprint("yellow", f"Attempt {attempt} failed: {e}")
            time.sleep(delay)
    dbprint("red", f"All {max_attempts} attempts to parse the file failed.")
    return None

def write_userconfig_xml(tree : ET.ElementTree):
    consolidate_sort_and_write_userconfig(tree)

def really_write_userconfig_xml(tree : ET.ElementTree):
    ET.indent(tree, " ")
    tree.write(userconfig_path, "utf-8")

# def consolidate_sort_and_write_userconfig(tree):
#     """
#     Deduplicates and reorders entries in the userconfig XML for consistency and performance.
#
#     Functionality:
#     - Ensures only one <profileMappings> entry exists per (sim, cls, model).
#     - Ensures <models> entries are byte-unique.
#     - Sorts <profileMappings> by (profile, model, cls, sim).
#     - Sorts <models> by (sim, model, device, name) for stable logical grouping.
#
#     Args:
#         tree (ElementTree): Parsed XML tree from userconfig_path.
#
#     Effects:
#         - Modifies the input tree in-place.
#         - Calls write_userconfig_xml(tree) to persist changes to disk.
#     """
#     root = tree.getroot()
#
#     # 1. Collect <profileMappings> entries uniquely by (sim, cls, model)
#     profile_map = {}
#     for elem in root.findall('profileMappings'):
#         key = (elem.findtext('sim'), elem.findtext('cls'), elem.findtext('model'))
#         profile_map[key] = ET.tostring(elem)  # last write wins
#
#     # 2. Deduplicate exact <models> entries (byte-level)
#     seen_model_bytes = set()
#     unique_models = []
#     for elem in root.findall('models'):
#         raw = ET.tostring(elem)
#         if raw not in seen_model_bytes:
#             seen_model_bytes.add(raw)
#             unique_models.append(elem)
#
#     # 3. Remove all <profileMappings> and <models> before reinserting
#     for child in list(root):
#         if child.tag in ('profileMappings', 'models'):
#             root.remove(child)
#
#     # 4. Sort <profileMappings> by (profile, model, cls, sim)
#     def profile_sort_key(raw_bytes):
#         e = ET.fromstring(raw_bytes)
#         return (
#             e.findtext('active_profile') or '',
#             e.findtext('model') or '',
#             e.findtext('cls') or '',
#             e.findtext('sim') or ''
#         )
#     for raw in sorted(profile_map.values(), key=profile_sort_key):
#         root.append(ET.fromstring(raw))
#
#     # 5. Sort <models> by (sim, model, device, name)
#     def model_sort_key(elem):
#         return (
#             elem.findtext('sim') or '',
#             elem.findtext('model') or '',
#             elem.findtext('profile') or '',
#             elem.findtext('device') or '',
#             elem.findtext('name') or ''
#         )
#     for elem in sorted(unique_models, key=model_sort_key):
#         root.append(elem)
#
#     # 6. Persist updated XML to disk
#     write_userconfig_xml(tree)
def consolidate_sort_and_write_userconfig(tree):
    """
    Deduplicates and reorders entries in the userconfig XML for consistency and performance.

    Functionality:
    - Ensures only one <profileMappings> entry exists per (sim, cls, model)
    - Ensures <models> entries are byte-unique
    - Sorts <profileMappings> by (profile, model, cls, sim)
    - Sorts <models> by (sim, model, profile, device, name)
    - Sorts <simSettings> by (sim, device, name)
    - Sorts <classSettings> by (sim, type, device, name)
    - Sorts <sc_overrides> by (model, name)

    Args:
        tree (ElementTree): Parsed XML tree from userconfig_path

    Effects:
        - Modifies the input tree in-place
        - Writes the updated XML to disk
    """
    root = tree.getroot()

    # Collect and deduplicate <profileMappings>
    profile_map = {}
    for elem in root.findall('profileMappings'):
        key = (elem.findtext('sim'), elem.findtext('cls'), elem.findtext('model'))
        profile_map[key] = ET.tostring(elem)  # last wins

    # Collect and deduplicate <models>
    seen_model_bytes = set()
    models_list = []
    for elem in root.findall('models'):
        raw = ET.tostring(elem)
        if raw not in seen_model_bytes:
            seen_model_bytes.add(raw)
            models_list.append(elem)

    # Collect others
    sim_list = root.findall('simSettings')
    class_list = root.findall('classSettings')
    sc_list = root.findall('sc_overrides')

    # Clear old entries
    for child in list(root):
        if child.tag in ('profileMappings', 'models', 'simSettings', 'classSettings', 'sc_overrides'):
            root.remove(child)


    # Sort and reinsert <simSettings>
    def sim_key(e):
        return (
            e.findtext('sim', ''),
            e.findtext('device', ''),
            e.findtext('name', '')
        )
    for e in sorted(sim_list, key=sim_key):
        root.append(e)

    # Sort and reinsert <classSettings>
    def class_key(e):
        return (
            e.findtext('sim', ''),
            e.findtext('type', ''),
            e.findtext('device', ''),
            e.findtext('name', '')
        )
    for e in sorted(class_list, key=class_key):
        root.append(e)

    # Sort and reinsert <sc_overrides>
    def sc_key(e):
        return (
            e.findtext('model', ''),
            e.findtext('name', '')
        )
    for e in sorted(sc_list, key=sc_key):
        root.append(e)

    # Sort and reinsert <profileMappings>
    def profile_key(raw_bytes):
        e = ET.fromstring(raw_bytes)
        return (
            e.findtext('sim') or '',
            e.findtext('cls') or '',
            e.findtext('model') or '',
            e.findtext('active_profile') or ''
        )
    for raw in sorted(profile_map.values(), key=profile_key):
        root.append(ET.fromstring(raw))

    # Sort and reinsert <models>
    def model_key(e):
        return (
            e.findtext('sim', ''),
            e.findtext('model', ''),
            e.findtext('profile', ''),
            e.findtext('device', ''),
            e.findtext('name', '')
        )
    for e in sorted(models_list, key=model_key):
        root.append(e)

    # Finalize save
    really_write_userconfig_xml(tree)

def convert_userconfig():
    """
    Updates a userconfig.xml file to support new profile mappings.
    - Ensures <profileMappings> element exists
    - Ensures every <model> has a <profile> child, defaulting to 'Auto User' for existing configs
    -
    - If any edits were made, Writes the updated XML back using xmlutils.consolidate_sort_and_write_userconfig
    """
    tree = try_parse(G.userconfig_path)
    if tree is None:
        logging.exception(f"Failed to parse: {G.userconfig_path}")
        return

    root = tree.getroot()
    did_work = False
    mappings_was_empty = False
    # Add <profileMappings> if missing
    if not root.findall("profileMappings"):
        header = ET.Element("profileMappings")
        for tag in ["model", "sim", "cls", "active_profile"]:
            ET.SubElement(header, tag).text = None
        root.append(header)
        did_work = True
        mappings_was_empty = True

    # Ensure each <model> has a <profile> element
    cls_sourced_list = []
    models = root.find("models")
    if models is not None:
        for model_entry in root.findall("models"):
            s = model_entry.findtext('sim')
            m = model_entry.findtext('model')
            if model_entry.find("profile") is None:
                ET.SubElement(model_entry, "profile").text = "Auto User"
                did_work = True
            if mappings_was_empty:
                if (s, m) not in cls_sourced_list:
                    cls = get_class_for_sim_model(s,m)
                    if cls is not None:
                        # add mapping for "Auto User" into the profileMappings table
                        mapping = ET.Element("profileMappings")
                        ET.SubElement(mapping, "model").text = m
                        ET.SubElement(mapping, "sim").text = s
                        ET.SubElement(mapping, "cls").text = cls
                        ET.SubElement(mapping, "active_profile").text = "Auto User"
                        root.append(mapping)
                        # add a "profile" name entry in the user models table
                        p_mapping =  ET.Element("models")
                        ET.SubElement(p_mapping, "name").text = 'profile'
                        ET.SubElement(p_mapping, "model").text = m
                        ET.SubElement(p_mapping, "value").text = cls
                        ET.SubElement(p_mapping, "sim").text = s
                        ET.SubElement(p_mapping, "device").text = 'any'
                        ET.SubElement(p_mapping, "profile").text = "Auto User"
                        root.append(p_mapping)
                        did_work = True

                    else:
                        print(f"For {s}:{m}, c is {cls}")

                    cls_sourced_list.append((s,m))


    # Save updated file with sorting/consolidation
    if did_work:
        consolidate_sort_and_write_userconfig(tree)
        return True
    else:
        return False


def update_vars(_device, _userconfig_path, _defaults_path):
    global device, userconfig_path, defaults_path
    device = _device
    userconfig_path = _userconfig_path
    defaults_path = _defaults_path

def get_available_profiles(sim, cls, model):
    """
    Returns a list of available profiles for the given sim, class, and model.
    Args:
        sim: the sim name (DCS, MSFS, etc)
        cls: the class name (PropellerAircraft, JetAircraft, etc)
        model: the model match string (e.g. 'P-51D.*' or '^P47D$')
    Returns:
        A list of available profiles for the given sim, class, and model.
    """
    # tree = try_parse(userconfig_path)
    # root = tree.getroot()
    profile_list = []
    for model_elem in auto_user_root.findall(f'.//models[sim="{sim}"][model="{model}"]'):
        profile_name = model_elem.find('profile')
        if profile_name is not None:
            p_name = profile_name.text
            if p_name is not None and p_name not in profile_list:
                profile_list.append(p_name)
    # Also look in defaults root for a match for any object with a 'type' <name> attribute and a <value> of "class name"..
    # add a "default" profile to the list if found
    model_elem = auto_defaults_root.find(f'.//models[sim="{sim}"][model="{model}"][name="type"][value="{cls}"]')
    if model_elem is not None:
        profile_list.append('default')

    return profile_list

def get_classes_for_sim(sim):
    # tree = try_parse(defaults_path)
    # root = tree.getroot()
    classes = []
    # print(f"LOOKING FOR CLASSES FOR SIM {sim}")
    for elem in auto_defaults_root.findall(f'.//classes[sim="{sim}"]'):
        class_name = elem.find('class_name')
        if class_name is not None:
            name = class_name.text
            if name is not None:
                classes.append(name)
    return classes

def get_sims():
    # tree = try_parse(defaults_path)
    # root = tree.getroot()
    sims = []
    for elem in auto_defaults_root.findall(f'.//sims'):
        sim_name = elem.find('sim')
        if sim_name is not None:
            name = sim_name.text
            sims.append(name)
    return sims



def read_xml_file(the_sim, instance_device=''):
    # mprint(f"read_xml_file  {the_sim}")
    # tree = try_parse(defaults_path)
    # root = tree.getroot()

    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in auto_defaults_root.findall(f'.//defaults[{the_sim}="true"][{the_device}="true"]'):

        grouping_elem = defaults_elem.find('grouping')
        grouping = grouping_elem.text if grouping_elem is not None else ""
        order = defaults_elem.find('order').text
        name = defaults_elem.find('name').text
        displayname = defaults_elem.find('displayname').text
        datatype = defaults_elem.find('datatype').text
        exclusive_with = defaults_elem.find('exclusive_with')
        exclusive_with = exclusive_with.text if exclusive_with is not None else ''
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
            'exclusive_with': exclusive_with,
            'value': value,
            'unit': unit,
            'datatype': datatype,
            'validvalues': validvalues,
            'replaced': replaced,
            'prereq': prereq,
            'info': info,
            'sliderfactor': sliderfactor,
            'device_text': device_text,
            'indent': 0
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

    # tree = try_parse(defaults_path)
    # root = tree.getroot()

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in auto_defaults_root.findall(f'.//defaults[{the_sim}="true"][any="true"]'):

        name_elem = defaults_elem.find('name')
        if name_elem is not None:
            name = name_elem.text
            data_list.append(name)

    return data_list



# def read_user_models(sim, cls, default_only=False):
#
#     """
#     A method to, by default, retrieve a list of models for which the user has created a custom profile.  For custom added
#     aircraft, there will be a "type" entry in the user config.  For modifications to default profiles, there are only
#     settings mapped to the aircraft name.
#     Optionally, can return only a list of default models for the sim, class.
#     Args:
#         sim: name of sim (DCS, MSFS, IL2, XPLANE)
#         cls: name oircraft class (PropellerAircraft, JetAircraft, etc)
#         default_only: if True, only the default models will be returned, otherwise only the user models will be returned
#
#     Returns: A sorted list of models
#
#     """
#
#     ## First read the default models
#     ## This data will be used as a referenc to get the class name for aircraft that are in the user table but do not have a "type"
#     ## meaning they are modifications of a default profile
#
#     total_aircraft = []
#     for model_elem in auto_defaults_root.findall(".//models"):
#         if model_elem.findtext("sim") == sim and model_elem.findtext("name") == "type" and model_elem.findtext("value") == cls:
#             # get all models of type 'cls' for the given sim.  Create a list of aircraft names matching the type and sim (i.e. ("P-51D", "P47D")
#             model_name = model_elem.findtext("model")
#             model_value = model_elem.findtext("value")
#             total_aircraft.append((model_name, "Default"))
#
#     if default_only:
#         return sorted(total_aircraft)
#     # Now we know all the models and their type for the given sim.
#     # Now we read the user config and see if they have any custom models.  Custom models will have a "type" entry whereas settings that modify a default profile will not
#
#     for model_elem in auto_user_root.findall(".//models"):
#         if model_elem.findtext("sim") == sim and model_elem.findtext("name") == "type" and model_elem.findtext("value") == cls:
#             # get all models of type 'cls' for the given sim.  Create a list of aircraft names matching the type and sim (i.e. ("CustomAircraft1.*", "Custom Airfraft Two.*")
#             # this list may be empty if the user has never manually created a profile
#             model_name = model_elem.findtext("model")
#             model_value = model_elem.findtext("value")
#             profile_name = model_elem.findtext("profile")
#             total_aircraft.append((model_name, profile_name))
#
#     # Now 'total_aircraft' contains all of the models for the given sim of the given class, both default models as well as user defined models
#     # Now we will look at all the settings in the users config.  If an entry exists for a model, that means the user has a "user profile" for that model.
#     results = []
#     for user_setting in auto_user_root.findall(f'.//models'):
#         # we now have all entries that pertain to 'sim', look for each model name in the known list of aircraft of type cls.  If it is in the lst,
#         # the user has a "profile" for that aircraft, whether it is based on a default profile or a user profile.
#         model_name = user_setting.findtext("model")
#         profile_name = user_setting.findtext("profile")
#         if any(model_name == aircraft for aircraft, _ in total_aircraft) and (model_name, profile_name) not in results:
#             # print(f"MODEL NAME: {model_name}")
#             results.append((model_name, profile_name))
#
#     return sorted(results)

def read_user_models(sim, cls, default_only=False, user_only=True, both=False):

    """
    A method to, by default, retrieve a list of models for which the user has created a custom profile.  For custom added
    aircraft, there will be a "type" entry in the user config.  For modifications to default profiles, there are only
    settings mapped to the aircraft name.
    Optionally, can return only a list of default models for the sim, class.
    Args:
        sim: name of sim (DCS, MSFS, IL2, XPLANE)
        cls: name oircraft class (PropellerAircraft, JetAircraft, etc)
        default_only: if True, only the default models will be returned, otherwise only the user models will be returned

    Returns: A sorted list of models

    """

    ## First read the default models
    ## This data will be used as a referenc to get the class name for aircraft that are in the user table but do not have a "type"
    ## meaning they are modifications of a default profile

    total_aircraft = []
    if default_only or both:
        for model_elem in auto_defaults_root.findall(".//models"):
            if model_elem.findtext("sim") == sim and model_elem.findtext("name") == "type" and model_elem.findtext("value") == cls:
                # get all models of type 'cls' for the given sim.  Create a list of aircraft names matching the type and sim (i.e. ("P-51D", "P47D")
                model_name = model_elem.findtext("model")
                model_value = model_elem.findtext("value")
                total_aircraft.append((model_name, "default"))
        if default_only:
            return sorted(total_aircraft)
    if user_only or both:
        for model_elem in auto_user_root.findall(".//models"):
            if model_elem.findtext("sim") == sim and model_elem.findtext("value") == cls:
                if model_elem.findtext("name") == "type" or model_elem.findtext("name") == "profile":
                    # get all models of type 'cls' for the given sim.  Create a list of aircraft names matching the type and sim (i.e. ("CustomAircraft1.*", "Custom Airfraft Two.*")
                    # this list may be empty if the user has never manually created a profile
                    model_name = model_elem.findtext("model")
                    model_value = model_elem.findtext("value")
                    profile_name = model_elem.findtext("profile")
                    if (model_name, profile_name) not in total_aircraft:
                        total_aircraft.append((model_name, profile_name))

    return total_aircraft

def read_models(the_sim, the_class=''):
    all_models = ['']
    # tree = try_parse(defaults_path)
    # root = tree.getroot()
    if the_class == '':
        def_models =  auto_defaults_root.findall(f'.//models[sim="{the_sim}"][device="{device}"]') + \
                      auto_defaults_root.findall(f'.//models[sim="any"][device="{device}"]') + \
                      auto_defaults_root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                      auto_defaults_root.findall(f'.//models[sim="any"][device="any"]')
    else:
        def_models = auto_defaults_root.findall(f'.//models[sim="{the_sim}"][value="{the_class}"]')

    for model_elem in def_models:
        pattern = model_elem.find('model')
        # lprint (pattern.text)
        if pattern is not None:
            if pattern.text not in all_models:
                all_models.append(pattern.text)

    # create_empty_userxml_file() - handled by TelemFFB on startup via utils.py
    # tree = try_parse(userconfig_path)
    # root = tree.getroot()
    if the_class == '':
        usr_models =  auto_user_root.findall(f'.//models[sim="{the_sim}"][device="{device}"]') + \
                      auto_user_root.findall(f'.//models[sim="any"][device="{device}"]') + \
                      auto_user_root.findall(f'.//models[sim="{the_sim}"][device="any"]') + \
                      auto_user_root.findall(f'.//models[sim="any"][device="any"]')
    else:
        usr_models = auto_user_root.findall(f'.//models[sim="{the_sim}"][value="{the_class}"]')
    for model_elem in usr_models:
        pattern = model_elem.find('model')
        # lprint (pattern.text)
        if pattern is not None:
            if pattern.text not in all_models:
                all_models.append(pattern.text)

    return sorted(all_models)

def new_read_usermodels_data(file_path, sim, full_model_name, alldevices=False, instance_device = '', active_profile=None):
    mprint(f"read_models_data  {file_path}, {sim}, {full_model_name}")
    if active_profile is None:
        active_profile = G.settings_mgr.active_profile
    tree = try_parse(file_path)
    root = tree.getroot()

    model_data = []
    found_pattern = ''

    the_device = instance_device or device

    match_pattern = get_pattern_by_sim_fullname(sim, full_model_name)

    # Collect models with 'device' set to 'any'
    any_models = root.findall(f'.//models[sim="{sim}"][model="{match_pattern}"][device="any"]')

    # Collect models with specific devices
    all_models = root.findall(f'.//models[sim="{sim}"][model="{match_pattern}"][device="{the_device}"]')

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

def read_models_data(file_path, sim, full_model_name, alldevices=False, instance_device = '', user=False, profile=None):
    mprint(f"read_models_data  {file_path}, {sim}, {full_model_name}")
    # runs on both defaults and userconfig xml files
    if profile is None:
        profile = G.settings_mgr.active_profile
    tree = try_parse(file_path)
    root = tree.getroot()

    model_data = []
    found_pattern = ''

    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device

    profile_match = f"[profile='{profile}']" if user else ''

    if alldevices:
        # Iterate through models elements
        #for model_elem in root.findall(f'.//models[sim="{self.sim}"][device="{device}"]'):
        any_models = root.findall(f'.//models[sim="any"]{profile_match}')

        all_models = root.findall(f'.//models[sim="{sim}"]{profile_match}')

    else:
        # Collect models with 'device' set to 'any' or both 'sim' and 'device' set to 'any'
        any_models = root.findall(f'.//models[sim="{sim}"][device="any"]{profile_match}') + \
                     root.findall(f'.//models[sim="any"][device="any"]{profile_match}')

        # Collect models with specific devices
        all_models = root.findall(f'.//models[sim="{sim}"][device="{the_device}"]{profile_match}') + \
                     root.findall(f'.//models[sim="any"][device="{the_device}"]{profile_match}')

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
    tree = try_parse(file_path)
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
    tree = auto_user_tree
    root = auto_user_root

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
    tree = auto_user_tree
    root = auto_user_root
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
                                   ("scale", str(scale))]:
                    ET.SubElement(overrides, tag).text = value

            # Write the modified XML back to the file
            tree = ET.ElementTree(root)
            write_userconfig_xml(tree)
            logging.info(f"Added <sc_overrides> element with values:"
                         f"var={the_var}, sc_unit={sc_unit}, model={the_model}, name={setting_name}, scale={scale}")


def read_default_class_data(the_sim, the_class, instance_device=''):
    mprint(f"read_default_class_data  sim {the_sim}, class {the_class}")
    # tree = try_parse(defaults_path)
    # root = tree.getroot()

    class_data = []
    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device
    # Iterate through models elements
    for model_elem in auto_defaults_root.findall(f'.//classdefaults_{the_sim}[sim="{the_sim}"][type="{the_class}"][device="{the_device}"]') + \
                      auto_defaults_root.findall(f'.//classdefaults_any[sim="any"][type="{the_class}"][device="{the_device}"]') + \
                      auto_defaults_root.findall(f'.//classdefaults_{the_sim}[sim="{the_sim}"][type="{the_class}"][device="any"]') + \
                      auto_defaults_root.findall(f'.//classdefaults_any[sim="any"][type="{the_class}"][device="any"]'):

        if model_elem.find('name') is not None:

            name = model_elem.find('name').text
            value_elem = model_elem.find('value')
            value = value_elem.text if value_elem is not None else ""
            unit_elem = model_elem.find('unit')
            unit = unit_elem.text if unit_elem is not None else ""

            model_dict = {
                'name': name,
                'value': value,
                'unit': unit,
                'replaced': 'Class Default'
            }

            class_data.append(model_dict)
    removal_data = []
    for model_elem in auto_defaults_root.findall(f'.//classdefaults_{the_sim}[sim="{the_sim}"][type="!{the_class}"][device="{the_device}"]') + \
                      auto_defaults_root.findall(f'.//classdefaults_any[sim="any"][type="!{the_class}"][device="{the_device}"]') + \
                      auto_defaults_root.findall(f'.//classdefaults_{the_sim}[sim="{the_sim}"][type="!{the_class}"][device="any"]') + \
                      auto_defaults_root.findall(f'.//classdefaults_any[sim="any"][type="!{the_class}"][device="any"]'):
        removal_data.append(model_elem.find('name').text)
    if not removal_data:
        removal_data = None
    return class_data, removal_data

def remove_dicts_by_names(data_list, removal_list):
    return [d for d in data_list if d.get('name') not in removal_list]

def read_single_model( the_sim, aircraft_name, input_modeltype = '', instance_device = '', active_profile=None):
    """
        Loads and merges configuration data for a specific aircraft in a simulator, producing a fully-resolved,
        sorted list of config parameters based on multiple levels of overrides.

        This function:
        - Loads default and user-specific model data from XML files
        - Detects the model's class (if not provided)
        - Applies layered overrides from:
            1. Simulator-level defaults
            2. Class-level defaults
            3. User-level simulator overrides
            4. User-level class overrides
            5. Model-specific defaults
            6. Model-specific user profile settings
        - Merges and sorts the resulting config, respecting prerequisites and eliminating invalid entries

        Args:
            the_sim (str): Simulator name (e.g., "DCS", "MSFS").
            aircraft_name (str): Full name or regex pattern identifying the aircraft.
            input_modeltype (str, optional): Override for the model's class (e.g., "PropellerAircraft").
            instance_device (str, optional): Specific input device name (e.g., "joystick").
            active_profile (str, optional): User profile to use. Defaults to currently active profile.

        Returns:
            tuple:
                - model_class (str): Final resolved aircraft class (from input or extracted from XML).
                - model_pattern (str): Regex or name pattern matched from the XML.
                - sorted_data (list of dict): Fully resolved and sorted configuration items.
        """
    logging.info (f"Reading from XML:  Sim: {the_sim}, Aircraft name: {aircraft_name}, Class: {input_modeltype}")
    if active_profile is None:
        active_profile = G.settings_mgr.active_profile
    time.sleep(0.1)

    print_counts = False
    print_each_step = False  # for debugging

    # Read models data first
    model_data, def_model_pattern = read_models_data(defaults_path, the_sim, aircraft_name,False,instance_device)
    user_model_data, usr_model_pattern = read_models_data(userconfig_path, the_sim, aircraft_name,False,instance_device, user=True, profile=active_profile)

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
        craftresult, removal_data = read_default_class_data(the_sim, model_class)

        if removal_data is not None:
            defaultdata = remove_dicts_by_names(defaultdata, removal_data)

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
    """
       Extracts simulator-level user configuration overrides from the user config XML.

       This method searches the `<simSettings>` section of the parsed user XML tree (`auto_user_root`) and returns
       a list of dictionaries containing user-defined settings. It supports specific matching on simulator and
       device values, as well as fallback matches using "any".

       Args:
           the_sim (str): The simulator name (e.g., "DCS", "MSFS").
           instance_device (str, optional): Specific device to filter for (e.g., "joystick").
                                            If empty, falls back to the global `device` value.

       Returns:
           list of dict: Each dictionary contains:
               - 'name': The setting name
               - 'value': The setting value
               - 'unit': Optional unit string (may be empty)
               - 'replaced': A fixed string indicating this data replaces 'Sim (user)' level
       """
    mprint(f"read_user_sim_data {the_sim}")
    # tree = try_parse(userconfig_path)
    # root = tree.getroot()

    sim_data = []
    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device
    # Iterate through models elements
    # for model_elem in root.findall(f'.//simSettings[sim="{the_sim}" or sim="any"][device="{device}" or device="any"]'):
    for model_elem in auto_user_root.findall(f'.//simSettings[sim="{the_sim}"][device="{the_device}"]') + \
                       auto_user_root.findall(f'.//simSettings[sim="any"][device="{the_device}"]') + \
                       auto_user_root.findall(f'.//simSettings[sim="{the_sim}"][device="any"]')  + \
                       auto_user_root.findall(f'.//simSettings[sim="any"][device="any"]'):

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
    """
        Retrieves user-defined configuration overrides for a specific aircraft class from the user config XML.

        This function scans the `<classSettings>` section in the global `auto_user_root` tree for entries that
        match the given simulator and device. It filters entries where the `<type>` tag (treated as a regex)
        matches the provided aircraft class type (`crafttype`).

        Args:
            the_sim (str): Simulator name (e.g., "DCS", "MSFS").
            crafttype (str): Aircraft class name to match (e.g., "JetAircraft").
            instance_device (str, optional): Device name to filter by. Defaults to global `device` if empty.

        Returns:
            list of dict: A list of matching configuration dictionaries, each with:
                - 'name': Configuration key
                - 'value': Configuration value
                - 'unit': Optional unit for the value (may be empty)
                - 'replaced': A fixed string "Class (user)" indicating the override level
        """
    mprint(f"read_user_class_data  {the_sim}, {crafttype}")
    # tree = try_parse(userconfig_path)
    # root = tree.getroot()

    model_data = []
    if instance_device == '':
        the_device = device
    else:
        the_device = instance_device
    # Iterate through models elements
    #for model_elem in root.findall(f'.//models[sim="{the_sim}"][device="{device}"]'):
    for model_elem in auto_user_root.findall(f'.//classSettings[sim="{the_sim}"][device="{the_device}"]'):     # + \
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

def get_pattern_by_sim_fullname(sim, full_name):
    """
    Knowing only the sim name and the full name of the model as received in telemetry, this function returns the
    pattern name stored in the config if it finds a item that will regex match the full name.
    First looks in userconfig, then defaults.
    Looks for all "type" elements and inspects the "model" element to see if it matches with the full name.
    Args:
        sim: name of sim (DCS, MSFS, etc)
        full_name: eg. "Super Special Awesome Aircraft Livery Green"

    Returns:
        match pattern if found, else None
    """
    def re_match(pattern, full_model_name):
        if re.match(pattern, full_model_name) or pattern == full_model_name:
            return True
        else:
            return False

    # users_root = try_parse(userconfig_path).getroot()
    user_elements = auto_user_root.findall(f'models[sim="{sim}"][name="type"]')
    for element in user_elements:
        if element is not None:
            match_string =  element.findtext("model")
            if re_match(match_string, full_name):
                # logging.info(f"!@!@!@!@!@!@!@!@!@!@!@!@!@Found a user config match, '{match_string}', for {full_name} in {sim}")
                return match_string
            else:
                continue
    # If we get here, there was no match in the user config, look in default config
    # defaults_root = try_parse(defaults_path).getroot()
    defaults_elements = auto_defaults_root.findall(f'models[sim="{sim}"][name="type"]')
    for element in defaults_elements:
        if element is not None:
            match_string =  element.findtext("model")
            if re_match(match_string, full_name):
                # logging.info(f"!@!@!@!@!@!@!@!@!@!@!@!@!@Found a default config match, '{match_string}', for {full_name} in {sim}")
                return match_string
            else:
                continue
    # If we get here, there was no match in the default config or the user config, return None
    logging.warning(f"!@!@!@!@!@!@!@!@!@!@!@!@!@No match found for {full_name} in {sim}")
    return None


def get_class_for_sim_model(sim, model):
    """
    Find and return the Aircraft Class for a given model/sim combo
    - Looks in user table first and tries to find a "type" attribute match, returns the value if found
    - If no match found in user table, it looks in defaults table to find a match for sim/aircraft and returns the type value
    - If no match found, returns None
    Args:
        sim: the sim (DCS, MSFS, etc)
        model: the model match string ('Super Special Aircraft.*)

    Returns: Aircraft Class (i.e. "PropellerAircraft") if found, else None
    """
    # look for match in user config
    # users_root = try_parse(userconfig_path).getroot()
    entry = auto_user_root.find(f'models[sim="{sim}"][model="{model}"][name="type"]')
    if entry is not None:
        cls = entry.findtext('value')
        return cls

    # defaults_root = try_parse(defaults_path).getroot()
    entry = auto_defaults_root.find(f'models[sim="{sim}"][model="{model}"][name="type"]')
    if entry is not None:
        cls = entry.findtext('value')
        return cls

    return None

def get_active_profile_for_model(sim, cls, model, users_root=None):
    """
    Given the sim, class, and model, return the currently active profile for that aircraft.

    Priority order:
    1. Check <profileMappings> section for an explicit active profile entry.
    2. Check <models> section for an implicit active profile.
    3. Look in default config, return "default" if match found
    4. return None if no available active profile is found

    Args:
        sim (str): Simulator name
        cls (str): Aircraft class
        model (str): Aircraft model
        users_root (ElementTree.Element, optional): Pre-parsed userconfig root to avoid redundant parsing.

    Returns:
        str: Active profile name (e.g. "JoystickA", or "default") or None if no active profile found.
    """
    # if users_root is None:
    #     users_root = try_parse(userconfig_path).getroot()

    # 1. Check <profileMappings> section
    for profile in auto_user_root.findall('.//profileMappings'):
        m = profile.findtext('model')
        s = profile.findtext('sim')
        c = profile.findtext('cls')

        if m == model and s == sim and c == cls:
            return profile.findtext('active_profile')

    # 2. Check <models> section for a profile tag
    for model_entry in auto_user_root.findall('.//models'):
        m = model_entry.findtext('model')
        s = model_entry.findtext('sim')

        if m == model and s == sim:
            p = model_entry.findtext('profile')
            if p:
                return p
    # 3. Nothing found in user profile, look for aircraft entry in defaults
    defaults_root = try_parse(defaults_path).getroot()
    for model_entry in defaults_root.findall('.//models'):
        m = model_entry.findtext('model')
        s = model_entry.findtext('sim')
        if m == model and s == sim:
            return "default"
    # 4. Fallback default
    logging.warning(f"No active or default profile found for model {model} in sim {sim}.")
    return None

def update_default_data_with_craft_result(defaultdata, craftresult):
    """
        Updates a base default configuration list with class-specific overrides.

        For each entry in `craftresult`, this method searches for a matching `name` in `defaultdata` and
        replaces the corresponding `value` and `unit` fields. It also adds a 'replaced' flag to indicate
        that the entry came from a class default override.

        Args:
            defaultdata (list of dict): The base default configuration data.
            craftresult (list of dict): The class-specific configuration overrides.

        Returns:
            list of dict: The updated configuration list, where applicable entries have been overridden
                          by class-specific values and marked with 'replaced': "Class Default".
        """
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
    """
        Merges settings from model-specific overrides into the default configuration list.

        This function updates each entry in `defaults_data` if a matching entry by 'name' is found
        in `model_data`. It copies the 'value' and 'unit' fields from `model_data` into the corresponding
        entry in `defaults_data`, and marks it with the provided `replacetext` in the 'replaced' field
        to indicate the source of the override.

        Args:
            defaults_data (list of dict): The base configuration settings.
            model_data (list of dict): The model-specific overrides to apply.
            replacetext (str): Text label to assign to the 'replaced' field when overrides are applied
                               (e.g., 'Model (user)', 'Sim (user)', 'Class (user)').

        Returns:
            list of dict: The updated configuration list with applied overrides.
        """
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


def add_new_model(sim, class_name, match_string):
    """A helper function to abstract the required parameters to create a new user model entry"""
    write_models_to_xml(sim, match_string, class_name, the_device='any', setting_name='type')

def add_new_profile(sim, class_name, match_string, profile_name):
    """A helper function to abstract the required parameters to create a new user model entry"""
    exists = auto_user_root.find(f'models[sim="{sim}"][model="{match_string}"][value="{class_name}"][name="profile"]')
    if not exists:
        write_models_to_xml(sim, match_string, class_name, the_device='any', setting_name='profile', profile_name=profile_name)

def write_models_to_xml(the_sim, the_model, the_value, setting_name, unit='', the_device='', profile_name=None):
    """
        Writes or updates a <models> entry in the user configuration XML.

        This function handles the creation or update of aircraft-specific settings in the user config file.
        It ensures proper device targeting, deduplication, and profile management (including redirecting
        writes from "default" to "Auto User" and establishing the active profile if necessary).

        Args:
            the_sim (str): Simulator name (e.g., "DCS", "MSFS").
            the_model (str): Aircraft model name (or match pattern).
            the_value (str): The value to assign to the setting.
            setting_name (str): The name of the setting to write.
            unit (str, optional): The unit for the setting (e.g., "kg", "rpm"). Defaults to empty string.
            the_device (str, optional): Device name. Defaults to global device fallback.
            profile_name (str, optional): Profile to write the setting under. If set to "default", the
                entry is redirected to "Auto User".

        Behavior:
            - Skips writing if model name is missing.
            - Uses `read_anydevice_settings` to decide whether the setting should target "any" device.
            - Redirects "default" profile writes to "Auto User", creating the entry if needed.
            - Updates existing entries if one is found; otherwise, writes a new <models> block.
            - Ensures no duplicates are created in the XML.
            - Persists changes using `consolidate_sort_and_write_userconfig`.

        Logging:
            - Logs both new additions and updates.
            - Prints a message if a duplicate entry is detected and skipped.
        """
    mprint(f"write_models_to_xml  {the_sim}, {the_model}, {the_value}, {setting_name}")
    dbprint("blue", f"[WRITE ENTRY] sim={the_sim}, model={the_model}, profile_name={profile_name}")

    # Use preloaded global XML tree and root
    tree = auto_user_tree
    root = auto_user_root

    if the_model == '':
        return  # Do not allow writes without a model name

    # Use global device fallback if none provided
    if the_device == '':
        the_device = device

    # check if the setting is an "any device" setting.  If so, change the device to 'any', otherwise will be for active device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'

    # Determine if profile was specified and be included in the write logic
    is_profile_used = profile_name is not None and profile_name.lower() != 'none'

    # If trying to write under "default", redirect to "Auto User"
    if is_profile_used and profile_name.lower() == 'default':
        # default is not a writable profile.. if a user modifies the config when default is loaded, write it to "Auto User' profile
        # first check if there is a "profile" entry for "Auto User" for this sim/model
        cls = get_class_for_sim_model(the_sim, the_model)

        # Ensure "Auto User" profile exists before writing
        auto_user_profile = root.find(
            f'.//models[sim="{the_sim}"][model="{the_model}"][name="profile"][profile="Auto User"]')
        if auto_user_profile is None:
            # There is no auto-user profile yet, so we need to create one
            add_new_profile(the_sim, cls, the_model, profile_name='Auto User')

        # Set active profile and override profile name for the write
        update_active_profile_entry(sim=the_sim, cls=cls, model=the_model, active_profile="Auto User")
        profile_name = "Auto User"

    # Build XPath query string to locate existing <models> entry
    xpath = f'.//models[sim="{the_sim}"][device="{the_device}"][model="{the_model}"][name="{setting_name}"]'
    if is_profile_used:
        xpath += f'[profile="{profile_name}"]'

    # Check if element already exists for this exact setting
    model_elem = root.find(xpath)

    if model_elem is not None:
        # Update existing <value> and <unit> fields if found
        for child_elem in model_elem:
            if child_elem.tag == 'value':
                child_elem.text = str(the_value)
            if child_elem.tag == 'unit':
                child_elem.text = str(unit)
        write_userconfig_xml(tree)
        logging.info(f"Updated <models> element with values: sim={the_sim}, device={the_device}, "
                     f"value={the_value}, unit={unit}, model={the_model}, name={setting_name}, profile={profile_name}")
        return

    # Deduplication: Avoid creating duplicate if exact entry already exists
    def same_model(e):
        tags = {
            "name": setting_name,
            "model": the_model,
            "value": the_value,
            "unit": unit,
            "sim": the_sim,
            "device": the_device,
        }
        if is_profile_used:
            tags["profile"] = profile_name
        return all(e.find(tag) is not None and e.find(tag).text == val for tag, val in tags.items())

    if any(same_model(e) for e in root.findall("models")):
        lprint("<models> element with the same values already exists. Skipping.")
        return

    # Create new <models> entry
    models = ET.SubElement(root, "models")
    tags_to_write = [("name", setting_name),
                     ("model", the_model),
                     ("value", the_value),
                     ("sim", the_sim),
                     ("device", the_device)]

    if unit:
        tags_to_write.append(("unit", unit))
    if is_profile_used:
        tags_to_write.append(("profile", profile_name))

    for tag, val in tags_to_write:
        ET.SubElement(models, tag).text = val

    # Finalize XML save
    consolidate_sort_and_write_userconfig(tree)
    logging.info(f"Added <models> element with values: sim={the_sim}, device={the_device}, "
                 f"value={the_value}, unit={unit}, model={the_model}, name={setting_name}, profile={profile_name}")

def old_write_models_to_xml(the_sim, the_model, the_value, setting_name, unit='', the_device='', profile_name=None):
    mprint(f"write_models_to_xml  {the_sim}, {the_model}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = auto_user_tree
    root = auto_user_root
    model_elem = None
    if the_model == '':
        return
    if the_device == '':
        the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'

    # profile_name = profile_name if profile_name else G.settings_mgr.active_profile
    if profile_name is None:
        profile_name = 'none'

    if profile_name.lower() == 'default':
        cls = get_class_for_sim_model(the_sim, the_model)
        # default is not a writable profile.. if a user modifies the config when default is loaded, write it to "Auto User' profile
        # first check if there is a "profile" entry for "Auto User" for this sim/model
        auto_user_profile = root.find(
            f'.//models[sim="{the_sim}"][model="{the_model}"][name="profile"][profile="Auto User"]')

        if auto_user_profile is None:
            # There is no auto-user profile yet, so we need to create one
            # we don't know the "class" here, so we need to get it from the defaults table
            # now add the profile entry
            add_new_profile(the_sim, cls, the_model, profile_name='Auto User')

        # set the active profile to "Auto User" so it will automatically load on the settings page
        update_active_profile_entry(sim=the_sim, cls=cls, model=the_model, active_profile="Auto User")
        # set the profile name to "Auto User" so the setting gets written to that profile
        profile_name = "Auto User"




    # Check if an identical <models> element already exists
    model_elem = root.find(f'.//models[sim="{the_sim}"]'  
                               f'[device="{the_device}"]'
                               f'[model="{the_model}"]'
                               f'[name="{setting_name}"]'
                               f'[profile="{profile_name}"]')


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
                     f"value={the_value}, unit={unit}, model={the_model}, name={setting_name}, profile={profile_name}")

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
                    ("device", the_device),
                    ("profile", profile_name)
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
                                   ("device", the_device),
                                   ("profile", profile_name)]:
                    ET.SubElement(models, tag).text = value
            else:
                for tag, value in [("name", setting_name),
                                   ("model", the_model),
                                   ("value", the_value),
                                   ("unit", unit),
                                   ("sim", the_sim),
                                   ("device", the_device),
                                   ("profile", profile_name)]:
                    ET.SubElement(models, tag).text = value

            # Write the modified XML back to the file
            tree = ET.ElementTree(root)
            consolidate_sort_and_write_userconfig(tree)
            logging.info(f"Added <models> element with values: sim={the_sim}, device={the_device}, "
                         f"value={the_value}, unit={unit}, model={the_model}, name={setting_name}, profile={profile_name}")


def write_class_to_xml(the_sim, the_class, the_value, setting_name, unit='', the_device = ''):
    """
        Writes or updates a <classSettings> entry in the user configuration XML.

        This function manages simulator and class-level (i.e., aircraft type) settings,
        ensuring values are appropriately scoped to the simulation environment and device.
        If a matching element already exists, its value is updated. Otherwise, a new entry is created.

        Args:
            the_sim (str): Simulator name (e.g., "DCS", "MSFS").
            the_class (str): Aircraft class or type (e.g., "JetAircraft", "Helicopter").
            the_value (str): The setting's value to be saved.
            setting_name (str): The name of the setting (e.g., "max_speed", "thrust_ratio").
            unit (str, optional): The unit for the value (e.g., "kts", "kg"). Defaults to ''.
            the_device (str, optional): Device name to scope the setting. Defaults to global `device`.

        Behavior:
            - If `the_device` is not provided, falls back to global `device`.
            - If the setting is in the "write_any_device_list", forces `device="any"`.
            - Searches for a matching <classSettings> block based on sim, device, type, and name.
                - If found, updates the <value> sub-element.
                - If not found, creates a new <classSettings> element.
            - Saves changes using `write_userconfig_xml`.

        Logging:
            - Logs updates and additions with the full context of the written data.
        """
    mprint(f"write_class_to_xml  {the_sim}, {the_class}, {the_value}{unit}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = auto_user_tree
    root = auto_user_root
    if the_device == '':
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


def write_sim_to_xml(the_sim, the_value, setting_name, unit='', the_device=''):
    """
       Writes or updates a <simSettings> entry in the user configuration XML file.

       This function handles writing simulator-wide settings for a specific device. If a matching
       setting already exists (based on sim, device, and name), it updates the <value> field.
       If no match is found, a new <simSettings> block is appended to the XML.

       Args:
           the_sim (str): The name of the simulator (e.g., "MSFS", "DCS").
           the_value (str): The value to be stored for the setting.
           setting_name (str): The name of the setting being written or updated.
           unit (str, optional): Unit of measurement (e.g., "kts", "lbs"). Defaults to ''.
           the_device (str, optional): The device associated with this setting. Defaults to global `device`.

       Behavior:
           - If `the_device` is empty, falls back to the globally active device.
           - If the setting is listed in `write_any_device_list`, sets device to 'any'.
           - Checks for an existing matching <simSettings> entry.
               - If found, updates the <value> child.
               - If not found, creates a new <simSettings> element with provided attributes.
           - Updates the XML on disk via `write_userconfig_xml`.

       Logging:
           - Logs both updates and new additions for full traceability.
       """
    mprint(f"write_sim_to_xml {the_sim}, {the_value}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = auto_user_tree
    root = auto_user_root
    if the_device == '':
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


def clone_profile_entry(sim, cls, src_model, src_profile, dst_profile):
    """
    Clone a profile entry for a src_model and src_profile into a dst_profile.
    This method clones *only* the profile entry and any profile specific settings.  To copy an entire model to another use 'clone_whole_model'
    """

    if src_profile == 'default':
        # we are cloning a default profile, so we need to create a placeholder entry in the userconfig.xml
        def_entry = auto_defaults_root.find(f'models[sim="{sim}"][value="{cls}"][model="{src_model}"][name="type"]')
        if def_entry is not None:
            cloned = ET.fromstring(ET.tostring(def_entry)) # Deep clone the element
            cloned.find('name').text  = "profile"
            ET.SubElement(cloned, 'profile').text = dst_profile
            auto_user_root.append(cloned)
            logging.info(f"Cloned profile entry for {sim} {cls} {src_model} from default to {dst_profile}")
        else:
            logging.error(f"No default entry found for {sim} {cls} {src_model} in defaults.xml while attempting to clone {src_profile} to {dst_profile}")
    else:
        # we are cloning a user profile or user created model, copy the entries
    # Search for an exact match in existing user profiles
        for entry in auto_user_root.findall("models"):
            if (entry.findtext("sim"), entry.findtext("model"), entry.findtext("profile")) == (sim, src_model, src_profile):
                cloned = ET.fromstring(ET.tostring(entry))  # Deep clone
                cloned.find("profile").text = dst_profile
                auto_user_root.append(cloned)

    consolidate_sort_and_write_userconfig(auto_user_tree)


def clone_whole_model(the_sim, old_pattern, new_pattern, old_profile, new_profile):
    """
        Clones all model configuration entries from an existing model profile and sim
        to a new model profile under a different pattern.

        This includes:
        - Entries from both default and user configuration XMLs.
        - Special handling of the `type` entry (which is written without profile).
        - Creation of a new `profile` entry referencing the `new_profile` name.
        - Cloning of special SC overrides (if any) for the model pattern.

        Args:
            the_sim (str): The simulator identifier (e.g., "MSFS", "DCS").
            old_pattern (str): The regex or model name pattern to clone from.
            new_pattern (str): The model pattern to clone to.
            old_profile (str): The existing profile name to clone from.
            new_profile (str): The new profile name to assign cloned settings to.

        Notes:
            - The method reads from both the defaults and userconfig XML paths using
              `read_models_data`.
            - For entries with `name == 'type'`, it creates an unprofiled entry, then rewrites
              it as a `profile` entry for tracking purposes.
            - `SC` override entries are also cloned using `write_sc_override_to_xml`.

        This method is typically used during profile creation or duplication workflows.
        """
    model_data, def_model_pattern = read_models_data(defaults_path, the_sim, old_pattern, alldevices=True)
    user_model_data, usr_model_pattern = read_models_data(userconfig_path, the_sim, old_pattern, alldevices=True, user=True, profile=old_profile)
    sc_overrides = read_sc_overrides(old_pattern)
    for item in user_model_data:
        model_data.append(item)
    for item in model_data:
        if item['name'] == 'profile':
            # we will be writing our own 'profile' value, so skip the existing models if it exists
            continue
        if item['unit'] is None:
            item['unit'] = ''

        if item['name'] == 'type':
            # we want to include a type entry, but it doesn't get a profile name
            write_models_to_xml(
                the_sim=the_sim,
                the_model=new_pattern,
                the_value=item['value'],
                setting_name=item['name'],
                unit=item['unit'],
                the_device=item['device']
            )
            # howerver, we want a name="profile" entry for the new model profile name, so we will change the value
            # of "name" from "type" to "profile" and let the loop write it
            item['name'] = 'profile'
        write_models_to_xml(
            the_sim=the_sim,
            the_model=new_pattern,
            the_value=item['value'],
            setting_name=item['name'],
            unit=item['unit'],
            the_device=item['device'],
            profile_name=new_profile
        )
    for item in sc_overrides:
        write_sc_override_to_xml(
            the_model=new_pattern,
            the_var=item['var'],
            setting_name=item['name'],
            sc_unit=item['sc_unit'],
            scale=item['scale']
        )


def erase_models_from_xml(the_sim, the_model, setting_name, the_device='', profile_name=None):
    mprint(f"erase_models_from_xml  {the_sim} {the_model}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = auto_user_tree
    root = auto_user_root
    if the_device == '':
        the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'
    profile_name = profile_name if profile_name else G.settings_mgr.active_profile

    if profile_name.lower() == 'default':
        # Profile name should never be 'default', but protect against issues and log a warning
        logging.warning(f"Attempted to erase a model from the default profile: {the_sim} {the_model} {setting_name}")
        return

    elements_to_remove = []
    for model_elem in root.findall(f'models[sim="{the_sim}"]'
                                   f'[device="{the_device}"]'                                   
                                   f'[model="{the_model}"]'
                                   f'[name="{setting_name}"]'
                                   f'[profile="{profile_name}"]'):

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



def erase_aircraft_profiles(sim, cls_name, model):
    tree = auto_user_tree
    root = auto_user_root
    for elem in root.findall(f'models[sim="{sim}"][model="{model}"]'):
        root.remove(elem)
    consolidate_sort_and_write_userconfig(tree)

def update_active_profile_entry(sim: str, cls: str, model: str, active_profile: str):
    # utils.debug_caller_args('green')
    """
    Update or create the <profileMappings> entry in the user configuration XML to reflect the active profile
    for a specific aircraft model within a given simulator and class.

    This method:
    - Looks for an existing <profileMappings> element matching the provided model, sim, and cls.
    - If found, it updates the <active_profile> tag with the new value.
    - If not found, it creates a new <profileMappings> entry with all required child elements.
    - Writes the modified XML tree back to disk using the shared XML utility method.

    Parameters:
        sim (str): The name of the simulator (e.g., "DCS", "MSFS").
        cls (str): The classification of the aircraft (e.g., "JetAircraft", "PropellerAircraft").
        model (str): The unique aircraft model identifier (e.g., "FA-18C_hornet").
        active_profile (str): The profile name to mark as active for this aircraft.

    Returns:
        None
    """
    # Use the up-to-date pre-parsed tree/root objects
    tree = auto_user_tree
    root = auto_user_root

    # First see if the incoming profile name is already set for the sim/cls/model
    match = root.find(f'profileMappings[model="{model}"][sim="{sim}"][cls="{cls}"][active_profile="{active_profile}"]')
    if match:
        #print("!!!!  MATCHES - NOT WRITING")
        return

    # Try to find an existing <profileMappings> entry matching all criteria
    entry = root.find(f'profileMappings[model="{model}"][sim="{sim}"][cls="{cls}"]')

    if entry is None:
        # Entry does not exist: create a new one with full structure
        entry = ET.SubElement(root, "profileMappings")
        ET.SubElement(entry, "model").text = model
        ET.SubElement(entry, "sim").text = sim
        ET.SubElement(entry, "cls").text = cls
        ET.SubElement(entry, "active_profile").text = active_profile
    else:
        # Entry exists: update the active_profile sub-element
        ap_elem = entry.find("active_profile")
        if ap_elem is None:
            ET.SubElement(entry, "active_profile").text = active_profile
        else:
            ap_elem.text = active_profile

    # Save the modified XML back to the file
    consolidate_sort_and_write_userconfig(tree)




def erase_model_profile(sim, model, profile):
    tree = auto_user_tree
    root = auto_user_root
    elements_to_remove = []
    for model_elem in root.findall(f'models[sim="{sim}"][model="{model}"][profile="{profile}"]'):
        if model_elem is not None:
            elements_to_remove.append(model_elem)

    if elements_to_remove:
        for elem in elements_to_remove:
            root.remove(elem)
        consolidate_sort_and_write_userconfig(tree)
        logging.info(f"Removed <models> element with values: sim={sim}, model={model}, profile={profile}")

def erase_entire_model_from_xml(the_sim, the_model):
    mprint(f"erase_entire_models_from_xml  {the_sim} {the_model}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = auto_user_tree
    root = auto_user_root
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


def erase_class_from_xml( the_sim, the_class,  setting_name, the_device=''):
    mprint(f"erase_class_from_xml  {the_sim} {the_class}, {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = auto_user_tree
    root = auto_user_root
    if the_device == '':
        the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'
    elements_to_remove = []
    for class_elem in root.findall(f'.//classSettings[sim="{the_sim}"]'
                                   f'[device="{the_device}"]'
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
                  f"type={the_class}, name={setting_name}")


def erase_sim_from_xml(the_sim, setting_name, the_device=''):
    mprint(f"erase_sim_from_xml  {the_sim} {setting_name}")
    # Load the existing XML file or create a new one if it doesn't exist
    tree = auto_user_tree
    root = auto_user_root
    if the_device == '':
        the_device = device
    write_any_device_list = read_anydevice_settings(the_sim)
    if setting_name in write_any_device_list:
        the_device = 'any'
    elements_to_remove = []
    for sim_elem in root.findall(f'.//simSettings[sim="{the_sim}"]'
                                   f'[device="{the_device}"]'
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
        logging.info(f"Removed <simSettings> element with values: sim={the_sim}, device={the_device}, name={setting_name}")


def sort_elements(tree):    #  unused for now.
    # Parse the XML file

    root = auto_user_root

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
    # tree = try_parse(defaults_path)
    # root = auto_user_root

    # Collect data in a list of dictionaries
    data_list = []
    for defaults_elem in auto_user_root.findall(f'.//defaults'):
        if defaults_elem.find('name') is None and defaults_elem.find('order') is None and defaults_elem.find('datatype') is None:
            # Ignore empty rows that may exist for readability purposes
            continue
        # name is unused here?
        # name_elem = defaults_elem.find('name').text
        # name = (f"{name_elem.text}") if name_elem is not None else ""
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
    for child_item in datalist:
        add_item = True
        if child_item['prereq'] != '':
            add_item = False
            for parent_item in datalist:
                if parent_item['name'] in child_item['prereq'] and (parent_item['value'].lower() == 'true' or '.0' in parent_item['order']):
                    if parent_item['name'] == child_item['prereq'] or '.' in child_item['prereq']:
                        add_item = True
                        break

        if add_item:
            newlist.append(child_item)

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
            if item['replaced'] == "Model (profile)": replacestring = "UP"
        spacing = 50 - (len(item['name']) + len(item['value']) + len(item['unit']))
        space = " " * spacing + " # " + replacestring + " # "

        lprint(f"{tabstring}{item['name']} = {item['value']} {item['unit']} {space} {item['info']}")


## unused?
def get_craft_attributes(file_path, sim, device):
    mprint(f"get_craft_attributes {file_path}, {sim}, {device}")
    craft_attributes = set()
    craft_attributes.add('Aircraft')

    tree = try_parse(file_path)
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