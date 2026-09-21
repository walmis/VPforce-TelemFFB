"""The shaker card: its controls load from and save to the shaker's
settings, the test button plays a preview through the selected output,
and saved settings reach a running shaker live."""
import os

import numpy as np
import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtWidgets

import telemffb.globals as G
from telemffb.hw.ffb_shaker import (
    DEFAULT_GAIN, SETTING_GAIN, SETTING_MODE, SETTING_PAN, SETTING_PROFILE,
    ShakerFFBDevice, ShakerPreview, ShakerProfile, default_profiles_path,
    find_profile, load_profiles, shaker_settings,
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
    def __init__(self):
        self.callback = None
        self.channels = None
        self.finished = None
        self.running = False
        self.starts = 0

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


# ---------------------------------------------------------------------------

class TestSettingsReader:
    def test_defaults_when_nothing_is_stored(self):
        values = shaker_settings(_Settings())
        _, active = load_profiles(default_profiles_path())
        assert values['gain'] == DEFAULT_GAIN
        assert values['channel_mode'] == 'mono' and values['pan'] == 0.0
        assert values['profile'].name == active

    def test_registry_strings_are_understood(self):
        values = shaker_settings(_Settings({SETTING_GAIN: '2.5', SETTING_MODE: 'Pan',
                                            SETTING_PAN: '-0.4', SETTING_PROFILE: 'Buttkicker LFE'}))
        assert values['gain'] == 2.5 and values['channel_mode'] == 'pan'
        assert values['pan'] == -0.4
        assert values['profile'].name == 'Buttkicker LFE'

    def test_bad_values_fall_back(self):
        values = shaker_settings(_Settings({SETTING_GAIN: 'loud', SETTING_MODE: 'surround',
                                            SETTING_PAN: '7', SETTING_PROFILE: 'no such'}))
        assert values['gain'] == DEFAULT_GAIN and values['channel_mode'] == 'mono'
        assert values['pan'] == 1.0
        assert values['profile'].name == load_profiles(default_profiles_path())[1]

    def test_find_profile_never_resolves_to_nothing(self):
        profiles = [ShakerProfile(name='a'), ShakerProfile(name='b')]
        assert find_profile(profiles, 'b').name == 'b'
        assert find_profile(profiles, 'zzz', 'b').name == 'b'
        assert find_profile(profiles, 'zzz', 'yyy').name == 'a'


class TestLiveApply:
    def test_saved_settings_reach_a_running_shaker(self):
        output = FakeOutput()
        dev = ShakerFFBDevice(output=output, reconnect_interval_ms=0)
        dev.apply_settings(_Settings({SETTING_GAIN: '1.5', SETTING_MODE: 'right',
                                      SETTING_PROFILE: 'Buttkicker LFE'}))
        assert dev.synth._gain == 1.5
        assert dev.channel_mode == 'right'
        assert dev.profile.name == 'Buttkicker LFE'
        from telemffb.hw.ffb_rhino import EFFECT_SINE
        handle = dev.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0).start()
        out = output.render(0.2)
        assert not out[:, 0].any() and out[:, 1].any()


class TestPreview:
    def test_pulse_then_tone_then_silence(self):
        output = FakeOutput()
        preview = ShakerPreview(profile=ShakerProfile(f_res_hz=50.0, carrier_offset_pct=0.0),
                                gain=1.0, output=output)
        preview.start()
        assert output.running
        first = output.render(ShakerPreview.TONE_START_S)
        assert abs(first).max() > 0.5                     # the pulse
        preview.cue_tone()
        tone = output.render(ShakerPreview.TONE_LENGTH_S)
        assert abs(tone[-SR // 10:]).max() > 0.4          # the tone, still on at its end
        tail = output.render(0.3)
        assert abs(tail[-SR // 10:]).max() < 0.05         # then over
        preview.stop()
        assert not output.running

    def test_channel_mode_reaches_the_preview(self):
        output = FakeOutput()
        preview = ShakerPreview(gain=1.0, channel_mode='left', output=output)
        preview.start()
        out = output.render(0.1)
        assert out[:, 0].any() and not out[:, 1].any()
        preview.stop()


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


OUTPUTS = [AudioOutputInfo(''), AudioOutputInfo('Card A')]


class TestCard:
    def test_controls_sit_on_the_shaker_card_only(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        cards = dialog.device_cards.cards
        assert cards['shaker'].shaker is not None
        assert all(cards[r].shaker is None for r in cards if r != 'shaker')
        controls = dialog.device_cards.shaker_controls
        names = [controls.profile_combo.itemText(i) for i in range(controls.profile_combo.count())]
        assert names == [p.name for p in load_profiles(default_profiles_path())[0]]

    def test_defaults_load_when_nothing_is_stored(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        assert controls.gain_spin.value() == DEFAULT_GAIN
        assert controls.mode_value() == 'mono'
        assert not controls.pan_slider.isVisibleTo(dialog)

    def test_stored_values_load_and_save_back(self, monkeypatch):
        stored = _Settings({SETTING_GAIN: '2.5', SETTING_MODE: 'pan', SETTING_PAN: '0.3',
                            SETTING_PROFILE: 'Dayton DAEX-25'})
        _, dialog = _make_dialog(monkeypatch, stored, OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        assert controls.gain_spin.value() == 2.5
        assert controls.mode_value() == 'pan'
        assert controls.pan_slider.isVisibleTo(dialog)
        assert controls.pan_value() == pytest.approx(0.3)
        assert controls.profile_combo.currentText() == 'Dayton DAEX-25'
        controls.gain_spin.setValue(4.0)
        controls.set_mode_value('right')
        assert dialog.shaker_settings_values() == {
            SETTING_GAIN: 4.0, SETTING_MODE: 'right', SETTING_PAN: 0.3,
            SETTING_PROFILE: 'Dayton DAEX-25'}

    def test_test_button_follows_the_selection(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        controls = dialog.device_cards.shaker_controls
        assert not controls.test_button.isEnabled()
        dialog.cb_select_s.setCurrentIndex(2)
        assert controls.test_button.isEnabled()
        dialog.cb_select_s.setCurrentIndex(0)
        assert not controls.test_button.isEnabled()

    def test_test_button_plays_the_card_values_through_the_selected_output(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        made = []

        class FakePreview:
            TONE_START_S = ShakerPreview.TONE_START_S
            LENGTH_S = ShakerPreview.LENGTH_S

            def __init__(self, output_device=None, **kwargs):
                self.output_device = output_device
                self.kwargs = kwargs
                self.stopped = False
                made.append(self)

            def start(self):
                pass

            def cue_tone(self):
                pass

            def stop(self):
                self.stopped = True
        monkeypatch.setattr('telemffb.hw.ffb_shaker.ShakerPreview', FakePreview)
        dialog.cb_select_s.setCurrentIndex(2)
        controls = dialog.device_cards.shaker_controls
        controls.gain_spin.setValue(1.5)
        controls.set_mode_value('left')
        controls.test_button.click()
        assert len(made) == 1
        preview = made[0]
        assert preview.output_device == 'Card A'
        assert preview.kwargs['gain'] == 1.5 and preview.kwargs['channel_mode'] == 'left'
        assert preview.kwargs['profile'].name == controls.profile_combo.currentText()
        assert not controls.test_button.isEnabled()         # one at a time
        dialog._shaker_test_finished()
        assert preview.stopped
        assert controls.test_button.isEnabled()

    def test_the_default_output_previews_as_none(self, monkeypatch):
        _, dialog = _make_dialog(monkeypatch, _Settings(), OUTPUTS)
        made = []

        class FakePreview:
            TONE_START_S = 0.3
            LENGTH_S = 1.0

            def __init__(self, output_device=None, **kwargs):
                made.append(output_device)

            def start(self):
                pass

            def cue_tone(self):
                pass

            def stop(self):
                pass
        monkeypatch.setattr('telemffb.hw.ffb_shaker.ShakerPreview', FakePreview)
        dialog.cb_select_s.setCurrentIndex(1)
        dialog.device_cards.shaker_controls.test_button.click()
        assert made == [None]
        dialog._shaker_test_finished()

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
