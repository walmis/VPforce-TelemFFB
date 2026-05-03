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

"""Step-by-step shaker calibration wizard.

Sits on top of the existing manual calibration panel in SystemSettingsDialog
without replacing it: the manual panel stays as expert mode, the wizard
guides a novice through one decision per page (preset → routing check →
resonance sweep → carrier offset ladder → halfwaves ladder → attack/release
sliders → brake decision → review and save).

The wizard does not have access to a microphone or accelerometer, so
"automatic value detection" means: smart defaults from a preset, the
existing sweep+mark workflow for resonance, "best of N" ladder picks for
parameters the user can compare by feel, and formula-derived defaults
(``brake_delay_ms ≈ 1000 / (2 · f_res_hz)``).

Pattern follows NewAircraftWizard: QDialog + QStackedWidget + four manual
nav buttons (Previous / Next / Finish / Cancel). No Ui_ file because the
ladder pages spawn rows dynamically.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDoubleSpinBox, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
    QSlider, QSpacerItem, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import _shaker_calib_runtime as _runtime
from .hw.shaker_profile import DEFAULT_PROFILE, ShakerProfile

log = logging.getLogger(__name__)


# Page indices — keep in one place so manage_pages and the page builders
# stay in sync without magic numbers scattered through the file.
_PAGE_PRESET = 0
_PAGE_ROUTING = 1
_PAGE_SWEEP = 2
_PAGE_CARRIER = 3
_PAGE_HALFWAVES = 4
_PAGE_ENVELOPE = 5
_PAGE_BRAKE = 6
_PAGE_REVIEW = 7
_PAGE_COUNT = 8

_CARRIER_LADDER = (0.0, 5.0, 10.0, 15.0, 20.0, 30.0)
_HALFWAVES_LADDER = (1, 2, 3)


class ShakerCalibrationWizard(QDialog):
    """Guided calibration flow producing a ShakerProfile.

    After ``exec() == Accepted`` the caller reads ``result_profile``
    (frozen ShakerProfile) and ``result_overwrite`` (whether the user
    chose to overwrite an existing entry by the same name).
    """

    def __init__(self, parent, profiles: dict, active_name: str):
        super().__init__(parent)
        self._parent = parent
        self._profiles = profiles  # {name: ShakerProfile} — read-only
        self._active_name = active_name
        self._backend_available = bool(
            getattr(parent, "_shaker_backend_available", False))

        # Working profile dict — mutated as the user progresses.
        seed = profiles.get(active_name) or DEFAULT_PROFILE
        self._working: dict = asdict(seed)

        # Sweep state on page 2.
        self._sweep_thread: Optional[threading.Thread] = None
        self._sweep_stop_evt: Optional[threading.Event] = None
        self._sweep_marked = False
        self._sweep_skipped = False

        # Test-tone confirmed checkbox on page 1.
        self._routing_confirmed = False

        # Brake yes/no on page 6.
        self._brake_decision: Optional[bool] = None

        # Wizard return values.
        self.result_profile: Optional[ShakerProfile] = None
        self.result_overwrite: bool = False

        self.setWindowTitle("Shaker Calibration Wizard")
        self.setModal(True)
        self.resize(640, 540)

        self._build_ui()
        self._wire_buttons()

        # Live-frequency tick for the sweep page.
        self._sweep_tick = QTimer(self)
        self._sweep_tick.setInterval(50)
        self._sweep_tick.timeout.connect(self._on_sweep_tick)

        # If backend is unavailable, jump to a stub page that only allows Cancel.
        if not self._backend_available:
            self._show_backend_unavailable()
        else:
            self.stack.setCurrentIndex(0)
            self.manage_pages(0)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.stack.addWidget(self._page_preset())          # 0
        self.stack.addWidget(self._page_routing())         # 1
        self.stack.addWidget(self._page_sweep())           # 2
        self.stack.addWidget(self._page_carrier_ladder())  # 3
        self.stack.addWidget(self._page_halfwaves())       # 4
        self.stack.addWidget(self._page_envelope())        # 5
        self.stack.addWidget(self._page_brake())           # 6
        self.stack.addWidget(self._page_review())          # 7

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        nav = QHBoxLayout()
        self.lbl_step = QLabel("")
        nav.addWidget(self.lbl_step)
        nav.addStretch(1)
        self.pb_previous = QPushButton("Previous")
        self.pb_next = QPushButton("Next")
        self.pb_finish = QPushButton("Finish")
        self.pb_cancel = QPushButton("Cancel")
        nav.addWidget(self.pb_previous)
        nav.addWidget(self.pb_next)
        nav.addWidget(self.pb_finish)
        nav.addWidget(self.pb_cancel)
        outer.addLayout(nav)

    def _wire_buttons(self):
        self.pb_previous.clicked.connect(self._on_prev)
        self.pb_next.clicked.connect(self._on_next)
        self.pb_finish.clicked.connect(self._on_finish)
        self.pb_cancel.clicked.connect(self.reject)
        self.stack.currentChanged.connect(self.manage_pages)

    def _show_backend_unavailable(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Shaker backend not available.</b><br><br>"
            "The wizard cannot drive any audio output. Install / enable the "
            "shaker audio dependencies (sounddevice + an output device) and "
            "restart TelemFFB to use the calibration wizard."))
        v.addStretch(1)
        # Replace stack with this single page.
        while self.stack.count() > 0:
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.stack.addWidget(page)
        self.stack.setCurrentIndex(0)
        self.pb_previous.setEnabled(False)
        self.pb_next.setEnabled(False)
        self.pb_finish.setVisible(False)

    # ------------------------------------------------------------------
    # Page 0 — preset chooser
    # ------------------------------------------------------------------

    def _page_preset(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Step 1 / 8 — Pick a starting preset</b>"))
        v.addWidget(QLabel(
            "Choose the closest match for your bass shaker. "
            "The wizard pre-fills sensible defaults for the rest of the "
            "calibration; you only fine-tune from there. Pick "
            "<i>Generic / Unknown</i> if you do not recognise your model."))

        self._preset_group = QButtonGroup(page)
        names = list(self._profiles.keys())
        # Make sure there is always a "Generic" / fallback choice on top.
        ordered = []
        for preferred in ("Generic", "Dayton DAEX-25", "Buttkicker LFE"):
            if preferred in self._profiles and preferred not in ordered:
                ordered.append(preferred)
        for n in names:
            if n not in ordered:
                ordered.append(n)

        for idx, name in enumerate(ordered):
            rb = QRadioButton(name)
            prof = self._profiles[name]
            if prof.description:
                rb.setToolTip(prof.description)
            if name == self._active_name:
                rb.setChecked(True)
            self._preset_group.addButton(rb, idx)
            v.addWidget(rb)

        # Auto-select the first if nothing matched.
        if self._preset_group.checkedButton() is None and ordered:
            self._preset_group.button(0).setChecked(True)

        self._preset_names = ordered
        self._preset_group.buttonToggled.connect(self._on_preset_toggled)

        v.addStretch(1)
        return page

    def _on_preset_toggled(self, _btn, _checked):
        # Reseed the working profile from the chosen preset every time
        # the radio changes — easier than tracking deltas per page.
        idx = self._preset_group.checkedId()
        if idx < 0:
            return
        name = self._preset_names[idx]
        self._working = asdict(self._profiles[name])
        # Refresh dependent pages' widgets next time they show.
        self._refresh_envelope_widgets()
        self._refresh_review_widgets()

    # ------------------------------------------------------------------
    # Page 1 — routing sanity
    # ------------------------------------------------------------------

    def _page_routing(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Step 2 / 8 — Audio routing check</b>"))
        v.addWidget(QLabel(
            "Confirm the shaker reacts before tuning. Press <b>Play test "
            "tone</b> — you should feel a clear, short pulse. If you do not, "
            "go back to the System Settings tab and check the output device, "
            "master gain and channel routing first."))

        self._routing_summary = QLabel("")
        self._routing_summary.setStyleSheet("color: gray;")
        v.addWidget(self._routing_summary)

        row = QHBoxLayout()
        self.btn_routing_play = QPushButton("Play test tone")
        self.btn_routing_play.clicked.connect(self._on_routing_play)
        row.addWidget(self.btn_routing_play)
        row.addStretch(1)
        v.addLayout(row)

        self.cb_routing_confirm = QCheckBox(
            "I felt the test tone — proceed to calibration.")
        self.cb_routing_confirm.toggled.connect(self._on_routing_confirm)
        v.addWidget(self.cb_routing_confirm)

        v.addStretch(1)
        return page

    def _on_routing_play(self):
        if not self._backend_available:
            return
        args = self._neutral_pulse_args()
        _runtime.play_pulse(self._parent, args)

    def _on_routing_confirm(self, checked: bool):
        self._routing_confirmed = bool(checked)
        self._refresh_nav_state()

    def _refresh_routing_summary(self):
        try:
            dev = self._parent.shaker_device_combo.currentText()
            gain = float(self._parent.shaker_gain_spin.value())
            ch = self._parent.shaker_channel_combo.currentText()
            self._routing_summary.setText(
                f"Output: <b>{dev}</b>  ·  Gain: <b>{gain:.2f}</b>  ·  "
                f"Channel: <b>{ch}</b>")
        except Exception:
            log.exception("Failed to read routing summary from parent")

    # ------------------------------------------------------------------
    # Page 2 — resonance sweep
    # ------------------------------------------------------------------

    def _page_sweep(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Step 3 / 8 — Resonance sweep → f_res</b>"))
        v.addWidget(QLabel(
            "The sweep slowly rises through the frequency range below. Listen "
            "and feel for the loudest, buzziest spot — that is your shaker's "
            "mechanical resonance. When you hear it, press <b>Mark "
            "resonance</b> to capture that frequency. Every later setting is "
            "derived from this number, so take your time."))

        grp = QGroupBox("Sweep parameters")
        gv = QVBoxLayout(grp)

        self.sp_sweep_lo = QDoubleSpinBox()
        self.sp_sweep_lo.setRange(5.0, 50.0)
        self.sp_sweep_lo.setSingleStep(1.0)
        self.sp_sweep_lo.setSuffix(" Hz")
        self.sp_sweep_hi = QDoubleSpinBox()
        self.sp_sweep_hi.setRange(30.0, 200.0)
        self.sp_sweep_hi.setSingleStep(5.0)
        self.sp_sweep_hi.setSuffix(" Hz")
        self.sp_sweep_dur = QDoubleSpinBox()
        self.sp_sweep_dur.setRange(3.0, 20.0)
        self.sp_sweep_dur.setSingleStep(0.5)
        self.sp_sweep_dur.setSuffix(" s")
        self.sp_sweep_dur.setValue(9.0)
        self.sp_sweep_amp = QDoubleSpinBox()
        self.sp_sweep_amp.setRange(0.05, 1.0)
        self.sp_sweep_amp.setSingleStep(0.05)
        self.sp_sweep_amp.setDecimals(2)
        self.sp_sweep_amp.setValue(0.4)

        for label, w in (
                ("Low (Hz):", self.sp_sweep_lo),
                ("High (Hz):", self.sp_sweep_hi),
                ("Duration:", self.sp_sweep_dur),
                ("Amplitude:", self.sp_sweep_amp),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(w)
            row.addStretch(1)
            gv.addLayout(row)

        v.addWidget(grp)

        btn_row = QHBoxLayout()
        self.btn_sweep_start = QPushButton("Start sweep")
        self.btn_sweep_mark = QPushButton("Mark resonance")
        self.btn_sweep_mark.setEnabled(False)
        self.lbl_sweep_freq = QLabel("Sweep: --.- Hz")
        self.lbl_sweep_freq.setMinimumWidth(140)
        btn_row.addWidget(self.btn_sweep_start)
        btn_row.addWidget(self.btn_sweep_mark)
        btn_row.addWidget(self.lbl_sweep_freq)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        self.lbl_sweep_marked = QLabel("")
        self.lbl_sweep_marked.setStyleSheet("color: gray;")
        v.addWidget(self.lbl_sweep_marked)

        self.cb_sweep_skip = QCheckBox(
            "Skip sweep — keep the preset value instead.")
        self.cb_sweep_skip.toggled.connect(self._on_sweep_skip_toggled)
        v.addWidget(self.cb_sweep_skip)

        self.btn_sweep_start.clicked.connect(self._on_sweep_toggle)
        self.btn_sweep_mark.clicked.connect(self._on_sweep_mark)

        v.addStretch(1)
        return page

    def _on_enter_sweep_page(self):
        # Re-derive defaults from current preset's f_res_hz so the sweep
        # brackets the expected peak even if the user changed presets.
        f_res = float(self._working.get("f_res_hz", DEFAULT_PROFILE.f_res_hz))
        self.sp_sweep_lo.setValue(max(5.0, f_res * 0.5))
        self.sp_sweep_hi.setValue(min(200.0, f_res * 2.0))
        self._refresh_marked_label()

    def _on_sweep_toggle(self):
        if self._sweep_thread is not None:
            self._stop_sweep_async()
            return

        lo = float(self.sp_sweep_lo.value())
        hi = float(self.sp_sweep_hi.value())
        dur = float(self.sp_sweep_dur.value())
        amp = float(self.sp_sweep_amp.value())
        if hi <= lo:
            QMessageBox.warning(self, "Sweep",
                                "High frequency must be greater than low.")
            return

        self._parent._shaker_calib_sweep_current_freq = 0.0
        self._sweep_stop_evt = threading.Event()
        self.btn_sweep_start.setText("Stop sweep")
        self.btn_sweep_mark.setEnabled(True)
        self._sweep_tick.start()
        self._sweep_thread = _runtime.start_sweep(
            self._parent, lo, hi, dur, amp,
            self._sweep_stop_evt, self._on_sweep_finished)

    def _on_sweep_tick(self):
        f = float(getattr(self._parent, "_shaker_calib_sweep_current_freq", 0.0))
        if f > 0.0:
            self.lbl_sweep_freq.setText(f"Sweep: {f:.1f} Hz")

    def _on_sweep_mark(self):
        f = float(getattr(self._parent, "_shaker_calib_sweep_current_freq", 0.0))
        if f <= 0.0:
            return
        self._working["f_res_hz"] = f
        self._sweep_marked = True
        self.cb_sweep_skip.setChecked(False)
        self._refresh_marked_label()
        self._refresh_envelope_widgets()
        self._refresh_review_widgets()
        self._refresh_nav_state()

    def _refresh_marked_label(self):
        f = float(self._working.get("f_res_hz", 0.0))
        if self._sweep_marked or self._sweep_skipped:
            self.lbl_sweep_marked.setText(
                f"Captured f_res: <b>{f:.1f} Hz</b>")
        else:
            self.lbl_sweep_marked.setText(
                f"Current preset f_res: {f:.1f} Hz "
                "(skip to keep, or run a sweep and mark to override)")

    def _on_sweep_skip_toggled(self, checked: bool):
        self._sweep_skipped = bool(checked)
        if checked:
            # Keep whatever the preset seeded.
            self._sweep_marked = False
        self._refresh_marked_label()
        self._refresh_nav_state()

    def _stop_sweep_async(self):
        if self._sweep_stop_evt is not None:
            self._sweep_stop_evt.set()
        # _on_sweep_finished arrives on the GUI thread via QTimer.singleShot.

    def _on_sweep_finished(self):
        self._sweep_tick.stop()
        # Wait briefly for the worker thread to actually exit so the next
        # ShakerSynth instance does not race for the audio device.
        if self._sweep_thread is not None:
            self._sweep_thread.join(timeout=1.0)
        self._sweep_thread = None
        self._sweep_stop_evt = None
        self._parent._shaker_calib_sweep_current_freq = 0.0
        self.lbl_sweep_freq.setText("Sweep: --.- Hz")
        self.btn_sweep_start.setText("Start sweep")
        self.btn_sweep_mark.setEnabled(False)

    # ------------------------------------------------------------------
    # Page 3 — carrier offset ladder
    # ------------------------------------------------------------------

    def _page_carrier_ladder(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Step 4 / 8 — Carrier offset</b>"))
        v.addWidget(QLabel(
            "The carrier offset detunes the drive a few percent below f_res. "
            "A small offset shortens the decay tail and produces a crisper "
            "thud; 0 % drives exactly on resonance and rings the longest. "
            "Play each option and pick the one that feels the most defined."))

        self._carrier_group = QButtonGroup(page)
        for idx, off in enumerate(_CARRIER_LADDER):
            row = QHBoxLayout()
            btn = QPushButton(f"Play  {off:>4.0f} %")
            btn.setMinimumWidth(110)
            btn.clicked.connect(lambda _=False, o=off: self._play_with_offset(o))
            rb = QRadioButton(f"Pick {off:.0f} %")
            self._carrier_group.addButton(rb, idx)
            row.addWidget(btn)
            row.addWidget(rb)
            row.addStretch(1)
            v.addLayout(row)

        self._carrier_group.buttonToggled.connect(self._on_carrier_picked)
        v.addStretch(1)
        return page

    def _on_enter_carrier_page(self):
        # Pre-select the ladder rung closest to the working value.
        cur = float(self._working.get("carrier_offset_pct", 15.0))
        nearest = min(range(len(_CARRIER_LADDER)),
                      key=lambda i: abs(_CARRIER_LADDER[i] - cur))
        btn = self._carrier_group.button(nearest)
        if btn is not None:
            btn.setChecked(True)

    def _play_with_offset(self, offset_pct: float):
        if not self._backend_available:
            return
        args = self._working_pulse_args(carrier_offset_pct=offset_pct)
        _runtime.play_pulse(self._parent, args)

    def _on_carrier_picked(self, _btn, checked: bool):
        if not checked:
            return
        idx = self._carrier_group.checkedId()
        if idx < 0:
            return
        self._working["carrier_offset_pct"] = float(_CARRIER_LADDER[idx])
        self._refresh_envelope_widgets()
        self._refresh_review_widgets()
        self._refresh_nav_state()

    # ------------------------------------------------------------------
    # Page 4 — halfwaves ladder
    # ------------------------------------------------------------------

    def _page_halfwaves(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Step 5 / 8 — Halfwaves (pulse length)</b>"))
        v.addWidget(QLabel(
            "How many sine half-cycles each pulse lasts. <b>1</b> is the "
            "sharpest click — best for gear and hook clunks. <b>2–3</b> is a "
            "fuller punch — better for weapon release. Play each and pick the "
            "feel you want as the default for your hardware."))

        self._halfwaves_group = QButtonGroup(page)
        labels = {1: "1 — sharp click", 2: "2 — punchy", 3: "3 — thumpy"}
        for hw in _HALFWAVES_LADDER:
            row = QHBoxLayout()
            btn = QPushButton(f"Play  {hw}")
            btn.setMinimumWidth(110)
            btn.clicked.connect(lambda _=False, h=hw: self._play_with_halfwaves(h))
            rb = QRadioButton(f"Pick {labels[hw]}")
            self._halfwaves_group.addButton(rb, hw)
            row.addWidget(btn)
            row.addWidget(rb)
            row.addStretch(1)
            v.addLayout(row)

        self._halfwaves_group.buttonToggled.connect(self._on_halfwaves_picked)
        v.addStretch(1)
        return page

    def _on_enter_halfwaves_page(self):
        cur = int(self._working.get("halfwaves", 2))
        cur = max(1, min(3, cur))
        btn = self._halfwaves_group.button(cur)
        if btn is not None:
            btn.setChecked(True)

    def _play_with_halfwaves(self, hw: int):
        if not self._backend_available:
            return
        args = self._working_pulse_args(halfwaves=hw)
        _runtime.play_pulse(self._parent, args)

    def _on_halfwaves_picked(self, _btn, checked: bool):
        if not checked:
            return
        hw = self._halfwaves_group.checkedId()
        if hw < 1:
            return
        self._working["halfwaves"] = int(hw)
        self._refresh_envelope_widgets()
        self._refresh_review_widgets()
        self._refresh_nav_state()

    # ------------------------------------------------------------------
    # Page 5 — attack / release with live preview
    # ------------------------------------------------------------------

    def _page_envelope(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Step 6 / 8 — Attack &amp; release</b>"))
        v.addWidget(QLabel(
            "Attack is how quickly the pulse ramps up — keep it short "
            "(≤ 1 ms) for snappy clunks, longer for softer onsets. "
            "Release is how the tail fades — shorten it on resonant shakers "
            "to remove ringing, lengthen it for a more sustained feel. The "
            "preview updates live; press <b>Play current</b> to hear it."))

        from .ui.ShakerWaveformWidget import ShakerWaveformWidget
        self._wave_envelope = ShakerWaveformWidget()
        v.addWidget(self._wave_envelope)

        row = QHBoxLayout()
        row.addWidget(QLabel("Attack (ms):"))
        self.sp_attack = QDoubleSpinBox()
        self.sp_attack.setRange(0.1, 20.0)
        self.sp_attack.setSingleStep(0.1)
        self.sp_attack.setDecimals(1)
        self.sp_attack.valueChanged.connect(self._on_envelope_changed)
        row.addWidget(self.sp_attack)
        row.addSpacerItem(QSpacerItem(20, 1, QSizePolicy.Policy.Fixed,
                                      QSizePolicy.Policy.Minimum))
        row.addWidget(QLabel("Release (ms):"))
        self.sp_release = QDoubleSpinBox()
        self.sp_release.setRange(0.1, 20.0)
        self.sp_release.setSingleStep(0.1)
        self.sp_release.setDecimals(1)
        self.sp_release.valueChanged.connect(self._on_envelope_changed)
        row.addWidget(self.sp_release)
        row.addStretch(1)
        v.addLayout(row)

        play_row = QHBoxLayout()
        self.btn_envelope_play = QPushButton("Play current")
        self.btn_envelope_play.clicked.connect(self._on_envelope_play)
        play_row.addWidget(self.btn_envelope_play)
        play_row.addStretch(1)
        v.addLayout(play_row)

        v.addStretch(1)
        return page

    def _on_enter_envelope_page(self):
        # Smart defaults if the seed was Generic / Unknown: release ≈ half
        # period of f_res, clamped to spinbox range.
        f_res = max(1.0, float(self._working.get("f_res_hz", 45.0)))
        if self._working.get("attack_ms", 0.0) <= 0.0:
            self._working["attack_ms"] = 0.7
        if self._working.get("release_ms", 0.0) <= 0.0:
            self._working["release_ms"] = max(1.0, 500.0 / f_res)

        self.sp_attack.blockSignals(True)
        self.sp_release.blockSignals(True)
        self.sp_attack.setValue(float(self._working["attack_ms"]))
        self.sp_release.setValue(float(self._working["release_ms"]))
        self.sp_attack.blockSignals(False)
        self.sp_release.blockSignals(False)
        self._refresh_envelope_widgets()

    def _on_envelope_changed(self, *_):
        self._working["attack_ms"] = float(self.sp_attack.value())
        self._working["release_ms"] = float(self.sp_release.value())
        self._refresh_envelope_widgets()
        self._refresh_review_widgets()

    def _on_envelope_play(self):
        if not self._backend_available:
            return
        _runtime.play_pulse(self._parent, self._working_pulse_args())

    def _refresh_envelope_widgets(self):
        # The waveform widget may not exist yet during __init__.
        wave = getattr(self, "_wave_envelope", None)
        if wave is None:
            return
        self._paint_waveform(wave)

    # ------------------------------------------------------------------
    # Page 6 — brake decision and tuning
    # ------------------------------------------------------------------

    def _page_brake(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Step 7 / 8 — Active braking</b>"))
        v.addWidget(QLabel(
            "After the main pulse, does the cone leave a boomy <i>boom-m-m</i> "
            "tail you can clearly hear over the pulse itself? Active braking "
            "fires a short counter-pulse that cancels that ringing. If the "
            "release alone already kills the tail, leave braking off."))

        decide_row = QHBoxLayout()
        self.rb_brake_no = QRadioButton("No tail — leave brake off")
        self.rb_brake_yes = QRadioButton("Yes — tune brake")
        self._brake_decide_group = QButtonGroup(page)
        self._brake_decide_group.addButton(self.rb_brake_no, 0)
        self._brake_decide_group.addButton(self.rb_brake_yes, 1)
        self._brake_decide_group.buttonToggled.connect(self._on_brake_decide)
        decide_row.addWidget(self.rb_brake_no)
        decide_row.addWidget(self.rb_brake_yes)
        decide_row.addStretch(1)
        v.addLayout(decide_row)

        self._brake_grp = QGroupBox("Brake parameters")
        self._brake_grp.setEnabled(False)
        bv = QVBoxLayout(self._brake_grp)

        amp_row = QHBoxLayout()
        amp_row.addWidget(QLabel("Brake amplitude:"))
        self.sl_brake_amp = QSlider(Qt.Orientation.Horizontal)
        self.sl_brake_amp.setRange(0, 100)
        self.lbl_brake_amp = QLabel("0%")
        self.lbl_brake_amp.setMinimumWidth(40)
        self.sl_brake_amp.valueChanged.connect(self._on_brake_amp_changed)
        amp_row.addWidget(self.sl_brake_amp, 1)
        amp_row.addWidget(self.lbl_brake_amp)
        bv.addLayout(amp_row)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Brake delay (ms):"))
        self.sp_brake_delay = QDoubleSpinBox()
        self.sp_brake_delay.setRange(0.0, 20.0)
        self.sp_brake_delay.setSingleStep(0.1)
        self.sp_brake_delay.setDecimals(1)
        self.sp_brake_delay.valueChanged.connect(self._on_brake_delay_changed)
        delay_row.addWidget(self.sp_brake_delay)
        delay_row.addStretch(1)
        bv.addLayout(delay_row)

        play_row = QHBoxLayout()
        self.btn_brake_play = QPushButton("Play with brake")
        self.btn_brake_play.clicked.connect(self._on_brake_play)
        play_row.addWidget(self.btn_brake_play)
        play_row.addStretch(1)
        bv.addLayout(play_row)

        v.addWidget(self._brake_grp)
        v.addStretch(1)
        return page

    def _on_enter_brake_page(self):
        # Pre-fill suggestions from f_res; preserve preset values otherwise.
        f_res = max(1.0, float(self._working.get("f_res_hz", 45.0)))
        suggested_delay = max(0.0, min(20.0, 1000.0 / (2.0 * f_res)))
        if self._working.get("brake_delay_ms", 0.0) <= 0.0:
            self._working["brake_delay_ms"] = suggested_delay
        if self._working.get("brake_amp_pct", 0.0) <= 0.0:
            self._working["brake_amp_pct"] = 40.0

        # Restore the radio choice if we already visited this page.
        if self._brake_decision is True:
            self.rb_brake_yes.setChecked(True)
        elif self._brake_decision is False:
            self.rb_brake_no.setChecked(True)
        elif self._working.get("brake_enabled"):
            self.rb_brake_yes.setChecked(True)

        amp = int(round(float(self._working.get("brake_amp_pct", 40.0))))
        self.sl_brake_amp.blockSignals(True)
        self.sp_brake_delay.blockSignals(True)
        self.sl_brake_amp.setValue(amp)
        self.lbl_brake_amp.setText(f"{amp}%")
        self.sp_brake_delay.setValue(float(self._working["brake_delay_ms"]))
        self.sl_brake_amp.blockSignals(False)
        self.sp_brake_delay.blockSignals(False)

    def _on_brake_decide(self, _btn, checked: bool):
        if not checked:
            return
        choice = self._brake_decide_group.checkedId()
        if choice == 1:
            self._brake_decision = True
            self._working["brake_enabled"] = True
            self._brake_grp.setEnabled(True)
        else:
            self._brake_decision = False
            self._working["brake_enabled"] = False
            self._brake_grp.setEnabled(False)
        self._refresh_review_widgets()
        self._refresh_nav_state()

    def _on_brake_amp_changed(self, v: int):
        self.lbl_brake_amp.setText(f"{int(v)}%")
        self._working["brake_amp_pct"] = float(v)
        self._refresh_review_widgets()

    def _on_brake_delay_changed(self, v: float):
        self._working["brake_delay_ms"] = float(v)
        self._refresh_review_widgets()

    def _on_brake_play(self):
        if not self._backend_available:
            return
        _runtime.play_pulse(self._parent, self._working_pulse_args())

    # ------------------------------------------------------------------
    # Page 7 — review and save
    # ------------------------------------------------------------------

    def _page_review(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel(
            "<b>Step 8 / 8 — Review &amp; save</b>"))
        v.addWidget(QLabel(
            "Final values below. Press <b>Play final</b> for one last "
            "audition, give the profile a name, and click <b>Finish</b> to "
            "save. The profile appears in the System Settings shaker dropdown "
            "and becomes active immediately. Restart the shaker child instance "
            "for in-flight effects to pick up the new shape."))

        from .ui.ShakerWaveformWidget import ShakerWaveformWidget
        self._wave_review = ShakerWaveformWidget()
        v.addWidget(self._wave_review)

        self._review_summary = QLabel("")
        self._review_summary.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self._review_summary)

        play_row = QHBoxLayout()
        self.btn_review_play = QPushButton("Play final")
        self.btn_review_play.clicked.connect(self._on_review_play)
        play_row.addWidget(self.btn_review_play)
        play_row.addStretch(1)
        v.addLayout(play_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Profile name:"))
        self.le_name = QLineEdit()
        self.le_name.textChanged.connect(self._on_review_name_changed)
        name_row.addWidget(self.le_name, 1)
        v.addLayout(name_row)

        self._overwrite_group = QButtonGroup(page)
        self.rb_save_new = QRadioButton("Save as new profile")
        self.rb_overwrite = QRadioButton("Overwrite existing profile")
        self.rb_save_new.setChecked(True)
        self._overwrite_group.addButton(self.rb_save_new, 0)
        self._overwrite_group.addButton(self.rb_overwrite, 1)
        self._overwrite_group.buttonToggled.connect(
            lambda *_: self._refresh_nav_state())
        ow_row = QHBoxLayout()
        ow_row.addWidget(self.rb_save_new)
        ow_row.addWidget(self.rb_overwrite)
        ow_row.addStretch(1)
        v.addLayout(ow_row)

        self.lbl_review_error = QLabel("")
        self.lbl_review_error.setStyleSheet("color: #b22; font-style: italic;")
        v.addWidget(self.lbl_review_error)

        v.addStretch(1)
        return page

    def _on_enter_review_page(self):
        # Default name = preset + "(calibrated)" unless we already filled it.
        if not self.le_name.text().strip():
            base = self._active_name or self._preset_names[0]
            self.le_name.setText(f"{base} (calibrated)")
        # Default overwrite radio: if the typed name matches the seed
        # exactly, suggest overwrite; otherwise suggest save-new.
        if self.le_name.text().strip() == self._active_name:
            self.rb_overwrite.setChecked(True)
        self._refresh_review_widgets()

    def _on_review_name_changed(self, _text: str):
        self._refresh_review_widgets()
        self._refresh_nav_state()

    def _on_review_play(self):
        if not self._backend_available:
            return
        _runtime.play_pulse(self._parent, self._working_pulse_args())

    def _refresh_review_widgets(self):
        wave = getattr(self, "_wave_review", None)
        if wave is not None:
            self._paint_waveform(wave)

        summary = getattr(self, "_review_summary", None)
        if summary is None:
            return
        w = self._working
        brake_line = (
            f"<b>Brake:</b> on  ·  amp {w.get('brake_amp_pct', 0):.0f} %  ·  "
            f"delay {w.get('brake_delay_ms', 0):.1f} ms"
            if w.get("brake_enabled") else "<b>Brake:</b> off")
        summary.setText(
            f"<b>f_res:</b> {w.get('f_res_hz', 0):.1f} Hz<br>"
            f"<b>Carrier offset:</b> {w.get('carrier_offset_pct', 0):.1f} %<br>"
            f"<b>Halfwaves:</b> {int(w.get('halfwaves', 0))}<br>"
            f"<b>Attack:</b> {w.get('attack_ms', 0):.1f} ms  ·  "
            f"<b>Release:</b> {w.get('release_ms', 0):.1f} ms<br>"
            f"{brake_line}")

        # Inline name validation feedback.
        err = self._validate_review_name(self.le_name.text().strip())
        self.lbl_review_error.setText(err or "")

    def _validate_review_name(self, name: str) -> str:
        if not name:
            return "Profile name cannot be empty."
        if name.lower() == "default":
            return "Profile name 'default' is reserved."
        if name in self._profiles and not self.rb_overwrite.isChecked():
            return (f"A profile named '{name}' already exists — choose "
                    "Overwrite or pick a different name.")
        return ""

    # ------------------------------------------------------------------
    # Navigation logic
    # ------------------------------------------------------------------

    def manage_pages(self, idx: int):
        # Page-specific on-enter setup.
        if idx == _PAGE_ROUTING:
            self._refresh_routing_summary()
        elif idx == _PAGE_SWEEP:
            self._on_enter_sweep_page()
        elif idx == _PAGE_CARRIER:
            self._on_enter_carrier_page()
        elif idx == _PAGE_HALFWAVES:
            self._on_enter_halfwaves_page()
        elif idx == _PAGE_ENVELOPE:
            self._on_enter_envelope_page()
        elif idx == _PAGE_BRAKE:
            self._on_enter_brake_page()
        elif idx == _PAGE_REVIEW:
            self._on_enter_review_page()

        self.lbl_step.setText(f"Step {idx + 1} of {_PAGE_COUNT}")
        self._refresh_nav_state()

    def _refresh_nav_state(self):
        idx = self.stack.currentIndex()
        on_review = (idx == _PAGE_REVIEW)
        self.pb_previous.setEnabled(idx > 0)
        self.pb_previous.setVisible(self._backend_available)
        self.pb_next.setVisible(self._backend_available and not on_review)
        self.pb_finish.setVisible(self._backend_available and on_review)
        if on_review:
            err = self._validate_review_name(self.le_name.text().strip())
            self.pb_finish.setEnabled(err == "")
        self.pb_next.setEnabled(self._backend_available
                                and self._can_advance_from(idx))

    def _can_advance_from(self, idx: int) -> bool:
        if idx == _PAGE_PRESET:
            return self._preset_group.checkedId() >= 0
        if idx == _PAGE_ROUTING:
            return self._routing_confirmed
        if idx == _PAGE_SWEEP:
            return self._sweep_marked or self._sweep_skipped
        if idx == _PAGE_CARRIER:
            return self._carrier_group.checkedId() >= 0
        if idx == _PAGE_HALFWAVES:
            return self._halfwaves_group.checkedId() >= 1
        if idx == _PAGE_ENVELOPE:
            return True
        if idx == _PAGE_BRAKE:
            return self._brake_decision is not None
        return True

    def _on_prev(self):
        idx = self.stack.currentIndex()
        if idx == _PAGE_SWEEP and self._sweep_thread is not None:
            self._stop_sweep_async()
        if idx > 0:
            self.stack.setCurrentIndex(idx - 1)

    def _on_next(self):
        idx = self.stack.currentIndex()
        if idx == _PAGE_SWEEP and self._sweep_thread is not None:
            self._stop_sweep_async()
        if idx < _PAGE_COUNT - 1:
            self.stack.setCurrentIndex(idx + 1)

    def _on_finish(self):
        name = self.le_name.text().strip()
        err = self._validate_review_name(name)
        if err:
            self.lbl_review_error.setText(err)
            return

        # Build an immutable ShakerProfile from the working dict, taking
        # description/notes/created_iso from the seeded profile so we keep
        # any preset metadata intact when the user just renames a clone.
        seed_name = self._preset_names[max(0, self._preset_group.checkedId())]
        seed = self._profiles.get(seed_name) or DEFAULT_PROFILE
        existing = self._profiles.get(name)
        meta_src = existing if existing is not None else seed
        try:
            self.result_profile = ShakerProfile(
                name=name,
                schema_version=1,
                description=meta_src.description,
                f_res_hz=float(self._working["f_res_hz"]),
                carrier_offset_pct=float(self._working["carrier_offset_pct"]),
                halfwaves=int(self._working["halfwaves"]),
                attack_ms=float(self._working["attack_ms"]),
                release_ms=float(self._working["release_ms"]),
                gain=float(self._working.get("gain", 1.0)),
                brake_enabled=bool(self._working["brake_enabled"]),
                brake_amp_pct=float(self._working["brake_amp_pct"]),
                brake_delay_ms=float(self._working["brake_delay_ms"]),
                created_iso=meta_src.created_iso,
                notes=meta_src.notes,
            )
        except Exception:
            log.exception("Failed to build ShakerProfile from wizard state")
            QMessageBox.critical(
                self, "Save profile",
                "Could not assemble the calibrated profile — see log.")
            return

        self.result_overwrite = bool(self.rb_overwrite.isChecked())
        self.accept()

    # ------------------------------------------------------------------
    # Pulse arg helpers + waveform painting
    # ------------------------------------------------------------------

    def _working_pulse_args(self, **overrides) -> dict:
        f_res = float(self._working.get("f_res_hz", 45.0))
        offset = float(overrides.get(
            "carrier_offset_pct",
            self._working.get("carrier_offset_pct", 15.0)))
        carrier_hz = max(1.0, f_res * (1.0 + offset / 100.0))
        amp = float(overrides.get("amplitude", 0.7))
        halfwaves = int(overrides.get(
            "halfwaves", self._working.get("halfwaves", 2)))
        attack_ms = float(overrides.get(
            "attack_ms", self._working.get("attack_ms", 1.5)))
        release_ms = float(overrides.get(
            "release_ms", self._working.get("release_ms", 2.0)))
        brake_on = (overrides.get("brake_enabled",
                                  self._working.get("brake_enabled", False)))
        brake_amp_pct = float(overrides.get(
            "brake_amp_pct", self._working.get("brake_amp_pct", 0.0)))
        brake_delay_ms = float(overrides.get(
            "brake_delay_ms", self._working.get("brake_delay_ms", 0.0)))
        brake_amp = (brake_amp_pct / 100.0) * amp if brake_on else 0.0
        if not brake_on:
            brake_delay_ms = 0.0
        return dict(
            carrier_hz=carrier_hz,
            halfwaves=halfwaves,
            amplitude=amp,
            attack_ms=attack_ms,
            release_ms=release_ms,
            brake_amp=brake_amp,
            brake_delay_ms=brake_delay_ms,
        )

    def _neutral_pulse_args(self) -> dict:
        # A safe, audible test pulse for the routing page that does not
        # depend on any in-progress calibration.
        return dict(
            carrier_hz=45.0,
            halfwaves=2,
            amplitude=0.5,
            attack_ms=1.5,
            release_ms=2.0,
            brake_amp=0.0,
            brake_delay_ms=0.0,
        )

    def _paint_waveform(self, widget):
        try:
            import numpy as np
            from .hw.shaker_synth import build_pulse_envelope
        except Exception:
            return
        args = self._working_pulse_args()
        sr = 48000
        drive_env, brake_signal, drive_end, brake_start, brake_end = (
            build_pulse_envelope(sr, args["carrier_hz"], args["halfwaves"],
                                 args["amplitude"], args["attack_ms"],
                                 args["release_ms"], args["brake_amp"],
                                 args["brake_delay_ms"]))
        total = max(brake_end, drive_end)
        if total <= 0:
            return
        buf = np.zeros(total, dtype=np.float32)
        if drive_end > 0:
            t = np.arange(drive_end, dtype=np.float64) / sr
            sine = np.sin(2.0 * np.pi * args["carrier_hz"] * t)
            buf[:drive_end] = (sine * drive_env).astype(np.float32)
        if brake_signal.size > 0 and brake_end > brake_start:
            buf[brake_start:brake_end] = brake_signal.astype(np.float32)
        widget.set_buffer(buf, drive_end, brake_start, brake_end)

    # ------------------------------------------------------------------
    # Lifecycle — make sure we never leak the sweep thread
    # ------------------------------------------------------------------

    def reject(self):
        if self._sweep_thread is not None:
            self._stop_sweep_async()
            self._sweep_thread.join(timeout=1.0)
        super().reject()

    def closeEvent(self, e):
        if self._sweep_thread is not None:
            self._stop_sweep_async()
            self._sweep_thread.join(timeout=1.0)
        super().closeEvent(e)
