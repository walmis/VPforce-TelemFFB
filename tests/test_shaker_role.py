"""The shaker as a device role: the registry, the audio output listing,
the devpath round trip through startup, the child launch, the settings
dialog's card and selector, and the settings scope in defaults.xml."""
import os
import sys
import tempfile
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtWidgets

import telemffb.globals as G
from telemffb import utils
from telemffb.utils import (
    ALL_ROLES, AUDIO_PREFIX, DEVICE_ROLES, SHAKER_PSEUDO_PID, AudioOutputInfo,
    audio_selection_devices, device_display_name, device_pid_key,
)

pytestmark = [pytest.mark.unit]

# main pulls simconnect in, whose unclosed data file would otherwise
# surface as an unraisable warning attributed to some other test
if sys.platform == "win32" and "simconnect" not in sys.modules:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        import simconnect  # noqa: F401


class _Settings(dict):
    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


class _Output:
    def __init__(self, name, channels=2):
        self.name = name
        self.channels = channels


# ---------------------------------------------------------------------------

class TestRegistry:
    def test_shaker_is_a_role_but_not_a_force_feedback_slot(self):
        assert ALL_ROLES[-1] == 'shaker'
        assert ALL_ROLES[:-1] == DEVICE_ROLES
        assert 'shaker' not in DEVICE_ROLES        # the tap never hands a game to it

    def test_names_and_keys(self):
        assert device_display_name('shaker') == 'Shaker'
        assert device_pid_key('shaker') == 'pidShaker'
        assert utils.SystemSettings.INSTANCE_ROLES[5] == 'shaker'

    def test_pseudo_pid_keeps_the_child_port_valid(self):
        assert 60000 + SHAKER_PSEUDO_PID < 65536


class TestAudioListing:
    def test_entries_carry_the_selector_surface(self):
        default = AudioOutputInfo('')
        assert default.path == AUDIO_PREFIX.encode()
        assert default.ident
        card = AudioOutputInfo('Speakers (USB)')
        assert card.path == b'audio:Speakers (USB)'
        assert card.ident == 'Speakers (USB)'
        assert card.vendor_id == 0 and card.product_id == SHAKER_PSEUDO_PID

    def test_listing_puts_the_default_first(self, monkeypatch):
        from telemffb.hw.shaker_synth import SoundDeviceOutput
        monkeypatch.setattr(SoundDeviceOutput, 'list_devices',
                            staticmethod(lambda *a, **k: [_Output('A'), _Output('B')]))
        listed = audio_selection_devices()
        assert [d.name for d in listed] == ['', 'A', 'B']

    def test_listing_survives_a_missing_audio_library(self, monkeypatch):
        from telemffb.hw.shaker_synth import SoundDeviceOutput

        def boom(*a, **k):
            raise RuntimeError("no portaudio")
        monkeypatch.setattr(SoundDeviceOutput, 'list_devices', staticmethod(boom))
        assert audio_selection_devices() == []


class TestStartupIdentity:
    @pytest.fixture
    def main(self, monkeypatch):
        import main
        for name in ('device_di_guid', 'device_audio_output'):
            monkeypatch.setattr(G, name, None, raising=False)
        return main

    def test_devpath_prefixes_set_one_identity_each(self, main):
        main._identity_from_devpath('audio:Card A')
        assert G.device_audio_output == 'Card A' and G.device_di_guid is None
        G.device_audio_output = None
        main._identity_from_devpath('dinput:{GUID}')
        assert G.device_di_guid == '{GUID}' and G.device_audio_output is None
        G.device_di_guid = None
        main._identity_from_devpath(r'\\?\hid#vid_ffff&pid_2055')
        assert G.device_di_guid is None and G.device_audio_output is None

    def test_the_default_output_is_the_empty_name(self, main):
        main._identity_from_devpath('audio:')
        assert G.device_audio_output == ''

    def test_configured_output_presence(self, main, monkeypatch):
        from telemffb.hw.shaker_synth import SoundDeviceOutput
        monkeypatch.setattr(SoundDeviceOutput, 'list_devices',
                            staticmethod(lambda *a, **k: [_Output('Speakers (Realtek)')]))
        monkeypatch.setattr(G, 'device_type', 'shaker', raising=False)
        for devpath, present in (('audio:', True), ('audio:realtek', True),
                                 ('audio:Missing Card', False)):
            monkeypatch.setattr(G, 'system_settings', _Settings({'devpath_shaker': devpath}),
                                raising=False)
            assert main._configured_device_present() is present, devpath

    def test_open_shaker_reads_its_settings(self, main, monkeypatch):
        from telemffb.hw.ffb_rhino import HapticEffect
        seen = {}

        def fake_open(output_device=None, **kwargs):
            seen['output'] = output_device
            seen.update(kwargs)
            return 'device'
        monkeypatch.setattr(HapticEffect, 'open_shaker', staticmethod(fake_open))
        monkeypatch.setattr(G, 'device_audio_output', 'Card A', raising=False)
        from telemffb.hw.ffb_shaker import Transducer, transducers_to_json
        rows = [Transducer('Seat', 3, 0.7, 'Buttkicker LFE', 'seat')]
        monkeypatch.setattr(G, 'system_settings', _Settings({
            'shakerGain': '0.5', 'shakerTransducers': transducers_to_json(rows)}), raising=False)
        assert main._open_shaker() == 'device'
        assert seen['output'] == 'Card A'
        assert seen['gain'] == 0.5
        assert seen['transducers'] == rows
        assert 'Buttkicker LFE' in {p.name for p in seen['profiles']}

    def test_open_shaker_defaults(self, main, monkeypatch):
        from telemffb.hw.ffb_rhino import HapticEffect
        from telemffb.hw.ffb_shaker import DEFAULT_GAIN, default_profiles_path, load_profiles
        seen = {}
        monkeypatch.setattr(HapticEffect, 'open_shaker',
                            staticmethod(lambda output_device=None, **kw: seen.update(kw, output=output_device)))
        monkeypatch.setattr(G, 'device_audio_output', '', raising=False)
        monkeypatch.setattr(G, 'system_settings', _Settings({'shakerProfile': 'no such'}),
                            raising=False)
        main._open_shaker()
        _, active = load_profiles(default_profiles_path())
        assert seen['output'] is None                 # '' = the system default
        assert [(t.channel, t.profile) for t in seen['transducers']] == [(0, active), (1, active)]
        assert seen['gain'] == DEFAULT_GAIN


class TestChildLaunch:
    def test_shaker_child_is_launched_by_its_pseudo_pid(self, monkeypatch):
        launched = []

        class FakePopen:
            def __init__(self, args):
                self.args = args
                launched.append(self)
        monkeypatch.setattr('telemffb.utils.filesystem.ChildPopen', FakePopen)
        monkeypatch.setattr(G, 'system_settings', _Settings({
            'autolaunchShaker': True, 'pidShaker': format(SHAKER_PSEUDO_PID, 'x'),
            'startHeadlessShaker': True}), raising=False)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        monkeypatch.setattr(G, 'args', SimpleNamespace(darkmode=False, lightmode=False),
                            raising=False)
        monkeypatch.setattr(G, 'launched_instances', {}, raising=False)
        proc = utils.check_launch_instance('shaker', 4321)
        assert proc is launched[0]
        args = proc.args
        assert args[args.index('-t') + 1] == 'shaker'
        assert args[args.index('-D') + 1].lower().endswith(format(SHAKER_PSEUDO_PID, 'x'))
        assert '--headless' in args
        assert proc.udp_port == 60000 + SHAKER_PSEUDO_PID
        assert G.launched_instances['shaker'] is proc


def _make_dialog(monkeypatch, settings, outputs):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(G, 'system_settings', settings, raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None), ('device_audio_output', None)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
    monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                        staticmethod(lambda enabled=None: []))
    monkeypatch.setattr(SystemSettingsDialog, '_enumerate_audio_outputs',
                        staticmethod(lambda: list(outputs)))
    monkeypatch.setattr(SystemSettingsDialog, '_query_ffb_axes',
                        staticmethod(lambda guid: []))
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                        lambda *a, **k: (True, ''))
    from telemffb.hw.ffb_dinput import BridgeStatus
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_status',
                        lambda *a, **k: BridgeStatus(installed=True, version='1.0.0'))
    return app, SystemSettingsDialog()


OUTPUTS = [AudioOutputInfo(''), AudioOutputInfo('Card A')]


class TestSettingsDialog:
    def test_the_shaker_card_lists_audio_outputs_only(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        card = dialog.device_cards.cards['shaker']
        assert card.primary_row.axis_combo is None
        assert not dialog.rb_master_s.isEnabled()
        assert dialog.cb_select_s.count() == 1 + len(OUTPUTS)      # (None) + outputs
        assert dialog.cb_select_s.itemText(0).startswith('(None)')
        paths = [dialog.cb_select_s.model().data(dialog.cb_select_s.model().index(i, 0), 0x0100).path
                 for i in range(1, dialog.cb_select_s.count())]
        assert paths == [b'audio:', b'audio:Card A']
        # the force feedback selectors never list a sound card
        for cb in (dialog.cb_select_j, dialog.cb_select_p):
            model = cb.model()
            assert model is not dialog.cb_select_s.model()
            for i in range(cb.count()):
                dev = model.data(model.index(i, 0), 0x0100)
                assert not bytes(getattr(dev, 'path', b'') or b'').startswith(b'audio:')

    def test_picking_an_output_stages_its_devpath(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        dialog.cb_select_s.setCurrentIndex(2)
        assert dialog._pending_devpaths['devpath_shaker'] == 'audio:Card A'
        assert dialog.selected_device('shaker').name == 'Card A'
        assert dialog.instance_pid('shaker') == format(SHAKER_PSEUDO_PID, 'x')
        assert dialog.device_is_audio('shaker')
        assert not dialog.device_is_dinput('shaker')
        dialog.toggle_device_launch_widgets()
        assert not dialog.rb_master_s.isEnabled()
        assert 'shaker' in dialog._selected_roles()
        startup = dialog.instance_panels[('startup', 'shaker')]
        assert startup.vpforce_blocked

    def test_a_stored_output_is_restored(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings({'devpath_shaker': 'audio:Card A'}), OUTPUTS)
        assert dialog.cb_select_s.currentIndex() == 2

    def test_role_tables_know_the_shaker(self, monkeypatch):
        from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog as D
        assert D.MASTER_ROLE_IDS[5] == 'shaker'
        assert D.AUTOLAUNCH_TOGGLES['shaker'] == 'cb_al_enable_s'
        assert D.DEVICE_SELECTORS['shaker'] == 'cb_select_s'
        assert D.INSTANCE_ROLES[-1] == 'shaker'
        assert D.ROLE_LAUNCH_WIDGETS['shaker'][0] == 'rb_master_s'


class TestSettingsScope:
    @pytest.fixture
    def names(self):
        from telemffb.xml import XmlConfigManager
        defaults_path = str(Path(__file__).parents[1] / "defaults.xml")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?><TelemFFB/>')
            userconfig_path = f.name
        try:
            mgr = XmlConfigManager(device="shaker", userconfig_path=userconfig_path,
                                   defaults_path=defaults_path)
            mgr.store.update_roots()
            rows = mgr.resolver.read_xml_file('DCS')
            yield {r['name'] for r in rows}
        finally:
            os.unlink(userconfig_path)

    def test_the_shaker_sees_what_it_renders(self, names):
        for name in ('buffeting_intensity', 'engine_prop_rumble_enabled',
                     'gunfire_effect_enabled', 'touchdown_effect_enabled',
                     'runway_rumble_enabled', 'type', 'basic_group', 'mechanical_group'):
            assert name in names, name

    def test_and_not_what_it_cannot(self, names):
        for name in ('enable_stick_shaker', 'aoa_effect_gain', 'spring_mode',
                     'enable_damper_ovd', 'joystick_device'):
            assert name not in names, name


class TestCardsPanel:
    def test_role_list_and_axis_roles(self):
        from telemffb.ui.panels.DeviceCardsPanel import AXIS_ROLES, ROLES
        roles = {r[0]: r for r in ROLES}
        assert roles['shaker'][1] == 's'
        assert 'shaker' not in AXIS_ROLES and 'joystick' not in AXIS_ROLES
        assert set(AXIS_ROLES) == {'pedals', 'collective', 'trimwheel'}
