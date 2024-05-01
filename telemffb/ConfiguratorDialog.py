from PyQt5 import QtCore
from PyQt5.QtWidgets import QDialog

import telemffb.globals as G
from telemffb.ui.Ui_ConfiguratorDialog import Ui_ConfiguratorDialog

from .hw.ffb_rhino import (FFB_GAIN_CONSTANT, FFB_GAIN_DAMPER,
                           FFB_GAIN_FRICTION, FFB_GAIN_INERTIA,
                           FFB_GAIN_MASTER, FFB_GAIN_PERIODIC, FFB_GAIN_SPRING,
                           HapticEffect)


class ConfiguratorDialog(QDialog, Ui_ConfiguratorDialog):
    global dev
    cb_states = {'cb_MasterGain': 0,'cb_Spring': 0, 'cb_Periodic': 0, 'cb_Damper': 0, 'cb_Inertia': 0, 'cb_Friction': 0, 'cb_Constant': 0}

    def __init__(self, parent=None):
        super(ConfiguratorDialog, self).__init__(parent)

        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"Configurator Gain Override ({G.device_type.capitalize()})")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        # self.cb_MasterGain.clicked
        self.sl_MasterGain.valueChanged.connect(self.update_labels)
        self.sl_MasterGain.valueChanged.connect(self.set_gain_value)

        self.sl_Periodic.valueChanged.connect(self.update_labels)
        self.sl_Periodic.valueChanged.connect(self.set_gain_value)

        self.sl_Spring.valueChanged.connect(self.update_labels)
        self.sl_Spring.valueChanged.connect(self.set_gain_value)

        self.sl_Damper.valueChanged.connect(self.update_labels)
        self.sl_Damper.valueChanged.connect(self.set_gain_value)

        self.sl_Inertia.valueChanged.connect(self.update_labels)
        self.sl_Inertia.valueChanged.connect(self.set_gain_value)

        self.sl_Friction.valueChanged.connect(self.update_labels)
        self.sl_Friction.valueChanged.connect(self.set_gain_value)

        self.sl_Constant.valueChanged.connect(self.update_labels)
        self.sl_Constant.valueChanged.connect(self.set_gain_value)

        self.cb_MasterGain.stateChanged.connect(self.cb_toggle)
        self.cb_Periodic.stateChanged.connect(self.cb_toggle)
        self.cb_Spring.stateChanged.connect(self.cb_toggle)
        self.cb_Damper.stateChanged.connect(self.cb_toggle)
        self.cb_Inertia.stateChanged.connect(self.cb_toggle)
        self.cb_Friction.stateChanged.connect(self.cb_toggle)
        self.cb_Constant.stateChanged.connect(self.cb_toggle)

        self.cb_MasterGain.setChecked(self.cb_states['cb_MasterGain'])
        self.cb_Periodic.setCheckState(self.cb_states['cb_Periodic'])
        self.cb_Spring.setCheckState(self.cb_states['cb_Spring'])
        self.cb_Damper.setCheckState(self.cb_states['cb_Damper'])
        self.cb_Inertia.setCheckState(self.cb_states['cb_Inertia'])
        self.cb_Friction.setCheckState(self.cb_states['cb_Friction'])
        self.cb_Constant.setCheckState(self.cb_states['cb_Constant'])

        self.pb_Revert.clicked.connect(self.revert_gains)

        self.pb_Finish.clicked.connect(self.close)

        self.starting_gains = HapticEffect.device.get_gains()
        self.read_gains()


    def cb_toggle(self, state):
        dev = HapticEffect.device

        sender = self.sender()
        sender_str = sender.objectName()
        self.cb_states[sender_str] = state

        if state: return
        match sender_str:
            case 'cb_MasterGain':
                dev.set_gain(FFB_GAIN_MASTER, int(G.startup_configurator_gains.master_gain))
            case 'cb_Periodic':
                dev.set_gain(FFB_GAIN_PERIODIC, G.startup_configurator_gains.periodic_gain)
            case 'cb_Spring':
                dev.set_gain(FFB_GAIN_SPRING, G.startup_configurator_gains.spring_gain)
            case 'cb_Damper':
                dev.set_gain(FFB_GAIN_DAMPER, G.startup_configurator_gains.damper_gain)
            case 'cb_Inertia':
                dev.set_gain(FFB_GAIN_INERTIA, G.startup_configurator_gains.inertia_gain)
            case 'cb_Friction':
                dev.set_gain(FFB_GAIN_FRICTION, G.startup_configurator_gains.friction_gain)
            case 'sl_Constant':
                dev.set_gain(FFB_GAIN_CONSTANT, G.startup_configurator_gains.constant_gain)
        self.read_gains()



    def revert_gains(self):
        dev = HapticEffect.device

        dev.set_gain(FFB_GAIN_MASTER, self.starting_gains.master_gain)
        dev.set_gain(FFB_GAIN_PERIODIC, self.starting_gains.periodic_gain)
        dev.set_gain(FFB_GAIN_SPRING, self.starting_gains.spring_gain)
        dev.set_gain(FFB_GAIN_DAMPER, self.starting_gains.damper_gain)
        dev.set_gain(FFB_GAIN_INERTIA, self.starting_gains.inertia_gain)
        dev.set_gain(FFB_GAIN_FRICTION, self.starting_gains.friction_gain)
        dev.set_gain(FFB_GAIN_CONSTANT, self.starting_gains.constant_gain)
        self.read_gains()

    def set_gain_value(self, value):
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
        gains = HapticEffect.device.get_gains()
        self.sl_MasterGain.setValue(gains.master_gain)
        self.sl_Periodic.setValue(gains.periodic_gain)
        self.sl_Spring.setValue(gains.spring_gain)
        self.sl_Damper.setValue(gains.damper_gain)
        self.sl_Inertia.setValue(gains.inertia_gain)
        self.sl_Friction.setValue(gains.friction_gain)
        self.sl_Constant.setValue(gains.constant_gain)
        # self.update_labels()
        print(gains)

    def update_labels(self):
        self.lab_MasterGainValue.setText(f"%{self.sl_MasterGain.value()}")
        self.lab_PeriodicValue.setText(f"%{self.sl_Periodic.value()}")
        self.lab_SpringValue.setText(f"%{self.sl_Spring.value()}")
        self.lab_DamperValue.setText(f"%{self.sl_Damper.value()}")
        self.lab_InertiaValue.setText(f"%{self.sl_Inertia.value()}")
        self.lab_FrictionValue.setText(f"%{self.sl_Friction.value()}")
        self.lab_ConstantValue.setText(f"%{self.sl_Constant.value()}")