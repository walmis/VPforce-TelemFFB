"""X-Plane dataref overrides: a numeric scale is sent to the plugin as its
conversion factor; an expression is sent as 1.0 and applied by TelemFFB to
the tagged value of each X-Plane frame, as the MSFS path does."""
import types

import pytest

import telemffb.globals as G
import telemffb.xmlutils as xmlutils
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.telem.TelemManager import TelemManager

pytestmark = [pytest.mark.unit, pytest.mark.xplane]


class _Socket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(data.decode("utf-8"))


def _manager(monkeypatch, rows):
    monkeypatch.setattr(G, "system_settings", types.SimpleNamespace(get=lambda k, d=None: d), raising=False)
    monkeypatch.setattr(G, "settings_mgr", types.SimpleNamespace(timed_out=False), raising=False)
    monkeypatch.setattr(G, "ipc_instance", None, raising=False)
    monkeypatch.setattr(xmlutils, "read_sc_overrides", lambda *a, **k: rows)
    tm = TelemManager()
    tm._socket = _Socket()
    return tm


def _row(name, scale):
    return {"name": name, "var": f"sim/test/{name}", "sc_unit": "float", "scale": scale}


def _conversion(sent, name):
    line = next(s for s in sent if f"tag={name}," in s)
    return float(line.rsplit("conversion=", 1)[1])


def _precision(sent, name):
    line = next(s for s in sent if f"tag={name}," in s)
    return int(line.split("precision=", 1)[1].split(",", 1)[0])


def test_a_number_goes_to_the_plugin_and_is_not_applied_again(monkeypatch):
    tm = _manager(monkeypatch, [_row("StabTrim", "2.5")])
    tm._setup_xpplugin_overrides("CL650", "XPLANE")

    assert _conversion(tm._socket.sent, "StabTrim") == 2.5
    assert _precision(tm._socket.sent, "StabTrim") == 3
    frame = BaseTelemetryData({"src": "XPLANE", "StabTrim": 4.0})
    tm._apply_xplane_transforms(frame)
    assert frame["StabTrim"] == 4.0


def test_an_expression_is_applied_to_the_frame_and_the_plugin_gets_1(monkeypatch):
    tm = _manager(monkeypatch, [_row("StabTrim", "(5 - x) * 40")])
    tm._setup_xpplugin_overrides("CL650", "XPLANE")

    assert _conversion(tm._socket.sent, "StabTrim") == 1.0
    assert _precision(tm._socket.sent, "StabTrim") == 6
    frame = BaseTelemetryData({"src": "XPLANE", "StabTrim": 1.25})
    tm._apply_xplane_transforms(frame)
    assert frame["StabTrim"] == pytest.approx(150.0)


def test_a_new_aircraft_replaces_the_transforms(monkeypatch):
    tm = _manager(monkeypatch, [_row("StabTrim", "x - 1")])
    tm._setup_xpplugin_overrides("CL650", "XPLANE")
    monkeypatch.setattr(xmlutils, "read_sc_overrides", lambda *a, **k: [])
    tm._setup_xpplugin_overrides("Other", "XPLANE")

    frame = BaseTelemetryData({"src": "XPLANE", "StabTrim": 3.0})
    tm._apply_xplane_transforms(frame)
    assert frame["StabTrim"] == 3.0
