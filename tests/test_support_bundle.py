"""Support bundle contents — user-supplied report context, which logs, and
the DirectInput tap's setup in each game.

The exception-report dialog collects an optional Discord username and
free-text notes; both must land in user_report.txt as the FIRST entry of
the bundle so support can map a bundle to a Discord user at a glance.
"""
import io
import os
import zipfile

import pytest

import telemffb.globals as G
from telemffb.tap import tap_install as ti
from telemffb.utils import create_support_bundle_data
from telemffb.utils.network import _bundle_log_files, _tap_setup


def _entries(bundle_bytes):
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as z:
        return z.namelist(), {n: z.read(n).decode("utf-8", "replace")
                              for n in z.namelist() if n.endswith(".txt")}


def test_user_info_written_as_first_entry(tmp_path):
    bundle = create_support_bundle_data(
        str(tmp_path), exceptions=None,
        user_info={"discord_username": "TestPilot#1234",
                   "notes": "Crashed while switching aircraft in MSFS."})
    names, texts = _entries(bundle)
    assert names[0] == "user_report.txt"
    report = texts["user_report.txt"]
    assert "Discord username: TestPilot#1234" in report
    assert "Crashed while switching aircraft in MSFS." in report


def test_user_info_fields_optional(tmp_path):
    bundle = create_support_bundle_data(
        str(tmp_path), exceptions=None,
        user_info={"discord_username": "", "notes": ""})
    names, texts = _entries(bundle)
    assert names[0] == "user_report.txt"
    report = texts["user_report.txt"]
    assert "Discord username: (not provided)" in report
    assert "(none)" in report


def test_no_user_info_omits_report_file(tmp_path):
    bundle = create_support_bundle_data(str(tmp_path), exceptions=None)
    names, _ = _entries(bundle)
    assert "user_report.txt" not in names


class TestWhichLogs:
    """Every device's current log, whole; then only as many archives as fit
    a size the report service accepts."""

    @staticmethod
    def _folder(tmp_path, archive_size=3):
        log = tmp_path / "log"
        (log / "tap").mkdir(parents=True)
        (log / "TelemFFB_joystick_20260927.log").write_text("today\n")
        (log / "TelemFFB_pedals_20260927.log").write_text("today\n")
        (log / "tap" / "dinput8_wrapper_DCS.log").write_text("tap\n")
        for day in ("20260924", "20260925", "20260926"):
            (log / f"TelemFFB_Log_Archive_{day}.zip").write_bytes(b"z" * archive_size)
        (log / "something_else.txt").write_text("not a log")
        return log

    @staticmethod
    def _names(log, **kwargs):
        return [os.path.basename(p) for p, _ in _bundle_log_files(str(log), **kwargs)]

    def test_everything_fits_so_everything_goes(self, tmp_path):
        self._folder(tmp_path)
        names, _ = _entries(create_support_bundle_data(str(tmp_path)))
        logs = sorted(n for n in names if n.startswith("log/"))
        assert logs == ["log/TelemFFB_Log_Archive_20260924.zip",
                        "log/TelemFFB_Log_Archive_20260925.zip",
                        "log/TelemFFB_Log_Archive_20260926.zip",
                        "log/TelemFFB_joystick_20260927.log",
                        "log/TelemFFB_pedals_20260927.log",
                        "log/tap/dinput8_wrapper_DCS.log"]

    def test_current_logs_go_whole_even_past_the_budget(self, tmp_path):
        log = self._folder(tmp_path)
        (log / "TelemFFB_pedals_20260927.log").write_bytes(b"x" * 5000)
        names = self._names(log, budget=1000)
        assert {"TelemFFB_joystick_20260927.log", "TelemFFB_pedals_20260927.log"} <= set(names)
        assert not [n for n in names if n.startswith("TelemFFB_Log_Archive_")]

    def test_archives_fill_what_is_left_newest_first(self, tmp_path):
        log = self._folder(tmp_path, archive_size=100)
        current = sum(os.path.getsize(log / n) for n in
                      ("TelemFFB_joystick_20260927.log", "TelemFFB_pedals_20260927.log",
                       "tap/dinput8_wrapper_DCS.log"))
        names = self._names(log, budget=current + 250)       # room for two
        archives = [n for n in names if n.startswith("TelemFFB_Log_Archive_")]
        assert archives == ["TelemFFB_Log_Archive_20260926.zip",
                            "TelemFFB_Log_Archive_20260925.zip"]


class TestTapSetup:
    """Every game's dinput8.ini goes in, beside a report of what is
    installed where."""

    @pytest.fixture
    def game(self, tmp_path, monkeypatch):
        root = tmp_path / "DCS World"
        for folder in ("bin", "bin-mt"):
            (root / folder).mkdir(parents=True)
        (root / "bin" / "dinput8.ini").write_bytes(b"[tap]\n")
        dcs = next(s for s in ti.SIMS if s.key == "DCS")
        bms = next(s for s in ti.SIMS if s.key == "BMS")

        def status(sim, configured=None):
            if sim is bms:
                raise OSError("registry unreadable")
            return ti.SimStatus(sim=sim, root=str(root), provenance="configured", targets=[
                ti.TargetStatus(str(root / "bin"), ti.WrapperState.TAP, True, "0.9.1.0"),
                ti.TargetStatus(str(root / "bin-mt"), ti.WrapperState.ABSENT, False)])
        monkeypatch.setattr(ti, "SIMS", (dcs, bms))
        monkeypatch.setattr(ti, "sim_status", status)
        monkeypatch.setattr(ti, "bundled_version", lambda: "0.9.3.0")
        return root

    def test_each_config_goes_in_under_its_game_and_folder(self, game):
        configs, _ = _tap_setup({})
        assert configs == [("tap/DCS/bin/dinput8.ini", b"[tap]\n")]

    def test_the_report_covers_every_target_folder(self, game):
        _, report = _tap_setup({})
        assert str(game / "bin") in report
        assert str(game / "bin-mt") in report

    def test_a_game_that_cannot_be_examined_does_not_stop_the_rest(self, game):
        configs, report = _tap_setup({})
        assert configs
        assert "[BMS]" in report

    def test_the_bundle_carries_them(self, game, tmp_path, monkeypatch):
        monkeypatch.setattr(G, "system_settings", {}, raising=False)
        names, _ = _entries(create_support_bundle_data(str(tmp_path)))
        assert {"tap/DCS/bin/dinput8.ini", "tap/tap_status.txt"} <= set(names)
