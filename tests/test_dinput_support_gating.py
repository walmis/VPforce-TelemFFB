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

from PyQt6 import QtTest, QtWidgets
from PyQt6.QtCore import Qt

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
        assert 'DirectLink' in reason
        assert 'must be installed' in reason

    def test_the_toggle_reverts_when_the_bridge_is_missing(self, dialog, monkeypatch):
        monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                            lambda *a, **k: (False, 'no bridge here'))
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: shown.append(a[2])))

        dialog.cb_enable_dinput.setChecked(True)
        # the revert is deferred by one event-loop pass, so that the switch
        # is not set from inside its own stateChanged - see the repeat-click
        # test below for what that costs
        QtWidgets.QApplication.instance().processEvents()

        assert shown, "the user was told nothing"
        assert 'no bridge here' in shown[0]
        assert not dialog.cb_enable_dinput.isChecked(), \
            "the box stayed on while doing nothing"

    def test_it_warns_again_every_time_it_is_clicked(self, dialog, monkeypatch):
        """A refusal must not be a one-shot.

        QCheckBox emits stateChanged only when the new state differs from
        the last one it published.  Reverting from inside that emission
        leaves the two out of step, and the *next* click then flips the
        switch with no signal at all: the track lit up, no warning, and
        nothing to tell the user why.
        """
        monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                            lambda *a, **k: (False, 'no bridge here'))
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: shown.append(a[2])))
        app = QtWidgets.QApplication.instance()

        for attempt in range(1, 4):
            QtTest.QTest.mouseClick(dialog.cb_enable_dinput.toggle,
                                    Qt.MouseButton.LeftButton)
            app.processEvents()
            assert len(shown) == attempt, \
                f"click {attempt} passed without a warning"
            assert not dialog.cb_enable_dinput.isChecked(), \
                f"the box stayed on after click {attempt}"

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
                                        'directlink.dlk')

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
        assert paths[0].endswith(os.path.join('dll', 'directlink.dlk'))
        assert 'telemffb' not in paths[0].lower().split(os.sep)[-3:-1], \
            "the path should be the project root, not the package directory"

    def test_the_paths_are_worked_out_when_asked(self, monkeypatch):
        """A tuple built at import time cannot know sys.executable, which is
        why this used to be wrong."""
        from telemffb.hw.ffb_dinput import DIBridge
        before = DIBridge.library_paths()
        self._frozen(monkeypatch)
        assert DIBridge.library_paths() != before

    def test_an_installed_copy_is_looked_for_first(self, monkeypatch):
        """Once the installer exists, its copy is the supported one.  A DLL
        left in dll/ by an earlier beta must not outrank it - the version
        gate would then report a shortfall the user has already fixed."""
        from telemffb.hw.ffb_dinput import DIBridge
        installed = r"C:\Users\someone\AppData\Local\DirectLink\directlink.vpx"
        monkeypatch.setattr(DIBridge, 'installed_location',
                            staticmethod(lambda: installed))
        paths = DIBridge.library_paths()
        assert paths[0] == installed
        assert len(paths) > 1, "the local fallback was dropped"

    def test_the_local_paths_are_all_there_is_without_one(self, monkeypatch):
        """Nothing installed is the normal case during the beta, when the
        DLL is dropped into dll/ by hand."""
        from telemffb.hw.ffb_dinput import DIBridge
        monkeypatch.setattr(DIBridge, 'installed_location',
                            staticmethod(lambda: None))
        assert all('DirectLink' not in p for p in DIBridge.library_paths())

    def test_the_installed_name_is_not_assumed_to_be_a_dll(self, monkeypatch):
        """The registry names the file, not the folder: the installed copy
        may carry a licensee or an extension that is not .dll, and none of
        that is TelemFFB's business."""
        from telemffb.hw.ffb_dinput import DIBridge
        odd = r"C:\somewhere\DirectLink\dl-4417.vpx"
        monkeypatch.setattr(DIBridge, 'installed_location',
                            staticmethod(lambda: odd))
        assert DIBridge.library_paths()[0] == odd

    def test_a_blank_registry_value_is_not_a_path(self, monkeypatch):
        """An empty string joins into the working directory and would load
        whatever happened to be sitting there."""
        from telemffb.hw.ffb_dinput import DIBridge
        monkeypatch.setattr(DIBridge, 'installed_location',
                            staticmethod(lambda: None))
        for path in DIBridge.library_paths():
            assert path.strip(), "an empty candidate reached the search list"

    def test_every_candidate_is_an_absolute_location(self):
        """No bare name: letting Windows search its own DLL path makes "not
        found" depend on the working directory and on whatever is already
        loaded in the process, and could pick up a directlink.dlk from
        somewhere nobody intended."""
        from telemffb.hw.ffb_dinput import DIBridge
        for path in DIBridge.library_paths():
            assert os.path.isabs(path), f"{path} is not an absolute location"

    def test_no_key_reads_as_not_installed(self, monkeypatch):
        """Absent is the normal state, not a fault: nobody has the installer
        during the beta, and OSError covers both a missing key and a missing
        value."""
        import winreg
        from telemffb.hw import ffb_dinput

        def refuse(*a, **k):
            raise OSError(2, 'The system cannot find the file specified')
        monkeypatch.setattr(winreg, 'OpenKey', refuse)
        # the reading itself, not the seam conftest stubs
        assert ffb_dinput._installed_dll_path() is None

    def test_the_recorded_path_is_returned(self, monkeypatch):
        import winreg
        from telemffb.hw import ffb_dinput
        wanted = r"C:\Users\someone\AppData\Local\DirectLink\directlink.vpx"

        class Key:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(winreg, 'OpenKey', lambda *a, **k: Key())
        monkeypatch.setattr(winreg, 'QueryValueEx',
                            lambda key, name: (wanted, 1))
        assert ffb_dinput._installed_dll_path() == wanted

    def test_an_empty_value_is_not_a_location(self, monkeypatch):
        """A key present but blank - a half-finished install, or one the
        uninstaller emptied - must read as absent rather than as the
        working directory."""
        import winreg
        from telemffb.hw import ffb_dinput

        class Key:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(winreg, 'OpenKey', lambda *a, **k: Key())
        monkeypatch.setattr(winreg, 'QueryValueEx', lambda key, name: ('   ', 1))
        assert ffb_dinput._installed_dll_path() is None

    def test_it_reads_directlinks_own_key(self, monkeypatch):
        """The whole interface between TelemFFB and the DirectLink
        installer, pinned: hive, key and value name together.

        Its own root rather than Software\\VPforce - that one is
        TelemFFB's, and DirectLink is a separate product.  The value is
        "Path", named after neither ".dll" nor any other extension, so
        that the installer stays free to choose the filename.

        Renaming any part of this silently breaks every installer already
        in the field, which is why it is asserted rather than left to the
        implementation.
        """
        import winreg
        from telemffb.hw import ffb_dinput
        opened, asked = [], []

        class Key:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(winreg, 'OpenKey',
                            lambda hive, path, *a, **k: opened.append((hive, path)) or Key())
        monkeypatch.setattr(winreg, 'QueryValueEx',
                            lambda key, name: asked.append(name) or ('x', 1))
        ffb_dinput._installed_dll_path()
        assert opened == [(winreg.HKEY_CURRENT_USER, r"Software\DirectLink")]
        assert asked == ["Path"]

    # A test here used to assert that the failure message listed the paths
    # it searched, real install ahead of the temp bundle.  The message no
    # longer names any path (see test_no_filesystem_path_is_shown), so the
    # assertion had no subject.  The ordering it depended on is still
    # covered, at the level that decides it:
    # test_a_frozen_build_prefers_the_install_over_the_bundle.


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
        assert 'obtain DirectLink from' in reason

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

    def test_no_filesystem_path_is_shown(self, monkeypatch):
        """DirectLink is installed by its own installer, so the folders
        TelemFFB searched answer a question the user cannot act on - and a
        list of them reads as an invitation to drop a file into one.  The
        bridge's own "Unable to load ... from: ..." error carries those
        paths, so it is suppressed rather than passed through."""
        from telemffb.hw import ffb_dinput
        available, reason = ffb_dinput.bridge_availability('not_here.dll')
        assert 'not_here.dll' not in reason, reason
        assert 'Looked in' not in reason, reason
        assert ':\\' not in reason and ':/' not in reason, reason

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
                ffb_dinput.DIBridgeError('Unable to load directlink.dlk from: x'))))
        status = ffb_dinput.bridge_status()
        assert not status.installed
        assert status.problem == 'not installed'

    def test_a_build_without_a_fuse_is_simply_installed(self, monkeypatch):
        mod = self._fake_dll(monkeypatch, {'version': '1.0.0', 'abi': 1,
                                           'built': 'Aug 26 2026'})
        status = mod.bridge_status()
        assert status.installed and status.version == '1.0.0'
        assert status.days_left is None and not status.problem

    def current(self):
        """A version this TelemFFB accepts.

        These tests are about what an expiry reports, so the version they
        carry is incidental - but a hardcoded one stops being current the
        next time the minimum is raised, and then they fail for a reason
        that has nothing to do with expiry.  Taken from the minimum
        itself, which is by definition acceptable."""
        import telemffb.globals as G
        return G.dinput_bridge_min_version or '1.0.0'

    def test_a_live_beta_reports_its_remaining_days(self, monkeypatch):
        from datetime import date, timedelta
        expires = (date.today() + timedelta(days=6)).isoformat()
        mod = self._fake_dll(monkeypatch, {'version': self.current(), 'abi': 1,
                                           'expires': expires})
        status = mod.bridge_status()
        assert status.days_left == 6 and not status.problem

    def test_an_expired_beta_is_a_problem(self, monkeypatch):
        from datetime import date, timedelta
        expires = (date.today() - timedelta(days=2)).isoformat()
        mod = self._fake_dll(monkeypatch, {'version': self.current(), 'abi': 1,
                                           'expires': expires})
        status = mod.bridge_status()
        assert status.days_left == -2
        assert status.problem.startswith('expired')

    def test_an_unreadable_expiry_is_a_problem(self, monkeypatch):
        mod = self._fake_dll(monkeypatch, {'version': self.current(), 'abi': 1,
                                           'expires': 'soonish'})
        status = mod.bridge_status()
        assert 'unreadable expiry' in status.problem

    def test_a_pre_identity_build_is_installed_but_nameless(self, monkeypatch):
        mod = self._fake_dll(monkeypatch, None)      # no build-info export
        status = mod.bridge_status()
        assert status.installed and not status.version

    def test_a_build_that_says_nothing_about_licensing_reads_unlicensed(
            self, monkeypatch):
        """A DLL predating the license fields must not read as licensed."""
        mod = self._fake_dll(monkeypatch, {'version': self.current(),
                                           'abi': 1})
        status = mod.bridge_status()
        assert not status.licensed
        assert status.license_days_left is None

    def test_a_full_license_has_no_expiry_to_count(self, monkeypatch):
        mod = self._fake_dll(monkeypatch, {'version': self.current(),
                                           'abi': 1, 'licensed': True})
        status = mod.bridge_status()
        assert status.licensed
        assert status.license_expires == ''
        assert status.license_days_left is None

    def test_an_evaluation_reports_its_remaining_days(self, monkeypatch):
        from datetime import date, timedelta
        expires = (date.today() + timedelta(days=8)).isoformat()
        mod = self._fake_dll(monkeypatch, {
            'version': self.current(), 'abi': 1,
            'licensed': True, 'license_expires': expires})
        status = mod.bridge_status()
        assert status.licensed and status.license_days_left == 8

    def test_a_lapsed_evaluation_counts_past_zero(self, monkeypatch):
        """Nothing enforces it yet, so the count simply goes negative -
        the dialog says 'expired' off the sign rather than off a flag."""
        from datetime import date, timedelta
        expires = (date.today() - timedelta(days=4)).isoformat()
        mod = self._fake_dll(monkeypatch, {
            'version': self.current(), 'abi': 1,
            'licensed': False, 'license_expires': expires})
        status = mod.bridge_status()
        assert status.license_days_left == -4

    def test_an_unreadable_license_expiry_is_dropped_not_shown(
            self, monkeypatch):
        """A date nothing can parse is worse than no date: it would be
        printed verbatim beside a day count that could not be computed."""
        mod = self._fake_dll(monkeypatch, {
            'version': self.current(), 'abi': 1,
            'licensed': True, 'license_expires': 'whenever'})
        status = mod.bridge_status()
        assert status.licensed
        assert status.license_expires == ''
        assert status.license_days_left is None

    def test_a_file_that_will_not_verify_is_present_but_not_licensed(
            self, monkeypatch):
        """The two have to stay separable: one says replace this file,
        the other says go and get one."""
        mod = self._fake_dll(monkeypatch, {
            'version': self.current(), 'abi': 1,
            'license_present': True, 'licensed': False})
        status = mod.bridge_status()
        assert status.license_present and not status.licensed

    def test_a_dll_predating_the_present_flag_reads_as_absent(
            self, monkeypatch):
        """Keys are additive, so an older 0.9.x reports no flag at all.
        Absent is the safe reading: it makes the line say 'no license
        file', which is what that build could already say."""
        mod = self._fake_dll(monkeypatch, {'version': self.current(),
                                           'abi': 1, 'licensed': False})
        status = mod.bridge_status()
        assert not status.license_present

    def test_a_build_refusing_its_location_says_so_in_the_report(
            self, monkeypatch):
        """location_ok=false means every device open will be refused -
        the page must learn it here, not from the first failed open."""
        mod = self._fake_dll(monkeypatch, {'version': self.current(),
                                           'abi': 1, 'location_ok': False})
        status = mod.bridge_status()
        assert status.location_ok is False

    def test_a_dll_predating_the_location_verdict_reads_as_fine(
            self, monkeypatch):
        """Pre-0.9.5 DLLs had no binding, so absence means unbound -
        and unbound builds accept any location."""
        mod = self._fake_dll(monkeypatch, {'version': self.current(),
                                           'abi': 1})
        status = mod.bridge_status()
        assert status.location_ok is True

    def test_the_licensee_never_reaches_the_settings_page(self, monkeypatch):
        """Name and email stay in the log.  On a settings page they tell
        the reader nothing they did not already know, and would put an
        email address into every screenshot of the dialog."""
        mod = self._fake_dll(monkeypatch, {
            'version': self.current(), 'abi': 1, 'licensed': True,
            'licensee': 'Ada Lovelace <ada@example.com>'})
        status = mod.bridge_status()
        assert 'ada@example.com' not in repr(status)
        assert 'Lovelace' not in repr(status)


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

class TestLicenseStatusLabel:
    """What the same line says about licensing.  Deliberately terse:
    a licensed build should say so and stop, an evaluation should say
    how long is left, and neither should name the licensee.
    """

    def _dialog_with(self, app, tmp_path, monkeypatch, status):
        from tests.test_tap_workflows import World
        world = World(tmp_path, monkeypatch, random.Random(0), settings={
            'pidJoystick': '2054', 'pidPedals': '2052', 'masterInstance': 1,
            'themeId': 2, 'enableDirectInput': True}, bridge=status)
        return world.dialog

    def status(self, **kw):
        from telemffb.hw.ffb_dinput import BridgeStatus
        kw.setdefault('version', '1.0.0')
        return BridgeStatus(installed=True, **kw)

    def test_a_licensed_build_says_licensed_and_no_more(
            self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch,
                                self.status(licensed=True))
        text = dlg.lab_dinput_status.text()
        assert 'licensed' in text
        assert 'expire' not in text
        assert not dlg.lab_dinput_status.styleSheet()

    def test_a_missing_license_is_named_not_called_installed(
            self, app, tmp_path, monkeypatch):
        """The state a reader most needs told apart from 'licensed'.
        Saying 'installed' for both left no way to see whether the key
        file had actually been put where the installer asked."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status())
        text = dlg.lab_dinput_status.text()
        assert 'DirectLink 1.0.0' in text
        assert 'no license file' in text

    def test_a_missing_license_is_not_flagged_as_a_fault(
            self, app, tmp_path, monkeypatch):
        """Nothing is enforced yet, so the device works regardless.
        Amber here would promise a consequence that does not exist -
        and this flips deliberately when enforcement lands."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status())
        assert not dlg.lab_dinput_status.styleSheet()

    def test_an_invalid_license_file_says_so_rather_than_missing(
            self, app, tmp_path, monkeypatch):
        """Telling someone to find a license file that is already on
        disk sends them looking for something they have.  A corrupt or
        truncated one needs replacing, which is a different errand."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            license_present=True, licensed=False))
        text = dlg.lab_dinput_status.text()
        assert 'not valid' in text
        assert 'no license file' not in text

    def test_an_invalid_license_file_is_flagged(
            self, app, tmp_path, monkeypatch):
        """Unlike an absent one: the user put this file there on purpose
        and it is not doing what they think it is."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            license_present=True, licensed=False))
        assert dlg.lab_dinput_status.styleSheet()

    def test_a_valid_license_is_not_second_guessed_by_the_flag(
            self, app, tmp_path, monkeypatch):
        """present=True is true of a good license too - it must not
        divert a licensed build into the invalid branch."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            license_present=True, licensed=True))
        text = dlg.lab_dinput_status.text()
        assert text.endswith('licensed')
        assert not dlg.lab_dinput_status.styleSheet()

    def test_a_misplaced_build_outranks_every_clock(
            self, app, tmp_path, monkeypatch):
        """A hand-copied release DLL loads, reports a version, and
        refuses every device open.  Saying anything else first - the
        license, the evaluation - would describe a build the user
        cannot actually use from where it sits."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            location_ok=False, licensed=True,
            license_expires='2026-09-09', license_days_left=10))
        text = dlg.lab_dinput_status.text()
        assert 'not installed with its installer' in text
        assert 'evaluation' not in text
        assert dlg.lab_dinput_status.styleSheet()

    def test_a_lapsed_evaluation_is_not_called_invalid(
            self, app, tmp_path, monkeypatch):
        """Its file is present and unlicensed, which is the invalid
        branch's shape - but it verified fine and simply ran out, and
        the expiry is the useful thing to say."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            license_present=True, licensed=False,
            license_expires='2026-08-01', license_days_left=-29))
        text = dlg.lab_dinput_status.text()
        assert 'expired 2026-08-01' in text
        assert 'not valid' not in text

    def test_a_fresh_evaluation_shows_its_days_without_alarm(
            self, app, tmp_path, monkeypatch):
        """A trial is *expected* to be counting down.  Painting it amber
        on the day it was installed would spend the warning color on
        news the user just chose."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            licensed=True, license_expires='2026-09-09',
            license_days_left=10))
        text = dlg.lab_dinput_status.text()
        assert 'evaluation' in text and '2026-09-09' in text
        assert '10 days' in text
        assert not dlg.lab_dinput_status.styleSheet()

    def test_an_evaluation_about_to_run_out_is_flagged(
            self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            licensed=True, license_expires='2026-09-01',
            license_days_left=2))
        assert '2 days' in dlg.lab_dinput_status.text()
        assert dlg.lab_dinput_status.styleSheet()

    def test_the_last_day_does_not_say_zero_days_left(
            self, app, tmp_path, monkeypatch):
        """'0 days left' reads as already gone; it still works today."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            licensed=True, license_expires='2026-08-30',
            license_days_left=0))
        text = dlg.lab_dinput_status.text()
        assert 'expires today' in text and '0 day' not in text
        assert dlg.lab_dinput_status.styleSheet()

    def test_one_day_left_is_not_pluralized(
            self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            licensed=True, license_expires='2026-08-31',
            license_days_left=1))
        assert '1 day left' in dlg.lab_dinput_status.text()

    def test_a_lapsed_evaluation_says_expired_not_a_negative_count(
            self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            licensed=False, license_expires='2026-08-01',
            license_days_left=-29))
        text = dlg.lab_dinput_status.text()
        assert 'expired 2026-08-01' in text
        assert '-29' not in text
        assert dlg.lab_dinput_status.styleSheet()

    def test_a_fuse_is_the_only_clock_shown(
            self, app, tmp_path, monkeypatch):
        """A beta goes to testers and a release goes to buyers, so the
        two clocks overlap only in a build made during the pre-release
        window.  The fuse wins there: it is the one that actually
        refuses to open a device, while the license fails open."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            version='0.9.3', expires='2026-09-06', days_left=7,
            licensed=True, license_expires='2026-09-20',
            license_days_left=21))
        text = dlg.lab_dinput_status.text()
        assert 'beta build' in text and '2026-09-06' in text
        assert 'evaluation' not in text and '2026-09-20' not in text

    def test_a_licensed_beta_still_says_only_beta(
            self, app, tmp_path, monkeypatch):
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            version='0.9.3', expires='2026-09-06', days_left=7,
            licensed=True))
        text = dlg.lab_dinput_status.text()
        assert 'beta build' in text
        assert 'licensed' not in text

    def test_a_broken_build_is_not_softened_by_a_license(
            self, app, tmp_path, monkeypatch):
        """`problem` wins the whole line: a build TelemFFB will not use
        is the only thing worth saying, and 'licensed' beside it would
        read as reassurance."""
        dlg = self._dialog_with(app, tmp_path, monkeypatch, self.status(
            version='0.9.1', licensed=True,
            problem='version 0.9.1 is older than the 0.9.3 this TelemFFB needs'))
        text = dlg.lab_dinput_status.text()
        assert 'older than' in text
        assert 'licensed' not in text
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

    # There was a test here that loaded the real DLL and checked its
    # version against G.dinput_bridge_min_version.  It could never run
    # where it mattered: DirectLink is distributed separately and
    # gitignored, so a checkout has no DLL and the test skipped itself -
    # except on a developer's machine, where it passed or failed on
    # whichever build happened to be sitting in dll/.
    #
    # Nothing synthetic replaces it.  Faking a version and asserting it
    # clears the minimum only asserts the fixture; the comparison itself
    # is covered above.  The real invariant - that the DLL we ship is not
    # older than the minimum this TelemFFB demands - is a release check
    # between two artifacts, and belongs on the release checklist rather
    # than in a suite that never sees either one.


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
