"""Advanced Dynamic spring curves driven by an input source instead of airspeed:
the raw-unit lookup, where each curve's source is read from, the gain held
while it is absent, the variable subscriptions on MSFS and X-Plane, and the
log records support relies on."""
import json
import logging
import types

import pytest

import telemffb.globals as G
import telemffb.sim.base.AdvancedSpringMixIn as adv_spring_module
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.sim.base.AdvancedSpringMixIn import ADV_SPR_INPUT_WAIT_S, AdvancedSpringMixIn, spring_curve_input_tag
from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
from telemffb.telem.TelemManager import TelemManager
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder

pytestmark = pytest.mark.unit

TAG_X = spring_curve_input_tag("joystick", "x")
TAG_Y = spring_curve_input_tag("joystick", "y")


@pytest.fixture(autouse=True)
def _joystick_role(monkeypatch):
    monkeypatch.setattr(G, "device_type", "joystick", raising=False)


def _curve(source=None, lo=0.0, hi=10.0):
    """A curve rising linearly from 0 % at ``lo`` to 100 % at ``hi``; with a
    ``source`` an input curve, else an airspeed curve in the settings' units."""
    curve = {
        "x_min": lo,
        "x_max": hi,
        "points": [{"x": lo, "y": 0}, {"x": hi, "y": 100}],
        "smooth_curve_enabled": False,
        "current_unit": "m/s",
    }
    if source is not None:
        curve["current_unit"] = "raw"
        curve["input"] = {"source": source, "min": lo, "max": hi}
    return curve


def _settings(curve_x, curve_y, units="m/s"):
    return {"curve_x": curve_x, "curve_y": curve_y, "units": units, "gain_x": 100, "gain_y": 100,
            "scale": 500}


class _Socket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(data.decode("utf-8"))


def _telem_manager(monkeypatch):
    monkeypatch.setattr(G, "system_settings", types.SimpleNamespace(get=lambda k, d=None: d), raising=False)
    monkeypatch.setattr(G, "settings_mgr", types.SimpleNamespace(timed_out=False), raising=False)
    monkeypatch.setattr(G, "ipc_instance", None, raising=False)
    tm = TelemManager()
    tm._socket = _Socket()
    return tm


def test_an_input_curve_is_looked_up_in_its_sources_own_units():
    # the airspeed curve of the same settings is in knots
    settings = _settings(_curve(lo=0.0, hi=100.0), _curve("StabTrim", 0.0, 8.0), units="kt")

    assert utils.get_curve_gain(settings, "y", 2.0) == 0.25
    assert utils.get_curve_gain(settings, "x", 25.0) == pytest.approx(0.486, abs=1e-3)


class TestAdvancedSpringInputSource(BaseTelemetryEffectTestCase):
    def _instance(self, curve_x, curve_y):
        instance = self.create_test_instance(AdvancedSpringMixIn)
        instance.spring_mode = SpringModeEnum.ADVANCED
        instance.adv_spr_gains = json.dumps(_settings(curve_x, curve_y))
        return instance

    def test_a_source_the_frame_carries_is_read_under_its_own_name(self):
        instance = self._instance(_curve("StabTrim"), _curve("StabTrim"))
        frame = BaseTelemetryData({"StabTrim": 2.5, TAG_X: 7.5, TAG_Y: 7.5, "IAS": 200.0})

        assert instance.adv_spr_curve_gains(frame, frame.IAS) == {"x": 0.25, "y": 0.25}
        self.set_telemetry(instance, frame)
        assert instance.adv_spr_input_key("y") == "StabTrim"

    def test_each_curve_reads_a_variable_source_under_its_own_tag(self):
        instance = self._instance(_curve("L:AILERON_TRIM"), _curve("L:STAB_TRIM"))
        frame = BaseTelemetryData({TAG_X: 2.5, TAG_Y: 7.5, "IAS": 200.0})

        assert instance.adv_spr_curve_gains(frame, frame.IAS) == {"x": 0.25, "y": 0.75}
        self.set_telemetry(instance, frame)
        assert (instance.adv_spr_input_key("x"), instance.adv_spr_input_key("y")) == (TAG_X, TAG_Y)

    def test_an_airspeed_curve_and_an_item_curve_in_one_frame(self):
        instance = self._instance(_curve(), _curve("StabTrim"))
        frame = BaseTelemetryData({"StabTrim": 7.5, "IAS": 2.5})

        assert instance.adv_spr_curve_gains(frame, frame.IAS) == {"x": 0.25, "y": 0.75}
        assert instance.adv_spr_input_source("x") is None
        assert instance.adv_spr_input_key("x") is None

    def test_each_role_reads_its_own_tag(self, monkeypatch):
        monkeypatch.setattr(G, "device_type", "pedals", raising=False)
        instance = self._instance(_curve("L:RUDDER_TRIM"), _curve())
        frame = BaseTelemetryData({TAG_X: 2.5, spring_curve_input_tag("pedals", "x"): 7.5})

        assert instance.adv_spr_curve_gain("x", frame, 0.0) == 0.75

    def test_each_curve_holds_its_last_gain_while_its_source_is_absent(self):
        instance = self._instance(_curve("AileronTrim"), _curve("StabTrim"))
        instance.adv_spr_curve_gains(BaseTelemetryData({"AileronTrim": 2.0, "StabTrim": 6.0}), 0.0)

        gains = instance.adv_spr_curve_gains(BaseTelemetryData({"AileronTrim": 4.0}), 0.0)

        assert gains == {"x": 0.4, "y": 0.6}

    def test_reversed_curve_interpolates_and_clamps_at_both_ends(self):
        # Min 5 at the left end, Max 1 at the right: the points are stored right to left in x
        reversed_curve = _curve("StabTrim", 5.0, 1.0)
        instance = self._instance(_curve(), reversed_curve)

        def gain(value):
            return instance.adv_spr_curve_gain("y", BaseTelemetryData({"StabTrim": value}), 0.0)

        assert gain(4.0) == 0.25
        assert gain(6.0) == 0.0
        assert gain(0.5) == 1.0


def _records(caplog, source, level=None):
    return [r for r in caplog.records
            if source in r.getMessage() and (level is None or r.levelno == level)]


class TestAdvancedSpringInputLogging(BaseTelemetryEffectTestCase):
    def test_applying_an_input_curve_logs_its_source_once(self, caplog):
        caplog.set_level(logging.INFO)
        instance = self.create_test_instance(AdvancedSpringMixIn)
        instance.adv_spr_gains = json.dumps(_settings(_curve(), _curve("L:STAB_TRIM", 6.0, 1.0)))
        frame = BaseTelemetryData({"IAS": 50.0})

        instance.on_telemetry(frame)
        instance.on_telemetry(frame)

        records = _records(caplog, "L:STAB_TRIM")
        assert len(records) == 1
        assert records[0].levelno == logging.INFO

    def test_the_first_value_of_a_source_is_logged_once(self, caplog):
        caplog.set_level(logging.INFO)
        instance = self.create_test_instance(AdvancedSpringMixIn)
        instance.adv_spr_gains = json.dumps(_settings(_curve(), _curve("StabTrim")))

        for value in (3.25, 4.0):
            instance.adv_spr_curve_gain("y", BaseTelemetryData({"StabTrim": value}), 0.0)

        records = _records(caplog, "StabTrim")
        assert len(records) == 1
        assert records[0].levelno == logging.INFO

    def test_a_source_absent_past_the_wait_logs_one_warning(self, caplog, monkeypatch):
        caplog.set_level(logging.INFO)
        clock = [100.0]
        monkeypatch.setattr(adv_spring_module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
        instance = self.create_test_instance(AdvancedSpringMixIn)
        instance.adv_spr_gains = json.dumps(_settings(_curve(), _curve("L:STAB_TRIM")))
        frame = BaseTelemetryData({"IAS": 50.0})

        for elapsed in (0.0, ADV_SPR_INPUT_WAIT_S - 1.0):
            clock[0] = 100.0 + elapsed
            instance.adv_spr_curve_gain("y", frame, 0.0)
        assert _records(caplog, "L:STAB_TRIM") == []

        for elapsed in (ADV_SPR_INPUT_WAIT_S + 0.5, ADV_SPR_INPUT_WAIT_S + 10.0):
            clock[0] = 100.0 + elapsed
            instance.adv_spr_curve_gain("y", frame, 0.0)

        assert len(_records(caplog, "L:STAB_TRIM", logging.WARNING)) == 1
        assert len(_records(caplog, "L:STAB_TRIM")) == 1


class TestSpringCurveInputSubscription(BaseTelemetryEffectTestCase):
    def _flight_frame(self, **items):
        builder = (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("IAS", 50.0)
            .set("DesignSpeed", (100.0, 50.0, 60.0))
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 3000.0)
            .set("Incidence", [0, -0.05, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .set("G", 1.0)
            .set("ElevDefl", 5.0)
            .set("ElevDeflPct", 0.25)
            .set("WeightOnWheels", [0, 0, 0])
            .set("AircraftClass", "Airplane")
            .ffb_type("joystick")
        )
        for name, value in items.items():
            builder.set(name, value)
        return builder.build()

    def test_msfs_subscribes_a_variable_source_under_its_curves_tag_and_releases_it(self):
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.ADVANCED
        instance.adv_spr_gains = json.dumps(_settings(_curve(), _curve("L:CL650_STAB_TRIM")))

        telem = self._flight_frame(**{TAG_Y: 4.0})
        self.set_telemetry(instance, telem)
        instance.on_telemetry(telem)

        assert instance.mock_simconnect.sv_dict[TAG_Y] == {"var": "L:CL650_STAB_TRIM", "unit": "number"}
        assert TAG_X not in instance.mock_simconnect.sv_dict
        assert telem._elev_coeff == pytest.approx(0.4)

        instance.adv_spr_gains = json.dumps(_settings(_curve(), _curve("StabTrim")))
        telem = self._flight_frame(StabTrim=4.0)
        self.set_telemetry(instance, telem)
        instance.on_telemetry(telem)

        assert TAG_Y not in instance.mock_simconnect.sv_dict

    def test_xplane_subscribes_a_dataref_and_releases_it(self, monkeypatch):
        tm = _telem_manager(monkeypatch)
        monkeypatch.setattr(G, "telem_manager", tm, raising=False)
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_xplane = True

        instance._sync_spring_curve_input("y", "sim/a/trim")
        instance._sync_spring_curve_input("y", "sim/a/trim")
        instance._sync_spring_curve_input("y", "sim/b/trim")
        instance.on_shutdown()

        assert TAG_Y == "SpringCurveInput_joystick_y"
        assert tm._socket.sent == [
            f"SUBSCRIBE:dataref=sim/a/trim,type=float,tag={TAG_Y},precision=6,conversion=1.0",
            f"UNSUBSCRIBE:tag={TAG_Y}",
            f"SUBSCRIBE:dataref=sim/b/trim,type=float,tag={TAG_Y},precision=6,conversion=1.0",
            f"UNSUBSCRIBE:tag={TAG_Y}",
        ]
