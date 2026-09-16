"""A child instance no longer carries a settings page of its own.

Every device is configured from the master's dialog, so the child's route
to one is gone.  What a child keeps is what only it can do: reach its own
config and log directory, reset its own window, and quit itself - which is
all "Quit" does from a child, hence the name it now carries.

The IPC message runs both ways and only one direction is retired: a child
whose device is unassigned still asks the master to open settings, and that
ask matters more than before, because the master's dialog is now where that
child's settings live.

The menu bar is built inline in MainWindow.__init__, so these read the
source rather than a live window: what is being asserted is the wiring, and
the guard a statement sits under is structure, not text.
"""
import re
from pathlib import Path

import pytest

from telemffb.utils import device_display_name, device_pid_key

pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parent.parent


def source(name):
    return (ROOT / name).read_text(encoding='utf-8')


def guards_of(text, needle):
    """The `if` conditions enclosing the line that contains `needle`.

    Walks outward by indentation, so it reports the statement's actual
    guards rather than whatever happens to be written near it.
    """
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if needle in line)
    indent = len(lines[index]) - len(lines[index].lstrip())
    guards = []
    for line in reversed(lines[:index]):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        here = len(line) - len(line.lstrip())
        if here < indent:
            indent = here
            if stripped.startswith(('if ', 'elif ')):
                guards.append(stripped.rstrip(':'))
            if stripped.startswith('def '):
                break
    return guards


class TestDeviceNaming:
    def test_the_trim_wheel_is_two_words(self):
        """``capitalize()`` writes "Trimwheel", which appears nowhere else in
        the app."""
        assert device_display_name('trimwheel') == 'Trim Wheel'

    @pytest.mark.parametrize("role,expected", [
        ('joystick', 'Joystick'), ('pedals', 'Pedals'),
        ('collective', 'Collective'),
    ])
    def test_the_others_read_as_written(self, role, expected):
        assert device_display_name(role) == expected

    @pytest.mark.parametrize("role,key", [
        ('joystick', 'pidJoystick'), ('pedals', 'pidPedals'),
        ('collective', 'pidCollective'), ('trimwheel', 'pidTrimWheel'),
    ])
    def test_product_id_keys_match_what_is_stored(self, role, key):
        assert device_pid_key(role) == key

    def test_every_role_maps_to_a_key_the_dialog_actually_writes(self):
        """Deriving these any other way reads nothing for the trim wheel,
        silently - its profiles would validate against no product ID."""
        written = set(re.findall(r"'(pid[A-Za-z]+)':",
                                 source('telemffb/SystemSettingsDialog.py')))
        for role in ('joystick', 'pedals', 'collective', 'trimwheel'):
            assert device_pid_key(role) in written, role


class TestTheChildsMenu:
    @pytest.fixture(scope="class")
    def main_window(self):
        return source('telemffb/MainWindow.py')

    def test_system_settings_is_the_masters(self, main_window):
        guards = guards_of(main_window, "system_settings_action = QAction")
        assert any('G.master_instance' in g for g in guards), guards

    @pytest.mark.parametrize("item", [
        "cfg_log_folder_action = QAction('Open Config/Log Directory'",
        "reset_geometry = QAction('Reset Window Size/Position'",
        "exit_app_action = QAction(",
    ])
    def test_what_only_a_child_can_do_for_itself_stays(self, main_window, item):
        assert not guards_of(main_window, item), f"{item} became conditional"

    def test_quit_says_what_it_quits(self, main_window):
        """From a child it ends that instance; only from the master does it
        take the application down."""
        assert "Quit the {utils.device_display_name(G.device_type)} Instance" \
            in main_window


class TestTheIpcMessage:
    def test_nothing_broadcasts_settings_to_the_children(self):
        main_window = source('telemffb/MainWindow.py')
        assert 'send_broadcast_message("SHOW SETTINGS")' not in main_window
        assert 'def show_child_settings' not in main_window

    def test_a_child_still_asks_the_master_to_open_settings(self):
        """The other direction of the same message, and the reason the
        handler stays."""
        assert 'send_message("SHOW SETTINGS")' in source('main.py')

    def test_only_the_master_answers_that_ask(self):
        guards = guards_of(source('main.py'), "show_settings_signal.connect")
        assert any('G.master_instance' in g for g in guards), guards


class TestDialogNoLongerBendsForChildren:
    def test_no_child_mode_stripping_remains(self):
        assert 'G.child_instance' not in source('telemffb/SystemSettingsDialog.py')
