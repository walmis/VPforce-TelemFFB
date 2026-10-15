"""The force direction calibration dialog: drives a
``ForceFrameCalibrator`` on a timer, shows its prompts and the live
stick position, and hands back the frame it derives."""
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout,
)

from telemffb.calibration.force_frame import (
    CalibrationDevice, ForceFrameCalibrator, Stage, Step,
)
from telemffb.ui.theme.tokens import DISABLED_GRAY, ERROR_RED, INFO_BLUE, OK_GREEN
from telemffb.ui.widgets.AxisPositionWidget import AxisPositionWidget


class ForceFrameCalibrationDialog(QDialog):
    """Runs one calibration against ``device``.  ``result`` holds the
    derived ``ForceFrame`` when the dialog is accepted; cancelling or
    closing at any point stops the run and leaves the device as it was."""

    TICK_MS = 20

    STAGE_COLORS = {'pending': DISABLED_GRAY, 'active': INFO_BLUE,
                    'done': OK_GREEN, 'failed': ERROR_RED}

    def __init__(self, device: CalibrationDevice, parent=None, device_name=''):
        super().__init__(parent)
        self.setWindowTitle("Detect Force Directions")
        self.setWindowFlags(self.windowFlags()
                            & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._device = device
        self.calibrator = ForceFrameCalibrator(device)
        self.result = None
        self._close_when_finished = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._build_ui(device_name)
        self._refresh()

    def _build_ui(self, device_name):
        root = QVBoxLayout(self)
        intro = QLabel(
            "<b>%s</b><br><br>"
            "This calibration routine measures how the device renders springs and directed "
            "forces and populates the force correction options automatically (if required).<br><br>"
            "<b>Keep your hands off</b> the stick except when asked to move "
            "the axis in a particular direction" % (device_name or "DirectInput device"))
        intro.setWordWrap(True)
        root.addWidget(intro)

        tracker = QHBoxLayout()
        self._stage_labels = {}
        for stage in (Stage.INPUT, Stage.SPRINGS, Stage.DIRECTIONS, Stage.VERIFY):
            label = QLabel(stage.value)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._stage_labels[stage] = label
            tracker.addWidget(label)
        root.addLayout(tracker)

        body = QHBoxLayout()
        self.position = AxisPositionWidget(self)
        body.addWidget(self.position, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(6)
        self.headline = QLabel("Ready")
        self.headline.setWordWrap(True)
        self.headline.setStyleSheet("font-size: 11pt; font-weight: bold;")
        text.addWidget(self.headline)
        self.prompt = QLabel("Press Start when ready.")
        self.prompt.setWordWrap(True)
        self.prompt.setMinimumHeight(48)
        self.prompt.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        text.addWidget(self.prompt)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        text.addWidget(self.detail)
        text.addStretch(1)
        body.addLayout(text, 1)
        root.addLayout(body)

        self.buttons = QDialogButtonBox()
        self.btn_start = self.buttons.addButton(
            "Start", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_close = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.btn_start.clicked.connect(self._start)
        self.btn_close.clicked.connect(self._close_clicked)
        root.addWidget(self.buttons)
        self.setMinimumWidth(520)

    # ------------------------------------------------------------------

    def _start(self):
        """Start a run; after one has ended, a fresh run on the same
        device (a push in the wrong direction is worth a second try
        without reopening the dialog)."""
        if self.calibrator.finished:
            self.calibrator = ForceFrameCalibrator(self._device)
            self.result = None
            self._close_when_finished = False
        self.btn_start.setEnabled(False)
        self.calibrator.start(time.monotonic())
        self._timer.start()
        self._refresh()

    def _tick(self):
        self.calibrator.advance(time.monotonic())
        self._refresh()
        if self.calibrator.finished:
            self._timer.stop()
            if self.calibrator.step is Step.DONE:
                self.result = self.calibrator.result
            self.btn_close.setText("Close")
            self.btn_start.setText("Run again")
            self.btn_start.setEnabled(True)
            if self._close_when_finished:
                self._close_clicked()

    def _close_clicked(self):
        """Cancel centers the stick first and closes when that is done;
        after a run it is Close."""
        cal = self.calibrator
        if cal.running:
            self._close_when_finished = True
            cal.cancel(time.monotonic())
            self._refresh()
            return
        if cal.step is Step.DONE and self.result is not None:
            self.accept()
        else:
            self.reject()

    def _refresh(self):
        cal = self.calibrator
        for stage, state in cal.stage_states().items():
            label = self._stage_labels[stage]
            label.setStyleSheet("color: %s; font-weight: %s;" % (
                self.STAGE_COLORS[state], 'bold' if state == 'active' else 'normal'))
        if cal.step is not Step.IDLE:
            self.headline.setText(cal.headline)
            self.prompt.setText(cal.prompt)
        self.detail.setText(cal.detail)
        self.position.set_position(cal.position)
        self.position.set_reference(cal.rest if cal.running else None)

    def done(self, result):
        # the window closing under a run (Escape, the title bar): no more
        # ticks are coming, so the effects stop at once
        self._timer.stop()
        self.calibrator.cancel()
        super().done(result)
