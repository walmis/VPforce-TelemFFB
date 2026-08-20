"""Unsafe-install-location guard: the auto-updater manages the entire
folder containing the executable, so running from a shared location
(Desktop, drive root, Downloads, ...) must be refused at startup.

Field incident: a release unzipped directly onto the Desktop - the updater
relocated the user's whole Desktop into the previous-version backup folder.
"""
import os
import tempfile

import pytest

from telemffb.utils import unsafe_install_location_reason

pytestmark = [pytest.mark.unit]

FAKE_LOCATIONS = {
    "your Desktop": r"C:\Users\test\OneDrive\Desktop",
    "your Documents folder": r"C:\Users\test\Documents",
    "your Downloads folder": r"C:\Users\test\Downloads",
    "your user profile folder": r"C:\Users\test",
    "the OneDrive root folder": r"C:\Users\test\OneDrive",
}


class TestDriveRoots:
    def test_drive_root_refused(self):
        assert unsafe_install_location_reason(r"C:\\", FAKE_LOCATIONS) is not None
        assert unsafe_install_location_reason(r"D:\\", FAKE_LOCATIONS) is not None
        assert "drive C:" in unsafe_install_location_reason(r"C:\\", FAKE_LOCATIONS)

    def test_normal_folder_on_drive_ok(self):
        assert unsafe_install_location_reason(r"C:\TelemFFB", FAKE_LOCATIONS) is None
        assert unsafe_install_location_reason(r"D:\Games\TelemFFB", FAKE_LOCATIONS) is None


class TestSharedFolders:
    def test_desktop_itself_refused(self):
        reason = unsafe_install_location_reason(
            r"C:\Users\test\OneDrive\Desktop", FAKE_LOCATIONS)
        assert reason == "your Desktop"

    def test_desktop_subfolder_ok(self):
        assert unsafe_install_location_reason(
            r"C:\Users\test\OneDrive\Desktop\TelemFFB", FAKE_LOCATIONS) is None

    def test_documents_downloads_profile_refused(self):
        for label, path in FAKE_LOCATIONS.items():
            assert unsafe_install_location_reason(path, FAKE_LOCATIONS) == label, label

    def test_comparison_is_case_and_slash_insensitive(self):
        assert unsafe_install_location_reason(
            r"c:\users\TEST\onedrive\desktop" + "\\", FAKE_LOCATIONS) == "your Desktop"

    def test_missing_location_values_are_skipped(self):
        locations = dict(FAKE_LOCATIONS)
        locations["the Program Files folder"] = None
        assert unsafe_install_location_reason(r"C:\TelemFFB", locations) is None


class TestTempFolder:
    def test_temp_and_subfolders_refused(self):
        tmp = tempfile.gettempdir()
        assert unsafe_install_location_reason(tmp, FAKE_LOCATIONS) is not None
        reason = unsafe_install_location_reason(
            os.path.join(tmp, "Temp1_TelemFFB.zip"), FAKE_LOCATIONS)
        assert reason is not None and "zip" in reason


class TestRealLocationResolution:
    def test_real_lookup_flags_actual_desktop(self):
        """No injected locations: the registry-resolved shell folders must
        identify this machine's real Desktop (OneDrive-redirected or not)."""
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as key:
            desktop = winreg.QueryValueEx(key, "Desktop")[0]
        assert unsafe_install_location_reason(desktop) == "your Desktop"

    def test_real_lookup_accepts_this_repo(self):
        assert unsafe_install_location_reason(os.path.dirname(__file__)) is None
