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

"""HeaderPanel: the window's Application Status box, the full-width row
above the offline editor and the tabs.

Replaces MainWindow's inline construction of the ``AppStatusWidget``
instance (``telemffb.ui.widgets.custom_widgets``) and its
``refresh_scope_status_indicators()``, which pushed the vpconf-profile and
gain-override indicators into that widget - ``bind()`` now keeps them in
step with ``AppState.scope_status_changed`` instead.

The logo and the compact device row used to share this row. The logo is
now the menu bar's corner widget (``telemffb.ui.menus``) and the device row
belongs to each tab page's header (``telemffb.ui.widgets.TabHeaderBar``),
which leaves the status box the full width of the column.

``AppStatusWidget`` (``status_container``) is an implementation detail of
this panel. Outside code (MainWindow, ``DcsIpcThread``) goes through
HeaderPanel's own plain methods and re-exported signals below instead of
reaching into ``status_container`` directly.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QWidget

import telemffb.globals as G
from telemffb.state.app_state import AppState
from telemffb.ui.widgets.custom_widgets import AppStatusWidget


class HeaderPanel(QWidget):
    """The Application Status row, full width above the tabs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        """ Application Status box - sim-status and application-status
        fields as two columns inside a single group box. """

        self.status_container = AppStatusWidget(master_instance=G.master_instance, parent=self)
        status_group = QGroupBox("Application Status")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(10, 18, 10, 8)
        status_layout.addWidget(self.status_container)
        self.status_container.sim_status_label.set_waiting()

        # Re-exported signals: same bound-signal objects as status_container's
        # own, so connecting/emitting through HeaderPanel is identical to
        # going through status_container directly (including the queued
        # cross-thread delivery request_set_telem_overrides already has).
        self.profile_chosen = self.status_container.profile_chosen
        self.profile_notes_clicked = self.status_container.profile_notes_clicked
        self.split_profile_clicked = self.status_container.split_profile_clicked
        self.request_set_telem_overrides = self.status_container.request_set_telem_overrides

        layout.addWidget(status_group, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)

    # -- Plain pass-throughs to status_container, so callers never reach
    # into the implementation widget directly. --

    def set_profile_choices(self, profiles):
        self.status_container.set_profile_choices(profiles)

    def set_craft_info(self, craft, pattern, profile):
        self.status_container.set_craft_info(craft, pattern, profile)

    def set_profile_state(self, enabled):
        self.status_container.set_profile_state(enabled)

    def set_notes_state(self, enabled, has_notes=False):
        self.status_container.set_notes_state(enabled, has_notes)

    def set_split_state(self, enabled):
        self.status_container.set_split_state(enabled)

    def set_offline(self, source):
        self.status_container.set_offline(source)

    def set_waiting(self, source):
        self.status_container.set_waiting(source)

    def set_fullname(self, full_name):
        self.status_container.set_fullname(full_name)

    def reset_sim_state(self, src):
        self.status_container.reset_sim_state(src)

    def reset(self):
        self.status_container.reset()

    def update_enabled_sims(self, sim, state):
        self.status_container.update_enabled_sims(sim, state)

    def bind(self, state: AppState) -> None:
        """Subscribe to ``state.scope_status_changed`` and
        ``state.sim_status_changed``, and paint each one's current value
        immediately - nothing re-emits just because a new subscriber
        connected, so the initial paint has to be pulled, not waited for."""
        state.scope_status_changed.connect(self._on_scope_status_changed)
        status = state.current_status()
        self._on_scope_status_changed(
            status.scope, status.vpconf, status.any_vpconf, status.ovd, status.any_ovd)

        self._last_sim_state = None
        state.sim_status_changed.connect(self._on_sim_status_changed)
        sim_state, sim_source, sim_message = state.current_sim_status()
        if sim_source:
            self._on_sim_status_changed(sim_state, sim_source, sim_message)

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

    def _on_sim_status_changed(self, state: str, source: str, message: str) -> None:
        """AppState.sim_status_changed relay: apply the sim-status field
        (running/paused/error) that ``SimStatusTracker`` derives, and fire
        the flag/clear-error request signals on the actual error-onset /
        error-cleared transitions - exactly what MainWindow's old
        ``update_sim_indicators`` plus its explicit ``request_clear_error``
        call did, just driven by AppState instead of by MainWindow reaching
        into this widget directly.

        The onset/clear edge is derived here, from the last state this
        panel painted, because AppState only carries the latest value, not
        SimStatusTracker's own error-state bookkeeping.
        """
        if not state:
            # AppState.reset_sim_status: the widget was reset directly;
            # forget the last state so the next error re-flags.
            self._last_sim_state = ''
            return
        was_error = self._last_sim_state == 'error'
        if state == 'error':
            self.status_container.set_error(source)
            if not was_error:
                self.status_container.request_flag_error.emit(message)
        else:
            if state == 'paused':
                self.status_container.set_paused(source)
            else:
                self.status_container.set_running(source)
            if was_error:
                self.status_container.request_clear_error.emit()
        self._last_sim_state = state
