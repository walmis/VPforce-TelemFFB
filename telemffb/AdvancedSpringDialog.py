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
from telemffb.ui.Ui_AdvancedSpring import Ui_AdvancedSpringDialog
from telemffb.utils import get_gain_from_speed
from telemffb.custom_widgets import SpringCurveWidget

class AdvancedSpringDialog(QDialog, Ui_AdvancedSpringDialog):
    UNIT_CONVERSIONS = {
        "kt": 1.94384,
        "mph": 2.23694,
        "kph": 3.6,
        "m/s": 1.0,
    }
    accepted = pyqtSignal(str)


    def __init__(self, parent=None, settings=None, device="joystick"):
        super(AdvancedSpringDialog, self).__init__(parent)

        # Units setup
        self.current_unit = "kt"
        self.base_unit = "m/s"
        self.x_scale = 500
        self.gain_x: int = 100
        self.gain_y: int = 100
        self.device_type = device
        self.current_settings = None
        self.init_settings = None
        self.default_settings = ('{'
                                 '"curve_x": {"x_min": 0, "x_max": 500, "points": [{"x": 0.0, "y": 0.0}, {"x": 500.0, "y": 100.0}], "smooth_curve_enabled": false, "current_unit": "kt"},'
                                 ' "curve_y": {"x_min": 0, "x_max": 500, "points": [{"x": 0.0, "y": 0.0}, {"x": 500.0, "y": 100.0}], "smooth_curve_enabled": false, "current_unit": "kt"},'
                                 ' "gain_x": 100,'
                                 ' "gain_y": 100,'
                                 ' "units": "kt",'
                                 ' "scale": 500}'
                                 )

        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"Advanced Spring Configuration ({self.device_type.capitalize()})")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)

        self.pb_copy_up.setIcon(QIcon(":/image/up_arrow.png"))
        self.pb_copy_up.setText('')
        self.pb_copy_up.setMinimumWidth(25)
        self.pb_copy_up.setToolTip('Copy Y-Axis settings up to X-Axis')
        self.pb_copy_up.clicked.connect(lambda: self.copy_y_to_x())

        self.pb_copy_down.setIcon(QIcon(":/image/down_arrow.png"))
        self.pb_copy_down.setText('')
        self.pb_copy_down.setMinimumWidth(25)
        self.pb_copy_down.setToolTip('Copy X-Axis settings down to Y-Axis')
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

        self.pb_save.setToolTip('Save setting and keep dialog open. Will revert upon close unless saved')
        self.pb_save.clicked.connect(lambda: self.save_curve_settings(close=False))

        self.pb_cancel.setToolTip('Close dialog and revert to settings state when dialog was opened')
        self.pb_cancel.clicked.connect(self.cancel_curve_settings)

        self.pb_revert.setToolTip('Revert to settings state when dialog opened')
        self.pb_revert.clicked.connect(self.revert_curve_settings)

        self.tog_live_view.setToolTip('Show current live spring gain and airspeed on graph')
        self.tog_live_view.stateChanged.connect(self.toggle_live_view)
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
            self.lab_y.setText(f'<html><head/><body><p><span style="color:grey;">Y</span></p></body></html>')

        self.sl_x_mastergain.valueChanged.connect(lambda: self.update_slider_labels())
        self.sl_y_mastergain.valueChanged.connect(lambda: self.update_slider_labels())
        self.sl_x_mastergain.setValue(self.gain_x)
        self.sl_y_mastergain.setValue(self.gain_y)

        self.cb_x_smoothcurve.stateChanged.connect(self.curve_x.toggle_smooth_curve)
        self.cb_y_smoothcurve.stateChanged.connect(self.curve_y.toggle_smooth_curve)

        self.pb_x_reset.clicked.connect(lambda: self.reset_curve('x'))
        self.pb_y_reset.clicked.connect(lambda: self.reset_curve('y'))

    def showEvent(self, event):
        if self.current_settings is not None:
            self.init_settings = self.current_settings

        # self.raise_()
        # self.activateWindow()
        # self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        # self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
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
            self.curve_x.clear_crosshairs()
            self.curve_x.msg_label.hide()
            self.curve_y.clear_crosshairs()
            self.curve_y.msg_label.hide()

    def draw_live_view(self, data):
        ias = data.get('IAS', 0)
        current_gains = G.telem_manager.currentAircraft.adv_spr_gains
        if current_gains != "none":
            gains = get_gain_from_speed(current_gains, ias)
        else:
            gains = None
        # print(f"gains: {current_gains}")
        for axis, gain in zip([self.curve_x, self.curve_y], ['x', 'y']):
            if gains is None:
                axis.msg_label.setText('Please save a configuration before enabling live view')
                axis.msg_label.show()
                # G.telem_manager.currentAircraft.flag_error('Please save a configuration before enabling live view')
                continue
            else:
                # print(f"DRAWING: {gains}")
                axis.draw_crosshairs(ias, gains.get(gain, 0)*100)

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
            "curve_x": self.curve_x.to_dict(),
            "gain_x": self.sl_x_mastergain.value(),
            "curve_y": self.curve_y.to_dict(),
            "gain_y": self.sl_y_mastergain.value(),
            "units": self.current_unit,
            "scale": round(self.x_scale)
        }
        json_string = json.dumps(settings)
        self.current_settings = json_string
        self.accepted.emit(json_string)
        if close:
            self.tog_live_view.setChecked(False)
            self.hide()

    def load_curve_settings(self, json_string):
        """
        Load settings from a JSON-formatted string and apply them to both curve widgets.
        """
        try:
            settings = json.loads(json_string)
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
                self.current_unit = settings.get('units', "kt")
                self.cb_airspeed_unit.setCurrentText(self.current_unit)
                self.update_slider_labels()

            else:
                raise ValueError("Invalid JSON format: Missing 'curve_x' or 'curve_y' keys.")
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON: {e}")
            raise ValueError("Invalid JSON string.")
        except Exception as e:
            print(f"Error loading curve settings: {e}")
            raise

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

                # # Apply the new airspeed value (example logic)
                # for axis in [self.curve_x, self.curve_y]:
                #     axis.x_scale = new_airspeed
                #     axis.update()
                print(f"increment = {new_airspeed - self.x_scale}")
                self.change_airspeed_scale(new_airspeed - self.x_scale)
                self.x_scale = new_airspeed

            except ValueError as e:
                # Show an error message if input is invalid
                QMessageBox.warning(self, "Invalid Input", str(e))

    def copy_x_to_y(self):
        self.curve_y.from_dict(self.curve_x.to_dict())

    def copy_y_to_x(self):
        self.curve_x.from_dict(self.curve_y.to_dict())
        pass

    def change_airspeed_scale(self, increment):
        for axis in [self.curve_x, self.curve_y]:
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

        for axis in [self.curve_x, self.curve_y]:
            # Update points and x_scale
            axis.points = [QPointF(p.x() * conversion_factor, p.y()) for p in axis.points]
            axis.x_min *= conversion_factor
            axis.x_max *= conversion_factor
            axis.update()
            axis.current_unit = new_unit
        self.x_scale *= conversion_factor
        self.current_unit = new_unit

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




