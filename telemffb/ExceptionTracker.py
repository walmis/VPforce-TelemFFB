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
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
import stransi

from PyQt6.QtCore import QObject, pyqtSignal, Qt, QThread
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QTextEdit, QWidget, QListWidget,
                             QListWidgetItem, QSplitter, QMessageBox, QProgressDialog, QSizePolicy)
from PyQt6.QtGui import QFont, QTextCursor


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
                tb_text = "".join(traceback.format_exception(*record.exc_info))
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


class ExceptionViewerDialog(QDialog):
    """Dialog for viewing logged exceptions with option to report them."""
    
    def __init__(self, tracker: ExceptionTracker, parent=None):
        super().__init__(parent)
        self.tracker = tracker
        self.setWindowTitle("Logged Exceptions")
        self.setMinimumSize(800, 600)
        self.setup_ui()
        self.populate_exceptions()
        
    def setup_ui(self):
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)
        
        # Header with count (compact)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(6, 6, 6, 6)
        header_layout.setSpacing(8)
        count = self.tracker.get_count()
        self.count_label = QLabel(f"Total Exceptions: {count}")
        # Slightly smaller and constrained so header doesn't grow vertically
        self.count_label.setStyleSheet("font-weight: bold; font-size: 10pt;")
        self.count_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.count_label.setMaximumHeight(24)
        header_layout.addWidget(self.count_label, 0)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        # Splitter for list and details
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Exception list
        self.exception_list = QListWidget()
        self.exception_list.setFont(QFont("Cascadia Code", 9))
        self.exception_list.currentItemChanged.connect(self.on_selection_changed)
        splitter.addWidget(self.exception_list)
        
        # Exception details
        self.details_text = QTextEdit()
        self.details_text.setReadOnly(True)
        self.details_text.setFont(QFont("Cascadia Code", 9))
        self.details_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        splitter.addWidget(self.details_text)
        
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        # Start with a reasonable default size for list and details
        try:
            splitter.setSizes([280, 520])
        except Exception:
            pass
        layout.addWidget(splitter)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(6, 6, 6, 6)
        button_layout.setSpacing(8)

        self.clear_button = QPushButton("Clear All")
        self.clear_button.setMaximumWidth(130)
        self.clear_button.clicked.connect(self.clear_exceptions)
        button_layout.addWidget(self.clear_button)

        self.report_button = QPushButton("Report Exceptions")
        self.report_button.setMaximumWidth(160)
        self.report_button.clicked.connect(self.report_exceptions)
        button_layout.addWidget(self.report_button)

        self.copy_button = QPushButton("Copy Selected")
        self.copy_button.setMaximumWidth(130)
        self.copy_button.clicked.connect(self.copy_selected)
        button_layout.addWidget(self.copy_button)

        self.copy_all_button = QPushButton("Copy All")
        self.copy_all_button.setMaximumWidth(130)
        self.copy_all_button.clicked.connect(self.copy_all)
        button_layout.addWidget(self.copy_all_button)

        button_layout.addStretch()

        self.close_button = QPushButton("Close")
        self.close_button.setMaximumWidth(100)
        self.close_button.clicked.connect(self.accept)
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)
        
    def populate_exceptions(self):
        """Populate the exception list."""
        self.exception_list.clear()
        for exc in self.tracker.get_exceptions():
            item = QListWidgetItem(exc.format_short())
            item.setData(Qt.ItemDataRole.UserRole, exc)
            self.exception_list.addItem(item)
            
        # Select the first item if available
        if self.exception_list.count() > 0:
            self.exception_list.setCurrentRow(0)
            
    def on_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        """Handle selection change in the exception list."""
        if current:
            exc = current.data(Qt.ItemDataRole.UserRole)
            if exc:
                self.details_text.setPlainText(exc.format_full())
        else:
            self.details_text.clear()
            
    def clear_exceptions(self):
        """Clear all exceptions after confirmation."""
        reply = QMessageBox.question(
            self,
            "Clear Exceptions",
            "Are you sure you want to clear all logged exceptions?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.tracker.clear()
            self.populate_exceptions()
            self.count_label.setText("Total Exceptions: 0")
            
    def report_exceptions(self):
        """Report exceptions by uploading support bundle to API.
        
        This method now delegates to the standalone report_exceptions function in utils.py.
        """
        from telemffb.utils import report_exceptions
        
        # Disable report button while running
        try:
            self.report_button.setEnabled(False)
        except Exception:
            pass
        
        # Callback to re-enable button when upload completes
        def on_complete(success, payload):
            try:
                self.report_button.setEnabled(True)
            except Exception:
                pass
        
        # Call the standalone function
        started = report_exceptions(
            parent_widget=self,
            on_complete_callback=on_complete
        )
        
        # Re-enable button if user cancelled before starting
        if not started:
            try:
                self.report_button.setEnabled(True)
            except Exception:
                pass
        
    def copy_selected(self):
        """Copy selected exception to clipboard."""
        current_item = self.exception_list.currentItem()
        if current_item:
            exc = current_item.data(Qt.ItemDataRole.UserRole)
            if exc:
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtGui import QGuiApplication
                clipboard = QGuiApplication.clipboard()
                if clipboard:
                    clipboard.setText(exc.format_full())
                    
                    # Show brief confirmation
                    self.details_text.moveCursor(QTextCursor.MoveOperation.Start)
                    original_text = self.details_text.toPlainText()
                    self.details_text.setPlainText("✓ Copied to clipboard!\n\n" + original_text)

    def copy_all(self):
        """Copy all exceptions to clipboard."""
        exceptions = self.tracker.get_exceptions()
        if not exceptions:
            return

        # Combine all exception details separated by a clear delimiter
        combined = "\n\n---\n\n".join(exc.format_full() for exc in exceptions)

        from PyQt6.QtGui import QGuiApplication
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(combined)

            # Show brief confirmation
            self.details_text.moveCursor(QTextCursor.MoveOperation.Start)
            original_text = self.details_text.toPlainText()
            self.details_text.setPlainText(f"✓ Copied {len(exceptions)} exception(s) to clipboard!\n\n" + original_text)
