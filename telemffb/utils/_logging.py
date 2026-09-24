#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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

import inspect
import logging
import sys
import threading
import time
from collections import deque

import stransi

from PyQt6 import QtCore, QtGui
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat

import telemffb.globals as G


__all__ = [
    "LOG_LEVELS",
    "apply_log_level",
    "dbprint",
    "debug_timed",
    "debug_caller_args",
    "millis",
    "micros",
    "AnsiColors",
    "parseAnsiText",
    "OutLog",
    "begin_early_logging",
    "replay_early_logging",
    "flush_early_logging_to_stderr",
    "DedupHandler",
    "LoggingFilter",
]


#: The log levels the settings offer, by the name stored in the settings.
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def apply_log_level(level_name: str) -> int:
    """Set the root logger to the named level and log the change.

    One place for the name -> level mapping, because three callers set it: the
    startup read, the settings dialog for this instance, and the IPC message a
    master sends a child whose level was changed from the master's dialog.

    :param level_name: a key of :data:`LOG_LEVELS`; anything else means DEBUG
    :returns: the level that was applied
    """
    level = LOG_LEVELS.get(str(level_name).upper(), logging.DEBUG)
    logger = logging.getLogger()
    logger.setLevel(level)
    logging.info(f"Logging level set to:{logging.getLevelName(logger.getEffectiveLevel())}")
    return level


def dbprint(color, msg, instance=None):
    if instance is not None:
        if instance != G.device_type:
            return
    reset = '\033[0m'
    match color:
        case "red":
            ccode = '\033[91m'
        case 'yellow':
            ccode = '\033[93m'
        case 'blue':
            ccode = '\033[94m'
        case 'green':
            ccode = '\033[92m'
        case _:
            ccode = '\033[0m'
    print(f"{ccode}{msg}{reset}")

def debug_timed(func):
    """
    import debug_timed from utils into any module
    add '@debug_timed` decorator to any method
    timing results will be logged along with the calling function and arguments that were passed


    """

    def wrapper(*args, **kwargs):
        if not G.master_instance:
            return func(*args, **kwargs)
        # Get caller frame
        caller_frame = inspect.stack()[1]
        caller_name = caller_frame.function
        start = time.perf_counter()
        result = func(*args, **kwargs)
        arg_strs = [repr(a) for a in args]
        kwarg_strs = [f"{k}={v!r}" for k, v in kwargs.items()]
        all_args = ", ".join(arg_strs + kwarg_strs)
        elapsed = (time.perf_counter() - start) * 1000

        logging.info(f"[TIMER] {elapsed:.2f} ms taken by {func.__name__} - called by {caller_name} ({all_args})")
        return result

    return wrapper

def debug_caller_args(color):
    frame = inspect.currentframe().f_back
    caller_frame = frame.f_back

    callee = frame.f_code.co_name
    caller = caller_frame.f_code.co_name if caller_frame else "<top-level>"

    args, _, _, values = inspect.getargvalues(frame)
    arg_list = ", ".join(f"{arg}={repr(values[arg])}" for arg in args)

    dbprint(color, f'"{callee}" called by "{caller}" Args: {arg_list}')

def millis() -> int:
    """return millisecond timer

    :return: milliseconds
    :rtype: int
    """
    return time.perf_counter_ns() // 1000000

def micros() -> int:
    """return microsecond timer

    :return: microseconds
    :rtype: int
    """
    return time.perf_counter_ns() // 1000


class AnsiColors:
    """ ANSI color codes """
    BLACK = "\033[30m"
    RED = "\033[38;5;160m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    PURPLE = "\033[35m"
    CYAN = "\033[36m"
    LIGHT_GRAY = "\033[37m"

    BLACKBG = "\033[40m"
    REDBG = "\033[48;5;160m"
    GREENBG = "\033[42m"
    YELLOWBG = "\033[43m"
    BLUEBG = "\033[44m"
    PURPLEBG = "\033[45m"
    CYANBG = "\033[46m"
    LIGHT_GRAYBG = "\033[47m"

    BRIGHT_REDBG = "\033[101m"

    GRAY = DARK_GRAY = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_BROWN = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_PURPLE = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    WHITE = "\033[97m"

    BOLD = "\033[1m"
    FAINT = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    BLINK = "\033[5m"
    NEGATIVE = "\033[7m"
    CROSSED = "\033[9m"
    END = "\033[0m"

    try:
        # cancel SGR codes if we don't write to a terminal
        if not __import__("sys").stdout.isatty():
            for _ in dir():
                if isinstance(_, str) and _[0] != "_":
                    locals()[_] = ""
        else:
            # set Windows console in VT mode
            if __import__("platform").system() == "Windows":
                kernel32 = __import__("ctypes").windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                del kernel32
    except: pass

def parseAnsiText(ansi_text):
    parsed = stransi.Ansi(ansi_text)
    current_format = QTextCharFormat()
    output = []
    for i in parsed.instructions():
        if isinstance(i, stransi.SetColor):
            if not i.color:
                current_format = QTextCharFormat()
            else:
                rgb = i.color.hex
                if i.role == stransi.color.ColorRole.BACKGROUND:
                    current_format.setBackground(QColor(rgb.hex_code))
                elif i.role ==  stransi.color.ColorRole.FOREGROUND:
                    current_format.setForeground(QColor(rgb.hex_code))
        elif isinstance(i, stransi.SetAttribute):
            match i.attribute:
                case stransi.attribute.Attribute.BOLD:
                    current_format.setFontWeight(100)
                case stransi.attribute.Attribute.DIM:
                    cl = current_format.foreground().color()
                    cl.setAlpha(128)
                    current_format.setForeground(cl)
                case stransi.attribute.Attribute.NEITHER_BOLD_NOR_DIM:
                    current_format.clearProperty(QTextCharFormat.Property.FontWeight)
                    current_format.clearForeground()
                case stransi.attribute.Attribute.ITALIC:
                    current_format.setFontItalic(True)
                case stransi.attribute.Attribute.NOT_ITALIC:
                    current_format.setFontItalic(False)
                case stransi.attribute.Attribute.UNDERLINE:
                    current_format.setFontUnderline(True)
                case stransi.attribute.Attribute.NOT_UNDERLINE:
                    current_format.setFontUnderline(False)
                case stransi.attribute.Attribute.NORMAL:
                    current_format = QTextCharFormat()
        else:
            output.append((i, QTextCharFormat(current_format)))
    return output

class OutLog(QtCore.QObject):
    textReceived = QtCore.pyqtSignal(str)

    def __init__(self, edit, out=None, color=None):
        QtCore.QObject.__init__(self)

        """(edit, out=None, color=None) -> can write stdout, stderr to a
        QTextEdit.
        edit = QTextEdit
        out = alternate stream ( can be the original sys.stdout )
        color = alternate color (i.e. color stderr a different color)
        """
        self.edit = edit
        self.out = out
        self.color = QtGui.QColor(color) if color else None
        self.textReceived.connect(self.on_received, Qt.ConnectionType.QueuedConnection)
        self.log_paused = False

    def isatty(self):
        return False

    def toggle_pause(self):
        # Toggle the pause state
        self.log_paused = not self.log_paused

    def on_received(self, m):
        p = parseAnsiText(m)
        try:
            if self.color:
                tc = self.edit.textColor()
                self.edit.setTextColor(self.color)

            self.edit.moveCursor(QtGui.QTextCursor.MoveOperation.End)
            for text, char_format in p:
                self.edit.setCurrentCharFormat(char_format)
                self.edit.insertPlainText(text)

            if self.color:
                self.edit.setTextColor(tc)
        except:
            pass

    def write(self, m):
        try:
            if not self.log_paused:
                self.textReceived.emit(m)
        except:
            pass
        if self.out:
            self.out.write(m)

    def flush(self):
        pass


#: Records logged before _init_logging can run.  It needs the LogWindow
#: widget, so it cannot run until Qt is up - but startup logs plenty
#: before that, including the version banner and the DirectLink build
#: identity.  Without this those records went to the throwaway handler
#: logging.info() installs when root has none, and _init_logging's
#: handlers.clear() then dropped them: never written to the log file, so
#: the one line support always asks for was the one line not on disk.
_early_log_buffer: 'logging.handlers.MemoryHandler | None' = None
_early_log_replayed = False


def begin_early_logging():
    """Start buffering log records until the real handlers exist.

    Called before anything else in main() logs.  Holding a handler on
    root also stops logging.info() from installing its own, which is
    what produced the unformatted 'INFO:root:' lines."""
    global _early_log_buffer
    import atexit
    import logging.handlers
    if _early_log_buffer is not None:
        return
    # capacity is a flush trigger, not a cap on what is kept, and a
    # MemoryHandler with no target DISCARDS on flush - so it is set far
    # above any plausible startup, and flushLevel above CRITICAL so that
    # an early error does not trigger the same discard.
    buf = logging.handlers.MemoryHandler(capacity=100000,
                                         flushLevel=logging.CRITICAL + 1)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(buf)
    _early_log_buffer = buf
    atexit.register(flush_early_logging_to_stderr)


def replay_early_logging(target):
    """Hand the buffered records to the real handlers, timestamps intact.

    Called by _init_logging once they exist.  Nothing is re-formatted:
    each record carries its own creation time, so the order holds."""
    global _early_log_replayed
    buf = _early_log_buffer
    if buf is None or _early_log_replayed:
        return
    _early_log_replayed = True
    buf.setTarget(target)
    buf.flush()
    buf.close()


def flush_early_logging_to_stderr():
    """Last resort: print buffered records if the real handlers never came.

    Startup can exit before _init_logging - the single-instance mutex, the
    unsafe-location refusal - and those paths are exactly the ones whose
    reason someone needs.  Buffering them must not be what makes the
    reason disappear."""
    buf = _early_log_buffer
    if buf is None or _early_log_replayed or not buf.buffer:
        return
    # the original stderr, not sys.stderr: by now that may be the OutLog
    # wrapper around a widget that is being torn down.  A frozen windowed
    # build has neither, and print(file=None) would quietly go to stdout.
    stream = sys.__stderr__ or sys.stderr
    if stream is None:
        return
    try:
        for record in buf.buffer:
            print(f"{record.levelname}: {record.getMessage()}", file=stream)
    except Exception:
        pass


class DedupHandler(logging.Handler):
    """Handler that suppresses repeated log records and summarizes them.

    It forwards records to one or more inner handlers passed during
    construction. Thread-safe.

    Two suppression behaviors, both driven by a rolling window of the last
    ``period_seconds`` of records:

    - *Consecutive repeats*: the same message arriving back-to-back is emitted
      once, then a "(message repeated N times)" summary every ``period_seconds``
      and a final summary when a different message arrives. Same as the
      original single-message dedup.
    - *Repeating cycles*: a message that was already seen earlier in the window
      (e.g. A, B, A, B) confirms a cycle; the whole cycle is then summarized in
      a single line and subsequent repetitions of its members are suppressed
      (with a periodic update) until the log goes quiet for ``period_seconds``
      or a genuinely different message arrives.
    """

    #: max distinct messages listed in a cycle summary (more are folded into a
    #: "+N more" count)
    MAX_LISTED_TYPES = 5

    def __init__(self, handlers=None, period_seconds: float = 5.0):
        super().__init__()
        self.handlers = handlers or []
        self._lock = threading.Lock()
        self.period_seconds = float(period_seconds)
        # clock indirection so tests can advance time without sleeping
        self._clock = time.monotonic
        # rolling window of (timestamp, key) for the last period_seconds
        self._window: deque = deque()
        # keys already seen in the current window generation
        self._seen: set = set()
        # True once a cycle summary has been emitted for the current generation;
        # while set, cycle members are suppressed instead of forward-checked
        self._collapsed = False
        # per-key bookkeeping for summaries (last record, occurrence count,
        # first-seen timestamp, most-recent-seen timestamp)
        # _key_last_ts drives window-pruning so long-running cycles stay alive
        self._records: dict = {}
        self._counts: dict = {}
        self._first_ts: dict = {}
        self._key_last_ts: dict = {}
        self._last_key = None
        self._last_entry_ts = 0.0
        # consecutive-repetition bookkeeping (preserves the original
        # single-message "(repeated N times)" behavior)
        self._repeat_count = 0
        self._repeat_record = None
        self._repeat_first_ts = 0.0
        self._repeat_periodic_ts = 0.0
        # timestamp of the last emitted cycle summary (initial or periodic);
        # only refreshed when a summary is actually emitted
        self._last_cycle_ts = 0.0

    def _normalize_message(self, record: logging.LogRecord) -> str:
        """
        Return a normalized, *uncolored* message string for de-duplication.

        - First attempt to collapse msg+args via record.getMessage().
        - If that fails (e.g. bad % formatting), fall back to record.msg.
        - In either case, clear record.args so future getMessage() calls are safe.
        """
        # First, try to safely collapse msg+args into a single string
        try:
            txt = record.getMessage() or ""
            # Now that we've successfully formatted, freeze it and drop args
            record.msg = txt
            record.args = ()
        except Exception as e:
            # getMessage() itself failed (e.g. "not all arguments converted")
            # Fall back to the raw msg, but still clear args so it can't blow up later.
            record.args = ()
            try:
                txt = str(record.msg) if record.msg is not None else ""
            except Exception:
                # Last-resort fallback
                txt = f"<unformattable log message: {e}>"

        # At this point txt is *some* string, args is empty, so no more % formatting.
        parts = parseAnsiText(txt)
        return "".join(t for t, _ in parts)

    def _make_key(self, record: logging.LogRecord):
        # Key by level, logger name and normalized message
        return (record.levelno, record.name, self._normalize_message(record))

    def _make_summary_record(self, base_record: logging.LogRecord, repeated_count: int, periodic: bool = False) -> logging.LogRecord:
        if periodic:
            msg = f"{self._normalize_message(base_record)} (message repeated {repeated_count} times so far)"
        else:
            msg = f"{self._normalize_message(base_record)} (message repeated {repeated_count} times)"
        new_rec = logging.LogRecord(
            name=base_record.name,
            level=base_record.levelno,
            pathname=base_record.pathname,
            lineno=base_record.lineno,
            msg=msg,
            args=(),
            exc_info=None,
            func=base_record.funcName,
        )
        # preserve timestamp
        new_rec.created = base_record.created
        return new_rec

    def _make_cycle_summary_record(self, keys, periodic: bool = False) -> logging.LogRecord:
        """Build one record summarizing an entire repeated message cycle.

        ``keys`` is the list of distinct message keys (in first-seen order) in
        the current window. The summary lists up to ``MAX_LISTED_TYPES``
        messages and reports per-type occurrence counts, for example::

            Cycle detected: 24 messages across 2 types over the last 5s.
                - Start effect 8 (Sine) ("flapsmovement"): 12
                - Stop effect 8 (Sine) ("flapsmovement"): 12
                (see DEBUG for details)
        """
        if not keys:
            raise ValueError("no keys to summarize")
        listed = keys[:self.MAX_LISTED_TYPES]
        extra = len(keys) - len(listed)
        total = sum(self._counts.get(k, 0) for k in keys)
        suffix = " so far" if periodic else ""

        lines = [f"Cycle detected: {total} messages across {len(keys)} types over the last {self.period_seconds:g}s{suffix}."]
        for k in listed:
            lines.append(f"    - {self._normalize_message(self._records[k])}: {self._counts[k]}")
        if extra > 0:
            lines.append(f"    \u2026 +{extra} more")
        if logging.getLogger().getEffectiveLevel() > logging.DEBUG:
            lines.append("    (see DEBUG for details)")
        msg = "\n".join(lines)

        ref = self._records[keys[0]]
        new_rec = logging.LogRecord(
            name=ref.name,
            level=ref.levelno,
            pathname=ref.pathname,
            lineno=ref.lineno,
            msg=msg,
            args=(),
            exc_info=None,
            func=ref.funcName,
        )
        # preserve the original record's wall-clock timestamp
        new_rec.created = ref.created
        return new_rec

    def _prune_window(self, now):
        """Drop keys whose most-recent appearance is older than period_seconds.

        Uses ``_key_last_ts`` (not the first-seen timestamps in ``_window``)
        so that a long-running cycle whose members keep recurring stays in
        the window even after their first-seen entries have aged out.
        """
        cutoff = now - self.period_seconds
        self._key_last_ts = {
            k: ts for k, ts in self._key_last_ts.items() if ts >= cutoff
        }
        self._window = deque(
            (ts, k) for ts, k in self._window if k in self._key_last_ts
        )

    def _reset(self):
        self._window.clear()
        self._seen.clear()
        self._collapsed = False
        self._records.clear()
        self._counts.clear()
        self._first_ts.clear()
        self._key_last_ts.clear()
        self._last_key = None
        self._last_entry_ts = 0.0
        self._repeat_count = 0
        self._repeat_record = None
        self._repeat_first_ts = 0.0
        self._repeat_periodic_ts = 0.0
        self._last_cycle_ts = 0.0

    def _forward(self, record):
        for h in self.handlers:
            try:
                h.emit(record)
            except Exception:
                pass

    def _emit_summary_record(self, summary_record):
        for h in self.handlers:
            try:
                h.emit(summary_record)
            except Exception:
                pass

    def _distinct_keys_in_window(self):
        return list(dict.fromkeys(k for _, k in self._window))

    def emit(self, record: logging.LogRecord):
        try:
            key = self._make_key(record)
            now = self._clock()
            with self._lock:
                # The window is pruned by time only (period_seconds); a full reset
                # happens only after the log has been quiet for at least one
                # period, so an episode is over and the next identical message
                # should print normally again.
                self._prune_window(now)
                if self._last_entry_ts and (now - self._last_entry_ts) >= self.period_seconds:
                    self._reset()
                self._last_entry_ts = now

                if key == self._last_key and self._repeat_count and not self._collapsed:
                    # same message back-to-back, and no cycle in play yet: keep
                    # the original consecutive-repetition behavior
                    if self._repeat_count == 1:
                        self._repeat_first_ts = now
                    self._repeat_count += 1
                    self._repeat_record = record
                    if (now - self._repeat_periodic_ts) >= self.period_seconds:
                        self._emit_summary_record(
                            self._make_summary_record(record, self._repeat_count, periodic=True)
                        )
                        self._repeat_periodic_ts = now
                    return

                if key in self._seen:
                    self._counts[key] = self._counts.get(key, 0) + 1
                    self._key_last_ts[key] = now
                    if self._collapsed:
                        # cycle already summarized: refresh it periodically only;
                        # the interval is measured from the last emitted summary
                        if (now - self._last_cycle_ts) >= self.period_seconds:
                            self._emit_summary_record(
                                self._make_cycle_summary_record(self._distinct_keys_in_window(), periodic=True)
                            )
                            self._last_cycle_ts = now
                        return
                    # this arrival means a previously-seen message is recurring,
                    # i.e. a genuine multi-message cycle: summarize it once and
                    # stop forwarding its individual repetitions from here on
                    self._emit_summary_record(self._make_cycle_summary_record(self._distinct_keys_in_window()))
                    self._collapsed = True
                    self._last_cycle_ts = now
                    return

                # first time this key is seen within this episode

                # When a distinct message interrupts a run of consecutive
                # repeats (and no cycle collapse is in progress), emit the
                # original final summary for the interrupted message, mirroring
                # the original single-message dedup behavior.
                if (
                    not self._collapsed
                    and self._repeat_count > 1
                    and self._repeat_record is not None
                ):
                    self._emit_summary_record(
                        self._make_summary_record(
                            self._repeat_record, self._repeat_count, periodic=False
                        )
                    )

                # register this key in the rolling window
                self._window.append((now, key))
                self._seen.add(key)
                self._records[key] = record
                self._counts[key] = 1
                self._first_ts[key] = now
                self._key_last_ts[key] = now
                # a new distinct message starts fresh consecutive-repeat
                # bookkeeping for itself
                self._last_key = key
                self._repeat_count = 1
                self._repeat_record = record
                self._repeat_first_ts = now
                self._repeat_periodic_ts = now
                self._forward(record)
        except Exception:
            # In case of any failure in dedup logic, fallback to best-effort forwarding
            for h in self.handlers:
                try:
                    h.emit(record)
                except Exception:
                    pass

    def flush(self):
        # flush inner handlers if they support flush
        for h in self.handlers:
            try:
                h.flush()
            except Exception:
                pass

    def close(self):
        # flush a collapsed cycle summary before shutting down so the final
        # occurrence counts are not lost
        try:
            with self._lock:
                if self._collapsed and self._window:
                    summary = self._make_cycle_summary_record(self._distinct_keys_in_window())
                    for h in self.handlers:
                        try:
                            h.emit(summary)
                        except Exception:
                            pass
        except Exception:
            pass

        # close inner handlers
        for h in self.handlers:
            try:
                h.close()
            except Exception:
                pass

        super().close()


class LoggingFilter(logging.Filter):
    def __init__(self, keywords):
        self.keywords = keywords

    def filter(self, record):
        # Check if any of the keywords are present in the log message
        record.device_type = G.device_type
        for keyword in self.keywords:
            if keyword in record.getMessage():
                # If any keyword is found, prevent the message from being logged
                return False
        # If none of the keywords are found, allow the message to be logged
        return True
