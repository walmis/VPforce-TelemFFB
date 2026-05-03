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


def test_impulse_train_falls_back_to_noise_above_max_rate() -> None:
    """Above ``max_impulse_rate_hz`` the oscillator switches to a bandpass
    noise voice instead of going silent. Verifies the fallback produces
    audible output and exposes ``fallback_active``.
    """
    sr = 48000
    block = 256
    osc = ImpulseTrainOscillator(sr, block, max_impulse_rate_hz=20.0)
    osc.configure(carrier_hz=20.0, gain=1.0)
    # 600 RPM × 4 = 40 Hz, well above the 20 Hz cap → fallback engages.
    osc.set_rpm(rpm=600.0, divisions_per_rev=4, load=1.0)
    # First block primes the hysteresis state; let amplitude ramp in.
    audio = np.concatenate([osc.render(block) for _ in range(40)])
    # Drop the first 20 blocks (noise still ramping in) and look at the
    # steady-state tail.
    tail = audio[20 * block:]
    rms = float(np.sqrt(np.mean(tail.astype(np.float64) ** 2)))
    assert osc.fallback_active, "fallback should have engaged at 2× cap"
    assert rms > 0.01, f"noise output too quiet: rms={rms}"
    # Sanity: the noise voice should not be a pure tone — many zero
    # crossings per block at this center frequency.
    sign_changes = int(np.sum(np.diff(np.sign(tail)) != 0))
    assert sign_changes > 50, \
        f"output looks too tonal for bandpass noise: zero-crossings={sign_changes}"


def test_impulse_train_fallback_hysteresis_does_not_flap() -> None:
    """With ``impulse_hz`` parked between the low (85%) and high (100%)
    thresholds, the active mode must remain whichever side it started on
    — both for low-side starts (impulses) and high-side starts (noise)."""
    sr = 48000
    block = 256
    cap = 100.0

    # 1) Approach from below — should stay in impulse mode at 90% of cap.
    osc_low = ImpulseTrainOscillator(sr, block, max_impulse_rate_hz=cap)
    osc_low.set_rpm(rpm=90.0, divisions_per_rev=60, load=1.0)  # 90 Hz
    for _ in range(20):
        osc_low.render(block)
    assert not osc_low.fallback_active, \
        "fallback should not engage at 90% of cap when approached from below"

    # 2) Approach from above — should stay in noise mode at 90% of cap.
    osc_high = ImpulseTrainOscillator(sr, block, max_impulse_rate_hz=cap)
    osc_high.set_rpm(rpm=120.0, divisions_per_rev=60, load=1.0)  # 120 Hz, >cap
    for _ in range(10):
        osc_high.render(block)
    assert osc_high.fallback_active, \
        "fallback should engage above cap"
    osc_high.set_rpm(rpm=90.0)  # drop to 90 Hz, between low (85) and high (100)
    for _ in range(20):
        osc_high.render(block)
    assert osc_high.fallback_active, \
        "fallback should remain engaged at 90% of cap when approached from above"


def test_impulse_train_fallback_exits_below_low_threshold() -> None:
    """Once ``impulse_hz`` falls below 85% of cap, fallback releases and
    the impulse path returns."""
    sr = 48000
    block = 256
    cap = 100.0
    osc = ImpulseTrainOscillator(sr, block, max_impulse_rate_hz=cap)
    osc.set_rpm(rpm=120.0, divisions_per_rev=60, load=1.0)  # 120 Hz → noise
    for _ in range(10):
        osc.render(block)
    assert osc.fallback_active
    osc.set_rpm(rpm=80.0)  # 80 Hz, below 85% cap
    for _ in range(10):
        osc.render(block)
    assert not osc.fallback_active, \
        "fallback should release once impulse rate drops below low threshold"


def test_impulse_train_smooth_crossover_no_silence_dip() -> None:
    """Ramp RPM through the cap and confirm output never silences for an
    extended stretch — the noise voice ramps in as the impulse path
    quiets, with no audible dropout."""
    sr = 48000
    block = 256
    cap = 50.0
    osc = ImpulseTrainOscillator(sr, block, max_impulse_rate_hz=cap)
    osc.configure(carrier_hz=50.0, halfwaves=1, attack_ms=1.0,
                  release_ms=3.0, brake_amp=0.0, gain=1.0)

    chunks = []
    # 60 blocks ≈ 320 ms; ramp impulse rate from 30 Hz to 80 Hz.
    n_blocks = 60
    for i in range(n_blocks):
        impulse_hz = 30.0 + (80.0 - 30.0) * i / (n_blocks - 1)
        rpm = impulse_hz * 60.0 / 5.0
        osc.set_rpm(rpm=rpm, divisions_per_rev=5, load=1.0)
        chunks.append(osc.render(block).copy())
    audio = np.concatenate(chunks)

    # Coarse-grained envelope: per-block RMS. No 4-block (≈20 ms) window
    # may be entirely silent during the crossover.
    rms_per_block = np.array([
        np.sqrt(np.mean(chunks[i].astype(np.float64) ** 2))
        for i in range(n_blocks)
    ])
    # Skip the first 5 blocks where we're still below cap and pulses are
    # only just starting; check the crossover region (blocks 15..55) for
    # no extended silence.
    crossover = rms_per_block[15:55]
    silent_window = max(
        sum(1 for r in crossover[i:i + 4] if r < 1e-3)
        for i in range(len(crossover) - 4)
    )
    assert silent_window < 4, \
        f"silence dip detected during crossover (4+ consecutive quiet blocks)"


def main() -> int:
    tests = [
        test_phase_accumulator_constant_rpm,
        test_phase_accumulator_zero_rate,
        test_phase_accumulator_ramp_matches_integral,
        test_impulse_train_oscillator_pulse_count,
        test_impulse_train_silent_when_zero_rpm,
        test_impulse_train_falls_back_to_noise_above_max_rate,
        test_impulse_train_fallback_hysteresis_does_not_flap,
        test_impulse_train_fallback_exits_below_low_threshold,
        test_impulse_train_smooth_crossover_no_silence_dip,
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
