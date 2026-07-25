"""
Comprehensive unit tests for MsfsXpFBWFlightControlsMixIn.

This module tests the fly-by-wire flight control effects including trim following,
autopilot following, and axis control for both joystick and pedals.
"""
import pytest
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import (
    TelemetryDataBuilder,
    assert_effect_started,
    assert_spring_coefficient_in_range,
    assert_spring_offset,
    assert_simconnect_event_sent,
    assert_simconnect_event_value,
)
from telemffb.sim.msfs_xp.MsfsXpFBWFlightControlsMixIn import MsfsXpFBWFlightControlsMixIn


class TestMsfsXpFBWFlightControlsJoystick(BaseTelemetryEffectTestCase):
    """Test suite for FBW flight controls with joystick."""
    
    def test_joystick_trim_following_disabled_by_default(self):
        """Test that trim following doesn't activate when disabled."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = False
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("joystick")
            .elevator_trim(0.5)
            .aileron_trim(0.3)
            .build()
        )
        self.set_telemetry(instance, telem)
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        # When disabled, spring offset should be 0
        spring = self.mock_effects['fbw_spring']
        x_offset, y_offset = spring.get_offsets()
        assert x_offset == 0, "X offset should be 0 when trim following disabled"
        assert y_offset == 0, "Y offset should be 0 when trim following disabled"
    
    def test_joystick_trim_following_moves_physical_stick(self):
        """Test that trim following moves physical stick position."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.joystick_trim_follow_gain_physical_y = 1.0
        instance.joystick_trim_follow_gain_physical_x = 1.0
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("joystick")
            .elevator_trim(0.5)  # 50% trim
            .aileron_trim(0.3)   # 30% trim
            .build()
        )
        self.set_telemetry(instance, telem)
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        spring = instance._spring_handle
        x_offset, y_offset = spring.get_offsets()
        
        # Offsets should be scaled from -1..1 to -4096..4096
        assert y_offset != 0, "Y offset should be non-zero with elevator trim"
        assert x_offset != 0, "X offset should be non-zero with aileron trim"
        assert abs(y_offset) <= 4096, "Y offset should not exceed max"
        assert abs(x_offset) <= 4096, "X offset should not exceed max"
    
    def test_joystick_trim_follow_gain_physical(self):
        """Test physical gain affects how much stick moves with trim."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("joystick")
            .elevator_trim(0.5)
            .build()
        )
        self.set_telemetry(instance, telem)
        
        # Test with low physical gain
        instance.joystick_trim_follow_gain_physical_y = 0.2
        instance.update_fbw_flight_controls(telem)
        spring = instance._spring_handle
        _, low_gain_offset = spring.get_offsets()
        
        # Test with high physical gain
        instance.joystick_trim_follow_gain_physical_y = 1.0
        instance.update_fbw_flight_controls(telem)
        _, high_gain_offset = spring.get_offsets()
        
        # Assert
        assert abs(high_gain_offset) > abs(low_gain_offset), (
            "Higher physical gain should produce larger stick movement"
        )
    
    def test_joystick_axis_control_sends_to_msfs(self):
        """Test that physical stick position is sent to MSFS."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.joystick_x_axis_scale = 1.0
        instance.joystick_y_axis_scale = 1.0
        
        # Set physical stick position
        self.mock_device._input_data.set_axis(x=0.5, y=-0.3)
        
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        self.set_telemetry(instance, telem)
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        assert_simconnect_event_sent(
            self.mock_simconnect,
            "AXIS_AILERONS_SET",
            "Aileron axis should be sent to MSFS"
        )
        assert_simconnect_event_sent(
            self.mock_simconnect,
            "AXIS_ELEVATOR_SET",
            "Elevator axis should be sent to MSFS"
        )
    
    def test_joystick_axis_scale_parameters(self):
        """Test that axis scale parameters affect sent values."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        
        # Set full deflection
        self.mock_device._input_data.set_axis(x=1.0, y=1.0)
        
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        self.set_telemetry(instance, telem)
        
        # Test with reduced scale
        instance.joystick_x_axis_scale = 0.5
        instance.joystick_y_axis_scale = 0.5
        instance.update_fbw_flight_controls(telem)
        
        # Get sent values
        aileron_events = [
            e for e in self.mock_simconnect.sent_events
            if e[0] == "AXIS_AILERONS_SET"
        ]
        elevator_events = [
            e for e in self.mock_simconnect.sent_events
            if e[0] == "AXIS_ELEVATOR_SET"
        ]
        
        # Assert - values should be scaled to half
        assert aileron_events, "Aileron commands should be sent"
        assert elevator_events, "Elevator commands should be sent"
        
        # With 0.5 scale, full stick should send half of max range (16384)
        expected_max = 16384 * 0.5
        assert abs(aileron_events[-1][1]) <= expected_max + 100, (
            f"Aileron value should be scaled to ~{expected_max}"
        )
    
    def test_autopilot_following_moves_stick_to_ap_position(self):
        """Test that autopilot following moves stick to match AP commands."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.ap_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.joystick_ap_follow_gain_physical_x = 1.0
        instance.joystick_ap_follow_gain_physical_y = 1.0
        
        # Set autopilot active with aileron deflection
        telem = (
            TelemetryDataBuilder()
            .ffb_type("joystick")
            .autopilot(True)
            .aileron_deflection(0.4, -0.4)  # AP commanding right turn
            .elevator_trim(0.2)
            .build()
        )
        self.set_telemetry(instance, telem)
        
        # Physical stick at neutral
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        spring = instance._spring_handle
        x_offset, _ = spring.get_offsets()
        
        # Should command stick to move toward AP position
        assert x_offset != 0, "Stick should be commanded to AP position"
    
    def test_autopilot_deadzone_prevents_axis_fighting(self):
        """Test that deadzone prevents axis commands when stick near AP position."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.ap_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.joystick_ap_x_follow_deadzone = 0.05
        instance.joystick_ap_follow_gain_physical_x = 1.0
        
        # AP commanding slight deflection
        telem = (
            TelemetryDataBuilder()
            .ffb_type("joystick")
            .autopilot(True)
            .aileron_deflection(0.02, -0.02)  # Small deflection
            .build()
        )
        self.set_telemetry(instance, telem)
        
        # Physical stick very close to AP position
        self.mock_device._input_data.set_axis(x=0.02, y=0.0)
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert - within deadzone, so axis should be zeroed
        aileron_events = [
            e for e in self.mock_simconnect.sent_events
            if e[0] == "AXIS_AILERONS_SET"
        ]
        
        # Should send 0 or very small value when within deadzone
        if aileron_events:
            assert abs(aileron_events[-1][1]) < 1000, (
                "Axis should be near zero within deadzone"
            )
    
    def test_fbw_spring_coefficients_joystick(self):
        """Test that FBW gain parameters set spring coefficients correctly."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        self.set_telemetry(instance, telem)
        
        # Test with low FBW gains
        instance.fbw_elevator_gain = 0.3
        instance.fbw_aileron_gain = 0.4
        instance.update_fbw_flight_controls(telem)
        
        spring = instance._spring_handle
        x_coeff_low, y_coeff_low = spring.get_coefficients()
        
        # Test with high FBW gains
        instance.fbw_elevator_gain = 0.9
        instance.fbw_aileron_gain = 0.8
        instance.update_fbw_flight_controls(telem)
        
        x_coeff_high, y_coeff_high = spring.get_coefficients()
        
        # Assert
        assert y_coeff_high > y_coeff_low, (
            "Higher elevator gain should produce stiffer Y spring"
        )
        assert x_coeff_high > x_coeff_low, (
            "Higher aileron gain should produce stiffer X spring"
        )
    
    def test_autopilot_overrides_fbw_spring_stiffness(self):
        """Test that AP active switches to full stiffness spring."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.ap_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.fbw_elevator_gain = 0.5
        instance.fbw_aileron_gain = 0.5
        
        # Test with AP off
        telem_ap_off = (
            TelemetryDataBuilder()
            .ffb_type("joystick")
            .autopilot(False)
            .build()
        )
        self.set_telemetry(instance, telem_ap_off)
        instance.update_fbw_flight_controls(telem_ap_off)
        spring = self.mock_effects['fbw_spring']
        x_coeff_ap_off, y_coeff_ap_off = spring.get_coefficients()
        
        # Test with AP on
        telem_ap_on = (
            TelemetryDataBuilder()
            .ffb_type("joystick")
            .autopilot(True)
            .build()
        )
        self.set_telemetry(instance, telem_ap_on)
        
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        instance.update_fbw_flight_controls(telem_ap_on)
        spring = instance._spring_handle
        x_coeff_ap_on, y_coeff_ap_on = spring.get_coefficients()
        
        # Assert - AP should use full coefficient (1.0 instead of 0.5)
        assert y_coeff_ap_on > y_coeff_ap_off, (
            "AP active should make spring stiffer"
        )


class TestMsfsXpFBWFlightControlsPedals(BaseTelemetryEffectTestCase):
    """Test suite for FBW flight controls with pedals."""
    
    def test_pedals_trim_following_disabled_by_default(self):
        """Test that rudder trim following doesn't activate when disabled."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = False
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .rudder_trim(0.5)
            .build()
        )
        self.set_telemetry(instance, telem)
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        spring = self.mock_effects['fbw_spring']
        x_offset, _ = spring.get_offsets()
        assert x_offset == 0, "X offset should be 0 when trim following disabled"
    
    def test_pedals_trim_following_moves_physical_pedals(self):
        """Test that rudder trim following moves physical pedal position."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.rudder_trim_follow_gain_physical_x = 1.0
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .rudder_trim(0.6)  # 60% trim
            .build()
        )
        self.set_telemetry(instance, telem)
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        spring = instance._spring_handle
        x_offset, _ = spring.get_offsets()
        
        assert x_offset != 0, "X offset should be non-zero with rudder trim"
        assert abs(x_offset) <= 4096, "X offset should not exceed max"
    
    def test_pedals_axis_control_sends_to_msfs(self):
        """Test that physical pedal position is sent to MSFS."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.rudder_x_axis_scale = 1.0
        
        # Set physical pedal position
        self.mock_device._input_data.set_axis(x=0.4, y=0.0)
        
        telem = TelemetryDataBuilder().ffb_type("pedals").build()
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        assert_simconnect_event_sent(
            self.mock_simconnect,
            "AXIS_RUDDER_SET",
            "Rudder axis should be sent to MSFS"
        )
    
    def test_pedals_axis_scale_parameter(self):
        """Test that rudder axis scale parameter affects sent values."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        
        # Set full deflection
        self.mock_device._input_data.set_axis(x=1.0, y=0.0)
        
        telem = TelemetryDataBuilder().ffb_type("pedals").build()
        
        # Test with reduced scale
        instance.rudder_x_axis_scale = 0.5
        instance.update_fbw_flight_controls(telem)
        
        # Get sent values
        rudder_events = [
            e for e in self.mock_simconnect.sent_events
            if e[0] == "AXIS_RUDDER_SET"
        ]
        
        # Assert
        assert rudder_events, "Rudder commands should be sent"
        expected_max = 16384 * 0.5
        assert abs(rudder_events[-1][1]) <= expected_max + 100, (
            f"Rudder value should be scaled to ~{expected_max}"
        )
    
    def test_pedals_autopilot_following(self):
        """Test that autopilot following works for pedals."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.ap_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        
        # AP active with rudder deflection
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .autopilot(True)
            .rudder_deflection(0.3)
            .build()
        )
        
        # Physical pedals at neutral
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        spring = instance._spring_handle
        x_offset, _ = spring.get_offsets()
        
        assert x_offset != 0, "Pedals should be commanded to AP position"
    
    def test_fbw_spring_coefficients_pedals(self):
        """Test that FBW rudder gain sets spring coefficient correctly."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        
        telem = TelemetryDataBuilder().ffb_type("pedals").build()
        
        # Test with low gain
        instance.fbw_rudder_gain = 0.3
        instance.update_fbw_flight_controls(telem)
        
        spring = instance._spring_handle
        x_coeff_low, _ = spring.get_coefficients()
        
        # Test with high gain
        instance.fbw_rudder_gain = 0.9
        instance.update_fbw_flight_controls(telem)
        
        x_coeff_high, _ = spring.get_coefficients()
        
        # Assert
        assert x_coeff_high > x_coeff_low, (
            "Higher rudder gain should produce stiffer spring"
        )
    
    def test_pedals_autopilot_full_stiffness(self):
        """Test that pedals use full stiffness when AP active."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.ap_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.fbw_rudder_gain = 0.5  # Half stiffness normally
        
        # Test with AP on
        telem_ap_on = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .autopilot(True)
            .build()
        )
        
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        instance.update_fbw_flight_controls(telem_ap_on)
        spring = instance._spring_handle
        x_coeff_ap_on, _ = spring.get_coefficients()
        
        # Assert - should be full stiffness (4096)
        assert x_coeff_ap_on == 4096, (
            f"AP should use full stiffness, got {x_coeff_ap_on}"
        )


class TestMsfsXpFBWFlightControlsXPlane(BaseTelemetryEffectTestCase):
    """Test suite for FBW flight controls with X-Plane."""
    
    def test_xplane_axis_commands_use_xp_format(self):
        """Test that X-Plane uses different command format than MSFS."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_xplane = True
        instance.telemffb_controls_axes = True
        instance.joystick_x_axis_scale = 1.0
        instance.joystick_y_axis_scale = 1.0
        
        # Set physical stick position
        self.mock_device._input_data.set_axis(x=0.5, y=-0.3)
        
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        assert hasattr(instance, '_xp_commands'), "Should send XP commands"
        assert len(instance._xp_commands) > 0, "Should have sent at least one command"
        
        # X-Plane commands should use AXIS: format with jx/jy
        xp_cmd = instance._xp_commands[-1]
        assert "AXIS:" in xp_cmd, "Should use AXIS: command format"
        assert "jx=" in xp_cmd or "jy=" in xp_cmd, "Should contain axis parameters"
    
    def test_xplane_pedals_use_px_format(self):
        """Test that X-Plane pedals use px format."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_xplane = True
        instance.telemffb_controls_axes = True
        instance.rudder_x_axis_scale = 1.0
        
        # Set physical pedal position
        self.mock_device._input_data.set_axis(x=0.4, y=0.0)
        
        telem = TelemetryDataBuilder().ffb_type("pedals").build()
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        assert hasattr(instance, '_xp_commands')
        xp_cmd = instance._xp_commands[-1]
        assert "AXIS:" in xp_cmd
        assert "px=" in xp_cmd, "Should use px= for pedals"
    
    def test_xplane_ap_servos_used_for_following(self):
        """Test that X-Plane uses AP servo values for autopilot following."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_xplane = True
        instance.trim_following = True
        instance.ap_following = True
        instance.telemffb_controls_axes = True
        
        # AP active with servo positions
        telem = (
            TelemetryDataBuilder()
            .ffb_type("joystick")
            .autopilot(True)
            .ap_servos(roll=0.3, pitch=0.2, yaw=0.0)
            .build()
        )
        
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        spring = instance._spring_handle
        x_offset, y_offset = spring.get_offsets()
        
        # Should use AP servo values
        assert x_offset != 0 or y_offset != 0, (
            "Should respond to AP servo positions"
        )


class TestMsfsXpFBWFlightControlsCustomAxes(BaseTelemetryEffectTestCase):
    """Test suite for custom axis configuration."""
    
    def test_custom_x_axis_uses_custom_variable(self):
        """Test that custom X axis uses specified SimConnect variable."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.enable_custom_x_axis = True
        instance.custom_x_axis = "CUSTOM_AILERON_AXIS"
        instance.raw_x_axis_scale = 8192  # Custom range
        
        self.mock_device._input_data.set_axis(x=0.5, y=0.0)
        
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        assert_simconnect_event_sent(
            self.mock_simconnect,
            "CUSTOM_AILERON_AXIS",
            "Should use custom axis variable"
        )
    
    def test_custom_axis_scale_affects_range(self):
        """Test that custom axis scale changes the output range."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.enable_custom_y_axis = True
        instance.custom_y_axis = "CUSTOM_ELEVATOR"
        instance.raw_y_axis_scale = 1  # Normalized 0-1 range instead of int
        instance.joystick_y_axis_scale = 1.0
        
        # Full deflection
        self.mock_device._input_data.set_axis(x=0.0, y=1.0)
        
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        
        # Act
        instance.update_fbw_flight_controls(telem)
        
        # Assert
        custom_events = [
            e for e in self.mock_simconnect.sent_events
            if e[0] == "CUSTOM_ELEVATOR"
        ]
        
        assert custom_events, "Should send custom elevator events"
        # With scale=1, should send normalized value close to 1.0
        assert abs(abs(custom_events[-1][1]) - 1.0) < 0.1, (
            "Should use normalized range with raw_scale=1"
        )


class TestAPFollowCurveConsistency(BaseTelemetryEffectTestCase):
    """AP following on calibrated aircraft (field report 2026-07-22): the
    AP-follow branch overwrote the curve-aware Y spring center/virtual offset
    with pre-calibration raw-trim formulas, snapping the stick off the curve
    the moment the AP engaged. With a curve active, the AP path must keep the
    curve values (MSFS: same trim signal; X-Plane: curve center + servo
    deviation); without one, behavior stays byte-identical legacy."""

    # Linear single-speed curve: offs = 0.5 * t, anchored at t0 = 0, so the
    # curve center at trim T is 0.5*T (physical gain 1.0) — distinguishable
    # from every legacy formula at the gains used below.
    CURVE = (
        '{"curves": [{"points": ['
        '{"t": -1.0, "offs": -0.5}, {"t": -0.75, "offs": -0.375}, '
        '{"t": -0.5, "offs": -0.25}, {"t": -0.25, "offs": -0.125}, '
        '{"t": 0.0, "offs": 0.0}, {"t": 0.25, "offs": 0.125}, '
        '{"t": 0.5, "offs": 0.25}, {"t": 0.75, "offs": 0.375}, '
        '{"t": 1.0, "offs": 0.5}], '
        '"t0": 0.0, "ias_kt": 100.0, "date": "2026-07-22"}]}'
    )

    def _curve_instance(self, sim_msfs=True):
        instance = self.create_test_instance(MsfsXpFBWFlightControlsMixIn)
        if sim_msfs:
            instance._test_sim_is_msfs = True
        else:
            instance._test_sim_is_xplane = True
        instance.trim_following = True
        instance.ap_following = True
        instance.telemffb_controls_axes = True
        instance._simconnect = self.mock_simconnect
        instance.joystick_trim_follow_gain_physical_y = 1.0
        instance.joystick_trim_follow_gain_virtual_y = 0.2
        instance.joystick_ap_follow_gain_physical_y = 1.0
        instance.joystick_trim_follow_use_curve_y = True
        instance.joystick_trim_follow_curve_y = self.CURVE
        assert instance._trim_curve_y_fam is not None, "curve must parse"
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        return instance

    def _y_offset(self, instance, telem):
        self.set_telemetry(instance, telem)
        instance.update_fbw_flight_controls(telem)
        _, y = instance._spring_handle.get_offsets()
        return y

    def _settled_y(self, instance, telem, frames=3):
        # A few frames so the trim dampener's derivative term dies off.
        for _ in range(frames):
            y = self._y_offset(instance, telem)
        return y

    def test_msfs_ap_engage_does_not_snap_off_the_curve(self):
        instance = self._curve_instance(sim_msfs=True)
        trimmed = TelemetryDataBuilder().ffb_type("joystick") \
            .elevator_trim(0.5).autopilot(False).build()
        y_hand = self._settled_y(instance, trimmed)
        # Curve center, not raw trim: 0.5*0.5*4096, not 0.5*4096.
        assert y_hand == pytest.approx(0.25 * 4096, abs=40)

        ap_on = TelemetryDataBuilder().ffb_type("joystick") \
            .elevator_trim(0.5).autopilot(True).build()
        y_ap = self._settled_y(instance, ap_on)
        assert y_ap == y_hand, (
            f"AP engage snapped the center: hand {y_hand} -> AP {y_ap}"
        )

    def test_xplane_ap_center_is_curve_center_plus_servo(self):
        instance = self._curve_instance(sim_msfs=False)
        trimmed = TelemetryDataBuilder().ffb_type("joystick") \
            .elevator_trim(0.5).autopilot(False).build()
        y_hand = self._settled_y(instance, trimmed)
        assert y_hand == pytest.approx(0.25 * 4096, abs=40)

        # AP in trim (servo ~ 0): rest point identical to hand-flying.
        ap_trimmed = TelemetryDataBuilder().ffb_type("joystick") \
            .elevator_trim(0.5).autopilot(True) \
            .ap_servos(roll=0.0, pitch=0.0).build()
        assert self._settled_y(instance, ap_trimmed) == y_hand

        # AP commanding pitch: stick deviates from the trimmed rest point by
        # exactly the servo command.
        ap_pitching = TelemetryDataBuilder().ffb_type("joystick") \
            .elevator_trim(0.5).autopilot(True) \
            .ap_servos(roll=0.0, pitch=0.2).build()
        y_dev = self._settled_y(instance, ap_pitching)
        assert y_dev - y_hand == pytest.approx(0.2 * 4096, abs=40)

    def test_msfs_legacy_ap_follow_unchanged_without_curve(self):
        instance = self._curve_instance(sim_msfs=True)
        instance.joystick_trim_follow_use_curve_y = False  # legacy path
        ap_on = TelemetryDataBuilder().ffb_type("joystick") \
            .elevator_trim(0.5).autopilot(True).build()
        y_ap = self._settled_y(instance, ap_on)
        # Legacy formula: raw trim * ap physical gain (1.0) * 4096.
        assert y_ap == pytest.approx(0.5 * 4096, abs=40)

    def test_xplane_legacy_ap_follow_unchanged_without_curve(self):
        instance = self._curve_instance(sim_msfs=False)
        instance.joystick_trim_follow_use_curve_y = False  # legacy path
        ap_on = TelemetryDataBuilder().ffb_type("joystick") \
            .elevator_trim(0.5).autopilot(True) \
            .ap_servos(roll=0.0, pitch=0.2).build()
        y_ap = self._settled_y(instance, ap_on)
        # Legacy: servo position alone.
        assert y_ap == pytest.approx(0.2 * 4096, abs=2)

    def test_msfs_ap_follow_axis_tracks_deflection(self):
        # joystick_ap_y_follow_axis (restored from the dea669a regression):
        # with no curve, AP-follow Y tracks elevator DEFLECTION, not trim.
        instance = self._curve_instance(sim_msfs=True)
        instance.joystick_trim_follow_use_curve_y = False
        instance.joystick_ap_follow_gain_virtual_y = 0.2
        telem = (TelemetryDataBuilder().ffb_type("joystick")
                 .elevator_trim(0.5).elevator_deflection(-0.3)
                 .autopilot(True).build())

        # Toggle OFF: follows trim.
        instance.joystick_ap_y_follow_axis = False
        assert self._settled_y(instance, telem) == pytest.approx(0.5 * 4096, abs=40)

        # Toggle ON: follows deflection (physical gain 1.0), NOT trim.
        instance.joystick_ap_y_follow_axis = True
        assert self._settled_y(instance, telem) == pytest.approx(-0.3 * 4096, abs=40)

    def test_curve_overrides_ap_follow_axis(self):
        # With a curve active, the toggle is ignored — the curve owns the
        # center (no snap to raw deflection when the AP engages).
        instance = self._curve_instance(sim_msfs=True)  # curve enabled
        instance.joystick_ap_y_follow_axis = True
        telem = (TelemetryDataBuilder().ffb_type("joystick")
                 .elevator_trim(0.5).elevator_deflection(-0.3)
                 .autopilot(True).build())
        # Curve center at trim 0.5 is 0.25*4096, not the -0.3 deflection.
        assert self._settled_y(instance, telem) == pytest.approx(0.25 * 4096, abs=40)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
