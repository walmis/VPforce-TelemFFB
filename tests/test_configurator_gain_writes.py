"""When TelemFFB writes Configurator gains to the device, and when it does not.

docs/telemffb/vpconf-profiles.md: overrides win over profile gains, and an
aircraft without overrides gets the baseline back.  The device is written in
exactly three situations - the exit restore (main), an aircraft that has
overrides of its own, and an aircraft that has none following one that did.
The last is skipped when a vpconf profile push is already on its way, since the
profile writes all seven sliders and a revert would only be overwritten.
"""
import json
from types import SimpleNamespace

import pytest

import telemffb.globals as G
from telemffb.telem.TelemManager import TelemManager

pytestmark = [pytest.mark.unit]

OVERRIDES = json.dumps({"master_gain": {"enabled": True, "value": 0}})


@pytest.fixture
def mgr(monkeypatch):
    """A manager whose only live collaborator is the gain dialog."""
    m = TelemManager.__new__(TelemManager)          # skip QObject/thread __init__
    m._gain_overrides_active = False
    writes = []
    monkeypatch.setattr(TelemManager, '_device_has_gains', lambda self: True, raising=False)
    monkeypatch.setattr(TelemManager, 'gain_overrides_active',
                        property(lambda self: self._gain_overrides_active,
                                 lambda self, v: setattr(self, '_gain_overrides_active', v)),
                        raising=False)
    monkeypatch.setattr(G, 'gain_override_dialog', SimpleNamespace(
        set_gains_from_state=lambda state: writes.append(('overrides', state)),
        set_gains_from_object=lambda obj: writes.append(('baseline', obj))), raising=False)
    monkeypatch.setattr(G, 'vpconf_configurator_gains', SimpleNamespace(master_gain=50), raising=False)
    monkeypatch.setattr(G, 'current_configurator_gains', None, raising=False)
    monkeypatch.setattr('telemffb.telem.TelemManager.log_device_gains', lambda *a, **k: None)
    m.writes = writes
    return m


class TestWritesOnAircraftLoad:
    def test_an_aircraft_with_overrides_is_written(self, mgr):
        mgr._handle_configurator_overrides(
            {'configurator_override_enabled': True, 'configurator_gains': OVERRIDES})
        assert [kind for kind, _ in mgr.writes] == ['overrides']
        assert mgr.gain_overrides_active is True

    def test_an_aircraft_with_none_after_one_with_gets_the_baseline_back(self, mgr):
        mgr.gain_overrides_active = True
        mgr._handle_configurator_overrides({})
        assert [kind for kind, _ in mgr.writes] == ['baseline']
        assert mgr.gain_overrides_active is False

    def test_a_profile_push_covers_the_revert(self, mgr):
        """The push writes all seven sliders, so undoing the previous aircraft's
        overrides first would be a write the profile immediately overwrites."""
        mgr.gain_overrides_active = True
        mgr._handle_configurator_overrides({'vpconf': 'C:/x.vpconf'}, vpconf_pushed=True)
        assert mgr.writes == []
        assert mgr.gain_overrides_active is False

    def test_nothing_is_written_when_no_override_was_ever_applied(self, mgr):
        mgr._handle_configurator_overrides({'vpconf': 'C:/x.vpconf'})
        assert mgr.writes == []

    def test_the_toggle_without_gains_counts_as_no_overrides(self, mgr):
        mgr.gain_overrides_active = True
        mgr._handle_configurator_overrides(
            {'configurator_override_enabled': True, 'configurator_gains': 'none'})
        assert [kind for kind, _ in mgr.writes] == ['baseline']

    def test_a_device_without_gains_is_never_written(self, mgr, monkeypatch):
        monkeypatch.setattr(TelemManager, '_device_has_gains', lambda self: False, raising=False)
        mgr.gain_overrides_active = True
        mgr._handle_configurator_overrides(
            {'configurator_override_enabled': True, 'configurator_gains': OVERRIDES})
        assert mgr.writes == []
