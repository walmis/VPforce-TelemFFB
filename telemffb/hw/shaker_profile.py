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

"""Shaker calibration profile.

Hardware-specific drive parameters for single-pulse haptic effects on a
bass shaker. Resonance frequency and carrier offset are advisory (used as
defaults in the calibration UI); halfwaves / attack / release / brake
values are consumed by Oscillator.trigger_pulse() in shaker_synth.py via
ffb_shaker._pulse_kwargs().
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ShakerProfile:
    name: str = "Generic"
    schema_version: int = 1
    description: str = ""

    # Resonance / carrier — advisory; tuner UI defaults only.
    f_res_hz: float = 45.0
    carrier_offset_pct: float = 15.0

    # Pulse shape (consumed by Oscillator.trigger_pulse).
    halfwaves: int = 2
    attack_ms: float = 1.5
    release_ms: float = 2.0
    gain: float = 1.0

    # Active braking. Brake is exactly one halfwave at carrier_hz,
    # phase-inverted, started after brake_delay_ms.
    brake_enabled: bool = False
    brake_amp_pct: float = 40.0
    brake_delay_ms: float = 0.5

    # Metadata.
    created_iso: str = ""
    notes: str = ""


DEFAULT_PROFILE = ShakerProfile()
