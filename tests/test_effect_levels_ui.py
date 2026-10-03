"""The effect levels UI: the Settings tab header controls
(``EffectLevelsHeader``), the mute split button (``EffectMuteButton``),
the Effect Levels dialog, the device strip's muted mark, the live readout
tooltips, the preview's muted notice and the menu entries.

A fake controller with ``G.effect_levels``'s API records every call, so
the widgets are tested against what they ask of it.
"""
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractSlider, QApplication

import telemffb.globals as G
import telemffb.ui.dialogs.EffectLevelsDialog as dialog_module
from telemffb.hw.effect_levels import LEVEL_NAMES, MUTE_ALL, MUTE_KEEP_SPRING
from telemffb.state.app_state import AppState
from telemffb.state.effect_levels_controller import SCOPE_ALL, SCOPE_DEVICE
from telemffb.state.mute_button_binding import BEHAVIOR_MOMENTARY, BEHAVIOR_TOGGLE
from telemffb.ui.dialogs.EffectLevelsDialog import EffectLevelsDialog
from telemffb.ui.panels.DevicePanel import DeviceIconPanel, MiniDevicePanel
from telemffb.ui.widgets.EffectLevelsHeader import EffectLevelsHeader
from telemffb.ui.widgets.EffectMuteButton import EffectMuteButton
from telemffb.ui.widgets.effect_levels_ui import readout_note

pytestmark = pytest.mark.unit

ALL_100 = dict.fromkeys(LEVEL_NAMES, 100)


class FakeController(QObject):
    """``EffectLevelsController``'s API over plain dicts, recording calls."""

    levels_changed = pyqtSignal(str)
    mute_changed = pyqtSignal()
    pins_changed = pyqtSignal()
    controls_shown_changed = pyqtSignal(bool)

    def __init__(self, own='joystick', roles=('joystick', 'pedals')):
        super().__init__()
        self.own_role = own
        self._roles = list(roles)
        self._levels = {}
        self._mode = MUTE_KEEP_SPRING
        self._scope = SCOPE_DEVICE
        self._muted = {}
        self._binding = (own, 0, BEHAVIOR_TOGGLE, False)
        self._pinned = ['master']
        self._shown = True
        self.calls = []

    def roles(self):
        return list(self._roles)

    def levels(self, role):
        return dict(self._levels.setdefault(role, dict(ALL_100)))

    def set_levels(self, role, values, persist=True):
        self.calls.append(('set_levels', role, dict(values), persist))
        current = self.levels(role)
        new = {**current, **values}
        self._levels[role] = new
        if new != current:
            self.levels_changed.emit(role)

    def pinned_levels(self):
        return [n for n in LEVEL_NAMES if n in self._pinned]

    def set_level_pinned(self, name, on):
        self.calls.append(('set_level_pinned', name, on))
        if name not in LEVEL_NAMES:
            raise ValueError(name)
        current = self.pinned_levels()
        wanted = set(current) | {name} if on else set(current) - {name}
        self._pinned = [n for n in LEVEL_NAMES if n in wanted]
        if self._pinned != current:
            self.pins_changed.emit()

    def controls_shown(self):
        return self._shown

    def set_controls_shown(self, on):
        self.calls.append(('set_controls_shown', on))
        on = bool(on)
        if on != self._shown:
            self._shown = on
            self.controls_shown_changed.emit(on)

    def mute_mode(self):
        return self._mode

    def set_mute_mode(self, mode):
        self.calls.append(('set_mute_mode', mode))
        self._mode = mode
        for role in self._muted:
            self._muted[role] = mode
        self.mute_changed.emit()

    def mute_scope(self):
        return self._scope

    def set_mute_scope(self, scope):
        self.calls.append(('set_mute_scope', scope))
        self._scope = scope
        self.mute_changed.emit()

    def toggle_mute(self, role):
        self.calls.append(('toggle_mute', role))
        if self._scope == SCOPE_ALL:
            self._apply_all(not self.muted_all())
        else:
            self._apply(role, not self.muted(role))
        self.mute_changed.emit()

    def _apply(self, role, on):
        if on:
            self._muted[role] = self._mode
        else:
            self._muted.pop(role, None)

    def _apply_all(self, on):
        for role in self._roles:
            self._apply(role, on)

    def mute_button_binding(self):
        return self._binding

    def set_mute_button_binding(self, role, button, behavior, inverted=False):
        self.calls.append(('set_mute_button_binding', role, button, behavior, inverted))
        self._binding = (role, button, behavior, inverted)

    def on_device_buttons(self, role, buttons):
        self.calls.append(('on_device_buttons', role, list(buttons)))

    def on_device_status(self, role, status):
        self.calls.append(('on_device_status', role, status))

    def muted(self, role):
        return role in self._muted

    def muted_all(self):
        return all(self.muted(r) for r in self._roles)

    def set_muted(self, role, on):
        self.calls.append(('set_muted', role, on))
        self._apply(role, on)
        self.mute_changed.emit()

    def set_muted_all(self, on):
        self.calls.append(('set_muted_all', on))
        self._apply_all(on)
        self.mute_changed.emit()


class RefusingController(FakeController):
    """Every change raises, as a controller refusing bad input does."""

    def set_muted(self, role, on):
        raise ValueError("refused")

    def toggle_mute(self, role):
        raise ValueError("refused")

    def set_mute_mode(self, mode):
        raise ValueError("refused")

    def set_mute_scope(self, scope):
        raise ValueError("refused")

    def set_mute_button_binding(self, role, button, behavior, inverted=False):
        raise ValueError("refused")

    def set_levels(self, role, values, persist=True):
        raise ValueError("refused")

    def set_level_pinned(self, name, on):
        raise ValueError("refused")

    def set_controls_shown(self, on):
        raise ValueError("refused")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ctl(qapp, monkeypatch):
    controller = FakeController()
    monkeypatch.setattr(G, 'effect_levels', controller, raising=False)
    return controller


def _level_calls(ctl):
    return [c for c in ctl.calls if c[0] == 'set_levels']


def _drag(slider_widget, value):
    """Drag the handle to ``value`` and let go."""
    slider = slider_widget.slider
    slider.setSliderDown(True)
    slider.setValue(value)
    slider.setSliderDown(False)


# --- header -----------------------------------------------------------------

def _bound_header(ctl):
    state = AppState()
    state.set_own_device_type('joystick')
    state.set_master(True)
    header = EffectLevelsHeader(ctl)
    header.bind(state)
    return state, header


class TestHeaderPinnedSliders:
    def test_exactly_the_pinned_sliders_in_level_order(self, ctl):
        ctl._pinned = ['spring', 'friction', 'periodic', 'master']
        header = EffectLevelsHeader(ctl)
        assert header.shown_levels() == ['master', 'periodic', 'spring', 'friction']
        for name in LEVEL_NAMES:
            shown = name in ctl._pinned
            assert header.sliders[name].isVisibleTo(header) == shown
            assert header.labels[name].isVisibleTo(header) == shown

    def test_follows_pins_changed(self, ctl):
        header = EffectLevelsHeader(ctl)
        ctl.set_level_pinned('damper', True)
        assert header.shown_levels() == ['master', 'damper']
        ctl.set_level_pinned('master', False)
        assert header.shown_levels() == ['damper']

    def test_nothing_pinned_keeps_the_mute_and_levels_buttons(self, ctl):
        ctl._pinned = []
        header = EffectLevelsHeader(ctl)
        assert header.shown_levels() == []
        assert header.mute_button.isVisibleTo(header)
        assert header.levels_button.isVisibleTo(header)
        header.mute_button.click()
        assert ctl.calls[-1] == ('toggle_mute', 'joystick')

    def test_a_pinned_slider_edits_its_level_with_the_persist_flag(self, ctl):
        ctl._pinned = ['master', 'damper']
        header = EffectLevelsHeader(ctl)
        slider = header.sliders['damper'].slider
        slider.setSliderDown(True)
        slider.setValue(40)
        assert _level_calls(ctl) == [('set_levels', 'joystick', {'damper': 40}, False)]
        slider.setSliderDown(False)
        assert _level_calls(ctl)[-1] == ('set_levels', 'joystick', {'damper': 40}, True)
        slider.triggerAction(QAbstractSlider.SliderAction.SliderPageStepSub)
        assert _level_calls(ctl)[-1] == ('set_levels', 'joystick', {'damper': 30}, True)

    def test_the_sliders_and_mute_button_follow_the_scope(self, ctl):
        ctl._pinned = ['master', 'periodic']
        ctl.set_levels('pedals', {'periodic': 35})
        state, header = _bound_header(ctl)
        assert header.sliders['periodic'].value() == 100
        state.set_scope('pedals')
        assert header.sliders['periodic'].value() == 35
        assert header.mute_button.role == 'pedals'
        _drag(header.sliders['periodic'], 60)
        assert _level_calls(ctl)[-1] == ('set_levels', 'pedals', {'periodic': 60}, True)
        ctl.set_levels('pedals', {'periodic': 20})
        assert header.sliders['periodic'].value() == 20
        ctl.set_levels('joystick', {'periodic': 70})
        assert header.sliders['periodic'].value() == 20

    def test_no_scope_falls_back_to_the_own_role(self, ctl):
        state, header = _bound_header(ctl)
        state.set_scope('pedals')
        state.set_scope(None)
        assert header.role == 'joystick'

    def test_a_change_elsewhere_does_not_move_a_pinned_handle_mid_drag(self, ctl):
        ctl._pinned = ['inertia']
        header = EffectLevelsHeader(ctl)
        slider = header.sliders['inertia'].slider
        slider.setSliderDown(True)
        slider.setValue(30)
        ctl.set_levels('joystick', {'inertia': 80})
        assert slider.value() == 30
        slider.setSliderDown(False)

    def test_a_refused_change_on_a_pinned_slider_does_not_raise(self, qapp):
        refusing = RefusingController()
        refusing._pinned = ['friction']
        header = EffectLevelsHeader(refusing)
        header.sliders['friction'].slider.setValue(10)   # would abort the app if it raised


class TestHeaderLevelsButton:
    def test_the_levels_button_asks_for_the_dialog(self, ctl):
        header = EffectLevelsHeader(ctl)
        asked = []
        header.open_dialog_requested.connect(lambda: asked.append(True))
        header.levels_button.click()
        assert asked == [True]


def _note_names(header):
    return [name for name, _value in header.note.entries]


class TestHiddenLevelsNote:
    """The note lists the levels below 100% whose sliders cannot be seen."""

    def test_shown_with_an_unpinned_level_below_100(self, ctl):
        ctl._pinned = ['master', 'spring']
        ctl.set_levels('joystick', {'master': 80, 'damper': 60, 'friction': 30})
        header = EffectLevelsHeader(ctl)
        assert header.note.entries == [('damper', 60), ('friction', 30)]
        assert not header.note.isHidden()
        assert header.box.isAncestorOf(header.note)

    def test_shown_with_only_pinned_levels_below_100(self, ctl):
        ctl._pinned = ['master', 'spring']
        ctl.set_levels('joystick', {'master': 80, 'spring': 50})
        header = EffectLevelsHeader(ctl)
        assert header.note.entries == []
        assert header.note.isHidden()

    def test_hidden_with_levels_below_100(self, ctl):
        ctl._shown = False
        ctl.set_levels('joystick', {'master': 80, 'periodic': 40})
        header = EffectLevelsHeader(ctl)
        assert header.note.entries == [('master', 80), ('periodic', 40)]
        assert not header.note.isHidden()
        assert not header.box.isAncestorOf(header.note)

    @pytest.mark.parametrize("shown", [True, False])
    def test_everything_at_100(self, ctl, shown):
        ctl._shown = shown
        header = EffectLevelsHeader(ctl)
        assert header.note.entries == []
        assert header.note.isHidden()

    def test_follows_level_changes(self, ctl):
        header = EffectLevelsHeader(ctl)
        ctl.set_levels('joystick', {'inertia': 45})
        assert header.note.entries == [('inertia', 45)]
        ctl.set_levels('joystick', {'inertia': 100})
        assert header.note.isHidden()
        ctl.set_levels('pedals', {'damper': 20})     # not the role in scope
        assert header.note.isHidden()

    def test_follows_pin_changes(self, ctl):
        ctl.set_levels('joystick', {'damper': 60})
        header = EffectLevelsHeader(ctl)
        assert _note_names(header) == ['damper']
        ctl.set_level_pinned('damper', True)
        assert header.note.isHidden()
        ctl.set_level_pinned('master', False)
        ctl.set_levels('joystick', {'master': 90})
        assert _note_names(header) == ['master']

    def test_follows_the_scope(self, ctl):
        ctl.set_levels('pedals', {'spring': 25})
        state, header = _bound_header(ctl)
        assert header.note.isHidden()
        state.set_scope('pedals')
        assert header.note.entries == [('spring', 25)]
        state.set_scope('joystick')
        assert header.note.isHidden()

    def test_follows_showing_and_hiding(self, ctl):
        ctl.set_levels('joystick', {'master': 50, 'friction': 70})
        header = EffectLevelsHeader(ctl)
        assert _note_names(header) == ['friction']
        ctl.set_controls_shown(False)
        assert _note_names(header) == ['master', 'friction']
        assert not header.box.isAncestorOf(header.note) and not header.note.isHidden()
        ctl.set_controls_shown(True)
        assert _note_names(header) == ['friction']
        assert header.box.isAncestorOf(header.note) and not header.note.isHidden()


# --- mute button --------------------------------------------------------------

class TestMuteButton:
    def test_the_menu_shows_the_selection(self, ctl):
        button = EffectMuteButton(ctl, 'joystick')
        assert button.mode_actions[MUTE_KEEP_SPRING].isChecked()
        assert button.scope_actions[SCOPE_DEVICE].isChecked()
        ctl.set_mute_mode(MUTE_ALL)
        ctl.set_mute_scope(SCOPE_ALL)
        assert button.mode_actions[MUTE_ALL].isChecked()
        assert not button.mode_actions[MUTE_KEEP_SPRING].isChecked()
        assert button.scope_actions[SCOPE_ALL].isChecked()
        assert not button.scope_actions[SCOPE_DEVICE].isChecked()

    def test_a_menu_entry_changes_the_selection_without_muting(self, ctl):
        button = EffectMuteButton(ctl, 'joystick')
        button.mode_actions[MUTE_ALL].trigger()
        button.scope_actions[SCOPE_ALL].trigger()
        assert ctl.calls == [('set_mute_mode', MUTE_ALL), ('set_mute_scope', SCOPE_ALL)]
        assert not any(ctl.muted(r) for r in ctl.roles())
        assert not button.isChecked()
        button.scope_actions[SCOPE_DEVICE].trigger()
        assert ctl.calls[-1] == ('set_mute_scope', SCOPE_DEVICE)

    @pytest.mark.parametrize('mode', [MUTE_KEEP_SPRING, MUTE_ALL])
    @pytest.mark.parametrize('scope', [SCOPE_DEVICE, SCOPE_ALL])
    def test_the_click_performs_the_selected_function(self, ctl, mode, scope):
        ctl.set_mute_mode(mode)
        ctl.set_mute_scope(scope)
        button = EffectMuteButton(ctl, 'pedals')
        button.click()
        expected = {'pedals': mode} if scope == SCOPE_DEVICE else dict.fromkeys(ctl.roles(), mode)
        assert ctl._muted == expected
        assert button.isChecked()
        button.click()
        assert ctl._muted == {}
        assert not button.isChecked()

    def test_checked_follows_the_scope(self, ctl):
        button = EffectMuteButton(ctl, 'joystick')
        ctl.set_muted('joystick', True)
        assert button.isChecked()
        ctl.set_mute_scope(SCOPE_ALL)
        assert not button.isChecked()          # pedals is not muted
        ctl.set_muted('pedals', True)
        assert button.isChecked()
        ctl.set_mute_scope(SCOPE_DEVICE)
        ctl.set_muted('joystick', False)
        assert not button.isChecked()

    def test_follows_a_new_role(self, ctl):
        button = EffectMuteButton(ctl, 'joystick')
        ctl.set_muted('pedals', True)
        button.set_role('pedals')
        assert button.isChecked()

    def test_a_refused_change_leaves_it_as_it_was(self, qapp):
        button = EffectMuteButton(RefusingController(), 'joystick')
        button.click()
        button.mode_actions[MUTE_ALL].trigger()
        button.scope_actions[SCOPE_ALL].trigger()
        assert not button.isChecked()
        assert button.mode_actions[MUTE_KEEP_SPRING].isChecked()
        assert button.scope_actions[SCOPE_DEVICE].isChecked()


# --- dialog -------------------------------------------------------------------

class TestDialog:
    def test_each_slider_sets_its_own_level(self, ctl):
        dialog = EffectLevelsDialog(ctl)
        for value, name in enumerate(LEVEL_NAMES, start=10):
            _drag(dialog.sliders[name], value)
            assert _level_calls(ctl)[-1] == ('set_levels', 'joystick', {name: value}, True)

    def test_a_drag_does_not_store_until_released(self, ctl):
        dialog = EffectLevelsDialog(ctl)
        slider = dialog.sliders['damper'].slider
        slider.setSliderDown(True)
        slider.setValue(25)
        assert _level_calls(ctl) == [('set_levels', 'joystick', {'damper': 25}, False)]
        slider.setSliderDown(False)

    def test_shows_current_values(self, ctl):
        ctl.set_levels('joystick', {'periodic': 45, 'master': 70})
        dialog = EffectLevelsDialog(ctl)
        assert dialog.sliders['periodic'].value() == 45
        assert dialog.sliders['master'].value() == 70
        ctl.set_levels('joystick', {'spring': 15})
        assert dialog.sliders['spring'].value() == 15

    def test_reset_sets_every_level_to_100_and_stores(self, ctl):
        ctl.set_levels('joystick', {'periodic': 45})
        dialog = EffectLevelsDialog(ctl)
        dialog.reset_button.click()
        assert _level_calls(ctl)[-1] == ('set_levels', 'joystick', ALL_100, True)
        assert all(s.value() == 100 for s in dialog.sliders.values())

    def test_the_device_selector_edits_the_chosen_role(self, ctl):
        ctl.set_levels('pedals', {'friction': 5})
        dialog = EffectLevelsDialog(ctl)
        assert not dialog.device_row.isHidden()
        dialog.device_combo.setCurrentIndex(dialog.device_combo.findData('pedals'))
        assert dialog.role == 'pedals'
        assert dialog.sliders['friction'].value() == 5
        assert dialog.mute_button.role == 'pedals'
        _drag(dialog.sliders['inertia'], 50)
        assert _level_calls(ctl)[-1] == ('set_levels', 'pedals', {'inertia': 50}, True)

    def test_no_selector_with_one_role(self, qapp):
        dialog = EffectLevelsDialog(FakeController(roles=('joystick',)))
        assert dialog.device_row.isHidden()

    def test_an_unknown_role_opens_on_the_own_role(self, ctl):
        dialog = EffectLevelsDialog(ctl)
        dialog.set_role('collective')
        assert dialog.role == 'joystick'

    def test_the_mute_button_row_shows_the_binding(self, ctl):
        ctl._binding = ('pedals', 7, BEHAVIOR_MOMENTARY, True)
        dialog = EffectLevelsDialog(ctl)
        assert dialog.binding_device_combo.currentData() == 'pedals'
        assert dialog.binding_button_spin.value() == 7
        assert dialog.behavior_buttons[BEHAVIOR_MOMENTARY].isChecked()
        assert not dialog.behavior_buttons[BEHAVIOR_TOGGLE].isChecked()
        assert dialog.binding_inverted_check.isChecked()
        assert ctl.calls == []

    def test_the_clear_button_unbinds(self, ctl):
        ctl._binding = ('pedals', 7, BEHAVIOR_MOMENTARY, False)
        dialog = EffectLevelsDialog(ctl)
        assert dialog.clear_button.isEnabled()
        dialog.clear_button.click()
        assert dialog.binding_button_spin.value() == 0
        assert ctl.calls[-1] == ('set_mute_button_binding', 'pedals', 0, BEHAVIOR_MOMENTARY, False)
        assert not dialog.clear_button.isEnabled()

    def test_each_mute_button_control_stores_the_binding(self, ctl):
        dialog = EffectLevelsDialog(ctl)
        dialog.binding_button_spin.setValue(4)
        assert ctl.calls[-1] == ('set_mute_button_binding', 'joystick', 4, BEHAVIOR_TOGGLE, False)
        dialog.binding_device_combo.setCurrentIndex(dialog.binding_device_combo.findData('pedals'))
        assert ctl.calls[-1] == ('set_mute_button_binding', 'pedals', 4, BEHAVIOR_TOGGLE, False)
        dialog.behavior_buttons[BEHAVIOR_MOMENTARY].click()
        assert ctl.calls[-1] == ('set_mute_button_binding', 'pedals', 4, BEHAVIOR_MOMENTARY, False)
        dialog.binding_inverted_check.click()
        assert ctl.calls[-1] == ('set_mute_button_binding', 'pedals', 4, BEHAVIOR_MOMENTARY, True)
        dialog.binding_button_spin.setValue(0)
        assert ctl.calls[-1] == ('set_mute_button_binding', 'pedals', 0, BEHAVIOR_MOMENTARY, True)

    def test_inverted_is_offered_only_with_momentary(self, ctl):
        ctl._binding = ('joystick', 4, BEHAVIOR_MOMENTARY, True)
        dialog = EffectLevelsDialog(ctl)
        assert dialog.binding_inverted_check.isEnabled()
        dialog.behavior_buttons[BEHAVIOR_TOGGLE].click()
        assert not dialog.binding_inverted_check.isEnabled()
        assert not dialog.binding_inverted_check.isChecked()
        dialog.behavior_buttons[BEHAVIOR_MOMENTARY].click()
        assert ctl._binding == ('joystick', 4, BEHAVIOR_MOMENTARY, True)
        assert dialog.binding_inverted_check.isEnabled()
        assert dialog.binding_inverted_check.isChecked()

    def test_a_stored_device_not_running_is_still_listed(self, ctl):
        ctl._binding = ('collective', 3, BEHAVIOR_TOGGLE, False)
        dialog = EffectLevelsDialog(ctl)
        assert dialog.binding_device_combo.currentData() == 'collective'

    def test_a_refused_binding_does_not_raise(self, qapp):
        dialog = EffectLevelsDialog(RefusingController())
        dialog.binding_button_spin.setValue(9)   # would abort the app if it raised
        assert dialog.binding_button_spin.value() == 0

    def test_detect_takes_the_pressed_button_of_the_selected_device(self, ctl, monkeypatch):
        asked = []

        def fake_wait(target, timeout):
            asked.append(target)
            return 12

        monkeypatch.setattr(dialog_module, 'wait_for_button_press', fake_wait)
        monkeypatch.setattr(dialog_module, 'schedule_on_main_thread', lambda f: f())
        monkeypatch.setattr(dialog_module, 'threading', SimpleNamespace(
            Thread=lambda target, **kwargs: SimpleNamespace(start=target)))
        dialog = EffectLevelsDialog(ctl)
        dialog.detect_button.click()
        assert asked == [None]                   # the own device
        assert ctl.calls[-1] == ('set_mute_button_binding', 'joystick', 12, BEHAVIOR_TOGGLE, False)
        assert dialog.detect_button.isEnabled()
        dialog.binding_device_combo.setCurrentIndex(dialog.binding_device_combo.findData('pedals'))
        dialog.detect_button.click()
        assert asked[-1] == 'pedals'

    def test_detect_with_no_press_keeps_the_binding(self, ctl, monkeypatch):
        monkeypatch.setattr(dialog_module, 'wait_for_button_press', lambda target, timeout: 0)
        monkeypatch.setattr(dialog_module, 'schedule_on_main_thread', lambda f: f())
        monkeypatch.setattr(dialog_module, 'threading', SimpleNamespace(
            Thread=lambda target, **kwargs: SimpleNamespace(start=target)))
        dialog = EffectLevelsDialog(ctl)
        dialog.detect_button.click()
        assert ctl.calls == []
        assert dialog.detect_button.isEnabled()

    def test_pin_toggles_show_the_pinned_set(self, ctl):
        ctl._pinned = ['master', 'spring']
        dialog = EffectLevelsDialog(ctl)
        assert list(dialog.pin_buttons) == list(LEVEL_NAMES)
        assert [n for n, pin in dialog.pin_buttons.items() if pin.isChecked()] == ['master', 'spring']
        ctl.set_level_pinned('inertia', True)
        assert dialog.pin_buttons['inertia'].isChecked()
        assert ctl.calls == [('set_level_pinned', 'inertia', True)]

    def test_a_pin_toggle_pins_and_unpins(self, ctl):
        dialog = EffectLevelsDialog(ctl)
        dialog.pin_buttons['damper'].click()
        assert ctl.calls[-1] == ('set_level_pinned', 'damper', True)
        assert ctl.pinned_levels() == ['master', 'damper']
        dialog.pin_buttons['master'].click()
        assert ctl.calls[-1] == ('set_level_pinned', 'master', False)
        assert ctl.pinned_levels() == ['damper']
        assert not dialog.pin_buttons['master'].isChecked()

    def test_a_refused_pin_leaves_the_toggle_as_it_was(self, qapp):
        dialog = EffectLevelsDialog(RefusingController())
        dialog.pin_buttons['spring'].click()     # would abort the app if it raised
        assert not dialog.pin_buttons['spring'].isChecked()
        assert dialog.pin_buttons['master'].isChecked()

    def test_closing_keeps_the_values(self, ctl):
        dialog = EffectLevelsDialog(ctl)
        dialog.show_for('joystick')
        _drag(dialog.sliders['constant'], 65)
        dialog.close()
        assert ctl.levels('joystick')['constant'] == 65
        dialog.show_for('joystick')
        try:
            assert dialog.sliders['constant'].value() == 65
        finally:
            dialog.close()


# --- device strip ---------------------------------------------------------------

def _full_icon(device):
    panel = DeviceIconPanel()
    panel.set_devices([device])
    return panel, panel.icons[device]


def _compact_chip(device):
    panel = MiniDevicePanel()
    panel.set_devices([device])
    return panel, panel.chips[device]


class TestDeviceMarks:
    """The device icons' muted and lowered-levels marks and tooltip lines."""

    def test_both_lines_survive_a_status_tooltip_and_clear_independently(self, qapp):
        panel = DeviceIconPanel()
        panel.set_devices(['joystick'])
        icon = panel.icons['joystick']
        panel.set_device_muted('joystick', 'MUTED')
        panel.set_device_levels_note('joystick', 'LEVELS')
        icon.setToolTip('BASE')
        assert icon.toolTip().split('\n') == ['BASE', 'MUTED', 'LEVELS']
        panel.set_device_muted('joystick', None)
        assert icon.toolTip().split('\n') == ['BASE', 'LEVELS']
        panel.set_device_muted('joystick', 'MUTED')
        panel.set_device_levels_note('joystick', None)
        assert icon.toolTip().split('\n') == ['BASE', 'MUTED']

    def test_kept_when_the_icons_are_rebuilt(self, qapp):
        panel = DeviceIconPanel()
        panel.set_devices(['joystick', 'pedals'])
        panel.set_device_muted('pedals', 'MUTED')
        panel.set_device_levels_note('pedals', 'LEVELS')
        panel.set_devices(['joystick', 'pedals', 'collective'])
        icon = panel.icons['pedals']
        assert not icon._mute_badge.isHidden() and not icon._levels_badge.isHidden()
        assert 'MUTED' in icon.toolTip() and 'LEVELS' in icon.toolTip()

    def test_the_compact_row_carries_it_too(self, qapp):
        mini = MiniDevicePanel()
        mini.set_devices(['joystick', 'pedals'])
        chip = mini.chips['pedals']
        chip.setToolTip('BASE')
        mini.set_device_muted('pedals', 'MUTED')
        mini.set_device_levels_note('pedals', 'LEVELS')
        assert chip.toolTip().split('\n') == ['BASE', 'MUTED', 'LEVELS']
        mini.set_device_muted('pedals', None)
        assert chip._mute_badge.isHidden()
        assert chip.toolTip().split('\n') == ['BASE', 'LEVELS']
        mini.set_device_levels_note('pedals', None)
        assert chip.toolTip() == 'BASE'

    @pytest.mark.parametrize('make', [_full_icon, _compact_chip], ids=['icon', 'chip'])
    def test_each_mark_shows_only_with_its_own_note(self, qapp, make):
        panel, widget = make('pedals')
        for levels in ('LEVELS', None):
            panel.set_device_levels_note('pedals', levels)
            for muted in ('MUTED', None, 'MUTED'):
                panel.set_device_muted('pedals', muted)
                assert widget._levels_badge.isHidden() == (levels is None)
                assert widget._mute_badge.isHidden() == (muted is None)
        for muted in ('MUTED', None):
            panel.set_device_muted('pedals', muted)
            for levels in ('LEVELS', None, 'LEVELS'):
                panel.set_device_levels_note('pedals', levels)
                assert widget._mute_badge.isHidden() == (muted is None)
                assert widget._levels_badge.isHidden() == (levels is None)


# --- readouts ---------------------------------------------------------------------

class TestReadoutNote:
    def test_a_lowered_level_or_a_mute_adds_the_note(self, ctl):
        assert readout_note('joystick') == ''
        ctl.set_levels('joystick', {'damper': 80})
        assert readout_note('joystick')
        assert readout_note('pedals') == ''
        ctl.set_muted('pedals', True)
        assert readout_note('pedals')

    def test_monitor_effect_tooltips_carry_it(self, ctl, monkeypatch):
        from telemffb.ui.panels.MonitorPanel import MonitorPanel
        monkeypatch.setattr(G, 'system_settings', SimpleNamespace(get=lambda k, d=None, **kw: d),
                            raising=False)
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
        monkeypatch.setattr(G, 'current_device_config_scope', 'pedals', raising=False)
        panel = MonitorPanel()
        effects = [{'label': 'ID:1 Engine Rumble', 'intensity': 0.3}]

        def tip():
            panel.update_effects(effects)
            model = panel._effects_model
            return model.data(model.index(0, 1), Qt.ItemDataRole.ToolTipRole)

        plain = tip()
        ctl.set_levels('joystick', {'master': 50})   # not the device shown
        assert tip() == plain
        ctl.set_levels('pedals', {'master': 50})
        assert tip() == f"{plain}\n{readout_note('pedals')}"


# --- menu ----------------------------------------------------------------------------

class TestMuteAllMenuEntry:
    def _menu(self):
        from PyQt6.QtGui import QAction
        from telemffb.ui import menus
        menu = menus.MainMenu.__new__(menus.MainMenu)
        menu.mw = None
        menu.mute_all_action = QAction('x')
        menu.mute_all_action.setCheckable(True)
        return menu

    def test_follows_the_mute(self, ctl):
        menu = self._menu()
        ctl.set_muted_all(True)
        menu.refresh_mute_all_action()
        assert menu.mute_all_action.isChecked()
        ctl.set_muted('pedals', False)
        menu.refresh_mute_all_action()
        assert not menu.mute_all_action.isChecked()
