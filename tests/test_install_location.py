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
    """Where temp is comes from the stub, not from the machine: the real
    one moves with the user account and says nothing about the rule."""

    TMP = r"C:\Users\synthetic\AppData\Local\Temp"

    @pytest.fixture(autouse=True)
    def temp_dir(self, monkeypatch):
        monkeypatch.setattr(tempfile, 'gettempdir', lambda: self.TMP)

    def test_temp_itself_is_refused(self):
        assert unsafe_install_location_reason(self.TMP, FAKE_LOCATIONS) is not None

    def test_a_folder_under_temp_is_refused_and_blames_the_zip(self):
        reason = unsafe_install_location_reason(
            os.path.join(self.TMP, "Temp1_TelemFFB.zip"), FAKE_LOCATIONS)
        assert reason is not None and "zip" in reason

    def test_a_folder_merely_named_like_temp_is_accepted(self):
        """Prefix matching has to respect the separator, or C:\\...\\Temporary
        would be swept up with C:\\...\\Temp."""
        assert unsafe_install_location_reason(
            self.TMP + "orary", FAKE_LOCATIONS) is None


class TestResolvedLocationsAreUsed:
    """With nothing injected, the guard falls back to the shell folders it
    resolves for itself.

    The resolver is stubbed rather than allowed to read the registry: a
    test that consults the machine it runs on asserts something different
    on every machine, and a build agent has no Desktop worth naming.  What
    is worth pinning is that the fallback is consulted at all, and that
    resolution is what it consults.
    """

    RESOLVED = {
        "your Desktop": r"C:\Users\synthetic\OneDrive\Desktop",
        "your Documents folder": r"C:\Users\synthetic\Documents",
    }

    @pytest.fixture
    def resolved(self, monkeypatch):
        import telemffb.utils as utils
        monkeypatch.setattr(utils, '_shared_shell_locations',
                            lambda: dict(self.RESOLVED))

    def test_a_resolved_folder_is_flagged_without_being_passed_in(self, resolved):
        assert (unsafe_install_location_reason(
            r"C:\Users\synthetic\OneDrive\Desktop") == "your Desktop")

    def test_redirection_is_honoured_rather_than_assumed(self, resolved):
        """The Desktop is wherever resolution says - under OneDrive here.
        The path it would have had without redirection means nothing."""
        assert unsafe_install_location_reason(
            r"C:\Users\synthetic\Desktop") is None

    def test_a_subfolder_of_a_resolved_location_is_fine(self, resolved):
        assert unsafe_install_location_reason(
            r"C:\Users\synthetic\OneDrive\Desktop\TelemFFB") is None

    def test_an_unrelated_folder_is_accepted(self, resolved):
        assert unsafe_install_location_reason(r"C:\Apps\TelemFFB") is None
