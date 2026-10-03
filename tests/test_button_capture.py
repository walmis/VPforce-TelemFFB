"""Button capture, shared by the settings form and the MSFS toolbar panel:
the first button pressed during the wait, ignoring any already held."""
import pytest

import telemffb.globals as G
import telemffb.hw.button_state as button_state
from telemffb.ButtonPressThread import wait_for_button_press
from telemffb.hw.ffb_rhino import HapticEffect

pytestmark = [pytest.mark.unit]


class Report:
    def __init__(self, sequence):
        self.sequence = sequence

    def getPressedButtons(self):
        return self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0]


class FakeSettings:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setattr(G, 'master_buttons', [], raising=False)
    monkeypatch.setattr(G, 'child_buttons', {}, raising=False)
    monkeypatch.setattr(G, 'button_states', {}, raising=False)
    monkeypatch.setattr(button_state, '_role_keys', {})

    def use(*frames):
        report = Report(list(frames))
        monkeypatch.setattr(HapticEffect, 'get_device_input', staticmethod(lambda: report))
    return use


def test_the_first_new_press_is_returned(device):
    device([], [], [4])
    assert wait_for_button_press(timeout=1.0) == 4


def test_a_button_held_when_the_wait_starts_does_not_count(device):
    device([3], [3], [3, 7])
    assert wait_for_button_press(timeout=1.0) == 7


def test_no_press_times_out_with_zero(device):
    device([])
    assert wait_for_button_press(timeout=0.3) == 0


def test_a_childs_device_is_read_from_what_it_reports(device, monkeypatch):
    device([])
    monkeypatch.setattr(G, 'child_buttons', {'pedals': [2]}, raising=False)
    ticks = []
    # held at the start, so only a later press counts
    assert wait_for_button_press('pedals', timeout=0.3, on_tick=ticks.append) == 0
    assert ticks


def test_no_device_reads_as_no_buttons(monkeypatch):
    monkeypatch.setattr(G, 'master_buttons', [], raising=False)
    monkeypatch.setattr(HapticEffect, 'get_device_input', staticmethod(lambda: None))
    assert wait_for_button_press(timeout=0.2) == 0


@pytest.fixture
def other_device(device):
    """A generic controller's buttons, changed by ``press`` mid-wait."""
    key = '1234:0001#2'
    G.button_states[key] = frozenset({2})       # held when the wait starts

    def press(*buttons):
        def tick(_left):
            G.button_states[key] = frozenset({2, *buttons})
        return tick
    return key, press


@pytest.fixture
def own_key(device, monkeypatch):
    """This instance's FFB device published under its own device key."""
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    monkeypatch.setattr(G, 'system_settings', FakeSettings(
        devids_joystick='FFFF:2055', devpath_joystick='p1'), raising=False)
    key = button_state.publish_role_buttons('joystick', [], broadcast=False)
    assert key in G.button_states
    return key


def test_a_press_on_another_device_returns_its_qualified_id(device, other_device):
    device([])
    key, press = other_device
    assert wait_for_button_press(timeout=1.0, on_tick=press(9)) == f'{key}:9'


def test_the_own_device_wins_over_another_device(device, other_device):
    device([], [5])
    _, press = other_device
    assert wait_for_button_press(timeout=1.0, on_tick=press(1)) == 5


def test_the_lowest_of_simultaneous_presses_is_returned(device, other_device):
    device([], [8, 3])
    assert wait_for_button_press(timeout=1.0) == 3
    device([])
    key, press = other_device
    assert wait_for_button_press(timeout=1.0, on_tick=press(9, 4)) == f'{key}:4'


def test_binding_for_a_child_qualifies_the_own_device(device, own_key):
    device([])
    G.child_buttons['pedals'] = []

    def press(_left):
        G.button_states[own_key] = frozenset({5})
    assert wait_for_button_press(target_device='pedals', timeout=1.0, on_tick=press) == f'{own_key}:5'


def test_the_own_device_is_never_returned_as_a_qualified_id(device, own_key):
    device([])

    def press(_left):
        G.button_states[own_key] = frozenset({6})
    assert wait_for_button_press(timeout=0.3, on_tick=press) == 0
