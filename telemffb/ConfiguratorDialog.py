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
    state = {
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

    def __init__(self, parent=None):
        super(ConfiguratorDialog, self).__init__(parent)

        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"Configurator Gain Override ({G.device_type.capitalize()})")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        # self.cb_MasterGain.clicked
        self.sl_MasterGain.valueChanged.connect(self.update_labels)
        self.sl_MasterGain.delayedValueChanged.connect(self.set_gain_value)

        self.sl_Periodic.valueChanged.connect(self.update_labels)
        self.sl_Periodic.delayedValueChanged.connect(self.set_gain_value)

        self.sl_Spring.valueChanged.connect(self.update_labels)
        self.sl_Spring.delayedValueChanged.connect(self.set_gain_value)

        self.sl_Damper.valueChanged.connect(self.update_labels)
        self.sl_Damper.delayedValueChanged.connect(self.set_gain_value)

        self.sl_Inertia.valueChanged.connect(self.update_labels)
        self.sl_Inertia.delayedValueChanged.connect(self.set_gain_value)

        self.sl_Friction.valueChanged.connect(self.update_labels)
        self.sl_Friction.delayedValueChanged.connect(self.set_gain_value)

        self.sl_Constant.valueChanged.connect(self.update_labels)
        self.sl_Constant.delayedValueChanged.connect(self.set_gain_value)

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

        self.read_gains()

    def construct_setting_table(self):
        try:
            gains = HapticEffect.device.get_gains()
        except Exception as e:
            logging.warning(f"Error getting gain values from the device: {e}")
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
        dev = HapticEffect.device
        if dev is None:
            QMessageBox.warning(self, "Error",
                                "Device is not connected.  Unable to open real-time gain override dialog")
            return
        try:
            gains = dev.get_gains()
        except:
            QMessageBox.warning(self, "Error",
                                "Error reading gains from device.  Unable to open real-time gain override dialog")
            return
        if G.current_configurator_gains is not None and G.current_configurator_gains != {}:
            self.set_gains_from_state(G.current_configurator_gains)
        self.read_gains()
        self.at_show_state = self.construct_setting_table()

        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.raise_()
        self.activateWindow()
        super().show()
        # dbprint("blue", f"Startup Gains: {self.at_show_state}")

    def canceled(self):
        if isinstance(self.at_show_state, dict):
            # dbprint("green", f"Override Dialog Canceled: {self.at_show_state}")
            self.set_ui_from_state(self.at_show_state)
            self.set_gains_from_state(self.at_show_state)
            self.read_gains()

        self.close()
    def finish(self):
        """
        constructs the settings dictionary, emits the accepted signal (which is connected to update the setting value
        in SettingsLayout and updates the G.current_configurator_gains value.
        """
        state = self.construct_setting_table()
        self.accepted.emit(state)
        G.current_configurator_gains = state
        self.close()

    def cb_toggle(self, state):
        """
        If the toggle has been disabled for a given effect type, this method reverts that setting back to the value
        which has been last configured by a vpconf profile push.  If no vpconf profile has been pushed, the values will
        be what were on the device when TelemFFB started
        """
        dev = HapticEffect.device

        sender = self.sender()
        sender_str = sender.objectName()
        self.cb_states[sender_str] = state

        if state: return

        match sender_str:
            case 'cb_MasterGain':
                dev.set_gain(FFB_GAIN_MASTER, int(G.vpconf_configurator_gains.master_gain))
            case 'cb_Periodic':
                dev.set_gain(FFB_GAIN_PERIODIC, G.vpconf_configurator_gains.periodic_gain)
            case 'cb_Spring':
                dev.set_gain(FFB_GAIN_SPRING, G.vpconf_configurator_gains.spring_gain)
            case 'cb_Damper':
                dev.set_gain(FFB_GAIN_DAMPER, G.vpconf_configurator_gains.damper_gain)
            case 'cb_Inertia':
                dev.set_gain(FFB_GAIN_INERTIA, G.vpconf_configurator_gains.inertia_gain)
            case 'cb_Friction':
                dev.set_gain(FFB_GAIN_FRICTION, G.vpconf_configurator_gains.friction_gain)
            case 'sl_Constant':
                dev.set_gain(FFB_GAIN_CONSTANT, G.vpconf_configurator_gains.constant_gain)
        self.read_gains()


    def reset_to_vpconf(self):
        self.cb_MasterGain.setChecked(False)
        self.cb_Periodic.setChecked(False)
        self.cb_Spring.setChecked(False)
        self.cb_Damper.setChecked(False)
        self.cb_Inertia.setChecked(False)
        self.cb_Friction.setChecked(False)
        self.cb_Constant.setChecked(False)
        self.set_gains_from_object(G.vpconf_configurator_gains)
        G.current_configurator_gains = self.construct_setting_table()

    def revert_gains(self):
        """
        Sets the gain back to 'G.vpconf_configurator_gains' which holds the gain values from the last time
        a vpconf profile was pushed to the device (or if no vpconf has been pushed, the gains when TelemFFB started

        Disables the UI checkboxes and then reads the gain values again to reset the sliders
        """
        dev = HapticEffect.device
        dev.set_gain(FFB_GAIN_MASTER, G.vpconf_configurator_gains.master_gain)
        self.cb_MasterGain.setChecked(False)
        dev.set_gain(FFB_GAIN_PERIODIC, G.vpconf_configurator_gains.periodic_gain)
        self.cb_Periodic.setChecked(False)
        dev.set_gain(FFB_GAIN_SPRING, G.vpconf_configurator_gains.spring_gain)
        self.cb_Spring.setChecked(False)
        dev.set_gain(FFB_GAIN_DAMPER, G.vpconf_configurator_gains.damper_gain)
        self.cb_Damper.setChecked(False)
        dev.set_gain(FFB_GAIN_INERTIA, G.vpconf_configurator_gains.inertia_gain)
        self.cb_Inertia.setChecked(False)
        dev.set_gain(FFB_GAIN_FRICTION, G.vpconf_configurator_gains.friction_gain)
        self.cb_Friction.setChecked(False)
        dev.set_gain(FFB_GAIN_CONSTANT, G.vpconf_configurator_gains.constant_gain)
        self.cb_Constant.setChecked(False)
        self.read_gains()

    def set_ui_from_state(self, state):
        dev = HapticEffect.device
        caller_frame = inspect.currentframe().f_back

        self.cb_MasterGain.setChecked(state['master_gain']['enabled'])
        self.sl_MasterGain.setValue(int(state['master_gain']['value']))
        # dev.set_gain(FFB_GAIN_MASTER, int(state['master_gain']['value']))
        self.cb_Periodic.setChecked(state['periodic_gain']['enabled'])
        self.sl_Periodic.setValue(int(state['periodic_gain']['value']))
        # dev.set_gain(FFB_GAIN_PERIODIC, int(state['periodic_gain']['value']))
        self.cb_Spring.setChecked(state['spring_gain']['enabled'])
        self.sl_Spring.setValue(int(state['spring_gain']['value']))
        # dev.set_gain(FFB_GAIN_SPRING, int(state['spring_gain']['value']))
        self.cb_Damper.setChecked(state['damper_gain']['enabled'])
        self.sl_Damper.setValue(int(state['damper_gain']['value']))
        # dev.set_gain(FFB_GAIN_DAMPER, int(state['damper_gain']['value']))
        self.cb_Inertia.setChecked(state['inertia_gain']['enabled'])
        self.sl_Inertia.setValue(int(state['inertia_gain']['value']))
        # dev.set_gain(FFB_GAIN_INERTIA, int(state['inertia_gain']['value']))
        self.cb_Friction.setChecked(state['friction_gain']['enabled'])
        self.sl_Friction.setValue(int(state['friction_gain']['value']))
        # dev.set_gain(FFB_GAIN_FRICTION, int(state['friction_gain']['value']))
        self.cb_Constant.setChecked(state['constant_gain']['enabled'])
        self.sl_Constant.setValue(int(state['constant_gain']['value']))
        # dev.set_gain(FFB_GAIN_CONSTANT, int(state['constant_gain']['value']))

    def set_gains_from_state(self, state):
        """
        Applies the gains held in the user configuration file.  Settings are exported by the 'construct_setting_table'
        method when user saves the gain config.
        """
        dev = HapticEffect.device
        caller_frame = inspect.currentframe().f_back

        if state['master_gain']['enabled']:
            self.cb_MasterGain.setChecked(True)
            self.sl_MasterGain.setValue(int(state['master_gain']['value']))
            dev.set_gain(FFB_GAIN_MASTER, int(state['master_gain']['value']))
        if state['periodic_gain']['enabled']:
            self.cb_Periodic.setChecked(True)
            self.sl_Periodic.setValue(int(state['periodic_gain']['value']))
            dev.set_gain(FFB_GAIN_PERIODIC, int(state['periodic_gain']['value']))
        if state['spring_gain']['enabled']:
            self.cb_Spring.setChecked(True)
            self.sl_Spring.setValue(int(state['spring_gain']['value']))
            dev.set_gain(FFB_GAIN_SPRING, int(state['spring_gain']['value']))
        if state['damper_gain']['enabled']:
            self.cb_Damper.setChecked(True)
            self.sl_Damper.setValue(int(state['damper_gain']['value']))
            dev.set_gain(FFB_GAIN_DAMPER, int(state['damper_gain']['value']))
        if state['inertia_gain']['enabled']:
            self.cb_Inertia.setChecked(True)
            self.sl_Inertia.setValue(int(state['inertia_gain']['value']))
            dev.set_gain(FFB_GAIN_INERTIA, int(state['inertia_gain']['value']))
        if state['friction_gain']['enabled']:
            self.cb_Friction.setChecked(True)
            self.sl_Friction.setValue(int(state['friction_gain']['value']))
            dev.set_gain(FFB_GAIN_FRICTION, int(state['friction_gain']['value']))
        if state['constant_gain']['enabled']:
            self.cb_Constant.setChecked(True)
            self.sl_Constant.setValue(int(state['constant_gain']['value']))
            dev.set_gain(FFB_GAIN_CONSTANT, int(state['constant_gain']['value']))


    def set_gains_from_object(self, gains_object):
        dev = HapticEffect.device

        dev.set_gain(FFB_GAIN_MASTER, gains_object.master_gain)
        dev.set_gain(FFB_GAIN_PERIODIC, gains_object.periodic_gain)
        dev.set_gain(FFB_GAIN_SPRING, gains_object.spring_gain)
        dev.set_gain(FFB_GAIN_DAMPER, gains_object.damper_gain)
        dev.set_gain(FFB_GAIN_INERTIA, gains_object.inertia_gain)
        dev.set_gain(FFB_GAIN_FRICTION, gains_object.friction_gain)
        dev.set_gain(FFB_GAIN_CONSTANT, gains_object.constant_gain)


    def set_gain_value(self, value):
        """
        Sets the gain value on device in real-time when sliders are adjusted
        """
        dev = HapticEffect.device

        sender = self.sender()
        sender_str = sender.objectName()
        match sender_str:
            case 'sl_MasterGain':
                dev.set_gain(FFB_GAIN_MASTER, int(value))
            case 'sl_Periodic':
                dev.set_gain(FFB_GAIN_PERIODIC, int(value))
            case 'sl_Spring':
                dev.set_gain(FFB_GAIN_SPRING, int(value))
            case 'sl_Damper':
                dev.set_gain(FFB_GAIN_DAMPER, int(value))
            case 'sl_Inertia':
                dev.set_gain(FFB_GAIN_INERTIA, int(value))
            case 'sl_Friction':
                dev.set_gain(FFB_GAIN_FRICTION, int(value))
            case 'sl_Constant':
                dev.set_gain(FFB_GAIN_CONSTANT, int(value))


    def read_gains(self):
        """
        Reads the gains from the device gains table and updates the sliders accordingly
        """
        try:
            gains = HapticEffect.device.get_gains()
        except Exception as e:
            logging.warning(f"Error getting gain values from the device: {e}")
            return
        self.sl_MasterGain.setValue(gains.master_gain)
        self.sl_Periodic.setValue(gains.periodic_gain)
        self.sl_Spring.setValue(gains.spring_gain)
        self.sl_Damper.setValue(gains.damper_gain)
        self.sl_Inertia.setValue(gains.inertia_gain)
        self.sl_Friction.setValue(gains.friction_gain)
        self.sl_Constant.setValue(gains.constant_gain)
        # self.update_labels()
        # print(gains)

    def update_labels(self):
        """
        Updates the slider labels
        """
        self.lab_MasterGainValue.setText(f"%{self.sl_MasterGain.value()}")
        self.lab_PeriodicValue.setText(f"%{self.sl_Periodic.value()}")
        self.lab_SpringValue.setText(f"%{self.sl_Spring.value()}")
        self.lab_DamperValue.setText(f"%{self.sl_Damper.value()}")
        self.lab_InertiaValue.setText(f"%{self.sl_Inertia.value()}")
        self.lab_FrictionValue.setText(f"%{self.sl_Friction.value()}")
        self.lab_ConstantValue.setText(f"%{self.sl_Constant.value()}")