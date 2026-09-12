"""Finding a DCS install from the log the game writes at startup.

The registry path is written once, at install time, and is never corrected
when the folder is moved by hand - a standalone install that has been
relocated is then invisible to every lookup, since it is not in a Steam
library either.  The log is written by the running game and lives in Saved
Games, so it survives the move and names where the executable actually was.

These build fake Saved Games trees rather than reading the machine, so they
say the same thing on a developer's box and a build agent.
"""
import os
import time

import pytest
from PyQt6 import QtWidgets

from telemffb import tap_install
from telemffb.tap_install import SIMS_BY_KEY, resolve_root

pytestmark = [pytest.mark.unit]

COMMAND_LINE = ('2026-09-11 16:43:05.803 INFO    APP (Main): Command line: '
                '"{exe}" --force_disable_VR --no-launcher\n')

PREAMBLE = ('=== Log opened UTC 2026-09-11 20:43:03\n'
            '2026-09-11 16:43:05.800 INFO    APP (Main): DCS/2.9.0.0 (x86_64; '
            'Windows NT 10.0.26200)\n')


def write_log(saved_games, folder, exe, name="dcs.log", preamble=PREAMBLE,
              mtime=None):
    """One write directory's log, naming the executable that ran."""
    logs = os.path.join(str(saved_games), folder, "Logs")
    os.makedirs(logs, exist_ok=True)
    path = os.path.join(logs, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(preamble)
        handle.write(COMMAND_LINE.format(exe=exe))
        handle.write("2026-09-11 16:43:05.900 INFO    APP (Main): Command "
                     "line was here, not again\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def make_tree(root, relpaths):
    for rel in relpaths:
        path = os.path.join(str(root), rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"exe")
    return str(root)


@pytest.fixture
def saved_games(tmp_path, monkeypatch):
    """A Saved Games folder of our own, wherever the real one is."""
    import telemffb.winpaths as winpaths
    root = tmp_path / "SavedGames"
    root.mkdir()
    monkeypatch.setattr(winpaths, 'get_path', lambda *a, **k: str(root))
    return root


class TestRootFromLog:
    def test_the_root_is_the_parent_of_the_executable_directory(self, saved_games, tmp_path):
        write_log(saved_games, "DCS", str(tmp_path / "Game" / "bin-mt" / "DCS.exe"))
        assert tap_install.dcs_log_roots() == [str(tmp_path / "Game")]

    def test_the_single_thread_executable_resolves_the_same_way(self, saved_games, tmp_path):
        write_log(saved_games, "DCS", str(tmp_path / "Game" / "bin" / "DCS.exe"))
        assert tap_install.dcs_log_roots() == [str(tmp_path / "Game")]

    def test_an_executable_somewhere_else_is_not_a_root(self, saved_games, tmp_path):
        """Only bin and bin-mt are known; anything else is not a game root
        one level up, and guessing would hand back a parent directory."""
        write_log(saved_games, "DCS", str(tmp_path / "Game" / "other" / "DCS.exe"))
        assert tap_install.dcs_log_roots() == []

    def test_a_log_without_a_command_line_yields_nothing(self, saved_games):
        logs = os.path.join(str(saved_games), "DCS", "Logs")
        os.makedirs(logs)
        with open(os.path.join(logs, "dcs.log"), "w", encoding="utf-8") as handle:
            handle.write(PREAMBLE)
        assert tap_install.dcs_log_roots() == []

    def test_no_saved_games_folders_yield_nothing(self, saved_games):
        assert tap_install.dcs_log_roots() == []

    def test_the_command_line_is_not_looked_for_past_the_head_of_the_file(
            self, saved_games, tmp_path):
        """A session log runs to tens of megabytes; the line is written
        before anything else or it is not there at all."""
        filler = "2026-09-11 16:43:05.803 INFO    APP: filler\n" * 500
        write_log(saved_games, "DCS", str(tmp_path / "Game" / "bin" / "DCS.exe"),
                  preamble=filler)
        assert tap_install.dcs_log_roots() == []


class TestWriteDirectories:
    def test_every_dcs_write_directory_is_read(self, saved_games, tmp_path):
        """--write-dir gives a module or a mission its own folder, and any
        of them may hold the most recent session."""
        write_log(saved_games, "DCS_OH58D",
                  str(tmp_path / "Game" / "bin-mt" / "DCS.exe"))
        assert tap_install.dcs_log_roots() == [str(tmp_path / "Game")]

    def test_folders_that_are_not_dcs_are_left_alone(self, saved_games, tmp_path):
        write_log(saved_games, "IL-2",
                  str(tmp_path / "Game" / "bin-mt" / "DCS.exe"))
        assert tap_install.dcs_log_roots() == []

    def test_the_newest_log_is_offered_first(self, saved_games, tmp_path):
        now = time.time()
        write_log(saved_games, "DCS_F14",
                  str(tmp_path / "Old" / "bin-mt" / "DCS.exe"), mtime=now - 9000)
        write_log(saved_games, "DCS",
                  str(tmp_path / "New" / "bin-mt" / "DCS.exe"), mtime=now)
        assert tap_install.dcs_log_roots() == [str(tmp_path / "New"),
                                               str(tmp_path / "Old")]

    def test_the_previous_session_log_counts_too(self, saved_games, tmp_path):
        now = time.time()
        write_log(saved_games, "DCS", str(tmp_path / "Game" / "bin" / "DCS.exe"),
                  name="dcs.log.old", mtime=now)
        assert tap_install.dcs_log_roots() == [str(tmp_path / "Game")]

    def test_one_root_is_offered_once_however_many_logs_name_it(
            self, saved_games, tmp_path):
        exe = str(tmp_path / "Game" / "bin-mt" / "DCS.exe")
        write_log(saved_games, "DCS", exe)
        write_log(saved_games, "DCS", exe, name="dcs.log.old")
        write_log(saved_games, "DCS_F4E", exe)
        assert tap_install.dcs_log_roots() == [str(tmp_path / "Game")]


class TestResolution:
    def test_the_log_finds_an_install_the_registry_lost(
            self, saved_games, tmp_path, monkeypatch):
        """The reported case: the folder was moved to another drive and the
        registry still names the old one."""
        dcs = SIMS_BY_KEY['DCS']
        moved = make_tree(tmp_path / "F" / "DCS World", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots',
                            lambda: [str(tmp_path / "C" / "DCS World")])
        write_log(saved_games, "DCS", os.path.join(moved, "bin-mt", "DCS.exe"))

        assert resolve_root(dcs) == (moved, "DCS log")

    def test_a_registry_path_that_still_exists_is_preferred(
            self, saved_games, tmp_path, monkeypatch):
        """The installer's own record, while it is still true, names the
        install the user is running rather than one they last logged."""
        dcs = SIMS_BY_KEY['DCS']
        registry = make_tree(tmp_path / "registry", dcs.exe_relpaths)
        logged = make_tree(tmp_path / "logged", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [registry])
        write_log(saved_games, "DCS", os.path.join(logged, "bin", "DCS.exe"))

        assert resolve_root(dcs) == (registry, "registry")

    def test_a_configured_path_still_beats_the_log(
            self, saved_games, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        configured = make_tree(tmp_path / "chosen", dcs.exe_relpaths)
        logged = make_tree(tmp_path / "logged", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [])
        write_log(saved_games, "DCS", os.path.join(logged, "bin", "DCS.exe"))

        assert resolve_root(dcs, configured) == (configured, "configured in TelemFFB")

    def test_a_logged_path_that_no_longer_holds_the_game_falls_through(
            self, saved_games, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        steam = make_tree(tmp_path / "steam", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [steam])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [])
        write_log(saved_games, "DCS",
                  str(tmp_path / "deleted" / "bin-mt" / "DCS.exe"))

        assert resolve_root(dcs) == (steam, "Steam library")

    def test_a_failing_lookup_does_not_stop_the_others(
            self, saved_games, tmp_path, monkeypatch):
        """One unreadable source must not cost the sim its other chances."""
        dcs = SIMS_BY_KEY['DCS']
        logged = make_tree(tmp_path / "logged", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])

        def boom():
            raise OSError("registry unavailable")

        monkeypatch.setattr(tap_install, 'dcs_registry_roots', boom)
        write_log(saved_games, "DCS", os.path.join(logged, "bin", "DCS.exe"))

        assert resolve_root(dcs) == (logged, "DCS log")


class TestFailureReport:
    def test_every_rejected_path_is_logged_with_where_it_came_from(
            self, saved_games, tmp_path, monkeypatch, caplog):
        """A sim that is installed but not found is a support question, and
        the answer is which paths were rejected."""
        dcs = SIMS_BY_KEY['DCS']
        stale = str(tmp_path / "gone")
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [stale])
        monkeypatch.setattr(tap_install, 'dcs_log_roots', lambda: [])

        with caplog.at_level('INFO'):
            assert resolve_root(dcs)[0] is None

        assert stale in caplog.text
        assert "registry" in caplog.text

    def test_steam_folders_are_counted_rather_than_listed(
            self, tmp_path, monkeypatch, caplog):
        """Every installed Steam app is a candidate; listing them all would
        bury the paths that were claims about this sim."""
        dcs = SIMS_BY_KEY['DCS']
        apps = [str(tmp_path / f"app{n}") for n in range(3)]
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: apps)
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_log_roots', lambda: [])

        with caplog.at_level('INFO'):
            resolve_root(dcs)

        assert "3" in caplog.text
        for app in apps:
            assert app not in caplog.text


@pytest.fixture(scope="module")
def app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestManualPath:
    def test_dcs_and_bms_can_be_pointed_at_a_folder_by_hand(self):
        """Both are registry-only otherwise, so a stale or missing key left
        them with no way back; the settings key is also what tells the UI to
        offer a field."""
        assert SIMS_BY_KEY['DCS'].settings_key == 'pathDCS'
        assert SIMS_BY_KEY['BMS'].settings_key == 'pathBMS'

    def test_every_path_setting_names_the_field_that_edits_it(self, app):
        """The dialog reads the field by the setting's own name, so a sim
        that claims a path setting and has no field would silently fall
        back to whatever was last saved."""
        from PyQt6 import QtWidgets

        from telemffb.tap_install import SIMS
        from telemffb.ui.Ui_SystemDialog import Ui_SystemDialog

        ui = Ui_SystemDialog()
        ui.setupUi(QtWidgets.QDialog())
        for sim in SIMS:
            if sim.settings_key:
                field = getattr(ui, sim.settings_key, None)
                assert isinstance(field, QtWidgets.QLineEdit), sim.key

    def test_every_path_setting_has_a_default(self):
        """A key the dialog writes and nothing declares reads back as None
        on a machine that has never saved settings."""
        from telemffb.tap_install import SIMS
        from telemffb.utils import SystemSettings

        for sim in SIMS:
            if sim.settings_key:
                assert sim.settings_key in SystemSettings.globl_sys_dict, sim.key

    def test_a_second_install_can_be_forced_over_the_detected_one(
            self, saved_games, tmp_path, monkeypatch):
        """More than one DCS on the machine: the one named here wins over
        the registry and the log, which is the whole point of the field."""
        dcs = SIMS_BY_KEY['DCS']
        first = make_tree(tmp_path / "first", dcs.exe_relpaths)
        second = make_tree(tmp_path / "second", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [first])
        write_log(saved_games, "DCS", os.path.join(first, "bin-mt", "DCS.exe"))

        status = tap_install.sim_status(dcs, second)
        assert status.root == second
        assert status.provenance == "configured in TelemFFB"
        assert status.rejected_configured is None

    def test_a_configured_bms_path_is_used(self, tmp_path, monkeypatch):
        bms = SIMS_BY_KEY['BMS']
        configured = make_tree(tmp_path / "BMS", bms.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'bms_registry_roots', lambda: [])

        assert resolve_root(bms, configured) == (configured,
                                                 "configured in TelemFFB")


class TestRejectedPath:
    """A path set by hand that does not hold the sim.

    Resolution falls through it either way - a setting left behind by a
    move must not strand a sim that is findable elsewhere - but the whole
    reason to set one is that detection got it wrong, so ignoring it in
    silence reads as it having worked.
    """

    def test_a_path_without_the_sim_is_reported(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        elsewhere = make_tree(tmp_path / "real", dcs.exe_relpaths)
        nonsense = str(tmp_path / "nonsense")
        os.makedirs(nonsense)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [elsewhere])
        monkeypatch.setattr(tap_install, 'dcs_log_roots', lambda: [])

        status = tap_install.sim_status(dcs, nonsense)
        assert status.rejected_configured == nonsense
        assert status.root == elsewhere, "the sim is still found elsewhere"

    def test_it_is_reported_when_nothing_is_found_either(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        nonsense = str(tmp_path / "nonsense")
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_log_roots', lambda: [])

        status = tap_install.sim_status(dcs, nonsense)
        assert status.rejected_configured == nonsense
        assert status.root is None

    def test_a_working_path_is_not_reported(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        chosen = make_tree(tmp_path / "chosen", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_log_roots', lambda: [])

        assert tap_install.sim_status(dcs, chosen).rejected_configured is None

    def test_nothing_is_reported_when_no_path_was_set(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_log_roots', lambda: [])

        assert tap_install.sim_status(dcs).rejected_configured is None

    def test_a_nested_layout_is_judged_after_normalizing(self, tmp_path, monkeypatch):
        """IL-2 Korea's standalone release nests the game one level down,
        so the folder the user points at is not itself the game root."""
        korea = SIMS_BY_KEY['IL2_K']
        outer = tmp_path / "IL-2 Korea"
        make_tree(outer / "game", ("bin/game/IL2Series.exe",))
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])

        status = tap_install.sim_status(korea, str(outer))
        assert status.rejected_configured is None
        assert status.root == korea.normalize_root(str(outer))

    def test_the_panel_flags_it(self, app, tmp_path):
        """Drawn in the attention color rather than as another dim line:
        it is the one thing on the panel the user can act on."""
        from telemffb.TapStatusPanel import TapStatusPanel
        from telemffb.tap_install import SimStatus

        nonsense = str(tmp_path / "nonsense")
        panel = TapStatusPanel(SimStatus(sim=SIMS_BY_KEY['DCS'], root=None,
                                         provenance="not found",
                                         rejected_configured=nonsense))
        amber = panel._color("attention")
        flagged = [w for w in panel.findChildren(QtWidgets.QLabel)
                   if amber in w.styleSheet() and w.text() and not w.isHidden()]
        assert flagged, "the rejected path is called out"
        assert any(nonsense == w.toolTip() for w in flagged), \
            "the path itself is reachable, however long it is"

    def test_the_panel_says_nothing_when_the_path_works(self, app, tmp_path):
        from telemffb.TapStatusPanel import TapStatusPanel
        from telemffb.tap_install import SimStatus

        panel = TapStatusPanel(SimStatus(sim=SIMS_BY_KEY['DCS'], root=None,
                                         provenance="not found"))
        amber = panel._color("attention")
        assert not [w for w in panel.findChildren(QtWidgets.QLabel)
                    if amber in w.styleSheet() and w.text()
                    and not w.isHidden()]

    def test_the_folders_a_root_holds_are_named_not_the_executable(self):
        """What the user is told to pick.  The executable sits one or two
        levels below the root, so naming it would send them into the
        executable's own folder, which is not a root the signature
        accepts."""
        assert SIMS_BY_KEY['DCS'].root_contents == ('bin', 'bin-mt')
        assert SIMS_BY_KEY['BMS'].root_contents == ('Bin',)
        assert SIMS_BY_KEY['IL2'].root_contents == ('bin',)

    def test_a_root_that_holds_one_folder_twice_names_it_once(self):
        from telemffb.tap_install import TapSim
        sim = TapSim(key='X', name='X', exe_relpaths=('bin/a.exe', 'bin/b.exe'))
        assert sim.root_contents == ('bin',)


class TestIL2SharedPathFields:
    """IL-2 has no path row in its tap section: the field its auto
    telemetry setup already uses is the same one the tap needs, and a
    second field for the same setting would be two ways to disagree.

    That field used to be editable only while auto telemetry setup was on,
    which left a user who sets telemetry up by hand unable to point the
    tap at the game - IL-2 records nothing in the registry, so a configured
    path and a Steam scan are all the tap has.
    """

    @staticmethod
    def enablement(sim_on, auto_on, tap_on):
        from telemffb.SystemSettingsDialog import SystemSettingsDialog as D

        class Box:
            def __init__(self, on): self.on = on
            def isChecked(self): return self.on

        class Widget:
            def __init__(self): self.enabled = None
            def setEnabled(self, on): self.enabled = on

        holder = type('Holder', (), dict(
            IL2_PATH_FIELDS=D.IL2_PATH_FIELDS,
            refresh_il2_path_fields=D.refresh_il2_path_fields))()
        holder.enableIL2 = Box(sim_on)
        for auto, tap, label, field, browse in D.IL2_PATH_FIELDS:
            setattr(holder, auto, Box(auto_on))
            setattr(holder, tap, Box(tap_on))
            for name in (label, field, browse):
                setattr(holder, name, Widget())
        holder.refresh_il2_path_fields()
        return {name: getattr(holder, name).enabled
                for _, _, label, field, browse in D.IL2_PATH_FIELDS
                for name in (label, field, browse)}

    def test_auto_telemetry_setup_still_enables_them(self):
        assert all(self.enablement(True, True, False).values())

    def test_the_tap_enables_them_on_its_own(self):
        assert all(self.enablement(True, False, True).values())

    def test_neither_leaves_them_alone(self):
        assert not any(self.enablement(True, False, False).values())

    def test_the_sim_being_off_wins_over_both(self):
        assert not any(self.enablement(False, True, True).values())

    def test_il2_has_no_path_row_of_its_own_in_the_tap_section(self):
        from telemffb.SystemSettingsDialog import SystemSettingsDialog as D
        assert set(D.TAP_PATH_WIDGETS) == {'DCS', 'BMS'}


class TestPathEntryValidation:
    """A path is checked where it is entered.

    The field exists to override detection, so storing one that cannot be
    used would look like it had taken effect.  A folder that is not the
    game is refused outright and the field keeps what it had.
    """

    @staticmethod
    def holder(sim_key, accepted='', monkeypatch=None):
        from PyQt6 import QtWidgets as W

        from telemffb.SystemSettingsDialog import SystemSettingsDialog as D
        from telemffb.tap_install import SIMS_BY_KEY

        warned = []

        class Holder:
            _accepted_paths = None
            _validating_path = False
            _sim_path_accepted = D._sim_path_accepted
            _on_sim_path_edited = D._on_sim_path_edited

            def refresh_tap_panels(self):
                self.refreshed = True

        holder = Holder()
        holder.refreshed = False
        holder._accepted_paths = {sim_key: accepted}
        holder.warned = warned
        setattr(holder, SIMS_BY_KEY[sim_key].settings_key, W.QLineEdit(accepted))
        monkeypatch.setattr(
            'telemffb.SystemSettingsDialog.QMessageBox.warning',
            lambda *a, **k: warned.append(a))
        return holder

    def field(self, holder, sim_key):
        from telemffb.tap_install import SIMS_BY_KEY
        return getattr(holder, SIMS_BY_KEY[sim_key].settings_key)

    def test_a_real_root_is_accepted(self, app, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "DCS", dcs.exe_relpaths)
        holder = self.holder('DCS', monkeypatch=monkeypatch)
        assert holder._sim_path_accepted('DCS', root) is True
        assert holder.warned == []

    def test_a_folder_without_the_game_is_refused_and_explained(
            self, app, tmp_path, monkeypatch):
        holder = self.holder('DCS', monkeypatch=monkeypatch)
        assert holder._sim_path_accepted('DCS', str(tmp_path)) is False
        assert holder.warned, "the user is told, rather than left guessing"

    def test_the_executables_own_folder_is_refused(
            self, app, tmp_path, monkeypatch):
        """The mistake the old wording invited: the executable's folder
        holds the executable but is not a root the signature accepts."""
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "DCS", dcs.exe_relpaths)
        holder = self.holder('DCS', monkeypatch=monkeypatch)
        assert holder._sim_path_accepted('DCS', os.path.join(root, "bin")) is False

    def test_a_refused_entry_puts_the_field_back(
            self, app, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        good = make_tree(tmp_path / "DCS", dcs.exe_relpaths)
        holder = self.holder('DCS', accepted=good, monkeypatch=monkeypatch)
        self.field(holder, 'DCS').setText(str(tmp_path / "nonsense"))

        holder._on_sim_path_edited('DCS')

        assert self.field(holder, 'DCS').text() == good
        assert holder._accepted_paths['DCS'] == good
        assert holder.warned

    def test_an_accepted_entry_is_kept_and_redrawn(
            self, app, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        chosen = make_tree(tmp_path / "DCS", dcs.exe_relpaths)
        holder = self.holder('DCS', monkeypatch=monkeypatch)
        self.field(holder, 'DCS').setText(chosen)

        holder._on_sim_path_edited('DCS')

        assert holder._accepted_paths['DCS'] == chosen
        assert holder.refreshed
        assert holder.warned == []

    def test_clearing_the_field_hands_detection_back_the_job(
            self, app, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        good = make_tree(tmp_path / "DCS", dcs.exe_relpaths)
        holder = self.holder('DCS', accepted=good, monkeypatch=monkeypatch)
        self.field(holder, 'DCS').setText('')

        holder._on_sim_path_edited('DCS')

        assert holder._accepted_paths['DCS'] == ''
        assert holder.warned == []

    def test_leaving_an_unchanged_field_asks_nothing(
            self, app, tmp_path, monkeypatch):
        """A stale path saved before the check existed is flagged on the
        panel; re-asking every time focus passes through would nag."""
        stale = str(tmp_path / "moved away")
        holder = self.holder('DCS', accepted=stale, monkeypatch=monkeypatch)

        holder._on_sim_path_edited('DCS')

        assert holder.warned == []
        assert self.field(holder, 'DCS').text() == stale


class TestIL2PathEntryValidation:
    """IL-2's fields are checked by the test Save has always made.

    A title is identified by its startup.cfg, not by the tap's executable
    signature, and the two IL-2 titles keep it in different places.  The
    check now runs where the folder is chosen as well, so a path refused
    at Save is not one the user picked several steps earlier.
    """

    @staticmethod
    def holder():
        from telemffb.SystemSettingsDialog import SystemSettingsDialog as D

        class Holder:
            IL2_STARTUP_CFG = D.IL2_STARTUP_CFG
            _il2_path_holds_the_game = D._il2_path_holds_the_game

        return Holder()

    @staticmethod
    def startup_cfg(*parts):
        path = os.path.join(*[str(part) for part in parts])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[KEY = core]\n")

    def test_great_battles_is_its_startup_cfg(self, tmp_path):
        root = tmp_path / "IL-2 Sturmovik Great Battles"
        self.startup_cfg(root, "data", "startup.cfg")
        assert self.holder()._il2_path_holds_the_game('pathIL2', str(root))

    def test_a_folder_without_it_is_refused(self, tmp_path):
        assert not self.holder()._il2_path_holds_the_game('pathIL2', str(tmp_path))

    def test_korea_standalone_nests_it_under_game(self, tmp_path):
        outer = tmp_path / "IL2Series Korea"
        self.startup_cfg(outer, "game", "data", "startup.cfg")
        assert self.holder()._il2_path_holds_the_game('pathIL2_K', str(outer))

    def test_korea_on_steam_keeps_it_directly_below(self, tmp_path):
        root = tmp_path / "IL2 Korea"
        self.startup_cfg(root, "data", "startup.cfg")
        assert self.holder()._il2_path_holds_the_game('pathIL2_K', str(root))

    def test_an_empty_path_is_not_a_game_folder(self):
        assert not self.holder()._il2_path_holds_the_game('pathIL2', '')

    def test_the_tap_signature_is_not_what_il2_is_judged_by(self, tmp_path):
        """The executable proves nothing here: an install missing its
        startup.cfg is one auto telemetry setup cannot work with, and that
        is the question this field has always asked."""
        root = make_tree(tmp_path / "IL2", SIMS_BY_KEY['IL2'].exe_relpaths)
        assert not self.holder()._il2_path_holds_the_game('pathIL2', root)

    def test_saving_and_picking_ask_the_same_question(self):
        """One test, reached two ways - so a folder accepted when it was
        chosen cannot be rejected later at Save."""
        import inspect

        from telemffb.SystemSettingsDialog import SystemSettingsDialog as D
        for method in (D.validate_il2_path, D.select_il2_directory):
            assert '_il2_path_holds_the_game' in inspect.getsource(method)
