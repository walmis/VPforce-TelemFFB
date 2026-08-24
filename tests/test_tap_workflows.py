"""Workflows nobody designed for.

The settings dialog ties together device selectors, sim switches, per-sim
tap opt-ins, four panel buttons, five prompts, Save and Cancel.  Every one
of those is reachable in any order, and the bugs that survive a feature's
own tests live in the orders its author never pictured.

So this file does not enumerate workflows.  It states what must hold *no
matter what order things happen in* - the invariants below - and then drives
the real dialog, over a real game tree in a temporary directory, through
seeded random sequences of everything a user can physically do, checking the
invariants after each step.  A failing seed is reproducible and becomes a
named test.  The second half is the seed corpus: specific illogical-but-
possible sequences worth pinning by name.

Prompts are answered by the harness, randomly.  The device dialog is the
real one - built, its boxes flipped at random, its answers read back - so
its own logic is exercised rather than stubbed around.
"""
import os
import random
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G
import telemffb.SystemSettingsDialog as dialog_module
import telemffb.TapStatusPanel as panel_module
from telemffb import tap_install
from telemffb.hw.ffb_rhino import DeviceInfo, FFBRhino
from telemffb.SystemSettingsDialog import (CLEANUP_CANCELLED, CLEANUP_LEAVE,
                                           CLEANUP_REMOVE, SystemSettingsDialog)
from telemffb.tap_config import read
from telemffb.tap_install import (GENERATED_MARKER, SIMS_BY_KEY, TapDevice,
                                  WrapperState, generate_config, sim_status)
from telemffb.TapDeviceDialog import TapDeviceDialog

pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

DCS = SIMS_BY_KEY["DCS"]

OUR_DLL = b"MZ\x00 FFB tap: device [%ls] bound to block %d \x00"
LEGACY_DLL = b"MZ\x00 dinput8.ini [FFBDevices] DeviceNameSubstring=action \x00"
LEGACY_INI = "\r\n".join([
    "[General]", "Enabled=true", "LogLevel=3", "",
    "[FFB]", "Enabled=true", "LogEffects=true", "DefaultScale=100", "",
    "[FFBDevices]",
    "; First matching rule wins.",
    "vJoy=block", "Pedals=block", "Collective=block", "",
])


def device(pid, name, path, vid=0xFFFF):
    """A fake the selectors will list, shaped like what enumeration returns."""
    return DeviceInfo(interface_number=0, manufacturer_string="VPforce",
                      path=path, product_id=pid, product_string=name,
                      release_number=0, serial_number=f"S{vid:04X}{pid:04X}",
                      usage=4, usage_page=1, vendor_id=vid)


RHINO = device(0x2054, "Rhino FFB Monster", rb"\\?\HID#VID_FFFF&PID_2054&MI_00#a")
WARTHOG = device(0xB10A, "Warthog", rb"\\?\HID#VID_044F&PID_B10A&MI_00#b", 0x044F)
PEDALS = device(0x2052, "Rhino FFB Pedals", rb"\\?\HID#VID_FFFF&PID_2052&MI_00#c")
COLLECTIVE = device(0x2051, "Rhino FFB Collective",
                    rb"\\?\HID#VID_FFFF&PID_2051&MI_00#d")
#: A DirectInput stick: its path is an instance GUID, not a HID path.
MOZA = device(0x0005, "[DI] MOZA AB9",
              b"dinput:{0d1e55b2-f16f-11cf-88cb-001111000030}", 0x346E)

#: What HID enumeration lists.  Row 0 is "(None)"; row i is ENUMERATED[i-1].
#: The DirectInput stick is listed by the DirectInput enumeration instead,
#: after these, and only while DirectInput support is on - as in the app.
ENUMERATED = [RHINO, WARTHOG, PEDALS, COLLECTIVE]
DINPUT_ROW = len(ENUMERATED) + 1
#: Which rows each slot may be set to.  Kept disjoint so the harness never
#: trips the device-conflict dialog, which is not the subject here.
ROWS = {"joystick": (0, 1, 2, DINPUT_ROW), "pedals": (0, 3), "collective": (0, 4)}
KNOWN_IDS = {f"{d.vendor_id:04X}:{d.product_id:04X}" for d in ENUMERATED + [MOZA]}

YES = QtWidgets.QMessageBox.StandardButton.Yes
NO = QtWidgets.QMessageBox.StandardButton.No
OK = QtWidgets.QMessageBox.StandardButton.Ok


class Settings(dict):
    def get(self, name, default=None, instance=None):
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value

    def value(self, key, default=None):
        return dict.get(self, key, default)

    def allKeys(self):
        return list(self.keys())

    @property
    def defaults(self):
        from telemffb.utils import SystemSettings
        merged = {}
        merged.update(SystemSettings.default_inst)
        merged.update(SystemSettings.globl_sys_dict)
        return merged


class Policy:
    """Answers every prompt the dialog can raise, at random unless told.

    ``fixed`` pins particular answers for the named workflows; everything
    else is drawn from the seed.  Every question asked is recorded, with its
    text, so a test can say what it expects to have been asked.
    """

    def __init__(self, rng, **fixed):
        self.rng = rng
        self.fixed = fixed
        self.asked = []
        self.warned = []

    def question(self, parent, title, text, *args, **kwargs):
        self.asked.append((title, text))
        if "question" in self.fixed:
            return YES if self.fixed["question"] else NO
        return YES if self.rng.random() < 0.6 else NO

    def information(self, parent, title, text, *args, **kwargs):
        self.asked.append((title, text))
        return OK

    def warning(self, parent, title, text, *args, **kwargs):
        self.warned.append((title, text))
        return OK

    def cleanup(self, message, plan):
        self.asked.append(("cleanup", message))
        return self.fixed.get("cleanup") or self.rng.choice(
            [CLEANUP_REMOVE, CLEANUP_LEAVE, CLEANUP_CANCELLED])

    def questions(self):
        """What was asked, minus the dialog's own 'Restart Required' notice,
        which predates the tap and fires on every save."""
        return [(t, x) for t, x in self.asked if t != "Restart Required"]

    def overwrite(self, sim_name, directories, parent=None):
        self.asked.append(("overwrite", sim_name))
        return self.fixed.get("overwrite", self.rng.random() < 0.7)

    def devices(self, sim, devices, existing=None, parent=None, preview=None):
        """Build the real device dialog, flip boxes at random, read it back.

        Returns None - the user cancelled - when the dialog would not let
        them press OK, exactly as the real thing does.
        """
        self.asked.append(("devices", sim.key))
        if self.fixed.get("devices") is not None:
            return self.fixed["devices"]
        if self.rng.random() < 0.1:
            return None
        dialog = TapDeviceDialog(sim, devices, existing=existing,
                                 preview=preview)
        for box in dialog.findChildren(QtWidgets.QCheckBox):
            if self.rng.random() < 0.3:
                box.setChecked(not box.isChecked())
        if dialog._ok is not None and not dialog._ok.isEnabled():
            return None
        return (dialog.chosen(), dialog.retire_lines(), dialog.ordered(),
                dialog.blocked())


def make_tree(tmp_path, start):
    """A DCS install in one of three starting states."""
    root = tmp_path / "DCS World"
    for sub in ("bin", "bin-mt"):
        (root / sub).mkdir(parents=True)
        (root / sub / "DCS.exe").write_bytes(b"exe")
    if start == "legacy":
        (root / "bin-mt" / "dinput8.dll").write_bytes(LEGACY_DLL)
        (root / "bin-mt" / "dinput8.ini").write_bytes(LEGACY_INI.encode())
    elif start == "ours":
        config = generate_config(
            [TapDevice("joystick", 0xFFFF, 0x2054, "Monster")],
            ordered=[TapDevice("joystick", 0xFFFF, 0x2054, "Monster")],
            blocked=[TapDevice("pedals", 0xFFFF, 0x2052, "Pedals")])
        for sub in ("bin", "bin-mt"):
            (root / sub / "dinput8.dll").write_bytes(OUR_DLL)
            (root / sub / "dinput8.ini").write_bytes(config.encode())
    return str(root)


class World:
    """One settings dialog over a real tree, with the means to drive it."""

    def __init__(self, tmp_path, monkeypatch, rng, start="legacy",
                 settings=None, **fixed):
        self.mp = monkeypatch
        self.rng = rng
        self.root = make_tree(tmp_path, start)
        self.start = start
        self.policy = Policy(rng, **fixed)
        self.settings = Settings({
            'devpath_joystick': RHINO.path.decode(), 'devids_joystick': 'FFFF:2054',
            'devident_joystick': 'Monster',
            'devpath_pedals': PEDALS.path.decode(), 'devids_pedals': 'FFFF:2052',
            'devident_pedals': 'Pedals',
            'devpath_collective': '', 'devpath_trimwheel': '',
            'enableDCS': True, 'enableIL2': False, 'enableBMS': False,
            'enableTapDCS': False, 'enableDirectInput': False,
        })
        self.settings.update(settings or {})
        self.caught = []
        monkeypatch.setattr(sys, "excepthook",
                            lambda t, v, tb: self.caught.append((t, v, tb)))

        monkeypatch.setattr(G, 'system_settings', self.settings, raising=False)
        for name, value in (('device_type', 'joystick'), ('master_instance', True),
                            ('child_instance', False), ('launched_instances', []),
                            ('device_usbpid', '2054'), ('device_capabilities', None),
                            ('device_di_guid', None), ('is_exe', False),
                            ('device_connection_status', True),
                            ('sim_listeners', SimpleNamespace(restart_all=lambda: None)),
                            ('ipc_instance', None)):
            monkeypatch.setattr(G, name, value, raising=False)
        monkeypatch.setattr(tap_install, 'steam_common_dirs', lambda: [])
        monkeypatch.setattr(tap_install, 'dcs_registry_roots', lambda: [self.root])
        monkeypatch.setattr(tap_install, 'bms_registry_roots', lambda: [])
        bundled = tmp_path / "bundled.dll"
        bundled.write_bytes(OUR_DLL)
        monkeypatch.setattr(tap_install, 'bundled_wrapper', lambda: str(bundled))
        monkeypatch.setattr(FFBRhino, 'enumerate',
                            staticmethod(lambda: list(ENUMERATED)))
        # the DirectInput list, gated the way the real one is - and not the
        # real one, which would find whatever is plugged into this machine
        monkeypatch.setattr(
            SystemSettingsDialog, '_enumerate_dinput_devices',
            staticmethod(lambda enabled=None: [MOZA] if (
                enabled if enabled is not None
                else self.settings.get('enableDirectInput')) else []))
        monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                            lambda *a, **k: (True, ''))
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question', self.policy.question)
        monkeypatch.setattr(QtWidgets.QMessageBox, 'information',
                            self.policy.information)
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning', self.policy.warning)
        monkeypatch.setattr(SystemSettingsDialog, '_ask_with_preview',
                            self.policy.cleanup)
        monkeypatch.setattr(panel_module, 'confirm_overwrite', self.policy.overwrite)
        monkeypatch.setattr(panel_module, 'ask_for_devices', self.policy.devices)
        # the live device-switch primitive lives in main.py and is registered
        # on G when the app starts; a save with a changed device calls it.
        # Recorded here so tests can assert on it - and so a test run that
        # happens to have imported main never opens real hardware.
        self.device_switches = 0

        def record_switch():
            self.device_switches += 1
            return True
        monkeypatch.setattr(G, 'switch_to_device', record_switch, raising=False)
        self.dialog = None
        self.open()

    # -------------------------------------------------------------- state
    def tree(self):
        """Every file under the game root, as bytes."""
        found = {}
        for folder, _, files in os.walk(self.root):
            for name in files:
                path = os.path.join(folder, name)
                with open(path, "rb") as handle:
                    found[os.path.relpath(path, self.root)] = handle.read()
        return found

    def open(self):
        self.dialog = SystemSettingsDialog()
        self.opened_tree = self.tree()
        self.opened_settings = dict(self.settings)
        self.check()

    # ------------------------------------------------------------ actions
    def set_device(self, role, row):
        combo = {"joystick": self.dialog.cb_select_j,
                 "pedals": self.dialog.cb_select_p,
                 "collective": self.dialog.cb_select_c}[role]
        if row >= combo.model().rowCount():
            return False            # not in the list right now
        combo.setCurrentIndex(row)
        return True

    def sim(self, name, on):
        getattr(self.dialog, name).setChecked(on)

    def tap(self, key, on):
        self.dialog.tap_enable_boxes[key].setChecked(on)

    def buttons(self, key):
        panel = self.dialog.tap_panels[key]
        return {b.text(): b for b in panel.findChildren(QtWidgets.QPushButton)}

    def press(self, key, text):
        """Click a panel button, if the panel is showing it right now."""
        if self.dialog.tap_panels[key].isHidden():
            return False
        button = self.buttons(key).get(text)
        if button is None:
            return False
        button.click()
        return True

    def dinput(self, on):
        self.dialog.cb_enable_dinput.setChecked(on)

    def save(self):
        """Save, reporting whether the dialog accepted it.

        A save the dialog refuses - no master device chosen, an IL-2
        path that does not exist - writes nothing and leaves the dialog
        open, which is correct and is not what the saved-state
        invariants are about.
        """
        if not self.dialog.validate_settings():
            return False
        self.dialog.save_settings()
        return True

    def cancel(self):
        self.dialog.close()

    def escape(self):
        self.dialog.reject()

    def random_action(self):
        kind = self.rng.choice(["device", "device", "sim", "tap", "tap",
                                "button", "button", "dinput"])
        if kind == "device":
            role = self.rng.choice(list(ROWS))
            self.set_device(role, self.rng.choice(ROWS[role]))
        elif kind == "sim":
            self.sim("enableDCS", self.rng.random() < 0.7)
        elif kind == "tap":
            self.tap("DCS", self.rng.random() < 0.7)
        elif kind == "button":
            self.press("DCS", self.rng.choice(
                ["Install", "Complete Install", "Reinstall",
                 "Configure Devices...", "Remove"]))
        else:
            self.dinput(self.rng.random() < 0.5)
        return kind

    # --------------------------------------------------------- invariants
    def check(self):
        """What must hold at every moment the dialog is open."""
        assert not self.caught, self.caught[0]
        for key, panel in self.dialog.tap_panels.items():
            box = self.dialog.tap_enable_boxes[key]
            assert panel.isHidden() == (not box.isChecked()), \
                f"{key}: panel hidden={panel.isHidden()} but toggle={box.isChecked()}"

    def check_cancelled(self):
        """Cancel means nothing happened - to the settings or the folder."""
        assert self.tree() == self.opened_tree, "cancel left the game folder changed"
        assert dict(self.settings) == self.opened_settings, "cancel changed settings"

    def check_saved(self):
        """What every managed config must look like after a save."""
        for rel, data in self.tree().items():
            if not rel.endswith("dinput8.ini"):
                continue
            assert b"\r\r\n" not in data, f"{rel}: doubled carriage returns"
            text = data.decode("utf-8", "surrogateescape")
            facts = read(text)
            seen = {}
            for rule in facts.rules:
                if rule.ids is None:
                    continue
                assert rule.ids not in seen, \
                    f"{rel}: two active rules for {rule.key}: {seen[rule.ids]} and {rule.value}"
                seen[rule.ids] = rule.value
            active = [l for l in text.splitlines()
                      if l.strip().lower().startswith("requiretelemffb")]
            assert len(active) <= 1, f"{rel}: RequireTelemFFB written twice"
            for rule in facts.rules:
                if rule.is_tap and rule.ids is not None:
                    assert rule.key.upper() in KNOWN_IDS, \
                        f"{rel}: tap rule for a device nobody configured: {rule.key}"
            before = self.opened_tree.get(rel)
            if before is not None and GENERATED_MARKER.encode() not in before:
                # their file: every line they had is still there, or is there
                # commented out as retired - never silently gone
                lines = text.splitlines()
                for theirs in before.decode("utf-8", "surrogateescape").splitlines():
                    if not theirs.strip():
                        continue
                    assert theirs in lines or \
                        f"; retired by TelemFFB: {theirs.strip()}" in lines, \
                        f"{rel}: their line vanished: {theirs!r}"
        # The settings agree with the selectors, for every slot.  One
        # exception, by design: a configured device that is not in the
        # list right now (unplugged, or DirectInput switched off under a
        # DirectInput stick) keeps its slot, because a rule can be written
        # for a device that is switched off - so the selector shows none
        # while the setting holds the path.
        for role, combo in (("joystick", self.dialog.cb_select_j),
                            ("pedals", self.dialog.cb_select_p),
                            ("collective", self.dialog.cb_select_c)):
            model = combo.model()
            from PyQt6.QtCore import Qt
            def path_at(r):
                dev = model.data(model.index(r, 0), Qt.ItemDataRole.UserRole)
                return dev.path.decode() if dev is not None else ''
            shown = path_at(combo.currentIndex())
            held = self.settings.get(f"devpath_{role}", '')
            listed = {path_at(r) for r in range(1, model.rowCount())}
            assert held == shown or (shown == '' and held not in listed), \
                f"{role}: selector shows {shown!r} but settings hold {held!r}"
        for key, box in self.dialog.tap_enable_boxes.items():
            assert bool(self.settings.get(SIMS_BY_KEY[key].tap_enable_key)) == \
                box.isChecked()

    def check_save_is_idempotent(self):
        """A second save with nothing changed asks nothing and writes nothing."""
        before, asked = self.tree(), len(self.policy.questions())
        self.dialog.save_settings()
        assert self.tree() == before, "a second save rewrote files"
        assert len(self.policy.questions()) == asked, \
            f"a second save asked: {self.policy.questions()[asked:]}"


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ======================================================================
# the fuzz
# ======================================================================
class TestAnyOrderAtAll:
    """Seeded random sequences against the invariants.  A failure prints the
    seed and the sequence so it can be replayed - and then pinned below."""

    @pytest.mark.parametrize("seed", range(40))
    def test_random_sequence(self, app, tmp_path, monkeypatch, seed):
        rng = random.Random(seed)
        start = rng.choice(["legacy", "empty", "ours"])
        settings = {}
        if rng.random() < 0.3:
            # nothing configured: every save is refused until a stick is
            settings = {'devpath_joystick': '', 'devids_joystick': '',
                        'devident_joystick': '', 'devpath_pedals': '',
                        'devids_pedals': '', 'devident_pedals': ''}
        world = World(tmp_path, monkeypatch, rng, start=start, settings=settings)
        trace = [f"start={start} preset={bool(settings)}"]
        try:
            for session in range(2):
                for _ in range(rng.randint(2, 9)):
                    trace.append(world.random_action())
                    world.check()
                if rng.random() < 0.6:
                    before_tree, before_settings = world.tree(), dict(world.settings)
                    if world.save():
                        trace.append("save")
                        world.check_saved()
                        world.check_save_is_idempotent()
                    else:
                        # refused: nothing may have moved, and the user is
                        # left in the dialog - they give up
                        trace.append("save-refused > cancel")
                        assert world.tree() == before_tree
                        assert dict(world.settings) == before_settings
                        world.cancel()
                        world.check_cancelled()
                else:
                    trace.append(rng.choice(["cancel", "escape"]))
                    (world.cancel if trace[-1] == "cancel" else world.escape)()
                    world.check_cancelled()
                trace.append("reopen")
                world.open()
        except AssertionError as failure:
            raise AssertionError(f"seed {seed}: {' > '.join(trace)}\n{failure}") \
                from failure


# ======================================================================
# the seed corpus: illogical but possible, by name
# ======================================================================
class TestInstallThenBackOut:
    def test_install_then_cancel_puts_the_legacy_wrapper_back(
            self, app, tmp_path, monkeypatch):
        """The one that started this: Install writes at once, Cancel never
        saves the opt-in, and the wrapper would have stayed active in DCS
        with nothing in TelemFFB saying so."""
        world = World(tmp_path, monkeypatch, random.Random(0), overwrite=True)
        world.tap("DCS", True)
        assert world.press("DCS", "Install")
        assert world.tree()["bin-mt\\dinput8.dll"] == OUR_DLL
        world.cancel()
        world.check_cancelled()
        assert world.tree()["bin-mt\\dinput8.dll"] == LEGACY_DLL
        assert "bin\\dinput8.dll" not in world.tree()

    def test_escape_is_cancel_too(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), overwrite=True)
        world.tap("DCS", True)
        world.press("DCS", "Install")
        world.escape()
        world.check_cancelled()

    def test_install_then_save_keeps_it(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), overwrite=True)
        world.tap("DCS", True)
        world.press("DCS", "Install")
        assert world.save()
        assert world.tree()["bin\\dinput8.dll"] == OUR_DLL
        assert world.settings["enableTapDCS"] is True

    def test_configure_then_cancel_restores_the_file(self, app, tmp_path,
                                                     monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings={'devpath_joystick': RHINO.path.decode(),
                                'devids_joystick': 'FFFF:2054',
                                'devpath_pedals': PEDALS.path.decode(),
                                'devids_pedals': 'FFFF:2052'},
                      devices=([], [], [], []))
        world.tap("DCS", True)
        assert world.press("DCS", "Configure Devices...")
        world.cancel()
        world.check_cancelled()

    def test_a_cancel_after_several_actions_undoes_them_all(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), overwrite=True,
                      devices=([], [], [], []))
        world.tap("DCS", True)
        world.press("DCS", "Install")
        world.press("DCS", "Configure Devices...")
        world.press("DCS", "Remove")
        world.cancel()
        world.check_cancelled()


class TestChangingYourMindAboutADevice:
    PRESET = {'devpath_joystick': RHINO.path.decode(), 'devids_joystick': 'FFFF:2054',
              'devident_joystick': 'Monster', 'enableTapDCS': True}

    def reconcile_questions(self, world):
        return [t for title, t in world.policy.asked
                if "still names the old device" in t]

    def test_change_and_change_back_writes_nothing(self, app, tmp_path,
                                                   monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings=self.PRESET, question=True)
        world.set_device("joystick", 2)          # Warthog: asked, says yes
        assert len(self.reconcile_questions(world)) == 1
        world.set_device("joystick", 1)          # back to the Monster
        assert world.save()
        assert world.tree() == world.opened_tree
        world.check_save_is_idempotent()

    def test_a_different_change_is_a_new_notice(self, app, tmp_path,
                                                monkeypatch):
        """Once-per-dialog would nag nobody but would also stay silent about
        a move to a third device - a new stale rule the user never heard
        of.  Once per change: cycling the same device is quiet, a different
        device is said."""
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings={**self.PRESET, 'enableDirectInput': True})
        world.set_device("joystick", 2)          # Warthog: said
        world.set_device("joystick", 2)          # same again: not said
        assert len(self.reconcile_questions(world)) == 1
        world.set_device("joystick", 5)          # Moza: said again
        assert len(self.reconcile_questions(world)) == 2

    def test_what_was_staged_at_the_change_is_written_at_save(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings=self.PRESET, question=True)
        world.set_device("joystick", 2)
        asked = len(world.policy.questions())
        assert world.save()
        assert len(world.policy.questions()) == asked, "said again at save"
        text = world.tree()["bin\\dinput8.ini"].decode()
        assert "044F:B10A=tap" in text
        assert "; retired by TelemFFB: FFFF:2054=tap" in text
        world.check_saved()

    def test_clearing_the_master_slot_cannot_be_saved(self, app, tmp_path,
                                                     monkeypatch):
        """The reconcile question is asked - the rule would be stranded -
        but the dialog refuses a save with no master device, so nothing is
        written and nothing is stranded.  Backing out leaves it all."""
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings=self.PRESET, question=True)
        world.set_device("joystick", 0)
        assert len(self.reconcile_questions(world)) == 1
        assert not world.save()
        assert world.tree() == world.opened_tree
        world.cancel()
        world.check_cancelled()


class TestSwitchesAndButtonsOutOfOrder:
    def test_install_with_the_sim_switched_off(self, app, tmp_path, monkeypatch):
        """Allowed - the panel is about the folder, not the sim switch - and
        quiet: no sim that is off gets asked about."""
        world = World(tmp_path, monkeypatch, random.Random(0), overwrite=True,
                      devices=([], [], [], []))
        world.sim("enableDCS", False)
        world.tap("DCS", True)
        assert world.press("DCS", "Install")
        assert world.save()
        assert world.tree()["bin\\dinput8.dll"] == OUR_DLL
        assert not [t for t, _ in world.policy.asked if t == "cleanup"]

    def test_remove_then_opt_out_offers_the_orphaned_config(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings={'enableTapDCS': True}, cleanup=CLEANUP_REMOVE)
        assert world.press("DCS", "Remove")
        assert "bin\\dinput8.dll" not in world.tree()
        world.tap("DCS", False)
        assert [t for t, _ in world.policy.asked if t == "cleanup"]
        assert world.save()
        assert "bin\\dinput8.ini" not in world.tree()

    def test_rapid_toggling_asks_once_and_changes_nothing(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings={'enableTapDCS': True}, cleanup=CLEANUP_LEAVE)
        for on in (False, True, False, True, False, True):
            world.tap("DCS", on)
            world.check()
        assert len([t for t, _ in world.policy.asked if t == "cleanup"]) == 1
        assert world.save()
        assert world.tree() == world.opened_tree

    def test_reinstall_keeps_a_config_the_user_edited(self, app, tmp_path,
                                                      monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings={'enableTapDCS': True})
        path = os.path.join(world.root, "bin", "dinput8.ini")
        with open(path, "ab") as handle:
            handle.write(b"; my note, do not lose\r\n")
        assert world.press("DCS", "Reinstall")
        assert world.save()
        assert b"; my note, do not lose" in world.tree()["bin\\dinput8.ini"]

    def test_turning_directinput_off_under_a_directinput_stick(
            self, app, tmp_path, monkeypatch):
        """A checkbox the user does not connect to devices takes the stick
        out of the list.  The slot keeps its setting - the device is not
        gone, only unlisted - but with no master device showing, the save
        is refused until they pick one, and what they then save agrees
        with what the selectors show."""
        world = World(tmp_path, monkeypatch, random.Random(0), start="ours",
                      settings={'devpath_joystick': MOZA.path.decode(),
                                'devids_joystick': '346E:0005',
                                'enableDirectInput': True, 'enableTapDCS': True},
                      question=True)
        world.dinput(False)
        world.check()
        assert world.dialog.cb_select_j.currentIndex() == 0
        assert not world.save()
        world.set_device("joystick", 1)
        assert world.save()
        world.check_saved()
        assert world.settings['devpath_joystick'] == RHINO.path.decode()
