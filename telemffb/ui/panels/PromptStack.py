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

"""PromptStack: the new-aircraft, trim-calibration-discovery and
matching-profile-offer "pill" prompts, stacked top-to-bottom by priority.

Replaces MainWindow's three hand-rolled QLabel pills (``new_craft_button``,
``trim_cal_prompt_button``, ``profile_change_button``) and their shared
``new_craft_container`` (which existed purely to collapse to zero height
when none of the three were showing, so the reserved space did not push
the Offline Editor Setup panel out of alignment with Active Devices beside
it - this widget does the same by hiding itself when nothing is active).

Driven entirely off ``telemffb.state.app_state.AppState.prompts_changed``:
producers call ``AppState.set_prompt(notice_id, Notice(...) or None)``,
this widget renders whatever is active as one ``NoticeCard`` per notice,
and forwards clicks back up tagged with the notice id - what a click
means (open the wizard, open the trim dialog, resolve the profile offer)
stays MainWindow's business, since it is the one with the dialogs and the
telemetry-manager calls to make.
"""

from typing import Dict, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from telemffb.state.app_state import AppState, Notice
from telemffb.ui.widgets.NoticeCard import (NEW_CRAFT_STYLE, PROFILE_CHANGE_STYLE,
                                            TRIM_CAL_STYLE, NoticeCard)

#: Notice.style -> the NoticeCard look it renders with.
_STYLES = {
    'new_craft': NEW_CRAFT_STYLE,
    'trim_cal': TRIM_CAL_STYLE,
    'profile_change': PROFILE_CHANGE_STYLE,
}

#: Spacing between stacked pills - matches the old new_craft_layout's
#: addSpacing(7) between each of its three widgets.
_SPACING = 7


class PromptStack(QWidget):
    """Container that shows one NoticeCard per active AppState prompt,
    ordered by priority (lowest first), and collapses to zero height when
    none are active."""

    #: Which notice was clicked, by id.
    activated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._cards: Dict[str, NoticeCard] = {}
        self.hide()

    def bind(self, state: AppState) -> None:
        """Subscribe to ``state`` and paint its current prompts immediately
        - nothing re-emits just because a new subscriber connected, so the
        initial paint has to be pulled, not waited for."""
        state.prompts_changed.connect(self._on_prompts_changed)
        self._on_prompts_changed(state.current_prompts())

    def _on_prompts_changed(self, notices: Tuple[Notice, ...]) -> None:
        active_ids = {n.notice_id for n in notices}
        for notice_id in list(self._cards):
            if notice_id not in active_ids:
                card = self._cards.pop(notice_id)
                self._layout.removeWidget(card)
                card.deleteLater()
        # Rebuilt in full each time: this only runs on an actual change to
        # what is shown (AppState dedupes), never on a telemetry frame, so
        # the cost of re-laying-out three labels at most is not a concern.
        while self._layout.count():
            self._layout.takeAt(0)
        for i, notice in enumerate(notices):
            if i:
                self._layout.addSpacing(_SPACING)
            card = self._cards.get(notice.notice_id)
            if card is None:
                card = NoticeCard(self)
                card.activated.connect(
                    lambda nid=notice.notice_id: self.activated.emit(nid))
                self._cards[notice.notice_id] = card
            card.set_notice(notice.html, _STYLES[notice.style], notice.pulse)
            self._layout.addWidget(card, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.setVisible(bool(notices))
