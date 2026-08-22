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


import ipaddress
import json
import logging
import os

from PyQt6 import QtCore
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIntValidator, QIcon, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QAbstractItemView, QButtonGroup, QDialog, QFileDialog, QMessageBox, QSizePolicy, QStyleOption, QTabWidget

from . import globals as G
from . import utils
from .ui.Ui_SystemDialog import Ui_SystemDialog
from .InstanceSettingsPanel import (
    STARTUP_FIELDS, SYSTEM_FIELDS, InstanceSettingsPanel,
)
from .utils import (
    device_display_name, device_pid_key, validate_vpconf_profile, HiDpiPixmap,
)
from telemffb.hw.ffb_rhino import DeviceInfo, FFBRhino
from .custom_widgets import FFBDeviceListModel

class SystemSettingsDialog(QDialog, Ui_SystemDialog):
    def __init__(self, parent=None,):
        super(SystemSettingsDialog, self).__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"System Settings ({G.device_type.capitalize()})")


        self.master_button_group = QButtonGroup()
        self.master_button_group.setObjectName(u"master_button_group")
        self.master_button_group.addButton(self.rb_master_j, id=1)
        self.master_button_group.addButton(self.rb_master_p, id=2)
        self.master_button_group.addButton(self.rb_master_c, id=3)
        self.master_button_group.addButton(self.rb_master_t, id=4)

        # depreciate this option
        self.focus_pauseIL2.setChecked(False)
        self.focus_pauseIL2.setVisible(False)

        # Add tooltips
        self.validateDCS.setToolTip('If enabled, TelemFFB will automatically install the necessary export script and update the DCS export.lua file')
        self.validateIL2.setToolTip('If enabled, TelemFFB will automatically set up the required configuration in IL2 to support telemetry export')
        # self.focus_pauseIL2.setToolTip('When enabled, TelemFFB will enter a pause state when focus is lost on the IL2 game window. (Enabled by default)\n\nNote: While disabling can aid in adjusting effects in real time, when the IL2 window loses focus, it also loses all inputs.\nThis may result in odd behavior and stuck effects while the window is out of focus.')
        self.pathIL2.setToolTip('The root path where IL-2 Strumovik is installed')
        self.lab_pathIL2.setToolTip('The root path where IL-2 Strumovik is installed')
        self.pathIL2_K.setToolTip('The root path where IL-2 Korea is installed')
        self.lab_pathIL2_2.setToolTip('The root path where IL-2 Korea is installed')
        self.validateXPLANE.setToolTip('If enabled, TelemFFB will automatically install the required X-Plane plugin and keep it up to date when it changes')
        self.lab_pathXPLANE.setToolTip('The root path where X-Plane is installed')
        self.pathXPLANE.setToolTip('The root path where X-Plane is installed')
        self.cb_logPrune.setToolTip('Auto delete archived logs after time frame')

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
        IL2_PIXMAP = HiDpiPixmap(':/image/icon_IL2.png') if G.useDarkMode else HiDpiPixmap(':/image/icon_IL2_lm.png')
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
        self.browseIL2_K.clicked.connect(self.select_il2_directory)
        self.buttonBox.accepted.connect(self.save_settings)
        self.resetButton.clicked.connect(self.reset_settings)
        self.master_button_group.buttonClicked.connect(lambda button: self.change_master_widgets(button))
        self.cb_al_enable.stateChanged.connect(self.toggle_al_widgets)
        self.buttonBox.rejected.connect(self.close)

        self.validateIL2.clicked.connect(self.toggle_il2_path)
        self.validateIL2_K.clicked.connect(self.toggle_il2_path)

        self.il2_fwd_model = QStandardItemModel(0, 5, self)
        self.il2_fwd_model.setHorizontalHeaderLabels(["IP", "Port", "Telem", "Motion", "FFB"])
        self.il2_fwd_table.setModel(self.il2_fwd_model)
        self.il2_fwd_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.il2_fwd_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.il2_fwd_table.horizontalHeader().setStretchLastSection(True)
        self.pb_add_dest.clicked.connect(self.add_il2_fwd_dest)
        self.pb_delete_dest.clicked.connect(self.delete_il2_fwd_dest)
        self.il2_fwd_table.selectionModel().selectionChanged.connect(self.update_il2_fwd_delete_enabled)
        self.udp_ip.textChanged.connect(self.update_il2_fwd_add_enabled)
        self.udp_port.textChanged.connect(self.update_il2_fwd_add_enabled)
        self.cb_telem.stateChanged.connect(self.update_il2_fwd_add_enabled)
        self.cb_motion.stateChanged.connect(self.update_il2_fwd_add_enabled)
        self.cb_ffb.stateChanged.connect(self.update_il2_fwd_add_enabled)
        self.il2_fwd_enable.stateChanged.connect(self.toggle_il2_fwd_widgets)

        for button in self.buttonBox.buttons():
            button.setMinimumWidth(60)


        # Set initial state
        # self.toggle_log_prune_widgets()
        # self.toggle_dcs_widgets()
        # self.toggle_il2_widgets()
        # self.toggle_xplane_widgets()
        # self.toggle_msfs_widgets()
        # self.toggle_al_widgets()

        self.parent_window = parent
        # Load settings from the registry and update widget states
        self.current_al_dict = {}

        # only allow dark mode if debug menu visible
        # self.useDarkmode.setVisible(G.system_settings.get('debug', False))
        self.themeButtonGroup.setId(self.rb_LightTheme, 0)
        self.themeButtonGroup.setId(self.rb_DarkTheme, 1)
        self.themeButtonGroup.setId(self.rb_SystemTheme, 2)

        self._build_instance_panels()

        self.load_settings()

        self.toggle_log_prune_widgets()
        self.toggle_dcs_widgets()
        self.toggle_il2_widgets()
        self.toggle_xplane_widgets()
        self.toggle_msfs_widgets()
        self.toggle_al_widgets()

        int_validator = QIntValidator()
        self.tb_logPrune.setValidator(int_validator)

        self.cb_min_enable_j.setObjectName('minimize_j')
        self.cb_min_enable_j.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_p.setObjectName('minimize_p')
        self.cb_min_enable_p.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_c.setObjectName('minimize_c')
        self.cb_min_enable_c.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_min_enable_t.setObjectName('minimize_t')
        self.cb_min_enable_t.clicked.connect(self.toggle_launchmode_cbs)

        self.cb_headless_j.setObjectName('headless_j')
        self.cb_headless_j.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_p.setObjectName('headless_p')
        self.cb_headless_p.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_c.setObjectName('headless_c')
        self.cb_headless_c.clicked.connect(self.toggle_launchmode_cbs)
        self.cb_headless_t.setObjectName('headless_t')
        self.cb_headless_t.clicked.connect(self.toggle_launchmode_cbs)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)


        self.cb_select_j._autolaunch_cb = self.cb_al_enable_j
        self.cb_select_p._autolaunch_cb = self.cb_al_enable_p
        self.cb_select_c._autolaunch_cb = self.cb_al_enable_c
        self.cb_select_t._autolaunch_cb = self.cb_al_enable_t

        self.cb_select_j._startmin_cb = self.cb_min_enable_j
        self.cb_select_p._startmin_cb = self.cb_min_enable_p
        self.cb_select_c._startmin_cb = self.cb_min_enable_c
        self.cb_select_t._startmin_cb = self.cb_min_enable_t

        self.cb_select_j._headless_cb = self.cb_headless_j
        self.cb_select_p._headless_cb = self.cb_headless_p
        self.cb_select_c._headless_cb = self.cb_headless_c
        self.cb_select_t._headless_cb = self.cb_headless_t





        if G.master_instance and G.launched_instances:
            self.labelLaunch.setText("Launch Options (Global):")
            # self.labelSim.setText("Sim Setup (Global):")
            # self.labelOther.setText("Other Settings (Per Instance):")

        # Enabling start with windows should force headless mode for children
        self.cb_startToTray.clicked.connect(self.toggle_headless)
        self.cb_startToTray.clicked.connect(self.toggle_start_mode)
        self.cb_masterStartMin.clicked.connect(self.toggle_start_mode)

        self.simTabWidget.tabBar().setExpanding(False)
        self.simTabWidget.tabBar().setUsesScrollButtons(False)
        self.simTabWidget.tabBar().setDocumentMode(True)

        self.select_enabled_sim()

        # pending device path changes (only written on Save)
        self._pending_devpaths = {}

        self.populateUSBSelectors()
        # Runs after the selectors are filled and the saved assignments
        # restored, so it sees what is actually assigned.
        self.toggle_device_launch_widgets()

        # Connected only now, not with the other signals earlier in __init__:
        # this handler repopulates the device selectors, which reach for the
        # companion widgets attached to them above.  The box is ticked while
        # restoring saved settings, so an earlier connection would fire the
        # handler before they exist.
        self.cb_enable_dinput.stateChanged.connect(self.toggle_dinput_support)

    @staticmethod
    def _enumerate_dinput_devices(enabled=None):
        """Generic DirectInput FFB devices, prepared for the selector model.

        VPforce hardware also enumerates as a DI FFB device, so VID 0xFFFF is
        filtered out — those entries come from the native HID enumeration.
        Debug override: list VPforce devices as [DI] entries too, so a Rhino
        can be driven through the DirectInput backend as a second test
        implementation (full generic-device behavior: capability gating,
        CP emulation, no vpconf/firmware).  Enable via the registry value
        'vpforce_as_dinput' = 1 under HKCU\\Software\\VPforce\\TelemFFB
        (root, or the master instance's device subkey).
        The devpath encodes the backend ('dinput:{GUID}') so the selection
        flows through the existing devpath_* persistence untouched.
        """
        # Opt-in: with support off, no [DI] entries appear anywhere and the
        # bridge is never loaded, so a stock install behaves exactly as it did
        # before DirectInput support existed.  `enabled` overrides the stored
        # setting so the dialog can re-list live as the box is ticked.
        if enabled is None:
            enabled = G.system_settings.get('enableDirectInput', False)
        if not enabled:
            return []

        try:
            from telemffb.hw.ffb_dinput import DInputFFBDevice

            # registry values arrive as strings; bool('0') is True
            flag = str(G.system_settings.get('vpforce_as_dinput', '')).strip().lower()
            include_vpforce = flag in ('1', 'true', 'yes', 'on')
            di_devices = []
            for dev in DInputFFBDevice.enumerate():
                if dev.vendor_id == 0xFFFF and not include_vpforce:
                    continue
                dev.product_string = f"[DI] {dev.product_string}"
                dev.path = f"dinput:{dev.guid}".encode()
                di_devices.append(dev)
            return di_devices
        except Exception as e:
            # Support is explicitly on at this point, so silence would just
            # look like "my device is missing".  The usual cause is the
            # separately distributed bridge DLL not being installed.
            logging.error(
                f"DirectInput support is enabled but no devices could be "
                f"enumerated: {e}. The DInput bridge DLL is required - see the "
                "TelemFFB DirectInput documentation.")
            return []

    def populateUSBSelectors(self, dinput_enabled=None):
        # Populate the USB device selectors with currently connected devices
        devices = FFBRhino.enumerate()

        combo_boxes = [self.cb_select_j, self.cb_select_p, self.cb_select_c, self.cb_select_t]

        # Generic DirectInput FFB devices are joystick-role only for now, so
        # the joystick combo gets its own extended model.
        model = FFBDeviceListModel(devices)
        joystick_model = FFBDeviceListModel(
            list(devices) + self._enumerate_dinput_devices(dinput_enabled))
        for cb in combo_boxes:
            cb_model = joystick_model if cb is self.cb_select_j else model
            # Swapping the model emits currentIndexChanged with nothing
            # selected yet.  On a repopulate (the DirectInput toggle) that
            # reaches the handler connected below, which reads it as "device
            # cleared" and wipes this role's launch options - so stay quiet
            # until the saved selection has been restored.
            cb.blockSignals(True)
            cb.setModel(cb_model)
            # Ensure the combobox shows the display role text but we can fetch the DeviceInfo from the model via UserRole
            cb.setModelColumn(0)
            # clear any existing items so the view updates cleanly
            # (when using setModel, clear isn't necessary but keep for safety)
            # store a reference so the model isn't garbage-collected
            cb._ffb_device_model = cb_model

        # Track previous index so we can revert on cancel
        for cb in combo_boxes:
            cb._prev_index = cb.currentIndex()

        # Whatever ends up selected below decides which launch options mean
        # anything; the sync at the end of this method applies that.
        # Try to select initial devices based on saved system settings devpath_{role}
        # mapping of combobox to device role name in settings
        dev_map = {
            self.cb_select_j: 'joystick',
            self.cb_select_p: 'pedals',
            self.cb_select_c: 'collective',
            self.cb_select_t: 'trimwheel',
        }

        for cb, short in dev_map.items():
            try:
                saved_key = f"devpath_{short}"
                saved_path = G.system_settings.get(saved_key, '')
            except Exception:
                saved_path = ''

            if not saved_path:
                continue

            model = cb.model()
            found_index = -1
            # iterate through model rows to find a matching device.path
            row_count = model.rowCount()
            for row in range(row_count):
                item_index = model.index(row, 0)
                dev : DeviceInfo = model.data(item_index, Qt.ItemDataRole.UserRole)
                if dev is None:
                    continue
                # some DeviceInfo objects may expose different path attributes
                dev_path = dev.path
                if dev_path and saved_path and dev_path.decode() == str(saved_path):
                    found_index = row
                    break

            if found_index >= 0:
                # block signals to avoid triggering change handlers
                cb.blockSignals(True)
                cb.setCurrentIndex(found_index)
                cb._prev_index = found_index
                cb.blockSignals(False)

        # Helper: persist a combobox selection into G.system_settings
        def persist_combobox_selection(cb, role_name):
            model = cb.model()
            sel_index = cb.currentIndex()
            if sel_index < 0:
                # clear saved setting
                try:
                    self._pending_devpaths[f'devpath_{role_name}'] = ''
                except Exception:
                    logging.exception('Failed to clear devpath setting')
                return

            dev = model.data(model.index(sel_index, 0), Qt.ItemDataRole.UserRole)
            if dev is None:
                try:
                    self._pending_devpaths[f'devpath_{role_name}'] = ''
                except Exception:
                    logging.exception('Failed to clear devpath setting')
                return

            # prefer device.path (bytes) decode
            dev_path = getattr(dev, 'path', None)
            if isinstance(dev_path, (bytes, bytearray)):
                try:
                    dev_path = dev_path.decode()
                except Exception:
                    dev_path = str(dev_path)

            try:
                self._pending_devpaths[f'devpath_{role_name}'] = dev_path
            except Exception:
                logging.exception('Failed to persist devpath setting')



        # Handler to enforce uniqueness across combo boxes
        def on_device_changed(index, changed_cb= None):
            # map combobox to role name for persistence
            cb_role_map = {
                self.cb_select_j: 'joystick',
                self.cb_select_p: 'pedals',
                self.cb_select_c: 'collective',
                self.cb_select_t: 'trimwheel',
            }
            # when model includes dummy at 0, index 0 means None
            if changed_cb is None:
                return

            # get selected device object
            model = changed_cb.model()
            dev = None
            if index >= 0:
                dev = model.data(model.index(index, 0), Qt.ItemDataRole.UserRole)

            # if no device selected, persist cleared selection and accept
            if dev is None:
                changed_cb._prev_index = index
                role = cb_role_map.get(changed_cb, None)
                if role:
                    persist_combobox_selection(changed_cb, role)
                # The user cleared this slot deliberately, so its launch
                # options go with it.
                changed_cb._autolaunch_cb.setChecked(False)
                changed_cb._startmin_cb.setChecked(False)
                changed_cb._headless_cb.setChecked(False)
                self.toggle_device_launch_widgets()
                return

            # check if any other combobox already has this device
            for other in combo_boxes:
                if other is changed_cb:
                    continue
                other_idx = other.currentIndex()
                if other_idx < 0:
                    continue
                other_dev = other.model().data(other.model().index(other_idx, 0), Qt.ItemDataRole.UserRole)
                if other_dev is not None and getattr(other_dev, 'serial_number', None) == getattr(dev, 'serial_number', None) and getattr(other_dev, 'path', None) == getattr(dev, 'path', None):
                    # conflict detected
                    msg = QMessageBox(self)
                    msg.setWindowTitle('Device Conflict')
                    msg.setText('The selected device is already assigned to another instance.')
                    msg.setInformativeText('Do you want to override the other assignment (clear it) or cancel this selection?')
                    override_btn = msg.addButton('Override', QMessageBox.ButtonRole.AcceptRole)
                    cancel_btn = msg.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
                    msg.setDefaultButton(cancel_btn)
                    msg.exec()

                    if msg.clickedButton() == override_btn:
                        # clear other combobox selection (set to index 0 = None)
                        other.setCurrentIndex(0)
                        # accept new selection
                        changed_cb._prev_index = index
                        self.toggle_device_launch_widgets()
                        return
                    else:
                        # revert selection on changed_cb
                        # block signals to avoid recursion
                        changed_cb.blockSignals(True)
                        changed_cb.setCurrentIndex(changed_cb._prev_index)
                        changed_cb.blockSignals(False)
                        return

            # no conflicts, commit
            changed_cb._prev_index = index

            # After successful change, persist the selection for this combobox's role
            # map combobox to role name
            cb_role_map = {
                self.cb_select_j: 'joystick',
                self.cb_select_p: 'pedals',
                self.cb_select_c: 'collective',
                self.cb_select_t: 'trimwheel',
            }

            role = cb_role_map.get(changed_cb, None)
            if role:
                persist_combobox_selection(changed_cb, role)

            self.toggle_device_launch_widgets()

        for cb in combo_boxes:
            cb.blockSignals(False)

        # connect signals (once - this method also runs on repopulate)
        if not getattr(self, '_device_signals_connected', False):
            for cb in combo_boxes:
                # use lambda binding to capture cb in the handler
                cb.currentIndexChanged.connect(lambda idx, _cb=cb: on_device_changed(idx, changed_cb=_cb))
            self._device_signals_connected = True

        # Additionally, ensure that when selection is cleared (index 0 / None) the setting is persisted.
        # The on_device_changed handler will call persist after committing the change, but if a selection
        # was reverted by conflict resolution it will also update the persisted value when appropriate.



    def select_enabled_sim(self):
        for sim in ('DCS', 'MSFS', 'XPLANE', 'IL2', 'BMS'):
            cb = getattr(self, f'enable{sim}')
            if cb.isChecked():
                # Find first enabled sim in the list and make that the default selected tab
                tab_index = getattr(self, f'{sim}_TAB')
                self.simTabWidget.setCurrentIndex(tab_index)
                return

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
        """The master instance launches itself, so it has no launch options.

        Only the clearing lives here; which row is hidden is worked out from
        the current state by toggle_device_launch_widgets.
        """
        role = self.MASTER_ROLE_IDS.get(self.master_button_group.id(button))
        if role:
            for name in self.ROLE_LAUNCH_WIDGETS[role][1:]:
                getattr(self, name).setChecked(False)
        self.toggle_device_launch_widgets()

    #: Device roles by the id their master-instance radio carries.
    MASTER_ROLE_IDS = {1: 'joystick', 2: 'pedals', 3: 'collective',
                       4: 'trimwheel'}

    #: Per device: the master radio, then auto-launch, start minimized
    #: and start headless.
    ROLE_LAUNCH_WIDGETS = {
        'joystick': ('rb_master_j', 'cb_al_enable_j', 'cb_min_enable_j', 'cb_headless_j'),
        'pedals': ('rb_master_p', 'cb_al_enable_p', 'cb_min_enable_p', 'cb_headless_p'),
        'collective': ('rb_master_c', 'cb_al_enable_c', 'cb_min_enable_c', 'cb_headless_c'),
        'trimwheel': ('rb_master_t', 'cb_al_enable_t', 'cb_min_enable_t', 'cb_headless_t'),
    }

    def toggle_device_launch_widgets(self):
        """Work out each device's launch row from the current state.

        A device with nothing assigned has nothing to master or launch, and
        the master instance launches itself, so its row has no options at
        all.  Both are facts about the state rather than events, so they are
        applied here rather than left to whichever signal last fired.

        This only shows, hides, enables and disables.  Clearing the
        checkboxes is left to the handlers for a change the user actually
        made: a device that is merely unplugged today shows as (None) here,
        and unchecking it would throw away a setting never touched.
        """
        self._update_vpconf_gates()
        al_enabled = self.cb_al_enable.isChecked()
        master_role = self.MASTER_ROLE_IDS.get(self.master_button_group.checkedId())
        for role, (radio, *launch_boxes) in self.ROLE_LAUNCH_WIDGETS.items():
            assigned = self.selected_device(role) is not None
            getattr(self, radio).setEnabled(assigned)
            for name in launch_boxes:
                widget = getattr(self, name)
                widget.setVisible(role != master_role)
                widget.setEnabled(assigned and al_enabled)

    def toggle_al_widgets(self):
        al_enabled = self.cb_al_enable.isChecked()
        self.lab_auto_launch.setEnabled(al_enabled)
        self.lab_start_min.setEnabled(al_enabled)
        self.lab_start_headless.setEnabled(al_enabled)
        self.toggle_device_launch_widgets()

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

    def toggle_dinput_support(self):
        """Re-list devices so [DI] entries appear or disappear immediately.

        The checkbox state is passed through rather than written to settings:
        the dialog persists it on save, and writing here would make the
        change stick even if the user cancels.

        Switching it on without the bridge DLL would silently list nothing,
        which reads as "my device is missing" - so the reason is shown and
        the box goes back off rather than sitting on and doing nothing.
        """
        if self.cb_enable_dinput.isChecked():
            from telemffb.hw.ffb_dinput import bridge_availability
            available, reason = bridge_availability()
            if not available:
                QMessageBox.warning(self, "DirectInput Unavailable", reason)
                # the revert re-enters here with the box off; block it so
                # the repopulate below runs once, for the settled state
                self.cb_enable_dinput.blockSignals(True)
                self.cb_enable_dinput.setChecked(False)
                self.cb_enable_dinput.blockSignals(False)

        self.populateUSBSelectors(dinput_enabled=self.cb_enable_dinput.isChecked())
        self.toggle_device_launch_widgets()

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
        self.lab_pathIL2_2.setEnabled(il2_enabled)
        self.pathIL2_K.setEnabled(il2_enabled)
        self.browseIL2_K.setEnabled(il2_enabled)
        self.validateIL2_K.setEnabled(il2_enabled)
        self.lab_IL2_S.setEnabled(il2_enabled)
        self.lab_IL2_K.setEnabled(il2_enabled)

        self.browseIL2.setEnabled(self.validateIL2.isChecked())
        self.pathIL2.setEnabled(self.validateIL2.isChecked())
        self.browseIL2_K.setEnabled(self.validateIL2_K.isChecked())
        self.pathIL2_K.setEnabled(self.validateIL2_K.isChecked())


    def toggle_il2_path(self):
        auto_config = self.validateIL2.isChecked() if self.sender() == self.validateIL2 else self.validateIL2_K.isChecked()
        if self.sender() == self.validateIL2:
            self.browseIL2.setEnabled(auto_config)
            self.pathIL2.setEnabled(auto_config)
        else:
            self.browseIL2_K.setEnabled(auto_config)
            self.pathIL2_K.setEnabled(auto_config)

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
            if self.sender() == self.browseIL2:
                self.pathIL2.setText(directory)
            elif self.sender() == self.browseIL2_K:
                self.pathIL2_K.setText(directory)

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
        logging.debug(f"{sender.objectName()} checked:{sender.isChecked()}")

    def validate_il2_path(self):
        if self.validateIL2.isChecked():
            file_path = os.path.join(self.pathIL2.text(), "data\\startup.cfg")
            if not os.path.exists(file_path):
                QMessageBox.warning(self, "Config Error",
                                    "IL2 Auto Telemetry is enabled but the path is invalid\n\n\\data\\startup.cfg not found at path")
                return False
        if self.validateIL2_K.isChecked():
            # Standalone nests the game under <root>\game\data; the Steam
            # release (IL2Series) uses <root>\data — accept either layout.
            game_root = utils.il2_korea_game_root(self.pathIL2_K.text())
            file_path = os.path.join(game_root, "data", "startup.cfg")
            if not os.path.exists(file_path):
                QMessageBox.warning(self, "Config Error",
                                    "IL2 Auto Telemetry is enabled but the path is invalid\n\n"
                                    "startup.cfg not found under '\\game\\data' (standalone) "
                                    "or '\\data' (Steam) at the configured path")
                return False
        return True

    def _add_il2_fwd_row(self, addr, port, telem, motion, ffb):
        ip_item = QStandardItem(str(addr))
        port_item = QStandardItem(str(port))
        telem_item = QStandardItem("Yes" if telem else "No")
        motion_item = QStandardItem("Yes" if motion else "No")
        ffb_item = QStandardItem("Yes" if ffb else "No")
        for item, value in ((telem_item, bool(telem)), (motion_item, bool(motion)), (ffb_item, bool(ffb))):
            item.setData(value, Qt.ItemDataRole.UserRole)
        for item in (ip_item, port_item, telem_item, motion_item, ffb_item):
            item.setEditable(False)
        self.il2_fwd_model.appendRow([ip_item, port_item, telem_item, motion_item, ffb_item])

    def add_il2_fwd_dest(self):
        addr = self.udp_ip.text().strip()
        port = self.udp_port.text().strip()
        telem = self.cb_telem.isChecked()
        motion = self.cb_motion.isChecked()
        ffb = self.cb_ffb.isChecked()

        self._add_il2_fwd_row(addr, port, telem, motion, ffb)

    def delete_il2_fwd_dest(self):
        selected_rows = sorted({idx.row() for idx in self.il2_fwd_table.selectionModel().selectedRows()}, reverse=True)
        for row in selected_rows:
            self.il2_fwd_model.removeRow(row)

    def _il2_fwd_ip_is_valid(self) -> bool:
        try:
            ipaddress.ip_address(self.udp_ip.text().strip())
            return True
        except ValueError:
            return False

    def _il2_fwd_port_is_valid(self) -> bool:
        port = self.udp_port.text().strip()
        return port.isdigit() and 1 <= int(port) <= 65535

    def update_il2_fwd_add_enabled(self):
        valid = (
            self.il2_fwd_enable.isChecked()
            and self._il2_fwd_ip_is_valid()
            and self._il2_fwd_port_is_valid()
            and (self.cb_telem.isChecked() or self.cb_motion.isChecked() or self.cb_ffb.isChecked())
        )
        self.pb_add_dest.setEnabled(valid)

    def update_il2_fwd_delete_enabled(self):
        has_selection = bool(self.il2_fwd_table.selectionModel().selectedRows())
        self.pb_delete_dest.setEnabled(self.il2_fwd_enable.isChecked() and has_selection)

    def toggle_il2_fwd_widgets(self):
        enabled = self.il2_fwd_enable.isChecked()
        for widget in (self.udp_ip, self.udp_port, self.cb_telem, self.cb_motion, self.cb_ffb, self.il2_fwd_table):
            widget.setEnabled(enabled)
        self.update_il2_fwd_add_enabled()
        self.update_il2_fwd_delete_enabled()

    def get_il2_fwd_destinations(self):
        destinations = []
        for row in range(self.il2_fwd_model.rowCount()):
            addr = self.il2_fwd_model.item(row, 0).text()
            port = self.il2_fwd_model.item(row, 1).text()
            telem = self.il2_fwd_model.item(row, 2).data(Qt.ItemDataRole.UserRole)
            motion = self.il2_fwd_model.item(row, 3).data(Qt.ItemDataRole.UserRole)
            ffb = self.il2_fwd_model.item(row, 4).data(Qt.ItemDataRole.UserRole)
            destinations.append({"addr": addr, "port": port, "telem": bool(telem), "motion": bool(motion), "ffb": bool(ffb)})
        return destinations

    def load_il2_fwd_destinations(self, destinations_json):
        self.il2_fwd_model.removeRows(0, self.il2_fwd_model.rowCount())
        try:
            destinations = json.loads(destinations_json) if destinations_json else []
        except (TypeError, ValueError):
            logging.exception("Failed to parse il2_fwd_destinations setting")
            destinations = []
        for dest in destinations:
            self._add_il2_fwd_row(dest.get('addr', ''), dest.get('port', ''), dest.get('telem', False),
                                   dest.get('motion', False), dest.get('ffb', False))

    #: Which auto-launch toggle belongs to which device.
    AUTOLAUNCH_TOGGLES = {'joystick': 'cb_al_enable_j', 'pedals': 'cb_al_enable_p',
                          'collective': 'cb_al_enable_c', 'trimwheel': 'cb_al_enable_t'}

    def validate_settings(self):
        master_role = self.MASTER_ROLE_IDS.get(
            self.master_button_group.checkedId())
        if self.cb_al_enable.isChecked() and not (self.cb_al_enable_j.isChecked() or self.cb_al_enable_p.isChecked() or self.cb_al_enable_c.isChecked()  or self.cb_al_enable_t.isChecked()):
            QMessageBox.warning(self, "Config Error", "Auto Launching is enabled but no devices are configured for auto launch.  Please enable a device or disable auto launching")
            return False
        # An instance is launched against a device; without one picked there
        # is nothing for it to drive.
        if master_role and not self.instance_pid(master_role):
            QMessageBox.warning(
                self, "Config Error",
                "Please select a device for the Master Instance")
            return False
        for role, toggle in self.AUTOLAUNCH_TOGGLES.items():
            if role == master_role:
                continue          # the master launches itself; it has no row
            if getattr(self, toggle).isChecked() and not self.instance_pid(role):
                QMessageBox.warning(
                    self, "Config Error",
                    f"Please select a device for the "
                    f"{device_display_name(role)} or disable its auto-launch")
                return False
        if self.validateXPLANE.isChecked():
            pth = os.path.join(self.pathXPLANE.text(), 'resources')
            if not os.path.isdir(pth):
                QMessageBox.warning(self, "Config Error", 'Please enter the root X-Plane install path or disable auto X-plane setup')
                return False
        if not self.validate_instance_settings():
            return False

        if self.enableIL2.isChecked():
            if not self.validate_il2_path():
                return False

        if self.il2_fwd_enable.isChecked() and self.il2_fwd_model.rowCount() == 0:
            QMessageBox.warning(self, "Config Error",
                                 "IL2 Telemetry Forwarding is enabled but no destinations have been added.\n\n"
                                 "Please add at least one destination or disable forwarding.")
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
            "validateIL2_K": self.validateIL2_K.isChecked(),
            "focus_pauseIL2": self.focus_pauseIL2.isChecked(),
            "pathIL2": self.pathIL2.text(),
            "portIL2": str(self.portIL2.text()),
            "pathIL2_K": self.pathIL2_K.text(),
            "il2_fwd_enable": self.il2_fwd_enable.isChecked(),
            "il2_fwd_destinations": json.dumps(self.get_il2_fwd_destinations()),
            'enableBMS': self.enableBMS.isChecked(),
            'enableDirectInput': self.cb_enable_dinput.isChecked(),
            'masterInstance': self.master_button_group.checkedId(),
            'autolaunchMaster': self.cb_al_enable.isChecked(),
            'autolaunchJoystick': self.cb_al_enable_j.isChecked(),
            'autolaunchPedals': self.cb_al_enable_p.isChecked(),
            'autolaunchCollective': self.cb_al_enable_c.isChecked(),
            'autolaunchTrimWheel': self.cb_al_enable_t.isChecked(),
            'startMinJoystick': self.cb_min_enable_j.isChecked(),
            'startMinPedals': self.cb_min_enable_p.isChecked(),
            'startMinCollective': self.cb_min_enable_c.isChecked(),
            'startMinTrimWheel': self.cb_min_enable_t.isChecked(),
            'startHeadlessJoystick': self.cb_headless_j.isChecked(),
            'startHeadlessPedals': self.cb_headless_p.isChecked(),
            'startHeadlessCollective': self.cb_headless_c.isChecked(),
            'startHeadlessTrimWheel': self.cb_headless_t.isChecked(),
            'pidJoystick': self.instance_pid('joystick'),
            'pidPedals': self.instance_pid('pedals'),
            'pidCollective': self.instance_pid('collective'),
            'pidTrimWheel': self.instance_pid('trimwheel'),
            'pruneLogs': self.cb_logPrune.isChecked(),
            'pruneLogsNum': self.tb_logPrune.text(),
            'pruneLogsUnit': self.combo_logPrune.currentText(),
            'startToTray': self.cb_startToTray.isChecked(),
            'masterStartMin': self.cb_masterStartMin.isChecked(),
            'closeToTray': self.cb_closeToTray.isChecked(),
            'themeId': self.themeButtonGroup.checkedId(),
        }

        # Only the update check remains this-instance-only in name; it is
        # a global concern (the master is what checks), so it is written as
        # one.  Everything else per-instance now comes from the panels.
        global_settings_dict["ignoreUpdate"] = self.ignoreUpdate.isChecked()

        key_list = [
            'autolaunchMaster',
            'autolaunchJoystick',
            'autolaunchPedals',
            'autolaunchCollective',
            'autolaunchTrimWheel',
            'startMinJoystick',
            'startMinPedals',
            'startMinCollective',
            'startMinTrimWheel',
            'startHeadlessJoystick',
            'startHeadlessPedals',
            'startHeadlessCollective',
            'startHeadlessTrimWheel',
            'pidJoystick',
            'pidPedals',
            'pidCollective',
            'pidTrimWheel',
            'themeId'
        ]
        saved_al_dict = {}
        for key in key_list:
            saved_al_dict[key] = global_settings_dict[key]

        if self.current_al_dict != saved_al_dict:
            QMessageBox.information(self, "Restart Required", "The Auto-Launch or Master Device settings have changed.  Please restart TelemFFB.")

        if not self.validate_settings():
            return

        for k,v in global_settings_dict.items():
            G.system_settings.setValue(f"{k}", v)

        # each device's own settings, from its panels
        for panel in self.instance_panels.values():
            panel.save(G.system_settings)

        # Persist any pending devpath selections that were changed while the dialog was open
        try:
            for k, v in getattr(self, '_pending_devpaths', {}).items():
                G.system_settings.setValue(k, v)
        except Exception:
            logging.exception('Failed to write pending devpath settings')

        G.sim_listeners.restart_all()

        if G.master_instance and G.launched_instances:
            G.ipc_instance.send_broadcast_message("RESTART SIMS")

        # adjust logging level (this instance's own panel):
        own = self.instance_panels.get(('system', G.device_type))
        ll = own.widgets['logLevel'].currentText() if own else 'INFO'
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
            pass
        else:
            # Read settings from the registry
            settings_dict = G.system_settings
            pass
        # Update widget states based on the loaded settings
        for panel in self.instance_panels.values():
            panel.load(G.system_settings, defaults_only=default)

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

        self.validateIL2.setChecked(settings_dict.get('validateIL2', False))
        self.validateIL2_K.setChecked(settings_dict.get('validateIL2_K', False))

        self.focus_pauseIL2.setChecked(settings_dict.get('focus_pauseIL2', False))

        self.pathIL2.setText(settings_dict.get('pathIL2', 'C:/Program Files/IL-2 Sturmovik Great Battles'))
        self.pathIL2_K.setText(settings_dict.get('pathIL2_K', ''))
        self.il2_fwd_enable.setChecked(settings_dict.get('il2_fwd_enable', False))
        self.load_il2_fwd_destinations(settings_dict.get('il2_fwd_destinations', '[]'))
        self.toggle_il2_fwd_widgets()

        self.portIL2.setText(str(settings_dict.get('portIL2', 34385)))

        self.enableBMS.setChecked(settings_dict.get('enableBMS', False))
        self.toggle_bms_widgets()

        self.cb_enable_dinput.setChecked(settings_dict.get('enableDirectInput', False))


        self.cb_al_enable.setChecked(settings_dict.get('autolaunchMaster', False))

        self.cb_al_enable_j.setChecked(settings_dict.get('autolaunchJoystick', False))
        self.cb_al_enable_p.setChecked(settings_dict.get('autolaunchPedals', False))
        self.cb_al_enable_c.setChecked(settings_dict.get('autolaunchCollective', False))
        self.cb_al_enable_t.setChecked(settings_dict.get('autolaunchTrimWheel', False))

        self.cb_min_enable_j.setChecked(settings_dict.get('startMinJoystick', False))
        self.cb_min_enable_p.setChecked(settings_dict.get('startMinPedals', False))
        self.cb_min_enable_c.setChecked(settings_dict.get('startMinCollective', False))
        self.cb_min_enable_t.setChecked(settings_dict.get('startMinTrimWheel', False))

        self.cb_headless_j.setChecked(settings_dict.get('startHeadlessJoystick', False))
        self.cb_headless_p.setChecked(settings_dict.get('startHeadlessPedals', False))
        self.cb_headless_c.setChecked(settings_dict.get('startHeadlessCollective', False))
        self.cb_headless_t.setChecked(settings_dict.get('startHeadlessTrimWheel', False))

        self.master_button_group.button(settings_dict.get('masterInstance', 1)).setChecked(True)
        self.master_button_group.button(settings_dict.get('masterInstance', 1)).click()


        self.toggle_al_widgets()

        # build record of auto-launch settings to see if they changed on save:
        self.current_al_dict = {
            'autolaunchMaster': self.cb_al_enable.isChecked(),
            'autolaunchJoystick': self.cb_al_enable_j.isChecked(),
            'autolaunchPedals': self.cb_al_enable_p.isChecked(),
            'autolaunchCollective': self.cb_al_enable_c.isChecked(),
            'autolaunchTrimWheel': self.cb_al_enable_t.isChecked(),
            'startMinJoystick': self.cb_min_enable_j.isChecked(),
            'startMinPedals': self.cb_min_enable_p.isChecked(),
            'startMinCollective': self.cb_min_enable_c.isChecked(),
            'startMinTrimWheel': self.cb_min_enable_t.isChecked(),
            'startHeadlessJoystick': self.cb_headless_j.isChecked(),
            'startHeadlessPedals': self.cb_headless_p.isChecked(),
            'startHeadlessCollective': self.cb_headless_c.isChecked(),
            'startHeadlessTrimWheel': self.cb_headless_t.isChecked(),
            'pidJoystick': self.instance_pid('joystick'),
            'pidPedals': self.instance_pid('pedals'),
            'pidCollective': self.instance_pid('collective'),
            'pidTrimWheel': self.instance_pid('trimwheel'),
            'themeId': self.themeButtonGroup.checkedId(),
        }

    #: Device roles, in the order their tabs appear.
    INSTANCE_ROLES = ('joystick', 'pedals', 'collective', 'trimwheel')

    #: The device selector for each role.
    DEVICE_SELECTORS = {'joystick': 'cb_select_j', 'pedals': 'cb_select_p',
                        'collective': 'cb_select_c', 'trimwheel': 'cb_select_t'}

    def selected_device(self, role):
        """The device currently picked for a role, if any."""
        combo = getattr(self, self.DEVICE_SELECTORS.get(role, ''), None)
        if combo is None or combo.currentIndex() < 0:
            return None
        model = combo.model()
        return model.data(model.index(combo.currentIndex(), 0),
                          Qt.ItemDataRole.UserRole)

    #: Shown on the Configurator settings a DirectInput device cannot use.
    VPCONF_BLOCKED_REASON = (
        'Not available: VPforce Configurator profiles do not apply to a generic DirectInput device')

    def device_is_dinput(self, role):
        """True when the device picked for a role is a generic DirectInput one.

        Keyed on the selection rather than on the connected device: the
        selection is what the settings being saved will apply to, and the
        device it names may not be plugged in yet.
        """
        device = self.selected_device(role)
        path = getattr(device, 'path', None) or b''
        return bytes(path).startswith(b'dinput:')

    def _update_vpconf_gates(self):
        """Grey out Configurator settings for any device that cannot use them.

        VPConf startup/exit/global-default profiles and the exit gain reset
        push VPforce Configurator gains, which a generic DirectInput device
        does not have.  The stored values are left alone so switching back
        to VPforce hardware restores them.
        """
        for (section, role), panel in self.instance_panels.items():
            if section != 'startup':
                continue
            blocked = self.device_is_dinput(role)
            panel.set_vpforce_features_enabled(
                not blocked, self.VPCONF_BLOCKED_REASON)

    def instance_pid(self, role):
        """A device's USB product ID, as the hex string it is stored as.

        Taken from the picked device, so a selection counts before it is
        saved.  Falling back to the stored value needs the str():
        SystemSettings.get() turns a numeric-looking value into an int, and
        validate_vpconf_profile() reads hex only from a str - so the pedals'
        '2052' would arrive as 2052 decimal and the profile be checked
        against PID 0804.  Returns '' when no device is configured for the
        role, which is not the same as this instance's own.
        """
        device = self.selected_device(role)
        if device is not None:
            return format(device.product_id, 'x')
        return str(G.system_settings.get(device_pid_key(role), '') or '').strip()

    def _configured_roles(self):
        """Devices this installation is set up for, so each gets a tab.

        Based on the stored device assignment rather than on which instances
        happen to be running: a device that is configured but not currently
        launched still needs to be configurable, and its settings are read at
        its next start.  This instance's own role is always included, so
        there is always at least one tab.
        """
        roles = [r for r in self.INSTANCE_ROLES
                 if G.system_settings.get(f'devpath_{r}', '')]
        if G.device_type not in roles:
            roles.insert(0, G.device_type)
        return roles

    def _build_instance_panels(self):
        """One panel per configured device, per section.

        The .ui carries an empty tab widget per page; the tabs themselves are
        filled here because which devices exist is only known at runtime.
        """
        self.instance_panels = {}      # (section, role) -> panel
        for section, fields, tabs in (
                ('system', SYSTEM_FIELDS, self.instance_tabs_system),
                ('startup', STARTUP_FIELDS, self.instance_tabs_startup)):
            for role in self._configured_roles():
                panel = InstanceSettingsPanel(
                    role, fields, browse_handler=self.browse_instance_vpconf)
                tabs.addTab(panel, device_display_name(role))
                self.instance_panels[(section, role)] = panel

    def panels_for(self, role):
        """Both of a device's panels."""
        return [p for (_, r), p in self.instance_panels.items() if r == role]

    def validate_instance_settings(self):
        """Check every device's Configurator profiles, not just this one's.

        A profile is validated against the device it will be pushed to, so a
        path that is right for the joystick is not accepted for the pedals.
        """
        for (section, role), panel in self.instance_panels.items():
            if section != 'startup':
                continue
            if panel.vpforce_blocked:
                # the settings are stored but inert; validating a profile the
                # device can never be sent would block saving for no reason
                continue
            pid = self.instance_pid(role)
            for field in panel.fields:
                if field.kind != 'path' or not panel.widgets[field.key].isChecked():
                    continue
                path = panel.widgets[field.path_key].path()
                label = field.label.strip(':')
                if not os.path.isfile(path):
                    QMessageBox.warning(
                        self, "Config Error",
                        f"{device_display_name(role)}: please select a valid "
                        f"'{label}' VPforce Configurator file")
                    return False
                if not pid:
                    # Nothing to check it against; the instance that owns the
                    # device knows its own product ID and validates on load.
                    logging.info(
                        f"No USB product ID configured for the "
                        f"{device_display_name(role)}; not validating {path}")
                elif not validate_vpconf_profile(path, pid, role):
                    return False
        return True

    def browse_instance_vpconf(self, role, field):
        """Pick a Configurator profile for one device's panel."""
        panel = self.instance_panels.get(('startup', role))
        if panel is None:
            return
        line_edit = panel.widgets[field.path_key]
        current = line_edit.path()
        starting_dir = os.path.dirname(current) \
            if os.path.exists(current) else os.getcwd()
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Choose a Configurator profile for the "
                  f"{device_display_name(role)}", starting_dir,
            "vpconf Files (*.vpconf)", options=QFileDialog.Option(0))
        if not file_path:
            return
        pid = self.instance_pid(role)
        if not pid or validate_vpconf_profile(file_path, pid, role):
            line_edit.setPath(file_path)

