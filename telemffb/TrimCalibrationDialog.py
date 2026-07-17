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
import json
import logging

from PyQt6 import QtCore
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QProgressBar, QMessageBox, QFrame, QCheckBox, QSizePolicy,
    QComboBox,
)

import telemffb.globals as G
from telemffb.custom_widgets import InfoLabel, TrimCurveWidget
from telemffb.sim.msfs_xp.TrimCalibrator import CalState

logger = logging.getLogger(__name__)

MS_TO_KT = 1.0 / 0.514444
MS_TO_FPM = 196.850394


class TrimCalibrationDialog(QDialog):
    """Modeless dialog to auto-calibrate ``joystick_trim_follow_gain_virtual_y``."""

    # Emitted on Save with a JSON payload:
    # {"virtual_y": float, "curve": dict|None, "use_curve": bool}
    result_saved = pyqtSignal(str)

    # Stage light: color + friendly text per engine state while a run is active.
    STAGE_DISPLAY = {
        "PROBE": ("#e6a817", "Probing control response…"),
        "STABILIZE": ("#e6a817", "Stabilizing level flight…"),
        "TRIM_NEUTRAL": ("#e6a817", "Finding natural trim point…"),
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
        self._cal_seen = None    # id() of the calibrator the display belongs to
        # Paused sims stop sending telemetry, so the timeout fires right after
        # the pause frame; remember it so the idle fallback can say "unpause"
        # instead of clobbering it with the generic waiting message.
        self._sim_paused_seen = False

        self._build_ui()
        self._refresh_idle()

    # ---- UI construction ----------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        instructions = QLabel(
            "<b>How to calibrate</b>"
            "<ul style='margin-top:2px; margin-bottom:6px; -qt-list-indent:1;'>"
            "<li>It is recommended to perform the calibration in <b>clear</b>, <b>calm</b> conditions.</li>"
            "<li>If you have a hardware device controlling the elevator trim <b>AXIS</b>, you may "
            "need to disconnect or un-bind it as it may interfere with TelemFFB "
            "controlling the trim during calibration. (Excludes Vpforce trim-wheel)</li>"
            "</ul>"
            "<ol style='margin-top:0px; margin-bottom:0px; -qt-list-indent:1;'>"
            "<li>Get airborne, straight &amp; level, at a stable cruise speed with "
            "Autopilot <b>OFF</b>.</li>"
            "<li><b>Trim the aircraft</b> so it holds level with near-zero stick force — "
            "starting far out of trim wastes elevator authority during the sweep.</li>"
            "<li>Press <b>Start</b> and take your hands <b>off</b> the stick — TelemFFB "
            "will fly the aircraft while it sweeps the elevator trim and measures the "
            "required stick input, then recommends a trim-following <i>curve</i> (with "
            "the equivalent static <i>Y&nbsp;Trim&nbsp;Gain&nbsp;Virtual</i> value) to "
            "hold the nose level as you trim.</li>"
            "</ol>"
        )
        instructions.setWordWrap(True)
        root.addWidget(instructions)

        self.chk_settle = QCheckBox(
            "Hold level to let the airspeed stabilize before the sweep (adds ~20 s)")
        self.chk_settle.setChecked(True)
        self.chk_settle.setToolTip(
            "After finding the natural trim point, hold straight-and-level for 20 seconds "
            "so the airspeed settles at the current throttle/trim before measuring.\n"
            "Recommended — uncheck for a faster run.")
        root.addWidget(self.chk_settle)

        response_row = QHBoxLayout()
        lbl_response = InfoLabel(
            text="Control response:",
            tooltip=(
                "Strength of the control inputs used to fly the aircraft during calibration.\n\n"
                "Normal — most aircraft.\n"
                "Reduced — sensitive aircraft that porpoise or bounce during stabilization.\n"
                "Minimal — very sensitive or aerobatic aircraft with light, twitchy pitch.\n\n"
                "Calibration also reduces its own control gains automatically when it detects\n"
                "oscillation; this option just starts from a gentler setting. If a run aborts\n"
                "with a pitch-oscillation error, retry with the next lower setting."))
        response_row.addWidget(lbl_response)
        self.cmb_response = QComboBox()
        self.cmb_response.addItems([
            "Normal",
            "Reduced — sensitive aircraft",
            "Minimal — very sensitive / aerobatic",
        ])
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
        for col, (name, w) in enumerate([
            ("IAS", self.lbl_ias), ("Pitch", self.lbl_pitch), ("VS", self.lbl_vs),
            ("Bank", self.lbl_bank), ("Trim", self.lbl_trim),
        ]):
            grid.addWidget(QLabel(f"<b>{name}</b>"), 0, col, alignment=Qt.AlignmentFlag.AlignHCenter)
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            grid.addWidget(w, 1, col)

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
        self.lbl_ready = QLabel("●  —")
        grid.addWidget(self.lbl_ready, 3, 0, 1, 5, alignment=Qt.AlignmentFlag.AlignRight)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        grid.addWidget(self.progress, 4, 0, 1, 5)
        root.addWidget(status_box)

        # ---- result ----
        result_box = QGroupBox("Result")
        rlay = QVBoxLayout(result_box)
        self.curve = TrimCurveWidget()
        # Expanding + a modest minimum makes the graph the flexible element:
        # a tall notes block shrinks it rather than overlapping it.
        self.curve.setMinimumHeight(200)
        self.curve.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rlay.addWidget(self.curve)

        self.lbl_virtual = QLabel("Recommended Y Trim Gain (Virtual): <b>—</b>")
        self.lbl_linearity = QLabel("Linearity (R²): —")
        # Force rich text so entities (e.g. &nbsp;) render regardless of whether
        # a given text branch happens to contain HTML tags for auto-detection.
        self.lbl_virtual.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_linearity.setTextFormat(Qt.TextFormat.RichText)
        # The recommendation line grows long (curve + static fit + current
        # value); wrap it instead of pushing past the window edge.
        self.lbl_virtual.setWordWrap(True)
        self.chk_use_curve = QCheckBox("Use calibrated curve (recommended) — static gain is used when unchecked")
        self.chk_use_curve.setChecked(True)
        self.chk_use_curve.setEnabled(False)
        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet("QLabel { color:#cc7a00; }")
        rlay.addWidget(self.lbl_virtual)
        rlay.addWidget(self.lbl_linearity)
        rlay.addWidget(self.chk_use_curve)
        rlay.addWidget(self.lbl_note)
        root.addWidget(result_box)

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

            ias_ref = getattr(cal, "_ias0", None) if (cal is not None and cal.active) else None
            self._update_live_values(data, ias_ref)

            if G.device_type != "joystick":
                self._set_ready(False, "Run from the joystick (master) instance")
                self._set_running(False)
                self.btn_start.setEnabled(False)
                return
            if cal is None:
                self._set_ready(False, "Load an MSFS / X-Plane aircraft")
                self._set_running(False)
                self.btn_start.setEnabled(False)
                return

            running = cal.active
            self.lbl_state.setText(getattr(cal.state, "name", str(cal.state)))
            if cal.status_message:
                self.lbl_state.setText(f"{getattr(cal.state, 'name', '')} — {cal.status_message}")
            self.progress.setValue(int(cal.progress * 100))
            self._set_running(running)

            if running:
                self._show_stage_light(cal)
                self.curve.set_live_point(data.get("ElevTrimPct"), getattr(cal, "_u_elev", None))
                # Show accepted stations as they land (full-scale view; the
                # zoom-to-fit happens once at completion in _show_result).
                self.curve.set_samples(list(getattr(cal, "_samples", []) or []))
            else:
                ok, msg = cal.can_start(data)
                self._set_ready(ok, msg)
                self.btn_start.setEnabled(ok)

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
        """Display the aircraft's SAVED calibration curve (view-only).

        A freshly loaded aircraft has a fresh calibrator with no result, but
        may carry a calibrated curve in its settings. The stored curve is the
        runtime offset ``offs(T) = -(u(T) - u(0))``; the plot shows the
        measured-axis space, so it is mirrored back for display. Returns True
        when something was shown.
        """
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        raw = getattr(ac, "joystick_trim_follow_curve_y", None) if ac is not None else None
        if not raw or raw == "none":
            return False
        try:
            data = json.loads(raw)
            samples = [(float(p["t"]), -float(p["offs"])) for p in data["points"]]
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Stored trim curve unreadable; not displaying ({e})")
            return False
        if len(samples) < 2:
            return False

        physical_y = getattr(ac, "joystick_trim_follow_gain_physical_y", 1.0) or 1.0
        virtual_y = getattr(ac, "joystick_trim_follow_gain_virtual_y", 0.0) or 0.0
        # The static gain's equivalent line, for comparison (zero-referenced).
        slope = -physical_y * (1.0 - virtual_y)
        self.curve.set_result(samples, slope, 0.0)

        prov = " · ".join(str(x) for x in [
            f"captured {data.get('date')}" if data.get("date") else "",
            f"{data.get('ias_kt')} kt" if data.get("ias_kt") else "",
        ] if x)
        use_curve = bool(getattr(ac, "joystick_trim_follow_use_curve_y", False))
        self.lbl_virtual.setText(
            "Saved calibration curve for this aircraft"
            + (f"  &nbsp;({prov})" if prov else "")
            + f"  &nbsp;·  static value: {virtual_y:.3f}")
        self.lbl_linearity.setText(
            f"Curve in use: {'yes' if use_curve else 'no — static gain active'}")
        self.chk_use_curve.setChecked(use_curve)
        self._fit_to_content()
        return True

    def _fit_to_content(self):
        """Grow (never shrink) the window so the content cannot overlap.

        Word-wrapped labels make the layout height-for-width: a plain
        sizeHint() under-reports the needed height until a later layout pass,
        which shows up as text overlapping the graph that fixes itself when
        the window is moved. Force a layout pass and measure with the
        height-for-width machinery at the current width instead.
        """
        lay = self.layout()
        lay.activate()
        if lay.hasHeightForWidth():
            needed = lay.totalHeightForWidth(self.width())
        else:
            needed = self.sizeHint().height()
        if self.height() < needed:
            self.resize(self.width(), needed)

    def _refit(self):
        """Fit now and once more on the next event-loop tick: the deferred
        pass re-measures after Qt has finished the pending relayout (the same
        settling a window move used to trigger by accident)."""
        self._fit_to_content()
        QtCore.QTimer.singleShot(0, self._fit_to_content)

    def _update_live_values(self, data, ias_ref=None):
        def fmt(v, conv=1.0, unit="", nd=0):
            if v is None:
                return "—"
            return f"{v * conv:.{nd}f}{unit}"

        # Live airspeed-drift warning during a run: the measured slope is only
        # trustworthy at roughly constant speed, so surface drift as it happens
        # rather than only in the post-run note.
        ias = data.get("IAS")
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
        cal.settle_before_sweep = self.chk_settle.isChecked()
        cal.initial_gain_scale = {0: 1.0, 1: 0.5, 2: 0.25}.get(
            self.cmb_response.currentIndex(), 1.0)
        if self._debug:
            cal.trim_write_method = \
                "axis" if self.cmb_trim_method.currentIndex() == 1 else "direct"
            cal.trace_enabled = self.chk_trace.isChecked()
        else:
            cal.trim_write_method = "direct"
            cal.trace_enabled = False
        cal.start()

    def _on_stop(self):
        cal = self._calibrator()
        if cal is not None:
            cal.stop("Cancelled by user")

    def _payload(self):
        curve = self._last_result.get("curve")
        return {
            "virtual_y": float(self._last_result["virtual_y"]),
            "curve": curve,
            "use_curve": bool(self.chk_use_curve.isChecked() and curve is not None),
        }

    def _on_apply(self):
        if self._last_result is None:
            return
        ac = G.telem_manager.currentAircraft if G.telem_manager else None
        if ac is not None:
            p = self._payload()
            ac.joystick_trim_follow_gain_virtual_y = p["virtual_y"]
            # Property setter parses the JSON once into lookup arrays.
            ac.joystick_trim_follow_curve_y = json.dumps(p["curve"]) if p["curve"] else "none"
            ac.joystick_trim_follow_use_curve_y = p["use_curve"]
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
                "needs work — re-run the calibration, or toggle <i>Use calibrated "
                "curve</i> to compare it against the static value."
                "<br><br>Click <b>Save</b> to write this to the profile.")
            box.exec()

    def _on_save(self):
        if self._last_result is None:
            return
        self.result_saved.emit(json.dumps(self._payload()))
        QMessageBox.information(self, "Saved",
                                "Trim-following calibration saved for this aircraft.")

    # ---- result / state display ---------------------------------------------

    def _show_result(self, result):
        self._result_shown = True
        self._last_result = result
        self.curve.set_result(
            result["samples"], result["slope"], result["intercept"],
            flagged=[f["index"] for f in result.get("flagged") or []])
        self.curve.set_live_point(None, None)
        current = result.get("current_virtual_y")
        current_txt = f"  &nbsp;·  current profile value: {current:.3f}" if current is not None else ""
        has_curve = result.get("curve") is not None
        rec_txt = "<b>calibrated curve</b> (solid line)" if has_curve else "static gain"
        self.lbl_virtual.setText(
            f"Recommended: {rec_txt}  &nbsp;·  static fit: {result['virtual_y']:.3f}"
            f"  &nbsp;(for Physical Y = {result['physical_y']:.2f}){current_txt}")
        self.lbl_linearity.setText(f"Linearity (R²): {result['r_squared']:.3f}")
        self.chk_use_curve.setEnabled(has_curve)
        self.chk_use_curve.setChecked(has_curve)
        notes = []
        if not result["linear_ok"]:
            notes.append(
                "Response is non-linear — a single gain can't hold level across the whole "
                "trim range. This value is the best linear fit; a trim curve would fit better.")
        ias_drift = result.get("ias_drift", 0)
        if abs(ias_drift) > 0.05:
            notes.append(
                f"Airspeed drifted {ias_drift * 100:+.0f}% during the sweep, which can skew "
                "the measurement. The result may still be fine — test it with Apply, and if "
                "trim following seems off, consider re-running with a steadier airspeed "
                "(stable power, and the airspeed-settle option enabled).")
        flagged = result.get("flagged") or []
        if flagged:
            worst = max(abs(f["vs_fpm"]) for f in flagged)
            where = ", ".join(f"{100 * f['trim']:+.0f}%" for f in flagged)
            plural = "s" if len(flagged) > 1 else ""
            notes.append(
                f"{len(flagged)} station{plural} (trim {where}, shown in amber) sampled "
                f"with a residual climb/descent of up to {worst:.0f} fpm — usually slow "
                "airspeed drift. The curve may be slightly skewed near those points; if "
                "trim following seems off there, consider re-running with a steadier "
                "airspeed (stable power, airspeed-settle option enabled).")
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
        self.lbl_virtual.setText("Recommended Y Trim Gain (Virtual): <b>—</b>")
        self.lbl_linearity.setText("Linearity (R²): —")
        self.lbl_note.setText("")
        self.chk_use_curve.setEnabled(False)
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
        color, text = self.STAGE_DISPLAY.get(state_name, ("#e6a817", state_name.title()))
        self._set_light(color, text)

    def _set_running(self, running):
        banner_appearing = running and not self.banner.isVisible()
        self.banner.setVisible(running)
        self.btn_stop.setEnabled(running)
        self.btn_start.setEnabled(not running and self.btn_start.isEnabled())
        if running:
            self.btn_apply.setEnabled(False)
            self.btn_save.setEnabled(False)
        if banner_appearing:
            # The banner adds a row mid-flight; grow the window for it or the
            # squeeze overlaps the curve's axis labels with the text below.
            self._refit()

    def _refresh_idle(self):
        self.lbl_ias.setText("—")
        self.lbl_pitch.setText("—")
        self.lbl_vs.setText("—")
        self.lbl_bank.setText("—")
        self.lbl_trim.setText("—")
        self.lbl_state.setText("Idle")
        self.progress.setValue(0)
        self.banner.setVisible(False)
        self.btn_stop.setEnabled(False)
        cal = self._calibrator()
        has_result = cal is not None and cal.state == CalState.DONE and cal.result is not None
        if has_result and not self._result_shown:
            self._show_result(cal.result)
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
