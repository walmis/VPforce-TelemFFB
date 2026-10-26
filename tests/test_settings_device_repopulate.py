"""Repopulating the device selectors must not disturb the launch options.

populateUSBSelectors() was written as a one-shot at dialog construction, but
the "Enable DirectInput Devices" toggle re-runs it so [DI] entries appear or
disappear immediately.  Swapping each combo's model emits currentIndexChanged
with nothing selected yet, which the change handler reads as "device cleared"
- switching off that role's auto-launch, start-minimised and headless boxes
and queuing its devpath to be cleared on save.
"""
import os
import threading
import time

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G

pytestmark = [pytest.mark.unit]


class _Settings(dict):
    """A settings stand-in that answers the instance-scoped get() the
    per-device panels use: get(key, default, instance=role)."""

    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


def _make_dialog(monkeypatch, settings):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(G, 'system_settings', settings, raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
    # never the real hardware layer: DirectInput enumeration loads the
    # bridge DLL and walks the machine's actual devices, which blocks
    # while a running TelemFFB holds one exclusively
    monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                        staticmethod(lambda enabled=None, settings=None: []))
    monkeypatch.setattr(SystemSettingsDialog, 'DINPUT_LISTING_THREADED', False)
    monkeypatch.setattr(SystemSettingsDialog, '_query_ffb_axes',
                        staticmethod(lambda guid: []))
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                        lambda *a, **k: (True, ''))
    # bridge_status loads the real bridge DLL - never from a test
    from telemffb.hw.ffb_dinput import BridgeStatus
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_status',
                        lambda *a, **k: BridgeStatus(
                            installed=True, version='1.0.0'))
    return app, SystemSettingsDialog()


class _FakeDevice:
    """Enough of DeviceInfo for the selector model and the restore path."""

    def __init__(self, path=b'fake-joystick-path'):
        self.path = path
        self.product_id = 0x2055
        self.vendor_id = 0xFFFF
        self.product_string = 'Fake Rhino FFB Joystick'
        self.manufacturer_string = 'VPforce'
        self.serial_number = 'FAKE0001'

    def vidpid(self):
        return f"{self.vendor_id:04X}:{self.product_id:04X}"


def test_dialog_opens_with_directinput_already_enabled(monkeypatch):
    """Restoring saved settings ticks the box, firing the repopulate handler.

    Connected too early in __init__ that runs before the combos have their
    companion PID fields attached, and the dialog dies on the way up with
    AttributeError: 'QComboBox' object has no attribute '_tb_box'.

    A device the saved devpath matches is required to reach that code: with
    nothing selected the restore loop skips the PID sync entirely.
    """
    device = _FakeDevice()
    monkeypatch.setattr('telemffb.ui.dialogs.SystemSettingsDialog.FFBRhino.enumerate',
                        staticmethod(lambda *a, **k: [device]), raising=False)
    app, dlg = _make_dialog(monkeypatch, _Settings({
        'devpath_joystick': device.path.decode(), 'devpath_pedals': '',
        'devpath_collective': '', 'devpath_trimwheel': '',
        'enableDirectInput': True,
    }))
    # the crash happened during construction; reaching here at all is the test
    assert dlg.cb_enable_dinput.isChecked()
    dlg.deleteLater()
    app.processEvents()


@pytest.fixture
def dialog(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    # a settings stand-in, so the real configuration is neither read nor written
    monkeypatch.setattr(G, 'system_settings', _Settings({
        'devpath_joystick': '', 'devpath_pedals': '', 'devpath_collective': '',
        'devpath_trimwheel': '', 'enableDirectInput': False,
    }), raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None)):
        monkeypatch.setattr(G, name, value, raising=False)

    from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
    # stub the hardware layer for the same reason as _make_dialog above -
    # and FFBRhino.enumerate too, which otherwise lists the developer's
    # actual VPforce devices
    monkeypatch.setattr('telemffb.ui.dialogs.SystemSettingsDialog.FFBRhino.enumerate',
                        staticmethod(lambda *a, **k: []), raising=False)
    monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                        staticmethod(lambda enabled=None, settings=None: []))
    monkeypatch.setattr(SystemSettingsDialog, 'DINPUT_LISTING_THREADED', False)
    monkeypatch.setattr(SystemSettingsDialog, '_query_ffb_axes',
                        staticmethod(lambda guid: []))
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                        lambda *a, **k: (True, ''))
    # bridge_status loads the real bridge DLL - never from a test
    from telemffb.hw.ffb_dinput import BridgeStatus
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_status',
                        lambda *a, **k: BridgeStatus(
                            installed=True, version='1.0.0'))
    dlg = SystemSettingsDialog()
    yield dlg
    dlg.deleteLater()
    app.processEvents()


def _launch_options(dlg):
    return {
        'autolaunch_joystick': dlg.cb_al_enable_j.isChecked(),
        'autolaunch_pedals': dlg.cb_al_enable_p.isChecked(),
        'autolaunch_collective': dlg.cb_al_enable_c.isChecked(),
        'autolaunch_trimwheel': dlg.cb_al_enable_t.isChecked(),
        'start_min_joystick': dlg.cb_min_enable_j.isChecked(),
        'headless_joystick': dlg.cb_headless_j.isChecked(),
    }


def test_directinput_toggle_preserves_launch_options(dialog):
    for box in (dialog.cb_al_enable_j, dialog.cb_al_enable_p,
                dialog.cb_al_enable_c, dialog.cb_al_enable_t,
                dialog.cb_min_enable_j, dialog.cb_headless_j):
        box.setChecked(True)
    configured = _launch_options(dialog)

    dialog.cb_enable_dinput.setChecked(True)
    assert _launch_options(dialog) == configured, "enabling DirectInput cleared launch options"

    dialog.cb_enable_dinput.setChecked(False)
    assert _launch_options(dialog) == configured, "disabling DirectInput cleared launch options"


def test_repopulate_does_not_queue_cleared_devpaths(dialog):
    """The same emission also reaches persist_combobox_selection, which would
    queue every role's devpath to be blanked when the dialog is saved."""
    dialog._pending_devpaths.clear()
    dialog._relist_dinput_devices(True)
    assert not [k for k, v in dialog._pending_devpaths.items() if v == ''], \
        f"repopulate queued devpath clears: {dialog._pending_devpaths}"


def test_repopulate_connects_handlers_once(dialog):
    """Reconnecting on every repopulate would multiply the handler, so a
    single user change would run it repeatedly."""
    before = dialog.cb_select_j.receivers(dialog.cb_select_j.currentIndexChanged)
    dialog._relist_dinput_devices(True)
    dialog._relist_dinput_devices(False)
    assert dialog.cb_select_j.receivers(dialog.cb_select_j.currentIndexChanged) == before


class _FakeDIDevice:
    """A generic DirectInput entry, as directinput_selection_devices shapes
    them."""

    def __init__(self):
        self.path = b'dinput:{0d1e55b2-f16f-11cf-88cb-001111000030}'
        self.product_id = 0x0005
        self.vendor_id = 0x346E
        self.product_string = '[DI] MOZA AB9'
        self.manufacturer_string = ''
        self.serial_number = ''


class TestDirectInputRolesListed:
    """DirectInput devices can serve any FFB role: third-party FFB
    pedals exist, and a modified DirectInput stick makes a collective or
    a trim wheel.  The selectors have to offer them everywhere the
    backend can drive them."""

    def _dialog(self, monkeypatch):
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        app, dlg = _make_dialog(monkeypatch, _Settings({
            'devpath_joystick': '', 'devpath_pedals': '',
            'devpath_collective': '', 'devpath_trimwheel': '',
            'enableDirectInput': True,
        }))
        # dropping the last QApplication reference destroys every widget
        self._app = app
        monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                            staticmethod(lambda enabled=None: [_FakeDIDevice()]))
        dlg._relist_dinput_devices(True)
        return dlg

    def _listed(self, combo):
        model = combo.model()
        return [model.data(model.index(row, 0))
                for row in range(model.rowCount())]

    def test_every_role_offers_di_devices(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        for combo in (dlg.cb_select_j, dlg.cb_select_p, dlg.cb_select_c,
                      dlg.cb_select_t):
            assert any('[DI]' in str(x) for x in self._listed(combo)), \
                combo.objectName()


class TestListingOffTheMainThread:
    """The DirectInput listing opens every FFB device on the PC through
    its driver and can block: the selectors come back at once with the
    native devices and gain the [DI] entries when the listing arrives."""

    @staticmethod
    def _di_listed(combo):
        model = combo.model()
        return any('[DI]' in str(model.data(model.index(row, 0)))
                   for row in range(model.rowCount()))

    def test_the_selectors_fill_in_when_the_listing_arrives(self, monkeypatch):
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        app, dlg = _make_dialog(monkeypatch, _Settings({
            'devpath_joystick': '', 'devpath_pedals': '',
            'devpath_collective': '', 'devpath_trimwheel': '',
            'enableDirectInput': True,
        }))
        self._app = app
        started, release = threading.Event(), threading.Event()

        def listing(enabled=None, settings=None):
            started.set()
            release.wait(5)
            return [_FakeDIDevice()]
        monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                            staticmethod(listing))
        monkeypatch.setattr(SystemSettingsDialog, 'DINPUT_LISTING_THREADED', True)

        dlg._relist_dinput_devices(True)

        assert started.wait(5)
        assert not self._di_listed(dlg.cb_select_j)
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self._di_listed(dlg.cb_select_j):
            app.processEvents()
            time.sleep(0.01)
        for combo in (dlg.cb_select_j, dlg.cb_select_p, dlg.cb_select_c,
                      dlg.cb_select_t):
            assert self._di_listed(combo), combo.objectName()


class TestAxisChooser:
    """The FFB-axis pulldown: pedals/collective/trimwheel on a
    DirectInput device choose which native axis the role drives.
    Hidden for VPforce devices (their axes are the convention), and the
    joystick never has one - X/Y untouched, always."""

    def _dialog(self, monkeypatch, axes=('X', 'RZ'), extra=None):
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        base = {'devpath_joystick': '', 'devpath_pedals': '',
                'devpath_collective': '', 'devpath_trimwheel': '',
                'enableDirectInput': True}
        base.update(extra or {})
        app, dlg = _make_dialog(monkeypatch, _Settings(base))
        self._app = app
        monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                            staticmethod(lambda enabled=None: [_FakeDIDevice()]))
        monkeypatch.setattr(SystemSettingsDialog, '_query_ffb_axes',
                            staticmethod(lambda guid: list(axes)))
        dlg._relist_dinput_devices(True)
        return dlg

    def _select_di(self, combo):
        model = combo.model()
        for row in range(model.rowCount()):
            if '[DI]' in str(model.data(model.index(row, 0))):
                combo.setCurrentIndex(row)
                return
        raise AssertionError('no [DI] row in the combo')

    def test_hidden_until_a_di_device_is_selected(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        assert dlg.cb_axis_p.isHidden()
        self._select_di(dlg.cb_select_p)
        assert not dlg.cb_axis_p.isHidden()

    def test_lists_auto_plus_the_reported_axes(self, monkeypatch):
        dlg = self._dialog(monkeypatch, axes=('X', 'RZ'))
        self._select_di(dlg.cb_select_p)
        items = [dlg.cb_axis_p.itemText(i)
                 for i in range(dlg.cb_axis_p.count())]
        assert items == ['Auto', 'X', 'RZ']

    def test_a_stored_choice_is_restored(self, monkeypatch):
        """...for the device it was made for - the saved one."""
        dlg = self._dialog(monkeypatch, axes=('X', 'RZ'),
                           extra={'dinput_axis_pedals': 'RZ',
                                  'devpath_pedals': 'dinput:{0d1e55b2-f16f-11cf-88cb-001111000030}'})
        self._select_di(dlg.cb_select_p)
        assert dlg.cb_axis_p.currentData() == 'RZ'

    def test_a_different_device_starts_from_auto(self, monkeypatch):
        """A stored choice belongs to the device it was made for; a
        different selection must not inherit it - its axes are its
        own."""
        dlg = self._dialog(monkeypatch, axes=('X', 'RZ'),
                           extra={'dinput_axis_pedals': 'RZ',
                                  'dinput_invert_pedals': True,
                                  'devpath_pedals': 'dinput:{SOME-OTHER}'})
        self._select_di(dlg.cb_select_p)
        assert dlg.cb_axis_p.currentData() == 'auto'
        row = dlg.device_cards.cards['pedals'].primary_row
        assert row.axis_invert_value() is False

    def test_the_value_persists_only_while_shown(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        row = dlg.device_cards.cards['pedals'].primary_row
        assert row.axis_choice_value() is None      # no DI device selected
        self._select_di(dlg.cb_select_p)
        assert row.axis_choice_value() == 'auto'

    def test_the_joystick_never_has_one(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        assert dlg.device_cards.cards['joystick'].primary_row.axis_combo \
            is None

    def test_collective_and_trimwheel_have_choosers(self, monkeypatch):
        # one dialog per role: the same DI device in two roles at once
        # raises the (modal) device-conflict prompt, which is not the
        # subject here
        for role, attr in (('collective', 'cb_select_c'),
                           ('trimwheel', 'cb_select_t')):
            dlg = self._dialog(monkeypatch)
            self._select_di(getattr(dlg, attr))
            assert not dlg.device_cards.cards[role] \
                .primary_row.axis_combo.isHidden(), role


class TestAxisInvert(TestAxisChooser):
    """The Invert checkbox rides the axis chooser: same visibility, its
    own stored flag - the DI-world equivalent of reversing the axis in
    VPConfigurator."""

    def test_appears_and_hides_with_the_chooser(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        row = dlg.device_cards.cards['pedals'].primary_row
        assert row.axis_invert.isHidden()
        assert row.axis_invert_value() is None
        self._select_di(dlg.cb_select_p)
        assert not row.axis_invert.isHidden()
        assert row.axis_invert_value() is False

    def test_a_stored_flag_is_restored(self, monkeypatch):
        dlg = self._dialog(monkeypatch,
                           extra={'dinput_invert_pedals': True,
                                  'devpath_pedals': 'dinput:{0d1e55b2-f16f-11cf-88cb-001111000030}'})
        self._select_di(dlg.cb_select_p)
        row = dlg.device_cards.cards['pedals'].primary_row
        assert row.axis_invert_value() is True


class TestForceFrameControls(TestAxisChooser):
    """The force-frame controls (Mirror X, Mirror Y, rotation) show on any
    row holding a DirectInput device, read the device's stored frame, and
    stage it under the device's VID:PID key at save."""

    KEY = 'dinput_force_frame_346E_0005'          # _FakeDIDevice's ids

    def test_hidden_until_a_di_device_is_selected(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        row = dlg.device_cards.cards['joystick'].primary_row
        assert row.force_frame_value() is None
        self._select_di(dlg.cb_select_j)
        assert row.force_frame_value() == (False, False, 0)

    def test_the_stored_frame_is_restored(self, monkeypatch):
        dlg = self._dialog(monkeypatch, extra={self.KEY: 'my,rot90'})
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        assert row.force_frame_value() == (False, True, 90)

    def test_the_joystick_row_has_them_too(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        assert not row.frame_rotate.isHidden()

    def test_a_change_is_staged_under_the_device_key(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_p)
        row = dlg.device_cards.cards['pedals'].primary_row
        row.frame_mirror_x.setChecked(True)
        row.frame_rotate.setCurrentIndex(row.frame_rotate.findData(180))
        dlg._stage_force_frames()
        assert dlg._pending_devpaths[self.KEY] == 'mx,rot180'

    def test_a_native_device_row_stages_nothing(self, monkeypatch):
        dlg = self._dialog(monkeypatch)
        dlg._stage_force_frames()
        assert not any(k.startswith('dinput_force_frame_')
                       for k in dlg._pending_devpaths)

    def test_calibration_runs_only_on_this_instances_joystick(self, monkeypatch):
        """Judged when the button is clicked, not when the row was filled:
        a Save that switches the instance to the device makes the button
        work without reopening the dialog."""
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        own = '{0d1e55b2-f16f-11cf-88cb-001111000030}'       # _FakeDIDevice's guid
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        assert row.frame_calibrate.isEnabled()
        device = row.selector.currentData()
        blockers = SystemSettingsDialog._force_frame_calibration_blockers
        monkeypatch.setattr('telemffb.hw.ffb_rhino.HapticEffect.device', None)
        monkeypatch.setattr(G, 'device_di_guid', '{other}', raising=False)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        assert blockers(device)
        monkeypatch.setattr(G, 'device_di_guid', own, raising=False)
        monkeypatch.setattr(G, 'device_type', 'pedals', raising=False)
        assert blockers(device)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        assert blockers(device) == ['no FFB device connected']

    def test_a_calibration_result_fills_the_row(self, monkeypatch):
        from telemffb.hw.ffb_dinput import ForceFrame
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        monkeypatch.setattr(SystemSettingsDialog, '_is_this_instances_device',
                            staticmethod(lambda device: True))
        monkeypatch.setattr(SystemSettingsDialog, '_force_frame_calibration_blockers',
                            staticmethod(lambda device: []))
        monkeypatch.setattr(SystemSettingsDialog, '_run_force_frame_calibration',
                            lambda self, device, name: ForceFrame(False, True, 90))
        monkeypatch.setattr('telemffb.hw.ffb_rhino.HapticEffect.device', object())
        dlg._calibrate_force_frame(row)
        assert row.force_frame_value() == (False, True, 90)
        dlg._stage_force_frames()
        assert dlg._pending_devpaths[self.KEY] == 'my,rot90'

    def test_an_unconnected_device_offers_an_in_place_save(self, monkeypatch):
        """Selecting a DirectInput device and calibrating at once: the
        dialog offers to save now, the save connects the instance to the
        device, and the calibration runs without reopening anything."""
        from telemffb.hw.ffb_dinput import ForceFrame
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        own = '{0d1e55b2-f16f-11cf-88cb-001111000030}'       # _FakeDIDevice's guid
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        monkeypatch.setattr(G, 'device_di_guid', None, raising=False)
        saves = []

        def save(self, close=True):
            saves.append(close)
            monkeypatch.setattr(G, 'device_di_guid', own, raising=False)
            return False
        monkeypatch.setattr(SystemSettingsDialog, 'save_settings', save)
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
        monkeypatch.setattr('telemffb.hw.ffb_rhino.HapticEffect.device_alive',
                            staticmethod(lambda: True))
        monkeypatch.setattr('telemffb.hw.ffb_rhino.HapticEffect.device', object())
        monkeypatch.setattr(G, 'telem_manager', None, raising=False)
        monkeypatch.setattr(SystemSettingsDialog, '_run_force_frame_calibration',
                            lambda self, device, name: ForceFrame(True, False, 0))
        dlg._calibrate_force_frame(row)
        assert saves == [False]
        assert row.force_frame_value() == (True, False, 0)

    def test_declining_the_save_offer_calibrates_nothing(self, monkeypatch):
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        monkeypatch.setattr(G, 'device_di_guid', None, raising=False)
        saves, opened = [], []
        monkeypatch.setattr(SystemSettingsDialog, 'save_settings',
                            lambda self, close=True: saves.append(close))
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.No))
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok))
        monkeypatch.setattr(SystemSettingsDialog, '_run_force_frame_calibration',
                            lambda self, device, name: opened.append(1))
        dlg._calibrate_force_frame(row)
        assert saves == [] and opened == []

    def test_a_pedals_row_gets_no_save_offer_from_a_joystick_instance(self, monkeypatch):
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_p)
        row = dlg.device_cards.cards['pedals'].primary_row
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        monkeypatch.setattr(G, 'device_di_guid', None, raising=False)
        asked, warned = [], []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                            staticmethod(lambda *a, **k: asked.append(1)))
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: warned.append(1)))
        dlg._calibrate_force_frame(row)
        assert asked == [] and warned == [1]

    def _detection_env(self, monkeypatch, dlg, aircraft, paused):
        """A connected instance with ``aircraft`` loaded in the telemetry
        manager, paused or streaming."""
        import threading
        from types import SimpleNamespace
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        monkeypatch.setattr(SystemSettingsDialog, '_is_this_instances_device',
                            staticmethod(lambda device: True))
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        monkeypatch.setattr('telemffb.hw.ffb_rhino.HapticEffect.device', object())
        monkeypatch.setattr('telemffb.hw.ffb_rhino.HapticEffect.device_alive',
                            staticmethod(lambda: True))
        freed = []
        monkeypatch.setattr('telemffb.hw.ffb_rhino.HapticEffect.destroy_all',
                            classmethod(lambda cls: freed.append(1) or 0))
        toggles = []
        manager = SimpleNamespace(currentAircraft=aircraft, pause_state=paused,
                                  frame_hold=lambda: threading.Lock(),
                                  set_paused=lambda state: toggles.append(state))
        monkeypatch.setattr(G, 'telem_manager', manager, raising=False)
        return freed, toggles

    def test_a_streaming_sim_is_paused_for_the_detection(self, monkeypatch):
        from telemffb.hw.ffb_dinput import ForceFrame
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        timeouts = []
        aircraft = type('Aircraft', (), {'on_timeout': lambda self: timeouts.append(1)})()
        freed, toggles = self._detection_env(monkeypatch, dlg, aircraft, paused=False)
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
        during = []

        def run(self, device, name):
            during.append((list(toggles), list(freed), list(timeouts)))
            return ForceFrame(False, True, 90)
        monkeypatch.setattr(SystemSettingsDialog, '_run_force_frame_calibration', run)
        dlg._calibrate_force_frame(row)
        assert during == [([True], [1], [1])]          # paused, freed, quieted before the run
        assert toggles == [True, False]                 # and resumed after
        assert row.force_frame_value() == (False, True, 90)

    def test_declining_the_pause_runs_nothing(self, monkeypatch):
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        aircraft = type('Aircraft', (), {'on_timeout': lambda self: None})()
        freed, toggles = self._detection_env(monkeypatch, dlg, aircraft, paused=False)
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.No))
        opened = []
        monkeypatch.setattr(SystemSettingsDialog, '_run_force_frame_calibration',
                            lambda self, device, name: opened.append(1))
        dlg._calibrate_force_frame(row)
        assert opened == [] and toggles == [] and freed == []

    def test_an_already_paused_aircraft_is_quieted_without_asking(self, monkeypatch):
        from telemffb.hw.ffb_dinput import ForceFrame
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        aircraft = type('Aircraft', (), {'on_timeout': lambda self: None})()
        freed, toggles = self._detection_env(monkeypatch, dlg, aircraft, paused=True)
        asked = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                            staticmethod(lambda *a, **k: asked.append(1)))
        monkeypatch.setattr(SystemSettingsDialog, '_run_force_frame_calibration',
                            lambda self, device, name: ForceFrame())
        dlg._calibrate_force_frame(row)
        assert asked == [] and toggles == [] and freed == [1]

    def test_a_blocked_calibration_changes_nothing(self, monkeypatch):
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
        dlg = self._dialog(monkeypatch)
        self._select_di(dlg.cb_select_j)
        row = dlg.device_cards.cards['joystick'].primary_row
        monkeypatch.setattr(SystemSettingsDialog, '_is_this_instances_device',
                            staticmethod(lambda device: True))
        monkeypatch.setattr(SystemSettingsDialog, '_force_frame_calibration_blockers',
                            staticmethod(lambda device: ['no FFB device connected']))
        opened = []
        monkeypatch.setattr(SystemSettingsDialog, '_run_force_frame_calibration',
                            lambda self, device, name: opened.append(1))
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok))
        dlg._calibrate_force_frame(row)
        assert opened == []
        assert row.force_frame_value() == (False, False, 0)
