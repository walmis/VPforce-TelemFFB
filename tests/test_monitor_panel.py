"""Unit tests for MonitorPanel (telemffb/ui/panels/MonitorPanel.py) - the
Monitor tab's telemetry/active-effects tables extracted from MainWindow's
two hand-rolled QLabels.

No real MainWindow, TelemManager or SimConnect: G.system_settings and
G.telem_manager.simconnect are monkeypatched, and the owning MainWindow is
stood in for by FakeMainWindow exposing just ``detach_tab`` - the same
"stand-in window" shape tests/test_offline_editor_panel.py already uses.
"""
import os
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import telemffb.globals as G
from telemffb.SettingsManager import SettingsManager
from telemffb.ui.panels.MonitorPanel import (_KEY_COL, _STAR_COL, _VALUE_COL,
                                            MonitorPanel)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class _FakeSettings:
    """Minimal stand-in for G.system_settings: the .get(key, default) read
    and the global (un-scoped) .setValue the favourites list is saved with."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        #: Every setValue as (key, value, instance) - the real
        #: SystemSettings scopes a write under `{instance}/` when one is
        #: passed, so what a favourites write does NOT pass is the whole
        #: point of it being common to all devices.
        self.writes = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value, instance=None):
        self.writes.append((key, value, instance))
        self.values[key] = value


class FakeMainWindow:
    """Stands in for MainWindow: only the public surface MonitorPanel calls
    back into (the detach toolbar button)."""

    def __init__(self):
        self.detach_calls = []

    def detach_tab(self, index):
        self.detach_calls.append(index)


@pytest.fixture(autouse=True)
def fake_system_settings(monkeypatch):
    monkeypatch.setattr(G, 'system_settings', _FakeSettings({
        'enableDCS': True, 'enableIL2': False, 'enableMSFS': True,
        'enableXPLANE': False, 'enableBMS': False,
    }), raising=False)


@pytest.fixture
def mainwindow():
    return FakeMainWindow()


@pytest.fixture
def panel(qapp, mainwindow):
    return MonitorPanel(mainwindow=mainwindow)


def _waiting_status(panel):
    """The waiting page's sim lines as {sim key: shown status}."""
    shown = {}
    for line in panel._telem_waiting_label.text().splitlines():
        label, sep, status = line.partition(" : ")
        if sep:
            shown[label.strip()] = status.strip()
    return {sim: shown.get(SettingsManager.sim_label(sim)) for sim in SettingsManager.SIM_LABELS}


class TestWaitingState:
    def test_starts_on_waiting_page(self, panel):
        assert panel._telem_stack.currentWidget() is panel._telem_waiting_label
        status = _waiting_status(panel)
        assert status['DCS'] == 'Enabled'
        assert status['IL2'] == 'Disabled'
        assert None not in status.values()       # every sim has its line

    def test_telemetry_keys_before_first_frame(self, panel):
        assert panel.telemetry_keys() == ['Sim not running']

    def test_refresh_waiting_status_reflects_current_settings(self, panel, monkeypatch):
        monkeypatch.setattr(G, 'system_settings', _FakeSettings({'enableDCS': False}),
                             raising=False)
        panel.refresh_waiting_status()
        assert _waiting_status(panel)['DCS'] == 'Disabled'


class TestTelemetryRows:
    def test_update_switches_to_table_and_formats_rows(self, panel):
        panel.update_telemetry({'AoA': 1.23456, 'N': 'F-16C', 'src': 'DCS'})
        assert panel._telem_stack.currentWidget() is panel.telem_view
        keys = panel.telemetry_keys()
        assert set(keys) == {'AoA', 'N', 'src'}
        model = panel._telem_model
        row = keys.index('AoA')
        assert model.column_values(_VALUE_COL)[row] == '+1.235'

    def test_list_values_formatted_like_old_label(self, panel):
        panel.update_telemetry({'Gear': [1.0, 0, None]})
        model = panel._telem_model
        assert model.column_values(_VALUE_COL)[0] == '[1.000, 0, None]'

    def test_filter_keeps_only_matching_keys(self, panel):
        panel.telem_filter.setText('aoa, ias')
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0, 'RPM': 3.0})
        assert set(panel.telemetry_keys()) == {'AoA', 'IAS'}

    def test_filter_is_case_insensitive_and_comma_separated(self, panel):
        panel.telem_filter.setText(' AOA ,rpm')
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0, 'RPM': 3.0})
        assert set(panel.telemetry_keys()) == {'AoA', 'RPM'}

    def test_same_keys_next_frame_only_updates_values(self, panel):
        # RPM rather than AoA: this test is about row structure, not sign
        # formatting, and RPM never renders signed so it stays a plain check.
        panel.update_telemetry({'RPM': 1.0})
        model = panel._telem_model
        rows_inserted = []
        model.rowsInserted.connect(lambda *a: rows_inserted.append(a))
        panel.update_telemetry({'RPM': 2.0})
        assert rows_inserted == []
        assert model.column_values(_VALUE_COL) == ['2.000']

    def test_show_simvars_renames_msfs_keys(self, panel, monkeypatch):
        fake_simconnect = SimpleNamespace(get_var_name=lambda k: f"SIMVAR_{k}" if k == 'foo' else None)
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=fake_simconnect),
                             raising=False)
        panel.set_show_simvars(True)
        panel.update_telemetry({'foo': 1.0, 'src': 'MSFS'})
        assert 'SIMVAR_foo' in panel.telemetry_keys()

    def test_show_simvars_only_applies_to_msfs(self, panel, monkeypatch):
        fake_simconnect = SimpleNamespace(get_var_name=lambda k: 'SHOULD_NOT_APPEAR')
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=fake_simconnect),
                             raising=False)
        panel.set_show_simvars(True)
        panel.update_telemetry({'foo': 1.0, 'src': 'DCS'})
        assert 'foo' in panel.telemetry_keys()

    def test_show_simvars_handles_missing_src_key(self, panel, monkeypatch):
        """Telemetry dicts without a 'src' key (e.g. before the first frame
        with source info arrives) must not raise a KeyError."""
        fake_simconnect = SimpleNamespace(get_var_name=lambda k: 'SHOULD_NOT_APPEAR')
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=fake_simconnect),
                             raising=False)
        panel.set_show_simvars(True)
        panel.update_telemetry({'foo': 1.0})
        assert 'foo' in panel.telemetry_keys()

    def test_duplicate_display_names_do_not_collide(self, panel, monkeypatch):
        """Two distinct telemetry keys that happen to rename to the same
        simvar name must still show as two rows - rows are keyed by the
        original telemetry key, not the (possibly renamed) display key."""
        fake_simconnect = SimpleNamespace(get_var_name=lambda k: 'SAME_NAME')
        monkeypatch.setattr(G, 'telem_manager', SimpleNamespace(simconnect=fake_simconnect),
                             raising=False)
        panel.set_show_simvars(True)
        panel.update_telemetry({'foo': 1.0, 'bar': 2.0, 'src': 'MSFS'})
        assert panel._telem_model.rowCount() == 3  # foo, bar, src

    def test_signed_key_renders_explicit_sign_both_ways(self, panel):
        """AoA is in the static signed-key set, so both a positive and a
        negative value get an explicit sign - the whole point being that
        the digits don't shift as the value crosses zero."""
        panel.update_telemetry({'Pitch': 2.5})
        assert panel._telem_model.column_values(_VALUE_COL)[0] == '+2.500'
        panel.update_telemetry({'Pitch': -2.5})
        assert panel._telem_model.column_values(_VALUE_COL)[0] == '-2.500'

    def test_unsigned_key_stays_bare(self, panel):
        """Keys that never go negative and aren't in the signed set or
        pattern list (IAS, RPM) keep the old unsigned rendering."""
        panel.update_telemetry({'IAS': 250.0, 'RPM': 2500.0})
        keys = panel.telemetry_keys()
        values = panel._telem_model.column_values(_VALUE_COL)
        assert values[keys.index('IAS')] == '250.000'
        assert values[keys.index('RPM')] == '2500.000'

    def test_list_elements_each_rendered_signed(self, panel):
        """A vector value (ACCs matches the 'acc' pattern) gets every
        float element signed, not just the negative one - so the columns
        line up instead of only the sign-crossing element jittering."""
        panel.update_telemetry({'ACCs': [1.0, -2.0, 0.5]})
        assert panel._telem_model.column_values(_VALUE_COL)[0] == '[+1.000, -2.000, +0.500]'

    def test_pattern_matched_key_is_signed_even_when_never_negative(self, panel):
        """The case-insensitive substring patterns are a static rule, not
        contingent on ever seeing a negative value - StickX matches
        'stick' and renders signed from its very first (positive) frame."""
        panel.update_telemetry({'StickX': 0.75})
        assert panel._telem_model.column_values(_VALUE_COL)[0] == '+0.750'

    def test_position_keys_signed_per_axis_not_per_suffix(self, panel):
        """"...Pos" is not a signed suffix. The control axes run -1..1
        about a neutral, but CollectivePos runs 0..1 ("unlike the other
        control axes" - BaseTelemetryData), as do GearPos and NozzlePos,
        so a blanket pattern would sign three fields that never go
        negative. They are listed individually instead."""
        panel.update_telemetry({'ElevPos': 0.5, 'CollectivePos': 0.5,
                                'GearPos': 1.0, 'NozzlePos': 0.25})
        keys = panel.telemetry_keys()
        values = panel._telem_model.column_values(_VALUE_COL)
        assert values[keys.index('ElevPos')] == '+0.500'
        assert values[keys.index('CollectivePos')] == '0.500'
        assert values[keys.index('GearPos')] == '1.000'
        assert values[keys.index('NozzlePos')] == '0.250'

    def test_sticky_learn_stays_signed_after_first_negative(self, panel):
        """An unknown key (no exact or pattern match) that goes negative
        must be learned signed on that very frame - not one frame late -
        and stay signed afterwards even once it goes positive again."""
        panel.update_telemetry({'Zorp': -1.0})
        assert panel._telem_model.column_values(_VALUE_COL)[0] == '-1.000'
        panel.update_telemetry({'Zorp': 1.0})
        assert panel._telem_model.column_values(_VALUE_COL)[0] == '+1.000'

    def test_sticky_learned_keys_reset_on_new_aircraft(self, panel):
        """A key learned signed for one aircraft/sim must not leak into
        the next: a change in 'src' or 'N' between frames clears it."""
        panel.update_telemetry({'Zorp': -1.0, 'N': 'F-16C', 'src': 'DCS'})
        panel.update_telemetry({'Zorp': 1.0, 'N': 'A-10C', 'src': 'DCS'})
        keys = panel.telemetry_keys()
        values = panel._telem_model.column_values(_VALUE_COL)
        assert values[keys.index('Zorp')] == '1.000'


class TestFavorites:
    """The star gutter: click a row's star to favourite its telemetry key,
    tick "Favorites" to list only those. The set is saved to one global
    registry value, so it is the same list on every device instance."""

    def _click_star(self, panel, key):
        row = panel.telemetry_keys().index(key)
        panel._on_telem_clicked(panel._telem_model.index(row, _STAR_COL))

    def test_clicking_the_star_column_favorites_the_row(self, panel):
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0})
        self._click_star(panel, 'AoA')
        assert panel._favorites == {'AoA'}

    def test_clicking_it_again_unfavorites(self, panel):
        panel.update_telemetry({'AoA': 1.0})
        self._click_star(panel, 'AoA')
        self._click_star(panel, 'AoA')
        assert panel._favorites == set()

    def test_a_click_outside_the_star_column_changes_nothing(self, panel):
        panel.update_telemetry({'AoA': 1.0})
        panel._on_telem_clicked(panel._telem_model.index(0, _KEY_COL))
        assert panel._favorites == set()

    def test_the_delegate_paints_from_the_panels_own_set(self, panel):
        """Held by reference, never rebound - or a toggle would show only
        once something else forced the delegate to be handed a new set."""
        panel.update_telemetry({'AoA': 1.0})
        self._click_star(panel, 'AoA')
        assert panel._star_delegate._favorites is panel._favorites

    def test_favorites_are_saved_globally_not_per_device(self, panel):
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0})
        self._click_star(panel, 'IAS')
        self._click_star(panel, 'AoA')
        # Comma-separated under one key, written with no `instance` - which
        # is what keeps it out of the `{device}/` scope and so common to
        # every device's instance.
        assert G.system_settings.values['monitorFavoriteKeys'] == 'AoA,IAS'
        assert G.system_settings.writes == [
            ('monitorFavoriteKeys', 'IAS', None),
            ('monitorFavoriteKeys', 'AoA,IAS', None),
        ]

    def test_a_click_picks_up_another_instances_stars(self, panel):
        """Every instance shares the one registry value but each holds only
        the copy it read at startup, so a click has to re-read before it
        writes - otherwise starring here would drop whatever the pedals
        instance starred in the meantime, silently."""
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0})
        G.system_settings.values['monitorFavoriteKeys'] = 'RPM'  # another instance
        self._click_star(panel, 'AoA')
        assert panel._favorites == {'AoA', 'RPM'}
        assert G.system_settings.values['monitorFavoriteKeys'] == 'AoA,RPM'

    def test_re_reading_keeps_the_delegates_set(self, panel):
        """The re-read must mutate the set in place - rebinding it would
        leave FavoriteStarDelegate painting from the old one."""
        panel.update_telemetry({'AoA': 1.0})
        G.system_settings.values['monitorFavoriteKeys'] = 'RPM'
        self._click_star(panel, 'AoA')
        assert panel._star_delegate._favorites is panel._favorites

    def test_favorites_are_restored_on_construction(self, qapp, mainwindow, monkeypatch):
        monkeypatch.setattr(G, 'system_settings', _FakeSettings({
            'monitorFavoriteKeys': 'AoA, IAS ,',
        }), raising=False)
        assert MonitorPanel(mainwindow=mainwindow)._favorites == {'AoA', 'IAS'}

    def test_favorites_checkbox_filters_the_list(self, panel):
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0, 'RPM': 3.0})
        self._click_star(panel, 'IAS')
        panel.favorites_check.setChecked(True)
        assert panel.telemetry_keys() == ['IAS']

    def test_it_combines_with_the_text_filter(self, panel):
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0, 'RPM': 3.0})
        for key in ('AoA', 'IAS'):
            self._click_star(panel, key)
        panel.favorites_check.setChecked(True)
        panel.telem_filter.setText('ias')
        assert panel.telemetry_keys() == ['IAS']

    def test_unstarring_while_filtered_drops_the_row_at_once(self, panel):
        """No telemetry frame need arrive in between - with the sim closed
        none ever would, and the row would sit there un-starred but listed."""
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0})
        self._click_star(panel, 'AoA')
        panel.favorites_check.setChecked(True)
        self._click_star(panel, 'AoA')
        assert panel.telemetry_keys() == []

    def test_the_checkbox_state_is_remembered(self, panel):
        panel.update_telemetry({'AoA': 1.0})
        self._click_star(panel, 'AoA')
        panel.favorites_check.setChecked(True)
        assert G.system_settings.values['monitorFavoritesOnly'] == 1

    def test_it_is_not_restored_with_nothing_starred(self, qapp, mainwindow, monkeypatch):
        """An empty Monitor tab is a poor way to be told the box was left
        ticked on a machine whose favourites have since been cleared."""
        monkeypatch.setattr(G, 'system_settings', _FakeSettings({
            'monitorFavoritesOnly': 1, 'monitorFavoriteKeys': '',
        }), raising=False)
        assert not MonitorPanel(mainwindow=mainwindow).favorites_check.isChecked()


class TestActiveEffects:
    def _effects(self, *pairs):
        return [{'label': label, 'intensity': intensity}
                for label, intensity in pairs]

    def test_update_effects_makes_a_row_each(self, panel):
        panel.update_effects(self._effects(
            ('ID:1 Spring Override', None), ('ID:2 Damper Override', None)))
        model = panel._effects_model
        assert model.rowCount() == 2
        assert model.column_values(0) == ['ID:1 Spring Override', 'ID:2 Damper Override']

    def test_an_empty_list_yields_no_rows(self, panel):
        panel.update_effects(self._effects(('ID:1 Spring', None)))
        panel.update_effects([])
        assert panel._effects_model.rowCount() == 0

    def test_clear_effects_empties_the_table(self, panel):
        panel.update_effects(self._effects(('ID:1 Spring', None)))
        panel.clear_effects()
        assert panel._effects_model.rowCount() == 0

    def test_intensity_shows_as_a_percentage(self, panel):
        panel.update_effects(self._effects(('ID:1 Runway Rumble', 0.42)))
        assert panel._effects_model.column_values(1) == ['42%']

    def test_a_started_but_silent_effect_reads_zero_not_blank(self, panel):
        """The whole point: a runway rumble sitting on the ground is started
        but commanding nothing, and must not look like one that is."""
        panel.update_effects(self._effects(('ID:1 Runway Rumble', 0.0)))
        assert panel._effects_model.column_values(1) == ['0%']

    def test_full_scale_reads_100(self, panel):
        panel.update_effects(self._effects(('ID:1 Stick Shaker', 1.0)))
        assert panel._effects_model.column_values(1) == ['100%']

    def test_a_condition_gets_a_dash_not_a_number(self, panel):
        """Springs and dampers have coefficients, not a magnitude; their
        force depends on where the stick is. A bar there would be a guess."""
        panel.update_effects(self._effects(('ID:1 Spring Override', None)))
        assert panel._effects_model.column_values(1) == ['-']

    def test_a_missing_intensity_key_is_treated_as_absent(self, panel):
        panel.update_effects([{'label': 'ID:1 Spring Override'}])
        assert panel._effects_model.column_values(1) == ['-']


class TestIntensityTooltip:
    """Device full scale is the honest, comparable figure, but it reads low
    for two compounding reasons: the slider factor scales what a slider
    position stores, and an effect may then split that across axes. The
    hover says what the reading is as a share of the setting itself."""

    def _tooltip(self, panel, intensity, configured, factor):
        from PyQt6.QtCore import Qt
        panel.update_effects([{'label': 'ID:11 Rotor RPM/Engine Rumble',
                               'intensity': intensity,
                               'configured': configured, 'factor': factor}])
        model = panel._effects_model
        return model.data(model.index(0, 1), Qt.ItemDataRole.ToolTipRole)

    def test_the_heli_rotor_rumble_case(self, panel):
        """The real numbers: slider at 30% with a 0.4 factor stores 0.12,
        HelicopterEffectsMixIn gives each of X and Y half of it, so 6% on
        the device is half of what was configured - not 15% of it, which is
        the slider position 0.06 would have come from and meant nothing."""
        tip = self._tooltip(panel, 0.06, 0.12, 0.4)
        assert '6% of device full scale' in tip
        assert '50% of the configured setting' in tip
        assert '30% on the slider' in tip

    def test_an_effect_at_its_configured_value_reads_full(self, panel):
        tip = self._tooltip(panel, 0.12, 0.12, 0.4)
        assert '100% of the configured setting' in tip

    def test_the_factor_only_restates_the_slider_position(self, panel):
        """It is not part of the share - dividing by it was the bug."""
        tip = self._tooltip(panel, 0.06, 0.12, 1)
        assert '50% of the configured setting' in tip
        assert '12% on the slider' in tip

    def test_an_unknown_setting_value_leaves_one_reading(self, panel):
        """Effects with no setting, or a pattern for a name, have nothing to
        be a share of - better silent than invented."""
        tip = self._tooltip(panel, 0.5, None, 1)
        assert tip == '50% of device full scale'

    def test_a_zero_setting_is_not_divided_by(self, panel):
        tip = self._tooltip(panel, 0.0, 0, 0.4)
        assert tip == '0% of device full scale'

    def test_a_non_numeric_setting_is_ignored(self, panel):
        tip = self._tooltip(panel, 0.5, 'BASIC', 1)
        assert tip == '50% of device full scale'

    def test_a_condition_explains_why_there_is_no_number(self, panel):
        tip = self._tooltip(panel, None, None, 1)
        assert 'condition effect' in tip
        assert 'stick position' in tip

    def test_it_can_exceed_the_configured_value(self, panel):
        """Other gains stack on top of the setting. Reporting it plainly is
        more use than clamping it to a tidy lie."""
        tip = self._tooltip(panel, 0.24, 0.12, 0.4)
        assert '200% of the configured setting' in tip

    def test_the_name_column_names_the_effect_type(self, panel):
        """The badge is a waveform or a letter - a reminder, not a code to
        memorise - so the hover spells it out."""
        from PyQt6.QtCore import Qt
        from telemffb.hw.ffb_rhino import EFFECT_SINE
        panel.update_effects([{'label': 'ID:8 Jet Engine Rumble',
                               'intensity': 0.01, 'type': EFFECT_SINE}])
        model = panel._effects_model
        tip = model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
        assert tip == 'ID:8 Jet Engine Rumble\nPeriodic Sine effect'

    def test_every_periodic_waveform_is_named_as_a_periodic(self, panel):
        """The shape alone says nothing about which family it belongs to."""
        from PyQt6.QtCore import Qt
        from telemffb.hw.ffb_rhino import PERIODIC_EFFECTS, effect_names
        for effect_type in PERIODIC_EFFECTS:
            panel.update_effects([{'label': 'ID:1 Rumble', 'intensity': 0.5,
                                   'type': effect_type}])
            model = panel._effects_model
            tip = model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
            assert tip == f'ID:1 Rumble\nPeriodic {effect_names[effect_type]} effect'

    def test_a_non_periodic_type_is_not_called_periodic(self, panel):
        from PyQt6.QtCore import Qt
        from telemffb.hw.ffb_rhino import EFFECT_CONSTANT
        panel.update_effects([{'label': 'ID:5 Runway Rumble',
                               'intensity': 0.0, 'type': EFFECT_CONSTANT}])
        model = panel._effects_model
        tip = model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
        assert tip == 'ID:5 Runway Rumble\nConstant effect'

    def test_a_condition_names_its_type_too(self, panel):
        from PyQt6.QtCore import Qt
        from telemffb.hw.ffb_rhino import EFFECT_SPRING
        panel.update_effects([{'label': 'ID:10 Cyclic Spring',
                               'intensity': None, 'type': EFFECT_SPRING}])
        model = panel._effects_model
        tip = model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
        assert tip == 'ID:10 Cyclic Spring\nSpring effect'

    def test_the_name_column_keeps_its_own_hover(self, panel):
        from PyQt6.QtCore import Qt
        panel.update_effects([{'label': 'ID:11 Rotor RPM/Engine Rumble',
                               'intensity': 0.06, 'configured': 0.12,
                               'factor': 0.4}])
        model = panel._effects_model
        assert model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole) ==             'ID:11 Rotor RPM/Engine Rumble'

    def test_effects_without_the_extra_keys_still_render(self, panel):
        panel.update_effects([{'label': 'ID:1 Rumble', 'intensity': 0.06}])
        assert panel._effects_model.column_values(1) == ['6%']


class TestTooltipRefresh:
    """The model's fast path compares values to decide what repainted; a
    tooltip that moves while its percentage rounds to the same number has
    to count as a change, or the hover goes stale."""

    def test_a_changed_tooltip_alone_still_signals(self, panel):
        from PyQt6.QtCore import Qt
        changed = []
        panel._effects_model.dataChanged.connect(
            lambda *a, **k: changed.append(True))

        panel.update_effects([{'label': 'ID:1 Rumble', 'intensity': 0.06,
                               'configured': 0.12, 'factor': 0.4}])
        changed.clear()
        # still 6% on the device, but the user moved the slider
        panel.update_effects([{'label': 'ID:1 Rumble', 'intensity': 0.06,
                               'configured': 0.24, 'factor': 0.4}])

        assert changed, 'the view was never told to repaint'
        model = panel._effects_model
        tip = model.data(model.index(0, 1), Qt.ItemDataRole.ToolTipRole)
        assert '25% of the configured setting' in tip

    def test_clearing_drops_the_tooltips_too(self, panel):
        panel.update_effects([{'label': 'ID:1 Rumble', 'intensity': 0.06,
                               'configured': 0.12, 'factor': 0.4}])
        panel.clear_effects()
        assert panel._effects_model.rowCount() == 0


class TestEffectGrouping:
    """The list is grouped by effect type so the intensity column reads as
    one scale at a time: everything that reports a number first, everything
    that reports a dash last."""

    def _rows(self, panel, *types):
        from telemffb.hw.ffb_rhino import EFFECT_CONSTANT  # noqa: F401
        panel.update_effects([
            {'label': f'ID:{i} effect', 'intensity': None, 'type': t}
            for i, t in enumerate(types)])
        return panel._effects_model.column_values(0)

    def test_periodic_then_constant_then_condition(self, panel):
        from telemffb.hw.ffb_rhino import (EFFECT_CONSTANT, EFFECT_SINE,
                                           EFFECT_SPRING)
        rows = self._rows(panel, EFFECT_SPRING, EFFECT_CONSTANT, EFFECT_SINE)
        assert rows == ['ID:2 effect', 'ID:1 effect', 'ID:0 effect']

    def test_ramp_sorts_with_constant_not_with_conditions(self, panel):
        """It is a force with a magnitude, just a moving one."""
        from telemffb.hw.ffb_rhino import EFFECT_RAMP, EFFECT_SPRING
        rows = self._rows(panel, EFFECT_SPRING, EFFECT_RAMP)
        assert rows == ['ID:1 effect', 'ID:0 effect']

    def test_spring_shaped_conditions_lead_the_conditions(self, panel):
        from telemffb.hw.ffb_rhino import (EFFECT_DAMPER, EFFECT_DETENT,
                                           EFFECT_SPRING)
        rows = self._rows(panel, EFFECT_DAMPER, EFFECT_DETENT, EFFECT_SPRING)
        assert rows == ['ID:1 effect', 'ID:2 effect', 'ID:0 effect']

    def test_insertion_order_is_kept_within_a_group(self, panel):
        """A stable sort, so the ID order this has always shown survives -
        and, more to the point, so the model's diff stays correct."""
        from telemffb.hw.ffb_rhino import EFFECT_SINE, EFFECT_SQUARE
        rows = self._rows(panel, EFFECT_SINE, EFFECT_SQUARE, EFFECT_SINE)
        assert rows == ['ID:0 effect', 'ID:1 effect', 'ID:2 effect']

    def test_an_unknown_type_sorts_last(self, panel):
        from telemffb.hw.ffb_rhino import EFFECT_SPRING
        rows = self._rows(panel, 999, EFFECT_SPRING)
        assert rows == ['ID:1 effect', 'ID:0 effect']

    def test_effects_with_no_type_still_render(self, panel):
        panel.update_effects([{'label': 'ID:1 Rumble', 'intensity': 0.5}])
        assert panel._effects_model.column_values(0) == ['ID:1 Rumble']

    def test_survivors_keep_their_relative_order_across_updates(self, panel):
        """What KeyValueTableModel._diff_update relies on: it removes and
        inserts rather than resetting, so a survivor moving past another
        survivor would leave the list wrongly ordered with no error."""
        from telemffb.hw.ffb_rhino import EFFECT_SINE, EFFECT_SPRING
        panel.update_effects([
            {'label': 'A', 'intensity': 0.1, 'type': EFFECT_SINE},
            {'label': 'B', 'intensity': None, 'type': EFFECT_SPRING},
        ])
        panel.update_effects([
            {'label': 'B', 'intensity': None, 'type': EFFECT_SPRING},
            {'label': 'C', 'intensity': 0.2, 'type': EFFECT_SINE},
            {'label': 'A', 'intensity': 0.1, 'type': EFFECT_SINE},
        ])
        rows = panel._effects_model.column_values(0)
        assert rows.index('A') < rows.index('B')
        assert rows.index('C') < rows.index('B')


class TestEffectTypeBadge:
    """Square against sine is a real difference in feel, and all five shapes
    are in use, so the glyph discriminates rather than decorates. Types with
    no waveform to draw get a letter, and every row reserves the badge width
    either way so the names line up."""

    def test_a_periodic_effect_gets_its_shape(self, panel):
        from telemffb.hw.ffb_rhino import EFFECT_SQUARE
        from telemffb.ui.widgets.EffectTypeDelegate import SHAPES
        panel.update_effects([{'label': 'ID:1 Rumble', 'intensity': 0.5,
                               'type': EFFECT_SQUARE}])
        assert panel._type_delegate._types['ID:1 Rumble'] == EFFECT_SQUARE
        assert EFFECT_SQUARE in SHAPES

    def test_every_periodic_type_has_a_shape(self):
        from telemffb.hw.ffb_rhino import PERIODIC_EFFECTS
        from telemffb.ui.widgets.EffectTypeDelegate import SHAPES
        assert set(PERIODIC_EFFECTS) == set(SHAPES)

    def test_constant_effects_are_given_no_shape(self):
        """The glyph describes the magnitude channel, and a constant's is a
        flat line - beside "Runway Rumble", whose feel comes from a random
        direction modulator and an envelope, that would read as "doing
        nothing"."""
        from telemffb.hw.ffb_rhino import EFFECT_CONSTANT, EFFECT_SPRING
        from telemffb.ui.widgets.EffectTypeDelegate import SHAPES
        assert EFFECT_CONSTANT not in SHAPES
        assert EFFECT_SPRING not in SHAPES

    def test_every_waveform_file_is_tintable(self):
        """Tinting is a substitution of currentColor. Artwork exported with
        a hardcoded fill renders black, which on the dark theme is an
        invisible badge rather than an obvious mistake."""
        from telemffb.ui.widgets.EffectTypeDelegate import SHAPES, _svg_bytes
        for name in SHAPES.values():
            assert b"currentColor" in _svg_bytes(name), name

    def test_every_waveform_file_is_registered_for_frozen_builds(self):
        """Loading falls back to the source tree, so a file missing from
        resources.qrc passes every other test here and fails only once the
        app is built."""
        from pathlib import Path
        from telemffb.ui.widgets.EffectTypeDelegate import SHAPES
        qrc = (Path(__file__).parents[1] / 'resources.qrc').read_text()
        for name in SHAPES.values():
            assert f'image/{name}' in qrc, f"{name} missing from resources.qrc"

    def test_non_periodic_types_get_a_letter(self):
        """A badge on every row, or the names of the constants and
        conditions start further left than the periodics above them."""
        from telemffb.hw.ffb_rhino import (EFFECT_CONSTANT, EFFECT_DAMPER,
                                           EFFECT_FRICTION, EFFECT_INERTIA,
                                           EFFECT_SPRING)
        from telemffb.ui.widgets.EffectTypeDelegate import LETTERS
        assert LETTERS[EFFECT_CONSTANT] == 'C'
        assert LETTERS[EFFECT_SPRING] == 'S'
        assert LETTERS[EFFECT_DAMPER] == 'D'
        assert LETTERS[EFFECT_INERTIA] == 'I'
        assert LETTERS[EFFECT_FRICTION] == 'F'

    def test_types_follow_the_rows(self, panel):
        """Keyed by the row label, so a stale entry cannot outlive its row."""
        from telemffb.hw.ffb_rhino import EFFECT_SINE
        panel.update_effects([{'label': 'A', 'intensity': 0.5, 'type': EFFECT_SINE}])
        panel.update_effects([{'label': 'B', 'intensity': 0.5, 'type': EFFECT_SINE}])
        assert 'A' not in panel._type_delegate._types


class TestConditionAxes:
    def test_the_cell_text_is_what_the_delegate_splits_on(self):
        """MonitorPanel writes the text and IntensityBarDelegate parses it
        back. If the two drift, the cell quietly falls back to plain text
        rather than failing."""
        from telemffb.ui.panels.MonitorPanel import _axes_text
        from telemffb.ui.widgets.IntensityBarDelegate import parse_axes
        assert parse_axes(_axes_text([1.0, 0.6])) == ('100%', '60%')
        assert parse_axes(_axes_text([0.08, None])) == ('8%', '-')
        assert parse_axes('42%') is None


class TestScopeDevice:
    """The "Device:" indicator in the page header: whose telemetry and
    effects the two tables are showing."""

    def test_hidden_until_there_is_a_device_to_name(self, panel):
        assert panel.scope_text() == ''

    def test_names_the_device_in_scope(self, panel):
        panel.set_scope_device('pedals')
        assert panel.scope_text() == 'Pedals'
        assert not panel._scope_icon.pixmap().isNull()

    def test_a_child_that_is_not_sending_is_said_so(self, panel):
        """The telemetry table has fallen back to this instance's own frame;
        naming the child without a word would pass that off as the child's."""
        panel.set_scope_device('pedals', sending=False)
        assert panel.scope_text() == 'Pedals (no data)'

    def test_a_device_strip_in_the_same_bar_stands_in_for_it(self, panel):
        """The "tab header" device view docks the strip an inch to the right,
        with the device in scope highlighted: saying it twice is clutter."""
        panel.set_scope_device('pedals')
        panel.header_bar.device_slot.show()
        panel.refresh_scope_indicator()
        assert panel.scope_text() == ''
        panel.header_bar.device_slot.hide()
        panel.refresh_scope_indicator()
        assert panel.scope_text() == 'Pedals'

    def test_no_device_hides_it_again(self, panel):
        panel.set_scope_device('pedals')
        panel.set_scope_device(None)
        assert panel.scope_text() == ''

    def test_the_tables_keep_plain_column_headers(self, panel):
        from PyQt6.QtCore import Qt
        panel.set_scope_device('trimwheel')
        headers = [panel._telem_model.headerData(section, Qt.Orientation.Horizontal)
                   for section in (_STAR_COL, _KEY_COL, _VALUE_COL)]
        headers += [panel._effects_model.headerData(section, Qt.Orientation.Horizontal)
                    for section in (0, 1)]
        # The star gutter has no header text of its own - a column heading
        # over a row of stars would only label the obvious.
        assert headers == ['', 'Key', 'Value', 'Active Effects', 'Intensity']


class TestCopySelection:
    """CopyableTableView (telemffb.ui.widgets.custom_widgets) stands in for
    the old QLabel's mouse-selectable free text - Ctrl+C copies the
    selected cells as tab/newline-separated text."""

    def test_copy_selected_row_to_clipboard(self, panel):
        panel.update_telemetry({'AoA': 1.0, 'IAS': 2.0})
        view = panel.telem_view
        model = view.model()
        selection = view.selectionModel()
        for column in (_KEY_COL, _VALUE_COL):
            selection.select(model.index(0, column),
                             selection.SelectionFlag.Select)
        view._copy_selection()
        expected = '\t'.join([model.data(model.index(0, _KEY_COL)),
                              model.data(model.index(0, _VALUE_COL))])
        assert QApplication.clipboard().text() == expected

    def test_star_gutter_is_not_copied_with_the_row(self, panel):
        """The star column holds no value, so it must stay out of a
        selection - otherwise every copied row would start with an empty
        field. KeyValueTableModel drops ItemIsSelectable for it, which
        QItemSelectionModel honours even for a programmatic select()."""
        panel.update_telemetry({'AoA': 1.0})
        view = panel.telem_view
        model = view.model()
        selection = view.selectionModel()
        selection.select(model.index(0, 0), selection.SelectionFlag.Select)
        selection.select(model.index(0, 1), selection.SelectionFlag.Select)
        selection.select(model.index(0, 2), selection.SelectionFlag.Select)
        assert {i.column() for i in selection.selectedIndexes()} == {_KEY_COL, _VALUE_COL}

    def test_copy_with_no_selection_leaves_clipboard_untouched(self, panel):
        QApplication.clipboard().setText('unchanged')
        panel.telem_view._copy_selection()
        assert QApplication.clipboard().text() == 'unchanged'


class TestDetachToolbar:
    def test_detach_button_calls_back_into_mainwindow_tab_zero(self, panel, mainwindow):
        panel.detach_action.trigger()
        assert mainwindow.detach_calls == [0]

    def test_set_detach_toolbar_visible(self, panel):
        # isHidden() reflects the widget's own explicit visibility flag
        # (unlike isVisible(), which also depends on the panel itself
        # having been shown inside a top-level window).
        panel.set_detach_toolbar_visible(False)
        assert panel.detach_toolbar.isHidden() is True
        panel.set_detach_toolbar_visible(True)
        assert panel.detach_toolbar.isHidden() is False


class TestSplit:
    """The two panes are tables with the same size hint, so the splitter
    would start them level; the telemetry pane's 55% share is stated instead.
    Before the first frame there is no split at all - nothing has effects
    yet, so that pane is hidden and the placeholder has the width to itself.
    """

    FRAME = {'src': 'DCS', 'N': 'F-16', 'T': 1.0}

    def _shares(self, panel):
        telemetry, effects = panel._splitter.sizes()
        return telemetry / (telemetry + effects)

    def _live(self, panel, width=1600):
        """A shown panel with one telemetry frame in it - the only state in
        which both panes exist."""
        panel.resize(width, 500)
        panel.show()
        panel.update_telemetry(dict(self.FRAME))
        QApplication.processEvents()
        return panel

    def test_the_effects_pane_is_hidden_until_the_first_frame(self, panel):
        """Waiting for data, the placeholder gets the whole splitter rather
        than being squeezed against an empty effects table."""
        panel.resize(1600, 500)
        panel.show()
        QApplication.processEvents()
        assert panel.effects_view.isHidden()
        assert panel._splitter.sizes()[1] == 0
        panel.close()

    def test_the_first_frame_brings_the_effects_pane_back(self, panel):
        self._live(panel)
        assert not panel.effects_view.isHidden()
        assert self._shares(panel) == pytest.approx(0.55, abs=0.01)
        panel.close()

    def test_the_split_holds_at_a_width_the_placeholder_is_wider_than(self, panel):
        """A QStackedWidget is as wide as its widest page even when that page
        is not the current one, so the placeholder used to floor the
        telemetry pane well past its 55% share in a narrow window."""
        self._live(panel, width=900)
        assert self._shares(panel) == pytest.approx(0.55, abs=0.01)
        panel.close()

    def test_a_split_the_user_dragged_is_not_reset_on_reshow(self, panel):
        """The starting split is applied once - coming back to the tab
        (or reattaching the detached window) keeps what the user set."""
        self._live(panel)
        total = sum(panel._splitter.sizes())
        panel._splitter.setSizes([total // 2, total - total // 2])
        panel.hide()
        panel.show()
        QApplication.processEvents()
        assert self._shares(panel) == pytest.approx(0.5, abs=0.02)
        panel.close()

    def test_a_split_the_user_dragged_survives_a_sim_restart(self, panel):
        """The effects pane going away with the sim and coming back with it
        must not quietly undo a drag."""
        self._live(panel)
        total = sum(panel._splitter.sizes())
        panel._splitter.setSizes([total // 2, total - total // 2])
        QApplication.processEvents()
        dragged = panel._splitter.sizes()
        panel.refresh_waiting_status()
        QApplication.processEvents()
        assert panel.effects_view.isHidden()
        panel.update_telemetry(dict(self.FRAME))
        QApplication.processEvents()
        assert panel._splitter.sizes() == dragged
        panel.close()
