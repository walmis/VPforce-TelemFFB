"""Characterization tests for ``TelemManager.config_has_changed``.

``config_has_changed`` is the gate ``TelemManager._handle_config_changes`` runs
on every telemetry frame to decide whether the on-disk XML config changed since
last frame.  It compares the (integer-second) mtimes of userconfig.xml and
defaults.xml against a stored baseline, defers the actual reload by a short
delay (to dodge multi-instance file-access races), and reports the change
exactly once.

The tests drive a controlled fake clock and a counting fake ``os.path.getmtime``
so behaviour is deterministic and we can also observe how often the mtime
syscall is issued.
"""
import os
import time as time_mod
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
import telemffb.telem.TelemManager as tm

pytestmark = [
    pytest.mark.unit,
    # importing TelemManager pulls in the simconnect package, which leaks an
    # open FileIO on its scvars.json at interpreter teardown - not ours
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


@pytest.fixture
def clock(monkeypatch, tmp_path):
    """Point G at scratch config files, reset module state, and hand the test a
    controllable fake clock plus a counting fake getmtime."""
    user = tmp_path / "userconfig_v2.xml"
    user.write_text("<TelemFFB_v2>\n</TelemFFB_v2>\n", encoding="utf-8")
    defs = tmp_path / "defaults.xml"
    defs.write_text("<defaults>\n</defaults>\n", encoding="utf-8")

    saved_paths = (G.userconfig_path, G.defaults_path)
    G.userconfig_path = str(user)
    G.defaults_path = str(defs)

    # Reset the module-level state machine to its fresh-start values.
    tm._config_mtime = 0
    tm._future_config_update_time = 0.0
    tm._pending_config_update = False
    tm._last_mtime_check = None

    now = [0.0]
    mtime = {'user': 1000.0, 'defs': 1000.0}
    calls = []

    def fake_time():
        return now[0]

    def fake_getmtime(path):
        calls.append(path)
        return mtime['user'] if path == str(user) else mtime['defs']

    monkeypatch.setattr(time_mod, 'time', fake_time)
    monkeypatch.setattr(os.path, 'getmtime', fake_getmtime)

    yield SimpleNamespace(now=now, mtime=mtime, calls=calls)

    G.userconfig_path, G.defaults_path = saved_paths


class TestConfigHasChanged:
    def test_first_call_seeds_baseline_and_reports_no_change(self, clock):
        assert tm.config_has_changed() is False
        # baseline is the sum of the two integer mtimes (1000 + 1000)
        assert tm._config_mtime == 2000
        # an immediately-following call still reports no change
        clock.now[0] = 0.5
        assert tm.config_has_changed() is False

    def test_unchanged_mtimes_never_report(self, clock):
        tm.config_has_changed()            # seed
        for _ in range(50):
            clock.now[0] += 0.5
            assert tm.config_has_changed() is False

    def test_change_is_delayed_then_reported_exactly_once(self, clock):
        tm.config_has_changed()            # seed at hash 2000
        clock.now[0] = 1.0
        tm.config_has_changed()            # still 2000, no change

        # user bumps the userconfig mtime -> hash becomes 9999 + 1000 = 10999
        clock.mtime['user'] = 9999.0
        clock.now[0] = 2.0
        # change detected, but the reload delay has not elapsed yet
        assert tm.config_has_changed() is False
        assert tm._pending_config_update is True

        # once the delay elapses, the change is reported exactly once
        clock.now[0] = 2.2
        assert tm.config_has_changed() is True
        assert tm._pending_config_update is False

        # and it is not reported again without a further change
        clock.now[0] = 3.0
        assert tm.config_has_changed() is False

    def test_mtime_stat_is_throttled_not_per_call(self, clock):
        # A stat round reads the mtime of BOTH config files, so each gated
        # check accounts for two getmtime calls. The first call must stat
        # (last check is None -> forced) -> 2 calls.
        clock.now[0] = 0.0
        tm.config_has_changed()
        assert len(clock.calls) == 2

        # A burst of 200 frames inside a single interval window (0.02s total)
        # must not re-stat: the gate pins _last_mtime_check at 0.0, so none of
        # these cross the _MTIME_CHECK_INTERVAL threshold. This is the property
        # the optimization buys - one stat round per window instead of one per
        # frame (the old code issued 2 calls per frame).
        for _ in range(200):
            clock.now[0] += 0.0001
            tm.config_has_changed()
        assert len(clock.calls) == 2

        # Once the interval elapses, the next call is allowed to stat again
        # -> two more calls (one per file).
        clock.now[0] += 0.2
        tm.config_has_changed()
        assert len(clock.calls) == 4
