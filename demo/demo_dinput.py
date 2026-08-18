#!/usr/bin/env python3
"""
DirectInput FFB Bridge Demo
===========================

Drives spring / constant-force / periodic effects on a real DirectInput FFB
device through the DInput bridge DLL, outside the TelemFFB app.  This is the
step-3 hardware verification gate from the DirectInput plan.

Usage:
    python demo_dinput.py                  - list FFB-capable devices
    python demo_dinput.py <n>              - run all tests on device number n
    python demo_dinput.py <n> spring       - spring center/coefficient sweep
    python demo_dinput.py <n> constant     - constant-force direction sweep
    python demo_dinput.py <n> periodic     - periodic waveform sweep
    python demo_dinput.py <n> input        - live axis/button/CP monitor
    python demo_dinput.py <n> abtest       - zero-spring feel A/B: any stick
                                             button toggles between a PLAYING
                                             zero-coefficient spring (motors
                                             energized) and NO effect
                                             (freewheel) - move the stick and
                                             compare the back-drive texture

CAUTION: the selected device is acquired exclusively and will produce forces.
Keep a hand on the stick, close other FFB software first.
"""

import ctypes
import logging
import os
import sys
import time

_repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _repo_root)
# hid.py loads hidapi.dll by bare name, which only resolves when the working
# directory is the repo root (IDE runs use the script dir).  Pre-loading by
# absolute path registers the module so the by-name lookup finds it.
try:
    ctypes.cdll.LoadLibrary(os.path.join(_repo_root, "dll", "hidapi.dll"))
except OSError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from PyQt6.QtCore import QCoreApplication

from telemffb.hw.ffb_rhino import HapticEffect, EFFECT_SINE, EFFECT_SPRING, EFFECT_SQUARE
from telemffb.hw.ffb_dinput import DibEffectParams, DInputFFBDevice


def list_devices():
    devs = DInputFFBDevice.enumerate()
    if not devs:
        print("No DirectInput FFB devices found.")
        return []
    print("\nDirectInput FFB devices:")
    for i, dev in enumerate(devs):
        print(f"  {i}: {dev.product_string} ({dev.vidpid()})  "
              f"ff_axes={dev.ff_axes} buttons={dev.buttons} povs={dev.povs}")
        print(f"     guid: {dev.guid}")
        print(f"     effects: {', '.join(dev.effects)}")
    return devs


def pump(app, seconds):
    """Run the Qt event loop (device polling) for a wall-clock interval."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def test_spring(app, device):
    print("\n" + "=" * 60)
    print("SPRING: coefficient ramp, then center sweep")
    print("=" * 60)
    spring = HapticEffect()

    print("Coefficient ramp 0 -> 4096 over 5s (stick should stiffen)...")
    spring.spring(0, 0).start()
    for step in range(0, 4097, 256):
        spring.spring(step, step)
        pump(app, 0.3)

    print("Center sweep X -0.5 -> +0.5 (stick should follow)...")
    from telemffb.hw.ffb_rhino import FFBReport_SetCondition
    for cp in range(-2048, 2049, 128):
        cond = FFBReport_SetCondition(parameterBlockOffset=0, cpOffset=cp,
                                      positiveCoefficient=4096, negativeCoefficient=4096,
                                      positiveSaturation=4096, negativeSaturation=4096)
        spring.setCondition(cond)
        pump(app, 0.15)
        snapshot = device.get_input()
        if snapshot:
            cp_x, _ = snapshot.CP_XY()
            x, _ = snapshot.axisXY()
            print(f"  cp={cp / 4096.0:+.3f}  CP_XY={cp_x:+.3f}  axis={x:+.3f}", end="\r")
    print()
    spring.destroy()
    print("spring test done")


def test_constant(app, device):
    print("\n" + "=" * 60)
    print("CONSTANT FORCE: direction sweep at 25% magnitude")
    print("=" * 60)
    print("Force direction should rotate through 360 degrees...")
    effect = HapticEffect()
    for direction in range(0, 361, 15):
        effect.constant(0.25, direction).start()
        print(f"  direction {direction:3d} deg", end="\r")
        pump(app, 0.4)
    print()
    effect.destroy()
    print("constant force test done")


def test_periodic(app, device):
    print("\n" + "=" * 60)
    print("PERIODIC: sine frequency sweep, then square buzz")
    print("=" * 60)
    print("Sine 2 -> 30 Hz at 20% magnitude...")
    sine = HapticEffect()
    for freq in (2, 5, 10, 15, 20, 30):
        sine.periodic(freq, 0.2, 0, effect_type=EFFECT_SINE).start()
        print(f"  {freq:2d} Hz", end="\r")
        pump(app, 1.5)
    sine.destroy()
    print()

    print("Square 12 Hz buzz, 1.5s on Y...")
    square = HapticEffect()
    square.periodic(12, 0.2, 90, effect_type=EFFECT_SQUARE).start()
    pump(app, 1.5)
    square.destroy()
    print("periodic test done")


def test_input(app, device):
    print("\n" + "=" * 60)
    print("INPUT MONITOR (10s): move axes, press buttons and hats")
    print("=" * 60)
    def _label(b):
        # raw signal carries 0-based bit indices for plain buttons and
        # 0x80|(hat<<4)|pos for hats; report plain buttons 1-based to match
        # the app's binding/polling numbering (getPressedButtons)
        return f"hat code 0x{b:02X}" if b & 0x80 else f"button {b + 1}"

    device.buttonPressed.connect(lambda b: print(f"\n  pressed:  {_label(b)}"))
    device.buttonReleased.connect(lambda b: print(f"\n  released: {_label(b)}"))
    end = time.monotonic() + 10
    while time.monotonic() < end:
        app.processEvents()
        snapshot = device.get_input()
        if snapshot:
            x, y = snapshot.axisXY()
            print(f"  X={x:+.3f}  Y={y:+.3f}   ", end="\r")
        time.sleep(0.02)
    print("\ninput monitor done")


def test_abtest(app, device):
    print("\n" + "=" * 60)
    print("ZERO-SPRING FEEL A/B/C (90s)")
    print("=" * 60)
    print("Move the stick around and press ANY stick button to cycle:")
    print("  A: no effect                        (freewheel)")
    print("  B: zero-coefficient spring PLAYING  (drive stage energized)")
    print("  C: 1% spring (coef 41), center 0    (DCS-style residual)")
    print("A and B command identical (zero) force - any difference is the")
    print("device's energized-motor behavior.\n")

    # raw bridge access: the backend now mutes zero-coefficient conditions
    # automatically, so state B must be forced below the handle layer
    def spring_params(coef):
        p = DibEffectParams()
        p.gain = 4096
        for block in (p.condition_x, p.condition_y):
            block.active = 1
            block.positive_coefficient = coef
            block.negative_coefficient = coef
            block.positive_saturation = 4096
            block.negative_saturation = 4096
        return p

    effect = device.bridge.effect_create(device._handle, EFFECT_SPRING, spring_params(0))
    if effect <= 0:
        print("could not create test spring")
        return

    states = [
        ("A: no effect (freewheel)", None),
        ("B: zero-coefficient spring PLAYING (energized)", 0),
        ("C: 1% spring, center 0 (DCS-style residual)", 41),
    ]
    state = 0
    prev_buttons = None
    print(f">>> STATE {states[state][0]}")
    end = time.monotonic() + 90
    while time.monotonic() < end:
        app.processEvents()
        snapshot = device.get_input()
        if snapshot is not None:
            buttons = snapshot.buttons
            if prev_buttons is not None and buttons & ~prev_buttons:
                state = (state + 1) % len(states)
                label, coef = states[state]
                if coef is None:
                    device.bridge.effect_stop(effect)
                else:
                    device.bridge.effect_update(effect, spring_params(coef))
                    device.bridge.effect_start(effect, 1)
                print(f">>> STATE {label}")
            prev_buttons = buttons
        time.sleep(0.01)

    device.bridge.effect_destroy(effect)
    print("abtest done")


def main():
    if len(sys.argv) < 2:
        list_devices()
        print("\nRun: python demo_dinput.py <device_number> [spring|constant|periodic|input]")
        return

    app = QCoreApplication(sys.argv)
    devs = list_devices()
    index = int(sys.argv[1])
    if not (0 <= index < len(devs)):
        print(f"No device {index}")
        return

    which = sys.argv[2] if len(sys.argv) > 2 else "all"
    print(f"\nOpening {devs[index].product_string} (exclusive acquire)...")
    device = HapticEffect.open_dinput(devs[index].guid)
    pump(app, 0.5)  # let the first polls land

    tests = {
        "spring": test_spring,
        "constant": test_constant,
        "periodic": test_periodic,
        "input": test_input,
        "abtest": test_abtest,
    }
    try:
        if which == "all":
            for name, fn in tests.items():
                if name != "abtest":  # interactive - run explicitly
                    fn(app, device)
        elif which in tests:
            tests[which](app, device)
        else:
            print(f"Unknown test '{which}'")
    finally:
        device.reset_effects()
        print("\nAll effects reset, releasing device.")


if __name__ == "__main__":
    main()
