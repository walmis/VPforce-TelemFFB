#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
"""What unit a telemetry value is in, and what else it can be shown in.

The Monitor tab's Unit column is filled from here. A key's unit is decided,
in order, by:

1. for MSFS, the unit its SimVar is actually subscribed in - so an
   ``sc_overrides`` entry or a runtime-added variable reports the unit it
   asked SimConnect for, not the one the field usually carries. A SimVar
   with a ``scale`` transform is skipped: the transform may change the unit
   (``AccBody`` is subscribed in ft/s² and scaled to g), so the registry
   below is trusted for it instead;
2. ``SIM_UNIT_OVERRIDES``, for the few keys whose unit differs by sim;
3. ``TELEMETRY_UNITS``, the registry kept in step with ``BaseTelemetryData``'s
   documented units (tests/test_telemetry_units.py fails when they drift).

Units are short canonical tokens (``"m/s"``, ``"kt"``, ``"deg"``, ...). A
token belonging to a family in ``UNIT_FAMILIES`` can be converted to any
other unit in the same family; the factors are built from
``telemffb.utils.conversions`` so the display agrees with the effect code.

Pure Python, no Qt: the in-sim panels can use it as well.
"""
from typing import Dict, List, Mapping, Optional, Tuple

import telemffb.globals as G
from telemffb.utils import conversions as conv

#: A 0..1 (or -1..1) normalized fraction - SimConnect's "Percent Over 100".
PCT_OVER_100 = "%/100"

#: Each family maps its units to a factor onto the family's base unit:
#: ``value_in_base = value * factor``. The first unit listed is the base.
UNIT_FAMILIES: Dict[str, Dict[str, float]] = {
    "speed": {"m/s": 1.0, "kt": conv.kt2ms, "kph": conv.kmh2ms, "mph": conv.mph2ms,
              "fpm": conv.ft2m / 60.0, "ft/s": conv.ft2m},
    "length": {"m": 1.0, "ft": conv.ft2m, "in": conv.in2m, "km": 1000.0, "nm": 1852.0},
    "angle": {"deg": 1.0, "rad": conv.rad2deg},
    "angular rate": {"deg/s": 1.0, "rad/s": conv.rad2deg},
    "angular acceleration": {"deg/s²": 1.0, "rad/s²": conv.rad2deg},
    "acceleration": {"m/s²": 1.0, "g": 1.0 / conv.mpss2gs, "ft/s²": conv.ft2m},
    "pressure": {"Pa": 1.0, "hPa": 100.0, "inHg": 3386.389, "psi": 6894.757},
    "density": {"kg/m³": 1.0, "slug/ft³": 1.0 / conv.slugft3},
    "fraction": {PCT_OVER_100: 1.0, "%": conv.percent},
    "time": {"s": 1.0, "ms": 0.001, "min": 60.0},
    "force": {"N": 1.0, "lbf": 4.448222},
}

#: unit -> (family name, factor), built once from UNIT_FAMILIES.
_UNIT_INDEX: Dict[str, Tuple[str, float]] = {
    unit: (family, factor)
    for family, units in UNIT_FAMILIES.items()
    for unit, factor in units.items()
}

#: SimConnect unit strings (lower-cased) -> canonical token. ``None`` means
#: the value carries no physical unit (flags, enums, raw numbers). A string
#: missing from here is shown verbatim, with nothing to convert it to.
SIMCONNECT_UNIT_ALIASES: Dict[str, Optional[str]] = {
    # unitless
    "": None, "bool": None, "boolean": None, "enum": None, "number": None,
    "numbers": None, "float": None, "int": None, "flags": None, "mask": None,
    # speed
    "meter/second": "m/s", "meters/second": "m/s", "meter per second": "m/s",
    "meters per second": "m/s", "m/s": "m/s",
    "knot": "kt", "knots": "kt", "kt": "kt", "kts": "kt",
    "feet/minute": "fpm", "feet per minute": "fpm", "ft/min": "fpm",
    "feet/second": "ft/s", "feet per second": "ft/s", "ft/s": "ft/s",
    "kilometers per hour": "kph", "kilometer per hour": "kph", "km/h": "kph",
    "miles per hour": "mph", "mile per hour": "mph", "mph": "mph",
    # length
    "meter": "m", "meters": "m", "m": "m",
    "foot": "ft", "feet": "ft", "ft": "ft",
    "inch": "in", "inches": "in", "in": "in",
    "kilometer": "km", "kilometers": "km", "km": "km",
    "nautical mile": "nm", "nautical miles": "nm", "nmile": "nm", "nmiles": "nm",
    # angle
    "degree": "deg", "degrees": "deg", "deg": "deg",
    "radian": "rad", "radians": "rad", "rad": "rad",
    "degree per second": "deg/s", "degrees per second": "deg/s",
    "radian per second": "rad/s", "radians per second": "rad/s",
    "degree per second squared": "deg/s²", "degrees per second squared": "deg/s²",
    "radian per second squared": "rad/s²", "radians per second squared": "rad/s²",
    # acceleration
    "meter per second squared": "m/s²", "meters per second squared": "m/s²",
    "foot per second squared": "ft/s²", "feet per second squared": "ft/s²",
    "gforce": "g", "g force": "g", "gforces": "g",
    # fraction
    "percent over 100": PCT_OVER_100, "position": PCT_OVER_100, "percent": "%",
    # pressure
    "pascal": "Pa", "pascals": "Pa",
    "hectopascal": "hPa", "hectopascals": "hPa", "millibar": "hPa", "millibars": "hPa", "mbar": "hPa",
    "inch of mercury": "inHg", "inches of mercury": "inHg", "inhg": "inHg",
    "psi": "psi", "pound per square inch": "psi", "pounds per square inch": "psi",
    # density
    "kilogram per cubic meter": "kg/m³", "kilograms per cubic meter": "kg/m³",
    "slug per cubic foot": "slug/ft³", "slugs per cubic feet": "slug/ft³",
    # time
    "second": "s", "seconds": "s", "minute": "min", "minutes": "min",
    # other
    "rpm": "rpm", "rpms": "rpm", "hz": "Hz", "hertz": "Hz",
    "newton": "N", "newtons": "N", "kilogram": "kg", "kilograms": "kg",
    "celsius": "°C",
}

#: Display unit per telemetry key, by BaseTelemetryData's documented units.
#: ``None`` means "deliberately no unit" (counts, raw numbers, bitfields), so
#: every float field is listed and the drift test can tell an omission from
#: a decision.
TELEMETRY_UNITS: Dict[str, Optional[str]] = {
    # timing
    "T": "s", "maxFrameTime": None, "avgFrameTime": None,
    # airspeed / atmosphere
    "TAS": "m/s", "TAS_kt": "kt", "IAS": "m/s", "IAS_kt": "kt", "Mach": None,
    "VerticalSpeed": "m/s", "GroundSpeed": "m/s", "DynPressure": "Pa",
    "AirDensity": "kg/m³", "DesignSpeed": "m/s",
    # stall / reference speeds
    "StallAoA": "deg", "StallFracL": PCT_OVER_100, "StallFracR": PCT_OVER_100, "StallFrac": PCT_OVER_100,
    "WarnAlpha": "deg", "Vle": "m/s", "Vfe": "m/s", "Vno": "m/s", "Vs": "m/s",
    "Vne": "m/s", "RefMaxIAS": "m/s", "RefMaxIAS_kt": "kt", "Vne_ms_calc": "m/s",
    "Vne_kt": "kt", "Qvne": "Pa", "Qvc_gain": None, "Vc_kt": "kt", "Vso": "kt",
    # attitude / position
    "AoA": "deg", "SideSlip": "deg", "VelRotBody": "deg/s", "Incidence": None,
    "Heading": "deg", "Pitch": "deg", "Roll": "deg",
    "AGL": "m", "MSL": "m", "X": "m", "Y": "m",
    # accelerations / velocities
    "ACCs": "g", "AccBody": "g", "AccBody_ms": "m/s²", "G": "g", "Gaxil": "g",
    "VlctVectors": "m/s", "VelAcf": "m/s", "VelWorld": "m/s", "Wind": "m/s",
    "AmbWind": "m/s", "RelWind": "m/s",
    # gear / surfaces / mechanisms
    "WeightOnWheels": None, "BumpIntensity": None, "gear_value": PCT_OVER_100,
    "GearPos": PCT_OVER_100, "RetractableGear": None, "Gear": PCT_OVER_100, "Flaps": PCT_OVER_100,
    "Spoilers": PCT_OVER_100, "speedbrakes_value": PCT_OVER_100, "Speedbrakes": PCT_OVER_100,
    "SpeedbrakePos": PCT_OVER_100, "canopy_value": PCT_OVER_100, "Canopy": PCT_OVER_100,
    "flaps_value": PCT_OVER_100, "TailHook": PCT_OVER_100, "FuelBoom": PCT_OVER_100, "WingFold": PCT_OVER_100,
    # control surfaces
    "ElevDefl": "deg", "ElevDeflPct": PCT_OVER_100, "ElevDeflPctLR": PCT_OVER_100, "ElevPos": PCT_OVER_100,
    "ElevTrim": "deg", "ElevTrimMin": "deg", "ElevTrimMax": "deg",
    "ElevTrimUpLmt": "deg", "ElevTrimDnLmt": "deg", "ElevTrimPct": PCT_OVER_100,
    "ElevTrimNeutral": "deg", "AileronDefl": "deg", "AileronDeflPctLR": PCT_OVER_100,
    "AileronPos": PCT_OVER_100, "AileronTrim": "deg", "AileronTrimPct": PCT_OVER_100,
    "RudderDefl": "deg", "RudderDeflPct": PCT_OVER_100, "RudderPos": PCT_OVER_100,
    "CollectivePos": PCT_OVER_100, "TailRotorPos": PCT_OVER_100, "RudderTrimPct": PCT_OVER_100,
    # engines
    "EngRPM": "%", "ThrottlePct": "%", "ActualRPM": "rpm", "PropRPM": "rpm",
    "RotorRPM": "rpm", "EngPCT": "%", "RPM": "rpm", "MaxRPM": "rpm",
    "Afterburner": None, "AfterburnerPct": PCT_OVER_100, "PropThrust": "N", "FTIT": "°C",
    "NozzlePos": PCT_OVER_100, "FuelFlow": None,
    # hydraulics
    "HydSys": None, "HydPress": "psi",
    # control surfaces (DCS MechInfo, -1..1 deflection)
    "controlsurfaces_rudder_right": PCT_OVER_100,
    # autopilot following (raw, whatever the named variable reports)
    "APFollowX": None, "APFollowY": None, "APFollowRudder": None,
    "APRollServo": None, "APPitchServo": None, "APYawServo": None,
    # weapons / countermeasures (counts)
    "Guns": None, "FlareCount": None, "ChaffCount": None, "Bombs": None,
    "Rockets": None, "Missiles": None,
    # ground handling
    "Brakes": PCT_OVER_100, "CenterSteerAnglePct": PCT_OVER_100, "WaterRudderExt": PCT_OVER_100,
    # device / FFB pipeline (normalized)
    "ForceXY": PCT_OVER_100, "JoyXY": PCT_OVER_100, "StickXY": PCT_OVER_100, "StickXY_offset": PCT_OVER_100,
    "phys_x": PCT_OVER_100, "phys_y": PCT_OVER_100, "phys_x_pos": PCT_OVER_100, "phys_y_pos": PCT_OVER_100,
    "StickX": PCT_OVER_100, "StickY": PCT_OVER_100, "TRIM_DELTA": None,
    # helicopters
    "TailRotorPedalPos": PCT_OVER_100, "CyclicTrimX": PCT_OVER_100, "CyclicTrimY": PCT_OVER_100,
    "BladeSlap": PCT_OVER_100, "SEMAx": None, "SEMAy": None,
    # FFB LVAR API
    "ffbApiVersion": None, "ffbFeatures": None,
    "ffbTrimCyclicPitch": PCT_OVER_100, "ffbTrimCyclicRoll": PCT_OVER_100,
    "ffbTrimCollective": PCT_OVER_100, "ffbTrimPedals": PCT_OVER_100,
    "ffbHydLossCyclicPitch": PCT_OVER_100, "ffbHydLossCyclicRoll": PCT_OVER_100,
    "ffbHydLossCollective": PCT_OVER_100, "ffbHydLossPedals": PCT_OVER_100,
    # aircraft-specific L:vars (raw)
    "hpgAfcsMaster": None, "hpgSEMAx": PCT_OVER_100, "hpgSEMAy": PCT_OVER_100, "hpgSEMAyaw": PCT_OVER_100,
    "AW109_aileron_trim_rate": None, "AW109_aileron_trim_req": None,
    "AW109_collective_axis_afcs_pos": None, "AW109_collective_input_ratio": None,
    "AW109_collective_ratio": None, "AW109_collective_trim_rate": None,
    "AW109_elevator_trim_rate": None, "AW109_elevator_trim_req": None,
    "AW109_rud_sim_trim_left": None, "AW109_rud_sim_trim_right": None,
    "AW109_rud_trim_coarse": None, "AW109_rud_trim_fine": None,
    "AW109_rud_trim_thresh_hi": None, "AW109_rud_trim_thresh_lo": None,
    "AW109_rud_trim_zero": None, "AW109_rudder_trim_rate": None,
    "AW109_rudder_trim_req": None, "AW109_target_collective": None,
    "AW109_target_pitch": None, "AW109_target_roll": None, "AW109_target_yaw": None,
    "FI_VibX": None, "FI_VibY": None, "FI_VibZ": None,
    # IL-2 motion
    "acc_vectors": None, "rot_velocity": "rad/s", "rot_accel": "rad/s²",
    "BuffetFrequency": "Hz", "BuffetAmplitude": None,
}

#: Keys whose unit differs for one sim, by telemetry ``src``.
SIM_UNIT_OVERRIDES: Dict[str, Dict[str, Optional[str]]] = {
    # IL-2's T is a tick counter, not seconds
    "IL2": {"T": None},
    "IL2K": {"T": None},
    # BMS engine_rpm is percent of max, not an absolute rpm
    "BMS": {"RPM": "%"},
}


def normalize_simconnect_unit(sc_unit: Optional[str]) -> Optional[str]:
    """The canonical token for a SimConnect unit string; ``None`` for a
    unitless one; the string itself (stripped) when it is not recognized."""
    if sc_unit is None:
        return None
    text = str(sc_unit).strip()
    return SIMCONNECT_UNIT_ALIASES.get(text.lower(), text)


def _simconnect_unit(key: str) -> Optional[str]:
    """The canonical unit MSFS is subscribed to for ``key``, or None when
    there is no live subscription for it, it is transformed by a scale, or
    it is unitless."""
    simconnect = getattr(getattr(G, "telem_manager", None), "simconnect", None)
    get_unit = getattr(simconnect, "get_var_unit", None)
    if get_unit is None:
        return None
    found = get_unit(key)
    if not found:
        return None
    sc_unit, scale = found
    if scale is not None:
        return None
    return normalize_simconnect_unit(sc_unit)


def unit_for(key: str, frame: Mapping) -> Optional[str]:
    """The unit ``frame[key]`` is in, or None when it has none (or none is
    known). See the module docstring for the order things are consulted."""
    src = frame.get("src")
    if src == "MSFS":
        unit = _simconnect_unit(key)
        if unit is not None:
            return unit
    overrides = SIM_UNIT_OVERRIDES.get(src)
    if overrides is not None and key in overrides:
        return overrides[key]
    return TELEMETRY_UNITS.get(key)


def conversions_for(unit: Optional[str]) -> List[str]:
    """Every unit ``unit`` can be shown in, itself included, in family order;
    empty when there is nothing to convert it to."""
    found = _UNIT_INDEX.get(unit) if unit else None
    if found is None:
        return []
    units = list(UNIT_FAMILIES[found[0]])
    return units if len(units) > 1 else []


def _convert_number(x, factor):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return x
    return float(x) * factor


def convert(value, from_unit: Optional[str], to_unit: Optional[str]):
    """``value`` (a number, or a list/tuple of them) re-expressed from
    ``from_unit`` in ``to_unit``. Non-numeric elements are passed through,
    and the value is returned untouched when the units are not in the same
    family."""
    src = _UNIT_INDEX.get(from_unit) if from_unit else None
    dst = _UNIT_INDEX.get(to_unit) if to_unit else None
    if src is None or dst is None or src[0] != dst[0] or from_unit == to_unit:
        return value
    factor = src[1] / dst[1]
    if isinstance(value, (list, tuple)):
        return [_convert_number(x, factor) for x in value]
    return _convert_number(value, factor)
