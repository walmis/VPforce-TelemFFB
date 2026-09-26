"""The speaker layout Windows reports for an output, decoded from the
endpoint's format: what tells 3.1 from Quadraphonic."""
import struct
import sys

import pytest

from telemffb.hw import win_audio

pytestmark = [pytest.mark.unit]


def waveformat(channels, mask=None):
    """A WAVEFORMATEX, extended with a speaker mask when one is given."""
    tag = 0xFFFE if mask is not None else 1
    cbsize = 22 if mask is not None else 0
    fmt = struct.pack('<HHIIHHH', tag, channels, 48000, 48000 * channels * 4, channels * 4, 32, cbsize)
    if mask is not None:
        fmt += struct.pack('<HI', 32, mask) + bytes(16)
    return fmt


class TestDecoding:
    def test_three_point_one_and_quad_share_a_count_but_not_a_layout(self):
        assert win_audio.positions_from_mask(0x1 | 0x2 | 0x4 | 0x8, 4) == (
            'Front L', 'Front R', 'Center', 'Subwoofer')
        assert win_audio.positions_from_mask(0x1 | 0x2 | 0x10 | 0x20, 4) == (
            'Front L', 'Front R', 'Rear L', 'Rear R')

    def test_seven_point_one_in_stream_order(self):
        assert win_audio.positions_from_mask(0x63F, 8) == (
            'Front L', 'Front R', 'Center', 'Subwoofer', 'Rear L', 'Rear R', 'Side L', 'Side R')

    def test_a_mask_that_does_not_fit_the_count_names_nothing(self):
        assert win_audio.positions_from_mask(0x3, 4) == ()
        assert win_audio.positions_from_mask(0, 2) == ()

    def test_the_mask_is_read_from_an_extensible_format_only(self):
        assert win_audio._mask_of(waveformat(4, 0xF)) == (4, 0xF)
        assert win_audio._mask_of(waveformat(2)) == (2, 0)
        assert win_audio._mask_of(b'') == (0, 0)


class TestReading:
    def test_off_windows_there_is_nothing_to_read(self, monkeypatch):
        monkeypatch.setattr(win_audio.sys, 'platform', 'linux')
        assert win_audio.output_formats() == {}
        assert win_audio.output_layouts() == {}

    def test_a_failure_reads_as_no_formats(self, monkeypatch):
        monkeypatch.setattr(win_audio.sys, 'platform', 'win32')

        def boom():
            raise OSError('no core audio')
        monkeypatch.setattr(win_audio, '_read_formats', boom)
        assert win_audio.output_formats() == {}

    def test_layouts_are_the_formats_named(self, monkeypatch):
        monkeypatch.setattr(win_audio, 'output_formats',
                            lambda: {'Card 3.1': (4, 0xF), 'Card Quad': (4, 0x33), 'Odd': (4, 0x3)})
        assert win_audio.output_layouts() == {
            'Card 3.1': ('Front L', 'Front R', 'Center', 'Subwoofer'),
            'Card Quad': ('Front L', 'Front R', 'Rear L', 'Rear R')}

    @pytest.mark.skipif(sys.platform != 'win32', reason='Core Audio')
    def test_this_machines_outputs_read_without_error(self):
        layouts = win_audio.output_layouts()
        for name, positions in layouts.items():
            assert name and positions
            assert all(p in {n for _, n in win_audio.SPEAKERS} for p in positions)
