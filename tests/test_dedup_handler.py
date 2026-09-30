"""Tests for the cycle-aware DedupHandler in telemffb.utils."""

import logging
import threading
import time

import pytest

from telemffb.utils import DedupHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class RecordingHandler(logging.Handler):
    """A simple in-memory handler that records all records it receives."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def make_record(msg, level=logging.INFO, name="test"):
    """Return a bare logging.LogRecord with sensible defaults."""
    rec = logging.LogRecord(
        name=name,
        level=level,
        pathname="test.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    rec.created = 1_000_000.0  # arbitrary fixed wall-clock value
    return rec


def make_handler(period_seconds: float = 5.0):
    """Create a DedupHandler wired to a RecordingHandler with a fake clock.

    Returns ``(dedup, recorder, advance)`` where ``advance(dt)`` moves the
    fake clock forward by ``dt`` seconds.
    """
    recorder = RecordingHandler()
    dedup = DedupHandler(handlers=[recorder], period_seconds=period_seconds)
    t = [0.0]
    dedup._clock = lambda: t[0]

    def advance(dt):
        t[0] += dt

    dedup._advance = advance
    return dedup, recorder, advance


# ---------------------------------------------------------------------------
# Consecutive-repeat (legacy) behaviour
# ---------------------------------------------------------------------------

class TestConsecutiveRepeats:

    def test_single_message_forwarded_once_then_suppressed(self):
        dedup, rec, advance = make_handler()
        dedup.emit(make_record("A"))
        dedup.emit(make_record("A"))
        dedup.emit(make_record("A"))
        # 1 forwarded, 2 suppressed
        assert len(rec.records) == 1
        assert rec.records[0].getMessage() == "A"

    def test_final_summary_on_distinct_message(self):
        dedup, rec, advance = make_handler()
        # 5× A
        for _ in range(5):
            dedup.emit(make_record("A"))
            advance(0.1)
        # B interrupts the run
        advance(1.0)
        dedup.emit(make_record("B"))
        # B forwards, and before B a final "A repeated 5 times" summary is emitted
        msgs = [r.getMessage() for r in rec.records]
        assert "A" in msgs, "first A should be forwarded"
        assert any("repeated 5 times" in m for m in msgs), f"expected final summary, got: {msgs}"
        assert "B" in msgs, "B should be forwarded"
        # The final summary comes before B in emission order
        summary_idx = next(i for i, m in enumerate(msgs) if "repeated 5 times" in m)
        b_idx = msgs.index("B")
        assert summary_idx < b_idx

    def test_periodic_summary_during_long_streak(self):
        dedup, rec, advance = make_handler(period_seconds=2.0)
        # continuous streak (gap < period so no quiet reset): t = 0, 1, 2
        for _ in range(3):
            dedup.emit(make_record("A"))
            advance(1.0)
        # the 4th A lands exactly one period after the streak started
        # (× the first repeat set _repeat_periodic_ts at the first A, t=0) →
        # periodic "(repeated N times so far)" summary
        dedup.emit(make_record("A"))
        msgs = [r.getMessage() for r in rec.records]
        assert any("repeated" in m and "so far" in m for m in msgs), f"expected periodic summary, got: {msgs}"


    def test_quiet_gap_resets_and_forwards_fresh(self):
        dedup, rec, advance = make_handler(period_seconds=2.0)
        dedup.emit(make_record("A"))
        advance(0.5)
        dedup.emit(make_record("A"))
        # Quiet for >= period
        advance(3.0)
        dedup.emit(make_record("A"))
        # A should be forwarded twice (once before the gap, once after)
        assert [r.getMessage() for r in rec.records] == ["A", "A"]

    def test_different_level_not_merged(self):
        dedup, rec, advance = make_handler()
        dedup.emit(make_record("A", level=logging.INFO))
        dedup.emit(make_record("A", level=logging.WARNING))
        # Both forwarded (different levelnum → different keys)
        assert len(rec.records) == 2

    def test_different_logger_name_not_merged(self):
        dedup, rec, advance = make_handler()
        dedup.emit(make_record("A", name="logger1"))
        dedup.emit(make_record("A", name="logger2"))
        assert len(rec.records) == 2


# ---------------------------------------------------------------------------
# Repeating-cycle (new) behaviour
# ---------------------------------------------------------------------------

class TestCycleDetection:

    def test_ababab_cycle_detected(self):
        dedup, rec, advance = make_handler(period_seconds=10.0)
        # A B A B A B (t = 0,1,2,3,4,5)
        msgs_in = ["A", "B", "A", "B", "A", "B"]
        for msg in msgs_in:
            dedup.emit(make_record(msg))
            advance(1.0)
        msgs = [r.getMessage() for r in rec.records]
        # First A and first B forwarded; at the 2nd A a cycle summary fires;
        # subsequent B, A, B suppressed
        assert "A" in msgs, f"first A missing: {msgs}"
        assert "B" in msgs, f"first B missing: {msgs}"
        assert any("Cycle detected" in m for m in msgs), f"cycle summary missing: {msgs}"
        # Count of forwarded records: A, B, cycle summary = 3
        assert len(rec.records) == 3, f"expected 3 emitted records, got {len(rec.records)}: {msgs}"
        # The cycle summary should mention both types
        cycle_msg = next(m for m in msgs if "Cycle detected" in m)
        assert "A" in cycle_msg
        assert "B" in cycle_msg

    def test_abca_all_three_types_listed(self):
        dedup, rec, advance = make_handler(period_seconds=10.0)
        # A B C A → cycle with 3 distinct types
        for msg in ["A", "B", "C", "A"]:
            dedup.emit(make_record(msg))
            advance(1.0)
        msgs = [r.getMessage() for r in rec.records]
        assert any("Cycle detected" in m for m in msgs), msgs
        cycle_msg = next(m for m in msgs if "Cycle detected" in m)
        assert "A" in cycle_msg
        assert "B" in cycle_msg
        assert "C" in cycle_msg
        # 3 forwards + 1 summary = 4
        assert len(rec.records) == 4, msgs

    def test_cycle_count_in_summary(self):
        dedup, rec, advance = make_handler(period_seconds=10.0)
        # A×3, B×3, A (triggers on 4th A → counts A:4, B:3)
        for msg in ["A", "A", "A", "B", "B", "B", "A"]:
            dedup.emit(make_record(msg))
            advance(0.5)
        msgs = [r.getMessage() for r in rec.records]
        cycle_msg = next(m for m in msgs if "Cycle detected" in m)
        assert "4" in cycle_msg or "3" in cycle_msg, f"expected counts in summary: {cycle_msg}"

    def test_foreign_message_forwards_during_cycle(self):
        dedup, rec, advance = make_handler(period_seconds=10.0)
        # A B A → cycle collapsed
        for msg in ["A", "B", "A"]:
            dedup.emit(make_record(msg))
            advance(1.0)
        assert any("Cycle detected" in r.getMessage() for r in rec.records)
        # Foreign message C arrives during the cycle: it should be forwarded
        advance(1.0)
        dedup.emit(make_record("C"))
        msgs = [r.getMessage() for r in rec.records]
        assert "C" in msgs, f"C missing from output: {msgs}"

    def test_cycle_periodic_refresh(self):
        dedup, rec, advance = make_handler(period_seconds=2.0)
        # A B A (collapse at t=1.0, _last_cycle_ts=1.0)
        for msg in ["A", "B", "A"]:
            dedup.emit(make_record(msg))
            advance(0.5)
        initial_count = len(rec.records)
        assert any("Cycle detected" in r.getMessage() for r in rec.records[:initial_count])
        # keep the streak continuous (gap < period) so no quiet reset fires
        advance(0.5)
        dedup.emit(make_record("B"))  # t=1.5, suppressed
        advance(0.5)
        dedup.emit(make_record("A"))  # t=2.0, suppressed, 2.0-1.0 < 2.0 -> no refresh yet
        advance(1.0)
        dedup.emit(make_record("B"))  # t=3.0: 3.0 - 1.0 >= 2.0 -> periodic "so far" summary
        msgs = [r.getMessage() for r in rec.records]
        assert any("Cycle detected" in m and "so far" in m for m in msgs[initial_count:]), \
            f"expected periodic cycle summary, got: {msgs[initial_count:]}"

    def test_close_flushes_final_cycle_summary(self):
        dedup, rec, advance = make_handler(period_seconds=10.0)
        # A B A → collapse
        for msg in ["A", "B", "A"]:
            dedup.emit(make_record(msg))
            advance(1.0)
        # close() should emit one more cycle summary
        dedup.close()
        msgs = [r.getMessage() for r in rec.records]
        cycle_msgs = [m for m in msgs if "Cycle detected" in m]
        # At least the initial summary + the close() summary
        assert len(cycle_msgs) >= 2, f"expected ≥2 cycle summaries (including close), got {cycle_msgs}"


# ---------------------------------------------------------------------------
# Loop-gated collapse: a duplicate alone must not prove a cycle
# ---------------------------------------------------------------------------

class TestLoopGatedCollapse:

    def test_diverse_burst_with_one_duplicate_is_not_collapsed(self):
        # The 11:39:55 startup shape: ~8-13 distinct messages, one of them
        # repeated non-adjacently. That is not a loop - every message stays
        # visible, including the repeat.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        msgs_in = ["m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "m1", "m9"]
        for msg in msgs_in:
            dedup.emit(make_record(msg))
            advance(0.2)
        out = [r.getMessage() for r in rec.records]
        assert not any("Cycle detected" in m for m in out), out
        assert out == msgs_in

    def test_diverse_burst_with_one_recurrence_does_not_collapse(self):
        # The PR #101 review shape: a burst of ~107 distinct one-off lines
        # (an aircraft load) with a single line repeated once.  Under the
        # old total-occurrences floor that crossed 100 and collapsed; the
        # repeats floor (1 repeat here) keeps all 108 messages visible.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        msgs_in = [f"m{i}" for i in range(107)]
        msgs_in.append("m0")
        for msg in msgs_in:
            dedup.emit(make_record(msg))
            advance(0.01)
        out = [r.getMessage() for r in rec.records]
        assert not any("Cycle detected" in m for m in out), out
        assert out == msgs_in

    def test_diverse_storm_collapses_after_rate_floor(self):
        # 10 distinct types at 60 Hz: far more than MAX_LOOP_TYPES different
        # messages, but the rate can only be a fault loop, so the window
        # collapses once it carries LOOP_TOTAL_FLOOR repeats.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        types = [f"t{i}" for i in range(10)]
        for i in range(1000):
            dedup.emit(make_record(types[i % 10]))
            advance(1.0 / 60.0)
        out = [r.getMessage() for r in rec.records]
        assert any("Cycle detected" in m for m in out), out
        first_cycle_idx = next(i for i, m in enumerate(out)
                               if "Cycle detected" in m)
        # the floor is 100 repeats; with 10 evenly cycling types, repeats =
        # total - 10, so the first repeat crossing it is the 110th message
        # (index 109): 11 occurrences per type, i.e. 10 repeats each
        assert first_cycle_idx == 109, out[:first_cycle_idx + 1]
        # no type was lost before the collapse
        for t in types:
            assert t in out[:first_cycle_idx], t

    def test_periodic_refresh_holds_interval_under_sustained_storm(self):
        # Regression for the pre-0926 bug: a 60 Hz storm used to re-emit the
        # cycle summary on nearly every message ("39 summaries in 26 ms").
        # 30 s of storm at period 5 s: collapse at t=1.65 s, then exactly
        # one refresh per period -> 5 refreshes (t=6.65, 11.65, ..., 26.65).
        dedup, rec, advance = make_handler(period_seconds=5.0)
        types = [f"t{i}" for i in range(10)]
        for i in range(1800):
            dedup.emit(make_record(types[i % 10]))
            advance(1.0 / 60.0)
        out = [r.getMessage() for r in rec.records]
        refreshes = [m for m in out if "so far" in m]
        assert len(refreshes) == 5, out

    def test_lone_recurrence_closes_the_pending_run_with_a_final_summary(self):
        # K A B C X X K: when the recurring K arrives, the pending two-X run
        # must be closed by its final "(message repeated 2 times)" summary
        # before K is forwarded.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        for msg in ["K", "A", "B", "C", "X", "X", "K"]:
            dedup.emit(make_record(msg))
            advance(0.2)
        out = [r.getMessage() for r in rec.records]
        assert out == ["K", "A", "B", "C", "X",
                       "X (message repeated 2 times)", "K"], out

    def test_four_type_burst_is_not_a_loop(self):
        # Exactly 4 distinct types: one above MAX_LOOP_TYPES (3) and far
        # below the floor - the other side of the type boundary.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        for i in range(8):
            dedup.emit(make_record(f"t{i % 4}"))
            advance(1.0)
        out = [r.getMessage() for r in rec.records]
        assert out == [f"t{i}" for i in range(4)] * 2, out
        assert not any("Cycle detected" in m for m in out), out

    def test_ninety_nine_repeats_do_not_reach_the_floor(self):
        # 6 distinct types, 99 repeats: one below LOOP_TOTAL_FLOOR (100) -
        # the other side of the rate boundary.  A 100 times (99 repeats)
        # plus five one-off types (0 repeats each) keeps the window below
        # the floor, so the final A is forwarded, not collapsed.
        dedup, rec, advance = make_handler(period_seconds=10.0)
        for _ in range(99):
            dedup.emit(make_record("A"))
            advance(0.5)
        for t in ["b", "c", "d", "e", "f"]:
            dedup.emit(make_record(t))
            advance(0.5)
        dedup.emit(make_record("A"))
        out = [r.getMessage() for r in rec.records]
        assert out[-1] == "A", out
        assert not any("Cycle detected" in m for m in out), out


# ---------------------------------------------------------------------------
# Non-loop members: a one-off that merely co-occurs with a collapsed loop must
# stay visible when it recurs - only genuine loop members are suppressed
# ---------------------------------------------------------------------------

class TestNonLoopMemberRecurrence:

    def test_oneoff_after_collapse_is_forwarded_on_recurrence(self):
        # Issue 103 repro: a 2-type fault loop collapses the window; a distinct
        # one-off that appears afterwards recurs and must stay visible (0 of its
        # recurrences reached the log before the fix).
        dedup, rec, advance = make_handler(period_seconds=5.0)
        dedup.emit(make_record("A")); advance(1.0)   # t=0
        dedup.emit(make_record("B")); advance(1.0)   # t=1
        dedup.emit(make_record("A")); advance(1.0)   # t=2 -> collapse (2 types)
        dedup.emit(make_record("P")); advance(1.0)   # t=3 one-off first -> forwarded
        dedup.emit(make_record("B")); advance(1.0)   # t=4 loop member suppressed
        dedup.emit(make_record("A")); advance(1.0)   # t=5 loop member suppressed
        for _ in range(5):
            dedup.emit(make_record("P")); advance(1.0)   # t=6..10 one-off recurrences
        out = [r.getMessage() for r in rec.records]
        # first sight + 5 recurrences all visible
        assert out.count("P") == 6, out

    def test_loop_members_stay_suppressed(self):
        # Guard: the fix must not start forwarding genuine loop members.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        dedup.emit(make_record("A")); advance(1.0)
        dedup.emit(make_record("B")); advance(1.0)
        dedup.emit(make_record("A")); advance(1.0)   # collapse
        for _ in range(10):
            dedup.emit(make_record("A")); advance(1.0)
            dedup.emit(make_record("B")); advance(1.0)
        out = [r.getMessage() for r in rec.records]
        assert out.count("A") == 1, out
        assert out.count("B") == 1, out

    def test_floor_collapse_oneoff_in_window_is_forwarded(self):
        # More than MAX_LOOP_TYPES types, so the collapse can only come from the
        # rate floor. A one-off that sits in the window with a single occurrence
        # does not drive the floor, so it is not a loop member: its recurrence is
        # forwarded while the high-repeat storm types stay suppressed.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        for i in range(100):
            dedup.emit(make_record(f"t{i % 4}"))
            advance(0.1)
        dedup.emit(make_record("Q")); advance(0.1)     # one-off, inside the window
        for i in range(100, 112):                      # crosses the floor mid-run
            dedup.emit(make_record(f"t{i % 4}"))
            advance(0.1)
        for _ in range(3):                             # one-off recurrences
            dedup.emit(make_record("Q")); advance(0.1)
        out = [r.getMessage() for r in rec.records]
        assert any("Cycle detected" in m for m in out), out
        # first sight + 3 recurrences all forwarded
        assert out.count("Q") == 4, out


# ---------------------------------------------------------------------------
# Stale keys: a seen key that aged out of the window must rejoin it
# ---------------------------------------------------------------------------

class TestStaleKeyWindowReappend:

    def test_stale_key_recurrence_appears_in_the_cycle_summary(self):
        # A at t=0, then B/C alternating at 1 Hz: the log never goes quiet for
        # a period, so the episode (and _seen) stays alive while A ages out of
        # the window. B/C alone look like a loop (2 <= MAX_LOOP_TYPES), so the
        # window collapses early (t=3) and the refreshes stop listing A. Two
        # more B/C after A's t=31 recurrence put the next periodic refresh at
        # t=33: with the re-joined A, it must list A with its full count.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        dedup.emit(make_record("A"))
        advance(1.0)
        for i in range(30):
            dedup.emit(make_record("B" if i % 2 == 0 else "C"))
            advance(1.0)
        dedup.emit(make_record("A"))
        advance(1.0)
        dedup.emit(make_record("B"))
        advance(1.0)
        dedup.emit(make_record("C"))
        summaries = [m for m in (r.getMessage() for r in rec.records)
                     if "Cycle detected" in m]
        lines = summaries[-1].splitlines()
        assert any(ln.strip() == "- A: 2" for ln in lines), summaries[-1]
        assert any("- B:" in ln for ln in lines), summaries[-1]
        assert any("- C:" in ln for ln in lines), summaries[-1]

    def test_stale_key_recurrence_is_forwarded_in_a_diverse_window(self):
        # Regression guard for the re-append: with the stale key re-joined the
        # window holds 5 distinct types and only 37 repeats (42 occurrences) -
        # above MAX_LOOP_TYPES and far below the floor, so the recurrence
        # must stay a lone one: forwarded, not swallowed by a collapse.
        dedup, rec, advance = make_handler(period_seconds=5.0)
        dedup.emit(make_record("stale"))
        advance(1.0)
        for i in range(1, 41):
            dedup.emit(make_record(f"t{i % 4}"))
            advance(1.0)
        dedup.emit(make_record("stale"))
        out = [r.getMessage() for r in rec.records]
        assert out[-1] == "stale", out
        assert not any("Cycle detected" in m for m in out), out


# ---------------------------------------------------------------------------
# Summary content: ordering, severity, truthfulness, exceptions
# ---------------------------------------------------------------------------

def _six_type_storm(period_seconds=10.0):
    """Drive a 6-type storm past the rate floor and return the summary text.

    100× A, then five one-off types, then one more A: the repeat of A finds
    a window of 6 distinct types with 100 repeats (the floor - A carries
    101 occurrences, i.e. 100 repeats, and the one-offs 0), so the collapse
    fires with A heavily weighted.
    """
    dedup, rec, advance = make_handler(period_seconds=period_seconds)
    for _ in range(100):
        dedup.emit(make_record("A"))
        advance(0.5)
    for t in ["b", "c", "d", "e", "f"]:
        dedup.emit(make_record(t))
        advance(0.5)
    dedup.emit(make_record("A"))
    return next(m for m in (r.getMessage() for r in rec.records)
                if "Cycle detected" in m)


class TestSummaryContent:

    def test_cycle_summary_lists_most_frequent_first(self):
        summary = _six_type_storm()
        listed = [ln for ln in summary.splitlines() if ln.strip().startswith("-")]
        assert listed[0].startswith("    - A:"), summary
        # the least frequent type (seen last) is the one folded away
        assert "+1 more" in summary, summary
        assert "    - f: 1" not in summary, summary

    def test_non_debug_level_lists_at_most_max_types_and_no_false_promise(self):
        summary = _six_type_storm()
        listed = [ln for ln in summary.splitlines() if ln.strip().startswith("-")]
        assert len(listed) <= 5, summary
        assert "see DEBUG" not in summary, summary

    def test_debug_level_lists_every_type(self):
        root = logging.getLogger()
        saved = root.level
        root.setLevel(logging.DEBUG)
        try:
            summary = _six_type_storm()
        finally:
            root.setLevel(saved)
        listed = [ln for ln in summary.splitlines() if ln.strip().startswith("-")]
        assert len(listed) == 6, summary
        assert "+1 more" not in summary, summary
        assert "see DEBUG" not in summary, summary

    def test_cycle_summary_level_is_max_of_members(self):
        dedup, rec, advance = make_handler(period_seconds=10.0)
        dedup.emit(make_record("A", level=logging.INFO))
        advance(1.0)
        dedup.emit(make_record("B", level=logging.ERROR))
        advance(1.0)
        dedup.emit(make_record("A", level=logging.INFO))
        summary = [r for r in rec.records if "Cycle detected" in r.getMessage()]
        assert summary, "no cycle summary emitted"
        assert summary[0].levelno == logging.ERROR

    def test_cycle_narrowing_to_one_type_uses_repeat_form(self):
        # A B A collapses (2 types). B then ages out of the window while A
        # keeps coming: the periodic refresh must read as the familiar
        # "(message repeated N times so far)", not "across 1 types".
        dedup, rec, advance = make_handler(period_seconds=2.0)
        for msg in ["A", "B", "A"]:          # t=0.0, 0.5, 1.0 -> collapse
            dedup.emit(make_record(msg))
            advance(0.5)
        for _ in range(4):                   # t=1.5, 2.0, 2.5, 3.0
            dedup.emit(make_record("A"))
            advance(0.5)
        last = rec.records[-1].getMessage()
        assert "message repeated" in last, last
        assert "so far" in last, last
        assert "Cycle detected" not in last, last

    def test_repeat_summary_carries_exception(self):
        dedup, rec, advance = make_handler(period_seconds=10.0)
        for _ in range(3):
            rec_in = make_record("boom")
            rec_in.exc_info = OSError("0xe06d7363")
            dedup.emit(rec_in)
            advance(0.5)
        dedup.emit(make_record("done"))      # interrupts the run -> final summary
        out = [r.getMessage() for r in rec.records]
        assert any("repeated 3 times" in m and "OSError: 0xe06d7363" in m
                   for m in out), out

    def test_cycle_summary_line_carries_latest_exception(self):
        # The DInput fault shape: the exception evolves across the storm
        # (access violation, then 0xe06d7363); the summary must show the
        # latest, not the first.
        dedup, rec, advance = make_handler(period_seconds=10.0)
        first = make_record("A")
        first.exc_info = (OSError,
                          OSError("access violation reading 0x0000021F8D2B0010"),
                          None)
        dedup.emit(first)
        advance(1.0)
        dedup.emit(make_record("B"))
        advance(1.0)
        latest = make_record("A")
        latest.exc_info = (OSError, OSError("0xe06d7363"), None)
        dedup.emit(latest)
        summary = next(m for m in (r.getMessage() for r in rec.records)
                       if "Cycle detected" in m)
        assert "OSError: 0xe06d7363" in summary, summary
        assert "access violation" not in summary, summary


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_message(self):
        dedup, rec, advance = make_handler()
        dedup.emit(make_record(""))
        assert len(rec.records) == 1

    def test_multiple_handlers_all_receive(self):
        r1 = RecordingHandler()
        r2 = RecordingHandler()
        dedup = DedupHandler(handlers=[r1, r2], period_seconds=5.0)
        t = [0.0]
        dedup._clock = lambda: t[0]
        dedup.emit(make_record("A"))
        assert len(r1.records) == 1
        assert len(r2.records) == 1

    def test_handler_exception_in_inner_does_not_crash(self):
        class BrokenHandler(logging.Handler):
            def emit(self, record):
                raise RuntimeError("oops")

        good = RecordingHandler()
        dedup = DedupHandler(handlers=[BrokenHandler(), good], period_seconds=5.0)
        t = [0.0]
        dedup._clock = lambda: t[0]
        # Should not raise
        dedup.emit(make_record("A"))
        assert len(good.records) == 1

    def test_thread_safety_smoke(self, n_threads=8, msgs_per_thread=50):
        dedup, _rec, _advance = make_handler()
        errors = []

        def worker(tid):
            try:
                for i in range(msgs_per_thread):
                    dedup.emit(make_record(f"msg-{tid}-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"exceptions in worker threads: {errors}"
