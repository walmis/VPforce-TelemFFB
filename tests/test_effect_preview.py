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
    AFTERBURNER, STICK_SHAKER, OVERSPEED_SHAKE, GEAR_BUFFET, SPEEDBRAKE_BUFFET,
    SPOILER_BUFFET, FLAPS_MOTION, SPEEDBRAKE_MOTION, SPOILER_MOTION, CANOPY_MOTION,
    TAILHOOK_MOTION, FUELBOOM_MOTION, WINGFOLD_MOTION,
    GUNFIRE, WEAPON_RELEASE, COUNTERMEASURES, DAMAGE,
    IL2_GUNFIRE, IL2_BOMB_RELEASE, IL2_ROCKET_RELEASE, steps, RandomHits, Jitter,
    TOUCHDOWN, DECELERATION, RUNWAY_RUMBLE, REFERENCE_SPRING, Gusts, TURBULENCE, WIND,
    ROTOR_RPM_NOMINAL, HOLD_SECONDS, PREVIEW_SPECS, PREVIEWS_BY_ROW, preview_for_row,
    FRAME_RATE_HZ)
from telemffb.telem import TelemManager as tm
from tests.framework.base import BaseTelemetryEffectTestCase, MockEffectDispenser

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

    def test_method_may_be_keyed_by_sim(self):
        spec = PreviewSpec(effect_id='x_enabled', kind='hold', fields={'*': {}},
                           method={'*': 'generic', 'MSFS': 'msfs_specific'})
        assert spec.method_for('DCS') == 'generic'
        assert spec.method_for('MSFS') == 'msfs_specific'
        only = PreviewSpec(effect_id='x_enabled', kind='hold', fields={'*': {}},
                           method={'MSFS': 'msfs_specific'})
        with pytest.raises(ValueError):
            only.method_for('DCS')
        assert self._spec('hold', {'*': {}}).method_for('IL2') == 'm'

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

    def test_registry_is_keyed_by_name_which_defaults_to_the_toggle(self):
        assert PREVIEW_SPECS['engine_jet_rumble_enabled'] is JET_ENGINE_RUMBLE
        assert PREVIEW_SPECS['gear_motion_effect_enabled'] is GEAR_MOTION
        assert PREVIEW_SPECS['engine_prop_rumble_enabled'] is PROP_ENGINE_RUMBLE
        assert JET_ENGINE_RUMBLE.name == JET_ENGINE_RUMBLE.effect_id
        # one toggle, three tuned effects, three previews
        assert PREVIEW_SPECS['il2_gunfire'] is IL2_GUNFIRE
        assert PREVIEW_SPECS['il2_bombs'] is IL2_BOMB_RELEASE
        assert PREVIEW_SPECS['il2_rockets'] is IL2_ROCKET_RELEASE
        assert {s.effect_id for s in (IL2_GUNFIRE, IL2_BOMB_RELEASE, IL2_ROCKET_RELEASE)} \
            == {'il2_enable_weapons'}


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

    def test_force_attrs_ride_along_with_the_toggle(self):
        ac, _ = self._runner(dict(force_attrs={'master': True, 'mode': 'basic'}))
        assert ac.master is True and ac.mode == 'basic'
        ac2, _ = self._runner(dict(force_attrs={'master': True}), force_enable=False)
        assert not hasattr(ac2, 'master')

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

    def test_streaming_aircraft_blocks(self):
        reasons = preview_blockers(current_aircraft=object(), device_alive=True)
        assert len(reasons) == 1 and 'streaming' in reasons[0]

    def test_loaded_aircraft_with_telemetry_paused_is_fine(self):
        """Offline editing with a sim in the background: the preview has
        its own effect table, so nothing to protect."""
        assert preview_blockers(current_aircraft=object(), device_alive=True,
                                telemetry_paused=True) == []

    def test_dead_device_blocks(self):
        reasons = preview_blockers(current_aircraft=None, device_alive=False)
        assert len(reasons) == 1 and 'device' in reasons[0]

    def test_both_reported(self):
        assert len(preview_blockers(current_aircraft=object(), device_alive=False)) == 2


class TestPrivateEffects(BaseTelemetryEffectTestCase):
    """The preview aircraft's own effect table: construction and cleanup
    never touch the shared one a live aircraft uses."""

    def test_effects_property_defaults_to_the_shared_dispenser(self):
        ac = aircrafts_dcs.Aircraft('live')
        assert ac.effects is self.mock_effects

    def test_an_instance_may_carry_its_own(self):
        ac = aircrafts_dcs.Aircraft('live')
        own = MockEffectDispenser()
        ac._effects = own
        assert ac.effects is own
        ac.effects['x'].start()
        assert 'x' in own and 'x' not in self.mock_effects

    def test_build_aircraft_leaves_a_live_aircrafts_effects_alone(self, monkeypatch):
        import telemffb.utils as utils
        live_spring = self.mock_effects['spring']          # a paused session's spring
        live_spring.start()
        monkeypatch.setattr(xmlutils, 'read_single_model',
                            lambda *a, **k: ('Aircraft', '.*', []))
        monkeypatch.setattr(xmlutils, 'get_active_profile_for_model', lambda *a: None)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        ac = tm.build_aircraft('DCS', 'Preview')
        assert isinstance(ac.effects, utils.Dispenser)
        assert ac.effects is not self.mock_effects
        # construction set up the aircraft's own spring handle in ITS table
        assert set(ac.effects.dict) == {'spring'}
        assert ac.effects.dict['spring'] is not live_spring
        assert self.mock_effects['spring'] is live_spring   # __init__ did not clear the shared one
        assert live_spring.started

    def test_a_preview_run_frees_only_its_own_effects(self):
        live_spring = self.mock_effects['spring']
        live_spring.start()
        # the private table must be in place BEFORE __init__ (which clears
        # whatever it sees) - the order build_aircraft uses
        ac = aircrafts_dcs.Aircraft.__new__(aircrafts_dcs.Aircraft)
        ac._effects = MockEffectDispenser()
        ac.__init__('preview')
        assert self.mock_effects['spring'] is live_spring   # construction left it alone
        ac.jet_engine_rumble_intensity = 0.3
        runner = PreviewRunner(ac, JET_ENGINE_RUMBLE, 'DCS', frame_rate=10.0)
        runner.step()
        assert 'je_rumble_1_1' in ac.effects and 'je_rumble_1_1' not in self.mock_effects
        runner.run(sleep=lambda s: None)
        assert not ac.effects.dict
        assert self.mock_effects['spring'] is live_spring and live_spring.started

    def test_shared_path_still_clears_on_construction(self):
        """The live behaviour is unchanged: a new aircraft on the shared
        table starts from an empty one."""
        self.mock_effects['stale'].start()
        aircrafts_dcs.Aircraft('next')
        assert 'stale' not in self.mock_effects


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


class HoldPreviewCase(BaseTelemetryEffectTestCase):
    """Shared shape for the on/off holds: run two frames (an effect that
    keys on change primes on the first), check the slot, run to the end,
    check nothing is left."""

    def _run_hold(self, ac, spec, sim, frames=2):
        runner = PreviewRunner(ac, spec, sim, frame_rate=10.0)
        assert runner.frames_total == HOLD_SECONDS * 10
        for _ in range(frames):
            assert runner.step() is True
        return runner

    def _finish(self, runner):
        while runner.step():
            pass
        assert not self.mock_effects.dict


class TestAfterburnerPreview(HoldPreviewCase):
    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'), (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_lights_one_frame_in_at_the_configured_intensity(self, cls, sim, monkeypatch):
        # The effect re-issues on change of its slow modulation term; live
        # that ticks every frame, in a test the frames are microseconds
        # apart, so stand in a modulation that advances per call.
        import telemffb.utils as utils
        ticks = iter(range(10_000))
        monkeypatch.setattr(utils, 'sine_point_in_time',
                            lambda *a, **k: next(ticks) * 0.01)
        ac = cls('preview')
        ac.afterburner_effect_enabled = False
        ac.afterburner_effect_intensity = 0.3
        runner = self._run_hold(ac, AFTERBURNER, sim, frames=1)
        assert 'ab_rumble_1_1' not in self.mock_effects      # tracker primes on frame 0
        runner.step()
        assert ac._telem_data['Afterburner'] == 1
        main = self.mock_effects['ab_rumble_1_1']
        assert main.started
        assert main._periodic[1] == pytest.approx(0.3)          # intensity * AB 1
        assert main._periodic[0] == pytest.approx(20, abs=2.5)  # 20 Hz + modulation
        assert self.mock_effects['ab_rumble_2_1']._periodic[2] == 45
        self._finish(runner)


class TestStickShakerPreview(HoldPreviewCase):
    def test_dcs_and_bms_shake_above_the_profile_aoa(self):
        for sim in ('DCS', 'BMS'):
            self.setup_method()
            ac = aircrafts_dcs.Aircraft('preview')
            ac.enable_stick_shaker = False
            ac.stick_shaker_aoa = 22.3
            ac.stick_shaker_intensity = 0.5
            ac.stick_shaker_frequency = 40
            runner = self._run_hold(ac, STICK_SHAKER, sim)
            assert runner.method_name == 'dcs_update_stick_shaker'
            assert ac._telem_data['AoA'] == pytest.approx(27.3)
            assert ac._telem_data['SimOnGround'] == 0
            one = self.mock_effects['stick_shaker1']._periodic
            two = self.mock_effects['stick_shaker2']._periodic
            assert one[:3] == (40, 0.5, 0) and two[:3] == (40, 0.5, 180)
            self._finish(runner)

    def test_msfs_shakes_on_the_stall_warning_flag(self):
        ac = aircrafts_msfs_xp.Aircraft('preview')
        ac.enable_stick_shaker = False
        ac.stick_shaker_intensity = 0.5
        runner = self._run_hold(ac, STICK_SHAKER, 'MSFS')
        assert runner.method_name == 'msfs_update_stick_shaker'
        assert ac._telem_data['StallWarning'] == 1
        assert self.mock_effects['stick_shaker']._periodic[:3] == (14, 0.5, 0)
        self._finish(runner)

    def test_xplane_is_not_offered(self):
        with pytest.raises(ValueError):
            PreviewRunner(aircrafts_msfs_xp.Aircraft('preview'), STICK_SHAKER, 'XPLANE')


class TestOverspeedPreview(HoldPreviewCase):
    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Helicopter, 'DCS'), (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_full_strength_past_the_onset_at_the_aircrafts_frequency(self, cls, sim):
        ac = cls('preview')
        ac.overspeed_effect_enable = False
        ac.overspeed_shake_intensity = 0.2
        ac.overspeed_shake_start = 70.0
        ac.etl_effect_enable = True
        ac.rotor_blade_count = 4
        runner = self._run_hold(ac, OVERSPEED_SHAKE, sim)
        assert ac._telem_data['TAS'] == pytest.approx(85.0)
        y = self.mock_effects['overspeedY']._periodic
        assert y[0] == pytest.approx(ROTOR_RPM_NOMINAL / 75 * 4 * 0.75)   # 12 Hz
        assert y[1] == pytest.approx(0.2)                                # full: 15 m/s past onset
        assert self.mock_effects['overspeedX']._periodic[2] == 90
        assert 'etlY' not in self.mock_effects                           # clear of the band
        self._finish(runner)


class TestGearBuffetPreview(HoldPreviewCase):
    def _aircraft(self, cls):
        ac = cls('preview')
        ac.gear_buffet_effect_enabled = False
        ac.gear_buffet_intensity = 0.15
        ac.gear_buffet_freq = 10
        ac.gear_buffet_speed_low = 100
        ac.gear_buffet_speed_high = 150
        ac.gear_motion_effect_enabled = True    # must not fire on a steady value
        return ac

    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'), (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_gear_down_at_the_top_of_the_band_is_full_intensity(self, cls, sim):
        ac = self._aircraft(cls)
        runner = self._run_hold(ac, GEAR_BUFFET, sim)
        buffet = self.mock_effects['gearbuffet']._periodic
        assert buffet[0] == 10
        assert buffet[1] == pytest.approx(0.15)
        assert self.mock_effects['gearbuffet2']._periodic[2] == 90
        assert 'gearmovement' not in self.mock_effects
        self._finish(runner)


class TestSpeedbrakeAndSpoilerBuffetPreview(HoldPreviewCase):
    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'), (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_speedbrake_fully_deployed_is_full_intensity(self, cls, sim):
        ac = cls('preview')
        ac.speedbrake_buffet_effect_enabled = False
        ac.speedbrake_buffet_intensity = 0.15
        ac.speedbrake_motion_effect_enabled = True
        runner = self._run_hold(ac, SPEEDBRAKE_BUFFET, sim)
        buffet = self.mock_effects['speedbrakebuffet']._periodic
        assert buffet[1] == pytest.approx(0.15)
        assert 'speedbrakemovement' not in self.mock_effects
        self._finish(runner)

    def test_speedbrake_is_not_offered_on_msfs(self):
        with pytest.raises(ValueError):
            PreviewRunner(aircrafts_msfs_xp.Aircraft('preview'), SPEEDBRAKE_BUFFET, 'MSFS')

    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'), (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_spoilers_fully_deployed_at_the_top_speed_is_full_intensity(self, cls, sim):
        ac = cls('preview')
        ac.spoiler_buffet_effect_enabled = False
        ac.spoiler_buffet_intensity = 0.15
        runner = self._run_hold(ac, SPOILER_BUFFET, sim)
        assert ac._telem_data['IAS'] == pytest.approx(ac.spoiler_spd_thresh_hi)
        assert self.mock_effects['spoilerbuffet1-1']._periodic[1] == pytest.approx(0.15)
        assert self.mock_effects['spoilerbuffet2-1']._periodic[2] == 90
        self._finish(runner)


class RampPreviewCase(BaseTelemetryEffectTestCase):
    """Shared shape for the motion ramps: the motion slot plays while the
    value moves, the tail lets the endpoint clunk (where the effect has
    one) fire after the change-tracker's quiet period, and nothing is
    left behind."""

    def _ramp(self, ac, spec, sim):
        runner = PreviewRunner(ac, spec, sim, frame_rate=10.0)   # 30 frames + 5 tail
        assert runner.tail_frames == 5
        for _ in range(runner.frames_total):
            runner.step()
        return runner

    def _settle_and_finish(self, runner, quiet=False):
        """Run the tail.  ``quiet`` waits out the change tracker's
        delta_ms first, as the real tail's wall-clock would."""
        if quiet:
            import time
            time.sleep(0.25)
        runner.step()                      # first tail frame: motion ends, clunk (if any)
        snapshot = {k: v._periodic for k, v in self.mock_effects.dict.items()
                    if getattr(v, '_periodic', None)}
        while runner.step():
            pass
        assert not self.mock_effects.dict
        return snapshot


class TestMotionRampPreviews(RampPreviewCase):
    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'), (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
        (aircrafts_il2.Aircraft, 'IL2'),
    ])
    def test_flaps(self, cls, sim):
        ac = cls('preview')
        ac.flaps_motion_effect_enabled = False
        ac.flaps_motion_intensity = 0.2
        runner = self._ramp(ac, FLAPS_MOTION, sim)
        motion = self.mock_effects['flapsmovement']
        assert motion.started and motion._periodic[:3] == (180, 0.2, 0)
        assert ac._telem_data['Flaps'] == pytest.approx(1.0)
        self._settle_and_finish(runner)

    @pytest.mark.parametrize("sim", ['DCS', 'XPLANE', 'BMS'])
    def test_speedbrake_moves_without_buffet(self, sim):
        cls = aircrafts_dcs.Aircraft if sim != 'XPLANE' else aircrafts_msfs_xp.Aircraft
        ac = cls('preview')
        ac.speedbrake_motion_effect_enabled = False
        ac.speedbrake_motion_intensity = 0.2
        ac.speedbrake_buffet_effect_enabled = True
        runner = self._ramp(ac, SPEEDBRAKE_MOTION, sim)
        assert self.mock_effects['speedbrakemovement']._periodic[:3] == (180, 0.2, 0)
        assert 'speedbrakebuffet' not in self.mock_effects
        self._settle_and_finish(runner)

    @pytest.mark.parametrize("sim", ['DCS', 'XPLANE', 'BMS'])
    def test_spoilers_move_without_buffet(self, sim):
        cls = aircrafts_dcs.Aircraft if sim != 'XPLANE' else aircrafts_msfs_xp.Aircraft
        ac = cls('preview')
        ac.spoiler_motion_effect_enabled = False
        ac.spoiler_motion_intensity = 0.2
        ac.spoiler_buffet_effect_enabled = True
        runner = self._ramp(ac, SPOILER_MOTION, sim)
        assert self.mock_effects['spoilermovement']._periodic[:3] == (118, 0.2, 0)
        assert self.mock_effects['spoilermovement2']._periodic[2] == 90
        # the buffet branch touches its slots to stop them (create-on-access
        # in the dispenser) but must not have started one at IAS 0
        buffet = self.mock_effects.get('spoilerbuffet1-1')
        assert buffet is None or not buffet.started
        self._settle_and_finish(runner)

    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_canopy_closes_and_clunks_shut(self, cls, sim):
        ac = cls('preview')
        ac.canopy_motion_effect_enabled = False
        ac.canopy_motion_intensity = 0.2
        runner = self._ramp(ac, CANOPY_MOTION, sim)
        assert self.mock_effects['canopymovement']._periodic[:3] == (120, 0.2, 0)
        assert ac._telem_data['Canopy'] == pytest.approx(0.0)       # closed
        played = self._settle_and_finish(runner, quiet=True)
        assert played['canopyclunk'][:3] == (10, 0.4, 180)          # 2x intensity, shut

    def test_tailhook_extends_and_clunks(self):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.tailhook_motion_effect_enabled = False
        ac.tailhook_motion_intensity = 0.2
        runner = self._ramp(ac, TAILHOOK_MOTION, 'DCS')
        assert self.mock_effects['hookmovement']._periodic[:3] == (160, 0.2, 0)
        played = self._settle_and_finish(runner, quiet=True)
        assert played['clunk'][:3] == (10, 0.4, 0)                  # (1 - hook) * 180 at hook 1

    def test_fuel_boom_extends_and_clunks(self):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.fuelboom_motion_effect_enabled = False
        ac.fuelboom_motion_intensity = 0.2
        runner = self._ramp(ac, FUELBOOM_MOTION, 'DCS')
        assert self.mock_effects['boommovement']._periodic[:3] == (150, 0.2, 0)
        played = self._settle_and_finish(runner, quiet=True)
        assert played['clunk'][:3] == (10, 0.4, 0)                  # extended: seats forward

    def test_wing_fold_on_the_ground_with_two_clunks(self):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.wingfold_motion_effect_enabled = False
        ac.wingfold_motion_intensity = 0.2
        runner = self._ramp(ac, WINGFOLD_MOTION, 'DCS')
        assert ac._telem_data['SimOnGround'] == 1
        one = self.mock_effects['wingfoldmovement_1']._periodic
        two = self.mock_effects['wingfoldmovement_2']._periodic
        assert one[:3] == (100, 0.2, 45) and two[:3] == (100, 0.2, 225)
        played = self._settle_and_finish(runner, quiet=True)
        assert played['wingfoldclunk1'][2] == 90 and played['wingfoldclunk2'][2] == 270
        assert played['wingfoldclunk1'][3]['duration'] == 100

    def test_joystick_only_motions_are_silent_on_pedals(self):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.tailhook_motion_intensity = 0.2
        runner = PreviewRunner(ac, TAILHOOK_MOTION, 'DCS', device_type='pedals', frame_rate=10.0)
        for _ in range(3):
            runner.step()
        assert 'hookmovement' not in self.mock_effects


class TestPreviewRows:
    """Every preview names the intensity row(s) that host its play
    button; every such row is a real setting; no row hosts two."""

    @pytest.fixture(scope='class')
    def setting_names(self):
        import xml.etree.ElementTree as ET
        root = ET.parse('defaults.xml').getroot()
        return {(d.findtext('name') or '').strip() for d in root.iter('defaults')}

    def test_every_spec_has_at_least_one_row(self):
        assert all(spec.rows for spec in PREVIEW_SPECS.values())

    def test_every_spec_states_its_reference_condition(self):
        """The tooltip and the constant-force popup slot this into a fixed
        template; an empty one would regress to vague text."""
        missing = [name for name, spec in PREVIEW_SPECS.items() if not spec.reference.strip()]
        assert not missing
        # a fragment, not a sentence: it is embedded mid-sentence
        assert all(not spec.reference.endswith('.') for spec in PREVIEW_SPECS.values())

    def test_every_row_is_a_setting_in_defaults_xml(self, setting_names):
        missing = [r for r in PREVIEWS_BY_ROW if r not in setting_names]
        assert not missing

    def test_rows_are_strengths_not_toggles_or_thresholds(self):
        toggles = {spec.effect_id for spec in PREVIEW_SPECS.values()}
        assert not (set(PREVIEWS_BY_ROW) & toggles)
        assert all('intensity' in r or 'force' in r for r in PREVIEWS_BY_ROW)

    def test_lookup(self):
        assert preview_for_row('engine_rumble_lowrpm_intensity') is PROP_ENGINE_RUMBLE
        assert preview_for_row('engine_rumble_highrpm_intensity') is PROP_ENGINE_RUMBLE
        assert preview_for_row('il2_bomb_release_intensity') is IL2_BOMB_RELEASE
        assert preview_for_row('engine_prop_rumble_enabled') is None
        assert preview_for_row('no_such_setting') is None


class TestStepsHelper:
    def test_changes_count_times_evenly(self):
        f = steps(3, start=4, step=-1)
        seen = [f(None, i / 99) for i in range(100)]
        assert seen[0] == 4 and seen[-1] == 1
        changes = [i for i in range(1, 100) if seen[i] != seen[i - 1]]
        assert len(changes) == 3
        assert changes == [33, 66, 99]                # thirds of the run


class EdgePreviewCase(BaseTelemetryEffectTestCase):
    """Run an edge spec frame by frame, recording every frame on which
    a slot was (re)started, so a test can count events and their
    spacing.  Frames are instantaneous here, so the change tracker's
    quiet-period stops never fire mid-run; only start counts are
    meaningful."""

    def _events(self, ac, spec, sim, slot, rate=10.0):
        runner = PreviewRunner(ac, spec, sim, frame_rate=rate)
        fired, last_count, params = [], 0, None
        for i in range(runner.frames_total):
            runner.step()
            fx = self.mock_effects.get(slot)
            if fx is not None and fx.start_count > last_count:
                fired.append(i)
                last_count = fx.start_count
                params = fx._periodic
        while runner.step():
            pass
        assert not self.mock_effects.dict
        return fired, params


class TestWeaponEdgePreviews(EdgePreviewCase):
    def _aircraft(self, sim):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.gunfire_effect_enabled = False
        ac.weapon_release_effect_enabled = False
        ac.countermeasure_effect_enabled = False
        ac.gun_vibration_intensity = 0.3
        ac.weapon_release_intensity = 0.25
        ac.cm_vibration_intensity = 0.2
        ac.weapon_effect_direction = 45
        return ac

    @pytest.mark.parametrize("sim", ['DCS', 'BMS'])
    def test_gunfire_is_a_continuous_burst(self, sim):
        fired, params = self._events(self._aircraft(sim), GUNFIRE, sim, 'gunfire')
        # 20 frames; the first primes the tracker, every later one fires
        assert fired == list(range(1, 20))
        freq, mag, direction, kw = params
        assert (freq, mag, direction) == (10, 0.3, 45)
        assert kw == {'effect_type': 6, 'duration': 80}      # sawtooth up
        assert 'payload_rel' not in self.mock_effects or \
            self.mock_effects.get('payload_rel') is None

    @pytest.mark.parametrize("sim", ['DCS', 'BMS'])
    def test_weapon_release_fires_three_times_a_second_apart(self, sim):
        fired, params = self._events(self._aircraft(sim), WEAPON_RELEASE, sim, 'payload_rel')
        assert fired == [10, 20, 29]                          # thirds of 30 frames
        assert params[:3] == (10, 0.25, 45)
        assert params[3] == {'effect_type': 3, 'duration': 80}   # square

    @pytest.mark.parametrize("sim", ['DCS', 'BMS'])
    def test_countermeasures_fire_four_times(self, sim):
        fired, params = self._events(self._aircraft(sim), COUNTERMEASURES, sim, 'cm')
        assert fired == [5, 10, 15, 19]                       # quarters of 20 frames
        assert params[:3] == (50, 0.2, 45)
        assert params[3] == {'duration': 80}

    def test_only_the_previewed_weapon_effect_plays(self):
        ac = self._aircraft('DCS')
        ac.gunfire_effect_enabled = True                      # user has it on
        self._events(ac, WEAPON_RELEASE, 'DCS', 'payload_rel')
        # a steady Gun value never fires the gunfire slot
        fx = self.mock_effects.get('gunfire')
        assert fx is None or fx.start_count == 0


class TestIl2WeaponPreviews(EdgePreviewCase):
    """The basic IL-2 weapon path, one preview per tuned effect, with the
    shake master forced on and the dynamic gunfire mode (which needs
    real gun telemetry) switched off for the throwaway."""

    def _aircraft(self):
        ac = aircrafts_il2.Aircraft('preview')
        ac.il2_shake_master = 0
        ac.il2_enable_weapons = 0
        ac.il2_dynamic_gunfire_mode = True             # must be switched off for the preview
        ac.il2_weapon_release_intensity = 0.3
        ac.il2_bomb_release_intensity = 0.25
        ac.il2_rocket_release_intensity = 0.2
        return ac

    def test_gunfire_is_one_held_burst(self):
        ac = self._aircraft()
        fired, params = self._events(ac, IL2_GUNFIRE, 'IL2', 'il2_gunfire')
        assert ac.il2_shake_master is True and ac.il2_dynamic_gunfire_mode is False
        assert fired == [1]                                          # primes, fires, holds
        assert params == (10, 0.3, 0, {'effect_type': 3})           # 600 rpm square
        for slot in ('il2_bombs', 'il2_rockets'):
            fx = self.mock_effects.get(slot)
            assert fx is None or fx.start_count == 0

    def test_bomb_drops_once_at_the_midpoint(self):
        fired, params = self._events(self._aircraft(), IL2_BOMB_RELEASE, 'IL2', 'il2_bombs')
        assert fired == [5]                                          # 10 frames, edge at 0.5
        assert params == (10, 0.25, 0, {'effect_type': 6, 'duration': 80})

    def test_rocket_fires_once_at_the_midpoint(self):
        fired, params = self._events(self._aircraft(), IL2_ROCKET_RELEASE, 'IL2', 'il2_rockets')
        assert fired == [5]
        assert params == (50, 0.2, 0, {'effect_type': 3, 'duration': 80})

    def test_only_il2(self):
        with pytest.raises(ValueError):
            PreviewRunner(aircrafts_dcs.Aircraft('preview'), IL2_GUNFIRE, 'DCS')


class TestRandomHits:
    def _ac(self):
        return SimpleNamespace()

    def test_schedule_is_drawn_once_per_aircraft_and_counts_up(self):
        import random
        hits = RandomHits(hits=(3, 3), cluster_chance=0.0, rng=random.Random(7))
        ac = self._ac()
        times = hits.schedule(ac)
        assert len(times) == 3 and list(times) == sorted(times)
        assert all(0.05 <= t <= 0.95 for t in times)
        assert hits.schedule(ac) is times                    # cached on the throwaway
        assert hits(ac, 0.0) == 0
        assert hits(ac, times[1]) == 2
        assert hits(ac, 1.0) == 3

    def test_each_aircraft_gets_its_own_draw(self):
        hits = RandomHits(hits=(6, 6), cluster_chance=0.0)   # system entropy
        a, b = hits.schedule(self._ac()), hits.schedule(self._ac())
        assert a != b

    def test_clusters_trail_a_hit_closely(self):
        import random
        hits = RandomHits(hits=(2, 2), cluster_chance=1.0, cluster_span=0.03,
                          rng=random.Random(3))
        times = hits.schedule(self._ac())
        assert 4 <= len(times) <= 6                          # 2 lone + 1-2 trailing each
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert min(gaps) <= 0.03                              # at least one tight pair


class TestDamageEdgePreview(EdgePreviewCase):
    def _seeded(self, monkeypatch, seed):
        import random
        import telemffb.preview as preview
        monkeypatch.setattr(preview._DAMAGE_HITS, 'rng', random.Random(seed))

    def test_dcs_hits_land_at_irregular_moments_within_the_intensity_band(self, monkeypatch):
        self._seeded(monkeypatch, 11)
        ac = aircrafts_dcs.Aircraft('preview')
        ac.damage_effect_enabled = False
        ac.damage_effect_intensity = 0.4
        fired, params = self._events(ac, DAMAGE, 'DCS', 'damage', rate=30.0)   # 150 frames
        assert 4 <= len(fired) <= 24
        gaps = {b - a for a, b in zip(fired, fired[1:])}
        assert len(gaps) >= 2                                 # not a metronome
        assert 0 < fired[0] and fired[-1] < 149              # inside the run, before the tail
        freq, mag, direction, kw = params
        assert freq == 10 and 0 <= direction <= 359
        assert 0.2 <= mag <= 0.6                              # 0.5x .. 1.5x intensity
        assert kw['duration'] == 30 and kw['effect_type'] in (3, 4, 5)

    def test_every_press_is_a_fresh_draw(self):
        seen = []
        for _ in range(2):
            self.setup_method()
            ac = aircrafts_dcs.Aircraft('preview')
            ac.damage_effect_intensity = 0.4
            fired, _ = self._events(ac, DAMAGE, 'DCS', 'damage', rate=30.0)
            seen.append(fired)
        assert seen[0] != seen[1]

    def test_il2_plays_hit_and_damage_slots_on_one_schedule(self, monkeypatch):
        self._seeded(monkeypatch, 5)
        ac = aircrafts_il2.Aircraft('preview')
        ac.damage_effect_enabled = False
        ac.damage_effect_intensity = 0.4
        runner = PreviewRunner(ac, DAMAGE, 'IL2', frame_rate=30.0)
        damage_fired, hit_fired = [], []
        counts = {'damage': 0, 'hit': 0}
        for i in range(runner.frames_total):
            runner.step()
            for slot, out in (('damage', damage_fired), ('hit', hit_fired)):
                fx = self.mock_effects.get(slot)
                if fx is not None and fx.start_count > counts[slot]:
                    counts[slot] = fx.start_count
                    out.append(i)
        assert damage_fired and damage_fired == hit_fired    # same schedule, both slots
        params = self.mock_effects['damage']._periodic
        assert params[:2] == (10, 0.4) and params[3] == {'effect_type': 3, 'duration': 30}
        while runner.step():
            pass
        assert not self.mock_effects.dict

    def test_msfs_is_not_offered(self):
        with pytest.raises(ValueError):
            PreviewRunner(aircrafts_msfs_xp.Aircraft('preview'), DAMAGE, 'MSFS')


class TestJitter:
    def test_walks_within_bounds_and_changes(self):
        import random
        j = Jitter(center=0.5, amplitude=0.4, size=3, rng=random.Random(1))
        ac = SimpleNamespace()
        seen = [j(ac, i / 50) for i in range(50)]
        assert all(len(v) == 3 for v in seen)
        assert all(0.1 - 1e-9 <= x <= 0.9 + 1e-9 for v in seen for x in v)
        assert any(a != b for a, b in zip(seen, seen[1:]))
        assert seen[0] != seen[-1]

    def test_scalar_form_and_per_aircraft_state(self):
        j = Jitter(center=0.5, amplitude=0.5)
        a, b = SimpleNamespace(), SimpleNamespace()
        va = [j(a, 0) for _ in range(5)]
        vb = [j(b, 0) for _ in range(5)]
        assert all(isinstance(x, float) for x in va)
        assert va != vb


class TestConstantForceGuard(BaseTelemetryEffectTestCase):
    def test_reference_spring_up_for_the_run_and_freed_after(self):
        ac = FakeAircraft(self.mock_effects)
        spec = PreviewSpec(effect_id='x_enabled', method='record', kind='hold',
                           fields={'*': {}}, duration=0.3, tail=0.0, constant_force=True)
        runner = PreviewRunner(ac, spec, 'DCS', frame_rate=10.0)
        assert 'preview_spring' not in self.mock_effects        # nothing until the run starts
        runner.step()
        spring = self.mock_effects['preview_spring']
        assert spring.started
        assert spring.get_coefficients() == (REFERENCE_SPRING, REFERENCE_SPRING)
        while runner.step():
            pass
        assert not self.mock_effects.dict

    def test_no_spring_for_a_periodic_preview(self):
        ac = FakeAircraft(self.mock_effects)
        spec = PreviewSpec(effect_id='x_enabled', method='record', kind='hold',
                           fields={'*': {}}, duration=0.3, tail=0.0)
        PreviewRunner(ac, spec, 'DCS', frame_rate=10.0).step()
        assert 'preview_spring' not in self.mock_effects

    def test_the_constant_force_specs_are_flagged(self):
        flagged = {s.name for s in PREVIEW_SPECS.values() if s.constant_force}
        assert flagged == {'touchdown_effect_enabled', 'deceleration_effect_enable',
                           'runway_rumble_enabled', 'turbulence_effect_enable',
                           'wind_effect_enabled'}


class TestGusts:
    def test_three_components_with_the_stated_rms_and_band(self):
        import random, math
        g = Gusts(rms=(2.0, 3.0, 1.0), steady=(0.0, 0.0, 60.0), band=(0.3, 2.0),
                  rng=random.Random(4))
        ac = SimpleNamespace(_preview_duration=20.0)
        samples = [g(ac, i / 2000) for i in range(2000)]          # 20 s at 100 Hz
        assert all(len(v) == 3 for v in samples)
        for axis, (rms, base) in enumerate(((2.0, 0.0), (3.0, 0.0), (1.0, 60.0))):
            vals = [v[axis] - base for v in samples]
            measured = math.sqrt(sum(x * x for x in vals) / len(vals))
            assert measured == pytest.approx(rms, rel=0.35)      # random phases: loose
        for axis in g._state(ac):
            assert all(0.3 <= f <= 2.0 for f, _, _ in axis)

    def test_each_run_is_a_fresh_draw(self):
        g = Gusts()
        a, b = SimpleNamespace(_preview_duration=8.0), SimpleNamespace(_preview_duration=8.0)
        assert g(a, 0.37) != g(b, 0.37)

    def test_runner_stamps_the_duration(self):
        ac = SimpleNamespace(_telem_data=BaseTelemetryData(), _last_telem_data=BaseTelemetryData(),
                             _name='x', effects=None, record=lambda *a, **k: None, x_enabled=False)
        spec = PreviewSpec(effect_id='x_enabled', method='record', kind='hold',
                           fields={'*': {}}, duration=8.0)
        PreviewRunner(ac, spec, 'DCS')
        assert ac._preview_duration == 8.0


class TestFrameArg(BaseTelemetryEffectTestCase):
    def test_a_method_without_a_frame_argument_reads_the_bound_frame(self):
        ac = FakeAircraft(self.mock_effects)
        seen = []
        ac.no_frame = lambda **kw: seen.append((ac._telem_data['v'], kw))
        spec = PreviewSpec(effect_id='x_enabled', method='no_frame', kind='hold',
                           fields={'*': {'v': 7}}, kwargs={'k': 1}, frame_arg=False,
                           duration=0.2, tail=0.0)
        PreviewRunner(ac, spec, 'DCS', frame_rate=10.0).step()
        assert seen == [(7, {'k': 1})]


def _advancing_clock(monkeypatch, step=1.0 / 30):
    """The turbulence modulator and the wind filters normalise by wall-clock
    dt; test frames are microseconds apart, which would make their filters
    do nothing.  Advance perf_counter a frame per call."""
    import time
    start = time.perf_counter()
    ticks = iter(range(1, 1_000_000))
    monkeypatch.setattr(time, 'perf_counter', lambda: start + next(ticks) * step)


class TestTurbulencePreview(BaseTelemetryEffectTestCase):
    def _aircraft(self):
        ac = aircrafts_msfs_xp.Aircraft('preview')
        ac.turbulence_effect_enable = False
        ac.turbulence_hpf_alpha = 0.95
        ac.turbulence_smoothing_alpha = 0.3
        ac.turbulence_sensitivity = 0.7
        ac.turbulence_intensity = 0.3
        return ac

    @pytest.mark.parametrize("sim", ['MSFS', 'XPLANE'])
    def test_joystick_feels_a_varying_push_capped_at_the_intensity(self, sim, monkeypatch):
        _advancing_clock(monkeypatch)
        ac = self._aircraft()
        runner = PreviewRunner(ac, TURBULENCE, sim, frame_rate=30.0)
        mags, dirs = [], set()
        for _ in range(runner.frames_total):
            runner.step()
            fx = self.mock_effects.get('turbulence')
            if fx is not None and fx.started:
                mags.append(fx._magnitude)
                dirs.add(fx._direction)
        assert mags and max(mags) > 0.02                         # the gusts come through
        assert max(mags) <= 0.3 + 1e-9                           # capped at the intensity
        assert len(set(round(m, 3) for m in mags)) > 10          # and it varies
        assert len(dirs) > 1                                     # pitch and roll mix
        assert runner.step() is False and not self.mock_effects.dict

    def test_pedals_feel_yaw(self, monkeypatch):
        _advancing_clock(monkeypatch)
        ac = self._aircraft()
        runner = PreviewRunner(ac, TURBULENCE, 'MSFS', device_type='pedals', frame_rate=30.0)
        dirs = set()
        for _ in range(runner.frames_total):
            runner.step()
            fx = self.mock_effects.get('turbulence')
            if fx is not None and fx.started:
                dirs.add(fx._direction)
        assert dirs and dirs <= {90, 270}

    def test_only_msfs_and_xplane(self):
        with pytest.raises(ValueError):
            PreviewRunner(aircrafts_dcs.Aircraft('preview'), TURBULENCE, 'DCS')


class TestWindPreview(BaseTelemetryEffectTestCase):
    @pytest.mark.parametrize("sim", ['DCS', 'BMS'])
    def test_gusts_come_through_the_speed_high_pass(self, sim, monkeypatch):
        import telemffb.utils as utils
        _advancing_clock(monkeypatch)
        ac = aircrafts_dcs.Aircraft('preview')
        ac.wind_effect_enabled = False
        ac.wind_effect_max_intensity = 0.5
        ac.wind_effect_scaling = 1.0
        runner = PreviewRunner(ac, WIND, sim, frame_rate=30.0)
        mags = []
        for _ in range(runner.frames_total):
            runner.step()
            fx = self.mock_effects.get('wnd')
            if fx is not None and fx.started:
                mags.append(fx._magnitude)
                assert fx._direction is utils.RandomDirectionModulator
        assert mags and max(mags) > 0.02
        assert max(mags) <= 0.5 + 1e-9
        assert runner.step() is False and not self.mock_effects.dict

    def test_pedals_are_silent_and_msfs_not_offered(self):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.wind_effect_max_intensity = 0.5
        runner = PreviewRunner(ac, WIND, 'DCS', device_type='pedals', frame_rate=10.0)
        for _ in range(3):
            runner.step()
        assert 'wnd' not in self.mock_effects
        with pytest.raises(ValueError):
            PreviewRunner(aircrafts_msfs_xp.Aircraft('preview'), WIND, 'MSFS')


class TestTouchdownPreview(BaseTelemetryEffectTestCase):
    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'), (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_a_bump_up_to_max_force_and_back(self, cls, sim):
        ac = cls('preview')
        ac.touchdown_effect_enabled = False
        ac.touchdown_effect_max_force = 0.5
        ac.touchdown_effect_max_gs = 3.0
        runner = PreviewRunner(ac, TOUCHDOWN, sim, frame_rate=20.0)   # 8 frames + 6 tail
        mags = []
        for _ in range(runner.frames_total):
            runner.step()
            fx = self.mock_effects.get('touchdown')
            mags.append(fx._magnitude if fx is not None else 0.0)
        assert max(mags) == pytest.approx(0.5)                        # max G -> max force, at the peak hold
        assert mags[0] < max(mags) and mags[-1] < max(mags)          # up and back down
        assert self.mock_effects['touchdown']._direction == 180
        while runner.step():
            pass
        assert not self.mock_effects.dict

    def test_pedals_are_silent(self):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.touchdown_effect_max_force = 0.5
        runner = PreviewRunner(ac, TOUCHDOWN, 'DCS', device_type='pedals', frame_rate=20.0)
        for _ in range(3):
            runner.step()
        assert 'touchdown' not in self.mock_effects


class TestDecelerationPreview(BaseTelemetryEffectTestCase):
    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'), (aircrafts_dcs.Aircraft, 'BMS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
        (aircrafts_il2.Aircraft, 'IL2'),
    ])
    def test_a_braking_run_builds_to_max_force(self, cls, sim):
        ac = cls('preview')
        ac.deceleration_effect_enable = False
        ac.deceleration_max_force = 0.5
        ac.decel_scale_factor = 1
        ac.decel_airborne_disable = True
        runner = PreviewRunner(ac, DECELERATION, sim, frame_rate=10.0)   # 40 frames + 3 tail
        mags = []
        for _ in range(runner.frames_total):
            runner.step()
            fx = self.mock_effects.get('decel')
            mags.append(fx._magnitude if fx is not None and fx.started else 0.0)
        # end of the hold (frame 24, t = 2.4 s): the 8-frame average has caught
        # up (the stimulus wobbles 2% so the effect keeps processing frames)
        assert mags[24] == pytest.approx(0.5, abs=0.03)
        assert mags[5] < mags[24]                                     # building
        assert mags[-1] < mags[24]                                    # releasing
        assert self.mock_effects['decel']._direction == 180
        while runner.step():
            pass
        assert not self.mock_effects.dict


class TestRunwayRumblePreview(BaseTelemetryEffectTestCase):
    @pytest.mark.parametrize("cls, sim", [
        (aircrafts_dcs.Aircraft, 'DCS'),
        (aircrafts_msfs_xp.Aircraft, 'MSFS'), (aircrafts_msfs_xp.Aircraft, 'XPLANE'),
    ])
    def test_jittering_wheels_come_through_the_high_pass(self, cls, sim):
        import telemffb.utils as utils
        ac = cls('preview')
        ac.runway_rumble_enabled = False
        ac.runway_rumble_intensity = 1.0
        runner = PreviewRunner(ac, RUNWAY_RUMBLE, sim, frame_rate=10.0)
        peak = 0.0
        for _ in range(runner.frames_total):
            runner.step()
            fx = self.mock_effects.get('runway0')
            if fx is not None and fx.started:
                peak = max(peak, abs(fx._magnitude))
                assert fx._direction is utils.RandomDirectionModulator
        assert 0.0 < peak <= 0.5                                       # clamped by the effect
        while runner.step():
            pass
        assert not self.mock_effects.dict

    def test_bms_takes_bump_telemetry(self):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.runway_rumble_intensity = 1.0
        runner = PreviewRunner(ac, RUNWAY_RUMBLE, 'BMS', frame_rate=10.0)
        for _ in range(3):
            runner.step()
        bump = self.mock_effects['runway_bump1']
        assert bump.started and bump._periodic[0] == 15
        assert 'runway0' not in self.mock_effects

    def test_il2_is_not_offered(self):
        with pytest.raises(ValueError):
            PreviewRunner(aircrafts_il2.Aircraft('preview'), RUNWAY_RUMBLE, 'IL2')


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
