#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""Keeping a sim's ``dinput8.ini`` in step with the devices TelemFFB is
configured for.

Three questions, each answered here and asked of the user by the settings
dialog:

* **reconcile** - a slot changed hardware.  Which configs still name the
  device that left, and what should they say instead?
* **gaps** - a DirectInput device has no tap rule in a sim that needs one,
  and so cannot be driven at all.  Where could one be added?
* **cleanup** - a sim was opted back out of the tap.  What of ours is there
  to take out, and what would a config left behind keep doing?

Nothing here writes until told to.  Each question comes back as a plan - a
``ReconcileItem``, a ``TapGap``, a ``TapCleanup`` - and a separate
``apply_*`` carries it out, so the dialog can ask at the moment of the change
and act at save, and a user who backs out leaves the game folders exactly as
they were.

What a config *says* is read through ``tap_config``; where it lives, and
which sims exist, through ``tap_install``.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from telemffb.tap_config import (OrderEntry, Rule, amend, order_matches,
                                 read, rule_matches, stale_tap_rules)
from telemffb.tap_install import (GENERATED_MARKER, SIMS, WRAPPER_CONFIG,
                                  SimStatus, TapDevice, TapSim, TargetOutcome,
                                  WrapperState, open_config, config_label,
                                  configured_devices, order_line, read_config,
                                  read_configs, remove, rule_line, sim_status,
                                  write_one_config)
from telemffb.utils import DEVICE_ROLES


@dataclass(frozen=True)
class DeviceChange:
    """A slot that now holds different hardware than it did."""
    role: str
    was: Optional[Tuple[int, int]]      # the ids that left the slot
    now: Optional[TapDevice] = None     # None when the slot was cleared
    #: What the departing device was called.  Carried because a rule may be
    #: keyed on a name rather than on ids - every config written before
    #: VID:PID matching existed is, and so is anything hand-written - and
    #: matching one needs the name the rule was written against.
    was_ident: str = ""


@dataclass
class ReconcileItem:
    """One config file that still names a device the user has replaced.

    Per file rather than per sim: a sim can hold two, and they need not
    agree.  Writing one sim-wide answer would copy one over the other.
    """
    status: SimStatus
    directory: str
    config: str
    obsolete: List[Rule]                # rules naming the old device
    replacement: Optional[str] = None   # the rule to add, if there is one
    #: The change this answers: which slot, and what the user called the
    #: device that left - so the question can say "the joystick changed
    #: from Monster to SideWinder" rather than list rule keys.
    role: str = ""
    was_ident: str = ""
    #: [DeviceOrder] entries naming the old device, and what replaces them.
    #: An order entry left pointing at replaced hardware strands the new
    #: device exactly as a stale tap rule does - the game keeps handing its
    #: effects to whatever it enumerated first.
    obsolete_order: List["OrderEntry"] = field(default_factory=list)
    order_replacement: Optional[str] = None

    @property
    def sim(self):
        return self.status.sim


def sim_is_enabled(sim: TapSim, settings) -> bool:
    """Whether TelemFFB is set to drive this sim at all.

    A sim the user has switched off is not something to interrupt them
    about, however out of date its config may be.
    """
    if not sim.enable_key:
        return True
    return bool(settings.get(sim.enable_key, False))


def tap_is_enabled(sim: TapSim, settings) -> bool:
    """Whether the user has opted this sim into the tap."""
    if not sim.tap_enable_key:
        return True
    return bool(settings.get(sim.tap_enable_key, False))


def pending_reconcile(changes: Sequence[DeviceChange], settings,
                      statuses: Optional[Sequence[SimStatus]] = None
                      ) -> List[ReconcileItem]:
    """Sims that need their config updated after a device swap.

    Deliberately narrow.  A prompt is worth showing only when both of these
    hold, and each rules out a different way of crying wolf:

    * the sim is enabled in TelemFFB;
    * one of its configs has a *tap* rule naming the device that left.

    Install state is deliberately not checked.  A stale rule matters because
    of what the file says, not because of which DLL happens to sit beside
    it, and a user part-way through setting things up - our wrapper beside
    one executable, something else beside the other - would otherwise be
    skipped for no good reason.  The tap rule is its own guard: nothing but
    TelemFFB writes one.

    The role never has to be checked separately either: a sim that does not
    render to a role was never offered it, so no rule for it exists to go
    stale.
    """
    # a name is enough on its own: a device whose ids were never recorded
    # can still be named by a rule
    relevant = [c for c in changes if c.was or c.was_ident]
    if not relevant:
        logging.info("DirectInput tap: nothing is known about the outgoing device - "
                     "no rule can be looked up")
        return []

    items = []
    for status in (statuses if statuses is not None else all_status(settings)):
        name = status.sim.name
        if not sim_is_enabled(status.sim, settings):
            logging.info(f"DirectInput tap: {name} - not enabled, skipped")
            continue

        configs = read_configs(status)
        if not configs:
            logging.info(f"DirectInput tap: {name} - no dinput8.ini found, skipped")
            continue

        for directory, config in configs:
            facts = read(config)
            tapped = [r.key for r in facts.rules if r.is_tap]
            logging.info(f"DirectInput tap: {name} [{os.path.basename(directory)}] - "
                         f"tap rules present: {tapped or 'none'}")
            for change in relevant:
                obsolete = [rule for rule in facts.rules
                            if rule.is_tap and rule_matches(
                                rule, change.was, change.was_ident)]
                stale_order = [entry for entry in facts.order
                               if order_matches(entry, change.was,
                                                change.was_ident)]
                if not obsolete and not stale_order:
                    continue
                items.append(ReconcileItem(
                    status=status, directory=directory, config=config,
                    obsolete=obsolete, obsolete_order=stale_order,
                    role=change.role, was_ident=change.was_ident,
                    order_replacement=(
                        order_line(change.now, int(stale_order[0].position)
                                   if stale_order[0].position.isdigit() else 1)
                        if stale_order and change.now is not None
                        and change.now.usable else None),
                    replacement=(rule_line(change.now)
                                 if change.now is not None
                                 and change.now.usable else None)))
    return items


def device_changes(before: dict, after: dict) -> List[DeviceChange]:
    """Which slots changed hardware, given the settings before and after.

    Both sides go through configured_devices so the ids are resolved exactly
    as they are everywhere else.  Reading the path directly here was a bug:
    a DirectInput device's path is its instance GUID, so both sides parsed
    to None, every swap looked like no change, and nothing was ever raised.

    Compared by ids rather than by path for a second reason too - Windows
    paths carry instance data that can differ between enumerations for what
    is plainly the same stick, and rewriting configs over that would be
    noise.
    """
    was = {d.role: d for d in configured_devices(before)}
    now = {d.role: d for d in configured_devices(after)}

    changes = []
    for role in DEVICE_ROLES:
        old, new = was.get(role), now.get(role)
        old_ids = (old.vid, old.pid) if old and old.usable else None
        new_ids = (new.vid, new.pid) if new and new.usable else None
        if old_ids == new_ids:
            continue
        changes.append(DeviceChange(
            role=role, was=old_ids,
            was_ident=old.ident if old is not None else "",
            now=new if new is not None and new.usable else None))
    return changes


@dataclass
class TapGap:
    """A DirectInput device a sim cannot render anything for.

    Distinct from a stale rule: nothing is wrong with the config, there is
    simply no rule where one is mandatory.  TelemFFB reaches a generic
    DirectInput device only through the tap, so without a rule the game
    keeps the device to itself and TelemFFB is silent - with nothing
    anywhere reporting a fault.
    """
    status: SimStatus
    device: TapDevice
    #: Where a rule could be added, when the sim already has a config.  None
    #: means the tap is not set up here at all, which is not something to
    #: fix behind the user's back.
    directory: Optional[str] = None
    config: Optional[str] = None

    @property
    def sim(self):
        return self.status.sim

    @property
    def fixable(self) -> bool:
        return self.config is not None


def missing_tap_rules(devices: Sequence[TapDevice], settings,
                      statuses: Optional[Sequence[SimStatus]] = None
                      ) -> List[TapGap]:
    """DirectInput devices with no tap rule in a sim that needs one.

    Only DirectInput devices.  A VPforce device works without the tap - it
    is only needed there to render the game's own effects in the Game
    Managed (DirectInput Tap) spring mode - so its absence is a choice,
    not a gap.

    Only sims that are enabled and that render to the device's role, for the
    same reason as everywhere else: a warning about a sim the user is not
    flying, or a control the sim never sends effects to, is noise.

    A rule is only offered where *our* wrapper is the one that will read
    it.  A config beside a dinput8.dll that is not ours - the legacy ffb-fix
    wrapper, typically - would take the line and do nothing with it, and
    the prompt would have claimed a fix it did not make.  Until the tap is
    installed there, the gap is reported as one the sim's tab has to close.

    A sim whose tap opt-in is off still reports its gaps - off is also the
    DEFAULT, and a fresh setup was never asked, while every device here is
    one the tap is mandatory for.  What the opt-in gates is the fix: those
    gaps are never fixable, because writing rules for a sim whose tap the
    user has not opted into is acting behind their back.  The notice sends
    them to the sim's tab instead.
    """
    wanted = [d for d in devices if d.directinput and d.usable]
    if not wanted:
        return []

    gaps = []
    for status in (statuses if statuses is not None else all_status(settings)):
        if not sim_is_enabled(status.sim, settings):
            continue
        # the opt-in gates the fix, not the report (see the docstring); a
        # config that already exists still counts as coverage, since rules
        # in a file mean the tap is in use whatever the toggle says
        opted_in = tap_is_enabled(status.sim, settings)
        configs = read_configs(status)
        ours = {t.directory for t in status.targets
                if t.state == WrapperState.TAP}
        for device in wanted:
            if not status.sim.renders_to(device.role):
                continue
            covered = False
            spare = None
            for directory, config in configs:
                facts = read(config)
                if any(r.is_tap and rule_matches(r, (device.vid, device.pid),
                                                 device.ident)
                       for r in facts.rules):
                    covered = True
                    break
                if opted_in and spare is None and directory in ours:
                    spare = (directory, config)
            if covered:
                continue
            gaps.append(TapGap(status=status, device=device,
                               directory=spare[0] if spare else None,
                               config=spare[1] if spare else None))
    return gaps


def apply_tap_rules(gaps: Sequence[TapGap]) -> List[TargetOutcome]:
    """Add the missing rule to every gap that has somewhere to put one."""
    outcomes = []
    for gap in gaps:
        if not gap.fixable:
            continue
        outcomes.append(write_one_config(
            gap.directory, amend(gap.config, [rule_line(gap.device)])))
    return outcomes


@dataclass
class TapCleanup:
    """What opting a sim back out of the tap would take out of it.

    Three separate things, deliberately kept apart, because they are ours to
    different degrees.  A config we generated outright is ours to delete.  A
    config that was somebody's before we touched it is theirs, so only the
    rules we added come out.  A dinput8.dll is only ever removed when it is
    the one we put there.
    """
    status: SimStatus
    #: Directories whose dinput8.ini we wrote from nothing.
    delete_config: List[str] = field(default_factory=list)
    #: (directory, text, lines) - our lines inside somebody else's file.
    #: Tap rules and, since we write those too, any [DeviceOrder] entries:
    #: leaving behind a section we added would contradict the rule the rest
    #: of this follows.
    edit_config: List[Tuple[str, str, List[int]]] = field(default_factory=list)
    #: Directories holding our wrapper.
    remove_wrapper: List[str] = field(default_factory=list)
    #: What a config left behind would go on doing.  Empty means leaving it
    #: really is inert; anything here means telling the user otherwise would
    #: be a comfortable lie.
    still_acts: List[str] = field(default_factory=list)

    @property
    def sim(self):
        return self.status.sim

    @property
    def empty(self) -> bool:
        return not (self.delete_config or self.edit_config
                    or self.remove_wrapper)

    def describe(self) -> List[str]:
        """What would happen, in the user's terms."""
        lines = []
        if self.delete_config:
            lines.append("remove the tap configuration")
        if self.edit_config:
            lines.append("remove TelemFFB's tap rules and device ordering "
                         "from dinput8.ini, leaving the rest of the file alone")
        if self.remove_wrapper:
            lines.append("remove the tap wrapper (dinput8.dll)")
        return lines


def plan_tap_cleanup(status: SimStatus) -> TapCleanup:
    """What there is to undo for one sim."""
    plan = TapCleanup(status=status)

    reasons = set()
    for directory, config in read_configs(status):
        facts = read(config)

        # What this file would keep doing if it stayed.  requireTelemFFB
        # gates only tap and sink; block and scale rules apply whatever
        # TelemFFB is doing, which is the half of this that is easy to
        # forget when telling someone it is harmless to leave.
        tapping = [r for r in facts.rules if r.is_tap]
        if tapping and not facts.require_telemffb:
            reasons.add("RequireTelemFFB is false in this config, so its tap "
                        "rules apply whether or not TelemFFB is running")
        if any(r.value in ("block", "sink") or r.value.isdigit()
               for r in facts.rules):
            # Whoever wrote them - the user, the legacy wrapper's sample,
            # or our own block lines for roles the sim does not drive -
            # they are not gated and are not taken out by a cleanup, so a
            # file left in place goes on applying them.
            reasons.add("it also has block or scale rules, which apply "
                        "whether or not TelemFFB is running")

        if GENERATED_MARKER in config:
            plan.delete_config.append(directory)
            continue
        # Ours to take back out: amend only ever writes [DeviceOrder] into
        # a file that had none, so where one is present alongside our rules
        # it is a section we added.
        ours = [r.line for r in tapping] + [e.line for e in facts.order]
        if ours:
            plan.edit_config.append((directory, config, sorted(ours)))
    plan.still_acts = sorted(reasons)

    for target in status.targets:
        if target.state == WrapperState.TAP:
            plan.remove_wrapper.append(target.directory)

    return plan


def cleanup_preview(plan: TapCleanup) -> List[Tuple[str, str, str]]:
    """(heading, current, proposed) for each file the cleanup would touch.

    Computed the same way apply_tap_cleanup computes what it writes, so the
    comparison shown is the change that would actually be made rather than a
    second description of it.
    """
    panes = []
    for directory, config, lines in plan.edit_config:
        panes.append((config_label(os.path.join(directory, WRAPPER_CONFIG),
                                   plan.status.root)[len("open "):],
                      config, amend(config, [], disable_lines=lines,
                                    ensure_require=False)))
    for directory in plan.delete_config:
        path = os.path.join(directory, WRAPPER_CONFIG)
        try:
            with open_config(path, "r") as handle:
                current = handle.read()
        except OSError:
            current = ""
        panes.append((config_label(path, plan.status.root)[len("open "):] +
                      "  (deleted)", current, ""))
    return panes


def apply_tap_cleanup(plans: Sequence[TapCleanup]) -> List[TargetOutcome]:
    """Undo the tap for these sims, touching only what is ours."""
    outcomes = []
    for plan in plans:
        for directory, config, lines in plan.edit_config:
            outcomes.append(write_one_config(
                directory,
                amend(config, [], disable_lines=lines,
                      ensure_require=False)))

        for directory in plan.delete_config:
            path = os.path.join(directory, WRAPPER_CONFIG)
            try:
                os.remove(path)
                outcomes.append(TargetOutcome(directory, True, "removed"))
            except OSError as e:
                outcomes.append(TargetOutcome(directory, False, "failed",
                                              str(e)))

        if plan.remove_wrapper:
            outcomes.extend(remove(plan.status))
    return outcomes


def apply_reconcile(items: Sequence[ReconcileItem]) -> List[TargetOutcome]:
    """Rewrite each sim's config so it names the device now in the slot."""
    outcomes = []
    for item in items:
        retire = [r.line for r in item.obsolete]
        retire += [e.line for e in item.obsolete_order]
        text = amend(item.config,
                     [item.replacement] if item.replacement else [],
                     disable_lines=retire,
                     order=([item.order_replacement]
                            if item.order_replacement else ()),
                     order_even_if_present=bool(item.obsolete_order))
        # only the file the rules came from: the other executable may hold a
        # different config that nothing asked us to touch
        outcomes.append(write_one_config(item.directory, text))
    return outcomes


def all_status(settings=None) -> List[SimStatus]:
    """Every sim's status, using the paths TelemFFB already has configured."""
    devices = configured_devices(settings) if settings is not None else []
    out = []
    for sim in SIMS:
        configured = None
        if settings is not None and sim.settings_key:
            configured = settings.get(sim.settings_key, "") or None
        status = sim_status(sim, configured)
        if settings is not None and any(t.has_config for t in status.targets):
            config = read_config(status)
            if config is not None:
                status.stale_rules = stale_tap_rules(read(config), devices)
        out.append(status)
    return out
