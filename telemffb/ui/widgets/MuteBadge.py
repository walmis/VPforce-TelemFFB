"""MuteBadge: the red muted mark on a device icon (a crossed speaker)."""

from PyQt6.QtWidgets import QWidget

from telemffb.ui.theme.tokens import ERROR_RED
from telemffb.ui.widgets.IconBadge import IconBadge


class MuteBadge(IconBadge):
    """A ``size`` px muted mark in its parent's top-right corner."""

    def __init__(self, parent: QWidget, size: int):
        super().__init__(parent, size, ERROR_RED, "badge-mute.svg")
