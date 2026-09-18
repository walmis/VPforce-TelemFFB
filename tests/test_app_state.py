"""Unit tests for AppState (telemffb/state/app_state.py).

Step 1 of the UI-model refactor moved the "scope status" indicators
(vpconf profile / gain-override-active) off MainWindow.refresh_scope_status_indicators
(deleted) and onto this model: writers call setters, AppState derives and
deduplicates the view, and scope_status_changed carries what's left to
show. These tests exercise the model in isolation - no MainWindow, no
real IPC/telemetry threads - covering: pure derivation, dedup (including
a raw-state change that does not move the *derived* view), scope
switching, master-vs-child visibility, and a setter called from a real
background thread.
"""
import os
import sys
import threading

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from telemffb.state.app_state import (AppState, Notice, ScopeStatus,
                                      NEW_CRAFT_PRIORITY, PROFILE_CHANGE_PRIORITY,
                                      TRIM_CAL_PRIORITY)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def state(app):
    """A fresh AppState identifying as a 'joystick' instance - not master
    unless a test opts in."""
    s = AppState()
    s.set_own_device_type('joystick')
    return s


def _capture(signal):
    """Collect every emission of a pyqtSignal as a list of arg-tuples."""
    calls = []
    signal.connect(lambda *args: calls.append(args))
    return calls


class TestDerivation:
    def test_defaults_are_empty_and_hidden(self, state):
        assert state.current_status() == ScopeStatus(
            scope='joystick', vpconf='', any_vpconf=False, ovd=False, any_ovd=False)

    def test_own_values_show_when_scope_is_own_device(self, state):
        state.set_own_vpconf('C:/foo.vpconf')
        state.set_own_gain_overrides_active(True)
        status = state.current_status()
        assert status.vpconf == 'C:/foo.vpconf'
        assert status.any_vpconf is True
        assert status.ovd is True
        assert status.any_ovd is True

    def test_none_and_empty_path_normalize_to_empty_string(self, state):
        state.set_own_vpconf('C:/foo.vpconf')
        state.set_own_vpconf(None)
        status = state.current_status()
        assert status.vpconf == ''
        assert status.any_vpconf is False


class TestDedup:
    def test_setting_the_same_value_twice_only_emits_once(self, state):
        calls = _capture(state.scope_status_changed)
        state.set_own_vpconf('C:/foo.vpconf')
        assert len(calls) == 1
        state.set_own_vpconf('C:/foo.vpconf')
        assert len(calls) == 1

    def test_a_raw_state_change_that_does_not_move_the_shown_view_does_not_emit(self, state):
        """A second device's report can change AppState's raw inputs
        (_child_status) without changing anything the currently-scoped
        view shows - any_vpconf is already True, so a second non-empty
        value for an unrelated device is a no-op for the signal."""
        state.set_master(True)
        state.set_scope('pedals')
        state.set_child_status('trimwheel', vpconf='X')  # any_vpconf False->True: emits
        calls = _capture(state.scope_status_changed)
        state.set_child_status('trimwheel', vpconf='Y')  # still non-empty: no change to shown
        assert calls == []


class TestScopeSwitching:
    def test_switching_scope_changes_which_device_is_shown(self, state):
        state.set_master(True)
        state.set_own_vpconf('own.vpconf')
        state.set_child_status('pedals', vpconf='pedals.vpconf', gain_ovd_active=True)

        assert state.current_status().vpconf == 'own.vpconf'  # default scope: own device

        state.set_scope('pedals')
        status = state.current_status()
        assert status.vpconf == 'pedals.vpconf'
        assert status.ovd is True

        state.set_scope('joystick')  # back to own device
        assert state.current_status().vpconf == 'own.vpconf'

    def test_scope_none_falls_back_to_own_device(self, state):
        state.set_own_vpconf('own.vpconf')
        state.set_scope('pedals')
        state.set_scope(None)
        status = state.current_status()
        assert status.scope == 'joystick'
        assert status.vpconf == 'own.vpconf'


class TestMasterVsChild:
    def test_non_master_ignores_child_status_even_when_scoped_to_it(self, state):
        # not master (the fixture's default)
        state.set_own_vpconf('own.vpconf')
        state.set_child_status('pedals', vpconf='pedals.vpconf')
        state.set_scope('pedals')
        assert state.current_status().vpconf == 'own.vpconf'

    def test_non_master_any_flags_ignore_child_reports(self, state):
        state.set_child_status('pedals', vpconf='pedals.vpconf', gain_ovd_active=True)
        status = state.current_status()
        assert status.any_vpconf is False
        assert status.any_ovd is False

    def test_master_scoped_to_its_own_device_never_reads_child_status(self, state):
        """A child_status entry keyed by the master's own device type can't
        happen in practice (children report their own type, never the
        master's), but the scope==own_device branch must still win over it."""
        state.set_master(True)
        state.set_own_vpconf('own.vpconf')
        state.set_child_status('joystick', vpconf='should-be-ignored')
        assert state.current_status().vpconf == 'own.vpconf'


class TestThreadOriginSetter:
    def test_setter_from_a_background_thread_is_reflected_and_dedup_still_holds(self, state, app):
        """Setters run on the telemetry thread, the IPC thread and the main
        thread. This drives them from a real background thread and checks
        both that the write lands correctly (read back after join) and
        that the signal still only fires for a change to the *shown* view
        - exercising the lock and the dedup logic together, not just one
        or the other."""
        state.set_master(True)
        calls = _capture(state.scope_status_changed)

        def worker():
            state.set_own_vpconf('from-thread.vpconf')       # changes the shown view: emits
            state.set_own_gain_overrides_active(True)         # changes the shown view: emits
            # scope is still 'joystick' (own device): a pedals report
            # changes _child_status but not anything currently displayed
            # (any_vpconf/any_ovd are already True from the own values above)
            state.set_child_status('pedals', vpconf='child.vpconf', gain_ovd_active=True)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive()

        # Flush a queued cross-thread delivery, if that's how this landed.
        app.processEvents()

        assert len(calls) == 2

        # The child's report was still recorded correctly under the lock,
        # even though it produced no signal at the time.
        state.set_scope('pedals')
        status = state.current_status()
        assert status.vpconf == 'child.vpconf'
        assert status.ovd is True


class TestTelemManagerForwarding:
    """TelemManager.gain_overrides_active is a property whose setter
    forwards to AppState, so no write site can leave the indicator stale."""

    def test_setter_forwards_to_app_state(self, state, monkeypatch):
        import telemffb.globals as G
        from telemffb.telem.TelemManager import TelemManager
        monkeypatch.setattr(G, 'app_state', state, raising=False)
        mgr = TelemManager.__new__(TelemManager)  # skip QObject/thread __init__

        mgr.gain_overrides_active = True
        assert mgr.gain_overrides_active is True
        assert state.current_status().ovd is True

        mgr.gain_overrides_active = False
        assert state.current_status().ovd is False

    def test_setter_tolerates_missing_app_state(self, monkeypatch):
        import telemffb.globals as G
        from telemffb.telem.TelemManager import TelemManager
        monkeypatch.delattr(G, 'app_state', raising=False)
        mgr = TelemManager.__new__(TelemManager)
        mgr.gain_overrides_active = True
        assert mgr.gain_overrides_active is True


class TestActiveSettings:
    """AppState.set_active_settings/current_active_settings - drives the
    settings-tab slider highlighting (SettingsLayout), replacing the
    per-frame findChildren(NoWheelSlider) walk MainWindow.on_update_telemetry
    used to do."""

    def test_defaults_to_empty(self, state):
        assert state.current_active_settings() == ()

    def test_set_value_is_read_back(self, state):
        state.set_active_settings(['max_elevator_coeff', 'wind_effect_gain'])
        assert state.current_active_settings() == ('max_elevator_coeff', 'wind_effect_gain')

    def test_setting_the_same_members_again_does_not_emit(self, state):
        state.set_active_settings(['a', 'b'])
        calls = _capture(state.active_settings_changed)
        state.set_active_settings(['a', 'b'])
        assert calls == []

    def test_same_members_in_a_different_order_does_not_emit(self, state):
        """Dedup is by set membership, not list order - a producer rebuilding
        the list every frame by iterating a dict must not flood the signal
        just because dict iteration order shuffled with no visible change."""
        state.set_active_settings(['a', 'b'])
        calls = _capture(state.active_settings_changed)
        state.set_active_settings(['b', 'a'])
        assert calls == []

    def test_adding_a_member_emits_the_new_value(self, state):
        state.set_active_settings(['a'])
        calls = _capture(state.active_settings_changed)
        state.set_active_settings(['a', 'b'])
        assert calls == [(('a', 'b'),)]

    def test_removing_a_member_emits(self, state):
        state.set_active_settings(['a', 'b'])
        calls = _capture(state.active_settings_changed)
        state.set_active_settings(['a'])
        assert calls == [(('a',),)]

    def test_clearing_to_empty_emits_once(self, state):
        state.set_active_settings(['a'])
        calls = _capture(state.active_settings_changed)
        state.set_active_settings([])
        assert calls == [((),)]
        state.set_active_settings([])
        assert calls == [((),)]  # still empty: no re-emit

    def test_setter_from_a_background_thread(self, state, app):
        calls = _capture(state.active_settings_changed)

        def worker():
            state.set_active_settings(['tap_effect_constant_gain'])

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive()
        app.processEvents()

        assert len(calls) == 1
        assert state.current_active_settings() == ('tap_effect_constant_gain',)


def _notice(notice_id, priority):
    """A minimally-distinct Notice for id/priority-focused tests."""
    return Notice(notice_id=notice_id, priority=priority, html=f'<a>{notice_id}</a>',
                  style=notice_id, pulse=False)


class TestPromptStackDerivation:
    """AppState.set_prompt / current_prompts - the model behind PromptStack.
    Step 2a moved MainWindow's three hand-rolled QLabel pills (new-craft,
    trim-cal-discovery, profile-change) onto this."""

    def test_no_prompts_by_default(self, state):
        assert state.current_prompts() == ()

    def test_a_shown_prompt_is_returned(self, state):
        n = _notice('new_craft', NEW_CRAFT_PRIORITY)
        state.set_prompt('new_craft', n)
        assert state.current_prompts() == (n,)

    def test_clearing_with_none_hides_it(self, state):
        state.set_prompt('new_craft', _notice('new_craft', NEW_CRAFT_PRIORITY))
        state.set_prompt('new_craft', None)
        assert state.current_prompts() == ()

    def test_clearing_an_id_that_was_never_shown_is_a_no_op(self, state):
        calls = _capture(state.prompts_changed)
        state.set_prompt('trim_cal', None)
        assert calls == []
        assert state.current_prompts() == ()

    def test_setting_under_an_id_replaces_what_was_there(self, state):
        state.set_prompt('new_craft', _notice('new_craft', NEW_CRAFT_PRIORITY))
        replacement = Notice(notice_id='new_craft', priority=NEW_CRAFT_PRIORITY,
                             html='<a>different aircraft</a>', style='new_craft', pulse=True)
        state.set_prompt('new_craft', replacement)
        assert state.current_prompts() == (replacement,)


class TestPromptStackPriorityOrder:
    """The stack orders by priority (lowest first) - the same top-to-bottom
    order MainWindow's new_craft_layout laid the three old pills out in:
    new-craft, then profile-change, then trim-cal."""

    def test_all_three_sort_lowest_priority_first(self, state):
        trim = _notice('trim_cal', TRIM_CAL_PRIORITY)
        new_craft = _notice('new_craft', NEW_CRAFT_PRIORITY)
        profile = _notice('profile_change', PROFILE_CHANGE_PRIORITY)
        # set in a deliberately scrambled order - the result must not
        # depend on call order, only on priority.
        state.set_prompt('trim_cal', trim)
        state.set_prompt('profile_change', profile)
        state.set_prompt('new_craft', new_craft)
        assert state.current_prompts() == (new_craft, profile, trim)

    def test_hiding_the_top_priority_promotes_the_next(self, state):
        state.set_prompt('new_craft', _notice('new_craft', NEW_CRAFT_PRIORITY))
        trim = _notice('trim_cal', TRIM_CAL_PRIORITY)
        state.set_prompt('trim_cal', trim)
        state.set_prompt('new_craft', None)
        assert state.current_prompts() == (trim,)


class TestPromptStackDedup:
    def test_setting_an_identical_notice_again_does_not_emit(self, state):
        n = _notice('trim_cal', TRIM_CAL_PRIORITY)
        state.set_prompt('trim_cal', n)
        calls = _capture(state.prompts_changed)
        state.set_prompt('trim_cal', _notice('trim_cal', TRIM_CAL_PRIORITY))  # equal by value
        assert calls == []

    def test_a_change_to_an_inactive_id_does_not_affect_the_active_one(self, state):
        """Setting one id doesn't touch another - a per-frame producer for
        one prompt should never be able to perturb a sibling's dedup key."""
        state.set_prompt('new_craft', _notice('new_craft', NEW_CRAFT_PRIORITY))
        calls = _capture(state.prompts_changed)
        state.set_prompt('trim_cal', None)  # already absent: no-op
        assert calls == []

    def test_emits_once_per_actual_change(self, state):
        calls = _capture(state.prompts_changed)
        state.set_prompt('new_craft', _notice('new_craft', NEW_CRAFT_PRIORITY))
        assert len(calls) == 1
        state.set_prompt('new_craft', _notice('new_craft', NEW_CRAFT_PRIORITY))
        assert len(calls) == 1                     # identical: no re-emit
        state.set_prompt('new_craft', None)
        assert len(calls) == 2

    def test_setter_from_a_background_thread(self, state, app):
        """Mirrors TestThreadOriginSetter for the scope-status setters:
        set_prompt is called from whichever thread notices the condition -
        exercise the lock from a real thread, not just call it inline."""
        calls = _capture(state.prompts_changed)
        n = _notice('trim_cal', TRIM_CAL_PRIORITY)

        def worker():
            state.set_prompt('trim_cal', n)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive()
        app.processEvents()

        assert len(calls) == 1
        assert state.current_prompts() == (n,)
