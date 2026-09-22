"""Shaker backend contract tests.

ShakerFFBDevice/ShakerEffectHandle against a fake audio output: the
backend seam, the effect lifecycle, the rules that turn an effect's own
parameters into a voice (transient, tone, folded tone, pulse train, the
AC-coupled constant), timed effects, output loss and recovery, and the
effect facade driving the device the way the aircraft modules do."""
import numpy as np
import pytest

from telemffb.hw.ffb_backend import BaseEffectHandle, BaseFFBDevice, DeviceCapabilities
from telemffb.hw.ffb_rhino import (
    EFFECT_CONSTANT, EFFECT_DAMPER, EFFECT_DETENT, EFFECT_SINE, EFFECT_SPRING,
    EFFECT_SQUARE, EFFECT_TRIANGLE, FFBReport_SetCondition, FFBReport_SetEnvelope,
    HapticEffect,
)
from telemffb.hw.ffb_shaker import (
    DEFAULT_GAIN, DEFAULT_PLACEMENT_DELAY_MS, EVERYWHERE, PLACEMENT_CHOICES,
    SHAKER_CAPABILITIES, TRANSIENT_MS, Placement, ShakerEffectHandle,
    ShakerFFBDevice, ShakerOutputError, ShakerProfile, Transducer, contact_of,
    default_profiles_path, load_profiles, placement_from_choice, profile_from_dict,
)
from telemffb.hw.shaker_synth import ImpulseTrain, Oscillator
from telemffb.utils import Dispenser

pytestmark = [pytest.mark.unit]

SR = 48000
BLOCK = 512

#: a profile with round numbers: tones from 20 to 100 Hz, pulses at 50 Hz
PROFILE = ShakerProfile(f_res_hz=50.0, carrier_offset_pct=0.0,
                        band_low_hz=20.0, band_high_hz=100.0, halfwaves=1,
                        attack_ms=0.0, release_ms=0.0)


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

    def render(self, seconds):
        frames = int(seconds * SR)
        blocks = []
        while frames > 0:
            n = min(BLOCK, frames)
            blocks.append(self.pump(n)[:, 0].copy())
            frames -= n
        return np.concatenate(blocks)

    def vanish(self):
        self.running = False
        self.finished()


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def output():
    return FakeOutput()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def device(output, clock):
    return ShakerFFBDevice(output=output, profile=PROFILE, gain=1.0,
                           reconnect_interval_ms=0, clock=clock)


def settle(device, blocks=30):
    for _ in range(blocks):
        device.synth.render_block()


# ---------------------------------------------------------------------------

class TestConformance:
    def test_is_a_backend(self):
        assert issubclass(ShakerFFBDevice, BaseFFBDevice)
        assert issubclass(ShakerEffectHandle, BaseEffectHandle)

    def test_capabilities_are_all_off(self, device):
        assert device.caps is SHAKER_CAPABILITIES
        assert device.caps == DeviceCapabilities()
        assert device.supports_axis_override() is False

    def test_vpforce_only_surface_is_safe(self, device):
        device.set_deadzone(100)
        device.set_gain(1, 50)
        device.send_axis_override(1, 100)
        device.clear_axis_override()
        assert device.get_gains() is None
        assert device.get_firmware_version() is None
        assert device.serial is None

    def test_input_reads_as_an_untouched_device(self, device):
        snap = device.get_input()
        assert snap.axisXY() == (0.0, 0.0)
        assert snap.CP_scaled_axisXY() == (0.0, 0.0)
        assert snap.isButtonPressed(1) is False
        assert snap.getPressedButtons() == []
        assert snap.axisOverrideActive() is False
        assert snap.forceXY() is None

    def test_identity(self):
        dev = ShakerFFBDevice(output_device='Speakers (USB)', output=FakeOutput(),
                              reconnect_interval_ms=0)
        assert 'Speakers (USB)' in dev.info.product_string
        assert dev.info.path == b'audio:Speakers (USB)'
        assert dev.info.vidpid() == 'audio'

    def test_default_gain_lifts_stick_intensities(self):
        dev = ShakerFFBDevice(output=FakeOutput(), reconnect_interval_ms=0)
        assert DEFAULT_GAIN > 1.0
        assert dev.synth._gain == DEFAULT_GAIN


class TestOpen:
    def test_opens_stereo_when_the_output_allows(self, device, output):
        assert device.connected
        assert output.channels == 2

    def test_mono_output_when_stereo_is_refused(self):
        output = FakeOutput()
        output.supports_channels = lambda n: n == 1
        ShakerFFBDevice(output=output, reconnect_interval_ms=0)
        assert output.channels == 1

    def test_failed_open_raises(self):
        output = FakeOutput()
        output.fail_start = True
        with pytest.raises(ShakerOutputError):
            ShakerFFBDevice(output=output, reconnect_interval_ms=0)

    def test_transducer_rows_are_live(self, device, output):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0).start()
        output.render(0.2)
        device.set_transducers([Transducer('Right only', 1)])
        out = output.pump()
        assert not out[:, 0].any() and out[:, 1].any()
        assert handle.started and handle.kind == 'tone'

    def test_one_render_per_profile_not_per_transducer(self, output, clock):
        light = ShakerProfile(name='light', band_low_hz=20.0, band_high_hz=100.0)
        heavy = ShakerProfile(name='heavy', band_low_hz=10.0, band_high_hz=60.0)
        rows = [Transducer('a', 0, 1.0, 'light'), Transducer('b', 1, 0.5, 'light'),
                Transducer('c', 2, 1.0, 'heavy')]
        dev = ShakerFFBDevice(output=output, transducers=rows, profiles=[light, heavy],
                              gain=1.0, reconnect_interval_ms=0, clock=clock)
        handle = dev.create_effect(EFFECT_SINE)
        handle.setPeriodic(80.0, 1.0, 0).start()
        assert set(handle.voices) == {'light|all', 'heavy|all'}
        assert handle.voices['light|all'].frequency == pytest.approx(80.0)
        assert handle.voices['heavy|all'].frequency == pytest.approx(40.0)   # folded for the heavy band
        out = np.concatenate([output.pump() for _ in range(30)])
        peaks = [abs(out[:, c]).max() for c in range(3)]
        assert peaks[0] == pytest.approx(1.0, abs=0.02)
        assert peaks[1] == pytest.approx(0.5, abs=0.02)
        assert peaks[2] == pytest.approx(1.0, abs=0.02)

    def test_shutdown_stops_the_output(self, device, output):
        device.shutdown()
        assert not output.running
        assert not device.connected


class TestEffectLifecycle:
    def test_conditions_and_extras_are_declined(self, device):
        for t in (EFFECT_SPRING, EFFECT_DAMPER, EFFECT_DETENT):
            assert device.create_effect(t) is None

    def test_periodic_and_constant_are_accepted(self, device):
        for t in (EFFECT_SINE, EFFECT_SQUARE, EFFECT_TRIANGLE, EFFECT_CONSTANT):
            assert isinstance(device.create_effect(t), ShakerEffectHandle)

    def test_no_effects_while_disconnected(self, device, output):
        output.vanish()
        assert device.create_effect(EFFECT_SINE) is None

    def test_start_stop_destroy(self, device, output):
        handle = device.create_effect(EFFECT_SINE)
        assert bool(handle)
        handle.setPeriodic(50.0, 1.0, 0)
        assert not handle.started and handle.voice is None
        handle.start()
        assert handle.started
        assert isinstance(handle.voice, Oscillator)
        assert abs(output.render(0.2)).max() > 0.9

        handle.stop()
        assert not handle.started
        output.render(0.2)
        assert not output.pump().any()

        handle.destroy()
        assert not bool(handle)
        assert device.synth.keys() == []

    def test_invalidate_drops_the_voice_and_forget_playback_keeps_it(self, device):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0).start()
        handle.forget_playback()
        assert not handle.started
        assert device.synth.keys() == [f"{handle.effect_id}:{device.profile.name}|all"]
        handle.invalidate()
        assert not bool(handle)
        assert device.synth.keys() == []

    def test_reset_effects_invalidates_everything(self, device):
        a = device.create_effect(EFFECT_SINE)
        a.setPeriodic(50.0, 1.0, 0).start()
        device.reset_effects()
        assert not bool(a)
        assert device.synth.keys() == []

    def test_handles_are_tracked_weakly(self, device):
        handle = device.create_effect(EFFECT_SINE)
        assert len(device._handles) == 1
        del handle
        import gc
        gc.collect()
        assert len(device._handles) == 0


class TestRules:
    """The voice follows from the effect's parameters and the profile."""

    def test_in_band_periodic_is_a_tone_at_its_frequency(self, device):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(60.0, 0.5, 0).start()
        assert handle.kind == 'tone'
        assert handle.voice.frequency == pytest.approx(60.0)
        settle(device)
        assert handle.voice.amplitude == pytest.approx(0.5, abs=0.01)

    def test_above_band_folds_down_by_octaves(self, device):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(180.0, 1.0, 0).start()
        assert handle.kind == 'tone'
        assert handle.voice.frequency == pytest.approx(90.0)
        handle.setPeriodic(500.0, 1.0, 0)
        assert handle.voice.frequency == pytest.approx(62.5)

    def test_below_band_is_a_pulse_per_cycle(self, device):
        handle = device.create_effect(EFFECT_SQUARE)
        handle.setPeriodic(10.0, 1.0, 0).start()          # gunfire
        assert handle.kind == 'train'
        assert isinstance(handle.voice, ImpulseTrain)
        assert handle.voice.rate_hz == pytest.approx(10.0)

    def test_a_short_periodic_is_one_pulse(self, device, output):
        handle = device.create_effect(EFFECT_SQUARE)
        handle.setPeriodic(15.0, 1.0, 0, duration=80).start()   # runway bump
        assert handle.kind == 'pulse'
        voice = handle.voice
        assert not voice.is_silent
        out = output.render(0.5)
        assert abs(out).max() > 0.9
        assert voice.is_silent                       # the pulse ended itself
        # an update or a repeated start while started never re-fires
        handle.setPeriodic(15.0, 1.0, 0, duration=80)
        handle.start()
        assert voice.is_silent
        handle.stop()
        handle.start()
        assert not voice.is_silent

    def test_a_pulse_carrier_is_the_folded_frequency_or_the_profile_carrier(self, device):
        low = device.create_effect(EFFECT_SQUARE)
        low.setPeriodic(15.0, 1.0, 0, duration=80).start()
        assert low.voice.frequency == pytest.approx(PROFILE.carrier_hz)
        high = device.create_effect(EFFECT_SQUARE)
        high.setPeriodic(240.0, 1.0, 0, duration=50).start()
        assert high.voice.frequency == pytest.approx(60.0)

    def test_transient_threshold(self, device):
        short = device.create_effect(EFFECT_SQUARE)
        short.setPeriodic(50.0, 1.0, 0, duration=TRANSIENT_MS).start()
        assert short.kind == 'pulse'
        long = device.create_effect(EFFECT_SQUARE)
        long.setPeriodic(50.0, 1.0, 0, duration=TRANSIENT_MS + 1).start()
        assert long.kind == 'tone'

    def test_a_kind_change_replaces_the_voice(self, device):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(8.0, 1.0, 0).start()             # idle: pulses
        assert isinstance(handle.voice, ImpulseTrain)
        handle.setPeriodic(40.0, 1.0, 0)                    # cruise: a tone
        assert isinstance(handle.voice, Oscillator)
        assert handle.voice.frequency == pytest.approx(40.0)
        assert len(device.synth.keys()) == 1

    def test_updates_follow_while_started(self, device):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0).start()
        voice = handle.voice
        handle.setPeriodic(70.0, 1.0, 0)
        assert voice.frequency == pytest.approx(70.0)
        handle.setEffect(gain=2048)
        settle(device)
        assert voice.amplitude == pytest.approx(0.5, abs=0.01)

    def test_updates_do_nothing_before_start(self, device):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0)
        handle.setPeriodic(70.0, 1.0, 0)
        assert handle.voice is None and device.synth.keys() == []

    def test_timed_effect_ends_by_itself(self, device, output):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0, duration=400).start()
        voice = handle.voice
        output.render(0.2)
        assert not voice.is_silent
        output.render(0.4)
        assert voice.is_silent

    def test_envelope_attack_sets_the_ramp(self, device):
        handle = device.create_effect(EFFECT_SINE)
        handle.setEnvelope(FFBReport_SetEnvelope(attackFromForce=0, decayToForce=0,
                                                 attackTime=500, decayTime=0))
        handle.setPeriodic(50.0, 1.0, 0).start()
        device.synth.render_block()
        assert handle.voice.amplitude < 0.05

    def test_monitor_readouts(self, device):
        tone = device.create_effect(EFFECT_SINE)
        tone.setPeriodic(50.0, 0.4, 0).start()
        assert tone.intensity == pytest.approx(0.4)
        assert tone.axis_gains is None
        tone.setEffect(gain=2048)
        assert tone.intensity == pytest.approx(0.2)
        quiet = device.create_effect(EFFECT_CONSTANT)
        assert quiet.intensity == 0.0                # nothing written yet

    def test_condition_is_ignored(self, device):
        handle = device.create_effect(EFFECT_SINE)
        handle.setCondition(FFBReport_SetCondition(parameterBlockOffset=0, positiveCoefficient=4096))
        handle.setPeriodic(50.0, 1.0, 0).start()
        assert handle.kind == 'tone'

    def test_profile_gain_scales_every_voice(self, output, clock):
        quiet = ShakerProfile(gain=0.5, band_low_hz=20.0, band_high_hz=100.0)
        dev = ShakerFFBDevice(output=output, profile=quiet, gain=1.0,
                              reconnect_interval_ms=0, clock=clock)
        handle = dev.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0).start()
        settle(dev)
        assert handle.voice.amplitude == pytest.approx(0.5, abs=0.01)


RIG = [Transducer('Buttkicker', 0, 1.0, 'heavy', 'seat'),
       Transducer('Dayton L', 1, 1.0, 'light', 'seat back left'),
       Transducer('Dayton R', 2, 1.0, 'light', 'seat back right'),
       Transducer('Pedals', 3, 1.0, 'light', 'floor')]
LIGHT = ShakerProfile(name='light', f_res_hz=50.0, carrier_offset_pct=0.0,
                      band_low_hz=20.0, band_high_hz=100.0, halfwaves=1, attack_ms=0.0, release_ms=0.0)
HEAVY = ShakerProfile(name='heavy', f_res_hz=50.0, carrier_offset_pct=0.0,
                      band_low_hz=20.0, band_high_hz=100.0, halfwaves=1, attack_ms=0.0, release_ms=0.0)


def peaks(output, seconds=0.2):
    out = np.concatenate([output.pump() for _ in range(int(seconds * SR / BLOCK) + 1)])
    return [float(abs(out[:, c]).max()) for c in range(out.shape[1])]


class TestPlacement:
    """Where an effect plays is asked of a resolver by the effect's name;
    the rows a placement reaches get its voices, no other row does."""

    def test_contacts_and_choices(self):
        assert contact_of('seat back left') == 'seat back'
        assert contact_of('floor right') == 'floor'
        assert contact_of('seat') == 'seat'
        assert contact_of('back left') == 'seat back'          # an old name
        assert placement_from_choice('all') is EVERYWHERE
        both = placement_from_choice('seat + floor')
        assert both.contacts == {'seat', 'floor'} and not both.delayed
        rolled = placement_from_choice('floor, then seat back')
        assert rolled.contacts == {'floor', 'seat back'}
        assert rolled.delayed == {'seat back'} and rolled.delay_ms == DEFAULT_PLACEMENT_DELAY_MS
        assert placement_from_choice('no such place') is EVERYWHERE
        assert set(PLACEMENT_CHOICES) >= {'all', 'seat', 'seat back', 'floor'}
        assert Placement(frozenset({'seat'})).key != Placement(frozenset({'floor'})).key

    def _device(self, output, clock, resolver):
        return ShakerFFBDevice(output=output, transducers=RIG, profiles=[LIGHT, HEAVY], gain=1.0,
                               placement_resolver=resolver, reconnect_interval_ms=0, clock=clock)

    def test_without_a_resolver_everything_plays_everywhere(self, output, clock):
        dev = self._device(output, clock, None)
        handle = dev.create_effect(EFFECT_SINE)
        handle.label = 'buffeting'
        handle.setPeriodic(50.0, 1.0, 0).start()
        assert all(p > 0.9 for p in peaks(output))

    def test_a_placement_reaches_only_its_rows(self, output, clock):
        table = {'buffeting': placement_from_choice('seat back'),
                 'prop_rpm0-1': placement_from_choice('seat'),
                 'gunfire': placement_from_choice('seat + floor')}
        dev = self._device(output, clock, lambda name: table.get(name, EVERYWHERE))
        expected = {'buffeting': [1, 2], 'prop_rpm0-1': [0], 'gunfire': [0, 3]}
        for name, hz in (('buffeting', 50.0), ('prop_rpm0-1', 60.0), ('gunfire', 70.0)):
            h = dev.create_effect(EFFECT_SINE)
            h.label = name
            h.setPeriodic(hz, 1.0, 0).start()
            heard = [c for c, p in enumerate(peaks(output)) if p > 0.9]
            assert heard == expected[name], name
            h.stop()
            peaks(output, 0.3)                                   # let the stop ramp out

    def test_one_render_per_profile_and_placement_pair(self, output, clock):
        dev = self._device(output, clock, lambda name: placement_from_choice('seat back') if name == 'a'
                           else placement_from_choice('seat back + floor'))
        a = dev.create_effect(EFFECT_SINE)
        a.label = 'a'
        a.setPeriodic(50.0, 1.0, 0).start()
        b = dev.create_effect(EFFECT_SINE)
        b.label = 'b'
        b.setPeriodic(50.0, 1.0, 0).start()
        assert list(a.voices) == ['light|seat back']
        assert list(b.voices) == ['light|seat back+floor']      # the heavy seat row is not reached
        assert len(dev.synth.keys()) == 2

    def test_a_placement_no_row_can_honor_plays_everywhere(self, output, clock):
        rows = [Transducer('Only', 0, 1.0, 'light', 'seat')]
        dev = ShakerFFBDevice(output=output, transducers=rows, profiles=[LIGHT], gain=1.0,
                              placement_resolver=lambda n: placement_from_choice('floor'),
                              reconnect_interval_ms=0, clock=clock)
        h = dev.create_effect(EFFECT_SINE)
        h.label = 'x'
        h.setPeriodic(50.0, 1.0, 0).start()
        assert peaks(output)[0] > 0.9

    def test_the_delayed_contact_hears_it_late(self, output, clock):
        dev = self._device(output, clock, lambda n: placement_from_choice('floor, then seat back'))
        h = dev.create_effect(EFFECT_SQUARE)
        h.label = 'runway_bump0'
        h.setPeriodic(15.0, 1.0, 0, duration=80).start()               # one pulse
        out = np.concatenate([output.pump() for _ in range(40)])

        def onset(c):
            return int(np.argmax(abs(out[:, c]) > 0.1))
        assert abs(out[:, 0]).max() < 1e-6                              # the seat is not in this placement
        expected = int(round(DEFAULT_PLACEMENT_DELAY_MS * SR / 1000.0))
        assert abs((onset(1) - onset(3)) - expected) <= 2                # the seat back hears it one delay later
        assert abs((onset(2) - onset(3)) - expected) <= 2
        assert abs(out[:, 1]).max() == pytest.approx(abs(out[:, 3]).max(), abs=0.02)

    def test_forgetting_placements_re_resolves_started_effects(self, output, clock):
        table = {'x': placement_from_choice('seat')}
        dev = self._device(output, clock, lambda n: table.get(n, EVERYWHERE))
        h = dev.create_effect(EFFECT_SINE)
        h.label = 'x'
        h.setPeriodic(50.0, 1.0, 0).start()
        assert [c for c, p in enumerate(peaks(output)) if p > 0.9] == [0]
        table['x'] = placement_from_choice('floor')
        dev.forget_placements()
        assert h.started
        assert [c for c, p in enumerate(peaks(output, 0.4)) if p > 0.9] == [3]

    def test_a_failing_resolver_plays_everywhere(self, output, clock):
        def boom(name):
            raise RuntimeError('settings not ready')
        dev = self._device(output, clock, boom)
        h = dev.create_effect(EFFECT_SINE)
        h.label = 'x'
        h.setPeriodic(50.0, 1.0, 0).start()
        assert all(p > 0.9 for p in peaks(output))


class TestConstantForce:
    """A steady push is felt as its changes: the magnitude is AC-coupled."""

    def test_a_step_is_a_thump_that_fades(self, device, output, clock):
        handle = device.create_effect(EFFECT_CONSTANT)
        handle.setConstantForce(0.5, 0).start()             # touchdown
        assert handle.kind == 'constant'
        assert handle.voice.frequency == pytest.approx(PROFILE.carrier_hz)
        assert abs(output.render(0.1)).max() > 0.5
        output.render(ShakerEffectHandle.AC_HOLD_S + 0.1)    # the hold, then the ramp out
        assert abs(output.render(0.3)).max() < 0.05          # held nothing: it faded

    def test_a_steady_load_goes_quiet(self, device, output, clock):
        handle = device.create_effect(EFFECT_CONSTANT)
        handle.setConstantForce(0.6, 0).start()              # g onset
        for _ in range(60):                                  # a second of frames, unchanged
            clock.t += 1 / 60
            handle.setConstantForce(0.6, 0)
            output.render(1 / 60)
        assert abs(output.render(0.1)).max() < 0.05

    def test_texture_keeps_rumbling(self, device, output, clock):
        rng = np.random.default_rng(5)
        handle = device.create_effect(EFFECT_CONSTANT)
        handle.setConstantForce(0.3, 0).start()              # runway rumble
        output.render(0.5)
        peaks = []
        for _ in range(60):
            clock.t += 1 / 60
            handle.setConstantForce(0.3 + 0.15 * rng.standard_normal(), 0)
            peaks.append(abs(output.render(1 / 60)).max())
        assert np.median(peaks) > 0.2

    def test_sign_does_not_matter(self, device, output, clock):
        handle = device.create_effect(EFFECT_CONSTANT)
        handle.setConstantForce(-0.5, 180).start()
        assert abs(output.render(0.1)).max() > 0.5


class TestOutputLoss:
    def test_loss_is_announced_once_and_recovered(self, device, output):
        seen = []
        device.deviceConnected.connect(lambda ok: seen.append(ok))
        reconnected = []
        device.deviceReconnected.connect(lambda: reconnected.append(True))
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(50.0, 1.0, 0).start()

        output.vanish()
        assert not device.connected
        output.fail_start = True
        device._tick()
        device._tick()
        assert seen == [False]
        assert reconnected == []

        output.fail_start = False
        device._tick()
        assert device.connected
        assert seen == [False, True]
        assert reconnected == [True]
        handle.forget_playback()
        handle.start()
        assert abs(output.render(0.2)).max() > 0.9

    def test_a_requested_stop_is_not_a_loss(self, device, output):
        device.shutdown()
        device._tick()
        assert not device.connected


class TestProfiles:
    def test_carrier_brake_and_fold(self):
        p = ShakerProfile(f_res_hz=40.0, carrier_offset_pct=25.0, brake_enabled=True,
                          brake_amp_pct=50.0, band_low_hz=20.0, band_high_hz=100.0)
        assert p.carrier_hz == pytest.approx(50.0)
        assert p.brake_amp(0.8) == pytest.approx(0.4)
        assert ShakerProfile(brake_enabled=False).brake_amp(1.0) == 0.0
        assert p.fold(60.0) == 60.0
        assert p.fold(180.0) == 90.0
        assert p.fold(1000.0) == 62.5
        assert p.fold(10.0) == 10.0                  # below the band is not folding's job

    def test_fold_never_drops_below_the_floor(self):
        narrow = ShakerProfile(band_low_hz=60.0, band_high_hz=100.0)
        assert narrow.fold(110.0) == 110.0           # halving would land under the floor

    def test_unknown_fields_are_dropped(self):
        p = profile_from_dict({'name': 'x', 'halfwaves': 3, 'schema_version': 1, 'created_iso': ''})
        assert p.name == 'x' and p.halfwaves == 3

    def test_bundled_pack_loads_with_bands(self):
        profiles, active = load_profiles(default_profiles_path())
        assert active in {p.name for p in profiles}
        for p in profiles:
            assert 0 < p.band_low_hz < p.band_high_hz

    def test_missing_pack_falls_back(self, tmp_path):
        profiles, active = load_profiles(str(tmp_path / 'none.json'))
        assert profiles[0].name == active

    def test_profile_shapes_the_pulse(self, output):
        brake = ShakerProfile(halfwaves=1, brake_enabled=True, brake_amp_pct=100.0, brake_delay_ms=0.0,
                              attack_ms=0.0, release_ms=0.0, band_low_hz=20.0, band_high_hz=100.0)
        dev = ShakerFFBDevice(output=output, profile=brake, gain=1.0, reconnect_interval_ms=0)
        handle = dev.create_effect(EFFECT_SQUARE)
        handle.setPeriodic(15.0, 1.0, 0, duration=80).start()
        out = output.render(0.1)
        assert out.max() > 0.9 and out.min() < -0.9   # drive then a full brake half wave


class TestFacade:
    @pytest.fixture
    def facade(self, device, monkeypatch):
        monkeypatch.setattr(HapticEffect, 'device', device)
        return Dispenser(HapticEffect)

    def test_effects_reach_their_voices_and_carry_their_name(self, facade, device, output):
        facade['buffeting'].periodic(50.0, 1.0, 0).start()
        assert facade['buffeting'].started
        handle = facade['buffeting']._h_effect
        assert handle.kind == 'tone'
        assert handle.label == 'buffeting'            # advisory: rendering never reads it
        assert 'buffeting' in repr(handle)
        assert abs(output.render(0.2)).max() > 0.9
        facade['buffeting'].stop()
        assert not facade['buffeting'].started

    def test_condition_effects_are_harmless(self, facade):
        facade['spring'].spring(coef_x=4096, coef_y=4096).start()
        assert not facade['spring'].started
        assert facade['spring']._h_effect is None
        facade['spring'].stop()
        facade['spring'].destroy()

    def test_per_frame_updates_track(self, facade):
        for hz in (30.0, 40.0, 50.0):
            facade['prop_rpm0-1'].periodic(hz, 0.5, 0).start()
        assert facade['prop_rpm0-1']._h_effect.voice.frequency == pytest.approx(50.0)

    def test_constant_through_the_facade(self, facade, output):
        facade['touchdown'].constant(0.7, 0).start()
        assert facade['touchdown']._h_effect.kind == 'constant'
        assert abs(output.render(0.1)).max() > 0.5

    def test_open_shaker_attaches_the_device(self, monkeypatch):
        output = FakeOutput()
        monkeypatch.setattr(HapticEffect, 'device', None)
        dev = HapticEffect.open_shaker('card', output=output, reconnect_interval_ms=0)
        assert HapticEffect.device is dev
        assert dev.output_device == 'card'
        assert HapticEffect.device_alive()
