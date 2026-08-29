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
import html
import logging
import os
import time

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIntValidator, QIcon, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QAbstractItemView, QButtonGroup, QDialog, QFileDialog, QMessageBox, QSizePolicy, QStyleOption, QTabWidget, QVBoxLayout

from . import globals as G
from . import utils
from .app_events import events as app_events
from .ui.Ui_SystemDialog import Ui_SystemDialog
from .TapStatusPanel import TapStatusPanel
from .tap_install import SIMS_BY_KEY, sim_status
from .InstanceSettingsPanel import (
    STARTUP_FIELDS, SYSTEM_FIELDS, InstanceSettingsPanel,
)
from .utils import (
    device_display_name, device_ident_key, device_ids_key, device_pid_key,
    directinput_selection_devices, format_usb_ids, recover_device_identity,
    validate_vpconf_profile, HiDpiPixmap,
)
from telemffb.hw.ffb_rhino import DeviceInfo, FFBRhino
from .custom_widgets import FFBDeviceListModel, LabeledToggle

def _as_bool(value):
    """A stored setting as a boolean.

    SystemSettings.get coerces the common spellings, but a value that
    reaches here any other way - a defaults dict, a raw QSettings read -
    can still be the string "false", and bool("false") is True: the
    obvious reading turns every saved-off switch on.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0")
    return bool(value)


def _same_hardware(a, b):
    """Whether two selector entries are one physical device.

    Normally the path settles it.  The exception is a VPforce device listed
    twice when the ``vpforce_as_dinput`` debug flag is on - once natively
    over HID, once as a DirectInput device - under two different paths (a
    HID path and an instance GUID) and with no serial on the DirectInput
    side.  Assigning the native entry to one slot and the DirectInput entry
    to another would be the same stick in two roles, so the two are matched
    on their USB ids when exactly one of them is the DirectInput listing.

    Two physically identical VPforce devices are still told apart: both
    are listed over HID, with their own paths and serials.
    """
    if a is None or b is None:
        return False
    path_a, path_b = getattr(a, 'path', None), getattr(b, 'path', None)
    if path_a is not None and path_a == path_b and \
            getattr(a, 'serial_number', None) == getattr(b, 'serial_number', None):
        return True
    di_a = bool(path_a) and bytes(path_a).startswith(b"dinput:")
    di_b = bool(path_b) and bytes(path_b).startswith(b"dinput:")
    if di_a == di_b:
        return False
    return (getattr(a, 'vendor_id', None), getattr(a, 'product_id', None)) == \
           (getattr(b, 'vendor_id', None), getattr(b, 'product_id', None)) and \
        getattr(a, 'vendor_id', None) not in (None, 0)


#: What the removal question can come back with.  Cancelling is not the
#: same as declining: one keeps the switch where the user just put it, the
#: other puts the switch back.
CLEANUP_REMOVE = "remove"
CLEANUP_LEAVE = "leave"
CLEANUP_CANCELLED = "cancelled"


class _LiveSettings:
    """Stored settings with the dialog's unsaved switches laid over the top.

    Thin on purpose: it answers ``get`` the way SystemSettings does and
    nothing else, so anything reading settings can be handed one without
    knowing a dialog is open.
    """

    def __init__(self, base, overrides):
        self._base, self._overrides = base, overrides

    def get(self, name, default=None, instance=None):
        if name in self._overrides:
            return self._overrides[name]
        return self._base.get(name, default, instance)


class _ImportedSettings:
    """A settings export file, readable the way SystemSettings is read.

    ``get`` resolves instance-scoped keys first and coerces stored
    booleans the same way, so the dialog's load paths work from a file
    exactly as they do from the registry.  Read-only: an import only
    populates the form, and Save is what writes.
    """

    def __init__(self, flat):
        self._flat = flat

    def get(self, name, default=None, instance=None):
        val = self._flat.get(f"{instance}/{name}") if instance else None
        if val is None:
            val = self._flat.get(name)
        if val is None:
            return default
        if val == "true":
            return 1
        if val == "false":
            return 0
        try:
            return int(val)
        except (TypeError, ValueError):
            return val


def _describe_setting_scope(elem):
    """A user-config entry's scope, phrased for a prompt.

    Presentation, so it lives with the prompt it serves rather than in
    the XML layer that produced the element.
    """
    if elem.tag == 'models':
        return elem.findtext('model') or 'unknown aircraft'
    if elem.tag == 'classSettings':
        return f"{elem.findtext('type') or 'unknown'} (class default)"
    return f"all {elem.findtext('sim') or 'sim'} aircraft (sim default)"


class SystemSettingsDialog(QDialog, Ui_SystemDialog):
    def __init__(self, parent=None,):
        super(SystemSettingsDialog, self).__init__(parent)
        # pending device path changes (only written on Save).  Created
        # before ANY wiring: state-derivation slots that read it fire as
        # early as load_settings' master-radio click.
        self._pending_devpaths = {}
        # while an import is populating the form, panels created on the fly
        # read the imported values instead of the store
        self._import_source = None
        self.setupUi(self)
        self.retranslateUi(self)
        self.setWindowTitle(f"System Settings ({G.device_type.capitalize()})")

        # The Devices / Launch Options area is built in code (dynamic device
        # rows do not fit a static .ui); the .ui keeps an empty host layout.
        # bind_to() re-creates every legacy widget name (cb_select_j,
        # rb_master_p, ...) as dialog attributes so the rest of this file -
        # and the test harness - work unchanged.
        from .DeviceCardsPanel import DeviceCardsPanel
        self.device_cards = DeviceCardsPanel(self)
        self.deviceCardsHostLayout.addWidget(self.device_cards)
        self.device_cards.bind_to(self)
        joy_card = self.device_cards.joystick_card
        joy_card.add_requested.connect(self._on_add_joystick_alt)
        joy_card.activate_requested.connect(self._on_activate_joystick_row)
        joy_card.remove_requested.connect(self._on_remove_joystick_alt)
        joy_card.primary_row.icon_changed.connect(
            lambda kind: self._on_device_icon_changed(
                joy_card.primary_row, kind))
        # the visible switches sync through the hidden legacy checkboxes;
        # a changed auto-launch state re-derives the card states (collapse,
        # master-radio gating, window-mode enabling)
        for suffix in 'jpct':
            getattr(self, f'cb_al_enable_{suffix}').toggled.connect(
                self.toggle_device_launch_widgets)

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

        # open on the tab the user last saved from (stored by name, so
        # tab reshuffles never send anyone to the wrong page)
        last = str(G.system_settings.get('sysDialogTab', '') or '')
        self.tabWidget.setCurrentIndex(0)
        for i in range(self.tabWidget.count()):
            if self.tabWidget.widget(i).objectName() == last:
                self.tabWidget.setCurrentIndex(i)
                break
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
        self._build_settings_menu()
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

        self.refresh_dinput_status()
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


        self._build_tap_panels()

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

        One definition, shared with startup: the listing is what a stored
        ``dinput:{GUID}`` slot is matched back to, so the two had better
        agree.  ``enabled`` overrides the stored setting so the dialog can
        re-list live as the box is ticked.
        """
        return directinput_selection_devices(G.system_settings, enabled)

    def populateUSBSelectors(self, dinput_enabled=None):
        # Populate the USB device selectors with currently connected devices
        devices = FFBRhino.enumerate()

        combo_boxes = [self.cb_select_j, self.cb_select_p, self.cb_select_c, self.cb_select_t]
        # kept so the tap code can ask what is connected without knowing
        # which widget holds which role
        self._device_combos = combo_boxes

        # Generic DirectInput FFB devices can serve any FFB role:
        # third-party FFB pedals exist (Brunner and friends), and a
        # modified DirectInput stick makes a collective or a trim wheel.
        # The joystick keeps its native X/Y; other roles get an axis
        # mapping when the hardware's FFB axis is not the one the role
        # addresses.
        extended_model = FFBDeviceListModel(
            list(devices) + self._enumerate_dinput_devices(dinput_enabled))
        for cb in combo_boxes:
            cb_model = extended_model
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
                # An unsaved pick outranks the saved one.  Re-listing
                # (the DirectInput toggle does it) used to restore from
                # the saved settings alone, so a device chosen a moment
                # ago vanished from the selector while still queued to be
                # written at Save - the selector said one thing and Save
                # did another.
                if saved_key in self._pending_devpaths:
                    saved_path = self._pending_devpaths[saved_key]
                else:
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


        # the joystick card's alternate rows share the joystick model and
        # restore from their own slots (devpath_joystick_2/_3)
        self._restore_joystick_alternates(extended_model)

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
                    # Clearing a slot is a device change like any other:
                    # a tap rule for what was there is stranded the same
                    # way, and used to be caught only at Save.
                    self._joystick_or_role_changed(changed_cb, role)
                # The user cleared this slot deliberately, so it will not
                # auto-launch.  Its window mode is a preference, not a
                # launch decision - it survives for whenever a device
                # returns.
                changed_cb._autolaunch_cb.setChecked(False)
                self.toggle_device_launch_widgets()
                return

            # check if any other combobox already has this device -
            # including the joystick card's alternate rows
            for other in combo_boxes + self.device_cards.alt_selectors():
                if other is changed_cb:
                    continue
                other_idx = other.currentIndex()
                if other_idx < 0:
                    continue
                other_dev = other.model().data(other.model().index(other_idx, 0), Qt.ItemDataRole.UserRole)
                if _same_hardware(other_dev, dev):
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
                self._joystick_or_role_changed(changed_cb, role)

            self.toggle_device_launch_widgets()

        for cb in combo_boxes:
            cb.blockSignals(False)

        # restores above ran with signals blocked; bring the ids
        # readouts in line with what is actually selected
        self.device_cards.refresh_ids_labels()

        # First-launch guidance for the non-VPforce user: their stick can
        # only appear through the DirectInput listing, and that switch
        # lives on the System page.  Shown only while the cards would
        # otherwise be a dead end - no VPforce hardware listed, nothing
        # configured, DirectInput off.
        dinput_on = (dinput_enabled if dinput_enabled is not None
                     else bool(G.system_settings.get('enableDirectInput',
                                                     False)))
        nothing_stored = not any(self._stored_or_pending(f'devpath_{r}')
                                 for r in self.INSTANCE_ROLES)
        self.device_cards.dinput_hint.setVisible(
            not devices and nothing_stored and not dinput_on)

        # connect signals (once - this method also runs on repopulate)
        if not getattr(self, '_device_signals_connected', False):
            for cb in combo_boxes:
                # use lambda binding to capture cb in the handler
                cb.currentIndexChanged.connect(lambda idx, _cb=cb: on_device_changed(idx, changed_cb=_cb))
            self._device_signals_connected = True

        # the FFB-axis choosers track whatever the restore above landed on
        self._refresh_all_axis_choices()

        # Additionally, ensure that when selection is cleared (index 0 / None) the setting is persisted.
        # The on_device_changed handler will call persist after committing the change, but if a selection
        # was reverted by conflict resolution it will also update the persisted value when appropriate.



    def _persist_combobox_selection(self, cb, role_name):
        """Stage a selector's device into the pending writes (devpath, and
        the display identity - ident and USB ids - remembered so rules and
        dialogs can name the device while it is unplugged)."""
        model = cb.model()
        sel_index = cb.currentIndex()
        dev = None
        if sel_index >= 0 and model is not None:
            dev = model.data(model.index(sel_index, 0),
                             Qt.ItemDataRole.UserRole)
        try:
            if dev is None:
                self._pending_devpaths[f'devpath_{role_name}'] = ''
                self._pending_devpaths[device_ident_key(role_name)] = ''
                self._pending_devpaths[device_ids_key(role_name)] = ''
                return
            dev_path = getattr(dev, 'path', None)
            if isinstance(dev_path, (bytes, bytearray)):
                try:
                    dev_path = dev_path.decode()
                except Exception:
                    dev_path = str(dev_path)
            self._pending_devpaths[f'devpath_{role_name}'] = dev_path
            self._pending_devpaths[device_ident_key(role_name)] = str(
                getattr(dev, 'ident', '') or '')
            # taken from the device, not parsed back out of its path: a
            # DirectInput device's path is a GUID and carries no ids
            self._pending_devpaths[device_ids_key(role_name)] = \
                format_usb_ids(getattr(dev, 'vendor_id', 0),
                               getattr(dev, 'product_id', 0))
        except Exception:
            logging.exception('Failed to persist devpath setting')

    #: Roles whose effects address one logical axis, remappable on a
    #: DirectInput device (the joystick stays native X/Y, always).
    AXIS_ROLES = {'pedals': 'p', 'collective': 'c', 'trimwheel': 't'}

    @staticmethod
    def _query_ffb_axes(guid):
        """Force-actuator axes for an UNOPENED device - a seam like
        _enumerate_dinput_devices: tests stub it, the real one loads
        DirectLink.  Safe while another process holds the device (object
        enumeration needs no acquisition)."""
        from telemffb.hw.ffb_dinput import DIBridge
        try:
            return DIBridge().query_ffb_axes(guid)
        except Exception:
            logging.debug('DirectLink axis query failed', exc_info=True)
            return []

    def _refresh_axis_choice(self, role):
        """Show the role's FFB-axis chooser when its selected device is
        DirectInput, filled with the axes the device reports; hide it
        otherwise.  The stored choice (or an unsaved pending one) is
        restored into it."""
        suffix = self.AXIS_ROLES.get(role)
        if suffix is None:
            return
        row = self.device_cards.cards[role].primary_row
        dev = getattr(self, f'cb_select_{suffix}').currentData()
        path = getattr(dev, 'path', b'') or b''
        if isinstance(path, (bytes, bytearray)):
            path = path.decode(errors='replace')
        if not str(path).startswith('dinput:'):
            row.show_axis_choice([])
            return
        from telemffb.hw.ffb_dinput import (axis_setting_key,
                                            invert_setting_key)
        names = self._query_ffb_axes(str(path)[len('dinput:'):])
        # the stored choice belongs to the device it was made FOR - the
        # one saved on disk.  A different selection starts from Auto,
        # uninverted: its axes are its own.  Cycling back to the saved
        # device restores the stored choice.
        saved_path = str(G.system_settings.get(f'devpath_{role}', '')
                         or '')
        if str(path) == saved_path:
            stored = str(self._stored_or_pending(axis_setting_key(role),
                                                 'auto') or 'auto')
            inverted = bool(self._stored_or_pending(
                invert_setting_key(role), False))
        else:
            stored, inverted = 'auto', False
        row.show_axis_choice(names, stored, inverted)

    @staticmethod
    def _request_axis_map_reapply_everywhere():
        """A saved axis/invert change reaches every running instance:
        this one directly, the children over IPC."""
        from telemffb.hw.ffb_rhino import HapticEffect
        request = getattr(HapticEffect.device,
                          'request_axis_map_reapply', None)
        if request:
            request()
        ipc = getattr(G, 'ipc_instance', None)
        if G.master_instance and ipc and G.launched_instances:
            ipc.send_broadcast_message('REAPPLY_AXIS_MAP')

    def _refresh_all_axis_choices(self):
        for role in self.AXIS_ROLES:
            try:
                self._refresh_axis_choice(role)
            except Exception:
                logging.exception(f'axis chooser refresh failed ({role})')

    def _slot_changed(self, cb, role):
        """A slot now holds something else - or nothing.  Record it, and
        let the tap say what that means while the change is still in front
        of the user."""
        # the state the dialog opened in, captured before the first
        # change - the only place the outgoing device's ids exist
        if self._tap_baseline is None:
            self._tap_baseline = self.tap_settings_view()
        self._persist_combobox_selection(cb, role)
        self._refresh_axis_choice(role)
        # the tap panels describe the devices as much as the folders,
        # so a new selection changes what they should be saying
        self.refresh_tap_panels()
        self._raise_tap_reconcile(self._tap_baseline,
                                  self.tap_settings_view())
        # a different question: not "this rule is stale" but "this
        # device cannot be driven at all without one"
        self._raise_tap_gaps()

    # ------------------------------------------------------------------
    # Joystick alternate devices.  The FIRST card row is always the active
    # device (devpath_joystick - everything downstream keys on it);
    # alternates store under devpath_joystick_2/_3 and become active by
    # SWAPPING their selection into row one.  A save with a swapped device
    # is then an ordinary device change: the live switch, tap reconcile
    # and status-panel refresh all follow from the existing machinery.
    # ------------------------------------------------------------------

    @staticmethod
    def _alt_role(slot: int) -> str:
        """The pseudo-role alternate slot N stores under (devpath_joystick_2
        and friends - the same key helpers apply)."""
        return f'joystick_{slot}'

    @staticmethod
    def _devicon_key(slot: int) -> str:
        return 'devicon_joystick' if slot == 1 else f'devicon_joystick_{slot}'

    def _stored_or_pending(self, key, default=''):
        if key in self._pending_devpaths:
            return self._pending_devpaths[key]
        return G.system_settings.get(key, default) or default

    def _select_devpath(self, cb, saved_path):
        """Point a selector at the item whose device path matches, quietly."""
        if not saved_path:
            return
        model = cb.model()
        for row in range(model.rowCount()):
            dev = model.data(model.index(row, 0), Qt.ItemDataRole.UserRole)
            path = getattr(dev, 'path', None)
            if path and path.decode() == str(saved_path):
                cb.blockSignals(True)
                cb.setCurrentIndex(row)
                cb._prev_index = row
                cb.blockSignals(False)
                return

    def _restore_joystick_alternates(self, joystick_model):
        """Build and fill the joystick card's alternate rows from the saved
        (or pending) state.  Runs from populateUSBSelectors, including on a
        repopulate - rows already on screen keep their place."""
        card = self.device_cards.joystick_card
        stored = 0
        for slot in (2, 3):
            if self._stored_or_pending(f'devpath_{self._alt_role(slot)}'):
                stored = slot - 1
        while len(card.alt_rows) < stored:
            self._create_joystick_alt_row()
        for i, row in enumerate(card.alt_rows):
            slot = i + 2
            cb = row.selector
            cb.blockSignals(True)
            cb.setModel(joystick_model)
            cb.setModelColumn(0)
            cb._ffb_device_model = joystick_model
            cb._prev_index = 0
            cb.blockSignals(False)
            self._select_devpath(
                cb, self._stored_or_pending(f'devpath_{self._alt_role(slot)}'))
            row._refresh_ids()
            row.set_device_icon(
                self._stored_or_pending(self._devicon_key(slot), 'stick')
                or 'stick')
        self.device_cards.joystick_card.primary_row.set_device_icon(
            self._stored_or_pending(self._devicon_key(1), 'stick') or 'stick')
        card.set_active_slot(1)

    def _create_joystick_alt_row(self):
        """One new alternate row, wired: selection changes stage into the
        pending writes, conflicts are refused, icons persist per device."""
        card = self.device_cards.joystick_card
        row = card.add_alt_row()
        row.selector.currentIndexChanged.connect(
            lambda _i, r=row: self._on_alt_device_changed(r))
        row.icon_changed.connect(
            lambda kind, r=row: self._on_device_icon_changed(r, kind))
        return row

    def _on_add_joystick_alt(self):
        row = self._create_joystick_alt_row()
        cb = row.selector
        model = getattr(self.cb_select_j, '_ffb_device_model', None)
        if model is not None:
            cb.blockSignals(True)
            cb.setModel(model)
            cb.setModelColumn(0)
            cb._ffb_device_model = model
            cb.setCurrentIndex(0)
            cb._prev_index = 0
            cb.blockSignals(False)

    def _alt_slot_of(self, row) -> int:
        return 2 + self.device_cards.joystick_card.alt_rows.index(row)

    def _on_alt_device_changed(self, row):
        cb = row.selector
        index = cb.currentIndex()
        dev = None
        if index >= 0:
            dev = cb.model().data(cb.model().index(index, 0),
                                  Qt.ItemDataRole.UserRole)
        if dev is not None:
            # one device, one slot - the same rule the role selectors keep
            others = [c for c in self._device_combos
                      if c is not cb] + [
                      c for c in self.device_cards.alt_selectors()
                      if c is not cb]
            for other in others:
                oidx = other.currentIndex()
                odev = other.model().data(
                    other.model().index(oidx, 0),
                    Qt.ItemDataRole.UserRole) if oidx >= 0 else None
                if odev is not None and _same_hardware(odev, dev):
                    QMessageBox.information(
                        self, 'Device Conflict',
                        'That device is already assigned to another slot.')
                    cb.blockSignals(True)
                    cb.setCurrentIndex(getattr(cb, '_prev_index', 0))
                    cb.blockSignals(False)
                    return
        cb._prev_index = index
        row._refresh_ids()
        if self.device_cards.joystick_card.active_row() is row:
            self._slot_changed(cb, 'joystick')
        self._persist_joystick_rows()

    def _joystick_or_role_changed(self, cb, role):
        """Route a role selector's change.  For the joystick, whether this
        is 'the active device changed' depends on where the marker sits;
        the whole card re-stages either way."""
        if role != 'joystick':
            self._slot_changed(cb, role)
            return
        card = self.device_cards.joystick_card
        if card.active_row() is card.primary_row:
            self._slot_changed(cb, 'joystick')
        self._persist_joystick_rows()

    def _on_device_icon_changed(self, row, kind):
        self._persist_joystick_rows()

    def _on_activate_joystick_row(self, slot: int):
        """The marker moved: the marked row's device becomes the active one
        (devpath_joystick) at Save.  Rows stay put; only the mapping onto
        storage slots changes - and downstream, this is an ordinary device
        change, so the tap notices and the live switch follow from it."""
        card = self.device_cards.joystick_card
        if slot == card.active_slot:
            return
        card.set_active_slot(slot)
        self._slot_changed(card.active_row().selector, 'joystick')
        self._persist_joystick_rows()
        self.toggle_device_launch_widgets()

    def _on_remove_joystick_alt(self, slot: int):
        card = self.device_cards.joystick_card
        was_active = (slot == card.active_slot)
        card.remove_alt_row(slot)     # resets the marker to row one if needed
        if was_active:
            # the active device went away with its row: row one takes over
            self._slot_changed(card.active_row().selector, 'joystick')
            self.toggle_device_launch_widgets()
        self._persist_joystick_rows()

    def _persist_joystick_rows(self):
        """Stage the whole joystick card: the marked row's device under
        'joystick' (the active device - what startup reads), the remaining
        rows in visual order under the _2/_3 slots, empties cleared.  Every
        card mutation funnels through here so the mapping can never drift.
        """
        card = self.device_cards.joystick_card
        rows = card.rows_by_priority()
        roles = ['joystick', self._alt_role(2), self._alt_role(3)]
        for i, role in enumerate(roles):
            key = self._devicon_key(1) if role == 'joystick' \
                else self._devicon_key(int(role.rsplit('_', 1)[1]))
            if i < len(rows):
                self._persist_combobox_selection(rows[i].selector, role)
                self._pending_devpaths[key] = rows[i].device_icon()
            else:
                self._pending_devpaths[f'devpath_{role}'] = ''
                self._pending_devpaths[device_ident_key(role)] = ''
                self._pending_devpaths[device_ids_key(role)] = ''
                self._pending_devpaths[key] = ''

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
        # Closing is cancelling: whatever the tap panels wrote goes back.
        # A wrapper installed and then backed out of would otherwise stay
        # active in a game folder, with the opt-in never saved and nothing
        # in TelemFFB saying a tap was live.
        self._discard_tap_writes()
        self.hide()
        event.ignore()

    def reject(self):
        # Escape and the close button both have to mean Cancel.  QDialog's
        # own reject would just hide the window, skipping closeEvent.
        self.close()

    def accept(self):
        self.hide()

    def _discard_tap_writes(self):
        failed = []
        for panel in self.tap_panels.values():
            failed += panel.undo_all()
        if failed:
            QMessageBox.warning(
                self, "DirectInput Tap Configuration",
                "Cancelled, but these could not be put back as they were - "
                "if a game is running, close it and check them:\n\n" +
                "\n".join(f"    {path}" for path in failed))

    def _build_settings_menu(self):
        """The File menu at the top of the dialog: import, export, reset.

        None of the three writes anything - each only populates (or, for
        export, reads) the form and store, and Save remains the single
        committing action.
        """
        self.settings_menu_bar = QtWidgets.QMenuBar(self)
        self.file_menu = self.settings_menu_bar.addMenu("&File")
        self.action_import = self.file_menu.addAction(
            "Import Settings...", self.import_settings)
        self.action_export = self.file_menu.addAction(
            "Export Settings...", self.export_settings)
        self.file_menu.addSeparator()
        self.action_reset = self.file_menu.addAction(
            "Reset to Defaults", self.reset_settings)
        self.layout().setMenuBar(self.settings_menu_bar)

    def export_settings(self):
        """Write every stored setting to a JSON file the user picks.

        The whole configuration - master and children alike - lives in
        this one store, so the file is a complete system-settings backup.
        Binary UI state (window geometry and the like) is not portable
        and is left out.
        """
        default_name = f"TelemFFB-settings-{time.strftime('%Y%m%d')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Settings", default_name,
            "TelemFFB settings (*.json)")
        if not path:
            return
        settings = {}
        for key in G.system_settings.allKeys():
            value = G.system_settings.value(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                settings[key] = value
        try:
            version = utils.get_version()
        except Exception:
            version = ''
        payload = {
            "application": "TelemFFB",
            "version": version,
            "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
            "settings": settings,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
        except OSError as e:
            QMessageBox.warning(self, "Export Settings",
                                f"Could not write the file:\n{e}")
            return
        QMessageBox.information(
            self, "Export Settings",
            f"{len(settings)} settings exported to:\n{path}")

    def import_settings(self):
        """Load a settings export into the form, committing nothing.

        The imported state flows through every guard Save already has -
        validation, the live device switch, restart notices, the tap
        reconcile - because it lands in the widgets exactly as if the
        user had clicked it all in.  Cancel discards it.

        Device identity keys are staged as pending writes so hardware
        that is not currently plugged in survives the round trip: the
        stored-but-unplugged machinery (retry ticker, panel labels)
        already handles the rest.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Settings", "", "TelemFFB settings (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            flat = data.get("settings", data) if isinstance(data, dict) \
                else None
            if not isinstance(flat, dict) or not flat:
                raise ValueError("no settings found in the file")
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import Settings",
                                f"Could not read the file:\n{e}")
            return

        source = _ImportedSettings(flat)
        # identity keys go through the pending-write path Save already
        # has; a devpath the file leaves out clears that slot rather than
        # keeping the old selection behind the imported ones
        for key, value in flat.items():
            if key.startswith(("devpath_", "devicon_", "devids_",
                               "devident_", "pid")):
                self._pending_devpaths[key] = value
        for role in list(self.INSTANCE_ROLES) + ['joystick_2', 'joystick_3']:
            self._pending_devpaths[f"devpath_{role}"] = \
                flat.get(f"devpath_{role}", "") or ""

        self._import_source = source
        try:
            # selectors restart from nothing so an unmatched (unplugged)
            # import shows no device rather than the previous selection
            selectors = list(self._device_combos) + [
                row.selector
                for row in self.device_cards.joystick_card.alt_rows]
            for cb in selectors:
                cb.blockSignals(True)
                cb.setCurrentIndex(0)
                cb._prev_index = 0
                cb.blockSignals(False)
            self.populateUSBSelectors()
            self.load_settings(source=source)
        finally:
            self._import_source = None

        QMessageBox.information(
            self, "Import Settings",
            f"Settings loaded from:\n{path}\n\n"
            "Nothing is applied yet - review the tabs and click Save, "
            "or Cancel to discard the import.")

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
        """The master instance launches itself, so its launch options hide
        (toggle_device_launch_widgets works that out from the state).

        The checkboxes deliberately keep their values: launch ignores the
        master role's flags anyway (check_launch_instance skips it), and
        clearing them meant an exploratory master change and back silently
        wiped a role's startup configuration.
        """
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
        """Work out each card's launch state from the current facts.

        The master's card hides its launch controls (it launches itself);
        a card whose auto-launch switch is off collapses to its header
        with the configuration kept underneath; a role with no device can
        neither launch nor be the master.  All derived from state rather
        than events, so no signal ordering can leave it stale.

        The collapse only applies while auto-launch is globally enabled:
        with it off the switches are inert, and collapsing would strand a
        role's device configuration out of reach.
        """
        self._refresh_instance_tabs()
        self._update_vpconf_gates()
        al_enabled = self.cb_al_enable.isChecked()
        master_role = self.MASTER_ROLE_IDS.get(self.master_button_group.checkedId())
        for role, (radio, al_box, *_rest) in self.ROLE_LAUNCH_WIDGETS.items():
            card = self.device_cards.cards[role]
            assigned = self.selected_device(role) is not None
            is_master = role == master_role
            launches = getattr(self, al_box).isChecked()
            card.set_launch_controls_visible(not is_master)
            # the switch works with no device picked: it is the door into
            # configuring an empty role (the card expands when switched
            # on).  Saving with it on and no device is refused by
            # validate_settings, so nothing half-configured escapes.
            card.launch_toggle.setEnabled(al_enabled)
            card.window_mode.setEnabled(al_enabled and launches)
            # the auto-launch switch gates the master radio only while the
            # global switch is on; master selection itself is independent
            # of auto-launch
            getattr(self, radio).setEnabled(
                assigned and (is_master or not al_enabled or launches))
            card.set_collapsed(
                (not is_master) and al_enabled and not launches)
        # cards expanding (or rows appearing) can push the content past
        # the window; grow to fit rather than letting the layout crush
        # the tallest card
        if self.isVisible():
            self._grow_to_fit()

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

    #: Each sim's opt-in toggle and the placeholder its status panel fills.
    #: Both are laid out in the .ui, so Designer shows the tab as it ships;
    #: only the panel, which depends on what is installed, is built here.
    #: IL-2's two titles share a tab and have a pair each.
    TAP_WIDGETS = {
        'DCS': ('enableTap_DCS', 'tapStatusHost_DCS'),
        'IL2': ('enableTap_IL2', 'tapStatusHost_IL2'),
        'IL2_K': ('enableTap_IL2_K', 'tapStatusHost_IL2_K'),
        'BMS': ('enableTap_BMS', 'tapStatusHost_BMS'),
    }

    def _build_tap_panels(self):
        """A status panel per sim, under its opt-in toggle.

        Each sits behind its own opt-in.  Most VPforce owners never need the
        tap - it is only required to render the *game's* effects, in the
        Game Managed (DirectInput Tap) spring mode - so presenting it as
        part of ordinary sim setup would suggest otherwise.  IL-2's two
        titles get one each, since they are separate installs with separate
        configs.
        """
        self.tap_panels = {}
        self.tap_enable_boxes = {}
        # kept so the tap code can read the sim switches as they stand in
        # the dialog rather than as they were last saved
        self._sim_enable_boxes = {}
        for name in ('enableDCS', 'enableIL2', 'enableBMS'):
            try:
                widget = getattr(self, name)
            except (AttributeError, RuntimeError):
                continue
            self._sim_enable_boxes[name] = widget
            # turning a sim on is a moment to say what its tap still needs,
            # and turning one off a moment to offer to take it out.
            # stateChanged, not toggled: these are LabeledToggles, which
            # forward that one and not Qt's.
            widget.stateChanged.connect(
                lambda state, n=name: self._on_sim_enabled(state, n))
        for key, (toggle_name, host_name) in self.TAP_WIDGETS.items():
            box = getattr(self, toggle_name, None)
            host = getattr(self, host_name, None)
            if box is None or host is None:
                continue
            sim = SIMS_BY_KEY[key]
            # Read here rather than relying on load_settings: that runs
            # before this method, when tap_enable_boxes is still empty,
            # so its restore loop had nothing to restore and every box
            # came up unchecked however the setting was saved.
            box.setChecked(_as_bool(
                G.system_settings.get(sim.tap_enable_key, False)))
            self.tap_enable_boxes[key] = box

            layout = QVBoxLayout(host)
            layout.setContentsMargins(0, 0, 0, 0)
            panel = TapStatusPanel(self._tap_status(key), host,
                                   devices=self.tap_devices)
            # after an install or removal, re-scan rather than trust the
            # panel's idea of what it just did
            panel.changed.connect(self.refresh_tap_panels)
            layout.addWidget(panel)
            self.tap_panels[key] = panel

            # stateChanged, not toggled: a LabeledToggle forwards that one,
            # and an int rather than a bool
            box.stateChanged.connect(
                lambda state, p=panel: p.setVisible(bool(state)))
            box.stateChanged.connect(
                lambda state, k=key: self._on_tap_opt_in(bool(state), k))
            panel.setVisible(box.isChecked())

    def tap_settings(self):
        """Settings with the dialog's unsaved sim switches over the top.

        Everything the tap decides - which sims to warn about, which to skip
        - keys off this rather than the registry, for the same reason the
        device list does: the user is changing these switches in the same
        visit, and answering from saved state would describe a machine they
        have already stopped configuring.
        """
        overrides = {}
        for key, box in self.tap_enable_boxes.items():
            overrides[SIMS_BY_KEY[key].tap_enable_key] = box.isChecked()
        for name, box in self._sim_enable_boxes.items():
            overrides[name] = box.isChecked()
        return _LiveSettings(G.system_settings, overrides)

    def _on_sim_enabled(self, state, setting_name):
        """A sim switch moved.  One switch can cover more than one title -
        IL-2's two share theirs - so both are considered."""
        self.refresh_tap_panels()
        if state:
            self._raise_tap_gaps()
            return
        for sim in SIMS_BY_KEY.values():
            if sim.enable_key == setting_name:
                self._offer_tap_cleanup(sim.key, sim_switched_off=True)

    def _on_tap_opt_in(self, checked, sim_key=None):
        """Opting in asks what is still missing; opting out asks what to
        take away.  Two different questions, and neither is worth asking in
        the other direction."""
        self.refresh_tap_panels()
        if checked:
            self._raise_tap_gaps()
        elif sim_key is not None:
            self._offer_tap_cleanup(sim_key)

    def tap_devices(self):
        """The devices configured right now, unsaved picks included.

        Everything about the tap keys off this rather than the registry.  A
        user does all of their changing in one visit - pick a device, then go
        set up the sim that uses it - so reading only what has been saved
        would offer them the device they just replaced, and make them save
        and reopen the dialog to get an answer that is already on screen.
        """
        from telemffb.tap_install import configured_devices

        return configured_devices(self.tap_settings_view())

    def tap_settings_view(self):
        """The device settings as they stand, unsaved picks included."""
        from telemffb.utils import DEVICE_ROLES

        merged = {}
        for role in DEVICE_ROLES:
            for key in (f'devpath_{role}', device_ident_key(role),
                        device_ids_key(role)):
                merged[key] = G.system_settings.get(key, '') or ''
        merged.update(self._pending_devpaths)
        return self._with_known_identity(merged)

    def _connected_devices(self):
        """Everything enumerated in the selectors right now."""
        devices = []
        for combo in self._device_combos:
            model = combo.model()
            if model is None:
                continue
            for row in range(model.rowCount()):
                device = model.data(model.index(row, 0),
                                    Qt.ItemDataRole.UserRole)
                if device is not None:
                    devices.append(device)
        return devices

    def _with_known_identity(self, view):
        """Fill in what a slot's settings predate storing.

        The same recovery startup performs, repeated here because this
        dialog can see devices startup could not: a DirectInput device is
        enumerated only when the selector asks for it.
        """
        view.update(recover_device_identity(view, self._connected_devices()))
        return view

    def _tap_status(self, sim_key):
        """One sim's status, using whatever this dialog currently shows.

        The dialog wins over the stored settings, for the path and for the
        devices alike: a user who has just corrected either should see the
        effect before saving it.
        """
        from telemffb.tap_config import read, stale_tap_rules
        from telemffb.tap_install import read_config

        sim = SIMS_BY_KEY[sim_key]
        configured = None
        field = {'IL2': getattr(self, 'pathIL2', None),
                 'IL2_K': getattr(self, 'pathIL2_K', None)}.get(sim_key)
        if field is not None:
            configured = field.text().strip() or None
        if configured is None and sim.settings_key:
            configured = G.system_settings.get(sim.settings_key, '') or None

        status = sim_status(sim, configured)
        # sim_status knows nothing about what is configured, so the drift
        # line has to be filled in here or it never appears at all
        if any(t.has_config for t in status.targets):
            config = read_config(status)
            if config is not None:
                status.stale_rules = stale_tap_rules(read(config),
                                                     self.tap_devices())
        return status

    def tap_statuses(self):
        """Every sim's status as this dialog sees it.

        Handed to reconcile rather than letting it resolve the sims itself:
        it would go back to the registry and the saved settings, and miss a
        path the user has typed into a field but not saved - which is the
        same visit in which they are changing devices.
        """
        if not self.tap_panels:
            # nothing better to offer; let reconcile resolve the sims itself
            return None
        return [self._tap_status(key) for key in self.tap_panels]

    def refresh_tap_panels(self):
        """Re-scan the game folders and redraw.

        Cheap - a handful of stat calls and, where a wrapper is present, one
        file read - so it runs whenever the dialog is shown rather than
        leaving stale state on screen after the user installs something.
        """
        for key, panel in getattr(self, 'tap_panels', {}).items():
            panel.set_status(self._tap_status(key))
            # Visibility normally follows the switch's toggled signal, but
            # anything that moves a switch with signals blocked - restoring
            # one after a cancelled question - would otherwise leave a
            # ticked box above an empty space.
            box = self.tap_enable_boxes.get(key)
            if box is not None:
                panel.setVisible(box.isChecked())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_tap_panels()
        QtCore.QTimer.singleShot(0, self._grow_to_fit)

    def _grow_to_fit(self):
        """Make the window at least as tall as its content needs.

        The .ui's opening size is a preference, not a promise: with real
        font metrics the Devices tab can need more height than it, and a
        programmatic resize below the layout minimum is allowed - the
        layout then compresses the tallest card and paints its selector
        clipped, until the user drags the frame and the window system
        enforces the real minimum.  Enforce it up front (and again when
        the content grows, e.g. cards expanding), capped to the screen.
        Never shrinks the window.
        """
        need = self.minimumSizeHint()
        width, height = self.width(), self.height()
        grown_w = max(width, need.width())
        grown_h = max(height, need.height())
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            grown_w = min(grown_w, avail.width())
            grown_h = min(grown_h, avail.height())
        if (grown_w, grown_h) != (width, height):
            self.resize(grown_w, grown_h)

    #: A beta fuse this close is worth flagging before launch day.
    BRIDGE_EXPIRY_WARN_DAYS = 14

    def refresh_dinput_status(self):
        """Say which bridge utility is installed, and whether it is
        healthy - the toggle alone cannot answer "is it there, which
        build, and how long is this beta good for?".

        Shown only while DirectInput support is switched ON: to everyone
        else the bridge is a utility they have never heard of, and a
        line about its absence would read as something being wrong.
        """
        from telemffb.hw.ffb_dinput import BRIDGE_DOWNLOAD_LOCATION, bridge_status
        if not self.cb_enable_dinput.isChecked():
            self.lab_dinput_status.setVisible(False)
            return
        self.lab_dinput_status.setVisible(True)
        try:
            status = bridge_status()
        except Exception:
            logging.exception('DInput bridge status check failed')
            self.lab_dinput_status.setVisible(False)
            return
        attention = True
        if not status.installed:
            text = ("DirectLink: not installed - available from "
                    f"{BRIDGE_DOWNLOAD_LOCATION}")
        elif status.problem:
            text = (f"DirectLink {status.version or '(unknown build)'}"
                    f": {status.problem} - a current build is available from "
                    f"{BRIDGE_DOWNLOAD_LOCATION}")
        elif not status.version:
            text = "DirectLink: installed (build identity unavailable)"
            attention = False
        elif status.days_left is None:
            text = f"DirectLink {status.version}: installed"
            attention = False
        else:
            days = status.days_left
            when = (f"expires {status.expires} "
                    f"({days} day{'' if days == 1 else 's'} left)")
            text = f"DirectLink {status.version}: beta build, {when}"
            attention = days <= self.BRIDGE_EXPIRY_WARN_DAYS
        self.lab_dinput_status.setText(text)
        self.lab_dinput_status.setStyleSheet(
            f"color: {self._attention_color().name()};" if attention else "")

    def _attention_color(self):
        """An amber that reads on either theme (the palette has no
        'warning' role, and a fixed one washes out on one of them)."""
        window = self.palette().color(QtGui.QPalette.ColorRole.Window)
        return (QtGui.QColor(0xE6, 0xA8, 0x23) if window.lightness() < 128
                else QtGui.QColor(0xA8, 0x66, 0x00))

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
        # the toggle is the moment a user who just installed the
        # bridge finds out whether it took
        self.refresh_dinput_status()
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

    #: The Window Mode tri-state, stored as windowMode{Role}.  The legacy
    #: booleans (startMin/startHeadless) are still written in step - the
    #: launch code reads them - but the tri-state is what LOADS, so
    #: 'Normal' survives a reopen (as two False booleans it would be
    #: indistinguishable from the never-configured default).
    WINDOW_MODES = ('headless', 'minimized', 'normal')

    def _window_mode_value(self, suffix: str) -> str:
        if getattr(self, f'cb_headless_{suffix}').isChecked():
            return 'headless'
        if getattr(self, f'cb_min_enable_{suffix}').isChecked():
            return 'minimized'
        return 'normal'

    def _load_window_mode(self, suffix: str, role_cap: str, settings_dict):
        mode = settings_dict.get(f'windowMode{role_cap}', None)
        if mode not in self.WINDOW_MODES:
            # Legacy store: the old checkboxes were mutually exclusive, so
            # 'neither' technically meant a normal window - but it is also
            # indistinguishable from the untouched default, and almost
            # nobody ran children windowed on purpose.  It reads as
            # Headless; a deliberate Normal is a one-time re-pick, after
            # which the tri-state key preserves it.
            mode = 'minimized' if settings_dict.get(
                f'startMin{role_cap}', False) else 'headless'
        getattr(self, f'cb_headless_{suffix}').setChecked(mode == 'headless')
        getattr(self, f'cb_min_enable_{suffix}').setChecked(mode == 'minimized')

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
            # per-sim DirectInput Tap opt-in, built in code rather than the .ui
            **{SIMS_BY_KEY[k].tap_enable_key: b.isChecked()
               for k, b in self.tap_enable_boxes.items()},
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
            'windowModeJoystick': self._window_mode_value('j'),
            'windowModePedals': self._window_mode_value('p'),
            'windowModeCollective': self._window_mode_value('c'),
            'windowModeTrimWheel': self._window_mode_value('t'),
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

        # What only takes effect at the next start, as it is about to be
        # saved.  Compared after the write, below: a save the validation
        # refuses must not leave the user restarting for changes never made.
        saved_al_dict = {key: global_settings_dict[key]
                         for _, keys in self.RESTART_GROUPS for key in keys}

        if not self.validate_settings():
            return

        # What each slot held on disk before this save - the reference for
        # deciding which devices actually changed and should switch live.
        # Compared on stored state rather than on the dialog's pending dict
        # so a change-then-change-back never triggers a needless switch.
        pre_devpaths = {role: G.system_settings.get(f'devpath_{role}', '') or ''
                        for role in self.INSTANCE_ROLES}
        # every joystick slot, alternates included: aircraft settings
        # reference these by devpath, and a replaced device strands those
        # references (offered for rewrite after the save, below)
        pre_joystick_slots = {
            suffix: str(G.system_settings.get(
                f'devpath_joystick{suffix}', '') or '')
            for suffix in ('', '_2', '_3')}
        # ...and their names, for the reconcile prompt: by the time it
        # asks, the stored idents already describe the newcomers
        pre_joystick_idents = {
            suffix: str(G.system_settings.get(
                f'devident_joystick{suffix}', '') or '')
            for suffix in ('', '_2', '_3')}
        # the before-snapshot for the device_config_changed signal: every
        # role plus the joystick alternates, by settings key
        pre_config = {f'devpath_{role}': path
                      for role, path in pre_devpaths.items()}
        pre_config.update({f'devpath_joystick{suffix}': path
                           for suffix, path in pre_joystick_slots.items()})

        for k,v in global_settings_dict.items():
            G.system_settings.setValue(f"{k}", v)

        # each device's own settings, from its panels
        for panel in self.instance_panels.values():
            panel.save(G.system_settings)

        # Persist any pending devpath selections that were changed while the dialog was open
        # What the slots held when the dialog opened, captured before the
        # first change was made.  None means no device was ever changed.
        baseline = self._tap_baseline
        # Identity we had to recover from the connected hardware is
        # written too, so a settings base that predates it being stored
        # heals itself after one save.  Only keys that are empty on disk:
        # this fills gaps, it does not overwrite the user's own selection.
        current = self.tap_settings_view()
        for key, value in current.items():
            if key.startswith(('devids_', 'devident_')) and value and \
                    not G.system_settings.get(key, ''):
                self._pending_devpaths.setdefault(key, value)

        # the FFB-axis choice rides the same flush; None means the
        # chooser is hidden (not a DirectInput device) and the stored
        # value, if any, is left alone for the device that set it
        try:
            from telemffb.hw.ffb_dinput import (axis_setting_key,
                                                invert_setting_key)
            for role in self.AXIS_ROLES:
                row = self.device_cards.cards[role].primary_row
                value = row.axis_choice_value()
                if value is not None:
                    self._pending_devpaths[axis_setting_key(role)] = value
                inverted = row.axis_invert_value()
                if inverted is not None:
                    self._pending_devpaths[invert_setting_key(role)] = \
                        inverted
        except Exception:
            logging.exception('Failed to record FFB axis choices')

        try:
            for k, v in self._pending_devpaths.items():
                G.system_settings.setValue(k, v)
        except Exception:
            logging.exception('Failed to write pending devpath settings')

        # a changed FFB axis map applies live: this instance re-resolves on
        # its next poll tick, and every child is told to do the same (each
        # re-reads its OWN settings, so an unchanged map is a no-op; the
        # re-apply recreates the device's effects, since DirectInput fixes
        # an effect's axes at creation)
        try:
            self._request_axis_map_reapply_everywhere()
        except Exception:
            logging.exception('Failed to request live axis-map re-apply')

        # A swapped device leaves any tap config pointing at hardware that is
        # no longer there, which fails silently in both directions.  Compared
        # against the baseline rather than against whatever keys happen to be
        # pending: the outgoing device's ids are only knowable from the state
        # the dialog opened in.
        try:
            if baseline is not None:
                self._offer_tap_reconcile(baseline, current)
            self._apply_tap_gaps()
            self._apply_tap_cleanup()
        except Exception:
            logging.exception('Failed to check DirectInput tap configs after a '
                              'device change')

        try:
            self._offer_aircraft_device_reconcile(pre_joystick_slots,
                                                  pre_joystick_idents)
        except Exception:
            logging.exception('Failed to check aircraft device references '
                              'after a device change')

        # remembered by name: the dialog reopens on the tab the user
        # last saved from
        try:
            G.system_settings.setValue(
                'sysDialogTab', self.tabWidget.currentWidget().objectName())
        except Exception:
            pass

        G.sim_listeners.restart_all()

        if G.master_instance and G.launched_instances:
            G.ipc_instance.send_broadcast_message("RESTART SIMS")

        # A changed device takes effect immediately: this instance
        # re-acquires its own, a running child is told to re-acquire via
        # IPC, and an instance that is not running simply reads the new
        # selection whenever it launches.
        try:
            self._apply_device_changes_live(pre_devpaths)
        except Exception:
            logging.exception('Live device switch after save failed')

        # Everything UI that follows a device-configuration change - the
        # status-icon labels, the aircraft settings form's Device section,
        # whatever subscribes next - hears about it through one signal
        # rather than a hand-wired call per consumer here.
        try:
            after_devpaths = {key: str(G.system_settings.get(key, '') or '')
                              for key in pre_config}
            app_events().device_config_changed.emit(dict(pre_config),
                                                    after_devpaths)
        except Exception:
            logging.exception('Device configuration change '
                              'notification failed')

        # the panels' own writes were meant after all
        for panel in self.tap_panels.values():
            panel.commit()

        # Said once the settings are on disk, naming what changed; and the
        # baseline moves to what was saved, so saving again in the same
        # dialog compares against this save rather than the one it opened with.
        changed = self._restart_worthy_changes(saved_al_dict)
        self.current_al_dict = saved_al_dict
        if changed:
            what = ", ".join(changed)
            QMessageBox.information(
                self, "Restart Required",
                f"{what[0].upper()}{what[1:]} changed. Restart TelemFFB for "
                "this to take effect.")

        # adjust logging level (this instance's own panel):
        own = self.instance_panels.get(('system', G.device_type))
        ll = own.widgets['logLevel'].currentText() if own else 'INFO'
        if ll == "INFO":
            logging.getLogger().setLevel(logging.INFO)
        elif ll == "DEBUG":
            logging.getLogger().setLevel(logging.DEBUG)

        self.accept()

    #: Which devices need the tap, and for what.  Stated wherever the tap
    #: comes up, because the distinction decides whether a missing rule is a
    #: preference or a broken setup.
    TAP_REQUIREMENT = (
        "Generic DirectInput devices require the tap for all TelemFFB "
        "effects. VPforce devices need it only for the "
        "'Game Managed (DirectInput Tap)' spring mode.")

    #: Device changes the user has been told about, as signatures - the set
    #: of slots that differ from the state the dialog opened in.  Cycling
    #: through devices to see what is there lands on the same signature and
    #: is not mentioned again; a different change is a new notice.  Rebound,
    #: never mutated: the class-level default is shared.
    _tap_reconcile_seen = frozenset()

    #: DirectInput devices the user has been told have no rule, by their
    #: ids.  Same idea: once per device, not once per toggle.
    _tap_gaps_seen = frozenset()

    #: The device settings as the dialog opened, captured lazily before the
    #: first change.  None means no device has been changed.
    _tap_baseline = None

    #: The device selectors, once they have been built.  A class-level
    #: default because several things ask what is connected, and they can
    #: run before the selectors exist.
    _device_combos = ()

    #: Per-sim DirectInput Tap opt-in boxes, and the sim switches, once built.
    #: Class-level for the same reason: loading settings and saving both
    #: read them, and either can run before the tap panels are built.
    tap_enable_boxes = {}
    _sim_enable_boxes = {}

    #: Sims the user has been asked about clearing, and those they agreed
    #: to clear.  Asked once each; acted on at save, and only for sims whose
    #: switch is still off by then.
    _tap_cleanup_asked = frozenset()
    _tap_cleanup_agreed = frozenset()

    def _raise_tap_reconcile(self, before, after):
        """Tell the user straight away that a tap config is now stranded, and
        that the update is staged.

        Raised here rather than only at save because this is the moment the
        change makes sense to them - by the time they close the dialog they
        may not connect a notice about DCS to a combo box they touched ten
        minutes ago.

        A notice, not a question.  Nothing is written yet - the update is
        applied when the settings are saved, and backing out of the dialog
        leaves the game folders exactly as they were - so Cancel is how to
        decline.  A "No" button here would have had exactly one effect:
        leaving a config that names hardware no longer in the slot.
        """
        from telemffb.tap_reconcile import device_changes, pending_reconcile

        changes = device_changes(before, after)
        signature = self._change_signature(changes)
        if signature in self._tap_reconcile_seen:
            return          # already said; saying it again is nagging
        items = pending_reconcile(changes, self.tap_settings(),
                                  self.tap_statuses())
        if not items:
            # logged because the silence is deliberate and indistinguishable
            # from a fault: a device did change, and no sim was tapping it
            if changes:
                logging.info(
                    "DirectInput tap: %s changed but no enabled sim has a tap rule "
                    "for the outgoing device - nothing to reconcile",
                    ", ".join(c.role for c in changes))
            return

        QMessageBox.information(
            self, "DirectInput Tap Configuration",
            self._reconcile_summary(items) +
            "\n\nThe update is staged and will be written when you save. "
            "Cancel the dialog to keep the files as they are.")
        self._tap_reconcile_seen = self._tap_reconcile_seen | {signature}

    @staticmethod
    def _change_signature(changes):
        """What a reconcile question is about: which slots changed, from
        what, to what.  The key an answer is remembered under."""
        return frozenset((c.role, c.was, c.now.key if c.now else None)
                         for c in changes)

    @staticmethod
    def _reconcile_summary(items):
        """The question in three parts: what changed, which sims' configs
        still say the old thing, and what happens until they are updated.

        One change is the normal case - the joystick went from A to B - and
        every sim's config says the same about it, so that is said once and
        the sims are simply listed.  Distinct changes (two slots, or one
        cleared) each get their own paragraph.  Items are per file, but a
        sim's two files almost always agree, so sims are listed by name.
        """
        def old_label(item):
            key = ", ".join(sorted({r.key for r in item.obsolete}))
            return f"{item.was_ident} ({key})" if item.was_ident and key else \
                (item.was_ident or key)

        def new_label(item):
            return (SystemSettingsDialog._rule_owner(item.replacement)
                    if item.replacement else None)

        groups = {}      # (role, old, new) -> sim names, in order, once
        for item in items:
            sims = groups.setdefault((item.role, old_label(item), new_label(item)), [])
            if item.sim.name not in sims:
                sims.append(item.sim.name)

        paragraphs = []
        for (role, old, new), sims in groups.items():
            slot = device_display_name(role).lower() if role else "device"
            if new:
                what = f"The {slot} changed from {old} to {new}."
            else:
                what = f"The {slot} slot was cleared; it held {old}."
            where = ("The DirectInput Tap configuration for these sims still "
                     "names the old device:" if len(sims) > 1 else
                     "The DirectInput Tap configuration for this sim still "
                     "names the old device:")
            paragraphs.append(what + " " + where + "\n\n" +
                              "\n".join(f"    {name}" for name in sims))

        replaced = any(new for (_, _, new) in groups)
        until = ("Until it is updated, those games keep tapping the old device "
                 "and leave the new one alone - TelemFFB has nothing to render "
                 "for it." if replaced else
                 "Until it is updated, those games keep tapping a device that "
                 "is no longer configured.")
        restart = ("A sim reads this configuration as it starts, so restart "
                   "any of these sims that is running now for the change to "
                   "take effect.")
        return "\n\n".join(paragraphs) + "\n\n" + until + "\n\n" + restart

    @staticmethod
    def _rule_owner(line):
        """'SideWinder (045E:001B)' from a rule line, or the bare key.

        The ids are what the rule is keyed on; the name is what the user
        recognizes.  A line written by TelemFFB carries both."""
        key, _, rest = line.partition("=")
        comment = rest.partition(";")[2].strip()
        name = comment.split(" (")[0].strip() if comment else ""
        return f"{name} ({key.strip()})" if name and name != "unnamed" \
            else key.strip()

    def _offer_tap_cleanup(self, sim_key, sim_switched_off=False):
        """Opting a sim out - offer to take the tap back out of it.

        Asked rather than done: the wrapper and its config live in a game
        folder, and leaving them is a reasonable choice - the wrapper is
        inert while TelemFFB is not running, and the user may be turning the
        switch off only to stop TelemFFB managing it.

        Only ever removes what is ours.  A config we generated goes; a
        config that was somebody else's keeps everything but the rules we
        added; a dinput8.dll that is not ours is never touched.

        Recorded now and done on save, like every other write a settings
        change implies.  Nothing in a game folder should disappear because
        of a switch the user then backed out of.
        """
        from telemffb.tap_reconcile import plan_tap_cleanup

        if sim_key in self._tap_cleanup_asked:
            return
        plan = plan_tap_cleanup(self._tap_status(sim_key))
        if plan.empty:
            return
        # rebound rather than mutated: the class-level default is shared,
        # and adding to it would leak between dialogs
        self._tap_cleanup_asked = self._tap_cleanup_asked | {sim_key}

        opening = (f"{plan.sim.name} is being turned off, but its tap is "
                   "still set up. Remove it?" if sim_switched_off else
                   f"The tap is still set up for {plan.sim.name}. "
                   "Remove it?")
        answer = self._ask_with_preview(
            self._cleanup_message(plan, opening), plan)

        if answer == CLEANUP_CANCELLED:
            # They dismissed the question, which is an answer about the
            # switch and not about the files: put it back where it was and
            # forget we asked, so flipping it again asks again.
            self._tap_cleanup_asked = self._tap_cleanup_asked - {sim_key}
            self._restore_switch(sim_key, sim_switched_off)
            return

        if answer == CLEANUP_REMOVE:
            self._tap_cleanup_agreed = self._tap_cleanup_agreed | {sim_key}
            # agreeing to take the tap out is opting the sim out of it, and
            # the removal at save is keyed on that switch being off
            box = self.tap_enable_boxes.get(sim_key)
            if box is not None:
                box.setChecked(False)

    @staticmethod
    def _leaving_it(plan):
        """What declining actually means, which is not always "nothing".

        RequireTelemFFB gates only the tap and sink rules; block and scale
        rules apply whatever TelemFFB is doing, and so does any reordering.
        Saying a leftover config is harmless when it is still shaping the
        game's force feedback would be a comfortable lie of exactly the kind
        this feature exists to avoid.
        """
        if not plan.still_acts:
            return ("\n\nLeaving it in place is harmless - the wrapper does "
                    "nothing while TelemFFB is not running.")
        return ("\n\nIf you leave it, note that " +
                "; and ".join(plan.still_acts) + ".")

    def _cleanup_message(self, plan, opening):
        """The removal question, as HTML.

        Rich text because of the link: a message box switches format the
        moment it sees an anchor, and plain newlines would then collapse.
        Worth the escaping - everything we say about a config is a summary,
        and someone deciding whether to delete it should be able to read the
        file itself without cancelling out of the question first.
        """
        from telemffb.tap_install import (config_label, config_link,
                                          config_paths)

        doing = "".join(f"<li>{html.escape(line)}</li>"
                        for line in plan.describe())
        body = (f"{html.escape(opening)}<br><br>This would:<ul>{doing}</ul>"
                f"{html.escape(self._leaving_it(plan).strip())}"
                " The change is made when you save.")
        # Named by folder: a sim can hold two configs that differ, and two
        # links both reading "open dinput8.ini" say nothing about which is
        # which - the exact question someone about to delete one has.
        links = "<br>".join(
            config_link(p, config_label(p, plan.status.root))
            for p in config_paths(plan.status))
        return body + (f"<br><br>{links}" if links else "")

    def _ask_with_preview(self, message, plan):
        """The removal question, with a look at the files on offer.

        Returns one of CLEANUP_REMOVE, CLEANUP_LEAVE or CLEANUP_CANCELLED.

        A message box closes on whichever button is pressed, so previewing
        means asking again afterwards - which is nothing worse than the
        question reappearing where the user left it.
        """
        from telemffb.tap_reconcile import cleanup_preview
        from telemffb.TapDiffDialog import TapDiffDialog

        while True:
            box = QMessageBox(self)
            box.setWindowTitle("DirectInput Tap Configuration")
            box.setText(message)
            # Three outcomes, not two.  Leaving the files and changing your
            # mind about the switch are different answers, and closing the
            # window has to mean the second - otherwise dismissing a
            # question silently keeps half of what it asked about.
            remove = box.addButton("Remove", QMessageBox.ButtonRole.YesRole)
            leave = box.addButton("Leave it", QMessageBox.ButtonRole.NoRole)
            cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            look = box.addButton("Preview changes...",
                                 QMessageBox.ButtonRole.ActionRole)
            box.setDefaultButton(leave)
            box.setEscapeButton(cancel)
            box.exec()

            clicked = box.clickedButton()
            if clicked is look:
                TapDiffDialog("DirectInput Tap - proposed changes",
                              cleanup_preview(plan), self).exec()
                continue
            if clicked is remove:
                return CLEANUP_REMOVE
            if clicked is leave:
                return CLEANUP_LEAVE
            return CLEANUP_CANCELLED

    def _restore_switch(self, sim_key, sim_switched_off):
        """Put back the switch whose flick raised the question.

        Signals blocked: turning it back on would otherwise run the opt-in
        handler and ask a fresh question about a sim nobody has changed.
        """
        widgets = []
        if sim_switched_off:
            sim = SIMS_BY_KEY[sim_key]
            widget = self._sim_enable_boxes.get(sim.enable_key)
            if widget is not None:
                widgets.append(widget)
        else:
            box = self.tap_enable_boxes.get(sim_key)
            if box is not None:
                widgets.append(box)

        for widget in widgets:
            widget.blockSignals(True)
            widget.setChecked(True)
            widget.blockSignals(False)
        # blocked signals mean nothing downstream ran, so the panels are
        # re-synced from the switches rather than by the toggle handler
        self.refresh_tap_panels()

    def _apply_tap_cleanup(self):
        """Undo the tap for sims the user turned off and agreed to clear.

        The toggle is read again here rather than trusted from when they
        answered: turning it back off and on again before saving must not
        leave a deletion queued against a sim that is switched on.
        """
        from telemffb.tap_reconcile import apply_tap_cleanup, plan_tap_cleanup

        wanted = [key for key in self._tap_cleanup_agreed
                  if key in self.tap_enable_boxes
                  and not self.tap_enable_boxes[key].isChecked()]
        if not wanted:
            return

        plans = [plan_tap_cleanup(self._tap_status(key)) for key in wanted]
        failures = [o for o in apply_tap_cleanup(plans) if not o.ok]
        if failures:
            QMessageBox.warning(
                self, "DirectInput Tap Configuration",
                "Some of it could not be removed:\n\n" +
                "\n".join(f"    {o.directory}: {o.detail}" for o in failures))

    def _raise_tap_gaps(self):
        """Tell the user a DirectInput device has no way to be driven at all,
        and that the missing rules are staged.

        Not the same problem as a stale rule.  Nothing is wrong with the
        config - there simply is no rule where one is mandatory, because
        TelemFFB reaches a generic DirectInput device only through the tap.
        Without it the game keeps the device and TelemFFB stays silent, and
        nothing anywhere reports a fault.

        Once per device, like the reconcile notice is once per change, and
        written at save for the same reason.  Where a reconcile the user has
        already been told about will write this device's rule into a sim,
        that sim is not a gap and is not mentioned - one change, one notice,
        not two dialogs saying the same thing in different words.  Where the
        tap is not installed at all, the notice says where to do that; a
        rule cannot be added to a wrapper that is not there.
        """
        from telemffb.tap_reconcile import missing_tap_rules

        closed = self._gaps_a_reconcile_will_close()
        gaps = [g for g in missing_tap_rules(self.tap_devices(),
                                             self.tap_settings(),
                                             self.tap_statuses())
                if (g.sim.key, g.device.key) not in closed
                and g.device.key not in self._tap_gaps_seen]
        if not gaps:
            return

        lines = []
        for gap in gaps:
            why = ("no tap rule" if gap.fixable
                   else "the tap is not set up for this sim")
            lines.append(f"    {gap.sim.name}:  {why}")
        names = sorted({g.device.ident or g.device.key for g in gaps})

        body = (f"{', '.join(names)} is a generic DirectInput device, so "
                "TelemFFB cannot render anything for it in these sims until "
                "the tap is configured:\n\n" + "\n".join(lines) +
                "\n\n" + self.TAP_REQUIREMENT)

        if any(g.fixable for g in gaps):
            tail = ("\n\nThe missing rules are staged and will be written when "
                    "you save.")
            if not all(g.fixable for g in gaps):
                tail += (" Where the tap is not set up, use the DirectInput "
                         "Tap section on that sim's tab to set it up.")
        else:
            # nothing to stage: setting the tap up is a bigger step than
            # adding a rule, and belongs on the sim's own tab
            tail = ("\n\nUse the DirectInput Tap section on each sim's tab to "
                    "set it up.")
        QMessageBox.information(self, "DirectInput Tap Configuration",
                                body + tail)
        self._tap_gaps_seen = self._tap_gaps_seen | {g.device.key for g in gaps}

    def _gaps_a_reconcile_will_close(self):
        """(sim key, device ids) a reconcile the user has been told about
        will write a rule for at save - so a gap there is already being
        closed."""
        from telemffb.tap_reconcile import device_changes, pending_reconcile

        if self._tap_baseline is None:
            return set()
        changes = device_changes(self._tap_baseline, self.tap_settings_view())
        if not changes or self._change_signature(changes) not in \
                self._tap_reconcile_seen:
            return set()
        return {(item.sim.key, item.replacement.split("=")[0].strip().upper())
                for item in pending_reconcile(changes, self.tap_settings(),
                                              self.tap_statuses())
                if item.replacement}

    def _apply_tap_gaps(self):
        """Add the missing rules, once the settings are saved.

        Runs after the reconcile has been applied, so a gap that reconcile
        closed is no longer reported and is not written twice.  A gap the
        user was never shown - one that opened after the last change, say by
        installing the wrapper - is shown now, before it is written."""
        from telemffb.tap_reconcile import apply_tap_rules, missing_tap_rules

        gaps = [g for g in missing_tap_rules(self.tap_devices(),
                                             self.tap_settings(),
                                             self.tap_statuses())
                if g.fixable]
        if not gaps:
            return
        if any(g.device.key not in self._tap_gaps_seen for g in gaps):
            self._raise_tap_gaps()
        failures = [o for o in apply_tap_rules(gaps) if not o.ok]
        if failures:
            QMessageBox.warning(
                self, "DirectInput Tap Configuration",
                "Some configurations could not be updated:\n\n" +
                "\n".join(f"    {o.directory}: {o.detail}" for o in failures))

    def _offer_aircraft_device_reconcile(self, before, before_idents=None):
        """Keep per-aircraft device references in step with the devices.

        Aircraft settings name a configured device by its devpath (the
        'joystick_device' setting), and the ONLY place the configured
        set changes is this save - so a device that was REPLACED is
        swept here: every user-config reference to the departed path is
        offered a rewrite to its slot's newcomer, and references never
        silently rot.

        Replacement means the DEVICE left, not that its slot changed
        hands: activating a different primary permutes devices between
        slots while every one of them stays configured, and references
        follow devices, so a shuffle is net-neutral and prompts for
        nothing (field case: swapping which stick is primary asked to
        rewrite perfectly valid references, in both directions).  A slot
        that was merely CLEARED is also left alone: the per-aircraft
        resolver falls back to the primary and says so, and there is
        nothing sensible to rewrite to.
        """
        from telemffb import xmlutils
        from telemffb.xml import devices as xml_devices
        before_idents = before_idents or {}
        after = {suffix: str(G.system_settings.get(
                     f'devpath_joystick{suffix}', '') or '')
                 for suffix in before}
        before_paths = {path for path in before.values() if path}
        after_paths = {path for path in after.values() if path}
        for suffix, old in before.items():
            new = after.get(suffix, '')
            if not old or not new or old == new:
                continue
            if old in after_paths or new in before_paths:
                continue        # a shuffle between slots, not a departure
            try:
                refs = xml_devices.find_references(xmlutils._store(), old)
            except Exception:
                logging.exception('Aircraft device reference scan failed')
                continue
            if not refs:
                continue
            scopes = sorted({_describe_setting_scope(e) for e in refs})
            shown = '\n'.join(f'    {s}' for s in scopes[:12])
            if len(scopes) > 12:
                shown += f'\n    ... and {len(scopes) - 12} more'
            new_name = str(G.system_settings.get(
                f'devident_joystick{suffix}', '') or '') or 'the new device'
            old_name = str(before_idents.get(suffix, '')
                           or 'a device that is no longer configured')
            answer = QMessageBox.question(
                self, 'Aircraft Device Settings',
                f'{len(refs)} aircraft setting(s) reference {old_name}, '
                'which was just replaced in System Settings:\n\n'
                f'{shown}\n\n'
                f'Update them to use {new_name} instead?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer == QMessageBox.StandardButton.Yes:
                count = xml_devices.update_references(
                    xmlutils._store(), old, new)
                logging.info(f'Updated {count} aircraft device '
                             f'reference(s) to {new_name}')

    def _offer_tap_reconcile(self, before, after):
        """Repoint tap configs at the device now in the slot.

        Only where it would change something.  A sim is skipped unless it is
        enabled, has our wrapper installed with a config, and that config
        actually names the device that just left - so swapping a device no
        sim was tapping stays silent, which is what keeps this worth reading
        when it does appear.

        This is where the writing happens.  Deferring it to save means a user
        who backs out of the dialog leaves the game folders untouched, rather
        than having their configs repointed at a device selection they then
        abandoned.
        """
        from telemffb.tap_reconcile import (apply_reconcile, device_changes,
                                          pending_reconcile)

        changes = device_changes(before, after)
        if not changes:
            return
        items = pending_reconcile(changes, self.tap_settings(),
                                  self.tap_statuses())
        if not items:
            return

        # said already, when the selection changed; a change that reached
        # save without being mentioned is mentioned now, before it is written
        signature = self._change_signature(changes)
        if signature not in self._tap_reconcile_seen:
            QMessageBox.information(
                self, "DirectInput Tap Configuration",
                self._reconcile_summary(items) + "\n\nThe update is written now.")
            self._tap_reconcile_seen = self._tap_reconcile_seen | {signature}

        failures = [o for o in apply_reconcile(items) if not o.ok]
        if failures:
            QMessageBox.warning(
                self, "DirectInput Tap Configuration",
                "Some configurations could not be updated:\n\n" +
                "\n".join(f"    {o.directory}: {o.detail}" for o in failures))

    def load_settings(self, default=False, source=None):
        """
        Load settings from the registry and update widget states.

        ``source`` substitutes a dict-like (an imported settings file) for
        the registry, populating the form without touching the store.
        """
        if source is not None:
            settings_dict = source
        elif default:
            settings_dict = G.system_settings.defaults
        else:
            # Read settings from the registry
            settings_dict = G.system_settings
        # Update widget states based on the loaded settings
        for panel in self.instance_panels.values():
            panel.load(source or G.system_settings, defaults_only=default)

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
        for key, box in self.tap_enable_boxes.items():
            box.setChecked(_as_bool(settings_dict.get(
                SIMS_BY_KEY[key].tap_enable_key, False)))
        self.toggle_bms_widgets()

        self.cb_enable_dinput.setChecked(settings_dict.get('enableDirectInput', False))


        self.cb_al_enable.setChecked(settings_dict.get('autolaunchMaster', False))

        self.cb_al_enable_j.setChecked(settings_dict.get('autolaunchJoystick', False))
        self.cb_al_enable_p.setChecked(settings_dict.get('autolaunchPedals', False))
        self.cb_al_enable_c.setChecked(settings_dict.get('autolaunchCollective', False))
        self.cb_al_enable_t.setChecked(settings_dict.get('autolaunchTrimWheel', False))

        for suffix, role_cap in (('j', 'Joystick'), ('p', 'Pedals'),
                                 ('c', 'Collective'), ('t', 'TrimWheel')):
            self._load_window_mode(suffix, role_cap, settings_dict)

        self.master_button_group.button(settings_dict.get('masterInstance', 1)).setChecked(True)
        self.master_button_group.button(settings_dict.get('masterInstance', 1)).click()


        self.toggle_al_widgets()

        # A reset or import only stages values in the form, so the
        # baseline the restart notice compares against must stay what the
        # app is actually running with - otherwise restoring a backup
        # with a different master would never say a restart is needed.
        if default or source is not None:
            return

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
            'windowModeJoystick': self._window_mode_value('j'),
            'windowModePedals': self._window_mode_value('p'),
            'windowModeCollective': self._window_mode_value('c'),
            'windowModeTrimWheel': self._window_mode_value('t'),
            'pidJoystick': self.instance_pid('joystick'),
            'pidPedals': self.instance_pid('pedals'),
            'pidCollective': self.instance_pid('collective'),
            'pidTrimWheel': self.instance_pid('trimwheel'),
            'masterInstance': self.master_button_group.checkedId(),
            'themeId': self.themeButtonGroup.checkedId(),
        }

    #: What only takes effect at the next start, grouped as the notice names
    #: them.  Devices and the master are process identity; the launch
    #: options are read when the master starts its children; the theme is
    #: applied once at startup.
    # Device selections are absent deliberately: a changed device is applied
    # live at save (_apply_device_changes_live) and needs no restart.
    RESTART_GROUPS = (
        ("the master device", ('masterInstance',)),
        ("the auto-launch options", ('autolaunchMaster', 'autolaunchJoystick',
                                     'autolaunchPedals', 'autolaunchCollective',
                                     'autolaunchTrimWheel', 'startMinJoystick',
                                     'startMinPedals', 'startMinCollective',
                                     'startMinTrimWheel', 'startHeadlessJoystick',
                                     'startHeadlessPedals', 'startHeadlessCollective',
                                     'startHeadlessTrimWheel')),
        ("the theme", ('themeId',)),
    )

    def _restart_worthy_changes(self, saved):
        """Which of the restart-only groups differ between what the dialog
        loaded and what it just saved, as the phrases the notice uses."""
        return [name for name, keys in self.RESTART_GROUPS
                if any(self.current_al_dict.get(k) != saved.get(k) for k in keys)]

    def _apply_device_changes_live(self, before):
        """Switch every changed device selection into effect, live.

        ``before`` maps role -> the devpath stored on disk when the save
        began; anything different now was really changed by this save.
        The master (this process) re-acquires its own device directly via
        the switch primitive; a RUNNING child instance is told to
        re-acquire over IPC and does the same on its side.  Instances that
        are not running need nothing - they read settings at launch.

        An UNCHANGED selection is retried anyway while the instance sits
        device-less: a failed open leaves no device object and therefore
        no replug watcher, so saving again is the recovery path - it must
        work even though the stored selection reads the same.
        """
        for role in self.INSTANCE_ROLES:
            after = G.system_settings.get(f'devpath_{role}', '') or ''
            changed = before.get(role, '') != after
            if role == G.device_type:
                if not changed and G.device_connection_status:
                    continue
                if not after:
                    continue   # nothing selected: nothing to (re)acquire
                switch = getattr(G, 'switch_to_device', None)
                if switch is None:
                    continue
                ok = switch()
                logging.info(
                    f"Device selection for '{role}' "
                    f"{'applied live' if changed else 'retried (was device-less)'}: "
                    f"{'connected' if ok else 'FAILED to connect'}")
            elif role in G.launched_instances:
                if not changed and G.ipc_instance.child_device_connected(
                        role) is not False:
                    continue
                if not after:
                    continue
                logging.info(
                    f"Device selection for '{role}' "
                    f"{'changed' if changed else 'unchanged but the child is device-less'}"
                    " - telling the running child to re-acquire")
                G.ipc_instance.send_broadcast_message(f'REACQUIRE:{role}')

    #: Device roles, in the order their tabs appear.
    INSTANCE_ROLES = ('joystick', 'pedals', 'collective', 'trimwheel')

    #: The device selector for each role.
    DEVICE_SELECTORS = {'joystick': 'cb_select_j', 'pedals': 'cb_select_p',
                        'collective': 'cb_select_c', 'trimwheel': 'cb_select_t'}

    def selected_device(self, role):
        """The device currently picked for a role, if any.

        For the joystick this is the ACTIVE row's device - the marker may
        sit on an alternate within a dialog session, and everything that
        asks "what will this role drive" (vpconf gating, tap views, launch
        options) must follow it.
        """
        combo = None
        if role == 'joystick' and getattr(self, 'device_cards', None):
            combo = self.device_cards.joystick_card.active_row().selector
        if combo is None:
            combo = getattr(self, self.DEVICE_SELECTORS.get(role, ''), None)
        if combo is None or combo.currentIndex() < 0:
            return None
        model = combo.model()
        if model is None:
            return None
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
        # pending first: an import stages the pid of hardware that is not
        # currently plugged in, and validation runs before anything is
        # written
        return str(self._stored_or_pending(device_pid_key(role)) or '').strip()

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
        """One page per configured device on the Devices tab, each holding
        both of the device's settings panels (system + startup) - one tab
        strip instead of the two the old System/Startup split needed.

        The .ui carries the empty tab widget; pages are built here because
        which devices exist is only known at runtime.  The panels dict
        keeps its (section, role) keys so save/load/validation and the
        vpconf gates run unchanged.
        """
        self.instance_panels = {}      # (section, role) -> panel
        self._role_pages = {}          # role -> the page holding its panels
        # both names the rest of the code and the tests know point at the
        # single merged tab widget
        self.instance_tabs_system = self.instance_tabs_devices
        self.instance_tabs_startup = self.instance_tabs_devices
        for role in self._configured_roles():
            self.instance_tabs_devices.addTab(
                self._build_role_page(role), device_display_name(role))

    def _build_role_page(self, role):
        """One device's page: its system panel over its startup panel."""
        page = QtWidgets.QWidget()
        page.role = role
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        for section, fields in (('system', SYSTEM_FIELDS),
                                ('startup', STARTUP_FIELDS)):
            panel = self.instance_panels.get((section, role))
            if panel is None:
                panel = InstanceSettingsPanel(
                    role, fields, browse_handler=self.browse_instance_vpconf)
                self.instance_panels[(section, role)] = panel
            layout.addWidget(panel)
        layout.addStretch(1)
        self._role_pages[role] = page
        return page

    def panels_for(self, role):
        """Both of a device's panels."""
        return [p for (_, r), p in self.instance_panels.items() if r == role]

    def _selected_roles(self):
        """Roles with a device picked RIGHT NOW - unsaved picks included -
        plus this instance's own.  The settings tabs follow the selection,
        not the store: enable a role, pick its device, and its settings
        are configurable in the same visit."""
        roles = {r for r in self.INSTANCE_ROLES
                 if self.selected_device(r) is not None
                 # stored-but-unplugged devices stay configurable: their
                 # selector shows (None) because the hardware is absent,
                 # not because the user cleared it (clearing empties the
                 # pending devpath, which this view sees)
                 or self._stored_or_pending(f'devpath_{r}')}
        roles.add(G.device_type)
        return [r for r in self.INSTANCE_ROLES if r in roles]

    def _refresh_instance_tabs(self):
        """Keep the per-instance settings tabs in step with the current
        selections.  Panels are hidden, never destroyed: staged edits
        survive a clear-and-repick within the session, and Save persists
        whatever they hold (settings for an unconfigured role are inert
        but preserved)."""
        desired = self._selected_roles()
        tabs = self.instance_tabs_devices
        for i in reversed(range(tabs.count())):
            if getattr(tabs.widget(i), 'role', None) not in desired:
                tabs.removeTab(i)
        for pos, role in enumerate(desired):
            page = self._role_pages.get(role)
            if page is None:
                page = self._build_role_page(role)
                for panel in self.panels_for(role):
                    panel.load(self._import_source or G.system_settings)
            if tabs.indexOf(page) < 0:
                tabs.insertTab(pos, page, device_display_name(role))

    def validate_instance_settings(self):
        """Check every device's Configurator profiles, not just this one's.

        A profile is validated against the device it will be pushed to, so a
        path that is right for the joystick is not accepted for the pedals.
        """
        selected = self._selected_roles()
        for (section, role), panel in self.instance_panels.items():
            if section != 'startup':
                continue
            if role not in selected:
                # cleared this session: its panel is out of sight (and its
                # settings inert) - failing the save on something the user
                # cannot see to fix would be a trap
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

