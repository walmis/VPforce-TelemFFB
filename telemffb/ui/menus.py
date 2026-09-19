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

``menu``, ``window_menu``, ``log_menu``, ``log_window_action`` and
``update_action`` are mirrored onto the MainWindow itself (``mainwindow.menu``
etc.), since other MainWindow methods (``add_instance_log_menu``,
``setup_master_instance``, ``perform_update``) read them directly.
``add_debug_menu`` mirrors ``configurator_settings_action`` the same way,
for ``refresh_configurator_gating``.
"""

import logging
import os
import sys

from PyQt6 import QtCore
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox

import telemffb.globals as G
import telemffb.utils as utils
from telemffb import match_history
from telemffb.preview.engine import PREVIEW_SPECS
from telemffb.ui.dialogs.ConfiguratorDialog import ConfiguratorDialog
from telemffb.ui.dialogs.SCOverridesEditor import SCOverridesEditor
from telemffb.ui.dialogs.TeleplotSetupDialog import TeleplotSetupDialog
from telemffb.ui.widgets.SettingsLayout import SettingsLayout
from telemffb.utils import exit_application


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
        mw.menu = menubar
        assert self.menu is not None
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

        mw.update_action = QAction('Install Latest TelemFFB', mw)
        mw.update_action.triggered.connect(mw.update_from_menu)
        if not G.release_version:
            utilities_menu.addAction(mw.update_action)
        mw.update_action.setDisabled(True)

        download_action = QAction('Download Other Versions', mw)
        download_action.triggered.connect(lambda: mw.open_url(dl_url))
        utilities_menu.addAction(download_action)

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

        reload_action = QAction('Force Reload Aircraft (Ctrl+Shift+R)', mw)
        reload_action.triggered.connect(mw.force_reload_aircraft)
        utilities_menu.addAction(reload_action)

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
            mw.window_menu = self.menu.addMenu('Window')

            def do_toggle_child_windows(toggle):
                if toggle == 'show':
                    G.ipc_instance.send_broadcast_message("SHOW WINDOW")
                elif toggle == 'hide':
                    G.ipc_instance.send_broadcast_message("HIDE WINDOW")

            self.show_children_action = QAction('Show Child Instance Windows')
            self.show_children_action.triggered.connect(lambda: do_toggle_child_windows('show'))
            mw.window_menu.addAction(self.show_children_action)
            self.hide_children_action = QAction('Hide Child Instance Windows')
            self.hide_children_action.triggered.connect(lambda: do_toggle_child_windows('hide'))
            mw.window_menu.addAction(self.hide_children_action)

        if G.child_instance:
            """
            Add Child instance window menu
            """
            mw.window_menu = self.menu.addMenu('Window')
            self.hide_window_action = QAction('Hide Window')
            def do_hide_window():
                try:
                    mw.hide()
                except Exception as e:
                    logging.error(f"EXCEPTION: {e}")
            self.hide_window_action.triggered.connect(do_hide_window)
            mw.window_menu.addAction(self.hide_window_action)


        """ Add Log Menu """

        mw.log_menu = self.menu.addMenu('Log')
        mw.log_window_action = QAction("Open Console Log", mw)

        def do_toggle_log_window():
            if G.log_window.isVisible():
                G.log_window.hide()
            else:
                G.log_window.move(mw.x()+50, mw.y()+100)
                G.log_window.show()

        mw.log_window_action.triggered.connect(do_toggle_log_window)
        mw.log_menu.addAction(mw.log_window_action)


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
        mw.configurator_settings_action = configurator_settings_action
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
            mw._update_available = True
            mw.perform_update()
        test_update.triggered.connect(do_test_update)
        debug_menu.addAction(test_update)

        if G.master_instance:
            custom_userconfig_action = QAction("Load Custom User Config", mw)
            custom_userconfig_action.triggered.connect(lambda: utils.load_custom_userconfig())
            debug_menu.addAction(custom_userconfig_action)
