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
import os

from PyQt6 import QtCore
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIntValidator, QIcon, QPixmap
from PyQt6.QtWidgets import QButtonGroup, QDialog, QFileDialog, QMessageBox, QSizePolicy, QStyleOption

from . import globals as G
from . import utils
from .ui.Ui_SystemDialog import Ui_SystemDialog
from .utils import validate_vpconf_profile, HiDpiPixmap


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
        self.master_button_group.addButton(self.rb_master_t, id=4)

        # Add tooltips
        self.validateDCS.setToolTip('If enabled, TelemFFB will automatically install the necessary export script and update the DCS export.lua file')
        self.validateIL2.setToolTip('If enabled, TelemFFB will automatically set up the required configuration in IL2 to support telemetry export')
        self.focus_pauseIL2.setToolTip('When enabled, TelemFFB will enter a pause state when focus is lost on the IL2 game window. (Enabled by default)\n\nNote: While disabling can aid in adjusting effects in real time, when the IL2 window loses focus, it also loses all inputs.\nThis may result in odd behavior and stuck effects while the window is out of focus.')
        self.pathIL2.setToolTip('The root path where IL-2 Strumovik is installed')
        self.lab_pathIL2.setToolTip('The root path where IL-2 Strumovik is installed')
        self.validateXPLANE.setToolTip('If enabled, TelemFFB will automatically install the required X-Plane plugin and keep it up to date when it changes')
        self.lab_pathXPLANE.setToolTip('The root path where X-Plane is installed')
        self.pathXPLANE.setToolTip('The root path where X-Plane is installed')
        self.enableVPConfStartup.setToolTip('Select VPforce Configurator profile to load when TelemFFB Starts')
        self.enableVPConfExit.setToolTip('Select VPforce Configurator profile to load when TelemFFB Exits')
        self.cb_logPrune.setToolTip('Auto delete archived logs after time frame')

        self.lb_configProfile.setText(f'<b><u>Configurator Profile Options ({G.device_type.capitalize()})</u></b>')

        style = self.style()  # Grab the current style engine

        # DCS
        DCS_PIXMAP = HiDpiPixmap(':/image/icon_DCS.png')
        self.DCS_ICON_ENABLED, self.DCS_ICON_DISABLED = self.make_icons(DCS_PIXMAP, style)
        self.DCS_TAB = self.simTabWidget.indexOf(self.tab_DCS)
        self.simTabWidget.setTabText(self.DCS_TAB, "")
        self.simTabWidget.setTabIcon(self.DCS_TAB, self.DCS_ICON_DISABLED)

        # MSFS
        MSFS_PIXMAP = HiDpiPixmap(':/image/icon_MSFS.png')
        self.MSFS_ICON_ENABLED, self.MSFS_ICON_DISABLED = self.make_icons(MSFS_PIXMAP, style)
        self.MSFS_TAB = self.simTabWidget.indexOf(self.tab_MSFS)
        self.simTabWidget.setTabText(self.MSFS_TAB, "")
        self.simTabWidget.setTabIcon(self.MSFS_TAB, self.MSFS_ICON_DISABLED)

        # XPLANE
        XPLANE_PIXMAP = HiDpiPixmap(':/image/icon_XPLANE.png')
        self.XPLANE_ICON_ENABLED, self.XPLANE_ICON_DISABLED = self.make_icons(XPLANE_PIXMAP, style)
        self.XPLANE_TAB = self.simTabWidget.indexOf(self.tab_XPLANE)
        self.simTabWidget.setTabText(self.XPLANE_TAB, "")
        self.simTabWidget.setTabIcon(self.XPLANE_TAB, self.XPLANE_ICON_DISABLED)

        # IL2
        IL2_PIXMAP = HiDpiPixmap(':/image/icon_IL2.png')
        self.IL2_ICON_ENABLED, self.IL2_ICON_DISABLED = self.make_icons(IL2_PIXMAP, style)
        self.IL2_TAB = self.simTabWidget.indexOf(self.tab_IL2)
        self.simTabWidget.setTabText(self.IL2_TAB, "")
        self.simTabWidget.setTabIcon(self.IL2_TAB, self.IL2_ICON_DISABLED)

        # BMS
        BMS_PIXMAP = HiDpiPixmap(':/image/icon_BMS.png') if G.useDarkMode else HiDpiPixmap(':/image/icon_BMS_lm.png')
        self.BMS_ICON_ENABLED, self.BMS_ICON_DISABLED = self.make_icons(BMS_PIXMAP, style)
        self.BMS_TAB = self.simTabWidget.indexOf(self.tab_BMS)
        self.simTabWidget.setTabText(self.BMS_TAB, "")
        self.simTabWidget.setTabIcon(self.BMS_TAB, self.BMS_ICON_DISABLED)

        # Optional: set uniform icon size once
        self.simTabWidget.setIconSize(QSize(48, 48))


        tab_format = """
            QTabWidget::pane {
                border: 1px solid transparent;  /* Use theme color */
                padding: 0px;
                margin: 0px;
            }

            QTabBar::tab {
                background: palette(window);      /* Use default theme bg */
                border: 1px solid palette(midlight);
                border-radius: 10px;
                margin: 4px;
                padding: 4px;
                width: 48px;
                height: 48px;
            }

            QTabBar::tab:hover {
                border: 2px solid palette(midlight);
                border-radius: 10px;
            }

            QTabBar::tab:selected {
                border: 3px solid palette(highlight);
                background-color: palette(button);
                border-radius: 10px;
            }
        """
        self.simTabWidget.setStyleSheet(tab_format)

        main_tab_format = """
            QTabWidget::pane {
                border: 1px solid palette(mid);  /* Use theme color */
                padding: 0px;
                margin: 0px;
            }
        """
        self.tabWidget.setStyleSheet(main_tab_format)

        self.tabWidget.setCurrentIndex(0)
        self.simTabWidget.setCurrentIndex(0)

        # Connect signals to slots
        self.cb_logPrune.stateChanged.connect(self.toggle_log_prune_widgets)
        self.enableDCS.stateChanged.connect(self.toggle_dcs_widgets)
        self.enableIL2.stateChanged.connect(self.toggle_il2_widgets)
        self.enableMSFS.stateChanged.connect(self.toggle_msfs_widgets)
        self.enableXPLANE.stateChanged.connect(self.toggle_xplane_widgets)
        self.enableBMS.stateChanged.connect(self.toggle_bms_widgets)
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
        for button in self.buttonBox.buttons():
            button.setMinimumWidth(60)

        self.buttonChildSettings.setEnabled(False)
        self.buttonChildSettings.setVisible(False)

        # Set initial state
        self.toggle_log_prune_widgets()
        self.toggle_dcs_widgets()
        self.toggle_il2_widgets()
        self.toggle_xplane_widgets()
        self.toggle_msfs_widgets()
        self.toggle_al_widgets()
        self.parent_window = parent
        # Load settings from the registry and update widget states
        self.current_al_dict = {}

        # only allow dark mode if debug menu visible
        # self.useDarkmode.setVisible(G.system_settings.get('debug', False))
        self.themeButtonGroup.setId(self.rb_LightTheme, 0)
        self.themeButtonGroup.setId(self.rb_DarkTheme, 1)
        self.themeButtonGroup.setId(self.rb_SystemTheme, 2)

        self._setup_shaker_tab()

        self.load_settings()

        int_validator = QIntValidator()
        self.tb_logPrune.setValidator(int_validator)
        self.telemTimeout.setValidator(int_validator)
        self.tb_pid_j.setValidator(int_validator)
        self.tb_pid_p.setValidator(int_validator)
        self.tb_pid_c.setValidator(int_validator)
        self.tb_pid_t.setValidator(int_validator)
        self.tb_pid_s.setValidator(int_validator)
        self.tb_pid_s.setToolTip("Synthetic ID for the shaker child instance — not a real USB device. "
                                 "Just needs to be unique vs. other configured PIDs.")

        self.cb_min_enable_j.setObjectName('minimize_j')
        self.cb_min_enable_j.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_p.setObjectName('minimize_p')
        self.cb_min_enable_p.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_c.setObjectName('minimize_c')
        self.cb_min_enable_c.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_t.setObjectName('minimize_t')
        self.cb_min_enable_t.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_s.setObjectName('minimize_s')
        self.cb_min_enable_s.clicked.connect(self.toggle_launchmode_cbs)

        self.cb_headless_j.setObjectName('headless_j')
        self.cb_headless_j.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_p.setObjectName('headless_p')
        self.cb_headless_p.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_c.setObjectName('headless_c')
        self.cb_headless_c.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_t.setObjectName('headless_t')
        self.cb_headless_t.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_s.setObjectName('headless_s')
        self.cb_headless_s.clicked.connect(self.toggle_launchmode_cbs)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)




        if (G.master_instance and G.launched_instances) or G.child_instance:
            self.labelSystem.setText("System (Per Instance):")
            self.labelLaunch.setText("Launch Options (Global):")
            # self.labelSim.setText("Sim Setup (Global):")
            # self.labelOther.setText("Other Settings (Per Instance):")

        if G.master_instance and G.launched_instances:
            self.buttonChildSettings.setVisible(True)
            self.buttonChildSettings.setEnabled(True)
            self.buttonChildSettings.clicked.connect(self.launch_child_settings_windows)

        if G.child_instance:
            simtab = self.tabWidget.indexOf(self.tab_Simulators)
            self.tabWidget.setTabVisible(simtab, False)
            self.tab_Simulators.setVisible(False)
            self.ignoreUpdate.setVisible(False)
            self.cb_startWithWindows.setVisible(False)
            self.cb_startToTray.setVisible(False)
            self.cb_masterStartMin.setVisible(False)
            self.cb_closeToTray.setVisible(False)
            sp1 = self.themeOptions.sizePolicy()
            sp1.setRetainSizeWhenHidden(False)
            self.themeOptions.setSizePolicy(sp1)
            self.themeOptions.setVisible(False)

            sp2 = self.masterLaunchOptions.sizePolicy()
            sp2.setRetainSizeWhenHidden(False)
            self.masterLaunchOptions.setSizePolicy(sp2)
            self.masterLaunchOptions.setVisible(False)

        # Enabling start with windows should force headless mode for children
        self.cb_startToTray.clicked.connect(self.toggle_headless)
        self.cb_startToTray.clicked.connect(self.toggle_start_mode)
        self.cb_masterStartMin.clicked.connect(self.toggle_start_mode)

        self.simTabWidget.tabBar().setExpanding(False)
        self.simTabWidget.tabBar().setUsesScrollButtons(False)
        self.simTabWidget.tabBar().setDocumentMode(True)

        self.select_enabled_sim()

    def select_enabled_sim(self):
        for sim in ('DCS', 'MSFS', 'XPLANE', 'IL2', 'BMS'):
            cb = getattr(self, f'enable{sim}')
            if cb.isChecked():
                # Find first enabled sim in the list and make that the default selected tab
                tab_index = getattr(self, f'{sim}_TAB')
                self.simTabWidget.setCurrentIndex(tab_index)
                return

    _SHAKER_TEST_BUTTON_TEXT = "Test (2 s, 35 Hz @ 0.5)"

    def _setup_shaker_tab(self):
        """Add a top-level Shaker tab with output-device / gain / test controls.

        Built programmatically so the Qt-Designer-generated Ui_SystemDialog.py
        does not need to be touched. If the audio backend can't be imported
        (e.g. PortAudio not present), the tab still opens with an explanation
        and disabled controls.
        """
        from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                                       QLabel, QComboBox, QDoubleSpinBox, QPushButton)

        self.tab_Shaker = QWidget()
        self.tab_Shaker.setObjectName("tab_Shaker")
        outer = QVBoxLayout(self.tab_Shaker)

        intro = QLabel(
            "Bass shaker output. These settings only take effect when a TelemFFB "
            "instance is launched as <b>--type shaker</b>. After changing the output "
            "device, restart the shaker child instance.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(form)

        # Output device combobox.
        self.shaker_device_combo = QComboBox()
        self.shaker_device_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.shaker_device_combo.addItem("(System default)", userData="")
        self._shaker_backend_available = True
        try:
            from telemffb.hw.shaker_synth import ShakerSynth
            for d in ShakerSynth.list_output_devices():
                self.shaker_device_combo.addItem(
                    f"{d['index']}: {d['name']} ({d['samplerate']:.0f} Hz)",
                    userData=d['name'])
        except Exception:
            logging.exception("Failed to enumerate audio output devices for shaker")
            self._shaker_backend_available = False
            self.shaker_device_combo.setEnabled(False)
        form.addRow("Output device:", self.shaker_device_combo)

        # Master gain spinbox.
        self.shaker_gain_spin = QDoubleSpinBox()
        self.shaker_gain_spin.setRange(0.0, 2.0)
        self.shaker_gain_spin.setSingleStep(0.05)
        self.shaker_gain_spin.setDecimals(2)
        self.shaker_gain_spin.setValue(1.0)
        form.addRow("Master gain:", self.shaker_gain_spin)

        # Test button.
        self.shaker_test_button = QPushButton(self._SHAKER_TEST_BUTTON_TEXT)
        self.shaker_test_button.clicked.connect(self._shaker_test_clicked)
        if not self._shaker_backend_available:
            self.shaker_test_button.setEnabled(False)
            self.shaker_test_button.setToolTip(
                "Audio backend (sounddevice / PortAudio) is unavailable.")
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.shaker_test_button)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        outer.addStretch(1)

        self.tabWidget.addTab(self.tab_Shaker, "Shaker")

    def _shaker_test_clicked(self):
        """Play a short test tone on the currently-selected device.

        Uses a short-lived ``ShakerSynth`` on a daemon thread so the UI does
        not block. The button is re-enabled via QTimer.singleShot from the
        main (Qt) thread once the worker is expected to be done.
        """
        import threading
        import time
        from PyQt6.QtCore import QTimer

        device_name = self.shaker_device_combo.currentData() or None
        gain = self.shaker_gain_spin.value()
        self.shaker_test_button.setEnabled(False)
        self.shaker_test_button.setText("Testing…")

        def _run():
            try:
                from telemffb.hw.shaker_synth import ShakerSynth
                synth = ShakerSynth(device=device_name, master_gain=gain)
                synth.start()
                try:
                    osc = synth.get_oscillator("test")
                    osc.set(35.0, 0.5, ramp_ms=100)
                    time.sleep(2.0)
                    osc.stop(ramp_ms=100)
                    time.sleep(0.2)
                finally:
                    synth.stop()
            except Exception:
                logging.exception("Shaker test failed")

        threading.Thread(target=_run, daemon=True).start()
        QTimer.singleShot(2500, self._shaker_test_finished)

    def _shaker_test_finished(self):
        self.shaker_test_button.setEnabled(self._shaker_backend_available)
        self.shaker_test_button.setText(self._SHAKER_TEST_BUTTON_TEXT)

    def _load_shaker_settings(self, settings_dict):
        """Restore the Shaker tab from persisted settings.

        Device match is exact-name first then case-insensitive substring (the
        same fallback policy used by ``ShakerSynth._resolve_device``). If the
        saved device is no longer present, default to "(System default)" at
        index 0; the actual fallback warning is logged at runtime by the
        shaker child, not here.
        """
        saved_device = (settings_dict.get('shakerDevice', '') or '').strip()
        idx = 0  # "(System default)"
        if saved_device:
            saved_lc = saved_device.lower()
            exact = None
            substring = None
            for i in range(self.shaker_device_combo.count()):
                data = self.shaker_device_combo.itemData(i)
                if not data:
                    continue
                data_lc = str(data).lower()
                if data_lc == saved_lc and exact is None:
                    exact = i
                elif saved_lc in data_lc and substring is None:
                    substring = i
            if exact is not None:
                idx = exact
            elif substring is not None:
                idx = substring
        self.shaker_device_combo.setCurrentIndex(idx)

        try:
            gain = float(settings_dict.get('shakerGain', 1.0))
        except (TypeError, ValueError):
            gain = 1.0
        self.shaker_gain_spin.setValue(gain)

    def make_icons(self, pixmap, style):
        icon_enabled = QIcon()
        icon_enabled.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.On)

        icon_disabled = QIcon()
        disabled_pixmap = style.generatedIconPixmap(QIcon.Mode.Disabled, pixmap, QStyleOption())
        icon_disabled.addPixmap(disabled_pixmap, QIcon.Mode.Normal, QIcon.State.On)

        return icon_enabled, icon_disabled

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
            self.cb_headless_t.setChecked(True)
            self.cb_min_enable_j.setChecked(False)
            self.cb_min_enable_p.setChecked(False)
            self.cb_min_enable_c.setChecked(False)
            self.cb_min_enable_t.setChecked(False)

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
        # Shaker row is never the master — its checkboxes stay visible regardless.
        self.cb_al_enable_s.setVisible(True)
        self.cb_min_enable_s.setVisible(True)
        self.cb_headless_s.setVisible(True)
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
            self.cb_al_enable_t.setVisible(True)
            self.cb_min_enable_t.setVisible(True)
            self.cb_headless_t.setVisible(True)
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
            self.cb_al_enable_t.setVisible(True)
            self.cb_min_enable_t.setVisible(True)
            self.cb_headless_t.setVisible(True)
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
            self.cb_al_enable_t.setVisible(True)
            self.cb_min_enable_t.setVisible(True)
            self.cb_headless_t.setVisible(True)
        elif button == self.rb_master_t:
            self.cb_al_enable_t.setChecked(False)
            self.cb_al_enable_t.setVisible(False)
            self.cb_min_enable_t.setChecked(False)
            self.cb_min_enable_t.setVisible(False)
            self.cb_headless_c.setChecked(False)
            self.cb_headless_c.setVisible(False)
            self.cb_al_enable_j.setVisible(True)
            self.cb_min_enable_j.setVisible(True)
            self.cb_headless_j.setVisible(True)
            self.cb_al_enable_p.setVisible(True)
            self.cb_min_enable_p.setVisible(True)
            self.cb_headless_p.setVisible(True)
            self.cb_al_enable_c.setVisible(True)
            self.cb_min_enable_c.setVisible(True)
            self.cb_headless_c.setVisible(True)

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
        self.cb_al_enable_t.setEnabled(al_enabled)
        self.cb_al_enable_s.setEnabled(al_enabled)
        self.cb_min_enable_j.setEnabled(al_enabled)
        self.cb_min_enable_p.setEnabled(al_enabled)
        self.cb_min_enable_c.setEnabled(al_enabled)
        self.cb_min_enable_t.setEnabled(al_enabled)
        self.cb_min_enable_s.setEnabled(al_enabled)
        self.cb_headless_j.setEnabled(al_enabled)
        self.cb_headless_p.setEnabled(al_enabled)
        self.cb_headless_c.setEnabled(al_enabled)
        self.cb_headless_t.setEnabled(al_enabled)
        self.cb_headless_s.setEnabled(al_enabled)

    def toggle_msfs_widgets(self):
        msfs_enabled = self.enableMSFS.isChecked()
        icon = self.MSFS_ICON_ENABLED if msfs_enabled else self.MSFS_ICON_DISABLED
        self.simTabWidget.setTabIcon(self.MSFS_TAB, icon)

    def toggle_xplane_widgets(self):
        xplane_enabled = self.enableXPLANE.isChecked()
        self.validateXPLANE.setEnabled(xplane_enabled)
        self.lab_pathXPLANE.setEnabled(xplane_enabled)
        self.pathXPLANE.setEnabled(xplane_enabled)
        self.browseXPLANE.setEnabled(xplane_enabled)
        icon = self.XPLANE_ICON_ENABLED if xplane_enabled else self.XPLANE_ICON_DISABLED
        self.simTabWidget.setTabIcon(self.XPLANE_TAB, icon)

    def toggle_dcs_widgets(self):
        # show/hide DCS related widgets based on checkbox state
        dcs_enabled = self.enableDCS.isChecked()
        self.validateDCS.setEnabled(dcs_enabled)
        icon = self.DCS_ICON_ENABLED if dcs_enabled else self.DCS_ICON_DISABLED
        self.simTabWidget.setTabIcon(self.DCS_TAB, icon)

    def toggle_il2_widgets(self):
        # Show/hide IL-2 related widgets based on checkbox state
        il2_enabled = self.enableIL2.isChecked()
        # self.il2_sub_layout.setEnabled(il2_enabled)
        self.validateIL2.setEnabled(il2_enabled)
        self.focus_pauseIL2.setEnabled(il2_enabled)
        self.lab_pathIL2.setEnabled(il2_enabled)
        self.pathIL2.setEnabled(il2_enabled)
        self.browseIL2.setEnabled(il2_enabled)
        self.lab_portIL2.setEnabled(il2_enabled)
        self.portIL2.setEnabled(il2_enabled)
        icon = self.IL2_ICON_ENABLED if il2_enabled else self.IL2_ICON_DISABLED
        self.simTabWidget.setTabIcon(self.IL2_TAB, icon)

    def toggle_bms_widgets(self):
        bms_enabled = self.enableBMS.isChecked()
        icon = self.BMS_ICON_ENABLED if bms_enabled else self.BMS_ICON_DISABLED
        self.simTabWidget.setTabIcon(self.BMS_TAB, icon)

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
            case 'headless_t':
                self.cb_min_enable_t.setChecked(False)
            case 'minimize_t':
                self.cb_headless_t.setChecked(False)
                self.cb_startToTray.setChecked(False)
            case 'headless_s':
                self.cb_min_enable_s.setChecked(False)
            case 'minimize_s':
                self.cb_headless_s.setChecked(False)
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
            case 4:
                val_entry = self.tb_pid_t.text()
        if self.cb_al_enable.isChecked() and not (self.cb_al_enable_j.isChecked() or self.cb_al_enable_p.isChecked() or self.cb_al_enable_c.isChecked()  or self.cb_al_enable_t.isChecked() or self.cb_al_enable_s.isChecked()):
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
        if self.cb_al_enable_t.isChecked() and self.tb_pid_t.text() == '':
            r = self.tb_pid_t.text()
            QMessageBox.warning(self, "Config Error", 'Please enter a valid USB Product ID for the trim wheel device or disable auto-launch')
            return False
        if self.cb_al_enable_s.isChecked() and self.tb_pid_s.text() == '':
            QMessageBox.warning(self, "Config Error", 'Please enter a Product ID for the shaker instance or disable auto-launch (any unique number works — it is not a real USB device)')
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
            "validateDCS": self.validateDCS.isChecked(),
            "enableMSFS": self.enableMSFS.isChecked(),
            "enableXPLANE": self.enableXPLANE.isChecked(),
            "validateXPLANE": self.validateXPLANE.isChecked(),
            "pathXPLANE": self.pathXPLANE.text(),
            "enableIL2": self.enableIL2.isChecked(),
            "validateIL2": self.validateIL2.isChecked(),
            "focus_pauseIL2": self.focus_pauseIL2.isChecked(),
            "pathIL2": self.pathIL2.text(),
            "portIL2": str(self.portIL2.text()),
            'enableBMS': self.enableBMS.isChecked(),
            'masterInstance': self.master_button_group.checkedId(),
            'autolaunchMaster': self.cb_al_enable.isChecked(),
            'autolaunchJoystick': self.cb_al_enable_j.isChecked(),
            'autolaunchPedals': self.cb_al_enable_p.isChecked(),
            'autolaunchCollective': self.cb_al_enable_c.isChecked(),
            'autolaunchTrimWheel': self.cb_al_enable_t.isChecked(),
            'autolaunchShaker': self.cb_al_enable_s.isChecked(),
            'startMinJoystick': self.cb_min_enable_j.isChecked(),
            'startMinPedals': self.cb_min_enable_p.isChecked(),
            'startMinCollective': self.cb_min_enable_c.isChecked(),
            'startMinTrimWheel': self.cb_min_enable_t.isChecked(),
            'startMinShaker': self.cb_min_enable_s.isChecked(),
            'startHeadlessJoystick': self.cb_headless_j.isChecked(),
            'startHeadlessPedals': self.cb_headless_p.isChecked(),
            'startHeadlessCollective': self.cb_headless_c.isChecked(),
            'startHeadlessTrimWheel': self.cb_headless_t.isChecked(),
            'startHeadlessShaker': self.cb_headless_s.isChecked(),
            'pidJoystick': str(self.tb_pid_j.text()),
            'pidPedals': str(self.tb_pid_p.text()),
            'pidCollective': str(self.tb_pid_c.text()),
            'pidTrimWheel': str(self.tb_pid_t.text()),
            'pidShaker': str(self.tb_pid_s.text()),
            'pruneLogs': self.cb_logPrune.isChecked(),
            'pruneLogsNum': self.tb_logPrune.text(),
            'pruneLogsUnit': self.combo_logPrune.currentText(),
            'startToTray': self.cb_startToTray.isChecked(),
            'masterStartMin': self.cb_masterStartMin.isChecked(),
            'closeToTray': self.cb_closeToTray.isChecked(),
            'themeId': self.themeButtonGroup.checkedId(),
            'shakerDevice': self.shaker_device_combo.currentData() or '',
            'shakerGain': float(self.shaker_gain_spin.value()),
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
            'autolaunchTrimWheel',
            'autolaunchShaker',
            'startMinJoystick',
            'startMinPedals',
            'startMinCollective',
            'startMinTrimWheel',
            'startMinShaker',
            'startHeadlessJoystick',
            'startHeadlessPedals',
            'startHeadlessCollective',
            'startHeadlessTrimWheel',
            'startHeadlessShaker',
            'pidJoystick',
            'pidPedals',
            'pidCollective',
            'pidTrimWheel',
            'pidShaker',
            'themeId'
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

        themeID = settings_dict.get('themeId', 2)

        self.themeButtonGroup.button(themeID).setChecked(True)


        # self.useDarkmode.setChecked(settings_dict.get('useDarkmode', False))
        #
        # self.useWindowsTheme.setChecked(settings_dict.get('useWindowsTheme', False))

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
        self.toggle_dcs_widgets()

        self.validateDCS.setChecked(settings_dict.get('validateDCS', True))

        self.enableMSFS.setChecked(settings_dict.get('enableMSFS', False))
        self.toggle_msfs_widgets()

        self.enableXPLANE.setChecked(settings_dict.get('enableXPLANE', False))
        self.toggle_xplane_widgets()

        self.validateXPLANE.setChecked(settings_dict.get('validateXPLANE', False))

        self.pathXPLANE.setText(settings_dict.get('pathXPLANE', ''))

        self.enableIL2.setChecked(settings_dict.get('enableIL2', False))
        self.toggle_il2_widgets()

        self.validateIL2.setChecked(settings_dict.get('validateIL2', True))

        self.focus_pauseIL2.setChecked(settings_dict.get('focus_pauseIL2', True))

        self.pathIL2.setText(settings_dict.get('pathIL2', 'C:/Program Files/IL-2 Sturmovik Great Battles'))

        self.portIL2.setText(str(settings_dict.get('portIL2', 34385)))

        self.enableBMS.setChecked(settings_dict.get('enableBMS', False))
        self.toggle_bms_widgets()

        self.cb_save_geometry.setChecked(settings_dict.get('saveWindow', True))

        self.cb_save_view.setChecked(settings_dict.get('saveLastTab', True))

        self.tb_pid_j.setText(str(settings_dict.get('pidJoystick', '2055')))

        self.tb_pid_p.setText(str(settings_dict.get('pidPedals', '')))

        self.tb_pid_c.setText(str(settings_dict.get('pidCollective', '')))

        self.tb_pid_t.setText(str(settings_dict.get('pidTrimWheel', '')))

        self.tb_pid_s.setText(str(settings_dict.get('pidShaker', '2059')))

        self.cb_al_enable.setChecked(settings_dict.get('autolaunchMaster', False))

        self.cb_al_enable_j.setChecked(settings_dict.get('autolaunchJoystick', False))
        self.cb_al_enable_p.setChecked(settings_dict.get('autolaunchPedals', False))
        self.cb_al_enable_c.setChecked(settings_dict.get('autolaunchCollective', False))
        self.cb_al_enable_t.setChecked(settings_dict.get('autolaunchTrimWheel', False))
        self.cb_al_enable_s.setChecked(settings_dict.get('autolaunchShaker', False))

        self.cb_min_enable_j.setChecked(settings_dict.get('startMinJoystick', False))
        self.cb_min_enable_p.setChecked(settings_dict.get('startMinPedals', False))
        self.cb_min_enable_c.setChecked(settings_dict.get('startMinCollective', False))
        self.cb_min_enable_t.setChecked(settings_dict.get('startMinTrimWheel', False))
        self.cb_min_enable_s.setChecked(settings_dict.get('startMinShaker', False))

        self.cb_headless_j.setChecked(settings_dict.get('startHeadlessJoystick', False))
        self.cb_headless_p.setChecked(settings_dict.get('startHeadlessPedals', False))
        self.cb_headless_c.setChecked(settings_dict.get('startHeadlessCollective', False))
        self.cb_headless_t.setChecked(settings_dict.get('startHeadlessTrimWheel', False))
        self.cb_headless_s.setChecked(settings_dict.get('startHeadlessShaker', False))

        self.master_button_group.button(settings_dict.get('masterInstance', 1)).setChecked(True)
        self.master_button_group.button(settings_dict.get('masterInstance', 1)).click()

        self._load_shaker_settings(settings_dict)

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
            'autolaunchTrimWheel': self.cb_al_enable_t.isChecked(),
            'autolaunchShaker': self.cb_al_enable_s.isChecked(),
            'startMinJoystick': self.cb_min_enable_j.isChecked(),
            'startMinPedals': self.cb_min_enable_p.isChecked(),
            'startMinCollective': self.cb_min_enable_c.isChecked(),
            'startMinTrimWheel': self.cb_min_enable_t.isChecked(),
            'startMinShaker': self.cb_min_enable_s.isChecked(),
            'startHeadlessJoystick': self.cb_headless_j.isChecked(),
            'startHeadlessPedals': self.cb_headless_p.isChecked(),
            'startHeadlessCollective': self.cb_headless_c.isChecked(),
            'startHeadlessTrimWheel': self.cb_headless_t.isChecked(),
            'startHeadlessShaker': self.cb_headless_s.isChecked(),
            'pidJoystick': str(self.tb_pid_j.text()),
            'pidPedals': str(self.tb_pid_p.text()),
            'pidCollective': str(self.tb_pid_c.text()),
            'pidTrimWheel': str(self.tb_pid_t.text()),
            'pidShaker': str(self.tb_pid_s.text()),
            'themeId': self.themeButtonGroup.checkedId(),
        }

    def browse_vpconf(self, mode):
        options = QFileDialog.Option(0)
        # options |= QFileDialog.Option.DontUseNativeDialog
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