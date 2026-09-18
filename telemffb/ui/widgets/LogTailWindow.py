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


from PyQt6.QtGui import QFont, QIcon, QTextCursor
from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QWidget, QHBoxLayout, QVBoxLayout, QWidget, QMainWindow, QPlainTextEdit
import os


import os


class LogTailWindow(QMainWindow):
    def __init__(self, main_window, args):
        super(LogTailWindow, self).__init__()

        self.main_window = main_window
        self.setWindowTitle(f"Log File Monitor ({args.type})")
        # Construct the absolute path of the icon file
        #icon_path = os.path.join(script_dir, "image/vpforceicon.png")
        icon = QIcon(":/image/vpforceicon.png")
        self.setWindowIcon(icon)
        self.resize(800, 500)
        self.move(self.main_window.x() + 50, self.main_window.y() + 100)
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.log_tail_thread = self.main_window.log_tail_thread

        # Replicate the log_tab_widget contents in the new window
        self.log_widget = QPlainTextEdit(self.central_widget)
        self.log_widget.setReadOnly(True)
        self.log_widget.setFont(QFont("Courier New"))
        self.log_widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.clear_button = QPushButton("Clear", self.central_widget)
        self.toggle_button = QPushButton("Pause", self.central_widget)
        self.close_button = QPushButton("Close Window", self.central_widget)
        self.clear_button.clicked.connect(self.clear_log_widget)
        self.toggle_button.clicked.connect(self.toggle_log_tailing)
        self.close_button.clicked.connect(lambda: self.hide())

        # self.open_log_button.clicked.connect(self.toggle_log_window)

        # Layout for the new window
        layout = QVBoxLayout(self.central_widget)
        layout.addWidget(self.log_widget)
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.toggle_button)
        button_layout.addStretch()  # Add stretch to push the next button to the right
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

        self.log_tail_thread.log_updated.connect(self.update_log_widget)

    def toggle_log_tailing(self):
        if self.log_tail_thread.is_paused():
            self.log_tail_thread.resume()
            self.toggle_button.setText("Pause")
        else:
            self.log_tail_thread.pause()
            self.toggle_button.setText("Resume")

    def clear_log_widget(self):
        self.log_widget.clear()

    def update_log_widget(self, log_line):
        cursor = self.log_widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(log_line)
        self.log_widget.setTextCursor(cursor)
        self.log_widget.ensureCursorVisible()