"""A Configurator profile is validated against the device it is for.

Configuring every device from the master means validating profiles for
devices this process does not own, and the seam had three defects:

  * SystemSettings.get() coerces a numeric-looking value to int, so a
    product ID stored as the hex string '2052' came back as 2052 decimal
    and the pedals were checked against PID 0804;
  * 'pid' + role.capitalize() spells pidTrimwheel, while the dialog stores
    pidTrimWheel, so a trim wheel read no product ID at all;
  * an unrecognized PID resolved to *this* instance's device identifier,
    so a profile for a device that is configured but unplugged was
    compared against the master's own joystick.
"""
import json
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import telemffb.globals as G
from telemffb.utils import device_pid_key, validate_vpconf_profile

pytestmark = [pytest.mark.unit]

PEDALS_PID = 0x2052          # stored as the hex string '2052'
JOYSTICK_PID = 0x2054


class FakeDeviceInfo:
    def __init__(self, product_id, ident):
        self.product_id = product_id
        self.ident = ident


class FakeDevice(FakeDeviceInfo):
    """Enough of DeviceInfo for FFBDeviceListModel to render and return."""

    def __init__(self, product_id, ident, serial="SERIAL"):
        super().__init__(product_id, ident)
        self.vendor_id = 0xFFFF
        self.serial_number = serial
        self.path = f"usb-{ident}".encode()


def write_profile(tmp_path, name, pid, device_name, serial="335953"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "config": {"usb_pid": pid, "device_name": device_name},
        "serial_number": serial,
    }), encoding="utf-8")
    return str(path)


@pytest.fixture
def connected(monkeypatch):
    """A master owning the joystick, with pedals also plugged in."""
    monkeypatch.setattr(G, 'instance_dev_dict', {
        JOYSTICK_PID: FakeDeviceInfo(JOYSTICK_PID, "Monster"),
        PEDALS_PID: FakeDeviceInfo(PEDALS_PID, "Pedals"),
    }, raising=False)
    monkeypatch.setattr(G, 'device_info',
                        FakeDeviceInfo(JOYSTICK_PID, "Monster"), raising=False)


class TestHexProductIds:
    def test_a_hex_string_pid_is_read_as_hex(self, tmp_path, connected):
        """The reported failure: '2052' read as decimal makes the target
        PID 0804, and a correct pedals profile is rejected."""
        profile = write_profile(tmp_path, "pedals.vpconf", PEDALS_PID, "Pedals")
        assert validate_vpconf_profile(profile, '2052', 'pedals', silent=True)

    def test_an_int_pid_is_still_taken_as_the_pid(self, tmp_path, connected):
        """Callers that already hold a real int - the device's own
        product_id - must keep working."""
        profile = write_profile(tmp_path, "pedals.vpconf", PEDALS_PID, "Pedals")
        assert validate_vpconf_profile(profile, PEDALS_PID, 'pedals', silent=True)

    def test_the_wrong_device_is_still_rejected(self, tmp_path, connected):
        joystick_profile = write_profile(
            tmp_path, "stick.vpconf", JOYSTICK_PID, "Monster")
        assert not validate_vpconf_profile(
            joystick_profile, '2052', 'pedals', silent=True)


class TestAbsentDevice:
    def test_a_configured_but_unplugged_device_is_not_compared_to_this_one(
            self, tmp_path, monkeypatch):
        """The collective is configured and its profile matches its PID, but
        it is not plugged in.  Comparing its identifier against the master's
        joystick would reject a perfectly good profile."""
        monkeypatch.setattr(G, 'instance_dev_dict',
                            {JOYSTICK_PID: FakeDeviceInfo(JOYSTICK_PID, "Monster")},
                            raising=False)
        monkeypatch.setattr(G, 'device_info',
                            FakeDeviceInfo(JOYSTICK_PID, "Monster"), raising=False)
        profile = write_profile(tmp_path, "coll.vpconf", 0x2051, "Collective")
        assert validate_vpconf_profile(profile, '2051', 'collective', silent=True)

    def test_a_mismatched_identifier_on_a_connected_device_still_fails(
            self, tmp_path, connected):
        """Skipping the check for an absent device must not skip it for a
        present one - that check is what catches USB disconnect trouble."""
        profile = write_profile(tmp_path, "odd.vpconf", PEDALS_PID, "SomeOtherName")
        assert not validate_vpconf_profile(profile, '2052', 'pedals', silent=True)


class TestDialogReadsTheRightPid:
    """The dialog is what feeds the validator, so it has to hand over the
    device's own product ID rather than this instance's."""

    @pytest.fixture
    def dialog(self, monkeypatch):
        from PyQt6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        class FakeSettings(dict):
            def get(self, name, default=None, instance=None):
                key = f"{instance}/{name}" if instance is not None else name
                value = dict.get(self, key, default)
                # SystemSettings.get coerces numeric-looking values to int
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return value

            def setValue(self, key, value):
                self[key] = value

        settings = FakeSettings({
            'devpath_joystick': 'j', 'devpath_pedals': 'p',
            'devpath_collective': '', 'devpath_trimwheel': 't',
            'pidJoystick': '2054', 'pidPedals': '2052', 'pidTrimWheel': '2050',
        })
        monkeypatch.setattr(G, 'system_settings', settings, raising=False)
        for name, value in (('device_type', 'joystick'), ('master_instance', True),
                            ('child_instance', False), ('launched_instances', []),
                            ('device_usbpid', '2054'), ('device_capabilities', None),
                            ('device_di_guid', None), ('is_exe', False)):
            monkeypatch.setattr(G, name, value, raising=False)
        from telemffb.SystemSettingsDialog import SystemSettingsDialog
        dlg = SystemSettingsDialog()
        yield dlg
        dlg.deleteLater()
        app.processEvents()

    def test_each_device_reports_its_own_product_id(self, dialog):
        assert dialog.instance_pid('joystick') == '2054'
        assert dialog.instance_pid('pedals') == '2052'

    def test_the_trim_wheel_is_not_lost_to_a_misspelled_key(self, dialog):
        assert device_pid_key('trimwheel') == 'pidTrimWheel'
        assert dialog.instance_pid('trimwheel') == '2050'

    def test_the_picked_device_wins_over_the_stored_value(self, dialog):
        """Setting up a device means picking it and then choosing its
        profile, in that order, before anything is saved."""
        from telemffb.custom_widgets import FFBDeviceListModel
        combo = dialog.cb_select_p
        combo.setModel(FFBDeviceListModel([FakeDevice(0x20ab, "DIY")]))
        combo.setCurrentIndex(1)                    # row 0 is "(None)"
        assert dialog.instance_pid('pedals') == '20ab'

    def test_a_hex_pid_survives_a_field_that_could_never_have_typed_it(
            self, dialog):
        """The old PID box carried a digits-only validator, so a device on
        PID 20ab could be displayed but never entered by hand."""
        from telemffb.custom_widgets import FFBDeviceListModel
        combo = dialog.cb_select_p
        combo.setModel(FFBDeviceListModel([FakeDevice(0x20ab, "DIY")]))
        combo.setCurrentIndex(1)
        assert int(dialog.instance_pid('pedals'), 16) == 0x20ab

    def test_an_unconfigured_device_reports_nothing_rather_than_this_one(
            self, dialog):
        # collective has no stored PID and nothing picked
        assert dialog.instance_pid('collective') == ''


class TestLaunchOptionsFollowTheDevice:
    """An instance is launched against a device, and the master instance is
    one of them - neither means anything for an empty slot."""

    @pytest.fixture
    def dialog(self, monkeypatch):
        from PyQt6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        class FakeSettings(dict):
            def get(self, name, default=None, instance=None):
                key = f"{instance}/{name}" if instance is not None else name
                return dict.get(self, key, default)

            def setValue(self, key, value):
                self[key] = value

        monkeypatch.setattr(G, 'system_settings', FakeSettings({
            'devpath_joystick': 'j', 'devpath_pedals': '',
            'devpath_collective': '', 'devpath_trimwheel': '',
        }), raising=False)
        for name, value in (('device_type', 'joystick'), ('master_instance', True),
                            ('child_instance', False), ('launched_instances', []),
                            ('device_usbpid', '2054'), ('device_capabilities', None),
                            ('device_di_guid', None), ('is_exe', False)):
            monkeypatch.setattr(G, name, value, raising=False)
        from telemffb.SystemSettingsDialog import SystemSettingsDialog
        dlg = SystemSettingsDialog()
        dlg.cb_al_enable.setChecked(True)
        dlg.toggle_al_widgets()
        yield dlg
        dlg.deleteLater()
        app.processEvents()

    def _assign(self, dialog, combo_name, device):
        from telemffb.custom_widgets import FFBDeviceListModel
        combo = getattr(dialog, combo_name)
        combo.setModel(FFBDeviceListModel([device]))
        combo.setCurrentIndex(1)                 # row 0 is "(None)"
        dialog.toggle_device_launch_widgets()

    def test_an_empty_slot_cannot_be_the_master(self, dialog):
        assert not dialog.rb_master_p.isEnabled()

    def test_an_empty_slot_still_offers_its_switch(self, dialog):
        """The switch is the door into configuring an empty role - it must
        work with nothing picked (the save is what refuses a switched-on
        role with no device)."""
        card = dialog.device_cards.cards['pedals']
        assert card.launch_toggle.isEnabled()
        assert not card.window_mode.isEnabled()   # follows the switch

    def test_assigning_a_device_opens_them(self, dialog):
        self._assign(dialog, 'cb_select_p', FakeDevice(PEDALS_PID, "Pedals"))
        card = dialog.device_cards.cards['pedals']
        assert card.launch_toggle.isEnabled()
        # the master radio waits for the auto-launch switch: an enabled
        # role is one that launches (while auto-launch is globally on)
        assert not dialog.rb_master_p.isEnabled()
        dialog.cb_al_enable_p.setChecked(True)
        assert dialog.rb_master_p.isEnabled()
        assert card.window_mode.isEnabled()

    def test_auto_launch_still_gates_its_own_column(self, dialog):
        """Two conditions, both required: a device to launch, and
        auto-launching switched on."""
        self._assign(dialog, 'cb_select_p', FakeDevice(PEDALS_PID, "Pedals"))
        dialog.cb_al_enable.setChecked(False)
        dialog.toggle_al_widgets()
        card = dialog.device_cards.cards['pedals']
        assert not card.launch_toggle.isEnabled()
        # being the master instance is not an auto-launch setting: with
        # auto-launch globally off, the radio is free
        assert dialog.rb_master_p.isEnabled()

    def test_a_disconnected_device_keeps_its_settings(self, dialog):
        """A device that is merely unplugged shows as (None); its
        auto-launch state must survive untouched, or an unplugged pedal
        set would silently lose its auto-launch on the next save."""
        dialog.cb_al_enable_p.setChecked(True)
        dialog.toggle_device_launch_widgets()
        card = dialog.device_cards.cards['pedals']
        assert dialog.cb_al_enable_p.isChecked()
        assert card.launch_toggle.isChecked()      # the switch shows it too

    def test_an_unconfigured_role_collapses_until_switched_on(self, dialog):
        """An unused role is a slim header; flipping its switch expands
        the card so a device can be picked."""
        card = dialog.device_cards.cards['pedals']       # no device assigned
        assert not card.body_host.isVisibleTo(card)      # collapsed
        dialog.cb_al_enable_p.setChecked(True)           # = the switch
        assert card.body_host.isVisibleTo(card)          # the door opens

    def test_switched_off_role_collapses_but_keeps_config(self, dialog):
        """Auto-launch off folds the card to its header; the device rows
        (and their settings) survive underneath."""
        self._assign(dialog, 'cb_select_p', FakeDevice(PEDALS_PID, "Pedals"))
        card = dialog.device_cards.cards['pedals']
        assert not card.body_host.isVisibleTo(card)      # collapsed
        assert card.collapsed_device.isVisibleTo(card)   # named in the header
        assert 'Pedals' in card.collapsed_device.text()
        dialog.cb_al_enable_p.setChecked(True)
        assert card.body_host.isVisibleTo(card)          # expands


class TestTheMasterRowHasNoLaunchOptions:
    """The master instance launches itself, so its row carries no
    auto-launch, start-minimized or start-headless options.

    This used to be driven by a click on the master radio, which
    load_settings faked by calling click() on the radio it had just
    checked.  A disabled button's click() is silently a no-op, so the master
    row could come up showing options for a device that launches itself.
    Which row is the master's is a fact about the state, so it is settled by
    the same sync that greys out unassigned devices.
    """

    @pytest.fixture
    def dialog(self, monkeypatch):
        from PyQt6 import QtWidgets
        from telemffb.custom_widgets import FFBDeviceListModel
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        class FakeSettings(dict):
            def get(self, name, default=None, instance=None):
                key = f"{instance}/{name}" if instance is not None else name
                return dict.get(self, key, default)

            def setValue(self, key, value):
                self[key] = value

        monkeypatch.setattr(G, 'system_settings', FakeSettings({
            'devpath_joystick': 'j', 'devpath_pedals': 'p',
            'devpath_collective': 'c', 'devpath_trimwheel': 't',
            'masterInstance': 1, 'autolaunchMaster': True,
        }), raising=False)
        for name, value in (('device_type', 'joystick'), ('master_instance', True),
                            ('child_instance', False), ('launched_instances', []),
                            ('device_usbpid', '2054'), ('device_capabilities', None),
                            ('device_di_guid', None), ('is_exe', False)):
            monkeypatch.setattr(G, name, value, raising=False)
        from telemffb.SystemSettingsDialog import SystemSettingsDialog
        dlg = SystemSettingsDialog()
        # stand in for connected hardware, as populateUSBSelectors would
        for combo, pid, ident in ((dlg.cb_select_j, 0x2054, 'Monster'),
                                  (dlg.cb_select_p, 0x2052, 'Pedals'),
                                  (dlg.cb_select_c, 0x2051, 'Collective'),
                                  (dlg.cb_select_t, 0x2050, 'TrimWheel')):
            combo.setModel(FFBDeviceListModel([FakeDevice(pid, ident)]))
            combo.setCurrentIndex(1)
        dlg.cb_al_enable.setChecked(True)
        for suffix in 'jpct':
            getattr(dlg, f'cb_al_enable_{suffix}').setChecked(True)
        dlg.toggle_device_launch_widgets()
        yield dlg
        dlg.deleteLater()
        app.processEvents()

    def _controls_hidden(self, dialog, role):
        card = dialog.device_cards.cards[role]
        return (not card.launch_toggle.isVisibleTo(card)
                and not card.window_mode.isVisibleTo(card))

    def test_the_master_card_hides_its_launch_controls(self, dialog):
        assert self._controls_hidden(dialog, 'joystick')

    @pytest.mark.parametrize("role", ['pedals', 'collective', 'trimwheel'])
    def test_every_other_card_keeps_its_controls(self, dialog, role):
        assert not self._controls_hidden(dialog, role), role

    def test_switching_master_moves_the_hidden_controls(self, dialog):
        dialog.rb_master_p.click()
        assert self._controls_hidden(dialog, 'pedals')
        assert not self._controls_hidden(dialog, 'joystick')

    def test_the_master_card_never_collapses(self, dialog):
        """The master launches itself - its device rows must stay in
        reach whatever its (hidden, inert) auto-launch state says."""
        card = dialog.device_cards.cards['joystick']
        assert card.body_host.isVisibleTo(card)

    def test_becoming_master_keeps_its_launch_settings(self, dialog):
        """They hide while master (launch ignores them anyway), but the
        values survive - clearing them meant an exploratory master change
        and back silently wiped a role's startup configuration."""
        dialog.cb_al_enable_p.setChecked(True)
        dialog.cb_headless_p.setChecked(True)
        dialog.rb_master_p.click()
        assert dialog.cb_al_enable_p.isChecked()
        assert dialog.cb_headless_p.isChecked()

    def test_an_unassigned_device_cannot_become_master(self, dialog, monkeypatch):
        from telemffb.custom_widgets import FFBDeviceListModel
        dialog.cb_select_t.setModel(FFBDeviceListModel([]))
        dialog.toggle_device_launch_widgets()
        assert not dialog.rb_master_t.isEnabled()
        dialog.rb_master_t.click()          # a disabled button ignores it
        assert dialog.master_button_group.checkedId() == 1
