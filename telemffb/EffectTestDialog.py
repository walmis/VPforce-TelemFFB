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

"""Interactive effect tester dialog.

Plays a single haptic effect on the locally-bound device (Rhino on
joystick / pedals / collective / trimwheel instances; the bass-shaker
facade on a shaker child instance). Effect type, frequency, magnitude,
and direction are tunable in real time while the effect is playing.

This is a developer / tuning tool and not a persisted setting.
"""

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSlider,
    QSpinBox, QVBoxLayout,
)

from . import globals as G

# (display label, EFFECT_* constant name on aircraft_base after rebind)
_EFFECT_TYPES = (
    ("Sine",          "EFFECT_SINE"),
    ("Square",        "EFFECT_SQUARE"),
    ("Triangle",      "EFFECT_TRIANGLE"),
    ("Sawtooth Up",   "EFFECT_SAWTOOTHUP"),
    ("Sawtooth Down", "EFFECT_SAWTOOTHDOWN"),
    ("Constant",      "EFFECT_CONSTANT"),
)

# A reserved effect name. The shaker whitelist explicitly admits this name so
# the tester can drive the shaker like any other effect.
TEST_EFFECT_NAME = "__effect_tester__"


class EffectTestDialog(QDialog):
    """Tunable single-effect playback dialog. Non-modal."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Effect Tester ({G.device_type.capitalize()})")
        self.setMinimumWidth(440)

        # Imported here so we pick up the post-rebind HapticEffect / effects
        # dispenser (see aircraft_base.use_shaker_backend).
        from .sim import aircraft_base
        self._aircraft_base = aircraft_base

        self._effect = None
        self._is_playing = False
        self._timed_done_timer = None

        outer = QVBoxLayout(self)
        self._build_header(outer)

        params_box = QGroupBox("Effect parameters")
        outer.addWidget(params_box)
        form = QFormLayout(params_box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._build_effect_type(form)
        self._build_frequency(form)
        self._build_magnitude(form)
        self._build_direction(form)

        dur_box = QGroupBox("Duration")
        outer.addWidget(dur_box)
        self._build_duration(dur_box)

        self._build_buttons(outer)
        self._update_enable_states()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _build_header(self, layout):
        if G.device_type == "shaker":
            backend = "Shaker (audio)"
        else:
            backend = f"Rhino ({G.device_type})"
        connected = "connected" if G.device_connection_status else "<b>NOT connected</b>"
        layout.addWidget(QLabel(f"Backend: <b>{backend}</b> – {connected}"))
        if G.device_type == "shaker":
            layout.addWidget(QLabel(
                "<i>Shaker note: only sine output is produced; effect-type and "
                "direction parameters are stored but currently unused.</i>"))
        if not G.device_connection_status:
            layout.addWidget(QLabel(
                "<i>Device is not connected — playback will be a no-op.</i>"))

    def _build_effect_type(self, form):
        self.type_combo = QComboBox()
        for label, _ in _EFFECT_TYPES:
            self.type_combo.addItem(label)
        self.type_combo.setCurrentIndex(0)
        self.type_combo.currentIndexChanged.connect(self._on_param_changed)
        form.addRow("Effect type:", self.type_combo)

    def _make_slider_row(self, slider_min, slider_max, value, suffix, *, double=False):
        row = QHBoxLayout()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(slider_min, slider_max)
        if double:
            spin = QDoubleSpinBox()
            spin.setRange(slider_min / 100.0, slider_max / 100.0)
            spin.setSingleStep(0.01)
            spin.setDecimals(2)
            spin.setValue(value / 100.0)
            slider.setValue(value)
            slider.valueChanged.connect(lambda v: spin.setValue(v / 100.0))
            spin.valueChanged.connect(lambda v: slider.setValue(int(round(v * 100))))
        else:
            spin = QSpinBox()
            spin.setRange(slider_min, slider_max)
            spin.setValue(value)
            slider.setValue(value)
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
        if suffix:
            spin.setSuffix(suffix)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        return row, slider, spin

    def _build_frequency(self, form):
        row, self.freq_slider, self.freq_spin = self._make_slider_row(
            1, 200, 30, " Hz")
        self.freq_spin.valueChanged.connect(self._on_param_changed)
        form.addRow("Frequency:", row)

    def _build_magnitude(self, form):
        row, self.mag_slider, self.mag_spin = self._make_slider_row(
            0, 100, 50, "", double=True)
        self.mag_spin.valueChanged.connect(self._on_param_changed)
        form.addRow("Magnitude:", row)

    def _build_direction(self, form):
        row, self.dir_slider, self.dir_spin = self._make_slider_row(
            0, 359, 0, "°")
        self.dir_spin.valueChanged.connect(self._on_param_changed)
        form.addRow("Direction:", row)

    def _build_duration(self, box):
        layout = QHBoxLayout(box)
        self.dur_continuous = QRadioButton("Continuous")
        self.dur_timed = QRadioButton("Fixed:")
        self.dur_continuous.setChecked(True)
        self.dur_group = QButtonGroup(self)
        self.dur_group.addButton(self.dur_continuous)
        self.dur_group.addButton(self.dur_timed)
        self.dur_spin = QSpinBox()
        self.dur_spin.setRange(10, 60000)
        self.dur_spin.setSingleStep(50)
        self.dur_spin.setSuffix(" ms")
        self.dur_spin.setValue(500)
        self.dur_continuous.toggled.connect(self._update_enable_states)
        layout.addWidget(self.dur_continuous)
        layout.addWidget(self.dur_timed)
        layout.addWidget(self.dur_spin)
        layout.addStretch(1)

    def _build_buttons(self, layout):
        row = QHBoxLayout()
        self.live_check = QCheckBox("Live update while playing")
        self.live_check.setChecked(True)
        self.live_check.setToolTip(
            "When checked, parameter changes during playback are applied "
            "immediately. Otherwise they take effect on the next Play.")
        row.addWidget(self.live_check)
        row.addStretch(1)
        self.play_btn = QPushButton("Play")
        self.stop_btn = QPushButton("Stop")
        self.close_btn = QPushButton("Close")
        self.play_btn.clicked.connect(self._on_play)
        self.stop_btn.clicked.connect(self._on_stop)
        self.close_btn.clicked.connect(self.close)
        row.addWidget(self.play_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.close_btn)
        layout.addLayout(row)

    # ------------------------------------------------------------------
    # State / dispatch
    # ------------------------------------------------------------------

    def _update_enable_states(self):
        self.dur_spin.setEnabled(self.dur_timed.isChecked())
        constant = self._is_constant_selected()
        self.freq_slider.setEnabled(not constant)
        self.freq_spin.setEnabled(not constant)
        # Constant doesn't have a duration parameter on Rhino setConstantForce.
        if constant:
            self.dur_continuous.setChecked(True)
            self.dur_timed.setEnabled(False)
        else:
            self.dur_timed.setEnabled(True)

    def _is_constant_selected(self) -> bool:
        return _EFFECT_TYPES[self.type_combo.currentIndex()][1] == "EFFECT_CONSTANT"

    def _get_effect_type_const(self):
        const_name = _EFFECT_TYPES[self.type_combo.currentIndex()][1]
        return getattr(self._aircraft_base, const_name)

    def _on_param_changed(self, *_):
        self._update_enable_states()
        if self._is_playing and self.live_check.isChecked():
            self._apply_to_effect()

    def _ensure_effect(self):
        if self._effect is None:
            self._effect = self._aircraft_base.effects[TEST_EFFECT_NAME]
        return self._effect

    def _apply_to_effect(self):
        desired_type = self._get_effect_type_const()
        e = self._ensure_effect()
        # The underlying _h_effect is created lazily on the first call to
        # constant()/periodic() and is never re-typed. If the user switches
        # effect type while playing (e.g. Constant -> Sine, or Sine -> Square),
        # we must destroy the old effect so the next call recreates it.
        current_type = getattr(e, 'effect_type', None)
        if current_type is not None and current_type != desired_type:
            self._discard_effect()
            e = self._ensure_effect()
        magnitude = self.mag_spin.value()
        direction = float(self.dir_spin.value())
        if self._is_constant_selected():
            e.constant(magnitude, direction)
        else:
            duration = self.dur_spin.value() if self.dur_timed.isChecked() else 0
            e.periodic(self.freq_spin.value(), magnitude, direction,
                       effect_type=desired_type,
                       duration=duration)
        e.start()

    def _discard_effect(self):
        if self._effect is not None:
            try:
                self._effect.destroy()
            except Exception:
                logging.exception("Effect tester: destroy on type change failed")
        try:
            self._aircraft_base.effects.dispose(TEST_EFFECT_NAME)
        except Exception:
            logging.exception("Effect tester: dispose on type change failed")
        self._effect = None

    def _on_play(self):
        try:
            self._apply_to_effect()
            self._is_playing = True
            if self.dur_timed.isChecked() and not self._is_constant_selected():
                if self._timed_done_timer is not None:
                    self._timed_done_timer.stop()
                self._timed_done_timer = QTimer(self)
                self._timed_done_timer.setSingleShot(True)
                self._timed_done_timer.timeout.connect(self._on_timed_done)
                self._timed_done_timer.start(self.dur_spin.value() + 100)
        except Exception:
            logging.exception("Effect tester: play failed")

    def _on_timed_done(self):
        self._is_playing = False

    def _on_stop(self):
        if self._timed_done_timer is not None:
            self._timed_done_timer.stop()
        if self._effect is not None:
            try:
                self._effect.stop(destroy_after=0)
            except Exception:
                logging.exception("Effect tester: stop failed")
        self._is_playing = False

    def closeEvent(self, event):
        try:
            self._on_stop()
            if self._effect is not None:
                try:
                    self._effect.destroy()
                except Exception:
                    logging.exception("Effect tester: destroy failed")
            try:
                self._aircraft_base.effects.dispose(TEST_EFFECT_NAME)
            except Exception:
                logging.exception("Effect tester: dispose failed")
        finally:
            self._effect = None
            super().closeEvent(event)
