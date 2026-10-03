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

"""TelemFFB effect levels per device role, and the mute: the one API the
UI and IPC use (``G.effect_levels``).

Levels are percentages 0..100, stored per role in the system settings.
The mute is runtime only and owned by the master: every process starts
unmuted, and a child takes its mute state and its levels from the
master's messages (see ``IPCNetworkThread.publish_effect_mute``).

The mute function is one app-wide selection, stored in the global system
settings: what is muted (``mute_mode``: the forces, keeping every condition
type, or everything) and
where (``mute_scope``: the device a mute button is bound to, or every
device).  ``toggle_mute`` and ``set_mute_active`` perform it; the mute
buttons and the bound hardware button (``MuteButtonBinding``) call them.

Which level sliders are pinned above the settings list
(``pinned_levels``) is one global setting as well, the same set for every
device in scope, and so is whether those controls show at all
(``controls_shown``).  Hiding them changes no level and no mute.

This process's own role drives ``telemffb.hw.effect_levels.levels``;
live effects pick a change up through a replay on the telemetry thread
(``TelemManager.request_effect_levels_reapply``).

Main thread only.
"""

import logging
import time
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import telemffb.globals as G
import telemffb.hw.effect_levels as effect_levels
from telemffb.hw.effect_levels import LEVEL_NAMES, MUTE_ALL, MUTE_KEEP_SPRING, MUTE_MODES, MUTE_OFF
from telemffb.state.mute_button_binding import MuteButtonBinding
from telemffb.utils import DEVICE_ROLES

#: Settings key of each level, instance-scoped (``{role}/{key}``).
LEVEL_KEYS = {name: f"effectLevel{name.capitalize()}" for name in LEVEL_NAMES}
#: Global settings keys of the selected mute function.
MUTE_MODE_KEY = "effectMuteMode"
MUTE_SCOPE_KEY = "effectMuteScope"
#: Global settings key of the pinned level sliders, comma separated.
PINNED_KEY = "effectLevelsPinned"
#: Pinned when nothing usable is stored.
DEFAULT_PINNED = ("master",)
#: Global settings key: whether the controls above the settings list show.
CONTROLS_SHOWN_KEY = "effectLevelsShown"
#: Shortest interval between replays requested by a slider drag.
_DRAG_REPLAY_MS = 80

#: Modes a mute can apply.
BUTTON_MUTE_MODES = (MUTE_KEEP_SPRING, MUTE_ALL)
#: Where a mute applies: the device in question, or every device.
SCOPE_DEVICE = "device"
SCOPE_ALL = "all"
MUTE_SCOPES = (SCOPE_DEVICE, SCOPE_ALL)

_MODE_TEXT = {MUTE_KEEP_SPRING: "haptics, control feel kept", MUTE_ALL: "all effects"}


def _percent(value) -> int:
    """``value`` as an integer percentage clamped to 0..100.

    :raises ValueError: a string that is not a number, or NaN.
    :raises TypeError: not a number.
    """
    return min(100, max(0, int(round(float(value)))))


def _levels_text(values: dict) -> str:
    return ", ".join(f"{name} {values[name]}%" for name in LEVEL_NAMES)


def _pinned_names(stored) -> tuple[str, ...]:
    """The level names in a stored pinned set, in ``LEVEL_NAMES`` order.

    Accepts a comma separated string or a list (an ini file can hand back
    either); anything else reads as ``DEFAULT_PINNED``.  Unknown names are
    dropped, and an empty string is an empty set.
    """
    if isinstance(stored, str):
        names = stored.split(",")
    elif isinstance(stored, (list, tuple)):
        names = [n for n in stored if isinstance(n, str)]
    else:
        return DEFAULT_PINNED
    names = {n.strip().lower() for n in names}
    return tuple(name for name in LEVEL_NAMES if name in names)


def _shown_flag(stored) -> bool:
    """A stored on/off value: a bool, a number, or the strings an ini file
    or the registry hands back.  Anything unreadable reads as on."""
    if isinstance(stored, str):
        text = stored.strip().lower()
        if text in ("0", "false", "no", "off"):
            return False
        return True
    if isinstance(stored, (bool, int, float)):
        return bool(stored)
    return True


class EffectLevelsController(QObject):
    """Per-role effect levels and the runtime mute.

    Own role: applied to this process's level table.  A child's role, in
    the master: sent to that child.  In a child only its own role does
    anything, and the master's changes arrive through
    ``apply_master_levels`` and ``apply_master_mute``.
    """

    #: a role's levels changed (the role)
    levels_changed = pyqtSignal(str)
    #: any role's muted state, or the selected mute mode or scope, changed
    mute_changed = pyqtSignal()
    #: the set of pinned level sliders changed
    pins_changed = pyqtSignal()
    #: the controls above the settings list were shown or hidden (shown)
    controls_shown_changed = pyqtSignal(bool)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._role: str = G.device_type
        self._levels: dict[str, dict[str, int]] = {}   # role -> percent as applied
        self._mode: Optional[str] = None                # selected mute mode, read lazily
        self._scope: Optional[str] = None               # selected mute scope, read lazily
        self._pinned: Optional[tuple[str, ...]] = None  # pinned level names, read lazily
        self._shown: Optional[bool] = None              # controls shown, read lazily
        self._muted: dict[str, str] = {}                # role -> applied mode, muted roles only
        self._all_muted: bool = False                   # a global mute is in force
        self._button = MuteButtonBinding(self)
        # a slider drag asks for a replay per tick; replays are limited to
        # one per _DRAG_REPLAY_MS, with the last tick's value sent when the
        # interval ends
        self._replay_timer = QTimer(self)
        self._replay_timer.setSingleShot(True)
        self._replay_timer.timeout.connect(self._request_replay)
        self._last_replay = 0.0
        own = self.levels(self._role)
        effect_levels.levels.set_levels({name: value / 100 for name, value in own.items()})
        if any(value < 100 for value in own.values()):
            logging.info(f"Effect levels for {self._role}: {_levels_text(own)}")
        try:
            self._button.apply_initial_state()
        except Exception:
            logging.exception("Effect levels: initial mute of the mute button not applied")

    # --- roles ---------------------------------------------------------

    @property
    def own_role(self) -> str:
        return self._role

    def roles(self) -> list[str]:
        """Every configured role: this process's own, then the children
        the master launched, in role order."""
        launched = getattr(G, "launched_instances", None) or {}
        return [self._role] + [r for r in DEVICE_ROLES if r != self._role and r in launched]

    # --- levels --------------------------------------------------------

    def levels(self, role: str) -> dict[str, int]:
        """``role``'s levels in percent, keyed by ``LEVEL_NAMES``."""
        if role not in self._levels:
            self._levels[role] = self._stored_levels(role)
        return dict(self._levels[role])

    def set_levels(self, role: str, values: dict, persist: bool = True) -> None:
        """Set some or all of ``role``'s levels, each clamped to 0..100.

        ``persist=False`` (a slider being dragged) applies without writing
        the settings or logging; ``persist=True`` writes the settings and
        logs a change.

        :raises ValueError: an unknown role or level name, or a value that
            is not a number; nothing is applied then.
        """
        if role not in DEVICE_ROLES:
            raise ValueError(f"unknown device role {role!r}")
        current = self.levels(role)
        new = dict(current)
        for name, value in values.items():
            if name not in LEVEL_KEYS:
                raise ValueError(f"unknown effect level {name!r}")
            try:
                new[name] = _percent(value)
            except TypeError:
                raise ValueError(f"effect level {name!r} must be a number") from None
        changed = new != current
        self._levels[role] = new
        if persist:
            self._store_levels(role, new)
        if changed or persist:
            self._apply_levels(role, new, throttled=not persist)
        if changed:
            self.levels_changed.emit(role)

    def apply_master_levels(self, values) -> None:
        """Child: this process's levels as the master sent them (percent).
        Not stored here; the master stores them.  Unknown names and values
        that are not numbers are ignored."""
        if not isinstance(values, dict):
            return
        clean = {}
        for name, value in values.items():
            if name in LEVEL_KEYS:
                try:
                    clean[name] = _percent(value)
                except (TypeError, ValueError):
                    pass
        if clean:
            self.set_levels(self._role, clean, persist=False)

    def _apply_levels(self, role: str, values: dict[str, int], throttled: bool = False) -> None:
        if role == self._role:
            if effect_levels.levels.set_levels({name: value / 100 for name, value in values.items()}):
                self._request_replay(throttled)
        elif G.master_instance:
            ipc = getattr(G, "ipc_instance", None)
            if ipc is not None:
                ipc.publish_effect_levels(role, values)

    def _stored_levels(self, role: str) -> dict[str, int]:
        out = {}
        for name, key in LEVEL_KEYS.items():
            try:
                out[name] = _percent(G.system_settings.get(key, 100, instance=role))
            except (TypeError, ValueError):
                out[name] = 100
        return out

    def _store_levels(self, role: str, values: dict[str, int]) -> None:
        stored = self._stored_levels(role)
        if stored == values:
            return
        for name, key in LEVEL_KEYS.items():
            if stored[name] != values[name]:
                G.system_settings.setValue(key, values[name], instance=role)
        logging.info(f"Effect levels for {role} set: {_levels_text(values)}")

    # --- pinned sliders ------------------------------------------------

    def pinned_levels(self) -> list[str]:
        """The level names whose sliders show above the settings list, in
        ``LEVEL_NAMES`` order.  The same for every role."""
        if self._pinned is None:
            self._pinned = _pinned_names(G.system_settings.get(PINNED_KEY, ",".join(DEFAULT_PINNED)))
        return list(self._pinned)

    def set_level_pinned(self, name: str, on: bool) -> None:
        """Pin or unpin the slider of level ``name`` and store the set.

        :raises ValueError: an unknown level name.
        """
        if name not in LEVEL_KEYS:
            raise ValueError(f"unknown effect level {name!r}")
        current = self.pinned_levels()
        wanted = set(current) | {name} if on else set(current) - {name}
        pinned = tuple(n for n in LEVEL_NAMES if n in wanted)
        if list(pinned) == current:
            return
        self._pinned = pinned
        G.system_settings.setValue(PINNED_KEY, ",".join(pinned))
        self.pins_changed.emit()

    def controls_shown(self) -> bool:
        """Whether the level controls show above the settings list.  The
        same for every role; shown unless the user hid them."""
        if self._shown is None:
            self._shown = _shown_flag(G.system_settings.get(CONTROLS_SHOWN_KEY, True))
        return self._shown

    def set_controls_shown(self, on: bool) -> None:
        """Show or hide the level controls and store the choice.  No level
        and no mute changes."""
        on = bool(on)
        if on == self.controls_shown():
            return
        self._shown = on
        G.system_settings.setValue(CONTROLS_SHOWN_KEY, on)
        self.controls_shown_changed.emit(on)

    # --- mute ----------------------------------------------------------

    def mute_mode(self) -> str:
        """What a mute silences: ``MUTE_KEEP_SPRING`` (the default) or
        ``MUTE_ALL``.  In a child, the mode the master last applied."""
        if self._mode is None:
            mode = G.system_settings.get(MUTE_MODE_KEY, MUTE_KEEP_SPRING)
            self._mode = mode if mode in BUTTON_MUTE_MODES else MUTE_KEEP_SPRING
        return self._mode

    def set_mute_mode(self, mode: str) -> None:
        """Select and store what a mute silences.  Every muted role
        switches to it at once.

        :raises ValueError: a mode not in ``BUTTON_MUTE_MODES``.
        """
        if mode not in BUTTON_MUTE_MODES:
            raise ValueError(f"unknown mute mode {mode!r}")
        if mode == self.mute_mode():
            return
        self._mode = mode
        G.system_settings.setValue(MUTE_MODE_KEY, mode)
        if self._muted:
            self._set_mute(dict.fromkeys(self._muted, mode), "muted devices")
        else:
            self.mute_changed.emit()

    def mute_scope(self) -> str:
        """Where a mute button's mute applies: ``SCOPE_DEVICE`` (the
        default) or ``SCOPE_ALL``."""
        if self._scope is None:
            scope = G.system_settings.get(MUTE_SCOPE_KEY, SCOPE_DEVICE)
            self._scope = scope if scope in MUTE_SCOPES else SCOPE_DEVICE
        return self._scope

    def set_mute_scope(self, scope: str) -> None:
        """Select and store where a mute button's mute applies.  Mutes
        already in place stay as they are.

        :raises ValueError: a scope not in ``MUTE_SCOPES``.
        """
        if scope not in MUTE_SCOPES:
            raise ValueError(f"unknown mute scope {scope!r}")
        if scope == self.mute_scope():
            return
        self._scope = scope
        G.system_settings.setValue(MUTE_SCOPE_KEY, scope)
        self.mute_changed.emit()

    def toggle_mute(self, role: str) -> None:
        """Perform the selected mute function as a toggle.  Scope device:
        flip ``role``'s mute.  Scope all: mute every configured role, or
        release every muted role when all are muted already."""
        if self.mute_scope() == SCOPE_ALL:
            self.set_muted_all(not self.muted_all())
        else:
            self.set_muted(role, not self.muted(role))

    def set_mute_active(self, role: str, on: bool, scope: Optional[str] = None) -> None:
        """Perform the selected mute function with an explicit state:
        scope device mutes or releases ``role``, scope all every role.
        ``scope`` overrides the selected scope (a held button releases
        with the scope it muted with)."""
        if (scope or self.mute_scope()) == SCOPE_ALL:
            self.set_muted_all(on)
        else:
            self.set_muted(role, on)

    def muted(self, role: str) -> bool:
        return role in self._muted

    def muted_all(self) -> bool:
        """True when every configured role is muted."""
        return all(self.muted(role) for role in self.roles())

    def set_muted(self, role: str, on: bool) -> None:
        """Mute ``role`` with the selected mode, or release it."""
        self._set_mute({role: self.mute_mode() if on else MUTE_OFF}, role)

    def set_muted_all(self, on: bool) -> None:
        """Mute every configured role with the selected mode, or release
        every muted role."""
        roles = self.roles()
        if not on:
            roles += [role for role in self._muted if role not in roles]
        mode = self.mute_mode() if on else MUTE_OFF
        # remembered so a child that connects later is muted too
        self._all_muted = bool(on)
        self._set_mute(dict.fromkeys(roles, mode), "all devices")

    # --- mute button on a device ---------------------------------------

    def mute_button_binding(self) -> tuple[str, int, str, bool]:
        """The hardware mute button: (role, button number, behavior,
        inverted).  Button 0 is not bound; inverted acts only with
        momentary."""
        b = self._button
        return b.role, b.button, b.behavior, b.inverted

    def set_mute_button_binding(self, role: str, button: int, behavior: str,
                                inverted: bool = False) -> None:
        """Bind (button > 0) or unbind (0) the hardware mute button.

        :raises ValueError: see ``MuteButtonBinding.set_binding``.
        """
        self._button.set_binding(role, button, behavior, inverted)

    def on_device_buttons(self, role: str, buttons) -> None:
        """Master: ``role``'s device now has ``buttons`` pressed (button
        numbers).  Connected to Qt signals, so it never raises."""
        if not G.master_instance:
            return
        try:
            self._button.buttons_changed(role, list(buttons or ()))
        except Exception:
            logging.exception(f"Effect levels: mute button on {role} not handled")

    def on_device_status(self, role: str, status: str) -> None:
        """Master: ``role``'s device status (``"ACTIVE"``, or anything
        else for a device no longer reporting).  A device that reports
        while a global mute is in force is muted too.  Never raises."""
        if not G.master_instance:
            return
        try:
            if status == "ACTIVE":
                if self._all_muted and role != self._role and not self.muted(role):
                    self._set_mute({role: self.mute_mode()}, role)
            else:
                self._button.device_lost(role)
        except Exception:
            logging.exception(f"Effect levels: device status for {role} not applied")

    def apply_master_mute(self, mode: str) -> None:
        """Child: this process's mute mode as the master last sent it.
        Repeated on every keepalive; acts only on a difference."""
        if mode in MUTE_MODES:
            if mode != MUTE_OFF:
                self._mode = mode
            self._set_mute({self._role: mode}, self._role)

    def _set_mute(self, changes: dict[str, str], target: str) -> None:
        changes = {role: mode for role, mode in changes.items()
                   if self._muted.get(role, MUTE_OFF) != mode}
        if not changes:
            return
        for role, mode in changes.items():
            if mode == MUTE_OFF:
                self._muted.pop(role, None)
            else:
                self._muted[role] = mode
        if self._role in changes:
            if effect_levels.levels.set_mute(changes[self._role]):
                self._request_replay()
        if G.master_instance:
            ipc = getattr(G, "ipc_instance", None)
            if ipc is not None:
                ipc.publish_effect_mute(dict(self._muted))
        applied = {role: mode for role, mode in changes.items() if mode != MUTE_OFF}
        if applied and target in applied:
            logging.info(f"Effect mute applied to {target} ({_MODE_TEXT[applied[target]]})")
        elif applied:
            modes = ", ".join(f"{role} {_MODE_TEXT[mode]}" for role, mode in applied.items())
            logging.info(f"Effect mute applied to {target}: {modes}")
        else:
            logging.info(f"Effect mute released on {target}")
        self.mute_changed.emit()

    def _request_replay(self, throttled: bool = False) -> None:
        """Re-send the live effects through the levels.  ``throttled``
        requests (slider drags) are limited to one per _DRAG_REPLAY_MS; the
        last one in an interval is sent when it ends."""
        manager = getattr(G, "telem_manager", None)
        if manager is None:
            return
        now = time.perf_counter()
        if throttled and (self._replay_timer.isActive()
                          or now - self._last_replay < _DRAG_REPLAY_MS / 1000):
            self._replay_timer.start(_DRAG_REPLAY_MS)
            return
        self._replay_timer.stop()
        self._last_replay = now
        manager.request_effect_levels_reapply()
