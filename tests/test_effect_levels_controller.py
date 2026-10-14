"""Effect levels per role and the mute: the controller (G.effect_levels),
its IPC to child instances, and the telemetry loop's replay request.

    master -> children   LEVELS:<role>:<json name -> percent>
    master -> children   MUTE:<json role -> mode>
    master -> children   Keepalive:{"mute": {...}, "levels": {role: {...}}}
"""
import json
import threading
import types

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
import telemffb.hw.effect_levels as effect_levels
import telemffb.telem.TelemManager as tm_module
from telemffb.hw.effect_levels import LEVEL_NAMES, MUTE_ALL, MUTE_KEEP_SPRING, MUTE_OFF
from telemffb.IPCNetworkThread import IPCNetworkThread
from telemffb.state.effect_levels_controller import (CONTROLS_SHOWN_KEY, LEVEL_KEYS,
                                                      MUTE_MODE_KEY, MUTE_SCOPE_KEY,
                                                      PINNED_KEY, SCOPE_ALL, SCOPE_DEVICE,
                                                      EffectLevelsController)
from telemffb.state.mute_button_binding import (BEHAVIOR_MOMENTARY, BEHAVIOR_TOGGLE,
                                                BUTTON_BEHAVIOR_KEY, BUTTON_INVERTED_KEY,
                                                BUTTON_NUMBER_KEY, BUTTON_ROLE_KEY)
from telemffb.telem.TelemManager import TelemManager

# IPCNetworkThread opens a UDP socket in its constructor; these tests never
# start the thread and close it themselves (see test_preview_ipc.py).
pytestmark = [pytest.mark.unit,
              pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")]

ALL_100 = dict.fromkeys(LEVEL_KEYS, 100)


class FakeSettings:
    """G.system_settings stand-in: values keyed (role, key), global ones
    (None, key); a read resolves the instance first, then the global."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []

    def get(self, name, default=None, instance=None):
        scoped = (instance or G.device_type, name)
        if scoped in self.values:
            return self.values[scoped]
        return self.values.get((None, name), default)

    def setValue(self, key, value, instance=None):
        self.values[(instance, key)] = value
        self.writes.append((instance, key, value))


class FakeIpc:
    def __init__(self):
        self.levels = []
        self.mute = []

    def publish_effect_levels(self, role, values):
        self.levels.append((role, dict(values)))

    def publish_effect_mute(self, state):
        self.mute.append(dict(state))


class FakeManager:
    def __init__(self):
        self.requests = 0

    def request_effect_levels_reapply(self):
        self.requests += 1


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data.decode(), addr))

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    """A master driving the joystick, with pedals and collective children."""
    ns = types.SimpleNamespace(settings=FakeSettings(), ipc=FakeIpc(), manager=FakeManager())
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    monkeypatch.setattr(G, 'master_instance', True, raising=False)
    monkeypatch.setattr(G, 'system_settings', ns.settings, raising=False)
    monkeypatch.setattr(G, 'ipc_instance', ns.ipc, raising=False)
    monkeypatch.setattr(G, 'telem_manager', ns.manager, raising=False)
    monkeypatch.setattr(G, 'launched_instances', {'pedals': object(), 'collective': object()},
                        raising=False)
    return ns


def _child(monkeypatch, env, role='pedals'):
    monkeypatch.setattr(G, 'device_type', role, raising=False)
    monkeypatch.setattr(G, 'master_instance', False, raising=False)
    monkeypatch.setattr(G, 'launched_instances', {}, raising=False)
    return EffectLevelsController()


def _ipc(monkeypatch, **kwargs):
    ipc = IPCNetworkThread(**kwargs)
    ipc._socket.close()
    fake = FakeSocket()
    monkeypatch.setattr(ipc, '_socket', fake)
    return ipc, fake


class TestLevels:
    def test_own_levels_load_from_settings_at_startup(self, env):
        env.settings.values[('joystick', 'effectLevelMaster')] = 50
        env.settings.values[('joystick', 'effectLevelDamper')] = '30'
        env.settings.values[('pedals', 'effectLevelMaster')] = 10    # not this process's
        ctl = EffectLevelsController()
        assert ctl.levels('joystick')['master'] == 50
        assert effect_levels.levels.level('master') == 0.5
        assert effect_levels.levels.level('damper') == 0.3
        assert effect_levels.levels.level('spring') == 1.0
        assert env.manager.requests == 0

    def test_own_role_updates_the_table_and_requests_a_replay(self, env):
        ctl = EffectLevelsController()
        ctl.set_levels('joystick', {'master': 80, 'periodic': 25})
        assert effect_levels.levels.level('master') == 0.8
        assert effect_levels.levels.level('periodic') == 0.25
        assert env.manager.requests == 1
        assert env.ipc.levels == []

    def test_a_drag_replays_once_per_interval_and_a_persist_at_once(self, env):
        ctl = EffectLevelsController()
        for value in (90, 80, 70):
            ctl.set_levels('joystick', {'master': value}, persist=False)
        assert env.manager.requests == 1              # the rest wait for the interval
        assert effect_levels.levels.level('master') == 0.7
        ctl.set_levels('joystick', {'master': 60})
        assert env.manager.requests == 2

    def test_only_a_persisting_set_writes_settings(self, env):
        ctl = EffectLevelsController()
        ctl.set_levels('joystick', {'spring': 70}, persist=False)
        assert env.settings.writes == []
        assert effect_levels.levels.level('spring') == 0.7
        ctl.set_levels('joystick', {'spring': 70})
        assert env.settings.writes == [('joystick', 'effectLevelSpring', 70)]
        assert env.manager.requests == 1

    def test_values_clamp(self, env):
        ctl = EffectLevelsController()
        ctl.set_levels('joystick', {'master': 150, 'damper': -5, 'friction': 33.6})
        got = ctl.levels('joystick')
        assert (got['master'], got['damper'], got['friction']) == (100, 0, 34)
        assert effect_levels.levels.level('damper') == 0.0

    def test_bad_input_raises_and_applies_nothing(self, env):
        ctl = EffectLevelsController()
        for role, values in (('joystick', {'loudness': 50}),
                             ('joystick', {'master': 50, 'damper': None}),
                             ('rudder', {'master': 50})):
            with pytest.raises(ValueError):
                ctl.set_levels(role, values)
        assert ctl.levels('joystick') == ALL_100
        assert env.settings.writes == [] and env.manager.requests == 0

    def test_a_child_role_goes_over_ipc_with_its_values(self, env):
        ctl = EffectLevelsController()
        ctl.set_levels('pedals', {'master': 40}, persist=False)
        assert env.ipc.levels == [('pedals', {**ALL_100, 'master': 40})]
        assert effect_levels.levels.unity
        assert env.manager.requests == 0
        assert env.settings.writes == []

    def test_signal_fires_only_on_change(self, env):
        ctl = EffectLevelsController()
        got = []
        ctl.levels_changed.connect(got.append)
        ctl.set_levels('pedals', {'master': 40}, persist=False)
        ctl.set_levels('pedals', {'master': 40}, persist=False)
        ctl.set_levels('pedals', {'master': 40})
        ctl.set_levels('joystick', {'master': 100})
        assert got == ['pedals']


class TestMute:
    def test_own_role_applies_the_default_mode_and_requests_a_replay(self, env):
        ctl = EffectLevelsController()
        ctl.set_muted('joystick', True)
        assert ctl.muted('joystick')
        assert effect_levels.levels.mute_mode == MUTE_KEEP_SPRING
        assert env.manager.requests == 1

    def test_changing_the_mode_while_muted_reapplies_it_to_every_muted_role(self, env):
        ctl = EffectLevelsController()
        ctl.set_muted('joystick', True)
        ctl.set_muted('pedals', True)
        ctl.set_mute_mode(MUTE_ALL)
        assert effect_levels.levels.mute_mode == MUTE_ALL
        assert env.ipc.mute[-1] == {'joystick': MUTE_ALL, 'pedals': MUTE_ALL}
        assert not ctl.muted('collective')
        assert env.manager.requests == 2

    def test_a_child_role_goes_over_ipc_and_leaves_the_own_table(self, env):
        ctl = EffectLevelsController()
        ctl.set_muted('pedals', True)
        assert env.ipc.mute == [{'pedals': MUTE_KEEP_SPRING}]
        assert effect_levels.levels.mute_mode == MUTE_OFF
        assert env.manager.requests == 0

    def test_a_child_that_connects_during_a_global_mute_is_muted(self, env, monkeypatch):
        monkeypatch.setattr(G, 'launched_instances', {}, raising=False)
        ctl = EffectLevelsController()
        ctl.set_muted_all(True)
        assert not ctl.muted('pedals')
        ctl.on_device_status('pedals', 'ACTIVE')
        assert ctl.muted('pedals')
        sent = len(env.ipc.mute)
        ctl.on_device_status('pedals', 'ACTIVE')          # repeated status: nothing new
        assert len(env.ipc.mute) == sent
        ctl.set_muted_all(False)
        ctl.on_device_status('collective', 'ACTIVE')
        assert not ctl.muted('collective')

    def test_global_mute_covers_every_role(self, env):
        env.settings.values[(None, MUTE_MODE_KEY)] = MUTE_ALL
        ctl = EffectLevelsController()
        ctl.set_muted_all(True)
        assert ctl.muted_all()
        assert env.ipc.mute[-1] == dict.fromkeys(('joystick', 'pedals', 'collective'), MUTE_ALL)
        assert effect_levels.levels.mute_mode == MUTE_ALL
        ctl.set_muted_all(False)
        assert not any(ctl.muted(r) for r in ('joystick', 'pedals', 'collective'))
        assert env.ipc.mute[-1] == {}
        assert effect_levels.levels.mute_mode == MUTE_OFF

    def test_muted_state_is_never_written_to_settings(self, env):
        ctl = EffectLevelsController()
        ctl.set_muted('joystick', True)
        ctl.set_muted_all(True)
        ctl.toggle_mute('joystick')
        ctl.set_muted_all(False)
        assert env.settings.writes == []

    def test_signal_fires_only_on_change(self, env):
        ctl = EffectLevelsController()
        got = []
        ctl.mute_changed.connect(lambda: got.append(1))
        ctl.set_muted('pedals', True)
        ctl.set_muted('pedals', True)
        ctl.set_mute_mode(MUTE_KEEP_SPRING)    # already selected
        ctl.set_mute_scope(SCOPE_DEVICE)       # already selected
        ctl.set_muted('joystick', False)       # already off
        assert got == [1]
        ctl.set_mute_mode(MUTE_ALL)
        assert got == [1, 1]
        ctl.set_mute_scope(SCOPE_ALL)
        assert got == [1, 1, 1]


class TestMuteSelection:
    def test_both_are_stored_globally_and_read_by_a_new_controller(self, env):
        ctl = EffectLevelsController()
        ctl.set_mute_mode(MUTE_ALL)
        ctl.set_mute_scope(SCOPE_ALL)
        assert env.settings.writes == [(None, MUTE_MODE_KEY, MUTE_ALL),
                                       (None, MUTE_SCOPE_KEY, SCOPE_ALL)]
        again = EffectLevelsController()
        assert (again.mute_mode(), again.mute_scope()) == (MUTE_ALL, SCOPE_ALL)

    def test_a_stored_value_that_is_not_a_choice_reads_as_the_default(self, env):
        env.settings.values[(None, MUTE_MODE_KEY)] = 'bogus'
        env.settings.values[(None, MUTE_SCOPE_KEY)] = 'bogus'
        ctl = EffectLevelsController()
        assert (ctl.mute_mode(), ctl.mute_scope()) == (MUTE_KEEP_SPRING, SCOPE_DEVICE)

    def test_selecting_leaves_the_mute_state_alone(self, env):
        ctl = EffectLevelsController()
        ctl.set_muted('pedals', True)
        ctl.set_mute_mode(MUTE_ALL)
        ctl.set_mute_scope(SCOPE_ALL)
        assert ctl.muted('pedals')
        assert not ctl.muted('joystick') and not ctl.muted('collective')


class TestPinnedLevels:
    def test_only_master_by_default(self, env):
        assert EffectLevelsController().pinned_levels() == ['master']

    def test_stored_globally_and_read_by_a_new_controller(self, env):
        ctl = EffectLevelsController()
        ctl.set_level_pinned('spring', True)
        ctl.set_level_pinned('periodic', True)
        assert ctl.pinned_levels() == ['master', 'periodic', 'spring']
        assert all(instance is None and key == PINNED_KEY
                   for instance, key, _value in env.settings.writes)
        assert EffectLevelsController().pinned_levels() == ['master', 'periodic', 'spring']

    def test_always_in_level_order(self, env):
        ctl = EffectLevelsController()
        for name in reversed(LEVEL_NAMES):
            ctl.set_level_pinned(name, True)
        assert ctl.pinned_levels() == list(LEVEL_NAMES)
        env.settings.values[(None, PINNED_KEY)] = 'friction, Spring ,master'
        assert EffectLevelsController().pinned_levels() == ['master', 'spring', 'friction']

    def test_nothing_pinned_is_kept(self, env):
        ctl = EffectLevelsController()
        ctl.set_level_pinned('master', False)
        assert ctl.pinned_levels() == []
        assert EffectLevelsController().pinned_levels() == []

    @pytest.mark.parametrize('stored, expected', [
        (12, ['master']),
        (None, ['master']),
        ({'spring': True}, ['master']),
        ('bogus,,spring', ['spring']),
        (['inertia', 'bogus', 3], ['inertia']),
    ])
    def test_a_malformed_stored_value_reads_safely(self, env, stored, expected):
        env.settings.values[(None, PINNED_KEY)] = stored
        assert EffectLevelsController().pinned_levels() == expected


class TestControlsShown:
    def test_stored_globally_and_read_by_a_new_controller(self, env, monkeypatch):
        EffectLevelsController().set_controls_shown(False)
        assert env.settings.writes == [(None, CONTROLS_SHOWN_KEY, False)]
        assert EffectLevelsController().controls_shown() is False
        monkeypatch.setattr(G, 'device_type', 'pedals', raising=False)
        assert EffectLevelsController().controls_shown() is False

    @pytest.mark.parametrize('stored, expected', [
        (False, False), ('false', False), ('0', False), ('true', True), (None, True), ('bogus', True),
    ])
    def test_a_stored_value_reads_safely(self, env, stored, expected):
        env.settings.values[(None, CONTROLS_SHOWN_KEY)] = stored
        assert EffectLevelsController().controls_shown() is expected

    def test_hiding_leaves_levels_mute_and_pins_alone(self, env):
        env.settings.values[('joystick', 'effectLevelMaster')] = 60
        ctl = EffectLevelsController()
        ctl.set_muted('joystick', True)
        env.ipc.mute.clear()
        changed = []
        ctl.levels_changed.connect(changed.append)
        ctl.mute_changed.connect(lambda: changed.append('mute'))
        ctl.pins_changed.connect(lambda: changed.append('pins'))
        ctl.set_controls_shown(False)
        assert changed == [] and env.ipc.levels == [] and env.ipc.mute == []
        assert ctl.levels('joystick')['master'] == 60
        assert ctl.muted('joystick')
        assert ctl.pinned_levels() == ['master']


class TestToggleMute:
    @pytest.mark.parametrize('mode', [MUTE_KEEP_SPRING, MUTE_ALL])
    def test_scope_device_flips_the_role(self, env, mode):
        ctl = EffectLevelsController()
        ctl.set_mute_mode(mode)
        ctl.toggle_mute('pedals')
        assert env.ipc.mute[-1] == {'pedals': mode}
        assert not ctl.muted('joystick')
        ctl.toggle_mute('pedals')
        assert env.ipc.mute[-1] == {}

    @pytest.mark.parametrize('mode', [MUTE_KEEP_SPRING, MUTE_ALL])
    def test_scope_all_mutes_every_role_then_releases(self, env, mode):
        ctl = EffectLevelsController()
        ctl.set_mute_mode(mode)
        ctl.set_mute_scope(SCOPE_ALL)
        ctl.toggle_mute('pedals')
        assert env.ipc.mute[-1] == dict.fromkeys(('joystick', 'pedals', 'collective'), mode)
        assert effect_levels.levels.mute_mode == mode
        ctl.toggle_mute('pedals')
        assert env.ipc.mute[-1] == {}
        assert effect_levels.levels.mute_mode == MUTE_OFF

    def test_scope_all_with_some_muted_mutes_the_rest(self, env):
        ctl = EffectLevelsController()
        ctl.set_muted('pedals', True)
        ctl.set_mute_scope(SCOPE_ALL)
        ctl.toggle_mute('joystick')
        assert ctl.muted_all()


def _store_binding(env, role='joystick', button=5, behavior=BEHAVIOR_TOGGLE, inverted=False):
    env.settings.values[(None, BUTTON_ROLE_KEY)] = role
    env.settings.values[(None, BUTTON_NUMBER_KEY)] = button
    env.settings.values[(None, BUTTON_BEHAVIOR_KEY)] = behavior
    env.settings.values[(None, BUTTON_INVERTED_KEY)] = inverted


def _bind(env, **binding):
    _store_binding(env, **binding)
    return EffectLevelsController()


class TestMuteButtonBinding:
    def test_unbound_by_default_and_does_nothing(self, env):
        ctl = EffectLevelsController()
        assert ctl.mute_button_binding() == ('joystick', 0, BEHAVIOR_TOGGLE, False)
        for buttons in ([1], [1, 5], [], [5]):
            ctl.on_device_buttons('joystick', buttons)
        assert env.ipc.mute == [] and not ctl.muted('joystick')

    def test_the_binding_is_stored_globally_and_read_back(self, env):
        ctl = EffectLevelsController()
        ctl.set_mute_button_binding('pedals', 7, BEHAVIOR_MOMENTARY, True)
        assert {(k, v) for _, k, v in env.settings.writes} == {
            (BUTTON_ROLE_KEY, 'pedals'), (BUTTON_NUMBER_KEY, 7),
            (BUTTON_BEHAVIOR_KEY, BEHAVIOR_MOMENTARY), (BUTTON_INVERTED_KEY, True)}
        assert all(scope is None for scope, _, _ in env.settings.writes)
        assert EffectLevelsController().mute_button_binding() == (
            'pedals', 7, BEHAVIOR_MOMENTARY, True)

    def test_toggle_acts_on_press_edges_only(self, env):
        ctl = _bind(env)
        ctl.on_device_buttons('joystick', [5])
        assert ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [5, 2])     # still held
        ctl.on_device_buttons('joystick', [2])        # released
        assert ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [2, 5])
        assert not ctl.muted('joystick')
        assert len(env.ipc.mute) == 2

    def test_momentary_mutes_while_held(self, env):
        ctl = _bind(env, behavior=BEHAVIOR_MOMENTARY)
        ctl.on_device_buttons('joystick', [5])
        assert ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [5, 1])
        assert ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [1])
        assert not ctl.muted('joystick')

    def test_momentary_releases_with_the_scope_it_muted_with(self, env):
        ctl = _bind(env, behavior=BEHAVIOR_MOMENTARY)
        ctl.set_mute_scope(SCOPE_ALL)
        ctl.on_device_buttons('joystick', [5])
        assert ctl.muted_all()
        ctl.set_mute_scope(SCOPE_DEVICE)
        ctl.on_device_buttons('joystick', [])
        assert env.ipc.mute[-1] == {}

    def test_a_held_momentary_mute_is_released_when_the_binding_changes(self, env):
        ctl = _bind(env, behavior=BEHAVIOR_MOMENTARY)
        ctl.on_device_buttons('joystick', [5])
        ctl.set_mute_button_binding('joystick', 6, BEHAVIOR_MOMENTARY)
        assert not ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [])          # the old button let go
        ctl.on_device_buttons('joystick', [5])         # no longer bound
        assert not ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [6])
        assert ctl.muted('joystick')

    def test_a_held_momentary_mute_is_released_when_the_device_is_lost(self, env):
        ctl = _bind(env, role='pedals', behavior=BEHAVIOR_MOMENTARY)
        ctl.on_device_buttons('pedals', [5])
        ctl.on_device_status('pedals', 'ACTIVE')
        ctl.on_device_status('collective', 'TIMEOUT')     # another device
        assert ctl.muted('pedals')
        ctl.on_device_status('pedals', 'TIMEOUT')
        assert not ctl.muted('pedals')

    def test_inverted_momentary_mutes_while_released(self, env):
        ctl = _bind(env, behavior=BEHAVIOR_MOMENTARY, inverted=True)
        ctl.on_device_buttons('joystick', [5])
        assert not ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [5, 1])
        assert not ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [1])
        assert ctl.muted('joystick')

    def test_momentary_calls_the_controller_only_on_a_change(self, env, monkeypatch):
        ctl = _bind(env, behavior=BEHAVIOR_MOMENTARY, inverted=True)
        calls = []
        monkeypatch.setattr(ctl, 'set_mute_active',
                            lambda role, on, scope=None: calls.append(on))
        for buttons in ([1], [], [5], [5], [5, 1], [1], [1], []):
            ctl.on_device_buttons('joystick', buttons)
        assert calls == [False, True]

    def test_an_inverted_binding_mutes_from_startup(self, env):
        ctl = _bind(env, role='pedals', behavior=BEHAVIOR_MOMENTARY, inverted=True)
        assert ctl.muted('pedals') and not ctl.muted('joystick')

    def test_setting_an_inverted_binding_mutes_at_once_and_leaving_it_releases(self, env):
        ctl = EffectLevelsController()
        ctl.set_mute_button_binding('joystick', 5, BEHAVIOR_MOMENTARY, True)
        assert ctl.muted('joystick')
        ctl.set_mute_button_binding('joystick', 5, BEHAVIOR_MOMENTARY, False)
        assert not ctl.muted('joystick')
        ctl.set_mute_button_binding('joystick', 5, BEHAVIOR_MOMENTARY, True)
        ctl.set_mute_button_binding('joystick', 5, BEHAVIOR_TOGGLE, True)
        assert not ctl.muted('joystick')

    def test_toggle_ignores_inversion(self, env):
        ctl = _bind(env, inverted=True)
        assert not ctl.muted('joystick')
        ctl.on_device_buttons('joystick', [5])
        ctl.on_device_buttons('joystick', [])
        assert ctl.muted('joystick')

    def test_a_lost_device_engages_an_inverted_mute(self, env):
        ctl = _bind(env, role='pedals', behavior=BEHAVIOR_MOMENTARY, inverted=True)
        ctl.on_device_buttons('pedals', [5])
        assert not ctl.muted('pedals')
        ctl.on_device_status('pedals', 'TIMEOUT')
        assert ctl.muted('pedals')

    def test_a_child_applies_no_initial_mute(self, env, monkeypatch):
        _store_binding(env, behavior=BEHAVIOR_MOMENTARY, inverted=True)
        child = _child(monkeypatch, env, role='joystick')
        assert not child.muted('joystick')
        assert effect_levels.levels.mute_mode == MUTE_OFF

    def test_a_toggled_mute_survives_a_lost_device(self, env):
        ctl = _bind(env)
        ctl.on_device_buttons('joystick', [5])
        ctl.on_device_status('joystick', 'DISCONNECTED')
        assert ctl.muted('joystick')

    def test_only_the_bound_devices_button_acts(self, env):
        ctl = _bind(env, role='pedals')
        ctl.on_device_buttons('joystick', [5])
        assert env.ipc.mute == []
        ctl.on_device_buttons('pedals', [5])
        assert env.ipc.mute[-1] == {'pedals': MUTE_KEEP_SPRING}

    def test_a_child_does_not_act_on_buttons(self, env, monkeypatch):
        _bind(env)
        child = _child(monkeypatch, env, role='joystick')
        child.on_device_buttons('joystick', [5])
        assert not child.muted('joystick')

    def test_a_failing_mute_does_not_raise(self, env, monkeypatch):
        ctl = _bind(env)

        def fail(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(ctl, 'toggle_mute', fail)
        ctl.on_device_buttons('joystick', [5])   # would abort the app if it raised

    def test_a_childs_button_arrives_over_ipc(self, env, monkeypatch):
        """The child's BUTTONS: message reaches the same handler as the
        master's own device."""
        monkeypatch.setattr(G, 'child_buttons', {}, raising=False)
        ctl = _bind(env, role='pedals', behavior=BEHAVIOR_MOMENTARY)
        master_ipc, _ = _ipc(monkeypatch)
        master_ipc.child_buttons_signal.connect(ctl.on_device_buttons)
        master_ipc.child_keepalive_signal.connect(ctl.on_device_status)
        master_ipc._handle_message('BUTTONS:pedals_[5, 9]', ('127.0.0.1', 1))
        assert ctl.muted('pedals')
        assert G.child_buttons['pedals'] == [5, 9]
        master_ipc._handle_message('BUTTONS:pedals_[9]', ('127.0.0.1', 1))
        assert not ctl.muted('pedals')
        master_ipc._handle_message('BUTTONS:pedals_[5]', ('127.0.0.1', 1))
        master_ipc.child_keepalive_signal.emit('pedals', 'TIMEOUT')
        assert not ctl.muted('pedals')


class TestChildInstance:
    def test_keepalive_carries_the_state_and_the_child_applies_it_once(self, env, monkeypatch):
        master_ipc, master_wire = _ipc(monkeypatch)
        master_ipc._child_addrs = {'pedals': ('127.0.0.1', 41001)}
        monkeypatch.setattr(G, 'ipc_instance', master_ipc, raising=False)
        master = EffectLevelsController()
        master.set_muted('pedals', True)
        master.set_levels('pedals', {'damper': 20}, persist=False)
        master_wire.sent.clear()
        master_ipc._send_keepalive()
        keepalive = next(m for m, _ in master_wire.sent if m.startswith('Keepalive:'))
        state = json.loads(keepalive.removeprefix('Keepalive:'))
        assert state['mute'] == {'pedals': MUTE_KEEP_SPRING}
        assert state['levels'] == {'pedals': {**ALL_100, 'damper': 20}}

        child = _child(monkeypatch, env)
        child_ipc, _ = _ipc(monkeypatch, dstport=40000)
        child_ipc.effect_mute_signal.connect(child.apply_master_mute)
        child_ipc.effect_levels_signal.connect(child.apply_master_levels)
        changed = []
        child.mute_changed.connect(lambda: changed.append(1))

        child_ipc._handle_message(keepalive, ('127.0.0.1', 1))
        assert child.muted('pedals')
        assert effect_levels.levels.mute_mode == MUTE_KEEP_SPRING
        assert effect_levels.levels.level('damper') == 0.2
        requests = env.manager.requests
        assert requests >= 1 and changed == [1]

        child_ipc._handle_message(keepalive, ('127.0.0.1', 1))
        assert env.manager.requests == requests and changed == [1]
        assert env.settings.writes == []

    def test_a_keepalive_saying_off_unmutes(self, env, monkeypatch):
        child = _child(monkeypatch, env)
        child_ipc, _ = _ipc(monkeypatch, dstport=40000)
        child_ipc.effect_mute_signal.connect(child.apply_master_mute)
        child_ipc._handle_message('MUTE:{"pedals": "all"}', ('127.0.0.1', 1))
        assert effect_levels.levels.mute_mode == MUTE_ALL
        child_ipc._handle_message('Keepalive:{"mute": {}, "levels": {}}', ('127.0.0.1', 1))
        assert not child.muted('pedals')
        assert effect_levels.levels.mute_mode == MUTE_OFF

    def test_levels_addressed_to_another_role_are_ignored(self, env, monkeypatch):
        child = _child(monkeypatch, env)
        child_ipc, _ = _ipc(monkeypatch, dstport=40000)
        child_ipc.effect_levels_signal.connect(child.apply_master_levels)
        child_ipc._handle_message('LEVELS:collective:{"master": 10}', ('127.0.0.1', 1))
        assert effect_levels.levels.unity
        child_ipc._handle_message('LEVELS:pedals:{"master": 10, "bogus": 5}', ('127.0.0.1', 1))
        assert effect_levels.levels.level('master') == 0.1
        assert env.settings.writes == []

    def test_malformed_messages_change_nothing(self, env, monkeypatch):
        child = _child(monkeypatch, env)
        child_ipc, _ = _ipc(monkeypatch, dstport=40000)
        child_ipc.effect_mute_signal.connect(child.apply_master_mute)
        child_ipc.effect_levels_signal.connect(child.apply_master_levels)
        for msg in ('Keepalive', 'Keepalive:not json', 'Keepalive:[]', 'MUTE:{"pedals": "loud"}',
                    'LEVELS:pedals:{"master": "x"}', 'LEVELS:pedals'):
            child_ipc._handle_message(msg, ('127.0.0.1', 1))
        assert effect_levels.levels.unity and not child.muted('pedals')
        assert child_ipc._last_keepalive_timestamp > 0


class _LoopSettings:
    def get(self, key, default=None, instance=None):
        # a long timeout: a replay seen promptly was woken, not timed out
        return 5000 if key == 'telemTimeout' else default


class TestTelemetryLoopReplay:
    @pytest.fixture
    def loop(self, monkeypatch):
        monkeypatch.setattr(G, 'system_settings', _LoopSettings(), raising=False)
        monkeypatch.setattr(G, 'settings_mgr', types.SimpleNamespace(timed_out=False), raising=False)
        monkeypatch.setattr(G, 'ipc_instance', None, raising=False)
        monkeypatch.setattr(G, 'vpconf_init_pending', False, raising=False)
        calls = []
        replayed = threading.Event()

        def reapply():
            calls.append(1)
            replayed.set()
            if len(calls) == 1:
                raise RuntimeError("device went away mid-replay")
            return 0

        monkeypatch.setattr(tm_module.HapticEffect, 'reapply_levels', reapply)
        manager = TelemManager()
        manager.start()
        yield manager, calls, replayed
        with manager._cond:
            manager._run = False
            manager._cond.notify_all()
        manager.join(2)

    def test_a_request_wakes_the_idle_loop_and_runs_once(self, loop):
        manager, calls, replayed = loop
        manager.request_effect_levels_reapply()
        assert replayed.wait(1.0)
        replayed.clear()
        # the first replay raised; the loop survives and serves the next one
        manager.request_effect_levels_reapply()
        assert replayed.wait(1.0)
        assert manager.is_alive()
        assert len(calls) == 2
