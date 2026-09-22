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

"""The Devices / Launch Options area of System Settings, as role cards.

The old layout was one grid where a row meant three different things at
once: an instance (master radio, launch flags), a device binding (the
selector) and their intersection.  Those have different cardinalities the
moment a role can hold more than one device (a stick AND a yoke in the
joystick role), so the section is split:

* Devices - one card per role.  A card owns the role's device selector(s);
  the joystick card can carry alternate devices, each with its own icon.
  The master-instance radio lives on the card header: which device is the
  master is device-first thinking, and most users never touch it.
* Launch Options - a slim grid of the true per-instance flags
  (auto-launch, start minimized, start headless), which stops growing:
  instances do not multiply, devices do.

Compatibility is deliberate: every widget the rest of the dialog (and the
test harness) knows by name - cb_select_j, rb_master_p, cb_al_enable_c and
friends - is created here under the same name and re-bound as an attribute
of the dialog, so the surrounding logic did not have to change shape.

Styling leans on palette() references so the app's light and dark themes
both work without theme-specific rules here.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from telemffb.ui.widgets.custom_widgets import LabeledToggle, Toggle
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

#: Device icon choices for the joystick role's devices: settings value ->
#: (tooltip, resource path).  The value is stored per device slot as
#: devicon_joystick / devicon_joystick_2 / ... and follows the device into
#: the Active Devices panel.
DEVICE_ICON_CHOICES = {
    'stick': ('Stick icon', ':/image/icon_joystick.png'),
    'yoke': ('Yoke icon', ':/image/icon_yoke.png'),
}

#: One selector width everywhere, so the columns line up across cards.
SELECTOR_WIDTH = 360

#: Where DirectLink for TelemFFB is offered.  DirectLink deliberately does
#: not ship with TelemFFB; the no-devices hint under the cards points here,
#: and omits its where-to-get-it sentence if this is ever emptied.
DINPUT_BRIDGE_URL = 'https://directlink.flyfrisby.com/'

#: Most rows a role card may hold (the active device plus alternates).
MAX_DEVICES_PER_ROLE = 3

#: (role key, legacy widget suffix, display name, icon resource)
ROLES = (
    ('joystick', 'j', 'Joystick', ':/image/icon_joystick.png'),
    ('pedals', 'p', 'Pedals', ':/image/icon_pedals.png'),
    ('collective', 'c', 'Collective', ':/image/icon_collective.png'),
    ('trimwheel', 't', 'Trim Wheel', ':/image/icon_trimwheel.png'),
    ('shaker', 's', 'Shaker', ':/image/icon_shaker.png'),
)

#: roles whose effects address one logical axis, so a DirectInput device
#: in them gets an FFB axis chooser (the joystick stays native X/Y; a
#: shaker has no axes at all)
AXIS_ROLES = ('pedals', 'collective', 'trimwheel')

def _card_qss(palette) -> str:
    """Card chrome with computed contrast: the palette's own alternate-base
    and mid sat too close to the ground on the dark theme to separate the
    header band from the device rows."""
    window = palette.color(QPalette.ColorRole.Window)
    dark = window.lightness() < 128
    head_bg = window.lighter(122).name() if dark else window.darker(106).name()
    border = window.lighter(150).name() if dark else window.darker(125).name()
    return """
QFrame#deviceCard {
    border: 1px solid %(border)s;
    border-radius: 4px;
}
QFrame#deviceCardHead {
    background: %(head_bg)s;
    border: none;
    border-bottom: 1px solid %(border)s;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QLabel#sectionLabel {
    color: palette(text);
    font-weight: bold;
}
""" % {'head_bg': head_bg, 'border': border}


def _icon_tint(palette) -> QColor:
    """The one purple every icon on this page is drawn in - the family
    color, brightened on the dark theme where the native shade sinks into
    the ground at small sizes."""
    tint = QColor(171, 55, 200)
    if palette.color(QPalette.ColorRole.Window).lightness() < 128:
        tint = tint.lighter(150)
    return tint


def _link_button(text: str, palette, tooltip: str = '') -> QPushButton:
    """A quiet link-style action: the family purple text, no box -
    bordered variants read as a broken widget, and the theme paints a
    flat QPushButton as a filled one.  Hover stays in the family: a step
    brighter, not a jump to grey."""
    button = QPushButton(text)
    button.setFlat(True)
    base = _icon_tint(palette)
    dark_theme = palette.color(QPalette.ColorRole.Window).lightness() < 128
    hover = base.lighter(130) if dark_theme else base.darker(120)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(
        'QPushButton { border: none; background: transparent;'
        ' padding: 1px 6px; text-align: left;'
        ' color: %s; }'
        'QPushButton:hover { color: %s; }'
        % (base.name(), hover.name()))
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def _tinted_icon(path, color) -> QIcon:
    """The icon artwork recolored (the source PNGs are purple line art,
    which disappears at small sizes on the dark theme)."""
    pm = QPixmap(path)
    if pm.isNull():
        return QIcon()
    tinted = QPixmap(pm.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.drawPixmap(0, 0, pm)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    return QIcon(tinted)


class DeviceRow(QWidget):
    """One device of a role: active marker, selector, ids, and - on roles
    with alternates - an icon picker and a remove button."""

    #: the user clicked this row's marker: make its device the active one
    make_active = pyqtSignal()
    #: the user removed this (alternate) row
    removed = pyqtSignal()
    icon_changed = pyqtSignal(str)

    def __init__(self, selector_name, alternates=False, primary=True,
                 axis_choice=False, parent=None):
        super().__init__(parent)
        self.primary = primary
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        if alternates:
            # a real radio: the marker MOVES between rows (the card's
            # exclusive group governs), the rows stay put.  The save maps
            # whichever row is marked onto devpath_{role}, so at rest the
            # active device always reopens in row one.
            self.marker = QRadioButton()
            self.marker.setChecked(primary)
            self.marker.setToolTip('Primary device - the device this '
                                   'instance connects to on startup.  '
                                   'Selecting another row switches to it '
                                   'at Save.')
            self.marker.clicked.connect(self.make_active.emit)
            # hidden (occupying nothing) while the card holds a single
            # device: a lone row must align exactly with the other cards -
            # the markers appearing when a choice exists may shift this
            # card's rows, and that reads as intended
            row.addWidget(self.marker)
        else:
            self.marker = None

        self.selector = QComboBox()
        self.selector.setObjectName(selector_name)
        # one fixed width everywhere: long enough for name + serial, and
        # every selector on the page lines up regardless of its content
        # or its row's trailing buttons
        self.selector.setFixedWidth(SELECTOR_WIDTH)
        row.addWidget(self.selector)

        self.ids_label = QLabel('')
        self.ids_label.setObjectName('deviceIds')
        self.ids_label.setFont(QFont('Consolas', 8))
        self.ids_label.setMinimumWidth(64)
        # dim through the theme's own placeholder color - a hard-coded dim
        # was unreadable on the dark theme
        self.ids_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.ids_label.setAlignment(Qt.AlignmentFlag.AlignLeft |
                                    Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.ids_label)

        # roles whose effects address one logical axis (pedals X,
        # collective Y, trim wheel Y) can point it at whichever axis a
        # DirectInput device actually renders force on.  Hidden until a
        # [DI] device is selected; the dialog fills it with the axes the
        # device reports.  The joystick never gets one - X/Y untouched.
        self.axis_label = None
        self.axis_combo = None
        self.axis_invert = None
        if axis_choice:
            self.axis_label = QLabel('FFB Axis:')
            self.axis_combo = QComboBox()
            self.axis_combo.setObjectName(
                selector_name.replace('cb_select', 'cb_axis'))
            self.axis_combo.setToolTip(
                "Which of the device's force feedback axes this role "
                "drives.\nAuto uses the role's own axis when the device "
                "has it, otherwise the device's first force-capable "
                "axis.\nOnly force-capable axes are listed.")
            self.axis_invert = QCheckBox('Invert')
            self.axis_invert.setObjectName(
                selector_name.replace('cb_select', 'cb_axis_inv'))
            self.axis_invert.setToolTip(
                "Reverse the axis's direction - forces and position "
                "both,\nas if the hardware ran the other way.  The "
                "DirectInput\nequivalent of reversing the axis in "
                "VPConfigurator.")
            for w in (self.axis_label, self.axis_combo, self.axis_invert):
                w.setVisible(False)
                row.addWidget(w)
        row.addStretch(1)

        if alternates:
            self.icon_group = QButtonGroup(self)
            self.icon_group.setExclusive(True)
            pick = QHBoxLayout()
            pick.setContentsMargins(0, 0, 0, 0)
            pick.setSpacing(0)
            self._icon_buttons = {}
            tint = _icon_tint(self.palette())
            for kind, (tip, path) in DEVICE_ICON_CHOICES.items():
                b = QToolButton()
                b.setCheckable(True)
                b.setToolTip(tip)
                b.setIcon(_tinted_icon(path, tint))
                b.setAutoRaise(True)
                self.icon_group.addButton(b)
                self._icon_buttons[kind] = b
                pick.addWidget(b)
                b.clicked.connect(lambda _c, k=kind: self.icon_changed.emit(k))
            self._icon_buttons['stick'].setChecked(True)
            row.addLayout(pick)

            self.remove_btn = QToolButton()
            self.remove_btn.setText('✕')
            self.remove_btn.setAutoRaise(True)
            self.remove_btn.setToolTip('Remove this device')
            # the primary row never shows one, but keeps the space so the
            # icon pickers line up down the card
            policy = self.remove_btn.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            self.remove_btn.setSizePolicy(policy)
            self.remove_btn.setVisible(not primary)
            self.remove_btn.clicked.connect(self.removed.emit)
            row.addWidget(self.remove_btn)
        else:
            self.icon_group = None
            self.remove_btn = None

        self.selector.currentIndexChanged.connect(self._refresh_ids)
        self._refresh_ids()

    def show_axis_choice(self, names, current='auto', inverted=False):
        """List the device's force axes; empty hides the chooser (a
        VPforce device, or nothing known)."""
        if self.axis_combo is None:
            return
        show = bool(names)
        self.axis_label.setVisible(show)
        self.axis_combo.setVisible(show)
        self.axis_invert.setVisible(show)
        self.axis_combo.blockSignals(True)
        self.axis_combo.clear()
        if show:
            self.axis_combo.addItem('Auto', 'auto')
            for name in names:
                self.axis_combo.addItem(name, name)
            idx = self.axis_combo.findData(current)
            self.axis_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.axis_combo.blockSignals(False)
        self.axis_invert.blockSignals(True)
        self.axis_invert.setChecked(bool(inverted) if show else False)
        self.axis_invert.blockSignals(False)

    def axis_choice_value(self):
        """The stored form of the chooser ('auto' or an axis name), or
        None while it is hidden - nothing to persist then.  isHidden, not
        isVisibleTo: a widget on a background tab page reports invisible
        while very much in play, and the save must see it."""
        if self.axis_combo is None or self.axis_combo.isHidden():
            return None
        return self.axis_combo.currentData() or 'auto'

    def axis_invert_value(self):
        """The inversion flag, or None while the chooser is hidden."""
        if self.axis_invert is None or self.axis_invert.isHidden():
            return None
        return self.axis_invert.isChecked()

    def device_icon(self) -> str:
        if self.icon_group is None:
            return 'stick'
        for kind, b in self._icon_buttons.items():
            if b.isChecked():
                return kind
        return 'stick'

    def set_device_icon(self, kind: str):
        if self.icon_group is not None and kind in self._icon_buttons:
            self._icon_buttons[kind].setChecked(True)

    def _refresh_ids(self, _index=None):
        """The USB ids of the selected device, display-only (the selector
        is the source of truth; nobody types PIDs any more).  Doubles as
        the marker gate: an empty row cannot be made the active device."""
        device = self.selector.currentData()
        vid = getattr(device, 'vendor_id', None)
        pid = getattr(device, 'product_id', None)
        channels = getattr(device, 'channels', None)
        if vid and pid is not None:
            self.ids_label.setText(f'{vid:04X}:{pid:04X}')
        elif device is not None and channels:
            # an audio output has no USB identity; its channel count is
            # what tells two 'Speakers' apart, and it reads 2 until
            # Windows has the card's layout set to more
            self.ids_label.setText(f'{int(channels)} ch')
        else:
            self.ids_label.setText('')
        if self.marker is not None:
            # the checked marker stays live however its row reads - being
            # active with nothing selected is a state, not a request
            self.marker.setEnabled(device is not None
                                   or self.marker.isChecked())


class TransducerRow(QWidget):
    """One transducer on the shaker card: name, output channel, position,
    gain, calibration profile, a play button that sounds this row alone
    (how a user tells three transducers apart) and a remove button."""

    removed = pyqtSignal()
    test_requested = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText('Name')
        self.name_edit.setFixedWidth(110)
        self.name_edit.setToolTip('What this transducer is called in the list')
        row.addWidget(self.name_edit)

        row.addWidget(QLabel('Ch:'))
        self.channel_combo = QComboBox()
        self.channel_combo.setToolTip(
            "Which of the output's channels this transducer is wired to.\n"
            'On a stereo card 1 is left and 2 is right; a 7.1 card counts\n'
            'front L/R, center, subwoofer, rear L/R, side L/R.')
        row.addWidget(self.channel_combo)

        self.position_combo = QComboBox()
        self.position_combo.setToolTip('Where the transducer sits')
        row.addWidget(self.position_combo)

        row.addWidget(QLabel('Gain:'))
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(0.0, 10.0)
        self.gain_spin.setSingleStep(0.1)
        self.gain_spin.setDecimals(1)
        self.gain_spin.setValue(1.0)
        self.gain_spin.setToolTip("This transducer's level, under the master gain")
        row.addWidget(self.gain_spin)

        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip(
            'How this transducer wants its pulses driven: resonance, band, '
            'pulse edges, brake.')
        row.addWidget(self.profile_combo)

        self.test_button = QToolButton()
        self.test_button.setText('\u25b6')
        self.test_button.setAutoRaise(True)
        self.test_button.setToolTip('Play a pulse and a tone through this transducer only')
        self.test_button.clicked.connect(self.test_requested.emit)
        row.addWidget(self.test_button)

        self.remove_button = QToolButton()
        self.remove_button.setText('\u2715')
        self.remove_button.setAutoRaise(True)
        self.remove_button.setToolTip('Remove this transducer')
        self.remove_button.clicked.connect(self.removed.emit)
        row.addWidget(self.remove_button)
        row.addStretch(1)

        self._channels = 0
        self.set_channel_count(2)
        for w in (self.name_edit, self.channel_combo, self.position_combo,
                  self.gain_spin, self.profile_combo):
            sig = getattr(w, 'currentIndexChanged', None) or getattr(w, 'valueChanged', None) \
                or getattr(w, 'textChanged', None)
            sig.connect(lambda *_: self.changed.emit())

    def set_channel_count(self, count: int) -> None:
        """Offer channels 1..count; a stored channel beyond that is kept
        on the list (marked) rather than lost, since the output may be
        reconfigured or reselected."""
        current = self.channel()
        count = max(1, int(count), current + 1)
        if count == self._channels:
            return
        self._channels = count
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for i in range(count):
            self.channel_combo.addItem(str(i + 1), i)
        self.channel_combo.setCurrentIndex(min(current, count - 1))
        self.channel_combo.blockSignals(False)

    def set_profiles(self, names) -> None:
        current = self.profile_combo.currentText()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for name in names:
            self.profile_combo.addItem(name)
        idx = self.profile_combo.findText(current)
        self.profile_combo.setCurrentIndex(max(0, idx))
        self.profile_combo.blockSignals(False)

    def set_positions(self, positions) -> None:
        current = self.position_combo.currentText()
        self.position_combo.blockSignals(True)
        self.position_combo.clear()
        for p in positions:
            self.position_combo.addItem(p)
        idx = self.position_combo.findText(current)
        self.position_combo.setCurrentIndex(max(0, idx))
        self.position_combo.blockSignals(False)

    def channel(self) -> int:
        data = self.channel_combo.currentData()
        return int(data) if data is not None else 0

    def value(self):
        from telemffb.hw.ffb_shaker import Transducer
        return Transducer(self.name_edit.text().strip() or 'Shaker', self.channel(),
                          round(self.gain_spin.value(), 2),
                          self.profile_combo.currentText(), self.position_combo.currentText())

    def set_value(self, t) -> None:
        self.name_edit.setText(t.name)
        self.set_channel_count(max(self._channels, t.channel + 1))
        self.channel_combo.setCurrentIndex(self.channel_combo.findData(t.channel))
        self.gain_spin.setValue(float(t.gain))
        idx = self.profile_combo.findText(t.profile)
        self.profile_combo.setCurrentIndex(max(0, idx))
        idx = self.position_combo.findText(t.position)
        self.position_combo.setCurrentIndex(max(0, idx))


class ShakerControls(QWidget):
    """The shaker's own controls under its output selector: master gain,
    the transducer rows, a row adder and a test button for the whole
    rig.  The dialog supplies the profile names and the output's channel
    count, wires the buttons, and reads the rows back at Save.
    """

    row_test_requested = pyqtSignal(int)
    rows_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(3)

        head = QHBoxLayout()
        self.head = head
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(QLabel('Gain:'))
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setObjectName('shaker_gain')
        self.gain_spin.setRange(0.0, 10.0)
        self.gain_spin.setSingleStep(0.1)
        self.gain_spin.setDecimals(1)
        self.gain_spin.setToolTip(
            "Master gain for everything the shaker plays.\nTelemFFB's effect "
            "intensities are tuned for a stick; a transducer needs several "
            "times that.  Each channel is limited, never clipped, so a high "
            "gain is safe.")
        head.addWidget(self.gain_spin)
        head.addStretch(1)
        # the output's own actions; the card places them on the selector row
        self.rescan_button = _link_button(
            'Rescan', self.palette(),
            'Look for sound cards plugged in since TelemFFB started.')
        self.rescan_button.setObjectName('shaker_rescan')
        self.layout_button = _link_button(
            'Speaker layout...', self.palette(),
            "Open the Windows Sound control panel.\n"
            "A card's channel count follows the speaker layout set there\n"
            "(select the card on the Playback tab, then Configure):\n"
            "a 7.1 card reports two channels until it is set to 7.1 Surround.\n"
            "Rescan afterwards.")
        self.layout_button.setObjectName('shaker_layout')
        self.add_button = _link_button('+ add transducer', self.palette(),
                                       'Another transducer on this sound card')
        self.add_button.setObjectName('shaker_add')
        head.insertWidget(2, self.add_button)
        outer.addLayout(head)

        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(2)
        outer.addWidget(self.rows_host)

        # shown when a row names a channel the selected output does not
        # report: the usual cause is a multi-channel card that Windows
        # still has set to stereo
        self.hint = QLabel('')
        self.hint.setObjectName('shakerHint')
        self.hint.setWordWrap(True)
        self.hint.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.hint.setVisible(False)
        outer.addWidget(self.hint)

        # the one real button on the card, bottom right
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 2, 0, 0)
        foot.addStretch(1)
        self.test_button = QPushButton('Test all')
        self.test_button.setObjectName('shaker_test')
        self.test_button.setToolTip(
            'Play a pulse and a short tone through every transducer at once, '
            'with these settings, saved or not.')
        foot.addWidget(self.test_button)
        outer.addLayout(foot)

        self._profiles = []
        self._positions = []
        self._channels = 2
        self.rows = []
        self.add_button.clicked.connect(lambda: self.add_row())

    def set_profiles(self, names) -> None:
        self._profiles = list(names)
        for row in self.rows:
            row.set_profiles(self._profiles)

    def set_positions(self, positions) -> None:
        self._positions = list(positions)
        for row in self.rows:
            row.set_positions(self._positions)

    def set_channel_count(self, count: int) -> None:
        self._channels = max(1, int(count))
        for row in self.rows:
            row.set_channel_count(self._channels)
        self.refresh_hint()

    def refresh_hint(self) -> None:
        """Say which rows the selected output cannot drive as it stands."""
        beyond = [row.value().name for row in self.rows if row.channel() >= self._channels]
        if beyond:
            self.hint.setText(
                f"This output reports {self._channels} channel"
                f"{'' if self._channels == 1 else 's'}, so {', '.join(beyond)} "
                f"cannot be driven yet.  If the card has more, set its speaker "
                f"layout in Windows (Speaker layout...), then Rescan outputs.")
        self.hint.setVisible(bool(beyond))

    def add_row(self, transducer=None):
        row = TransducerRow(self.rows_host)
        row.set_profiles(self._profiles)
        row.set_positions(self._positions)
        row.set_channel_count(self._channels)
        if transducer is not None:
            row.set_value(transducer)
        else:
            row.name_edit.setText(f'Transducer {len(self.rows) + 1}')
            # a new row takes the first channel nobody else is on
            taken = {r.channel() for r in self.rows}
            free = next((c for c in range(self._channels) if c not in taken), 0)
            row.channel_combo.setCurrentIndex(row.channel_combo.findData(free))
        row.removed.connect(lambda r=row: self.remove_row(r))
        row.test_requested.connect(lambda r=row: self.row_test_requested.emit(self.rows.index(r)))
        row.changed.connect(self.rows_changed.emit)
        row.changed.connect(self.refresh_hint)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self.refresh_hint()
        self.rows_changed.emit()
        return row

    def remove_row(self, row) -> None:
        if row not in self.rows:
            return
        self.rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self.refresh_hint()
        self.rows_changed.emit()

    def clear_rows(self) -> None:
        for row in list(self.rows):
            self.remove_row(row)

    def transducers(self):
        return [row.value() for row in self.rows]

    def set_transducers(self, transducers) -> None:
        self.clear_rows()
        for t in transducers:
            self.add_row(t)

    def set_test_enabled(self, enabled: bool) -> None:
        self.test_button.setEnabled(enabled and bool(self.rows))
        for row in self.rows:
            row.test_button.setEnabled(enabled)

class RoleCard(QFrame):
    """One role: header (icon, name, master radio) over its device row(s).

    A card built with ``alternates=True`` (the joystick) can hold extra
    device rows - the rig realities are a center stick plus a side stick,
    or a stick plus a yoke; nobody runs two pedal sets.  The rows STAY PUT
    and the active marker moves between them; at Save the marked row's
    device is written to ``devpath_{role}`` (what startup and everything
    else reads) and the remaining rows to ``devpath_{role}_2``/``_3``, so
    a reopened dialog always shows the active device in row one, marker
    at rest.
    """

    add_requested = pyqtSignal()
    activate_requested = pyqtSignal(int)  # row slot (1 = primary, 2, 3)
    remove_requested = pyqtSignal(int)    # alternate slot number (2, 3)

    def __init__(self, role, suffix, display, icon_path, alternates=False,
                 parent=None):
        super().__init__(parent)
        self.role = role
        self.suffix = suffix
        self.alternates = alternates
        self.active_slot = 1
        self.setObjectName('deviceCard')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QFrame()
        head.setObjectName('deviceCardHead')
        head_l = QHBoxLayout(head)
        head_l.setContentsMargins(10, 4, 10, 4)
        head_l.setSpacing(8)

        # auto-launch switch, leftmost: off collapses the card to this
        # header (settings survive - see the dialog's launch-state logic).
        # Hidden on the master's card, which launches itself.
        self.launch_toggle = Toggle(self)
        self.launch_toggle.setToolTip(
            'Launch this instance automatically when the master starts.  '
            'Switched off, the card collapses; its configuration is kept.')
        # the master's card hides it but keeps the space: every card's
        # icon and name line up whichever role is master
        policy = self.launch_toggle.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.launch_toggle.setSizePolicy(policy)
        head_l.addWidget(self.launch_toggle)

        icon = QLabel()
        pm = _tinted_icon(icon_path, _icon_tint(self.palette())).pixmap(18, 18)
        if not pm.isNull():
            icon.setPixmap(pm)
        head_l.addWidget(icon)

        name = QLabel(display)
        f = name.font()
        f.setBold(True)
        name.setFont(f)
        # fixed name column so the separators and radios align down the page
        name.setFixedWidth(92)
        head_l.addWidget(name)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.VLine)
        rule.setFixedHeight(14)
        head_l.addWidget(rule)

        # One radio per card, but only the CHECKED one says what it is -
        # four repetitions of 'Master instance' were noise.  The bare
        # circles carry a tooltip, and the single visible label teaches
        # the pattern.
        self.master_radio = QRadioButton('')
        self.master_radio.setObjectName(f'rb_master_{suffix}')
        # radios live on different cards; the dialog's QButtonGroup makes
        # them exclusive, autoExclusive would fight it per-card
        self.master_radio.setAutoExclusive(False)
        self.master_radio.setToolTip(
            'Make this instance the master (takes effect after a restart)')
        self.master_radio.toggled.connect(
            lambda on, rb=self.master_radio:
                rb.setText('Master instance' if on else ''))
        head_l.addWidget(self.master_radio)
        head_l.addStretch(1)

        # a collapsed card still says what it is configured to drive
        self.collapsed_device = QLabel('')
        self.collapsed_device.setForegroundRole(
            QPalette.ColorRole.PlaceholderText)
        self.collapsed_device.setVisible(False)
        head_l.addWidget(self.collapsed_device)

        self.window_mode_label = QLabel('Window Mode:')
        head_l.addWidget(self.window_mode_label)
        self.window_mode = QComboBox()
        self.window_mode.addItems(['Headless', 'Minimized', 'Normal'])
        self.window_mode.setToolTip(
            "How this instance's window starts.\n"
            'Headless: no window at all - the instance runs invisibly '
            '(everything is configured from the master).\n'
            'Minimized: a window, started minimized to the taskbar.\n'
            'Normal: a regular window.')
        head_l.addWidget(self.window_mode)

        outer.addWidget(head)

        self.body_host = QWidget()
        self.body = QVBoxLayout(self.body_host)
        self.body.setContentsMargins(8, 4, 8, 5)
        self.body.setSpacing(2)
        outer.addWidget(self.body_host)

        # primary device row (the active device whenever the card is at
        # rest; the marker may move to an alternate within a session)
        self.primary_row = DeviceRow(f'cb_select_{suffix}',
                                     alternates=alternates, primary=True,
                                     axis_choice=(role in AXIS_ROLES))
        self.body.addWidget(self.primary_row)
        self.selector = self.primary_row.selector
        self.marker_group = None
        if alternates:
            self.marker_group = QButtonGroup(self)
            self.marker_group.setExclusive(True)
            self.marker_group.addButton(self.primary_row.marker)
            self.primary_row.make_active.connect(
                lambda: self.activate_requested.emit(1))

        # the shaker's controls live under its selector; the dialog
        # reaches them through the panel's shaker_controls
        self.shaker = None
        if role == 'shaker':
            self.shaker = ShakerControls()
            # the output's actions sit at the right end of the output's
            # own row, not among the transducer controls
            row = self.primary_row.layout()
            row.insertWidget(2, self.shaker.rescan_button)
            row.insertWidget(3, self.shaker.layout_button)
            self.body.addWidget(self.shaker)

        self.alt_rows = []               # DeviceRow, slots 2..MAX
        self.add_button = None
        if alternates:
            self.add_button = _link_button('+ add device', self.palette())
            self.add_button.clicked.connect(self.add_requested.emit)
            wrap = QHBoxLayout()
            wrap.setContentsMargins(24, 1, 0, 0)
            wrap.addWidget(self.add_button)
            wrap.addStretch(1)
            self.body.addLayout(wrap)
            self._sync_marker_visibility()

    # ---- alternate row management (cards built with alternates=True) ----

    def alt_slot_numbers(self):
        """The settings-slot numbers currently shown (2, 3, ...)."""
        return list(range(2, 2 + len(self.alt_rows)))

    def add_alt_row(self) -> DeviceRow:
        """Append an alternate row and return it (caller fills the combo)."""
        slot = 2 + len(self.alt_rows)
        row = DeviceRow(f'cb_select_{self.suffix}_{slot}',
                        alternates=True, primary=False)
        row.marker.setChecked(False)
        self.marker_group.addButton(row.marker)
        row.make_active.connect(
            lambda r=row: self.activate_requested.emit(self._slot_of(r)))
        row.removed.connect(
            lambda r=row: self.remove_requested.emit(self._slot_of(r)))
        self.alt_rows.append(row)
        # keep the add button last
        self.body.insertWidget(self.body.count() - 1, row)
        self._sync_add_button()
        self._sync_marker_visibility()
        return row

    def remove_alt_row(self, slot: int):
        idx = slot - 2
        if 0 <= idx < len(self.alt_rows):
            row = self.alt_rows.pop(idx)
            self.marker_group.removeButton(row.marker)
            self.body.removeWidget(row)
            row.deleteLater()
        if self.active_slot == slot or self.active_slot > 1 + len(self.alt_rows):
            self.set_active_slot(1)
        self._sync_add_button()
        self._sync_marker_visibility()

    def _slot_of(self, row) -> int:
        return 2 + self.alt_rows.index(row)

    def set_active_slot(self, slot: int):
        """Move the marker to a row (1 = primary).  State only - the caller
        owns what activation means (persistence, notices)."""
        self.active_slot = slot
        row = self.active_row()
        if row is not None and row.marker is not None:
            row.marker.setChecked(True)
        # re-derive each marker's enabled state around the new position
        for r in [self.primary_row] + self.alt_rows:
            r._refresh_ids()

    def active_row(self):
        if self.active_slot == 1 or not self.alt_rows:
            return self.primary_row
        idx = self.active_slot - 2
        return self.alt_rows[idx] if 0 <= idx < len(self.alt_rows) \
            else self.primary_row

    def rows_by_priority(self):
        """(row, is_active) pairs: the active row first, then the rest in
        visual order - the order the save maps onto storage slots."""
        active = self.active_row()
        ordered = [active] + [r for r in [self.primary_row] + self.alt_rows
                              if r is not active]
        return ordered

    def _sync_add_button(self):
        if self.add_button is not None:
            self.add_button.setVisible(
                1 + len(self.alt_rows) < MAX_DEVICES_PER_ROLE)

    def _sync_marker_visibility(self):
        """The primary marker only means something once there is a choice:
        with a single device it hides (space retained)."""
        if self.primary_row.marker is not None:
            self.primary_row.marker.setVisible(bool(self.alt_rows))

    def set_launch_controls_visible(self, visible: bool):
        """The master's card hides these: it launches itself."""
        self.launch_toggle.setVisible(visible)
        self.window_mode_label.setVisible(visible)
        self.window_mode.setVisible(visible)

    def set_collapsed(self, collapsed: bool):
        """Collapse to the header (auto-launch switched off).  The
        configuration underneath is kept, and the header names the
        configured device so an off-role is not a mystery box."""
        self.body_host.setVisible(not collapsed)
        if collapsed:
            device = self.active_row().selector.currentData()                 if self.alternates else self.primary_row.selector.currentData()
            ident = str(getattr(device, 'ident', '') or '').strip()
            self.collapsed_device.setText(ident or 'no device')
        self.collapsed_device.setVisible(collapsed)


class DeviceCardsPanel(QWidget):
    """The full Devices + Launch Options area; see the module docstring."""

    #: attribute names the dialog re-binds onto itself for compatibility
    LEGACY_WIDGETS = (
        'cb_select_j', 'cb_select_p', 'cb_select_c', 'cb_select_t', 'cb_select_s',
        'cb_axis_p', 'cb_axis_c', 'cb_axis_t',
        'rb_master_j', 'rb_master_p', 'rb_master_c', 'rb_master_t', 'rb_master_s',
        'cb_al_enable',
        'cb_al_enable_j', 'cb_al_enable_p', 'cb_al_enable_c', 'cb_al_enable_t', 'cb_al_enable_s',
        'cb_min_enable_j', 'cb_min_enable_p', 'cb_min_enable_c', 'cb_min_enable_t', 'cb_min_enable_s',
        'cb_headless_j', 'cb_headless_p', 'cb_headless_c', 'cb_headless_t', 'cb_headless_s',
        'labelLaunch', 'lab_auto_launch', 'lab_start_min', 'lab_start_headless',
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_card_qss(self.palette()))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # section header row: label left, the global auto-launch switch in
        # the dead space at the right - it governs everything below it
        head_row = QHBoxLayout()
        devices_label = QLabel('Devices:')
        devices_label.setObjectName('sectionLabel')
        f = devices_label.font()
        f.setUnderline(True)
        devices_label.setFont(f)
        head_row.addWidget(devices_label)
        head_row.addStretch(1)
        self.al_enable_toggle = LabeledToggle(
            self, label='Enable Auto-Launch',
            tooltip='Launch the configured child instances automatically '
                    'when the master starts.  Each role opts in with the '
                    'switch on its own card.')
        head_row.addWidget(self.al_enable_toggle)
        layout.addLayout(head_row)

        self.cards = {}
        for role, suffix, display, icon in ROLES:
            card = RoleCard(role, suffix, display, icon,
                            alternates=(role == 'joystick'))
            self.cards[role] = card
            layout.addWidget(card)
            setattr(self, f'cb_select_{suffix}', card.selector)
            if role in AXIS_ROLES:
                setattr(self, f'cb_axis_{suffix}', card.primary_row.axis_combo)
            setattr(self, f'rb_master_{suffix}', card.master_radio)
            if role == 'shaker':
                # the master coordinates the force feedback instances;
                # a shaker is always a child of one
                card.master_radio.setEnabled(False)
                card.master_radio.setToolTip(
                    'A shaker runs as a child instance; a force feedback '
                    'device is the master.')
        self.joystick_card = self.cards['joystick']
        self.shaker_controls = self.cards['shaker'].shaker

        # For the first launch with no VPforce hardware: the cards sit
        # empty and the one switch that would list a DirectInput stick
        # lives on another page - say so.  The dialog decides when this
        # shows (no VPforce devices, nothing configured, DirectInput off).
        #
        # Line-broken by hand rather than word-wrapped: a wrapping label
        # reports a one-line minimum height, which let the dialog open
        # too short and crush the tallest card (the joystick selector
        # painted clipped until the frame was dragged).
        lines = ['No VPforce devices were found.  DirectInput support for '
                 'other force feedback devices',
                 'can be enabled on the System page.']
        if DINPUT_BRIDGE_URL:
            lines[-1] += (f'  Visit <a href="{DINPUT_BRIDGE_URL}">'
                          f'{DINPUT_BRIDGE_URL}</a> to get your copy of')
            lines.append('DirectLink for TelemFFB if you do not '
                         'already have it.')
            hint = '<br/>'.join(lines)     # rich text: the link needs it
        else:
            hint = '\n'.join(lines)
        self.dinput_hint = QLabel(hint)
        self.dinput_hint.setObjectName('dinputHint')
        self.dinput_hint.setOpenExternalLinks(True)
        # dim through the theme's own placeholder color, like the ids labels
        self.dinput_hint.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.dinput_hint.setVisible(False)
        layout.addWidget(self.dinput_hint)

        # ------------------------------------------------------------------
        # Legacy launch widgets, alive but invisible.  Every save/load/
        # validation path (and the test harness) reads and writes these
        # checkboxes; the visible controls - the per-card switches and
        # Window Mode dropdowns, and the global toggle above - are synced
        # to them in both directions.
        # ------------------------------------------------------------------
        self.cb_al_enable = QCheckBox('Enable Auto-Launch', self)
        self.cb_al_enable.setVisible(False)
        self.labelLaunch = QLabel('Launch Options:', self)
        self.lab_auto_launch = QLabel('Auto Launch:', self)
        self.lab_start_min = QLabel('Start Minimized:', self)
        self.lab_start_headless = QLabel('Start Headless:', self)
        for lab in (self.labelLaunch, self.lab_auto_launch,
                    self.lab_start_min, self.lab_start_headless):
            lab.setVisible(False)

        for role, suffix, display, _icon in ROLES:
            al = QCheckBox(self)
            al.setObjectName(f'cb_al_enable_{suffix}')
            mn = QCheckBox(self)
            mn.setObjectName(f'cb_min_enable_{suffix}')
            hl = QCheckBox(self)
            hl.setObjectName(f'cb_headless_{suffix}')
            for cb in (al, mn, hl):
                cb.setVisible(False)
            setattr(self, f'cb_al_enable_{suffix}', al)
            setattr(self, f'cb_min_enable_{suffix}', mn)
            setattr(self, f'cb_headless_{suffix}', hl)
            self._wire_launch_sync(self.cards[role], al, mn, hl)

        # global toggle <-> hidden global checkbox
        self.cb_al_enable.toggled.connect(self.al_enable_toggle.setChecked)
        self.al_enable_toggle.stateChanged.connect(
            lambda state: self.cb_al_enable.setChecked(bool(state)))

    def _wire_launch_sync(self, card, al_box, min_box, headless_box):
        """Two-way sync between a card's visible launch controls and its
        hidden legacy checkboxes.  setChecked on an unchanged value emits
        nothing, so the loops terminate."""
        card.launch_toggle.stateChanged.connect(
            lambda state: al_box.setChecked(bool(state)))
        al_box.toggled.connect(card.launch_toggle.setChecked)

        def apply_combo(index):
            headless_box.setChecked(index == 0)
            min_box.setChecked(index == 1)

        def sync_combo(*_):
            index = 0 if headless_box.isChecked() else \
                1 if min_box.isChecked() else 2
            card.window_mode.blockSignals(True)
            card.window_mode.setCurrentIndex(index)
            card.window_mode.blockSignals(False)

        card.window_mode.currentIndexChanged.connect(apply_combo)
        headless_box.toggled.connect(sync_combo)
        min_box.toggled.connect(sync_combo)
        sync_combo()

    def bind_to(self, dialog):
        """Give the dialog the widget names its logic and the tests know."""
        for name in self.LEGACY_WIDGETS:
            setattr(dialog, name, getattr(self, name))

    def refresh_ids_labels(self):
        """Re-derive every row's USB-ids readout.  Selection restores run
        with signals blocked, so the labels never hear about them - without
        this, a freshly opened dialog showed placeholders on every row the
        user had not touched."""
        for card in self.cards.values():
            card.primary_row._refresh_ids()
            for row in card.alt_rows:
                row._refresh_ids()

    def alt_selectors(self):
        """The alternate-device combos, in slot order (may be empty)."""
        return [row.selector for row in self.joystick_card.alt_rows]
