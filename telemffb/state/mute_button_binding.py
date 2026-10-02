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

"""MuteButtonBinding: a device button that performs the selected mute
function (``EffectLevelsController.toggle_mute`` / ``set_mute_active``).

Owned by the master's ``EffectLevelsController``, which feeds it every
device's pressed-button list as it changes: the master's own device from
``MainWindow.get_active_buttons``, a child's from its ``BUTTONS:`` IPC
message.  Neither depends on telemetry, so the button works with no sim,
a paused sim or no aircraft loaded.

The binding is stored in the global system settings.  ``toggle`` acts on
each press; ``momentary`` mutes on the press and releases on the release.
A held momentary mute is released when the binding changes or the
button's device is lost.

Main thread only.
"""

import logging
from typing import Iterable, Optional

import telemffb.globals as G
from telemffb.utils import DEVICE_ROLES

BEHAVIOR_TOGGLE = "toggle"
BEHAVIOR_MOMENTARY = "momentary"
BEHAVIORS = (BEHAVIOR_TOGGLE, BEHAVIOR_MOMENTARY)

#: Global settings keys.  An empty role is the master's own device; button
#: 0 is not bound.
BUTTON_ROLE_KEY = "effectMuteButtonDevice"
BUTTON_NUMBER_KEY = "effectMuteButtonNumber"
BUTTON_BEHAVIOR_KEY = "effectMuteButtonBehavior"


def _button_number(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class MuteButtonBinding:
    """The bound (role, button, behavior) and the edge detection for it."""

    def __init__(self, controller):
        self._controller = controller
        settings = G.system_settings
        role = settings.get(BUTTON_ROLE_KEY, "") or ""
        self._role: str = role if role in DEVICE_ROLES else ""
        self._button: int = _button_number(settings.get(BUTTON_NUMBER_KEY, 0))
        behavior = settings.get(BUTTON_BEHAVIOR_KEY, BEHAVIOR_TOGGLE)
        self._behavior: str = behavior if behavior in BEHAVIORS else BEHAVIOR_TOGGLE
        self._held: bool = False
        # the scope a momentary mute was applied with, while it is held
        self._active_scope: Optional[str] = None

    @property
    def role(self) -> str:
        """The bound device's role; the controller's own role when none is stored."""
        return self._role or self._controller.own_role

    @property
    def button(self) -> int:
        return self._button

    @property
    def behavior(self) -> str:
        return self._behavior

    def set_binding(self, role: str, button: int, behavior: str) -> None:
        """Store a new binding.  Releases a held momentary mute.

        :raises ValueError: an unknown role or behavior, or a button that
            is not a whole number >= 0; nothing changes then.
        """
        if role not in DEVICE_ROLES:
            raise ValueError(f"unknown device role {role!r}")
        if behavior not in BEHAVIORS:
            raise ValueError(f"unknown mute button behavior {behavior!r}")
        if isinstance(button, bool) or not isinstance(button, int) or button < 0:
            raise ValueError(f"mute button must be a whole number >= 0, not {button!r}")
        if (role, button, behavior) == (self.role, self._button, self._behavior):
            return
        self._release()
        self._held = False
        settings = G.system_settings
        if role != self._role:
            settings.setValue(BUTTON_ROLE_KEY, role)
        if button != self._button:
            settings.setValue(BUTTON_NUMBER_KEY, button)
        if behavior != self._behavior:
            settings.setValue(BUTTON_BEHAVIOR_KEY, behavior)
        self._role, self._button, self._behavior = role, button, behavior
        if button:
            logging.info(f"Effect mute button: {role} button {button}, {behavior}")
        else:
            logging.info("Effect mute button: not bound")

    def buttons_changed(self, role: str, buttons: Iterable[int]) -> None:
        """``role``'s device now has ``buttons`` pressed."""
        if not self._button or role != self.role:
            return
        held = self._button in buttons
        if held == self._held:
            return
        self._held = held
        ctl = self._controller
        if self._behavior == BEHAVIOR_TOGGLE:
            if held:
                ctl.toggle_mute(role)
        elif held:
            self._active_scope = ctl.mute_scope()
            ctl.set_mute_active(role, True, scope=self._active_scope)
        else:
            self._release()

    def device_lost(self, role: str) -> None:
        """``role``'s device stopped reporting: its button counts as released."""
        self.buttons_changed(role, ())

    def _release(self) -> None:
        """Release a momentary mute applied by a press still held."""
        scope, self._active_scope = self._active_scope, None
        if scope is not None:
            self._controller.set_mute_active(self.role, False, scope=scope)
