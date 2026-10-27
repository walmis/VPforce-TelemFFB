"""The telemetry plot widget: which values become series, the history
buffer, the followed window, pause, the tiles and the CSV export."""
import csv
import sys

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from PyQt6.QtCore import QPointF, QRectF

from telemffb.ui.widgets.TelemetryPlotWidget import (HISTORY_SECONDS, TelemetryPlotWidget, _Series,
                                                      _TileView, series_from_frame)

pytestmark = [pytest.mark.unit]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def plot(qapp):
    widget = TelemetryPlotWidget()
    widget.resize(900, 500)
    yield widget
    widget.deleteLater()


def _feed(plot, names):
    plot.add_frame(0.0, {name: 1.0 for name in names}, names)


class TestSeriesFromFrame:
    def test_numbers_lists_and_bools_become_series_and_strings_do_not(self):
        frame = {'IAS': 51.5, 'EnginePCT': [90.0, 0.0, 0.0, 0.0], 'SimOnGround': True,
                 'N': 'C172', 'Gear': 1}
        out = series_from_frame(frame, ['IAS', 'EnginePCT', 'SimOnGround', 'N', 'Gear', 'Absent'])
        assert out == {'IAS': 51.5, 'EnginePCT[0]': 90.0, 'EnginePCT[1]': 0.0,
                       'EnginePCT[2]': 0.0, 'EnginePCT[3]': 0.0, 'SimOnGround': 1.0, 'Gear': 1.0}

    def test_only_marked_keys_are_taken(self):
        assert series_from_frame({'IAS': 1.0, 'TAS': 2.0}, ['TAS']) == {'TAS': 2.0}


class TestSeriesBuffer:
    def test_keeps_the_history_window_and_grows_when_it_must(self):
        series = _Series()
        for i in range(50000):
            series.append(i / 100.0, float(i))
        ts, vs = series.arrays()
        assert ts[-1] == pytest.approx(499.99)
        assert ts[0] >= ts[-1] - HISTORY_SECONDS - 10.0
        assert len(ts) == len(vs) == len(series)
        assert list(ts[-3:]) == [499.97, 499.98, 499.99]

    def test_value_at_is_the_last_sample_at_or_before_the_time(self):
        series = _Series()
        for i in range(5):
            series.append(float(i), float(i) * 10)
        assert series.value_at(2.5) == 20.0
        assert series.value_at(4.0) == 40.0
        assert series.value_at(-1.0) is None


class TestBuffering:
    def test_frames_build_series_in_first_seen_order(self, plot):
        plot.add_frame(0.0, {'IAS': 1.0}, ['IAS', 'AoA'])
        plot.add_frame(0.1, {'IAS': 2.0, 'AoA': 3.0}, ['IAS', 'AoA'])
        assert plot.series_names() == ['IAS', 'AoA']
        ts, vs = plot._series['IAS'].visible(-1.0, 1.0)
        assert list(ts) == [0.0, 0.1] and list(vs) == [1.0, 2.0]

    def test_unmarking_drops_the_series_its_list_elements_and_its_tiles(self, plot):
        plot.add_frame(0.0, {'IAS': 1.0, 'EnginePCT': [1.0, 0.0]}, ['IAS', 'EnginePCT'])
        plot.add_series_to_tile('EnginePCT[1]', 0)
        plot.drop_series_not_in(['IAS'])
        assert plot.series_names() == ['IAS']
        assert plot.tiles() == [(['IAS'], False, False)]

    def test_the_view_follows_the_latest_frame_over_the_window(self, plot):
        for i in range(100):
            plot.add_frame(i * 1.0, {'IAS': float(i)}, ['IAS'])
        plot.set_window_seconds(10)
        assert plot.view_span() == (89.0, 99.0)

    def test_pause_holds_the_view_while_data_keeps_buffering(self, plot):
        for i in range(5):
            plot.add_frame(float(i), {'IAS': float(i)}, ['IAS'])
        plot.refresh()
        plot.set_paused(True)
        plot.add_frame(5.0, {'IAS': 5.0}, ['IAS'])
        plot.refresh()
        assert plot.view_span()[1] == pytest.approx(4.0)
        assert len(plot._series['IAS']) == 6
        plot.set_paused(False)
        assert plot.view_span()[1] == 5.0

    def test_a_mouse_range_change_pauses_and_says_so(self, plot):
        heard = []
        plot.pausedChanged.connect(heard.append)
        plot.add_frame(0.0, {'IAS': 1.0}, ['IAS'])
        plot.refresh()
        plot._plots[0].getViewBox().sigRangeChangedManually.emit([True, False])
        assert plot.is_paused() and heard == [True]

    def test_clear_forgets_everything(self, plot):
        plot.add_frame(0.0, {'IAS': 1.0}, ['IAS'])
        plot.clear()
        assert plot.series_names() == [] and plot.tiles() == [] and plot.view_span() == (-30.0, 0.0)


class TestTiles:
    def test_each_new_series_gets_a_tile_and_the_plots_share_the_time_axis(self, plot):
        plot.add_frame(0.0, {'IAS': 1.0, 'WeightOnWheels': [0.2, 0.4]}, ['IAS', 'WeightOnWheels'])
        plot.refresh()
        assert plot.tiles() == [(['IAS'], False, False), (['WeightOnWheels[0]'], False, False),
                                (['WeightOnWheels[1]'], False, False)]
        assert len(plot._plots) == 3
        plot._plots[2].getViewBox().setXRange(3.0, 7.0, padding=0)
        plot._plots[2].getViewBox().sigRangeChangedManually.emit([True, False])
        assert [tuple(round(v, 6) for v in p.getViewBox().viewRange()[0]) for p in plot._plots] == [(3.0, 7.0)] * 3

    def test_a_series_dropped_on_a_tile_overlays_it_there(self, plot):
        _feed(plot, ['IAS', 'AoA', 'Pitch'])
        plot._view.seriesDropped.emit('AoA', 0)
        plot.refresh()
        assert plot.tiles() == [(['IAS', 'AoA'], False, False), (['AoA'], False, False), (['Pitch'], False, False)]
        assert set(plot._plot_curves[0]) == {'IAS', 'AoA'}

    def test_a_series_dropped_on_empty_space_or_the_drop_zone_makes_a_new_tile(self, plot):
        _feed(plot, ['IAS'])
        plot._view.seriesDropped.emit('IAS', -1)
        plot._drop_zone.seriesDropped.emit('IAS')
        assert plot.tiles() == [(['IAS'], False, False)] * 3

    def test_removing_the_last_series_closes_the_tile(self, plot):
        _feed(plot, ['IAS', 'AoA'])
        plot.add_series_to_tile('AoA', 0)
        plot.remove_from_tile(0, 'IAS')
        plot.remove_from_tile(0, 'AoA')
        assert plot.tiles() == [(['AoA'], False, False)]

    def test_close_move_and_widen(self, plot):
        _feed(plot, ['A', 'B', 'C', 'D'])
        plot.close_tile(1)
        assert [names for names, *_ in plot.tiles()] == [['A'], ['C'], ['D']]
        plot._view.tileDropped.emit(2, 0)
        assert [names for names, *_ in plot.tiles()] == [['D'], ['A'], ['C']]
        plot.move_tile(0, -1)
        assert [names for names, *_ in plot.tiles()] == [['A'], ['C'], ['D']]
        plot.move_tile(0, 2)
        assert [names for names, *_ in plot.tiles()] == [['C'], ['A'], ['D']]
        plot.move_tile(2, 0)
        assert [names for names, *_ in plot.tiles()] == [['D'], ['C'], ['A']]
        plot.move_tile(1, 2)
        assert [names for names, *_ in plot.tiles()] == [['D'], ['C'], ['A']]
        plot.move_tile(2, 0)
        plot.move_tile(1, 3)
        assert [names for names, *_ in plot.tiles()] == [['A'], ['C'], ['D']]
        plot.set_tile_wide(1, True)
        plot.set_tile_tall(2, True)
        plot.refresh()
        assert plot.tiles()[1:] == [(['C'], True, False), (['D'], False, True)]
        assert len(plot._plots) == 3

    def test_tiles_pack_into_two_columns_around_wide_and_tall_ones(self, plot):
        _feed(plot, ['A', 'B', 'C', 'D', 'E', 'F'])
        plot.set_tile_wide(1, True)
        plot.set_tile_tall(2, True)
        plot.refresh()
        grid = plot._view.ci.layout
        placed = {}
        for row in range(grid.rowCount()):
            for col in range(grid.columnCount()):
                item = grid.itemAt(row, col)
                if item is not None and item not in placed:
                    placed[item] = (row, col)
        assert [placed[p] for p in plot._plots] == [(0, 0), (1, 0), (2, 0), (2, 1), (3, 1), (4, 0)]

    def test_statistics_describe_the_visible_span(self, plot):
        for i in range(10):
            plot.add_frame(float(i), {'IAS': float(i)}, ['IAS'])
        plot.set_window_seconds(10)
        plot.set_tile_stats(0, True)
        plot.refresh()
        assert 'min 0  max 9  mean 4.5  sd 2.87' in plot._label_text[0]

    def test_reset_gives_one_tile_per_series_again(self, plot):
        _feed(plot, ['IAS', 'AoA'])
        plot.add_series_to_tile('AoA', 0)
        plot.close_tile(1)
        plot.reset_tiles()
        assert plot.tiles() == [(['IAS'], False, False), (['AoA'], False, False)]

    def test_the_series_list_names_every_series_with_its_latest_value(self, plot):
        plot.add_frame(0.0, {'IAS': 51.25, 'AoA': 3.0}, ['IAS', 'AoA'])
        plot.refresh()
        texts = [plot._list.item(i).text() for i in range(plot._list.count())]
        assert texts == ['IAS   51.25', 'AoA   3']


class TestExport:
    def test_csv_has_one_row_per_frame_time_and_a_column_per_series(self, plot, tmp_path):
        plot.add_frame(0.0, {'IAS': 1.0, 'AoA': 2.0}, ['IAS', 'AoA'])
        plot.add_frame(0.5, {'IAS': 1.5}, ['IAS', 'AoA'])
        path = tmp_path / "plot.csv"
        assert plot.write_csv(str(path)) == 2
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[0] == ['t', 'IAS', 'AoA']
        assert rows[1] == ['0.0000', '1.0', '2.0']
        assert rows[2] == ['0.5000', '1.5', '']


class TestRecording:
    def _record(self, plot, tmp_path):
        for i in range(20):
            plot.add_frame(i * 0.5, {'IAS': float(i), 'AoA': 2.0}, ['IAS', 'AoA'])
        path = str(tmp_path / "rec.csv")
        plot.write_csv(path)
        return path

    def test_a_written_csv_loads_back_paused_over_its_whole_span(self, plot, tmp_path):
        path = self._record(plot, tmp_path)
        plot.clear()
        plot.add_frame(1000.0, {'Other': 1.0}, ['Other'])
        assert plot.load_csv(path) == 20
        assert plot.is_recording() and plot.is_paused()
        assert plot.series_names() == ['IAS', 'AoA']
        assert len(plot._series['IAS']) == 20
        assert plot.view_span() == pytest.approx((0.0, 9.5))
        assert plot.tiles() == [(['IAS'], False, False), (['AoA'], False, False)]

    def test_a_file_without_a_time_column_is_refused(self, plot, tmp_path):
        path = tmp_path / "other.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        with pytest.raises(ValueError):
            plot.load_csv(str(path))

    def test_replay_moves_the_view_head_through_the_recording_and_stops_at_its_end(self, plot, tmp_path):
        path = self._record(plot, tmp_path)
        plot.load_csv(path)
        plot.set_window_seconds(10)
        heard = []
        plot.replayChanged.connect(heard.append)
        plot.start_replay()
        assert plot.is_replaying()
        plot._advance_replay(4.0)
        assert plot.view_span() == pytest.approx((-6.0, 4.0))
        assert 'IAS' in plot._readout_html('IAS') and '@ 4.00s' in plot._readout_html('IAS')
        plot._advance_replay(100.0)
        assert not plot.is_replaying()
        assert plot.view_span() == pytest.approx((-0.5, 9.5))
        assert heard == [True, False]

    def test_back_to_live_discards_the_recording(self, plot, tmp_path):
        path = self._record(plot, tmp_path)
        plot.load_csv(path)
        plot.start_replay()
        heard = []
        plot.recordingChanged.connect(heard.append)
        plot.back_to_live()
        assert not plot.is_recording() and not plot.is_replaying() and not plot.is_paused()
        assert plot.series_names() == []
        assert heard == [False]


class TestPainting:
    def test_renders_empty_tiled_and_overlaid(self, plot):
        image = QImage(plot.size(), QImage.Format.Format_ARGB32)
        plot.refresh()
        plot.render(image)
        for i in range(300):
            plot.add_frame(i / 60.0, {'IAS': 50 + (i % 7), 'WeightOnWheels': [0.2, 0.4, 0.4]},
                           ['IAS', 'WeightOnWheels'])
        plot.refresh()
        plot.render(image)
        plot.add_series_to_tile('WeightOnWheels[1]', 0)
        plot.set_tile_wide(0, True)
        plot.refresh()
        plot.render(image)
        assert plot.series_names() == ['IAS', 'WeightOnWheels[0]', 'WeightOnWheels[1]', 'WeightOnWheels[2]']


class TestRebuildDeferral:
    def test_no_rebuild_while_a_mouse_button_is_down_or_a_menu_is_open(self, plot):
        _feed(plot, ["IAS"])
        plot.refresh()
        first = plot._plots[0]
        plot._view.mouse_down = True
        _feed(plot, ["IAS", "AoA"])
        plot.refresh()
        assert plot._plots == [first]
        plot._view.mouse_down = False
        first.getViewBox().menu.popup(first.getViewBox().menu.pos())
        plot.refresh()
        assert plot._plots == [first]
        first.getViewBox().menu.hide()
        plot.refresh()
        assert len(plot._plots) == 2


class TestZoomHistory:
    def test_previous_zoom_steps_back_through_mouse_zooms_and_live_clears_them(self, plot):
        for i in range(100):
            plot.add_frame(float(i), {'IAS': float(i)}, ['IAS'])
        plot.set_window_seconds(30)
        plot.refresh()
        vb = plot._plots[0].getViewBox()
        assert not plot.has_previous_zoom()
        vb.setXRange(80.0, 90.0, padding=0)
        vb.sigRangeChangedManually.emit([True, False])
        plot._last_push_t = 0.0
        vb.setXRange(84.0, 86.0, padding=0)
        vb.sigRangeChangedManually.emit([True, False])
        assert plot.is_paused() and plot.has_previous_zoom()
        plot.previous_zoom()
        assert plot.view_span() == pytest.approx((80.0, 90.0))
        plot.previous_zoom()
        assert plot.view_span() == pytest.approx((69.0, 99.0))
        assert not plot.has_previous_zoom()
        plot.back_to_live()
        assert not plot.is_paused() and plot.view_span() == (69.0, 99.0)

    def test_wheel_ticks_within_half_a_second_make_one_history_entry(self, plot):
        plot.add_frame(0.0, {'IAS': 1.0}, ['IAS'])
        plot.refresh()
        vb = plot._plots[0].getViewBox()
        for span in [(-20.0, 0.0), (-15.0, 0.0), (-10.0, 0.0)]:
            vb.setXRange(*span, padding=0)
            vb.sigRangeChangedManually.emit([True, False])
        assert len(plot._span_history) == 1


class TestTileDropTarget:
    # two rows of two, then a full-width third row
    RECTS = [QRectF(0, 0, 100, 50), QRectF(100, 0, 100, 50),
             QRectF(0, 50, 100, 50), QRectF(100, 50, 100, 50), QRectF(0, 100, 200, 50)]
    WIDE = [False, False, False, False, True]

    def _index(self, x, y):
        return _TileView.insertion_index(self.RECTS, self.WIDE, QPointF(x, y))

    def test_the_pointer_side_of_the_nearest_tile_decides(self):
        assert self._index(20, 25) == 0
        assert self._index(80, 25) == 1
        assert self._index(120, 25) == 1
        assert self._index(180, 25) == 2
        assert self._index(20, 75) == 2

    def test_gaps_and_the_space_above_the_first_row_land_next_to_the_nearest_tile(self):
        assert self._index(20, -30) == 0
        assert self._index(-30, 75) == 2
        assert self._index(250, 25) == 2
        assert self._index(50, 49.9) == 0

    def test_a_full_width_tile_splits_top_and_bottom(self):
        assert self._index(150, 110) == 4
        assert self._index(150, 140) == 5
        assert self._index(150, 400) == 5

    def test_marker_sits_on_the_left_edge_or_the_last_right_edge(self):
        assert _TileView.marker_rect(self.RECTS, 1).center().x() == pytest.approx(100.0)
        assert _TileView.marker_rect(self.RECTS, 5).center().x() == pytest.approx(200.0)
        assert _TileView.marker_rect([], 0) is None


class TestTileMenu:
    def test_only_the_applicable_view_options_remain(self, plot):
        _feed(plot, ["IAS", "AoA"])
        plot.add_series_to_tile("AoA", 0)
        plot.refresh()
        menu = plot._plots[0].getViewBox().menu
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert texts == ["Close tile", "Full width", "Double height", "Statistics", "Remove from tile",
                         "Back to live", "Previous zoom", "All buffered data", "Y axis"]
        y_axis = menu.ctrl[1]
        assert y_axis.linkCombo.isHidden() and y_axis.invertCheck.isHidden() and y_axis.autoPanCheck.isHidden()
        assert not y_axis.manualRadio.isHidden() and not y_axis.visibleOnlyCheck.isHidden()
        assert not y_axis.mouseCheck.isHidden()


class TestZoomBox:
    def test_the_zoom_rectangle_spans_the_full_height(self, plot):
        from PyQt6.QtCore import QPointF
        for i in range(50):
            plot.add_frame(float(i), {"IAS": 10.0 + (i % 5)}, ["IAS"])
        plot.refresh()
        vb = plot._plots[0].getViewBox()
        vb.setYRange(0.0, 20.0, padding=0)
        p1 = vb.childGroup.mapToParent(QPointF(10.0, 12.0))
        p2 = vb.childGroup.mapToParent(QPointF(20.0, 14.0))
        vb.updateScaleBox(p1, p2)
        box = vb.rbScaleBox.mapRectToParent(vb.rbScaleBox.rect())
        assert box.top() == pytest.approx(0.0) and box.bottom() == pytest.approx(20.0)
        assert box.left() == pytest.approx(10.0) and box.right() == pytest.approx(20.0)
