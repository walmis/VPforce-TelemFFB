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

"""TelemetryPlotWindow: the window of the plot process.

Hosts a ``TelemetryPlotWidget`` under a toolbar (window length, pause,
tile reset, clear, CSV export).  Frames and the marked keys arrive through
``add_frame`` and ``set_keys`` from the feed listener (``telemffb.plot.app``);
the frame times are the sender's clock, shown relative to the first frame.
Closing hides; the buffered data survives until Clear.
"""

import logging
from typing import Iterable, List, Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QComboBox, QFileDialog, QLabel, QMainWindow

from telemffb.ui.widgets.TelemetryPlotWidget import WINDOW_CHOICES, TelemetryPlotWidget


class TelemetryPlotWindow(QMainWindow):
    """A plot of the Monitor page's marked telemetry keys."""

    def __init__(self, device: str = '', parent=None):
        super().__init__(parent)
        self._keys: List[str] = []
        self._t0: Optional[float] = None
        self.setWindowTitle(f"Telemetry Plot ({device})" if device else "Telemetry Plot")
        self.resize(1100, 640)

        self._plot = TelemetryPlotWidget(self)
        self.setCentralWidget(self._plot)

        toolbar = self.addToolBar("Plot")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)

        toolbar.addWidget(QLabel(" Window "))
        self._window_box = QComboBox()
        for seconds in WINDOW_CHOICES:
            self._window_box.addItem(f"{seconds} s", seconds)
        self._window_box.setCurrentIndex(WINDOW_CHOICES.index(int(self._plot.window_seconds())))
        self._window_box.currentIndexChanged.connect(self._on_window_changed)
        toolbar.addWidget(self._window_box)
        toolbar.addSeparator()

        self._pause_action = toolbar.addAction("Pause")
        self._pause_action.setCheckable(True)
        self._pause_action.setToolTip("Stop following the latest frame; data keeps buffering behind it.\n"
                                      "A drag, wheel or right-drag zoom in the plot pauses too.")
        self._pause_action.toggled.connect(self._on_pause_toggled)
        self._plot.pausedChanged.connect(self._on_plot_paused)

        self._previous_action = toolbar.addAction("Previous zoom")
        self._previous_action.setShortcut(QKeySequence(QtCore.Qt.Key.Key_Backspace))
        self._previous_action.setToolTip("The span shown before the last zoom or pan (Backspace)")
        self._previous_action.setEnabled(False)
        self._previous_action.triggered.connect(self._plot.previous_zoom)

        reset_action = toolbar.addAction("Reset tiles")
        reset_action.setToolTip("One tile per series again, in series order")
        reset_action.triggered.connect(self._plot.reset_tiles)

        clear_action = toolbar.addAction("Clear")
        clear_action.setToolTip("Drop every buffered sample")
        clear_action.triggered.connect(self._plot.clear)

        export_action = toolbar.addAction("Export CSV")
        export_action.setToolTip("Write every buffered sample to a CSV file, one row per frame")
        export_action.triggered.connect(self._on_export)

        open_action = toolbar.addAction("Open CSV")
        open_action.setToolTip("Load an exported CSV in place of the live feed; Back to live discards it")
        open_action.triggered.connect(self._on_open)

        self._play_action = toolbar.addAction("Play")
        self._play_action.setCheckable(True)
        self._play_action.setEnabled(False)
        self._play_action.setToolTip("Replay the loaded recording from its start at real-time speed")
        self._play_action.toggled.connect(self._on_play_toggled)
        self._plot.recordingChanged.connect(self._on_recording_changed)
        self._plot.replayChanged.connect(self._on_replay_changed)

        toolbar.addSeparator()
        self._hint = QLabel(" Mark items in the Monitor table; drag a series onto a tile to overlay it ")
        toolbar.addWidget(self._hint)

    # ---- feed -------------------------------------------------------------------

    def set_keys(self, keys: Iterable[str]) -> None:
        """The marked keys: series of keys no longer marked leave the plot.
        Newly marked keys appear with their next frame."""
        self._keys = list(keys)
        self._plot.drop_series_not_in(self._keys)

    def add_frame(self, t: float, values: dict) -> None:
        """One frame's marked keys at the sender's time ``t``.  Dropped
        while a recording is loaded."""
        if self._plot.is_recording():
            return
        if self._t0 is None:
            self._t0 = t
        try:
            self._plot.add_frame(t - self._t0, values, self._keys or list(values))
        except Exception:
            logging.exception("Telemetry plot: frame not recorded")

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """Hide rather than destroy: the buffer and the tiles survive a
        close, and the Plot button shows the same window again."""
        event.ignore()
        self.hide()

    # ---- toolbar -----------------------------------------------------------------

    def _on_window_changed(self, index: int) -> None:
        self._plot.set_window_seconds(self._window_box.itemData(index))

    def _on_pause_toggled(self, paused: bool) -> None:
        self._plot.set_paused(paused)
        self._show_paused(paused)

    def _show_paused(self, paused: bool) -> None:
        self._pause_action.setText("Back to live" if paused else "Pause")
        self._previous_action.setEnabled(paused)

    def _on_plot_paused(self, paused: bool) -> None:
        """The plot paused itself (a mouse zoom or pan): show it on the
        button without re-entering the plot."""
        if self._pause_action.isChecked() != paused:
            self._pause_action.blockSignals(True)
            self._pause_action.setChecked(paused)
            self._pause_action.blockSignals(False)
        self._show_paused(paused)

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open telemetry plot CSV", "",
                                              "CSV files (*.csv)")
        if not path:
            return
        try:
            rows = self._plot.load_csv(path)
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "Open failed", str(e))
            return
        self._hint.setText(f" {rows} rows from {path}; Back to live discards the recording ")

    def _on_recording_changed(self, loaded: bool) -> None:
        self._play_action.setEnabled(loaded)
        if not loaded:
            # live frames start a fresh time base
            self._t0 = None
            self._hint.setText(" Mark items in the Monitor table; drag a series onto a tile to overlay it ")

    def _on_play_toggled(self, play: bool) -> None:
        if play:
            self._plot.start_replay()
        else:
            self._plot.stop_replay()

    def _on_replay_changed(self, replaying: bool) -> None:
        if self._play_action.isChecked() != replaying:
            self._play_action.blockSignals(True)
            self._play_action.setChecked(replaying)
            self._play_action.blockSignals(False)
        self._play_action.setText("Stop" if replaying else "Play")

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export telemetry plot", "telemetry.csv",
                                              "CSV files (*.csv)")
        if not path:
            return
        try:
            rows = self._plot.write_csv(path)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(e))
            return
        self._hint.setText(f" {rows} rows written to {path} ")
