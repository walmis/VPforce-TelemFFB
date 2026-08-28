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

"""What the DirectInput tap looks like in one sim's install, as a panel.

The states worth telling apart are not "installed" and "not installed".  A
partial install is the one that misleads - DCS ships two executables, and a
wrapper beside only one of them does nothing at all if the user launches the
other.  A foreign dinput8.dll matters too: it belongs to something the user
installed deliberately, and saying "not installed" would invite them to
overwrite it - so replacing one is offered, but only after asking.  Which
tool it belongs to is not something we try to work out: several install a
proxy under this name, and the user knows what they put there.

Installing and configuring are separate buttons on purpose.  One moves a
DLL; the other decides what the game hands over.  Folded together, every
reinstall would re-ask about devices, and a config could not be changed
without touching the wrapper.
"""

import os

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPalette

from telemffb.custom_widgets import InfoLabel
from telemffb.TapDeviceDialog import TapDeviceDialog
from telemffb import tap_install
from telemffb.tap_config import (already_blocked, already_ordered,
                                 already_tapped, amend, lines_for,
                                 read as read_config_text,
                                 retired_identities)
from telemffb.tap_install import (SimStatus, VJOY_RULE, WRAPPER_CONFIG,
                                  WRAPPER_NAME, WrapperState, block_line,
                                  config_label,
                                  config_link,
                                  config_paths, generate_config, read_configs,
                                  install, order_line, remove,
                                  rule_line, write_one_config)

#: The user closed the device dialog without choosing.  Distinct from
#: None, which means "install the wrapper and leave any config alone".
CANCELLED = object()


def ask_for_devices(sim, devices, existing=None, parent=None, preview=None):
    """Which of this sim's devices should have their effects relayed to
    TelemFFB.

    Returns ``(devices, retire_lines, ordered, blocked)``, or None if the
    user cancelled.

    The devices are handed in rather than read from settings here: while the
    settings dialog is open the user's latest pick is not saved yet, and
    asking the registry would offer them the device they just replaced.

    A module-level seam so the question can be answered without a modal
    dialog - a test that clicks Install would otherwise wait forever for a
    user who is not there.
    """
    dialog = TapDeviceDialog(sim, devices, existing=existing, parent=parent,
                             preview=preview)
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None
    return (dialog.chosen(), dialog.retire_lines(), dialog.ordered(),
            dialog.blocked())


def confirm_legacy_upgrade(sim_name, directories, parent=None):
    """Confirm upgrading a recognized ffb-fix wrapper.

    The wrapper identified itself (LEGACY_MARKERS), so the user is asked
    to confirm an upgrade, not to classify a DLL they installed a year
    ago and may not remember.  Affirmative where confirm_overwrite is
    cautious: replacing ffb-fix with its successor is safe, and the ini
    beside it is kept.

    A module-level seam, for the same reason as ask_for_devices.
    """
    where = "\n".join(f"    {d}" for d in directories)
    answer = QtWidgets.QMessageBox.question(
        parent, "DirectInput Tap Install",
        f"{sim_name} has the community ffb-fix wrapper installed:\n\n"
        f"{where}\n\n"
        "TelemFFB's DirectInput Tap is built from it and replaces it in "
        "place. Everything it does keeps working: the dinput8.ini beside "
        "it is kept, and every rule in it stays in effect.\n\n"
        "TelemFFB will show the device configuration next, with the "
        "proposed additions for your devices - nothing is added to the "
        "file until you confirm it there.\n\n"
        "Upgrade it to TelemFFB's wrapper?",
        QtWidgets.QMessageBox.StandardButton.Yes |
        QtWidgets.QMessageBox.StandardButton.No,
        QtWidgets.QMessageBox.StandardButton.Yes)
    return answer == QtWidgets.QMessageBox.StandardButton.Yes


def confirm_overwrite(sim_name, directories, parent=None):
    """Ask before replacing a dinput8.dll that is not ours and not the
    recognized ffb-fix wrapper.

    We do not try to work out whose it is.  Several tools install a proxy
    under this name and the file does not reliably say which, so the useful
    thing is to name what is there and let the person who installed it
    decide - guessing would either block a legitimate install or break a
    tool the user wanted.

    A module-level seam, for the same reason as ask_for_devices.
    """
    where = "\n".join(f"    {d}" for d in directories)
    answer = QtWidgets.QMessageBox.question(
        parent, "DirectInput Tap Install",
        f"A dinput8.dll that is not TelemFFB's is already installed in "
        f"{sim_name}:\n\n{where}\n\n"
        "If it is the ffb-fix wrapper TelemFFB's is built from, replacing "
        "it is safe: the dinput8.ini beside it is kept and keeps working "
        "as it is. If it belongs to another mod or utility, replacing it "
        "will stop that from working.\n\n"
        "Replace it with TelemFFB's wrapper?",
        QtWidgets.QMessageBox.StandardButton.Yes |
        QtWidgets.QMessageBox.StandardButton.No,
        QtWidgets.QMessageBox.StandardButton.No)
    return answer == QtWidgets.QMessageBox.StandardButton.Yes


#: Detail lines sit under the sim name rather than level with it.
INDENT = 14

#: How each target's state reads, and whether it wants attention.
TARGET_TEXT = {
    WrapperState.TAP: ("installed", False),
    WrapperState.ABSENT: ("not installed", False),
    WrapperState.LEGACY: ("ffb-fix wrapper installed - Install upgrades "
                          "it in place", True),
    WrapperState.FOREIGN: ("another dinput8.dll installed", True),
    WrapperState.UNREADABLE: ("present, unreadable", True),
}


class TapStatusPanel(QtWidgets.QWidget):
    """One sim's tap installation, described - and changed."""

    #: Emitted after the wrapper is installed or removed, so the dialog can
    #: re-scan rather than trust this panel's idea of what happened.
    changed = pyqtSignal()

    def __init__(self, status: SimStatus, parent=None, devices=None):
        super().__init__(parent)
        #: Callable returning the devices configured right now.  A callable
        #: rather than a list: the settings dialog can change the selection
        #: while this panel is on screen, and the answer has to be current
        #: at the moment the user asks, not at the moment we were built.
        self._devices = devices or (lambda: [])
        self._outer = QtWidgets.QVBoxLayout(self)
        self._outer.setContentsMargins(8, 4, 8, 4)
        self._outer.setSpacing(1)
        self._body = None
        #: What the files looked like before each action this panel ran,
        #: newest last - so the dialog can put them back on Cancel.
        self._undo = []
        self.set_status(status)

    # ------------------------------------------------------------------
    def _color(self, kind):
        """Palette-derived, so it reads on either theme."""
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        if kind == "attention":
            # amber carries on both grounds where a red would shout
            return "#d08a2a" if window.lightness() < 128 else "#a35f00"
        if kind == "dim":
            dim = QtWidgets.QApplication.palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
            return dim.name()
        return text.name()

    def _label(self, text, kind="normal", tooltip="", bold=False):
        label = QtWidgets.QLabel(text)
        weight = "font-weight: 600;" if bold else ""
        label.setStyleSheet(f"color: {self._color(kind)};{weight}")
        if tooltip:
            label.setToolTip(tooltip)
        return label

    def _link(self, path, label="open dinput8.ini"):
        """A link that opens the config in the system's text editor.

        Qt opens it itself - QLabel with openExternalLinks needs no handler,
        and hands the URL to the desktop the same way a browser link would.
        """
        widget = QtWidgets.QLabel(config_link(path, label))
        widget.setOpenExternalLinks(True)
        widget.setToolTip(path)
        return widget

    def _row(self, *widgets, indent=0):
        """One line of labels, packed left with the slack after them."""
        line = QtWidgets.QWidget()
        box = QtWidgets.QHBoxLayout(line)
        box.setContentsMargins(indent, 0, 0, 0)
        box.setSpacing(10)
        for widget in widgets:
            box.addWidget(widget)
        box.addStretch(1)
        return line

    # ------------------------------------------------------------------
    def set_status(self, status: SimStatus):
        """Render a status, replacing whatever was shown."""
        self.status = status
        if self._body is not None:
            # setParent(None) detaches it now; deleteLater alone only queues
            # the deletion, leaving the old labels in the widget tree until
            # the event loop gets round to them
            self._outer.removeWidget(self._body)
            self._body.setParent(None)
            self._body.deleteLater()

        self._body = QtWidgets.QWidget(self)
        body = QtWidgets.QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(1)

        headline, kind = self._headline(status)
        parts = [self._label(status.sim.name, bold=True)]
        if headline:
            parts.append(self._label(headline, kind))
        header = self._row(*parts)
        for button in self._buttons(status):
            header.layout().addWidget(button)
        body.addWidget(header)

        if not status.found:
            body.addWidget(self._row(
                self._label("Install not found. Set the path in this tab, or "
                            "install the sim, and reopen this dialog.", "dim"),
                indent=INDENT))
            self._outer.addWidget(self._body)
            return

        # the resolved root, with how we came to it - a user whose sim was
        # found somewhere unexpected needs to see which one we mean
        body.addWidget(self._row(
            self._label(status.root, "dim", status.root),
            self._label(f"({status.provenance})", "dim"),
            indent=INDENT))

        # the targets get a grid of their own, so its columns are sized by
        # the short strings in them rather than by the sim's name
        targets = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(targets)
        grid.setContentsMargins(INDENT, 2, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(1)
        for row, target in enumerate(status.targets):
            text, attention = TARGET_TEXT.get(target.state, (target.state, True))
            # a gap in an otherwise complete install is the misleading case:
            # the game may launch the executable that was missed, so the row
            # that is missing one carries the warning
            if target.state == WrapperState.ABSENT and status.partially_installed:
                attention = True
            # relative to the root, not just the last component: IL-2's
            # target basename is "game", which names nothing on its own
            try:
                where = os.path.relpath(target.directory, status.root)
            except ValueError:
                where = target.directory
            grid.addWidget(self._label(where, "dim", target.directory), row, 0)
            grid.addWidget(
                self._label(text, "attention" if attention else "normal"), row, 1)
            if target.state == WrapperState.TAP:
                version = (f"v{target.version}" if target.version
                           else "version unknown")
                tip = ("" if target.version else
                       "Built before the wrapper carried a version resource")
                style = "dim"
                # Say how the installed build compares with the one
                # TelemFFB ships: without it, reinstalling the same
                # version looks identical to a failed update.
                bundled = tap_install.bundled_version()
                if tap_install.version_is_newer(bundled, target.version):
                    version = f"{version} → v{bundled} available"
                    tip = (f"TelemFFB ships v{bundled}; use Install to "
                           "update this copy")
                    style = "attention"
                elif bundled and target.version == bundled:
                    version = f"{version} (current)"
                    tip = "Matches the wrapper TelemFFB ships"
                grid.addWidget(self._label(version, style, tip), row, 2)
            # Everything this panel says about a config is a summary; the
            # file is the only thing that settles a disagreement with it.
            if target.has_config:
                grid.addWidget(
                    self._link(os.path.join(target.directory, WRAPPER_CONFIG)),
                    row, 3)
        grid.setColumnStretch(4, 1)
        body.addWidget(targets)

        if status.stale_rules:
            # the silent failure worth a line of its own: the game hands a
            # device TelemFFB is not driving, and the one that is configured
            # keeps the game's own force feedback.  Nothing else reports it.
            named = ", ".join(sorted({r.key for r in status.stale_rules}))
            body.addWidget(self._row(self._label(
                f"configured for a device that is not selected: {named}",
                "attention",
                "Use Configure Devices to point it at what is selected now"),
                indent=INDENT))

        self._outer.addWidget(self._body)

    def _buttons(self, status: SimStatus):
        """Install and Remove, offered only where they would do something."""
        if not status.found or not status.targets:
            return []
        states = {t.state for t in status.targets}
        buttons = []

        if not status.installed:
            # offered even over a dinput8.dll that is not ours: whether that
            # one matters is the user's call, and _install asks before
            # replacing it
            install_button = QtWidgets.QPushButton(
                "Complete Install" if status.partially_installed
                else "Install")
            install_button.clicked.connect(self._install)
            buttons.append(install_button)
        else:
            update = QtWidgets.QPushButton("Reinstall")
            update.setToolTip("Copy the bundled wrapper over the installed one")
            update.clicked.connect(self._install)
            buttons.append(update)

        if WrapperState.TAP in states:
            # Separate from installing: one button moves a DLL, the other
            # decides what the game hands over.  Folding them together would
            # mean re-asking about devices every reinstall.
            configure = QtWidgets.QPushButton("Configure Devices...")
            configure.setToolTip(
                "Choose which devices this sim hands to TelemFFB")
            configure.clicked.connect(self._configure)
            buttons.append(configure)

            # only ever ours: removing a dinput8.dll we did not put there
            # would delete somebody else's file
            remove_button = QtWidgets.QPushButton("Remove")
            remove_button.clicked.connect(self._remove)
            buttons.append(remove_button)
        return buttons

    # ------------------------------------------------------------------
    def _install(self):
        # asked before anything else: there is no point choosing devices for
        # an install the user is about to call off.  A recognized ffb-fix
        # wrapper gets the affirmative upgrade question; anything unknown
        # keeps the cautious one, which also covers a mixed tree.
        foreign = [t.directory for t in self.status.targets
                   if t.state == WrapperState.FOREIGN]
        legacy = [t.directory for t in self.status.targets
                  if t.state == WrapperState.LEGACY]
        if foreign:
            if not confirm_overwrite(self.status.sim.name, foreign + legacy,
                                     parent=self):
                return
        elif legacy:
            if not confirm_legacy_upgrade(self.status.sim.name, legacy,
                                          parent=self):
                return

        config_text = self._config_to_write()
        if config_text is CANCELLED:
            return
        # A confirmed ffb-fix upgrade flows straight into the adoption
        # dialog: the config already exists, so the proposed additions (the
        # tap rule for the selected joystick, blocks for uncovered devices)
        # open pre-selected with the preview available, and nothing is
        # written to the file until the user confirms.  One click fewer
        # than making them find Configure Devices - with the same final say.
        adopt = bool(legacy) and not foreign and any(
            t.has_config for t in self.status.targets)
        # bound here rather than passed through _run, so every action stays a
        # one-argument call; `install` is still looked up when the lambda runs
        self._run(lambda status: install(status, config_text,
                                         overwrite_foreign=bool(foreign or
                                                                legacy)),
                  "Install", then=self._configure if adopt else None)

    def _config_to_write(self):
        """The config this install should lay down, or None to leave it alone.

        Only asked when the sim has no config at all.  An existing file is
        the user's - hand-written, or a setup that predates us, or one we
        wrote and they have since tuned - and rewriting it during what they
        asked to be an install of the wrapper would discard that silently.
        Configure Devices is where a config gets changed.
        """
        if any(target.has_config for target in self.status.targets):
            return None

        answer = ask_for_devices(self.status.sim, self._devices(),
                                 parent=self)
        if answer is None:
            return CANCELLED
        chosen, _, ordered, blocked = answer
        return generate_config(chosen, ordered, blocked)

    def _configure(self):
        """Change which devices this sim hands over.

        Adopts whatever config is already there rather than replacing it:
        a user arriving from ffb-fix keeps every rule and comment they wrote,
        and the only lines that change are the ones they were asked about.
        """
        # Every config this sim holds, not just the first.  A sim can hold
        # two that differ, and writing one answer over both would discard
        # whichever was not consulted.
        configs = read_configs(self.status)
        existing = configs[0][1] if configs else None

        def each(chosen, retire, ordered, blocked):
            """(directory, current, proposed) for every file involved.

            The dialog reports its retirements as line numbers in the file
            it was built from, so they are translated to the rules they name
            before being looked up again in each of the others.
            """

            if not configs:
                fresh = generate_config(chosen, ordered, blocked)
                return [(t.directory, "", fresh) for t in self.status.targets]

            names = retired_identities(existing, retire)
            panes = []
            for directory, text in configs:
                lines = lines_for(text, *names)
                # A device this file already hands over needs no second,
                # identical rule - which is what confirming the dialog on an
                # already-configured sim used to add.
                wanted = [rule_line(d) for d in chosen
                          if not already_tapped(text, (d.vid, d.pid),
                                                d.ident, lines)]
                # Blocks for the roles this sim never drives, where the file
                # does not already keep them out.
                wanted += [block_line(d) for d in blocked
                           if not already_blocked(text, (d.vid, d.pid),
                                                  d.ident, lines)]
                # vJoy too, unless the file already says something about
                # it - a legacy sample did, and a rule of theirs wins.
                if not already_blocked(text, None, "vJoy", lines):
                    wanted.append(VJOY_RULE)
                # Same for ordering: a device this file already reports
                # first needs no second entry saying so.
                promote = [d for d in ordered
                           if not already_ordered(text, (d.vid, d.pid),
                                                  d.ident, lines)]
                # New entries take positions AFTER what the file already
                # holds - two entries sharing a rank is a conflict the
                # wrapper never resolves predictably.  An entry naming
                # hardware we cannot match (a renamed device, or a stick
                # that is not ours to judge) stays where it is; it matches
                # nothing under the wrapper's rules, so numbering past it
                # keeps the ordering correct without touching it.
                taken = [int(e.position) for e in read_config_text(text).order
                         if e.line not in set(lines) and e.position.isdigit()]
                order_lines = [order_line(d, i)
                               for i, d in enumerate(
                                   promote, start=max(taken, default=0) + 1)]
                panes.append((directory, text,
                              amend(text, wanted, lines, order=order_lines,
                                    order_even_if_present=True)))
            return panes

        def preview(chosen, retire, ordered, blocked):
            return [(config_label(os.path.join(directory, WRAPPER_CONFIG),
                                  self.status.root)[len("open "):],
                     current, proposed)
                    for directory, current, proposed
                    in each(chosen, retire, ordered, blocked)]

        answer = ask_for_devices(self.status.sim, self._devices(), existing,
                                 parent=self, preview=preview)
        if answer is None:
            return

        def write(_status):
            return [write_one_config(directory, proposed)
                    for directory, _, proposed in each(*answer)]

        self._run(write, "Configure")

    def _remove(self):
        self._run(remove, "Remove")

    def _run(self, action, title, then=None):
        """Run an action and report what happened to each target.

        Reported per directory rather than as one verdict: with two targets
        one can succeed while the other is locked by a running game, and
        "failed" alone would hide that half the job is done.

        The files an action can touch are snapshotted first.  These
        buttons act at once - the panel shows what is really there - but
        the dialog around them is a Cancel/Save dialog, and a wrapper
        installed and then cancelled out of would otherwise stay active
        in the game folder with nothing in TelemFFB saying so.

        ``then`` runs after a fully successful action, before the changed
        signal - so a follow-on step (the adoption dialog after an ffb-fix
        upgrade) opens from a panel the rescan has not yet replaced.
        """
        self._undo.append(self._snapshot())
        outcomes = action(self.status)
        failures = [o for o in outcomes if not o.ok]
        if not failures and then is not None:
            then()
        if failures:
            lines = [f"{os.path.basename(o.directory)}: {o.detail}"
                     for o in failures]
            done = [o for o in outcomes if o.ok]
            if done:
                lines.append("")
                lines.append("Completed for: " + ", ".join(
                    os.path.basename(o.directory) for o in done))
            QtWidgets.QMessageBox.warning(
                self, f"DirectInput Tap {title}",
                f"{self.status.sim.name}\n\n" + "\n".join(lines))
        self.changed.emit()

    def _snapshot(self):
        """The bytes of every file an action may touch; None where absent."""
        taken = {}
        for target in self.status.targets:
            for name in (WRAPPER_NAME, WRAPPER_CONFIG):
                path = os.path.join(target.directory, name)
                try:
                    with open(path, "rb") as handle:
                        taken[path] = handle.read()
                except FileNotFoundError:
                    taken[path] = None
                except OSError:
                    # unreadable now means unrestorable later; leaving it
                    # out is better than restoring a guess
                    logging.warning(f"DirectInput tap: could not snapshot {path}")
        return taken

    def undo_all(self):
        """Put back everything this panel's actions changed, newest first.

        Returns the paths that could not be restored - a game holding the
        wrapper open is the usual reason - so the dialog can say so rather
        than let a Cancel silently leave half of what it undid.
        """
        failed = []
        while self._undo:
            for path, content in self._undo.pop().items():
                try:
                    if content is None:
                        if os.path.exists(path):
                            os.remove(path)
                    else:
                        with open(path, "wb") as handle:
                            handle.write(content)
                except OSError:
                    failed.append(path)
        return failed

    def commit(self):
        """The dialog was saved: what the actions did is now meant."""
        self._undo.clear()

    def _headline(self, status: SimStatus):
        """A phrase for the whole sim, where the rows below cannot say it.

        Empty once there are target rows: they state the same thing per
        directory and in more detail, and a summary beside them only
        competes with the buttons for a line that has no room for both.
        """
        if not status.found:
            return "not detected", "dim"
        if not status.targets:
            return "no executable found to install beside", "attention"
        return "", "normal"
