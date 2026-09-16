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
