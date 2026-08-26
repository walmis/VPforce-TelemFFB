"""Phase 2 of device hot-swap: the per-aircraft device selection.

An aircraft setting (`joystick_device`) stores the devpath of one of the
joystick role's configured devices; loading such an aircraft swaps to
that device EPHEMERALLY - stored settings untouched, the next aircraft
without a preference (or a restart) comes back up on the primary.

Three seams under test: the switch primitive's devpath parameter, the
resolver that runs at aircraft load, and the save-time reconcile that
keeps stored references from rotting when a slot's device is replaced.
"""
import os
import random  # noqa: F401
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import telemffb.globals as G

pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings(
        "ignore::pytest.PytestUnraisableExceptionWarning"),
]

from tests.test_device_switch import FakeDevice, app, rig  # noqa: F401

ALT_PATH = r'\\?\HID#VID_044F&PID_B10A&MI_00#b'
PRIMARY_PATH = r'\\?\HID#VID_FFFF&PID_2054&MI_00#a'


class TestSwitchPrimitiveDevpath:
    """switch_to_device(devpath=...) acquires exactly that device and
    leaves the stored settings alone."""

    def _path_aware_open(self, rig, monkeypatch):
        from telemffb.hw.ffb_rhino import HapticEffect
        calls = []

        def fake_open(cls, pid=0x2055, serial=None, path=None):
            calls.append({'pid': pid, 'path': path})
            HapticEffect.device = rig.new
            return rig.new
        monkeypatch.setattr(HapticEffect, 'open', classmethod(fake_open))
        return calls

    def test_ephemeral_swap_opens_the_exact_path(self, rig, monkeypatch):
        calls = self._path_aware_open(rig, monkeypatch)
        assert rig.main.switch_to_device(devpath=ALT_PATH) is True
        assert calls[0]['path'] == ALT_PATH
        assert calls[0]['pid'] == 0xB10A        # parsed from the path
        assert G.device_devpath == ALT_PATH

    def test_ephemeral_swap_writes_nothing(self, rig, monkeypatch):
        self._path_aware_open(rig, monkeypatch)
        stored_before = dict(rig.settings)
        assert rig.main.switch_to_device(devpath=ALT_PATH) is True
        assert rig.settings == stored_before

    def test_a_parameterless_call_returns_to_the_stored_primary(
            self, rig, monkeypatch):
        calls = self._path_aware_open(rig, monkeypatch)
        rig.settings['devpath_joystick'] = PRIMARY_PATH
        rig.main.switch_to_device(devpath=ALT_PATH)
        rig.main.switch_to_device()
        assert calls[-1]['path'] == PRIMARY_PATH
        assert G.device_devpath == PRIMARY_PATH

    def test_an_ephemeral_dinput_target_opens_by_guid(self, rig, monkeypatch):
        self._path_aware_open(rig, monkeypatch)
        guid = 'dinput:{0d1e55b2-f16f-11cf-88cb-001111000030}'
        assert rig.main.switch_to_device(devpath=guid) is True
        di_opens = [o for o in rig.order
                    if not isinstance(o, str) and o[0] == 'open_di']
        assert di_opens and di_opens[0][1] == guid[len('dinput:'):]


class Settings(dict):
    def get(self, name, default=None, instance=None):
        return dict.get(self, name, default)


@pytest.fixture
def resolver(app, monkeypatch):
    """A TelemManager (not started) with G stubbed; returns (manager,
    emitted-payload list, settings dict)."""
    from telemffb.telem.TelemManager import TelemManager
    settings = Settings({
        'devpath_joystick': PRIMARY_PATH,
        'devpath_joystick_2': ALT_PATH,
        'devpath_joystick_3': '',
    })
    monkeypatch.setattr(G, 'system_settings', settings, raising=False)
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    monkeypatch.setattr(G, 'device_devpath', PRIMARY_PATH, raising=False)
    monkeypatch.setattr(G, 'device_connection_status', True, raising=False)
    manager = TelemManager()
    emitted = []
    manager.deviceSwapRequested.connect(emitted.append)
    return manager, emitted, settings


class TestResolver:
    """_handle_device_selection: what each aircraft load asks for."""

    def test_no_preference_on_the_primary_is_a_no_op(self, resolver):
        manager, emitted, _ = resolver
        manager._handle_device_selection('C172', {})
        assert emitted == []

    def test_primary_sentinel_is_a_no_op(self, resolver):
        manager, emitted, _ = resolver
        manager._handle_device_selection('C172', {'joystick_device': 'primary'})
        assert emitted == []

    def test_a_configured_alternate_is_requested(self, resolver):
        manager, emitted, _ = resolver
        manager._handle_device_selection('C172', {'joystick_device': ALT_PATH})
        assert emitted == [ALT_PATH]

    def test_the_next_aircraft_without_a_preference_reverts(self, resolver):
        manager, emitted, _ = resolver
        G.device_devpath = ALT_PATH        # ephemeral swap in effect
        manager._handle_device_selection('F16', {})
        assert emitted == ['']             # '' = back to the primary

    def test_a_stale_reference_stays_on_the_primary(self, resolver):
        manager, emitted, _ = resolver
        manager._handle_device_selection(
            'C172', {'joystick_device': r'\\?\HID#VID_0000&PID_0000#gone'})
        assert emitted == []               # already on the primary

    def test_one_request_per_aircraft(self, resolver):
        manager, emitted, _ = resolver
        params = {'joystick_device': ALT_PATH}
        manager._handle_device_selection('C172', params)
        manager._handle_device_selection('C172', params)
        assert emitted == [ALT_PATH]       # no retry storm on failure

    def test_another_aircraft_may_ask_again(self, resolver):
        manager, emitted, _ = resolver
        manager._handle_device_selection('C172', {'joystick_device': ALT_PATH})
        G.device_devpath = ALT_PATH        # ...and the swap happened
        manager._handle_device_selection('F16', {})
        G.device_devpath = PRIMARY_PATH    # ...and the revert happened
        manager._handle_device_selection('C172', {'joystick_device': ALT_PATH})
        assert emitted == [ALT_PATH, '', ALT_PATH]

    def test_a_deviceless_primary_is_retried(self, resolver):
        manager, emitted, _ = resolver
        G.device_connection_status = False
        manager._handle_device_selection('C172', {})
        assert emitted == ['']

    def test_other_roles_never_act(self, resolver, monkeypatch):
        manager, emitted, _ = resolver
        monkeypatch.setattr(G, 'device_type', 'pedals', raising=False)
        manager._handle_device_selection('C172', {'joystick_device': ALT_PATH})
        assert emitted == []


class TestDeviceChoices:
    def test_choices_list_configured_slots_with_ids(self):
        from telemffb.utils import joystick_device_choices
        settings = Settings({
            'devpath_joystick': PRIMARY_PATH,
            'devident_joystick': 'Rhino FFB Monster',
            'devids_joystick': 'FFFF:2054',
            'devpath_joystick_2': ALT_PATH,
            'devident_joystick_2': 'Warthog',
            'devids_joystick_2': '044F:B10A',
            'devpath_joystick_3': '',
        })
        assert joystick_device_choices(settings) == [
            (PRIMARY_PATH, 'Rhino FFB Monster (FFFF:2054) *'),
            (ALT_PATH, 'Warthog (044F:B10A)'),
        ]

    def test_only_the_primary_slot_is_starred(self):
        from telemffb.utils import joystick_device_choices
        settings = Settings({'devpath_joystick_2': ALT_PATH,
                             'devident_joystick_2': 'Warthog'})
        assert joystick_device_choices(settings) == [(ALT_PATH, 'Warthog')]


def userconfig_with_refs(monkeypatch, *values):
    """A fake in-memory userconfig holding one <models> joystick_device
    entry per value; returns (root, writes) where writes records persists."""
    from telemffb import xmlutils
    root = ET.fromstring('<TelemFFB></TelemFFB>')
    for n, value in enumerate(values):
        entry = ET.SubElement(root, 'models')
        for tag, text in (('name', 'joystick_device'),
                          ('model', f'Aircraft{n}.*'), ('value', value),
                          ('sim', 'MSFS'), ('device', 'joystick')):
            ET.SubElement(entry, tag).text = text
    tree = ET.ElementTree(root)
    monkeypatch.setattr(xmlutils, 'auto_user_root', root, raising=False)
    monkeypatch.setattr(xmlutils, 'auto_user_tree', tree, raising=False)
    writes = []
    monkeypatch.setattr(xmlutils, 'write_userconfig_xml',
                        lambda t: writes.append(t))
    return root, writes


def stored_values(root):
    return [e.findtext('value') for e in root.findall('models')]


class TestAircraftDeviceReconcile:
    """Replacing a slot's device in System Settings offers to rewrite the
    aircraft settings that reference the outgoing device - the one place
    identity changes, so references never silently rot."""

    def _world(self, tmp_path, monkeypatch, **fixed):
        from tests.test_tap_workflows import World, RHINO  # noqa: F401
        settings = {'pidJoystick': '2054', 'pidPedals': '2052',
                    'pidCollective': '', 'pidTrimWheel': '',
                    'masterInstance': 1, 'themeId': 2}
        return World(tmp_path, monkeypatch, random.Random(0),
                     settings=settings, **fixed)

    def prompts(self, world):
        return [text for title, text in world.policy.asked
                if title == 'Aircraft Device Settings']

    def test_replacing_a_device_offers_the_rewrite(
            self, app, tmp_path, monkeypatch):
        from tests.test_tap_workflows import RHINO, WARTHOG
        world = self._world(tmp_path, monkeypatch, question=True)
        root, writes = userconfig_with_refs(
            monkeypatch, RHINO.path.decode(), RHINO.path.decode())
        world.set_device('joystick', 2)              # the Warthog replaces it
        assert world.save()
        assert len(self.prompts(world)) == 1
        assert 'Aircraft0' in self.prompts(world)[0]
        assert stored_values(root) == [WARTHOG.path.decode()] * 2
        assert writes

    def test_declining_leaves_the_references_alone(
            self, app, tmp_path, monkeypatch):
        from tests.test_tap_workflows import RHINO
        world = self._world(tmp_path, monkeypatch, question=False)
        root, writes = userconfig_with_refs(monkeypatch, RHINO.path.decode())
        world.set_device('joystick', 2)
        assert world.save()
        assert len(self.prompts(world)) == 1
        assert stored_values(root) == [RHINO.path.decode()]
        assert not writes

    def test_no_references_no_prompt(self, app, tmp_path, monkeypatch):
        world = self._world(tmp_path, monkeypatch, question=True)
        userconfig_with_refs(monkeypatch, r'\?\HID#some_other_device')
        world.set_device('joystick', 2)
        assert world.save()
        assert self.prompts(world) == []

    def test_an_unchanged_save_never_prompts(self, app, tmp_path, monkeypatch):
        from tests.test_tap_workflows import RHINO
        world = self._world(tmp_path, monkeypatch, question=True)
        userconfig_with_refs(monkeypatch, RHINO.path.decode())
        assert world.save()
        assert self.prompts(world) == []

    def test_changing_the_primary_designation_prompts_for_nothing(
            self, app, tmp_path, monkeypatch):
        """Activating a different primary PERMUTES devices between slots
        while every one of them stays configured; references follow
        devices, so the shuffle is net-neutral (field case: it asked to
        rewrite perfectly valid references, in both directions)."""
        from tests.test_tap_workflows import WARTHOG
        world = self._world(tmp_path, monkeypatch, question=True)
        card = world.dialog.device_cards.joystick_card
        card.add_button.click()
        card.alt_rows[0].selector.setCurrentIndex(2)     # Warthog alternate
        assert world.save()
        root, writes = userconfig_with_refs(
            monkeypatch, WARTHOG.path.decode(), WARTHOG.path.decode())
        world.dialog._on_activate_joystick_row(2)        # Warthog -> primary
        assert world.save()
        assert self.prompts(world) == []
        assert stored_values(root) == [WARTHOG.path.decode()] * 2
        world.dialog._on_activate_joystick_row(2)        # ...and back
        assert world.save()
        assert self.prompts(world) == []
        assert stored_values(root) == [WARTHOG.path.decode()] * 2

    def test_the_prompt_names_both_devices(self, app, tmp_path, monkeypatch):
        from tests.test_tap_workflows import RHINO
        world = self._world(tmp_path, monkeypatch, question=True)
        userconfig_with_refs(monkeypatch, RHINO.path.decode())
        world.set_device('joystick', 2)
        assert world.save()
        text = self.prompts(world)[0]
        assert 'Monster' in text          # the departed device, by name

    def test_a_cleared_slot_is_left_alone(self, app, tmp_path, monkeypatch):
        """Removal is not replacement: there is nothing sensible to
        rewrite to - the resolver falls back to the primary at load."""
        from tests.test_tap_workflows import RHINO, WARTHOG
        world = self._world(tmp_path, monkeypatch, question=True)
        card = world.dialog.device_cards.joystick_card
        card.add_button.click()
        card.alt_rows[0].selector.setCurrentIndex(2)     # Warthog as alt
        assert world.save()
        root, writes = userconfig_with_refs(
            monkeypatch, WARTHOG.path.decode())
        world.dialog._on_remove_joystick_alt(2)          # slot cleared
        assert world.save()
        assert self.prompts(world) == []
        assert stored_values(root) == [WARTHOG.path.decode()]


class TestDevicelistRow:
    """The devicelist settings row builds against the real row generator.

    Pins a field crash: the option loop's variable was named `label`,
    clobbering the row's InfoLabel widget - the tail of
    generate_settings_row then called setDisabled on a string.
    """

    def _item(self, value='primary'):
        return {
            'name': 'joystick_device', 'displayname': 'Aircraft Device',
            'datatype': 'devicelist', 'value': value, 'unit': '',
            'validvalues': '', 'order': '80150', 'info': '',
            'replaced': 'Sim Default', 'prereq': 'system_group',
            'has_expander': 'false', 'prereq_count': '', 'hasbump': 'false',
            'grouping': 'System', 'indent': 1, 'is_visible': 'true',
            'sliderfactor': '1', 'force_disabled': False,
        }

    def _layout(self, app, monkeypatch):
        from PyQt6 import QtWidgets
        from telemffb.SettingsLayout import SettingsLayout
        monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(
            current_sim='MSFS', current_class='PropellerAircraft',
            current_pattern='C172', timed_out=False, offline_mode=False),
            raising=False)
        monkeypatch.setattr(G, 'system_settings', Settings({
            'devpath_joystick': PRIMARY_PATH,
            'devident_joystick': 'Rhino FFB Monster',
            'devids_joystick': 'FFFF:2054',
            'devpath_joystick_2': ALT_PATH,
            'devident_joystick_2': 'Warthog',
            'devids_joystick_2': '044F:B10A',
        }), raising=False)
        # the row generator walks three widget levels up to register
        # sliders; give it that ancestry with a stub at the top
        class SliderHost(QtWidgets.QWidget):
            def addSlider(self, slider):
                pass
        outer = SliderHost()
        wrapper = QtWidgets.QWidget(outer)
        host = QtWidgets.QWidget(wrapper)
        self._keepalive = outer
        from PyQt6.QtWidgets import QGridLayout
        layout = SettingsLayout.__new__(SettingsLayout)
        QGridLayout.__init__(layout, host)
        layout.exclusive_list = []
        layout.revert_targets = {}
        layout.parent_expander_dict = {}
        layout.mainwindow = None
        return host, layout

    def _combo(self, host):
        from PyQt6 import QtWidgets
        return host.findChild(QtWidgets.QComboBox, 'ddb_joystick_device')

    def test_the_row_builds_and_lists_the_slots(self, app, monkeypatch):
        host, layout = self._layout(app, monkeypatch)
        layout.generate_settings_row(self._item(), 1)
        combo = self._combo(host)
        assert combo is not None
        texts = [combo.itemText(n) for n in range(combo.count())]
        assert texts == ['Primary (default)',
                         'Rhino FFB Monster (FFFF:2054) *',
                         'Warthog (044F:B10A)']
        assert combo.currentIndex() == 0
        assert combo.itemData(1) == PRIMARY_PATH

    def test_a_stored_alternate_is_preselected(self, app, monkeypatch):
        host, layout = self._layout(app, monkeypatch)
        layout.generate_settings_row(self._item(value=ALT_PATH), 1)
        combo = self._combo(host)
        assert combo.currentData() == ALT_PATH

    def test_a_stale_reference_shows_the_truth(self, app, monkeypatch):
        host, layout = self._layout(app, monkeypatch)
        gone = r'\?\HID#VID_0000&PID_0000#gone'
        layout.generate_settings_row(self._item(value=gone), 1)
        combo = self._combo(host)
        assert combo.currentText() == '(no longer configured)'
        assert combo.currentData() == gone


class TestSettingVisibilityGate:
    """joystick_device (and its Device section header) only exist while
    the joystick role holds more than one device - filtered where the
    defaults are read, so the form and runtime resolution agree.  A
    stored preference goes inert, not lost."""

    @pytest.fixture
    def xml_env(self, tmp_path, monkeypatch):
        from pathlib import Path
        from telemffb import xmlutils
        saved = {attr: getattr(xmlutils, attr, None)
                 for attr in ('auto_user_root', 'auto_user_tree',
                              'auto_defaults_root', 'device',
                              'userconfig_path', 'defaults_path')}
        userconfig = tmp_path / 'userconfig.xml'
        userconfig.write_text('<TelemFFB_v2>\n</TelemFFB_v2>\n',
                              encoding='utf-8')
        defaults = str(Path(__file__).parents[1] / 'defaults.xml')
        monkeypatch.setattr(G, 'userconfig_path', str(userconfig),
                            raising=False)
        monkeypatch.setattr(G, 'defaults_path', defaults, raising=False)
        xmlutils.update_vars('joystick', str(userconfig), defaults)
        xmlutils.update_roots()
        yield xmlutils
        for attr, value in saved.items():
            setattr(xmlutils, attr, value)

    def _names(self, xmlutils):
        return {item['name']
                for item in xmlutils.read_xml_file('MSFS', 'joystick')}

    def test_hidden_with_a_single_device(self, xml_env, monkeypatch):
        monkeypatch.setattr(G, 'system_settings', Settings({
            'devpath_joystick': PRIMARY_PATH}), raising=False)
        names = self._names(xml_env)
        assert 'joystick_device' not in names
        assert 'device_group' not in names

    def test_offered_with_two_devices(self, xml_env, monkeypatch):
        monkeypatch.setattr(G, 'system_settings', Settings({
            'devpath_joystick': PRIMARY_PATH,
            'devpath_joystick_2': ALT_PATH}), raising=False)
        names = self._names(xml_env)
        assert 'joystick_device' in names
        assert 'device_group' in names

    def test_the_group_is_a_locked_open_header(self, xml_env, monkeypatch):
        monkeypatch.setattr(G, 'system_settings', Settings({
            'devpath_joystick': PRIMARY_PATH,
            'devpath_joystick_2': ALT_PATH}), raising=False)
        items = {item['name']: item
                 for item in xml_env.read_xml_file('MSFS', 'joystick')}
        group = items['device_group']
        assert group['datatype'] == 'group'
        assert group['order'].endswith('.0')      # locked open, no arrow
        assert not group['prereq']                # top-level section
        assert items['joystick_device']['prereq'] == 'device_group'


class TestDeviceConfigChangedSignal:
    """System Settings announces device-configuration changes on
    app_events instead of hand-calling each UI consumer - one connect()
    per future consumer instead of another line in save_settings."""

    @pytest.fixture
    def received(self):
        from telemffb.app_events import events

        hub = events()
        record = []

        def on_change(before, after):
            record.append((before, after))
        hub.device_config_changed.connect(on_change)
        yield record
        hub.device_config_changed.disconnect(on_change)

    def _world(self, tmp_path, monkeypatch):
        from tests.test_tap_workflows import World
        return World(tmp_path, monkeypatch, random.Random(0), settings={
            'pidJoystick': '2054', 'pidPedals': '2052', 'pidCollective': '',
            'pidTrimWheel': '', 'masterInstance': 1, 'themeId': 2})

    def test_a_device_change_is_announced_with_before_and_after(
            self, app, tmp_path, monkeypatch, received):
        from tests.test_tap_workflows import RHINO, WARTHOG
        world = self._world(tmp_path, monkeypatch)
        world.set_device('joystick', 2)
        assert world.save()
        assert len(received) == 1
        before, after = received[0]
        assert before['devpath_joystick'] == RHINO.path.decode()
        assert after['devpath_joystick'] == WARTHOG.path.decode()

    def test_every_save_announces_even_unchanged(
            self, app, tmp_path, monkeypatch, received):
        """Identity details the path snapshot cannot see (idents, icons)
        may have changed; consumers decide what to do."""
        world = self._world(tmp_path, monkeypatch)
        assert world.save()
        before, after = received[0]
        assert before == after


class TestMainWindowConfigRouting:
    """The main window's one subscriber routes the signal: labels always,
    the aircraft settings form only when the joystick slots changed, and
    scroll-to-top only when one device became several."""

    def _handler(self):
        from telemffb.MainWindow import MainWindow
        calls = []
        fake = SimpleNamespace(
            refresh_device_labels=lambda: calls.append('labels'),
            settings_layout=SimpleNamespace(
                reload_caller=lambda reveal_top=False:
                    calls.append(('reload', reveal_top))))
        return (lambda b, a: MainWindow.on_device_config_changed(fake, b, a),
                calls)

    def test_an_unchanged_config_only_refreshes_labels(self):
        handler, calls = self._handler()
        handler({'devpath_joystick': 'a'}, {'devpath_joystick': 'a'})
        assert calls == ['labels']

    def test_a_pedals_change_leaves_the_form_alone(self):
        handler, calls = self._handler()
        handler({'devpath_joystick': 'a', 'devpath_pedals': ''},
                {'devpath_joystick': 'a', 'devpath_pedals': 'p'})
        assert calls == ['labels']

    def test_adding_a_second_joystick_rebuilds_and_reveals(self):
        handler, calls = self._handler()
        handler({'devpath_joystick': 'a', 'devpath_joystick_2': ''},
                {'devpath_joystick': 'a', 'devpath_joystick_2': 'b'})
        assert calls == ['labels', ('reload', True)]

    def test_a_replacement_keeps_the_reading_position(self):
        handler, calls = self._handler()
        handler({'devpath_joystick': 'a', 'devpath_joystick_2': ''},
                {'devpath_joystick': 'b', 'devpath_joystick_2': ''})
        assert calls == ['labels', ('reload', False)]

    def test_dropping_back_to_one_device_rebuilds_quietly(self):
        handler, calls = self._handler()
        handler({'devpath_joystick': 'a', 'devpath_joystick_2': 'b'},
                {'devpath_joystick': 'a', 'devpath_joystick_2': ''})
        assert calls == ['labels', ('reload', False)]


class TestFrameHoldDuringSwitch:
    """Pausing telemetry only stops NEW frames; one in flight kept
    writing effects to the device being torn down (field case: assert on
    a closed device mid-swap).  The switch now holds the frame lock."""

    def test_the_switch_waits_out_a_frame_in_flight(self, rig, monkeypatch):
        import threading
        import time as _time
        cond = threading.Condition()
        released_at = {}
        G.telem_manager.frame_hold = lambda: cond
        frame_started = threading.Event()

        def in_flight_frame():
            with cond:
                frame_started.set()
                _time.sleep(0.3)
                released_at['t'] = _time.perf_counter()

        worker = threading.Thread(target=in_flight_frame)
        worker.start()
        assert frame_started.wait(2.0)
        begun = _time.perf_counter()
        assert rig.main.switch_to_device() is True
        finished = _time.perf_counter()
        worker.join()
        # teardown must not have begun until the frame released the lock
        assert finished >= released_at['t']
        assert finished - begun >= 0.2

    def test_a_wedged_telemetry_thread_does_not_hang_the_switch(
            self, rig, monkeypatch):
        import threading
        lock = threading.Lock()
        lock.acquire()                       # never released
        G.telem_manager.frame_hold = lambda: lock
        monkeypatch.setattr(rig.main.switch_to_device.__globals__['logging'],
                            'warning', lambda *a, **k: None)
        assert rig.main.switch_to_device() is True   # proceeds after timeout


class TestPanelFollowsTheSwap:
    """The status icon and its name show what is actually in hand, found
    by matching the acquired devpath back to its configured slot."""

    def _settings(self):
        return Settings({
            'devpath_joystick': PRIMARY_PATH,
            'devident_joystick': 'Monster', 'devicon_joystick': 'stick',
            'devpath_joystick_2': ALT_PATH,
            'devident_joystick_2': 'Big Yoke', 'devicon_joystick_2': 'yoke',
        })

    def test_slot_lookup_by_devpath(self):
        from telemffb.utils import active_joystick_slot_suffix
        settings = self._settings()
        assert active_joystick_slot_suffix(settings, PRIMARY_PATH) == ''
        assert active_joystick_slot_suffix(settings, ALT_PATH) == '_2'
        assert active_joystick_slot_suffix(settings, 'unknown') is None
        assert active_joystick_slot_suffix(settings, '') is None

    def test_label_and_icon_route_through_the_slot(self):
        from telemffb.utils import device_panel_icon, device_panel_label
        settings = self._settings()
        assert device_panel_label('joystick', settings,
                                  slot_suffix='_2') == 'Big Yoke'
        assert device_panel_icon('joystick', settings,
                                 slot_suffix='_2') == ':/image/icon_yoke.png'
        # and the default stays the primary
        assert device_panel_label('joystick', settings) == 'Monster'
        assert device_panel_icon('joystick', settings) == ''


class TestInputWarmup:
    """A just-opened device has no input snapshot until its first report;
    the mixins read input every frame and assume it is always there
    (field case: AttributeError on the swap back).  The switch pumps the
    device until input flows, and the telemetry loop skips frames for
    whatever window remains."""

    def test_the_switch_pumps_until_input_flows(self, rig, monkeypatch):
        reports = {'count': 0}

        def pump_input():
            reports['count'] += 1

        rig.new.pump_input = pump_input
        rig.new.get_input = lambda: 'snapshot' if reports['count'] >= 3 \
            else None
        assert rig.main.switch_to_device() is True
        assert reports['count'] >= 3      # pumped until the report arrived

    def test_frames_are_skipped_until_input_exists(self, app, monkeypatch):
        from telemffb.hw.ffb_rhino import HapticEffect
        from telemffb.telem.TelemManager import TelemManager
        manager = TelemManager()
        processed = []
        manager.currentAircraft = SimpleNamespace(
            _telem_data={}, _last_telem_data={},
            on_telemetry=lambda data: processed.append(data))
        monkeypatch.setattr(HapticEffect, 'device',
                            SimpleNamespace(get_input=lambda: None),
                            raising=False)
        manager._process_current_aircraft_telemetry({'N': 'C172'})
        assert processed == []            # no input yet: frame held
        monkeypatch.setattr(HapticEffect, 'device',
                            SimpleNamespace(get_input=lambda: 'snapshot'),
                            raising=False)
        manager._process_current_aircraft_telemetry({'N': 'C172'})
        assert len(processed) == 1        # input flowing: frames resume


class TestEventHubLifetime:
    """The hub is reached through an accessor, not a module-level
    instance: a parentless QObject dies with its QApplication, which
    never happens in the app but does between test processes/apps -
    emitting into a dead hub would silently drop the event."""

    def test_a_dead_hub_is_replaced(self, app):
        from PyQt6 import sip
        from telemffb import app_events
        hub = app_events.events()
        sip.delete(hub)
        fresh = app_events.events()
        assert fresh is not hub
        assert not sip.isdeleted(fresh)

    def test_a_live_hub_is_reused(self, app):
        from telemffb import app_events
        assert app_events.events() is app_events.events()
