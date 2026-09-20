"""NoticeCard (telemffb/ui/widgets/NoticeCard.py) and PromptStack
(telemffb/ui/panels/PromptStack.py) - the reusable pill widget and the
panel that renders AppState's prompt stack.

These replace MainWindow's three hand-rolled QLabel pills (new-craft,
trim-calibration-discovery, matching-profile-offer), each of which used to
carry its own private QVariantAnimation doing an identical breathing-pulse
tween. No real telemetry/IPC threads here - PromptStack is driven purely
off AppState, exercised directly.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QVariantAnimation
from PyQt6.QtWidgets import QApplication

from telemffb.state.app_state import (AppState, Notice, NEW_CRAFT_PRIORITY,
                                      PROFILE_CHANGE_PRIORITY, TRIM_CAL_PRIORITY)
from telemffb.ui.panels.PromptStack import PromptStack
from telemffb.ui.widgets.NoticeCard import NoticeCard
from telemffb.ui.widgets.NoticeStyle import (NEW_CRAFT_STYLE, PROFILE_CHANGE_STYLE,
                                             TRIM_CAL_STYLE)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def state(app):
    s = AppState()
    s.set_own_device_type('joystick')
    s.set_master(True)
    return s


def _new_craft_notice(html='<a>unmatched aircraft</a>'):
    return Notice(notice_id='new_craft', priority=NEW_CRAFT_PRIORITY, html=html,
                 style='new_craft', pulse=True)


def _trim_cal_notice():
    return Notice(notice_id='trim_cal', priority=TRIM_CAL_PRIORITY,
                 html='<a>no trim calibration</a>', style='trim_cal', pulse=True)


def _profile_change_notice():
    return Notice(notice_id='profile_change', priority=PROFILE_CHANGE_PRIORITY,
                 html='<a>multiple profiles</a>', style='profile_change', pulse=False)


class TestNoticeCard:
    def test_hidden_until_a_notice_is_set(self, app):
        card = NoticeCard()
        assert card.isHidden()

    def test_set_notice_shows_the_text_and_starts_pulsing(self, app):
        card = NoticeCard()
        card.set_notice('<a>hello</a>', NEW_CRAFT_STYLE, pulse=True)
        assert card.text() == '<a>hello</a>'
        assert not card.isHidden()
        assert card._anim.state() == QVariantAnimation.State.Running

    def test_a_static_notice_does_not_pulse(self, app):
        card = NoticeCard()
        card.set_notice('<a>offer</a>', PROFILE_CHANGE_STYLE, pulse=False)
        assert card._anim.state() == QVariantAnimation.State.Stopped

    def test_clear_hides_and_stops_the_animation(self, app):
        card = NoticeCard()
        card.set_notice('<a>hello</a>', NEW_CRAFT_STYLE, pulse=True)
        card.clear()
        assert card.isHidden()
        assert card._anim.state() == QVariantAnimation.State.Stopped

    def test_activated_fires_on_link_click(self, app):
        card = NoticeCard()
        card.set_notice("<a href='#x'>hello</a>", TRIM_CAL_STYLE, pulse=True)
        seen = []
        card.activated.connect(lambda: seen.append(True))
        card.linkActivated.emit('#x')
        assert seen == [True]


class TestPromptStackVisibility:
    def test_hidden_with_no_active_prompts(self, state):
        stack = PromptStack()
        stack.bind(state)
        assert stack.isHidden()

    def test_binding_paints_whatever_is_already_active(self, state):
        """bind() must pull the current state immediately - AppState does
        not re-emit just because a new subscriber connected."""
        state.set_prompt('trim_cal', _trim_cal_notice())
        stack = PromptStack()
        stack.bind(state)
        assert not stack.isHidden()
        assert 'trim_cal' in stack._cards

    def test_a_new_prompt_shows_a_card(self, state):
        stack = PromptStack()
        stack.bind(state)
        state.set_prompt('new_craft', _new_craft_notice())
        assert not stack.isHidden()
        assert 'new_craft' in stack._cards
        assert stack._cards['new_craft'].text() == '<a>unmatched aircraft</a>'

    def test_clearing_the_only_prompt_hides_the_stack(self, state):
        stack = PromptStack()
        stack.bind(state)
        state.set_prompt('new_craft', _new_craft_notice())
        state.set_prompt('new_craft', None)
        assert stack.isHidden()
        assert 'new_craft' not in stack._cards


class TestPromptStackOrdering:
    def test_cards_are_laid_out_in_priority_order(self, state):
        """Matches the old fixed layout order: new-craft, profile-change,
        trim-cal, top to bottom."""
        stack = PromptStack()
        stack.bind(state)
        state.set_prompt('trim_cal', _trim_cal_notice())
        state.set_prompt('profile_change', _profile_change_notice())
        state.set_prompt('new_craft', _new_craft_notice())

        widgets = [stack._layout.itemAt(i).widget()
                  for i in range(stack._layout.count())
                  if stack._layout.itemAt(i).widget() is not None]
        assert widgets == [stack._cards['new_craft'], stack._cards['profile_change'],
                           stack._cards['trim_cal']]


class TestPromptStackActivation:
    def test_activated_is_tagged_with_the_notice_id(self, state):
        stack = PromptStack()
        stack.bind(state)
        state.set_prompt('trim_cal', _trim_cal_notice())
        seen = []
        stack.activated.connect(seen.append)
        stack._cards['trim_cal'].linkActivated.emit('#trimcal')
        assert seen == ['trim_cal']

    def test_a_click_on_one_card_never_reports_a_sibling(self, state):
        stack = PromptStack()
        stack.bind(state)
        state.set_prompt('new_craft', _new_craft_notice())
        state.set_prompt('trim_cal', _trim_cal_notice())
        seen = []
        stack.activated.connect(seen.append)
        stack._cards['new_craft'].linkActivated.emit('#newcraft')
        assert seen == ['new_craft']
