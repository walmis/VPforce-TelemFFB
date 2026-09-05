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

"""Startup offer to put back tap files a game folder has lost.

A sim the user opted into the tap is expected to hold TelemFFB's wrapper
and its ``dinput8.ini`` beside every executable.  A game update rewrites
the game's folders and takes both files with it, and nothing then says
so: the sim's tab shows "not installed" only when somebody opens it, and
until then the game keeps its force feedback to itself.  This dialog does
the rounds at startup - every enabled, tap-opted sim with a wrapper or a
config gone, one prompt, each sim deselectable.

The wrapper is reinstalled where it is absent.  A config is put back the
way a fresh install would write one: copied from the sim's other
executable when one still has it, generated from the FFB-fix-only mode
when the sim is set to that, and otherwise by asking which devices to hand
over.  A foreign or legacy ``dinput8.dll`` is never touched here - that is
a decision for the sim's tab, with the questions it asks.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from PyQt6 import QtWidgets

import telemffb.globals as G
from telemffb.tap_install import (SimStatus, TargetOutcome, TargetStatus,
                                  WrapperState, configured_devices,
                                  fix_only_config, generate_config, install,
                                  read_config, write_one_config)
from telemffb.tap_reconcile import all_status, sim_is_enabled, tap_is_enabled
from telemffb.TapStatusPanel import CANCELLED, ask_for_devices


@dataclass
class TapRepair:
    """One sim that is set to use the tap and no longer holds its files."""
    status: SimStatus
    #: Targets with no dinput8.dll at all.
    missing_wrapper: List[TargetStatus] = field(default_factory=list)
    #: Targets with no dinput8.ini - only where the wrapper is ours or
    #: absent, since a config beside somebody else's DLL is theirs to want.
    missing_config: List[TargetStatus] = field(default_factory=list)

    @property
    def sim(self):
        return self.status.sim

    def describe(self) -> str:
        parts = []
        if self.missing_wrapper:
            parts.append("wrapper")
        if self.missing_config:
            parts.append("configuration")
        return " and ".join(parts) + " missing"

    def where(self) -> List[str]:
        seen = []
        for target in self.missing_wrapper + self.missing_config:
            if target.directory not in seen:
                seen.append(target.directory)
        return seen


def pending_wrapper_repairs(settings) -> List[TapRepair]:
    """Enabled, tap-opted sims missing a wrapper or a config beside any of
    their executables - what the startup offer shows.

    A sim the user never installed into looks the same as one a game
    update emptied, and is reported the same: either way the opt-in says
    the tap should be there and it is not.
    """
    out = []
    for status in all_status(settings):
        if not status.found:
            continue
        if not sim_is_enabled(status.sim, settings):
            continue
        if not tap_is_enabled(status.sim, settings):
            continue
        repair = TapRepair(status=status)
        for target in status.targets:
            if target.state == WrapperState.ABSENT:
                repair.missing_wrapper.append(target)
            if (not target.has_config
                    and target.state in (WrapperState.ABSENT, WrapperState.TAP)):
                repair.missing_config.append(target)
        if repair.missing_wrapper or repair.missing_config:
            out.append(repair)
    return out


def config_for(repair: TapRepair, settings, parent=None):
    """The dinput8.ini to lay down where one is missing.

    CANCELLED when it cannot be decided here: the user closed the device
    dialog, or the sim is set to FFB-fix-only with a DirectInput joystick,
    which the panel refuses for the same reason (the mode leaves such a
    stick with no forces at all).  Those sims are left for their tab.
    """
    surviving = read_config(repair.status)
    if surviving is not None:
        return surviving
    devices = configured_devices(settings)
    sim = repair.sim
    if sim.fix_only_key and settings.get(sim.fix_only_key, False):
        if any(d.role == "joystick" and d.directinput for d in devices):
            return CANCELLED
        return fix_only_config(sim, devices)
    answer = ask_for_devices(sim, devices, parent=parent)
    if answer is None:
        return CANCELLED
    chosen, _retire, ordered, blocked = answer
    return generate_config(chosen, ordered, blocked)


def apply_repairs(repairs: Sequence[TapRepair], settings, parent=None
                  ) -> Tuple[List[TargetOutcome], List[str]]:
    """Put the files back for each sim; returns the per-directory outcomes
    and the names of sims left alone because no config could be decided."""
    outcomes: List[TargetOutcome] = []
    skipped: List[str] = []
    for repair in repairs:
        text: Optional[str] = None
        if repair.missing_config:
            text = config_for(repair, settings, parent)
            if text is CANCELLED:
                skipped.append(repair.sim.name)
                continue
        if repair.missing_wrapper:
            # Only the targets that lost the wrapper: copying over a copy
            # that is still there gains nothing and fails when the game
            # holds it open.
            partial = SimStatus(sim=repair.sim, root=repair.status.root,
                                provenance=repair.status.provenance,
                                targets=list(repair.missing_wrapper))
            outcomes.extend(install(partial, text))
        for target in repair.missing_config:
            if target.state == WrapperState.TAP and text is not None:
                outcomes.append(write_one_config(target.directory, text))
    return outcomes, skipped


class TapRepairDialog(QtWidgets.QDialog):
    """One checkbox per sim missing its tap files, all preselected."""

    def __init__(self, pending: Sequence[TapRepair], parent=None):
        super().__init__(parent)
        self.setWindowTitle("DirectInput Tap Missing")
        self._boxes = []

        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "These simulators are set to use the DirectInput Tap, but the "
            "tap files are no longer in their game folders, possibly caused "
            "by a game update or repair.  Until they are put back the "
            "DirectInput Tap spring mode will not function.\n\n"
            "Reinstall them now?  This may also be accomplished later via "
            "the affected simulator's system settings.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        for repair in pending:
            box = QtWidgets.QCheckBox(f"{repair.sim.name}  ({repair.describe()})")
            box.setChecked(True)
            box.setToolTip("\n".join(repair.where()))
            layout.addWidget(box)
            self._boxes.append((box, repair))

        buttons = QtWidgets.QDialogButtonBox()
        reinstall = buttons.addButton(
            "Reinstall selected", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Not now",
                          QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        reinstall.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> List[TapRepair]:
        return [repair for box, repair in self._boxes if box.isChecked()]


def offer_wrapper_repairs(parent=None, settings=None) -> bool:
    """Show the startup offer when any sim is missing its tap files; apply
    the user's selection.  Returns whether anything was put back."""
    settings = settings if settings is not None else G.system_settings
    try:
        pending = pending_wrapper_repairs(settings)
    except Exception:
        logging.exception("DirectInput tap: repair scan failed")
        return False
    if not pending:
        return False

    dialog = TapRepairDialog(pending, parent)
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return False

    outcomes, skipped = apply_repairs(dialog.selected(), settings, parent)
    repaired = False
    lines = []
    for outcome in outcomes:
        if outcome.ok:
            repaired = True
            logging.info(f"DirectInput tap: {outcome.action} in {outcome.directory}")
        else:
            lines.append(f"{outcome.directory}: {outcome.detail}")
    for name in skipped:
        lines.append(f"{name} was not set up.  Choose its devices from the "
                     f"{name} tab in System Settings.")
    if lines:
        QtWidgets.QMessageBox.warning(
            parent, "DirectInput Tap Missing",
            "Not everything could be put back:\n\n"
            + "\n".join(f"    {line}" for line in lines))
    return repaired
