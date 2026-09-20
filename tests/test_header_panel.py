"""HeaderPanel (telemffb/ui/panels/HeaderPanel.py) - the Application
Status box, full width above the tabs.

Extracted from MainWindow's inline construction of the AppStatusWidget
instance, and its ``refresh_scope_status_indicators()`` push (now
``HeaderPanel.bind`` / ``_on_scope_status_changed``) of the vpconf-profile
and gain-override indicators, which follow AppState.

No real MainWindow here: HeaderPanel needs nothing from the outside but
``G.master_instance``.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import telemffb.globals as G
from telemffb.state.app_state import AppState
from telemffb.ui.panels.HeaderPanel import HeaderPanel
from telemffb.ui.widgets.custom_widgets import AppStatusWidget

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def state(qapp):
    s = AppState()
    s.set_own_device_type('joystick')
    s.set_master(True)
    return s


class TestHeaderPanelConstruction:
    def test_owns_the_status_container(self, qapp, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel = HeaderPanel()
        assert isinstance(panel.status_container, AppStatusWidget)

    def test_status_container_starts_on_the_waiting_page(self, qapp, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel = HeaderPanel()
        assert panel.status_container.sim_status_label.sim_label.text() == "Waiting..."

    def test_master_instance_gets_a_usable_profile_combo(self, qapp, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel = HeaderPanel()
        assert panel.status_container.cb_selectProfileCombo.isEnabled()

    def test_child_instance_disables_the_profile_combo(self, qapp, monkeypatch):
        """A child instance has no profile-selection UI of its own - it
        mirrors the master's selection instead (see OfflineEditorPanel)."""
        monkeypatch.setattr(G, 'master_instance', False, raising=False)
        panel = HeaderPanel()
        assert not panel.status_container.cb_selectProfileCombo.isEnabled()


class TestMessageArea:
    """The error notice and the offline banner share an area at the foot of
    the box. It used to hold its 60px whether or not it had anything to
    say, which the box - and on the Hide tab the whole window - paid for
    all the time. Now it takes height only while there is a message."""

    def _panel(self, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        monkeypatch.setattr(G, 'useDarkMode', True, raising=False)
        panel = HeaderPanel()
        panel.resize(1000, 300)
        panel.show()
        QApplication.processEvents()
        return panel

    def test_it_is_hidden_with_nothing_to_say(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        assert panel.status_container.message_container.isHidden()
        panel.close()

    def test_an_error_shows_it_and_clearing_the_error_hides_it(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        container = panel.status_container.message_container
        panel.status_container.flag_error("Something is wrong")
        assert not container.isHidden()
        panel.status_container.clear_error()
        assert container.isHidden()
        panel.close()

    def test_the_offline_banner_shows_it_and_going_back_online_hides_it(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        container = panel.status_container.message_container
        panel.set_offline('MSFS')
        assert not container.isHidden()
        panel.reset()
        assert container.isHidden()
        panel.close()

    def test_the_box_is_shorter_without_a_message_and_grows_for_one(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        quiet = panel.sizeHint().height()
        panel.status_container.flag_error("Something is wrong")
        assert panel.sizeHint().height() > quiet
        panel.status_container.clear_error()
        assert panel.sizeHint().height() == quiet
        panel.close()


class TestHeaderPanelBind:
    """bind() replaces MainWindow's old refresh_scope_status_indicators push:
    AppState.scope_status_changed -> the AppStatusWidget's own (queued)
    request signals."""

    def test_bind_paints_whatever_state_already_holds(self, qapp, monkeypatch, state):
        """AppState does not re-emit just because a new subscriber
        connected, so bind() has to pull the current value, not wait for
        the next change - same contract as PromptStack.bind()."""
        state.set_own_vpconf('C:/profiles/mine.vpconf')
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.active_vpconf_label.text() == 'mine'
        assert not panel.status_container.active_vpconf_label.isHidden()

    def test_a_later_vpconf_update_reaches_the_widget(self, qapp, monkeypatch, state):
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.active_vpconf_label.isHidden()

        state.set_own_vpconf('C:/profiles/other.vpconf')
        assert panel.status_container.active_vpconf_label.text() == 'other'
        assert not panel.status_container.active_vpconf_label.isHidden()

    def test_gain_overrides_toggle_the_configurator_pill(self, qapp, monkeypatch, state):
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.active_configurator_label.isHidden()

        state.set_own_gain_overrides_active(True)
        assert panel.status_container.active_configurator_label.text() == 'Active'
        assert not panel.status_container.active_configurator_label.isHidden()

        state.set_own_gain_overrides_active(False)
        assert panel.status_container.active_configurator_label.text() == 'None'

    def test_scoping_to_a_child_shows_the_childs_own_state(self, qapp, monkeypatch, state):
        """Master, scoped to a child device, shows what that child last
        reported over IPC rather than its own vpconf/gain state."""
        state.set_own_vpconf('C:/profiles/master.vpconf')
        state.set_child_status('pedals', vpconf='C:/profiles/child.vpconf')
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.active_vpconf_label.text() == 'master'

        state.set_scope('pedals')
        assert panel.status_container.active_vpconf_label.text() == 'child'


class TestHeaderPanelProfileCombo:
    """set_profile_choices/profile_chosen replace MainWindow's old direct
    manipulation of status_container.cb_selectProfileCombo - see
    AppStatusWidget.set_profile_choices/_on_profile_combo_changed."""

    def _panel(self, monkeypatch, master=True):
        monkeypatch.setattr(G, 'master_instance', master, raising=False)
        return HeaderPanel()

    def test_set_profile_choices_lists_the_profiles_and_add_new(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        panel.set_profile_choices(['Built-In', 'Auto User'])
        combo = panel.status_container.cb_selectProfileCombo
        assert [combo.itemText(i) for i in range(combo.count())] == \
            ['Built-In', 'Auto User', 'Add New...']

    def test_set_profile_choices_does_not_emit_profile_chosen(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        chosen = []
        panel.profile_chosen.connect(chosen.append)
        panel.set_craft_info('C172', 'C172.*', 'Auto User')
        panel.set_profile_choices(['Built-In', 'Auto User'])
        assert chosen == []

    def test_the_active_profile_is_the_combos_selection(self, qapp, monkeypatch):
        """Whichever of the two arrives first - the list or the name."""
        panel = self._panel(monkeypatch)
        combo = panel.status_container.cb_selectProfileCombo
        panel.set_profile_choices(['Built-In', 'Auto User'])
        panel.set_craft_info('C172', 'C172.*', 'Auto User')
        assert combo.currentText() == 'Auto User'

        other = self._panel(monkeypatch)
        other.set_craft_info('C172', 'C172.*', 'Auto User')
        other.set_profile_choices(['Built-In', 'Auto User'])
        assert other.status_container.cb_selectProfileCombo.currentText() == 'Auto User'

    def test_the_master_shows_no_separate_value_label(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        assert panel.status_container.active_profile_label.isHidden()
        assert not panel.status_container.cb_selectProfileCombo.isHidden()

    def test_a_child_keeps_the_label_and_has_no_combo(self, qapp, monkeypatch):
        """It mirrors the master's choice and has nothing to pick."""
        panel = self._panel(monkeypatch, master=False)
        panel.set_craft_info('C172', 'C172.*', 'Auto User')
        assert not panel.status_container.active_profile_label.isHidden()
        assert panel.status_container.active_profile_label.text() == 'Auto User'
        assert panel.status_container.cb_selectProfileCombo.isHidden()

    def test_a_state_that_is_not_a_profile_shows_as_placeholder_text(self, qapp, monkeypatch):
        """"(None)" with no sim, "Offline" in the offline editor: nothing
        is selected, and the combo says so in its own text."""
        panel = self._panel(monkeypatch)
        combo = panel.status_container.cb_selectProfileCombo
        panel.set_profile_choices(['Built-In', 'Auto User'])
        panel.set_craft_info('C172', 'C172.*', 'Auto User')
        panel.set_offline('MSFS')
        assert combo.currentIndex() == -1
        assert combo.placeholderText() == 'Offline'
        panel.reset()
        assert combo.currentText() == 'Auto User'

    def test_picking_a_profile_emits_its_name(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        chosen = []
        panel.profile_chosen.connect(chosen.append)
        panel.set_profile_choices(['Built-In', 'Auto User'])
        panel.set_craft_info('C172', 'C172.*', 'Built-In')
        combo = panel.status_container.cb_selectProfileCombo

        combo.setCurrentIndex(combo.findText('Auto User'))

        assert chosen == ['Auto User']

    def test_the_combo_shows_what_the_handler_made_active(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        panel.set_profile_choices(['Built-In', 'Auto User'])
        panel.set_craft_info('C172', 'C172.*', 'Built-In')
        panel.profile_chosen.connect(lambda name: panel.set_craft_info('C172', 'C172.*', name))
        combo = panel.status_container.cb_selectProfileCombo

        combo.setCurrentIndex(combo.findText('Auto User'))

        assert combo.currentText() == 'Auto User'

    def test_a_refused_change_goes_back_to_the_active_profile(self, qapp, monkeypatch):
        """Nothing reported a new active profile - a cancelled dialog, a
        refused change - so the combo must not be left on what was clicked."""
        panel = self._panel(monkeypatch)
        panel.set_profile_choices(['Built-In', 'Auto User'])
        panel.set_craft_info('C172', 'C172.*', 'Built-In')
        combo = panel.status_container.cb_selectProfileCombo

        combo.setCurrentIndex(combo.findText('Auto User'))

        assert combo.currentText() == 'Built-In'

    def test_add_new_is_offered_but_never_left_selected(self, qapp, monkeypatch):
        panel = self._panel(monkeypatch)
        chosen = []
        panel.profile_chosen.connect(chosen.append)
        panel.set_profile_choices(['Built-In', 'Auto User'])
        panel.set_craft_info('C172', 'C172.*', 'Built-In')
        combo = panel.status_container.cb_selectProfileCombo

        combo.setCurrentIndex(combo.findText('Add New...'))

        assert chosen == ['Add New...']
        assert combo.currentText() == 'Built-In'


class TestHeaderPanelSimStatusBind:
    """bind() also replaces MainWindow's old update_sim_indicators +
    explicit request_clear_error call: AppState.sim_status_changed ->
    set_running/set_paused/set_error plus the flag/clear-error request
    signals, fired only on the actual onset/clear transition."""

    def test_bind_paints_a_status_already_reported(self, qapp, monkeypatch, state):
        state.set_sim_status('running', 'DCS')
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.sim_status_label.sim_label.text() == 'DCS'
        assert panel.status_container.sim_status_label.status_label.text() == 'Running'

    def test_nothing_reported_yet_stays_on_the_waiting_page(self, qapp, monkeypatch, state):
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.sim_status_label.sim_label.text() == "Waiting..."

    def test_reconnect_after_sim_exit_repaints_running(self, qapp, monkeypatch, state):
        """Sim exit resets the widget directly; the next connection's
        'running' (same as before the exit) must still repaint it."""
        panel = HeaderPanel()
        panel.bind(state)
        state.set_sim_status('running', 'DCS')
        panel.status_container.reset_sim_state('DCS')
        state.reset_sim_status()
        state.set_sim_status('running', 'DCS')
        assert panel.status_container.sim_status_label.sim_label.text() == 'DCS'  # not "Waiting..."

    def test_same_error_after_reset_flags_again(self, qapp, monkeypatch, state):
        flagged = []
        panel = HeaderPanel()
        panel.status_container.request_flag_error.connect(lambda msg: flagged.append(msg))
        panel.bind(state)
        state.set_sim_status('error', 'DCS', 'bad config')
        state.reset_sim_status()
        state.set_sim_status('error', 'DCS', 'bad config')
        assert flagged == ['bad config', 'bad config']

    def test_running_then_paused(self, qapp, monkeypatch, state):
        panel = HeaderPanel()
        panel.bind(state)
        state.set_sim_status('running', 'DCS')
        state.set_sim_status('paused', 'DCS')
        assert panel.status_container.sim_status_label.status_label.text() == 'Paused'

    def test_error_onset_flags_but_repeated_error_does_not_reflag(self, qapp, monkeypatch, state):
        flagged = []
        panel = HeaderPanel()
        panel.status_container.request_flag_error.connect(lambda msg: flagged.append(msg))
        panel.bind(state)

        state.set_sim_status('error', 'DCS', 'bad config')
        assert flagged == ['bad config']
        assert panel.status_container.sim_status_label.status_label.text() == 'Error'

        # A different message while still erroring is a distinct AppState
        # value (SimStatusTracker itself only calls this once per onset in
        # practice) - the panel does not gate on message, only on the
        # error->non-error edge, so this exercises that it does not fire a
        # *clear* here either.
        state.set_sim_status('error', 'DCS', 'still bad')
        assert flagged == ['bad config']

    def test_error_to_running_fires_clear_error_once(self, qapp, monkeypatch, state):
        cleared = []
        panel = HeaderPanel()
        panel.status_container.request_clear_error.connect(lambda: cleared.append(True))
        panel.bind(state)

        state.set_sim_status('error', 'DCS', 'bad config')
        assert cleared == []
        state.set_sim_status('running', 'DCS')
        assert cleared == [True]

        # Back-to-back non-error states must not refire clear-error.
        state.set_sim_status('paused', 'DCS')
        assert cleared == [True]
