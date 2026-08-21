"""An existing settings base must survive the move to a unified dialog.

These run against the real SystemSettings class, pointed at a scratch ini
file, and seed it with the shape a long-running install actually has: global
keys at the root, one subkey per device, and - the hazard - copies of
global settings sitting under a device.

get() resolves instance-scoped before global, so those copies shadow the
global value.  ignoreUpdate used to be written per-device even though only
the master checks for updates; left in place, it would make the setting
appear not to stick and would keep the updater reading the old answer.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings

import telemffb.globals as G
from telemffb.utils import SystemSettings

pytestmark = [pytest.mark.unit]


#: What an existing install looks like, taken from a real registry base.
EXISTING = {
    'masterInstance': 1,
    'ignoreUpdate': 'false',            # written by get()'s default fallback
    'pidJoystick': '2054', 'pidPedals': '2052', 'pidCollective': '2051',
    'pidTrimWheel': '',
    'devpath_joystick': r'\\?\HID#VID_FFFF&PID_2054&MI_00#f&9caf07d&0&0000',
    'devpath_pedals': r'\\?\HID#VID_FFFF&PID_2052&MI_00#f&2e2e7e50&0&0000',
    'devpath_collective': r'\\?\HID#VID_FFFF&PID_2051&MI_00#e&de4230a&0&0000',
    'devpath_trimwheel': '',
    'pruneLogs': 'false', 'pruneLogsNum': 1, 'pruneLogsUnit': 'Week(s)',
    'themeId': 2, 'autolaunchMaster': 'false',

    'joystick/logLevel': 'INFO',
    'joystick/telemTimeout': '500',
    'joystick/saveWindow': 'true',
    'joystick/saveLastTab': 'true',
    'joystick/enableVPConfStartup': 'true',
    'joystick/pathVPConfStartup': 'C:/Configurator/MyConfigs/Default_MONSTER.vpconf',
    'joystick/enableVPConfExit': 'true',
    'joystick/pathVPConfExit': 'C:/Configurator/MyConfigs/Default_MONSTER.vpconf',
    'joystick/enableVPConfGlobalDefault': 'true',
    'joystick/enableResetGainsExit': 'false',
    'joystick/ignoreUpdate': 'true',          # the shadowing copy
    'joystick/WindowData': '{"Tab": 1}',      # per-device UI state, not ours
    'joystick/TrimCalInstructionsCollapsed': 'true',

    'pedals/logLevel': 'INFO',
    'pedals/telemTimeout': '200',
    'pedals/pathVPConfStartup': 'C:/Configurator/MyConfigs/Default_Pedals.vpconf',
    'pedals/enableVPConfStartup': 'true',
    'pedals/ignoreUpdate': 'false',
    'pedals/WindowData': '{"Tab": 1}',

    'collective/logLevel': 'INFO',
    'collective/telemTimeout': '200',
    'collective/ignoreUpdate': 'false',
}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)

    store = SystemSettings(path=str(tmp_path / 'TelemFFB.ini'))
    # QSettings.setDefaultFormat() does NOT redirect the two-argument
    # constructor on Windows: without an explicit path this store would be
    # the user's live registry, and the clear() below would erase it.
    assert 'HKEY' not in store.fileName(),         f"refusing to run against the real settings store: {store.fileName()}"
    store.clear()
    for key, value in EXISTING.items():
        QSettings.setValue(store, key, value)
    store.sync()
    return store


class TestTheShadowingCopiesGo:
    def test_the_stale_copy_is_removed(self, settings):
        assert settings.value('joystick/ignoreUpdate') is not None
        settings.migrate_instance_scoped_globals()
        for role in ('joystick', 'pedals', 'collective'):
            assert settings.value(f'{role}/ignoreUpdate') is None, role

    def test_the_masters_answer_is_the_one_kept(self, settings):
        """The instance copy is what the app has been honoring, so promoting
        it is what preserves the behavior the user actually sees."""
        settings.migrate_instance_scoped_globals()
        assert settings.value('ignoreUpdate') == 'true'
        assert settings.get('ignoreUpdate') == 1

    def test_the_setting_sticks_afterwards(self, settings):
        """The point of the migration: a global write is no longer shadowed."""
        settings.migrate_instance_scoped_globals()
        settings.setValue('ignoreUpdate', False)
        assert not settings.get('ignoreUpdate')

    def test_it_reports_what_it_moved(self, settings):
        assert settings.migrate_instance_scoped_globals() == ['ignoreUpdate']

    def test_running_it_again_does_nothing(self, settings):
        settings.migrate_instance_scoped_globals()
        assert settings.migrate_instance_scoped_globals() == []


class TestEverythingElseIsLeftAlone:
    @pytest.fixture(autouse=True)
    def migrated(self, settings):
        settings.migrate_instance_scoped_globals()

    @pytest.mark.parametrize("key", [
        'joystick/logLevel', 'joystick/telemTimeout', 'joystick/saveWindow',
        'joystick/saveLastTab', 'joystick/enableVPConfStartup',
        'joystick/pathVPConfStartup', 'joystick/enableVPConfExit',
        'joystick/pathVPConfExit', 'joystick/enableVPConfGlobalDefault',
        'joystick/enableResetGainsExit',
        'pedals/logLevel', 'pedals/telemTimeout', 'pedals/pathVPConfStartup',
        'collective/logLevel', 'collective/telemTimeout',
    ])
    def test_per_device_settings_survive(self, settings, key):
        assert settings.value(key) == EXISTING[key], key

    @pytest.mark.parametrize("key", [
        'joystick/WindowData', 'joystick/TrimCalInstructionsCollapsed',
        'pedals/WindowData',
    ])
    def test_per_device_ui_state_is_not_touched(self, settings, key):
        """Window geometry and the like live under a device too, and are no
        business of this migration."""
        assert settings.value(key) == EXISTING[key], key

    @pytest.mark.parametrize("key", [
        'pidJoystick', 'pidPedals', 'pidCollective', 'pidTrimWheel',
        'devpath_joystick', 'devpath_pedals', 'devpath_collective',
        'masterInstance', 'pruneLogsUnit', 'themeId',
    ])
    def test_global_settings_survive(self, settings, key):
        assert settings.value(key) == EXISTING[key], key


class TestProductIdsStillReadAsHex:
    def test_a_stored_hex_pid_round_trips(self, settings):
        """get() coerces '2052' to the int 2052; str() restores the text the
        validator needs, and int(_, 16) then gives back 0x2052."""
        stored = str(settings.get('pidPedals', '') or '')
        assert stored == '2052'
        assert int(stored, 16) == 0x2052

    def test_an_unset_pid_stays_empty(self, settings):
        assert str(settings.get('pidTrimWheel', '') or '') == ''


class TestFirstOpenOfTheNewDialog:
    """Loading an existing base into the unified dialog and saving it back
    must not change what the settings say."""

    def test_a_load_and_save_round_trip_preserves_every_device(self, settings,
                                                               monkeypatch):
        from telemffb.InstanceSettingsPanel import ALL_FIELDS, InstanceSettingsPanel
        from PyQt6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        settings.migrate_instance_scoped_globals()

        roles = ('joystick', 'pedals', 'collective')
        # Compared through get(), which is how the app reads these - the
        # raw store keeps bools as the strings 'true'/'false'.
        stored = {role: {f.key: settings.get(f.key, instance=role)
                         for f in ALL_FIELDS if
                         settings.value(f"{role}/{f.key}") is not None}
                  for role in roles}

        for role in roles:
            panel = InstanceSettingsPanel(role, ALL_FIELDS)
            panel.load(settings)
            panel.save(settings)

        for role, keys in stored.items():
            for key, value in keys.items():
                assert settings.get(key, instance=role) == value, f"{role}/{key}"
        app.processEvents()
