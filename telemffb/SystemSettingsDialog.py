import json
import logging
import os

from PyQt5 import QtCore
from PyQt5.QtGui import QIntValidator
from PyQt5.QtWidgets import QButtonGroup, QDialog, QFileDialog, QMessageBox

from . import globals as G
from . import utils
from .ui.Ui_SystemDialog import Ui_SystemDialog
from .utils import validate_vpconf_profile


class SystemSettingsDialog(QDialog, Ui_SystemDialog):
    def __init__(self, parent=None,):
        super(SystemSettingsDialog, self).__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"System Settings ({G.device_type.capitalize()})")


        # Add  "INFO" and "DEBUG" options to the logLevel combo box
        self.logLevel.addItems(["INFO", "DEBUG"])
        self.master_button_group = QButtonGroup()
        self.master_button_group.setObjectName(u"master_button_group")
        self.master_button_group.addButton(self.rb_master_j, id=1)
        self.master_button_group.addButton(self.rb_master_p, id=2)
        self.master_button_group.addButton(self.rb_master_c, id=3)

        # Add tooltips
        self.validateIL2.setToolTip('If enabled, TelemFFB will automatically set up the required configuration in IL2 to support telemetry export')
        self.pathIL2.setToolTip('The root path where IL-2 Strumovik is installed')
        self.lab_pathIL2.setToolTip('The root path where IL-2 Strumovik is installed')
        self.validateXPLANE.setToolTip('If enabled, TelemFFB will automatically install the required X-Plane plugin and keep it up to date when it changes')
        self.lab_pathXPLANE.setToolTip('The root path where X-Plane is installed')
        self.pathXPLANE.setToolTip('The root path where X-Plane is installed')
        self.enableVPConfStartup.setToolTip('Select VPforce Configurator profile to load when TelemFFB Starts')
        self.enableVPConfExit.setToolTip('Select VPforce Configurator profile to load when TelemFFB Exits')
        self.cb_logPrune.setToolTip('Auto delete archived logs after time frame')

        # Connect signals to slots
        self.cb_logPrune.stateChanged.connect(self.toggle_log_prune_widgets)
        self.enableIL2.stateChanged.connect(self.toggle_il2_widgets)
        self.enableXPLANE.stateChanged.connect(self.toggle_xplane_widgets)
        self.browseXPLANE.clicked.connect(self.select_xplane_directory)
        self.browseIL2.clicked.connect(self.select_il2_directory)
        self.buttonBox.accepted.connect(self.save_settings)
        self.resetButton.clicked.connect(self.reset_settings)
        self.master_button_group.buttonClicked.connect(lambda button: self.change_master_widgets(button))
        self.cb_al_enable.stateChanged.connect(self.toggle_al_widgets)
        self.enableVPConfStartup.stateChanged.connect(self.toggle_vpconf_startup)
        self.enableVPConfExit.stateChanged.connect(self.toggle_vpconf_exit)
        self.browseVPConfStartup.clicked.connect(lambda: self.browse_vpconf('startup'))
        self.browseVPConfExit.clicked.connect(lambda: self.browse_vpconf('exit'))
        self.buttonBox.rejected.connect(self.close)

        self.buttonChildSettings.setEnabled(False)
        self.buttonChildSettings.setVisible(False)

        # Set initial state
        self.toggle_log_prune_widgets()
        self.toggle_il2_widgets()
        self.toggle_xplane_widgets()
        self.toggle_al_widgets()
        self.parent_window = parent
        # Load settings from the registry and update widget states
        self.current_al_dict = {}
        self.load_settings()
        int_validator = QIntValidator()
        self.tb_logPrune.setValidator(int_validator)
        self.telemTimeout.setValidator(int_validator)
        self.tb_pid_j.setValidator(int_validator)
        self.tb_pid_p.setValidator(int_validator)
        self.tb_pid_c.setValidator(int_validator)

        self.cb_min_enable_j.setObjectName('minimize_j')
        self.cb_min_enable_j.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_p.setObjectName('minimize_p')
        self.cb_min_enable_p.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_c.setObjectName('minimize_c')
        self.cb_min_enable_c.clicked.connect(self.toggle_launchmode_cbs)

        self.cb_headless_j.setObjectName('headless_j')
        self.cb_headless_j.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_p.setObjectName('headless_p')
        self.cb_headless_p.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_c.setObjectName('headless_c')
        self.cb_headless_c.clicked.connect(self.toggle_launchmode_cbs)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)

        if (G.master_instance and G.launched_instances) or G.child_instance:
            self.labelSystem.setText("System (Per Instance):")
            self.labelLaunch.setText("Launch Options (Global):")
            self.labelSim.setText("Sim Setup (Global):")
            self.labelOther.setText("Other Settings (Per Instance):")

        if G.master_instance and G.launched_instances:
            self.buttonChildSettings.setVisible(True)
            self.buttonChildSettings.setEnabled(True)
            self.buttonChildSettings.clicked.connect(self.launch_child_settings_windows)

        # Enabling start with windows should force headless mode for children
        self.cb_startToTray.clicked.connect(self.toggle_headless)
        self.cb_startToTray.clicked.connect(self.toggle_start_mode)
        self.cb_masterStartMin.clicked.connect(self.toggle_start_mode)


    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def accept(self):
        self.hide()

    def launch_child_settings_windows(self):
        G.main_window.show_child_settings()

    def reset_settings(self):
        # Load default settings and update widgets
        # default_settings = utils.get_default_sys_settings()
        self.load_settings(default=True)

    def toggle_headless(self, state):
        # force headless mode for children
        if state:
            self.cb_headless_j.setChecked(True)
            self.cb_headless_p.setChecked(True)
            self.cb_headless_c.setChecked(True)
            self.cb_min_enable_j.setChecked(False)
            self.cb_min_enable_p.setChecked(False)
            self.cb_min_enable_c.setChecked(False)

    def toggle_start_mode(self, state):
        """
        Toggle between 'start to tray' and 'start minimized
        """
        sender = self.sender()
        if not state: return  # only concerned with state changed to enable
        if sender == self.cb_startToTray:
            self.cb_masterStartMin.setChecked(False)
        elif sender == self.cb_masterStartMin:
            self.cb_startToTray.setChecked(False)


    def change_master_widgets(self, button):
        if button == self.rb_master_j:
            self.cb_al_enable_j.setChecked(False)
            self.cb_al_enable_j.setVisible(False)
            self.cb_min_enable_j.setChecked(False)
            self.cb_min_enable_j.setVisible(False)
            self.cb_headless_j.setChecked(False)
            self.cb_headless_j.setVisible(False)
            self.cb_al_enable_c.setVisible(True)
            self.cb_min_enable_c.setVisible(True)
            self.cb_headless_c.setVisible(True)
            self.cb_al_enable_p.setVisible(True)
            self.cb_min_enable_p.setVisible(True)
            self.cb_headless_p.setVisible(True)
        elif button == self.rb_master_p:
            self.cb_al_enable_p.setChecked(False)
            self.cb_al_enable_p.setVisible(False)
            self.cb_min_enable_p.setChecked(False)
            self.cb_min_enable_p.setVisible(False)
            self.cb_headless_p.setChecked(False)
            self.cb_headless_p.setVisible(False)
            self.cb_al_enable_c.setVisible(True)
            self.cb_min_enable_c.setVisible(True)
            self.cb_headless_c.setVisible(True)
            self.cb_al_enable_j.setVisible(True)
            self.cb_min_enable_j.setVisible(True)
            self.cb_headless_j.setVisible(True)
        elif button == self.rb_master_c:
            self.cb_al_enable_c.setChecked(False)
            self.cb_al_enable_c.setVisible(False)
            self.cb_min_enable_c.setChecked(False)
            self.cb_min_enable_c.setVisible(False)
            self.cb_headless_c.setChecked(False)
            self.cb_headless_c.setVisible(False)
            self.cb_al_enable_j.setVisible(True)
            self.cb_min_enable_j.setVisible(True)
            self.cb_headless_j.setVisible(True)
            self.cb_al_enable_p.setVisible(True)
            self.cb_min_enable_p.setVisible(True)
            self.cb_headless_p.setVisible(True)

    def toggle_vpconf_startup(self):
        vpconf_startup_enabled = self.enableVPConfStartup.isChecked()
        self.pathVPConfStartup.setEnabled(vpconf_startup_enabled)
        self.browseVPConfStartup.setEnabled(vpconf_startup_enabled)
        if not vpconf_startup_enabled:
            self.enableVPConfGlobalDefault.setChecked(False)
        self.enableVPConfGlobalDefault.setEnabled(vpconf_startup_enabled)

    def toggle_vpconf_exit(self):
        vpconf_exit_enabled = self.enableVPConfExit.isChecked()
        self.pathVPConfExit.setEnabled(vpconf_exit_enabled)
        self.browseVPConfExit.setEnabled(vpconf_exit_enabled)

    def toggle_al_widgets(self):
        al_enabled = self.cb_al_enable.isChecked()
        self.lab_auto_launch.setEnabled(al_enabled)
        self.lab_start_min.setEnabled(al_enabled)
        self.lab_start_headless.setEnabled(al_enabled)
        self.cb_al_enable_j.setEnabled(al_enabled)
        self.cb_al_enable_p.setEnabled(al_enabled)
        self.cb_al_enable_c.setEnabled(al_enabled)
        self.cb_min_enable_j.setEnabled(al_enabled)
        self.cb_min_enable_p.setEnabled(al_enabled)
        self.cb_min_enable_c.setEnabled(al_enabled)
        self.cb_headless_j.setEnabled(al_enabled)
        self.cb_headless_p.setEnabled(al_enabled)
        self.cb_headless_c.setEnabled(al_enabled)

    def toggle_xplane_widgets(self):
        xplane_enabled = self.enableXPLANE.isChecked()
        self.validateXPLANE.setEnabled(xplane_enabled)
        self.lab_pathXPLANE.setEnabled(xplane_enabled)
        self.pathXPLANE.setEnabled(xplane_enabled)
        self.browseXPLANE.setEnabled(xplane_enabled)

    def toggle_il2_widgets(self):
        # Show/hide IL-2 related widgets based on checkbox state
        il2_enabled = self.enableIL2.isChecked()
        # self.il2_sub_layout.setEnabled(il2_enabled)
        self.validateIL2.setEnabled(il2_enabled)
        self.lab_pathIL2.setEnabled(il2_enabled)
        self.pathIL2.setEnabled(il2_enabled)
        self.browseIL2.setEnabled(il2_enabled)
        self.lab_portIL2.setEnabled(il2_enabled)
        self.portIL2.setEnabled(il2_enabled)


    def toggle_log_prune_widgets(self):
        prune = self.cb_logPrune.isChecked()
        self.lab_logPrune.setEnabled(prune)
        self.tb_logPrune.setEnabled(prune)
        self.combo_logPrune.setEnabled(prune)

    def select_xplane_directory(self):
        # Open a directory dialog and set the result in the pathIL2 QLineEdit
        directory = QFileDialog.getExistingDirectory(self, "Select X-Plane Install Path", "")
        if directory:
            self.pathXPLANE.setText(directory)

    def select_il2_directory(self):
        # Open a directory dialog and set the result in the pathIL2 QLineEdit
        directory = QFileDialog.getExistingDirectory(self, "Select IL-2 Install Path", "")
        if directory:
            self.pathIL2.setText(directory)

    def toggle_launchmode_cbs(self):
        """
        Toggles "start minimized" and "start headless" checkboxes
        Disables "start to tray" if minimize is selected
        """
        sender = self.sender()
        if not sender.isChecked():
            return
        object_name = sender.objectName()
        match object_name:
            case 'headless_j':
                self.cb_min_enable_j.setChecked(False)
            case 'minimize_j':
                self.cb_headless_j.setChecked(False)
                self.cb_startToTray.setChecked(False)
            case 'headless_p':
                self.cb_min_enable_p.setChecked(False)
            case 'minimize_p':
                self.cb_headless_p.setChecked(False)
                self.cb_startToTray.setChecked(False)
            case 'headless_c':
                self.cb_min_enable_c.setChecked(False)
            case 'minimize_c':
                self.cb_headless_c.setChecked(False)
                self.cb_startToTray.setChecked(False)
        logging.debug(f"{sender.objectName()} checked:{sender.isChecked()}")

    def validate_settings(self):
        master = self.master_button_group.checkedId()
        match master:
            case 1:
                val_entry = self.tb_pid_j.text()
            case 2:
                val_entry = self.tb_pid_p.text()
            case 3:
                val_entry = self.tb_pid_c.text()
        if self.cb_al_enable.isChecked() and not (self.cb_al_enable_j.isChecked() or self.cb_al_enable_p.isChecked() or self.cb_al_enable_c.isChecked()):
            QMessageBox.warning(self, "Config Error", "Auto Launching is enabled but no devices are configured for auto launch.  Please enable a device or disable auto launching")
            return False
        if val_entry == '':
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the selected Master Instance')
            return False
        if self.cb_al_enable_c.isChecked() and self.tb_pid_c.text() == '':
            r = self.tb_pid_c.text()
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the collective device or disable auto-launch')
            return False
        if self.cb_al_enable_j.isChecked() and self.tb_pid_j.text() == '':
            r = self.tb_pid_j.text()
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the joystick device or disable auto-launch')
            return False
        if self.cb_al_enable_p.isChecked() and self.tb_pid_p.text() == '':
            r = self.tb_pid_p.text()
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the pedals device or disable auto-launch')
            return False
        if self.validateXPLANE.isChecked():
            pth = os.path.join(self.pathXPLANE.text(), 'resources')
            if not os.path.isdir(pth):
                QMessageBox.warning(self, "Config Error", 'Please enter the root X-Plane install path or disable auto X-plane setup')
                return False
        if self.enableVPConfStartup.isChecked():
            if not os.path.isfile(self.pathVPConfStartup.text()):
                QMessageBox.warning(self, "Config Error", "Please select a valid 'on Startup' VPforce Configurator file")
                return False
            if not validate_vpconf_profile(self.pathVPConfStartup.text(), G.device_usbpid, G.device_type):
                return False
        if self.enableVPConfExit.isChecked():
            if not os.path.isfile(self.pathVPConfExit.text()):
                QMessageBox.warning(self, "Config Error", "Please select a valid 'on Exit' VPforce Configurator file")
                return False
            if not validate_vpconf_profile(self.pathVPConfExit.text(), G.device_usbpid, G.device_type):
                return False
        return True

    def save_settings(self):
        # Create a dictionary with the values of all components
        tp = G.device_type

        if G.is_exe:
            G.main_window.toggle_start_with_windows(self.cb_startWithWindows.isChecked())

        global_settings_dict = {
            "enableDCS": self.enableDCS.isChecked(),
            "enableMSFS": self.enableMSFS.isChecked(),
            "enableXPLANE": self.enableXPLANE.isChecked(),
            "validateXPLANE": self.validateXPLANE.isChecked(),
            "pathXPLANE": self.pathXPLANE.text(),
            "enableIL2": self.enableIL2.isChecked(),
            "validateIL2": self.validateIL2.isChecked(),
            "pathIL2": self.pathIL2.text(),
            "portIL2": str(self.portIL2.text()),
            'masterInstance': self.master_button_group.checkedId(),
            'autolaunchMaster': self.cb_al_enable.isChecked(),
            'autolaunchJoystick': self.cb_al_enable_j.isChecked(),
            'autolaunchPedals': self.cb_al_enable_p.isChecked(),
            'autolaunchCollective': self.cb_al_enable_c.isChecked(),
            'startMinJoystick': self.cb_min_enable_j.isChecked(),
            'startMinPedals': self.cb_min_enable_p.isChecked(),
            'startMinCollective': self.cb_min_enable_c.isChecked(),
            'startHeadlessJoystick': self.cb_headless_j.isChecked(),
            'startHeadlessPedals': self.cb_headless_p.isChecked(),
            'startHeadlessCollective': self.cb_headless_c.isChecked(),
            'pidJoystick': str(self.tb_pid_j.text()),
            'pidPedals': str(self.tb_pid_p.text()),
            'pidCollective': str(self.tb_pid_c.text()),
            'pruneLogs': self.cb_logPrune.isChecked(),
            'pruneLogsNum': self.tb_logPrune.text(),
            'pruneLogsUnit': self.combo_logPrune.currentText(),
            'startToTray': self.cb_startToTray.isChecked(),
            'masterStartMin': self.cb_masterStartMin.isChecked(),
            'closeToTray': self.cb_closeToTray.isChecked()
        }

        instance_settings_dict = {
            "logLevel": self.logLevel.currentText(),
            "telemTimeout": str(self.telemTimeout.text()),
            "ignoreUpdate": self.ignoreUpdate.isChecked(),
            "saveWindow": self.cb_save_geometry.isChecked(),
            "saveLastTab": self.cb_save_view.isChecked(),
            "enableVPConfStartup": self.enableVPConfStartup.isChecked(),
            "pathVPConfStartup": self.pathVPConfStartup.text(),
            "enableVPConfExit": self.enableVPConfExit.isChecked(),
            "pathVPConfExit": self.pathVPConfExit.text(),
            "enableVPConfGlobalDefault": self.enableVPConfGlobalDefault.isChecked(),
            "enableResetGainsExit": self.enableResetGainsExit.isChecked(),
        }

        key_list = [
            'autolaunchMaster',
            'autolaunchJoystick',
            'autolaunchPedals',
            'autolaunchCollective',
            'startMinJoystick',
            'startMinPedals',
            'startMinCollective',
            'startHeadlessJoystick',
            'startHeadlessPedals',
            'startHeadlessCollective',
            'pidJoystick',
            'pidPedals',
            'pidCollective',
        ]
        saved_al_dict = {}
        for key in key_list:
            saved_al_dict[key] = global_settings_dict[key]

        if self.current_al_dict != saved_al_dict:
            QMessageBox.information(self, "Restart Required", "The Auto-Launch or Master Device settings have changed.  Please restart TelemFFB.")

        for k,v in global_settings_dict.items():
            G.system_settings.setValue(f"{k}", v)

        for k,v in instance_settings_dict.items():
            G.system_settings.setValue(f"{G.device_type}/{k}", v)

        if not self.validate_settings():
            return

        G.sim_listeners.restart_all()

        if G.master_instance and G.launched_instances:
            G.ipc_instance.send_broadcast_message("RESTART SIMS")

        # adjust logging level:
        ll = self.logLevel.currentText()
        if ll == "INFO":
            logging.getLogger().setLevel(logging.INFO)
        elif ll == "DEBUG":
            logging.getLogger().setLevel(logging.DEBUG)

        self.accept()

    def load_settings(self, default=False):
        """
        Load settings from the registry and update widget states.
        """
        if default:
            settings_dict = G.system_settings.defaults
            self.cb_save_geometry.setChecked(True)
            self.cb_save_view.setChecked(True)
        else:
            # Read settings from the registry
            settings_dict = G.system_settings
            pass
        # Update widget states based on the loaded settings
        self.logLevel.setCurrentText(settings_dict.get('logLevel', 'INFO'))

        self.telemTimeout.setText(str(settings_dict.get('telemTimeout', 200)))

        self.ignoreUpdate.setChecked(settings_dict.get('ignoreUpdate', False))

        self.cb_logPrune.setChecked(settings_dict.get('pruneLogs', False))

        self.tb_logPrune.setText(str(settings_dict.get("pruneLogsNum", 1)))

        self.combo_logPrune.setCurrentText(settings_dict.get("pruneLogsUnit", "Week(s)"))

        if G.is_exe:
            self.cb_startWithWindows.setChecked(G.main_window.toggle_start_with_windows())
        else:
            self.cb_startWithWindows.setChecked(False)
            self.cb_startWithWindows.setEnabled(False)
            self.cb_startWithWindows.setText('Start With Windows (EXE Version Only)')

        self.cb_startToTray.setChecked(settings_dict.get('startToTray', False))

        self.cb_masterStartMin.setChecked(settings_dict.get('masterStartMin', False))

        self.cb_closeToTray.setChecked(settings_dict.get('closeToTray', False))

        self.enableDCS.setChecked(settings_dict.get('enableDCS', False))

        self.enableMSFS.setChecked(settings_dict.get('enableMSFS', False))

        self.enableXPLANE.setChecked(settings_dict.get('enableXPLANE', False))
        self.toggle_xplane_widgets()

        self.validateXPLANE.setChecked(settings_dict.get('validateXPLANE', False))

        self.pathXPLANE.setText(settings_dict.get('pathXPLANE', ''))

        self.enableIL2.setChecked(settings_dict.get('enableIL2', False))
        self.toggle_il2_widgets()

        self.validateIL2.setChecked(settings_dict.get('validateIL2', True))

        self.pathIL2.setText(settings_dict.get('pathIL2', 'C:/Program Files/IL-2 Sturmovik Great Battles'))

        self.portIL2.setText(str(settings_dict.get('portIL2', 34385)))

        self.cb_save_geometry.setChecked(settings_dict.get('saveWindow', True))

        self.cb_save_view.setChecked(settings_dict.get('saveLastTab', True))

        self.tb_pid_j.setText(str(settings_dict.get('pidJoystick', '2055')))

        self.tb_pid_p.setText(str(settings_dict.get('pidPedals', '')))

        self.tb_pid_c.setText(str(settings_dict.get('pidCollective', '')))

        self.cb_al_enable.setChecked(settings_dict.get('autolaunchMaster', False))

        self.cb_al_enable_j.setChecked(settings_dict.get('autolaunchJoystick', False))
        self.cb_al_enable_p.setChecked(settings_dict.get('autolaunchPedals', False))
        self.cb_al_enable_c.setChecked(settings_dict.get('autolaunchCollective', False))

        self.cb_min_enable_j.setChecked(settings_dict.get('startMinJoystick', False))
        self.cb_min_enable_p.setChecked(settings_dict.get('startMinPedals', False))
        self.cb_min_enable_c.setChecked(settings_dict.get('startMinCollective', False))

        self.cb_headless_j.setChecked(settings_dict.get('startHeadlessJoystick', False))
        self.cb_headless_p.setChecked(settings_dict.get('startHeadlessPedals', False))
        self.cb_headless_c.setChecked(settings_dict.get('startHeadlessCollective', False))

        self.master_button_group.button(settings_dict.get('masterInstance', 1)).setChecked(True)
        self.master_button_group.button(settings_dict.get('masterInstance', 1)).click()

        self.enableVPConfStartup.setChecked(settings_dict.get('enableVPConfStartup', False))
        self.pathVPConfStartup.setText(settings_dict.get('pathVPConfStartup', ''))
        self.enableVPConfExit.setChecked(settings_dict.get('enableVPConfExit', False))
        self.pathVPConfExit.setText(settings_dict.get('pathVPConfExit', ''))
        self.enableVPConfGlobalDefault.setChecked(settings_dict.get('enableVPConfGlobalDefault', False))
        self.enableResetGainsExit.setChecked(settings_dict.get('enableResetGainsExit', False))


        self.toggle_al_widgets()

        # build record of auto-launch settings to see if they changed on save:
        self.current_al_dict = {
            'autolaunchMaster': self.cb_al_enable.isChecked(),
            'autolaunchJoystick': self.cb_al_enable_j.isChecked(),
            'autolaunchPedals': self.cb_al_enable_p.isChecked(),
            'autolaunchCollective': self.cb_al_enable_c.isChecked(),
            'startMinJoystick': self.cb_min_enable_j.isChecked(),
            'startMinPedals': self.cb_min_enable_p.isChecked(),
            'startMinCollective': self.cb_min_enable_c.isChecked(),
            'startHeadlessJoystick': self.cb_headless_j.isChecked(),
            'startHeadlessPedals': self.cb_headless_p.isChecked(),
            'startHeadlessCollective': self.cb_headless_c.isChecked(),
            'pidJoystick': str(self.tb_pid_j.text()),
            'pidPedals': str(self.tb_pid_p.text()),
            'pidCollective': str(self.tb_pid_c.text()),
        }

    def browse_vpconf(self, mode):
        options = QFileDialog.Options()
        # options |= QFileDialog.DontUseNativeDialog
        calling_button = self.sender()
        starting_dir = os.getcwd()
        if mode == 'startup':
            lbl = self.pathVPConfStartup
        elif mode == 'exit':
            lbl = self.pathVPConfExit
        else:
            return

        cur_path = lbl.text()
        if os.path.exists(cur_path):
            starting_dir = os.path.dirname(cur_path)

        # Open the file browser dialog
        file_path, _ = QFileDialog.getOpenFileName(self, f"Choose {mode} vpconf profile for {G.device_type} ", starting_dir, "vpconf Files (*.vpconf)", options=options)

        if file_path:
            if validate_vpconf_profile(file_path, G.device_usbpid, G.device_type):
                lbl.setText(file_path)