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

from telemffb.tap_install import SIMS_BY_KEY, SimStatus, TargetStatus, WrapperState
from telemffb.TapStatusPanel import TapStatusPanel

pytestmark = [pytest.mark.unit]

DCS = SIMS_BY_KEY['DCS']
ROOT = r"C:\Games\DCS World"


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
    return [label.text() for label in panel.findChildren(QtWidgets.QLabel)]


class TestHeadline:
    @staticmethod
    def attention_texts(panel):
        """Labels drawn in the attention color."""
        amber = panel._color("attention")
        return [w.text() for w in panel.findChildren(QtWidgets.QLabel)
                if amber in w.styleSheet()]

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
        panel = TapStatusPanel(status(target("bin", WrapperState.ABSENT),
                                      target("bin-mt", WrapperState.TAP)))
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
        assert rendered(panel).count("v0.9.0.0") == 2

    def test_a_wrapper_without_a_version_says_so(self, app):
        """Builds predating the version resource are still ours; they just
        cannot say which build they are."""
        panel = TapStatusPanel(status(target("bin", WrapperState.TAP)))
        assert "version unknown" in rendered(panel)

    def test_no_version_is_shown_for_a_target_without_our_wrapper(self, app):
        panel = TapStatusPanel(status(target("bin", WrapperState.FOREIGN,
                                             version="9.9.9")))
        assert not [t for t in rendered(panel) if t.startswith("v")]

    def test_targets_are_named_relative_to_the_root(self, app):
        """IL-2's target basename is "game", which names nothing alone."""
        il2 = SIMS_BY_KEY['IL2']
        root = r"C:\Games\IL-2 Sturmovik Great Battles"
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
        assert "v0.9.0.0" in text

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
