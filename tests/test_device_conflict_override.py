"""The device-conflict dialog's Override: the device moves to the slot the
user picked and leaves the one it held.

What is on screen after an Override and what gets saved must be the same
thing.  Save derives the pid keys from the selectors and the devpaths from
the staged writes; an Override that updates one without the other leaves a
store whose pid keys name different hardware than its devpaths, and the
next start - which opens by pid when the devpath is empty - drives the
wrong device.
"""
import os
import random

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import telemffb.globals as G
import tests.test_tap_workflows as tw
from tests.test_tap_workflows import World, device

pytestmark = [pytest.mark.unit]

H = rb"\\?\HID#VID_FFFF&PID_%s&MI_00#f&%s&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"
COLL = device(0x2053, "Rhino FFB Collective", H % (b"2053", b"aa"))
TRIM = device(0x2052, "Rhino FFB Trim", H % (b"2052", b"bb"))
RUDDER = device(0x2055, "Rhino FFB Rudder", H % (b"2055", b"cc"))
STICK = device(0x2054, "Rhino FFB Joystick", H % (b"2054", b"dd"))
#: HID enumeration order; row i of a selector is ENUMERATED[i-1]
ENUMERATED = [COLL, TRIM, RUDDER, STICK]

ROLES = ('joystick', 'pedals', 'collective', 'trimwheel')
HELD = {'joystick': STICK, 'pedals': RUDDER, 'collective': COLL,
        'trimwheel': TRIM}
COMBO = {'joystick': 'cb_select_j', 'pedals': 'cb_select_p',
         'collective': 'cb_select_c', 'trimwheel': 'cb_select_t'}
PID_KEY = {'joystick': 'pidJoystick', 'pedals': 'pidPedals',
           'collective': 'pidCollective', 'trimwheel': 'pidTrimWheel'}


def stored_state():
    state = {'masterInstance': 1, 'themeId': 2,
             'autolaunchPedals': True, 'autolaunchCollective': True,
             'autolaunchTrimWheel': True}
    for role, dev in HELD.items():
        state[f'devpath_{role}'] = dev.path.decode()
        state[f'devids_{role}'] = f'FFFF:{dev.product_id:04X}'
        state[f'devident_{role}'] = dev.ident
        state[PID_KEY[role]] = format(dev.product_id, 'x')
    return state


def row_of(dev):
    return ENUMERATED.index(dev) + 1


class FakeIPC:
    def __init__(self):
        self.sent = []

    def child_device_connected(self, role):
        return True

    def send_broadcast_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def world(app, tmp_path, monkeypatch):
    monkeypatch.setattr(tw, 'ENUMERATED', list(ENUMERATED))
    w = World(tmp_path, monkeypatch, random.Random(0), settings=stored_state())
    w.ipc = FakeIPC()
    monkeypatch.setattr(G, 'ipc_instance', w.ipc, raising=False)
    monkeypatch.setattr(G, 'launched_instances',
                        {'pedals': 1, 'collective': 1, 'trimwheel': 1},
                        raising=False)
    return w


def answer_conflict(monkeypatch, button):
    """The conflict box is a QMessageBox built inline; its buttons are
    added Override first, Cancel second."""
    index = {'Override': 0, 'Cancel': 1}[button]
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', lambda self: 0)
    monkeypatch.setattr(QtWidgets.QMessageBox, 'clickedButton',
                        lambda self: self.buttons()[index])


def pid_of(dev):
    return format(dev.product_id, 'x')


def assert_store_is_self_consistent(settings):
    """A pid key names the device its devpath names, or nothing."""
    by_path = {d.path.decode(): d for d in ENUMERATED}
    for role in ROLES:
        devpath = str(settings.get(f'devpath_{role}') or '')
        pid = str(settings.get(PID_KEY[role]) or '')
        if not devpath:
            assert pid == '', f'{role}: no device but pid {pid!r}'
        else:
            assert pid == pid_of(by_path[devpath]), \
                f'{role}: devpath names {by_path[devpath].ident}, pid is {pid!r}'


PAIRINGS = [(taker, holder) for taker in ROLES for holder in ROLES
            if taker != holder]


@pytest.mark.parametrize('taker,holder', PAIRINGS,
                         ids=[f'{t}-takes-{h}' for t, h in PAIRINGS])
class TestOverride:
    def test_the_taken_device_is_staged_for_its_new_slot(
            self, world, monkeypatch, taker, holder):
        answer_conflict(monkeypatch, 'Override')
        dev = HELD[holder]
        getattr(world.dialog, COMBO[taker]).setCurrentIndex(row_of(dev))

        pending = world.dialog._pending_devpaths
        assert pending.get(f'devpath_{taker}') == dev.path.decode()
        assert pending.get(f'devpath_{holder}') == ''
        assert world.dialog.selected_device(taker) is dev
        assert world.dialog.selected_device(holder) is None

    def test_save_writes_what_the_selectors_show(
            self, world, monkeypatch, taker, holder):
        answer_conflict(monkeypatch, 'Override')
        dev = HELD[holder]
        getattr(world.dialog, COMBO[taker]).setCurrentIndex(row_of(dev))

        saved = world.save()
        if holder == 'joystick':
            # the master's slot is now empty and Save refuses; the staged
            # state is still coherent for whatever the user does next
            assert not saved
            assert world.dialog.instance_pid(taker) == pid_of(dev)
            assert world.dialog.instance_pid(holder) == ''
            return
        assert saved
        s = world.settings
        assert s.get(f'devpath_{taker}') == dev.path.decode()
        assert str(s.get(PID_KEY[taker])) == pid_of(dev)
        assert s.get(f'devpath_{holder}') == ''
        assert str(s.get(PID_KEY[holder]) or '') == ''
        assert_store_is_self_consistent(s)

    def test_the_change_is_applied_live_and_the_cleared_slot_is_not(
            self, world, monkeypatch, taker, holder):
        answer_conflict(monkeypatch, 'Override')
        getattr(world.dialog, COMBO[taker]).setCurrentIndex(
            row_of(HELD[holder]))
        if holder == 'joystick':
            return
        assert world.save()
        if taker == 'joystick':
            assert world.device_switches == 1
        else:
            assert f'REACQUIRE:{taker}' in world.ipc.sent
        assert f'REACQUIRE:{holder}' not in world.ipc.sent


class TestCancel:
    def test_cancel_stages_nothing_and_reverts_the_selector(
            self, world, monkeypatch):
        answer_conflict(monkeypatch, 'Cancel')
        world.dialog.cb_select_j.setCurrentIndex(row_of(RUDDER))
        assert world.dialog.selected_device('joystick') is STICK
        assert world.dialog.selected_device('pedals') is RUDDER
        assert not any(k.startswith('devpath_')
                       for k in world.dialog._pending_devpaths)
        assert world.save()
        assert_store_is_self_consistent(world.settings)


class TestPidFollowsTheStore:
    """instance_pid answers for the device the settings will name, never
    for a selector that got out of step with them."""

    def test_a_selector_the_store_does_not_agree_with_is_ignored(self, world):
        # put a selector out of step by hand: what the old Override did
        cb = world.dialog.cb_select_p
        cb.blockSignals(True)
        cb.setCurrentIndex(row_of(STICK))
        cb.blockSignals(False)
        assert world.dialog.selected_device('pedals') is STICK
        assert world.dialog.instance_pid('pedals') == pid_of(RUDDER)

    def test_a_cleared_slot_has_no_pid(self, world):
        world.dialog.cb_select_c.setCurrentIndex(0)
        assert world.dialog.instance_pid('collective') == ''
        assert world.save()
        assert str(world.settings.get('pidCollective') or '') == ''

    def test_a_stored_device_that_is_unplugged_keeps_its_pid(
            self, tmp_path, monkeypatch, app):
        monkeypatch.setattr(tw, 'ENUMERATED', [COLL, TRIM, STICK])   # no Rudder
        w = World(tmp_path, monkeypatch, random.Random(0),
                  settings=stored_state())
        assert w.dialog.selected_device('pedals') is None
        assert w.dialog.instance_pid('pedals') == pid_of(RUDDER)

    def test_an_unopposed_pick_is_reported_before_it_is_saved(self, world):
        world.dialog.cb_select_c.setCurrentIndex(0)
        world.dialog.cb_select_t.setCurrentIndex(row_of(COLL))
        assert world.dialog.instance_pid('trimwheel') == pid_of(COLL)
