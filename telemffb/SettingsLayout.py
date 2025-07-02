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
import json
import logging
import os
import re

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QIcon, QFont, QColor, QPixmap
from PyQt6.QtWidgets import (QGridLayout, QLabel, QPushButton, QStyle, QMessageBox,
                             QToolButton, QCheckBox, QComboBox, QLineEdit, QFileDialog, QSpinBox, QHBoxLayout)

from telemffb.ButtonPressThread import ButtonPressThread
from telemffb.custom_widgets import (InfoLabel, NoWheelSlider, NoWheelNumberSlider, vpf_purple, t_purple, Toggle, EraseButton, CenteredClickableComboBox)
from telemffb.ConfiguratorDialog import ConfiguratorDialog
from telemffb.AdvancedSpringDialog import AdvancedSpringDialog
from telemffb.AdvancedGDialog import AdvancedGDialog
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.utils import validate_vpconf_profile, dbprint, HiDpiPixmap
import telemffb.utils as utils
import styles
from . import globals as G
from . import xmlutils

class SettingsLayout(QGridLayout):
    expanded_items = []
    prereq_list = []
    ##########
    # debug settings
    show_col_debug = False      # shows column, span, indent in label tooltip
    show_slider_debug = False   # set to true for slider values shown
    show_order_debug = False    # set to true for order numbers shown
    show_settings_names = False # show setting internal name instead of displayname
    bump_up = True              # set to false for no row bumping up

    all_sliders = []
    incrvalue = 1
    def __init__(self, parent=None, mainwindow=None):
        super(SettingsLayout, self).__init__(parent)
        self.exclusive_list = []
        self.parent_expander_dict = {}
        result = None
        if G.settings_mgr.current_sim != 'nothing':
            a, b, result = xmlutils.read_single_model(G.settings_mgr.current_sim, G.settings_mgr.current_aircraft_name)

        self.mainwindow = mainwindow
        if result is not None:
            self.build_rows(result)
        self.device = HapticEffect()
        self.setColumnMinimumWidth(7, 20)
        self.trigger_form_reload = True
        self.advanced_spring_settings = None
        self.adv_spr_dialog = None
        self.advanced_g_settings = None
        self.adv_g_dialog = None

    def handleScrollKeyPressEvent(self, event):
        # Forward key events to each slider in the layout
        for i in range(self.count()):
            item = self.itemAt(i)
            if isinstance(item.widget(), NoWheelSlider()):
                item.widget().handleKeyPressEvent(event)

    def append_prereq_count(self, datalist):
        for item in datalist:
            item['prereq_count'] = ''
            item['has_expander'] = ''
            for pr in self.prereq_list:
                if item['name'] == pr['prereq']:
                    item['prereq_count'] = pr['count']
            p_count = 0
            if item['prereq_count'] != '':
                p_count = int(item['prereq_count'])

            if p_count > 1 or (p_count == 1 and item['hasbump'] != 'true'):
                item['has_expander'] = 'true'
            if '.0' in item['order']:
                item['has_expander'] = 'false'

    def has_bump(self, datalist):
        for item in datalist:
            item['hasbump'] = ''
            bumped_up = item['order'][-1:] == '1' and '.' in item['order']
            if bumped_up:
                for b in datalist:
                    if item['prereq'] == b['name']:
                        b['hasbump'] = 'true'

    def get_parent_indent(self, datalist):
        name_map = {item['name']: item for item in datalist}

        for item in datalist:
            indent = 0
            parent_name = item.get('prereq')

            # Check if order meets the ".endswith('1')" and contains '.' condition
            order = item.get('order', '')
            skip_increment = (
                    isinstance(order, str) and
                    '.' in order and
                    order.strip().endswith('1')
            )

            while parent_name in name_map:
                parent = name_map[parent_name]

                # If parent is a group, force indent = 0 and stop
                if parent.get('datatype') == 'group':
                    break

                if not skip_increment:
                    indent += 1
                skip_increment = False  # Only skip increment for the first level

                parent_name = parent.get('prereq')

            item['indent'] = indent

    def add_expanded(self, datalist):
        for item in datalist:
            item['parent_expanded'] = ''
            for exp in self.expanded_items:
                if item['prereq'] == exp:
                    item['parent_expanded'] = 'true'
                else:
                    item['parent_expanded'] = 'false'

    def is_top_level_expanded(self, item, datalist):
        """
        Recursively check if the top-level parent of `item` is in self.expanded_items.
        """
        prereq = item.get('prereq', '')
        if not prereq:
            # Reached the top-level item
            if '.0' in item['order']:
                return True
            else:
                return item['name'] in self.expanded_items

        # Find the parent item by its name
        parent_item = next((x for x in datalist if x['name'] in prereq), None)

        if parent_item is None:
            # Broken link; treat as not expanded
            return False

        # Recurse upward
        return self.is_top_level_expanded(parent_item, datalist)

    def is_visible(self, datalist):
        for item in datalist:
            bumped_up = item['order'][-1:] == '1' and '.' in item['order']
            iv = 'false'
            cond = ''

            if item['prereq'] == '':
                iv = 'true'
                cond = 'no prereq needed'
                pcount = 0
                if item['datatype'] == 'group':
                    for prereqs in datalist:
                        if item['name'] in prereqs['prereq']:
                            pcount += 1
                    if pcount == 0:
                        iv = 'false'
            else:
                if not self.is_top_level_expanded(item, datalist):
                    iv = 'false'
                else:
                    for p in datalist:
                        if p['name'] in item['prereq']:
                            #  check if the parent's value is part of the prereq string
                            if p['value'] in item['prereq'] or p['value'].lower() == 'true':
                                if p.get('has_expander', 'false').lower() == 'true':
                                    if p['name'] in self.expanded_items and p.get('is_visible', 'false') == 'true':
                                        iv = 'true'
                                        cond = 'item parent expanded'
                                    elif p.get('hasbump', 'false').lower() == 'true' and bumped_up:
                                        iv = 'true'
                                        cond = 'parent hasbump & bumped'
                                elif p.get('is_visible', 'false').lower() == 'true':
                                    if p.get('hasbump', 'false').lower() == 'true' and bumped_up:
                                        iv = 'true'
                                        cond = 'parent hasbump & bumped no expander par vis'
                                    if '.0' in p['order']:
                                        iv = 'true'
                            break
            if item['datatype'] == 'convert':
                iv = 'false'
            item['is_visible'] = iv

            # for things not showing debugging:
            # if iv.lower() == 'true':
            #     print (f"{item['displayname']} visible because {cond}")

    def eliminate_invisible(self, datalist):
        newlist = []
        for item in datalist:
            if item['is_visible'] == 'true':
                newlist.append(item)

        for item in newlist:
            item['prereq_count'] = '0'
            pcount = 0
            for row in newlist:
                if row['prereq'] == item['name']:
                    pcount += 1
                    if row['has_expander'].lower() == 'true':
                        pcount -= 1
            item['prereq_count'] = str(pcount)

        return newlist

    def read_active_prereqs(self, datalist):
        p_list = []
        for item in datalist:
            found = False
            count = 0
            itemname = item.get('name','')
            for p in datalist:
                prereq = p.get('prereq', '')
                if itemname in prereq:
                    if ('.' in prereq or itemname == prereq):
                        count += 1
                        found = True
                # If 'prereq' is not in the list, add a new entry
            if found:
                p_list.append({'prereq': item['name'], 'value': item['value'], 'count': count})
        return p_list

    def set_mode(self, mode, setting, datalist):
        #save the new mode to file
        for item in datalist:
            if item['name'] == setting:
                item['value'] = mode
                xmlutils.write_to_xml(G.settings_mgr.current_sim,
                              G.settings_mgr.current_class,
                              G.settings_mgr.current_pattern,
                              item['name'],
                              item['value'])

    def convert_to_springmode(self, datalist):
        # read old value from xml, remap & erase old setting
        # List of (name, new_mode)
        mode_map = {
            "adv_spr_override_enabled": "ADVANCED",
            "aircraft_is_spring_centered": "CENTER",
            "aircraft_is_fbw": "FBW",
            "override_spring_enabled": "CUSTOM"
        }
        for item in datalist:
            for name, mode in mode_map.items():
                if item['name'] == name and item['value'] == 'true':
                    xmlutils.erase_from_xml(
                        G.settings_mgr.current_sim,
                        G.settings_mgr.current_class,
                        G.settings_mgr.current_pattern,
                        name
                    )
                    self.set_mode(mode, "spring_mode", datalist)

    def convert_pedal_to_springmode(self, datalist):
        mode_map = {
            "Dynamic Spring": "DYNAMIC",
            "Static Spring": "STATIC",
            "No Spring": "NOSPRING",
            "Sim Default": "NONE",
        }

        for item in datalist:
            if item['name'] == 'pedal_spring_mode':
                spring_mode = mode_map.get(item['value'])
                if spring_mode:
                    xmlutils.erase_from_xml(
                        G.settings_mgr.current_sim,
                        G.settings_mgr.current_class,
                        G.settings_mgr.current_pattern,
                        item['name']
                    )
                    self.set_mode(spring_mode, 'spring_mode', datalist)

    def set_gforce_effect(self, mode, datalist):
        for item in datalist:
            if item['name'] == 'gforce_effect':
                item['value'] = mode
        xmlutils.write_to_xml(G.settings_mgr.current_sim,
                              G.settings_mgr.current_class,
                              G.settings_mgr.current_pattern,
                              item['name'],
                              item['value'])

    def convert_to_gforcemode(self, datalist):
        mode_map = {
            'gforce_effect_enable': 'LEGACY',
            'new_gforce_effect_enable': 'NEW',
        }

        for item in datalist:
            for name, mode in mode_map.items():
                if item['name'] == name and item['value'] == 'true':
                    xmlutils.erase_from_xml(
                        G.settings_mgr.current_sim,
                        G.settings_mgr.current_class,
                        G.settings_mgr.current_pattern,
                        name
                    )
                    self.set_mode(mode, 'gforce_effect', datalist)

    def build_rows(self, datalist):
        sorted_data = sorted(datalist, key=lambda x: float(x['order']))
        # self.prereq_list = xmlutils.read_prereqs()
        self.convert_to_springmode(sorted_data)
        self.convert_pedal_to_springmode(sorted_data)
        self.convert_to_gforcemode(sorted_data)
        self.prereq_list = self.read_active_prereqs(sorted_data)
        self.has_bump(sorted_data)
        self.append_prereq_count(sorted_data)
        self.add_expanded(sorted_data)
        self.is_visible(sorted_data)
        self.get_parent_indent(sorted_data)
        newlist = self.eliminate_invisible(sorted_data)

        def is_expanded(item):
            if item['name'] in self.expanded_items:
                return True
            for row in sorted_data:
                if row['name'] == item['prereq'] and is_expanded(row):
                    return True
            return False

        i = 0
        for item in newlist:
            bumped_up = item['order'][-1:] == '1' and '.' in item['order']
            rowdisabled = False
            addrow = False
            is_expnd = is_expanded(item)
            # print(f"{item['order']} - {item['value']} - b {bumped_up} - hb {item['hasbump']} - ex {is_expnd} - hs {item['has_expander']} - pex {item['parent_expanded']} - iv {item['is_visible']} - pcount {item['prereq_count']} - {item['displayname']} - pr {item['prereq']}")
            if item['is_visible'].lower() == 'true':
                i += 1
                if bumped_up:
                    if self.bump_up:  # debug
                        i -= 1   # bump .1 setting onto the enable row
                self.generate_settings_row(item, i, rowdisabled)

        spacerItem = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding)
        self.addItem(spacerItem, i+1, 1, 1, 1)
        # Give entry column a high stretch factor, all others remain default 0.
        # When window is resized, the entry column will grow to take up all the new space
        self.setColumnStretch(5, 10)

        # print (f"{i} rows with {self.count()} widgets")

    def reload_caller(self):
        # caller_frame = inspect.currentframe().f_back
        # caller_name = caller_frame.f_code.co_name
        # dbprint("blue", f"RELOAD_CALLER was called by {caller_name}")
        if not self.trigger_form_reload:
            self.trigger_form_reload = True  # reset back to true for next iteration
        else:
            self.reload_layout(None)

    def reload_layout(self, result=None):
        # stack = inspect.stack()
        # for frame_info in stack:
        #     dbprint("green", f"Function {frame_info.function} in {frame_info.filename} at line {frame_info.lineno}")
        self.clear_layout()
        if result is None:
            cls, pat, result = xmlutils.read_single_model(G.settings_mgr.current_sim, G.settings_mgr.current_aircraft_name, G.settings_mgr.current_class)
            G.settings_mgr.current_pattern = pat
        if result is not None:
            self.build_rows(result)

    def clear_layout(self):
        layout = self.layout()
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
                else:
                    sub_layout = item.layout()
                    if sub_layout:
                        self._clear_sub_layout(sub_layout)
                        sub_layout.deleteLater()

    def _clear_sub_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            else:
                sub_layout = item.layout()
                if sub_layout:
                    self._clear_sub_layout(sub_layout)
                    sub_layout.deleteLater()

    def do_move_to_class(self, csim, cclass, value, setting, model, unit):

        reply = QMessageBox.question(
            None,
            "Confirmation",
            f"Are you sure you want to move {setting} ({value}) from {model} to all {csim} {cclass} class aircraft?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No  # Default button
        )

        if reply == QMessageBox.StandardButton.Yes:
            print(f"Moving {setting} setting ({value}) from {model} to all {csim} {cclass} class aircraft")
            xmlutils.write_class_to_xml(csim, cclass, value, setting, unit)
            xmlutils.erase_models_from_xml(csim, model, setting)

    def do_move_to_sim(self, csim, value, setting, model, unit):

        reply = QMessageBox.question(
            None,
            "Confirmation",
            f"Are you sure you want to move {setting} ({value}) from {model} to ALL {csim} aircraft?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No  # Default button
        )
        if reply == QMessageBox.StandardButton.Yes:
            print(f"Moving {setting} setting ({value}) from {model} to {csim} Sim for all aircraft")
            xmlutils.write_sim_to_xml(csim,value,setting, unit)
            xmlutils.erase_models_from_xml(csim,model,setting)

    def generate_settings_row(self, item, i,  rowdisabled=False ):
        self.setRowMinimumHeight(i, 25)
        entry_colspan = 2
        lbl_colspan = 3

        exp_col = 0
        chk_col = 1
        lbl_col = 2
        entry_col = 5
        unit_col = entry_col + 1
        val_col = unit_col + 1
        erase_col = val_col + 1
        fct_col = 10
        ord_col = 11

        validvalues = item['validvalues'].split(',')

        if self.show_order_debug:
            order_lbl = QLabel()
            order_lbl.setText(item['order'])
            order_lbl.setMaximumWidth(45)
            self.addWidget(order_lbl, i, ord_col)

        # right click erase button to move the setting up in hierarchy
        show_move_menu = True if item['replaced'] == 'Model (user)' else False
        erase_button = EraseButton(csim=G.settings_mgr.current_sim,
                                   cclass=G.settings_mgr.current_class,
                                   cmodel=G.settings_mgr.current_pattern,
                                   csetting=item['name'],
                                   cvalue=item['value'],
                                   cunit=item['unit'],
                                   enable_context_menu=show_move_menu) # Create erase button at beginning so behavior can be modified per widget type if necessary
        if show_move_menu:
            erase_button.move_to_class_signal.connect(self.do_move_to_class)
            erase_button.move_to_sim_signal.connect(self.do_move_to_sim)

        # everything has a name, except for things that have a checkbox *and* slider
        # some labels show Move right click menu

        label = InfoLabel(text=f"{item['name']}") if self.show_settings_names else (
            InfoLabel(text=f"{item['displayname']}"))

        label.setObjectName(f'namelabel_{item["name"]}')
        label.setToolTip(item['info'])
        label.setMinimumHeight(20)
        label.setMinimumWidth(20)

        #determine indentation

        if item['datatype'] == 'group':
            lbl_col = 0
            item['indent'] = -1
        else:
            exp_col = item['indent']
            chk_col = exp_col + 1
            lbl_col = chk_col + 1
        lbl_colspan = entry_col - lbl_col


        # booleans get a checkbox
        if item['datatype'] == 'bool':
            # print(f"Exclusive: >{item['exclusive_with']}<")
            if item['exclusive_with'] != '':
                pair = [item['name'],item['exclusive_with']]
                if not pair in self.exclusive_list:
                    self.exclusive_list.append(pair)
            # print(item)
            checkbox = Toggle(
                checked_color=vpf_purple,
                bar_color=t_purple
            )

            # checkbox = AnimatedToggle(
            #     checked_color="#ab37c8",
            #     pulse_checked_color="#44ab37c8"
            # )
            checkbox.setMaximumSize(QtCore.QSize(45, 30))
            checkbox.setMinimumSize(QtCore.QSize(45, 30))
            checkbox.setObjectName(f"cb_{item['name']}")
            # checkbox.blockSignals(True)
            if item['value'].lower() == 'false':
                checkbox.setChecked(0)
                rowdisabled = True
            else:
                checkbox.setChecked(2)
            checkbox.blockSignals(False)

            if self.show_col_debug:
                checkbox.setToolTip(f"{chk_col}")
            self.addWidget(checkbox, i, chk_col)
            checkbox.stateChanged.connect(lambda state, name=item['name']: self.checkbox_changed(name, state))

        if item['unit'] is not None and item['unit'] != '':
            entry_colspan = 1
            unit_dropbox = QComboBox()
            unit_dropbox.blockSignals(True)
            if item['unit'] == 'hz':
                unit_dropbox.addItem('hz')
                unit_dropbox.setCurrentText('hz')
            elif item['unit'] == 'deg':
                unit_dropbox.addItem('deg')
                unit_dropbox.setCurrentText('deg')
            else:
                unit_dropbox.addItems(validvalues)
                unit_dropbox.setCurrentText(item['unit'])
            unit_dropbox.setObjectName(f"ud_{item['name']}")
            unit_dropbox.currentIndexChanged.connect(self.unit_dropbox_changed)
            self.addWidget(unit_dropbox, i, unit_col)
            unit_dropbox.blockSignals(False)
            unit_dropbox.setDisabled(rowdisabled)


        if item['order'][-1:] == '1' and '.' in item['order']:
            olditem = self.itemAtPosition(i, lbl_col)
            if olditem is not None:
                self.remove_widget(olditem)

        cdb = f"{lbl_col} {lbl_colspan} ind:{item['indent']} " if self.show_col_debug else ''
        label.setToolTip(f"{cdb}{item['info']}")
        self.addWidget(label, i, lbl_col, 1, lbl_colspan)

        slider = NoWheelSlider()
        slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        slider.setObjectName(f"sld_{item['name']}")

        n_slider = NoWheelNumberSlider()
        n_slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        n_slider.setObjectName(f"sld_{item['name']}")

        d_slider = NoWheelSlider()
        d_slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        d_slider.setObjectName(f"dsld_{item['name']}")

        df_slider = NoWheelSlider()
        df_slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        df_slider.setObjectName(f"dfsld_{item['name']}")

        m_butt = QPushButton("-")
        m_butt.setProperty('buttonType', 'p_m_button')
        m_butt.setFixedSize(20, 20)

        # Create the "+" button
        p_butt = QPushButton("+")
        p_butt.setProperty('buttonType', 'p_m_button')
        p_butt.setFixedSize(20, 20)

        line_edit = QLineEdit()
        line_edit.blockSignals(True)
        # unit?
        line_edit.setText(item['value'])
        line_edit.blockSignals(False)
        line_edit.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        line_edit.setObjectName(f"vle_{item['name']}")
        line_edit.setMinimumWidth(150)
        line_edit.editingFinished.connect(self.line_edit_changed)

        expand_button = QToolButton()
        expand_button.setProperty('buttonType', 'expand_button')
        if item['name'] in self.expanded_items:
            expand_button.setArrowType(Qt.ArrowType.DownArrow)
        else:
            expand_button.setArrowType(Qt.ArrowType.RightArrow)
        expand_button.setMaximumWidth(24)
        expand_button.setMinimumWidth(24)
        expand_button.setObjectName(f"ex_{item['name']}")
        expand_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        expand_button.clicked.connect(self.expander_clicked)

        usb_button_text = f"Button {item['value']}"
        if item['value'] == '0':
            usb_button_text = 'Click to Configure'
        self.usbdevice_button = QPushButton(usb_button_text)
        self.usbdevice_button.setMinimumWidth(150)
        self.usbdevice_button.setObjectName(f"pb_{item['name']}")
        self.usbdevice_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.usbdevice_button.clicked.connect(self.usb_button_clicked)

        value_label = QLabel()
        value_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        value_label.setMaximumWidth(50)
        value_label.setObjectName(f"vl_{item['name']}")
        sliderfactor = QLabel(f"{item['sliderfactor']}")
        if self.show_slider_debug:
            sliderfactor.setMaximumWidth(20)
        else:
            sliderfactor.setMaximumWidth(0)
        sliderfactor.setObjectName(f"sf_{item['name']}")

        if item['datatype'] == 'float' or item['datatype'] == 'negfloat':

            # print(f"label {value_label.objectName()} for slider {slider.objectName()}")
            factor = float(item['sliderfactor'])
            if '%' in item['value']:
                floatval = float(item['value'].replace('%', ''))
                val = floatval / 100
            else:
                val = float(item['value'])

            pctval = int((val / factor) * 100)
            if self.show_slider_debug:
                logging.debug(f"read value: {item['value']}  factor: {item['sliderfactor']} slider: {pctval}")
            slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                slider.setRange(int(validvalues[0]), int(validvalues[1]))
            slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')

            slider.valueChanged.connect(lambda value: self.slider_changed(write=False))  # update label on every change
            slider.delayedValueChanged.connect(lambda value: self.slider_changed(write=True))  # only write config when delay timer triggers
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(slider.decrease_single_step)
            p_butt.clicked.connect(slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col, alignment=Qt.AlignmentFlag.AlignVCenter)
            self.addWidget(sliderfactor, i, fct_col)

            slider.blockSignals(False)

        if item['datatype'] == 'n_float' :

            # print(f"label {value_label.objectName()} for slider {slider.objectName()}")
            factor = float(item['sliderfactor'])
            if '%' in item['value']:
                floatval = float(item['value'].replace('%', ''))
                val = floatval / 100
            else:
                val = float(item['value'])

            pctval = int((val / factor) * 100)
            if self.show_slider_debug:
                logging.debug(f"read value: {item['value']}  factor: {item['sliderfactor']} slider: {pctval}")
            n_slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                n_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            n_slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')
            # value_label.setToolTip(f"Actual Value: %{int(val * 100)}")
            n_slider.valueChanged.connect(lambda value: self.slider_changed(write=False))  # update label on every change
            n_slider.delayedValueChanged.connect(lambda value: self.slider_changed(write=True)) # only write config when delay timer triggers
            # n_slider.sliderPressed.connect(self.sldDisconnect)
            # n_slider.sliderReleased.connect(self.sldReconnect)
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(n_slider.decrease_single_step)
            p_butt.clicked.connect(n_slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(n_slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col, alignment=Qt.AlignmentFlag.AlignVCenter)
            self.addWidget(sliderfactor, i, fct_col)

            n_slider.blockSignals(False)

        if item['datatype'] == 'd_float':

            d_val = float(item['value'])
            factor = float(item['sliderfactor'])
            val = float(round(d_val / factor))
            if self.show_slider_debug:
                print(f"read value: {item['value']}   df_slider: {val}")
            df_slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                df_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            df_slider.setValue(round(val))
            value_label.setText(str(d_val))
            df_slider.valueChanged.connect(lambda value: self.df_slider_changed(write=False))  # update label on every change
            df_slider.delayedValueChanged.connect(lambda value: self.df_slider_changed(write=True)) # only write config when delay timer triggers
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(df_slider.decrease_single_step)
            p_butt.clicked.connect(df_slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(df_slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)
            df_slider.blockSignals(False)

        if item['datatype'] == 'cfgfloat':

            # print(f"label {value_label.objectName()} for slider {slider.objectName()}")

            if '%' in item['value']:
                pctval = int(item['value'].replace('%', ''))
            else:
                pctval = int(float(item['value']) * 100)
            pctval = int(round(pctval))
            # print (f"configurator value: {item['value']}   slider: {pctval}")
            slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                slider.setRange(int(validvalues[0]), int(validvalues[1]))
            slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')
            slider.valueChanged.connect(lambda value: self.cfg_slider_changed(write=False))  # update label on every change
            slider.delayedValueChanged.connect(lambda value: self.cfg_slider_changed(write=True)) # only write config when delay timer triggers
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(slider.decrease_single_step)
            p_butt.clicked.connect(slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)

            slider.blockSignals(False)

        if item['datatype'] == 'd_int':

            d_val = int(item['value'])
            factor = float(item['sliderfactor'])
            val = int(round(d_val / factor))
            if self.show_slider_debug:
                print(f"read value: {item['value']}   d_slider: {val}")
            d_slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                d_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            d_slider.setValue(val)
            value_label.setText(str(d_val))
            d_slider.valueChanged.connect(lambda value: self.d_slider_changed(write=False))  # update label on every change
            d_slider.delayedValueChanged.connect(lambda value: self.d_slider_changed(write=True)) # only write config when delay timer triggers
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(d_slider.decrease_single_step)
            p_butt.clicked.connect(d_slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(d_slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)

            d_slider.blockSignals(False)

        if item['datatype'] == 'text':
            textbox = QLineEdit()
            textbox.setObjectName(f"le_{item['name']}")
            textbox.setText(item['value'])
            textbox.editingFinished.connect(self.textbox_changed)
            self.addWidget(textbox, i, entry_col, 1, entry_colspan)

        if item['datatype'] == 'spin_int':
            spin_box = QSpinBox()
            spin_box.blockSignals(True)
            spin_box.setValue(int(item['value']))
            spin_box.blockSignals(False)
            spin_box.setMinimumWidth(80)
            spin_box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            spin_box.setObjectName(f"sb_{item['name']}")
            if validvalues is None or validvalues == '':
                pass
            else:
                spin_box.setMinimum(int(validvalues[0]))
                spin_box.setMaximum(int(validvalues[1]))
            spin_box.valueChanged.connect(self.spin_box_changed)
            self.addWidget(spin_box, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] in ('list', 'anylist', 'enumlist'):
            dropbox = QComboBox()
            dropbox.setStyleSheet("""
                QComboBox {
                    qproperty-alignment: 'AlignCenter';
                }
            """)

            if item['datatype'] == 'anylist':
                dropbox.setEditable(True)
                dropbox.lineEdit().setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            # else:
            #     dropbox = CenteredClickableComboBox()
            dropbox.setMinimumWidth(150)
            dropbox.blockSignals(True)

            if item['datatype'] == 'enumlist':
                dropbox.setObjectName(f"edb_{item['name']}")

                label_dict_name = item['validvalues']
                label_dict = getattr(utils, label_dict_name, None)

                if not isinstance(label_dict, dict):
                    dropbox.addItem(f"Label dict '{label_dict_name}' not found or invalid")
                else:
                    try:
                        enum_class = type(next(iter(label_dict)))
                    except StopIteration:
                        dropbox.addItem("Label dictionary is empty")
                        enum_class = None

                    for enum_member, the_label in label_dict.items():
                        dropbox.addItem(the_label, enum_member)

                    if enum_class and item['value']:
                        try:
                            enum_member = enum_class[item['value']]
                            for index in range(dropbox.count()):
                                if dropbox.itemData(index) == enum_member:
                                    dropbox.setCurrentIndex(index)
                                    break
                        except (KeyError, ValueError):
                            for index in range(dropbox.count()):
                                if dropbox.itemText(index) == item['value']:
                                    dropbox.setCurrentIndex(index)
                                    enum_member = dropbox.itemData(index)
                                    if enum_member:
                                        item['value'] = enum_member.name
                                    break
                            else:
                                dropbox.addItem(f"Invalid enum value: {item['value']}")


            else:
                dropbox.setObjectName(f"db_{item['name']}")
                dropbox.addItems(validvalues)
                dropbox.setCurrentText(item['value'])

            if item['name'] == 'type' and 'Default' in item['replaced']:
                # block editing of type for default craft
                # dropbox.lineEdit().removeEventFilter(dropbox) # Disable expand of items when clicking on lineEdit area (req'd for custom widget type)
                dropbox.setDisabled(True)
            else:
                if item['datatype'] == 'list':
                    # dropbox.lineEdit().setReadOnly(True)
                    dropbox.editTextChanged.connect(self.dropbox_changed)
                else:
                    dropbox.currentTextChanged.connect(self.dropbox_changed)
                dropbox.blockSignals(False)
            self.addWidget(dropbox, i, entry_col, 1, entry_colspan)
            # dropbox.currentTextChanged.connect(self.dropbox_changed)

        if item['datatype'] == 'path':
            self.vpconf_browse_button = QPushButton()
            self.vpconf_browse_button.blockSignals(True)
            self.vpconf_browse_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self.vpconf_browse_button.setMinimumWidth(150)
            self.vpconf_browse_button.setObjectName('path_vpconf')
            if item['value'] == '-':
                self.vpconf_browse_button.setText('Browse...')
            else:
                fname = os.path.basename(item['value'])
                button_text = fname
                self.vpconf_browse_button.setToolTip(item['value'])
                # p_length = len(item['value'])
                # if p_length > 45:
                #     button_text = f"{button_text[:40]}...{button_text[-25:]}"
                self.vpconf_browse_button.setText(button_text)
            self.vpconf_browse_button.blockSignals(False)
            self.vpconf_browse_button.setMaximumHeight(25)
            self.vpconf_browse_button.clicked.connect(self.browse_for_config)
            self.addWidget(self.vpconf_browse_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'int' or item['datatype'] == 'anyfloat':
            self.addWidget(line_edit, i, entry_col, 1, entry_colspan)

        if item['datatype'] == 'button':
            self.addWidget(self.usbdevice_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'advgs':
            self.advanced_g_settings = item['value']
            b_txt = 'Configure Settings' if item['value'] == "none" else "Edit Settings"
            # print(f"ADVANCED SPRING {self.advanced_spring_settings}")
            self.adv_g_button = QPushButton(b_txt)
            self.adv_g_button.setMinimumWidth(150)
            self.adv_g_button.setMinimumHeight(25)
            self.adv_g_button.setObjectName(f"advspr_{item['name']}")
            self.adv_g_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self.adv_g_button.clicked.connect(lambda: self.advanced_g_button_clicked(self.advanced_g_settings))
            self.addWidget(self.adv_g_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'advspr':
            self.advanced_spring_settings = item['value']
            b_txt = 'Configure Settings' if item['value'] == "none" else "Edit Settings"
            # print(f"ADVANCED SPRING {self.advanced_spring_settings}")
            self.adv_spr_button = QPushButton(b_txt)
            self.adv_spr_button.setMinimumWidth(150)
            self.adv_spr_button.setMinimumHeight(25)
            self.adv_spr_button.setObjectName(f"advspr_{item['name']}")
            self.adv_spr_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self.adv_spr_button.clicked.connect(lambda: self.advanced_spring_button_clicked(self.advanced_spring_settings))
            self.addWidget(self.adv_spr_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'configurator':
            self.configurator_button = QPushButton("Configure Gain Overrides")

            try:
                gaindict = json.loads(item['value'])
                self.configurator_button = QPushButton("Edit Gain Overrides")
            except:
                pass

            self.configurator_button.setMinimumWidth(150)
            self.configurator_button.setMaximumHeight(25)
            self.configurator_button.setObjectName(f"config_{item['name']}")
            self.configurator_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self.configurator_button.clicked.connect(self.configurator_button_clicked)
            erase_button.clicked.connect(self.erase_configurator_overrides)
            self.addWidget(self.configurator_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        # grouping collapsible header
        if item['datatype'] == 'group':

            font_family = "Black Ops One"
            font_size = 14
            label.text_label.setFont(QFont(font_family, font_size))

            expand_button.setVisible(False)

        if not rowdisabled:
            # for p_item in self.prereq_list:
            #     if p_item['prereq'] == item['name'] : # and p_item['count'] > 1:
            p_count = 0
            if item['prereq_count'] != '':
                p_count = int(item['prereq_count'])
            if self.show_col_debug:
                expand_button.setToolTip(f"{exp_col}")
            if item['has_expander'].lower() == 'true':
                if item['name'] in self.expanded_items:
                    row_count = p_count
                    if item['hasbump'].lower() != 'true':
                        row_count += 1
                    expand_button.setMaximumHeight(200)
                    # self.addWidget(expand_button, i, exp_col, row_count, 1)
                    self.addWidget(expand_button, i, exp_col)
                else:
                    self.addWidget(expand_button, i, exp_col)

        label.setDisabled(rowdisabled)
        slider.setDisabled(rowdisabled)
        d_slider.setDisabled(rowdisabled)
        df_slider.setDisabled(rowdisabled)
        line_edit.setDisabled(rowdisabled)
        expand_button.setDisabled(rowdisabled)

        self.parent().parent().parent().addSlider(slider)
        self.parent().parent().parent().addSlider(d_slider)
        self.parent().parent().parent().addSlider(df_slider)

        # erase_button = QToolButton()
        icon = QIcon()
        pixmap = HiDpiPixmap(":/image/delete_button.png")
        #pixmap = pixmap.scaled(15, 15, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        icon.addPixmap(pixmap)

        # Create the erase button

        erase_button.setObjectName(f"eb_{item['name']}")
        erase_button.setMaximumSize(25, 25)
        erase_button.setMinimumSize(25, 25)
        erase_button.setIcon(icon)
        #erase_button.setIconSize(pixmap.rect().size())
        erase_button.setToolTip("")
        erase_button.clicked.connect(lambda _, name=item['name']: self.erase_setting(name))
        self.addWidget(erase_button, i, erase_col)
        sp_retain = erase_button.sizePolicy()
        sp_retain.setRetainSizeWhenHidden(True)
        erase_button.setSizePolicy(sp_retain)
        erase_button.setVisible(False)

        replacematch = "Model (user)"
        if G.settings_mgr.offline_mode:
            replacematch = G.settings_mgr.offline_scope.lower() + " (user)"

        if item['replaced'].lower() == replacematch.lower():
            if item['name'] != 'type':  # dont erase type on mainwindow settings
                logging.debug(f"show erase {G.settings_mgr.offline_scope}")
                erase_button.setVisible(True)
                erase_button.setToolTip("Reset to Default, Right-Click for more options")

        self.setRowStretch(i, 0)


        if item['has_expander'].lower() == 'true' or item['datatype'] == 'group':
            # These are top level config sections that have an expander button but do not have any ".1" sliders
            # color = "#cc7ee0" if G.useDarkMode else "#ab37c8"              # lighter purple looks better in dark
            # color = "#ab37c8" if item['datatype'] == 'group' else color    # unless its big group font
            labeltext = item["name"] if self.show_settings_names else item["displayname"]
            if '.0' not in item['order']:
                # label.text_label.setText(f'<a href="#" style="color: {color};">{labeltext}</a>')
                clickaction = "Expand" if item['name'] not in self.expanded_items else "Collapse"
                label.text_label.setToolTip(f'Click to {clickaction}')
                label.setClickable(True)
                label.clicked.connect(expand_button.click)
                label.text_label.setStyleSheet(styles.GROUP_LABEL_STYLESHEET)
                # label.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                # label.text_label.setOpenExternalLinks(False)
                # label.text_label.linkActivated.connect(expand_button.click)
            else:
                label.text_label.setStyleSheet("color: #ab37c8;")

        if item['order'][-1:] == '1' and '.' in item['order']:
            # For ".1" config objects, they take the place of the parent setting in the row when enabled so #we
            # need to find the expander button from the prerequisite object
            parent = item['prereq']
            parent_expand_button = self.mainwindow.findChild(QToolButton, f"ex_{parent}")
            if parent_expand_button is not None:
                # label.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                # label.text_label.setOpenExternalLinks(False)
                labeltext = item["name"] if self.show_settings_names else item["displayname"]
                color = "#cc7ee0" if G.useDarkMode else "#ab37c8"
                # label.text_label.setText(f'<a href="#" style="color: {color};">{labeltext}</a>')
                label.text_label.setStyleSheet(styles.EXPAND_LABEL_STYLESHEET)
                label.setClickable(True)
                label.clicked.connect(lambda parent_name=parent: self.expander_hyperlink_clicked(parent_name))

    def expander_hyperlink_clicked(self, parent):
        dbprint("red", f"EXPANDER: {parent}")
        parent_expand_button = self.mainwindow.findChild(QToolButton, f"ex_{parent}")
        if parent_expand_button is not None:
            parent_expand_button.click()

    # expander.click()
    def show_erase_button(self, setting_name=None):
        if setting_name is None:
            setting_name = self.sender().objectName()

        # This regex pattern matches any prefix followed by an underscore and captures the rest as the settingname
        pattern = r'^[^_]+_(.+)$'
        setting = re.match(pattern, setting_name).group(1)
        # dbprint("red", f"SETTING: {setting}")
        eb = self.mainwindow.findChild(QPushButton, f"eb_{setting}")
        if eb is not None:
            eb.setVisible(True)
            eb.setToolTip("Reset to Default")
        else:
            logging.info(f"Can't find erase button for '{setting}'.  Probably child device being configured via master")

    def remove_widget(self, olditem):
        widget = olditem.widget()
        if widget is not None:
            widget.deleteLater()
            self.removeWidget(widget)

    def enforce_exclusives(self, name, state, exclusive_list):
        """Used to force disable bool settings that are mutually exclusive with others"""
        if state != 2:
            # we are only interested in bool settings that have been enabled (state = 2)
            return None
        for pair in exclusive_list:  # Iterate through prebuilt exclusivity list to find one matching this call
            a = pair[0]  # This is the enforcing setting
            b = pair[1]  # This is the setting(s) which need to be disabled if 'a' is enabled (comma separated)
            if a == name:  # Find if there is pair that matches this function call
                disable_list = b.split(',')
                return disable_list
        return None


    def checkbox_changed(self, name, state):
        self.trigger_form_reload = True
        logging.debug(f"Checkbox {name} changed. New state: {state}")
        value = 'false' if state == 0 else 'true'
        enforce_list = self.enforce_exclusives(name, state, self.exclusive_list)  # Check for and enforce exclusive settings
        if value == 'true' and enforce_list is not None:
            for exclusive in enforce_list:
                #enforce_list is a list of settings that must not be true if 'value' is true (per exclusive attribute in defauls.xml)
                xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, "false", exclusive)

        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value, name)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def erase_setting(self, name):
        self.trigger_form_reload = True
        logging.debug(G.settings_mgr.offline_scope)
        logging.debug(f"Erase {name} clicked")
        xmlutils.erase_from_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, name, )
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def browse_for_config(self):
        self.trigger_form_reload = False
        options = QFileDialog.Option(0)
        # options |= QFileDialog.Option.DontUseNativeDialog
        calling_button = self.sender()
        starting_dir = os.getcwd()
        if calling_button:
            tooltip_text = calling_button.toolTip()
            print(f"Tooltip text of the calling button: {tooltip_text}")
            if os.path.isfile(tooltip_text):
                # Use the existing file path as the starting point
                starting_dir = os.path.dirname(tooltip_text)

        # Open the file browser dialog
        file_path, _ = QFileDialog.getOpenFileName(self.mainwindow, "Choose File", starting_dir, "vpconf Files (*.vpconf)", options=options)

        if file_path:
            cfg_scope = xmlutils.device
            key = "pid" + cfg_scope.capitalize()
            pid = G.system_settings.get(key, '')

            if validate_vpconf_profile(file_path, pid=pid, dev_type=cfg_scope):
                #lprint(f"Selected File: {file_path}")
                xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, file_path, 'vpconf')

                self.show_erase_button('config_vpconf')
                self.vpconf_browse_button.setText(os.path.basename(file_path))
                if G.settings_mgr.timed_out:
                    self.reload_caller()

    def spin_box_changed(self):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('sb_', '')
        value = str(self.sender().value())
        logging.debug(f"Spin Box {setting_name} changed. New value: {value}")
        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value, setting_name)
        self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def textbox_changed(self):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('le_', '')
        value = self.sender().text()
        logging.debug(f"Textbox {setting_name} changed. New value: {value}")
        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value, setting_name)
        self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def dropbox_changed(self):
        self.trigger_form_reload = True
        sender = self.sender()
        object_name = sender.objectName()
        setting_name = object_name.replace('edb_', '').replace('db_', '')  # handle both db_ and edb_

        # If it's an enum dropbox, get the enum member name
        if object_name.startswith("edb_"):
            enum_member = sender.itemData(sender.currentIndex())
            value = enum_member.name if enum_member else sender.currentText()
        else:
            value = sender.currentText()

        logging.debug(f"Dropbox {setting_name} changed. New value: {value}")
        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value, setting_name)
        # commented out because erase happens on setting load
        # if setting_name == 'spring_mode':
        #     xmlutils.erase_models_from_xml(G.settings_mgr.current_sim,G.settings_mgr.current_pattern, 'adv_spr_override_enabled')
        #     xmlutils.erase_models_from_xml(G.settings_mgr.current_sim,G.settings_mgr.current_pattern, 'aircraft_is_spring_centered')
        #     xmlutils.erase_models_from_xml(G.settings_mgr.current_sim,G.settings_mgr.current_pattern, 'aircraft_is_fbw')
        #     if G.settings_mgr.timed_out:
        #         pass
        # if setting_name == 'gforce_effect':
        #     xmlutils.erase_models_from_xml(G.settings_mgr.current_sim,G.settings_mgr.current_pattern, 'gforce_effect_enable')
        #     xmlutils.erase_models_from_xml(G.settings_mgr.current_sim,G.settings_mgr.current_pattern, 'new_gforce_effect_enable')
        #     xmlutils.erase_models_from_xml(G.settings_mgr.current_sim,G.settings_mgr.current_pattern, 'gforce_effect_advanced_enabled')
        #     if G.settings_mgr.timed_out:
        #         pass
        self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def unit_dropbox_changed(self):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('ud_', '')
        line_edit_name = 'vle_' + self.sender().objectName().replace('ud_', '')
        line_edit = self.mainwindow.findChild(QLineEdit, line_edit_name)
        value = ''
        unit = self.sender().currentText()
        if line_edit is not None:
            value = line_edit.text()
        logging.debug(f"Unit {self.sender().objectName()} changed. New value: {value}{unit}")
        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value, setting_name, unit)
        self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def line_edit_changed(self):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('vle_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('vle_', '')
        unit_dropbox = self.mainwindow.findChild(QComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value = self.sender().text()
        logging.debug(f"Text box {self.sender().objectName()} changed. New value: {value}{unit}")
        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value, setting_name, unit)
        self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def expander_clicked(self):
        self.trigger_form_reload = True
        logging.debug(f"expander {self.sender().objectName()} clicked.  value: {self.sender().text()}")
        settingname = self.sender().objectName().replace('ex_', '')
        if self.sender().arrowType() == Qt.ArrowType.RightArrow:
            # print ('expanded')

            self.expanded_items.append(settingname)
            self.sender().setArrowType(Qt.ArrowType.DownArrow)

            self.reload_caller()
        else:
            # print ('collapsed')
            new_exp_items = []
            for ex in self.expanded_items:
                if ex != settingname:
                    new_exp_items.append(ex)
            self.expanded_items = new_exp_items
            self.sender().setArrowType(Qt.ArrowType.DownArrow)

            self.reload_caller()

    def usb_button_clicked(self):
        if G.master_instance and G.device_type != G.current_device_config_scope:
            # button is being bound for non master device
            target_device = G.current_device_config_scope
        else:
            target_device = None

        button_name = self.sender().objectName().replace('pb_', '')
        the_button = self.mainwindow.findChild(QPushButton, f'pb_{button_name}')
        the_button.setText("Push a button! ")
        # listen for button loop
        # Start a thread to fetch button press with a timeout
        self.thread = ButtonPressThread(self.device, self.sender(), target_device)
        self.thread.button_pressed.connect(self.update_button)
        self.thread.start()

    def update_button(self, button_name, value):
        self.trigger_form_reload = False
        the_button = self.mainwindow.findChild(QPushButton, f'pb_{button_name}')
        the_button.setText(str(value))
        if str(value) != '0':
            xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, str(value), button_name)
            self.show_erase_button(setting_name=f'pb_{button_name}')
        else:
            the_button.setText("Click to Configure")
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def advanced_g_button_clicked(self, value=None):
        if value is None:
            value = self.advanced_g_settings
        if G.master_instance and G.device_type != G.current_device_config_scope:
            G.ipc_instance.send_broadcast_message(f'SHOW ADV SPR:{G.current_device_config_scope}')
        else:
            if self.adv_g_dialog is None:
                self.adv_g_dialog = AdvancedGDialog(parent=G.main_window, settings=value, device=G.current_device_config_scope)
                self.adv_g_dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
                self.adv_g_dialog.accepted.connect(self.update_advanced_g_effect)
                self.adv_g_dialog.show()
            else:
                self.adv_g_dialog.showme(settings=value)

    def advanced_spring_button_clicked(self, value=None):
        if value is None:
            value = self.advanced_spring_settings
        if G.master_instance and G.device_type != G.current_device_config_scope:
            G.ipc_instance.send_broadcast_message(f'SHOW ADV SPR;{G.current_device_config_scope};{value}')  # use semicolon for delimiter as value contains colons
        else:
            if self.adv_spr_dialog is None:
                self.adv_spr_dialog = AdvancedSpringDialog(parent=G.main_window, settings=value, device=G.current_device_config_scope, sim=G.settings_mgr.current_sim)
                self.adv_spr_dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
                self.adv_spr_dialog.accepted.connect(self.update_advanced_spring_gains)
                self.adv_spr_dialog.show()
            else:
                self.adv_spr_dialog.showme(settings=value, sim=G.settings_mgr.current_sim)

    def configurator_button_clicked(self):
        """
        User clicked the configurator gain override settings button.
        If the current settings scope is not the master device, send the command over IPC to have the child instance
        pop its dialog window.
        """
        if G.master_instance and G.device_type != G.current_device_config_scope:
            G.ipc_instance.send_broadcast_message(f'SHOW GAIN OVD:{G.current_device_config_scope}')
        else:
            # dbprint("red", f"Device = {G.device_type}")
            # dbprint("green", f"Scope = {G.current_device_config_scope}")
            G.gain_override_dialog.accepted.connect(self.update_configurator_overrides)
            G.gain_override_dialog.raise_()
            G.gain_override_dialog.activateWindow()
            G.gain_override_dialog.show()

    def erase_configurator_overrides(self):
        if G.master_instance and G.device_type != G.current_device_config_scope:
            G.ipc_instance.send_broadcast_message(f'ERASE GAIN OVD:{G.current_device_config_scope}')
        else:
            G.gain_override_dialog.revert_gains()
            G.gain_override_dialog.reset_to_vpconf()

    def update_advanced_g_effect(self, g_effect_curves: str):
        self.trigger_form_reload = True
        """
        Called when signal received that user saved the advanced g-effect settings
        """
        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, g_effect_curves,"gforce_effect_adv_curve")
        self.show_erase_button("config_gforce_effect_adv_curve")
        # self.adv_spr_button.setText("Edit Spring Gains")
        # self.adv_spr_button.clicked.disconnect(lambda: self.advanced_spring_button_clicked(spring_gain_curves))
        self.reload_caller()

    def update_advanced_spring_gains(self, spring_gain_curves: str, scale: str, units: str):
        self.trigger_form_reload = True
        """
        Called when signal received that user saved the advanced spring gain settings
        """
        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, spring_gain_curves, "adv_spr_gains")
        # xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, str(scale), "vne_override", unit=units)
        self.show_erase_button("config_adv_spr_gains")
        # self.adv_spr_button.setText("Edit Spring Gains")
        # self.adv_spr_button.clicked.disconnect(lambda: self.advanced_spring_button_clicked(spring_gain_curves))
        self.reload_caller()

    def update_configurator_overrides(self, gain_dict):
        self.trigger_form_reload = False
        """
        Called when signal received that user saved the configurator gain settings
        convert dictionary to text string and write to config.
        """
        gain_dict_json = json.dumps(gain_dict)
        xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, gain_dict_json, "configurator_gains")
        self.show_erase_button("config_configurator_gains")
        self.configurator_button.setText("Edit Gain Overrides")

    def slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('sld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('sld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('sld_', '')
        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)
        value = 0
        factor = 1.0
        if value_label is not None:
            value_label.setText(str(self.sender().value()) + '%')
            value = int(self.sender().value())
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())
        value_to_save = str(round(value * factor / 100, 4))
        if self.show_slider_debug:
            logging.debug(f"Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}")
        if write:
            xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value_to_save, setting_name)
            self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def cfg_slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('sld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('sld_', '')

        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        value = 0
        factor = 1.0
        if value_label is not None:
            value_label.setText(str(self.sender().value()) + '%')
            value = int(self.sender().value())

        value_to_save = str(round(value / 100, 4))
        if self.show_slider_debug:
            logging.debug(f"Slider {self.sender().objectName()} cfg changed. New value: {value}  saving: {value_to_save}")
        if write:
            xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value_to_save, setting_name)
            self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def d_slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('dsld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('dsld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('dsld_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('dsld_', '')
        unit_dropbox = self.mainwindow.findChild(QComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)
        factor = 1
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())
        if value_label is not None:
            value = self.sender().value()
            value_to_save = str(round(value * factor))
            value_label.setText(value_to_save)
        if self.show_slider_debug:
            logging.debug(f"d_Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}{unit}")
        if write:
            xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value_to_save, setting_name, unit)
            self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def df_slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('dfsld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('dfsld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('dfsld_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('dfsld_', '')
        unit_dropbox = self.mainwindow.findChild(QComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)
        factor = 1
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())
        if value_label is not None:
            value = self.sender().value()
            value_to_save = str(round(value * factor, 2))
            value_label.setText(value_to_save)
        if self.show_slider_debug:
            logging.debug(f"df_Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}{unit}")
        if write:
            xmlutils.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value_to_save, setting_name, unit)
            self.show_erase_button()
        if G.settings_mgr.timed_out:
            self.reload_caller()

    # # prevent slider from sending values as you drag
    # def sldDisconnect(self):
    #     self.sender().valueChanged.disconnect()
    #
    # # reconnect slider after you let go
    # def sldReconnect(self):
    #     self.sender().valueChanged.connect(self.slider_changed)
    #     self.sender().valueChanged.emit(self.sender().value())
    #
    # def cfg_sldReconnect(self):
    #     self.sender().valueChanged.connect(self.cfg_slider_changed)
    #     self.sender().valueChanged.emit(self.sender().value())
    #
    # def d_sldReconnect(self):
    #     self.sender().valueChanged.connect(self.d_slider_changed)
    #     self.sender().valueChanged.emit(self.sender().value())
    #
    # def df_sldReconnect(self):
    #     self.sender().valueChanged.connect(self.df_slider_changed)
    #     self.sender().valueChanged.emit(self.sender().value())