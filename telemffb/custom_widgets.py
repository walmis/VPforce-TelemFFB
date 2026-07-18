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
import os.path
from typing import Optional

from PyQt6.QtGui import QAction, QWheelEvent, QPalette

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QScrollArea, QHBoxLayout, QSlider, QCheckBox, QFrame, \
    QComboBox, QMessageBox, QMenu, QPushButton, QStyleOptionButton, QGridLayout, QGroupBox, QStackedLayout, QSizePolicy, \
    QGraphicsColorizeEffect
from PyQt6.QtCore import pyqtSignal, Qt, QSize, QRect, QPointF, QPropertyAnimation, QRectF, QPoint, \
    QSequentialAnimationGroup, QEasingCurve, pyqtSlot, pyqtProperty, QTimer, QAbstractAnimation
from PyQt6.QtGui import QPixmap, QPainter, QColor, QCursor, QGuiApplication, QBrush, QPen, QPaintEvent, QRadialGradient, \
    QLinearGradient, QFont
from PyQt6.QtWidgets import QStyle, QStyleOptionSlider

from PyQt6.QtCore import Qt

from PyQt6.QtCore import QAbstractListModel, QModelIndex

import numpy as np

import telemffb.globals as G
from telemffb.utils import HiDpiPixmap, Akima1DInterpolator, debug_caller_args
import styles

vpf_purple = "#ab37c8"   # rgb(171, 55, 200)
t_purple = QColor(f"#44{vpf_purple[-6:]}")


class FFBDeviceListModel(QAbstractListModel):
    """A simple list model exposing `telemffb.hw.ffb_rhino.DeviceInfo` entries.

    - DisplayRole returns a human-readable string for the device
    - UserRole (Qt.UserRole) returns the DeviceInfo instance
    """

    def __init__(self, devices=None, parent=None, include_none: bool = True):
        super().__init__(parent)
        self._devices = list(devices) if devices else []
        # include a dummy 'Not Selected' entry at index 0 when True
        self._include_none = bool(include_none)

    def rowCount(self, parent=QModelIndex()):
        return len(self._devices) + (1 if self._include_none else 0)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()

        # If include_none is enabled, row 0 is the dummy placeholder
        if self._include_none:
            if row == 0:
                if role == Qt.ItemDataRole.DisplayRole:
                    return "(None) - Not Selected"
                if role == Qt.ItemDataRole.UserRole:
                    return None
                return None
            # offset into actual devices
            row -= 1

        try:
            dev = self._devices[row]
        except IndexError:
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            # Friendly name: Config ident, vendor:product, serial
            try:
                vid = getattr(dev, 'vendor_id', 0)
                pid = getattr(dev, 'product_id', 0)
                ident = getattr(dev, 'ident', getattr(dev, 'product_string', 'Unknown'))
                serial = getattr(dev, 'serial_number', '')
                return f"{ident} ({vid:04X}:{pid:04X}) {serial}"
            except Exception:
                return str(getattr(dev, 'product_string', dev))

        if role == Qt.ItemDataRole.UserRole:
            return dev

        return None

    def update(self, devices):
        """Replace the device list and notify views."""
        self.beginResetModel()
        self._devices = list(devices) if devices else []
        self.endResetModel()

    def device_at(self, idx: int):
        """Return the DeviceInfo at the given model index, or None for the dummy entry or out of range."""
        if self._include_none:
            if idx == 0:
                return None
            idx -= 1

        if 0 <= idx < len(self._devices):
            return self._devices[idx]
        return None


class DetachedTabWindow(QtWidgets.QMainWindow):
    reattachRequested = pyqtSignal(str)  # emit the tab title

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self.setWindowTitle(title)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        # Same look your screenshot shows
        tb = self.addToolBar("Tab")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        tb.setIconSize(QtCore.QSize(16, 16))

        act = tb.addAction("Reattach")
        act.triggered.connect(lambda: self.reattachRequested.emit(self._title))

    def adopt_page(self, page: QtWidgets.QWidget):
        self.setCentralWidget(page)
        page.show()

    def release_page(self) -> QtWidgets.QWidget | None:
        page = self.takeCentralWidget()
        if page:
            page.setParent(None)
            page.show()
        return page

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        if self.centralWidget() is not None:
            self.reattachRequested.emit(self._title)
        super().closeEvent(e)

class ElidedLabel(QLabel):
    """QLabel that elides its text at a fixed pixel budget so long values
    can't grow the surrounding layout. When elided, the full text is shown
    in the tooltip; text() always returns the full string."""
    def __init__(self, text='', max_text_px=200, parent=None):
        super().__init__(parent)
        self._full_text = ''
        self._max_text_px = max_text_px
        if text:
            self.setText(text)

    def setText(self, text):
        self._full_text = text
        elided = self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, self._max_text_px)
        super().setText(elided)
        super().setToolTip(text if elided != text else '')

    def text(self):
        return self._full_text


class AppStatusWidget(QWidget):
    request_set_active_vpconf = pyqtSignal(str, bool)
    request_set_active_configurator = pyqtSignal(bool, bool)
    request_flag_error = pyqtSignal(str)
    request_clear_error = pyqtSignal()
    def __init__(self, master_instance=True, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.offline = False
        self.offline_recall_ac = ''
        self.offline_recall_ptn = ''
        self.offline_recall_pro = ''

        # connect signal to slot (QueuedConnection by default across threads)
        self.request_set_active_vpconf.connect(self.set_active_vpconf)
        self.request_set_active_configurator.connect(self.set_active_configurator)
        # flag_error/clear_error mutate the notification widget, a one-shot
        # change that never repaints if called from the telemetry thread.
        # Route through signals so the mutation runs on the GUI thread.
        self.request_flag_error.connect(self.flag_error)
        self.request_clear_error.connect(self.clear_error)

        grid = QGridLayout(self)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(10)
        # Pin the value column so the panel width is constant regardless of
        # content: every value widget's width is capped below this (elided
        # labels / chip budgets), so nothing can grow the column and shorter
        # values can't shrink it.
        grid.setColumnMinimumWidth(1, 280)

        row = 0
        label_align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        value_align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        # Item labels are dimmed (values full-brightness) so the label/value
        # distinction reads across the alignment gap. Derived from the active
        # theme's text color so it stays legible in both dark and light mode.
        dim = self.palette().color(QPalette.ColorRole.WindowText)
        dim_label_style = f"color: rgba({dim.red()}, {dim.green()}, {dim.blue()}, 150);"

        def make_item_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(dim_label_style)
            return lbl

        sim_status_header = InfoLabel()
        sim_status_header.text_label.setText('Sim Status')
        sim_status_header.text_label.setStyleSheet(dim_label_style)
        sim_status_header.setToolTip('Enabled Sims:\n  DCS\n  MSFS\n  XPLANE\n\nDisabled Sims:\n  IL2')

        self.sim_status_label = SimStatusWidget()
        # Semibold values against the dimmed labels: two-tier hierarchy.
        # Set via QFont (not stylesheet) so ElidedLabel's fontMetrics-based
        # elide budget accounts for the wider weight.
        value_font = QFont(self.font())
        value_font.setWeight(QFont.Weight.DemiBold)
        self.cur_craft_label = ElidedLabel("None Detected", max_text_px=260)
        self.cur_pattern_label = ElidedLabel("(No Match)", max_text_px=260)
        self.active_profile_label = ElidedLabel("(None)", max_text_px=180)
        for lbl in (self.cur_craft_label, self.cur_pattern_label, self.active_profile_label):
            lbl.setFont(value_font)
        # Pill styling for the vpconf/override labels; also used for the
        # "None" placeholder so the row height never changes.
        self._chip_style = """
                    QLabel {
                        padding: 2px 8px;
                        border-radius: 10px;
                        background-color: rgba(128,128,128, 100);
                        font-weight: bold;
                    }
                """

        self.active_vpconf_header = make_item_label('VPconf File')
        self.active_vpconf_label = QLabel('')
        self.active_vpconf_label.setStyleSheet(self._chip_style)
        self.active_vpconf_label.hide()
        self.active_vpconf_header.hide()

        self.active_configurator_header = make_item_label('Gains Ovd')
        self.active_configurator_label = QLabel('Active')
        self.active_configurator_label.setStyleSheet(self._chip_style)
        self.active_configurator_label.hide()
        self.active_configurator_header.hide()

        self.notification_label = QLabel('')
        self.notification_label.setWordWrap(True)
        self.notification_label.setMinimumHeight(60)
        size_policy = self.notification_label.sizePolicy()
        size_policy.setRetainSizeWhenHidden(True)
        self.notification_label.setSizePolicy(size_policy)
        self.notification_label.hide()
        self.notification_label.setStyleSheet("""
            QLabel {
                padding-left: 10px;
                padding-top: 2px;
                color: #ff6b6b;
                background-color: rgba(255, 50, 50, 30);
                border: 1px solid #c33;
                border-radius: 4px;
            }
        """)

        self.offline_label = QLabel('Telemetry is paused while in offline editing mode')
        self.offline_label.setWordWrap(True)
        self.offline_label.setMinimumHeight(60)
        self.offline_label.setSizePolicy(size_policy)
        self.offline_label.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 165, 0, 100);
                color: palette(windowText);
                padding: 6px 10px;
                font-weight: bold;
                border: 1px solid palette(dark);
                border-radius: 6px;
            }
        """)

        # Host the stacked layout in a real child widget so placeholder pages are never
        # created as transient top-level windows during construction.
        self.message_container = QWidget(self)
        self.message_stack = QStackedLayout(self.message_container)
        self.message_placeholder = QWidget(self.message_container)
        self.message_stack.addWidget(self.message_placeholder)  # Index 0
        self.message_stack.addWidget(self.notification_label)  # Index 1
        self.message_stack.addWidget(self.offline_label)  # Index 2
        self.message_stack.setCurrentIndex(0)

        # Layout content
        grid.addWidget(sim_status_header, row, 0, alignment=label_align)
        grid.addWidget(self.sim_status_label, row, 1, alignment=value_align)
        row += 1

        grid.addWidget(make_item_label("Current Aircraft"), row, 0, alignment=label_align)
        grid.addWidget(self.cur_craft_label, row, 1, alignment=value_align)
        row += 1

        grid.addWidget(make_item_label("Matched Model"), row, 0, alignment=label_align)
        grid.addWidget(self.cur_pattern_label, row, 1, alignment=value_align)
        row += 1

        self.cb_selectProfileCombo = QComboBox()
        self.cb_selectProfileCombo.addItems(['Select...'])

        profile_row_layout = QHBoxLayout()
        profile_row_layout.setContentsMargins(0, 0, 0, 0)
        profile_row_layout.setSpacing(6)
        profile_row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        profile_row_layout.addWidget(self.active_profile_label)
        profile_row_layout.addWidget(self.cb_selectProfileCombo)
        # Absorb the value column's spare width so the combo keeps its natural
        # size instead of stretching to fill the (fixed-width) cell.
        profile_row_layout.addStretch(1)

        grid.addWidget(make_item_label("Active Profile"), row, 0, alignment=label_align)
        grid.addLayout(profile_row_layout, row, 1)
        row += 1

        grid.addWidget(self.active_vpconf_header, row, 0, alignment=label_align)
        grid.addWidget(self.active_vpconf_label, row, 1, alignment=value_align)
        row += 1

        grid.addWidget(self.active_configurator_header, row, 0, alignment=label_align)
        grid.addWidget(self.active_configurator_label, row, 1, alignment=value_align)
        row += 1

        grid.addWidget(self.message_container, row, 0, 1, 2)

        if not master_instance:
            self.cb_selectProfileCombo.setDisabled(True)
            self.cb_selectProfileCombo.setVisible(False)

    def reset(self):
        self.offline = False
        self.sim_status_label.set_waiting()
        self.cur_craft_label.setText(self.offline_recall_ac)
        self.cur_pattern_label.setText(self.offline_recall_ptn)
        self.active_profile_label.setText(self.offline_recall_pro)
        self.offline_recall_ac = ''
        self.offline_recall_ptn = ''
        self.offline_recall_pro = ''
        self.message_stack.setCurrentIndex(0)


    def set_running(self, source):
        if self.offline: return
        self.sim_status_label.set_status(source, 'Running')
        self.cb_selectProfileCombo.setDisabled(False)
        self.message_stack.setCurrentIndex(0)
        self.pulse_label(self.sim_status_label.status_label, pulses=2, duration_ms=1000, color=QColor(0,200,0))

    def set_paused(self, source):
        if self.offline: return
        self.sim_status_label.set_status(source, 'Paused')
        self.cb_selectProfileCombo.setDisabled(False)
        self.message_stack.setCurrentIndex(0)
        self.pulse_label(self.sim_status_label.status_label, pulses=2, duration_ms=1000, color=QColor(255,200,0))

    def set_error(self, source):
        if self.offline: return
        self.sim_status_label.set_status(source, 'Error')
        self.cb_selectProfileCombo.setDisabled(False)
        self.pulse_label(self.sim_status_label.status_label, pulses=20000, color=QColor(200,0,0))

    def set_waiting(self, source):
        if self.offline: return
        self.sim_status_label.set_waiting()
        self.cb_selectProfileCombo.setDisabled(False)

    def set_offline(self, source):
        self.offline = True
        self.sim_status_label.set_status(source, 'Offline')
        self.offline_recall_ac = self.cur_craft_label.text()
        self.offline_recall_pro = self.cur_pattern_label.text()
        self.offline_recall_pro = self.active_profile_label.text()
        self.cur_craft_label.setText('Offline')
        self.cur_pattern_label.setText('Offline')
        self.active_profile_label.setText('Offline')
        self.cb_selectProfileCombo.setDisabled(True)
        self.message_stack.setCurrentIndex(2)
        self.pulse_label(self.sim_status_label.status_label, stop=True)

    def flag_error(self, message):
        # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!TESTING HERE: {message}')
        self.notification_label.setText(message)
        self.notification_label.show()
        self.message_stack.setCurrentIndex(1)


    def clear_error(self):
        self.notification_label.setText('')
        self.notification_label.hide()
        self.message_stack.setCurrentIndex(0)


    def set_fullname(self, full_name):
        self.cur_craft_label.setText(full_name)

    def set_match_pattern(self, pattern):
        self.cur_pattern_label.setText(pattern)

    def set_profile_name(self, profile_name):
        self.active_profile_label.setText(profile_name)

    def reset_sim_state(self, src: str):
        """Reset all status labels to their initial (no-sim) default values."""
        self.cur_craft_label.setText("None Detected")
        self.cur_pattern_label.setText("(No Match)")
        self.active_profile_label.setText("(None)")
        self.set_waiting(src)

    @pyqtSlot(str, bool)
    def set_active_vpconf(self, file, row_visible=True):
        if not file:
            # No profile pushed for the scoped device. Keep the row as a
            # "None" chip while any device is using the feature — same chip
            # styling as a real value so the row height is identical across
            # scopes — and hide it entirely when no device is. Placeholders
            # never pulse.
            self.active_vpconf_label.setText('None')
            self.active_vpconf_label.setToolTip('No vpconf profile has been pushed by TelemFFB for this device')
            self.active_vpconf_header.setVisible(row_visible)
            self.active_vpconf_label.setVisible(row_visible)
            return
        name = os.path.splitext(os.path.basename(file))[0]
        # Elide long profile names so the chip can't stretch the panel; the
        # tooltip carries the full path. The budget is the value column's
        # 280px minus the chip's horizontal padding, so the chip can use the
        # full width that is reserved for it anyway. Metrics use a bold font
        # to match the chip stylesheet's font-weight.
        font = QFont(self.active_vpconf_label.font())
        font.setBold(True)
        name = QtGui.QFontMetrics(font).elidedText(name, Qt.TextElideMode.ElideRight, 260)
        self.active_vpconf_label.setText(name)
        self.active_vpconf_label.setToolTip(f"Last profile pushed by TelemFFB:\n{file}")
        self.active_vpconf_header.setVisible(True)
        self.active_vpconf_label.setVisible(True)
        self.pulse_label(self.active_vpconf_label, color=QColor(0, 200, 0))

    @pyqtSlot(bool, bool)
    def set_active_configurator(self, active=True, row_visible=True):
        if active:
            self.active_configurator_label.setText('Active')
            self.active_configurator_label.setToolTip('The configurator gains have been modified from the currently\nactive configurator profile (if any)')
            self.active_configurator_header.setVisible(True)
            self.active_configurator_label.setVisible(True)
            self.pulse_label(self.active_configurator_label, color=QColor(0, 200, 0))
        else:
            self.active_configurator_label.setText('None')
            self.active_configurator_label.setToolTip('Configurator gains have been reset to those applied by\nthe current vpconf profile (if active) or the gains learned on startup')
            self.active_configurator_header.setVisible(row_visible)
            self.active_configurator_label.setVisible(row_visible)



    def update_enabled_sims(self, sim: str, state: bool):
        # Maintain state map across calls
        if not hasattr(self, "_sim_states"):
            self._sim_states = {
                "DCS": False,
                "MSFS": False,
                "XPLANE": False,
                "IL2": False,
                "BMS": False,
            }

        # Update the state for the provided sim
        if sim in self._sim_states:
            self._sim_states[sim] = state

        # Build tooltip content
        enabled = [s for s, enabled in self._sim_states.items() if enabled]
        disabled = [s for s, enabled in self._sim_states.items() if not enabled]

        tooltip = "Enabled Sims:\n"
        tooltip += "".join(f"  {s}\n" for s in sorted(enabled)) if enabled else "  (None)\n"
        tooltip += "\nDisabled Sims:\n"
        tooltip += "".join(f"  {s}\n" for s in sorted(disabled)) if disabled else "  (None)"

        # Set the updated tooltip
        self.findChild(InfoLabel).setToolTip(tooltip)

    def pulse_label(self, widget: QWidget, *, pulses: int | None = 3, duration_ms: int = 600, color: QColor | None = None, stop: bool = False, auto_stop_after_ms: int | None = None) -> None:
        """
        Briefly tint a widget with a pulse using a colorize effect.
        - pulses: how many up+down pulses
        - duration_ms: total time for one pulse (up&down)
        - color: optional QColor for the tint
        """
        # ---- STOP path ----
        if stop:
            effect = getattr(widget, "_pulse_effect", None)
            anim = getattr(widget, "_pulse_anim", None)
            if anim:
                anim.stop()
            if effect:
                effect.setStrength(0.0)
                widget.setGraphicsEffect(None)
            widget._pulse_effect = None
            widget._pulse_anim = None
            return

        if color is None:
            color = QColor(0, 200, 0)

            # restart cleanly if already pulsing
        self.pulse_label(widget, stop=True)

        # effect
        effect = QGraphicsColorizeEffect(widget)
        effect.setColor(color)
        effect.setStrength(0.0)
        widget.setGraphicsEffect(effect)
        widget._pulse_effect = effect

        # animation (one pulse 0 -> 1 -> 0)
        anim = QPropertyAnimation(effect, b"strength", widget)
        anim.setDuration(duration_ms)
        anim.setKeyValueAt(0.0, 0.0)
        anim.setKeyValueAt(0.5, 1.0)
        anim.setKeyValueAt(1.0, 0.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        if pulses is None:
            # Seamless infinite loop; use fallback if attr missing
            infinite = getattr(QAbstractAnimation, "Infinite", -1)
            anim.setLoopCount(infinite)
        else:
            anim.setLoopCount(max(1, int(pulses)))

            def cleanup():
                if getattr(widget, "_pulse_anim", None) is anim:
                    effect.setStrength(0.0)
                    widget.setGraphicsEffect(None)
                    widget._pulse_anim = None
                    widget._pulse_effect = None

            anim.finished.connect(cleanup)

        widget._pulse_anim = anim
        anim.start()

        if auto_stop_after_ms and auto_stop_after_ms > 0 and pulses is None:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(auto_stop_after_ms, lambda: self.pulse_label(widget, stop=True))


class SimStatusWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.sim_label = QLabel("Waiting...")
        self.status_label = QLabel("")
        self.status_label.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.sim_label)
        layout.addWidget(self.status_label)

        self._base_styles()

    def _base_styles(self):
        self.sim_label.setStyleSheet("QLabel { font-weight: bold; }")
        self.status_label.setStyleSheet("""
            QLabel {
                padding: 2px 8px;
                border-radius: 4px;
                color: white;
                font-weight: bold;
            }
        """)

    def set_waiting(self):
        self.sim_label.setText("Waiting...")
        self.status_label.setVisible(False)

    def set_status(self, sim_name: str, status: str):
        self.sim_label.setText(sim_name)
        self.status_label.setVisible(True)

        status_color = {
            "Running": "rgba(0, 200, 0, 150)",   # Green
            "Paused": "rgba(255, 200, 0, 150)",  # Yellow
            "Error": "rgba(255, 0, 0, 120)",      # Red
            "Offline": "rgba(128,128,128, 100)",  # Grey
        }.get(status, "rgba(120, 120, 120, 150)")

        self.status_label.setText(status)
        self.status_label.setStyleSheet(f"""
            QLabel {{
                padding: 2px 8px;
                border-radius: 10px;
                background-color: {status_color};
                font-weight: bold;
            }}
        """)

class StyledButton(QPushButton):
    """
    A QPushButton subclass that ensures consistent styling and sizing.

    This class is designed to be used with custom QSS (Qt Style Sheets)
    where button appearance is heavily styled (e.g., gradients, rounded borders, etc.).

    Key Features:
    - Ensures a minimum button width (default: 75px) so that short labels like "OK" or "Go"
      do not result in overly narrow buttons, which can look awkward or inconsistent.
    - Maintains the height determined by the base QPushButton and active style/theme.
    - Applies a specific object name ("styledButton") to associate with custom CSS rules.

    Usage:
    - Promote QPushButton widgets to StyledButton in Qt Designer.
    - Ensure stylesheet contains styles for `#styledButton` to take effect.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the object name to apply custom QSS styles targeting "#styledButton"
        self.setObjectName("styledButton")

    def sizeHint(self):
        """
        Returns the recommended size for the button, ensuring a minimum width
        while preserving default style-calculated height.
        """
        opt = QStyleOptionButton()
        self.initStyleOption(opt)

        # Calculate the styled button size from the current style
        style_size = self.style().sizeFromContents(
            QStyle.ContentsType.CT_PushButton,
            opt,
            super().sizeHint(),
            self
        )

        # Enforce a minimum width of 60px to match standard button sizing
        min_width = max(style_size.width(), 75)

        return QSize(min_width, super().sizeHint().height())

class NoKeyScrollArea(QScrollArea):
    def __init__(self):
        super().__init__()

        self.sliders = []
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

    def addSlider(self, slider):
        self.sliders.append(slider)


class SliderWithLabel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.slider = QSlider(Qt.Orientation.Horizontal)
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

class DelayTimerSlider(QSlider):
    delayedValueChanged = pyqtSignal(int)
    def __init__(self, *args, **kwargs):
        super(DelayTimerSlider, self).__init__(*args, **kwargs)
        self.checkbox : Optional[QCheckBox] = None # bound checkbox
        self.label : Optional[QLabel] = None # bound label
        self.setting_key : Optional[str] = None # bound setting key
        self.gain_id : Optional[int] = None # bound gain id

        self._delay = 150  # Delay in milliseconds
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._emitDelayedValueChanged)
        self.valueChanged.connect(self._startTimer)

    def _startTimer(self):
        self._timer.start(self._delay)

    def _emitDelayedValueChanged(self):
        self.delayedValueChanged.emit(self.value())

class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event: QWheelEvent):
        # Ignore the wheel event entirely
        event.ignore()

class NoWheelSlider(QSlider):
    delayedValueChanged = pyqtSignal(int)
    def __init__(self, *args, **kwargs):

        super(NoWheelSlider, self).__init__(*args, **kwargs)
        # Default colors
        self.groove_color = "#bbb"
        self.handle_color = vpf_purple
        self.handle_height = 20
        self.handle_width = 16
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        # Apply styles
        self.update_styles()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.is_mouse_over = False
        self._delay = 300  # Delay in milliseconds
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._emitDelayedValueChanged)
        self.valueChanged.connect(self._startTimer)

        self.setMinimumHeight(int(self.handle_height ) + 10)
    def _startTimer(self):
        self._timer.start(self._delay)

    def _emitDelayedValueChanged(self):
        self.delayedValueChanged.emit(self.value())

    def paintEvent(self, event):
        # super(NoWheelNumberSlider, self).paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # --- Draw groove manually ---
        groove_rect = QRectF()
        if self.orientation() == Qt.Orientation.Horizontal:
            groove_height = 10
            groove_y = (self.height() - groove_height) / 2
            groove_rect = QRectF(0, groove_y, self.width(), groove_height)
        else:
            groove_width = 8
            groove_x = (self.width() - groove_width) / 2
            groove_rect = QRectF(groove_x, 0, groove_width, self.height())

        palette = self.palette()
        base_color = palette.color(QPalette.ColorRole.Mid)
        highlight_color = palette.color(QPalette.ColorRole.Midlight)

        gradient = QLinearGradient(groove_rect.topLeft(), groove_rect.bottomLeft())
        gradient.setColorAt(0.0, base_color)
        gradient.setColorAt(1.0, highlight_color)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(groove_rect, 2, 2)

        # Style option for the handle
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self
        )

        # Adjust handle size
        handle_rect.setWidth(self.handle_width)
        handle_rect.setHeight(self.handle_height)

        # Calculate new handle position
        if self.orientation() == Qt.Orientation.Horizontal:
            handle_x = self.style().sliderPositionFromValue(
                self.minimum(), self.maximum(), self.value(), self.width() - self.handle_width)
            handle_rect.moveLeft(handle_x)

            # Vertical alignment fix: center handle on groove
            groove_y = (self.height() - 10) / 2  # groove_height is 10
            handle_rect.moveTop(int(groove_y + 10 / 2 - self.handle_height / 2))

        else:
            handle_y = self.style().sliderPositionFromValue(
                self.minimum(), self.maximum(), self.value(), self.height() - self.handle_height)
            handle_rect.moveTop(handle_y)

            # Horizontal alignment fix for vertical slider
            groove_x = (self.width() - 8) / 2  # groove_width is 8
            handle_rect.moveLeft(int(groove_x + 8 / 2 - self.handle_width / 2))

        # Draw custom gradient background
        # Shift center to upper-left
        cx = handle_rect.left() + handle_rect.width() * 0.3
        cy = handle_rect.top() + handle_rect.height() * 0.3

        # Increase radius for smoother falloff
        radius = max(handle_rect.width(), handle_rect.height())
        gradient = QRadialGradient(cx, cy, radius)

        lighter_val = 150 if G.useDarkMode else 150
        darker_val = 200 if G.useDarkMode else 150
        gradient.setColorAt(0.0, QColor(self.handle_color).lighter(lighter_val))
        gradient.setColorAt(0.15, QColor(self.handle_color))
        gradient.setColorAt(1.0, QColor(self.handle_color).darker(darker_val))

        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(self.handle_color).darker(120)))
        painter.drawRoundedRect(handle_rect, self.handle_height / 4, self.handle_height / 4)

        painter.end()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
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
            QSlider::handle:horizontal {{
                background: qradialgradient(
                    cx: 0.3, cy: 0.5, fx: 0.3, fy: 0.35, radius: 0.8,
                    stop: 0.0 #ffffff,
                    stop: 0.3 {self.handle_color},
                    stop: 1.0 {QColor(self.handle_color).darker().name()}
                );
                border: 1px solid #565a5e;
                width: {int(self.handle_width)}px;  /* Adjusted handle width */
                height: {int(self.handle_height)}px;  /* Adjusted handle height */
                border-radius: {int(self.handle_height / 4 )}px;  /* Adjusted border radius */
                margin-top: -{int(self.handle_height / 4 )}px;  /* Negative margin to overlap with groove */
                margin-bottom: -{int(self.handle_height / 4 )}px;  /* Negative margin to overlap with groove */
                margin-left: -1px;  /* Adjusted left margin */
                margin-right: -1px;  /* Adjusted right margin */
            }}
        """
        self.setStyleSheet(css)

    def increase_single_step(self):
        self.setValue(self.value() + self.singleStep())

    def decrease_single_step(self):
        self.setValue(self.value() - self.singleStep())

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
        self.setFocus()
        super().enterEvent(event)  # Call the default handler to ensure normal behavior

    def leaveEvent(self, event):
        self.clearFocus()
        super().leaveEvent(event)  # Call the default handler to ensure normal behavior


class NoWheelNumberSlider(NoWheelSlider):
    def __init__(self, *args, **kwargs):
        super(NoWheelNumberSlider, self).__init__(*args, **kwargs)
        self.handle_width = 32  # Different handle width for NoWheelNumberSlider
        self.value_text = ""  # Add an attribute to store the text to be shown in the handle
        self.update_styles()

    def setHandleColor(self, color, text=""):
        self.handle_color = color
        self.value_text = text
        self.update_styles()
        self.update()  # Ensure the slider is repainted to show the new text

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # --- Draw groove manually ---
        groove_rect = QRectF()
        if self.orientation() == Qt.Orientation.Horizontal:
            groove_height = 10
            groove_y = (self.height() - groove_height) / 2
            groove_rect = QRectF(0, groove_y, self.width(), groove_height)
        else:
            groove_width = 8
            groove_x = (self.width() - groove_width) / 2
            groove_rect = QRectF(groove_x, 0, groove_width, self.height())

        palette = self.palette()
        base_color = palette.color(QPalette.ColorRole.Mid)
        highlight_color = palette.color(QPalette.ColorRole.Midlight)

        gradient = QLinearGradient(groove_rect.topLeft(), groove_rect.bottomLeft())
        gradient.setColorAt(0.0, base_color)
        gradient.setColorAt(1.0, highlight_color)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(groove_rect, 2, 2)

        # Style option for the handle
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self
        )

        # Adjust handle size
        handle_rect.setWidth(self.handle_width)
        handle_rect.setHeight(self.handle_height)

        # Calculate new handle position
        if self.orientation() == Qt.Orientation.Horizontal:
            handle_x = self.style().sliderPositionFromValue(
                self.minimum(), self.maximum(), self.value(), self.width() - self.handle_width)
            handle_rect.moveLeft(handle_x)

            # Center handle vertically on groove
            groove_height = 10
            groove_y = (self.height() - groove_height) / 2
            handle_rect.moveTop(int(groove_y + groove_height / 2 - self.handle_height / 2))

        else:
            handle_y = self.style().sliderPositionFromValue(
                self.minimum(), self.maximum(), self.value(), self.height() - self.handle_height)
            handle_rect.moveTop(handle_y)

            # Center handle horizontally on groove
            groove_width = 8
            groove_x = (self.width() - groove_width) / 2
            handle_rect.moveLeft(int(groove_x + groove_width / 2 - self.handle_width / 2))

        # Draw custom gradient background
        # Shift center to upper-left
        cx = handle_rect.left() + handle_rect.width() * 0.25
        cy = handle_rect.top() + handle_rect.height() * 0.3

        center = handle_rect.center()
        gradient = QRadialGradient(center.x(), center.y(), handle_rect.width() / 2)

        radius = max(handle_rect.width(), handle_rect.height())
        gradient = QRadialGradient(cx, cy, radius)

        lighter_val = 150 if G.useDarkMode else 150
        darker_val = 200 if G.useDarkMode else 150
        gradient.setColorAt(0.0, QColor(self.handle_color).lighter(lighter_val))
        gradient.setColorAt(0.1, QColor(self.handle_color))
        gradient.setColorAt(1.0, QColor(self.handle_color).darker(darker_val))

        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(self.handle_color).darker(120)))
        painter.drawRoundedRect(handle_rect, self.handle_height / 4, self.handle_height / 4)

        # Draw the value text
        font = painter.font()
        font.setPointSize(font.pointSize() - 1)
        font.setBold(True)
        painter.setFont(font)


        painter.setPen(Qt.GlobalColor.black)
        painter.drawText(handle_rect, Qt.AlignmentFlag.AlignCenter, self.value_text)

        painter.end()

    def initStyleOption(self, option):
        option.initFrom(self)
        option.subControls = QStyle.SubControl.SC_SliderHandle | QStyle.SubControl.SC_SliderGroove
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
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if self._clickable:
            self.clicked.emit()

    def enterEvent(self, event):
        if self._clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)


class InfoLabel(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None, text=None, tooltip=None):
        super(InfoLabel, self).__init__(parent)
        self._clickable = False

        # Text label
        self.text_label = QLabel(self)
        self.text_label.setText(text)
        self.text_label.setMinimumWidth(self.text_label.sizeHint().height())

        # Information icon
        self.icon_label = QLabel(self)
        # icon_img = os.path.join(script_dir, "image/information.png")
        icon_img = ":/image/information.png"
        self.pixmap = HiDpiPixmap(icon_img)
        self.icon_label.setPixmap(self.pixmap._scaled(12, 12, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))  # Adjust the height as needed
        self.icon_label.setVisible(False)

        # Layout to align the text label and icon
        self.layout = QHBoxLayout(self)
        self.layout.addWidget(self.text_label, alignment=Qt.AlignmentFlag.AlignLeft)
        self.layout.addSpacing(0)
        self.layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignLeft)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addStretch()

        # Set initial size for text_label based on the size of the icon
        # self.text_label.setFixedHeight(self.icon_label.height())

        if text:
            self.setText(text)
        if tooltip:
            self.setToolTip(tooltip)

    def setClickable(self, clickable):
        self._clickable = clickable
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if self._clickable:
            self.clicked.emit()

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

class EraseButton(QPushButton):
    # Define the signals (you can connect these in the layout)
    move_to_class_signal = pyqtSignal(str, str, str, str, str, str)  # csim, cclass, setting, value, unit
    move_to_sim_signal = pyqtSignal(str, str, str, str, str)  # csim, model, setting, value, unit

    def __init__(self, *args, csim=None, cclass=None, cmodel=None, csetting=None, cvalue=None, cunit=None,
                 enable_context_menu=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.csim = csim
        self.cclass = cclass
        self.cmodel = cmodel
        self.csetting = csetting
        self.cvalue = cvalue
        self.cunit = cunit
        self.setProperty("buttonType", 'erase_button')

        self.enable_context_menu = enable_context_menu
        if self.enable_context_menu:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(
                lambda pos: self.show_context_menu(pos, csim, cclass)  #, cvalue, csetting, cmodel)
            )

    def show_context_menu(self,pos, csim, cclass):   # , cvalue, csetting, cmodel):
        menu = QMenu(self)

        action1 = QAction(f"Move setting to all {csim} {cclass} class aircraft", self)
        action1.triggered.connect(lambda: self.move_to_class_signal.emit(
            self.csim,
            self.cclass,
            self.cvalue,
            self.csetting,
            self.cmodel,
            self.cunit))
        if not G.settings_mgr.offline_scope:
            menu.addAction(action1)
        else:
            match G.settings_mgr.offline_scope:
                case 'MODEL':
                    menu.addAction(action1)
                case _:
                    pass

        action2 = QAction(f"Move setting to {csim} Sim for all aircraft", self)
        action2.triggered.connect(lambda: self.move_to_sim_signal.emit(
            self.csim,
            self.cvalue,
            self.csetting,
            self.cmodel,
            self.cunit))
        if not G.settings_mgr.offline_scope:
            menu.addAction(action2)
        else:
            match G.settings_mgr.offline_scope:
                case 'MODEL':
                    menu.addAction(action2)
                case 'CLASS':
                    menu.addAction(action2)
                case _:
                    pass

        # Show the menu at the global cursor position
        menu.exec(self.mapToGlobal(pos))


class StatusLabel(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, parent=None, text='', color: QColor = Qt.GlobalColor.yellow, size=10):
        super(StatusLabel, self).__init__(parent)

        self.label = QLabel(text)
        self.label.setObjectName("StatusLabel")

        self.dot_color = color  # Default color
        self.dot_size = size
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clickable = True
        self.setToolTip('Click to manage this device')
        layout = QHBoxLayout(self)
        layout.addWidget(self.label)

    def mousePressEvent(self, event):
        if self._clickable:
            dev = self.label.text().lower().replace(" ","")
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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Calculate adjusted positioning for the dot
        dot_x = self.label.geometry().right() - 1  # 5 is an arbitrary offset for better alignment
        dot_y = self.label.geometry().center().y() - self.dot_size // 2 + 1

        # Define thicknesses
        outer_black_thickness = 1
        ring_thickness = 2
        total_thickness = outer_black_thickness + ring_thickness

        # Adjust the size to include the rings
        total_size = self.dot_size + 2 * total_thickness

        # Draw the outermost black ring
        painter.setBrush(Qt.GlobalColor.black)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(dot_x - total_thickness, dot_y - total_thickness, total_size, total_size)

        # Draw the metallic grey ring
        ring_gradient = QRadialGradient(dot_x - total_thickness + total_size / 3,
                                        dot_y - total_thickness + total_size / 3, total_size / 2)
        ring_color = QColor(192, 192, 192)  # Metallic grey
        ring_gradient.setColorAt(0, ring_color.lighter(180))
        ring_gradient.setColorAt(0.35, ring_color)
        ring_gradient.setColorAt(1, ring_color.darker(200))

        painter.setBrush(ring_gradient)
        painter.drawEllipse(dot_x - total_thickness + outer_black_thickness,
                            dot_y - total_thickness + outer_black_thickness, total_size - 2 * outer_black_thickness,
                            total_size - 2 * outer_black_thickness)

        # Create a gradient for the dot with a 3D effect
        gradient = QRadialGradient(dot_x + self.dot_size / 3, dot_y + self.dot_size / 3, self.dot_size / 2)
        gradient.setColorAt(0, QColor(self.dot_color).lighter(180))  # Increase lightness for stronger highlight
        gradient.setColorAt(0.35, QColor(self.dot_color))  # Base color in the middle
        gradient.setColorAt(1, QColor(self.dot_color).darker(200))  # Increase darkness for stronger shadow

        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(dot_x, dot_y, self.dot_size, self.dot_size)

        painter.end()

class SimStatusLabel(QWidget):
    def __init__(self, name : str):
        super().__init__()
        self.icon_size = QSize(24, 24)

        self._paused_state = False
        self._error_state = False
        self._active_state = False
        self._enabled_state = False

        self.error_message = None

        self.lbl = QLabel(name)
        # font = QFont("xxxxxx", weight=QFont.Weight.Bold)
        #
        # # Set the font to the label
        # self.lbl.setFont(font)

        self.lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        self.pix = QLabel()
        self.pix.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        enable_color = QColor(255, 235, 0)
        disable_color = QColor(128, 128, 128) # grey
        active_color = QColor(23, 196, 17)
        paused_color = QColor(0, 0, 255)
        error_color = QColor(255, 0, 0)

        self.enabled_pixmap = self.create_status_icon(enable_color, self.icon_size, icon_type="colored")
        self.disabled_pixmap = self.create_status_icon(disable_color, self.icon_size, icon_type="x")
        self.active_pixmap = self.create_status_icon(active_color, self.icon_size, icon_type="colored")
        self.paused_pixmap = self.create_status_icon(paused_color, self.icon_size, icon_type="paused")
        self.error_pixmap = self.create_status_icon(error_color, self.icon_size, icon_type="exclamation")

        v_layout = QVBoxLayout()
        v_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.setLayout(v_layout)
        v_layout.addWidget(self.lbl)
        v_layout.addWidget(self.pix)

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
            self.error_message = None
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
            msg = self.error_message if self.error_message is not None else 'check log'
            self.setToolTip(f"Error condition: {msg}")
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

    def create_status_icon(self, color, size: QSize, icon_type="colored"):
        pixmap = HiDpiPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, 1)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, 1)

        # Define thicknesses
        outer_black_thickness = 1
        ring_thickness = 2
        inner_black_thickness = 1

        total_thickness = outer_black_thickness + ring_thickness + inner_black_thickness

        # Draw the outermost ring with gradient
        outer_ring_gradient = QRadialGradient(size.width() / 3, size.height() / 3, size.width() / 2)
        outer_ring_color = QColor(30, 30, 30)  # Dark grey for outer ring
        outer_ring_gradient.setColorAt(0, outer_ring_color.lighter(180))
        outer_ring_gradient.setColorAt(0.35, outer_ring_color)
        outer_ring_gradient.setColorAt(1, outer_ring_color.darker(200))

        painter.setBrush(outer_ring_gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size.width(), size.height())

        # Draw the metallic grey ring
        ring_gradient = QRadialGradient(size.width() / 3, size.height() / 3, size.width() / 2)
        ring_color = QColor(192, 192, 192)  # Metallic grey
        ring_gradient.setColorAt(0, ring_color.lighter(180))
        ring_gradient.setColorAt(0.35, ring_color)
        ring_gradient.setColorAt(1, ring_color.darker(200))

        painter.setBrush(ring_gradient)
        painter.drawEllipse(outer_black_thickness, outer_black_thickness, size.width() - 2 * outer_black_thickness,
                            size.height() - 2 * outer_black_thickness)

        # Draw the inner ring with gradient
        inner_ring_gradient = QRadialGradient(size.width() / 3, size.height() / 3, size.width() / 2)
        inner_ring_color = QColor(100, 100, 100)  # Grey for inner ring
        inner_ring_gradient.setColorAt(0, inner_ring_color.lighter(180))
        inner_ring_gradient.setColorAt(0.35, inner_ring_color)
        inner_ring_gradient.setColorAt(1, inner_ring_color.darker(200))

        painter.setBrush(inner_ring_gradient)
        painter.drawEllipse(outer_black_thickness + ring_thickness, outer_black_thickness + ring_thickness,
                            size.width() - 2 * (outer_black_thickness + ring_thickness),
                            size.height() - 2 * (outer_black_thickness + ring_thickness))

        # Draw the colored dot
        dot_gradient = QRadialGradient(size.width() / 3, size.height() / 3, size.width() / 2)
        dot_gradient.setColorAt(0, color)  # Increase lightness for stronger highlight
        dot_gradient.setColorAt(0.35, color)  # Base color in the middle
        dot_gradient.setColorAt(1, color)  # Increase darkness for stronger shadow

        painter.setBrush(dot_gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(total_thickness, total_thickness, size.width() - 2 * total_thickness,
                            size.height() - 2 * total_thickness)

        if icon_type == "paused":
            # Draw two vertical lines for the pause icon
            line_length = int(size.height() * 0.4)
            line_width = int(size.width() * 0.12)
            spacing = int(size.width() * 0.1)
            line1_x = int((size.width() / 2) - spacing)
            line2_x = int((size.width() / 2) + spacing)
            line_y = int((size.height() - line_length) / 2)

            # Draw the white pause lines
            painter.setPen(QPen(Qt.GlobalColor.white, line_width))
            painter.drawLine(line1_x, line_y, line1_x, line_y + line_length)
            painter.drawLine(line2_x, line_y, line2_x, line_y + line_length)

        elif icon_type == "x":
            # Draw two diagonal lines for the 'X' icon with shadow
            line_length = int(size.width() * 0.6)
            line_width = int(size.width() * 0.12)
            offset = int((size.width() - line_length) / 2)

            line1_start = QPointF(total_thickness + offset, total_thickness + offset)
            line1_end = QPointF(size.width() - total_thickness - offset, size.height() - total_thickness - offset)
            line2_start = QPointF(size.width() - total_thickness - offset, total_thickness + offset)
            line2_end = QPointF(total_thickness + offset, size.height() - total_thickness - offset)

            # Draw the white 'X' lines
            painter.setPen(QPen(Qt.GlobalColor.white, line_width))
            painter.drawLine(line1_start, line1_end)
            painter.drawLine(line2_start, line2_end)

        elif icon_type == "exclamation":

            # Draw an exclamation mark for the exclamation icon
            line_length = int(size.height() * 0.3)  # Adjusted to ensure the dot is distinct and separate
            line_width = int(size.width() * 0.15)
            dot_radius = int(size.width() * 0.05)  # Adjusted for a smaller dot
            line_x = int(size.width() / 2)
            line_y1 = int((size.height() - line_length - dot_radius * 2) / 2)
            line_y2 = line_y1 + line_length

            # Draw the white exclamation mark
            painter.setPen(QPen(Qt.GlobalColor.white, line_width))
            painter.drawLine(line_x, line_y1, line_x, line_y2)
            painter.setBrush(QBrush(Qt.GlobalColor.white))
            painter.drawEllipse(QPointF(line_x, line_y2 + dot_radius + 4), dot_radius, dot_radius)  # Move the dot down

        painter.end()

        return pixmap

class Toggle(QCheckBox):
    """Borrowed from qtwidgets library: https://github.com/pythonguis/python-qtwidgets
    Modified default behavior to support simple checkbox widget replacement in QT designer"""
    _transparent_pen = QPen(Qt.GlobalColor.transparent)
    _light_grey_pen = QPen(Qt.GlobalColor.lightGray)

    def __init__(self,
                 parent=None,
                 bar_color=QColor("#44ab37c8"),
                 checked_color="#ab37c8",
                 handle_color=Qt.GlobalColor.white,
                 disabled_color=Qt.GlobalColor.gray):
        super().__init__(parent)
        self.setStyleSheet("QCheckBox::indicator { width: 0px; height: 0px; }")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        # Save our properties on the object via self, so we can access them later
        # in the paintEvent.
        self._bar_color = bar_color
        self._checked_color = checked_color
        self._handle_color = handle_color
        self._disabled_color = QColor(disabled_color)

        self._bar_brush = QBrush(bar_color)
        self._bar_checked_brush = QBrush(QColor(checked_color).lighter())

        self._handle_brush = QBrush(handle_color)
        self._handle_checked_brush = QBrush(QColor(checked_color))

        # Setup the rest of the widget.
        self.setContentsMargins(8, 0, 8, 0)
        self._handle_position = 0
        self.setMaximumSize(QSize(45, 30))
        self.setMinimumSize(QSize(45, 30))

        self.stateChanged.connect(self.handle_state_change)

    def sizeHint(self):
        return QSize(58, 45)

    def hitButton(self, pos: QPointF):
        return self.contentsRect().contains(pos)

    def paintEvent(self, e: QPaintEvent):
        contRect = self.contentsRect()
        handleRadius = round(0.24 * contRect.height())

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        p.setPen(self._transparent_pen)
        barRect = QRectF(
            0, 0,
            contRect.width() - handleRadius, 0.40 * contRect.height()
        )
        barRect.moveCenter(QPointF(contRect.center()))
        rounding = barRect.height() / 2

        # the handle will move along this line
        trailLength = contRect.width() - 2 * handleRadius
        xPos = contRect.x() + handleRadius + trailLength * self._handle_position

        # Draw the bar with a subtle 3D sunken effect
        barGradient = QLinearGradient(0, 0, 0, barRect.height())
        barGradient.setStart(barRect.topLeft())
        barGradient.setFinalStop(barRect.bottomLeft())

        if not self.isEnabled():
            barGradient.setColorAt(0.0, self._disabled_color.lighter(150))
            barGradient.setColorAt(0.0, self._disabled_color)
            barGradient.setColorAt(1.0, self._disabled_color.darker(150))
        else:
            barGradient.setColorAt(0.0, self._bar_color.lighter(150))
            barGradient.setColorAt(0.5, self._bar_color)
            barGradient.setColorAt(1.0, self._bar_color.darker(150))

            if self.isChecked():
                barGradient.setColorAt(0.0, QColor(self._checked_color).lighter(150))
                barGradient.setColorAt(0.5, QColor(self._checked_color))
                barGradient.setColorAt(1.0, QColor(self._checked_color).darker(150))

        p.setBrush(QBrush(barGradient))
        p.drawRoundedRect(barRect, rounding, rounding)

        # Draw the border around the bar
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(barRect, rounding, rounding)

        if not self.isEnabled():
            handle_color = self._disabled_color.darker(110)
        elif self.isChecked():
            handle_color = self._handle_checked_brush.color()
        else:
            handle_color = self._handle_brush.color()

        # Draw the handle with a gradient for 3D effect
        handleGradient = QRadialGradient(
            QPointF(xPos - handleRadius / 3, barRect.center().y() - handleRadius / 3),
            handleRadius
        )

        if not self.isEnabled():
            handleGradient.setColorAt(0.0, handle_color.lighter(120))
            handleGradient.setColorAt(0.4, handle_color)
            handleGradient.setColorAt(1.0, handle_color.darker(130))
        elif self.isChecked():
            handleGradient.setColorAt(0.0, QColor(255, 255, 255, 180))
            handleGradient.setColorAt(0.3, handle_color)
            handleGradient.setColorAt(1.0, handle_color.darker(120))
        else:
            # OFF + Enabled: More subtle highlight
            handleGradient.setColorAt(0.0, handle_color.lighter(150))
            handleGradient.setColorAt(0.3, handle_color)
            handleGradient.setColorAt(1.0, handle_color.darker(300))

        p.setBrush(handleGradient)
        p.setPen(QPen(handle_color.darker()))
        p.drawEllipse(
            QPointF(xPos, barRect.center().y()),
            handleRadius, handleRadius)

        p.end()

    @pyqtSlot(int)
    def handle_state_change(self, value):
        self._handle_position = 1 if value else 0

    @pyqtProperty(float)
    def handle_position(self):
        return self._handle_position

    @handle_position.setter
    def handle_position(self, pos):
        """change the property
        we need to trigger QWidget.update() method, either by:
            1- calling it here [ what we're doing ].
            2- connecting the QPropertyAnimation.valueChanged() signal to it.
        """
        self._handle_position = pos
        self.update()

class LabeledToggle(QWidget):
    """Combo widget that creates a single widget with label and connectable slots using the Toggle widget"""
    stateChanged = pyqtSignal(int)  # Expose the stateChanged signal
    clicked = pyqtSignal(bool)      # Expose the clicked signal

    def __init__(self, parent=None, label=""):
        super().__init__(parent)

        self.toggle = Toggle(self)
        self.label = QLabel(label, self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignVCenter)  # Ensure the label is vertically centered

        layout = QHBoxLayout(self)
        layout.addWidget(self.toggle)
        layout.addWidget(self.label)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self.setLayout(layout)

        self.toggle.stateChanged.connect(self.stateChanged)  # Forward the stateChanged signal
        self.toggle.clicked.connect(self.clicked)  # Forward the clicked signal

    def isChecked(self):
        return self.toggle.isChecked()

    def setChecked(self, checked):
        self.toggle.setChecked(checked)

    def setText(self, text):
        self.label.setText(text)

    def connect(self, *args, **kwargs):
        return self.stateChanged.connect(*args, **kwargs)

    def checkState(self):
        return self.toggle.checkState()

    def setCheckState(self, state):
        self.toggle.setCheckState(state)

    def click(self):
        self.toggle.click()

class AnimatedToggle(QCheckBox):
    """Borrowed from qtwidgets library: https://github.com/pythonguis/python-qtwidgets"""
    _transparent_pen = QPen(Qt.GlobalColor.transparent)
    _light_grey_pen = QPen(Qt.GlobalColor.lightGray)

    def __init__(self,
        parent=None,
        bar_color=Qt.GlobalColor.gray,
        checked_color="#ab37c8",
        handle_color=Qt.GlobalColor.white,
        pulse_unchecked_color="#44999999",
        pulse_checked_color="#44#ab37c8"
        ):
        super().__init__(parent)

        # Save our properties on the object via self, so we can access them later
        # in the paintEvent.
        self._bar_brush = QBrush(bar_color)
        self._bar_checked_brush = QBrush(QColor(checked_color).lighter())

        self._handle_brush = QBrush(handle_color)
        self._handle_checked_brush = QBrush(QColor(checked_color))

        self._pulse_unchecked_animation = QBrush(QColor(pulse_unchecked_color))
        self._pulse_checked_animation = QBrush(QColor(pulse_checked_color))

        # Setup the rest of the widget.
        self.setContentsMargins(8, 0, 8, 0)
        self._handle_position = 0

        self._pulse_radius = 0

        self.animation = QPropertyAnimation(self, b"handle_position", self)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.setDuration(200)  # time in ms

        self.pulse_anim = QPropertyAnimation(self, b"pulse_radius", self)
        self.pulse_anim.setDuration(350)  # time in ms
        self.pulse_anim.setStartValue(10)
        self.pulse_anim.setEndValue(20)

        self.animations_group = QSequentialAnimationGroup()
        self.animations_group.addAnimation(self.animation)
        self.animations_group.addAnimation(self.pulse_anim)

        self.stateChanged.connect(self.setup_animation)

    def sizeHint(self):
        return QSize(58, 45)

    def hitButton(self, pos: QPoint):
        return self.contentsRect().contains(pos)

    @pyqtSlot(int)
    def setup_animation(self, value):
        self.animations_group.stop()
        if value:
            self.animation.setEndValue(1)
        else:
            self.animation.setEndValue(0)
        self.animations_group.start()

    def paintEvent(self, e: QPaintEvent):

        contRect = self.contentsRect()
        handleRadius = round(0.24 * contRect.height())

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        p.setPen(self._transparent_pen)
        barRect = QRectF(
            0, 0,
            contRect.width() - handleRadius, 0.40 * contRect.height()
        )
        barRect.moveCenter(contRect.center())
        rounding = barRect.height() / 2

        # the handle will move along this line
        trailLength = contRect.width() - 2 * handleRadius

        xPos = contRect.x() + handleRadius + trailLength * self._handle_position

        if self.pulse_anim.state() == QPropertyAnimation.Running:
            p.setBrush(
                self._pulse_checked_animation if
                self.isChecked() else self._pulse_unchecked_animation)
            p.drawEllipse(QPointF(xPos, barRect.center().y()),
                          self._pulse_radius, self._pulse_radius)

        if self.isChecked():
            p.setBrush(self._bar_checked_brush)
            p.drawRoundedRect(barRect, rounding, rounding)
            p.setBrush(self._handle_checked_brush)

        else:
            p.setBrush(self._bar_brush)
            p.drawRoundedRect(barRect, rounding, rounding)
            p.setPen(self._light_grey_pen)
            p.setBrush(self._handle_brush)

        p.drawEllipse(
            QPointF(xPos, barRect.center().y()),
            handleRadius, handleRadius)

        p.end()

    @pyqtProperty(float)
    def handle_position(self):
        return self._handle_position

    @handle_position.setter
    def handle_position(self, pos):
        """change the property
        we need to trigger QWidget.update() method, either by:
            1- calling it here [ what we doing ].
            2- connecting the QPropertyAnimation.valueChanged() signal to it.
        """
        self._handle_position = pos
        self.update()

    @pyqtProperty(float)
    def pulse_radius(self):
        return self._pulse_radius

    @pulse_radius.setter
    def pulse_radius(self, pos):
        self._pulse_radius = pos
        self.update()

class InstanceStatusRow(QWidget):
    changeConfigScope = QtCore.pyqtSignal(str)
    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.instance_status_row = QHBoxLayout()
        self.master_status_icon = StatusLabel(None, f'This Instance({ G.device_type.capitalize() }):', Qt.GlobalColor.green, 8)
        self.joystick_status_icon = StatusLabel(None, 'Joystick:', Qt.GlobalColor.yellow, 8)
        self.pedals_status_icon = StatusLabel(None, 'Pedals:', Qt.GlobalColor.yellow, 8)
        self.collective_status_icon = StatusLabel(None, 'Collective:', Qt.GlobalColor.yellow, 8)
        self.trimwheel_status_icon = StatusLabel(None, 'Trim Wheel:', Qt.GlobalColor.yellow, 8)

        self.status_icons = {
            "joystick" : self.joystick_status_icon,
            "pedals" : self.pedals_status_icon,
            "collective" : self.collective_status_icon,
            "trimwheel" : self.trimwheel_status_icon
        }

        self.master_status_icon.clicked.connect(self.change_config_scope)
        self.joystick_status_icon.clicked.connect(self.change_config_scope)
        self.pedals_status_icon.clicked.connect(self.change_config_scope)
        self.collective_status_icon.clicked.connect(self.change_config_scope)
        self.trimwheel_status_icon.clicked.connect(self.change_config_scope)

        self.instance_status_row.addWidget(self.master_status_icon)
        self.instance_status_row.addWidget(self.joystick_status_icon)
        self.instance_status_row.addWidget(self.pedals_status_icon)
        self.instance_status_row.addWidget(self.collective_status_icon)
        self.instance_status_row.addWidget(self.trimwheel_status_icon)
        self.joystick_status_icon.hide()
        self.pedals_status_icon.hide()
        self.collective_status_icon.hide()
        self.trimwheel_status_icon.hide()

        self.instance_status_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.instance_status_row.setSpacing(10)

        self.setLayout(self.instance_status_row)

    def change_config_scope(self, val):
        self.changeConfigScope.emit(val)

    def set_status(self, device, status):
        status_icon = self.status_icons[device]
        if status == 'ACTIVE':
            status_icon.set_dot_color(Qt.GlobalColor.green)
        elif status == 'TIMEOUT':
            status_icon.set_dot_color(Qt.GlobalColor.red)
        else:
            status_icon.set_dot_color(Qt.GlobalColor.yellow)


class CurveWidget(QWidget):
    modified = pyqtSignal()

    # Tolerance (in %) applied uniformly to every smooth-curve bounds check below,
    # to absorb floating point noise without letting real overshoot slip through.
    _BOUNDS_EPSILON = 0.1

    def __init__(self, parent=None, unit=None, base_unit=None):
        super().__init__(parent)
        # Default: 0% at 0 knots and 100% at 500 knots
        self.points = [QPointF(0, 0), QPointF(1, 100)]
        self.interaction_radius = 8
        self.dragging_point = None
        self.right_clicked_point = None
        self.smooth_curve_enabled = False
        self.x_min = 0
        self.x_max = 100
        # self.x_scale = 100  # Default range in base unit
        self.point_radius = 3
        self.margin = 50

        self.margin_top = 20  # Reduced space at the top
        self.margin_bottom = 30  # Reduced space at the bottom
        self.margin_left = 50  # Retain enough space for Y-axis labels
        self.margin_right = 20  # Minimal margin on the right

        self.setMinimumSize(400, 300)
        self.setWindowTitle("Curve Editor")

        # Units setup
        self.current_unit = unit
        self.base_unit = base_unit

        # message label
        self.msg_label = QLabel(self)
        if G.useDarkMode:
            self.msg_label.setStyleSheet("""
                QLabel {
                    background-color: #ffcccc;
                    color: #990000;
                    border: 1px solid #cc6666;
                    border-radius: 6px;
                    padding: 4px 8px;
                    font-weight: bold;
                }
            """)
        else:
            self.msg_label.setStyleSheet("""
                    QLabel {
                        background-color: #ffeeee;
                        color: #cc0000;
                        border: 1px solid #aa4444;
                        border-radius: 6px;
                        padding: 4px 8px;
                        font-weight: bold;
                    }
                """)
        self.msg_label.move(60, 40)
        self.msg_label.hide()

        self.coordinate_label = QLabel(self)
        bg = "#ffffff" if not G.useDarkMode else "#444444"
        fg = "black" if not G.useDarkMode else "white"
        border = "black" if not G.useDarkMode else "#888888"

        self.coordinate_label.setStyleSheet(f"background-color: {bg}; color: {fg}; border: 1px solid {border};")
        self.coordinate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.coordinate_label.setFixedSize(120, 20)  # Adjust size as needed
        self.coordinate_label.hide()  # Initially hidden

        self.test_point = None
        self.last_valid_position = None
        self._enabled = True  # Internal state to track enabled/disabled status

        self.x_label_text = "X:"
        #self.x_label_legend = "X:"  # Add to sublcass for specific Text
        self.y_label_text = "Y:"
        #self.y_label_legend = "Y:"  # Add to sublcass for specific Text
        dark_mode = G.useDarkMode

        # Axis and label color
        self.axis_color = QColor("white") if dark_mode else QColor("black")

        # Grid
        self.grid_color = QColor(100, 100, 100) if dark_mode else QColor(Qt.GlobalColor.lightGray)

        # Curve line
        self.curve_color = QColor("#ab37c8") if dark_mode else Qt.GlobalColor.blue

        # Point fill
        self.point_fill = QColor(255, 200, 200) if dark_mode else QColor(255, 0, 0)

        self.crosshair_color = Qt.GlobalColor.lightGray if G.useDarkMode else QColor("#ab37c8")

    def setEnabled(self, enabled: bool):
        """Enable or disable the widget."""
        self._enabled = enabled
        super().setEnabled(enabled)
        self.update()  # Trigger a repaint to reflect the new state

    def isEnabled(self):
        """Check if the widget is enabled."""
        return self._enabled

    def apply_disabled_overlay(self, painter):
        """Draw a semi-transparent overlay to indicate the widget is disabled."""
        painter.save()
        painter.setBrush(QColor(200, 200, 200, 128))  # Gray with 50% transparency
        painter.setPen(Qt.PenStyle.NoPen)
        rect = self.rect()
        painter.drawRect(rect)
        painter.restore()



    def _curve_bounds(self, x_values, y_values):
        """Return the (min, max) of the Akima-interpolated curve for the given points.

        Sample density scales with how tightly the closest pair of points is packed
        relative to the full X span, so a sharp bend between two nearby points isn't
        missed by a sampling grid that's only fine enough for the widest gaps.
        """
        x_values = np.asarray(x_values, dtype=float)
        y_values = np.asarray(y_values, dtype=float)
        akima = Akima1DInterpolator(x_values, y_values)

        span = float(x_values.max() - x_values.min())
        gaps = np.diff(np.sort(x_values))
        gaps = gaps[gaps > 0]
        min_gap = float(gaps.min()) if gaps.size else span

        samples = 500 if min_gap <= 0 else int(np.clip((span / min_gap) * 20, 500, 4000))

        x_smooth = np.linspace(x_values.min(), x_values.max(), samples)
        y_smooth = akima(x_smooth)
        return float(np.min(y_smooth)), float(np.max(y_smooth))

    def toggle_smooth_curve(self, state):
        """Toggles smooth curve drawing, ensuring bounds are checked and the checkbox state is consistent."""
        toggle = self.sender()
        if not state:
            self.smooth_curve_enabled = False
            # self.msg_label.hide()  # Hide any error messages
            self.update()
            self.modified.emit()
            return

        if len(self.points) < 4:
            self.msg_label.setText("Error: Need at least 4 points for smooth mode.")
            self.msg_label.show()
            QTimer.singleShot(3000, self.msg_label.hide)
            QTimer.singleShot(300, lambda: toggle.setChecked(False)) # Force the toggle to unchecked

            return

        # Validate the entire curve for smooth mode
        x_values = [p.x() for p in self.points]
        y_values = [p.y() for p in self.points]

        try:
            y_min, y_max = self._curve_bounds(x_values, y_values)
            if y_min < -self._BOUNDS_EPSILON or y_max > 100 + self._BOUNDS_EPSILON:
                self.msg_label.setText("Error: Smooth curve would exceed bounds.")
                self.msg_label.show()
                QTimer.singleShot(3000, self.msg_label.hide)
                QTimer.singleShot(300, lambda: toggle.setChecked(False))  # Force the toggle to unchecked

                return
        except Exception as e:
            self.msg_label.setText(f"Error: Cannot enable smooth curve ({e}).")
            self.msg_label.show()
            QTimer.singleShot(300, lambda: toggle.setChecked(False))  # Force the toggle to unchecked

            # self.smooth_toggle.setChecked(False)  # Force the toggle to unchecked
            return

        # Enable smooth curve mode
        self.smooth_curve_enabled = True
        self.msg_label.hide()  # Hide any error messages
        self.modified.emit()
        self.update()

    def highlight_dragged_point(self, painter):
        """Highlights the point currently being dragged."""
        painter.setPen(QPen(Qt.GlobalColor.red, 2))  # Red outline
        painter.setBrush(QColor(255, 255, 0))  # Yellow fill

        # Convert the dragged point to widget space
        dragged_widget_point = self.map_to_widget_space(self.dragging_point)

        # Draw the highlighted point with a larger size
        painter.drawEllipse(
            QRectF(
                dragged_widget_point.x() - self.point_radius * 1.5,
                dragged_widget_point.y() - self.point_radius * 1.5,
                3 * self.point_radius,
                3 * self.point_radius,
            )
        )

    def draw_crosshairs(self, x_value, y_value):
        if not self.isEnabled():
            return
        """
        Draw crosshairs on the graph at the specified x and y.
        Args:
            x_value (float):.
            y_value (float):.
        """

        x_value = max(self.x_min, min(self.x_max, x_value))

        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
        x = rect.left() + ((x_value - self.x_min) / (self.x_max - self.x_min)) * rect.width()
        y = rect.top() + (1 - y_value / 100.0) * rect.height()

        # Set crosshair position and store gain and speed for label
        self.crosshair_position = QPointF(x, y)
        self.crosshair_y = y_value
        self.crosshair_x = x_value
        self.update()  # Trigger repaint

    def clear_crosshairs(self):
        """
        Clear the crosshairs from the graph.
        """
        self.crosshair_position = None
        self.crosshair_y = None
        self.crosshair_x = None
        self.update()  # Trigger repaint

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw_grid(painter)
        self.draw_axis_labels(painter)
        if self.smooth_curve_enabled:
            self.draw_smooth_curve(painter)
        else:
            self.draw_curve(painter)

        # Highlight the dragged point
        if self.dragging_point is not None:
            self.highlight_dragged_point(painter)

        # Draw the crosshairs if position is set
        if hasattr(self, 'crosshair_position') and self.crosshair_position is not None:
            painter.setPen(QPen(self.crosshair_color, 2, Qt.PenStyle.DashLine))  # Red dashed lines for crosshairs
            rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)

            # Draw vertical and horizontal crosshair lines
            painter.drawLine(int(self.crosshair_position.x()), rect.top(),
                             int(self.crosshair_position.x()), rect.bottom())  # Vertical line
            painter.drawLine(rect.left(), int(self.crosshair_position.y()),
                             rect.right(), int(self.crosshair_position.y()))  # Horizontal line

        # Draw the live speed and gain label at the center top of the graph
        if hasattr(self, 'crosshair_x') and self.crosshair_x is not None and hasattr(self, 'crosshair_y'):
            rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
            label_text = f"{self.x_label_text} {self.crosshair_x:.1f} {self.current_unit}, {self.y_label_text} {self.crosshair_y:.1f}%"
            label_x = rect.left() + (rect.width() // 2) - (len(label_text) * 3)  # Center horizontally
            label_y = rect.top() - 5  # Fixed position slightly above the graph area

            painter.setPen(QPen(QColor("white") if G.useDarkMode else QColor("black")))
            painter.setFont(QFont('Arial', 10, QFont.Weight.Bold))  # Bold font for visibility
            painter.drawText(label_x, label_y, label_text)

        # Apply disabled overlay if the widget is disabled
        if not self._enabled:
            self.apply_disabled_overlay(painter)

    def draw_grid(self, painter):
        """Draws the grid lines."""
        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)  # Adjust to ensure margin
        painter.setPen(QPen(self.grid_color, 1, Qt.PenStyle.DotLine))

        # Draw horizontal grid lines (Y-axis 0% to 100%)
        for i in range(0, 11):
            y = int(rect.top() + i * rect.height() / 10)  # Cast to int
            painter.drawLine(rect.left(), y, rect.right(), y)

        # Draw vertical grid lines (X-axis controlled by x_scale)
        for i in range(0, 11):
            x = int(rect.left() + i * rect.width() / 10)  # Cast to int
            painter.drawLine(x, rect.top(), x, rect.bottom())

    def draw_axis_labels(self, painter, x_unit=None, integer=True, sign=None):
        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
        font = QFont()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(self.axis_color))
        if x_unit is None:
            x_unit = ''

        for i in range(0, 11):
            y = int(rect.top() + i * rect.height() / 10)
            painter.drawText(rect.left() - self.margin_left + 15, y + 5, f"{100 - i * 10}%")

        for i in range(0, 11):
            x = int(rect.left() + i * rect.width() / 10)
            x_val = self.x_min + (self.x_max - self.x_min) * i / 10
            x_val = round(x_val, 1)
            if integer:
                # print(f"int{x_val}")
                x_val=round(x_val)
            outsign = sign if sign is not None and x_val != 0 else ''
            painter.drawText(x - 10, rect.bottom() + self.margin_bottom // 2, f"{outsign}{x_val}{x_unit}")
            # 🔽 Draw additional axis description below tick labels

            if hasattr(self, "x_label_legend") and self.x_label_legend:
                painter.setFont(QFont('Arial', 9))
                text = self.x_label_legend
                text_width = painter.fontMetrics().horizontalAdvance(text)
                center_x = rect.left() + (rect.width() // 2)
                center_y = rect.bottom() + self.margin_bottom - 2  # Adjust as needed for spacing
                painter.drawText(center_x - text_width // 2, center_y, text)
            # 🔽 Draw Y-axis legend vertically to the left of tick labels
            if hasattr(self, "y_label_legend") and self.y_label_legend:
                painter.save()
                painter.setFont(QFont('Arial', 9))

                text = self.y_label_legend
                text_width = painter.fontMetrics().height()  # height now represents width of rotated text
                text_height = painter.fontMetrics().horizontalAdvance(text)

                # Calculate center of y-axis
                y_center = rect.top() + rect.height() // 2

                # X-position further left to avoid cutting off text
                x_pos = rect.left() - self.margin_left + text_width -5  # Adjust this to your margin_left

                painter.translate(x_pos, y_center + text_height // 2)
                painter.rotate(-90)
                painter.drawText(0, 0, text)

                painter.restore()

    def draw_curve(self, painter):
        """Draws the curve and points (linear segments)."""
        painter.setPen(QPen(self.curve_color, 2))

        # Convert points into widget space
        widget_points = [self.map_to_widget_space(p) for p in self.points]

        # Draw the lines between points
        for i in range(len(widget_points) - 1):
            painter.drawLine(widget_points[i], widget_points[i + 1])

        # Draw the control points
        for point in widget_points:
            painter.setBrush(QColor(255, 0, 0))
            painter.drawEllipse(QRectF(
                point.x() - self.point_radius,
                point.y() - self.point_radius,
                2 * self.point_radius,
                2 * self.point_radius
            ))

    def draw_smooth_curve(self, painter):
        """Draws a smooth curve using Akima interpolation."""
        if len(self.points) < 4:
            # Fallback to linear interpolation or a simple curve
            self.draw_curve(painter)
            return

        painter.setPen(QPen(self.curve_color, 2))

        # Extract x and y values from points
        x_values = [p.x() for p in self.points]
        y_values = [p.y() for p in self.points]

        # Create smooth x and y values using Akima interpolation
        x_smooth = np.linspace(min(x_values), max(x_values), 500)
        try:
            akima = Akima1DInterpolator(x_values, y_values)
            y_smooth = akima(x_smooth)
        except Exception as e:
            QMessageBox.warning(self, "Interpolation Error", f"Error creating Akima interpolation: {e}")
            self.draw_curve(painter)
            return

        # Convert to widget coordinates
        widget_smooth_points = [self.map_to_widget_space(QPointF(x, y)) for x, y in zip(x_smooth, y_smooth)]

        # Draw smooth curve
        for i in range(len(widget_smooth_points) - 1):
            painter.drawLine(widget_smooth_points[i], widget_smooth_points[i + 1])

        # Draw control points
        for point in [self.map_to_widget_space(p) for p in self.points]:
            painter.setBrush(self.point_fill)
            painter.drawEllipse(QRectF(
                point.x() - self.point_radius,
                point.y() - self.point_radius,
                2 * self.point_radius,
                2 * self.point_radius
            ))

    def map_to_widget_space(self, point):
        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
        x = rect.left() + ((point.x() - self.x_min) / (self.x_max - self.x_min)) * rect.width()
        y = rect.top() + (1 - point.y() / 100.0) * rect.height()
        return QPointF(x, y)

    def map_from_widget_space(self, point):
        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
        x = self.x_min + ((point.x() - rect.left()) / rect.width()) * (self.x_max - self.x_min)
        y = (1 - (point.y() - rect.top()) / rect.height()) * 100
        return QPointF(x, y)



    def check_smooth_curve_bounds(self, new_pos, index):
        """Check if the smooth curve will exceed bounds (0-100%) when a point is moved."""
        # Copy points and apply the new position
        projected_points = self.points.copy()
        projected_points[index] = new_pos
        x_values = [p.x() for p in projected_points]
        y_values = [p.y() for p in projected_points]

        # Ensure points are sorted by X
        sorted_indices = np.argsort(x_values)
        x_values = np.array(x_values)[sorted_indices]
        y_values = np.array(y_values)[sorted_indices]

        try:
            y_min, y_max = self._curve_bounds(x_values, y_values)
        except Exception as e:
            # If interpolation fails, log the issue and reject the move
            print(f"Akima interpolation error: {e}")
            return False

        # Check bounds with the same tolerance used everywhere else in this widget
        if y_min < -self._BOUNDS_EPSILON or y_max > 100 + self._BOUNDS_EPSILON:
            return False  # Curve exceeds bounds
        return True  # Curve is within acceptable bounds

    def mousePressEvent(self, event):
        clicked_widget_point = event.pos()  # This is in widget space

        if event.button() == Qt.MouseButton.RightButton:
            for i, p in enumerate(self.points):
                point_on_screen = self.map_to_widget_space(p)
                if (point_on_screen - QPointF(clicked_widget_point)).manhattanLength() < self.interaction_radius:
                    self.right_clicked_point = p
                    if i not in [0, len(self.points) - 1]:
                        self.show_context_menu(event.pos())
                    break
            else:
                # No point clicked: add new one
                new_data_point = self.map_from_widget_space(clicked_widget_point)
                self.add_new_point(new_data_point)

        elif event.button() == Qt.MouseButton.LeftButton:
            for p in self.points:
                point_on_screen = self.map_to_widget_space(p)
                if (point_on_screen - QPointF(clicked_widget_point)).manhattanLength() < self.interaction_radius:
                    self.dragging_point = p
                    self.update()
                    break

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self.dragging_point is not None:
            if self.last_valid_position is None:
                self.last_valid_position = QPointF(self.dragging_point)

            index = self.points.index(self.dragging_point)
            new_pos = self.map_from_widget_space(pos)
            valid_move = True

            x_lower = min(self.x_min, self.x_max)
            x_upper = max(self.x_min, self.x_max)

            if index == 0:
                # First point: allowed anywhere along X=x_lower (Y free) or Y=0 (X free).
                # If the cursor is off both legs, snap to whichever leg is nearer instead
                # of freezing the point in place.
                on_x_leg = new_pos.x() <= x_lower
                on_y_leg = new_pos.y() <= 0
                if not (on_x_leg or on_y_leg):
                    x_frac = (new_pos.x() - x_lower) / (x_upper - x_lower) if x_upper != x_lower else 0
                    y_frac = new_pos.y() / 100.0
                    on_x_leg = x_frac <= y_frac

                if on_x_leg:
                    new_pos.setX(x_lower)  # Lock X to min
                    new_pos.setY(max(0, min(100, new_pos.y())))  # Allow vertical movement (Y-axis)
                else:
                    new_pos.setY(0)  # Lock Y to 0
                    new_pos.setX(max(x_lower, min(self.points[1].x() - 0.01, new_pos.x())))  # Allow horizontal movement (X-axis)

                if self.smooth_curve_enabled:
                    valid_move = self.check_smooth_curve_bounds(new_pos, index)

            elif index == len(self.points) - 1:
                # Last point: allowed anywhere along X=x_upper (Y free) or Y=100 (X free).
                # Snap to whichever leg is nearer rather than freezing when off both.
                on_x_leg = new_pos.x() >= x_upper
                on_y_leg = new_pos.y() >= 100
                if not (on_x_leg or on_y_leg):
                    x_frac = (x_upper - new_pos.x()) / (x_upper - x_lower) if x_upper != x_lower else 0
                    y_frac = (100 - new_pos.y()) / 100.0
                    on_x_leg = x_frac <= y_frac

                if on_x_leg:
                    new_pos.setX(x_upper)  # Lock X to max
                    new_pos.setY(max(0, min(100, new_pos.y())))  # Allow vertical movement (Y-axis)
                else:
                    new_pos.setY(100)  # Lock Y to 100
                    new_pos.setX(
                        max(self.points[-2].x() + 0.01,
                            min(x_upper, new_pos.x()))  # Allow horizontal movement (X-axis)
                    )

                if self.smooth_curve_enabled:
                    valid_move = self.check_smooth_curve_bounds(new_pos, index)

            else:
                # Intermediate points: ensure proper bounds and prevent overlap
                new_x = max(self.points[index - 1].x() + 0.01, min(self.points[index + 1].x() - 0.01, new_pos.x()))
                new_y = max(0, min(100, new_pos.y()))
                proposed_pos = QPointF(new_x, new_y)

                if self.smooth_curve_enabled:
                    valid_move = self.check_smooth_curve_bounds(proposed_pos, index)
                else:
                    valid_move = True

                if valid_move:
                    new_pos = proposed_pos

            if valid_move:
                self.dragging_point.setX(new_pos.x())
                self.dragging_point.setY(new_pos.y())
                self.last_valid_position = QPointF(self.dragging_point)  # Save the last valid position
                self.msg_label.hide()  # Hide any error message
            else:
                # For first and last points, silently block invalid moves
                if self.last_valid_position is not None:
                    self.dragging_point.setX(self.last_valid_position.x())
                    self.dragging_point.setY(self.last_valid_position.y())

                if index != 0 and index != len(self.points) - 1:
                    if not self.msg_label.isVisible():
                        self.msg_label.setText("Error: Further movement would exceed curve bounds.")
                        self.msg_label.show()
                        QTimer.singleShot(3000, self.msg_label.hide)

            # Update the coordinate label with the current position of the dragging point
            rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)  # Graph area
            self.coordinate_label.setText(f"{self.dragging_point.x():.2f} {self.current_unit}, %{self.dragging_point.y():.2f}")
            self.coordinate_label.move(
                rect.right() - self.coordinate_label.width() - 1,
                rect.bottom() - self.coordinate_label.height() - 1
            )
            self.coordinate_label.show()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.dragging_point is not None:
            # Restore last valid position if bounds were violated
            if self.last_valid_position and self.smooth_curve_enabled:
                self.dragging_point.setX(self.last_valid_position.x())
                self.dragging_point.setY(self.last_valid_position.y())
            self.dragging_point = None
            self.last_valid_position = None  # Reset last valid position
            self.coordinate_label.hide()  # Hide the coordinate label
            self.modified.emit()
            self.update()

    def show_context_menu(self, pos):
        """Shows a context menu for deleting points."""
        context_menu = QMenu(self)
        delete_action = context_menu.addAction("Delete Point")
        action = context_menu.exec(self.mapToGlobal(pos))

        if action == delete_action:
            # Prevent deletion if smooth mode is enabled and only 4 points remain
            if self.smooth_curve_enabled and len(self.points) <= 4:
                self.msg_label.setText("Error: Cannot delete more points with smooth mode enabled.")
                self.msg_label.show()
                QTimer.singleShot(3000, self.msg_label.hide)
            else:
                self.points.remove(self.right_clicked_point)
                self.modified.emit()
                self.update()

    def add_new_point(self, new_point):
        """Add a new point and maintain order by X."""
        if not (min(self.x_min, self.x_max) <= new_point.x() <= max(self.x_min, self.x_max)):
            return  # Disallow point outside range

        # Temporarily add the new point and check bounds
        projected_points = self.points + [new_point]
        projected_points.sort(key=lambda p: p.x())  # Ensure points are ordered by x (speed)

        if self.smooth_curve_enabled:
            # Check bounds using Akima interpolation
            x_values = [p.x() for p in projected_points]
            y_values = [p.y() for p in projected_points]

            try:
                y_min, y_max = self._curve_bounds(x_values, y_values)
            except Exception:
                self.msg_label.setText("Error: Invalid smooth curve with this point.")
                self.msg_label.show()
                return

            if y_min < -self._BOUNDS_EPSILON or y_max > 100 + self._BOUNDS_EPSILON:
                # Reject point if bounds exceeded
                self.msg_label.setText("Error: Adding this point would exceed curve bounds.")
                self.msg_label.show()
                QTimer.singleShot(3000, self.msg_label.hide)
                return
        else:
            # In linear mode, ensure Y is within 0 to 100
            if new_point.y() < 0 or new_point.y() > 100:
                self.msg_label.setText("Error: Adding this point would exceed bounds.")
                self.msg_label.show()
                QTimer.singleShot(3000, self.msg_label.hide)
                return

        self.points.append(new_point)
        self.points.sort(key=lambda p: p.x())  # Ensure points are ordered by x (speed)
        self.modified.emit()
        self.update()

    def to_dict(self):
        return {
            "x_min": round(self.x_min, 2),
            "x_max": round(self.x_max, 2),
            "points": [{"x": round(p.x(), 2), "y": round(p.y(), 2)} for p in self.points],
            "smooth_curve_enabled": self.smooth_curve_enabled,
            "current_unit": self.current_unit,
        }

    def from_dict(self, data):
        self.x_min = data.get("x_min", 0)
        self.x_max = data.get("x_max", 100)
        self.points = [QPointF(p["x"], p["y"]) for p in data.get("points", [{"x": 0, "y": 0}, {"x": 100, "y": 100}])]
        self.smooth_curve_enabled = data.get("smooth_curve_enabled", False)
        self.current_unit = data.get("current_unit", "kt")
        self.update()


    def clear_points(self):
        """Resets the points to the default values."""
        self.points = [QPointF(self.x_min, 0), QPointF(self.x_max, 100)]  # Default 0% at 0 knots and 100%
        # self.test_point = None  # Clear the test point when resetting
        self.modified.emit()
        self.update()


class SpringCurveWidget(CurveWidget):
    from telemffb.util import conversions as conv
    UNIT_CONVERSIONS = {
        "kt": conv.ms2kt,
        "mph": conv.ms2mph,
        "kph": conv.ms2kmh,
        "m/s": 1.0,
    }
    def __init__(self, parent=None, unit='kt', base_unit='m/s'):
        super().__init__(parent)
        self.points = [QPointF(0, 0), QPointF(500, 100)]
        self.x_min = 0
        self.x_max = 500
        self.current_unit = unit
        self.base_unit = base_unit
        self.setWindowTitle("Spring Force Curve Editor")
        self.x_label_text = "Speed:"
        self.x_label_legend = "Speed (IAS)"
        self.y_label_text = "Gain:"
        self.y_label_legend = "% Spring Gain"

    def draw_crosshairs(self, speed_mps, gain):
        if not self.isEnabled():
            return
        """
        Draw crosshairs on the graph at the specified speed and gain.
        Args:
            speed_mps (float): The airspeed in m/s.
            gain (float): The percentage gain.
        """
        if self.msg_label.isVisible() and self.msg_label.text() == 'Please save a configuration before enabling live view':
            self.msg_label.hide()

        # Convert speed to current units
        current_conversion = self.UNIT_CONVERSIONS[self.current_unit]
        speed_converted = speed_mps * current_conversion

        super().draw_crosshairs(speed_converted, gain)

    def set_airspeed_range(self, new_max):
        """
        Set the x-axis range by assigning a new x_max directly.
        Keeps x_min fixed at 0, and scales all X points proportionally.
        """
        if new_max <= 0:
            return  # Optionally raise an exception

        current_range = self.x_max - self.x_min
        if current_range == 0:
            return

        scale_factor = new_max / current_range
        for point in self.points:
            point.setX(point.x() * scale_factor)

        self.x_max = new_max
        self.update()

    def update_airspeed_range(self, increment):
        """
        Adjust the x-axis range by increasing or decreasing x_max.
        Keeps x_min fixed at 0, and scales all X points proportionally.
        """
        current_range = self.x_max - self.x_min
        new_x_max = max(100, self.x_max + increment)

        scale_factor = new_x_max / current_range

        for point in self.points:
            point.setX(point.x() * scale_factor)

        self.x_max = new_x_max
        self.update()

    def change_unit(self, new_unit):
        """Change the unit of the x-axis and update points and labels."""
        if new_unit == self.current_unit:
            return

        # Conversion factors
        current_conversion = self.UNIT_CONVERSIONS[self.current_unit]
        new_conversion = self.UNIT_CONVERSIONS[new_unit]
        conversion_factor = current_conversion / new_conversion

        # Update points and x_scale
        self.points = [
            QPointF(p.x() * conversion_factor, p.y()) for p in self.points
        ]
        self.x_max *= conversion_factor
        self.x_min *= conversion_factor

        self.current_unit = new_unit
        self.update()


class GForceCurveWidget(CurveWidget):
    UNIT_CONVERSIONS = {
    }

    def __init__(self, parent=None, unit='gs', base_unit='gs', x_min=0, x_max=10):
        super().__init__(parent)
        self.points = [QPointF(0, 0), QPointF(10, 100)]
        self.x_min = x_min
        self.x_max = x_max
        self.current_unit = unit
        self.base_unit = base_unit
        self.setWindowTitle("G-Force Effect Curve Editor")
        self.x_label_text = "G Force:"
        self.x_label_legend = "G Force"
        self.y_label_text = "Effect Force:"
        self.y_label_legend = "% Constant Force"
        self.update()
        self.negative_instance = False

    def draw_crosshairs(self, gs, gain):
        if not self.isEnabled():
            return
        """
        Draw crosshairs on the graph at the specified speed and gain.
        Args:
            speed_mps (float): The airspeed in m/s.
            gain (float): The percentage gain.
        """
        if self.msg_label.isVisible() and self.msg_label.text() == 'Please save a configuration before enabling live view':
            self.msg_label.hide()

        # # Convert speed to current units
        # current_conversion = self.UNIT_CONVERSIONS[self.current_unit]
        # speed_converted = speed_mps * current_conversion

        super().draw_crosshairs(gs, gain)

    def draw_axis_labels(self, painter, x_unit=None, integer=False, sign=None):
        if self.negative_instance:
            sign = '-'
        super().draw_axis_labels(painter, "g", integer=False, sign=sign)

    def update_x_range(self, new_x_min=None, new_x_max=None):
        """
        Dynamically updates the x-axis range and rescales all X points proportionally.
        """
        new_x_min = self.x_min if new_x_min is None else new_x_min
        new_x_max = self.x_max if new_x_max is None else new_x_max

        old_range = self.x_max - self.x_min
        new_range = new_x_max - new_x_min

        if old_range == 0 or new_range == 0:
            return  # Avoid division by zero

        # Rescale points
        for point in self.points:
            normalized_x = (point.x() - self.x_min) / old_range
            rescaled_x = new_x_min + normalized_x * new_range
            point.setX(rescaled_x)

        self.x_min = new_x_min
        self.x_max = new_x_max
        self.update()


class TrimCurveWidget(CurveWidget):
    """Read-only display of the auto-trim calibration measurement.

    Sibling of :class:`SpringCurveWidget`/:class:`GForceCurveWidget`. Plots the
    measured ``elevator_axis(trim)`` samples (scatter) against the fitted line
    used to solve ``virtual_y``. X is trim %, Y is the elevator-axis command %
    (signed). It is not editable — the base's point-drag machinery is stubbed
    out — and it carries none of the spring-specific unit/airspeed/smoothing
    behavior.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trim Calibration")
        self.x_label_text = "Trim:"
        self.x_label_legend = "Elevator Trim %"
        self.y_label_text = "Elevator:"
        self.y_label_legend = "Elevator Axis %"
        self.current_unit = "%"

        # Label band sized for this widget's tick labels ("-103%") plus the
        # rotated Y legend; the base margins are too tight and put ticks and
        # legends in the same pixel columns.
        self.margin_left = 58
        self.margin_bottom = 42

        # Full-scale view while idle/measuring; set_result() zooms to the data.
        self.x_min = -100.0
        self.x_max = 100.0
        self.y_min = -100.0
        self.y_max = 100.0

        self.sample_points = []          # [QPointF(trim%, elevator%)]
        self.flagged_indices = set()     # sample indices taken with a VS residual
        self.fit_line = None             # (QPointF, QPointF) in %-space
        self.curve_polyline = []         # sorted samples joined = the calibrated curve
        self.extrap_tails = []           # dashed edge-slope segments beyond the band
        self.live_point = None           # QPointF(trim%, elevator%) or None
        self.points = []                 # unused; keeps base paint helpers safe

    # ---- data API -----------------------------------------------------------

    def set_result(self, samples, slope, intercept, flagged=None):
        """Populate from calibration output.

        Args:
            samples: list of (trim_frac, u_elev_frac), both normalized [-1, 1].
            slope, intercept: linear fit of u_elev vs trim (normalized units).
            flagged: sample indices accepted with a VS residual (drawn amber).
        """
        self.sample_points = [QPointF(t * 100.0, u * 100.0) for t, u in samples]
        self.flagged_indices = set(flagged or [])
        xs = [p.x() for p in self.sample_points]
        ys = [p.y() for p in self.sample_points]
        if not xs:
            self.update()
            return

        # The calibrated curve is the sorted samples joined; dashed tails show
        # the edge-slope extrapolation the runtime applies beyond the band.
        pts = sorted(self.sample_points, key=lambda p: p.x())
        self.curve_polyline = pts
        self.extrap_tails = []
        span = pts[-1].x() - pts[0].x()
        ext = max(span * 0.15, 2.0)
        if len(pts) >= 2:
            p0, p1 = pts[0], pts[1]
            s0 = (p1.y() - p0.y()) / (p1.x() - p0.x()) if p1.x() != p0.x() else 0.0
            self.extrap_tails.append(
                (QPointF(p0.x() - ext, p0.y() - s0 * ext), QPointF(p0.x(), p0.y())))
            q0, q1 = pts[-2], pts[-1]
            s1 = (q1.y() - q0.y()) / (q1.x() - q0.x()) if q1.x() != q0.x() else 0.0
            self.extrap_tails.append(
                (QPointF(q1.x(), q1.y()), QPointF(q1.x() + ext, q1.y() + s1 * ext)))

        xmin, xmax = min(xs), max(xs)
        self.x_min, self.x_max = xmin - ext - 1.0, xmax + ext + 1.0

        y1 = (slope * (self.x_min / 100.0) + intercept) * 100.0
        y2 = (slope * (self.x_max / 100.0) + intercept) * 100.0
        self.fit_line = (QPointF(self.x_min, y1), QPointF(self.x_max, y2))

        tail_ys = [p.y() for seg in self.extrap_tails for p in seg]
        ylim = max(max((abs(v) for v in ys), default=0.0),
                   max((abs(v) for v in tail_ys), default=0.0),
                   abs(y1), abs(y2), 5.0) * 1.2
        self.y_min, self.y_max = -ylim, ylim
        self.update()

    def set_live_point(self, trim_frac, u_elev_frac):
        """Optional live marker for the current trim/elevator during a run."""
        if trim_frac is None or u_elev_frac is None:
            self.live_point = None
        else:
            self.live_point = QPointF(trim_frac * 100.0, u_elev_frac * 100.0)
        self.update()

    def set_samples(self, samples):
        """Live scatter of the stations accepted so far (no zoom/fit).

        Used while a run is in progress; the view stays at full scale so the
        picture is stable — set_result() does the zoom at completion.
        """
        self.sample_points = [QPointF(t * 100.0, u * 100.0) for t, u in samples]
        self.update()

    def clear(self):
        self.sample_points = []
        self.flagged_indices = set()
        self.fit_line = None
        self.curve_polyline = []
        self.extrap_tails = []
        self.live_point = None
        # Back to the stable full-scale measuring view (a previous result may
        # have zoomed the axes to its data).
        self.x_min, self.x_max = -100.0, 100.0
        self.y_min, self.y_max = -100.0, 100.0
        self.update()

    # ---- coordinate mapping (signed Y range) --------------------------------

    def map_to_widget_space(self, point):
        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
        xr = (self.x_max - self.x_min) or 1.0
        yr = (self.y_max - self.y_min) or 1.0
        x = rect.left() + ((point.x() - self.x_min) / xr) * rect.width()
        y = rect.top() + (1 - (point.y() - self.y_min) / yr) * rect.height()
        return QPointF(x, y)

    def map_from_widget_space(self, point):
        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
        x = self.x_min + ((point.x() - rect.left()) / rect.width()) * (self.x_max - self.x_min)
        y = self.y_min + (1 - (point.y() - rect.top()) / rect.height()) * (self.y_max - self.y_min)
        return QPointF(x, y)

    # ---- rendering ----------------------------------------------------------

    def draw_axis_labels(self, painter, *args, **kwargs):
        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
        painter.setFont(QFont('Arial', 8))
        painter.setPen(QPen(self.axis_color))
        fm = painter.fontMetrics()

        # Every other gridline gets a tick label — all 11 collide at typical
        # widget heights. Y ticks are right-aligned against the plot edge so
        # they never reach the rotated legend at the far left.
        for i in range(0, 11, 2):
            y = int(rect.top() + i * rect.height() / 10)
            val = self.y_max - (self.y_max - self.y_min) * i / 10
            text = f"{val:.0f}%"
            painter.drawText(rect.left() - fm.horizontalAdvance(text) - 6,
                             y + fm.ascent() // 2, text)

        for i in range(0, 11, 2):
            x = int(rect.left() + i * rect.width() / 10)
            val = self.x_min + (self.x_max - self.x_min) * i / 10
            text = f"{val:.0f}"
            painter.drawText(x - fm.horizontalAdvance(text) // 2,
                             rect.bottom() + fm.ascent() + 4, text)

        painter.setFont(QFont('Arial', 9))
        fm = painter.fontMetrics()
        if self.x_label_legend:
            tw = fm.horizontalAdvance(self.x_label_legend)
            painter.drawText(rect.left() + rect.width() // 2 - tw // 2,
                             rect.bottom() + self.margin_bottom - 6, self.x_label_legend)
        if self.y_label_legend:
            painter.save()
            th = fm.horizontalAdvance(self.y_label_legend)
            painter.translate(12, rect.top() + rect.height() // 2 + th // 2)
            painter.rotate(-90)
            painter.drawText(0, 0, self.y_label_legend)
            painter.restore()

    def _draw_zero_axes(self, painter):
        """Emphasize the x=0 and y=0 reference lines within the plot band."""
        rect = self.rect().adjusted(self.margin_left, self.margin_top, -self.margin_right, -self.margin_bottom)
        painter.setPen(QPen(self.axis_color, 1, Qt.PenStyle.SolidLine))
        if self.y_min <= 0 <= self.y_max:
            zy = self.map_to_widget_space(QPointF(self.x_min, 0)).y()
            painter.drawLine(rect.left(), int(zy), rect.right(), int(zy))
        if self.x_min <= 0 <= self.x_max:
            zx = self.map_to_widget_space(QPointF(0, self.y_min)).x()
            painter.drawLine(int(zx), rect.top(), int(zx), rect.bottom())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw_grid(painter)
        self._draw_zero_axes(painter)
        self.draw_axis_labels(painter)

        # Static best-fit: dotted, de-emphasized — the calibrated curve is the
        # recommended output; the fit is the legacy single-gain reference.
        if self.fit_line is not None:
            painter.setPen(QPen(self.curve_color, 1, Qt.PenStyle.DotLine))
            painter.drawLine(self.map_to_widget_space(self.fit_line[0]),
                             self.map_to_widget_space(self.fit_line[1]))

        if len(self.curve_polyline) >= 2:
            painter.setPen(QPen(self.curve_color, 2))
            wpts = [self.map_to_widget_space(p) for p in self.curve_polyline]
            for a, b in zip(wpts, wpts[1:]):
                painter.drawLine(a, b)

        # Edge-slope extrapolation beyond the measured band: dashed.
        if self.extrap_tails:
            painter.setPen(QPen(self.curve_color, 2, Qt.PenStyle.DashLine))
            for seg in self.extrap_tails:
                painter.drawLine(self.map_to_widget_space(seg[0]),
                                 self.map_to_widget_space(seg[1]))

        for i, p in enumerate(self.sample_points):
            wp = self.map_to_widget_space(p)
            if i in self.flagged_indices:
                # taken with a residual VS (deadline compromise) — draw amber
                painter.setPen(QPen(QColor("#8a6510"), 1))
                painter.setBrush(QColor("#e6a817"))
            else:
                painter.setPen(QPen(self.axis_color, 1))
                painter.setBrush(self.point_fill)
            painter.drawEllipse(QRectF(wp.x() - 3, wp.y() - 3, 6, 6))

        if self.live_point is not None:
            wp = self.map_to_widget_space(self.live_point)
            painter.setBrush(QColor("#33cc33"))
            painter.setPen(QPen(QColor("#116611"), 1))
            painter.drawEllipse(QRectF(wp.x() - 4, wp.y() - 4, 8, 8))

        if not self._enabled:
            self.apply_disabled_overlay(painter)

    # ---- read-only: disable point editing -----------------------------------

    def mousePressEvent(self, event):
        pass

    def mouseMoveEvent(self, event):
        pass

    def mouseReleaseEvent(self, event):
        pass


class ExceptionStatusWidget(QWidget):
    """Status bar widget showing logged exception count with clickable link."""
    
    clicked = pyqtSignal()  # Emitted when the widget is clicked
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.exception_count = 0
        self.setup_ui()
        
    def setup_ui(self):
        """Setup the widget UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Error icon/emoji
        self.icon_label = QLabel("❌")
        #self.icon_label.setStyleSheet("font-size: 14pt;")
        
        # Text label
        self.text_label = QLabel("Errors: 0")
        self.text_label.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        
        # Make it look clickable
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to view logged exceptions")
        
        # Initially hidden
        self.hide()
        
    def set_count(self, count: int):
        """Update the exception count."""
        self.exception_count = count
        self.text_label.setText(f"Errors: {count}")
        
        if count > 0:
            self.show()
        else:
            self.hide()
            
    def mousePressEvent(self, event):
        """Handle mouse click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)





