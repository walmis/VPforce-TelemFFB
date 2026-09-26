"""Which output channel reaches which transducer.

Plays a short, gently ramped 40 Hz burst on one channel at a time and
prints the channel before each one, first through the shared-mode
WASAPI endpoint with the card's own speaker mask (the way TelemFFB opens
it), then through the card's WDM-KS endpoint, which bypasses the Windows
audio engine and its speaker layout altogether.  If a channel is felt
under WDM-KS but not under WASAPI, the engine is routing it away; if it
is felt under neither, the signal is not reaching the amp.

    python tools/shaker_channel_check.py "StarTech 7.1"
"""
import os
import sys
import time

import numpy as np
import sounddevice as sd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from telemffb.hw.shaker_synth import SoundDeviceOutput, clean_device_name

BURST_S = 0.4
RAMP_S = 0.03
LEVEL = 0.4
HZ = 40.0
GAP_S = 1.2


def burst(samplerate):
    n = int(BURST_S * samplerate)
    t = np.arange(n) / samplerate
    sig = LEVEL * np.sin(2 * np.pi * HZ * t)
    ramp = int(RAMP_S * samplerate)
    env = np.ones(n)
    env[:ramp] = np.linspace(0.0, 1.0, ramp)
    env[-ramp:] = np.linspace(1.0, 0.0, ramp)
    return (sig * env).astype(np.float32)


def play(index, channels, samplerate, extra=None, label=''):
    sig = burst(samplerate)
    for ch in range(channels):
        print(f"  {label}: channel {ch + 1} of {channels}", flush=True)
        block = np.zeros((len(sig), channels), dtype=np.float32)
        block[:, ch] = sig
        sd.play(block, samplerate=samplerate, device=index, extra_settings=extra, blocking=True)
        time.sleep(GAP_S)


def main(wanted):
    wanted = clean_device_name(wanted).lower()
    apis = [a['name'] for a in sd.query_hostapis()]
    found = []
    for i, d in enumerate(sd.query_devices()):
        if d['max_output_channels'] > 0 and wanted in clean_device_name(d['name']).lower():
            found.append((i, apis[d['hostapi']], d['max_output_channels'], int(d['default_samplerate'])))
    if not found:
        print(f"no output matches {wanted!r}; outputs are:")
        for d in SoundDeviceOutput.list_devices(all_host_apis=True):
            print(f"  {d.index:3d} {d.host_api:20s} {d.channels} ch  {d.name}")
        return 1
    for index, api, channels, rate in found:
        if api == 'Windows WASAPI':
            extra = SoundDeviceOutput._wasapi_settings(index, channels)
            mask = hex(extra._streaminfo.channelMask) if extra else 'none (default labelling)'
            print(f"WASAPI shared, {channels} channels, speaker mask {mask}")
            play(index, channels, rate, extra, 'WASAPI')
        elif api == 'Windows WDM-KS':
            print(f"WDM-KS, {channels} channels, straight to the driver")
            play(index, channels, rate, None, 'WDM-KS')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'StarTech'))
