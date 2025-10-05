import numpy as np


import math


class TurbulenceModulator:
    """
    Simulates atmospheric turbulence effects for force feedback by analyzing wind velocity changes.

    This class processes relative wind data to generate realistic turbulence forces that can be
    applied to flight controls. It uses high-pass filtering to detect rapid wind changes and
    converts them into force magnitude and direction suitable for haptic feedback systems.

    The turbulence simulation works by:
    1. Monitoring changes in relative wind velocity (X and Z components)
    2. High-pass filtering the wind changes to isolate turbulent fluctuations
    3. Converting wind deltas to force magnitude using configurable sensitivity
    4. Smoothing the output force to prevent jarring transitions
    5. Calculating force direction based on wind gust angle

    Attributes:
        prev_wind_x (float): Previous frame's wind X-component for delta calculation
        prev_wind_z (float): Previous frame's wind Z-component for delta calculation  
        hpf_x (float): High-pass filtered wind change in X direction
        hpf_z (float): High-pass filtered wind change in Z direction
        prev_force (float): Previous frame's force output for smoothing
    """

    def __init__(self):
        """
        Initialize the turbulence modulator with default state.

        All wind history and filter states are reset to initial values.
        """
        self.prev_wind_x = None
        self.prev_wind_z = None
        self.hpf_x = 0.0
        self.hpf_z = 0.0
        self.prev_force = 0.0

    def update(self, telem_data,
               turbulence_hpf_alpha=0.95,
               turbulence_smoothing_alpha=0.3,
               turbulence_sensitivity=0.5,
               turbulence_intensity=0.2):
        """
        Process telemetry data to generate turbulence force and direction.

        Args:
            telem_data (dict): Telemetry data containing 'RelWind' key with [x, y, z] wind vector
            turbulence_hpf_alpha (float, optional): High-pass filter coefficient (0.0-1.0).
                Higher values = more aggressive filtering. Defaults to 0.95.
            turbulence_smoothing_alpha (float, optional): Output smoothing coefficient (0.0-1.0).
                Higher values = more responsive but less smooth. Defaults to 0.3.
            turbulence_sensitivity (float, optional): Sensitivity to wind changes (0.0-1.0).
                Lower values = more sensitive to small changes. Defaults to 0.5.
            turbulence_intensity (float, optional): Maximum force intensity multiplier (0.0-1.0).
                Higher values = stronger turbulence effects. Defaults to 0.2.

        Returns:
            tuple: (force_magnitude, force_angle)
                - force_magnitude (float): Normalized force strength (0.0-turbulence_intensity)
                - force_angle (int): Force direction in degrees (0-359), where 0° is positive X-axis

        Note:
            Returns (0.0, 0) if telemetry data is invalid or during initialization.
            The force angle represents the direction opposite to the wind gust direction,
            simulating the aircraft's resistance to the turbulent air mass.
        """
        try:
            wind_x = telem_data['RelWind'][0]
            wind_z = telem_data['RelWind'][2]
        except (KeyError, IndexError, TypeError):
            return 0.0, 0

        if self.prev_wind_x is None or self.prev_wind_z is None:
            self.prev_wind_x = wind_x
            self.prev_wind_z = wind_z
            return 0.0, 0

        max_delta = (1.0 - turbulence_sensitivity) * 9.0 + 1.0

        dx = wind_x - self.prev_wind_x
        dz = wind_z - self.prev_wind_z

        self.hpf_x = turbulence_hpf_alpha * (self.hpf_x + dx)
        self.hpf_z = turbulence_hpf_alpha * (self.hpf_z + dz)

        self.prev_wind_x = wind_x
        self.prev_wind_z = wind_z

        delta_mag = math.sqrt(self.hpf_x ** 2 + self.hpf_z ** 2)
        normalized = min(delta_mag / max_delta, 1.0)
        target_force = normalized * turbulence_intensity

        smoothed_force = (
            turbulence_smoothing_alpha * target_force
            + (1 - turbulence_smoothing_alpha) * self.prev_force
        )
        self.prev_force = smoothed_force

        gust_angle = np.degrees(np.arctan2(self.hpf_z, self.hpf_x))
        force_angle = (gust_angle + 180 + 90 + 360) % 360

        return smoothed_force, int(force_angle)