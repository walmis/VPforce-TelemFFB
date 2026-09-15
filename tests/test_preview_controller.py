"""EffectPreviewController: the preview lifecycle without a main window.

The controller owns the local run, the play-all group and the settings-row
cue; the window only lends it a parent for dialogs and a root for the row
lock.  So it is driven here against a bare QWidget, with the runner, the
timed player, the aircraft build, the dialogs and the row lock all faked
at the controller's own module boundary - what is under test is the state
machine: who starts, who stops, when the rows release, what the children
are told, and what happens when a child never answers.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets

import telemffb.globals as G
import telemffb.preview_controller as pc
from telemffb.preview import JET_ENGINE_RUMBLE, AFTERBURNER, TOUCHDOWN, PREVIEW_SPECS


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeTimed:
    """Stands in for TimedPreview: nothing ticks until the test says so."""
    instances = []

    def __init__(self, runner, on_finished=None):
        self.runner = runner
        self.on_finished = on_finished
        self.running = False
        self.stopped = False
        FakeTimed.instances.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.stopped = True
        self.on_finished()

    def finish(self):
        """The run reaching its last frame on its own."""
        self.running = False
        self.on_finished()


class FakeRunner:
    def __init__(self, aircraft, spec, sim):
        self.aircraft, self.spec, self.sim = aircraft, spec, sim
        self.steps_total, self.frame_rate = 10, 30.0


class FakeIPC:
    def __init__(self, connected=True):
        self.connected = connected
        self.sent = []

    def child_device_connected(self, dev):
        return self.connected

    def send_preview(self, dev, name):
        self.sent.append(('PREVIEW', dev, name))

    def send_preview_stop(self, dev):
        self.sent.append(('STOP', dev))

    def send_preview_done(self, name):
        self.sent.append(('DONE', name))


class FakeMessageBox:
    """Records every dialog; answers constant-force confirmations as told."""
    StandardButton = QtWidgets.QMessageBox.StandardButton

    def __init__(self):
        self.shown = []
        self.confirm = True

    def information(self, parent, title, text, *a):
        self.shown.append(('info', title, text))

    def warning(self, parent, title, text, *a):
        self.shown.append(('warning', title, text))
        return self.StandardButton.Ok if self.confirm else self.StandardButton.Cancel


@pytest.fixture
def rig(qapp, monkeypatch):
    """A controller on a bare widget, every collaborator faked."""
    FakeTimed.instances = []
    window = QtWidgets.QWidget()
    ipc = FakeIPC()
    box = FakeMessageBox()
    locks = []
    monkeypatch.setattr(pc, 'TimedPreview', FakeTimed)
    monkeypatch.setattr(pc, 'PreviewRunner', FakeRunner)
    monkeypatch.setattr(pc, 'QMessageBox', box)
    monkeypatch.setattr(pc, 'HapticEffect', SimpleNamespace(device_alive=lambda: True))
    monkeypatch.setattr(pc.TelemManager, 'build_aircraft', lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(pc, 'lock_preview_rows',
                        lambda root, spec, locked, slot=None: locks.append((spec, locked, slot)))
    monkeypatch.setattr(G, 'device_type', 'joystick', raising=False)
    monkeypatch.setattr(G, 'master_instance', True, raising=False)
    monkeypatch.setattr(G, 'current_device_config_scope', 'joystick', raising=False)
    monkeypatch.setattr(G, 'launched_instances', {'pedals': object()}, raising=False)
    monkeypatch.setattr(G, 'ipc_instance', ipc, raising=False)
    monkeypatch.setattr(G, 'telem_manager', None, raising=False)
    monkeypatch.setattr(G, 'settings_mgr', SimpleNamespace(
        current_sim='DCS', current_aircraft_name='F-16C_50', current_class='JetAircraft'), raising=False)
    ctl = pc.EffectPreviewController(window)
    return SimpleNamespace(ctl=ctl, window=window, ipc=ipc, box=box, locks=locks,
                           timed=lambda: FakeTimed.instances[-1])


class TestLocalRun:
    def test_toggle_starts_on_the_scoped_device_and_holds_the_row(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE)
        assert rig.timed().running and rig.timed().runner.spec is JET_ENGINE_RUMBLE
        assert rig.ctl.running_spec() == (JET_ENGINE_RUMBLE, 'pv')
        assert rig.locks == [(JET_ENGINE_RUMBLE, True, 'pv')]
        assert rig.ipc.sent == []                              # nothing left this instance

    def test_toggle_again_stops_it_and_releases_the_row(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE)
        rig.ctl.toggle(JET_ENGINE_RUMBLE)
        assert rig.timed().stopped
        assert rig.ctl.running_spec() == (None, None)
        assert rig.locks[-1] == (JET_ENGINE_RUMBLE, False, None)

    def test_a_run_that_ends_on_its_own_releases_the_row(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE)
        rig.timed().finish()
        assert rig.ctl.running_spec() == (None, None)
        assert rig.locks[-1] == (JET_ENGINE_RUMBLE, False, None)

    def test_starting_another_preview_stops_the_first(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE)
        first = rig.timed()
        rig.ctl.toggle(AFTERBURNER)
        assert first.stopped
        assert rig.ctl.running_spec() == (AFTERBURNER, 'pv')
        # the first row released before the second was held
        assert rig.locks == [(JET_ENGINE_RUMBLE, True, 'pv'), (JET_ENGINE_RUMBLE, False, None),
                             (AFTERBURNER, True, 'pv')]

    def test_debug_menu_start_carries_no_button_slot(self, rig):
        assert rig.ctl.start(JET_ENGINE_RUMBLE)
        assert rig.ctl.running_spec() == (JET_ENGINE_RUMBLE, None)


class TestBlockedAndConfirmed:
    def test_blocked_start_shows_the_reasons_and_does_not_run(self, rig, monkeypatch):
        monkeypatch.setattr(pc, 'HapticEffect', SimpleNamespace(device_alive=lambda: False))
        assert not rig.ctl.start(JET_ENGINE_RUMBLE)
        assert rig.box.shown and rig.box.shown[0][0] == 'info'
        assert 'device' in rig.box.shown[0][2]
        assert FakeTimed.instances == [] and rig.locks == []

    def test_blocked_start_without_confirm_only_logs(self, rig, monkeypatch):
        monkeypatch.setattr(pc, 'HapticEffect', SimpleNamespace(device_alive=lambda: False))
        assert not rig.ctl.start(JET_ENGINE_RUMBLE, confirm=False)
        assert rig.box.shown == []

    def test_remote_scope_blockers_come_from_the_child_state(self, rig, monkeypatch):
        monkeypatch.setattr(G, 'current_device_config_scope', 'collective', raising=False)
        assert rig.ctl.blockers() == ["no collective instance is running"]
        monkeypatch.setattr(G, 'current_device_config_scope', 'pedals', raising=False)
        assert rig.ctl.blockers() == []
        rig.ipc.connected = False
        assert rig.ctl.blockers() == ["no pedals device connected"]

    def test_constant_force_asks_first_and_honors_cancel(self, rig):
        rig.box.confirm = False
        assert not rig.ctl.start(TOUCHDOWN)
        assert rig.box.shown[0][1] == "Constant Force Preview"
        assert FakeTimed.instances == []
        rig.box.confirm = True
        assert rig.ctl.start(TOUCHDOWN)
        assert rig.timed().running

    def test_running_devices_lists_connected_children_only(self, rig):
        assert rig.ctl.running_devices() == ['joystick', 'pedals']
        rig.ipc.connected = False
        assert rig.ctl.running_devices() == ['joystick']


class TestGroupRun:
    def test_play_all_drives_the_child_over_ipc_and_the_local_device_directly(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE, devices=('joystick', 'pedals'))
        assert rig.ipc.sent == [('PREVIEW', 'pedals', JET_ENGINE_RUMBLE.name)]
        assert rig.timed().running                             # the local half
        assert rig.ctl.running_spec() == (JET_ENGINE_RUMBLE, 'pvall')
        assert rig.locks == [(JET_ENGINE_RUMBLE, True, 'pvall')]   # one hold for the group, not one per device

    def test_the_group_ends_when_every_device_has_reported(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE, devices=('joystick', 'pedals'))
        rig.timed().finish()                                   # local done first
        assert rig.ctl.running(JET_ENGINE_RUMBLE)              # still waiting on the child
        assert rig.locks == [(JET_ENGINE_RUMBLE, True, 'pvall')]
        rig.ctl.on_child_done('pedals', JET_ENGINE_RUMBLE.name)
        assert rig.ctl.running_spec() == (None, None)
        assert rig.locks[-1] == (JET_ENGINE_RUMBLE, False, None)
        assert not rig.ctl._group['timer'].isActive() if rig.ctl._group else True

    def test_a_done_for_another_preview_is_ignored(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE, devices=('joystick', 'pedals'))
        rig.timed().finish()
        rig.ctl.on_child_done('pedals', AFTERBURNER.name)
        assert rig.ctl.running(JET_ENGINE_RUMBLE)

    def test_stopping_a_group_tells_the_children_and_stops_locally(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE, devices=('joystick', 'pedals'))
        rig.ctl.toggle(JET_ENGINE_RUMBLE)
        assert ('STOP', 'pedals') in rig.ipc.sent
        assert rig.timed().stopped
        assert rig.ctl.running_spec() == (None, None)

    def test_a_child_that_never_answers_is_timed_out(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE, devices=('joystick', 'pedals'))
        timer = rig.ctl._group['timer']
        assert timer.isActive()
        assert timer.interval() == int((JET_ENGINE_RUMBLE.duration + JET_ENGINE_RUMBLE.tail + 2.0) * 1000)
        rig.timed().finish()
        timer.timeout.emit()                                   # the fallback firing
        assert rig.ctl.running_spec() == (None, None)
        assert rig.locks[-1] == (JET_ENGINE_RUMBLE, False, None)

    def test_children_only_group_needs_no_local_run(self, rig):
        rig.ctl.toggle(JET_ENGINE_RUMBLE, devices=('pedals',))
        assert FakeTimed.instances == []
        assert rig.ipc.sent == [('PREVIEW', 'pedals', JET_ENGINE_RUMBLE.name)]
        rig.ctl.on_child_done('pedals', JET_ENGINE_RUMBLE.name)
        assert rig.ctl.running_spec() == (None, None)


class TestChildSide:
    def test_a_child_plays_unconfirmed_and_reports_when_done(self, rig):
        rig.ctl.start_child(TOUCHDOWN.name)                    # constant force, yet no dialog
        assert rig.box.shown == []
        assert rig.timed().running
        rig.timed().finish()
        assert rig.ipc.sent == [('DONE', TOUCHDOWN.name)]

    def test_an_unknown_name_is_reported_done_at_once(self, rig):
        rig.ctl.start_child('no-such-preview')
        assert rig.ipc.sent == [('DONE', 'no-such-preview')]
        assert FakeTimed.instances == []

    def test_a_refused_child_run_still_reports_done(self, rig, monkeypatch):
        monkeypatch.setattr(pc, 'HapticEffect', SimpleNamespace(device_alive=lambda: False))
        rig.ctl.start_child(JET_ENGINE_RUMBLE.name)
        assert rig.ipc.sent == [('DONE', JET_ENGINE_RUMBLE.name)]

    def test_every_shipped_spec_is_addressable_by_name(self):
        assert all(PREVIEW_SPECS[s.name] is s for s in PREVIEW_SPECS.values())
