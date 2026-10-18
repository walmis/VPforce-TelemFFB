"""AdvancedSpringDialog's per-curve input: each of the X and Y curves follows
airspeed or its own telemetry item or sim variable, saved as an ``input``
block in that curve with raw units."""
import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

import telemffb.globals as G
from telemffb.ui.dialogs.AdvancedSpringDialog import AdvancedSpringDialog

pytestmark = pytest.mark.unit


def _airspeed_curve():
    return {"x_min": 0, "x_max": 500,
            "points": [{"x": 0.0, "y": 0.0}, {"x": 250.0, "y": 30.0}, {"x": 500.0, "y": 100.0}],
            "smooth_curve_enabled": False, "current_unit": "kt"}


def _item_curve(source, x_min, x_max, points):
    return {"x_min": x_min, "x_max": x_max, "points": points,
            "smooth_curve_enabled": False, "current_unit": "raw",
            "input": {"source": source, "min": x_min, "max": x_max}}


def _settings(curve_x, curve_y):
    return {"curve_x": curve_x, "gain_x": 80, "curve_y": curve_y, "gain_y": 100,
            "units": "kt", "scale": 500}


TRIM_CURVE = _item_curve("L:TRIM_POS", 1.25, 5.0,
                         [{"x": 1.25, "y": 0.0}, {"x": 3.0, "y": 40.0}, {"x": 5.0, "y": 100.0}])
# Min 5 at the left end, Max 1.25 at the right: the points run right to left in x
REVERSED_CURVE = _item_curve("L:TRIM_POS", 5.0, 1.25,
                             [{"x": 5.0, "y": 0.0}, {"x": 3.0, "y": 40.0}, {"x": 1.25, "y": 100.0}])

ROUND_TRIPS = {
    "x airspeed, y item": _settings(_airspeed_curve(), TRIM_CURVE),
    "both items": _settings(TRIM_CURVE, _item_curve("AoA", -5.0, 20.0,
                                                    [{"x": -5.0, "y": 10.0}, {"x": 20.0, "y": 90.0}])),
    "both airspeed": _settings(_airspeed_curve(), _airspeed_curve()),
    "reversed range": _settings(_airspeed_curve(), REVERSED_CURVE),
}


class FakeTelemManager(QObject):
    telemetryReceived = pyqtSignal(object)
    telemetryTimeout = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.currentAircraft = None


class StubAircraft:
    """The aircraft's per-curve input API: each axis has its saved source and the key it is read under."""

    def __init__(self, sources, keys):
        self._sources = sources
        self._keys = keys

    def adv_spr_input_source(self, axis):
        return self._sources.get(axis)

    def adv_spr_input_key(self, axis):
        return self._keys.get(axis)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_dialog(app, monkeypatch):
    monkeypatch.setattr(G, 'telem_manager', FakeTelemManager(), raising=False)
    monkeypatch.setattr(G, 'useDarkMode', False, raising=False)
    dialogs = []

    def make(settings=None, sim="MSFS"):
        dlg = AdvancedSpringDialog(settings=None if settings is None else json.dumps(settings),
                                   device="joystick", sim=sim)
        saved = []
        dlg.accepted.connect(lambda json_string, scale, units: saved.append(json.loads(json_string)))
        dlg.saved = saved
        dialogs.append(dlg)
        return dlg

    yield make
    for dlg in dialogs:
        dlg.deleteLater()


def _warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, 'warning',
                        staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    return shown


def _enter_item_mode(dlg, axis, source, x_min, x_max):
    getattr(dlg, f'cb_axis_mode_{axis}').setCurrentIndex(AdvancedSpringDialog.MODE_ITEM)
    getattr(dlg, f'cb_input_source_{axis}').setEditText(source)
    getattr(dlg, f'le_input_min_{axis}').setText(x_min)
    getattr(dlg, f'le_input_max_{axis}').setText(x_max)


class TestPerCurveInput:
    def test_an_item_curve_saves_its_own_input_block_and_raw_units(self, make_dialog):
        dlg = make_dialog()
        _enter_item_mode(dlg, 'y', "ElevTrimPct", "1.25", "5.0")

        dlg.save_curve_settings(close=False)

        saved = dlg.saved[-1]
        assert "input" not in saved
        assert saved["units"] == "kt"
        assert "input" not in saved["curve_x"]
        assert saved["curve_x"]["current_unit"] == "kt"
        curve_y = saved["curve_y"]
        assert curve_y["input"] == {"source": "ElevTrimPct", "min": 1.25, "max": 5.0}
        assert curve_y["current_unit"] == "raw"
        assert (curve_y["x_min"], curve_y["x_max"]) == (1.25, 5.0)
        xs = [p["x"] for p in curve_y["points"]]
        assert (xs[0], xs[-1]) == (1.25, 5.0)

    def test_loading_opens_each_curve_in_its_own_mode_with_the_fields_filled(self, make_dialog):
        dlg = make_dialog(ROUND_TRIPS["x airspeed, y item"])

        assert not dlg.is_item_mode('x')
        assert dlg.is_item_mode('y')
        assert dlg.input_source('y') == "L:TRIM_POS"
        assert float(dlg.le_input_min_y.text()) == 1.25
        assert float(dlg.le_input_max_y.text()) == 5.0
        assert dlg.w_input_axis_x.isHidden()
        assert not dlg.w_input_axis_y.isHidden()
        assert not dlg.w_airspeed_axis.isHidden()

    def test_the_input_row_keeps_its_height_across_modes(self, make_dialog):
        dlg = make_dialog()
        airspeed_height = dlg.w_curve_input_x.sizeHint().height()

        dlg.cb_axis_mode_x.setCurrentIndex(AdvancedSpringDialog.MODE_ITEM)

        assert dlg.w_curve_input_x.sizeHint().height() == airspeed_height

    def test_the_airspeed_bar_hides_when_both_curves_are_items(self, make_dialog):
        dlg = make_dialog(ROUND_TRIPS["both items"])

        assert dlg.w_airspeed_axis.isHidden()

    @pytest.mark.parametrize("name", ROUND_TRIPS)
    def test_load_and_save_round_trip_is_unchanged(self, make_dialog, name):
        settings = ROUND_TRIPS[name]
        dlg = make_dialog(settings)

        assert dlg.check_dirty_state(apply=False) is False
        dlg.save_curve_settings(close=False)

        assert json.dumps(dlg.saved[-1], sort_keys=True) == json.dumps(settings, sort_keys=True)

    def test_min_equal_to_max_is_refused(self, make_dialog, monkeypatch):
        shown = _warnings(monkeypatch)
        dlg = make_dialog()
        _enter_item_mode(dlg, 'x', "ElevTrimPct", "5", "5")

        dlg.save_curve_settings(close=False)

        assert dlg.saved == []
        assert len(shown) == 1

    def test_switching_back_to_airspeed_restores_the_unit_and_range(self, make_dialog):
        dlg = make_dialog()
        _enter_item_mode(dlg, 'x', "ElevTrimPct", "1.25", "5.0")
        dlg.apply_input_range('x')

        dlg.cb_axis_mode_x.setCurrentIndex(AdvancedSpringDialog.MODE_AIRSPEED)
        dlg.save_curve_settings(close=False)

        curve_x = dlg.saved[-1]["curve_x"]
        assert "input" not in curve_x
        assert curve_x["current_unit"] == "kt"
        assert (curve_x["x_min"], curve_x["x_max"]) == (0, 500)
        xs = [p["x"] for p in curve_x["points"]]
        assert (xs[0], xs[-1]) == (0, 500)

    def test_loading_an_airspeed_curve_clears_the_previous_item_fields(self, make_dialog):
        dlg = make_dialog(ROUND_TRIPS["x airspeed, y item"])

        dlg.load_curve_settings(json.dumps(ROUND_TRIPS["both airspeed"]))

        assert (dlg.input_source('y'), dlg.le_input_min_y.text(), dlg.le_input_max_y.text()) == ("", "", "")
        dlg.cb_axis_mode_y.setCurrentIndex(AdvancedSpringDialog.MODE_ITEM)
        assert (float(dlg.le_input_min_y.text()), float(dlg.le_input_max_y.text())) == (0, 500)
        assert (dlg.curve_y.x_min, dlg.curve_y.x_max) == (0, 500)

    def test_a_range_edit_rechecks_the_copy_buttons(self, make_dialog):
        dlg = make_dialog(_settings(TRIM_CURVE, TRIM_CURVE))
        assert dlg.pb_copy_up.isEnabled()
        assert dlg.pb_copy_down.isEnabled()

        dlg.le_input_max_x.setText("6")
        dlg.apply_input_range('x')

        assert not dlg.pb_copy_up.isEnabled()
        assert not dlg.pb_copy_down.isEnabled()

    def test_copy_needs_both_curves_on_the_same_input_and_range(self, make_dialog):
        dlg = make_dialog(ROUND_TRIPS["x airspeed, y item"])
        assert not dlg.pb_copy_up.isEnabled()
        assert not dlg.pb_copy_down.isEnabled()

        dlg.cb_axis_mode_y.setCurrentIndex(AdvancedSpringDialog.MODE_AIRSPEED)

        assert dlg.pb_copy_up.isEnabled()
        assert dlg.pb_copy_down.isEnabled()

    def test_live_value_of_a_telemetry_item_is_read_under_its_name(self, make_dialog):
        dlg = make_dialog(ROUND_TRIPS["x airspeed, y item"])

        assert dlg.current_input_value('y', {"L:TRIM_POS": 2.5}) == 2.5

    def test_live_value_of_a_sim_variable_is_read_under_the_curves_input_key(self, make_dialog):
        dlg = make_dialog(ROUND_TRIPS["x airspeed, y item"])
        G.telem_manager.currentAircraft = StubAircraft({'y': "L:TRIM_POS"}, {'y': "SpringCurveInput_joystick_y"})

        assert dlg.current_input_value('y', {"SpringCurveInput_joystick_y": 3.5}) == 3.5

    def test_the_value_readout_follows_each_frame(self, make_dialog):
        dlg = make_dialog(ROUND_TRIPS["x airspeed, y item"])

        G.telem_manager.telemetryReceived.emit({"L:TRIM_POS": 2.5, "IAS": 100.0})
        assert dlg.lab_input_value_y.text().endswith(AdvancedSpringDialog._value_text(2.5))

        G.telem_manager.telemetryReceived.emit({"IAS": 100.0})
        assert dlg.lab_input_value_y.text().endswith(AdvancedSpringDialog.NO_VALUE_TEXT)

    def test_the_value_readout_clears_on_telemetry_timeout(self, make_dialog):
        dlg = make_dialog(ROUND_TRIPS["x airspeed, y item"])
        G.telem_manager.telemetryReceived.emit({"L:TRIM_POS": 2.5})

        G.telem_manager.telemetryTimeout.emit(True)

        assert dlg.lab_input_value_y.text().endswith(AdvancedSpringDialog.NO_VALUE_TEXT)


@pytest.mark.parametrize("clears", [True, False])
def test_live_data_hides_only_a_notice_that_clears_on_it(make_dialog, clears):
    dlg = make_dialog()
    dlg.curve_x.show_message(AdvancedSpringDialog.NO_SAVED_CONFIG_MSG, clears_on_live_data=clears)

    dlg.curve_x.draw_crosshairs(100.0, 50.0)

    assert dlg.curve_x.msg_label.isHidden() is clears


@pytest.mark.parametrize("sim, offered", [("DCS", False), ("IL2", False), ("XPLANE", True), ("MSFS", True)])
def test_the_curve_input_row_is_offered_on_msfs_and_xplane_only(make_dialog, sim, offered):
    dlg = make_dialog(sim=sim)
    assert dlg.w_curve_input_x.isHidden() is (not offered)
    assert dlg.w_curve_input_y.isHidden() is (not offered)


def test_enter_in_a_range_field_keeps_the_curve_points(make_dialog):
    dlg = make_dialog(_settings(_airspeed_curve(), TRIM_CURVE))
    dlg.show()   # a shown QDialog promotes its first auto-default button to the default
    points_before = len(dlg.curve_y.points)
    dlg.le_input_max_y.setFocus()
    dlg.le_input_max_y.setText("6")
    QTest.keyClick(dlg.le_input_max_y, Qt.Key.Key_Return)
    assert len(dlg.curve_y.points) == points_before
    assert dlg.curve_y.x_max == 6.0
