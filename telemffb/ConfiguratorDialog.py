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


import logging

from PyQt6 import QtCore
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import QDialog, QMessageBox
import inspect

import telemffb.globals as G
from telemffb.ui.Ui_ConfiguratorDialog import Ui_ConfiguratorDialog
from telemffb.utils import dbprint
from .hw.ffb_rhino import (FFB_GAIN_CONSTANT, FFB_GAIN_DAMPER,
                           FFB_GAIN_FRICTION, FFB_GAIN_INERTIA,
                           FFB_GAIN_MASTER, FFB_GAIN_PERIODIC, FFB_GAIN_SPRING,
                           HapticEffect)


class ConfiguratorDialog(QDialog, Ui_ConfiguratorDialog):
    global dev
    reference_state = {
        "master_gain": {"enabled": False, "value": 0},
        "periodic_gain": {"enabled": False, "value": 0},
        "spring_gain": {"enabled": False, "value": 0},
        "damper_gain": {"enabled": False, "value": 0},
        "inertia_gain": {"enabled": False, "value": 0},
        "friction_gain": {"enabled": False, "value": 0},
        "constant_gain": {"enabled": False, "value": 0},
    }
    cb_states = {'cb_MasterGain': 0,'cb_Spring': 0, 'cb_Periodic': 0, 'cb_Damper': 0, 'cb_Inertia': 0, 'cb_Friction': 0, 'cb_Constant': 0}
    accepted = pyqtSignal(dict)
    cb_dict = {}
    sl_dict = {}

    def __init__(self, parent=None):
        super(ConfiguratorDialog, self).__init__(parent)

        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"Configurator Gain Override ({G.device_type.capitalize()})")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)


        self.sliders = [
            self.sl_MasterGain,
            self.sl_Periodic,
            self.sl_Spring,
            self.sl_Damper,
            self.sl_Inertia,
            self.sl_Friction,
            self.sl_Constant
        ]
        self.checkboxes = [
            self.cb_MasterGain,
            self.cb_Periodic,
            self.cb_Spring,
            self.cb_Damper,
            self.cb_Inertia,
            self.cb_Friction,
            self.cb_Constant
        ]

        self.labels = [
            self.lab_MasterGainValue,
            self.lab_PeriodicValue,
            self.lab_SpringValue,
            self.lab_DamperValue,
            self.lab_InertiaValue,
            self.lab_FrictionValue,
            self.lab_ConstantValue
        ]

        self.cb_MasterGain.stateChanged.connect(self.cb_toggle)
        self.cb_Periodic.stateChanged.connect(self.cb_toggle)
        self.cb_Spring.stateChanged.connect(self.cb_toggle)
        self.cb_Damper.stateChanged.connect(self.cb_toggle)
        self.cb_Inertia.stateChanged.connect(self.cb_toggle)
        self.cb_Friction.stateChanged.connect(self.cb_toggle)
        self.cb_Constant.stateChanged.connect(self.cb_toggle)

        self.cb_MasterGain.setChecked(self.cb_states['cb_MasterGain'])
        self.cb_Periodic.setChecked(self.cb_states['cb_Periodic'])
        self.cb_Spring.setChecked(self.cb_states['cb_Spring'])
        self.cb_Damper.setChecked(self.cb_states['cb_Damper'])
        self.cb_Inertia.setChecked(self.cb_states['cb_Inertia'])
        self.cb_Friction.setChecked(self.cb_states['cb_Friction'])
        self.cb_Constant.setChecked(self.cb_states['cb_Constant'])

        self.pb_Revert.clicked.connect(self.revert_gains)
        self.pb_Revert.setToolTip('Revert the settings to the values learned when TelemFFB was started -or- to the values in the last vpconf profile that was pushed by TelemFFB (if one has been pushed)')

        self.pb_Finish.clicked.connect(self.finish)
        self.pb_Finish.setToolTip('Save the current settings to the configuration')

        self.pb_Cancel.clicked.connect(self.canceled)
        self.pb_Cancel.setToolTip('Revert the settings to the current saved config value and close the dialog')

        self.at_show_state = self.construct_setting_table()

        if HapticEffect.device is not None:
            self._dev_gains_at_show = HapticEffect.device.get_gains()
        else:
            self._dev_gains_at_show = None

        # self.read_gains()

        self.live_updates = False

        self.cb_LiveUpdates.stateChanged.connect(self.toggle_live_updates)
        self.cb_LiveUpdates.setChecked(False)

        self.setup_properties()

        self.connect_all_sliders()

    def connect_slider(self, slider):
        slider.valueChanged.connect(self.update_labels)
        slider.delayedValueChanged.connect(self.set_gain_value)

    def disconnect_slider(self, slider):
        try:
            slider.valueChanged.disconnect()
            slider.delayedValueChanged.disconnect()
        except (TypeError, RuntimeError):
            pass

    def connect_all_sliders(self):
        for sl in self.sliders:
            sl.valueChanged.connect(self.update_labels)
            sl.delayedValueChanged.connect(self.set_gain_value)

    def disconnect_all_sliders(self):
        for sl in self.sliders:
            try:
                sl.valueChanged.disconnect()
                sl.delayedValueChanged.disconnect()
            except (TypeError, RuntimeError):
                pass  # nothing connected / already disconnected

    def setup_properties(self):

        # Tie checkboxes to setting keys:

        self.ui_dict = {
            'master_gain': {'cb': self.cb_MasterGain, 'sl': self.sl_MasterGain, 'label': self.lab_MasterGainValue, 'gain_id': FFB_GAIN_MASTER},
            'periodic_gain': {'cb': self.cb_Periodic, 'sl': self.sl_Periodic, 'label': self.lab_PeriodicValue, 'gain_id': FFB_GAIN_PERIODIC},
            'spring_gain': {'cb': self.cb_Spring, 'sl': self.sl_Spring, 'label': self.lab_SpringValue, 'gain_id': FFB_GAIN_SPRING},
            'damper_gain': {'cb': self.cb_Damper, 'sl': self.sl_Damper, 'label': self.lab_DamperValue, 'gain_id': FFB_GAIN_DAMPER},
            'inertia_gain': {'cb': self.cb_Inertia, 'sl': self.sl_Inertia, 'label': self.lab_InertiaValue, 'gain_id': FFB_GAIN_INERTIA},
            'friction_gain': {'cb': self.cb_Friction, 'sl': self.sl_Friction, 'label': self.lab_FrictionValue, 'gain_id': FFB_GAIN_FRICTION},
            'constant_gain': {'cb': self.cb_Constant, 'sl': self.sl_Constant, 'label': self.lab_ConstantValue, 'gain_id': FFB_GAIN_CONSTANT},
        }

        self.cb_dict = {
            'master_gain': self.cb_MasterGain,
            'periodic_gain': self.cb_Periodic,
            'spring_gain': self.cb_Spring,
            'damper_gain': self.cb_Damper,
            'inertia_gain': self.cb_Inertia,
            'friction_gain': self.cb_Friction,
            'constant_gain': self.cb_Constant,
        }

        # Tie dictionary keys to checkboxes
        self.cb_MasterGain.setting_key = 'master_gain'
        self.cb_Periodic.setting_key = 'periodic_gain'
        self.cb_Spring.setting_key = 'spring_gain'
        self.cb_Damper.setting_key = 'damper_gain'
        self.cb_Inertia.setting_key = 'inertia_gain'
        self.cb_Friction.setting_key = 'friction_gain'
        self.cb_Constant.setting_key = 'constant_gain'

        # Tie sliders to checkboxes
        self.cb_MasterGain.slider = self.sl_MasterGain
        self.cb_Periodic.slider = self.sl_Periodic
        self.cb_Spring.slider = self.sl_Spring
        self.cb_Damper.slider = self.sl_Damper
        self.cb_Inertia.slider = self.sl_Inertia
        self.cb_Friction.slider = self.sl_Friction
        self.cb_Constant.slider = self.sl_Constant

        # Tie gain IDs to checkboxes
        self.cb_MasterGain.gain_id = FFB_GAIN_MASTER
        self.cb_Periodic.gain_id = FFB_GAIN_PERIODIC
        self.cb_Spring.gain_id = FFB_GAIN_SPRING
        self.cb_Damper.gain_id = FFB_GAIN_DAMPER
        self.cb_Inertia.gain_id = FFB_GAIN_INERTIA
        self.cb_Friction.gain_id = FFB_GAIN_FRICTION
        self.cb_Constant.gain_id = FFB_GAIN_CONSTANT

        self.sl_dict = {
            'master_gain': self.sl_MasterGain,
            'periodic_gain': self.sl_Periodic,
            'spring_gain': self.sl_Spring,
            'damper_gain': self.sl_Damper,
            'inertia_gain': self.sl_Inertia,
            'friction_gain': self.sl_Friction,
            'constant_gain': self.sl_Constant,
        }

        # Tie sliders to dictionary keys
        self.sl_MasterGain.setting_key = 'master_gain'
        self.sl_Periodic.setting_key = 'periodic_gain'
        self.sl_Spring.setting_key = 'spring_gain'
        self.sl_Damper.setting_key = 'damper_gain'
        self.sl_Inertia.setting_key = 'inertia_gain'
        self.sl_Friction.setting_key = 'friction_gain'
        self.sl_Constant.setting_key = 'constant_gain'

        # tie sliders to checkboxes
        self.sl_MasterGain.checkbox = self.cb_MasterGain
        self.sl_Periodic.checkbox = self.cb_Periodic
        self.sl_Spring.checkbox = self.cb_Spring
        self.sl_Damper.checkbox = self.cb_Damper
        self.sl_Inertia.checkbox = self.cb_Inertia
        self.sl_Friction.checkbox = self.cb_Friction
        self.sl_Constant.checkbox = self.cb_Constant

        # Tie sliders to labels
        self.sl_MasterGain.label = self.lab_MasterGainValue
        self.sl_Periodic.label = self.lab_PeriodicValue
        self.sl_Spring.label = self.lab_SpringValue
        self.sl_Damper.label = self.lab_DamperValue
        self.sl_Inertia.label = self.lab_InertiaValue
        self.sl_Friction.label = self.lab_FrictionValue
        self.sl_Constant.label = self.lab_ConstantValue

        # Tie gain IDs to sliders
        self.sl_MasterGain.gain_id = FFB_GAIN_MASTER
        self.sl_Periodic.gain_id = FFB_GAIN_PERIODIC
        self.sl_Spring.gain_id = FFB_GAIN_SPRING
        self.sl_Damper.gain_id = FFB_GAIN_DAMPER
        self.sl_Inertia.gain_id = FFB_GAIN_INERTIA
        self.sl_Friction.gain_id = FFB_GAIN_FRICTION
        self.sl_Constant.gain_id = FFB_GAIN_CONSTANT

    def toggle_live_updates(self, state):
        self.live_updates = state
        if state:
            self.set_gains_from_ui()

    def construct_setting_table(self):
        try:
            gains = HapticEffect.device.get_gains()
        except Exception as e:
            logging.warning(f"Error getting gain values from the device: {e}")
            return
        if gains is None:
            # device backend has no Configurator gains (generic DirectInput)
            logging.debug("construct_setting_table: no gains available from this device")
            return
        state = {
            "master_gain": {"enabled": self.cb_MasterGain.isChecked(), "value": gains.master_gain},
            "periodic_gain": {"enabled": self.cb_Periodic.isChecked(), "value": gains.periodic_gain},
            "spring_gain": {"enabled": self.cb_Spring.isChecked(), "value": gains.spring_gain},
            "damper_gain": {"enabled": self.cb_Damper.isChecked(), "value": gains.damper_gain},
            "inertia_gain": {"enabled": self.cb_Inertia.isChecked(), "value": gains.inertia_gain},
            "friction_gain": {"enabled": self.cb_Friction.isChecked(), "value": gains.friction_gain},
            "constant_gain": {"enabled": self.cb_Constant.isChecked(), "value": gains.constant_gain},
        }
        return state

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def close(self):
        self.hide()

    def showEvent(self, event):
        self.raise_()
        self.activateWindow()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        super().showEvent(event)

    def show(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.raise_()
        self.activateWindow()
        super().show()

    def load_and_show(self, state=None):
        self._dev_gains_at_show = HapticEffect.device.get_gains()
        self.at_show_state = state
        self.set_ui_from_state(state)
        self.show()

    def canceled(self):
        self.set_gains_from_object(self._dev_gains_at_show)
        # self.read_gains()
        self.close()

    def reset_to_vpconf(self):
        self.set_gains_from_object(G.vpconf_configurator_gains)

    def revert_gains(self):
        """
        Reverts sliders to state when dialog shown

        If live updates are enabled, the gains are set on the device immediately
        """
        self.set_ui_from_state(self.at_show_state)
        if self.live_updates:
            self.set_gains_from_state(self.at_show_state)

    def finish(self):
        """
        constructs the settings dictionary, emits the accepted signal (which is connected to update the setting value
        in SettingsLayout and updates the G.current_configurator_gains value.
        """
        state = self.get_state_from_ui()
        self.accepted.emit(state)
        self.close()

    def reset_ui(self):
        for cb in self.checkboxes:
            print(f"resetting {cb.setting_key}")
            sl = cb.slider
            label = sl.label
            cb.setChecked(False)
            sl.setValue(0)
            sl.setEnabled(False)
            label.setText(f"%N/A")

    def cb_toggle(self, state):
        print(f"cb_toggle: {state}")
        current_gains = HapticEffect.device.get_gains()
        cb = self.sender()
        slider = cb.slider
        label = slider.label
        cb.slider.blockSignals(True)

        if state:
            slider.setEnabled(True)
            slider.setValue(getattr(current_gains, cb.setting_key) if current_gains is not None else 0)
            label.setText(f"%{slider.value()}")
        else:
            print(f"Disabling {cb.setting_key}")
            slider.setEnabled(True)
            slider.setValue(0)
            slider.setEnabled(False)
            label.setText(f"%N/A")

        cb.slider.blockSignals(False)

    def get_state_from_ui(self):
        state = {}
        for cb in self.checkboxes:
            key = cb.setting_key
            enable = cb.isChecked()
            value = cb.slider.value() if enable else 0
            state.update({key: {'enabled': enable, 'value': value}})

        return state if state else None

    def set_ui_from_state(self, state):
        self.reset_ui()
        if not state:
            return
        for gain in state.keys():
            print(f"Setting {gain} to {state[gain]}")
            cb = self.cb_dict[gain]
            sl = self.sl_dict[gain]
            label = sl.label
            enabled = state[gain]['enabled']
            value = state[gain]['value']
            cb.setChecked(enabled)
            if enabled:
                sl.setEnabled(True)
                sl.setValue(int(value))
                label.setText(f"%{value}")
            else:
                sl.setValue(0)
                sl.setEnabled(False)
                label.setText(f"%N/A")

    def set_gains_from_state(self, state):
        """
        Applies the gains held in the user configuration file.  Settings are exported by the 'construct_setting_table'
        method when user saves the gain config.
        """
        dev = HapticEffect.device
        # For any gain whose override is DISABLED, restore it to the baseline
        # (the vpconf-profile gains, else the startup/live device gains). The
        # baseline can be unset when telemetry loads an override aircraft
        # before the async startup init populated G.vpconf_configurator_gains
        # (sim already running at launch) — guard rather than crash.
        baseline = self._baseline_gains()
        for setting in state.keys():
            enabled = state[setting]['enabled']
            id = self.ui_dict[setting]['gain_id']
            if enabled:
                dev.set_gain(id, int(state[setting]['value']))
            elif baseline is not None:
                dev.set_gain(id, getattr(baseline, setting))



    @staticmethod
    def _baseline_gains():
        """Baseline configurator gains for restoring non-overridden values.

        Prefers the gains captured after the active vpconf profile was pushed,
        then the gains learned at startup, then the device's live gains.
        Guards the startup race where telemetry loads an override aircraft
        before the async init populated G.vpconf_configurator_gains.
        """
        baseline = G.vpconf_configurator_gains
        if baseline is None:
            baseline = G.startup_configurator_gains
        if baseline is None and HapticEffect.device is not None:
            try:
                baseline = HapticEffect.device.get_gains()
            except Exception:
                logging.exception("Could not read device gains for override baseline")
        return baseline

    def set_gains_from_object(self, gains_object):
        dev = HapticEffect.device
        if gains_object is None:
            gains_object = self._baseline_gains()
        if gains_object is None:
            logging.warning("No baseline configurator gains available; "
                            "leaving device gains unchanged")
            return

        dev.set_gain(FFB_GAIN_MASTER, gains_object.master_gain)
        dev.set_gain(FFB_GAIN_PERIODIC, gains_object.periodic_gain)
        dev.set_gain(FFB_GAIN_SPRING, gains_object.spring_gain)
        dev.set_gain(FFB_GAIN_DAMPER, gains_object.damper_gain)
        dev.set_gain(FFB_GAIN_INERTIA, gains_object.inertia_gain)
        dev.set_gain(FFB_GAIN_FRICTION, gains_object.friction_gain)
        dev.set_gain(FFB_GAIN_CONSTANT, gains_object.constant_gain)

    def set_gains_from_ui(self):
        dev = HapticEffect.device

        for cb in self.checkboxes:
            sl = cb.slider
            gain_id = cb.gain_id
            if cb.isChecked():
                dev.set_gain(gain_id, int(sl.value()))

    def set_gain_value(self, value):
        """
        Sets the gain value on device in real-time when sliders are adjusted
        """

        if not self.live_updates:
            return

        dev = HapticEffect.device

        # sender = self.sender()
        # sender_str = sender.objectName()
        sl = self.sender()
        id = sl.gain_id
        dev.set_gain(id, int(value))
        # match sender_str:
        #     case 'sl_MasterGain':
        #         dev.set_gain(FFB_GAIN_MASTER, int(value))
        #     case 'sl_Periodic':
        #         dev.set_gain(FFB_GAIN_PERIODIC, int(value))
        #     case 'sl_Spring':
        #         dev.set_gain(FFB_GAIN_SPRING, int(value))
        #     case 'sl_Damper':
        #         dev.set_gain(FFB_GAIN_DAMPER, int(value))
        #     case 'sl_Inertia':
        #         dev.set_gain(FFB_GAIN_INERTIA, int(value))
        #     case 'sl_Friction':
        #         dev.set_gain(FFB_GAIN_FRICTION, int(value))
        #     case 'sl_Constant':
        #         dev.set_gain(FFB_GAIN_CONSTANT, int(value))

    def update_labels(self):
        """
        Updates the slider labels
        """
        slider = self.sender()
        slider.label.setText(f"%{slider.value()}")
