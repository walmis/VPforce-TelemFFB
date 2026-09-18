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

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMessageBox, QPushButton,
                             QSizePolicy, QSplitter, QTextEdit, QVBoxLayout)

from telemffb.ExceptionTracker import ExceptionTracker


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
