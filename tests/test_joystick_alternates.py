"""Joystick alternate devices: the card's extra rows, the swap-to-activate
semantics, per-device icons, and how it all lands in the settings.

Driven through the workflow harness's World (the real dialog over a fake
tree).  Row indices into the selector model: 0 = (None), 1 = Rhino,
2 = Warthog, 3 = Pedals, 4 = Collective.
"""
import os
import random

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_tap_workflows import RHINO, WARTHOG, World

pytestmark = [pytest.mark.unit]


@pytest.fixture
def app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


SETTLED = {'pidJoystick': '2054', 'pidPedals': '2052', 'pidCollective': '',
           'pidTrimWheel': '', 'masterInstance': 1, 'themeId': 2}


def add_alt(world, row_index):
    """Click '+ add device' and pick a device for the new row."""
    card = world.dialog.device_cards.joystick_card
    card.add_button.click()
    alt = card.alt_rows[-1]
    alt.selector.setCurrentIndex(row_index)
    return alt


class TestCardShape:
    def test_only_the_joystick_card_offers_alternates(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        cards = world.dialog.device_cards.cards
        assert cards['joystick'].add_button is not None
        for role in ('pedals', 'collective', 'trimwheel'):
            assert cards[role].add_button is None
            assert cards[role].alt_rows == []

    def test_add_button_disappears_at_the_cap(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.joystick_card
        add_alt(world, 2)
        assert card.add_button.isVisibleTo(card)
        add_alt(world, 0)
        assert not card.add_button.isVisibleTo(card)   # 3 devices = the cap


class TestPersistence:
    def test_alternate_saves_under_its_slot(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        add_alt(world, 2)                              # the Warthog
        assert world.save()
        s = world.settings
        assert s.get('devpath_joystick') == RHINO.path.decode()
        assert s.get('devpath_joystick_2') == WARTHOG.path.decode()
        assert s.get('devident_joystick_2') == 'Warthog'
        assert s.get('devids_joystick_2') == '044F:B10A'

    def test_reopen_restores_the_alternate_row(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=dict(
            SETTLED, devpath_joystick_2=WARTHOG.path.decode(),
            devident_joystick_2='Warthog', devids_joystick_2='044F:B10A'))
        card = world.dialog.device_cards.joystick_card
        assert len(card.alt_rows) == 1
        alt = card.alt_rows[0].selector
        dev = alt.currentData()
        assert getattr(dev, 'product_string', '') == 'Warthog'

    def test_removing_the_alternate_clears_its_slot(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=dict(
            SETTLED, devpath_joystick_2=WARTHOG.path.decode(),
            devident_joystick_2='Warthog', devids_joystick_2='044F:B10A'))
        card = world.dialog.device_cards.joystick_card
        card.alt_rows[0].remove_btn.click()
        assert card.alt_rows == []
        assert world.save()
        assert world.settings.get('devpath_joystick_2') == ''
        assert world.settings.get('devident_joystick_2') == ''

    def test_icon_choice_saves_per_device(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.joystick_card
        card.primary_row._icon_buttons['yoke'].click()
        assert world.save()
        assert world.settings.get('devicon_joystick') == 'yoke'


class TestActivate:
    def test_activating_an_alternate_switches_live_at_save(
            self, app, tmp_path, monkeypatch):
        """The rows stay put and the marker moves; the save writes the
        marked row's device to devpath_joystick - an ordinary device
        change downstream, riding the existing live-switch path."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        alt = add_alt(world, 2)                        # the Warthog
        alt.marker.click()                             # make active
        # nothing jumps rows in the UI
        assert world.dialog.cb_select_j.currentData().product_string == \
            'Rhino FFB Monster'
        assert alt.selector.currentData().product_string == 'Warthog'
        assert world.save()
        assert world.settings.get('devpath_joystick') == WARTHOG.path.decode()
        assert world.settings.get('devpath_joystick_2') == RHINO.path.decode()
        assert world.settings.get('devident_joystick') == 'Warthog'
        assert world.device_switches == 1              # applied live at save

    def test_marker_moves_to_the_activated_row(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.joystick_card
        alt = add_alt(world, 2)
        alt.marker.click()
        assert not card.primary_row.marker.isChecked()
        assert alt.marker.isChecked()
        assert card.active_row() is alt
        card.primary_row.marker.click()               # and back
        assert card.primary_row.marker.isChecked()
        assert card.active_row() is card.primary_row

    def test_icons_stay_with_their_rows_and_map_to_slots(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.joystick_card
        card.primary_row._icon_buttons['yoke'].click()  # Rhino shown as yoke
        alt = add_alt(world, 2)                         # Warthog, stick
        alt.marker.click()
        assert card.primary_row.device_icon() == 'yoke'    # rows unchanged
        assert alt.device_icon() == 'stick'
        assert world.save()
        # the ACTIVE (Warthog) row maps to the unsuffixed keys
        assert world.settings.get('devicon_joystick') == 'stick'
        assert world.settings.get('devicon_joystick_2') == 'yoke'

    def test_reopening_shows_the_active_device_in_row_one(
            self, app, tmp_path, monkeypatch):
        """The marker position is session state: storage always keeps the
        active device under devpath_joystick, so a fresh dialog shows it
        in row one, marker at rest."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=dict(
            SETTLED, devpath_joystick_2=WARTHOG.path.decode(),
            devident_joystick_2='Warthog', devids_joystick_2='044F:B10A'))
        card = world.dialog.device_cards.joystick_card
        assert card.active_row() is card.primary_row
        assert card.primary_row.marker.isChecked()
        assert world.dialog.cb_select_j.currentData().product_string == \
            'Rhino FFB Monster'

    def test_removing_the_active_alternate_falls_back_to_row_one(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.joystick_card
        alt = add_alt(world, 2)
        alt.marker.click()
        alt.remove_btn.click()
        assert card.active_row() is card.primary_row
        assert card.primary_row.marker.isChecked()
        assert world.save()
        assert world.settings.get('devpath_joystick') == RHINO.path.decode()
        assert world.settings.get('devpath_joystick_2') == ''


class TestConflicts:
    def test_alternate_may_not_take_another_slots_device(
            self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        alt = add_alt(world, 3)                        # the pedals' device
        dev = alt.selector.currentData()
        assert dev is None or getattr(dev, 'product_string', '') != 'Rhino FFB Pedals'


class TestMarkerGating:
    def test_empty_row_cannot_be_made_active(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.joystick_card
        card.add_button.click()
        alt = card.alt_rows[0]                      # created empty: (None)
        assert not alt.marker.isEnabled()
        alt.selector.setCurrentIndex(2)             # the Warthog
        assert alt.marker.isEnabled()
        alt.selector.setCurrentIndex(0)             # cleared again
        assert not alt.marker.isEnabled()

    def test_the_active_marker_survives_its_row_emptying(
            self, app, tmp_path, monkeypatch):
        """Being active with nothing selected is a state the user can be in
        deliberately (clearing the role); the marker must not die there."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.joystick_card
        world.dialog.cb_select_j.setCurrentIndex(0)   # clear the active row
        assert card.primary_row.marker.isChecked()
        assert card.primary_row.marker.isEnabled()

    def test_marker_hidden_until_there_is_a_choice(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        card = world.dialog.device_cards.joystick_card
        prim = card.primary_row
        assert not prim.marker.isVisibleTo(prim)       # one device: no marker
        alt = add_alt(world, 2)
        assert prim.marker.isVisibleTo(prim)
        assert alt.marker.isVisibleTo(alt)
        alt.remove_btn.click()
        assert not prim.marker.isVisibleTo(prim)


class TestInstanceTabsFollowSelection:
    """The per-instance settings tabs (system + startup) track the CURRENT
    selections, unsaved picks included - enable a role, pick its device,
    and its settings are configurable in the same visit."""

    def _tab_roles(self, tabs):
        return [tabs.widget(i).role for i in range(tabs.count())]

    def test_selecting_a_device_adds_its_tabs(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        dlg = world.dialog
        assert self._tab_roles(dlg.instance_tabs_system) == ['joystick', 'pedals']
        world.set_device('collective', 4)
        assert self._tab_roles(dlg.instance_tabs_system) == \
            ['joystick', 'pedals', 'collective']
        assert self._tab_roles(dlg.instance_tabs_startup) == \
            ['joystick', 'pedals', 'collective']

    def test_clearing_hides_but_preserves_the_panel(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        dlg = world.dialog
        world.set_device('collective', 4)
        panel = dlg.instance_panels[('system', 'collective')]
        world.set_device('collective', 0)              # cleared
        assert 'collective' not in self._tab_roles(dlg.instance_tabs_system)
        world.set_device('collective', 4)              # picked again
        assert dlg.instance_panels[('system', 'collective')] is panel

    def test_own_role_always_has_a_tab(self, app, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch, random.Random(0), settings=SETTLED)
        dlg = world.dialog
        world.set_device('joystick', 0)                # cleared, but it's us
        assert 'joystick' in self._tab_roles(dlg.instance_tabs_system)

    def test_a_stored_but_unplugged_role_keeps_its_tabs(
            self, app, tmp_path, monkeypatch):
        """An unplugged device's selector reads (None), but its role is
        still configured - its settings must stay reachable."""
        world = World(tmp_path, monkeypatch, random.Random(0), settings=dict(
            SETTLED, devpath_collective='usb-not-plugged-in',
            devident_collective='Collective'))
        assert 'collective' in self._tab_roles(
            world.dialog.instance_tabs_system)
