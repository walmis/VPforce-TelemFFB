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

"""
HapticEffect facade for the bass-shaker device type.

This module mirrors the surface of telemffb.hw.ffb_rhino so that
``aircraft_base.py`` and the per-sim aircraft modules can be imported and run
unchanged when ``G.device_type == 'shaker'``. Calls that produce vibration on
the Rhino (.periodic / .constant / .start / .stop) are routed into a
module-level ShakerSynth oscillator. Force-only calls (.spring, .damper,
.friction, .inertia, .setCondition, .spring_adjuster, ._conditional_effect)
are chainable no-ops.

Whitelist-based effect filtering is added in STEP_04.
"""

import argparse
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from telemffb.hw.shaker_synth import Oscillator, ShakerSynth

logger = logging.getLogger(__name__)

# Effect-type constants. Values mirror telemffb.hw.ffb_rhino so that the two
# modules are interchangeable from the perspective of aircraft_base.
EFFECT_CONSTANT = 1
EFFECT_RAMP = 2
EFFECT_SQUARE = 3
EFFECT_SINE = 4
EFFECT_TRIANGLE = 5
EFFECT_SAWTOOTHUP = 6
EFFECT_SAWTOOTHDOWN = 7
EFFECT_SPRING = 8
EFFECT_DAMPER = 9
EFFECT_INERTIA = 10
EFFECT_FRICTION = 11
EFFECT_CUSTOM = 12
EFFECT_DETENT = 13
EFFECT_SPRING_ADJUSTER = 14

# Default frequency used by .constant() (no inherent periodicity, so we map it
# to a low-rumble carrier).
CONSTANT_FORCE_FREQUENCY_HZ = 25.0

# Effect names known to translate usefully to a bass shaker. Effect names not
# in this set are dropped at HapticEffect.start() with a debug log -- this is
# how we keep force-only ('spring_adjuster' etc.) and unrelated effects from
# producing audio. New effects added in aircraft_base.py upstream don't
# accidentally produce noise on the shaker; they need an explicit opt-in here.
SHAKER_EFFECT_WHITELIST = {
    # wheel / runway
    "runway0", "runway1", "runway_bump0", "runway_bump1", "touchdown",
    "gearclunk",
    # weapons / countermeasures
    "gunfire", "cm", "payload_rel",
    # buffeting
    "buffeting", "buffeting2", "vrs_buffet",
    "gearbuffet", "gearbuffet2",
    "spoilerbuffet1-1", "spoilerbuffet1-2", "spoilerbuffet2-1", "spoilerbuffet2-2",
    # afterburner / jet
    "ab_rumble_1_1", "ab_rumble_1_2", "ab_rumble_2_1", "ab_rumble_2_2",
    "je_rumble_1_1", "je_rumble_1_2", "je_rumble_2_1", "je_rumble_2_2",
    # prop / rotor
    "prop_rpm0-1", "prop_rpm0-2", "prop_rpm1-1", "prop_rpm1-2",
    "rotor_rpm0-1", "rotor_rpm1-1",
    # ETL
    "etlX", "etlY",
    # surface movements
    "flapsmovement", "gearmovement", "gearmovement2",
    "speedbrakemovement", "spoilermovement", "spoilermovement2",
    "canopymovement", "hookmovement",
    # overspeed / aoa
    "overspeedX", "overspeedY", "aoa", "crit_aoa",
    # wind
    "wnd",
    # interactive effect tester (telemffb.EffectTestDialog)
    "__effect_tester__",
}

# Per-effect tuning. Effects without an entry use _DEFAULT_PROFILE.
# kind="transient" routes through Oscillator.trigger_pulse() — the active
# ShakerProfile (set by main.py at startup) supplies halfwaves / attack /
# release / brake; the per-effect attack_ms / decay_ms below are LEGACY and
# IGNORED on the transient path. Only freq and gain still apply for transients.
# kind="continuous" uses Oscillator.set() with the given ramp_ms.
SHAKER_EFFECT_PROFILES: dict = {
    "gearclunk":    {"kind": "transient", "freq": 55.0, "gain": 1.0,
                     "attack_ms": 3.0, "decay_ms": 110.0},
    "runway_bump0": {"kind": "transient", "freq": 50.0, "gain": 0.9,
                     "attack_ms": 2.0, "decay_ms": 70.0},
    "runway_bump1": {"kind": "transient", "freq": 35.0, "gain": 0.9,
                     "attack_ms": 2.0, "decay_ms": 130.0},
    "payload_rel":  {"kind": "transient", "freq": 40.0, "gain": 1.0,
                     "attack_ms": 3.0, "decay_ms": 200.0},
    "buffeting2":   {"kind": "continuous", "ramp_ms": 15.0, "gain": 1.1},
    "gearbuffet2":  {"kind": "continuous", "ramp_ms": 20.0, "gain": 1.0},
}

_DEFAULT_PROFILE = {"kind": "continuous", "ramp_ms": 50.0, "gain": 1.0}


@dataclass(frozen=True)
class Layer:
    freq_factor: float = 1.0
    gain: float = 1.0
    route: str = "both"      # "shaker" | "stick" | "both"
    osc_type: str = "sine"   # "sine" | "impulse" | "bandpass_noise"
    # Only meaningful when osc_type == "bandpass_noise":
    center_hz: Optional[float] = None     # if None, uses freq_factor * call_site_freq
    bandwidth_hz: Optional[float] = None  # if None, defaults to 20.0 Hz at runtime
    # Only meaningful when osc_type == "impulse":
    # If None, Oscillator.trigger() uses its built-in defaults
    # (attack_ms=4.0, decay_ms=90.0).
    attack_ms: Optional[float] = None
    decay_ms:  Optional[float] = None


DEFAULT_LAYER = Layer()


def _layer_is_for_shaker(layer: Layer) -> bool:
    return layer.route in ("shaker", "both")


def _load_builtin_defaults() -> "dict[str, list[Layer]]":
    """Parse the bundled default layer pack at module import.

    Returns an empty dict if the bundle is missing or malformed (logged at
    warn level). Used as the base for reload_layers() and as the source for
    "Reset to default" UI actions.
    """
    from .shaker_layers_io import get_default_pack_path, load
    try:
        path = get_default_pack_path()
        if path and os.path.exists(path):
            return load(path)
    except Exception:
        logger.exception("Could not load bundled shaker_effects_default.json")
    return {}


_BUILTIN_DEFAULT_LAYERS: dict[str, list[Layer]] = _load_builtin_defaults()

# Populated at startup by reload_layers() from shaker_effects.json.
# All entries come from disk; nothing is hardcoded here.
EFFECT_LAYERS: dict[str, list[Layer]] = {}


# Active shaker calibration profile. Replaced by main.py at startup via
# set_active_profile() once shaker_profiles.json has been read. Until then,
# transient effects use the dataclass defaults (a conservative no-brake shape).
from .shaker_profile import ShakerProfile, DEFAULT_PROFILE  # noqa: E402

ACTIVE_PROFILE: ShakerProfile = DEFAULT_PROFILE


def set_active_profile(profile: Optional[ShakerProfile]) -> None:
    """Set the global ShakerProfile consumed by transient/impulse effects.

    Called from main.py at startup and from the System Settings calibration
    UI when the user saves a profile. None resets to the dataclass default.
    """
    global ACTIVE_PROFILE
    ACTIVE_PROFILE = profile if profile is not None else DEFAULT_PROFILE
    logger.info("Shaker active profile set to %r", ACTIVE_PROFILE.name)


def _pulse_kwargs(amplitude: float, layer: Optional[Layer] = None) -> dict:
    """Compose Oscillator.trigger_pulse kwargs from the active profile.

    Per-layer attack_ms / decay_ms (schema v3) override the profile's
    attack_ms / release_ms when set, preserving the per-layer tuning hook.
    The brake is gated by the profile's brake_enabled flag and scales with
    the call-site amplitude.
    """
    p = ACTIVE_PROFILE
    attack = (layer.attack_ms if (layer is not None and layer.attack_ms is not None)
              else p.attack_ms)
    release = (layer.decay_ms if (layer is not None and layer.decay_ms is not None)
               else p.release_ms)
    brake_amp = (p.brake_amp_pct / 100.0) * amplitude if p.brake_enabled else 0.0
    return dict(halfwaves=p.halfwaves, attack_ms=attack, release_ms=release,
                brake_amp=brake_amp, brake_delay_ms=p.brake_delay_ms)


def get_builtin_default_for(name: str) -> "list[Layer]":
    """Return a fresh copy of the bundled default layers for a single effect.

    Used by the System Settings layer editor's "Reset effect to default"
    button. Returns [] if there is no built-in default for that effect.
    """
    return list(_BUILTIN_DEFAULT_LAYERS.get(name, []))


def reload_layers() -> None:
    """Re-read the user's shaker_effects.json and replace EFFECT_LAYERS.

    Called at startup (main.py) and by the System Settings UI after Save
    (STEP_03). Does NOT clear built-in defaults — anything not in the JSON
    falls back to the built-in default layer pack (STEP_04).
    """
    from .shaker_layers_io import load, get_shaker_effects_path
    path = get_shaker_effects_path()
    if not path:
        return
    new_data = load(path)
    EFFECT_LAYERS.clear()
    EFFECT_LAYERS.update(_BUILTIN_DEFAULT_LAYERS)  # populated by STEP_04
    EFFECT_LAYERS.update(new_data)
    logger.info("EFFECT_LAYERS reloaded: %d entries", len(EFFECT_LAYERS))


_synth: Optional[ShakerSynth] = None


def init_shaker(synth: ShakerSynth) -> None:
    """Bind the module-level ShakerSynth instance.

    Called once at startup from main.py after the ShakerSynth has been
    constructed and started.
    """
    global _synth
    _synth = synth
    logger.info("ffb_shaker bound to ShakerSynth (samplerate=%d, blocksize=%d)",
                synth.samplerate, synth.blocksize)


class FFBReport_SetCondition:
    """Stub for compatibility with ffb_rhino.FFBReport_SetCondition.

    The Rhino implementation is a ctypes Structure that serialises to a HID
    report. On the shaker device there is no condition force to send, so we
    accept arbitrary keyword arguments and store them for inspection /
    debugging only.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self) -> str:
        return f"FFBReport_SetCondition(shaker stub, {self.__dict__!r})"


class _ZeroInput:
    """Stub for the FFBReport_Input returned by Rhino's get_input().

    aircraft_base / aircrafts_msfs_xp / aircrafts_dcs read positions, forces,
    and button state every telemetry frame. The shaker has no such inputs;
    we return zeros / False so the upstream maths fall through to no-ops.
    """
    def forceXY(self):
        return 0.0, 0.0
    def axisXY(self):
        return 0.0, 0.0
    def CP_XY(self):
        return 0.0, 0.0
    def CP_scaled_axisXY(self):
        return 0.0, 0.0
    def isButtonPressed(self, _button):
        return False
    def getPressedButtons(self):
        return set()


class _StubDevice:
    """Stand-in for FFBRhino on the shaker child.

    Provides the small subset of FFBRhino methods that aircraft_base.py
    invokes regardless of device type (input polling, deadzone). All are
    silent no-ops or zero-returning.
    """
    _zero = _ZeroInput()

    def get_input(self):
        return self._zero

    def set_deadzone(self, _value):
        pass

    def reset_effects(self):
        pass

    def get_gains(self):
        # Return an object with the gain attributes ConfiguratorDialog expects
        # (all zero — shaker has no FFB gains). Returning None would crash the
        # dialog with AttributeError on .master_gain etc.
        class _ZeroGains:
            master_gain = 0
            periodic_gain = 0
            spring_gain = 0
            damper_gain = 0
            inertia_gain = 0
            friction_gain = 0
            constant_gain = 0
        return _ZeroGains()


class HapticEffect:
    """Mirror of ffb_rhino.HapticEffect for the bass-shaker device type."""

    # Stub stand-in for the Rhino device. aircraft_base.on_telemetry calls
    # HapticEffect.device.get_input() every frame; on the shaker child there
    # is no real device, so this returns zeros instead of raising.
    device = _StubDevice()

    def __init__(self):
        self.name: Optional[str] = None
        self.frequency: float = 0.0
        self.magnitude: float = 0.0
        self.direction: float = 0.0
        self.duration: int = 0
        self.effect_type: Optional[int] = None
        self.modulator = None
        self._duration_timer: Optional[threading.Timer] = None

    def __repr__(self) -> str:
        return f"HapticEffect(shaker name={self.name!r}, freq={self.frequency:.1f}, mag={self.magnitude:.3f})"

    @property
    def id(self):
        # Mirror ffb_rhino.HapticEffect.id (which returns the Rhino effect block
        # index). The shaker has no such index — return None; UI code that
        # formats this typically tolerates None or skips the row.
        return None

    # ------------------------------------------------------------------
    # Effect-shape configuration (chainable; do not touch the synth).
    # ------------------------------------------------------------------

    def periodic(self, frequency, magnitude: float, direction: float, *args,
                 effect_type: int = EFFECT_SINE, duration: int = 0, **kwargs) -> "HapticEffect":
        self.frequency = float(frequency)
        self.magnitude = float(magnitude)
        self.direction = float(direction) if not isinstance(direction, type) else 0.0
        self.duration = int(duration)
        self.effect_type = effect_type
        return self

    def constant(self, magnitude: float, direction: float, *args, **kwargs) -> "HapticEffect":
        self.frequency = CONSTANT_FORCE_FREQUENCY_HZ
        self.magnitude = float(magnitude)
        self.direction = float(direction) if not isinstance(direction, type) else 0.0
        self.duration = int(kwargs.get("duration", 0))
        self.effect_type = EFFECT_CONSTANT
        return self

    # ------------------------------------------------------------------
    # Force-only effects: chainable no-ops with a debug log.
    # ------------------------------------------------------------------

    def _force_only(self, label: str) -> "HapticEffect":
        logger.debug("Force effect %s ignored on shaker device (name=%r)",
                     label, self.name)
        return self

    def spring(self, coef_x=None, coef_y=None) -> "HapticEffect":
        return self._force_only("spring")

    def damper(self, coef_x=None, coef_y=None) -> "HapticEffect":
        return self._force_only("damper")

    def friction(self, coef_x=None, coef_y=None) -> "HapticEffect":
        return self._force_only("friction")

    def inertia(self, coef_x=None, coef_y=None) -> "HapticEffect":
        return self._force_only("inertia")

    def spring_adjuster(self, coef_x=4096, coef_y=4096) -> "HapticEffect":
        return self._force_only("spring_adjuster")

    def setCondition(self, cond: FFBReport_SetCondition) -> "HapticEffect":
        return self._force_only("setCondition")

    def _conditional_effect(self, effect_type, coef_x=None, coef_y=None) -> "HapticEffect":
        return self._force_only("_conditional_effect")

    # ------------------------------------------------------------------
    # Playback control.
    # ------------------------------------------------------------------

    @property
    def started(self) -> bool:
        if _synth is None or self.name is None:
            return False
        if self.name in EFFECT_LAYERS:
            # Layer-aware path: True if any shaker-routed layer oscillator
            # exists and is not silent.
            layers = EFFECT_LAYERS[self.name]
            for idx, layer in enumerate(layers):
                if not _layer_is_for_shaker(layer):
                    continue
                osc = _synth.peek_oscillator(f"{self.name}__layer{idx}")
                if osc is not None and not osc.is_silent:
                    return True
            return False
        # Legacy path: single oscillator keyed by plain effect name.
        osc = _synth.peek_oscillator(self.name)
        return osc is not None and not osc.is_silent

    def _stop_layer_names(self, names: list) -> None:
        if _synth is None:
            return
        for name in names:
            osc = _synth.peek_oscillator(name)
            if osc is not None:
                osc.stop()

    def _start_layered(self, layers: list) -> "HapticEffect":
        created_names = []
        for idx, layer in enumerate(layers):
            if not _layer_is_for_shaker(layer):
                continue
            osc_name = f"{self.name}__layer{idx}"
            eff_freq = self.frequency * layer.freq_factor
            eff_mag  = self.magnitude * layer.gain
            if layer.osc_type == "sine":
                osc = _synth.get_oscillator(osc_name)
                osc.set(eff_freq, eff_mag)
            elif layer.osc_type == "impulse":
                osc = _synth.get_oscillator(osc_name)
                osc.trigger_pulse(carrier_hz=eff_freq, amplitude=eff_mag,
                                  **_pulse_kwargs(eff_mag, layer))
            elif layer.osc_type == "bandpass_noise":
                osc = _synth.get_noise_oscillator(osc_name)
                center = layer.center_hz if layer.center_hz is not None else eff_freq
                bw = layer.bandwidth_hz if layer.bandwidth_hz is not None else 20.0
                osc.set(center_hz=center, bandwidth_hz=bw, amplitude=eff_mag)
            else:
                logger.warning("Unknown osc_type %r in layer for %s — skipping",
                               layer.osc_type, self.name)
                continue
            created_names.append(osc_name)

        logger.debug("Shaker layered start name=%r layers=%d -> %s",
                     self.name, len(layers), created_names)

        if self._duration_timer is not None:
            self._duration_timer.cancel()
            self._duration_timer = None
        needs_timer = self.duration > 0 and any(
            l.osc_type in ("sine", "bandpass_noise") and _layer_is_for_shaker(l) for l in layers
        )
        if needs_timer:
            continuous_names = [
                f"{self.name}__layer{i}"
                for i, l in enumerate(layers)
                if l.osc_type in ("sine", "bandpass_noise") and _layer_is_for_shaker(l)
            ]
            t = threading.Timer(
                self.duration / 1000.0,
                lambda: self._stop_layer_names(continuous_names),
            )
            t.daemon = True
            self._duration_timer = t
            t.start()
        return self

    def start(self, force: bool = False, **kw) -> "HapticEffect":
        if _synth is None:
            logger.warning("HapticEffect.start called before init_shaker; dropping (name=%r)",
                           self.name)
            return self
        if self.name is None:
            logger.warning("HapticEffect.start called with no name; dropping")
            return self
        if self.name not in SHAKER_EFFECT_WHITELIST:
            logger.debug("Shaker start: effect %r not in whitelist; dropping", self.name)
            return self

        if self.name in EFFECT_LAYERS:
            return self._start_layered(EFFECT_LAYERS[self.name])

        profile = SHAKER_EFFECT_PROFILES.get(self.name)
        if profile is None:
            # Heuristic: short square pulses (gear/runway-bump-like calls
            # without an explicit profile entry) are transients.
            if (self.effect_type == EFFECT_SQUARE
                    and 0 < self.duration <= 80):
                use_transient = True
                freq = float(self.frequency) if self.frequency > 0 else 50.0
                gain = 1.0
                ramp_ms = _DEFAULT_PROFILE["ramp_ms"]
            else:
                use_transient = False
                freq = self.frequency
                gain = _DEFAULT_PROFILE["gain"]
                ramp_ms = _DEFAULT_PROFILE["ramp_ms"]
        else:
            use_transient = profile.get("kind") == "transient"
            freq = float(profile.get("freq", self.frequency))
            gain = float(profile.get("gain", 1.0))
            ramp_ms = float(profile.get("ramp_ms", _DEFAULT_PROFILE["ramp_ms"]))

        magnitude = self.magnitude * gain

        osc = _synth.get_oscillator(self.name)
        if use_transient:
            osc.trigger_pulse(carrier_hz=freq, amplitude=magnitude,
                              **_pulse_kwargs(magnitude, layer=None))
        else:
            osc.set(freq, magnitude, ramp_ms=ramp_ms)

        logger.debug("Shaker start name=%r kind=%s freq=%.2f mag=%.3f dur=%d",
                     self.name, "transient" if use_transient else "continuous",
                     freq, magnitude, self.duration)

        # Cancel any prior duration timer.
        if self._duration_timer is not None:
            self._duration_timer.cancel()
            self._duration_timer = None
        # Transient envelopes end themselves; only continuous effects need
        # the duration timer.
        if not use_transient and self.duration > 0:
            t = threading.Timer(self.duration / 1000.0, self._timed_stop)
            t.daemon = True
            self._duration_timer = t
            t.start()
        return self

    def _timed_stop(self) -> None:
        try:
            self.stop()
        except Exception:
            logger.exception("Error in shaker timed stop (name=%r)", self.name)

    def stop(self, destroy_after: int = 10000) -> "HapticEffect":
        if _synth is None or self.name is None:
            return self
        if self._duration_timer is not None:
            self._duration_timer.cancel()
            self._duration_timer = None

        if self.name in EFFECT_LAYERS:
            layers = EFFECT_LAYERS[self.name]
            names = [
                f"{self.name}__layer{i}"
                for i, l in enumerate(layers)
                if _layer_is_for_shaker(l)
            ]
            self._stop_layer_names(names)
            logger.debug("Shaker layered stop name=%r", self.name)
            return self

        osc = _synth.peek_oscillator(self.name)
        if osc is not None:
            osc.stop()
            logger.debug("Shaker stop name=%r", self.name)
        return self

    def destroy(self) -> None:
        if _synth is None or self.name is None:
            return
        if self._duration_timer is not None:
            self._duration_timer.cancel()
            self._duration_timer = None
        if self.name in EFFECT_LAYERS:
            # Layer-aware path: remove each shaker-routed __layerN oscillator.
            layers = EFFECT_LAYERS[self.name]
            for idx, layer in enumerate(layers):
                if not _layer_is_for_shaker(layer):
                    continue
                _synth.remove_oscillator(f"{self.name}__layer{idx}")
        else:
            # Legacy path: single oscillator keyed by plain effect name.
            _synth.remove_oscillator(self.name)
        logger.debug("Shaker destroy name=%r", self.name)

    def __del__(self):
        try:
            self.destroy()
        except Exception:
            pass


def _selftest_layered(device, samplerate: int) -> None:
    from telemffb.hw.shaker_synth import ShakerSynth as _ShakerSynth
    logger.info("ffb_shaker layered selftest: device=%r samplerate=%s", device, samplerate)
    synth = _ShakerSynth(samplerate=samplerate, device=device)
    synth.start()
    init_shaker(synth)
    try:
        e = HapticEffect()
        e.name = "je_rumble_1_1"
        e.periodic(40, 0.5, 0).start()
        logger.info("Layered start issued — expect 20 Hz (layer0) and 80 Hz (layer2) oscillators in synth")
        names = synth.list_oscillator_names()
        logger.info("  oscillators in synth: %s", names)
        assert "je_rumble_1_1__layer0" in names, "layer0 missing"
        assert "je_rumble_1_1__layer2" in names, "layer2 missing"
        assert "je_rumble_1_1__layer1" not in names, "stick layer1 must not be created"
        logger.info("  assertions passed")
        time.sleep(2.0)
        e.stop()
        logger.info("Layered stop issued")
        for n in ["je_rumble_1_1__layer0", "je_rumble_1_1__layer2"]:
            osc = synth.peek_oscillator(n)
            assert osc is None or osc.is_silent, f"{n} not silent after stop"
        logger.info("  stop assertions passed")
    finally:
        synth.stop()


def _parse_device(spec):
    if spec is None:
        return None
    try:
        return int(spec)
    except ValueError:
        return spec


def main() -> None:
    p = argparse.ArgumentParser(
        description="ffb_shaker layer dispatch selftest")
    p.add_argument("--selftest-layered", action="store_true",
                   help="Run the layered-dispatch acceptance selftest")
    p.add_argument("--device", type=str, default=None,
                   help="Output device (integer index or name substring)")
    p.add_argument("--samplerate", type=int, default=48000,
                   help="Sample rate in Hz (default 48000)")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.selftest_layered:
        _selftest_layered(_parse_device(args.device), args.samplerate)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
