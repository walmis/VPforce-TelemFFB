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

"""Choosing which devices a sim hands to TelemFFB.

Asked rather than assumed.  Handing a device over means the game stops
rendering force feedback for it, so choosing wrongly on the user's behalf is
not cosmetic - it is a stick that goes quiet in a cockpit, with the cause
several menus away from the symptom.

Three filters decide what appears here, and each exists to prevent a
different silent failure:

* only devices configured in TelemFFB, because a device TelemFFB does not
  drive has nothing to render the effects it takes away;
* only roles the sim renders to, because tapping a control a sim never sends
  effects to looks exactly like TelemFFB not working;
* only devices whose USB ids are known, because a rule is keyed on ids and
  there is nothing to write without them.

Where the sim already has a config, any rule that would take precedence over
ours is shown alongside the device, because the wrapper takes the first match
and a rule appended below an existing one is dead on arrival.
"""

from typing import List, Optional, Sequence

from PyQt6 import QtWidgets
from PyQt6.QtGui import QPalette

from telemffb.tap_config import (blocking_rules, order_matches, read,
                                 shadowing_rules, stale_tap_rules)
from telemffb.tap_install import devices_a_sim_drives
from telemffb.utils import device_display_name

INDENT = 22


class TapDeviceDialog(QtWidgets.QDialog):
    """Pick the devices to write tap rules for."""

    def __init__(self, sim, devices: Sequence, existing: Optional[str] = None,
                 parent=None, preview=None):
        super().__init__(parent)
        self.setWindowTitle("DirectInput Tap Devices")
        self._rows = []                     # (checkbox, device, [(box, rule)])
        self._shadows = {}                  # device key -> existing rules
        self._orphans = []                  # (checkbox, rule) for stale rules
        self._order_box = None              # offered only where a sim needs it
        self._order_entries = []            # existing entries for these devices
        self._ok = None                     # disabled when nothing would change
        self._blocks = []                   # (checkbox, device, [rules])
        self._facts = read(existing) if existing else None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)

        # a label, not a question: the boxes below are the questions
        heading = QtWidgets.QLabel(
            f"DirectInput Tap configuration for <b>{sim.name}</b>")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(self._note(
            "Checking a device configures the tap to intercept the game's "
            "effects for it, so TelemFFB can render them in the "
            "'Game Managed (DirectInput Tap)' spring mode."))

        offered = [d for d in devices if d.usable and sim.renders_to(d.role)]
        for device in offered:
            layout.addLayout(self._row(device))

        if not offered:
            layout.addWidget(self._nothing_to_offer(devices, sim))

        # Ordering: only where the sim is known to need it, and only when
        # there is something to order.  A game that hands force feedback to
        # whichever device it saw first can leave a tapped device with no
        # effects at all - and then there is nothing for TelemFFB to render,
        # with no error to explain it.
        if sim.supports_ordering and offered:
            # Ticked reflects what the file says, the same way a device row
            # does, and unticking is how the ordering comes back out.  This
            # was disabled whenever a [DeviceOrder] section existed, which
            # left the one config that might need changing unable to.
            self._order_entries = [
                entry for entry in (self._facts.order if self._facts else [])
                if any(order_matches(entry, (d.vid, d.pid), d.ident)
                       for d in offered)]
            self._order_box = QtWidgets.QCheckBox(
                f"Make {sim.name} detect this joystick before any other device")
            self._order_box.setChecked(True)
            self._order_box.setToolTip(
                "Adds a [DeviceOrder] section so the game enumerates these "
                "devices ahead of the rest. Without it the game may hand its "
                "effects to a different device entirely.")
            layout.addWidget(self._order_box)

        # Rules for hardware no longer selected anywhere.  Shown unchecked,
        # which in this dialog means "not tapped" - so leaving them
        # alone clears them out.  Listed rather than removed silently: the
        # device may simply be unplugged today.
        stale = (stale_tap_rules(self._facts,
                                 devices_a_sim_drives(sim, devices))
                 if self._facts else [])
        if stale:
            layout.addWidget(self._note(
                "These are still in the configuration but no longer "
                "selected in TelemFFB. Leave one unchecked to remove it."))
            for rule in stale:
                layout.addLayout(self._orphan_row(rule))

        # Devices in a role this sim never renders to.  The game sends them
        # nothing either way, but while they enumerate as force feedback
        # devices they can still take a slot from the stick that wanted one -
        # which is the whole reason a configured stick can end up silent.
        #
        # Not offered where the sim does render to the role: IL-2 Korea
        # drives pedals, so blocking its pedals would take away something
        # real.  That falls out of the capability table rather than being a
        # special case here.
        skipped = [d for d in devices if not sim.renders_to(d.role)]
        blockable = [d for d in skipped if d.usable]
        if blockable:
            names = ", ".join(device_display_name(d.role) for d in skipped)
            layout.addWidget(self._note(
                f"{sim.name} does not render force feedback for {names}. "
                "Blocking them keeps the game from counting them as force "
                "feedback devices at all."))
            for device in blockable:
                layout.addLayout(self._block_row(device))
        elif skipped:
            names = ", ".join(device_display_name(d.role) for d in skipped)
            layout.addWidget(self._note(
                f"{sim.name} does not render force feedback for {names}."))

        layout.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        # No plain link to the config here: this dialog is about to change
        # that file, and its current state alone is half the picture.  The
        # comparison is the honest version of the same offer.
        self._preview = preview
        self._look = None
        if preview is not None:
            self._look = buttons.addButton(
                "Preview",
                QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
            self._look.setToolTip(
                "Show the config as it is, beside how it would be")
            self._look.clicked.connect(self._show_preview)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # OK and Preview both go with whether anything would change: a
        # preview of no change is the same non-answer as an OK that writes
        # an identical file.  Recomputed on every tick because what counts
        # as a change depends entirely on which boxes are set.
        self._ok = buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Ok)
        for box in self.findChildren(QtWidgets.QCheckBox):
            box.toggled.connect(self._update_ok)
        self._update_ok()

    # ------------------------------------------------------------------
    def _update_ok(self):
        """Enable OK only where confirming would change a file."""
        if self._preview is None or self._ok is None:
            return
        from telemffb.TapDiffDialog import aligned_diff, changed

        panes = self._preview(self.chosen(), self.retire_lines(),
                              self.ordered(), self.blocked())
        differs = any(changed(aligned_diff(before, after))
                      for _, before, after in panes)
        self._ok.setEnabled(differs)
        if self._look is not None:
            self._look.setEnabled(differs)

    def _show_preview(self):
        """What the file would look like given the boxes as they stand now.

        Computed on the click, not when the dialog opened: the whole point
        is to show the consequence of the choices actually made.
        """
        from telemffb.TapDiffDialog import TapDiffDialog

        panes = self._preview(self.chosen(), self.retire_lines(),
                              self.ordered(), self.blocked())
        TapDiffDialog("DirectInput Tap - proposed changes", panes, self).exec()

    def _block_row(self, device) -> QtWidgets.QHBoxLayout:
        """One device the sim will not drive, offered for blocking."""
        existing = (blocking_rules(self._facts, (device.vid, device.pid),
                                   device.ident) if self._facts else [])
        box = QtWidgets.QCheckBox(
            f"Block {device_display_name(device.role)}: "
            f"{device.ident or 'unnamed device'}  ({device.key})")
        box.setChecked(True)
        box.setToolTip(
            "Keeps the game from counting this device as a force feedback "
            "device. It sends it nothing either way, but an unblocked one "
            "can still take a slot from the stick you want driven.")
        self._blocks.append((box, device, existing))
        row = QtWidgets.QHBoxLayout()
        row.addSpacing(INDENT)
        row.addWidget(box)
        return row

    def _orphan_row(self, rule) -> QtWidgets.QHBoxLayout:
        """A tap rule whose device is not selected any more."""
        label = rule.comment or "device not selected"
        box = QtWidgets.QCheckBox(f"{label}  ({rule.key})")
        box.setChecked(False)
        box.setToolTip(
            "No configured device has these ids. The name is whatever was "
            "written when the rule was made, so it may be out of date.")
        self._orphans.append((box, rule))
        row = QtWidgets.QHBoxLayout()
        row.addSpacing(INDENT)
        row.addWidget(box)
        return row

    def _row(self, device) -> QtWidgets.QVBoxLayout:
        """One device, and anything in the config that would outrank it."""
        column = QtWidgets.QVBoxLayout()
        column.setSpacing(2)

        shadows = (shadowing_rules(self._facts, (device.vid, device.pid),
                                   device.ident) if self._facts else [])
        self._shadows[device.key] = shadows
        box = QtWidgets.QCheckBox(self._label(device))
        # Checked by default, whatever the config says.  A device sitting in
        # a slot is one the user chose, and the reason to open this dialog is
        # to tap it; a rule for some other device says nothing about
        # what they want for this one.  Turning it off is one click, and the
        # rule that would be removed is listed below rather than vanishing.
        box.setChecked(True)
        box.setToolTip(f"USB {device.key} - the rule is keyed on these ids, "
                       "not on the name")
        column.addWidget(box)

        replacements = []
        for rule in shadows:
            if rule.is_tap:
                continue        # already tapped; nothing to displace
            retire = QtWidgets.QCheckBox(
                f"replace existing rule  {rule.key}={rule.value}"
                f"  with new rule  {device.key}=tap")
            retire.setChecked(True)
            retire.setToolTip(
                "The wrapper uses the first rule that matches, so this one "
                "would win and TelemFFB would never see the device. It is "
                "commented out, not deleted.")
            retire.setContentsMargins(INDENT, 0, 0, 0)
            wrapper = QtWidgets.QHBoxLayout()
            wrapper.addSpacing(INDENT)
            wrapper.addWidget(retire)
            column.addLayout(wrapper)
            replacements.append((retire, rule))
            box.toggled.connect(retire.setEnabled)
            retire.setEnabled(box.isChecked())

        self._rows.append((box, device, replacements))
        return column

    def _note(self, text: str) -> QtWidgets.QLabel:
        """Secondary text, readable on either theme.

        Not palette(mid): that is a frame-shading color, which on a dark
        theme is a dark gray on a dark ground and effectively invisible.
        The disabled window-text color is the one Qt defines as readable
        but de-emphasized, whichever way round the theme runs.
        """
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        dim = QtWidgets.QApplication.palette().color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        label.setStyleSheet(f"color: {dim.name()};")
        return label

    @staticmethod
    def _label(device) -> str:
        return (f"{device_display_name(device.role)}: "
                f"{device.ident or 'unnamed device'}  ({device.key})")

    def _nothing_to_offer(self, devices, sim) -> QtWidgets.QLabel:
        """Say which of the three reasons applies; one message for all
        three would misdirect."""
        if not devices:
            text = ("No devices are configured in TelemFFB, so there is "
                    "nothing to write a rule for. The wrapper can still be "
                    "installed; it will do nothing until a rule exists.")
        elif not any(d.usable for d in devices):
            text = ("None of the configured devices report USB ids, so no "
                    "rule can be written for them.")
        else:
            roles = ", ".join(device_display_name(r) for r in sim.ffb_roles)
            text = (f"{sim.name} renders force feedback to the {roles} only, "
                    f"and no such device is configured in TelemFFB.")
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        return label

    # ------------------------------------------------------------------
    def chosen(self) -> List:
        """The devices to write rules for."""
        return [device for box, device, _ in self._rows if box.isChecked()]

    def blocked(self) -> List:
        """Devices to keep the game's force feedback away from."""
        return [device for box, device, _ in self._blocks if box.isChecked()]

    def ordered(self) -> List:
        """The devices to report to the game first, if the user asked.

        The same devices they chose to tap: ordering exists so the game
        drives them, and a device it does not drive has nothing to tap.
        """
        if self._order_box is None or not self._order_box.isChecked():
            return []
        return self.chosen()

    def retire_lines(self) -> List[int]:
        """Every existing rule line that should stop applying.

        Two kinds, both the user's explicit choice:

        * a rule outranking a device they chose to tap, where they agreed to
          replace it - never one for a device they left unchecked, since that
          would take force feedback away and put nothing in its place;
        * our own tap rule for a device they have just unchecked, which is
          what unchecking means.  Without this the box would move and the
          file would not.
        """
        lines = set()
        # Unticking the ordering is how it is taken back out, so the entries
        # it names are retired along with everything else.
        if self._order_box is not None and not self._order_box.isChecked():
            lines.update(entry.line for entry in self._order_entries)
        # Unticking a block is how an existing one is taken back out.
        for box, _, existing in self._blocks:
            if not box.isChecked():
                lines.update(rule.line for rule in existing)
        for box, rule in self._orphans:
            if not box.isChecked():
                lines.add(rule.line)
        for box, device, replacements in self._rows:
            if box.isChecked():
                lines.update(rule.line for retire, rule in replacements
                             if retire.isChecked())
            else:
                lines.update(rule.line
                             for rule in self._shadows.get(device.key, [])
                             if rule.is_tap)
        return sorted(lines)
