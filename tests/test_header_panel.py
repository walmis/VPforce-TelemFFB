"""HeaderPanel (telemffb/ui/panels/HeaderPanel.py) - the window's top
header: the VPforce logo, the compact mini device row, and the Application
Status box.

Extracted from MainWindow's inline construction of the logo QLabel, the
MiniDevicePanel instance and the AppStatusWidget instance, plus its
``_on_scope_status_changed`` relay (now ``HeaderPanel.bind`` /
``_on_scope_status_changed``) that keeps the vpconf-profile and
gain-override indicators in step with AppState.

No real MainWindow here: the only thing HeaderPanel needs from the outside
is ``G.vpf_logo`` resolving to a loadable image - in the app this is a Qt
resource path registered by ``import resources`` in main.py (Windows-only,
not imported by the test suite), so it is pointed at a real temp PNG here
instead of the ``:/image/...`` resource path.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

import telemffb.globals as G
from telemffb.state.app_state import AppState
from telemffb.ui.panels.DevicePanel import MiniDevicePanel
from telemffb.ui.panels.HeaderPanel import HeaderPanel
from telemffb.ui.widgets.custom_widgets import AppStatusWidget

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake_logo(tmp_path, monkeypatch, qapp):
    """A real, loadable image file standing in for the ':/image/...' Qt
    resource path, which is only registered when main.py's ``import
    resources`` has run (not the case in the test suite)."""
    path = tmp_path / "logo.png"
    QPixmap(QSize(20, 10)).save(str(path))
    monkeypatch.setattr(G, 'vpf_logo', str(path), raising=False)
    return path


@pytest.fixture
def state(qapp):
    s = AppState()
    s.set_own_device_type('joystick')
    s.set_master(True)
    return s


class TestHeaderPanelConstruction:
    def test_owns_the_mini_device_row_and_status_container(self, fake_logo, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel = HeaderPanel()
        assert isinstance(panel.device_mini_panel, MiniDevicePanel)
        assert isinstance(panel.status_container, AppStatusWidget)

    def test_mini_device_row_starts_hidden(self, fake_logo, monkeypatch):
        """MiniDevicePanel hides itself until set_devices() populates it -
        nothing here does that until MainWindow's device panel exists."""
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel = HeaderPanel()
        assert panel.device_mini_panel.isHidden()

    def test_status_container_starts_on_the_waiting_page(self, fake_logo, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel = HeaderPanel()
        assert panel.status_container.sim_status_label.sim_label.text() == "Waiting..."

    def test_master_instance_gets_a_usable_profile_combo(self, fake_logo, monkeypatch):
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel = HeaderPanel()
        assert panel.status_container.cb_selectProfileCombo.isEnabled()

    def test_child_instance_disables_the_profile_combo(self, fake_logo, monkeypatch):
        """A child instance has no profile-selection UI of its own - it
        mirrors the master's selection instead (see OfflineEditorPanel)."""
        monkeypatch.setattr(G, 'master_instance', False, raising=False)
        panel = HeaderPanel()
        assert not panel.status_container.cb_selectProfileCombo.isEnabled()

    def test_missing_logo_resource_does_not_crash(self, monkeypatch, qapp):
        """A null pixmap (missing/unregistered resource path) has width 0 -
        constructing the header used to divide by it and raise
        ZeroDivisionError."""
        monkeypatch.setattr(G, 'vpf_logo', ':/nonexistent/resource.png', raising=False)
        monkeypatch.setattr(G, 'master_instance', True, raising=False)
        panel = HeaderPanel()  # must not raise
        assert isinstance(panel.device_mini_panel, MiniDevicePanel)
        assert not panel.status_container.cb_selectProfileCombo.isVisible()


class TestHeaderPanelBind:
    """bind() replaces MainWindow's old _on_scope_status_changed relay:
    AppState.scope_status_changed -> the AppStatusWidget's own (queued)
    request signals."""

    def test_bind_paints_whatever_state_already_holds(self, fake_logo, monkeypatch, state):
        """AppState does not re-emit just because a new subscriber
        connected, so bind() has to pull the current value, not wait for
        the next change - same contract as PromptStack.bind()."""
        state.set_own_vpconf('C:/profiles/mine.vpconf')
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.active_vpconf_label.text() == 'mine'
        assert not panel.status_container.active_vpconf_label.isHidden()

    def test_a_later_vpconf_update_reaches_the_widget(self, fake_logo, monkeypatch, state):
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.active_vpconf_label.isHidden()

        state.set_own_vpconf('C:/profiles/other.vpconf')
        assert panel.status_container.active_vpconf_label.text() == 'other'
        assert not panel.status_container.active_vpconf_label.isHidden()

    def test_gain_overrides_toggle_the_configurator_pill(self, fake_logo, monkeypatch, state):
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.active_configurator_label.isHidden()

        state.set_own_gain_overrides_active(True)
        assert panel.status_container.active_configurator_label.text() == 'Active'
        assert not panel.status_container.active_configurator_label.isHidden()

        state.set_own_gain_overrides_active(False)
        assert panel.status_container.active_configurator_label.text() == 'None'

    def test_scoping_to_a_child_shows_the_childs_own_state(self, fake_logo, monkeypatch, state):
        """Master, scoped to a child device, shows what that child last
        reported over IPC rather than its own vpconf/gain state."""
        state.set_own_vpconf('C:/profiles/master.vpconf')
        state.set_child_status('pedals', vpconf='C:/profiles/child.vpconf')
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.active_vpconf_label.text() == 'master'

        state.set_scope('pedals')
        assert panel.status_container.active_vpconf_label.text() == 'child'


class TestHeaderPanelSimStatusBind:
    """bind() also replaces MainWindow's old update_sim_indicators +
    explicit request_clear_error call: AppState.sim_status_changed ->
    set_running/set_paused/set_error plus the flag/clear-error request
    signals, fired only on the actual onset/clear transition."""

    def test_bind_paints_a_status_already_reported(self, fake_logo, monkeypatch, state):
        state.set_sim_status('running', 'DCS')
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.sim_status_label.sim_label.text() == 'DCS'
        assert panel.status_container.sim_status_label.status_label.text() == 'Running'

    def test_nothing_reported_yet_stays_on_the_waiting_page(self, fake_logo, monkeypatch, state):
        panel = HeaderPanel()
        panel.bind(state)
        assert panel.status_container.sim_status_label.sim_label.text() == "Waiting..."

    def test_reconnect_after_sim_exit_repaints_running(self, fake_logo, monkeypatch, state):
        """Sim exit resets the widget directly; the next connection's
        'running' (same as before the exit) must still repaint it."""
        panel = HeaderPanel()
        panel.bind(state)
        state.set_sim_status('running', 'DCS')
        panel.status_container.reset_sim_state('DCS')
        state.reset_sim_status()
        state.set_sim_status('running', 'DCS')
        assert panel.status_container.sim_status_label.sim_label.text() == 'DCS'  # not "Waiting..."

    def test_same_error_after_reset_flags_again(self, fake_logo, monkeypatch, state):
        flagged = []
        panel = HeaderPanel()
        panel.status_container.request_flag_error.connect(lambda msg: flagged.append(msg))
        panel.bind(state)
        state.set_sim_status('error', 'DCS', 'bad config')
        state.reset_sim_status()
        state.set_sim_status('error', 'DCS', 'bad config')
        assert flagged == ['bad config', 'bad config']

    def test_running_then_paused(self, fake_logo, monkeypatch, state):
        panel = HeaderPanel()
        panel.bind(state)
        state.set_sim_status('running', 'DCS')
        state.set_sim_status('paused', 'DCS')
        assert panel.status_container.sim_status_label.status_label.text() == 'Paused'

    def test_error_onset_flags_but_repeated_error_does_not_reflag(self, fake_logo, monkeypatch, state):
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

    def test_error_to_running_fires_clear_error_once(self, fake_logo, monkeypatch, state):
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
