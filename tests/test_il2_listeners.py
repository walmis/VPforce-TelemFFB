"""IL-2 Great Battles and IL-2 Korea as separate sims.

The two games speak one telemetry protocol; what tells them apart is the
UDP port each is configured to send to, so each has its own listener and
the frames carry the listener's source key.
"""
from types import SimpleNamespace

import pytest

import telemffb.globals as G
from telemffb.telem.IL2Manager import IL2TelemParser
from telemffb.telem.SimTelemListener import SimIL2, SimIL2K
from telemffb.utils.integration import _il2_config_on_port

pytestmark = [pytest.mark.unit]


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    def get(self, name, default=None, instance=None):
        return self.values.get(name, default)


@pytest.fixture
def settings(monkeypatch):
    def make(**values):
        monkeypatch.setattr(G, "system_settings", FakeSettings(**values), raising=False)
        monkeypatch.setattr(G, "args", SimpleNamespace(sim="None"), raising=False)
        monkeypatch.setattr(G, "main_window", None, raising=False)
    return make


def test_each_game_validates_its_own_config_on_its_own_port(settings, monkeypatch):
    """Korea's config gets Korea's port and its FFB stream section; Great
    Battles' gets neither."""
    settings(portIL2=34385, portIL2_K=34386, pathIL2="C:/GB", pathIL2_K="C:/Korea")
    calls = []
    monkeypatch.setattr("telemffb.utils.analyze_il2_config",
                        lambda path, port, window, sim_name, korea, legacy_port: calls.append((path, port, korea)))
    SimIL2().validate()
    SimIL2K().validate()
    (gb_path, gb_port, gb_ffb), (k_path, k_port, k_ffb) = calls
    assert (gb_port, gb_ffb) == (34385, False) and gb_path.startswith("C:/GB")
    assert (k_port, k_ffb) == (34386, True) and k_path.startswith("C:/Korea")


def test_only_the_shared_port_config_is_treated_as_the_port_change():
    """Korea's config from before the split - TelemFFB's own sections, all
    on Great Battles' port - is the one case explained as the port change;
    anything else keeps the ordinary config-check prompt."""
    def config(port, **changes):
        section = {'addr': '127.255.255.255', 'decimation': '1', 'enable': 'true', 'port': str(port), **changes}
        return {s: dict(section) for s in ('telemetrydevice', 'motiondevice', 'ffbdevice')}
    assert _il2_config_on_port(config(34385), 34385)
    assert not _il2_config_on_port(config(34386), 34385)                       # already on its own port
    assert not _il2_config_on_port(config(34385, decimation='2'), 34385)       # something else is off too
    partial = config(34385)
    del partial['ffbdevice']
    assert not _il2_config_on_port(partial, 34385)                             # a section is missing


def test_an_aircraft_change_keeps_the_source():
    """The parser starts over on a new aircraft; the source it was given
    must survive that, or Korea's frames revert to IL2 mid-session."""
    parser = IL2TelemParser(src="IL2K")
    parser.ac_name = "F-86A-5"
    name = b"MiG-15bis"
    # one telemetry packet: header, size, tick, no state entries, one
    # VehicleName event (type 0) carrying the new aircraft's name
    packet = (0x54000101).to_bytes(4, "little") + (0).to_bytes(2, "little") + (1).to_bytes(4, "little")
    packet += bytes([0])                                   # state entry count
    packet += bytes([0])                                   # trailing byte before the events
    packet += (0).to_bytes(2, "little") + bytes([1 + len(name)]) + bytes([len(name)]) + name
    parser.process_packet(packet)
    assert parser.ac_name == "MiG-15bis"
    assert parser.telem_data["src"] == "IL2K"
