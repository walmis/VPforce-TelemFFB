"""What the DirectInput Tap panel tells the user.

The states worth telling apart are not "installed" and "not installed".  A
partial install misleads - DCS ships two executables, and a wrapper beside
only one of them does nothing at all if the game launches the other.  A
foreign dinput8.dll matters too: it belongs to something the user installed
deliberately, and reporting "not installed" would invite them to overwrite it.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.tap_install import (SIMS_BY_KEY, SimStatus, TapDevice,
                                  TargetStatus, WrapperState)
from telemffb.TapStatusPanel import TapStatusPanel

pytestmark = [pytest.mark.unit]

DCS = SIMS_BY_KEY['DCS']

#: A configured rig: the stick DCS should drive, plus two devices it
#: renders nothing to.
DEVICES = [TapDevice("joystick", 0xFFFF, 0x2054, "Monster"),
           TapDevice("pedals", 0xFFFF, 0x2052, "Pedals"),
           TapDevice("collective", 0xFFFF, 0x2051, "Collective")]
#: A game root that cannot be a real install.  The panel stats and reads
#: config files under whatever root it is given, so a plausible-looking
#: path here would read - and the install paths would write - the machine
#: running the tests.  Guarded below rather than trusted.
ROOT = r"C:\pytest-tap-fixture\DCS World"


@pytest.fixture(autouse=True)
def nothing_real_is_reachable():
    """No test here may touch a game that exists on this machine.

    These tests build a SimStatus by hand and hand it to the panel, which
    stats and reads the config beside each target - and the install paths
    write one.  Pointed at a real install that reads the user's own
    configuration and writes test fixtures over it, which is exactly what
    happened once.  A build machine has no games installed, so this only
    ever fires on a developer's box.
    """
    for root in (ROOT, os.path.dirname(ROOT)):
        assert not os.path.exists(root), (
            f"{root} exists on this machine - the tap tests would read and "
            f"write a real install. Point ROOT at something synthetic.")


@pytest.fixture(autouse=True)
def bundled(monkeypatch):
    """Pin the version TelemFFB ships.  Version rows compare against it,
    and reading the real bundled DLL would make these tests change
    meaning every time the wrapper is rebuilt."""
    from telemffb import tap_install
    monkeypatch.setattr(tap_install, 'bundled_version', lambda: "0.9.0.0")
    return "0.9.0.0"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def status(*targets, root=ROOT, provenance="registry", sim=DCS):
    return SimStatus(sim=sim, root=root, provenance=provenance,
                     targets=list(targets))


def target(name, state, version=None, has_config=False):
    return TargetStatus(directory=os.path.join(ROOT, name), state=state,
                        version=version, has_config=has_config)


def ok(directory):
    """What write_one_config really returns; _run reads .ok off it."""
    from telemffb.tap_install import TargetOutcome
    return TargetOutcome(directory, True, "configured")


def rendered(panel):
    """Every piece of text the panel is showing.

    Searched rather than walked one layout: the header, the path and the
    target rows live in separate layouts so that the sim name cannot set
    the column widths the target rows use.
    """
    # an empty or hidden label shows the user nothing, so it is not part
    # of what the panel renders
    return [label.text() for label in panel.findChildren(QtWidgets.QLabel)
            if label.text() and not label.isHidden()]


class TestHeadline:
    @staticmethod
    def attention_texts(panel):
        """Labels drawn in the attention color - and actually showing
        something: an empty or hidden one flags nothing."""
        amber = panel._color("attention")
        return [w.text() for w in panel.findChildren(QtWidgets.QLabel)
                if amber in w.styleSheet() and w.text() and not w.isHidden()]

    def test_fully_installed(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP),
                                      target("bin-mt", WrapperState.TAP)))
        assert "installed" in rendered(panel)

    def test_a_partial_install_shows_both_halves(self, app):
        """The whole reason the panel exists: the game may launch the
        executable that was missed, and one word for the pair would hide
        which."""
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT),
                                      target("bin-mt", WrapperState.TAP)))
        text = " ".join(rendered(panel))
        assert "not installed" in text and "installed" in text

    def test_the_missing_half_of_a_partial_install_is_flagged(self, app):
        """It has to stand out from an install that is simply absent, which
        is not a problem at all."""
        panel = TapStatusPanel(status(
            target("bin", WrapperState.ABSENT),
            # current, so the missing half is the only thing flagged
            target("bin-mt", WrapperState.TAP, version="0.9.0.0")))
        assert self.attention_texts(panel) == ["not installed"]


    def test_a_foreign_dll_is_called_out_not_reported_as_missing(self, app):
        """Named generically: we do not know what it is, and guessing in the
        UI would be as wrong as guessing in the code."""
        panel = TapStatusPanel(status(target("bin", WrapperState.FOREIGN)))
        text = " ".join(rendered(panel))
        assert "another dinput8.dll installed" in text
        assert "not installed" not in text


    def test_nothing_installed(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT),
                                      target("bin-mt", WrapperState.ABSENT)))
        assert "not installed" in rendered(panel)

    def test_an_undetected_sim_explains_what_to_do(self, app):
        panel = TapStatusPanel(SimStatus(sim=DCS, root=None,
                                         provenance="not found"))
        text = " ".join(rendered(panel))
        assert "not detected" in text
        assert "path" in text.lower(), "the user is told how to fix it"


class TestDetail:
    def test_the_resolved_path_and_its_provenance_are_shown(self, app):
        """A user whose sim was found somewhere unexpected needs to see
        which install is meant."""
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP)))
        text = " ".join(rendered(panel))
        assert ROOT in text
        assert "registry" in text

    def test_versions_are_shown_per_target(self, app):
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, version="0.9.0.0"),
            target("bin-mt", WrapperState.TAP, version="0.9.0.0")))
        assert len([t for t in rendered(panel) if "v0.9.0.0" in t]) == 2

    def test_a_current_wrapper_says_so(self, app):
        """Reinstalling the same build looked identical to a failed
        update until the row said which build TelemFFB ships."""
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, version="0.9.0.0")))
        assert any("(current)" in t for t in rendered(panel))

    def test_an_outdated_wrapper_offers_the_shipped_build(self, app):
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, version="0.8.0.0")))
        text = " ".join(rendered(panel))
        assert "v0.8.0.0" in text and "v0.9.0.0 available" in text

    def test_an_unversioned_wrapper_is_offered_the_shipped_build(self, app):
        """A build with no version resource predates every build that has
        one, so it is exactly what an update is for."""
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP)))
        assert any("v0.9.0.0 available" in t for t in rendered(panel))

    def test_a_newer_wrapper_is_left_alone(self, app):
        """A user testing a newer wrapper than TelemFFB ships must not
        be told to downgrade."""
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, version="1.0.0.0")))
        assert not any("available" in t for t in rendered(panel))

    def test_a_wrapper_without_a_version_says_so(self, app):
        """Builds predating the version resource are still ours; they just
        cannot say which build they are."""
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP)))
        assert any("version unknown" in t for t in rendered(panel))

    def test_no_version_is_shown_for_a_target_without_our_wrapper(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.FOREIGN,
                                             version="9.9.9")))
        assert not [t for t in rendered(panel) if t.startswith("v")]

    def test_targets_are_named_relative_to_the_root(self, app):
        """IL-2's target basename is "game", which names nothing alone."""
        il2 = SIMS_BY_KEY['IL2']
        root = r"C:\pytest-tap-fixture\IL-2 Sturmovik Great Battles"
        panel = TapStatusPanel(SimStatus(
            sim=il2, root=root, provenance="configured in TelemFFB",
            targets=[TargetStatus(directory=os.path.join(root, "bin", "game"),
                                  state=WrapperState.TAP, version="0.9.0.0")]))
        assert os.path.join("bin", "game") in rendered(panel)


class TestRedraw:
    def test_setting_a_new_status_replaces_the_old_one(self, app):
        """The panel is refreshed whenever the dialog is shown, so stale
        state must not accumulate."""
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)))
        assert "not installed" in rendered(panel)

        panel.set_status(status(target("bin", WrapperState.TAP,
                                       version="0.9.0.0")))
        text = rendered(panel)
        assert "not installed" not in text
        assert any("v0.9.0.0" in t for t in text)

    def test_redrawing_does_not_accumulate_widgets(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP)))
        before = len(rendered(panel))
        for _ in range(5):
            panel.set_status(status(target("bin", WrapperState.TAP)))
        assert len(rendered(panel)) == before,             "a replaced body is still attached to the panel"


class TestButtons:
    """Install and Remove are offered only where they would do something."""

    def buttons(self, panel):
        return {b.text(): b for b in panel.findChildren(QtWidgets.QPushButton)}

    def test_nothing_installed_offers_install_only(self, app):
        """One Install button whatever the mode - which mode it lays
        down is the FFB-Fix toggle's job, not a second button's."""
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)))
        assert set(self.buttons(panel)) == {"Install"}

    def test_a_partial_install_offers_to_complete_it(self, app):
        """"Install" would read as though nothing were there."""
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT),
                                      target("bin-mt", WrapperState.TAP)))
        assert "Complete Install" in self.buttons(panel)
        assert "Remove" in self.buttons(panel)

    def test_a_full_install_offers_reinstall_and_remove(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP),
                                      target("bin-mt", WrapperState.TAP)))
        assert set(self.buttons(panel)) == {
            "Reinstall", "Configure Devices...", "Remove"}

    def test_reinstall_goes_flat_when_the_installed_build_is_current(
            self, app, bundled):
        """Copying identical bytes over identical bytes is a no-op the
        user cannot tell apart from a real update."""
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, version=bundled),
            target("bin-mt", WrapperState.TAP, version=bundled)))
        assert not self.buttons(panel)["Reinstall"].isEnabled()

    def test_reinstall_is_live_when_a_newer_build_is_bundled(self, app):
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, version="0.8.0.0"),
            target("bin-mt", WrapperState.TAP, version="0.8.0.0")))
        assert self.buttons(panel)["Reinstall"].isEnabled()

    def test_one_stale_half_is_enough_to_offer_it(self, app, bundled):
        """Both halves are written in one go, so either being behind
        leaves the button something to do."""
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, version=bundled),
            target("bin-mt", WrapperState.TAP, version="0.8.0.0")))
        assert self.buttons(panel)["Reinstall"].isEnabled()

    def test_a_build_too_old_to_name_itself_can_still_be_replaced(self, app):
        """Wrappers built before the version resource report nothing, and
        that is precisely the case worth replacing - so an unknown version
        must not read as "current"."""
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP),
                                      target("bin-mt", WrapperState.TAP)))
        assert self.buttons(panel)["Reinstall"].isEnabled()

    def test_a_foreign_dll_can_be_installed_over_but_not_removed(self, app):
        """Replacing it is the user's call, so Install is offered - but
        Remove would delete a file we did not put there."""
        panel = TapStatusPanel(status(target("bin", WrapperState.FOREIGN)))
        assert set(self.buttons(panel)) == {"Install"}

    def test_an_undetected_sim_offers_nothing(self, app):
        panel = TapStatusPanel(SimStatus(sim=DCS, root=None,
                                         provenance="not found"))
        assert self.buttons(panel) == {}

    def test_acting_asks_the_dialog_to_rescan(self, app, monkeypatch):
        """The panel reports what it did; the truth comes from re-reading
        the folders."""
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)))
        monkeypatch.setattr(module, 'ask_for_devices', lambda *a, **k: ([], [], [], []))
        monkeypatch.setattr(module, 'install', lambda s, config=None, overwrite_foreign=False: [])
        seen = []
        panel.changed.connect(lambda: seen.append(True))
        self.buttons(panel)["Install"].click()
        assert seen == [True]

    def test_a_partial_failure_names_what_did_work(self, app, monkeypatch):
        """With two targets one can succeed while a running game locks the
        other; "failed" alone would hide that half the job is done."""
        import telemffb.TapStatusPanel as module
        from telemffb.tap_install import TargetOutcome
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT),
                                      target("bin-mt", WrapperState.ABSENT)))
        monkeypatch.setattr(module, 'ask_for_devices', lambda *a, **k: ([], [], [], []))
        monkeypatch.setattr(module, 'install', lambda s, config=None, overwrite_foreign=False: [
            TargetOutcome(os.path.join(ROOT, "bin"), True, "installed"),
            TargetOutcome(os.path.join(ROOT, "bin-mt"), False, "failed",
                          "the file is in use - close the game and try again"),
        ])
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: shown.append(a[2])))
        self.buttons(panel)["Install"].click()

        assert shown, "the user was told nothing"
        assert "in use" in shown[0]
        assert "Completed for: bin" in shown[0]

    def test_success_says_nothing(self, app, monkeypatch):
        """The panel redraws to show the new state; a dialog on top of that
        is noise."""
        import telemffb.TapStatusPanel as module
        from telemffb.tap_install import TargetOutcome
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)))
        monkeypatch.setattr(module, 'ask_for_devices', lambda *a, **k: ([], [], [], []))
        monkeypatch.setattr(module, 'install', lambda s, config=None, overwrite_foreign=False: [
            TargetOutcome(os.path.join(ROOT, "bin"), True, "installed")])
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: shown.append(a)))
        self.buttons(panel)["Install"].click()
        assert not shown


class TestAskingBeforeWriting:
    """Installing writes a config, and what goes in it is the user's call."""

    def buttons(self, panel):
        return {b.text(): b for b in panel.findChildren(QtWidgets.QPushButton)}

    def test_a_sim_with_no_config_is_asked_about(self, app, monkeypatch):
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)))
        asked, written = [], []
        monkeypatch.setattr(module, 'ask_for_devices',
                            lambda *a, **k: (asked.append(True), ([], [], [], []))[1])
        monkeypatch.setattr(module, 'install',
                            lambda s, config=None, overwrite_foreign=False: written.append(config) or [])
        self.buttons(panel)["Install"].click()
        assert asked and written[0] is not None

    def test_an_existing_config_is_left_alone_without_asking(self, app,
                                                             monkeypatch):
        """Reinstalling the wrapper is not a request to rewrite the rules."""
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, has_config=True)))
        asked, written = [], []
        monkeypatch.setattr(module, 'ask_for_devices',
                            lambda *a, **k: (asked.append(True), ([], [], [], []))[1])
        monkeypatch.setattr(module, 'install',
                            lambda s, config=None, overwrite_foreign=False: written.append(config) or [])
        self.buttons(panel)["Reinstall"].click()
        assert not asked and written == [None]

    def test_cancelling_installs_nothing_at_all(self, app, monkeypatch):
        """Backing out of the question backs out of the install - not an
        install with no rules, which looks identical and behaves differently."""
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)))
        ran = []
        monkeypatch.setattr(module, 'ask_for_devices', lambda *a, **k: None)
        monkeypatch.setattr(module, 'install',
                            lambda s, config=None, overwrite_foreign=False: ran.append(True) or [])
        self.buttons(panel)["Install"].click()
        assert ran == []


class TestConfiguringSeparately:
    """Installing moves a DLL; configuring decides what the game hands over.
    Kept apart so a reinstall does not re-ask, and so a config can be changed
    without touching the wrapper."""

    def buttons(self, panel):
        return {b.text(): b for b in panel.findChildren(QtWidgets.QPushButton)}

    def test_it_is_offered_once_our_wrapper_is_installed(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP)))
        assert "Configure Devices..." in self.buttons(panel)

    def test_it_is_not_offered_before_anything_is_installed(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)))
        assert "Configure Devices..." not in self.buttons(panel)

    def test_an_existing_config_is_amended_rather_than_replaced(self, app,
                                                                monkeypatch):
        import telemffb.TapStatusPanel as module
        from telemffb.tap_install import TapDevice
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, has_config=True)))
        rhino = TapDevice("joystick", 0xFFFF, 0x2054, "VPforce Rhino")
        written = []
        monkeypatch.setattr(module, 'read_configs', lambda s: [
            (r"C:\DCS\bin", "; theirs\n[FFBDevices]\nWarthog=block\n")])
        monkeypatch.setattr(module, 'ask_for_devices',
                            lambda *a, **k: ([rhino], [], [], []))
        monkeypatch.setattr(module, 'write_one_config',
                            lambda d, text: written.append(text) or ok(d))
        self.buttons(panel)["Configure Devices..."].click()
        assert "; theirs" in written[0]
        assert "Warthog=block" in written[0]
        assert "FFFF:2054=tap" in written[0]

    def test_cancelling_writes_nothing(self, app, monkeypatch):
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, has_config=True)))
        written = []
        monkeypatch.setattr(module, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", "[FFBDevices]\n")])
        monkeypatch.setattr(module, 'ask_for_devices', lambda *a, **k: None)
        monkeypatch.setattr(module, 'write_one_config',
                            lambda d, text: written.append(text) or ok(d))
        self.buttons(panel)["Configure Devices..."].click()
        assert written == []


class TestOverwritingSomebodyElsesDll:
    """A dinput8.dll that is not ours may belong to a mod the user wants.
    We do not try to identify it - several tools install a proxy under this
    name - so the install is offered and the decision is theirs."""

    def buttons(self, panel):
        return {b.text(): b for b in panel.findChildren(QtWidgets.QPushButton)}

    def _stub(self, monkeypatch, module, answer, calls, done):
        monkeypatch.setattr(module, 'confirm_overwrite',
                            lambda *a, **k: (calls.append(a), answer)[1])
        monkeypatch.setattr(module, 'ask_for_devices', lambda *a, **k: ([], [], [], []))
        monkeypatch.setattr(
            module, 'install',
            lambda s, config=None, overwrite_foreign=False:
                done.append(overwrite_foreign) or [])

    def test_the_user_is_asked_first(self, app, monkeypatch):
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(target("bin", WrapperState.FOREIGN)))
        calls, done = [], []
        self._stub(monkeypatch, module, True, calls, done)
        self.buttons(panel)["Install"].click()
        assert calls and done == [True]

    def test_declining_installs_nothing(self, app, monkeypatch):
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(target("bin", WrapperState.FOREIGN)))
        calls, done = [], []
        self._stub(monkeypatch, module, False, calls, done)
        self.buttons(panel)["Install"].click()
        assert done == []

    def test_the_directory_is_named_so_they_know_what_is_at_risk(self, app,
                                                                 monkeypatch):
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(target("bin", WrapperState.FOREIGN)))
        calls, done = [], []
        self._stub(monkeypatch, module, True, calls, done)
        self.buttons(panel)["Install"].click()
        assert os.path.join(ROOT, "bin") in calls[0][1]

    def test_nothing_is_asked_when_no_foreign_dll_is_present(self, app,
                                                             monkeypatch):
        """A question with an obvious answer trains people to click through
        the ones that matter."""
        import telemffb.TapStatusPanel as module
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)))
        calls, done = [], []
        self._stub(monkeypatch, module, True, calls, done)
        self.buttons(panel)["Install"].click()
        assert calls == [] and done == [False]


class TestDriftIsVisible:
    """A config can fall out of date with nothing having just happened -
    hardware swapped while TelemFFB was closed, for instance.  The panel is
    the only place that would ever say so."""

    def test_a_stale_rule_is_called_out(self, app):
        from telemffb.tap_config import Rule
        st = status(target("bin", WrapperState.TAP, has_config=True))
        st.stale_rules = [Rule("FFFF:2054", "tap", 1, ids=(0xFFFF, 0x2054))]
        shown = " ".join(rendered(TapStatusPanel(st)))
        assert "not selected" in shown
        assert "FFFF:2054" in shown




class TestItAsksAboutTheDeviceOnScreen:
    """People change everything in one visit: pick a device, then go set up
    the sim that uses it. Reading only saved settings would offer them the
    device they just replaced, and make them save and reopen the dialog to
    get an answer already on screen."""

    def buttons(self, panel):
        return {b.text(): b for b in panel.findChildren(QtWidgets.QPushButton)}

    def test_the_unsaved_selection_is_what_gets_asked_about(self, app,
                                                            monkeypatch):
        import telemffb.TapStatusPanel as module
        from telemffb.tap_install import TapDevice

        selected = [TapDevice("joystick", 0xFFFF, 0x2054, "Monster")]
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)),
                               devices=lambda: list(selected))
        asked = []
        monkeypatch.setattr(
            module, 'ask_for_devices',
            lambda sim, devices, *a, **k: asked.append(devices) or ([], [], [], []))
        monkeypatch.setattr(
            module, 'install',
            lambda s, config=None, overwrite_foreign=False: [])

        self.buttons(panel)["Install"].click()
        assert [d.ident for d in asked[0]] == ["Monster"]

        # the user picks a different stick without leaving the dialog
        selected[:] = [TapDevice("joystick", 0x045E, 0x001B, "Sidewinder")]
        self.buttons(panel)["Install"].click()
        assert [d.ident for d in asked[1]] == ["Sidewinder"]



class TestReadingTheConfigItself:
    """Everything the panel and its prompts say about a config is a summary.
    A link to the file is what settles a disagreement with one - and what
    lets someone check before agreeing to delete it."""

    def links(self, panel):
        return [w.text() for w in panel.findChildren(QtWidgets.QLabel)
                if "<a href" in w.text()]

    def test_a_target_with_a_config_offers_one(self, app):
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, has_config=True)))
        assert len(self.links(panel)) == 1
        assert "dinput8.ini" in self.links(panel)[0]

    def test_a_target_without_one_does_not(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP)))
        assert self.links(panel) == []

    def test_the_link_points_at_that_target(self, app):
        """Two executables can hold different files; a link to the wrong one
        would answer a question nobody asked."""
        panel = TapStatusPanel(status(
            target("bin", WrapperState.ABSENT),
            target("bin-mt", WrapperState.TAP, has_config=True)))
        assert "bin-mt" in self.links(panel)[0]

    def test_it_opens_without_a_handler(self, app):
        """Qt hands the URL to the desktop itself; a handler we forgot to
        connect would just do nothing when clicked."""
        panel = TapStatusPanel(status(
            target("bin", WrapperState.TAP, has_config=True)))
        label = [w for w in panel.findChildren(QtWidgets.QLabel)
                 if "<a href" in w.text()][0]
        assert label.openExternalLinks()


class TestConfiguringTwoFilesAtOnce:
    """A sim can hold two configs that differ. Writing one answer over both
    would discard whichever was not consulted - the same fault reconcile
    had, and the reason the preview showed only one file."""

    def buttons(self, panel):
        return {b.text(): b for b in panel.findChildren(QtWidgets.QPushButton)}

    OURS = "[FFBDevices]\nFFFF:2054=tap\n"
    THEIRS = "; hand written\n[FFBDevices]\nWarthog=block\n"

    def wire(self, monkeypatch, module, answer):
        written = []
        monkeypatch.setattr(module, 'read_configs', lambda s: [
            (r"C:\DCS\bin", self.OURS), (r"C:\DCS\bin-mt", self.THEIRS)])
        monkeypatch.setattr(module, 'ask_for_devices', lambda *a, **k: answer)
        monkeypatch.setattr(module, 'write_one_config',
                            lambda d, text: written.append((d, text)) or ok(d))
        return written

    def panel(self):
        return TapStatusPanel(status(
            target("bin", WrapperState.TAP, has_config=True),
            target("bin-mt", WrapperState.TAP, has_config=True)))

    def test_each_file_is_written_from_its_own_contents(self, app,
                                                        monkeypatch):
        import telemffb.TapStatusPanel as module
        from telemffb.tap_install import TapDevice
        rhino = TapDevice("joystick", 0xFFFF, 0x2054, "Rhino")
        written = self.wire(monkeypatch, module, ([rhino], [], [], []))
        panel = self.panel()   # bound: a temporary is collected mid-click
        self.buttons(panel)["Configure Devices..."].click()

        assert [d for d, _ in written] == [r"C:\DCS\bin", r"C:\DCS\bin-mt"]
        by_dir = dict(written)
        assert "; hand written" in by_dir[r"C:\DCS\bin-mt"]
        assert "; hand written" not in by_dir[r"C:\DCS\bin"]

    def test_a_retirement_is_applied_by_name_not_by_line(self, app,
                                                         monkeypatch):
        """Line numbers only mean anything in the file they came from."""
        import telemffb.TapStatusPanel as module
        from telemffb.tap_config import read
        from telemffb.tap_install import TapDevice
        new = TapDevice("joystick", 0x045E, 0x001B, "SideWinder")
        # line 1 of OURS is the tap rule the dialog offered to replace
        written = self.wire(monkeypatch, module, ([new], [1], [], []))
        panel = self.panel()   # bound: a temporary is collected mid-click
        self.buttons(panel)["Configure Devices..."].click()

        by_dir = dict(written)
        assert not any(r.key == "FFFF:2054"
                       for r in read(by_dir[r"C:\DCS\bin"]).rules)
        # the other file never had that rule, so nothing of its own was lost
        assert "Warthog=block" in by_dir[r"C:\DCS\bin-mt"]


class TestLegacyWrapperOnThePanel:
    """A recognized ffb-fix wrapper renders as an upgrade opportunity,
    and Install asks the affirmative upgrade question - the cautious
    classify-it-yourself prompt is reserved for DLLs we cannot name."""

    def test_the_row_names_it_and_offers_the_upgrade(self):
        panel = TapStatusPanel(status(target("bin", WrapperState.LEGACY)))
        text = rendered(panel)
        assert any("ffb-fix wrapper installed" in t for t in text)
        assert any("Install upgrades it in place" in t for t in text)

    def _prompts(self, monkeypatch):
        import telemffb.TapStatusPanel as module
        calls = []
        monkeypatch.setattr(module, 'confirm_legacy_upgrade',
                            lambda *a, **k: calls.append('legacy') or False)
        monkeypatch.setattr(module, 'confirm_overwrite',
                            lambda *a, **k: calls.append('classify') or False)
        return calls

    def test_a_legacy_tree_gets_the_upgrade_question(self, monkeypatch):
        calls = self._prompts(monkeypatch)
        panel = TapStatusPanel(status(target("bin", WrapperState.LEGACY)))
        panel._install()
        assert calls == ['legacy']

    def test_an_unknown_dll_keeps_the_cautious_question(self, monkeypatch):
        calls = self._prompts(monkeypatch)
        panel = TapStatusPanel(status(target("bin", WrapperState.FOREIGN)))
        panel._install()
        assert calls == ['classify']

    def test_a_mixed_tree_gets_the_cautious_question_once(self, monkeypatch):
        """One folder ffb-fix, one unknown: the cautious question covers
        both, and nobody is asked twice."""
        calls = self._prompts(monkeypatch)
        panel = TapStatusPanel(status(target("bin", WrapperState.LEGACY),
                                      target("bin-mt", WrapperState.FOREIGN)))
        panel._install()
        assert calls == ['classify']


class TestFixOnlyInstall:
    """The wrapper's original job, offered without the tap: block the
    devices the sim will not drive, put the joystick first, relay
    nothing.  For users who want dcs-force-feedback-fix behaviour and
    no TelemFFB involvement in the game's own forces."""

    def buttons(self, panel):
        return {b.text(): b for b in panel.findChildren(QtWidgets.QPushButton)}

    def test_offered_only_where_device_order_decides_the_stick(self, app):
        """DCS today - the reason the original fix exists.  IL-2
        identifies devices by position, so it is not offered there."""
        il2 = SIMS_BY_KEY['IL2']
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT),
                                      sim=il2))
        assert "Install FFB Fix Only" not in self.buttons(panel)

    def test_the_config_taps_nothing(self):
        from telemffb.tap_config import read
        from telemffb.tap_install import fix_only_config
        text = fix_only_config(DCS, DEVICES)
        assert not [r for r in read(text).rules if r.is_tap]

    def test_it_blocks_what_the_sim_will_not_drive(self):
        from telemffb.tap_config import read
        from telemffb.tap_install import fix_only_config
        blocks = {r.key for r in read(fix_only_config(DCS, DEVICES)).rules
                  if r.value == 'block'}
        assert 'FFFF:2052' in blocks           # pedals
        assert 'FFFF:2051' in blocks           # collective
        assert 'FFFF:2054' not in blocks       # the joystick is the point

    def test_the_joystick_is_ordered_first(self):
        from telemffb.tap_config import read
        from telemffb.tap_install import fix_only_config
        entries = [(e.position, e.match)
                   for e in read(fix_only_config(DCS, DEVICES)).order]
        assert entries == [("1", "FFFF:2054")]

    def test_korea_would_not_block_its_pedals(self):
        """The blocks fall out of the capability table, so a sim that
        does drive pedals keeps them - no special case."""
        from telemffb.tap_config import read
        from telemffb.tap_install import fix_only_config
        korea = SIMS_BY_KEY['IL2_K']
        blocks = {r.key for r in read(fix_only_config(korea, DEVICES)).rules
                  if r.value == 'block'}
        assert 'FFFF:2052' not in blocks

    def _panel(self, *targets, fix_only=False):
        # held: a panel nothing references is collected, and Qt deletes
        # its children out from under the test
        self._held = TapStatusPanel(status(*targets),
                                    devices=lambda: DEVICES,
                                    fix_only=lambda: fix_only)
        return self._held

    def _toggle(self, panel):
        """The FFB-Fix toggle, or None.  A LabeledToggle like every other
        switch in the dialog, so it is found by type and read by its
        label's text."""
        from telemffb.custom_widgets import LabeledToggle
        for widget in panel.findChildren(LabeledToggle):
            if "FFB-Fix only" in widget.label.text_label.text():
                return widget
        return None

    def test_the_toggle_is_shown_for_dcs(self, app):
        panel = self._panel(target("bin", WrapperState.ABSENT))
        assert self._toggle(panel) is not None

    def test_no_toggle_where_the_sim_has_no_mode(self, app):
        """The key is set only for sims whose enumeration order decides
        which stick the game drives."""
        il2 = SIMS_BY_KEY['IL2']
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT),
                                      sim=il2))
        assert self._toggle(panel) is None

    def test_the_toggle_reflects_the_stored_choice(self, app):
        panel = self._panel(target("bin", WrapperState.ABSENT),
                            fix_only=True)
        assert self._toggle(panel).isChecked()

    def test_it_carries_the_explanation(self, app):
        """Rich text like the per-sim tap toggles: plain-text tooltips
        never word-wrap and would run off the screen, while Qt wraps
        HTML by itself."""
        from PyQt6.QtGui import QTextDocument
        tip = self._toggle(self._panel(
            target("bin", WrapperState.ABSENT))).toolTip()
        # Qt treats a tooltip as rich text when it carries markup, which
        # is what makes it wrap; parsing it back proves it is really HTML
        # rather than angle brackets in a plain string.
        assert "<p>" in tip and "<b>" in tip
        doc = QTextDocument()
        doc.setHtml(tip)
        rendered_text = doc.toPlainText()
        assert "OFF - Install DirectInput Tap:" in rendered_text
        assert "ON - Install FFB-Fix only:" in rendered_text
        assert "<p>" not in rendered_text, "markup should render, not show"

    def test_moving_it_tells_the_dialog(self, app):
        """The panel shows and applies the mode; the dialog owns the
        setting."""
        panel = self._panel(target("bin", WrapperState.ABSENT))
        seen = []
        panel.fix_only_toggled.connect(seen.append)
        self._toggle(panel).setChecked(True)
        assert seen == [True]

    def test_a_fresh_install_in_fix_mode_asks_nothing_and_taps_nothing(
            self, app, monkeypatch):
        """The mode decides every rule, so there is no device question."""
        import telemffb.TapStatusPanel as module
        from telemffb.tap_config import read
        asked, written = [], []
        monkeypatch.setattr(module, 'ask_for_devices',
                            lambda *a, **k: asked.append(True) or ([], [], [], []))
        monkeypatch.setattr(
            module, 'install',
            lambda s, config=None, overwrite_foreign=False:
            written.append(config) or [])
        panel = self._panel(target("bin", WrapperState.ABSENT), fix_only=True)
        panel._buttons(panel.status)
        {b.text(): b for b in panel.findChildren(
            QtWidgets.QPushButton)}["Install"].click()
        assert asked == [], "fix-only needs no device dialog"
        assert written and not [r for r in read(written[0]).rules if r.is_tap]

    def test_the_panel_says_what_is_actually_installed(self, app,
                                                       monkeypatch):
        """The toggle is intent; the file is fact.  When they differ the
        panel says so rather than letting the toggle imply otherwise."""
        import telemffb.TapStatusPanel as module
        monkeypatch.setattr(module, 'installed_mode',
                            lambda status: module.MODE_TAP)
        panel = self._panel(target("bin", WrapperState.TAP, has_config=True),
                            fix_only=True)
        line = [t for t in rendered(panel) if "installed as:" in t]
        assert line and "DirectInput Tap" in line[0]
        assert "Configure Devices" in line[0]

    def test_no_such_line_when_they_agree(self, app, monkeypatch):
        import telemffb.TapStatusPanel as module
        monkeypatch.setattr(module, 'installed_mode',
                            lambda status: module.MODE_FIX_ONLY)
        panel = self._panel(target("bin", WrapperState.TAP, has_config=True),
                            fix_only=True)
        assert not any("installed as:" in t for t in rendered(panel))

    def test_that_case_still_says_what_is_installed(self, app, monkeypatch):
        """Silent about rewriting, not silent about the state: the line
        points at Configure Devices instead of Reinstall."""
        import telemffb.TapStatusPanel as module
        monkeypatch.setattr(module, 'installed_mode',
                            lambda status: module.MODE_FIX_ONLY)
        panel = self._panel(target("bin", WrapperState.TAP, has_config=True),
                            fix_only=False)
        line = [t for t in rendered(panel) if "installed as:" in t]
        assert line and "Configure Devices" in line[0]


class TestFixOnlyIsRefusedForADirectInputStick:
    """The mode hands the joystick to the game, which a DirectInput device
    cannot be handed to.

    TelemFFB reaches one only by holding it exclusively, so installing this
    way would leave the stick with nothing: no rule for TelemFFB to render,
    and no device for the game to open - while the panel called the install
    healthy, because the wrapper would be doing what it was asked.  Refused
    at the point of acting, not explained afterwards.
    """

    def _di_panel(self, *targets, fix_only=True):
        devices = [TapDevice("joystick", 0x044F, 0xB10A, "Foreign Stick",
                             directinput=True),
                   TapDevice("pedals", 0xFFFF, 0x2052, "Pedals")]
        self._held = TapStatusPanel(status(*targets),
                                    devices=lambda: devices,
                                    fix_only=lambda: fix_only)
        return self._held

    @pytest.fixture
    def refusals(self, monkeypatch):
        import telemffb.TapStatusPanel as module
        seen = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: seen.append(a[2])))
        # anything the refusal is supposed to come before
        monkeypatch.setattr(module, 'ask_for_devices',
                            lambda *a, **k: pytest.fail(
                                "the device dialog was opened anyway"))
        monkeypatch.setattr(module, 'install',
                            lambda *a, **k: pytest.fail(
                                "the wrapper was installed anyway"))
        return seen

    def test_the_toggle_refuses_and_springs_back(self, app, refusals):
        """Answered where the choice is made.  The switch does not stay on
        looking like a mode that was accepted."""
        panel = self._di_panel(target("bin", WrapperState.ABSENT),
                               fix_only=False)
        chosen = []
        panel.fix_only_toggled.connect(chosen.append)

        toggle = self._toggle(panel)
        toggle.toggle.nextCheckState()              # the user's own click
        QtWidgets.QApplication.processEvents()      # the revert is deferred

        assert refusals, "the mode was accepted"
        assert "Foreign Stick" in refusals[0], "the device is not named"
        assert not toggle.isChecked(), "the switch stayed on"
        assert True not in chosen, "the dialog was told to store the mode"

    def _toggle(self, panel):
        from telemffb.custom_widgets import LabeledToggle
        for widget in panel.findChildren(LabeledToggle):
            if "FFB-Fix only" in widget.label.text_label.text():
                return widget
        return None

    def test_install_is_refused_and_says_why(self, app, refusals):
        self._di_panel(target("bin", WrapperState.ABSENT))._install()
        assert refusals, "the install went ahead"
        assert "Foreign Stick" in refusals[0], "the device is not named"

    def test_configure_devices_never_opens(self, app, refusals):
        """The dialog is where the user would otherwise spend a while
        choosing rules for an install that cannot work."""
        self._di_panel(target("bin", WrapperState.TAP,
                              has_config=True))._configure()
        assert refusals

    def test_the_tap_is_unaffected(self, app, monkeypatch):
        """Only the mode is refused.  With it off, a DirectInput stick is
        the case the tap exists for."""
        import telemffb.TapStatusPanel as module
        seen = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: seen.append(a)))
        monkeypatch.setattr(module, 'ask_for_devices',
                            lambda *a, **k: ([], [], [], []))
        monkeypatch.setattr(module, 'install', lambda *a, **k: [])
        self._di_panel(target("bin", WrapperState.ABSENT),
                       fix_only=False)._install()
        assert not seen

    def test_a_natively_driven_stick_is_left_alone(self, app, monkeypatch):
        """VPforce hardware is what the mode is for."""
        import telemffb.TapStatusPanel as module
        seen = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: seen.append(a)))
        monkeypatch.setattr(module, 'install', lambda *a, **k: [])
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)),
                               devices=lambda: DEVICES,
                               fix_only=lambda: True)
        panel._install()
        assert not seen

    def test_directinput_pedals_do_not_block_it(self, app, monkeypatch):
        """Only the joystick decides.  DCS renders nothing to pedals, so a
        DirectInput pedal set loses nothing the mode was going to give it."""
        import telemffb.TapStatusPanel as module
        seen = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: seen.append(a)))
        monkeypatch.setattr(module, 'install', lambda *a, **k: [])
        devices = [TapDevice("joystick", 0xFFFF, 0x2054, "Monster"),
                   TapDevice("pedals", 0x044F, 0xB10A, "DI Pedals",
                             directinput=True)]
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT)),
                               devices=lambda: devices,
                               fix_only=lambda: True)
        panel._install()
        assert not seen
