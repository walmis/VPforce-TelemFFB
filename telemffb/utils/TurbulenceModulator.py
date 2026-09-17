import math
import time
from typing import NamedTuple


# Reference frame rate (seconds) used to calibrate alpha coefficients.
# Users tune alpha at ~60 Hz; dt normalization scales it for other rates.
_REF_DT = 1.0 / 60.0

# --------------------------------------------------------------------------- #
# Axis mixing gains — aerodynamically motivated ratios.                       #
#                                                                             #
# Vertical gusts change AoA directly → dominant pitch-stick force.            #
# Longitudinal gusts change dynamic pressure → minor pitch contribution.      #
# Lateral gusts create sideslip → roll (dihedral/sweep) & yaw (weathervane).  #
# --------------------------------------------------------------------------- #
_VERT_TO_PITCH = 1.0   # vertical gust  → pitch  (dominant)
_LON_TO_PITCH = 0.25   # longitudinal   → pitch  (dynamic-pressure effect)
_LAT_TO_ROLL = 0.7     # lateral gust   → roll   (dihedral/sweep coupling)
_LAT_TO_YAW = 0.5      # lateral gust   → yaw    (weathervane / sideslip)


class TurbulenceForces(NamedTuple):
    """Turbulence force components in pilot-centric stick axes.

    pitch: positive = pull back (nose up), negative = push forward (nose down)
    roll:  positive = deflect right, negative = deflect left
    yaw:   positive = right yaw (right rudder), negative = left yaw
    """
    pitch: float
    roll: float
    yaw: float


class TurbulenceModulator:
    """Detect atmospheric turbulence from body-frame wind changes and produce
    per-axis FFB forces (pitch, roll, yaw).

    The caller extracts sim-specific wind components and passes them as
    ``lateral``, ``vertical``, and ``longitudinal`` body-frame values.

    Body-frame conventions per sim
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ============  ===========  ============  ==============
    Sim           Lateral      Vertical      Longitudinal
    ============  ===========  ============  ==============
    MSFS / XP     RelWind[0]   RelWind[1]    RelWind[2]
    DCS           RelWind[2]   RelWind[1]    RelWind[0]
    ============  ===========  ============  ==============

    Signal chain
    ~~~~~~~~~~~~
    1. Frame-to-frame delta of each body-axis component.
    2. First-order high-pass filter (dt-normalised) per axis.
    3. Map HPF outputs to pilot-centric axes via aerodynamic mixing gains:
       - vertical  → pitch  (AoA change)
       - longitudinal → pitch (dynamic-pressure change, minor)
       - lateral → roll  (dihedral/sweep) and yaw (weathervane)
    4. Sensitivity-based normalisation, clamped to [0, intensity].
    5. Per-axis exponential smoothing (dt-normalised).
    """

    def __init__(self):
        self._prev_lateral: float | None = None
        self._prev_vertical: float | None = None
        self._prev_longitudinal: float | None = None
        self.hpf_lat = 0.0
        self.hpf_vert = 0.0
        self.hpf_lon = 0.0
        self.prev_pitch = 0.0
        self.prev_roll = 0.0
        self.prev_yaw = 0.0
        self._prev_time: float | None = None

    def update(
        self,
        lateral_wind: float,
        vertical_wind: float,
        longitudinal_wind: float,
        *,
        hpf_alpha: float = 0.95,
        smoothing_alpha: float = 0.3,
        sensitivity: float = 0.5,
        intensity: float = 0.2,
    ) -> TurbulenceForces:
        """Compute per-axis turbulence forces from body-frame wind components.

        Parameters
        ----------
        lateral_wind:
            Body-frame lateral wind (positive = starboard).
        vertical_wind:
            Body-frame vertical wind (positive = up).
        longitudinal_wind:
            Body-frame longitudinal wind (positive = forward).
        hpf_alpha:
            High-pass filter coefficient calibrated at 60 Hz.
            Closer to 1.0 → higher cutoff, passes faster changes only.
        smoothing_alpha:
            Output smoothing coefficient calibrated at 60 Hz.
            Higher → more responsive, less smooth.
        sensitivity:
            Gust sensitivity (0–1).  Higher = more sensitive.
        intensity:
            Maximum output force magnitude (0–1).

        Returns
        -------
        TurbulenceForces
            Named tuple ``(pitch, roll, yaw)`` with signed force components.
            Magnitudes are in [0, intensity].
        """
        now = time.perf_counter()

        if (self._prev_lateral is None
                or self._prev_vertical is None
                or self._prev_longitudinal is None
                or self._prev_time is None):
            self._prev_lateral = lateral_wind
            self._prev_vertical = vertical_wind
            self._prev_longitudinal = longitudinal_wind
            self._prev_time = now
            return TurbulenceForces(0.0, 0.0, 0.0)

        # --- dt-normalised alpha ---
        dt: float = now - self._prev_time
        self._prev_time = now
        if dt <= 0:
            dt = _REF_DT

        # α_corrected = α^(dt / dt_ref) keeps cutoff frequency constant
        dt_ratio = dt / _REF_DT
        alpha = hpf_alpha ** dt_ratio
        smooth = 1.0 - (1.0 - smoothing_alpha) ** dt_ratio

        # --- high-pass filter on each body axis ---
        d_lat = lateral_wind - self._prev_lateral
        d_vert = vertical_wind - self._prev_vertical
        d_lon = longitudinal_wind - self._prev_longitudinal

        self.hpf_lat = alpha * (self.hpf_lat + d_lat)
        self.hpf_vert = alpha * (self.hpf_vert + d_vert)
        self.hpf_lon = alpha * (self.hpf_lon + d_lon)

        self._prev_lateral = lateral_wind
        self._prev_vertical = vertical_wind
        self._prev_longitudinal = longitudinal_wind

        # --- map to pilot-centric force axes ---
        # Vertical gust up → AoA increases → nose pitches up → stick pulls back
        # Longitudinal gust forward → dynamic pressure up → slight nose-up
        raw_pitch = self.hpf_vert * _VERT_TO_PITCH + self.hpf_lon * _LON_TO_PITCH

        # Lateral gust starboard → port sideslip → aircraft rolls LEFT
        # (negative sign: starboard gust produces leftward roll force)
        raw_roll = -self.hpf_lat * _LAT_TO_ROLL

        # Lateral gust starboard → weathervane yaws LEFT (nose into wind)
        raw_yaw = -self.hpf_lat * _LAT_TO_YAW

        # --- sensitivity-based normalisation ---
        # max_delta maps sensitivity [0,1] → divisor [10, 1]
        max_delta = (1.0 - sensitivity) * 9.0 + 1.0

        # Joystick axes (pitch + roll) scaled together to preserve direction
        stick_mag = math.sqrt(raw_pitch ** 2 + raw_roll ** 2)
        stick_norm = min(stick_mag / max_delta, 1.0)

        if stick_mag > 0:
            scale = (stick_norm * intensity) / stick_mag
        else:
            scale = 0.0

        target_pitch = raw_pitch * scale
        target_roll = raw_roll * scale

        # Yaw scaled independently (separate device — pedals)
        yaw_norm = min(abs(raw_yaw) / max_delta, 1.0)
        target_yaw = math.copysign(yaw_norm * intensity, raw_yaw)

        # --- per-axis exponential smoothing ---
        self.prev_pitch = smooth * target_pitch + (1.0 - smooth) * self.prev_pitch
        self.prev_roll = smooth * target_roll + (1.0 - smooth) * self.prev_roll
        self.prev_yaw = smooth * target_yaw + (1.0 - smooth) * self.prev_yaw

        return TurbulenceForces(self.prev_pitch, self.prev_roll, self.prev_yaw)