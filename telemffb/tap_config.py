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

"""Reading and amending a ``dinput8.ini``.

Text in, text out.  No Qt, no settings, no filesystem - so what this does can
be read and tested on its own, and every decision that needs a human stays in
the caller, where the human is.

The governing rule is **never regenerate a file we did not write; only add to
it**.  Plenty of users already run walmis's ffb-fix with a config they tuned
by hand, and our wrapper reads that file correctly as it stands: the format is
the same one, and ``block``/``allow``/scale still mean what they always did.
So adopting such a file is a few inserted lines rather than a migration, and
everything we did not touch survives byte for byte.

The one hazard is ordering.  The wrapper takes the **first** matching rule, so
a pre-existing ``Rhino=block`` above a rule we append wins, and TelemFFB looks
broken for a reason nothing reports.  This module finds those rules; it does
not resolve them.  Which one the user meant is not something to infer.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

#: A ``VVVV:PPPP`` key.  Exactly four hex digits each - a shorter key is a
#: device name that happens to contain a colon, which is how the wrapper
#: reads it too.
_IDS_KEY = re.compile(r"^([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})$")

#: Byte order mark.  Some editors prefix one to a UTF-8 file; it is not text,
#: and a parser that treats it as text stops recognizing the first section.
BOM = "﻿"


@dataclass(frozen=True)
class Rule:
    """One ``key=value`` line under ``[FFBDevices]``."""
    key: str                                  # as written
    value: str                                # lowercased, comment stripped
    line: int                                 # index into the file's lines
    ids: Optional[Tuple[int, int]] = None     # set when the key is VID:PID
    #: Whatever followed a ``;`` on the line.  Never used for matching - it
    #: is a label, and may name hardware that has since been replaced - but
    #: it is the only human-readable thing we have for a rule whose device
    #: is no longer connected.
    comment: str = ""

    @property
    def is_tap(self) -> bool:
        return self.value == "tap"


@dataclass(frozen=True)
class OrderEntry:
    """One ``position=device`` line under ``[DeviceOrder]``.

    The mirror image of a rule: here the key is the position and the value
    names the device, so the two cannot share a type without one of them
    reading backwards.
    """
    position: str                             # as written
    match: str                                # device name or VVVV:PPPP
    line: int
    ids: Optional[Tuple[int, int]] = None
    comment: str = ""


@dataclass
class ConfigFacts:
    """What a config file contains, in the terms this program cares about."""
    rules: List[Rule] = field(default_factory=list)
    #: Line index of each section header, when present.
    devices_header: Optional[int] = None
    general_header: Optional[int] = None
    #: Line index of a RequireTelemFFB setting, when present, and what it
    #: says.  The value matters: with it false, tap and sink rules apply
    #: whether or not TelemFFB is running, so leaving such a config behind
    #: silences the device rather than doing nothing.
    require_line: Optional[int] = None
    require_telemffb: bool = True       # the wrapper's own default
    #: Line index the last ``[FFBDevices]`` entry sits on - where an appended
    #: rule belongs, so it lands inside the section rather than after it.
    devices_end: Optional[int] = None
    #: ``[DeviceOrder]`` entries, and where that section sits.  Ordering
    #: decides which device a game drives at all, so an entry naming
    #: hardware that has been replaced strands the device just as a stale
    #: tap rule does.
    order: List[OrderEntry] = field(default_factory=list)
    order_header: Optional[int] = None
    order_end: Optional[int] = None


def _strip_comment(value: str) -> str:
    """Drop a trailing ``;`` or ``#`` comment."""
    for marker in (";", "#"):
        cut = value.find(marker)
        if cut != -1:
            value = value[:cut]
    return value.strip()


def read(text: str) -> ConfigFacts:
    """Parse a config the way the wrapper parses it.

    Deliberately the same shape as ``Config::load``: sections are tracked as
    the file is walked, unknown keys are ignored, and a leading byte order
    mark is skipped.  Diverging here would mean reporting one thing while the
    game does another.
    """
    facts = ConfigFacts()
    section = ""
    lines = text.splitlines()

    for index, raw in enumerate(lines):
        line = raw.lstrip(BOM).strip() if index == 0 else raw.strip()
        if not line:
            continue
        if line[0] in ";#":
            # A comment still belongs to the section it sits in, and an
            # appended line belongs after it - otherwise a rule lands above
            # the comments explaining the syntax, which is what happens to
            # our own generated file the first time it is amended.
            if section == "ffbdevices":
                facts.devices_end = index
            elif section == "deviceorder":
                facts.order_end = index
            continue

        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section == "ffbdevices" and facts.devices_header is None:
                facts.devices_header = index
                facts.devices_end = index
            elif section == "general" and facts.general_header is None:
                facts.general_header = index
            elif section == "deviceorder" and facts.order_header is None:
                facts.order_header = index
                facts.order_end = index
            continue

        if "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        # kept in both forms: a rule's value is a keyword and reads better
        # lowercased, but a [DeviceOrder] value is a device name the user
        # typed, and rewriting it in lower case would be a change nobody
        # asked for
        spoken = _strip_comment(raw_value)
        value = spoken.lower()
        comment = ""
        for marker in (";", "#"):
            if marker in raw_value:
                comment = raw_value.split(marker, 1)[1].strip()
                break

        if section == "general":
            if key.lower() == "requiretelemffb" and facts.require_line is None:
                facts.require_line = index
                facts.require_telemffb = value not in ("false", "0")
        elif section == "deviceorder":
            match = _IDS_KEY.match(spoken)
            facts.order.append(OrderEntry(
                position=key, match=spoken, line=index, comment=comment,
                ids=((int(match.group(1), 16), int(match.group(2), 16))
                     if match else None)))
            facts.order_end = index
        elif section == "ffbdevices":
            match = _IDS_KEY.match(key)
            facts.rules.append(Rule(
                key=key, value=value, line=index, comment=comment,
                ids=((int(match.group(1), 16), int(match.group(2), 16))
                     if match else None)))
            facts.devices_end = index

    return facts


#: Every VPforce device's DirectInput product string carries this prefix
#: ('Rhino FFB Monster'); TelemFFB's stored ident strips it ('Monster' -
#: DeviceInfo.ident).  The wrapper matches name fragments against the
#: FULL DirectInput string, so matching here must answer for both forms,
#: or a config written against what enumerators show ('Rhino FFB
#: Monster=tap', '1=Rhino FFB Monster') reads as covering nothing and
#: gets a duplicate line appended beside it.
VPFORCE_VID = 0xFFFF
VPFORCE_NAME_PREFIX = "Rhino FFB "


def _names_the_wrapper_sees(ids: Optional[Tuple[int, int]],
                            name: str = "") -> Tuple[str, ...]:
    """The product names the wrapper could match this device under."""
    if not name:
        return ()
    lowered = name.lower()
    if (ids is not None and ids[0] == VPFORCE_VID
            and not lowered.startswith(VPFORCE_NAME_PREFIX.lower())):
        return (lowered, VPFORCE_NAME_PREFIX.lower() + lowered)
    return (lowered,)


def rule_matches(rule: Rule, ids: Optional[Tuple[int, int]],
                 name: str = "") -> bool:
    """Whether the wrapper would apply this rule to this device.

    Mirrors ``Config::getDevicePolicy``: an id rule matches ids exactly, and a
    name rule matches when its key appears anywhere in the product name,
    case-insensitively - the full DirectInput name, which for a VPforce
    device is the stored ident plus its 'Rhino FFB ' prefix.

    The name we hold is the one TelemFFB remembers, which is not guaranteed to
    be the string the game sees - so a name match here is a strong reason to
    ask the user, and never a reason to act without them.
    """
    if rule.ids is not None:
        return ids is not None and rule.ids == ids
    return bool(rule.key) and any(
        rule.key.lower() in seen
        for seen in _names_the_wrapper_sees(ids, name))


def order_matches(entry: OrderEntry, ids: Optional[Tuple[int, int]],
                  name: str = "") -> bool:
    """Whether this ordering entry names the given device.

    Same matching as a rule - ids exactly, or a name fragment anywhere in
    the product name (including the VPforce 'Rhino FFB ' prefix the stored
    ident strips) - because the wrapper reads both the same way.
    """
    if entry.ids is not None:
        return ids is not None and entry.ids == ids
    return bool(entry.match) and any(
        entry.match.lower() in seen
        for seen in _names_the_wrapper_sees(ids, name))


def shadowing_rules(facts: ConfigFacts, ids: Optional[Tuple[int, int]],
                    name: str = "") -> List[Rule]:
    """Existing rules that would take precedence over an appended one.

    Every match qualifies, not just conflicting ones: appending puts our rule
    last, so anything already matching this device beats it - including
    another ``tap`` rule, which is harmless but makes the line we would add
    dead weight.
    """
    return [rule for rule in facts.rules if rule_matches(rule, ids, name)]


def stale_tap_rules(facts: ConfigFacts, devices: Sequence) -> List[Rule]:
    """Tap rules matching none of the devices currently configured.

    What is left behind when hardware is swapped: the game still hands the
    old device to TelemFFB, TelemFFB has never heard of it, and the new stick
    keeps the game's own force feedback.  Nothing reports any of that.

    Only tap rules.  A block or scale rule for something not configured is
    the user managing a device TelemFFB has nothing to do with, which is
    theirs to keep.
    """
    return [rule for rule in facts.rules
            if rule.is_tap and not any(
                rule_matches(rule, (d.vid, d.pid), d.ident) for d in devices)]


def already_tapped(text: str, ids, name: str = "",
                   ignoring: Sequence[int] = ()) -> bool:
    """Whether this file already hands that device over.

    ``ignoring`` are lines about to be retired, which do not count - a rule
    being replaced is not one that makes its replacement redundant.

    Without this a device that is already tapped gets a second, identical
    rule appended every time the dialog is confirmed.
    """
    skip = set(ignoring)
    return any(rule.is_tap and rule.line not in skip
               and rule_matches(rule, ids, name)
               for rule in read(text).rules)


def already_blocked(text: str, ids, name: str = "",
                    ignoring: Sequence[int] = ()) -> bool:
    """Whether this file already keeps force feedback away from a device."""
    skip = set(ignoring)
    return any(rule.line not in skip and rule.value == "block"
               and rule_matches(rule, ids, name)
               for rule in read(text).rules)


def blocking_rules(facts: ConfigFacts, ids, name: str = "") -> List[Rule]:
    """The block rules naming that device, so they can be taken back out."""
    return [rule for rule in facts.rules
            if rule.value == "block" and rule_matches(rule, ids, name)]


def already_ordered(text: str, ids, name: str = "",
                    ignoring: Sequence[int] = ()) -> bool:
    """Whether this file already reports that device first."""
    skip = set(ignoring)
    return any(entry.line not in skip and order_matches(entry, ids, name)
               for entry in read(text).order)


def retired_identities(text: str, lines: Sequence[int]):
    """What those line numbers name, rather than where they sit.

    Line indices only mean anything in the file they came from, and a sim
    can hold two configs that differ.  Naming the rules instead lets the
    same decision be applied to a second file honestly - and lets it apply
    to nothing at all where that file never had the rule.
    """
    facts = read(text)
    wanted = set(lines)
    return ({rule.key.lower() for rule in facts.rules if rule.line in wanted},
            {entry.match.lower() for entry in facts.order
             if entry.line in wanted})


def lines_for(text: str, rule_keys, order_matches) -> List[int]:
    """Where those names sit in this file, if they are here at all."""
    facts = read(text)
    return sorted(
        [rule.line for rule in facts.rules if rule.key.lower() in rule_keys] +
        [entry.line for entry in facts.order
         if entry.match.lower() in order_matches])


#: How a retired line is marked.  Commented rather than deleted, so the
#: change is visible in the file and can be undone in an editor.
RETIRED_PREFIX = "; retired by TelemFFB: "


def _payload(line: str):
    """``(key, value)`` of a rule or order line, lowercased, comment dropped -
    retired or not.  What makes two lines "the same line" for the purpose
    of restoring one or not retiring it twice."""
    body = line.strip()
    if body.startswith(RETIRED_PREFIX):
        body = body[len(RETIRED_PREFIX):]
    key, sep, rest = body.partition("=")
    if not sep:
        return None
    return key.strip().lower(), _strip_comment(rest).lower()


def _restore_retired(lines: List[str], wanted: Sequence[str]) -> List[str]:
    """Put back, in place, any of ``wanted`` that the file holds retired.

    Returns the ones it did not find, which still need appending.  Flipping
    between two devices used to retire one line and append another every
    time; restoring the retired twin instead keeps the file at one active
    and one retired line however often the user changes their mind.
    """
    remaining = []
    for new in wanted:
        payload = _payload(new)
        spot = next((i for i, line in enumerate(lines)
                     if line.strip().startswith(RETIRED_PREFIX)
                     and _payload(line) == payload), None)
        if spot is None:
            remaining.append(new)
        else:
            lines[spot] = new        # the new text: the name may have changed
    return remaining


def _eol(text: str) -> str:
    """The file's own line ending, so amending it does not churn every line."""
    return "\r\n" if "\r\n" in text else ("\n" if "\n" in text else "\r\n")


def amend(text: str, rules: Sequence[str],
          disable_lines: Sequence[int] = (),
          ensure_require: bool = True,
          order: Sequence[str] = ()) -> str:
    """Add rules to an existing config, leaving the rest of it alone -
    except [DeviceOrder], which is ours wholesale.

    ``rules`` are finished ``key=value`` lines.  ``order`` are finished
    ``position=device`` lines for ``[DeviceOrder]``.  Unlike the rules,
    a non-empty ``order`` REPLACES every entry the section holds:
    TelemFFB introduced the section (it exists in no hand-written
    config that predates the tap) and its policy is a single entry -
    the joystick device at position 1 - so an existing entry is never a
    user's answer to preserve, only a previous device selection to
    supersede.  An empty ``order`` leaves the section alone.

    ``ensure_require`` adds ``RequireTelemFFB=true`` when the file has no
    such setting - off only when taking our lines back out, where adding a
    setting would contradict the removal.  An existing value, true or
    false, is never changed.

    ``disable_lines`` are line indices to comment out, for a rule the user
    chose to retire in favor of ours - commented rather than deleted, so the
    change is visible in the file and can be undone in an editor.  Bounded:
    a line whose retired twin is already in the file is dropped rather than
    retired a second time, and a line being added that has a retired twin
    is restored in place rather than appended - so changing one's mind back
    and forth leaves one active and one retired line, not a graveyard.

    Anything not named here is untouched: comments, spacing, unknown keys, and
    any section this program does not understand.
    """
    eol = _eol(text)
    bom, body = (BOM, text[1:]) if text.startswith(BOM) else ("", text)
    lines = body.splitlines()
    facts = read(body)

    # Each step below can move the lines under it, so the facts are re-read
    # rather than adjusted.  Parsing a small file again is free, and index
    # arithmetic spread across three edits is where this would rot.
    already_retired = {_payload(line) for line in lines
                       if line.strip().startswith(RETIRED_PREFIX)}
    drop = []
    for index in disable_lines:
        if 0 <= index < len(lines):
            payload = _payload(lines[index])
            if payload is not None and payload in already_retired:
                drop.append(index)      # its retired twin already tells the story
            else:
                lines[index] = RETIRED_PREFIX + lines[index].strip()
                already_retired.add(payload)
    for index in sorted(set(drop), reverse=True):
        del lines[index]
    if disable_lines:
        facts = read(eol.join(lines))

    # lines coming back from retirement go back where they were, and
    # only what is still missing gets appended below
    rules = _restore_retired(lines, rules)
    facts = read(eol.join(lines))

    if ensure_require and facts.require_line is None:
        setting = "RequireTelemFFB=true"
        if facts.general_header is not None:
            lines.insert(facts.general_header + 1, setting)
        else:
            lines.extend(["", "[General]", setting])
        facts = read(eol.join(lines))

    if rules:
        if facts.devices_end is not None:
            for offset, rule in enumerate(rules):
                lines.insert(facts.devices_end + 1 + offset, rule)
        else:
            lines.extend(["", "[FFBDevices]", *rules])
        facts = read(eol.join(lines))

    # The whole section, every time: [DeviceOrder] is TelemFFB's own
    # concept and holds exactly the current joystick device at position
    # 1 - stale entries from earlier device selections are dropped, not
    # deferred to (an old entry at 1 hands the game's forces to a
    # blocked device, which renders nothing).
    if order:
        entry_lines = sorted((e.line for e in facts.order), reverse=True)
        for index in entry_lines:
            del lines[index]
        facts = read(eol.join(lines))
        if facts.order_header is not None:
            for offset, entry in enumerate(order):
                lines.insert(facts.order_header + 1 + offset, entry)
        else:
            lines.extend([
                "",
                "[DeviceOrder]",
                "; Reported to the game first, so it drives this device rather",
                "; than whichever it happened to enumerate first.",
                *order,
            ])

    return bom + eol.join(lines) + eol
