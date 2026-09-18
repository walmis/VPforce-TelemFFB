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

"""HeaderPanel: the window's top header - the VPforce logo, the compact
mini device row beneath it, and the Application Status box - sharing one
full-width row above the Active Devices / tabs split below it.

Replaces MainWindow's inline construction of the logo ``QLabel``, the
``MiniDevicePanel`` instance (``telemffb.ui.panels.DevicePanel``) and the
``AppStatusWidget`` instance (``telemffb.ui.widgets.custom_widgets``), plus
its ``_on_scope_status_changed`` relay that kept the vpconf-profile and
gain-override indicators in step with ``AppState.scope_status_changed`` -
see ``bind()``, which replaces that relay one-for-one.

Two things this panel deliberately does NOT own, because they reach outside
the header:

- Mirroring this panel's mini device row against the full Active Devices
  panel (``DeviceIconPanel``) and deciding whether the mini row or the full
  frame is shown - that also depends on the Active Devices frame and the
  tab widget, neither of which is part of the header. MainWindow's
  ``_sync_mini_device_panel`` / ``_sync_devices_display`` keep doing that,
  reaching this panel's mini row through the public ``device_mini_panel``
  attribute, exactly as they already reach the full panel through
  ``self.device_panel``.
- Wiring ``device_mini_panel.DeviceClicked`` (scope switching) and three of
  ``AppStatusWidget``'s signals (profile-combo change, profile-notes click,
  split-profile click) to MainWindow's own methods/dialogs - those
  connections are made by whoever constructs this panel, right after
  construction, the same way MainWindow wires ``device_panel.DeviceClicked``
  itself.

``AppStatusWidget`` is already a complete, self-contained widget with its
own public methods and signals; this panel does not wrap them in a second
API - callers reach the instance through the public ``status_container``
attribute, same as before extraction.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import telemffb.globals as G
from telemffb.state.app_state import AppState
from telemffb.ui.panels.DevicePanel import MiniDevicePanel
from telemffb.ui.widgets.custom_widgets import AppStatusWidget
from telemffb.utils import HiDpiPixmap


class HeaderPanel(QWidget):
    """The logo / mini-device-row / Application-Status row that runs full
    width above the Active Devices + tabs split."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        """ Main App Logo, with the compact mini device row (small
        per-device icons, only the active one showing its name) beneath
        it - stands in for the Active Devices frame whenever that frame
        is hidden, in what would otherwise be empty space between the
        logo and the devices/tabs row below. See
        MainWindow._sync_devices_display() and _sync_mini_device_panel(). """

        t_logo = QLabel()
        t_pixmap = HiDpiPixmap(G.vpf_logo)
        logo_width = 200
        logo_height = round(t_pixmap.height() * logo_width / t_pixmap.width())
        t_pixmap = t_pixmap._scaled(logo_width, logo_height)
        t_logo.setPixmap(t_pixmap)

        self.device_mini_panel = MiniDevicePanel()

        logo_column_layout = QVBoxLayout()
        logo_column_layout.setContentsMargins(0, 0, 0, 0)
        logo_column_layout.setSpacing(6)
        logo_column_layout.addWidget(t_logo, alignment=Qt.AlignmentFlag.AlignLeft)
        logo_column_layout.addWidget(self.device_mini_panel, alignment=Qt.AlignmentFlag.AlignLeft)

        """ Application Status box - sim-status and application-status
        fields as two columns inside a single group box. """

        self.status_container = AppStatusWidget(master_instance=G.master_instance, parent=self)
        status_group = QGroupBox("Application Status")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(10, 18, 10, 8)
        status_layout.addWidget(self.status_container)
        self.status_container.sim_status_label.set_waiting()

        layout.addLayout(logo_column_layout)
        layout.setAlignment(logo_column_layout, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(status_group, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)

    def bind(self, state: AppState) -> None:
        """Subscribe to ``state.scope_status_changed`` and paint its current
        value immediately - nothing re-emits just because a new subscriber
        connected, so the initial paint has to be pulled, not waited for."""
        state.scope_status_changed.connect(self._on_scope_status_changed)
        status = state.current_status()
        self._on_scope_status_changed(
            status.scope, status.vpconf, status.any_vpconf, status.ovd, status.any_ovd)

    def _on_scope_status_changed(self, scope, vpconf, any_vpconf, ovd, any_ovd):
        """AppState.scope_status_changed relay: update the vpconf-profile
        and gain-override indicators to reflect the device currently
        selected as the config scope.

        AppState already derives and deduplicates this (the master shows
        its own state while scoped to its own device, and the state
        reported over IPC while scoped to a child; child instances always
        show their own state) - this only forwards to the widget's own
        (queued, cross-thread-safe) request signals, so the pulse
        animation still only fires when something actually changed.
        """
        self.status_container.request_set_active_vpconf.emit(vpconf, any_vpconf)
        self.status_container.request_set_active_configurator.emit(ovd, any_ovd)
