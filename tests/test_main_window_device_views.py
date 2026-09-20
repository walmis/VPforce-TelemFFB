"""MainWindow's device views, against the real window.

Nothing else in the suite builds a MainWindow, and the bugs these pin could
not be reproduced without one: stand-in layouts collapsed happily on the Hide
tab while the real window stayed stuck at full height; every child instance
put up a floating strip of its own, because they all read one shared setting;
showing the Active Devices frame in a window at its minimum width counted the
frame's width twice.

The window is built with the application's globals stubbed just far enough
for ``MainWindow.__init__`` and ``setup_master_instance`` to run: no device,
no sims, no IPC. It is kept to this one file so that, should building the
real window prove fragile somewhere, it can be deselected on its own.
"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

import telemffb.globals as G
from telemffb.ExceptionTracker import ExceptionTracker
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.MainWindow import (DEVICE_VIEW_FLOATING, DEVICE_VIEW_FRAME,
                                 DEVICE_VIEW_HEADER, DEVICE_VIEW_MENUBAR,
                                 DEVICE_VIEW_ROW, MainWindow)
from telemffb.state.app_state import AppState

pytestmark = pytest.mark.integration

HIDE_TAB, SETTINGS_TAB, MONITOR_TAB = 2, 1, 0


class _Settings(dict):
    """G.system_settings: a .get(key, default) / .setValue(key, value) store."""

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def setValue(self, key, value):
        self[key] = value


class _SimListeners(QObject):
    simStarted = pyqtSignal(object)
    simStopped = pyqtSignal(object)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump():
    QApplication.processEvents()
    QApplication.processEvents()


@pytest.fixture
def build_window(qapp, monkeypatch):
    """``build_window(master=True, children=(...), **settings)`` -> a shown
    MainWindow. Every window built is closed when the test ends."""
    built = []

    def build(master=True, children=('pedals', 'collective'), **settings):
        device = 'joystick' if master else 'pedals'
        state = AppState()
        state.set_own_device_type(device)
        state.set_master(master)
        telem_manager = MagicMock()
        telem_manager.simconnect = None
        for name, value in {
            'system_settings': _Settings(settings),
            'master_instance': master,
            'child_instance': not master,
            'device_type': device,
            'launched_instances': {name: object() for name in children} if master else {},
            'app_state': state,
            'exception_tracker': ExceptionTracker(),
            'telem_manager': telem_manager,
            'ipc_instance': MagicMock(),
            'sim_listeners': _SimListeners(),
            'settings_mgr': SimpleNamespace(current_sim='nothing', current_class='', current_pattern='',
                                            current_aircraft_name='', offline_mode=False, offline_scope=None),
            'useDarkMode': True,
            # Set by the window itself as it is built; listed so that they are
            # put back afterwards too, and nothing of this window outlives it
            # in a later test's globals.
            'current_device_config_scope': device,
            'gain_override_dialog': None,
        }.items():
            monkeypatch.setattr(G, name, value, raising=False)
        # No device, whatever an earlier test in this process left behind:
        # the window asks the device for its gains as it is built.
        monkeypatch.setattr(HapticEffect, 'device', None, raising=False)
        window = MainWindow()
        monkeypatch.setattr(G, 'main_window', window, raising=False)
        if master:
            window.setup_master_instance()
        window.show()
        window.tab_widget.setCurrentIndex(SETTINGS_TAB)
        _pump()
        built.append(window)
        return window

    yield build
    for window in built:
        strip = window.device_strip
        strip.hide()
        if strip.parent() is None:
            strip.deleteLater()  # floating free of the window, so not its to delete
        window.hide()
        window.close()
        window.deleteLater()
    # Deleted here and not whenever the garbage collector gets to it: a
    # window being torn down halfway through the next test sends events to
    # widgets whose globals have moved on, and takes the process with it.
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _pump()


def _showing(window):
    """Which device displays are on screen, by name."""
    if window.device_groupbox.isVisible():
        return {'frame'}
    strip = window.device_strip
    shown = set()
    if strip.isVisible():
        slots = {
            'status box': window.header_panel.device_slot,
            'menubar': window.corner_device_slot,
            'settings header': window.settings_header_bar.device_slot,
            'monitor header': window.monitor_panel.header_bar.device_slot,
        }
        shown.add(next((name for name, slot in slots.items() if slot.holds(strip)), 'floating'))
    if window.monitor_device_strip.isVisible():
        shown.add('detached monitor')
    return shown


class TestWhichViewIsInEffect:
    """``_device_view``: the saved choice, read tolerantly."""

    def test_the_saved_view_is_used(self, build_window):
        assert build_window(deviceView=DEVICE_VIEW_MENUBAR)._device_view() == DEVICE_VIEW_MENUBAR

    def test_the_older_setting_decides_until_a_view_has_been_picked(self, build_window):
        """``showDevicesFrame`` is from before there was a choice of more
        than two, and is all an existing configuration has."""
        assert build_window(showDevicesFrame=True)._device_view() == DEVICE_VIEW_FRAME
        assert build_window(showDevicesFrame=False)._device_view() == DEVICE_VIEW_ROW

    def test_a_value_it_does_not_know_falls_back_the_same_way(self, build_window):
        window = build_window(deviceView='somewhere else', showDevicesFrame=False)
        assert window._device_view() == DEVICE_VIEW_ROW

    def test_a_lone_device_has_no_side_panel(self, build_window):
        """The frame needs more than one device to be worth its width: the
        view it would have alternated with stands in."""
        window = build_window(children=(), deviceView=DEVICE_VIEW_FRAME, deviceViewIntegrated=DEVICE_VIEW_MENUBAR)
        assert window._device_view() == DEVICE_VIEW_MENUBAR

    def test_picking_a_view_saves_it_for_new_and_old_builds_alike(self, build_window):
        window = build_window(deviceView=DEVICE_VIEW_ROW)
        window._set_device_view(DEVICE_VIEW_HEADER)
        assert G.system_settings.get('deviceView') == DEVICE_VIEW_HEADER
        assert G.system_settings.get('deviceViewIntegrated') == DEVICE_VIEW_HEADER
        assert G.system_settings.get('showDevicesFrame') is False
        window._set_device_view(DEVICE_VIEW_FRAME)
        assert G.system_settings.get('deviceView') == DEVICE_VIEW_FRAME
        assert G.system_settings.get('showDevicesFrame') is True
        assert G.system_settings.get('deviceViewIntegrated') == DEVICE_VIEW_HEADER  # still the one to go back to

    def test_the_view_toggle_buttons_alternate_with_the_last_integrated_view(self, build_window):
        window = build_window(deviceView=DEVICE_VIEW_MENUBAR)
        window._set_devices_frame_preference(True)
        assert window._device_view() == DEVICE_VIEW_FRAME
        window._set_devices_frame_preference(False)
        assert window._device_view() == DEVICE_VIEW_MENUBAR


class TestAChildInstance:
    """Every instance reads the same system settings, and the view is the
    master's to act on."""

    def test_never_floats_its_strip(self, build_window):
        window = build_window(master=False, deviceView=DEVICE_VIEW_FLOATING)
        assert not window.device_strip.floating()
        assert window._device_view() == DEVICE_VIEW_ROW
        assert _showing(window) == {'status box'}

    def test_is_the_only_window_it_shows(self, build_window, qapp):
        before = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
        window = build_window(master=False, deviceView=DEVICE_VIEW_FLOATING)
        new = [w for w in QApplication.topLevelWidgets() if w.isVisible() and id(w) not in before]
        assert new == [window]


class TestWhichDisplayShows:
    @pytest.mark.parametrize("view, expected", [
        (DEVICE_VIEW_FRAME, {'frame'}),
        (DEVICE_VIEW_MENUBAR, {'menubar'}),
        (DEVICE_VIEW_ROW, {'status box'}),
        (DEVICE_VIEW_HEADER, {'settings header'}),
        (DEVICE_VIEW_FLOATING, {'floating'}),
    ])
    def test_each_view_shows_its_own_display_and_no_other(self, build_window, view, expected):
        window = build_window(deviceView=DEVICE_VIEW_ROW)
        window.resize(1100, 700)  # room for the menu bar view beside the logo
        window._set_device_view(view)
        _pump()
        assert _showing(window) == expected

    def test_the_header_view_is_on_the_monitor_page_too(self, build_window):
        window = build_window(deviceView=DEVICE_VIEW_HEADER)
        window.tab_widget.setCurrentIndex(MONITOR_TAB)
        _pump()
        assert _showing(window) == {'monitor header'}

    @pytest.mark.parametrize("view", [DEVICE_VIEW_FRAME, DEVICE_VIEW_MENUBAR, DEVICE_VIEW_ROW, DEVICE_VIEW_HEADER])
    def test_the_hide_tab_shows_none_of_the_windows_own_displays(self, build_window, view):
        window = build_window(deviceView=view)
        window.tab_widget.setCurrentIndex(HIDE_TAB)
        _pump()
        assert _showing(window) == set()

    def test_a_free_floating_strip_outlives_the_hide_tab(self, build_window):
        """It is a window of its own; confined to this one, it goes with the
        rest."""
        window = build_window(deviceView=DEVICE_VIEW_FLOATING, deviceStripConfined=False)
        window.tab_widget.setCurrentIndex(HIDE_TAB)
        _pump()
        assert _showing(window) == {'floating'}
        window._set_device_strip_confined(True)
        _pump()
        assert _showing(window) == set()

    @pytest.mark.parametrize("view", [DEVICE_VIEW_FRAME, DEVICE_VIEW_ROW, DEVICE_VIEW_FLOATING])
    def test_a_detached_monitor_window_always_has_its_own_row(self, build_window, view):
        """None of the main window's displays can be seen from it."""
        window = build_window(deviceView=view)
        window.tab_widget.setCurrentIndex(MONITOR_TAB)
        window.detach_tab(MONITOR_TAB)
        _pump()
        try:
            assert window.monitor_device_strip.isVisible()
            # No glyph: it would change a different window than the one it
            # is in, and no grip: it is not this window's to float.
            assert window.monitor_device_strip.toggle is None
        finally:
            window.reattach_tab('Monitor')
            _pump()

    def test_a_lone_device_gets_no_view_toggle_buttons(self, build_window):
        """There is no side panel for them to switch to."""
        window = build_window(children=(), deviceView=DEVICE_VIEW_ROW)
        assert _showing(window) == {'status box'}
        assert not window.device_strip.toggle.isVisible()


class TestTheHideTabCollapsesTheWindow:
    @pytest.mark.parametrize("view", [DEVICE_VIEW_FRAME, DEVICE_VIEW_ROW])
    def test_to_its_minimum_height(self, build_window, view):
        """Hiding the frame and resizing in the same step stopped at the
        height the window had needed *with* the frame."""
        window = build_window(deviceView=view)
        window.resize(1000, 700)
        _pump()
        window.tab_widget.setCurrentIndex(HIDE_TAB)
        _pump()
        assert window.height() == window.minimumSizeHint().height()
        assert window.height() < 400

    def test_the_same_height_whichever_view_it_came_from(self, build_window):
        heights = set()
        for view in (DEVICE_VIEW_FRAME, DEVICE_VIEW_ROW, DEVICE_VIEW_MENUBAR):
            window = build_window(deviceView=view)
            window.tab_widget.setCurrentIndex(HIDE_TAB)
            _pump()
            heights.add(window.height())
        assert len(heights) == 1


class TestTheWindowsWidthFollowsTheFrame:
    @pytest.mark.parametrize("start_width", [1000, 0])
    def test_showing_then_hiding_the_frame_is_a_round_trip(self, build_window, start_width):
        """From a roomy window and from one at its minimum width - where
        the layout widens the window for the frame on its own account, and
        that width was then added a second time."""
        window = build_window(deviceView=DEVICE_VIEW_ROW)
        window.resize(start_width, 700)
        _pump()
        before, column = window.width(), window.tab_widget.width()

        window._set_device_view(DEVICE_VIEW_FRAME)
        _pump()
        frame_cost = window.device_groupbox.width() + window._content_hbox.spacing()
        assert window.width() == before + frame_cost
        assert window.tab_widget.width() == column  # the column beside it keeps its width

        window._set_device_view(DEVICE_VIEW_ROW)
        _pump()
        assert window.width() == before
        assert window.tab_widget.width() == column
