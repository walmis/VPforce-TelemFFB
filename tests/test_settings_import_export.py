"""The dialog's File menu: export the settings store to JSON, import a
file back into the form, and Reset to Defaults relocated off the bottom
row.

Import commits nothing - it populates the widgets and Save applies the
result through the dialog's usual guards (validation, the live device
switch, the restart notice).  Driven through the workflow harness's
World.
"""
import json
import os
import random

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from tests.test_tap_workflows import PEDALS, RHINO, WARTHOG, World

pytestmark = [pytest.mark.unit]


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


SETTLED = {'pidJoystick': '2054', 'pidPedals': '2052', 'pidCollective': '',
           'pidTrimWheel': '', 'masterInstance': 1, 'themeId': 2}


def restart_notices(world):
    return [text for title, text in world.policy.asked
            if title == "Restart Required"]


def pick_save(monkeypatch, path):
    monkeypatch.setattr(
        QtWidgets.QFileDialog, 'getSaveFileName',
        staticmethod(lambda *a, **k: (str(path), 'TelemFFB settings (*.json)')))


def pick_open(monkeypatch, path):
    monkeypatch.setattr(
        QtWidgets.QFileDialog, 'getOpenFileName',
        staticmethod(lambda *a, **k: (str(path), 'TelemFFB settings (*.json)')))


def write_export(path, flat):
    path.write_text(json.dumps({'application': 'TelemFFB',
                                'settings': flat}), encoding='utf-8')


class TestMenu:
    def test_the_menu_replaces_the_reset_button(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        dlg = world.dialog
        assert not hasattr(dlg, 'resetButton')
        texts = [a.text() for a in dlg.file_menu.actions() if a.text()]
        assert texts == ['Import Settings...', 'Export Settings...',
                         'Reset to Defaults']

    def test_reset_from_the_menu_loads_defaults(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0),
                      settings=dict(SETTLED, enableMSFS=True))
        assert world.dialog.enableMSFS.isChecked()
        world.dialog.action_reset.trigger()
        assert not world.dialog.enableMSFS.isChecked()
        assert world.settings.get('enableMSFS') is True   # nothing written


class TestExport:
    def test_export_writes_the_whole_store(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        out = tmp_path / 'backup.json'
        pick_save(monkeypatch, out)
        world.dialog.action_export.trigger()
        data = json.loads(out.read_text(encoding='utf-8'))
        assert data['application'] == 'TelemFFB'
        assert data['settings']['devpath_joystick'] == RHINO.path.decode()
        assert data['settings']['masterInstance'] == 1
        assert any(title == 'Export Settings'
                   for title, _ in world.policy.asked)

    def test_export_leaves_out_binary_state(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        world.settings['windowLocation'] = b'\x00\x01'   # QByteArray-ish
        out = tmp_path / 'backup.json'
        pick_save(monkeypatch, out)
        world.dialog.action_export.trigger()
        data = json.loads(out.read_text(encoding='utf-8'))
        assert 'windowLocation' not in data['settings']


class TestImport:
    def imported(self, tmp_path, monkeypatch, flat, seed=0, settings=SETTLED):
        world = World(tmp_path, monkeypatch, random.Random(seed),
                      settings=settings)
        source = tmp_path / 'restore.json'
        write_export(source, flat)
        pick_open(monkeypatch, source)
        world.dialog.action_import.trigger()
        return world

    def test_import_populates_the_form_but_writes_nothing(
            self, app, tmp_path, monkeypatch):
        world = self.imported(tmp_path, monkeypatch, {
            'devpath_joystick': WARTHOG.path.decode(),
            'devpath_pedals': PEDALS.path.decode(),
            'masterInstance': 1, 'themeId': 1,
            'autolaunchMaster': True, 'autolaunchPedals': True,
            'enableMSFS': True,
        })
        dlg = world.dialog
        assert dlg.themeButtonGroup.checkedId() == 1
        assert dlg.cb_al_enable.isChecked()
        assert dlg.cb_al_enable_p.isChecked()
        assert dlg.enableMSFS.isChecked()
        assert dlg.selected_device('joystick').path == WARTHOG.path
        # the store is untouched until Save
        assert world.settings.get('themeId') == 2
        assert world.settings.get('devpath_joystick') == RHINO.path.decode()

    def test_saving_an_import_applies_it_through_the_usual_path(
            self, app, tmp_path, monkeypatch):
        world = self.imported(tmp_path, monkeypatch, {
            'devpath_joystick': WARTHOG.path.decode(),
            'devpath_pedals': PEDALS.path.decode(),
            'masterInstance': 1, 'themeId': 1,
        })
        assert world.save()
        assert world.settings.get('themeId') == 1
        assert world.settings.get('devpath_joystick') == WARTHOG.path.decode()
        assert world.device_switches == 1     # the changed device went live

    def test_an_imported_master_change_asks_for_a_restart(
            self, app, tmp_path, monkeypatch):
        world = self.imported(tmp_path, monkeypatch, {
            'devpath_joystick': RHINO.path.decode(),
            'devpath_pedals': PEDALS.path.decode(),
            'masterInstance': 2, 'themeId': 2,
        })
        assert world.save()
        assert any('master device' in text for text in restart_notices(world))

    def test_an_import_that_autolaunches_nothing_refuses_to_save(
            self, app, tmp_path, monkeypatch):
        world = self.imported(tmp_path, monkeypatch, {
            'devpath_joystick': RHINO.path.decode(),
            'masterInstance': 1,
            'autolaunchMaster': True, 'autolaunchCollective': True,
        })
        assert not world.save()
        assert any('Collective' in text for _t, text in world.policy.warned)

    def test_an_unplugged_device_survives_the_round_trip(
            self, app, tmp_path, monkeypatch):
        """A backup restored on a machine where the hardware is not
        currently connected still saves; the stored-but-unplugged
        machinery takes it from there."""
        foreign = '\\\\?\\hid#not_plugged_in_right_now'
        world = self.imported(tmp_path, monkeypatch, {
            'devpath_joystick': RHINO.path.decode(),
            'devpath_pedals': foreign, 'pidPedals': '2052',
            'masterInstance': 1,
            'autolaunchMaster': True, 'autolaunchPedals': True,
        })
        assert world.dialog.selected_device('pedals') is None
        assert world.save()
        assert world.settings.get('devpath_pedals') == foreign

    def test_a_devpath_the_file_lacks_clears_that_slot(
            self, app, tmp_path, monkeypatch):
        world = self.imported(tmp_path, monkeypatch, {
            'devpath_joystick': RHINO.path.decode(),
            'masterInstance': 1,
        })
        assert world.dialog.selected_device('pedals') is None
        assert world.save()
        assert world.settings.get('devpath_pedals') == ''

    def test_a_bad_file_warns_and_changes_nothing(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        source = tmp_path / 'restore.json'
        source.write_text('this is not json', encoding='utf-8')
        pick_open(monkeypatch, source)
        world.dialog.action_import.trigger()
        assert any(title == 'Import Settings'
                   for title, _ in world.policy.warned)
        assert world.dialog.themeButtonGroup.checkedId() == 2
        assert world.dialog.selected_device('joystick').path == RHINO.path
