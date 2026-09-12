#
# This file is part of the TelemFFB distribution.
#
# One of the user's own aircraft profiles and a shipped one both match the
# loaded aircraft: which one names it, what the aircraft flies with now and
# would after a merge, and the three things the user can do about it.
#
import json
import logging

from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView,
                             QLabel, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

import telemffb.globals as G

NL = "\n"


#: The first line of each value column, by which side the aircraft is flying
#: on today.  Both patterns matched it - that is why this dialog exists - so
#: the heading says which side is applied rather than which one matched.
COLUMN_HEADS = {
    'built-in': ("Built-in, applied", "Yours, not applied"),
    'yours': ("Built-in, not applied", "Yours, applied"),
    'both': ("Built-in, applied", "Yours, applied on top"),
}


def offers_decline(change) -> bool:
    """Whether the user may answer this collision with a permanent no.

    They may only when their own rows still reach the aircraft, because a
    decline leaves things as they are and there has to be something worth
    leaving.  That is true when their pattern names the aircraft, and when
    the two patterns are the identical string, where their rows sit on top
    of the built-in's and both apply.  It is false when the built-in is the
    more specific of the two, and when their pattern claims exactly what
    the built-in claims: there their rows reach nothing, so a permanent no
    would only hide the fact and leave the rows orphaned.  Those two are
    offered the merge and Not now, and the prompt stays until one is taken.
    """
    return change.get('winner') == 'user' or change.get('user') == change.get('curated')


def describe(value: str) -> str:
    """A value as a table cell.  Some settings hold a JSON payload - a trim
    calibration is a few thousand characters of curve points - so those are
    described by what they contain rather than printed."""
    value = (value or "").strip()
    if len(value) <= 40:
        return value
    if value.startswith(("{", "[")):
        try:
            data = json.loads(value)
        except ValueError:
            return value[:37] + "..."
        curves = data.get("curves", [data]) if isinstance(data, dict) else data
        if isinstance(curves, list) and curves and isinstance(curves[0], dict) and "points" in curves[0]:
            points = sum(len(c.get("points", [])) for c in curves)
            return (f"1 curve, {points} points" if len(curves) == 1
                    else f"{len(curves)} curves, {points} points")
        return f"{len(curves)} entries" if isinstance(curves, list) else "structured value"
    return value[:37] + "..."


def _cell(value):
    """A value as a table cell, '-' where that side sets nothing."""
    return "-" if value is None else describe(value)


class _TwoLineHeader(QHeaderView):
    """A horizontal header whose sections hold two lines: what the column
    is, and the match and profile it stands for.  The default header
    measures its text as one line and clips the rest, which would hide the
    half that identifies the column."""

    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Orientation.Horizontal, parent)
        self.setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignLeft
                                 | QtCore.Qt.AlignmentFlag.AlignVCenter)

    def sectionSizeFromContents(self, index):
        text = str(self.model().headerData(index, self.orientation()) or "")
        fm = self.fontMetrics()
        lines = text.split("\n")
        return QtCore.QSize(max(fm.horizontalAdvance(ln) for ln in lines) + 16,
                            fm.height() * len(lines) + 10)


class ProfileOfferDialog(QDialog):
    """Two profiles match one aircraft; what to do about it.

    ``choice`` after ``exec()`` is one of MERGE, DECLINE or LATER.  LATER is
    also what closing the dialog means, so the offer survives being
    dismissed by the window manager.
    """

    MERGE, DECLINE, LATER = 'merge', 'decline', 'later'

    def __init__(self, change, preview, labels=None, parent=None):
        """``preview`` is what ``merge_preview`` returns: the entries with
        their now and after values and sources, the counts, the other
        profiles that move, and the built-in's notes.  ``labels`` maps a
        setting name to the label the UI shows for it."""
        super().__init__(parent)
        self.choice = self.LATER
        self._change = change
        labels = labels or {}
        user, curated = change['user'], change['curated']
        winner_user = change.get('winner') == 'user'
        keep = bool(change.get('keep'))

        self.setWindowTitle("Multiple matching profiles")
        self.setMinimumWidth(760)
        layout = QVBoxLayout(self)

        if winner_user:
            what = (f"Your User Default profile <b>{user}</b> has matched this aircraft because it "
                    f"is the most specific entry.<br><br>"
                    f"However, the built-in profile <b>{curated}</b> also matches, and its settings "
                    f"are not being applied.")
        elif user == curated:
            what = (f"A built-in profile now ships for <b>{curated}</b>, the same match string as "
                    f"your User Default profile.<br><br>"
                    f"Your settings are applied on top of it, but the two entries compete for every "
                    f"aircraft this match string names.")
        else:
            why = ("it takes precedence on an equal match" if change.get('same_claim')
                   else "it is the most specific entry")
            what = (f"The built-in profile <b>{curated}</b> has matched this aircraft because {why}."
                    f"<br><br>"
                    f"However, your User Default profile <b>{user}</b> also matches and holds "
                    f"settings that are not being applied.")
        may_decline = offers_decline(change)
        if not may_decline:
            what += (f"<br><br>Until you merge, nothing you have set under <b>{user}</b> reaches "
                     f"this aircraft.")
        blurb = QLabel(what)
        blurb.setTextFormat(QtCore.Qt.TextFormat.RichText)
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        notes = (preview.get('curated_notes') or '').strip()
        if notes:
            about = QLabel(f"About the built-in <b>{curated}</b>: {notes}")
            about.setTextFormat(QtCore.Qt.TextFormat.RichText)
            about.setWordWrap(True)
            layout.addWidget(about)

        # What merging does, a sentence at a time.  Each line is written out
        # whole, with only counts and names in it, so what a user reads is
        # what is written here rather than something assembled at runtime.
        gains, restores, keeps = (preview.get(k, 0) for k in ('gains', 'restores', 'keeps'))
        clashes = preview.get('conflicts', 0)
        others = preview.get('other_profiles') or []
        said = []
        if restores:
            # "not applied today" rather than "the built-in does not contain":
            # this also counts a setting both sides hold, where the built-in's
            # value is in force today and theirs takes over after the merge.
            said.append(f"Merging starts applying {restores} of your settings that do not apply today.")
        if keeps:
            said.append(f"Merging changes nothing for {keeps} of your settings that already apply.")
        if gains:
            said.append(f"Merging adds {gains} of the built-in's settings that you do not have.")
        if not said:
            said.append("Merging changes nothing the aircraft flies with today.")
        if clashes:
            said.append(f"On {clashes} of the rows below the two set the same thing to different "
                        f"values; yours stands, as it does on any built-in.")
        if others:
            # Named as they arrive, not as they stand: the base of an aircraft
            # the user added cannot stay "User Default" under a built-in.
            said.append(f"These profiles under {user} come across too: {', '.join(others)}.")
        lead = QLabel(" ".join(said))
        lead.setWordWrap(True)
        layout.addWidget(lead)

        entries = preview.get('entries') or []
        built_in_head, yours_head = COLUMN_HEADS[preview.get('in_effect')]
        heads = [
            "Setting",
            f"{built_in_head}\n{preview.get('built_in_pattern')}",
            f"{yours_head}\n{preview.get('your_pattern')} ({preview.get('your_profile')})",
            f"Post Merge\n{preview.get('after_pattern')} ({preview.get('after_profile')})",
        ]
        table = QTableWidget(len(entries), len(heads), self)
        table.setHorizontalHeader(_TwoLineHeader(table))
        table.setHorizontalHeaderLabels(heads)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for col in range(len(heads)):
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        # Without this the slack between the last column and the table's edge
        # draws as a blank column of its own.
        table.horizontalHeader().setStretchLastSection(True)
        table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        bold = QtGui.QFont()
        bold.setBold(True)
        dim = QtGui.QColor("#8a8a8a" if G.useDarkMode else "#7a7a7a")
        warn = QtGui.QColor("#d99a2b" if G.useDarkMode else "#9a6b00")
        for row, e in enumerate(entries):
            name = labels.get(e['name'], e['name'])
            if e['kind'] == 'override':
                name += " (override)"
            clash = e['conflict']
            cells = [QTableWidgetItem(name),
                     QTableWidgetItem(_cell(e['built_in'])),
                     QTableWidgetItem(_cell(e['yours'])),
                     QTableWidgetItem(_cell(e['after']))]
            # A column can be elided, so every value carries its full text.
            for col, value in ((1, e['built_in']), (2, e['yours']), (3, e['after'])):
                if value:
                    cells[col].setToolTip(value)
            if clash:
                cells[1].setToolTip(f"The merge leaves your value in place, so the built-in's "
                                    f"{e['built_in']} is not applied.")
            for cell in cells:
                if clash:
                    cell.setForeground(warn)     # the two disagree and yours stands
                elif not e['changes']:
                    cell.setForeground(dim)      # nothing at stake on this row
                if e['changes']:
                    cell.setFont(bold)
            for col, cell in enumerate(cells):
                table.setItem(row, col, cell)
        table.setMaximumHeight(280)
        # The headers name a match and a profile, so how wide the table wants
        # to be depends on those strings and on the user's font.  Give the
        # dialog that width, within reason, rather than guessing one.
        table.resizeColumnsToContents()
        wanted = (sum(table.columnWidth(c) for c in range(table.columnCount()))
                  + table.verticalScrollBar().sizeHint().width() + 2 * table.frameWidth() + 24)
        self.setMinimumWidth(max(self.minimumWidth(), min(wanted, 1150)))
        layout.addWidget(table)

        buttons = QDialogButtonBox(self)
        merge_btn = QPushButton("Merge into the built-in")
        if keep:
            merge_btn.setToolTip(
                f"Copy every profile under {user} onto the built-in {curated} as user profiles "
                f"of it, and switch this aircraft to the copy." + NL +
                f"{user} stays in place for any other aircraft it matches." + NL +
                # Without this the line above reads as though every other
                # aircraft the built-in claims will ask the same question.
                f"You will not be asked again for any aircraft {curated} matches.")
        elif user == curated:
            # Same string: the rows already sit under it, so nothing travels.
            merge_btn.setToolTip(
                f"Your profiles already sit under {curated}, so nothing moves." + NL +
                "Your own aircraft entry is dropped and its settings become a user profile of "
                "the built-in, so the two stop competing." + NL +
                "The aircraft flies with exactly what it flies with now.")
        else:
            merge_btn.setToolTip(
                f"Move every profile under {user} onto the built-in {curated} as user profiles "
                f"of it, then remove {user}." + NL + "Nothing you have set is lost.")
        merge_btn.setDefault(True)
        buttons.addButton(merge_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        merge_btn.clicked.connect(self._confirm_merge if not keep else lambda: self._done(self.MERGE))

        later_btn = QPushButton("Not now")
        later_btn.setToolTip("Close without deciding." + NL +
                             "You will be asked again the next time this aircraft loads.")
        buttons.addButton(later_btn, QDialogButtonBox.ButtonRole.RejectRole)
        later_btn.clicked.connect(lambda: self._done(self.LATER))

        if may_decline:
            if winner_user:
                decline_btn = QPushButton("Keep mine")
                decline_btn.setToolTip(
                    f"Leave {user} naming this aircraft: nothing about what it flies changes." + NL +
                    f"You will not be asked again unless the built-in {curated} changes.")
            else:
                decline_btn = QPushButton("Don't ask again")
                decline_btn.setToolTip(
                    "Leave things as they are: nothing about what this aircraft flies changes." + NL +
                    f"You will not be asked again unless the built-in {curated} changes.")
            buttons.addButton(decline_btn, QDialogButtonBox.ButtonRole.ActionRole)
            decline_btn.clicked.connect(lambda: self._done(self.DECLINE))

        layout.addWidget(buttons)
        hint = QLabel("Hover a button to see exactly what it does.")
        # palette(mid) is all but invisible on the dark theme, which is where
        # this dialog mostly appears.
        hint.setStyleSheet("color: #a0a0a0;" if G.useDarkMode else "color: #5a5a5a;")
        layout.addWidget(hint)

    def _confirm_merge(self):
        user, curated = self._change['user'], self._change['curated']
        if user == curated:
            body = (f"Your own entry for {curated} is dropped, and the settings under it become a "
                    f"user profile of the built-in of the same name." + NL + NL +
                    "The aircraft carries on flying with exactly what it flies with now. What "
                    "changes is that your entry stops competing with the built-in.")
        else:
            body = (f"Your settings under {user} become profiles of the built-in {curated}, which "
                    f"then takes over this aircraft. {user} itself is removed." + NL + NL +
                    "Nothing you have set is lost.")
        ok = QMessageBox.question(
            self, "Move your settings to the built-in?", body,
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Yes)
        if ok == QMessageBox.StandardButton.Yes:
            self._done(self.MERGE)

    def _done(self, choice):
        self.choice = choice
        logging.info(f"Profile offer for {self._change['aircraft']}: {choice}")
        self.accept()
