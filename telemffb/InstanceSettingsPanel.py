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

"""The settings that belong to one device instance rather than to TelemFFB
as a whole, built for any instance rather than only this process's own.

Every setting here is stored under a ``{role}/`` key, so the master can show
one panel per configured device and write them all - which is what lets the
child instances stop carrying a settings page of their own.  A child exists
to drive its device and process telemetry; configuring it is the master's
job.

The widgets are built from the field lists below rather than drawn in
Designer: the same handful repeats for every device, and which devices exist
is only known at runtime.
"""

import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from PyQt6 import QtGui, QtWidgets

from telemffb.custom_widgets import InfoLabel, LabeledToggle


@dataclass(frozen=True)
class Field:
    """One instance setting: how it is stored, and how it is shown."""
    key: str
    kind: str                        # toggle | combo | int | path
    label: str
    default: object = None
    items: tuple = ()                # combo choices
    tooltip: str = ""
    path_key: Optional[str] = None   # 'path' fields pair a toggle with a file path
    depends_on: Optional[str] = None # only usable while another field is on
    inline_with: Optional[str] = None  # share a row with an earlier field
    vpforce_only: bool = False       # meaningless on a generic DirectInput device


#: The System page's instance settings.
SYSTEM_FIELDS: List[Field] = [
    Field("logLevel", "combo", "System Logging Level:", default="INFO",
          items=("INFO", "DEBUG"),
          tooltip="Logging verbosity for this instance"),
    Field("telemTimeout", "int", "Telemetry Timeout (ms):", default=200,
          tooltip="How long this instance waits for telemetry before "
                  "treating the sim as stopped"),
]

#: The Startup Behavior page's instance settings.
STARTUP_FIELDS: List[Field] = [
    Field("saveWindow", "toggle", "Restore window position", default=True),
    Field("saveLastTab", "toggle", "Restore last tab view", default=True,
          inline_with="saveWindow"),
    Field("enableVPConfStartup", "path", "Load on Startup:", default=False,
          path_key="pathVPConfStartup", vpforce_only=True,
          tooltip="VPforce Configurator profile to load when TelemFFB starts"),
    Field("enableVPConfGlobalDefault", "toggle",
          "Make Startup Profile Global Default", default=False,
          depends_on="enableVPConfStartup", inline_with="enableVPConfStartup",
          vpforce_only=True),
    Field("enableVPConfExit", "path", "Load on Exit:", default=False,
          path_key="pathVPConfExit", vpforce_only=True,
          tooltip="VPforce Configurator profile to load when TelemFFB exits"),
    Field("enableResetGainsExit", "toggle", "Restore Startup Gains on Exit",
          default=False, vpforce_only=True),
]

ALL_FIELDS: List[Field] = SYSTEM_FIELDS + STARTUP_FIELDS


class PathDisplay(QtWidgets.QLineEdit):
    """A profile path, shown by its file name.

    Configurator profiles live several directories deep, and the full path
    crowds out everything else on the row while telling the reader nothing
    they did not already know.  The name is what distinguishes one profile
    from another; the path stays a hover away, and is what gets stored.

    The field is still typeable - anything typed is taken as the path
    itself, since a bare file name is not something the app could resolve.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = ""
        self.textEdited.connect(self.setPath)

    def setPath(self, path):
        self._path = path or ""
        name = os.path.basename(self._path) or self._path
        if name != self.text():
            self.setText(name)
        self.setToolTip(self._path)

    def path(self):
        return self._path


class InstanceSettingsPanel(QtWidgets.QWidget):
    """One device's settings, for one page of the dialog.

    `browse_handler` is called with (role, field) when a path field's browse
    button is pressed; the dialog owns the file dialog and its validation.
    """

    def __init__(self, role: str, fields: List[Field], parent=None,
                 browse_handler: Optional[Callable] = None):
        super().__init__(parent)
        self.role = role
        self.fields = list(fields)
        self._browse_handler = browse_handler
        self.widgets = {}            # setting key -> widget holding its value
        self._build()

    # ------------------------------------------------------------------
    @staticmethod
    def _caption(text, tooltip=""):
        """A field's caption, held to its own width.

        Left to grow, the caption column would swallow the space the path
        fields need, pushing the short controls to the far edge of the page.
        """
        label = InfoLabel(text=text, tooltip=tooltip or None)
        label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,
                            QtWidgets.QSizePolicy.Policy.Preferred)
        return label

    def _build(self):
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        rows = {}                    # field key -> the row it was placed on
        ends = {}                    # field key -> the column just past it
        inlined = {f.inline_with for f in self.fields if f.inline_with}
        row = 0
        for f in self.fields:
            if f.inline_with:
                # beside the setting it qualifies rather than under it, and
                # immediately beside it - not aligned to some other row's
                # second column, which reads as a column of its own
                r, c = rows[f.inline_with], ends[f.inline_with]
            else:
                r, c = row, 0
                row += 1
            rows[f.key] = r

            if f.kind == "toggle":
                w = LabeledToggle(label=f.label)
                # A toggle with nothing beside it spans the form's columns so
                # a long caption does not widen the first one; one that shares
                # its row keeps to a single column, so its neighbor can sit
                # right next to it rather than out at the far side.
                span = 1 if f.key in inlined else 3
                grid.addWidget(w, r, c, 1, span)
                ends[f.key] = c + span
                self.widgets[f.key] = w

            elif f.kind == "combo":
                grid.addWidget(self._caption(f.label, f.tooltip), r, c)
                w = QtWidgets.QComboBox()
                w.addItems(list(f.items))
                # a short list of choices; left at natural width rather than
                # stretched across the page like the path fields
                w.setMaximumWidth(160)
                grid.addWidget(w, r, c + 1)
                ends[f.key] = c + 2
                self.widgets[f.key] = w

            elif f.kind == "int":
                grid.addWidget(self._caption(f.label, f.tooltip), r, c)
                w = QtWidgets.QLineEdit()
                w.setValidator(QtGui.QIntValidator(0, 999999))
                w.setMaximumWidth(80)
                grid.addWidget(w, r, c + 1)
                ends[f.key] = c + 2
                self.widgets[f.key] = w

            elif f.kind == "path":
                toggle = LabeledToggle(label=f.label)
                path = PathDisplay()
                path.setMinimumWidth(220)
                browse = QtWidgets.QToolButton()
                browse.setText("...")
                grid.addWidget(toggle, r, c)
                grid.addWidget(path, r, c + 1)
                grid.addWidget(browse, r, c + 2)
                ends[f.key] = c + 3
                self.widgets[f.key] = toggle
                self.widgets[f.path_key] = path
                self.widgets[f.path_key + "__browse"] = browse
                if self._browse_handler is not None:
                    browse.clicked.connect(
                        lambda _, ff=f: self._browse_handler(self.role, ff))
                toggle.stateChanged.connect(self._apply_dependencies)

            else:
                raise ValueError(f"unknown field kind {f.kind!r}")

            if f.tooltip:
                self.widgets[f.key].setToolTip(f.tooltip)
            # objectNames carry the role, so widgets stay identifiable with
            # several panels alive at once
            self.widgets[f.key].setObjectName(f"{self.role}__{f.key}")

        # The slack lives in a column of its own: with it in a content
        # column, the widest caption would take it and push the short
        # controls to the far edge of the page.
        grid.setColumnStretch(max(ends.values(), default=1), 1)
        grid.setRowStretch(row, 1)

    # ------------------------------------------------------------------
    def _apply_dependencies(self):
        """Grey out whatever a switched-off parent setting makes meaningless."""
        for f in self.fields:
            if f.vpforce_only and self.vpforce_blocked:
                continue          # held disabled by set_vpforce_features_enabled
            if f.kind == "path":
                on = self.widgets[f.key].isChecked()
                self.widgets[f.path_key].setEnabled(on)
                self.widgets[f.path_key + "__browse"].setEnabled(on)
            if f.depends_on and f.depends_on in self.widgets:
                on = self.widgets[f.depends_on].isChecked()
                self.widgets[f.key].setEnabled(on)
                if not on and f.kind == "toggle":
                    self.widgets[f.key].setChecked(False)

    # ------------------------------------------------------------------
    def vpforce_fields(self):
        """Every widget belonging to a VPforce-only setting."""
        out = []
        for f in self.fields:
            if not f.vpforce_only:
                continue
            out.append(self.widgets[f.key])
            if f.path_key:
                out.append(self.widgets[f.path_key])
                out.append(self.widgets[f.path_key + "__browse"])
        return out

    def set_vpforce_features_enabled(self, enabled, reason=""):
        """Grey out the Configurator settings, without clearing them.

        A device can be swapped back to VPforce hardware, so the stored
        values have to survive the trip - only the controls are disabled,
        and the runtime pushes are gated separately.
        """
        self._vpforce_blocked = not enabled
        for widget in self.vpforce_fields():
            if not hasattr(widget, '_vpforce_tooltip'):
                widget._vpforce_tooltip = widget.toolTip()
            widget.setEnabled(enabled)
            widget.setToolTip(widget._vpforce_tooltip if enabled else reason)
        if enabled:
            # dependent enable-states follow their own toggles again
            self._apply_dependencies()

    @property
    def vpforce_blocked(self):
        return getattr(self, '_vpforce_blocked', False)

    # ------------------------------------------------------------------
    def load(self, settings, defaults_only=False):
        """Populate from this instance's stored settings."""
        for f in self.fields:
            value = (f.default if defaults_only
                     else settings.get(f.key, f.default, instance=self.role))
            w = self.widgets[f.key]
            if f.kind in ("toggle", "path"):
                w.setChecked(bool(value))
            elif f.kind == "combo":
                w.setCurrentText(str(value))
            elif f.kind == "int":
                w.setText(str(value))
            if f.kind == "path":
                path = ("" if defaults_only
                        else settings.get(f.path_key, "", instance=self.role))
                self.widgets[f.path_key].setPath(str(path or ""))
        self._apply_dependencies()

    def values(self) -> dict:
        """The panel's contents, as stored setting keys and values."""
        out = {}
        for f in self.fields:
            w = self.widgets[f.key]
            if f.kind in ("toggle", "path"):
                out[f.key] = w.isChecked()
            elif f.kind == "combo":
                out[f.key] = w.currentText()
            elif f.kind == "int":
                out[f.key] = str(w.text())
            if f.kind == "path":
                out[f.path_key] = self.widgets[f.path_key].path()
        return out

    def save(self, settings) -> dict:
        """Write the panel's contents under this instance's keys."""
        values = self.values()
        for key, value in values.items():
            settings.setValue(f"{self.role}/{key}", value)
        return values
