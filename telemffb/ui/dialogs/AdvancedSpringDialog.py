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
import json
import logging
import math

from PyQt6 import QtCore
from PyQt6.QtCore import pyqtSignal, Qt, QPointF, QLocale
from PyQt6.QtGui import QIcon, QDoubleValidator
from PyQt6.QtWidgets import QDialog, QMessageBox, QComboBox, QInputDialog, QFileDialog, QMenu, QPushButton
import inspect
from types import SimpleNamespace

import telemffb.globals as G
from telemffb import utils
from telemffb.ui.generated.Ui_AdvancedSpring import Ui_AdvancedSpringDialog
from telemffb.ui.widgets.custom_widgets import SpringCurveWidget

class AdvancedSpringDialog(QDialog, Ui_AdvancedSpringDialog):
    from telemffb.utils import conversions as conv
    UNIT_CONVERSIONS = {
        "kt": conv.ms2kt,
        "mph": conv.ms2mph,
        "kph": conv.ms2kmh,
        "m/s": 1.0,
    }
    accepted = pyqtSignal(str, int, str)

    AXES = ('x', 'y')
    # Curve input modes, in the order of the cb_axis_mode_x/_y items
    MODE_AIRSPEED = 0
    MODE_ITEM = 1
    MODE_NAMES = ("Airspeed", "Telemetry item")
    NO_SAVED_CONFIG_MSG = 'Please save a configuration before enabling live view'
    APPLY_FOR_LIVE_VIEW_MSG = 'Apply the curve settings to see live values.'
    # Source field help by sim; the row is offered on MSFS and X-Plane only
    SOURCE_HELP = {
        'MSFS': ('The telemetry item or sim variable that drives the {axis} curve. Pick a telemetry item '
                 'from the list, or type a SimVar, L: var or B: input event.'),
        'XPLANE': ('The telemetry item or dataref that drives the {axis} curve. Pick a telemetry item '
                   'from the list, or type a dataref path.'),
        None: 'The telemetry item that drives the {axis} curve.',
    }
    COPY_UP_TIP = 'Copy Y-Axis settings up to X-Axis'
    COPY_DOWN_TIP = 'Copy X-Axis settings down to Y-Axis'
    COPY_BLOCKED_TIP = 'The curves can be copied only when both use the same input and the same range.'
    # Source value readout while there is no value or no telemetry
    NO_VALUE_TEXT = '-'


    def __init__(self, parent=None, settings=None, device="joystick", sim=None):
        super(AdvancedSpringDialog, self).__init__(parent)

        # Units setup
        self.current_unit = "kt"
        self.base_unit = "m/s"
        self.x_scale = 500
        self.gain_x: int = 100
        self.gain_y: int = 100
        self.device_type = device
        self.sim = sim
        self.current_settings = None
        self.init_settings = None
        self.applied_not_saved = False
        self.default_settings = ('{'
                                 '"curve_x": {"x_min": 0, "x_max": 500, "points": [{"x": 0.0, "y": 0.0}, {"x": 500.0, "y": 100.0}], "smooth_curve_enabled": false, "current_unit": "kt"},'
                                 ' "curve_y": {"x_min": 0, "x_max": 500, "points": [{"x": 0.0, "y": 0.0}, {"x": 500.0, "y": 100.0}], "smooth_curve_enabled": false, "current_unit": "kt"},'
                                 ' "gain_x": 100,'
                                 ' "gain_y": 100,'
                                 ' "units": "kt",'
                                 ' "scale": 500}'
                                 )

        self.setupUi(self)
        # Enter in a field must only finish that field's edit: a QDialog otherwise
        # activates its first auto-default push button, which here is a curve's Reset
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.retranslateUi(self)
        self.setWindowTitle(f"Advanced Spring Configuration ({self.device_type.capitalize()})")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.pb_copy_up.setIcon(QIcon(":/image/up_arrow.png"))
        self.pb_copy_up.setText('')
        self.pb_copy_up.setMinimumWidth(25)
        self.pb_copy_up.setToolTip(self.COPY_UP_TIP)
        self.pb_copy_up.clicked.connect(lambda: self.copy_y_to_x())

        self.pb_copy_down.setIcon(QIcon(":/image/down_arrow.png"))
        self.pb_copy_down.setText('')
        self.pb_copy_down.setMinimumWidth(25)
        self.pb_copy_down.setToolTip(self.COPY_DOWN_TIP)
        self.pb_copy_down.clicked.connect(lambda: self.copy_x_to_y())

        self.cb_airspeed_unit.addItems(self.UNIT_CONVERSIONS.keys())
        self.cb_airspeed_unit.setCurrentText(self.current_unit)
        self.cb_airspeed_unit.currentTextChanged.connect(self.change_airspeed_unit)

        self.pb_airspeed_neg_ten.setIcon(QIcon(":/image/left_grey.png"))
        self.pb_airspeed_neg_ten.setText('')
        self.pb_airspeed_neg_ten.setToolTip('Minus 10')
        self.pb_airspeed_neg_ten.clicked.connect(lambda: self.change_airspeed_scale(-10))

        self.pb_airspeed_neg_hundred.setIcon(QIcon(":/image/left-left_grey.png"))
        self.pb_airspeed_neg_hundred.setText('')
        self.pb_airspeed_neg_hundred.setToolTip('Minus 100')
        self.pb_airspeed_neg_hundred.clicked.connect(lambda: self.change_airspeed_scale(-100))
        self.pb_airspeed_neg_hundred.setFixedWidth(25)

        self.pb_airspeed_pos_ten.setIcon(QIcon(":/image/right_grey.png"))
        self.pb_airspeed_pos_ten.setText('')
        self.pb_airspeed_pos_ten.setToolTip('Plus 10')
        self.pb_airspeed_pos_ten.clicked.connect(lambda: self.change_airspeed_scale(10))


        self.pb_airspeed_pos_hundred.setIcon(QIcon(":/image/right-right_grey.png"))
        self.pb_airspeed_pos_hundred.setText('')
        self.pb_airspeed_pos_hundred.setToolTip('Plus 100')
        self.pb_airspeed_pos_hundred.clicked.connect(lambda: self.change_airspeed_scale(100))

        self.pb_airspeed_manual.setToolTip("Manually enter max value for airspeed range")
        self.pb_airspeed_manual.clicked.connect(self.manual_entry_dialog)

        self.pb_saveclose.setToolTip('Save setting and close dialog')
        self.pb_saveclose.clicked.connect(lambda: self.save_curve_settings(close=True))

        self.pb_apply.setToolTip('Apply setting and keep dialog open. Will revert upon close unless saved')
        self.pb_apply.clicked.connect(lambda: self.save_curve_settings(close=False))

        self.pb_cancel.setToolTip('Close dialog and revert to settings state when dialog was opened')
        self.pb_cancel.clicked.connect(self.cancel_curve_settings)

        self.pb_revert.setToolTip('Revert to settings state when dialog opened')
        self.pb_revert.clicked.connect(self.revert_curve_settings)

        self.tog_live_view.setToolTip("Show the live spring gain and each curve's input value on the graph")
        self.tog_live_view.stateChanged.connect(self.toggle_live_view)

        self._last_frame = None
        # True from the first frame until a telemetry timeout
        self._telemetry_live = False
        self._setup_curve_inputs()

        if settings != "none" and settings is not None:
            self.init_settings = settings
            self.load_curve_settings(self.init_settings)
        else:
            self.init_settings = self.default_settings
            self.load_curve_settings(self.init_settings)

        if self.device_type == 'pedals':
            self.curve_y.setEnabled(False)
            self.pb_copy_up.setEnabled(False)
            self.pb_copy_down.setEnabled(False)
            self.sl_y_mastergain.setEnabled(False)
            self.cb_y_smoothcurve.setEnabled(False)
            self.pb_y_reset.setEnabled(False)
            self.cb_axis_mode_y.setEnabled(False)
            self.w_input_axis_y.setEnabled(False)
            self.lab_y.setText(f'<html><head/><body><p><span style="color:grey;">Y</span></p></body></html>')

        self.sl_x_mastergain.valueChanged.connect(lambda: self.update_slider_labels())
        self.sl_y_mastergain.valueChanged.connect(lambda: self.update_slider_labels())
        self.sl_x_mastergain.setValue(self.gain_x)
        self.sl_y_mastergain.setValue(self.gain_y)

        self.cb_x_smoothcurve.stateChanged.connect(self.curve_x.toggle_smooth_curve)
        self.cb_y_smoothcurve.stateChanged.connect(self.curve_y.toggle_smooth_curve)

        self.pb_x_reset.clicked.connect(lambda: self.reset_curve('x'))
        self.pb_y_reset.clicked.connect(lambda: self.reset_curve('y'))

        self.pb_apply.setEnabled(False)
        self.pb_saveclose.setEnabled(False)

        self.sl_x_mastergain.valueChanged.connect(lambda: (self.update_slider_labels(), self.check_dirty_state()))
        self.sl_y_mastergain.valueChanged.connect(lambda: (self.update_slider_labels(), self.check_dirty_state()))

        self.cb_x_smoothcurve.stateChanged.connect(lambda _: self.check_dirty_state())
        self.cb_y_smoothcurve.stateChanged.connect(lambda _: self.check_dirty_state())

        self.cb_airspeed_unit.currentTextChanged.connect(lambda _: self.check_dirty_state())
        self.pb_copy_up.clicked.connect(self.check_dirty_state)
        self.pb_copy_down.clicked.connect(self.check_dirty_state)

        self.curve_x.modified.connect(self.check_dirty_state)
        self.curve_y.modified.connect(self.check_dirty_state)

        self.pb_export.clicked.connect(self.export_settings)
        self.pb_import.clicked.connect(self.import_settings)
        if self.sim not in ("MSFS", "XPLANE"):
            self.pb_get_vne.hide()
        # self.pb_get_vne.clicked.connect(self.import_airspeed)
        self.pb_get_vne.setEnabled(False)

        G.telem_manager.telemetryReceived.connect(self._on_telemetry)
        G.telem_manager.telemetryTimeout.connect(self._on_telemetry_timeout)
        self.pb_get_vne.setMenu(self.create_option_menu())

        self.pb_get_vne.clicked.connect(self.pb_get_vne.showMenu)

    def _setup_curve_inputs(self):
        """Wire each curve's input mode switch and telemetry item widgets."""
        self._bars = {
            axis: SimpleNamespace(
                axis=axis,
                name=axis.upper(),
                curve=getattr(self, f'curve_{axis}'),
                mode=getattr(self, f'cb_axis_mode_{axis}'),
                inputs=getattr(self, f'w_input_axis_{axis}'),
                source=getattr(self, f'cb_input_source_{axis}'),
                le_min=getattr(self, f'le_input_min_{axis}'),
                le_max=getattr(self, f'le_input_max_{axis}'),
                read=getattr(self, f'tb_read_input_{axis}'),
                value=getattr(self, f'lab_input_value_{axis}'),
                # True while the curve shows APPLY_FOR_LIVE_VIEW_MSG
                apply_notice=False,
            )
            for axis in self.AXES
        }
        # C locale: the fields are parsed with float(), which takes no group or comma separators
        validator = QDoubleValidator(self)
        validator.setLocale(QLocale.c())
        for bar in self._bars.values():
            axis = bar.axis
            bar.mode.addItems(self.MODE_NAMES)
            bar.mode.setToolTip(f'Choose what drives the {bar.name} curve: the airspeed, '
                                f'or a telemetry item or sim variable.')
            bar.mode.currentIndexChanged.connect(lambda index, axis=axis: self.change_axis_mode(axis, index))

            bar.source.popupAboutToShow.connect(lambda axis=axis: self.refresh_input_sources(axis))
            bar.source.currentTextChanged.connect(lambda _text, axis=axis: self._input_source_changed(axis))

            bar.le_min.setToolTip(f"The source value at the left edge of the {bar.name} curve, in the source's "
                                  f"own units. A Min above Max reverses the axis.")
            bar.le_max.setToolTip(f"The source value at the right edge of the {bar.name} curve, in the source's "
                                  f"own units.")
            for edit in (bar.le_min, bar.le_max):
                edit.setValidator(validator)
                edit.editingFinished.connect(lambda axis=axis: self.apply_input_range(axis))

            bar.read.setMenu(self.create_read_input_menu(axis))
            bar.read.clicked.connect(bar.read.showMenu)
            bar.read.setEnabled(False)
            bar.read.setToolTip("Load into the aircraft to read the source's current value.")

            # Hidden fields keep their space, so the input row and the chart below it hold still on a mode change
            policy = bar.inputs.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            bar.inputs.setSizePolicy(policy)
            self._set_input_value(bar, self.NO_VALUE_TEXT)
        self._apply_source_rules()

        self._update_axis_mode_ui()

    def create_read_input_menu(self, axis):
        menu = QMenu(self)
        set_min_action = menu.addAction("Set as Min")
        set_max_action = menu.addAction("Set as Max")
        # triggered passes a checked flag first
        set_min_action.triggered.connect(lambda _checked=False: self.read_input_value(axis, 'min'))
        set_max_action.triggered.connect(lambda _checked=False: self.read_input_value(axis, 'max'))
        return menu

    @staticmethod
    def _other(axis):
        return 'y' if axis == 'x' else 'x'

    def is_item_mode(self, axis) -> bool:
        return self._bars[axis].mode.currentIndex() == self.MODE_ITEM

    def input_source(self, axis) -> str:
        return self._bars[axis].source.currentText().strip()

    def _airspeed_curves(self):
        return [bar.curve for bar in self._bars.values() if not self.is_item_mode(bar.axis)]

    @staticmethod
    def _number_text(value) -> str:
        """Field text for *value*: no trailing ``.0`` on whole numbers."""
        value = float(value)
        return str(int(value)) if value.is_integer() else repr(value)

    def _entered_range(self, axis):
        """The (min, max) typed in the curve's fields, or None unless both are numbers that differ.

        Min is the left end of the axis and Max the right end; Min above Max reverses the axis.
        """
        bar = self._bars[axis]
        try:
            digits = SpringCurveWidget.RAW_DIGITS
            x_min = round(float(bar.le_min.text()), digits)
            x_max = round(float(bar.le_max.text()), digits)
        except ValueError:
            return None
        return (x_min, x_max) if x_min != x_max else None

    def _input_error(self, axis):
        """The message refusing the curve's telemetry item settings, or None when they are valid."""
        bar = self._bars[axis]
        if not self.input_source(axis):
            return f"Enter a telemetry item or sim variable as the source of the {bar.name} curve."
        try:
            float(bar.le_min.text())
            float(bar.le_max.text())
        except ValueError:
            return f"The {bar.name} curve's Min and Max must be numbers."
        if self._entered_range(axis) is None:
            return f"The {bar.name} curve's Min and Max must be different values."
        return None

    def change_axis_mode(self, axis, index):
        """Switch a curve between airspeed and a telemetry item; its points keep their places in the range."""
        bar = self._bars[axis]
        curve = bar.curve
        if index == self.MODE_ITEM:
            for edit, value in ((bar.le_min, curve.x_min), (bar.le_max, curve.x_max)):
                if not edit.text().strip():
                    edit.setText(self._number_text(value))
            curve.current_unit = SpringCurveWidget.RAW_UNIT
            entered = self._entered_range(axis)
            if entered is not None:
                curve.set_input_range(*entered)
            self.refresh_input_sources(axis)
        else:
            # Back on the airspeed axis the other airspeed curve sets the range
            other = self._other(axis)
            if self.is_item_mode(other):
                x_min, x_max = 0, self.x_scale
            else:
                x_min, x_max = self._bars[other].curve.x_min, self._bars[other].curve.x_max
            curve.current_unit = self.current_unit
            curve.set_input_range(x_min, x_max)
        self._update_axis_mode_ui()
        self.check_dirty_state()

    def _update_axis_mode_ui(self):
        any_airspeed = False
        for bar in self._bars.values():
            item = self.is_item_mode(bar.axis)
            any_airspeed = any_airspeed or not item
            bar.inputs.setVisible(item)
            bar.source.setToolTip(self.input_source(bar.axis) or bar.source_help)
            legend = (self.input_source(bar.axis) or self.MODE_NAMES[self.MODE_ITEM]) if item else None
            bar.curve.set_input_legend(legend)
        self.w_airspeed_axis.setVisible(any_airspeed)
        self._update_copy_buttons()

    def _curves_compatible(self) -> bool:
        """True when both curves use the same input mode over the same range."""
        if self.is_item_mode('x') != self.is_item_mode('y'):
            return False
        digits = SpringCurveWidget.RAW_DIGITS
        return all(round(getattr(self.curve_x, end), digits) == round(getattr(self.curve_y, end), digits)
                   for end in ('x_min', 'x_max'))

    def _update_copy_buttons(self):
        # The Y curve is unused on pedals, which keep both buttons disabled
        if self.device_type == 'pedals':
            return
        compatible = self._curves_compatible()
        self.pb_copy_up.setEnabled(compatible)
        self.pb_copy_down.setEnabled(compatible)
        if compatible:
            self.pb_copy_up.setToolTip(self.COPY_UP_TIP)
            self.pb_copy_down.setToolTip(self.COPY_DOWN_TIP)
        else:
            self.pb_copy_up.setToolTip(self.COPY_BLOCKED_TIP)
            self.pb_copy_down.setToolTip(self.COPY_BLOCKED_TIP)

    def _input_source_changed(self, axis):
        self._set_input_value(self._bars[axis], self.NO_VALUE_TEXT)
        self._update_axis_mode_ui()
        self.check_dirty_state()

    def apply_input_range(self, axis):
        """Apply the curve's Min and Max fields, keeping its points' places in the range."""
        entered = self._entered_range(axis)
        if entered is None or not self.is_item_mode(axis):
            return
        self._bars[axis].curve.set_input_range(*entered)
        self._update_copy_buttons()
        self.check_dirty_state()

    def _apply_source_rules(self):
        """The Curve Input row is offered on MSFS and X-Plane only: the other sims
        subscribe no variable at runtime, and nothing their frames carry drives a
        spring gradient better than airspeed.  A loaded item curve still displays."""
        offered = self.sim in ('MSFS', 'XPLANE')
        help_text = self.SOURCE_HELP[self.sim if offered else None]
        for bar in self._bars.values():
            bar.source_help = help_text.format(axis=bar.name)
            if not bar.source.currentText().strip():
                bar.source.setToolTip(bar.source_help)
            getattr(self, f'w_curve_input_{bar.axis}').setVisible(offered)

    def refresh_input_sources(self, axis):
        """List the telemetry keys of the latest frame, keeping the typed source."""
        frame = self._last_frame
        if not frame:
            return
        keys = sorted((str(key) for key in frame.keys()), key=str.lower)
        combo = self._bars[axis].source
        if keys == [combo.itemText(i) for i in range(combo.count())]:
            return
        text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(keys)
        combo.setEditText(text)
        combo.blockSignals(False)

    def current_input_value(self, axis, frame=None):
        """The curve's source value in *frame* (default: the latest frame) as a float, or None.

        A telemetry item is read under its own name. A sim variable is read under
        the key the loaded aircraft subscribes it to, which carries this source
        only when it is the curve's saved source.
        """
        if frame is None:
            frame = self._last_frame
        source = self.input_source(axis)
        if frame is None or not source:
            return None
        key = source
        if source not in frame:
            aircraft = G.telem_manager.currentAircraft
            key = None
            if aircraft is not None and aircraft.adv_spr_input_source(axis) == source:
                key = aircraft.adv_spr_input_key(axis)
        try:
            return float(frame.get(key)) if key else None
        except (TypeError, ValueError):
            return None

    def read_input_value(self, axis, end):
        value = self.current_input_value(axis)
        if value is None:
            QMessageBox.critical(self, "Error",
                                 "No current value is available for this source. For a sim variable, "
                                 "apply the settings so TelemFFB subscribes to it, then read the value again.")
            return
        bar = self._bars[axis]
        edit = bar.le_min if end == 'min' else bar.le_max
        edit.setText(self._number_text(round(value, SpringCurveWidget.RAW_DIGITS)))
        self.apply_input_range(axis)

    @staticmethod
    def _value_text(value) -> str:
        """Readout text for *value*: up to four decimals, trailing zeros trimmed."""
        if value is None or not math.isfinite(value):
            return AdvancedSpringDialog.NO_VALUE_TEXT
        text = f"{value:.4f}".rstrip('0').rstrip('.')
        return '0' if text == '-0' else text

    @staticmethod
    def _set_input_value(bar, text: str) -> None:
        bar.value.setText(f"Value: {text}")

    def _update_input_values(self, frame):
        for bar in self._bars.values():
            if self.is_item_mode(bar.axis):
                self._set_input_value(bar, self._value_text(self.current_input_value(bar.axis, frame)))

    def _on_telemetry(self, data):
        self._last_frame = data
        if not self._telemetry_live:
            self._telemetry_live = True
            self.toggle_vne_import_button(True)
            self.toggle_read_input_button(True)
        try:
            self._update_input_values(data)
        except Exception:
            # A Qt slot must not raise; the readout is lost for this frame only
            logging.debug("[SpringDialog] Source value readout update failed", exc_info=True)

    def _on_telemetry_timeout(self, timed_out=True):
        # Resumed telemetry re-enables the buttons with its first frame
        if not timed_out:
            return
        self._telemetry_live = False
        self.toggle_vne_import_button(False)
        self.toggle_read_input_button(False)
        for bar in self._bars.values():
            self._set_input_value(bar, self.NO_VALUE_TEXT)

    def toggle_read_input_button(self, enabled):
        for bar in self._bars.values():
            if enabled and not bar.read.isEnabled():
                bar.read.setEnabled(True)
                bar.read.setToolTip("Read the source's current value into Min or Max.")
            elif not enabled and bar.read.isEnabled():
                bar.read.setEnabled(False)
                bar.read.setToolTip("Load into the aircraft to read the source's current value.")

    def _settings_dict(self) -> dict:
        """The ``adv_spr_gains`` settings the dialog currently shows."""
        settings = {
            "curve_x": self.curve_x.to_dict(),
            "gain_x": self.sl_x_mastergain.value(),
            "curve_y": self.curve_y.to_dict(),
            "gain_y": self.sl_y_mastergain.value(),
            "units": self.current_unit,
            "scale": round(self.x_scale)
        }
        for axis in self.AXES:
            if self.is_item_mode(axis):
                curve = settings[f"curve_{axis}"]
                curve["input"] = {
                    "source": self.input_source(axis),
                    "min": curve["x_min"],
                    "max": curve["x_max"],
                }
        return settings

    @classmethod
    def _canonical_json(cls, settings) -> str:
        """*settings* as JSON that compares equal whatever the key order or int/float spelling."""
        def canonical(value):
            if isinstance(value, dict):
                return {key: canonical(item) for key, item in value.items()}
            if isinstance(value, list):
                return [canonical(item) for item in value]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return value
        return json.dumps(canonical(settings), sort_keys=True)

    def create_option_menu(self):
        menu = QMenu(self)

        get_telem_action = menu.addAction("Get from Telemetry")
        override_action = menu.addAction("Use Override Value")

        get_telem_action.triggered.connect(lambda: self.import_airspeed(mode='telem'))
        override_action.triggered.connect(lambda: self.import_airspeed(mode='override'))

        return menu

    def toggle_vne_import_button(self, enabled):
        if enabled and not self.pb_get_vne.isEnabled():
            self.pb_get_vne.setEnabled(True)
            self.pb_get_vne.setToolTip("Import VNE value from active telemetry")
        elif not enabled and self.pb_get_vne.isEnabled():
            self.pb_get_vne.setEnabled(False)
            self.pb_get_vne.setToolTip("Load into aircraft to enable VNE import")

    def export_settings(self):
        settings = {
            "settings_type": "advanced_spring",
            "device_type": self.device_type,
            **self._settings_dict(),
        }

        json_string = json.dumps(settings, indent=4)
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Settings",
            filter="Settings Files (*.txt *.json);;All Files (*)"
        )
        if filename:
            try:
                with open(filename, 'w') as f:
                    f.write(json_string)
                QMessageBox.information(self, "Export", "Settings exported successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export settings:\n{e}")

    def import_settings(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import Settings",
            filter="Settings Files (*.txt *.json);;All Files (*)"
        )
        if filename:
            try:
                with open(filename, 'r') as f:
                    json_string = f.read()
                settings = json.loads(json_string)
                if settings.get('settings_type', None) != 'advanced_spring':
                    QMessageBox.critical(self, "Import Error", "The file is malformed or not an advanced spring curve settings file.")
                    return
                # Validate schema
                required_top_keys = {
                    "curve_x", "curve_y", "gain_x", "gain_y",
                    "units", "scale"
                }
                required_curve_keys = {"x_min", "x_max", "points", "smooth_curve_enabled", "current_unit"}

                missing_keys = required_top_keys - settings.keys()
                if missing_keys:
                    raise ValueError(f"Malformed settings - Missing keys: {', '.join(missing_keys)}")

                for curve in ["curve_x", "curve_y"]:
                    curve_keys = settings[curve].keys()
                    missing_curve_keys = required_curve_keys - curve_keys
                    if missing_curve_keys:
                        raise ValueError(f"Malformed settings - Missing keys in '{curve}': {', '.join(missing_curve_keys)}")

                # If validation passes:
                self.original_settings = json.dumps(settings, sort_keys=True)
                self.load_curve_settings(self.original_settings)
                self.init_settings = self.original_settings
                self.check_dirty_state()
                QMessageBox.information(self, "Import", "Settings imported successfully.")

            except json.JSONDecodeError:
                QMessageBox.critical(self, "Import Error", "The file is not valid JSON.")
            except ValueError as ve:
                QMessageBox.critical(self, "Import Error", f"Invalid settings file:\n{ve}")
            except Exception as e:
                QMessageBox.critical(self, "Import Error", f"Unexpected error:\n{e}")


    def set_dirty_state(self, dirty: bool):
        self.pb_apply.setEnabled(dirty)
        self.pb_saveclose.setEnabled(dirty)

    def check_dirty_state(self, apply=True):
        try:
            current_json = self._canonical_json(self._settings_dict())
            baseline_json = self._canonical_json(json.loads(self.init_settings))
            result = (current_json != baseline_json)
            if apply:
                self.set_dirty_state(result)
            else :
                return result

        except Exception as e:
            logging.error(f"[SpringDialog] Dirty check error: {e}")
            self.set_dirty_state(True)

    def showme(self, settings = None, sim=None):
        if self.current_settings is not None:
            self.init_settings = self.current_settings
        if settings is not None:
            self.load_curve_settings(settings)
        self.check_dirty_state()
        if sim in ('MSFS', 'XPLANE'):
            self.pb_get_vne.show()
        else:
            self.pb_get_vne.hide()
        self.sim = sim
        self._apply_source_rules()
        self.applied_not_saved = False
        self.show()

    def showEvent(self, event):
        super().showEvent(event)

    def closeEvent(self, event):
        ## When dialog is opened on child instance via event triggered by IPC message from master instance
        ## The close event on the dialog will cause the entire instance to exit
        ## This remaps the close event to execute the cancel command which reverts the setting and hides the window
        self.cancel_curve_settings()
        event.ignore()

    def reset_curve(self, axis):
        if axis == "x":
            self.curve_x.clear_points()
            self.cb_x_smoothcurve.setChecked(False)
        elif axis == "y":
            self.curve_y.clear_points()
            self.cb_y_smoothcurve.setChecked(False)

    def toggle_live_view(self):
        if self.tog_live_view.isChecked():
            G.telem_manager.telemetryReceived.connect(self.draw_live_view)
        else:
            G.telem_manager.telemetryReceived.disconnect(self.draw_live_view)
            for bar in self._bars.values():
                bar.curve.clear_crosshairs()
                bar.curve.msg_label.hide()
                bar.apply_notice = False

    def draw_live_view(self, data):
        try:
            self._draw_live_view(data)
        except Exception:
            # A Qt slot must not raise; the readout is lost for this frame only
            logging.debug("[SpringDialog] Live view update failed", exc_info=True)

    def _draw_live_view(self, data):
        aircraft = G.telem_manager.currentAircraft
        if aircraft is None:
            return
        ias = data.get('IAS', 0)
        # Runs on the main thread: the storing adv_spr_curve_gains belongs to the telemetry thread
        gains = aircraft.adv_spr_curve_gains_now(data, ias) if aircraft.adv_spr_gains is not None else None
        for axis in self.AXES:
            bar = self._bars[axis]
            curve = bar.curve
            item = self.is_item_mode(axis)
            # The applied gain matches the plot only when the saved curve is driven by the input the dialog shows
            saved_item = aircraft.adv_spr_input_source(axis) is not None
            if gains is None:
                curve.show_message(self.NO_SAVED_CONFIG_MSG, clears_on_live_data=True)
                bar.apply_notice = False
                continue
            if saved_item != item:
                curve.show_message(self.APPLY_FOR_LIVE_VIEW_MSG)
                bar.apply_notice = True
                continue
            if bar.apply_notice:
                curve.msg_label.hide()
                bar.apply_notice = False
            x_value = self.current_input_value(axis, data) if item else ias
            if x_value is not None:
                curve.draw_crosshairs(x_value, gains.get(axis, 0) * 100)

    def cancel_curve_settings(self):
        if self.applied_not_saved:
            self.load_curve_settings(self.init_settings)
            self.save_curve_settings(close=True)
        else:
            self.load_curve_settings(self.init_settings)
            self.tog_live_view.setChecked(False)
            self.hide()


    def revert_curve_settings(self):
        self.load_curve_settings(self.init_settings)
        self.save_curve_settings(close=False)
        self.check_dirty_state()

    def save_curve_settings(self, close=True):
        """
            Save the settings of both curve widgets into a JSON-formatted string.
            """
        item_axes = [axis for axis in self.AXES if self.is_item_mode(axis)]
        for axis in item_axes:
            error = self._input_error(axis)
            if error:
                QMessageBox.warning(self, "Invalid Input", error)
                return
        for axis in item_axes:
            self.apply_input_range(axis)
        settings = self._settings_dict()
        json_string = json.dumps(settings)
        self.current_settings = json_string
        self.accepted.emit(json_string, round(self.x_scale), settings["units"])
        if close:
            self.tog_live_view.setChecked(False)
            self.init_settings = json_string  # Update baseline
            self.applied_not_saved = False
            self.hide()
        else:
            self.applied_not_saved = True
            self.pb_apply.setEnabled(False)

    def load_curve_settings(self, json_string):
        """
        Load settings from a JSON-formatted string and apply them to both curve widgets.
        """
        if json_string == 'none':
            json_string = self.default_settings
        try:
            settings = json.loads(json_string)

        except json.JSONDecodeError as e:
            logging.error(f"Error decoding JSON: {e}")
            raise ValueError("Invalid JSON string.")
        except Exception as e:
            logging.error(f"Error loading curve settings: {e}")
            raise
        if hasattr(G.telem_manager.currentAircraft, 'adv_spr_settings_dict'):
            # Only update the aircraft on load if telem_manager is active.  Else will throw error when offline
            G.telem_manager.currentAircraft.adv_spr_settings_dict = settings
        if "curve_x" in settings and "curve_y" in settings:
            self.curve_x.from_dict(settings["curve_x"])
            self.cb_x_smoothcurve.setChecked(settings['curve_x']['smooth_curve_enabled'])
            self.gain_x = settings.get('gain_x', 100)
            self.sl_x_mastergain.setValue(settings['gain_x'])

            self.curve_y.from_dict(settings["curve_y"])
            self.cb_y_smoothcurve.setChecked(settings['curve_y']['smooth_curve_enabled'])
            self.gain_y = settings.get('gain_y', 100)
            self.sl_y_mastergain.setValue(settings['gain_y'])

            self.x_scale = settings.get('scale', 500)
            # "units" is the airspeed curves' unit; a curve with an input block is in its source's units
            units = settings.get('units', "kt")
            if units in self.UNIT_CONVERSIONS:
                self.current_unit = units
                self.cb_airspeed_unit.setCurrentText(self.current_unit)
            for axis in self.AXES:
                bar = self._bars[axis]
                loaded = self._loaded_input(settings[f"curve_{axis}"], axis)
                item = loaded is not None
                # An airspeed curve clears the fields; switching it to item mode fills Min and Max from its range
                source, x_min, x_max = loaded if item else ("", None, None)
                bar.source.blockSignals(True)
                bar.source.setEditText(source)
                bar.source.blockSignals(False)
                bar.le_min.setText(self._number_text(x_min) if item else "")
                bar.le_max.setText(self._number_text(x_max) if item else "")
                if not item and bar.curve.is_raw():
                    bar.curve.current_unit = self.current_unit
                bar.mode.blockSignals(True)
                bar.mode.setCurrentIndex(self.MODE_ITEM if item else self.MODE_AIRSPEED)
                bar.mode.blockSignals(False)
            self._update_axis_mode_ui()
            self.update_slider_labels()

        else:
            raise ValueError("Invalid JSON format: Missing 'curve_x' or 'curve_y' keys.")

    @staticmethod
    def _loaded_input(curve, axis):
        """A saved curve's ``input`` block as ``(source, min, max)``; None for an
        airspeed curve.  A malformed block loads as an airspeed curve."""
        block = curve.get("input")
        if block is None:
            return None
        if not isinstance(block, dict):
            logging.warning(f"[SpringDialog] The {axis.upper()} curve's input block is not an object; "
                            f"the curve loads as an airspeed curve")
            return None
        try:
            x_min = float(block.get("min", curve.get("x_min", 0)))
            x_max = float(block.get("max", curve.get("x_max", 0)))
        except (TypeError, ValueError):
            logging.warning(f"[SpringDialog] The {axis.upper()} curve's input Min or Max is not a number; "
                            f"the curve loads as an airspeed curve")
            return None
        return str(block.get("source") or ""), x_min, x_max

    def manual_entry_dialog(self):
        """
        Show a simple popup dialog to manually enter the airspeed.
        """
        # Prompt the user to enter a new airspeed value
        text, ok = QInputDialog.getText(
            self,
            "Manual Airspeed Entry",
            "Enter the new airspeed:"
        )

        if ok and text:
            try:
                # Convert the input to a float
                new_airspeed = float(text)
                if new_airspeed <= 0:
                    raise ValueError("Airspeed must be a positive value.")


                self.set_airspeed_scale(new_airspeed)
                self.x_scale = new_airspeed
                self.check_dirty_state()

            except ValueError as e:
                # Show an error message if input is invalid
                QMessageBox.warning(self, "Invalid Input", str(e))

    def copy_x_to_y(self):
        self.curve_y.from_dict(self.curve_x.to_dict())
        self.check_dirty_state()

    def copy_y_to_x(self):
        self.curve_x.from_dict(self.curve_y.to_dict())
        self.check_dirty_state()

    def change_airspeed_scale(self, increment):
        curves = self._airspeed_curves()
        for axis in curves:
            axis.update_airspeed_range(increment)
        # The widgets clamp the range at 100; x_scale follows them, never below
        self.x_scale = curves[0].x_max if curves else max(100, self.x_scale + increment)
        self.check_dirty_state()

    def set_airspeed_scale(self, new_scale: float):
        for axis in self._airspeed_curves():
            axis.set_airspeed_range(new_scale)
        self.x_scale = new_scale
        self.check_dirty_state()

    def import_airspeed(self, mode="telem"):
        if G.telem_manager.currentAircraft is None:
            QMessageBox.critical(self, "Error", f"Please ensure you are loaded into the aircraft before importing the VNE value")
            return
        if mode == 'telem':
            vne_airspeed = G.telem_manager.currentAircraft.telem_data.get('Vne_ms_calc', None)
        elif mode == 'override':
            vne_airspeed = G.telem_manager.currentAircraft.vne_override # get override vne from telemetry (in m/s)
            if not vne_airspeed:
                QMessageBox.critical(self, "Error", f"There does not appear to be a stored vne override for this aircraft")
                return
        else:
            QMessageBox.critical(self, "Error", f"There was an error importing the VNE value")
            return

        if vne_airspeed is None:
            QMessageBox.critical(self, "Error", f"Please ensure you are loaded into the aircraft before importing the VNE value")
            return

        telem_airspeed = vne_airspeed * self.UNIT_CONVERSIONS[self.current_unit]
        self.set_airspeed_scale(telem_airspeed)
        self.check_dirty_state()


    def change_airspeed_unit(self, new_unit):
        """Change the unit of the x-axis and update points and labels."""
        if new_unit == self.current_unit:
            return

        # Conversion factors
        current_conversion = self.UNIT_CONVERSIONS[self.current_unit]
        new_conversion = self.UNIT_CONVERSIONS[new_unit]
        conversion_factor = new_conversion / current_conversion

        for axis in self._airspeed_curves():
            # Update points and x_scale
            axis.points = [QPointF(p.x() * conversion_factor, p.y()) for p in axis.points]
            axis.x_min *= conversion_factor
            axis.x_max *= conversion_factor
            axis.update()
            axis.current_unit = new_unit
        self.x_scale *= conversion_factor
        self.current_unit = new_unit
        self.check_dirty_state()


    def update_slider_labels(self):
        """
        Updates the slider labels
        """
        self.gain_x = self.sl_x_mastergain.value()
        self.gain_y = self.sl_y_mastergain.value()
        self.lab_x_mastergain.setText(f"%{self.gain_x}")
        self.lab_y_mastergain.setText(f"%{self.gain_y}")
        self.lab_x.adjustSize()
        self.lab_y.adjustSize()




