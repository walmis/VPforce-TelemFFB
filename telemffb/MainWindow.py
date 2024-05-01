import json
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
from collections import OrderedDict
from datetime import datetime

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import QCoreApplication, Qt, QTimer, QUrl
from PyQt5.QtGui import (QColor, QCursor, QDesktopServices, QIcon,
                         QKeySequence, QPixmap)
from PyQt5.QtWidgets import (QAction, QApplication, QButtonGroup, QCheckBox,
                             QComboBox, QFrame, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPushButton, QScrollArea, QShortcut, QTabWidget,
                             QToolButton, QVBoxLayout, QWidget)

import telemffb.globals as G
import telemffb.utils as utils
import telemffb.xmlutils as xmlutils
from telemffb.config_utils import autoconvert_config
from telemffb.ConfiguratorDialog import ConfiguratorDialog
from telemffb.custom_widgets import (ClickLogo, InstanceStatusRow, NoKeyScrollArea, NoWheelSlider,
                                     SimStatusLabel, vpf_purple)
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.SCOverridesEditor import SCOverridesEditor
from telemffb.SettingsLayout import SettingsLayout
from telemffb.settingsmanager import UserModelDialog
from telemffb.sim.aircraft_base import effects
from telemffb.telem.SimTelemListener import SimTelemListener
from telemffb.SystemSettingsDialog import SystemSettingsDialog
from telemffb.TeleplotSetupDialog import TeleplotSetupDialog
from telemffb.utils import exit_application, overrides

class MainWindow(QMainWindow):
    
    def __init__(self):
        super().__init__()

        self.show_simvars = False

        self.latest_version = None
        self._update_available = None

        self.show_new_craft_button = False
        # Get the absolute path of the script's directory
        # script_dir = os.path.dirname(os.path.abspath(__file__))
        doc_url = 'https://vpforcecontrols.com/downloads/VPforce_Rhino_Manual.pdf'

        if G.release_version:
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
        version = utils.get_version()
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
        if not G.release_version:
            utilities_menu.addAction(self.update_action)
        self.update_action.setDisabled(True)

        download_action = QAction('Download Other Versions', self)
        download_action.triggered.connect(lambda: self.open_url(dl_url))
        utilities_menu.addAction(download_action)

        self.reset_user_config_action = QAction('Reset User Config', self)
        self.reset_user_config_action.triggered.connect(self.reset_user_config)
        utilities_menu.addAction(self.reset_user_config_action)

        self.vpconf_action = QAction("Launch VPforce Configurator", self)
        self.vpconf_action.triggered.connect(lambda: utils.launch_vpconf(HapticEffect.device.serial))
        utilities_menu.addAction(self.vpconf_action)

        # Add settings converter
        _legacy_override_file = utils.get_legacy_override_file()
        if _legacy_override_file is not None:
            convert_settings_action = QAction('Convert legacy user config to XML', self)
            _legacy_config_file = utils.get_resource_path('config.ini')
            convert_settings_action.triggered.connect(lambda: autoconvert_config(self, _legacy_config_file, _legacy_override_file))
            utilities_menu.addAction(convert_settings_action)

        if G.master_instance and G.system_settings.get('autolaunchMaster', 0):
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
        if G.master_instance and G.system_settings.get('autolaunchMaster', 0):
            self.child_log_menu = log_menu.addMenu('Open Child Logs')

            self.log_action = {}
            for d in ["joystick", "pedals", "collective"]:
                if d in G.launched_instances:
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

        logo_status_layout = QHBoxLayout()

        # Add a label for the image
        # Construct the absolute path of the image file
        self.logo_stack = QGroupBox()
        self.vpflogo_label = QLabel(self.logo_stack)
        self.devicetype_label = ClickLogo(self.logo_stack)
        self.devicetype_label.clicked.connect(self.device_logo_click_event)
        pixmap = QPixmap(":/image/vpforcelogo.png")
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

           
        # Add the image labels to the layout
        logo_status_layout.addWidget(self.logo_stack, alignment=Qt.AlignVCenter | Qt.AlignLeft)

        rh_status_area = QWidget()
        rh_status_layout = QVBoxLayout()

        sim_status_area = QWidget()
        status_layout = QHBoxLayout()

        self.dcs_label_icon = SimStatusLabel("DCS")
        self.il2_label_icon = SimStatusLabel("IL2")
        self.msfs_label_icon = SimStatusLabel("MSFS")
        self.xplane_label_icon = SimStatusLabel("X-Plane")

        status_layout.addWidget(self.dcs_label_icon)
        status_layout.addWidget(self.il2_label_icon)
        status_layout.addWidget(self.msfs_label_icon)
        status_layout.addWidget(self.xplane_label_icon)

        self.label_icons = {
            'DCS': self.dcs_label_icon,
            'IL2': self.il2_label_icon,
            'MSFS': self.msfs_label_icon,
            'XPLANE': self.xplane_label_icon,
        }

        def on_sims_changed(sim : SimTelemListener):
            self.label_icons[sim.name].enabled = sim.started
            self.refresh_telem_status()

        def on_event(event):
            if event[0] == "Stop":
                src = G.telem_manager.getTelemValue("src")
                if src in self.label_icons:
                    lb = self.label_icons[src]
                    lb.active = False
                    lb.paused = False
                self.refresh_telem_status()

        G.telem_manager.eventReceived.connect(on_event)

        G.sim_listeners.simStarted.connect(on_sims_changed)
        G.sim_listeners.simStopped.connect(on_sims_changed)

        status_layout.setAlignment(Qt.AlignRight)

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
        self.configmode_group.buttonClicked.connect(self.change_config_scope)
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
        self.lbl_telem_data = QLabel()

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
        if G.master_instance:
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
        # def update_log_widget(log_line):
        #     cursor = self.log_widget.textCursor()
        #     cursor.movePosition(QTextCursor.End)
        #     cursor.insertText(log_line)
        #     self.log_widget.setTextCursor(cursor)
        #     self.log_widget.ensureCursorVisible()
        # self.log_tail_thread.log_updated.connect(update_log_widget)
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
        self.tab_widget.currentChanged.connect(self.switch_window_view)


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

        self.instance_status_row = InstanceStatusRow()
        self.instance_status_row.changeConfigScope.connect(self.change_config_scope)
        self.instance_status_row.hide()
        layout.addWidget(self.instance_status_row)          

        version_row_layout = QHBoxLayout()
        self.version_label = QLabel()

        if G.release_version:
            status_text = f"Release Version {utils.get_version()}"
        else:
            status_text = "UNKNOWN"

        self.version_label.setText(f'Version Status: {status_text}')
        self.version_label.setOpenExternalLinks(True)

        self.firmware_label = QLabel()
        self.firmware_label.setText(f'Rhino Firmware: {HapticEffect.device.get_firmware_version()}')

        self.version_label.setAlignment(Qt.AlignLeft)
        self.firmware_label.setAlignment(Qt.AlignRight)
        version_row_layout.addWidget(self.version_label)
        version_row_layout.addWidget(self.firmware_label)

        version_row_layout.setAlignment(Qt.AlignBottom)
        layout.addLayout(version_row_layout)

        # self.test_button = QPushButton("SEND TEST MESSAGE")
        # self.test_button.clicked.connect(lambda: send_test_message())

        # layout.addWidget(self.test_button)
        G.telem_manager.telemetryReceived.connect(self.on_update_telemetry)
        G.telem_manager.telemetryTimeout.connect(self.on_telemetry_timeout)
        G.telem_manager.aircraftUpdated.connect(self.update_settings)

        central_widget.setLayout(layout)

        # Load Stored Geomoetry
        self.load_main_window_geometry()
        shortcut = QShortcut(QKeySequence('Alt+D'), self)
        shortcut.activated.connect(self.add_debug_menu)
        # try:
        #     if utils.get_reg("debug"):
        #         self.add_debug_menu()
        # except Exception:
        #     pass

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

        self.lbl_telem_data.setText(
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
        debug_menu = self.menu.addMenu("Debug")
        aircraft_picker_action = QAction('Enable Manual Aircraft Selection', self)
        aircraft_picker_action.triggered.connect(lambda: self.toggle_settings_window(dbg=True))
        debug_menu.addAction(aircraft_picker_action)

        teleplot_action = QAction("Teleplot Setup", self)
        def do_open_teleplot_setup_dialog():
            dialog = TeleplotSetupDialog(self)
            dialog.exec_()
        teleplot_action.triggered.connect(do_open_teleplot_setup_dialog)
        debug_menu.addAction(teleplot_action)

        show_simvar_action = QAction("Show simvar in telem window", self)
        def do_toggle_simvar_telemetry():
            self.show_simvars = not self.show_simvars
            show_simvar_action.setChecked(self.show_simvars)

        show_simvar_action.triggered.connect(do_toggle_simvar_telemetry)
        show_simvar_action.setCheckable(True)
        debug_menu.addAction(show_simvar_action)

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


    def update_child_status(self, device, status):
        self.instance_status_row.set_status(device, status)

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
        self.show_device_logo()
        self.enable_device_logo_click(True)

        #self.devicetype_label.hide()
        current_title = self.windowTitle()
        new_title = f"** MASTER INSTANCE ** {current_title}"
        self.setWindowTitle(new_title)
        self.instance_status_row.show()
        if "joystick" in G.launched_instances:
            self.instance_status_row.joystick_status_icon.show()
        if "pedals" in G.launched_instances:
            self.instance_status_row.pedals_status_icon.show()
        if "collective" in G.launched_instances:
            self.instance_status_row.collective_status_icon.show()


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
        def check_instance(name):
            return name in G.launched_instances or G.device_type == name
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
                self.version_label.setText('Version Status: <b>Non release - Modified Source</b>')

        elif vers == 'needsupdate':
            self.version_label.setText('Version Status: <b>Out of Date Source - Git pull needed</b>')

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

        if G.master_instance:
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

    @overrides(QWidget)
    def closeEvent(self, event):
        # Perform cleanup before closing the application
        if G.child_instance:
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
        except Exception:
            logging.exception("Exception")
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
                HapticEffect.device.reset_effects()
            except Exception:
                pass

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

        except Exception:
            traceback.print_exc()

    def show_user_model_dialog(self):
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


    def update_sim_indicators(self, source, paused=False, error=False):
        """Runs on every telemetry frame
        """
        if source is None:
            return

        ic = self.label_icons[source]
        ic.error = error
        ic.paused = paused
        ic.active = True



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
            except Exception:
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
    
    def on_telemetry_timeout(self):
        self.lbl_effects_data.setText("")

    def on_update_telemetry(self, datadict: dict):

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
        except Exception:
            pass

        try:

            items = ""
            for k, v in data.items():
                # check for msfs and debug mode (alt-d pressed), change to simvar name
                if self.show_simvars:
                    if data["src"] == "MSFS":
                        s = G.telem_manager.simconnect.get_var_name(k)
                        # s = simvarnames.get_var_name(k)
                        if s is not None:
                            k = s
                if isinstance(v, float):
                    items += f"{k}: {v:.3f}\n"
                else:
                    if isinstance(v, list):
                        v = "[" + ", ".join([f"{x:.3f}" if not isinstance(x, str) else x for x in v]) + "]"
                    items += f"{k}: {v}\n"

            active_effects = ""
            active_settings = []

            if G.master_instance and self._current_config_scope != G.device_type:
                dev = self._current_config_scope
                active_effects = G.ipc_instance._ipc_telem_effects.get(f'{dev}_active_effects', '')
                active_settings = G.ipc_instance._ipc_telem_effects.get(f'{dev}_active_settings', [])
            else:
                effect : HapticEffect
                for key, effect in effects.dict.items():
                    if effect.started:
                        descr, settingname = utils.EffectTranslator.get_translation(effect.name)
                        if descr not in active_effects:
                            active_effects = '\n'.join([active_effects, descr])
                        if settingname not in active_settings and settingname != '':
                            active_settings.append(settingname)

            if G.child_instance:
                child_effects = str(effects.dict.keys())
                if child_effects:
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

        except Exception:
            logging.exception("Exception")

    def perform_update(self, auto=True):
        if G.release_version:
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
        if G.child_instance: return False
        if ignore_auto_updates: return False
        if not is_exe: return False

        if self._update_available:
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

                call = [updater_execution_path, "--current_version", utils.get_version()] + sys.argv[1:]
                subprocess.Popen(call, cwd=utils.get_install_path())
                if auto:
                    for child_widget in self.findChildren(QMessageBox):
                        child_widget.reject()
                    QTimer.singleShot(250, exit_application)
                else:
                    return True

        return False
