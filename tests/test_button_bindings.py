"""Button bindings that name a device by identity.

A binding is a bare int (button n on this instance's own device) or a
qualified id ``<key>:<n>`` resolved through ``G.button_states``.  The
master fills that map and relays it to the children over IPC:

    child  -> master     BUTTONS:<role>_<json list>     (legacy, unchanged)
    master -> children   BTNDEV:<key>:<json list>
"""
import json

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
import telemffb.hw.button_state as button_state
from telemffb.hw.button_refs import (button_binding_label, button_device_name,
                                     ffb_device_key, parse_button_ref)
from telemffb.IPCNetworkThread import IPCNetworkThread
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase

pytestmark = [pytest.mark.unit,
              # the IPC thread opens a UDP socket in its constructor; see
              # test_preview_ipc.py
              pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")]


class FakeSettings:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


class Report:
    """An FFB input report with the given buttons down."""

    def __init__(self, *pressed):
        self.pressed = set(pressed)

    def isButtonPressed(self, number):
        return number in self.pressed


@pytest.fixture
def states(monkeypatch):
    table = {}
    monkeypatch.setattr(G, 'button_states', table, raising=False)
    monkeypatch.setattr(button_state, '_role_keys', {})
    return table


# ---------------------------------------------------------------------------
# parse_button_ref
# ---------------------------------------------------------------------------

def test_parse_bare_number():
    assert parse_button_ref(12) == (None, 12)
    assert parse_button_ref('12') == (None, 12)


def test_parse_qualified_id_splits_on_the_last_colon():
    assert parse_button_ref('045E:028E#AB_1:12') == ('045E:028E#AB_1', 12)
    assert parse_button_ref('045E:028E:3') == ('045E:028E', 3)


@pytest.mark.parametrize('value', ['', 0, '0', 'x', '045E:028E', '045E:028E:0',
                                   '045E:028E:x', ':5', 'Z:5', None, 1.5, -2])
def test_parse_invalid_is_nothing(value):
    assert parse_button_ref(value) == (None, 0)


def test_parse_whole_float_as_its_number():
    assert parse_button_ref(5.0) == (None, 5)


# ---------------------------------------------------------------------------
# ffb_device_key
# ---------------------------------------------------------------------------

def test_ffb_device_key_comes_from_the_stored_ids():
    settings = FakeSettings(devids_joystick='ffff:2055', devpath_joystick='p1',
                            devids_pedals='FFFF:2056', devpath_pedals='p2')
    assert ffb_device_key('joystick', settings) == 'FFFF:2055'
    assert ffb_device_key('pedals', settings) == 'FFFF:2056'
    assert ffb_device_key('collective', settings) == ''


def test_ffb_device_key_names_the_role_when_ids_are_shared():
    settings = FakeSettings(devids_joystick='FFFF:2055', devpath_joystick='p1',
                            devids_pedals='FFFF:2055', devpath_pedals='p2')
    assert ffb_device_key('joystick', settings) == 'FFFF:2055#joystick'
    assert ffb_device_key('pedals', settings) == 'FFFF:2055#pedals'


# ---------------------------------------------------------------------------
# button_device_name / button_binding_label
# ---------------------------------------------------------------------------

def names_settings(**extra):
    return FakeSettings(buttonDeviceNames=json.dumps({'1234:0001#2': 'Box'}), **extra)


def test_device_name_comes_from_the_stored_map():
    assert button_device_name('1234:0001#2', names_settings()) == 'Box'
    assert button_device_name('9999:0001', names_settings()) == ''


def test_device_name_of_an_ffb_slot_is_its_stored_product_name():
    settings = names_settings(devids_joystick='FFFF:2055', devpath_joystick='p1',
                              devident_joystick='Stick', devids_pedals='FFFF:2055',
                              devpath_pedals='p2', devident_pedals='Pedals')
    assert button_device_name(ffb_device_key('pedals', settings), settings) == 'Pedals'
    assert button_device_name(ffb_device_key('joystick', settings), settings) == 'Stick'


def test_binding_label_of_a_bare_number():
    label = button_binding_label('7', names_settings())
    assert label and '7' in label
    assert button_binding_label(7, names_settings()) == label
    assert button_binding_label('0', names_settings()) == ''


def test_binding_label_names_a_known_device():
    settings = names_settings()
    assert (button_binding_label('1234:0001#2:7', settings)
            == f"Box: {button_binding_label(7, settings)}")


def test_binding_label_shows_the_key_of_an_unknown_device():
    settings = names_settings()
    assert (button_binding_label('9999:0001:7', settings)
            == f"9999:0001: {button_binding_label(7, settings)}")


# ---------------------------------------------------------------------------
# check_button_press
# ---------------------------------------------------------------------------

@pytest.fixture
def aircraft(states, monkeypatch):
    monkeypatch.setattr(G, 'master_buttons', [7], raising=False)
    monkeypatch.setattr(AircraftEffectUtilsBase, '_get_device_report',
                        staticmethod(lambda: Report(3)))
    states['1234:0001#2'] = frozenset({5})
    return AircraftEffectUtilsBase()


def test_bare_number_reads_this_instances_device(aircraft):
    assert aircraft.check_button_press(3)
    assert aircraft.check_button_press('3')
    assert not aircraft.check_button_press(5)
    assert aircraft.check_button_press(7, check_master=True)
    assert aircraft.check_button_press(9, report=Report(9))


def test_qualified_id_reads_the_named_device(aircraft):
    assert aircraft.check_button_press('1234:0001#2:5')
    assert aircraft.check_button_press('1234:0001#2:5', check_master=True)
    assert not aircraft.check_button_press('1234:0001#2:3')


def test_qualified_id_of_an_unknown_device_is_not_pressed(aircraft):
    assert not aircraft.check_button_press('ABCD:0001:5')


@pytest.mark.parametrize('value', ['garbage', '1234:0001#2:x', ':5', '1234:0001#2:0', 1.5])
def test_invalid_value_is_not_pressed(aircraft, value):
    assert not aircraft.check_button_press(value)


# ---------------------------------------------------------------------------
# IPC relay
# ---------------------------------------------------------------------------

class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data.decode(), addr))

    def close(self):
        pass


def _thread(monkeypatch, **kwargs):
    ipc = IPCNetworkThread(**kwargs)
    ipc._socket.close()
    fake = FakeSocket()
    monkeypatch.setattr(ipc, '_socket', fake)
    return ipc, fake


def test_child_stores_a_relayed_device(states, monkeypatch):
    monkeypatch.setattr(G, 'device_type', 'pedals', raising=False)
    ipc, _ = _thread(monkeypatch, dstport=40000)
    ipc._handle_message('BTNDEV:1234:0001#2:[3, 5]', ('127.0.0.1', 1))
    assert states == {'1234:0001#2': frozenset({3, 5})}


def test_child_replaces_its_table_from_a_snapshot(states, monkeypatch):
    monkeypatch.setattr(G, 'device_type', 'pedals', raising=False)
    monkeypatch.setattr(G, 'system_settings', FakeSettings(
        devids_pedals='FFFF:2056', devpath_pedals='p2'), raising=False)
    own = button_state.publish_role_buttons('pedals', [2], broadcast=False)
    states['1234:0001#2'] = frozenset({3})       # stale: its release was dropped
    ipc, _ = _thread(monkeypatch, dstport=40000)

    ipc._handle_message('BTNDEV_ALL:{"5678:0001": [1, 4]}', ('127.0.0.1', 1))

    assert states == {own: frozenset({2}), '5678:0001': frozenset({1, 4})}


def test_master_keys_a_childs_buttons_and_relays_them(states, monkeypatch):
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    monkeypatch.setattr(G, 'child_buttons', {}, raising=False)
    monkeypatch.setattr(G, 'system_settings', FakeSettings(
        devids_joystick='FFFF:2055', devpath_joystick='p1',
        devids_pedals='FFFF:2056', devpath_pedals='p2'), raising=False)
    ipc, fake = _thread(monkeypatch)
    ipc._child_addrs = {'pedals': ('127.0.0.1', 41001), 'collective': ('127.0.0.1', 41002)}

    ipc._handle_message('BUTTONS:pedals_[4]', ('127.0.0.1', 41001))

    assert G.child_buttons == {'pedals': [4]}
    assert states == {'FFFF:2056': frozenset({4})}
    assert sorted(fake.sent) == [('BTNDEV:FFFF:2056:[4]', ('127.0.0.1', 41001)),
                                 ('BTNDEV:FFFF:2056:[4]', ('127.0.0.1', 41002))]
