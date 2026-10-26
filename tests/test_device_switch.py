"""The live device-switch primitive (main.switch_to_device): teardown
order, identity re-derivation for both device kinds, and failure behavior.
"""
import os
import random  # noqa: F401  (parity with sibling harness tests)
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import telemffb.globals as G

pytestmark = [
    pytest.mark.unit,
    # importing main pulls in the simconnect package, which leaks an open
    # FileIO on its scvars.json at interpreter teardown - not ours
    pytest.mark.filterwarnings(
        "ignore::pytest.PytestUnraisableExceptionWarning"),
]


@pytest.fixture
def app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeDevice:
    def __init__(self, name="Old Monster", caps=None, serial="S-OLD"):
        self.info = SimpleNamespace(product_string=name, ident=name,
                                    vendor_id=0xFFFF, product_id=0x2054,
                                    path=b'old')
        self.caps = caps or SimpleNamespace(has_firmware_version=True,
                                            has_gains=True)
        self.serial = serial
        self.shutdown_calls = 0
        # signals the open core connects
        self.deviceConnected = SimpleNamespace(connect=lambda *a: None)
        self.deviceReconnected = SimpleNamespace(connect=lambda *a: None)
        self.buttonPressed = SimpleNamespace(connect=lambda *a: None)
        self.buttonReleased = SimpleNamespace(connect=lambda *a: None)

    def shutdown(self):
        self.shutdown_calls += 1

    def get_firmware_version(self):
        return None

    def get_input(self):
        return 'input-snapshot'          # a device with input flowing

    def pump_input(self):
        pass

    def reset_effects(self):
        pass


@pytest.fixture
def rig(app, monkeypatch):
    """main imported, G stubbed, HapticEffect faked; returns a mutable
    namespace the tests adjust."""
    import main
    from telemffb.hw.ffb_rhino import HapticEffect

    r = SimpleNamespace(order=[], settings={}, main=main)

    old = FakeDevice()
    new = FakeDevice("New Yoke", serial="S-NEW")
    r.old, r.new = old, new

    monkeypatch.setattr(HapticEffect, 'device', old, raising=False)
    monkeypatch.setattr(
        HapticEffect, 'destroy_all',
        classmethod(lambda cls: r.order.append('destroy_all') or 3))
    orig_shutdown = old.shutdown
    old.shutdown = lambda: (r.order.append('old_shutdown'),
                            orig_shutdown())[-1]
    monkeypatch.setattr(
        HapticEffect, 'open',
        classmethod(lambda cls, pid: (r.order.append(('open_hid', pid)),
                                      setattr(cls, 'device', new), new)[-1]))
    monkeypatch.setattr(
        HapticEffect, 'open_dinput',
        classmethod(lambda cls, guid: (r.order.append(('open_di', guid)),
                                       setattr(cls, 'device', new), new)[-1]))

    r.settings = {'devpath_joystick': b'\\\\?\\HID#new'.decode('latin1'),
                  'pidJoystick': '2055'}
    fake_settings = SimpleNamespace(
        get=lambda name, default=None, instance=None:
            r.settings.get(name, default))

    r.paused = []
    monkeypatch.setattr(G, 'system_settings', fake_settings, raising=False)
    monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(
        set_paused=lambda v: r.paused.append(v),
        currentAircraftName='C172'), raising=False)
    monkeypatch.setattr(G, 'effects', SimpleNamespace(
        clear=lambda: r.order.append('effects_clear')), raising=False)
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    monkeypatch.setattr(G, 'device_di_guid', 'stale-guid', raising=False)
    monkeypatch.setattr(G, 'device_usbpid', '2054', raising=False)
    monkeypatch.setattr(G, 'args', SimpleNamespace(reset=False), raising=False)
    monkeypatch.setattr(G, 'force_reload_aircraft_trigger', False, raising=False)
    if hasattr(G, 'main_window'):
        monkeypatch.setattr(G, 'main_window', None, raising=False)

    monkeypatch.setattr(main, '_publish_beacons',
                        lambda: r.order.append('beacons'))
    monkeypatch.setattr(main.QMessageBox, 'warning',
                        staticmethod(lambda *a, **k: None))
    r.ticker = SimpleNamespace(started=0, stopped=0)
    monkeypatch.setattr(main, 'device_retry_ticker', SimpleNamespace(
        start=lambda: setattr(r.ticker, 'started', r.ticker.started + 1),
        stop=lambda: setattr(r.ticker, 'stopped', r.ticker.stopped + 1)))
    # the open checks presence first; the rig's devices are present unless
    # a test says otherwise.  The real check stays reachable for its tests.
    r.present = True
    r.real_present = main._device_present
    monkeypatch.setattr(main, '_device_present', lambda guid, pid: r.present)
    return r


class TestSwitchToDevice:
    def test_teardown_precedes_open_in_order(self, rig):
        assert rig.main.switch_to_device() is True
        names = [o if isinstance(o, str) else o[0] for o in rig.order]
        assert names == ['destroy_all', 'effects_clear', 'old_shutdown',
                         'open_hid', 'beacons']

    def test_hid_identity_re_derived(self, rig):
        assert rig.main.switch_to_device() is True
        assert G.device_di_guid is None          # stale DI identity cleared
        assert G.device_usbpid == '2055'
        assert ('open_hid', 0x2055) in rig.order
        assert G.device_info is rig.new.info
        assert G.device_connection_status is True

    def test_dinput_identity_re_derived(self, rig):
        rig.settings['devpath_joystick'] = 'dinput:{ABCD-1234}'
        assert rig.main.switch_to_device() is True
        assert G.device_di_guid == '{ABCD-1234}'
        assert ('open_di', '{ABCD-1234}') in rig.order

    def test_telemetry_held_for_the_duration(self, rig):
        rig.main.switch_to_device()
        assert rig.paused == [True, False]

    def test_aircraft_reload_forced(self, rig):
        rig.main.switch_to_device()
        assert G.force_reload_aircraft_trigger is True
        assert G.telem_manager.currentAircraftName is None

    def test_open_failure_leaves_deviceless_and_unpauses(self, rig, monkeypatch):
        from telemffb.hw.ffb_rhino import HapticEffect
        def boom(cls, pid):
            raise OSError("no such device")
        monkeypatch.setattr(HapticEffect, 'open', classmethod(boom))
        assert rig.main.switch_to_device() is False
        assert G.device_connection_status is False
        assert rig.paused == [True, False]       # never left held
        assert rig.old.shutdown_calls == 1       # old device still released

    def test_pid_key_missing_falls_back_to_default(self, rig):
        rig.settings.pop('pidJoystick')
        assert rig.main.switch_to_device() is True
        assert G.device_usbpid == '2055'         # the family default

    def test_old_device_without_shutdown_is_tolerated(self, rig, monkeypatch):
        from telemffb.hw.ffb_rhino import HapticEffect
        monkeypatch.setattr(HapticEffect, 'device',
                            SimpleNamespace(info=None), raising=False)
        assert rig.main.switch_to_device() is True

    def test_failed_switch_shows_disconnected_in_the_ui(self, rig, monkeypatch):
        """The status icon and firmware label only hear about state through
        a device object's signals; a failed open has none, so the switch
        must refresh the UI to the disconnected state itself."""
        from telemffb.hw.ffb_rhino import HapticEffect
        def boom(cls, pid):
            raise OSError("no such device")
        monkeypatch.setattr(HapticEffect, 'open', classmethod(boom))
        refreshes = []
        monkeypatch.setattr(G, 'main_window', SimpleNamespace(
            refresh_device_identity=lambda: refreshes.append(True)),
            raising=False)
        assert rig.main.switch_to_device() is False
        assert refreshes == [True]

    def test_serial_global_tracks_the_new_device(self, rig, monkeypatch):
        """The exit/startup vpconf pushes read G.device_serial; a live
        switch must update it (a stale None once had exit push
        '-serial None' into a crashed Configurator thread)."""
        monkeypatch.setattr(G, 'device_serial', None, raising=False)
        assert rig.main.switch_to_device() is True
        assert G.device_serial == "S-NEW"

    def test_vpconf_push_without_serial_is_skipped(self, monkeypatch):
        from telemffb import utils
        monkeypatch.setattr(G, 'device_capabilities', None, raising=False)
        # must return before ever touching Configurator machinery
        utils.upload_vpconf_profile('some_profile.vpconf', None)

    def test_failed_switch_arms_the_retry_ticker(self, rig, monkeypatch):
        from telemffb.hw.ffb_rhino import HapticEffect
        def boom(cls, pid):
            raise OSError("no such device")
        monkeypatch.setattr(HapticEffect, 'open', classmethod(boom))
        rig.main.switch_to_device()
        assert rig.ticker.started == 1

    def test_successful_switch_stops_the_ticker(self, rig):
        rig.main.switch_to_device()
        assert rig.ticker.stopped == 1
        assert rig.ticker.started == 0


class TestDeviceRetryTicker:
    """The device-less watcher: quiet, self-stopping, and it only ever
    attempts a switch once the configured device actually enumerates."""

    @pytest.fixture
    def ticker(self, rig, monkeypatch):
        t = rig.main._DeviceRetryTicker()
        t.CHECK_THREADED = False
        rig.switch_calls = []
        monkeypatch.setattr(
            rig.main, 'switch_to_device',
            lambda show_error=True: (rig.switch_calls.append(show_error),
                                     rig.switch_ok)[-1])
        rig.switch_ok = True
        monkeypatch.setattr(G, 'device_connection_status', False, raising=False)
        rig.present = False
        monkeypatch.setattr(rig.main, '_configured_device_present',
                            lambda *a: rig.present)
        t.start()
        assert t._timer.isActive()
        return t

    def test_device_absent_keeps_waiting(self, rig, ticker):
        ticker._tick()
        assert rig.switch_calls == []
        assert ticker._timer.isActive()

    def test_device_present_switches_quietly_and_stops(self, rig, ticker):
        rig.present = True
        ticker._tick()
        assert rig.switch_calls == [False]     # show_error suppressed
        assert not ticker._timer.isActive()

    def test_present_but_unopenable_keeps_trying(self, rig, ticker):
        rig.present = True
        rig.switch_ok = False
        ticker._tick()
        assert rig.switch_calls == [False]
        assert ticker._timer.isActive()        # next tick tries again

    def test_stops_when_connected_by_other_means(self, rig, ticker, monkeypatch):
        monkeypatch.setattr(G, 'device_connection_status', True, raising=False)
        ticker._tick()
        assert rig.switch_calls == []
        assert not ticker._timer.isActive()

    def test_stops_when_nothing_is_selected(self, rig, ticker):
        rig.settings['devpath_joystick'] = ''
        ticker._tick()
        assert rig.switch_calls == []
        assert not ticker._timer.isActive()

    def test_threaded_check_enumerates_off_the_main_thread(self, rig, ticker,
                                                           monkeypatch):
        """The check runs on a worker with the selection read beforehand;
        the switch runs when the main thread receives the result."""
        import threading
        ticker.CHECK_THREADED = True
        seen = {}

        def present(devpath, pid):
            seen['thread'] = threading.current_thread().name
            seen['selection'] = (devpath, pid)
            return True
        monkeypatch.setattr(rig.main, '_configured_device_present', present)
        delivered = []
        monkeypatch.setattr(rig.main.utils, 'schedule_on_main_thread',
                            lambda fn: delivered.append(fn))
        ticker._tick()
        ticker._check.join(timeout=5)
        assert seen['thread'] != threading.main_thread().name
        assert seen['selection'] == (rig.settings['devpath_joystick'], 0x2055)
        assert rig.switch_calls == []          # nothing until delivery
        delivered[0]()
        assert rig.switch_calls == [False]
        assert not ticker._timer.isActive()


class TestConfiguredDevicePresent:
    def test_hid_matches_by_pid(self, rig, monkeypatch):
        from telemffb.hw.ffb_rhino import FFBRhino
        monkeypatch.setattr(FFBRhino, 'enumerate', staticmethod(
            lambda pid=0: [SimpleNamespace(product_id=0x2055)]))
        assert rig.real_present(None, 0x2055) is True
        monkeypatch.setattr(FFBRhino, 'enumerate', staticmethod(
            lambda pid=0: [SimpleNamespace(product_id=0x2052)]))
        assert rig.real_present(None, 0x2055) is False

    def test_dinput_matches_by_guid(self, rig, monkeypatch):
        from telemffb.hw import ffb_dinput
        monkeypatch.setattr(ffb_dinput.DInputFFBDevice, 'enumerate',
                            staticmethod(lambda bridge=None: [
                                SimpleNamespace(guid='{ABCD-1234}')]))
        assert rig.real_present('{ABCD-1234}', 0) is True
        assert rig.real_present('{other}', 0) is False

    def test_enumeration_failure_reads_as_absent(self, rig, monkeypatch):
        from telemffb.hw.ffb_rhino import FFBRhino
        def boom(pid=0):
            raise OSError("hid backend gone")
        monkeypatch.setattr(FFBRhino, 'enumerate', staticmethod(boom))
        assert rig.real_present(None, 0x2055) is False

    def test_no_selection_reads_as_absent(self, rig):
        rig.settings['devpath_joystick'] = ''
        assert rig.main._configured_device_present() is False

    def test_the_saved_selection_names_the_device(self, rig, monkeypatch):
        asked = []
        monkeypatch.setattr(rig.main, '_device_present',
                            lambda guid, pid: asked.append((guid, pid)) or True)
        assert rig.main._configured_device_present() is True
        rig.settings['devpath_joystick'] = 'dinput:{ABCD-1234}'
        assert rig.main._configured_device_present() is True
        assert asked == [(None, 0x2055), ('{ABCD-1234}', 0x2055)]


class TestAbsentDeviceAtOpen:
    """A configured device that is not connected is an expected state:
    no open is attempted, nothing is logged as an error, and the retry
    ticker is armed."""

    def test_absent_device_skips_the_open_and_arms_the_ticker(self, rig, caplog):
        import logging
        rig.present = False
        with caplog.at_level(logging.INFO):
            assert rig.main.switch_to_device() is False
        opens = [o for o in rig.order if not isinstance(o, str)]
        assert opens == []
        assert rig.ticker.started == 1
        assert G.device_connection_status is False
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not [r for r in caplog.records if r.exc_info]

    def test_present_device_that_fails_to_open_is_an_error(self, rig, monkeypatch, caplog):
        import logging
        from telemffb.hw.ffb_rhino import HapticEffect

        def refuse(cls, pid):
            raise OSError("device busy")
        monkeypatch.setattr(HapticEffect, 'open', classmethod(refuse))
        with caplog.at_level(logging.INFO):
            assert rig.main.switch_to_device() is False
        assert [r for r in caplog.records if r.levelno >= logging.ERROR and r.exc_info]
        assert rig.ticker.started == 1


class TestIdentityRefreshWithoutDebugMenu:
    """The Configurator gain action lives on the Debug menu, which only
    exists with the debug registry key.  The live switch's identity
    refresh must survive a normal install where the menu - and so the
    action - was never built.  (Field case: a first-launch restore on a
    clean registry crashed the switch mid-apply.)"""

    def test_gating_refresh_survives_a_missing_action(self):
        from telemffb.MainWindow import MainWindow
        from telemffb.ui.menus import MainMenu
        bare = SimpleNamespace(main_menu=MainMenu(None))  # no configurator_settings_action
        MainWindow.refresh_configurator_gating(bare)   # must not raise

    def test_gating_still_applies_when_the_menu_exists(self, monkeypatch):
        from telemffb.MainWindow import MainWindow
        from telemffb.ui.menus import MainMenu
        from telemffb.hw.ffb_rhino import HapticEffect

        class Action:
            enabled = None
            def setEnabled(self, v): self.enabled = v
            def setToolTip(self, t): self.tooltip = t

        monkeypatch.setattr(
            HapticEffect, 'device',
            SimpleNamespace(caps=SimpleNamespace(has_gains=False)),
            raising=False)
        main_menu = MainMenu(None)
        main_menu.configurator_settings_action = Action()
        window = SimpleNamespace(main_menu=main_menu)
        MainWindow.refresh_configurator_gating(window)
        assert window.main_menu.configurator_settings_action.enabled is False
