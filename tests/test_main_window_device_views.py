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

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import telemffb.globals as G
from telemffb.ExceptionTracker import ExceptionTracker
from telemffb.hw.effect_levels import LEVEL_NAMES
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.MainWindow import (DEVICE_VIEW_FLOATING, DEVICE_VIEW_FRAME,
                                 DEVICE_VIEW_HEADER, DEVICE_VIEW_MENUBAR,
                                 DEVICE_VIEW_RAIL, DEVICE_VIEW_ROW,
                                 DEVICE_VIEW_BOTTOM, MainWindow)
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
            'rail': window.rail_device_slot,
            'bottom': window.bottom_device_slot,
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

    def test_a_lone_device_has_the_side_panel_it_picked(self, build_window):
        """The views are the same however many devices there are."""
        window = build_window(children=(), deviceView=DEVICE_VIEW_FRAME, deviceViewIntegrated=DEVICE_VIEW_MENUBAR)
        assert window._device_view() == DEVICE_VIEW_FRAME
        assert _showing(window) == {'frame'}

    def test_everyone_starts_on_the_frame(self, build_window):
        """One device or several: it is what the older window had, and it is
        never a panel for one icon - all four roles are always on it, the
        ones with no instance greyed out."""
        window = build_window(children=())
        assert window._device_view() == DEVICE_VIEW_FRAME
        assert window.device_panel.get_device_names() == ['joystick', 'pedals', 'collective', 'trimwheel']
        assert build_window()._device_view() == DEVICE_VIEW_FRAME

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
        (DEVICE_VIEW_RAIL, {'rail'}),
        (DEVICE_VIEW_MENUBAR, {'menubar'}),
        (DEVICE_VIEW_ROW, {'status box'}),
        (DEVICE_VIEW_HEADER, {'settings header'}),
        (DEVICE_VIEW_BOTTOM, {'bottom'}),
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

    @pytest.mark.parametrize("view", [DEVICE_VIEW_FRAME, DEVICE_VIEW_RAIL, DEVICE_VIEW_MENUBAR,
                                      DEVICE_VIEW_ROW, DEVICE_VIEW_HEADER, DEVICE_VIEW_BOTTOM])
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

    def test_a_lone_device_gets_the_same_glyph_doing_the_same_thing(self, build_window):
        """It used to be hidden, there being no frame for a lone device -
        which left nothing on screen to say the devices could be moved, and
        nothing to drag the strip off by."""
        window = build_window(children=(), deviceView=DEVICE_VIEW_HEADER)
        assert window.device_strip.toggle.isVisible()

        window.device_strip.toggle.click()
        _pump()
        assert _showing(window) == {'frame'}

        window.device_frame_toggle.click()
        _pump()
        assert _showing(window) == {'settings header'}

    def test_a_child_instance_gets_no_glyph(self, build_window):
        """Its view is the master's to choose."""
        window = build_window(master=False)
        assert not window.device_strip.toggle.isVisible()


class TestTheHideTabCollapsesTheWindow:
    @pytest.mark.parametrize("view", [DEVICE_VIEW_FRAME, DEVICE_VIEW_RAIL, DEVICE_VIEW_ROW, DEVICE_VIEW_BOTTOM])
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


class TestTheCompactSidePanel:
    """The strip stood on its end down the left of the window."""

    def test_the_strip_stands_on_end_there_and_lies_down_anywhere_else(self, build_window):
        """One strip moves between the slots, so which way up it is has to
        be re-decided at every move - left alone, a strip that had been in
        the side panel would arrive in the menu bar as a column."""
        window = build_window(deviceView=DEVICE_VIEW_RAIL)
        strip = window.device_strip
        assert strip.vertical()
        assert strip.height() > strip.width()

        for view in (DEVICE_VIEW_ROW, DEVICE_VIEW_FLOATING):
            window._set_device_view(DEVICE_VIEW_RAIL)
            window._set_device_view(view)
            _pump()
            assert not strip.vertical(), view
            assert strip.width() > strip.height(), view

    def test_it_is_narrower_than_the_frame_it_stands_in_for(self, build_window):
        window = build_window(deviceView=DEVICE_VIEW_FRAME)
        frame = window._side_panel_cost()
        window._set_device_view(DEVICE_VIEW_RAIL)
        _pump()
        assert 0 < window._side_panel_cost() < frame / 2

    @pytest.mark.parametrize("start_width", [1000, 0])
    def test_the_windows_width_follows_it_as_it_does_the_frame(self, build_window, start_width):
        window = build_window(deviceView=DEVICE_VIEW_ROW)
        window.resize(start_width, 700)
        _pump()
        before, column = window.width(), window.tab_widget.width()

        window._set_device_view(DEVICE_VIEW_RAIL)
        _pump()
        assert window.width() == before + window._side_panel_cost()
        assert window.tab_widget.width() == column

        window._set_device_view(DEVICE_VIEW_ROW)
        _pump()
        assert window.width() == before
        assert window.tab_widget.width() == column

    def test_swapping_it_for_the_frame_moves_the_window_by_the_difference(self, build_window):
        """Not by either panel's whole width: one goes as the other comes,
        and the column beside them is owed the same width throughout."""
        window = build_window(deviceView=DEVICE_VIEW_RAIL)
        window.resize(1000, 700)
        _pump()
        width, column, rail = window.width(), window.tab_widget.width(), window._side_panel_cost()

        window._set_device_view(DEVICE_VIEW_FRAME)
        _pump()
        assert window.width() == width - rail + window._side_panel_cost()
        assert window.tab_widget.width() == column

        window._set_device_view(DEVICE_VIEW_RAIL)
        _pump()
        assert window.width() == width
        assert window.tab_widget.width() == column

    def test_the_left_edge_docks_it_and_just_inside_docks_the_frame(self, build_window):
        """Both side panels are reached at the left edge, as bands about as
        wide as what they dock."""
        from PyQt6.QtCore import QPoint
        window = build_window(deviceView=DEVICE_VIEW_FLOATING)
        window.resize(1000, 700)
        window.show()
        _pump()
        y = window.tab_widget.mapTo(window, QPoint(0, 0)).y() + 40

        def zone_at(x):
            zone = window._device_dock_zone_at(window.mapToGlobal(QPoint(x, y)))
            return zone and zone[0]

        assert zone_at(10) == DEVICE_VIEW_RAIL
        assert zone_at(50) == DEVICE_VIEW_FRAME

    def test_a_lone_device_is_offered_every_view(self, build_window):
        window = build_window(deviceView=DEVICE_VIEW_RAIL, children=())
        offered = [key for key, _label in window._device_views()]
        assert window._device_view() == DEVICE_VIEW_RAIL
        # a second window swaps the settings the first one reads, so it is last
        assert offered == [key for key, _label in build_window()._device_views()]

    def test_it_stands_at_full_height_after_the_strip_has_floated(self, build_window):
        """Floating pins the strip to the floating row's size, and docking
        did not unpin it. Every other slot holds a row that size, so nothing
        showed until the strip was stood on end: held to the row's height,
        it drew the top few pixels of its first icon - and only until a
        restart, which builds a strip that has never floated."""
        window = build_window(deviceView=DEVICE_VIEW_FLOATING)
        _pump()
        window._set_device_view(DEVICE_VIEW_RAIL)
        _pump()
        strip = window.device_strip
        assert strip.height() == strip.sizeHint().height()
        assert strip.height() > 150  # four icons, not the 52px of a row


class TestClickingAViewToggleGlyph:
    def test_a_click_made_with_the_mouse_still_moving_is_a_click(self, build_window):
        """The glyph is some 20px across and the drag distance 10, so a
        click pressed before the hand had stopped covered it without leaving
        the glyph - and floated the strip instead of switching the view."""
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        window = build_window(deviceView=DEVICE_VIEW_FRAME, deviceViewIntegrated=DEVICE_VIEW_RAIL)
        window.show()
        _pump()
        glyph = window.device_frame_toggle
        start = QPoint(4, glyph.height() // 2)
        end = start + QPoint(12, 2)
        assert glyph.rect().contains(end)

        QTest.mousePress(glyph, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(glyph, end)
        QTest.mouseRelease(glyph, Qt.MouseButton.LeftButton, pos=end)
        _pump()
        assert window._device_view() == DEVICE_VIEW_RAIL

    def test_dragging_it_off_the_glyph_still_floats_the_strip(self, build_window):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        window = build_window(deviceView=DEVICE_VIEW_FRAME)
        window.show()
        _pump()
        glyph = window.device_frame_toggle
        start = glyph.rect().center()
        QTest.mousePress(glyph, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(glyph, start + QPoint(60, 40))
        _pump()
        assert window._device_view() == DEVICE_VIEW_FLOATING
        QTest.mouseRelease(window.device_strip, Qt.MouseButton.LeftButton)
        _pump()


def _left_of_the_tabs(window, panel) -> bool:
    from PyQt6.QtCore import QPoint
    return panel.mapTo(window, QPoint(0, 0)).x() < window.tab_widget.mapTo(window, QPoint(0, 0)).x()


class TestWhichSideTheSidePanelsAreOn:
    """One setting both side panels follow, rather than a left and a right
    view of each."""

    @pytest.mark.parametrize("view, panel", [(DEVICE_VIEW_FRAME, 'device_groupbox'),
                                             (DEVICE_VIEW_RAIL, 'rail_device_slot')])
    def test_both_panels_follow_it(self, build_window, view, panel):
        window = build_window(deviceView=view)
        window.resize(1000, 700)
        _pump()
        assert _left_of_the_tabs(window, getattr(window, panel))

        window._set_device_side(True)
        _pump()
        assert not _left_of_the_tabs(window, getattr(window, panel))
        assert G.system_settings.get('deviceSideRight') is True

    def test_it_is_read_when_the_window_is_built(self, build_window):
        window = build_window(deviceView=DEVICE_VIEW_RAIL, deviceSideRight=True)
        assert not _left_of_the_tabs(window, window.rail_device_slot)

    def test_the_glyph_swaps_the_panels_where_they_stand(self, build_window):
        """The reason side is a setting: as two more views, a click on the
        glyph in the compact panel on the right could land the devices in a
        full panel last used on the left."""
        window = build_window(deviceView=DEVICE_VIEW_RAIL, deviceSideRight=True)
        window.device_strip.toggle.click()
        _pump()
        assert _showing(window) == {'frame'}
        assert not _left_of_the_tabs(window, window.device_groupbox)

        window.device_frame_toggle.click()
        _pump()
        assert _showing(window) == {'rail'}
        assert not _left_of_the_tabs(window, window.rail_device_slot)

    def test_changing_sides_costs_the_window_nothing(self, build_window):
        window = build_window(deviceView=DEVICE_VIEW_RAIL)
        window.resize(1000, 700)
        _pump()
        width, column = window.width(), window.tab_widget.width()
        window._set_device_side(True)
        _pump()
        assert (window.width(), window.tab_widget.width()) == (width, column)

    def test_a_drop_on_the_right_edge_docks_there_and_moves_the_side(self, build_window):
        from PyQt6.QtCore import QPoint
        window = build_window(deviceView=DEVICE_VIEW_FLOATING)
        window.resize(1000, 700)
        window.show()
        _pump()
        y = window.tab_widget.mapTo(window, QPoint(0, 0)).y() + 200  # clear of the page header

        def zone_at(x):
            zone = window._device_dock_zone_at(window.mapToGlobal(QPoint(x, y)))
            return zone and (zone[0], zone[3])

        assert zone_at(window.width() - 10) == (DEVICE_VIEW_RAIL, True)
        assert zone_at(window.width() - 50) == (DEVICE_VIEW_FRAME, True)
        assert zone_at(10) == (DEVICE_VIEW_RAIL, False)

        window._on_device_strip_dropped(window.mapToGlobal(QPoint(window.width() - 10, y)))
        _pump()
        assert _showing(window) == {'rail'}
        assert not _left_of_the_tabs(window, window.rail_device_slot)

    def test_the_page_header_keeps_its_right_hand_end(self, build_window):
        """Its strip sits there, so that is what a drop there should mean -
        not the side panel whose band it crosses."""
        from PyQt6.QtCore import QPoint
        window = build_window(deviceView=DEVICE_VIEW_FLOATING)
        window.resize(1000, 700)
        window.show()
        _pump()
        page = window.tab_widget.currentWidget().mapTo(window, QPoint(0, 0))
        zone = window._device_dock_zone_at(window.mapToGlobal(QPoint(window.width() - 40, page.y() + 20)))
        assert zone[0] == DEVICE_VIEW_HEADER


class TestTheBottomView:
    """A row of its own between the tabs and the status bar, the strip at its
    right-hand end."""

    def test_it_sits_under_the_tabs_and_over_the_status_bar(self, build_window):
        from PyQt6.QtCore import QPoint
        window = build_window(deviceView=DEVICE_VIEW_BOTTOM)
        window.resize(1000, 700)
        _pump()
        top = lambda w: w.mapTo(window, QPoint(0, 0)).y()
        strip, tabs, bar = window.device_strip, window.tab_widget, window.status_bar
        assert top(tabs) + tabs.height() <= top(strip)
        assert top(strip) + strip.height() <= top(bar)
        # right-justified: its right edge is the tabs' right edge
        right = lambda w: w.mapTo(window, QPoint(0, 0)).x() + w.width()
        assert right(strip) == right(tabs)

    def test_the_status_bar_is_left_as_it_was(self, build_window):
        """The strip was first put in the bar itself, which it made two and
        a half times its height."""
        window = build_window(deviceView=DEVICE_VIEW_ROW)
        _pump()
        plain = window.status_bar.height()
        window._set_device_view(DEVICE_VIEW_BOTTOM)
        _pump()
        assert window.status_bar.height() == plain

    def test_the_row_takes_no_space_without_the_strip(self, build_window):
        """Against the floating view, which costs the window nothing - the
        status box view takes height of its own."""
        window = build_window(deviceView=DEVICE_VIEW_FLOATING)
        window.resize(1000, 700)
        _pump()
        tabs = window.tab_widget.height()
        window._set_device_view(DEVICE_VIEW_BOTTOM)
        _pump()
        assert window.tab_widget.height() < tabs
        window._set_device_view(DEVICE_VIEW_FLOATING)
        _pump()
        assert window.tab_widget.height() == tabs

    def test_a_drop_at_the_foot_of_the_window_docks_there(self, build_window):
        from PyQt6.QtCore import QPoint
        window = build_window(deviceView=DEVICE_VIEW_FLOATING)
        window.resize(1000, 700)
        window.show()
        _pump()
        tabs = window.tab_widget
        foot = tabs.mapTo(window, QPoint(0, 0)).y() + tabs.height() - 10
        for x in (400, window.width() - 20):  # the right-hand end too, where the strip will sit
            zone = window._device_dock_zone_at(window.mapToGlobal(QPoint(x, foot)))
            assert zone[0] == DEVICE_VIEW_BOTTOM, x


class TestEffectLevelsControls:
    """The effect levels controls: on the master's Settings tab header and
    its View and Utilities menus, none on a child; the device icons show a
    mute and lowered levels."""

    @pytest.fixture
    def levels(self, monkeypatch):
        from tests.test_effect_levels_ui import FakeController
        controller = FakeController(roles=('joystick', 'pedals', 'collective'))
        monkeypatch.setattr(G, 'effect_levels', controller, raising=False)
        return controller

    def test_the_master_has_them(self, build_window, levels):
        window = build_window()
        assert window.effect_levels_header is not None
        assert window.settings_header_bar.isAncestorOf(window.effect_levels_header)
        assert window.main_menu.mute_all_action is not None

    def test_a_child_has_none(self, build_window, levels):
        window = build_window(master=False)
        assert window.effect_levels_header is None
        assert window.main_menu.mute_all_action is None
        window.open_effect_levels_dialog()
        assert window._effect_levels_dialog is None

    def test_the_header_and_dialog_follow_the_scope(self, build_window, levels, monkeypatch):
        window = build_window()
        monkeypatch.setattr(G, 'current_device_config_scope', 'pedals', raising=False)
        G.app_state.set_scope('pedals')
        _pump()
        assert window.effect_levels_header.role == 'pedals'
        window.open_effect_levels_dialog()
        dialog = window._effect_levels_dialog
        try:
            assert dialog.role == 'pedals'
        finally:
            dialog.close()

    def test_pinned_sliders_push_the_settings_list_down(self, build_window, levels):
        window = build_window(deviceView=DEVICE_VIEW_HEADER)
        window.resize(1000, 700)
        _pump()
        header = window.effect_levels_header
        bar = window.settings_header_bar
        page = window.settings_area.parentWidget()
        monitor_bar = window.monitor_panel.header_bar.height()
        one_row = window.settings_area.y()
        tops = []
        for count in (2, 4, 7):
            for name in LEVEL_NAMES:
                levels.set_level_pinned(name, name in LEVEL_NAMES[:count])
            _pump()
            assert header.height() == header.sizeHint().height()   # nothing clipped
            last = header.sliders[header.shown_levels()[-1]]
            last_bottom = last.mapTo(page, last.rect().bottomLeft()).y()
            assert last_bottom <= bar.geometry().bottom()
            assert window.settings_area.y() > last_bottom
            tops.append(window.settings_area.y())
            # the device strip keeps to the top of the bar, level with the
            # first row once the rows stand taller than it
            slot = bar.device_slot
            assert slot.y() == bar.contentsRect().top()
            if header.height() > slot.height():
                assert header.y() == slot.y()
        assert one_row <= tops[0] < tops[1] < tops[2]   # two rows can fit beside the strip
        for name in LEVEL_NAMES[1:]:
            levels.set_level_pinned(name, False)
        _pump()
        assert window.settings_area.y() == one_row
        assert window.monitor_panel.header_bar.height() == monitor_bar

    def test_the_view_menu_sits_between_utilities_and_window_with_its_items(self, build_window,
                                                                             levels):
        menu = build_window().main_menu
        menus = [a.menu() for a in menu.menu.actions() if a.menu() is not None]
        assert menus.index(menu.view_menu) == menus.index(menu.utilities_menu) + 1
        assert menus.index(menu.window_menu) == menus.index(menu.view_menu) + 1
        assert menu.view_menu.menuAction().isVisible()
        items = [a for a in menu.view_menu.actions() if not a.isSeparator()]
        assert items == [menu.show_levels_action, menu.configure_levels_action,
                         menu.devices_menu.menuAction()]
        assert menu.show_levels_action.isCheckable()

    def test_utilities_keeps_mute_all_and_not_the_dialog_entry(self, build_window, levels):
        menu = build_window().main_menu
        actions = menu.utilities_menu.actions()
        assert menu.mute_all_action in actions
        assert menu.configure_levels_action not in actions
        assert menu.show_levels_action not in actions

    def test_a_child_shows_no_view_menu_items(self, build_window, levels):
        menu = build_window(master=False).main_menu
        assert menu.show_levels_action is None and menu.configure_levels_action is None
        assert menu.view_menu.actions() == []
        assert not menu.view_menu.menuAction().isVisible()

    def test_the_devices_submenu_is_under_view_and_drives_the_view(self, build_window, levels):
        window = build_window(deviceView=DEVICE_VIEW_ROW)
        menu = window.main_menu
        assert menu.devices_menu.menuAction() in menu.view_menu.actions()
        assert all(a.menu() is not menu.devices_menu for a in menu.window_menu.actions())
        menu.device_view_actions[DEVICE_VIEW_FLOATING].trigger()
        _pump()
        assert window._device_view() == DEVICE_VIEW_FLOATING
        window._set_device_view(DEVICE_VIEW_ROW)
        _pump()
        assert menu.device_view_actions[DEVICE_VIEW_ROW].isChecked()

    def test_show_effect_level_controls_hides_and_shows_the_box(self, build_window, levels):
        levels.set_levels('joystick', {'master': 70})
        window = build_window()
        action = window.main_menu.show_levels_action
        header = window.effect_levels_header
        assert action.isChecked() and header.box.isVisible()
        before = len(levels.calls)
        action.trigger()
        _pump()
        assert levels.calls[before:] == [('set_controls_shown', False)]
        assert not action.isChecked() and not header.box.isVisible()
        assert levels.levels('joystick')['master'] == 70 and not levels.muted('joystick')
        action.trigger()
        _pump()
        assert action.isChecked() and header.box.isVisible()

    def test_the_check_follows_a_change_made_elsewhere(self, build_window, levels):
        window = build_window()
        levels.set_controls_shown(False)
        _pump()
        assert not window.main_menu.show_levels_action.isChecked()
        assert not window.effect_levels_header.box.isVisible()

    def test_hidden_controls_stay_hidden_at_startup(self, build_window, levels):
        levels._shown = False
        window = build_window()
        assert not window.main_menu.show_levels_action.isChecked()
        assert window.effect_levels_header.box.isHidden()

    def test_configure_opens_the_dialog(self, build_window, levels):
        window = build_window()
        window.main_menu.configure_levels_action.trigger()
        dialog = window._effect_levels_dialog
        try:
            assert dialog is not None and dialog.isVisible()
        finally:
            if dialog is not None:
                dialog.close()

    def test_the_note_opens_the_dialog(self, build_window, levels):
        levels.set_levels('joystick', {'damper': 50})
        window = build_window()
        note = window.effect_levels_header.note
        assert note.isVisible()
        QTest.mouseClick(note, Qt.MouseButton.LeftButton)
        dialog = window._effect_levels_dialog
        try:
            assert dialog is not None and dialog.isVisible()
        finally:
            if dialog is not None:
                dialog.close()

    @pytest.mark.parametrize('view', [DEVICE_VIEW_HEADER, DEVICE_VIEW_ROW])
    @pytest.mark.parametrize('state', ['box', 'box with note', 'nothing', 'note alone'])
    def test_the_settings_list_starts_directly_below_the_bar(self, build_window, levels, state, view):
        if 'note' in state:
            levels.set_levels('joystick', {'damper': 50})
        if not state.startswith('box'):
            levels._shown = False
        window = build_window(deviceView=view)
        window.resize(1000, 700)
        _pump()
        header = window.effect_levels_header
        bar = window.settings_header_bar
        page = window.settings_area.parentWidget()
        assert header.box.isVisible() == state.startswith('box')
        assert header.note.isVisible() == ('note' in state)
        assert window.settings_area.y() == bar.geometry().bottom() + 1
        for widget in (header.box, header.note):
            if widget.isVisible():
                bottom = widget.mapTo(page, widget.rect().bottomLeft()).y()
                assert bottom <= bar.geometry().bottom()

    def test_the_menu_mutes_every_device(self, build_window, levels):
        window = build_window()
        window.main_menu.mute_all_action.trigger()
        assert levels.calls[-1] == ('set_muted_all', True)
        assert window.main_menu.mute_all_action.isChecked()
        window.main_menu.mute_all_action.trigger()
        assert levels.calls[-1] == ('set_muted_all', False)
        assert not window.main_menu.mute_all_action.isChecked()

    def test_a_muted_device_is_marked_on_the_panel_and_the_strip(self, build_window, levels):
        window = build_window(deviceView=DEVICE_VIEW_ROW)
        levels.set_muted('pedals', True)
        _pump()
        assert window.device_panel.device_muted_note('pedals')
        assert not window.device_panel.icons['pedals']._mute_badge.isHidden()
        chip = window.device_strip.device_mini_panel.chips['pedals']
        assert not chip._mute_badge.isHidden()
        assert chip._levels_badge.isHidden()
        assert window.device_strip.device_mini_panel.chips['joystick']._mute_badge.isHidden()
        levels.set_muted('pedals', False)
        _pump()
        assert chip._mute_badge.isHidden()

    def test_a_childs_lowered_levels_reach_its_icon_and_the_strip(self, build_window, levels):
        window = build_window(deviceView=DEVICE_VIEW_ROW)
        assert window.device_panel.device_levels_note('pedals') is None
        levels.set_levels('pedals', {'damper': 40})
        _pump()
        note = window.device_panel.device_levels_note('pedals')
        assert note
        icon = window.device_panel.icons['pedals']
        assert note in icon.toolTip()
        assert not icon._levels_badge.isHidden()
        assert icon._mute_badge.isHidden()
        assert window.device_panel.device_levels_note('joystick') is None
        assert window.device_panel.icons['joystick']._levels_badge.isHidden()
        chip = window.device_strip.device_mini_panel.chips['pedals']
        assert chip.levels_note == note
        assert not chip._levels_badge.isHidden()
        assert chip._mute_badge.isHidden()
        assert window.device_strip.device_mini_panel.chips['joystick']._levels_badge.isHidden()
        levels.set_levels('pedals', {'damper': 100})
        _pump()
        assert window.device_panel.device_levels_note('pedals') is None
        assert chip.levels_note is None
        assert icon._levels_badge.isHidden()
        assert chip._levels_badge.isHidden()

    def test_levels_stored_before_startup_show_on_the_icon(self, build_window, levels):
        levels.set_levels('collective', {'spring': 70})
        window = build_window()
        assert window.device_panel.device_levels_note('collective')

    def test_the_own_devices_buttons_reach_the_mute_binding(self, build_window, levels,
                                                            monkeypatch):
        window = build_window()
        report = SimpleNamespace(getPressedButtons=lambda: [3])
        monkeypatch.setattr(HapticEffect, 'device', SimpleNamespace(get_input=lambda: report),
                            raising=False)
        monkeypatch.setattr(G, 'active_buttons', [], raising=False)
        monkeypatch.setattr(G, 'device_connection_status', True, raising=False)
        window.get_active_buttons()
        assert levels.calls[-1] == ('on_device_buttons', 'joystick', [3])
        window.get_active_buttons()                  # unchanged: not reported again
        assert len([c for c in levels.calls if c[0] == 'on_device_buttons']) == 1
        monkeypatch.setattr(HapticEffect, 'device', None, raising=False)
        window.update_device_status(False)
        assert levels.calls[-1] == ('on_device_buttons', 'joystick', [])

    def test_a_child_reports_its_buttons_only_over_ipc(self, build_window, levels, monkeypatch):
        window = build_window(master=False)
        report = SimpleNamespace(getPressedButtons=lambda: [3])
        monkeypatch.setattr(HapticEffect, 'device', SimpleNamespace(get_input=lambda: report),
                            raising=False)
        monkeypatch.setattr(G, 'active_buttons', [], raising=False)
        window.get_active_buttons()
        assert not [c for c in levels.calls if c[0] == 'on_device_buttons']
        G.ipc_instance.send_message.assert_called_with('BUTTONS:pedals_[3]')
