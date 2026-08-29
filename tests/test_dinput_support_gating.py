"""DirectInput support is opt-in, and honest about not being available.

The bridge DLL ships separately from TelemFFB, so it can be absent, out of
date, or past a beta expiry.  Enabling support without it would list no
devices and explain nothing, which reads as "my device is missing".
"""
import os
import random

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G


@pytest.fixture
def app():
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication([]))


# Importing main pulls in simconnect, which leaves a file handle open. pytest
# raises that as an unraisable-exception warning against whichever test is
# running when the garbage collector gets to it. Scoped to this module so it
# cannot mask a leak of our own elsewhere.
pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


class FakeSettings(dict):
    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def dialog(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(G, 'system_settings', FakeSettings({
        'devpath_joystick': 'usb-monster', 'devpath_pedals': 'usb-pedals',
        'devpath_collective': '', 'devpath_trimwheel': '',
        'joystick/enableVPConfStartup': True,
        'joystick/pathVPConfStartup': r'C:\profiles\stick.vpconf',
    }), raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2054'), ('device_capabilities', None),
                        ('device_di_guid', None), ('is_exe', False)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.SystemSettingsDialog import SystemSettingsDialog
    # bridge_status loads the real bridge DLL - never from a test
    from telemffb.hw.ffb_dinput import BridgeStatus
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_status',
                        lambda *a, **k: BridgeStatus(
                            installed=True, version='1.0.0'))
    monkeypatch.setattr(SystemSettingsDialog, '_query_ffb_axes',
                        staticmethod(lambda guid: []))
    dlg = SystemSettingsDialog()
    yield dlg
    dlg.deleteLater()
    app.processEvents()


class TestBridgeAvailability:
    def test_a_missing_dll_is_reported_not_raised(self):
        from telemffb.hw.ffb_dinput import bridge_availability
        available, reason = bridge_availability('definitely_not_here.dll')
        assert not available
        assert 'dinput_ffb.dll' in reason
        assert 'Looked in' in reason

    def test_the_toggle_reverts_when_the_bridge_is_missing(self, dialog, monkeypatch):
        monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                            lambda *a, **k: (False, 'no bridge here'))
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: shown.append(a[2])))

        dialog.cb_enable_dinput.setChecked(True)

        assert shown, "the user was told nothing"
        assert 'no bridge here' in shown[0]
        assert not dialog.cb_enable_dinput.isChecked(), \
            "the box stayed on while doing nothing"

    def test_the_toggle_sticks_when_the_bridge_is_there(self, dialog, monkeypatch):
        monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                            lambda *a, **k: (True, ''))
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: shown.append(a)))
        dialog.cb_enable_dinput.setChecked(True)
        assert dialog.cb_enable_dinput.isChecked()
        assert not shown

    def test_turning_it_off_never_complains(self, dialog, monkeypatch):
        monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                            lambda *a, **k: (False, 'no bridge here'))
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: shown.append(a)))
        dialog.cb_enable_dinput.setChecked(False)
        assert not shown


class TestStartupCheck:
    def _run(self, monkeypatch, *, enabled, available, child=False):
        # imported here, not at module scope: importing it pulls in
        # simconnect, which leaves a file handle open, and at module scope
        # the collector gets to it during some unrelated test in another
        # file - outside the reach of this module's warning filter
        import main
        settings = FakeSettings({'enableDirectInput': enabled})
        monkeypatch.setattr(G, 'system_settings', settings, raising=False)
        monkeypatch.setattr(G, 'child_instance', child, raising=False)
        monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                            lambda *a, **k: (available, 'the DLL is missing'))
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: shown.append(a[2])))
        main._check_directinput_support()
        return settings, shown

    def test_enabled_without_the_bridge_is_switched_off_and_explained(self, monkeypatch):
        settings, shown = self._run(monkeypatch, enabled=True, available=False)
        assert settings['enableDirectInput'] is False
        assert shown and 'the DLL is missing' in shown[0]

    def test_enabled_with_the_bridge_is_left_alone(self, monkeypatch):
        settings, shown = self._run(monkeypatch, enabled=True, available=True)
        assert settings['enableDirectInput'] is True
        assert not shown

    def test_disabled_does_not_probe_or_nag(self, monkeypatch):
        settings, shown = self._run(monkeypatch, enabled=False, available=False)
        assert not shown

    def test_a_child_instance_stays_quiet(self, monkeypatch):
        """Four instances raising the same dialog helps nobody."""
        settings, shown = self._run(monkeypatch, enabled=True, available=False,
                                    child=True)
        assert not shown


class TestBridgeDllLocation:
    """Where the DLL is looked for has to match where the install keeps it.

    The paths were built from os.path.dirname(__file__), which under
    PyInstaller resolves inside the unpacked bundle - a temp directory that
    exists only while the app runs. A frozen build therefore searched
    everywhere except the installation the DLL is actually dropped into,
    and since developers run from source, it worked here and nowhere else.
    """

    EXE = r"C:\Program Files\VPforce\TelemFFB\TelemFFB.exe"
    MEIPASS = r"C:\Users\someone\AppData\Local\Temp\_MEI98765"

    def _frozen(self, monkeypatch):
        import sys
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        monkeypatch.setattr(sys, 'executable', self.EXE, raising=False)
        monkeypatch.setattr(sys, '_MEIPASS', self.MEIPASS, raising=False)

    def test_a_frozen_build_looks_beside_the_executable_first(self, monkeypatch):
        from telemffb.hw.ffb_dinput import DIBridge
        self._frozen(monkeypatch)
        paths = DIBridge.library_paths()
        assert paths[0] == os.path.join(os.path.dirname(self.EXE), 'dll',
                                        'dinput_ffb.dll')

    def test_a_frozen_build_prefers_the_install_over_the_bundle(self, monkeypatch):
        """The bundle copy is whatever shipped; the install copy is what the
        user put there deliberately."""
        from telemffb.hw.ffb_dinput import DIBridge
        self._frozen(monkeypatch)
        paths = DIBridge.library_paths()
        first_bundled = next(i for i, p in enumerate(paths)
                             if p.startswith(self.MEIPASS))
        assert all(not p.startswith(self.MEIPASS) for p in paths[:first_bundled])
        assert first_bundled > 0

    def test_running_from_source_looks_in_the_repo(self, monkeypatch):
        import sys
        from telemffb.hw.ffb_dinput import DIBridge
        monkeypatch.delattr(sys, 'frozen', raising=False)
        paths = DIBridge.library_paths()
        assert paths[0].endswith(os.path.join('dll', 'dinput_ffb.dll'))
        assert 'telemffb' not in paths[0].lower().split(os.sep)[-3:-1], \
            "the path should be the project root, not the package directory"

    def test_the_paths_are_worked_out_when_asked(self, monkeypatch):
        """A tuple built at import time cannot know sys.executable, which is
        why this used to be wrong."""
        from telemffb.hw.ffb_dinput import DIBridge
        before = DIBridge.library_paths()
        self._frozen(monkeypatch)
        assert DIBridge.library_paths() != before

    def test_every_candidate_is_an_absolute_location(self):
        """No bare name: letting Windows search its own DLL path makes "not
        found" depend on the working directory and on whatever is already
        loaded in the process, and could pick up a dinput_ffb.dll from
        somewhere nobody intended."""
        from telemffb.hw.ffb_dinput import DIBridge
        for path in DIBridge.library_paths():
            assert os.path.isabs(path), f"{path} is not an absolute location"

    def test_a_frozen_build_tells_the_user_a_path_they_can_act_on(self, monkeypatch):
        """The message has to name somewhere the user can put the file."""
        from telemffb.hw.ffb_dinput import bridge_availability
        self._frozen(monkeypatch)
        available, reason = bridge_availability()
        assert not available, "no DLL exists at the simulated install"
        assert os.path.dirname(self.EXE) in reason
        offered = [ln.strip() for ln in reason.splitlines() if ln.startswith('    ')]
        assert not offered[0].startswith(self.MEIPASS),             "the first path offered should not be the temp bundle"


class TestTheUserIsToldWhereToGetIt:
    """"It isn't there" is only half an answer.

    The DLL ships separately from TelemFFB, so a user who has never had it
    needs to be pointed somewhere - and the address is a placeholder until
    the download location is settled, which is exactly why every message
    reads it from one constant instead of spelling it out.
    """

    def test_a_missing_dll_says_where_to_get_one(self, monkeypatch):
        from telemffb.hw import ffb_dinput
        available, reason = ffb_dinput.bridge_availability('not_here.dll')
        assert not available
        assert ffb_dinput.BRIDGE_DOWNLOAD_LOCATION in reason
        assert 'obtain it from' in reason

    def test_an_expired_build_says_where_to_get_a_current_one(self, monkeypatch):
        import telemffb.globals as G
        from telemffb.hw import ffb_dinput
        monkeypatch.setattr(ffb_dinput.DIBridge, '__init__',
                            lambda self, p=None: None)
        # current enough to reach the expiry check: an older build is
        # refused by the minimum-version gate first, which is a different
        # message and a different test
        current = G.dinput_bridge_min_version or '1.0.0'
        monkeypatch.setattr(ffb_dinput.DIBridge, 'build_info',
                            {'version': current, 'expires': '2020-01-01'},
                            raising=False)
        available, reason = ffb_dinput.bridge_availability()
        assert not available
        assert 'expired on 2020-01-01' in reason
        assert ffb_dinput.BRIDGE_DOWNLOAD_LOCATION in reason

    def test_the_search_list_is_not_printed_twice(self, monkeypatch):
        """The bridge's own error names every path it tried; the one-per-line
        list below it is the readable version, so the echo is dropped."""
        from telemffb.hw import ffb_dinput
        available, reason = ffb_dinput.bridge_availability('not_here.dll')
        assert reason.count('not_here.dll') == 1, reason

    def test_a_failure_that_says_something_else_is_still_shown(self, monkeypatch):
        from telemffb.hw import ffb_dinput

        def refuse(self, dll_path=None):
            raise ffb_dinput.DIBridgeError("ABI version 0, expected 1")

        monkeypatch.setattr(ffb_dinput.DIBridge, '__init__', refuse)
        available, reason = ffb_dinput.bridge_availability()
        assert not available
        assert 'ABI version 0, expected 1' in reason


class TestBridgeStatusReport:
    """bridge_status(): the facts the settings page shows beside the
    DirectInput toggle - which utility is installed, which build, and how
    long a beta build is good for."""

    def _fake_dll(self, monkeypatch, payload):
        import json as _json
        from telemffb.hw import ffb_dinput

        class FakeDLL:
            def dib_build_info(self, buf, size):
                raw = _json.dumps(payload).encode()
                buf.value = raw
                return len(raw)

        class PreIdentityDLL:
            """A pre-0.9 build: the export simply is not there, which is
            an AttributeError on ACCESS, as ctypes gives it."""

        dll = FakeDLL() if payload is not None else PreIdentityDLL()
        monkeypatch.setattr(ffb_dinput.DIBridge, '_load_library',
                            classmethod(lambda cls, path=None: dll))
        return ffb_dinput

    def test_a_missing_dll_reads_as_not_installed(self, monkeypatch):
        from telemffb.hw import ffb_dinput
        monkeypatch.setattr(
            ffb_dinput.DIBridge, '_load_library',
            classmethod(lambda cls, path=None: (_ for _ in ()).throw(
                ffb_dinput.DIBridgeError('Unable to load dinput_ffb.dll from: x'))))
        status = ffb_dinput.bridge_status()
        assert not status.installed
        assert status.problem == 'not installed'

    def test_a_build_without_a_fuse_is_simply_installed(self, monkeypatch):
        mod = self._fake_dll(monkeypatch, {'version': '1.0.0', 'abi': 1,
                                           'built': 'Aug 26 2026'})
        status = mod.bridge_status()
        assert status.installed and status.version == '1.0.0'
        assert status.days_left is None and not status.problem

    def test_a_live_beta_reports_its_remaining_days(self, monkeypatch):
        from datetime import date, timedelta
        expires = (date.today() + timedelta(days=6)).isoformat()
        mod = self._fake_dll(monkeypatch, {'version': '0.9.2', 'abi': 1,
                                           'expires': expires})
        status = mod.bridge_status()
        assert status.days_left == 6 and not status.problem

    def test_an_expired_beta_is_a_problem(self, monkeypatch):
        from datetime import date, timedelta
        expires = (date.today() - timedelta(days=2)).isoformat()
        mod = self._fake_dll(monkeypatch, {'version': '0.9.2', 'abi': 1,
                                           'expires': expires})
        status = mod.bridge_status()
        assert status.days_left == -2
        assert status.problem.startswith('expired')

    def test_an_unreadable_expiry_is_a_problem(self, monkeypatch):
        mod = self._fake_dll(monkeypatch, {'version': '0.9.2', 'abi': 1,
                                           'expires': 'soonish'})
        status = mod.bridge_status()
        assert 'unreadable expiry' in status.problem

    def test_a_pre_identity_build_is_installed_but_nameless(self, monkeypatch):
        mod = self._fake_dll(monkeypatch, None)      # no build-info export
        status = mod.bridge_status()
        assert status.installed and not status.version


class TestBridgeStatusLabel:
    """The line beside the DirectInput toggle: which utility is
    installed, which build, and how long a beta is good for - the
    toggle alone cannot answer that."""

    def _dialog_with(self, app, tmp_path, monkeypatch, status):
        from tests.test_tap_workflows import World
        world = World(tmp_path, monkeypatch, random.Random(0), settings={
            'pidJoystick': '2054', 'pidPedals': '2052', 'masterInstance': 1,
            'themeId': 2, 'enableDirectInput': True}, bridge=status)
        return world.dialog

    def status(self, **kw):
        from telemffb.hw.ffb_dinput import BridgeStatus
        return BridgeStatus(**kw)

    def test_a_missing_bridge_says_so(self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            installed=False, problem='not installed'))
        text = dlg.lab_dinput_status.text()
        assert 'not installed' in text
        assert dlg.lab_dinput_status.styleSheet()      # flagged for attention

    def test_a_settled_build_is_named_and_unflagged(
            self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            installed=True, version='1.0.0'))
        assert 'DirectLink 1.0.0' in dlg.lab_dinput_status.text()
        assert not dlg.lab_dinput_status.styleSheet()

    def test_a_beta_shows_its_expiry_and_days(
            self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            installed=True, version='0.9.2', expires='2026-09-01',
            days_left=6))
        text = dlg.lab_dinput_status.text()
        assert '0.9.2' in text and '2026-09-01' in text and '6 days' in text
        assert dlg.lab_dinput_status.styleSheet()      # inside the warn window

    def test_a_distant_expiry_is_stated_without_alarm(
            self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            installed=True, version='0.9.2', expires='2027-01-01',
            days_left=120))
        assert '120 days' in dlg.lab_dinput_status.text()
        assert not dlg.lab_dinput_status.styleSheet()

    def test_an_expired_build_is_flagged(self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            installed=True, version='0.9.2', expires='2026-08-01',
            days_left=-25, problem='expired 2026-08-01'))
        assert 'expired 2026-08-01' in dlg.lab_dinput_status.text()
        assert dlg.lab_dinput_status.styleSheet()


class TestMinimumBridgeVersion:
    """TelemFFB declares the oldest bridge build it accepts
    (G.dinput_bridge_min_version).  It keeps a TelemFFB that depends on
    newer bridge behavior from running against a build that lacks it -
    and, being the pairing actually tested together, doubles as a light
    gate on redistributed builds."""

    def test_ordering(self):
        from telemffb.hw.ffb_dinput import version_is_at_least as ok
        assert ok('0.9.2', '0.9.2')
        assert ok('0.9.3', '0.9.2')
        assert ok('1.0.0', '0.9.2')
        assert ok('0.10.0', '0.9.2')          # not string ordering
        assert not ok('0.9.1', '0.9.2')
        assert not ok('0.8.9', '0.9.2')

    def test_short_and_padded_versions_compare_equal(self):
        from telemffb.hw.ffb_dinput import version_is_at_least as ok
        assert ok('0.9', '0.9.0')
        assert ok('1', '1.0.0')

    def test_an_unknown_version_cannot_vouch_for_itself(self):
        from telemffb.hw.ffb_dinput import version_is_at_least as ok
        assert not ok('', '0.9.2')
        assert not ok('unreleased', '0.9.2')

    def test_no_minimum_configured_accepts_anything(self):
        from telemffb.hw.ffb_dinput import version_is_at_least as ok
        assert ok('0.0.1', '')
        assert ok('', '')

    def test_an_old_build_is_refused_with_both_versions_named(
            self, monkeypatch):
        import telemffb.globals as G
        from telemffb.hw import ffb_dinput
        monkeypatch.setattr(G, 'dinput_bridge_min_version', '0.9.2',
                            raising=False)

        class FakeBridge:
            build_info = {'version': '0.9.0', 'abi': 1}
        monkeypatch.setattr(ffb_dinput, 'DIBridge',
                            lambda *a, **k: FakeBridge())
        available, reason = ffb_dinput.bridge_availability()
        assert not available
        assert '0.9.0' in reason and '0.9.2' in reason

    def test_a_current_build_passes_the_gate(self, monkeypatch):
        import telemffb.globals as G
        from telemffb.hw import ffb_dinput
        monkeypatch.setattr(G, 'dinput_bridge_min_version', '0.9.2',
                            raising=False)

        class FakeBridge:
            build_info = {'version': '0.9.2', 'abi': 1}
        monkeypatch.setattr(ffb_dinput, 'DIBridge',
                            lambda *a, **k: FakeBridge())
        available, reason = ffb_dinput.bridge_availability()
        assert available and reason == ''

    def test_status_reports_the_shortfall(self, monkeypatch):
        import telemffb.globals as G
        from telemffb.hw import ffb_dinput
        monkeypatch.setattr(G, 'dinput_bridge_min_version', '0.9.2',
                            raising=False)

        class FakeDLL:
            def dib_build_info(self, buf, size):
                import json
                raw = json.dumps({'version': '0.9.0', 'abi': 1}).encode()
                buf.value = raw
                return len(raw)
        monkeypatch.setattr(ffb_dinput.DIBridge, '_load_library',
                            classmethod(lambda cls, path=None: FakeDLL()))
        status = ffb_dinput.bridge_status()
        assert status.installed
        assert 'older than the 0.9.2' in status.problem

    def test_the_shipped_dll_meets_the_shipped_minimum(self):
        """The DLL in dll/ and the minimum in globals.py travel together;
        a bump to one without the other breaks the app on launch."""
        import telemffb.globals as G
        from telemffb.hw.ffb_dinput import bridge_status, version_is_at_least
        status = bridge_status()
        if not status.installed or not status.version:
            pytest.skip('no identifiable bridge DLL in this checkout')
        assert version_is_at_least(status.version,
                                   G.dinput_bridge_min_version)


class TestStatusLineFollowsTheToggle:
    """The bridge is a utility most owners have never heard of.  Its
    status belongs beside the switch that uses it and nowhere else - a
    line about a missing DLL, to someone who never wanted one, reads as
    something being broken."""

    def _world(self, tmp_path, monkeypatch, dinput_on):
        from tests.test_tap_workflows import World
        return World(tmp_path, monkeypatch, random.Random(0), settings={
            'pidJoystick': '2054', 'pidPedals': '2052', 'masterInstance': 1,
            'themeId': 2, 'enableDirectInput': dinput_on})

    def test_hidden_while_directinput_is_off(self, app, tmp_path, monkeypatch):
        dlg = self._world(tmp_path, monkeypatch, False).dialog
        assert dlg.lab_dinput_status.isHidden()

    def test_shown_while_directinput_is_on(self, app, tmp_path, monkeypatch):
        dlg = self._world(tmp_path, monkeypatch, True).dialog
        assert not dlg.lab_dinput_status.isHidden()
        assert dlg.lab_dinput_status.text()

    def test_it_follows_the_toggle_live(self, app, tmp_path, monkeypatch):
        dlg = self._world(tmp_path, monkeypatch, False).dialog
        assert dlg.lab_dinput_status.isHidden()
        dlg.cb_enable_dinput.setChecked(True)
        assert not dlg.lab_dinput_status.isHidden()
        dlg.cb_enable_dinput.setChecked(False)
        assert dlg.lab_dinput_status.isHidden()
