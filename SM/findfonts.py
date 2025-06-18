import sys
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QLabel,
    QScrollArea, QFrame, QSpacerItem, QSizePolicy, QHBoxLayout
)
from PyQt6.QtGui import QFontDatabase, QFont
from PyQt6.QtCore import Qt


class FontPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Font Browser")
        self.resize(900, 700)

        layout = QVBoxLayout(self)

        self.input = QLineEdit("The quick brown fox jumps over the lazy dog")
        self.input.setPlaceholderText("Type text to preview...")
        self.input.textChanged.connect(self.update_previews)
        layout.addWidget(self.input)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        self.preview_container = QWidget()
        self.preview_layout = QVBoxLayout(self.preview_container)
        scroll_area.setWidget(self.preview_container)
        layout.addWidget(scroll_area)

        self.font_labels = []
        self.all_fonts = QFontDatabase.families()

        self.populate_font_previews(self.input.text())

    def populate_font_previews(self, text):
        for font_family in self.all_fonts:
            row = QHBoxLayout()

            font_name_label = QLabel(font_family)
            font_name_label.setFont(QFont("Arial", 12))
            font_name_label.setFixedWidth(200)
            font_name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            preview_label = QLabel(text)
            preview_label.setFont(QFont(font_family, 16))
            preview_label.setStyleSheet("padding: 6px;")
            preview_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            row.addWidget(font_name_label)
            row.addWidget(preview_label, 1)

            container = QWidget()
            container.setLayout(row)
            self.preview_layout.addWidget(container)

            self.font_labels.append((font_family, preview_label))

        self.preview_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

    def update_previews(self, new_text):
        for font_family, label in self.font_labels:
            label.setText(new_text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FontPreview()
    window.show()
    sys.exit(app.exec())