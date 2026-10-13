"""ForceFrameCalibrationDialog: it drives the calibrator on its timer and
closing it at any point cancels the run and releases the device."""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.calibration.force_frame import Step
from telemffb.hw.ffb_dinput import ForceFrame
from telemffb.ui.dialogs import ForceFrameCalibrationDialog as dialog_module
from telemffb.ui.dialogs.ForceFrameCalibrationDialog import ForceFrameCalibrationDialog

pytestmark = [pytest.mark.unit, pytest.mark.joystick]


class StubDevice:
    def __init__(self):
        self.axes = (0.0, 0.0)
        self.released = False
        self.frames = []

    def read_axes(self):
        return self.axes

    def set_frame(self, frame):
        self.frames.append(frame)

    def spring(self, x, y):
        pass

    def constant(self, magnitude, direction_deg):
        pass

    def stop_all(self):
        pass

    def release(self):
        self.released = True


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _accept_warning(monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok))


class FakeClock:
    """time.monotonic that advances one dialog tick per call."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        self.now += ForceFrameCalibrationDialog.TICK_MS / 1000.0
        return self.now


class TestDialog:
    def test_start_runs_the_calibrator_and_shows_its_prompt(self, app, monkeypatch):
        _accept_warning(monkeypatch)
        device = StubDevice()
        dlg = ForceFrameCalibrationDialog(device)
        dlg._start()
        assert dlg.calibrator.running
        assert dlg.calibrator.step is Step.INPUT_FORWARD
        dlg._tick()
        assert dlg.headline.text() == dlg.calibrator.headline
        assert dlg.prompt.text() == dlg.calibrator.prompt
        assert not dlg.btn_start.isEnabled()

    def test_closing_mid_run_cancels_and_releases(self, app, monkeypatch):
        _accept_warning(monkeypatch)
        device = StubDevice()
        dlg = ForceFrameCalibrationDialog(device)
        dlg._start()
        dlg._tick()
        dlg.reject()
        assert dlg.calibrator.step is Step.CANCELLED
        assert device.released and device.frames[-1] is None
        assert dlg.result is None

    def test_the_cancel_button_centers_the_stick_then_closes(self, app, monkeypatch):
        _accept_warning(monkeypatch)
        device = StubDevice()
        dlg = ForceFrameCalibrationDialog(device)
        monkeypatch.setattr(dialog_module, 'time', FakeClock())
        dlg._start()
        dlg._tick()
        rejected = []
        dlg.rejected.connect(lambda: rejected.append(1))
        dlg._close_clicked()
        assert dlg.calibrator.parking and not device.released
        assert rejected == []
        for _ in range(400):                       # the centering runs out
            dlg._tick()
            if dlg.calibrator.finished:
                break
        assert dlg.calibrator.step is Step.CANCELLED
        assert device.released and rejected == [1]

    def test_a_failed_run_can_be_started_again(self, app, monkeypatch):
        _accept_warning(monkeypatch)
        device = StubDevice()
        dlg = ForceFrameCalibrationDialog(device)
        dlg._start()
        first = dlg.calibrator
        first._fail('no_push')                      # ends at once: no clock
        dlg._tick()
        assert first.finished and dlg.btn_start.isEnabled()
        dlg._start()
        assert dlg.calibrator is not first
        assert dlg.calibrator.running and dlg.calibrator.step is Step.INPUT_FORWARD
        assert dlg.result is None

    def test_a_finished_run_hands_back_its_frame(self, app, monkeypatch):
        _accept_warning(monkeypatch)
        device = StubDevice()
        dlg = ForceFrameCalibrationDialog(device)
        dlg._start()
        cal = dlg.calibrator
        cal.candidate = ForceFrame(False, True, 90)
        cal._enter(Step.DONE, None)
        dlg._tick()
        assert dlg.result == ForceFrame(False, True, 90)
        results = []
        dlg.accepted.connect(lambda: results.append(dlg.result))
        dlg._close_clicked()               # the Close button after success
        assert results == [ForceFrame(False, True, 90)]
