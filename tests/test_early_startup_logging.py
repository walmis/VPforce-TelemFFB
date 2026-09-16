"""Startup logs emitted before the real handlers exist.

_init_logging needs the LogWindow widget, so it cannot run until Qt is up.
Plenty is logged before that - the version banner, the DirectLink build
identity - and it used to go to the throwaway handler logging.info()
installs when root has none, which _init_logging's handlers.clear() then
dropped.  Those records never reached the log file: the line support asks
for first was the one line not on disk.

These tests are about the buffer that keeps them.  They exercise the three
helpers directly rather than running main(), which wants Qt, a device and
a registry - nothing here touches any of them.
"""

import logging

import pytest

from telemffb import utils


@pytest.fixture(autouse=True)
def isolated_root_logger(monkeypatch):
    """A root logger of this test's own, restored afterwards.

    The helpers reach for logging.getLogger() by design - that is the
    thing they have to fix - so the real one is put back rather than
    worked around.
    """
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    root.handlers.clear()
    monkeypatch.setattr(utils, '_early_log_buffer', None, raising=False)
    monkeypatch.setattr(utils, '_early_log_replayed', False, raising=False)
    yield root
    root.handlers.clear()
    root.handlers.extend(saved_handlers)
    root.setLevel(saved_level)


class Collector(logging.Handler):
    """Stands in for the dedup handler _init_logging builds."""

    def __init__(self):
        super().__init__()
        self.records = []

    def handle(self, record):
        self.records.append(record)
        return True


def messages(collector):
    return [r.getMessage() for r in collector.records]


class TestEarlyRecordsSurvive:

    def test_a_record_logged_before_setup_reaches_the_real_handler(self):
        """The whole point: nothing said during startup is lost."""
        utils.begin_early_logging()
        logging.info("TelemFFB version local-abc123: starting up")

        collector = Collector()
        utils.replay_early_logging(collector)

        assert "TelemFFB version local-abc123: starting up" in messages(collector)

    def test_order_and_levels_are_preserved(self):
        utils.begin_early_logging()
        logging.info("first")
        logging.warning("second")
        logging.info("third")

        collector = Collector()
        utils.replay_early_logging(collector)

        assert messages(collector) == ["first", "second", "third"]
        assert [r.levelno for r in collector.records] == [
            logging.INFO, logging.WARNING, logging.INFO]

    def test_timestamps_are_the_originals_not_the_replay(self):
        """Replayed records carry their own creation time, so the log
        file shows when startup actually did each thing rather than
        stamping the whole run at the moment the handlers appeared."""
        utils.begin_early_logging()
        logging.info("early")
        early = utils._early_log_buffer.buffer[0].created

        collector = Collector()
        utils.replay_early_logging(collector)

        assert collector.records[0].created == early

    def test_debug_records_are_kept_too(self):
        """Root is set to DEBUG here; the file handler wants them, and
        deciding what to drop is the real handlers' job, not this one's."""
        utils.begin_early_logging()
        logging.debug("a detail")

        collector = Collector()
        utils.replay_early_logging(collector)

        assert "a detail" in messages(collector)


class TestNoStrayHandler:

    def test_logging_info_does_not_install_its_own_handler(self):
        """The 'INFO:root:' lines came from logging.info() calling
        basicConfig() when root had no handlers.  Holding one stops
        that - which is why the buffer is added before anything logs."""
        utils.begin_early_logging()
        before = list(logging.getLogger().handlers)

        logging.info("something during startup")

        assert logging.getLogger().handlers == before

    def test_starting_twice_is_harmless(self):
        """Startup paths are re-entered in tests and by child instances;
        a second buffer would strand the records in the first."""
        utils.begin_early_logging()
        first = utils._early_log_buffer
        logging.info("kept")
        utils.begin_early_logging()

        assert utils._early_log_buffer is first
        collector = Collector()
        utils.replay_early_logging(collector)
        assert "kept" in messages(collector)


class TestExitBeforeSetup:

    def test_the_fallback_prints_when_the_handlers_never_came(self, capfd):
        """Startup can exit before _init_logging - the single-instance
        mutex, the unsafe-location refusal - and those are exactly the
        paths whose reason someone needs.  Buffering must not be what
        makes the reason disappear."""
        utils.begin_early_logging()
        logging.error("refusing to run from Downloads")

        utils.flush_early_logging_to_stderr()

        assert "refusing to run from Downloads" in capfd.readouterr().err

    def test_the_fallback_stays_quiet_once_records_were_replayed(self, capfd):
        """It runs at exit on every normal run too.  Printing there would
        duplicate the whole of startup onto the console."""
        utils.begin_early_logging()
        logging.info("already delivered")
        utils.replay_early_logging(Collector())

        utils.flush_early_logging_to_stderr()

        assert capfd.readouterr().err == ""

    def test_the_fallback_is_quiet_when_nothing_was_logged(self, capfd):
        utils.begin_early_logging()
        utils.flush_early_logging_to_stderr()
        assert capfd.readouterr().err == ""


class TestReplayIsOnce:

    def test_a_second_replay_does_not_repeat_the_records(self):
        """_init_logging is reachable more than once across a session's
        instances; the second call must not re-emit the first's startup."""
        utils.begin_early_logging()
        logging.info("once")

        first, second = Collector(), Collector()
        utils.replay_early_logging(first)
        utils.replay_early_logging(second)

        assert messages(first) == ["once"]
        assert second.records == []
