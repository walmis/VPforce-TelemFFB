#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
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

"""FFB device backend interface.

Codifies the device/effect-handle surface the application actually consumes,
so that force-feedback hardware backends are interchangeable:

- ``FFBRhino``/``FFBEffectHandle`` (ffb_rhino.py): the native VPforce raw-HID
  PID backend, with the full capability set.
- ``DInputFFBDevice`` (ffb_dinput.py): generic DirectInput FFB devices via the
  DInput bridge DLL, with a reduced capability set.

The contract mirrors the surface the aircraft/effect code and the test mocks
(tests/framework/base.py) already rely on.  ``HapticEffect`` sits above this
seam and is backend-neutral: it only calls ``device.create_effect()`` and the
handle operations defined here.

Capability flags — not firmware-version checks — are how app code should gate
backend-specific features (spring adjuster, detents, gains, ...).  A flag being
True means the backend *class* implements the concept; runtime probes (e.g.
``supports_axis_override()``, which depends on device firmware) remain
authoritative where they exist.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True)
class DeviceCapabilities:
    """What a device backend can do beyond the universal effect set.

    The universal set (available on every backend) is: condition effects
    (spring/damper/inertia/friction), constant force, periodic effects with
    the standard waveforms, envelopes, and axis/button input reads.
    """
    has_spring_adjuster: bool = False   # EFFECT_SPRING_ADJUSTER (VPforce firmware)
    has_detents: bool = False           # EFFECT_DETENT (VPforce firmware)
    has_override_start: bool = False    # OP_START_OVERRIDE 'sticky' start
    has_axis_override: bool = False     # firmware-fixed axis output to the sim
    has_force_telemetry: bool = False   # ForceX/ForceY in the input report
    has_cp_telemetry: bool = False      # device-reported spring center point
    has_gains: bool = False             # Configurator gain sliders (get/set)
    has_deadzone: bool = False          # device-side axis deadzone
    has_firmware_version: bool = False  # queryable firmware version
    effect_slots_hint: Optional[int] = None  # None = unknown / firmware-managed


#: The native VPforce device: everything on.
VPFORCE_CAPABILITIES = DeviceCapabilities(
    has_spring_adjuster=True,
    has_detents=True,
    has_override_start=True,
    has_axis_override=True,
    has_force_telemetry=True,
    has_cp_telemetry=True,
    has_gains=True,
    has_deadzone=True,
    has_firmware_version=True,
    effect_slots_hint=None,
)


class BaseEffectHandle:
    """One allocated effect slot on a device.

    Implementations must provide the operations below; ``HapticEffect`` is the
    only intended caller.  All setters are expected to write-coalesce (skip
    the transport when parameters are unchanged) where the transport is
    expensive.
    """
    effect_id = None
    type = None

    @property
    def started(self) -> bool:
        raise NotImplementedError

    def invalidate(self):
        """Forget the device slot (after a device-side reset dropped it) so
        the owning HapticEffect lazily re-creates on next start."""
        raise NotImplementedError

    def start(self, loopCount=1, override=False):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def destroy(self):
        raise NotImplementedError

    def setEffect(self, **kwargs):
        raise NotImplementedError

    def setCondition(self, condition):
        """Apply an FFBReport_SetCondition (the universal condition value
        object; parameterBlockOffset selects the axis, units are +-4096)."""
        raise NotImplementedError

    def setPeriodic(self, freq, magnitude, direction, duration=0, **kwargs):
        raise NotImplementedError

    def setConstantForce(self, magnitude, direction, **kwargs):
        raise NotImplementedError

    def setEnvelope(self, envelope):
        raise NotImplementedError


class BaseFFBDevice(QObject):
    """A connected FFB device.

    Signals and the input/effect surface follow the shapes established by
    FFBRhino; the app observes connection state exclusively through
    ``deviceConnected`` and reads input via ``get_input()`` snapshots.

    VPforce-only operations have safe defaults here (no-op or None) so
    backend-agnostic call sites degrade without conditionals; feature-bearing
    UI should gate on ``caps`` instead of calling and hoping.
    """
    buttonPressed = pyqtSignal(int)
    buttonReleased = pyqtSignal(int)
    deviceConnected = pyqtSignal(bool)

    firmware_version: Optional[str] = None

    @property
    def caps(self) -> DeviceCapabilities:
        return DeviceCapabilities()

    @property
    def serial(self) -> Optional[str]:
        return None

    def create_effect(self, effect_type) -> Optional[BaseEffectHandle]:
        """Allocate a device effect slot, or None when the type is
        unsupported or the device pool is exhausted."""
        raise NotImplementedError

    def get_input(self):
        """Latest input snapshot: axisXY/rawAxisXY/CP_scaled_axisXY/CP_XY/
        forceXY/axisOverrideActive/isButtonPressed/getPressedButtons."""
        raise NotImplementedError

    def reset_effects(self):
        pass

    def supports_axis_override(self) -> bool:
        return False

    # --- VPforce-only surface: safe defaults ------------------------------
    def set_deadzone(self, deadzone):
        logging.debug("set_deadzone: not supported by this device backend")

    def get_gains(self):
        return None

    def set_gain(self, slider_id, value):
        logging.debug("set_gain: not supported by this device backend")

    def send_axis_override(self, x_mode=0, x_value=0, y_mode=0, y_value=0, watchdog_ms=1000):
        logging.debug("send_axis_override: not supported by this device backend")

    def clear_axis_override(self):
        pass

    def get_firmware_version(self, cached=True) -> Optional[str]:
        return None
