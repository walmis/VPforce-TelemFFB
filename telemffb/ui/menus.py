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

"""MainMenu: builds MainWindow's menu bar (System / Profiles / Utilities /
Window / Log / Help) and the developer-only Debug menu (Alt+D, or the
``debug`` registry key).

Like ``OfflineEditorPanel``/``SettingsLayout``, this takes the owning
``MainWindow`` as ``mainwindow`` and connects menu actions back to its
public methods (dialogs, mode toggles) rather than duplicating that logic
here - only menu-construction/wiring lives in this module. ``G.master_instance``
/``G.child_instance`` gating is carried over verbatim from where it used to
live inline in ``MainWindow.__init__``.

The menu bar, the Window/Log menus and the Debug menu's Configurator
action all live on this object (``self.menu``, ``self.window_menu``,
``self.log_menu``, ``self.log_window_action``, ``self.configurator_settings_action``)
rather than being mirrored onto MainWindow. MainWindow reaches them through
``self.main_menu`` and the small public methods below (``add_instance_log_menu``,
``add_device_view_actions``, ``set_configurator_action_enabled``).

The bar is held at ``MENU_BAR_HEIGHT``, a little taller than it would be
left to itself: with the margin beneath it, that is the band the app logo
floats over in the window's top-right corner (``telemffb.ui.widgets.
CornerLogo``).

The "Install Latest TelemFFB" action is not mirrored onto MainWindow; it is
handed to ``mainwindow.updates`` (an ``UpdateChecker``, see
``telemffb/ui/updates.py``) via ``bind_action`` so that module can
enable/relabel it once a check completes.
"""

import logging
import os
import sys

from PyQt6 import QtCore
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QAction, QActionGroup, QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox

import telemffb.globals as G
import telemffb.utils as utils
from telemffb import match_history
from telemffb.preview.engine import PREVIEW_SPECS
from telemffb.ui.dialogs.ConfiguratorDialog import ConfiguratorDialog
from telemffb.ui.dialogs.SCOverridesEditor import SCOverridesEditor
from telemffb.ui.dialogs.TeleplotSetupDialog import TeleplotSetupDialog
from telemffb.ui.widgets.SettingsLayout import SettingsLayout
from telemffb.utils import ALL_ROLES, exit_application

#: Height the menu bar is held at - see the module docstring.
MENU_BAR_HEIGHT = 32


class MainMenu:
    def __init__(self, mainwindow):
        self.mw = mainwindow

    def build(self):
        """Build the main menu bar. Called once, from MainWindow.__init__."""
        mw = self.mw

        doc_url = 'https://docs.vpforce.eu/telemffb/'
        if G.release_version:
            dl_url = 'https://github.com/walmis/VPforce-TelemFFB/releases'
        else:
            dl_url = 'https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=D'
        notes_url = utils.get_resource_path('_RELEASE_NOTES.txt')

        menubar = mw.menuBar()
        self.menu = menubar
        menubar.setMinimumHeight(MENU_BAR_HEIGHT)
        # Set the background color of the menu bar
        # "#ab37c8" is VPForce purple

        """ Add the "System" menu and its sub-option """

        system_menu = self.menu.addMenu('&System')

        if G.master_instance:
            # Settings for every device live in the master's dialog; a child
            # has none of its own to show.
            system_settings_action = QAction('System Settings', mw)
            system_settings_action.triggered.connect(mw.open_system_settings_dialog)
            system_menu.addAction(system_settings_action)

        cfg_log_folder_action = QAction('Open Config/Log Directory', mw)
        def do_open_cfg_dir():
            modifiers = QApplication.keyboardModifiers()
            if (modifiers & QtCore.Qt.KeyboardModifier.ControlModifier) and (modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier) and getattr(sys, 'frozen', False):
                os.startfile(getattr(sys, "_MEIPASS"), 'open')
            else:
                os.startfile(G.userconfig_rootpath, 'open')
        cfg_log_folder_action.triggered.connect(do_open_cfg_dir)
        system_menu.addAction(cfg_log_folder_action)

        reset_geometry = QAction('Reset Window Size/Position', mw)

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
                case 'shaker':
                    x_pos = 20
                    y_pos = 10
            mw.setGeometry(x_pos, y_pos, 530, 700)

        reset_geometry.triggered.connect(do_reset_window_size)
        system_menu.addAction(reset_geometry)

        # Quitting a child ends that instance; only the master takes the
        # whole application down with it.
        exit_app_action = QAction(
            'Quit TelemFFB' if G.master_instance else
            f'Quit the {utils.device_display_name(G.device_type)} Instance',
            mw)
        exit_app_action.triggered.connect(exit_application)
        system_menu.addAction(exit_app_action)

        if G.master_instance:
            """
            Create profiles menu - only for Master Instance
            """
            self.profiles_menu = self.menu.addMenu('Profiles')

            self.profile_manager_action = QAction('Profile Manager...', mw)
            self.profile_manager_action.triggered.connect(mw.show_profile_manager)
            self.profiles_menu.addAction(self.profile_manager_action)

            self.offline_config_action = QAction(r'Offline Editor/Effect Preview', mw)
            self.offline_config_action.triggered.connect(lambda: mw.toggle_offline_mode(True))
            self.profiles_menu.addAction(self.offline_config_action)

            self.profiles_menu.setToolTipsVisible(True)
            self.forget_offers_action = QAction('Reset Dismissed Profile Prompts', mw)
            self.forget_offers_action.setToolTip(
                "TelemFFB will ask again about each aircraft you answered with 'Keep mine' "
                "or 'Don't ask again', the next time the aircraft loads.")
            self.forget_offers_action.triggered.connect(mw.forget_profile_offers)
            self.profiles_menu.addAction(self.forget_offers_action)
            # Nothing dismissed means nothing to bring back; checked as the menu
            # opens rather than tracked, since prompts are answered elsewhere.
            self.profiles_menu.aboutToShow.connect(
                lambda: self.forget_offers_action.setEnabled(match_history.has_declines()))


        """ Create the "Utilities" menu """

        utilities_menu = self.menu.addMenu('Utilities')

        # Add the "Reset" action to the "Utilities" menu
        reset_action = QAction('Reset All Effects', mw)
        reset_action.triggered.connect(mw.reset_all_effects)
        utilities_menu.addAction(reset_action)

        update_action = QAction('Install Latest TelemFFB', mw)
        update_action.triggered.connect(mw.updates.update_from_menu)
        if not G.release_version:
            utilities_menu.addAction(update_action)
        update_action.setDisabled(True)
        mw.updates.bind_action(update_action)

        # 'Download Other Versions' was removed from this menu; dl_url and
        # mw.open_url stay so it can be put back.

        self.reset_user_config_action = QAction('Reset User Config', mw)
        self.reset_user_config_action.triggered.connect(mw.reset_user_config)
        utilities_menu.addAction(self.reset_user_config_action)

        def launch_vpconf():
            try:
                utils.launch_vpconf()
            except Exception as e:
                logging.error(f"Error launching VPforce Configurator: {e}")
                QMessageBox.critical(mw, "Error", f"Error launching VPforce Configurator: {e}")
        self.vpconf_action = QAction("Launch VPforce Configurator", mw)
        self.vpconf_action.triggered.connect(launch_vpconf)
        utilities_menu.addAction(self.vpconf_action)

        # 'Force Reload Aircraft' was removed from this menu. The Ctrl+Shift+R
        # shortcut still invokes mw.force_reload_aircraft (MainWindow.__init__).

        sc_overrides_action = QAction('SimConnect/Dataref Overrides Editor', mw)

        def do_open_sc_override_dialog():
            dialog = SCOverridesEditor(mw)
            # Overrides save immediately in the editor; refresh the status
            # pill once the dialog closes so it reflects any changes.
            dialog.finished.connect(lambda *_: mw.refresh_telem_override_pill(force=True))
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()

        # dialog.exec_()
        sc_overrides_action.triggered.connect(do_open_sc_override_dialog)
        utilities_menu.addAction(sc_overrides_action)


        trim_cal_action = QAction('Elevator Trim Calibration...', mw)
        trim_cal_action.triggered.connect(mw.open_trim_calibration_dialog)
        utilities_menu.addAction(trim_cal_action)

        # A window on the tap's shared-memory mirror: whether a game is
        # publishing, which devices the wrapper captured, and what every
        # effect slot is being told - with a timestamped change log to
        # save and send in.  The remote-troubleshooting answer to "no
        # forces in DCS".
        tap_monitor_action = QAction('DirectInput Tap Monitor...', mw)
        tap_monitor_action.triggered.connect(mw.open_tap_monitor)
        utilities_menu.addAction(tap_monitor_action)

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
                    mw.hide()
                except Exception as e:
                    logging.error(f"EXCEPTION: {e}")
            self.hide_window_action.triggered.connect(do_hide_window)
            self.window_menu.addAction(self.hide_window_action)


        """ Add Log Menu """

        self.log_menu = self.menu.addMenu('Log')
        self.log_window_action = QAction("Open Console Log", mw)

        def do_toggle_log_window():
            if G.log_window.isVisible():
                G.log_window.hide()
            else:
                G.log_window.move(mw.x()+50, mw.y()+100)
                G.log_window.show()

        self.log_window_action.triggered.connect(do_toggle_log_window)
        self.log_menu.addAction(self.log_window_action)


        """ Add Help Menu """

        help_menu = self.menu.addMenu('Help')

        notes_action = QAction('Release Notes', mw)
        def do_open_file(url):
            try:
                file_url = QUrl.fromLocalFile(url)
                QDesktopServices.openUrl(file_url)
            except Exception as e:
                logging.error(f"There was an error opening the file: {str(e)}")
        notes_action.triggered.connect(lambda : do_open_file(notes_url))
        help_menu.addAction(notes_action)

        docs_action = QAction('Documentation', mw)
        docs_action.triggered.connect(lambda: mw.open_url(doc_url))
        help_menu.addAction(docs_action)

        self.support_action = QAction("Create support bundle", mw)
        self.support_action.triggered.connect(lambda: utils.create_support_bundle(G.userconfig_rootpath))
        help_menu.addAction(self.support_action)

    def add_instance_log_menu(self):
        self.log_menu.addAction(self.log_window_action)
        if G.master_instance and G.system_settings.get('autolaunchMaster', 0):
            self.child_log_menu = self.log_menu.addMenu('Open Child Logs')

            self.log_action = {}
            for d in ALL_ROLES:
                if d in G.launched_instances:
                    def do_show_child_log(child=d):
                        G.ipc_instance.send_broadcast_message(f'SHOW LOG:{child}')

                    self.log_action[d] = QAction(f'{d} Log'.capitalize())
                    self.log_action[d].triggered.connect(lambda _, child=d: do_show_child_log(child))
                    self.child_log_menu.addAction(self.log_action[d])

    def add_device_view_actions(self, views, current, on_chosen, group_starts=(),
                                side_right=False, on_side_chosen=None):
        """Window menu: a 'Devices' submenu choosing where the devices are
        shown. ``views`` is ``[(key, label), ...]`` and ``on_chosen(key)`` is
        called with the pick; a line is ruled above each key in
        ``group_starts``. With ``on_side_chosen``, a checkable item under
        the views says which side the side panels are on, and calls it with
        True for the right. Lazily creates the Window menu if this instance
        hasn't needed one yet (a solo master/child never gets one
        otherwise)."""
        if not hasattr(self, 'window_menu'):
            self.window_menu = self.menu.addMenu('Window')
        elif self.window_menu.actions():
            self.window_menu.addSeparator()
        submenu = self.window_menu.addMenu('Devices')
        self.device_view_actions = {}
        group = QActionGroup(self.mw)
        group.setExclusive(True)
        for key, label in views:
            if key in group_starts:
                submenu.addSeparator()
            action = QAction(label, self.mw)
            action.setCheckable(True)
            action.setChecked(key == current)
            action.triggered.connect(lambda _checked, k=key: on_chosen(k))
            group.addAction(action)
            submenu.addAction(action)
            self.device_view_actions[key] = action
        self.device_side_action = None
        if on_side_chosen is not None:
            submenu.addSeparator()
            self.device_side_action = QAction("Side panels on the right", self.mw)
            self.device_side_action.setCheckable(True)
            self.device_side_action.setChecked(bool(side_right))
            self.device_side_action.triggered.connect(lambda checked: on_side_chosen(bool(checked)))
            submenu.addAction(self.device_side_action)

    def set_device_side_checked(self, right: bool):
        """Keep the item's mark in step when the side was changed from
        somewhere else (a right-click menu, a drop on the other edge)."""
        action = getattr(self, 'device_side_action', None)
        if action is not None and action.isChecked() != bool(right):
            action.setChecked(bool(right))

    def set_device_view_checked(self, key):
        """Keep the submenu's mark in step when the view was changed from
        somewhere else (a view-toggle button, a right-click menu)."""
        action = getattr(self, 'device_view_actions', {}).get(key)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def set_configurator_action_enabled(self, enabled, tooltip=''):
        # the action is part of the Debug menu, which only exists with the
        # debug registry key (or Alt+D) - on a normal install there is
        # nothing to gate
        action = getattr(self, 'configurator_settings_action', None)
        if action is None:
            return
        action.setEnabled(enabled)
        action.setToolTip(tooltip)

    def add_debug_menu(self):
        mw = self.mw
        # debug mode
        for action in self.menu.actions():
            if action.text() == "Debug":
                return
        debug_menu = self.menu.addMenu("Debug")

        teleplot_action = QAction("Teleplot Setup", mw)
        def do_open_teleplot_setup_dialog():
            mw.teleplot_dialog = TeleplotSetupDialog(mw)
            mw.teleplot_dialog.cb_send.setChecked(utils.teleplot.enabled)
            mw.teleplot_dialog.exec()
        teleplot_action.triggered.connect(do_open_teleplot_setup_dialog)
        debug_menu.addAction(teleplot_action)

        show_simvar_action = QAction("Show simvar in telem window", mw)
        def do_toggle_simvar_telemetry():
            mw.monitor_panel.set_show_simvars(not mw.monitor_panel.show_simvars)
            show_simvar_action.setChecked(mw.monitor_panel.show_simvars)

        show_simvar_action.triggered.connect(do_toggle_simvar_telemetry)
        show_simvar_action.setCheckable(True)
        debug_menu.addAction(show_simvar_action)

        show_order_action = QAction("Show settings order numbering", mw)
        def do_toggle_order_numbering():
            SettingsLayout.show_order_debug = not  SettingsLayout.show_order_debug
            show_order_action.setChecked(SettingsLayout.show_order_debug)

        show_order_action.triggered.connect(do_toggle_order_numbering)
        show_order_action.setCheckable(True)
        debug_menu.addAction(show_order_action)

        show_replaced = QAction("Show settings source", mw)
        def do_toggle_replaced():
            SettingsLayout.show_replaced = not SettingsLayout.show_replaced
            show_replaced.setChecked(SettingsLayout.show_replaced)

        show_replaced.triggered.connect(do_toggle_replaced)
        show_replaced.setCheckable(True)
        debug_menu.addAction(show_replaced)


        show_settingname_action = QAction("Show settings internal name", mw)
        def do_toggle_settingsnames():
            SettingsLayout.show_settings_names = not  SettingsLayout.show_settings_names
            show_settingname_action.setChecked(SettingsLayout.show_settings_names)

        show_settingname_action.triggered.connect(do_toggle_settingsnames)
        show_settingname_action.setCheckable(True)
        debug_menu.addAction(show_settingname_action)

        # Effect preview (hardware check for the preview runner): one
        # entry per shipped spec, played on the device with synthetic
        # telemetry and the settings tab's current model.
        preview_menu = debug_menu.addMenu("Preview Effect")
        for name, spec in PREVIEW_SPECS.items():
            preview_action = QAction(f"{name}  ({spec.kind}, {spec.duration:g}s)", mw)
            preview_action.triggered.connect(
                lambda checked=False, s=spec: mw.preview.start(s))
            preview_menu.addAction(preview_action)
        stop_preview_action = QAction("Stop preview", mw)
        stop_preview_action.triggered.connect(mw.preview.stop)
        preview_menu.addAction(stop_preview_action)

        configurator_settings_action = QAction('Configurator Gain Override', mw)
        def do_open_configurator_dialog():
            dialog = ConfiguratorDialog(mw)
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        configurator_settings_action.triggered.connect(do_open_configurator_dialog)
        self.configurator_settings_action = configurator_settings_action
        mw.refresh_configurator_gating()
        debug_menu.addAction(configurator_settings_action)

        sc_overrides_action = QAction('SimConnect/Dataref Overrides Editor', mw)
        def do_open_sc_override_dialog():
            dialog = SCOverridesEditor(mw)
            # Overrides save immediately in the editor; refresh the status
            # pill once the dialog closes so it reflects any changes.
            dialog.finished.connect(lambda *_: mw.refresh_telem_override_pill(force=True))
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()
        # dialog.exec_()
        sc_overrides_action.triggered.connect(do_open_sc_override_dialog)
        debug_menu.addAction(sc_overrides_action)

        test_update = QAction('Test updater', mw)
        def do_test_update():
            mw.updates._update_available = True
            mw.updates.perform_update()
        test_update.triggered.connect(do_test_update)
        debug_menu.addAction(test_update)

        if G.master_instance:
            custom_userconfig_action = QAction("Load Custom User Config", mw)
            custom_userconfig_action.triggered.connect(lambda: utils.load_custom_userconfig())
            debug_menu.addAction(custom_userconfig_action)
