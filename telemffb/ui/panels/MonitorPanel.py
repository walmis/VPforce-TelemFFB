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

"""MonitorPanel: the "Monitor" tab's telemetry and active-effects display.

Replaces MainWindow's two hand-rolled ``QLabel``s (``lbl_telem_data``,
``lbl_effects_data``) sitting inside ``QScrollArea``s, each refreshed every
frame by concatenating one formatted line of text per row and calling
``setText()`` on the whole block. That scales badly with a QAbstractItemView
in mind - a table view backed by ``MonitorTableModel.KeyValueTableModel``
redraws only the rows that changed instead of re-laying-out one giant label
- and it is also how selecting a handful of telemetry lines and copying
them (``CopyableTableView``, ``telemffb.ui.widgets.custom_widgets``) keeps
working without free-text selection.

Two visual differences from the old label-based rendering, both judged the
closest faithful option for a table view:

- The "Waiting for data... / DCS: Enabled / ..." pre-telemetry message was
  free text, not key/value rows. It is kept as a plain ``QLabel`` shown in
  a ``QStackedWidget`` ahead of the telemetry table, swapped in by
  ``refresh_waiting_status()`` and swapped out the moment the first
  telemetry frame renders through ``update_telemetry()`` - exactly when the
  old label's text was first overwritten with real data.
- Copy is now cell/row based (``CopyableTableView``'s Ctrl+C) rather than
  drag-selecting arbitrary runs of free text out of a QLabel.

The telemetry table's leading column is a star gutter: click a row's star
to favourite that telemetry key, tick "Favorites" beside the filter to
list only those. The set is kept here and saved to one *global* registry
value - not an instance-scoped one - so it is the same list whichever
device's instance you star a key from; what the interesting telemetry is
is a property of the sim, not of the device watching it. The stars
themselves are painted by ``FavoriteStarDelegate``, which is handed this
panel's own set to read.

Ownership split follows ``OfflineEditorPanel``/``SettingsLayout``: this
panel owns the two tables, the filter box and the detach toolbar button;
what a detach *does* (reparenting the tab into its own window) stays
MainWindow's business, since it also owns the tab widget and the other
detachable tabs - the panel calls back through ``mainwindow.detach_tab(0)``,
matching the previous hardcoded "Monitor is always tab 0" assumption.

Building the row list itself (alphabetizing telemetry keys, moving certain
keys to the front, walking ``G.effects.dict`` to translate active-effect
names) stays out of this file where it was already entangled with logic
this refactor step is not touching (the per-frame ``NoWheelSlider`` handle
colors, which key off the very same ``active_settings`` list the effects
loop builds). MainWindow still does that part and calls
``update_telemetry(data)`` / ``update_effects(effects)`` with the result.
"""

from typing import Dict, List, Optional, Set, Tuple

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QSplitter, QStackedWidget,
                             QWidget)

import telemffb.globals as G
from telemffb.SettingsManager import SettingsManager
from telemffb.hw.ffb_rhino import (EFFECT_CONSTANT, EFFECT_CUSTOM,
                                   EFFECT_DAMPER, EFFECT_DETENT,
                                   EFFECT_FRICTION, EFFECT_INERTIA,
                                   EFFECT_RAMP, EFFECT_SAWTOOTHDOWN,
                                   EFFECT_SAWTOOTHUP, EFFECT_SINE,
                                   EFFECT_SPRING, EFFECT_SPRING_ADJUSTER,
                                   EFFECT_SQUARE, EFFECT_TRIANGLE,
                                   PERIODIC_EFFECTS, effect_names)
from telemffb.ui.panels.DevicePanel import DEVICE_ICONS, tint_pixmap
from telemffb.ui.panels.MonitorTableModel import KeyValueTableModel
from telemffb.ui.widgets.EffectTypeDelegate import EffectTypeDelegate
from telemffb.ui.widgets.FavoriteStarDelegate import (STAR_COLUMN_WIDTH,
                                                      FavoriteStarDelegate)
from telemffb.ui.widgets.IntensityBarDelegate import IntensityBarDelegate
from telemffb.ui.widgets.TabHeaderBar import TabHeaderBar
from telemffb.ui.widgets.custom_widgets import CopyableTableView

_MONOSPACE_STYLE = """
    padding: 2px;
    font-family: Cascadia Mono;
"""

#: Wide enough for "100%" over a bar that still reads as a bar, narrow
#: enough to leave the effect names the rest of a half-split pane.
INTENSITY_COLUMN_WIDTH = 72

#: The telemetry table's columns. The favourite star sits in a gutter ahead
#: of the key, the way a starred row reads in a mail client - and the model
#: is told it holds no selectable value (see KeyValueTableModel).
_STAR_COL, _KEY_COL, _VALUE_COL = 0, 1, 2

#: Where the favourite keys live: one registry value, deliberately global
#: rather than instance-scoped (SystemSettings.setValue without an
#: `instance`), so starring a key on the joystick instance stars it for the
#: pedals and collective too - the interesting telemetry is a property of
#: the sim, not of the device watching it.
_FAVORITES_KEY = 'monitorFavoriteKeys'
_FAVORITES_ONLY_KEY = 'monitorFavoritesOnly'

#: The scoped device's icon in the page header: the height of the text it
#: sits in, against the 72px the device panel draws it at.
_SCOPE_ICON_PX = 16

#: The telemetry pane's share of the monitor splitter, in percent - the rest
#: goes to the active-effects pane.
_TELEM_SHARE_PCT = 55

#: The order the active-effects list is grouped into, by PID effect type.
#: Periodics first - magnitude and shape both mean something for them -
#: then the forces that have a magnitude but no shape, then the conditions
#: with the spring-shaped ones ahead of the rest. It puts every effect that
#: reports a dash together at the foot of the list, so the intensity column
#: reads as one scale at a time instead of alternating down the page.
_EFFECT_GROUPS = {
    EFFECT_SQUARE: 0, EFFECT_SINE: 0, EFFECT_TRIANGLE: 0,
    EFFECT_SAWTOOTHUP: 0, EFFECT_SAWTOOTHDOWN: 0,
    EFFECT_CONSTANT: 1, EFFECT_RAMP: 1,
    EFFECT_SPRING: 2, EFFECT_SPRING_ADJUSTER: 2, EFFECT_DETENT: 2,
    EFFECT_DAMPER: 3, EFFECT_INERTIA: 3, EFFECT_FRICTION: 3,
    EFFECT_CUSTOM: 3,
}
#: Anything unrecognised sorts last rather than silently joining a group.
_UNGROUPED = max(_EFFECT_GROUPS.values()) + 1


def _name_tooltip(label, effect_type):
    """What the name cell says on hover: the effect, and what kind it is.

    The badge beside the name is a waveform or a single letter, which is a
    reminder rather than a code to memorise - this is where it is spelled
    out. A waveform is named as the periodic it is ("Periodic Sine"), since
    the shape alone says nothing about which family it belongs to. None
    when the type is unknown, so the cell falls back to hovering its own
    text as every other column does.
    """
    name = effect_names.get(effect_type)
    if not name:
        return None
    kind = f"Periodic {name}" if effect_type in PERIODIC_EFFECTS else name
    return f"{label}\n{kind} effect"


def _axes_text(gains):
    """A condition's cell text: ``"X 50% Y 30%"``, a dash for an axis that
    was never written. Labelled rather than positional so a copied row still
    says which is which, and it is what IntensityBarDelegate splits on."""
    x, y = (list(gains) + [None, None])[:2]
    pct = lambda g: '-' if g is None else f"{round(g * 100)}%"
    return f"X {pct(x)} Y {pct(y)}"


def _gains_tooltip(gains):
    """A condition's hover: its gain per axis, and why that is the number
    shown rather than an intensity."""
    x, y = (list(gains) + [None, None])[:2]
    lines = [f"{axis} axis gain {round(g * 100)}%"
             for axis, g in (("X", x), ("Y", y)) if g is not None]
    lines.append("The gain it is set to - the force it produces depends on "
                 "where the stick is.")
    return "\n".join(lines)


def _intensity_tooltip(intensity, configured, factor):
    """What the intensity cell says on hover.

    The column shows device full scale, which is the honest and comparable
    figure but reads surprisingly low, for two compounding reasons: most
    settings carry a slider factor, so a slider at 30% stores 0.12 rather
    than 0.3, and an effect may then split that across axes - the heli
    rotor rumble gives each of X and Y half. 0.06 on the device is a
    correct reading of a setting the user set to 30%.

    So the second line divides by the setting's VALUE, not by its slider
    factor: the factor describes how the slider maps to the value and says
    nothing about what was actually set. The factor is used only to put the
    setting back in the slider's own terms, which is how the user last saw
    it.
    """
    if intensity is None:
        return ("No meaningful intensity: this is a condition effect, and "
                "the force it produces depends on stick position or "
                "velocity rather than on a magnitude.")
    text = f"{round(intensity * 100)}% of device full scale"
    if isinstance(configured, (int, float)) and configured > 0:
        line = f"{round(intensity / configured * 100)}% of the configured setting"
        if factor:
            line += f" ({round(configured / factor * 100)}% on the slider)"
        text += "\n" + line
    return text


#: Telemetry keys that can swing negative, keyed on the ORIGINAL telemetry
#: key rather than the debug simvar display name - the display name is
#: MSFS-only and only exists when Alt+D is toggled, so deciding sign off it
#: would make the same field flip formatting depending on a debug setting.
#: Casing matches BaseTelemetryData's own attribute names.
_SIGNED_EXACT_KEYS = frozenset({
    'AoA', 'SideSlip', 'Pitch', 'Roll', 'G', 'Gaxil', 'VerticalSpeed',
    'Incidence', 'X', 'Y', 'MSL', 'AGL', 'TRIM_DELTA',
    # Control-input positions, named individually because "...Pos" is not
    # a signed suffix: the control axes run -1..1 about a neutral, but
    # CollectivePos runs 0..1 ("unlike the other control axes" - see
    # BaseTelemetryData), as do GearPos, NozzlePos and SpeedbrakePos.
    'ElevPos', 'AileronPos', 'RudderPos', 'TailRotorPos',
    'TailRotorPedalPos', 'YokeXLinearPos', 'YokeYPos',
    # Steering angle off centre, signed left/right, and named here
    # because its "Pct" spelling matches nothing else signed.
    'CenterSteerAnglePct',
})

#: Case-insensitive substrings that mark a key as signed by convention:
#: trims, deflections, positions, accelerations, velocities and the like
#: are offsets from a zero point rather than magnitudes, so they cross
#: zero routinely (this is what makes ACCs/VelWorld/StickXY jitter worst).
_SIGNED_KEY_PATTERNS = (
    'trim', 'defl', 'acc', 'vel', 'wind', 'force', 'stick', 'joy',
    'phys_', 'sema', 'cp_xy', 'vib', 'target_', 'rot_',
)


def _key_is_statically_signed(key: str) -> bool:
    """Layers 1-2 of the signed-key decision: an exact key known to go
    negative, or a name matching one of the signed-by-convention
    substrings (checked case-insensitively; the exact set above is not,
    since it is quoting BaseTelemetryData's own attribute spelling)."""
    if key in _SIGNED_EXACT_KEYS:
        return True
    key_cf = key.lower()
    return any(pattern in key_cf for pattern in _SIGNED_KEY_PATTERNS)


def _value_is_negative(v) -> bool:
    """True when `v` itself, or any float element of it, is negative -
    layer 3's trigger for sticky-learning a key that layers 1-2 miss."""
    if isinstance(v, float):
        return v < 0
    if isinstance(v, list):
        return any(isinstance(x, float) and x < 0 for x in v)
    return False


def _format_telemetry_value(v, signed: bool) -> str:
    """Renders one telemetry cell. Unchanged from the pre-sign-flag
    rendering except a float - scalar, or a list's float elements - gets
    an explicit '+' when `signed` and non-negative, so its digits don't
    shift horizontally as the value crosses zero. Non-float list elements
    and ints are untouched either way."""
    if isinstance(v, float):
        return f"{v:+.3f}" if signed else f"{v:.3f}"
    if isinstance(v, list):
        return "[" + ", ".join(
            (f"{x:+.3f}" if signed else f"{x:.3f}") if isinstance(x, float)
            else str(x) if x is not None else "None"
            for x in v
        ) + "]"
    return str(v)


class MonitorPanel(QWidget):
    """The Monitor tab: telemetry table + active-effects table, the filter
    box and the detach-to-window toolbar button."""

    def __init__(self, parent=None, mainwindow=None):
        super().__init__(parent)
        self.mainwindow = mainwindow
        self.show_simvars = False
        # Sign-formatting state for `_build_telemetry_rows` (see
        # `_is_signed_key`): keys known to render signed - either always
        # (layers 1-2) or learned sticky after going negative once (layer
        # 3) - and the (src, N) pair last seen, so a new sim/aircraft
        # starts clean instead of inheriting another aircraft's negatives.
        self._signed_keys: Set[str] = set()
        self._signed_session: Tuple[Optional[str], Optional[str]] = (None, None)
        # Starred telemetry keys, by their ORIGINAL key rather than the
        # MSFS simvar display name - the display name only exists while the
        # Alt+D debug rename is on, so a favourite made with it on has to
        # survive it going off again.
        self._favorites: Set[str] = self._load_favorites()
        # The last frame rendered, so toggling a star or the Favorites box
        # redraws the list now rather than at whatever point the next frame
        # arrives (which, with the sim closed, is never).
        self._last_data: Optional[Dict] = None
        self._build_ui()
        self.refresh_waiting_status()

    # ---- construction ----------------------------------------------------

    def _build_ui(self):
        layout = QGridLayout(self)

        """ Detach toolbar - moves the whole Monitor tab into its own
        window. Always tab index 0: MainWindow's tab-context-menu and
        Ctrl+Shift+M shortcut make the same assumption. """
        self.detach_toolbar = QtWidgets.QToolBar(self)
        self.detach_toolbar.setObjectName("monitorInlineToolbar")
        self.detach_toolbar.setMovable(False)
        self.detach_toolbar.setFloatable(False)
        self.detach_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.detach_toolbar.setIconSize(QtCore.QSize(16, 16))
        self.detach_toolbar.setStyleSheet("QToolBar { border: 0; background: transparent; }")

        self.detach_action = self.detach_toolbar.addAction("Detach")
        self.detach_action.setToolTip('Detach the Monitor Tab from the main window\ninto a separate window.')
        self.detach_action.triggered.connect(self._on_detach_clicked)

        btn = self.detach_toolbar.widgetForAction(self.detach_action)
        if isinstance(btn, QtWidgets.QToolButton):
            btn.setAutoRaise(False)
            btn.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet("""
                QToolButton {
                    border: 1px solid palette(mid);
                    border-radius: 4px;
                    padding: 3px 9px;
                    background: palette(button);
                    color: palette(button-text);
                }
                QToolButton:hover { background: palette(midlight); }
                QToolButton:pressed {
                    background: palette(dark);
                    color: palette(highlight);
                }
                QToolButton:disabled { color: palette(mid); border-color: palette(mid); }
            """)

        """ Page header - this page's own controls, and a compact device
        row for the "tab header" device view and for when the page is
        detached into a window of its own. See
        telemffb/ui/widgets/TabHeaderBar.py. """
        self.header_bar = TabHeaderBar()

        self.telem_lbl = QLabel('Telemetry:')
        self.telem_filter = QLineEdit()
        self.telem_filter.setToolTip(
            "Comma Separated, Case Insensitive list of telemetry items to show (e.g. 'aoa, ias, rpm')")
        self.telem_filter.setPlaceholderText("Filter")
        self.telem_filter.setMaximumWidth(100)
        self.telem_filter.textChanged.connect(self._refresh_rows)

        self.favorites_check = QCheckBox("Favorites")
        self.favorites_check.setToolTip(
            "Show only the telemetry items starred in the left-hand column.\n"
            "Favorites are shared by all devices and kept between sessions.")
        # Restored, but only when there is something starred to show for it:
        # an empty Monitor tab is a poor way to be reminded the box was left
        # ticked on a machine whose favourites have since been cleared.
        self.favorites_check.setChecked(
            bool(self._favorites) and bool(G.system_settings.get(_FAVORITES_ONLY_KEY, 0)))
        self.favorites_check.toggled.connect(self._on_favorites_only_toggled)

        # Whose data the page is showing - both tables, so it is said once up
        # here rather than in a header of each. At the far end of the bar
        # from the page's controls: it is something to read, not to use.
        # Hidden until there is a choice of device to speak of
        # (set_scope_device).
        self._scope_device = None
        self._scope_indicator = QWidget()
        scope_row = QHBoxLayout(self._scope_indicator)
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.setSpacing(4)
        self._scope_icon = QLabel()
        self._scope_name = QLabel()
        for widget in (QLabel('Device:'), self._scope_icon, self._scope_name):
            scope_row.addWidget(widget)
        self._scope_indicator.hide()

        self.header_bar.add_left(self.detach_toolbar)
        self.header_bar.add_left(self.telem_lbl)
        self.header_bar.add_left(self.telem_filter)
        self.header_bar.add_left(self.favorites_check)
        self.header_bar.add_right(self._scope_indicator)


        """ Telemetry pane: a plain "waiting for data" label shown until the
        first telemetry frame, then the live table. """
        self._telem_model = KeyValueTableModel(
            ['', 'Key', 'Value'], self, unselectable_columns=[_STAR_COL])
        self.telem_view = CopyableTableView(self)
        self.telem_view.setModel(self._telem_model)
        telem_header = self.telem_view.horizontalHeader()
        telem_header.setStretchLastSection(True)
        telem_header.setSectionResizeMode(_STAR_COL, QHeaderView.ResizeMode.Fixed)
        self.telem_view.setColumnWidth(_STAR_COL, STAR_COLUMN_WIDTH)
        self._star_delegate = FavoriteStarDelegate(self)
        self._star_delegate.set_favorites(self._favorites)
        self.telem_view.setItemDelegateForColumn(_STAR_COL, self._star_delegate)
        # So an unstarred row's star lights up under the cursor - the column
        # carries no text to make it look clickable on its own.
        self.telem_view.setMouseTracking(True)
        self.telem_view.clicked.connect(self._on_telem_clicked)
        self.telem_view.setStyleSheet(f"QTableView {{ {_MONOSPACE_STYLE} }}")

        self._telem_waiting_label = QLabel()
        self._telem_waiting_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._telem_waiting_label.setWordWrap(False)
        self._telem_waiting_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._telem_waiting_label.setStyleSheet(_MONOSPACE_STYLE)
        # A QStackedWidget is as wide as its widest page, current or not, so
        # this placeholder's longest line was flooring the telemetry pane at
        # ~570px for the whole session - enough to claim the effects pane's
        # share back below a ~1030px splitter. It needs no floor of its own:
        # while it is up the effects pane is hidden and it has the splitter
        # to itself (_hide_effects_pane), handle and all.
        self._telem_waiting_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            self._telem_waiting_label.sizePolicy().verticalPolicy())

        self._telem_stack = QStackedWidget()
        self._telem_stack.addWidget(self._telem_waiting_label)
        self._telem_stack.addWidget(self.telem_view)
        self._telem_stack.setMinimumHeight(100)

        """ Active-effects pane. """
        self._effects_model = KeyValueTableModel(['Active Effects', 'Intensity'], self)
        self.effects_view = CopyableTableView(self)
        self.effects_view.setModel(self._effects_model)
        header = self.effects_view.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.effects_view.setColumnWidth(1, INTENSITY_COLUMN_WIDTH)
        self.effects_view.setItemDelegateForColumn(1, IntensityBarDelegate(self))
        self._type_delegate = EffectTypeDelegate(self)
        self.effects_view.setItemDelegateForColumn(0, self._type_delegate)
        self.effects_view.setStyleSheet(f"QTableView {{ {_MONOSPACE_STYLE} }}")
        self.effects_view.setMinimumHeight(100)

        layout.addWidget(self.header_bar, 0, 0, 1, 2)
        # Two tables have the same size hint and would start level, so the
        # split is stated (_TELEM_SHARE_PCT). The stretch factors hold it as
        # the window is resized; the starting split is set on first show
        # (showEvent), once the splitter has a width to divide.
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._telem_stack)
        self._splitter.addWidget(self.effects_view)
        self._splitter.setStretchFactor(0, _TELEM_SHARE_PCT)
        self._splitter.setStretchFactor(1, 100 - _TELEM_SHARE_PCT)
        self._split_applied = False
        self._restore_sizes = None
        layout.addWidget(self._splitter, 1, 0, 1, 2)  # Span both columns

    def showEvent(self, event):
        super().showEvent(event)
        if not self._split_applied:
            self._split_applied = True
            self._apply_default_split()

    # ---- pane sizing --------------------------------------------------------

    def _apply_default_split(self) -> None:
        """Put the two panes back at their default share of the splitter."""
        width = self._splitter.width()
        if width > 0:
            self._splitter.setSizes([width * _TELEM_SHARE_PCT // 100,
                                     width * (100 - _TELEM_SHARE_PCT) // 100])

    def _hide_effects_pane(self) -> None:
        """Nothing is flying, so nothing has effects: hand the whole splitter
        to the telemetry pane rather than squeezing an empty table against
        it. This is also what keeps the default split honest - the waiting
        placeholder's widest line floors the telemetry pane at ~570px, which
        at 55% would otherwise claim the effects pane's share back on any
        window narrower than about 1030px."""
        if self.effects_view.isHidden():
            return
        sizes = self._splitter.sizes()
        # Only worth remembering once a real layout has set them; before the
        # first show they are whatever the splitter guessed.
        if self._split_applied and all(sizes):
            self._restore_sizes = sizes
        self.effects_view.hide()

    def _show_effects_pane(self) -> None:
        """Bring the effects pane back at whatever share it last held, so a
        drag of the splitter survives a sim restart, or at the default."""
        if not self.effects_view.isHidden():
            return
        self.effects_view.show()
        if self._restore_sizes:
            self._splitter.setSizes(self._restore_sizes)
        else:
            self._apply_default_split()

    def _on_detach_clicked(self):
        if self.mainwindow is not None:
            self.mainwindow.detach_tab(0)

    # ---- detach toolbar visibility (MainWindow.detach_tab/reattach_tab) --

    def set_detach_toolbar_visible(self, visible: bool) -> None:
        self.detach_toolbar.setVisible(visible)

    # ---- whose data this is (master scope switch) --------------------------

    def set_scope_device(self, device_type: Optional[str], sending: bool = True) -> None:
        """Name the device whose telemetry and effects the page is showing,
        in the header bar. ``None`` hides it: an instance with one device has
        no other it could be.

        A child that is not ``sending`` its telemetry has left that table on
        this instance's own frame, and the indicator says so."""
        if not device_type:
            self._scope_device = None
            self.refresh_scope_indicator()
            return
        if device_type != self._scope_device:
            self._scope_device = device_type
            ratio = self.devicePixelRatioF()
            icon = QPixmap(DEVICE_ICONS[device_type]).scaledToHeight(
                round(_SCOPE_ICON_PX * ratio), Qt.TransformationMode.SmoothTransformation)
            icon.setDevicePixelRatio(ratio)
            # Grey, of the theme's own making: the text colour, held back.
            grey = self._scope_name.palette().windowText().color()
            grey.setAlphaF(0.55)
            self._scope_icon.setPixmap(tint_pixmap(icon, grey))
        name = device_type.title()
        self._scope_name.setText(name if sending else f"{name} (no data)")
        self._scope_name.setToolTip(
            '' if sending else f"{name} is not sending its telemetry - this instance's is shown")
        # Shown once it has its contents, so the header bar lays it out once at
        # its real width rather than empty first and again a pass later.
        self.refresh_scope_indicator()

    def refresh_scope_indicator(self) -> None:
        """Show the indicator if there is a device to name and nothing beside
        it already naming it: a device strip in this bar - the "tab header"
        device view, or a detached Monitor's stand-in - has the device in
        scope highlighted, an inch to the right."""
        self._scope_indicator.setVisible(
            self._scope_device is not None and not self.header_bar.shows_device_strip())

    def scope_text(self) -> str:
        """What the indicator says, or '' while it is hidden."""
        return '' if self._scope_indicator.isHidden() else self._scope_name.text()

    # ---- debug menu: "Show simvar in telem window" ------------------------

    def set_show_simvars(self, value: bool) -> None:
        self.show_simvars = bool(value)

    # ---- pre-telemetry status ----------------------------------------------

    def refresh_waiting_status(self) -> None:
        sims = list(SettingsManager.SIM_LABELS)
        width = max(len(SettingsManager.sim_label(sim)) for sim in sims)
        lines = [f"{SettingsManager.sim_label(sim):<{width}} : "
                 f"{'Enabled' if SettingsManager.sim_enabled(sim) else 'Disabled'}"
                 for sim in sims]
        self._telem_waiting_label.setText(
            "Waiting for data...\n\n"
            + "\n".join(lines)
            + "\n\nEnable or Disable in System -> System Settings"
        )
        self._telem_stack.setCurrentWidget(self._telem_waiting_label)
        self._hide_effects_pane()

    # ---- per-frame updates --------------------------------------------------

    def update_telemetry(self, data: Dict) -> None:
        """``data`` is the (already alphabetized/reordered) telemetry dict
        for this frame. Builds one table row per key, applying the same
        filter and MSFS-simvar-name debug substitution
        ``get_telem_items`` used to."""
        self._last_data = data
        self._telem_model.set_rows(self._build_telemetry_rows(data))
        self._telem_stack.setCurrentWidget(self.telem_view)
        self._show_effects_pane()

    def _refresh_rows(self) -> None:
        """Re-render the last frame under whatever the filter, the Favorites
        box and the favourites set now say. Does not touch the stacked
        widget: while the "waiting for data" page is up there is no frame to
        re-render anyway."""
        if self._last_data is not None:
            self._telem_model.set_rows(self._build_telemetry_rows(self._last_data))

    def _build_telemetry_rows(self, data: Dict) -> List[Tuple[str, Tuple[str, str, str]]]:
        raw = (self.telem_filter.text() or "")
        tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
        favorites_only = self.favorites_check.isChecked()

        # A new sim/aircraft (src or the aircraft name "N" changed since
        # the last frame) starts the sticky-learned signed keys clean -
        # otherwise a key one aircraft happened to push negative would
        # render signed forever after for every aircraft that follows.
        session = (data.get("src"), data.get("N"))
        if session != self._signed_session:
            self._signed_session = session
            self._signed_keys.clear()

        rows: List[Tuple[str, Tuple[str, str, str]]] = []
        for key, v in data.items():
            # Favourites are keyed on the raw telemetry key, so this is
            # settled before the debug rename and costs one set lookup.
            if favorites_only and key not in self._favorites:
                continue

            display_key = key
            # check for msfs and debug mode (alt-d pressed), change to simvar name
            if self.show_simvars:
                if data.get("src") == "MSFS":
                    s = G.telem_manager.simconnect.get_var_name(display_key)
                    if s is not None:
                        display_key = s

            # Apply simple OR filtering against the (possibly renamed) key only
            if tokens:
                k_cf = str(display_key).lower()
                if not any(tok in k_cf for tok in tokens):
                    continue

            value_str = _format_telemetry_value(v, self._is_signed_key(key, v))

            # Keyed by the ORIGINAL telemetry key (always unique in `data`)
            # rather than the display key, so two keys that happen to
            # resolve to the same simvar name under the debug rename don't
            # collide into a single row. The star column holds no text of
            # its own - FavoriteStarDelegate paints it from the row key.
            rows.append((str(key), ('', str(display_key), value_str)))
        return rows

    # ---- favourites ---------------------------------------------------------

    @staticmethod
    def _load_favorites() -> Set[str]:
        """The starred keys from the registry, as written by
        ``_save_favorites``. Telemetry keys carry no commas, so one comma-
        separated value keeps this to a single registry entry."""
        raw = G.system_settings.get(_FAVORITES_KEY, '')
        return {key.strip() for key in str(raw or '').split(',') if key.strip()}

    def _save_favorites(self) -> None:
        G.system_settings.setValue(_FAVORITES_KEY,
                                   ','.join(sorted(self._favorites)))

    def _on_telem_clicked(self, index) -> None:
        """A click in the star gutter toggles that row's favourite. Handled
        from the view rather than inside the delegate so the favourites set
        is only ever mutated by the object that owns and persists it."""
        if index.column() != _STAR_COL:
            return
        key = self._telem_model.row_key(index.row())
        # Re-read before toggling: every instance shares this one registry
        # value but each holds only the copy it read at startup, so writing
        # a stale set back would silently drop whatever another instance
        # has starred since - and this instance would go on not showing it
        # until restarted. Mutated in place, never rebound:
        # FavoriteStarDelegate paints from this very set.
        self._favorites.clear()
        self._favorites.update(self._load_favorites())
        if key in self._favorites:
            self._favorites.discard(key)
        else:
            self._favorites.add(key)
        self._save_favorites()
        # The row's values have not changed, so set_rows would see nothing
        # to emit and the star would keep its old colour until some other
        # value moved - repaint the column outright.
        self.telem_view.viewport().update()
        self._refresh_rows()

    def _on_favorites_only_toggled(self, checked: bool) -> None:
        G.system_settings.setValue(_FAVORITES_ONLY_KEY, int(checked))
        self._refresh_rows()

    def _is_signed_key(self, key: str, v) -> bool:
        """Whether `key` renders with an explicit sign this frame.

        `self._signed_keys` memoizes the decision per (session, key), so a
        key already known signed - by the static layers or learned below -
        costs one set lookup. A key not yet in it still has to be checked
        against this frame's value: layer 3 (sticky learning) has to catch
        a key going negative on the very frame it first does so, or that
        frame renders unsigned and only the next one picks up the sign.
        """
        if key in self._signed_keys:
            return True
        if _key_is_statically_signed(key) or _value_is_negative(v):
            self._signed_keys.add(key)
            return True
        return False

    def update_effects(self, active_effects) -> None:
        """``active_effects`` is the list of ``{'label', 'intensity'}`` dicts
        MainWindow builds (and sends over IPC as JSON when this is a child
        instance) - one per started effect. The label already embeds the
        effect's id, so it is a unique-enough row key on its own.

        ``intensity`` is a fraction of device full scale, or None for an
        effect that has no meaningful one - a condition, whose force depends
        on where the stick is. Those get a dash rather than a bar: an effect
        that is merely started and one that is pushing nothing must not look
        the same, but nor should a guess look like a measurement."""
        # Grouped by effect type, insertion order kept inside each group.
        # KeyValueTableModel._diff_update requires the ordering to be a
        # stable function of the key set - a surviving row must never change
        # position relative to the other survivors - and this is, because an
        # effect's type is fixed for its lifetime and the sort is stable.
        # Sorting on anything that moves (intensity, say) would silently
        # produce a wrongly ordered list rather than an error.
        ordered = sorted(active_effects,
                         key=lambda e: _EFFECT_GROUPS.get(e.get('type'),
                                                          _UNGROUPED))
        rows, tooltips, types = [], {}, {}
        for effect in ordered:
            label = effect.get('label', '')
            intensity = effect.get('intensity')
            effect_type = effect.get('type')
            gains = effect.get('gains')
            if intensity is not None:
                shown = f"{round(intensity * 100)}%"
                value_tip = _intensity_tooltip(intensity,
                                               effect.get('configured'),
                                               effect.get('factor'))
            elif gains:
                shown = _axes_text(gains)
                value_tip = _gains_tooltip(gains)
            else:
                shown = '-'
                value_tip = _intensity_tooltip(None, None, None)
            rows.append((label, (label, shown)))
            tooltips[label] = (_name_tooltip(label, effect_type), value_tip)
            types[label] = effect_type
        self._type_delegate.set_types(types)
        self._effects_model.set_rows(rows, tooltips)

    def clear_effects(self) -> None:
        self._effects_model.clear()

    # ---- external reach-in: TeleplotSetupDialog's key picker ---------------

    def telemetry_keys(self) -> List[str]:
        """The Key column's current values, in row order - what
        ``TeleplotSetupDialog.KeySelectionDialog`` used to get by parsing
        ``lbl_telem_data.text()`` (``line.split(':')[0]``) before this was a
        table. Mirrors the old "Sim not running" placeholder for the
        pre-telemetry state."""
        if self._telem_stack.currentWidget() is self._telem_waiting_label:
            return ['Sim not running']
        return self._telem_model.column_values(_KEY_COL)
