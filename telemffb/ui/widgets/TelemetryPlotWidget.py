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

"""TelemetryPlotWidget: live plots of marked telemetry keys, on pyqtgraph.

The plotted series are listed on the left with their live values; on the
right, the *tiles*, two to a row on a shared time axis.  A new series gets a
tile of its own.  Dragging a series from the list onto a tile adds it to
that tile (an overlay on a shared y-axis); dropping it on empty space, or
on the list's drop zone, or double-clicking it, makes a new tile.  A tile's
title drags it before another tile to reorder; its context menu closes it,
makes it full width or double height, adds statistics of the visible span
to its readouts, or takes one of its series out.  A series stays
buffered while it is in no tile.

A telemetry key whose value is a list becomes one series per element,
``key[i]``; a bool becomes 0 or 1; a string is not a series.

The time axis is seconds since the first frame.  While *following*, the view
ends at the latest frame and spans the chosen window.  A left-drag draws a
zoom rectangle, the wheel zooms, a right-drag scales, and any of those
pauses following (``pausedChanged``); Resume follows again.  Data keeps
buffering while paused.  Y-axes always autoscale to what is visible.

Frames arrive at the telemetry rate through ``add_frame`` and only mark the
widget dirty; the curves, view and readouts are refreshed from a timer that
runs at 20 Hz at most and slows down when a refresh and its repaint would
take more than a set share of the main thread.  Each series keeps the last ``HISTORY_SECONDS`` in growable numpy
arrays, and the curves downsample to the view's pixel width (peak method, so
a spike narrower than a pixel survives).
"""

import csv
import time
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QMimeData, QObject, QPointF, QRectF, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QPen, QTransform
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QMenu, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from telemffb.plot.protocol import plottable

#: Seconds of history every series keeps, whatever window is shown.
HISTORY_SECONDS = 300.0
#: The window lengths the toolbar offers, seconds.
WINDOW_CHOICES = (10, 30, 60, 120, 300)
#: Refresh cadence while data is arriving: the fastest, and the share of the
#: main thread a refresh plus its repaint may take before the cadence slows.
_REFRESH_MS = 50
_REFRESH_MAX_MS = 500
_MAIN_THREAD_SHARE = 0.3
#: Series colors, legible on the light and the dark theme.
SERIES_COLORS = ("#2f8fdc", "#e8a33d", "#3fbf7f", "#d9534f", "#9b6bd6",
                 "#3bbfbf", "#d66ba8", "#8c8c3b")
_INITIAL_CAPACITY = 4096
#: Curve rendering: a 1 px cosmetic pen is QPainter's fast path (1.5 px
#: costs about three times as much), and pyqtgraph's grid, painted over the
#: whole plot by each axis, costs as much again as the curves.
_CURVE_WIDTH = 1
_ANTIALIAS = True
_AXIS_WIDTH_PX = 64
_TILE_COLUMNS = 2
_MIN_TILE_PX = 180
_SERIES_MIME = "application/x-telemffb-series"
_TILE_MIME = "application/x-telemffb-tile"
_DROP_ACCENT = QColor("#2f8fdc")
_DROP_MARKER_PX = 5


def series_from_frame(frame: Dict, keys: Iterable[str]) -> Dict[str, float]:
    """The plottable series of ``frame`` for the marked ``keys``: a number
    under its key, a list as ``key[i]`` per element, a bool as 0 or 1, a
    string only when it is a number with a unit suffix.  Anything else is
    left out."""
    out: Dict[str, float] = {}
    for key in keys:
        value = frame.get(key)
        if isinstance(value, (list, tuple)):
            for i, element in enumerate(value):
                number = plottable(element)
                if number is not None:
                    out[f"{key}[{i}]"] = number
        else:
            number = plottable(value)
            if number is not None:
                out[key] = number
    return out


class _Series:
    """One plotted series: parallel growable arrays of time and value,
    trimmed to the last ``HISTORY_SECONDS`` when they fill."""

    def __init__(self):
        self._t = np.empty(_INITIAL_CAPACITY)
        self._v = np.empty(_INITIAL_CAPACITY)
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def append(self, t: float, v: float) -> None:
        if self._n == len(self._t):
            self._make_room(t)
        self._t[self._n] = t
        self._v[self._n] = v
        self._n += 1

    def _make_room(self, latest_t: float) -> None:
        keep_from = int(np.searchsorted(self._t[:self._n], latest_t - HISTORY_SECONDS, side="left"))
        if keep_from > 0:
            n = self._n - keep_from
            self._t[:n] = self._t[keep_from:self._n]
            self._v[:n] = self._v[keep_from:self._n]
            self._n = n
        if self._n == len(self._t):
            self._t = np.concatenate([self._t, np.empty(len(self._t))])
            self._v = np.concatenate([self._v, np.empty(len(self._v))])

    def arrays(self) -> Tuple[np.ndarray, np.ndarray]:
        """Views of the buffered time and value arrays."""
        return self._t[:self._n], self._v[:self._n]

    def visible(self, t_from: float, t_to: float) -> Tuple[np.ndarray, np.ndarray]:
        """The samples with ``t_from <= t <= t_to`` as arrays."""
        ts, vs = self.arrays()
        lo = int(np.searchsorted(ts, t_from, side="left"))
        hi = int(np.searchsorted(ts, t_to, side="right"))
        return ts[lo:hi], vs[lo:hi]

    def value_at(self, t: float) -> Optional[float]:
        """The last sample at or before ``t``."""
        ts, vs = self.arrays()
        i = int(np.searchsorted(ts, t, side="right")) - 1
        return float(vs[i]) if 0 <= i < len(vs) else None


class _Tile:
    """One plot: the series it shows, its grid span (full width, double
    height) and whether its readouts carry statistics of the visible span."""

    def __init__(self, names: List[str], wide: bool = False, tall: bool = False):
        self.names = list(names)
        self.wide = wide
        self.tall = tall
        self.stats = False


class _SeriesList(QListWidget):
    """The plotted series with their live values; a drag source for the
    tiles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setToolTip("Drag a series onto a tile to add it there, or onto empty space\n"
                        "for a new tile. Double-click for a new tile.")

    def mimeTypes(self):
        return [_SERIES_MIME]

    def mimeData(self, items):
        data = QMimeData()
        if items:
            data.setData(_SERIES_MIME, items[0].data(Qt.ItemDataRole.UserRole).encode("utf-8"))
        return data


class _NewTileDropZone(QLabel):
    """The list's "drop here for a new tile" strip."""

    seriesDropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("Drop here for a new tile", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setStyleSheet("QLabel { border: 1px dashed palette(mid); padding: 6px; }")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_SERIES_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        self.seriesDropped.emit(bytes(event.mimeData().data(_SERIES_MIME)).decode("utf-8"))
        event.acceptProposedAction()


class _TimeZoomViewBox(pg.ViewBox):
    """A ViewBox whose zoom rectangle spans the full height: only the time
    axis is zoomed by the mouse, and the box says so."""

    def updateScaleBox(self, p1, p2) -> None:
        r = self.childGroup.mapRectFromParent(QRectF(p1, p2))
        y_lo, y_hi = self.viewRange()[1]
        r.setTop(y_lo)
        r.setBottom(y_hi)
        self.rbScaleBox.setPos(r.topLeft())
        self.rbScaleBox.setTransform(QTransform.fromScale(r.width(), r.height()))
        self.rbScaleBox.show()


class _TileView(pg.GraphicsLayoutWidget):
    """The tiles' canvas: a drop target for series and tiles, and the drag
    source for a tile's title.  Times its own repaints."""

    #: (series name, tile index or -1 for a new tile)
    seriesDropped = pyqtSignal(str, int)
    #: (dragged tile index, the index to insert it before; the tile count
    #: means the end)
    tileDropped = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._plots: List[pg.PlotItem] = []
        self._wide: List[bool] = []
        #: Drop feedback, in scene coordinates: the insertion bar of a tile
        #: drag, the outline of the tile a series would join.
        self._marker: Optional[QRectF] = None
        self._highlight: Optional[QRectF] = None
        #: Seconds the last repaint took.
        self.last_paint_s = 0.0
        #: A mouse button is held in the view: the scene is holding the
        #: pressed item for the release, so the tiles must not be rebuilt.
        self.mouse_down = False

    def paintEvent(self, event) -> None:
        t0 = time.perf_counter()
        super().paintEvent(event)
        self.last_paint_s = time.perf_counter() - t0

    def set_plots(self, plots: List[pg.PlotItem], wide: List[bool]) -> None:
        self._plots = plots
        self._wide = wide

    def tile_at(self, scene_pos) -> int:
        """The index of the tile under a scene position, else -1."""
        for i, plot in enumerate(self._plots):
            if plot.sceneBoundingRect().contains(scene_pos):
                return i
        return -1

    def _rects(self) -> List[QRectF]:
        return [plot.sceneBoundingRect() for plot in self._plots]

    @staticmethod
    def insertion_index(rects: List[QRectF], wide: List[bool], pos: QPointF) -> int:
        """Where a dragged tile dropped at ``pos`` goes: before the nearest
        tile, or after it when the pointer is past its center (its right
        half, or the lower half of a full-width tile).  A gap between tiles
        or a spot above the first row still lands next to the nearest tile
        rather than at the end."""
        if not rects:
            return 0
        best, best_d = 0, None
        for i, rect in enumerate(rects):
            dx = max(0.0, rect.left() - pos.x(), pos.x() - rect.right())
            dy = max(0.0, rect.top() - pos.y(), pos.y() - rect.bottom())
            d = dx * dx + dy * dy
            if best_d is None or d < best_d:
                best, best_d = i, d
        rect = rects[best]
        after = pos.y() > rect.center().y() if wide[best] else pos.x() > rect.center().x()
        return best + 1 if after else best

    @staticmethod
    def marker_rect(rects: List[QRectF], index: int) -> Optional[QRectF]:
        """The insertion bar for ``index``: the left edge of that tile, or
        the right edge of the last tile for the end."""
        if not rects:
            return None
        if index < len(rects):
            rect = rects[index]
            x = rect.left() - _DROP_MARKER_PX / 2
        else:
            rect = rects[-1]
            x = rect.right() - _DROP_MARKER_PX / 2
        return QRectF(x, rect.top(), _DROP_MARKER_PX, rect.height())

    def _set_feedback(self, marker: Optional[QRectF], highlight: Optional[QRectF]) -> None:
        if marker != self._marker or highlight != self._highlight:
            self._marker, self._highlight = marker, highlight
            self.viewport().update()

    def drawForeground(self, painter, rect) -> None:
        super().drawForeground(painter, rect)
        if self._marker is not None:
            painter.fillRect(self._marker, _DROP_ACCENT)
        if self._highlight is not None:
            painter.setPen(QPen(_DROP_ACCENT, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._highlight.adjusted(1, 1, -1, -1))

    def _title_at(self, scene_pos) -> int:
        for i, plot in enumerate(self._plots):
            if plot.titleLabel.isVisible() and plot.titleLabel.sceneBoundingRect().contains(scene_pos):
                return i
        return -1

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._title_at(self.mapToScene(event.position().toPoint()))
            if index >= 0:
                drag = QDrag(self)
                data = QMimeData()
                data.setData(_TILE_MIME, str(index).encode("ascii"))
                drag.setMimeData(data)
                drag.exec(Qt.DropAction.MoveAction)
                return
        self.mouse_down = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        try:
            super().mouseReleaseEvent(event)
        finally:
            self.mouse_down = False

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_SERIES_MIME) or event.mimeData().hasFormat(_TILE_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        data = event.mimeData()
        if data.hasFormat(_TILE_MIME):
            rects = self._rects()
            index = self.insertion_index(rects, self._wide, pos)
            self._set_feedback(self.marker_rect(rects, index), None)
        elif data.hasFormat(_SERIES_MIME):
            target = self.tile_at(pos)
            self._set_feedback(None, self._plots[target].sceneBoundingRect() if target >= 0 else None)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._set_feedback(None, None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        data = event.mimeData()
        self._set_feedback(None, None)
        if data.hasFormat(_SERIES_MIME):
            self.seriesDropped.emit(bytes(data.data(_SERIES_MIME)).decode("utf-8"), self.tile_at(pos))
        elif data.hasFormat(_TILE_MIME):
            index = self.insertion_index(self._rects(), self._wide, pos)
            self.tileDropped.emit(int(bytes(data.data(_TILE_MIME)).decode("ascii")), index)
        event.acceptProposedAction()


class TelemetryPlotWidget(QWidget):
    """The series list and the tiles of the series fed to ``add_frame``."""

    #: Emitted when a mouse zoom or pan pauses following, and on
    #: ``set_paused``.
    pausedChanged = pyqtSignal(bool)
    #: A recording was loaded (True) or discarded (False).
    recordingChanged = pyqtSignal(bool)
    #: Replay of a recording started (True) or ended (False).
    replayChanged = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        palette = self.palette()
        self._background = palette.window().color()
        self._foreground = palette.text().color()
        self._faded = QColor(self._foreground)
        self._faded.setAlphaF(0.3)
        pg.setConfigOptions(antialias=_ANTIALIAS, background=self._background, foreground=self._foreground)

        self._series: Dict[str, _Series] = {}
        self._order: List[str] = []
        self._tiles: List[_Tile] = []
        self._window_s = float(WINDOW_CHOICES[1])
        self._paused = False
        self._pause_t: Optional[float] = None
        self._latest_t: Optional[float] = None
        self._latest_values: Dict[str, float] = {}
        self._cursor_t: Optional[float] = None
        self._current_span: Optional[Tuple[float, float]] = None
        #: A loaded CSV recording stands in for the live feed until
        #: ``back_to_live``; replay moves a time head through it.
        self._recording: Optional[Tuple[float, float]] = None
        self._replay_t: Optional[float] = None
        self._replay_wall: float = 0.0
        self._replay_timer = QTimer(self)
        self._replay_timer.setInterval(_REFRESH_MS)
        self._replay_timer.timeout.connect(self._advance_replay)
        #: The spans left behind by mouse zooms, most recent last.
        self._span_history: List[Tuple[float, float]] = []
        self._last_push_t = 0.0
        self._dirty = False
        self._rebuild_needed = True

        self._plots: List[pg.PlotItem] = []
        self._plot_curves: List[Dict[str, pg.PlotDataItem]] = []
        self._labels: List[pg.TextItem] = []
        self._label_text: List[str] = []
        self._cursor_lines: List[pg.InfiniteLine] = []
        self._list_items: Dict[str, QListWidgetItem] = {}
        self._list_text: Dict[str, str] = {}

        self._list = _SeriesList()
        self._list.itemDoubleClicked.connect(
            lambda item: self.new_tile(item.data(Qt.ItemDataRole.UserRole)))
        self._drop_zone = _NewTileDropZone()
        self._drop_zone.seriesDropped.connect(self.new_tile)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self._drop_zone)
        side_layout.addWidget(self._list)

        self._view = _TileView()
        self._view.setBackground(self._background)
        self._view.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._view.installEventFilter(self)
        self._view.seriesDropped.connect(self._on_series_dropped)
        self._view.tileDropped.connect(self._on_tile_dropped)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._view)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(side)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 700])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._refresh_if_dirty)
        self._timer.start()

    # ---- data ---------------------------------------------------------------

    def add_frame(self, t: float, frame: Dict, keys: Iterable[str]) -> None:
        """Record this frame's marked series at time ``t`` (seconds, any
        monotonic origin).  A key that is marked but absent from the frame
        adds nothing this frame.  A series seen for the first time gets a
        tile of its own."""
        values = series_from_frame(frame, keys)
        for name, value in values.items():
            series = self._series.get(name)
            if series is None:
                series = self._series[name] = _Series()
                self._order.append(name)
                self._tiles.append(_Tile([name]))
                self._add_list_item(name)
                self._rebuild_needed = True
            series.append(t, value)
        self._latest_t = t
        self._latest_values.update(values)
        self._dirty = True

    def drop_series_not_in(self, keys: Iterable[str]) -> None:
        """Forget the series of keys no longer marked, so an unmarked key
        leaves the window rather than freezing in it."""
        wanted = set(keys)
        for name in list(self._order):
            base = name.split("[", 1)[0]
            if base not in wanted:
                del self._series[name]
                self._order.remove(name)
                self._latest_values.pop(name, None)
                self._list.takeItem(self._list.row(self._list_items.pop(name)))
                self._list_text.pop(name, None)
                for tile in self._tiles:
                    if name in tile.names:
                        tile.names.remove(name)
                self._tiles = [tile for tile in self._tiles if tile.names]
                self._rebuild_needed = True
        self._dirty = True

    def clear(self) -> None:
        self._series.clear()
        self._order.clear()
        self._tiles.clear()
        self._latest_values.clear()
        self._list.clear()
        self._list_items.clear()
        self._list_text.clear()
        self._latest_t = None
        self._pause_t = None
        self._rebuild_needed = True
        self._dirty = True

    def series_names(self) -> List[str]:
        return list(self._order)

    # ---- tiles ----------------------------------------------------------------

    def tiles(self) -> List[Tuple[List[str], bool, bool]]:
        """Each tile's series names, full width and double height."""
        return [(list(tile.names), tile.wide, tile.tall) for tile in self._tiles]

    def new_tile(self, name: str) -> None:
        if name in self._series:
            self._tiles.append(_Tile([name]))
            self._rebuild_needed = True
            self._dirty = True

    def add_series_to_tile(self, name: str, index: int) -> None:
        """Overlay ``name`` on tile ``index``; a series already there stays
        as it is."""
        if name not in self._series or not 0 <= index < len(self._tiles):
            return
        tile = self._tiles[index]
        if name not in tile.names:
            tile.names.append(name)
            self._rebuild_needed = True
            self._dirty = True

    def remove_from_tile(self, index: int, name: str) -> None:
        """Take ``name`` out of tile ``index``; an emptied tile closes."""
        if not 0 <= index < len(self._tiles):
            return
        tile = self._tiles[index]
        if name in tile.names:
            tile.names.remove(name)
            if not tile.names:
                del self._tiles[index]
            self._rebuild_needed = True
            self._dirty = True

    def close_tile(self, index: int) -> None:
        if 0 <= index < len(self._tiles):
            del self._tiles[index]
            self._rebuild_needed = True
            self._dirty = True

    def move_tile(self, src: int, before: int) -> None:
        """Put tile ``src`` before the tile now at ``before`` (the tile
        count, or ``-1``, means the end)."""
        count = len(self._tiles)
        if not 0 <= src < count:
            return
        if before < 0 or before > count:
            before = count
        if before in (src, src + 1):
            return
        tile = self._tiles.pop(src)
        self._tiles.insert(before - 1 if before > src else before, tile)
        self._rebuild_needed = True
        self._dirty = True

    def set_tile_wide(self, index: int, wide: bool) -> None:
        if 0 <= index < len(self._tiles) and self._tiles[index].wide != wide:
            self._tiles[index].wide = wide
            self._rebuild_needed = True
            self._dirty = True

    def set_tile_tall(self, index: int, tall: bool) -> None:
        if 0 <= index < len(self._tiles) and self._tiles[index].tall != tall:
            self._tiles[index].tall = tall
            self._rebuild_needed = True
            self._dirty = True

    def set_tile_stats(self, index: int, stats: bool) -> None:
        """Show, in tile ``index``'s readouts, the min, max, mean and standard
        deviation of each series over the visible time span."""
        if 0 <= index < len(self._tiles):
            self._tiles[index].stats = stats
            self._dirty = True

    def reset_tiles(self) -> None:
        """One tile per series, in series order."""
        self._tiles = [_Tile([name]) for name in self._order]
        self._rebuild_needed = True
        self._dirty = True

    def _on_series_dropped(self, name: str, target: int) -> None:
        if target < 0:
            self.new_tile(name)
        else:
            self.add_series_to_tile(name, target)

    def _on_tile_dropped(self, src: int, target: int) -> None:
        self.move_tile(src, target)

    # ---- controls -------------------------------------------------------------

    def set_window_seconds(self, seconds: float) -> None:
        """The span shown while following.  Paused, the view keeps its right
        edge and takes the new span."""
        self._window_s = float(seconds)
        if self._paused and self._plots:
            end = self._plots[0].getViewBox().viewRange()[0][1]
            self._set_x_range(end - self._window_s, end)
        self._dirty = True

    def window_seconds(self) -> float:
        return self._window_s

    def set_paused(self, paused: bool) -> None:
        """Pause: the view stops following the latest frame; data keeps
        buffering behind it.  Resume: follow again, y-axes autoscaled."""
        if paused == self._paused:
            return
        self._paused = paused
        self._pause_t = self._latest_t if paused else None
        if not paused:
            self._span_history.clear()
            self._autoscale_y()
        self._dirty = True
        self.pausedChanged.emit(paused)

    def back_to_live(self) -> None:
        """Follow the latest frame again over the chosen window.  A loaded
        recording is discarded."""
        if self._recording is not None:
            self.stop_replay()
            self.clear()
            self._recording = None
            self.recordingChanged.emit(False)
        self.set_paused(False)

    # ---- recordings -------------------------------------------------------------

    def load_csv(self, path: str) -> int:
        """Replace the buffer with a recording written by ``write_csv``: a
        ``t`` column and one column per series.  The view pauses over the
        whole recording.  Returns the number of rows read."""
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if not header or header[0] != "t":
                raise ValueError("not a telemetry plot CSV: the first column must be t")
            names = header[1:]
            rows = []
            for row in reader:
                if not row:
                    continue
                values = {}
                for name, cell in zip(names, row[1:]):
                    if cell != "":
                        values[name] = float(cell)
                rows.append((float(row[0]), values))
        self.stop_replay()
        self.clear()
        self._recording = None
        for t, values in rows:
            self.add_frame(t, values, values)
        if rows:
            self._recording = (rows[0][0], rows[-1][0])
            self.set_paused(True)
            self.refresh()
            self._set_x_range(*self._recording)
            self._span_history.clear()
        self.recordingChanged.emit(self._recording is not None)
        return len(rows)

    def is_recording(self) -> bool:
        return self._recording is not None

    def start_replay(self) -> None:
        """Play the recording from its start at real-time speed: the view
        follows a time head the way it follows live frames."""
        if self._recording is None:
            return
        self._replay_t = self._recording[0]
        self._replay_wall = time.monotonic()
        self._replay_timer.start()
        self._dirty = True
        self.replayChanged.emit(True)

    def stop_replay(self) -> None:
        if self._replay_t is None:
            return
        self._replay_timer.stop()
        self._replay_t = None
        self._dirty = True
        self.replayChanged.emit(False)

    def is_replaying(self) -> bool:
        return self._replay_t is not None

    def _advance_replay(self, dt: Optional[float] = None) -> None:
        if self._replay_t is None or self._recording is None:
            return
        now = time.monotonic()
        self._replay_t += (now - self._replay_wall) if dt is None else dt
        self._replay_wall = now
        if self._replay_t >= self._recording[1]:
            self._replay_t = self._recording[1]
            self._set_x_range(self._replay_t - self._window_s, self._replay_t)
            self._update_labels()
            self.stop_replay()
            return
        self._set_x_range(self._replay_t - self._window_s, self._replay_t)
        self._update_labels()

    def previous_zoom(self) -> None:
        """The span shown before the last mouse zoom or pan."""
        if not self._span_history:
            return
        t_from, t_to = self._span_history.pop()
        self._set_x_range(t_from, t_to)
        self._autoscale_y()
        self._dirty = True

    def has_previous_zoom(self) -> bool:
        return bool(self._span_history)

    def is_paused(self) -> bool:
        return self._paused

    def view_span(self) -> Tuple[float, float]:
        """The time span the view shows: the trailing window while
        following, else whatever the pause or the mouse left in place."""
        if not self._paused or not self._plots:
            end = self._latest_t if self._latest_t is not None else 0.0
            return end - self._window_s, end
        lo, hi = self._plots[0].getViewBox().viewRange()[0]
        return float(lo), float(hi)

    def write_csv(self, path: str) -> int:
        """Write every buffered sample as one row per frame time, a column
        per series (blank where a series had no sample at that time).
        Returns the number of rows written."""
        times: Dict[float, Dict[str, float]] = {}
        for name in self._order:
            ts, vs = self._series[name].arrays()
            for t, v in zip(ts.tolist(), vs.tolist()):
                times.setdefault(t, {})[name] = v
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["t"] + list(self._order))
            for t in sorted(times):
                row = times[t]
                writer.writerow([f"{t:.4f}"] + [("" if name not in row else repr(row[name]))
                                                for name in self._order])
        return len(times)

    # ---- layout ---------------------------------------------------------------

    def _color(self, name: str) -> str:
        return SERIES_COLORS[self._order.index(name) % len(SERIES_COLORS)]

    def _add_list_item(self, name: str) -> None:
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, name)
        item.setForeground(QColor(self._color(name)))
        self._list.addItem(item)
        self._list_items[name] = item

    def _rebuild(self) -> None:
        self._view.clear()
        self._plots = []
        self._plot_curves = []
        self._labels = []
        self._label_text = []
        self._cursor_lines = []
        if not self._tiles:
            self._view.addLabel("Mark items in the Monitor table to plot them" if not self._order
                                else "Drop a series here for a tile", row=0, col=0)
            self._view.set_plots([], [])
            return
        taken: set = set()
        cursor = (0, 0)
        rows = 0
        for index, tile in enumerate(self._tiles):
            width = _TILE_COLUMNS if tile.wide else 1
            height = 2 if tile.tall else 1
            row, col = self._free_cell(taken, width, height, cursor)
            taken.update((row + r, col + c) for r in range(height) for c in range(width))
            cursor = (row, col + width) if col + width < _TILE_COLUMNS else (row + 1, 0)
            rows = max(rows, row + height)
            plot = self._add_plot(row, col, width, height, index, tile)
            curves = {name: self._add_curve(plot, name) for name in tile.names}
            self._plot_curves.append(curves)
            self._add_label(plot)
        self._view.setMinimumHeight(_MIN_TILE_PX * rows)
        self._view.set_plots(self._plots, [tile.wide for tile in self._tiles])
        self._apply_cursor()

    @staticmethod
    def _free_cell(taken: set, width: int, height: int, start: Tuple[int, int]) -> Tuple[int, int]:
        """The first grid cell at or after ``start``, row by row, where a
        ``width`` x ``height`` tile fits.  Cells skipped on the way stay
        empty: tiles keep their order on screen."""
        row, first_col = start
        while True:
            for col in range(first_col, _TILE_COLUMNS - width + 1):
                if not any((row + r, col + c) in taken for r in range(height) for c in range(width)):
                    return row, col
            row, first_col = row + 1, 0

    def _add_plot(self, row: int, col: int, colspan: int, rowspan: int, index: int,
                  tile: _Tile) -> pg.PlotItem:
        plot = self._view.addPlot(row=row, col=col, colspan=colspan, rowspan=rowspan,
                                  viewBox=_TimeZoomViewBox())
        plot.hideButtons()
        plot.getAxis("left").setWidth(_AXIS_WIDTH_PX)
        plot.setLabel("bottom", "time", units="s")
        plot.setTitle(" + ".join(f'<span style="color:{self._color(n)}">{n}</span>' for n in tile.names),
                      size="9pt")
        plot.setMenuEnabled(False, enableViewBoxMenu=True)
        vb = plot.getViewBox()
        vb.setMouseMode(pg.ViewBox.RectMode)
        vb.setMouseEnabled(x=True, y=False)
        vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        vb.setAutoVisible(y=True)
        vb.sigRangeChangedManually.connect(lambda _mask, vb=vb: self._on_manual_range(vb))
        self._add_tile_menu(vb.menu, index, tile)
        line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(self._faded, width=1))
        line.setVisible(False)
        plot.addItem(line, ignoreBounds=True)
        self._cursor_lines.append(line)
        self._plots.append(plot)
        return plot

    def _add_tile_menu(self, menu: QMenu, index: int, tile: _Tile) -> None:
        """The tile's own entries at the top of the ViewBox context menu."""
        first = menu.actions()[0] if menu.actions() else None
        close = menu.addAction("Close tile")
        close.triggered.connect(lambda: self.close_tile(index))
        wide = menu.addAction("Full width")
        wide.setCheckable(True)
        wide.setChecked(tile.wide)
        wide.toggled.connect(lambda checked: self.set_tile_wide(index, checked))
        tall = menu.addAction("Double height")
        tall.setCheckable(True)
        tall.setChecked(tile.tall)
        tall.toggled.connect(lambda checked: self.set_tile_tall(index, checked))
        stats = menu.addAction("Statistics")
        stats.setCheckable(True)
        stats.setChecked(tile.stats)
        stats.setToolTip("Min, max, mean and standard deviation over the visible span")
        stats.toggled.connect(lambda checked: self.set_tile_stats(index, checked))
        actions = [close, wide, tall, stats]
        if len(tile.names) > 1:
            remove = QMenu("Remove from tile", menu)
            for name in tile.names:
                remove.addAction(name).triggered.connect(
                    lambda _checked=False, n=name: self.remove_from_tile(index, n))
            actions.append(remove.menuAction())
        actions.append(menu.addSeparator())
        live = menu.addAction("Back to live")
        live.triggered.connect(self.back_to_live)
        previous = menu.addAction("Previous zoom")
        previous.triggered.connect(self.previous_zoom)
        menu.aboutToShow.connect(lambda: previous.setEnabled(self.has_previous_zoom()))
        actions += [live, previous, menu.addSeparator()]
        if first is not None:
            for action in actions:
                menu.removeAction(action)
                menu.insertAction(first, action)
        self._trim_view_menu(menu)

    @staticmethod
    def _trim_view_menu(menu: QMenu) -> None:
        """pyqtgraph's stock ViewBox menu, cut to what applies here: the
        time axis is set by the widget every refresh (no X axis submenu),
        tiles are kept in step by the widget (no axis linking), and the
        mouse mode is the zoom rectangle (no Mouse Mode submenu).  Left on
        the Y axis: manual range, autoscale percentile, visible data only,
        mouse enabled."""
        for action in list(menu.actions()):
            submenu = action.menu()
            if submenu is not None and (submenu.title().startswith("X ") or submenu.title() == "Mouse Mode"):
                menu.removeAction(action)
        menu.viewAll.setText("All buffered data")
        y_axis = menu.ctrl[1]
        y_axis.linkCombo.hide()
        y_axis.invertCheck.hide()
        y_axis.autoPanCheck.hide()
        for label in y_axis.linkCombo.parentWidget().findChildren(QLabel):
            if label.text().lower().startswith("link"):
                label.hide()

    def _add_curve(self, plot: pg.PlotItem, name: str) -> pg.PlotDataItem:
        return plot.plot(name=name, pen=pg.mkPen(self._color(name), width=_CURVE_WIDTH), autoDownsample=True,
                         downsampleMethod="peak", clipToView=True, skipFiniteCheck=True)

    def _add_label(self, plot: pg.PlotItem) -> None:
        backdrop = QColor(self._background)
        backdrop.setAlphaF(0.7)
        label = pg.TextItem(anchor=(0, 0), fill=pg.mkBrush(backdrop))
        label.setParentItem(plot.getViewBox())
        label.setPos(6, 2)
        self._labels.append(label)
        self._label_text.append("")

    def _autoscale_y(self) -> None:
        for plot in self._plots:
            plot.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis)

    # ---- refresh ------------------------------------------------------------------

    def _refresh_if_dirty(self) -> None:
        if not self._dirty:
            return
        t0 = time.perf_counter()
        self.refresh()
        self._pace(time.perf_counter() - t0 + self._view.last_paint_s)

    def _pace(self, cost_s: float) -> None:
        """Slow the refresh so that a refresh and its repaint take at most
        ``_MAIN_THREAD_SHARE`` of the main thread: many tiles of long history
        must not make the rest of the application lag."""
        interval = int(cost_s * 1000 / _MAIN_THREAD_SHARE)
        self._timer.setInterval(max(_REFRESH_MS, min(_REFRESH_MAX_MS, interval)))

    def refresh(self) -> None:
        """Bring the curves, the view, the readouts and the list up to date
        now."""
        self._dirty = False
        if self._rebuild_needed and not self._interacting():
            self._rebuild_needed = False
            self._rebuild()
        elif self._rebuild_needed:
            self._dirty = True
        for curves in self._plot_curves:
            for name, curve in curves.items():
                ts, vs = self._series[name].arrays()
                curve.setData(ts, vs)
        if not self._paused and self._plots and self._latest_t is not None:
            self._set_x_range(self._latest_t - self._window_s, self._latest_t)
        self._update_labels()
        self._update_list()

    def _set_x_range(self, t_from: float, t_to: float) -> None:
        """Every tile shows the same time span.  Done here rather than with
        pyqtgraph's view linking, which aligns views of unequal width by
        pixel rather than by range, so a full-width tile would show more
        time than the others."""
        self._current_span = (t_from, t_to)
        for plot in self._plots:
            plot.getViewBox().setXRange(t_from, t_to, padding=0)

    def _interacting(self) -> bool:
        """A mouse button is down in the view or a tile's context menu is
        open: a rebuild now would delete the plot the scene or the menu is
        still acting on."""
        if self._view.mouse_down:
            return True
        for plot in self._plots:
            menu = getattr(plot.getViewBox(), 'menu', None)
            if menu is not None and menu.isVisible():
                return True
        return False

    def _on_manual_range(self, vb) -> None:
        """A mouse zoom or pan in one tile: every tile takes its time span,
        following stops, and the y-axes stay autoscaled (a zoom rectangle
        sets both axes).  The span left behind goes on the history, one
        entry per gesture (wheel ticks within half a second count as one)."""
        t_from, t_to = vb.viewRange()[0]
        now = time.monotonic()
        if self._current_span is not None and now - self._last_push_t > 0.5:
            self._span_history.append(self._current_span)
            del self._span_history[:-20]
        self._last_push_t = now
        self._set_x_range(t_from, t_to)
        self._autoscale_y()
        self.set_paused(True)

    # ---- readouts -------------------------------------------------------------------

    def _on_mouse_moved(self, scene_pos) -> None:
        if not self._plots:
            return
        vb = self._plots[0].getViewBox()
        self._cursor_t = float(vb.mapSceneToView(scene_pos).x())
        self._apply_cursor()
        self._update_labels()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._view and event.type() == QEvent.Type.Leave:
            self._cursor_t = None
            self._apply_cursor()
            self._update_labels()
        return super().eventFilter(obj, event)

    def _apply_cursor(self) -> None:
        for line in self._cursor_lines:
            if self._cursor_t is None:
                line.setVisible(False)
            else:
                line.setPos(self._cursor_t)
                line.setVisible(True)

    def _update_labels(self) -> None:
        for i, tile in enumerate(self._tiles[:len(self._labels)]):
            html = "<br>".join(self._readout_html(name, tile.stats) for name in tile.names)
            if self._label_text[i] != html:
                self._label_text[i] = html
                self._labels[i].setHtml(html)

    def _update_list(self) -> None:
        for name, item in self._list_items.items():
            latest = self._latest_values.get(name)
            text = name if latest is None else f"{name}   {latest:.4g}"
            if self._list_text.get(name) != text:
                self._list_text[name] = text
                item.setText(text)

    def _reading(self, name: str) -> str:
        """The value under the cursor with its time, else at the replay
        head, else the latest value."""
        if self._cursor_t is not None:
            value = self._series[name].value_at(self._cursor_t)
            return "" if value is None else f"{value:.4g} @ {self._cursor_t:.2f}s"
        if self._replay_t is not None:
            value = self._series[name].value_at(self._replay_t)
            return "" if value is None else f"{value:.4g} @ {self._replay_t:.2f}s"
        latest = self._latest_values.get(name)
        return "" if latest is None else f"{latest:.4g}"

    def _stats(self, name: str) -> str:
        """Min, max, mean and standard deviation of the visible span."""
        t_from, t_to = self.view_span()
        _ts, vs = self._series[name].visible(t_from, t_to)
        if len(vs) == 0:
            return ""
        return (f"min {float(np.min(vs)):.4g}  max {float(np.max(vs)):.4g}  "
                f"mean {float(np.mean(vs)):.4g}  sd {float(np.std(vs)):.3g}")

    def _readout_html(self, name: str, stats: bool = False) -> str:
        reading = self._reading(name)
        if stats:
            reading = f"{reading}&nbsp;&nbsp;&nbsp;{self._stats(name)}"
        return (f'<span style="color:{self._color(name)}; font-weight:bold">{name}</span>'
                f'&nbsp;&nbsp;<span style="color:{self._foreground.name()}">{reading}</span>')
