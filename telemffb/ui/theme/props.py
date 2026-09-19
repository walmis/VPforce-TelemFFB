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
"""Dynamic-property helper for QSS-driven widget state.

A handful of widgets used to react to a state change (active/idle,
locked/unlocked, connected/disconnected) by rebuilding a whole
stylesheet string in Python and calling ``setStyleSheet()``. Setting a
Qt "dynamic property" instead and asking the style to re-polish the
widget lets QSS selectors such as ``[active="true"]`` react to the same
state change, so the color/rule lives in one shared template
(`styles.py`) rather than being re-generated per call site.
"""
from PyQt6.QtWidgets import QWidget


def set_state_prop(widget: QWidget, name: str, value) -> None:
    """Set dynamic property ``name`` on ``widget`` and re-polish it so QSS
    selectors keyed on that property re-evaluate.

    No-ops (no property write, no re-polish) when ``value`` already
    matches the current property value, so a caller that re-asserts the
    same state every frame doesn't force needless style work.
    """
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
