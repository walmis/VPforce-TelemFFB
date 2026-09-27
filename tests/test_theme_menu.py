"""System > Theme: a pick is saved as the System Settings dialog saves it,
and a changed theme offers the restart it needs."""
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
import telemffb.utils as utils
from telemffb.ui import menus

pytestmark = [pytest.mark.unit]


class Settings(dict):
    def get(self, name, default=None, instance=None):
        return super().get(name, default)

    def setValue(self, key, value, instance=None):
        self[key] = value


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(settings=Settings(themeId=2), asked=[], restarts=0, answer=True)
    monkeypatch.setattr(G, 'system_settings', state.settings, raising=False)

    def ask(parent, what):
        state.asked.append(what)
        return state.answer

    def restart():
        state.restarts += 1
    monkeypatch.setattr(menus, 'ask_to_restart', ask)
    monkeypatch.setattr(utils, 'request_restart', restart)
    state.menu = menus.MainMenu.__new__(menus.MainMenu)
    state.menu.mw = None
    return state


def test_a_new_theme_is_saved_and_offers_a_restart(world):
    world.menu._choose_theme(1)
    assert world.settings['themeId'] == 1
    assert len(world.asked) == 1
    assert world.restarts == 1


def test_later_keeps_the_choice_without_restarting(world):
    world.answer = False
    world.menu._choose_theme(0)
    assert world.settings['themeId'] == 0
    assert world.restarts == 0


def test_the_theme_already_saved_asks_nothing(world):
    world.menu._choose_theme(2)
    assert world.asked == []
    assert world.restarts == 0
