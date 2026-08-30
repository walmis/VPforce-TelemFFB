"""The startup offer to update installed tap wrappers.

TelemFFB ships one wrapper build; each game folder holds its own copy
that only changes when something copies over it.  On startup the master
sweeps every enabled, tap-opted sim and offers - one prompt, per-sim
deselectable - to bring outdated copies up to the bundled build.
Update only: no fresh installs, no foreign DLLs touched, configs kept.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.tap_install import (SIMS_BY_KEY, SimStatus, TargetStatus,
                                  WrapperState)
from telemffb import TapUpdateDialog as mod

pytestmark = [pytest.mark.unit]


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Settings(dict):
    def get(self, name, default=None, instance=None):
        return dict.get(self, name, default)


def status_for(key, *targets, root=r"C:\Games\X"):
    return SimStatus(sim=SIMS_BY_KEY[key], root=root, provenance="test",
                     targets=list(targets))


def target(version, state=WrapperState.TAP, directory=r"C:\Games\X\bin"):
    return TargetStatus(directory=directory, state=state, version=version)


class TestPendingScan:
    def _scan(self, monkeypatch, statuses, settings=None, bundled='0.9.1.0'):
        monkeypatch.setattr(mod, 'bundled_version', lambda: bundled)
        monkeypatch.setattr(mod, 'all_status', lambda s: statuses)
        return mod.pending_wrapper_updates(settings or Settings({
            'enableTapDCS': True, 'enableTapIL2': True}))

    def test_an_outdated_enabled_sim_is_offered(self, monkeypatch):
        pending = self._scan(monkeypatch,
                             [status_for('DCS', target('0.9.0.0'))])
        assert [(s.sim.key, len(t)) for s, t in pending] == [('DCS', 1)]

    def test_a_current_wrapper_is_not_offered(self, monkeypatch):
        assert self._scan(monkeypatch,
                          [status_for('DCS', target('0.9.1.0'))]) == []

    def test_a_sim_without_the_tap_opt_in_is_left_alone(self, monkeypatch):
        pending = self._scan(
            monkeypatch, [status_for('BMS', target('0.9.0.0'))],
            settings=Settings({'enableTapBMS': False}))
        assert pending == []

    def test_no_fresh_installs_and_no_foreign_touching(self, monkeypatch):
        pending = self._scan(monkeypatch, [status_for(
            'DCS',
            target(None, state=WrapperState.ABSENT),
            target('9.9', state=WrapperState.FOREIGN))])
        assert pending == []

    def test_no_bundled_wrapper_offers_nothing(self, monkeypatch):
        assert self._scan(monkeypatch,
                          [status_for('DCS', target('0.9.0.0'))],
                          bundled=None) == []


class TestDialog:
    def _pending(self):
        return [(status_for('DCS', target('0.9.0.0')),
                 [target('0.9.0.0')]),
                (status_for('IL2', target(None)), [target(None)])]

    def test_every_sim_is_listed_and_preselected(self, app, monkeypatch):
        monkeypatch.setattr(mod, 'bundled_version', lambda: '0.9.1.0')
        dialog = mod.TapUpdateDialog(self._pending())
        labels = [box.text() for box, _ in dialog._boxes]
        assert any('DCS' in t and '0.9.0.0' in t for t in labels)
        assert any('IL-2' in t and 'unversioned' in t for t in labels)
        assert len(dialog.selected()) == 2

    def test_deselecting_a_sim_excludes_it(self, app, monkeypatch):
        monkeypatch.setattr(mod, 'bundled_version', lambda: '0.9.1.0')
        dialog = mod.TapUpdateDialog(self._pending())
        dialog._boxes[0][0].setChecked(False)
        assert [s.sim.key for s in dialog.selected()] == ['IL2']


class TestOffer:
    def test_accepting_updates_the_selected_sims(self, app, monkeypatch):
        monkeypatch.setattr(mod, 'bundled_version', lambda: '0.9.1.0')
        monkeypatch.setattr(mod, 'all_status', lambda s: [
            status_for('DCS', target('0.9.0.0'))])
        installed = []

        class Outcome:
            ok, action, directory, detail = True, 'updated', 'x', ''
        monkeypatch.setattr(mod, 'install',
                            lambda status: installed.append(status.sim.key)
                            or [Outcome()])
        monkeypatch.setattr(mod.TapUpdateDialog, 'exec',
                            lambda self: QtWidgets.QDialog.DialogCode.Accepted)
        assert mod.offer_wrapper_updates(
            settings=Settings({'enableTapDCS': True}))
        assert installed == ['DCS']

    def test_declining_updates_nothing(self, app, monkeypatch):
        monkeypatch.setattr(mod, 'bundled_version', lambda: '0.9.1.0')
        monkeypatch.setattr(mod, 'all_status', lambda s: [
            status_for('DCS', target('0.9.0.0'))])
        monkeypatch.setattr(mod, 'install', lambda status: pytest.fail(
            'declined offer must not install'))
        monkeypatch.setattr(mod.TapUpdateDialog, 'exec',
                            lambda self: QtWidgets.QDialog.DialogCode.Rejected)
        assert not mod.offer_wrapper_updates(
            settings=Settings({'enableTapDCS': True}))

    def test_nothing_pending_shows_no_dialog(self, app, monkeypatch):
        monkeypatch.setattr(mod, 'bundled_version', lambda: '0.9.1.0')
        monkeypatch.setattr(mod, 'all_status', lambda s: [
            status_for('DCS', target('0.9.1.0'))])
        monkeypatch.setattr(mod.TapUpdateDialog, 'exec', lambda self:
                            pytest.fail('no dialog when nothing is pending'))
        assert not mod.offer_wrapper_updates(settings=Settings({
            'enableTapDCS': True}))

    def test_failures_are_reported(self, app, monkeypatch):
        monkeypatch.setattr(mod, 'bundled_version', lambda: '0.9.1.0')
        monkeypatch.setattr(mod, 'all_status', lambda s: [
            status_for('DCS', target('0.9.0.0'))])

        class Outcome:
            ok, action, directory = False, 'failed', r'C:\Games\X\bin'
            detail = 'the file is in use - close the game and try again'
        monkeypatch.setattr(mod, 'install', lambda status: [Outcome()])
        monkeypatch.setattr(mod.TapUpdateDialog, 'exec',
                            lambda self: QtWidgets.QDialog.DialogCode.Accepted)
        warnings = []
        monkeypatch.setattr(QtWidgets.QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: warnings.append(a)))
        assert not mod.offer_wrapper_updates(
            settings=Settings({'enableTapDCS': True}))
        assert warnings and 'file is in use' in warnings[0][2]
