"""A table header that carries one title across all of its sections.

Used by the monitor's active-effects table, whose header is really the
pane's title rather than a label per column.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QHeaderView, QStyle, QStyleOptionHeader


class SpanningTitleHeaderView(QHeaderView):
    """``QHeaderView`` that paints one title centred across the whole header,
    instead of one label per column.

    A table whose header doubles as its pane's title has nowhere good to put
    it: section text is centred within its own section, so a title sitting in
    the first of two columns lands off-centre by half the second, and a title
    split across both is anchored to the divider between them rather than to
    the table's middle. This draws the title in ``paintEvent``, which - unlike
    ``paintSection`` - is not clipped to any one section, so it centres on the
    table's true width. The sections keep their own text (blank, here), their
    backgrounds, separators and resize handles.

    The title is drawn through the style's ``CE_HeaderLabel`` rather than by
    hand, so it picks up the font and text colour the sections beside it would
    have used, in either theme.
    """

    #: Breathing room at each end before the title starts eliding.
    _MARGIN_PX = 4

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._title = ''

    def title(self) -> str:
        """The full title, as set - never the elided form that was painted."""
        return self._title

    def setTitle(self, text: str) -> None:
        """Set the title painted across the header."""
        text = text or ''
        if text != self._title:
            self._title = text
            self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._title:
            return

        rect = self.viewport().rect().adjusted(self._MARGIN_PX, 0, -self._MARGIN_PX, 0)
        if rect.width() <= 0:
            return

        opt = QStyleOptionHeader()
        opt.initFrom(self)
        opt.rect = rect
        opt.section = 0
        opt.orientation = self.orientation()
        opt.textAlignment = Qt.AlignmentFlag.AlignCenter
        opt.text = self.fontMetrics().elidedText(
            self._title, Qt.TextElideMode.ElideRight, rect.width())

        painter = QPainter(self.viewport())
        # CE_HeaderLabel is the text half of a section: no background, so the
        # sections super() just painted show through underneath.
        self.style().drawControl(QStyle.ControlElement.CE_HeaderLabel, opt, painter, self)
        painter.end()
