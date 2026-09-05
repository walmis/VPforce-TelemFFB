"""The startup offer to put back tap files a game folder has lost.

A sim opted into the tap should hold the wrapper and its config beside
every executable.  A game update takes them; on startup the master sweeps
every enabled, tap-opted sim and offers - one prompt, per-sim
deselectable - to reinstall what is gone.  The wrapper is copied back
where absent; a config is copied from a surviving twin, generated from
FFB-fix-only mode, or asked for, as a fresh install would.  Foreign DLLs
are never touched.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.tap_install import (SIMS_BY_KEY, SimStatus, TapDevice,
                                  TargetStatus, WrapperState)
from telemffb import TapRepairDialog as mod

pytestmark = [pytest.mark.unit]


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Settings(dict):
    def get(self, name, default=None, instance=None):
        return dict.get(self, name, default)


ENABLED = {'enableDCS': True, 'enableTapDCS': True,
           'enableIL2': True, 'enableTapIL2': True,
           'enableBMS': True, 'enableTapBMS': True}


def status_for(key, *targets, root=r"C:\Games\X"):
    return SimStatus(sim=SIMS_BY_KEY[key], root=root, provenance="test",
                     targets=list(targets))


def target(state, has_config, directory=r"C:\Games\X\bin"):
    return TargetStatus(directory=directory, state=state,
                        has_config=has_config)


def stick(directinput=True):
    return TapDevice(role='joystick', vid=0x045E, pid=0x001B,
                     ident='SideWinder', directinput=directinput)


class TestPendingScan:
    def _scan(self, monkeypatch, statuses, settings=None):
        monkeypatch.setattr(mod, 'all_status', lambda s: statuses)
        return mod.pending_wrapper_repairs(settings or Settings(ENABLED))

    def test_a_missing_wrapper_beside_a_surviving_config_is_offered(self, monkeypatch):
        pending = self._scan(monkeypatch, [status_for(
            'DCS', target(WrapperState.ABSENT, True))])
        assert [r.sim.key for r in pending] == ['DCS']
        assert len(pending[0].missing_wrapper) == 1
        assert pending[0].missing_config == []

    def test_a_missing_config_beside_our_wrapper_is_offered(self, monkeypatch):
        pending = self._scan(monkeypatch, [status_for(
            'DCS', target(WrapperState.TAP, False))])
        assert pending[0].missing_wrapper == []
        assert len(pending[0].missing_config) == 1

    def test_both_gone_reports_both(self, monkeypatch):
        pending = self._scan(monkeypatch, [status_for(
            'DCS', target(WrapperState.ABSENT, False))])
        assert len(pending[0].missing_wrapper) == 1
        assert len(pending[0].missing_config) == 1

    def test_only_the_emptied_target_of_two_is_listed(self, monkeypatch):
        pending = self._scan(monkeypatch, [status_for(
            'DCS',
            target(WrapperState.TAP, True, r"C:\Games\X\bin"),
            target(WrapperState.ABSENT, False, r"C:\Games\X\bin-mt"))])
        assert pending[0].where() == [r"C:\Games\X\bin-mt"]

    def test_an_intact_install_is_not_offered(self, monkeypatch):
        assert self._scan(monkeypatch, [status_for(
            'DCS', target(WrapperState.TAP, True))]) == []

    def test_a_foreign_dll_is_left_alone(self, monkeypatch):
        assert self._scan(monkeypatch, [status_for(
            'DCS', target(WrapperState.FOREIGN, False))]) == []

    def test_a_sim_without_the_tap_opt_in_is_skipped(self, monkeypatch):
        settings = Settings(dict(ENABLED, enableTapBMS=False))
        assert self._scan(monkeypatch, [status_for(
            'BMS', target(WrapperState.ABSENT, False))], settings) == []

    def test_a_disabled_sim_is_skipped(self, monkeypatch):
        settings = Settings(dict(ENABLED, enableDCS=False))
        assert self._scan(monkeypatch, [status_for(
            'DCS', target(WrapperState.ABSENT, False))], settings) == []

    def test_a_sim_that_was_not_found_is_skipped(self, monkeypatch):
        assert self._scan(monkeypatch, [status_for(
            'DCS', target(WrapperState.ABSENT, False), root=None)]) == []


class TestDialog:
    def _pending(self):
        return [mod.TapRepair(status_for('DCS', target(WrapperState.ABSENT, True)),
                              missing_wrapper=[target(WrapperState.ABSENT, True)]),
                mod.TapRepair(status_for('IL2', target(WrapperState.TAP, False)),
                              missing_config=[target(WrapperState.TAP, False)])]

    def test_every_sim_is_listed_and_preselected(self, app):
        dialog = mod.TapRepairDialog(self._pending())
        labels = [box.text() for box, _ in dialog._boxes]
        assert any('DCS' in t for t in labels)
        assert any('IL-2' in t for t in labels)
        assert len(dialog.selected()) == 2

    def test_deselecting_a_sim_excludes_it(self, app):
        dialog = mod.TapRepairDialog(self._pending())
        dialog._boxes[0][0].setChecked(False)
        assert [r.sim.key for r in dialog.selected()] == ['IL2']


class Outcome:
    def __init__(self, directory, ok=True, action='installed', detail=''):
        self.directory, self.ok, self.action, self.detail = (
            directory, ok, action, detail)


class TestApply:
    def test_wrapper_copied_only_where_absent_with_the_surviving_config(
            self, monkeypatch):
        kept = target(WrapperState.TAP, True, r"C:\Games\X\bin")
        gone = target(WrapperState.ABSENT, False, r"C:\Games\X\bin-mt")
        repair = mod.TapRepair(status_for('DCS', kept, gone),
                               missing_wrapper=[gone], missing_config=[gone])
        installs = []
        monkeypatch.setattr(mod, 'read_config', lambda status: 'SURVIVING')
        monkeypatch.setattr(mod, 'install', lambda status, text=None: (
            installs.append(([t.directory for t in status.targets], text))
            or [Outcome(gone.directory)]))
        monkeypatch.setattr(mod, 'write_one_config', lambda d, t: pytest.fail(
            'install writes the config beside a reinstalled wrapper itself'))

        outcomes, skipped = mod.apply_repairs([repair], Settings(ENABLED))
        assert installs == [([r"C:\Games\X\bin-mt"], 'SURVIVING')]
        assert skipped == [] and [o.ok for o in outcomes] == [True]

    def test_config_gone_beside_our_wrapper_is_asked_for_and_written(
            self, monkeypatch):
        bare = target(WrapperState.TAP, False)
        repair = mod.TapRepair(status_for('DCS', bare), missing_config=[bare])
        monkeypatch.setattr(mod, 'read_config', lambda status: None)
        monkeypatch.setattr(mod, 'configured_devices', lambda s: [stick()])
        asked = []
        monkeypatch.setattr(mod, 'ask_for_devices',
                            lambda sim, devices, parent=None: (
                                asked.append(sim.key) or ([stick()], [], [], [])))
        monkeypatch.setattr(mod, 'generate_config',
                            lambda chosen, ordered, blocked: 'GENERATED')
        written = []
        monkeypatch.setattr(mod, 'write_one_config', lambda d, t: (
            written.append((d, t)) or Outcome(d, action='configured')))
        monkeypatch.setattr(mod, 'install', lambda *a, **k: pytest.fail(
            'nothing to install when the wrapper is still there'))

        outcomes, skipped = mod.apply_repairs([repair], Settings(ENABLED))
        assert asked == ['DCS']
        assert written == [(bare.directory, 'GENERATED')]
        assert skipped == []

    def test_a_cancelled_device_dialog_leaves_the_sim_for_its_tab(
            self, monkeypatch):
        gone = target(WrapperState.ABSENT, False)
        repair = mod.TapRepair(status_for('DCS', gone),
                               missing_wrapper=[gone], missing_config=[gone])
        monkeypatch.setattr(mod, 'read_config', lambda status: None)
        monkeypatch.setattr(mod, 'configured_devices', lambda s: [stick()])
        monkeypatch.setattr(mod, 'ask_for_devices',
                            lambda sim, devices, parent=None: None)
        monkeypatch.setattr(mod, 'install', lambda *a, **k: pytest.fail(
            'no wrapper without a config the user agreed to'))

        outcomes, skipped = mod.apply_repairs([repair], Settings(ENABLED))
        assert outcomes == [] and skipped == ['DCS World']

    def test_fix_only_mode_regenerates_without_asking(self, monkeypatch):
        gone = target(WrapperState.ABSENT, False)
        repair = mod.TapRepair(status_for('DCS', gone),
                               missing_wrapper=[gone], missing_config=[gone])
        settings = Settings(dict(ENABLED, tapFixOnlyDCS=True))
        monkeypatch.setattr(mod, 'read_config', lambda status: None)
        monkeypatch.setattr(mod, 'configured_devices',
                            lambda s: [stick(directinput=False)])
        monkeypatch.setattr(mod, 'ask_for_devices', lambda *a, **k: pytest.fail(
            'fix-only mode decides every rule'))
        monkeypatch.setattr(mod, 'fix_only_config', lambda sim, devices: 'FIX')
        installs = []
        monkeypatch.setattr(mod, 'install', lambda status, text=None: (
            installs.append(text) or [Outcome(gone.directory)]))

        mod.apply_repairs([repair], settings)
        assert installs == ['FIX']

    def test_fix_only_mode_with_a_directinput_stick_is_left_for_its_tab(
            self, monkeypatch):
        gone = target(WrapperState.ABSENT, False)
        repair = mod.TapRepair(status_for('DCS', gone),
                               missing_wrapper=[gone], missing_config=[gone])
        settings = Settings(dict(ENABLED, tapFixOnlyDCS=True))
        monkeypatch.setattr(mod, 'read_config', lambda status: None)
        monkeypatch.setattr(mod, 'configured_devices', lambda s: [stick()])
        monkeypatch.setattr(mod, 'install', lambda *a, **k: pytest.fail(
            'fix-only with a DirectInput stick renders nothing'))

        outcomes, skipped = mod.apply_repairs([repair], settings)
        assert skipped == ['DCS World']


class TestOffer:
    def _one_pending(self, monkeypatch):
        gone = target(WrapperState.ABSENT, True)
        monkeypatch.setattr(mod, 'all_status',
                            lambda s: [status_for('DCS', gone)])
        return gone

    def test_accepting_puts_the_files_back(self, app, monkeypatch):
        gone = self._one_pending(monkeypatch)
        monkeypatch.setattr(mod, 'read_config', lambda status: 'SURVIVING')
        installed = []
        monkeypatch.setattr(mod, 'install', lambda status, text=None: (
            installed.append(status.sim.key) or [Outcome(gone.directory)]))
        monkeypatch.setattr(mod.TapRepairDialog, 'exec',
                            lambda self: QtWidgets.QDialog.DialogCode.Accepted)
        assert mod.offer_wrapper_repairs(settings=Settings(ENABLED))
        assert installed == ['DCS']

    def test_declining_touches_nothing(self, app, monkeypatch):
        self._one_pending(monkeypatch)
        monkeypatch.setattr(mod, 'install', lambda *a, **k: pytest.fail(
            'declined offer must not install'))
        monkeypatch.setattr(mod.TapRepairDialog, 'exec',
                            lambda self: QtWidgets.QDialog.DialogCode.Rejected)
        assert not mod.offer_wrapper_repairs(settings=Settings(ENABLED))

    def test_nothing_pending_shows_no_dialog(self, app, monkeypatch):
        monkeypatch.setattr(mod, 'all_status', lambda s: [
            status_for('DCS', target(WrapperState.TAP, True))])
        monkeypatch.setattr(mod.TapRepairDialog, 'exec', lambda self:
                            pytest.fail('no dialog when nothing is pending'))
        assert not mod.offer_wrapper_repairs(settings=Settings(ENABLED))

    def test_failures_and_skips_are_reported(self, app, monkeypatch):
        gone = self._one_pending(monkeypatch)
        monkeypatch.setattr(mod, 'read_config', lambda status: 'SURVIVING')
        monkeypatch.setattr(mod, 'install', lambda status, text=None: [
            Outcome(gone.directory, ok=False, action='failed',
                    detail='access denied')])
        monkeypatch.setattr(mod.TapRepairDialog, 'exec',
                            lambda self: QtWidgets.QDialog.DialogCode.Accepted)
        shown = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a: shown.append(a[2])))
        assert not mod.offer_wrapper_repairs(settings=Settings(ENABLED))
        assert shown and 'access denied' in shown[0]
