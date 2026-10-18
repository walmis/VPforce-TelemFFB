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

"""NoticeCard: one reusable "pill" notice widget.

MainWindow used to hand-roll three of these as plain QLabels - new-aircraft,
trim-calibration-discovery and matching-profile-offer - each with its own
private QVariantAnimation doing the identical breathing-pulse tween. This
holds that pulse once; ``telemffb.ui.panels.PromptStack`` creates one
NoticeCard per currently-active ``telemffb.state.app_state.Notice``.
"""

from typing import Optional

from PyQt6.QtCore import QPoint, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtWidgets import QLabel, QMenu, QToolButton

from telemffb.ui.widgets.NoticeStyle import NoticeStyle


class NoticeCard(QLabel):
    """A content-sized, centered pill showing rich text with an embedded
    link spanning the whole label (so the whole pill is clickable and Qt
    supplies the hand cursor), optionally breathing between two fill
    colors.

    Rich text (not a QPushButton) so partial emphasis - a bold aircraft
    name among medium-weight fixed words - can render at all.
    """

    #: The pill was clicked. What that means is the caller's business -
    #: PromptStack forwards it tagged with the notice's id.
    activated = pyqtSignal()

    #: The dismiss entry in the close segment's menu was chosen.
    dismissed = pyqtSignal()

    #: Matches the pulse timing every one of the old hand-rolled pills used.
    _PULSE_DURATION_MS = 2600

    _BORDER_PX = 3
    _PAD_RIGHT_PX = 18
    #: The close segment: its width, and the gap from it to the pill's
    #: outer edge, which keeps the divider on the straight part of the
    #: pill, clear of the rounded end.
    _CLOSE_W = 30
    _CLOSE_GAP = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self._style: Optional[NoticeStyle] = None
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.0)
        self._anim.setDuration(self._PULSE_DURATION_MS)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._paint_pulse)
        self.linkActivated.connect(lambda _href: self.activated.emit())
        self._dismiss_label: Optional[str] = None
        self._close = QToolButton(self)
        self._close.setText("\u2715")
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.clicked.connect(self._show_dismiss_menu)
        self._close.hide()
        self.hide()

    def set_notice(self, html: str, style: NoticeStyle, pulse: bool,
                   dismiss_label: Optional[str] = None) -> None:
        """Show ``html`` styled as ``style``, breathing while ``pulse`` is
        true (started only while visible, like the originals - it costs
        nothing while hidden). ``dismiss_label`` adds a close segment past
        a divider whose menu offers that one entry."""
        self.setText(html)
        self._style = style
        self._dismiss_label = dismiss_label
        self._close.setVisible(bool(dismiss_label))
        self._close.setToolTip(dismiss_label or "")
        self._close.setStyleSheet(f"""QToolButton {{
                            border: none;
                            border-left: 2px solid {style.border};
                            background: transparent;
                            color: {style.text};
                            font-weight: bold;
                        }}
                        QToolButton:hover {{
                            background-color: {style.hover};
                        }}""")
        if pulse:
            if self._anim.state() != QVariantAnimation.State.Running:
                self._paint_pulse(0.0)
                self._anim.start()
        else:
            self._anim.stop()
            self._paint_pulse(1.0)
        self.show()

    def _show_dismiss_menu(self) -> None:
        if not self._dismiss_label:
            return
        menu = QMenu(self)
        menu.addAction(self._dismiss_label).triggered.connect(self.dismissed.emit)
        menu.exec(self._close.mapToGlobal(QPoint(0, self._close.height())))
        menu.deleteLater()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        b = self._BORDER_PX
        self._close.setGeometry(self.width() - b - self._CLOSE_GAP - self._CLOSE_W, b,
                                self._CLOSE_W, max(0, self.height() - 2 * b))

    def clear(self) -> None:
        """Hide and stop pulsing - cheap since the animation only runs
        while visible."""
        self._anim.stop()
        self.hide()

    def _paint_pulse(self, v: float) -> None:
        style = self._style
        if style is None:
            return
        r0, g0, b0 = style.fill_dim
        r1, g1, b1 = style.fill_bright
        r = int(r0 + (r1 - r0) * v)
        g = int(g0 + (g1 - g0) * v)
        b = int(b0 + (b1 - b0) * v)
        pad_right = self._PAD_RIGHT_PX
        if self._dismiss_label:
            pad_right += self._CLOSE_W + self._CLOSE_GAP
        self.setStyleSheet(f"""QLabel {{
                            background-color: rgb({r}, {g}, {b});
                            border: 3px solid {style.border};
                            border-radius: 17px;
                            color: {style.text};
                            padding: 8px {pad_right}px 8px 18px;
                        }}
                        QLabel:hover {{
                            background-color: {style.hover};
                        }}""")
