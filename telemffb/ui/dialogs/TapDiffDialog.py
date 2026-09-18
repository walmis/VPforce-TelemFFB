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

"""Showing a config file as it is, beside how it would be.

Every prompt that offers to change a dinput8.ini describes the change in a
sentence, and a sentence is a summary of somebody's file.  This is the thing
that settles it: the current text on the left, the proposed text on the
right, lined up so the differences are where the eye already is.

Read-only on purpose.  Editing here would be a second way to change a file
that TelemFFB is in the middle of changing, and reconciling the two is a
problem nobody needs.
"""

from difflib import SequenceMatcher
from typing import List, Optional, Sequence, Tuple

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPalette, QTextCursor, QTextCharFormat

#: One line of the comparison: what happened to it, and the text on each
#: side.  None where a side has no line, which is what keeps the two panes
#: aligned - a file with three lines inserted still reads straight across.
Row = Tuple[str, Optional[str], Optional[str]]


def aligned_diff(before: str, after: str) -> List[Row]:
    """Line up two versions of a file for side-by-side reading.

    Tags are difflib's - equal, insert, delete, replace - kept rather than
    reduced to added/removed because a replaced line reads differently from
    a line added next to an unrelated deletion.
    """
    left, right = before.splitlines(), after.splitlines()
    rows: List[Row] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, left, right).get_opcodes():
        if tag == "equal":
            rows.extend(("equal", a, b)
                        for a, b in zip(left[i1:i2], right[j1:j2]))
            continue
        a_lines, b_lines = left[i1:i2], right[j1:j2]
        for index in range(max(len(a_lines), len(b_lines))):
            rows.append((
                tag,
                a_lines[index] if index < len(a_lines) else None,
                b_lines[index] if index < len(b_lines) else None,
            ))
    return rows


def changed(rows: Sequence[Row]) -> bool:
    """Whether anything actually differs."""
    return any(tag != "equal" for tag, _, _ in rows)


class TapDiffDialog(QtWidgets.QDialog):
    """Two panes, scrolled together, with the changed lines colored."""

    def __init__(self, title: str, panes: Sequence[Tuple[str, str, str]],
                 parent=None):
        """``panes`` is (heading, current, proposed) per file.

        A list because a sim can hold two configs that differ, and showing
        one of them would answer half the question.
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 520)

        layout = QtWidgets.QVBoxLayout(self)

        # Two panes of identical text with a color key above them is a
        # worse way of saying "nothing would change" than saying it.
        if not any(changed(aligned_diff(before, after))
                   for _, before, after in panes):
            self.resize(360, 120)
            nothing = QtWidgets.QLabel("No changes to make.")
            nothing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(nothing, 1)
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            layout.addWidget(buttons)
            return

        layout.addWidget(self._legend())

        for heading, current, proposed in panes:
            if len(panes) > 1:
                label = QtWidgets.QLabel(f"<b>{heading}</b>")
                label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                    QtWidgets.QSizePolicy.Policy.Fixed)
                layout.addWidget(label)
            # the comparison takes the slack; everything else is trim
            layout.addWidget(self._comparison(current, proposed), 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def _shade(self, kind: str) -> QColor:
        """Backgrounds mixed into the window color, so they read on either
        theme instead of assuming a light one."""
        window = self.palette().color(QPalette.ColorRole.Window)
        dark = window.lightness() < 128
        tints = {
            "insert":  (60, 140, 60),
            "delete":  (150, 60, 60),
            "replace": (150, 120, 40),
        }
        if kind not in tints:
            return window
        r, g, b = tints[kind]
        weight = 0.45 if dark else 0.22
        return QColor(
            int(window.red() * (1 - weight) + r * weight),
            int(window.green() * (1 - weight) + g * weight),
            int(window.blue() * (1 - weight) + b * weight),
        )

    def _legend(self) -> QtWidgets.QWidget:
        strip = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 0, 0)
        for kind, text in (("delete", "removed"), ("insert", "added"),
                           ("replace", "changed")):
            chip = QtWidgets.QLabel(f"  {text}  ")
            chip.setStyleSheet(
                f"background: {self._shade(kind).name()}; border-radius: 3px;")
            # Left to itself a label stretches to whatever height the layout
            # will give it, and three color swatches then take half the
            # window off the comparison they are explaining.
            chip.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,
                               QtWidgets.QSizePolicy.Policy.Fixed)
            row.addWidget(chip)
        row.addStretch(1)
        strip.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                            QtWidgets.QSizePolicy.Policy.Fixed)
        return strip

    def _pane(self) -> QtWidgets.QTextEdit:
        view = QtWidgets.QTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        view.setFont(font)
        return view

    def _comparison(self, current: str, proposed: str) -> QtWidgets.QWidget:
        rows = aligned_diff(current, proposed)
        left, right = self._pane(), self._pane()

        def kind_for(tag, text, changed_kind):
            # A line missing from one side is padding, not content.  It keeps
            # the panes level so a change sits on the same row in both, and
            # shading it would claim something happened where nothing did.
            if text is None:
                return "pad"
            if tag == "equal":
                return "equal"
            # a replaced line keeps its own tint on both sides; one that is
            # only on one side is an addition or a removal
            return "replace" if tag == "replace" else changed_kind

        for tag, a, b in rows:
            self._append(left, a, kind_for(tag, a, "delete"))
            self._append(right, b, kind_for(tag, b, "insert"))

        for source, target in ((left, right), (right, left)):
            source.verticalScrollBar().valueChanged.connect(
                target.verticalScrollBar().setValue)
            source.horizontalScrollBar().valueChanged.connect(
                target.horizontalScrollBar().setValue)

        split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([450, 450])
        return split

    def _append(self, view: QtWidgets.QTextEdit, text: Optional[str],
                kind: str) -> None:
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block = cursor.blockFormat()
        block.setBackground(self._shade(kind))
        cursor.setBlockFormat(block)
        # a byte that was not UTF-8 in the file rides through the text as
        # an escape surrogate, which Qt cannot show; it is displayed as a
        # replacement character and written back, elsewhere, as itself
        shown = "" if text is None else text.encode(
            "utf-8", "replace").decode("utf-8")
        cursor.insertText(shown + "\n", QTextCharFormat())
