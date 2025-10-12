"""
Utility functions and helpers for telemetry effect testing.
"""
from typing import Dict, Any, Optional, Tuple


class TelemetryDataBuilder:
    """Builder class for creating test telemetry data.
    
    Provides a fluent interface for constructing telemetry dictionaries
    with sensible defaults.
    """
    
    def __init__(self):
        self._data = {
            "SimOnGround": 0,
            "WeightOnWheels": [0, 0, 0],
            "GroundSpeed": 0.0,
            "CenterSteerAngle": 0.0,
            "CenterSteerAnglePct": 0.0,
            "WaterRudderExt": 0.0,
            "SurfaceType": "Asphalt",
            "FFBType": "joystick",
            "AircraftType": "TestAircraft",
            "APMaster": 0,
            "APServos": 0,
            "ElevTrimPct": 0.0,
            "AileronTrimPct": 0.0,
            "RudderTrimPct": 0.0,
            "ElevDeflPct": 0.0,
            "AileronDeflPctLR": (0.0, 0.0),
            "RudderDeflPct": 0.0,
            "APRollServo": 0.0,
            "APPitchServo": 0.0,
            "APYawServo": 0.0,
            "Incidence": [0.0, 0.0, 1.0],
            "RudderDefl": 0.0,
            "ElevDefl": 0.0,
            "AileronDefl": 0.0,
            "AirDensity": 1.225,
            "PropThrust": 0,
            "DynPressure": 500.0,
            "G": 1.0,
            "src": "MSFS",
            "DesignSpeed": (100.0, 50.0, 60.0),
            "AccBody": [0, 1, 0],
        }
    
    def on_ground(self, on_ground: bool = True):
        """Set whether aircraft is on ground."""
        self._data["SimOnGround"] = 1 if on_ground else 0
        return self
    
    def weight_on_wheels(self, nose: float = 1.0, left: float = 1.0, right: float = 1.0):
        """Set weight on wheels for each gear."""
        self._data["WeightOnWheels"] = [nose, left, right]
        return self
    
    def ground_speed(self, speed: float):
        """Set ground speed in knots."""
        self._data["GroundSpeed"] = speed
        return self
    
    def surface_type(self, surface: str):
        """Set surface type (Asphalt, Grass, Water, etc.)."""
        self._data["SurfaceType"] = surface
        return self
    
    def water_rudder(self, extension: float):
        """Set water rudder extension (0.0 to 1.0)."""
        self._data["WaterRudderExt"] = extension
        return self
    
    def ffb_type(self, ffb_type: str):
        """Set FFB device type (joystick, pedals, collective, etc.)."""
        self._data["FFBType"] = ffb_type
        return self
    
    def autopilot(self, active: bool = True):
        """Set autopilot state."""
        self._data["APMaster"] = 1 if active else 0
        self._data["APServos"] = 1 if active else 0
        return self
    
    def elevator_trim(self, trim_pct: float):
        """Set elevator trim percentage (-1.0 to 1.0)."""
        self._data["ElevTrimPct"] = trim_pct
        return self
    
    def aileron_trim(self, trim_pct: float):
        """Set aileron trim percentage (-1.0 to 1.0)."""
        self._data["AileronTrimPct"] = trim_pct
        return self
    
    def rudder_trim(self, trim_pct: float):
        """Set rudder trim percentage (-1.0 to 1.0)."""
        self._data["RudderTrimPct"] = trim_pct
        return self
    
    def elevator_deflection(self, deflection_pct: float):
        """Set elevator deflection percentage (-1.0 to 1.0)."""
        self._data["ElevDeflPct"] = deflection_pct
        return self
    
    def aileron_deflection(self, left: float, right: float):
        """Set aileron deflection percentages (-1.0 to 1.0)."""
        self._data["AileronDeflPctLR"] = (left, right)
        return self
    
    def rudder_deflection(self, deflection_pct: float):
        """Set rudder deflection percentage (-1.0 to 1.0)."""
        self._data["RudderDeflPct"] = deflection_pct
        return self
    
    def ap_servos(self, roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0):
        """Set autopilot servo positions for X-Plane."""
        self._data["APRollServo"] = roll
        self._data["APPitchServo"] = pitch
        self._data["APYawServo"] = yaw
        return self
    
    def set(self, key: str, value: Any):
        """Set arbitrary telemetry value."""
        self._data[key] = value
        return self
    
    def build(self) -> Dict[str, Any]:
        """Build and return the telemetry dictionary."""
        return self._data.copy()


def assert_effect_started(effect, msg: str = ""):
    """Assert that an effect was started.
    
    Args:
        effect: The mock effect to check
        msg: Optional message for assertion failure
    """
    assert effect.started, f"Effect was not started. {msg}"
    assert effect.start_count > 0, f"Effect start was never called. {msg}"


def assert_effect_stopped(effect, msg: str = ""):
    """Assert that an effect was stopped or destroyed.
    
    Args:
        effect: The mock effect to check
        msg: Optional message for assertion failure
    """
    assert not effect.started, f"Effect is still started. {msg}"


def assert_effect_not_modified(effect, msg: str = ""):
    """Assert that an effect was not started or modified.
    
    Args:
        effect: The mock effect to check
        msg: Optional message for assertion failure
    """
    assert effect.start_count == 0, f"Effect was unexpectedly started. {msg}"
    assert effect.stop_count == 0, f"Effect was unexpectedly stopped. {msg}"


def assert_friction_coefficient_in_range(
    effect,
    min_coeff: int,
    max_coeff: int,
    axis: str = "both",
    msg: str = ""
):
    """Assert that friction coefficients are within expected range.
    
    Args:
        effect: The mock effect to check
        min_coeff: Minimum expected coefficient (0-4096)
        max_coeff: Maximum expected coefficient (0-4096)
        axis: Which axis to check ('x', 'y', or 'both')
        msg: Optional message for assertion failure
    """
    x_coeff, y_coeff = effect.get_coefficients()
    
    if axis in ("x", "both"):
        assert min_coeff <= x_coeff <= max_coeff, (
            f"X-axis friction coefficient {x_coeff} not in range "
            f"[{min_coeff}, {max_coeff}]. {msg}"
        )
    
    if axis in ("y", "both"):
        assert min_coeff <= y_coeff <= max_coeff, (
            f"Y-axis friction coefficient {y_coeff} not in range "
            f"[{min_coeff}, {max_coeff}]. {msg}"
        )


def assert_spring_coefficient_in_range(
    effect,
    min_coeff: int,
    max_coeff: int,
    axis: str = "both",
    msg: str = ""
):
    """Assert that spring coefficients are within expected range.
    
    Args:
        effect: The mock effect to check
        min_coeff: Minimum expected coefficient (0-4096)
        max_coeff: Maximum expected coefficient (0-4096)
        axis: Which axis to check ('x', 'y', or 'both')
        msg: Optional message for assertion failure
    """
    x_coeff, y_coeff = effect.get_coefficients()
    
    if axis in ("x", "both"):
        assert min_coeff <= x_coeff <= max_coeff, (
            f"X-axis spring coefficient {x_coeff} not in range "
            f"[{min_coeff}, {max_coeff}]. {msg}"
        )
    
    if axis in ("y", "both"):
        assert min_coeff <= y_coeff <= max_coeff, (
            f"Y-axis spring coefficient {y_coeff} not in range "
            f"[{min_coeff}, {max_coeff}]. {msg}"
        )


def assert_spring_offset(
    effect,
    expected_x: Optional[int] = None,
    expected_y: Optional[int] = None,
    tolerance: int = 10,
    msg: str = ""
):
    """Assert that spring offsets match expected values.
    
    Args:
        effect: The mock effect to check
        expected_x: Expected X-axis offset (-4096 to 4096), None to skip
        expected_y: Expected Y-axis offset (-4096 to 4096), None to skip
        tolerance: Acceptable deviation from expected value
        msg: Optional message for assertion failure
    """
    x_offset, y_offset = effect.get_offsets()
    
    if expected_x is not None:
        assert abs(x_offset - expected_x) <= tolerance, (
            f"X-axis offset {x_offset} not within {tolerance} of expected {expected_x}. {msg}"
        )
    
    if expected_y is not None:
        assert abs(y_offset - expected_y) <= tolerance, (
            f"Y-axis offset {y_offset} not within {tolerance} of expected {expected_y}. {msg}"
        )


def assert_simconnect_event_sent(simconnect, event_name: str, msg: str = ""):
    """Assert that a SimConnect event was sent.
    
    Args:
        simconnect: Mock SimConnect instance
        event_name: Name of the event to check for
        msg: Optional message for assertion failure
    """
    event_names = [event[0] for event in simconnect.sent_events]
    assert event_name in event_names, (
        f"Event '{event_name}' was not sent to SimConnect. "
        f"Sent events: {event_names}. {msg}"
    )


def assert_simconnect_event_value(
    simconnect,
    event_name: str,
    expected_value: Any,
    tolerance: float = 0.01,
    msg: str = ""
):
    """Assert that a SimConnect event was sent with expected value.
    
    Args:
        simconnect: Mock SimConnect instance
        event_name: Name of the event to check for
        expected_value: Expected value of the event
        tolerance: Tolerance for numeric comparisons
        msg: Optional message for assertion failure
    """
    matching_events = [
        event for event in simconnect.sent_events
        if event[0] == event_name
    ]
    
    assert matching_events, (
        f"Event '{event_name}' was not sent. {msg}"
    )
    
    last_value = matching_events[-1][1]
    
    if isinstance(expected_value, (int, float)) and isinstance(last_value, (int, float)):
        assert abs(last_value - expected_value) <= tolerance, (
            f"Event '{event_name}' value {last_value} not within {tolerance} "
            f"of expected {expected_value}. {msg}"
        )
    else:
        assert last_value == expected_value, (
            f"Event '{event_name}' value {last_value} != expected {expected_value}. {msg}"
        )


def create_msfs_telemetry(**overrides) -> Dict[str, Any]:
    """Create MSFS telemetry data with common defaults.
    
    Args:
        **overrides: Values to override defaults
    
    Returns:
        Dictionary of telemetry data
    """
    return TelemetryDataBuilder().set("Sim", "MSFS").build() | overrides


def create_xplane_telemetry(**overrides) -> Dict[str, Any]:
    """Create X-Plane telemetry data with common defaults.
    
    Args:
        **overrides: Values to override defaults
    
    Returns:
        Dictionary of telemetry data
    """
    return TelemetryDataBuilder().set("Sim", "X-Plane").build() | overrides
