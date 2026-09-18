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

from telemffb.state.app_state import AppState, ScopeStatus

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
