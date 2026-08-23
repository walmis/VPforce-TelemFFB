"""Keeping a sim's tap config in step with the devices TelemFFB is driving.

When hardware is swapped, a config left behind still hands the old device to
TelemFFB, TelemFFB has never heard of it, and the new stick quietly keeps the
game's own force feedback. Nothing reports any of that, which is why it is
worth interrupting the user over.

The other half of the job is not interrupting them the rest of the time. A
prompt that appears when nothing needs doing teaches people to dismiss the
ones that matter, so most of what is pinned here is when reconcile stays
silent.
"""
import pytest

from telemffb import tap_reconcile
from telemffb.tap_config import read, stale_tap_rules
from telemffb.utils import DEVICE_ROLES
from telemffb.tap_install import (SIMS_BY_KEY, SimStatus, TapDevice,
                                  TargetStatus, WrapperState)
from telemffb.tap_reconcile import (DeviceChange, ReconcileItem,
                                    apply_reconcile, device_changes,
                                    pending_reconcile, sim_is_enabled)

pytestmark = [pytest.mark.unit]

OLD = (0xFFFF, 0x2054)
NEW_DEVICE = TapDevice("joystick", 0x044F, 0xB10A, "Warthog")

TAPPED = "[FFBDevices]\nFFFF:2054=tap    ; old stick (joystick)\n"

DCS_PATH = r"\\?\HID#VID_FFFF&PID_2054&MI_00#x"
NEW_PATH = r"\\?\HID#VID_044F&PID_B10A&MI_00#x"


class Settings(dict):
    def get(self, name, default=None, instance=None):
        return dict.get(self, name, default)


def dcs_status(state=WrapperState.TAP):
    return SimStatus(sim=SIMS_BY_KEY["DCS"], root=r"C:\DCS",
                     provenance="test",
                     targets=[TargetStatus(r"C:\DCS\bin", state,
                                           has_config=True)])


@pytest.fixture
def config(monkeypatch):
    """The config every sim is holding, swappable per test."""
    holder = {"text": TAPPED}
    monkeypatch.setattr(tap_reconcile, 'read_configs',
                        lambda s: [(r"C:\DCS\bin", holder["text"])]
                        if holder["text"] is not None else [])
    return holder


class TestSpottingASwap:
    def test_a_changed_slot_is_a_change(self):
        changes = device_changes({"devpath_joystick": DCS_PATH},
                                 {"devpath_joystick": NEW_PATH})
        assert [(c.role, c.was) for c in changes] == [("joystick", OLD)]

    def test_the_new_device_comes_with_it(self):
        changes = device_changes(
            {"devpath_joystick": DCS_PATH},
            {"devpath_joystick": NEW_PATH, "devident_joystick": "Warthog"})
        assert changes[0].now.ident == "Warthog"
        assert changes[0].now.key == "044F:B10A"

    def test_an_untouched_slot_is_not_a_change(self):
        assert device_changes({"devpath_joystick": DCS_PATH},
                              {"devpath_joystick": DCS_PATH}) == []

    def test_the_same_device_at_a_different_path_is_not_a_change(self):
        """Windows paths carry instance data that shifts between
        enumerations; rewriting configs over that would be pure noise."""
        other = r"\\?\HID#VID_FFFF&PID_2054&MI_00#f&99999999&0&0000"
        assert device_changes({"devpath_joystick": DCS_PATH},
                              {"devpath_joystick": other}) == []

    def test_clearing_a_slot_is_a_change_with_nothing_to_put_back(self):
        changes = device_changes({"devpath_joystick": DCS_PATH},
                                 {"devpath_joystick": ""})
        assert changes[0].was == OLD and changes[0].now is None

    def test_filling_an_empty_slot_leaves_nothing_stale(self):
        """Nothing was there before, so no rule can be pointing at it."""
        changes = device_changes({}, {"devpath_joystick": NEW_PATH})
        assert changes[0].was is None



class TestWhenToSpeakUp:
    """Each of these rules out a different way of crying wolf."""

    def setup_method(self):
        self.change = [DeviceChange("joystick", OLD, NEW_DEVICE)]
        self.on = Settings({"enableDCS": True})

    def test_an_enabled_sim_naming_the_old_device_is_reported(self, config):
        items = pending_reconcile(self.change, self.on, [dcs_status()])
        assert [i.sim.key for i in items] == ["DCS"]

    def test_a_disabled_sim_is_left_alone(self, config):
        """However out of date its config is, the user is not using it."""
        items = pending_reconcile(self.change, Settings({"enableDCS": False}),
                                  [dcs_status()])
        assert items == []

    def test_install_state_is_not_a_gate(self, config):
        """What matters is what the file says, not which DLL sits beside it.
        A user part-way through setting up - our wrapper beside one
        executable, something else beside the other - was being skipped for
        no good reason.  Nothing but TelemFFB writes a tap rule, so the rule
        is its own guard."""
        items = pending_reconcile(self.change, self.on,
                                  [dcs_status(WrapperState.ABSENT)])
        assert [i.sim.key for i in items] == ["DCS"]

    def test_a_sim_with_no_config_is_left_alone(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [])
        assert pending_reconcile(self.change, self.on, [dcs_status()]) == []

    def test_a_config_that_never_named_the_old_device_is_left_alone(self, config):
        """The gate that matters most: swapping a device no sim was tapping
        must stay completely silent."""
        config["text"] = "[FFBDevices]\n0000:1111=tap\n"
        assert pending_reconcile(self.change, self.on, [dcs_status()]) == []

    def test_a_non_tap_rule_for_the_old_device_is_left_alone(self, config):
        """block and scale rules are the user managing their own hardware."""
        config["text"] = "[FFBDevices]\nFFFF:2054=block\n"
        assert pending_reconcile(self.change, self.on, [dcs_status()]) == []

    def test_no_change_means_no_question(self, config):
        assert pending_reconcile([], self.on, [dcs_status()]) == []

    def test_filling_a_previously_empty_slot_asks_nothing(self, config):
        """There was no old device, so nothing can be pointing at one."""
        change = [DeviceChange("joystick", None, NEW_DEVICE)]
        assert pending_reconcile(change, self.on, [dcs_status()]) == []

    def test_a_role_the_sim_never_rendered_to_cannot_go_stale(self, config):
        """No separate role check is needed: DCS was never offered pedals,
        so no pedal rule exists in its config to become stale."""
        change = [DeviceChange("pedals", (0xFFFF, 0x2052), None)]
        assert pending_reconcile(change, self.on, [dcs_status()]) == []


class TestSharedEnableKeys:
    def test_il2_korea_rides_on_the_il2_toggle(self):
        """There is one IL-2 switch in the UI, and Korea has no separate
        setting - so enabling Great Battles enables Korea here too."""
        korea = SIMS_BY_KEY["IL2_K"]
        assert sim_is_enabled(korea, Settings({"enableIL2": True}))
        assert not sim_is_enabled(korea, Settings({"enableIL2": False}))



class TestWhatItDoes:
    def setup_method(self):
        self.change = [DeviceChange("joystick", OLD, NEW_DEVICE)]
        self.on = Settings({"enableDCS": True})

    def test_the_old_rule_is_retired_and_the_new_one_added(self, config,
                                                           monkeypatch):
        written = []
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        apply_reconcile(pending_reconcile(self.change, self.on, [dcs_status()]))
        assert "044F:B10A=tap" in written[0]
        assert "; retired by TelemFFB: FFFF:2054=tap" in written[0]


    def test_clearing_a_slot_retires_the_rule_and_adds_nothing(self, config,
                                                               monkeypatch):
        """The game goes back to rendering it itself, which is the honest
        outcome when TelemFFB is no longer driving anything there."""
        written = []
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        change = [DeviceChange("joystick", OLD, None)]
        apply_reconcile(pending_reconcile(change, self.on, [dcs_status()]))
        assert read(written[0]).rules == []
        assert "retired by TelemFFB" in written[0]

    def test_the_rest_of_the_config_is_untouched(self, config, monkeypatch):
        config["text"] = ("; mine\n[FFBDevices]\nWarthog=block\n"
                          "FFFF:2054=tap\n")
        written = []
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        apply_reconcile(pending_reconcile(self.change, self.on, [dcs_status()]))
        assert "; mine" in written[0] and "Warthog=block" in written[0]


class TestSpottingDriftWithoutAChange:
    """The status panel's side of it: a config can be out of date without
    anything having just happened - the user may have swapped hardware while
    TelemFFB was closed."""

    @pytest.mark.parametrize("configured, stale", [
        ([NEW_DEVICE], 1),                                    # swapped away
        ([TapDevice("joystick", 0xFFFF, 0x2054, "old")], 0),  # still selected
        ([], 1),                                              # nothing selected
    ])
    def test_a_rule_is_stale_when_no_configured_device_matches(
            self, configured, stale):
        assert len(stale_tap_rules(read(TAPPED), configured)) == stale


    def test_a_block_rule_is_never_stale(self):
        """It is the user managing a device TelemFFB has nothing to do with."""
        facts = read("[FFBDevices]\nFFFF:2054=block\n")
        assert stale_tap_rules(facts, []) == []



class TestIdsComeFromWhereTheyAreStored:
    """device_changes resolves ids exactly as configured_devices does.

    Reading the devpath directly was a bug that made the whole feature
    inert: a DirectInput device's path is its instance GUID, so both sides
    of the comparison parsed to None, every swap looked like no change, and
    nothing was ever raised.
    """

    def test_a_directinput_swap_is_seen(self):
        changes = device_changes(
            {"devpath_joystick": "{GUID-A}", "devids_joystick": "FFFF:2054"},
            {"devpath_joystick": "{GUID-B}", "devids_joystick": "045E:001B",
             "devident_joystick": "SideWinder"})
        assert [(c.role, c.was, c.now.key) for c in changes] == \
            [("joystick", OLD, "045E:001B")]


    def test_a_hid_device_without_stored_ids_still_works(self):
        changes = device_changes(
            {"devpath_joystick": DCS_PATH},
            {"devpath_joystick": r"\?\HID#VID_045E&PID_001B&x"})
        assert changes[0].was == OLD

    def test_swapping_in_a_device_with_no_usable_ids_strands_the_old_rule(self):
        """Still a change worth raising - the old rule has to go even though
        nothing can replace it."""
        changes = device_changes(
            {"devpath_joystick": "{A}", "devids_joystick": "FFFF:2054"},
            {"devpath_joystick": "{B}", "devids_joystick": ""})
        assert changes[0].was == OLD and changes[0].now is None


class TestASimWithTwoDifferentConfigs:
    """DCS ships two executables and a user may have our wrapper beside one
    and something else beside the other, each with its own dinput8.ini.
    Collapsing them read whichever came first and, on write, copied it over
    the other."""

    OURS = "[FFBDevices]\nFFFF:2054=tap    ; Monster (joystick)\n"
    THEIRS = "[General]\nLogLevel=2\n\n[FFBDevices]\n; not ours\n"

    def status(self):
        return SimStatus(
            sim=SIMS_BY_KEY["DCS"], root=r"C:\DCS", provenance="test",
            targets=[TargetStatus(r"C:\DCS\bin", WrapperState.TAP,
                                  has_config=True),
                     TargetStatus(r"C:\DCS\bin-mt", WrapperState.FOREIGN,
                                  has_config=True)])

    @pytest.fixture
    def both(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [
            (r"C:\DCS\bin", self.OURS), (r"C:\DCS\bin-mt", self.THEIRS)])

    def test_only_the_file_that_names_the_device_is_picked_up(self, both):
        items = pending_reconcile([DeviceChange("joystick", OLD, NEW_DEVICE)],
                                  Settings({"enableDCS": True}),
                                  [self.status()])
        assert [i.directory for i in items] == [r"C:\DCS\bin"]

    def test_the_other_file_is_never_written(self, both, monkeypatch):
        """It belongs to whatever else is installed there."""
        written = []
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append((d, text)))
        apply_reconcile(pending_reconcile(
            [DeviceChange("joystick", OLD, NEW_DEVICE)],
            Settings({"enableDCS": True}), [self.status()]))
        assert [d for d, _ in written] == [r"C:\DCS\bin"]

    def test_a_config_beside_a_foreign_dll_still_counts(self, monkeypatch):
        """If it holds a tap rule, TelemFFB wrote it - whatever DLL is there
        now."""
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin-mt", self.OURS)])
        items = pending_reconcile([DeviceChange("joystick", OLD, NEW_DEVICE)],
                                  Settings({"enableDCS": True}),
                                  [self.status()])
        assert len(items) == 1


class TestRulesKeyedOnANameRatherThanIds:
    """Every config written before VID:PID matching existed keys its rules
    on a name fragment, and so does anything hand-written. Reconcile looked
    those up by ids alone, so they could never match - it reported "no rule
    for the outgoing device" while sitting on a file full of them.
    """

    NAMED = "[FFBDevices]\nMonster=tap\n"
    IDENT = "[DI] VPforce Rhino FFB Monster"

    @pytest.fixture
    def named(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", self.NAMED)])

    def change(self, ids=OLD, ident=None):
        return [DeviceChange("joystick", ids,
                             was_ident=self.IDENT if ident is None else ident,
                             now=NEW_DEVICE)]

    def test_a_name_rule_for_the_outgoing_device_is_found(self, named):
        items = pending_reconcile(self.change(), Settings({"enableDCS": True}),
                                  [dcs_status()])
        assert [r.key for r in items[0].obsolete] == ["Monster"]

    def test_the_match_is_on_the_name_the_device_had(self, named):
        """Its ids are irrelevant here - the rule never mentioned them."""
        items = pending_reconcile(self.change(ids=None),
                                  Settings({"enableDCS": True}),
                                  [dcs_status()])
        assert len(items) == 1

    def test_a_device_with_neither_ids_nor_a_name_looks_nothing_up(self, named):
        assert pending_reconcile(self.change(ids=None, ident=""),
                                 Settings({"enableDCS": True}),
                                 [dcs_status()]) == []

    def test_an_unrelated_name_does_not_match(self, named):
        assert pending_reconcile(self.change(ids=None, ident="Warthog HOTAS"),
                                 Settings({"enableDCS": True}),
                                 [dcs_status()]) == []

    def test_reconciling_moves_it_onto_ids(self, named, monkeypatch):
        """A useful side effect: the replacement is keyed on ids, so the
        config stops depending on a product name that can change."""
        written = []
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        apply_reconcile(pending_reconcile(
            self.change(), Settings({"enableDCS": True}), [dcs_status()]))
        assert [r.key for r in read(written[0]).rules] == ["044F:B10A"]
        assert "; retired by TelemFFB: Monster=tap" in written[0]


class TestADeviceThatCannotBeDrivenAtAll:
    """A generic DirectInput device is reachable only through the tap, so a
    missing rule is not a preference - it is a device that does nothing,
    with no error anywhere. A VPforce device is reachable natively, so the
    same absence is a legitimate choice."""

    DI = TapDevice("joystick", 0x045E, 0x001B, "SideWinder", directinput=True)
    VPF = TapDevice("joystick", 0xFFFF, 0x2054, "Rhino", directinput=False)
    ON = None

    def setup_method(self):
        # the tap is opt-in per sim, so a gap only exists once the user
        # has asked for the tap there
        self.ON = Settings({"enableDCS": True, "enableIL2": True,
                            "enableTapDCS": True, "enableTapIL2": True,
                            "enableTapIL2_K": True})

    @pytest.fixture
    def empty_config(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", "[FFBDevices]\n")])

    def test_a_directinput_device_with_no_rule_is_a_gap(self, empty_config):
        gaps = tap_reconcile.missing_tap_rules([self.DI], self.ON,
                                             [dcs_status()])
        assert [g.sim.key for g in gaps] == ["DCS"]


    def test_a_device_that_already_has_a_rule_is_not(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [
            (r"C:\DCS\bin", "[FFBDevices]\n045E:001B=tap\n")])
        assert tap_reconcile.missing_tap_rules([self.DI], self.ON,
                                             [dcs_status()]) == []

    def test_a_name_keyed_rule_counts_as_covered(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [
            (r"C:\DCS\bin", "[FFBDevices]\nSideWinder=tap\n")])
        assert tap_reconcile.missing_tap_rules([self.DI], self.ON,
                                             [dcs_status()]) == []

    def test_a_disabled_sim_is_not_a_gap(self, empty_config):
        assert tap_reconcile.missing_tap_rules(
            [self.DI], Settings({"enableDCS": False}), [dcs_status()]) == []

    def test_a_role_the_sim_never_renders_to_is_not_a_gap(self, empty_config):
        """DCS sends no effects to pedals, so no rule is missing."""
        pedals = TapDevice("pedals", 0x045E, 0x001B, "DI Pedals",
                           directinput=True)
        assert tap_reconcile.missing_tap_rules([pedals], self.ON,
                                             [dcs_status()]) == []

    def test_a_sim_with_no_config_is_reported_but_not_fixable(self,
                                                              monkeypatch):
        """Setting the tap up is a bigger step than adding a rule, and not
        something to do behind the user's back."""
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [])
        gap, = tap_reconcile.missing_tap_rules([self.DI], self.ON,
                                             [dcs_status()])
        assert not gap.fixable

    def test_fixing_adds_the_rule_and_keeps_the_rest(self, monkeypatch):
        written = []
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [
            (r"C:\DCS\bin", "; mine\n[FFBDevices]\nWarthog=block\n")])
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        tap_reconcile.apply_tap_rules(
            tap_reconcile.missing_tap_rules([self.DI], self.ON, [dcs_status()]))
        assert "045E:001B=tap" in written[0]
        assert "; mine" in written[0] and "Warthog=block" in written[0]

    def test_an_unfixable_gap_writes_nothing(self, monkeypatch):
        written = []
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [])
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        tap_reconcile.apply_tap_rules(
            tap_reconcile.missing_tap_rules([self.DI], self.ON, [dcs_status()]))
        assert written == []


class TestTheTapIsOptInPerSim:
    """Most VPforce owners never need the tap - it only renders the game's
    own effects, in DirectInput Tap spring mode. Presenting it as part of
    ordinary sim setup would suggest otherwise, so each sim is opted in
    separately and nothing nags until it is."""

    DI = TapDevice("joystick", 0x045E, 0x001B, "SideWinder", directinput=True)

    @pytest.fixture
    def no_rule(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", "[FFBDevices]\n")])

    def test_a_sim_not_opted_in_is_never_flagged(self, no_rule):
        settings = Settings({"enableDCS": True, "enableTapDCS": False})
        assert tap_reconcile.missing_tap_rules([self.DI], settings,
                                             [dcs_status()]) == []




    def test_an_existing_config_is_still_reconciled_when_opted_out(self,
                                                                   config):
        """Rules in a file mean the tap is in use, whatever the switch says -
        so a swap still strands them, and staying quiet would break a
        working setup."""
        settings = Settings({"enableDCS": True, "enableTapDCS": False})
        items = pending_reconcile([DeviceChange("joystick", OLD, NEW_DEVICE)],
                                  settings, [dcs_status()])
        assert [i.sim.key for i in items] == ["DCS"]


class TestTakingTheTapBackOut:
    """Opting a sim out offers to undo the tap, and the whole risk is in
    what "undo" covers. Three things are ours to different degrees, and
    getting any of them wrong deletes somebody else's work."""

    OURS = ("; dinput8.ini - written by TelemFFB on 2026-08-22.\n"
            "[General]\nRequireTelemFFB=true\n\n[FFBDevices]\n"
            "FFFF:2054=tap    ; Monster (joystick)\n")
    THEIRS = ("; tuned by hand\n[General]\nLogLevel=2\n\n[FFBDevices]\n"
              "Warthog=block\nFFFF:2054=tap\n")

    def status(self, *targets):
        return SimStatus(sim=SIMS_BY_KEY["DCS"], root=r"C:\DCS",
                         provenance="test", targets=list(targets))

    def test_a_config_we_wrote_is_deleted_outright(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", self.OURS)])
        plan = tap_reconcile.plan_tap_cleanup(
            self.status(TargetStatus(r"C:\DCS\bin", WrapperState.TAP)))
        assert plan.delete_config == [r"C:\DCS\bin"]
        assert plan.edit_config == []

    def test_somebody_elses_config_only_loses_our_rules(self, monkeypatch):
        """Their block rule and their comments are not ours to remove."""
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", self.THEIRS)])
        plan = tap_reconcile.plan_tap_cleanup(
            self.status(TargetStatus(r"C:\DCS\bin", WrapperState.TAP)))
        assert plan.delete_config == []
        assert [d for d, _, _ in plan.edit_config] == [r"C:\DCS\bin"]

    def test_editing_leaves_everything_but_the_tap_rules(self, monkeypatch):
        written = []
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", self.THEIRS)])
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        tap_reconcile.apply_tap_cleanup([tap_reconcile.plan_tap_cleanup(
            self.status(TargetStatus(r"C:\DCS\bin", WrapperState.TAP)))])
        assert "; tuned by hand" in written[0]
        assert "Warthog=block" in written[0]
        assert [r.key for r in read(written[0]).rules] == ["Warthog"]

    def test_a_dll_that_is_not_ours_is_never_removed(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [])
        plan = tap_reconcile.plan_tap_cleanup(
            self.status(TargetStatus(r"C:\DCS\bin-mt", WrapperState.FOREIGN)))
        assert plan.remove_wrapper == []
        assert plan.empty

    def test_only_our_half_of_a_mixed_install_is_taken_out(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [])
        plan = tap_reconcile.plan_tap_cleanup(self.status(
            TargetStatus(r"C:\DCS\bin", WrapperState.TAP),
            TargetStatus(r"C:\DCS\bin-mt", WrapperState.FOREIGN)))
        assert plan.remove_wrapper == [r"C:\DCS\bin"]

    def test_nothing_installed_means_nothing_to_offer(self, monkeypatch):
        """No question is asked when there is nothing to undo."""
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [])
        plan = tap_reconcile.plan_tap_cleanup(
            self.status(TargetStatus(r"C:\DCS\bin", WrapperState.ABSENT)))
        assert plan.empty

    def test_the_description_names_each_kind_of_removal(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", self.OURS)])
        plan = tap_reconcile.plan_tap_cleanup(
            self.status(TargetStatus(r"C:\DCS\bin", WrapperState.TAP)))
        said = " ".join(plan.describe())
        assert "configuration" in said and "dinput8.dll" in said

    def test_requiretelemffb_is_not_reintroduced_while_removing(self,
                                                                monkeypatch):
        """amend adds it by default; a cleanup that put it back would be
        writing a new TelemFFB setting into a file it is leaving."""
        written = []
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin",
                                        "[FFBDevices]\nFFFF:2054=tap\n")])
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        tap_reconcile.apply_tap_cleanup([tap_reconcile.plan_tap_cleanup(
            self.status(TargetStatus(r"C:\DCS\bin", WrapperState.TAP)))])
        assert "RequireTelemFFB" not in written[0]


class TestOrderingGoesStaleToo:
    """A [DeviceOrder] entry naming replaced hardware strands the new device
    exactly as a stale tap rule does - the game keeps handing its effects to
    whatever it enumerated first, and nothing reports it."""

    ORDERED = ("[FFBDevices]\n"
               "FFFF:2054=tap    ; Monster (joystick)\n"
               "\n[DeviceOrder]\n"
               "1=FFFF:2054    ; Monster (joystick)\n")

    @pytest.fixture
    def ordered(self, monkeypatch):
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", self.ORDERED)])

    def change(self):
        return [DeviceChange("joystick", OLD, NEW_DEVICE)]

    def test_a_stale_order_entry_is_found(self, ordered):
        item, = pending_reconcile(self.change(), Settings({"enableDCS": True}),
                                  [dcs_status()])
        assert [e.match for e in item.obsolete_order] == ["FFFF:2054"]

    def test_it_is_retired_and_replaced(self, ordered, monkeypatch):
        written = []
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        apply_reconcile(pending_reconcile(
            self.change(), Settings({"enableDCS": True}), [dcs_status()]))
        facts = read(written[0])
        assert [e.match for e in facts.order] == ["044F:B10A"]
        assert "; retired by TelemFFB: 1=FFFF:2054" in written[0]

    def test_the_position_is_kept(self, ordered, monkeypatch):
        """Promoting the replacement to a different slot would change which
        device the game drives - the thing ordering exists to control."""
        written = []
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        apply_reconcile(pending_reconcile(
            self.change(), Settings({"enableDCS": True}), [dcs_status()]))
        assert read(written[0]).order[0].position == "1"

    def test_clearing_the_slot_retires_it_with_no_replacement(self, ordered,
                                                              monkeypatch):
        written = []
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        apply_reconcile(pending_reconcile(
            [DeviceChange("joystick", OLD, None)],
            Settings({"enableDCS": True}), [dcs_status()]))
        assert read(written[0]).order == []

    def test_an_order_entry_alone_is_enough_to_reconcile(self, monkeypatch):
        """Even with no tap rule left to fix - the ordering is what decides
        whether the game drives the device at all."""
        monkeypatch.setattr(tap_reconcile, 'read_configs', lambda s: [
            (r"C:\DCS\bin", "[DeviceOrder]\n1=FFFF:2054\n")])
        items = pending_reconcile(self.change(), Settings({"enableDCS": True}),
                                  [dcs_status()])
        assert len(items) == 1 and items[0].obsolete == []




class TestWhatALeftoverConfigKeepsDoing:
    """The removal prompt used to say leaving it was harmless, full stop.
    RequireTelemFFB gates only tap and sink; block and scale rules apply
    whatever TelemFFB is doing, and so does any reordering - so that was
    true only for a config we generated ourselves."""

    def plan_for(self, monkeypatch, config):
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", config)])
        return tap_reconcile.plan_tap_cleanup(SimStatus(
            sim=SIMS_BY_KEY["DCS"], root=r"C:\DCS", provenance="t",
            targets=[TargetStatus(r"C:\DCS\bin", WrapperState.TAP)]))

    def test_our_own_config_really_is_inert(self, monkeypatch):
        plan = self.plan_for(monkeypatch,
                             "; written by TelemFFB\n[General]\n"
                             "RequireTelemFFB=true\n\n[FFBDevices]\n"
                             "FFFF:2054=tap\n")
        assert plan.still_acts == []

    def test_require_false_means_the_tap_applies_regardless(self, monkeypatch):
        """The case that made the old wording a lie: with this false the
        device is tapped whether TelemFFB is there to render or not."""
        plan = self.plan_for(monkeypatch,
                             "[General]\nRequireTelemFFB=false\n\n"
                             "[FFBDevices]\nFFFF:2054=tap\n")
        assert any("RequireTelemFFB is false" in r for r in plan.still_acts)

    def test_block_rules_are_never_gated(self, monkeypatch):
        plan = self.plan_for(monkeypatch, "[FFBDevices]\nvJoy=block\n")
        assert any("block or scale rules, which apply whether or not"
                   in r for r in plan.still_acts)

    def test_our_ordering_is_taken_out_rather_than_warned_about(
            self, monkeypatch):
        """We write [DeviceOrder] ourselves now, so leaving it behind would
        contradict the rule the rest of cleanup follows."""
        plan = self.plan_for(monkeypatch,
                             "[DeviceOrder]\n1=FFFF:2054\n")
        assert plan.still_acts == []
        assert [lines for _, _, lines in plan.edit_config] == [[1]]

    def test_require_false_without_tap_rules_is_not_flagged(self, monkeypatch):
        """Nothing to apply regardless, so the setting is moot."""
        plan = self.plan_for(monkeypatch,
                             "[General]\nRequireTelemFFB=false\n\n"
                             "[FFBDevices]\n")
        assert plan.still_acts == []


class TestCleanupTakesOnlyWhatWeWrote:
    """The ownership rule, now that [DeviceOrder] is something we write:
    our tap rules and our ordering come out, everything else stays."""

    ADOPTED = ("; tuned by hand\n"
               "[FFBDevices]\n"
               "Warthog=block\n"
               "FFFF:2054=tap\n"
               "\n[DeviceOrder]\n"
               "1=FFFF:2054\n")

    def apply(self, monkeypatch, config):
        written = []
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", config)])
        monkeypatch.setattr(tap_reconcile, 'write_one_config',
                            lambda d, text: written.append(text))
        tap_reconcile.apply_tap_cleanup([tap_reconcile.plan_tap_cleanup(SimStatus(
            sim=SIMS_BY_KEY["DCS"], root=r"C:\DCS", provenance="t",
            targets=[TargetStatus(r"C:\DCS\bin", WrapperState.TAP)]))])
        return written[0]

    def test_their_rules_and_comments_survive(self, monkeypatch):
        out = self.apply(monkeypatch, self.ADOPTED)
        assert "; tuned by hand" in out
        assert [r.key for r in read(out).rules] == ["Warthog"]

    def test_our_tap_rule_and_ordering_both_go(self, monkeypatch):
        out = self.apply(monkeypatch, self.ADOPTED)
        assert read(out).order == []
        assert not any(r.is_tap for r in read(out).rules)

    def test_the_preview_is_the_edit_that_would_be_made(self, monkeypatch):
        """Not a second description of it.  A preview computed one way and
        a write computed another would drift, and the user would agree to
        one thing and get the other."""
        monkeypatch.setattr(tap_reconcile, 'read_configs',
                            lambda s: [(r"C:\DCS\bin", self.ADOPTED)])
        status = SimStatus(sim=SIMS_BY_KEY["DCS"], root=r"C:\DCS",
                           provenance="t",
                           targets=[TargetStatus(r"C:\DCS\bin", WrapperState.TAP)])
        plan = tap_reconcile.plan_tap_cleanup(status)
        (heading, current, proposed), = tap_reconcile.cleanup_preview(plan)
        assert heading == r"bin\dinput8.ini"
        assert current == self.ADOPTED
        assert proposed == self.apply(monkeypatch, self.ADOPTED)
