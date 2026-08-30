# device_icon_widget.py

import sys


from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer, QPropertyAnimation, QRect, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPixmap, QEnterEvent, QPainter, QColor, QFont, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QMainWindow, QSizePolicy, QGraphicsOpacityEffect
)
import os

from telemffb import utils

ICON_SIZE = QSize(72, 72)

DEVICE_ICONS = {
    "joystick": ":/image/icon_joystick.png",
    "pedals": ":/image/icon_pedals.png",
    "collective": ":/image/icon_collective.png",
    "trimwheel": ":/image/icon_trimwheel.png",
}

STATUS_COLORS = {
    "normal": QColor(171, 55, 200, 255),   # purple
    "ok": QColor(0, 153, 76, 220),         # green
    "warning": QColor(204, 153, 0, 220),   # amber
    "error": QColor(204, 51, 51, 255),     # red
    "hover": QColor(120, 120, 120, 180),   # soft gray for hover
    "selected": QColor(0, 120, 215, 200)   # blue for active
}

STATUS_COLORS_DARK = {
    "normal": QColor(171, 55, 200, 255),
    "ok": QColor(0, 204, 102, 200),
    "warning": QColor(255, 204, 0, 200),
    "error": QColor(255, 77, 77, 255),
    "hover": QColor(180, 180, 180, 100),    # lighter gray in dark mode
    "selected": QColor(0, 120, 215, 200)
}

def device_status_state() -> str:
    """Derive the device panel state from the live device object.

    NOT_FOUND: the configured board never opened (zombie startup - the
    app runs, telemetry is processed, but no FFB can be sent until the
    board appears).
    RECONNECTING: the board was opened but its HID handle is dead
    (hot-unplug) - the reconnect backoff is running.
    ACTIVE: the handle is alive.

    Pure and side-effect free so it is testable without a window.
    """
    from telemffb.hw.ffb_rhino import HapticEffect  # local: keep import light
    dev = HapticEffect.device
    if dev is None:
        return "NOT_FOUND"
    if not dev.connected:
        return "RECONNECTING"
    return "ACTIVE"


class DeviceIconWidget(QWidget):
    clicked = pyqtSignal(str)

    FADE_MS = 1000     # the error border pulse's fade pace
    FLASH_MS = 40      # step pace of the finite attention flash

    def __init__(self, device_name, icon_path, parent=None):
        super().__init__(parent)
        self.status_color = STATUS_COLORS['normal']  # Default
        self.device_name = device_name
        self.icon_path = icon_path
        self.active = False
        self.hover = False
        self.pressed = False
        self._border_opacity = 1.0
        self.border_anim = None
        self.setFixedWidth(100)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        # Icon

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(ICON_SIZE)
        self.icon_label.setScaledContents(True)
        self._original_pixmap = QPixmap(icon_path).scaled(
            ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.icon_label.setPixmap(self._original_pixmap)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_opacity = QGraphicsOpacityEffect()
        self.icon_label.setGraphicsEffect(self.icon_opacity)

        self.icon_fade = QPropertyAnimation(self.icon_opacity, b"opacity")
        self.icon_fade.setStartValue(0.5)
        self.icon_fade.setEndValue(1.0)
        self.icon_fade.setDuration(self.FADE_MS)
        self.icon_fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.icon_fade.setLoopCount(1)
        self.icon_fade.setDirection(QPropertyAnimation.Direction.Forward)
        self.icon_fade.finished.connect(self._reverse_icon_fade)
        # finite attention flash (tint blinking; see flash())
        self._flash_timer = None
        self._flash_state = 0

        # Text.  Shown/hidden directly - no QGraphicsOpacityEffect: the
        # status updates restyle this label periodically, and repainting a
        # label through an opacity effect leaves a stale (blank) render
        # until something else forces a full repaint.  Size is retained
        # while hidden so the icon above never shifts.
        self.text_label = QLabel(self.device_name.capitalize(), self)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setFont(QFont("Top Secret", 12))
        self.text_label.setStyleSheet("color: #ab37c8;")
        policy = self.text_label.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.text_label.setSizePolicy(policy)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.insertSpacing(1, 6)  # Add 6px vertical space between icon and text
        layout.addWidget(self.text_label)

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._fade_text(False)  # Initially hidden

    def _reverse_icon_fade(self):
        direction = self.icon_fade.direction()
        self.icon_fade.setDirection(
            QPropertyAnimation.Direction.Backward
            if direction == QPropertyAnimation.Direction.Forward
            else QPropertyAnimation.Direction.Forward
        )
        self.icon_fade.start()

    #: one pulse of the attention flash, as brightness factors for
    #: QColor.lighter() - a ramp up and back down, stepped at FLASH_MS
    FLASH_CURVE = (108, 115, 125, 135, 148, 160, 171, 180,
                   171, 160, 148, 135, 125, 115, 108, 100)

    def flash(self, pulses=2):
        """A brief, finite brightness pulse of the icon tint to draw the
        eye - used when the hardware behind this icon changes.

        Implemented by re-tinting the pixmap (the same machinery as status
        colors), NOT the graphics-effect opacity: animating the opacity
        effect over a scaled pixmap misrenders the icon at the wrong
        size/position for the duration.  The error border pulse takes
        precedence and cancels a flash in progress.
        """
        if self.border_anim:
            return
        self._flash_state = pulses * len(self.FLASH_CURVE)
        if self._flash_timer is None:
            self._flash_timer = QTimer(self)
            self._flash_timer.setInterval(self.FLASH_MS)
            self._flash_timer.timeout.connect(self._flash_step)
        self._flash_timer.start()
        self._flash_step()

    def _flash_step(self):
        if self.border_anim or self._flash_state <= 0:
            self._end_flash()
            return
        self._flash_state -= 1
        factor = self.FLASH_CURVE[
            (len(self.FLASH_CURVE) - 1 - self._flash_state)
            % len(self.FLASH_CURVE)]
        color = (QColor(self.status_color).lighter(factor)
                 if factor > 100 else self.status_color)
        self.icon_label.setPixmap(self._tint_pixmap(self._original_pixmap, color))

    def _end_flash(self):
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_state = 0
        self.icon_label.setPixmap(
            self._tint_pixmap(self._original_pixmap, self.status_color))

    def set_label(self, text):
        """The text under the icon - the hardware holding this role, when
        known, in place of the generic role name."""
        self.text_label.setText(text or self.device_name.capitalize())

    def set_icon(self, icon_path):
        """Swap the icon artwork (the active device's icon choice - e.g.
        the yoke).  Returns True when this actually changed the image."""
        if icon_path == self.icon_path:
            return False
        pm = QPixmap(icon_path)
        if pm.isNull():
            return False
        self.icon_path = icon_path
        self._original_pixmap = pm.scaled(
            ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.icon_label.setPixmap(
            self._tint_pixmap(self._original_pixmap, self.status_color))
        return True

    def _fade_text(self, visible: bool):
        # Always show text if this widget is active
        self.text_label.setVisible(visible or self.active)

    def set_active(self, active: bool):
        self.active = active
        self._fade_text(self.hover)  # Maintain hover logic too
        self.update()

    def enterEvent(self, event: QEnterEvent):
        self.hover = True
        self._fade_text(True)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover = False
        self._fade_text(False)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.pressed = True
        self.icon_label.move(self.icon_label.x() + 2, self.icon_label.y() + 2)
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.pressed = False
        self.icon_label.move(self.icon_label.x() - 2, self.icon_label.y() - 2)
        self.clicked.emit(self.device_name)
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        should_draw_border = self.active or self.hover or self.border_anim is not None
        if not should_draw_border:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Color setup
        if self.active:
            pen_color = STATUS_COLORS["ok"]
            pen_color.setAlpha(int(200 * self.border_opacity))
        elif self.border_anim:
            pen_color = QColor(STATUS_COLORS["error"])
            pen_color.setAlpha(int(200 * self.border_opacity))
        else:
            pen_color = STATUS_COLORS["hover"]

        pen = painter.pen()
        pen.setColor(pen_color)
        pen.setWidth(2)
        painter.setPen(pen)

        rect = self.icon_label.geometry().adjusted(-4, 0, 4, 4)
        radius = 10

        # Top-left arc
        path_top_left = QPainterPath()
        path_top_left.arcMoveTo(rect.left(), rect.top(), radius * 2, radius * 2, 180)
        path_top_left.arcTo(rect.left(), rect.top(), radius * 2, radius * 2, 180, -90)
        painter.drawPath(path_top_left)

        # Top-right arc
        path_top_right = QPainterPath()
        path_top_right.arcMoveTo(rect.right() - 2 * radius, rect.top(), radius * 2, radius * 2, 90)
        path_top_right.arcTo(rect.right() - 2 * radius, rect.top(), radius * 2, radius * 2, 90, -90)
        painter.drawPath(path_top_right)

        # Bottom-left arc
        path_bot_left = QPainterPath()
        path_bot_left.arcMoveTo(rect.left(), rect.bottom() - 2 * radius, radius * 2, radius * 2, 180)
        path_bot_left.arcTo(rect.left(), rect.bottom() - 2 * radius, radius * 2, radius * 2, 180, 90)
        painter.drawPath(path_bot_left)

        # Bottom-right arc
        path_bot_right = QPainterPath()
        path_bot_right.arcMoveTo(rect.right() - 2 * radius, rect.bottom() - 2 * radius, radius * 2, radius * 2, 270)
        path_bot_right.arcTo(rect.right() - 2 * radius, rect.bottom() - 2 * radius, radius * 2, radius * 2, 270, 90)
        painter.drawPath(path_bot_right)

    def _tint_pixmap(self, base_pixmap: QPixmap, color: QColor) -> QPixmap:
        tinted = QPixmap(base_pixmap.size())
        tinted.fill(Qt.GlobalColor.transparent)

        painter = QPainter(tinted)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, base_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), color)
        painter.end()

        return tinted

    def set_status_color(self, color):
        if isinstance(color, str):
            color = STATUS_COLORS.get(color.lower(), STATUS_COLORS["normal"])

        self.status_color = color
        tinted = self._tint_pixmap(self._original_pixmap, self.status_color)
        self.icon_label.setPixmap(tinted)

        # Use RGBA string for precise styling (including alpha)
        r, g, b, a = color.red(), color.green(), color.blue(), color.alpha()
        alpha_f = a / 255.0
        self.text_label.setStyleSheet(
            f"color: rgba({r}, {g}, {b}, {alpha_f:.2f});"
        )

        if color == STATUS_COLORS["error"]:
            self._start_border_pulse()
        else:
            self._stop_border_pulse()
        self.update()

    def _start_border_pulse(self):
        if self.border_anim:
            return  # Already animating

        self.border_anim = QPropertyAnimation(self, b"border_opacity")
        self.border_anim.setStartValue(0.3)
        self.border_anim.setEndValue(1.0)
        self.border_anim.setDuration(600)
        self.border_anim.setLoopCount(-1)
        self.border_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.border_anim.valueChanged.connect(self.update)
        self.border_anim.start()
        self._end_flash()   # the pulse takes over the icon's appearance
        self.icon_fade.start()

    def _stop_border_pulse(self):
        if self.border_anim:
            self.border_anim.stop()
            self.border_anim = None
        self.border_opacity = 1.0
        self.icon_fade.stop()
        self.icon_opacity.setOpacity(1.0)
        self.update()

    def get_border_opacity(self):
        return self.border_opacity

    def set_border_opacity(self, value):
        self.border_opacity = value
        self.update()

    @pyqtProperty(float)
    def border_opacity(self):
        return self._border_opacity

    @border_opacity.setter
    def border_opacity(self, value):
        self._border_opacity = value
        self.update()



class DeviceIconPanel(QWidget):
    DeviceClicked = pyqtSignal(str)
    def __init__(self, parent=None):
        super().__init__(parent)

        self.icons = {}
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(5, 10, 5, 1)
        self.layout.setSpacing(5)

    def get_device_names(self) -> list[str]:
        return list(self.icons.keys())

    def set_devices(self, device_list):
        # Clear old
        for w in self.icons.values():
            self.layout.removeWidget(w)
            w.deleteLater()
        self.icons.clear()

        icon_count = 0

        for device in device_list:
            if device.lower() not in DEVICE_ICONS:
                continue
            icon_path =  DEVICE_ICONS[device.lower()]
            widget = DeviceIconWidget(device.lower(), icon_path)
            widget.clicked.connect(self.handle_icon_click)
            self.layout.addWidget(widget)
            self.icons[device.lower()] = widget
            icon_count += 1

        # Enforce minimum size based on icon width + spacing
        icon_width = 100
        spacing = self.layout.spacing()
        margins = self.layout.contentsMargins().left() + self.layout.contentsMargins().right()
        min_width = icon_count * icon_width + (icon_count - 1) * spacing + margins
        self.setMinimumWidth(min_width)

    def handle_icon_click(self, device_name):
        self.set_active_device(device_name)
        self.DeviceClicked.emit(device_name)

    def set_active_device(self, device_name):
        for name, widget in self.icons.items():
            widget.set_active(name == device_name)

    def set_device_status(self, device_name: str, status: str):
        # utils.dbprint("red", f"Setting status for >{device_name}< to {status}")

        dev_status = status
        if status == 'TIMEOUT':
            status = 'error'
        if status == 'ACTIVE':
            status = 'ok'
        if status == 'DISCONNECTED':
            status = 'warning'
        if status == 'RECONNECTING':
            status = 'warning'
        if status == 'NOT_FOUND':
            status = 'error'
        widget = self.icons.get(device_name.lower())
        if widget:
            widget.set_status_color(status)
            tooltip = {
                'ACTIVE': 'Device connected',
                'RECONNECTING': 'Device disconnected - reconnecting automatically',
                'NOT_FOUND': 'Configured device not found - check USB connection',
                'DISCONNECTED': 'Device disconnected',
            }.get(dev_status)
            if tooltip:
                widget.setToolTip(tooltip)

    def set_device_label(self, device_name: str, text: str):
        """The text under a role's icon: the hardware holding the role.
        Returns True when this actually changed what was displayed."""
        widget = self.icons.get(device_name.lower())
        if widget is None:
            return False
        changed = widget.text_label.text() != (
            text or widget.device_name.capitalize())
        widget.set_label(text)
        return changed

    def flash_device(self, device_name: str):
        widget = self.icons.get(device_name.lower())
        if widget:
            widget.flash()

    def set_device_icon(self, device_name: str, icon_path: str):
        """The icon artwork for a role ('' = the role's default icon).
        Returns True when the image actually changed."""
        widget = self.icons.get(device_name.lower())
        if widget is None:
            return False
        return widget.set_icon(
            icon_path or DEVICE_ICONS.get(device_name.lower(), ''))

    def update_device_status_icon(self, device_name, new_icon_path):
        if device_name.lower() in self.icons:
            widget = self.icons[device_name.lower()]
            pixmap = QPixmap(new_icon_path).scaled(ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            widget.icon_label.setPixmap(pixmap)



if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton


    # from device_icon_widget import DeviceIconPanel

    class TestWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Device Icon Widget Test")
            # self.setMinimumSize(700, 300)

            central = QWidget()
            self.setCentralWidget(central)

            layout = QVBoxLayout(central)

            self.device_panel = DeviceIconPanel()
            self.device_panel.set_devices(["joystick", "pedals", "collective", "trimwheel"])

            # Buttons for test
            btn_bar = QHBoxLayout()
            for label in ["Normal", "OK", "Warning", "Error"]:
                btn = QPushButton(label)
                btn.setMaximumWidth(150)
                btn.clicked.connect(lambda _, l=label: self.set_status_all(l.lower()))
                btn_bar.addWidget(btn)

            layout.addWidget(self.device_panel)
            layout.addLayout(btn_bar)

        def set_status_all(self, status):
            for icon in self.device_panel.icons.values():
                icon.set_status_color(status)

    from PyQt6 import QtGui
    app = QApplication(sys.argv)
    app.setStyle('fusion')  # Set Fusion style
    app.setFont(QFont('Segoe UI', 10))
    global useDarkMode


    def _setup_theme_and_styling(app):
        global useDarkMode
        """
        Configure application theme and styling based on system settings.

        Flow:
        1. Read theme preference (light/dark/system)
        2. Set Qt color scheme accordingly
        3. Create custom palette with accent colors
        4. Apply dark mode palette if needed
        5. Apply custom stylesheets
        """
        theme_setting = 1

        match theme_setting:
            case 0:  # Light Mode
                app.styleHints().setColorScheme(Qt.ColorScheme.Light)
                useDarkMode = False
            case 1:  # Dark Mode
                app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
                useDarkMode = True
            case 2:  # System Controlled
                windows_mode = app.styleHints().colorScheme()
                if windows_mode == Qt.ColorScheme.Light:
                    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
                    useDarkMode = False
                else:
                    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
                    useDarkMode = True

        # Create and set custom palette with accent color
        palette = app.palette()
        accent_color = QtGui.QColor('#9430ad')
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent_color)
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor('white'))
        palette.setColor(QtGui.QPalette.ColorRole.Link, accent_color)
        app.setPalette(palette)

        if useDarkMode:
            _apply_dark_mode_palette(app, palette)


    def _apply_dark_mode_palette(app, palette):
        """Apply dark mode color palette."""
        # Base colors with updated ColorRole enums
        palette.setColor(QtGui.QPalette.ColorRole.Window, QColor(53, 53, 53))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#dddddd"))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QColor(35, 35, 35))
        palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor('#dddddd'))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor('#dddddd'))
        palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#cccccc"))
        palette.setColor(QtGui.QPalette.ColorRole.Button, QColor(53, 53, 53))
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor('#dddddd'))
        palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor('red'))

        # Disabled colors
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, QColor(127, 127, 127))
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.ButtonText, QColor(127, 127, 127))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QColor(43, 43, 43))  # or #2b2b2b
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor('#dddddd'))

        app.setPalette(palette)
        STATUS_COLORS = STATUS_COLORS_DARK


    _setup_theme_and_styling(app)
    win = TestWindow()
    win.show()
    sys.exit(app.exec())
