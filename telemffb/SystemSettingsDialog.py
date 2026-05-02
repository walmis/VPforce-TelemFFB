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
                                       QLabel, QComboBox, QDoubleSpinBox, QPushButton,
                                       QSlider)

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

        # Output channel routing.
        self.shaker_channel_combo = QComboBox()
        self.shaker_channel_combo.addItem("Mono",            userData="mono")
        self.shaker_channel_combo.addItem("Left only",       userData="left")
        self.shaker_channel_combo.addItem("Right only",      userData="right")
        self.shaker_channel_combo.addItem("Stereo (pan)",    userData="pan")
        self.shaker_channel_combo.setToolTip(
            "Routes the shaker mono signal to a specific stereo output channel. "
            "Useful when the shaker amplifier sits behind the pilot while the "
            "stick is in front. Restart the shaker child instance after changing.")
        form.addRow("Output channel:", self.shaker_channel_combo)

        # Pan slider (-1 left, +1 right) with live label.
        self.shaker_pan_slider = QSlider(Qt.Orientation.Horizontal)
        self.shaker_pan_slider.setRange(-100, 100)
        self.shaker_pan_slider.setSingleStep(5)
        self.shaker_pan_slider.setPageStep(10)
        self.shaker_pan_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.shaker_pan_slider.setTickInterval(25)
        self.shaker_pan_slider.setValue(0)
        self.shaker_pan_label = QLabel(self._format_pan_label(0))
        self.shaker_pan_label.setMinimumWidth(60)
        pan_row = QHBoxLayout()
        pan_row.addWidget(self.shaker_pan_slider, 1)
        pan_row.addWidget(self.shaker_pan_label)
        form.addRow("Pan:", pan_row)

        self.shaker_pan_slider.valueChanged.connect(self._on_shaker_pan_changed)
        self.shaker_channel_combo.currentIndexChanged.connect(
            self._on_shaker_channel_changed)
        self._on_shaker_channel_changed()

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

        self._setup_shaker_layers_section(outer)

        outer.addStretch(1)

        self.tabWidget.addTab(self.tab_Shaker, "Shaker")

    # ------------------------------------------------------------------
    # Effect layers subsection
    # ------------------------------------------------------------------

    def _setup_shaker_layers_section(self, parent_layout):
        from PyQt6.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
            QLabel, QComboBox, QDoubleSpinBox, QPushButton,
            QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
            QScrollArea,
        )
        from telemffb.hw.ffb_shaker import SHAKER_EFFECT_WHITELIST
        from telemffb.hw import shaker_layers_io

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        parent_layout.addWidget(sep)

        section_label = QLabel("<b>Effect layers</b>")
        parent_layout.addWidget(section_label)

        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("Effect:"))
        self.shaker_layer_effect_combo = QComboBox()
        self.shaker_layer_effect_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        for name in sorted(SHAKER_EFFECT_WHITELIST):
            self.shaker_layer_effect_combo.addItem(name, userData=name)
        effect_row.addWidget(self.shaker_layer_effect_combo)
        effect_row.addStretch(1)
        parent_layout.addLayout(effect_row)

        self.shaker_layer_table = QTableWidget(0, 10)
        self.shaker_layer_table.setHorizontalHeaderLabels(
            ["#", "Freq ×", "Gain", "Route", "OscType", "Remove",
             "Center Hz", "Bandwidth Hz", "Attack ms", "Decay ms"])
        hdr = self.shaker_layer_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
        self.shaker_layer_table.verticalHeader().setVisible(False)
        self.shaker_layer_table.setSelectionMode(
            QTableWidget.SelectionMode.NoSelection)
        self.shaker_layer_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        parent_layout.addWidget(self.shaker_layer_table)

        per_effect_row = QHBoxLayout()
        self.shaker_layer_add_btn = QPushButton("+ Add layer")
        self.shaker_layer_reset_btn = QPushButton("Reset effect to default")
        self.shaker_layer_test_btn = QPushButton("Test effect")
        if not self._shaker_backend_available:
            self.shaker_layer_test_btn.setEnabled(False)
            self.shaker_layer_test_btn.setToolTip(
                "Audio backend (sounddevice / PortAudio) is unavailable.")
        per_effect_row.addWidget(self.shaker_layer_add_btn)
        per_effect_row.addWidget(self.shaker_layer_reset_btn)
        per_effect_row.addWidget(self.shaker_layer_test_btn)
        per_effect_row.addStretch(1)
        parent_layout.addLayout(per_effect_row)

        bottom_row1 = QHBoxLayout()
        self.shaker_layer_save_btn = QPushButton("Save all effects")
        self.shaker_layer_reload_btn = QPushButton("Reload from disk")
        bottom_row1.addWidget(self.shaker_layer_save_btn)
        bottom_row1.addWidget(self.shaker_layer_reload_btn)
        bottom_row1.addStretch(1)
        parent_layout.addLayout(bottom_row1)

        bottom_row2 = QHBoxLayout()
        self.shaker_layer_reset_all_btn = QPushButton("Reset all effects to defaults")
        bottom_row2.addWidget(self.shaker_layer_reset_all_btn)
        bottom_row2.addStretch(1)
        parent_layout.addLayout(bottom_row2)

        path = shaker_layers_io.get_shaker_effects_path()
        if path:
            self._shaker_layer_saved_state = shaker_layers_io.load(path)
        else:
            self._shaker_layer_saved_state = {}
        self._shaker_layer_working_copy = {}

        self._shaker_layer_loading = False
        self._shaker_layer_prev_effect = None

        self._shaker_layer_rebuild_table(
            self.shaker_layer_effect_combo.currentData())
        self._shaker_layer_prev_effect = self.shaker_layer_effect_combo.currentData()

        self._shaker_layer_refresh_combo_markers()
        self._shaker_layer_update_save_btn()

        self.shaker_layer_effect_combo.currentIndexChanged.connect(
            self._on_shaker_layer_effect_changed)
        self.shaker_layer_add_btn.clicked.connect(self._on_shaker_layer_add)
        self.shaker_layer_reset_btn.clicked.connect(self._on_shaker_layer_reset_effect)
        self.shaker_layer_test_btn.clicked.connect(self._on_shaker_layer_test)
        self.shaker_layer_save_btn.clicked.connect(self._on_shaker_layer_save_all)
        self.shaker_layer_reload_btn.clicked.connect(self._on_shaker_layer_reload)
        self.shaker_layer_reset_all_btn.clicked.connect(self._on_shaker_layer_reset_all)

    def _shaker_layer_default_rows(self, name: str) -> list:
        from telemffb.hw.ffb_shaker import Layer, _BUILTIN_DEFAULT_LAYERS
        if name in _BUILTIN_DEFAULT_LAYERS:
            return list(_BUILTIN_DEFAULT_LAYERS[name])
        return [Layer()]

    def _shaker_layer_get_rows(self, name: str) -> list:
        if name in self._shaker_layer_working_copy:
            return list(self._shaker_layer_working_copy[name])
        if name in self._shaker_layer_saved_state:
            return list(self._shaker_layer_saved_state[name])
        return self._shaker_layer_default_rows(name)

    def _shaker_layer_is_modified(self, name: str) -> bool:
        working = self._shaker_layer_working_copy.get(name)
        saved = self._shaker_layer_saved_state.get(name)
        if working is None:
            return False
        default = self._shaker_layer_default_rows(name)
        effective_saved = saved if saved is not None else default
        return working != effective_saved

    def _shaker_layer_any_modified(self) -> bool:
        return any(self._shaker_layer_is_modified(n)
                   for n in self._shaker_layer_working_copy)

    def _shaker_layer_refresh_combo_markers(self):
        self._shaker_layer_loading = True
        try:
            combo = self.shaker_layer_effect_combo
            for i in range(combo.count()):
                name = combo.itemData(i)
                base = name
                if self._shaker_layer_is_modified(name):
                    combo.setItemText(i, f"● {base}")
                else:
                    combo.setItemText(i, base)
        finally:
            self._shaker_layer_loading = False

    def _shaker_layer_update_save_btn(self):
        self.shaker_layer_save_btn.setEnabled(self._shaker_layer_any_modified())

    def _shaker_layer_flush_current_to_working_copy(self):
        name = self._shaker_layer_prev_effect
        if name is None:
            return
        rows = self._shaker_layer_read_table_rows()
        self._shaker_layer_working_copy[name] = rows

    def _shaker_layer_read_table_rows(self) -> list:
        from telemffb.hw.ffb_shaker import Layer
        table = self.shaker_layer_table
        rows = []
        for r in range(table.rowCount()):
            freq_spin = table.cellWidget(r, 1)
            gain_spin = table.cellWidget(r, 2)
            route_combo = table.cellWidget(r, 3)
            osc_combo = table.cellWidget(r, 4)
            center_spin = table.cellWidget(r, 6)
            bw_spin = table.cellWidget(r, 7)
            attack_spin = table.cellWidget(r, 8)
            decay_spin  = table.cellWidget(r, 9)
            freq = freq_spin.value() if freq_spin else 1.0
            gain = gain_spin.value() if gain_spin else 1.0
            route = route_combo.currentData() if route_combo else "both"
            osc_type = osc_combo.currentData() if osc_combo else "sine"
            center_hz = center_spin.value() if center_spin else None
            bandwidth_hz = bw_spin.value() if bw_spin else None
            attack_ms = attack_spin.value() if attack_spin else None
            decay_ms  = decay_spin.value()  if decay_spin  else None
            rows.append(Layer(freq_factor=freq, gain=gain,
                              route=route, osc_type=osc_type,
                              center_hz=center_hz,
                              bandwidth_hz=bandwidth_hz,
                              attack_ms=attack_ms,
                              decay_ms=decay_ms))
        return rows

    def _make_layer_row_widgets(self, row_index: int, layer) -> dict:
        """Build the cell widgets for one layer row.

        Returns a dict of {column_name: widget} for placement and signal
        wiring.  Signal connections and setCellWidget calls are the
        caller's responsibility.  ``extras`` is intentionally empty for
        STEP_02; STEP_07 will populate it for bandpass_noise rows.
        """
        from PyQt6.QtWidgets import QDoubleSpinBox, QComboBox, QPushButton

        freq_spin = QDoubleSpinBox()
        freq_spin.setRange(0.10, 4.00)
        freq_spin.setSingleStep(0.05)
        freq_spin.setDecimals(2)
        freq_spin.setValue(layer.freq_factor)

        gain_spin = QDoubleSpinBox()
        gain_spin.setRange(0.00, 1.50)
        gain_spin.setSingleStep(0.05)
        gain_spin.setDecimals(2)
        gain_spin.setValue(layer.gain)

        route_combo = QComboBox()
        route_combo.addItem("shaker", userData="shaker")
        route_combo.addItem("stick",  userData="stick")
        route_combo.addItem("both",   userData="both")
        idx = route_combo.findData(layer.route)
        if idx >= 0:
            route_combo.setCurrentIndex(idx)

        osc_combo = QComboBox()
        osc_combo.addItem("sine",           userData="sine")
        osc_combo.addItem("impulse",        userData="impulse")
        osc_combo.addItem("bandpass_noise", userData="bandpass_noise")
        idx = osc_combo.findData(layer.osc_type)
        if idx >= 0:
            osc_combo.setCurrentIndex(idx)

        remove_btn = QPushButton("−")
        remove_btn.setFixedWidth(28)

        # Center Hz spinbox (col 6)
        center_default = (layer.center_hz if layer.center_hz is not None
                          else round(layer.freq_factor * 40, 1))
        center_spin = QDoubleSpinBox()
        center_spin.setRange(5.0, 200.0)
        center_spin.setSingleStep(0.5)
        center_spin.setDecimals(1)
        center_spin.setValue(max(5.0, min(200.0, center_default)))
        center_spin.setEnabled(layer.osc_type == "bandpass_noise")

        # Bandwidth Hz spinbox (col 7)
        bw_default = layer.bandwidth_hz if layer.bandwidth_hz is not None else 20.0
        bw_spin = QDoubleSpinBox()
        bw_spin.setRange(1.0, 100.0)
        bw_spin.setSingleStep(1.0)
        bw_spin.setDecimals(1)
        bw_spin.setValue(bw_default)
        bw_spin.setEnabled(layer.osc_type == "bandpass_noise")

        # Attack ms spinbox (col 8)
        attack_spin = QDoubleSpinBox()
        attack_spin.setRange(0.1, 50.0)
        attack_spin.setSingleStep(0.5)
        attack_spin.setDecimals(1)
        attack_spin.setValue(max(0.1, min(50.0,
                              layer.attack_ms if layer.attack_ms is not None
                              else 4.0)))
        attack_spin.setEnabled(layer.osc_type == "impulse")

        # Decay ms spinbox (col 9)
        decay_spin = QDoubleSpinBox()
        decay_spin.setRange(5.0, 500.0)
        decay_spin.setSingleStep(5.0)
        decay_spin.setDecimals(1)
        decay_spin.setValue(max(5.0, min(500.0,
                             layer.decay_ms if layer.decay_ms is not None
                             else 90.0)))
        decay_spin.setEnabled(layer.osc_type == "impulse")

        return {
            "freq_factor": freq_spin,
            "gain":        gain_spin,
            "route":       route_combo,
            "osc_type":    osc_combo,
            "remove":      remove_btn,
            "extras":      {
                "center_hz":    center_spin,
                "bandwidth_hz": bw_spin,
                "attack_ms":    attack_spin,
                "decay_ms":     decay_spin,
            },
        }

    def _shaker_layer_rebuild_table(self, name: str):
        from PyQt6.QtWidgets import QTableWidgetItem
        rows = self._shaker_layer_get_rows(name)
        table = self.shaker_layer_table
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for r, layer in enumerate(rows):
            idx_item = QTableWidgetItem(str(r))
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, 0, idx_item)

            w = self._make_layer_row_widgets(r, layer)
            w["freq_factor"].valueChanged.connect(self._on_shaker_layer_cell_changed)
            table.setCellWidget(r, 1, w["freq_factor"])
            w["gain"].valueChanged.connect(self._on_shaker_layer_cell_changed)
            table.setCellWidget(r, 2, w["gain"])
            w["route"].currentIndexChanged.connect(self._on_shaker_layer_cell_changed)
            table.setCellWidget(r, 3, w["route"])
            w["osc_type"].currentIndexChanged.connect(self._on_shaker_layer_cell_changed)
            table.setCellWidget(r, 4, w["osc_type"])
            w["remove"].clicked.connect(
                lambda checked, row=r: self._on_shaker_layer_remove(row))
            table.setCellWidget(r, 5, w["remove"])
            w["extras"]["center_hz"].valueChanged.connect(self._on_shaker_layer_cell_changed)
            table.setCellWidget(r, 6, w["extras"]["center_hz"])
            w["extras"]["bandwidth_hz"].valueChanged.connect(self._on_shaker_layer_cell_changed)
            table.setCellWidget(r, 7, w["extras"]["bandwidth_hz"])
            w["extras"]["attack_ms"].valueChanged.connect(self._on_shaker_layer_cell_changed)
            table.setCellWidget(r, 8, w["extras"]["attack_ms"])
            w["extras"]["decay_ms"].valueChanged.connect(self._on_shaker_layer_cell_changed)
            table.setCellWidget(r, 9, w["extras"]["decay_ms"])

        self._shaker_layer_update_remove_buttons()

    def _shaker_layer_update_remove_buttons(self):
        table = self.shaker_layer_table
        only_one = table.rowCount() == 1
        for r in range(table.rowCount()):
            btn = table.cellWidget(r, 5)
            if btn:
                btn.setEnabled(not only_one)

    def _on_shaker_layer_effect_changed(self, _index):
        if self._shaker_layer_loading:
            return
        self._shaker_layer_flush_current_to_working_copy()
        name = self.shaker_layer_effect_combo.currentData()
        self._shaker_layer_rebuild_table(name)
        self._shaker_layer_prev_effect = name
        self._shaker_layer_refresh_combo_markers()
        self._shaker_layer_update_save_btn()

    def _on_shaker_layer_cell_changed(self, *_):
        name = self.shaker_layer_effect_combo.currentData()
        if name is None or self._shaker_layer_loading:
            return

        # Toggle Center/Bandwidth spin boxes based on current osc_type per row,
        # and populate defaults when switching to bandpass_noise.
        table = self.shaker_layer_table
        for r in range(table.rowCount()):
            osc_combo   = table.cellWidget(r, 4)
            center_spin = table.cellWidget(r, 6)
            bw_spin     = table.cellWidget(r, 7)
            attack_spin = table.cellWidget(r, 8)
            decay_spin  = table.cellWidget(r, 9)
            if any(w is None for w in (osc_combo, center_spin, bw_spin, attack_spin, decay_spin)):
                continue
            osc_type   = osc_combo.currentData()
            is_noise   = (osc_type == "bandpass_noise")
            is_impulse = (osc_type == "impulse")
            if is_noise and not center_spin.isEnabled():
                # Switching into bandpass_noise — populate defaults
                freq_spin = table.cellWidget(r, 1)
                freq_factor = freq_spin.value() if freq_spin else 1.0
                default_center = round(freq_factor * 40, 1)
                default_center = max(5.0, min(200.0, default_center))
                center_spin.blockSignals(True)
                center_spin.setValue(default_center)
                center_spin.blockSignals(False)
                bw_spin.blockSignals(True)
                bw_spin.setValue(20.0)
                bw_spin.blockSignals(False)
            # Switching into impulse — populate defaults
            if is_impulse and not attack_spin.isEnabled():
                attack_spin.blockSignals(True);  attack_spin.setValue(4.0);  attack_spin.blockSignals(False)
                decay_spin.blockSignals(True);   decay_spin.setValue(90.0);  decay_spin.blockSignals(False)
            center_spin.setEnabled(is_noise)
            bw_spin.setEnabled(is_noise)
            attack_spin.setEnabled(is_impulse)
            decay_spin.setEnabled(is_impulse)

        rows = self._shaker_layer_read_table_rows()
        self._shaker_layer_working_copy[name] = rows
        self._shaker_layer_refresh_combo_markers()
        self._shaker_layer_update_save_btn()

    def _on_shaker_layer_add(self):
        from telemffb.hw.ffb_shaker import Layer
        from PyQt6.QtWidgets import QTableWidgetItem
        table = self.shaker_layer_table
        r = table.rowCount()
        table.insertRow(r)

        idx_item = QTableWidgetItem(str(r))
        idx_item.setFlags(idx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(r, 0, idx_item)

        layer = Layer()
        w = self._make_layer_row_widgets(r, layer)
        w["freq_factor"].valueChanged.connect(self._on_shaker_layer_cell_changed)
        table.setCellWidget(r, 1, w["freq_factor"])
        w["gain"].valueChanged.connect(self._on_shaker_layer_cell_changed)
        table.setCellWidget(r, 2, w["gain"])
        w["route"].currentIndexChanged.connect(self._on_shaker_layer_cell_changed)
        table.setCellWidget(r, 3, w["route"])
        w["osc_type"].currentIndexChanged.connect(self._on_shaker_layer_cell_changed)
        table.setCellWidget(r, 4, w["osc_type"])
        w["remove"].clicked.connect(
            lambda checked, row=r: self._on_shaker_layer_remove(row))
        table.setCellWidget(r, 5, w["remove"])
        w["extras"]["center_hz"].valueChanged.connect(self._on_shaker_layer_cell_changed)
        table.setCellWidget(r, 6, w["extras"]["center_hz"])
        w["extras"]["bandwidth_hz"].valueChanged.connect(self._on_shaker_layer_cell_changed)
        table.setCellWidget(r, 7, w["extras"]["bandwidth_hz"])
        w["extras"]["attack_ms"].valueChanged.connect(self._on_shaker_layer_cell_changed)
        table.setCellWidget(r, 8, w["extras"]["attack_ms"])
        w["extras"]["decay_ms"].valueChanged.connect(self._on_shaker_layer_cell_changed)
        table.setCellWidget(r, 9, w["extras"]["decay_ms"])

        self._shaker_layer_update_remove_buttons()
        self._on_shaker_layer_cell_changed()

    def _on_shaker_layer_remove(self, row: int):
        table = self.shaker_layer_table
        if table.rowCount() <= 1:
            return
        table.removeRow(row)
        for r in range(table.rowCount()):
            idx_item = table.item(r, 0)
            if idx_item:
                idx_item.setText(str(r))
            btn = table.cellWidget(r, 5)
            if btn:
                btn.clicked.disconnect()
                btn.clicked.connect(
                    lambda checked, row=r: self._on_shaker_layer_remove(row))
        self._shaker_layer_update_remove_buttons()
        self._on_shaker_layer_cell_changed()

    def _on_shaker_layer_reset_effect(self):
        name = self.shaker_layer_effect_combo.currentData()
        if self._shaker_layer_is_modified(name):
            ans = QMessageBox.question(
                self, "Reset effect",
                f"Effect '{name}' has unsaved changes. Reset to default anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        default_rows = self._shaker_layer_default_rows(name)
        self._shaker_layer_working_copy[name] = list(default_rows)
        self._shaker_layer_rebuild_table(name)
        self._shaker_layer_prev_effect = name
        self._shaker_layer_refresh_combo_markers()
        self._shaker_layer_update_save_btn()

    def _on_shaker_layer_test(self):
        import threading
        import time as _time
        from PyQt6.QtCore import QTimer

        rows = self._shaker_layer_read_table_rows()
        device_name = self.shaker_device_combo.currentData() or None
        gain = self.shaker_gain_spin.value()
        channel_mode = self.shaker_channel_combo.currentData() or "mono"
        pan = self.shaker_pan_slider.value() / 100.0

        self.shaker_layer_test_btn.setEnabled(False)
        self.shaker_layer_test_btn.setText("Testing…")

        def _run():
            try:
                from telemffb.hw.shaker_synth import ShakerSynth, Oscillator, BandpassNoiseGenerator
                synth = ShakerSynth(device=device_name, master_gain=gain,
                                    channel_mode=channel_mode, pan=pan)
                synth.start()
                try:
                    created_oscs = []
                    for idx, layer in enumerate(rows):
                        osc_name = f"layer_test_{idx}"
                        freq = 40.0 * layer.freq_factor
                        amp = min(1.0, layer.gain)
                        if layer.osc_type == "bandpass_noise":
                            osc = BandpassNoiseGenerator(synth.samplerate)
                            center = (layer.center_hz if layer.center_hz is not None
                                      else freq)
                            bw = layer.bandwidth_hz if layer.bandwidth_hz is not None else 20.0
                            osc.set(center_hz=center, bandwidth_hz=bw, amplitude=amp)
                        elif layer.osc_type == "impulse":
                            osc = Oscillator(synth.samplerate, synth.blocksize)
                            kwargs = {}
                            if layer.attack_ms is not None:
                                kwargs["attack_ms"] = layer.attack_ms
                            if layer.decay_ms is not None:
                                kwargs["decay_ms"] = layer.decay_ms
                            osc.trigger(freq, amp, **kwargs)
                        else:  # sine
                            osc = Oscillator(synth.samplerate, synth.blocksize)
                            osc.set(freq, amp, ramp_ms=80.0)
                        synth.add_oscillator(osc_name, osc)
                        created_oscs.append(osc)
                    _time.sleep(1.8)
                    for osc in created_oscs:
                        osc.stop(ramp_ms=100)
                    _time.sleep(0.2)
                finally:
                    synth.stop()
            except Exception:
                logging.exception("Shaker layer test failed")

        threading.Thread(target=_run, daemon=True).start()
        QTimer.singleShot(2500, self._on_shaker_layer_test_finished)

    def _on_shaker_layer_test_finished(self):
        self.shaker_layer_test_btn.setEnabled(self._shaker_backend_available)
        self.shaker_layer_test_btn.setText("Test effect")

    def _on_shaker_layer_save_all(self):
        from telemffb.hw import shaker_layers_io, ffb_shaker
        self._shaker_layer_flush_current_to_working_copy()
        path = shaker_layers_io.get_shaker_effects_path()
        if not path:
            logging.warning("Cannot save shaker effects: userconfig path not set")
            return
        saved = shaker_layers_io.load(path)
        merged = dict(saved)
        merged.update(self._shaker_layer_working_copy)
        shaker_layers_io.save(path, merged)
        self._shaker_layer_saved_state = shaker_layers_io.load(path)
        ffb_shaker.reload_layers()
        self._shaker_layer_refresh_combo_markers()
        self._shaker_layer_update_save_btn()

    def _on_shaker_layer_reload(self):
        if self._shaker_layer_any_modified():
            ans = QMessageBox.question(
                self, "Reload from disk",
                "There are unsaved changes. Discard and reload from disk?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        from telemffb.hw import shaker_layers_io
        path = shaker_layers_io.get_shaker_effects_path()
        if path:
            self._shaker_layer_saved_state = shaker_layers_io.load(path)
        else:
            self._shaker_layer_saved_state = {}
        self._shaker_layer_working_copy = {}
        name = self.shaker_layer_effect_combo.currentData()
        self._shaker_layer_rebuild_table(name)
        self._shaker_layer_prev_effect = name
        self._shaker_layer_refresh_combo_markers()
        self._shaker_layer_update_save_btn()

    def _on_shaker_layer_reset_all(self):
        ans = QMessageBox.question(
            self, "Reset all effects",
            "This will overwrite all saved layer customisations with built-in "
            "defaults. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        from telemffb.hw import shaker_layers_io, ffb_shaker
        path = shaker_layers_io.get_shaker_effects_path()
        if path:
            shaker_layers_io.save(path, ffb_shaker._BUILTIN_DEFAULT_LAYERS)
            self._shaker_layer_saved_state = shaker_layers_io.load(path)
        else:
            self._shaker_layer_saved_state = {}
        self._shaker_layer_working_copy = {}
        ffb_shaker.reload_layers()
        name = self.shaker_layer_effect_combo.currentData()
        self._shaker_layer_rebuild_table(name)
        self._shaker_layer_prev_effect = name
        self._shaker_layer_refresh_combo_markers()
        self._shaker_layer_update_save_btn()

    @staticmethod
    def _format_pan_label(slider_value: int) -> str:
        v = slider_value / 100.0
        if abs(v) < 0.01:
            return "Center"
        side = "L" if v < 0 else "R"
        return f"{side} {abs(v):.2f}"

    def _on_shaker_pan_changed(self, value: int) -> None:
        self.shaker_pan_label.setText(self._format_pan_label(value))

    def _on_shaker_channel_changed(self, *_args) -> None:
        is_pan = self.shaker_channel_combo.currentData() == "pan"
        self.shaker_pan_slider.setEnabled(is_pan)
        self.shaker_pan_label.setEnabled(is_pan)

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
        channel_mode = self.shaker_channel_combo.currentData() or "mono"
        pan = self.shaker_pan_slider.value() / 100.0
        self.shaker_test_button.setEnabled(False)
        self.shaker_test_button.setText("Testing…")

        def _run():
            try:
                from telemffb.hw.shaker_synth import ShakerSynth
                synth = ShakerSynth(device=device_name, master_gain=gain,
                                    channel_mode=channel_mode, pan=pan)
                synth.start()
                try:
                    osc = synth.get_oscillator("test")
                    osc.set(35.0, 0.5, ramp_ms=100)
                    time.sleep(1.2)
                    osc.stop(ramp_ms=80)
                    time.sleep(0.15)
                    osc.trigger(55.0, 0.9, attack_ms=3.0, decay_ms=120.0)
                    time.sleep(0.4)
                    osc.trigger(55.0, 0.9, attack_ms=3.0, decay_ms=120.0)
                    time.sleep(0.4)
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

        saved_mode = (settings_dict.get('shakerChannelMode', 'mono') or 'mono').strip().lower()
        mode_idx = 0
        for i in range(self.shaker_channel_combo.count()):
            if self.shaker_channel_combo.itemData(i) == saved_mode:
                mode_idx = i
                break
        self.shaker_channel_combo.setCurrentIndex(mode_idx)

        try:
            pan = float(settings_dict.get('shakerPan', 0.0))
        except (TypeError, ValueError):
            pan = 0.0
        pan = max(-1.0, min(1.0, pan))
        self.shaker_pan_slider.setValue(int(round(pan * 100)))
        self.shaker_pan_label.setText(self._format_pan_label(self.shaker_pan_slider.value()))
        self._on_shaker_channel_changed()

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
            'shakerChannelMode': self.shaker_channel_combo.currentData() or 'mono',
            'shakerPan': self.shaker_pan_slider.value() / 100.0,
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