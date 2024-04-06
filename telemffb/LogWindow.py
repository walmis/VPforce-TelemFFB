import telemffb.globals as G

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QAction, QHBoxLayout, QMainWindow, QPlainTextEdit, QPushButton, QSizePolicy, QSpacerItem, QVBoxLayout, QWidget

import logging

logger = logging.getLogger()

class LogWindow(QMainWindow):
    log_paused = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Log Console ({G._device_type})")
        self.resize(800, 500)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.widget = QPlainTextEdit(self.central_widget)
        self.widget.setMaximumBlockCount(20000)
        self.widget.setReadOnly(True)
        self.widget.setFont(QFont("Courier New"))

        layout = QVBoxLayout(self.central_widget)
        layout.addWidget(self.widget)

        # Create a menu bar
        menubar = self.menuBar()

        # Create a "Logging" menu
        logging_menu = menubar.addMenu("Logging Level")

        # Create "Debug Logging" action and add it to the "Logging" menu
        debug_action = QAction("Debug Logging", self)
        debug_action.triggered.connect(self.set_debug_logging)
        logging_menu.addAction(debug_action)

        # Create "Normal Logging" action and add it to the "Logging" menu
        normal_action = QAction("Normal Logging", self)
        normal_action.triggered.connect(self.set_info_logging)
        logging_menu.addAction(normal_action)

        # Create a QHBoxLayout for the buttons
        button_layout = QHBoxLayout()
        button_layout.addItem(QSpacerItem(20, 40, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.clear_button = QPushButton('Clear', self.central_widget)
        self.clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(self.clear_button)

        self.pause_button = QPushButton("Pause", self.central_widget)
        self.pause_button.clicked.connect(self.toggle_pause)
        button_layout.addWidget(self.pause_button)

        # Add the button layout to the main layout
        layout.addLayout(button_layout)

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def clear_log(self):
        self.widget.clear()

    def toggle_pause(self):
        # Implement the logic to toggle pause/unpause
        if self.log_paused:
            self.log_paused = False
            self.pause_button.setText("Pause")
        else:
            self.log_paused = True
            self.pause_button.setText("Resume")

    def set_debug_logging(self):
        logger.setLevel(logging.DEBUG)
        logging.info(f"Logging level set to DEBUG")

    def set_info_logging(self):
        logger.setLevel(logging.INFO)
        logging.info(f"Logging level set to INFO")