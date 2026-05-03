#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""Audio-side helpers shared by the manual calibration panel and the wizard.

Extracted from SystemSettingsDialog so the calibration wizard can drive the
shaker without depending on that dialog's internals. Both call sites build
a fresh ShakerSynth on demand because the master-instance dialog has no
running shaker child to talk to.
"""

import logging
import threading
import time


def _read_routing(parent) -> dict:
    """Pull the audio routing (device/gain/channel/pan) off the dialog.

    The wizard and the manual panel both live on a SystemSettingsDialog
    that already exposes these widgets at the top of the Shaker tab.
    """
    return dict(
        device=parent.shaker_device_combo.currentData() or None,
        gain=float(parent.shaker_gain_spin.value()),
        channel_mode=parent.shaker_channel_combo.currentData() or "mono",
        pan=parent.shaker_pan_slider.value() / 100.0,
    )


def play_pulse(parent, args: dict) -> threading.Thread:
    """Spin up a one-shot ShakerSynth and trigger one calibration pulse.

    ``args`` is the same dict returned by
    SystemSettingsDialog._shaker_calib_current_pulse_args (carrier_hz,
    halfwaves, amplitude, attack_ms, release_ms, brake_amp, brake_delay_ms).
    Runs the audio in a background thread so the GUI stays responsive;
    returns the thread for the caller that wants to join it (most don't).
    """
    routing = _read_routing(parent)

    def _run():
        try:
            from telemffb.hw.shaker_synth import ShakerSynth
            synth = ShakerSynth(
                device=routing["device"],
                master_gain=routing["gain"],
                channel_mode=routing["channel_mode"],
                pan=routing["pan"],
            )
            synth.start()
            try:
                osc = synth.get_oscillator("__calib_pulse__")
                osc.trigger_pulse(**args)
                # Sleep long enough for drive + delay + brake to finish.
                half_period = 1.0 / max(1.0, args["carrier_hz"]) / 2.0
                drive_s = args["halfwaves"] * half_period
                brake_s = (half_period if args["brake_amp"] > 0.0 else 0.0)
                delay_s = args["brake_delay_ms"] / 1000.0
                time.sleep(drive_s + delay_s + brake_s + 0.15)
            finally:
                synth.stop()
        except Exception:
            logging.exception("Shaker calibration pulse failed")

    thr = threading.Thread(target=_run, daemon=True)
    thr.start()
    return thr


def start_sweep(parent, lo: float, hi: float, dur: float, amp: float,
                stop_evt: threading.Event,
                on_finished_main_thread) -> threading.Thread:
    """Start a linear ``lo→hi`` resonance sweep on a background thread.

    The thread mutates ``parent._shaker_calib_sweep_current_freq`` (read by
    the foreground 50 ms QTimer that paints the live frequency label).
    ``stop_evt`` is signalled by the caller to abort early; the thread also
    exits when the sweep duration elapses. ``on_finished_main_thread`` is
    invoked once via QTimer.singleShot(0, …) so the caller can re-enable
    GUI controls without worrying about thread affinity.
    """
    routing = _read_routing(parent)

    def _run():
        try:
            from telemffb.hw.shaker_synth import ShakerSynth
            synth = ShakerSynth(
                device=routing["device"],
                master_gain=routing["gain"],
                channel_mode=routing["channel_mode"],
                pan=routing["pan"],
            )
            synth.start()
            try:
                osc = synth.get_oscillator("__calib_sweep__")
                t0 = time.perf_counter()
                while not stop_evt.is_set():
                    t = time.perf_counter() - t0
                    if t >= dur:
                        break
                    f = lo + (hi - lo) * (t / dur)
                    osc.set(f, amp, ramp_ms=20.0)
                    parent._shaker_calib_sweep_current_freq = f
                    time.sleep(0.05)
                osc.stop(ramp_ms=80.0)
                time.sleep(0.1)
            finally:
                synth.stop()
        except Exception:
            logging.exception("Shaker calibration sweep failed")
        finally:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, on_finished_main_thread)

    thr = threading.Thread(target=_run, daemon=True)
    thr.start()
    return thr
