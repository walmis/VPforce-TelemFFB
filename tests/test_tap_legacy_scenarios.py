"""Arriving at a game folder that walmis's ffb-fix already lives in.

This is the common case, not an edge.  Most VPforce owners put the legacy
wrapper beside DCS to keep the game's force feedback off their pedals and
collective, using the sample ini's name-keyed block rules, and usually in
only one of DCS's two executable directories.  TelemFFB's wrapper is built
from that one and reads that file as it stands, so the whole job here is to
not break a setup that already works - and to say so.

Real files in a temporary tree rather than stubs: the thing under test is
what ends up on disk.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.TapStatusPanel as panel_module
from telemffb import tap_install, tap_reconcile
from telemffb.tap_config import BOM, already_blocked, amend, read
from telemffb.tap_install import (SIMS_BY_KEY, TapDevice, WrapperState,
                                  read_configs, sim_status, write_one_config)
from telemffb.TapStatusPanel import TapStatusPanel

pytestmark = [pytest.mark.unit]

DCS = SIMS_BY_KEY["DCS"]

#: Our wrapper carries the tap markers.  The legacy ffb-fix wrapper
#: carries its own log strings, which we recognize (WrapperState.LEGACY)
#: so the install prompt can be an affirmative upgrade instead of asking
#: the user to classify a DLL they installed a year ago.
OUR_DLL = b"MZ\x00 FFB tap: device [%ls] bound to block %d \x00"
LEGACY_DLL = (b"MZ\x00 CreateDevice: [%ls]  FFB=%s  scale=%d%% \x00"
              b" dinput8.ini [FFBDevices] DeviceNameSubstring=action \x00")

#: The upstream sample as shipped, plus the two rules every VPforce owner
#: added.  CRLF, as a Windows editor writes it.
LEGACY_INI = "\r\n".join([
    "[General]",
    "; Enable the wrapper (false = pure pass-through, no interception)",
    "Enabled=true",
    "",
    "; Log level: 0=none, 1=error, 2=warn, 3=info, 4=debug",
    "LogLevel=3",
    "",
    "[FFB]",
    "; Global FFB enable/disable (false = block FFB on ALL devices)",
    "Enabled=true",
    "LogEffects=true",
    "DefaultScale=100",
    "",
    "[FFBDevices]",
    "; Matching is case-insensitive substring against the device product name.",
    "; First matching rule wins.",
    "vJoy=block",
    "Pedals=block",
    "Collective=block",
    "; Example: scale a specific stick to 50% force",
    "; MSFFB 2=50",
    "",
])

RHINO = TapDevice("joystick", 0xFFFF, 0x2054, "VPforce Rhino")
PEDALS = TapDevice("pedals", 0xFFFF, 0x2052, "VPforce Pedals")
COLLECTIVE = TapDevice("collective", 0xFFFF, 0x2051, "VPforce Collective")
MOZA = TapDevice("joystick", 0x346E, 0x0005, "MOZA AB9", directinput=True)

#: What a VPforce owner answers in the device dialog: tap the stick, put it
#: first, block what DCS will not drive.
VPFORCE_ANSWER = ([RHINO], [], [RHINO], [PEDALS, COLLECTIVE])


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def nothing_from_this_machine(monkeypatch, tmp_path):
    monkeypatch.setattr(tap_install, "steam_common_dirs", lambda: [])
    monkeypatch.setattr(tap_install, "dcs_registry_roots", lambda: [])
    monkeypatch.setattr(tap_install, "bms_registry_roots", lambda: [])
    bundled = tmp_path / "bundled" / "dinput8.dll"
    bundled.parent.mkdir()
    bundled.write_bytes(OUR_DLL)
    monkeypatch.setattr(tap_install, "bundled_wrapper", lambda: str(bundled))


def dcs_root(tmp_path, legacy_in=("bin-mt",), ini=LEGACY_INI):
    """A DCS install with the legacy wrapper beside the named executables."""
    root = tmp_path / "DCS World"
    for sub in ("bin", "bin-mt"):
        (root / sub).mkdir(parents=True)
        (root / sub / "DCS.exe").write_bytes(b"exe")
    for sub in legacy_in:
        (root / sub / "dinput8.dll").write_bytes(LEGACY_DLL)
        if ini is not None:
            (root / sub / "dinput8.ini").write_bytes(
                ini.encode("utf-8") if isinstance(ini, str) else ini)
    return str(root)


def status_of(root):
    return sim_status(DCS, root)


def ini_bytes(root, sub):
    with open(os.path.join(root, sub, "dinput8.ini"), "rb") as handle:
        return handle.read()


def dll_bytes(root, sub):
    with open(os.path.join(root, sub, "dinput8.dll"), "rb") as handle:
        return handle.read()


def panel_for(monkeypatch, root, devices=(), overwrite=True, answer=None):
    """A status panel over a real tree, with both questions pre-answered.

    ``answer`` is what the device dialog returns - the fresh-install ask,
    or the adoption dialog that opens itself after an ffb-fix upgrade.
    None is the user cancelling it.
    """
    monkeypatch.setattr(panel_module, "confirm_overwrite",
                        lambda *a, **k: overwrite)
    monkeypatch.setattr(panel_module, "confirm_legacy_upgrade",
                        lambda *a, **k: overwrite)
    monkeypatch.setattr(panel_module, "ask_for_devices",
                        lambda *a, **k: answer)
    return TapStatusPanel(status_of(root), devices=lambda: list(devices))


class TestWhatWeFind:
    def test_the_legacy_wrapper_is_recognized_and_the_install_is_partial(
            self, tmp_path):
        """Upstream's README says bin\\; MT users chose bin-mt.  Either way
        one of DCS's two directories has it and the other does not - and
        the one that does is identified as ffb-fix, not an unknown DLL."""
        status = status_of(dcs_root(tmp_path))
        by_name = {os.path.basename(t.directory): t for t in status.targets}
        assert by_name["bin"].state == WrapperState.ABSENT
        assert by_name["bin-mt"].state == WrapperState.LEGACY
        assert by_name["bin-mt"].has_config
        assert not status.installed and not status.partially_installed

    def test_their_rules_already_cover_their_devices(self, tmp_path):
        """'Pedals' is a substring of 'VPforce Pedals', so the name rule is
        recognized as the block it is - nothing to add, and unticking it in
        the dialog is what would retire it."""
        _, text = read_configs(status_of(dcs_root(tmp_path)))[0]
        assert already_blocked(text, (PEDALS.vid, PEDALS.pid), PEDALS.ident)
        assert already_blocked(text, (COLLECTIVE.vid, COLLECTIVE.pid),
                               COLLECTIVE.ident)
        assert already_blocked(text, None, "vJoy")


class TestInstallingOverIt:
    def test_the_user_is_asked_and_the_ini_is_kept(self, app, tmp_path,
                                                   monkeypatch):
        """Replacing the DLL is an upgrade; the file beside it is theirs and
        goes on working.  The question asked is the affirmative upgrade one
        - the wrapper identified itself - and the adoption dialog opens by
        itself afterwards, so the additions are offered without hunting for
        Configure Devices.  Cancelling it leaves the file exactly as it
        was: the user has the final say over every write."""
        root = dcs_root(tmp_path)
        asked, chose, classify = [], [], []
        monkeypatch.setattr(panel_module, "confirm_legacy_upgrade",
                            lambda *a, **k: asked.append(a) or True)
        monkeypatch.setattr(panel_module, "confirm_overwrite",
                            lambda *a, **k: classify.append(a) or True)
        monkeypatch.setattr(panel_module, "ask_for_devices",
                            lambda *a, **k: chose.append(a) or None)
        TapStatusPanel(status_of(root))._install()

        assert asked and not classify
        assert len(chose) == 1, "the adoption dialog opens itself, once"
        assert dll_bytes(root, "bin") == dll_bytes(root, "bin-mt") == OUR_DLL
        assert ini_bytes(root, "bin-mt") == LEGACY_INI.encode("utf-8")

    def test_the_ini_is_carried_to_the_directory_that_had_none(
            self, app, tmp_path, monkeypatch):
        """DCS launches whichever executable the user picked, and reads the
        config beside that one - so a config beside only one of them makes
        the tap depend on a choice the user does not associate with it."""
        root = dcs_root(tmp_path)
        panel_for(monkeypatch, root)._install()
        assert ini_bytes(root, "bin") == ini_bytes(root, "bin-mt")

    def test_declining_leaves_everything_exactly_as_it_was(
            self, app, tmp_path, monkeypatch):
        root = dcs_root(tmp_path)
        panel_for(monkeypatch, root, overwrite=False)._install()
        assert dll_bytes(root, "bin-mt") == LEGACY_DLL
        assert not os.path.exists(os.path.join(root, "bin", "dinput8.dll"))


class TestAdoptingTheirFile:
    """Configure Devices on a file we did not write adds exactly what is
    missing, and nothing it already says is rewritten."""

    def configured(self, monkeypatch, tmp_path):
        root = dcs_root(tmp_path)
        panel = panel_for(monkeypatch, root, devices=[RHINO, PEDALS, COLLECTIVE],
                          answer=VPFORCE_ANSWER)
        # the adoption dialog opens itself after the upgrade
        panel._install()
        return ini_bytes(root, "bin-mt").decode("utf-8")

    def test_the_stick_is_tapped_and_put_first(self, app, tmp_path, monkeypatch):
        facts = read(self.configured(monkeypatch, tmp_path))
        assert [r.key for r in facts.rules if r.is_tap] == ["FFFF:2054"]
        assert [e.match for e in facts.order] == ["FFFF:2054"]
        assert facts.require_telemffb and facts.require_line is not None

    def test_their_blocks_are_not_duplicated(self, app, tmp_path, monkeypatch):
        """Pedals, Collective and vJoy are already blocked by name.  A second
        rule by id would be dead weight and would make the file look like
        something we had taken over."""
        text = self.configured(monkeypatch, tmp_path)
        assert "FFFF:2052=block" not in text
        assert "FFFF:2051=block" not in text
        assert text.count("vJoy=block") == 1

    def test_everything_else_survives_byte_for_byte(self, app, tmp_path,
                                                    monkeypatch):
        """Their comments, their [FFB] section, their line endings.  The
        only lines that change are the ones the dialog asked about."""
        text = self.configured(monkeypatch, tmp_path)
        lines = text.split("\r\n")
        for theirs in LEGACY_INI.split("\r\n"):
            assert theirs in lines, theirs
        assert "\r\r\n" not in text
        assert text.count("\n") == text.count("\r\n")


class TestADeviceOnlyOurWrapperCanDrive:
    """A Moza owner with VPforce pedals: a DirectInput stick beside the
    legacy wrapper.  The stick needs a tap rule, but not in a file the
    legacy DLL is the one reading - that DLL does nothing with it."""

    SETTINGS = {"enableDCS": True, "enableTapDCS": True}

    def test_no_rule_is_offered_while_the_legacy_dll_is_running(self, tmp_path):
        status = status_of(dcs_root(tmp_path))
        gap, = tap_reconcile.missing_tap_rules([MOZA], self.SETTINGS, [status])
        assert not gap.fixable

    def test_once_ours_is_installed_the_same_gap_can_be_fixed(
            self, app, tmp_path, monkeypatch):
        root = dcs_root(tmp_path)
        panel_for(monkeypatch, root)._install()
        gap, = tap_reconcile.missing_tap_rules([MOZA], self.SETTINGS,
                                               [status_of(root)])
        assert gap.fixable
        tap_reconcile.apply_tap_rules([gap])
        sub = os.path.basename(gap.directory)
        assert b"346E:0005=tap" in ini_bytes(root, sub)


class TestFilesAsPeopleActuallySaveThem:
    def test_an_ansi_file_with_an_accent_does_not_crash_and_is_kept(
            self, tmp_path):
        """Notepad's ANSI, a French comment.  Not UTF-8, so the obvious read
        raised straight through the settings dialog; now the byte rides
        through untouched and comes back out where it was."""
        accented = LEGACY_INI.replace("; First matching rule wins.",
                                      "; Pédales - bloquées").encode("cp1252")
        root = dcs_root(tmp_path, ini=accented)
        (directory, text), = read_configs(status_of(root))
        write_one_config(directory, amend(text, ["FFFF:2054=tap"]))
        after = ini_bytes(root, "bin-mt")
        assert b"; P\xe9dales - bloqu\xe9es" in after
        assert b"FFFF:2054=tap" in after
        for theirs in accented.split(b"\r\n"):
            assert theirs in after.split(b"\r\n"), theirs

    def test_a_byte_order_mark_is_kept_where_it_was(self, tmp_path):
        root = dcs_root(tmp_path, ini=(BOM + LEGACY_INI).encode("utf-8"))
        (directory, text), = read_configs(status_of(root))
        write_one_config(directory, amend(text, ["FFFF:2054=tap"]))
        after = ini_bytes(root, "bin-mt")
        assert after.startswith(b"\xef\xbb\xbf[General]")
        assert after.count(b"\xef\xbb\xbf") == 1

    def test_a_generated_file_has_windows_line_endings_once(
            self, app, tmp_path, monkeypatch):
        """Not twice: writing an already-CRLF text through newline
        translation doubled every carriage return, and reading that back
        split each line in two."""
        root = dcs_root(tmp_path, legacy_in=())
        panel_for(monkeypatch, root, devices=[RHINO, PEDALS],
                  answer=([RHINO], [], [RHINO], [PEDALS]))._install()
        raw = ini_bytes(root, "bin")
        assert b"\r\n" in raw and b"\r\r\n" not in raw
        assert b"vJoy=block" in raw


class TestTakingTheTapBackOut:
    def test_only_our_lines_go_and_their_blocks_stay(self, app, tmp_path,
                                                     monkeypatch):
        root = dcs_root(tmp_path)
        panel = panel_for(monkeypatch, root, devices=[RHINO, PEDALS, COLLECTIVE],
                          answer=VPFORCE_ANSWER)
        # the adoption dialog opens itself after the upgrade
        panel._install()

        plan = tap_reconcile.plan_tap_cleanup(status_of(root))
        assert plan.delete_config == []          # not ours to delete
        assert any("block or scale rules" in r for r in plan.still_acts)
        tap_reconcile.apply_tap_cleanup([plan])

        text = ini_bytes(root, "bin-mt").decode("utf-8")
        facts = read(text)
        assert not any(r.is_tap for r in facts.rules)
        assert facts.order == []
        assert {r.key for r in facts.rules if r.value == "block"} >= \
            {"vJoy", "Pedals", "Collective"}
        assert "LogEffects=true" in text
        for sub in ("bin", "bin-mt"):
            assert not os.path.exists(os.path.join(root, sub, "dinput8.dll"))


class TestOrderingBesideAnExistingSection:
    """[DeviceOrder] is TelemFFB's own concept - no hand-written config
    predates it - so an existing entry is never a user's answer to
    defer to, only an earlier device selection to supersede.  The
    section is rewritten wholesale to its one policy entry: the
    joystick device at position 1.  Deference was tried first, and it
    left a real config with a stale device at rank 1 - which hands the
    game's forces to a blocked device that renders nothing."""

    MONSTER = TapDevice("joystick", 0xFFFF, 0x2054, "Monster")

    def test_the_section_is_rewritten_to_the_joystick_at_one(
            self, app, tmp_path, monkeypatch):
        ini = LEGACY_INI + "\r\n".join([
            "[DeviceOrder]",
            "1=Rhino FFB Joystick",
            "",
        ])
        root = dcs_root(tmp_path, ini=ini)
        answer = ([self.MONSTER], [], [self.MONSTER], [PEDALS, COLLECTIVE])
        panel = panel_for(monkeypatch, root,
                          devices=[self.MONSTER, PEDALS, COLLECTIVE],
                          answer=answer)
        panel._install()

        facts = read(ini_bytes(root, "bin-mt").decode("utf-8"))
        entries = [(e.position, e.match) for e in facts.order]
        assert entries == [("1", "FFFF:2054")], \
            "one entry: the joystick, first"
