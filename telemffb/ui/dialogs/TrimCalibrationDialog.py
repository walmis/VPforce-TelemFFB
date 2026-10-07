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
"""Dialog front-end for the elevator ``virtual_y`` auto-calibration engine.

Runs on the master (joystick) instance. It arms/aborts the ``TrimCalibrator``
that lives on ``G.telem_manager.currentAircraft`` and displays live status +
the result; the control loop itself runs in the telemetry thread. See
:mod:`telemffb.sim.msfs_xp.TrimCalibrator`.
"""
import html
import json
import logging
import os
import re
import time

from PyQt6 import QtCore
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QProgressBar, QMessageBox, QFrame, QCheckBox, QSizePolicy,
    QComboBox, QToolButton, QMenu, QFileDialog, QSpinBox, QScrollArea,
)

import telemffb.globals as G
import telemffb.utils as utils
import telemffb.xmlutils as xmlutils
from telemffb.ui.widgets.custom_widgets import (
    IasTrendWidget, InfoLabel, NoWheelComboBox, TrimCurveWidget, VsTargetWidget,
    svg_icon, vpf_purple,
)
from telemffb.sim.msfs_xp.MsfsXpTrimwheelMixIn import MsfsXpTrimwheelMixIn
from telemffb.sim.msfs_xp.TrimCalibrator import CalState, TrimCalibrator
from telemffb.ui.theme.tokens import (
    BORDER_GRAY, DISABLED_GRAY, DIVIDER_GRAY, ERROR_RED, INFO_BLUE, OK_GREEN,
    PURPLE_HOVER_FILL, WARNING_AMBER, WARNING_BANNER_BG, WARNING_TEXT_AMBER,
    DANGER_BANNER_BG,
)

logger = logging.getLogger(__name__)

MS_TO_KT = 1.0 / 0.514444
MS_TO_FPM = 196.850394


class TrimCalibrationDialog(QDialog):
    """Modeless dialog to auto-calibrate ``joystick_trim_follow_gain_virtual_y``."""

    # Emitted on Save / family edits with a JSON payload:
    # {"curves": [entry,...], "use_curve": bool, "stick_position"?: str}
    result_saved = pyqtSignal(str)
    # Emitted when the trimmed-stick-position pulldown changes — persisted
    # standalone so the mode is adjustable post-calibration without a run.
    position_mode_changed = pyqtSignal(str)
    # Emitted when a debug trim-write control changes: (setting name, value),
    # persisted under the TRIMWHEEL device scope of the active profile.
    trim_write_setting_changed = pyqtSignal(str, str)

    # The aircraft's trimwheel-scope trim write settings the calibration
    # honors, with the trimwheel mixin's defaults (MsfsXpTrimwheelMixIn /
    # MsfsXpFBWFlightControlsMixIn) for rows the aircraft does not set.
    TRIM_WRITE_DEFAULTS = {
        "trimwheel_use_axis": False,
        "trimwheel_axis_invert": False,
        "enable_custom_y_axis": False,
        "custom_y_axis": "AXIS_ELEV_TRIM_SET",
        "raw_y_axis_scale": 16384,
    }
    # defaults.xml validvalues for the trimwheel's custom_y_axis / raw_y_axis_scale.
    TW_VAR_CHOICES = ("AXIS_ELEV_TRIM_SET", "L:INPUT_TRIM_AXIS", "DO_NOT_SEND")
    TW_RANGE_CHOICES = ("1", "100", "256", "4096", "16384")

    STICK_POSITION_MODES = ("Follows Trim", "Stays Centered")
    # Export-file header for the shareable calibration-set format.
    EXPORT_TYPE = "telemffb-trim-calibration"
    # Save within this many knots of a stored entry offers to REPLACE it
    # (re-calibration semantics) instead of storing a near-duplicate speed.
    FAMILY_REPLACE_KT = 5.0

    # Combo index -> engine pitch-gain scale (Control response).
    RESPONSE_SCALES = {0: 1.5, 1: 1.0, 2: 0.5, 3: 0.25}
    RESPONSE_DEFAULT_INDEX = 1   # Normal

    # Glider descent target (spinbox, shown only for GliderAircraft). An
    # engineless aircraft cannot hold level flight, so calibration holds a
    # steady sink instead. fpm; m/s = fpm / MS_TO_FPM.
    MS_TO_FPM = 196.85
    GLIDER_VS_DEFAULT_FPM = -100
    GLIDER_VS_MIN_FPM = -1000
    GLIDER_VS_MAX_FPM = 0
    GLIDER_VS_STEP_FPM = 10
    # States in which the control-response combo may still be changed live
    # (from the sweep onward it locks so the measurement dynamics stay fixed).
    RESPONSE_LIVE_STATES = ("PROBE", "STABILIZE", "TRIM_NEUTRAL",
                            "ASSIST_HOLD", "SPEED_SETTLE")

    # Guided-flow step tracker: the engine's states map onto three
    # user-facing steps in domain terms. Colors carry WHO acts — blue =
    # automatic (TelemFFB is flying), amber = user action needed, green =
    # armed / complete, red = failure. The engine's own status_message
    # remains the unabridged detail feed below the tracker; only the raw
    # state enum leaves the (non-debug) UI.
    TRACKER_STEPS = ("Level &amp; neutral trim", "Test speed", "Trim sweep")  # HTML-ready
    TRACKER_STEP_OF_STATE = {
        "PROBE": 1, "STABILIZE": 1, "TRIM_NEUTRAL": 1,
        "ASSIST_HOLD": 2,
        "SPEED_SETTLE": 3, "SWEEP": 3, "SOLVE": 3, "RESTORE": 3,
    }
    COL_AUTO = INFO_BLUE
    COL_ACTION = WARNING_AMBER
    COL_OK = OK_GREEN
    COL_BAD = ERROR_RED

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Elevator Trim Calibration (Joystick)")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        # No explicit minimum width: an explicit minimum OVERRIDES the
        # layout-derived one in Qt, which allowed the window to be squeezed
        # below what the stored-family row needs (controls overlapped
        # instead of the resize stopping). Let the layout derive it.

        self._last_result = None
        self._result_shown = False
        self._telem_connected = False
        self._ias_hist = []   # (t, IAS m/s) for the trend indicator
        self._cal_seen = None    # id() of the calibrator the display belongs to
        self._stored_curve_seen = None  # raw curve JSON the display reflects
        self._was_running = False       # rising-edge detect: a new run clears the graph
        self._fam_raw = []       # stored family: raw entry dicts, speed-sorted
        self._fam = []           # parsed view of the same entries (index-aligned)
        self._fam_sel = 0        # manager selection index
        # Paused sims stop sending telemetry, so the timeout fires right after
        # the pause frame; remember it so the idle fallback can say "unpause"
        # instead of clobbering it with the generic waiting message.
        self._sim_paused_seen = False

        # Parented single-shot (never QTimer.singleShot with a bound method:
        # a fire after deleteLater is a fatal Qt-slot exception) deferring
        # the help-pane re-derive out of resizeEvent.
        self._repane_timer = QtCore.QTimer(self)
        self._repane_timer.setSingleShot(True)
        self._repane_timer.timeout.connect(self._repane)

        self._build_ui()
        self._refresh_idle()

    # ---- UI construction ----------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # Trim-following-disabled notice. The Settings-tab entry point is
        # prereq-gated, but the Utilities menu opens this dialog
        # unconditionally — without this, a user whose aircraft has trim
        # following disabled can run a perfect calibration and never learn
        # why flying with the result changes nothing (the curve/gain
        # settings are prereq-filtered off the aircraft entirely).
        self.warn_tf = QLabel()
        self.warn_tf.setWordWrap(True)
        self.warn_tf.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.warn_tf.setStyleSheet(
            f"QLabel {{ background-color:{WARNING_BANNER_BG}; color:white; font-weight:bold;"
            " font-size:11pt; border-radius:6px; padding:6px; }"
        )
        self.warn_tf.setVisible(False)
        root.addWidget(self.warn_tf)

        # Collapsible instructions: veterans reclaim the space with one click
        # and the collapsed state persists per device (first-time users see
        # them expanded). A vertical disclosure beats a popup (no second
        # window to manage) and a side drawer (width changes re-wrap every
        # height-for-width label — the exact fragility _fit_to_content
        # exists to contain).
        # Clickability must be visible: link-blue bold text, hand cursor and
        # a hover highlight — a plain bold label reads as a heading, not a
        # control (field feedback). Shared with the Result disclosure.
        disclosure_style = (
            f"QToolButton {{ border: none; font-weight: bold; color: {INFO_BLUE};"
            " padding: 2px 4px; }"
            "QToolButton:hover { background-color: rgba(42, 127, 212, 38);"
            " border-radius: 4px; }")
        self.btn_instructions = QToolButton()
        self.btn_instructions.setCheckable(True)
        self.btn_instructions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_instructions.setStyleSheet(disclosure_style)
        self.btn_instructions.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_instructions.clicked.connect(self._on_instructions_toggled)
        root.addWidget(self.btn_instructions)

        self.lbl_instructions = QLabel(
            "<ul style='margin-top:2px; margin-bottom:6px; -qt-list-indent:1;'>"
            "<li>It is recommended to perform the calibration in <b>clear</b>, <b>calm</b> conditions.</li>"
            "<li>If you have a hardware device controlling the elevator trim <b>AXIS</b>, you may "
            "need to disconnect or un-bind it as it may interfere with TelemFFB "
            "controlling the trim during calibration. (Excludes VPforce trim-wheel)</li>"
            "</ul>"
            "<ol style='margin-top:0px; margin-bottom:0px; -qt-list-indent:1;'>"
            "<li>Get airborne, straight &amp; level, at a stable cruise speed with "
            "Autopilot <b>OFF</b>.</li>"
            "<li>Press <b>Begin Calibration</b> — TelemFFB takes the controls, "
            "levels and trims the aircraft (<i>Preparing…</i>), then holds it "
            "while you set your test speed with the throttle (<i>Settling…</i> — "
            "it re-trims after every power change; in a glider, adjust the "
            "target descent rate instead — airspeed follows the sink). Your "
            "stick is <b>inactive</b> while TelemFFB has the controls.</li>"
            "<li>Once speed and trim hold steady, the button becomes "
            "<b>Start Trim Sweep</b> — press it to calibrate the current "
            "airspeed, or adjust power first to pick a different speed. With "
            "<b>Start sweep automatically when steady</b> checked, the sweep "
            "begins on its own at that point. "
            "<b>Abort</b> works at any point; aborting during the hold leaves "
            "the aircraft trimmed for the current power.</li>"
            "<li>The sweep measures the stick input needed to hold the nose level "
            "across the trim range, producing a calibrated trim curve <b>for that "
            "airspeed</b>; trim is restored when it finishes.</li>"
            "<li><b>Save</b>, then repeat at 2–3 different airspeeds (slow flight, "
            "cruise, high cruise) — the window stays ready after each save. In flight, "
            "TelemFFB blends the stored speeds by airspeed, so trim behaves correctly "
            "across the whole envelope.</li>"
            "<li>The <b>Stored speeds</b> row reviews each calibration (all are shown "
            "on the graph), deletes individually, and exports/imports complete "
            "calibration sets to share between users.</li>"
            "</ol>"
        )
        self.lbl_instructions.setWordWrap(True)
        # Top-aligned: when the pane is taller than a re-wrapped text (user
        # widened the window) the text must hug the disclosure button, not
        # float vertically centered.
        self.lbl_instructions.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # The help pane is the dialog's one ELASTIC element: it lives in a
        # scroll area whose height _fit_to_content sets explicitly - full
        # content height when the window fits the screen, shrunk (scrollbar
        # appears) when it would not. Everything else keeps its natural
        # height, so on a low-resolution monitor the live status, buttons
        # and result stay reachable instead of the window growing off-screen.
        self.scroll_instructions = QScrollArea()
        self.scroll_instructions.setWidget(self.lbl_instructions)
        self.scroll_instructions.setWidgetResizable(True)
        self.scroll_instructions.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_instructions.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll_instructions)
        expanded = not bool(G.system_settings.get(
            "TrimCalInstructionsCollapsed", False))
        self.btn_instructions.setChecked(expanded)
        self._apply_instructions_state(expanded)

        response_row = QHBoxLayout()
        lbl_response = InfoLabel(
            text="Control response:",
            tooltip=(
                "Strength of the control inputs used to fly the aircraft during calibration.\n\n"
                "Increased — sluggish or heavy aircraft whose slow porpoising gets WORSE\n"
                "when the response is lowered (the leveling loop is the only thing damping\n"
                "a long, lazy phugoid — it needs more authority, not less).\n"
                "Normal — most aircraft.\n"
                "Reduced — sensitive aircraft that porpoise or bounce during stabilization.\n"
                "Minimal — very sensitive or aerobatic aircraft with light, twitchy pitch.\n\n"
                "Calibration also reduces its own control gains automatically when it detects\n"
                "a growing oscillation; this option just starts from a different setting. If a\n"
                "run aborts with a pitch-oscillation error, retry with the next lower setting;\n"
                "if a slow porpoise gets worse as you lower it, go the other way.\n\n"
                "Can be changed during a run up until the sweep begins — e.g. adjust it\n"
                "while watching a porpoise develop during stabilization. The change applies\n"
                "immediately (during the brief polarity probe it is applied when the probe\n"
                "completes); once the sweep starts the setting locks for the rest of the run."))
        # InfoLabel's internal minimum is broken (width pinned to text
        # HEIGHT), so it truncates under squeeze — enforce content width.
        lbl_response.setMinimumWidth(lbl_response.sizeHint().width())
        response_row.addWidget(lbl_response)
        self.cmb_response = QComboBox()
        self.cmb_response.addItems([
            "Increased — sluggish / heavy aircraft",
            "Normal",
            "Reduced — sensitive aircraft",
            "Minimal — very sensitive / aerobatic",
        ])
        self.cmb_response.setCurrentIndex(self.RESPONSE_DEFAULT_INDEX)
        self.cmb_response.currentIndexChanged.connect(self._on_response_changed)
        response_row.addWidget(self.cmb_response)
        response_row.addStretch(1)
        root.addLayout(response_row)

        # Glider-only: target descent rate. Gliders have no engine, so there
        # is no "hold level" — the calibration holds a steady sink instead.
        # The whole row hides for powered aircraft (visibility tracked per
        # aircraft in _on_telemetry). Not persisted — resets to the default
        # each time the dialog opens.
        self.glider_row = QHBoxLayout()
        self.lbl_glider_vs = InfoLabel(
            text="Glider descent rate:",
            tooltip=(
                "This aircraft is a glider — with no engine it cannot hold level\n"
                "flight, so the calibration holds a steady descent instead of\n"
                "leveling off. Pick the sink rate you want to hold during the run\n"
                "(a gentle, steady descent your glider can maintain hands-off is\n"
                "ideal).\n\n"
                "While TelemFFB holds at the test speed, this is also how you pick a\n"
                "different test speed: with no throttle, a glider's airspeed\n"
                "follows its sink rate — set a steeper descent to fly faster, a\n"
                "shallower one to fly slower, then start the sweep once it\n"
                "re-settles. Locks once the sweep begins.\n\n"
                "This only affects how the aircraft is flown while calibrating;\n"
                "the stored trim curve is the same regardless."))
        self.lbl_glider_vs.setMinimumWidth(self.lbl_glider_vs.sizeHint().width())
        self.glider_row.addWidget(self.lbl_glider_vs)
        self.spn_glider_vs = QSpinBox()
        self.spn_glider_vs.setRange(self.GLIDER_VS_MIN_FPM, self.GLIDER_VS_MAX_FPM)
        self.spn_glider_vs.setSingleStep(self.GLIDER_VS_STEP_FPM)
        self.spn_glider_vs.setValue(self.GLIDER_VS_DEFAULT_FPM)
        self.spn_glider_vs.setSuffix(" fpm")
        self.spn_glider_vs.setToolTip(self.lbl_glider_vs.toolTip())
        self.spn_glider_vs.valueChanged.connect(self._on_glider_vs_changed)
        self.glider_row.addWidget(self.spn_glider_vs)
        self.glider_row.addStretch(1)
        root.addLayout(self.glider_row)
        self._is_glider = False
        self._set_glider_row_visible(False)

        # Debug-only controls: the aircraft's trim write settings + per-run
        # diagnostic trace, for problem-aircraft reports. Visible when debug
        # mode is active by EITHER route: the registry 'debug' flag or the
        # session Debug menu summoned with Alt+D — so a remote tester can be
        # talked through enabling the trace without editing the registry.
        # Evaluated at construction; the dialog destroys on close, so Alt+D
        # followed by reopening the tool picks it up.
        #
        # The trim write settings are the aircraft's TRIMWHEEL-scope settings
        # (the same rows a trimwheel owner edits on the Settings tab) and are
        # ALWAYS honored by a run, debug or not — an aircraft that needs axis
        # / custom-variable trim writes ships them in defaults.xml. The debug
        # controls only expose them for editing.
        debug_flag = bool(getattr(G, "system_settings", None)
                          and G.system_settings.get("debug", False))
        main_menu = getattr(getattr(G, "main_window", None), "menu", None)
        debug_menu_active = bool(main_menu) and any(
            a.text() == "Debug" for a in main_menu.actions())
        self._debug = debug_flag or debug_menu_active
        self.chk_trace = None
        self.chk_tw_use_axis = None
        self._tw_last_var = None
        if self._debug:
            debug_row = QHBoxLayout()
            # MSFS-only trim write settings, in their own container so the
            # whole group hides for other sims while the trace stays.
            self._tw_box = QFrame()
            tw_lay = QHBoxLayout(self._tw_box)
            tw_lay.setContentsMargins(0, 0, 0, 0)
            lbl_trim_method = InfoLabel(
                text="Trim write:",
                tooltip=(
                    "Debug options — this row is visible when debug mode is "
                    "active: either the Debug Mode system setting, or the Debug "
                    "menu shown with Alt+D (reopen this dialog after pressing "
                    "it).\n\n"
                    "Trim write: how calibration commands the sim's elevator "
                    "trim (MSFS). These are this aircraft's TRIM WHEEL settings\n"
                    "(saved under the trim wheel device, active profile) — the "
                    "calibration always uses them, whether or not this row is "
                    "shown.\n"
                    "• Direct (default) — writes the ELEVATOR TRIM POSITION SimVar "
                    "itself; the most reliable method across tested aircraft.\n"
                    "• Use Axis — sends AXIS_ELEV_TRIM_SET, or the custom Y "
                    "variable scaled to its ± range, for aircraft whose systems "
                    "ignore direct trim writes.\n\n"
                    "Record diagnostic trace: writes a per-frame CSV of everything "
                    "the calibration commands and observes (trimcal_trace_*.csv in "
                    "the TelemFFB log folder) — attach it when reporting a problem "
                    "aircraft."))
            # InfoLabel's internal minimum is broken (width pinned to text
            # HEIGHT), so it truncates under squeeze — enforce content width.
            lbl_trim_method.setMinimumWidth(lbl_trim_method.sizeHint().width())
            tw_lay.addWidget(lbl_trim_method)
            self.chk_tw_use_axis = QCheckBox("Use Axis instead of Direct")
            self.chk_tw_use_axis.setToolTip(
                "Enable to use Axis control (vs direct trim surface manipulating)")
            self.chk_tw_use_axis.toggled.connect(
                lambda c: self._on_tw_bool("trimwheel_use_axis", c))
            tw_lay.addWidget(self.chk_tw_use_axis)
            self.chk_tw_invert = QCheckBox("Axis Invert")
            self.chk_tw_invert.setToolTip(
                "Enable if trim wheel axis moves the wrong way")
            self.chk_tw_invert.toggled.connect(
                lambda c: self._on_tw_bool("trimwheel_axis_invert", c))
            tw_lay.addWidget(self.chk_tw_invert)
            self.chk_tw_custom = QCheckBox("Custom Variable")
            self.chk_tw_custom.setToolTip(
                'Custom Simconnect Variable - use "VARNAME" for simvars or '
                '"L:VARNAME" for LVARS')
            self.chk_tw_custom.toggled.connect(
                lambda c: self._on_tw_bool("enable_custom_y_axis", c))
            tw_lay.addWidget(self.chk_tw_custom)
            debug_row.addWidget(self._tw_box)
            # The trace rides the Control response row (before its stretch),
            # so it stays put while the MSFS-only trim write group hides.
            self.chk_trace = QCheckBox("Record diagnostic trace")
            self.chk_trace.setChecked(False)
            self.chk_trace.setToolTip(
                "Write a per-frame CSV of everything the calibration commands and\n"
                "observes to the TelemFFB log folder (trimcal_trace_*.csv).\n"
                "Attach it when reporting a problem aircraft.")
            response_row.insertSpacing(response_row.count() - 1, 12)
            response_row.insertWidget(response_row.count() - 1, self.chk_trace)
            debug_row.addStretch(1)
            root.addLayout(debug_row)

            # Custom-variable details: shown only while Use Axis AND Custom Y
            # Variable are on (the defaults.xml prereq chain).
            self._tw_custom_w = QFrame()
            cv_lay = QHBoxLayout(self._tw_custom_w)
            cv_lay.setContentsMargins(0, 0, 0, 0)
            cv_lay.addSpacing(20)
            cv_lay.addWidget(QLabel("Trim Axis Var:"))
            self.cmb_tw_var = NoWheelComboBox()
            self.cmb_tw_var.setEditable(True)
            self.cmb_tw_var.addItems(list(self.TW_VAR_CHOICES))
            self.cmb_tw_var.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToContents)
            self.cmb_tw_var.setMinimumWidth(180)
            self.cmb_tw_var.setToolTip(self.chk_tw_custom.toolTip())
            self.cmb_tw_var.activated.connect(
                lambda _i: self._on_tw_var_committed())
            self.cmb_tw_var.lineEdit().editingFinished.connect(
                self._on_tw_var_committed)
            cv_lay.addWidget(self.cmb_tw_var)
            cv_lay.addSpacing(10)
            cv_lay.addWidget(QLabel("+/- Range:"))
            self.cmb_tw_range = NoWheelComboBox()
            self.cmb_tw_range.addItems(list(self.TW_RANGE_CHOICES))
            self.cmb_tw_range.setToolTip("+/- Value Range to send to sim")
            self.cmb_tw_range.activated.connect(
                lambda _i: self._on_tw_setting(
                    "raw_y_axis_scale", self.cmb_tw_range.currentText()))
            cv_lay.addWidget(self.cmb_tw_range)
            cv_lay.addStretch(1)
            root.addWidget(self._tw_custom_w)
            for w in (self.chk_tw_invert, self.chk_tw_custom, self._tw_custom_w):
                w.setVisible(False)

        # ---- guided calibration controls ----
        # The action pair sits directly above the status cluster —
        # left-justified, with deliberate distance between the go button and
        # Abort so neither is pressed reaching for the other.
        wizard_row = QHBoxLayout()
        # No tooltip: the instructions, phase line, and step lamps carry the
        # flow now — a tooltip restating them was one more redundant surface.
        self.btn_wizard = QPushButton("Begin Calibration")
        self.btn_wizard.clicked.connect(self._on_wizard)
        # QPushButton's minimum hint is smaller than its text (Qt clips the
        # label under squeeze) — pin the minimum to the WIDEST label the
        # button morphs through so state changes never jiggle the layout.
        widest = 0
        for txt in ("Begin Calibration", "Start Trim Sweep",
                    "Preparing…", "Settling…", "Measuring…"):
            self.btn_wizard.setText(txt)
            widest = max(widest, self.btn_wizard.sizeHint().width())
        self.btn_wizard.setText("Begin Calibration")
        self.btn_wizard.setMinimumWidth(widest)
        wizard_row.addWidget(self.btn_wizard)
        wizard_row.addSpacing(10)
        self.chk_auto_sweep = QCheckBox("Start sweep automatically when steady")
        self.chk_auto_sweep.setToolTip(
            "Begin the trim sweep as soon as speed and trim hold steady,\n"
            "instead of waiting for Start Trim Sweep to be pressed.")
        self.chk_auto_sweep.setChecked(bool(G.system_settings.get(
            "TrimCalAutoSweep", False)))
        self.chk_auto_sweep.toggled.connect(self._on_auto_sweep_toggled)
        wizard_row.addWidget(self.chk_auto_sweep)
        # Abort at the far edge — deliberate distance from the go button.
        wizard_row.addStretch(1)
        self.btn_stop = QPushButton("Abort")
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setMinimumWidth(self.btn_stop.sizeHint().width())
        wizard_row.addWidget(self.btn_stop)
        root.addLayout(wizard_row)

        # ---- live status ----
        status_box = QGroupBox("Live status")
        grid = QGridLayout(status_box)

        def _hline():
            ln = QFrame()
            ln.setFrameShape(QFrame.Shape.HLine)
            ln.setStyleSheet(f"color: {DIVIDER_GRAY};")
            return ln

        # Row order (user-designed): suggested speeds, the separated flight
        # values, the status lines, sweep progress, then the three step
        # lamps at the bottom of the box.
        suggest_row = QHBoxLayout()
        self.lbl_suggest_name = InfoLabel(
            text="Suggested calibration speeds (IAS):",
            tooltip=(
                "Indicated airspeeds derived from the aircraft's declared\n"
                "speed envelope: low = 1.3 × clean stall; high estimated\n"
                "from design cruise (MSFS, corrected to indicated airspeed)\n"
                "or just below the green arc (X-Plane), capped below the\n"
                "red-line; plus a midpoint on wide envelopes.\n\n"
                "Suggestions only — there is no requirement to calibrate\n"
                "them all. The LOW and HIGH speeds matter most.\n"
                "A struck-out speed already has a stored calibration\n"
                "within ±5 kt."))
        self.lbl_suggest_name.setTextStyleSheet("QLabel { color: gray; }")
        suggest_row.addWidget(self.lbl_suggest_name)
        self.lbl_suggest = QLabel()
        self.lbl_suggest.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_suggest.setStyleSheet("QLabel { color: gray; }")
        suggest_row.addWidget(self.lbl_suggest)
        suggest_row.addStretch(1)
        # Fixed footprint: the row keeps its height while hidden (no
        # suggestions / offline) so showing it never reflows the box.
        for w in (self.lbl_suggest_name, self.lbl_suggest):
            sp = w.sizePolicy()
            sp.setRetainSizeWhenHidden(True)
            w.setSizePolicy(sp)
        grid.addLayout(suggest_row, 0, 0, 1, 5)
        self._set_suggest_visible(False)

        grid.addWidget(_hline(), 1, 0, 1, 5)

        self.lbl_ias = QLabel("—")
        self.lbl_pitch = QLabel("—")
        self.lbl_vs = QLabel("—")
        self.lbl_bank = QLabel("—")
        self.lbl_trim = QLabel("—")
        # IAS gets a live trend arrow beside the value (MSFS creeps toward
        # its new equilibrium speed for a minute-plus after a power change;
        # the arrow shows the assistant is waiting on physics, not wedged).
        self.ias_trend = IasTrendWidget()
        # VS gets the same arrow against the band the current ready gate
        # accepts.
        self.vs_target = VsTargetWidget()
        arrows = {self.lbl_ias: self.ias_trend, self.lbl_vs: self.vs_target}
        widest = {self.lbl_ias: "888.8 kt", self.lbl_vs: "+8888 fpm"}
        gap = 3
        for col, (name, w) in enumerate([
            ("IAS", self.lbl_ias), ("VS", self.lbl_vs), ("Pitch", self.lbl_pitch),
            ("Bank", self.lbl_bank), ("Trim", self.lbl_trim),
        ]):
            grid.addWidget(QLabel(f"<b>{name}</b>"), 2, col, alignment=Qt.AlignmentFlag.AlignHCenter)
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            if w in arrows:
                # The value is right-aligned in a fixed-width box with the
                # arrow just after it, and a matching space before it keeps
                # the value centered under its heading. Changing text or a
                # redrawn arrow shifts nothing.
                w.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                w.setMinimumWidth(w.fontMetrics().horizontalAdvance(widest[w]))
                cell = QHBoxLayout()
                cell.setContentsMargins(0, 0, 0, 0)
                cell.setSpacing(0)
                cell.addStretch(1)
                cell.addSpacing(arrows[w].width() + gap)
                cell.addWidget(w)
                cell.addSpacing(gap)
                cell.addWidget(arrows[w])
                cell.addStretch(1)
                grid.addLayout(cell, 3, col)
            else:
                grid.addWidget(w, 3, col)
        # Equal columns regardless of content width, or the whole row
        # re-negotiates as values change length.
        for col in range(5):
            grid.setColumnStretch(col, 1)

        grid.addWidget(_hline(), 4, 0, 1, 5)

        # Phase line: the light + one factual sentence — readiness when
        # idle, the current phase while running. Detail line: the engine's
        # own status_message feed, unabridged (VS tolerances, station
        # numbers, ...). Both word-wrap (per-frame text must never force
        # the window wider); the detail reserves two text lines so an
        # occasional wrap cannot bounce the layout below it.
        self.lbl_ready = QLabel("●  —")
        self.lbl_ready.setWordWrap(True)
        self.lbl_ready.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Two text lines reserved: a wrap must change what's shown, never
        # the geometry around it.
        self.lbl_ready.setMinimumHeight(2 * self.lbl_ready.fontMetrics().lineSpacing())
        grid.addWidget(self.lbl_ready, 5, 0, 1, 5)
        self.lbl_detail = QLabel("")
        self.lbl_detail.setStyleSheet("QLabel { color: gray; }")
        self.lbl_detail.setWordWrap(True)
        self.lbl_detail.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.lbl_detail.setMinimumHeight(2 * self.lbl_detail.fontMetrics().lineSpacing())
        grid.addWidget(self.lbl_detail, 6, 0, 1, 5)

        # Progress is meaningful only during the sweep (step 3): shown there
        # with its real meaning (station count), hidden otherwise so a dead
        # bar never reads as "stuck".
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        # Fixed footprint while hidden — appearing at sweep time must not
        # push the lamps/result area down.
        sp = self.progress.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.progress.setSizePolicy(sp)
        grid.addWidget(self.progress, 7, 0, 1, 5)

        # Step lamps (bottom of the box): the three steps as equal-width
        # annunciator cells — the active one lit in the who-acts color
        # (blue = automatic, amber = user action, green = armed/complete),
        # completed steps become green checks.
        lamp_row = QHBoxLayout()
        self._tracker_cells = []
        self._tracker_cache = [None, None, None]
        for _ in range(3):
            c = QLabel()
            c.setTextFormat(Qt.TextFormat.RichText)
            c.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            c.setStyleSheet(f"QLabel {{ border: 1px solid {BORDER_GRAY};"
                            " border-radius: 3px; padding: 4px 6px; }")
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._tracker_cells.append(c)
            lamp_row.addWidget(c, 1)
        grid.addLayout(lamp_row, 8, 0, 1, 5)
        root.addWidget(status_box)

        # Warning banner between the status cluster and the results, shown
        # only while the engine is flying the aircraft. Space is reserved
        # while hidden so a run starting/stopping never reflows the form.
        self.banner = QLabel("⚠  TelemFFB is controlling your aircraft — stay ready to take over")
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setStyleSheet(
            f"QLabel {{ background-color:{DANGER_BANNER_BG}; color:white; font-weight:bold;"
            " border-radius:6px; padding:5px; }"
        )
        self.banner.setVisible(False)
        sp = self.banner.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.banner.setSizePolicy(sp)
        root.addWidget(self.banner)

        # ---- result ----
        # Collapsible with the same disclosure language as the instructions,
        # so a cramped screen can trade the result area for the help text
        # and back. NOT persisted, unlike the instructions: the result frame
        # is the dialog's payload, so every open starts expanded, and
        # _show_result force-expands it - a fresh curve and its Save
        # decision must never land in a hidden pane.
        self.btn_result = QToolButton()
        self.btn_result.setCheckable(True)
        self.btn_result.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_result.setStyleSheet(disclosure_style)
        self.btn_result.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_result.clicked.connect(self._apply_result_state)
        root.addWidget(self.btn_result)

        self.result_box = result_box = QGroupBox()
        rlay = QVBoxLayout(result_box)
        # Which aircraft this dialog (and any result on it) belongs to —
        # live telemetry name, or the matched settings pattern when the sim
        # is offline. Plain text: aircraft names are arbitrary strings.
        self.lbl_aircraft = QLabel("<b>Aircraft:</b> —")
        # Rich text for the bold prefix; the aircraft NAME is an arbitrary
        # string, so _update_aircraft_label must html-escape it.
        self.lbl_aircraft.setTextFormat(Qt.TextFormat.RichText)
        rlay.addWidget(self.lbl_aircraft)
        self.curve = TrimCurveWidget()
        # Expanding + a modest minimum makes the graph the flexible element:
        # a tall notes block shrinks it rather than overlapping it.
        self.curve.setMinimumHeight(200)
        self.curve.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.curve.curve_context_requested.connect(self._on_curve_context)
        rlay.addWidget(self.curve)

        # Stored-family manager: page through the calibrated speeds (all are
        # ghosted on the plot, the selected one highlighted), delete
        # individually, or clear the set. Destructive actions are view-only
        # gated (offline editing / active run).
        # Compact flat controls (QToolButton escapes the app's chunky
        # QPushButton theming) — same visual language as the instructions
        # disclosure: hand cursor + hover pill for affordance.
        _flat = ("QToolButton { border: none; padding: 2px 6px; }"
                 "QToolButton:hover { background-color: rgba(42, 127, 212, 38);"
                 " border-radius: 4px; }"
                 f"QToolButton:disabled {{ color: {DISABLED_GRAY}; }}")

        def _tool_btn(text=None, icon=None, tooltip=None):
            b = QToolButton()
            if icon is not None:
                b.setIcon(icon)
                b.setIconSize(QtCore.QSize(20, 20))
            if text is not None:
                b.setText(text)
            if tooltip:
                b.setToolTip(tooltip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_flat)
            return b

        # Paging arrows: text glyphs (arrow-type glyph size is style-locked)
        # at a larger size in VPForce purple, translucent-purple hover pill.
        _arrow = (f"QToolButton {{ border: none; padding: 0px 6px;"
                  f" color: {vpf_purple}; font-size: 13pt; font-weight: bold; }}"
                  f"QToolButton:hover {{ background-color: {PURPLE_HOVER_FILL};"
                  f" border-radius: 4px; }}"
                  f"QToolButton:disabled {{ color: {DISABLED_GRAY}; }}")

        fam_row = QHBoxLayout()
        fam_row.addWidget(QLabel("Stored speeds:"))
        self.btn_fam_prev = _tool_btn(text="◀",
                                      tooltip="Previous stored calibration")
        self.btn_fam_prev.setStyleSheet(_arrow)
        self.btn_fam_prev.clicked.connect(lambda: self._step_family(-1))
        fam_row.addWidget(self.btn_fam_prev)
        self.cmb_family = NoWheelComboBox()
        # Speed-only entries are short: size to content (the 170px minimum
        # from the dated-entry era kept the row wide after the dates left).
        self.cmb_family.setMinimumWidth(90)
        self.cmb_family.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_family.currentIndexChanged.connect(self._on_family_selected)
        fam_row.addWidget(self.cmb_family)
        self.btn_fam_next = _tool_btn(text="▶",
                                      tooltip="Next stored calibration")
        self.btn_fam_next.setStyleSheet(_arrow)
        self.btn_fam_next.clicked.connect(lambda: self._step_family(+1))
        fam_row.addWidget(self.btn_fam_next)
        fam_row.addSpacing(10)
        self.btn_fam_delete = _tool_btn(
            icon=svg_icon("delete.svg"),
            tooltip="Delete the selected calibration "
                    "(or right-click a curve on the plot)")
        self.btn_fam_delete.clicked.connect(self._on_family_delete)
        fam_row.addWidget(self.btn_fam_delete)
        self.btn_fam_clear = _tool_btn(
            icon=svg_icon("delete-all.svg"),
            tooltip="Delete all stored calibrations")
        self.btn_fam_clear.clicked.connect(self._on_family_clear)
        fam_row.addWidget(self.btn_fam_clear)
        fam_row.addSpacing(6)
        fam_sep = QFrame()
        fam_sep.setFrameShape(QFrame.Shape.VLine)
        fam_sep.setFrameShadow(QFrame.Shadow.Sunken)
        fam_row.addWidget(fam_sep)
        fam_row.addSpacing(6)
        self.btn_fam_export = _tool_btn(
            icon=svg_icon("file-download.svg"),
            tooltip="Export this aircraft's stored calibrations to a file "
                    "another user can import")
        self.btn_fam_export.clicked.connect(self._on_family_export)
        fam_row.addWidget(self.btn_fam_export)
        self.btn_fam_import = _tool_btn(
            icon=svg_icon("file-upload.svg"),
            tooltip="Import a shared calibration file for this aircraft "
                    "(replaces the stored set)")
        self.btn_fam_import.clicked.connect(self._on_family_import)
        fam_row.addWidget(self.btn_fam_import)
        fam_row.addStretch(1)
        rlay.addLayout(fam_row)

        # Trimmed-stick-position mode: an airframe description, adjustable
        # post-calibration and persisted immediately (no Save required).
        pos_row = QHBoxLayout()
        lbl_stick_pos = InfoLabel(
            text="Trimmed stick position:",
            tooltip=(
                "Where the stick rests when the aircraft is trimmed, once\n"
                "calibrations at multiple speeds are stored.\n\n"
                "Follows Trim — the rest position rides the measured trim\n"
                "state (aft when trimmed slow, forward when fast), like\n"
                "cable/trim-tab aircraft where the yoke is linked to the\n"
                "control surface.\n\n"
                "Stays Centered — trimmed flight always rests the stick at\n"
                "center, like moving-stabilizer, FBW or spring-cartridge\n"
                "aircraft where trim re-rigs the feel datum. Jets default\n"
                "to this.\n\n"
                "Force behavior is identical in both modes (trimmed = zero\n"
                "force, out-of-trim = force); only the resting geometry\n"
                "differs. Changes apply and save immediately."))
        lbl_stick_pos.setMinimumWidth(lbl_stick_pos.sizeHint().width())
        pos_row.addWidget(lbl_stick_pos)
        self.cmb_stick_pos = NoWheelComboBox()
        self.cmb_stick_pos.addItems(list(self.STICK_POSITION_MODES))
        self.cmb_stick_pos.currentTextChanged.connect(self._on_stick_pos_changed)
        pos_row.addWidget(self.cmb_stick_pos)
        pos_row.addStretch(1)
        rlay.addLayout(pos_row)

        self.lbl_virtual = QLabel("Recommended Y Trim Gain (Virtual): <b>—</b>")
        self.lbl_linearity = QLabel("Linearity (R²): —")
        # Force rich text so entities (e.g. &nbsp;) render regardless of whether
        # a given text branch happens to contain HTML tags for auto-detection.
        self.lbl_virtual.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_linearity.setTextFormat(Qt.TextFormat.RichText)
        # The recommendation line grows long (curve + static fit + current
        # value); wrap it instead of pushing past the window edge.
        self.lbl_virtual.setWordWrap(True)
        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet(f"QLabel {{ color:{WARNING_TEXT_AMBER}; }}")
        rlay.addWidget(self.lbl_virtual)
        rlay.addWidget(self.lbl_linearity)
        rlay.addWidget(self.lbl_note)
        root.addWidget(result_box)
        self.btn_result.setChecked(True)
        self._apply_result_state(True)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)

        # ---- result / session actions (Abort lives with the wizard button) ----
        btns = QHBoxLayout()
        btns.addStretch(1)

        self.btn_apply = QPushButton("Apply (test in sim)")
        self.btn_apply.setToolTip("Apply the value live for a fly-test without saving it to the profile")
        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip("Add this result to the aircraft's stored calibrations "
                                 "and turn on the calibrated trim curve")
        self.btn_close = QPushButton("Close")
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_close.clicked.connect(self.close)
        btns.addWidget(self.btn_apply)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_close)
        # Same clip-under-squeeze protection as the wizard button: pin each
        # bottom-row button's minimum to its content so the layout minimum
        # (and thus the window's) accounts for their full labels.
        for b in (self.btn_apply, self.btn_save, self.btn_close):
            b.setMinimumWidth(b.sizeHint().width())
        root.addLayout(btns)

    # ---- telemetry plumbing -------------------------------------------------

    def _connect_telemetry(self):
        if not self._telem_connected and G.telem_manager is not None:
            G.telem_manager.telemetryReceived.connect(self._on_telemetry)
            G.telem_manager.telemetryTimeout.connect(self._on_timeout)
            self._telem_connected = True

    def _disconnect_telemetry(self):
        if not self._telem_connected:
            return
        try:
            G.telem_manager.telemetryReceived.disconnect(self._on_telemetry)
            G.telem_manager.telemetryTimeout.disconnect(self._on_timeout)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self._telem_connected = False

    def _calibrator(self):
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        if ac is None or not hasattr(ac, "get_trim_calibrator"):
            return None
        return ac.get_trim_calibrator()

    @staticmethod
    def _offline_editing():
        return G.settings_mgr is not None and \
            getattr(G.settings_mgr, "offline_mode", False)

    @staticmethod
    def _offline_target_valid():
        """True when the offline editor has an actual aircraft selected.
        Entering offline mode RETAINS the online aircraft's identity fields
        and leaves offline_scope None until a selection is made — in that
        limbo, offline writes silently hit the scope-match fall-through and
        persist NOTHING, so nothing write-shaped may be offered."""
        sm = G.settings_mgr
        return bool(sm and getattr(sm, "offline_scope", None)
                    and sm.current_sim and sm.current_aircraft_name)

    def _offline_settings(self):
        """Prereq-filtered settings of the profile selected in the OFFLINE
        editor, as {name: value} with units applied (the same to_number
        conversion the live pipeline uses), or None when not in offline
        editing mode. While editing offline, the live aircraft — and any
        result sitting on its calibrator — belongs to whatever was flying
        before offline mode was entered; never display state from it."""
        if not self._offline_editing():
            return None
        if not self._offline_target_valid():
            # Offline with nothing selected: the identity fields still hold
            # the ONLINE aircraft — reading through them would display (and
            # invite edits against) an aircraft the offline editor is not
            # actually targeting. Present a clean empty state instead.
            return {}
        sm = G.settings_mgr
        try:
            _, _, result = xmlutils.read_single_model(
                sm.current_sim, sm.current_aircraft_name, sm.current_class,
                G.device_type, active_profile=sm.active_profile)
            return {i["name"]: utils.to_number(f"{i['value']}{i['unit'] or ''}")
                    for i in result if i.get("value") not in (None, "-")}
        except Exception as e:
            logger.warning(f"Offline settings read failed: {e}")
            return None

    def _update_aircraft_label(self):
        """Aircraft identity for the result box: the offline editor's
        selection while editing offline, else the live telemetry name with
        the matched settings pattern as fallback."""
        sm = G.settings_mgr
        if self._offline_editing():
            if self._offline_target_valid():
                name = sm.current_aircraft_name or sm.current_pattern or "—"
                text = f"<b>Aircraft:</b> {html.escape(name)}  <i>(offline editor)</i>"
            else:
                text = "<b>Aircraft:</b> —  <i>(offline editor — no aircraft selected)</i>"
        else:
            name = getattr(G.telem_manager, "currentAircraftName", None) \
                if G.telem_manager else None
            if not name:
                name = getattr(sm, "current_pattern", None) if sm else None
            text = f"<b>Aircraft:</b> {html.escape(name)}" if name \
                else "<b>Aircraft:</b> —"
        if self.lbl_aircraft.text() != text:
            self.lbl_aircraft.setText(text)

    def _update_tf_warning(self):
        """Show/hide the trim-following-disabled banner for the current
        aircraft. When Axis Control is off, trim following is prereq-blocked
        with it, so the message names the real first step (the Trim Following
        row is not even visible on the Settings tab until Axis Control is
        enabled)."""
        if self._offline_editing():
            # Judge the EDITED profile, not the stale live aircraft. Prereq
            # filtering drops trim_following when axis control is off, so
            # "missing" reads as disabled — the same semantics the live
            # aircraft would have. With NO aircraft selected there is
            # nothing to judge: no banner (an empty settings dict would
            # otherwise read as "axis control disabled" — misleading).
            if not self._offline_target_valid():
                self.warn_tf.setVisible(False)
                return
            vals = self._offline_settings()
            show = (G.device_type == "joystick" and vals is not None
                    and vals.get("trim_following") is not True)
            axis_off = vals is None or \
                vals.get("telemffb_controls_axes") is not True
        else:
            ac = G.telem_manager.currentAircraft if G.telem_manager else None
            show = (G.device_type == "joystick"
                    and ac is not None and hasattr(ac, "get_trim_calibrator")
                    and not getattr(ac, "trim_following", False))
            axis_off = show and not getattr(ac, "telemffb_controls_axes", False)
        if show:
            if axis_off:
                text = (
                    "⚠  AXIS CONTROL IS DISABLED for this aircraft\n"
                    "Calibration can run and save, but the result has NO effect "
                    "in flight. Enable Axis Control, then Trim Following, on the "
                    "Settings tab.")
            else:
                text = (
                    "⚠  TRIM FOLLOWING IS DISABLED for this aircraft\n"
                    "Calibration can run and save, but the result has NO effect "
                    "in flight until Trim Following is enabled on the Settings "
                    "tab.")
            if self.warn_tf.text() != text:
                self.warn_tf.setText(text)
        if show != self.warn_tf.isVisible():
            self.warn_tf.setVisible(show)
            if show:
                self._refit()

    # ---- slots --------------------------------------------------------------

    def _on_timeout(self, timed_out):
        if timed_out:
            self._refresh_idle()

    def _on_telemetry(self, data):
        try:
            self._sim_paused_seen = bool(data.get("SimPaused"))
            cal = self._calibrator()

            # The calibrator lives on the aircraft instance; a different object
            # means a new aircraft was loaded — drop the previous aircraft's
            # displayed result (its own result re-displays if it has one).
            if cal is not None and id(cal) != self._cal_seen:
                self._cal_seen = id(cal)
                self._reset_display(cal)

            # Live-tracked (not just at open): enabling trim following on the
            # Settings tab re-applies the aircraft params, and the banner
            # should clear the moment it does.
            self._update_tf_warning()
            self._update_aircraft_label()

            ias_ref = getattr(cal, "_ias0", None) if (cal is not None and cal.active) else None
            self._update_live_values(data, ias_ref)
            self._update_vs_target(data, cal)

            if G.device_type != "joystick":
                self._set_ready(False, "Run from the joystick (master) instance")
                self._set_running(False)
                self._apply_wizard("Begin Calibration", False)
                self._set_tracker(0)
                self._set_detail("")
                self._set_progress(False)
                return
            if cal is None:
                self._set_ready(False, "Load an MSFS / X-Plane aircraft")
                self._set_running(False)
                self._apply_wizard("Begin Calibration", False)
                self._set_tracker(0)
                self._set_detail("")
                self._set_progress(False)
                return

            running = cal.active
            assist_holding = running and cal.state == CalState.ASSIST_HOLD
            state_name = getattr(cal.state, "name", "")
            # A new run owns the graph: whatever the display held (the
            # stored-family view after a save, a previous result's zoom)
            # must not leave its ghosts/axes under the live points — the
            # live marker walks straight off a view fitted to old data.
            # Rising-edge here catches EVERY start path (wizard, sweep
            # handoff, calibrator recreation mid-session).
            if running and not self._was_running:
                self.curve.clear()
                self._clear_result_labels()
                self._stored_curve_seen = None
            self._was_running = running
            self._set_running(running)
            # One narrative cluster: the wizard button walks the guided flow,
            # the tracker shows where in it we are (color = who acts), the
            # phase line states the current fact, and the detail line carries
            # the engine's unabridged status feed. While the assistant holds,
            # the button is the sweep trigger, gated on the assistant's own
            # stability determination (starting mid-transient is a guaranteed
            # abort; the hysteresis lives engine-side, next to the data).
            if assist_holding:
                if getattr(cal, "assist_stable", False):
                    if self.chk_auto_sweep.isChecked():
                        cal.begin_sweep()
                    self._apply_wizard("Start Trim Sweep", True)
                    self._set_tracker(2, self.COL_OK)
                    ias_kt = (data.get("IAS") or 0) * 1.94384
                    self._set_light(self.COL_OK,
                                    f"Steady at {ias_kt:.0f} kt — sweep ready")
                else:
                    self._apply_wizard("Settling…", False)
                    self._set_tracker(2, self.COL_ACTION)
                    self._set_light(self.COL_ACTION,
                                    "Holding level — set power for the target "
                                    "airspeed; the sweep arms when speed and "
                                    "trim are steady")
            elif running:
                step = self.TRACKER_STEP_OF_STATE.get(state_name, 1)
                if step == 1:
                    self._apply_wizard("Preparing…", False)
                    self._set_tracker(1, self.COL_AUTO)
                    self._set_light(self.COL_AUTO,
                                    "TelemFFB is flying — leveling and finding "
                                    "the neutral trim point")
                else:
                    self._apply_wizard("Measuring…", False)
                    self._set_tracker(3, self.COL_AUTO)
                    self._set_light(self.COL_AUTO,
                                    "Confirming a steady airspeed before the sweep"
                                    if state_name == "SPEED_SETTLE" else
                                    "Sweeping the trim range"
                                    if state_name == "SWEEP" else
                                    "Computing the curve and restoring trim")
            if running:
                self._set_detail(cal.status_message or "")
                if state_name in ("SWEEP", "SOLVE", "RESTORE"):
                    max_st = getattr(cal, "SWEEP_MAX_STATIONS", 15)
                    n = len(getattr(cal, "_samples", []) or [])
                    fmt = (f"station {min(n + 1, max_st)} of ~{max_st}"
                           if state_name == "SWEEP" else "finishing…")
                    self._set_progress(True, int(cal.progress * 100), fmt)
                else:
                    self._set_progress(False)
            # Idle: tracker/phase/button applied in the not-running branch
            # below (can_start decides).

            # Run options lock while a run is active — they are read once at
            # start and a mid-run change would silently do nothing. The
            # control-response combo is the exception: it applies live up
            # until the sweep locks the measurement dynamics.
            state_name = getattr(cal.state, "name", "")
            self.cmb_response.setEnabled(
                not running or state_name in self.RESPONSE_LIVE_STATES)
            self._set_trim_write_enabled(not running)
            if self.chk_trace is not None:
                self.chk_trace.setEnabled(not running)

            # Glider descent control: visible only for engineless aircraft.
            # Live-adjustable through the hold — with the controls taken, the
            # sink target is the ONLY way a glider user can pick a different
            # test speed (no throttle; the polar couples speed to sink).
            # Locked from the sweep onward, same rule as Control response.
            self._is_glider = self._current_is_glider(data)
            self._set_glider_row_visible(self._is_glider)
            self.spn_glider_vs.setEnabled(
                self._is_glider and
                (not running or state_name in self.RESPONSE_LIVE_STATES))

            if running:
                # Family edits and the position mode lock during a run — the
                # engine read its state at start. Re-enabled by the next
                # stored-curve/result render's _update_family_ui.
                self.btn_fam_delete.setEnabled(False)
                self.btn_fam_clear.setEnabled(False)
                self.cmb_stick_pos.setEnabled(False)
                self.curve.set_live_point(data.get("ElevTrimPct"), getattr(cal, "_u_elev", None))
                # Show accepted stations as they land (full-scale view; the
                # zoom-to-fit happens once at completion in _show_result).
                self.curve.set_samples(list(getattr(cal, "_samples", []) or []))
            else:
                # Keep the engine's held-VS target current so the live "ready"
                # gate (can_start) judges a descending glider against its
                # target sink, not against level.
                cal.vs_target = self._glider_vs_target_ms()
                ok, msg = cal.can_start(data)
                self._set_ready(ok, msg)
                self._apply_wizard("Begin Calibration", ok)
                # Tracker: a fresh unsaved result keeps all three checks
                # (orientation: the run finished); anything else idles gray.
                self._set_tracker(0, complete=(
                    cal.state == CalState.DONE and cal.result is not None))
                self._set_detail("")
                self._set_progress(False)
                # The aircraft's applied curve can change while the dialog is
                # open (enabling trim following applies a previously
                # prereq-blocked curve; the config pipeline only runs on
                # telemetry frames). Redraw the stored-curve view when the
                # raw JSON changes — string compare per frame, parse only on
                # change. DONE/ABORT keep their own displays (fresh result /
                # abort post-mortem).
                if cal.state not in (CalState.DONE, CalState.ABORT):
                    ac = G.telem_manager.currentAircraft if G.telem_manager else None
                    raw = getattr(ac, "joystick_trim_follow_curve_y", None) if ac is not None else None
                    if raw != self._stored_curve_seen:
                        self.curve.clear()
                        self._clear_result_labels()
                        self._show_stored_curve()
                self._update_speed_suggestions(data)

            if cal.state == CalState.DONE and cal.result and not self._result_shown:
                self._show_result(cal.result)
            elif cal.state == CalState.ABORT and not running and cal.abort_reason:
                self.lbl_note.setText(f"Last run aborted: {cal.abort_reason}")
        except Exception as e:  # never let a UI slot break the telemetry stream
            logger.error(f"TrimCalibrationDialog telemetry update error: {e}")

    def _reset_display(self, cal):
        """Reset all result display state; re-show the (new) calibrator's own
        completed result when it has one, else the aircraft's saved curve."""
        self._last_result = None
        self._result_shown = False
        self.curve.clear()
        self._clear_result_labels()
        # Aircraft changed: last aircraft's speed suggestions are stale;
        # the next live frame recomputes them for the new one.
        self._set_suggest_visible(False)
        # New aircraft: show ITS trim write settings.
        self._sync_trim_write_widgets()
        if cal is not None and cal.active:
            # A run is in progress on this engine: the live view owns the
            # graph — re-rendering the stored family here would put its
            # zoom/ghosts under the run's live points.
            return
        if cal is not None and cal.state == CalState.DONE and cal.result:
            self._show_result(cal.result)
        else:
            self._show_stored_curve()

    def _show_stored_curve(self):
        """Display the STORED calibration family (view-only): every entry
        ghosted on the plot, the selected one highlighted with its stats.

        The stored entries are runtime offsets ``offs(T)``; the plot shows
        measured-axis space, so they are mirrored back for display. In
        offline editing mode everything comes from the EDITED profile's XML
        (through the same shared parser), never the stale live aircraft.
        Returns True when something was shown.
        """
        vals = self._offline_settings()
        if vals is not None:
            raw = vals.get("joystick_trim_follow_curve_y")
            use_curve = vals.get("joystick_trim_follow_use_curve_y") is True
            stick_pos = vals.get("joystick_trim_follow_stick_position")
        else:
            ac = G.telem_manager.currentAircraft if G.telem_manager else None
            raw = getattr(ac, "joystick_trim_follow_curve_y", None) if ac is not None else None
            use_curve = bool(getattr(ac, "joystick_trim_follow_use_curve_y", False))
            stick_pos = getattr(ac, "joystick_trim_follow_stick_position", None) \
                if use_curve else None
        if stick_pos not in self.STICK_POSITION_MODES:
            stick_pos = self._resolved_stick_position()
        # Record what this display attempt was based on, drawn or not, so the
        # live change check in _on_telemetry knows when a redraw is due.
        self._stored_curve_seen = raw
        self._sync_stick_pos_combo(stick_pos)
        self._load_family_from_raw(raw)
        self._update_family_ui()
        if not self._fam:
            return False
        entry = self._fam[self._fam_sel]
        self.curve.set_family(
            [[(t, -o) for t, o in zip(e["xs"], e["ys"])] for e in self._fam])
        samples = [(t, -o) for t, o in zip(entry["xs"], entry["ys"])]
        # No fit line here: the static gain is dormant in curve mode, and a
        # stale tangent among several ghosted curves reads as noise.
        self.curve.set_result(samples, 0.0, 0.0, show_fit=False)
        # Slope and R² re-fit from the stored points: the mirror-and-shift
        # from measured samples to stored offsets is affine in y, so R² (and
        # |slope|) reproduce the original run's fit exactly — works for every
        # curve ever saved, no payload change. Reuses the calibrator's own
        # fit routine.
        fit_slope, _, r2 = TrimCalibrator._fit(list(zip(entry["xs"], entry["ys"])))
        # Glider provenance: the sink held while measuring, folded into the
        # speed part ("65.0 kt @ 100 fpm descent").
        vs_fpm = entry.get("vs_fpm")
        speed_txt = f"{entry['ias_kt']:.1f} kt" if entry.get("ias_kt") else ""
        if speed_txt and vs_fpm:
            speed_txt += (f" @ {abs(vs_fpm):.0f} fpm "
                          + ("descent" if vs_fpm < 0 else "climb"))
        prov = " · ".join(str(x) for x in [
            speed_txt,
            f"captured {entry['date']}" if entry.get("date") else "",
        ] if x)
        n = len(self._fam)
        self.lbl_virtual.setText(
            f"Mean Trim Slope: <b>{-fit_slope:+.2f}</b>"
            f"  &nbsp;·  stored calibration {self._fam_sel + 1} of {n}"
            + (f"  ({prov})" if prov else ""))
        self.lbl_linearity.setText(
            f"Linearity (R²): {r2:.3f}  &nbsp;·  curve in use: "
            + ("yes" if use_curve else
               "no — disabled on the Settings tab (static gain active)"))
        self._fit_to_content()
        return True

    # ---- stored-family manager ----------------------------------------------

    def _load_family_from_raw(self, raw):
        """Populate the index-aligned raw/parsed family lists from the stored
        setting value. Each raw entry is probe-parsed individually through
        the shared family parser, so display indices always match what a
        delete/save round-trips back to the profile."""
        entries = []
        if raw and raw != "none":
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                for e in data.get("curves", [data]):
                    parsed = utils.parse_trim_follow_family(e)
                    if parsed:
                        entries.append((float(e.get("ias_kt") or 0.0), e, parsed[0]))
            except (ValueError, TypeError, AttributeError) as err:
                logger.warning(f"Stored trim curve unreadable; not displaying ({err})")
        entries.sort(key=lambda x: x[0])
        self._fam_raw = [e[1] for e in entries]
        self._fam = [e[2] for e in entries]
        self._fam_sel = max(0, min(self._fam_sel, len(self._fam) - 1)) \
            if self._fam else 0

    def _update_family_ui(self):
        n = len(self._fam)
        self.cmb_family.blockSignals(True)
        self.cmb_family.clear()
        for e in self._fam:
            # Speed only — the capture date is in the stats line below.
            self.cmb_family.addItem(f"{e['ias_kt']:.1f} kt")
        if n:
            self.cmb_family.setCurrentIndex(self._fam_sel)
        self.cmb_family.blockSignals(False)
        cal = self._calibrator()
        running = bool(cal is not None and cal.active)
        editable = n > 0 and not running and not self._offline_editing()
        self.cmb_family.setEnabled(n > 0)
        self.btn_fam_prev.setEnabled(n > 0 and self._fam_sel > 0)
        self.btn_fam_next.setEnabled(n > 0 and self._fam_sel < n - 1)
        self.btn_fam_delete.setEnabled(editable)
        self.btn_fam_clear.setEnabled(editable)
        # Export = read-only, works everywhere something is stored. Import
        # deliberately works in OFFLINE mode too (install a shared file for
        # an aircraft that is not loaded) — but only against a real offline
        # SELECTION: with none, offline writes hit the scope fall-through
        # and silently persist nothing.
        self.btn_fam_export.setEnabled(n > 0)
        self.btn_fam_import.setEnabled(
            not running and (not self._offline_editing()
                             or self._offline_target_valid()))
        self.cmb_stick_pos.setEnabled(not running and not self._offline_editing())

    def _step_family(self, delta):
        if self._fam:
            self.cmb_family.setCurrentIndex(
                max(0, min(self._fam_sel + delta, len(self._fam) - 1)))

    def _on_family_selected(self, index):
        if index < 0 or index == self._fam_sel:
            return
        self._fam_sel = index
        self._clear_result_labels()
        self.curve.clear()
        self._show_stored_curve()

    def _apply_family_live(self, raw_entries):
        """Live-set an edited family on the aircraft so the change acts (and
        displays) immediately — the XML write follows via result_saved, and
        the config pipeline re-confirms it on the next telemetry frame."""
        if self._offline_editing():
            return
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        if ac is None:
            return
        ac.joystick_trim_follow_curve_y = \
            json.dumps({"curves": raw_entries}) if raw_entries else "none"
        ac.joystick_trim_follow_use_curve_y = bool(raw_entries)

    def _emit_family_edit(self, raw_entries):
        self._apply_family_live(raw_entries)
        self.result_saved.emit(json.dumps(
            {"curves": raw_entries, "use_curve": bool(raw_entries)}))
        self._clear_result_labels()
        self.curve.clear()
        self._show_stored_curve()

    def _on_family_delete(self):
        if not self._fam:
            return
        e = self._fam[self._fam_sel]
        what = (f"the {e['ias_kt']:.1f} kt calibration"
                + (f" (captured {e['date']})" if e.get("date") else ""))
        if len(self._fam) == 1:
            # Escalate: the last one also turns curve mode off.
            msg = (f"Delete {what}?\n\n"
                   "This is the LAST stored calibration for this aircraft — "
                   "deleting it disables curve mode (the static gain applies) "
                   "until you calibrate again.")
        else:
            msg = f"Delete {what}?"
        q = QMessageBox.question(self, "Delete calibration", msg)
        if q != QMessageBox.StandardButton.Yes:
            return
        remaining = [r for i, r in enumerate(self._fam_raw) if i != self._fam_sel]
        logger.info(f"Trim calibration deleted: {what} "
                    f"({len(remaining)} remaining)")
        self._fam_sel = max(0, self._fam_sel - 1)
        self._emit_family_edit(remaining)

    def _on_family_clear(self):
        if not self._fam:
            return
        q = QMessageBox.question(
            self, "Clear calibrations",
            f"Delete all {len(self._fam)} stored calibration(s) for this "
            "aircraft?\n\nThis disables curve mode (the static gain applies) "
            "until you calibrate again.")
        if q != QMessageBox.StandardButton.Yes:
            return
        logger.info(f"Trim calibrations cleared: all {len(self._fam)} removed")
        self._fam_sel = 0
        self._emit_family_edit([])

    def _on_family_export(self):
        """Write the stored calibration set to a shareable JSON file.

        Portable payload = curves + stick-position mode only. The static
        virtual_y deliberately stays home: it is entangled with the
        exporter's physical-gain setting, while the curves are
        physical-gain-independent by design (absolute axis units)."""
        if not self._fam_raw:
            return
        sm = G.settings_mgr
        vals = self._offline_settings()
        if vals is not None:
            stick = vals.get("joystick_trim_follow_stick_position")
        else:
            ac = G.telem_manager.currentAircraft if G.telem_manager else None
            stick = getattr(ac, "joystick_trim_follow_stick_position", None) \
                if ac is not None and getattr(ac, "joystick_trim_follow_use_curve_y", False) else None
        if stick not in self.STICK_POSITION_MODES:
            stick = self._resolved_stick_position()
        payload = {
            "type": self.EXPORT_TYPE,
            "version": 1,
            "sim": getattr(sm, "current_sim", "") if sm else "",
            "aircraft_name": getattr(sm, "current_aircraft_name", "") if sm else "",
            "pattern": getattr(sm, "current_pattern", "") if sm else "",
            "exported": time.strftime("%Y-%m-%d"),
            "stick_position": stick if stick in self.STICK_POSITION_MODES else None,
            "curves": self._fam_raw,
        }
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_",
                      payload["pattern"] or payload["aircraft_name"]
                      or "aircraft").strip("_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export trim calibrations",
            f"trimcal_{safe}_{payload['exported']}.json",
            "TelemFFB Trim Calibration (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        n = len(self._fam_raw)
        logger.info(f"Trim calibrations exported: {n} speed(s) for "
                    f"'{payload['pattern'] or payload['aircraft_name']}' -> {path}")
        self.lbl_note.setText(
            f"Exported {n} stored speed{'s' if n != 1 else ''} to "
            f"{os.path.basename(path)}.")

    def _on_family_import(self):
        """Import a shared calibration set, replacing the stored family.

        Identity check (user-specified): the match string is the profile key
        and the authority — the incoming pattern must equal the active one,
        and the exporter's aircraft NAME must regex-match the ACTIVE match
        string (liveries differ; the pattern unifies them). Mismatches warn
        with full detail and allow a deliberate override."""
        cal = self._calibrator()
        if cal is not None and cal.active:
            return
        if self._offline_editing() and not self._offline_target_valid():
            return  # backstop: offline writes without a selection go nowhere
        path, _ = QFileDialog.getOpenFileName(
            self, "Import trim calibrations", "",
            "TelemFFB Trim Calibration (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import failed",
                                f"Could not read the file:\n{e}")
            return
        if not isinstance(data, dict) or data.get("type") != self.EXPORT_TYPE \
                or "curves" not in data:
            QMessageBox.warning(self, "Import failed",
                                "Not a TelemFFB trim-calibration export file.")
            return
        fam = utils.parse_trim_follow_family({"curves": data["curves"]})
        if not fam:
            QMessageBox.warning(self, "Import failed",
                                "The file contains no usable calibration curves.")
            return

        sm = G.settings_mgr
        cur_sim = getattr(sm, "current_sim", "") if sm else ""
        cur_pattern = getattr(sm, "current_pattern", "") if sm else ""
        name = data.get("aircraft_name") or ""
        problems = []
        if data.get("sim") and cur_sim and data["sim"] != cur_sim:
            problems.append(f"Sim: file is for {data['sim']}, active is {cur_sim}")
        if data.get("pattern") != cur_pattern:
            problems.append(
                f"Match string: file has '{data.get('pattern')}', "
                f"active is '{cur_pattern}'")
        try:
            if cur_pattern and not re.match(cur_pattern, name):
                problems.append(
                    f"Aircraft name '{name}' does not match the active "
                    f"match string '{cur_pattern}'")
        except re.error:
            pass  # unmatchable pattern: the equality check above governs

        n_in = len(fam)
        speeds = ", ".join(f"{e['ias_kt']:.0f} kt" for e in fam)
        summary = (f"{n_in} calibrated speed{'s' if n_in != 1 else ''} "
                   f"({speeds})\nAircraft: {name or '?'}\n"
                   f"Match string: {data.get('pattern') or '?'}"
                   + (f"\nExported: {data['exported']}"
                      if data.get("exported") else ""))
        if problems:
            q = QMessageBox.warning(
                self, "Calibration does not match this aircraft",
                "This file does not appear to belong to the active aircraft "
                "profile:\n\n- " + "\n- ".join(problems)
                + f"\n\n{summary}\n\nImport anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
        else:
            have = len(self._fam_raw)
            q = QMessageBox.question(
                self, "Import calibrations",
                summary
                + (f"\n\nThis replaces the {have} currently stored "
                   f"speed{'s' if have != 1 else ''}." if have else "")
                + "\n\nImport?")
        if q != QMessageBox.StandardButton.Yes:
            return

        payload = {"curves": data["curves"], "use_curve": True}
        if data.get("stick_position") in self.STICK_POSITION_MODES:
            payload["stick_position"] = data["stick_position"]
            if not self._offline_editing():
                ac = G.telem_manager.currentAircraft if G.telem_manager else None
                if ac is not None:
                    ac.joystick_trim_follow_stick_position = data["stick_position"]
            self._sync_stick_pos_combo(data["stick_position"])
        logger.info(f"Trim calibrations imported: {n_in} speed(s) ({speeds}) "
                    f"from {os.path.basename(path)}"
                    + (" [identity mismatch overridden]" if problems else ""))
        self._apply_family_live(list(data["curves"]))
        self.result_saved.emit(json.dumps(payload))
        self._clear_result_labels()
        self.curve.clear()
        self._show_stored_curve()
        self.lbl_note.setText(
            f"Imported {n_in} calibrated speed{'s' if n_in != 1 else ''} "
            f"from {os.path.basename(path)}.")

    def _on_curve_context(self, index):
        """Right-click on a ghosted family curve: context-menu delete.

        Routed through the selector's Delete flow (selection + confirmation),
        so every gate — offline view-only, active run — is inherited from
        the Delete button's enabled state."""
        if index >= len(self._fam) or not self.btn_fam_delete.isEnabled():
            return
        e = self._fam[index]
        menu = QMenu(self)
        act = menu.addAction(f"Delete the {e['ias_kt']:.1f} kt calibration…")
        if menu.exec(QCursor.pos()) == act:
            self._fam_sel = index
            self._on_family_delete()

    def _set_suggest_visible(self, visible):
        self.lbl_suggest_name.setVisible(visible)
        self.lbl_suggest.setVisible(visible)

    def _set_glider_row_visible(self, visible):
        self.lbl_glider_vs.setVisible(visible)
        self.spn_glider_vs.setVisible(visible)

    def _current_is_glider(self, data=None):
        """True when the active aircraft is a glider (no engine). Reads the
        live telemetry class, falling back to the aircraft's last frame."""
        cls = data.get("AircraftClass") if data is not None else None
        if cls is None:
            ac = G.telem_manager.currentAircraft if G.telem_manager else None
            td = getattr(ac, "telem_data", None) if ac is not None else None
            cls = td.get("AircraftClass") if td is not None else None
        return cls == "GliderAircraft"

    def _glider_vs_target_ms(self):
        """Sink target (m/s) the calibrator should hold for this run: the
        glider spinbox value when the glider row applies, else 0.0 (level)."""
        if self._is_glider:
            return self.spn_glider_vs.value() / self.MS_TO_FPM
        return 0.0

    def _update_speed_suggestions(self, data):
        """Refresh the passive suggested-speeds line from live telemetry;
        stored-covered speeds render struck-through."""
        sugg = utils.suggest_calibration_speeds(
            data, getattr(G.settings_mgr, "current_sim", "") if G.settings_mgr else "")
        if not sugg:
            if self.lbl_suggest.isVisible():
                self._set_suggest_visible(False)
            return
        parts = []
        for s in sugg:
            covered = any(abs(e["ias_kt"] - s) <= self.FAMILY_REPLACE_KT
                          for e in self._fam)
            parts.append(f"<s>{s}</s>" if covered else f"<b>{s}</b>")
        text = " &nbsp;·&nbsp; ".join(parts) + " &nbsp;kt"
        if self.lbl_suggest.text() != text:
            self.lbl_suggest.setText(text)
        if not self.lbl_suggest.isVisible():
            self._set_suggest_visible(True)
            self._refit()

    def _resolved_stick_position(self):
        """The trimmed stick position the aircraft resolves to once curve
        mode is on.  The setting hangs under the curve toggle, so the live
        aircraft only carries it after a calibration exists; before that
        the class default lives in the XML alone."""
        sm = G.settings_mgr
        if sm is None or not getattr(sm, "current_aircraft_name", ""):
            return None
        if self._offline_editing() and not self._offline_target_valid():
            return None
        try:
            _, _, rows = xmlutils.read_single_model(
                sm.current_sim, sm.current_aircraft_name, sm.current_class,
                G.device_type, active_profile=sm.active_profile, apply_prereqs=False)
        except Exception as e:
            logger.warning(f"Stick position read failed: {e}")
            return None
        for row in rows:
            if row.get("name") == "joystick_trim_follow_stick_position":
                value = row.get("value")
                return value if value in self.STICK_POSITION_MODES else None
        return None

    def _sync_stick_pos_combo(self, value):
        want = value if value in self.STICK_POSITION_MODES \
            else self.STICK_POSITION_MODES[0]
        if self.cmb_stick_pos.currentText() != want:
            self.cmb_stick_pos.blockSignals(True)
            self.cmb_stick_pos.setCurrentText(want)
            self.cmb_stick_pos.blockSignals(False)

    def _on_stick_pos_changed(self, text):
        # An airframe description, not a run result: apply live and persist
        # immediately — usable post-calibration without a fresh run. The
        # combo is disabled in offline mode (view-only), so no gating here
        # beyond the live aircraft's presence.
        if self._offline_editing():
            return
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        if ac is not None:
            ac.joystick_trim_follow_stick_position = text
        self.position_mode_changed.emit(text)

    def _instructions_natural_height(self):
        """Unscrolled height of the help text at the pane's current width."""
        w = self.scroll_instructions.viewport().width()
        if w <= 0:
            # Pre-show the viewport has no real geometry yet; the deferred
            # _refit pass re-measures once it does.
            w = max(self.width() - 24, 100)
        return self.lbl_instructions.heightForWidth(w)

    def _instructions_min_height(self):
        # Below a few visible lines a scrolled help pane stops being
        # readable; past this floor the window grows off-screen instead
        # (the pre-fix behavior).
        return 4 * self.lbl_instructions.fontMetrics().lineSpacing()

    def _layout_height(self):
        lay = self.layout()
        lay.activate()
        if lay.hasHeightForWidth():
            return lay.totalHeightForWidth(self.width())
        return self.sizeHint().height()

    def _size_instructions_pane(self, budget_h):
        """Set the help pane's height for a window height budget: natural
        (unscrolled) when the whole layout fits within ``budget_h``, shrunk
        (its scrollbar appears) to absorb the overflow when it does not,
        floored at a few readable lines. Returns the layout height the
        window then needs at the current width."""
        natural = self._instructions_natural_height()
        self.scroll_instructions.setFixedHeight(natural)
        needed = self._layout_height()
        if needed > budget_h:
            give = min(needed - budget_h,
                       max(natural - self._instructions_min_height(), 0))
            if give > 0:
                self.scroll_instructions.setFixedHeight(natural - give)
                needed = self._layout_height()
        return needed

    def _fit_to_content(self, allow_shrink=False):
        """Grow (never shrink, unless asked) the window so the content cannot
        overlap, keeping it on the screen.

        Word-wrapped labels make the layout height-for-width: a plain
        sizeHint() under-reports the needed height until a later layout pass,
        which shows up as text overlapping the graph that fixes itself when
        the window is moved. Force a layout pass and measure with the
        height-for-width machinery at the current width instead.

        The help pane is the one elastic element (_size_instructions_pane):
        when the full-content window would not fit the screen the overflow
        is taken out of the pane. The window is never clamped below what
        the remaining fixed-height content needs - squeezing the layout
        under its minimum is what causes widget overlap - so with the pane
        at its floor (or collapsed) the window can still exceed a very
        small screen, matching the pre-clamp behavior.

        ``allow_shrink`` is for deliberate content removal (collapsing the
        instructions) - the one case where handing space back beats leaving
        a gap. A window already taller than the screen may always shrink
        to content: no user chose that height on purpose, and honoring it
        is exactly how the dialog used to walk off the bottom edge.
        """
        avail = self.screen().availableGeometry()
        overhead = self.frameGeometry().height() - self.height()
        if overhead <= 0:
            overhead = 40   # frame not realized yet (pre-show): estimate
        max_h = avail.height() - overhead

        if self.btn_instructions.isChecked():
            needed = self._size_instructions_pane(max_h)
        else:
            needed = self._layout_height()

        shrink_ok = allow_shrink or self.height() > max_h
        if self.height() < needed or (shrink_ok and self.height() > needed):
            self.resize(self.width(), needed)

        # Growth can push the bottom edge off a window the user parked low;
        # slide it up (title bar stays reachable via the avail.top() floor).
        over = self.frameGeometry().bottom() - avail.bottom()
        if over > 0:
            self.move(self.x(), max(avail.top(), self.y() - over))

    def _refit(self, allow_shrink=False):
        """Fit now and once more on the next event-loop tick: the deferred
        pass re-measures after Qt has finished the pending relayout (the same
        settling a window move used to trigger by accident)."""
        self._fit_to_content(allow_shrink)
        QtCore.QTimer.singleShot(0, lambda: self._fit_to_content(allow_shrink))

    def _on_instructions_toggled(self, checked):
        self._apply_instructions_state(checked)
        # Persist per device, same store as the main window geometry.
        G.system_settings.setValue(
            f"{G.device_type}/TrimCalInstructionsCollapsed", not checked)

    def _on_auto_sweep_toggled(self, checked):
        G.system_settings.setValue(f"{G.device_type}/TrimCalAutoSweep", checked)

    def _apply_instructions_state(self, expanded):
        self.scroll_instructions.setVisible(expanded)
        self.btn_instructions.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.btn_instructions.setText(
            "How to use  (click to close)" if expanded
            else "How to use  (click to expand)")
        self.btn_instructions.setToolTip(
            "Hide the instructions" if expanded else "Show the instructions")
        self._refit(allow_shrink=not expanded)

    def _apply_result_state(self, expanded):
        self.result_box.setVisible(expanded)
        self.btn_result.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.btn_result.setText(
            "Result  (click to hide)" if expanded
            else "Result  (click to show)")
        self.btn_result.setToolTip(
            "Hide the result area" if expanded else "Show the result area")
        self._refit(allow_shrink=not expanded)

    def _update_live_values(self, data, ias_ref=None):
        def fmt(v, conv=1.0, unit="", nd=0, signed=False):
            if v is None:
                return "—"
            # + 0.0 turns a rounded -0 into 0, so it never reads "-0"
            v = round(v * conv, nd) + 0.0
            if not signed:
                return f"{v:.{nd}f}{unit}"
            # U+2212 minus is as wide as the plus; a hyphen is narrower
            return f"{v:+.{nd}f}".replace("-", "−") + unit

        # Live airspeed-drift warning during a run: the measured slope is only
        # trustworthy at roughly constant speed, so surface drift as it happens
        # rather than only in the post-run note.
        ias = data.get("IAS")
        # Trend arrow: windowed rates (never a per-frame derivative — that
        # amplifies telemetry jitter ~30x, a lesson the engine learned
        # first). Two windows: FAST for responsiveness, SLOW because the
        # assistant responds to ACCUMULATED drift — a creep far below any
        # instantaneous-rate threshold still walks the natural trim past
        # the retrim tolerance within a minute. The arrow shows whichever
        # window sees more (slow creep stays visible); the static dash
        # requires BOTH quiet, so a green dash genuinely means no retrim
        # is brewing.
        rate, static = None, False
        if ias is not None:
            now = time.monotonic()
            self._ias_hist.append((now, ias))
            while self._ias_hist and now - self._ias_hist[0][0] > 12.5:
                self._ias_hist.pop(0)
            span = now - self._ias_hist[0][0]
            fast = slow = None
            recent = [s for s in self._ias_hist if now - s[0] <= 2.5]
            if recent and now - recent[0][0] >= 1.0:
                fast = (ias - recent[0][1]) / (now - recent[0][0]) * MS_TO_KT
            if span >= 4.0:
                slow = (ias - self._ias_hist[0][1]) / span * MS_TO_KT
            if fast is not None:
                rate = fast if slow is None or abs(fast) >= abs(slow) else slow
                static = abs(fast) <= 0.08 and slow is not None and abs(slow) <= 0.03
        else:
            self._ias_hist.clear()
        self.ias_trend.set_rate(rate, static)
        # Tenths: during the assistant hold the user is WATCHING the speed
        # settle toward the readiness gate; whole knots hide the trend the
        # gate reacts to (power-user field request).
        ias_txt = fmt(ias, MS_TO_KT, " kt", nd=1)
        ias_style = ""
        if ias_ref and ias:
            drift = (ias - ias_ref) / ias_ref
            if abs(drift) >= 0.05:
                ias_txt += f" ({drift * 100:+.0f}%)"
                color = ERROR_RED if abs(drift) >= 0.10 else WARNING_TEXT_AMBER
                ias_style = f"QLabel {{ color:{color}; font-weight:bold; }}"
        self.lbl_ias.setText(ias_txt)
        self.lbl_ias.setStyleSheet(ias_style)
        self.lbl_pitch.setText(fmt(data.get("Pitch"), 1.0, "°", nd=1, signed=True))
        self.lbl_vs.setText(fmt(data.get("VerticalSpeed"), MS_TO_FPM, " fpm", signed=True))
        self.lbl_bank.setText(fmt(data.get("Roll"), 1.0, "°", nd=1, signed=True))
        self.lbl_trim.setText(fmt(data.get("ElevTrimPct"), 100.0, "%", signed=True))

    def _update_vs_target(self, data, cal):
        """Point the VS arrow at the band the current ready gate accepts:
        the start gate while idle, the level-hold gate during a run."""
        vs = data.get("VerticalSpeed")
        if cal is None or vs is None or G.device_type != "joystick":
            self.vs_target.set_deviation(None)
            return
        if cal.active:
            target, band = cal.vs_target, cal.STABLE_VS_TOL
        else:
            target, band = self._glider_vs_target_ms(), cal.START_MAX_VS
        self.vs_target.set_deviation((vs - target) * MS_TO_FPM, band * MS_TO_FPM)

    # ---- button handlers ----------------------------------------------------

    def _on_wizard(self):
        """The single guided-flow button: begins the assisted run when idle,
        hands off to the sweep while the assistant holds steady. (The direct
        no-assistant start is engine/tests-only now — assisted runs proved
        strictly better in the field, so the UI offers one path.)"""
        cal = self._calibrator()
        if cal is None:
            return
        if cal.active and cal.state == CalState.ASSIST_HOLD:
            # The button is gated on the assistant's own stability
            # determination (assist_stable).
            cal.begin_sweep()
            return
        if not cal.active:
            self._arm_and_start(cal, assist=True)

    def _apply_wizard(self, label, enabled):
        """Apply the wizard button state in ONE transition per property.

        Runs per telemetry frame — a disable→re-enable pulse clears a
        QPushButton's in-progress press, silently swallowing user clicks
        (the bug the old gated Start button had), so every property is
        change-guarded."""
        if self.btn_wizard.text() != label:
            self.btn_wizard.setText(label)
        if self.btn_wizard.isEnabled() != enabled:
            self.btn_wizard.setEnabled(enabled)

    def _set_tracker(self, active, color=None, complete=False, failed=False):
        """Render the 3-step lamps (change-guarded per cell).

        ``active`` 1-3 lights that step in ``color`` (who-acts color); steps
        before it become green checks. 0 = all pending (idle). ``complete``
        checks all three; ``failed`` marks the active step red.
        """
        for i, name in enumerate(self.TRACKER_STEPS, start=1):
            digit = "①②③"[i - 1]
            if complete or (active and i < active):
                html = f"<span style='color:{self.COL_OK}'>✓ {name}</span>"
            elif i == active and failed:
                html = f"<b><span style='color:{self.COL_BAD}'>{digit} {name}</span></b>"
            elif i == active:
                html = f"<b><span style='color:{color}'>{digit} {name}</span></b>"
            else:
                html = f"<span style='color:gray'>{digit} {name}</span>"
            if html != self._tracker_cache[i - 1]:
                self._tracker_cache[i - 1] = html
                self._tracker_cells[i - 1].setText(html)

    def _set_detail(self, text):
        if self.lbl_detail.text() != text:
            self.lbl_detail.setText(text)

    def _set_progress(self, visible, value=None, fmt=None):
        if self.progress.isVisible() != visible:
            self.progress.setVisible(visible)
        if value is not None:
            self.progress.setValue(value)
        if fmt is not None and self.progress.format() != fmt:
            self.progress.setFormat(fmt)

    def _on_response_changed(self, index):
        # Live control-response change on a running loop (up to the sweep;
        # the combo is disabled from the sweep onward). Idle changes are
        # picked up by _arm_and_start at the next start as before.
        cal = self._calibrator()
        if cal is not None and cal.active:
            cal.set_gain_scale(self.RESPONSE_SCALES.get(index, 1.0))

    def _on_glider_vs_changed(self, _value):
        # Live sink-target change on a running loop — the glider's only way
        # to pick a different test speed once the assistant has the controls
        # (a steeper sink flies faster, a shallower one slower). The engine
        # reads vs_target every frame, so the hold simply chases the new
        # target and the ready gate re-arms when it settles. The spinbox is
        # disabled from the sweep onward (same rule as Control response), so
        # a change can never move a measurement in progress; idle changes are
        # pushed by the per-frame update and _arm_and_start.
        cal = self._calibrator()
        if cal is not None and cal.active and self.spn_glider_vs.isEnabled():
            cal.vs_target = self._glider_vs_target_ms()
            logger.info(f"Glider sink target changed live: "
                        f"{self.spn_glider_vs.value()} fpm")

    # ---- trim write settings (trimwheel scope) --------------------------------

    @staticmethod
    def _current_sim_is_msfs():
        sm = G.settings_mgr
        return sm is not None and getattr(sm, "current_sim", None) == "MSFS"

    def _read_trim_write_settings(self):
        """The current aircraft's trimwheel-scope trim write settings.

        Resolved through the full config cascade for the TRIMWHEEL device and
        the active profile (a profile is shared by every device), so the
        values are exactly those a trimwheel instance would fly with —
        including per-model rows shipped in defaults.xml. Prereq filtering
        drops rows whose prereq is off, which then read as their defaults.
        Returns :attr:`TRIM_WRITE_DEFAULTS` when there is no MSFS aircraft.
        """
        s = dict(self.TRIM_WRITE_DEFAULTS)
        sm = G.settings_mgr
        if not self._current_sim_is_msfs() or not sm.current_aircraft_name:
            return s
        if self._offline_editing() and not self._offline_target_valid():
            return s
        try:
            _, _, rows = xmlutils.read_single_model(
                sm.current_sim, sm.current_aircraft_name, sm.current_class,
                "trimwheel", active_profile=sm.active_profile)
        except Exception as e:
            logger.warning(f"Trim write settings read failed: {e}")
            return s
        for row in rows:
            name = row.get("name")
            raw = row.get("value")
            if name not in s or raw in (None, "-"):
                continue
            if name == "custom_y_axis":
                s[name] = str(raw).strip() or s[name]
            elif name == "raw_y_axis_scale":
                try:
                    s[name] = int(float(raw))
                except (TypeError, ValueError):
                    pass
            else:
                s[name] = str(raw).strip().lower() == "true"
        return s

    @staticmethod
    def _apply_trim_write(cal, s):
        """Set a calibrator's trim write run options from trimwheel settings,
        with the trimwheel mixin's semantics: invert and the custom variable
        only apply in axis mode."""
        use_axis = bool(s["trimwheel_use_axis"])
        custom = use_axis and bool(s["enable_custom_y_axis"])
        cal.trim_use_axis = use_axis
        cal.trim_axis_invert = use_axis and bool(s["trimwheel_axis_invert"])
        cal.trim_axis_var, cal.trim_axis_range = MsfsXpTrimwheelMixIn.trim_axis_target(
            custom, s["custom_y_axis"], s["raw_y_axis_scale"])
        logger.info(
            "Trim calibration write settings (trimwheel scope): "
            + (f"axis {cal.trim_axis_var} ±{cal.trim_axis_range}"
               f"{' inverted' if cal.trim_axis_invert else ''}"
               if use_axis else "direct"))

    def _sync_trim_write_widgets(self, s=None):
        """Show the aircraft's saved trim write settings in the debug row."""
        if self.chk_tw_use_axis is None:
            return
        if s is None:
            s = self._read_trim_write_settings()
        var = s["custom_y_axis"]
        rng = str(s["raw_y_axis_scale"])
        for w in (self.chk_tw_use_axis, self.chk_tw_invert, self.chk_tw_custom,
                  self.cmb_tw_var, self.cmb_tw_range):
            w.blockSignals(True)
        try:
            self.chk_tw_use_axis.setChecked(bool(s["trimwheel_use_axis"]))
            self.chk_tw_invert.setChecked(bool(s["trimwheel_axis_invert"]))
            self.chk_tw_custom.setChecked(bool(s["enable_custom_y_axis"]))
            if self.cmb_tw_var.findText(var) < 0:
                self.cmb_tw_var.addItem(var)
            self.cmb_tw_var.setCurrentText(var)
            if self.cmb_tw_range.findText(rng) < 0:
                self.cmb_tw_range.addItem(rng)
            self.cmb_tw_range.setCurrentText(rng)
        finally:
            for w in (self.chk_tw_use_axis, self.chk_tw_invert, self.chk_tw_custom,
                      self.cmb_tw_var, self.cmb_tw_range):
                w.blockSignals(False)
        self._tw_last_var = var
        self._update_trim_write_visibility()

    def _update_trim_write_visibility(self):
        """MSFS only; invert / custom variable follow Use Axis, the variable
        details follow Custom Y Variable (the defaults.xml prereq chain)."""
        if self.chk_tw_use_axis is None:
            return
        msfs = self._current_sim_is_msfs()
        use_axis = msfs and self.chk_tw_use_axis.isChecked()
        custom = use_axis and self.chk_tw_custom.isChecked()
        changed = False
        for w, vis in ((self._tw_box, msfs), (self.chk_tw_invert, use_axis),
                       (self.chk_tw_custom, use_axis), (self._tw_custom_w, custom)):
            if w.isVisibleTo(self) != vis:
                w.setVisible(vis)
                changed = True
        if changed and self.isVisible():
            self._refit()

    def _set_trim_write_enabled(self, enabled):
        """Editable only while idle and online — the offline editor view is
        read-only here, like the other airframe controls."""
        if self.chk_tw_use_axis is None:
            return
        enabled = enabled and not self._offline_editing()
        for w in (self._tw_box, self._tw_custom_w):
            if w.isEnabled() != enabled:
                w.setEnabled(enabled)

    def _on_tw_setting(self, name, value):
        if self._offline_editing() or not self._current_sim_is_msfs():
            return
        logger.info(f"Trim write setting {name} = {value} (trimwheel scope)")
        self.trim_write_setting_changed.emit(name, value)

    def _on_tw_bool(self, name, checked):
        self._on_tw_setting(name, "true" if checked else "false")
        self._update_trim_write_visibility()

    def _on_tw_var_committed(self):
        text = self.cmb_tw_var.currentText().strip()
        if not text or text == self._tw_last_var:
            return
        self._tw_last_var = text
        self._on_tw_setting("custom_y_axis", text)

    def _arm_and_start(self, cal, assist):
        ac = G.telem_manager.currentAircraft
        telem = getattr(ac, "telem_data", None)
        # Held vertical speed for this run: the glider sink target, else level.
        # Set before can_start so its VS gate judges against the same target.
        cal.vs_target = self._glider_vs_target_ms()
        ok, msg = cal.can_start(telem)
        if not ok:
            logger.info(f"Calibration start refused: {msg}")
            QMessageBox.warning(self, "Not ready", msg)
            return
        self._result_shown = False
        self._last_result = None
        self.curve.clear()
        self._clear_result_labels()
        cal.initial_gain_scale = self.RESPONSE_SCALES.get(
            self.cmb_response.currentIndex(), 1.0)
        # Trim write settings come from the aircraft's saved trimwheel-scope
        # config on EVERY run (read fresh — a Settings-tab edit since the
        # dialog opened must apply), debug controls shown or not.
        tw = self._read_trim_write_settings()
        self._apply_trim_write(cal, tw)
        self._sync_trim_write_widgets(tw)
        cal.trace_enabled = self._debug and self.chk_trace.isChecked()
        cal.start(assist=assist)

    def _on_stop(self):
        cal = self._calibrator()
        if cal is not None:
            cal.stop("Cancelled by user")

    def _merged_family(self, confirm=True):
        """Fold the fresh result into the stored family.

        Returns the merged raw entry list (speed-sorted), or None when the
        user declined replacing a near-duplicate speed. The stored state is
        re-read from the live aircraft at call time, so a save can't
        resurrect entries deleted after the run finished. ``confirm=False``
        computes the outcome without asking (prospective display note).
        """
        new = self._last_result.get("curve") if self._last_result else None
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        raw = getattr(ac, "joystick_trim_follow_curve_y", None) if ac is not None else None
        self._load_family_from_raw(raw)
        merged = list(self._fam_raw)
        if not new:
            # No-curve run: leave the stored family untouched — a failed or
            # degenerate run must never clobber good stored calibrations.
            return merged
        nv = float(new.get("ias_kt") or 0.0)
        for i, e in enumerate(merged):
            if abs(float(e.get("ias_kt") or 0.0) - nv) <= self.FAMILY_REPLACE_KT:
                if confirm:
                    q = QMessageBox.question(
                        self, "Replace calibration?",
                        f"Replace the {float(e.get('ias_kt') or 0):.1f} kt "
                        f"calibration"
                        + (f" (captured {e.get('date')})" if e.get("date") else "")
                        + f" with this new {nv:.1f} kt run?")
                    if q != QMessageBox.StandardButton.Yes:
                        return None
                merged[i] = new
                return merged
        merged.append(new)
        merged.sort(key=lambda e: float(e.get("ias_kt") or 0.0))
        return merged

    def _payload(self, merged):
        # Saving/applying a run that produced a curve always enables curve
        # mode; a no-curve run leaves the stored family untouched and curve
        # mode follows whether stored curves exist. The stick-position mode
        # rides along so Save captures the whole curve-mode state. The
        # legacy static virtual_y is NOT written anywhere anymore — the
        # curve is the product (the solver still computes the static fit
        # for display and logs only).
        return {
            "curves": merged,
            "use_curve": bool(merged),
            "stick_position": self.cmb_stick_pos.currentText(),
        }

    def _live_set_merged(self, p, merged):
        """Apply the merged family + companion settings to the live aircraft
        (shared by Apply and Save — Save must act immediately too, not wait
        for the telemetry-driven config pipeline)."""
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        if ac is None:
            return
        # Property setter parses the JSON once into lookup structures.
        ac.joystick_trim_follow_curve_y = \
            json.dumps({"curves": merged}) if merged else "none"
        ac.joystick_trim_follow_use_curve_y = p["use_curve"]
        ac.joystick_trim_follow_stick_position = p["stick_position"]

    def _on_apply(self):
        if self._last_result is None:
            return
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        if ac is not None:
            merged = self._merged_family()
            if merged is None:
                return  # user declined replacing the near-duplicate speed
            p = self._payload(merged)
            self._live_set_merged(p, merged)
            mode = "calibrated curve" if p["use_curve"] else "static gain"
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Applied for testing")
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setText(f"<b>Applied live ({mode})</b> — not yet saved to this "
                        "aircraft's profile.")
            box.setInformativeText(
                "<b>How to test it:</b>"
                "<ol>"
                "<li>Fly straight &amp; level and let the aircraft settle.</li>"
                "<li><b>Hold the stick still</b> in one spot and keep it there — "
                "this is the condition the value is tuned for.</li>"
                "<li>Slowly run elevator trim nose-up, then nose-down, across "
                "its range.</li>"
                "</ol>"
                "With a good value the <b>nose stays level</b> as you trim: the "
                "stick relieves under force feedback but the aircraft does not "
                "pitch. If the nose drifts up or down while you trim, the value "
                "needs work — re-run the calibration."
                "<br><br>Click <b>Save</b> to write this to the profile. Saving "
                "enables the calibrated curve; it can be disabled later from "
                "the Settings tab.")
            box.exec()

    def _on_save(self):
        """Save is a step in the multi-speed loop, not the end of it: fold
        the result into the family, persist, apply live, and return the
        dialog to the ready state showing the updated stored set — so the
        next speed is one Begin Calibration click away, no reopen needed."""
        if self._last_result is None:
            return
        merged = self._merged_family()
        if merged is None:
            return  # user declined replacing the near-duplicate speed
        p = self._payload(merged)
        self.result_saved.emit(json.dumps(p))
        self._live_set_merged(p, merged)
        # Consume the result: the display moves on to the stored family
        # (which now includes this run), and the idle refresh must not
        # resurrect the already-saved result view.
        self._last_result = None
        self._result_shown = True
        self._clear_result_labels()
        self.curve.clear()
        self._show_stored_curve()
        n = len(merged)
        self.lbl_note.setText(
            f"Saved — {n} stored speed{'s' if n != 1 else ''} for this "
            f"aircraft. Ready for the next run: press Begin Calibration, set "
            f"the next test speed, and calibrate again.")

    # ---- result / state display ---------------------------------------------

    def _show_result(self, result):
        # A fresh result must be seen: force the pane open even if the user
        # collapsed it earlier in the session.
        if not self.btn_result.isChecked():
            self.btn_result.setChecked(True)
            self._apply_result_state(True)
        self._result_shown = True
        self._last_result = result
        # Ghost the stored family behind the fresh result for context (and
        # so the prospective save note below matches what the plot shows).
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        self._load_family_from_raw(
            getattr(ac, "joystick_trim_follow_curve_y", None) if ac is not None else None)
        self._update_family_ui()
        self.curve.set_family(
            [[(t, -o) for t, o in zip(e["xs"], e["ys"])] for e in self._fam])
        self.curve.set_result(
            result["samples"], result["slope"], result["intercept"],
            flagged=[f["index"] for f in result.get("flagged") or []])
        self.curve.set_live_point(None, None)
        has_curve = result.get("curve") is not None
        if has_curve:
            # The curve is the product. The static gain is still written to
            # the profile but only acts if the user disables curve mode, so
            # headlining it was misleading — and it clamps at the setting
            # bound on steep aircraft, where the measured slope stays
            # honest. Mean slope is also the number every cross-run and
            # cross-speed comparison uses.
            self.lbl_virtual.setText(
                f"Mean Trim Slope: <b>{result['slope']:+.2f}</b>")
        else:
            current = result.get("current_virtual_y")
            current_txt = f"  &nbsp;·  current profile value: {current:.3f}" \
                if current is not None else ""
            self.lbl_virtual.setText(
                f"Recommended: static gain {result['virtual_y']:.3f}"
                f"  &nbsp;(for Physical Y = {result['physical_y']:.2f}){current_txt}")
        self.lbl_linearity.setText(f"Linearity (R²): {result['r_squared']:.3f}")
        notes = []
        # Prospective save outcome against the stored family (computed
        # without dialogs — the confirmation happens at Save/Apply).
        if has_curve:
            nv = float((result.get("curve") or {}).get("ias_kt") or 0.0)
            near = next((e for e in self._fam_raw
                         if abs(float(e.get("ias_kt") or 0.0) - nv)
                         <= self.FAMILY_REPLACE_KT), None)
            if near is not None:
                notes.append(
                    f"Saving will offer to replace the stored "
                    f"{float(near.get('ias_kt') or 0):.1f} kt calibration "
                    f"(within {self.FAMILY_REPLACE_KT:.0f} kt of this run).")
            elif self._fam_raw:
                notes.append(
                    f"Saving stores this as speed {len(self._fam_raw) + 1} — "
                    "the runtime blends the stored speeds by airspeed.")
        if not result["linear_ok"]:
            notes.append(
                "Response is non-linear — a single gain can't hold level across the whole "
                "trim range. This value is the best linear fit; a trim curve would fit better.")
        ias_drift = result.get("ias_drift", 0)
        if abs(ias_drift) > 0.05:
            notes.append(
                f"Airspeed drifted {ias_drift * 100:+.0f}% during the sweep, which can skew "
                "the measurement. The result may still be fine — test it with Apply, and if "
                "trim following seems off, re-run with steadier power and let the airspeed "
                "settle before the sweep starts.")
        flagged = result.get("flagged") or []
        if flagged:
            worst = max(abs(f["vs_fpm"]) for f in flagged)
            where = ", ".join(f"{100 * f['trim']:+.0f}%" for f in flagged)
            plural = "s" if len(flagged) > 1 else ""
            notes.append(
                f"{len(flagged)} station{plural} (trim {where}, shown in amber) sampled "
                f"with a residual climb/descent of up to {worst:.0f} fpm — usually slow "
                "airspeed drift. The curve may be slightly skewed near those points; if "
                "trim following seems off there, re-run with steadier power and let the "
                "airspeed settle before the sweep starts.")
        split = result.get("split")
        if split and split["mismatch"] > 0.2:
            notes.append(
                "Trim response is asymmetric — stronger on one side of neutral trim "
                f"than the other (equivalent static gains: {split['virtual_y_above']:.2f} "
                f"nose-up, {split['virtual_y_below']:.2f} nose-down). This is normal when "
                "the aircraft's nose-up and nose-down trim limits differ. The calibrated "
                "curve captures the asymmetry; the single static value is a compromise, so "
                "expect some residual pitch toward the trim extremes.")
        self.lbl_note.setText("\n".join(notes))
        self.btn_apply.setEnabled(True)
        self.btn_save.setEnabled(True)
        self._refit()

    def _clear_result_labels(self):
        self.lbl_virtual.setText("Mean Trim Slope: <b>—</b>")
        self.lbl_linearity.setText("Linearity (R²): —")
        self.lbl_note.setText("")
        self.btn_apply.setEnabled(False)
        self.btn_save.setEnabled(False)

    def _set_ready(self, ok, message):
        self._set_light(OK_GREEN if ok else ERROR_RED, message)

    def _set_light(self, color, message):
        self.lbl_ready.setText(
            f"<span style='color:{color}; font-size:15pt;'>●</span>  {message}")

    def _set_running(self, running):
        # Banner space is retained while hidden (fixed footprint), so
        # showing it never reflows the window — no refit needed.
        self.banner.setVisible(running)
        self.btn_stop.setEnabled(running)
        # The wizard button (label/enabled) is applied separately via
        # _apply_wizard — change-guarded there for the same click-swallowing
        # reason the old gated Start needed.
        if running:
            self.btn_apply.setEnabled(False)
            self.btn_save.setEnabled(False)

    def _refresh_idle(self):
        self._ias_hist.clear()
        self.ias_trend.set_rate(None)
        self.vs_target.set_deviation(None)
        self.lbl_ias.setText("—")
        self.lbl_pitch.setText("—")
        self.lbl_vs.setText("—")
        self.lbl_bank.setText("—")
        self.lbl_trim.setText("—")
        self._set_progress(False, value=0)
        self.banner.setVisible(False)
        self.btn_stop.setEnabled(False)
        self._apply_wizard("Begin Calibration", False)
        self._set_detail("")
        cal_pre = self._calibrator()
        self._set_tracker(0, complete=(
            cal_pre is not None and cal_pre.state == CalState.DONE
            and cal_pre.result is not None))
        self.cmb_response.setEnabled(True)
        self._set_trim_write_enabled(not (cal_pre is not None and cal_pre.active))
        self._sync_trim_write_widgets()
        if self.chk_trace is not None:
            self.chk_trace.setEnabled(True)
        cal = self._calibrator()
        self._update_tf_warning()
        self._update_aircraft_label()
        if self._offline_editing():
            # Offline editing: the live aircraft — and any result sitting on
            # its calibrator — belongs to whatever was flying before offline
            # mode was entered. Show the EDITED profile's stored curve,
            # view-only (no Apply/Save of a stale result into the wrong
            # profile, no starting runs without telemetry). _result_shown is
            # cleared so a live result re-displays after exiting offline.
            self._result_shown = False
            self._clear_result_labels()
            self.curve.clear()
            self._show_stored_curve()
            # Suggestions need live telemetry; the offline-edited aircraft's
            # envelope is not on the wire.
            self._set_suggest_visible(False)
            if self._offline_target_valid():
                self._set_ready(False, "Offline editing — run calibrations with a live sim")
            else:
                self._set_ready(False, "Offline editing — select an aircraft "
                                       "in the Offline Editor to view its calibrations")
            return
        if cal is not None and cal.active:
            # A telemetry BLIP mid-run lands here (timeout fires while the
            # engine is still active, and often resumes before the engine's
            # own lost-telemetry abort). The live run owns the graph:
            # rendering the stored family now would park its zoom/ghosts
            # under the run's points (field report: mid-sweep zoom jump).
            # Re-arm the rising edge so resumed frames re-clear the view.
            self._was_running = False
            return
        has_result = cal is not None and cal.state == CalState.DONE and cal.result is not None
        last_abort = cal is not None and cal.state == CalState.ABORT and cal.abort_reason
        if has_result and not self._result_shown:
            self._show_result(cal.result)
        elif last_abort:
            # Pausing the sim right after a failed run fires the telemetry
            # timeout — do NOT wipe the post-mortem (abort reason + the
            # partial-run plot): users pause precisely to read it and take
            # screenshots. The calibrator still holds the state; re-assert
            # the note in case this refresh follows a display reset.
            self.lbl_note.setText(f"Last run aborted: {cal.abort_reason}")
        elif not has_result:
            self._clear_result_labels()
            self._show_stored_curve()
        if G.device_type != "joystick":
            self._set_ready(False, "Run from the joystick (master) instance")
        elif cal is None:
            self._set_ready(False, "Load an MSFS / X-Plane aircraft")
        else:
            # No telemetry right now. If the last frame before the timeout
            # said the sim was paused, keep the specific message; otherwise
            # show the generic one. _on_telemetry replaces this with the live
            # can_start result once frames arrive.
            if self._sim_paused_seen:
                self._set_ready(False, "Unpause the simulator to calibrate")
            else:
                self._set_ready(False, "Waiting for telemetry — is the sim running?")

    # ---- lifecycle ----------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # A width change re-wraps the help text, changing its natural
        # height while the pane's set height stays frozen - re-derive it.
        # Deferred (resizing layout from inside resizeEvent invites a
        # feedback loop) and width-only, so _fit_to_content's own resizes
        # (height-only) and the user's height drags never re-enter here.
        if (self.btn_instructions.isChecked()
                and event.oldSize().width() != event.size().width()):
            self._repane_timer.start(0)

    def _repane(self):
        # Fit the pane to the CURRENT window height: the user may have just
        # chosen it with the mouse, so unlike _fit_to_content the window is
        # not resized.
        if self.btn_instructions.isChecked():
            self._size_instructions_pane(self.height())

    def showEvent(self, event):
        super().showEvent(event)
        self._connect_telemetry()
        self._refresh_idle()

    def closeEvent(self, event):
        # Safety: never leave the aircraft being flown by a hidden dialog.
        cal = self._calibrator()
        if cal is not None and cal.active:
            cal.stop("Calibration window closed")
        self._disconnect_telemetry()
        # Destroy rather than hide: a reopened dialog rebuilds its display from
        # the CURRENT aircraft's calibrator (completed results live on the
        # engine), so no stale state can survive an aircraft change.
        self.deleteLater()
        super().closeEvent(event)
