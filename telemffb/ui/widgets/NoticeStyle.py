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

"""NoticeStyle: the look of one ``NoticeCard`` pill, and the three looks
in use (``PromptStack`` maps a ``Notice.style`` name to one of them).
"""

from dataclasses import dataclass
from typing import Tuple


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
