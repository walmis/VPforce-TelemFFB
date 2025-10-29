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
import shutil
import subprocess
import sys
import time
import traceback
import winreg
from collections import OrderedDict
from datetime import datetime
from typing import override

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import QCoreApplication, Qt, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import (QColor, QCursor, QDesktopServices, QIcon,
                         QKeySequence, QPixmap, QFontMetrics, QAction, QShortcut, QFontDatabase, QFont)
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                             QComboBox, QFrame, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                             QPushButton, QScrollArea, QTabWidget,
                             QToolButton, QVBoxLayout, QWidget, QSpacerItem, QSizePolicy, QSystemTrayIcon, QMenu,
                             QDialog, QStatusBar, QSplitter)

import telemffb.globals as G
import telemffb.utils as utils
import telemffb.xmlutils as xmlutils
# from telemffb.config_utils import autoconvert_config
from telemffb.ConfiguratorDialog import ConfiguratorDialog
from telemffb.custom_widgets import ClickLogo, InstanceStatusRow, NoKeyScrollArea, NoWheelSlider, NoWheelNumberSlider, \
    SimStatusLabel, vpf_purple, AppStatusWidget, DetachedTabWindow, ExceptionStatusWidget
from telemffb.DevicePanel import DeviceIconPanel
from telemffb.ExceptionTracker import ExceptionViewerDialog
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.SCOverridesEditor import SCOverridesEditor
from telemffb.SettingsLayout import SettingsLayout
# from telemffb.UserModelDialog import UserModelDialog
from telemffb.NewAircraftWizard import NewAircraftWizard
from telemffb.telem.SimTelemListener import SimTelemListener
from telemffb.SystemSettingsDialog import SystemSettingsDialog
from telemffb.TeleplotSetupDialog import TeleplotSetupDialog
from telemffb.ProfileManager import ProfileManagerDialog, NewProfileDialog
from telemffb.utils import exit_application, HiDpiPixmap

class MainWindow(QMainWindow):
    
    def __init__(self):
        super().__init__()
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_notifications = {}
        self.new_craft_notification_sent = False
        self.error_state = False # True='error' key found in telem_data, False=clean telem_data
        self.error_clean_counter = 0 # counter to use as hysteresis for clearing error condition - not always 'error' from child instance on every loop
        self.telemetry_timed_out = True
        self.last_telemetry_refresh = utils.millis()
        self.show_simvars = False
        self.latest_version = None
        self._update_available = None
        self.show_new_craft_button = False
        self.profile_mgr_dialog = None
        self.all_offline_models = []


        """ Add font used for settngs area group labels """

        QFontDatabase.addApplicationFont(':/image/BlackOpsOne-Regular.ttf')

        # Get the absolute path of the script's directory
        # script_dir = os.path.dirname(os.path.abspath(__file__))
        doc_url = 'https://vpforcecontrols.com/downloads/VPforce_Rhino_Manual.pdf'
        if G.release_version:
            dl_url = 'https://github.com/walmis/VPforce-TelemFFB/releases'
        else:
            dl_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=D'

        # notes_url = os.path.join(script_dir, '_RELEASE_NOTES.txt')
        notes_url = utils.get_resource_path('_RELEASE_NOTES.txt')
        G.current_device_config_scope = G.device_type
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
            "2": {  # hide
                'height': 0,
                'width': 0,
            }
        }

        self.tab_sizes = self.default_tab_sizes

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
            case 'trimwheel':
                x_pos = 40
                y_pos = 30

        self.setGeometry(x_pos, y_pos, 530, 700)

        version = utils.get_version()
        if version:
            self.setWindowTitle(f"TelemFFB v2 ({G.device_type}) ({version})")
        else:
            self.setWindowTitle(f"TelemFFB v2")

        # Construct the absolute path of the icon file
        icon = QIcon(":/image/vpforceicon.png")

        self.setWindowIcon(icon)

        self.resize(530, 700)
        self.hidden_active = False
        # Create a layout for the main window
        layout = QVBoxLayout()
        notes_row_layout = QHBoxLayout()


        """ Create the menu bar """

        menubar = self.menuBar()
        self.menu = menubar
        assert self.menu is not None
        # Set the background color of the menu bar
        # "#ab37c8" is VPForce purple


        """ Add the "System" menu and its sub-option """

        system_menu = self.menu.addMenu('&System')

        system_settings_action = QAction('System Settings', self)
        system_settings_action.triggered.connect(self.open_system_settings_dialog)
        system_menu.addAction(system_settings_action)

        cfg_log_folder_action = QAction('Open Config/Log Directory', self)
        def do_open_cfg_dir():
            modifiers = QApplication.keyboardModifiers()
            if (modifiers & QtCore.Qt.KeyboardModifier.ControlModifier) and (modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier) and getattr(sys, 'frozen', False):
                os.startfile(getattr(sys, "_MEIPASS"), 'open')
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
                case 'trimwheel':
                    x_pos = 40
                    y_pos = 30
            self.setGeometry(x_pos, y_pos, 530, 700)

        reset_geometry.triggered.connect(do_reset_window_size)
        system_menu.addAction(reset_geometry)

        exit_app_action = QAction('Quit TelemFFB', self)
        exit_app_action.triggered.connect(exit_application)
        system_menu.addAction(exit_app_action)

        if G.master_instance:
            """
            Create profiles menu - only for Master Instance
            """
            self.profiles_menu = self.menu.addMenu('Profiles')

            self.profile_manager_action = QAction('Profile Manager...', self)
            self.profile_manager_action.triggered.connect(self.show_profile_manager)
            self.profiles_menu.addAction(self.profile_manager_action)

            self.offline_config_action = QAction(r'Offline Profile\Sim Default\Class Default Mode', self)
            self.offline_config_action.triggered.connect(lambda: self.toggle_offline_mode(True))
            self.profiles_menu.addAction(self.offline_config_action)


        """ Create the "Utilities" menu """

        utilities_menu = self.menu.addMenu('Utilities')

        # Add the "Reset" action to the "Utilities" menu
        reset_action = QAction('Reset All Effects', self)
        reset_action.triggered.connect(self.reset_all_effects)
        utilities_menu.addAction(reset_action)

        self.update_action = QAction('Install Latest TelemFFB', self)
        self.update_action.triggered.connect(self.update_from_menu)
        if not G.release_version:
            utilities_menu.addAction(self.update_action)
        self.update_action.setDisabled(True)

        download_action = QAction('Download Other Versions', self)
        download_action.triggered.connect(lambda: self.open_url(dl_url))
        utilities_menu.addAction(download_action)

        self.reset_user_config_action = QAction('Reset User Config', self)
        self.reset_user_config_action.triggered.connect(self.reset_user_config)
        utilities_menu.addAction(self.reset_user_config_action)

        def launch_vpconf():
            try:
                utils.launch_vpconf()
            except Exception as e:
                logging.error(f"Error launching VPforce Configurator: {e}")
                QMessageBox.critical(self, "Error", f"Error launching VPforce Configurator: {e}")
        self.vpconf_action = QAction("Launch VPforce Configurator", self)
        self.vpconf_action.triggered.connect(launch_vpconf)
        utilities_menu.addAction(self.vpconf_action)

        reload_action = QAction('Force Reload Aircraft (Ctrl+Shift+R)', self)
        reload_action.triggered.connect(self.force_reload_aircraft)
        utilities_menu.addAction(reload_action)

        if G.master_instance and G.system_settings.get('autolaunchMaster', 0):
            """
            Add Window menu to manage child instances if it is a master instance
            """
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

        if G.child_instance:
            """
            Add Child instance window menu
            """
            self.window_menu = self.menu.addMenu('Window')
            self.hide_window_action = QAction('Hide Window')
            def do_hide_window():
                try:
                    self.hide()
                except Exception as e:
                    logging.error(f"EXCEPTION: {e}")
            self.hide_window_action.triggered.connect(do_hide_window)
            self.window_menu.addAction(self.hide_window_action)


        """ Add Log Menu """

        self.log_menu = self.menu.addMenu('Log')
        self.log_window_action = QAction("Open Console Log", self)

        def do_toggle_log_window():
            if G.log_window.isVisible():
                G.log_window.hide()
            else:
                G.log_window.move(self.x()+50, self.y()+100)
                G.log_window.show()

        self.log_window_action.triggered.connect(do_toggle_log_window)
        self.log_menu.addAction(self.log_window_action)


        """ Add Help Menu """

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
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)

        # Add the line to the menu frame layout
        layout.addWidget(line)

        # Set the layout of the menu frame as the main layout

        logo_status_layout = QGridLayout()


        """ Create Main App Logo Label """

        t_logo = QLabel()
        t_pixmap = HiDpiPixmap(G.vpf_logo)
        t_pixmap = t_pixmap._scaled(round(t_pixmap.width()/5), round(t_pixmap.height()/5))
        t_logo.setPixmap(t_pixmap)


        """ Create Device Panel """

        device_groupbox = QGroupBox("Active Devices")

        device_groupbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        device_groupbox_layout = QVBoxLayout()
        device_groupbox_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.device_panel = DeviceIconPanel()
        device_groupbox_layout.addWidget(self.device_panel)
        device_groupbox.setLayout(device_groupbox_layout)

        if not G.master_instance:
            self.device_panel.set_devices([G.device_type])
            self.device_panel.set_device_status(G.device_type, "ok")
            self.device_panel.set_active_device(G.device_type)


        """ Create Status Panel """

        self.status_container = AppStatusWidget(master_instance=G.master_instance)
        status_group = QGroupBox("Application Status")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(10, 18, 10, 8)
        status_layout.addWidget(self.status_container)

        self.status_container.cb_selectProfileCombo.currentIndexChanged.connect(self.on_profile_change)
        self.status_container.sim_status_label.set_waiting()

        def on_sims_changed(sim: SimTelemListener):
            self.status_container.update_enabled_sims(sim.name, sim.started)
            self.refresh_telem_status()


        """ Connect sim listeners to sim change function """

        G.sim_listeners.simStarted.connect(on_sims_changed)
        G.sim_listeners.simStopped.connect(on_sims_changed)


        """ Add spacer items to fill first row and 2nd column with 10x10 empty space """

        logo_status_layout.addItem(QSpacerItem(10, 10, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed), 0, 0, 1, 1)
        logo_status_layout.addItem(QSpacerItem(10, 10, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed), 0, 1, 1, 1)


        """ Add Logo to the top left cell """

        logo_status_layout.addWidget(t_logo, 1, 0, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)


        """ Add spacer in row 2 """

        logo_status_layout.addItem(QSpacerItem(10, 10, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed), 2, 0, 1, 1)


        """ Add device panel to row 3 column 0 """

        logo_status_layout.addWidget(device_groupbox, 3, 0,alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        """ Add Status widget to column 2, span 3 rows """

        logo_status_layout.addWidget(status_group, 1, 2, 3, 1, alignment=Qt.AlignmentFlag.AlignTop)
        logo_status_layout.addItem(QSpacerItem(10, 10, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed), 4, 0, 1, 1)

        logo_status_layout.setColumnStretch(0, 1)
        logo_status_layout.setColumnStretch(1, 1)


        """ Add upper grid layout to main layout """

        layout.addLayout(logo_status_layout)


        """ Create new craft button - pops when unknown aircraft is detected """

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
        self.new_craft_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        new_craft_layout.addWidget(self.new_craft_button)
        new_craft_layout.addSpacing(7)


        """ Add new craft button to main layout """

        layout.addLayout(new_craft_layout)
        self.new_craft_button.hide()


        """ Create offline config control area QWidget """

        self.offline_config_area = QWidget()
        self.offline_config_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        offline_config_layout = QVBoxLayout()  # vertical layout to hold both rows


        # First row layout (existing widgets)
        # --- Create the Offline Editor GroupBox ---
        self.offline_groupbox = QGroupBox("Offline Editor Setup")
        self.offline_groupbox.setStyleSheet("""
            QGroupBox {
            
                font-weight: bold;
                border: 1px solid gray;
                border-radius: 5px;
                margin-top: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
        """)

        offline_layout = QVBoxLayout(self.offline_groupbox)
        offline_layout.setContentsMargins(10, 18, 10, 10)
        offline_layout.setSpacing(10)


        """ Create Offline controls layout """

        offline_grid_layout = QGridLayout()

        # --- Labels ---
        offline_sim_lbl = QLabel('Sim:')
        offline_sim_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        offline_class_lbl = QLabel('Class:')
        offline_class_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        offline_name_lbl = QLabel('Aircraft Name:')
        offline_name_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        offline_profile_lbl = QLabel('Profile:')
        offline_profile_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Create filter box
        self.offline_name_filter = QLineEdit()
        self.offline_name_filter.setPlaceholderText("Filter")
        self.offline_name_filter.setEnabled(False)
        self.offline_name_filter.textChanged.connect(self.filter_offline_name_list)

        """ Add label widgets to layout """

        offline_grid_layout.addWidget(offline_sim_lbl, 0, 0)
        offline_grid_layout.addWidget(offline_class_lbl, 0, 1)
        offline_grid_layout.addWidget(offline_name_lbl, 0, 2)
        offline_grid_layout.addWidget(self.offline_name_filter, 0, 3)
        offline_grid_layout.addWidget(offline_profile_lbl, 0, 4)


        """ Create Offline controls combo boxes """

        # --- ComboBoxes ---
        self.offline_sim = QComboBox()
        self.offline_sim.addItems([''] + xmlutils.get_sims())
        self.offline_sim.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.offline_sim.setMinimumContentsLength(10)
        self.offline_sim.setEditable(False)
        self.offline_sim.currentTextChanged.connect(self.offline_sim_changed)

        self.offline_class = QComboBox()
        self.offline_class.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.offline_class.setMinimumContentsLength(15)
        self.offline_class.setEditable(False)
        self.offline_class.currentTextChanged.connect(self.offline_class_changed)

        self.offline_name = QComboBox()
        self.offline_name.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.offline_name.setMinimumContentsLength(20)
        self.offline_name.setEditable(False)
        self.offline_name.currentTextChanged.connect(self.offline_aircraft_changed)

        self.offline_profile = QComboBox()
        self.offline_profile.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.offline_profile.setMinimumContentsLength(15)
        self.offline_profile.setEditable(False)
        self.offline_profile.currentTextChanged.connect(self.offline_profile_changed)


        """ Add offline combo box controls to layout """

        offline_grid_layout.addWidget(self.offline_sim, 1, 0)
        offline_grid_layout.addWidget(self.offline_class, 1, 1)
        offline_grid_layout.addWidget(self.offline_name, 1, 2, 1, 2)
        offline_grid_layout.addWidget(self.offline_profile, 1, 4)

        # --- Column stretch ratios (1:2:4:2) ---
        offline_grid_layout.setColumnStretch(0, 1)
        offline_grid_layout.setColumnStretch(1, 2)
        offline_grid_layout.setColumnStretch(2, 4)
        offline_grid_layout.setColumnStretch(4, 2)

        offline_layout.addLayout(offline_grid_layout)


        """ Add layout for labels/buttons on bottom row of offline config area """

        bottom_row = QHBoxLayout()


        """ Create offline scope label """

        offline_scope = QLabel("<b>Offline Scope:   </b>")
        self.offline_scope_label = QLabel('None')


        """
        Create 'back to profile manager' button.  Only shows when edit is
        activated via profile manager
        """

        self.back_to_profile_mgr_button = QPushButton('Back to Profile Manager')
        self.back_to_profile_mgr_button.setVisible(False)
        self.back_to_profile_mgr_button.clicked.connect(self.back_to_profile_mgr)


        """ Create offline mode exit button """

        self.exit_offline_button = QPushButton()
        self.exit_offline_button.setText('Exit Offline Mode')
        self.exit_offline_button.clicked.connect(lambda: self.toggle_offline_mode(False))


        """ Add labels/buttons to bottom row layout """

        bottom_row.addWidget(offline_scope, alignment=Qt.AlignmentFlag.AlignLeft)
        bottom_row.addWidget(self.offline_scope_label, alignment=Qt.AlignmentFlag.AlignLeft)
        bottom_row.addStretch()
        bottom_row.addWidget(self.back_to_profile_mgr_button, alignment=Qt.AlignmentFlag.AlignRight)
        bottom_row.addWidget(self.exit_offline_button, alignment=Qt.AlignmentFlag.AlignRight)


        """ Add bottom row to layout """

        offline_layout.addLayout(bottom_row)


        """ Add items to layout """

        offline_config_layout.addWidget(self.offline_groupbox)
        offline_config_layout.addLayout(offline_grid_layout)
        offline_config_layout.addLayout(bottom_row)


        """ Add layout to QWidget """

        self.offline_config_area.setLayout(offline_config_layout)


        """ Hide Offline config area (gets shown when it is enabled) """

        self.offline_config_area.hide()


        """ Add offline panel to main layout """

        layout.addWidget(self.offline_config_area)


        """ Create tab widget where monitor/settings/hide will live """

        self.tab_widget = QTabWidget(self)


        """ Add the tab widget to the main layout """

        layout.addWidget(self.tab_widget, stretch=1)
        layout.setSpacing(0)


        """ Create the monitor tab telemetry display panel """

        self.monitor_widget = QWidget()
        self.telem_area = QScrollArea()
        monitor_area_layout = QGridLayout()
        self.telem_area.setWidgetResizable(True)
        self.telem_area.setMinimumHeight(100)


        """ Create the active effects display panel """

        self.effects_area = QScrollArea()
        self.effects_area.setWidgetResizable(True)
        self.effects_area.setMinimumHeight(100)

        # self.effects_area.setMaximumWidth(200)


        """ Create the Telemetry Label widget and set its properties """

        self.lbl_telem_data = QLabel()

        self.refresh_telem_status()

        self.lbl_telem_data.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_telem_data.setWordWrap(False)
        self.lbl_telem_data.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_telem_data.setStyleSheet("""
            padding: 2px;
            font-family: Cascadia Mono;
        """)


        """ Set the QLabel widget as the widget inside the scroll area """

        self.telem_area.setWidget(self.lbl_telem_data)

        self.lbl_effects_data = QLabel("            ")  # Empty space placeholder so splitter weights work
        self.effects_area.setWidget(self.lbl_effects_data)
        self.lbl_effects_data.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_effects_data.setStyleSheet("""
            padding: 2px;
            font-family: Cascadia Mono;
        """)

        """ Create Monitor Page detach toolbar"""

        self.monitor_detach_tb = QtWidgets.QToolBar(self.monitor_widget)
        self.monitor_detach_tb.setObjectName("monitorInlineToolbar")
        self.monitor_detach_tb.setMovable(False)
        self.monitor_detach_tb.setFloatable(False)
        self.monitor_detach_tb.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.monitor_detach_tb.setIconSize(QtCore.QSize(16, 16))
        self.monitor_detach_tb.setStyleSheet("QToolBar { border: 0; background: transparent; }")

        self.monitor_detach_act = self.monitor_detach_tb.addAction("Detach")
        self.monitor_detach_act.setToolTip('Detach the Monitor Tab from the main window\ninto a separate window.')
        self.monitor_detach_act.triggered.connect(lambda: self.detach_tab(0))

        btn = self.monitor_detach_tb.widgetForAction(self.monitor_detach_act)
        if isinstance(btn, QtWidgets.QToolButton):
            btn.setAutoRaise(False)
            btn.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet("""
                QToolButton {
                    border: 1px solid palette(mid);
                    border-radius: 4px;
                    padding: 3px 9px;
                    background: palette(button);
                    color: palette(button-text);
                }
                QToolButton:hover { background: palette(midlight); }
                QToolButton:pressed {
                    background: palette(dark);
                    color: palette(highlight);
                }
                QToolButton:disabled { color: palette(mid); border-color: palette(mid); }
            """)

        telem_header_widget = QWidget()
        telem_header_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        telem_header_layout = QHBoxLayout(telem_header_widget)
        telem_header_layout.setContentsMargins(0, 0, 0, 0)
        
        self.telem_lbl = QLabel('Telemetry:')
        self.telem_filter = QLineEdit()
        self.telem_filter.setToolTip("Comma Separated, Case Insensitive list of telemetry items to show (e.g. 'aoa, ias, rpm')")


        """ Add placeholder for the filter """

        self.telem_filter.setPlaceholderText("Filter")
        self.telem_filter.setMaximumWidth(100)


        """ Add telemetry label and filter placeholder to the layout """
        telem_header_layout.addWidget(self.monitor_detach_tb)
        telem_header_layout.addWidget(self.telem_lbl)
        telem_header_layout.addWidget(self.telem_filter)
        telem_header_layout.addStretch()  # Push everything to the left


        """ Add Active effects header label """

        self.effect_lbl = QLabel('Active Effects:')
        if G.master_instance:
            self.effect_lbl.setText(f'Active Effects for: {G.current_device_config_scope}')


        """ Add headers and labels to the monitor layout """

        monitor_area_layout.addWidget(telem_header_widget, 0, 0)
        monitor_area_layout.addWidget(self.effect_lbl, 0, 1)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.telem_area)
        splitter.addWidget(self.effects_area)
        splitter.setStretchFactor(0, 2)  # Wider telemetry
        splitter.setStretchFactor(1, 3)  # Narrow effects
        monitor_area_layout.addWidget(splitter, 1, 0, 1, 2)  # Span both columns

        self.monitor_widget.setLayout(monitor_area_layout)


        """ Add the monitor tab object to the tab widget"""

        self.tab_widget.addTab(self.monitor_widget, "Monitor")

        self._install_detachable_tabs()

        """ Create settings scroll area widget that will hold the settings page"""

        self.settings_area = NoKeyScrollArea()
        self.settings_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.settings_area.setWidgetResizable(True)


        """ Create widget to hold the settings layout """

        settings_widget = QWidget()


        """ Create settings layout instance """

        self.settings_layout = SettingsLayout(parent=self, mainwindow=self)


        """ Add settings layout to the tab widget """

        settings_widget.setLayout(self.settings_layout)
        self.settings_area.setWidget(settings_widget)
        self.tab_widget.addTab(self.settings_area, "Settings")


        """ Create the Hide tab and set its properties """

        self.tab_widget.addTab(QWidget(), "Hide")
        self.tab_widget.currentChanged.connect(self.switch_window_view)
        tb_height = self.tab_widget.tabBar().sizeHint().height()
        self.tab_widget.setMinimumHeight(tb_height)


        """ Create central widget to whole the entire layout """

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        self.layout = QVBoxLayout(central_widget)


        """ Add status bar to hold version information """

        self.status_bar = QStatusBar(self)

        """ Add version label to the status bar """

        self.version_label = QLabel()

        if G.release_version:
            status_text = f"Release Version {utils.get_version()}"
        else:
            status_text = "UNKNOWN"

        self.version_label.setText(f'Version Status: {status_text}')
        self.version_label.setOpenExternalLinks(True)
        self.setStatusBar(self.status_bar)
        self.firmware_label = QLabel()
        if not HapticEffect.device:
            f_vers = 'Device Disconnected'
            self.firmware_label.setText("Device Disconnected")
        else:
            try:
                f_vers = HapticEffect.device.get_firmware_version()
            except:
                f_vers = 'error fetching'
            self.firmware_label.setText(f'Rhino Firmware: {f_vers}')

        self.version_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.firmware_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        # Add exception status widget to status bar
        self.exception_status_widget = ExceptionStatusWidget(self)
        self.exception_status_widget.clicked.connect(self.show_exception_viewer)
        
        self.status_bar.addWidget(self.firmware_label)
        self.status_bar.addPermanentWidget(self.exception_status_widget)
        self.status_bar.addPermanentWidget(self.version_label)

        self.exception_status_widget.set_count(G.exception_tracker.get_count())


        """ Setup hooks to update the telemetry and settings widgets """

        G.telem_manager.telemetryReceived.connect(self.on_update_telemetry)
        G.telem_manager.telemetryTimeout.connect(self.on_telemetry_timeout)
        G.telem_manager.aircraftUpdated.connect(self.update_settings)
        
        """ Connect exception tracker signals """
        
        G.exception_tracker.exception_added.connect(self.update_exception_count)
        G.exception_tracker.exceptions_cleared.connect(self.update_exception_count)


        """  Load the stored window geometry from users registry keys """

        self.load_main_window_geometry()

        """ Add Debug Menu to the menu bar - control visibility with Alt+D shortcut or via debug key in registry """

        debug_shortcut = QShortcut(QKeySequence('Alt+D'), self)
        debug_shortcut.activated.connect(self.add_debug_menu)

        reload_shortcut = QShortcut(QKeySequence('Ctrl+Shift+R'), self)
        reload_shortcut.activated.connect(self.force_reload_aircraft)

        if G.system_settings.get('debug', False):
            # debug manu is disabled by default.  change debug = true (1) in registry to permanently enable
            self.add_debug_menu()

        """  Create configurator gain dialog for use during TelemFFB session and store object in globals """

        G.gain_override_dialog = ConfiguratorDialog(self)

    def _install_detachable_tabs(self):
        """Enable context menu on the tab bar for detaching/reattaching."""
        self._detached_tabs = {}  # title -> {"win": DetachedTabWindow, "index": int, "widget": QWidget}

        bar = self.tab_widget.tabBar()
        bar.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._show_tab_context_menu)

        # Optional: keyboard shortcut to detach the Monitor tab
        detach_shortcut = QShortcut(QKeySequence("Ctrl+Shift+M"), self)
        detach_shortcut.activated.connect(self._detach_monitor_via_shortcut)

    def _detach_monitor_via_shortcut(self):
        idx = self.tab_widget.indexOf(self.monitor_widget)
        if idx != -1:
            self.detach_tab(idx)

    def _show_tab_context_menu(self, pos: QtCore.QPoint):
        bar = self.tab_widget.tabBar()
        index = bar.tabAt(pos)
        ## Montor page only supported for now
        if index != 0:
            return
        title = self.tab_widget.tabText(index)

        menu = QtWidgets.QMenu(bar)
        detach_act = QAction("Detach", self)
        reattach_act = QAction("Reattach", self)

        # Only allow detach/reattach for the Monitor tab (per your request)
        is_monitor = (title == "Monitor")
        is_detached = title in self._detached_tabs

        detach_act.setEnabled(is_monitor and not is_detached)
        reattach_act.setEnabled(is_monitor and is_detached)

        detach_act.triggered.connect(lambda: self.detach_tab(index))
        reattach_act.triggered.connect(lambda: self.reattach_tab(title))

        menu.addAction(detach_act)
        menu.addAction(reattach_act)
        menu.exec(bar.mapToGlobal(pos))

    def detach_tab(self, index: int):
        if index == 0:  # Monitor Tab
            self.monitor_detach_tb.setVisible(False)
        title = self.tab_widget.tabText(index)
        if hasattr(self, "_detached_tabs") and title in self._detached_tabs:
            return
        page = self.tab_widget.widget(index)
        if page is None:
            return

        self.tab_widget.removeTab(index)
        page.setParent(None)
        page.show()

        win = DetachedTabWindow(title, self)
        win.reattachRequested.connect(self.reattach_tab)
        win.adopt_page(page)
        win.show()

        self._detached_tabs = getattr(self, "_detached_tabs", {})
        self._detached_tabs[title] = {"win": win, "index": index}

    def reattach_tab(self, title: str):
        entry = getattr(self, "_detached_tabs", {}).pop(title, None)
        if not entry:
            return

        if title == 'Monitor':
            self.monitor_detach_tb.setVisible(True)

        win: DetachedTabWindow = entry["win"]
        original_index: int = entry["index"]

        page = win.release_page()
        if page is None:
            win.deleteLater()
            return

        win.deleteLater()

        insert_at = max(0, min(original_index, self.tab_widget.count()))
        page.setParent(self.tab_widget)
        self.tab_widget.insertTab(insert_at, page, title)
        self.tab_widget.setCurrentWidget(page)
        page.show()

    def get_active_buttons(self):
        input_data = HapticEffect.device.get_input()
        if input_data is not None:
            btns = input_data.getPressedButtons()
            if btns != G.active_buttons:
                # only send if pressed buttons has changed
                G.active_buttons = btns
                if G.master_instance:
                    G.ipc_instance.send_broadcast_message(f"MASTER_BUTTONS:{G.active_buttons}")
                else:
                    G.ipc_instance.send_message(f"BUTTONS:{G.device_type}_{G.active_buttons}")

    def add_system_tray(self):
        self.tray_icon.setIcon(QIcon(":/image/vpforceicon.png"))
        self.tray_icon.setToolTip("VPforce TelemFFB")

        # Create the tray menu
        tray_menu = QMenu()
        show_action = QAction("Show Window", self)

        def do_show_main_window(trigger):
            if isinstance(trigger, QSystemTrayIcon.ActivationReason):
                if trigger == QSystemTrayIcon.ActivationReason.DoubleClick:
                    self.showNormal()  # Restore the window to its normal state if minimized
                    self.show()
                    self.raise_()
                    self.activateWindow()
            elif isinstance(trigger, str) and trigger == "show":
                self.showNormal()  # Restore the window to its normal state if minimized
                self.show()
                self.raise_()
                self.activateWindow()
            if G.is_exe:
                start_with_windows_action.setChecked(self.toggle_start_with_windows())
            start_minimized_action.setChecked(G.system_settings.get('startToTray', False))
            send_to_tray_action.setChecked(G.system_settings.get('closeToTray', False))

        self.tray_icon.activated.connect(do_show_main_window)
        show_action.triggered.connect(lambda: do_show_main_window('show'))

        tray_menu.addAction(show_action)

        # Create the "Options" menu
        options_menu = QMenu("Options", self)

        # Setup Start With Windows menu option
        if G.is_exe:
            start_with_windows_action = QAction("Start With Windows", self)
            start_with_windows_action.setCheckable(True)
            start_with_windows_action.setChecked(G.system_settings.get('startWithWindows', False))

            def do_toggle_set_start_with_windows(checked):
                self.toggle_start_with_windows(checked)

            start_with_windows_action.triggered.connect(lambda checked: do_toggle_set_start_with_windows(checked))

            options_menu.addAction(start_with_windows_action)

        # Setup Start Minimized menu option
        start_minimized_action = QAction("Start in Tray", self)
        start_minimized_action.setCheckable(True)
        start_minimized_action.setChecked(G.system_settings.get('startToTray', False))

        def do_toggle_set_start_minimized(checked):
            G.system_settings.setValue('startToTray', checked)

        start_minimized_action.triggered.connect(lambda checked: do_toggle_set_start_minimized(checked))

        options_menu.addAction(start_minimized_action)

        # Setup Send to Tray menu option
        send_to_tray_action = QAction("Closing App Sends to Tray", self)
        send_to_tray_action.setCheckable(True)
        send_to_tray_action.setChecked(G.system_settings.get('closeToTray', False))

        def do_toggle_set_send_to_tray(checked):
            G.system_settings.setValue('closeToTray', checked)

        send_to_tray_action.triggered.connect(lambda checked: do_toggle_set_send_to_tray(checked))

        options_menu.addAction(send_to_tray_action)

        tray_menu.addMenu(options_menu)

        # Create the "Instances" menu
        if G.launched_instances:
            show_menu = QMenu("Instances", self)
            show_child_window_action = {}
            for d in ["joystick", "pedals", "collective", 'trimwheel']:
                if d in G.launched_instances:
                    def do_show_child_window(child=d):
                        G.ipc_instance.send_broadcast_message(f'SHOW WINDOW:{child}')

                    show_child_window_action[d] = QAction(f'Show {d.capitalize()} Instance', self)
                    show_child_window_action[d].triggered.connect(lambda _, child=d: do_show_child_window(child))
                    show_menu.addAction(show_child_window_action[d])
            tray_menu.addMenu(show_menu)

        quit_action = QAction("Quit TelemFFB", self)
        quit_action.triggered.connect(exit_application)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        # Show the tray icon
        self.tray_icon.show()
        if self.isHidden():
            #  don't show, send message to tray icon that will pop to notify user that TelemFFB is running in Tray
            icon = QIcon(":/image/vpforceicon.png")
            self.pop_tray_notification(
                None,
                "TelemFFB is running in the system tray.  Double-Click the VPforce Icon to show or right click to set options in the context menu",
                5
            )

    def toggle_start_with_windows(self, set_enabled=None):
        exe_path = sys.executable
        reg_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        reg_key_name = "VPforce TelemFFB"

        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ)
            if set_enabled is None:
                #if no state defined, just querey and return state
                try:
                    value, _ = winreg.QueryValueEx(reg_key, reg_key_name)
                    winreg.CloseKey(reg_key)
                    return True
                except FileNotFoundError:
                    return False
            else:
                if set_enabled:
                    winreg.SetValueEx(reg_key, reg_key_name, 0, winreg.REG_SZ, exe_path)
                else:
                    try:
                        winreg.DeleteValue(reg_key, reg_key_name)
                    except FileNotFoundError:
                        pass
                winreg.CloseKey(reg_key)
        except FileNotFoundError:
            if set_enabled:
                reg_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_key_path)
                winreg.SetValueEx(reg_key, reg_key_name, 0, winreg.REG_SZ, exe_path)
                winreg.CloseKey(reg_key)

    def add_instance_log_menu(self):
        self.log_menu.addAction(self.log_window_action)
        if G.master_instance and G.system_settings.get('autolaunchMaster', 0):
            self.child_log_menu = self.log_menu.addMenu('Open Child Logs')

            self.log_action = {}
            for d in ["joystick", "pedals", "collective", 'trimwheel']:
                if d in G.launched_instances:
                    def do_show_child_log(child=d):
                        G.ipc_instance.send_broadcast_message(f'SHOW LOG:{child}')

                    self.log_action[d] = QAction(f'{d} Log'.capitalize())
                    self.log_action[d].triggered.connect(lambda _, child=d: do_show_child_log(child))
                    self.child_log_menu.addAction(self.log_action[d])

    def test_function(self):
        self.set_scrollbar(400)

    def refresh_telem_status(self):
        dcs_enabled = G.system_settings.get('enableDCS')
        il2_enabled = G.system_settings.get('enableIL2')
        msfs_enabled = G.system_settings.get('enableMSFS')
        xplane_enabled = G.system_settings.get('enableXPLANE')
        bms_enabled = G.system_settings.get('enableBMS')

        # Convert True/False to "enabled" or "disabled"
        dcs_status = "Enabled" if dcs_enabled else "Disabled"
        il2_status = "Enabled" if il2_enabled else "Disabled"
        msfs_status = "Enabled" if msfs_enabled else "Disabled"
        xplane_status = "Enabled" if xplane_enabled else "Disabled"
        bms_status = "Enabled" if bms_enabled else "Disabled"

        self.lbl_telem_data.setText(
            f"Waiting for data...\n\n"
            f"DCS     : {dcs_status}\n"
            f"IL2     : {il2_status}\n"
            f"MSFS    : {msfs_status}\n"
            f"X-Plane : {xplane_status}\n"
            f"BMS     : {bms_status}\n\n"
            "Enable or Disable in System -> System Settings"
        )

    def force_reload_aircraft(self):
        G.force_reload_aircraft_trigger = True
        G.telem_manager.currentAircraftName = None
        logging.info("Force Reload (Ctrl+Shift+R) initiated.  Reloading config and re-pushing configurator file (if applicable)")
        if G.master_instance:
            G.ipc_instance.send_broadcast_message("RELOAD AIRCRAFT")

    def show_exception_viewer(self):
        """Show the exception viewer dialog."""
        dialog = ExceptionViewerDialog(G.exception_tracker, self)
        dialog.exec()
        
    def update_exception_count(self):
        """Update the exception count in the status bar."""
        count = G.exception_tracker.get_count()
        self.exception_status_widget.set_count(count)

    def add_debug_menu(self):
        # debug mode
        for action in self.menu.actions():
            if action.text() == "Debug":
                return
        debug_menu = self.menu.addMenu("Debug")

        teleplot_action = QAction("Teleplot Setup", self)
        def do_open_teleplot_setup_dialog():
            dialog = TeleplotSetupDialog(self)
            dialog.exec()
        teleplot_action.triggered.connect(do_open_teleplot_setup_dialog)
        debug_menu.addAction(teleplot_action)

        show_simvar_action = QAction("Show simvar in telem window", self)
        def do_toggle_simvar_telemetry():
            self.show_simvars = not self.show_simvars
            show_simvar_action.setChecked(self.show_simvars)

        show_simvar_action.triggered.connect(do_toggle_simvar_telemetry)
        show_simvar_action.setCheckable(True)
        debug_menu.addAction(show_simvar_action)

        show_order_action = QAction("Show settings order numbering", self)
        def do_toggle_order_numbering():
            SettingsLayout.show_order_debug = not  SettingsLayout.show_order_debug
            show_order_action.setChecked(SettingsLayout.show_order_debug)

        show_order_action.triggered.connect(do_toggle_order_numbering)
        show_order_action.setCheckable(True)
        debug_menu.addAction(show_order_action)

        show_replaced = QAction("Show settings source", self)
        def do_toggle_replaced():
            SettingsLayout.show_replaced = not SettingsLayout.show_replaced
            show_replaced.setChecked(SettingsLayout.show_replaced)

        show_replaced.triggered.connect(do_toggle_replaced)
        show_replaced.setCheckable(True)
        debug_menu.addAction(show_replaced)


        show_settingname_action = QAction("Show settings internal name", self)
        def do_toggle_settingsnames():
            SettingsLayout.show_settings_names = not  SettingsLayout.show_settings_names
            show_settingname_action.setChecked(SettingsLayout.show_settings_names)

        show_settingname_action.triggered.connect(do_toggle_settingsnames)
        show_settingname_action.setCheckable(True)
        debug_menu.addAction(show_settingname_action)

        configurator_settings_action = QAction('Configurator Gain Override', self)
        def do_open_configurator_dialog():
            dialog = ConfiguratorDialog(self)
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        configurator_settings_action.triggered.connect(do_open_configurator_dialog)
        debug_menu.addAction(configurator_settings_action)

        sc_overrides_action = QAction('SimConnect Overrides Editor', self)
        def do_open_sc_override_dialog():
            dialog = SCOverridesEditor(self)
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        # dialog.exec_()
        sc_overrides_action.triggered.connect(do_open_sc_override_dialog)
        debug_menu.addAction(sc_overrides_action)

        test_update = QAction('Test updater', self)
        def do_test_update():
            self._update_available = True
            self.perform_update()
        test_update.triggered.connect(do_test_update)
        debug_menu.addAction(test_update)

        if G.master_instance:
            custom_userconfig_action = QAction("Load Custom User Config", self)
            custom_userconfig_action.triggered.connect(lambda: utils.load_custom_userconfig())
            debug_menu.addAction(custom_userconfig_action)

    def set_scrollbar(self, pos):
        self.settings_area.verticalScrollBar().setValue(pos)

    @pyqtSlot(bool)
    def update_device_status(self, connected):
        G.device_connection_status = connected
        status = "ACTIVE" if connected else "DISCONNECTED"
        self.device_panel.set_device_status(G.device_type, status)

    @pyqtSlot(str, str)
    def update_child_status(self, device, status):
        # self.instance_status_row.set_status(device, status)
        self.device_panel.set_device_status(device, status)

    def show_child_settings(self):
        G.ipc_instance.send_broadcast_message("SHOW SETTINGS")

    def reset_user_config(self):
        ans = QMessageBox.warning(self, "Caution", "Are you sure you want to proceed?  All contents of your user configuration will be erased\n\nA backup of the configuration will be generated containing the current timestamp.", QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)

        if ans == QMessageBox.StandardButton.Ok:
            try:
                # Get the current timestamp
                timestamp = datetime.now().strftime('%Y%m%d_%H%M')

                backup_path = os.path.join(G.userconfig_rootpath, 'cfg_backup')
                os.makedirs(backup_path, exist_ok=True)

                # Create the backup file name with the timestamp
                backup_file = os.path.join(backup_path, ('userconfig_' + timestamp + '.bak'))

                # Copy the file to the backup file
                shutil.copy(G.userconfig_path, backup_file)

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


    def setup_master_instance(self):
        # self.show_device_logo()
        # self.enable_device_logo_click(True)

        #self.devicetype_label.hide()
        current_title = self.windowTitle()
        if len(G.launched_instances):
            current_title = f"** MASTER INSTANCE ** {current_title}"
        self.setWindowTitle(current_title)
        # self.instance_status_row.show()
        # if "joystick" in G.launched_instances:
        #     self.instance_status_row.joystick_status_icon.show()
        # if "pedals" in G.launched_instances:
        #     self.instance_status_row.pedals_status_icon.show()
        # if "collective" in G.launched_instances:
        #     self.instance_status_row.collective_status_icon.show()
        # if 'trimwheel' in G.launched_instances:
        #     self.instance_status_row.trimwheel_status_icon.show()
        self.add_instance_log_menu()
        self.add_system_tray()
        d_list = [G.device_type]
        for d in G.launched_instances:
            d_list.append(d)
        self.device_panel.set_devices(d_list)
        self.device_panel.set_device_status(G.device_type, "ok")
        self.device_panel.DeviceClicked.connect(self.change_config_scope)
        self.device_panel.set_active_device(G.device_type)


    def show_device_logo(self):
        self.devicetype_label.show()

    def enable_device_logo_click(self, state):
        hover_color = "#444444" if G.useDarkMode else "#DCDCDC"
        self.devicetype_label.setClickable(state)
        self.devicetype_label.setStyleSheet(
            f"""
               QLabel {{
                   border-radius: 4px;
               }}
               QLabel:hover {{
                   background-color: {hover_color};
               }}
               """
        )

    def device_logo_click_event(self):
        # print("External function executed on label click")
        # print(G.current_device_config_scope)
        def check_instance(name):
            return name in G.launched_instances or G.device_type == name
        if G.current_device_config_scope == 'joystick':
            if check_instance("pedals"):
                self.change_config_scope(2)
            elif check_instance("collective"):
                self.change_config_scope(3)
            elif check_instance("trimwheel"):
                self.change_config_scope(4)
        elif G.current_device_config_scope == 'pedals':
            if check_instance("collective"):
                self.change_config_scope(3)
            elif check_instance("trimwheel"):
                self.change_config_scope(4)
            elif check_instance("joystick"):
                self.change_config_scope(1)
        elif G.current_device_config_scope == 'collective':
            if check_instance("trimwheel"):
                self.change_config_scope(4)
            elif check_instance("joystick"):
                self.change_config_scope(1)
            elif check_instance("pedals"):
                self.change_config_scope(2)
        elif G.current_device_config_scope == 'trimwheel':
            if check_instance("joystick"):
                self.change_config_scope(1)
            elif check_instance("pedals"):
                self.change_config_scope(2)
            elif check_instance("collective"):
                self.change_config_scope(3)

    def update_version_result(self, vers, url):
        self.latest_version = vers

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
                self.version_label.setText('Version Status: <b>Development Build</b>')
            else:
                self.version_label.setText('Version Status: <b>Development - Clean source</b>')

        elif vers == 'needsupdate':
            self.version_label.setText('Version Status: <b>Out of Date Source - Git pull needed</b>')
        
        elif vers == 'dirty':
            self.version_label.setText('Version Status: <b>Development - Modified Source</b>')

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
            elif 'trimwheel' in _arg: arg = 4
        else:
            arg = _arg

        types = {
            1 : "joystick",
            2 : "pedals",
            3 : "collective",
            4 : "trimwheel"
        }

        xmlutils.update_vars(types[arg], G.userconfig_path, G.defaults_path)
        G.current_device_config_scope = types[arg]
        self.device_panel.set_active_device(types[arg])

        # pixmap = HiDpiPixmap(utils.get_device_logo(G.current_device_config_scope))
        # self.devicetype_label.setPixmap(pixmap)
        #self.devicetype_label.setFixedSize(pixmap.width(), pixmap.height())

        if G.master_instance:
            self.effect_lbl.setText(f'Active Effects for: {G.current_device_config_scope}')
        self.settings_layout.reload_caller()

    def resize_offline_combos(self):
        """
            Dynamically resizes the minimum width of all offline mode combo boxes
            based on the widest item in each. Adds 50 pixels padding to ensure space.

            This ensures no items are truncated in display and helps with layout alignment.
            """
        for combo in [self.offline_sim, self.offline_class, self.offline_name, self.offline_profile]:
            metrics = QFontMetrics(combo.font())
            max_width = 0

            for i in range(combo.count()):
                text = combo.itemText(i)
                width = metrics.horizontalAdvance(text)
                max_width = max(max_width, width)

            # Add 2 pixels for spacing and set minimum width
            combo.setMinimumWidth(max_width + 50)

        self.settings_layout.reload_caller()

    def show_profile_manager(self):
        xmlutils.update_roots() # make sure roots get updated in case state is timedout and file has changed
        self.profile_mgr_dialog = ProfileManagerDialog(self)
        self.profile_mgr_dialog.raise_()
        self.profile_mgr_dialog.activateWindow()
        self.profile_mgr_dialog.show()

    def exit_offline_mode(self):
        self.toggle_offline_mode(False)
        if self.profile_mgr_dialog:
            self.profile_mgr_dialog.close()


    def back_to_profile_mgr(self):
        self.back_to_profile_mgr_button.setVisible(False)
        try:
            # in case it somehow got closed
            self.profile_mgr_dialog.show()
        except:
            QMessageBox.warning(self, "Profile Manager", "IDK WHY THIS ERROR HAPPENED")
            pass
        self.toggle_offline_mode(False)


    @pyqtSlot(bool)
    def toggle_offline_mode(self, state):
        if state == G.settings_mgr.offline_mode:
            # if already in the same state, do nothing
            return
        if not state:
            # Exiting Offline editing mode

            G.settings_mgr.go_online()

            # clear the layout after going back online
            G.main_window.settings_layout.clear_layout()

            # reset the craft area text to default
            self.status_container.reset()
            self.settings_layout.reload_caller()
        else:
            # Entering offline editing mode
            G.settings_mgr.go_offline()
            self.status_container.set_offline("None")
            # clear the layout in case an aircraft was previously loaded live
            G.main_window.settings_layout.clear_layout()

            # Block signals so we don't trigger text change on .clear() calls
            self.offline_name.blockSignals(True)
            self.offline_class.blockSignals(True)
            self.offline_name.blockSignals(True)

            # clear contents of combo boxes so they can be repopulated
            self.offline_name.clear()
            self.offline_class.clear()
            self.offline_sim.clear()

            # unblock signals
            self.offline_name.blockSignals(False)
            self.offline_class.blockSignals(False)
            self.offline_name.blockSignals(False)

            # build sim list
            sims = [''] + xmlutils.get_sims()
            self.offline_sim.addItems(sims)

            # force the settings tab to be active
            self.tab_widget.setCurrentIndex(1)

        if G.master_instance:
            # Show the offline mode widgets, but only for master instance
            self.offline_config_area.setVisible(state)

            # Send command to chile instance to replicate actions
            G.ipc_instance.send_broadcast_message(f"TOGGLE OFFLINE:{state}")

    @pyqtSlot(str, str, str, str)
    def load_single_offline_model(self, sim, cls, model, profile):

        self.toggle_offline_mode(True)
        for cb in {self.offline_sim, self.offline_class, self.offline_name, self.offline_profile}:
            cb.blockSignals(True)
            cb.clear()
            cb.addItem('')

        sim_list = xmlutils.get_sims()
        for s in sim_list:
            self.offline_sim.addItem(s)
        self.offline_sim.setCurrentText(sim)

        cls_list = xmlutils.get_classes_for_sim(sim)
        for c in cls_list:
            self.offline_class.addItem(c)
        self.offline_class.setCurrentText(cls)

        model_list = xmlutils.read_models(sim, cls)
        self.all_offline_models = model_list
        self.filter_offline_name_list(self.offline_name_filter.text())
        self.offline_name.setCurrentText(model)

        profile_list = xmlutils.get_available_profiles(sim, cls, model)
        self.offline_profile.clear()
        for p in profile_list:
            if p != 'Built-In':
                self.offline_profile.addItem(p)
        self.offline_profile.setCurrentText(profile)
        self.offline_profile_changed(profile)

        for cb in {self.offline_sim, self.offline_class, self.offline_name, self.offline_profile}:
            cb.blockSignals(False)

        G.settings_mgr.offline_scope = 'MODEL'

        self.force_sim_aircraft()
        if G.master_instance:
            self.back_to_profile_mgr_button.setVisible(True)
            args = [sim, cls, model, profile]
            G.ipc_instance.send_broadcast_message(f"SHOW_OFFLINE_MODEL:{json.dumps(args)} ")
            self.resize_offline_combos()



    def update_offline_labeling(self):
        pass

    def offline_sim_changed(self, sim=None):
        """
            Triggered when the offline 'Sim' combo box changes.

            Updates all related combo boxes (class, aircraft, profile),
            sets the configuration scope, and broadcasts the change
            if in master mode.

            Args:
                sim (str, optional): The selected simulation name. If None or empty,
                                     resets the offline editing UI.
            """
        self.offline_name.blockSignals(True)
        self.offline_name_filter.blockSignals(True)
        self.offline_class.blockSignals(True)
        self.offline_name.clear()
        self.offline_name_filter.clear()
        self.offline_class.clear()
        self.offline_name.blockSignals(False)
        self.offline_name_filter.blockSignals(False)
        self.offline_class.blockSignals(False)
        if sim is None or sim == '':
            # if sim combobox is cleared, reset everything and clear the layout
            self.offline_class.clear()  # clear class field
            self.offline_name.clear()
            self.offline_profile.clear()
            self.settings_layout.clear_layout()
            self.offline_scope_label.setText(f"None")
            self.offline_name_filter.setEnabled(False)
            return
        self.offline_name_filter.setEnabled(True)
        self.offline_class.clear()  #clear class field
        self.offline_class.addItem('')
        self.offline_name.clear()
        #self.offline_name.setMaximumWidth(200)
        self.offline_profile.clear()
        classes = xmlutils.get_classes_for_sim(sim)  # get classes based on chosen sim

        for class_name in classes:
            self.offline_class.addItem(class_name)  #populate class combobox based on results

        self.offline_name.clear()  #clear aircraft selection combobox

        model_list = xmlutils.read_models(sim)
        self.all_offline_models = model_list
        self.filter_offline_name_list(self.offline_name_filter.text())

        if G.master_instance:
            # send to child instances to mimic action
            G.ipc_instance.send_broadcast_message(f"OFFLINE_SIM:{self.offline_sim.currentText()}")

        G.settings_mgr.offline_scope = 'SIM'  # set config scope to SIM
        self.offline_scope_label.setText(f"Editing SIM Defaults ({sim})")

        self.resize_offline_combos()
        self.force_sim_aircraft() # load settings based on sim

    def offline_class_changed(self, class_name):
        self.offline_name_filter.blockSignals(True)
        self.offline_name_filter.clear()
        self.offline_name_filter.blockSignals(False)
        model_list = xmlutils.read_models(self.offline_sim.currentText(), class_name)  # get all available models based on sim and class
        self.all_offline_models = model_list
        self.offline_name.clear()  # clear the aircraft selection combobox
        self.offline_profile.clear()
        self.filter_offline_name_list(self.offline_name_filter.text())

        if G.master_instance:
            # send to child instances to mimic action
            G.ipc_instance.send_broadcast_message(f"OFFLINE_CLASS:{self.offline_class.currentText()}")
        if class_name == '':
            # reset back to sim mode if class field is cleared
            self.offline_sim_changed(self.offline_sim.currentText())
        else:
            G.settings_mgr.offline_scope = 'CLASS' # set config scope to CLASS
            self.offline_scope_label.setText(f"Editing Class Defaults ({class_name})")

        self.resize_offline_combos()
        self.force_sim_aircraft() # load settings based on class and currently selected sim

    def offline_aircraft_changed(self, ac_name=None):
        cfg, cls = G.telem_manager.get_aircraft_config(ac_name, self.offline_sim.currentText()) # get class based on selected aircraft
        profiles = xmlutils.get_available_profiles(self.offline_sim.currentText(), self.offline_class.currentText(), ac_name)
        self.offline_profile.setEnabled(True)
        self.offline_profile.clear()

        for profile_name in profiles:
            if profile_name != 'Built-In':
                self.offline_profile.addItem(profile_name)

        if not self.offline_profile.count():
            self.offline_profile.addItem('Auto User')  # manually add 'Auto User' so it is at the top and always present even if there is not yet a Auto User Profile
            xmlutils.update_active_profile_entry(sim=self.offline_sim.currentText(), cls=cls, model=ac_name, new_profile="Auto User")
        self.offline_class.blockSignals(True)  # block signals to prevent triggering of offline_class_changed
        self.offline_class.setCurrentText(cls) # set class combobox to learned class from aircraft config
        self.offline_class.blockSignals(False)  # unblock signals

        if ac_name == '':
            self.offline_class_changed(self.offline_class.currentText())
        else:
            G.settings_mgr.offline_scope = 'MODEL'
            self.offline_scope_label.setText(f"Editing Aircraft ({ac_name} - {self.offline_profile.currentText()})")

        if G.master_instance:
            G.ipc_instance.send_broadcast_message(f'OFFLINE_AC:{self.offline_name.currentText()}')

        self.resize_offline_combos()
        self.force_sim_aircraft()

    def offline_profile_changed(self, profile):
        # self.update_craft_text_block(profile=profile)
        if not profile:
            return
        G.settings_mgr.offline_scope = 'MODEL'
        self.resize_offline_combos()
        self.force_sim_aircraft()
        if G.master_instance:
            # send to child instances to mimic action
            G.ipc_instance.send_broadcast_message(f"OFFLINE_PROFILE:{profile}")
        self.offline_scope_label.setText(f"Editing Aircraft ({self.offline_name.currentText()} - {profile})")

    def filter_offline_name_list(self, text):
        self.offline_name.blockSignals(True)
        self.offline_name.clear()
        self.offline_profile.blockSignals(True)
        self.offline_profile.clear()
        self.offline_name.addItems([''])
        filtered = [name for name in self.all_offline_models if text.lower() in name.lower()]
        self.offline_name.addItems(filtered)
        if len(filtered) == 1:
            self.offline_name.setCurrentIndex(1)
            # Manually trigger the downstream handler
            self.offline_aircraft_changed(filtered[0])
        self.offline_name.blockSignals(False)
        self.offline_profile.blockSignals(False)

    def force_sim_aircraft(self):
        G.settings_mgr.current_sim = self.offline_sim.currentText()
        G.settings_mgr.current_class = self.offline_class.currentText()
        G.settings_mgr.current_aircraft_name = self.offline_name.currentText()
        G.settings_mgr.active_profile = self.offline_profile.currentText()
        self.settings_layout.reload_caller()


    def show_new_aircraft_wizard(self, manual=False, sim=None, name=None, cls=None):
        # utils.debug_caller_args("red")
        wizard = NewAircraftWizard(parent=self, manual=manual, auto_sim=sim, auto_name=name, auto_cls=cls)
        wizard.accepted.connect(self.new_ac_wizard_finished)
        if wizard.exec():
            try:
                # make sure no other calls are connected to avoid stacking lambda calls if user cancels and doesn't add new aircraft
                self.new_craft_button.clicked.disconnect()
            except TypeError:
                pass  # No handler connected yet

    @override
    def closeEvent(self, event):
        # Perform cleanup before closing the application
        if G.child_instance:
            self.hide()
            event.ignore()
        else:
            if G.system_settings.get('closeToTray', False):
                self.hide()
                event.ignore()
                self.pop_tray_notification(
                    None,
                    "TelemFFB is running in the system tray.  Double-Click the VPforce Icon to re-show or right click to set options in the context menu",
                    5
                )
            else:
                exit_application()

    def is_valid_geometry(self, x, y):
        '''
        Check whether proposed window position is valid on any active screen
        '''
        for screen in QApplication.screens():
            screen_geometry = screen.availableGeometry()
            if screen_geometry.contains(x, y):
                return True
        return False

    def load_main_window_geometry(self):
        settings = G.system_settings
        window_data = settings.get("WindowData")
        
        if window_data is not None:
            try:
                window_data_dict = json.loads(window_data)
                
                # Restore geometry and state if available
                if 'geometry' in window_data_dict:
                    geometry = QtCore.QByteArray.fromBase64(window_data_dict['geometry'].encode())
                    if not self.restoreGeometry(geometry):
                        self.set_default_geometry()

                if 'state' in window_data_dict:
                    state = QtCore.QByteArray.fromBase64(window_data_dict['state'].encode())
                    self.restoreState(state)
                
                # Load tab settings
                if G.system_settings.get('saveLastTab', True):
                    tab = window_data_dict.get('Tab', 0)
                    self.tab_sizes = window_data_dict.get('TabSizes', self.default_tab_sizes)
                    self.tab_widget.setCurrentIndex(tab)
                    self.switch_window_view(tab)
                    
                    h = self.tab_sizes[str(tab)]['height']
                    w = self.tab_sizes[str(tab)]['width']
                    self.resize(w, h)

                # Validate window position is on screen
                if not self.is_valid_geometry(self.x(), self.y()):
                    self.set_default_geometry()
                    
            except Exception as e:
                logging.warning(f"Error restoring window geometry: {e}")
                self.set_default_geometry()
        else:
            self.set_default_geometry()

    def save_main_window_geometry(self):
        # Save both geometry and window state
        settings = G.system_settings
        device_type = G.device_type

        # Convert geometry and state to base64 strings for storage
        geometry = self.saveGeometry().toBase64().data().decode()
        state = self.saveState().toBase64().data().decode()
        
        # Save current tab info
        cur_index = self.tab_widget.currentIndex()
        self.tab_sizes[str(cur_index)]['width'] = self.width()
        self.tab_sizes[str(cur_index)]['height'] = self.height()

        window_dict = {
            'geometry': geometry,
            'state': state,
            'Tab': cur_index,
            'TabSizes': self.tab_sizes
        }

        settings.setValue(f"{device_type}/WindowData", json.dumps(window_dict))

    def set_default_geometry(self):
        """Set default window position based on device type"""
        match G.device_type:
            case 'joystick':
                x_pos = 160
                y_pos = 130
            case 'pedals':
                x_pos = 110
                y_pos = 100
            case 'collective':
                x_pos = 60
                y_pos = 70
            case 'trimwheel':
                x_pos = 10
                y_pos = 40
                
        self.setGeometry(x_pos, y_pos, 530, 700)

    def open_system_settings_dialog(self):
        try:
            dialog = SystemSettingsDialog(self)
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        except Exception:
            logging.exception("Exception")
        # dialog.exec_()

    def update_settings(self):
        # utils.debug_caller_args('blue')
        self.populate_profile_combo(None) # populate combo with any new profiles
        self.update_craft_text_block(craft=G.settings_mgr.current_aircraft_name, pattern=G.settings_mgr.current_pattern, profile=G.settings_mgr.active_profile)
        self.settings_layout.reload_caller()

    def open_url(self, url):

        # Open the URL
        QDesktopServices.openUrl(QUrl(url))

    def reset_all_effects(self):
        result = QMessageBox.warning(self, "Are you sure?", "*** Only use this if you have effects which are 'stuck' ***\n\n  Proceeding will result in the destruction"
                                                            " of any effects which are currently being generated by the simulator and may result in requiring a restart of"
                                                            " the sim or a new session.\n\n~~ Proceed with caution ~~", QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)

        if result == QMessageBox.StandardButton.Ok:
            try:
                HapticEffect.device.reset_effects()
            except Exception:
                pass



    def update_from_menu(self):
        if self.perform_update(auto=False):
            QCoreApplication.instance().quit()

    def pop_tray_notification(self, title, message, renew_period):
            current_time = time.time()
            notification_key = (title, message)

            # Check if the notification was shown within the specified period
            if notification_key in self.tray_notifications:
                last_shown_time = self.tray_notifications[notification_key]
                if current_time - last_shown_time < renew_period:
                    # Notification was shown recently, do not show again
                    return
            # Show the notification
            icon = QIcon(":/image/vpforceicon.png")
            self.tray_icon.showMessage(title, message, icon)
            # Update the last shown time
            self.tray_notifications[notification_key] = current_time
            self.tray_icon.messageClicked.connect(self.show)


    def update_sim_indicators(self, source, paused=False, error=False, message=None):
        """Runs on every telemetry frame
        """
        if source is None:
            return

        if error:
            self.status_container.set_error(source)
        elif paused:
            self.status_container.set_paused(source)
        else:
            self.status_container.set_running(source)


        if G.master_instance:
            if error:
                # error is true and was previously false.  Set sys tray attributes and pop notification

                self.tray_icon.setIcon(QIcon(':/image/vpforceicon_error.png'))
                self.tray_icon.setToolTip(f"VPforce TelemFFB -- There is an error occurring:\n\n{message}")

                self.status_container.flag_error(message)

                self.pop_tray_notification("Error", message, renew_period= 2)


            elif paused:
                self.tray_icon.setIcon(QIcon(':/image/vpforceicon_paused.png'))
                self.tray_icon.setToolTip(f"VPforce TelemFFB\n{source} is Paused ")

            elif not paused:
                self.tray_icon.setIcon(QIcon(':/image/vpforceicon_run.png'))
                self.tray_icon.setToolTip(f"VPforce TelemFFB\n{source} is Running ")
                # re-show the "current aircraft" label once error cleared





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
            except Exception: pass

        elif index == 1:  # Settings Tab
            self.current_tab_index = 1
            try:
                h = self.tab_sizes[str(index)]['height']
                w = self.tab_sizes[str(index)]['width']
                self.resize(int(w), int(h))
            except Exception:
                pass

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

    def populate_profile_combo(self, new_items: list[str]=None):
        """
        Updates the profile combo box only if its contents differ (excluding 'Add New...').

        Args:
            new_items (list[str]): List of profiles to populate.
        """
        SELECT_LABEL = 'Select...'
        ADD_NEW_LABEL = "Add New..."
        if not self.status_container.cb_selectProfileCombo.isEnabled():
            self.status_container.cb_selectProfileCombo.setEnabled(True)
        if new_items is None:
            new_items = xmlutils.get_available_profiles(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern)

        self.status_container.cb_selectProfileCombo.blockSignals(True)
        self.status_container.cb_selectProfileCombo.clear()

        self.status_container.cb_selectProfileCombo.addItem(SELECT_LABEL)
        for item in new_items:
                self.status_container.cb_selectProfileCombo.addItem(item)

        self.status_container.cb_selectProfileCombo.addItem(ADD_NEW_LABEL)
        index = self.status_container.cb_selectProfileCombo.findText(ADD_NEW_LABEL)
        if index >= 0:
            font = QFont()
            font.setItalic(True)
            self.status_container.cb_selectProfileCombo.setItemData(index, font, role=Qt.ItemDataRole.FontRole)

        self.status_container.cb_selectProfileCombo.setCurrentIndex(0)
        self.status_container.cb_selectProfileCombo.blockSignals(False)

    def on_profile_change(self, index):
        # utils.debug_caller_args("red")
        """
        Call to xmlutils to update the profile mapping for the aircraft when the user changes the profile
        If the "add new" option is selected, pop a dialog asking for the new profile name.  If the user chooses
        the "make active' option, make a further call to make the new profile the active one
        Args:
            index: The selected index in the combobox.

        Returns: Nothing

        """
        if not G.master_instance:
            return
        if index == 0:
            return

        profile_name = self.status_container.cb_selectProfileCombo.itemText(index)

        self.status_container.cb_selectProfileCombo.blockSignals(True)
        self.status_container.cb_selectProfileCombo.setCurrentIndex(0)
        self.status_container.cb_selectProfileCombo.blockSignals(False)

        sim = G.settings_mgr.current_sim
        cls = G.settings_mgr.current_class
        pattern = G.settings_mgr.current_pattern

        cur_txt = xmlutils.get_active_profile_for_model(sim, cls, pattern)
        if profile_name == 'Add New...':
            ## Quickly block signals and set it back to "Select".. then kick off new profile dialog


            dlg = NewProfileDialog(self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                # user canceled
                return
            new_profile, make_active, clone, profile_to_clone = dlg.get_data()
            if not new_profile:
                # user did not enter a string
                return
            # write new profile entry to user config file
            if clone:
                xmlutils.clone_profile_entry(
                    sim=G.settings_mgr.current_sim,
                    cls=G.settings_mgr.current_class,
                    src_model=G.settings_mgr.current_pattern,
                    src_profile=profile_to_clone,
                    dst_profile=new_profile
                )
            else:
                xmlutils.add_new_profile(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, new_profile)

            if make_active:
                # change the profileMapping for this aircraft to the new profile
                xmlutils.update_active_profile_entry(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, new_profile)
                G.settings_mgr.update_state_vars(active_profile=new_profile)

            if G.telem_manager.timed_out:
                self.populate_profile_combo(xmlutils.get_available_profiles(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern))
                self.update_craft_text_block(craft=G.settings_mgr.current_aircraft_name, pattern=G.settings_mgr.current_pattern, profile=G.settings_mgr.active_profile)
        else:
            xmlutils.update_active_profile_entry(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, profile_name)
            if G.telem_manager.timed_out:
                self.update_craft_text_block(craft=G.settings_mgr.current_aircraft_name, pattern=G.settings_mgr.current_pattern, profile=profile_name)
        if G.telem_manager.timed_out:
            self.settings_layout.reload_caller()

    def on_telemetry_timeout(self):
        self.lbl_effects_data.setText("")
        if not self.error_state:
            # Only set icon to pause if error condition is not present when pausing
            self.update_sim_indicators(G.telem_manager.getTelemValue('src'), paused=True)
        self.telemetry_timed_out = True

    def on_update_telemetry(self, datadict: dict):
        if utils.millis() - self.last_telemetry_refresh < 50:
            return
        self.last_telemetry_refresh = utils.millis()

        data = OrderedDict(sorted(datadict.items()))  # Alphabetize telemetry data
        keys = data.keys()
        try:
            # use ordereddict and move some telemetry to the top
            # Items to move to the beginning (reverse order)
            if 'SimconnectCategory' in keys: data.move_to_end('SimconnectCategory', last=False)
            if 'AircraftClass' in keys: data.move_to_end('AircraftClass', last=False)
            if 'msfs_vers' in keys: data.move_to_end('msfs_vers', last=False)
            if 'src' in keys: data.move_to_end('src', last=False)
            if 'N' in keys: data.move_to_end('N', last=False)
            if 'FFBType' in keys: data.move_to_end('FFBType', last=False)
            if 'perf' in keys: data.move_to_end('perf', last=False)
            if 'avgFrameTime' in keys: data.move_to_end('avgFrameTime', last=False)
            if 'maxFrameTime' in keys: data.move_to_end('maxFrameTime', last=False)
            if 'frameTimes' in keys: data.move_to_end('frameTimes', last=False)
            if 'T' in keys: data.move_to_end('T', last=False)

            # Items to move to the end
        except Exception:
            pass

        try:

            telem_items = ""
            # Parse filter once per update
            raw = (self.telem_filter.text() or "")
            tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
            for k, v in data.items():

                # check for msfs and debug mode (alt-d pressed), change to simvar name
                if self.show_simvars:
                    if data["src"] == "MSFS":
                        s = G.telem_manager.simconnect.get_var_name(k)
                        # s = simvarnames.get_var_name(k)
                        if s is not None:
                            k = s

                # Apply simple OR filtering against the key only
                if tokens:
                    k_cf = str(k).lower()
                    if not any(tok in k_cf for tok in tokens):
                        continue

                if isinstance(v, float):
                    telem_items += f"{k}: {v:.3f}\n"
                else:
                    if isinstance(v, list):
                        v = "[" + ", ".join([f"{x:.3f}" if isinstance(x, float) else str(x) if x is not None else "None" for x in v]) + "]"
                    telem_items += f"{k}: {v}\n"

            active_effects = ""
            active_settings = []

            if G.master_instance and G.current_device_config_scope != G.device_type:
                dev = G.current_device_config_scope
                active_effects = G.ipc_instance._ipc_telem_effects.get(f'{dev}_active_effects', '')
                active_settings = G.ipc_instance._ipc_telem_effects.get(f'{dev}_active_settings', [])
            else:
                effect : HapticEffect
                for key, effect in G.effects.dict.items():
                    if effect.started:
                        descr, settingname = utils.EffectTranslator.get_translation(effect.name)
                        
                        descr = "ID:{} {}".format(effect.id, descr)
                        
                        active_effects += descr + "\n"
                        if settingname not in active_settings and settingname != '':
                            active_settings.append(settingname)

            if G.child_instance:
                child_effects = str(G.effects.dict.keys())
                if child_effects:
                    G.ipc_instance.send_ipc_effects(active_effects, active_settings)

            window_mode = self.tab_widget.currentIndex()
            # update slider colors
            pct_max_a = data.get('_pct_max_a', 0)
            pct_max_e = data.get('_pct_max_e', 0)
            pct_max_r = data.get('_pct_max_r', 0)
            pct_steer_f = data.get('_pct_steer_f', 0)
            qcolor_green = QColor("#17c411")
            qcolor_grey = QColor("grey")
            if window_mode == 1:
                sliders = self.findChildren(NoWheelSlider)
                for my_slider in sliders:
                    slidername = my_slider.objectName().replace('sld_', '')
                    my_slider.blockSignals(True)

                    for a_s in active_settings:
                        if a_s in slidername:
                            my_slider.setHandleColor("#17c411")
                            break
                        else:
                            my_slider.setHandleColor(vpf_purple)
                    my_slider.blockSignals(False)

                n_sliders = self.findChildren(NoWheelNumberSlider)
                for my_slider in n_sliders:
                    """This section updates the labels which are on the "NoWheelNumberSlider elements that reflect
                    the current value of the coeff % values"""
                    slidername = my_slider.objectName().replace('sld_', '')
                    my_slider.blockSignals(True)

                    if slidername == 'max_elevator_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_e)
                        my_slider.setHandleColor(new_color.name(), f"{int(pct_max_e *100)}%")
                        # print(int(pct_max_e * 100))
                        my_slider.blockSignals(False)
                        continue
                    if slidername == 'max_aileron_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_a)
                        my_slider.setHandleColor(new_color.name(), f"{int(pct_max_a * 100)}%")
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    if slidername == 'max_rudder_coeff':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_max_r)
                        my_slider.setHandleColor(new_color.name(), f"{int(pct_max_r * 100)}%")
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    if slidername == 'steering_friction_intensity':
                        new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct_steer_f)
                        my_slider.setHandleColor(new_color.name(), f"{int(pct_steer_f * 100)}%")
                        # print(new_color)
                        my_slider.blockSignals(False)
                        continue
                    for a_s in active_settings:
                        if a_s in slidername:
                            my_slider.setHandleColor("#17c411")
                            break
                        else:
                            my_slider.setHandleColor(vpf_purple)
                    my_slider.blockSignals(False)

            is_paused = max(data.get('SimPaused', 0), data.get('Parked', 0))
            error_cond = data.get('error', None)

            if error_cond is None:  # no 'error' key in telemetry
                if self.telemetry_timed_out or self.error_state:  # only set status to run if previously debug_timed out or error status was true
                    if not self.error_clean_counter:  # avoid flapping due to ipc_telem not populating on every frame due to thread timing between instances
                        self.update_sim_indicators(data.get('src'), paused=False)
                        self.error_state = False
                        self.telemetry_timed_out = False
                        self.status_container.clear_error()
                    else:
                        self.error_clean_counter -= 1  # decrement the counter so that it will reach 0 once error is *truly* cleared
            elif error_cond is not None:

                self.error_clean_counter = 5
                if not self.error_state:  # only set error status once when there is error cond but state is not yet true
                    self.update_sim_indicators(data.get('src'), error=True, message=error_cond)
                    logging.error(error_cond)
                    self.error_state = True




            shown_pattern = G.settings_mgr.current_pattern
            if G.settings_mgr.current_pattern == '' and data.get('N', '') != '':
                shown_pattern = 'Using defaults'
                new_sim = data.get('src', None)
                new_aircraft = data.get('N', None)
                new_class = G.settings_mgr.current_class
                if G.master_instance:
                    if not self.new_craft_button.isVisible():
                        self.new_craft_button.clicked.connect(lambda: self.show_new_aircraft_wizard(manual=False,sim=new_sim,cls=new_class,name=new_aircraft))
                        self.new_craft_button.show()

                    if not data.get('STOP', False):
                        if not self.new_craft_notification_sent:

                            self.pop_tray_notification(
                                "** New Aircraft Found **",
                                f"No profile was found for the aircraft\n{data.get('N')}\n\nClick to open TelemFFB.",
                                10,
                            )
                            self.new_craft_notification_sent = True
                            self.show_new_aircraft_wizard(manual=False, sim=data.get('src', None), cls=G.settings_mgr.current_class, name=data.get('N', ''))



            else:
                self.new_craft_button.hide()
                self.new_craft_notification_sent = False

            # Update the status labels and profile selection box
            self.status_container.set_fullname(data['N'])
            ap = G.settings_mgr.active_profile
            active_profile = xmlutils.get_active_profile_for_model(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern)

            self.update_craft_text_block(pattern=shown_pattern, profile=active_profile)

            if window_mode == 0:
                self.lbl_telem_data.setText(telem_items)
                self.lbl_effects_data.setText(active_effects)

        except Exception:
            logging.exception("Exception")

    def update_craft_text_block(self, craft=None, pattern=None, profile=None):
        if craft is None:
            craft = G.settings_mgr.current_aircraft_name
        if pattern is None:
            pattern = G.settings_mgr.current_pattern
        if profile is None:
            profile = G.settings_mgr.active_profile
        self.status_container.cur_craft_label.setText(craft)
        self.status_container.cur_pattern_label.setText(pattern)
        self.status_container.active_profile_label.setText(profile)

    def new_ac_wizard_finished(self):
        self.new_craft_button.setVisible(False)
        self.settings_layout.reload_layout(None)


    def perform_update(self, auto=True):
        if G.release_version:
            return False

        ignore_auto_updates = G.system_settings.get('ignoreUpdate', False)
        if not auto:
            ignore_auto_updates = False
        update_ans = QMessageBox.StandardButton.No
        proceed_ans = QMessageBox.StandardButton.Cancel
        try:
            updater_execution_path = os.path.join(utils.get_script_path(), 'updater.exe')
            if os.path.exists(updater_execution_path):
                os.remove(updater_execution_path)
        except Exception as e:
            logging.error(f'Error in perform_update: {e}')

        is_exe = getattr(sys, 'frozen', False)  # TODO: Make sure to swap these comment-outs before build to commit - this line should be active, next line should be commented out
        # is_exe = True
        if G.child_instance: return False
        if ignore_auto_updates: return False
        if not is_exe: return False

        if self._update_available:
            update_ans = QMessageBox.StandardButton.Yes
            if auto:
                update_ans = QMessageBox.information(self, "Update Available!!",
                                                     f"A new version of TelemFFB is available ({self.latest_version}).\n\nWould you like to automatically download and install it now?\n\nYou may also update later from the Utilities menu, or the\nnext time TelemFFB starts.\n\n~~ Note ~~ If you no longer wish to see this message on startup,\nyou may enable `ignore_auto_updates` in your user config.\n\nYou will still be able to update via the Utilities menu",
                                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

            if update_ans == QMessageBox.StandardButton.Yes:
                proceed_ans = QMessageBox.information(self, "TelemFFB Updater",
                                                      f"TelemFFB will now exit and launch the updater.\n\nPress OK to continue",
                                                      QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)

            if proceed_ans == QMessageBox.StandardButton.Ok:
                updater_execution_path = os.path.join(utils.get_script_path(), 'updater.exe')
                shutil.copy(sys.argv[0], updater_execution_path)

                # Copy the updater executable with forced overwrite

                call = [updater_execution_path, "--current_version", utils.get_version()] + sys.argv[1:]
                subprocess.Popen(call, cwd=utils.get_install_path())
                if auto:
                    for child_widget in self.findChildren(QMessageBox):
                        child_widget.reject()
                    QTimer.singleShot(250, exit_application)
                else:
                    return True

        return False



