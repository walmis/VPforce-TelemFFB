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

"""TrayController: the system tray icon, its context menu, and the tray-
notification / status-icon bookkeeping that used to live inline on
MainWindow (``add_system_tray``, ``pop_tray_notification``, and the
icon/tooltip switching in ``update_sim_indicators``).

Like ``OfflineEditorPanel``/``MainMenu``, this takes the owning
``MainWindow`` as ``mainwindow`` and connects tray actions back to its
public methods rather than duplicating that logic here. The tray icon
itself (``self.icon``) is created unconditionally (a child instance gets
one too, inert, exactly as before) but ``build()`` - which wires up the
context menu and calls ``.show()`` - is only ever invoked for the master
instance, from ``setup_master_instance``.

``set_status`` is the one behavioral entry point MainWindow calls on every
status transition (error/paused/running); ``show_notification`` is the
general-purpose tray popup used elsewhere (profile-change / new-aircraft
toasts, closing to tray).
"""

import time

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

import telemffb.globals as G
from telemffb.utils import exit_application


class TrayController:
    def __init__(self, mainwindow):
        self.mw = mainwindow
        self.icon = QSystemTrayIcon(mainwindow)
        self._notifications = {}

    def build(self):
        """Wire up the tray icon's context menu and show it. Master-only,
        called once from MainWindow.setup_master_instance."""
        mw = self.mw
        self.icon.setIcon(QIcon(":/image/vpforceicon.png"))
        self.icon.setToolTip("VPforce TelemFFB")

        # Create the tray menu
        tray_menu = QMenu()
        show_action = QAction("Show Window", mw)

        def do_show_main_window(trigger):
            if isinstance(trigger, QSystemTrayIcon.ActivationReason):
                if trigger == QSystemTrayIcon.ActivationReason.DoubleClick:
                    mw.showNormal()  # Restore the window to its normal state if minimized
                    mw.show()
                    mw.raise_()
                    mw.activateWindow()
            elif isinstance(trigger, str) and trigger == "show":
                mw.showNormal()  # Restore the window to its normal state if minimized
                mw.show()
                mw.raise_()
                mw.activateWindow()
            if G.is_exe:
                start_with_windows_action.setChecked(mw.toggle_start_with_windows())
            start_minimized_action.setChecked(G.system_settings.get('startToTray', False))
            send_to_tray_action.setChecked(G.system_settings.get('closeToTray', False))

        self.icon.activated.connect(do_show_main_window)
        show_action.triggered.connect(lambda: do_show_main_window('show'))

        tray_menu.addAction(show_action)

        # Create the "Options" menu
        options_menu = QMenu("Options", mw)

        # Setup Start With Windows menu option
        if G.is_exe:
            start_with_windows_action = QAction("Start With Windows", mw)
            start_with_windows_action.setCheckable(True)
            start_with_windows_action.setChecked(G.system_settings.get('startWithWindows', False))

            def do_toggle_set_start_with_windows(checked):
                mw.toggle_start_with_windows(checked)

            start_with_windows_action.triggered.connect(lambda checked: do_toggle_set_start_with_windows(checked))

            options_menu.addAction(start_with_windows_action)

        # Setup Start Minimized menu option
        start_minimized_action = QAction("Start in Tray", mw)
        start_minimized_action.setCheckable(True)
        start_minimized_action.setChecked(G.system_settings.get('startToTray', False))

        def do_toggle_set_start_minimized(checked):
            G.system_settings.setValue('startToTray', checked)

        start_minimized_action.triggered.connect(lambda checked: do_toggle_set_start_minimized(checked))

        options_menu.addAction(start_minimized_action)

        # Setup Send to Tray menu option
        send_to_tray_action = QAction("Closing App Sends to Tray", mw)
        send_to_tray_action.setCheckable(True)
        send_to_tray_action.setChecked(G.system_settings.get('closeToTray', False))

        def do_toggle_set_send_to_tray(checked):
            G.system_settings.setValue('closeToTray', checked)

        send_to_tray_action.triggered.connect(lambda checked: do_toggle_set_send_to_tray(checked))

        options_menu.addAction(send_to_tray_action)

        tray_menu.addMenu(options_menu)

        # Create the "Instances" menu
        if G.launched_instances:
            show_menu = QMenu("Instances", mw)
            show_child_window_action = {}
            for d in ["joystick", "pedals", "collective", 'trimwheel']:
                if d in G.launched_instances:
                    def do_show_child_window(child=d):
                        G.ipc_instance.send_broadcast_message(f'SHOW WINDOW:{child}')

                    show_child_window_action[d] = QAction(f'Show {d.capitalize()} Instance', mw)
                    show_child_window_action[d].triggered.connect(lambda _, child=d: do_show_child_window(child))
                    show_menu.addAction(show_child_window_action[d])
            tray_menu.addMenu(show_menu)

        quit_action = QAction("Quit TelemFFB", mw)
        quit_action.triggered.connect(exit_application)
        tray_menu.addAction(quit_action)

        self.icon.setContextMenu(tray_menu)
        # Show the tray icon
        self.icon.show()
        if mw.isHidden():
            #  don't show, send message to tray icon that will pop to notify user that TelemFFB is running in Tray
            self.show_notification(
                None,
                "TelemFFB is running in the system tray.  Double-Click the VPforce Icon to show or right click to set options in the context menu",
                5
            )

    def show_notification(self, title, message, renew_period):
        current_time = time.time()
        notification_key = (title, message)

        # Check if the notification was shown within the specified period
        if notification_key in self._notifications:
            last_shown_time = self._notifications[notification_key]
            if current_time - last_shown_time < renew_period:
                # Notification was shown recently, do not show again
                return
        # Show the notification
        icon = QIcon(":/image/vpforceicon.png")
        self.icon.showMessage(title, message, icon)
        # Update the last shown time
        self._notifications[notification_key] = current_time
        self.icon.messageClicked.connect(self.mw.show)

    def set_status(self, state, source, message=None):
        """Set the tray icon/tooltip for the current status ('error' /
        'paused' / 'running'), and pop the error notification. Master-only
        - a child instance has no system tray."""
        if not G.master_instance:
            return

        if state == 'error':
            # error is true and was previously false.  Set sys tray attributes and pop notification

            self.icon.setIcon(QIcon(':/image/vpforceicon_error.png'))
            self.icon.setToolTip(f"VPforce TelemFFB -- There is an error occurring:\n\n{message}")

            # The popup's job is initial attention; the tray icon and
            # tooltip carry the persistent state.  A short renew period
            # made a persistent error a metronome - the same message
            # popped every couple of seconds for as long as it held.
            self.show_notification("Error", message, renew_period=300)

        elif state == 'paused':
            self.icon.setIcon(QIcon(':/image/vpforceicon_paused.png'))
            self.icon.setToolTip(f"VPforce TelemFFB\n{source} is Paused ")

        else:  # running
            self.icon.setIcon(QIcon(':/image/vpforceicon_run.png'))
            self.icon.setToolTip(f"VPforce TelemFFB\n{source} is Running ")
            # re-show the "current aircraft" label once error cleared
