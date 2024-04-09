import sys
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QScrollArea
from PyQt5.QtGui import QPainter, QPen, QLinearGradient, QColor, QFont
from PyQt5.QtCore import Qt, QPointF, QTimer
import random

class FrameTimeWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.max_frame_count = 100  # Maximum number of frames to display
        self.frame_times = []  # List to store frame times (capped at max_frame_count)
        self.max_time = 0  # Maximum frame time seen so far

    def add_frame_time(self, frame_time):
        self.frame_times.append(frame_time)
        self.max_time = max(self.max_time, frame_time)

        # Keep only the most recent max_frame_count entries
        self.frame_times = self.frame_times[-self.max_frame_count:]

        self.update()  # Trigger widget repaint

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.HighQualityAntialiasing)

        # Set background color
        painter.fillRect(self.rect(), Qt.white)

        # Draw vertical lines for each frame time
        if not self.frame_times:
            return  # No data to draw
        
        self.max_frame_count = int(self.width()/2.0)

        strokew = (self.width()) / (len(self.frame_times))
        print(strokew)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(1, Qt.green)
        gradient.setColorAt(0.5, QColor(128,200,0)) # Frame time of 10 corresponds to green
        gradient.setColorAt(0, Qt.red)
          # Frame time of 50 corresponds to yellow

        pen = QPen(gradient, strokew)
        painter.setPen(pen)

        max_time = max(self.max_time, 50)
        for i, frame_time in enumerate(self.frame_times):
            x = (i / (len(self.frame_times))) * self.width()
            y = self.height() - int((frame_time /(max_time)) * self.height())


            painter.drawLine(QPointF(x, self.height()), QPointF(x, y))

        pen = QPen(Qt.black)
        painter.setPen(pen)

        font = QFont("Courier New", 8)
        font.setBold(1)  # Example: Arial font, size 10
        painter.setFont(font)

        max_ = max(self.frame_times)
        avg_ = sum(self.frame_times) / len(self.frame_times)
        painter.drawText(1,10, f"max:{max_:.1f}ms")
        painter.drawText(1,20, f"avg:{avg_:.1f}ms")


if __name__ == '__main__':
    class MainWindow(QWidget):
        def __init__(self):
            super().__init__()

            self.scroll_area = QScrollArea()
            self.frame_time_widget = FrameTimeWidget()

            self.init_ui()

        def init_ui(self):
            layout = QVBoxLayout(self)
            self.setLayout(layout)

            self.scroll_area.setWidgetResizable(True)
            self.scroll_area.setWidget(self.frame_time_widget)

            add_button = QPushButton("Add Frame Time")
            add_button.clicked.connect(self.add_frame_time)

            layout.addWidget(self.scroll_area)
            layout.addWidget(add_button)

        def add_frame_time(self):
            # Here, you can add the frame time dynamically, replace 16 with your actual frame time value
            ft = 20+random.randint(0,5)
            if random.randint(0,1000) < 10:
                ft+=30
            self.frame_time_widget.add_frame_time(ft)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.setWindowTitle('Frame Times Widget')
    window.setGeometry(100, 100, 500, 400)
    window.show()
    
    def do_add():
        window.add_frame_time()

        QTimer.singleShot(20, do_add)

    QTimer.singleShot(20, do_add)

    sys.exit(app.exec_())