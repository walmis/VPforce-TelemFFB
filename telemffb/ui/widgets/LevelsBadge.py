"""LevelsBadge: the amber mark on a device icon for effect levels below 100%
(a bar chart)."""

from PyQt6.QtWidgets import QWidget

from telemffb.ui.theme.tokens import WARNING_AMBER
from telemffb.ui.widgets.IconBadge import IconBadge

#: The glyph's color: dark, for contrast on the amber disc.
LEVELS_GLYPH_COLOR = "#202020"


class LevelsBadge(IconBadge):
    """A ``size`` px levels-reduced mark in its parent's bottom-right corner."""

    def __init__(self, parent: QWidget, size: int):
        super().__init__(parent, size, WARNING_AMBER, "badge-levels.svg",
                         glyph_color=LEVELS_GLYPH_COLOR, bottom=True)
