"""Gain overrides survive the vpconf push that lands after them.

An aircraft change applies the aircraft's gain overrides immediately, but
``upload_vpconf_profile`` runs the Configurator in a background thread that
finishes ~100 ms later and writes every gain slider from the profile.  Without
a second pass the override is overwritten by the profile it was meant to sit
on top of.
"""
import threading

import pytest

import telemffb.globals as G
from telemffb.telem.TelemManager import TelemManager

pytestmark = [pytest.mark.unit]


@pytest.fixture
def mgr(monkeypatch):
    """A manager with only the state this path touches."""
    m = TelemManager.__new__(TelemManager)      # skip QObject/thread __init__
    m._cond = threading.Condition()             # the loop's wakeup, normally built in __init__
    m.currentAircraftConfig = {'configurator_override_enabled': True,
                               'configurator_gains': '{"master_gain": {"enabled": true, "value": 0}}'}
    applied = []
    monkeypatch.setattr(TelemManager, '_handle_configurator_overrides',
                        lambda self, params, context="gain overrides": applied.append((params, context)),
                        raising=False)
    monkeypatch.setattr(G, 'vpconf_init_pending', False, raising=False)
    m.applied = applied
    return m


def _frames(m, count=1):
    """Run what the processing loop runs per frame, before the frame itself.

    Deliberately not submit_frame: that is the listener thread, and the device
    must be driven from the processing thread like every other gain write.
    """
    for _ in range(count):
        m._reapply_configurator_overrides()
    return m.applied


class TestOverrideReapply:
    def test_the_request_wakes_the_loop(self, mgr):
        """Otherwise the device keeps the profile's gains until the loop's timeout
        expires, and a paused sim sends nothing to wake it sooner."""
        woken = []
        cond = threading.Condition()
        mgr._cond = type('C', (), {
            '__enter__': lambda self: cond.__enter__(),
            '__exit__': lambda self, *a: cond.__exit__(*a),
            'notify': lambda self: woken.append(True),
        })()
        mgr.request_configurator_override_reapply()
        assert woken == [True]

    def test_a_finished_push_gets_the_overrides_written_again(self, mgr):
        mgr.request_configurator_override_reapply()
        assert [p for p, _ in _frames(mgr)] == [mgr.currentAircraftConfig]

    def test_it_happens_once_not_on_every_frame(self, mgr):
        mgr.request_configurator_override_reapply()
        assert len(_frames(mgr, count=5)) == 1

    def test_nothing_is_rewritten_without_a_push(self, mgr):
        assert _frames(mgr, count=3) == []

    def test_the_reapply_says_so_in_the_log(self, mgr):
        """The log drops a message identical to one it just printed, so a re-apply
        that reported itself the same way as the first pass would be invisible."""
        mgr.request_configurator_override_reapply()
        contexts = [c for _, c in _frames(mgr)]
        assert contexts and contexts[0] != "gain overrides"

    def test_a_push_still_running_waits(self, mgr, monkeypatch):
        """The gate is the push, not the frame: writing while the Configurator
        is still going would be overwritten again."""
        monkeypatch.setattr(G, 'vpconf_init_pending', True, raising=False)
        mgr.request_configurator_override_reapply()
        assert _frames(mgr, count=2) == []

        monkeypatch.setattr(G, 'vpconf_init_pending', False, raising=False)
        assert [p for p, _ in _frames(mgr)] == [mgr.currentAircraftConfig]


def test_the_processing_loop_calls_it_every_frame():
    """The write has to happen on the processing thread, so the run loop is the
    only correct caller; an earlier version called it from submit_frame (the
    listener thread) and the re-apply never showed up in the field."""
    import inspect

    from telemffb.telem import TelemManager as module

    run_src = inspect.getsource(module.TelemManager.run)
    assert '_reapply_configurator_overrides' in run_src
    # and not behind the frame check: the sim is usually paused right after an
    # aircraft loads, which is exactly when the push lands
    before_data, _, after_data = run_src.partition('if data:')
    assert '_reapply_configurator_overrides' in before_data
    submit_src = inspect.getsource(module.TelemManager.submit_frame)
    assert '_reapply_configurator_overrides' not in submit_src
