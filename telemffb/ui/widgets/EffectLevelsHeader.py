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

"""EffectLevelsHeader: the Settings tab header's effect levels controls.

An "Effect Levels" group box (``box``) holding a grid with one row per
pinned level (``controller.pinned_levels``) of the device in scope: its
label, then its slider and percent readout.  The mute split button and a
button that opens the Effect Levels dialog sit to the right of the first
row.  The role follows the config scope (``bind``); values follow the
controller's ``levels_changed``, and the rows shown follow
``pins_changed``.

The box shows while ``controller.controls_shown()``.  A note (``note``)
lists every level below 100% that has no slider showing: under the
sliders inside the box, or alone in the box's place while the box is
hidden.  Master instance only (``build_effect_levels_header``).
"""

import logging
from typing import Optional

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel, QSizePolicy,
                             QToolButton, QVBoxLayout, QWidget)

import telemffb.globals as G
from telemffb.hw.effect_levels import LEVEL_NAMES
from telemffb.ui.widgets.EffectLevelSlider import EffectLevelSlider
from telemffb.ui.widgets.EffectMuteButton import EffectMuteButton
from telemffb.ui.widgets.HiddenLevelsNote import HiddenLevelsNote
from telemffb.ui.widgets.effect_levels_ui import LEVEL_TEXT, TOOL_BUTTON_QSS, role_name

#: Each header slider, short enough to leave the bar its device strip.
_SLIDER_WIDTH = 110
#: Space between a row's label and its slider, and between the readouts
#: and the buttons.
_LABEL_SPACING = 6
_BUTTONS_SPACING = 10
#: How far the buttons reach past the first row, above and below it.  Each
#: row is twice this much shorter than the buttons, so the rows stack a
#: little tighter than the buttons are tall.
_BUTTONS_OVERHANG = 2
#: Clear space left of the box (off the tab pane's edge) and below it
#: (above the settings list); none while nothing shows.
_OUTER_MARGINS = (6, 0, 0, 4)
#: Inside the box: room for its title above the rows, and its frame.
_BOX_MARGINS = (10, 18, 10, 8)
_BOX_SPACING = 4


class EffectLevelsHeader(QWidget):
    """Pinned level sliders, mute button and Levels button for one role,
    in a group box, and the note on levels that cannot be seen."""

    #: the Levels button or the note was clicked
    open_dialog_requested = pyqtSignal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._role = controller.own_role

        # header: [ box ][ note, while the box is hidden ][ stretch ]
        # box:    [ grid: label | slider + readout, one row per pinned level ][ buttons ]
        #         [ note, while the box shows ]
        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(*_OUTER_MARGINS)
        self._outer.setSpacing(0)

        self.box = QGroupBox("Effect Levels", self)
        self.box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._box_layout = QVBoxLayout(self.box)
        self._box_layout.setContentsMargins(*_BOX_MARGINS)
        self._box_layout.setSpacing(_BOX_SPACING)
        row = self._row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_BUTTONS_SPACING)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_LABEL_SPACING)
        self._grid.setVerticalSpacing(0)
        row.addLayout(self._grid)
        row.setAlignment(self._grid, Qt.AlignmentFlag.AlignTop)
        self._box_layout.addLayout(row)

        #: per level: its label and its slider
        self.labels: dict[str, QLabel] = {}
        self.sliders: dict[str, EffectLevelSlider] = {}
        for name in LEVEL_NAMES:
            label = QLabel(LEVEL_TEXT[name][0], self.box)
            slider = EffectLevelSlider(self.box, slider_width=_SLIDER_WIDTH)
            slider.edited.connect(lambda value, persist, n=name: self._on_edited(n, value, persist))
            self.labels[name] = label
            self.sliders[name] = slider
        self.master = self.sliders["master"]

        self.mute_button = EffectMuteButton(controller, self._role)

        self.levels_button = QToolButton()
        self.levels_button.setText("Levels...")
        self.levels_button.setStyleSheet(TOOL_BUTTON_QSS)
        self.levels_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.levels_button.clicked.connect(self.open_dialog_requested.emit)

        self._buttons = QWidget(self.box)
        buttons_row = QHBoxLayout(self._buttons)
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(6)
        buttons_row.addWidget(self.mute_button)
        buttons_row.addWidget(self.levels_button)
        row.addWidget(self._buttons, alignment=Qt.AlignmentFlag.AlignTop)

        #: the levels below 100% that have no slider showing
        self.note = HiddenLevelsNote(self.box)
        self.note.clicked.connect(self.open_dialog_requested.emit)
        self._box_layout.addWidget(self.note, alignment=Qt.AlignmentFlag.AlignLeft)
        self._note_in_box = True

        self._outer.addWidget(self.box, alignment=Qt.AlignmentFlag.AlignTop)
        self._outer.addStretch(1)

        try:
            self._shown = bool(controller.controls_shown())
        except Exception:
            logging.exception("Effect levels: whether the controls show is unavailable")
            self._shown = True
        self.box.setHidden(not self._shown)

        controller.levels_changed.connect(self._on_levels_changed)
        controller.pins_changed.connect(self._on_pins_changed)
        controller.controls_shown_changed.connect(self._on_controls_shown_changed)
        self._show_pinned()
        self.refresh()

    # --- role ------------------------------------------------------------

    @property
    def role(self) -> str:
        return self._role

    def bind(self, app_state) -> None:
        """Follow the config scope: ``app_state``'s scope now and on every
        change."""
        app_state.scope_status_changed.connect(self._on_scope_status)
        self.set_role(app_state.current_status().scope)

    def set_role(self, role: Optional[str]) -> None:
        """Show and act on ``role`` (the master's own role when empty)."""
        role = role or self._controller.own_role
        if role == self._role:
            return
        self._role = role
        self.mute_button.set_role(role)
        self.refresh()

    def _on_scope_status(self, scope, *_):
        self.set_role(scope)

    # --- shown or hidden -------------------------------------------------

    def controls_shown(self) -> bool:
        """Whether the box with the sliders and buttons shows."""
        return self._shown

    def set_controls_shown(self, on: bool) -> None:
        """Show or hide the box; the note moves to suit."""
        on = bool(on)
        if on == self._shown:
            return
        self._shown = on
        self.box.setHidden(not on)
        self._refresh_note()

    def _on_controls_shown_changed(self, on) -> None:
        try:
            self.set_controls_shown(on)
        except Exception:
            logging.exception("Effect levels: controls not shown or hidden")

    # --- pinned sliders --------------------------------------------------

    def shown_levels(self) -> list[str]:
        """The level names whose sliders are showing, in row order."""
        return [name for name in LEVEL_NAMES if not self.sliders[name].isHidden()]

    def _show_pinned(self) -> None:
        try:
            pinned = set(self._controller.pinned_levels())
        except Exception:
            logging.exception("Effect levels: pinned sliders unavailable")
            pinned = {"master"}
        shown = [name for name in LEVEL_NAMES if name in pinned]
        grid = self._grid
        for name in LEVEL_NAMES:
            grid.removeWidget(self.labels[name])
            grid.removeWidget(self.sliders[name])
            self.labels[name].setHidden(name not in pinned)
            self.sliders[name].setHidden(name not in pinned)
        for row, name in enumerate(shown):
            grid.addWidget(self.labels[name], row, 0)
            grid.addWidget(self.sliders[name], row, 1)
        self._fit_rows()
        self._refresh_note()

    def _fit_rows(self) -> None:
        """Give the shown rows one height and center the first on the
        buttons.  Sizes follow the font and style, so this runs again when
        either changes."""
        shown = self.shown_levels()
        buttons = max(self.mute_button.sizeHint().height(), self.levels_button.sizeHint().height())
        content = max((max(self.labels[n].sizeHint().height(), self.sliders[n].sizeHint().height())
                       for n in shown), default=0)
        row = max(content, buttons - 2 * _BUTTONS_OVERHANG)
        offset = (buttons - row) // 2
        self._grid.setContentsMargins(0, max(0, offset), 0, 0)
        self._buttons.layout().setContentsMargins(0, max(0, -offset), 0, 0)
        for r in range(self._grid.rowCount()):
            self._grid.setRowMinimumHeight(r, row if r < len(shown) else 0)
        self._relayout()

    def _relayout(self) -> None:
        """Drop every cached size between the rows and this widget, so its
        size hint is right before the next layout pass runs."""
        for layout in (self._grid, self._row, self._box_layout, self._outer):
            layout.invalidate()
        self.box.updateGeometry()
        self.updateGeometry()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange):
            try:
                self._fit_rows()
            except Exception:
                logging.exception("Effect levels: header rows not refitted")

    def _on_pins_changed(self) -> None:
        try:
            self._show_pinned()
        except Exception:
            logging.exception("Effect levels: pinned sliders not updated")

    # --- note ------------------------------------------------------------

    def unseen_levels(self) -> list[tuple[str, int]]:
        """(name, percent) of each level of the role in scope that is below
        100% and has no slider showing, in ``LEVEL_NAMES`` order."""
        try:
            levels = self._controller.levels(self._role)
        except Exception:
            logging.exception(f"Effect levels: levels of {self._role} unavailable")
            return []
        seen = set(self.shown_levels()) if self._shown else set()
        return [(name, levels.get(name, 100)) for name in LEVEL_NAMES
                if levels.get(name, 100) < 100 and name not in seen]

    def _refresh_note(self) -> None:
        """Word the note, put it under the sliders or in the box's place,
        and drop the header's margins while nothing at all shows."""
        entries = self.unseen_levels()
        if self._shown != self._note_in_box:
            if self._shown:
                self._outer.removeWidget(self.note)
                self._box_layout.addWidget(self.note, alignment=Qt.AlignmentFlag.AlignLeft)
            else:
                self._box_layout.removeWidget(self.note)
                self._outer.insertWidget(1, self.note, alignment=Qt.AlignmentFlag.AlignVCenter)
            self._note_in_box = self._shown
        self.note.set_levels(entries, role_name(self._role), beside_sliders=self._shown)
        if self._shown or entries:
            self._outer.setContentsMargins(*_OUTER_MARGINS)
        else:
            self._outer.setContentsMargins(0, 0, 0, 0)
        self._relayout()

    # --- values ----------------------------------------------------------

    def refresh(self) -> None:
        try:
            levels = self._controller.levels(self._role)
        except Exception:
            logging.exception(f"Effect levels: levels of {self._role} unavailable")
            return
        name = role_name(self._role)
        for level, slider in self.sliders.items():
            slider.set_value(levels.get(level, 100))
            text, tip = LEVEL_TEXT[level]
            tip = f"{name}: {text} effect level. {tip}"
            self.labels[level].setToolTip(tip)
            slider.setToolTip(tip)
        lowered = [f"{n.capitalize()} {levels[n]}%" for n in LEVEL_NAMES if levels[n] < 100]
        levels_tip = f"Open the Effect Levels dialog for the {name}."
        if lowered:
            levels_tip += "\nBelow 100%: " + ", ".join(lowered)
        self.levels_button.setToolTip(levels_tip)
        self._refresh_note()

    def _on_levels_changed(self, role: str) -> None:
        if role == self._role:
            self.refresh()

    def _on_edited(self, name: str, value: int, persist: bool) -> None:
        try:
            self._controller.set_levels(self._role, {name: value}, persist=persist)
        except Exception:
            logging.exception(f"Effect levels: could not set {name} on {self._role}")


def build_effect_levels_header(controller) -> Optional[EffectLevelsHeader]:
    """The header controls for this instance: None in a child instance or
    without a controller."""
    if controller is None or not getattr(G, 'master_instance', False):
        return None
    return EffectLevelsHeader(controller)
