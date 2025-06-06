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

from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal, Qt, QPointF
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QDialog, QMessageBox, QComboBox, QInputDialog
import inspect

import telemffb.globals as G
from telemffb.ui.Ui_AdvancedGCurve import Ui_AdvancedGForceDialog
from telemffb.utils import get_gain_from_speed, get_gain_from_gs


class AdvancedGDialog(QDialog, Ui_AdvancedGForceDialog):

    accepted = pyqtSignal(str)

    def __init__(self, parent=None, settings=None, device="joystick"):
        super(AdvancedGDialog, self).__init__(parent)

        # Units setup
        self.current_unit = "gs"
        self.base_unit = "gs"
        self.x_scale = 10
        self.gain_pos: int = 100
        self.gain_neg: int = 100
        self.device_type = device
        self.current_settings = None
        self.init_settings = None
        self.effect_mode: str = 'constant'
        self.current_settings_dict = {}
        self.default_settings = ('{'
                                 '"curve_pos": {"x_min": 1.0, "x_max": 10, "points": [{"x": 1.5, "y": 0.0}, {"x": 10.0, "y": 100.0}], "smooth_curve_enabled": false, "current_unit": "gs"},'
                                 ' "curve_neg": {"x_min": 0, "x_max": 7, "points": [{"x": 0.5, "y": 0.0}, {"x": 7, "y": 100.0}], "smooth_curve_enabled": false, "current_unit": "gs"},'
                                 ' "enable_neg": false, '
                                 ' "gain_pos": 100,'
                                 ' "gain_neg": 100,'
                                 ' "units": "gs",'
                                 ' "scale": 10'
                                 ' "mode": constant'
                                 '}'
                                 )

        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"Advanced G-Force Effect Configuration ({self.device_type.capitalize()})")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.pb_saveclose.setToolTip('Save setting and close dialog')
        self.pb_saveclose.clicked.connect(lambda: self.save_curve_settings(close=True))

        self.pb_save.setToolTip('Save setting and keep dialog open. Will revert upon close unless saved')
        self.pb_save.clicked.connect(lambda: self.save_curve_settings(close=False))

        self.pb_cancel.setToolTip('Close dialog and revert to settings state when dialog was opened')
        self.pb_cancel.clicked.connect(self.cancel_curve_settings)

        self.pb_revert.setToolTip('Revert to settings state when dialog opened')
        self.pb_revert.clicked.connect(self.revert_curve_settings)

        self.tog_live_view.setToolTip('Show current live spring gain and airspeed on graph')
        self.tog_live_view.stateChanged.connect(self.toggle_live_view)

        self.curve_neg.negative_instance = True

        self.lab_effect_mode.setText("<b>Effect Force Mode:</b>")
        self.lab_effect_mode.setToolTip("Switch between a constant force effect or a shifting spring center point")

        if settings != "none" and settings is not None:
            self.init_settings = settings
            self.load_curve_settings(self.init_settings)
        else:
            self.init_settings = self.default_settings
            self.load_curve_settings(self.init_settings)

        if self.effect_mode == 'constant':
            self.lab_pos_gain_label.setText("Maximum Positive G Force")
            self.lab_pos_gain_label.setToolTip(
                "Sets the maximum amount of constant force generated based on the curve settings below")
            self.lab_neg_gain_label.setText("Maximum Negative G Force")
            self.lab_neg_gain_label.setToolTip(
                "Sets the maximum amount of constant force generated based on the curve settings below")
        elif self.effect_mode == 'offset':
            self.lab_pos_gain_label.setText("Maximum Positive G Offset")
            self.lab_pos_gain_label.setToolTip(
                "Sets the maximum amount that the spring offset can shift based on the curve settings below")
            self.lab_neg_gain_label.setText("Maximum Negative G Offset")
            self.lab_neg_gain_label.setToolTip(
                "Sets the maximum amount that the spring offset can shift based on the curve settings below")
        self.toggle_negative_settings()

        self.cb_enable_negative.clicked.connect(self.toggle_negative_settings)

        self.sl_pos_mastergain.valueChanged.connect(lambda: self.update_slider_labels())
        self.sl_neg_mastergain.valueChanged.connect(lambda: self.update_slider_labels())
        self.sl_pos_mastergain.setValue(self.gain_pos)
        self.sl_neg_mastergain.setValue(self.gain_neg)

        self.cb_pos_smoothcurve.stateChanged.connect(self.curve_pos.toggle_smooth_curve)
        self.cb_neg_smoothcurve.stateChanged.connect(self.curve_neg.toggle_smooth_curve)

        self.pb_pos_reset.clicked.connect(lambda: self.reset_curve('pos'))
        self.pb_neg_reset.clicked.connect(lambda: self.reset_curve('neg'))

        self.sb_pos_max.setValue(self.current_settings_dict.get('curve_pos').get('x_max'))
        self.sb_neg_max.setValue(self.current_settings_dict.get('curve_neg').get('x_max'))

        self.sb_pos_max.valueChanged.connect(lambda value: self.update_x_max(value, self.curve_pos))
        self.sb_neg_max.valueChanged.connect(lambda value: self.update_x_max(value, self.curve_neg))

        self.cb_offset.clicked.connect(self.enable_offset)
        self.cb_constant.clicked.connect(self.enable_constant)

    def enable_constant(self, state):
        if state is False:
            self.cb_offset.setChecked(True)
            self.enable_offset(True)
            return  # don't allow user to turn off checkbox.  Only enabling other option can disable the other option
        # if b_id == 1:  # Constant
        self.effect_mode = "constant"
        self.cb_offset.setChecked(False)
        self.lab_pos_gain_label.setText("Maximum Positive G Force")
        self.lab_pos_gain_label.setToolTip("Sets the maximum amount of constant force generated based on the curve settings below")
        self.lab_neg_gain_label.setText("Maximum Negative G Force")
        self.lab_neg_gain_label.setToolTip("Sets the maximum amount of constant force generated based on the curve settings below")

    def enable_offset(self, state):
        if state is False:
            self.cb_constant.setChecked(True)
            self.enable_constant(True)
            return  # don't allow user to turn off checkbox.  Only enabling other option can disable the other option
        # if b_id == 2:  # Offset
        self.effect_mode = "offset"
        self.cb_constant.setChecked(False)
        self.lab_pos_gain_label.setText("Maximum Positive G Offset")
        self.lab_pos_gain_label.setToolTip("Sets the maximum amount that the spring offset can shift based on the curve settings below")
        self.lab_neg_gain_label.setText("Maximum Negative G Offset")
        self.lab_neg_gain_label.setToolTip("Sets the maximum amount that the spring offset can shift based on the curve settings below")

    def load_default_settings(self):
        self.init_settings = self.default_settings
        self.load_curve_settings(self.init_settings)

    def update_x_max(self, value, widget):
        widget.update_x_range(new_x_max=value)

    def toggle_negative_settings(self):
        bool_val = self.cb_enable_negative.isChecked()
        self.curve_neg.setEnabled(bool_val)
        # self.pb_copy_up.setEnabled(bool_val)
        # self.pb_copy_down.setEnabled(bool_val)
        self.sl_neg_mastergain.setEnabled(bool_val)
        self.cb_neg_smoothcurve.setEnabled(bool_val)
        self.pb_neg_reset.setEnabled(bool_val)

    def showme(self, settings = None):
        if self.current_settings is not None:
            self.init_settings = self.current_settings
        if settings is not None:
            self.load_curve_settings(settings)
            self.toggle_negative_settings()
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
        if axis == "pos":
            self.cb_pos_smoothcurve.setChecked(False)
            self.curve_pos.clear_points()
            self.load_curve_settings(self.default_settings, pos=True, neg=False)
        elif axis == "neg":
            self.cb_neg_smoothcurve.setChecked(False)
            self.curve_neg.clear_points()
            self.load_curve_settings(self.default_settings, pos=False, neg=True)


    def toggle_live_view(self):
        if self.tog_live_view.isChecked():
            G.telem_manager.telemetryReceived.connect(self.draw_live_view)
        else:
            G.telem_manager.telemetryReceived.disconnect(self.draw_live_view)
            self.curve_pos.clear_crosshairs()
            self.curve_pos.msg_label.hide()
            self.curve_neg.clear_crosshairs()
            self.curve_neg.msg_label.hide()

    def draw_live_view(self, data):
        gs = data.get("ACCs", None)
        if gs is None:
            gs = data.get('G')
        else:
            gs = gs[1]
        current_gains = G.telem_manager.currentAircraft.gforce_effect_adv_curve
        if current_gains == "none":
            self.curve_pos.msg_label.setText('Please save a configuration before enabling live view')
            self.curve_neg.msg_label.setText('Please save a configuration before enabling live view')
            self.curve_pos.msg_label.show()
            self.curve_neg.msg_label.show()
            return



        if gs >= 0:
            gains = get_gain_from_gs(current_gains, gs)
            gain_pos = gains.get('pos')
            if gain_pos > 0:
                gain_pos = gain_pos * (100/self.sl_pos_mastergain.value())  # convert back to %100 reference gain so it follows curve line
            self.curve_pos.draw_crosshairs(gs, gain_pos*100)
        else:
            gains = get_gain_from_gs(current_gains, abs(gs))
            gain_neg = gains.get('neg')
            if gain_neg > 0:
                gain_neg = gain_neg * (100/self.sl_pos_mastergain.value()) # convert back to %100 reference gain so it follows curve line
            self.curve_neg.draw_crosshairs(abs(gs), gain_neg*100)

    def cancel_curve_settings(self):
        self.revert_curve_settings()
        self.save_curve_settings(close=True)

    def revert_curve_settings(self):
        self.load_curve_settings(self.init_settings)

    def save_curve_settings(self, close=True):
        """
            Save the settings of both curve widgets into a JSON-formatted string.
            """
        settings = {
            "curve_pos": self.curve_pos.to_dict(),
            "gain_pos": self.sl_pos_mastergain.value(),
            "curve_neg": self.curve_neg.to_dict(),
            "gain_neg": self.sl_neg_mastergain.value(),
            "enable_neg": self.cb_enable_negative.isChecked(),
            "units": self.current_unit,
            "scale": self.x_scale,
            "mode": self.effect_mode
        }
        json_string = json.dumps(settings)
        if hasattr(G.telem_manager.currentAircraft, 'adv_spr_settings_dict'):
            # Only update the aircraft on load if telem_manager is active.  Else will throw error when offline
            G.telem_manager.currentAircraft.adv_g_settings_dict = settings
        self.current_settings = json_string
        self.accepted.emit(json_string)
        if close:
            self.tog_live_view.setChecked(False)
            self.hide()

    def load_curve_settings(self, json_string, pos=True, neg=True):
        """
        Load settings from a JSON-formatted string and apply them to both curve widgets.
        """
        try:
            settings = json.loads(json_string)
            if "curve_pos" in settings and "curve_neg" in settings:
                if pos:
                    self.curve_pos.from_dict(settings["curve_pos"])
                    self.cb_pos_smoothcurve.setChecked(settings['curve_pos']['smooth_curve_enabled'])
                    self.gain_pos = settings.get('gain_pos', 100)
                    self.sl_pos_mastergain.setValue(settings['gain_pos'])
                if neg:
                    self.curve_neg.from_dict(settings["curve_neg"])
                    self.cb_neg_smoothcurve.setChecked(settings['curve_neg']['smooth_curve_enabled'])
                    self.gain_neg = settings.get('gain_neg', 100)
                    self.sl_neg_mastergain.setValue(settings['gain_neg'])

                    self.cb_enable_negative.setChecked(settings['enable_neg'])

                self.x_scale = settings.get('scale', 10)
                self.current_unit = settings.get('units', "g")
                self.effect_mode = settings.get('mode', 'constant')
                if self.effect_mode == 'constant':
                    self.cb_constant.setChecked(True)
                    self.cb_offset.setChecked(False)
                elif self.effect_mode == 'offset':
                    self.cb_offset.setChecked(True)
                    self.cb_constant.setChecked(False)

                # self.cb_airspeed_unit.setCurrentText(self.current_unit)
                self.update_slider_labels()
                self.current_settings_dict = settings

            else:
                raise ValueError("Invalid JSON format: Missing 'curve_pos' or 'curve_neg' keys.")
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON: {e}")
            raise ValueError("Invalid JSON string.")
        except Exception as e:
            print(f"Error loading curve settings: {e}")
            raise


    def copy_x_to_y(self):
        self.curve_neg.from_dict(self.curve_pos.to_dict())

    def copy_y_to_x(self):
        self.curve_pos.from_dict(self.curve_neg.to_dict())
        pass

    def change_airspeed_scale(self, increment):
        for axis in [self.curve_pos, self.curve_neg]:
            axis.update_airspeed_range(increment)

        self.x_scale += increment

    def change_airspeed_unit(self, new_unit):
        """Change the unit of the x-axis and update points and labels."""
        if new_unit == self.current_unit:
            return

        # Conversion factors
        current_conversion = self.UNIT_CONVERSIONS[self.current_unit]
        new_conversion = self.UNIT_CONVERSIONS[new_unit]
        conversion_factor = new_conversion / current_conversion

        for axis in [self.curve_pos, self.curve_neg]:
            # Update points and x_scale
            axis.points = [QPointF(p.x() * conversion_factor, p.y()) for p in axis.points]
            axis.x_scale *= conversion_factor
            axis.update()
            axis.current_unit = new_unit
        self.x_scale *= conversion_factor
        self.current_unit = new_unit

    def update_slider_labels(self):
        """
        Updates the slider labels
        """
        self.gain_pos = self.sl_pos_mastergain.value()
        self.gain_neg = self.sl_neg_mastergain.value()
        self.lab_pos_mastergain.setText(f"%{self.gain_pos}")
        self.lab_neg_mastergain.setText(f"%{self.gain_neg}")
