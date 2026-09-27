#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
"""Wraps long tooltips, application-wide.

Qt word-wraps a tooltip only when its text is rich text; a long plain one is
shown as a single line running across the screen, and no stylesheet property
changes that. Installed once on the application, this shows every widget's
long plain tooltip as rich text instead, so no call site has to remember.

Tooltips a view takes from its model, and ones shown through
QToolTip.showText directly, do not pass through here: rich_tooltip() is the
same rule for those.
"""
import html

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QToolTip, QWidget


#: Plain tooltips up to this long read fine on one line and are left alone.
MAX_PLAIN = 60


def rich_tooltip(text: str) -> str:
    """``text`` as a tooltip Qt will wrap: a long plain one as rich text,
    reading as it did; anything else as it is."""
    if not text or len(text) <= MAX_PLAIN or Qt.mightBeRichText(text):
        return text
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


class TooltipWrapFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.ToolTip or not isinstance(obj, QWidget):
            return False
        text = obj.toolTip()
        wrapped = rich_tooltip(text)
        if wrapped == text:
            return False
        QToolTip.showText(event.globalPos(), wrapped, obj, obj.rect(), obj.toolTipDuration())
        return True
