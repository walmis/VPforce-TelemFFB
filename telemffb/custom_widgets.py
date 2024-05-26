from PyQt5 import QtWidgets, QtCore
from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout, QScrollArea, QHBoxLayout, QSlider
from PyQt5.QtCore import pyqtSignal, Qt, QSize, QRect
from PyQt5.QtGui import QPixmap, QPainter, QColor, QCursor, QGuiApplication, QBrush, QPen
from PyQt5.QtWidgets import QStyle, QStyleOptionSlider

import telemffb.globals as G
from telemffb.utils import HiDpiPixmap

vpf_purple = "#ab37c8"   # rgb(171, 55, 200)
t_purple = QColor(f"#44{vpf_purple[-6:]}")

class NoKeyScrollArea(QScrollArea):
    def __init__(self):
        super().__init__()

        self.sliders = []
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)

    def addSlider(self, slider):
        self.sliders.append(slider)

    def keyPressEvent(self, event):
        # Forward keypress events to all sliders
        for slider in self.sliders:
            try:
                slider.keyPressEvent(event)
            except:
                pass

    def keyReleaseEvent(self, event):
        # Forward keypress events to all sliders
        for slider in self.sliders:
            try:
                slider.keyReleaseEvent(event)
            except:
                pass


class SliderWithLabel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(100)
        self.slider.setValue(50)
        self.slider.valueChanged.connect(self.updateLabel)

        self.label = QLabel(str(self.slider.value()))

        layout = QVBoxLayout(self)
        layout.addWidget(self.slider)
        layout.addWidget(self.label)

    def updateLabel(self, value):
        self.label.setText(str(value))

class NoWheelSlider(QSlider):
    def __init__(self, *args, **kwargs):
        super(NoWheelSlider, self).__init__(*args, **kwargs)
        # Default colors
        self.groove_color = "#bbb"
        self.handle_color = vpf_purple
        self.handle_height = 20
        self.handle_width = 16
        self.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
        # Apply styles
        self.update_styles()

        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.is_mouse_over = False

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ShiftModifier:
            # Adjust the value by increments of 1
            current_value = self.value()
            if event.angleDelta().y() > 0:
                new_value = current_value + 1
            elif event.angleDelta().y() < 0:
                new_value = current_value - 1

            # Ensure the new value is within the valid range
            new_value = max(self.minimum(), min(self.maximum(), new_value))

            self.setValue(new_value)
            event.accept()
        else:
            event.ignore()

    def update_styles(self):
        # Generate CSS based on color and size properties
        css = f"""
            QSlider::groove:horizontal {{
                border: 1px solid #565a5e;
                height: 8px;  /* Adjusted groove height */
                background: {self.groove_color};
                margin: 0;
                border-radius: 3px;  /* Adjusted border radius */
            }}
            QSlider::handle:horizontal {{
                background: {self.handle_color};
                border: 1px solid #565a5e;
                width: {self.handle_width}px;  /* Adjusted handle width */
                height: {self.handle_height}px;  /* Adjusted handle height */
                border-radius: {self.handle_height / 4}px;  /* Adjusted border radius */
                margin-top: -{self.handle_height / 4}px;  /* Negative margin to overlap with groove */
                margin-bottom: -{self.handle_height / 4}px;  /* Negative margin to overlap with groove */
                margin-left: -1px;  /* Adjusted left margin */
                margin-right: -1px;  /* Adjusted right margin */
            }}
        """
        self.setStyleSheet(css)

    def setGrooveColor(self, color):
        self.groove_color = color
        self.update_styles()

    def setHandleColor(self, color):
        self.handle_color = color
        self.update_styles()

    def setHandleHeight(self, height):
        self.handle_height = height
        self.update_styles()

    def enterEvent(self, event):
        self.is_mouse_over = True

    def leaveEvent(self, event):
        self.is_mouse_over = False

    def keyPressEvent(self, event):

        if self.is_mouse_over:
            # self.blockSignals(True)
            if event.key() == Qt.Key_Left:
                self.setValue(self.value() - 1)
            elif event.key() == Qt.Key_Right:
                self.setValue(self.value() + 1)
            else:
                super().keyPressEvent(event)
        # else:
        #     super().keyPressEvent(event)

    def keyReleaseEvent(self, event):

        if self.is_mouse_over:
            self.blockSignals(False)
            # if event.key() == Qt.Key_Left:
            #     self.valueChanged.emit(self.value() - 1)
            # elif event.key() == Qt.Key_Right:
            #     self.valueChanged.emit(self.value() + 1)

            pass
        else:
            super().keyReleaseEvent(event)


class NoWheelNumberSlider(QSlider):
    def __init__(self, *args, **kwargs):
        super(NoWheelNumberSlider, self).__init__(*args, **kwargs)
        # Default colors
        self.groove_color = "#bbb"
        self.handle_color = vpf_purple
        self.handle_height = 20
        self.handle_width = 32
        self.setCursor(QCursor(QtCore.Qt.PointingHandCursor))
        # Apply styles
        self.update_styles()
        #self.pct_max = 0
        self.value_text = ""  # Add an attribute to store the text to be shown in the handle

        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.is_mouse_over = False

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ShiftModifier:
            # Adjust the value by increments of 1
            current_value = self.value()
            if event.angleDelta().y() > 0:
                new_value = current_value + 1
            elif event.angleDelta().y() < 0:
                new_value = current_value - 1

            # Ensure the new value is within the valid range
            new_value = max(self.minimum(), min(self.maximum(), new_value))

            self.setValue(new_value)
            event.accept()
        else:
            event.ignore()

    def update_styles(self):
        # Generate CSS based on color and size properties
        css = f"""
            QSlider::groove:horizontal {{
                border: 1px solid #565a5e;
                height: 8px;  /* Adjusted groove height */
                background: {self.groove_color};
                margin: 0;
                border-radius: 3px;  /* Adjusted border radius */
            }}
            QSlider::handle:horizontal {{
                background: {self.handle_color};
                border: 1px solid #565a5e;
                width: {self.handle_width}px;  /* Adjusted handle width */
                height: {self.handle_height}px;  /* Adjusted handle height */
                border-radius: {self.handle_height / 4}px;  /* Adjusted border radius */
                margin-top: -{self.handle_height / 4}px;  /* Negative margin to overlap with groove */
                margin-bottom: -{self.handle_height / 4}px;  /* Negative margin to overlap with groove */
                margin-left: -1px;  /* Adjusted left margin */
                margin-right: -1px;  /* Adjusted right margin */
            }}
        """
        self.setStyleSheet(css)

    def setGrooveColor(self, color):
        self.groove_color = color
        self.update_styles()

    def setHandleColor(self, color, text=""):
        self.handle_color = color
        self.value_text = text
        self.update_styles()
        self.update()  # Ensure the slider is repainted to show the new text

    def setHandleHeight(self, height):
        self.handle_height = height
        self.update_styles()

    def enterEvent(self, event):
        self.is_mouse_over = True

    def leaveEvent(self, event):
        self.is_mouse_over = False

    def keyPressEvent(self, event):

        if self.is_mouse_over:
            # self.blockSignals(True)
            if event.key() == Qt.Key_Left:
                self.setValue(self.value() - 1)
            elif event.key() == Qt.Key_Right:
                self.setValue(self.value() + 1)
            else:
                super().keyPressEvent(event)
        # else:
        #     super().keyPressEvent(event)

    def keyReleaseEvent(self, event):

        if self.is_mouse_over:
            self.blockSignals(False)
            # if event.key() == Qt.Key_Left:
            #     self.valueChanged.emit(self.value() - 1)
            # elif event.key() == Qt.Key_Right:
            #     self.valueChanged.emit(self.value() + 1)

            pass
        else:
            super().keyReleaseEvent(event)

    def paintEvent(self, event):
        super(NoWheelNumberSlider, self).paintEvent(event)
        painter = QPainter(self)

        # Draw the handle with the gradient color
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle_rect = self.style().subControlRect(self.style().CC_Slider, option,
                                                  self.style().SC_SliderHandle, self)

        # Adjust the handle rect width to match the custom handle width
        handle_rect.setWidth(self.handle_width)

        # Calculate the correct position for the handle based on the slider value
        if self.orientation() == Qt.Horizontal:
            handle_x = self.style().sliderPositionFromValue(self.minimum(), self.maximum(), self.value(),
                                                            self.width() - self.handle_width)
            handle_rect.moveLeft(handle_x + 3)
        else:
            handle_y = self.style().sliderPositionFromValue(self.minimum(), self.maximum(), self.value(),
                                                            self.height() - self.handle_height)
            handle_rect.moveTop(handle_y)

        # Calculate the center of the handle rect
        handle_center = handle_rect.center()

        # Calculate the text width and height
        text_width = painter.fontMetrics().width(self.value_text)
        text_height = painter.fontMetrics().height()

        # Calculate the top-left position for the text to be centered
        text_x = handle_center.x() - text_width / 2
        text_y = handle_center.y() - text_height / 2

        # Set the text position within the handle
        text_rect = QRect(int(text_x), int(text_y), text_width, text_height)

        # Draw the text inside the handle
        painter.setPen(Qt.white)
        painter.drawText(handle_rect, Qt.AlignCenter, self.value_text)

    def initStyleOption(self, option):
        option.initFrom(self)
        option.subControls = QStyle.SC_SliderHandle | QStyle.SC_SliderGroove
        option.orientation = self.orientation()
        option.minimum = self.minimum()
        option.maximum = self.maximum()
        option.sliderPosition = self.sliderPosition()
        option.sliderValue = self.value()
        option.singleStep = self.singleStep()
        option.pageStep = self.pageStep()
        option.tickPosition = self.tickPosition()
        option.tickInterval = self.tickInterval()

class ClickLogo(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent=None):

        super(ClickLogo, self).__init__(parent)

        # Initial clickable state
        self._clickable = False

    def setClickable(self, clickable):
        self._clickable = clickable
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if self._clickable:
            self.clicked.emit()

    def enterEvent(self, event):
        if self._clickable:
            self.setCursor(Qt.PointingHandCursor)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)


class InfoLabel(QWidget):
    def __init__(self, text=None, tooltip=None, parent=None):
        super(InfoLabel, self).__init__(parent)

        # Text label
        self.text_label = QLabel(self)
        self.text_label.setText(text)
        self.text_label.setMinimumWidth(self.text_label.sizeHint().height())

        # Information icon
        self.icon_label = QLabel(self)
        # icon_img = os.path.join(script_dir, "image/information.png")
        icon_img = ":/image/information.png"
        self.pixmap = HiDpiPixmap(icon_img)
        self.icon_label.setPixmap(self.pixmap.scaledToHeight(self.text_label.sizeHint().height()))  # Adjust the height as needed
        self.icon_label.setVisible(False)

        # Layout to align the text label and icon
        self.layout = QHBoxLayout(self)
        self.layout.addWidget(self.text_label, alignment=Qt.AlignLeft)
        self.layout.addSpacing(0)
        self.layout.addWidget(self.icon_label, alignment=Qt.AlignLeft)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addStretch()

        # Set initial size for text_label based on the size of the icon
        # self.text_label.setFixedHeight(self.icon_label.height())

        if text:
            self.setText(text)
        if tooltip:
            self.setToolTip(tooltip)

    def setText(self, text):
        self.text_label.setText(text)
        # Adjust the size of text_label based on the new text
        # self.text_label.setFixedHeight(self.icon_label.height())

    def setToolTip(self, tooltip):
        if tooltip:
            self.icon_label.setToolTip(tooltip)
            self.icon_label.setVisible(True)
        else:
            self.icon_label.setToolTip('')
            self.icon_label.setVisible(False)

    def setTextStyleSheet(self, style_sheet):
        self.text_label.setStyleSheet(style_sheet)

    def show_icon(self):
        # Manually scale the pixmap to a reasonable size
        scaled_pixmap = self.pixmap.scaledToHeight(self.text_label.sizeHint().height())  # Adjust the height as needed
        self.icon_label.setPixmap(scaled_pixmap)


class StatusLabel(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, parent=None, text='', color: QColor = Qt.yellow, size=8):
        super(StatusLabel, self).__init__(parent)

        self.label = QLabel(text)
        self.label.setStyleSheet("QLabel { padding-right: 5px; }")

        self.dot_color = color  # Default color
        self.dot_size = size
        self.setCursor(Qt.PointingHandCursor)
        self._clickable = True
        self.setToolTip('Click to manage this device')
        layout = QHBoxLayout(self)
        layout.addWidget(self.label)

    def enterEvent(self, event):
        # Set the label to be blue and underlined when the mouse enters
        self.label.setStyleSheet("QLabel { padding-right: 5px; color: blue; text-decoration: underline; }")

    def leaveEvent(self, event):
        # Set the label back to its original style when the mouse leaves
        self.label.setStyleSheet("QLabel { padding-right: 5px; color: black; text-decoration: none; }")

    def mousePressEvent(self, event):
        if self._clickable:
            dev = self.label.text().lower()
            self.clicked.emit(dev)

    def hide(self):
        self.label.hide()
        super().hide()

    def show(self):
        self.label.show()
        super().show()

    def set_text(self, text):
        self.label.setText(text)

    def set_dot_color(self, color: QColor):
        self.dot_color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate adjusted positioning for the dot
        dot_x = self.label.geometry().right() - 1  # 5 is an arbitrary offset for better alignment
        dot_y = self.label.geometry().center().y() - self.dot_size // 2 + 1

        painter.setBrush(QColor(self.dot_color))
        painter.drawEllipse(dot_x, dot_y, self.dot_size, self.dot_size)

class SimStatusLabel(QWidget):
    def __init__(self, name : str):
        super().__init__()
        self.icon_size = QSize(18, 18)

        self._paused_state = False
        self._error_state = False
        self._active_state = False
        self._enabled_state = False

        self.lbl = QLabel(name)
        self.lbl.setStyleSheet("padding: 2px")
        self.lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.pix = QLabel()
        self.pix.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        enable_color = QColor(255,255,0)
        disable_color = QColor(128, 128, 128) # grey
        active_color = QColor(0, 255, 0)
        paused_color = QColor(0, 0, 255)
        error_color = QColor(255, 0, 0)

        self.enabled_pixmap = self.create_colored_icon(enable_color, self.icon_size)
        self.disabled_pixmap = self.create_x_icon(disable_color, self.icon_size)
        self.paused_pixmap = self.create_paused_icon(paused_color, self.icon_size)
        self.active_pixmap = self.create_colored_icon(active_color, self.icon_size)
        self.error_pixmap = self.create_x_icon(error_color, self.icon_size)

        h_layout = QtWidgets.QHBoxLayout()
        self.setLayout(h_layout)
        h_layout.addWidget(self.pix)
        h_layout.addWidget(self.lbl)

        self.update()

    @property
    def paused(self):
        return self._paused_state

    @paused.setter
    def paused(self, value):
        if self._paused_state != value:
            self._paused_state = value
            self.update()

    @property
    def error(self):
        return self._error_state

    @error.setter
    def error(self, value):
        if self._error_state != value:
            self._error_state = value
            self.update()

    @property
    def active(self):
        return self._active_state

    @active.setter
    def active(self, value):
        if self._active_state != value:
            self._active_state = value
            self.update()

    @property
    def enabled(self):
        return self._enabled_state

    @enabled.setter
    def enabled(self, value):
        if self._enabled_state != value:
            self._enabled_state = value
            self.update()

    def update(self):
        if self._error_state:
            self.pix.setPixmap(self.error_pixmap)
            self.setToolTip("Error condition: check log")
        elif self._paused_state:
            self.pix.setPixmap(self.paused_pixmap)
            self.setToolTip("Telemetry stopped or sim is paused")
        elif self._active_state:
            self.pix.setPixmap(self.active_pixmap)
            self.setToolTip("Sim is running, receiving telemetry")
        elif self._enabled_state:
            self.pix.setPixmap(self.enabled_pixmap)
            self.setToolTip("Sim is enabled, not receiving telemetry")
        else:
            self.pix.setPixmap(self.disabled_pixmap)
            self.setToolTip("Sim is disabled")


    def create_paused_icon(self, color, size):
        pixmap = HiDpiPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, 1)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, 1)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)

        # Draw two vertical lines for the pause icon
        line_length = int(size.width() / 3)
        line_width = 1
        line1_x = int((size.width() / 2) - 2)
        line2_x = int((size.width() / 2) + 2)
        line_y = int((size.height() - line_length) / 2)

        painter.setPen(QColor(Qt.white))
        painter.drawLine(line1_x, line_y, line1_x, line_y + line_length)
        painter.drawLine(line2_x, line_y, line2_x, line_y + line_length)

        painter.end()

        return pixmap

    def create_colored_icon(self, color, size : QSize):
        # Create a QPixmap with the specified color and size
        pixmap = HiDpiPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)

        painter.setRenderHint(QPainter.Antialiasing, 1)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, 1)
        painter.setBrush(color)
        painter.drawEllipse(QtCore.QRectF(2, 2, size.width() - 4, size.height() - 4))
        painter.end()

        return pixmap

    def create_x_icon(self, color, size):
        pixmap = HiDpiPixmap(size)
        pixmap.fill(Qt.transparent)

        # Draw a circle (optional)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, 1)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, 1)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, size.width() - 4, size.height() - 4)

        # Draw two vertical lines for the pause icon
        line_length = int(size.width() / 3)
        line_width = 1
        line1_x = int((size.width() / 2) - 2)
        line2_x = int((size.width() / 2) + 2)
        line_y = int((size.height() - line_length) / 2)

        painter.setPen(QColor(Qt.white))
        painter.drawLine(line1_x, line_y, line2_x, line_y + line_length)
        painter.drawLine(line2_x, line_y, line1_x, line_y + line_length)

        painter.end()

        return pixmap


class InstanceStatusRow(QWidget):
    changeConfigScope = QtCore.pyqtSignal(str)
    def __init__(self) -> None:
        super().__init__()

        self.instance_status_row = QHBoxLayout()
        self.master_status_icon = StatusLabel(None, f'This Instance({ G.device_type.capitalize() }):', Qt.green, 8)
        self.joystick_status_icon = StatusLabel(None, 'Joystick:', Qt.yellow, 8)
        self.pedals_status_icon = StatusLabel(None, 'Pedals:', Qt.yellow, 8)
        self.collective_status_icon = StatusLabel(None, 'Collective:', Qt.yellow, 8)

        self.status_icons = {
            "joystick" : self.joystick_status_icon,
            "pedals" : self.pedals_status_icon,
            "collective" : self.collective_status_icon
        }

        self.master_status_icon.clicked.connect(self.change_config_scope)
        self.joystick_status_icon.clicked.connect(self.change_config_scope)
        self.pedals_status_icon.clicked.connect(self.change_config_scope)
        self.collective_status_icon.clicked.connect(self.change_config_scope)

        self.instance_status_row.addWidget(self.master_status_icon)
        self.instance_status_row.addWidget(self.joystick_status_icon)
        self.instance_status_row.addWidget(self.pedals_status_icon)
        self.instance_status_row.addWidget(self.collective_status_icon)
        self.joystick_status_icon.hide()
        self.pedals_status_icon.hide()
        self.collective_status_icon.hide()

        self.instance_status_row.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        self.instance_status_row.setSpacing(10)

        self.setLayout(self.instance_status_row)

    def change_config_scope(self, val):
        self.changeConfigScope.emit(val)

    def set_status(self, device, status):
        status_icon = self.status_icons[device]
        if status == 'ACTIVE':
            status_icon.set_dot_color(Qt.green)
        elif status == 'TIMEOUT':
            status_icon.set_dot_color(Qt.red)
        else:
            status_icon.set_dot_color(Qt.yellow)
