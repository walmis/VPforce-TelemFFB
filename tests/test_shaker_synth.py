"""Shaker synthesis, offline: voices render deterministic blocks, the
mixer sums and clips them, and the output fan-out lands on the channels
the gains say.  No sound card; the output is a fake."""
import math

import numpy as np
import pytest

from telemffb.hw.shaker_synth import (
    ImpulseTrain, Oscillator, PhaseAccumulator, Route, ShakerSynth,
    SoundDeviceOutput, build_pulse_shape, clean_device_name,
)

pytestmark = [pytest.mark.unit]

SR = 48000
BLOCK = 512


def render(voice, samples, block=BLOCK):
    out = []
    done = 0
    while done < samples:
        n = min(block, samples - done)
        out.append(np.array(voice.render(n), dtype=np.float32, copy=True))
        done += n
    return np.concatenate(out)


class FakeOutput:
    def __init__(self):
        self.callback = None
        self.channels = None
        self.finished = None
        self.running = False
        self.fail_start = False
        self.starts = 0

    def start(self, callback, channels, finished_callback=None):
        self.starts += 1
        if self.fail_start:
            raise RuntimeError("no card")
        self.callback, self.channels, self.finished = callback, channels, finished_callback
        self.running = True

    def stop(self):
        self.running = False

    def pump(self, frames=BLOCK):
        out = np.zeros((frames, self.channels), dtype=np.float32)
        self.callback(out, frames, None, None)
        return out

    def vanish(self):
        self.running = False
        self.finished()


# ---------------------------------------------------------------------------

class TestPulseShape:
    def test_drive_length_is_halfwave_precise(self):
        shape = build_pulse_shape(SR, 50.0, 1, 1.0, 0.0, 0.0)
        assert shape.drive_end == round(SR / 100)          # one half wave at 50 Hz = 10 ms
        assert shape.total == shape.drive_end               # no brake: nothing after the drive
        shape2 = build_pulse_shape(SR, 50.0, 3, 1.0, 0.0, 0.0)
        assert shape2.drive_end == 3 * shape.drive_end

    def test_edges_never_overlap(self):
        shape = build_pulse_shape(SR, 50.0, 1, 1.0, attack_ms=100.0, release_ms=100.0)
        assert shape.drive_env[0] == 0.0
        assert shape.drive_env[shape.drive_end // 2] == pytest.approx(1.0)

    def test_brake_is_one_inverted_halfwave_after_the_gap(self):
        shape = build_pulse_shape(SR, 50.0, 1, 1.0, 0.0, 0.0, brake_amp=0.5, brake_delay_ms=1.0)
        assert shape.brake_start == shape.drive_end + round(SR / 1000)
        assert shape.brake_end - shape.brake_start == round(SR / 100)
        assert shape.brake_signal[1] < 0.0
        assert abs(shape.brake_signal).max() == pytest.approx(0.5, abs=0.01)


class TestOscillator:
    def test_steady_tone_ramps_to_amplitude_at_frequency(self):
        osc = Oscillator(SR)
        osc.set(50.0, 0.5, ramp_ms=10.0)
        out = render(osc, SR)
        tail = out[-SR // 2:]
        assert abs(tail).max() == pytest.approx(0.5, abs=0.01)
        spectrum = np.abs(np.fft.rfft(tail))
        peak_hz = np.argmax(spectrum) * SR / tail.size
        assert peak_hz == pytest.approx(50.0, abs=2.0)

    def test_ramp_is_gradual(self):
        osc = Oscillator(SR)
        osc.set(50.0, 1.0, ramp_ms=100.0)
        first = osc.render(BLOCK)
        assert abs(first).max() < 0.2

    def test_stop_ramps_to_silence(self):
        osc = Oscillator(SR)
        osc.set(50.0, 1.0, ramp_ms=1.0)
        render(osc, 2048)
        osc.stop(ramp_ms=10.0)
        assert not osc.is_silent
        render(osc, 2048)
        assert osc.is_silent
        assert not osc.render(BLOCK).any()

    def test_lifetime_ends_the_tone(self):
        osc = Oscillator(SR)
        osc.set(50.0, 1.0, ramp_ms=1.0)
        osc.expire_after(SR // 10)
        render(osc, SR // 10 + BLOCK)          # lifetime over, stop ramp begun
        render(osc, SR // 10)
        assert osc.is_silent

    def test_phase_is_continuous_across_blocks(self):
        osc = Oscillator(SR)
        osc.set(50.0, 1.0, ramp_ms=1.0)
        render(osc, 4096)
        a = np.array(osc.render(BLOCK), copy=True)
        b = np.array(osc.render(BLOCK), copy=True)
        joined = np.concatenate([a, b])
        assert abs(np.diff(joined)).max() < 2 * math.pi * 50.0 / SR * 1.5

    def test_one_shot_ends_itself(self):
        osc = Oscillator(SR)
        osc.trigger(50.0, 1.0, attack_ms=4.0, decay_ms=90.0)
        out = render(osc, SR // 5)
        assert abs(out).max() > 0.5
        assert osc.is_silent

    def test_pulse_plays_its_shape_then_ends(self):
        osc = Oscillator(SR)
        osc.trigger_pulse(50.0, 1, 1.0, attack_ms=0.0, release_ms=0.0, brake_amp=0.5, brake_delay_ms=0.0)
        shape = build_pulse_shape(SR, 50.0, 1, 1.0, 0.0, 0.0, 0.5, 0.0)
        out = render(osc, shape.total + BLOCK)
        assert out[: shape.drive_end].max() > 0.9           # the drive half wave, positive
        assert out[shape.brake_start: shape.brake_end].min() < -0.4   # the brake, inverted
        assert not out[shape.total:].any()
        assert osc.is_silent


class TestOutputSampleRate:
    """A card runs at one rate in shared mode; the output adopts it
    unless told otherwise."""

    def test_adopts_the_devices_default_rate(self, monkeypatch):
        monkeypatch.setattr(SoundDeviceOutput, 'default_samplerate', lambda self: 44100)
        assert SoundDeviceOutput('Card A').samplerate == 44100

    def test_a_requested_rate_wins(self, monkeypatch):
        monkeypatch.setattr(SoundDeviceOutput, 'default_samplerate', lambda self: 44100)
        assert SoundDeviceOutput('Card A', 48000).samplerate == 48000

    def test_an_unaskable_device_gets_the_usual_rate(self, monkeypatch):
        monkeypatch.setattr(SoundDeviceOutput, 'default_samplerate', lambda self: None)
        assert SoundDeviceOutput('Card A').samplerate == SoundDeviceOutput.FALLBACK_SAMPLERATE

    def test_asking_reads_the_devices_entry(self, monkeypatch):
        import sys
        import types
        fake = types.SimpleNamespace(
            query_devices=lambda index=None: {'default_samplerate': 44100.0, 'index': index},
            default=types.SimpleNamespace(device=(3, 7)))
        monkeypatch.setitem(sys.modules, 'sounddevice', fake)
        monkeypatch.setattr(SoundDeviceOutput, 'resolve', classmethod(lambda cls, spec: 5 if spec else None))
        assert SoundDeviceOutput('Card A').samplerate == 44100
        assert SoundDeviceOutput(None).samplerate == 44100

    def test_a_failing_query_is_no_rate(self, monkeypatch):
        import sys
        import types

        def boom(index=None):
            raise RuntimeError('no PortAudio')
        monkeypatch.setitem(sys.modules, 'sounddevice',
                            types.SimpleNamespace(query_devices=boom, default=types.SimpleNamespace(device=(0, 0))))
        monkeypatch.setattr(SoundDeviceOutput, 'resolve', classmethod(lambda cls, spec: None))
        assert SoundDeviceOutput(None).default_samplerate() is None


class TestPhaseAccumulator:
    def test_counts_cycles(self):
        acc = PhaseAccumulator()
        total = sum(acc.advance(25.0, 1 / 60) for _ in range(60))
        assert total == 25

    def test_zero_rate_or_time_counts_nothing(self):
        acc = PhaseAccumulator()
        assert acc.advance(0.0, 1.0) == 0
        assert acc.advance(10.0, 0.0) == 0

    def test_ramp_matches_the_integral(self):
        acc = PhaseAccumulator()
        steps, duration = 200, 10.0
        total = sum(acc.advance(25.0 * (i + 0.5) / steps, duration / steps) for i in range(steps))
        assert 124 <= total <= 126


class TestImpulseTrain:
    def _count_pulses(self, train, seconds):
        fired = []
        original = train._osc.trigger_pulse

        def counting(*a, **k):
            fired.append(a)
            return original(*a, **k)
        train._osc.trigger_pulse = counting
        render(train, int(seconds * SR))
        return len(fired)

    def test_one_pulse_per_cycle_of_the_rate(self):
        train = ImpulseTrain(SR)
        train.set_rate(10.0, load=1.0)
        assert self._count_pulses(train, 2.0) in (19, 20, 21)

    def test_no_load_no_pulses(self):
        train = ImpulseTrain(SR)
        train.set_rate(10.0, load=0.0)
        assert self._count_pulses(train, 1.0) == 0

    def test_stop_silences(self):
        train = ImpulseTrain(SR)
        train.set_rate(10.0, load=1.0)
        render(train, SR // 2)
        train.stop(ramp_ms=1.0)
        render(train, SR // 2)
        assert train.is_silent

    def test_lifetime(self):
        train = ImpulseTrain(SR)
        train.set_rate(10.0, load=1.0)
        train.expire_after(SR // 10)
        render(train, SR)
        assert train.is_silent


class TestDeviceListing:
    """PortAudio's raw list, as a driver on this machine reports it: one
    card under several host APIs, with its name padded under one."""

    RAW = [
        {'name': 'Speakers (USB Sound Device        )', 'hostapi': 0, 'max_output_channels': 2,
         'default_samplerate': 48000.0},
        {'name': 'Speakers (USB Sound Device)', 'hostapi': 1, 'max_output_channels': 8,
         'default_samplerate': 48000.0},
        {'name': 'Microphone (USB Sound Device)', 'hostapi': 0, 'max_output_channels': 0,
         'max_input_channels': 2, 'default_samplerate': 48000.0},
        {'name': 'Primary Sound Driver', 'hostapi': 2, 'max_output_channels': 2,
         'default_samplerate': 44100.0},
    ]
    APIS = [{'name': 'Windows WASAPI'}, {'name': 'Windows WDM-KS'}, {'name': 'Windows DirectSound'}]

    @pytest.fixture
    def fake_portaudio(self, monkeypatch):
        import sounddevice as sd
        monkeypatch.setattr(sd, 'query_devices', lambda *a, **k: list(self.RAW))
        monkeypatch.setattr(sd, 'query_hostapis', lambda *a, **k: list(self.APIS))

    def test_clean_name(self):
        assert clean_device_name('Speakers (USB Sound Device        )') == 'Speakers (USB Sound Device)'
        assert clean_device_name('  Realtek   Digital  Output ') == 'Realtek Digital Output'
        assert clean_device_name(None) == ''

    def test_a_padded_name_lists_once_under_the_preferred_api(self, fake_portaudio):
        listed = SoundDeviceOutput.list_devices()
        assert [(d.name, d.host_api, d.channels) for d in listed] == [
            ('Speakers (USB Sound Device)', 'Windows WASAPI', 2)]

    def test_inputs_and_virtual_outputs_are_left_out(self, fake_portaudio):
        names = [d.name for d in SoundDeviceOutput.list_devices(all_host_apis=True)]
        assert 'Microphone (USB Sound Device)' not in names
        assert 'Primary Sound Driver' in names          # only the deduplicated list hides it

    def test_a_stored_name_resolves_padded_or_clean(self, fake_portaudio):
        assert SoundDeviceOutput.resolve('Speakers (USB Sound Device        )') == 0
        assert SoundDeviceOutput.resolve('Speakers (USB Sound Device)') == 0
        assert SoundDeviceOutput.resolve('usb sound') == 0
        assert SoundDeviceOutput.resolve('') is None                 # the system default, by choice

    def test_a_missing_card_is_an_error_not_the_default_output(self, fake_portaudio):
        # a shaker rig on the desktop speakers is worse than a silent one
        with pytest.raises(LookupError):
            SoundDeviceOutput.resolve('no such card')

    def test_rescan_reinitializes_the_library(self, monkeypatch):
        import sounddevice as sd
        calls = []
        monkeypatch.setattr(sd, '_terminate', lambda: calls.append('down'))
        monkeypatch.setattr(sd, '_initialize', lambda: calls.append('up'))
        assert SoundDeviceOutput.rescan() is True
        assert calls == ['down', 'up']

        def boom():
            raise RuntimeError('busy')
        monkeypatch.setattr(sd, '_terminate', boom)
        assert SoundDeviceOutput.rescan() is False

    def test_refresh_rereads_only_while_no_stream_is_held(self, monkeypatch):
        calls = []
        monkeypatch.setattr(SoundDeviceOutput, 'rescan', staticmethod(lambda: calls.append(1) or True))
        out = SoundDeviceOutput('Card A', 48000)
        monkeypatch.setattr(SoundDeviceOutput, '_held', 1)
        assert out.refresh() is False and calls == []
        monkeypatch.setattr(SoundDeviceOutput, '_held', 0)
        assert out.refresh() is True and calls == [1]


class TestRoutes:
    def test_channel_count_follows_the_routes(self):
        synth = ShakerSynth(SR, BLOCK, routes=[Route(0, 'a'), Route(5, 'b', 0.5)])
        assert synth.channels == 6
        assert synth.groups == ['a', 'b']
        synth.set_routes([Route(1, 'a')])
        assert synth.channels == 2
        synth.set_routes([Route(0, 'a')], channels=4)
        assert synth.channels == 4

    def test_groups_render_once_and_fan_out(self):
        synth = ShakerSynth(SR, BLOCK, routes=[Route(0, 'a'), Route(1, 'a', 0.5), Route(2, 'b')])
        synth.voice('x', Oscillator, 'a').set(50.0, 0.8, ramp_ms=1.0)
        synth.voice('y', Oscillator, 'b').set(50.0, 0.4, ramp_ms=1.0)
        for _ in range(10):
            synth.render_block()
        blocks = [np.array(synth.render_block(), copy=True) for _ in range(3)]
        peaks = np.max([abs(b).max(axis=0) for b in blocks], axis=0)
        assert peaks == pytest.approx([0.8, 0.4, 0.4], abs=0.02)

    def test_a_delayed_route_lags_and_plays_out_its_tail(self):
        synth = ShakerSynth(SR, BLOCK, routes=[Route(0, 'g'), Route(1, 'g', 1.0, 1000)])
        osc = synth.voice('x', Oscillator, 'g')
        osc.trigger_pulse(50.0, 1, 1.0, attack_ms=0.0, release_ms=0.0)
        out = np.concatenate([np.array(synth.render_block(), copy=True) for _ in range(6)])
        direct = np.argmax(abs(out[:, 0]) > 0.1)
        late = np.argmax(abs(out[:, 1]) > 0.1)
        assert late - direct == 1000
        assert abs(out[:, 1]).max() == pytest.approx(abs(out[:, 0]).max(), abs=0.01)
        # the group is silent now; the delayed channel still carried the pulse
        assert osc.is_silent

    def test_a_voice_moves_group_when_remade(self):
        synth = ShakerSynth(SR, BLOCK, routes=[Route(0, 'a'), Route(1, 'b')])
        v1 = synth.voice('x', Oscillator, 'a')
        assert synth.voice('x', Oscillator, 'a') is v1
        v2 = synth.voice('x', Oscillator, 'b')
        assert v2 is not v1


class TestShakerSynth:
    def test_voices_are_made_on_demand_and_remade_on_kind_change(self):
        synth = ShakerSynth(SR, BLOCK)
        a = synth.voice('k', Oscillator)
        assert synth.voice('k', Oscillator) is a
        b = synth.voice('k', ImpulseTrain)
        assert b is not a and isinstance(b, ImpulseTrain)
        synth.remove('k')
        assert synth.get('k') is None
        assert synth.keys() == []

    def test_mix_sums_and_master_gain_scales(self):
        synth = ShakerSynth(SR, BLOCK)
        for key in ('a', 'b'):
            synth.voice(key, Oscillator).set(50.0, 0.4, ramp_ms=1.0)
        for _ in range(10):
            synth.render_block()
        peak = max(synth.render_block().max() for _ in range(3))   # three blocks span a 50 Hz period
        assert peak == pytest.approx(0.8, abs=0.02)
        synth.set_master_gain(0.5)
        peak = max(synth.render_block().max() for _ in range(3))
        assert peak == pytest.approx(0.4, abs=0.02)

    def test_limiter_holds_the_sum_in_range_without_flattening_it(self):
        synth = ShakerSynth(SR, BLOCK)
        for key in ('a', 'b'):
            synth.voice(key, Oscillator).set(50.0, 0.8, ramp_ms=1.0)
        blocks = [np.array(synth.render_block(), copy=True) for _ in range(20)]
        out = np.concatenate(blocks[5:]).astype(np.float64)
        assert abs(out).max() <= 1.0
        assert abs(out).max() > 0.9
        crest = abs(out).max() / np.sqrt(np.mean(out * out))
        assert crest == pytest.approx(np.sqrt(2), abs=0.05)   # still a sine: limited, not clipped
        assert synth.limiter_gains[0] < 0.7

    def test_limiter_lets_go_after_the_peak(self):
        synth = ShakerSynth(SR, BLOCK)
        loud = synth.voice('a', Oscillator)
        loud.set(50.0, 1.0, ramp_ms=1.0)
        synth.set_master_gain(2.0)
        for _ in range(10):
            synth.render_block()
        assert synth.limiter_gains[0] == pytest.approx(0.5, abs=0.05)
        synth.set_master_gain(0.5)
        for _ in range(int(4 * ShakerSynth.LIMITER_RELEASE_S * SR / BLOCK)):   # four time constants
            synth.render_block()
        assert synth.limiter_gains[0] > 0.97
        peak = max(synth.render_block().max() for _ in range(3))
        assert peak == pytest.approx(0.5, abs=0.03)

    def test_silent_voices_cost_nothing_and_silence_ramps_everything(self):
        synth = ShakerSynth(SR, BLOCK)
        synth.voice('a', Oscillator).set(50.0, 1.0, ramp_ms=1.0)
        synth.render_block()
        synth.silence(ramp_ms=1.0)
        for _ in range(4):
            synth.render_block()
        assert not synth.render_block().any()

    def test_callback_fans_out_by_route(self):
        output = FakeOutput()
        synth = ShakerSynth(SR, BLOCK, routes=[Route(0, ''), Route(1, '', 0.5)], output=output)
        synth.voice('a', Oscillator).set(50.0, 1.0, ramp_ms=1.0)
        synth.start()
        assert output.channels == 2
        for _ in range(4):
            output.pump()
        blocks = [output.pump() for _ in range(3)]
        assert max(b[:, 0].max() for b in blocks) == pytest.approx(1.0, abs=0.01)
        assert max(b[:, 1].max() for b in blocks) == pytest.approx(0.5, abs=0.01)
        synth.set_routes([Route(1, '')], channels=2)
        out = output.pump()
        assert not out[:, 0].any() and out[:, 1].any()

    def test_callback_never_writes_past_the_stream_width(self):
        output = FakeOutput()
        synth = ShakerSynth(SR, BLOCK, routes=[Route(0, ''), Route(1, '')], output=output)
        synth.voice('a', Oscillator).set(50.0, 1.0, ramp_ms=1.0)
        synth.start()
        out = np.zeros((BLOCK, 4), dtype=np.float32)
        synth._callback(out, BLOCK, None, None)          # a wider stream than the routes
        assert out[:, 2:].any() is np.False_ or not out[:, 2:].any()
        synth.set_routes([Route(3, '')], channels=4)
        narrow = np.zeros((BLOCK, 2), dtype=np.float32)
        synth._callback(narrow, BLOCK, None, None)       # a narrower one: nothing spills
        assert narrow.shape == (BLOCK, 2)

    def test_odd_frame_counts(self):
        output = FakeOutput()
        synth = ShakerSynth(SR, BLOCK, output=output)
        synth.voice('a', Oscillator).set(50.0, 1.0, ramp_ms=1.0)
        synth.start()
        out = output.pump(frames=300)
        assert out.shape == (300, 1)

    def test_finished_hook_fires_only_for_unrequested_ends(self):
        output = FakeOutput()
        synth = ShakerSynth(SR, BLOCK, output=output)
        seen = []
        synth.on_finished = lambda: seen.append(True)
        synth.start()
        synth.stop()
        assert seen == []
        synth.start()
        output.vanish()
        assert seen == [True]
        assert not synth.running

    def test_limiters_are_per_channel(self):
        synth = ShakerSynth(SR, BLOCK, routes=[Route(0, 'loud', 2.0), Route(1, 'quiet', 0.5)])
        synth.voice('a', Oscillator, 'loud').set(50.0, 1.0, ramp_ms=1.0)
        synth.voice('b', Oscillator, 'quiet').set(50.0, 1.0, ramp_ms=1.0)
        for _ in range(10):
            synth.render_block()
        gains = synth.limiter_gains
        assert gains[0] < 0.6 and gains[1] == 1.0

    def test_start_without_output_is_an_error(self):
        with pytest.raises(RuntimeError):
            ShakerSynth(SR, BLOCK).start()
