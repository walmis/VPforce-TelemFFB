"""The MSFS toolbar panel's API: which device's settings the panel reads and
writes, chosen in the panel among this instance's device and its children's,
and erasing the user's own changes the way the desktop form does."""
import io
import json
from types import SimpleNamespace
from wsgiref.util import setup_testing_defaults

import pytest

import telemffb.api_server as api_server
import telemffb.globals as G
from telemffb import xmlutils

pytestmark = [pytest.mark.unit, pytest.mark.msfs]


def call(method, path, body=None):
    """The server's response to one request, as (status, JSON)."""
    data = json.dumps(body).encode() if body is not None else b""
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(data)),
               "wsgi.input": io.BytesIO(data)}
    setup_testing_defaults(environ)
    status = []
    out = b"".join(api_server.app(environ, lambda s, h, e=None: status.append(s)))
    return int(status[0].split()[0]), json.loads(out)


def row(name, replaced):
    return {"name": name, "datatype": "bool", "value": "true", "replaced": replaced}


@pytest.fixture
def server(monkeypatch):
    writes, erases, reads = [], [], []
    rows = []
    sm = SimpleNamespace(
        device="joystick", current_sim="MSFS", current_aircraft_name="H145",
        current_class="Helicopter", current_pattern="H145", active_profile=None,
        offline_mode=False, offline_scope=None, timed_out=False,
        write_to_xml=lambda *a, the_device="", **k: writes.append(the_device),
        erase_from_xml=lambda sim, cls, model, name, the_device="": erases.append((name, the_device)))
    monkeypatch.setattr(api_server, "_settings_mgr", sm)
    monkeypatch.setattr(api_server, "_scope", {"device": None})
    monkeypatch.setattr(G, "device_type", "joystick", raising=False)
    monkeypatch.setattr(G, "launched_instances", {"trimwheel": None, "pedals": None}, raising=False)
    monkeypatch.setattr(G, "main_window", SimpleNamespace(sim_status=SimpleNamespace(held_errors=[])),
                        raising=False)
    monkeypatch.setattr(xmlutils, "read_single_model",
                        lambda sim, name, cls, device, **k: reads.append(device) or (cls, name, rows))
    monkeypatch.setattr(xmlutils, "get_active_profile_for_model", lambda *a: None)
    return SimpleNamespace(sm=sm, writes=writes, erases=erases, reads=reads, rows=rows)


class TestDeviceScope:
    def test_the_panel_starts_on_this_instances_device(self, server):
        _, status = call("GET", "/api/status")
        assert status["device"] == "joystick"
        assert status["devices"] == ["joystick", "pedals", "trimwheel"]

    def test_a_childs_device_is_read_and_written_once_chosen(self, server):
        assert call("POST", "/api/device", {"device": "pedals"})[0] == 200
        call("GET", "/api/settings")
        call("POST", "/api/settings", {"name": "spring_gain", "value": 0.5})
        call("POST", "/api/erase", {"name": "spring_gain"})
        assert server.reads == ["pedals"]
        assert server.writes == ["pedals"]
        assert server.erases == [("spring_gain", "pedals")]

    def test_a_device_nothing_drives_is_refused(self, server):
        assert call("POST", "/api/device", {"device": "collective"})[0] == 400
        assert call("GET", "/api/status")[1]["device"] == "joystick"

    def test_a_child_that_goes_away_leaves_the_panel_on_this_instances_device(self, server):
        call("POST", "/api/device", {"device": "pedals"})
        del G.launched_instances["pedals"]
        call("POST", "/api/settings", {"name": "spring_gain", "value": 0.5})
        assert server.writes == ["joystick"]


class TestErase:
    def _erasable(self, server):
        _, data = call("GET", "/api/settings")
        return {s["name"]: s["erasable"] for s in data["settings"]}

    def test_only_the_users_own_model_changes_can_be_erased(self, server):
        """The desktop form offers erase on the same rows; a class or sim
        override is removed there, deliberately, not from the panel."""
        server.rows[:] = [row("mine", "Model (user)"), row("default", "Default"),
                          row("by_class", "Class (user)"), row("by_sim", "Sim (user)")]
        assert self._erasable(server) == {"mine": True, "default": False,
                                          "by_class": False, "by_sim": False}

    def test_offline_editing_erases_at_the_scope_being_edited(self, server):
        server.sm.offline_mode, server.sm.offline_scope = True, "CLASS"
        server.rows[:] = [row("mine", "Model (user)"), row("by_class", "Class (user)")]
        assert self._erasable(server) == {"mine": False, "by_class": True}

    def test_erase_needs_a_setting_name(self, server):
        assert call("POST", "/api/erase", {})[0] == 400
        assert server.erases == []
