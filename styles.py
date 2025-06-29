"""
Stylesheet definitions for TelemFFB application.
Contains both dark mode and light mode stylesheets.
"""

DARK_MODE_STYLESHEET = """
QPushButton:!pressed, #styledButton:!pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #e4a9e7, stop: 0.2 #c174e6,
                                      stop: 0.5 #ab37c8, stop: 0.8 #8e1da8, stop: 1.0 #6e1d6f);
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    color: white;
    border: 1px solid #6e1d6f;
}

QPushButton:disabled:!pressed, #styledButton:disabled:!pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #e1e1e1, stop: 0.2 #cccccc,
                                      stop: 0.5 #bbbbbb, stop: 0.8 #aaaaaa, stop: 1.0 #999999);
    color: #666666;
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    border: 1px solid #999999;
}

QPushButton:pressed, #styledButton:pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #6e1d6f, stop: 0.2 #8e1da8,
                                      stop: 0.5 #ab37c8, stop: 0.8 #c174e6, stop: 1.0 #e4a9e7);
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    color: white;
    border: 1px solid #4e164e;
}

QPushButton:hover:!pressed, #styledButton:hover:!pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #f0b0f0, stop: 0.2 #d897d8,
                                      stop: 0.5 #c07ec0, stop: 0.8 #a965a9, stop: 1.0 #914b91);
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    border: 1px solid #8e1da8;
}

QLineEdit, QPlainTextEdit, QTextEdit {
    background-color: #3a3a3a;
    color: #ffffff;
    selection-background-color: #ab37c8;
}

QSlider::handle:horizontal {
    background: #ab37c8;
    border: 1px solid #565a5e;
    width: 16px;
    height: 20px;
    border-radius: 5px;
    margin-top: -5px;
    margin-bottom: -5px;
    margin-left: -1px;
    margin-right: -1px;
}

QSlider::handle:horizontal:disabled {
    background: #888888;
}

QSlider::groove:horizontal {
    border: 1px solid #333333;
    height: 8px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #5a5a5a, stop: 1 #3e3e3e
    );
    margin: 0;
    border-radius: 3px;
}

QMenuBar {
    background-color: #353535;
    color: #dddddd;
}

QMenuBar::item:selected {
    background-color: #ab37c8;
    color: white;
}

QMenuBar::item:pressed {
    background-color: #ab37c8;
    color: white;
}

QMenu {
    background-color: #2b2b2b;
    color: #dddddd;
    border: 1px solid #444444;
}

QMenu::item {
    padding: 6px 20px;
    background-color: transparent;
}

QMenu::item:selected {
    background-color: #ab37c8;
    color: white;
}

QFrame {
    background-color: #2b2b2b;
    color: #dddddd;
}

QComboBox {
    background-color: #3a3a3a;
    color: white;
    border: 1px solid #999;
}

QComboBox QAbstractItemView {
    background-color: #2b2b2b;
}

QCheckBox {
    color: white;
    spacing: 5px;
}

QCheckBox::indicator {
    border: 1px solid #888;
    background-color: transparent;
    border-radius: 3px;
}

QCheckBox::indicator:checked {
    background-color: #ab37c8;
    border: 1px solid #c174e6;
}

QCheckBox::indicator:unchecked:hover {
    border: 1px solid #ab37c8;
}

QCheckBox::indicator:disabled {
    background-color: #444;
    border: 1px solid #666;
}

QLabel {
    color: #dddddd;
    background: transparent;
}

"""

LIGHT_MODE_STYLESHEET = """
QPushButton:!pressed, #styledButton:!pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #e4a9e7, stop: 0.2 #c174e6,
                                      stop: 0.5 #ab37c8, stop: 0.8 #8e1da8, stop: 1.0 #6e1d6f);
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    color: white;
    border: 1px solid #6e1d6f;
}

QPushButton:disabled:!pressed, #styledButton:disabled:!pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #e1e1e1, stop: 0.2 #cccccc,
                                      stop: 0.5 #bbbbbb, stop: 0.8 #aaaaaa, stop: 1.0 #999999);
    color: #666666;
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    border: 1px solid #999999;
}

QPushButton:pressed, #styledButton:pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #6e1d6f, stop: 0.2 #8e1da8,
                                      stop: 0.5 #ab37c8, stop: 0.8 #c174e6, stop: 1.0 #e4a9e7);
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    color: white;
    border: 1px solid #4e164e;
}

QPushButton:hover:!pressed, #styledButton:hover:!pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #f0b0f0, stop: 0.2 #d897d8,
                                      stop: 0.5 #c07ec0, stop: 0.8 #a965a9, stop: 1.0 #914b91);
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    border: 1px solid #8e1da8;
}

QLineEdit, QPlainTextEdit, QTextEdit {
    selection-background-color: #ab37c8;
}

QSlider::handle:horizontal {
    background: #ab37c8;
    border: 1px solid #565a5e;
    width: 16px;
    height: 20px;
    border-radius: 5px;
    margin-top: -5px;
    margin-bottom: -5px;
    margin-left: -1px;
    margin-right: -1px;
}

QSlider::handle:horizontal:disabled {
    background: #888888;
}

QSlider::groove:horizontal {
    border: 1px solid #565a5e;
    height: 8px;
    background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                stop: 0 #e6e6e6, stop: 1 #bfbfbf);
    margin: 0;
    border-radius: 3px;
}

QMenuBar {
    background-color: #f0f0f0;
}

QMenu::item {
    background-color: transparent;
}

QMenu::item:selected {
    color: white;
    background-color: #ab37c8;
}
"""
