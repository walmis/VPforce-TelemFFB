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

"""Application state: the model layer background code writes to instead of
reaching into MainWindow.

MainWindow has historically been its own model - widgets held the truth,
and background code forced repaints by calling into the window directly
(the old ``MainWindow.refresh_scope_status_indicators``, called from the
telemetry thread, the IPC thread and the settings UI, with a ``force``
flag to defeat its own dedup key because there was nowhere else for that
dedup state to live). ``AppState`` is the first slice of a model MainWindow
no longer owns: writers call its setters, it derives and deduplicates the
view state exactly once, and widgets subscribe to a signal instead of
being poked with an escape hatch. Later steps grow it with more of
MainWindow's state; this one only carries what the "scope status"
indicators (active vpconf profile / gain-override-active, per device) need.

Relationship to ``telemffb.app_events``: ``AppEvents`` is a fire-and-forget
pub/sub hub for one-off notifications between components that should not
know each other (a settings save happened - whoever cares decides for
itself what to do about it). ``AppState`` is a persistent model: it holds
current values, not just announcements that something happened, and its
signal always carries the latest derived, deduplicated state rather than
an ad-hoc payload a consumer has to interpret. The two are complementary,
not layered - ``AppState`` does not publish through ``app_events``, though
a later step may have it *consume* an app-wide event (``device_config_changed``)
as one of its inputs.
"""

import threading
from dataclasses import dataclass
from typing import Dict, Optional

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True)
class ScopeStatus:
    """The scope-status indicators' derived view state.

    ``scope`` is the device the indicators currently describe; ``vpconf``/
    ``ovd`` are what to show for it; ``any_vpconf``/``any_ovd`` say whether
    the row should be visible at all (any device - master or child - is
    using the feature) versus a placeholder-less hide.
    """
    scope: str
    vpconf: str
    any_vpconf: bool
    ovd: bool
    any_ovd: bool


class AppState(QObject):
    """Single source of truth for the "scope status" indicators, and the
    seed of a broader app-state model.

    Inputs, each its own setter, called from whichever thread owns the
    write:

    - ``set_own_device_type`` / ``set_master`` - this instance's fixed
      identity; set once at startup.
    - ``set_scope`` - the device currently selected in the config-scope
      switcher (main thread: the device panel).
    - ``set_own_vpconf`` / ``set_own_gain_overrides_active`` - this
      instance's own state, reported by the telemetry thread,
      ``upload_vpconf_profile`` (telemetry or main thread) and the
      settings UI (main thread).
    - ``set_child_status`` - master only: what a child last reported over
      IPC (the STATUS keepalive and the effects payload both carry these
      two keys), called from the IPC thread.

    All of the above lives behind ``_lock``, since it is written from more
    than one thread. ``scope_status_changed`` is emitted only when the
    derived view actually differs from the last value shown, so callers
    never need a ``force=True`` escape hatch to defeat a stale dedup key -
    setting something to the value it already has never emits.

    ``scope_status_changed`` is safe to connect to a slot on a widget
    living on the main thread regardless of which thread emits it: PyQt
    queues the delivery automatically because the connection type is
    resolved from the *receiver's* thread affinity, not the emitter's -
    the same mechanism ``AppStatusWidget.request_set_active_vpconf`` (a
    plain signal re-emitted at the widget) already relies on.
    """

    #: (scope, vpconf, any_vpconf, ovd, any_ovd) - see ScopeStatus.
    scope_status_changed = pyqtSignal(str, str, bool, bool, bool)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._own_device_type: str = ''
        self._master: bool = False
        self._scope: Optional[str] = None
        self._own_vpconf: str = ''
        self._own_gain_ovd: bool = False
        # Master-only: {device_type: {'vpconf': str, 'gain_ovd_active': bool}}
        # - what each child last reported over IPC.
        self._child_status: Dict[str, Dict[str, object]] = {}
        self._last_shown: Optional[tuple] = None

    # ---- identity (set once at startup) --------------------------------

    def set_own_device_type(self, device_type: str) -> None:
        with self._lock:
            self._own_device_type = device_type or ''
        self._recompute_and_emit()

    def set_master(self, master: bool) -> None:
        with self._lock:
            self._master = bool(master)
        self._recompute_and_emit()

    # ---- inputs ----------------------------------------------------------

    def set_scope(self, scope: Optional[str]) -> None:
        """The device currently selected as the config scope (the device
        panel's active selection)."""
        with self._lock:
            if self._scope == scope:
                return
            self._scope = scope
        self._recompute_and_emit()

    def set_own_vpconf(self, path: Optional[str]) -> None:
        """This instance's own last-pushed vpconf profile path."""
        value = path or ''
        with self._lock:
            if self._own_vpconf == value:
                return
            self._own_vpconf = value
        self._recompute_and_emit()

    def set_own_gain_overrides_active(self, active: bool) -> None:
        """Whether this instance's own configurator gain overrides are
        active."""
        value = bool(active)
        with self._lock:
            if self._own_gain_ovd == value:
                return
            self._own_gain_ovd = value
        self._recompute_and_emit()

    def set_child_status(self, device: str, *, vpconf: Optional[str] = None,
                          gain_ovd_active: Optional[bool] = None) -> None:
        """Master-side: record what ``device`` last reported over IPC.

        Either field may be left ``None`` when the payload that triggered
        this call did not carry it - the STATUS keepalive and the effects
        payload both carry both keys today, but a caller should not have
        to know that to report just one.
        """
        with self._lock:
            entry = self._child_status.setdefault(
                device, {'vpconf': '', 'gain_ovd_active': False})
            changed = False
            if vpconf is not None:
                value = vpconf or ''
                if entry['vpconf'] != value:
                    entry['vpconf'] = value
                    changed = True
            if gain_ovd_active is not None:
                value = bool(gain_ovd_active)
                if entry['gain_ovd_active'] != value:
                    entry['gain_ovd_active'] = value
                    changed = True
            if not changed:
                return
        self._recompute_and_emit()

    # ---- derived view ----------------------------------------------------

    def current_status(self) -> ScopeStatus:
        """The current derived view, computed fresh - not gated by the
        dedup key. A subscriber calls this right after connecting, so the
        initial paint is correct regardless of setter call order at
        startup (nothing re-emits just because a new listener showed up)."""
        return self._derive()

    def _derive(self) -> ScopeStatus:
        with self._lock:
            own_device_type = self._own_device_type
            master = self._master
            scope = self._scope or own_device_type
            own_vpconf = self._own_vpconf
            own_ovd = self._own_gain_ovd
            if scope == own_device_type or not master:
                vpconf, ovd = own_vpconf, own_ovd
            else:
                entry = self._child_status.get(scope, {})
                vpconf = entry.get('vpconf', '') or ''
                ovd = bool(entry.get('gain_ovd_active', False))
            # A row is only present at all while at least one device
            # (master or child) is using the feature; devices without a
            # value show a "(None)" placeholder instead (widget's job) so
            # the panel geometry stays identical across scopes.
            any_vpconf = bool(own_vpconf) or (
                master and any(e.get('vpconf') for e in self._child_status.values()))
            any_ovd = own_ovd or (
                master and any(e.get('gain_ovd_active') for e in self._child_status.values()))
        return ScopeStatus(scope=scope, vpconf=vpconf, any_vpconf=bool(any_vpconf),
                            ovd=bool(ovd), any_ovd=bool(any_ovd))

    def _recompute_and_emit(self) -> None:
        status = self._derive()
        shown = (status.scope, status.vpconf, status.any_vpconf, status.ovd, status.any_ovd)
        with self._lock:
            if shown == self._last_shown:
                return
            self._last_shown = shown
        self.scope_status_changed.emit(*shown)
