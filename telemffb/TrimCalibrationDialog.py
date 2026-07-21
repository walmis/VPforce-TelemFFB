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
    QComboBox, QToolButton, QMenu, QFileDialog,
)

import telemffb.globals as G
import telemffb.utils as utils
import telemffb.xmlutils as xmlutils
from telemffb.custom_widgets import (
    IasTrendWidget, InfoLabel, NoWheelComboBox, TrimCurveWidget, vpf_purple,
)
from telemffb.sim.msfs_xp.TrimCalibrator import CalState, TrimCalibrator

logger = logging.getLogger(__name__)

MS_TO_KT = 1.0 / 0.514444
MS_TO_FPM = 196.850394


class TrimCalibrationDialog(QDialog):
    """Modeless dialog to auto-calibrate ``joystick_trim_follow_gain_virtual_y``."""

    # Emitted on Save / family edits with a JSON payload:
    # {"virtual_y"?: float, "curves": [entry,...], "use_curve": bool,
    #  "stick_position"?: str}
    result_saved = pyqtSignal(str)
    # Emitted when the trimmed-stick-position pulldown changes — persisted
    # standalone so the mode is adjustable post-calibration without a run.
    position_mode_changed = pyqtSignal(str)

    STICK_POSITION_MODES = ("Follows Trim", "Stays Centered")
    # Export-file header for the shareable calibration-set format.
    EXPORT_TYPE = "telemffb-trim-calibration"
    # Save within this many knots of a stored entry offers to REPLACE it
    # (re-calibration semantics) instead of storing a near-duplicate speed.
    FAMILY_REPLACE_KT = 5.0

    # Combo index -> engine pitch-gain scale (Control response).
    RESPONSE_SCALES = {0: 1.5, 1: 1.0, 2: 0.5, 3: 0.25}
    RESPONSE_DEFAULT_INDEX = 1   # Normal
    # States in which the control-response combo may still be changed live
    # (from the sweep onward it locks so the measurement dynamics stay fixed).
    RESPONSE_LIVE_STATES = ("PROBE", "STABILIZE", "TRIM_NEUTRAL",
                            "ASSIST_HOLD", "SPEED_SETTLE")

    # Stage light: color + friendly text per engine state while a run is active.
    STAGE_DISPLAY = {
        "PROBE": ("#e6a817", "Probing control response…"),
        "STABILIZE": ("#e6a817", "Stabilizing level flight…"),
        "TRIM_NEUTRAL": ("#e6a817", "Finding natural trim point…"),
        "ASSIST_HOLD": ("#e6a817", "Trim assistant holding…"),
        "SPEED_SETTLE": ("#e6a817", "Letting airspeed stabilize…"),
        "SWEEP": ("#2a7fd4", "Sweeping trim…"),
        "SOLVE": ("#2a7fd4", "Computing…"),
        "RESTORE": ("#2a7fd4", "Restoring trim…"),
        "DONE": ("#33aa33", "Calibration complete"),
        "ABORT": ("#cc3333", "Aborted"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Elevator Trim Calibration (Joystick)")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        self.setMinimumWidth(560)

        self._last_result = None
        self._result_shown = False
        self._telem_connected = False
        self._ias_hist = []   # (t, IAS m/s) for the trend indicator
        self._cal_seen = None    # id() of the calibrator the display belongs to
        self._stored_curve_seen = None  # raw curve JSON the display reflects
        self._fam_raw = []       # stored family: raw entry dicts, speed-sorted
        self._fam = []           # parsed view of the same entries (index-aligned)
        self._fam_sel = 0        # manager selection index
        # Paused sims stop sending telemetry, so the timeout fires right after
        # the pause frame; remember it so the idle fallback can say "unpause"
        # instead of clobbering it with the generic waiting message.
        self._sim_paused_seen = False

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
            "QLabel { background-color:#b36b00; color:white; font-weight:bold;"
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
        self.btn_instructions = QToolButton()
        self.btn_instructions.setCheckable(True)
        # Clickability must be visible: link-blue bold text, hand cursor and
        # a hover highlight — a plain bold label reads as a heading, not a
        # control (field feedback).
        self.btn_instructions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_instructions.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; color: #2a7fd4;"
            " padding: 2px 4px; }"
            "QToolButton:hover { background-color: rgba(42, 127, 212, 38);"
            " border-radius: 4px; }")
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
            "<li><b>Trim the aircraft</b> so it holds level with near-zero stick force — "
            "starting far out of trim wastes elevator authority during the sweep.</li>"
            "<li><i>Recommended</i> — press <b>Trim Assistant</b> and TelemFFB will level "
            "and trim the aircraft for you, then keep re-trimming as you adjust power "
            "(this also measures the aircraft's trim response, which fine-tunes the sweep). "
            "Set your test speed with the throttle, wait for the assistant to report "
            "steady (the Start button enables), then start the sweep.</li>"
            "<li>Press <b>Start</b> — TelemFFB will fly the aircraft while it sweeps the "
            "elevator trim and measures the required stick input, then recommends a "
            "trim-following <i>curve</i> (with the equivalent static "
            "<i>Y&nbsp;Trim&nbsp;Gain&nbsp;Virtual</i> value) to hold the nose level as "
            "you trim. Your stick is <b>inactive</b> during calibration — its input is "
            "disconnected while TelemFFB has the controls.</li>"
            "</ol>"
        )
        self.lbl_instructions.setWordWrap(True)
        root.addWidget(self.lbl_instructions)
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

        # Debug-only controls (system settings 'debug' flag): trim write
        # method override + per-run diagnostic trace, for problem-aircraft
        # reports. Direct SimVar writes are the tested-primary method; the
        # axis event stays selectable in case an aircraft ever requires it.
        self._debug = bool(getattr(G, "system_settings", None)
                           and G.system_settings.get("debug", False))
        self.cmb_trim_method = None
        self.chk_trace = None
        if self._debug:
            debug_row = QHBoxLayout()
            lbl_trim_method = InfoLabel(
                text="Trim write method:",
                tooltip=(
                    "Debug options — this row is only visible when the Debug Mode "
                    "system setting is enabled.\n\n"
                    "Trim write method: how calibration commands the sim's elevator "
                    "trim (MSFS).\n"
                    "• Direct (default) — writes the ELEVATOR TRIM POSITION SimVar "
                    "itself; the most reliable method across tested aircraft.\n"
                    "• Axis event — sends AXIS_ELEV_TRIM_SET instead, which assumes "
                    "the aircraft maps the event 1:1 onto its trim. Some addons "
                    "mishandle the event (e.g. Just Flight); use this only to test "
                    "an aircraft that misbehaves with the direct method.\n\n"
                    "Record diagnostic trace: writes a per-frame CSV of everything "
                    "the calibration commands and observes (trimcal_trace_*.csv in "
                    "the TelemFFB log folder) — attach it when reporting a problem "
                    "aircraft."))
            debug_row.addWidget(lbl_trim_method)
            self.cmb_trim_method = QComboBox()
            self.cmb_trim_method.addItems([
                "Direct (ELEVATOR TRIM POSITION)",
                "Axis event (AXIS_ELEV_TRIM_SET)",
            ])
            debug_row.addWidget(self.cmb_trim_method)
            self.chk_trace = QCheckBox("Record diagnostic trace")
            self.chk_trace.setChecked(False)
            self.chk_trace.setToolTip(
                "Write a per-frame CSV of everything the calibration commands and\n"
                "observes to the TelemFFB log folder (trimcal_trace_*.csv).\n"
                "Attach it when reporting a problem aircraft.")
            debug_row.addWidget(self.chk_trace)
            debug_row.addStretch(1)
            root.addLayout(debug_row)

        # Warning banner shown only while the engine is flying the aircraft.
        self.banner = QLabel("⚠  TelemFFB is controlling your aircraft — stay ready to take over")
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setStyleSheet(
            "QLabel { background-color:#cc3300; color:white; font-weight:bold;"
            " border-radius:6px; padding:5px; }"
        )
        self.banner.setVisible(False)
        root.addWidget(self.banner)

        # ---- live status ----
        status_box = QGroupBox("Live status")
        grid = QGridLayout(status_box)
        self.lbl_ias = QLabel("—")
        self.lbl_pitch = QLabel("—")
        self.lbl_vs = QLabel("—")
        self.lbl_bank = QLabel("—")
        self.lbl_trim = QLabel("—")
        self.lbl_state = QLabel("Idle")
        # IAS gets a live trend arrow beside the value (MSFS creeps toward
        # its new equilibrium speed for a minute-plus after a power change;
        # the arrow shows the assistant is waiting on physics, not wedged).
        self.ias_trend = IasTrendWidget()
        for col, (name, w) in enumerate([
            ("IAS", self.lbl_ias), ("Pitch", self.lbl_pitch), ("VS", self.lbl_vs),
            ("Bank", self.lbl_bank), ("Trim", self.lbl_trim),
        ]):
            grid.addWidget(QLabel(f"<b>{name}</b>"), 0, col, alignment=Qt.AlignmentFlag.AlignHCenter)
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            if w is self.lbl_ias:
                # Arrow pinned at the cell's right edge, OUTSIDE the label's
                # centering, so neither the arrow redrawing nor the label
                # text changing shifts anything else.
                cell = QHBoxLayout()
                cell.setContentsMargins(0, 0, 0, 0)
                cell.setSpacing(0)
                cell.addStretch(1)
                cell.addWidget(self.lbl_ias)
                cell.addStretch(1)
                cell.addWidget(self.ias_trend)
                grid.addLayout(cell, 1, col)
            else:
                grid.addWidget(w, 1, col)
        # Equal columns regardless of content width, or the whole row
        # re-negotiates as values change length.
        for col in range(5):
            grid.setColumnStretch(col, 1)

        # State text gets its own full-width row (long per-frame status lines)
        # and the stage/ready light its own — sharing a row clips one or the
        # other. An HBox keeps the message left-justified right after the
        # State: prefix instead of starting at the (wide) second grid column,
        # and the label reserves two text lines so an occasional word-wrap
        # cannot bounce the layout below it up and down.
        state_row = QHBoxLayout()
        state_row.addWidget(QLabel("<b>State:</b>"), 0, Qt.AlignmentFlag.AlignTop)
        self.lbl_state.setWordWrap(True)
        self.lbl_state.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.lbl_state.setMinimumHeight(2 * self.lbl_state.fontMetrics().lineSpacing())
        state_row.addWidget(self.lbl_state, 1)
        grid.addLayout(state_row, 2, 0, 1, 5)
        # Left-justified so the light sits at a fixed position — right-
        # aligned, the whole line (light included) shifted with text length.
        self.lbl_ready = QLabel("●  —")
        grid.addWidget(self.lbl_ready, 3, 0, 1, 5, alignment=Qt.AlignmentFlag.AlignLeft)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        grid.addWidget(self.progress, 4, 0, 1, 5)
        root.addWidget(status_box)

        # ---- result ----
        result_box = QGroupBox("Result")
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
                 "QToolButton:disabled { color: #808080; }")

        def _tool_btn(text, tooltip=None):
            b = QToolButton()
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
                  f"QToolButton:hover {{ background-color: #44ab37c8;"
                  f" border-radius: 4px; }}"
                  "QToolButton:disabled { color: #808080; }")

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
            text="Delete", tooltip="Delete the selected calibration "
                                   "(or right-click a curve on the plot)")
        self.btn_fam_delete.clicked.connect(self._on_family_delete)
        fam_row.addWidget(self.btn_fam_delete)
        self.btn_fam_clear = _tool_btn(
            text="Clear all", tooltip="Delete all stored calibrations")
        self.btn_fam_clear.clicked.connect(self._on_family_clear)
        fam_row.addWidget(self.btn_fam_clear)
        fam_row.addSpacing(10)
        self.btn_fam_export = _tool_btn(
            text="Export…",
            tooltip="Export this aircraft's stored calibrations to a file "
                    "another user can import")
        self.btn_fam_export.clicked.connect(self._on_family_export)
        fam_row.addWidget(self.btn_fam_export)
        self.btn_fam_import = _tool_btn(
            text="Import…",
            tooltip="Import a shared calibration file for this aircraft "
                    "(replaces the stored set)")
        self.btn_fam_import.clicked.connect(self._on_family_import)
        fam_row.addWidget(self.btn_fam_import)
        fam_row.addStretch(1)
        rlay.addLayout(fam_row)

        # Trimmed-stick-position mode: an airframe description, adjustable
        # post-calibration and persisted immediately (no Save required).
        pos_row = QHBoxLayout()
        pos_row.addWidget(InfoLabel(
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
                "differs. Changes apply and save immediately.")))
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
        self.lbl_note.setStyleSheet("QLabel { color:#cc7a00; }")
        rlay.addWidget(self.lbl_virtual)
        rlay.addWidget(self.lbl_linearity)
        rlay.addWidget(self.lbl_note)
        root.addWidget(result_box)

        # Trim assistant gets its own row (it lives outside the run-control
        # button row so it does not get lost among the bottom buttons).
        assist_row = QHBoxLayout()
        self.btn_assist = QPushButton("Trim Assistant")
        self.btn_assist.setToolTip(
            "Have TelemFFB level and trim the aircraft, then hold it level while\n"
            "you adjust power to the speed you want to calibrate at. The assistant\n"
            "re-trims after every power change; when it reports steady, press Start\n"
            "to run the sweep from there (the leveling and trim-finding phases are\n"
            "already complete). Cancelling the assistant leaves the trim where it\n"
            "is — the aircraft stays trimmed for the current power.")
        self.btn_assist.clicked.connect(self._on_assist)
        assist_row.addWidget(self.btn_assist)
        lbl_assist = QLabel("Levels and trims the aircraft for you while you set your test speed")
        lbl_assist.setStyleSheet("QLabel { color: gray; }")
        assist_row.addWidget(lbl_assist)
        assist_row.addStretch(1)
        root.addLayout(assist_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)

        # ---- buttons ----
        btns = QHBoxLayout()
        self.btn_start = QPushButton("Start")
        self.btn_stop = QPushButton("⛔ Abort")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)

        self.btn_apply = QPushButton("Apply (test in sim)")
        self.btn_apply.setToolTip("Apply the value live for a fly-test without saving it to the profile")
        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip("Write the value to this aircraft's Y Trim Gain Virtual setting")
        self.btn_close = QPushButton("Close")
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_close.clicked.connect(self.close)
        btns.addWidget(self.btn_apply)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_close)
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

    def _offline_settings(self):
        """Prereq-filtered settings of the profile selected in the OFFLINE
        editor, as {name: value} with units applied (the same to_number
        conversion the live pipeline uses), or None when not in offline
        editing mode. While editing offline, the live aircraft — and any
        result sitting on its calibrator — belongs to whatever was flying
        before offline mode was entered; never display state from it."""
        if not self._offline_editing():
            return None
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
            name = sm.current_aircraft_name or sm.current_pattern or "—"
            text = f"<b>Aircraft:</b> {html.escape(name)}  <i>(offline editor)</i>"
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
            # aircraft would have.
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

            if G.device_type != "joystick":
                self._set_ready(False, "Run from the joystick (master) instance")
                self._set_running(False)
                self.btn_start.setEnabled(False)
                self.btn_assist.setEnabled(False)
                return
            if cal is None:
                self._set_ready(False, "Load an MSFS / X-Plane aircraft")
                self._set_running(False)
                self.btn_start.setEnabled(False)
                self.btn_assist.setEnabled(False)
                return

            running = cal.active
            assist_holding = running and cal.state == CalState.ASSIST_HOLD
            self.lbl_state.setText(getattr(cal.state, "name", str(cal.state)))
            if cal.status_message:
                self.lbl_state.setText(f"{getattr(cal.state, 'name', '')} — {cal.status_message}")
            self.progress.setValue(int(cal.progress * 100))
            if assist_holding:
                # Start is the sweep trigger while the assistant holds, gated
                # on the assistant's own stability determination (starting
                # mid-transient is a guaranteed abort; the hysteresis lives
                # engine-side, next to the data).
                start_want = bool(getattr(cal, "assist_stable", False))
            elif running:
                start_want = False
            else:
                start_want = self.btn_start.isEnabled()  # can_start decides below
            self._set_running(running, start_enabled=start_want)
            self.btn_start.setText("Start sweep" if assist_holding else "Start")

            # Run options lock while a run is active — they are read once at
            # start and a mid-run change would silently do nothing. The
            # control-response combo is the exception: it applies live up
            # until the sweep locks the measurement dynamics.
            state_name = getattr(cal.state, "name", "")
            self.cmb_response.setEnabled(
                not running or state_name in self.RESPONSE_LIVE_STATES)
            if self.cmb_trim_method is not None:
                self.cmb_trim_method.setEnabled(not running)
            if self.chk_trace is not None:
                self.chk_trace.setEnabled(not running)

            if running:
                # Family edits and the position mode lock during a run — the
                # engine read its state at start. Re-enabled by the next
                # stored-curve/result render's _update_family_ui.
                self.btn_fam_delete.setEnabled(False)
                self.btn_fam_clear.setEnabled(False)
                self.cmb_stick_pos.setEnabled(False)
                self._show_stage_light(cal)
                self.curve.set_live_point(data.get("ElevTrimPct"), getattr(cal, "_u_elev", None))
                # Show accepted stations as they land (full-scale view; the
                # zoom-to-fit happens once at completion in _show_result).
                self.curve.set_samples(list(getattr(cal, "_samples", []) or []))
                self.btn_assist.setEnabled(False)
            else:
                ok, msg = cal.can_start(data)
                self._set_ready(ok, msg)
                self.btn_start.setEnabled(ok)
                self.btn_assist.setEnabled(ok)
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
            stick_pos = getattr(ac, "joystick_trim_follow_stick_position", None)
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
        prov = " · ".join(str(x) for x in [
            f"{entry['ias_kt']:.1f} kt" if entry.get("ias_kt") else "",
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
        # an aircraft that is not loaded); only an active run blocks it.
        self.btn_fam_export.setEnabled(n > 0)
        self.btn_fam_import.setEnabled(not running)
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
                if ac is not None else None
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

    def _fit_to_content(self, allow_shrink=False):
        """Grow (never shrink, unless asked) the window so the content cannot
        overlap.

        Word-wrapped labels make the layout height-for-width: a plain
        sizeHint() under-reports the needed height until a later layout pass,
        which shows up as text overlapping the graph that fixes itself when
        the window is moved. Force a layout pass and measure with the
        height-for-width machinery at the current width instead.

        ``allow_shrink`` is for deliberate content removal (collapsing the
        instructions) — the one case where handing space back beats leaving
        a gap.
        """
        lay = self.layout()
        lay.activate()
        if lay.hasHeightForWidth():
            needed = lay.totalHeightForWidth(self.width())
        else:
            needed = self.sizeHint().height()
        if self.height() < needed or (allow_shrink and self.height() > needed):
            self.resize(self.width(), needed)

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

    def _apply_instructions_state(self, expanded):
        self.lbl_instructions.setVisible(expanded)
        self.btn_instructions.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.btn_instructions.setText(
            "How to use  (click to close)" if expanded
            else "How to use  (click to expand)")
        self.btn_instructions.setToolTip(
            "Hide the instructions" if expanded else "Show the instructions")
        self._refit(allow_shrink=not expanded)

    def _update_live_values(self, data, ias_ref=None):
        def fmt(v, conv=1.0, unit="", nd=0):
            if v is None:
                return "—"
            return f"{v * conv:.{nd}f}{unit}"

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
        ias_txt = fmt(ias, MS_TO_KT, " kt")
        ias_style = ""
        if ias_ref and ias:
            drift = (ias - ias_ref) / ias_ref
            if abs(drift) >= 0.05:
                ias_txt += f" ({drift * 100:+.0f}%)"
                color = "#cc3333" if abs(drift) >= 0.10 else "#cc7a00"
                ias_style = f"QLabel {{ color:{color}; font-weight:bold; }}"
        self.lbl_ias.setText(ias_txt)
        self.lbl_ias.setStyleSheet(ias_style)
        self.lbl_pitch.setText(fmt(data.get("Pitch"), 1.0, "°", nd=1))
        self.lbl_vs.setText(fmt(data.get("VerticalSpeed"), MS_TO_FPM, " fpm"))
        self.lbl_bank.setText(fmt(data.get("Roll"), 1.0, "°", nd=1))
        et = data.get("ElevTrimPct")
        self.lbl_trim.setText("—" if et is None else f"{et * 100:.0f}%")

    # ---- button handlers ----------------------------------------------------

    def _on_start(self):
        cal = self._calibrator()
        if cal is None:
            return
        if cal.active and cal.state == CalState.ASSIST_HOLD:
            # Assistant is holding: Start hands off to the sweep (the button
            # is gated on the assistant's own stability determination).
            cal.begin_sweep()
            return
        self._arm_and_start(cal, assist=False)

    def _on_assist(self):
        cal = self._calibrator()
        if cal is None or cal.active:
            return
        self._arm_and_start(cal, assist=True)

    def _on_response_changed(self, index):
        # Live control-response change on a running loop (up to the sweep;
        # the combo is disabled from the sweep onward). Idle changes are
        # picked up by _arm_and_start at the next start as before.
        cal = self._calibrator()
        if cal is not None and cal.active:
            cal.set_gain_scale(self.RESPONSE_SCALES.get(index, 1.0))

    def _arm_and_start(self, cal, assist):
        ac = G.telem_manager.currentAircraft
        telem = getattr(ac, "telem_data", None)
        ok, msg = cal.can_start(telem)
        if not ok:
            QMessageBox.warning(self, "Not ready", msg)
            return
        self._result_shown = False
        self._last_result = None
        self.curve.clear()
        self._clear_result_labels()
        cal.initial_gain_scale = self.RESPONSE_SCALES.get(
            self.cmb_response.currentIndex(), 1.0)
        if self._debug:
            cal.trim_write_method = \
                "axis" if self.cmb_trim_method.currentIndex() == 1 else "direct"
            cal.trace_enabled = self.chk_trace.isChecked()
        else:
            cal.trim_write_method = "direct"
            cal.trace_enabled = False
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
        # rides along so Save captures the whole curve-mode state.
        return {
            "virtual_y": float(self._last_result["virtual_y"]),
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
        ac.joystick_trim_follow_gain_virtual_y = p["virtual_y"]
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
        next speed is one Trim Assistant click away, no reopen needed."""
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
            f"aircraft. Ready for the next run: press Trim Assistant, pick "
            f"the next test speed, and calibrate again.")

    # ---- result / state display ---------------------------------------------

    def _show_result(self, result):
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
                "trim following seems off, re-run with steadier power (the Trim Assistant "
                "helps find a stable speed first).")
        flagged = result.get("flagged") or []
        if flagged:
            worst = max(abs(f["vs_fpm"]) for f in flagged)
            where = ", ".join(f"{100 * f['trim']:+.0f}%" for f in flagged)
            plural = "s" if len(flagged) > 1 else ""
            notes.append(
                f"{len(flagged)} station{plural} (trim {where}, shown in amber) sampled "
                f"with a residual climb/descent of up to {worst:.0f} fpm — usually slow "
                "airspeed drift. The curve may be slightly skewed near those points; if "
                "trim following seems off there, re-run with steadier power (the Trim "
                "Assistant helps find a stable speed first).")
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
        self._set_light("#33aa33" if ok else "#cc3333", message)

    def _set_light(self, color, message):
        self.lbl_ready.setText(
            f"<span style='color:{color}; font-size:15pt;'>●</span>  {message}")

    def _show_stage_light(self, cal):
        """While a run is active, the light reflects the engine stage."""
        state_name = getattr(cal.state, "name", "")
        if state_name == "ASSIST_HOLD":
            # The assistant's light doubles as the sweep-readiness indicator.
            if getattr(cal, "assist_stable", False):
                self._set_light("#33aa33", "Assistant steady — ready to start the sweep")
            else:
                self._set_light("#e6a817", "Assistant leveling / re-trimming…")
            return
        color, text = self.STAGE_DISPLAY.get(state_name, ("#e6a817", state_name.title()))
        self._set_light(color, text)

    def _set_running(self, running, start_enabled=None):
        banner_appearing = running and not self.banner.isVisible()
        self.banner.setVisible(running)
        self.btn_stop.setEnabled(running)
        # The desired Start state is applied in ONE transition. This slot runs
        # per telemetry frame — a disable→re-enable pulse (the old code path
        # for the assistant's gated Start) clears a QPushButton's in-progress
        # press, silently swallowing every user click.
        if start_enabled is None:
            start_enabled = not running and self.btn_start.isEnabled()
        if self.btn_start.isEnabled() != start_enabled:
            self.btn_start.setEnabled(start_enabled)
        if running:
            self.btn_apply.setEnabled(False)
            self.btn_save.setEnabled(False)
        if banner_appearing:
            # The banner adds a row mid-flight; grow the window for it or the
            # squeeze overlaps the curve's axis labels with the text below.
            self._refit()

    def _refresh_idle(self):
        self._ias_hist.clear()
        self.ias_trend.set_rate(None)
        self.lbl_ias.setText("—")
        self.lbl_pitch.setText("—")
        self.lbl_vs.setText("—")
        self.lbl_bank.setText("—")
        self.lbl_trim.setText("—")
        self.lbl_state.setText("Idle")
        self.progress.setValue(0)
        self.banner.setVisible(False)
        self.btn_stop.setEnabled(False)
        self.btn_start.setText("Start")
        self.btn_assist.setEnabled(False)
        self.cmb_response.setEnabled(True)
        if self.cmb_trim_method is not None:
            self.cmb_trim_method.setEnabled(True)
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
            self._set_ready(False, "Offline editing — run calibrations with a live sim")
            self.btn_start.setEnabled(False)
            self.btn_assist.setEnabled(False)
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
            self.btn_start.setEnabled(False)
        elif cal is None:
            self._set_ready(False, "Load an MSFS / X-Plane aircraft")
            self.btn_start.setEnabled(False)
        else:
            # No telemetry right now. If the last frame before the timeout
            # said the sim was paused, keep the specific message; otherwise
            # show the generic one. _on_telemetry replaces this with the live
            # can_start result once frames arrive.
            if self._sim_paused_seen:
                self._set_ready(False, "Unpause the simulator to calibrate")
            else:
                self._set_ready(False, "Waiting for telemetry — is the sim running?")
            self.btn_start.setEnabled(False)

    # ---- lifecycle ----------------------------------------------------------

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
