"""The shaker card: transducer rows load from and save to the shaker's
settings, follow the selected output's channel count, the test buttons
play a preview of the rig or one row, and saved settings reach a running
shaker live."""
import json
import os

import numpy as np
import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtWidgets

import resources  # noqa: F401  (the compiled icons the card draws)
import telemffb.globals as G
from telemffb.hw.ffb_shaker import (
    DEFAULT_GAIN, LEGACY_SETTING_MODE, LEGACY_SETTING_PAN, LEGACY_SETTING_PROFILE,
    POSITIONS, SETTING_GAIN, SETTING_TRANSDUCERS, ShakerFFBDevice, ShakerPreview,
    ShakerProfile, Transducer, default_profiles_path, find_profile, load_profiles,
    shaker_settings, transducers_from_json, transducers_to_json,
)
from telemffb.utils import AudioOutputInfo

pytestmark = [pytest.mark.unit]

SR = 48000
BLOCK = 512


class _Settings(dict):
    def get(self, name, default=None, instance=None):
        if instance is not None:
            return dict.get(self, f"{instance}/{name}", default)
        return dict.get(self, name, default)

    def setValue(self, key, value):
        self[key] = value


class FakeOutput:
    def __init__(self, max_channels=8, exact=False):
        self.callback = None
        self.channels = None
        self.finished = None
        self.running = False
        self.starts = 0
        self.max_channels = max_channels
        #: a shared-mode WASAPI endpoint: only its own width is accepted
        self.exact = exact

    def supports_channels(self, n):
        return n == self.max_channels if self.exact else n <= self.max_channels

    def native_channels(self):
        return self.max_channels

    def start(self, callback, channels, finished_callback=None):
        self.starts += 1
        self.callback, self.channels, self.finished = callback, channels, finished_callback
        self.running = True

    def stop(self):
        self.running = False

    def render(self, seconds):
        frames = int(seconds * SR)
        blocks = []
        while frames > 0:
            n = min(BLOCK, frames)
            out = np.zeros((n, self.channels), dtype=np.float32)
            self.callback(out, n, None, None)
            blocks.append(out)
            frames -= n
        return np.concatenate(blocks)


RIG = [Transducer('Buttkicker', 3, 1.0, 'ButtKicker LFE', 'seat'),
       Transducer('Dayton L', 4, 0.8, 'Dayton TT25 Puck', 'seat back left'),
       Transducer('Dayton R', 5, 0.8, 'Dayton TT25 Puck', 'seat back right')]


# ---------------------------------------------------------------------------

class TestSettingsReader:
    def test_defaults_when_nothing_is_stored(self):
        values = shaker_settings(_Settings())
        _, active = load_profiles(default_profiles_path())
        assert values['gain'] == DEFAULT_GAIN
        assert [(t.channel, t.profile) for t in values['transducers']] == [(0, active), (1, active)]
        assert [p.name for p in values['profiles']] == [p.name for p in load_profiles(default_profiles_path())[0]]

    def test_stored_rows_round_trip(self):
        stored = _Settings({SETTING_GAIN: '2.5', SETTING_TRANSDUCERS: transducers_to_json(RIG)})
        values = shaker_settings(stored)
        assert values['gain'] == 2.5
        assert values['transducers'] == RIG

    def test_bad_values_fall_back(self):
        values = shaker_settings(_Settings({SETTING_GAIN: 'loud', SETTING_TRANSDUCERS: '{not json'}))
        assert values['gain'] == DEFAULT_GAIN
        assert len(values['transducers']) == 2

    def test_rows_json_tolerates_junk(self):
        rows = transducers_from_json(json.dumps([
            {'name': 'ok', 'channel': '2', 'gain': '0.5', 'profile': 'Generic', 'extra': 1},
            {'name': 'bad', 'channel': 'x'},
            'not a row',
        ]))
        assert rows == [Transducer('ok', 2, 0.5, 'Generic', 'seat')]
        assert transducers_from_json('') == []
        assert transducers_from_json('42') == []

    @pytest.mark.parametrize("mode,expected", [
        ('mono', [(0, 1.0), (1, 1.0)]),
        ('left', [(0, 1.0)]),
        ('right', [(1, 1.0)]),
    ])
    def test_legacy_channel_mode_becomes_rows(self, mode, expected):
        values = shaker_settings(_Settings({LEGACY_SETTING_MODE: mode,
                                            LEGACY_SETTING_PROFILE: 'ButtKicker LFE'}))
        assert [(t.channel, t.gain) for t in values['transducers']] == expected
        assert all(t.profile == 'ButtKicker LFE' for t in values['transducers'])

    def test_legacy_pan_becomes_two_gains(self):
        values = shaker_settings(_Settings({LEGACY_SETTING_MODE: 'pan', LEGACY_SETTING_PAN: '-1'}))
        gains = [t.gain for t in values['transducers']]
        assert gains == pytest.approx([1.0, 0.0])

    def test_stored_rows_outrank_legacy_keys(self):
        stored = _Settings({SETTING_TRANSDUCERS: transducers_to_json(RIG), LEGACY_SETTING_MODE: 'left'})
        assert shaker_settings(stored)['transducers'] == RIG

    def test_old_position_names_map_to_the_new_ones(self):
        rows = transducers_from_json(json.dumps([
            {'name': 'a', 'channel': 0, 'position': 'back left'},
            {'name': 'b', 'channel': 1, 'position': 'front'},
            {'name': 'c', 'channel': 2, 'position': 'seat'},
        ]))
        assert [t.position for t in rows] == ['seat back left', 'floor', 'seat']
        assert all(t.position in POSITIONS for t in rows)

    def test_old_profile_names_still_resolve(self):
        profiles, _ = load_profiles(default_profiles_path())
        assert find_profile(profiles, 'Dayton DAEX-25').name == 'Dayton TT25 Puck'
        assert find_profile(profiles, 'Buttkicker LFE').name == 'ButtKicker LFE'

    def test_find_profile_never_resolves_to_nothing(self):
        profiles = [ShakerProfile(name='a'), ShakerProfile(name='b')]
        assert find_profile(profiles, 'b').name == 'b'
        assert find_profile(profiles, 'zzz', 'b').name == 'b'
        assert find_profile(profiles, 'zzz', 'yyy').name == 'a'


class TestLiveApply:
    def test_saved_rows_reach_a_running_shaker(self):
        output = FakeOutput()
        profiles, _ = load_profiles(default_profiles_path())
        dev = ShakerFFBDevice(output=output, transducers=RIG, profiles=profiles, gain=1.0,
                              reconnect_interval_ms=0)
        from telemffb.hw.ffb_rhino import EFFECT_SINE
        handle = dev.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0).start()
        assert set(handle.voices) == {'ButtKicker LFE|all', 'Dayton TT25 Puck|all'}
        dev.apply_settings(_Settings({SETTING_GAIN: '1.0', SETTING_TRANSDUCERS: transducers_to_json(
            [Transducer('Only', 1, 0.5, 'Generic')])}))
        assert list(dev.groups) == ['Generic|all']
        assert list(handle.voices) == ['Generic|all']
        assert handle.started
        out = output.render(0.3)
        assert not out[:, 0].any() and abs(out[:, 1]).max() == pytest.approx(0.5, abs=0.02)

    def test_an_endpoint_that_takes_only_its_own_width_opens_that_wide(self):
        """A card set to 7.1 in Windows refuses a two-channel stream; two
        rows on channels 1 and 2 still play, on an eight-wide stream."""
        output = FakeOutput(max_channels=8, exact=True)
        dev = ShakerFFBDevice(output=output, gain=1.0, reconnect_interval_ms=0)
        assert output.channels == 8
        assert [t.channel for t in dev.transducers] == [0, 1]
        from telemffb.hw.ffb_rhino import EFFECT_SINE
        handle = dev.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0).start()
        out = output.render(0.3)
        assert abs(out[:, 0]).max() > 0.9 and abs(out[:, 1]).max() > 0.9
        assert not out[:, 2:].any()
        # and rows added later up to that width are driven without a restart
        dev.set_transducers(RIG)
        assert [r.channel for r in dev.synth.routes] == [3, 4, 5]

    def test_a_preview_on_such_an_endpoint_opens_that_wide_too(self):
        output = FakeOutput(max_channels=8, exact=True)
        preview = ShakerPreview(transducers=RIG, gain=DEFAULT_GAIN, only=0, output=output)
        preview.start()
        assert output.channels == 8
        out = output.render(0.1)
        assert abs(out[:, 3]).max() > 0.5
        preview.stop()

    def test_a_row_beyond_the_open_stream_is_kept_but_not_driven(self):
        output = FakeOutput(max_channels=2)
        dev = ShakerFFBDevice(output=output, gain=1.0, reconnect_interval_ms=0)
        dev.set_transducers([Transducer('Far', 5), Transducer('Near', 0)])
        assert [t.name for t in dev.transducers] == ['Far', 'Near']
        assert [(r.channel) for r in dev.synth.routes] == [0]


class TestPreview:
    def test_the_levels_are_a_typical_effects_and_the_default_gain_brings_them_up(self):
        # a stick intensity is a few tenths; through the default gain the
        # test lands near full scale without the limiter taking it
        assert 0.1 <= ShakerPreview.PULSE_AMPLITUDE <= 0.35
        assert ShakerPreview.TONE_AMPLITUDE < ShakerPreview.PULSE_AMPLITUDE
        assert 0.6 <= ShakerPreview.PULSE_AMPLITUDE * DEFAULT_GAIN <= 1.0
        output = FakeOutput()
        preview = ShakerPreview(transducers=RIG, gain=1.0, only=0, output=output)
        preview.start()
        out = output.render(0.1)
        assert 0.15 < abs(out[:, 3]).max() < 0.35             # at unity gain: a typical effect's level
        preview.stop()

    def test_pulse_then_tone_then_silence_per_row(self):
        output = FakeOutput()
        profiles, _ = load_profiles(default_profiles_path())
        preview = ShakerPreview(transducers=RIG, profiles=profiles, gain=DEFAULT_GAIN, output=output)
        preview.start()
        assert output.channels == 6
        first = output.render(ShakerPreview.TONE_START_S)
        assert abs(first[:, 3]).max() > 0.5 and abs(first[:, 4]).max() > 0.4
        assert not first[:, 0].any()
        preview.cue_tone()
        tone = output.render(ShakerPreview.TONE_LENGTH_S)
        assert abs(tone[-SR // 10:, 3]).max() > 0.4
        tail = output.render(0.3)
        assert abs(tail[-SR // 10:]).max() < 0.05
        preview.stop()
        assert not output.running

    def test_only_one_row(self):
        output = FakeOutput()
        preview = ShakerPreview(transducers=RIG, gain=DEFAULT_GAIN, only=1, output=output)
        preview.start()
        assert output.channels == 5                      # opened only as wide as that row needs
        out = output.render(0.1)
        assert abs(out[:, 4]).max() > 0.4
        assert not out[:, :4].any()
        preview.stop()

    def test_refuses_an_output_without_the_channel(self):
        from telemffb.hw.ffb_shaker import ShakerOutputError
        with pytest.raises(ShakerOutputError):
            ShakerPreview(transducers=RIG, output=FakeOutput(max_channels=2))


def _make_dialog(monkeypatch, settings, outputs):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(G, 'system_settings', settings, raising=False)
    for name, value in (('device_type', 'joystick'), ('master_instance', True),
                        ('child_instance', False), ('launched_instances', []),
                        ('device_usbpid', '2055'), ('device_capabilities', None),
                        ('device_di_guid', None), ('device_audio_output', None)):
        monkeypatch.setattr(G, name, value, raising=False)
    from telemffb.ui.dialogs.SystemSettingsDialog import SystemSettingsDialog
    monkeypatch.setattr(SystemSettingsDialog, '_enumerate_dinput_devices',
                        staticmethod(lambda enabled=None: []))
    monkeypatch.setattr(SystemSettingsDialog, '_enumerate_audio_outputs',
                        staticmethod(lambda: list(outputs)))
    monkeypatch.setattr(SystemSettingsDialog, '_query_ffb_axes',
                        staticmethod(lambda guid: []))
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_availability',
                        lambda *a, **k: (True, ''))
    from telemffb.hw.ffb_dinput import BridgeStatus
    monkeypatch.setattr('telemffb.hw.ffb_dinput.bridge_status',
                        lambda *a, **k: BridgeStatus(installed=True, version='1.0.0'))
    return app, SystemSettingsDialog()


OUTPUTS = [AudioOutputInfo(''), AudioOutputInfo('Card A'), AudioOutputInfo('Realtek 7.1', channels=8)]


class FakePreview:
    made = []
    TONE_START_S = ShakerPreview.TONE_START_S
    LENGTH_S = ShakerPreview.LENGTH_S

    def __init__(self, output_device=None, **kwargs):
        self.output_device = output_device
        self.kwargs = kwargs
        self.stopped = False
        FakePreview.made.append(self)

    def start(self):
        pass

    def cue_tone(self):
        pass

    def stop(self):
        self.stopped = True


class TestCard:
    def test_controls_sit_on_the_shaker_card_only(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        cards = dialog.device_cards.cards
        assert cards['shaker'].shaker is not None
        assert all(cards[r].shaker is None for r in cards if r != 'shaker')

    def test_defaults_load_when_nothing_is_stored(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        assert controls.gain_spin.value() == DEFAULT_GAIN
        rows = controls.transducers()
        assert [t.channel for t in rows] == [0, 1]
        names = [p.name for p in load_profiles(default_profiles_path())[0]]
        assert all(t.profile in names for t in rows)
        assert all(t.position in POSITIONS for t in rows)

    def test_stored_rows_load_and_save_back(self, monkeypatch):
        stored = _Settings({SETTING_GAIN: '2.5', SETTING_TRANSDUCERS: transducers_to_json(RIG),
                            'devpath_shaker': 'audio:Realtek 7.1'})
        _, dialog = _make_dialog(monkeypatch, stored, OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        assert controls.gain_spin.value() == 2.5
        assert controls.transducers() == RIG
        # the stored output was restored silently; the rows still learn
        # its channel count, so no hint and a full channel list
        assert not controls.hint.isVisibleTo(dialog)
        assert controls.rows[0].channel_combo.count() == 8
        controls.gain_spin.setValue(4.0)
        controls.rows[0].gain_spin.setValue(0.5)
        values = dialog.shaker_settings_values()
        assert values[SETTING_GAIN] == 4.0
        saved = transducers_from_json(values[SETTING_TRANSDUCERS])
        assert saved[0] == Transducer('Buttkicker', 3, 0.5, 'ButtKicker LFE', 'seat')
        assert saved[1:] == RIG[1:]

    def test_the_ids_column_shows_the_channel_count(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        row = dialog.device_cards.cards['shaker'].primary_row
        dialog.cb_select_s.setCurrentIndex(3)
        assert row.ids_label.text() == '8 ch'
        dialog.cb_select_s.setCurrentIndex(2)
        assert row.ids_label.text() == '2 ch'
        dialog.cb_select_s.setCurrentIndex(0)
        assert row.ids_label.text() == ''

    def test_channels_are_named_by_position(self, monkeypatch):
        from telemffb.hw.ffb_shaker import channel_label
        assert channel_label(3, 8) == '4 Subwoofer'
        assert channel_label(7, 8) == '8 Side R'
        assert channel_label(1, 2) == '2 Right'
        assert channel_label(4, 6) == '5 Rear L'
        assert channel_label(2, 3) == '3'                    # no standard three-channel layout
        assert channel_label(9, 8) == '10'                   # beyond the layout: the number
        assert channel_label(3, 4) == '4'                    # Quadraphonic or 3.1: the count cannot say
        three_one = ('Front L', 'Front R', 'Center', 'Subwoofer')
        assert channel_label(3, 4, three_one) == '4 Subwoofer'
        assert channel_label(2, 4, three_one) == '3 Center'
        assert channel_label(3, 8, three_one) == '4 Subwoofer'   # the platform's names win over the count
        assert channel_label(5, 4, three_one) == '6'

    def test_the_rows_name_channels_by_the_outputs_speaker_layout(self, monkeypatch):
        outputs = [AudioOutputInfo(''),
                   AudioOutputInfo('StarTech 3.1', channels=4,
                                   positions=('Front L', 'Front R', 'Center', 'Subwoofer'))]
        _, dialog = _make_dialog(monkeypatch, _Settings(), outputs)
        dialog.cb_select_s.setCurrentIndex(2)
        combo = dialog.device_cards.shaker_controls.rows[0].channel_combo
        assert [combo.itemText(i) for i in range(combo.count())] == [
            '1 Front L', '2 Front R', '3 Center', '4 Subwoofer']
        dialog.device_cards.shaker_controls.add_row()
        combo = dialog.device_cards.shaker_controls.rows[-1].channel_combo
        assert combo.itemText(3) == '4 Subwoofer'
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        combo = dialog.device_cards.shaker_controls.rows[0].channel_combo
        dialog.cb_select_s.setCurrentIndex(2)                 # stereo
        assert [combo.itemText(i) for i in range(combo.count())] == ['1 Left', '2 Right']
        dialog.cb_select_s.setCurrentIndex(3)                 # the 7.1 card
        assert combo.itemText(3) == '4 Subwoofer' and combo.count() == 8
        combo.setCurrentIndex(7)
        dialog.cb_select_s.setCurrentIndex(2)                 # back to stereo, channel 8 kept
        assert combo.itemText(7) == '8' and combo.itemText(1) == '2 Right'

    def test_channels_follow_the_selected_output(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        dialog.cb_select_s.setCurrentIndex(2)                     # Card A: stereo
        assert controls.rows[0].channel_combo.count() == 2
        dialog.cb_select_s.setCurrentIndex(3)                     # the 7.1 card
        assert controls.rows[0].channel_combo.count() == 8
        controls.rows[0].channel_combo.setCurrentIndex(5)
        dialog.cb_select_s.setCurrentIndex(2)                     # back to stereo
        assert controls.rows[0].channel() == 5                    # a stored channel is never lost
        assert controls.rows[0].channel_combo.count() == 6

    def test_add_and_remove_rows(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        dialog.cb_select_s.setCurrentIndex(3)
        controls.add_button.click()
        assert len(controls.rows) == 3
        assert controls.rows[2].channel() == 2                    # the first free channel
        controls.rows[0].remove_button.click()
        assert [t.channel for t in controls.transducers()] == [1, 2]

    def test_test_buttons_follow_the_selection(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        assert not controls.test_button.isEnabled()
        assert not controls.rows[0].test_button.isEnabled()
        dialog.cb_select_s.setCurrentIndex(2)
        assert controls.test_button.isEnabled()
        assert controls.rows[0].test_button.isEnabled()
        dialog.cb_select_s.setCurrentIndex(0)
        assert not controls.test_button.isEnabled()

    def test_test_all_plays_the_rows_through_the_selected_output(self, monkeypatch):
        FakePreview.made = []
        monkeypatch.setattr('telemffb.hw.ffb_shaker.ShakerPreview', FakePreview)
        stored = _Settings({SETTING_TRANSDUCERS: transducers_to_json(RIG)})
        _, dialog = _make_dialog(monkeypatch, stored, OUTPUTS)
        dialog.cb_select_s.setCurrentIndex(3)
        controls = dialog.device_cards.shaker_controls
        controls.gain_spin.setValue(1.5)
        controls.test_button.click()
        assert len(FakePreview.made) == 1
        preview = FakePreview.made[0]
        assert preview.output_device == 'Realtek 7.1'
        assert preview.kwargs['gain'] == 1.5
        assert preview.kwargs['transducers'] == RIG
        assert preview.kwargs['only'] is None
        assert not controls.test_button.isEnabled()               # one at a time
        dialog._shaker_test_finished()
        assert preview.stopped
        assert controls.test_button.isEnabled()

    def test_a_row_test_plays_that_row_only(self, monkeypatch):
        FakePreview.made = []
        monkeypatch.setattr('telemffb.hw.ffb_shaker.ShakerPreview', FakePreview)
        stored = _Settings({SETTING_TRANSDUCERS: transducers_to_json(RIG)})
        _, dialog = _make_dialog(monkeypatch, stored, OUTPUTS)
        dialog.cb_select_s.setCurrentIndex(3)
        dialog.device_cards.shaker_controls.rows[1].test_button.click()
        assert FakePreview.made[0].kwargs['only'] == 1
        dialog._shaker_test_finished()

    def test_the_default_output_previews_as_none(self, monkeypatch):
        FakePreview.made = []
        monkeypatch.setattr('telemffb.hw.ffb_shaker.ShakerPreview', FakePreview)
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        dialog.cb_select_s.setCurrentIndex(1)
        dialog.device_cards.shaker_controls.test_button.click()
        assert FakePreview.made[0].output_device is None
        dialog._shaker_test_finished()

    def test_hint_names_rows_the_output_cannot_drive(self, monkeypatch):
        stored = _Settings({SETTING_TRANSDUCERS: transducers_to_json(RIG),
                            'devpath_shaker': 'audio:Card A'})
        _, dialog = _make_dialog(monkeypatch, stored, OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        assert controls.hint.isVisibleTo(dialog)
        for name in ('Buttkicker', 'Dayton L', 'Dayton R'):
            assert name in controls.hint.text()
        dialog.cb_select_s.setCurrentIndex(3)                     # the 7.1 card
        assert not controls.hint.isVisibleTo(dialog)
        controls.rows[0].channel_combo.setCurrentIndex(7)
        dialog.cb_select_s.setCurrentIndex(2)                     # back to stereo
        assert 'Buttkicker' in controls.hint.text()
        controls.rows[0].remove_button.click()
        controls.rows[0].remove_button.click()
        controls.rows[0].remove_button.click()
        assert not controls.hint.isVisibleTo(dialog)

    def test_speaker_layout_button_opens_the_panel(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        opened = []
        monkeypatch.setattr(type(dialog), '_open_speaker_layout',
                            staticmethod(lambda: opened.append(True)))
        dialog.device_cards.shaker_controls.layout_button.click()
        assert opened == [True]

    def test_output_actions_sit_at_the_end_of_the_selector_row(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        card = dialog.device_cards.cards['shaker']
        row = card.primary_row.layout()
        widgets = [row.itemAt(i).widget() for i in range(row.count())]
        ids = widgets.index(card.primary_row.ids_label)
        assert widgets[ids + 1: ids + 4] == [card.shaker.rescan_button, card.shaker.layout_button,
                                             card.shaker.layout_info]
        assert card.shaker.rescan_button.parentWidget() is card.primary_row
        # the icon says there are instructions; it and the link carry the same ones
        assert card.shaker.layout_info.toolTip() == card.shaker.layout_button.toolTip()
        assert card.shaker.layout_info.toolTip().count('\n') >= 4
        assert not card.shaker.layout_info.pixmap().isNull()
        # the transducer actions stay with the transducer controls
        assert card.shaker.add_button.parentWidget() is card.shaker
        assert card.shaker.test_button.parentWidget() is card.shaker

    def test_rescan_relists_the_outputs_and_keeps_the_pick(self, monkeypatch):
        outputs = [AudioOutputInfo(''), AudioOutputInfo('Card A')]
        _, dialog = _make_dialog(monkeypatch, _Settings(), outputs)
        rescans = []
        monkeypatch.setattr(type(dialog), '_rescan_audio_library',
                            staticmethod(lambda: rescans.append(True) or True))
        dialog.cb_select_s.setCurrentIndex(2)                     # Card A
        outputs.append(AudioOutputInfo('New USB card', channels=8))
        dialog.device_cards.shaker_controls.rescan_button.click()
        assert rescans == [True]
        assert dialog.cb_select_s.count() == 1 + 3
        assert dialog.selected_device('shaker').name == 'Card A'   # the pick survives the re-list
        dialog.cb_select_s.setCurrentIndex(3)
        assert dialog.device_cards.shaker_controls.rows[0].channel_combo.count() == 8

    def test_rescan_waits_for_a_running_test(self, monkeypatch):
        FakePreview.made = []
        monkeypatch.setattr('telemffb.hw.ffb_shaker.ShakerPreview', FakePreview)
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        rescans = []
        monkeypatch.setattr(type(dialog), '_rescan_audio_library',
                            staticmethod(lambda: rescans.append(True) or True))
        dialog.cb_select_s.setCurrentIndex(2)
        dialog.device_cards.shaker_controls.test_button.click()
        dialog.device_cards.shaker_controls.rescan_button.click()
        assert rescans == []                                       # a stream is open: refused
        dialog._shaker_test_finished()
        dialog.device_cards.shaker_controls.rescan_button.click()
        assert rescans == [True]

    def test_save_reapplies_live(self, monkeypatch):
        from telemffb.hw.ffb_rhino import HapticEffect
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        applied = []

        class Dev:
            def apply_settings(self, settings):
                applied.append(settings)
        monkeypatch.setattr(HapticEffect, 'device', Dev())
        sent = []
        monkeypatch.setattr(G, 'ipc_instance',
                            type('Ipc', (), {'send_broadcast_message': lambda self, m: sent.append(m)})(),
                            raising=False)
        monkeypatch.setattr(G, 'launched_instances', {'shaker': object()}, raising=False)
        dialog._request_shaker_reapply_everywhere()
        assert applied == [G.system_settings]
        assert sent == ['REAPPLY_SHAKER']
