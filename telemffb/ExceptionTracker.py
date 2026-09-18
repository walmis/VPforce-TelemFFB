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

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List

import stransi

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass
class ExceptionRecord:
    """Records details about a logged exception."""
    timestamp: datetime
    message: str
    traceback: str
    level: str
    module: str
    count: int = 1
    
    def format_short(self) -> str:
        """Format a short summary for list display."""
        time_str = self.timestamp.strftime("%H:%M:%S")
        short_msg = self.message[:50]
        if len(self.message) > 50:
            short_msg = short_msg + '...'
        if self.count > 1:
            return f"[{time_str}] {self.module}: {short_msg} (x{self.count})"
        return f"[{time_str}] {self.module}: {short_msg}"
    
    def format_full(self) -> str:
        """Format full exception details."""
        time_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"Timestamp: {time_str}\n"
            f"Module: {self.module}\n"
            f"Level: {self.level}\n"
            f"Count: {self.count}\n"
            f"Message: {self.message}\n"
            f"{self.traceback}"
        )


class ExceptionLoggingHandler(logging.Handler):
    """Custom logging handler that captures exceptions and errors."""
    
    def __init__(self, tracker: 'ExceptionTracker'):
        super().__init__()
        self.tracker = tracker
        
    def emit(self, record):
        """Process log records and capture exceptions/errors."""
        # Only track ERROR and EXCEPTION level logs
        if record.levelno >= logging.ERROR:
            tb_text = ""
            if record.exc_info:
                # Use stackprinter for a debug-friendly traceback (source
                # context + local variable values). Falls back to the standard
                # traceback if stackprinter is unavailable or fails.
                import telemffb.utils as _utils
                tb_text = _utils.format_exception_stackprinter(record.exc_info)
            elif hasattr(record, 'exc_text') and record.exc_text:
                tb_text = record.exc_text
            # Strip ANSI color codes using stransi before storing
            def _strip_ansi_stransi(s: str) -> str:
                if not s:
                    return ""
                try:
                    parsed = stransi.Ansi(s)
                    parts = []
                    for ins in parsed.instructions():
                        if isinstance(ins, str):
                            parts.append(ins)
                    return "".join(parts)
                except Exception:
                    return s or ""

            msg = _strip_ansi_stransi(record.getMessage() or "")
            tb_text = _strip_ansi_stransi(tb_text or "")

            exc_record = ExceptionRecord(
                timestamp=datetime.fromtimestamp(record.created),
                message=msg,
                traceback=tb_text,
                level=_strip_ansi_stransi(record.levelname),
                module=record.name or record.module or "unknown"
            )
            self.tracker.add_exception(exc_record)


class ExceptionTracker(QObject):
    """Tracks exceptions and errors logged during application runtime."""
    
    exception_added = pyqtSignal()  # Emitted when a new exception is added
    exceptions_cleared = pyqtSignal()  # Emitted when exceptions are cleared
    
    def __init__(self):
        super().__init__()
        self.exceptions: List[ExceptionRecord] = []
        self.max_exceptions = 100  # Limit to prevent memory issues
        
    def add_exception(self, exc_record: ExceptionRecord):
        """Add an exception record to the tracker."""
        # If same exception already exists, increment its count.
        # Matching strategy: same module AND (traceback matches OR message matches).
        matched = None
        # Normalize incoming message for comparison
        incoming_msg = (exc_record.message or "").strip()

        # Check most recent entries first for performance
        for existing in reversed(self.exceptions):
            if existing.module != exc_record.module:
                continue

            # Prefer exact traceback match when available
            existing_tb = (existing.traceback or "").strip()
            incoming_tb = (exc_record.traceback or "").strip()
            if existing_tb and incoming_tb and existing_tb == incoming_tb:
                matched = existing
                break

            # Fall back to message match (helps aggregate exceptions where tracebacks are not present
            # or differ slightly but the message is identical)
            existing_msg = (existing.message or "").strip()
            if existing_msg and incoming_msg and existing_msg == incoming_msg:
                matched = existing
                break

        if matched:
            matched.count += 1
            matched.timestamp = exc_record.timestamp
            matched.message = exc_record.message
            # Move this record to the end (most recent)
            try:
                self.exceptions.remove(matched)
                self.exceptions.append(matched)
            except ValueError:
                pass
        else:
            self.exceptions.append(exc_record)

        # Limit the number of stored exceptions
        if len(self.exceptions) > self.max_exceptions:
            self.exceptions.pop(0)

        try:
            self.exception_added.emit()
        except RuntimeError:
            pass  # Qt object deleted during shutdown; nothing to do

        self._forward_to_master(exc_record)

    def _forward_to_master(self, exc_record: ExceptionRecord):
        """Child instances forward exceptions to the master over IPC so users
        running headless children still see the error notification (and the
        full record in the master's exception viewer).

        Never raises and never logs at ERROR level — an error here would
        re-enter this tracker via the logging handler.
        """
        try:
            import json

            import telemffb.globals as G
            if not G.child_instance or G.ipc_instance is None or not G.ipc_instance.running:
                return
            payload = {
                "device": G.device_type,
                "timestamp": exc_record.timestamp.isoformat(),
                "message": exc_record.message[:2000],
                "traceback": exc_record.traceback[:20000],
                "level": exc_record.level,
                "module": exc_record.module,
            }
            G.ipc_instance.send_message(f"EXCEPTION:{json.dumps(payload)}")
        except Exception:
            logging.debug("Failed to forward exception to master", exc_info=True)
        
    def remove_matching(self, message: str) -> int:
        """Remove records whose message matches the given flagged-error text.

        Used to auto-clear flag_error-sourced records when the underlying
        condition clears (the app-status message and tracker entry then agree).
        Matches the exact message and, because a child's error arrives at the
        master both device-prefixed (its own log of the merged telemetry) and
        raw (forwarded from the child's tracker), also the message with a
        leading "Device: " prefix stripped.

        Returns the number of records removed. Emits exceptions_cleared when
        anything was removed so the status-bar count refreshes.
        """
        msg = (message or "").strip()
        if not msg:
            return 0
        candidates = {msg}
        for dev in ("Joystick", "Pedals", "Collective", "Trimwheel"):
            prefix = f"{dev}: "
            if msg.startswith(prefix):
                candidates.add(msg[len(prefix):].strip())
        before = len(self.exceptions)
        self.exceptions = [e for e in self.exceptions
                           if (e.message or "").strip() not in candidates]
        removed = before - len(self.exceptions)
        if removed:
            try:
                self.exceptions_cleared.emit()
            except RuntimeError:
                pass  # Qt object deleted during shutdown; nothing to do
        return removed

    def get_count(self) -> int:
        """Get the number of tracked exceptions."""
        return len(self.exceptions)
    
    def get_exceptions(self) -> List[ExceptionRecord]:
        """Get all tracked exceptions."""
        return self.exceptions.copy()
    
    def clear(self):
        """Clear all tracked exceptions."""
        self.exceptions.clear()
        self.exceptions_cleared.emit()
        
    def get_handler(self) -> ExceptionLoggingHandler:
        """Get a logging handler for this tracker."""
        return ExceptionLoggingHandler(self)
