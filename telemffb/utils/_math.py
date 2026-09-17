#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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

import bisect
import json
import logging
import math
import random
import time
from collections import deque

import numpy as np
import akima

from . import conversions as conv

__all__ = [
    "Interp1D",
    "Akima1DInterpolator",
    "Smoother",
    "mix",
    "convert_between_units",
    "to_number",
    "clamp",
    "clamp_minmax",
    "scale",
    "scale_clamp",
    "piecewise_linear",
    "parse_trim_follow_family",
    "parse_trim_follow_curve",
    "suggest_calibration_speeds",
    "trim_follow_blend",
    "non_linear_scaling",
    "gaussian_scaling",
    "sine_point_in_time",
    "interpolate_curve_y_point",
    "get_gain_from_gs",
    "get_gain_from_speed",
    "pressure_from_altitude",
    "average",
    "polar_to_cartesian_deg",
    "add_vectors_deg",
    "LowPassFilter",
    "HighPassFilter",
    "Derivative",
    "Dampener",
    "PID",
    "DirectionModulator",
    "RandomDirectionModulator",
    "expocurve",
]

class Interp1D:
    def __init__(self, x, y, bounds_error=False, fill_value=None):
        self.x = np.asarray(x)
        self.y = np.asarray(y)
        self.bounds_error = bounds_error
        self.fill_value = fill_value

        if np.any(np.diff(self.x) <= 0):
            raise ValueError("x values must be strictly increasing")

    def __call__(self, x_new):
        x_new = np.asarray(x_new)

        # Interpolation for in-bounds
        y_interp = np.interp(x_new, self.x, self.y)

        # Out-of-bounds handling
        if not self.bounds_error and self.fill_value is not None:
            below = x_new < self.x[0]
            above = x_new > self.x[-1]
            y_interp = np.where(below, self.fill_value[0], y_interp)
            y_interp = np.where(above, self.fill_value[1], y_interp)
        elif self.bounds_error:
            if np.any(x_new < self.x[0]) or np.any(x_new > self.x[-1]):
                raise ValueError("A value in x_new is outside the interpolation range.")

        return y_interp if x_new.ndim > 0 else y_interp.item()


class Akima1DInterpolator:
    """
        A drop-in replacement for scipy.interpolate.Akima1DInterpolator
        using the 'akima' package backend, but mimicking SciPy's behavior.

        - No extrapolation by default: returns np.nan for out-of-bounds inputs.
        - Accepts any input shape (scalar, list, or ndarray).
        """

    def __init__(self, x, y, extrapolate=False):
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        if len(self.x) != len(self.y):
            raise ValueError("x and y must have the same length")

        # Must be strictly increasing
        sort_idx = np.argsort(self.x)
        self.x = self.x[sort_idx]
        self.y = self.y[sort_idx]

        self._interp = akima.interpolate
        self.extrapolate = extrapolate

    def __call__(self, x_new):
        scalar_input = np.isscalar(x_new)
        x_new = np.atleast_1d(x_new).astype(float)

        y_new = self._interp(self.x, self.y, x_new)

        if not self.extrapolate:
            out_of_bounds = (x_new < self.x[0]) | (x_new > self.x[-1])
            y_new = np.where(out_of_bounds, np.nan, y_new)

        return y_new[0] if scalar_input else y_new


class Smoother:
    def __init__(self):
        self.value_dict = {}

    def get_average(self, key, value, sample_size=10):
        # Get average of 'sample_size' instances of 'value', tracked by string 'key'
        if key not in self.value_dict:
            self.value_dict[key] = []
        self.value_dict[key].append(value)
        if len(self.value_dict[key]) > sample_size:
            self.value_dict[key].pop(0)

        values = self.value_dict.get(key, [])
        if not values:
            return 0
        return sum(values) / len(values)

    def get_rolling_average(self, key, value, window_ms=1000):
        # get average value of a rolling window of tracker string 'key', updated by 'value' over a period of 'window_ms'
        current_time_ms = time.time() * 1000  # Convert current time to milliseconds

        if key not in self.value_dict:
            self.value_dict[key] = deque()

        # Remove values older than the specified window
        while self.value_dict[key] and (current_time_ms - self.value_dict[key][0][1]) > window_ms:
            self.value_dict[key].popleft()

        self.value_dict[key].append((value, current_time_ms))

        if not self.value_dict[key]:
            return 0

        total = sum(val[0] for val in self.value_dict[key])
        return total / len(self.value_dict[key])


def mix(a, b, val):
    return a * (1 - val) + b * (val)

_TRUE_SET = frozenset(["true", "yes", "on", "enable", "enabled"])
_FALSE_SET = frozenset(["false", "no", "off", "disable", "disabled"])
_UNIT_CONVERSIONS = {
    "%": conv.percent,
    "kt": conv.kt2ms,
    "kph": conv.kmh2ms,
    "fpm": 0.00508,
    "m/s": 1,
    "mph": conv.mph2ms,
    "deg": 1,
    "ms": 1,
    "hz": 1,
    "m": 1,
    "ft": conv.ft2m,
    "in": conv.in2m,
}


def convert_between_units(value: float, from_unit: str, to_unit: str):
    """Convert ``value`` between two units of the same dimension using the
    canonical ``_UNIT_CONVERSIONS`` factors (each maps its unit to base SI).

    Returns the converted float, or None when either unit is unknown so the
    caller can leave the original value untouched.  Rows only ever offer
    same-dimension unit choices in their validvalues, so no dimensional
    checking is needed here.
    """
    f = _UNIT_CONVERSIONS.get(from_unit)
    t = _UNIT_CONVERSIONS.get(to_unit)
    if not f or not t:
        return None
    return value * f / t


def to_number(v: str):
    """Try to convert string to number
    If unable, return the original string
    """
    orig_v = v
    if isinstance(v, (bool, int, float)):
        return v

    v_lower = v.lower()
    if v_lower in _TRUE_SET:
        return True
    if v_lower in _FALSE_SET:
        return False

    scale = 1

    for unit, factor in _UNIT_CONVERSIONS.items():
        if v_lower.endswith(unit) or v_lower.startswith(unit):
            scale = factor
            v = v.strip(unit)
            break

    try:
        return round(float(v) * scale, 4) if "." in v else int(v) * scale
    except ValueError:
        return orig_v


def clamp(n, minn, maxn):
    return type(n)(sorted((minn, n, maxn))[1])


def clamp_minmax(n, max):
    return clamp(n, -max, max)


def scale(val, src: tuple, dst: tuple, return_round=False, return_int=False):
    """
    Scale the given value from the scale of src to the scale of dst.
    """
    if src[0] == src[1]: # avoid div/0
        return dst[1]
    result = (val - src[0]) * (dst[1] - dst[0]) / (src[1] - src[0]) + dst[0]
    if return_round:
        return round(result)
    elif return_int:
        return int(result)
    else:
        return result


def scale_clamp(val, src: tuple, dst: tuple, return_round=False, return_int=False):
    """
    Scale the given value from the scale of src to the scale of dst.
    and clamp the result to dst
    """
    v = scale(val, src, dst, return_round=return_round, return_int=return_int)
    return clamp(v, dst[0], dst[1])


def piecewise_linear(xs, ys, x):
    """Piecewise-linear lookup with edge-slope extrapolation.

    :param xs: sample x values, strictly increasing
    :param ys: sample y values, same length as xs (>= 2 points)
    :param x: lookup position
    :returns: interpolated y inside [xs[0], xs[-1]]; outside that range the
        nearest edge segment's slope is continued linearly (a flat clamp would
        silently stop correcting past the sampled band).
    """
    i = bisect.bisect_left(xs, x)
    if i <= 0:
        i = 1
    elif i >= len(xs):
        i = len(xs) - 1
    x0, x1 = xs[i - 1], xs[i]
    y0, y1 = ys[i - 1], ys[i]
    slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
    return y0 + slope * (x - x0)


def _parse_trim_curve_entry(data):
    """Parse ONE stored curve entry into an anchor-referenced dict, or None.

    Points are sorted and deduped on trim, then REBASED so offs(t0) == 0:
    t0 from the payload, falling back to the measured band's midpoint for
    curves saved before t0 existed (the sweep centers its band on the
    natural point). The rebase is idempotent. This pins the runtime
    invariant "trimmed for level => stick at physical center, zero force,
    zero delivered input" and keeps every lookup band-internal regardless
    of where the trim gauge's zero lies.
    """
    pts = sorted((float(p["t"]), float(p["offs"])) for p in data["points"])
    xs, ys = [], []
    for t, o in pts:  # drop duplicate trim values, keep xs strictly increasing
        if xs and abs(t - xs[-1]) < 1e-9:
            ys[-1] = o
        else:
            xs.append(t)
            ys.append(o)
    if len(xs) < 2:
        logging.warning("Trim-follow curve has fewer than 2 usable points; ignoring")
        return None
    t0 = float(data["t0"]) if "t0" in data else (xs[0] + xs[-1]) / 2.0
    ref = piecewise_linear(xs, ys, t0)
    return {
        "ias_kt": float(data.get("ias_kt") or 0.0),
        "t0": t0,
        "date": data.get("date"),
        # Provenance only (glider runs): the sink held while measuring.
        # Displayed in the stored-curve description; never used at runtime.
        "vs_fpm": data.get("vs_fpm"),
        "xs": xs,
        "ys": [y - ref for y in ys],
    }


def parse_trim_follow_family(value):
    """Parse the stored trim-calibration setting into a speed-sorted family.

    Single source of truth for the curve convention — the runtime property
    setter AND any display/offline reader must go through here (hand-rolled
    re-derivations of the runtime's math have rotted before: the takeover
    baseline computed a false value for months).

    Accepts the family form ``{"curves": [entry, ...]}``, the legacy
    single-curve blob ``{"points": ...}``, a JSON string of either, or
    'none'/empty. Returns a list of entries ``{ias_kt, t0, date, xs, ys, r}``
    sorted by ias_kt (ys anchor-rebased per entry, see
    :func:`_parse_trim_curve_entry`), or None when nothing is usable.
    Entries within 0.5 kt of each other dedupe to the later one in payload
    order (re-calibration semantics).

    ``r`` is the positional track R(v) at each entry — where the trimmed
    stick RESTS in follows-trim mode. Per adjacent speed pair the
    displacement is the AVERAGE of the two curves' independent estimates of
    the elevator-equivalent between their anchors (field data: the two
    estimates agree within ~2%); the chain is normalized to 0 at the
    median-index entry (the reference constant is sim-invisible — it rides
    identically in the virtual offset and the spring center) and clamped to
    +-1 WITH a warning: an extreme aircraft's follows-trim rest position
    truncates at the stick limits rather than silently changing behavior.
    """
    if not value or value == 'none':
        return None
    try:
        data = json.loads(value) if isinstance(value, str) else value
        raw_entries = data["curves"] if "curves" in data else [data]
        entries = []
        for raw in raw_entries:
            parsed = _parse_trim_curve_entry(raw)
            if parsed is not None:
                entries.append(parsed)
    except (ValueError, KeyError, TypeError) as e:
        logging.warning(f"Invalid trim-follow curve setting; ignoring ({e})")
        return None
    if not entries:
        return None

    # Sort by speed; near-identical speeds keep the later payload entry
    # (stable sort preserves payload order within equal keys).
    entries.sort(key=lambda e: e["ias_kt"])
    deduped = []
    for e in entries:
        if deduped and abs(e["ias_kt"] - deduped[-1]["ias_kt"]) < 0.5:
            deduped[-1] = e
        else:
            deduped.append(e)
    entries = deduped

    # Positional track: chain the averaged inter-anchor displacements.
    # xs are absolute trim, ys anchor-rebased, so offs_a evaluated at b's
    # anchor IS the displacement estimate S_a(t0_b - t0_a).
    chain = [0.0]
    for a, b in zip(entries, entries[1:]):
        est_a = piecewise_linear(a["xs"], a["ys"], b["t0"])
        est_b = -piecewise_linear(b["xs"], b["ys"], a["t0"])
        chain.append(chain[-1] + (est_a + est_b) / 2.0)
    ref = chain[len(entries) // 2]
    for e, c in zip(entries, chain):
        r = c - ref
        if abs(r) > 1.0:
            logging.warning(
                f"Trim-follow positional track clamped at the stick limits "
                f"for the {e['ias_kt']:.0f} kt calibration (R={r:+.2f}) — "
                f"the follows-trim rest position truncates there")
            r = clamp(r, -1.0, 1.0)
        e["r"] = r
    return entries


def parse_trim_follow_curve(value):
    """Legacy single-curve view of the stored setting: the median-speed
    entry's anchor-referenced ``(xs, ys)``, or None. Superseded by
    :func:`parse_trim_follow_family`; kept for transitional callers."""
    fam = parse_trim_follow_family(value)
    if fam is None:
        return None
    mid = fam[len(fam) // 2]
    return mid["xs"], mid["ys"]


def suggest_calibration_speeds(telem_data, sim):
    """Suggest 2-3 trim-calibration speeds (knots, rounded to 5) from the
    aircraft's declared speed envelope, or [] when the data is implausible.

    Anchors (clean configuration — calibrations are config-specific):
    LOW = 1.3 x clean stall (approach-margin factor: safely level-flyable,
    solidly in the nose-up trim region). HIGH = the max LEVEL-FLIGHT speed,
    not the red-line (most aircraft cannot hold Vne level): MSFS design
    cruise VC capped at 0.85 x the red-line, X-Plane Vno capped the same
    way. MIDDLE = the midpoint, only when the envelope is wide enough to
    need it (high/low > 1.6). All telemetry sources are m/s (the sims'
    kt/ft-per-s units are normalized upstream).

    Guards degrade to fewer (or no) suggestions rather than ever returning
    a confidently wrong number: MSFS's VS1 defaults to 0 when absent from
    the flightmodel, VC can be an internal estimate, and X-Plane datarefs
    can hold junk on oddball aircraft.
    """
    def pos(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    kt = 1.94384  # m/s -> knots
    vs = level_max = redline = None
    if sim == "MSFS":
        ds = getattr(telem_data, "DesignSpeed", None)
        if ds is not None and len(ds) >= 3:
            level_max = pos(ds[0])   # VC (design cruise) — TAS-referenced
            vs = pos(ds[2])          # VS1 (clean stall) — indicated
        # VC (flightmodel.cfg cruise_speed) is TAS at the design cruise
        # altitude, but suggestions are IAS targets: scale by the LIVE
        # IAS/TAS ratio — the exact conversion for the air currently being
        # flown in, no assumed altitude (and correctly altitude-aware:
        # achievable IAS falls as the user climbs). Guarded to a sane band;
        # outside it (or on the ground) the raw value stands, which field
        # data shows is a good low-altitude approximation for GA anyway.
        ratio = 1.0
        ias = pos(getattr(telem_data, "IAS", None))
        tas = pos(getattr(telem_data, "TAS", None))
        if ias and tas and tas > 5.0 and 0.5 <= ias / tas <= 1.05:
            ratio = ias / tas
        if level_max is not None:
            level_max *= ratio
        redline = pos(getattr(telem_data, "RefMaxIAS", None))  # indicated: no scaling
        if redline is None:
            # The estimated Vne is VC-derived, so it is TAS-referenced too.
            vne_kt = pos(getattr(telem_data, "Vne_kt", None))
            redline = (vne_kt / kt) * ratio if vne_kt else None
    elif sim == "XPLANE":
        vs = pos(getattr(telem_data, "Vs", None))
        # Vno is a structural LIMIT (top of the green arc), not a
        # performance capability — draggy GA aircraft often cannot hold it
        # in level flight (X-Plane's C172 tops out well below it). 0.9x
        # errs conservative: an unreachable suggestion strands the user
        # chasing a number; a slightly low one costs a few knots the
        # blend's edge-clamping absorbs.
        vno = pos(getattr(telem_data, "Vno", None))
        level_max = 0.9 * vno if vno is not None else None
        redline = pos(getattr(telem_data, "Vne", None))
    if vs is None or level_max is None:
        return []

    low = 1.3 * vs * kt
    high = level_max * kt
    if redline:
        high = min(high, 0.85 * redline * kt)
    if low < 40.0 or high <= low * 1.15:
        return []
    speeds = [low, high]
    if high / low > 1.6:
        speeds.insert(1, (low + high) / 2.0)
    rounded = [int(round(s / 5.0) * 5) for s in speeds]
    out = [rounded[0]]
    for s in rounded[1:]:
        if s - out[-1] >= 15:   # comfortably clear of the replace window
            out.append(s)
    return out if len(out) >= 2 else []


def trim_follow_blend(fam, t, ias_kt, include_r=True):
    """Evaluate the multi-speed trim-follow offset at trim ``t`` (ElevTrimPct
    space) and speed ``ias_kt`` (knots).

    Bracketing interpolation between the two nearest calibrated speeds in
    ANCHOR-ALIGNED space: the anchor t0(v) lerps, each bracket's shape is
    looked up at the same anchor-relative position, and the shapes lerp —
    which reconstructs translating-knee aircraft exactly where absolute-trim
    lerping smears them (SR22T: 2.5x under-correction). Beyond the
    calibrated speed range the exact lowest/highest calibration applies (no
    extrapolation across speed; per-curve edge-slope extrapolation across
    TRIM is unchanged). ``include_r`` folds in the positional track
    (follows-trim mode); centered mode passes False. Result clamped +-1.
    """
    lo = hi = fam[-1]
    w = 0.0
    for i, e in enumerate(fam):
        if ias_kt <= e["ias_kt"]:
            hi = e
            lo = fam[i - 1] if i > 0 else e
            span = hi["ias_kt"] - lo["ias_kt"]
            w = (ias_kt - lo["ias_kt"]) / span if span > 1e-9 else 0.0
            break
    t0v = lo["t0"] + w * (hi["t0"] - lo["t0"])
    x = t - t0v   # anchor-relative position, shared by both brackets
    s = piecewise_linear(lo["xs"], lo["ys"], lo["t0"] + x)
    if hi is not lo:
        s += w * (piecewise_linear(hi["xs"], hi["ys"], hi["t0"] + x) - s)
    if include_r:
        s += lo["r"] + w * (hi["r"] - lo["r"])
    return clamp(s, -1.0, 1.0)


def non_linear_scaling(x, min_val, max_val, curvature=1.0):
    # Scale the input value to a value between 0 and 1 within the given range
    scaled_value = (x - min_val) / (max_val - min_val)

    # Apply the non-linear scaling based on the specified curvature
    if curvature < 0:
        result = scaled_value ** (1 / abs(curvature))
    elif curvature > 0:
        result = scaled_value ** curvature
    else:
        result = scaled_value

    return result


def gaussian_scaling(x, min_val, max_val, peak_percentage=0.5, curve_width=1.0):
    # Calculate the midpoint of the range and the distance between the min and max values
    midpoint = (min_val + max_val) / 2
    range_distance = (max_val - min_val)

    # Calculate the value of x as a percentage between 0 and 1 in the range
    scaled_value = (x - min_val) / range_distance

    # Calculate the distance of the scaled value from the peak_percentage
    distance_from_peak = abs(scaled_value - peak_percentage)

    # Apply the Gaussian distribution to get the scaling factor
    scaling_factor = math.exp(-0.5 * ((distance_from_peak / (curve_width / 2)) ** 2))

    # Scale the result back to the desired range (0 to 1)
    result = scaling_factor

    return result


def sine_point_in_time(amplitude, period_ms, phase_offset_deg=0):
    current_time = time.perf_counter()  # Get the current time in seconds with high resolution

    # Convert frequency from milliseconds to Hz
    frequency_hz = 1 / (period_ms / 1000)

    # Calculate the angular frequency (2 * pi * frequency)
    angular_frequency = 2 * math.pi * frequency_hz

    phase_offset_rad = math.radians(phase_offset_deg)

    # Calculate the value of the sine wave at the current time with phase offset
    value = amplitude * math.sin(angular_frequency * current_time + phase_offset_rad)

    # print(f"Amp:{amplitude}     |Freq:{frequency_hz}     |Offset:{phase_offset_deg}        |Val:{value}")

    return value


def interpolate_curve_y_point(curve_dict, input_x, conversion_factor=1):
    """Interpolates the Y value given curve data and input x value."""
    points = curve_dict.get("points", [])
    smooth_curve_enabled = curve_dict.get("smooth_curve_enabled", False)

    # Extract x and y values from points
    x_values = np.array([p["x"] for p in points]) * conversion_factor  # Convert curve points from user units to m/s
    y_values = np.array([p["y"] for p in points])

    # Handle out-of-bounds x_values
    if input_x <= x_values[0]:
        return y_values[0]
    if input_x >= x_values[-1]:
        return y_values[-1]

    # Perform interpolation
    if smooth_curve_enabled:
        if len(x_values) < 4:
            # Fallback to linear interpolation for insufficient points
            interpolation = Interp1D(x_values, y_values, bounds_error=False,
                                     fill_value=(y_values[0], y_values[-1]))
        else:
            interpolation = Akima1DInterpolator(x_values, y_values)
    else:
        interpolation = Interp1D(x_values, y_values, bounds_error=False,
                                 fill_value=(y_values[0], y_values[-1]))

    return float(interpolation(input_x))


def get_gain_from_gs(curve_settings, input_gs):
    if isinstance(curve_settings, str):
        settings = json.loads(curve_settings)
    elif isinstance(curve_settings, dict):
        settings = curve_settings
    else:
        raise ValueError("Invalid input: must be a JSON string or a dictionary.")

    curve_pos = settings.get("curve_pos", {})
    curve_neg = settings.get("curve_neg", {})
    gain_pos = settings.get('gain_pos') / 100
    gain_neg = settings.get('gain_neg') / 100

    interpolated_pos = round(float(interpolate_curve_y_point(curve_pos, input_gs) / 100) * gain_pos, 3)
    interpolated_neg = round(float(interpolate_curve_y_point(curve_neg, input_gs) / 100) * gain_neg, 3)

    return interpolated_pos, interpolated_neg


def get_gain_from_speed(curve_settings : str | dict, input_airspeed_ms):
    """
    Interpolates the % force input airspeed and the advanced spring curve settings passed.

    Args:
        json_string (str): JSON-encoded string containing x and y curve dictionaries, units, and scale.
        input_airspeed_ms (float): The airspeed in m/s for which to calculate the interpolated values.

    Returns:
        dict: A dictionary containing the interpolated X and Y gain values as a factor (0...1).
    """
    # Unit conversion factors (to m/s)
    UNIT_CONVERSIONS = {
        "kt": conv.kt2ms,
        "mph": conv.mph2ms,
        "kph": conv.kmh2ms,
        "m/s": 1.0,
    }

    if isinstance(curve_settings, dict):
        settings = curve_settings
    else:
        # Parse JSON string
        settings = json.loads(curve_settings)
    assert settings is not None, "Invalid settings for speed curve."

    # Extract curves and units
    curve_x = settings.get("curve_x", {})
    curve_y = settings.get("curve_y", {})
    gain_x = settings.get('gain_x')/100
    gain_y = settings.get('gain_y')/100
    units = settings.get("units", "m/s")  # Default to m/s if units not specified

    # Conversion factor to m/s
    conversion_factor = UNIT_CONVERSIONS[units]

    # Interpolate X and Y values
    # print(f"x:{gain_x}, y:{gain_y}")
    interpolated_x = round(float(interpolate_curve_y_point(curve_x, input_airspeed_ms, conversion_factor) / 100) * gain_x, 3)
    interpolated_y = round(float(interpolate_curve_y_point(curve_y, input_airspeed_ms, conversion_factor) / 100) * gain_y, 3)

    return {"x": interpolated_x, "y": interpolated_y}


def pressure_from_altitude(altitude_m):
    """Calculate pressure at specified altitude

    Args:
        altitude_m (float): meters

    Returns:
        float: Pressure in kpa
    """
    return 101.3 * ((288 - 0.0065 * altitude_m) / 288) ** 5.256


def average(l):
    if not l:
        return 0
    return sum(l) / float(len(l))


def polar_to_cartesian_deg(angle_deg, magnitude):
    angle_rad = math.radians(angle_deg)
    x = magnitude * math.cos(angle_rad)
    y = magnitude * math.sin(angle_rad)
    return x, y


def add_vectors_deg(angle1_deg, mag1, angle2_deg, mag2):
    x1, y1 = polar_to_cartesian_deg(angle1_deg, mag1)
    x2, y2 = polar_to_cartesian_deg(angle2_deg, mag2)

    x_sum = x1 + x2
    y_sum = y1 + y2

    # Convert back to polar
    magnitude = math.hypot(x_sum, y_sum)
    angle_deg = math.degrees(math.atan2(y_sum, x_sum))

    return angle_deg, magnitude


class LowPassFilter:
    def __init__(self, cutoff_freq_hz, init_val=0.0, **kwargs):
        self.cutoff_freq_hz = cutoff_freq_hz
        self.alpha = 0.0
        self.x_filt = init_val
        self.last_update = time.perf_counter()

    def __call__(self, x):
        return self.update(x)

    def update(self, x):
        now = time.perf_counter()
        dt = now - self.last_update
        if dt > 1: self.x_filt = x  # initialize filter
        self.last_update = now
        self.alpha = dt / (1.0 / self.cutoff_freq_hz + dt)
        self.x_filt = self.alpha * x + (1.0 - self.alpha) * self.x_filt
        return self.x_filt

    @property
    def value(self):
        return self.x_filt


class HighPassFilter:
    def __init__(self, cutoff_freq_hz, init_val=0.0, **kwargs):
        self.RC = 1.0 / (2 * math.pi * cutoff_freq_hz)
        self.value = 0
        self.last_update = 0
        self.last_input = init_val
        self.value = init_val

    def __call__(self, x):
        return self.update(x)

    def update(self, x):
        now = time.perf_counter()
        dt = now - self.last_update
        if dt > 1:
            self.last_input = x  # initialize filter
            self.value = x

        self.last_update = now
        alpha = self.RC / (self.RC + dt)

        self.value = alpha * (self.value + x - self.last_input)
        self.last_input = x
        return self.value

    def reset(self):
        self.last_update = 0


class Derivative:
    def __init__(self, filter_hz=None) -> None:
        self.prev_update = 0
        self.prev_value = 0
        self.value = 0
        self.lpf = None
        self.derivative_dict = {}
        if filter_hz:
            self.lpf = LowPassFilter(filter_hz)

    def update(self, value):
        now = time.perf_counter()
        dx = value - self.prev_value
        self.prev_value = value
        dt = now - self.prev_update
        self.prev_update = now
        val = dx / dt
        if self.lpf:
            val = self.lpf.update(val)
        self.value = val

        return self.value

    def dampen_value(self, var, name, derivative_hz=5, derivative_k=0.1):
        # Check if derivative information is already stored, and initialize if not
        derivative_data = self.derivative_dict.get(name, None)
        if derivative_data is None:
            derivative_data = self.derivative_dict[name] = {
                'derivative': Derivative(derivative_hz),
                'cutoff_freq_hz': derivative_hz,
            }

        # Update the cutoff frequency if needed
        if derivative_data['cutoff_freq_hz'] != derivative_hz:
            derivative_data['derivative'].lpf.cutoff_freq_hz = derivative_hz
            derivative_data['cutoff_freq_hz'] = derivative_hz

        # Compute the derivative
        derivative = -derivative_data['derivative'].update(var) * derivative_k

        # Update the variable
        var += derivative

        return var


class Dampener(Derivative):
    def __init__(self, filter_hz=5, k=0.1):
        super().__init__(filter_hz)
        self.k = k

    def update(self, value, derivative_hz=5, derivative_k=0.1):
        # update filters if needed
        if self.lpf:
            if self.lpf.cutoff_freq_hz != derivative_hz:
                self.lpf.cutoff_freq_hz = derivative_hz
        if derivative_k != self.k:
            self.k = derivative_k

        derivative = -super().update(value) * self.k
        value += derivative
        return value


class PID:
    """Minimal dt-aware PID controller.

    Written for the trim-calibration leveling loops but generic. Pass ``dt``
    explicitly to :meth:`update` for deterministic stepping (fixed-rate loops or
    unit tests); leave it ``None`` to measure wall-clock dt via
    ``time.perf_counter()``.

    Features:
      - output clamping (``output_limits``)
      - conditional-integration anti-windup: the integral only accumulates when
        doing so would not drive an already-saturated output further into
        saturation
      - optional low-pass filtered derivative-on-error term
    """

    def __init__(self, kp=0.0, ki=0.0, kd=0.0, output_limits=(-1.0, 1.0),
                 integral_limit=None, derivative_lpf_hz=None):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_limits = output_limits
        # Optional symmetric clamp on the integral term contribution (ki * integral).
        self.integral_limit = integral_limit
        self._d_lpf = LowPassFilter(derivative_lpf_hz) if derivative_lpf_hz else None
        self.reset()

    def reset(self):
        self._integral = 0.0
        self._prev_error = None
        self._prev_time = None
        self.output = 0.0

    def set_gains(self, kp, ki, kd, preserve_integral_term=True):
        """Change gains in place (e.g. adaptive backoff on a live loop).

        With ``preserve_integral_term`` the integral state is rescaled so the
        integral's output contribution (``ki * integral``) does not step when
        ``ki`` changes — that contribution typically carries the steady-state
        component holding the plant, and a step there jolts the output.
        """
        if preserve_integral_term and self.ki != ki:
            if ki:
                self._integral *= self.ki / ki
            else:
                self._integral = 0.0
        self.kp = kp
        self.ki = ki
        self.kd = kd

    def update(self, error, dt=None):
        now = time.perf_counter()
        if dt is None:
            dt = 0.0 if self._prev_time is None else now - self._prev_time
        self._prev_time = now
        dt = max(dt, 0.0)

        p_term = self.kp * error

        # Derivative on error (optionally low-pass filtered).
        if self._prev_error is None or dt <= 0:
            d_raw = 0.0
        else:
            d_raw = (error - self._prev_error) / dt
        self._prev_error = error
        if self._d_lpf is not None:
            d_raw = self._d_lpf.update(d_raw)
        d_term = self.kd * d_raw

        pd = p_term + d_term
        new_integral = self._integral + error * dt
        lo, hi = self.output_limits

        # Conditional-integration anti-windup: predict saturation from the full
        # output; only commit the new integral if it would not push a saturated
        # output further out.
        raw = pd + self.ki * new_integral
        if not ((raw > hi and error > 0) or (raw < lo and error < 0)):
            self._integral = new_integral
            if self.integral_limit is not None and self.ki:
                max_i = abs(self.integral_limit / self.ki)
                self._integral = clamp(self._integral, -max_i, max_i)

        self.output = clamp(pd + self.ki * self._integral, lo, hi)
        return self.output


class DirectionModulator:
    pass


class RandomDirectionModulator(DirectionModulator):
    def __init__(self, *args, period=0.1, **kwargs):
        self.prev_upd = time.perf_counter()
        self.value = 0
        self.period = period

    def update(self):
        now = time.perf_counter()
        # dt = now - self.prev_upd
        if now - self.prev_upd > self.period:
            self.prev_upd = now
            random.seed()
            self.value = random.randint(0, 360)

        return self.value


def expocurve(x, k):
    # expo function for + k: y = (1-k)x + k( (1-e^(-ax)) / (1-e^-a))
    #       for negative k: y = (1+k)x + -k(e^(a(x-1))-e^(-a)) / (1-e^(-a))
    #   x = orig pct_max
    #   y = new pct_max
    #   k = expo value 0-1
    #   a = alpha, controls how much to bend the curve.
    #       a=5.5 gives approx 2x increase at 25% orig pct_max with k=0.5, 3x at 25% with k=1
    #               and 1/2x decrease with k=-0.5, 1/3x with k=-1 at 75%
    newvalue = 0
    expo_a = 5.5  # alpha
    if k >= 0:
        newvalue = (1 - k) * x + k * (1 - math.exp(-expo_a * x)) / (1 - math.exp(-expo_a))
    else:
        newvalue = (1 + k) * x + (-k) * (math.exp(expo_a * (x - 1)) - math.exp(-expo_a)) / (1 - math.exp(-expo_a))
    #print(f'expo input:{x} k:{k} output:{newvalue}')
    return newvalue
