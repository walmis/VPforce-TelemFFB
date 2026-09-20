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


import html
import inspect
import json
import logging
import os
import re
import shutil
import sys
import traceback
from collections import OrderedDict
from datetime import datetime
from typing import override

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt, QUrl, pyqtSlot
from PyQt6.QtGui import (QColor, QCursor, QDesktopServices, QIcon,
                         QKeySequence, QPixmap, QAction, QShortcut, QFontDatabase)
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                             QFrame, QGroupBox,
                             QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPushButton, QTabWidget,
                             QToolButton, QVBoxLayout, QWidget, QSizePolicy,
                             QDialog, QStatusBar)

import telemffb.globals as G
from telemffb import match_history
from telemffb.ui.dialogs.ProfileOfferDialog import ProfileOfferDialog
import telemffb.utils as utils
import telemffb.xmlutils as xmlutils
from telemffb.app_events import events as app_events
# from telemffb.config_utils import autoconvert_config
from telemffb.ui.dialogs.ConfiguratorDialog import ConfiguratorDialog
from telemffb.ui.theme.tokens import ACTIVE_GREEN
from telemffb.ui.widgets.custom_widgets import AppStatusWidget, InstanceStatusRow, NoKeyScrollArea, \
    SimStatusLabel, DetachedTabWindow, ExceptionStatusWidget
from telemffb.ui.panels.DevicePanel import DeviceIconPanel, device_status_state
from telemffb.ui.panels.PromptStack import PromptStack
from telemffb.ui.panels.OfflineEditorPanel import OfflineEditorPanel
from telemffb.ui.panels.MonitorPanel import MonitorPanel
from telemffb.ui.panels.HeaderPanel import HeaderPanel
from telemffb.state.app_state import Notice, NEW_CRAFT_PRIORITY, PROFILE_CHANGE_PRIORITY, TRIM_CAL_PRIORITY
from telemffb.state.sim_status import SimStatusTracker
from telemffb.ui.dialogs.ExceptionViewerDialog import ExceptionViewerDialog
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.ui.dialogs.SCOverridesEditor import SCOverridesEditor
from telemffb.ui.dialogs.ProfileNotesDialog import ProfileNotesDialog
from telemffb.ui.widgets.SettingsLayout import SettingsLayout
from telemffb.ui.widgets.CornerLogo import CornerLogo
from telemffb.ui.widgets.DeviceViewToggle import DeviceViewToggle
from telemffb.ui.widgets.TabHeaderBar import TabHeaderBar
from telemffb.preview.engine import PREVIEW_SPECS
from telemffb.preview.controller import EffectPreviewController
# from telemffb.ui.dialogs.UserModelDialog import UserModelDialog
from telemffb.ui.dialogs.NewAircraftWizard import NewAircraftWizard
from telemffb.telem.SimTelemListener import SimTelemListener
from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
from telemffb.ui.dialogs.TeleplotSetupDialog import TeleplotSetupDialog
from telemffb.ui.dialogs.ProfileManager import ProfileManagerDialog, NewProfileDialog
from telemffb.utils import exit_application
from telemffb.ui.menus import MainMenu
from telemffb.ui.tray import TrayController
from telemffb.ui.updates import UpdateChecker

class MainWindow(QMainWindow):
    #: Number-sliders whose handle shows a live force readout: setting name
    #: -> the telemetry key its aircraft code publishes (fraction 0..1 of
    #: the relevant full scale, so 100% on the handle means clipping/max).
    N_SLIDER_LIVE_KEYS = {
        'max_elevator_coeff': '_pct_max_e',
        'max_aileron_coeff': '_pct_max_a',
        'max_rudder_coeff': '_pct_max_r',
        'steering_friction_intensity': '_pct_steer_f',
        'tap_spring_gain_x': '_pct_tap_x',
        'tap_spring_gain_y': '_pct_tap_y',
        'tap_effect_constant_gain': '_pct_tap_const',
        'tap_effect_periodic_gain': '_pct_tap_periodic',
        'tap_effect_damper_gain': '_pct_tap_damper',
        'tap_effect_inertia_gain': '_pct_tap_inertia',
        'tap_effect_friction_gain': '_pct_tap_friction',
    }
    def __init__(self):
        super().__init__()
        self.preview = EffectPreviewController(self)   # effect previews, see preview_controller
        self.tray = TrayController(self)
        self.updates = UpdateChecker(self)
        self.new_craft_notification_sent = False
        # The new-craft prompt's click target: the (sim, cls, name) the
        # prompt currently names.  Re-captured (and the prompt rebuilt)
        # whenever a *different* unmatched aircraft arrives, so the text
        # and the click always follow the current one; unchanged frames
        # construct nothing (see on_update_telemetry and PromptStack).
        self._new_craft_prompt_active = False
        self._new_craft_target = None  # (sim, cls, name) the prompt names
        self._profile_change_prompt_active = False  # for the same one-shot-toast gating
        # Error-onset/hold/clear state machine + timed-out flag - reports
        # to G.app_state.set_sim_status; HeaderPanel/TrayController.bind()
        # apply it. See telemffb/state/sim_status.py.
        self.sim_status = SimStatusTracker(G.app_state, G.exception_tracker)
        self.last_telemetry_refresh = utils.millis()
        self.profile_mgr_dialog = None


        """ Add font used for settngs area group labels """

        QFontDatabase.addApplicationFont(':/image/BlackOpsOne-Regular.ttf')

        # Get the absolute path of the script's directory
        # script_dir = os.path.dirname(os.path.abspath(__file__))
        doc_url = 'https://docs.vpforce.eu/telemffb/'
        if G.release_version:
            dl_url = 'https://github.com/walmis/VPforce-TelemFFB/releases'
        else:
            dl_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=D'

        # notes_url = os.path.join(script_dir, '_RELEASE_NOTES.txt')
        notes_url = utils.get_resource_path('_RELEASE_NOTES.txt')
        G.current_device_config_scope = G.device_type
        G.app_state.set_scope(G.device_type)
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

        """ Build the menu bar - System/Profiles/Utilities/Window/Log/
        Help menus and (Alt+D / debug key) the Debug menu. See
        telemffb/ui/menus.py """

        self.main_menu = MainMenu(self)
        self.main_menu.build()

        """ The window below the menu bar is a left column (Active
        Devices) and a right column (Application Status, prompts, offline
        editor, tabs). The two are wired together at the end of __init__,
        once every right-column piece has been built. """

        content_hbox = QHBoxLayout()
        # The space under the menu bar, above both columns, so the Active
        # Devices frame and the Application Status box start level.
        content_hbox.setContentsMargins(0, 10, 0, 0)
        content_hbox.setSpacing(10)
        right_column_layout = QVBoxLayout()

        """ Create the header - the Application Status box, see
        telemffb/ui/panels/HeaderPanel.py. The panel owns construction and
        the AppState-driven vpconf/gain-override indicators (bind()); the
        signal connections below are the ones that need MainWindow's own
        methods/dialogs. """

        self.header_panel = HeaderPanel(parent=self)
        self.header_panel.profile_chosen.connect(self.on_profile_change)
        self.header_panel.profile_notes_clicked.connect(self.open_profile_notes_dialog)
        self.header_panel.split_profile_clicked.connect(self.split_loaded_aircraft_profile)
        self.header_panel.bind(G.app_state)
        # The app logo: over the corner, down to the status box, in no layout.
        self.corner_logo = CornerLogo(self, below=self.header_panel.status_group,
                                      menubar=self.main_menu.menu, logo_path=G.vpf_logo)
        self.tray.bind(G.app_state)


        """ Create Device Panel - pinned to the left edge of the window.
        Hidden while the Hide tab is active, or by user preference when
        there are multiple devices, or always when there is only one (see
        _sync_devices_display() / switch_window_view()). """

        # Every tab page's header bar owns a compact device row; they are
        # collected here so _sync_mini_device_panel() can mirror the full
        # panel onto all of them at once. Populated as the pages are built
        # below, which is after the full panel - hence the empty list
        # rather than a late attribute.
        self._mini_device_panels = []
        self._mini_device_toggles = {}  # mini panel -> the view toggle beside it

        self.device_groupbox = QGroupBox("Active Devices")

        # Expanding vertically: the frame runs the full height of the
        # window, from the Application Status box down past the tabs,
        # rather than stopping at the last icon.
        self.device_groupbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        device_groupbox_layout = QVBoxLayout()
        device_groupbox_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.device_panel = DeviceIconPanel()
        # Keeps the mini device row in step with the full panel - device
        # list, active device, and every device's icon/label/status -
        # however it was changed.
        self.device_panel.changed.connect(self._sync_mini_device_panel)
        device_groupbox_layout.addWidget(self.device_panel)
        self.device_groupbox.setLayout(device_groupbox_layout)
        # Switches to the compact icon rows; theirs switch back (see
        # _register_mini_device_panel). The frame only ever shows with
        # more than one device configured, so its button needs no gating.
        self.device_frame_toggle = DeviceViewToggle(frame_shown=True)
        self.device_frame_toggle.pin_to_title(self.device_groupbox)
        self.device_frame_toggle.clicked.connect(lambda: self._set_devices_frame_preference(False))
        self._add_device_view_context_menu(self.device_groupbox)
        # Stays hidden until _sync_devices_display() runs with a populated
        # device panel (master instances populate it later, in
        # setup_master_instance()) - otherwise an empty frame flashes
        # before then.
        self.device_groupbox.hide()

        if not G.master_instance:
            # A child instance only ever drives its own device - showing
            # the other three roles (which it has no visibility into) as
            # ghost icons would be misleading, and briefly flashes an
            # odd-looking little window on startup for any child whose
            # own window isn't suppressed (e.g. trimwheel, which has no
            # startHeadless/startMin default). Master-only.
            self.device_panel.set_devices([G.device_type])
            self.device_panel.set_device_status(G.device_type, device_status_state())
            self.device_panel.set_active_device(G.device_type)
            self.refresh_device_labels()


        def on_sims_changed(sim: SimTelemListener):
            self.header_panel.update_enabled_sims(sim.name, sim.started)
            self.monitor_panel.refresh_waiting_status()


        """ Connect sim listeners to sim change function """

        G.sim_listeners.simStarted.connect(on_sims_changed)
        G.sim_listeners.simStopped.connect(on_sims_changed)


        """ Add the header to the right column, above the prompt stack -
        the Active Devices frame runs the full height of the window beside
        it, so the status box starts where the tabs below it do. """

        right_column_layout.addWidget(self.header_panel)


        """ Create the prompt stack - the new-aircraft, trim-calibration-
        discovery and matching-profile-offer "pill" prompts. It collapses
        to 0 height once none of the three are active, so it does not push
        the Offline Editor Setup frame below out of alignment with the
        Active Devices frame beside it. See telemffb/ui/panels/PromptStack.py
        and telemffb/state/app_state.py (Notice / AppState.set_prompt) -
        producers declare what should be showing, PromptStack renders it. """

        self.prompt_stack = PromptStack()
        self.prompt_stack.activated.connect(self._on_prompt_activated)
        self.prompt_stack.bind(G.app_state)

        """ Add the prompt stack to the main layout """

        right_column_layout.addWidget(self.prompt_stack)


        """ Create the offline editor panel (sim/class/aircraft/profile
        selectors used to edit config without a live sim connected) and add
        it to the right column.  See telemffb/ui/panels/OfflineEditorPanel.py
        - the panel starts hidden and is shown/hidden by toggle_offline_mode
        below. """

        self.offline_editor = OfflineEditorPanel(parent=self, mainwindow=self)
        right_column_layout.addWidget(self.offline_editor)


        """ Create tab widget where monitor/settings/hide will live """

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setObjectName('mainTabs')  # styled in styles.py

        # Offline editing for the aircraft that is loaded right now.  The
        # other two entry points (Profiles menu, the empty-settings notice)
        # open the editor with nothing selected; this one lands on the
        # live aircraft's sim / class / model / profile, which is what you
        # want when you have just been flying it and want to tune or
        # preview its effects.  Lives in the tab bar's spare corner, shown
        # only while an aircraft is loaded and the editor is not open.
        self.offline_editor_button = QPushButton('Offline/Preview Mode')
        self.offline_editor_button.setObjectName('offline_editor_button')
        self.offline_editor_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.offline_editor_button.setVisible(False)
        self.offline_editor_button.clicked.connect(self.enter_offline_for_live_aircraft)
        self.tab_widget.setCornerWidget(self.offline_editor_button, Qt.Corner.TopRightCorner)

        """ Add the tab widget to the right column """

        right_column_layout.addWidget(self.tab_widget, stretch=1)
        right_column_layout.setSpacing(0)

        """ Wire the left (Active Devices) and right columns together """

        content_hbox.addWidget(self.device_groupbox)
        content_hbox.addLayout(right_column_layout, 1)
        layout.addLayout(content_hbox, stretch=1)
        self._content_hbox = content_hbox  # its spacing is part of what the frame costs in width


        """ Create the monitor tab: telemetry + active-effects display """

        self.monitor_panel = MonitorPanel(parent=self.tab_widget, mainwindow=self)
        self._register_mini_device_panel(self.monitor_panel.header_bar)
        if G.master_instance:
            self.monitor_panel.set_effects_scope_label(G.current_device_config_scope)

        """ Add the monitor tab object to the tab widget"""

        self.tab_widget.addTab(self.monitor_panel, "Monitor")

        self._install_detachable_tabs()

        """ Create settings scroll area widget that will hold the settings page"""

        self.settings_area = NoKeyScrollArea()
        self.settings_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.settings_area.setWidgetResizable(True)
        # No frame of its own: its top edge would draw a line between the
        # page's header bar and the form, which are meant to read as one
        # surface. The tab pane already outlines the page.
        self.settings_area.setFrameShape(QFrame.Shape.NoFrame)


        """ Create widget to hold the settings layout """

        settings_widget = QWidget()


        """ Create settings layout instance """

        self.settings_layout = SettingsLayout(parent=self, mainwindow=self)


        """ Add settings layout to the tab widget """

        settings_widget.setLayout(self.settings_layout)
        self.settings_layout.bind(G.app_state)
        self.settings_area.setWidget(settings_widget)

        """ The settings page is the scroll area under a header bar of its
        own, so the compact device row lands in the same place here as it
        does on the Monitor page. The bar holds nothing else, so it is
        shown only while the device row is (see _sync_devices_display). """

        self.settings_header_bar = TabHeaderBar()
        self.settings_header_bar.match_page_background()
        # The inset the Monitor page's own layout gives its bar, so the two
        # device rows sit at the same height.
        self.settings_header_bar.set_page_inset(
            self.monitor_panel.layout().contentsMargins().top())
        self._register_mini_device_panel(self.settings_header_bar)
        settings_page = QWidget()
        settings_page_layout = QVBoxLayout(settings_page)
        settings_page_layout.setContentsMargins(0, 0, 0, 0)
        settings_page_layout.setSpacing(0)
        settings_page_layout.addWidget(self.settings_header_bar)
        settings_page_layout.addWidget(self.settings_area, stretch=1)
        self.tab_widget.addTab(settings_page, "Settings")


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
        self.refresh_firmware_label()

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
        debug_shortcut.activated.connect(self.main_menu.add_debug_menu)

        reload_shortcut = QShortcut(QKeySequence('Ctrl+Shift+R'), self)
        reload_shortcut.activated.connect(self.force_reload_aircraft)

        # System Settings announces device-configuration changes here
        # rather than calling each UI consumer by hand
        app_events().device_config_changed.connect(
            self.on_device_config_changed)

        if G.system_settings.get('debug', False):
            # debug manu is disabled by default.  change debug = true (1) in registry to permanently enable
            self.main_menu.add_debug_menu()

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
        idx = self.tab_widget.indexOf(self.monitor_panel)
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
            self.monitor_panel.set_detach_toolbar_visible(False)
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
        self._sync_devices_display()

    def reattach_tab(self, title: str):
        entry = getattr(self, "_detached_tabs", {}).pop(title, None)
        if not entry:
            return

        if title == 'Monitor':
            self.monitor_panel.set_detach_toolbar_visible(True)

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
        self._sync_devices_display()

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

    def toggle_start_with_windows(self, set_enabled=None):
        try:
            import winreg
        except ImportError:
            return  # the "run at logon" key is a Windows registry key

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

    def test_function(self):
        self.set_scrollbar(400)

    def refresh_firmware_label(self):
        if not HapticEffect.device:
            self.firmware_label.setText("Device Disconnected")
        elif not HapticEffect.device.caps.has_firmware_version:
            self.firmware_label.setText('DirectInput device')
        else:
            try:
                f_vers = HapticEffect.device.get_firmware_version()
            except:
                f_vers = 'error fetching'
            self.firmware_label.setText(f'Rhino Firmware: {f_vers}')

    def refresh_configurator_gating(self):
        caps = getattr(HapticEffect.device, 'caps', None)
        no_gains = caps is not None and not caps.has_gains
        self.main_menu.set_configurator_action_enabled(
            not no_gains,
            'Not supported on this device (no Configurator gains)'
            if no_gains else '')

    def refresh_device_identity(self):
        """Bring every device-identity-derived piece of the UI in line with
        the currently open device - called after a live device switch."""
        self.refresh_firmware_label()
        self.refresh_configurator_gating()
        self.update_device_status(G.device_connection_status)
        self.refresh_device_labels()

    def on_device_config_changed(self, before, after):
        """The stored device configuration changed (System Settings save,
        announced via app_events) - bring every UI consumer in line.

        The status labels refresh unconditionally: identity details the
        path snapshots cannot see (idents, icon choices) may have
        changed.  The aircraft settings form only rebuilds when the
        joystick slots really changed - its Device section and dropdown
        are read at build time - and a section APPEARING at the top
        (one device becoming several) also scrolls there, since holding
        the reading position would leave it silently off-screen above.
        """
        try:
            self.refresh_device_labels()
        except Exception:
            logging.exception('Device label refresh failed')
        try:
            joy = [key for key in after if key.startswith('devpath_joystick')]
            if any(before.get(key, '') != after.get(key, '') for key in joy):
                was_multi = sum(1 for key in joy if before.get(key)) > 1
                now_multi = sum(1 for key in joy if after.get(key)) > 1
                self.settings_layout.reload_caller(
                    reveal_top=now_multi and not was_multi)
        except Exception:
            logging.exception('Aircraft settings form refresh failed')

    def refresh_device_labels(self):
        """Name the hardware under each status icon (the stored per-slot
        identity) and show its icon choice, flashing any icon whose device
        just changed."""
        for role in self.device_panel.get_device_names():
            try:
                slot_suffix = ''
                if role == 'joystick' and G.device_type == 'joystick':
                    # a per-aircraft swap may have an ALTERNATE slot's
                    # device in hand; the icon names what is actually
                    # connected, not the stored primary
                    slot_suffix = utils.active_joystick_slot_suffix(
                        G.system_settings,
                        getattr(G, 'device_devpath', '')) or ''
                label = utils.device_panel_label(
                    role, G.system_settings, slot_suffix=slot_suffix)
                icon = utils.device_panel_icon(
                    role, G.system_settings, slot_suffix=slot_suffix)
            except Exception:
                label, icon = '', ''
            changed = self.device_panel.set_device_label(role, label)
            changed = self.device_panel.set_device_icon(role, icon) or changed
            if changed:
                self.device_panel.flash_device(role)

    def _device_display_order(self):
        """Every device role, always - not just the ones this instance is
        actually driving. Order: this instance's own device first, then
        the rest of what it considers configured (itself plus any child
        instances it launched) in joystick/pedals/collective/trimwheel
        order, then the unconfigured roles in that same order.

        Returns (ordered_roles, configured_role_set)."""
        configured = {G.device_type} | set(G.launched_instances)
        rest = [r for r in utils.DEVICE_ROLES if r != G.device_type]
        configured_rest = [r for r in rest if r in configured]
        unconfigured_rest = [r for r in rest if r not in configured]
        order = [G.device_type] + configured_rest + unconfigured_rest
        return order, configured

    def _register_mini_device_panel(self, header_bar):
        """Take a tab page's header bar into the set the full Active
        Devices panel is mirrored onto, and wire its row to the scope
        switcher."""
        header_bar.DeviceClicked.connect(self.change_config_scope)
        toggle = DeviceViewToggle(frame_shown=False)
        toggle.reveal_on_hover(header_bar)
        toggle.clicked.connect(lambda: self._set_devices_frame_preference(True))
        header_bar.add_before_devices(toggle, top_padding=DeviceViewToggle.GLYPH_TOP)
        self._add_device_view_context_menu(header_bar.device_mini_panel)
        self._mini_device_panels.append(header_bar.device_mini_panel)
        self._mini_device_toggles[header_bar.device_mini_panel] = toggle
        self._sync_mini_device_panel()

    def _add_device_view_context_menu(self, widget):
        """Right-click on either device display: the same switch as its
        button and the Window menu's 'Show Device Frame'."""
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, w=widget: self._show_device_view_context_menu(w, pos))

    def _show_device_view_context_menu(self, widget, pos):
        if not self._multiple_devices_configured():
            return  # one device: the frame never shows, nothing to switch
        showing = self.device_groupbox.isVisible()
        menu = QtWidgets.QMenu(widget)
        action = menu.addAction("Show compact device icons" if showing
                                else "Show the Active Devices panel")
        action.triggered.connect(lambda: self._set_devices_frame_preference(not showing))
        menu.exec(widget.mapToGlobal(pos))

    def _multiple_devices_configured(self) -> bool:
        icons = self.device_panel.icons
        return sum(1 for name in self.device_panel.get_device_names() if icons[name].configured) > 1

    def _sync_mini_device_panel(self):
        """Mirror the full Active Devices panel's device list, active
        device, and each device's icon/label/status/configured state onto
        every page's compact mini row - connected to DeviceIconPanel.changed
        so every mutation path stays in sync automatically without its own
        call site here."""
        names = self.device_panel.get_device_names()
        active = self.device_panel.get_active_device()
        for mini in self._mini_device_panels:
            if mini.get_device_names() != names:
                mini.set_devices(names)
            mini.set_active_device(active)
            for name in names:
                widget = self.device_panel.icons[name]
                mini.set_device_icon(name, widget.icon_path)
                mini.set_device_label(name, widget.text_label.text())
                mini.set_device_configured(name, widget.configured)
                if widget.configured:
                    mini.set_device_status(name, widget.status_color)
        self._sync_devices_display()

    def _sync_devices_display(self):
        """Reconcile the Active Devices frame and the compact mini device
        rows in the tab page headers with: how many devices this instance's
        panel has *configured* (all four roles are always shown, but
        unconfigured ones are inert ghost icons and don't count here), the
        persisted Show/Hide Devices preference (meaningful only with
        multiple configured devices), and whether the Hide tab is active.

        One configured device: the frame never shows and the mini rows'
        chips are not clickable, there being nothing to switch to.
        Multiple: the frame follows the saved preference (default shown),
        and the mini rows - shown only when the frame is not - have
        clickable chips (for configured devices only), so status colors
        stay visible in this small a space and clicking one switches
        straight to it. The Hide tab always collapses the frame (its
        minimum height would stop the window from shrinking); the mini
        rows belong to the Monitor and Settings pages, so that tab has
        none of its own to hide.

        The settings page's header bar holds nothing but its mini row, so
        it goes with it rather than leaving an empty strip above the
        settings. """
        names = self.device_panel.get_device_names()
        configured_names = [n for n in names if self.device_panel.icons[n].configured]
        multiple = len(configured_names) > 1
        tab_widget = getattr(self, 'tab_widget', None)
        on_hide_tab = tab_widget is not None and tab_widget.currentIndex() == 2
        show_frame = multiple and bool(G.system_settings.get('showDevicesFrame', True)) and not on_hide_tab
        self.device_groupbox.setVisible(show_frame)
        show_mini = bool(names) and not show_frame
        # A row's switch back to the frame: only with something to switch
        # to, and not in a detached Monitor window, where it would change
        # a different window than the one it sits in.
        monitor_detached = 'Monitor' in getattr(self, '_detached_tabs', {})
        monitor_mini = self.monitor_panel.header_bar.device_mini_panel if hasattr(self, 'monitor_panel') else None
        for mini in self._mini_device_panels:
            mini.setVisible(show_mini)
            mini.set_clickable(multiple)
            detached = monitor_detached and mini is monitor_mini
            self._mini_device_toggles[mini].setVisible(show_mini and multiple and not detached)
        settings_bar = getattr(self, 'settings_header_bar', None)
        if settings_bar is not None:
            settings_bar.setVisible(show_mini)

    def _set_devices_frame_preference(self, visible: bool):
        G.system_settings.setValue('showDevicesFrame', visible)
        # The Window menu's item is one of three ways here; keep its check
        # mark in step when one of the others was used.
        action = getattr(self.main_menu, 'show_devices_frame_action', None)
        if action is not None and action.isChecked() != visible:
            action.setChecked(visible)
        was_showing = self.device_groupbox.isVisible()
        frame_width, window_width = self.device_groupbox.width(), self.width()
        self._sync_devices_display()
        self._resize_for_devices_frame(was_showing, frame_width, window_width)

    def _activate_layouts(self):
        """Bring the window's layouts up to date with a widget shown or
        hidden in this same slot. Until they are next activated they hold
        the minimum size they had before, and a resize stops at that old
        floor."""
        self.centralWidget().layout().activate()
        QMainWindow.layout(self).activate()

    def _resize_for_devices_frame(self, was_showing: bool, frame_width: int, window_width: int):
        """Give the window the width the Active Devices frame takes when
        it appears, and take it back when it goes, so the column beside it
        keeps its width either way.

        Left alone, the window only ever grows: showing the frame pushes
        it wider when there is no room, and hiding the frame hands the
        width to the tabs instead of giving it back.

        ``frame_width`` and ``window_width`` are from before the change:
        the frame's width can only be read while it is up, and activating
        the layouts may already have widened a window too narrow for the
        frame, which must not be counted twice.
        """
        now_showing = self.device_groupbox.isVisible()
        if now_showing == was_showing or self.isMaximized() or self.isFullScreen():
            return
        self._activate_layouts()
        if now_showing:
            frame_width = self.device_groupbox.width()
        delta = frame_width + self._content_hbox.spacing()
        if not now_showing:
            delta = -delta
        self.resize(window_width + delta, self.height())
        # The Monitor and Settings tabs each remember their own window
        # size; the other one's is from before the frame changed.
        for key in ("0", "1"):
            self.tab_sizes[key]['width'] = int(self.tab_sizes[key]['width']) + delta

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

    def on_child_exception(self, data):
        """Ingest an exception forwarded from a child instance over IPC.

        The record lands in the master's own tracker with the module prefixed
        by the child's device name, so the status-bar notification fires and
        the viewer shows the full record labeled with its origin. Runs on the
        main thread (queued from the IPC thread via child_exception_signal).
        """
        from datetime import datetime

        from telemffb.ExceptionTracker import ExceptionRecord
        try:
            ts = datetime.fromisoformat(data.get("timestamp", ""))
        except (ValueError, TypeError):
            ts = datetime.now()
        record = ExceptionRecord(
            timestamp=ts,
            message=data.get("message", ""),
            traceback=data.get("traceback", ""),
            level=data.get("level", "ERROR"),
            module=f"{data.get('device', 'child')}: {data.get('module', 'unknown')}",
        )
        G.exception_tracker.add_exception(record)
        
    def update_exception_count(self):
        """Update the exception count in the status bar."""
        count = G.exception_tracker.get_count()
        self.exception_status_widget.set_count(count)

    def set_scrollbar(self, pos):
        self.settings_area.verticalScrollBar().setValue(pos)

    @pyqtSlot(bool)
    def update_device_status(self, connected):
        G.device_connection_status = connected
        # three states, derived from the device object itself (the signal
        # only says a transition happened): never opened (zombie),
        # opened-but-dead (reconnecting), or alive (active).
        self.device_panel.set_device_status(G.device_type, device_status_state())

    @pyqtSlot(str, str)
    def update_child_status(self, device, status):
        # self.instance_status_row.set_status(device, status)
        self.device_panel.set_device_status(device, status)

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
            match_history.reset()

            logging.info(f"User config Reset:  Backup file created: {backup_file}")
        else:
            return


    def setup_master_instance(self):
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
        self.main_menu.add_instance_log_menu()
        self.tray.build()
        order, configured = self._device_display_order()
        self.device_panel.set_devices(order, configured=configured)
        self.device_panel.set_device_status(G.device_type, device_status_state())
        self.device_panel.DeviceClicked.connect(self.change_config_scope)
        self.device_panel.set_active_device(G.device_type)
        self.refresh_device_labels()

        """ Window menu: Show Device Frame, only meaningful with more than
        one CONFIGURED device on this instance's own panel - all four are
        always shown, but the rest may just be inert ghost icons """

        if len(configured) > 1:
            self.main_menu.add_show_devices_frame_action(
                bool(G.system_settings.get('showDevicesFrame', True)),
                self._set_devices_frame_preference)

        self._sync_devices_display()

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

        if G.master_instance:
            self.monitor_panel.set_effects_scope_label(G.current_device_config_scope)
        G.app_state.set_scope(G.current_device_config_scope)
        self.settings_layout.reload_caller()

    def show_profile_manager(self):
        xmlutils.update_roots() # make sure roots get updated in case state is timedout and file has changed
        self.profile_mgr_dialog = ProfileManagerDialog(self)
        self.profile_mgr_dialog.raise_()
        self.profile_mgr_dialog.activateWindow()
        self.profile_mgr_dialog.show()

    @pyqtSlot(bool)
    def toggle_offline_mode(self, state, broadcast=True):
        if state == G.settings_mgr.offline_mode:
            # if already in the same state, do nothing
            return
        if not state:
            # Exiting Offline editing mode

            G.settings_mgr.go_online()

            # clear the layout after going back online
            G.main_window.settings_layout.clear_layout()

            # reset the craft area text to default
            self.header_panel.reset()
            G.app_state.reset_sim_status()
            self.settings_layout.reload_caller()
            # go_online restored the pre-offline context; re-evaluate the
            # notes button against it (dedupe dropped so a re-load of the
            # same context still refreshes)
            self._profile_notes_shown = None
            self.refresh_profile_notes_button()
            self.refresh_offline_editor_button()
        else:
            # Entering offline editing mode
            G.settings_mgr.go_offline()
            G.app_state.set_active_settings(())  # drop live-telemetry highlighting
            self.refresh_offline_editor_button()      # hidden while the editor is open
            self.header_panel.set_offline("None")
            G.app_state.reset_sim_status()
            # clear the layout in case an aircraft was previously loaded live
            G.main_window.settings_layout.clear_layout()

            # Nothing is selected in the offline editor yet; disable the notes
            # button until force_sim_aircraft establishes an offline scope
            self._profile_notes_shown = None
            self.header_panel.set_notes_state(False)

            # Clear/repopulate the offline editor's combo boxes.
            self.offline_editor.reset_for_entry()

            # force the settings tab to be active
            self.tab_widget.setCurrentIndex(1)

        if G.master_instance:
            # Show the offline mode widgets, but only for master instance
            self.offline_editor.setVisible(state)

            # Send command to child instances to replicate actions
            if broadcast:
                G.ipc_instance.send_broadcast_message(f"TOGGLE OFFLINE:{state}")

    @pyqtSlot(str, str, str, str)
    def load_single_offline_model(self, sim, cls, model, profile, from_profile_manager=True):
        """Thin passthrough kept on MainWindow: IPCNetworkThread.show_offline_model_signal
        and ProfileManagerDialog both call this by name.  See
        telemffb.ui.panels.OfflineEditorPanel.load_single_offline_model."""
        self.offline_editor.load_single_offline_model(sim, cls, model, profile, from_profile_manager)


    def show_new_aircraft_wizard(self, manual=False, sim=None, name=None, cls=None, clone_from=None):
        # utils.debug_caller_args("red")
        wizard = NewAircraftWizard(parent=self, manual=manual, auto_sim=sim, auto_name=name, auto_cls=cls,
                                   clone_from=clone_from)
        wizard.accepted.connect(self.new_ac_wizard_finished)
        wizard.exec()

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
                self.tray.show_notification(
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

    def open_tap_monitor(self):
        """One monitor, re-raised rather than duplicated: two pollers
        on the same mapping would only double the wakeups."""
        from telemffb.ui.dialogs.TapMonitorDialog import TapMonitorDialog
        dlg = getattr(self, '_tap_monitor', None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        self._tap_monitor = TapMonitorDialog(self)
        self._tap_monitor.show()

    def open_trim_calibration_dialog(self):
        """Open (or focus) the elevator trim calibration dialog.

        Shared entry point for the Utilities menu action and the settings-row
        'trimcal' button; one dialog instance serves both.

        Trim calibration is MSFS/X-Plane only, so refuse to open (with an
        explanation) when nothing is loaded or the active aircraft is for a
        different simulator.
        """
        # Offline editing with no aircraft selected: the settings manager
        # still carries the ONLINE aircraft's identity and offline writes go
        # nowhere (scope fall-through) — the dialog would show a misleading
        # limbo state. Refuse with directions instead.
        if getattr(G.settings_mgr, "offline_mode", False):
            from telemffb.ui.dialogs.TrimCalibrationDialog import TrimCalibrationDialog
            if not TrimCalibrationDialog._offline_target_valid():
                QMessageBox.information(
                    self, "Elevator Trim Calibration",
                    "Offline editing mode is active but no aircraft is "
                    "selected.\n\nChoose a Sim, Class and Aircraft in the "
                    "Offline Editor Setup panel first, then open the "
                    "Elevator Trim Calibration tool to view or import its "
                    "calibrations.")
                return

        sim = (G.settings_mgr.current_sim or "").upper()
        if sim not in ("MSFS", "XPLANE"):
            if sim in ("", "NOTHING"):
                msg = ("No aircraft is loaded.\n\nLoad into an MSFS or X-Plane "
                       "aircraft, then open the Elevator Trim Calibration tool.")
            else:
                msg = (f"Elevator Trim Calibration is only available for MSFS and "
                       f"X-Plane.\n\nThe active aircraft is for {sim}.")
            QMessageBox.information(self, "Elevator Trim Calibration", msg)
            return

        from telemffb.ui.dialogs.TrimCalibrationDialog import TrimCalibrationDialog
        if getattr(self, 'trim_cal_dialog', None) is None:
            # The dialog destroys itself on close (stale-display safety); the
            # destroyed signal clears this reference so the next open builds
            # a fresh one against the then-current aircraft.
            self.trim_cal_dialog = TrimCalibrationDialog(self)
            self.trim_cal_dialog.result_saved.connect(self.settings_layout.save_trim_calibration)
            self.trim_cal_dialog.position_mode_changed.connect(
                self.settings_layout.save_trim_position_mode)
            self.trim_cal_dialog.destroyed.connect(
                lambda: setattr(self, 'trim_cal_dialog', None))
        self.trim_cal_dialog.raise_()
        self.trim_cal_dialog.activateWindow()
        self.trim_cal_dialog.show()

    def update_settings(self):
        # utils.debug_caller_args('blue')
        self.populate_profile_combo(None) # populate combo with any new profiles
        self.update_craft_text_block(craft=G.settings_mgr.current_aircraft_name, pattern=G.settings_mgr.current_pattern, profile=G.settings_mgr.active_profile)
        self.settings_layout.reload_caller()
        self._update_profile_change_prompt()
        self.refresh_offline_editor_button()

    def _update_profile_change_prompt(self):
        change = getattr(G.settings_mgr, 'profile_change', None)
        if not change or not G.master_instance:
            G.app_state.set_prompt('profile_change', None)
            self._profile_change_prompt_active = False
            return
        # One line, like the trim prompt beside it: the detail and the choice
        # need more room than a pill has, so they live in the dialog it opens.
        G.app_state.set_prompt('profile_change', Notice(
            notice_id='profile_change', priority=PROFILE_CHANGE_PRIORITY,
            style='profile_change', pulse=False,
            html="<a href='#open' style='color:white; text-decoration:none;'>"
                 "<span style='font-weight:500;'>Multiple matching profiles detected — </span>"
                 "<b>Click Here</b><span style='font-weight:500;'> to resolve</span></a>"))
        if not self._profile_change_prompt_active:
            self._profile_change_prompt_active = True
            # Only worth a toast when the window cannot be seen, and it says
            # what is true: nothing has been decided and nothing is asked for.
            if self.isHidden() or self.isMinimized():
                self.tray.show_notification(
                    "Multiple matching profiles",
                    f"{change['aircraft']}: your {change['user']} and the built-in "
                    f"{change['curated']} both match.\nOpen TelemFFB to resolve.",
                    15)

    def _on_profile_change_link(self, href):
        change = getattr(G.settings_mgr, 'profile_change', None)
        if not change:
            G.app_state.set_prompt('profile_change', None)
            self._profile_change_prompt_active = False
            return
        choice = self._ask_profile_change(change)
        if choice == ProfileOfferDialog.LATER:
            return                      # the prompt stays put, and returns next load
        # Clear only the offer that was answered.  The telemetry thread
        # replaces the field on every resolution, so if the aircraft
        # changed while the dialog was open the field now holds the next
        # aircraft's offer, and that one must stay for its own prompt.
        if G.settings_mgr.profile_change is change:
            G.settings_mgr.profile_change = None
        G.app_state.set_prompt('profile_change', None)
        self._profile_change_prompt_active = False
        sim, user, curated = change['sim'], change['user'], change['curated']
        shipped = change.get('shipped', '')
        try:
            if choice == ProfileOfferDialog.MERGE:
                xmlutils.merge_user_pattern(sim, user, curated, keep=bool(change.get('keep')))
                match_history.resolve(sim, user, curated, match_history.MERGED, shipped)
                if G.telem_manager is not None:
                    G.telem_manager.currentAircraftName = None    # re-resolve from scratch
            elif choice == ProfileOfferDialog.DECLINE:
                match_history.resolve(sim, user, curated, match_history.DECLINED, shipped)
            else:
                return
        except Exception:
            logging.exception("Acting on the profile offer failed")
            return
        if not G.settings_mgr.offline_mode and G.telem_manager is not None:
            G.telem_manager.refresh_aircraft_profile()
        self.settings_layout.reload_layout(None)

    def _ask_profile_change(self, change):
        """Show the offer; returns one of the ProfileOfferDialog choices."""
        preview = xmlutils.merge_preview(change['sim'], change['aircraft'],
                                         change['user'], change['curated'])
        labels = xmlutils.setting_display_names([e['name'] for e in preview['entries']])
        dialog = ProfileOfferDialog(change, preview, labels, self)
        dialog.exec()
        return dialog.choice


    # ---- "Enter Offline Editor" for the live aircraft -----------------------

    @staticmethod
    def live_offline_target(settings_mgr, available_profiles=()):
        """What the offline editor should open on for the loaded aircraft:
        ``(sim, cls, model, profile)`` or ``None`` when nothing is loaded.

        ``model`` is the matched pattern (what the editor's model list
        holds), empty when the aircraft only has class-level settings - the
        editor then opens at CLASS scope.  ``profile`` is the active one,
        else the first available, else empty."""
        sim = getattr(settings_mgr, 'current_sim', None)
        if not sim or sim == 'nothing':
            return None
        cls = getattr(settings_mgr, 'current_class', '') or ''
        model = getattr(settings_mgr, 'current_pattern', '') or ''
        profile = getattr(settings_mgr, 'active_profile', None) or ''
        if not profile and model:
            profile = next((p for p in available_profiles if p != 'Built-In'), '')
        return sim, cls, model, profile

    def refresh_offline_editor_button(self):
        """Show the corner button only while an aircraft is loaded and the
        offline editor is not open; its tooltip names where it will land."""
        btn = getattr(self, 'offline_editor_button', None)
        if btn is None:
            return
        manager = getattr(G, 'telem_manager', None)
        loaded = manager is not None and getattr(manager, 'currentAircraft', None) is not None
        target = self.live_offline_target(G.settings_mgr) if loaded else None
        if target is None or G.settings_mgr.offline_mode:
            btn.setVisible(False)
            return
        sim, cls, model, profile = target
        where = f"{sim} / {cls or '-'} / {model or '(class defaults)'}"
        if profile:
            where += f" / {profile}"
        btn.setToolTip(f"Edit and preview effects for the loaded aircraft:\n{where}\n"
                       "Telemetry pauses while you work; play buttons appear on the sliders.")
        btn.setVisible(True)

    def enter_offline_for_live_aircraft(self):
        target = self.live_offline_target(G.settings_mgr)
        if target is None:
            return
        sim, cls, model, profile = target
        if not profile and model:
            profiles = xmlutils.get_available_profiles(sim, cls, model)
            profile = next((p for p in profiles if p != 'Built-In'), '')
        self.load_single_offline_model(sim, cls, model, profile, from_profile_manager=False)

    def open_url(self, url):

        # Open the URL
        QDesktopServices.openUrl(QUrl(url))

    def forget_profile_offers(self):
        """Give back every matching-profile offer the user answered for good,
        so each one is raised again.  Their configuration is untouched: the
        only thing forgotten is what they answered."""
        ans = QMessageBox.question(
            self, "Reset dismissed profile prompts?",
            "TelemFFB will ask again about each aircraft you answered with 'Keep mine' "
            "or 'Don't ask again'. The prompt returns as that aircraft loads, starting "
            "with the one you have loaded now.\n\n"
            "Nothing in your configuration changes, and merges you already made stay as "
            "they are.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Yes)
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            match_history.forget_declines()
        except Exception:
            # The history lock can time out; an exception escaping a Qt slot
            # takes the process down, so it is logged here instead.
            logging.exception("Could not forget the declined profile offers")
            return
        logging.info("Declined profile offers forgotten at the user's request")
        # The loaded aircraft may be one of them, so bring its prompt back now
        # rather than on whichever frame next resolves it.
        if not G.settings_mgr.offline_mode and G.telem_manager is not None:
            G.telem_manager.recheck_profile_offer()
        self._update_profile_change_prompt()

    def reset_all_effects(self):
        result = QMessageBox.warning(self, "Are you sure?", "*** Only use this if you have effects which are 'stuck' ***\n\n  Proceeding will result in the destruction"
                                                            " of any effects which are currently being generated by the simulator and may result in requiring a restart of"
                                                            " the sim or a new session.\n\n~~ Proceed with caution ~~", QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)

        if result == QMessageBox.StandardButton.Ok:
            try:
                HapticEffect.device.reset_effects()
            except Exception:
                pass



    def update_sim_indicators(self, source, paused=False, error=False, message=None):
        """External entry point (e.g. DcsIpcThread's Ev=Start) that pushes
        a status straight to AppState, bypassing the error-onset/hold/clear
        state machine - see SimStatusTracker.push_status, which this now
        just forwards to. HeaderPanel/TrayController.bind() apply the
        result to the status container and tray."""
        self.sim_status.push_status(source, paused=paused, error=error, message=message)

    def on_first_sim_frame(self, src):
        """Handle first_frame_received: see SimStatusTracker.on_first_frame."""
        self.sim_status.on_first_frame(src)





    def switch_window_view(self, index):
        previous_index = self.current_tab_index
        # Get window geometry and store as the geometry for the previous index for later recall
        self.tab_sizes[str(previous_index)]['height'] = self.height()
        self.tab_sizes[str(previous_index)]['width'] = self.width()

        # Active Devices (and its mini-indicator stand-in) must get out of
        # the way on the Hide tab, or their own minimum height would stop
        # the window from collapsing to a compact view.
        self._sync_devices_display()

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
            # active_settings_changed skips painting while this tab is
            # hidden (see SettingsLayout._on_active_settings_changed) - catch
            # up on whatever changed while the user was elsewhere.
            self.settings_layout.repaint_active_settings()

        elif index == 2:  # Hide Tab
            self.current_tab_index = 2

            # Hiding the Active Devices frame (above) leaves the layouts
            # holding the minimum height they had while it was up. Showing
            # the mini device row under the logo used to refresh them as a
            # side effect; the rows now live on the tab pages, which are
            # not visible here, so ask directly.
            self._activate_layouts()
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
        # Profiles belong to the pattern that names the aircraft, so with
        # nothing matched there are none to pick between and none to add to.
        self.header_panel.set_profile_state(bool(G.settings_mgr.current_pattern))
        if new_items is None:
            new_items = xmlutils.get_available_profiles(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern)

        self.header_panel.set_profile_choices(new_items)

    def on_profile_change(self, profile_name: str):
        # utils.debug_caller_args("red")
        """
        Call to xmlutils to update the profile mapping for the aircraft when the user changes the profile
        If the "add new" option is selected, pop a dialog asking for the new profile name.  If the user chooses
        the "make active' option, make a further call to make the new profile the active one
        Args:
            profile_name: The chosen profile name (the combo has already reset itself to the placeholder).

        Returns: Nothing

        """
        if not G.master_instance:
            return

        sim = G.settings_mgr.current_sim
        cls = G.settings_mgr.current_class
        pattern = G.settings_mgr.current_pattern

        cur_txt = xmlutils.get_active_profile_for_model(sim, cls, pattern)
        if profile_name == AppStatusWidget.ADD_NEW_LABEL:
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
            # Keep the in-memory active profile in sync with the mapping we
            # just wrote (the Add New path already does this). Without it, the
            # timed-out reload below re-reads settings through the STALE
            # profile filter — so the form never appears to change while the
            # sim is paused — and the notes dialog targets the old profile.
            G.settings_mgr.update_state_vars(active_profile=profile_name)
            if G.telem_manager.timed_out:
                self.update_craft_text_block(craft=G.settings_mgr.current_aircraft_name, pattern=G.settings_mgr.current_pattern, profile=profile_name)
        if G.telem_manager.timed_out:
            self.settings_layout.reload_caller()

    def on_telemetry_timeout(self):
        self.monitor_panel.clear_effects()
        # No frames means no running effects; don't leave the last live
        # frame's slider highlighting up (on_update_telemetry won't clear it).
        G.app_state.set_active_settings(())
        self.sim_status.on_timeout(G.telem_manager.getTelemValue('src'))

    def on_sim_exited(self, src: str):
        """Called when a sim sends a clean exit notification (STATUS=EXIT).
        Resets the Application Status area and settings tab to the waiting state
        so stale aircraft info and the 'Paused' badge are cleared before the
        next sim connects."""
        logging.info(f"Application Status: clearing display after {src} exit")
        self.monitor_panel.clear_effects()
        G.app_state.set_active_settings(())
        self.header_panel.reset_sim_state(src)
        # reset_sim_state disabled the notes button; drop the dedupe context
        # so the next aircraft load re-evaluates it even if identical.
        self._profile_notes_shown = None
        self.settings_layout.clear_layout()
        self.sim_status.on_sim_exited()
        self.refresh_offline_editor_button()      # nothing loaded to edit any more

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

            self.monitor_panel.update_telemetry(data)

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

            # The scoped device-status indicators (vpconf profile / gain
            # override) are no longer polled here - AppState is updated
            # directly wherever the underlying state changes (this
            # instance's own vpconf/gain-override writers, and the IPC
            # thread for a child's reported state) and repaints only when
            # its derived view actually changes.

            if G.child_instance:
                child_effects = str(G.effects.dict.keys())
                if child_effects:
                    G.ipc_instance.send_ipc_effects(active_effects, active_settings)

            # Drives the settings-tab slider highlighting (green = active).
            # AppState dedupes and only repaints the (cached, non-live-key)
            # sliders when this actually changes - see
            # SettingsLayout._on_active_settings_changed/repaint_active_settings.
            G.app_state.set_active_settings(active_settings)

            # The live-key number sliders (coeff % handles) still need a
            # per-frame color+label update straight from telemetry, whether
            # or not active_settings changed - a small cached list, not
            # findChildren(), per SettingsLayout._rebuild_slider_caches().
            if self.tab_widget.currentIndex() == 1:
                qcolor_green = QColor(ACTIVE_GREEN)
                qcolor_grey = QColor("grey")
                for my_slider, live_key in self.settings_layout.live_key_sliders:
                    pct = min(data.get(live_key, 0), 1.0)
                    new_color = self.interpolate_color(qcolor_grey, qcolor_green, pct)
                    my_slider.blockSignals(True)
                    my_slider.setHandleColor(new_color.name(), f"{int(pct * 100)}%")
                    my_slider.blockSignals(False)

            # Error onset/hold/clear - see SimStatusTracker.on_frame.
            self.sim_status.on_frame(data)




            shown_pattern = G.settings_mgr.current_pattern
            if G.settings_mgr.current_pattern == '' and data.get('N', '') != '':
                shown_pattern = 'Using defaults'
                new_sim = data.get('src', None)
                new_aircraft = data.get('N', None)
                new_class = G.settings_mgr.current_class
                G.app_state.set_prompt('trim_cal', None)  # profile creation first
                if G.master_instance:
                    target = (new_sim, new_class, new_aircraft)
                    if not self._new_craft_prompt_active or target != self._new_craft_target:
                        # Rebuilt only when the unmatched aircraft changes, so
                        # the text and click target always follow the current
                        # one without per-frame Notice construction.
                        self._new_craft_target = target
                        G.app_state.set_prompt('new_craft', Notice(
                            notice_id='new_craft', priority=NEW_CRAFT_PRIORITY,
                            style='new_craft', pulse=True,
                            html="<a href='#newcraft' style='color:white; text-decoration:none;'>"
                                 "<span style='font-weight:500;'>No Profile Found for </span>"
                                 f"<b>{html.escape(str(new_aircraft or ''))}</b>"
                                 "<span style='font-weight:500;'> — Click Here to Create a New Profile</span></a>"))
                        self._new_craft_prompt_active = True

                    if not data.get('STOP', False):
                        if not self.new_craft_notification_sent:

                            self.tray.show_notification(
                                "** New Aircraft Found **",
                                f"No profile was found for the aircraft\n{data.get('N')}\n\nClick to open TelemFFB.",
                                10,
                            )
                            self.new_craft_notification_sent = True
                            self.show_new_aircraft_wizard(manual=False, sim=data.get('src', None), cls=G.settings_mgr.current_class, name=data.get('N', ''))



            else:
                if self._new_craft_prompt_active:
                    G.app_state.set_prompt('new_craft', None)
                    self._new_craft_prompt_active = False
                    self._new_craft_target = None
                self.new_craft_notification_sent = False
                self._update_trim_cal_prompt()

            # Update the status labels and profile selection box
            self.header_panel.set_fullname(data.get('N', ''))
            ap = G.settings_mgr.active_profile
            active_profile = xmlutils.get_active_profile_for_model(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern)

            self.update_craft_text_block(pattern=shown_pattern, profile=active_profile)

            self.monitor_panel.update_effects(active_effects)

        except Exception:
            logging.exception("Exception")

    def update_craft_text_block(self, craft=None, pattern=None, profile=None):
        if craft is None:
            craft = G.settings_mgr.current_aircraft_name
        if pattern is None:
            pattern = G.settings_mgr.current_pattern
        if profile is None:
            profile = G.settings_mgr.active_profile
        self.header_panel.set_craft_info(craft, pattern, profile)
        # The resolved pattern, not the label: with nothing matched the label
        # reads "Using defaults", which is neither something to fork off nor
        # something with profiles to pick between.
        named = (bool(craft) and bool(G.settings_mgr.current_pattern) and G.master_instance
                 and G.settings_mgr.current_sim not in ('', 'nothing')
                 and not G.settings_mgr.offline_mode)
        self.header_panel.set_split_state(named)
        self.header_panel.set_profile_state(named)
        self.refresh_profile_notes_button()

    def split_loaded_aircraft_profile(self):
        """The wizard, prefilled for the loaded aircraft and cloning from the
        profile it matches now, so a livery or variant that rode a broader
        pattern gets a profile of its own."""
        sm = G.settings_mgr
        if sm.offline_mode or not sm.current_aircraft_name or not sm.current_pattern:
            return
        self.show_new_aircraft_wizard(
            manual=False, sim=sm.current_sim, name=sm.current_aircraft_name,
            cls=sm.current_class, clone_from=(sm.current_pattern, sm.active_profile or 'Built-In'))

    def refresh_telem_override_pill(self, force=False):
        """Update the telemetry-override pill in the status area for the
        current aircraft context. SimConnect/Dataref overrides are
        aircraft-scoped (every device instance subscribes the same set), so
        unlike the vpconf/gains indicators there is no per-device scope or
        IPC handling. Rides the same telemetry-update cadence as the
        profile-notes button, with the same context deduplication so the
        XML is not re-read every frame."""
        sim = G.settings_mgr.current_sim
        aircraft = G.settings_mgr.current_aircraft_name
        ctx = (sim, aircraft, G.settings_mgr.current_pattern, G.settings_mgr.current_class)
        if not force and ctx == getattr(self, '_telem_ovd_shown', None):
            return
        self._telem_ovd_shown = ctx
        text, tip = '', ''
        if sim in ('MSFS', 'XPLANE') and aircraft:
            try:
                overrides = xmlutils.read_sc_overrides(
                    aircraft, sim=sim, cls=G.settings_mgr.current_class or None)
            except Exception:
                logging.exception('Failed to read sc_overrides for status pill')
                overrides = []
            if overrides:
                n_usr = sum(1 for o in overrides if o.get('source') == 'user')
                n_cls = sum(1 for o in overrides
                            if o.get('source') != 'user' and o.get('scope') == 'class')
                n_def = len(overrides) - n_usr - n_cls
                parts = ([f'Class ({n_cls})'] if n_cls else []) + \
                        ([f'Default ({n_def})'] if n_def else []) + \
                        ([f'User ({n_usr})'] if n_usr else [])
                text = ' + '.join(parts)
                lines = [f"{o['name']}  ←  {o['var']}   [{o['source']} {o.get('scope', 'model')}]"
                         for o in overrides[:15]]
                if len(overrides) > 15:
                    lines.append(f"... and {len(overrides) - 15} more")
                tip = ('Active SimConnect/Dataref overrides for this aircraft\n'
                       '(Utilities → SimConnect/Dataref Overrides Editor):\n\n'
                       + '\n'.join(lines))
        self.header_panel.request_set_telem_overrides.emit(text, tip)

    def refresh_profile_notes_button(self):
        """Update the profile-notes button (enabled + notes-exist tint) for
        the current aircraft/profile context. Runs from the telemetry update
        path, so repeat contexts are deduplicated to avoid re-reading the
        XML tables every frame."""
        self.refresh_telem_override_pill()
        sim = G.settings_mgr.current_sim
        aircraft = G.settings_mgr.current_aircraft_name
        pattern = G.settings_mgr.current_pattern
        profile = G.settings_mgr.active_profile
        ctx = (sim, aircraft, pattern, profile)
        if ctx == getattr(self, '_profile_notes_shown', None):
            return
        self._profile_notes_shown = ctx
        # A note is written against the pattern that names the aircraft, so
        # with nothing matched there is nothing to attach one to: the dialog
        # would open, fail to save and log an error.
        enabled = bool(aircraft) and bool(pattern) and sim not in ('', 'nothing')
        has_notes = False
        if enabled:
            target = profile if profile and str(profile).lower() not in ('none', 'built-in', 'default') else 'Auto User'
            # Same tiers the dialog shows: curated defaults type notes, user
            # default (user config type row) notes, and the active profile's
            # own note — never another profile's.
            has_notes = bool(
                xmlutils.read_default_model_notes(sim, aircraft, prefer_pattern=pattern)
                or xmlutils.read_user_default_model_notes(sim, pattern)
                or xmlutils.read_user_model_notes(sim, pattern, target))
        self.header_panel.set_notes_state(enabled, has_notes)

    def open_profile_notes_dialog(self):
        dlg = ProfileNotesDialog(self)

        def on_saved():
            # Force a re-read so the button tint reflects the new note state.
            self._profile_notes_shown = None
            self.refresh_profile_notes_button()

        dlg.notes_saved.connect(on_saved)
        dlg.show()

    def new_ac_wizard_finished(self):
        G.app_state.set_prompt('new_craft', None)
        self._new_craft_prompt_active = False
        self._new_craft_target = None
        # The wizard just wrote the type row and the profile mapping. The
        # telemetry loop only re-resolves the active profile when a frame
        # notices the config change; a setting changed before that frame
        # (sim paused, in a menu) would be written against the stale None
        # profile, as a row that belongs to no profile. Re-resolve now, so
        # the form we reload below already edits the new profile.
        if not G.settings_mgr.offline_mode and G.telem_manager is not None:
            G.telem_manager.refresh_aircraft_profile()
        # A profile the user just made on purpose is not a surprise, and
        # the wizard already offered the clone; the refresh above recorded
        # the new match, so neither the next reload nor the next start
        # offers again.
        G.settings_mgr.profile_change = None
        G.app_state.set_prompt('profile_change', None)
        self.settings_layout.reload_layout(None)

    def _update_trim_cal_prompt(self):
        """Show the trim-calibration discovery prompt when the loaded
        MSFS/X-Plane aircraft has a matched profile, its config RESOLVES the
        trim-curve setting, and no calibration is stored.

        Availability is stamped ONCE per aircraft load by
        TelemManager._stamp_trim_cal_availability from the "!class"
        exclusion markers in defaults.xml — the same data that hides the
        curve settings rows for helicopter classes — so this method only
        reads two attributes per pass. Prereq VALUES are deliberately not
        part of availability: trim_following defaults off, and users who
        have not enabled it yet are exactly the audience this prompt exists
        for (the calibration dialog's banner walks them through enabling).

        Self-maintaining thereafter: a saved (or imported) calibration
        populates the aircraft's parsed curve family and hides the prompt;
        deleting the last calibration brings it back."""
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        show = (G.master_instance and G.device_type == "joystick"
                and ac is not None
                and getattr(ac, "_trim_cal_available", False)
                and getattr(ac, "_trim_curve_y_fam", None) is None)
        G.app_state.set_prompt('trim_cal', Notice(
            notice_id='trim_cal', priority=TRIM_CAL_PRIORITY, style='trim_cal', pulse=True,
            html="<a href='#trimcal' style='color:black; text-decoration:none;'>"
                 "<span style='font-weight:500;'>No Trim Calibration Found for this "
                 "Aircraft — </span><b>Click Here</b><span style='font-weight:500;'>"
                 " to Set Up Realistic Trim</span></a>") if show else None)

    def _on_prompt_activated(self, notice_id: str):
        """PromptStack.activated relay: what a click on each of the three
        prompts does. Kept here rather than in the panel because every one
        of them opens a MainWindow-owned dialog or reaches into
        G.telem_manager - the panel only knows what is showing, not what
        clicking it means."""
        if notice_id == 'new_craft':
            if self._new_craft_target is not None:
                sim, cls, name = self._new_craft_target
                self.show_new_aircraft_wizard(manual=False, sim=sim, cls=cls, name=name)
        elif notice_id == 'trim_cal':
            self.open_trim_calibration_dialog()
        elif notice_id == 'profile_change':
            self._on_profile_change_link(None)

