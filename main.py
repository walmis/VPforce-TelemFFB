# 
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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

import sys
if sys.argv[0].lower().endswith("updater.exe"):
    from updater import main
    main()
    sys.exit()

import argparse
import json
import logging

import time
import os

from telemffb.IPCNetworkThread import IPCNetworkThread
from telemffb.config_utils import autoconvert_config
from telemffb.telem.NetworkThread import NetworkThread
from telemffb.telem.SimConnectSock import SimConnectSock
from telemffb.telem.TelemManager import TelemManager
from telemffb.LogWindow import LogWindow
from telemffb.ConfiguratorDialog import ConfiguratorDialog
from telemffb.TeleplotSetupDialog import TeleplotSetupDialog
from telemffb.SettingsLayout import SettingsLayout
from telemffb.LogTailWindow import LogTailWindow
from telemffb.SystemSettingsDialog import SystemSettingsDialog
from telemffb.SCOverridesEditor import SCOverridesEditor
from telemffb.sim import aircrafts_msfs_xp
from telemffb.telem.il2_telem import IL2Manager
from telemffb.sim.aircraft_base import effects

import telemffb.globals as G

import telemffb.hw.ffb_rhino as ffb_rhino
import telemffb.utils as utils
from telemffb.utils import AnsiColors

import re
import threading
from collections import OrderedDict
import subprocess

import traceback
from traceback import print_exc

from telemffb.hw.ffb_rhino import HapticEffect, FFBRhino

from telemffb.settingsmanager import *
from telemffb.utils import LoggingFilter
from telemffb.utils import load_custom_userconfig
from telemffb.utils import set_vpconf_profile
from telemffb.utils import save_main_window_geometry
import telemffb.xmlutils as xmlutils

from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QMainWindow, QVBoxLayout, QMessageBox, QPushButton, QRadioButton, QScrollArea, QHBoxLayout, QAction, QMenu, QButtonGroup, QFrame, \
    QTabWidget, QGroupBox, QShortcut
from PyQt5.QtCore import Qt, QCoreApplication, QUrl, QSize, QByteArray, QTimer, QSettings
from PyQt5.QtGui import QPixmap, QIcon, QDesktopServices, QPainter, QColor, QKeyEvent, QCursor, \
    QTextCursor, QKeySequence
from PyQt5.QtWidgets import QGridLayout, QToolButton

from telemffb.custom_widgets import *

import resources
resources # used

class MainWindow(QMainWindow):
    show_simvars = False
    def __init__(self):
        super().__init__()

        global log_tail_window
        self._latest_version_url = None
        self.latest_version = None
        self._update_available = None

        self.show_new_craft_button = False
        # Get the absolute path of the script's directory
        # script_dir = os.path.dirname(os.path.abspath(__file__))
        doc_url = 'https://vpforcecontrols.com/downloads/VPforce_Rhino_Manual.pdf'

        if _release:
            dl_url = 'https://github.com/walmis/VPforce-TelemFFB/releases'
        else:
            dl_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=D'

        # notes_url = os.path.join(script_dir, '_RELEASE_NOTES.txt')
        notes_url = utils.get_resource_path('_RELEASE_NOTES.txt')
        self._current_config_scope = G.device_type
        self.current_tab_index = 0

        if G.system_settings.get('saveLastTab', 0):
            data = G.system_settings.get("WindowData")
            if data is not None:
                tab = json.loads(data)
                self.current_tab_index = tab.get("Tab", 0)

        self.default_tab_sizes = {
            "0": {  # monitor
                'height': 530,
                'width': 700,
            },
            "1": {  # settings
                'height': 530,
                'width': 700,
            },
            # "2": {  # log
            #     'height': 530,
            #     'width': 700,
            # },
            "2": {  # hide
                'height': 0,
                'width': 0,
            }
        }
        self.setMinimumWidth(600)
        self.tab_sizes = self.default_tab_sizes
        
        self.settings_layout = SettingsLayout(parent=self, mainwindow=self)
        match G.device_type:
            case 'joystick':
                x_pos = 150
                y_pos = 130
            case 'pedals':
                x_pos = 100
                y_pos = 100
            case 'collective':
                x_pos = 50
                y_pos = 70

        self.setGeometry(x_pos, y_pos, 530, 700)
        if version:
            self.setWindowTitle(f"TelemFFB ({G.device_type}) ({version})")
        else:
            self.setWindowTitle(f"TelemFFB")
        # Construct the absolute path of the icon file
        icon = QIcon(":/image/vpforceicon.png")

        self.setWindowIcon(icon)

        self.resize(530, 700)
        self.hidden_active = False
        # Create a layout for the main window
        layout = QVBoxLayout()
        notes_row_layout = QHBoxLayout()

        # Create the menu bar
        menu_frame = QFrame()
        menu_frame_layout = QVBoxLayout(menu_frame)

        # Create the menu bar
        menubar = self.menuBar()
        self.menu = menubar
        # Set the background color of the menu bar
        # "#ab37c8" is VPForce purple
        self.menu.setStyleSheet("""
            QMenuBar { background-color: #f0f0f0; } 
            QMenu::item {background-color: transparent;}
            QMenu::item:selected { color: #ffffff; background-color: "#ab37c8"; } 
        """)
        # Add the "System" menu and its sub-option

        system_menu = self.menu.addMenu('&System')

        system_settings_action = QAction('System Settings', self)
        system_settings_action.triggered.connect(self.open_system_settings_dialog)
        system_menu.addAction(system_settings_action)

        settings_manager_action = QAction('Edit Sim/Class Defaults && Offline Models', self)
        settings_manager_action.triggered.connect(self.toggle_settings_window)
        system_menu.addAction(settings_manager_action)



        cfg_log_folder_action = QAction('Open Config/Log Directory', self)
        def do_open_cfg_dir():
            modifiers = QApplication.keyboardModifiers()
            if (modifiers & QtCore.Qt.ControlModifier) and (modifiers & QtCore.Qt.ShiftModifier) and getattr(sys, 'frozen', False):
                os.startfile(sys._MEIPASS, 'open')
            else:
                os.startfile(G.userconfig_rootpath, 'open')
        cfg_log_folder_action.triggered.connect(do_open_cfg_dir)
        system_menu.addAction(cfg_log_folder_action)

        reset_geometry = QAction('Reset Window Size/Position', self)

        def do_reset_window_size():
            match G.device_type:
                case 'joystick':
                    x_pos = 150
                    y_pos = 130
                case 'pedals':
                    x_pos = 100
                    y_pos = 100
                case 'collective':
                    x_pos = 50
                    y_pos = 70
            self.setGeometry(x_pos, y_pos, 530, 700)

        reset_geometry.triggered.connect(do_reset_window_size)
        system_menu.addAction(reset_geometry)

        # self.menu.setStyleSheet("QMenu::item:selected { color: red; }")
        exit_app_action = QAction('Quit TelemFFB', self)
        exit_app_action.triggered.connect(exit_application)
        system_menu.addAction(exit_app_action)

        # Create the "Utilities" menu
        utilities_menu = self.menu.addMenu('Utilities')

        # Add the "Reset" action to the "Utilities" menu
        reset_action = QAction('Reset All Effects', self)
        reset_action.triggered.connect(self.reset_all_effects)
        utilities_menu.addAction(reset_action)

        self.update_action = QAction('Install Latest TelemFFB', self)
        self.update_action.triggered.connect(self.update_from_menu)
        if not _release:
            utilities_menu.addAction(self.update_action)
        self.update_action.setDisabled(True)

        download_action = QAction('Download Other Versions', self)
        download_action.triggered.connect(lambda: self.open_url(dl_url))
        utilities_menu.addAction(download_action)

        self.reset_user_config_action = QAction('Reset User Config', self)
        self.reset_user_config_action.triggered.connect(self.reset_user_config)
        utilities_menu.addAction(self.reset_user_config_action)

        self.vpconf_action = QAction("Launch VPforce Configurator", self)
        self.vpconf_action.triggered.connect(lambda: utils.launch_vpconf(dev_serial))
        utilities_menu.addAction(self.vpconf_action)

        # Add settings converter
        if _legacy_override_file is not None:
            convert_settings_action = QAction('Convert legacy user config to XML', self)
            convert_settings_action.triggered.connect(lambda: autoconvert_config(self, _legacy_config_file, _legacy_override_file))
            utilities_menu.addAction(convert_settings_action)

        if G._master_instance and G.system_settings.get('autolaunchMaster', 0):
            self.window_menu = self.menu.addMenu('Window')

            def do_toggle_child_windows(toggle):
                if toggle == 'show':
                    G.ipc_instance.send_broadcast_message("SHOW WINDOW")
                elif toggle == 'hide':
                    G.ipc_instance.send_broadcast_message("HIDE WINDOW")

            self.show_children_action = QAction('Show Child Instance Windows')
            self.show_children_action.triggered.connect(lambda: do_toggle_child_windows('show'))
            self.window_menu.addAction(self.show_children_action)
            self.hide_children_action = QAction('Hide Child Instance Windows')
            self.hide_children_action.triggered.connect(lambda: do_toggle_child_windows('hide'))
            self.window_menu.addAction(self.hide_children_action)

        if G._child_instance:
            self.window_menu = self.menu.addMenu('Window')
            self.hide_window_action = QAction('Hide Window')
            def do_hide_window():
                try:
                    self.hide()
                except Exception as e:
                    logging.error(f"EXCEPTION: {e}")
            self.hide_window_action.triggered.connect(do_hide_window)
            self.window_menu.addAction(self.hide_window_action)

        log_menu = self.menu.addMenu('Log')
        self.log_window_action = QAction("Open Console Log", self)
        
        def do_toggle_log_window():
            if G.log_window.isVisible():
                G.log_window.hide()
            else:
                G.log_window.move(self.x()+50, self.y()+100)
                G.log_window.show()

        self.log_window_action.triggered.connect(do_toggle_log_window)
        log_menu.addAction(self.log_window_action)
        if G._master_instance and G.system_settings.get('autolaunchMaster', 0):
            self.child_log_menu = log_menu.addMenu('Open Child Logs')
            
            self.log_action = {}
            for d in ["joystick", "pedals", "collective"]:
                if d in G._launched_instances:
                    def do_show_child_log(child=d):
                        G.ipc_instance.send_broadcast_message(f'SHOW LOG:{child}')
                    log_action = QAction(f'{d} Log'.capitalize())
                    log_action.triggered.connect(lambda: do_show_child_log())
                    self.log_action[d] = log_action
                    self.child_log_menu.addAction(log_action)


        help_menu = self.menu.addMenu('Help')

        notes_action = QAction('Release Notes', self)
        def do_open_file(url):
            try:
                file_url = QUrl.fromLocalFile(url)
                QDesktopServices.openUrl(file_url)
            except Exception as e:
                logging.error(f"There was an error opening the file: {str(e)}")
        notes_action.triggered.connect(lambda : do_open_file(notes_url))
        help_menu.addAction(notes_action)

        docs_action = QAction('Documentation', self)
        docs_action.triggered.connect(lambda: self.open_url(doc_url))
        help_menu.addAction(docs_action)

        self.support_action = QAction("Create support bundle", self)
        self.support_action.triggered.connect(lambda: utils.create_support_bundle(G.userconfig_rootpath))
        help_menu.addAction(self.support_action)

        # Create a line beneath the menu bar
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)

        # Add the line to the menu frame layout
        menu_frame_layout.addWidget(menubar)
        menu_frame_layout.addWidget(line)
        menu_frame_layout.setContentsMargins(0, 0, 0, 0)

        # Set the layout of the menu frame as the main layout
        layout.addWidget(menu_frame)

        dcs_enabled = G.system_settings.get('enableDCS')
        il2_enabled = G.system_settings.get('enableIL2')
        msfs_enabled = G.system_settings.get('enableMSFS')
        xplane_enabled = G.system_settings.get('enableXPLANE')

        self.icon_size = QSize(18, 18)
        if G.args.sim == "DCS" or dcs_enabled:
            dcs_color = QColor(255, 255, 0)
            dcs_icon = self.create_colored_icon(dcs_color, self.icon_size)
        else:
            dcs_color = QColor(128, 128, 128)
            dcs_icon = self.create_x_icon(dcs_color, self.icon_size)

        if G.args.sim == "MSFS" or msfs_enabled:
            msfs_color = QColor(255, 255, 0)
            msfs_icon = self.create_colored_icon(msfs_color, self.icon_size)
        else:
            msfs_color = QColor(128, 128, 128)
            msfs_icon = self.create_x_icon(msfs_color, self.icon_size)

        if G.args.sim == "IL2" or il2_enabled:
            il2_color = QColor(255, 255, 0)
            il2_icon = self.create_colored_icon(il2_color, self.icon_size)
        else:
            il2_color = QColor(128, 128, 128)
            il2_icon = self.create_x_icon(il2_color, self.icon_size)

        if G.args.sim == "XPLANE" or xplane_enabled:
            xplane_color = QColor(255, 255, 0)
            xplane_icon = self.create_colored_icon(xplane_color, self.icon_size)
        else:
            xplane_color = QColor(128, 128, 128)
            xplane_icon = self.create_x_icon(xplane_color, self.icon_size)


        logo_status_layout = QHBoxLayout()

        # Add a label for the image
        # Construct the absolute path of the image file
        self.logo_stack = QGroupBox()
        self.vpflogo_label = QLabel(self.logo_stack)
        self.devicetype_label = ClickLogo(self.logo_stack)
        self.devicetype_label.clicked.connect(self.device_logo_click_event)
        pixmap = QPixmap(_vpf_logo)
        pixmap2 = QPixmap(utils.get_device_logo(G.device_type))
        self.vpflogo_label.setPixmap(pixmap)
        self.devicetype_label.setPixmap(pixmap2)
        self.devicetype_label.setScaledContents(True)

        # Resize QGroupBox to match the size of the larger label
        max_width = pixmap.width()
        max_height = pixmap.height()
        self.logo_stack.setFixedSize(max_width, max_height)
        self.logo_stack.setStyleSheet("QGroupBox { border: none; }")
        # Align self.image_label2 with the upper left corner of self.image_label
        self.devicetype_label.move(self.vpflogo_label.pos())
        if not G.args.child:
            self.devicetype_label.hide()
        # Add the image labels to the layout
        logo_status_layout.addWidget(self.logo_stack, alignment=Qt.AlignVCenter | Qt.AlignLeft)

        rh_status_area = QWidget()
        rh_status_layout = QVBoxLayout()

        sim_status_area = QWidget()
        status_layout = QGridLayout()

        self.dcs_label_icon = QLabel("", self)
        self.dcs_label_icon.setPixmap(dcs_icon)
        self.dcs_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dcs_label = QLabel("DCS", self)
        dcs_label.setStyleSheet("""padding: 2px""")
        dcs_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.dcs_label_icon, 0, 0)
        status_layout.addWidget(dcs_label, 0, 1)

        self.il2_label_icon = QLabel("", self)
        self.il2_label_icon.setPixmap(il2_icon)
        self.il2_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        il2_label = QLabel("IL2", self)
        il2_label.setStyleSheet("""padding: 2px""")
        il2_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.il2_label_icon, 0, 2)
        status_layout.addWidget(il2_label, 0, 3)

        self.msfs_label_icon = QLabel("", self)
        self.msfs_label_icon.setPixmap(msfs_icon)
        self.msfs_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        msfs_label = QLabel("MSFS", self)
        msfs_label.setStyleSheet("""padding: 2px""")
        msfs_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.msfs_label_icon, 0, 4)
        status_layout.addWidget(msfs_label, 0, 5)

        self.xplane_label_icon = QLabel("", self)
        self.xplane_label_icon.setPixmap(dcs_icon)
        self.xplane_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dcs_label = QLabel("X-Plane", self)
        dcs_label.setStyleSheet("""padding: 2px""")
        dcs_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.xplane_label_icon, 0, 6)
        status_layout.addWidget(dcs_label, 0, 7)


        status_layout.setAlignment(Qt.AlignRight)
        self.init_sim_indicators(['DCS', 'IL2', 'MSFS', 'XPLANE'], G.system_settings)



        # self.xplane_label_icon = QLabel("", self)
        # xplane_icon = self.create_colored_icon(xplane_color, self.icon_size)
        # self.xplane_label_icon.setPixmap(xplane_icon)
        # self.xplane_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # xplane_label = QLabel("XPlane", self)
        # status_layout.addWidget(self.xplane_label_icon, 1, 0)
        # status_layout.addWidget(xplane_label, 1, 1)
        #
        # self.condor_label_icon = QLabel("", self)
        # condor_icon = self.create_colored_icon(condor_color, self.icon_size)
        # self.condor_label_icon.setPixmap(condor_icon)
        # self.condor_label_icon.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # condor_label = QLabel("Condor2", self)
        # status_layout.addWidget(self.condor_label_icon, 1, 2)
        # status_layout.addWidget(condor_label, 1, 3)

        sim_status_area.setLayout(status_layout)

        rh_status_layout.addWidget(sim_status_area)
        rh_status_layout.setAlignment(Qt.AlignRight)

        ############
        # current craft

        cur_ac_lbl = QLabel()
        cur_ac_lbl.setText("<b>Current Aircraft:</b>")
        cur_ac_lbl.setAlignment(Qt.AlignLeft)
        cur_ac_lbl.setStyleSheet("QLabel { padding-left: 10px; padding-top: 2px; }")

        self.cur_craft = QLabel()
        self.cur_craft.setText('Unknown')
        self.cur_craft.setStyleSheet("QLabel { padding-left: 15px; padding-top: 2px; font-family: Courier New; }")
        self.cur_craft.setAlignment(Qt.AlignLeft)

        self.cur_pattern = QLabel()
        self.cur_pattern.setText('(No Match)')
        self.cur_pattern.setStyleSheet("QLabel { padding-left: 15px; padding-top: 2px; font-family: Courier New; }")
        self.cur_pattern.setAlignment(Qt.AlignLeft)

        rh_status_layout.addWidget(cur_ac_lbl)
        rh_status_layout.addWidget(self.cur_craft)
        rh_status_layout.addWidget(self.cur_pattern)

        rh_status_area.setLayout(rh_status_layout)

        logo_status_layout.addWidget(rh_status_area)

        layout.addLayout(logo_status_layout)

        ##################
        #  new craft button

        new_craft_layout = QVBoxLayout()
        self.new_craft_button = QPushButton('Create/clone config for new aircraft')
        ncb_css = """QPushButton {
                            background-color: #ab37c8;
                            border-style: outset;
                            border-width: 1px;
                            border-radius: 10px;
                            border-color: black;
                            color: white;
                            font: bold 14px;
                            min-width: 10em;
                            padding: 5px;
                        }"""
        self.new_craft_button.setStyleSheet(ncb_css)
        self.new_craft_button.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
        new_craft_layout.addWidget(self.new_craft_button)
        new_craft_layout.addSpacing(7)
        self.new_craft_button.clicked.connect(self.show_user_model_dialog)
        layout.addLayout(new_craft_layout)
        self.new_craft_button.hide()

        #####################
        #  test loading buttons, set to true for debug

        self.test_craft_area = QWidget()
        test_craft_layout = QHBoxLayout()
        test_sim_lbl = QLabel('Sim:')
        test_sim_lbl.setMaximumWidth(30)
        test_sim_lbl.setAlignment(Qt.AlignRight)
        sims = ['', 'DCS', 'IL2', 'MSFS', 'XPLANE']
        self.test_sim = QComboBox()
        self.test_sim.setMaximumWidth(60)
        self.test_sim.addItems(sims)
        self.test_sim.currentTextChanged.connect(self.test_sim_changed)
        test_name_lbl = QLabel('Aircraft Name:')
        test_name_lbl.setMaximumWidth(90)
        test_name_lbl.setAlignment(Qt.AlignRight)
        self.test_name = QComboBox()
        self.test_name.setMinimumWidth(100)
        self.test_name.setEditable(True)
        test_button = QToolButton()
        test_button.setMaximumWidth(20)
        test_button.setText('>')
        test_button.clicked.connect(self.force_sim_aircraft)
        test_craft_layout.addWidget(test_sim_lbl)
        test_craft_layout.addWidget(self.test_sim)
        test_craft_layout.addWidget(test_name_lbl)
        test_craft_layout.addWidget(self.test_name)
        test_craft_layout.addWidget(test_button)
        self.test_craft_area.setLayout(test_craft_layout)
        self.test_craft_area.hide()
        layout.addWidget(self.test_craft_area)
        self.configmode_group = QButtonGroup()
        self.cb_joystick = QCheckBox('Joystick')
        self.cb_pedals = QCheckBox('Pedals')
        self.cb_collective = QCheckBox('Collective')

        self.config_scope_row = QHBoxLayout()
        self.configmode_group.addButton(self.cb_joystick, 1)
        self.configmode_group.addButton(self.cb_pedals, 2)
        self.configmode_group.addButton(self.cb_collective, 3)
        self.configmode_group.buttonClicked[int].connect(self.change_config_scope)
        self.config_scope_row.addWidget(self.cb_joystick)
        self.config_scope_row.addWidget(self.cb_pedals)
        self.config_scope_row.addWidget(self.cb_collective)
        self.cb_joystick.setVisible(False)
        self.cb_pedals.setVisible(False)
        self.cb_collective.setVisible(False)

        layout.addLayout(self.config_scope_row)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setTabShape(QTabWidget.Triangular)  # Set triangular tab shape
        # self.tab_widget.addTab(QWidget(), "Log")
        # self.tab_widget.setCursor(QCursor(QtCore.Qt.PointingHandCursor))

        # Set the main window area height to 0
        self.tab_widget.setMinimumHeight(14)
        style_sheet = """
                    QTabBar::tab:selected {
                        background-color: #ab37c8;
                        color: white;
                    }
                """
        self.tab_widget.setStyleSheet(style_sheet)

        # Create a horizontal line widget
        self.line_widget = QFrame(self)
        self.line_widget.setFrameShape(QFrame.HLine)
        self.line_widget.setFrameShadow(QFrame.Sunken)

        # Add the tab widget and line widget to the main layout
        layout.addWidget(self.tab_widget)
        layout.setSpacing(0)
        layout.addWidget(self.line_widget)

        ################
        #  main scroll area

        self.monitor_widget = QWidget()
        self.telem_area = QScrollArea()
        monitor_area_layout = QGridLayout()
        self.telem_area.setWidgetResizable(True)
        self.telem_area.setMinimumHeight(100)

        self.effects_area = QScrollArea()
        self.effects_area.setWidgetResizable(True)
        self.effects_area.setMinimumHeight(100)
        self.effects_area.setMaximumWidth(200)

        # Create the QLabel widget and set its properties

        self.refresh_telem_status()

        self.lbl_telem_data.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_telem_data.setWordWrap(False)
        self.lbl_telem_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_telem_data.setStyleSheet("""
            padding: 2px;
            font-family: Courier New;
        """)

        # Set the QLabel widget as the widget inside the scroll area
        self.telem_area.setWidget(self.lbl_telem_data)

        self.lbl_effects_data = QLabel()
        self.effects_area.setWidget(self.lbl_effects_data)
        self.lbl_effects_data.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_effects_data.setStyleSheet("""
            padding: 2px;
            font-family: Courier New;
        """)

        self.telem_lbl = QLabel('Telemetry:')
        self.effect_lbl = QLabel('Active Effects:')
        if G._master_instance:
            self.effect_lbl.setText(f'Active Effects for: {self._current_config_scope}')
        monitor_area_layout.addWidget(self.telem_lbl, 0, 0)
        monitor_area_layout.addWidget(self.effect_lbl, 0, 1)
        monitor_area_layout.addWidget(self.telem_area, 1, 0)
        monitor_area_layout.addWidget(self.effects_area, 1, 1)

        self.monitor_widget.setLayout(monitor_area_layout)
        # Add the scroll area to the layout
        self.tab_widget.addTab(self.monitor_widget, "Monitor")

        # layout.addWidget(self.monitor_widget)

        # Create a scrollable area
        self.settings_area = NoKeyScrollArea()
        self.settings_area.setObjectName('theScrollArea')
        self.settings_area.setWidgetResizable(True)

        ##############
        # settings

        # Create a widget to hold the layout
        scroll_widget = QWidget()

        all_sliders = []
        scroll_widget.setLayout(self.settings_layout)

        self.settings_area.setWidget(scroll_widget)

        self.tab_widget.addTab(self.settings_area, "Settings")

        # self.log_tab_widget = QWidget(self.tab_widget)
        # self.tab_widget.addTab(self.log_tab_widget, "Log")

        # self.log_widget = QPlainTextEdit(self.log_tab_widget)
        # self.log_widget.setMaximumBlockCount(20)
        # self.log_widget.setReadOnly(True)
        # self.log_widget.setFont(QFont("Courier New"))
        # self.log_widget.setLineWrapMode(QPlainTextEdit.NoWrap)
        # self.log_tail_thread = LogTailer(log_file)
        # self.log_tail_thread.log_updated.connect(self.update_log_widget)
        # self.log_tail_thread.start()

        # self.clear_button = QPushButton("Clear", self.log_tab_widget)
        # self.toggle_button = QPushButton("Pause", self.log_tab_widget)
        # self.open_log_button = QPushButton("Open in Window", self.log_tab_widget)

        # self.clear_button.clicked.connect(self.clear_log_widget)
        # def toggle_log_tailing():
        #     if self.log_tail_thread.is_paused():
        #         self.log_tail_thread.resume()
        #         self.toggle_button.setText("Pause")
        #     else:
        #         self.log_tail_thread.pause()
        #         self.toggle_button.setText("Resume")
        # self.toggle_button.clicked.connect(self.toggle_log_tailing)
        # def show_tail_log_window():
        #     log_tail_window.move(self.x() + 50, self.y() + 100)
        #     log_tail_window.show()
        #     log_tail_window.activateWindow()
        # self.open_log_button.clicked.connect(show_tail_log_window)

        self.tab_widget.addTab(QWidget(), "Hide")
        self.tab_widget.currentChanged.connect(lambda index: self.switch_window_view(index))


        # log_layout = QVBoxLayout(self.log_tab_widget)
        # log_layout.addWidget(self.log_widget)

        # button_layout = QHBoxLayout()
        # button_layout.addWidget(self.clear_button)
        # button_layout.addWidget(self.toggle_button)
        # button_layout.addStretch()  # Add stretch to push the next button to the right
        # button_layout.addWidget(self.open_log_button)
        # log_layout.addLayout(button_layout)

        # self.log_tab_widget.setLayout(log_layout)
        ##############
        #  buttons

        # test buttons
        show_clear_reload = False
        if show_clear_reload:
            test_layout = QHBoxLayout()
            clear_button = QPushButton('clear')
            clear_button.clicked.connect(self.settings_layout.clear_layout)
            test_layout.addWidget(clear_button)
            self.reload_button = QPushButton('reload')
            self.reload_button.clicked.connect(self.settings_layout.reload_caller)
            test_layout.addWidget(self.reload_button)
            layout.addLayout(test_layout)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        self.layout = QVBoxLayout(central_widget)

        if G._master_instance and G._launched_children:
            self.instance_status_row = QHBoxLayout()
            self.master_status_icon = StatusLabel(None, f'This Instance({ G.device_type.capitalize() }):', Qt.green, 8)
            self.joystick_status_icon = StatusLabel(None, 'Joystick:', Qt.yellow, 8)
            self.pedals_status_icon = StatusLabel(None, 'Pedals:', Qt.yellow, 8)
            self.collective_status_icon = StatusLabel(None, 'Collective:', Qt.yellow, 8)

            self.master_status_icon.clicked.connect(self.change_config_scope)
            self.joystick_status_icon.clicked.connect(self.change_config_scope)
            self.pedals_status_icon.clicked.connect(self.change_config_scope)
            self.collective_status_icon.clicked.connect(self.change_config_scope)

            self.instance_status_row.addWidget(self.master_status_icon)
            self.instance_status_row.addWidget(self.joystick_status_icon)
            self.instance_status_row.addWidget(self.pedals_status_icon)
            self.instance_status_row.addWidget(self.collective_status_icon)
            self.joystick_status_icon.hide()
            self.pedals_status_icon.hide()
            self.collective_status_icon.hide()

            self.instance_status_row.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
            self.instance_status_row.setSpacing(10)

            layout.addLayout(self.instance_status_row)

        version_row_layout = QHBoxLayout()
        self.version_label = QLabel()

        if _release:
            status_text = f"Release Version {version}"
        else:
            status_text = "UNKNOWN"

        self.version_label.setText(f'Version Status: {status_text}')
        self.version_label.setOpenExternalLinks(True)

        global dev_firmware_version
        self.firmware_label = QLabel()
        self.firmware_label.setText(f'Rhino Firmware: {dev_firmware_version}')

        self.version_label.setAlignment(Qt.AlignLeft)
        self.firmware_label.setAlignment(Qt.AlignRight)
        version_row_layout.addWidget(self.version_label)
        version_row_layout.addWidget(self.firmware_label)

        version_row_layout.setAlignment(Qt.AlignBottom)
        layout.addLayout(version_row_layout)

        # self.test_button = QPushButton("SEND TEST MESSAGE")
        # self.test_button.clicked.connect(lambda: send_test_message())

        # layout.addWidget(self.test_button)

        central_widget.setLayout(layout)

        # Load Stored Geomoetry
        self.load_main_window_geometry()
        shortcut = QShortcut(QKeySequence('Alt+D'), self)
        shortcut.activated.connect(self.add_debug_menu)
        try:
            if utils.get_reg("debug"):
                self.add_debug_menu()
        except:
            pass

    def test_function(self):
        self.set_scrollbar(400)

    def refresh_telem_status(self):
        dcs_enabled = G.system_settings.get('enableDCS')
        il2_enabled = G.system_settings.get('enableIL2')
        msfs_enabled = G.system_settings.get('enableMSFS')
        xplane_enabled = G.system_settings.get('enableXPLANE')

        # Convert True/False to "enabled" or "disabled"
        dcs_status = "Enabled" if dcs_enabled else "Disabled"
        il2_status = "Enabled" if il2_enabled else "Disabled"
        msfs_status = "Enabled" if msfs_enabled else "Disabled"
        xplane_status = "Enabled" if xplane_enabled else "Disabled"

        self.lbl_telem_data = QLabel(
            f"Waiting for data...\n\n"
            f"DCS     : {dcs_status}\n"
            f"IL2     : {il2_status}\n"
            f"MSFS    : {msfs_status}\n"
            f"X-Plane : {xplane_status}\n\n"
            "Enable or Disable in System -> System Settings"
        )

    def add_debug_menu(self):
        # debug mode
        for action in self.menu.actions():
            if action.text() == "Debug":
                return
        self.debug_menu = self.menu.addMenu("Debug")
        aircraft_picker_action = QAction('Enable Manual Aircraft Selection', self)
        aircraft_picker_action.triggered.connect(lambda: self.toggle_settings_window(dbg=True))
        self.debug_menu.addAction(aircraft_picker_action)

        self.teleplot_action = QAction("Teleplot Setup", self)
        def do_open_teleplot_setup_dialog():
            dialog = TeleplotSetupDialog(self)
            dialog.exec_()
        self.teleplot_action.triggered.connect(do_open_teleplot_setup_dialog)
        self.debug_menu.addAction(self.teleplot_action)

        self.show_simvar_action = QAction("Show simvar in telem window", self)
        def do_toggle_simvar_telemetry():
            if self.show_simvars:
                self.show_simvars = False
                self.show_simvar_action.setChecked(False)
            else:
                self.show_simvars = True
                self.show_simvar_action.setChecked(True)
        self.show_simvar_action.triggered.connect(do_toggle_simvar_telemetry)
        self.show_simvar_action.setCheckable(True)
        self.debug_menu.addAction(self.show_simvar_action)

        self.configurator_settings_action = QAction('Configurator Gain Override', self)
        def do_open_configurator_dialog():
            dialog = ConfiguratorDialog(self)
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        self.configurator_settings_action.triggered.connect(do_open_configurator_dialog)
        self.debug_menu.addAction(self.configurator_settings_action)

        self.sc_overrides_action = QAction('SimConnect Overrides Editor', self)
        def do_open_sc_override_dialog():
            dialog = SCOverridesEditor(self)
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        # dialog.exec_()
        self.sc_overrides_action.triggered.connect(do_open_sc_override_dialog)
        self.debug_menu.addAction(self.sc_overrides_action)
        
        self.test_update = QAction('Test updater', self)
        def do_test_update():
            self._update_available = True
            self.perform_update()
        self.test_update.triggered.connect(do_test_update)
        self.debug_menu.addAction(self.test_update)
        

        if G._master_instance:
            self.custom_userconfig_action = QAction("Load Custom User Config", self)
            self.custom_userconfig_action.triggered.connect(lambda: load_custom_userconfig())
            self.debug_menu.addAction(self.custom_userconfig_action)

    def set_scrollbar(self, pos):
        self.settings_area.verticalScrollBar().setValue(pos)

    def process_error_signal(self, message):
        # Placeholder function to process signal generated from anywhere using utils.signal_emitter
        pass

    def update_child_status(self, device, status):
        status_icon_name = f'{device}_status_icon'
        status_icon = getattr(self, status_icon_name, None)

        if status_icon is not None and status == 'ACTIVE':
            status_icon.set_dot_color(Qt.green)
        if status_icon is not None and status == 'TIMEOUT':
            status_icon.set_dot_color(Qt.red)



    def show_child_settings(self):
        G.ipc_instance.send_broadcast_message("SHOW SETTINGS")

    def reset_user_config(self):
        ans = QMessageBox.warning(self, "Caution", "Are you sure you want to proceed?  All contents of your user configuration will be erased\n\nA backup of the configuration will be generated containing the current timestamp.", QMessageBox.Ok | QMessageBox.Cancel)

        if ans == QMessageBox.Ok:
            try:
                # Get the current timestamp
                timestamp = datetime.now().strftime('%Y%m%d_%H%M')

                # Create the backup file name with the timestamp
                backup_file = os.path.join(G.userconfig_rootpath, ('userconfig_' + timestamp + '.bak'))

                # Copy the file to the backup file
                shutil.copy(G.globals.userconfig_path, backup_file)

                logging.debug(f"Backup created: {backup_file}")

            except Exception as e:
                logging.error(f"Error creating backup: {str(e)}")
                QMessageBox.warning(self, 'Error', f'There was an error resetting the config:\n\n{e}')
                return

            os.remove(G.userconfig_path)
            utils.create_empty_userxml_file(G.userconfig_path)

            logging.info(f"User config Reset:  Backup file created: {backup_file}")
        else:
            return

    def update_log_widget(self, log_line):
        cursor = self.log_widget.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(log_line)
        self.log_widget.setTextCursor(cursor)
        self.log_widget.ensureCursorVisible()

    def clear_log_widget(self):
        self.log_widget.clear()


    def show_device_logo(self):
        self.devicetype_label.show()

    def enable_device_logo_click(self, state):
        self.devicetype_label.setClickable(state)
        self.devicetype_label.setStyleSheet(
            "QLabel {"
            # "   background-color: #4CAF50;"  # Set background color
            # "   color: white;"               # Set text color
            # "   padding: 1px;"               # Add padding
            "   border-radius: 4px;"         # Add rounded corners
            # "   border: 2px solid #808080;"  # Add border
            "}"
            "QLabel:hover {"
            "   background-color: #DCDCDC;"  # Change background color on hover
            "}"
        )

    def device_logo_click_event(self):
        # print("External function executed on label click")
        # print(self._current_config_scope)
        def check_instance(name): return name in G._launched_instances or G.device_type == name
        if self._current_config_scope == 'joystick':
            if check_instance("pedals"):
                self.change_config_scope(2)
            elif check_instance("collective"):
                self.change_config_scope(3)
        elif self._current_config_scope == 'pedals':
            if check_instance("collective"):
                self.change_config_scope(3)
            elif check_instance("joystick"):
                self.change_config_scope(1)
        elif self._current_config_scope == 'collective':
            if check_instance("joystick"):
                self.change_config_scope(1)
            elif check_instance("pedals"):
                self.change_config_scope(2)

    def update_version_result(self, vers, url):
        self.latest_version = vers
        self.latest_version_url = url

        is_exe = getattr(sys, 'frozen', False)

        if vers == "uptodate":
            status_text = "Up To Date"
            self.update_action.setDisabled(True)
            self.version_label.setText(f'Version Status: {status_text}')
        elif vers == "error":
            status_text = "UNKNOWN"
            self.version_label.setText(f'Version Status: {status_text}')
        elif vers == 'dev':
            if is_exe:
                self.version_label.setText(f'Version Status: <b>Development Build</b>')
            else:
                self.version_label.setText(f'Version Status: <b>Non release - Modified Source</b>')

        elif vers == 'needsupdate':
            self.version_label.setText(f'Version Status: <b>Out of Date Source - Git pull needed</b>')

        else:
            # print(_update_available)
            self._update_available = True
            logging.info(f"<<<<Update available - new version={vers}>>>>")

            status_text = f"New version <a href='{url}'><b>{vers}</b></a> is available!"
            self.update_action.setDisabled(False)
            self.update_action.setText("Install Latest TelemFFB")
            self.version_label.setToolTip(url)
            self.version_label.setText(f'Version Status: {status_text}')

        self.perform_update(auto=True)

    def change_config_scope(self, _arg):

        if isinstance(_arg, str):
            if 'joystick' in _arg: arg = 1
            elif 'pedals' in _arg: arg = 2
            elif 'collective' in _arg: arg = 3
        else:
            arg = _arg

        types = {
            1 : "joystick",
            2 : "pedals",
            3 : "collective"
        }

        xmlutils.update_vars(types[arg], G.userconfig_path, G.defaults_path)
        self._current_config_scope = types[arg]

        pixmap = QPixmap(utils.get_device_logo(self._current_config_scope))
        self.devicetype_label.setPixmap(pixmap)
        self.devicetype_label.setFixedSize(pixmap.width(), pixmap.height())

        if G._master_instance:
            self.effect_lbl.setText(f'Active Effects for: {self._current_config_scope}')

        # for file in os.listdir(log_folder):
        #     if file.endswith(self._current_config_scope + '_' + current_log_ts):
        #         self.log_tail_thread.change_log_file(os.path.join(log_folder, file))
        #         pass
        # log_tail_window.setWindowTitle(f"Log File Monitor ({self._current_config_scope})")

        self.update_settings()

    def test_sim_changed(self):
        models = xmlutils.read_models(self.test_sim.currentText())
        self.test_name.blockSignals(True)
        self.test_name.clear()
        self.test_name.addItems(models)
        self.test_name.blockSignals(False)

    def closeEvent(self, event):
        # Perform cleanup before closing the application
        if G._child_instance:
            self.hide()
            event.ignore()
        else:
            exit_application()



    def load_main_window_geometry(self):
        window_data = G.system_settings.get("WindowData")
        # print(window_data)
        if window_data is not None:
            window_data_dict = json.loads(window_data)
        else:
            window_data_dict = {}
        # print(window_data_dict)
        load_geometry = G.system_settings.get('saveWindow', True)
        load_tab = G.system_settings.get('saveLastTab', True)

        if load_tab:
            tab = window_data_dict.get('Tab', 0)
            self.tab_sizes = window_data_dict.get('TabSizes', self.default_tab_sizes)
            self.tab_widget.setCurrentIndex(tab)
            self.switch_window_view(tab)
            h = self.tab_sizes[str(tab)]['height']
            w = self.tab_sizes[str(tab)]['width']
            self.resize(w, h)

        if load_geometry:
            win_pos = window_data_dict.get('WindowPosition', {})
            win_x = win_pos.get('x', 100)
            win_y = win_pos.get('y', 100)
            self.move(win_x, win_y)

    def force_sim_aircraft(self):
        G.settings_mgr.current_sim = self.test_sim.currentText()
        G.settings_mgr.current_aircraft_name = self.test_name.currentText()
        self.settings_layout.expanded_items.clear()
        self.monitor_widget.hide()
        self.settings_layout.reload_caller()


    def open_system_settings_dialog(self):
        try:
            dialog = SystemSettingsDialog(self)
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        except:
            traceback.print_exc()
        # dialog.exec_()

    def update_settings(self):
        self.settings_layout.reload_caller()

    # def show_sub_menu(self):
    #     edit_button = self.sender()
    #     self.sub_menu.popup(edit_button.mapToGlobal(edit_button.rect().bottomLeft()))

    def open_url(self, url):

        # Open the URL
        QDesktopServices.openUrl(QUrl(url))

    def reset_all_effects(self):
        result = QMessageBox.warning(self, "Are you sure?", "*** Only use this if you have effects which are 'stuck' ***\n\n  Proceeding will result in the destruction"
                                                            " of any effects which are currently being generated by the simulator and may result in requiring a restart of"
                                                            " the sim or a new session.\n\n~~ Proceed with caution ~~", QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)

        if result == QMessageBox.Ok:
            try:
                HapticEffect.device.resetEffects()
            except Exception as error:
                pass



    def create_colored_icon(self, color, size):
        # Create a QPixmap with the specified color and size
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)
        painter.end()

        return pixmap

    def create_paused_icon(self, color, size):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)

        # Draw two vertical lines for the pause icon
        line_length = int(size.width() / 3)
        line_width = 1
        line1_x = int((size.width() / 2) - 2)
        line2_x = int((size.width() / 2) + 2)
        line_y = int((size.height() - line_length) / 2)

        painter.setPen(QColor(Qt.white))
        painter.drawLine(line1_x, line_y, line1_x, line_y + line_length)
        painter.drawLine(line2_x, line_y, line2_x, line_y + line_length)

        painter.end()

        return pixmap

    def create_x_icon(self, color, size):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)

        # Draw two vertical lines for the pause icon
        line_length = int(size.width() / 3)
        line_width = 1
        line1_x = int((size.width() / 2) - 2)
        line2_x = int((size.width() / 2) + 2)
        line_y = int((size.height() - line_length) / 2)

        painter.setPen(QColor(Qt.white))
        painter.drawLine(line1_x, line_y, line2_x, line_y + line_length)
        painter.drawLine(line2_x, line_y, line1_x, line_y + line_length)

        painter.end()

        return pixmap


    def toggle_settings_window(self, dbg=False):
        try:
            modifiers = QApplication.keyboardModifiers()
            if ((modifiers & QtCore.Qt.ControlModifier) and (modifiers & QtCore.Qt.ShiftModifier)) or dbg:
                if self.test_craft_area.isVisible():
                    self.test_craft_area.hide()
                else:
                    self.test_craft_area.show()
            else:
                sm = G.settings_mgr
                if sm.isVisible():
                    sm.hide()
                else:
                    sm.move(self.x() + 50, self.y() + 100)
                    sm.show()
                    logging.debug(f"# toggle settings window   sim:'{sm.current_sim}' ac:'{sm.current_aircraft_name}'")
                    if sm.current_aircraft_name != '':
                        sm.currentmodel_click()
                    else:
                        sm.update_table_on_class_change()

                    if sm.current_sim == '' or sm.current_sim == 'nothing':
                        sm.update_table_on_sim_change()
        except:
            traceback.print_exc()

    def show_user_model_dialog(self):
        mprint("show_user_model_dialog")
        current_aircraft = self.cur_craft.text()
        dialog = UserModelDialog(G.settings_mgr.current_sim, current_aircraft, G.settings_mgr.current_class, self)
        result = dialog.exec_()
        if result == QtWidgets.QDialog.Accepted:
            # Handle accepted
            new_aircraft = dialog.tb_current_aircraft.currentText()
            if new_aircraft == current_aircraft:
                qm = QMessageBox()
                ret = qm.question(self, 'Create Match Pattern', "Are you sure you want to match on the\nfull aircraft name and not a search pattern?", qm.Yes | qm.No)
                if ret == qm.No:
                    return
            new_combo_box_value = dialog.combo_box.currentText()
            pat_to_clone = dialog.models_combo_box.currentText()
            if pat_to_clone == '':
                logging.info(f"New: {new_aircraft} {new_combo_box_value}")
                xmlutils.write_models_to_xml(G.settings_mgr.current_sim, new_aircraft, new_combo_box_value, 'type', None)
            else:
                logging.info(f"Cloning: {pat_to_clone} as {new_aircraft}")
                xmlutils.clone_pattern(G.settings_mgr.current_sim, pat_to_clone, new_aircraft)
        else:
            # Handle canceled
            pass

    def update_from_menu(self):
        if self.perform_update(auto=False):
            QCoreApplication.instance().quit()

    def init_sim_indicators(self, sims, settings_dict):
        label_icons = {
            'DCS': self.dcs_label_icon,
            'IL2': self.il2_label_icon,
            'MSFS': self.msfs_label_icon,
            'XPLANE': self.xplane_label_icon,

        }
        enable_color = QColor(255, 255, 0)
        disable_color = QColor(128, 128, 128)
        enable_icon = self.create_colored_icon(enable_color, self.icon_size)
        disable_icon = self.create_x_icon(disable_color, self.icon_size)
        for sim in sims:
            state = settings_dict.get(f"enable{sim}")
            lb = label_icons[sim]
            if state:
                lb.setPixmap(enable_icon)
                lb.setToolTip("Sim is enabled, no telemetry yet received")

            else:
                lb.setPixmap(disable_icon)
                lb.setToolTip("Sim is disabled")

    def update_sim_indicators(self, source, paused=False, error=False):
        label_icons = {
            'DCS': self.dcs_label_icon,
            'IL2': self.il2_label_icon,
            'MSFS2020': self.msfs_label_icon,
            'XPLANE': self.xplane_label_icon,
        }
        active_color = QColor(0, 255, 0)
        paused_color = QColor(0, 0, 255)
        error_color = Qt.red
        active_icon = self.create_colored_icon(active_color, self.icon_size)
        active_tooltip = "Sim is running, receiving telemetry"
        paused_icon = self.create_paused_icon(paused_color, self.icon_size)
        paused_tooltip = "Telemetry stopped or sim is paused"
        error_icon = self.create_x_icon(error_color, self.icon_size)
        error_tooltip = "Error condition: check log"
        lb = label_icons[source]
        if error:
            lb.setPixmap(error_icon)
            lb.setToolTip(error_tooltip)
        elif not paused:
            lb.setPixmap(active_icon)
            lb.setToolTip(active_tooltip)
        elif paused:
            lb.setPixmap(paused_icon)
            lb.setToolTip(paused_tooltip)

    def switch_window_view(self, index):
        previous_index = self.current_tab_index
        # Get window geometry and store as the geometry for the previous index for later recall
        self.tab_sizes[str(previous_index)]['height'] = self.height()
        self.tab_sizes[str(previous_index)]['width'] = self.width()

        if index == 0:  # Monitor Tab
            self.current_tab_index = 0
            try:
                h = self.tab_sizes[str(index)]['height']
                w = self.tab_sizes[str(index)]['width']
                self.resize(int(w), int(h))
            except: pass

        elif index == 1:  # Settings Tab
            self.current_tab_index = 1
            modifiers = QApplication.keyboardModifiers()
            if (modifiers & QtCore.Qt.ControlModifier) and (modifiers & QtCore.Qt.ShiftModifier):
                self.cb_joystick.setVisible(True)
                self.cb_pedals.setVisible(True)
                self.cb_collective.setVisible(True)
                match G.device_type:
                    case 'joystick':
                        self.cb_joystick.setChecked(True)
                    case 'pedals':
                        self.cb_pedals.setChecked(True)
                    case 'collective':
                        self.cb_collective.setChecked(True)

            try:
                h = self.tab_sizes[str(index)]['height']
                w = self.tab_sizes[str(index)]['width']
                self.resize(int(w), int(h))
            except:
                pass

        # elif index == 2:  # Log Tab
        #     self.current_tab_index = 2
        #     try:
        #         h = self.tab_sizes[str(index)]['height']
        #         w = self.tab_sizes[str(index)]['width']
        #         self.resize(int(w), int(h))
        #     except: pass

        elif index == 2:  # Hide Tab
            self.current_tab_index = 2

            self.resize(0, 0)

    def interpolate_color(self, color1, color2, value):
        # Ensure value is between 0 and 1
        value = max(0.0, min(1.0, value))

        # Extract individual color components
        r1, g1, b1, a1 = color1.getRgb()
        r2, g2, b2, a2 = color2.getRgb()

        # Interpolate each color component
        r = int(r1 + (r2 - r1) * value)
        g = int(g1 + (g2 - g1) * value)
        b = int(b1 + (b2 - b1) * value)
        a = int(a1 + (a2 - a1) * value)

        # Create and return the interpolated color
        return QColor(r, g, b, a)

    def update_telemetry(self, datadict: dict):

        data = OrderedDict(sorted(datadict.items()))  # Alphabetize telemetry data
        keys = data.keys()
        try:
            # use ordereddict and move some telemetry to the top
            # Items to move to the beginning (reverse order)
            if 'SimconnectCategory' in keys: data.move_to_end('SimconnectCategory', last=False)
            if 'AircraftClass' in keys: data.move_to_end('AircraftClass', last=False)
            if 'src' in keys: data.move_to_end('src', last=False)
            if 'N' in keys: data.move_to_end('N', last=False)
            if 'FFBType' in keys: data.move_to_end('FFBType', last=False)
            if 'perf' in keys: data.move_to_end('perf', last=False)
            if 'avgFrameTime' in keys: data.move_to_end('avgFrameTime', last=False)
            if 'maxFrameTime' in keys: data.move_to_end('maxFrameTime', last=False)
            if 'frameTimes' in keys: data.move_to_end('frameTimes', last=False)
            if 'T' in keys: data.move_to_end('T', last=False)

            # Items to move to the end
        except:
            pass

        try:

            items = ""
            for k, v in data.items():
                # check for msfs and debug mode (alt-d pressed), change to simvar name
                if self.show_simvars:
                    if data["src"] == "MSFS2020":
                        s = G.telem_manager.simconnect.get_var_name(k)
                        # s = simvarnames.get_var_name(k)
                        if s is not None:
                            k = s
                if type(v) == float:
                    items += f"{k}: {v:.3f}\n"
                else:
                    if isinstance(v, list):
                        v = "[" + ", ".join([f"{x:.3f}" if not isinstance(x, str) else x for x in v]) + "]"
                    items += f"{k}: {v}\n"
            active_effects = ""
            active_settings = []

            if G._master_instance and self._current_config_scope != G.device_type:
                dev = self._current_config_scope
                active_effects = G.ipc_instance._ipc_telem_effects.get(f'{dev}_active_effects', '')
                active_settings = G.ipc_instance._ipc_telem_effects.get(f'{dev}_active_settings', [])
            else:
                for key in effects.dict.keys():
                    if effects[key].started:
                        descr = utils.EffectTranslator.get_translation(key)[0]
                        settingname = utils.EffectTranslator.get_translation(key)[1]
                        if descr not in active_effects:
                            active_effects = '\n'.join([active_effects, descr])
                        if settingname not in active_settings and settingname != '':
                            active_settings.append(settingname)

            if G.args.child:
                child_effects = str(effects.dict.keys())
                if len(child_effects):
                    G.ipc_instance.send_ipc_effects(active_effects, active_settings)

            window_mode = self.tab_widget.currentIndex()
            # update slider colors
            pct_max_a = data.get('_pct_max_a', 0)
            pct_max_e = data.get('_pct_max_e', 0)
            pct_max_r = data.get('_pct_max_r', 0)
            qcolor_green = QColor("#17c411")
            qcolor_grey = QColor("grey")
            if window_mode == 1:
                sliders = self.findChildren(NoWheelSlider)
                for my_slider in sliders:
                    slidername = my_slider.objectName().replace('sld_', '')
                    my_slider.blockSignals(True)

                    if slidername == 'max_elevator_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_e)
                        my_slider.setHandleColor(new_color.name())
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    if slidername == 'max_aileron_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_a)
                        my_slider.setHandleColor(new_color.name())
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    if slidername == 'max_rudder_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_r)
                        my_slider.setHandleColor(new_color.name())
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    for a_s in active_settings:
                        if bool(re.search(a_s, slidername)):
                            my_slider.setHandleColor("#17c411")
                            break
                        else:
                            my_slider.setHandleColor(vpf_purple)
                    my_slider.blockSignals(False)

            is_paused = max(data.get('SimPaused', 0), data.get('Parked', 0))
            error_cond = data.get('error', 0)
            if error_cond:
                self.update_sim_indicators(data.get('src'), error=True)
            else:
                self.update_sim_indicators(data.get('src'), paused=is_paused)

            shown_pattern = G.settings_mgr.current_pattern
            if G.settings_mgr.current_pattern == '' and data.get('N', '') != '':
                shown_pattern = 'Using defaults'
                self.new_craft_button.show()
            else:
                self.new_craft_button.hide()

            self.cur_craft.setText(data['N'])
            self.cur_pattern.setText(f'Matched: <span style="font-family: Consolas, monospace;font-size: 14px">"{shown_pattern}"</span> ')

            if window_mode == 0:
                self.lbl_telem_data.setText(items)
            # elif window_mode == self.effect_monitor_radio:
                self.lbl_effects_data.setText(active_effects)

        except Exception as e:
            traceback.print_exc()

    def perform_update(self, auto=True):
        if _release:
            return False

        ignore_auto_updates = G.system_settings.get('ignoreUpdate', False)
        if not auto:
            ignore_auto_updates = False
        update_ans = QMessageBox.No
        proceed_ans = QMessageBox.Cancel
        try:
            updater_execution_path = os.path.join(utils.get_script_path(), 'updater.exe')
            if os.path.exists(updater_execution_path):
                os.remove(updater_execution_path)
        except Exception as e:
            logging.error(f'Error in perform_update: {e}')
        is_exe = getattr(sys, 'frozen', False)  # TODO: Make sure to swap these comment-outs before build to commit - this line should be active, next line should be commented out
        # is_exe = True
        if is_exe and self._update_available and not ignore_auto_updates and not G._child_instance:
            # vers, url = utils.fetch_latest_version()
            update_ans = QMessageBox.Yes
            if auto:
                update_ans = QMessageBox.information(self, "Update Available!!",
                                                     f"A new version of TelemFFB is available ({self.latest_version}).\n\nWould you like to automatically download and install it now?\n\nYou may also update later from the Utilities menu, or the\nnext time TelemFFB starts.\n\n~~ Note ~~ If you no longer wish to see this message on startup,\nyou may enable `ignore_auto_updates` in your user config.\n\nYou will still be able to update via the Utilities menu",
                                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

            if update_ans == QMessageBox.Yes:
                proceed_ans = QMessageBox.information(self, "TelemFFB Updater",
                                                      f"TelemFFB will now exit and launch the updater.\n\nOnce the update is complete, TelemFFB will restart.\n\n~~ Please Note~~~  The primary `config.ini` file will be overwritten.  If you\nhave made changes to `config.ini`, please back up the file or move the modifications to a user config file before upgrading.\n\nPress OK to continue",
                                                      QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)

            if proceed_ans == QMessageBox.Ok:
                updater_execution_path = os.path.join(utils.get_script_path(), 'updater.exe')
                shutil.copy(sys.argv[0], updater_execution_path)

                # Copy the updater executable with forced overwrite

                call = [updater_execution_path, "--current_version", version] + sys.argv[1:]
                subprocess.Popen(call, cwd=utils.get_install_path())
                if auto:
                    for child_widget in self.findChildren(QMessageBox):
                        child_widget.reject()
                    QTimer.singleShot(250, lambda: exit_application())
                else:
                    return True

        return False





def exit_application():
    # Perform any cleanup or save operations here
    save_main_window_geometry()
    QCoreApplication.instance().quit()


def send_test_message():
    if G.ipc_instance.running:
        if G._master_instance:
            G.ipc_instance.send_broadcast_message("TEST MESSAGE TO ALL")
        else:
            G.ipc_instance.send_message("TEST MESSAGE")


    # sys_out = {}
    # for key, value in sys_dict.items():
    #     reg_key = map_dict.get(key, None)
    #     if reg_key is None:
    #         logging.error(f"System Setting conversion error: '{key}' is not a valid setting!")
    #         continue
    #     sys_out[key] = value
    # out_val = json.dumps(sys_out)
    # utils.set_reg('Sys', out_val)
    # pass


def restart_sims():
    sim_list = ['DCS', 'MSFS', 'IL2', 'XPLANE']
    sys_settings = G.system_settings
    stop_sims()
    init_sims()
    G.main_window.init_sim_indicators(sim_list, sys_settings)


def init_sims():
    global dcs_telem, il2_telem, sim_connect_telem, xplane_telem

    xplane_enabled = G.system_settings.get('enableXPLANE', False)

    xplane_telem = NetworkThread(G.telem_manager, host='', port=34390)
    # xplane_enabled = G.system_settings.get('enableXPLANE', False)
    if xplane_enabled or G.args.sim == 'XPLANE':
        if not G._child_instance and G.system_settings.get('validateXPLANE', False):
            xplane_path = G.system_settings.get('pathXPLANE', '')
            utils.install_xplane_plugin(xplane_path, G.main_window)
        logging.info("Starting XPlane Telemetry Listener")
        xplane_telem.start()

    dcs_telem = NetworkThread(G.telem_manager, host="", port=34380)
    # dcs_enabled = utils.sanitize_dict(config["system"]).get("dcs_enabled", None)
    dcs_enabled = G.system_settings.get('enableDCS', False)
    if dcs_enabled or G.args.sim == "DCS":
        # check and install/update export lua script
        if not G._child_instance:
            utils.install_export_lua(G.main_window)
        logging.info("Starting DCS Telemetry Listener")
        dcs_telem.start()

    # il2_port = utils.sanitize_dict(config["system"]).get("il2_telem_port", 34385)
    il2_port = int(G.system_settings.get('portIL2', 34385))
    # il2_path = utils.sanitize_dict(config["system"]).get("il2_path", 'C: \\Program Files\\IL-2 Sturmovik Great Battles')
    il2_path = G.system_settings.get('pathIL2', 'C:\\Program Files\\IL-2 Sturmovik Great Battles')
    # il2_validate = utils.sanitize_dict(config["system"]).get("il2_cfg_validation", True)
    il2_validate = G.system_settings.get('validateIL2', True)
    il2_telem = NetworkThread(G.telem_manager, host="", port=il2_port, telem_parser=IL2Manager())

    # il2_enabled = utils.sanitize_dict(config["system"]).get("il2_enabled", None)
    il2_enabled = G.system_settings.get('enableIL2', False)

    if il2_enabled or G.args.sim == "IL2":
        if not G._child_instance:
            if il2_validate:
                utils.analyze_il2_config(il2_path, port=il2_port, window=G.main_window)
            else:
                logging.warning(
                    "IL2 Config validation is disabled - please ensure the IL2 startup.cfg is configured correctly")
        logging.info("Starting IL2 Telemetry Listener")
        il2_telem.start()

    sim_connect_telem = SimConnectSock(G.telem_manager)
    msfs = G.system_settings.get('enableMSFS', False)

    try:
        # msfs = utils.sanitize_dict(config["system"]).get("msfs_enabled", None)
        logging.debug(f"MSFS={msfs}")
        if msfs or G.args.sim == "MSFS":
            logging.info("MSFS Enabled:  Starting Simconnect Manager")
            sim_connect_telem.start()
            aircrafts_msfs_xp.Aircraft.set_simconnect(sim_connect_telem)

    except:
        logging.exception("Error loading MSFS enable flag from config file")

    dcs_status = "Enabled" if dcs_enabled else "Disabled"
    il2_status = "Enabled" if il2_enabled else "Disabled"
    msfs_status = "Enabled" if msfs else "Disabled"

    G.main_window.refresh_telem_status()

def stop_sims():
    xplane_telem.quit()
    dcs_telem.quit()
    il2_telem.quit()
    sim_connect_telem.quit()

# this needs a better solution
G.stop_sims = stop_sims
G.init_sims = init_sims




def launch_children():
    if not G.system_settings.get('autolaunchMaster', False) or G.args.child or not G._master_instance:
        return False

    master_port = f"6{G.device_usbpid}"
    try:

        def check_launch_instance(dev_type :str):
            Dev_type = dev_type.capitalize()
            if G.system_settings.get(f'autolaunch{Dev_type}', False) and G.device_type != dev_type:
                usbpid = G.system_settings.get(f'pid{Dev_type}', '2055')
                usb_vidpid = f"FFFF:{usbpid}"
            
                args = [sys.argv[0], '-D', usb_vidpid, '-t', dev_type, '--child', '--masterport', master_port]
                if sys.argv[0].endswith(".py"): # insert python interpreter if we launch ourselves as a script
                    args.insert(0, sys.executable)

                if G.system_settings.get(f'startMin{Dev_type}', False): args.append('--minimize')
                if G.system_settings.get(f'startHeadless{Dev_type}', False): args.append('--headless')

                logging.info(f"Auto-Launch: starting instance: {args}")
                subprocess.Popen(args)
                G._launched_instances.append(dev_type)
                G._child_ipc_ports.append(int(f"6{usbpid}"))

        check_launch_instance("joystick")
        check_launch_instance("pedals")
        check_launch_instance("collective")

    except Exception as e:
        logging.error(f"Error during Auto-Launch sequence: {e}")
    return True


def main():
    # TODO: Avoid globals
    global dev_firmware_version
    global dev_serial
    global dev
    global _release
    global version
    global _legacy_override_file
    global _vpf_logo
    global _device_logo
    global dcs_telem, il2_telem, sim_connect_telem, xplane_telem

    app = QApplication(sys.argv)

    parser = argparse.ArgumentParser(description='Send telemetry data over USB')

    # Add destination telemetry address argument
    parser.add_argument('--teleplot', type=str, metavar="IP:PORT", default=None,
                        help='Destination IP:port address for teleplot.fr telemetry plotting service')

    parser.add_argument('-p', '--plot', type=str, nargs='+',
                        help='Telemetry item names to send to teleplot, separated by spaces')

    parser.add_argument('-D', '--device', type=str, help='Rhino device USB VID:PID', default=None)
    parser.add_argument('-r', '--reset', help='Reset all FFB effects', action='store_true')

    # Add config file argument, default config.ini
    parser.add_argument('-c', '--configfile', type=str, help='Config ini file (default config.ini)', default='config.ini')
    parser.add_argument('-o', '--overridefile', type=str, help='User config override file (default = config.user.ini', default='None')
    parser.add_argument('-s', '--sim', type=str, help='Set simulator options DCS|MSFS|IL2 (default DCS', default="None")
    parser.add_argument('-t', '--type', help='FFB Device Type | joystick (default) | pedals | collective', default=None)
    parser.add_argument('--headless', action='store_true', help='Run in headless mode')
    parser.add_argument('--child', action='store_true', help='Is a child instance')
    parser.add_argument('--masterport', type=str, help='master instance IPC port', default=None)

    parser.add_argument('--minimize', action='store_true', help='Minimize on startup')

    G.args = parser.parse_args()

    # script_dir = os.path.dirname(os.path.abspath(__file__))
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True) #enable highdpi scaling
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True) #use highdpi icons

    headless_mode = G.args.headless

    G._launched_joystick = False
    G._launched_pedals = False
    G._launched_collective = False
    G._launched_children = False
    G._child_ipc_ports = []
    G._master_instance = False
    G.ipc_instance = None
    G._child_instance = G.args.child

    G.system_settings = utils.SystemSettings()

    # _vpf_logo = os.path.join(script_dir, "image/vpforcelogo.png")
    _vpf_logo = ":/image/vpforcelogo.png"
    if G.args.device is None:
        master_rb = G.system_settings.get('masterInstance', 1)
        match master_rb:
            case 1:
                G.device_usbpid = G.system_settings.get('pidJoystick', "2055")
                G.device_type = 'joystick'
            case 2:
                G.device_usbpid = G.system_settings.get('pidPedals', "2055")
                G.device_type = 'pedals'
            case 3:
                G.device_usbpid = G.system_settings.get('pidCollective', "2055")
                G.device_type = 'collective'
            case _:
                G.device_usbpid = G.system_settings.get('pidJoystick', "2055")
                G.device_type = 'joystick'

        if not G.device_usbpid: # check empty string
            G.device_usbpid = '2055'

        G.device_usbvidpid = f"FFFF:{G.device_usbpid}"
        G.args.type = G.device_type
    else:
        if G.args.type is None:
            G.device_type = 'joystick'
            G.args.type = G.device_type
        else:
            G.device_type = str.lower(G.args.type)

        G.device_usbpid = G.args.device.split(":")[1]
        G.device_usbvidpid = G.args.device



    G.system_settings = utils.SystemSettings()

    G.args.sim = str.upper(G.args.sim)
    G.args.type = str.lower(G.args.type)

    # need to determine if someone has auto-launch enabled but has started an instance with -D
    # The 'masterInstance' reg key holds the radio button index of the configured master instance
    # 1=joystick, 2=pedals, 3=collective
    index_dict = {
        'joystick': 1,
        'pedals': 2,
        'collective': 3
    }
    master_index = G.system_settings.get('masterInstance', 1)
    if index_dict[G.device_type] == master_index:
        G._master_instance = True
    else:
        G._master_instance = False


    sys.path.insert(0, '')
    # sys.path.append('/simconnect')

    #################
    ################
    ###  Setting _release flag to true will disable all auto-updating and 'WiP' downloads server version checking
    ###  Set the version number to version tag that will be pushed to master repository branch
    _release = False  # Todo: Validate release flag!

    if _release:
        version = "vX.X.X"
    else:
        version = utils.get_version()

    min_firmware_version = 'v1.0.15'

    dev_serial = None
    vpf_purple = "#ab37c8"   # rgb(171, 55, 200)


    _legacy_override_file = None
    _legacy_config_file = utils.get_resource_path('config.ini')  # get from bundle (if exe) or local root if source

    if G.args.overridefile == 'None':
        _install_path = utils.get_install_path()

        # Need to determine if user is using default config.user.ini without passing the override flag:
        if os.path.isfile(os.path.join(_install_path, 'config.user.ini')):
            _legacy_override_file = os.path.join(_install_path, 'config.user.ini')

    else:
        if not os.path.isabs(G.args.overridefile):  # user passed just file name, construct absolute path from script/exe directory
            ovd_path = utils.get_resource_path(G.args.overridefile, prefer_root=True, force=True)
        else:
            ovd_path = G.args.overridefile  # user passed absolute path, use that

        if os.path.isfile(ovd_path):
            _legacy_override_file = ovd_path
        else:
            _legacy_override_file = ovd_path
            logging.warning(f"Override file {G.args.overridefile} passed with -o argument, but can not find the file for auto-conversion")


    G.defaults_path = utils.get_resource_path('defaults.xml', prefer_root=True)
    G.userconfig_rootpath = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB")
    G.userconfig_path = os.path.join(G.userconfig_rootpath, 'userconfig.xml')

    utils.create_empty_userxml_file(G.userconfig_path)


    if getattr(sys, 'frozen', False):
        appmode = 'Executable'
    else:
        appmode = 'Source'
    logging.info("**************************************")
    logging.info("**************************************")
    logging.info(f"*****    TelemFFB starting up from {appmode}:  Args= {G.args.__dict__}")
    logging.info("**************************************")
    logging.info("**************************************")
    if G.args.teleplot:
        logging.info(f"Using {G.args.teleplot} for plotting")
        utils.teleplot.configure(G.args.teleplot)

    def excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stdout.write(f"{AnsiColors.BRIGHT_REDBG}{AnsiColors.WHITE}{tb}{AnsiColors.END}")
        #QtWidgets.QApplication.quit()
        # or QtWidgets.QApplication.exit(0)
    sys.excepthook = excepthook

    app.setStyleSheet(
        """
            QCheckBox::indicator:checked { image: url(:/image/purplecheckbox.png); }
            QRadioButton::indicator:checked { image: url(:/image/rchecked.png);}
            
            QPushButton, #styledButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                  stop: 0 #ab37c8, stop: 0.4 #9a24b5,
                                                  stop: 0.5 #8e1da8, stop: 1.0 #ab37c8);
                border: 1px solid #6e1d6f;
                border-radius: 5px;
                padding: 3px;
                margin: 1px;
                color: white;
            }
            QPushButton:disabled {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                  stop: 0 #cccccc, stop: 0.4 #bbbbbb,
                                                  stop: 0.5 #C1ADC6, stop: 1.0 #cccccc);
                color: #666666;  /* Set the text color for disabled buttons */
                border: 1px solid #6e1d6f;
                border-radius: 5px;
                padding: 3px;
                margin: 1px;
            }
            QPushButton:hover, #styledButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                stop: 0 #d97ed1, stop: 0.4 #d483d4,
                                                stop: 0.5 #d992da, stop: 1.0 #e4a9e7);
            }

            QComboBox::down-arrow {
                image: url(:/image/down-down.png);
            }

            QComboBox QAbstractItemView {
                border: 2px solid darkgray;
                selection-background-color: #ab37c8;
            }
            QLineEdit {
                selection-background-color: #ab37c8;  /* Set the highlight color for selected text */
            }
            QPlainTextEdit {
                selection-background-color: #ab37c8;  /* Set the highlight color for selected text */
            }
            QSlider::handle:horizontal {
                background: #ab37c8; /* Set the handle color */
                border: 1px solid #565a5e;
                width: 16px;  /* Adjusted handle width */
                height: 20px;  /* Adjusted handle height */
                border-radius: 5px;  /* Adjusted border radius */
                margin-top: -5px;  /* Negative margin to overlap with groove */
                margin-bottom: -5px;  /* Negative margin to overlap with groove */
                margin-left: -1px;  /* Adjusted left margin */
                margin-right: -1px;  /* Adjusted right margin */
            }
            QSlider::handle:horizontal:disabled {
                background: #888888; /* Set the color of the handle when disabled */
            }
        """
    )


    G.log_window = LogWindow()
    init_logging(G.log_window.widget)
    G.log_window.pause_button.clicked.connect(sys.stdout.toggle_pause)

    xmlutils.update_vars(G.device_type, G.userconfig_path, G.defaults_path)
    try:
        G.settings_mgr = SettingsWindow(datasource="Global", device=G.device_type, userconfig_path=G.userconfig_path, defaults_path=G.defaults_path, system_settings=G.system_settings)
    except Exception as e:
        traceback.print_exc()
        logging.error(f"Error Reading user config file..")
        ans = QMessageBox.question(None, "User Config Error", "There was an error reading the userconfig.  The file is likely corrupted.\n\nDo you want to back-up the existing config and create a new default (empty) config?\n\nIf you chose No, TelemFFB will exit.")
        if ans == QMessageBox.Yes:
            # Get the current timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')

            # Create the backup file name with the timestamp
            backup_file = os.path.join(G.userconfig_rootpath, ('userconfig_' + os.environ['USERNAME'] + "_" + timestamp + '_corrupted.bak'))

            # Copy the file to the backup file
            shutil.copy(G.userconfig_path, backup_file)

            logging.debug(f"Backup created: {backup_file}")

            os.remove(G.userconfig_path)
            utils.create_empty_userxml_file(G.userconfig_path)

            logging.info(f"User config Reset:  Backup file created: {backup_file}")
            G.settings_mgr = SettingsWindow(datasource="Global", device=G.device_type, userconfig_path=G.userconfig_path, defaults_path=G.defaults_path, system_settings=G.system_settings)
            QMessageBox.information(None, "New Userconfig created", f"A backup has been created: {backup_file}\n")
        else:
            QCoreApplication.instance().quit()
            return



    logging.info(f"TelemFFB (version {version}) Starting")
    try:
        vid_pid = [int(x, 16) for x in G.device_usbvidpid.split(":")]
    except:
        pass

    devs = FFBRhino.enumerate()
    logging.info("Available Rhino Devices:")
    logging.info("-------")
    for dev in devs:

        logging.info(f"* {dev.vendor_id:04X}:{dev.product_id:04X} - {dev.product_string} - {dev.serial_number}")
        logging.info(f"* Path:{dev.path}")
        logging.info(f"*")

    logging.info("-------")


    try:
        dev = HapticEffect.open(vid_pid[0], vid_pid[1])  # try to open RHINO
        if G.args.reset:
            dev.resetEffects()
        dev_firmware_version = dev.get_firmware_version()
        dev_serial = dev.serial
        if dev_firmware_version:
            logging.info(f"Rhino Firmware: {dev_firmware_version}")
            minver = re.sub(r'\D', '', min_firmware_version)
            devver = re.sub(r'\D', '', dev_firmware_version)
            if devver < minver:
                QMessageBox.warning(None, "Outdated Firmware", f"This version of TelemFFB requires Rhino Firmware version {min_firmware_version} or later.\n\nThe current version installed is {dev_firmware_version}\n\n\n Please update to avoid errors!")

    except Exception as e:
        logging.exception("Exception")
        QMessageBox.warning(None, "Cannot connect to Rhino", f"Unable to open HID at {G.device_usbvidpid} for device: {G.device_type}\nError: {e}\n\nPlease open the System Settings and verify the Master\ndevice PID is configured correctly")
        dev_firmware_version = 'ERROR'

    # config = get_config()
    # ll = config["system"].get("logging_level", "INFO")
    ll = G.system_settings.get('logLevel', 'INFO')
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    logger = logging.getLogger()
    logger.setLevel(log_levels.get(ll, logging.DEBUG))
    logging.info(f"Logging level set to:{logging.getLevelName(logger.getEffectiveLevel())}")

    G.is_master_instance = launch_children()
    if G.is_master_instance:
        myport = int(f"6{G.device_usbpid}")
        G.ipc_instance = IPCNetworkThread(master=True, myport=myport, child_ports=G._child_ipc_ports)
        G.ipc_instance.child_keepalive_signal.connect(lambda device, status: G.main_window.update_child_status(device, status))
        G.ipc_instance.start()
        G._launched_children = True
    elif G.args.child:
        myport = int(f"6{G.device_usbpid}")
        G.ipc_instance = IPCNetworkThread(child=True, myport=myport, dstport=G.args.masterport)
        G.ipc_instance.exit_signal.connect(exit_application)
        G.ipc_instance.restart_sim_signal.connect(restart_sims)
        G.ipc_instance.show_signal.connect(lambda: G.main_window.show())
        G.ipc_instance.hide_signal.connect(lambda: G.main_window.hide())
        G.ipc_instance.showlog_signal.connect(lambda: G.log_window.show())
        G.ipc_instance.show_settings_signal.connect(lambda: G.main_window.open_system_settings_dialog())
        G.ipc_instance.start()

    G.main_window = MainWindow()

    # log_tail_window = LogTailWindow(window)

    if not headless_mode:
        if G.args.minimize:
            G.main_window.showMinimized()
        else:
            G.main_window.show()

    autoconvert_config(G.main_window, _legacy_config_file, _legacy_override_file)
    if not _release:
        th = utils.FetchLatestVersionThread()
        th.version_result_signal.connect(G.main_window.update_version_result)
        th.error_signal.connect(lambda error_message: print("Error in thread:", error_message))
        th.start()

    G.telem_manager = TelemManager()
    G.telem_manager.start()

    G.telem_manager.telemetryReceived.connect(G.main_window.update_telemetry)
    G.telem_manager.aircraftUpdated.connect(G.main_window.update_settings)

    init_sims()

    if G.is_master_instance:
        G.main_window.show_device_logo()
        G.main_window.enable_device_logo_click(True)
        current_title = G.main_window.windowTitle()
        new_title = f"** MASTER INSTANCE ** {current_title}"
        G.main_window.setWindowTitle(new_title)
        if "joystick" in G._launched_instances:
            G.main_window.joystick_status_icon.show()
        if "pedals" in G._launched_instances:
            G.main_window.pedals_status_icon.show()
        if "collective" in G._launched_instances:
            G.main_window.collective_status_icon.show()

    if not G.system_settings.get("pidJoystick", None):
        G.main_window.open_system_settings_dialog()

    G.telem_manager.telemetryTimeout.connect(lambda state: G.main_window.update_sim_indicators(G.telem_manager.getTelemValue("src"), state))

    utils.signal_emitter.error_signal.connect(G.main_window.process_error_signal)
    utils.signal_emitter.msfs_quit_signal.connect(restart_sims)

    # do some init in the background not blocking the main window first appearance
    def init_async():
        if G.system_settings.get('enableVPConfStartup', False):
            try:
                set_vpconf_profile(G.system_settings.get('pathVPConfStartup', ''), dev_serial)
            except:
                logging.error("Unable to set VPConfigurator startup profile")

        try:
            G.startup_configurator_gains = dev.getGains()
        except:
            logging.error("Unable to get configurator slider values from device")

    threading.Thread(target=init_async, daemon=True).start()

    app.exec_()

    if G.ipc_instance:
        G.ipc_instance.notify_close_children()
        G.ipc_instance.stop()

    stop_sims()
    G.telem_manager.quit()
    if G.system_settings.get('enableVPConfExit', False):
        try:
            set_vpconf_profile(G.system_settings.get('pathVPConfExit', ''), dev_serial)
        except:
            logging.error("Unable to set VPConfigurator exit profile")


def init_logging(log_widget : QPlainTextEdit):
    log_folder = os.path.join(os.environ['LOCALAPPDATA'], "VPForce-TelemFFB", 'log')
    
    sys.stdout = utils.OutLog(log_widget, sys.stdout)
    sys.stderr = utils.OutLog(log_widget, sys.stderr)

    if not os.path.exists(log_folder):
        os.makedirs(log_folder)

    date_str = datetime.now().strftime("%Y%m%d")

    logname = "".join(["TelemFFB", "_", G.device_usbvidpid.replace(":", "-"), '_', G.device_type, "_", date_str, ".log"])
    log_file = os.path.join(log_folder, logname)

    # Create a logger instance
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    logging.addLevelName(logging.DEBUG, f'{AnsiColors.GREEN}DEBUG{AnsiColors.END}')
    logging.addLevelName(logging.INFO, f'{AnsiColors.BLUE}INFO{AnsiColors.END}')
    logging.addLevelName(logging.ERROR, f'{AnsiColors.REDBG}{AnsiColors.WHITE}ERROR{AnsiColors.END}')
    logging.addLevelName(logging.WARNING, f'{AnsiColors.YELLOW}WARNING{AnsiColors.END}')

    # remove ansi escape strings
    class MyFormatter(logging.Formatter):
        def format(self, record):
            s = super().format(record)
            p = utils.parseAnsiText(s)
            return "".join([txt[0] for txt in p])
            
    # Create a formatter for the log messages
    fmt_string = f'{utils.AnsiColors.DARK_GRAY}%(asctime)s.%(msecs)03d - {G.device_type}{utils.AnsiColors.END} - %(levelname)s - %(message)s'
    formatter = logging.Formatter(fmt_string, datefmt='%Y-%m-%d %H:%M:%S')
    formatter_file = MyFormatter(fmt_string, datefmt='%Y-%m-%d %H:%M:%S')

    # Create a StreamHandler to log messages to the console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # Create a FileHandler to log messages to the log file
    file_handler = logging.FileHandler(log_file, mode='a')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter_file)

    # Add the handlers to the logger
    #logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # Create a list of keywords to filter
    log_filter_strings = [
        # "unrecognized Miscellaneous Unit in typefor(POSITION)",
        # "Unrecognized event AXIS_CYCLIC_LATERAL_SET",
        # "Unrecognized event AXIS_CYCLIC_LONGITUDINAL_SET",
        # "Unrecognized event ROTOR_AXIS_TAIL_ROTOR_SET",
        # "Unrecognized event AXIS_COLLECTIVE_SET",
    ]

    log_filter = LoggingFilter(log_filter_strings)

    console_handler.addFilter(log_filter)
    file_handler.addFilter(log_filter)

    logging.getLogger().handlers[0].setStream(sys.stdout)
    logging.getLogger().handlers[0].setFormatter(formatter)

    if not G.args.child:
        try:    # in case other instance tries doing at the same time
            utils.archive_logs(log_folder)
        except: pass


if __name__ == "__main__":
    main()
