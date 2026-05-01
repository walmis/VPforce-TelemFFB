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

import logging
import threading
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
}

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


class HapticEffect:
    """Mirror of ffb_rhino.HapticEffect for the bass-shaker device type."""

    device = None  # placeholder for compat with ffb_rhino.HapticEffect.device

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
        with _synth._lock:
            osc = _synth._oscillators.get(self.name)
            return osc is not None and not osc.is_silent

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

        with _synth._lock:
            osc = _synth._oscillators.get(self.name)
            if osc is None:
                osc = Oscillator(_synth.samplerate, _synth.blocksize)
                _synth._oscillators[self.name] = osc
            osc.set(self.frequency, self.magnitude)
        logger.debug("Shaker start name=%r freq=%.2f mag=%.3f duration=%d",
                     self.name, self.frequency, self.magnitude, self.duration)

        # Cancel any prior duration timer; schedule a new stop if duration > 0.
        if self._duration_timer is not None:
            self._duration_timer.cancel()
            self._duration_timer = None
        if self.duration > 0:
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
        with _synth._lock:
            osc = _synth._oscillators.get(self.name)
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
        _synth.remove_oscillator(self.name)
        logger.debug("Shaker destroy name=%r", self.name)

    def __del__(self):
        try:
            self.destroy()
        except Exception:
            pass
