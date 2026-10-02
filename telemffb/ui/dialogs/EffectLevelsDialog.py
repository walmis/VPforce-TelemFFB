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

"""EffectLevelsDialog: every effect level of one device role, its mute,
the device button bound to the mute function, and which sliders are
pinned above the settings list.

Non-modal.  Changes apply as they are made: a drag applies without
storing, its release and any keyboard or wheel change store.  Closing
discards nothing, and ``show_for`` reopens it on the controller's current
values.  The device selectors list ``controller.roles()`` and show only
when there is more than one.  The pin toggles are global, whichever device
is selected.
"""

import logging
import threading
from typing import Optional

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFrame,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QRadioButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from telemffb.ButtonPressThread import wait_for_button_press
from telemffb.hw.effect_levels import LEVEL_NAMES
from telemffb.state.mute_button_binding import BEHAVIOR_MOMENTARY, BEHAVIOR_TOGGLE
from telemffb.ui.widgets.EffectLevelSlider import EffectLevelSlider
from telemffb.ui.widgets.EffectMuteButton import EffectMuteButton
from telemffb.ui.widgets.PinToggleButton import PinToggleButton
from telemffb.ui.widgets.effect_levels_ui import (BEHAVIOR_TEXT, LEVEL_TEXT, PIN_TIP,
                                                  SCOPE_TEXT, UNPIN_TIP, role_name)
from telemffb.utils import schedule_on_main_thread

_BINDING_TIP = ("A button on a device that performs the function selected on the Mute "
                "button. Toggle: each press mutes or releases. Momentary: muted while held.")

_SLIDER_WIDTH = 220
_DIALOG_MIN_WIDTH = 420
#: Highest button number: hat positions are numbered from 0x80.
_MAX_BUTTON = 255
_DETECT_TIMEOUT_S = 5.0


class EffectLevelsDialog(QDialog):
    """The seven levels of the selected role, its mute and a reset."""

    def __init__(self, controller, role: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._role = role or controller.own_role
        self.setWindowTitle("TelemFFB Effect Levels")
        self.setModal(False)
        self.setMinimumWidth(_DIALOG_MIN_WIDTH)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.scope_label = QLabel(SCOPE_TEXT)
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)

        # device selector, shown only with more than one role
        self.device_row = QWidget()
        device_layout = QHBoxLayout(self.device_row)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.addWidget(QLabel("Device:"))
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._on_device_chosen)
        device_layout.addWidget(self.device_combo)
        device_layout.addStretch()
        layout.addWidget(self.device_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.sliders: dict[str, EffectLevelSlider] = {}
        self.pin_buttons: dict[str, PinToggleButton] = {}
        row = 0
        for name in LEVEL_NAMES:
            text, tip = LEVEL_TEXT[name]
            label = QLabel(text)
            label.setToolTip(tip)
            slider = EffectLevelSlider()
            slider.slider.setMinimumWidth(_SLIDER_WIDTH)
            slider.setToolTip(tip)
            slider.edited.connect(lambda value, persist, n=name: self._on_edited(n, value, persist))
            if name == "master":
                font = QFont(label.font())
                font.setBold(True)
                label.setFont(font)
            pin = PinToggleButton(PIN_TIP, UNPIN_TIP)
            pin.clicked.connect(lambda on, n=name: self._on_pin_clicked(n, on))
            grid.addWidget(label, row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(pin, row, 2)
            self.sliders[name] = slider
            self.pin_buttons[name] = pin
            row += 1
            if name == "master":
                # Master scales everything; the type levels below it are
                # relative to one another
                line = QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet("background: palette(mid); border: none;")
                grid.addWidget(line, row, 0, 1, 3)
                row += 1
        layout.addLayout(grid)
        layout.addWidget(self._build_binding_box())

        buttons = QHBoxLayout()
        self.mute_button = EffectMuteButton(controller, self._role)
        buttons.addWidget(self.mute_button)
        self.reset_button = QPushButton("Reset to 100%")
        self.reset_button.setToolTip("Set every level of this device back to 100%.")
        self.reset_button.clicked.connect(self._reset)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.close)
        buttons.addWidget(close_box)
        layout.addLayout(buttons)

        controller.levels_changed.connect(self._on_levels_changed)
        controller.pins_changed.connect(self._refresh_pins)
        self._fill_devices()
        self.refresh()
        self._refresh_pins()

    def _build_binding_box(self) -> QGroupBox:
        """The "Mute button" group: device, button number and behavior."""
        box = QGroupBox("Mute button")
        box.setToolTip(_BINDING_TIP)
        row = QHBoxLayout(box)
        row.setSpacing(8)
        self.binding_device_combo = QComboBox()
        self.binding_device_combo.setToolTip("The device the button is on.")
        row.addWidget(self.binding_device_combo)
        row.addWidget(QLabel("Button:"))
        self.binding_button_spin = QSpinBox()
        self.binding_button_spin.setRange(0, _MAX_BUTTON)
        self.binding_button_spin.setSpecialValueText("None")
        self.binding_button_spin.setKeyboardTracking(False)
        self.binding_button_spin.setToolTip(_BINDING_TIP)
        row.addWidget(self.binding_button_spin)
        self.clear_button = QToolButton()
        self.clear_button.setText("✕")
        self.clear_button.setAutoRaise(True)
        self.clear_button.setToolTip("Clear the bound button.")
        self.clear_button.clicked.connect(lambda _checked=False: self.binding_button_spin.setValue(0))
        self.binding_button_spin.valueChanged.connect(lambda value: self.clear_button.setEnabled(value != 0))
        self.clear_button.setEnabled(False)
        row.addWidget(self.clear_button)
        self.detect_button = QPushButton("Bind")
        self.detect_button.setToolTip("Click, then press the button on the device within five seconds.")
        self.detect_button.clicked.connect(self._detect_button)
        row.addWidget(self.detect_button)
        row.addSpacing(6)
        self.behavior_buttons: dict[str, QRadioButton] = {}
        group = QButtonGroup(box)
        for behavior in (BEHAVIOR_TOGGLE, BEHAVIOR_MOMENTARY):
            radio = QRadioButton(BEHAVIOR_TEXT[behavior])
            radio.setToolTip(_BINDING_TIP)
            radio.toggled.connect(lambda on: on and self._on_binding_edited())
            group.addButton(radio)
            row.addWidget(radio)
            self.behavior_buttons[behavior] = radio
        row.addStretch()
        self.binding_device_combo.currentIndexChanged.connect(lambda _i: self._on_binding_edited())
        self.binding_button_spin.valueChanged.connect(lambda _v: self._on_binding_edited())
        return box

    # --- role ------------------------------------------------------------

    @property
    def role(self) -> str:
        return self._role

    def show_for(self, role: Optional[str]) -> None:
        """Open (or raise) the dialog on ``role`` with current values."""
        self._fill_devices(role)
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_role(self, role: Optional[str]) -> None:
        self._fill_devices(role)
        self.refresh()

    def _fill_devices(self, role: Optional[str] = None) -> None:
        """List the controller's roles and select ``role`` (or the one
        already shown, or the own role when it is not listed)."""
        try:
            roles = list(self._controller.roles())
        except Exception:
            logging.exception("Effect levels: device roles unavailable")
            roles = [self._controller.own_role]
        role = role or self._role
        if role not in roles:
            role = roles[0] if roles else self._controller.own_role
        self._role = role
        self.device_combo.blockSignals(True)
        try:
            self.device_combo.clear()
            for r in roles:
                self.device_combo.addItem(role_name(r), r)
            self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(role)))
        finally:
            self.device_combo.blockSignals(False)
        self.device_row.setVisible(len(roles) > 1)
        self.mute_button.set_role(role)
        self._refresh_binding(roles)

    def _on_device_chosen(self, index: int) -> None:
        role = self.device_combo.itemData(index)
        if role and role != self._role:
            self._role = role
            self.mute_button.set_role(role)
            self.refresh()

    # --- values ----------------------------------------------------------

    def refresh(self) -> None:
        try:
            levels = self._controller.levels(self._role)
        except Exception:
            logging.exception(f"Effect levels: levels of {self._role} unavailable")
            return
        for name, slider in self.sliders.items():
            slider.set_value(levels.get(name, 100))

    def _on_levels_changed(self, role: str) -> None:
        if role == self._role:
            self.refresh()

    def _on_edited(self, name: str, value: int, persist: bool) -> None:
        try:
            self._controller.set_levels(self._role, {name: value}, persist=persist)
        except Exception:
            logging.exception(f"Effect levels: could not set {name} on {self._role}")

    # --- pinned sliders --------------------------------------------------

    def _refresh_pins(self) -> None:
        try:
            pinned = set(self._controller.pinned_levels())
        except Exception:
            logging.exception("Effect levels: pinned sliders unavailable")
            return
        for name, pin in self.pin_buttons.items():
            pin.setChecked(name in pinned)

    def _on_pin_clicked(self, name: str, on: bool) -> None:
        try:
            self._controller.set_level_pinned(name, on)
        except Exception:
            logging.exception(f"Effect levels: could not pin the {name} slider")
        self._refresh_pins()

    # --- mute button binding ---------------------------------------------

    def _refresh_binding(self, roles: Optional[list] = None) -> None:
        """Show the stored binding; its device is listed even when it is
        not among ``roles``."""
        try:
            role, button, behavior = self._controller.mute_button_binding()
            if roles is None:
                roles = list(self._controller.roles())
        except Exception:
            logging.exception("Effect levels: mute button binding unavailable")
            return
        listed = list(roles) + ([role] if role not in roles else [])
        widgets = (self.binding_device_combo, self.binding_button_spin,
                   *self.behavior_buttons.values())
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self.binding_device_combo.clear()
            for r in listed:
                self.binding_device_combo.addItem(role_name(r), r)
            self.binding_device_combo.setCurrentIndex(
                max(0, self.binding_device_combo.findData(role)))
            self.binding_button_spin.setValue(button)
            self.clear_button.setEnabled(button != 0)
            for b, radio in self.behavior_buttons.items():
                radio.setChecked(b == behavior)
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self.binding_device_combo.setVisible(len(listed) > 1)

    def _on_binding_edited(self) -> None:
        role = self.binding_device_combo.currentData() or self._controller.own_role
        behavior = next((b for b, radio in self.behavior_buttons.items() if radio.isChecked()),
                        BEHAVIOR_TOGGLE)
        try:
            self._controller.set_mute_button_binding(role, self.binding_button_spin.value(),
                                                     behavior)
        except Exception:
            logging.exception("Effect levels: could not set the mute button")
        self._refresh_binding()

    def _detect_button(self) -> None:
        """Wait, off the main thread, for a press on the selected device
        and take its number."""
        role = self.binding_device_combo.currentData() or self._controller.own_role
        target = None if role == self._controller.own_role else role
        self.detect_button.setEnabled(False)
        self.detect_button.setText("Press a button...")

        def wait():
            try:
                value = wait_for_button_press(target, _DETECT_TIMEOUT_S)
            except Exception:
                logging.exception("Effect levels: button detection failed")
                value = 0
            schedule_on_main_thread(lambda: self._on_detected(value))

        threading.Thread(target=wait, name="MuteButtonDetect", daemon=True).start()

    def _on_detected(self, value: int) -> None:
        # runs from a queued call; the dialog may be gone by then
        try:
            self.detect_button.setEnabled(True)
            self.detect_button.setText("Bind")
            if value:
                self.binding_button_spin.setValue(value)
        except Exception:
            logging.exception("Effect levels: detected button not applied")

    def _reset(self) -> None:
        try:
            self._controller.set_levels(self._role, dict.fromkeys(LEVEL_NAMES, 100), persist=True)
        except Exception:
            logging.exception(f"Effect levels: could not reset {self._role}")
        self.refresh()
