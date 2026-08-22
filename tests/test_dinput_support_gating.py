"""DirectInput support is opt-in, and honest about not being available.

The bridge DLL ships separately from TelemFFB, so it can be absent, out of
date, or past a beta expiry.  Enabling support without it would list no
devices and explain nothing, which reads as "my device is missing".
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G


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
        from telemffb.hw import ffb_dinput
        monkeypatch.setattr(ffb_dinput.DIBridge, '__init__',
                            lambda self, p=None: None)
        monkeypatch.setattr(ffb_dinput.DIBridge, 'build_info',
                            {'version': '0.9.0', 'expires': '2020-01-01'},
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
