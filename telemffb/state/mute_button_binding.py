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
An inverted ``momentary`` binding mutes while the button is released, so
it mutes from the start (master only) until the button is first held.
Inversion does not apply to ``toggle``.  A momentary mute the binding
applied is released when the binding changes.  A lost device counts as
all buttons released.

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
BUTTON_INVERTED_KEY = "effectMuteButtonInverted"


def _button_number(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _inverted_flag(value) -> bool:
    """A stored on/off value: a bool, a number, or a string.  Anything
    unreadable reads as off."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (bool, int, float)):
        return bool(value)
    return False


class MuteButtonBinding:
    """The bound (role, button, behavior, inverted) and the edge detection
    for it.  The owner calls ``apply_initial_state`` once it can mute."""

    def __init__(self, controller):
        self._controller = controller
        settings = G.system_settings
        role = settings.get(BUTTON_ROLE_KEY, "") or ""
        self._role: str = role if role in DEVICE_ROLES else ""
        self._button: int = _button_number(settings.get(BUTTON_NUMBER_KEY, 0))
        behavior = settings.get(BUTTON_BEHAVIOR_KEY, BEHAVIOR_TOGGLE)
        self._behavior: str = behavior if behavior in BEHAVIORS else BEHAVIOR_TOGGLE
        self._inverted: bool = _inverted_flag(settings.get(BUTTON_INVERTED_KEY, False))
        self._held: bool = False
        # the scope of the momentary mute this binding applied; None while
        # it applies none
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

    @property
    def inverted(self) -> bool:
        """The stored inversion; it only acts with ``momentary``."""
        return self._inverted

    def set_binding(self, role: str, button: int, behavior: str, inverted: bool = False) -> None:
        """Store a new binding.  Releases the momentary mute the old one
        applied, then applies the new one's initial state.

        :raises ValueError: an unknown role or behavior, or a button that
            is not a whole number >= 0; nothing changes then.
        """
        if role not in DEVICE_ROLES:
            raise ValueError(f"unknown device role {role!r}")
        if behavior not in BEHAVIORS:
            raise ValueError(f"unknown mute button behavior {behavior!r}")
        if isinstance(button, bool) or not isinstance(button, int) or button < 0:
            raise ValueError(f"mute button must be a whole number >= 0, not {button!r}")
        inverted = bool(inverted)
        if (role, button, behavior, inverted) == (self.role, self._button, self._behavior,
                                                  self._inverted):
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
        if inverted != self._inverted:
            settings.setValue(BUTTON_INVERTED_KEY, inverted)
        self._role, self._button, self._behavior = role, button, behavior
        self._inverted = inverted
        if button:
            suffix = ", inverted" if self._inverts() else ""
            logging.info(f"Effect mute button: {role} button {button}, {behavior}{suffix}")
        else:
            logging.info("Effect mute button: not bound")
        self.apply_initial_state()

    def apply_initial_state(self) -> None:
        """Master: mute now when the binding is inverted momentary, since
        its button counts as released until a press arrives."""
        if self._button and self._inverts() and G.master_instance:
            self._apply(True)

    def buttons_changed(self, role: str, buttons: Iterable[int]) -> None:
        """``role``'s device now has ``buttons`` pressed."""
        if not self._button or role != self.role:
            return
        held = self._button in buttons
        pressed = held and not self._held
        self._held = held
        if self._behavior == BEHAVIOR_TOGGLE:
            if pressed:
                self._controller.toggle_mute(role)
        else:
            self._apply(held != self._inverted)

    def device_lost(self, role: str) -> None:
        """``role``'s device stopped reporting: its button counts as released."""
        self.buttons_changed(role, ())

    def _inverts(self) -> bool:
        return self._inverted and self._behavior == BEHAVIOR_MOMENTARY

    def _apply(self, active: bool) -> None:
        """Bring the momentary mute this binding applies to ``active``;
        the controller is called only on a change."""
        if active == (self._active_scope is not None):
            return
        if active:
            ctl = self._controller
            self._active_scope = ctl.mute_scope()
            ctl.set_mute_active(self.role, True, scope=self._active_scope)
        else:
            self._release()

    def _release(self) -> None:
        """Release the momentary mute this binding applied, if any."""
        scope, self._active_scope = self._active_scope, None
        if scope is not None:
            self._controller.set_mute_active(self.role, False, scope=scope)
