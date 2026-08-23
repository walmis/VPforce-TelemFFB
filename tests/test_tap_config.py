"""Reading and amending a dinput8.ini that somebody else wrote.

Many users already run walmis's ffb-fix with a config they tuned by hand.
Adopting one has to be conservative in a specific way: their file keeps
working, their edits survive, and anything we cannot honestly resolve gets
handed back to them rather than guessed at.

The failure this file exists to prevent is silent. A rule that never applies,
or one that applies to the wrong device, produces no error - just force
feedback that is missing or dead, with the cause in a text file the user has
no reason to open.
"""
import pytest

from telemffb.tap_config import (BOM, ConfigFacts, Rule, already_blocked,
                                 already_tapped, amend, read,
                                 rule_matches, shadowing_rules)

pytestmark = [pytest.mark.unit]

RHINO = (0xFFFF, 0x2054)
PEDALS = (0xFFFF, 0x2052)

#: What an ffb-fix user's file plausibly looks like: hand-written, commented,
#: and using the name-keyed rules that were the only kind available.
FFB_FIX = """\
; my setup - do not lose this
[General]
LogLevel=2

[FFBDevices]
; the Warthog has its own springs
Warthog=block
Rhino=allow
"""


def lines_of(text):
    return text.splitlines()


class TestReadingRules:
    def test_rules_are_found_in_order(self):
        facts = read(FFB_FIX)
        assert [r.key for r in facts.rules] == ["Warthog", "Rhino"]

    def test_a_rule_remembers_which_line_it_is_on(self):
        """Amending edits by line, so a wrong index retires the wrong rule."""
        facts = read(FFB_FIX)
        warthog = facts.rules[0]
        assert lines_of(FFB_FIX)[warthog.line] == "Warthog=block"

    def test_id_keys_are_recognized_as_ids(self):
        facts = read("[FFBDevices]\nFFFF:2054=tap\n")
        assert facts.rules[0].ids == RHINO

    def test_a_name_that_looks_almost_like_ids_is_still_a_name(self):
        """The wrapper requires four hex digits either side; a shorter key is
        a product name that happens to contain a colon."""
        facts = read("[FFBDevices]\nFFF:20=block\n")
        assert facts.rules[0].ids is None

    def test_values_lose_their_trailing_comments(self):
        facts = read("[FFBDevices]\nFFFF:2054=tap    ; VPforce Rhino\n")
        assert facts.rules[0].value == "tap"

    def test_comments_and_blank_lines_are_not_rules(self):
        assert read("[FFBDevices]\n; Rhino=block\n\n").rules == []

    def test_keys_outside_the_device_section_are_not_rules(self):
        """A [General] key is not a device, and treating one as a rule would
        invent a device nobody owns."""
        assert read("[General]\nLogLevel=2\n").rules == []


    def test_a_byte_order_mark_does_not_hide_the_first_section(self):
        """An editor that writes one would otherwise make every rule vanish -
        the same bug the wrapper's parser had."""
        assert len(read(BOM + "[FFBDevices]\nRhino=tap\n").rules) == 1


class TestReadingTheGeneralSection:
    def test_require_telemffb_is_located(self):
        facts = read("[General]\nRequireTelemFFB=false\n")
        assert facts.require_line == 1

    def test_its_absence_is_reported_as_absence(self):
        assert read(FFB_FIX).require_line is None



class TestWhichRuleWins:
    def test_ids_match_exactly(self):
        rule = Rule("FFFF:2054", "block", 0, ids=RHINO)
        assert rule_matches(rule, RHINO)
        assert not rule_matches(rule, PEDALS)

    def test_a_name_rule_matches_as_a_substring(self):
        """Mirrors the wrapper: 'Rhino' matches 'VPforce Rhino FFB Base'."""
        rule = Rule("Rhino", "block", 0)
        assert rule_matches(rule, RHINO, "VPforce Rhino FFB Base")


    def test_a_name_rule_cannot_match_a_device_we_cannot_name(self):
        """We hold a remembered name that may be empty; guessing a match
        would retire a rule the user still wants."""
        assert not rule_matches(Rule("Rhino", "block", 0), RHINO, "")



class TestFindingWhatWouldShadowUs:
    def test_an_existing_rule_for_the_same_device_is_reported(self):
        facts = read(FFB_FIX)
        shadows = shadowing_rules(facts, RHINO, "VPforce Rhino")
        assert [r.key for r in shadows] == ["Rhino"]

    def test_an_unrelated_rule_is_not(self):
        facts = read(FFB_FIX)
        assert shadowing_rules(facts, PEDALS, "VPforce Pedals") == []

    def test_an_existing_tap_rule_counts_too(self):
        """Harmless, but it means the line we would add is dead weight and
        the user should know the device is already handled."""
        facts = read("[FFBDevices]\nFFFF:2054=tap\n")
        assert len(shadowing_rules(facts, RHINO)) == 1



class TestAmendingLeavesTheRestAlone:
    def test_their_comments_and_settings_survive(self):
        result = amend(FFB_FIX, ["FFFF:2052=tap"])
        assert "; my setup - do not lose this" in result
        assert "LogLevel=2" in result
        assert "; the Warthog has its own springs" in result

    def test_their_rules_survive(self):
        result = amend(FFB_FIX, ["FFFF:2052=tap"])
        assert "Warthog=block" in result
        assert "Rhino=allow" in result

    def test_our_rule_is_added(self):
        assert "FFFF:2052=tap" in amend(FFB_FIX, ["FFFF:2052=tap"])

    def test_our_rule_lands_inside_the_device_section(self):
        """Appended past the end of the file it would sit under whatever
        section came last, and the wrapper would never read it."""
        facts = read(amend(FFB_FIX, ["FFFF:2052=tap"]))
        assert "FFFF:2052" in [r.key for r in facts.rules]


    def test_line_endings_are_left_as_they_were(self):
        crlf = FFB_FIX.replace("\n", "\r\n")
        result = amend(crlf, ["FFFF:2052=tap"])
        assert "\r\n" in result
        assert result.count("\n") == result.count("\r\n")



class TestRetiringARule:
    def test_the_old_rule_stops_applying(self):
        facts = read(FFB_FIX)
        rhino = [r for r in facts.rules if r.key == "Rhino"][0]
        result = amend(FFB_FIX, ["FFFF:2054=tap"], disable_lines=[rhino.line])
        assert [r.key for r in read(result).rules] == ["Warthog", "FFFF:2054"]

    def test_it_is_commented_rather_than_deleted(self):
        """So the change is visible in the file and can be undone by hand."""
        facts = read(FFB_FIX)
        rhino = [r for r in facts.rules if r.key == "Rhino"][0]
        result = amend(FFB_FIX, [], disable_lines=[rhino.line])
        assert "Rhino=allow" in result
        assert "; retired by TelemFFB: Rhino=allow" in result


    def test_an_index_past_the_end_is_ignored_not_fatal(self):
        assert "Warthog=block" in amend(FFB_FIX, [], disable_lines=[999])


class TestRequireTelemFFB:
    def test_it_is_added_when_missing(self):
        assert read(amend(FFB_FIX, [])).require_line is not None

    def test_it_goes_into_an_existing_general_section(self):
        """A second [General] would work, but the file is the user's to read."""
        result = amend(FFB_FIX, [])
        assert result.count("[General]") == 1

    def test_an_existing_setting_is_left_as_the_user_set_it(self):
        """Even set to false. Overriding it would re-enable rules they
        deliberately turned off."""
        original = "[General]\nRequireTelemFFB=false\n\n[FFBDevices]\n"
        assert "false" in amend(original, ["FFFF:2054=tap"])

    def test_rules_still_land_correctly_after_the_setting_is_inserted(self):
        """The insert shifts every line below it; a stale index would put our
        rule in the wrong section."""
        result = amend(FFB_FIX, ["FFFF:2052=tap"])
        assert "FFFF:2052" in [r.key for r in read(result).rules]


class TestStartingFromNothing:
    def test_an_empty_file_gets_both_sections(self):
        result = amend("", ["FFFF:2054=tap"])
        facts = read(result)
        assert facts.devices_header is not None
        assert facts.require_line is not None
        assert [r.key for r in facts.rules] == ["FFFF:2054"]

    def test_a_file_with_no_device_section_gains_one(self):
        result = amend("[General]\nLogLevel=2\n", ["FFFF:2054=tap"])
        assert [r.key for r in read(result).rules] == ["FFFF:2054"]



class TestAmendingIsStable:
    def test_amending_twice_does_not_duplicate_the_setting(self):
        once = amend(FFB_FIX, ["FFFF:2052=tap"])
        twice = amend(once, [])
        assert twice.count("RequireTelemFFB") == 1

    def test_the_result_reads_back_as_what_was_intended(self):
        """The real contract: whatever we wrote, the wrapper's view of it
        matches what the user was told would happen."""
        facts = read(FFB_FIX)
        rhino = [r for r in facts.rules if r.key == "Rhino"][0]
        result = amend(FFB_FIX, ["FFFF:2054=tap"], disable_lines=[rhino.line])
        after = read(result)
        assert shadowing_rules(after, RHINO, "VPforce Rhino")[0].is_tap


class TestWhereAnAddedLineLands:
    """Inside its section, and after what is already there. A rule that
    lands above the comments explaining the syntax makes our own generated
    file look scrambled the first time it is amended."""

    GENERATED = ("[FFBDevices]\n"
                 "; VVVV:PPPP=tap - relay this device's effects to TelemFFB\n"
                 ";   other actions: block, allow, or 0-100\n"
                 "FFFF:2054=tap\n")

    def test_it_goes_after_the_existing_rules(self):
        out = amend(self.GENERATED, ["044F:B10A=tap"])
        lines = [l for l in out.splitlines() if "=tap" in l]
        assert lines[-1].strip() == "044F:B10A=tap"

    def test_it_goes_after_the_comments_when_no_rule_remains(self):
        """The case that exposed this: retiring the only rule left the
        section's end at its header, so the replacement jumped the comments."""
        facts = read(self.GENERATED)
        rule = facts.rules[0]
        out = amend(self.GENERATED, ["044F:B10A=tap"],
                    disable_lines=[rule.line])
        body = out.splitlines()
        assert body.index("044F:B10A=tap") > body.index(
            ";   other actions: block, allow, or 0-100")




class TestNotAddingWhatIsAlreadyThere:
    """Confirming the dialog on a sim that is already configured used to
    append a second identical rule each time."""

    TAPPED = "[FFBDevices]\nFFFF:2054=tap    ; Monster\n"

    def test_a_device_already_tapped_is_recognized(self):
        assert already_tapped(self.TAPPED, RHINO, "Monster")

    def test_a_different_device_is_not(self):
        assert not already_tapped(self.TAPPED, PEDALS, "Pedals")

    def test_a_rule_about_to_be_retired_does_not_count(self):
        """It is being replaced, so its replacement is not redundant."""
        assert not already_tapped(self.TAPPED, RHINO, "Monster", ignoring=[1])

    def test_a_block_rule_is_not_a_handover(self):
        assert not already_tapped("[FFBDevices]\nFFFF:2054=block\n", RHINO)

    def test_a_name_keyed_rule_counts(self):
        assert already_tapped("[FFBDevices]\nMonster=tap\n", RHINO,
                              "VPforce Rhino FFB Monster")

    def test_confirming_an_unchanged_choice_rewrites_nothing(self):
        """The end of the story: nothing to add means a byte-identical file.

        Complete enough to have nothing missing - a config without
        RequireTelemFFB would legitimately gain one, which is a change but
        not this one."""
        settled = ("[General]\nRequireTelemFFB=true\n\n"
                   "[FFBDevices]\nFFFF:2054=tap    ; Monster\n")
        assert amend(settled, [], []) == settled


class TestBlocksAlreadyInPlace:
    def test_an_existing_block_is_recognized(self):
        assert already_blocked("[FFBDevices]\nFFFF:2052=block\n", PEDALS)

    def test_a_tap_rule_is_not_a_block(self):
        assert not already_blocked("[FFBDevices]\nFFFF:2052=tap\n", PEDALS)

    def test_a_block_about_to_be_retired_does_not_count(self):
        assert not already_blocked("[FFBDevices]\nFFFF:2052=block\n", PEDALS,
                                   ignoring=[1])


class TestChangingYourMindBackAndForth:
    """Switching between two sticks and reconciling each time used to retire
    a line and append another per switch - the file grew a graveyard of
    retired lines that all said the same two things."""

    MONSTER = "FFFF:2054=tap    ; Monster (joystick)"
    SIDEWINDER = "045E:001B=tap    ; SideWinder (joystick)"

    def flip(self, text, retire_key, add):
        facts = read(text)
        lines = [r.line for r in facts.rules if r.key == retire_key]
        return amend(text, [add], disable_lines=lines)

    def test_a_line_coming_back_is_restored_not_written_again(self):
        start = "[FFBDevices]\n" + self.MONSTER + "\n"
        once = self.flip(start, "FFFF:2054", self.SIDEWINDER)
        back = self.flip(once, "045E:001B", self.MONSTER)
        assert [r.key for r in read(back).rules] == ["FFFF:2054"]
        assert back.count("FFFF:2054=tap") == 1
        assert back.count("; retired by TelemFFB: " + self.SIDEWINDER) == 1

    def test_the_file_stays_the_same_size_however_often(self):
        text = "[FFBDevices]\n" + self.MONSTER + "\n"
        for flip in range(6):
            text = (self.flip(text, "FFFF:2054", self.SIDEWINDER) if flip % 2 == 0
                    else self.flip(text, "045E:001B", self.MONSTER))
        # header, RequireTelemFFB's [General] (3 lines), one active, one retired
        assert len([l for l in text.splitlines() if "=tap" in l]) == 2
        assert len(read(text).rules) == 1

    def test_ordering_entries_are_bounded_the_same_way(self):
        text = "[DeviceOrder]\n1=FFFF:2054    ; Monster (joystick)\n"
        for flip in range(5):
            facts = read(text)
            stale = [e.line for e in facts.order]
            new = ("1=045E:001B    ; SideWinder (joystick)" if flip % 2 == 0
                   else "1=FFFF:2054    ; Monster (joystick)")
            text = amend(text, [], disable_lines=stale, order=[new],
                         order_even_if_present=True)
        assert len(read(text).order) == 1
        assert len([l for l in text.splitlines() if "1=" in l]) == 2

    def test_a_retired_line_is_still_there_to_read(self):
        """Bounded, not erased: the one retired copy is what tells a reader
        the other stick used to be configured here."""
        start = "[FFBDevices]\n" + self.MONSTER + "\n"
        once = self.flip(start, "FFFF:2054", self.SIDEWINDER)
        assert "; retired by TelemFFB: " + self.MONSTER in once
