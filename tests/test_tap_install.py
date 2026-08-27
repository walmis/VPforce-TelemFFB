"""Finding the sims the DirectInput tap installs into.

A sim is identified by its signature - the executables it has, relative to
the game root - rather than by its install folder name, because the name is
the least reliable thing about a game install: it varies by storefront, and
IL-2 Korea's standalone release nests the whole game one level deeper than
its sibling title does.

These build fake install trees rather than reading the machine, so they say
the same thing on a developer's box and a build agent.
"""
import os

import pytest

from telemffb import tap_install
from telemffb.tap_install import (
    SIMS_BY_KEY, SimStatus, TapSim, TargetStatus, WRAPPER_CONFIG,
    WRAPPER_NAME, WrapperState, generate_config, install, outdated_targets,
    resolve_root, sim_status, tap_log_dir, target_dirs, wrapper_state,
)

pytestmark = [pytest.mark.unit]

TAP_BYTES = b"MZ\x00\x00 ... FFB tap: device [%ls] bound to block %d ... \x00"
FOREIGN_BYTES = b"MZ\x00\x00 ReShade 5.9 ... \x00"


def make_tree(root, relpaths, wrapper=None, config=False):
    """A fake install: the executables, and optionally a dinput8.dll."""
    for rel in relpaths:
        path = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"exe")
        if wrapper is not None:
            with open(os.path.join(os.path.dirname(path), "dinput8.dll"), "wb") as h:
                h.write(wrapper)
        if config:
            with open(os.path.join(os.path.dirname(path), "dinput8.ini"), "w") as h:
                h.write("[general]\n")
    return str(root)


@pytest.fixture(autouse=True)
def no_machine_lookups(monkeypatch):
    """Nothing here should depend on what is installed on this computer."""
    monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
    monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [])
    monkeypatch.setattr(tap_install, 'bms_registry_roots', lambda: [])


class TestSignatureMatching:
    def test_a_directory_is_the_sim_only_if_the_executable_is_there(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        empty = str(tmp_path / "empty")
        os.makedirs(empty)
        assert not tap_install.matches_signature(dcs, empty)

        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths)
        assert tap_install.matches_signature(dcs, root)




class TestKoreaNesting:
    """The standalone release nests the game under <root>/game; the other
    layout does not.  Both have to resolve from whatever the user points at."""

    def test_the_nested_layout_resolves_to_the_inner_directory(self, tmp_path):
        korea = SIMS_BY_KEY['IL2_K']
        outer = tmp_path / "IL2Series Korea"
        make_tree(outer / "game", korea.exe_relpaths)
        # il2_korea_game_root keys off data/startup.cfg
        os.makedirs(outer / "game" / "data", exist_ok=True)
        (outer / "game" / "data" / "startup.cfg").write_text("[KEY]")

        root, provenance = resolve_root(korea, str(outer))
        assert root == str(outer / "game")
        assert provenance == "configured in TelemFFB"

    def test_the_flat_layout_resolves_to_the_root(self, tmp_path):
        korea = SIMS_BY_KEY['IL2_K']
        outer = tmp_path / "IL2 Korea"
        make_tree(outer, korea.exe_relpaths)
        os.makedirs(outer / "data", exist_ok=True)
        (outer / "data" / "startup.cfg").write_text("[KEY]")

        root, _ = resolve_root(korea, str(outer))
        assert root == str(outer)



class TestTargets:
    def test_dcs_targets_both_executables(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths)
        dirs = target_dirs(dcs, root)
        assert [os.path.basename(d) for d in dirs] == ['bin', 'bin-mt']

    def test_a_missing_executable_is_not_a_target(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",))
        assert [os.path.basename(d) for d in target_dirs(dcs, root)] == ['bin-mt']

    def test_bms_targets_x64_only(self):
        """arm64 ships alongside but is not what the launcher runs."""
        bms = SIMS_BY_KEY['BMS']
        assert bms.exe_relpaths == ("Bin/x64/Falcon BMS.exe",)


class TestWrapperIdentity:
    def test_nothing_installed(self, tmp_path):
        assert wrapper_state(str(tmp_path)) == WrapperState.ABSENT

    def test_our_wrapper_is_recognized(self, tmp_path):
        (tmp_path / "dinput8.dll").write_bytes(TAP_BYTES)
        assert wrapper_state(str(tmp_path)) == WrapperState.TAP

    def test_someone_elses_dinput8_is_not_claimed(self, tmp_path):
        """Overwriting it would break whatever the user installed it for."""
        (tmp_path / "dinput8.dll").write_bytes(FOREIGN_BYTES)
        assert wrapper_state(str(tmp_path)) == WrapperState.FOREIGN


class TestSimStatus:
    def test_a_partial_install_is_visible(self, tmp_path):
        """The case that matters: DCS may launch the executable that was
        missed, and the tap would silently do nothing."""
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths)
        (tmp_path / "dcs" / "bin-mt" / "dinput8.dll").write_bytes(TAP_BYTES)

        status = sim_status(dcs, root)
        assert status.partially_installed
        assert not status.installed
        states = {os.path.basename(t.directory): t.state for t in status.targets}
        assert states == {'bin': WrapperState.ABSENT, 'bin-mt': WrapperState.TAP}

    def test_fully_installed(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths, wrapper=TAP_BYTES)
        status = sim_status(dcs, root)
        assert status.installed
        assert not status.partially_installed


    def test_the_optional_config_is_noticed(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",),
                         wrapper=TAP_BYTES, config=True)
        status = sim_status(dcs, root)
        assert status.targets[0].has_config


class TestCandidateOrdering:
    def test_a_configured_path_is_tried_before_the_registry(self, tmp_path, monkeypatch):
        """What the user told us beats what an installer left behind."""
        dcs = SIMS_BY_KEY['DCS']
        configured = make_tree(tmp_path / "chosen", dcs.exe_relpaths)
        registry = make_tree(tmp_path / "registry", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [registry])

        root, provenance = resolve_root(dcs, configured)
        assert root == configured
        assert provenance == "configured in TelemFFB"

    def test_the_registry_is_tried_before_a_steam_scan(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        registry = make_tree(tmp_path / "registry", dcs.exe_relpaths)
        steam = make_tree(tmp_path / "steam", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [registry])
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [steam])

        assert resolve_root(dcs)[0] == registry

    def test_a_wrong_configured_path_falls_through(self, tmp_path, monkeypatch):
        """A stale setting should not stop the sim being found elsewhere."""
        dcs = SIMS_BY_KEY['DCS']
        registry = make_tree(tmp_path / "registry", dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [registry])

        root, provenance = resolve_root(dcs, str(tmp_path / "moved away"))
        assert root == registry
        assert provenance == "registry"

    def test_steam_is_scanned_when_nothing_else_knows(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        steam = make_tree(tmp_path / "steamapps" / "common" / "DCSWorld",
                          dcs.exe_relpaths)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [steam])
        root, provenance = resolve_root(dcs)
        assert root == steam
        assert provenance == "Steam library"


class TestSteamLibraries:
    def test_every_library_is_read_not_just_the_default(self, tmp_path, monkeypatch):
        """A sim is as likely to be on a second drive as on Steam's own."""
        steam = tmp_path / "Steam"
        apps = steam / "steamapps"
        os.makedirs(apps)
        (apps / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n'
            '  "0" { "path" "%s" }\n'
            '  "1" { "path" "%s" }\n}\n'
            % (str(steam).replace("\\", "\\\\"),
               str(tmp_path / "Second").replace("\\", "\\\\")))
        monkeypatch.setattr(tap_install, '_registry_values',
                            lambda *a, **k: [str(steam)])

        roots = [r.lower() for r in tap_install.steam_library_roots()]
        assert str(steam).lower() in roots
        assert str(tmp_path / "Second").lower() in roots

    def test_a_missing_vdf_still_yields_the_steam_directory(self, tmp_path, monkeypatch):
        steam = tmp_path / "Steam"
        os.makedirs(steam)
        monkeypatch.setattr(tap_install, '_registry_values',
                            lambda *a, **k: [str(steam)])
        assert [r.lower() for r in tap_install.steam_library_roots()] == \
            [str(steam).lower()]


class TestWrapperVersion:
    """The wrapper carries a version resource so status can say which build
    is in a game folder, not merely that one is.

    Read from the file, never by loading it: it is a DirectInput hook and
    belongs in the game's process, not TelemFFB's.
    """

    def test_a_file_with_no_version_resource_reports_none(self, tmp_path):
        """Wrappers built before the resource existed are still recognized
        by their marker strings; they just cannot say which build they are."""
        plain = tmp_path / "dinput8.dll"
        plain.write_bytes(TAP_BYTES)
        assert tap_install.file_version(str(plain)) is None


    def test_status_carries_the_version_when_there_is_one(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",), wrapper=TAP_BYTES)
        monkeypatch.setattr(tap_install, 'file_version', lambda p: "0.9.0.0")
        status = sim_status(dcs, root)
        assert status.targets[0].version == "0.9.0.0"

    def test_no_version_is_read_for_a_foreign_dll(self, tmp_path, monkeypatch):
        """Its version is not ours to report, and reading it would suggest
        otherwise."""
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",), wrapper=FOREIGN_BYTES)
        called = []
        monkeypatch.setattr(tap_install, 'file_version',
                            lambda p: called.append(p) or "9.9.9")
        status = sim_status(dcs, root)
        assert status.targets[0].state == WrapperState.FOREIGN
        assert status.targets[0].version is None
        assert not called


class TestDcsFolderLayoutMayChange:
    """Both bin and bin-mt are multi-threaded builds now; Eagle Dynamics
    simply has not retired the bin-mt folder. Whichever survives, the tap
    installs into what is there and does not fail over what is not.
    """

    def test_only_bin_mt_present(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",), wrapper=TAP_BYTES)
        status = sim_status(dcs, root)
        assert status.found
        assert [os.path.basename(t.directory) for t in status.targets] == ['bin-mt']
        assert status.installed


    def test_neither_present_is_simply_not_found(self, tmp_path):
        """A directory that is not DCS must not be mistaken for a broken
        DCS - nothing should be written into it."""
        root = str(tmp_path / "not dcs")
        os.makedirs(root)
        status = sim_status(SIMS_BY_KEY['DCS'], root)
        assert not status.found
        assert status.targets == []



class TestInstall:
    """Putting the wrapper in, without damaging what is already there."""

    @pytest.fixture(autouse=True)
    def bundled(self, tmp_path_factory, monkeypatch):
        source = tmp_path_factory.mktemp("bundled") / "dinput8.dll"
        source.write_bytes(TAP_BYTES)
        monkeypatch.setattr(tap_install, 'bundled_wrapper', lambda: str(source))
        return str(source)

    def test_every_target_gets_it(self, tmp_path):
        """A sim launches whichever executable the user picked, so a wrapper
        beside only one of them does nothing at all."""
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths)
        outcomes = tap_install.install(sim_status(dcs, root))

        assert all(o.ok for o in outcomes)
        assert {o.action for o in outcomes} == {"installed"}
        assert sim_status(dcs, root).installed

    def test_a_partial_install_is_completed(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths)
        (tmp_path / "dcs" / "bin-mt" / "dinput8.dll").write_bytes(TAP_BYTES)

        outcomes = {os.path.basename(o.directory): o.action
                    for o in tap_install.install(sim_status(dcs, root))}
        assert outcomes == {'bin': 'installed', 'bin-mt': 'updated'}
        assert sim_status(dcs, root).installed

    def test_a_foreign_dll_is_not_overwritten_by_default(self, tmp_path):
        """It belongs to something the user installed deliberately, so
        replacing it takes their say-so rather than happening by default."""
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",),
                         wrapper=FOREIGN_BYTES)
        outcomes = tap_install.install(sim_status(dcs, root))

        assert not outcomes[0].ok
        assert outcomes[0].action == "skipped"
        assert "not ours" in outcomes[0].detail
        installed = tmp_path / "dcs" / "bin-mt" / "dinput8.dll"
        assert installed.read_bytes() == FOREIGN_BYTES, "it was overwritten"

    def test_it_is_overwritten_when_the_caller_says_so(self, tmp_path):
        """The user has been asked and said yes; we cannot tell what the
        file is, so the judgment is theirs to make."""
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",),
                         wrapper=FOREIGN_BYTES)
        outcomes = tap_install.install(sim_status(dcs, root),
                                       overwrite_foreign=True)

        assert outcomes[0].ok
        assert outcomes[0].action == "replaced"
        installed = tmp_path / "dcs" / "bin-mt" / "dinput8.dll"
        assert installed.read_bytes() != FOREIGN_BYTES
        assert sim_status(dcs, root).installed

    def test_a_locked_file_says_to_close_the_game(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",))

        def locked(src, dst):
            raise PermissionError(13, "in use")
        monkeypatch.setattr(tap_install.shutil, 'copyfile', locked)

        outcome = tap_install.install(sim_status(dcs, root))[0]
        assert not outcome.ok
        assert "close the game" in outcome.detail

    def test_a_missing_bundled_copy_fails_cleanly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tap_install, 'bundled_wrapper', lambda: None)
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",))
        outcome = tap_install.install(sim_status(dcs, root))[0]
        assert not outcome.ok
        assert "missing" in outcome.detail


class TestConfigStaysConsistent:
    """A sim reads the config beside whichever executable it launched, so
    one target configured and the other not makes behavior depend on a
    choice the user does not connect to it."""

    @pytest.fixture(autouse=True)
    def bundled(self, tmp_path_factory, monkeypatch):
        source = tmp_path_factory.mktemp("bundled2") / "dinput8.dll"
        source.write_bytes(TAP_BYTES)
        monkeypatch.setattr(tap_install, 'bundled_wrapper', lambda: str(source))

    def test_an_existing_config_is_copied_to_the_other_target(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths)
        configured = tmp_path / "dcs" / "bin-mt" / "dinput8.ini"
        configured.write_text("[FFBDevices]\nMonster=tap\n")

        tap_install.install(sim_status(dcs, root))

        other = tmp_path / "dcs" / "bin" / "dinput8.ini"
        assert other.is_file()
        assert other.read_text() == configured.read_text()

    def test_an_existing_config_is_never_overwritten(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths)
        (tmp_path / "dcs" / "bin-mt" / "dinput8.ini").write_text("first\n")
        (tmp_path / "dcs" / "bin" / "dinput8.ini").write_text("second\n")

        tap_install.install(sim_status(dcs, root))
        assert (tmp_path / "dcs" / "bin" / "dinput8.ini").read_text() == "second\n"

    def test_no_config_is_invented(self, tmp_path):
        """The wrapper has built-in defaults; with no file anywhere they
        apply to every target equally."""
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths)
        tap_install.install(sim_status(dcs, root))
        assert not (tmp_path / "dcs" / "bin" / "dinput8.ini").exists()
        assert not (tmp_path / "dcs" / "bin-mt" / "dinput8.ini").exists()


class TestRemove:
    def test_our_wrapper_goes(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", dcs.exe_relpaths, wrapper=TAP_BYTES)
        outcomes = tap_install.remove(sim_status(dcs, root))

        assert all(o.ok for o in outcomes)
        assert {o.action for o in outcomes} == {"removed"}
        assert not sim_status(dcs, root).installed

    def test_the_config_is_left_alone(self, tmp_path):
        """It may predate this installation - written for the upstream
        wrapper - and removing a file we did not create is not ours to do."""
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",),
                         wrapper=TAP_BYTES, config=True)
        tap_install.remove(sim_status(dcs, root))

        assert not (tmp_path / "dcs" / "bin-mt" / "dinput8.dll").exists()
        assert (tmp_path / "dcs" / "bin-mt" / "dinput8.ini").is_file()

    def test_a_foreign_dll_is_not_removed(self, tmp_path):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",),
                         wrapper=FOREIGN_BYTES)
        outcome = tap_install.remove(sim_status(dcs, root))[0]
        assert not outcome.ok
        assert "not ours" in outcome.detail
        assert (tmp_path / "dcs" / "bin-mt" / "dinput8.dll").exists()


    def test_a_locked_file_says_to_close_the_game(self, tmp_path, monkeypatch):
        dcs = SIMS_BY_KEY['DCS']
        root = make_tree(tmp_path / "dcs", ("bin-mt/DCS.exe",), wrapper=TAP_BYTES)

        def locked(path):
            raise PermissionError(13, "in use")
        monkeypatch.setattr(tap_install.os, 'remove', locked)

        outcome = tap_install.remove(sim_status(dcs, root))[0]
        assert not outcome.ok
        assert "close the game" in outcome.detail


class TestUpdateMechanics:
    """The update path, validated end to end: an installed wrapper is
    byte-replaced by the bundled one, its config file is preserved, and
    the outdated flag clears once the copy lands."""

    def _sim(self, tmp_path, dll_bytes=TAP_BYTES):
        game = tmp_path / "DCS World" / "bin"
        game.mkdir(parents=True)
        (game / WRAPPER_NAME).write_bytes(dll_bytes)
        (game / WRAPPER_CONFIG).write_text(
            "[FFBDevices]\nFFFF:2054=tap\n", encoding="utf-8")
        status = SimStatus(
            sim=SIMS_BY_KEY['DCS'], root=str(tmp_path / "DCS World"),
            provenance="test",
            targets=[TargetStatus(directory=str(game),
                                  state=wrapper_state(str(game)),
                                  has_config=True)])
        return game, status

    def test_update_replaces_bytes_and_keeps_the_config(
            self, tmp_path, monkeypatch):
        game, status = self._sim(tmp_path, TAP_BYTES + b" STALE")
        bundled = tmp_path / "bundled.dll"
        bundled.write_bytes(TAP_BYTES + b" FRESH")
        monkeypatch.setattr('telemffb.tap_install.bundled_wrapper',
                            lambda: str(bundled))
        outcomes = install(status)
        assert [(o.ok, o.action) for o in outcomes] == [(True, 'updated')]
        assert (game / WRAPPER_NAME).read_bytes() == TAP_BYTES + b" FRESH"
        assert "FFFF:2054=tap" in (game / WRAPPER_CONFIG).read_text(
            encoding="utf-8")

    def test_outdated_flag_raises_and_clears(self, tmp_path, monkeypatch):
        game, status = self._sim(tmp_path)
        monkeypatch.setattr('telemffb.tap_install.bundled_version',
                            lambda: '0.9.1.0')
        # installed build has no readable version -> superseded
        assert outdated_targets(status)
        # after an update the installed version matches the bundled one
        monkeypatch.setattr('telemffb.tap_install.file_version',
                            lambda path: '0.9.1.0')
        refreshed = SimStatus(
            sim=status.sim, root=status.root, provenance="test",
            targets=[TargetStatus(directory=str(game),
                                  state=WrapperState.TAP,
                                  version='0.9.1.0')])
        assert not outdated_targets(refreshed)

    def test_a_newer_installed_build_is_not_outdated(self, tmp_path):
        _, status = self._sim(tmp_path)
        status.targets[0].version = '9.9.9.9'
        assert not outdated_targets(status, bundled='0.9.1.0')

    def test_foreign_wrappers_are_never_outdated(self, tmp_path):
        game, status = self._sim(tmp_path, b"MZ someone else's proxy")
        assert status.targets[0].state == WrapperState.FOREIGN
        assert not outdated_targets(status, bundled='9.9.9.9')


class TestSharedLogFolder:
    """TelemFFB points every sim's wrapper log at one folder - the LogDir
    it writes into configs - so support means searching one place.  The
    wrapper's own defaults stay standalone-friendly (beside itself, then
    the user's home folder); TelemFFB never relies on them."""

    OURS = ("; dinput8.ini - written by TelemFFB on 2026-08-01.\r\n"
            "[General]\r\n"
            "RequireTelemFFB=true\r\n"
            "\r\n"
            "[FFBDevices]\r\n"
            "FFFF:2054=tap\r\n")

    def _sim(self, tmp_path, config, monkeypatch):
        game = tmp_path / "DCS World" / "bin"
        game.mkdir(parents=True)
        (game / WRAPPER_NAME).write_bytes(TAP_BYTES)
        (game / WRAPPER_CONFIG).write_bytes(config.encode())
        bundled = tmp_path / "bundled.dll"
        bundled.write_bytes(TAP_BYTES)
        monkeypatch.setattr('telemffb.tap_install.bundled_wrapper',
                            lambda: str(bundled))
        status = SimStatus(
            sim=SIMS_BY_KEY['DCS'], root=str(tmp_path / "DCS World"),
            provenance="test",
            targets=[TargetStatus(directory=str(game),
                                  state=wrapper_state(str(game)),
                                  has_config=True)])
        return game, status

    def test_generated_configs_carry_the_shared_folder(self):
        assert "LogDir=" + tap_log_dir() in generate_config([])

    def test_an_update_backfills_our_old_config(self, tmp_path, monkeypatch):
        game, status = self._sim(tmp_path, self.OURS, monkeypatch)
        install(status)
        text = (game / WRAPPER_CONFIG).read_bytes().decode()
        head, _, tail = text.partition("[FFBDevices]")
        assert "LogDir=" + tap_log_dir() in head
        assert "FFFF:2054=tap" in tail
        assert "\r\nLogDir=" in text          # the CRLF shape survives

    def test_the_backfill_happens_once(self, tmp_path, monkeypatch):
        game, status = self._sim(tmp_path, self.OURS, monkeypatch)
        install(status)
        install(status)
        assert (game / WRAPPER_CONFIG).read_bytes().decode().count(
            "LogDir=") == 1

    def test_a_log_decision_already_made_is_kept(self, tmp_path, monkeypatch):
        """Including LogDir= left empty - that is how to decline the
        shared folder without an update putting it back."""
        decisions = ("LogDir=D:\\my own logs\r\n", "LogDir=\r\n")
        for n, decision in enumerate(decisions):
            config = self.OURS.replace("[General]\r\n",
                                       "[General]\r\n" + decision)
            game, status = self._sim(tmp_path / f"case{n}", config,
                                     monkeypatch)
            install(status)
            assert (game / WRAPPER_CONFIG).read_bytes().decode() == config

    def test_a_config_that_is_not_ours_is_left_alone(self, tmp_path,
                                                     monkeypatch):
        config = "[General]\r\nLogLevel=3\r\n[FFBDevices]\r\nPedals=block\r\n"
        game, status = self._sim(tmp_path, config, monkeypatch)
        install(status)
        assert (game / WRAPPER_CONFIG).read_bytes().decode() == config


class TestWriteRefusals:
    """Two different problems arrive as the same PermissionError: the
    game holding the file open (sharing violation) and the folder's ACL
    refusing writes (a game under Program Files).  Telling a user with
    an elevation problem to close a game that is not running sends them
    in circles - the winerror says which it was."""

    def _sim(self, tmp_path, monkeypatch):
        game = tmp_path / "DCS World" / "bin"
        game.mkdir(parents=True)
        (game / WRAPPER_NAME).write_bytes(TAP_BYTES)
        bundled = tmp_path / "bundled.dll"
        bundled.write_bytes(TAP_BYTES)
        monkeypatch.setattr('telemffb.tap_install.bundled_wrapper',
                            lambda: str(bundled))
        return SimStatus(
            sim=SIMS_BY_KEY['DCS'], root=str(tmp_path / "DCS World"),
            provenance="test",
            targets=[TargetStatus(directory=str(game),
                                  state=wrapper_state(str(game)),
                                  has_config=False)])

    def _refuse(self, monkeypatch, winerror):
        import shutil
        def denied(*args, **kwargs):
            raise PermissionError(13, "refused", None, winerror)
        monkeypatch.setattr(shutil, 'copyfile', denied)

    def test_access_denied_names_the_folder_not_the_game(
            self, tmp_path, monkeypatch):
        status = self._sim(tmp_path, monkeypatch)
        self._refuse(monkeypatch, winerror=5)
        outcome, = install(status)
        assert not outcome.ok
        assert "administrator" in outcome.detail
        assert "Program Files" in outcome.detail
        assert "close the game" not in outcome.detail

    def test_a_sharing_violation_still_says_close_the_game(
            self, tmp_path, monkeypatch):
        status = self._sim(tmp_path, monkeypatch)
        self._refuse(monkeypatch, winerror=32)
        outcome, = install(status)
        assert not outcome.ok
        assert "close the game" in outcome.detail

    def test_removal_distinguishes_the_same_two_cases(
            self, tmp_path, monkeypatch):
        from telemffb.tap_install import remove
        status = self._sim(tmp_path, monkeypatch)
        def denied(path):
            raise PermissionError(13, "refused", None, 5)
        monkeypatch.setattr(os, 'remove', denied)
        outcome, = remove(status)
        assert not outcome.ok
        assert "administrator" in outcome.detail
