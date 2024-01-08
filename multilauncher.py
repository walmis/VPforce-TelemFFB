from launcherwindow import Ui_LauncherWindow
from PyQt5 import QtWidgets, QtCore
from PyQt5.QtWidgets import (QApplication,  QTableWidgetItem, QCheckBox, QLineEdit, QDialog, QLabel, QComboBox,
                             QVBoxLayout, QPushButton, QFileDialog)
from PyQt5.QtGui import QPixmap, QIcon

import os
import subprocess
import winreg
import sys
import concurrent.futures

#import main

REG_PATH = r"SOFTWARE\VPForce\TelemFFB\MultiLauncher"
class LauncherWindow(QtWidgets.QMainWindow, Ui_LauncherWindow):
    def __init__(self):
        super(LauncherWindow, self).__init__()
        self.setupUi(self)

        self.readsettings()
        self.set_save_on_change()
        self.b_launch.clicked.connect(self.launch)

        script_dir = os.path.dirname(os.path.abspath(__file__))

        # Construct the absolute path of the icon file
        icon_path = os.path.join(script_dir, "image/vpforceicon.png")
        self.setWindowIcon(QIcon(icon_path))

        # autolaunch
        if self.cb_autolaunch.checkState() != 0:
            self.b_launch.click()

    def set_save_on_change(self):
        self.cb_autolaunch.stateChanged.connect(self.savesettings)
        self.cb_pedals.stateChanged.connect(self.savesettings)
        self.cb_collective.stateChanged.connect(self.savesettings)
        self.tb_pedals.textChanged.connect(self.savesettings)
        self.tb_collective.textChanged.connect(self.savesettings)
        self.tb_joystick.textChanged.connect(self.savesettings)

    def readsettings(self):
        self.cb_autolaunch.setCheckState(get_reg('AutoLaunch'))
        self.cb_pedals.setCheckState(get_reg('pedals'))
        self.cb_collective.setCheckState(get_reg('collective'))
        self.tb_pedals.setText(get_reg('pedaladdress'))
        self.tb_collective.setText(get_reg('collectiveaddress'))
        self.tb_joystick.setText(get_reg('joystickaddress'))

    def savesettings(self):

        set_dw_reg('AutoLaunch', self.cb_autolaunch.checkState())
        set_dw_reg('pedals', self.cb_pedals.checkState())
        set_dw_reg('collective', self.cb_collective.checkState())
        set_sz_reg('pedaladdress',self.tb_pedals.text())
        set_sz_reg('collectiveaddress', self.tb_collective.text())
        set_sz_reg('joystickaddress', self.tb_joystick.text())

    def launch(self):
        joystick_args = [f"-D FFFF:{self.tb_joystick.text()}"]
        pedals_args = [f"-D FFFF:{self.tb_pedals.text()}", '-t','pedals'] if self.cb_pedals.checkState() != 0 else None
        collective_args = [f"-D FFFF:{self.tb_collective.text()}", '-t' 'collective'] if self.cb_collective.checkState() != 0 else None

        with concurrent.futures.ProcessPoolExecutor() as executor:
            futures = []

            if joystick_args:
                futures.append(executor.submit(run_process, joystick_args))
            if pedals_args:
                futures.append(executor.submit(run_process, pedals_args))
            if collective_args:
                futures.append(executor.submit(run_process, collective_args))


def run_process(args):
    subprocess.Popen(["python", "main.py"] + args)


def set_sz_reg(name, value):
    try:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                                       winreg.KEY_WRITE)
        winreg.SetValueEx(registry_key, name, 0, winreg.REG_SZ, value)
        winreg.CloseKey(registry_key)
        return True
    except WindowsError:
        return False

def set_dw_reg(name, value):
    try:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                                       winreg.KEY_WRITE)
        winreg.SetValueEx(registry_key, name, 0, winreg.REG_DWORD, value)
        winreg.CloseKey(registry_key)
        return True
    except WindowsError:
        return False

def get_reg(name):
    try:
        registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                                       winreg.KEY_READ)
        value, regtype = winreg.QueryValueEx(registry_key, name)
        winreg.CloseKey(registry_key)
        return value
    except WindowsError:
        return None




if __name__ == "__main__":
    app = QApplication(sys.argv)
    lw = LauncherWindow()
    lw.show()
    sys.exit(app.exec_())
