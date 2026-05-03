"""Offline numerical tests for PhaseAccumulator and ImpulseTrainOscillator.

Runs without audio hardware. Invoke with::

    python -m tests.test_phase_locked_impulses

Exits with non-zero status on failure. No third-party test runner required.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

# Allow running as a loose script from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from telemffb.hw.shaker_synth import ImpulseTrainOscillator, PhaseAccumulator


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def test_phase_accumulator_constant_rpm() -> None:
    # 300 RPM × 5 divisions = 25 Hz crossings. Run for 1 s in 60 Hz ticks.
    acc = PhaseAccumulator()
    total_crossings = 0
    ticks = 60
    dt = 1.0 / ticks
    for _ in range(ticks):
        total_crossings += acc.advance(rpm=300.0, divisions_per_rev=5, dt=dt)
    # Expect exactly 25 crossings (modulo phase fractional residual <1).
    assert total_crossings == 25, f"expected 25, got {total_crossings}"
    # Residual phase should be 0 or extremely close to it.
    assert acc.phase < 1.0, f"residual phase out of range: {acc.phase}"


def test_phase_accumulator_zero_rate() -> None:
    acc = PhaseAccumulator()
    assert acc.advance(rpm=0.0, divisions_per_rev=5, dt=1.0) == 0
    assert acc.advance(rpm=300.0, divisions_per_rev=0, dt=1.0) == 0
    assert acc.advance(rpm=300.0, divisions_per_rev=5, dt=0.0) == 0


def test_phase_accumulator_ramp_matches_integral() -> None:
    """RPM ramping linearly from 0 → 300 over 10 s, divisions=5. Expected
    total crossings = ∫ω dt = 300/60 * 5 * 10 / 2 = 125."""
    acc = PhaseAccumulator()
    total = 0
    steps = 200
    duration = 10.0
    dt = duration / steps
    for i in range(steps):
        rpm = 300.0 * (i + 0.5) / steps  # midpoint rule
        total += acc.advance(rpm=rpm, divisions_per_rev=5, dt=dt)
    # Allow ±1 because the last fractional crossing depends on rounding.
    assert 124 <= total <= 126, f"expected ~125, got {total}"


def test_impulse_train_oscillator_pulse_count() -> None:
    """Render a short stretch of audio and count distinct pulse onsets.

    At 200 RPM × 5 divisions = ~16.7 Hz, over 0.6 s we expect ~10 pulses.
    Onsets are detected as samples where the absolute amplitude rises from
    near zero past a threshold."""
    sr = 48000
    block = 256
    osc = ImpulseTrainOscillator(sr, block, max_impulse_rate_hz=180.0)
    osc.configure(carrier_hz=60.0, halfwaves=1, attack_ms=1.0,
                  release_ms=3.0, brake_amp=0.4, gain=1.0)
    osc.set_rpm(rpm=200.0, divisions_per_rev=5, load=0.9)

    duration_s = 0.6
    total_blocks = int(round(duration_s * sr / block))
    chunks = []
    for _ in range(total_blocks):
        chunks.append(osc.render(block).copy())
    audio = np.concatenate(chunks)

    # Detect onsets via thresholded rising edges of the abs signal.
    abs_audio = np.abs(audio)
    threshold = 0.2
    above = abs_audio > threshold
    rising = np.diff(above.astype(np.int8)) == 1
    onsets = np.flatnonzero(rising)
    # Group onsets within 30 ms (a pulse's drive + brake halfwaves both show
    # as positive peaks under np.abs — count them as one event).
    if len(onsets) == 0:
        pulses = 0
    else:
        min_separation = int(0.030 * sr)
        pulses = 1
        last = onsets[0]
        for o in onsets[1:]:
            if o - last >= min_separation:
                pulses += 1
                last = o
    expected = int(round(duration_s * 200.0 / 60.0 * 5))
    # Allow ±1 — block quantization can shift a pulse across the window edge.
    assert abs(pulses - expected) <= 1, f"expected ~{expected}, got {pulses}"


def test_impulse_train_silent_when_zero_rpm() -> None:
    sr = 48000
    block = 256
    osc = ImpulseTrainOscillator(sr, block)
    osc.set_rpm(rpm=0.0, divisions_per_rev=5, load=1.0)
    audio = osc.render(block * 5)
    assert np.allclose(audio, 0.0)
    assert osc.is_silent


def test_impulse_train_silent_above_max_rate() -> None:
    sr = 48000
    block = 256
    osc = ImpulseTrainOscillator(sr, block, max_impulse_rate_hz=20.0)
    # 600 RPM × 4 = 40 Hz, above the 20 Hz cap → should not fire pulses.
    osc.set_rpm(rpm=600.0, divisions_per_rev=4, load=1.0)
    audio = osc.render(block * 20)
    # Inner Oscillator was never triggered; output should be silent or very small.
    assert np.max(np.abs(audio)) < 1e-3, f"audio not silent: max={np.max(np.abs(audio))}"


def main() -> int:
    tests = [
        test_phase_accumulator_constant_rpm,
        test_phase_accumulator_zero_rate,
        test_phase_accumulator_ramp_matches_integral,
        test_impulse_train_oscillator_pulse_count,
        test_impulse_train_silent_when_zero_rpm,
        test_impulse_train_silent_above_max_rate,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    if failed:
        print(f"{failed}/{len(tests)} tests failed")
        return 1
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
