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

"""Wording and styling shared by the effect levels controls
(``EffectLevelsHeader``, ``EffectMuteButton``, ``EffectLevelsDialog``),
the device strip's muted mark and the live readouts' tooltips.

Every function here reads ``G.effect_levels`` when it needs the levels and
returns an empty result when there is none, so callers need no guard.
"""

import logging
from typing import Optional

import telemffb.globals as G
from telemffb.hw.effect_levels import LEVEL_NAMES, MUTE_ALL, MUTE_KEEP_SPRING
from telemffb.state.effect_levels_controller import SCOPE_ALL, SCOPE_DEVICE
from telemffb.state.mute_button_binding import BEHAVIOR_MOMENTARY, BEHAVIOR_TOGGLE
from telemffb.ui.theme.tokens import ERROR_RED
from telemffb.utils.device import device_display_name

#: The mute button's menu entries, one per mode it can apply.
MODE_MENU_TEXT = {
    MUTE_KEEP_SPRING: "Mute haptics (keep control feel)",
    MUTE_ALL: "Mute all effects",
}

#: The mute button's menu entries, one per scope.
SCOPE_MENU_TEXT = {
    SCOPE_DEVICE: "This device",
    SCOPE_ALL: "All devices",
}

#: The hardware mute button's behaviors.
BEHAVIOR_TEXT = {
    BEHAVIOR_TOGGLE: "Toggle",
    BEHAVIOR_MOMENTARY: "Momentary",
}

#: What each mode silences, for tooltips and notices.
MODE_EFFECT_TEXT = {
    MUTE_KEEP_SPRING: "the haptic effects, vibrations and constant forces (springs, dampers, inertia and friction stay)",
    MUTE_ALL: "all effects",
}

#: What the levels and the mute leave alone.
SCOPE_TEXT = ("Scales only the effects TelemFFB creates. Effects created by a sim or by "
                  "the device's own software are not changed.")

MUTE_SCOPE_TEXT = ("Only effects TelemFFB creates are muted. Effects a sim creates directly "
                   "on the device are not.")

#: Each level's label and tooltip, keyed by ``LEVEL_NAMES``.
LEVEL_TEXT = {
    "master": ("Master", "Scales every effect equally. Note: Applies after effect specific controls and will further reduce/increase the effective levels"),
    "periodic": ("Periodic", "Vibration/Rumble effects."),
    "constant": ("Constant", "Steady pushes: control weight, elevator droop, G-force and AoA."),
    "spring": ("Spring", "Centering and position-holding springs, including trim and autopilot following."),
    "damper": ("Damper", "Resistance that grows with stick speed."),
    "inertia": ("Inertia", "Resistance to changes in stick speed."),
    "friction": ("Friction", "Steady drag against stick movement."),
}

#: The dialog's pin toggles, unpinned and pinned.
PIN_TIP = "Show this slider above the settings list"
UNPIN_TIP = "Remove this slider from above the settings list"

#: The header's and the dialog's framed tool buttons: the Monitor tab's
#: Detach button look, in palette roles so it follows the theme.  The mute
#: button adds a checked state of its own (``MUTE_BUTTON_QSS``).
TOOL_BUTTON_QSS = """
QToolButton {
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 3px 9px;
    background: palette(button);
    color: palette(button-text);
}
QToolButton:hover { background: palette(midlight); }
QToolButton:pressed { background: palette(dark); color: palette(highlight); }
QToolButton:disabled { color: palette(mid); border-color: palette(mid); }
"""

#: The mute split button.  Checked (muted) is a solid red with white text
#: in both themes; the menu half keeps its own divider.
MUTE_BUTTON_QSS = TOOL_BUTTON_QSS + f"""
QToolButton {{ padding-right: 20px; }}
QToolButton::menu-button {{
    border: none;
    border-left: 1px solid palette(mid);
    width: 16px;
}}
QToolButton:checked {{
    background: {ERROR_RED};
    border-color: #8f1f1f;
    color: white;
    font-weight: bold;
}}
QToolButton:checked:hover {{ background: #d94848; }}
QToolButton:checked::menu-button {{ border-left: 1px solid rgba(255, 255, 255, 110); }}
"""


def mode_effect_text(mode: str) -> str:
    """What ``mode`` silences, for tooltips and notices."""
    return MODE_EFFECT_TEXT.get(mode, MODE_EFFECT_TEXT[MUTE_ALL])


def role_name(role: str) -> str:
    """The name a user knows ``role`` by."""
    return device_display_name(role)


def mute_badge_text(role: str) -> Optional[str]:
    """The device strip's tooltip line for a muted ``role``, or None while
    it is not muted."""
    controller = getattr(G, 'effect_levels', None)
    if controller is None or not role:
        return None
    try:
        if not controller.muted(role):
            return None
        return f"TelemFFB effects muted: {mode_effect_text(controller.mute_mode())}"
    except Exception:
        logging.exception(f"Effect levels: mute state of {role} unavailable")
        return None


def levels_tooltip_text(role: str) -> Optional[str]:
    """The device strip's tooltip line naming ``role``'s levels below 100%,
    or None while they are all 100%."""
    controller = getattr(G, 'effect_levels', None)
    if controller is None or not role:
        return None
    try:
        levels = controller.levels(role)
        lowered = [f"{LEVEL_TEXT[name][0]} {levels[name]}%"
                   for name in LEVEL_NAMES if levels.get(name, 100) < 100]
        if not lowered:
            return None
        return f"Effect Levels: {', '.join(lowered)}"
    except Exception:
        logging.exception(f"Effect levels: levels of {role} unavailable")
        return None


def readout_note(role: Optional[str]) -> str:
    """The tooltip line for a live effect strength readout of ``role``:
    empty while its levels are all 100% and it is not muted."""
    controller = getattr(G, 'effect_levels', None)
    if controller is None or not role:
        return ''
    try:
        if controller.muted(role):
            return ("Shown after TelemFFB effect levels (muted: "
                    f"{mode_effect_text(controller.mute_mode())}).")
        if any(value < 100 for value in controller.levels(role).values()):
            return "Shown after TelemFFB effect levels."
    except Exception:
        logging.exception(f"Effect levels: levels of {role} unavailable")
    return ''
