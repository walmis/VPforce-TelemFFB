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

from dataclasses import dataclass
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtWidgets import QLabel


@dataclass(frozen=True)
class NoticeStyle:
    """One pill's look. ``fill_dim``/``fill_bright`` are the breathing
    range (equal for a static, non-pulsing pill - the profile-change offer
    is teal and does not pulse, since it is an offer, not something wrong).
    """
    fill_dim: Tuple[int, int, int]
    fill_bright: Tuple[int, int, int]
    border: str
    text: str
    hover: str


#: The three looks in use today, colors preserved verbatim from the
#: MainWindow QLabels this widget replaces.
#: Red, breathing dim<->bright, white text/border - "something needs doing".
NEW_CRAFT_STYLE = NoticeStyle(fill_dim=(150, 28, 28), fill_bright=(225, 45, 45),
                              border='white', text='white', hover='#ef5350')
#: Mustard, breathing (same family as the Paused status badge), black
#: text/border so contrast holds throughout the pulse.
TRIM_CAL_STYLE = NoticeStyle(fill_dim=(130, 100, 12), fill_bright=(242, 180, 34),
                             border='black', text='black', hover='#f5bc28')
#: Teal, static (dim == bright: this is an offer, not an alert).
PROFILE_CHANGE_STYLE = NoticeStyle(fill_dim=(0, 121, 107), fill_bright=(0, 121, 107),
                                   border='white', text='white', hover='#26a69a')


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

    #: Matches the pulse timing every one of the old hand-rolled pills used.
    _PULSE_DURATION_MS = 2600

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
        self.hide()

    def set_notice(self, html: str, style: NoticeStyle, pulse: bool) -> None:
        """Show ``html`` styled as ``style``, breathing while ``pulse`` is
        true (started only while visible, like the originals - it costs
        nothing while hidden)."""
        self.setText(html)
        self._style = style
        if pulse:
            if self._anim.state() != QVariantAnimation.State.Running:
                self._paint_pulse(0.0)
                self._anim.start()
        else:
            self._anim.stop()
            self._paint_pulse(1.0)
        self.show()

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
        self.setStyleSheet(f"""QLabel {{
                            background-color: rgb({r}, {g}, {b});
                            border: 3px solid {style.border};
                            border-radius: 17px;
                            color: {style.text};
                            padding: 8px 18px;
                        }}
                        QLabel:hover {{
                            background-color: {style.hover};
                        }}""")
