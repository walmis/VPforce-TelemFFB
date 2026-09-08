"""Effect preview: the headless aircraft factory and the preview runner.

Pins the two things a preview depends on that nothing else exercises:

* ``TelemManager.build_aircraft`` builds a configured aircraft from a sim
  name and model with no telemetry and no live-session side effects
  (no settings-manager state change in particular).
* ``PreviewRunner`` drives one effect method with a scripted frame
  sequence the way the live loop would (frame rotation, change detection
  across frames), forces the effect on, and frees every effect it made.

The two shipped specs prove the two stimulus kinds end to end against
the real aircraft classes: jet rumble is a 'hold', gear motion is a
'ramp' whose whole point is that the effect only plays while the value
keeps changing.
"""
import math
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
import telemffb.xmlutils as xmlutils
from telemffb.sim import aircrafts_dcs, aircrafts_msfs_xp, aircrafts_il2
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.preview import (
    Attr, PreviewSpec, PreviewRunner, TimedPreview, preview_blockers, resolve_preview_target,
    JET_ENGINE_RUMBLE, JET_IDLE_PCT, GEAR_MOTION, PROP_ENGINE_RUMBLE, STALL_BUFFET, ETL,
    ROTOR_RPM_NOMINAL, PREVIEW_SPECS, FRAME_RATE_HZ)
from telemffb.telem import TelemManager as tm
from tests.framework.base import BaseTelemetryEffectTestCase

pytestmark = [
    pytest.mark.unit,
    # importing TelemManager pulls in the simconnect package, which leaks an
    # open FileIO on its scvars.json at interpreter teardown - not ours
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestAircraftModuleForSource:
    @pytest.mark.parametrize("source, module", [
        ("DCS", aircrafts_dcs), ("BMS", aircrafts_dcs),
        ("MSFS", aircrafts_msfs_xp), ("XPLANE", aircrafts_msfs_xp),
        ("IL2", aircrafts_il2),
    ])
    def test_known_sources(self, source, module):
        assert tm.aircraft_module_for_source(source) is module

    @pytest.mark.parametrize("source", [None, "", "FSX", "msfs"])
    def test_unknown_is_none(self, source):
        assert tm.aircraft_module_for_source(source) is None


class TestBuildAircraft(BaseTelemetryEffectTestCase):
    """build_aircraft: settings resolved and applied, class picked, no
    live-session side effects."""

    def _stub_model(self, monkeypatch, cls_name, rows):
        settings = [{'name': n, 'value': v, 'unit': u} for (n, v, u) in rows]
        seen = {}

        def fake_read_single_model(the_sim, aircraft_name, input_modeltype='',
                                   instance_device='', active_profile=None):
            seen.update(sim=the_sim, name=aircraft_name,
                        modeltype=input_modeltype, device=instance_device)
            # like the real resolver: a pre-known class stands unless the
            # model names its own
            return (cls_name or input_modeltype), '.*', list(settings)

        monkeypatch.setattr(xmlutils, 'read_single_model', fake_read_single_model)
        monkeypatch.setattr(xmlutils, 'get_active_profile_for_model',
                            lambda sim, cls, model: None)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        # A settings manager whose state update EXPLODES: build_aircraft
        # must never record the preview aircraft as the current model.
        def boom(**kw):
            raise AssertionError("build_aircraft touched the settings manager")
        monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(update_state_vars=boom),
                            raising=False)
        return seen

    def test_builds_configured_instance_of_the_models_class(self, monkeypatch):
        seen = self._stub_model(monkeypatch, 'JetAircraft', [
            ('jet_engine_rumble_intensity', '0.3', ''),
            ('vne_override', '', 'kt'),          # blank stays blank (TASK004)
        ])
        ac = tm.build_aircraft('DCS', 'Preview Jet')
        assert isinstance(ac, aircrafts_dcs.JetAircraft)
        assert ac._name == 'Preview Jet'
        assert ac.jet_engine_rumble_intensity == pytest.approx(0.3)
        assert ac.vne_override == ''
        assert seen == dict(sim='DCS', name='Preview Jet', modeltype='', device='joystick')

    def test_unclassed_model_falls_back_to_the_generic_aircraft(self, monkeypatch):
        self._stub_model(monkeypatch, '', [])
        ac = tm.build_aircraft('MSFS', 'Some Addon')
        assert type(ac) is aircrafts_msfs_xp.Aircraft

    def test_class_scope_builds_that_class_from_the_class_cascade(self, monkeypatch):
        """No model, a known class: the resolver is told the class so the
        class-level settings apply, and the instance is of that class."""
        seen = self._stub_model(monkeypatch, '', [('jet_engine_rumble_intensity', '0.4', '')])
        ac = tm.build_aircraft('DCS', 'Preview', cls_name='JetAircraft')
        assert seen['modeltype'] == 'JetAircraft'
        assert isinstance(ac, aircrafts_dcs.JetAircraft)
        assert ac.jet_engine_rumble_intensity == pytest.approx(0.4)

    def test_a_model_that_names_its_class_overrides_the_given_one(self, monkeypatch):
        self._stub_model(monkeypatch, 'Helicopter', [])
        ac = tm.build_aircraft('DCS', 'UH-1H', cls_name='JetAircraft')
        assert isinstance(ac, aircrafts_dcs.Helicopter)

    def test_explicit_device_type_reaches_the_resolver(self, monkeypatch):
        seen = self._stub_model(monkeypatch, 'Aircraft', [])
        tm.build_aircraft('DCS', 'x', device_type='pedals')
        assert seen['device'] == 'pedals'

    def test_unknown_source_is_an_error(self):
        with pytest.raises(ValueError):
            tm.build_aircraft('FSX', 'x')

    def test_get_aircraft_config_still_records_the_current_model(self, monkeypatch):
        """The live path keeps its settings-manager side effect; only the
        pure resolver underneath it was extracted."""
        self._stub_model(monkeypatch, 'Aircraft', [('foo', '1', '')])
        recorded = {}
        monkeypatch.setattr(G, 'settings_mgr',
                            SimpleNamespace(update_state_vars=lambda **kw: recorded.update(kw)),
                            raising=False)
        manager = tm.TelemManager()
        params, cls_name = manager.get_aircraft_config('A Model', 'MSFS.Helicopter')
        assert params == {'foo': 1}
        assert cls_name == 'Aircraft'
        assert recorded['current_sim'] == 'MSFS'
        assert recorded['current_aircraft_name'] == 'A Model'


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------

class TestPreviewSpec:
    def _spec(self, kind, fields):
        return PreviewSpec(effect_id='x_enabled', method='m', kind=kind, fields=fields)

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            self._spec('pulse', {'*': {}})

    def test_rejects_unknown_sim_key(self):
        with pytest.raises(ValueError):
            self._spec('hold', {'FSX': {}})

    def test_resolve_rejects_unknown_sim(self):
        with pytest.raises(ValueError):
            self._spec('hold', {'*': {}}).resolve_fields(None, 'FSX', 0.0)

    def test_sim_entries_merge_over_star(self):
        spec = self._spec('hold', {'*': {'A': 1, 'B': 2}, 'XPLANE': {'B': 3, 'C': 4}})
        assert spec.resolve_fields(None, 'DCS', 0.0) == {'A': 1, 'B': 2}
        assert spec.resolve_fields(None, 'XPLANE', 0.0) == {'A': 1, 'B': 3, 'C': 4}

    def test_ramp_interpolates_a_pair(self):
        spec = self._spec('ramp', {'*': {'v': (0.0, 2.0), 'k': 7}})
        assert spec.resolve_fields(None, 'DCS', 0.0) == {'v': 0.0, 'k': 7}
        assert spec.resolve_fields(None, 'DCS', 0.5) == {'v': 1.0, 'k': 7}
        assert spec.resolve_fields(None, 'DCS', 1.0) == {'v': 2.0, 'k': 7}

    def test_edge_steps_at_the_midpoint(self):
        spec = self._spec('edge', {'*': {'v': (10, 20)}})
        assert spec.resolve_fields(None, 'DCS', 0.49)['v'] == 10
        assert spec.resolve_fields(None, 'DCS', 0.5)['v'] == 20

    def test_hold_uses_the_first_of_a_pair(self):
        spec = self._spec('hold', {'*': {'v': (10, 20)}})
        assert spec.resolve_fields(None, 'DCS', 1.0)['v'] == 10

    def test_pair_endpoints_may_name_aircraft_attributes(self):
        """A sweep between the profile's own thresholds, no lambda needed."""
        ac = SimpleNamespace(lo=600, hi=2600)
        spec = self._spec('ramp', {'*': {'RPM': ('lo', 'hi'), 'mixed': (0, 'hi')}})
        assert spec.resolve_fields(ac, 'DCS', 0.0) == {'RPM': 600, 'mixed': 0}
        assert spec.resolve_fields(ac, 'DCS', 0.5) == {'RPM': 1600, 'mixed': 1300}
        assert spec.resolve_fields(ac, 'DCS', 1.0) == {'RPM': 2600, 'mixed': 2600}

    def test_dwell_holds_both_ends_and_sweeps_between(self):
        spec = PreviewSpec(effect_id='x_enabled', method='m', kind='ramp',
                           fields={'*': {'v': (0.0, 1.0)}}, duration=10.0, dwell=2.0)
        sp = spec.stimulus_progress
        assert sp(0.0) == 0.0 and sp(0.2) == 0.0            # leading dwell
        assert sp(0.5) == pytest.approx(0.5)                # middle of the sweep
        assert sp(0.35) == pytest.approx(0.25)
        assert sp(0.8) == 1.0 and sp(1.0) == 1.0            # trailing dwell
        assert spec.resolve_fields(None, 'DCS', 0.1) == {'v': 0.0}
        assert spec.resolve_fields(None, 'DCS', 0.9) == {'v': 1.0}

    def test_no_dwell_is_the_identity(self):
        spec = self._spec('ramp', {'*': {'v': (0.0, 1.0)}})
        assert spec.stimulus_progress(0.3) == 0.3

    def test_dwell_shapes_callables_too(self):
        spec = PreviewSpec(effect_id='x_enabled', method='m', kind='ramp',
                           fields={'*': {'v': lambda a, p: p}}, duration=10.0, dwell=2.0)
        assert spec.resolve_fields(None, 'DCS', 0.1) == {'v': 0.0}

    def test_dwell_must_fit_inside_the_duration(self):
        with pytest.raises(ValueError):
            PreviewSpec(effect_id='x_enabled', method='m', kind='ramp',
                        fields={'*': {}}, duration=4.0, dwell=2.0)
        with pytest.raises(ValueError):
            PreviewSpec(effect_id='x_enabled', method='m', kind='ramp',
                        fields={'*': {}}, duration=4.0, dwell=-1.0)

    def test_attr_resolves_on_the_instance_in_fields_and_pairs(self):
        ac = SimpleNamespace(blades=4, lo=1, hi=3)
        spec = self._spec('ramp', {'*': {'n': Attr('blades'), 'v': (Attr('lo'), 'hi')}})
        assert spec.resolve_fields(ac, 'DCS', 0.5) == {'n': 4, 'v': 2}

    def test_schedule_holds_and_sweeps_in_order_and_sets_the_duration(self):
        spec = PreviewSpec(effect_id='x_enabled', method='m', kind='ramp',
                           fields={'*': {'v': (0.0, 1.0)}}, duration=99.0,
                           schedule=((4.0, 0.5, 0.5), (6.0, 0.0, 1.0)))
        assert spec.duration == 10.0                         # segments' sum, not 99
        sp = spec.stimulus_progress
        assert sp(0.0) == 0.5 and sp(0.2) == 0.5             # hold at the peak
        assert sp(0.4) == pytest.approx(0.0)                 # sweep starts
        assert sp(0.7) == pytest.approx(0.5)
        assert sp(1.0) == pytest.approx(1.0)

    def test_dwell_is_sugar_for_a_three_segment_schedule(self):
        spec = PreviewSpec(effect_id='x_enabled', method='m', kind='ramp',
                           fields={'*': {}}, duration=14.0, dwell=4.0)
        assert spec.schedule == ((4.0, 0.0, 0.0), (6.0, 0.0, 1.0), (4.0, 1.0, 1.0))
        plain = self._spec('ramp', {'*': {}})
        assert plain.schedule == ((plain.duration, 0.0, 1.0),)

    def test_schedule_validation(self):
        with pytest.raises(ValueError):                     # not both
            PreviewSpec(effect_id='x_enabled', method='m', kind='ramp', fields={'*': {}},
                        duration=10.0, dwell=2.0, schedule=((10.0, 0.0, 1.0),))
        with pytest.raises(ValueError):                     # zero-length segment
            PreviewSpec(effect_id='x_enabled', method='m', kind='ramp', fields={'*': {}},
                        schedule=((0.0, 0.0, 1.0),))
        with pytest.raises(ValueError):                     # progress out of range
            PreviewSpec(effect_id='x_enabled', method='m', kind='ramp', fields={'*': {}},
                        schedule=((1.0, 0.0, 1.5),))

    def test_kwargs_resolve_like_fields(self):
        ac = SimpleNamespace(blades=4)
        spec = PreviewSpec(effect_id='x_enabled', method='m', kind='ramp', fields={'*': {}},
                           kwargs={'blade_ct': Attr('blades'), 'p': lambda a, p: p})
        assert spec.resolve_kwargs(ac, 0.25) == {'blade_ct': 4, 'p': 0.25}
        assert self._spec('hold', {'*': {}}).kwargs == {}

    def test_callable_sees_aircraft_and_progress(self):
        ac = SimpleNamespace(peak_rpm=650)
        spec = self._spec('hold', {'*': {'RPM': lambda a, p: a.peak_rpm,
                                         'Gear': lambda a, p: [p]}})
        assert spec.resolve_fields(ac, 'DCS', 0.25) == {'RPM': 650, 'Gear': [0.25]}

    def test_registry_is_keyed_by_effect_id(self):
        assert PREVIEW_SPECS['engine_jet_rumble_enabled'] is JET_ENGINE_RUMBLE
        assert PREVIEW_SPECS['gear_motion_effect_enabled'] is GEAR_MOTION
        assert PREVIEW_SPECS['engine_prop_rumble_enabled'] is PROP_ENGINE_RUMBLE


# ---------------------------------------------------------------------------
# Runner mechanics (a stand-in aircraft; no effect code involved)
# ---------------------------------------------------------------------------

class FakeAircraft:
    """The runner's contract with an aircraft: a name, the two frame
    slots, an ``effects`` dispenser and the method the spec names."""

    def __init__(self, effects):
        self._name = 'fake'
        self._telem_data = BaseTelemetryData()
        self._last_telem_data = BaseTelemetryData()
        self._effects = effects
        self.calls = []
        self.x_enabled = False

    @property
    def effects(self):
        return self._effects

    def record(self, frame, **kwargs):
        self.calls.append(frame)
        self.kwargs_seen = kwargs

    def boom(self, frame):
        raise RuntimeError("effect blew up")


class TestPreviewRunnerMechanics(BaseTelemetryEffectTestCase):
    def _runner(self, spec_kw=None, **runner_kw):
        kw = dict(effect_id='x_enabled', method='record', kind='ramp',
                  fields={'*': {'v': (0.0, 1.0)}}, duration=1.0, tail=0.0)
        kw.update(spec_kw or {})
        ac = FakeAircraft(self.mock_effects)
        runner = PreviewRunner(ac, PreviewSpec(**kw), 'DCS', **runner_kw)
        return ac, runner

    def test_frame_count_follows_duration_and_rate(self):
        _, runner = self._runner(dict(duration=1.0), frame_rate=30.0)
        assert runner.frames_total == 30
        assert runner.period == pytest.approx(1 / 30)

    def test_at_least_two_frames_so_an_edge_has_both_sides(self):
        _, runner = self._runner(dict(duration=0.0))
        assert runner.frames_total == 2

    def test_tail_repeats_the_last_frame_before_cleanup(self):
        """A one-shot fired on the final scripted frame needs time to play
        before the effects are destroyed; the tail feeds the last frame
        again (progress pinned at 1.0) for that long."""
        ac, runner = self._runner(dict(duration=1.0, tail=0.5), frame_rate=10.0)
        assert (runner.frames_total, runner.tail_frames, runner.steps_total) == (10, 5, 15)
        progress = []
        while True:
            progress.append(runner.progress)
            if not runner.step():
                break
        assert len(ac.calls) == 15
        assert progress[9] == pytest.approx(1.0)
        assert progress[10:] == [pytest.approx(1.0)] * 5
        assert [f['v'] for f in ac.calls[9:]] == [pytest.approx(1.0)] * 6
        assert not runner.finished or runner.frame_index == 15

    def test_progress_spans_zero_to_one_across_the_run(self):
        ac, runner = self._runner(frame_rate=5.0)          # 5 frames
        seen = []
        while True:
            seen.append(runner.progress)
            if not runner.step():
                break
        assert seen == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])
        assert [f['v'] for f in ac.calls] == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])

    def test_kwargs_reach_the_effect_method(self):
        ac, runner = self._runner(dict(kwargs={'blade_ct': 3}))
        runner.step()
        assert ac.kwargs_seen == {'blade_ct': 3}

    def test_frame_carries_sim_device_and_name(self):
        ac, runner = self._runner(device_type='pedals')
        runner.step()
        frame = ac.calls[0]
        assert frame['src'] == 'DCS'
        assert frame['FFBType'] == 'pedals'
        assert frame['N'] == 'fake'
        assert ac._telem_data is frame

    def test_device_type_defaults_to_this_instance(self, monkeypatch):
        monkeypatch.setattr(G, 'device_type', 'collective', raising=False)
        ac, runner = self._runner()
        runner.step()
        assert ac.calls[0]['FFBType'] == 'collective'

    def test_frames_rotate_like_the_live_loop(self):
        ac, runner = self._runner(frame_rate=5.0)
        runner.step()
        runner.step()
        assert ac._last_telem_data['v'] == pytest.approx(0.0)
        assert ac._telem_data['v'] == pytest.approx(0.25)

    def test_effect_is_forced_on_by_default_and_not_when_asked(self):
        ac, _ = self._runner()
        assert ac.x_enabled is True
        ac2, _ = self._runner(force_enable=False)
        assert ac2.x_enabled is False

    def test_finish_frees_effects_once_and_step_is_inert_after(self):
        ac, runner = self._runner(frame_rate=2.0)          # 2 frames
        self.mock_effects['slot'].start()
        clears = []
        orig = self.mock_effects.clear
        self.mock_effects.clear = lambda: (clears.append(1), orig())
        assert runner.step() is True
        assert runner.step() is False                      # last frame finishes
        assert runner.finished
        assert 'slot' not in self.mock_effects
        runner.finish()
        assert runner.step() is False
        assert len(ac.calls) == 2
        assert clears == [1]

    def test_a_raising_effect_stops_and_cleans_up(self):
        ac, runner = self._runner(dict(method='boom'))
        self.mock_effects['slot'].start()
        assert runner.step() is False
        assert runner.finished
        assert 'slot' not in self.mock_effects

    def test_run_sleeps_one_period_between_frames(self):
        ac, runner = self._runner(frame_rate=10.0)         # 10 frames
        sleeps = []
        runner.run(sleep=sleeps.append)
        assert len(ac.calls) == 10
        assert sleeps == [pytest.approx(0.1)] * 9
        assert runner.finished

    def test_rejects_a_sim_the_spec_does_not_cover(self):
        ac = FakeAircraft(self.mock_effects)
        spec = PreviewSpec(effect_id='x_enabled', method='record', kind='hold',
                           fields={'*': {}}, sims=('DCS',))
        with pytest.raises(ValueError):
            PreviewRunner(ac, spec, 'MSFS')

    def test_rejects_an_aircraft_without_the_method(self):
        ac = FakeAircraft(self.mock_effects)
        spec = PreviewSpec(effect_id='x_enabled', method='nope', kind='hold', fields={'*': {}})
        with pytest.raises(ValueError):
            PreviewRunner(ac, spec, 'DCS')


class TestResolvePreviewTarget:
    def test_settings_tab_model_selection_wins(self):
        mgr = SimpleNamespace(current_sim='MSFS', current_aircraft_name='C172',
                              current_class='PropellerAircraft')
        assert resolve_preview_target(mgr) == ('MSFS', 'C172', 'PropellerAircraft')

    def test_class_scope_carries_the_class_with_no_model(self):
        """Offline editor at CLASS scope: the user is tuning class
        defaults, so the preview must resolve the class-level cascade."""
        mgr = SimpleNamespace(current_sim='DCS', current_aircraft_name='',
                              current_class='JetAircraft')
        assert resolve_preview_target(mgr) == ('DCS', 'Preview', 'JetAircraft')

    def test_no_selection_falls_back_to_a_generic_dcs_aircraft(self):
        mgr = SimpleNamespace(current_sim='nothing', current_aircraft_name='',
                              current_class='')
        assert resolve_preview_target(mgr) == ('DCS', 'Preview', '')

    def test_sim_scope_has_no_class(self):
        mgr = SimpleNamespace(current_sim='IL2', current_aircraft_name='', current_class='')
        assert resolve_preview_target(mgr) == ('IL2', 'Preview', '')

    def test_survives_a_missing_manager(self):
        assert resolve_preview_target(None) == ('DCS', 'Preview', '')


@pytest.fixture
def app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestTimedPreview(BaseTelemetryEffectTestCase):
    """The Qt clock around the runner: one step per tick, stops itself
    on the last frame, an early stop still frees effects, and the
    finished callback fires exactly once either way."""

    def _timed(self, app, frames=3, rate=10.0):
        ac = FakeAircraft(self.mock_effects)
        spec = PreviewSpec(effect_id='x_enabled', method='record', kind='hold',
                           fields={'*': {'v': 1}}, duration=frames / rate, tail=0.0)
        runner = PreviewRunner(ac, spec, 'DCS', frame_rate=rate)
        done = []
        timed = TimedPreview(runner, on_finished=lambda: done.append(1))
        return ac, runner, timed, done

    def test_interval_follows_the_frame_rate(self, app):
        _, _, timed, _ = self._timed(app, rate=30.0)
        assert timed.interval_ms == 33
        _, _, timed10, _ = self._timed(app, rate=10.0)
        assert timed10.interval_ms == 100

    def test_ticks_step_and_the_last_one_stops_the_clock(self, app):
        ac, runner, timed, done = self._timed(app, frames=3)
        timed.start()
        assert timed.running
        timed._tick()
        timed._tick()
        assert timed.running and not runner.finished and done == []
        timed._tick()
        assert not timed.running and runner.finished
        assert len(ac.calls) == 3
        assert done == [1]

    def test_stop_ends_early_frees_effects_and_notifies_once(self, app):
        ac, runner, timed, done = self._timed(app, frames=3)
        self.mock_effects['slot'].start()
        timed.start()
        timed._tick()
        timed.stop()
        assert not timed.running and runner.finished
        assert 'slot' not in self.mock_effects
        assert len(ac.calls) == 1
        timed.stop()
        timed._tick()
        assert done == [1]


class TestPreviewBlockers:
    def test_clear_when_idle_with_a_device(self):
        assert preview_blockers(current_aircraft=None, device_alive=True) == []

    def test_live_aircraft_blocks(self):
        reasons = preview_blockers(current_aircraft=object(), device_alive=True)
        assert len(reasons) == 1 and 'sim session' in reasons[0]

    def test_dead_device_blocks(self):
        reasons = preview_blockers(current_aircraft=None, device_alive=False)
        assert len(reasons) == 1 and 'device' in reasons[0]

    def test_both_reported(self):
        assert len(preview_blockers(current_aircraft=object(), device_alive=False)) == 2


# ---------------------------------------------------------------------------
# The shipped specs against the real aircraft classes
# ---------------------------------------------------------------------------

class TestJetEngineRumblePreview(BaseTelemetryEffectTestCase):
    """Sweep from idle to 100% with dwells: intensity scales with RPM and
    the frequency climbs, on every sim, reading the sim's own RPM field."""

    def _aircraft(self, cls):
        ac = cls('preview')
        ac.jet_engine_rumble_intensity = 0.3
        ac.jet_engine_rumble_freq = 45
        ac.engine_jet_rumble_enabled = False   # the preview must force it on
        return ac

    @pytest.mark.parametrize("cls, sim, field", [
        (aircrafts_dcs.Aircraft, 'DCS', 'EngRPM'),
        (aircrafts_dcs.Aircraft, 'BMS', 'EngRPM'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS', 'EngRPM'),
        (aircrafts_msfs_xp.Aircraft, 'XPLANE', 'EngPCT'),
        (aircrafts_il2.Aircraft, 'IL2', 'EngRPM'),
    ])
    def test_sweeps_idle_to_full_power(self, cls, sim, field):
        ac = self._aircraft(cls)
        runner = PreviewRunner(ac, JET_ENGINE_RUMBLE, sim, frame_rate=10.0)   # 140 frames
        runner.step()
        main = self.mock_effects['je_rumble_1_1']
        cross = self.mock_effects['je_rumble_2_1']
        assert ac._telem_data[field] == JET_IDLE_PCT
        assert main.started and cross.started
        assert main._periodic[1] == pytest.approx(0.3 * JET_IDLE_PCT / 100)
        assert main._periodic[2] == 0 and cross._periodic[2] == 90
        # idle sits the frequency at base + 10 * 0.6 (plus a slow modulation term)
        assert main._periodic[0] == pytest.approx(45 + 6, abs=3.5)
        rpm_seen = [ac._telem_data[field]]
        for _ in range(runner.frames_total - 2):       # up to the last audible frame
            runner.step()
            rpm_seen.append(ac._telem_data[field])
        assert rpm_seen[:40] == [JET_IDLE_PCT] * 40                 # idle dwell
        assert rpm_seen[100:] == [pytest.approx(100)] * 39          # full-power dwell
        assert main._periodic[1] == pytest.approx(0.3)              # intensity * 100/100
        assert main._periodic[0] == pytest.approx(55, abs=3.5)
        assert runner.step() is False
        assert not self.mock_effects.dict

    def test_disabled_toggle_is_honoured_when_not_forced(self):
        ac = self._aircraft(aircrafts_dcs.Aircraft)
        runner = PreviewRunner(ac, JET_ENGINE_RUMBLE, 'DCS', force_enable=False)
        runner.step()
        assert 'je_rumble_1_1' not in self.mock_effects

    def test_full_run_leaves_nothing_behind(self):
        ac = self._aircraft(aircrafts_dcs.Aircraft)
        runner = PreviewRunner(ac, JET_ENGINE_RUMBLE, 'DCS', frame_rate=10.0)
        runner.run(sleep=lambda s: None)
        assert runner.finished
        assert not self.mock_effects.dict


class TestPropEngineRumblePreview(BaseTelemetryEffectTestCase):
    """'ramp' as a sweep: RPM runs from the profile's Low RPM point to its
    High RPM point, so the preview plays the taper (intensity falling,
    frequency rising) rather than one level."""

    LOW, HIGH = 650, 2800

    def _aircraft(self, cls):
        ac = cls('preview')
        ac.engine_prop_rumble_enabled = False   # forced on by the preview
        ac.engine_rumble_lowrpm = self.LOW
        ac.engine_rumble_lowrpm_intensity = 0.06
        ac.engine_rumble_highrpm = self.HIGH
        ac.engine_rumble_highrpm_intensity = 0.03
        return ac

    @pytest.mark.parametrize("cls, sim, field", [
        (aircrafts_dcs.Aircraft, 'DCS', 'ActualRPM'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS', 'PropRPM'),
        (aircrafts_msfs_xp.Aircraft, 'XPLANE', 'PropRPM'),
        (aircrafts_il2.Aircraft, 'IL2', 'RPM'),
    ])
    def test_sweeps_the_profiles_rpm_range_on_each_sims_field(self, cls, sim, field):
        ac = self._aircraft(cls)
        runner = PreviewRunner(ac, PROP_ENGINE_RUMBLE, sim, frame_rate=10.0)   # 140 frames
        assert runner.frames_total == 140
        rpm_seen = []
        main = None
        # Up to the penultimate frame: the final one is destroyed in the
        # same step it plays (tail 0), so the last audible frame is the
        # one to inspect - and with the dwell it is already at High RPM.
        for _ in range(runner.frames_total - 1):
            runner.step()
            rpm_seen.append(ac._telem_data[field])
            main = self.mock_effects['prop_rpm0-1']
            if len(rpm_seen) == 1:
                assert main.started
                assert main._periodic[0] == pytest.approx(self.LOW / 60)
                start_mag = main._periodic[1]
                assert start_mag == pytest.approx(0.06)     # Low RPM intensity, full beat depth
        # 4 s dwell at each end of a 14 s run at 10 Hz: 40 frames flat
        # at Low RPM, 40 flat at High RPM, the sweep between.
        assert rpm_seen[:40] == [pytest.approx(self.LOW)] * 40
        assert rpm_seen[100:] == [pytest.approx(self.HIGH)] * 39
        assert all(a < b for a, b in zip(rpm_seen[40:100], rpm_seen[41:101]))
        assert main._periodic[0] == pytest.approx(self.HIGH / 60)
        end_mag = main._periodic[1]
        assert end_mag < start_mag                           # the taper
        beat_depth = (self.LOW / self.HIGH) ** 2
        assert end_mag == pytest.approx(
            0.03 * (2.0 - beat_depth ** 2) ** 0.5, abs=1e-6)  # High RPM intensity, beat faded
        assert self.mock_effects['prop_rpm1-1']._periodic[2] == 90
        assert runner.step() is False
        assert runner.finished and not self.mock_effects.dict

    def test_bms_is_not_offered(self):
        ac = self._aircraft(aircrafts_dcs.Aircraft)
        with pytest.raises(ValueError):
            PreviewRunner(ac, PROP_ENGINE_RUMBLE, 'BMS')


class TestStallBuffetPreview(BaseTelemetryEffectTestCase):
    """AoA sweeps onset -> stall then holds at stall.  With no sim-side
    thresholds in the frame the effect uses the profile's own band and
    the legacy airflow scale (1.0 at 75 kt), so magnitude runs 0 -> the
    configured intensity."""

    def _aircraft(self, cls):
        ac = cls('preview')
        ac.aoa_buffeting_enabled = False     # forced on by the preview
        ac.buffeting_intensity = 0.2
        ac.buffet_aoa = 10.0
        ac.stall_aoa = 15.0
        ac.aoa_buffet_freq = 13
        ac.stall_buffet_style = "Classic"
        return ac

    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'),
        (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'),
        (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_sweeps_the_band_then_holds_at_stall(self, cls, sim):
        ac = self._aircraft(cls)
        runner = PreviewRunner(ac, STALL_BUFFET, sim, frame_rate=10.0)   # 80 frames
        assert runner.frames_total == 80
        mags = []
        for _ in range(runner.frames_total - 1):           # up to the last audible frame
            runner.step()
            fx = self.mock_effects['buffeting']
            mags.append(fx._periodic[1])
            if len(mags) == 1:
                assert fx.started
                assert ac._telem_data['AoA'] == pytest.approx(10.0)      # onset
                assert ac._telem_data.get('StallAoA') is None          # fallback band
                assert fx._periodic[0] == 13
        assert mags[0] == pytest.approx(0.0)                           # silent at onset
        # 3 s onset sweep, 4 s hold, 1 s recovery at 10 Hz over 8 s:
        # frame 30 (t = 30/79 * 8 = 3.04 s) is the first held frame and
        # frame 70 (t = 7.09 s) the first of the fade
        assert all(a <= b + 1e-9 for a, b in zip(mags[:30], mags[1:31]))   # rising
        assert mags[30:70] == [pytest.approx(0.2)] * 40                # the hold at stall
        assert all(a > b for a, b in zip(mags[70:], mags[71:]))        # fading
        assert mags[-1] < 0.05                                         # nearly gone
        assert ac._telem_data['AoA'] < 11.0                            # back near onset
        assert runner.step() is False
        assert not self.mock_effects.dict

    def test_il2_is_not_offered(self):
        with pytest.raises(ValueError):
            PreviewRunner(self._aircraft(aircrafts_il2.Aircraft), STALL_BUFFET, 'IL2')


class TestEtlPreview(BaseTelemetryEffectTestCase):
    """The event itself: one acceleration through the band, Gaussian
    peak at mid-band.  The blade count reaches the effect the way the
    live loop passes it, so the frequency is the aircraft's rather than
    the hard-coded fallback."""

    def _aircraft(self, cls):
        ac = cls('preview')
        ac.etl_effect_enable = False         # forced on by the preview
        ac.etl_effect_intensity = 0.2
        ac.etl_start_speed = 6.0
        ac.etl_stop_speed = 22.0
        ac.overspeed_shake_start = 70.0
        ac.rotor_blade_count = 4
        return ac

    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Helicopter, 'DCS'),
        (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'),
        (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_one_pass_through_the_band_at_the_aircrafts_frequency(self, cls, sim):
        ac = self._aircraft(cls)
        runner = PreviewRunner(ac, ETL, sim, frame_rate=10.0)   # 50 frames
        assert runner.frames_total == 50
        mags, tas_seen = [], []
        for _ in range(runner.frames_total - 1):
            runner.step()
            tas_seen.append(ac._telem_data['TAS'])
            mags.append(self.mock_effects['etlY']._periodic[1])
        y = self.mock_effects['etlY']._periodic
        x = self.mock_effects['etlX']._periodic
        expected_freq = ROTOR_RPM_NOMINAL / 75 * 4          # 16 Hz: blade count got through
        assert y[0] == pytest.approx(expected_freq) and y[2] == 0
        assert x[0] == pytest.approx(expected_freq + 4) and x[2] == 90
        edge = 0.2 * math.exp(-0.5 * (0.5 / 0.35) ** 2)
        assert tas_seen[0] == pytest.approx(6.0)             # etl_start
        assert mags[0] == pytest.approx(edge, rel=1e-3)      # Gaussian tail at the edge
        assert all(a < b for a, b in zip(tas_seen, tas_seen[1:]))   # accelerating through
        assert tas_seen[-1] == pytest.approx(22.0, rel=2e-2)   # last audible frame, 48/49 of the way
        assert max(mags) == pytest.approx(0.2, rel=1e-2)     # peak = intensity, mid-band
        assert mags[24] > mags[0] and mags[24] > mags[-1]
        assert 'overspeedX' not in self.mock_effects
        assert runner.step() is False
        assert not self.mock_effects.dict

    def test_without_the_blade_count_the_effect_would_guess(self):
        """Documents why the spec passes blade_ct: the bare call falls
        back to 2 blades at 250 RPM regardless of telemetry."""
        ac = self._aircraft(aircrafts_dcs.Helicopter)
        frame = PreviewRunner(ac, ETL, 'DCS').build_frame(0.0)
        ac._telem_data = frame
        ac.ac_calc_etl_effect(frame)
        assert self.mock_effects['etlY']._periodic[0] == pytest.approx(250 / 75 * 2)


class TestGearMotionPreview(BaseTelemetryEffectTestCase):
    """'ramp': the motion effect plays only while the gear value keeps
    changing, so a swept value keeps it alive for the whole run and the
    exact 1.0 endpoint fires the clunk."""

    def _aircraft(self, cls):
        ac = cls('preview')
        ac.gear_motion_intensity = 0.5
        ac.gear_motion_effect_enabled = False  # forced on by the preview
        ac.gear_buffet_effect_enabled = True   # must stay silent at IAS 0
        return ac

    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'),
        (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_motion_plays_every_changing_frame_and_clunks_at_the_end(self, cls, sim):
        ac = self._aircraft(cls)
        runner = PreviewRunner(ac, GEAR_MOTION, sim, frame_rate=10.0)   # 30 frames + 5 tail
        assert runner.tail_frames > 0, "the ramp spec needs a tail or the clunk dies unheard"
        for _ in range(runner.frames_total):
            assert runner.step() is True
        # Last scripted frame landed on exactly 1.0: the clunk is on the
        # device and the tail is keeping it alive.
        assert 'gearclunk' in self.mock_effects, "clunk never fired"
        freq, mag, direction, kw = self.mock_effects['gearclunk']._periodic
        assert direction == 180                      # gear reached 'down'
        assert mag == pytest.approx(1.0)             # 0.5 * 3, clamped
        assert kw['duration'] == 40
        assert self.mock_effects['gearmovement'].start_count == runner.frames_total - 1
        while runner.step():
            pass
        assert not self.mock_effects.dict           # everything freed after the tail

    def test_motion_is_alive_mid_run_and_buffet_stays_silent(self):
        ac = self._aircraft(aircrafts_dcs.Aircraft)
        runner = PreviewRunner(ac, GEAR_MOTION, 'DCS', frame_rate=10.0)
        runner.step()                                 # first frame: change tracker primes
        runner.step()                                 # second: value changed -> plays
        motion = self.mock_effects['gearmovement']
        assert motion.started
        assert motion._periodic[1] == pytest.approx(0.5)
        assert 'gearbuffet' not in self.mock_effects
        assert 0.0 < ac._telem_data['gear_value'] < 1.0

    def test_no_clunk_before_the_endpoint(self):
        ac = self._aircraft(aircrafts_dcs.Aircraft)
        runner = PreviewRunner(ac, GEAR_MOTION, 'DCS', frame_rate=10.0)
        for _ in range(runner.frames_total - 1):
            runner.step()
        assert 'gearclunk' not in self.mock_effects
