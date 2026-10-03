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

"""EffectMuteButton: the mute split button for one device role.

The main area performs the selected mute function
(``controller.toggle_mute``) for the role it is bound to.  The arrow opens
a menu of two exclusive groups: what is muted (the two modes) and where
(this device or all devices).  Choosing an entry only changes the
selection, which is app-wide.  The button shows the controller's state,
refreshed on ``mute_changed`` and as the menu opens, so a click the
controller refuses leaves it as it was.
"""

import logging
from typing import Optional

from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QMenu, QToolButton

from telemffb.hw.effect_levels import MUTE_ALL, MUTE_KEEP_SPRING
from telemffb.state.effect_levels_controller import SCOPE_ALL, SCOPE_DEVICE
from telemffb.ui.widgets.effect_levels_ui import (MODE_MENU_TEXT, MUTE_BUTTON_QSS,
                                                  MUTE_SCOPE_TEXT, SCOPE_MENU_TEXT,
                                                  mode_effect_text, role_name)


def button_text(mode: str, scope: str, muted: bool) -> str:
    """The button's label for a selected function: what it mutes
    ("Haptics" or "All Effects"), as a command while off and a state while
    on, with ": All Devices" when the scope is every device."""
    what = "All Effects" if mode == MUTE_ALL else "Haptics"
    text = f"{what} Muted" if muted else f"Mute {what}"
    if scope == SCOPE_ALL:
        text += ": All Devices"
    return text


class EffectMuteButton(QToolButton):
    """Mute toggle for the role it is bound to (``set_role``)."""

    def __init__(self, controller, role: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._role = role or controller.own_role
        self.setObjectName("effectMuteButton")
        self.setCheckable(True)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.setStyleSheet(MUTE_BUTTON_QSS)

        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self.mode_actions: dict[str, QAction] = {}
        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        for mode in (MUTE_KEEP_SPRING, MUTE_ALL):
            action = QAction(MODE_MENU_TEXT[mode], self)
            action.setCheckable(True)
            action.setToolTip(f"The button mutes {mode_effect_text(mode)}.")
            action.triggered.connect(lambda _checked=False, m=mode: self._on_mode_chosen(m))
            mode_group.addAction(action)
            menu.addAction(action)
            self.mode_actions[mode] = action
        menu.addSeparator()
        self.scope_actions: dict[str, QAction] = {}
        scope_group = QActionGroup(self)
        scope_group.setExclusive(True)
        for scope, tip in ((SCOPE_DEVICE, "The button mutes the device it belongs to."),
                           (SCOPE_ALL, "The button mutes every device at once.")):
            action = QAction(SCOPE_MENU_TEXT[scope], self)
            action.setCheckable(True)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, sc=scope: self._on_scope_chosen(sc))
            scope_group.addAction(action)
            menu.addAction(action)
            self.scope_actions[scope] = action
        menu.aboutToShow.connect(self.refresh)
        self.setMenu(menu)

        # As wide as the widest label, so the row does not shift when the
        # selected function or the muted state changes.
        self.ensurePolished()
        width = 0
        for mode in (MUTE_KEEP_SPRING, MUTE_ALL):
            for scope in (SCOPE_DEVICE, SCOPE_ALL):
                self.setText(button_text(mode, scope, True))
                width = max(width, self.sizeHint().width())
        self.setFixedWidth(width)

        self.clicked.connect(self._on_clicked)
        controller.mute_changed.connect(self.refresh)
        self.refresh()

    @property
    def role(self) -> str:
        return self._role

    def set_role(self, role: Optional[str]) -> None:
        self._role = role or self._controller.own_role
        self.refresh()

    def refresh(self) -> None:
        """Show the selected function and its state: the bound role's
        mute with scope device, every device's with scope all."""
        try:
            ctl = self._controller
            mode = ctl.mute_mode()
            scope = ctl.mute_scope()
            muted = ctl.muted_all() if scope == SCOPE_ALL else ctl.muted(self._role)
        except Exception:
            logging.exception(f"Effect levels: mute state of {self._role} unavailable")
            return
        self.setChecked(muted)
        self.setText(button_text(mode, scope, muted))
        for action_mode, action in self.mode_actions.items():
            action.setChecked(action_mode == mode)
        for action_scope, action in self.scope_actions.items():
            action.setChecked(action_scope == scope)
        effects = mode_effect_text(mode)
        where = "every device" if scope == SCOPE_ALL else f"the {role_name(self._role)}"
        if muted:
            first = f"TelemFFB effects muted on {where} ({effects}). Click to release."
        else:
            first = f"Click to mute {effects} on {where}."
        self.setToolTip(f"{first}\n{MUTE_SCOPE_TEXT}\n"
                        "The arrow chooses what is muted and where.")

    def _on_clicked(self, _checked: bool = False) -> None:
        try:
            self._controller.toggle_mute(self._role)
        except Exception:
            logging.exception(f"Effect levels: could not change the mute on {self._role}")
        self.refresh()

    def _on_mode_chosen(self, mode: str) -> None:
        try:
            self._controller.set_mute_mode(mode)
        except Exception:
            logging.exception("Effect levels: could not set the mute mode")
        self.refresh()

    def _on_scope_chosen(self, scope: str) -> None:
        try:
            self._controller.set_mute_scope(scope)
        except Exception:
            logging.exception("Effect levels: could not set the mute scope")
        self.refresh()
