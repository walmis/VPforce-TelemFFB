"""Button capture, shared by the settings form and the MSFS toolbar panel:
the first button pressed during the wait, ignoring any already held."""
import pytest

import telemffb.globals as G
from telemffb.ButtonPressThread import wait_for_button_press
from telemffb.hw.ffb_rhino import HapticEffect

pytestmark = [pytest.mark.unit]


class Report:
    def __init__(self, sequence):
        self.sequence = sequence

    def getPressedButtons(self):
        return self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0]


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setattr(G, 'master_buttons', [], raising=False)
    monkeypatch.setattr(G, 'child_buttons', {}, raising=False)

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
