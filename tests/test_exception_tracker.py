"""Tests for ExceptionTracker.remove_matching — the flag_error auto-clear.

flag_error-sourced records are removed from the tracker when the error
condition clears so the tracker agrees with the (cleared) app status. A
child's error reaches the master both raw (forwarded record) and with a
"Device: " prefix (master's own log of the merged telemetry), so removal
matches both forms.
"""
from datetime import datetime

import pytest

from telemffb.ExceptionTracker import ExceptionRecord, ExceptionTracker

pytestmark = [pytest.mark.unit]

MSG = "Force trim enabled but buttons not configured"


def make_record(message, module='root', level='ERROR'):
    return ExceptionRecord(timestamp=datetime.now(), message=message,
                           traceback='', level=level, module=module)


class TestRemoveMatching:
    def test_removes_exact_match(self):
        tracker = ExceptionTracker()
        tracker.add_exception(make_record(MSG))
        tracker.add_exception(make_record('some unrelated exception'))
        removed = tracker.remove_matching(MSG)
        assert removed == 1
        assert [e.message for e in tracker.get_exceptions()] == ['some unrelated exception']

    def test_removes_device_prefixed_and_raw_forms(self):
        # Master tracker after a child flag_error: its own log of the merged
        # telemetry (prefixed) plus the record forwarded from the child (raw)
        tracker = ExceptionTracker()
        tracker.add_exception(make_record(f'Joystick: {MSG}'))
        tracker.add_exception(make_record(MSG, module='joystick: root'))
        removed = tracker.remove_matching(f'Joystick: {MSG}')
        assert removed == 2
        assert tracker.get_count() == 0

    def test_raw_message_does_not_remove_other_devices(self):
        tracker = ExceptionTracker()
        tracker.add_exception(make_record(f'Pedals: {MSG}'))
        removed = tracker.remove_matching(MSG)
        assert removed == 0
        assert tracker.get_count() == 1

    def test_non_matching_records_untouched(self):
        tracker = ExceptionTracker()
        tracker.add_exception(make_record('a real exception traceback'))
        assert tracker.remove_matching(MSG) == 0
        assert tracker.get_count() == 1

    def test_empty_message_is_noop(self):
        tracker = ExceptionTracker()
        tracker.add_exception(make_record(MSG))
        assert tracker.remove_matching('') == 0
        assert tracker.remove_matching(None) == 0
        assert tracker.get_count() == 1

    def test_whitespace_tolerant(self):
        tracker = ExceptionTracker()
        tracker.add_exception(make_record(MSG + '  '))
        assert tracker.remove_matching(f'  {MSG}') == 1
        assert tracker.get_count() == 0

    def test_signal_emitted_only_when_removed(self):
        tracker = ExceptionTracker()
        fired = []
        tracker.exceptions_cleared.connect(lambda: fired.append(True))
        tracker.add_exception(make_record(MSG))
        tracker.remove_matching('no such message')
        assert fired == []
        tracker.remove_matching(MSG)
        assert fired == [True]

    def test_reflag_after_clear_creates_fresh_record(self):
        # Episode semantics: error -> clear -> error again yields a new
        # record with count 1, not a resurrected dedup counter
        tracker = ExceptionTracker()
        tracker.add_exception(make_record(MSG))
        tracker.add_exception(make_record(MSG))  # dedup increments
        assert tracker.get_exceptions()[0].count == 2
        tracker.remove_matching(MSG)
        assert tracker.get_count() == 0
        tracker.add_exception(make_record(MSG))
        assert tracker.get_count() == 1
        assert tracker.get_exceptions()[0].count == 1
